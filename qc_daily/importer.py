"""品控日报智能体 · 数据导入工具

把用户自己的 Excel / CSV 数据导入 SQLite（qc_source.db），
之后把配置切到 "source.type": "sql" 即可用真实数据出报。

为贴近国内企业实际数据习惯，做了三件事：
1. 中文表头自动识别（批次号→batch_id、实测值→value……），也兼容英文表头；
2. 兼容"宽表"质检报表（酸价/过氧化值等每个项目一列，一个批次一行），
   自动翻转成系统要求的"长表"（一个批次×一个项目一行）；
3. 表里缺的字段（单位/方向/限值/标准/出处）按配置的产品限值自动补齐——
   限值不靠猜，全部来自 qc_daily_config.json（与国标语料同源）。

用法：
    python -m qc_daily import --file 我的数据.xlsx --dry-run   # 先预览映射，不写库
    python -m qc_daily import --file 我的数据.xlsx             # 确认后正式导入
    python -m qc_daily import --dir my_data/                   # 整个目录批量导入
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
from datetime import datetime, date
from typing import Optional

from .config import QCDailyConfig
from .console import say

# ------------------------------------------------------------------ 表头别名表

#: 规范字段 → 可能的表头写法（命中任一即映射）。先按去掉空格/大小写精确匹配。
ALIASES: dict[str, list[str]] = {
    # 生产
    "batch_id":    ["批次号", "批次编码", "批次", "生产批次", "批号", "批次编号", "batch", "batchid",
                    "batch_id", "batch no", "lot", "lot_no", "lotno"],
    "product":     ["产品", "产品名称", "品名", "油品", "产品类别", "稻谷品种", "原粮品种",
                    "粮种", "product", "product_name"],
    "line":        ["产线", "生产线", "车间", "线别", "line", "workshop", "production_line"],
    "planned_qty": ["计划产量", "计划量", "计划", "planned", "planned_qty", "plan"],
    "actual_qty":  ["实际产量", "产量", "实际", "实际量", "actual", "actual_qty", "output"],
    "started_at":  ["开工时间", "开始时间", "生产日期", "投产日期", "start", "started_at",
                    "start_time", "production_date"],
    "finished_at": ["完工时间", "结束时间", "完工日期", "finish", "finished_at", "end_time"],
    # 质检
    "item":        ["检测项目", "项目", "指标", "检测指标", "检验项目", "检验指标",
                    "item", "test_item", "indicator"],
    "value":       ["实测值", "检测值", "测定值", "结果", "测量值", "value", "result", "measured",
                    "test_result"],
    "unit":        ["单位", "计量单位", "unit", "units"],
    "direction":   ["方向", "指标方向", "direction"],
    "limit_value": ["限值", "标准限值", "限量", "标准值", "limit", "limit_value", "standard_limit"],
    "standard":    ["标准", "标准号", "依据标准", "执行标准", "standard", "std"],
    "tested_at":   ["检验日期", "检测日期", "检验时间", "检测时间", "扦样日期", "tested",
                    "tested_at", "test_date", "检验日期时间"],
    "inspector":   ["检验员", "检测人", "检测员", "化验员", "检验人", "inspector", "tester"],
    "lab":         ["实验室", "检测机构", "检测单位", "班组", "检验班组", "班组名",
                    "lab", "laboratory"],
    "limit_source": ["限值出处", "出处", "limit_source"],
    # 仓储
    "warehouse":   ["仓库", "库房", "库区", "warehouse", "store", "storage"],
    "event":       ["事件", "类型", "操作类型", "业务类型", "event", "type", "operation"],
    "qty":         ["数量", "数量(t)", "吨数", "重量", "qty", "quantity", "weight", "amount"],
    "temp":        ["温度", "库温", "温度(℃)", "temp", "temperature"],
    "humidity":    ["湿度", "相对湿度", "humidity", "rh"],
    "occurred_at": ["发生时间", "日期", "时间", "业务日期", "occurred", "occurred_at",
                    "入库时间", "warehouse_date"],
}

#: 宽表质检报表中常见的检测项目列名（用于识别"宽表"格式）
#: 前段是食用油（早期架构验证案例，保留兼容），后段是主域稻谷验收的 7 项数值指标。
#: 只追加列名，不改 map_headers / detect_type / convert 的任何判定逻辑。
KNOWN_ITEMS = ["酸价", "过氧化值", "水分及挥发物", "不溶性杂质", "溶剂残留量", "烟点",
               "色泽", "透明度", "气味滋味", "含皂量", "冷冻试验", "加热试验",
               "相对密度", "折光指数", "碘值", "皂化值", "脂肪酸组成", "极性组分",
               "黄曲霉毒素B1", "铅", "游离羟基",
               "出糙率", "整精米率", "杂质含量", "水分含量",
               "黄粒米含量", "谷外糙米含量", "互混率"]

#: 化验室简称 → 词典标准名。
#: 真实送检表的列头写「水分」「杂质」「黄粒」「谷外糙米」，词典里是「水分含量」
#: 「杂质含量」「黄粒米含量」「谷外糙米含量」——不做这层映射，32 列里只认出出糙率
#: 和整精米率两项（2026-11-09 实测，用户连续三次问到「还是只有两个指标吗」）。
#:
#: ⚠️ 只收**一对一**、语义明确的简称。故意不映射：
#:   「同类互混」「长短互混」→ 互混率  ← 两者是不同指标，合并会在同批同日造出重复
#:   记录，被 DUP_QC 整单拦下；宁可让它认不出、如实说认不出，也不悄悄合并。
#:   「整米」→ 整精米率  ← 含义不确定（可能是整米率而非整精米率），不猜。
ITEM_ALIASES: dict[str, str] = {
    "水分": "水分含量",
    "杂质": "杂质含量",
    "黄粒": "黄粒米含量",
    "黄粒米": "黄粒米含量",
    "谷外糙米": "谷外糙米含量",
    "出糙": "出糙率",
    "整精米": "整精米率",
}

_NORM_ITEM_ALIASES: dict[str, str] | None = None


def _item_alias_map() -> dict[str, str]:
    """简称归一化后的查表：{'水分': '水分含量', ...}"""
    global _NORM_ITEM_ALIASES
    if _NORM_ITEM_ALIASES is None:
        _NORM_ITEM_ALIASES = {_norm_header(k): v for k, v in ITEM_ALIASES.items()}
    return _NORM_ITEM_ALIASES


def normalize_item(name) -> str:
    """把列头/项目名换成词典里的标准名；认不出就原样返回，绝不硬凑。"""
    s = _to_text(name).strip()
    if not s:
        return s
    return _item_alias_map().get(_norm_header(s), s)

#: 表类型识别关键词（按 sheet 名 / 文件名）
TYPE_KEYWORDS = {
    "production": ["生产", "投产", "mes", "production"],
    "quality": ["质检", "质量", "检验", "检测", "lims", "quality", "qc", "品控"],
    "warehouse": ["仓储", "仓库", "库存", "入库", "出库", "wms", "warehouse", "storage"],
}

TABLE_COLUMNS = {
    "production": ["batch_id", "product", "line", "planned_qty", "actual_qty",
                   "started_at", "finished_at"],
    "quality": ["batch_id", "product", "item", "value", "unit", "direction", "limit_value",
                "standard", "limit_source", "tested_at", "inspector", "lab"],
    "warehouse": ["batch_id", "warehouse", "event", "qty", "temp", "humidity", "occurred_at"],
}

DATE_FIELD = {"production": "started_at", "quality": "tested_at", "warehouse": "occurred_at"}


# ------------------------------------------------------------------ 基础工具

def _norm_header(h) -> str:
    """表头归一化：去空格/小写，并剥离括号后缀（'计划产量(t)' → '计划产量'）。

    全角括号必须先转半角：国内化验室的表几乎都写 `出糙率（%）`，
    原先只匹配半角 `()`，导致整张 31 列的表一列都认不出来（2026-11-08 实测）。
    """
    s = str(h or "").strip().lower()
    # 化验室表头常在列名里硬换行（'水分\n (%)'、'出糙率\n（%）'）。
    # 只去空格不清换行时，归一化结果是 '水分\n'，跟词典里的 '水分' 对不上，
    # 整张 32 列真表一列都认不出（2026-11-08 晚实测）。连同制表/不间断空格一起清。
    s = re.sub(r"[\s\u00a0\u3000]+", "", s)
    s = s.replace("（", "(").replace("）", ")").replace("［", "[").replace("］", "]")
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"\[[^\]]*\]", "", s)
    return s


_KNOWN_HEADER_NORMS: set[str] | None = None


def _known_header_norms() -> set[str]:
    """所有"能被认出来"的表头写法（规范字段别名 + 检测项目名），用于猜表头在第几行。"""
    global _KNOWN_HEADER_NORMS
    if _KNOWN_HEADER_NORMS is None:
        s: set[str] = set()
        for aliases in ALIASES.values():
            s.update(_norm_header(a) for a in aliases)
        s.update(_norm_header(i) for i in KNOWN_ITEMS)
        _KNOWN_HEADER_NORMS = {x for x in s if x}
    return _KNOWN_HEADER_NORMS


def guess_header_row(rows: list, scan: int = 8) -> int:
    """在前几行里找真正的表头行。

    化验室的原始记录表，上面常压着「××公司 送检水稻检测」「黄色信息需重点确认」这类
    标题/说明行。原先硬取第 1 行，整张表一列都认不出来，还报成"日期不匹配"，
    把人指去改日期（2026-11-08 实测）。
    评分 = 这一行里有多少格能对上已知表头写法；都认不出来时退回第一行非空行。
    """
    known = _known_header_norms()
    first_nonempty = next((i for i, r in enumerate(rows[:scan])
                           if any(c is not None and str(c).strip() for c in r)), None)
    if first_nonempty is None:
        return 0
    best_i, best_score = first_nonempty, 0
    for i, row in enumerate(rows[:scan]):
        cells = [c for c in row if c is not None and str(c).strip()]
        if not cells:
            continue
        score = sum(1 for c in cells if _norm_header(c) in known)
        if score > best_score:
            best_i, best_score = i, score
    return best_i if best_score >= 2 else first_nonempty


def _to_float(v) -> Optional[float]:
    """把一格读成数字；读不懂就返回 None —— 绝不猜、绝不悄悄改数。

    ⚠️ 实测值写 `>5.0` / `＜0.5` 时真值未知（大于 5.0、小于 0.5），旧版用
    lstrip 把比较符抹掉读成 5.0，方向为 ≤ 的项目会被判「合格」——这是唯一能把
    不合格判成合格的路径（2026-11-08 夜测 S1）。现在一律返回 None，由 convert()
    记「按原文保留，未参与比较」并跳过该行；其余行照跑。
    """
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)                      # NaN / inf 交给下游 validate_collected
    s = str(v).strip().replace(",", "").replace("，", "")
    # 全角数字/小数点归一（１２．３ → 12.3），别把「写了数」误当成「读不懂」
    s = s.translate(str.maketrans("０１２３４５６７８９．－＋％",
                                  "0123456789.-+%"))
    # 比较符 / 约等号：真值未知，不猜（限值列请用 _to_limit_float）
    if any(ch in s for ch in "<>＜＞≤≥≦≧=～~约"):
        return None
    m = re.match(r"^([+-]?\d+(?:\.\d+)?)\s*[^\d.\-]*$", s)   # 允许 13.2% / 0.50 mg/g
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _to_limit_float(v) -> Optional[float]:
    """限值列允许写 `≤13.5` / `≥77.0` —— 方向符号本就是限值的写法，剥掉再读数。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    for ch in ("≤", "≥", "≦", "≧", "＜", "＞", "<", ">", "="):
        s = s.lstrip(ch)
    return _to_float(s)


