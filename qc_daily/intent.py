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
# 技术类越界与闲聊走同一条路。⚠️ 不能裸加「代码」二字：
# 判定行文案里有「代码判定「合格」」，是业务词，裸加会把正常问答也拦掉
# （夜测 11-10 r03：问「帮我写一段 Python 爬虫代码」被答成「我没找到资料」）。
_OUT_OF_SCOPE_RE = re.compile(
    r"(写|编|搞|来|给)[^。？!！]{0,8}(代码|爬虫|脚本|程序|网页|网站|软件)"
    r"|(爬虫|Python|python|SQL|sql|写程序|做网站|做网页|前端|后端|翻译一段)")
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
            "数据范围：DEMO-only 假批次；当前执行 GB 1350—2009，限值已逐格核对并经人工复核",
            "⚠️ 所有结论仅供复核，不得作为放行依据",
        ],
        "examples": [
            "DEMO-001 这批稻谷出糙率 76.5 能收吗",
            "出糙率 3 等限值是多少，依据哪一条",
            "DEMO-002 水分 1.45 是不是敲错了",
            "整精米率和出糙率有什么区别",
        ],
    }


_CONCEPT_WORDS = ("区别", "是什么", "定义", "什么意思", "不同", "差异", "含义")

# 概念性解释写死在代码里，是人工核对过的固定文本，不让模型生成、也不冒充语料摘录。
# 语料 corpus/ 里这两个指标只有限值表格，没有定义性段落，检索器拼不出「区别」。
_CONCEPT_NOTES = [
    {
        "keys": ("整精米率", "出糙率"),
        "title": "整精米率和出糙率，差在「有没有碾白、米粒还完不完整」",
        "paras": [
            "出糙率：稻谷脱掉稻壳之后，糙米占原粮的重量比例。它回答的是「这批谷能出多少米」。",
            "整精米率：继续碾磨成白米之后，仍然完整的米粒占原稻谷的比例。它回答的是「加工完还剩多少完整米」。",
            "因为多了一道碾白、中间一定会产生碎米，整精米率不可能大于出糙率。两个数差得越大，碎米越多。"
            "录入自查里「整精米率比出糙率还高」会被标成疑点，就是这个道理。",
        ],
        "boundary": [
            "以上是写在代码里的通用概念说明，不是语料摘录，也不是限值依据",
            "检验方法标准：GB/T 5495（出糙率）、GB/T 21719（整精米率），见 corpus/米粮标准清单_待核对.md",
            "具体限值仍须人工核对标准原文后方可用于判定",
        ],
        "examples": ["出糙率 3 等限值是多少，依据哪一条",
                     "DEMO-003 整精米率 80 比出糙率 72 还高，可疑吗"],
    },
]


def _concept_note(question: str) -> dict | None:
    """概念问答先走固定说明；没命中就交回检索，不让表格行凑答案。"""
    q = str(question or "").strip()
    if not any(w in q for w in _CONCEPT_WORDS):
        return None
    for note in _CONCEPT_NOTES:
        if all(k in q for k in note["keys"]):
            return {
                "title": note["title"],
                "lines": note["paras"],
                "boundary": note["boundary"],
                "examples": note["examples"],
                "prose": True,
            }
    return None


def _table_cells(line: str) -> str:
    """单行 markdown 表格 → 能读的一句话；丢掉 --- 分隔单元格和加粗符号。"""
    cells = []
    for c in line.split("|"):
        c = c.strip().replace("**", "").strip()
        if not c or all(ch in "-: " for ch in c):
            continue
        cells.append(c)
    return " · ".join(cells[:8]) if cells else ""


#: 指标别名：问题里常写元素符号，语料里写中文名。不映射就永远命中不了，
#: 然后被无关段落顶上来（2026-11-07 实例：问「Cd」答成「油脂铅」）。
_ALIAS = {"cd": "镉", "pb": "铅", "hg": "汞", "as": "砷", "cr": "铬",
          "afb1": "黄曲霉", "黄曲霉毒素b1": "黄曲霉", "黄曲霉毒素b₁": "黄曲霉"}
#: 污染物词：只要问题里出现，答案就必须真的提到它，不许拿别的污染物顶
_CONTAMINANTS = ("镉", "铅", "无机砷", "总砷", "砷", "汞", "铬", "黄曲霉毒素", "黄曲霉",
                 "苯并", "溶剂残留", "农药残留")
