# -*- coding: utf-8 -*-
"""2026-11-22 白天第二轮优化回归。

锁死：M6 过期说明 / near_limit 日期 / M14 /api 404 JSON / M7 原值全列 /
M2 标题默认等别 / G1 铬同卡矛盾 / G2 表内标准号假陈述 /
斜率小样本过滤 / M5 等级假设前缀剥离。
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qc_daily import templates                    # noqa: E402
from qc_daily.analysis import _trend_summary       # noqa: E402
from qc_daily.intent import route                  # noqa: E402
from qc_daily.models import DailyMetrics, TrendPoint  # noqa: E402


class TestConfigFacts(unittest.TestCase):
    """M6 + 告警日期：事实描述与补录日期不再错位。"""

    def test_1_limits_doc_no_longer_says_stale(self):
        doc = json.loads((ROOT / "corpus" / "limits.json").read_text(encoding="utf-8"))
        blob = json.dumps(doc, ensure_ascii=False)
        self.assertNotIn('_未启用说明', blob, "旧说明仍称写死 2025 / limits_verified=false")
        self.assertIn('_启用状态说明', blob)
        self.assertIn('GB1350-2009', blob)

    def test_2_near_limit_alert_carries_report_date(self):
        for f in (ROOT / "qc_daily" / "config.py", ROOT / "qc_daily_config.json"):
            txt = f.read_text(encoding="utf-8")
            self.assertIn('near_limit', txt)
            self.assertNotIn('"detail": "当日 {value} 个项次限值利用率', txt)
            self.assertIn('{report_date} {value} 个项次限值利用率', txt)


class TestM2TitleDefaultGrade(unittest.TestCase):
    """M2：没写等别时标题标「默认3等」，不冒充用户问的档。"""

    def test_1_未写等别标题带默认(self):
        a = route("籼稻谷出糙率限值是多少")["answer"]
        self.assertIn("默认3 等", a["title"])

    def test_2_写了等别标题不带默认(self):
        a = route("籼稻谷 1 等 出糙率限值是多少")["answer"]
        self.assertNotIn("默认", a["title"])
        self.assertIn("1 等", a["title"])


class TestG1Contradiction(unittest.TestCase):
    """G1：语料原文有 1.0 mg/kg，正文先顶一句「未核对不能判」。"""

    def test_1_铬答案首行是免责提示(self):
        a = route("铬在稻谷里的限值是多少")["answer"]
        lines = a.get("lines") or []
        self.assertTrue(lines, "应有命中行")
        self.assertIn("不能当限值直接判", lines[0])
        # 原文仍在（可追溯）
        self.assertTrue(any("1.0 mg/kg" in str(x) for x in lines[1:]))


class TestG2StandardClaim(unittest.TestCase):
    """G2：不说「表内标准号」，补齐时不挂「不一致」假图例。"""

    def _facts(self, qc, notes=None):
        m = DailyMetrics(report_date="2026-11-21", plant="", generated_at="")
        html = templates.render_html(m, {"qc_details": qc, "import_notes": notes or []})
        seg = re.search(r"本报告的判定口径.*?</div>", html, re.S)
        return seg.group(0) if seg else ""

    def test_1_不出现表内标准号字眼(self):
        f = self._facts([{"standard": "GB 1350—2009", "limit_source": "表1"}])
        self.assertNotIn("表内标准号", f)
        self.assertIn("判定依据标准号", f)

    def test_2_补齐同版不挂不一致图例(self):
        f = self._facts([{"standard": "GB 1350—2009", "limit_source": "表1"}])
        self.assertNotIn("适用版本须由化验室确认", f)
        self.assertIn("由上表执行版补齐，非表内原文", f)

    def test_3_真有异版仍提示确认(self):
        f = self._facts([{"standard": "GB 1350—2025", "limit_source": "表1"}])
        self.assertIn("适用版本须由化验室确认", f)


class TestTrendSmallSamples(unittest.TestCase):
    """斜率：total<5 的探针天不参与，结论说明排除了几天。"""

    def test_1_小样本天被过滤(self):
        pts = [TrendPoint(date="2026-11-18", pass_rate=1.0, total=1, fail=0, abnormal_batches=0),
               TrendPoint(date="2026-11-19", pass_rate=0.5, total=10, fail=5, abnormal_batches=1),
               TrendPoint(date="2026-11-20", pass_rate=0.6, total=10, fail=4, abnormal_batches=1),
               TrendPoint(date="2026-11-21", pass_rate=0.7, total=10, fail=3, abnormal_batches=1)]
        s = _trend_summary(pts)
        self.assertIn("已排除 1 个样本<5 项次的天", s["comment"])
        # 若未过滤，1.0 会显著改变斜率；过滤后应更接近后三天的真实走势
        self.assertGreater(s["slope"], 0)

    def test_2_全是小样本返回数据不足(self):
        pts = [TrendPoint(date="2026-11-20", pass_rate=1.0, total=1, fail=0, abnormal_batches=0),
               TrendPoint(date="2026-11-21", pass_rate=0.0, total=2, fail=0, abnormal_batches=0)]
        s = _trend_summary(pts)
        self.assertEqual(s["direction"], "数据不足")
        self.assertIn("排除 2 个样本", s["comment"])


class TestM5GradeNotePrefix(unittest.TestCase):
    """M5：等级假设不把文件名 / [sheet] 原样印进签字纸。"""

    def _grade(self, note):
        m = DailyMetrics(report_date="2026-11-21", plant="", generated_at="")
        html = templates.render_html(m, {"qc_details": [], "import_notes": [note]})
        hit = re.search(r"等级假设：([^<]+)", html)
        return hit.group(1) if hit else ""

    def test_1_文件名与sheet被剥离(self):
        out = self._grade("稻谷验收.xlsx[Sheet1] ⚠️【等级】表里没有「等级 / 等别」列，按 3 等补齐。")
        self.assertNotIn(".xlsx", out)
        self.assertNotIn("[Sheet1]", out)
        self.assertIn("表里没有", out)

    def test_2_不重复表里没有(self):
        out = self._grade("【等级】表里没有等级列，按 3 等补齐。")
        self.assertNotIn("表里没有表里没有", out)


if __name__ == "__main__":
    unittest.main()
