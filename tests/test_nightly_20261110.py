# -*- coding: utf-8 -*-
"""夜测 2026-11-10 第 1–5 轮的回归测试（用户授权「自己可以决定」后自主修的那一批）。

按报告里的病灶分四组：
  A 任务卡：说明行数成检测项 / critical 详情不浮现 / 逐行明细被双重截断 /
            「出错了」贴给按规矩拒判（r03/r04/r05 连续三晚）
  B 导入层：单行表上下两句话打架 / 坏 xlsx 甩英文异常 / 表里版本没人问一句
  C 问答层：籼粳两套限值整节糊上来 / 同义问法分叉 / 技术类越界掉兜底 /
            边界话与贴出来的表打架
  D 评测层：断言只问「数字在不在」/ 拒答靠别处否定词蹭过 / 同答指纹全记 PASS

跑法：.venv/Scripts/python.exe -m unittest tests.test_nightly_20261110 -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qc_daily.task_web import (  # noqa: E402
    _STOP_CN, _build_view, _stop_code,
)
from qc_daily.importer import (  # noqa: E402
    _check_version, _finalize_skip_notes, _norm_std, _skip_note,
)
from qc_daily.config import QCDailyConfig  # noqa: E402
from qc_daily.tasks.runner import diagnose_missing_dates  # noqa: E402
from qc_daily.analysis import HistoryStore  # noqa: E402
from qc_daily.intent import knowledge_answer, route  # noqa: E402
from qc_daily.client_ui import _brief  # noqa: E402
from qc_daily.artifacts import index_runs  # noqa: E402
from nightly import run_round as rr  # noqa: E402


VERDICT = ("DEMO-001 · 水分含量 ≤13.5 → 实测 14.6 → "
           "代码判定「不合格」（GB 1350—2025 表1 第3等）")
NOTE_VERSION = "标准版本：当前执行 GB 1350—2009，限值待人工核对"
NOTE_RELEASE = "放行决策需人工复核签字，本系统不放行"


def _state(**kw) -> dict:
    d = {"status": "done", "task_id": "grain_daily_report", "sections": {},
         "metrics": {}, "notes": [], "errors": [], "alerts": [],
         "artifacts": [], "trace": []}
    d.update(kw)
    return d


# ------------------------------------------------------------------ A 任务卡
class TestTaskCard(unittest.TestCase):
    def test_说明行不进分组计数(self):
        d = _state(sections={"待人工复核": [VERDICT, NOTE_VERSION, NOTE_RELEASE],
                             "已确认": [], "未覆盖": []},
                   metrics={"total_tests": 1, "fail_tests": 1})
        v = _build_view(d, "T-TEST")
        review = next(g for g in v["groups"] if g["key"] == "待人工复核")
        self.assertEqual(review["count"], 1, "14 个检测项不该被数成 19")
        self.assertEqual(len(review["items"]), 1)
        self.assertTrue(any("说明：标准版本" in a for a in v["alerts"]),
                        "说明行不能丢，只是不计数")

    def test_三组说明行都不计数_三组之和等于检测项数(self):
        # 11-09 报告把这条记成「M8 计数口径未定」，看穿后其实和 r03 是同一个病：
        # 说明行混进了全部三组，尤其「未覆盖」整组都是范围声明。
        notes_uncovered = ["感官项「色泽、气味」：人工确认项，不参与数值判定",
                           "其它等级（1/2/4/5 等）、粳稻谷表2：本任务未覆盖",
                           "数据范围：DEMO-only 假批次，真实批次与实测值不进本系统"]
        d = _state(sections={"待人工复核": [VERDICT, NOTE_VERSION, NOTE_RELEASE],
                             "已确认": [VERDICT.replace("不合格", "合格")],
                             "未覆盖": notes_uncovered},
                   metrics={"total_tests": 2, "fail_tests": 1})
        v = _build_view(d, "T-TEST")
        self.assertEqual(sum(g["count"] for g in v["groups"]), 2)
        uncovered = next(g for g in v["groups"] if g["key"] == "未覆盖")
        self.assertEqual(uncovered["count"], 0)
        self.assertEqual(sum(1 for a in v["alerts"] if a.startswith("说明：")), 5)

    def test_critical_详情原样浮现_不再只留在_json_里(self):
        detail = ("2026-11-10 数据存在 14 项错误：质检记录重复: "
                  "DEMO-N01 × 出糙率；DEMO-N01 × 整精米率")
        d = _state(status="failed",
                   errors=["error 级校验失败：按「宁可不发，不可错发」不发布报告"],
                   alerts=[{"level": "error", "detail": detail}],
                   metrics={})
        v = _build_view(d, "T-TEST")
        self.assertIn(detail, v["alerts"], "拦得对却不说哪一批 = 使用人没法动手")

    def test_三行明细不再只剩第一行(self):
        notes = ["下一步：按提示修正输入",
                 "⚠️ 数据第 1 行 · DEMO-U01×水分含量 实测值「未检出」不是数字",
                 "⚠️ 数据第 2 行 · DEMO-U02×水分含量 实测值「—」不是数字",
                 "⚠️ 数据第 3 行 · DEMO-U03×水分含量 实测值「ND」不是数字"]
        v = _build_view(_state(notes=notes), "T-TEST")
        joined = "\n".join(v["alerts"])
        for who in ("DEMO-U01", "DEMO-U02", "DEMO-U03"):
            self.assertIn(who, joined, "改完第 1 行重传不该再撞两次同样的错")

    def test_按规矩停下不贴红字出错了(self):
        for code, cn in (("missing_limits", "缺限值、按规矩没判"),
                         ("no_rows", "表里没读到数据行"),
                         ("values_unreadable", "实测值读不懂"),
                         ("validation_error", "校验拦下、未发报告")):
            with self.subTest(code=code):
                d = _state(status="failed", errors=["没做 —— 按规矩拦下"],
                           trace=[{"state": "failed", "event": "state_change"},
                                  {"state": "failed", "event": code}])
                v = _build_view(d, "T-TEST")
                self.assertEqual(v["status_cn"], cn)
                self.assertEqual(v["status_tone"], "warn")
        # 真异常仍留红字
        v = _build_view(_state(status="failed", errors=["运行中断"],
                               trace=[{"state": "failed", "event": "exception"}]), "T-TEST")
        self.assertEqual(v["status_tone"], "bad")

    def test_stop_code_从_trace取_不靠字符串猜(self):
        d = {"trace": [{"state": "failed", "event": "state_change"},
                       {"state": "failed", "event": "no_dates"},
                       {"state": "failed", "event": "finished"}]}
        self.assertEqual(_stop_code(d), "no_dates")
        self.assertIn("no_dates", _STOP_CN)

    def test_历史索引带出短错因码(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "T20261110-abc"
            run.mkdir()
            (run / "summary.json").write_text(json.dumps({
                "run_id": "T20261110-abc", "task_id": "grain_daily_report",
                "status": "failed", "started_at": "2026-11-10 05:20:00",
                "errors": ["这些记录没取到限值，系统不猜限值、不判"],
                "trace": [{"state": "failed", "event": "missing_limits"}],
            }, ensure_ascii=False), encoding="utf-8")
            rows = index_runs(td, limit=5)
        self.assertEqual(rows[0]["stop_code"], "missing_limits")


# ------------------------------------------------------------------ B 导入层
class TestImporterWording(unittest.TestCase):
    def test_逐行提示不再写其余行照跑(self):
        self.assertNotIn("其余行照跑", _skip_note(1, "DEMO-Z01", "杂质含量", "＜0.5"))

    def test_单行表总账说没有其余行(self):
        notes = []
        skip = [_skip_note(1, "DEMO-Z01", "杂质含量", "＜0.5")]
        _finalize_skip_notes(notes, skip, {1}, 1)
        joined = "\n".join(notes)
        self.assertIn("表里没有其它数据行", joined)
        self.assertIn("一行都没进判定", joined)
        self.assertNotIn("其余行照跑", joined, "别把要修掉的假句子复述一遍")

    def test_多行表总账给出还剩几行(self):
        notes = []
        skip = [_skip_note(2, "DEMO-U02", "水分含量", "—")]
        _finalize_skip_notes(notes, skip, {2}, 3)
        self.assertTrue(any("其余 2 行照跑" in n for n in notes))

    def test_跳过行时诊断给确定话_不再三选一猜测(self):
        msg, nxt, code = diagnose_missing_dates([], True, skipped=1, total=1)
        self.assertEqual(code, "values_unreadable")
        self.assertIn("实测值读不懂", msg)
        self.assertNotIn("可能是空表", msg)
        self.assertIn("不要改日期", nxt)

    def test_没有跳过信息时保留原话术_向后兼容(self):
        msg, _nxt, code = diagnose_missing_dates([], True)
        self.assertIn("可能是空表", msg)
        self.assertEqual(code, "no_rows")

    def test_坏xlsx给中文话术_不甩英文异常(self):
        from qc_daily.importer import _read_xlsx
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "坏文件.xlsx"
            p.write_bytes(b"not a real xlsx")
            with self.assertRaises(ValueError) as ctx:
                _read_xlsx(str(p))
        self.assertIn("这个 Excel 打不开", str(ctx.exception))
        self.assertNotIn("File is not a zip file", str(ctx.exception))

    def test_表里版本与执行版不一致要说不得放行(self):
        cfg = QCDailyConfig.load()
        notes: list[str] = []
        _check_version([{"standard": "GB 1350—2025"}], cfg, notes, set())
        self.assertTrue(any("【版本】" in n for n in notes))
        joined = "\n".join(notes)
        self.assertIn("不得作为放行依据", joined)
        self.assertIn(cfg.standard_version, joined)

    def test_版本一致时不吭声(self):
        cfg = QCDailyConfig.load()
        notes: list[str] = []
        _check_version([{"standard": cfg.standard_version}], cfg, notes, set())
        self.assertFalse(notes)
        self.assertEqual(_norm_std("GB 1350—2009"), _norm_std("gb 1350-2009"))


# ------------------------------------------------------------------ C 问答层
class TestIntentWording(unittest.TestCase):
    def test_分不清籼粳就反问_不并列贴两套限值(self):
        a = route("稻谷水分限值是多少")["answer"]
        joined = " ".join(a["lines"])
        self.assertIn("表1", joined)
        self.assertIn("表2", joined)
        self.assertIn("籼", joined)
        self.assertIn("粳", joined)
        self.assertNotIn("13.5", joined, "不许把 13.5 与 14.5 一起贴出来")
        self.assertNotIn("14.5", joined)

    def test_反问时带上当前执行版声明(self):
        a = route("稻谷水分限值是多少")["answer"]
        self.assertTrue(any("2009" in b and "不得" in b for b in a["boundary"]))

    def test_同义问法不再分叉(self):
        a1 = route("大米的水分限值是多少")["answer"]
        a2 = route("大米水分限值是多少")["answer"]
        self.assertEqual(a1["title"], a2["title"])
        self.assertIn("水分含量", a1["title"])

    def test_技术类越界与闲聊同一条路(self):
        a = route("帮我写一段 Python 爬虫代码")["answer"]
        self.assertIn("不在我的业务范围", a["title"])
        self.assertIn("本来就不覆盖", " ".join(a["lines"]))

    def test_不能把业务词代码判定误伤成越界(self):
        self.assertIsNone(route("DEMO-001 这批稻谷出糙率 76.5 能收吗"),
                          "这句该交给判定链，不能被越界正则拦掉")

    def test_纯符号有独立话术_不与写代码同答(self):
        a = route("？？？！！！……")["answer"]
        self.assertIn("没读懂", a["title"])
        self.assertNotEqual(a["title"], route("帮我写一段 Python 爬虫代码")["answer"]["title"])

    def test_边界话不再否认表里有数字(self):
        a = knowledge_answer("稻谷里砷的限值是多少")
        self.assertNotIn("没检索到带限值的表行", " ".join(a["boundary"]))
        # 2026-11-11 01:17 切版后限值已逐格人工核对入库，旧句
        # 「未核对原文前不能拿去判合格」被更准的一句替换；这里锁语义、不锁字面。
        self.assertTrue(any("已核对的结构化限值" in b or "不能拿去判" in b
                            for b in a["boundary"]),
                        "boundary 仍须明说：片段里的数字不能直接拿去判定")

    def test_不含双表时仍能答出单行限值(self):
        a = route("大米成品的Cd含量不得超过多少")["answer"]
        self.assertIn("0.2", " ".join(a["lines"]))

    def test_brief_拿到批次号就不再说没识别出批次(self):
        b = _brief({"batch_id": "DEMO-999", "sections": {},
                    "tools_called": ["lookup_limit"]})
        joined = " ".join(b["paras"])
        self.assertIn("DEMO-999", joined)
        self.assertNotIn("没识别出你要问的批次和指标", joined)


# ------------------------------------------------------------------ D 评测层
class TestNightlyAssertions(unittest.TestCase):
    def test_前七个场景改成反问断言_不再只问数字在不在(self):
        sc = rr.build_scenarios()
        first7 = sc[:7]
        self.assertTrue(all(s.get("expect_clarify") for s in first7))
        self.assertFalse(any(s.get("must_contain") for s in first7))
        self.assertTrue(all(s.get("goal") for s in first7),
                        "标题即验收标准：每条要写清过了说明什么能力")

    def test_反问断言验收对齐(self):
        scn = rr.S("QA-01", "问答·低置信度应反问", "稻谷水分限值是多少", "chat",
                   expect_clarify=True, clarify_entity="水分含量")
        facts = {"title": "先确认物类，再给「水分含量」的限值",
                 "lines": ["GB 1350 里「水分含量」分两张表：表1 是早籼，表2 是粳稻谷。",
                           "你验的是籼稻还是粳稻？确认后我再给对应那一格。"],
                 "boundary": [], "http": 200}
        verdict, notes = rr.auto_check(scn, facts)
        self.assertEqual(verdict, "PASS", notes)

    def test_http400中文错误直接算合格拒收(self):
        scn = rr.S("RJ-02", "拒绝", "不支持的扩展名 .txt", "task",
                   expect_refuse=True, question="做成日报")
        facts = {"http": 400, "error": "只收 Excel / CSV，你给的是 .txt"}
        verdict, notes = rr.auto_check(scn, facts)
        self.assertEqual(verdict, "PASS", notes)

    def test_拒答必须回扣被问的实体(self):
        scn = rr.S("RF-14", "问答·应拒答", "稻谷里砷的限值是多少", "chat",
                   payload={"question": "稻谷里砷的限值是多少"}, expect_refuse=True)
        # 只出现「铬…暂不收录」这种别处的否定词，不该算拒答成功
        facts = {"http": 200, "title": "查到的相关条款",
                 "lines": ["铬、亚硝酸盐：与稻谷验收关系弱，暂不收录。"],
                 "boundary": []}
        verdict, notes = rr.auto_check(scn, facts)
        self.assertEqual(verdict, "FAIL")
        self.assertTrue(any("没回扣" in n for n in notes))

    def test_裸数字流只记WARN不硬失败(self):
        scn = rr.S("QA-99", "问答·应能答", "压力", "chat")
        facts = {"http": 200, "title": "查到的相关条款",
                 "lines": ["≥79.0 ≥77.0 ≥75.0 ≥73.0 ≤1.0 ≤13.5 ≤1.0 ≤4.0 ≤5.0"],
                 "boundary": []}
        verdict, notes = rr.auto_check(scn, facts)
        self.assertEqual(verdict, "WARN")
        self.assertTrue(any("比较符" in n for n in notes))


class TestHistoryByDate(unittest.TestCase):
    def test_last_days按日期去重_同日重跑不挤掉前几天(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "history.jsonl")
            store = HistoryStore(p)
            for d, rate in (("2026-11-07", 0.9), ("2026-11-08", 0.8),
                            ("2026-11-09", 0.7), ("2026-11-09", 0.6),
                            ("2026-11-09", 0.5)):
                store.append({"report_date": d, "pass_rate": rate})
            rows = store.last_days(3)
        self.assertEqual([r["report_date"] for r in rows],
                         ["2026-11-07", "2026-11-08", "2026-11-09"])
        self.assertEqual(rows[-1]["pass_rate"], 0.5, "同日只留最后一次")


if __name__ == "__main__":
    unittest.main()
