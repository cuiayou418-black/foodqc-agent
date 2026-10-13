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
    """读 limits.json，取出标准类条目（跳过下划线开头的说明节点）"""
    path = os.path.join(CORPUS_DIR, "limits.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for key, val in data.items():
        if key.startswith("_"):          # 我自己加的说明节点，不是标准
            continue
        if isinstance(val, dict):
            head = f'{val.get("标准号","")} {val.get("标准名称","")}'
            body = json.dumps(val, ensure_ascii=False)
            out.append({"source": f"limits.json → {key}", "text": head + " " + body})
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
    results = [{"出处": c["source"], "得分": s, "内容": c["text"][:300]}
               for s, c in scored[:top_k]]
    return json.dumps({"查询": query, "命中片段数": len(scored), "结果": results},
                      ensure_ascii=False, indent=2)

if __name__ == "__main__":
    print(lookup_from_corpus("一级浸出大豆油的酸价限值是多少"))