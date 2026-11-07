# 夜间自动巡检（只测不改）

2026-11-08 00:00–05:00，每小时一轮，共 6 轮 / 53 个场景。

## 一句话

脚本只负责**跑**和**记事实**（HTTP 码、耗时、返回了什么）；
"答得好不好、是不是 bug、该怎么改"由每轮来看日志的 agent 判断。
两者分开，是为了不让"跑通了"被当成"做对了"。

## 目录

```
nightly/
  run_round.py        单轮执行器（读 state.json 取下一轮）
  state.json          进度（{"next_round": 1}）
  FINDINGS.md         累计台账（每轮只在末尾追加）
  rounds/
    round-01.md       人读的日志
    round-01.json     机读的原始结果
```

## 手动跑

```bash
cd /c/Users/1/foodqc-agent

# 看六轮分别测什么
.venv/Scripts/python.exe nightly/run_round.py --list

# 跑下一轮（自动确保服务在跑）
.venv/Scripts/python.exe nightly/run_round.py

# 指定轮次 / 重置进度
.venv/Scripts/python.exe nightly/run_round.py --round 3
.venv/Scripts/python.exe nightly/run_round.py --reset
```

## 覆盖范围（53 个场景）

| 组 | 数量 | 举例 |
|---|---|---|
| 问答·应能答 | 9 | 稻谷七项限值、Cd、铅（都要答出数字 + 出处） |
| 问答·应拒答 | 6 | 大米水分、玉米容重、砷、铬（库里没有，必须拒答） |
| 边界 | 8 | 开放式问法、无批次问放行、不存在的批次、空问题、超长、纯符号、闲聊、越界写代码 |
| 表格·正常 | 6 | 长表日报、录入自查、单批、宽表、18 批 126 项压力、xlsx 多 sheet |
| 表格·畸形 | 10 | 表头上方标题行、`13.2%`、未检出/—/ND、纯公式列、日期不符、重复批次、缺限值列、空表、全角`＜`、数值列混文字 |
| 拒绝 | 6 | 真实批次号、`.txt`、坏 xlsx、0 字节、附表却要逐项判定、越界路径 |
| 页面 | 8 | 五个页面 200、未知页面 404、不存在的 run_id、路径穿越 |

## 安全边界

- **不改源码**：脚本只写 `nightly/` 和 `qc_daily_data/task_uploads/`
- **不删文件**、**不动 .env**、**不开大模型**（`QC_USE_LLM` 保持关闭）
- 测试表全部是 DEMO- 假批次，真实批次号只在"应被拒绝"的场景里出现，且是编的
