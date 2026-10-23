"""GB 1350-2025 切块：读原文 + 清洗噪声 + 按条款切块"""
from pathlib import Path

SRC = Path("gb-bot/_dump_GB1350-2025.txt")

raw = SRC.read_text(encoding="utf-8").splitlines()
print(f"原始行数：{len(raw)}")

clean = []
for ln in raw:
    s = ln.strip()

    if not s:                      continue    # ① 空行
    if s.startswith("【第"):        continue# ② 页码标记：以什么开头？
    if set(s) == {"="}:             continue# ③ 分隔线：整行只有等号
    if s == "GB 1350—2025":        continue# ④ 页眉：单独一行的那串标准号

    clean.append(s)

print(f"清洗后：{len(clean)} 行")
print("---- 前 15 行 ----")
for ln in clean[:15]:
    print(ln)
# ===== 第二段：切块 =====
import re

RE_CLAUSE  = re.compile(r"^(\d+\.\d+)(?:\u3000|$)")   # 3.1 / 6.1　扦样…
RE_CHAPTER = re.compile(r"^(\d+)\u3000")              # 1　范围

# 丢掉「1　范围」之前的封面和前言
start = 0
for i, ln in enumerate(clean):
    if ln.startswith("1\u3000"):
        start = i
        break
body = clean[start:]
print(f"\n正文：从第 {start} 行开始，共 {len(body)} 行")

# 切
chunks = []
buf = []
for ln in body:
    if RE_CLAUSE.match(ln) or RE_CHAPTER.match(ln):
        if buf:
            chunks.append(buf)
        buf = []
    buf.append(ln)
if buf:
    chunks.append(buf)

print(f"切出 {len(chunks)} 块")
for c in chunks:
    print(f"  {c[0][:30]:<32} {len(c):>3} 行")
# ===== 第三段：打包成 jsonl =====
import json

def make_chunk(buf):
    return {
        "standard_no": "GB 1350-2025",
        "clause":      buf[0].split("\u3000")[0],   # "3.1" / "1　范围" → "1"
        "text":        "\n".join(buf),
        "source_file": "gb-bot/_dump_GB1350-2025.txt",
    }

records = []
for c in chunks:
    rec = make_chunk(c)
    if rec["clause"] == "2":        # 第 2 章是引用文件清单，没有指标信息，丢掉
        continue
    records.append(rec)

OUT = Path("gb-bot/chunks.jsonl")
with OUT.open("w", encoding="utf-8") as f:
    for r in records:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

print(f"\n写出 {len(records)} 块 -> {OUT}")
