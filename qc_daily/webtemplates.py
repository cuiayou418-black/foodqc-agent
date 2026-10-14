"""品控日报智能体 · Web 界面模板

与日报报告同一套视觉语言（#B81C22 红色主题、卡片式布局）。
全部模板为自包含 HTML（内联 CSS + 少量原生 JS，无外部 CDN 依赖，
局域网离线环境也能正常显示）。
"""
from __future__ import annotations

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
       background: #f0f2f5; color: #1f2329; font-size: 14px; line-height: 1.6; }
a { color: #1664ff; text-decoration: none; } a:hover { text-decoration: underline; }
.topbar { background: #b81c22; color: #fff; padding: 0 24px; display: flex;
          align-items: center; height: 52px; box-shadow: 0 2px 8px rgba(0,0,0,.15); }
.topbar .brand { font-size: 17px; font-weight: 700; letter-spacing: 1px; margin-right: 36px; }
.topbar nav { display: flex; gap: 4px; height: 100%; }
.topbar nav a { color: rgba(255,255,255,.85); padding: 0 14px; display: flex;
                align-items: center; font-size: 13.5px; border-bottom: 3px solid transparent; }
.topbar nav a:hover { color: #fff; text-decoration: none; background: rgba(0,0,0,.12); }
.topbar nav a.active { color: #fff; border-bottom-color: #fff; font-weight: 600; }
.topbar .spacer { flex: 1; }
.topbar .meta { font-size: 12px; color: rgba(255,255,255,.75); }
.wrap { max-width: 1180px; margin: 20px auto; padding: 0 20px 40px; }
.page-title { font-size: 18px; font-weight: 700; margin: 4px 0 16px; }
.kpi-row { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 20px; }
.kpi { flex: 1; min-width: 160px; background: #fff; border: 1px solid #e5e6eb; border-radius: 10px;
       padding: 14px 18px; }
.kpi .label { font-size: 12.5px; color: #646a73; }
.kpi .value { font-size: 26px; font-weight: 700; margin-top: 2px; }
.kpi .hint { font-size: 11.5px; color: #8f959e; margin-top: 2px; }
.kpi.ok .value { color: #0e7a3d; } .kpi.bad .value { color: #b81c22; } .kpi.warn .value { color: #c2771a; }
.panel { background: #fff; border: 1px solid #e5e6eb; border-radius: 10px; padding: 18px 22px;
         margin-bottom: 18px; }
.panel h2 { font-size: 15px; margin-bottom: 12px; padding-left: 9px; border-left: 4px solid #b81c22; }
.panel h2 .more { float: right; font-size: 12px; font-weight: 400; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { background: #f2f3f5; color: #383a42; font-weight: 600; }
th, td { border: 1px solid #e5e6eb; padding: 7px 10px; text-align: left; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.fail td { background: #fef0ef; } tr.warn td { background: #fdf6ec; }
code, .mono { font-family: Consolas, monospace; font-size: 12px; }
.btn { display: inline-block; background: #b81c22; color: #fff; border: none; border-radius: 6px;
       padding: 8px 18px; font-size: 13.5px; cursor: pointer; }
.btn:hover { background: #a01620; }
.btn.ghost { background: #fff; color: #b81c22; border: 1px solid #b81c22; }
.btn.ghost:hover { background: #fef0ef; }
.btn.small { padding: 3px 10px; font-size: 12px; border-radius: 4px; }
.btn:disabled { background: #c9cdd4; cursor: not-allowed; }
.tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11.5px; }
.tag.ok { background: #e3f5ea; color: #0e7a3d; } .tag.fail { background: #fde8e7; color: #b81c22; }
.tag.warn { background: #fdf1dc; color: #c2771a; } .tag.info { background: #e8f1fd; color: #1664ff; }
.tag.critical { background: #fde8e7; color: #b81c22; font-weight: 700; }
.tag.grey { background: #f2f3f5; color: #646a73; }
.empty { text-align: center; color: #8f959e; padding: 48px 0; }
.empty .btn { margin-top: 14px; }
.actions a, .actions button { margin-right: 6px; }
.form-row { display: flex; gap: 12px; align-items: end; flex-wrap: wrap; }
.form-row label { font-size: 13px; color: #383a42; display: block; margin-bottom: 4px; }
input[type=date], input[type=text], input[type=password] { border: 1px solid #dee0e3; border-radius: 6px;
       padding: 8px 10px; font-size: 13.5px; font-family: inherit; }
input[type=password] { width: 260px; }
.job-box { margin-top: 16px; border: 1px dashed #dee0e3; border-radius: 8px; padding: 14px 16px;
           display: none; font-size: 13px; }
.job-box.show { display: block; }
.job-box .st-running { color: #1664ff; font-weight: 600; }
.job-box .st-ok { color: #0e7a3d; font-weight: 600; }
.job-box .st-fail { color: #b81c22; font-weight: 600; }
.job-log { white-space: pre-wrap; font-family: Consolas, monospace; font-size: 12px;
           background: #f7f8fa; border-radius: 6px; padding: 10px 12px; margin-top: 8px;
           max-height: 320px; overflow: auto; }
.viewer { display: flex; flex-direction: column; height: calc(100vh - 130px); }
.viewer .bar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }
.viewer iframe { flex: 1; border: 1px solid #e5e6eb; border-radius: 8px; background: #fff; }
.footer { text-align: center; color: #8f959e; font-size: 12px; margin-top: 26px; }
.login-box { max-width: 360px; margin: 90px auto; background: #fff; border: 1px solid #e5e6eb;
             border-radius: 10px; padding: 28px 30px; }
.login-box h1 { font-size: 18px; color: #b81c22; margin-bottom: 16px; }
.login-box .btn { width: 100%; margin-top: 14px; }
.login-box .err { color: #b81c22; font-size: 12.5px; margin-top: 8px; }
.drop-zone { border: 2px dashed #c9cdd4; border-radius: 10px; padding: 42px 20px; text-align: center;
             color: #646a73; background: #fafbfc; cursor: pointer; transition: all .15s; }
.drop-zone.over { border-color: #b81c22; background: #fef0ef; color: #b81c22; }
.drop-zone .big { font-size: 34px; }
.drop-zone .hint { font-size: 12px; color: #8f959e; margin-top: 6px; }
.progress-wrap { margin-top: 16px; display: none; }
.progress-wrap.show { display: block; }
.progress-track { background: #f2f3f5; border-radius: 4px; height: 10px; overflow: hidden; }
.progress-bar { height: 100%; width: 0; background: #b81c22; transition: width .2s; }
.progress-label { font-size: 12px; color: #646a73; margin-top: 4px; }
.import-result { margin-top: 16px; display: none; }
.import-result.show { display: block; }
.result-ok { border: 1px solid #bbe0c6; background: #f0faf4; border-radius: 8px; padding: 14px 16px; }
.result-fail { border: 1px solid #f0c8c6; background: #fef0ef; border-radius: 8px; padding: 14px 16px; }
.result-title { font-weight: 700; margin-bottom: 8px; }
.result-ok .result-title { color: #0e7a3d; } .result-fail .result-title { color: #b81c22; }
.result-body { font-size: 12.5px; }
.result-body table { margin: 8px 0; }
.ov-table td, .ov-table th { text-align: left; }
.file-meta { font-size: 12px; color: #646a73; margin-top: 8px; }
.file-meta b { color: #1f2329; }
"""

_JS = """
function postJSON(url, data, cb) {
  fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify(data || {})})
    .then(r => r.json()).then(cb).catch(e => cb({error: String(e)}));
}
function pollJob(id, boxId) {
  const box = document.getElementById(boxId);
  box.className = 'job-box show';
  const timer = setInterval(() => {
    fetch('/api/job/' + id).then(r => r.json()).then(j => {
      if (j.status === 'running') {
        box.innerHTML = '<span class="st-running">⏳ 正在生成中……</span>';
      } else if (j.status === 'done') {
        clearInterval(timer);
        let html = '<span class="st-ok">✅ 生成成功</span><div class="job-log">' +
                   (j.summary || '') + '</div>';
        if (j.outputs && j.outputs.html) {
          html += '<div style="margin-top:8px"><a class="btn small" href="/reports/' +
                  j.date + '">查看日报</a> ' +
                  '<a class="btn small ghost" href="/reports/' + j.date +
                  '/download/pdf">下载 PDF</a> ' +
                  '<a class="btn small ghost" href="/reports/' + j.date +
                  '/download/excel">下载 Excel</a></div>';
        }
        box.innerHTML = html;
      } else {
        clearInterval(timer);
        box.innerHTML = '<span class="st-fail">❌ 生成失败</span><div class="job-log">' +
                        (j.error || '未知错误') + '</div>';
      }
    });
  }, 1500);
}
function startRun() {
  const date = document.getElementById('run-date').value;
  const source = document.getElementById('run-source').value;
  postJSON('/api/run', {date: date, source: source}, r => {
    if (r.job_id) { pollJob(r.job_id, 'job-box'); }
    else { alert('启动失败: ' + (r.error || '未知错误')); }
  });
}
"""


def page(title: str, active: str, body: str, meta: str = "", extra_js: str = "") -> str:
    """整页框架：顶栏导航 + 内容区 + 页脚。"""
    nav_items = [("/", "仪表盘", "dashboard"), ("/reports", "日报列表", "reports"),
                 ("/import", "数据导入", "import"), ("/alerts", "预警记录", "alerts"),
                 ("/audit", "审计日志", "audit"),
                 ("/generate", "生成日报", "generate")]
    nav = "".join(
        f"<a href='{href}' class='{'active' if key == active else ''}'>{label}</a>"
        for href, label, key in nav_items)
    return f"""<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{title} · 品控日报智能体</title><style>{_CSS}</style></head><body>
<div class='topbar'>
  <div class='brand'>🛢️ 品控日报智能体</div>
  <nav>{nav}</nav>
  <div class='spacer'></div>
  <div class='meta'>{meta}</div>
</div>
<div class='wrap'>{body}</div>
<div class='footer'>QC-Daily-Agent · 数据由程序按国标限值计算生成 · 放行决策以人工复核为准</div>
<script>{_JS}</script>
<script>{extra_js}</script>
</body></html>"""


def dashboard(kpi: dict, trend: list, alerts: list, scheduler_on: bool) -> str:
    """仪表盘：KPI 卡片 + 趋势图 + 最新预警。"""
    if not kpi:
        return page("仪表盘", "dashboard", """
<div class='empty'><p>还没有生成过日报</p>
<a class='btn' href='/generate'>生成第一期日报</a></div>""")

    # 对缺失字段容错（旧版历史记录可能没有 achievement 等新字段）
    kpi = {**{"batch_count": 0, "planned_qty": 0.0, "actual_qty": 0.0,
              "achievement": 0.0, "abnormal_batch_count": 0, "alerts_count": 0}, **kpi}
    pass_rate = kpi.get("pass_rate", 0.0)
    pass_cls = "ok" if pass_rate >= 0.98 else ("bad" if pass_rate < 0.95 else "warn")
    crit = sum(1 for a in alerts if a.get("level") == "critical")
    cards = f"""
<div class='kpi-row'>
  <div class='kpi'><div class='label'>最新日报</div><div class='value' style='font-size:20px'>{kpi['date']}</div>
    <div class='hint'>{kpi['batch_count']} 个生产批次</div></div>
  <div class='kpi {pass_cls}'><div class='label'>总体合格率</div><div class='value'>{pass_rate:.2%}</div>
    <div class='hint'>{kpi.get('total_tests', 0) - kpi.get('fail_tests', 0)}/{kpi.get('total_tests', 0)} 项次合格</div></div>
  <div class='kpi'><div class='label'>一次交验合格率</div><div class='value'>{kpi.get('batch_pass_rate', 0.0):.2%}</div>
    <div class='hint'>全部项目一次通过的批次占比</div></div>
  <div class='kpi {'bad' if kpi['abnormal_batch_count'] else 'ok'}'><div class='label'>异常批次</div>
    <div class='value'>{kpi['abnormal_batch_count']}</div><div class='hint'>不合格/预警/提示</div></div>
  <div class='kpi {'bad' if crit else 'ok'}'><div class='label'>当日预警</div><div class='value'>{len(alerts)}</div>
    <div class='hint'>严重 {crit} 条</div></div>
  <div class='kpi'><div class='label'>产量达成率</div>
    <div class='value'>{f"{kpi['achievement']:.1%}" if kpi['planned_qty'] > 0 else '无产量数据'}</div>
    <div class='hint'>{f"{kpi['actual_qty']:g} / {kpi['planned_qty']:g} 吨" if kpi['planned_qty'] > 0 else '未采集到生产记录'}</div></div>
</div>"""

    # ---- 趋势图（近 N 日合格率，内联 SVG） ----
    chart = "<div class='empty'>历史数据不足，连续生成数日后显示趋势</div>"
    if len(trend) >= 2:
        w, h, pad = 100, 46, 4
        bw = max(6, (w - 8) / len(trend) - 2)
        bars = []
        for i, t in enumerate(trend):
            bh = (h - 12) * t["pass_rate"]
            color = "#0e7a3d" if t["pass_rate"] >= 0.98 else ("#d83931" if t["pass_rate"] < 0.95 else "#e8a23d")
            x = pad + i * ((w - 8) / len(trend))
            bars.append(f"<rect x='{x:.1f}' y='{h - 6 - bh:.1f}' width='{bw:.1f}' height='{bh:.1f}' rx='1' fill='{color}'>"
                        f"<title>{t['date']} 合格率 {t['pass_rate']:.2%}</title></rect>")
            if i % max(1, len(trend) // 7) == 0:
                bars.append(f"<text x='{x + bw / 2:.1f}' y='{h + 2}' font-size='3.4' fill='#8f959e' "
                            f"text-anchor='middle'>{t['date'][5:]}</text>")
        chart = (f"<svg viewBox='0 0 {w} {h + 5}' style='width:100%;height:150px'>"
                 f"<line x1='{pad}' y1='{h - 6}' x2='{w - pad}' y2='{h - 6}' stroke='#e5e6eb'/>"
                 + "".join(bars) + "</svg>"
                 f"<div style='font-size:12px;color:#8f959e'>近 {len(trend)} 日总体合格率"
                 f"（绿≥98% / 橙≥95% / 红&lt;95%）</div>")

    alert_rows = "".join(
        f"<tr class='{'fail' if a['level'] == 'critical' else ('warn' if a['level'] == 'warning' else '')}'>"
        f"<td>{a.get('created_at', '')[:16]}</td>"
        f"<td><span class='tag {a['level']}'>{a['level'].upper()}</span></td>"
        f"<td>{a['title']}</td><td>{a['detail']}</td></tr>"
        for a in alerts[:8])
    sched = "<span class='tag ok'>调度运行中</span>" if scheduler_on else "<span class='tag grey'>调度未启动</span>"

    body = f"""
<div class='page-title'>生产质量仪表盘 {sched}</div>
{cards}
<div class='panel'><h2>合格率趋势 <a class='more' href='/reports'>查看全部日报 →</a></h2>{chart}</div>
<div class='panel'><h2>最新预警 <a class='more' href='/alerts'>查看全部预警 →</a></h2>
{'<table><tr><th>时间</th><th>级别</th><th>规则</th><th>详情</th></tr>' + alert_rows + '</table>' if alert_rows else "<div class='empty'>暂无预警</div>"}
</div>
<div class='panel'><h2>快捷操作</h2>
<a class='btn' href='/generate'>生成日报</a>
<a class='btn ghost' href='/reports/{kpi["date"]}'>查看最新日报</a>
<a class='btn ghost' href='/reports/{kpi["date"]}/download/pdf'>下载最新 PDF</a>
</div>"""
    return page("仪表盘", "dashboard", body, meta=f"数据截至 {kpi['date']}")


def reports_list(rows: list) -> str:
    """日报列表页。rows: [{date, status, pass_rate, fail_tests, total_tests, abnormal, alerts, files}]"""
    if not rows:
        return page("日报列表", "reports", """
<div class='empty'><p>还没有生成过日报</p>
<a class='btn' href='/generate'>生成第一期日报</a></div>""")
    trs = []
    for r in rows:
        cls = "fail" if (r["pass_rate"] is not None and r["pass_rate"] < 0.95) else ""
        status_tag = {"success": "<span class='tag ok'>成功</span>",
                      "partial": "<span class='tag warn'>部分完成</span>",
                      "failed": "<span class='tag fail'>失败</span>"}.get(r["status"], r["status"])
        pass_cell = f"{r['pass_rate']:.2%}" if r["pass_rate"] is not None else "-"
        files = r["files"]
        acts = []
        if "html" in files:
            acts.append(f"<a class='btn small' href='/reports/{r['date']}'>查看</a>")
        if "pdf" in files:
            acts.append(f"<a class='btn small ghost' href='/reports/{r['date']}/download/pdf'>PDF</a>")
        if "excel" in files:
            acts.append(f"<a class='btn small ghost' href='/reports/{r['date']}/download/excel'>Excel</a>")
        if "html" in files:
            acts.append(f"<button class='btn small ghost' onclick=\"verifyDate('{r['date']}')\">校验</button>")
        else:
            acts.append("<span style='color:#8f959e;font-size:12px'>未生成报告</span>")
        trs.append(
            f"<tr class='{cls}'><td><b>{r['date']}</b></td><td>{status_tag}</td>"
            f"<td class='num'>{pass_cell}</td>"
            f"<td class='num'>{r['fail_tests']}/{r['total_tests']}</td>"
            f"<td class='num'>{r['abnormal']}</td><td class='num'>{r['alerts']}</td>"
            f"<td class='actions'>{''.join(acts)}</td>"
            f"<td id='verify-{r['date']}' class='mono' style='font-size:11px'></td></tr>")
    body = f"""
<div class='page-title'>日报列表</div>
<div class='panel'><table>
<tr><th>报告日期</th><th>状态</th><th class='num'>总体合格率</th><th class='num'>不合格/总项次</th>
<th class='num'>异常批次</th><th class='num'>预警</th><th>操作</th><th>完整性校验</th></tr>
{''.join(trs)}
</table></div>
<script>
function verifyDate(d) {{
  fetch('/api/verify/' + d).then(r => r.json()).then(j => {{
    document.getElementById('verify-' + d).innerHTML = j.ok ?
      '<span class="tag ok">未被篡改</span>' : '<span class="tag fail">校验失败</span>';
  }});
}}
</script>"""
    return page("日报列表", "reports", body)


def report_viewer(date: str) -> str:
    """日报查看页：iframe 嵌入 HTML 报告 + 下载按钮。"""
    body = f"""
<div class='page-title'>品控日报 · {date}</div>
<div class='viewer'>
  <div class='bar'>
    <a class='btn small ghost' href='/reports'>← 返回列表</a>
    <a class='btn small' href='/reports/{date}/download/pdf'>下载 PDF</a>
    <a class='btn small' href='/reports/{date}/download/excel'>下载 Excel</a>
    <a class='btn small ghost' href='/reports/{date}/file' target='_blank'>新窗口打开原报告</a>
  </div>
  <iframe src='/reports/{date}/file' title='品控日报 {date}'></iframe>
</div>"""
    return page(f"日报 {date}", "reports", body)


def generate_page(default_date: str, scheduler_on: bool, db_ready: bool) -> str:
    """一键生成页（只使用真实导入数据，不提供模拟数据选项）。"""
    sched_note = ("<p style='color:#0e7a3d;font-size:13px'>✅ 后台调度器正在运行，每天自动生成昨日日报，"
                  "本页用于手动补生成。</p>" if scheduler_on else
                  "<p style='color:#c2771a;font-size:13px'>⚠️ 后台调度器未启动（用 "
                  "<code>python -m qc_daily web --scheduler</code> 启动可自动每日生成）。</p>")
    if db_ready:
        source_options = "<option value='sql' selected>SQL 数据库（已导入的数据）</option>"
        run_btn = "<button class='btn' onclick='startRun()'>开始生成</button>"
    else:
        source_options = ("<option value='sql' selected disabled>SQL 数据库"
                          "（尚未导入数据）</option>")
        run_btn = ("<button class='btn' disabled title='请先导入数据'>开始生成</button>"
                   "<a class='btn ghost' href='/import' style='margin-left:8px'>去导入数据 →</a>")
    body = f"""
<div class='page-title'>生成日报</div>
<div class='panel'>
  {sched_note}
  {'<p style="color:#b81c22;font-size:13px">⛔ 尚未导入任何数据，请先到「数据导入」页上传文件。</p>' if not db_ready else ''}
  <div class='form-row'>
    <div><label>报告日期（留空则生成昨日日报）</label>
      <input type='date' id='run-date' value='{default_date}'></div>
    <div><label>数据源</label>
      <select id='run-source' style='border:1px solid #dee0e3;border-radius:6px;padding:8px 10px;
font-size:13.5px;font-family:inherit'>{source_options}</select></div>
    <div>{run_btn}</div>
  </div>
  <div id='job-box' class='job-box'></div>
  <p style='margin-top:14px;font-size:12.5px;color:#8f959e'>
    生成过程约 10~30 秒：采集三系统数据 → 校验 → 计算指标 → 渲染报告 → 导出 PDF/Excel → 推送预警。
    生成期间可离开本页，结果可在「日报列表」找到。</p>
</div>"""
    return page("生成日报", "generate", body)


def alerts_page(rows: list) -> str:
    """预警记录页。rows: [{date, level, title, detail, refs, created_at}]"""
    if not rows:
        return page("预警记录", "alerts", "<div class='page-title'>预警记录</div>"
                                           "<div class='empty'>暂无预警记录</div>")
    trs = "".join(
        f"<tr class='{'fail' if r['level'] == 'critical' else ('warn' if r['level'] == 'warning' else '')}'>"
        f"<td>{r['date']}</td>"
        f"<td><span class='tag {r['level']}'>{ {'critical':'严重','warning':'警告','info':'提示'}.get(r['level'], r['level']) }</span></td>"
        f"<td>{r['title']}</td><td>{r['detail']}</td>"
        f"<td class='mono' style='font-size:11px'>{', '.join(r['refs'][:8])}</td></tr>"
        for r in rows)
    body = f"""
<div class='page-title'>预警记录（共 {len(rows)} 条）</div>
<div class='panel'><table>
<tr><th>报告日期</th><th>级别</th><th>规则</th><th>详情</th><th>关联记录</th></tr>
{trs}
</table></div>"""
    return page("预警记录", "alerts", body)


def audit_page(entries: list) -> str:
    """审计日志页。"""
    if not entries:
        return page("审计日志", "audit", "<div class='page-title'>审计日志</div>"
                                         "<div class='empty'>暂无运行记录</div>")
    trs = []
    for e in entries:
        rc = e.get("record_counts", {})
        status_tag = {"success": "<span class='tag ok'>成功</span>",
                      "partial": "<span class='tag warn'>部分完成</span>",
                      "failed": "<span class='tag fail'>失败</span>"}.get(e.get("status"), e.get("status"))
        outputs = e.get("outputs", {})
        links = " ".join(f"<a href='/reports/{e['report_date']}/download/{fmt}'>{fmt}</a>"
                         for fmt in outputs if fmt != "html")
        errs = "; ".join(e.get("errors", [])[:2])
        trs.append(
            f"<tr><td class='mono'>{e.get('run_id', '')}</td><td><b>{e.get('report_date', '')}</b></td>"
            f"<td>{status_tag}</td><td class='num'>{rc.get('production', 0)}</td>"
            f"<td class='num'>{rc.get('quality', 0)}</td><td class='num'>{len(e.get('alerts', []))}</td>"
            f"<td class='mono' style='font-size:11px'>{e.get('data_checksum', '')[:12]}</td>"
            f"<td>{links or '-'}</td><td style='color:#b81c22;font-size:12px'>{errs}</td></tr>")
    body = f"""
<div class='page-title'>审计日志（最近 {len(entries)} 次运行，只追加不可篡改）</div>
<div class='panel'><table>
<tr><th>运行编号</th><th>报告日期</th><th>状态</th><th class='num'>生产记录</th><th class='num'>质检记录</th>
<th class='num'>预警</th><th>数据指纹</th><th>导出</th><th>错误</th></tr>
{''.join(trs)}
</table></div>"""
    return page("审计日志", "audit", body)


def login_page(error: str = "") -> str:
    """登录页（配置了 token 时才启用）。"""
    err = f"<div class='err'>{error}</div>" if error else ""
    return f"""<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>
<title>登录 · 品控日报智能体</title><style>{_CSS}</style></head><body>
<div class='login-box'><h1>🛢️ 品控日报智能体</h1>
<p style='font-size:13px;color:#646a73'>请输入访问口令</p>
<form method='post' action='/login'>
  <div style='margin-top:12px'><input type='password' name='token' placeholder='访问口令' autofocus></div>
  <button class='btn' type='submit'>登 录</button>{err}
</form></div></body></html>"""


_IMPORT_JS = """
const ALLOWED_EXT = ['.xlsx', '.xlsm', '.csv', '.json'];
const MAX_SIZE = 20 * 1024 * 1024;
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');

['dragenter', 'dragover'].forEach(ev => dropZone.addEventListener(ev, e => {
  e.preventDefault(); dropZone.classList.add('over');
}));
['dragleave', 'drop'].forEach(ev => dropZone.addEventListener(ev, e => {
  e.preventDefault(); dropZone.classList.remove('over');
}));
dropZone.addEventListener('drop', e => {
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
});
dropZone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
  if (fileInput.files.length) handleFile(fileInput.files[0]);
});

function showError(msg) {
  const box = document.getElementById('import-result');
  box.className = 'import-result show';
  box.innerHTML = '<div class="result-fail"><div class="result-title">⛔ ' + esc(msg) +
                  '</div></div>';
  document.getElementById('progress-wrap').className = 'progress-wrap';
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function fmtSize(b) {
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
  return (b / 1048576).toFixed(2) + ' MB';
}

function handleFile(file) {
  const ext = ('.' + file.name.split('.').pop()).toLowerCase();
  if (!ALLOWED_EXT.includes(ext)) {
    return showError('不支持的文件格式 ' + ext + '，仅支持 .xlsx / .csv / .json');
  }
  if (file.size > MAX_SIZE) {
    return showError('文件大小 ' + fmtSize(file.size) + ' 超过 20MB 限制');
  }
  if (file.size === 0) return showError('文件为空，请检查后重试');

  document.getElementById('import-result').className = 'import-result';
  const pw = document.getElementById('progress-wrap');
  pw.className = 'progress-wrap show';
  const bar = document.getElementById('progress-bar');
  const label = document.getElementById('progress-label');
  bar.style.width = '0%';
  label.textContent = '上传中 0%';

  const fd = new FormData();
  fd.append('file', file);
  const xhr = new XMLHttpRequest();
  xhr.upload.onprogress = e => {
    if (e.lengthComputable) {
      const pct = Math.round(e.loaded / e.total * 100);
      bar.style.width = pct + '%';
      label.textContent = '上传中 ' + pct + '%（' + fmtSize(e.loaded) + ' / ' + fmtSize(e.total) + '）';
    }
  };
  xhr.onload = () => {
    bar.style.width = '100%';
    label.textContent = '上传完成，正在解析导入……';
    if (xhr.status === 200) {
      try { pollImportJob(JSON.parse(xhr.responseText).job_id); }
      catch (e) { showError('响应解析失败: ' + e); }
    } else {
      let msg = '上传失败（HTTP ' + xhr.status + '）';
      try { msg = JSON.parse(xhr.responseText).error || msg; } catch (e) {}
      showError(msg);
    }
  };
  xhr.onerror = () => showError('网络错误，上传失败（请检查服务是否启动）');
  xhr.open('POST', '/api/import/upload');
  xhr.send(fd);
}

function pollImportJob(id) {
  const timer = setInterval(() => {
    fetch('/api/import/job/' + id).then(r => r.json()).then(j => {
      if (j.status === 'running') return;
      clearInterval(timer);
      if (j.status === 'done') renderImportResult(j);
      else showError(j.error || '导入失败');
      loadOverview();
    }).catch(() => { clearInterval(timer); showError('任务状态查询失败'); });
  }, 1200);
}

function renderImportResult(j) {
  const s = j.summary || {};
  const box = document.getElementById('import-result');
  let html = '<div class="result-ok"><div class="result-title">✅ 导入成功：' +
             esc(j.filename || '') + '</div><div class="result-body">';
  (s.files || []).forEach(f => {
    html += '<div style="margin-top:6px">📄 <b>' + esc(f.file) + '</b> [' + esc(f.sheet) +
            '] → <b>' + esc(f.type) + '</b> 表，导入 <b>' + f.rows + '</b> 行' +
            (f.dates && f.dates.length ? '，日期 ' + esc(f.dates.join(', ')) : '') + '</div>';
    if (f.mapped && Object.keys(f.mapped).length) {
      html += '<div style="color:#8f959e;font-size:11.5px;margin:2px 0 6px">表头映射：' +
              esc(Object.entries(f.mapped).map(([k, v]) => k + '→' + v).join('，')) + '</div>';
    }
  });
  if (s.notes && s.notes.length) {
    html += '<div style="margin-top:6px;color:#c2771a">' +
            s.notes.map(n => '⚠️ ' + esc(n)).join('<br>') + '</div>';
  }
  if (s.errors && s.errors.length) {
    html += '<div style="margin-top:6px;color:#b81c22">' +
            s.errors.map(n => '⛔ ' + esc(n)).join('<br>') + '</div>';
  }
  html += '<div style="margin-top:8px">合计：生产 ' + (s.rows ? s.rows.production : 0) +
          ' 行 ｜ 质检 ' + (s.rows ? s.rows.quality : 0) +
          ' 行 ｜ 仓储 ' + (s.rows ? s.rows.warehouse : 0) + ' 行</div>';
  html += '<div style="margin-top:10px"><a class="btn small" href="/generate">去生成日报 →</a>' +
          ' <span style="color:#8f959e;font-size:12px">（在生成页选择「SQL 数据库」数据源）</span></div>';
  html += '</div></div>';
  box.className = 'import-result show';
  box.innerHTML = html;
}

function loadOverview() {
  fetch('/api/import/overview').then(r => r.json()).then(o => {
    const el = document.getElementById('overview-body');
    if (!o.exists) {
      el.innerHTML = '<div class="empty" style="padding:20px 0">数据库中还没有数据，先上传导入一份吧</div>';
      return;
    }
    let html = '<table class="ov-table"><tr><th>数据表</th><th class="num">记录数</th>' +
               '<th class="num">覆盖天数</th><th>最早日期</th><th>最晚日期</th></tr>';
    const names = {production: '生产记录', quality: '质检记录', warehouse: '仓储记录'};
    o.tables.forEach(t => {
      html += '<tr><td>' + (names[t.table] || t.table) + '</td><td class="num">' + t.total +
              '</td><td class="num">' + t.days + '</td><td>' + (t.min || '-') +
              '</td><td>' + (t.max || '-') + '</td></tr>';
    });
    html += '</table>';
    el.innerHTML = html;
  }).catch(() => {});
}
loadOverview();
"""


def import_page() -> str:
    """数据导入页：拖拽/选择上传 + 进度 + 结果 + 已导入数据概要。"""
    body = """
<div class='page-title'>数据导入</div>
<div class='panel'>
  <h2>上传数据文件</h2>
  <div id='drop-zone' class='drop-zone'>
    <div class='big'>📁</div>
    <div style='margin-top:8px'><b>拖拽文件到此处</b>，或点击选择文件</div>
    <div class='hint'>支持 .xlsx / .csv / .json ｜ 单个文件不超过 20MB ｜
      中文表头自动识别，宽表质检报表自动翻转</div>
    <input type='file' id='file-input' accept='.xlsx,.xlsm,.csv,.json' style='display:none'>
  </div>
  <div id='progress-wrap' class='progress-wrap'>
    <div class='progress-track'><div id='progress-bar' class='progress-bar'></div></div>
    <div id='progress-label' class='progress-label'></div>
  </div>
  <div id='import-result' class='import-result'></div>
</div>
<div class='panel'>
  <h2>已导入数据概要</h2>
  <div id='overview-body'><div class='empty' style='padding:20px 0'>加载中……</div></div>
</div>
<div class='panel'>
  <h2>说明</h2>
  <ul style='font-size:12.5px;color:#646a73;padding-left:18px;line-height:2'>
    <li>Excel 可有多个工作表（如"生产报表/质检报告/仓储流水"），系统按表头和 sheet 名自动识别类型；</li>
    <li>质检报表为宽表（酸价、过氧化值每项一列）时自动翻转，无需手动整理；</li>
    <li>表中缺少的单位/限值/标准，按 <code>qc_daily_config.json</code> 的产品限值自动补齐；</li>
    <li>同一日期重复导入会自动刷新该日期数据；</li>
    <li>导入完成后，到「生成日报」页选择 <b>SQL 数据库</b> 数据源即可用真实数据出报。</li>
  </ul>
</div>"""
    return page("数据导入", "import", body, extra_js=_IMPORT_JS)
