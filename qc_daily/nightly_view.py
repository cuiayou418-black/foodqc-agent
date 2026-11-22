"""每晚巡检 · 只读看板

职责：把 nightly/ 里**已经写好的**账本（轮次日志 / FINDINGS / LEARNINGS / 夜测报告）
读出来拼成一张网页。不重跑巡检、不写文件、不碰判定逻辑、不回传本机绝对路径。

路由在 client_ui.py 挂：GET /nightly、GET /nightly/file/<文件名>
"""
from __future__ import annotations

import html
import re
from collections import defaultdict
from pathlib import Path

# qc_daily/nightly_view.py → 项目根 → nightly/
NIGHTLY = Path(__file__).resolve().parent.parent / "nightly"

# 允许被网页打开的文件名（白名单，防止 ../ 穿越）
_ALLOWED_NAME = re.compile(
    r"^(nightly-\d{4}-\d{2}-\d{2}(?:-[a-z])?-r\d{2}\.md"
    r"|FINDINGS\.md|LEARNINGS\.md"
    r"|夜测报告_\d{4}-\d{2}-\d{2}\.md)$"
)


def file_text(name: str) -> str | None:
    """按白名单读一份账本原文；找不到返回 None。"""
    if not _ALLOWED_NAME.match(name or ""):
        return None
    for base in (NIGHTLY / "rounds", NIGHTLY):
        path = base / name
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    return None


def _collect() -> dict:
    """解析磁盘上现成的账本，返回页面要用的数据。"""
    rounds: dict[str, list[dict]] = defaultdict(list)
    for path in sorted((NIGHTLY / "rounds").glob("nightly-*.md")):
        m = re.search(r"nightly-(\d{4}-\d{2}-\d{2})(-[a-z])?-r(\d{2})\.md$", path.name)
        if not m:
            continue
        day, suffix, rnd = m.group(1), m.group(2) or "", int(m.group(3))
        text = path.read_text(encoding="utf-8", errors="replace")
        status = re.findall(r"^## \[(PASS|WARN|FAIL)\] ", text, re.M)
        den = re.search(r"^- 执行数 (\d+) ／ 有效判定数 (\d+) ／ 实际触达数 (\d+)", text, re.M)
        neg = re.search(r"^- 已执行 (\d+) ／ 触达判据 (\d+) ／ 确实拦住 (\d+)", text, re.M)
        service = re.search(r"^服务：(.+)$", text, re.M)
        rounds[day].append({
            "name": path.name, "r": rnd, "suffix": suffix,
            "n_pass": status.count("PASS"), "n_warn": status.count("WARN"),
            "n_fail": status.count("FAIL"),
            "den": "/".join(den.groups()) if den else "—",
            "neg": "/".join(neg.groups()) if neg else "—",
            "service": service.group(1).strip() if service else "",
        })

    findings: dict[str, list[dict]] = defaultdict(list)
    section = None
    findings_path = NIGHTLY / "FINDINGS.md"
    if findings_path.is_file():
        for line in findings_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^## (\d{4}-\d{2}-\d{2}) 每晚巡检 · 第 (\d+) 轮(.+)$", line)
            if m:
                section = {"day": m.group(1), "r": int(m.group(2)),
                           "title": m.group(3).strip(" （）"), "sev": defaultdict(int)}
                findings[section["day"]].append(section)
                continue
            if section and line.strip():
                for sev in ("严重", "一般", "轻微"):
                    if re.search(r"\[" + sev + r"[^\]]*\]", line):
                        section["sev"][sev] += 1

    learn: dict[str, list[dict]] = defaultdict(list)
    cur = None
    learn_path = NIGHTLY / "LEARNINGS.md"
    if learn_path.is_file():
        for line in learn_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^### (\d{4}-\d{2}-\d{2}) · 第 (\d+) 轮学习", line)
            if m:
                cur = {"day": m.group(1), "r": int(m.group(2)), "title": ""}
                learn[cur["day"]].append(cur)
                continue
            if cur and not cur["title"] and line.startswith("- **看了什么**"):
                cur["title"] = line.split("：", 1)[-1].split("·")[0].strip()[:120]

    reports = {}
    for path in NIGHTLY.glob("夜测报告_*.md"):
        day = path.name.replace("夜测报告_", "").replace(".md", "")
        reports[day] = path.name

    days = sorted(set(rounds) | set(findings) | set(learn) | set(reports), reverse=True)
    return {
        "days": days, "rounds": rounds, "findings": findings,
        "learn": learn, "reports": reports,
        "n_rounds": sum(len(v) for v in rounds.values()),
        "n_findings": sum(sum(s["sev"].values()) for v in findings.values() for s in v),
        "n_learn": sum(len(v) for v in learn.values()),
    }