def _to_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


def normalize_datetime(v) -> str:
    """把各种日期写法归一为 'YYYY-MM-DD HH:MM:SS' 或 'YYYY-MM-DD'（SQLite date() 可直接用）。"""
    if v is None or v == "":
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
                "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日 %H:%M:%S", "%Y年%m月%d日 %H:%M",
                "%Y年%m月%d日", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return s  # 原样返回，校验环节会提示


# ------------------------------------------------------------------ 文件读取

#: 公式格未保存计算结果时的占位前缀。
#: Excel 用 data_only=True 读这类格得到 None，旧版当成"空格子"静默跳过 ——
#: 于是一整行数据凭空消失、报告少列一项不合格还不吭声（夜测 2026-11-09 S2）。
#: 用 U+2063 做前缀，正常文本里不会出现，也不影响数字解析。
FORMULA_NO_CACHE = "\u2063公式无结果:"


def _is_formula_cell(v) -> bool:
    return isinstance(v, str) and v.lstrip().startswith("=") and len(v.strip()) > 1


def _is_formula_marker(v) -> bool:
    return isinstance(v, str) and v.startswith(FORMULA_NO_CACHE)


def _formula_expr(v) -> str:
    return str(v)[len(FORMULA_NO_CACHE):].strip() if _is_formula_marker(v) else ""


