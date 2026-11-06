"""入口意图路由 · 把「问能力 / 问业务知识 / 闲聊」与「要判定」分开

设计原则（针对「你能干什么」被当成指标去查限值的事故）：
1. **硬闸门只管判定链，不管对话入口。** 拒答只针对「限值查不到」，
   不针对「听不懂问题」。
2. 判定链（harness.py）零改动；本模块只决定「要不要进判定链」。
3. 垂直度体现在**业务术语与出处**：能力清单写的是 7 项稻谷指标、
   ≥/≤ 方向、定等、GB 1350 表号，不是泛泛的「我能查数据」。
4. 知识问答必须带出处（corpus/ 文件名），查不到就明说未覆盖，不许编。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import knowledge as kb

_FALLBACK_INDICATORS = ["出糙率", "整精米率", "水分含量", "杂质含量",
                        "互混率", "黄粒米含量", "谷外糙米含量"]

_CAPABILITY = ("你能干什么", "你能做什么", "你会什么", "你能干嘛", "有什么功能",
               "有哪些功能", "支持什么", "怎么用", "如何使用", "使用方法",
               "你是谁", "介绍一下", "自我介绍", "能帮我", "帮助", "help",
               "能做什么", "功能", "能力")
_OUT_OF_SCOPE = ("讲个笑话", "写首诗", "天气", "翻译", "讲个故事", "聊聊天")
_GREET = ("你好", "您好", "hi", "hello", "在吗", "嗨", "早上好")
_VAGUE_BUSINESS = ("帮我看", "看一下", "怎么样", "这批", "看看", "分析一下",
                   "检查一下", "有问题吗", "有没有问题", "帮我查", "咋样")

_JUDGE_WORDS = ("合格", "达标", "超标", "能收", "收不收", "几等", "定等",
                "判定", "判一下", "能不能收", "放行", "合不合格")


def indicators() -> list[str]:
    """指标名从 limits.json 动态取，不手抄（避免语料更新后失同步）。"""
    try:
        from .limits_resolver import load_limits, build_products
        names: set[str] = set()
        for product in build_products(load_limits(), grade="3").values():
            names.update(str(k) for k in product.get("items", {}).keys())
        return sorted(names, key=len, reverse=True) or list(_FALLBACK_INDICATORS)
    except Exception:
        return list(_FALLBACK_INDICATORS)


def capability_answer() -> dict:
    """结构化能力清单：业务细节 + 边界 + 示例问法。"""
    return {
        "title": "我是 FoodQC-Agent：粮油原粮验收的判定型助手",
        "lines": [
            "【逐项判定】对 DEMO 假批次判稻谷 7 项：出糙率、整精米率、杂质含量、水分含量、"
            "黄粒米含量、谷外糙米含量、互混率。≥ 下限型与 ≤ 上限型分开判，"
            "方向判反会放行不合格原粮，所以比较大小全部由代码做。",
            "【定等】按 GB 1350 表 1（籼稻谷）/ 表 2（粳稻谷）逐档定位等级，默认 3 等。",
            "【答案必带出处】每条结论都带标准号 + 表号 + 条款/页；查不到限值就拒答，不许推算。",
            "【录入错误自查】5 类可疑数：值域越界、10 倍量级跳变、小数位异常、"
            "跨列逻辑（整精米率不可能大于出糙率）、重复或漏填。只标疑点、不改数。",
            "【三段式与留痕】输出「已确认 / 待人工复核 / 未覆盖」，"
            "每次运行写 runs/<batch>/trace.jsonl、summary.json、report.html。",
            "【知识库】corpus/ 下逐格核对的国标与方法笔记，可查看、可上传；"
            "上传先进「待核对区」，人工确认前不进检索与判定。",
        ],
        "boundary": [
            "不放行、不签字、不替代人工复核",
            "不发邮件、不改源数据、不接真实批次与实测值",
            "数据范围：DEMO-only 假批次；当前执行 GB 1350—2009，限值尚未人工核对",
            "⚠️ 所有结论仅供复核，不得作为放行依据",
        ],
        "examples": [
            "DEMO-001 这批稻谷出糙率 76.5 能收吗",
            "出糙率 3 等限值是多少，依据哪一条",
            "DEMO-002 水分 1.45 是不是敲错了",
            "整精米率和出糙率有什么区别",
        ],
    }


def knowledge_answer(question: str, terms: list[str] | None = None) -> dict:
    """业务知识问答：从 corpus/*.md 找命中段落，带文件名出处；查不到明说未覆盖。

    terms 可由 LLM 意图层提供（比关键词更懂同义问法）；不传则本地抽词。
    只做只读检索，不做生成结论，不改语料。
    """
    if terms is None:
        terms = [t for t in indicators() if t in question]
        stds = re.findall(r"GB\s*/?\s*T?\s*\d{3,5}", question, re.IGNORECASE)
        terms += [s.strip() for s in stds]
        extra = ("定等", "等级", "下限", "上限", "判定", "验收", "扦样", "杂质", "黄曲霉", "镉")
        terms += [e for e in extra if e in question]
    terms = [str(t).strip() for t in terms if str(t).strip()][:6]

    hits: list[str] = []
    sources: list[str] = []
    scored: list[tuple[int, str, str]] = []
    if terms:
        for path in sorted(kb.CORPUS.glob("*.md")):
            text = kb._decode(path.read_bytes())
            for para in kb.split_entries(text):
                if len(para) < 20 or not any(t in para for t in terms):
                    continue
                score = 2 * sum(1 for t in terms if t in para)
                if any(k in para for k in ("是指", "定义", "称为", "区别", "不同于",
                                           "定等指标", "质量要求", "术语")):
                    score += 3
                if para.count("|") > 3:      # 纯表格行降权，优先给定义性文字
                    score -= 2
                scored.append((score, para[:260].strip(), path.name))
        scored.sort(key=lambda x: -x[0])
        for _s, para, name in scored[:3]:
            hits.append(f"[{name}] {para}")
            if name not in sources:
                sources.append(name)

    if hits:
        return {
            "title": f"业务知识检索：{question}",
            "lines": hits,
            "boundary": [f"出处：{', '.join(sources)}（corpus/ 只读，未生成任何结论）",
                         "以上为语料片段摘录，限值数字仍须人工核对原文后方可用于判定"],
            "examples": ["DEMO-001 这批稻谷出糙率 76.5 能收吗"],
        }
    return {
        "title": "这个问题目前的语料还没覆盖",
        "lines": [
            "我查了 corpus/ 下的国标与方法笔记，没有找到对应片段，"
            "按「读不准就不写」的铁律不编答案。",
            "可以换个更具体的问法：指出指标名（如出糙率）、标准号（如 GB 1350）"
            "或批次号（DEMO-001）。",
        ],
        "boundary": ["知识库支持在左栏上传文件；新资料先进待核对区，人工确认后才可用",
                     "真实批次与实测值不进本系统"],
        "examples": capability_answer()["examples"],
    }


