# -*- coding: utf-8 -*-
# tasks/report.py：装配任务报告的 8 块内容（4.4），HTML 必出。
# 输入：TaskRunner 汇总的结构化 ctx；输出：runs/<run_id>/report.html + 单次内防篡改指纹。
# 复用 audit.verify_report() 的 __FP__ 占位机制，不扩展 audit.py；数字全部来自代码产物。
#
# 四层可复现性分工（第三次别再踩「拿单次溯源字段做跨运行断言」的坑）：
#   input_digest        数据层 · 跨运行可复现：input.db 三表规范化行
#   conclusion_digest   结论层 · 跨运行可复现：verdicts.json 规范化内容
#   report_fingerprint  产物层 · 仅单次：__FP__ + verify_report() 防篡改
#   data_checksum       采集层 · 仅单次：agent 既有审计字段，含 extracted_at=iso_now()
from __future__ import annotations

import os
from html import escape

from ..audit import verify_report
from ..models import fingerprint

#: 4.4 规定 8 块，缺一块算未完成；顺序固定，测试按这个列表断言。
BLOCKS = ("任务摘要", "输入清单", "校验结果", "指标汇总",
          "逐项判定与出处", "三段式结论", "人工核对指路", "留痕与复算")

_SIGN_OFF = "结论仅供复核，不得作为放行依据；最终由人签字"


def _e(v) -> str:
    return escape(str(v if v is not None else ""))


def _pct(v) -> str:
    try:
        return f"{float(v):.2%}"
    except (TypeError, ValueError):
        return "-"


def _ul(items, empty="（无）") -> str:
    rows = "".join(f"<li>{_e(x)}</li>" for x in (items or []))
    return f"<ul>{rows or f'<li>{_e(empty)}</li>'}</ul>"