def _read_xlsx(path: str) -> list[tuple[str, list[dict]]]:
    """返回 [(sheet名, [行dict, ...]), ...]。

    同时用 data_only=True / False 各开一次：后者只为识别「是公式、但没存计算结果」的格子。
    这类格子不能当空格子跳过——跳过等于把整行证据丢了。
    """
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        wb_f = load_workbook(path, data_only=False, read_only=True)
    except Exception:
        wb_f = None
    sheets = []
    try:
        for idx, ws in enumerate(wb.worksheets):
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            frows = []
            if wb_f is not None and idx < len(wb_f.worksheets):
                frows = list(wb_f.worksheets[idx].iter_rows(values_only=True))
            header_idx = guess_header_row(rows)
            headers = [_to_text(h) for h in rows[header_idx]]
            records = []
            for r_i, r in enumerate(rows[header_idx + 1:], start=header_idx + 1):
                if not any(c is not None and str(c).strip() for c in r):
                    continue
                frow = frows[r_i] if r_i < len(frows) else ()
                rec = {}
                for i, v in enumerate(r):
                    if i >= len(headers):
                        continue
                    if v is None and i < len(frow) and _is_formula_cell(frow[i]):
                        v = FORMULA_NO_CACHE + str(frow[i])     # 留证据，别当空格子
                    rec[headers[i]] = v
                records.append(rec)
            sheets.append((ws.title, records))
    finally:
        wb.close()
        if wb_f is not None:
            wb_f.close()
    return sheets