def route(question: str) -> dict | None:
    """返回 None = 交给 harness 判定链；否则返回可直接渲染的结构化回答。"""
    q = str(question or "").strip()
    if not q:
        return {"kind": "chat", "answer": {
            "title": "请输入一个问题",
            "lines": ["可以问我能力、业务知识，或者直接给一个 DEMO 批次让我判定。"],
            "boundary": [], "examples": capability_answer()["examples"]}}

    if any(k in q for k in _CAPABILITY):
        return {"kind": "chat", "answer": capability_answer()}

    if any(k in q for k in _OUT_OF_SCOPE):
        return {"kind": "chat", "answer": {
            "title": "这不在我的业务范围内",
            "lines": ["我是粮油原粮验收的判定型助手，只处理检验指标、标准条款、"
                      "录入自查和 DEMO 批次判定，不做闲聊、创作或通用问答。",
                      "这不是「拒答」——是我本来就不覆盖这类任务。"],
            "boundary": ["想看我能做什么，直接问「你能干什么」"],
            "examples": capability_answer()["examples"]}}

    if any(q.lower().startswith(g) or q.lower() == g for g in _GREET):
        return {"kind": "chat", "answer": {
            "title": "你好，我是 FoodQC-Agent",
            "lines": ["我负责粮油原粮（稻谷）验收的逐项判定、录入错误自查和标准条款检索。",
                      "可以直接给我一个 DEMO 假批次和指标数值，或者问「你能干什么」。"],
            "boundary": ["只用 DEMO 假数据；真实批次与实测值不进本系统"],
            "examples": capability_answer()["examples"]}}

    has_batch = bool(re.search(r"DEMO-\d+", q, re.IGNORECASE))
    has_indicator = any(i in q for i in indicators())
    has_number = bool(re.search(r"\d", q))
    wants_judge = any(w in q for w in _JUDGE_WORDS)

    # 明确要判定 → 判定链
    if has_batch and (has_indicator or wants_judge or has_number):
        return None
    if has_indicator and has_number:
        return None
    if wants_judge:
        return None

    # 开放式业务问法（没给批次也没给数值）：听懂意图，主动追问，不要回「语料未覆盖」
    if any(k in q for k in _VAGUE_BUSINESS):
        return {"kind": "chat", "answer": {
            "title": "我可以帮你判定，但还差一点信息",
            "lines": [
                "听起来你想看某批稻谷的检验结果。给我一个 DEMO 假批次号"
                "（DEMO-001 / 002 / 003）和具体指标数值，我就跑逐项判定 + 录入错误自查，"
                "并给出带 GB 出处的三段式结论。",
                "如果只想问术语或标准依据，直接说指标名，例如「出糙率 3 等限值是多少」。",
                "我不会对「能收吗」下放行结论——结论仅供复核，最终由人签字。",
            ],
            "boundary": ["真实批次号与实测值不能进本系统",
                         "当前执行 GB 1350—2009，限值尚未人工核对"],
            "examples": capability_answer()["examples"]}}

    # 其余（问术语、问区别、问依据、纯知识）→ 带出处的语料检索
    return {"kind": "chat", "answer": knowledge_answer(q)}


