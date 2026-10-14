"""品控日报智能体 · 审计与追溯

三道追溯机制，保证"可追溯"落到磁盘：
1. 原始数据快照（raw/YYYY-MM-DD/）：每次采集的原始记录全量落盘，只写一次，永不修改；
2. 审计日志（audit/audit.jsonl）：JSONL 只追加，记录每次运行的全要素
   （时间窗、记录数、各来源指纹、报告指纹、导出文件、预警、投递、状态）；
3. 报告指纹：HTML 报告与快照的 SHA-256 写入审计日志，事后可验证报告未被篡改。
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

from .models import CollectedData, fingerprint, iso_now


class AuditLogger:
    """只追加 JSONL 审计日志。"""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def log(self, entry: dict) -> str:
        entry = dict(entry)
        entry.setdefault("run_id", uuid.uuid4().hex[:12])
        entry.setdefault("logged_at", iso_now())
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry["run_id"]

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

    def last_run_dates(self) -> set:
        """已成功生成日报的日期集合（供调度器判断是否需要补跑）。"""
        return {e.get("report_date") for e in self.read_all()
                if e.get("status") in ("success", "partial") and e.get("report_date")}


def new_run_id() -> str:
    """运行编号：日期 + 当日序号 + 随机后缀，保证唯一且可读。"""
    now = datetime.now()
    return f"R{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"


def save_raw_snapshot(base_dir: str, data: CollectedData) -> dict:
    """保存原始采集快照（只写一次）。

    目录结构：{base_dir}/raw/{report_date}/
        ├── manifest.json      # 快照清单：各系统记录数 + 指纹 + 采集时间
        ├── production.json    # 生产记录全量
        ├── quality.json       # 质检记录全量
        └── warehouse.json     # 仓储记录全量
    """
    day_dir = os.path.join(base_dir, "raw", data.report_date)
    os.makedirs(day_dir, exist_ok=True)

    files = {
        "production.json": [r.to_dict() for r in data.production],
        "quality.json": [r.to_dict() for r in data.quality],
        "warehouse.json": [r.to_dict() for r in data.warehouse],
    }
    for name, rows in files.items():
        path = os.path.join(day_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2, default=str)

    manifest = {
        "report_date": data.report_date,
        "saved_at": iso_now(),
        "provenance": [p.to_dict() for p in data.provenance],
        "checksum": data.checksum,
        "record_counts": {"production": len(data.production),
                          "quality": len(data.quality),
                          "warehouse": len(data.warehouse)},
    }
    with open(os.path.join(day_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return {"snapshot_dir": day_dir, "checksum": data.checksum,
            "files": sorted(files.keys()) + ["manifest.json"]}


def verify_report(path: str) -> tuple[bool, str]:
    """校验 HTML 报告是否被篡改。

    机制：文件内嵌 <meta name='report-fingerprint'> 存储占位版（__FP__）的 SHA-256。
    复算方式：把文件中的真实指纹替换回 __FP__ 再哈希，与内嵌值比对。
    返回 (是否通过, 期望指纹/失败原因)。
    """
    import re

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    m = re.search(r"<meta name='report-fingerprint' content='([0-9a-f]{64})'>", content)
    if not m:
        return False, "未找到内嵌报告指纹"
    expected = m.group(1)
    recomputed = fingerprint(content.replace(expected, "__FP__"))
    return recomputed == expected, expected