#: 物类词：问大米不许答油脂
_CATEGORY = ("大米", "稻谷", "糙米", "粳米", "籼米", "米制品", "谷物", "小麦", "玉米",
             "大豆", "植物油", "油脂", "花生油", "菜籽油", "食用植物油")

#: 业务范围边界：这些品类「查不到限值」不是「还没补国标」，是压根不归这套系统判。
#: 出处：corpus/米粮标准清单_待核对.md「七、业务范围边界」（内部业务事实，非标准原文）。
#: 玉米 / 大豆由小崔 2026-11-11 口述确认：厂里有，但属油化板块榨油原料，不是米粮部原粮。
_BUSINESS_SCOPE = {"玉米": "油化", "大豆": "油化", "小麦": "不做"}

#: 全品类限量表里常见的「与所问物类无关」的食品类别词。命中 ≥3 个就当整表处理，
#: 不把 PDF 压平、栏位错位的原文整段贴给人（夜测 2026-11-11 r02 一般②）。
_FOREIGN_TABLE_CATS = ("水产动物", "鱼类", "新鲜蔬菜", "食用菌", "松茸", "木耳",
                       "肉类", "乳类", "蛋类", "水果", "坚果", "饮料", "调味品",
                       "婴幼儿", "藻类", "贝类", "甲壳类", "罐头", "焙烤")


def _business_scope_answer(cat: str) -> dict:
    """「不归这套系统判」和「还没补国标」是两回事，别再劝人去补一份不该补的标准。

    夜测 2026-11-11 r02 一般①：小麦的拒答把「不在业务范围」说成「库里还没收」，
    还建议把对应国标逐格核对进 corpus/ —— 指着一条不该走的补料路线。
    """
    src = "corpus/米粮标准清单_待核对.md「七、业务范围边界」"
    if _BUSINESS_SCOPE[cat] == "油化":
        title = f"{cat}不归本系统的收录范围（属油化板块）"
        lines = [
            f"厂里确实有{cat}，但那是油化那边榨油用的原料，不在米粮部原粮验收范围内；"
            f"本系统收录的是稻谷原粮验收与大米成品检验这套标准（业务范围见 {src}）。",
            "所以这不是「库里少了一份国标」，也不是换个问法就能查到——"
            "这条不该往 corpus/ 补，补了也不会有人拿它判货。",
            f"要问{cat}的验收限值，请找油化那边的标准或对口的人；"
            "我能查的是 GB 1350 稻谷原粮这套限值。",
        ]
    else:
        title = f"{cat}不在本厂化验室的检验范围"
        lines = [
            f"化验室不测{cat}和面粉，这不在本厂检验范围内（{src} 记着 2026-10-16 的答复）；"
            "GB/T 10361 那类标准只是流程上有参考意义，归 L3 参考层，不是本厂检验项目。",
            "所以这项不用补国标进 corpus/——补了也不会有人拿它判货。",
            "若只想了解方法，我可以说流程性标准放在哪；要限值判定，本系统不给。",
        ]
    return {
        "title": title,
        "lines": lines,
        "boundary": [
            "已收录：GB 1350 稻谷质量要求、GB 2716/2761/2762 已核对条目、检验方法与术语",
            "范围外：玉米 / 大豆（油化榨油原料，非米粮部原粮验收）、小麦 / 面粉（本厂化验室不测）",
            "待补：大米成品指标（GB/T 1354—2018）、GB 2762 尚未逐格核对的污染物限值",
            "真实批次与实测值不进本系统",
        ],
        "examples": capability_answer()["examples"],
    }


