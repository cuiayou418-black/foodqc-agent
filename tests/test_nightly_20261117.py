# -*- coding: utf-8 -*-
"""11/14–11/17 巡检修复回归。

三组：
1. run_page 抽正文 —— 修 11-13 三态闸门引入的假 UNKNOWN（11-14 r06 整轮 8 场景全废），
   同时锁死「真没正文仍然 UNKNOWN」，防止把闸门放松过头。
2. Excel / PDF 无生产记录口径 —— 11-17 S1，累计第 6 晚，Excel 印 0 / 0.00%
   而同 run 的 HTML 印「—（无生产记录）」。
3. 导入层失败留痕 —— 11-17 G-2，复发第 4 晚，6/6 失败 run 无 snapshots / audit_entry。
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

from nightly import negative_controls as nc      # noqa: E402
from nightly import run_round as rr              # noqa: E402
from qc_daily import exporters                    # noqa: E402
from qc_daily.models import CollectedData, DailyMetrics  # noqa: E402
from qc_daily.tasks import runner as tr          # noqa: E402


class TestPageObservation(unittest.TestCase):
    @staticmethod
    def _run_page(raw: str) -> dict:
        def fake_http(method, path, payload=None, timeout=30):
            return {"http": 200, "seconds": 0.1, "body": {"_raw": raw}}
        old = rr.http
        rr.http = fake_http
        try:
            return rr.run_page({"kind": "page", "path": "/app"})
        finally:
            rr.http = old

    def test_1_页面正文被抽成观测_不再整轮假UNKNOWN(self):
        raw = ("<html><head><script>bad()</script><style>x{}</style></head>"
               "<body><h1>品控日报</h1><p>今天来了三批样</p></body></html>")
        facts = self._run_page(raw)
        self.assertIn("品控日报", facts.get("content", ""))
        self.assertIn("今天来了三批样", facts.get("content", ""))
        self.assertNotIn("bad()", facts.get("content", ""))
        state, _ = nc.classify_observation({"kind": "page"}, facts)
        self.assertEqual(state, nc.OBSERVED, "有正文的页面不能再被判成没观测")

    def test_2_真的渲染不出正文仍然UNKNOWN(self):
        facts = self._run_page("<html><head></head><body></body></html>")
        state, why = nc.classify_observation({"kind": "page"}, facts)
        self.assertEqual(state, nc.UNKNOWN)
        self.assertIn("截断", why)


class TestNoProductionConsistency(unittest.TestCase):
    @staticmethod
    def _metrics() -> DailyMetrics:
        return DailyMetrics(
            report_date="2026-11-17", plant="示例米厂（占位）",
            generated_at="2026-11-17 05:00:00",
            batch_count=0, planned_qty=0.0, actual_qty=0.0, achievement=0.0,
            total_tests=8, fail_tests=0, pass_rate=1.0,
            batch_pass_rate=0.0, abnormal_batches=[],
        )

    def test_3_excel_无生产记录时产量不印0(self):
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "report.xlsx"
            exporters.export_excel(self._metrics(), CollectedData(report_date="2026-11-17"),
                                   {"title": "测试日报"}, str(p))
            ws = load_workbook(p).worksheets[0]
            vals = {str(ws.cell(row=r, column=1).value): ws.cell(row=r, column=2).value
                    for r in range(5, 20)}
            self.assertEqual(vals.get("产量达成率"), "—（无生产记录）")
            self.assertEqual(vals.get("计划产量(吨)"), "—（无生产记录）")
            self.assertEqual(vals.get("生产批次"), "—（无生产记录）")
            self.assertNotEqual(vals.get("产量达成率"), "0.00%")

    def test_4_pdf_指标为字符串时不抛异常(self):
        m = self._metrics()
        # runner 在无生产记录时会把这两个字段改成字符串；旧版 f"{str:.2%}" 会崩。
        m.batch_pass_rate = "—（无生产记录）"
        m.achievement = "—（无生产记录）"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "report.pdf"
            exporters.export_pdf(m, {"title": "测试日报"}, str(p))
            self.assertTrue(p.is_file())
            self.assertGreater(p.stat().st_size, 0)


class TestFailureEvidence(unittest.TestCase):
    def test_5_导入层判死也留快照与审计(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "R1"
            (run / "inputs").mkdir(parents=True)
            (run / "inputs" / "bad.xlsx").write_bytes(b"x")
            res = tr.TaskResult(run_id="R1", task_id="grain_daily_report",
                                report_date="2026-11-17", status="failed",
                                run_dir=str(run))
            res.errors.append("宽表缺少批次列")
            ctx: dict = {}
            tr.TaskRunner()._leave_failure_evidence(run, res, ctx)
            self.assertTrue((run / "snapshots" / "bad.xlsx").is_file())
            entry = json.loads((run / "audit_entry.json").read_text(encoding="utf-8"))
            self.assertEqual(entry["scope"], "import_layer")
            self.assertIn("宽表缺少批次列", entry["errors"][0])
            self.assertEqual(ctx.get("snapshot_dir"), "snapshots/")

    def test_6_已有真实审计条目不被覆盖(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "R2"
            run.mkdir()
            (run / "audit_entry.json").write_text('{"scope": "engine"}', encoding="utf-8")
            res = tr.TaskResult(run_id="R2", task_id="grain_daily_report",
                                report_date="2026-11-17", status="failed",
                                run_dir=str(run))
            tr.TaskRunner()._leave_failure_evidence(run, res, {})
            self.assertEqual(json.loads((run / "audit_entry.json").read_text(
                encoding="utf-8"))["scope"], "engine")


class TestExpectedDeclaration(unittest.TestCase):
    def test_7_判读前落应产声明含输入指纹(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "R3"
            (run / "inputs").mkdir(parents=True)
            (run / "inputs" / "sample.xlsx").write_bytes(b"demo bytes")
            res = tr.TaskResult(run_id="R3", task_id="grain_daily_report",
                                report_date="2026-11-17", run_dir=str(run))
            ctx = {"import_rows": {"quality": 8}}
            decl = tr.TaskRunner()._declare_expected(run, res, ctx)
            self.assertTrue((run / "expected_manifest.json").is_file())
            self.assertTrue(decl["input_fingerprint"].startswith("sha256:"))
            self.assertEqual(decl["expected_checks"], 8)
            self.assertIn("report.html", decl["expected_outputs"])
            self.assertEqual(ctx["expected_manifest"]["run_id"], "R3")
            self.assertEqual(ctx["expected_manifest"]["task_id"], "grain_daily_report")

    def test_8_应产未落全记UNKNOWN不记PASS(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "R4"
            run.mkdir()
            res = tr.TaskResult(run_id="R4", task_id="grain_daily_report",
                                report_date="2026-11-17", run_dir=str(run))
            res.outputs = ["report.html"]
            tr.TaskRunner()._declare_expected(run, res, {})
            (run / "expected_manifest.json").write_text(json.dumps({
                "expected_outputs": ["report.html", "report.xlsx"]}), encoding="utf-8")
            chk = tr.TaskRunner()._check_declaration(run, res, {})
            self.assertEqual(chk["state"], "UNKNOWN")
            self.assertIn("report.xlsx", chk["reason"])

    def test_9_声明缺失记判不了(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "R5"
            run.mkdir()
            res = tr.TaskResult(run_id="R5", task_id="grain_daily_report", run_dir=str(run))
            chk = tr.TaskRunner()._check_declaration(run, res, {})
            self.assertEqual(chk["state"], "UNKNOWN")
            self.assertIn("没有判读前的应产声明", chk["reason"])


if __name__ == "__main__":
    unittest.main()
