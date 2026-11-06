# -*- coding: utf-8 -*-
# qa.py：一句话问答入口，系统按问题自己选工具；判定结论由代码产出，模型只把结果翻译成人话。
# 输入：一句中文问题（cli: python -m qc_daily ask "..."）+ 环境变量 QC_USE_LLM（默认关闭）。
# 输出：打印工具清单与逐次调用过程、代码结论与原文出处，末尾固定提示「需人工复核签字」。
# 只读：不写源表/数据库、不重新判定、不打印任何凭据；LLM 不可用时自动退回纯代码模式。
from __future__ import annotations

import json
import os
from datetime import date, timedelta

from .config import QCDailyConfig
from .console import say
from .limits_resolver import build_products, load_limits
from .models import compare
from .validate import validate_records

#: 查不到就照抄 lookup_from_corpus.py 的拒答口径，不许推算
NO_ANSWER = "语料库中未找到相关内容，请人工核对标准原文，不得推算"
MAX_STEPS = 5                  # 步数上限，最多转 5 圈，防死循环
MAX_RETRY = 2                 # 允许模型自己补救的上限
DEMO_BATCH = "B-QA-DEMO-01"   # 演示批次（明确标注，不是公司真实表格）
_DEMO_NOTE = "内置演示数据（非公司真实表格，限值只读自 corpus/limits.json）"

SYSTEM = (
    "你是一名米粮/稻谷验收质检助手，只负责把「代码算出来的结果 + 查到的标准原文」翻译成人话。"
    "四条硬规则，任何情况下都不得违反："
    "① 不得自行心算或估算任何数值，所有数字必须来自工具返回；"
    "② 不得自行比较大小、不得自行判合格或不合格，判定结论只能引用 check_batch 返回的 verdict；"
    "③ 每个数字都必须能说出出处（标准号、表号/等级、原文页码），说不出出处就不要写这个数；"
    "④ 查不到就说查不到，并明确转人工，不得推算、不得补写、不得凭印象写限值或标准号。"
    "若工具返回错误，按错误提示里列出的可用工具修正后重试；仍取不到结果时，只回答"
    "'无法给出结论，请人工复核'。你的回答只是解释层，不是判定结论，也不替代人工签字。"
)


def _llm_enabled() -> bool:
    """QC_USE_LLM 默认关闭；只有 1/true/on/yes 才算开启。"""
    return os.getenv("QC_USE_LLM", "").strip().lower() not in ("", "0", "false", "off", "no")


def _default_date(config: QCDailyConfig) -> str:
    d = date.today()
    if config.report.get("report_for", "previous_day") == "previous_day":
        d -= timedelta(days=1)
    return d.isoformat()


