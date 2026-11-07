# -*- coding: utf-8 -*-
# harness.py：qa.py 外面的可审计壳，只跑 DEMO- 假数据；判定/比较/限值取值全在 4 个工具代码里。
# 输入：一句提问 + batch_id（必须 DEMO- 开头）；开关 QC_USE_LLM 默认关闭。
# 输出：三段式「已确认 / 待人工复核 / 未覆盖」+ runs/<batch_id>/ 下 trace.jsonl、summary.json、report.html。
# 只读源数据：不接 SQL/API、不读 raw 快照、不发外部系统；模型不产出判定结论、不替代签字。
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from html import escape

from .console import say
from .limits_resolver import build_products, load_limits
from .models import compare
from .validate import validate_records

MAX_STEPS = 6                 # 主循环步数上限，超了直接返回「已达最大步数，请人工复核」
MAX_FAIL = 2                  # 连续失败次数上限（代码强停，不给模型说话机会）
MAX_CONTEXT_CHARS = 320       # 每次调模型前的上下文上限；演示用小值，便于验证截断留痕
NO_ANSWER = "语料库中未找到相关内容，请人工核对标准原文，不得推算"
DATA_SCOPE = "DEMO-only（仅内置假数据，不含任何真实批次或实测值）"
DEMO_BATCH_DEFAULT = "DEMO-001"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 语料版本映射：None 表示该版本限值尚未收录，必须拒答，不许模型用记忆补数
_VERSION_ALIASES = {
    "GB 1350—2025": "GB1350-2025", "GB 1350-2025": "GB1350-2025",
    "GB1350-2025": "GB1350-2025", "2025": "GB1350-2025",
    "GB 1350—2009": None, "GB 1350-2009": None, "GB1350-2009": None, "2009": None,
}

# DEMO 假数据（不是任何真实批次/实测值）；水分含量 145.0 是故意放入的录入错误演示值
_DEMO_PLAN = {
    "DEMO-001": [("出糙率", 76.5), ("整精米率", 45.0), ("水分含量", 145.0),
                 ("杂质含量", 0.8), ("互混率", 4.5)],
}

SYSTEM = (
    "你是稻谷验收审计助手，只负责把工具返回的代码结论与标准原文翻译成人话。"
    "① 不得自行心算或估算任何数值；② 不得自行比较大小、不得自行判定合格或不合格；"
    "③ 每个数字都必须能说出出处与版本号；④ 查不到就说查不到并转人工，不得用记忆补一个数。"
    "你的回答只是解释层，不是判定结论，不替代人工签字，也不给放行建议。"
)

_SESSION: dict = {}


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _is_demo(batch_id) -> bool:
    return str(batch_id or "").upper().startswith("DEMO-")


def _runs_dir(batch_id: str) -> str:
    return os.path.join(ROOT, "runs", batch_id)


def _tool_result(ok, safety, confidence, data, brief, reject=False) -> dict:
    """四个工具统一返回结构：safety / confidence 供主循环代码闸门消费。"""
    return {"ok": ok, "safety": safety, "confidence": confidence,
            "data": data, "brief": brief, "reject": reject}


def _demo_records(batch_id: str) -> list[dict]:
    """限值一律经 resolve_limit()/build_products() 只读取自 corpus/limits.json，零手抄。"""
    specs = build_products(load_limits(), grade="3")["籼稻谷"]["items"]
    rows = []
    for item, value in _DEMO_PLAN.get(batch_id, []):
        s = specs[item]
        rows.append({
            "batch_id": batch_id, "product": "籼稻谷", "item": item, "value": value,
            "unit": s["unit"], "direction": s["direction"], "limit_value": s["limit"],
            "standard": s["standard"], "limit_source": s["limit_source"],
            "limit_expr": s.get("expr", ""),
        })
    return rows


def _extract_indicator(question: str) -> str:
    names = list(build_products(load_limits(), grade="3")["籼稻谷"]["items"])
    for n in names:
        if n in str(question):
            return n
    # 未命中已知指标：尽量还原用户真正问的那个词，拒答时才列得出「具体指标」
    cleaned = re.sub(r"DEMO-\d+", "", str(question), flags=re.IGNORECASE)
    segs = re.findall(r"[\u4e00-\u9fa5]{2,}", cleaned)
    if not segs:
        return "未收录指标"
    cand = max(segs, key=len).lstrip("的之")
    for tail in ("限值是多少", "限值多少", "是多少", "多少", "限值", "能收吗", "吗"):
        if cand.endswith(tail):
            cand = cand[:-len(tail)]
            break
    return cand.lstrip("的之") or "未收录指标"


