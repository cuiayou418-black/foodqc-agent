import os, json, glob, re

CORPUS_DIR = "corpus"

def load_md_chunks():
    """把 corpus 里所有 .md 按空行切成知识片"""
    chunks = []
    for path in glob.glob(os.path.join(CORPUS_DIR, "*.md")):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if len(para) >= 8:
                chunks.append({"source": os.path.basename(path), "text": para})
    return chunks

def load_json_limits():
    """读 limits.json，把每个标准的子主题拆成独立小块（避免元数据淹没关键值）"""
    path = os.path.join(CORPUS_DIR, "limits.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    def flatten(obj, prefix, pieces):
        """递归展开嵌套字典，最终产出 '路径: 值' 形式的碎片"""
        if isinstance(obj, dict):
            for k, v in obj.items():
                flatten(v, f"{prefix} → {k}" if prefix else k, pieces)
        elif isinstance(obj, list):
            for item in obj:
                pieces.append(f"{prefix}: {item}")
        else:
            pieces.append(f"{prefix}: {obj}")

    out = []
    for key, val in data.items():
        if key.startswith("_"):
            continue
        if not isinstance(val, dict):
            continue
        # ① 标准头单独成片
        head = f'{val.get("标准号","")} {val.get("标准名称","")}（{val.get("实施日期","")}实施）'
        out.append({"source": f"limits.json → {key}", "text": head})
        # ② 每个子主题单独成片（这是关键）
        for k2, v2 in val.items():
            if isinstance(v2, dict):
                pieces = []
                flatten(v2, k2, pieces)
                out.append({"source": f"limits.json → {key} → {k2}",
                            "text": f"{key} {k2} " + " ".join(pieces)})
    return out


CORPUS = load_md_chunks() + load_json_limits()

def tokenize(query):
    """英文数字按整段切；中文没有空格，按 2 字滑窗切（2-gram）"""
    tokens = []
    for seg_en in re.findall(r"[a-zA-Z0-9\.]+", query):
        tokens.append(seg_en.lower())
    for seg in re.findall(r"[\u4e00-\u9fa5]+", query):
        for i in range(len(seg) - 1):        # 滑窗：每次取相邻两个字
            tokens.append(seg[i:i + 2])
    return tokens



def lookup_from_corpus(query, top_k=3):
    """从语料库召回最相关的若干段，必须带出处。"""
    kws = tokenize(query)
    if not kws:
        return "查询为空或无法提取关键词"
    scored = []
    for c in CORPUS:
        score = sum(c["text"].count(k) for k in kws)
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        return json.dumps({"结论": "语料库中未找到相关内容",
                           "建议": "请人工核对标准原文，不得推算"}, ensure_ascii=False)
    results = [{"出处": c["source"], "得分": s, "内容": c["text"][:800]}
               for s, c in scored[:top_k]]
    return json.dumps({"查询": query, "命中片段数": len(scored), "结果": results},
                      ensure_ascii=False, indent=2)

if __name__ == "__main__":
    print(lookup_from_corpus("一级浸出大豆油的酸价限值是多少"))