# ------------------------------------------------------------------ 可选 LLM 理解层

_ALLOWED_INTENTS = {"capability", "knowledge", "judge", "out_of_scope"}

_LLM_SYSTEM = """你是 FoodQC-Agent 的「入口意图识别器」。只判断用户想干什么，输出严格 JSON，不要解释。

允许的 intent 只有四个：
- capability：问你能干什么 / 怎么用 / 你是谁 / 有哪些功能
- knowledge：问业务知识、术语、标准条款、指标区别，但不需要对具体批次下判定
- judge：要求对某个 DEMO 批次做判定、定等、查限值、查录入错误
- out_of_scope：闲聊、创作、翻译、与粮油检验完全无关

输出格式（严格 JSON，不要 markdown 围栏）：
{"intent":"...","terms":["检索词1","检索词2"],"reply":"一句不超过60字的中文说明"}

铁律：
- 严禁输出任何限值数字、合格/不合格结论、定等结论。
- 严禁编造标准号、条款号。
- terms 只放用于在国标笔记里检索的名词，最多 6 个。
- 拿不准就输出 intent="knowledge"。
"""


def llm_route(question: str) -> tuple[str, dict | None]:
    """可选 LLM 意图层。返回 (action, payload)：

        ("judge", None)              → 交判定链
        ("chat", {"answer": ...})    → 直接渲染
        ("fallback", None)           → 静默退回规则路由（无 key / 未开 / 超时 / 异常）

    ⚠️ 模型只输出「白名单意图 + 检索词 + 一句人话」，绝不产出数字或结论；
       意图不在白名单、JSON 解析失败、任何异常，一律回退，不让模型碰判定。
    """
    if os.getenv("QC_USE_LLM", "").strip().lower() in ("", "0", "false", "off"):
        return "fallback", None
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        return "fallback", None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url="https://api.deepseek.com",
                        timeout=8.0, max_retries=0)
        resp = client.chat.completions.create(
            model="deepseek-chat", temperature=0.0,
            messages=[{"role": "system", "content": _LLM_SYSTEM},
                      {"role": "user", "content": question}])
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        name = str(data.get("intent", "")).strip().lower()
        if name not in _ALLOWED_INTENTS:
            return "fallback", None
        reply = str(data.get("reply", ""))[:200]
        terms = [str(x)[:40] for x in (data.get("terms") or [])][:6]

        if name == "judge":
            return "judge", None
        if name == "capability":
            ans = capability_answer()
            if reply:
                ans["lines"].insert(0, reply)
            return "chat", {"answer": ans}
        if name == "knowledge":
            ans = knowledge_answer(question, terms=terms or None)
            if reply:
                ans["title"] = reply[:80]
            return "chat", {"answer": ans}
        return "chat", {"answer": {
            "title": reply or "这不在我的业务范围内",
            "lines": ["我是粮油原粮验收的判定型助手，只处理检验指标、标准条款、"
                      "录入自查和 DEMO 批次判定，不做闲聊、创作或通用问答。",
                      "这不是「拒答」——是我本来就不覆盖这类任务。"],
            "boundary": ["想看我能做什么，直接问「你能干什么」"],
            "examples": capability_answer()["examples"],
        }}
    except Exception:
        return "fallback", None
