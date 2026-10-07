import os, json
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()
client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")

# ① 告诉模型"我有哪些工具可用”（只描述，不实现）
tools = [{
    "type":"function",
    "function": {
        "name":"lookup_limit",
        "description":"查询食用油某项指标的标准限值（国标或企业内控)",
        "parameters": {
            "type":"object",
            "properties": {
                "oil":       {"type": "string", "description": "油种, 如 大豆油"},
                "grade":     {"type": "string", "description": "等级，如 一级"},
                "indicator": {"type": "string", "description": "指标，如 酸价"},
            },
            "required": ["oil", "grade", "indicator"],
        },
    },
}]

# ② 工具的真正实现——这里是一张"标准表" (真实项目来自知识图谱/标准库)
STANDARD_TABLE = {
    ("大豆油", "一级", "酸价"):        {"limit": 0.50, "std": "GB/T 1535-2017"},
    ("大豆油", "一级", "过氧化值"):     {"limit": 5.0,  "std": "GB/T 1535-2017"},
    ("大豆油", "一级", "水分及挥发物"): {"limit": 0.10, "std": "GB/T 1535-2017"},
}

def lookup_limit(oil, grade, indicator):
    row = STANDARD_TABLE.get((oil, grade, indicator))
    if row:
        return json.dumps({"限值": row["limit"], "标准号": row["std"]}, ensure_ascii=False)
    return json.dumps({"error":"标准表中未收录"}, ensure_ascii=False)

# ③ 问一个 Day2 里模型答错的问题
messages = [{"role": "user", "content": "一级大豆油的酸价限值是多少?"}]
resp = client.chat.completions.create(model="deepseek-chat", messages=messages, tools=tools)
msg = resp.choices[0].message

# ④ 模型说"我要调哪个函数", 我们负责执行
if msg.tool_calls:
    for tc in msg.tool_calls:
        args = json.loads(tc.function.arguments)
        result = lookup_limit(**args)
        print("模型要调：", tc.function.name, "| 参数:", args, "| 工具返回:", result)
else:
    print("模型没调工具，直接回答:", msg.content)