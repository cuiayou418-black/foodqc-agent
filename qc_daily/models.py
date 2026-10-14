"""品控日报智能体 · 数据模型

与 FoodQC-Agent 一脉相承的设计原则：
- 每条记录自带溯源信息（来源系统 / 记录ID / 抽取时间），报告里每个数字都能回查到原始记录；
- 判定结论由代码比大小产生（见 QCRecord.verdict 的生成逻辑），绝不依赖模型生成；
- 只读源数据：本包所有产物都是"新增文件"，不修改任何原始记录。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------- 基础工具

def iso_now() -> str:
    """本地时间 ISO 字符串（秒级）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fingerprint(obj) -> str:
    """对任意可 JSON 化对象计算 SHA-256 指纹（键排序，保证可复现）。

    用途：原始数据快照、生成的报告都带指纹，写入审计日志，事后可验证"报告未被篡改"。
    """
    canonical = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compare(value: float, limit: float, direction: str) -> bool:
    """代码硬判定：实测值 vs 限值。direction: '≤' 或 '≥'。返回是否合格。"""
    if direction == "≤":
        return value <= limit
    if direction == "≥":
        return value >= limit
    raise ValueError(f"不支持的方向: {direction!r}（仅支持 '≤' / '≥'）")


def utilization(value: float, limit: float, direction: str) -> float:
    """限值利用率：≤1 合格，>1 不合格。≥ 方向取 limit/value。"""
    if direction == "≤":
        return value / limit if limit else float("inf")
    return limit / value if value else float("inf")


# ---------------------------------------------------------------- 原始记录

@dataclass
class ProductionRecord:
    """生产系统记录（一个批次一行）。"""
    batch_id: str
    product: str
    line: str
    planned_qty: float          # 计划产量（吨）
    actual_qty: float           # 实际产量（吨）
    started_at: str
    finished_at: str
    record_id: str = ""
    source: str = ""            # 来源系统名
    extracted_at: str = ""      # 抽取时间

    def __post_init__(self):
        if not self.record_id:
            self.record_id = f"PRD-{self.batch_id}"
        if not self.extracted_at:
            self.extracted_at = iso_now()

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id, "batch_id": self.batch_id, "product": self.product,
            "line": self.line, "planned_qty": self.planned_qty, "actual_qty": self.actual_qty,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "source": self.source, "extracted_at": self.extracted_at,
        }


@dataclass
class QCRecord:
    """质检系统记录（一个批次 × 一个检测项目一行）。"""
    batch_id: str
    product: str
    item: str
    value: float
    unit: str
    direction: str              # '≤' / '≥'
    limit_value: float
    standard: str               # 依据标准号
    limit_source: str           # 限值出处（标准原文位置）
    verdict: str                # '合格' / '不合格'（由代码判定）
    tested_at: str
    inspector: str
    lab: str
    record_id: str = ""
    source: str = ""
    extracted_at: str = ""

    def __post_init__(self):
        if not self.record_id:
            self.record_id = f"QC-{self.batch_id}-{self.item}"
        if not self.extracted_at:
            self.extracted_at = iso_now()
        # 硬闸门：判定只由代码产生，忽略外部传入的 verdict
        self.verdict = "合格" if compare(self.value, self.limit_value, self.direction) else "不合格"

    @property
    def ratio(self) -> float:
        return utilization(self.value, self.limit_value, self.direction)

    @property
    def near_limit(self) -> bool:
        """接近限值（利用率 ≥ 0.8 且 < 1）。"""
        return 0.8 <= self.ratio < 1.0

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id, "batch_id": self.batch_id, "product": self.product,
            "item": self.item, "value": self.value, "unit": self.unit, "direction": self.direction,
            "limit_value": self.limit_value, "standard": self.standard, "limit_source": self.limit_source,
            "verdict": self.verdict, "tested_at": self.tested_at, "inspector": self.inspector,
            "lab": self.lab, "ratio": round(self.ratio, 4),
            "source": self.source, "extracted_at": self.extracted_at,
        }


