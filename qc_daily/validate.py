# -*- coding: utf-8 -*-
"""检验记录录入错误自查 · 只标「待人工确认」，绝不判合格/不合格

化验室表里已经有公式判「合不合格」，但判不了「这个数是不是敲错了」。
本模块是公式前面的另一道关：

    录入自查（本层） → 先确认数是不是真的 → models.compare() 再判合不合格

★ 铁律（后续改动不许破）：
  1. 只读：不修改任何数值、不写回源记录、不碰公司真实表格；
  2. 不改写 verdict，也不调用 compare()；本层不产出任何合格/不合格结论；
  3. 不自动放行、不自动判不合格 —— 每条输出的状态都只是「待人工确认」；
  4. 安全指标（镉、无机砷、黄曲霉毒素等 ≤ 上限型）数值读不出来或符号不规整，
     一律「无法判定，转人工」，不许猜、不许把 < / ≤ 之类符号剥掉硬凑一个数字。

只依赖标准库。入参可以是 dict 行（importer 的输出），也可以是 QCRecord 对象
（collectors 的输出），两种都只读。

规则清单（Suspect.codes 用的就是这些代号，日报里可据此回溯）：

    RANGE            ① 值域          百分类 < 0 或 > 100
    MAGNITUDE_JUMP   ② 量级跳变      同项目相邻批次两数相差 ≥ 10 倍
    DECIMAL_SHIFT    ③ 小数位异常    与同项目历史中位数相差约 10 倍（历史需 ≥ 3 条）
    CROSS_COLUMN     ④ 跨列逻辑      同批次「整精米率 > 出糙率」
    DUPLICATE        ⑤ 重复          同批次同项目出现 ≥ 2 行
    MISSING          ⑤ 漏填          batch_id / item / value 任一为空
    UNREADABLE       安全指标兜底    数值读不出来或方向符号不规整 → 无法判定，转人工

接口与返回：
    validate_records(records) -> list[Suspect]
    入参支持 dict 行或 QCRecord 对象；同一个「批次 × 项目」合并成一条 Suspect，
    多条规则命中就把多条大白话原因拼进 reason；无疑点返回空表 [] 而不是 None，
    方便模板直接写 meta.get("suspects") or []。
    Suspect 是不可变对象（frozen），to_dict() 带「建议动作」与 status 字段。

接入位置：
    agent.run() 在采集后自动执行，疑点经 base_meta["suspects"] 交给
    templates.render_html()，在「一、合格率分析」之前渲染「⚠️ 待人工复核」；
    控制台同步打印疑点条数。疑点不写回源表、不进 verdict、不构成放行结论。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from statistics import median
from typing import Any

__all__ = ["Suspect", "validate_records", "STATUS"]

STATUS = "待人工确认"

#: 必填项：缺一样就没法确认这条记录是不是真的
REQUIRED_FIELDS = ("batch_id", "item", "value")
_FIELD_CN = {"batch_id": "批次号", "item": "检测项目", "value": "实测值"}

#: 污染物 / 真菌毒素等安全指标（都是 ≤ 上限型）
#: 按「项目名包含关键词」匹配：化验室表里写法不统一（如「镉(以 Cd 计)」）；
#: 日后新增安全指标只需往这个元组里加词，不必改任何规则代码。
SAFETY_KEYWORDS = ("镉", "砷", "铅", "汞", "铬", "黄曲霉", "毒素", "赭曲霉",
                   "脱氧雪腐", "玉米赤霉", "展青霉", "苯并", "农药残留",
                   "污染物", "真菌毒素", "重金属")

#: 百分类指标：优先看单位，没单位时按项目名里的说法兜一下
_PERCENT_UNITS = {"%", "％", "percent", "pct", "百分比"}
_PERCENT_HINTS = ("率", "含量", "水分", "杂质", "互混")

#: 规规矩矩的数字（只认半角数字，全角/带符号/带文字一律读不出来）
_NUM_RE = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$")

_ACTION_HINT = {
    "RANGE": "翻原始记录核对这个数和单位（百分类应在 0–100 之间）",
    "MAGNITUDE_JUMP": "对照前后批次的原始记录，确认小数点位置",
    "DECIMAL_SHIFT": "对照同项目历史记录，确认是不是多敲/少敲了一位小数",
    "CROSS_COLUMN": "同时复核出糙率、整精米率两列，确认有没有串行",
    "DUPLICATE": "核对是否重复录入，确认保留哪一条",
    "MISSING": "补查原始记录，把缺的字段补齐",
    "UNREADABLE": "查原始记录或仪器导出值，人工确认后再录入规范数字",
}


@dataclass(frozen=True)
class Suspect:
    """一条「数可能敲错了」的疑点。frozen：连疑点本身都不许被改。

    reason 一律大白话；status 恒为「待人工确认」，没有合格/不合格字段。
    """

    batch_id: str
    item: str
    value: Any
    reason: str
    suggestion: str
    codes: tuple[str, ...] = ()
    product: str = ""
    unit: str = ""
    status: str = STATUS

    @property
    def 建议动作(self) -> str:
        """suggestion 的中文别名，方便按需求里的字段名取用。"""
        return self.suggestion

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "item": self.item,
            "value": self.value,
            "reason": self.reason,
            "建议动作": self.suggestion,
            "codes": list(self.codes),
            "product": self.product,
            "unit": self.unit,
            "status": self.status,
        }


# ------------------------------------------------------------------ 只读工具

def _get(rec: Any, name: str, default: Any = "") -> Any:
    """dict 行 / dataclass 对象统一只读取值，绝不写回。"""
    if isinstance(rec, dict):
        return rec.get(name, default)
    return getattr(rec, name, default)


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _parse_number(v: Any) -> float | None:
    """只把规规矩矩的数字读成 float；带 < ≤ 等符号或文字一律返回 None（不猜）。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    s = str(v).strip()
    if not _NUM_RE.match(s):
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def _is_percent(item: str, unit: str) -> bool:
    u = unit.strip().lower()
    if u in _PERCENT_UNITS:
        return True
    if u:  # 明确写了 mg/kg、μg/kg 等其它单位，就不是百分类
        return False
    return any(h in item for h in _PERCENT_HINTS)