def _read_csv(path: str) -> list[tuple[str, list[dict]]]:
    """尝试多种编码读取（国内 Excel 导出常见 GBK）。"""
    for enc in ("utf-8-sig", "gb18030", "utf-8"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                rows = list(csv.reader(f))
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"无法识别文件编码: {path}")
    if not rows:
        return []
    hi = guess_header_row(rows)
    headers = [h.strip() for h in rows[hi]]
    records = [{headers[i] if i < len(headers) else f"col{i}": (r[i] if i < len(r) else "")
                for i in range(len(headers))}
               for r in rows[hi + 1:] if any(c.strip() for c in r)]
    return [(os.path.splitext(os.path.basename(path))[0], records)]


def read_tabular(path: str) -> list[tuple[str, list[dict]]]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return _read_xlsx(path)
    if ext == ".csv":
        return _read_csv(path)
    if ext == ".json":
        return _read_json(path)
    raise ValueError(f"不支持的文件类型: {ext}（仅支持 .xlsx / .csv / .json）")


#: JSON 多表结构的键名别名 → 规范表名
_JSON_TABLE_KEYS = {
    "production": "production", "生产": "production", "mes": "production",
    "quality": "quality", "质检": "quality", "检验": "quality", "检测": "quality",
    "lims": "quality", "qc": "quality",
    "warehouse": "warehouse", "仓储": "warehouse", "仓库": "warehouse", "wms": "warehouse",
}


def _read_json(path: str) -> list[tuple[str, list[dict]]]:
    """读取 JSON：

    - 记录数组：[{...}, {...}] → 单表（按列名识别类型）；
    - 多表对象：{"production": [...], "quality": [...], "warehouse": [...]}
      （也认中文键"生产"/"质检"/"仓储"）→ 三张表一次导入；
    - 包装对象：{"data": [...]} / {"items": [...]} → 单表。
    """
    with open(path, "r", encoding="utf-8-sig") as f:
        payload = json.load(f)

    tables: list[tuple[str, list[dict]]] = []
    if isinstance(payload, list):
        tables.append(("json", [dict(r) for r in payload if isinstance(r, dict)]))
    elif isinstance(payload, dict):
        for key, value in payload.items():
            tname = _JSON_TABLE_KEYS.get(str(key).strip().lower())
            if tname and isinstance(value, list):
                tables.append((tname, [dict(r) for r in value if isinstance(r, dict)]))
        if not tables:
            for key in ("data", "items", "records", "list"):
                if isinstance(payload.get(key), list):
                    tables.append(("json", [dict(r) for r in payload[key] if isinstance(r, dict)]))
                    break
        if not tables:
            raise ValueError("JSON 结构无法识别：应为记录数组，或含 production/quality/warehouse"
                             "（或中文“生产/质检/仓储”）键的对象")
    else:
        raise ValueError("JSON 顶层必须是数组或对象")
    return [(name, rows) for name, rows in tables if rows]


# ------------------------------------------------------------------ 映射与转换