@dataclass
class WarehouseRecord:
    """仓储系统记录（入库/出库/移库事件）。"""
    batch_id: str
    warehouse: str
    event: str                  # 入库 / 出库 / 移库
    qty: float                  # 吨
    temp: Optional[float]       # 库温 ℃
    humidity: Optional[float]   # 湿度 %
    occurred_at: str
    record_id: str = ""
    source: str = ""
    extracted_at: str = ""

    def __post_init__(self):
        if not self.record_id:
            self.record_id = f"WHS-{self.batch_id}-{self.event}"
        if not self.extracted_at:
            self.extracted_at = iso_now()

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id, "batch_id": self.batch_id, "warehouse": self.warehouse,
            "event": self.event, "qty": self.qty, "temp": self.temp, "humidity": self.humidity,
            "occurred_at": self.occurred_at,
            "source": self.source, "extracted_at": self.extracted_at,
        }


# ---------------------------------------------------------------- 采集结果与校验

@dataclass
class SourceProvenance:
    """单个来源系统的溯源信息。"""
    system: str                 # 生产 / 质检 / 仓储
    source_type: str            # simulated / api / sql
    record_count: int
    checksum: str               # 本系统原始记录的 SHA-256
    window_start: str
    window_end: str
    extracted_at: str
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "system": self.system, "source_type": self.source_type,
            "record_count": self.record_count, "checksum": self.checksum,
            "window_start": self.window_start, "window_end": self.window_end,
            "extracted_at": self.extracted_at, "note": self.note,
        }


@dataclass
class CollectedData:
    """一次采集得到的全量原始数据 + 溯源信息。"""
    report_date: str
    production: list = field(default_factory=list)
    quality: list = field(default_factory=list)
    warehouse: list = field(default_factory=list)
    provenance: list = field(default_factory=list)

    @property
    def checksum(self) -> str:
        return fingerprint({
            "production": [r.to_dict() for r in self.production],
            "quality": [r.to_dict() for r in self.quality],
            "warehouse": [r.to_dict() for r in self.warehouse],
        })

    def to_snapshot(self) -> dict:
        return {
            "report_date": self.report_date,
            "production": [r.to_dict() for r in self.production],
            "quality": [r.to_dict() for r in self.quality],
            "warehouse": [r.to_dict() for r in self.warehouse],
            "provenance": [p.to_dict() for p in self.provenance],
            "checksum": self.checksum,
        }


@dataclass
class ValidationIssue:
    """数据校验问题。error 级将阻断报告发布（宁可不发，不可错发）。"""
    level: str                  # error / warning
    code: str
    message: str
    ref: str = ""

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message, "ref": self.ref}


# ---------------------------------------------------------------- 分析结果

@dataclass
class ProductStat:
    product: str
    batches: int = 0
    qualified_batches: int = 0      # 全部项目合格的批次数
    total_tests: int = 0
    fail_tests: int = 0
    pass_rate: float = 0.0
    batch_pass_rate: float = 0.0    # 一次交验合格率
    planned_qty: float = 0.0
    actual_qty: float = 0.0
    achievement: float = 0.0        # 产量达成率

    def to_dict(self) -> dict:
        return {
            "product": self.product, "batches": self.batches, "qualified_batches": self.qualified_batches,
            "total_tests": self.total_tests, "fail_tests": self.fail_tests,
            "pass_rate": round(self.pass_rate, 4), "batch_pass_rate": round(self.batch_pass_rate, 4),
            "planned_qty": self.planned_qty, "actual_qty": self.actual_qty,
            "achievement": round(self.achievement, 4),
        }


@dataclass
class ItemStat:
    item: str
    unit: str = ""
    total: int = 0
    fail: int = 0
    fail_batches: list = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return (self.total - self.fail) / self.total if self.total else 0.0

    def to_dict(self) -> dict:
        return {"item": self.item, "unit": self.unit, "total": self.total, "fail": self.fail,
                "pass_rate": round(self.pass_rate, 4), "fail_batches": self.fail_batches}


@dataclass
class AbnormalBatch:
    batch_id: str
    product: str
    line: str
    level: str                  # critical / warning / info
    reasons: list = field(default_factory=list)
    fail_items: list = field(default_factory=list)
    worst_ratio: float = 0.0

    def to_dict(self) -> dict:
        return {"batch_id": self.batch_id, "product": self.product, "line": self.line,
                "level": self.level, "reasons": self.reasons, "fail_items": self.fail_items,
                "worst_ratio": round(self.worst_ratio, 4)}


@dataclass
class TrendPoint:
    date: str
    pass_rate: float
    total: int
    fail: int
    abnormal_batches: int

    def to_dict(self) -> dict:
        return {"date": self.date, "pass_rate": round(self.pass_rate, 4), "total": self.total,
                "fail": self.fail, "abnormal_batches": self.abnormal_batches}


