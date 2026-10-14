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
    "product":     ["产品", "产品名称", "品名", "油品", "产品类别", "product", "product_name"],
    "line":        ["产线", "生产线", "车间", "线别", "line", "workshop", "production_line"],
    "planned_qty": ["计划产量", "计划量", "计划", "planned", "planned_qty", "plan"],
    "actual_qty":  ["实际产量", "产量", "实际", "实际量", "actual", "actual_qty", "output"],
    "started_at":  ["开工时间", "开始时间", "生产日期", "投产日期", "start", "started_at",
                    "start_time", "production_date"],
    "finished_at": ["完工时间", "结束时间", "完工日期", "finish", "finished_at", "end_time"],
    # 质检
    "item":        ["检测项目", "项目", "指标", "检测指标", "item", "test_item", "indicator"],
    "value":       ["实测值", "检测值", "测定值", "结果", "测量值", "value", "result", "measured",
                    "test_result"],
    "unit":        ["单位", "unit", "units"],
    "direction":   ["方向", "指标方向", "direction"],
    "limit_value": ["限值", "标准限值", "限量", "标准值", "limit", "limit_value", "standard_limit"],
    "standard":    ["标准", "标准号", "依据标准", "执行标准", "standard", "std"],
    "tested_at":   ["检验日期", "检测日期", "检验时间", "检测时间", "tested", "tested_at",
                    "test_date", "检验日期时间"],
    "inspector":   ["检验员", "检测人", "检测员", "inspector", "tester"],
    "lab":         ["实验室", "检测机构", "检测单位", "lab", "laboratory"],
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
KNOWN_ITEMS = ["酸价", "过氧化值", "水分及挥发物", "不溶性杂质", "溶剂残留量", "烟点",
               "色泽", "透明度", "气味滋味", "含皂量", "冷冻试验", "加热试验",
               "相对密度", "折光指数", "碘值", "皂化值", "脂肪酸组成", "极性组分",
               "黄曲霉毒素B1", "铅", "游离羟基"]

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
    """表头归一化：去空格/小写，并剥离括号后缀（'计划产量(t)' → '计划产量'）。"""
    s = str(h or "").strip().lower().replace(" ", "").replace("　", "")
    s = re.sub(r"\([^)]*\)", "", s)
    return s


def _to_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    # 去掉单位后缀，如 "0.50 mg/g"、"≤3.0"、"未检出"
    for ch in ("<", ">", "≤", "≥", "="):
        s = s.lstrip(ch)
    try:
        return float(s)
    except ValueError:
        return None


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

def _read_xlsx(path: str) -> list[tuple[str, list[dict]]]:
    """返回 [(sheet名, [行dict, ...]), ...]。"""
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    sheets = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        header_idx = next((i for i, r in enumerate(rows)
                           if any(c is not None and str(c).strip() for c in r)), None)
        if header_idx is None:
            continue
        headers = [_to_text(h) for h in rows[header_idx]]
        records = []
        for r in rows[header_idx + 1:]:
            if not any(c is not None and str(c).strip() for c in r):
                continue
            records.append({headers[i] if i < len(headers) else f"col{i}": v
                            for i, v in enumerate(r) if i < len(headers)})
        sheets.append((ws.title, records))
    wb.close()
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
    headers = [h.strip() for h in rows[0]]
    records = [{headers[i] if i < len(headers) else f"col{i}": (r[i] if i < len(r) else "")
                for i in range(len(headers))}
               for r in rows[1:] if any(c.strip() for c in r)]
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
    """从配置取该产品该项目的限值/单位/方向/标准/出处。"""
    for pname, spec in config.products.items():
        if pname == product or pname in product or product in pname:
            items = spec.get("items", {})
            if item in items:
                return items[item]
    return {}