def _present_para(para: str) -> str:
    """非表格段落：先洗掉 markdown 记号，再截断。

    语料是笔记体，`**强调**`、`> 引用`、`- 列表` 到处都是；原样吐给用户就是一屏星号。
    """
    p = para.strip()
    if "```" in p:
        p = p.split("```")[0].strip()
        if len(p) < 20:
            return ""
    if p.count("|") > 3:
        return _table_cells(p)
    for _ in range(3):                      # 多层引用 `> > `
        p = re.sub(r"^\s*>\s?", "", p, flags=re.M)
    p = re.sub(r"^\s*#{1,6}\s*", "", p, flags=re.M)
    p = re.sub(r"^\s*[-*+]\s+", "· ", p, flags=re.M)
    p = re.sub(r"\*\*(.+?)\*\*", r"\1", p)
    p = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", p)
    p = re.sub(r"`([^`]*)`", r"\1", p)
    p = re.sub(r"\s*\n\s*", " ", p).strip()
    if len(p) <= 260:
        return p
    # 硬切 260 字会切在半个词上（夜测 2026-11-11 r02：「木耳」被切成「木」，
    # 尾巴还拖着半句「4. 4.」）。退到最近的句读或空格，并标明原文未完。
    cut = p[:260]
    for sep in ("。", "；", ";", "，", " "):
        pos = cut.rfind(sep)
        if pos >= 180:          # 别为了找句读把整段砍到只剩一句
            cut = cut[:pos + 1] if sep in ("。", "；", ";") else cut[:pos]
            break
    return cut.rstrip("，, ") + "…（原文未完，出处见下）"