def _clause_text(indicator: str) -> str:
    """只放命中的那一条条款原文 + 版本号，不塞整张表 / 整个语料库。"""
    for k, spec in build_products(load_limits(), grade="3")["籼稻谷"]["items"].items():
        if indicator and (indicator == k or indicator in k or k in indicator):
            return (f"{spec['standard']} 表1 第3等 质量要求·{k}：原文「{spec.get('expr')}」"
                    f"（单位 {spec['unit']}，出处 {spec['limit_source']}）；版本 {spec['standard']}")
    return NO_ANSWER


def _extract_batch_id(question: str) -> str:
    m = re.search(r"DEMO-\d+", str(question), re.IGNORECASE)
    return m.group(0).upper() if m else DEMO_BATCH_DEFAULT


# ------------------------------------------------------------------ 四个工具（判定全在代码里）

def lookup_limit(indicator: str = "出糙率", version: str = "GB 1350—2025") -> dict:
    """查语料：返回限值 + 方向 + 出处 + 版本；查不到直接拒答，不许推算。"""
    v = str(version or "GB 1350—2025").strip()
    key = _VERSION_ALIASES.get(v)
    if key is None:
        return _tool_result(False, True, 0.0, {"指标": indicator, "版本": v},
                            f"版本 {v} 的限值语料尚未收录（待人工核对）；{NO_ANSWER}", True)
    limits = load_limits()
    if key not in limits:
        return _tool_result(False, True, 0.0, {"指标": indicator, "版本": v}, NO_ANSWER, True)
    section = build_products(limits, grade="3")["籼稻谷"]["items"]
    name = str(indicator or "")
    hit = None
    for k, spec in section.items():
        if name and (name == k or name in k or k in name):
            hit = (k, spec)
            break
    if hit is None:
        return _tool_result(False, True, 0.0, {"指标": name, "版本": v},
                            f"指标「{name}」：{NO_ANSWER}", True)
    k, spec = hit
    return _tool_result(True, False, 0.95, {
        "指标": k, "限值": spec["limit"], "方向": spec["direction"],
        "出处": spec["limit_source"], "版本": spec["standard"],
        "条款原文": _clause_text(k),
    }, f"{k} {spec['direction']}{spec['limit']}（{spec['limit_source']}，版本 {spec['standard']}）")


def validate_record(batch_id: str = DEMO_BATCH_DEFAULT) -> dict:
    """P2 录入错误校验：只标「待人工确认」，不改数值、不改写 verdict。"""
    if not _is_demo(batch_id):
        return _tool_result(False, True, 0.0, {"batch_id": str(batch_id)},
                            "拒绝：harness 只接受 DEMO- 假批次", True)
    suspects = [s.to_dict() for s in validate_records(_demo_records(batch_id))]
    brief = (f"P2 录入自查：{len(suspects)} 条待人工确认"
             + ("（" + "；".join(s["item"] for s in suspects) + "）" if suspects else "，无疑点"))
    # 疑点不触发硬闸门，只降低置信度但仍 >=0.8，保证固定顺序能跑完 4 个工具
    return _tool_result(True, False, 0.85 if suspects else 0.95,
                        {"batch_id": batch_id, "疑点": suspects, "条数": len(suspects)}, brief)


def judge_batch(batch_id: str = DEMO_BATCH_DEFAULT) -> dict:
    """代码硬判定：models.compare() 逐项比大小并附引用；不产出放行结论。"""
    if not _is_demo(batch_id):
        return _tool_result(False, True, 0.0, {}, "拒绝：只接受 DEMO- 假批次", True)
    verdicts = []
    for r in _demo_records(batch_id):
        try:
            verdict = ("合格" if compare(float(r["value"]), float(r["limit_value"]),
                                         r["direction"]) else "不合格")
        except (TypeError, ValueError, KeyError):
            verdict = "无法判定，转人工"
        verdicts.append({
            "指标": r["item"], "实测值": r["value"], "方向": r["direction"],
            "限值": r["limit_value"], "verdict": verdict, "引用": r["limit_source"],
        })
    return _tool_result(True, False, 0.95, {
        "batch_id": batch_id, "逐项结论": verdicts,
        "说明": "判定由 models.compare() 在工具代码里完成；含录入疑点的项不得用于放行",
    }, f"代码判定完成：{len(verdicts)} 项（逐项结论与引用见 data）")


