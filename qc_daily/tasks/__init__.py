# -*- coding: utf-8 -*-
# tasks/：把三条既有执行链（A 日报 / B 问答 / C 单批次判定）暴露成可复用的业务任务。
# 输入：TaskRequest（结构化，不是自由文本）；输出：TaskResult + runs/<run_id>/ 下的一等产物。
# 只做薄封装与编排，不复制采集 / 校验 / 判定 / 导出的实现。
from __future__ import annotations

from .registry import PRIMARY_TASKS, TASKS, get_spec, list_tasks
from .request import RequestError, TaskRequest
from .runner import TaskResult, TaskRunner, get_run

__all__ = ["run_task", "list_tasks", "get_run", "get_spec", "TASKS", "PRIMARY_TASKS",
           "TaskRequest", "RequestError", "TaskResult", "TaskRunner"]


def run_task(request: TaskRequest | dict | None = None, **kwargs) -> TaskResult:
    """统一执行入口：CLI / Web / 任务理解层都走这里。"""
    if isinstance(request, TaskRequest):
        req = request
    elif isinstance(request, dict):
        req = TaskRequest(**request)
    else:
        req = TaskRequest(**kwargs)
    return TaskRunner().run(req)
