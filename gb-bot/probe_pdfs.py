# probe_pdfs.py —— 侦察 18 份国标 PDF 的"可提取性"
# 目的：摸清哪些 PDF 能直接抽出文字、哪些疑似乱码、各多少页
# 用法：在 foodqc-agent 目录下 → 激活 venv → pip install pymupdf → python gb-bot/probe_pdfs.py

import fitz          # PyMuPDF：操作 PDF 的库（先 pip install pymupdf）
import os
import json
import re

# raw 字符串（r 开头）：路径里的反斜杠不当转义符处理，Windows 路径必须这样写
PDF_DIR = r"D:\桌面\植物油国标"

results = []   # 每一份 PDF 的结果都塞进这个列表，最后整体写成 json

# 第一步：把文件夹里所有 .pdf 文件名列出来
pdf_files = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")]
print(f"共找到 {len(pdf_files)} 个 PDF")

# 第二步：挨个打开、抽文字、统计
for fname in pdf_files:
    path = os.path.join(PDF_DIR, fname)   # 文件夹路径 + 文件名 = 完整路径
    doc = fitz.open(path)                 # 打开这份 PDF
    total_text = ""
    for page in doc:                      # doc 是一叠"页"，for 循环一页页翻
        total_text += page.get_text()     # 把每页能选中的文字拼起来
    # doc.close()  # 可选：用完关掉，规范写法

    page_count = len(doc)
    char_count = len(total_text)          # 抽出来的总字符数

    # TODO②：判断是否"疑似乱码"（这是真正考你的地方，自己写条件）
    # 思路提示：
    #   1) 用 re.findall(r"[\u4e00-\u9fff]", total_text) 把文字里所有"中文字"挑出来数个数
    #   2) 如果"页数 > 5 但中文字占比 < 10%"，就标记为乱码
    #   3) 想想：占比怎么算？中文字数 / 总字符数
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", total_text))
    # 防除零：某 PDF 一个字都抽不出时 char_count=0，直接除会崩。0 字必属乱码，所以给 ratio 兜底 0
    ratio = chinese_chars / char_count if char_count > 0 else 0
    is_garbled = page_count > 5 and ratio < 0.1          # TODO②：判断条件（页数多 且 中文字占比低 → 疑似乱码）

    # 第三步：打印一行结果，肉眼检查
    print(f"{fname} | 页数:{page_count} | 字符:{char_count} | 乱码:{is_garbled}")

    # 第四步：把这一份的结果存进列表
    results.append({
        "file": fname,
        "pages": page_count,
        "chars": char_count,
        "garbled": is_garbled,
    })

# 第五步：全部跑完，写进 json 文件（下一步程序要读它）
with open("gb-bot/probe_result.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("已保存 gb-bot/probe_result.json")
