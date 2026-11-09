# -*- coding: utf-8 -*-
# tasks/runner.py：一次任务 = 一次完整执行（不是「看已有产物」）。
# 输入：TaskRequest；输出：TaskResult + <output_root>/<run_id>/ 下的全部一等产物。
# 状态机 queued→collecting→validating→analyzing→rendering→done|partial|failed|pending_review，
# 每步（含异常）写 trace.jsonl。只编排既有引擎，不复制采集/校验/判定/导出实现。
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from ..agent import QCDailyAgent
from ..config import QCDailyConfig
from ..importer import import_path
from ..models import fingerprint, iso_now
from ..validate import validate_records
from .registry import TASKS, get_spec
from .report import BLOCKS, write_report
from .request import RequestError, TaskRequest


def diagnose_missing_dates(dates: list, no_rows: bool) -> tuple[str, str, str]:
    """「一条检验日期都没读出来」时该怎么说话。

    返回 (给人看的错因, 下一步, trace code)。

    以前这里直接走"日期不匹配"分支，把四种完全不同的输入错误（空表 / 只有表头 /
    日期列是公式没存结果 / 实测值整列读不懂）说成同一句话，还叫人去改根本没坏的
    日期列——照着改会把真实记录改坏（夜测 2026-11-09 S3 / G4）。
    """
    if no_rows:
        return ("这张表一行可判定的记录都没读出来（可能是空表、只有表头，"
                "或者「实测值」整列都读不懂）。没有可判定的记录，本次没跑。",
                "下一步：确认表里有数据行；实测值要写数字——「未检出」「—」「＜0.5」"
                "这类现在会整行跳过，原值会列在下方提醒里。",
                "no_rows")
    return ("表里读到了数据行，但一条检验日期都没读出来"
            "（日期列可能是公式没存计算结果，或格式认不出）。",
            "下一步：在 Excel 里重算并保存后再传，或把日期列改成 yyyy-mm-dd 文本；"
            "不要改动业务日期本身。",
            "no_dates")

ROOT = Path(__file__).resolve().parent.parent.parent
STATES = ("queued", "collecting", "validating", "analyzing", "rendering")
TERMINAL = ("done", "partial", "failed", "pending_review")

#: 四层可复现性分工（别再拿单次溯源字段做跨运行断言，前两次踩的都是这个坑）：
#:   input_digest        数据层 · 跨运行可复现 —— 对 input.db 三表规范化行算 SHA-256
#:   conclusion_digest   结论层 · 跨运行可复现 —— 对 verdicts.json 规范化内容算 SHA-256
#:   report_fingerprint  产物层 · 仅单次 —— __FP__ 占位 + verify_report() 防篡改
#:   data_checksum       采集层 · 仅单次 —— agent 既有审计字段；SQL 链路 QCRecord.to_dict()
#:                       含 extracted_at=iso_now()，两次跑必然不同，只用于单次溯源
DIGEST_NOTES = {
    "input_digest": "数据层：同输入两次应一致（input.db 规范化行，不含运行时字段）",
    "conclusion_digest": "结论层：同输入两次应一致（verdicts.json 规范化内容，不含时间戳/run_id）",
    "report_fingerprint": "产物层：仅单次运行内防篡改（__FP__ + audit.verify_report），不做跨运行断言",
    "data_checksum": "采集层：仅单次溯源（agent 既有审计字段，含 extracted_at 运行时信息），不做跨运行断言",
}

_RUNS: dict[str, "TaskResult"] = {}


