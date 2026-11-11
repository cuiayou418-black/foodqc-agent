"""品控日报智能体 · 分析引擎

全部指标由代码计算，不经过任何模型：
- 合格率（总体 / 分产品 / 分项目 / 一次交验合格率）
- 缺陷分布（按检测项目聚合失败项次与占比）
- 异常批次（超标 / 接近限值 / 仓储环境超限，附原因与原始记录引用）
- 趋势分析（近 N 日合格率序列 + 分项目均值序列 + 斜率判定）
- 预警规则评估（配置驱动，命中即生成 Alert）

HistoryStore 以 JSONL 追加方式沉淀每日指标，是趋势分析与调度补跑的依据。
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

from .config import QCDailyConfig
from .models import (AbnormalBatch, Alert, CollectedData, DailyMetrics, ItemStat,
                     ItemTrendPoint, ProductStat, TrendPoint, ValidationIssue,
                     WarehouseRecord, fingerprint, iso_now)

_OPS = {
    "<":  lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">":  lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
}


# ------------------------------------------------------------------ 历史存储

class HistoryStore:
    """每日指标历史（JSONL 追加写）。

    每次日报成功生成后追加一条记录；趋势分析从这里取数。
    只追加、不修改 —— 与审计日志一样，保证历史不可篡改。
    """

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def append(self, entry: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def read_all(self) -> list:
        if not os.path.exists(self.path):
            return []
        entries = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def last_days(self, n: int) -> list:
        """最近 n 个**日期**（不是 n 条记录），按日期升序、同日只留最后一次。

        旧版 entries[-n:] 按条数截：同一天重跑 6 次，取到的 7 条里全是今天，
        前几天真实业务数据被挤出去，趋势塌成一个点、看不出下滑（夜测 11-10 r04）。
        夜测产物写进 history.jsonl 时这个坑尤其明显。
        """
        if n <= 0:
            return []
        by_date: dict[str, dict] = {}
        for e in self.read_all():
            d = str(e.get("report_date") or "").strip()
            if d:
                by_date[d] = e
        return [by_date[d] for d in sorted(by_date)[-n:]]


# ------------------------------------------------------------------ 指标计算

def compute_metrics(collected: CollectedData, config: QCDailyConfig,
                    history: list) -> DailyMetrics:
    """由原始记录计算日报全部指标。"""
    near_ratio = float(config.analysis.get("near_limit_ratio", 0.8))
    trend_days = int(config.analysis.get("trend_days", 7))
    wh_limits = config.warehouse_limits

    m = DailyMetrics(report_date=collected.report_date,
                     plant=config.report.get("plant", ""),
                     generated_at=iso_now())

    # ---------- 1. 生产与批次 ----------
    prod_by_batch = {r.batch_id: r for r in collected.production}
    m.batch_count = len(prod_by_batch)
    m.planned_qty = round(sum(r.planned_qty for r in prod_by_batch.values()), 2)
    m.actual_qty = round(sum(r.actual_qty for r in prod_by_batch.values()), 2)
    m.achievement = m.actual_qty / m.planned_qty if m.planned_qty else 0.0

    # ---------- 2. 质检：分产品 / 分项目 ----------
    products: dict[str, ProductStat] = {}
    items: dict[str, ItemStat] = {}
    qc_by_batch: dict[str, list] = {}
    fail_count = 0

    for r in collected.quality:
        qc_by_batch.setdefault(r.batch_id, []).append(r)
        p = products.setdefault(r.product, ProductStat(product=r.product))
        p.total_tests += 1
        if r.verdict == "不合格":
            p.fail_tests += 1
            fail_count += 1
        it = items.setdefault(r.item, ItemStat(item=r.item, unit=r.unit))
        it.total += 1
        if r.verdict == "不合格":
            it.fail += 1
            it.fail_batches.append(r.batch_id)
        # 与 templates.py:118 的「接近限值」同口径：只数未超标项（利用率 ≥ 阈值 且 < 1）。
        # 旧写法 r.ratio >= near_ratio 把超标项也算进来，同一份日报正文写 5、预警写 7
        # （夜测 2026-11-11 r03）。本文件 :178 的异常批次统计本来就用的这个口径。
        if near_ratio <= r.ratio < 1.0:
            m.near_limit_count += 1

    for batch_id, pr in prod_by_batch.items():
        p = products.setdefault(pr.product, ProductStat(product=pr.product))
        p.batches += 1
        p.planned_qty += pr.planned_qty
        p.actual_qty += pr.actual_qty
        fails = [r for r in qc_by_batch.get(batch_id, []) if r.verdict == "不合格"]
        if not qc_by_batch.get(batch_id):
            continue
        if not fails:
            p.qualified_batches += 1

    for p in products.values():
        p.pass_rate = (p.total_tests - p.fail_tests) / p.total_tests if p.total_tests else 0.0
        p.batch_pass_rate = p.qualified_batches / p.batches if p.batches else 0.0
        p.achievement = p.actual_qty / p.planned_qty if p.planned_qty else 0.0
        p.planned_qty = round(p.planned_qty, 2)
        p.actual_qty = round(p.actual_qty, 2)
    m.products = sorted(products.values(), key=lambda x: x.pass_rate)
    m.items = sorted(items.values(), key=lambda x: (-x.fail, x.item))

    m.total_tests = len(collected.quality)
    m.fail_tests = fail_count
    m.pass_rate = (m.total_tests - m.fail_tests) / m.total_tests if m.total_tests else 0.0
    tested_batches = [b for b in prod_by_batch if qc_by_batch.get(b)]
    qualified = sum(1 for b in tested_batches
                    if not any(r.verdict == "不合格" for r in qc_by_batch[b]))
    m.batch_pass_rate = qualified / len(tested_batches) if tested_batches else 0.0

    # ---------- 3. 缺陷分布 ----------
    m.defect_distribution = [
        {"item": it.item, "unit": it.unit, "fail": it.fail,
         "share": round(it.fail / fail_count, 4) if fail_count else 0.0,
         "fail_batches": it.fail_batches}
        for it in m.items if it.fail > 0
    ]

    # ---------- 4. 异常批次 ----------
    whs_by_batch: dict[str, list[WarehouseRecord]] = {}
    for w in collected.warehouse:
        whs_by_batch.setdefault(w.batch_id, []).append(w)

    m.warehouse_events = len(collected.warehouse)
    for w in collected.warehouse:
        if w.temp is not None and not (wh_limits["temp_min"] <= w.temp <= wh_limits["temp_max"]):
            m.warehouse_violations.append({
                "record_id": w.record_id, "batch_id": w.batch_id, "kind": "库温超限",
                "detail": f"{w.warehouse} 库温 {w.temp}℃（限 {wh_limits['temp_min']}~{wh_limits['temp_max']}℃）"})
        if w.humidity is not None and not (wh_limits["humidity_min"] <= w.humidity <= wh_limits["humidity_max"]):
            m.warehouse_violations.append({
                "record_id": w.record_id, "batch_id": w.batch_id, "kind": "湿度超限",
                "detail": f"{w.warehouse} 湿度 {w.humidity}%（限 {wh_limits['humidity_min']}~{wh_limits['humidity_max']}%）"})

    whs_violation_batches = {v["batch_id"] for v in m.warehouse_violations}

    abnormal: list[AbnormalBatch] = []
    for batch_id, pr in prod_by_batch.items():
        recs = qc_by_batch.get(batch_id, [])
        if not recs:
            continue
        fails = [r for r in recs if r.verdict == "不合格"]
        nears = [r for r in recs if near_ratio <= r.ratio < 1.0]
        if not fails and not nears and batch_id not in whs_violation_batches:
            continue
        reasons, fail_items = [], []
        for r in fails:
            fail_items.append(r.item)
            src = f" {r.limit_source}" if r.limit_source else ""
            reasons.append(f"{r.item}不合格：实测 {r.value:g}{r.unit} {r.direction} 限值 {r.limit_value:g}"
                           f"（利用率 {r.ratio:.0%}，依据 {r.standard}{src}）")
        for r in nears:
            reasons.append(f"{r.item}接近限值：实测 {r.value:g}{r.unit}，利用率 {r.ratio:.0%}")
        for v in m.warehouse_violations:
            if v["batch_id"] == batch_id:
                reasons.append(f"{v['kind']}：{v['detail']}")
        worst = max((r.ratio for r in recs), default=0.0)
        level = "critical" if fails else ("warning" if nears else "info")
        if batch_id in whs_violation_batches and level == "info":
            level = "warning"
        abnormal.append(AbnormalBatch(batch_id=batch_id, product=pr.product, line=pr.line,
                                      level=level, reasons=reasons, fail_items=fail_items,
                                      worst_ratio=worst))
    m.abnormal_batches = sorted(abnormal, key=lambda b: (-b.worst_ratio, b.batch_id))

    # ---------- 5. 趋势分析 ----------
    # 历史按日期去重（同一日期多次运行只取最后一次），再截取最近 trend_days-1 天
    by_date: dict[str, dict] = {}
    for e in history:
        if e.get("report_date") != m.report_date:
            by_date[e["report_date"]] = e
    hist_entries = [by_date[d] for d in sorted(by_date)][-(trend_days - 1):]
    today_entry = {
        "report_date": m.report_date, "pass_rate": round(m.pass_rate, 4),
        "total_tests": m.total_tests, "fail_tests": m.fail_tests,
        "abnormal_batch_count": len(m.abnormal_batches),
        "item_fail": {it.item: it.fail for it in m.items},
        "item_mean": _item_means(collected),
    }
    # 当日无条件追加在末尾时，补录/回溯日期（化验室常态）会排在最新日之后，
    # 网页趋势错序、Excel 导出却是对的（exporters.py 用了 sorted）——两处口径不一致。
    series = sorted(hist_entries + [today_entry],
                    key=lambda e: str(e.get("report_date", "")))
    m.trend_points = [TrendPoint(date=e["report_date"], pass_rate=e.get("pass_rate", 0.0),
                                 total=e.get("total_tests", 0), fail=e.get("fail_tests", 0),
                                 abnormal_batches=e.get("abnormal_batch_count", 0))
                      for e in series]
    # 分项目均值序列（仅当日涉及的项目）
    item_names = sorted({r.item for r in collected.quality})
    for item in item_names:
        for e in series:
            mean = e.get("item_mean", {}).get(item)
            if mean is None:
                continue
            m.item_trend.append(ItemTrendPoint(date=e["report_date"], item=item,
                                               mean_value=mean,
                                               fail_count=e.get("item_fail", {}).get(item, 0),
                                               unit=_item_unit(collected, item)))
    m.trend_summary = _trend_summary(m.trend_points)

    return m


def _item_means(collected: CollectedData) -> dict:
    sums: dict[str, list] = {}
    for r in collected.quality:
        s = sums.setdefault(r.item, [0.0, 0])
        s[0] += r.value
        s[1] += 1
    return {item: round(s[0] / s[1], 4) for item, s in sums.items() if s[1]}


def _item_unit(collected: CollectedData, item: str) -> str:
    for r in collected.quality:
        if r.item == item:
            return r.unit
    return ""


def _trend_summary(points: list[TrendPoint]) -> dict:
    """合格率趋势摘要：斜率 + 环比 + 文字结论。"""
    if len(points) < 2:
        return {"direction": "数据不足", "slope": 0.0, "wow_delta": 0.0,
                "comment": "历史数据不足 2 天，暂无法判定趋势，请连续运行数日后查看"}
    rates = [p.pass_rate for p in points]
    n = len(rates)
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(rates) / n
    denom = sum((x - x_mean) ** 2 for x in xs)
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, rates)) / denom if denom else 0.0
    wow = rates[-1] - rates[-2]
    if slope > 0.005:
        direction = "持续改善"
    elif slope < -0.005:
        direction = "持续下滑"
    elif wow < -0.01:
        direction = "环比转弱"
    elif wow > 0.01:
        direction = "环比走强"
    else:
        direction = "基本平稳"
    # wow 是「两个比例相减」＝百分点差，不是百分比。旧文案 f"{wow:+.2%}" 把
    # 0.2321 印成「+23.21%」（实为 23.21 个百分点，真实环比 +37.1%），
    # 还把「总体持续下滑」挂在「涨了」后面自相矛盾（夜测 2026-11-11 r03 一般②）。
    prev = rates[-2]
    if prev:
        change = f"较昨日 {wow / prev:+.1%}（{wow * 100:+.2f} 个百分点）"
    else:
        change = f"较昨日 {wow * 100:+.2f} 个百分点（昨日为 0，百分比无从算起）"
    return {"direction": direction, "slope": round(slope, 4), "wow_delta": round(wow, 4),
            "comment": f"近 {n} 日合格率斜率 {slope:+.4f}/天（{direction}）；{change}"}


# ------------------------------------------------------------------ 预警评估

def metric_map(m: DailyMetrics) -> dict:
    """把指标展平为 {键: 数值}，供预警规则引用。"""
    flat = {
        "overall_pass_rate": round(m.pass_rate, 4),
        "batch_pass_rate": round(m.batch_pass_rate, 4),
        "total_tests": m.total_tests,
        "fail_tests": m.fail_tests,
        "abnormal_batch_count": len(m.abnormal_batches),
        "critical_batch_count": sum(1 for b in m.abnormal_batches if b.level == "critical"),
        "max_item_fail_count": max((it.fail for it in m.items), default=0),
        "near_limit_count": m.near_limit_count,
        "warehouse_violation_count": len(m.warehouse_violations),
        "batch_count": m.batch_count,
    }
    # 无生产数据时不做产量达成率判定（避免无数据触达预警）
    if m.planned_qty > 0:
        flat["production_achievement"] = round(m.achievement, 4)
    for p in m.products:
        flat[f"product_pass_rate::{p.product}"] = round(p.pass_rate, 4)
    return flat


def evaluate_alerts(m: DailyMetrics, rules: list) -> list[Alert]:
    """按配置规则逐条评估，返回命中的预警列表（按级别排序）。"""
    flat = metric_map(m)
    alerts: list[Alert] = []
    for rule in rules:
        metric = rule.get("metric", "")
        if metric not in flat:
            continue
        op = rule.get("op", ">=")
        threshold = rule.get("value", 0)
        actual = flat[metric]
        if op not in _OPS or not _OPS[op](actual, threshold):
            continue
        refs = _rule_refs(m, rule["id"])
        detail = rule.get("detail", "").format(value=actual) if "{value" in rule.get("detail", "") \
            else rule.get("detail", "")
        alerts.append(Alert(rule_id=rule["id"], level=rule.get("level", "info"),
                            title=rule.get("title", rule["id"]), detail=detail, refs=refs))
    order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: order.get(a.level, 3))
    return alerts


def _rule_refs(m: DailyMetrics, rule_id: str) -> list:
    """预警关联的原始记录/批次引用（可追溯）。"""
    if rule_id in ("batch_fail",):
        return [b.batch_id for b in m.abnormal_batches if b.level == "critical"]
    if rule_id == "item_fail_spike":
        worst = max(m.items, key=lambda i: i.fail, default=None)
        return worst.fail_batches if worst else []
    if rule_id == "warehouse_violation":
        return [v["record_id"] for v in m.warehouse_violations]
    if rule_id == "near_limit":
        return []
    return []
