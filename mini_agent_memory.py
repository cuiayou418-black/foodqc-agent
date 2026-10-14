import os, json, re
from openai import OpenAI
from dotenv import load_dotenv
from lookup_from_corpus import lookup_from_corpus   # ← 复用 Day11 的成果

load_dotenv()
client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")

SYSTEM = """你是一名食用油质检助手，可调用工具。

【回答限值类、检测方法类问题前，必须先调用 lookup_from_corpus】
查证国标原文，并在最终答案中写明出处（标准号 + 表号/章节）。
禁止凭记忆回答任何数值。

【若工具返回"未找到相关内容"】
必须回答"未能查到依据，请人工核对标准原文"，不得推算。

【需要计算时必须调用 calc】
严禁自行心算或估算数值。

【收敛要求】
当你已查到限值并完成比对后，必须立即给出最终答案，
不得为了"再确认一下"而重复调用工具。"""

def calc(expression):
    try:
        return str(eval(expression))
    except Exception as e:
        return f"错误:{e}"
    
def verify_numbers(answer, retrieved_texts, question=""):
    """只校验'实质性数值'，并剔除标准号（标识符不算数值）"""
    cleaned = re.sub(r"(GB|GB/T)\s*\d+(\.\d+)?(-?\d{4})?", "", answer)   # ← 新增：剔除标准号
    nums = set(re.findall(r"\d+\.\d+|\d{2,}", cleaned))                  # 改用 cleaned
    q_nums = set(re.findall(r"\d+\.?\d*", question))
    nums = nums - q_nums
    corpus_text = " ".join(retrieved_texts)
    suspicious = [n for n in nums if n not in corpus_text]
    return suspicious


TOOLS_IMPL = {"calc": calc, "lookup_from_corpus": lookup_from_corpus}

tools = [
    {
        "type": "function",
        "function": {
            "name": "calc",
            "description": "计算数学表达式，如 '0.62/0.50*100'",
            "parameters": {"type": "object",
                "properties": {"expression": {"type": "string"}}, "required": ["expression"]}
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_from_corpus",
            "description": "在国标语料库中检索限值或测定方法的原文。"
                           "【当用户问某指标的限值、限量、单位、检验方法，或要判断合格与否时，"
                           "必须先调用本工具查证，不得凭记忆回答】",
            "parameters": {"type": "object",
                "properties": {"query": {"type": "string",
                    "description": "检索关键词，如 '一级浸出大豆油 酸价 限值'"}},
                "required": ["query"]}
        },
    },
]

messages = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": "水泥 C30 抗压强度标准是多少"
                                "若实测 0.62 mg/g,是否合格？给出出处。"},
]

MAX_STEPS = 8
failed = 0
MAX_RETRY = 2

collected_texts = []      # 所有工具的返回，都算"可引用来源"

for step in range(MAX_STEPS):
    is_last = (step == MAX_STEPS - 1)
    kwargs = {} if is_last else {"tools": tools}
    resp = client.chat.completions.create(model="deepseek-chat", messages=messages, **kwargs)
    msg = resp.choices[0].message

    if msg.tool_calls:
        messages.append(msg)
        for tc in msg.tool_calls:
            name = tc.function.name
            if name not in TOOLS_IMPL:
                result = f"错误, 未知工具 '{name}', 可用工具: {list(TOOLS_IMPL)}"
                failed += 1
            else:
                try:
                    args = json.loads(tc.function.arguments)
                    result = TOOLS_IMPL[name](**args)
                except Exception as e:
                    result = f"工具执行失败: {e}。请换个参数重试。"
                    failed += 1
            collected_texts.append(str(result))          # ← 所有工具返回都收集
            print(f"[第{step+1}步] 调 {name}({tc.function.arguments})")
            print(f"         返回: {str(result)[:200]}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})

        if failed >= MAX_RETRY:
            print("最终答案: 【系统拦截】工具连续失败，无法给出结论，请人工复核。")
            break
        continue

    # ↓↓↓ 4 空格缩进 = 走出 if msg.tool_calls 了 ↓↓↓
    suspicious = verify_numbers(msg.content, collected_texts, question=messages[1]["content"])
    if suspicious:
        print("【系统拦截】答案含语料中不存在的数值:", suspicious)
        print("            已阻止输出，请人工复核。")
    else:
        print("最终答案:", msg.content)
    break
else:
    print("⚠️ 已打最大步数仍未结束，强制停止")