class _Workflow:
    """纯代码主干：读表 → 校验 → 代码判定 → 出处 → 出报告。

    verdict 只在这里算一次并缓存；后面的工具全部只读缓存，不重新判定。
    """

    def __init__(self, config: QCDailyConfig, report_date: str):
        self.config = config
        self.date = report_date
        self.rows: list[dict] = []
        self.verdicts: dict[str, list[dict]] = {}
        self.suspects: list = []
        self.source_note = ""
        self.source_example = ""
        self.summary: dict = {}

    @staticmethod
    def _normalize(r: dict) -> dict:
        return {
            "batch_id": str(r.get("batch_id", "")), "product": str(r.get("product", "")),
            "item": str(r.get("item", "")), "value": r.get("value"),
            "unit": r.get("unit", ""), "direction": r.get("direction", ""),
            "limit_value": r.get("limit_value"), "standard": r.get("standard", ""),
            "limit_source": r.get("limit_source", ""),
            "verdict": r.get("verdict", ""),  # 快照里已由代码算好，绝不重算
            "tested_at": str(r.get("tested_at", "")),
        }

    def rows_for_date(self, d: str) -> list[dict]:
        """按日期取记录：优先缓存，否则只读已生成的原始快照；都不存在返回空表。"""
        if d == self.date:
            return self.rows
        return self._read_snapshot(d)

    def _read_snapshot(self, d: str) -> list[dict]:
        path = os.path.join(self.config.output_root, "raw", d, "quality.json")
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return [self._normalize(r) for r in raw] if isinstance(raw, list) else []
        except (OSError, ValueError):
            return []

    def _demo_rows(self) -> list[dict]:
        """演示行：限值全部经 resolve_limit()/build_products() 取自语料，零手抄。"""
        specs = build_products(load_limits(), grade="3")["籼稻谷"]["items"]
        plan = {
            DEMO_BATCH: {"出糙率": 76.0, "整精米率": 45.0, "水分含量": 13.2,
                         "杂质含量": 0.8, "互混率": 4.5},
            "B-QA-DEMO-02": {"出糙率": 74.0, "整精米率": 45.0, "水分含量": 145.0,
                             "杂质含量": 0.9, "互混率": 4.0},
        }
        rows = []
        for batch, vals in plan.items():
            for item, value in vals.items():
                s = specs[item]
                rows.append({
                    "batch_id": batch, "product": "籼稻谷", "item": item, "value": value,
                    "unit": s["unit"], "direction": s["direction"], "limit_value": s["limit"],
                    "standard": s["standard"], "limit_source": s["limit_source"],
                    "verdict": "", "tested_at": self.date + " 08:30:00",
                })
        return rows

    def run(self) -> None:
        say("[Workflow 1/5] 读表：" + (self._step_read()))
        self.suspects = validate_records(self.rows)
        say(f"[Workflow 2/5] 校验：P2 录入自查，待人工确认疑点 {len(self.suspects)} 条（不判合格/不合格）")
        self._step_adjudicate()
        say(f"[Workflow 3/5] 代码判定：models.compare() 硬判定并缓存 verdict，共 "
            f"{len(self.rows)} 项次 / {len(self.verdicts)} 批次（只算一次，工具不再重判）")
        self.source_example = next((r["limit_source"] for r in self.rows if r.get("limit_source")), "无")
        say(f"[Workflow 4/5] 出处：{self.source_example}")
        self.summary = {
            "日期": self.date, "数据来源": self.source_note,
            "质检项次": len(self.rows), "批次数": len(self.verdicts),
            "待复核疑点": len(self.suspects), "标准版本": self.config.standard_version,
        }
        say(f"[Workflow 5/5] 出报告：{json.dumps(self.summary, ensure_ascii=False)}")

    def _step_read(self) -> str:
        snap = self._read_snapshot(self.date)
        if snap:
            self.rows = snap
            self.source_note = f"已生成原始快照（只读）raw/{self.date}/quality.json"
        else:
            self.rows = self._demo_rows()
            self.source_note = _DEMO_NOTE
        return f"{len(self.rows)} 条质检记录，来源：{self.source_note}"

    def _step_adjudicate(self) -> None:
        for r in self.rows:
            verdict = r.get("verdict")
            if not verdict:
                try:
                    verdict = ("合格" if compare(float(r["value"]), float(r["limit_value"]),
                                                 r["direction"]) else "不合格")
                except (TypeError, ValueError, KeyError):
                    verdict = "无法判定，转人工"
            r["verdict"] = verdict
            self.verdicts.setdefault(r["batch_id"], []).append({
                "项目": r["item"], "实测值": r["value"], "方向": r["direction"],
                "限值": r["limit_value"], "verdict": verdict, "依据": r["limit_source"],
            })


class _Context:
    def __init__(self, config: QCDailyConfig, workflow: _Workflow):
        self.config = config
        self.workflow = workflow


_CTX: _Context | None = None


# ---------------------------------------------------------------- 四个工具（给代码执行）

