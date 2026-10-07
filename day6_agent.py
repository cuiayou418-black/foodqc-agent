from smolagents import CodeAgent, OpenAIServerModel, tool
import os, json
from dotenv import load_dotenv
load_dotenv()

@tool
def lookup_limit(oil: str, grade: str, indicator: str) -> str:
    """查询食用油某项指标的标准限值 (国标或企业内控) 。

    Args:
        oil: 油种, 如 大豆油
        grade: 等级, 如 一级
        indicator: 指标, 如 酸价
    """

    STANDARD_TABLE = {
        ("大豆油", "一级", "酸价"): {"limit": 0.50, "std": "GB/T 1535-2017"},
        ("大豆油", "一级", "过氧化值"): {"limit": 5.0, "std": "GB/T 1535-2017"},
        ("大豆油", "一级", "水分及挥发物"): {"limit": 0.10, "std": "GB/T 1535-2017"},
    }

    row = STANDARD_TABLE.get((oil, grade, indicator))
    if row:
        return json.dumps({"限值": row["limit"], "标准号": row["std"]},ensure_ascii=False)
    return json.dumps({"error": "标准表中未收录"}, ensure_ascii=False)

model = OpenAIServerModel(
    model_id="deepseek-chat",
    api_base="https://api.deepseek.com",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
)
agent = CodeAgent(tools=[lookup_limit], model=model)
print(agent.run("一级大豆油酸价国标限值是多少?依据哪个标准?"))