def build_report(batch_id: str = DEMO_BATCH_DEFAULT) -> dict:
    """生成 HTML 到 runs/<batch_id>/report.html；不写 qc_daily_data/reports/。"""
    if not _is_demo(batch_id):
        return _tool_result(False, True, 0.0, {}, "拒绝：只接受 DEMO- 假批次", True)
    out_dir = _SESSION.get("out_dir") or _runs_dir(batch_id)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "report.html")
    sections = _assemble_sections()
    with open(path, "w", encoding="utf-8") as f:
        f.write(_render_report(sections, batch_id))
    return _tool_result(True, False, 0.9,
                        {"path": path, "note": "harness 审计产物，非日报，不作为放行依据"},
                        f"HTML 报告已生成：{path}")


# ① 给代码的执行索引：只有这里登记过的工具才会被执行，无写外部系统 / 发邮件 / 改源数据工具
TOOLS_IMPL = {
    "lookup_limit": lookup_limit,
    "validate_record": validate_record,
    "judge_batch": judge_batch,
    "build_report": build_report,
}

# ② 给模型看的说明书：模型自己决定调哪个，但说明书里也写死「不许自己算数 / 下结论」
TOOLS = [
    {"type": "function", "function": {
        "name": "lookup_limit",
        "description": "查 corpus/limits.json 返回限值、方向、条款出处与版本；查不到会直接拒答，不得推算",
        "parameters": {"type": "object", "properties": {
            "indicator": {"type": "string", "description": "检测项目，如 出糙率 / 整精米率 / 水分含量"},
            "version": {"type": "string", "description": "标准版本，如 GB 1350—2025"}},
            "required": ["indicator", "version"]}}},
    {"type": "function", "function": {
        "name": "validate_record",
        "description": "跑 P2 录入错误校验，返回可疑数清单；只标待人工确认，不判合格/不合格",
        "parameters": {"type": "object", "properties": {
            "batch_id": {"type": "string", "description": "DEMO- 开头的假批次号"}},
            "required": ["batch_id"]}}},
    {"type": "function", "function": {
        "name": "judge_batch",
        "description": "由代码逐项判定并附引用；只准引用结论，严禁自行比较大小或重新判定",
        "parameters": {"type": "object", "properties": {
            "batch_id": {"type": "string", "description": "DEMO- 开头的假批次号"}},
            "required": ["batch_id"]}}},
    {"type": "function", "function": {
        "name": "build_report",
        "description": "生成三段式 HTML 审计报告到 runs/<batch_id>/，返回文件路径",
        "parameters": {"type": "object", "properties": {
            "batch_id": {"type": "string", "description": "DEMO- 开头的假批次号"}},
            "required": ["batch_id"]}}},
]


# ------------------------------------------------------------------ 留痕与上下文装配

