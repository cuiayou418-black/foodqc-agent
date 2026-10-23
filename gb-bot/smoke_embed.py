# -*- coding: utf-8 -*-
"""embedding 冒烟测试：确认模型能用、语义检索真的比字面匹配强。

跑法（在仓库根目录）：
    .venv/Scripts/python.exe gb-bot/smoke_embed.py

依赖的 3 个用户环境变量（已在 2026-10-22 设好，新开终端才生效）：
    HF_HOME                = D:\hf-cache            ← 模型缓存放 D 盘，不占 C
    HF_ENDPOINT            = https://hf-mirror.com  ← 国内镜像，直连 huggingface 会超时
    HF_HUB_DISABLE_SYMLINKS = 1                     ← Windows 建不了符号链接，必须强制复制
                                                       （不设这个，缓存里全是 0 字节的空文件）

注意：如果模型还没下载过，先跑一次
    .venv/Scripts/python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-small-zh-v1.5')"
"""
from sentence_transformers import SentenceTransformer

MODEL = "BAAI/bge-small-zh-v1.5"

# 三条「文档」：模拟切块后的块内容
DOCS = [
    "稻谷水分含量应不超过13.5%",
    "出糙率一等不低于79.0%",
    "杂质含量不超过1.0%",
]

# 两条「问题」：模拟用户提问
QUERIES = [
    "水分上限是多少",      # 字面只有"水分"重合，看语义能不能抓准
    "一等要求多少",        # 字面完全没有重合词，纯语义
]


def main() -> None:
    model = SentenceTransformer(MODEL)
    print(f"模型: {MODEL}")
    print(f"向量维度: {model.get_embedding_dimension()}\n")

    doc_vecs = model.encode(DOCS)

    for q in QUERIES:
        qv = model.encode([q])
        sims = (qv @ doc_vecs.T)[0]
        ranked = sorted(zip(DOCS, sims), key=lambda x: -x[1])

        print(f"问：{q}")
        for i, (d, s) in enumerate(ranked, 1):
            mark = "  ← 命中" if i == 1 else ""
            print(f"   {i}. {s:.4f}  {d}{mark}")
        print()


if __name__ == "__main__":
    main()
