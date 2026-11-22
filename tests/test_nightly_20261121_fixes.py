# -*- coding: utf-8 -*-
"""2026-11-21 夜测 5 严重项修复回归。

S1 报告按 run 留档不再互相覆盖 / S2 当日唯一口径 daily_rollup /
S3 被跳过行在结论行可见 / S4 只给品类词走「缺指标」话术 /
S5 产物下载白名单（inputs/input.db 不再无鉴权可取）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qc_daily.analysis import daily_rollup                     # noqa: E402
from qc_daily.artifacts import _is_artifact, resolve_artifact  # noqa: E402
from qc_daily.intent import route                              # noqa: E402
from qc_daily import templates                                 # noqa: E402
from qc_daily.models import DailyMetrics                        # noqa: E402


class TestDailyRollup(unittest.TestCase):
    """S2：同一天多次运行合并成唯一口径，趋势不再被 1 项探针洗成 100%。"""

    ENTRIES = [
        {"report_date": "2026-11-21", "total_tests": 14, "fail_tests": 2,
         "pass_rate": 0.8571, "abnormal_batch_count": 1, "near_limit_count": 3,
         "item_fail": {"杂质含量": 1, "水分含量": 1},
         "item_mean": {"杂质含量": 1.2, "水分含量": 13.5}},
        {"report_date": "2026-11-21", "total_tests": 7, "fail_tests": 0,
         "pass_rate": 1.0, "item_fail": {}, "item_mean": {"水分含量": 13.2}},
        {"report_date": "2026-11-21", "total_tests": 4, "fail_tests": 0,
         "pass_rate": 1.0, "item_fail": {}, "item_mean": {"水分含量": 13.4}},
        {"report_date": "2026-11-20", "total_tests": 170, "fail_tests": 4,
         "pass_rate": 0.9765, "item_fail": {"杂质含量": 4}, "item_mean": {}},
    ]

    def test_1_同日合并项次与不合格相加_比率重算(self):
        r = daily_rollup(self.ENTRIES, "2026-11-21")
        self.assertEqual(r["total_tests"], 25)
        self.assertEqual(r["fail_tests"], 2)
        self.assertEqual(r["pass_rate"], 0.92)        # 不是三比率的平均
        self.assertEqual(r["run_count"], 3)

    def test_2_合并只取指定日期_不串日(self):
        r = daily_rollup(self.ENTRIES, "2026-11-20")
        self.assertEqual(r["total_tests"], 170)
        self.assertEqual(r["run_count"], 1)

    def test_3_item_fail累加_item_mean按项次加权(self):
        r = daily_rollup(self.ENTRIES, "2026-11-21")
        self.assertEqual(r["item_fail"], {"杂质含量": 1, "水分含量": 1})
        # 按各 run 的 total_tests 加权：(13.5×14 + 13.2×7 + 13.4×4)/25 = 335/25 = 13.4
        self.assertAlmostEqual(r["item_mean"]["水分含量"], 13.4, places=2)

    def test_4_空输入不崩(self):
        r = daily_rollup([], "2026-11-21")
        self.assertEqual(r["total_tests"], 0)
        self.assertEqual(r["run_count"], 0)


class TestArtifactWhitelist(unittest.TestCase):
    """S5：直链下载与列表共用白名单，复盘素材一律拒绝。"""

    def test_1_复盘素材不是产物(self):
        for rel in ("inputs/raw.csv", "snapshots/s.json", "input.db",
                    "request.json", "audit_entry.json", "trace.jsonl"):
            self.assertFalse(_is_artifact(rel), rel)

    def test_2_真产物放行(self):
        for rel in ("品控日报_2026-11-21.html", "品控日报_2026-11-21.xlsx",
                    "经理日报_2026-11-21.txt"):
            self.assertTrue(_is_artifact(rel), rel)

    def test_3_resolve_artifact拒绝输入库(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / "TTEST001"
            (run / "inputs").mkdir(parents=True)
            (run / "input.db").write_bytes(b"sqlite")
            (run / "inputs" / "raw.csv").write_text("x")
            (run / "report.html").write_text("<html>x</html>")
            with self.assertRaises(ValueError):
                resolve_artifact("TTEST001", "input.db", td)
            with self.assertRaises(ValueError):
                resolve_artifact("TTEST001", "inputs/raw.csv", td)
            self.assertTrue(resolve_artifact("TTEST001", "report.html", td).is_file())


class TestCategoryOnlyAnswer(unittest.TestCase):
    """S4：只给品类词走「缺指标」，不再谎称「库里没有稻谷」。"""

    def _answer(self, q: str) -> dict:
        return route(q)["answer"]

    def test_1_籼稻谷不再说库里没有(self):
        a = self._answer("籼稻谷")
        self.assertIn("还没说要问哪一项", a["title"])
        self.assertNotIn("库里没有", a["title"])
        joined = " ".join(a.get("lines") or [])
        self.assertIn("出糙率", joined)

    def test_2_籼稻品种缩写也命中(self):
        a = self._answer("籼稻")
        self.assertIn("还没说要问哪一项", a["title"])

    def test_3_带指标仍正常反问品种或给数(self):
        a = self._answer("稻谷水分限值是多少")
        self.assertNotIn("还没说要问哪一项", a["title"])


class TestSkippedRowsInConclusion(unittest.TestCase):
    """S3：被跳过的行在「今日结论」里显式点出，不再说「不判 0」。"""

    def test_1_结论行点出未纳入的行数(self):
        meta = {
            "qc_details": [
                {"batch_id": "A", "item": "杂质含量", "verdict": "不合格",
                 "standard": "GB 1350—2009", "limit_source": "表1"},
                {"batch_id": "B", "item": "水分含量", "verdict": "合格",
                 "standard": "GB 1350—2009", "limit_source": "表1"},
            ],
            "import_notes": [
                "⚠️ 数据第 3 行 · A×杂质含量 实测值「＜0.5」这一行没进判定",
                "⚠️ 本表共 7 行数据，其中 1 行有实测值没进判定；其余 6 行照跑。",
            ],
        }
        m = DailyMetrics(report_date="2026-11-21", plant="", generated_at="")
        html = templates.render_html(m, meta)
        self.assertIn("另有 1 行因写法无法判定、未纳入上面 2 项次", html)
        self.assertIn("本表共 7 行", html)

    def test_2_无跳过行时不出现该提示(self):
        meta = {
            "qc_details": [
                {"batch_id": "A", "item": "杂质含量", "verdict": "合格",
                 "standard": "GB 1350—2009", "limit_source": "表1"},
            ],
            "import_notes": [],
        }
        m = DailyMetrics(report_date="2026-11-21", plant="", generated_at="")
        html = templates.render_html(m, meta)
        self.assertNotIn("因写法无法判定", html)


if __name__ == "__main__":
    unittest.main()