def _write_trace(state: dict, step: int, tool: str, args: dict, brief: str) -> None:
    rec = {
        "step": step, "tool": tool,
        "args": {k: str(v)[:120] for k, v in (args or {}).items()},
        "result_brief": str(brief)[:300], "ts": _ts(),
    }
    with open(state["trace_path"], "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _build_context(question: str, batch_id: str, indicator: str, state: dict) -> list[dict]:
    """每次调模型前只拼三样：提问 / 本批次结构化摘要 / 命中条款原文+版本号。"""
    rows = _demo_records(batch_id)
    suspects = validate_records(rows)
    summary = {
        # 疑点前置：上下文超限时按开头保留，优先保住「待人工复核」的关键信息
        "批次号": batch_id,
        "已标出的疑点": [{"指标": s.item, "原因": s.reason} for s in suspects],
        "指标": [{"名称": r["item"], "实测值": r["value"]} for r in rows],
    }
    parts = [
        {"role": "user", "content": "提问：" + str(question)},
        {"role": "user", "content": "本批次结构化摘要：" + json.dumps(summary, ensure_ascii=False)},
        {"role": "user", "content": "命中标准条款原文 + 版本号：" + _clause_text(indicator)},
    ]
    before = sum(len(p["content"]) for p in parts)
    if before > MAX_CONTEXT_CHARS:
        per = max(80, MAX_CONTEXT_CHARS // 3)
        for p in parts:
            if len(p["content"]) > per:
                p["content"] = p["content"][:per] + "…（已按 MAX_CONTEXT_CHARS 截断）"
        state["context_truncated"] = True
        # 截断日志只写 runs/<batch_id>/trace.jsonl，绝不写 corpus/
        _write_trace(state, 0, "context_truncate",
                     {"max_context_chars": MAX_CONTEXT_CHARS, "before_chars": before},
                     f"上下文 {before} 字超过 {MAX_CONTEXT_CHARS}，已按三样等比截断并留痕")
    return parts


# ------------------------------------------------------------------ 主循环（闸门全在代码里）

def _call_one(name: str, args: dict, state: dict):
    """执行一个工具并做硬闸门判定；返回 (stop_reason 或 None, result)。"""
    if name not in TOOLS_IMPL:
        state["failed"] += 1
        _write_trace(state, state["step"], name, args,
                     f"错误：未知工具 '{name}'，可用工具 {list(TOOLS_IMPL)}")
        return ("consecutive_failures" if state["failed"] >= MAX_FAIL else None), None
    try:
        result = TOOLS_IMPL[name](**args)
    except Exception as e:
        state["failed"] += 1
        _write_trace(state, state["step"], name, args, f"工具执行失败（{type(e).__name__}）")
        return ("consecutive_failures" if state["failed"] >= MAX_FAIL else None), None
    state["failed"] = 0
    _SESSION["results"][name] = result
    state["tools_called"].append(name)
    _write_trace(state, state["step"], name, args, result["brief"])
    if result.get("reject"):
        return "rejected", result
    if result.get("safety") or float(result.get("confidence", 1.0)) < 0.8:
        return "safety_gate", result
    return None, result


def _execute_plan(plan: list, state: dict) -> str:
    for name, args in plan:
        if state["step"] >= MAX_STEPS:
            return "max_steps"
        state["step"] += 1
        stop, _ = _call_one(name, args, state)
        if stop:
            return stop
    return "done"


def _llm_session():
    """仅在开关开启且凭据可用时建立 client；否则返回 None 走降级。"""
    try:
        from openai import OpenAI
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if not key:
            return None
        return OpenAI(api_key=key, base_url="https://api.deepseek.com",
                      timeout=8.0, max_retries=0)
    except Exception as e:
        say(f"[harness] LLM 初始化失败（{type(e).__name__}）-> 自动退回固定顺序")
        return None


def _llm_enabled() -> bool:
    """是否允许调模型。请求级开关（前端 DeepSeek 按钮）优先于环境变量。"""
    try:
        from . import flags
        return flags.llm_enabled()
    except Exception:
        return os.getenv("QC_USE_LLM", "").strip().lower() not in ("", "0", "false", "off", "no")


def _llm_loop(client, question, batch_id, indicator, state) -> str | None:
    """模型决定调哪个工具；每次调模型前重新装配三样上下文。返回 None 表示应降级。"""
    for _ in range(MAX_STEPS):
        if state["step"] >= MAX_STEPS:
            return "max_steps"
        messages = [{"role": "system", "content": SYSTEM}] + \
                   _build_context(question, batch_id, indicator, state)
        try:
            resp = client.chat.completions.create(model="deepseek-chat",
                                                  messages=messages, tools=TOOLS)
        except Exception as e:
            say(f"[harness] 模型调用失败（{type(e).__name__}）-> 自动退回固定顺序")
            return None
        msg = resp.choices[0].message
        if not msg.tool_calls:
            say(f"[模型解释层 · 仅供参考，不是判定结论] {msg.content}")
            return "done"
        for tc in msg.tool_calls:
            if state["step"] >= MAX_STEPS:
                return "max_steps"
            state["step"] += 1
            try:
                args = json.loads(tc.function.arguments or "{}")
            except ValueError:
                args = {}
            stop, _ = _call_one(tc.function.name, args, state)
            if stop:
                return stop
    return "max_steps"


# ------------------------------------------------------------------ 三段输出 / 报告 / summary

def _assemble_sections() -> dict:
    results = _SESSION.get("results", {})
    confirmed: list[str] = []
    review: list[str] = []
    uncovered: list[str] = []

    look = results.get("lookup_limit", {})
    if look.get("ok") and not look.get("reject"):
        d = look["data"]
        confirmed.append(f"{d['指标']}：限值 {d['方向']}{d['限值']}；出处 {d['出处']}；版本 {d['版本']}")
    else:
        confirmed.append("无（限值未命中，未作任何判定）")
        review.append(f"【拒答】指标「{look.get('data', {}).get('指标', '未识别')}」："
                      f"{look.get('brief', NO_ANSWER)}；不许用记忆补数，转人工核对标准原文")

    suspect_items = {s["item"] for s in results.get("validate_record", {})
                     .get("data", {}).get("疑点", [])}
    for row in results.get("judge_batch", {}).get("data", {}).get("逐项结论", []):
        line = (f"{row['指标']}：实测 {row['实测值']} {row['方向']} 限值 {row['限值']}"
                f" → 代码判定「{row['verdict']}」；引用 {row['引用']}")
        if row["指标"] in suspect_items:
            review.append(f"{row['指标']}：该值存在录入疑点，代码结论不可用于放行 → {line}")
        else:
            confirmed.append(line)

    for s in results.get("validate_record", {}).get("data", {}).get("疑点", []):
        review.append(f"{s['item']}：{s['reason']}；建议 {s['建议动作']}")

    review.append("标准版本：当前执行 GB 1350—2009，限值尚未人工核对；"
                  "语料中 GB 1350—2025 仅作预置（2027-01-01 实施），不得用于判旧货")
    review.append("放行决策（能否收购/接收）：本 harness 只摆事实与待复核原因，"
                  "不给「能收/不能收」结论，需人工复核签字")

    uncovered.append("感官项「色泽、气味」：人工确认项，不参与数值判定")
    uncovered.append("其它等级（1/2/4/5 等）、粳稻谷表2、GB 1350—2009 限值：本次 DEMO 未覆盖")
    uncovered.append(f"数据范围：{DATA_SCOPE}")
    return {"已确认": confirmed, "待人工复核": review, "未覆盖": uncovered}


def _render_report(sections: dict, batch_id: str) -> str:
    def block(title: str, items: list) -> str:
        lis = "".join(f"<li>{escape(str(x))}</li>" for x in items) or "<li>（无）</li>"
        return f"<h2>{escape(title)}</h2><ul>{lis}</ul>"

    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>harness 审计报告 {escape(batch_id)}</title>"
        "<style>body{font-family:'Microsoft YaHei',sans-serif;max-width:860px;margin:24px auto;"
        "padding:0 16px;line-height:1.7}h1{color:#b81c22}h2{border-left:4px solid #b81c22;"
        "padding-left:8px;font-size:16px;margin-top:22px}li{margin:4px 0}"
        ".note{background:#fdfaf3;border:1px dashed #c2771a;padding:8px 12px;font-size:12px}</style>"
        "</head><body>"
        f"<h1>harness 审计报告 · {escape(batch_id)}</h1>"
        f"<div class='note'>{escape(DATA_SCOPE)}；生成时间 {escape(_ts())}。"
        "本报告为 harness 审计产物，非日报，不作为放行依据，需人工复核签字。</div>"
        + block("已确认", sections["已确认"])
        + block("待人工复核", sections["待人工复核"])
        + block("未覆盖", sections["未覆盖"])
        + "</body></html>"
    )


def _write_summary(state: dict, sections: dict, stop_reason: str,
                   llm_used: bool, report_path: str, summary_path: str) -> None:
    payload = {
        "batch_id": state["batch_id"], "question": state["question"],
        "data_scope": DATA_SCOPE, "llm_used": llm_used,
        "max_steps": MAX_STEPS, "steps": state["step"],
        "tools_called": state["tools_called"], "stop_reason": stop_reason,
        "context_truncated": state["context_truncated"],
        "max_context_chars": MAX_CONTEXT_CHARS, "sections": sections,
        "trace_path": state["trace_path"], "summary_path": summary_path,
        "report_path": report_path, "ts": _ts(),
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ 主入口

def run(question: str, batch_id: str = DEMO_BATCH_DEFAULT) -> dict:
    """主循环：最多 MAX_STEPS 步；关闭 LLM 时按固定顺序跑完 4 个工具。"""
    global _SESSION
    if not _is_demo(batch_id):
        return {"ok": False, "reject": True, "stop_reason": "non_demo_batch",
                "batch_id": str(batch_id), "sections": _assemble_sections(),
                "trace_path": "", "summary_path": "", "report_path": "", "llm_used": False}
    out_dir = _runs_dir(batch_id)
    os.makedirs(out_dir, exist_ok=True)
    trace_path = os.path.join(out_dir, "trace.jsonl")
    summary_path = os.path.join(out_dir, "summary.json")
    with open(trace_path, "w", encoding="utf-8"):
        pass                                  # 每轮清空，保证 trace 只记本次运行
    state = {"batch_id": batch_id, "question": question, "trace_path": trace_path,
             "step": 0, "failed": 0, "tools_called": [], "context_truncated": False}
    _SESSION = {"batch_id": batch_id, "question": question, "out_dir": out_dir,
                "trace_path": trace_path, "results": {}, "stop_reason": ""}

    indicator = _extract_indicator(question)
    _build_context(question, batch_id, indicator, state)   # 装配一次并留痕（超限时写截断日志）

    client = _llm_session() if _llm_enabled() else None
    stop_reason = None
    llm_used = False
    if client:
        stop_reason = _llm_loop(client, question, batch_id, indicator, state)
        llm_used = stop_reason is not None
    if not llm_used:
        if _llm_enabled():
            say("[harness] QC_USE_LLM 已开启但 LLM 不可用 -> 自动退回固定顺序（不联网、不中断）")
        plan = [
            ("lookup_limit", {"indicator": indicator, "version": "GB 1350—2025"}),
            ("validate_record", {"batch_id": batch_id}),
            ("judge_batch", {"batch_id": batch_id}),
            ("build_report", {"batch_id": batch_id}),
        ]
        stop_reason = _execute_plan(plan, state)

    sections = _assemble_sections()
    if stop_reason == "max_steps":
        sections["待人工复核"].append("已达最大步数，请人工复核")
    if stop_reason == "safety_gate":
        sections["待人工复核"].append("代码硬闸门触发（safety=True 或 confidence<0.8）：待人工复核")
    if stop_reason == "consecutive_failures":
        sections["待人工复核"].append("工具连续失败 2 次，代码强制停止：待人工复核")
    _SESSION["stop_reason"] = stop_reason

    report_path = _SESSION["results"].get("build_report", {}).get("data", {}).get("path", "")
    _write_summary(state, sections, stop_reason, llm_used, report_path, summary_path)
    return {
        "ok": True, "batch_id": batch_id, "question": question,
        "sections": sections, "stop_reason": stop_reason, "llm_used": llm_used,
        "steps": state["step"], "tools_called": state["tools_called"],
        "context_truncated": state["context_truncated"],
        "trace_path": trace_path, "summary_path": summary_path, "report_path": report_path,
        "data_scope": DATA_SCOPE,
    }


def run_harness(question: str) -> dict:
    """CLI 用的结构化入口：返回 dict，不复用/劫持 qa.run_qa 的 stdout 格式。"""
    batch_id = _extract_batch_id(question)
    if not _is_demo(batch_id):
        say("【拒绝】harness 只接受 DEMO- 假批次，真实批次与实测值不进仓库。")
        return {"ok": False, "reject": True, "reason": "non_demo_batch", "batch_id": batch_id}
    result = run(question, batch_id)
    say("=" * 60)
    say(f"harness 审计输出 ｜ 批次 {batch_id} ｜ 数据源 {DATA_SCOPE}")
    say("=" * 60)
    for title in ("已确认", "待人工复核", "未覆盖"):
        say(f"[{title}]")
        for line in result["sections"][title]:
            say(f"  - {line}")
    say(f"留痕：{result['trace_path']} ｜ {result['summary_path']}")
    if result.get("report_path"):
        say(f"报告：{result['report_path']}")
    say(f"终止原因：{result['stop_reason']} ｜ 步骤 {result['steps']}/{MAX_STEPS} "
        f"｜ LLM：{'启用' if result['llm_used'] else '关闭/降级'}")
    return result