def lookup_standard(item: str = "出糙率", product: str = "籼稻谷", grade: str = "3") -> str:
    """查语料：返回限值 + 标准号 + 条款出处；查不到一律拒答，不推算。"""
    if _CTX is None:
        return NO_ANSWER
    table_key = "表1" if "籼" in str(product) else ("表2" if "粳" in str(product) else None)
    if table_key is None:
        return NO_ANSWER
    try:
        products = build_products(load_limits(), grade=str(grade or "3"))
    except (KeyError, ValueError):
        return NO_ANSWER
    section = products["籼稻谷" if table_key == "表1" else "粳稻谷"]
    name = str(item or "")
    hit = None
    if name in section["items"]:
        hit = (name, section["items"][name])
    else:
        for k, v in section["items"].items():
            if name and (name in k or k in name):
                hit = (k, v)
                break
    if hit is None:
        for m in section["manual_checks"]:
            if name and (name in m["item"] or m["item"] in name):
                return json.dumps({"项目": m["item"], "标准号": m["standard"],
                                   "条款出处": m["limit_source"],
                                   "结论": "感官型，人工确认项，不参与数值判定，转人工"},
                                  ensure_ascii=False)
        return NO_ANSWER
    n, spec = hit
    return json.dumps({
        "项目": n, "产品": section["table"], "等级": str(grade or "3"),
        "标准号": spec["standard"], "限值": spec["limit"], "方向": spec["direction"],
        "限值原文": spec.get("expr"), "条款出处": spec["limit_source"],
        "版本状态": (f"GB 1350—2025 预置（未启用）；当前执行 {_CTX.config.standard_version}，"
                     f"限值待人工核对，不得用于放行"),
    }, ensure_ascii=False)


def check_batch(batch_id: str = "") -> str:
    """只读已算好的 verdict；禁止重新判定、禁止模型自己比大小。"""
    if _CTX is None:
        return "系统未就绪，无法给出结论，请人工复核"
    rows = _CTX.workflow.verdicts.get(str(batch_id or ""))
    if not rows:
        return f"未找到批次 [{batch_id}] 已由代码算好的结论，转人工复核（本工具不重新判定）"
    return json.dumps({
        "批次": str(batch_id), "结论": rows,
        "结论来源": "Workflow 中 models.compare() 已算好并缓存；本工具只读，不重新判定",
        "版本状态": (f"当前执行 {_CTX.config.standard_version}，限值待人工核对；"
                     f"结论仅供复核，不得作为放行依据"),
    }, ensure_ascii=False)


def list_suspects(date: str = "") -> str:
    """P2 校验层（validate.validate_records）标出的待人工复核清单。"""
    if _CTX is None:
        return "系统未就绪，无法给出结论，请人工复核"
    d = str(date or _CTX.workflow.date)
    rows = [r for r in _CTX.workflow.rows_for_date(d)
            if str(r.get("tested_at", "")).startswith(d)] if d != _CTX.workflow.date \
        else _CTX.workflow.rows
    suspects = validate_records(rows)
    if not suspects:
        return json.dumps({"日期": d, "待复核条数": 0, "结论": "今日无录入疑点",
                           "说明": "P2 校验层只标「待人工确认」，不判合格/不合格"}, ensure_ascii=False)
    return json.dumps({
        "日期": d, "待复核条数": len(suspects),
        "清单": [s.to_dict() for s in suspects],
        "说明": "全部待人工确认；不修改数值、不改写 verdict、不自动放行",
    }, ensure_ascii=False)


def summarize_report(date: str = "") -> str:
    """已生成日报的摘要：摘自己追加审计日志，不重新计算指标。"""
    if _CTX is None:
        return "系统未就绪，无法给出结论，请人工复核"
    d = str(date or _CTX.workflow.date)
    path = os.path.join(_CTX.config.output_root, "audit", "audit.jsonl")
    entries = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except ValueError:
                            continue
        except OSError:
            pass
    matched = [e for e in entries if e.get("report_date") == d]
    if not matched:
        return json.dumps({
            "日期": d, "已生成日报": "无",
            "说明": "该日期没有已生成日报；以下为本次纯代码 Workflow 摘要（非已发布日报，未重新计算指标）",
            "摘要": _CTX.workflow.summary,
        }, ensure_ascii=False)
    e = matched[-1]
    return json.dumps({
        "日期": d, "已生成日报": "有", "运行状态": e.get("status", ""),
        "记录数": e.get("record_counts", {}), "预警条数": len(e.get("alerts", [])),
        "说明": "摘自只追加审计日志，未重新计算指标；详细结论见对应日期的 HTML 日报",
    }, ensure_ascii=False)


