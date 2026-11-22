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
            "DEMO-001 水分含量 145 是不是敲错了",
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
                     "籼稻谷 1 等 出糙率限值是多少"],
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


def _row_cat_ok(clean: str, cats: list[str]) -> bool:
    """表格「行级」物类闸门：这一格限量是不是给所问物类的。

    段级闸门（:529）挡不住整表里混着的别类行——问「大米成品的 Cd」，
    语料表 2 三行都含「镉」全被选中，其中「谷物（稻谷除外）≤0.1」和
    「谷物碾磨加工品［糙米、大米（粉）除外］≤0.1」并不适用于大米，
    0.2 与 0.1 并排，0.15 的大米样照哪行判会是相反结论（夜测 11-17 r01 一般）。
    规则：带「所问物类除外 / 所问物类除外」标记的排外行一律不贴；
    其余行只要明说含所问物类，或没提任何别的物类（通用行），才留。
    """
    if not cats:
        return True
    wanted = [c for c in cats if c in _CATEGORY]
    if not wanted:
        return True
    # 所问物类 + 它的上位词（稻谷→谷物）：GB 2762 表 1 写「谷物及淀粉类制品」，
    # 不会逐条重述「稻谷」，只认原词会把唯一正确的 ≤0.5 行误杀（本轮实测踩到）。
    kin = set(wanted)
    for c in wanted:
        kin.update(_CAT_PARENTS.get(c, ()))
    # 排除标记：语料有全角/半角、圆/方括号混用且嵌套（「［糙米、大米（粉）除外］」），
    # 解析嵌套括号纯属给自己挖坑（本轮连翻两次车）。改用最笨也最稳的办法：
    # 凡行里出现「除外」，看它之前那一小段里有没有所问物类（含上位词）——
    # 「谷物（稻谷除外）」「糙米、大米（粉）除外」都能命中，不依赖括号配对。
    if "除外" in clean:
        for seg in clean.split("除外"):
            head = seg[-40:]          # 「除外」前最近的一段字
            if any(c in head for c in kin):
                return False
    if any(c in clean for c in kin):
        return True
    # 没提所问物类（含上位词），却点名了别的食品类别 → 不贴。
    # _CATEGORY 没覆盖「肉类」这类词，补一组常见动物/植物性食品兜底，
    # 免得问稻谷却把 ≤0.05 的肉类汞行贴出来。
    _FOOD_OTHER = ("肉类", "蛋类", "乳", "水产", "鱼类", "蔬菜", "水果", "豆类",
                   "坚果", "油脂", "饮料", "调味品")
    others = [c for c in _CATEGORY if c not in kin] + [c for c in _FOOD_OTHER if c not in kin]
    return not any(c in clean for c in others)


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
                       "婴幼儿", "藻类", "贝类", "甲壳类", "罐头", "焙烤",
                       # GB 2762 原文写的是「肉及肉制品 / 乳及乳制品 / 豆类及其制品」，
                       # 词表里只有「肉类 / 乳类」时，问铬的表 7 只数到 2 个异类
                       # （水产动物 + 新鲜蔬菜），整表折叠的 ≥3 门槛不触发，
                       # 蔬菜 0.5 / 水产 2.0 就被当成稻谷的限值贴出来
                       # （夜测 11-16 r02 / 11-17 r02 一般项，复发 2 晚）。
                       "豆类", "肉及肉制品", "乳及乳制品")

