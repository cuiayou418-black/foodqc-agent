"""品控日报智能体 · 日报模板渲染（HTML）

输出一份自包含 HTML（内联 CSS，无外部依赖），既可在浏览器查看，
也可直接用于 PDF 导出与邮件正文。样式遵循：
- 打印友好（@page A4，表格不跨页断裂）；
- 缺陷/异常用颜色分级（红=不合格，橙=预警，蓝=提示，绿=合格）；
- 页脚带报告指纹与审计入口，保证可追溯。
"""
from __future__ import annotations

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
    """渲染日报 HTML。meta 需含 run_id / fingerprint / source_type / audit_path / provenance / errors。"""
    parts: list[str] = []
    add = parts.append

    add("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>")
    add(f"<title>{meta.get('title', '品控日报')} {m.report_date}</title>")
    add("<meta name='report-fingerprint' content='__FP__'>")  # 由编排器回填真实指纹
    add(f"<style>{_CSS}</style></head><body><div class='page'>")

    # ---------- 报告头 ----------
    add("<header class='report-head'>")
    add(f"<h1>{meta.get('title', '食用油中控品控日报')}</h1>")
    add(f"<div class='sub'>报告日期：<b>{m.report_date}</b>　|　生产基地：{m.plant}　|　"
        f"生成时间：{m.generated_at}　|　数据来源：{meta.get('source_type', '')}　|　"
        f"运行编号：<code>{meta.get('run_id', '')}</code></div>")
    add("</header>")

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

    # ---------- KPI ----------
    crit = sum(1 for a in m.alerts if a.level == "critical")
    warn = sum(1 for a in m.alerts if a.level == "warning")
    info = sum(1 for a in m.alerts if a.level == "info")
    pass_cls = "ok" if m.pass_rate >= 0.98 else ("bad" if m.pass_rate < 0.95 else "warn")
    add("<div class='kpi-row'>")
    add(f"<div class='kpi {pass_cls}'><div class='label'>总体合格率</div>"
        f"<div class='value'>{_pct(m.pass_rate)}</div>"
        f"<div class='hint'>{m.total_tests - m.fail_tests}/{m.total_tests} 项次合格</div></div>")
    add(f"<div class='kpi'><div class='label'>一次交验合格率</div>"
        f"<div class='value'>{_pct(m.batch_pass_rate)}</div>"
        f"<div class='hint'>全部项目一次通过的批次占比</div></div>")
    add(f"<div class='kpi'><div class='label'>产量达成率</div>"
        f"<div class='value'>{_pct(m.achievement, 1) if m.planned_qty > 0 else '无产量数据'}</div>"
        f"<div class='hint'>{f'{m.actual_qty:g} / {m.planned_qty:g} 吨 · {m.batch_count} 批次' if m.planned_qty > 0 else '未采集到生产记录'}</div></div>")
    add(f"<div class='kpi {'bad' if m.abnormal_batches else 'ok'}'><div class='label'>异常批次</div>"
        f"<div class='value'>{len(m.abnormal_batches)}</div>"
        f"<div class='hint'>不合格 {sum(1 for b in m.abnormal_batches if b.level == 'critical')} · "
        f"预警 {sum(1 for b in m.abnormal_batches if b.level == 'warning')} · "
        f"提示 {sum(1 for b in m.abnormal_batches if b.level == 'info')}</div></div>")
    add(f"<div class='kpi {'bad' if crit else 'ok'}'><div class='label'>当日预警</div>"
        f"<div class='value'>{len(m.alerts)}</div>"
        f"<div class='hint'>严重 {crit} · 警告 {warn} · 提示 {info}</div></div>")
    add("</div>")

    # ---------- 一、合格率分析 ----------
    add("<h2>一、合格率分析</h2>")
    add("<h3>分产品合格率</h3>")
    add("<table><tr><th>产品</th><th class='num'>批次</th><th class='num'>检测项次</th>"
        "<th class='num'>不合格项次</th><th class='num'>项目合格率</th><th class='num'>一次交验合格率</th>"
        "<th class='num'>计划产量(t)</th><th class='num'>实际产量(t)</th></tr>")
    for p in m.products:
        cls = " class='fail'" if p.fail_tests else ""
        add(f"<tr{cls}><td>{p.product}</td><td class='num'>{p.batches}</td>"
            f"<td class='num'>{p.total_tests}</td><td class='num'>{p.fail_tests}</td>"
            f"<td class='num'>{_pct(p.pass_rate)}</td><td class='num'>{_pct(p.batch_pass_rate)}</td>"
            f"<td class='num'>{p.planned_qty:g}</td><td class='num'>{p.actual_qty:g}</td></tr>")
    add(f"<tr style='font-weight:700'><td>合计</td><td class='num'>{m.batch_count}</td>"
        f"<td class='num'>{m.total_tests}</td><td class='num'>{m.fail_tests}</td>"
        f"<td class='num'>{_pct(m.pass_rate)}</td><td class='num'>{_pct(m.batch_pass_rate)}</td>"
        f"<td class='num'>{m.planned_qty:g}</td><td class='num'>{m.actual_qty:g}</td></tr></table>")

    add("<h3>分检测项目合格率</h3>")
    add("<table><tr><th>检测项目</th><th>单位</th><th class='num'>检测项次</th>"
        "<th class='num'>不合格项次</th><th class='num'>合格率</th><th>判定</th></tr>")
    for it in m.items:
        cls = " class='fail'" if it.fail else ""
        add(f"<tr{cls}><td>{it.item}</td><td>{it.unit}</td><td class='num'>{it.total}</td>"
            f"<td class='num'>{it.fail}</td><td class='num'>{_pct(it.pass_rate)}</td>"
            f"<td>{_tag('合格' if it.fail == 0 else '不合格')}</td></tr>")
    add("</table>")

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

    # ---------- 页脚 ----------
    add(f"<footer class='report-foot'><span>{meta.get('title', '食用油中控品控日报')} · "
        f"{m.report_date}</span><span>RunID {meta.get('run_id', '')} · "
        f"FP __FP__</span></footer>")

    add("</div></body></html>")
    return "".join(parts)
