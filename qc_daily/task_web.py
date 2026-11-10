# -*- coding: utf-8 -*-
# task_web.py：Web 任务台 Blueprint（Phase 4）。Web 只做可视化，主入口是 CLI。
# 输入：POST /api/tasks/run 的 TaskRequest；输出：/tasks 页面 + 状态/产物/轨迹 API。
# 写操作显式校验 web.token / QC_WEB_TOKEN（不依赖全局 guard 的 token 空值放行）；
# 产物查看走 artifacts.resolve_artifact() 白名单，只回传相对路径，防目录穿越。
#
# 界面口径（2026-11-07 重做）：这一屏是给化验室的人用的，不是给验收人看的。
#   · 只出现「你要做什么 / 给哪个表 / 结果是什么」，不出现 run_id、digest、engine-C 这类内部词；
#   · 内部信息全部收进「技术信息」折叠，默认收起；
#   · 结论按「要不要你亲自动手」排序，而不是按系统处理顺序。
from __future__ import annotations

import re
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, session

from . import webtemplates as tpl
from .artifacts import index_runs, list_artifacts, resolve_artifact, resolve_run_dir
from .config import QCDailyConfig, env
from .tasks import TaskRequest, TaskRunner, get_run, list_tasks
from .tasks.registry import TASKS

task_bp = Blueprint("tasks", __name__)

#: run 根目录白名单：与 --out 允许值一致，防止把产物写到仓库任意位置
_ALLOWED_ROOTS = ("runs", "qc_daily_data/task_runs")
#: 上传文件的落地目录（与 runs/ 分开，避免被当成 run 目录扫到）
_UPLOAD_DIR = Path(__file__).resolve().parent.parent / "qc_daily_data" / "task_uploads"
_UPLOAD_EXT = {".csv", ".xlsx", ".xls"}
_UPLOAD_MAX = 10 * 1024 * 1024
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _write_guard():
    """写操作自己再校验一次：配了 token 就必须登录，未配则按现有演示模式（绑 127.0.0.1）。

    不直接依赖 webapp._guard —— 那里 `if not token: return None` 会放行，
    而任务写操作会导入文件、创建数据库，必须显式检查。
    """
    cfg = QCDailyConfig.load()
    token = cfg.web.get("token") or env(cfg.web.get("token_env", "QC_WEB_TOKEN"))
    if token and not session.get("auth"):
        return jsonify({"ok": False, "error": "未登录，不能触发任务；请先在 /login 登录"}), 401
    return None


def _find_root(run_id: str):
    for root in _ALLOWED_ROOTS:
        try:
            d = resolve_run_dir(run_id, root)
            if d.is_dir():
                return root, d
        except ValueError:
            continue
    return None, None


def _status(run_id: str) -> dict | None:
    with _LOCK:
        job = dict(_JOBS.get(run_id) or {})
    if job:
        return job
    for root in _ALLOWED_ROOTS:
        res = get_run(run_id, root)
        if res:
            return {"run_id": run_id, "status": res.status, "task_id": res.task_id,
                    "outputs": res.outputs, "trace": res.trace, "notes": res.notes,
                    "errors": res.errors, "sections": res.sections,
                    "metrics": res.metrics, "verify_ok": res.verify_ok,
                    "input_digest": res.input_digest,
                    "conclusion_digest": res.conclusion_digest,
                    "data_checksum": res.data_checksum,
                    "report_fingerprint": res.report_fingerprint,
                    "root": root}
    return None


# ------------------------------------------------------------------ 人话层
# 下面这层把 runner 产出的内部结构翻译成「使用人能直接读」的字段。
# 规则：翻译失败一律回退原文，绝不吞掉信息（宁可难看，不可丢结论）。

_STATUS_CN = {
    "queued": ("排队中", "mute"), "running": ("正在做", "run"),
    "collecting": ("正在读表格", "run"), "validating": ("正在检查数据", "run"),
    "analyzing": ("正在算", "run"), "rendering": ("正在生成报告", "run"),
    "done": ("做完了", "ok"), "partial": ("只做了一部分", "warn"),
    "pending_review": ("等你确认", "warn"), "failed": ("出错了", "bad"),
}

#: 三段式的显示名：不写「已确认」——按代码算完不等于业务上确认了
_SECTION_VIEW = {
    "已确认": ("机器算完、看不出疑点", "ok"),
    "待人工复核": ("必须你亲自确认", "warn"),
    "未覆盖": ("没找到标准依据，机器不判", "mute"),
}

_VERDICT_RE = re.compile(
    r"^(?P<batch>[^·]+?)\s*·\s*(?P<item>[^≥≤<>]+?)\s*"
    r"(?P<dir>[≥≤<>]+)\s*(?P<lim>[\d.]+)\s*→\s*实测\s*(?P<val>[\d.]+)\s*→\s*"
    r"代码判定「(?P<verdict>[^」]+)」（(?P<src>.*)）\s*$")
_DOUBT_PREFIX = "该值有录入疑点，结论不可用于放行"

# 「按规矩停下」≠「系统故障」。缺限值、空表、非 DEMO 批次、校验拦下……
# 七种停法共用一句红字「出错了」，使用人会去重启、去重传，而不是去补数据
# （夜测 2026-11-10 第 3/4/5 轮连续三晚记同一条）。
# 短码取自 trace 的终态事件，不靠 errors[0] 的字符串猜——那是拿最不确定的
# 方式报告一个确定性结果。
_STOP_CN = {
    "no_rows":             ("表里没读到数据行", "warn"),
    "values_unreadable":   ("实测值读不懂", "warn"),
    "no_dates":            ("日期列读不出", "warn"),
    "import_errors":       ("文件打不开", "warn"),
    "import_blocked":      ("表结构不合要求", "warn"),
    "unmapped_headers":    ("表头认不出", "warn"),
    "date_mismatch":       ("日期与表内不一致", "warn"),
    "missing_limits":      ("缺限值、按规矩没判", "warn"),
    "real_batch_rejected": ("有非 DEMO 批次", "warn"),
    "validation_error":    ("校验拦下、未发报告", "warn"),
    "request_rejected":    ("输入不合法", "warn"),
    "fingerprint_mismatch": ("指纹校验未通过", "bad"),
    "db_error":            ("独立库读取失败", "bad"),
    "exception":           ("运行中断", "bad"),
}


