import os, json
from openai import OpenAI
from judge_tool import judge
from dotenv import load_dotenv
load_dotenv()
client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")

SYSTEM = ("你是一名米粮质检助手。需要计算时必须调用工具，严禁自行心算或估算数值。"
          "若工具返回错误，请根据错误提示中列出的'可用工具'修正后重试；"
          "仅在重试后仍无法取得结果时，才回答'无法给出结论，请人工复核'。"
          "任何情况下都不得用自行推算的数值代替工具结果。")

def calc(expression):
    try:
        return str(eval(expression))
    except Exception as e:
        return f"错误:{e}"

# ① 所有工具集中登记在这一个字典里（这就是"工具箱"）
TOOLS_IMPL = {"calc": calc, "judge": judge}

tools = [{
    "type": "function",
    "function": {
        "name": "calc",
        "description":"计算数学表达式,如'0.15/0.25*100'",
        "parameters": {"type": "object",
            "properties": {"expression": {"type": "string"}}, "required": ["expression"]}
    },
},
 {
     "type": "function",
     "function": {
         "name": "judge",
         "description": "判定实测值是否达标,凡涉及合格、达标或超标的判断必须调用本工具，严禁自行比较大小",
         "parameters": {
             "type": "object",
             "properties": {
                 "value":      {"type": "number", "description": "实测值"},
                 "limit_expr": {"type": "string", "description": "限值原文，如 ≥77.0 或 ≤1.0"}
             },
             "required": ["value", "limit_expr"]
         }
     },
 }]



messages = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": "稻谷出糙率实测 76.5%，二等品要求 ≥77.0%，这批合格吗？"},
]

MAX_STEPS = 5                   # ② 步数上限, 最多转 5 圈，防死循环烧钱
failed = 0                # 跨轮累计
MAX_RETRY = 2        # 允许模型自己补救的上限

for step in range(MAX_STEPS):
    resp = client.chat.completions.create(model="deepseek-chat", messages=messages, tools=tools)
    msg = resp.choices[0].message
    if msg.tool_calls:
        messages.append(msg)
        for tc in msg.tool_calls:
            name = tc.function.name
            # ③ 工具名校验：模型可能调一个不存在的工具
            if name not in TOOLS_IMPL:
                result = f"错误, 未知工具 '{name}', 可用工具: {list(TOOLS_IMPL)}"
                failed += 1
            else:
                try:                                # ④ 异常兜底，工具执行出错也别崩
                    args = json.loads(tc.function.arguments)
                    result = TOOLS_IMPL[name](**args)
                except Exception as e:
                    result = f"工具执行失败: {e}。请换个参数重试。"
                    failed += 1
            print(f"[第{step+1}步] 调 {name}({tc.function.arguments}) = {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content":str(result)})

            # ★ 硬闸门：连续失败超限，代码直接短路，不给模型说话的机会
        if failed >= MAX_RETRY:
            print("最终答案: 【系统拦截】工具连续失败，无法给出结论，请人工复核。")
            break
        continue
    print("最终答案:", msg.content)
    break
else:
    print("⚠️ 已打最大步数仍未结束，强制停止（防死循环）")