#: 段落级物类闸门的上位类映射，只放宽「稻谷验收」这条业务线。
#: GB 2762 表 1 写的是「谷物及淀粉类制品」，不会逐条重述「稻谷」二字；
#: 老闸门只认 cats 里的原词，于是问「稻谷的铅」时把唯一带 ≤0.5 的表行筛掉，
#: 只剩讲油脂的脚注 —— 问稻谷、答油脂（夜测 11-14 r02 / 11-15 r01 严重③）。
#: 不扩到「大米」等其它物类，免得把越界问法一起放进来。
_CAT_PARENTS = {"稻谷": ("谷物",), "糙米": ("谷物",),
                "籼稻谷": ("谷物",), "粳稻谷": ("谷物",)}


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
            "待补：大米成品指标（GB/T 1354—2018）；GB 2762 的镉/铅/汞已从 PDF 逐格抽取，但尚未与化验室受控纸质本对照",
            "系统只按 DEMO- 前缀判断假批次；实测值无法判真假，请勿上传真实数据",
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
    # PDF 抽取在表格脚注后常留一串孤立页码（「b 稻谷以糙米计。 4. 3. 2 …」），
    # 贴到答案尾巴就是「4. 4.」，看着像系统吐了一半崩了
    # （夜测 2026-11-12 r02 一般②，残渣来自语料不是代码截断）。
    # 只清「连续两个以上 数字+句点」的碎片，不碰「0. 1」这类被 PDF 拆开的限值数字。
    p = re.sub(r"(?:^|\s)(?:\d{1,3}\.)(?:\s+\d{1,3}\.)+(?=\s|$)", " ", p).strip()
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


#: 中文数字等别 → 语料键。「一等」「1 等」「第1等」「壹等」都会出现，写法不统一。
_GRADE_CN = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
             "壹": "1", "贰": "2", "叁": "3", "肆": "4", "伍": "5"}
_GRADE_RE = re.compile(r"第?\s*([1-5一二三四五壹贰叁肆伍])\s*等")