def _stop_code(d: dict) -> str:
    """从 trace 倒序找终态事件码；找不到返回空串（保持原显示，不瞎猜）。"""
    for t in reversed(d.get("trace") or []):
        if str(t.get("state", "")) not in ("failed", "partial", "pending_review"):
            continue
        ev = str(t.get("event", ""))
        if ev and ev not in ("state_change", "finished", "report_written"):
            return ev
    return ""


def _is_verdict_line(raw: str) -> bool:
    """这一行是「一个检测项的结论」，还是一句范围/版本/放行声明？

    只用来决定「要不要进分组计数」，不参与任何判定。宁可把可疑行留着，
    也不能把真结论踢掉：两种特征命中任一就算数据行。
    """
    s = str(raw or "").strip()
    if "→ 代码判定「" in s:
        return True
    # 批号开头（DEMO-001 / B2026-11）+ 分隔符「·」= 逐项明细行；
    # 说明文字都是中文开头（「标准版本：」「感官项…」「数据范围：」）。
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9_-]*\d[^·]{0,40}·", s))


def _dedup_src(src: str) -> str:
    """出处里标准号常被拼两遍（GB 1350—2025 GB 1350—2025 表1…），去掉相邻重复段。

    只删「紧挨着重复」的整段，不动后面的表号页码——宁可少删，不可删错。
    """
    s = (src or "").strip()
    toks = s.split()
    out: list[str] = []
    i = 0
    while i < len(toks):
        matched = False
        for n in range(min(6, (len(toks) - i) // 2), 1, -1):
            if toks[i:i + n] == toks[i + n:i + 2 * n]:
                out.extend(toks[i:i + n])
                i += 2 * n
                matched = True
                break
        if not matched:
            out.append(toks[i])
            i += 1
    return " ".join(out)


def _clean_item(raw: str) -> dict:
    """把一条结论压成「批次 项目 实测值（要求 X）结论」，出处塞进 tip。"""
    s = str(raw or "").strip()
    flag = ""
    if _DOUBT_PREFIX in s:
        head, _, tail = s.partition(_DOUBT_PREFIX)
        flag = "有录入疑点"
        tail = tail.lstrip(" →").strip()
        # 前缀里重复的项目名（「水分含量：」）丢掉，正文本身已经带项目了
        s = tail or (head.split("：")[0].strip() if "：" in head else "") or s

    m = _VERDICT_RE.match(s)
    if m:
        text = (f"{m.group('batch').strip()} {m.group('item').strip()} "
                f"{m.group('val')}（要求 {m.group('dir')}{m.group('lim')}）→ {m.group('verdict')}")
        tip = _dedup_src(m.group("src"))
        return {"text": text, "tip": tip, "flag": flag, "raw": str(raw)}

    # 疑点说明类长句：取第一句做正文，全文放 tip
    first = re.split(r"[；;。]", s, maxsplit=1)[0].strip()
    if len(first) > 120:
        first = first[:120] + "…"
    return {"text": first or s[:70], "tip": s if s != first else "", "flag": flag,
            "raw": str(raw)}


def _batch_count(sections: dict) -> int:
    """数这次涉及几个批次。

    只认「以字母开头、带数字」的批号（DEMO-001 / B2026-11），不认「其它等级（1/2/4/5」
    这种以中文开头、后面碰巧有数字的说明文字——否则 3 个批次能数成 4 个。
    """
    seen = set()
    for items in sections.values():
        for s in items or []:
            m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*\d)", str(s).strip())
            if m:
                seen.add(m.group(1))
    return len(seen)


def _headline(d: dict) -> tuple[str, str]:
    """一句话说清「这次跑出了什么」。返回 (文案, 语气)。

    一个数字只允许有一个来源：合格数来自 metrics（按项算），待确认数只数逐项结论，
    不把「标准版本提醒」这类固定说明混进去，否则 10 个检测项能写出 13 条待确认。
    """
    sec = d.get("sections") or {}
    met = d.get("metrics") or {}
    # 拒跑的时候，「为什么没跑」就是这次唯一的结论，别让人去折叠区里找
    if d.get("status") == "failed":
        errs = [str(e) for e in (d.get("errors") or []) if str(e).strip()]
        if errs:
            return f"没做 —— {errs[0]}", "bad"
        return "没做 —— 没给出原因，展开「技术信息」看留痕", "bad"
    n_ok = len(sec.get("已确认") or [])
    rev = [str(x) for x in (sec.get("待人工复核") or [])]
    n_rev_v = sum(1 for x in rev if "→ 代码判定「" in x)          # 逐项结论
    n_doubt = sum(1 for x in rev if "该值有录入疑点" in x        # 其中「数值合格但存疑」的
                  and "代码判定「合格」" in x)
    n_note = len(rev) - n_rev_v                                   # 说明/提醒，不是检测项
    n_miss = len(sec.get("未覆盖") or [])
    batches = _batch_count(sec)

    total = met.get("total_tests")
    fail = met.get("fail_tests")
    if isinstance(total, int) and isinstance(fail, int) and total > 0:
        head = f"{batches} 批共 {total} 个检测项：{total - fail} 项数值符合、{fail} 项不符合"
        if n_doubt:
            head += f"；符合的里有 {n_doubt} 项要先核对录入疑点，暂不能当结论"
        if n_note:
            head += f"；另有 {n_note} 条提醒"
        return head, ("warn" if (fail or n_rev_v) else "ok")

    # 录入自查这类没有逐项判定的任务：结论就是「扫出几处可疑」
    if not n_rev_v and not n_ok and rev:
        n_sus = sum(1 for x in rev if "标准版本" not in x and "系统不给" not in x)
        if n_sus:
            return (f"扫出 {n_sus} 处可疑数，逐条核对后改回原表——"
                    f"系统只标疑点，不改数、不判合格"), "warn"
        return "没扫出可疑数；这只说明「没发现明显敲错」，不等于数据都对", "ok"

    if not (n_ok or rev or n_miss):
        return "任务跑完了，但没有产出可读结论——展开下面的「技术信息」看原因", "warn"

    parts = []
    if n_ok:
        parts.append(f"{n_ok} 条机器算完没疑点")
    if n_rev_v:
        parts.append(f"{n_rev_v} 条要你确认")
    if n_miss:
        parts.append(f"{n_miss} 条缺标准依据")
    return "，".join(parts), ("warn" if n_rev_v else "ok")


def _priority_files(arts: list[dict], run_id: str) -> list[dict]:
    """只把「能给人看的成品」提到前面，工程文件不进主界面。

    顺序按人找东西的习惯排：日报正文 → Excel → 任务报告页 → PDF。
    （原先按文件名里有没有 report 判断，结果「品控日报_xxx.html」不算报告，
      被排到 Excel 后面，第一眼看到的是个表。）
    """
    def href(name: str) -> str:
        return f"/api/tasks/{run_id}/artifact/{name}"

    def rank(n: str) -> int:
        if "日报" in n:
            return 0
        return 1 if "report" in n.lower() else 2

    htmls = sorted([a["name"] for a in arts if a["name"].lower().endswith(".html")], key=rank)
    out: list[dict] = []
    for i, n in enumerate(htmls[:2]):
        out.append({"label": "打开日报（网页）" if rank(n) == 0 else "打开任务报告",
                    "href": href(n), "primary": i == 0})
    for a in arts:
        low = a["name"].lower()
        if low.endswith(".xlsx"):
            out.append({"label": "下载 Excel 表", "href": href(a["name"]), "primary": not out})
        elif low.endswith(".pdf"):
            out.append({"label": "打开 PDF", "href": href(a["name"]), "primary": False})
    return out


def _build_view(d: dict, run_id: str) -> dict:
    """把一份状态 dict 翻成前端直接渲染的结构；前端不再做业务判断。"""
    status_cn, status_tone = _STATUS_CN.get(d.get("status", ""), (d.get("status") or "未知", "mute"))
    if d.get("status") == "failed":
        # 只有真异常才配红字「出错了」；按规矩停下的一律降级成黄色短码。
        friendly = _STOP_CN.get(_stop_code(d))
        if friendly:
            status_cn, status_tone = friendly
    arts = d.get("artifacts") or []
    sec = d.get("sections") or {}
    head, head_tone = _headline(d)

    groups = []
    section_notes: list[str] = []
    for key in ("待人工复核", "已确认", "未覆盖"):   # 要人动手的排最前
        # 「标准版本…」「放行决策…」「感官项…」「数据范围：DEMO-only」是说明文字，
        # 不是检测项。三组里都有，不止「待人工复核」——旧版 count=len(items)
        # 一视同仁，40 个检测项被数成 43，录入自查明明 0 疑点却显示「亲自确认 2」
        # （夜测 11-10 r03/r04；11-09 报告里 M8 误判成「计数口径未定」）。
        # 判据与 _headline 一致：逐项结论行都含「→ 代码判定「」；再补一条
        # 「以批号开头且带 · 」兜住缺限值/真值未知这类没有结论字样的数据行。
        # ⚠️ 必须在 _clean_item **之前**判：清洗后「→ 代码判定「」会被改写掉。
        raw_items = [str(x) for x in (sec.get(key) or [])]
        verdict_raw = [x for x in raw_items if _is_verdict_line(x)]
        for x in raw_items:
            if not _is_verdict_line(x):
                section_notes.append(f"说明：{x[:120]}")
        items = [_clean_item(x) for x in verdict_raw]
        label, tone = _SECTION_VIEW.get(key, (key, "mute"))
        groups.append({"key": key, "label": label, "tone": tone,
                       "count": len(items), "items": items})

    # 结构化 critical 告警：detail 里本来就写着批号、项名和十几个 refs，
    # 旧版只读 notes + metrics，从不读 d["alerts"] → 最该给人看的那条留在 JSON 里
    # （夜测 11-10 r04：重复批次被拦得对，界面却不说哪一批）。
    alerts: list[str] = []
    for a in (d.get("alerts") or []):
        if not isinstance(a, dict):
            continue
        if str(a.get("level", "")).lower() not in ("error", "critical"):
            continue
        detail = str(a.get("detail") or a.get("message") or "").strip()
        if detail:
            alerts.append(detail[:400])
    alerts = alerts[:2]
    # 范围/版本/放行声明紧跟 critical：这些是「这份数据能不能用」的前提，
    # 不能被逐行明细挤到截断线外（否则又只剩一句「已确认」摆在那儿）。
    alerts.extend(section_notes)

    # 「下一步怎么办」写在 notes 里，旧版不进 view，卡片上看不到（夜测 M6）。
    # 旧版先 notes[:3] 再 note_lines[:2] 双重截断：3 行读不懂只列第 1 行，
    # 使用人改完第 1 行重传还会再撞两次同样的错（夜测 11-10 r04）。全留。
    for n in (d.get("notes") or []):
        # 导入层给每条 note 加了「文件名[sheet] 」前缀；剥掉它再判语气，
        # 否则一句「下一步：…」会被套成「提示：xxx.xlsx[记录] 下一步：…」。
        s = re.sub(r"^[^\s\[]*\[[^\]]*\]\s*", "", str(n).strip())
        if not s or s.startswith("[pending_review]"):
            continue
        alerts.append(s if s.startswith(("下一步", "原因", "提示", "说明",
                                         "⚠️", "⛔", "【版本】"))
                      else f"提示：{s}")
    for a in (d.get("metrics") or {}).get("alerts") or []:
        alerts.append(str(a))
    errs = [str(e) for e in (d.get("errors") or []) if str(e).strip()]
    # 失败时第一条已经当一句话结论了，别在这儿再念一遍
    for e in (errs[1:] if (d.get("status") == "failed" and errs) else errs):
        alerts.append(f"报错：{e}")
    # 上限放宽到 16：说明行已提到最前，逐行明细不该再把它们挤出屏幕。
    if len(alerts) > 16:
        rest = len(alerts) - 16
        alerts = alerts[:16] + [f"另有 {rest} 条提醒（完整见 summary.json）"]

    tech_files = [a["name"] for a in arts
                  if not re.search(r"\.(html|xlsx|pdf)$", a["name"], re.I)]
    return {
        "status": d.get("status", ""), "status_cn": status_cn, "status_tone": status_tone,
        "headline": head, "headline_tone": head_tone,
        "alerts": alerts,
        "files": _priority_files(arts, run_id),
        "groups": groups,
        "task_name": (TASKS[d["task_id"]].name if d.get("task_id") in TASKS else "（历史任务）"),
        "tech": {
            "run_id": run_id,
            "digests": [
                ["数据指纹", d.get("input_digest") or "—"],
                ["结论指纹", d.get("conclusion_digest") or "—"],
                ["报告防篡改", d.get("report_fingerprint") or "—"],
            ],
            "files": tech_files,
            "all_files": [a["name"] for a in arts],
        },
        "trace": d.get("trace") or [],
    }


class TaskSpecName:
    """task_id 查不到时的兜底，避免页面因为历史 run 而 500。"""

    name = "（历史任务）"


def _history_rows(limit: int = 10) -> list[dict]:
    """最近做过的活：翻成人能认的「哪天 · 做了什么 · 成没成」。"""
    rows = index_runs("runs", limit=40) + index_runs("qc_daily_data/task_runs", limit=40)
    rows = sorted(rows, key=lambda x: x.get("started_at", ""), reverse=True)
    out = []
    for r in rows:
        if str(r.get("run_id", "")).startswith("T") is False and not r.get("task_id"):
            continue
        task = TASKS.get(r.get("task_id", ""))
        name = task.name if task else "（已下线的任务）"
        when = (r.get("started_at") or "")[:16]
        if when:
            try:
                when = datetime.strptime(when, "%Y-%m-%d %H:%M").strftime("%m-%d %H:%M")
            except ValueError:
                pass
        stop = str(r.get("stop_code") or "")
        cn, tone = _STATUS_CN.get(r.get("status", ""), (r.get("status") or "", "mute"))
        if r.get("status") == "failed" and stop in _STOP_CN:
            cn, tone = _STOP_CN[stop]
        errs = [e for e in (r.get("errors") or []) if str(e).strip()]
        reason = str(errs[0]).strip() if errs else ""
        # 右栏窄：先给 ≤10 字的短错因码，再挂一点原文。旧版截 22 字，
        # 四种不同的病前 22 字全一样，区分点恰好被切掉（夜测 11-10 r05）。
        short = _STOP_CN.get(stop, ("", ""))[0]
        if short:
            reason = f"{short}：{reason[:14] + '…' if len(reason) > 14 else reason}" \
                if reason else short
        elif len(reason) > 22:
            reason = reason[:22] + "…"
        out.append({"run_id": r.get("run_id", ""), "name": name, "when": when,
                    "status_cn": cn, "tone": tone, "reason": reason, "count": 1,
                    "report_date": r.get("report_date", "")})
        if len(out) >= limit:
            break
    # 同一分钟、同名、同状态、同错因的连点记录折叠成「×N」，
    # 否则演示时右栏挂六条一模一样的「出错了」很扎眼（夜测 2026-11-09 G7）
    merged: list[dict] = []
    for h in out:
        key = (h["name"], h["when"], h["status_cn"], h["reason"])
        if merged and merged[-1].get("_key") == key:
            merged[-1]["count"] += 1
            continue
        h["_key"] = key
        merged.append(h)
    for h in merged:
        h.pop("_key", None)
    return merged


# ------------------------------------------------------------------ 页面

_CSS = """
.tk-wrap{display:grid;grid-template-columns:236px 1fr;gap:16px;align-items:start}
.tk-side{background:#fff;border:1px solid #e4e7ea;border-radius:10px;padding:12px}
.tk-side h3{font-size:11px;color:#8f959e;margin:2px 0 8px;font-weight:600;letter-spacing:.4px}
.tk-item{display:block;width:100%;text-align:left;border:1px solid transparent;background:#f6f8f7;
color:#1f2328;border-radius:8px;padding:10px 11px;margin-bottom:7px;cursor:pointer;font-family:inherit;
line-height:1.5}
.tk-item:hover{border-color:#b81c22}
.tk-item.on{border-color:#b81c22;background:#fef0ef}
.tk-item b{display:block;font-size:13.5px;font-weight:600}
.tk-item small{display:block;font-size:11px;color:#8f959e;margin-top:3px}
.tk-item.aux{background:#fff;border:1px dashed #d6dbe0}
.tk-hist{display:flex;gap:6px;align-items:baseline;font-size:11.5px;padding:6px 4px;
border-bottom:1px dashed #eef0f2;cursor:pointer}
.tk-hist:hover{background:#fafbfb}
.tk-hist .w{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#4a5058}
.tk-hist .w i{font-style:normal;color:#8f959e;margin-right:5px}
.tk-hist em{font-style:normal;font-size:10.5px}
.tk-muted{font-size:11.5px;color:#8f959e;padding:8px 0}
.tk-main{background:#fff;border:1px solid #e4e7ea;border-radius:10px;padding:18px 20px}
.tk-h{display:flex;align-items:center;gap:10px;margin-bottom:4px}
.tk-h h2{font-size:17px;margin:0;flex:1;font-weight:600}
.tk-sub{font-size:12.5px;color:#6b7280;margin-bottom:16px;line-height:1.6}
.tk-step{font-size:11px;color:#8f959e;font-weight:600;letter-spacing:.5px;margin:16px 0 7px}
.tk-drop{border:1.5px dashed #cbd5e1;border-radius:8px;padding:14px;background:#fafbfb;
display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.tk-drop.over{border-color:#b81c22;background:#fef0ef}
.tk-drop .fname{font-size:13px;color:#1f2328;font-weight:500}
.tk-drop .fmeta{font-size:11.5px;color:#8f959e;margin-top:2px}
.tk-btn{border:0;background:#b81c22;color:#fff;border-radius:7px;padding:9px 18px;font-size:13px;
cursor:pointer;font-family:inherit;font-weight:500}
.tk-btn:disabled{opacity:.55;cursor:default}
.tk-btn.gray{background:#f2f3f5;color:#4a5058}
.tk-btn.line{background:#fff;color:#b81c22;border:1px solid #e6b8b6}
.tk-date{border:1px solid #cbd5e1;border-radius:7px;padding:8px 10px;font-size:13px;
font-family:inherit;width:170px}
.tk-badge{font-size:11.5px;padding:3px 10px;border-radius:12px;background:#eef0f2;color:#6b7280}
.tk-badge.ok{background:#e9f6f0;color:#0f6e56}
.tk-badge.warn{background:#fff6e5;color:#8a5a06}
.tk-badge.bad{background:#fef0ef;color:#a32d2d}
.tk-badge.run{background:#eef4ff;color:#1d4ed8}
.tk-msg{font-size:12.5px;color:#a32d2d;margin:10px 0;line-height:1.6}
.tk-res{margin-top:18px;border-top:1px solid #eef0f2;padding-top:16px}
.tk-head{font-size:15px;line-height:1.6;font-weight:500;color:#1f2328}
.tk-head.warn{color:#8a5a06}
.tk-files{display:flex;gap:9px;flex-wrap:wrap;margin:14px 0 4px}
.tk-files a{text-decoration:none;font-size:12.5px;padding:9px 16px;border-radius:7px;
border:1px solid #e6b8b6;color:#b81c22;background:#fff;font-weight:500}
.tk-files a.pri{background:#b81c22;color:#fff;border-color:#b81c22}
.tk-alert{background:#fff6e5;border-left:3px solid #c2771a;padding:8px 11px;font-size:12.5px;
margin:8px 0;line-height:1.6;color:#6b4a06}
.tk-grp{margin-top:16px}
.tk-grp>.gt{font-size:13px;font-weight:600;margin-bottom:7px;display:flex;align-items:center;gap:7px}
.tk-grp>.gt .n{font-size:11px;font-weight:500;padding:1px 7px;border-radius:9px;background:#eef0f2;color:#6b7280}
.tk-li{font-size:12.5px;line-height:1.75;padding:5px 0 5px 13px;border-left:2px solid #eef0f2;
margin-bottom:2px;color:#374151}
.tk-li .fl{font-size:10.5px;background:#fef0ef;color:#a32d2d;padding:1px 6px;border-radius:4px;
margin-right:6px;white-space:nowrap}
.tk-li .tp{font-size:11px;color:#9ca3af;margin-left:6px}
.tk-empty{font-size:12px;color:#9ca3af;padding:6px 0 6px 13px}
.tk-note{background:#fff6e5;border:1px dashed #c2771a;padding:10px 12px;font-size:12px;
line-height:1.75;margin-top:14px;border-radius:6px}
details.tk-tech{margin-top:18px;border-top:1px solid #eef0f2;padding-top:12px}
details.tk-tech summary{font-size:12px;color:#8f959e;cursor:pointer;user-select:none}
details.tk-tech .inner{font-size:11.5px;color:#6b7280;line-height:2;margin-top:9px;
font-family:Consolas,monospace;word-break:break-all}
details.tk-tech code{background:#f6f8f7;padding:1px 5px;border-radius:3px}
.tk-ev{font-family:Consolas,monospace;font-size:11px;background:#f6f8f7;padding:5px 8px;
margin-bottom:4px;white-space:pre-wrap;border-radius:4px;color:#4a5058}
"""

_JS = r"""
var TK={id:'',run:'',timer:null,file:''};
function tkEl(x){return document.getElementById(x);}
function tkEsc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
 return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function tkBadge(txt,tone){var b=tkEl('tk-badge');b.textContent=txt;
 b.className='tk-badge'+(tone&&tone!=='mute'?' '+tone:'');}

// 左栏：选中哪个任务
document.querySelectorAll('.tk-item').forEach(function(b){b.onclick=function(){
 document.querySelectorAll('.tk-item').forEach(function(x){x.classList.remove('on');});
 b.classList.add('on');TK.id=b.dataset.id;TK.run='';TK.file='';
 tkEl('tk-title').textContent=b.querySelector('b').textContent;
 tkEl('tk-sub').textContent=b.querySelector('small').textContent;
 tkBadge('还没开始','mute');
 tkEl('tk-msg').textContent='';
 tkEl('tk-res').innerHTML='';
 if(b.dataset.aux==='1'){tkEl('tk-workspace').hidden=true;tkEl('tk-auxhint').hidden=false;}
 else{tkEl('tk-workspace').hidden=false;tkEl('tk-auxhint').hidden=true;}
};});

// 选文件
tkEl('tk-pick').onclick=function(){tkEl('tk-file').click();};
tkEl('tk-file').onchange=function(){
 var f=this.files&&this.files[0];if(!f)return;
 var fd=new FormData();fd.append('file',f);
 tkEl('tk-msg').textContent='正在上传…';
 fetch('/api/tasks/upload',{method:'POST',body:fd}).then(function(r){return r.json();})
 .then(function(d){
  if(!d.ok){tkEl('tk-msg').textContent=d.error||'上传失败';return;}
  TK.file=d.path;tkEl('tk-msg').textContent='';
  tkEl('tk-name').textContent=d.name;
  tkEl('tk-meta').textContent=d.rows?('已读到 '+d.rows+' 行数据'):(d.size_kb+' KB');
  tkEl('tk-drop').classList.add('have');
 }).catch(function(e){tkEl('tk-msg').textContent='上传失败：'+e.message;});};

// 拖拽
var dz=tkEl('tk-drop');
['dragenter','dragover'].forEach(function(ev){dz.addEventListener(ev,function(e){
 e.preventDefault();dz.classList.add('over');});});
['dragleave','drop'].forEach(function(ev){dz.addEventListener(ev,function(e){
 e.preventDefault();dz.classList.remove('over');});});
dz.addEventListener('drop',function(e){
 var f=e.dataTransfer&&e.dataTransfer.files&&e.dataTransfer.files[0];if(!f)return;
 var fd=new FormData();fd.append('file',f);
 tkEl('tk-msg').textContent='正在上传…';
 fetch('/api/tasks/upload',{method:'POST',body:fd}).then(function(r){return r.json();})
 .then(function(d){
  if(!d.ok){tkEl('tk-msg').textContent=d.error||'上传失败';return;}
  TK.file=d.path;tkEl('tk-msg').textContent='';
  tkEl('tk-name').textContent=d.name;
  tkEl('tk-meta').textContent=d.rows?('已读到 '+d.rows+' 行数据'):(d.size_kb+' KB');
 });});

function tkGroup(g){
 var li=(g.items||[]).map(function(it){
  return '<div class="tk-li">'+(it.flag?'<span class="fl">'+tkEsc(it.flag)+'</span>':'')+
   tkEsc(it.text)+(it.tip?'<span class="tp" title="'+tkEsc(it.tip)+'">ⓘ 出处</span>':'')+'</div>';
 }).join('');
 if(!li){
  li='<div class="tk-empty">'+(g.key==='待人工复核'?'这次没有需要你额外确认的项。'
    :(g.key==='已确认'?'这次没有机器能直接算完的项。':'没有缺依据的项。'))+'</div>';
 }
 return '<div class="tk-grp"><div class="gt">'+tkEsc(g.label)+
  (g.count?'<span class="n">'+g.count+'</span>':'')+'</div>'+li+'</div>';
}

function tkRender(v){
 var tone=v.status_tone||'mute';
 tkBadge(v.status_cn||'',tone);
 var html='<div class="tk-res">';
 html+='<div class="tk-head '+(v.headline_tone||'')+'">'+tkEsc(v.headline)+'</div>';
 if(v.alerts&&v.alerts.length){
  html+=v.alerts.map(function(a){return '<div class="tk-alert">'+tkEsc(a)+'</div>';}).join('');
 }
 if(v.files&&v.files.length){
  html+='<div class="tk-files">'+v.files.map(function(f){
   return '<a class="'+(f.primary?'pri':'')+'" href="'+tkEsc(f.href)+'" target="_blank" rel="noopener">'+
    tkEsc(f.label)+'</a>';}).join('')+'</div>';
 }
 html+=(v.groups||[]).map(tkGroup).join('');
 html+='<div class="tk-note">报告是给你看的辅助判断，不是放行依据。最终签字由人来做。</div>';
 var t=v.tech||{};
 html+='<details class="tk-tech"><summary>技术信息（run_id / 指纹 / 全部文件）</summary><div class="inner">';
 html+='任务编号 <code>'+tkEsc(t.run_id||'')+'</code><br>';
 (t.digests||[]).forEach(function(d){html+=tkEsc(d[0])+' <code>'+tkEsc(String(d[1]).slice(0,16))+'…</code><br>';});
 if(t.files&&t.files.length){html+='全部文件：'+(t.files||[]).map(function(n){
   return '<code>'+tkEsc(n)+'</code>';}).join(' ');}
 html+='</div></details>';
 if(v.trace&&v.trace.length){
  html+='<details class="tk-tech"><summary>执行过程（'+v.trace.length+' 步）</summary><div class="inner">'+
   v.trace.map(function(x){return '<div class="tk-ev">#'+tkEsc(x.step)+' '+tkEsc(x.state)+' '+
    tkEsc(x.event)+' '+tkEsc(x.detail||'')+'</div>';}).join('')+'</div></details>';
 }
 html+='</div>';
 tkEl('tk-res').innerHTML=html;
}

function tkPoll(){
 if(!TK.run)return;
 fetch('/api/tasks/'+encodeURIComponent(TK.run)).then(function(r){return r.json();})
 .then(function(d){
  if(!d.ok){tkEl('tk-msg').textContent=d.error||'查不到这次结果';return;}
  if(d.view)tkRender(d.view);
  if(['done','partial','failed','pending_review'].indexOf(d.status)>=0){
   clearInterval(TK.timer);TK.timer=null;tkEl('tk-msg').textContent='';}
 }).catch(function(e){tkEl('tk-msg').textContent='查询失败：'+e.message;});}

tkEl('tk-run').onclick=function(){
 var btn=this;
 var aux=document.querySelector('.tk-item.on').dataset.aux==='1';
 if(!aux&&!TK.file){tkEl('tk-msg').textContent='请先选一个表格文件（点「选表格」或直接拖进来）。';return;}
 btn.disabled=true;
 tkBadge('提交中','run');tkEl('tk-msg').textContent='';
 tkEl('tk-res').innerHTML='<div class="tk-res"><div class="tk-head">正在处理，稍等…</div></div>';
 fetch('/api/tasks/run',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({task_id:TK.id,inputs:TK.file?[TK.file]:[],
   report_date:tkEl('tk-date').value,formats:['html','excel'],output_root:'runs'})})
 .then(function(r){return r.json();}).then(function(d){
  btn.disabled=false;
  if(!d.ok){tkEl('tk-msg').textContent=d.error||'没能开始，请重试';tkBadge('没开始','bad');return;}
  TK.run=d.run_id;tkBadge('正在做','run');
  clearInterval(TK.timer);TK.timer=setInterval(tkPoll,1200);tkPoll();
 }).catch(function(e){btn.disabled=false;tkEl('tk-msg').textContent='没能开始：'+e.message;});};

// 历史：点了就重新载入那次结果
tkEl('tk-history').onclick=function(e){
 var row=e.target.closest('.tk-hist');if(!row)return;
 TK.run=row.dataset.run;tkBadge('正在读','run');tkPoll();};

// 首次：默认选第一个一等任务
(function(){
 var first=document.querySelector('.tk-item:not(.aux)');
 if(first){first.click();}
 tkEl('tk-date').value=(new Date()).toISOString().slice(0,10);
})();
"""


@task_bp.get("/tasks")
def tasks_view():
    primary = list_tasks()
    more = [t for t in list_tasks(include_auxiliary=True) if t["auxiliary"]]

    def card(t, aux=False):
        return (f"<button class='tk-item{' aux' if aux else ''}' data-id='{t['task_id']}' "
                f"data-aux='{1 if aux else 0}'><b>{t['name']}</b>"
                f"<small>{t['description']}</small></button>")

    cards = "".join(card(t) for t in primary)
    more_html = "".join(card(t, True) for t in more) or "<div class='tk-muted'>无</div>"
    hist = _history_rows()

    def _hist_item(h: dict) -> str:
        cnt = f" ×{h['count']}" if h.get("count", 1) > 1 else ""
        rsn = h.get("reason") or ""
        rsn = rsn.replace("<", "&lt;").replace(">", "&gt;")
        sub = f"<br><span style='color:#8f959e;font-size:12px'>{rsn}</span>" if rsn else ""
        color = "#0f6e56" if h["tone"] == "ok" else ("#8a5a06" if h["tone"] == "warn" else "#8f959e")
        return (f"<div class='tk-hist' data-run='{h['run_id']}'>"
                f"<span class='w'><i>{h['when']}</i>{h['name']}{cnt}{sub}</span>"
                f"<em style='color:{color}'>{h['status_cn']}</em></div>")

    hist_html = "".join(_hist_item(h) for h in hist) or "<div class='tk-muted'>还没做过任务</div>"

    first = primary[0] if primary else {"name": "选一个任务", "description": ""}
    body = f"""
<style>{_CSS}</style>
<div class='tk-wrap'>
  <aside class='tk-side'>
    <h3>你要做什么？</h3>
    <div id='tk-primary'>{cards}</div>
    <h3 style='margin-top:14px'>其他</h3>
    <div>{more_html}</div>
    <h3 style='margin-top:14px'>最近做过的</h3>
    <div id='tk-history'>{hist_html}</div>
  </aside>
  <main class='tk-main'>
    <div class='tk-h'>
      <h2 id='tk-title'>{first['name']}</h2>
      <span class='tk-badge' id='tk-badge'>还没开始</span>
    </div>
    <div class='tk-sub' id='tk-sub'>{first['description']}</div>

    <div id='tk-workspace'>
      <div class='tk-step'>① 选表格</div>
      <div class='tk-drop' id='tk-drop'>
        <button class='tk-btn gray' id='tk-pick'>选表格…</button>
        <div>
          <div class='fname' id='tk-name'>还没选文件</div>
          <div class='fmeta' id='tk-meta'>Excel / CSV，也可以直接把文件拖到这里</div>
        </div>
        <input type='file' id='tk-file' accept='.csv,.xlsx,.xls' style='display:none'>
      </div>

      <div class='tk-step'>② 这是哪天的数据</div>
      <input type='date' class='tk-date' id='tk-date'>

      <div style='margin-top:18px'>
        <button class='tk-btn' id='tk-run'>开始分析</button>
      </div>
    </div>

    <div id='tk-auxhint' hidden>
      <div class='tk-note' style='margin-top:8px'>这个任务不用上传表格，去左侧「对话客户端」直接问就行。</div>
    </div>

    <div class='tk-msg' id='tk-msg'></div>
    <div id='tk-res'></div>
  </main>
</div>
"""
    return tpl.page(title="做任务", active="tasks", body=body,
                    meta="业务任务台 · 只接 DEMO 假数据", extra_js=_JS)


# ------------------------------------------------------------------ API

def _safe_name(raw: str) -> str:
    base = Path(raw or "upload").name
    stem = re.sub(r"[^\w\u4e00-\u9fff.\-]", "_", base).strip("._") or "upload"
    return stem[:80]


def _peek_table(p: Path) -> tuple[int, str]:
    """扫一眼表：多少行、数据是哪一天的。

    日期必须自动读出来，不能让用的人再去填——填错了就查不到数据、出一份空报告，
    而他不会知道是自己填错还是系统坏了。
    """
    rows, dates = 0, {}
    try:
        from .importer import guess_header_row
        if p.suffix.lower() == ".csv":
            import csv
            with p.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
                all_rows = [list(r) for r in csv.reader(f)]
            # CSV 上面同样可能压着标题行 / 「检验日期：2026-11-10」这种说明行。
            # 旧版用 DictReader 硬吃首行 → 一列日期都找不到 → 回退今天 → 整单拒跑
            # （夜测 2026-11-09 G1：xlsx 分支修了，CSV 分支漏改）。
            hi = guess_header_row(all_rows) if all_rows else 0
            head = all_rows[hi] if hi < len(all_rows) else []
            idx = [i for i, h in enumerate(head) if h and "日期" in str(h)]
            for row in all_rows[hi + 1:]:
                if not any(str(c).strip() for c in row):
                    continue
                rows += 1
                for i in idx:
                    if i < len(row) and str(row[i]).strip():
                        d = str(row[i]).strip()[:10]
                        dates[d] = dates.get(d, 0) + 1
        else:
            import openpyxl
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            ws = wb[wb.sheetnames[0]]
            all_rows = [list(r) for r in ws.iter_rows(values_only=True)]
            # 化验室的原始表上面常压着标题行/业务说明行（送检水稻检测就是第 3 行
            # 才是表头）。硬取第 1 行会一列日期都找不到 → 回退今天 → 与表内日期
            # 不匹配而拒跑（2026-11-08 演示前实测）。与导入层共用同一套探测。
            hi = guess_header_row(all_rows)
            head = all_rows[hi] if hi < len(all_rows) else ()
            idx = [i for i, h in enumerate(head) if h and "日期" in str(h)]
            for row in all_rows[hi + 1:]:
                if not any(c is not None and str(c).strip() for c in row):
                    continue
                rows += 1
                for i in idx:
                    if i < len(row) and row[i]:
                        d = str(getattr(row[i], "date", lambda: row[i])())[:10]
                        dates[d] = dates.get(d, 0) + 1
            wb.close()
    except Exception:
        return rows, ""
    best = max(dates.items(), key=lambda kv: kv[1])[0] if dates else ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", best or ""):
        return rows, best
    return rows, ""


def start_background(req, root: str = "runs") -> str:
    """起一个后台任务并登记进 _JOBS，返回 run_id。

    对话端（/app）直接复用这套状态管理，不重复实现一份。
    """
    run_id = f"T{date.today():%Y%m%d}-{uuid.uuid4().hex[:10]}"
    with _LOCK:
        _JOBS[run_id] = {"run_id": run_id, "status": "running", "task_id": req.task_id,
                         "outputs": [], "trace": [], "notes": [], "errors": [],
                         "sections": {}, "metrics": {}, "artifacts": [], "root": root}

    def work():
        try:
            res = TaskRunner(config=QCDailyConfig.load()).run(req, run_id=run_id)
            with _LOCK:
                _JOBS[res.run_id] = {"run_id": res.run_id, "status": res.status,
                                     "task_id": res.task_id, "outputs": res.outputs,
                                     "trace": res.trace, "notes": res.notes,
                                     "errors": res.errors, "sections": res.sections,
                                     "metrics": res.metrics, "verify_ok": res.verify_ok,
                                     "input_digest": res.input_digest,
                                     "conclusion_digest": res.conclusion_digest,
                                     "data_checksum": res.data_checksum,
                                     "report_fingerprint": res.report_fingerprint,
                                     "root": root,
                                     "artifacts": list_artifacts(res.run_id, root)}
        except Exception as e:
            with _LOCK:
                _JOBS.setdefault(run_id, {}).update(
                    {"status": "failed",
                     "errors": [f"运行中断（{type(e).__name__}）：{str(e)[:200]}"]})

    threading.Thread(target=work, daemon=True).start()
    return run_id


@task_bp.post("/api/tasks/upload")
def api_upload():
    """收一张表，落在 qc_daily_data/task_uploads/，回传相对仓库根的路径。

    使用人不需要知道路径，选完文件后前端只拿这个 path 去跑任务。
    顺手把「多少行」和「数据是哪天的」读出来——这两件事不该让使用人自己填。
    """
    denied = _write_guard()
    if denied:
        return denied
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "没收到文件"}), 400
    name = _safe_name(f.filename)
    if Path(name).suffix.lower() not in _UPLOAD_EXT:
        return jsonify({"ok": False,
                        "error": f"只支持 {'/'.join(sorted(_UPLOAD_EXT))}，你传的是 {Path(name).suffix}"}), 400
    blob = f.read()
    if len(blob) > _UPLOAD_MAX:
        return jsonify({"ok": False, "error": "文件超过 10 MB，请先在 Excel 里删掉多余列"}), 400
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = _UPLOAD_DIR / f"{stamp}_{name}"
    dst.write_bytes(blob)
    rel = dst.relative_to(Path(__file__).resolve().parent.parent).as_posix()
    rows, data_date = _peek_table(dst)
    return jsonify({"ok": True, "path": rel, "name": name,
                    "size_kb": round(len(blob) / 1024, 1),
                    "rows": rows, "data_date": data_date})


