import os, json
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()
client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")

resp = client.chat.completions.create(
    model="deepseek-chat",
    response_format={"type": "json_object"},
    messages=[
        {"role": "system", "content": "你是质检数据抽取器。只输出 JSON，不要任何解释。字段：指标(string), 数值(number), 单位(string), 限值(number)"},
        # 关键：限值是我们从标准表查好后"喂"进去的，不是让模型自己回忆 —— 呼应 Day2 的幻觉教训
        {"role": "user", "content": "一级大豆油中控检验：酸价 0.62 mgKOH/g。适用限值（由标准表提供）：≤0.50 mgKOH/g"},
    ],
)
data = json.loads(resp.choices[0].message.content)
data["是否合格"] = data["数值"] <= data["限值"]
print(data)
print("数值类型:", type(data["数值"]), "| 是否合格类型:", type(data["是否合格"]))
