# FoodQC-Agent · W1 雏形

> 食用油中控质检辅助智能体（毕业设计雏形）· 第一周学习产出

## 这是什么
基于「大语言模型 + 工具调用」的食用油质检辅助 Agent 最小可行原型。
核心原则：**模型不算数，代码算数**——标准限值存在代码里的标准表，模型只负责"抽取 / 调用 / 组织语言"，权威数字永远来自代码。

## W1 进度（Day 1–6 已完成）
| Day | 内容 | 交付 |
|---|---|---|
| 1 | 环境搭建（Python + Git + VS Code + venv） | 可运行环境 |
| 2 | 第一次调通 LLM 对话 | `hello_llm.py`（暴露模型幻觉） |
| 3 | 结构化 JSON 抽取 | `struct_out.py` |
| 4 | Tool Calling：模型说要查、代码去查 | `tool_calling.py`（查表得 0.50，对） |
| 5 | 手写 Agent 闭环 | `mini_agent.py`（查完回传、模型开口） |
| 6 | smolagents 框架版 | `day6_agent.py`（框架托管循环） |

## 目录
- `*.py` —— 各 Day 的代码
- `docs/` —— 四张实测证据截图 + 「架构演进对照」图
  - `01_幻觉实证_酸价限值.png`（Day2 模型答错 0.20）
  - `02_工具调用_酸价限值正确.png`（Day4 查表得 0.50）
  - `03_Agent闭环_最终答案.png`（Day5 闭环）
  - `04_smolagents框架_最终答案.png`（Day6 框架版）
- `notes/` —— 每日学习笔记（模板见 `notes/模板.md`）

## 运行方式
```bash
cd foodqc-agent
. .venv/Scripts/activate
pip install -r requirements.txt
# 在 .env 中填入 DEEPSEEK_API_KEY=sk-xxx
python day6_agent.py
```

## 安全说明
`.env`（含 API Key）、`.venv/`、`__pycache__/` 已在 `.gitignore` 中，**不会进入版本库**。
克隆本仓库后需自行配置 `.env`。
