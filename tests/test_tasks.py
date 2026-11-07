# -*- coding: utf-8 -*-
"""业务任务注册表 / TaskRunner / 8 块报告 · 回归测试（标准库 unittest，无新依赖）

跑法：.venv/Scripts/python.exe tests/test_tasks.py
     或 .venv/Scripts/python.exe -m unittest discover -s tests

4a 按裁决 B+A 写：同输入两次 input_digest 与 conclusion_digest 一致；
4b 单次运行内 audit.verify_report(report.html) 为 True。
明确不断言 data_checksum / report_fingerprint 跨运行相等（它们是单次溯源字段）。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qc_daily.artifacts import list_artifacts, resolve_artifact  # noqa: E402
from qc_daily.audit import verify_report  # noqa: E402
from qc_daily.config import QCDailyConfig  # noqa: E402
from qc_daily.tasks import TASKS, TaskRequest, TaskRunner, get_run, list_tasks  # noqa: E402
from qc_daily.tasks.report import BLOCKS  # noqa: E402
from qc_daily.tasks.request import RequestError  # noqa: E402
from qc_daily.tasks.runner import DIGEST_NOTES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class TestTasksPhase1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="foodqc_tasks_"))
        cls.cfg = QCDailyConfig.load()
        # agent 的 outputs / 审计 / 快照都写进临时目录，不污染 qc_daily_data/
        cls.cfg.raw["export"]["output_dir"] = str(cls.tmp / "agent_out")
        cls.sample = ROOT / "samples" / "稻谷验收_假数据.csv"
        cls.bad_pdf = cls.tmp / "手写表单.pdf"
        cls.bad_pdf.write_text("not a structured table", encoding="utf-8")
        cls.out_root = str(cls.tmp / "runs")

        def make_req() -> TaskRequest:
            return TaskRequest(task_id="grain_daily_report",
                               inputs=[str(cls.sample)], report_date="2026-11-05",
                               formats=["html", "excel"], output_root=cls.out_root)

        cls.make_req = staticmethod(make_req)
        cls.res = TaskRunner(config=cls.cfg).run(make_req())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_1_registry_three_primary_plus_auxiliary(self):
        """首页 3 个一等卡片；ask_qa 只在「更多」里。"""
        ids = [t["task_id"] for t in list_tasks()]
        self.assertEqual(ids, ["grain_daily_report", "grain_batch_judge", "quality_record_audit"])
        more = [t["task_id"] for t in list_tasks(include_auxiliary=True)]
        self.assertIn("ask_qa", more)
        self.assertTrue(TASKS["ask_qa"].auxiliary)
        self.assertEqual(len(BLOCKS), 8)

    def test_2_request_validation_rejects_bad_input(self):
        with self.assertRaises(RequestError):
            TaskRequest(task_id="not_registered", inputs=[str(self.sample)]).validate(TASKS.keys())
        with self.assertRaises(RequestError):
            TaskRequest(task_id="grain_daily_report", inputs=[]).validate(TASKS.keys())
        with self.assertRaises(RequestError) as cm:
            TaskRequest(task_id="grain_daily_report",
                        inputs=[str(self.bad_pdf)]).validate(TASKS.keys())
        self.assertIn("不支持", str(cm.exception))
        with self.assertRaises(RequestError):
            TaskRequest(task_id="grain_daily_report", inputs=[str(self.sample)],
                        formats=["pdf"]).validate(TASKS.keys())     # HTML 必出
        with self.assertRaises(RequestError):
            TaskRequest(task_id="grain_daily_report", inputs=[str(self.sample)],
                        push=True).validate(TASKS.keys())           # 不外发

    def test_3_state_machine_writes_trace(self):
        states = [t["state"] for t in self.res.trace]
        for s in ("queued", "collecting", "validating", "analyzing", "rendering"):
            self.assertIn(s, states)
        self.assertEqual(self.res.status, "done")
        run_dir = Path(self.res.run_dir)
        self.assertTrue((run_dir / "trace.jsonl").is_file())
        self.assertTrue((run_dir / "request.json").is_file())

    def test_4_eight_blocks_and_single_run_verify(self):
        """4b：单次运行内 verify_report(report.html) 返回 True，8 块齐全。"""
        run_dir = Path(self.res.run_dir)
        html = (run_dir / "report.html").read_text(encoding="utf-8")
        for i, block in enumerate(BLOCKS, 1):
            self.assertIn(f">{i}. {block}", html)
        self.assertIn("结论仅供复核，不得作为放行依据；最终由人签字", html)
        ok, _ = verify_report(run_dir / "report.html")
        self.assertTrue(ok)
        self.assertTrue(self.res.verify_ok)
        self.assertEqual(len(json.loads(
            (run_dir / "verdicts.json").read_text(encoding="utf-8"))), 10)

    def test_5_4a_reproducible_digests(self):
        """同输入两次：input_digest + conclusion_digest 一致；不碰单次字段。"""
        res2 = TaskRunner(config=self.cfg).run(self.make_req())
        self.assertEqual(self.res.status, "done")
        self.assertEqual(res2.status, "done")
        self.assertTrue(self.res.input_digest)
        self.assertEqual(self.res.input_digest, res2.input_digest)
        self.assertTrue(self.res.conclusion_digest)
        self.assertEqual(self.res.conclusion_digest, res2.conclusion_digest)
        for r in (self.res, res2):
            ok, _ = verify_report(Path(r.run_dir) / "report.html")
            self.assertTrue(ok)
        # 定位写死：data_checksum / report_fingerprint 只做单次溯源，不做跨运行断言
        self.assertIn("不做跨运行断言", DIGEST_NOTES["data_checksum"])
        self.assertIn("不做跨运行断言", DIGEST_NOTES["report_fingerprint"])
        summary = json.loads(
            (Path(res2.run_dir) / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["input_digest"], res2.input_digest)
        self.assertEqual(summary["digest_notes"], DIGEST_NOTES)

    def test_6_artifact_path_whitelist(self):
        self.assertTrue(list_artifacts(self.res.run_id, self.out_root))
        with self.assertRaises(ValueError):
            resolve_artifact(self.res.run_id, "../../qc_daily_config.json", self.out_root)
        with self.assertRaises(ValueError):
            resolve_artifact("../evil", "summary.json", self.out_root)

    def test_7_get_run_and_outputs_exist(self):
        r = get_run(self.res.run_id, self.out_root)
        self.assertIsNotNone(r)
        self.assertEqual(r.status, "done")
        run_dir = Path(self.res.run_dir)
        for rel in self.res.outputs:
            self.assertTrue((run_dir / rel).is_file(), rel)
        for name in ("input.db", "inputs", "snapshots", "audit_entry.json"):
            self.assertTrue((run_dir / name).exists(), name)


class TestRiceWideTable(unittest.TestCase):
    """Phase 2：稻谷宽表识别为 quality，7 项指标都映射上（不重写导入逻辑）。"""

    def test_rice_wide_table_maps_seven_items(self):
        import csv

        from qc_daily.importer import KNOWN_ITEMS, convert, detect_type, map_headers, read_tabular

        expected = ("出糙率", "整精米率", "杂质含量", "水分含量",
                    "黄粒米含量", "谷外糙米含量", "互混率")
        for item in expected:
            self.assertIn(item, KNOWN_ITEMS)
        tmp = Path(tempfile.mkdtemp(prefix="foodqc_rice_wide_"))
        try:
            p = tmp / "稻谷质检宽表.csv"
            with open(p, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["批次号", "产品名称", "检验日期"] + list(expected))
                w.writerow(["DEMO-001", "籼稻谷", "2026-11-05",
                            76.5, 45.0, 0.8, 13.2, 0.5, 2.0, 3.0])
            sheets = read_tabular(str(p))
            self.assertEqual(len(sheets), 1)
            records = sheets[0][1]
            mapping = map_headers(records)
            self.assertEqual(mapping.get("批次号"), "batch_id")
            self.assertEqual(mapping.get("产品名称"), "product")
            self.assertEqual(mapping.get("检验日期"), "tested_at")
            self.assertEqual(detect_type(p.name, mapping), "quality")
            rows, notes = convert(p.stem, records, "quality", QCDailyConfig.load())
            self.assertTrue(any("宽表" in n for n in notes))
            self.assertEqual(len(rows), 7)
            self.assertTrue(set(expected).issubset({r["item"] for r in rows}))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestTaskExceptions(unittest.TestCase):
    """第 7 部分异常分支：表头不认识 / error 级校验 / 导出降级 / 模型回落。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="foodqc_task_exc_"))
        cls.cfg = QCDailyConfig.load()
        cls.cfg.raw["export"]["output_dir"] = str(cls.tmp / "agent_out")
        cls.out_root = str(cls.tmp / "runs")
        cls.sample = str(ROOT / "samples" / "稻谷验收_假数据.csv")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _csv(self, name, headers, rows):
        import csv

        p = self.tmp / name
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows)
        return p

    def _run(self, path, formats=None):
        req = TaskRequest(task_id="grain_daily_report", inputs=[str(path)],
                          report_date="2026-11-05", formats=formats or ["html", "excel"],
                          output_root=self.out_root)
        return TaskRunner(config=self.cfg).run(req)

    def test_1_unmapped_headers_rejected_with_trace(self):
        p = self._csv("unknown.csv", ["foo", "bar", "baz"], [["x", "y", "z"]])
        res = self._run(p)
        self.assertEqual(res.status, "failed")
        self.assertTrue(any("表头" in e for e in res.errors))
        self.assertTrue(any("无法识别表类型" in n for n in res.notes))
        self.assertTrue((Path(res.run_dir) / "trace.jsonl").is_file())

    def test_2_validation_error_no_html_but_snapshot_and_trace(self):
        headers = ["批次号", "产品名称", "检测项目", "实测值", "单位", "指标方向",
                   "限值", "标准号", "限值出处", "检验日期", "检验员", "班组"]
        row = ["DEMO-001", "籼稻谷", "出糙率", 76.5, "%", "≥", 0,
               "GB 1350—2025", "GB 1350—2025 表1 第3等 原文第5页",
               "2026-11-05", "假检验员", "假班组"]
        p = self._csv("bad_limit.csv", headers, [row])
        res = self._run(p, formats=["html"])
        self.assertEqual(res.status, "failed")
        self.assertFalse((Path(res.run_dir) / "report.html").exists())
        self.assertTrue((Path(res.run_dir) / "snapshots").exists())
        self.assertTrue((Path(res.run_dir) / "trace.jsonl").is_file())
        self.assertTrue(any("宁可不发" in e for e in res.errors))
        self.assertTrue(any(a.get("level") == "critical"
                            and a.get("rule_id") == "data_validation_failed"
                            for a in res.alerts), res.alerts)

    def test_3_export_failure_degrades_to_partial(self):
        from unittest import mock

        with mock.patch("qc_daily.agent.export_pdf", side_effect=RuntimeError("模拟 PDF 失败")):
            res = self._run(self.sample, formats=["html", "pdf"])
        self.assertEqual(res.status, "partial")
        self.assertIn("report.html", res.outputs)
        self.assertTrue(any("导出降级" in e or "PDF" in e for e in res.errors))

    def test_4_model_fallback_to_rules_and_cli_entry(self):
        import contextlib
        import io

        from qc_daily import flags, task_cli
        from qc_daily.intent import understand_task

        token = flags.set_llm(False)
        try:
            got = understand_task("帮我把这几张表做成稻谷验收日报", [self.sample])
            self.assertTrue(got["ok"])
            self.assertEqual(got["task_id"], "grain_daily_report")
            self.assertEqual(got["params"]["router"], "rules")
            self.assertNotIn("limit", str(got["params"]))
            unknown = understand_task("今天天气怎么样", [])
            self.assertFalse(unknown["ok"])
            self.assertEqual(len(unknown["candidates"]), 3)   # 白名单外给候选，不硬猜
        finally:
            flags.reset_llm(token)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = task_cli.execute(task_id="grain_daily_report", inputs=[self.sample],
                                  report_date="2026-11-05", formats=["html", "excel"],
                                  output_root=self.out_root)
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        for key in ("run_id", "status", "verify_report", "input_digest", "conclusion_digest"):
            self.assertIn(key, out)

    def test_5_audit_pending_review_still_renders_8_blocks(self):
        """pending_review 不能提前收尾：录入自查任务也必须出 8 块报告（4.4）。"""
        from qc_daily.tasks.report import BLOCKS

        req = TaskRequest(task_id="quality_record_audit", inputs=[self.sample],
                          report_date="2026-11-05", formats=["html"],
                          output_root=self.out_root)
        res = TaskRunner(config=self.cfg).run(req)
        self.assertEqual(res.status, "pending_review")
        html = (Path(res.run_dir) / "report.html").read_text(encoding="utf-8")
        for i, block in enumerate(BLOCKS, 1):
            self.assertIn(f">{i}. {block}", html)
        self.assertTrue(res.verify_ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