@dataclass
class TaskResult:
    run_id: str
    task_id: str
    status: str = "queued"
    report_date: str = ""
    started_at: str = ""
    finished_at: str = ""
    outputs: list[str] = field(default_factory=list)      # 相对 run 目录
    trace: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)   # 含 error 级校验触发的 critical 告警
    run_dir: str = ""
    input_digest: str = ""
    data_checksum: str = ""
    conclusion_digest: str = ""
    report_fingerprint: str = ""
    verify_ok: bool = False
    metrics: dict = field(default_factory=dict)
    sections: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class TaskRunner:
    """任务状态机。三条既有链保持原样，这里只做输入装载 + 调用 + 归集 + 报告装配。"""

    def __init__(self, config: QCDailyConfig | None = None):
        self.config = config or QCDailyConfig.load()

    # ---------------------------------------------------------------- 留痕
    @staticmethod
    def _run_dir(req: TaskRequest, run_id: str) -> Path:
        root = Path(req.output_root)
        if not root.is_absolute():
            root = ROOT / root
        return (root / run_id).resolve()

    def _trace(self, res: TaskResult, state: str, event: str, detail: str = "") -> None:
        rec = {"step": len(res.trace) + 1, "state": state, "event": event,
               "detail": str(detail)[:400], "ts": iso_now()}
        res.trace.append(rec)
        if res.run_dir:
            with open(Path(res.run_dir) / "trace.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _state(self, res: TaskResult, state: str, detail: str = "") -> None:
        res.status = state
        self._trace(res, state, "state_change", detail)

    # ---------------------------------------------------------------- 主入口
    def run(self, req: TaskRequest, run_id: str | None = None) -> TaskResult:
        # 可选 run_id：Web 需要先返回 run_id 再轮询，CLI 不传则由本层生成。
        run_id = run_id or f"T{date.today():%Y%m%d}-{uuid.uuid4().hex[:10]}"
        run_dir = self._run_dir(req, run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        res = TaskResult(run_id=run_id, task_id=req.task_id, report_date=req.report_date,
                         started_at=iso_now(), run_dir=str(run_dir))
        _RUNS[run_id] = res

        try:
            req.save(str(run_dir / "request.json"))
            self._trace(res, "queued", "request_saved", "request.json 已落盘（可复现）")
            req.validate(TASKS.keys())
            spec = get_spec(req.task_id)
            self._trace(res, "queued", "task_selected",
                        f"{req.task_id} → engine={spec.engine} auxiliary={spec.auxiliary}")

            ctx = self._collect_context(req, run_dir, res)
            if res.status in TERMINAL:
                return self._finish(req, res, run_dir, ctx)

            ctx = self._execute(req, run_dir, res, ctx)
            # partial / pending_review 都要继续出 8 块报告：录入自查任务几乎总是
            # pending_review，若在这里提前收尾就不会有 report.html（4.4 缺一块算未完成）。
            if res.status in TERMINAL and res.status not in ("partial", "pending_review"):
                return self._finish(req, res, run_dir, ctx)

            # 渲染阶段的状态机切换会覆盖终态，先记下来
            degraded = res.status if res.status in TERMINAL else ""
            self._render(req, run_dir, res, ctx, degraded)
            return self._finish(req, res, run_dir, ctx)

        except RequestError as e:
            res.status = "failed"
            res.errors.append(f"输入不合法：{e}")
            res.notes.append("下一步：按提示修正输入文件或 task_id 后重跑。")
            self._trace(res, "failed", "request_rejected", str(e))
            return self._finish(req, res, run_dir, {})
        except Exception as e:
            res.status = "failed"
            res.errors.append(f"运行中断（{type(e).__name__}）：{str(e)[:300]}")
            res.notes.append("下一步：查看 trace.jsonl 最后一条定位失败步骤；"
                             "输入表与快照已保留，可修正后重跑。")
            self._trace(res, "failed", "exception", f"{type(e).__name__}: {str(e)[:300]}")
            return self._finish(req, res, run_dir, {})

    # ---------------------------------------------------------------- 输入装载（纠偏 2）
    def _collect_context(self, req: TaskRequest, run_dir: Path, res: TaskResult) -> dict:
        """只把输入表装进本次独立库；不重写 importer，不碰 qc_source.db / samples/。"""
        ctx: dict = {"input_files": [], "skipped": [], "notes": [],
                     "validation_issues": [], "suspects": [], "verdicts": [],
                     "metrics": {}, "checkpoints": [], "outputs": [],
                     "source_type": "", "data_range": req.report_date,
                     "snapshot_dir": "", "trace_path": "trace.jsonl",
                     "standard_warning": self.config.standard_warning}

        spec = get_spec(req.task_id)
        if spec.auxiliary and spec.engine == "B":
            return ctx                      # ask_qa 不读表，只走 B 链

        self._state(res, "collecting", "装载输入表到独立 input.db")
        inputs_dir = run_dir / "inputs"
        inputs_dir.mkdir(exist_ok=True)
        for raw in req.inputs:
            p = Path(raw)
            files = ([x for x in p.iterdir()
                      if x.is_file() and x.suffix.lower() in (".csv", ".xlsx", ".xlsm", ".json")]
                     if p.is_dir() else [p])
            for f in files:
                shutil.copy2(str(f), str(inputs_dir / f.name))   # 只读源文件，新增副本
        self._trace(res, "collecting", "inputs_copied",
                    f"{len(list(inputs_dir.iterdir()))} 个文件 → inputs/")

        db_path = str(run_dir / "input.db")
        # config_copy 只改 source.sql.dsn；products / limits / 版本门禁一律沿用原值。
        summary = import_path(str(inputs_dir), db_path, self.config, dry_run=False)
        ctx["input_files"] = summary["files"]
        ctx["notes"] = list(summary["notes"])
        ctx["import_rows"] = summary["rows"]
        ctx["import_dates"] = summary["dates"]
        ctx["source_type"] = self.config.source.get("type", "sql")
        self._trace(res, "collecting", "import_summary",
                    f"rows={summary['rows']} dates={summary['dates']}")

        # 4.6：表头映射不出来的 sheet 列进 notes 并标 pending_review
        for n in summary["notes"]:
            if "无法识别表类型" in n or "跳过" in n:
                ctx["skipped"].append(n)
                res.notes.append(f"[pending_review] {n}")
        unrecognized = any("无法识别表类型" in n for n in summary["notes"])
        no_rows = sum(summary["rows"].values()) == 0
        if unrecognized and no_rows:
            res.status = "failed"
            res.errors.append("所有表头都无法映射到 production / quality / warehouse，拒绝执行")
            res.notes.append("未映射表头见 notes（importer 已列出前 8 个）；请改表头后重跑，不猜不补。")
            self._trace(res, "failed", "unmapped_headers", str(ctx["skipped"])[:400])
            return ctx
        if summary["errors"]:
            res.status = "failed"
            res.errors.extend(summary["errors"])
            res.notes.append("下一步：按错误修正对应文件后重跑；其余可读文件已保留。")
            self._trace(res, "failed", "import_errors", str(summary["errors"])[:400])
            return ctx
        # 导入层已经判死（如"宽表缺少批次列"）时，先报这个。
        # 不然会掉进下面的日期检查，报一句"日期不匹配"把人指去改日期——
        # 真实原因是一列都没读进来（2026-11-08 拿真表实测踩到）
        blocked = [n for n in summary["notes"] if "⛔" in n]
        if no_rows and blocked:
            res.status = "failed"
            res.errors.append(blocked[0].split("⛔", 1)[-1].strip(" ：:"))
            res.notes.append("下一步：按上一条补齐后重跑；没有批次号列的表，改成一"
                            "行一项的长表（批次号 / 检测项目 / 实测值 / 限值）也能跑。")
            self._trace(res, "failed", "import_blocked", str(blocked)[:300])
            return ctx
        # 日期列表为空 ≠ 日期不匹配。空表、只有表头、日期列是公式没存结果、整表实测值
        # 都读不懂——这四种都报成"日期不匹配"，会把人指去改根本没坏的日期列
        # （夜测 2026-11-09 S3 / G4：改日期照提示做，反而把真实记录改坏）。
        if not summary["dates"]:
            res.status = "failed"
            msg, nxt, code = diagnose_missing_dates(summary["dates"], no_rows)
            res.errors.append(msg)
            res.notes.append(nxt)
            self._trace(res, "failed", code,
                        str(ctx["notes"] if code == "no_rows" else ctx["import_dates"])[:400])
            return ctx
        if req.report_date not in summary["dates"]:
            res.status = "failed"
            res.errors.append(
                f"输入表日期 {summary['dates']} 与 report_date={req.report_date} 不匹配；"
                "source.sql.queries 用 date(tested_at)=:report_date，静默跑会出空报告")
            res.notes.append("下一步：把 --date 改成表里的检验日期，或修正表内日期列。")
            self._trace(res, "failed", "date_mismatch", str(summary["dates"]))
            return ctx
        if not self._demo_only(db_path, res):
            return ctx
        missing = self._missing_limits(db_path)
        if missing:
            names = "、".join(f"{k}（{v} 条）" for k, v in list(missing.items())[:6])
            res.status = "failed"
            res.errors.append(f"这些记录没取到限值，系统不猜限值、不判：{names}")
            res.notes.append(
                "原因：当前执行版（GB 1350—2009）限值还没人工核对，宽表模式不会拿 2025 版的数补齐；"
                "补了就成「按新版判旧货」。下一步：改用带「实测值 / 指标方向 / 限值 / 标准号 / "
                "限值出处」列的明细表，或先核对限值后回填。")
            self._trace(res, "failed", "missing_limits", str(missing)[:300])
            return ctx
        ctx["input_db"] = db_path
        ctx["input_digest"] = self._input_digest(db_path)
        self._trace(res, "collecting", "input_digest", ctx["input_digest"])
        return ctx

    @staticmethod
    def _input_digest(db_path: str) -> str:
        """数据层可复现凭证：直接对 input.db 三表规范化行算 SHA-256。

        importer.write_to_db() INSERT 的列名取自 TABLE_COLUMNS[ttype]，
        quality 列集合本来就不含 extracted_at —— input.db 天然干净，
        不需要写任何「剔除时间戳 / 运行时字段」的清洗逻辑。
        write_to_db 同日期先删后插，重复导入同一文件后表内容逐行相同，指纹自动稳定。
        """
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        payload: dict = {}
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("production", "quality", "warehouse"):
                if table not in names:
                    payload[table] = []
                    continue
                rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
                rows.sort(key=lambda r: json.dumps(r, ensure_ascii=False,
                                                   sort_keys=True, default=str))
                payload[table] = rows
        finally:
            conn.close()
        return fingerprint(payload)

    @staticmethod
    def _missing_limits(db_path: str) -> dict[str, int]:
        """宽表常见情形：项目名能认，但限值取不到。这种必须拦在判定之前。

        不拦的后果不是「少判一条」，是 compare(None) 直接抛 TypeError，
        整次任务变成一句英文报错——使用人只会以为系统坏了。
        """
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT item, COUNT(*) FROM quality WHERE limit_value IS NULL GROUP BY item"
            ).fetchall()
            conn.close()
            return {str(r[0]): int(r[1]) for r in rows if r[0]}
        except sqlite3.Error:
            return {}

    def _demo_only(self, db_path: str, res: TaskResult) -> bool:
        """DEMO-only 红线：批次号必须 DEMO- 前缀（实测值无法判真假，只拦批次号）。"""
        try:
            conn = sqlite3.connect(db_path)
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            bad = []
            for table in ("production", "quality", "warehouse"):
                if table not in names:
                    continue
                for (bid,) in conn.execute(f"SELECT DISTINCT batch_id FROM {table}"):
                    if bid and not str(bid).upper().startswith("DEMO-"):
                        bad.append(str(bid))
            conn.close()
        except sqlite3.Error as e:
            res.status = "failed"
            res.errors.append(f"独立库读取失败（{type(e).__name__}）")
            self._trace(res, "failed", "db_error", str(e)[:200])
            return False
        if bad:
            res.status = "failed"
            res.errors.append("只接 DEMO 假数据：发现非 DEMO- 前缀的批次号，拒绝执行")
            res.notes.append("下一步：把真实批次号换成 DEMO- 假批次后重跑，真实批次与实测值不进本系统。")
            self._trace(res, "failed", "real_batch_rejected", f"{len(bad)} 个非 DEMO 批次")
        return not bad

    # ---------------------------------------------------------------- 执行（复用 A/B/C/D）
    def _execute(self, req: TaskRequest, run_dir: Path, res: TaskResult, ctx: dict) -> dict:
        spec = get_spec(req.task_id)
        self._state(res, "validating", "执行数据层与录入层校验")
        if spec.engine == "A":
            return self._engine_a(req, run_dir, res, ctx)
        if spec.engine == "C":
            return self._engine_c(req, run_dir, res, ctx)
        if spec.engine == "D":
            return self._engine_d(req, run_dir, res, ctx)
        return self._engine_b(req, run_dir, res, ctx)

    def _config_copy(self, db_path: str) -> QCDailyConfig:
        cfg = QCDailyConfig(raw=copy.deepcopy(self.config.raw),
                            config_path=self.config.config_path)
        cfg.raw["source"]["sql"]["dsn"] = os.path.abspath(db_path)
        return cfg

    def _engine_a(self, req, run_dir, res, ctx) -> dict:
        cfg = self._config_copy(ctx["input_db"])
        self._trace(res, "validating", "config_copy",
                    f"只改 dsn → {cfg.raw['source']['sql']['dsn']}")
        agent = QCDailyAgent(cfg)
        # 把导入层的「哪几行没进判定」带进日报正文，让看报告的人也知道少了什么
        agent.import_notes = list(ctx.get("notes") or [])
        self._state(res, "analyzing", "调 QCDailyAgent.run（八步流水线原样）")
        result = agent.run(report_date=date.fromisoformat(req.report_date),
                           push=False, formats=req.formats)

        # 归集 agent 原生产物到 run 目录（收敛点 2：一切落在同一 run 根下）
        outdir = run_dir / "outputs"
        outdir.mkdir(exist_ok=True)
        for fmt, path in (result.outputs or {}).items():
            if path and os.path.isfile(path):
                dst = outdir / Path(path).name
                shutil.copy2(path, str(dst))
                ctx["outputs"].append(f"outputs/{dst.name}")
        snap_src = Path(cfg.output_root) / "raw" / req.report_date
        if snap_src.is_dir():
            snap_dst = run_dir / "snapshots"
            shutil.copytree(str(snap_src), str(snap_dst), dirs_exist_ok=True)
            ctx["snapshot_dir"] = "snapshots/"
            q = snap_dst / "quality.json"
            if q.is_file():
                ctx["verdicts"] = json.loads(q.read_text(encoding="utf-8"))

        # data_checksum 来自 audit.jsonl 本次 run_id；不做跨运行指纹断言
        audit_file = Path(cfg.output_root) / "audit" / "audit.jsonl"
        if audit_file.is_file():
            for line in audit_file.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("run_id") == result.run_id:
                    ctx["data_checksum"] = e.get("data_checksum", "")
                    (run_dir / "audit_entry.json").write_text(
                        json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")
                    break

        m = result.metrics
        if m is not None:
            # 没采集到生产记录时，一次交验合格率 / 异常批次都是算不出来的。
            # 旧版照印 0.00% 与 0，和同页「不合格项次 2」自相矛盾（夜测 S2/S3）。
            has_prod = bool(getattr(m, "batch_count", 0))
            ctx["metrics"] = {
                "pass_rate": m.pass_rate,
                "batch_pass_rate": m.batch_pass_rate if has_prod else "—（无生产记录）",
                "total_tests": m.total_tests, "fail_tests": m.fail_tests,
                "abnormal_batches": (len(m.abnormal_batches) if has_prod
                                     else "—（无生产记录）"),
                "has_production": has_prod,
                "defect_distribution": {d["item"]: d["fail"]
                                        for d in (m.defect_distribution or [])},
                "trend": [f"{t.date}:{t.pass_rate:.2%}" for t in (m.trend_points or [])],
                "alerts": [a.title for a in (m.alerts or [])],
            }
            ctx["validation_issues"] = [
                {"level": i.level, "code": i.code, "message": i.message, "ref": i.ref}
                for i in (getattr(m, "validation_issues", None) or [])]
        errors = [str(x) for x in (result.errors or [])]
        ctx["agent_status"] = result.status
        ctx["agent_errors"] = errors
        res.alerts = [a.to_dict() for a in (result.alerts or [])]

        err_level = [i for i in ctx["validation_issues"] if i["level"] == "error"]
        if err_level or result.status == "failed":
            res.status = "failed"
            res.errors.append("error 级校验失败：按「宁可不发，不可错发」不发布报告")
            res.errors.extend(errors)
            res.notes.append("critical 告警已记录；下一步：指路到源系统核对后重跑，"
                             "快照与 trace 照常保留。")
            self._trace(res, "failed", "validation_error", str(err_level)[:400])
            return ctx
        missing = [f for f in req.formats if f not in (result.outputs or {})]
        if missing:
            res.status = "partial"
            res.errors.append(f"导出降级：{missing} 未产出，HTML 仍交付（4.6）")
            self._trace(res, "partial", "export_degraded", str(missing))
        if errors:
            res.errors.extend(errors)
            if res.status not in TERMINAL:
                res.status = "partial"
        self._trace(res, "analyzing", "agent_done",
                    f"status={result.status} outputs={list(result.outputs)}")
        return ctx

    def _engine_d(self, req, run_dir, res, ctx) -> dict:
        """录入自查：只读 input.db，绝不调 compare()、不产合格/不合格结论。"""
        conn = sqlite3.connect(ctx["input_db"])
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM quality")]
        conn.close()
        suspects = [s.to_dict() for s in validate_records(rows)]
        ctx["suspects"] = suspects
        ctx["source_type"] = "file(sqlite:input.db)"
        ctx["metrics"] = {"pass_rate": "-", "batch_pass_rate": "-",
                          "abnormal_batches": "本任务不涉及",
                          "total_tests": len(rows), "fail_tests": "本任务不涉及",
                          "defect_distribution": {}, "trend": [], "alerts": []}
        ctx["agent_status"] = "done"
        ctx["agent_errors"] = []
        res.notes.append(f"录入自查命中 {len(suspects)} 条疑点，全部标「待人工确认」。")
        if suspects:
            res.status = "pending_review"
            self._trace(res, "pending_review", "suspects_found", str(len(suspects)))
        else:
            self._state(res, "validating", "无疑点")
        return ctx

    def _engine_c(self, req, run_dir, res, ctx) -> dict:
        """复用 C：harness 的 DEMO 假批次数据，输入表只作附件，两份数据不混用。"""
        from ..harness import run_harness

        question = str((req.scope or {}).get("question")
                       or "DEMO-001 这批稻谷出糙率 76.5 能收吗")
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            result = run_harness(question)
        (run_dir / "harness_stdout.txt").write_text(sink.getvalue(), encoding="utf-8")
        ctx["sections"] = result.get("sections") or {}
        ctx["source_type"] = "harness._DEMO_PLAN（内置 DEMO 假数据）"
        ctx["metrics"] = {"pass_rate": "-", "batch_pass_rate": "-",
                          "abnormal_batches": "本任务不涉及", "total_tests": "-",
                          "fail_tests": "本任务不涉及", "defect_distribution": {},
                          "trend": [], "alerts": []}
        ctx["agent_status"] = result.get("stop_reason", "")
        ctx["agent_errors"] = []
        # 把 C 链产物复制进本次 run 目录，保证一个 run 不分散在两处
        for key in ("trace_path", "summary_path", "report_path"):
            p = result.get(key)
            if p and os.path.isfile(p):
                dst = run_dir / f"harness_{Path(p).name}"
                shutil.copy2(p, str(dst))
                ctx["outputs"].append(dst.name)
        res.notes.append(
            "判定链读取 qc_daily/harness.py 的内置 DEMO 假数据（水分 145.0）；"
            "输入表与 samples/ 里的同名批次是另一份演练数据，不要混用。")
        self._trace(res, "analyzing", "harness_done", str(result.get("stop_reason")))
        return ctx

    def _engine_b(self, req, run_dir, res, ctx) -> dict:
        """辅助只读：捕获 B 链 stdout 落 answer.txt，不出 8 块报告。"""
        from ..qa import run_qa

        question = str((req.scope or {}).get("question") or "出糙率 3 等限值是多少")
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            run_qa(question)
        (run_dir / "answer.txt").write_text(sink.getvalue(), encoding="utf-8")
        ctx["outputs"].append("answer.txt")
        ctx["source_type"] = "qa.run_qa（stdout 步骤流）"
        self._trace(res, "analyzing", "ask_qa_done", question)
        return ctx

    # ---------------------------------------------------------------- 报告装配
    def _render(self, req, run_dir, res, ctx, degraded: str = "") -> None:
        spec = get_spec(req.task_id)
        self._state(res, "rendering", "装配 8 块任务报告")
        rows = ctx.get("verdicts") or []
        normalized = [
            {k: r.get(k) for k in ("batch_id", "item", "value", "direction",
                                   "limit_value", "verdict", "standard", "limit_source")}
            for r in sorted(rows, key=lambda x: (str(x.get("batch_id", "")), str(x.get("item", ""))))
        ]
        (run_dir / "verdicts.json").write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        # 4a：结论可复现 —— 不含时间戳 / run_id，同输入两次一致
        ctx["conclusion_digest"] = fingerprint(normalized)

        if not ctx.get("suspects") and rows:
            ctx["suspects"] = [s.to_dict() for s in validate_records(rows)]
        if not ctx.get("sections"):
            ctx["sections"] = self._sections(ctx, res)
        # ctx 初始化时已经有 "checkpoints": []，setdefault 永远不会生效 ——
        # 第 7 节「人工核对指路」成了只有标题的空壳，却被算作「8 块齐全」（夜测 M7）
        if not ctx.get("checkpoints"):
            ctx["checkpoints"] = self._checkpoints(ctx, res)

        if spec.auxiliary and spec.engine == "B":
            ctx["outputs"] = ctx.get("outputs") or ["answer.txt"]
            return                          # ask_qa 明确不出 8 块报告

        ctx.update({
            "task_id": req.task_id, "run_id": res.run_id,
            "title": spec.name, "started_at": res.started_at,
            "finished_at": iso_now(), "status": degraded or res.status,
            "data_range": req.report_date,
            "input_digest": ctx.get("input_digest", ""),
            "data_checksum": ctx.get("data_checksum", ""),
        })
        # 先写一次拿校验结果，再带真实值重写，保证 HTML 显示与实际一致
        path = run_dir / "report.html"
        _, ok = write_report({**ctx, "verify_ok": False}, str(path))
        ctx["verify_ok"] = bool(ok)
        fp, ok = write_report(ctx, str(path))
        res.report_fingerprint = fp
        res.verify_ok = bool(ok)
        if not ok:
            res.status = "failed"
            res.errors.append("report.html 指纹校验未通过，status=failed 不交付（4.5）")
            self._trace(res, "failed", "fingerprint_mismatch", fp)
        if "report.html" not in ctx["outputs"]:
            ctx["outputs"].append("report.html")
        res.data_checksum = ctx.get("data_checksum", "")
        res.conclusion_digest = ctx["conclusion_digest"]
        res.metrics = ctx.get("metrics") or {}
        res.sections = ctx.get("sections") or {}
        if degraded and res.status != "failed":
            res.status = degraded
        self._trace(res, "rendering", "report_written",
                    f"{BLOCKS} 8 块齐全 / verify={ok} / degraded={degraded or '-'}")

    def _sections(self, ctx: dict, res: TaskResult) -> dict:
        confirmed, review, uncovered = [], [], []
        # 疑点必须按「批次 × 项目」对，不能只按项目：DEMO-003 的整精米率可疑，
        # 不能顺带把 DEMO-001 正常的整精米率也标成疑点——假警报会把真警报淹掉
        sus_list = list(ctx.get("suspects") or [])
        sus_map = {(str(s.get("batch_id") or ""), str(s.get("item") or "")): s
                   for s in sus_list}
        flagged: set[tuple[str, str]] = set()
        for v in (ctx.get("verdicts") or []):
            key = (str(v.get("batch_id", "")), str(v.get("item", "")))
            src = f"{v.get('standard','')} {v.get('limit_source','')}".strip()
            line = (f"{v.get('batch_id','')} · {v.get('item','')} "
                    f"{v.get('direction','')}{v.get('limit_value','')} → "
                    f"实测 {v.get('value','')} → 代码判定「{v.get('verdict','')}」"
                    f"（{src}）")
            if key in sus_map:
                flagged.add(key)
                # 疑点原因并进同一行，不再单独再列一条同样的话
                why = str(sus_map[key].get("reason") or "").strip()
                review.append(f"{v.get('item','')}：该值有录入疑点，结论不可用于放行 → "
                              + (line[:-1] + f"；疑点：{why}）" if why else line))
            elif v.get("verdict") == "合格":
                confirmed.append(line)
            else:
                review.append(line)
            if not v.get("limit_source"):
                uncovered.append(f"{v.get('item','')}：检索不到条款出处，未覆盖，不推算")
        # 没对上任何逐项结论的疑点仍然要报（比如整行缺失、字段为空）
        for s in sus_list:
            key = (str(s.get("batch_id") or ""), str(s.get("item") or ""))
            if key in flagged:
                continue
            who = str(s.get("batch_id") or "").strip()
            item = str(s.get("item") or "")
            why = str(s.get("reason") or "").strip()
            if item and why.startswith(f"「{item}」"):
                why = "这格数据" + why[len(f"「{item}」"):]   # 前面已经写了项目名，别重复
            review.append(f"{who + ' · ' if who and who not in why else ''}{item}：{why}；"
                          f"建议 {s.get('建议动作','')}")
        review.append("标准版本：当前执行 GB 1350—2009，限值尚未人工核对；"
                      "GB 1350—2025 为 2027-01-01 预置，不得用于判旧货")
        review.append("放行决策（能否收购/接收）：系统不给「能收/不能收」结论，"
                      "只摆事实与待复核原因，需人工复核签字")
        uncovered.append("感官项「色泽、气味」：人工确认项，不参与数值判定")
        uncovered.append("其它等级（1/2/4/5 等）、粳稻谷表2：本任务未覆盖")
        uncovered.append("数据范围：DEMO-only 假批次，真实批次与实测值不进本系统")
        return {"已确认": confirmed, "待人工复核": review, "未覆盖": uncovered}

    def _checkpoints(self, ctx: dict, res: TaskResult) -> list[dict]:
        """沿用 client_ui.py::_checkpoints 口径：去哪 / 看什么 / 入口；系统外明说系统外。"""
        out = [{
            "kind": "record", "title": "先看这批的原始记录",
            "desc": "判定链实际读的原始值在本次 run 的 snapshots/quality.json，只读，不会改动任何数据。",
            "action": "snapshots/quality.json", "label": "打开原始记录（只读）",
        }, {
            "kind": "knowledge", "title": "去知识库核对限值",
            "desc": "当前执行 GB 1350—2009，限值还没人工核对。"
                    "在 /knowledge 页下半部分的「待核对区」上传或确认资料；"
                    "确认纳入 ≠ 可用新限值判定。",
            "action": "/knowledge#kb-inbox-section", "label": "打开知识库 · 待核对区",
        }, {
            "kind": "human", "title": "放行签字这一步在系统外",
            "desc": "本系统不放行、不签字。按汇报线找班长或主任，由人复核后签字。",
            "action": "", "label": "",
        }]
        if res.status == "failed":
            out.insert(0, {
                "kind": "source", "title": "回到源系统核对输入",
                "desc": "本次未发布报告。快照与 trace 已保留，请按 errors[] 指出的文件与字段核对后重跑。",
                "action": "trace.jsonl", "label": "打开执行轨迹",
            })
        return out

    # ---------------------------------------------------------------- 收尾
    def _finish(self, req, res, run_dir, ctx) -> TaskResult:
        if res.status not in TERMINAL:
            res.status = "done"
        res.finished_at = iso_now()
        if ctx:
            res.notes = list(dict.fromkeys(res.notes + list(ctx.get("notes") or [])))
            res.outputs = list(dict.fromkeys(ctx.get("outputs") or []))
            res.input_digest = ctx.get("input_digest", "")
        payload = res.to_dict()
        payload["digest_notes"] = DIGEST_NOTES
        (run_dir / "summary.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        self._trace(res, res.status, "finished",
                    f"status={res.status} outputs={len(res.outputs)}")
        _RUNS[res.run_id] = res
        return res


def get_run(run_id: str, output_root: str = "runs") -> TaskResult | None:
    """先查内存，再从磁盘 summary.json 回读（服务重启后仍可查历史 run）。"""
    if run_id in _RUNS:
        return _RUNS[run_id]
    root = Path(output_root)
    if not root.is_absolute():
        root = ROOT / root
    f = root / run_id / "summary.json"
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return TaskResult(**{k: v for k, v in data.items() if k in TaskResult.__dataclass_fields__})
    except (OSError, ValueError, TypeError):
        return None
