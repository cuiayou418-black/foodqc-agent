# -*- coding: utf-8 -*-
"""米粮（稻谷验收）限值解析器 · 唯一取值入口 resolve_limit()

限值唯一来源：corpus/limits.json → GB1350-2025 → 表格 → 表1/表2 → 质量要求。
本模块对该文件只读 —— 不写回、不覆盖、不格式化。

三类指标统一由 resolve_limit() 处理：
  ① 下限分档型「各等级」        → 按等级取表达式（如 "≥79.0"），解析方向与数值；出糙率带「是否定等指标」；
  ② 固定上限型「指标方向+限值」→ 直接取 "≤" / 1.0；
  ③ 感官型「要求」              → 非数值，标记人工确认项，不参与数值判定。

★ 版本纪律：解析出的数值属于 GB 1350—2025（语料记载 2027-01-01 实施）的预置数据。
  当前执行版由 qc_daily_config.json 的 standard_version 指定（GB 1350—2009，限值待核对）。
  预置值不得冒充当前执行版限值用于放行，见 standard_versions / standard_warning。
"""
from __future__ import annotations

import json
import os
import re

CORPUS_LIMITS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "corpus", "limits.json")
STANDARD_KEY = "GB1350-2025"
PRESET_REF = "corpus/limits.json → GB1350-2025 → 表格 → 表1/表2 → 质量要求"
DEFAULT_GRADE = "3"          # 语料「定等规则」：3 等为中等
PRODUCT_TABLES = {"籼稻谷": "表1", "粳稻谷": "表2"}
MANUAL_NOTE = "人工确认项：感官型指标，不参与数值判定"


def load_limits(path: str = CORPUS_LIMITS) -> dict:
    """只读加载语料；调用方不得写回该文件。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_expr(expr: str) -> tuple[str, float]:
    """'≥77.0' / '<71.0' / '≤1.0' → (方向, 数值)；表达式必须来自语料原文。"""
    m = re.search(r"([≥≤><=]+)\s*(-?\d+(?:\.\d+)?)", str(expr))
    if not m:
        raise ValueError(f"限值表达式无法解析: {expr!r}")
    return m.group(1), float(m.group(2))


def _page_ref(page_text: str) -> str:
    """从语料「出处」里取页码引用（表2 跨页会保留「原文第5页+第6页」）。"""
    head = re.split(r"[（(]", page_text or "")[0]      # 去掉括号里的核对备注
    return re.sub(r"\s+", "", head).rstrip("+")


def resolve_limit(item_spec: dict, grade: str = DEFAULT_GRADE) -> dict:
    """统一取值：把「质量要求」里的一项解析成规范结构，数值只来自语料。"""
    unit = item_spec.get("单位", "")

    # ① 下限分档型：字段是「各等级」
    if "各等级" in item_spec:
        grades = item_spec["各等级"]
        if grade not in grades:
            raise KeyError(f"语料中没有第 {grade} 等限值，可选：{list(grades)}")
        expr = grades[grade]
        if str(expr).strip() == "—":
            raise ValueError(f"第 {grade} 等该项原表为「—」（不要求），不应参与判定")
        direction, limit = _parse_expr(expr)
        return {"kind": "graded_lower", "unit": unit, "direction": direction,
                "limit": limit, "expr": expr, "grade": grade, "numeric": True,
                "is_grading_item": bool(item_spec.get("是否定等指标", False))}

    # ② 固定上限型：字段是「指标方向」+「限值」
    if "指标方向" in item_spec and "限值" in item_spec:
        return {"kind": "fixed_upper", "unit": unit,
                "direction": item_spec["指标方向"], "limit": float(item_spec["限值"]),
                "expr": f"{item_spec['指标方向']}{item_spec['限值']}", "numeric": True}

    # ③ 感官型：字段是「要求」，不是数字
    if "要求" in item_spec:
        return {"kind": "sensory", "unit": unit, "direction": None, "limit": None,
                "requirement": item_spec["要求"], "numeric": False,
                "manual_check": True, "note": MANUAL_NOTE}

    raise ValueError(f"未识别的指标结构：{list(item_spec)}")


def build_products(limits_doc: dict, grade: str = DEFAULT_GRADE) -> dict:
    """生成 products 段：籼稻谷←表1、粳稻谷←表2；数值全部来自语料，零手抄。"""
    std = limits_doc[STANDARD_KEY]
    standard = std["标准号"]                      # 标准号取自语料，不凭印象写
    out = {}
    for product, table_key in PRODUCT_TABLES.items():
        table = std["表格"][table_key]
        page = _page_ref(table.get("出处", ""))
        items, manual_checks = {}, []
        for name, spec in table["质量要求"].items():
            r = resolve_limit(spec, grade=grade)
            if r["kind"] == "graded_lower":
                source = f"{standard} {table_key} 第{grade}等 {page}"
            else:
                source = f"{standard} {table_key} 质量要求·{name} {page}（各等级同值）"
            if r["numeric"]:
                items[name] = {
                    "unit": r["unit"], "direction": r["direction"], "limit": r["limit"],
                    "standard": standard, "limit_source": source,
                    "kind": r["kind"], "expr": r["expr"],
                    "is_grading_item": r.get("is_grading_item", False),
                }
            else:
                manual_checks.append({
                    "item": name, "unit": r["unit"], "direction": None, "limit": None,
                    "standard": standard, "limit_source": source,
                    "requirement": r["requirement"], "note": r["note"],
                })
        out[product] = {
            "table": table_key,
            "scope": table.get("适用范围", []),
            "grade": grade,
            "limits_basis": f"{standard} 预置（{PRESET_REF}），enabled=false；"
                            f"当前执行版以 standard_version 为准，限值待人工核对",
            "items": items,
            "manual_checks": manual_checks,
        }
    return out


if __name__ == "__main__":
    try:
        from .console import say          # 项目约定：GBK 控制台安全输出
    except ImportError:
        say = print
    doc = load_limits()
    say(f"# 标准号（取自语料）: {doc[STANDARD_KEY]['标准号']}")
    say(f"# 实施日期（取自语料）: {doc[STANDARD_KEY]['实施日期']}")
    say(f"# 版本提示（取自语料）: {doc[STANDARD_KEY]['版本提示']}")
    say(json.dumps(build_products(doc), ensure_ascii=False, indent=2))