def map_headers(records: list[dict]) -> dict[str, str]:
    """表头 → 规范字段 的映射 {中文表头: 规范字段}。"""
    mapping = {}
    used = set()
    for header in records[0].keys():
        nh = _norm_header(header)
        for field, aliases in ALIASES.items():
            if field in used:
                continue
            if nh in [_norm_header(a) for a in aliases]:
                mapping[header] = field
                used.add(field)
                break
    return mapping


def detect_type(sheet_name: str, mapping: dict) -> Optional[str]:
    """判断这个 sheet 属于生产/质检/仓储哪类。"""
    name = sheet_name.lower()
    for ttype, keywords in TYPE_KEYWORDS.items():
        if any(k in name for k in keywords):
            return ttype
    # 按已映射字段投票
    votes = {"production": 0, "quality": 0, "warehouse": 0}
    for field in mapping.values():
        for ttype, cols in TABLE_COLUMNS.items():
            if field in cols:
                votes[ttype] += 1
    best = max(votes, key=votes.get)
    return best if votes[best] > 0 else None


def item_spec(config: QCDailyConfig, product: str, item: str) -> dict:
    """从配置取该产品该项目的限值/单位/方向/标准/出处。

    ★ 版本门禁：当前执行版限值未人工核对时，products 里的 GB 1350—2025 数值只是预置副本，
    禁止用它补齐导入数据 —— 否则就是「按新版判旧货」。此时只返回单位/方向/出处等元数据，
    limit 留空，由人工核对后回填。
    """
    for pname, spec in config.products.items():
        if pname == product or pname in product or product in pname:
            items = spec.get("items", {})
            if item in items:
                found = dict(items[item])
                if not config.active_limits_verified:
                    found.pop("limit", None)
                return found
    return {}


# ---------------------------------------------------------------- 判定方向
# 「指标方向」决定 compare() 怎么比，写反了整批判反，而且两个方向的后果不一样：
#   下限型（≥，如出糙率/整精米率）：判反＝把好粮当坏粮 → 赔钱
#   上限型（≤，如水分/杂质/黄粒米/谷外糙米）：判反＝把超标粮放行 → 害人
# 表里那一列是化验室手填的，一张表所有项目共用一列、模板统一填「≥」的情况真实存在
# （2026-11-09 演示表就是这么写的：黄粒米 0.0%、谷外糙米 0.4% 全被判不合格）。
# 所以口径定为：**标准配置里有方向就以配置为准**；表里写反了按标准判，但必须留痕告知。
_DIR_ALIASES = {
    "≤": "≤", "<=": "≤", "不大于": "≤", "不超过": "≤", "上限": "≤", "以下": "≤",
    "≥": "≥", ">=": "≥", "不小于": "≥", "不低于": "≥", "下限": "≥", "以上": "≥",
}


def _norm_direction(v) -> str:
    s = re.sub(r"[\s\u00a0\u3000]+", "", str(v or ""))
    return _DIR_ALIASES.get(s, "")


def resolve_direction(sheet_dir, spec_dir, item: str, noted: set, notes: list) -> str:
    """定判定方向：标准配置 > 表内「指标方向」列。

    表内与标准冲突时以标准为准，并按项目（不是按行）提示一次，避免刷屏。
    """
    s = _norm_direction(sheet_dir)
    a = _norm_direction(spec_dir)
    if a and s and s != a:
        if item not in noted:
            noted.add(item)
            notes.append(
                f"⚠️ 判定方向冲突：「{item}」表里「指标方向」写 {s}，按标准应为 {a}，已按 {a} 判定。"
                f"请让化验室核对该列——同一份数据若按表里的方向算，结论正好相反。")
        return a
    return a or s


def _skip_note(line_no: int, batch_id, item, raw) -> str:
    """一行被跳过时的提示。必须分清「公式没存结果」与「写了但读不懂」——
    两种原因不同、补救动作也不同（前者去 Excel 重算保存，后者改写法）。"""
    who = f"{_to_text(batch_id)}×{_to_text(item)}"
    if _is_formula_marker(raw):
        return (f"⚠️ 数据第 {line_no} 行 · {who} 的实测值是公式「{_formula_expr(raw)}」"
                "但 Excel 没存计算结果，这一行没进判定（其余行照跑）")
    return (f"⚠️ 数据第 {line_no} 行 · {who} 实测值「{_to_text(raw)}」不是数字"
            "（含比较符 / 写了未检出 / 格式认不出），这一行没进判定（其余行照跑）")


