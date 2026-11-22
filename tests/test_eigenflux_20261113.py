# -*- coding: utf-8 -*-
"""2026-11-13 从 EigenFlux 漫游抄回来的五条工程做法 · 回归测试

跑法：.venv/Scripts/python.exe -m unittest tests.test_eigenflux_20261113 -v

锁死的四件事：
1. 负控样本必须真的戳中判据；判据死了 / 判据换了 / 样本缺列都要报 UNKNOWN。
2. 观测三态：没跑、跑了没东西、真观测到，三者不能混。
3. 产物回读：三件套缺一记 UNKNOWN；只读源被动一个字节就是 FAIL。
4. 回扫校验门自己也要有负控，不能一直报 PASS 其实是瞎的。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nightly import check_doc_sync as cds       # noqa: E402
from nightly import negative_controls as nc     # noqa: E402
from nightly import run_round as rr             # noqa: E402
from qc_daily import readback as rb             # noqa: E402


def _always_pass(scn, facts):
    """故意做成一条死判据：什么都不拦。用来验 SILENT_GREEN 分支。"""
    return "PASS", []


class TestNegativeControls(unittest.TestCase):
    def test_1_三列齐全(self):
        cases = nc.load_controls()
        self.assertTrue(cases, "负控样本文件不能是空的")
        for c in cases:
            for col in nc.REQUIRED_COLUMNS:
                self.assertIn(col, c, f"{c.get('case_id')} 缺 {col}")

    def test_2_判据哈希与当前源码一致_整批PASS(self):
        r = nc.verify(rr.auto_check)
        self.assertEqual(r["overall"], "PASS", r["cases"])
        nd = r["denominator"]
        self.assertGreater(nd["effective"], 0)
        self.assertEqual(nd["silent_green"], 0)
        self.assertEqual(nd["criteria_drift"], 0)

    def test_3_判据死了必须报假绿而不是PASS(self):
        # 把哈希先对齐到这条假判据，否则会先被 CRITERIA_DRIFT 拦下，
        # 走不到「判据活着但什么都不拦」这一格（漂移优先是设计，不是 bug）。
        cases = []
        for c in nc.load_controls():
            c = dict(c)
            c["criteria_digest"] = nc.criteria_digest(
                _always_pass, c.get("injected_fault", ""))
            cases.append(c)
        r = nc.verify(_always_pass, cases)
        self.assertEqual(r["overall"], "UNKNOWN")
        self.assertEqual(r["denominator"]["criteria_drift"], 0)
        self.assertGreater(r["denominator"]["silent_green"], 0)
        self.assertTrue(any(c["state"] == "SILENT_GREEN" for c in r["cases"]))

    def test_4_判据源码换了旧负控自动作废(self):
        # _always_pass 的源码 != auto_check 的源码，哈希必然不同。
        self.assertNotEqual(nc.criteria_digest(rr.auto_check, "http_5xx"),
                            nc.criteria_digest(_always_pass, "http_5xx"))
        r = nc.verify(_always_pass)
        self.assertGreater(r["denominator"]["criteria_drift"], 0)

    def test_5_未执行样本单列不进有效分母(self):
        r = nc.verify(rr.auto_check)
        nd = r["denominator"]
        self.assertEqual(nd["not_executed"], 1)
        self.assertEqual(nd["touched"], nd["executed"] - 1)
        self.assertTrue(any(c["state"] == nc.NOT_EXECUTED for c in r["cases"]))

    def test_6_缺必需列记UNKNOWN(self):
        r = nc.verify(rr.auto_check, [{"case_id": "X", "arm_executed": True}])
        self.assertEqual(r["overall"], "UNKNOWN")
        self.assertEqual(r["denominator"]["bad_cases"], 1)

    def test_7_自测入口可直接调用(self):
        self.assertEqual(nc.self_test(rr.auto_check)["overall"], "PASS")


class TestObservationTristate(unittest.TestCase):
    def test_8_空观测等于没执行(self):
        state, _ = nc.classify_observation({"kind": "chat"}, {})
        self.assertEqual(state, nc.NOT_EXECUTED)

    def test_9_回执正常但观测全空记UNKNOWN(self):
        state, why = nc.classify_observation({"kind": "chat"}, {"http": 200})
        self.assertEqual(state, nc.UNKNOWN)
        self.assertIn("截断", why)

    def test_10_http400中文拒收是有效观测(self):
        state, _ = nc.classify_observation(
            {"kind": "task", "expect_refuse": True},
            {"http": 400, "error": "只收 Excel / CSV"})
        self.assertEqual(state, nc.OBSERVED)

    def test_11_正常回答是有效观测(self):
        state, _ = nc.classify_observation(
            {"kind": "chat"},
            {"http": 200, "title": "查到的相关条款", "lines": ["≥75.0"], "boundary": []})
        self.assertEqual(state, nc.OBSERVED)

    def test_12_只有本地异常没有回执算没执行(self):
        state, _ = nc.classify_observation({"kind": "chat"},
                                           {"error": "connection refused"})
        self.assertEqual(state, nc.NOT_EXECUTED)


class TestReadback(unittest.TestCase):
    def _fixture(self, td: str):
        root = Path(td)
        (root / "corpus").mkdir(exist_ok=True)
        (root / "corpus" / "limits.json").write_text('{"x": 1}', encoding="utf-8")
        before = rb.snapshot_sources(root)
        run = root / "runs" / "R1"
        run.mkdir(parents=True)
        (run / "request.json").write_text("{}", encoding="utf-8")
        (run / "trace.jsonl").write_text(
            json.dumps({"step": 1, "event": "request_saved"}) + "\n", encoding="utf-8")
        (run / "report.html").write_text(
            "<html><body>" + "日报内容 " * 20 + "</body></html>", encoding="utf-8")
        (run / "report.json").write_text('{"a": 1}', encoding="utf-8")
        return root, before, run

    def test_13_三件套齐全与源数据未变时PASS(self):
        with tempfile.TemporaryDirectory() as td:
            root, before, run = self._fixture(td)
            rep = rb.verify_run(run, ["report.html", "report.json"],
                                before, {"a": 1}, repo_root=root)
            self.assertEqual(rep["overall"], "PASS", rep["reason"])
            self.assertTrue(all(rep["triple"].values()))
            self.assertEqual(rep["side_effects"]["overall"], "PASS")
            self.assertTrue(all(a["digest"].startswith("sha256:") for a in rep["artifacts"]))

    def test_14_产物没落盘记UNKNOWN不降级成PASS(self):
        with tempfile.TemporaryDirectory() as td:
            root, before, run = self._fixture(td)
            rep = rb.verify_run(run, ["report.html", "missing.xlsx"],
                                before, {"a": 1}, repo_root=root)
            self.assertEqual(rep["overall"], "UNKNOWN")
            self.assertFalse(rep["triple"]["artifacts_landed_and_parseable"])
            self.assertEqual(rep["denominator"]["unparsed"], 1)

    def test_15_只读源被改一个字节就是FAIL(self):
        with tempfile.TemporaryDirectory() as td:
            root, before, run = self._fixture(td)
            (root / "corpus" / "limits.json").write_text('{"x": 2}', encoding="utf-8")
            rep = rb.verify_run(run, ["report.html"], before, {"a": 1}, repo_root=root)
            self.assertEqual(rep["overall"], "FAIL")
            self.assertEqual(rep["side_effects"]["overall"], "FAIL")
            self.assertTrue(rep["side_effects"]["changed"])

    def test_16_没拍到快照要如实说不知道(self):
        with tempfile.TemporaryDirectory() as td:
            root, _before, run = self._fixture(td)
            rep = rb.verify_run(run, ["report.html"], {}, {"a": 1}, repo_root=root)
            self.assertEqual(rep["overall"], "UNKNOWN")
            self.assertEqual(rep["side_effects"]["overall"], "UNKNOWN")

    def test_17_目录穿越产物被拒(self):
        with tempfile.TemporaryDirectory() as td:
            root, before, run = self._fixture(td)
            arts = rb.readback_artifacts(run, ["../evil.html"])
            self.assertFalse(arts[0]["parseable"])
            self.assertIn("越界", arts[0]["why"])


class TestDocSyncGate(unittest.TestCase):
    def test_18_校验门自身有负控(self):
        self.assertEqual(cds.self_test(), 0)

    def test_19_注入旧词会命中(self):
        rules = cds.load_ledger()
        bad = rules[0]["禁用旧词"][0]
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "创新月_样本.html").write_text(
                f"<p>台账禁用短语：{bad} —— 没有任何说明。</p>", encoding="utf-8")
            hits = cds.scan(ws, rules)
            self.assertTrue(hits)
            self.assertEqual(hits[0]["rule"], rules[0]["id"])

    def test_20_带更正说明的历史引用放行(self):
        rules = cds.load_ledger()
        bad = rules[0]["禁用旧词"][0]
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "创新月_样本.html").write_text(
                f"<p>上一版写的是「{bad}」，已更正，这里只是历史说明。</p>",
                encoding="utf-8")
            self.assertEqual(cds.scan(ws, rules), [])

    def test_21_真实工作区当前无台账标记的旧结论(self):
        ws = cds.find_workspace()
        self.assertEqual(cds.scan(ws, cds.load_ledger()), [],
                         "有旧结论残留，先按台账改干净再提交")


class TestRoundReport(unittest.TestCase):
    def test_22_巡检报告写进三态分母与负控结论(self):
        # 故意不起服务、不发请求、不碰真表；只验「报告里到底有没有这两段」。
        with tempfile.TemporaryDirectory() as td:
            saved = (rr.OUT_DIR, rr.STATE, rr.NIGHTLY_STATE, rr.ensure_server)
            rr.OUT_DIR = Path(td)
            rr.STATE = Path(td) / "state.json"
            rr.NIGHTLY_STATE = Path(td) / "state-nightly.json"
            rr.ensure_server = lambda *a, **k: (False, "单测：故意不起服务")
            try:
                rr.run_round(1)
                md = (Path(td) / "round-01.md").read_text(encoding="utf-8")
                meta = json.loads((Path(td) / "round-01.json").read_text(encoding="utf-8"))
            finally:
                rr.OUT_DIR, rr.STATE, rr.NIGHTLY_STATE, rr.ensure_server = saved
            self.assertIn("## 分母（三态）", md)
            self.assertIn("## 负控样本：", md)
            self.assertIn("denominator", meta)
            self.assertEqual(meta["negative_controls"]["overall"], "PASS")
            self.assertEqual(meta["denominator"]["executed"], 0,
                             "服务没起时执行数应为 0，不能虚报跑过场景")


if __name__ == "__main__":
    unittest.main()