_CSS = r"""
:root{--bg:#fff;--side:#eef7f3;--ink:#1f2328;--ink2:#4a5058;--ink3:#8b949e;
--line:#e4ebe7;--brand:#0f6e56;--brand-soft:#dcefe8;--ok:#0f6e56;--warn:#8a5a06;
--bad:#a32d2d;--code:#f6f8f7}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.app{display:flex;min-height:100vh}
.side{width:252px;flex:0 0 252px;background:var(--side);border-right:1px solid var(--line);
padding:14px 12px;position:sticky;top:0;height:100vh;overflow-y:auto}
.logo{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:600;margin:2px 0 14px;padding-left:4px}
.logo .dot{width:12px;height:12px;border-radius:4px;background:var(--brand)}
.ver{font-size:9px;letter-spacing:1px;color:var(--ink3);border:1px solid var(--line);border-radius:3px;padding:0 4px}
.side-link{display:block;text-decoration:none;color:var(--ink2);font-size:13px;padding:7px 8px;
border-radius:6px;margin-bottom:2px}
.side-link:hover,.side-link.on{background:var(--brand-soft);color:var(--brand)}
.side-note{margin-top:18px;padding:9px 8px;font-size:11px;color:var(--ink3);border-top:1px solid var(--line)}
.main{flex:1;min-width:0;padding:28px 34px 60px}
h1{font-size:24px;margin:0 0 4px}.sub{color:var(--ink2);margin:0 0 18px;font-size:13px}
.notice{background:var(--brand-soft);border-left:3px solid var(--brand);padding:9px 13px;
border-radius:5px;font-size:12.5px;color:var(--ink2);margin-bottom:20px}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:12px;margin-bottom:22px}
.kpi{border:1px solid var(--line);border-radius:10px;padding:12px 15px}
.kpi b{display:block;font-size:24px;color:var(--brand);line-height:1.2}
.kpi span{font-size:11px;color:var(--ink3)}
.day{border:1px solid var(--line);border-radius:12px;margin-bottom:14px;overflow:hidden}
.day>h2{margin:0;padding:10px 16px;background:#f4f8f6;font-size:15px;border-bottom:1px solid var(--line)}
.body{padding:10px 16px 14px}
.sec{font-size:12px;color:var(--ink3);margin:10px 0 5px;font-weight:600}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--ink3);font-weight:600;font-size:11px}
a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}
code{font:11.5px ui-monospace,Consolas,monospace;background:var(--code);padding:1px 5px;border-radius:4px}
.p{color:var(--ok);font-weight:600}.w{color:var(--warn);font-weight:600}.f{color:var(--bad);font-weight:600}
.tag{display:inline-block;font-size:11px;border:1px solid var(--line);border-radius:20px;padding:0 8px;margin-right:6px;color:var(--ink3)}
ul{margin:5px 0;padding-left:20px}li{margin:2px 0;color:var(--ink2)}
@media(max-width:760px){.side{display:none}.main{padding:16px}.kpis{grid-template-columns:repeat(2,1fr)}}
"""