def convert(sheet_name: str, records: list[dict], ttype: str, config: QCDailyConfig
            ) -> tuple[list[dict], list[str]]:
    """把原始行转成规范行。宽表质检自动翻转。返回 (规范行列表, 提示信息)。"""
    mapping = map_headers(records)
    notes: list[str] = []
    rows: list[dict] = []

    # ---- 质检宽表：有批次列、无 item/value 列，但有多个"检测项目"列 ----
    # 列名按归一化后匹配（'酸价(mg/g)' → '酸价'），项目名取归一化后的干净名字
    norm_to_item = {_norm_header(i): i for i in KNOWN_ITEMS}
    alias = _item_alias_map()
    item_cols = {}  # {原始表头: 干净项目名}
    seen_items: dict[str, str] = {}   # 干净项目名 → 第一个认领它的表头
    for h in records[0].keys():
        nh = _norm_header(h)
        item = norm_to_item.get(nh) or alias.get(nh)
        if not item:
            continue
        if item in seen_items:
            # 两列指向同一指标（如同时有「水分」「水分含量」）：只认第一列。
            # 否则同批同日同项目会出现两条记录，被 DUP_QC 整单拦下，反而更难查。
            notes.append(f"⚠️ 列「{h}」与「{seen_items[item]}」都指向「{item}」，只认前者")
            continue
        seen_items[item] = h
        item_cols[h] = item
    is_wide_quality = (ttype == "quality" and "item" not in mapping.values()
                       and "value" not in mapping.values() and item_cols)
    # 一列项目名都认不出来时，不能静默走长表分支变成 0 条记录——
    # 那样最后只会报一句"日期不匹配"，把人指去改日期（2026-11-08 实测）
    if not is_wide_quality and ttype == "quality" and "item" not in mapping.values() \
            and "value" not in mapping.values() and records:
        guess = [str(h) for h in list(records[0].keys())[:12]]
        notes.append("⛔ 没认出任何检测项目列；当前表头是：" + "、".join(guess))
        notes.append("下一步：把检测项目写进表头（如「水分含量」「出糙率」），或改成一行一项的长表")
    if is_wide_quality:
        notes.append(f"识别为宽表质检格式，已自动翻转 {len(item_cols)} 个检测项目列")
        dir_noted: set = set()   # 方向冲突提示按项目去重，别每行刷一遍
        batch_h = next((h for h, f in mapping.items() if f == "batch_id"), None)
        prod_h = next((h for h, f in mapping.items() if f == "product"), None)
        # 化验室表里日期那一列常就写「日期」，它会被映射成 occurred_at；
        # 宽表质检只有一个日期列，认它就行，不必死等 tested_at
        date_h = (next((h for h, f in mapping.items() if f == "tested_at"), None)
                  or next((h for h, f in mapping.items() if f == "occurred_at"), None))
        insp_h = next((h for h, f in mapping.items() if f == "inspector"), None)
        # 化验室送检表常在右侧「系统判定区」为每个检测项目单独带限值列
        # （如「出糙率限值」「整精米率限值」）。表内自带的限值以表为准：
        # 出处可逐行追溯，也不受 config 里 active_limits_verified 版本门禁影响。
        # 没有这些列时完全回落原来的 item_spec 行为，向后兼容。
        per_item_limit: dict[str, str] = {}
        for h in records[0].keys():
            nh = _norm_header(h)
            for item in set(item_cols.values()):
                if nh == _norm_header(item) + "限值":
                    per_item_limit[item] = h
        dir_h = next((h for h, f in mapping.items() if f == "direction"), None)
        std_h = next((h for h, f in mapping.items() if f == "standard"), None)
        src_h = next((h for h, f in mapping.items() if f == "limit_source"), None)
        if not batch_h:
            notes.append("⛔ 宽表缺少批次列，无法导入")
            return [], notes
        for line_no, rec in enumerate(records, start=1):
            batch_id = _to_text(rec.get(batch_h))
            if not batch_id:
                continue
            product = _to_text(rec.get(prod_h)) if prod_h else ""
            tested_at = ("" if _is_formula_marker(rec.get(date_h))
                         else normalize_datetime(rec.get(date_h))) if date_h else ""
            inspector = _to_text(rec.get(insp_h)) if insp_h else ""
            for col, item in item_cols.items():
                raw = rec.get(col)
                val = _to_float(raw)
                if val is None:
                    # 空格子 = 未测该项目，静默跳过；写了却读不懂 = 真值未知，绝不改数
                    if raw is not None and str(raw).strip():
                        notes.append(_skip_note(line_no, batch_id, item, raw))
                    continue
                spec = item_spec(config, product, item)
                lim_col = per_item_limit.get(item)
                row_limit = _to_limit_float(rec.get(lim_col)) if lim_col else None
                rows.append({
                    "batch_id": batch_id, "product": product, "item": item, "value": val,
                    "unit": spec.get("unit", ""),
                    "direction": resolve_direction(
                        (_to_text(rec.get(dir_h)) if dir_h else ""),
                        spec.get("direction", ""), item, dir_noted, notes),
                    "limit_value": row_limit if row_limit is not None else spec.get("limit"),
                    "standard": (_to_text(rec.get(std_h)) if std_h else "")
                                or spec.get("standard", ""),
                    "limit_source": (_to_text(rec.get(src_h)) if src_h else "")
                                    or spec.get("limit_source", ""),
                    "tested_at": tested_at, "inspector": inspector, "lab": "",
                })
        return rows, notes

    # ---- 常规长表 ----
    dir_noted: set = set()
    for line_no, rec in enumerate(records, start=1):
        row = {col: None for col in TABLE_COLUMNS[ttype]}
        for header, field in mapping.items():
            if field in row:
                row[field] = rec.get(header)
        raw_value = row.get("value") if ttype == "quality" else None
        # 数值/日期/文本归一（限值列允许带 ≤/≥，实测值列不允许）
        for f in ("planned_qty", "actual_qty", "value", "limit_value", "qty", "temp", "humidity"):
            if f in row:
                row[f] = (_to_limit_float(row[f]) if f == "limit_value"
                          else _to_float(row[f]))
        for f in ("started_at", "finished_at", "tested_at", "occurred_at"):
            if f in row:
                # 日期列是公式且没存结果 → 当空值，不能把占位串当日期带进 dates
                row[f] = "" if _is_formula_marker(row[f]) else normalize_datetime(row[f])
        for f in ("batch_id", "product", "line", "item", "unit", "direction", "standard",
                  "limit_source", "inspector", "lab", "warehouse", "event"):
            if f in row:
                row[f] = _to_text(row[f])

        # 项目名可能是简称（「水分」），换成词典标准名（「水分含量」），
        # 否则 item_spec 查不到限值、或者被当成不认识的项目
        if ttype == "quality" and row.get("item"):
            row["item"] = normalize_item(row["item"])

        # 实测值读不懂（>5.0 / ＜0.5 / 十三点二 / 未检出）：真值未知。
        # 绝不写 NULL 进库（会崩在 models.compare），该行不判、其余照跑（夜测 S1/S6）。
        if ttype == "quality" and row.get("value") is None:
            if raw_value is not None and str(raw_value).strip():
                notes.append(_skip_note(line_no, row.get("batch_id"), row.get("item"), raw_value))
            continue

        if ttype == "quality":
            # 缺限值/单位/方向 → 按配置补齐（限值只来自配置，不猜）
            spec = item_spec(config, row.get("product", ""), row.get("item", ""))
            if row.get("limit_value") is None:
                row["limit_value"] = spec.get("limit")
            if not row.get("unit"):
                row["unit"] = spec.get("unit", "")
            # 方向不能只「空则补」：表里写了但写反，同样得按标准纠正（2026-11-09 事故）
            row["direction"] = resolve_direction(
                row.get("direction"), spec.get("direction", ""),
                row.get("item", ""), dir_noted, notes)
            if not row.get("standard"):
                row["standard"] = spec.get("standard", "")
            if not row.get("limit_source"):
                row["limit_source"] = spec.get("limit_source", "")
            if row.get("limit_value") is None:
                reason = (f"当前执行版 {config.standard_version} 限值待人工核对，禁止用 2025 预置值补齐"
                          if not config.active_limits_verified else
                          "请把该产品/项目加进 qc_daily_config.json 的 products")
                notes.append(f"⚠️ {row.get('batch_id')}×{row.get('item')} 未取到限值（{reason}）")
        if not row.get("batch_id"):
            continue
        rows.append(row)
    return rows, notes


