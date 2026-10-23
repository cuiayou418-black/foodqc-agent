# -*- coding: utf-8 -*-
"""玩具版切块：先不管国标，用 8 行假数据把「累积 - 清空」这个套路练熟。

    LINES = ["A1", "内容1","噪声-页码3", "内容2", "A2", "内容3", "噪声-页眉GB1350","内容4", "A3", "内容5"]

    目标：切成 3 块
        第1块: ["A1", "内容1", "内容2"]
        第2块: ["A2", "内容3", "内容4"]
        第3块: ["A3", "内容5"]

    规则：以 "A" 开头的行 = 新块的开头；其余行 = 属于上一块。

跑法：
    cd ~/foodqc-agent
    . .venv/Scripts/activate
    python gb-bot/toy_split.py
"""

LINES = [
    
    "A1", "内容1", "噪声-页码3", "内容2",
    "A2", "内容3", "噪声-页眉GB1350", "内容4",
    "A3", "内容5",
]


# ────────────────────────────────────────────────────────────
# 核心就这一段：一个「缓冲区」buf，攒着当前这块的内容。
# 遇到新块开头 → 先把 buf 存进 chunks，再清空 buf，然后从新行重新攒。
# 循环结束后 buf 里还剩最后一块 —— 这是最常忘的一步。
# ────────────────────────────────────────────────────────────
def make_chunk(buf):
    """把攒好一串行，打包成一块"""
    return {
        "clause": buf[0],          # ← 这条条款号是 buf 里的第几个？
        "text":   " ".join(buf),   # ← 提示： " ".join(buf)
        "standard_no": "GB 1350-2025",
    }
chunks = []
buf = []

for line in LINES:
    if line.startswith("噪声"):    # 是噪声 → 跳过
            continue                   # ← 结束本轮，直接看下一行
    if line.startswith("A"):
        if buf:                 # 上一块攒好了，存起来
            chunks.append(make_chunk(buf))
        buf = []                # 清空，准备装新块
    buf.append(line)            # 当前行永远属于当前块


if buf:                         # 收尾：最后一块还在 buf 里
    chunks.append(make_chunk(buf))


print(f"切出 {len(chunks)} 块")
for i, c in enumerate(chunks, 1):
    print(f"  第{i}块: {c}")


# ────────────────────────────────────────────────────────────
# 接下来你自己改三处，改完就跑一次看结果 —— 这就是通往 chunk_corpus.py 的桥
#
# 任务 1（丢弃）：把 LINES 里加两行 "噪声xxx"，然后写一条规则把它们丢掉。
#                 （对应国标里的页眉、页脚、分隔线）
#                 提示：continue
#
# 任务 2（结构化）：把每块从 list 变成 dict：
#                 {"clause": "A1", "text": "A1 内容1 内容2"}
#                 提示：clause = buf[0]；text = " ".join(buf)
#                       " ".join(列表) 会把列表里的元素用空格连成一串
#
# 任务 3（加字段）：给每块再加 "standard_no": "GB 1350-2025"
#                 提示：dict 里多写一对 key: value
#
# 三个任务都做完，你就有了 chunk_corpus.py 的骨架 80%。
# 剩下要换的只有：读真文件、正则换掉 startswith。
# ────────────────────────────────────────────────────────────
