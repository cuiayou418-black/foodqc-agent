import os, json
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()
client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")

SYSTEM = "你是食用油质检助手。回答限值类问题时,先调用 lookup_limit 查标准, 再组织成带标准号的结论。"

# ① 工具说明书（给模型看，和 Day4 一样）
tools = [{
    "type": "function",
    "function": {
        "name": "lookup_limit",
        "description": "查询食用油某项指标的标准限值（国标或企业内控）",
        "parameters": {"type": "object",
            "properties": {
                "oil": {"type": "string", "description": "油种，如 大豆油"},
                "grade": {"type": "string", "description": "等级，如 一级"},
                "indicator": {"type": "string", "description": "指标，如 酸价"},
            },
            "required": ["oil", "grade", "indicator"]},
    },
}]

# ② 工具真身（和 Day4 一样）
STANDARD_TABLE = {
    ("大豆油", "一级", "酸价"): {"limit": 0.50, "std": "GB/T 1535-2017"},
    ("大豆油", "一级", "过氧化值"): {"limit": 5.0, "std": "GB/T 1535-2017"},
    ("大豆油", "一级", "水分及挥发物"): {"limit": 0.10, "std": "GB/T 1535-2017"},
}
def lookup_limit(oil, grade, indicator):
    row = STANDARD_TABLE.get((oil, grade, indicator))
    if row:
        return json.dumps({"限值": row["limit"], "标准号": row["std"]}, ensure_ascii=False)
    return json.dumps({"error": "标准表中未收录"}, ensure_ascii=False)

# ③ 问题
messages = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": "一级大豆油的酸价国标限值是多少?依据哪个标准?"},
]

# ④ Agent 闭环: 最多 5 步, 放死循环
for step in range(5):
    resp = client.chat.completions.create(model="deepseek-chat", messages=messages, tools=tools)
    msg = resp.choices[0].message
    if msg.tool_calls:
        messages.append(msg)    # 把模型"我要调工具"的意图记进历史
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            result = lookup_limit(**args)
            print(f"[第{step+1}步] 调 {tc.function.name}({args}) = {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        continue    # 拿到结果, 再问一次模型
    print("最终答案:", msg.content)
    break