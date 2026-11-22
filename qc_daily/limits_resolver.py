# -*- coding: utf-8 -*-
"""米粮（稻谷验收）限值解析器 · 唯一取值入口 resolve_limit()

限值唯一来源：corpus/limits.json → GB1350-2009（当前执行版）→ 表格 → 表1/表2 → 质量要求。
本模块对该文件只读 —— 不写回、不覆盖、不格式化。

三类指标统一由 resolve_limit() 处理：
  ① 下限分档型「各等级」        → 按等级取表达式（如 "≥79.0"），解析方向与数值；出糙率带「是否定等指标」；
  ② 固定上限型「指标方向+限值」→ 直接取 "≤" / 1.0；
  ③ 感官型「要求」              → 非数值，标记人工确认项，不参与数值判定。

★ 版本纪律（2026-11-11 切版）：解析出的数值属于 GB 1350—2009，即当前执行版。
  该版限值已于 2026-11-11 对原文扫描件 180dpi 渲染逐格核对、经小崔复核签字后入库，
  配置 qc_daily_config.json 的 standard_version 与之保持一致。
  GB 1350—2025 是 2027-01-01 才实施的预置版，仍留在 limits.json 里供换版与历史追溯，
  但不得用于判 2026 年的货。换版时改下面 STANDARD_KEY 一处即可（见
  docs/GB1350_版本差异_2009vs2025.md ⑤-B）。
"""
from __future__ import annotations

import json
import os
import re

CORPUS_LIMITS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "corpus", "limits.json")
STANDARD_KEY = "GB1350-2009"   # ⚠️ 2027-01-01 换 2025 版时只改这一行 + config 的 standard_version
LIMITS_REF = "corpus/limits.json → GB1350-2009 → 表格 → 表1/表2 → 质量要求"
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


def _std_key(name: str) -> str:
    """标准号归一成 limits.json 的键形态：'GB 1350—2009' → 'GB1350-2009'。"""
    return (re.sub(r"[\s\u00a0\u3000]", "", str(name or ""))
            .replace("—", "-").replace("－", "-").upper())


def _table_for(product: str) -> str:
    """产品名 → 表号。化验室表里写的是「中晚籼稻谷」「粳糯稻谷」这类别名，
    不是 PRODUCT_TABLES 的键，按「粳 / 籼」判一下，别因为名字对不上就查不到。"""
    p = str(product or "").strip()
    if p in PRODUCT_TABLES:
        return PRODUCT_TABLES[p]
    if "粳" in p:
        return "表2"
    if "籼" in p or "稻谷" in p:
        return "表1"
    return ""


def limits_by_version(limits_doc: dict, item: str, product: str,
                      grade: str = DEFAULT_GRADE) -> dict:
    """同一指标在各版本里的取值：{'GB1350-2009': {...}, 'GB1350-2025': {...}}。

    只读语料；某版没有这一项 / 这一档写「—」就不出现在结果里（不猜、不补）。
    """
    table_key = _table_for(product)
    out: dict = {}
    if not table_key:
        return out
    for key, std in (limits_doc or {}).items():
        if not isinstance(std, dict) or "表格" not in std:
            continue
        spec = (((std.get("表格") or {}).get(table_key) or {})
                .get("质量要求", {}).get(item))
        if not spec:
            continue
        try:
            r = resolve_limit(spec, grade=grade)
        except (KeyError, ValueError):
            continue
        if r.get("numeric"):
            out[_std_key(std.get("标准号", key))] = r
    return out


def version_conflict(limits_doc: dict, used_std: str, active_std: str, item: str,
                     product: str, value, direction: str = "",
                     grade: str = DEFAULT_GRADE) -> str:
    """表内写的那一版与当前执行版，对同一指标的限值不一样时返回一句话，否则空串。

    夜测 2026-11-12 r04 严重：表里写 `GB 1350—2025` / 谷外糙米 `≤4.0`，实测 3.0，
    系统照表内限值判「合格」——而本版（2009）是 ≤2.0，这一项其实不合格。
    两版数值都在 limits.json 里，这个比对本来就会做，只是此前没人做。

    ⚠️ 只在**结论会变**时才算冲突：两版都合格 / 都不合格的项，不必吓人。
    """
    u, a = _std_key(used_std), _std_key(active_std)
    if not u or not a or u == a:
        return ""
    lims = limits_by_version(limits_doc, item, product, grade=grade)
    ru, ra = lims.get(u), lims.get(a)
    if not ru or not ra:
        return ""                      # 有一版取不到值：不能凭空说"两版不同"
    if ru["expr"] == ra["expr"]:
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    d = direction or ru.get("direction") or ra.get("direction")
    from .models import compare
    v_used = compare(v, ru["limit"], d)
    v_active = compare(v, ra["limit"], d)
    if v_used == v_active:
        return ""                      # 两版结论一样，不构成风险
    return (f"表内 {used_std} 给 {ru['expr']}{ru.get('unit', '')}，"
            f"当前执行 {active_std} 给 {ra['expr']}{ra.get('unit', '')}；"
            f"本次实测 {v:g}：按表内那版判「{'合格' if v_used else '不合格'}」，"
            f"按本版判「{'合格' if v_active else '不合格'}」")


def build_products(limits_doc: dict, grade: str = DEFAULT_GRADE) -> dict:
    """生成 products 段：籼稻谷←表1、粳稻谷←表2；数值全部来自语料，零手抄。"""
    std = limits_doc[STANDARD_KEY]
    standard = std["标准号"]                      # 标准号取自语料，不凭印象写
    out = {}
    for product, table_key in PRODUCT_TABLES.items():
        table = std["表格"][table_key]
        page = _page_ref(table.get("出处", ""))
        # 「等外」不是数字等别，别拼成「第等外等」这种没人看得懂的出处
        # （夜测 2026-11-15 白天复验发现）。
        grade_ref = "等外" if grade == "等外" else f"第{grade}等"
        items, manual_checks, not_required = {}, [], []
        for name, spec in table["质量要求"].items():
            try:
                r = resolve_limit(spec, grade=grade)
            except ValueError as e:
                # 某一档某一项原表写「—」（如等外的整精米率）：只让这一项退出数值判定，
                # 不能让整档构建失败、把同档其余六项一起说成「原表没给数值」
                # （夜测 2026-11-15 r01 严重②）。KeyError 不在此列：整个等别不存在
                # 时仍要抛，免得把「查无此档」伪装成「这一档全不要求」。
                not_required.append({
                    "item": name, "grade": grade, "standard": standard,
                    "reason": str(e),
                    "limit_source": f"{standard} {table_key} {grade_ref} {page}",
                })
                continue
            if r["kind"] == "graded_lower":
                source = f"{standard} {table_key} {grade_ref} {page}"
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
            "limits_basis": f"{standard} 当前执行版（{LIMITS_REF}）；"
                            f"已逐格核对并经人工复核，standard_version 与本条一致",
            "items": items,
            "manual_checks": manual_checks,
            "not_required": not_required,
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
