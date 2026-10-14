"""品控日报智能体 · 数据采集层

从生产、质检、仓储三类系统采集原始数据。三种数据源实现：

- SimulatedDataSource  模拟数据源：确定性随机（同一天生成的数据永远一样），
                       用于开发调试与端到端自测，无需真实系统；
- ApiDataSource        HTTP API 数据源：按配置的端点拉取 JSON；
- SqlDataSource        SQL 数据源：sqlite3 开箱即用，MySQL 等可同构扩展。

所有数据源都返回带溯源信息的 CollectedData（见 models.py），
并且只读源系统 —— 本包绝不修改任何原始记录。
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import urllib.request
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta

from .config import QCDailyConfig, env
from .models import (CollectedData, ProductionRecord, QCRecord, SourceProvenance,
                     ValidationIssue, WarehouseRecord, fingerprint, iso_now)

INSPECTORS = ["王建国", "李秀兰", "张伟", "陈思远", "刘敏"]
LABS = ["中控实验室", "中心化验室"]


class BaseDataSource(ABC):
    """数据源基类。子类实现 _fetch，基类负责包装溯源信息。"""

    source_type = "base"

    def __init__(self, config: QCDailyConfig):
        self.config = config

    @abstractmethod
    def _fetch(self, report_date: date) -> tuple[list, list, list]:
        """返回 (production, quality, warehouse) 原始记录列表。"""

    def collect(self, report_date: date) -> CollectedData:
        production, quality, warehouse = self._fetch(report_date)
        day_start = f"{report_date.isoformat()} 00:00:00"
        day_end = f"{report_date.isoformat()} 23:59:59"

        def window(records, ts_field):
            ts = [getattr(r, ts_field) for r in records]
            return (min(ts) if ts else day_start, max(ts) if ts else day_end)

        p_win = window(production, "finished_at")
        q_win = window(quality, "tested_at")
        w_win = window(warehouse, "occurred_at")

        provenance = [
            SourceProvenance("生产", self.source_type, len(production),
                             fingerprint([r.to_dict() for r in production]),
                             *p_win, iso_now()),
            SourceProvenance("质检", self.source_type, len(quality),
                             fingerprint([r.to_dict() for r in quality]),
                             *q_win, iso_now()),
            SourceProvenance("仓储", self.source_type, len(warehouse),
                             fingerprint([r.to_dict() for r in warehouse]),
                             *w_win, iso_now()),
        ]
        return CollectedData(report_date=report_date.isoformat(),
                             production=production, quality=quality, warehouse=warehouse,
                             provenance=provenance)


# ------------------------------------------------------------------ 模拟数据源

class SimulatedDataSource(BaseDataSource):
    """确定性模拟数据源。

    - 同一天的数据永远相同（种子 = 盐值 + 日期），保证日报可复现、可回溯；
    - 按 anomaly_rate 概率注入异常（超标 / 接近限值 / 仓储超限），供预警链路演示；
    - 限值全部来自配置文件，与 FoodQC-Agent 语料库（corpus/limits.json）同源。
    """

    source_type = "simulated"

    def __init__(self, config: QCDailyConfig):
        super().__init__(config)
        self.salt = config.source["simulated"].get("seed_salt", "foodqc")
        self.batches_per_day = int(config.source["simulated"].get("batches_per_day", 8))
        self.anomaly_rate = float(config.source["simulated"].get("anomaly_rate", 0.12))
        self.product_names = list(config.products.keys())
        self.lines = ["精炼一车间 L1", "精炼二车间 L2", "压榨车间 L3", "灌装车间 L4"]

    def _rng(self, report_date: date) -> random.Random:
        return random.Random(f"{self.salt}:{report_date.isoformat()}")

    def _fetch(self, report_date: date) -> tuple[list, list, list]:
        rng = self._rng(report_date)
        production: list[ProductionRecord] = []
        quality: list[QCRecord] = []
        warehouse: list[WarehouseRecord] = []
        # 模拟数据的"抽取时间"固定为当日数据收口时刻，保证同一天多次运行指纹一致
        # （真实数据源则记录真实抽取时间，见 BaseDataSource.collect）
        cutoff = f"{report_date.isoformat()} 23:59:59"

        for i in range(self.batches_per_day):
            batch_id = f"{report_date.strftime('%Y%m%d')}-{i + 1:02d}"
            product = self.product_names[i % len(self.product_names)]
            line = self.lines[rng.randrange(len(self.lines))]
            planned = round(rng.uniform(40, 90), 1)
            actual = round(planned * rng.uniform(0.88, 1.06), 1)
            start_h = rng.randrange(6, 20)
            start = datetime.combine(report_date, datetime.min.time()).replace(hour=start_h)
            finish = start + timedelta(hours=rng.randrange(3, 8))
            production.append(ProductionRecord(
                batch_id=batch_id, product=product, line=line,
                planned_qty=planned, actual_qty=actual,
                started_at=start.strftime("%Y-%m-%d %H:%M:%S"),
                finished_at=finish.strftime("%Y-%m-%d %H:%M:%S"),
                source="生产执行系统(MES·模拟)"))

            # ---- 质检记录：每个批次覆盖该产品全部检测项目
            for item, spec in self.config.products[product]["items"].items():
                limit = float(spec["limit"])
                direction = spec["direction"]
                if direction == "≤":
                    base = limit * rng.uniform(0.35, 0.70)
                else:
                    base = limit * rng.uniform(1.03, 1.15)
                roll = rng.random()
                if roll < self.anomaly_rate * 0.45:          # 超标
                    if direction == "≤":
                        value = limit * rng.uniform(1.02, 1.30)
                    else:
                        value = limit * rng.uniform(0.88, 0.96)
                elif roll < self.anomaly_rate:                # 接近限值
                    if direction == "≤":
                        value = limit * rng.uniform(0.82, 0.97)
                    else:
                        value = limit * rng.uniform(1.01, 1.10)
                else:                                          # 正常
                    value = base
                value = round(value, 3)
                tested = start + timedelta(hours=rng.randrange(1, 10))
                quality.append(QCRecord(
                    batch_id=batch_id, product=product, item=item, value=value,
                    unit=spec["unit"], direction=direction, limit_value=limit,
                    standard=spec["standard"], limit_source=spec["limit_source"],
                    verdict="",  # 由 __post_init__ 代码硬判定
                    tested_at=tested.strftime("%Y-%m-%d %H:%M:%S"),
                    inspector=rng.choice(INSPECTORS), lab=rng.choice(LABS),
                    source="实验室信息管理系统(LIMS·模拟)"))

            # ---- 仓储记录：入库为主，少量出库；小概率库温/湿度超限
            wh = "一号常温库" if i % 2 == 0 else "二号恒温库"
            temp = round(rng.uniform(12, 26), 1)
            humidity = round(rng.uniform(45, 62), 1)
            if rng.random() < self.anomaly_rate * 0.5:
                temp = round(rng.uniform(31, 36), 1)   # 库温超限
            warehouse.append(WarehouseRecord(
                batch_id=batch_id, warehouse=wh, event="入库", qty=actual,
                temp=temp, humidity=humidity,
                occurred_at=(finish + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
                source="仓储管理系统(WMS·模拟)"))
            if rng.random() < 0.35:
                out_qty = round(actual * rng.uniform(0.3, 0.8), 1)
                warehouse.append(WarehouseRecord(
                    batch_id=batch_id, warehouse=wh, event="出库", qty=out_qty,
                    temp=round(rng.uniform(12, 26), 1), humidity=round(rng.uniform(45, 62), 1),
                    occurred_at=(finish + timedelta(hours=rng.randrange(2, 10))).strftime("%Y-%m-%d %H:%M:%S"),
                    source="仓储管理系统(WMS·模拟)"))

        # 统一回填确定的抽取时间，保证"同一天 → 同一份数据 → 同一个指纹"
        for r in production + quality + warehouse:
            r.extracted_at = cutoff

        return production, quality, warehouse


# ------------------------------------------------------------------ HTTP API 数据源

class ApiDataSource(BaseDataSource):
    """HTTP API 数据源。

    约定：GET {base_url}{endpoint}?from=YYYY-MM-DD&to=YYYY-MM-DD
          返回 JSON 数组（或 {"data": [...]} 包装），元素字段与 models 中各 to_dict 字段一致。
    认证：Header Authorization: Bearer <token>，token 从环境变量读取。
    """

    source_type = "api"

    def _get(self, path: str, report_date: date, token: str, timeout: int) -> list:
        url = f"{self.config.source['api']['base_url'].rstrip('/')}{path}"
        url += f"?from={report_date.isoformat()}&to={report_date.isoformat()}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                                   "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if isinstance(payload, dict):        # 兼容 {"data": [...]} 包装
            for key in ("data", "items", "records", "list"):
                if key in payload and isinstance(payload[key], list):
                    return payload[key]
            raise ValueError(f"API 返回结构无法识别: {url}")
        return payload

    def _fetch(self, report_date: date) -> tuple[list, list, list]:
        cfg = self.config.source["api"]
        if not cfg.get("base_url"):
            raise RuntimeError("api 数据源未配置 base_url（qc_daily_config.json → source.api.base_url）")
        token = env(cfg.get("token_env", "QC_API_TOKEN"))
        timeout = int(cfg.get("timeout", 15))
        endpoints = cfg["endpoints"]

        def norm(rows: list) -> list:
            return [{str(k).lower(): v for k, v in row.items()} for row in rows]

        prod_rows = norm(self._get(endpoints["production"], report_date, token, timeout))
        qual_rows = norm(self._get(endpoints["quality"], report_date, token, timeout))
        wh_rows = norm(self._get(endpoints["warehouse"], report_date, token, timeout))

        production = [ProductionRecord(
            batch_id=r["batch_id"], product=r["product"], line=r.get("line", ""),
            planned_qty=float(r["planned_qty"]), actual_qty=float(r["actual_qty"]),
            started_at=r["started_at"], finished_at=r["finished_at"],
            source=r.get("source", "生产系统(API)")) for r in prod_rows]

        quality = [QCRecord(
            batch_id=r["batch_id"], product=r["product"], item=r["item"], value=float(r["value"]),
            unit=r["unit"], direction=r["direction"], limit_value=float(r["limit_value"]),
            standard=r.get("standard", ""), limit_source=r.get("limit_source", ""),
            verdict="", tested_at=r["tested_at"],
            inspector=r.get("inspector", ""), lab=r.get("lab", ""),
            source=r.get("source", "质检系统(API)")) for r in qual_rows]

        warehouse = [WarehouseRecord(
            batch_id=r["batch_id"], warehouse=r["warehouse"], event=r["event"], qty=float(r["qty"]),
            temp=r.get("temp"), humidity=r.get("humidity"), occurred_at=r["occurred_at"],
            source=r.get("source", "仓储系统(API)")) for r in wh_rows]

        return production, quality, warehouse


# ------------------------------------------------------------------ SQL 数据源

class SqlDataSource(BaseDataSource):
    """SQL 数据源。sqlite3 开箱即用；MySQL/PostgreSQL 可换驱动后同构使用。

    查询语句写在配置里（支持 :report_date 命名参数），字段名与 models 一致。
    """

    source_type = "sql"

    def _connect(self):
        cfg = self.config.source["sql"]
        driver = cfg.get("driver", "sqlite3")
        if driver == "sqlite3":
            dsn = cfg["dsn"]
            if not os.path.isabs(dsn):  # 相对路径按项目根目录解析，避免受启动目录影响
                root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                dsn = os.path.join(root, dsn)
            conn = sqlite3.connect(dsn)
            return conn
        raise RuntimeError(f"暂不支持的 SQL 驱动: {driver}（可安装 pymysql/psycopg2 后扩展）")

    def _fetch(self, report_date: date) -> tuple[list, list, list]:
        cfg = self.config.source["sql"]
        dsn = cfg["dsn"]
        if not os.path.isabs(dsn):
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            dsn = os.path.join(root, dsn)
        # 未导入数据时不生成日报：给出人话错误，而不是 "no such table"
        if not os.path.exists(dsn):
            raise RuntimeError(
                "尚未导入任何数据：请先到网站「数据导入」页上传 Excel/CSV/JSON，"
                "或在 qc_daily_config.json 中连接公司数据源（api/sql）")
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [t for t in ("production", "quality", "warehouse") if t not in existing]
        if missing:
            conn.close()
            raise RuntimeError(
                f"数据库中还没有数据表（{', '.join(missing)}）：请先导入数据，"
                f"或检查 source.sql.dsn 是否指向正确的数据库")

        def num(v):
            """SQLite TEXT 列安全转 float（NULL/空串 → None）。"""
            if v is None or v == "":
                return None
            return float(v)

        try:
            def query(name: str) -> list:
                sql = cfg["queries"][name]
                return [dict(row) for row in conn.execute(sql, {"report_date": report_date.isoformat()})]

            prod_rows, qual_rows, wh_rows = (query("production"), query("quality"), query("warehouse"))
        finally:
            conn.close()

        production = [ProductionRecord(
            batch_id=r["batch_id"], product=r["product"], line=r.get("line", ""),
            planned_qty=num(r["planned_qty"]) or 0.0, actual_qty=num(r["actual_qty"]) or 0.0,
            started_at=r["started_at"], finished_at=r["finished_at"],
            source=r.get("source", "生产系统(SQL)")) for r in prod_rows]

        quality = [QCRecord(
            batch_id=r["batch_id"], product=r["product"], item=r["item"], value=num(r["value"]),
            unit=r["unit"], direction=r["direction"], limit_value=num(r["limit_value"]),
            standard=r.get("standard", ""), limit_source=r.get("limit_source", ""),
            verdict="", tested_at=r["tested_at"],
            inspector=r.get("inspector", ""), lab=r.get("lab", ""),
            source=r.get("source", "质检系统(SQL)")) for r in qual_rows]

        warehouse = [WarehouseRecord(
            batch_id=r["batch_id"], warehouse=r["warehouse"], event=r["event"], qty=num(r["qty"]) or 0.0,
            temp=num(r.get("temp")), humidity=num(r.get("humidity")), occurred_at=r["occurred_at"],
            source=r.get("source", "仓储系统(SQL)")) for r in wh_rows]

        return production, quality, warehouse


# ------------------------------------------------------------------ 数据校验

def validate_collected(data: CollectedData, config: QCDailyConfig) -> list[ValidationIssue]:
    """采集后数据校验（保证"数据准确"的第一道闸门）。

    error 级问题将阻断报告发布；warning 级在报告中显著提示。
    """
    issues: list[ValidationIssue] = []

    # 1. 非空
    if not data.production:
        # 无生产数据不阻断报告（很多企业只先接质检），降级为警告，产量类指标置空
        issues.append(ValidationIssue("warning", "EMPTY_PRODUCTION",
                                      "未采集到生产记录，产量/达成率指标本次为空", ""))
    if not data.quality:
        issues.append(ValidationIssue("error", "EMPTY_QUALITY", "未采集到任何质检记录", ""))
    if not data.warehouse:
        issues.append(ValidationIssue("warning", "EMPTY_WAREHOUSE", "未采集到仓储记录", ""))

    # 2. 生产记录合法性 + 批次唯一性
    seen_batches = set()
    for r in data.production:
        if r.batch_id in seen_batches:
            issues.append(ValidationIssue("error", "DUP_BATCH", f"批次号重复: {r.batch_id}", r.record_id))
        seen_batches.add(r.batch_id)
        if r.planned_qty <= 0 or r.actual_qty <= 0:
            issues.append(ValidationIssue("warning", "BAD_QTY", f"产量非正数: {r.batch_id}", r.record_id))
        if r.finished_at < r.started_at:
            issues.append(ValidationIssue("warning", "BAD_TS", f"完工早于开工: {r.batch_id}", r.record_id))
        if r.product not in config.products:
            issues.append(ValidationIssue("warning", "UNKNOWN_PRODUCT",
                                          f"产品 [{r.product}] 未配置限值，跳过其质检判定", r.record_id))

    # 3. 质检记录：引用完整性 + 唯一性 + 数值合法性
    seen_qc = set()
    for r in data.quality:
        key = (r.batch_id, r.item)
        if key in seen_qc:
            issues.append(ValidationIssue("error", "DUP_QC", f"质检记录重复: {r.batch_id} × {r.item}", r.record_id))
        seen_qc.add(key)
        if r.batch_id not in seen_batches:
            # 完全没有生产记录时，前述 EMPTY_PRODUCTION 警告已覆盖，不重复记错误
            if seen_batches:
                issues.append(ValidationIssue("error", "ORPHAN_QC",
                                              f"质检记录引用了不存在的批次: {r.batch_id}", r.record_id))
        if r.value != r.value or r.value in (float("inf"), float("-inf")):
            issues.append(ValidationIssue("error", "BAD_VALUE", f"检测值非法: {r.record_id}", r.record_id))
        if r.limit_value <= 0:
            issues.append(ValidationIssue("error", "BAD_LIMIT", f"限值非正数: {r.record_id}", r.record_id))

    # 4. 仓储记录
    for r in data.warehouse:
        if r.event not in ("入库", "出库", "移库"):
            issues.append(ValidationIssue("warning", "BAD_EVENT", f"未知仓储事件: {r.event}", r.record_id))
        if r.qty <= 0:
            issues.append(ValidationIssue("error", "BAD_WHS_QTY", f"仓储数量非正数: {r.record_id}", r.record_id))

    return issues


def build_data_source(config: QCDailyConfig) -> BaseDataSource:
    """按配置构建数据源。"""
    stype = config.source.get("type", "simulated")
    mapping = {"simulated": SimulatedDataSource, "api": ApiDataSource, "sql": SqlDataSource}
    if stype not in mapping:
        raise ValueError(f"未知数据源类型: {stype!r}（可选: {list(mapping)}）")
    return mapping[stype](config)
