# -*- coding: utf-8 -*-
"""夜间自动巡检 · 单轮执行器（只测不改）

用法：
  .venv/Scripts/python.exe nightly/run_round.py --list           # 看八轮都测什么
  .venv/Scripts/python.exe nightly/run_round.py --round 3        # 只跑第 3 轮
  .venv/Scripts/python.exe nightly/run_round.py                  # 跑下一轮（读 state.json）

设计原则：
1. **只读不改**：本脚本不会修改 qc_daily/ 下任何源码，只生成测试数据、发请求、写报告。
2. **每轮场景不同**：53 个场景切成 6 轮（00:00–05:00 每小时一轮），每轮 9 个，
   覆盖问答 / 表格 / 畸形输入 / 拒绝 / 页面。
3. **机器判定 + 人判定分开**：本脚本只记录客观事实（能不能答、有没有数字、耗时、HTTP 码），
   "答得好不好、是不是 bug、该怎么改" 由人（或 agent）看日志判断。
4. **不动用户数据**：测试表一律写进 qc_daily_data/task_uploads/，用完不删（便于复现）。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8000"
UPLOAD_DIR = REPO / "qc_daily_data" / "task_uploads"
OUT_DIR = Path(__file__).resolve().parent / "rounds"
STATE = Path(__file__).resolve().parent / "state.json"
NIGHTLY_STATE = Path(__file__).resolve().parent / "state-nightly.json"
NIGHTLY_LOCK = Path(__file__).resolve().parent / ".nightly.lock"
LOCK_STALE_MIN = 20   # 20 分钟内已有轮次启动过 → 视为重复触发，直接跳过
ROUNDS = 6          # 00:00–05:00，每小时一轮共 6 轮
PER_ROUND = 9       # 每轮 9 个场景，覆盖问答/表格/畸形/拒绝/页面
# 每晚巡检：按日期轮换轮次（6 天覆盖全部 53 个场景后循环），日志按日期归档。
# 与一次性整夜巡检（state.json / rounds/round-NN.md）互不干扰。

sys.path.insert(0, str(REPO))


# ----------------------------------------------------------------- 环境
def _port_open(host="127.0.0.1", port=8000) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def ensure_server(timeout=40) -> tuple[bool, str]:
    """端口没起就拉一个；起不来如实返回，不假装。"""
    if _port_open():
        return True, "复用已在运行的服务"
    proc = subprocess.Popen(
        [sys.executable, "-m", "qc_daily", "web", "--port", "8000", "--host", "127.0.0.1"],
        cwd=str(REPO), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _port_open():
            return True, f"脚本临时拉起服务（pid={proc.pid}）"
        time.sleep(1)
    return False, f"服务起不来（pid={proc.pid}），端口 8000 无响应"


def http(method: str, path: str, payload: dict | None = None, timeout: float = 90):
    import requests
    url = BASE + path
    t0 = time.time()
    try:
        if method == "GET":
            r = requests.get(url, timeout=timeout)
        else:
            r = requests.post(url, json=payload, timeout=timeout)
        cost = time.time() - t0
        try:
            body = r.json()
        except ValueError:
            body = {"_raw": r.text[:400]}
        return {"http": r.status_code, "seconds": round(cost, 2), "body": body}
    except Exception as e:
        return {"http": 0, "seconds": round(time.time() - t0, 2),
                "body": {}, "error": f"{type(e).__name__}: {str(e)[:160]}"}


# ----------------------------------------------------------------- 造表
def _write_csv(name: str, rows: list[list], encoding="utf-8-sig") -> str:
    p = UPLOAD_DIR / name
    with p.open("w", encoding=encoding, newline="") as f:
        csv.writer(f).writerows(rows)
    return str(p)


def _write_xlsx(name: str, sheets: dict[str, list[list]], formula_at: tuple | None = None) -> str:
    import openpyxl
    wb = openpyxl.Workbook()
    first = True
    for title, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        for r in rows:
            ws.append(r)
        first = False
    if formula_at:
        wb[list(sheets)[0]][formula_at[0]] = formula_at[1]
    p = UPLOAD_DIR / name
    wb.save(str(p))
    return str(p)


LONG_HEAD = ["批次号", "产品名称", "检测项目", "实测值", "单位", "指标方向", "限值",
             "标准号", "限值出处", "检验日期", "检验员", "班组"]
ITEMS = [("出糙率", "≥", "75.0"), ("整精米率", "≥", "44.0"), ("杂质含量", "≤", "1.0"),
         ("水分含量", "≤", "13.5"), ("黄粒米含量", "≤", "1.0"),
         ("谷外糙米含量", "≤", "4.0"), ("互混率", "≤", "5.0")]
SRC = "GB 1350—2025 表1 第3等 原文第5页"


def long_table(batches: list[dict], date="2026-11-10") -> list[list]:
    rows = [LONG_HEAD]
    for b in batches:
        for item, dirn, lim in ITEMS:
            v = b["v"].get(item)
            if v is None:
                continue
            rows.append([b["id"], b.get("prod", "中晚籼稻谷"), item, v, "%", dirn, lim,
                         "GB 1350—2025", SRC, b.get("date", date),
                         b.get("insp", "假检验员"), b.get("lab", "假班组A")])
    return rows


def mk_file(spec: dict) -> str:
    """按场景定义生成文件，返回绝对路径。"""
    kind = spec["kind"]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if kind == "long":
        return _write_csv(f"{stamp}_夜测_长表.csv", long_table(spec["batches"], spec.get("date", "2026-11-10")))
    if kind == "raw_csv":
        return _write_csv(f"{stamp}_夜测_{spec['name']}", spec["rows"], spec.get("encoding", "utf-8-sig"))
    if kind == "xlsx":
        return _write_xlsx(f"{stamp}_夜测_{spec['name']}", spec["sheets"], spec.get("formula"))
    if kind == "plain":
        p = UPLOAD_DIR / f"{stamp}_夜测_{spec['name']}"
        if spec.get("binary"):
            p.write_bytes(spec["binary"])
        else:
            p.write_text(spec.get("text", ""), encoding="utf-8")
        return str(p)
    raise ValueError(kind)


def rel(p: str) -> str:
    """传给 /api/client/task 的必须是仓库相对路径（服务端只认上传目录内）。"""
    return Path(p).resolve().relative_to(REPO).as_posix()


# ----------------------------------------------------------------- 场景
def S(sid, group, title, kind, **kw):
    d = {"id": sid, "group": group, "title": title, "kind": kind}
    d.update(kw)
    return d


def build_scenarios() -> list[dict]:
    sc: list[dict] = []

    # ---- 组1：知识问答，应给出数字 + 出处
    for q, must in [
        ("稻谷水分限值是多少", "13.5"),
        ("出糙率 3 等限值是多少，依据在哪一条", "75.0"),
        ("稻谷杂质限值多少", "1.0"),
        ("整精米率三等要求多少", "44.0"),
        ("谷外糙米限值是多少", "4.0"),
        ("互混率限值是多少", "5.0"),
        ("黄粒米限值多少", "1.0"),
        ("大米成品的Cd含量不得超过多少", "0.2"),
        ("铅在油脂里限值多少", "0.08"),
    ]:
        sc.append(S(f"QA-{len(sc)+1:02d}", "问答·应能答", q, "chat",
                    payload={"question": q, "mode": "chat"}, must_contain=[must]))

    # ---- 组2：应拒答（库里没有 / 超出范围），不能编
    for q in [
        "大米的水分限值是多少",
        "玉米的容重限值是多少",
        "大豆的粗蛋白限值是多少",
        "小麦的面筋含量限值是多少",
        "稻谷里砷的限值是多少",
        "铬在稻谷里的限值是多少",
    ]:
        sc.append(S(f"RF-{len(sc)+1:02d}", "问答·应拒答", q, "chat",
                    payload={"question": q, "mode": "chat"}, expect_refuse=True))

    # ---- 组3：边界与歧义
    sc += [
        S("ED-01", "边界", "开放式问法：这批怎么样", "chat",
          payload={"question": "帮我看一下这批粮怎么样", "mode": "chat"}),
        S("ED-02", "边界", "无批次问能不能放行", "chat",
          payload={"question": "这批能不能放行", "mode": "auto"}),
        S("ED-03", "边界", "不存在的批次号", "chat",
          payload={"question": "DEMO-999 这批能收吗", "mode": "auto"}),
        S("ED-04", "边界", "空问题（应 400）", "chat",
          payload={"question": "", "mode": "chat"}, expect_http=400),
        S("ED-05", "边界", "超长问题 320 字（应 400）", "chat",
          payload={"question": "水分" * 160, "mode": "chat"}, expect_http=400),
        S("ED-06", "边界", "纯符号", "chat",
          payload={"question": "？？？！！！……", "mode": "chat"}),
        S("ED-07", "边界", "闲聊（应说出范围）", "chat",
          payload={"question": "今天天气怎么样", "mode": "chat"}),
        S("ED-08", "边界", "让模型写代码（越界）", "chat",
          payload={"question": "帮我写一段 Python 爬虫代码", "mode": "chat"}),
    ]

    # ---- 组4：正常表格
    base = [{"id": "DEMO-N01", "v": {"出糙率": "78.2", "整精米率": "51.0", "杂质含量": "0.5",
                                     "水分含量": "13.1", "黄粒米含量": "0.2",
                                     "谷外糙米含量": "1.0", "互混率": "1.5"}},
            {"id": "DEMO-N02", "v": {"出糙率": "76.0", "整精米率": "45.5", "杂质含量": "1.3",
                                     "水分含量": "13.6", "黄粒米含量": "0.4",
                                     "谷外糙米含量": "1.8", "互混率": "2.2"}}]
    sc += [
        S("TB-01", "表格·正常", "标准长表 2 批 14 项 → 日报", "task",
          file={"kind": "long", "batches": base}, question="把这张表做成今天的日报"),
        S("TB-02", "表格·正常", "同一张表 → 录入自查", "task",
          file={"kind": "long", "batches": base}, question="帮我查查有没有敲错的数"),
        S("TB-03", "表格·正常", "单批单行", "task",
          file={"kind": "long", "batches": base[:1]}, question="做成日报"),
        S("TB-04", "表格·正常", "宽表（一行一批，一列一项）", "task",
          file={"kind": "raw_csv", "name": "宽表.csv", "rows": [
              ["批次号", "产品名称", "检验日期", "检验员", "出糙率", "整精米率", "杂质含量", "水分含量"],
              ["DEMO-W01", "中晚籼稻谷", "2026-11-10", "张伟", "78.2", "51.0", "0.5", "13.1"]]},
          question="做成日报"),
        S("TB-05", "表格·正常", "18 批 126 项（压力）", "task",
          file={"kind": "long", "batches": [
              {"id": f"DEMO-P{i:02d}", "v": {"出糙率": f"{76 + i % 3}.0", "整精米率": "46.0",
                                             "杂质含量": "0.6", "水分含量": "13.2",
                                             "黄粒米含量": "0.2", "谷外糙米含量": "1.0",
                                             "互混率": "1.8"}} for i in range(1, 19)]},
          question="做成今天的日报"),
        S("TB-06", "表格·正常", "xlsx 多 sheet", "task",
          file={"kind": "xlsx", "name": "多sheet.xlsx", "sheets": {
              "原始记录": long_table(base),
              "报告": [["说明"], ["这是汇总页，不应被当成检测记录"]]}},
          question="做成日报"),
    ]

    # ---- 组5：畸形表格（最可能挖出 bug）
    sc += [
        S("BD-01", "表格·畸形", "表头上方有标题行", "task",
          file={"kind": "raw_csv", "name": "标题行.csv", "rows": [
              ["××粮油有限公司 原粮检验原始记录表"], ["检验日期：2026-11-10"],
              LONG_HEAD, ["DEMO-T01", "中晚籼稻谷", "水分含量", "13.2", "%", "≤", "13.5",
                          "GB 1350—2025", SRC, "2026-11-10", "张三", "A"]]},
          question="做成日报"),
        S("BD-02", "表格·畸形", "数值带百分号 13.2%", "task",
          file={"kind": "raw_csv", "name": "百分号.csv", "rows": [
              LONG_HEAD, ["DEMO-V01", "中晚籼稻谷", "水分含量", "13.2%", "%", "≤", "13.5",
                          "GB 1350—2025", SRC, "2026-11-10", "张三", "A"]]},
          question="做成日报"),
        S("BD-03", "表格·畸形", "未检出 / — / / / ND 混用", "task",
          file={"kind": "raw_csv", "name": "未检出.csv", "rows": [
              LONG_HEAD,
              ["DEMO-U01", "中晚籼稻谷", "水分含量", "未检出", "%", "≤", "13.5", "GB 1350—2025", SRC, "2026-11-10", "张三", "A"],
              ["DEMO-U02", "中晚籼稻谷", "水分含量", "—", "%", "≤", "13.5", "GB 1350—2025", SRC, "2026-11-10", "张三", "A"],
              ["DEMO-U03", "中晚籼稻谷", "水分含量", "ND", "%", "≤", "13.5", "GB 1350—2025", SRC, "2026-11-10", "张三", "A"]]},
          question="做成日报"),
        S("BD-04", "表格·畸形", "xlsx 纯公式列（无缓存值）", "task",
          file={"kind": "xlsx", "name": "公式列.xlsx", "sheets": {"记录": [
              ["批次号", "产品名称", "出糙率", "水分含量", "检验日期", "检验员"],
              ["DEMO-F01", "中晚籼稻谷", 76.5, 13.2, "2026-11-10", "张三"]]},
              "formula": ("E2", "=IF(D2<=13.5,\"合格\",\"不合格\")")},
          question="做成日报"),
        S("BD-05", "表格·畸形", "日期与表内不一致", "task",
          file={"kind": "long", "batches": base, "date": "2026-10-01"}, question="做成日报"),
        S("BD-06", "表格·畸形", "重复批次号 3 次", "task",
          file={"kind": "long", "batches": [base[0], base[0], base[0]]}, question="做成日报"),
        S("BD-07", "表格·畸形", "缺限值列（只剩实测值）", "task",
          file={"kind": "raw_csv", "name": "缺限值.csv", "rows": [
              ["批次号", "产品名称", "检测项目", "实测值", "检验日期"],
              ["DEMO-X01", "中晚籼稻谷", "水分含量", "13.2", "2026-11-10"]]},
          question="做成日报"),
        S("BD-08", "表格·畸形", "空表（只有表头）", "task",
          file={"kind": "raw_csv", "name": "空表.csv", "rows": [LONG_HEAD]}, question="做成日报"),
        S("BD-09", "表格·畸形", "全角 ＜0.5", "task",
          file={"kind": "raw_csv", "name": "全角.csv", "rows": [
              LONG_HEAD, ["DEMO-Z01", "中晚籼稻谷", "杂质含量", "＜0.5", "%", "≤", "1.0",
                          "GB 1350—2025", SRC, "2026-11-10", "张三", "A"]]},
          question="做成日报"),
        S("BD-10", "表格·畸形", "数值列混入文字", "task",
          file={"kind": "raw_csv", "name": "混文字.csv", "rows": [
              LONG_HEAD, ["DEMO-M01", "中晚籼稻谷", "水分含量", "十三点二", "%", "≤", "13.5",
                          "GB 1350—2025", SRC, "2026-11-10", "张三", "A"]]},
          question="做成日报"),
    ]

    # ---- 组6：应被拒绝
    sc += [
        S("RJ-01", "拒绝", "真实批次号（非 DEMO）", "task",
          file={"kind": "raw_csv", "name": "真实批次.csv", "rows": [
              LONG_HEAD, ["2026ML1109001", "中晚籼稻谷", "水分含量", "13.2", "%", "≤", "13.5",
                          "GB 1350—2025", SRC, "2026-11-10", "张三", "A"]]},
          question="做成日报", expect_refuse=True),
        S("RJ-02", "拒绝", "不支持的扩展名 .txt", "task",
          file={"kind": "plain", "name": "记录.txt", "text": "批次号,水分\nDEMO-01,13.2"},
          question="做成日报", expect_refuse=True),
        S("RJ-03", "拒绝", "Excel 文件其实是垃圾字节", "task",
          file={"kind": "plain", "name": "坏文件.xlsx", "binary": b"not a real xlsx"},
          question="做成日报", expect_refuse=True),
        S("RJ-04", "拒绝", "空文件 0 字节", "task",
          file={"kind": "plain", "name": "空.csv", "text": ""},
          question="做成日报", expect_refuse=True),
        S("RJ-05", "拒绝", "附表却要求逐项判定", "task",
          file={"kind": "long", "batches": base}, question="判一批粮能不能收",
          expect_need="choose"),
        S("RJ-06", "拒绝", "越界路径（不在上传目录）", "task_direct",
          files=[".env"], question="做成日报", expect_refuse=True),
    ]

    # ---- 组7：页面与接口
    for path in ["/app", "/tasks", "/knowledge", "/", "/reports"]:
        sc.append(S(f"PG-{len(sc)+1:02d}", "页面", f"GET {path}", "page", path=path, expect_http=200))
    sc += [
        S("PG-90", "页面", "GET 不存在的页面（应 404）", "page", path="/nope-xxx", expect_http=404),
        S("PG-91", "页面", "不存在的 run_id（应 404）", "page",
          path="/api/client/task/T00000000-deadbeef", expect_http=404),
        S("PG-92", "页面", "路径穿越 run_id", "page",
          path="/api/tasks/..%2F..%2F.env/artifact/x", expect_http=404),
    ]

    return sc


# ----------------------------------------------------------------- 执行
def run_chat(scn) -> dict:
    r = http("POST", "/api/client/chat", scn["payload"])
    b = r.get("body") or {}
    facts = {"http": r["http"], "seconds": r["seconds"]}
    if b.get("ok"):
        if b.get("mode") == "run":
            brief = b.get("brief") or {}
            facts.update({"mode": "run", "batch": b.get("batch_id"),
                          "paras": (brief.get("paras") or [])[:4],
                          "sections_n": {k: len(v or []) for k, v in (b.get("sections") or {}).items()}})
        else:
            a = b.get("answer") or {}
            facts.update({"mode": "chat", "title": a.get("title"),
                          "lines": (a.get("lines") or [])[:4],
                          "boundary": (a.get("boundary") or [])[:2]})
    else:
        facts.update({"error": b.get("error") or r.get("error")})
    return facts


def run_task(scn) -> dict:
    p = mk_file(scn["file"])
    r = http("POST", "/api/client/task", {"question": scn["question"], "files": [rel(p)]})
    b = r.get("body") or {}
    facts = {"http": r["http"], "seconds": r["seconds"],
             "file": Path(p).name, "file_kb": round(Path(p).stat().st_size / 1024, 1)}
    if not b.get("ok"):
        facts.update({"error": b.get("error") or r.get("error")})
        return facts
    if b.get("need") == "choose":
        facts.update({"need": "choose", "reason": b.get("reason"),
                      "candidates": [c.get("name") for c in (b.get("candidates") or [])]})
        return facts
    rid = b.get("run_id")
    facts["run_id"] = rid
    t0 = time.time()
    view = {}
    while time.time() - t0 < 90:
        time.sleep(1.5)
        s = http("GET", f"/api/client/task/{rid}")
        body = s.get("body") or {}
        if body.get("ok") and body.get("terminal"):
            view = body.get("view") or {}
            facts["run_seconds"] = round(time.time() - t0, 1)
            break
    facts.update({
        "status": view.get("status"), "status_cn": view.get("status_cn"),
        "headline": view.get("headline"),
        "files": [f.get("label") for f in (view.get("files") or [])],
        "groups": [(g.get("label"), g.get("count")) for g in (view.get("groups") or [])],
        "alerts": (view.get("alerts") or [])[:3],
        "timeout": not bool(view),
    })
    return facts


def run_task_direct(scn) -> dict:
    r = http("POST", "/api/client/task", {"question": scn["question"], "files": scn["files"]})
    b = r.get("body") or {}
    return {"http": r["http"], "seconds": r["seconds"],
            "error": b.get("error") or r.get("error") or ("ok" if b.get("ok") else "")}


def run_page(scn) -> dict:
    r = http("GET", scn["path"], timeout=30)
    out = {"http": r["http"], "seconds": r["seconds"]}
    if r.get("error"):
        out["error"] = r["error"]
    body = r.get("body") or {}
    if "_raw" in body:
        out["raw_head"] = body["_raw"][:120]
    return out


# ----------------------------------------------------------------- 判定
def auto_check(scn, facts: dict) -> tuple[str, list[str]]:
    """只做客观判定，评语留给读日志的人。返回 (PASS/FAIL/WARN, 原因列表)。"""
    notes: list[str] = []
    hard_fail = False

    exp_http = scn.get("expect_http")
    if exp_http and facts.get("http") != exp_http:
        notes.append(f"HTTP 期望 {exp_http}，实际 {facts.get('http')}")
        hard_fail = True
    if facts.get("http", 0) >= 500:
        notes.append("出现 5xx")
        hard_fail = True
    if facts.get("error") and scn["kind"] in ("chat", "task", "task_direct"):
        if not (scn.get("expect_refuse") or exp_http):
            notes.append(f"异常：{facts['error'][:100]}")
            hard_fail = True

    if scn.get("expect_refuse"):
        text = json.dumps(facts, ensure_ascii=False)
        refused = any(k in text for k in ("没有", "不收录", "没做", "拒绝", "不支持", "未覆盖", "不在"))
        if not refused:
            notes.append("期望拒绝/说明查不到，但看起来给了确定结论")
            hard_fail = True
        # 按预期拒绝不加注记：那是"确认没问题"，不是警告。
        # 早先这里写了一句"已按预期拒绝"，结果六个拒答场景全被打成 WARN（23:16 修）

    for must in scn.get("must_contain") or []:
        text = json.dumps(facts, ensure_ascii=False)
        if must not in text:
            notes.append(f"答案里没有期望的数字/文字：{must}")
            hard_fail = True

    need = scn.get("expect_need")
    if need and facts.get("need") != need:
        notes.append(f"期望 need={need}，实际 {facts.get('need')}")
        hard_fail = True

    if scn["kind"] == "task" and facts.get("run_id"):
        if facts.get("timeout"):
            notes.append("90 秒内没跑完")
            hard_fail = True
        if facts.get("status") == "failed":
            notes.append(f"任务失败：{(facts.get('alerts') or [''])[0][:80]}")
        if facts.get("run_seconds", 0) > 60:
            notes.append(f"耗时偏长：{facts['run_seconds']}s")
    if facts.get("seconds", 0) > 20 and scn["kind"] in ("chat", "page"):
        notes.append(f"响应偏慢：{facts['seconds']}s")

    if hard_fail:
        return "FAIL", notes
    return ("WARN" if notes else "PASS"), notes


# ----------------------------------------------------------------- 输出
def _fmt_facts(f: dict) -> list[str]:
    lines = []
    for k, v in f.items():
        if isinstance(v, list):
            v = " / ".join(str(x) for x in v) if v else "（空）"
        lines.append(f"    {k}: {v}")
    return lines


def run_round(n: int, nightly_tag: str = "") -> int:
    all_sc = build_scenarios()
    start = (n - 1) * PER_ROUND
    batch = all_sc[start:start + PER_ROUND]
    ok, how = ensure_server()

    meta = {"round": n, "of": ROUNDS, "started_at": datetime.now().isoformat(timespec="seconds"),
            "server": how, "server_ok": ok, "scenarios": []}
    if not ok:
        meta["fatal"] = "服务起不来，本轮未执行"
    else:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        for scn in batch:
            kind = scn["kind"]
            t0 = datetime.now()
            if kind == "chat":
                facts = run_chat(scn)
            elif kind == "task":
                facts = run_task(scn)
            elif kind == "task_direct":
                facts = run_task_direct(scn)
            else:
                facts = run_page(scn)
            verdict, notes = auto_check(scn, facts)
            meta["scenarios"].append({
                "id": scn["id"], "group": scn["group"], "title": scn["title"],
                "question": scn.get("question") or (scn.get("payload") or {}).get("question", ""),
                "verdict": verdict, "notes": notes, "facts": facts,
                "at": t0.isoformat(timespec="seconds"),
            })
            print(f"  [{verdict:4s}] {scn['id']} {scn['title'][:34]}")

    meta["finished_at"] = datetime.now().isoformat(timespec="seconds")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"round-{n:02d}" if not nightly_tag else f"nightly-{nightly_tag}-r{n:02d}"
    if nightly_tag:
        # state 只存纯日期（YYYY-MM-DD），同日重试的 -b 后缀只用于日志文件名
        meta["nightly_date"] = nightly_tag.split("-b")[0] if nightly_tag.endswith("-b") else nightly_tag
    (OUT_DIR / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    head = (f"# 每晚巡检 · {nightly_tag} · 第 {n}/{ROUNDS} 轮" if nightly_tag
            else f"# 第 {n}/{ROUNDS} 轮")
    md = [head, f" · {meta['started_at']}", "",
          f"服务：{meta['server']}", ""]
    if meta.get("fatal"):
        md += [f"**{meta['fatal']}**", ""]
    for s in meta["scenarios"]:
        md.append(f"## [{s['verdict']}] {s['id']} · {s['title']}")
        if s["question"]:
            md.append(f"- 输入：`{s['question']}`")
        md += _fmt_facts(s["facts"])
        for nt in s["notes"]:
            md.append(f"    ! {nt}")
        md.append("")
    (OUT_DIR / f"{stem}.md").write_text("\n".join(md), encoding="utf-8")

    state_file = STATE if not nightly_tag else NIGHTLY_STATE
    old = {}
    if nightly_tag and state_file.exists():
        try:
            old = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            old = {}
    payload = {"next_round": n + 1, "last_round": n,
               "last_at": meta["finished_at"]}
    if nightly_tag:
        payload["last_date"] = (nightly_tag[:-2] if nightly_tag.endswith("-b")
                                else nightly_tag)
        payload["cycles_done"] = int(old.get("cycles_done", 0)) + (1 if n == ROUNDS else 0)
    state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    nxt = n + 1 if n < ROUNDS else 1      # 每晚巡检：跑满 6 轮后从 1 重新循环
    print(f"\nROUND={n} OF={ROUNDS} NEXT={nxt}")
    if nightly_tag:
        print(f"DATE={nightly_tag} MODE=NIGHTLY")
    print(f"日志：nightly/rounds/{stem}.md")
    return 0


def _nightly_locked() -> bool:
    """同一次触发被连点两次时只跑一轮。

    2026-11-08 夜：21:16:57 与 21:17:14 相隔 17 秒跑了两轮，一个晚上的
    6 轮预算瞬间少了 2 轮，汇总因此落在 03:46 而不是 05:xx。
    这里只看「上一轮什么时候启动的」，不依赖进程存活判断（Windows 上不可靠）。
    """
    if NIGHTLY_LOCK.exists():
        try:
            started = float(json.loads(NIGHTLY_LOCK.read_text(encoding="utf-8")).get("ts", 0))
        except Exception:
            started = 0.0
        gap = time.time() - started
        if 0 <= gap < LOCK_STALE_MIN * 60:
            print(f"LOCKED: 上一轮 {gap:.0f} 秒前刚开始（<{LOCK_STALE_MIN} 分钟），本次跳过")
            print("ROUND=SKIP OF=%d NEXT=NONE MODE=NIGHTLY" % ROUNDS)
            return True
    NIGHTLY_LOCK.write_text(json.dumps({"ts": time.time(), "pid": os.getpid()}),
                            encoding="utf-8")
    return False


def plan_nightly(prev: dict, today: str) -> tuple[int, str, bool]:
    """决定今晚跑第几轮：跨日归 1，同日递进。返回 (轮次, 日志标签, 是否同日)。

    2026-11-08 夜：跨日接着数（第 5、6 轮在前一晚用掉了），19:16 起只判读了 4 轮
    就收尾。每天 0 点归 1 才能保证一晚 6 轮都有人判读。
    """
    if prev.get("last_date") == today:
        n = int(prev.get("next_round", 1))
        return n, f"{today}-b", True
    return 1, today, False


def run_nightly() -> int:
    """每晚一次：按日期归 1、轮次递进，日志按天归档，同一天重跑不覆盖。

    **每天 0 点从第 1 轮开始**；6 轮跑满即停，不再自动循环。
    """
    if _nightly_locked():
        return 0
    try:
        today = date.today().isoformat()
        prev: dict = {}
        if NIGHTLY_STATE.exists():
            try:
                prev = json.loads(NIGHTLY_STATE.read_text(encoding="utf-8"))
            except Exception:
                prev = {}
        n, tag, same_day = plan_nightly(prev, today)
        if n < 1 or n > ROUNDS:
            print(f"今晚 {ROUNDS} 轮已跑完（第 {n} 轮），不再重复。")
            print(f"ROUND=DONE OF={ROUNDS} NEXT=NONE DATE={today} MODE=NIGHTLY")
            return 0
        print(f"=== 每晚巡检 {today} · 第 {n}/{ROUNDS} 轮"
              f"{'（当日第 2 次触发，日志加 -b）' if same_day else ''} ===")
        return run_round(n, nightly_tag=tag)
    finally:
        try:
            NIGHTLY_LOCK.unlink()
        except OSError:
            pass


def run_next() -> int:
    all_sc = build_scenarios()
    total_rounds = (len(all_sc) + PER_ROUND - 1) // PER_ROUND
    if STATE.exists():
        try:
            n = int(json.loads(STATE.read_text(encoding="utf-8")).get("next_round", 1))
        except Exception:
            n = 1
    else:
        n = 1
    if n > ROUNDS:
        print(f"全部 {ROUNDS} 轮已完成（场景共 {len(all_sc)} 个）。跑汇总：见 nightly/README.md")
        print(f"ROUND=DONE OF={ROUNDS} NEXT=NONE")
        return 0
    print(f"=== 开始第 {n} 轮（共 {ROUNDS} 轮，场景 {len(all_sc)} 个）===")
    return run_round(n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=0, help="指定轮次（1-8）")
    ap.add_argument("--list", action="store_true", help="列出全部轮次与场景")
    ap.add_argument("--reset", action="store_true", help="把进度重置到第 1 轮")
    ap.add_argument("--nightly", action="store_true",
                    help="每晚巡检：按日期轮换一轮、日志按天归档，6 轮后自动循环")
    a = ap.parse_args()

    if a.nightly:
        return run_nightly()
    if a.reset:
        STATE.write_text(json.dumps({"next_round": 1}), encoding="utf-8")
        print("进度已重置为第 1 轮")
        return 0
    if a.list:
        all_sc = build_scenarios()
        for i in range(ROUNDS):
            part = all_sc[i * PER_ROUND:(i + 1) * PER_ROUND]
            print(f"\n第 {i+1} 轮：")
            for s in part:
                print(f"  {s['id']:6s} [{s['group']}] {s['title']}")
        print(f"\n合计 {len(all_sc)} 个场景 / {ROUNDS} 轮")
        return 0
    if a.round:
        return run_round(a.round)
    return run_next()


if __name__ == "__main__":
    raise SystemExit(main())
