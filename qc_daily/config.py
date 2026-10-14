"""品控日报智能体 · 配置系统

一切业务参数（产品限值、预警规则、推送渠道、调度时间）都来自配置文件，
代码里不硬编码任何限值 —— 与 FoodQC-Agent「模型不算数，代码算数」同源：
数字由配置/代码产出，改业务不改代码。

配置文件为 JSON（qc_daily_config.json），支持环境变量覆盖敏感项（密码/Token/URL）。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "qc_daily_config.json")

#: 限值方向合法值
DIRECTIONS = ("≤", "≥")

#: 预警级别合法值
ALERT_LEVELS = ("critical", "warning", "info")


def env(name: str, default: str = "") -> str:
    """读取环境变量（用于 SMTP 密码、Webhook URL 等敏感项，不进配置文件）。"""
    return os.environ.get(name, default)


DEFAULT_CONFIG: dict = {
    "report": {
        "title": "食用油中控品控日报",
        "plant": "第一油脂工厂",
        # previous_day: 每天生成"昨天"的日报（推荐，数据已收口）；same_day: 生成当天
        "report_for": "previous_day",
        "timezone_offset": 8,
    },
    "source": {
        # 默认 sql：只有导入了数据（或接了公司数据源）才出日报；
        # 未导入数据时系统干净地提示"无数据"，绝不生成演示日报。
        # 模拟数据源仅供开发自测（python -m qc_daily selftest 内部使用）。
        "type": "sql",                  # sql / api / simulated
        "simulated": {
            "seed_salt": "foodqc",
            "batches_per_day": 8,
            "anomaly_rate": 0.12,     # 每天注入异常的概率基数（演示/测试用）
        },
        "api": {
            "base_url": "",
            "token_env": "QC_API_TOKEN",
            "endpoints": {"production": "/api/production", "quality": "/api/quality", "warehouse": "/api/warehouse"},
            "timeout": 15,
        },
        "sql": {
            "driver": "sqlite3",      # sqlite3 / mysql（mysql 需额外安装驱动）
            "dsn": "qc_source.db",
            "queries": {
                "production": "SELECT batch_id, product, line, planned_qty, actual_qty, started_at, finished_at FROM production WHERE date(started_at) = :report_date",
                "quality": "SELECT batch_id, product, item, value, unit, direction, limit_value, standard, limit_source, tested_at, inspector, lab FROM quality WHERE date(tested_at) = :report_date",
                "warehouse": "SELECT batch_id, warehouse, event, qty, temp, humidity, occurred_at FROM warehouse WHERE date(occurred_at) = :report_date",
            },
        },
    },
    # 产品与检测项目限值。direction: '≤' 越小越好 / '≥' 越大越好（如烟点）。
    # standard + limit_source 即"答案必带出处"，会写进日报与 Excel 明细。
    "products": {
        "一级浸出大豆油": {
            "items": {
                "酸价":         {"unit": "mg/g",  "direction": "≤", "limit": 0.50, "standard": "GB/T 1535—2026", "limit_source": "表4 一级（原文第5页）"},
                "过氧化值":     {"unit": "g/100g", "direction": "≤", "limit": 0.13, "standard": "GB/T 1535—2026", "limit_source": "表4 一级（原文第5页）"},
                "水分及挥发物": {"unit": "%",     "direction": "≤", "limit": 0.10, "standard": "GB/T 1535—2026", "limit_source": "表4 一级（原文第5页）"},
                "不溶性杂质":   {"unit": "%",     "direction": "≤", "limit": 0.05, "standard": "GB/T 1535—2026", "limit_source": "表4 一级（原文第5页）"},
                "溶剂残留量":   {"unit": "mg/kg", "direction": "≤", "limit": 20,   "standard": "GB 2716—2018",  "limit_source": "表2 食用植物油（原文第3页）；GB/T 1535 表4 一级要求'不应检出'"},
                "烟点":         {"unit": "℃",    "direction": "≥", "limit": 190,  "standard": "GB/T 1535—2026", "limit_source": "表4 一级（原文第5页）"},
            }
        },
        "浸出大豆原油": {
            "items": {
                "酸价":         {"unit": "mg/g",  "direction": "≤", "limit": 4.0,  "standard": "GB/T 1535—2026", "limit_source": "表2 大豆原油（原文第4页）"},
                "过氧化值":     {"unit": "g/100g", "direction": "≤", "limit": 0.25, "standard": "GB/T 1535—2026", "limit_source": "表2 大豆原油（原文第4页）"},
                "水分及挥发物": {"unit": "%",     "direction": "≤", "limit": 0.20, "standard": "GB/T 1535—2026", "limit_source": "表2 大豆原油（原文第4页）"},
                "不溶性杂质":   {"unit": "%",     "direction": "≤", "limit": 0.20, "standard": "GB/T 1535—2026", "limit_source": "表2 大豆原油（原文第4页）"},
                "溶剂残留量":   {"unit": "mg/kg", "direction": "≤", "limit": 100,  "standard": "GB/T 1535—2026", "limit_source": "表2 浸出法（原文第4页）"},
            }
        },
        "食用植物调和油": {
            "items": {
                "酸价":       {"unit": "mg/g",  "direction": "≤", "limit": 3.0,  "standard": "GB 2716—2018", "limit_source": "表2 食用植物油（含调和油）（原文第3页）"},
                "过氧化值":   {"unit": "g/100g", "direction": "≤", "limit": 0.25, "standard": "GB 2716—2018", "limit_source": "表2 食用植物油（含调和油）（原文第3页）"},
                "溶剂残留量": {"unit": "mg/kg", "direction": "≤", "limit": 20,   "standard": "GB 2716—2018", "limit_source": "表2 食用植物油（原文第3页）"},
            }
        },
    },
    "warehouse_limits": {
        "temp_min": 5, "temp_max": 30,          # 库温 ℃
        "humidity_min": 40, "humidity_max": 70,  # 湿度 %
    },
    "analysis": {
        "near_limit_ratio": 0.8,   # 限值利用率 ≥ 0.8 记为"接近限值"
        "trend_days": 7,           # 趋势分析回溯天数
    },
    # 预警规则：metric 为指标键（见 analysis.metric_map），op 支持 < <= > >= ==
    "alert_rules": [
        {"id": "pass_rate_low",       "level": "critical", "metric": "overall_pass_rate",        "op": "<",  "value": 0.95, "title": "总体合格率跌破 95%",        "detail": "当日总体合格率 {value:.2%}，低于红线 95%，请立即组织复盘"},
        {"id": "batch_fail",           "level": "critical", "metric": "abnormal_batch_count",     "op": ">=", "value": 1,    "title": "出现不合格/异常批次",       "detail": "当日检出 {value} 个异常批次，详见日报异常批次明细"},
        {"id": "item_fail_spike",      "level": "warning",  "metric": "max_item_fail_count",      "op": ">=", "value": 3,    "title": "单项缺陷集中出现",           "detail": "有检测项目当日失败 {value} 项次，疑似系统性偏差"},
        {"id": "warehouse_violation",  "level": "warning",  "metric": "warehouse_violation_count", "op": ">=", "value": 1,   "title": "仓储环境超限",               "detail": "当日库温/湿度超限 {value} 次，存在品质劣变风险"},
        {"id": "near_limit",           "level": "info",     "metric": "near_limit_count",         "op": ">=", "value": 3,    "title": "多项指标接近限值",           "detail": "当日 {value} 个项次限值利用率 ≥ 80%，建议关注工艺波动"},
        {"id": "production_shortfall", "level": "warning",  "metric": "production_achievement",   "op": "<",  "value": 0.90,  "title": "产量达成率不足 90%",        "detail": "当日产量达成率 {value:.2%}"},
    ],
    "push": {
        "console": {"enabled": True},
        "email": {
            "enabled": False, "dry_run": True,
            "smtp_host": "", "smtp_port": 465, "use_ssl": True,
            "username": "", "password_env": "QC_SMTP_PASSWORD",
            "sender": "", "receivers": [],
            "subject_prefix": "【品控预警】",
        },
        "webhook": {
            "enabled": False, "dry_run": True,
            "url": "", "url_env": "QC_WEBHOOK_URL",
            "platform": "wecom",     # wecom / dingtalk / slack / generic
            "timeout": 10,
        },
    },
    "export": {
        "formats": ["html", "pdf", "excel"],
        "output_dir": "qc_daily_data",   # 相对项目根目录
    },
    "web": {
        "host": "0.0.0.0",               # 0.0.0.0 = 局域网可访问；127.0.0.1 = 仅本机
        "port": 8000,
        # 登录保护：设置 token（或环境变量 QC_WEB_TOKEN）后，访问网站需先登录。
        # 局域网演示可不设；公网部署必须设置。
        "token": "",
        "token_env": "QC_WEB_TOKEN",
        "start_scheduler": False,       # True = Web 进程内同时跑每日定时调度
    },
    "schedule": {
        "time": "07:30",             # 每天生成时间（HH:MM，本地时区）
        "catch_up": False,           # True 时启动后自动补齐遗漏日期
        "timezone_offset": 8,
    },
}


@dataclass
class QCDailyConfig:
    """配置对象：包装原始 dict + 类型化访问 + 环境变量覆盖。"""
    raw: dict = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_CONFIG)))
    config_path: str = ""

    # ---------- 加载 ----------
    @classmethod
    def load(cls, path: Optional[str] = None) -> "QCDailyConfig":
        path = path or DEFAULT_CONFIG_PATH
        merged = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝默认值
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            merged = _deep_merge(merged, user_cfg)
            return cls(raw=merged, config_path=path)
        return cls(raw=merged, config_path="")

    # ---------- 分区访问 ----------
    @property
    def report(self) -> dict:
        return self.raw["report"]

    @property
    def source(self) -> dict:
        return self.raw["source"]

    @property
    def products(self) -> dict:
        return self.raw["products"]

    @property
    def warehouse_limits(self) -> dict:
        return self.raw["warehouse_limits"]

    @property
    def analysis(self) -> dict:
        return self.raw["analysis"]

    @property
    def alert_rules(self) -> list:
        return self.raw["alert_rules"]

    @property
    def push(self) -> dict:
        return self.raw["push"]

    @property
    def export(self) -> dict:
        return self.raw["export"]

    @property
    def web(self) -> dict:
        return self.raw["web"]

    @property
    def schedule(self) -> dict:
        return self.raw["schedule"]

    # ---------- 派生值 ----------
    @property
    def output_root(self) -> str:
        """输出根目录（绝对路径，相对项目根目录解析）。"""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = self.export.get("output_dir", "qc_daily_data")
        return out if os.path.isabs(out) else os.path.join(base, out)

    def check(self) -> list:
        """配置自检：返回问题列表（空列表 = 通过）。"""
        issues = []
        for product, spec in self.products.items():
            items = spec.get("items", {})
            if not items:
                issues.append(f"产品 [{product}] 未配置任何检测项目")
            for item, spec_i in items.items():
                if spec_i.get("direction") not in DIRECTIONS:
                    issues.append(f"产品 [{product}] 项目 [{item}] 方向非法: {spec_i.get('direction')!r}")
                if not spec_i.get("standard"):
                    issues.append(f"产品 [{product}] 项目 [{item}] 缺少依据标准（standard）")
                if not spec_i.get("limit_source"):
                    issues.append(f"产品 [{product}] 项目 [{item}] 缺少限值出处（limit_source）")
                if float(spec_i.get("limit", 0)) <= 0:
                    issues.append(f"产品 [{product}] 项目 [{item}] 限值必须为正数")
        for rule in self.alert_rules:
            if rule.get("level") not in ALERT_LEVELS:
                issues.append(f"预警规则 [{rule.get('id')}] 级别非法: {rule.get('level')!r}")
            if rule.get("op") not in ("<", "<=", ">", ">=", "=="):
                issues.append(f"预警规则 [{rule.get('id')}] 运算符非法: {rule.get('op')!r}")
        if self.report.get("report_for") not in ("previous_day", "same_day"):
            issues.append(f"report.report_for 非法: {self.report.get('report_for')!r}")
        return issues


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并：override 覆盖 base，未提供的键保留默认值。"""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