def render_html() -> str:
    """返回整页 HTML（服务端渲染，无前端依赖）。"""
    d = _collect()
    e = html.escape
    out = [f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>每晚巡检 · FoodQC-Agent</title><style>{_CSS}</style></head><body>
<div class="app">
<aside class="side">
  <div class="logo"><span class="dot"></span>FoodQC-Agent <span class="ver">NIGHTLY</span></div>
  <a class="side-link" href="/app">← 返回对话客户端</a>
  <a class="side-link on" href="/nightly">🌙 每晚巡检</a>
  <a class="side-link" href="/">⌂ 品控日报后台</a>
  <div class="side-note">只读账本页：数据来自磁盘上的 nightly/ 目录，不重跑巡检、不改判定、不放行。</div>
</aside>
<main class="main">
<h1>🌙 每晚巡检 · 账本</h1>
<p class="sub">每天 0:00–5:59 每小时一轮，共 6 轮；每轮真实跑场景、写日志、记问题、做一条学习。</p>
<div class="notice"><b>怎么看：</b>点每轮的 <code>rNN</code> 打开原始日志；<b>一般 / 严重</b>才是要拍板的，轻微是文案和排版；
「已修确认」表示白天改完后我在下一轮复验过。原始账本全文见 <a href="/nightly/file/FINDINGS.md">FINDINGS.md</a> 与
<a href="/nightly/file/LEARNINGS.md">LEARNINGS.md</a>。</div>
<div class="kpis">
  <div class="kpi"><b>{len(d['days'])}</b><span>覆盖天数</span></div>
  <div class="kpi"><b>{d['n_rounds']}</b><span>轮次日志</span></div>
  <div class="kpi"><b>{d['n_findings']}</b><span>问题条目（含复发）</span></div>
  <div class="kpi"><b>{d['n_learn']}</b><span>学习条目</span></div>
</div>"""]

    for day in d["days"]:
        out.append(f'<div class="day"><h2>{e(day)}</h2><div class="body">')
        rs = sorted(d["rounds"].get(day, []), key=lambda x: (x["r"], x["suffix"]))
        if rs:
            out.append('<div class="sec">本轮场景（点编号看原始日志全文）</div>'
                       '<table><tr><th>轮</th><th>PASS / WARN / FAIL</th><th>分母 执行/有效/触达</th>'
                       '<th>负控 执行/触达/拦住</th><th>服务来源</th></tr>')
            for x in rs:
                out.append(
                    f'<tr><td><a href="/nightly/file/{e(x["name"])}">r{x["r"]:02d}{e(x["suffix"])}</a></td>'
                    f'<td><span class="p">{x["n_pass"]}</span> / <span class="w">{x["n_warn"]}</span>'
                    f' / <span class="f">{x["n_fail"]}</span></td>'
                    f'<td>{e(x["den"])}</td><td>{e(x["neg"])}</td><td>{e(x["service"])}</td></tr>')
            out.append("</table>")
        fs = d["findings"].get(day, [])
        if fs:
            out.append('<div class="sec">bug 台账 · FINDINGS.md</div>')
            for s in sorted(fs, key=lambda x: x["r"]):
                sev = " · ".join(f"{k} {v}" for k, v in s["sev"].items() if v) or "0 条"
                out.append(f'<p style="margin:4px 0"><span class="tag">第 {s["r"]} 轮</span>'
                           f'<b>{e(sev)}</b> <span style="color:var(--ink3)">{e(s["title"])}</span></p>')
        ls = d["learn"].get(day, [])
        if ls:
            out.append('<div class="sec">当晚学习 · LEARNINGS.md（B/C 级均为线索，未看正文）</div><ul>')
            for s in ls:
                out.append(f'<li>第 {s["r"]} 轮 · {e(s["title"] or "标题待解析")}</li>')
            out.append("</ul>")
        if day in d["reports"]:
            out.append(f'<p><a href="/nightly/file/{e(d["reports"][day])}">'
                       f'📄 打开当晚汇总 夜测报告_{e(day)}.md</a></p>')
        out.append("</div></div>")

    out.append('<p style="color:var(--ink3);font-size:12px">本页只读；数据来源为磁盘现有账本，'
               '未修改任何源数据，也不构成放行依据。</p></main></div></body></html>')
    return "\n".join(out)
