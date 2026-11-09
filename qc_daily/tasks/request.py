# -*- coding: utf-8 -*-
# tasks/request.py：一次业务任务的结构化输入，不是自由文本问答。
# 输入：CLI 参数或任务理解层识别结果；输出：可序列化、可复现的 request.json。
# 只校验输入形态（文件存在 / 扩展名 / 日期 / 格式），绝不读表内容、绝不做判定。
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Iterable

#: 只收有可核对结构化字段的表；PDF / 扫描件 / 图片 / 手写表单 / 私有二进制一律拒收。
ALLOWED_EXT = (".csv", ".xlsx", ".xlsm", ".json")
FORMATS = ("html", "pdf", "excel")


class RequestError(ValueError):
    """输入不合法：必须给出具体原因和下一步，不允许静默跳过。"""


@dataclass
class TaskRequest:
    """4.3 的任务输入。同输入必须能复现同一次执行，故全文落盘 request.json。"""

    task_id: str
    inputs: list[str] = field(default_factory=list)
    report_date: str = ""
    scope: dict = field(default_factory=lambda: {"batch": "全量", "product": "全量", "grade": "3"})
    formats: list[str] = field(default_factory=lambda: ["html", "pdf", "excel"])
    output_root: str = "runs"
    push: bool = False
    # 班长审核人姓名。空 = 未审核，经理版只会出「草稿·待审核」。
    # 审计上很重要：它记录了「谁看过这份数据并同意对外」。
    reviewed_by: str = ""

    def __post_init__(self) -> None:
        if not self.report_date:
            self.report_date = (date.today() - timedelta(days=1)).isoformat()
        if isinstance(self.inputs, str):
            self.inputs = [self.inputs]
        if isinstance(self.formats, str):
            self.formats = [x.strip() for x in re.split(r"[,\s]+", self.formats) if x.strip()]

    def validate(self, known_task_ids: Iterable[str]) -> None:
        """拒绝执行要给中文具体原因；不猜、不补、不静默降级。"""
        if self.task_id not in set(known_task_ids):
            raise RequestError(
                f"未知任务 [{self.task_id}]；可用任务：{', '.join(sorted(set(known_task_ids)))}")
        if not self.inputs:
            raise RequestError("至少给一张表（CSV / XLSX / JSON）或一个目录")
        for raw in self.inputs:
            p = str(raw)
            if not os.path.exists(p):
                raise RequestError(f"输入不存在：{p}")
            if os.path.isdir(p):
                usable = [f for f in os.listdir(p) if f.lower().endswith(ALLOWED_EXT)]
                if not usable:
                    raise RequestError(f"目录里没有可导入的 CSV/XLSX/JSON：{p}")
                continue
            ext = os.path.splitext(p)[1].lower()
            if ext not in ALLOWED_EXT:
                raise RequestError(
                    f"不支持 {ext or '(无扩展名)'}：PDF / 扫描件 / 图片 / 手写表单 / "
                    f"LIMS 私有二进制 / 加密宏文件不收（没有可核对的结构化字段）。"
                    f"请把 {os.path.basename(p)} 另存为 CSV 或 XLSX。")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.report_date):
            raise RequestError(f"report_date 必须是 YYYY-MM-DD：{self.report_date!r}")
        date.fromisoformat(self.report_date)
        bad = [f for f in self.formats if f not in FORMATS]
        if bad:
            raise RequestError(f"不支持的导出格式 {bad}；可选 {list(FORMATS)}")
        if "html" not in self.formats:
            raise RequestError("HTML 必出（4.4），formats 里必须含 html")
        if self.push:
            raise RequestError("任务层不允许真实推送：email/webhook 保持 dry_run=true，push 请留 false")

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2, sort_keys=True)
        return path

    @classmethod
    def load(cls, path: str) -> "TaskRequest":
        with open(path, "r", encoding="utf-8") as f:
            return cls(**json.load(f))