# ------------------------------------------------------------------ 写入 SQLite

def _ensure_tables(conn: sqlite3.Connection) -> None:
    for ttype, cols in TABLE_COLUMNS.items():
        col_defs = ", ".join(f'"{c}" TEXT' for c in cols)
        conn.execute(f"CREATE TABLE IF NOT EXISTS {ttype} ({col_defs})")


def write_to_db(conn: sqlite3.Connection, ttype: str, rows: list[dict]) -> int:
    """写入（同日期先删后插，保证重复导入可刷新）。"""
    if not rows:
        return 0
    date_field = DATE_FIELD[ttype]
    dates = {r[date_field][:10] for r in rows if r.get(date_field)}
    for d in dates:
        conn.execute(f"DELETE FROM {ttype} WHERE substr({date_field}, 1, 10) = ?", (d,))
    cols = TABLE_COLUMNS[ttype]
    placeholders = ", ".join("?" for _ in cols)
    conn.executemany(
        f"INSERT INTO {ttype} ({', '.join(cols)}) VALUES ({placeholders})",
        [tuple(r.get(c) for c in cols) for r in rows])
    conn.commit()
    return len(rows)


# ------------------------------------------------------------------ 主入口

def import_path(path: str, db_path: str, config: QCDailyConfig,
                dry_run: bool = False) -> dict:
    """导入单个文件（或目录下所有文件）。返回汇总统计。"""
    files = []
    if os.path.isdir(path):
        for name in sorted(os.listdir(path)):
            if name.lower().endswith((".xlsx", ".xlsm", ".csv", ".json")):
                files.append(os.path.join(path, name))
    else:
        files.append(path)

    summary = {"files": [], "rows": {"production": 0, "quality": 0, "warehouse": 0},
               "dates": set(), "notes": [], "errors": []}
    conn = None if dry_run else sqlite3.connect(db_path)
    if conn:
        _ensure_tables(conn)

    try:
        for f in files:
            try:
                sheets = read_tabular(f)
            except Exception as e:
                summary["errors"].append(f"{os.path.basename(f)}: {e}")
                continue
            for sheet_name, records in sheets:
                if not records:
                    continue
                mapping = map_headers(records)
                ttype = detect_type(f"{os.path.basename(f)} {sheet_name}", mapping)
                if not ttype:
                    summary["notes"].append(
                        f"{os.path.basename(f)}[{sheet_name}] 无法识别表类型，已跳过"
                        f"（表头: {list(records[0].keys())[:8]}）")
                    continue
                rows, notes = convert(sheet_name, records, ttype, config)
                summary["notes"].extend(f"{os.path.basename(f)}[{sheet_name}] {n}" for n in notes)
                dates = {r[DATE_FIELD[ttype]][:10] for r in rows if r.get(DATE_FIELD[ttype])}
                summary["dates"].update(dates)
                summary["rows"][ttype] += len(rows)
                summary["files"].append({"file": os.path.basename(f), "sheet": sheet_name,
                                         "type": ttype, "rows": len(rows),
                                         "mapped": {h: f for h, f in mapping.items()},
                                         "dates": sorted(dates)})
                if conn and rows:
                    write_to_db(conn, ttype, rows)
    finally:
        if conn:
            conn.close()
    summary["dates"] = sorted(summary["dates"])
    return summary


