import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)

messages = [
    {"role": "system", "content": "你是一名食用油精炼车间的质检工程师，回答简洁专业，先给结论再给依据。"},
    {"role": "user", "content": "一级大豆油的酸价国标限值是多少？超标可能有哪些原因？"},
]

resp = client.chat.completions.create(
    model="deepseek-chat",
    messages=messages,
    temperature=0.2,
)

print(resp.choices[0].message.content)
