"""运行期开关：把「服务启动时的环境变量」升级为「可被单次请求覆盖」。

背景：LLM 开关原先只读 QC_USE_LLM 环境变量，进程一起来就定死，
前端那个 "DeepSeek · 关闭" 标签点了也不会变（因为它本来就没有后端支持）。
这里用 contextvars 存一个请求级覆盖值，优先级高于环境变量，
请求结束用 reset 清理，不会串到下一个请求。

红线不变：这里只控制「要不要调模型」，不控制「放不放行」。
任何模型输出照旧进不了判定结论，判定链的比大小、定等、出处全部由代码算。
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Any

# None = 跟随环境变量；True/False = 本次请求强制开/关
_LLM_OVERRIDE: ContextVar[bool | None] = ContextVar("qc_llm_override", default=None)

_TRUTHY = ("", "0", "false", "off", "no")


def env_llm() -> bool:
    """只看环境变量的默认值（QC_USE_LLM）。"""
    return os.getenv("QC_USE_LLM", "").strip().lower() not in _TRUTHY


def llm_enabled() -> bool:
    """当前请求实际是否允许调模型。请求级覆盖优先，其次环境变量。"""
    override = _LLM_OVERRIDE.get()
    if override is not None:
        return bool(override)
    return env_llm()


def set_llm(on: bool | None) -> Any:
    """设置请求级覆盖，返回 token 供 reset 使用。on=None 表示交还环境变量。"""
    return _LLM_OVERRIDE.set(None if on is None else bool(on))


def reset_llm(token: Any) -> None:
    """恢复上一层设置。异常吞掉，避免清理动作本身影响响应。"""
    if token is None:
        return
    try:
        _LLM_OVERRIDE.reset(token)
    except (ValueError, RuntimeError):
        pass


def llm_state() -> dict:
    """给前端回显用：真实生效状态，不是贴纸。"""
    return {
        "enabled": llm_enabled(),
        "source": "request" if _LLM_OVERRIDE.get() is not None else "env",
        "env": env_llm(),
        "has_key": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
    }