def _must_entities(question: str) -> tuple[list[str], list[str]]:
    """问题要求答案必须回扣的实体：([指标/污染物], [物类])。

    命中不了就别给答案 —— 宁可拒答，不可答非所问。
    """
    q = str(question or "").lower()
    ents: list[str] = []
    for alias, cn in _ALIAS.items():
        if alias in q and cn not in ents:
            ents.append(cn)
    ents += [c for c in _CONTAMINANTS if c in question and c not in ents]
    ents += [t for t in indicators() if t in question and t not in ents]
    # 「水分」「杂质」这类简称：指标名去掉量纲后缀后再比一次。
    # 不这么做，「大米的水分限值是多少」识别不出指标，掉进通用兜底，
    # 而「大米水分含量限值是多少」走的是「库里没有这条限值」——
    # 同一个问题两种说法（夜测 11-10 r02，4/4 分叉）。
    for t in indicators():
        for suf in ("含量", "率", "量"):
            stem = t[:-len(suf)] if t.endswith(suf) else ""
            if stem and stem in question and t not in ents:
                ents.append(t)
                break
    cats = [c for c in _CATEGORY if c in question]
    # 「糙米」是「谷外糙米含量」的子串：问「谷外糙米限值是多少」会被当成问糙米，
    # 反问话术里凭空多出「（糙米）」。指标名里裹着的字不算独立物类。
    for ind in indicators():
        stem = ind
        for suf in ("含量", "率", "量"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        if stem and stem in question:
            cats = [c for c in cats if c not in stem]
    return ents, cats


def _ent_in(ent: str, text: str) -> bool:
    """实体命中判定：中文词直接判，别名（Cd↔镉）两边都认。"""
    if ent in text:
        return True
    for alias, cn in _ALIAS.items():
        if cn == ent and alias in text.lower():
            return True
    return False


def _version_notice() -> str:
    """命中 GB 1350 语料时，把「当前执行哪一版」说清。

    2026-11-11 切版后，当前执行版 GB 1350—2009 的限值已逐格核对入库并经人工复核；
    语料池里仍留着 2025 版原文归档（预置、2027-01-01 才实施），检索可能命中它。
    不给这句，答案贴着 2025 的数却没说它不能用来判今天的货（夜测 11-10 严重②）。
    """
    try:
        from .config import QCDailyConfig
        c = QCDailyConfig.load()
        active = str(getattr(c, "standard_version", "") or "").strip()
        preset = [k for k, v in (getattr(c, "standard_versions", None) or {}).items()
                  if isinstance(v, dict) and v.get("role") == "preset"]
        if not active and not preset:
            return ""
        tail = ("；" + "、".join(preset) + " 为预置、未到实施日期，不得用于判旧货") if preset else ""
        return f"版本口径：当前执行 {active}（限值已逐格核对并经人工复核）{tail}"
    except Exception:
        return ""          # 配置读不到就别硬塞，宁可少一句也不编


def _mixed_quality_tables(question: str, hits: list[str]) -> bool:
    """GB 1350 的表1（籼）与表2（粳）被整节糊上来 → 低置信度命中。

    实测「稻谷水分限值是多少」一次返回 575 字、7 个指标全部数字，
    里面同时有 ≤13.5（表1）与 ≤14.5（表2）。7 个 must_contain 全被喂饱，
    脚本记满分，人却拿不到能用的答案（DECISIONS_2026-11-10 §0 勘误）。
    """
    if not any(w in question for w in ("限值", "多少", "合格线", "几等", "限量", "上限", "下限")):
        return False
    blob = "\n".join(hits)
    return ("表1" in blob and "表2" in blob
            and "出糙率" in blob and "水分含量" in blob)


def _clarify_answer(question: str, must: list[str], cats: list[str]) -> dict:
    """同一指标两张表、问题没说清物类 → 反问；说清了 → 给当前执行版那一格。

    2026-11-11 切版：GB 1350—2009 限值已逐格核对入库并经人工复核，
    「确认后我再给」这句空头支票现在有地方兑现了。数值取自 corpus/limits.json
    的结构化条目（build_products），不取自可能被 2025 原文归档污染的 md 片段。
    分不清籼粳时仍不并列贴数、不替人挑一个（DECISIONS_2026-11-10 §1）。
    """
    item = "、".join(dict.fromkeys(must))[:24] or "这个指标"
    cat = f"（{cats[0]}）" if cats else ""

    cultivar = "粳" if "粳" in question else ("籼" if "籼" in question else "")
    if cultivar:
        product, table = ("粳稻谷", "表2") if cultivar == "粳" else ("籼稻谷", "表1")
        try:
            from .limits_resolver import build_products, load_limits
            items = build_products(load_limits(), grade="3")[product]["items"]
            key = next((m for m in must if m in items), None)
            if key is None:
                key = next((t for t in indicators() if t in question and t in items), None)
            if key:
                spec = items[key]
                return {
                    "title": f"{product}「{key}」限值 · {spec['standard']} {table}",
                    "lines": [
                        f"{key}：{spec['expr']}{spec['unit']}（{table}，3 等口径）。",
                        f"出处：{spec['limit_source']}。",
                    ],
                    "boundary": [b for b in [
                        _version_notice(),
                        "数值取自 corpus/limits.json → GB1350-2009 结构化条目，已逐格核对；语料片段摘录仍以原文为准",
                        "真实批次与实测值不进本系统",
                    ] if b],
                    "examples": ["出糙率 3 等限值是多少", "把这张表做成今天的日报"],
                }
        except Exception:
            pass          # 结构化限值取不到就落回反问，绝不编数

    return {
        "title": f"先确认物类，再给「{item}」{cat}的限值",
        "lines": [
            f"GB 1350 里「{item}」分两张表：表1 是早籼 / 晚籼 / 籼糯稻谷，"
            "表2 是粳稻谷、粳糯稻谷，部分指标两表数值不同、部分相同。",
            "你验的是籼稻还是粳稻？请把品种连着指标一起问，例如「籼稻谷水分限值是多少」，"
            "我就给当前执行版对应那一格。",
            "现在不并列贴两个数，也不替你挑一个 —— 挑错表就是拿错限值，"
            "这恰恰是最不该由系统代做的判断。",
        ],
        "boundary": [b for b in [
            "检索到的原文同时含表1与表2，属低置信度命中，按四条原则转人工确认",
            _version_notice(),
            "真实批次与实测值不进本系统",
        ] if b],
        "examples": ["先确认这批来料是籼稻还是粳稻",
                     "查化验室手册里这批的品种与定等依据",
                     "问班长当前执行的是 GB 1350 哪一版"],
    }


def knowledge_answer(question: str, terms: list[str] | None = None) -> dict:
    """业务知识问答：概念问法给固定人话，其余从 corpus/*.md 找命中段落并带出处。

    只读检索，不生成结论，不改语料。表格行不再原样贴给用户。
    """
    note = _concept_note(question)
    if note:
        return note

    if terms is None:
        terms = []
        for ind in indicators():
            if ind in question:
                terms.append(ind)
                continue
            # 「水分含量」对「水分」、「整精米率」对「整精米」：去掉量纲后缀再试一次，
            # 否则用户说「稻谷水分限值」永远命中不了「水分含量」这个键。
            for suf in ("含量", "率", "量"):
                if ind.endswith(suf) and ind[:-len(suf)] and ind[:-len(suf)] in question:
                    terms.append(ind)
                    break
        stds = re.findall(r"GB\s*/?\s*T?\s*\d{3,5}", question, re.IGNORECASE)
        terms += [s.strip() for s in stds]
        extra = ("定等", "等级", "下限", "上限", "判定", "验收", "扦样", "杂质")
        terms += [e for e in extra if e in question]
        # 污染物必须进检索词，否则问「铅」时 terms 为空、直接掉进拒答分支
        terms += [c for c in _CONTAMINANTS if c in question]
        terms = list(dict.fromkeys(terms))
    must, cats = _must_entities(question)
    terms = [str(t).strip() for t in terms if str(t).strip()]
    # 别名映射出来的中文词（Cd→镉 / Pb→铅）必须一起进检索词，
    # 否则用户写元素符号时 terms 为空、直接掉进拒答分支（2026-11-07 实测踩到）。
    terms = list(dict.fromkeys(terms + must))
    # 必须命中的实体排前面，别被 [:8] 截掉（污染物词往往排在最后）
    terms = ([t for t in terms if t in must] + [t for t in terms if t not in must])[:8]

    hits: list[str] = []
    sources: list[str] = []
    scored: list[tuple[int, str, str]] = []
    if terms:
        for path in sorted(kb.CORPUS.glob("*.md")):
            # ⚠️ 2026-11-11 切版：GB 1350—2025 原文归档是 2027-01-01 才实施的预置版，
            # 它的表行里谷外糙米写 ≤4.0，与当前执行版 2009 的 ≤2.0 冲突。放它进检索池，
            # 就会贴出「同一张卡里 2.0 与 4.0 并列」，甚至按新版判旧货（11-10 严重①）。
            # 故整份排除出问答检索；文件仍留在 corpus/ 供 2027 换版与历史追溯，
            # knowledge.scan_builtin() 也照常列出它。
            if "GB1350-2025" in path.name:
                continue
            text = kb._decode(path.read_bytes())
            # 文件级硬闸门：整份文件里都没有问题问的那个指标/污染物，
            # 这份文件的任何段落都不许拿来答（否则就会出现「问镉答铅」）。
            if must and not any(_ent_in(m, text) for m in must):
                continue
            for para in kb.split_entries(text):
                if len(para) < 20 or not any(t in para for t in terms):
                    continue
                # 物类闸门：问大米不许拿油脂的段落顶
                # 例外：整份文件就是讲这个物类时（文件名带「稻谷」），段落里不会逐条重复写，
                # 不能按段落卡——否则 GB 1350 整份被自己过滤掉，问「稻谷水分限值」直接拒答
                if cats and not any(c in para for c in cats):
                    if not any(c in path.name for c in cats):
                        continue
                # 表格块按行拆，只留真正命中的那几行，别把整张表糊给用户
                if para.count("|") > 3:
                    candidates = []
                    for ln in para.splitlines():
                        if "|" not in ln or not any(t in ln for t in terms):
                            continue
                        clean = _table_cells(ln)
                        # 整行分等矩阵（≥79.0 · ≥77.0 · ≥75.0 …）去掉了表头就成一串数字，
                        # 读起来毫无意义；这种行不当作答案，让下面的说明段落顶上
                        if clean and sum(clean.count(c) for c in ("≥", "≤")) >= 4:
                            continue
                        if clean:
                            candidates.append(clean)
                    candidates = candidates[:2]
                else:
                    clean = _present_para(para)
                    candidates = [clean] if clean else []
                for cand in candidates:
                    score = 2 * sum(1 for t in terms if t in cand)
                    if any(k in cand for k in ("是指", "定义", "称为", "区别", "不同于",
                                               "定等指标", "质量要求", "术语")):
                        score += 3
                    # 问限值时，带 ≥/≤ 的表行优先，别拿版本说明来凑答案
                    if any(ch in cand for ch in ("≥", "≤", ">=", "<=")):
                        score += 5
                    # 命中所问物类（大米 / 油脂）的行再往前排，
                    # 否则问油脂会把同表的谷物行顶到第一条
                    if cats and any(c in cand for c in cats):
                        score += 4
                    scored.append((score, cand[:260], path.name))
        scored.sort(key=lambda x: -x[0])
        for _s, para, name in scored[:3]:
            clean = _present_para(para)
            if not clean:
                continue
            n_off = sum(1 for c in _FOREIGN_TABLE_CATS if c in clean)
            if n_off >= 3:
                # 拍板（2026-11-11）：不切句。目标表行可能不含被问实体，
                # 按句过滤有把正确那行一起删掉的风险；宁可少给，不可给错。
                # 折叠成一句能行动的话，符合「低置信度转人工」。
                clean = (f"这里原本是 {name} 的一张全品类限量表（含 {n_off} 个与所问物类"
                         "无关的食品类别）。原文是 PDF 表格压成的一段，栏位已错位，"
                         "整表贴出来容易把别类的数抄成所问品类的值，所以我不贴；"
                         "请核对 corpus/limits.json 里已逐格核对的结构化条目，或回原文对应表行。")
            hits.append(clean)
            if name not in sources:
                sources.append(name)

    # 2026-11-11 切版：GB 1350 双表限值问法改为「显式判品种」，不再靠检索命中嗅探。
    # 2025 原文归档已排除出检索池，hits 不再天然同时含表1与表2，旧版
    # _mixed_quality_tables 的前提没了；若仍只看命中，就会把表1的 13.5 当通用答案贴出去。
    # 物类白名单挡住「大米的水分限值」这类本该拒答的问法（它 must 里也有水分含量）。
    _LIMIT_WORDS = ("限值", "多少", "合格线", "几等", "限量", "上限", "下限")
    _OTHER_CATS = ("大米", "糙米", "粳米", "籼米", "米制品", "小麦", "玉米", "大豆",
                   "植物油", "油脂", "花生油", "菜籽油", "食用植物油")
    if (any(w in question for w in _LIMIT_WORDS)
            and any(m in indicators() for m in must)
            and not any(c in _OTHER_CATS for c in cats)
            and "2025" not in question):
        return _clarify_answer(question, must, cats)

    if hits:
        # 兜底：万一语料以后又出现整节糊上来的情况，仍优先反问。
        if _mixed_quality_tables(question, hits):
            return _clarify_answer(question, must, cats)
        boundary = [f"出处：{', '.join(sources)}（corpus/ 只读，未生成结论）"]
        if any("GB1350" in s or "GB 1350" in s for s in sources):
            vn = _version_notice()
            if vn:
                boundary.append(vn)
        # 旧版写「这次没检索到带限值的表行」，可同一张卡里贴的表明写 0.35。
        # PDF 表是「—/0.35」列式、没有 ≥/≤，判据失效（夜测 11-10 r02 复发两晚）。
        # 改成不否认表里有数，只说「没逐格核对、不能拿去判」。
        if (any(w in question for w in ("限值", "多少", "合格线", "几等"))
                and not any(any(ch in h for ch in ("≥", "≤", ">=", "<=")) for h in hits)):
            boundary.append("上面片段里有数字，但没有可逐格核对的表行结构；"
                            "请改用 corpus/limits.json 里已核对的结构化限值判定")
        boundary.append("以上为语料片段摘录，未生成结论；限值以 corpus/limits.json 已核对的"
                        "结构化条目为准，答复须带标准版本与表号")
        return {
            "title": "查到的相关条款",
            "lines": hits,
            "boundary": boundary,
            "examples": ["整精米率和出糙率有什么区别",
                         "DEMO-001 这批稻谷出糙率 76.5 能收吗"],
        }
    # 拒答要说清「缺的是哪一条」，不能用「没找到相关段落」糊过去。
    # 问到了具体指标却没命中，说明这条限值没进库，用户需要知道的是这个。
    if must:
        missing = "、".join(dict.fromkeys(must))[:24]
        cat = f"（{cats[0]}）" if cats else ""
        return {
            "title": f"库里没有「{missing}」的限值记录",
            "lines": [
                f"我把 corpus/ 下的语料全翻了一遍，没有找到能回答「{missing}」{cat}的段落。",
                "这是真没有，不是我不肯答——硬凑一个别的指标的数字给你，比直接说没有危险得多。",
                f"补齐的办法：把写着「{missing}」限值的那份国标原文找出来，逐格核对后放进 corpus/，"
                "再问我一次。",
            ],
            "boundary": [
                "已收录范围：GB 1350 稻谷质量要求、GB 2716/2761/2762 中已核对的条目、"
                "检验方法标准与术语（详见 corpus/README.md）",
                "未收录：GB 2762 的镉、无机砷等尚未逐格核对的污染物限值",
                "真实批次与实测值不进本系统",
            ],
            "examples": capability_answer()["examples"],
        }
    # 纯符号 / 空泛输入：本来就不是业务问题，别拿「翻了语料库」这套话去接。
    # 旧版「？？？」和「写 Python 爬虫」逐字同答，兜底模板接客面太宽
    # （夜测 11-10 r02）。
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", question):
        return {
            "title": "这句话我没读懂",
            "lines": ["没识别出指标名、物类或批次号，所以没有去翻语料库，也不编一段给你。",
                      "换成一句具体的：比如「稻谷水分限值是多少」或「DEMO-001 出糙率 76.5 能收吗」。"],
            "boundary": ["真实批次与实测值不进本系统"],
            "examples": capability_answer()["examples"],
        }
    # 拒答要点名「缺的是哪一条、哪个品类」，不能把「这个指标没入库」
    # 说成「这段语料里没有定义性文字」，更不能给一个写死的「出糙率」示例
    # —— 问水分的人按提示去问出糙率只会更糊涂（夜测 11-10 r02，4/4 分叉）。
    cat = cats[0] if cats else ""
    # 范围外品类先分流：它们不是「还没补」，劝人补国标等于指着一条不该走的路。
    if cat in _BUSINESS_SCOPE:
        return _business_scope_answer(cat)
    scope = f"已收录范围里没有{cat}的这套限值" if cat else "没能定位到你要查的指标或物类"
    return {
        # 标题说「这个问法」、正文说「不是换个问法就能查到」，同一张卡自己拆台
        # （夜测 2026-11-11 r02 轻微④）。统一成陈述式，指向的是缺记录不是问法不好。
        "title": (f"库里没有{cat}的限值记录" if cat
                  else "库里没有能引用的限值记录"),
        "lines": [
            f"我把 corpus/ 下的国标和方法笔记翻了一遍：{scope}。"
            "这是收录范围的问题，不是换个问法就能查到。",
            "我不拿别的指标或别的品类的表行凑一段给你 —— 那比直接说没收录危险得多。",
            "要补：把对应国标原文逐格核对后放进 corpus/，或问班长当前执行的是哪一版。",
        ],
        "boundary": ["已收录：GB 1350 稻谷质量要求、GB 2716/2761/2762 已核对条目、检验方法与术语",
                     "待补：大米成品指标（GB/T 1354—2018）、GB 2762 尚未逐格核对的污染物限值",
                     "范围外：玉米 / 大豆（油化榨油原料）、小麦 / 面粉（本厂不测）——不是「还没补」",
                     "知识库支持在左栏上传文件；新资料先进待核对区，人工确认后才可用",
                     "真实批次与实测值不进本系统"],
        "examples": capability_answer()["examples"],
    }


#: 说人话就该直接触发业务任务，而不是让人再去另一个页面点按钮填表单。
#: 只收「明显是整批活儿」的说法；单批能不能收仍走判定链，这里不抢。
_TASK_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("quality_record_audit",
     ("敲错", "录错", "填错", "输错", "疑点", "自查", "有没有错", "串行", "对不上")),
    ("grain_daily_report",
     ("日报", "出报告", "出份报告", "汇总", "所有批次", "今天的表", "这几张表",
      "整批", "全部批次", "做个汇总")),
)