# ① 给代码的执行索引：模型报出工具名，只有这里登记过的才会被执行
TOOLS_IMPL = {
    "lookup_standard": lookup_standard,
    "check_batch": check_batch,
    "list_suspects": list_suspects,
    "summarize_report": summarize_report,
}

# ② 给模型的说明书：模型据此自己决定调哪个，但说明书里也写死「不许自己判定」
tools = [
    {"type": "function", "function": {
        "name": "lookup_standard",
        "description": "查 corpus/limits.json 的稻谷质量要求，返回限值、标准号与条款出处；查不到会明确拒答，不得推算",
        "parameters": {"type": "object", "properties": {
            "item": {"type": "string", "description": "检测项目，如 出糙率 / 整精米率 / 水分含量"},
            "product": {"type": "string", "description": "产品，籼稻谷 对应表1，粳稻谷 对应表2"},
            "grade": {"type": "string", "description": "等级 1-5，默认 3（中等）"}},
            "required": ["item", "product", "grade"]}}},
    {"type": "function", "function": {
        "name": "check_batch",
        "description": "读取该批次已由代码算好的 verdict 与依据；只准引用，严禁自行比较大小或重新判定",
        "parameters": {"type": "object", "properties": {
            "batch_id": {"type": "string", "description": "批次号"}},
            "required": ["batch_id"]}}},
    {"type": "function", "function": {
        "name": "list_suspects",
        "description": "列出某日 P2 录入自查层标出的待人工复核清单；这些都是疑点，不是合格/不合格结论",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "日期 YYYY-MM-DD"}},
            "required": ["date"]}}},
    {"type": "function", "function": {
        "name": "summarize_report",
        "description": "返回某日已生成日报的摘要；没有就是没有，不得由现算数字编一份摘要",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "日期 YYYY-MM-DD"}},
            "required": ["date"]}}},
]


# ---------------------------------------------------------------- 模型解释层（可选）

def _run_llm(question: str) -> bool:
    """返回是否走通了 LLM；False = 已自动退回纯代码模式（不中断流程）。"""
    try:
        from openai import OpenAI
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if not key:
            say("[LLM] 开关已开启，但未检测到可用凭据 -> 自动退回纯代码模式（不影响演示）")
            return False
        client = OpenAI(api_key=key, base_url="https://api.deepseek.com",
                        timeout=8.0, max_retries=0)
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": question}]
        failed = 0
        for step in range(MAX_STEPS):
            resp = client.chat.completions.create(model="deepseek-chat",
                                                  messages=messages, tools=tools)
            msg = resp.choices[0].message
            if msg.tool_calls:
                messages.append(msg)
                for tc in msg.tool_calls:
                    name = tc.function.name
                    if name not in TOOLS_IMPL:
                        result = f"错误, 未知工具 '{name}', 可用工具: {list(TOOLS_IMPL)}"
                        failed += 1
                    else:
                        try:
                            args = json.loads(tc.function.arguments or "{}")
                            result = TOOLS_IMPL[name](**args)
                        except Exception as e:
                            result = f"工具执行失败（{type(e).__name__}）。请换个参数重试。"
                            failed += 1
                    say(f"[第{step + 1}步] 调 {name}({tc.function.arguments}) = {result}")
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": str(result)})
                # ★ 硬闸门：工具连续失败超限，代码直接短路，不给模型说话的机会
                if failed >= MAX_RETRY:
                    say("最终答案: 【系统拦截】工具连续失败，无法给出结论，请人工复核。")
                    return True
                continue
            say(f"[模型解释层 · 仅供参考，不是判定结论] {msg.content}")
            return True
        say("[LLM] 已打最大步数仍未结束，强制停止（防死循环）")
        return True
    except Exception as e:
        # 只报异常类型，绝不打印异常正文/URL/凭据
        say(f"[LLM] 调用失败（{type(e).__name__}）-> 自动退回纯代码模式（不影响演示）")
        return False