def convert(sheet_name: str, records: list[dict], ttype: str, config: QCDailyConfig
            ) -> tuple[list[dict], list[str]]:
    """把原始行转成规范行。宽表质检自动翻转。返回 (规范行列表, 提示信息)。"""
    mapping = map_headers(records)
    notes: list[str] = []
    rows: list[dict] = []

    # ---- 质检宽表：有批次列、无 item/value 列，但有多个"检测项目"列 ----
    # 列名按归一化后匹配（'酸价(mg/g)' → '酸价'），项目名取归一化后的干净名字
    norm_to_item = {_norm_header(i): i for i in KNOWN_ITEMS}
    item_cols = {}  # {原始表头: 干净项目名}
    for h in records[0].keys():
        nh = _norm_header(h)
        if nh in norm_to_item:
            item_cols[h] = norm_to_item[nh]
    is_wide_quality = (ttype == "quality" and "item" not in mapping.values()
                       and "value" not in mapping.values() and item_cols)
    if is_wide_quality:
        notes.append(f"识别为宽表质检格式，已自动翻转 {len(item_cols)} 个检测项目列")
        batch_h = next((h for h, f in mapping.items() if f == "batch_id"), None)
        prod_h = next((h for h, f in mapping.items() if f == "product"), None)
        date_h = next((h for h, f in mapping.items() if f == "tested_at"), None)
        insp_h = next((h for h, f in mapping.items() if f == "inspector"), None)
        if not batch_h:
            notes.append("⛔ 宽表缺少批次列，无法导入")
            return [], notes
        for rec in records:
            batch_id = _to_text(rec.get(batch_h))
            if not batch_id:
                continue
            product = _to_text(rec.get(prod_h)) if prod_h else ""
            tested_at = normalize_datetime(rec.get(date_h)) if date_h else ""
            inspector = _to_text(rec.get(insp_h)) if insp_h else ""
            for col, item in item_cols.items():
                val = _to_float(rec.get(col))
                if val is None:
                    continue  # 空格子跳过（未测该项目）
                spec = item_spec(config, product, item)
                rows.append({
                    "batch_id": batch_id, "product": product, "item": item, "value": val,
                    "unit": spec.get("unit", ""), "direction": spec.get("direction", "≤"),
                    "limit_value": spec.get("limit"), "standard": spec.get("standard", ""),
                    "limit_source": spec.get("limit_source", ""),
                    "tested_at": tested_at, "inspector": inspector, "lab": "",
                })
        return rows, notes

    # ---- 常规长表 ----
    for rec in records:
        row = {col: None for col in TABLE_COLUMNS[ttype]}
        for header, field in mapping.items():
            if field in row:
                row[field] = rec.get(header)
        # 数值/日期/文本归一
        for f in ("planned_qty", "actual_qty", "value", "limit_value", "qty", "temp", "humidity"):
            if f in row:
                row[f] = _to_float(row[f])
        for f in ("started_at", "finished_at", "tested_at", "occurred_at"):
            if f in row:
                row[f] = normalize_datetime(row[f])
        for f in ("batch_id", "product", "line", "item", "unit", "direction", "standard",
                  "limit_source", "inspector", "lab", "warehouse", "event"):
            if f in row:
                row[f] = _to_text(row[f])

        if ttype == "quality":
            # 缺限值/单位/方向 → 按配置补齐（限值只来自配置，不猜）
            spec = item_spec(config, row.get("product", ""), row.get("item", ""))
            if row.get("limit_value") is None:
                row["limit_value"] = spec.get("limit")
            if not row.get("unit"):
                row["unit"] = spec.get("unit", "")
            if not row.get("direction"):
                row["direction"] = spec.get("direction", "≤")
            if not row.get("standard"):
                row["standard"] = spec.get("standard", "")
            if not row.get("limit_source"):
                row["limit_source"] = spec.get("limit_source", "")
            if row.get("limit_value") is None:
                notes.append(f"⚠️ {row.get('batch_id')}×{row.get('item')} 未找到限值"
                             f"（请把该产品/项目加进 qc_daily_config.json 的 products）")
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