@dataclass
class ItemTrendPoint:
    date: str
    item: str
    mean_value: float
    fail_count: int
    unit: str

    def to_dict(self) -> dict:
        return {"date": self.date, "item": self.item, "mean_value": round(self.mean_value, 4),
                "fail_count": self.fail_count, "unit": self.unit}


@dataclass
class DailyMetrics:
    """日报全部指标 —— 全部由代码计算得出。"""
    report_date: str
    plant: str
    generated_at: str

    # 总览
    batch_count: int = 0
    planned_qty: float = 0.0
    actual_qty: float = 0.0
    achievement: float = 0.0
    total_tests: int = 0
    fail_tests: int = 0
    pass_rate: float = 0.0
    batch_pass_rate: float = 0.0

    # 明细
    products: list = field(default_factory=list)        # List[ProductStat]
    items: list = field(default_factory=list)           # List[ItemStat]
    defect_distribution: list = field(default_factory=list)   # [{item, fail, share}]
    abnormal_batches: list = field(default_factory=list)      # List[AbnormalBatch]
    near_limit_count: int = 0

    # 仓储
    warehouse_events: int = 0
    warehouse_violations: list = field(default_factory=list)  # [{record_id, batch_id, kind, detail}]

    # 趋势
    trend_points: list = field(default_factory=list)          # List[TrendPoint]
    item_trend: list = field(default_factory=list)            # List[ItemTrendPoint]
    trend_summary: dict = field(default_factory=dict)         # {direction, slope, wow_delta, comment}

    # 预警与校验
    alerts: list = field(default_factory=list)                # List[Alert]
    validation_issues: list = field(default_factory=list)     # List[ValidationIssue]

    def to_dict(self) -> dict:
        return {
            "report_date": self.report_date, "plant": self.plant, "generated_at": self.generated_at,
            "batch_count": self.batch_count, "planned_qty": self.planned_qty,
            "actual_qty": self.actual_qty, "achievement": round(self.achievement, 4),
            "total_tests": self.total_tests, "fail_tests": self.fail_tests,
            "pass_rate": round(self.pass_rate, 4), "batch_pass_rate": round(self.batch_pass_rate, 4),
            "products": [p.to_dict() for p in self.products],
            "items": [i.to_dict() for i in self.items],
            "defect_distribution": self.defect_distribution,
            "abnormal_batches": [b.to_dict() for b in self.abnormal_batches],
            "near_limit_count": self.near_limit_count,
            "warehouse_events": self.warehouse_events,
            "warehouse_violations": self.warehouse_violations,
            "trend_points": [t.to_dict() for t in self.trend_points],
            "item_trend": [t.to_dict() for t in self.item_trend],
            "trend_summary": self.trend_summary,
            "alerts": [a.to_dict() for a in self.alerts],
            "validation_issues": [v.to_dict() for v in self.validation_issues],
        }


@dataclass
class Alert:
    """一条预警。refs 指向触发它的原始记录/批次，保证可追溯。"""
    rule_id: str
    level: str                  # critical / warning / info
    title: str
    detail: str
    refs: list = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = iso_now()

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "level": self.level, "title": self.title,
                "detail": self.detail, "refs": self.refs, "created_at": self.created_at}


@dataclass
class RunResult:
    """一次完整运行的产物与结论。"""
    run_id: str
    status: str                 # success / partial / failed
    report_date: str
    started_at: str
    finished_at: str = ""
    metrics: Optional[DailyMetrics] = None
    outputs: dict = field(default_factory=dict)   # {html/pdf/excel: 路径}
    alerts: list = field(default_factory=list)
    deliveries: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    audit_path: str = ""

    def summary(self) -> str:
        lines = [
            f"运行编号 : {self.run_id}",
            f"报告日期 : {self.report_date}",
            f"运行状态 : {self.status}",
        ]
        if self.metrics:
            m = self.metrics
            lines.append(f"总体合格率: {m.pass_rate:.2%}（{m.total_tests - m.fail_tests}/{m.total_tests} 项次）")
            lines.append(f"异常批次  : {len(m.abnormal_batches)} 个")
            lines.append(f"预警      : {len(self.alerts)} 条")
        for fmt, path in self.outputs.items():
            lines.append(f"导出[{fmt}] : {path}")
        if self.errors:
            lines.append("错误      : " + "；".join(self.errors))
        return "\n".join(lines)