@task_bp.post("/api/tasks/run")
def api_run():
    denied = _write_guard()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    root = str(data.get("output_root") or "runs").rstrip("/\\")
    if root not in _ALLOWED_ROOTS:
        return jsonify({"ok": False,
                        "error": f"output_root 只允许 {list(_ALLOWED_ROOTS)}（防任意路径写入）"}), 400
    try:
        req = TaskRequest(
            task_id=str(data.get("task_id") or ""),
            inputs=[str(x) for x in (data.get("inputs") or [])],
            report_date=str(data.get("report_date") or ""),
            formats=([str(x) for x in (data.get("formats") or [])]
                     or list(QCDailyConfig.load().export.get("formats", ["html", "pdf", "excel"]))),
            output_root=root, push=False)
        req.validate(TASKS.keys())
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}), 400

    run_id = f"T{date.today():%Y%m%d}-{uuid.uuid4().hex[:10]}"
    # TaskRunner 会按 run_id 建目录；这里预置一个 queued 状态供前端轮询，避免改 runner 主干
    with _LOCK:
        _JOBS[run_id] = {"run_id": run_id, "status": "running", "task_id": req.task_id,
                         "outputs": [], "trace": [], "notes": [], "errors": [],
                         "sections": {}, "metrics": {}, "artifacts": [], "root": root}

    def work():
        try:
            res = TaskRunner(config=QCDailyConfig.load()).run(req, run_id=run_id)
            with _LOCK:
                _JOBS[res.run_id] = {"run_id": res.run_id, "status": res.status,
                                     "task_id": res.task_id, "outputs": res.outputs,
                                     "trace": res.trace, "notes": res.notes,
                                     "errors": res.errors, "sections": res.sections,
                                     "metrics": res.metrics, "verify_ok": res.verify_ok,
                                     "input_digest": res.input_digest,
                                     "conclusion_digest": res.conclusion_digest,
                                     "data_checksum": res.data_checksum,
                                     "report_fingerprint": res.report_fingerprint,
                                     "root": root,
                                     "artifacts": list_artifacts(res.run_id, root)}
        except Exception as e:
            with _LOCK:
                _JOBS[run_id].update({"status": "failed",
                                      "errors": [f"运行中断（{type(e).__name__}）：{str(e)[:200]}"]})

    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True, "run_id": run_id, "status": "running"})


@task_bp.get("/api/tasks/<run_id>")
def api_status(run_id: str):
    d = _status(run_id)
    if not d:
        return jsonify({"ok": False, "error": "找不到这次运行"}), 404
    root = d.get("root") or (_find_root(run_id)[0] or "runs")
    try:
        d["artifacts"] = list_artifacts(run_id, root)
    except ValueError:
        d["artifacts"] = []
    d["ok"] = True
    d["view"] = _build_view(d, run_id)
    return jsonify(d)


@task_bp.get("/api/tasks/<run_id>/artifact/<path:name>")
def api_artifact(run_id: str, name: str):
    root, _ = _find_root(run_id)
    if not root:
        return jsonify({"ok": False, "error": "run 目录不在白名单内"}), 404
    try:
        p = resolve_artifact(run_id, name, root)
    except (ValueError, FileNotFoundError) as e:
        return jsonify({"ok": False, "error": f"产物不可读：{e}"}), 400
    mime = {".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
            ".jsonl": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8",
            ".csv": "text/csv; charset=utf-8",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".pdf": "application/pdf"}.get(p.suffix.lower(), "application/octet-stream")
    return Response(p.read_bytes(), mimetype=mime)