# ---------------------------------------------------------------- 纯代码主干（默认）

def _choose_tools(question: str) -> list[tuple[str, dict]]:
    """没有模型时，由代码按问题关键词决定查什么；涉及判定就连带查疑点与日报。"""
    q = str(question)
    d = _CTX.workflow.date
    picks: list[tuple[str, dict]] = []
    if any(k in q for k in ("限值", "标准", "等", "判", "为什么", "多少", "出糙",
                            "整精米", "水分", "杂质", "合格", "等级")):
        picks += [("lookup_standard", {"item": "出糙率", "product": "籼稻谷", "grade": "3"}),
                  ("check_batch", {"batch_id": DEMO_BATCH})]
    if any(k in q for k in ("疑点", "敲错", "复核", "录入", "错", "待确认")):
        picks.append(("list_suspects", {"date": d}))
    if any(k in q for k in ("日报", "报告", "摘要", "汇总", "概览", "今天")):
        picks.append(("summarize_report", {"date": d}))
    if not picks:
        picks = [("lookup_standard", {"item": "出糙率", "product": "籼稻谷", "grade": "3"}),
                 ("check_batch", {"batch_id": DEMO_BATCH})]
    # 凡涉及判定，必须先看数有没有敲错，避免拿错数下结论
    for name, kwargs in [("list_suspects", {"date": d}),
                         ("summarize_report", {"date": d})]:
        if name not in [n for n, _ in picks]:
            picks.append((name, kwargs))
    return picks


def _run_code_route(question: str) -> None:
    say("[纯代码模式] 由代码按问题关键词选工具（无模型参与，工具调用过程如下）")
    for name, kwargs in _choose_tools(question):
        result = TOOLS_IMPL[name](**kwargs)
        say(f"[工具] {name}({json.dumps(kwargs, ensure_ascii=False)})")
        say(f"       -> {result}")


def _print_code_conclusion() -> None:
    """无论 LLM 开不开，最终结论都由代码打印，模型回答永远只是解释层。"""
    w = _CTX.workflow
    say("[代码结论 · 判定权在代码，模型只做翻译]")
    say(f"  日期 {w.date} ｜ 数据来源 {w.source_note} ｜ 质检项次 {len(w.rows)} "
        f"｜ 待复核疑点 {len(w.suspects)}")
    for batch, items in w.verdicts.items():
        brief = "；".join(f"{i['项目']}={i['verdict']}" for i in items)
        say(f"  {batch}: {brief}")
    say("  以上 verdict 由 models.compare() 在 Workflow 中一次性算出；LLM 未参与判定。")
    if w.config.standard_warning and not w.config.active_limits_verified:
        say("  " + w.config.standard_warning)
    say("  ⚠️ 结论仅供复核，不得作为放行依据；需人工复核签字，模型不替代签字。")


def run_qa(question: str) -> int:
    """CLI 入口：返回 0 表示链路跑通（无论是否启用 LLM）。"""
    global _CTX
    config = QCDailyConfig.load()
    workflow = _Workflow(config, _default_date(config))
    _CTX = _Context(config, workflow)

    say("=" * 60)
    say("米粮问答入口 · 系统自己选工具，判定权在代码")
    say("=" * 60)
    say(f"问题: {question}")
    say(f"工具清单（已登记 {len(TOOLS_IMPL)} 个）: {', '.join(TOOLS_IMPL)}")
    say(f"LLM 开关 QC_USE_LLM: {'开启' if _llm_enabled() else '关闭（默认）'}")
    say("-" * 60)
    workflow.run()
    say("-" * 60)

    used_llm = False
    if _llm_enabled():
        used_llm = _run_llm(question)
        if not used_llm:
            say("[退回] 已自动退回纯代码模式，继续完整演示（流程不中断）")
    else:
        say("[主干] QC_USE_LLM 未设置/关闭：只跑纯代码 Workflow"
            "（读表→校验→代码判定→出处→出报告）")
    if not used_llm:
        _run_code_route(question)

    say("-" * 60)
    _print_code_conclusion()
    return 0
