"""品控日报智能体 · 日报模板渲染（HTML）

输出一份自包含 HTML（内联 CSS，无外部依赖），既可在浏览器查看，
也可直接用于 PDF 导出与邮件正文。样式遵循：
- 打印友好（@page A4，表格不跨页断裂）；
- 缺陷/异常用颜色分级（红=不合格，橙=预警，蓝=提示，绿=合格）；
- 页脚带报告指纹与审计入口，保证可追溯。
"""
from __future__ import annotations

from html import escape

from .models import DailyMetrics

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
       background: #f0f2f5; color: #1f2329; font-size: 13px; line-height: 1.6; }
.page { max-width: 900px; margin: 24px auto; background: #fff; padding: 40px 48px;
        box-shadow: 0 2px 12px rgba(0,0,0,.08); }
header.report-head { border-bottom: 3px solid #b81c22; padding-bottom: 16px; margin-bottom: 20px; }
header.report-head h1 { font-size: 24px; color: #b81c22; letter-spacing: 2px; }
header.report-head .sub { color: #646a73; margin-top: 6px; font-size: 12px; }
header.report-head .sub b { color: #1f2329; }
.kpi-row { display: flex; gap: 12px; margin: 18px 0 26px; }
.kpi { flex: 1; border: 1px solid #e5e6eb; border-radius: 8px; padding: 12px 14px; background: #fafbfc; }
.kpi .label { font-size: 12px; color: #646a73; }
.kpi .value { font-size: 22px; font-weight: 700; margin-top: 2px; }
.kpi .hint { font-size: 11px; color: #8f959e; margin-top: 2px; }
.kpi.ok .value { color: #0e7a3d; } .kpi.bad .value { color: #b81c22; }
.kpi.warn .value { color: #c2771a; }
h2 { font-size: 16px; margin: 26px 0 10px; padding-left: 10px; border-left: 4px solid #b81c22; }
h3 { font-size: 13px; margin: 14px 0 6px; color: #383a42; }
table { width: 100%; border-collapse: collapse; margin: 8px 0 14px; page-break-inside: avoid; }
th { background: #f2f3f5; color: #383a42; font-weight: 600; }
th, td { border: 1px solid #dee0e3; padding: 6px 8px; text-align: left; font-size: 12px; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.fail td { background: #fef0ef; }
tr.warn td { background: #fdf6ec; }
.tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11px; }
.tag.ok { background: #e3f5ea; color: #0e7a3d; }
.tag.fail { background: #fde8e7; color: #b81c22; }
.tag.warn { background: #fdf1dc; color: #c2771a; }
.tag.info { background: #e8f1fd; color: #1664ff; }
.tag.critical { background: #fde8e7; color: #b81c22; font-weight: 700; }
.bar-row { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
.bar-row .name { width: 110px; text-align: right; color: #383a42; font-size: 12px; flex-shrink: 0; }
.bar-track { flex: 1; background: #f2f3f5; border-radius: 3px; height: 16px; position: relative; }
.bar-fill { height: 100%; border-radius: 3px; background: #1664ff; }
.bar-fill.bad { background: #d83931; } .bar-fill.warn { background: #e8a23d; }
.bar-row .val { width: 120px; font-size: 12px; color: #646a73; flex-shrink: 0; }
.alert-card { border: 1px solid #f0c8c6; border-left: 4px solid #d83931; border-radius: 6px;
              padding: 10px 14px; margin: 8px 0; background: #fef8f7; page-break-inside: avoid; }
.alert-card.warning { border-color: #f0dcb8; border-left-color: #e8a23d; background: #fdfaf3; }
.alert-card.info { border-color: #c9dbfa; border-left-color: #1664ff; background: #f5f9ff; }
.alert-card .t { font-weight: 700; }
.alert-card .d { color: #646a73; font-size: 12px; margin-top: 2px; }
.alert-card .r { color: #8f959e; font-size: 11px; margin-top: 2px; font-family: Consolas, monospace; }
.notice { border: 1px dashed #c2771a; background: #fdfaf3; color: #8a5a12;
          padding: 8px 12px; border-radius: 6px; margin: 10px 0; font-size: 12px; }
.notice.error { border-color: #d83931; background: #fef0ef; color: #8c1d18; }
.trace { background: #f7f8fa; border: 1px solid #e5e6eb; border-radius: 6px; padding: 12px 16px;
         font-size: 12px; }
.trace code { font-family: Consolas, monospace; font-size: 11px; color: #383a42; }
footer.report-foot { margin-top: 30px; padding-top: 12px; border-top: 1px solid #e5e6eb;
                     color: #8f959e; font-size: 11px; display: flex; justify-content: space-between; }
@media print {
  body { background: #fff; } .page { box-shadow: none; margin: 0; max-width: none; padding: 20px; }
  h2 { page-break-after: avoid; }
}
"""


#: 日报「需关注」筛选条件（2026-11-08 小崔定）：只展示异常，正常项汇总不逐条列。
#: 命中任一条才单独列出；都不命中的记录只进汇总句。
#: ⚠️「接近限值但未超标」按小崔口径属于**正常范围、未超标**，只汇总提示，
#: 不逐条列——否则 16 项次里会列出 14 条，日报失去重点。
ATTENTION_RULES = [
    ("超标 / 低于下限", "代码判定为「不合格」（实测值越过限值）"),
    ("真值未知 / 不判", "缺限值，或实测值含比较符 / 非数字 / 未检出 —— 系统不猜、不判"),
    ("录入疑点", "录入层标出的疑似敲错（只标疑点，不改数、不改判定）"),
]
NEAR_LIMIT_RATIO = 0.8   # 仅用于汇总提示，不作为逐条展示条件


def _attention_rows(qc_details: list[dict], suspects: list[dict]):
    """按 ATTENTION_RULES 筛选需关注记录。

    返回 (关注行 [(原因, 记录dict)], 正常项条数, 各原因计数, 接近限值但未超标条数)。
    判定逻辑完全不在这里产生：verdict / ratio 都读自已由 models.compare() 算好的快照。
    """
    suspect_keys = {(str(s.get("batch_id", "")), str(s.get("item", "")))
                    for s in (suspects or [])}
    attention: list[tuple[str, dict]] = []
    normal = 0
    near_only = 0
    counts = {name: 0 for name, _ in ATTENTION_RULES}
    for r in (qc_details or []):
        verdict = str(r.get("verdict", ""))
        try:
            ratio = float(r.get("ratio") or 0)
        except (TypeError, ValueError):
            ratio = 0.0
        key = (str(r.get("batch_id", "")), str(r.get("item", "")))
        reasons: list[str] = []
        if verdict == "不合格":
            reasons.append("超标 / 低于下限")
        elif verdict == "不判":
            reasons.append("真值未知 / 不判")
        if key in suspect_keys:
            reasons.append("录入疑点")
        if reasons:
            for x in reasons:
                counts[x] += 1
            attention.append(("＋".join(dict.fromkeys(reasons)), r))
        else:
            normal += 1
            if verdict == "合格" and ratio >= NEAR_LIMIT_RATIO:
                near_only += 1
    return attention, normal, counts, near_only


def _pct(x: float, digits: int = 2) -> str:
    return f"{x * 100:.{digits}f}%"


def _tag(verdict: str) -> str:
    return f'<span class="tag {"ok" if verdict == "合格" else "fail"}">{verdict}</span>'


def _level_tag(level: str) -> str:
    m = {"critical": "不合格", "warning": "预警", "info": "提示"}
    return f'<span class="tag {level}">{m.get(level, level)}</span>'


def _bar(name: str, value: float, max_value: float, label: str, cls: str = "") -> str:
    width = 0 if max_value <= 0 else min(100.0, value / max_value * 100)
    return (f'<div class="bar-row"><div class="name">{name}</div>'
            f'<div class="bar-track"><div class="bar-fill {cls}" style="width:{width:.1f}%"></div></div>'
            f'<div class="val">{label}</div></div>')


def render_html(m: DailyMetrics, meta: dict) -> str:
    """渲染日报 HTML。meta 需含 run_id / fingerprint / source_type / audit_path /
    provenance / errors；可选 suspects（录入自查疑点，见 validate.validate_records）。
    """
    parts: list[str] = []
    add = parts.append

    add("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>")
    add(f"<title>{meta.get('title', '品控日报')} {m.report_date}</title>")
    add("<meta name='report-fingerprint' content='__FP__'>")  # 由编排器回填真实指纹
    add(f"<style>{_CSS}</style></head><body><div class='page'>")

    # ---------- 报告头 ----------
    add("<header class='report-head'>")
    add(f"<h1>{meta.get('title', '米粮品控日报（稻谷验收）')}</h1>")
    add(f"<div class='sub'>报告日期：<b>{m.report_date}</b>　|　生产基地：{m.plant}　|　"
        f"生成时间：{m.generated_at}　|　数据来源：{meta.get('source_type', '')}　|　"
        f"运行编号：<code>{meta.get('run_id', '')}</code></div>")
    add("</header>")

    # ---------- 标准版本警告（报告顶部置顶，未核对时不可跳过） ----------
    banner = meta.get("warning_banner", "")
    if banner:
        add(f"<div class='notice error'>{banner}</div>")

    # ---------- 数据校验提示（若有 error 级问题，整份报告带显著警示） ----------
    errors = [v for v in m.validation_issues if v.level == "error"]
    warnings = [v for v in m.validation_issues if v.level == "warning"]
    if errors:
        add(f"<div class='notice error'>⛔ 数据校验未通过（{len(errors)} 项错误），"
            f"本报告数据可信度存疑，禁止直接用于放行决策，请人工复核："
            f"{'；'.join(e.message for e in errors[:5])}</div>")
    if warnings:
        add(f"<div class='notice'>⚠️ 数据校验提示（{len(warnings)} 项）："
            f"{'；'.join(w.message for w in warnings[:5])}</div>")

    # ---------- 结论先行 + 展示口径（2026-11-08 小崔定：结果导向、只列异常） ----------
    qc_all = meta.get("qc_details") or []
    suspects_all = meta.get("suspects") or []
    attention, normal_n, reason_counts, near_only = _attention_rows(qc_all, suspects_all)
    attention_batches = sorted({str(r.get("batch_id", "")) for _, r in attention})
    fail_n = sum(1 for _, r in attention if r.get("verdict") == "不合格")
    nojudge_n = sum(1 for _, r in attention if r.get("verdict") == "不判")

    add("<h2>今日结论（结果导向）</h2>")
    if attention:
        lead = (f"今日共 <b>{len(qc_all)}</b> 项次纳入判定：<b>{fail_n}</b> 项次超标（涉及 "
                f"<b>{len(attention_batches)}</b> 个批次）需逐条核对；"
                f"真值未知不判 <b>{nojudge_n}</b> 项次、录入疑点 "
                f"<b>{reason_counts['录入疑点']}</b> 项次。<br>"
                f"其余 <b>{normal_n}</b> 项次在限值内、未超标，<b>不单独展示</b>"
                f"（其中 {near_only} 项次接近限值但未超标，仅在此提示，不逐条列）。")
        add(f"<div class='notice'>{lead}<br>"
            "<b>需人工处理：</b>下面「需关注明细」逐条给出实测值、限值、标准号与出处；"
            "Agent 只把该看的行指出来，<b>不放行、不替代签字</b>，收不收由人定。</div>")
    else:
        near_note = (f"其中 {near_only} 项次接近限值但未超标，仅作提示。" if near_only else "")
        add(f"<div class='notice'>今日 {len(qc_all)} 项次全部在限值内、未超阈值，"
            f"无超标、无录入疑点、无真值未知项。{near_note}"
            "<b>明细不逐条罗列。</b>放行仍以人工复核签字为准。</div>")

    # 筛选条件与最终展示范围（必须写在日报里，让看的人知道为什么只看到这几行）
    rule_lines = "".join(
        f"<li><b>{escape(name)}</b>：{escape(desc)}</li>" for name, desc in ATTENTION_RULES)
    add("<div class='trace' style='margin:10px 0 18px'>")
    add("<div style='font-weight:700;margin-bottom:4px'>本日报的展示口径（筛选条件与范围）</div>")
    add("<p style='margin:4px 0'><b>筛选条件</b>：逐条记录命中下列任一条，才单独列出：</p>")
    add(f"<ul style='margin:4px 0 10px 20px'>{rule_lines}</ul>")
    add(f"<p style='margin:4px 0'><b>最终展示范围</b>：本次逐条展示 "
        f"<b>{len(attention)}</b> 条需关注记录"
        f"（超标 {reason_counts['超标 / 低于下限']} · 真值未知/不判 "
        f"{reason_counts['真值未知 / 不判']} · 录入疑点 {reason_counts['录入疑点']}，"
        f"同一条可同时命中多条）；其余 <b>{normal_n}</b> 条未超标项<b>不单独展示</b>，"
        "只作本段汇总说明，不在任何明细表中逐条罗列。</p>")
    add(f"<p style='margin:4px 0'><b>特别说明</b>：另有 <b>{near_only}</b> 项次"
        f"「接近限值」（利用率 ≥ {int(NEAR_LIMIT_RATIO * 100)}%）但<b>未超标</b>，"
        "按口径归为正常范围，只在此汇总提示、不逐条列；如要单独关注，"
        "需另行调整阈值口径，由人确认后再改。</p>")
    add("<p style='margin:4px 0;color:#8f959e'>判定本身由代码按配置限值产生，"
        "与展示筛选无关：筛选只决定「哪几行给人看」，不改变任何 verdict。</p>")
    add("</div>")

    # ---------- KPI ----------
    crit = sum(1 for a in m.alerts if a.level == "critical")
    warn = sum(1 for a in m.alerts if a.level == "warning")
    info = sum(1 for a in m.alerts if a.level == "info")
    pass_cls = "ok" if m.pass_rate >= 0.98 else ("bad" if m.pass_rate < 0.95 else "warn")
    # 没采集到生产记录时，一次交验合格率 / 异常批次 / 批次列都算不出来，
    # 不许印 0.00% 或 0 让签字人误读（2026-11-08 夜测 S2/S3）
    has_prod = m.batch_count > 0
    na = "—（无生产记录）"
    add("<div class='kpi-row'>")
    add(f"<div class='kpi {pass_cls}'><div class='label'>总体合格率</div>"
        f"<div class='value'>{_pct(m.pass_rate)}</div>"
        f"<div class='hint'>{m.total_tests - m.fail_tests}/{m.total_tests} 项次合格</div></div>")
    add(f"<div class='kpi'><div class='label'>一次交验合格率</div>"
        f"<div class='value'>{_pct(m.batch_pass_rate) if has_prod else na}</div>"
        f"<div class='hint'>{'全部项目一次通过的批次占比' if has_prod else '未采集到生产记录，本项不计算'}</div></div>")
    add(f"<div class='kpi'><div class='label'>产量达成率</div>"
        f"<div class='value'>{_pct(m.achievement, 1) if m.planned_qty > 0 else '无产量数据'}</div>"
        f"<div class='hint'>{f'{m.actual_qty:g} / {m.planned_qty:g} 吨 · {m.batch_count} 批次' if m.planned_qty > 0 else '未采集到生产记录'}</div></div>")
    add(f"<div class='kpi {'bad' if m.abnormal_batches else 'ok'}'><div class='label'>异常批次</div>"
        f"<div class='value'>{len(m.abnormal_batches) if has_prod else na}</div>"
        f"<div class='hint'>{'不合格 ' + str(sum(1 for b in m.abnormal_batches if b.level == 'critical')) + ' · ' if has_prod else ''}"
        f"{('预警 ' + str(sum(1 for b in m.abnormal_batches if b.level == 'warning')) + ' · ') if has_prod else ''}"
        f"{('提示 ' + str(sum(1 for b in m.abnormal_batches if b.level == 'info'))) if has_prod else '未采集到生产记录，本项不计算'}</div></div>")
    add(f"<div class='kpi {'bad' if crit else 'ok'}'><div class='label'>当日预警</div>"
        f"<div class='value'>{len(m.alerts)}</div>"
        f"<div class='hint'>严重 {crit} · 警告 {warn} · 提示 {info}</div></div>")
    add("</div>")

    # ---------- ⚠️ 待人工复核（录入错误自查，必须排在合格率之前） ----------
    # 这一层只回答「数是不是敲错了」，不回答「合不合格」；
    # 与后面的合格率是前后两道关：先人工确认数据，再看判定结果。
    suspects = meta.get("suspects") or []
    add("<h2>⚠️ 待人工复核</h2>")
    if suspects:
        add(f"<div class='notice'>共发现 {len(suspects)} 条录入疑点，全部标为「待人工确认」。"
            f"本区块不修改任何数值、不改写合格判定、不自动放行；"
            f"请先人工核对原始检验记录，再看下面的合格率。</div>")
        add("<table><tr><th>批次号</th><th>检测项目</th><th>实测值</th>"
            "<th>疑点（大白话）</th><th>建议动作</th></tr>")
        for s in suspects:
            add(f"<tr class='warn'><td>{escape(str(s.get('batch_id', '')))}</td>"
                f"<td>{escape(str(s.get('item', '')))}</td>"
                f"<td class='num'>{escape(str(s.get('value', '')))}</td>"
                f"<td>{escape(str(s.get('reason', '')))}</td>"
                f"<td>{escape(str(s.get('建议动作', '')))}</td></tr>")
        add("</table>")
    else:
        add("<div class='notice'>今日无录入疑点（已自查值域 / 相邻批次量级跳变 / "
            "小数位 / 跨列逻辑 / 重复与漏填）。</div>")

    # ---------- 一、合格率分析 ----------
    add("<h2>一、合格率分析</h2>")
    add("<h3>分产品合格率</h3>")
    add("<table><tr><th>产品</th><th class='num'>批次</th><th class='num'>检测项次</th>"
        "<th class='num'>不合格项次</th><th class='num'>项目合格率</th><th class='num'>一次交验合格率</th>"
        "<th class='num'>计划产量(t)</th><th class='num'>实际产量(t)</th></tr>")
    for p in m.products:
        cls = " class='fail'" if p.fail_tests else ""
        add(f"<tr{cls}><td>{p.product}</td><td class='num'>{p.batches if has_prod else '—'}</td>"
            f"<td class='num'>{p.total_tests}</td><td class='num'>{p.fail_tests}</td>"
            f"<td class='num'>{_pct(p.pass_rate)}</td>"
            f"<td class='num'>{_pct(p.batch_pass_rate) if has_prod else '—'}</td>"
            f"<td class='num'>{p.planned_qty:g}</td><td class='num'>{p.actual_qty:g}</td></tr>")
    add(f"<tr style='font-weight:700'><td>合计</td><td class='num'>{m.batch_count if has_prod else '—'}</td>"
        f"<td class='num'>{m.total_tests}</td><td class='num'>{m.fail_tests}</td>"
        f"<td class='num'>{_pct(m.pass_rate)}</td>"
        f"<td class='num'>{_pct(m.batch_pass_rate) if has_prod else '—'}</td>"
        f"<td class='num'>{m.planned_qty:g}</td><td class='num'>{m.actual_qty:g}</td></tr></table>")

    # 只列需要关注的项目；全部合格的项目汇总成一句，不逐条列（结果导向、避免全量罗列）
    add("<h3>分检测项目（仅列需关注项）</h3>")
    watch_items = [it for it in m.items if it.fail > 0]
    ok_items = [it for it in m.items if it.fail == 0]
    if watch_items:
        add("<table><tr><th>检测项目</th><th>单位</th><th class='num'>检测项次</th>"
            "<th class='num'>不合格项次</th><th class='num'>合格率</th><th>判定</th></tr>")
        for it in watch_items:
            add(f"<tr class='fail'><td>{it.item}</td><td>{it.unit}</td><td class='num'>{it.total}</td>"
                f"<td class='num'>{it.fail}</td><td class='num'>{_pct(it.pass_rate)}</td>"
                f"<td>{_tag('不合格')}</td></tr>")
        add("</table>")
    else:
        add("<div class='notice'>今日所有检测项目均未出现不合格项次，本表不逐条罗列。</div>")
    if ok_items:
        add(f"<p style='color:#8f959e;margin-top:6px'>另有 <b>{len(ok_items)}</b> 个检测项目"
            f"（{ '、'.join(escape(it.item) for it in ok_items[:8]) }"
            f"{'…' if len(ok_items) > 8 else ''}）全部合格、未超阈值，"
            "按展示口径仅作本句汇总，不单独列出。</p>")

    # ---- 需关注明细：逐条给出依据，只列命中筛选条件的记录 ----
    add("<h2>需关注明细（逐条结论与依据）</h2>")
    if attention:
        add("<table><tr><th>批次号</th><th>检测项目</th><th>需关注原因</th>"
            "<th class='num'>实测值</th><th>方向</th><th class='num'>限值</th><th>判定</th>"
            "<th>标准号</th><th>条款出处</th></tr>")
        for reason, r in attention:
            cls = " class='fail'" if r.get("verdict") == "不合格" else (
                  " class='warn'" if r.get("verdict") == "不判" else "")
            add(f"<tr{cls}><td><code>{escape(str(r.get('batch_id', '')))}</code></td>"
                f"<td>{escape(str(r.get('item', '')))}</td>"
                f"<td>{escape(reason)}</td>"
                f"<td class='num'>{escape(str(r.get('value', '')))}</td>"
                f"<td>{escape(str(r.get('direction', '')))}</td>"
                f"<td class='num'>{escape(str(r.get('limit_value', '')))}</td>"
                f"<td>{escape(str(r.get('verdict', '')))}</td>"
                f"<td>{escape(str(r.get('standard', '')))}</td>"
                f"<td>{escape(str(r.get('limit_source', '')))}</td></tr>")
        add("</table>")
    else:
        add("<div class='notice'>今日无需关注记录（无超标、无接近限值、无录入疑点、无真值未知项）。</div>")

    # ---------- 未参与判定的记录 ----------
    # 这些行在导入时就被跳过了（公式没存结果 / 写了比较符或未检出），根本没进判定。
    # 只在任务卡上提一句不够：看报告、签字的人也必须知道"这一行没被算过"，
    # 否则会觉得"没出现 = 没问题"（夜测 2026-11-09 S2）。
    skipped_rows = [str(n) for n in (meta.get("import_notes") or []) if "没进判定" in str(n)]
    if skipped_rows:
        add("<h2>未参与判定的记录</h2>")
        add(f"<p>本次有 <b>{len(skipped_rows)}</b> 条记录<b>没进判定</b>——"
            "它们既没被算成合格，也没被算成不合格，原值仍在原始表里。</p>")
        add("<ul>" + "".join(f"<li>{escape(n)}</li>" for n in skipped_rows) + "</ul>")
        add("<p style='color:#8f959e'>常见原因：实测值是 Excel 公式但没保存计算结果，或写成 "
            "「&lt;0.5」「未检出」这类读不出数的写法。补齐后重跑即可；"
            "系统不会替这些格子猜数值。</p>")

    # ---------- 二、缺陷分布 ----------
    add("<h2>二、缺陷分布</h2>")
    if m.defect_distribution:
        max_fail = max(d["fail"] for d in m.defect_distribution)
        add("<h3>不合格项次分布（按检测项目）</h3>")
        for d in m.defect_distribution:
            add(_bar(d["item"], d["fail"], max_fail,
                     f"{d['fail']} 项次 · {_pct(d['share'], 1)}", cls="bad"))
        add("<table><tr><th>检测项目</th><th>单位</th><th class='num'>不合格项次</th>"
            "<th class='num'>占全部缺陷比例</th><th>涉及批次</th></tr>")
        for d in m.defect_distribution:
            add(f"<tr class='fail'><td>{d['item']}</td><td>{d['unit']}</td>"
                f"<td class='num'>{d['fail']}</td><td class='num'>{_pct(d['share'], 1)}</td>"
                f"<td><code>{', '.join(d['fail_batches'])}</code></td></tr>")
        add("</table>")
    else:
        add("<p>当日无不合格项次，缺陷分布为空。</p>")

    # ---------- 三、异常批次明细 ----------
    add("<h2>三、异常批次明细</h2>")
    if m.abnormal_batches:
        add(f"<p>共 <b>{len(m.abnormal_batches)}</b> 个批次触发异常（不合格 / 接近限值 / 仓储环境超限），"
            f"明细如下（按最大限值利用率降序）：</p>")
        add("<table><tr><th>批次号</th><th>产品</th><th>产线</th><th>级别</th>"
            "<th class='num'>最大利用率</th><th>异常原因</th></tr>")
        for b in m.abnormal_batches:
            cls = "fail" if b.level == "critical" else ("warn" if b.level == "warning" else "")
            row_cls = f" class='{cls}'" if cls else ""
            add(f"<tr{row_cls}><td><code>{b.batch_id}</code></td><td>{b.product}</td><td>{b.line}</td>"
                f"<td>{_level_tag(b.level)}</td><td class='num'>{b.worst_ratio:.0%}</td>"
                f"<td>{'<br>'.join(b.reasons)}</td></tr>")
        add("</table>")
    else:
        # 没采集到生产记录时 abnormal_batches 必然为空，但「空」不等于「当日无异常」：
        # 同一页上方可能正列着若干项次不合格（夜测 2026-11-09 S1）。签字人翻到这一页
        # 读到"无异常"是最危险的，所以这里必须按「算不出来」说，而不是按「没有」说。
        if not has_prod:
            if m.fail_tests:
                add(f"<p><b>—（无生产记录，无法按批次归集）</b>：本次共 <b>{m.fail_tests}</b> 项次不合格，"
                    "逐条见上方「需关注明细（逐条结论与依据）」——<b>这不等于当日无异常</b>。</p>")
            else:
                add("<p>—（无生产记录）：无法按批次归集；按项次统计，本次无不合格项次。</p>")
        else:
            add("<p>当日无异常批次。</p>")

    # ---------- 四、趋势分析 ----------
    add("<h2>四、趋势分析</h2>")
    if len(m.trend_points) >= 2:
        add(f"<h3>近 {len(m.trend_points)} 日合格率走势（{m.trend_summary.get('comment', '')}）</h3>")
        for tp in m.trend_points:
            add(_bar(tp.date, tp.pass_rate, 1.0,
                     f"合格率 {_pct(tp.pass_rate)} · 不合格 {tp.fail} 项次 · 异常批次 {tp.abnormal_batches}",
                     cls="bad" if tp.pass_rate < 0.95 else ("warn" if tp.pass_rate < 0.98 else "")))
        add("<table><tr><th>日期</th><th class='num'>合格率</th><th class='num'>检测项次</th>"
            "<th class='num'>不合格项次</th><th class='num'>异常批次</th></tr>")
        for tp in m.trend_points:
            cls = " class='fail'" if tp.fail else ""
            add(f"<tr{cls}><td>{tp.date}</td><td class='num'>{_pct(tp.pass_rate)}</td>"
                f"<td class='num'>{tp.total}</td><td class='num'>{tp.fail}</td>"
                f"<td class='num'>{tp.abnormal_batches}</td></tr>")
        add("</table>")
        # 分项目均值趋势
        item_names = sorted({t.item for t in m.item_trend})
        if item_names:
            add("<h3>分检测项目均值走势</h3>")
            add("<table><tr><th>日期</th>" + "".join(f"<th class='num'>{n}</th>" for n in item_names) + "</tr>")
            dates = sorted({t.date for t in m.item_trend})
            lookup = {(t.date, t.item): t for t in m.item_trend}
            for d in dates:
                cells = []
                for n in item_names:
                    t = lookup.get((d, n))
                    cells.append(f"<td class='num'>{t.mean_value:g}{t.unit}</td>" if t
                                 else "<td class='num'>-</td>")
                add(f"<tr><td>{d}</td>{''.join(cells)}</tr>")
            add("</table>")
    else:
        add("<p>历史数据不足，暂无法呈现趋势。连续运行数日后将自动生成近 7 日走势。</p>")

    # ---------- 五、预警汇总 ----------
    add("<h2>五、预警汇总</h2>")
    if m.alerts:
        for a in m.alerts:
            refs = f"<div class='r'>关联记录：{', '.join(a.refs[:12])}</div>" if a.refs else ""
            add(f"<div class='alert-card {a.level}'><div class='t'>[{a.level.upper()}] {a.title}</div>"
                f"<div class='d'>{a.detail}</div>{refs}</div>")
    else:
        add("<p>当日无预警命中，各项指标处于受控范围。</p>")

    # ---------- 六、数据溯源与审计 ----------
    add("<h2>六、数据溯源与审计</h2>")
    add("<div class='trace'>")
    add("<table><tr><th>来源系统</th><th>类型</th><th class='num'>记录数</th>"
        "<th>数据窗口</th><th>记录指纹(SHA-256 前16位)</th></tr>")
    for p in meta.get("provenance", []):
        add(f"<tr><td>{p['system']}</td><td>{p['source_type']}</td><td class='num'>{p['record_count']}</td>"
            f"<td>{p['window_start']} ~ {p['window_end']}</td>"
            f"<td><code>{p['checksum'][:16]}</code></td></tr>")
    add("</table>")
    add(f"<p>原始数据快照指纹：<code>{meta.get('data_checksum', '')[:32]}</code></p>")
    add(f"<p>本报告指纹：<code>__FP__</code>（占位符渲染后计算，见审计日志）</p>")
    add(f"<p>审计日志：<code>{meta.get('audit_path', '')}</code>（只追加，不可篡改）</p>")
    add(f"<p>采集快照目录：<code>{meta.get('snapshot_dir', '')}</code></p>")
    add("<p style='margin-top:8px;color:#8f959e'>说明：本报告全部指标由程序按配置限值计算生成，"
        "未经过模型推算；每个数字均可通过上方指纹与记录号回溯至原始系统记录。"
        "本报告仅作过程监控参考，放行决策以人工复核与检验报告为准。</p>")
    add("</div>")

    # ---------- 附：全量快照（默认折叠，供审计复算；不占日报主视图） ----------
    # 2026-11-08 小崔定：日报只列需关注项，正常项汇总不逐条罗列。
    # 全量逐条数据仍保留给审计/复算，但收进折叠区，避免主视图变成全量台账。
    qc_details = meta.get("qc_details") or []
    if qc_details:
        add("<details style='margin-top:18px;border-top:1px solid #e3e6eb;padding-top:10px'>")
        add(f"<summary style='cursor:pointer;color:#4b535c;font-weight:600'>"
            f"附：全量质检快照（{len(qc_details)} 条，审计复算用，默认收起）</summary>")
        add("<p style='color:#8f959e;margin:6px 0'>日常阅读不需要展开。"
            "主视图只展示需关注项；此区保留全部逐条记录供指纹复算与追溯。</p>")
        add("<table><tr><th>批次号</th><th>检测项目</th><th class='num'>实测值</th>"
            "<th>方向</th><th class='num'>限值</th><th>判定</th>"
            "<th>标准号</th><th>条款出处</th></tr>")
        for r in qc_details:
            cls = " class='fail'" if r.get("verdict") == "不合格" else ""
            add(f"<tr{cls}><td><code>{escape(str(r.get('batch_id', '')))}</code></td>"
                f"<td>{escape(str(r.get('item', '')))}</td>"
                f"<td class='num'>{escape(str(r.get('value', '')))}</td>"
                f"<td>{escape(str(r.get('direction', '')))}</td>"
                f"<td class='num'>{escape(str(r.get('limit_value', '')))}</td>"
                f"<td>{escape(str(r.get('verdict', '')))}</td>"
                f"<td>{escape(str(r.get('standard', '')))}</td>"
                f"<td>{escape(str(r.get('limit_source', '')))}</td></tr>")
        add("</table></details>")

    add("<div class='notice error' style='margin-top:14px'>"
        "本结论仅供复核，最终由人签字放行；系统不改数值、不自动判合格、不替代人工签字。</div>")

    # ---------- 页脚 ----------
    add(f"<footer class='report-foot'><span>{meta.get('title', '食用油中控品控日报')} · "
        f"{m.report_date}</span><span>RunID {meta.get('run_id', '')} · "
        f"FP __FP__</span></footer>")

    add("</div></body></html>")
    return "".join(parts)