def _is_safety(item: str) -> bool:
    return any(k in item for k in SAFETY_KEYWORDS)


def _pick(items: dict, name: str):
    """先精确匹配项目名，再退而求其次找包含关系。"""
    if name in items:
        return items[name]
    for k, v in items.items():
        if name in k:
            return v
    return None


# ------------------------------------------------------------------ 主入口

def validate_records(records) -> list[Suspect]:
    """自查录入疑点，返回 Suspect 列表（无疑点时返回空表）。

    同一个「批次 × 项目」合并成一条疑点，多个规则命中就把多条大白话原因拼在一起，
    避免同一格被刷屏。传入的 records 从头到尾只读，不做任何赋值。
    """
    rows = list(records or [])
    meta: dict[tuple, dict] = {}
    reasons: dict[tuple, list[str]] = {}
    codes: dict[tuple, list[str]] = {}
    first_rec: dict[tuple, Any] = {}
    dup: dict[tuple, int] = {}

    def key(rec: Any, idx: int) -> tuple:
        b, it = _text(_get(rec, "batch_id")), _text(_get(rec, "item"))
        # 批次号为空时把行号带上，别把不同行的空格子误并成一条
        return (b, it) if b else ("", it, idx)

    def flag(k: tuple, rec: Any, code: str, reason: str) -> None:
        if k not in meta:
            meta[k] = {
                "batch_id": _text(_get(rec, "batch_id")),
                "item": _text(_get(rec, "item")),
                "value": _get(rec, "value", None),
                "product": _text(_get(rec, "product")),
                "unit": _text(_get(rec, "unit")),
            }
        bucket = codes.setdefault(k, [])
        if code not in bucket:
            bucket.append(code)
        reasons.setdefault(k, []).append(reason)

    # ---------- ⑤ 重复 / 漏填 / 读不出来（含安全指标转人工） ----------
    for idx, rec in enumerate(rows):
        k = key(rec, idx)
        dup[k] = dup.get(k, 0) + 1
        first_rec.setdefault(k, rec)

        missing = [f for f in REQUIRED_FIELDS if _is_blank(_get(rec, f, None))]
        if missing:
            flag(k, rec, "MISSING",
                 f"这条记录有必填项空着（{'、'.join(_FIELD_CN[f] for f in missing)}），"
                 f"数都没填全，没法确认它是不是真的")

        raw = _get(rec, "value", None)
        it = _text(_get(rec, "item"))
        if not _is_blank(raw) and _parse_number(raw) is None:
            if _is_safety(it):
                flag(k, rec, "UNREADABLE",
                     f"「{it}」是安全指标，实测值写成「{_text(raw)}」，数字读不出来或符号不规整；"
                     f"无法判定，转人工，不猜数、不剥掉符号硬凑")
            else:
                flag(k, rec, "UNREADABLE",
                     f"实测值写成「{_text(raw)}」，不是规规矩矩的数字，读不出来；"
                     f"无法判定，转人工核对原始记录")

        if _is_safety(it):
            direction = _text(_get(rec, "direction"))
            if direction not in ("≤", "<="):
                flag(k, rec, "UNREADABLE",
                     f"「{it}」是 ≤ 上限型安全指标，指标方向却写成「{direction or '空着'}」；"
                     f"符号不规整，无法判定，转人工")

    for k, n in dup.items():
        if n >= 2 and k[0]:
            flag(k, first_rec[k], "DUPLICATE",
                 f"同批次 {k[0]} 的「{k[1]}」出现了 {n} 行，可能重复录入；"
                 f"请人工确认保留哪一条，本层不删不改")

    # ---------- 数值行按项目归组（供 ①②③④ 用；重复行只留第一条） ----------
    by_item: dict[str, list[tuple[str, float, tuple, Any]]] = {}
    by_batch: dict[str, dict[str, tuple[float, tuple, Any]]] = {}
    seen_pair: set[tuple] = set()
    for idx, rec in enumerate(rows):
        it = _text(_get(rec, "item"))
        b = _text(_get(rec, "batch_id"))
        num = _parse_number(_get(rec, "value", None))
        if not it or num is None:
            continue
        k = key(rec, idx)
        if b:
            by_batch.setdefault(b, {}).setdefault(it, (num, k, rec))
        pair = (b, it)
        if pair in seen_pair:
            continue
        seen_pair.add(pair)
        by_item.setdefault(it, []).append((b, num, k, rec))

    # ---------- ① 值域：百分类必须落在 0–100 ----------
    for idx, rec in enumerate(rows):
        num = _parse_number(_get(rec, "value", None))
        if num is None:
            continue
        it = _text(_get(rec, "item"))
        if _is_percent(it, _text(_get(rec, "unit"))) and (num < 0 or num > 100):
            flag(key(rec, idx), rec, "RANGE",
                 f"「{it or '这个指标'}」是百分类，正常得在 0–100 之间，这里填了 {num:g}，"
                 f"负数或超过 100 基本就是小数点或单位敲错了")

    # ---------- ② 量级跳变：同项目相邻批次相差 ≥10 倍 ----------
    for item, group in by_item.items():
        for (b1, v1, k1, r1), (b2, v2, k2, r2) in zip(group, group[1:]):
            if v1 <= 0 or v2 <= 0:
                continue
            ratio = max(v1, v2) / min(v1, v2)
            if ratio + 1e-9 < 10:
                continue
            detail = (f"「{item}」相邻批次 {b1 or '批次号空'} 填 {v1:g}、"
                      f"{b2 or '批次号空'} 填 {v2:g}，两个数相差 {ratio:.1f} 倍（≥10 倍），"
                      f"通常是小数点敲错或单位写错；两条都标疑，请人工确认哪一条敲错，本层不猜")
            flag(k1, r1, "MAGNITUDE_JUMP", detail)
            flag(k2, r2, "MAGNITUDE_JUMP", detail)

    # ---------- ③ 小数位异常：与同项目历史中位数整整差一个数量级 ----------
    for item, group in by_item.items():
        vals = [v for _, v, _, _ in group if v > 0]
        if len(vals) < 3:  # 历史不足 3 条，中位数没代表性，不硬判
            continue
        med = median(vals)
        if med <= 0:
            continue
        for _, v, k, rec in group:
            if v <= 0:
                continue
            ratio = v / med
            if 9.0 <= ratio <= 11.0 or 0.09 <= ratio <= 0.11:
                flag(k, rec, "DECIMAL_SHIFT",
                     f"「{item}」同项目历史中位数是 {med:g}，这条填了 {v:g}，"
                     f"正好差了约 10 倍（一整个数量级），像是小数点挪了一位")

    # ---------- ④ 跨列逻辑：同批次整精米率不可能大于出糙率 ----------
    for b, items in by_batch.items():
        husked, head = _pick(items, "出糙率"), _pick(items, "整精米率")
        if husked is None or head is None:
            continue
        v_h = husked[0]
        v_z, k_z, r_z = head
        if v_z > v_h:
            flag(k_z, r_z, "CROSS_COLUMN",
                 f"同批次 {b} 里「整精米率」填了 {v_z:g}%，比「出糙率」的 {v_h:g}% 还高；"
                 f"整精米是从糙米里碾出来的，不可能比出糙率还多，多半是两列串行或敲错")

    # ---------- 汇总：一个「批次 × 项目」一条疑点 ----------
    out: list[Suspect] = []
    for k, info in meta.items():
        cs = tuple(codes.get(k, []))
        hints = "；".join(_ACTION_HINT.get(c, "对照原始记录核对") for c in cs)
        out.append(Suspect(
            batch_id=info["batch_id"],
            item=info["item"],
            value=info["value"],
            reason="；".join(reasons.get(k, [])),
            suggestion=(f"待人工确认：{hints}；"
                        f"本层只标疑点，不改数值、不改合格判定、不自动放行。"),
            codes=cs,
            product=info["product"],
            unit=info["unit"],
        ))
    out.sort(key=lambda s: (s.batch_id, s.item))
    return out
