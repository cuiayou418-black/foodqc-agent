"""Windows GBK 控制台兼容：安全输出。

项目在 Windows 默认控制台（GBK 代码页）下直接 print emoji 会抛
UnicodeEncodeError。say() 在打印失败时自动把 emoji 降级为 ASCII 标记，
保证任何终端都不会因输出而崩溃。
"""
from __future__ import annotations

import sys

_EMOJI_FALLBACK = {
    "⛔": "[X]", "⚠️": "[!]", "⚠": "[!]", "✅": "[OK]", "❌": "[X]",
    "📌": "[*]", "🔶": "[~]", "⭐": "[*]", "🚀": "[>]", "📊": "[#]",
}


def say(msg: str = "") -> None:
    """安全打印：GBK 控制台自动降级 emoji。"""
    try:
        print(msg)
        return
    except UnicodeEncodeError:
        pass
    for emoji, ascii_mark in _EMOJI_FALLBACK.items():
        msg = msg.replace(emoji, ascii_mark)
    enc = sys.stdout.encoding or "ascii"
    try:
        print(msg.encode(enc, "replace").decode(enc))
    except Exception:
        print(msg.encode("ascii", "replace").decode("ascii"))