def print_summary(summary: dict, db_path: str, dry_run: bool) -> None:
    say("=" * 56)
    say("数据导入预览（dry-run，未写库）" if dry_run else "数据导入完成")
    say("=" * 56)
    for f in summary["files"]:
        say(f"  {f['file']} [{f['sheet']}] → {f['type']} 表，{f['rows']} 行，"
            f"日期 {', '.join(f['dates']) or '无'}")
        say(f"      表头映射: {f['mapped']}")
    for n in summary["notes"]:
        say(f"  提示: {n}")
    for e in summary["errors"]:
        say(f"  ⛔ 错误: {e}")
    say("-" * 56)
    say(f"  合计: 生产 {summary['rows']['production']} 行 ｜ "
        f"质检 {summary['rows']['quality']} 行 ｜ 仓储 {summary['rows']['warehouse']} 行")
    say(f"  覆盖日期: {', '.join(summary['dates']) or '无'}")
    if not dry_run:
        say(f"  已写入数据库: {db_path}")
        say("")
        say("下一步（把数据源切到 SQL）：")
        say('  1. 编辑 qc_daily_config.json → "source": {"type": "sql"}（dsn 默认 qc_source.db）')
        say("  2. python -m qc_daily run --date 上面列表中的日期")
        say("  3. python -m qc_daily web --scheduler    # 网站用上真实数据")
    else:
        say("确认映射无误后，去掉 --dry-run 重新执行即正式导入。")
