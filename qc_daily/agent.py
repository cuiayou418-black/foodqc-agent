"""品控日报智能体 · 主编排器（Agent 本体）

一次完整运行（run）的流水线：

    解析日期 → 采集三系统数据 → 数据校验 → 落盘原始快照 → 代码计算指标
    → 评估预警规则 → 渲染 HTML 日报 → 导出 PDF/Excel → 推送预警 → 写审计日志

可靠性设计：
- 数据校验 error 级 → 拒绝发布报告（宁可不发，不可错发），并触发数据质量预警；
- 任何一步异常都不中断已产出的部分（status=partial），全部记入审计日志；
- 全流程只读源系统，产物全部是新增文件。
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from .analysis import HistoryStore, compute_metrics, evaluate_alerts
from .alerting import Notifier
from .audit import AuditLogger, new_run_id, save_raw_snapshot
from .collectors import build_data_source, validate_collected
from .config import QCDailyConfig
from .console import say
from .exporters import export_excel, export_pdf
from .models import Alert, DailyMetrics, RunResult, fingerprint, iso_now
from .templates import render_html

_STEP = lambda tag, msg: say(f"[{tag}] {msg}")


class QCDailyAgent:
    """品控日报智能体：配置驱动、全代码计算、全程可追溯。"""

    def __init__(self, config: QCDailyConfig):
        self.config = config
        self.output_root = config.output_root
        self.audit = AuditLogger(os.path.join(self.output_root, "audit", "audit.jsonl"))
        self.history = HistoryStore(os.path.join(self.output_root, "audit", "history.jsonl"))
        self.notifier = Notifier(config)
        self.source = build_data_source(config)

    # ---------------------------------------------------------------- 日期解析

    def resolve_report_date(self, report_date: date | None = None) -> date:
        if report_date:
            return report_date if isinstance(report_date, date) else date.fromisoformat(report_date)
        now = datetime.now()
        return now.date() - timedelta(days=1) \
            if self.config.report.get("report_for", "previous_day") == "previous_day" else now.date()

    # ---------------------------------------------------------------- 主流程

    def run(self, report_date: date | None = None, push: bool | None = None,
            formats: list | None = None) -> RunResult:
        run_id = new_run_id()
        started = iso_now()
        target = self.resolve_report_date(report_date)
        formats = formats or self.config.export.get("formats", ["html", "pdf", "excel"])
        do_push = (push is not None) or bool(self.config.push.get("console", {}).get("enabled", True)) \
            or self.config.push.get("email", {}).get("enabled", False) \
            or self.config.push.get("webhook", {}).get("enabled", False)

        _STEP("启动", f"运行编号 {run_id} ｜ 报告日期 {target} ｜ 数据源 {self.config.source.get('type')}")
        result = RunResult(run_id=run_id, status="failed", report_date=target.isoformat(),
                           started_at=started)
        errors: list[str] = []
        data_checksum = ""
        data_counts = {"production": 0, "quality": 0, "warehouse": 0}
        snapshot_info: dict = {}
        outputs: dict[str, str] = {}
        metrics: DailyMetrics | None = None
        alerts: list[Alert] = []
        deliveries: list = []

        try:
            # ---- 1. 采集 ----
            _STEP("采集", "从生产/质检/仓储系统拉取数据 ...")
            data = self.source.collect(target)
            data_checksum = data.checksum
            data_counts = {"production": len(data.production),
                           "quality": len(data.quality),
                           "warehouse": len(data.warehouse)}
            _STEP("采集", f"完成：生产 {len(data.production)} 条 ｜ 质检 {len(data.quality)} 条 ｜ "
                          f"仓储 {len(data.warehouse)} 条 ｜ 数据指纹 {data_checksum[:16]}")

            # ---- 2. 数据校验 ----
            issues = validate_collected(data, self.config)
            err_issues = [i for i in issues if i.level == "error"]
            warn_issues = [i for i in issues if i.level == "warning"]
            _STEP("校验", f"错误 {len(err_issues)} 项 ｜ 提示 {len(warn_issues)} 项")
            for i in err_issues:
                _STEP("校验", f"  ⛔ {i.message}（{i.ref}）")
            for i in warn_issues:
                _STEP("校验", f"  ⚠️ {i.message}（{i.ref}）")

            # ---- 3. 原始快照落盘（即使校验失败也留痕，便于排查） ----
            snapshot_info = save_raw_snapshot(self.output_root, data)
            _STEP("快照", f"已保存至 {snapshot_info['snapshot_dir']}")

            # ---- 4. 指标计算 ----
            _STEP("分析", "按配置限值计算合格率/缺陷分布/异常批次/趋势 ...")
            history = self.history.last_days(int(self.config.analysis.get("trend_days", 7)))
            metrics = compute_metrics(data, self.config, history)
            metrics.validation_issues = issues
            _STEP("分析", f"总体合格率 {metrics.pass_rate:.2%} ｜ 一次交验 {metrics.batch_pass_rate:.2%} ｜ "
                          f"异常批次 {len(metrics.abnormal_batches)} 个")

            # ---- 5. 预警评估 ----
            alerts = evaluate_alerts(metrics, self.config.alert_rules)
            metrics.alerts = alerts
            _STEP("预警", f"命中 {len(alerts)} 条：" +
                  ("；".join(f"[{a.level}]{a.title}" for a in alerts) if alerts else "无"))

            # ---- 5.5 数据质量预警（校验失败也必须让人知道） ----
            if err_issues:
                alerts.append(Alert(
                    rule_id="data_validation_failed", level="critical",
                    title="日报数据校验未通过，报告未发布",
                    detail=f"{target} 数据存在 {len(err_issues)} 项错误："
                           + "；".join(e.message for e in err_issues[:5])
                           + "。已按『宁可不发，不可错发』原则拒绝发布，请人工核查源系统。",
                    refs=[e.ref for e in err_issues if e.ref]))
                _STEP("预警", "⛔ 数据校验失败，拒绝发布报告")

            # ---- 6. 渲染 + 导出 ----
            report_fingerprint = ""
            outputs: dict[str, str] = {}
            if not err_issues:
                report_dir = os.path.join(self.output_root, "reports", target.isoformat())
                os.makedirs(report_dir, exist_ok=True)
                title = self.config.report.get("title", "食用油中控品控日报")
                base_meta = {
                    "run_id": run_id, "title": title,
                    "source_type": self.config.source.get("type", ""),
                    "data_checksum": data_checksum,
                    "audit_path": self.audit.path, "snapshot_dir": snapshot_info["snapshot_dir"],
                    "provenance": [p.to_dict() for p in data.provenance],
                    "warehouse_limits": self.config.warehouse_limits,
                    "output_root": self.output_root,
                }

                if "html" in formats:
                    title = self.config.report.get("title", "食用油中控品控日报")
                    html = render_html(metrics, base_meta)  # 含 __FP__ 占位符
                    report_fingerprint = fingerprint(html)  # 对占位版计算，保证可复算
                    html = html.replace("__FP__", report_fingerprint)
                    html_path = os.path.join(report_dir, f"品控日报_{target.isoformat()}.html")
                    with open(html_path, "w", encoding="utf-8") as f:
                        f.write(html)
                    outputs["html"] = html_path
                    _STEP("报告", f"HTML 已生成: {html_path}（指纹 {report_fingerprint[:16]}）")

                full_meta = {**base_meta, "fingerprint": report_fingerprint}
                if "pdf" in formats:
                    try:
                        pdf_path = os.path.join(report_dir, f"品控日报_{target.isoformat()}.pdf")
                        export_pdf(metrics, full_meta, pdf_path)
                        outputs["pdf"] = pdf_path
                        _STEP("导出", f"PDF 已生成: {pdf_path}")
                    except Exception as e:
                        errors.append(f"PDF 导出失败: {e}")
                        _STEP("导出", f"⛔ PDF 导出失败: {e}")
                if "excel" in formats:
                    try:
                        xlsx_path = os.path.join(report_dir, f"品控日报_{target.isoformat()}.xlsx")
                        export_excel(metrics, data, full_meta, xlsx_path)
                        outputs["excel"] = xlsx_path
                        _STEP("导出", f"Excel 已生成: {xlsx_path}")
                    except Exception as e:
                        errors.append(f"Excel 导出失败: {e}")
                        _STEP("导出", f"⛔ Excel 导出失败: {e}")

                # ---- 7. 推送预警 ----
                if do_push:
                    push_alerts = [a for a in alerts if a.rule_id != "data_validation_failed"]
                    deliveries = self.notifier.push(push_alerts, metrics, full_meta)
                else:
                    _STEP("推送", "已禁用推送（--no-push）")

                # ---- 8. 历史沉淀（趋势分析的原料） ----
                self.history.append({
                    "report_date": target.isoformat(), "run_id": run_id,
                    "pass_rate": round(metrics.pass_rate, 4),
                    "batch_pass_rate": round(metrics.batch_pass_rate, 4),
                    "total_tests": metrics.total_tests, "fail_tests": metrics.fail_tests,
                    "abnormal_batch_count": len(metrics.abnormal_batches),
                    "near_limit_count": metrics.near_limit_count,
                    "batch_count": metrics.batch_count,
                    "planned_qty": metrics.planned_qty, "actual_qty": metrics.actual_qty,
                    "achievement": round(metrics.achievement, 4),
                    "item_fail": {it.item: it.fail for it in metrics.items},
                    "item_mean": {it.item: round(
                        sum(r.value for r in data.quality if r.item == it.item)
                        / max(1, it.total), 4) for it in metrics.items},
                    "alerts_count": len(alerts), "report_fingerprint": report_fingerprint,
                    "recorded_at": iso_now(),
                })
                _STEP("历史", "已追加至趋势历史（history.jsonl）")
            else:
                outputs = {}
                # 校验失败也推送数据质量预警（走邮件/Webhook 时能第一时间通知）
                if do_push and any(ch.name != "console" for ch in self.notifier.channels):
                    deliveries = self.notifier.push(
                        [a for a in alerts], metrics,
                        {"output_root": self.output_root, "run_id": run_id,
                         "title": self.config.report.get("title", "食用油中控品控日报"),
                         "fingerprint": "", "audit_path": self.audit.path})

            result.status = "failed" if err_issues else ("partial" if errors else "success")

        except Exception as e:
            errors.append(str(e))
            _STEP("异常", f"⛔ 运行中断: {e}")
            result.status = "failed"

        # ---- 9. 审计日志（无论成败都写） ----
        result.finished_at = iso_now()
        result.metrics = metrics
        result.outputs = outputs
        result.alerts = alerts
        result.deliveries = deliveries
        result.errors = errors
        entry = {
            "run_id": run_id, "status": result.status, "report_date": target.isoformat(),
            "started_at": started, "finished_at": result.finished_at,
            "source_type": self.config.source.get("type", ""),
            "record_counts": data_counts,
            "data_checksum": data_checksum,
            "snapshot_dir": snapshot_info.get("snapshot_dir", ""),
            "report_fingerprint": fingerprint(
                {k: os.path.getmtime(v) for k, v in outputs.items()}) if outputs else "",
            "outputs": {k: os.path.abspath(v) for k, v in outputs.items()},
            "alerts": [a.to_dict() for a in alerts],
            "deliveries": deliveries,
            "errors": errors,
        }
        result.audit_path = self.audit.path
        self.audit.log(entry)
        _STEP("审计", f"已写入 {self.audit.path}")

        _STEP("完成", f"状态 {result.status.upper()}")
        return result