def parse_grade(question: str) -> str:
    """从问句里取等别。取不到返回空串 —— 不假装问过。

    夜测 2026-11-12 r01 严重①：`籼稻谷 1 等 出糙率限值` 与「3 等」逐字同答，
    都给 ≥75.0（3 等口径），而 1 等应为 ≥79.0。差 4 个百分点、方向是**放宽**，
    而这两项（出糙率 / 整精米率）恰恰是 GB 1350 的定等指标。
    根因是取值处写死 grade="3"，全库没有等别解析。
    """
    m = _GRADE_RE.search(str(question or ""))
    if not m:
        # 「等外」也认：原表该档写「—」，由 resolve_limit 抛「不要求」，比默默给 3 等好
        return "等外" if "等外" in str(question or "") else ""
    g = m.group(1)
    return _GRADE_CN.get(g, g)


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
    grade_in_q = parse_grade(question)
    # ⚠️ 11-12 曾在这里写死「GB 1350—2009 只有 1/2/3 等与等外」，问 4/5 等一律
    # 回「没有这一等」——**这是事实错误**（DECISIONS_2026-11-12 §2 第 17 项，现作废）。
    # corpus/GB1350_稻谷质量要求.md:20/38 与 limits.json 表1/表2 都写着
    # 等级体系 = 1/2/3/4/5/等外，4 等出糙率 ≥73.0、5 等 ≥71.0。
    # 现在一律走结构化取值，不拿 3 等的数顶，也不许谎称没有这一档（夜测 11-15 r01 严重①）。
    grade = grade_in_q or "3"          # 3 等 = 语料「定等规则」里的中等，仅作没写等别时的兜底
    grade_label = grade if grade == "等外" else f"{grade} 等"
    if cultivar:
        product, table = ("粳稻谷", "表2") if cultivar == "粳" else ("籼稻谷", "表1")
        try:
            from .limits_resolver import build_products, load_limits
            built = build_products(load_limits(), grade=grade)[product]
            items = built["items"]
            key = next((m for m in must if m in items), None)
            if key is None:
                key = next((t for t in indicators() if t in question and t in items), None)
            if key is None and grade_in_q:
                # 这一档这一项原表写「—」（如等外的整精米率）：明明有答案却落回
                # 「先确认物类」，或整档被说成「原表没给数值」，都是答非所问
                # （夜测 2026-11-15 r01 严重②）。build_products 现在逐项容错，
                # 「—」的项单列在 not_required 里。
                nr = next((x for x in (built.get("not_required") or [])
                           if x["item"] in str(question)), None)
                if nr:
                    return {
                        "title": f"{product}「{nr['item']}」{grade_label}：原表不要求",
                        "lines": [
                            f"GB 1350 {table} 里 {grade_label}，「{nr['item']}」原文写的是「—」，"
                            "即这一项在这一档不要求；不能拿别的等别那一格顶上。",
                            f"出处：{nr['limit_source']}。",
                            "要确认这一档实际怎么执行，翻化验室现行手册或问班长。",
                        ],
                        "boundary": [b for b in [
                            _version_notice(),
                            "数值取自 corpus/limits.json → GB1350-2009 结构化条目，已逐格核对",
                        ] if b],
                        "examples": [f"籼稻谷 {grade_label} 出糙率限值是多少",
                                     "籼稻谷 3 等 整精米率限值是多少"],
                    }
            if key:
                spec = items[key]
                lines = [f"{key}：{spec['expr']}{spec['unit']}（{table}，{grade_label}）。",
                         f"出处：{spec['limit_source']}。"]
                # 问句没写等别时不许默不作声地按 3 等给：这样人是拿 3 等的线在判 1 等的粮。
                # 只有真随等别变的指标才需要这句提醒（水分 / 杂质这类各等级同值）。
                if spec.get("kind") == "graded_lower":
                    if grade_in_q:
                        lines.append(f"这条随等别变：你问的是 {grade_label}，上面就是 "
                                     f"{grade_label}那一格；换成别的等别数值会不同。")
                    else:
                        lines.append("这条限值**随等别变**，你问题里没写等别，现按 3 等（中等）给；"
                                     "如果这批是按 1 等 / 2 等收购，把等别写进问题"
                                     "（例如「籼稻谷 1 等 出糙率限值」），我再给对应那一档。")
                else:
                    lines.append("这项各等级同值，等别不影响数值。")
                # 问句没写等别时，标题别印成「3 等」——那是兜底默认档，不是用户问的档；
                # 签字人扫一眼标题就以为这批按 3 等，而 1 等的线差 4 个百分点
                # （夜测 11-20 r01 M2）。lines 里已有同款提醒，标题同步标清。
                _title_grade = grade_label if grade_in_q else f"默认{grade_label}"
                return {
                    "title": f"{product}「{key}」限值 · {spec['standard']} {table} {_title_grade}",
                    "lines": lines,
                    "boundary": [b for b in [
                        _version_notice(),
                        "数值取自 corpus/limits.json → GB1350-2009 结构化条目，已逐格核对；语料片段摘录仍以原文为准",
                        "系统只按 DEMO- 前缀判断假批次；实测值无法判真假，请勿上传真实数据",
                    ] if b],
                    "examples": ["籼稻谷 1 等 出糙率限值是多少",
                                 "籼稻谷 3 等 出糙率限值是多少",
                                 "把这张表做成今天的日报"],
                }
        except (KeyError, ValueError):
            # 「—（不要求）」或该档不存在：说清是哪一档没有数值，别拿别的档顶。
            # （等外档的整精米率原表就是「—」，此处不是异常，是原表的说法。）
            if grade_in_q:
                return {
                    "title": f"{product}「{item}」{grade_label}：原表没给数值",
                    "lines": [
                        f"GB 1350 {table} 里 {grade_label}，原文写的不是数字"
                        "（原表是「—」，即该项在这一档不要求），不能拿别的档的数值顶上。",
                        "要确认这一档实际怎么执行，翻化验室现行手册或问班长。",
                    ],
                    "boundary": [_version_notice(), "系统只按 DEMO- 前缀判断假批次；实测值无法判真假，请勿上传真实数据"],
                    "examples": ["籼稻谷 3 等 出糙率限值是多少"],
                }
        except Exception:
            pass          # 结构化限值取不到就落回反问，绝不编数

    return {
        "title": f"先确认物类，再给「{item}」{cat}的限值",
        "lines": [
            f"GB 1350 里「{item}」分两张表：表1 是早籼 / 晚籼 / 籼糯稻谷，"
            "表2 是粳稻谷、粳糯稻谷，部分指标两表数值不同、部分相同。",
            # 举例必须跟着被问的指标走：问出糙率却拿「水分」举例，等于把人往岔路上引
            # （夜测 11-14 r01/r02、11-15 r01 轻微，连记三晚）。
            f"你验的是籼稻还是粳稻？请把品种连着指标一起问，"
            f"例如「籼稻谷{must[0] if must else '水分含量'}限值是多少」，"
            "我就给当前执行版对应那一格。",
            "现在不并列贴两个数，也不替你挑一个 —— 挑错表就是拿错限值，"
            "这恰恰是最不该由系统代做的判断。",
        ],
        "boundary": [b for b in [
            "检索到的原文同时含表1与表2，属低置信度命中，按四条原则转人工确认",
            _version_notice(),
            "系统只按 DEMO- 前缀判断假批次；实测值无法判真假，请勿上传真实数据",
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
                    parents = tuple(p for c in cats for p in _CAT_PARENTS.get(c, ()))
                    parent_ok = bool(parents) and any(p in para for p in parents)
                    if not parent_ok and not any(c in path.name for c in cats):
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
                        # 行级物类闸门：一整张表里混着别类的限量行，段级闸门管不到。
                        # 问大米只贴给大米的那行，「谷物（稻谷除外）」等排外行不进答案
                        # （夜测 11-17 r01 一般）。
                        if clean and not _row_cat_ok(clean, cats):
                            continue
                        if clean:
                            candidates.append(clean)
                    candidates = candidates[:2]
                else:
                    clean = _present_para(para)
                    # 笔记体语料把「砷 / 铬 / 亚硝酸盐」三个污染物写在同一个段落里，
                    # 整段命中后 lines[0] 对问砷、问铬逐字相同——问砷先读到铬、问铬先读到砷
                    # （夜测 11-16 r02 / 11-17 r02 一般，第 8 晚）。按「· 」切成句。
                    parts = [x.strip() for x in clean.split("·") if len(x.strip()) >= 8]
                    if must and len(parts) > 1:
                        # 只认「句首就在讲被问实体」的那句。不能用「句中出现过这个字」——
                        # 铬句里有「与砷同为待补项」，问砷时它也会被判成命中，
                        # 而且带 1.0 和标准号、打分更高，反把真正的砷句挤出前三（本轮实测踩到）。
                        mine = [x for x in parts if any(_ent_in(m, x[:14]) for m in must)]
                        if mine:
                            parts = mine
                    candidates = (["· " + x for x in parts] if parts
                                  else ([clean] if clean else []))
                for cand in candidates:
                    score = 2 * sum(1 for t in terms if t in cand)
                    # 句首（前 16 字，含「· 」前缀）就在答被问实体 → 强加权排第一；
                    # 只在句中顺带提到 → 弱加权。段级打分分不出砷句 / 铬句高下。
                    if must:
                        if any(_ent_in(m, cand[:16]) for m in must):
                            score += 12
                        elif any(_ent_in(m, cand) for m in must):
                            score += 3
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
        # 问「铅」却贴出整段「一级浸出大豆油：酸价 ≤0.50、过氧化值…」，
        # 是同一份语料里另一条产品线的段落被算成命中（11-12 r01 轻微，复发 3 次）。
        # 只在「问的是污染物、且这段通篇是油脂线指标、不含被问实体」时剔；
        # 剔空就回退不过滤 —— 宁可多给一段，也不把唯一能答的那段删掉。
        _asked = [c for c in _CONTAMINANTS if c in question]
        _oil_marks = ("酸价", "过氧化值", "黄曲霉毒素", "浸出", "压榨")

        def _off_topic(cand: str) -> bool:
            return bool(_asked and any(m in cand for m in _oil_marks)
                        and not any(c in cand for c in _asked))

        _kept = [x for x in scored[:3] if not _off_topic(x[1])]
        _pool = _kept or scored[:3]
        foreign_rows = 0
        for _s, para, name in _pool:
            clean = _present_para(para)
            if not clean:
                continue
            # 同一段笔记把「酸价 / 过氧化值 / 黄曲霉」和污染物混着写时，段里也会
            # 出现被问的那个字（例如「③ 铅：…」），上面的预筛挡不住。但只要答案里
            # 已经有带 ≤/≥ 的那一格，这段整段贴出来就纯属噪音 —— 不切句、不删已有
            # 正确行，直接跳过这一段（11-12 r01 轻微，复发 3 次）。
            if (_asked and any(m in clean for m in _oil_marks)
                    and any(any(ch in h for ch in ("≤", "≥")) for h in hits)):
                continue
            n_off = sum(1 for c in _FOREIGN_TABLE_CATS if c in clean)
            # 门槛原是 ≥3（2026-11-11 拍板）。但只混进 1 行异类时（如问稻谷的汞，
            # 「总汞｜肉类｜≤0.05」照样贴出来），使用人会把别类的限值抄成本批的值
            # （夜测 11-15 r01 一般②、11-14 r02 一般②；QA-08 镉整表贴入同根）。
            # 现在：带 ≤/≥ 的限量行，只要含异类类别、又不含被问物类，就不贴那个数；
            # 整段全品类表（≥3）仍折叠成一句可行动的话。宁可少给，不可给错。
            is_limit_row = any(ch in clean for ch in ("≤", "≥"))
            asked_here = bool(cats and any(c in clean for c in cats))
            if n_off >= 1 and is_limit_row and not asked_here:
                foreign_rows += 1
                continue
            if n_off >= 3:
                clean = (f"这里原本是 {name} 的一张全品类限量表（含 {n_off} 个与所问物类"
                         "无关的食品类别）。原文是 PDF 表格压成的一段，栏位已错位，"
                         "整表贴出来容易把别类的数抄成所问品类的值，所以我不贴；"
                         "请回原文对应表行核对，或问班长。")
            hits.append(clean)
            if name not in sources:
                sources.append(name)
        if foreign_rows:
            hits.append(f"另有 {foreign_rows} 行是其它食品类别的限量（与本批所问物类无关），"
                        "未贴；需要那一格请翻原文对应表行，别拿它当本批的限值。")

    # 2026-11-11 切版：GB 1350 双表限值问法改为「显式判品种」，不再靠检索命中嗅探。
    # 2025 原文归档已排除出检索池，hits 不再天然同时含表1与表2，旧版
    # _mixed_quality_tables 的前提没了；若仍只看命中，就会把表1的 13.5 当通用答案贴出去。
    # 物类白名单挡住「大米的水分限值」这类本该拒答的问法（它 must 里也有水分含量）。
    _LIMIT_WORDS = ("限值", "多少", "合格线", "几等", "限量", "上限", "下限")
    _OTHER_CATS = ("大米", "糙米", "粳米", "籼米", "米制品", "小麦", "玉米", "大豆",
                   "植物油", "油脂", "花生油", "菜籽油", "食用植物油")
    # 问「糙米的出糙率限值」时，检索会翻出镉的折算注、标准清单行、检验方法行
    # 三段互不相干的文字（夜测 11-15 r01 一般①）。根子是口径：出糙率是对
    # 稻谷定的分等指标，糙米没有这一格；「稻谷以糙米计」是污染物折算，不是另一套限值。
    # 能靠说清楚解决，就不动判定链。
    if any(c in cats for c in ("糙米",)) and any(
            i in question for i in ("出糙率", "整精米率")):
        return {
            "title": "糙米没有独立的「出糙率 / 整精米率」限值",
            "lines": [
                "GB 1350—2009 的分等指标是对**稻谷**定的：出糙率是把稻谷砻谷后算糙米占比，"
                "整精米率同理；糙米是加工后的产物，原表没有给它单独立一格限值。",
                "GB 2762 那条「稻谷以糙米计」（注 a）是**污染物限量的折算口径**，"
                "跟出糙率不是一回事，别把两句话拼成一个数。",
                "要判这批粮，按稻谷问（例如「籼稻谷 3 等 出糙率限值是多少」）；"
                "若化验室现行手册对糙米另有执行口径，以手册为准并请人工确认。",
            ],
            "boundary": [b for b in [_version_notice(),
                                     "出处：GB 1350—2009 表1/表2（分等指标体系）、GB 2762—2025 注 a"] if b],
            "examples": ["籼稻谷 3 等 出糙率限值是多少",
                         "大米成品的Cd含量不得超过多少"],
        }
    # 「粳稻谷 1 等 出糙率」这类问法没有「限值」二字，但句子里点了等别，
    # 已经在问某一档的数了。不放进来就会掉进整节检索，把表1 的籼稻数贴给问粳稻的人
    # （夜测 2026-11-12 r01：同一句话既漏了等别、又跨了表）。
    if ((any(w in question for w in _LIMIT_WORDS) or _GRADE_RE.search(question))
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
        no_limit_hit = False   # 这段里连一个限值数字都没有 → 不能叫「查到的相关条款」
        if (any(w in question for w in ("限值", "多少", "合格线", "几等"))
                and not any(any(ch in h for ch in ("≥", "≤", ">=", "<=")) for h in hits)):
            # 上一版只判「有没有比较符」，于是一个阿拉伯数字都没有的片段也被说成
            # 「上面片段里有数字」（夜测 2026-11-12 r02 轻微③，问铬时贴的是砷那句话）。
            # 有数就说有数，没数就直说没数——这句话是给人判断「能不能将就用」的。
            # 只认「像限值的数字」：带小数（含 PDF 拆开的 0. 1）或 ≥/≤ 后跟数字。
            # 「表 4」「第 3 等」这类序号不算，否则问铬时那段一个限值都没有，
            # 也会被说成「上面片段里有数字」（11-12 r02 轻微③，今早那版仍漏）。
            if any(re.search(r"\d+\.\d+|\d\.\s*\d|[≥≤]\s*\d+", h) for h in hits):
                # 旧句「没有可逐格核对的表行结构」是假的：语料里就有 | 铅 | 谷物… |
                # 这样的表行，只是抽进来的这段不是「所问这一项」的表行
                # （夜测 2026-11-15 r01 严重③ / r02 轻微③）。不再否认表里有表行，
                # 只说清「片段里的数不能当这一项的限值用」。
                boundary.append("上面片段里有数字，但它们不是「这一项」逐格核对过的限量表行，"
                                "不能拿去判；请翻国标原文对应表行或问班长，"
                                "别拿片段里的数当限值用")
                # G1：语料原句写「明写 1.0 mg/kg…这是强制项」，读起来像结论，
                # 而底注又说「不能拿去判」——同一张卡打架（夜测 11-21 r02 一般）。
                # 不动语料原文（可追溯），在正文最前面顶一条醒目提示，
                # 让「数字是摘录、未核对、不能判」成为读者第一眼看到的约束。
                hits = ["⚠️ 先说清楚：下面的数字是国标原文片段摘录，本库尚未逐格核对，"
                        "不能当限值直接判；要判请翻原文对应表行、以化验室受控本为准或问班长。"] \
                       + list(hits)
            else:
                # 别再指路 limits.json：砷/铬在这个文件里零命中，指过去等于
                # 让人去一个没有这一项的地方找（夜测 2026-11-15 r02 轻微③）。
                no_limit_hit = True
                boundary.append("上面这段里一个限值数字都没有，不能拿去判；"
                                "这一项本库还没逐格抽取（标准里可能有，属待补项），"
                                "请翻国标原文或问班长，别拿别的物类的数顶")
        boundary.append("以上为语料片段摘录，未生成结论；限值以 corpus/limits.json 已核对的"
                        "结构化条目为准，答复须带标准版本与表号")
        return {
            "title": ("这一项还没收录，下面是相关说明" if no_limit_hit
                      else "查到的相关条款"),
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
                "库里还没收录这一条，不是国标没有这项要求——比如大米成品的水分，GB/T 1354—2018 里是有规定的，只是还没逐格提取进 corpus/。硬凑一个别的指标的数字给你，比直接说没有危险得多。",
                f"补齐的办法：把写着「{missing}」限值的那份国标原文找出来，逐格核对后放进 corpus/，"
                "再问我一次。",
            ],
            "boundary": [
                "已收录范围：GB 1350 稻谷质量要求、GB 2716/2761/2762 中已核对的条目、"
                "检验方法标准与术语（详见 corpus/README.md）",
                "未收录：GB 2762 的无机砷等污染物限值（镉/铅/汞已抽取，尚未与受控纸质本对照）",
                "系统只按 DEMO- 前缀判断假批次；实测值无法判真假，请勿上传真实数据",
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
            "boundary": ["系统只按 DEMO- 前缀判断假批次；实测值无法判真假，请勿上传真实数据"],
            "examples": capability_answer()["examples"],
        }
    # 拒答要点名「缺的是哪一条、哪个品类」，不能把「这个指标没入库」
    # 说成「这段语料里没有定义性文字」，更不能给一个写死的「出糙率」示例
    # —— 问水分的人按提示去问出糙率只会更糊涂（夜测 11-10 r02，4/4 分叉）。
    cat = cats[0] if cats else ""
    # 范围外品类先分流：它们不是「还没补」，劝人补国标等于指着一条不该走的路。
    if cat in _BUSINESS_SCOPE:
        return _business_scope_answer(cat)
    # 只给品类、没给指标：这是「缺指标」，不是「品类没收录」。
    # 反问卡让人「把品种连着指标一起问」，用户照做只回「籼稻谷」，旧版却答
    # 「库里没有稻谷的限值记录」——而同卡 boundary 第一行就写着「已收录 GB 1350
    # 稻谷质量要求」，一张卡自相矛盾（夜测 11-21 r01 严重 / r06 复验，第 3 晚）。
    # 化验室的人读到「库里没有稻谷」要么去补一份早已入库的 GB 1350，要么弃用。
    # 这里只认已收录的稻谷系品类；大米是「待补」不在此列，仍走下面的兜底。
    _COVERED_CAT = ("稻谷", "籼稻谷", "粳稻谷", "糙米", "籼稻", "粳稻")
    # 「籼稻 / 粳稻」只是品种缩写，_CATEGORY 只收「稻谷」，cats 解析不出它们，
    # 这里从原问句兜底认一次，避免只回「籼稻」仍掉进「库里没有能引用的限值」。
    _cat_word = cat
    if not _cat_word and not must:
        _cat_word = next((w for w in ("籼稻谷", "粳稻谷", "籼稻", "粳稻")
                          if w in question), "")
    if _cat_word and not must and (_cat_word in _COVERED_CAT or cat in _COVERED_CAT):
        show_cat = cat or _cat_word
        names = indicators()
        name_list = " / ".join(names)
        return {
            "title": f"你只说了{show_cat}，还没说要问哪一项",
            "lines": [
                f"{show_cat}的限值我这里收着，但要按具体指标查。已收录的指标有：{name_list}。",
                f"你想看哪一项？这样说就行：「{show_cat}{{指标}}限值是多少」，"
                "分品种/等别的指标（出糙率、整精米率、水分）再把籼稻或粳稻、等别带上。",
            ],
            "boundary": [
                "已收录：GB 1350 稻谷质量要求（出糙率 / 整精米率 / 杂质 / 水分 / "
                "黄粒米 / 谷外糙米 / 互混率），7 项逐格可用",
                "分品种或等别的指标要写明籼稻 / 粳稻、第几等，否则按 3 等（中等）给并注明",
                "范围外：玉米 / 大豆（油化榨油原料）、小麦 / 面粉（本厂不测）——不是「还没补」",
                "系统只按 DEMO- 前缀判断假批次；实测值无法判真假，请勿上传真实数据",
            ],
            "examples": [f"{show_cat}出糙率限值是多少", f"{show_cat}杂质限值是多少",
                         "DEMO-001 这批稻谷出糙率 76.5 能收吗"],
        }
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
                     "待补：大米成品指标（GB/T 1354—2018）；GB 2762 的镉/铅/汞已从 PDF 逐格抽取，但尚未与化验室受控纸质本对照",
                     "范围外：玉米 / 大豆（油化榨油原料）、小麦 / 面粉（本厂不测）——不是「还没补」",
                     "知识库支持在左栏上传文件；新资料先进待核对区，人工确认后才可用",
                     "系统只按 DEMO- 前缀判断假批次；实测值无法判真假，请勿上传真实数据"],
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
            "boundary": ["系统只按 DEMO- 前缀判断假批次；实测值无法判真假，请勿上传真实数据"],
            "examples": capability_answer()["examples"]}}

    has_batch = bool(re.search(r"DEMO-\d+", q, re.IGNORECASE))
    has_indicator = any(i in q for i in indicators())
    # 「1 等」「3 等」是等级，不是实测值。不剥掉它，「籼稻谷 1 等 整精米率是多少」
    # 会因为句子里有个 1 被当成判定请求抢走，回一句「请切到判定模式」——
    # 而判定模式又要批次号，人就被堵在死胡同里（夜测 2026-11-12 r01 一般①）。
    has_number = bool(re.search(r"\d", _GRADE_RE.sub("", q)))
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
                "（目前内置只有 DEMO-001）和具体指标数值，我就跑逐项判定 + 录入错误自查，"
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