def _table(headers, rows) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in row) + "</tr>"
                   for row in (rows or []))
    if not body:
        body = "<tr>" + "".join("<td>（无）</td>" for _ in headers) + "</tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_task_report(ctx: dict) -> str:
    """返回含 __FP__ 占位的 HTML；调用方负责算指纹并回填。"""
    metrics = ctx.get("metrics") or {}
    sections = ctx.get("sections") or {}
    parts: list[str] = []
    add = parts.append

    add("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>")
    add(f"<title>{_e(ctx.get('task_id',''))} · {_e(ctx.get('run_id',''))}</title>")
    add("<meta name='report-fingerprint' content='__FP__'>")
    add("""<style>
body{font-family:'Microsoft YaHei',sans-serif;max-width:1000px;margin:24px auto;padding:0 18px;
line-height:1.7;color:#1f2328}h1{color:#b81c22;font-size:21px}h2{border-left:4px solid #b81c22;
padding-left:9px;font-size:15px;margin-top:26px}table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{border:1px solid #e4e7ea;padding:5px 8px;text-align:left}th{background:#f2f3f5}
.notice{background:#fdf6ec;border:1px dashed #c2771a;padding:9px 12px;font-size:12.5px}
.error{background:#fef0ef;border:1px solid #b81c22;color:#a32d2d}
.ok{background:#e9f6f0;border:1px solid #0f6e56;color:#0f6e56}
code{background:#f6f8f7;padding:1px 4px;border-radius:3px;font-size:12px}
.tri{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.tri>div{border-radius:8px;padding:9px 11px;font-size:12.5px}
.tri .a{background:#e9f6f0;color:#0f6e56}.tri .b{background:#fff6e5;color:#8a5a06}
.tri .c{background:#f6f8f7;color:#4a5058}
.sign{margin-top:20px;border:2px solid #b81c22;background:#fef0ef;color:#a32d2d;
padding:11px 14px;font-weight:700;text-align:center}
</style></head><body>""")

    warn = ctx.get("standard_warning") or ""
    if warn:
        add(f"<div class='notice error'>{_e(warn)}</div>")
    add(f"<h1>{_e(ctx.get('title', '业务任务报告'))}</h1>")

    # ---- 1 任务摘要 ----
    add("<h2>1. 任务摘要</h2>")
    add(_table(["项", "值"], [
        ("task_id", ctx.get("task_id", "")), ("run_id", ctx.get("run_id", "")),
        ("执行起止", f"{ctx.get('started_at','')} ~ {ctx.get('finished_at','')}"),
        ("数据源类型", ctx.get("source_type", "")),
        ("数据时间范围", ctx.get("data_range", "")),
        ("状态", ctx.get("status", "")),
    ]))

    # ---- 2 输入清单 ----
    add("<h2>2. 输入清单</h2>")
    add(_table(["文件 / sheet", "类型判定", "行数", "表头映射结果", "日期"], [
        (f"{f.get('file','')}[{f.get('sheet','')}]", f.get("type", ""), f.get("rows", 0),
         "; ".join(f"{k}→{v}" for k, v in (f.get("mapped") or {}).items()),
         ", ".join(f.get("dates") or []))
        for f in (ctx.get("input_files") or [])
    ]))
    add(f"<div class='notice'>被跳过的 sheet / 未映射表头："
        f"{_e('；'.join(ctx.get('skipped') or []) or '无')}</div>")
    if ctx.get("notes"):
        add(_ul(ctx["notes"], empty="无其它提示"))

    # ---- 3 校验结果 ----
    add("<h2>3. 校验结果</h2>")
    issues = ctx.get("validation_issues") or []
    add("<h3>数据层 validate_collected</h3>")
    add(_table(["级别", "代号", "说明", "关联记录"],
               [(i.get("level", ""), i.get("code", ""), i.get("message", ""), i.get("ref", ""))
                for i in issues]))
    add("<h3>录入层 validate_records（只标待人工确认，不改数 / 不改 verdict）</h3>")
    add(_table(["批次号", "检测项目", "实测值", "疑点（大白话）", "所在行 / 字段", "建议动作"],
               [(s.get("batch_id", ""), s.get("item", ""), s.get("value", ""),
                 s.get("reason", ""), f"{s.get('batch_id','')}×{s.get('item','')} / value",
                 s.get("建议动作", "")) for s in (ctx.get("suspects") or [])]))

    # ---- 4 指标汇总 ----
    add("<h2>4. 指标汇总</h2>")
    add(_table(["指标", "值"], [
        ("总体合格率", _pct(metrics.get("pass_rate"))),
        ("一次交验合格率", _pct(metrics.get("batch_pass_rate"))),
        ("异常批次", metrics.get("abnormal_batches", "-")),
        ("质检项次 / 不合格项次",
         f"{metrics.get('total_tests','-')} / {metrics.get('fail_tests','-')}"),
        ("缺陷分布", "；".join(f"{d}×{n}" for d, n in (metrics.get("defect_distribution") or {}).items()) or "-"),
        ("近 N 日趋势", "；".join(str(x) for x in (metrics.get("trend") or [])) or "-"),
        ("命中预警", "；".join(str(x) for x in (metrics.get("alerts") or [])) or "-"),
    ]))

    # ---- 5 逐项判定与出处 ----
    add("<h2>5. 逐项判定与出处</h2>")
    add(_table(["批次号", "检测项目", "实测值", "方向", "限值", "判定", "标准号", "条款出处"],
               [(v.get("batch_id", ""), v.get("item", ""), v.get("value", ""),
                 v.get("direction", ""), v.get("limit_value", ""), v.get("verdict", ""),
                 v.get("standard", ""), v.get("limit_source", "") or "未覆盖（检索不到出处）")
                for v in (ctx.get("verdicts") or [])]))
    add(f"<div class='notice'>以上 verdict 读自 raw 快照里已由 <code>models.compare()</code> "
        f"算好的结果，任务层不重新比大小；检索不到依据的项明说「未覆盖」，不推算。</div>")

    # ---- 6 三段式结论 ----
    add("<h2>6. 三段式结论</h2>")
    add("<div class='tri'>")
    add(f"<div class='a'><b>已确认</b>{_ul(sections.get('已确认'))}</div>")
    add(f"<div class='b'><b>待人工复核</b>{_ul(sections.get('待人工复核'))}</div>")
    add(f"<div class='c'><b>未覆盖</b>{_ul(sections.get('未覆盖'))}</div>")
    add("</div>")

    # ---- 7 人工核对指路 ----
    add("<h2>7. 人工核对指路</h2>")
    cps = ctx.get("checkpoints") or []
    for c in cps:
        action = c.get("action", "")
        link = (f"<a href='{_e(action)}'>{_e(c.get('label') or action)} ↗</a>"
                if action else "<b>系统外 · 不用点这里</b>")
        add(f"<div class='notice'><b>{_e(c.get('title',''))}</b><br/>"
            f"{_e(c.get('desc',''))}<br/>入口：{link}</div>")

    # ---- 8 留痕与复算 ----
    add("<h2>8. 留痕与复算</h2>")
    add(_table(["项", "值"], [
        ("run_id", ctx.get("run_id", "")),
        ("input_digest（数据层 · 跨运行可复现）", ctx.get("input_digest", "") or "-"),
        ("conclusion_digest（结论层 · 跨运行可复现）", ctx.get("conclusion_digest", "") or "-"),
        ("report_fingerprint（产物层 · 仅单次防篡改）", "__FP__"),
        ("data_checksum（采集层 · 仅单次溯源）", ctx.get("data_checksum", "") or "-"),
        ("verify_report", "通过" if ctx.get("verify_ok") else "未通过"),
        ("raw 快照", ctx.get("snapshot_dir", "") or "-"),
        ("trace", ctx.get("trace_path", "") or "-"),
        ("报告文件", "；".join(ctx.get("outputs") or []) or "-"),
    ]))
    add("<div class='notice'>四层分工：<b>input_digest</b>（数据层，同输入两次一致）、"
        "<b>conclusion_digest</b>（结论层，同输入两次一致）承担跨运行复现；"
        "<b>report_fingerprint</b>（产物层，__FP__ + verify_report）、"
        "<b>data_checksum</b>（采集层，agent 既有审计字段，含 extracted_at 运行时信息）"
        "只用于单次运行内溯源与防篡改，<b>不做跨运行断言</b>。"
        "每个数都能从 raw 快照复算；指纹不一致 status=failed 不交付。</div>")

    add(f"<div class='sign'>{_e(_SIGN_OFF)}</div>")
    add("<footer style='margin:18px 0;color:#8f959e;font-size:12px'>"
        "FoodQC-Agent · 业务任务报告 ｜ FP __FP__</footer>")
    add("</body></html>")
    return "".join(parts)


def write_report(ctx: dict, path: str) -> tuple[str, bool]:
    """写报告 → 内嵌占位版指纹 → 用现成 verify_report() 做单次内防篡改校验。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    html = render_task_report(ctx)
    fp = fingerprint(html)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html.replace("__FP__", fp))
    ok, _ = verify_report(path)
    return fp, ok
