# -*- coding: utf-8 -*-
# task_cli.py：不依赖 Flask 的业务任务独立入口（Phase 3 最核心验收点）。
# 输入：--task / --input / --date / --formats / --out；输出：run 目录下完整报告 + 结果摘要。
# 停掉 Web、注释 /app 也能跑；只调 qc_daily.tasks，不 import 任何 Web 模块。
from __future__ import annotations

import argparse
import os
import sys

from .config import QCDailyConfig
from .tasks import TaskRequest, TaskRunner


def _utf8_stdout() -> None:
    """只在本入口改 stdout 编码，不动全局 console.say()（既有链路共用函数）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass


def execute(task_id: str, inputs: list[str], report_date: str = "",
            formats: list[str] | None = None, output_root: str = "runs",
            config_path: str | None = None, reviewed_by: str = "") -> int:
    _utf8_stdout()
    cfg = QCDailyConfig.load(config_path) if config_path else QCDailyConfig.load()
    fmts = list(formats or cfg.export.get("formats", ["html", "pdf", "excel"]))
    req = TaskRequest(task_id=task_id, inputs=list(inputs), report_date=report_date,
                      formats=fmts, output_root=output_root, push=False,
                      reviewed_by=reviewed_by)
    res = TaskRunner(config=cfg).run(req)

    print("=" * 68)
    print("业务任务执行结果（独立入口，未启动 Flask）")
    print("=" * 68)
    print(f"run_id   : {res.run_id}")
    print(f"task_id  : {res.task_id}")
    print(f"status   : {res.status}")
    print(f"run_dir  : {res.run_dir}")
    print("产物文件（真实路径，可直接打开）：")
    for rel in res.outputs:
        p = os.path.join(res.run_dir, rel)
        print(f"  - {p}{'' if os.path.isfile(p) else '   [缺失]'}")
    print(f"data_checksum      : {res.data_checksum or '-'}   [采集层 · 仅单次溯源]")
    print(f"input_digest       : {res.input_digest or '-'}   [数据层 · 跨运行可复现]")
    print(f"conclusion_digest  : {res.conclusion_digest or '-'}   [结论层 · 跨运行可复现]")
    print(f"report_fingerprint : {res.report_fingerprint or '-'}   [产物层 · 仅单次防篡改]")
    print(f"verify_report      : {'通过' if res.verify_ok else '未通过 / 本任务无 HTML 报告'}")
    for e in res.errors:
        print(f"[error] {e}")
    for n in res.notes:
        print(f"[note ] {n}")
    print("=" * 68)
    return 0 if res.status in ("done", "partial") else 1


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    p = argparse.ArgumentParser(prog="qc_daily task", description="业务任务独立入口")
    p.add_argument("--task", required=True, help="任务 ID（grain_daily_report / grain_batch_judge / quality_record_audit）")
    p.add_argument("--input", nargs="+", required=True, help="表格文件或目录，可多个")
    p.add_argument("--date", help="报告日期 YYYY-MM-DD（默认昨天）")
    p.add_argument("--formats", nargs="+", help="导出格式，空格分隔，如 html excel")
    p.add_argument("--out", default="runs", help="run 根目录（默认项目根 runs/）")
    p.add_argument("--config", help="配置文件路径")
    p.add_argument("--reviewed-by", default="",
                   help="班长审核人姓名。填了才出经理版正式件；不填则经理版恒为「草稿·待审核」")
    a = p.parse_args(argv)
    return execute(task_id=a.task, inputs=a.input, report_date=a.date or "",
                   formats=a.formats, output_root=a.out, config_path=a.config,
                   reviewed_by=a.reviewed_by)


if __name__ == "__main__":
    sys.exit(main())