def task_route(question: str) -> str | None:
    """识别「这句话其实是在下一个业务任务」的情况，返回 task_id。

    单批 DEMO 判定不在此列（有专门的判定链），避免抢已有链路。
    """
    q = str(question or "")
    if re.search(r"DEMO-\d+", q, re.IGNORECASE):
        return None
    for task_id, kws in _TASK_HINTS:
        if any(k in q for k in kws):
            return task_id
    return None


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

    if any(k in q for k in _OUT_OF_SCOPE) or _OUT_OF_SCOPE_RE.search(q):
        return {"kind": "chat", "answer": {
            "title": "这不在我的业务范围内",
            "lines": ["我是粮油原粮验收的判定型助手，只处理检验指标、标准条款、"
                      "录入自查和 DEMO 批次判定，不做闲聊、创作、写代码或通用问答。",
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

    # 查标准 ≠ 判合格。问「X 限值是多少 / 依据在哪一条」是检索，
    # 不能因为句子里带了指标名和等级数字，就被判定链抢走
    # （原先「出糙率 3 等限值是多少」会被判成判定请求，回一句「请切到判定模式」）
    asks_limit = any(w in q for w in ("限值", "标准值", "限量", "上限", "下限", "依据", "出处"))
    asks_verdict = any(w in q for w in ("能收", "能不能收", "合格吗", "合不合格",
                                        "超标", "超了", "怎么判", "判成"))
    if asks_limit and not asks_verdict and not has_batch:
        return {"kind": "chat", "answer": knowledge_answer(q)}

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
                         "当前执行 GB 1350—2009，限值已逐格核对并经人工复核"],
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
    # 请求级开关优先（前端 DeepSeek 按钮），其次环境变量 QC_USE_LLM
    try:
        from . import flags
        if not flags.llm_enabled():
            return "fallback", None
    except Exception:
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


# ------------------------------------------------------------------ 任务理解层（Phase 5 纯增量）
# 只做「自然语言 + 文件清单 → TASKS key + 参数」。route() / llm_route() /
# _ALLOWED_INTENTS 一个字不动；模型输出的数字绝不进 TaskRequest 的判定字段。
# 自由文本只用于这一步识别，识别结果落成 TaskRequest 后才执行。

_TASK_WORDS = {
    "grain_daily_report": ("日报", "验收日报", "出报告", "生成报告", "汇总", "做成日报", "品控日报"),
    "grain_batch_judge": ("判定", "逐项", "能收", "合格", "定等", "几等", "这批", "批次判定"),
    "quality_record_audit": ("自查", "敲错", "疑点", "录入", "核对表", "有没有错", "检查记录"),
}


def _task_candidates() -> list[dict]:
    from .tasks import list_tasks
    return list_tasks()


def _task_result(task_id: str, files: list[str], router: str) -> dict:
    return {"ok": True, "task_id": task_id,
            "params": {"inputs": files, "router": router},
            "note": "参数只取任务 ID 与文件清单；判定所需限值 / 实测值一律由代码从表读取"}


def llm_understand_task(text: str, files: list[str] | None = None) -> dict | None:
    """可选 LLM：只输出白名单内 task_id；任何数字 / 限值 / 结论都不进参数。

    无 key / 未开 QC_USE_LLM / 超时 / 非法 JSON / 白名单外 → 静默返回 None，
    由 understand_task() 的规则兜底，不把失败抛给用户。
    """
    try:
        from . import flags
        if not flags.llm_enabled():
            return None
        from dotenv import load_dotenv
        load_dotenv()
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if not key:
            return None
        from openai import OpenAI
        from .tasks.registry import TASKS
        client = OpenAI(api_key=key, base_url="https://api.deepseek.com",
                        timeout=8.0, max_retries=0)
        resp = client.chat.completions.create(
            model="deepseek-chat", temperature=0.0,
            messages=[{"role": "system", "content":
                       "你是任务识别器。只输出一个 JSON：{\"task_id\":\"...\"}，"
                       "task_id 必须来自给定白名单。禁止输出任何数字、限值、判定结论。"
                       f"白名单：{', '.join(TASKS)}"},
                      {"role": "user", "content": str(text)[:300]}])
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        tid = str(json.loads(raw).get("task_id", "")).strip()
        if tid not in TASKS:
            return None
        return _task_result(tid, list(files or []), "llm")
    except Exception:
        return None


def understand_task(text: str, files: list[str] | None = None) -> dict:
    """自然语言 + 文件清单 → 白名单内 task_id + 参数；白名单外给候选让人选，不硬猜。"""
    q = str(text or "").strip()
    fs = [str(f) for f in (files or []) if str(f).strip()]
    if not q:
        return {"ok": False, "candidates": _task_candidates(),
                "reason": "没看明白要做什么，请从候选任务里选一个"}
    got = llm_understand_task(q, fs)
    if got:
        return got
    scores = {k: sum(1 for w in ws if w in q) for k, ws in _TASK_WORDS.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return {"ok": False, "candidates": _task_candidates(),
                "reason": f"「{q[:40]}」没命中任何任务关键词；请从候选任务里选，不硬猜"}
    return _task_result(best, fs, "rules")
