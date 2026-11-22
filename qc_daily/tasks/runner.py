# -*- coding: utf-8 -*-
# tasks/runner.py：一次任务 = 一次完整执行（不是「看已有产物」）。
# 输入：TaskRequest；输出：TaskResult + <output_root>/<run_id>/ 下的全部一等产物。
# 状态机 queued→collecting→validating→analyzing→rendering→done|partial|failed|pending_review，
# 每步（含异常）写 trace.jsonl。只编排既有引擎，不复制采集/校验/判定/导出实现。
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .. import readback as rb
from ..agent import QCDailyAgent
from ..config import QCDailyConfig
from ..importer import import_path
from ..models import fingerprint, iso_now
from ..validate import validate_records
from .registry import TASKS, get_spec
from .report import BLOCKS, write_report
from .request import RequestError, TaskRequest


def diagnose_missing_dates(dates: list, no_rows: bool, skipped: int = 0,
                           total: int = 0) -> tuple[str, str, str]:
    """「一条检验日期都没读出来」时该怎么说话。

    返回 (给人看的错因, 下一步, trace code)。

    以前这里直接走"日期不匹配"分支，把四种完全不同的输入错误（空表 / 只有表头 /
    日期列是公式没存结果 / 实测值整列读不懂）说成同一句话，还叫人去改根本没坏的
    日期列——照着改会把真实记录改坏（夜测 2026-11-09 S3 / G4）。

    更晚一层：导入层已经数清「共几行、跳过几行」，手里有确定数字就不该再给
    「可能是空表」这种三选一猜测（夜测 11-10 r05：单行表上卡说没读出来、
    下卡说其余行照跑）。skipped/total 由 importer 的结构化 stats 传入。
    """
    # 2026-11-15 白天修复：「改成数字后重传」把两种完全不同的写法说成一种。
    # 「＜0.5」「未检出」「ND」「—」是合法写法（低于检出限），照字面改成 0.5 会把
    # 结论从「不判」翻转成「合格」，改的正是原始记录，与「只读不写源数据」冲突
    # （夜测 11-13/11-14/11-15 连续四晚）。下面两支分开说，且不再硬举四种写法。
    _two_cases = ("分两种：① 写成「＜0.5」「未检出」「ND」「—」的是合法写法，"
                  "系统现在不替你折算，请人工判这一项，不要为了上传改原始记录；"
                  "② 真写错的（如「十三点二」这类中文数字、多打了空格）才改成 13.2 "
                  "这样的阿拉伯数字后重传。日期列本身没问题，不要改日期。")
    if no_rows and skipped and total and skipped >= total:
        return (f"表里读到 {total} 行数据，但 {skipped} 行的实测值读不懂，"
                "一行都没进判定，本次没跑。下方提醒里点名了具体哪几行、原值是什么。",
                "下一步：" + _two_cases,
                "values_unreadable")
    if no_rows and skipped:
        return (f"表里读到 {total} 行数据，其中 {skipped} 行的实测值读不懂；"
                "其余行也没能形成可判定记录，本次没跑。",
                "下一步：按下方提醒逐行核对实测值写法；" + _two_cases,
                "values_unreadable")
    if no_rows:
        return ("这张表一行可判定的记录都没读出来（可能是空表、只有表头，"
                "或者「实测值」整列都读不懂）。没有可判定的记录，本次没跑。",
                "下一步：确认表里有数据行；实测值要能读成数字，"
                "原值会列在下方提醒里。" + _two_cases,
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
        # 收尾做副作用断言用：run_id → 运行前的只读源哈希快照。
        # 放在实例上而不塞进 TaskResult，是为了不动 summary.json 的既有字段结构。
        self._source_snapshots: dict[str, dict] = {}

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
        # 运行前先拍只读源快照；收尾比对「不该变的是否变了」。
        self._source_snapshots[run_id] = rb.snapshot_sources(ROOT)

        try:
            req.save(str(run_dir / "request.json"))
            self._trace(res, "queued", "request_saved", "request.json 已落盘（可复现）")
            req.validate(TASKS.keys())
            spec = get_spec(req.task_id)
            self._trace(res, "queued", "task_selected",
                        f"{req.task_id} → engine={spec.engine} auxiliary={spec.auxiliary}")

            ctx = self._collect_context(req, run_dir, res)
            if res.status in TERMINAL:
                if res.status == "failed":
                    self._leave_failure_evidence(run_dir, res, ctx)
                return self._finish(req, res, run_dir, ctx)

            # 判读前先落应产声明（心跳 11/17 第 2 条）：没有它，「没适用工作」
            # 和「该判的没拿到」在报表上长得一模一样。只写声明，不参与判定。
            self._declare_expected(run_dir, res, ctx, req)
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

    def _declare_expected(self, run_dir: Path, res: TaskResult, ctx: dict,
                          req=None) -> dict:
        """判读前落「应产声明」：本轮应检查多少项、输入指纹、应出哪些产物。

        11/17 心跳汇总第 2 条。为什么必须有它：没有声明时，「本来就没东西可判」
        和「有东西该判但没拿到」在报表上长得一模一样，都会被记成「没检出异常」。
        声明只在判读前写一次，收尾时核对，不参与判定、不改 status。
        """
        inputs_dir = run_dir / "inputs"
        h = hashlib.sha256()
        files = sorted(p for p in inputs_dir.iterdir() if p.is_file()) if inputs_dir.is_dir() else []
        for f in files:
            h.update(f.name.encode("utf-8"))
            h.update(f.read_bytes())

        rows = ctx.get("import_rows") or {}
        checks = 0
        if isinstance(rows, dict):
            checks = sum(int(v) for v in rows.values()
                         if isinstance(v, (int, float)) and not isinstance(v, bool))
        elif isinstance(rows, (int, float)):
            checks = int(rows)

        # 声明「应产格式」而非硬编码文件名：将来产物改名也不会误报缺失。
        # report.html 是最小硬约束（4.4），其余按后缀核对。
        formats = list(getattr(req, "formats", []) or []) if req is not None else []

        decl = {
            "report_date": res.report_date,
            "run_id": res.run_id,
            "task_id": res.task_id,
            "input_fingerprint": "sha256:" + h.hexdigest()[:16],
            "input_files": [f.name for f in files],
            "expected_checks": checks,
            "expected_formats": formats,
            "expected_outputs": ["report.html"],
            "declared_at": iso_now(),
            "_rule": "应产为空→无适用工作；应产非空而回执空→失败；声明缺失→判不了",
        }
        (run_dir / "expected_manifest.json").write_text(
            json.dumps(decl, ensure_ascii=False, indent=2), encoding="utf-8")
        ctx["expected_manifest"] = decl
        return decl

    def _check_declaration(self, run_dir: Path, res: TaskResult, ctx: dict) -> dict:
        """收尾核对：声明缺失 / 应产落空都如实记 UNKNOWN，不降级成 PASS。"""
        f = run_dir / "expected_manifest.json"
        if not f.is_file():
            return {"state": "UNKNOWN", "reason": "没有判读前的应产声明，不能判断是否跑全"}
        try:
            decl = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return {"state": "UNKNOWN", "reason": f"应产声明读不出来：{type(e).__name__}"}
        declared = list(decl.get("expected_outputs") or [])
        actual = set(res.outputs or [])
        missing = [x for x in declared if x not in actual]
        suffix_map = {"html": ".html", "pdf": ".pdf", "excel": ".xlsx",
                      "xls": ".xls", "csv": ".csv", "txt": ".txt", "eml": ".eml"}
        missing_formats = [f for f in (decl.get("expected_formats") or [])
                           if not any(x.lower().endswith(suffix_map.get(f, "." + f))
                                      for x in actual)]
        if not declared and not ctx.get("import_rows"):
            state, reason = "NO_APPLICABLE_WORK", "本轮无应产项，不是「检过了没事」"
        elif missing or missing_formats:
            state, reason = "UNKNOWN", "应产未落全：" + "/".join(missing + missing_formats)
        else:
            state, reason = "PASS", "应产产物全部落盘"
        return {"state": state, "reason": reason,
                "declared": declared, "declared_formats": decl.get("expected_formats") or [],
                "actual": sorted(actual),
                "expected_checks": decl.get("expected_checks"),
                "input_fingerprint": decl.get("input_fingerprint")}

    def _leave_failure_evidence(self, run_dir: Path, res: TaskResult, ctx: dict) -> None:
        """导入层判死也要留下可查的现场（夜测 11-17 G-2，复发第 4 晚，6/6）。

        失败 run 在 _collect_context 里提前 return，跳过了成功路径的 snapshots/
        复制和 audit_entry.json 写入，事后只能翻 trace 才知道今天拒了几张表。
        这里只写**本次 run 目录内**的留痕，不往全局 audit.jsonl 塞条目
        （那是引擎审计流，失败任务引擎根本没启动，塞进去会混淆口径）。
        """
        try:
            src = run_dir / "inputs"
            if src.is_dir():
                shutil.copytree(str(src), str(run_dir / "snapshots"), dirs_exist_ok=True)
                ctx["snapshot_dir"] = "snapshots/"
            f = run_dir / "audit_entry.json"
            if not f.exists():
                f.write_text(json.dumps({
                    "run_id": res.run_id,
                    "report_date": res.report_date,
                    "status": "failed",
                    "scope": "import_layer",
                    "errors": [str(e) for e in res.errors],
                    "notes": [str(n) for n in res.notes[:5]],
                    "at": iso_now(),
                    "note": "导入层判死，引擎未启动；此条由 runner 失败收尾时写入，非引擎审计",
                }, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            # 留痕写不进去不能盖掉原本的导入错误；trace 里已有失败事件可追。
            pass

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
            # 只有 1 个文件、而且它就坏了时，别提「其余」——根本没有其余
            # （夜测 2026-11-11 r05 轻微①）。用导入汇总判断，不依赖请求字段名。
            if len(summary["errors"]) == 1 and not summary["files"]:
                res.notes.append("下一步：这个文件没能读入，按上面的错误修好后重新上传。")
            else:
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
            msg, nxt, code = diagnose_missing_dates(
                summary["dates"], no_rows,
                skipped=int(summary.get("skipped_rows") or 0),
                total=int(summary.get("data_rows") or 0))
            # 导入层现在会写明「0 字节」还是「只有标题行」，别再拿三选一糊过去
            # （夜测 2026-11-11 r05 一般②）：两种 run 的 headline 不该逐字相同。
            empty_notes = [n for n in summary["notes"] if "没有数据行" in n]
            if code == "no_rows" and empty_notes:
                detail = empty_notes[0].split("：", 1)[-1].strip()
                msg = f"表里没读到数据行：{detail}"
                nxt = "下一步：确认文件传完整、且除标题行外有数据行，然后重传；不要改检验日期。"
                res.notes.extend(empty_notes[:2])
            # 把本次真正跳过的原值带进 headline。BD-09（＜0.5）与 BD-10（十三点二）
            # 的 errors[0] 原本逐字相同，/tasks 右栏按未截断原文折叠时会被合成「×2」，
            # 两种病看不出来（夜测 11-15 r05 一般④）。折叠键用的是 errors[0]，
            # 所以在源头区分，不放宽折叠口径。
            raw_samples = [str(n).split("实测值「", 1)[1].split("」", 1)[0]
                           for n in summary["notes"]
                           if "实测值「" in str(n) and "」" in str(n)]
            if code == "values_unreadable" and raw_samples:
                # 旧版只点前 2 个原值还带「字节数」噪音，第 3 个起悄悄消失，
                # 签字人以为只有 2 行有问题（夜测 11-21 r03 M7）。全列 + 报总数。
                _sample_text = "、".join(raw_samples)
                msg = (f"{msg}（本次读不懂的原值共 {len(raw_samples)} 处："
                       f"{_sample_text}）")
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
                "原因：这些项目在表里没有限值列，结构化限值库里也没有逐格核对过的对应条目，"
                "系统不猜限值、不判。下一步：改用带「实测值 / 指标方向 / 限值 / 标准号 / "
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
            uniq = list(dict.fromkeys(bad))
            shown = "、".join(uniq[:3]) + ("…" if len(uniq) > 3 else "")
            res.status = "failed"
            # 只报个数不说哪几行，混合表里人不知道该改哪儿
            # （夜测 2026-11-11 r05 轻微④）。
            res.errors.append(f"只接 DEMO 假数据：发现非 DEMO- 前缀的批次号（{shown}），拒绝执行")
            # 别再说「不进本系统」：trace 的顺序是写库→拷原文件→导入→才拒绝，
            # 真实批号、检验员、实测值此刻已经躺在 runs/<run_id>/ 里了
            # （夜测 2026-11-12 r05 一般②）。改成不撒谎、且给出下一步的说法。
            res.notes.append("下一步：请不要只把批号改成 DEMO- 前缀就重传——"
                             "值一个没动，等于把真实实测值当演示数据跑，系统分辨不出来。"
                             "要试流程请用系统内置的 DEMO-001 假数据。上传的原文件与解析结果"
                             "已留在本机 runs/<run_id>/ 供出错复盘，不需要时可删除。")
            self._trace(res, "failed", "real_batch_rejected",
                        f"{len(uniq)} 个非 DEMO 批次：{shown}")
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
        # 【版本】是声明，不是「哪一行没进判定」，别混进日报那一节。
        agent.import_notes = [n for n in (ctx.get("notes") or [])
                              if "【版本】" not in str(n)]
        self._state(res, "analyzing", "调 QCDailyAgent.run（八步流水线原样）")
        result = agent.run(report_date=date.fromisoformat(req.report_date),
                           push=False, formats=req.formats,
                           reviewed_by=getattr(req, "reviewed_by", ""))

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
        # 0 条时「全部标待人工确认」没有「全部」可言，读数的人会去找不存在的待办
        # （夜测 2026-11-11 r03 轻微④）。顺手把「读了 N 行」带上做数据对账。
        if suspects:
            res.notes.append(f"录入自查命中 {len(suspects)} 条疑点，已标「待人工确认」。")
            res.status = "pending_review"
            self._trace(res, "pending_review", "suspects_found", str(len(suspects)))
        else:
            res.notes.append(
                f"读了 {len(rows)} 行，未命中疑点（值域 / 相邻批次量级跳变 / 小数位 / "
                "跨列逻辑 / 重复漏填五项都查过）；这不等于数据都对。")
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

        # 报告是终态产物，第 1 节不能写「正在生成报告」。渲染时 res.status 已被
        # _state(...,"rendering",...) 改成中间态，真正的 done 要到 _finish 才置——
        # 于是终态文件永久印着"正在生成报告"（夜测 11-20 r03 / 11-21 r04 轻微，第 3 晚）。
        # 这里用「已定的终态 + 缺省 done」预估，与 _finish 的判定一致：
        # degraded（partial/pending_review）优先，否则本次跑完即 done。
        _display_status = degraded or ("done" if res.status not in TERMINAL else res.status)
        ctx.update({
            "task_id": req.task_id, "run_id": res.run_id,
            "title": spec.name, "started_at": res.started_at,
            "finished_at": iso_now(), "status": _display_status,
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
            # 输入表「限值出处」列常以标准号开头，直接拼就成了
            # 「GB 1350—2025 GB 1350—2025 表1 第3等…」（11-08 轻微，复发 3 次）。
            _std = str(v.get("standard", "") or "").strip()
            _src = str(v.get("limit_source", "") or "").strip()
            src = (_src if _src.startswith(_std) else f"{_std} {_src}".strip()) if _std else _src
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
        review.append("标准版本：当前执行 GB 1350—2009，限值已逐格人工核对并经复核；"
                      "GB 1350—2025 为 2027-01-01 预置，不得用于判旧货")
        review.append("放行决策（能否收购/接收）：系统不给「能收/不能收」结论，"
                      "只摆事实与待复核原因，需人工复核签字")
        uncovered.append("感官项「色泽、气味」：人工确认项，不参与数值判定")
        uncovered.append("其它等级（1/2/4/5 等）、粳稻谷表2：本任务未覆盖")
        uncovered.append("数据范围：只按批号前缀判断 DEMO 假数据；系统无法分辨实测值真假，"
                         "请勿用改批号的方式上传真实数据（改号后报告里的值仍是你上传的原值）")
        return {"已确认": confirmed, "待人工复核": review, "未覆盖": uncovered}

    def _checkpoints(self, ctx: dict, res: TaskResult) -> list[dict]:
        """沿用 client_ui.py::_checkpoints 口径：去哪 / 看什么 / 入口；系统外明说系统外。"""
        out = [{
            "kind": "record", "title": "先看这批的原始记录",
            "desc": "判定链实际读的原始值在本次 run 的 snapshots/quality.json，只读，不会改动任何数据。",
            "action": "snapshots/quality.json", "label": "打开原始记录（只读）",
        }, {
            "kind": "knowledge", "title": "去知识库核对限值",
            "desc": "当前执行 GB 1350—2009，限值已逐格人工核对并经复核。"
                    "如需补新资料，可在 /knowledge 页下半部分的「待核对区」上传或确认；"
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
        # 应产声明核对：只写证据，不改 status —— 失败/待复核任务本来就缺产物，
        # 记 UNKNOWN 是预期，不该再往 errors 里塞噪音。
        payload["declaration_check"] = self._check_declaration(run_dir, res, ctx)
        # 2026-11-13 产物回读：status=done 不算数，要把产物、回执、账本读回来核。
        # 副作用断言一并做：只读源一个字节都不该被这次任务改动。
        before = self._source_snapshots.pop(res.run_id, {})
        try:
            report = rb.verify_run(run_dir, res.outputs, before, payload)
        except Exception as e:  # 回读自身出错也要留痕，绝不静默当通过
            report = {"overall": "UNKNOWN",
                      "reason": f"回读自身异常：{type(e).__name__}: {str(e)[:200]}"}
        payload["readback"] = report
        # 只有 FAIL（只读源真被动过）才追加到 errors。
        # UNKNOWN 多发生在失败/待复核任务（本来就没产物），再塞一条会把真错因淹掉。
        if report.get("overall") == "FAIL":
            payload["errors"] = list(payload.get("errors") or []) + [
                f"产物回读未通过：{report.get('reason', '')}"]
        (run_dir / "summary.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        # 失败 / 待复核的任务别加这条事件：index_runs 从 trace 末尾取「终态短码」，
        # 多一条 readback 会把真正的错因顶掉（任务卡上会显示 readback 而不是缺限值）。
        if res.status not in ("failed", "partial", "pending_review"):
            self._trace(res, res.status, "readback",
                        f"overall={report.get('overall')} "
                        f"artifacts={report.get('denominator', {}).get('landed', '?')}"
                        f"/{report.get('denominator', {}).get('declared', '?')}"
                        f" side_effects={report.get('side_effects', {}).get('overall', '?')}")
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
