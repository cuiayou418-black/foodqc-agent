# -*- coding: utf-8 -*-
# task_web.py：Web 任务台 Blueprint（Phase 4）。Web 只做可视化，主入口是 CLI。
# 输入：POST /api/tasks/run 的 TaskRequest；输出：/tasks 页面 + 状态/产物/轨迹 API。
# 写操作显式校验 web.token / QC_WEB_TOKEN（不依赖全局 guard 的 token 空值放行）；
# 产物查看走 artifacts.resolve_artifact() 白名单，只回传相对路径，防目录穿越。
from __future__ import annotations

import threading
import uuid
from datetime import date

from flask import Blueprint, Response, jsonify, request, session

from . import webtemplates as tpl
from .artifacts import index_runs, list_artifacts, resolve_artifact, resolve_run_dir
from .config import QCDailyConfig, env
from .tasks import TaskRequest, TaskRunner, get_run, list_tasks
from .tasks.registry import TASKS

task_bp = Blueprint("tasks", __name__)

#: run 根目录白名单：与 --out 允许值一致，防止把产物写到仓库任意位置
_ALLOWED_ROOTS = ("runs", "qc_daily_data/task_runs")
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _write_guard():
    """写操作自己再校验一次：配了 token 就必须登录，未配则按现有演示模式（绑 127.0.0.1）。

    不直接依赖 webapp._guard —— 那里 `if not token: return None` 会放行，
    而任务写操作会导入文件、创建数据库，必须显式检查。
    """
    cfg = QCDailyConfig.load()
    token = cfg.web.get("token") or env(cfg.web.get("token_env", "QC_WEB_TOKEN"))
    if token and not session.get("auth"):
        return jsonify({"ok": False, "error": "未登录，不能触发任务；请先在 /login 登录"}), 401
    return None


def _find_root(run_id: str):
    for root in _ALLOWED_ROOTS:
        try:
            d = resolve_run_dir(run_id, root)
            if d.is_dir():
                return root, d
        except ValueError:
            continue
    return None, None


def _status(run_id: str) -> dict | None:
    with _LOCK:
        job = dict(_JOBS.get(run_id) or {})
    if job:
        return job
    for root in _ALLOWED_ROOTS:
        res = get_run(run_id, root)
        if res:
            return {"run_id": run_id, "status": res.status, "task_id": res.task_id,
                    "outputs": res.outputs, "trace": res.trace, "notes": res.notes,
                    "errors": res.errors, "sections": res.sections,
                    "metrics": res.metrics, "verify_ok": res.verify_ok,
                    "input_digest": res.input_digest,
                    "conclusion_digest": res.conclusion_digest,
                    "data_checksum": res.data_checksum,
                    "report_fingerprint": res.report_fingerprint,
                    "root": root}
    return None


# ------------------------------------------------------------------ 页面

@task_bp.get("/tasks")
def tasks_view():
    primary = list_tasks()
    more = [t for t in list_tasks(include_auxiliary=True) if t["auxiliary"]]
    history = index_runs("runs", limit=12) + index_runs("qc_daily_data/task_runs", limit=12)
    history = sorted(history, key=lambda x: x.get("started_at", ""), reverse=True)[:12]

    cards = "".join(
        f"<button class='tk-item' data-id='{t['task_id']}' data-aux='0'>"
        f"<b>{t['name']}</b><small>{t['description']}</small></button>"
        for t in primary)
    more_html = "".join(
        f"<button class='tk-item aux' data-id='{t['task_id']}' data-aux='1'>"
        f"<b>{t['name']}</b><small>{t['description']}</small></button>"
        for t in more)
    hist_html = "".join(
        f"<div class='tk-hist' data-run='{h['run_id']}'><span>{h['run_id']}</span>"
        f"<em>{h.get('status','')}</em><button class='tk-del' title='从本机列表移除'>×</button></div>"
        for h in history) or "<div class='tk-muted'>暂无历史运行</div>"

    body = f"""
<style>
.tk-wrap{{display:grid;grid-template-columns:250px 1fr;gap:16px;align-items:start}}
.tk-side{{background:#fff;border:1px solid #e4e7ea;border-radius:10px;padding:12px}}
.tk-side h3{{font-size:12px;color:#8f959e;margin:4px 0 8px}}
.tk-item{{display:block;width:100%;text-align:left;border:1px solid transparent;background:#f6f8f7;
color:#1f2328;border-radius:8px;padding:9px 10px;margin-bottom:7px;cursor:pointer;font-family:inherit}}
.tk-item:hover{{border-color:#b81c22}}.tk-item.on{{border-color:#b81c22;background:#fef0ef}}
.tk-item b{{display:block;font-size:13px}}.tk-item small{{display:block;font-size:11px;color:#8f959e;margin-top:3px}}
.tk-item.aux{{background:#fff;border:1px dashed #cbd5e1}}
.tk-hist{{display:flex;gap:6px;align-items:center;font-size:11px;padding:5px 4px;border-bottom:1px dashed #e4e7ea}}
.tk-hist span{{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer}}
.tk-hist em{{font-style:normal;color:#0f6e56}}
.tk-del{{border:0;background:transparent;color:#8f959e;cursor:pointer}}
.tk-muted,.tk-empty{{font-size:12px;color:#8f959e;padding:8px 0}}
.tk-main{{background:#fff;border:1px solid #e4e7ea;border-radius:10px;padding:16px}}
.tk-row{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}}
.tk-row input,.tk-row select{{border:1px solid #cbd5e1;border-radius:6px;padding:6px 8px;font-size:12px;font-family:inherit}}
.tk-row input[type=text]{{flex:1;min-width:220px}}
.tk-btn{{border:0;background:#b81c22;color:#fff;border-radius:6px;padding:7px 15px;font-size:12px;cursor:pointer;font-family:inherit}}
.tk-btn.gray{{background:#f2f3f5;color:#4a5058}}
.tk-badge{{font-size:11px;padding:2px 9px;border-radius:12px;background:#e9f6f0;color:#0f6e56}}
.tk-badge.bad{{background:#fef0ef;color:#a32d2d}}.tk-badge.warn{{background:#fff6e5;color:#8a5a06}}
.tk-tabs{{display:flex;gap:4px;border-bottom:1px solid #e4e7ea;margin:14px 0 10px}}
.tk-tabs button{{border:0;background:transparent;color:#8f959e;font-size:12px;font-family:inherit;
padding:6px 13px;cursor:pointer;border-bottom:2px solid transparent}}
.tk-tabs button.on{{color:#b81c22;border-bottom-color:#b81c22;font-weight:600}}
.tk-pane[hidden]{{display:none}}
.tk-art{{display:block;font-size:12px;color:#0f6e56;padding:4px 0;text-decoration:none}}
.tk-ev{{border:1px solid #e4e7ea;border-radius:6px;background:#f6f8f7;padding:6px 9px;
font-family:Consolas,monospace;font-size:11px;margin-bottom:5px;white-space:pre-wrap}}
.tk-msg{{font-size:12px;color:#a32d2d;margin:8px 0}}
.tk-note{{background:#fff6e5;border:1px dashed #c2771a;padding:9px 11px;font-size:12px;line-height:1.7}}
</style>
<div class='tk-wrap'>
  <aside class='tk-side'>
    <button class='tk-btn gray' id='tk-new' style='width:100%;margin-bottom:12px'>＋ 新建任务</button>
    <h3>业务任务（首页 {len(primary)} 项）</h3>
    <div id='tk-primary'>{cards}</div>
    <h3 style='margin-top:14px'>更多 · 辅助只读</h3>
    <div>{more_html or '<div class="tk-muted">无</div>'}</div>
    <h3 style='margin-top:14px'>最近运行 <small>（删除只清本机列表，不删文件）</small></h3>
    <div id='tk-history'>{hist_html}</div>
  </aside>
  <main class='tk-main'>
    <div style='display:flex;align-items:center;gap:10px'>
      <h2 id='tk-title' style='font-size:16px;margin:0;flex:1'>{primary[0]['name']}</h2>
      <span class='tk-badge' id='tk-badge'>待运行</span>
    </div>
    <div id='tk-desc' class='tk-muted' style='margin:6px 0 14px'>{primary[0]['description']}</div>
    <div class='tk-row'>
      <input type='text' id='tk-input' value='samples/稻谷验收_假数据.csv' placeholder='表格文件或目录，多个用空格分隔'>
      <input type='text' id='tk-date' value='2026-11-05' placeholder='YYYY-MM-DD' style='max-width:130px'>
      <select id='tk-formats'><option>html excel</option><option>html</option><option>html pdf excel</option></select>
      <input type='text' id='tk-out' value='runs' style='max-width:150px' title='run 根目录白名单'>
      <button class='tk-btn' id='tk-run'>运行任务</button>
    </div>
    <div class='tk-note'>只接 DEMO 假数据；push 恒为 false，邮件 / 企微保持 dry_run。
    当前执行 GB 1350—2009，限值未人工核对，所有结论仅供复核、不得作为放行依据；最终由人签字。</div>
    <div id='tk-msg' class='tk-msg'></div>
    <div class='tk-tabs'>
      <button class='on' data-p='concl'>结论视图</button>
      <button data-p='trace'>执行轨迹（默认收起）</button>
    </div>
    <div class='tk-pane' id='tk-p-concl'><div class='tk-empty'>还没运行任务。</div></div>
    <div class='tk-pane' id='tk-p-trace' hidden><div class='tk-empty'>执行轨迹会在运行后出现。</div></div>
  </main>
</div>
"""
    js = r"""
var TK={id:'',run:'',timer:null};
function tkEl(x){return document.getElementById(x);}
function tkEsc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
 return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function tkBadge(s){var b=tkEl('tk-badge');b.textContent=s||'待运行';
 b.className='tk-badge'+(s==='failed'?' bad':(s==='partial'||s==='pending_review'?' warn':''));}
document.querySelectorAll('.tk-item').forEach(function(b){b.onclick=function(){
 document.querySelectorAll('.tk-item').forEach(function(x){x.classList.remove('on');});
 b.classList.add('on');TK.id=b.dataset.id;
 tkEl('tk-title').textContent=b.querySelector('b').textContent;
 tkEl('tk-desc').textContent=b.querySelector('small').textContent;};});
document.querySelector('.tk-item').classList.add('on');
TK.id=document.querySelector('.tk-item').dataset.id;
tkEl('tk-new').onclick=function(){TK.run='';tkBadge('待运行');
 tkEl('tk-p-concl').innerHTML='<div class="tk-empty">新建任务：填好输入表后点「运行任务」。</div>';
 tkEl('tk-p-trace').innerHTML='<div class="tk-empty">执行轨迹会在运行后出现。</div>';
 tkEl('tk-msg').textContent='';tkEl('tk-input').focus();};
document.querySelectorAll('.tk-tabs button').forEach(function(b){b.onclick=function(){
 document.querySelectorAll('.tk-tabs button').forEach(function(x){x.classList.remove('on');});
 b.classList.add('on');
 tkEl('tk-p-concl').hidden=b.dataset.p!=='concl';
 tkEl('tk-p-trace').hidden=b.dataset.p!=='trace';};});
tkEl('tk-history').onclick=function(e){
 var row=e.target.closest('.tk-hist');if(!row)return;
 if(e.target.classList.contains('tk-del')){row.remove();return;}
 TK.run=row.dataset.run;tkPoll();};
function tkPoll(){
 if(!TK.run)return;
 fetch('/api/tasks/'+encodeURIComponent(TK.run)).then(function(r){return r.json();}).then(function(d){
  if(!d.ok){tkEl('tk-msg').textContent=d.error||'查询失败';return;}
  tkBadge(d.status);
  var arts=(d.artifacts||[]).map(function(a){
   return '<a class="tk-art" href="/api/tasks/'+encodeURIComponent(TK.run)+
    '/artifact/'+encodeURIComponent(a.name)+'" target="_blank" rel="noopener">↗ '+tkEsc(a.name)+'</a>';}).join('');
  var sec=d.sections||{};
  var tri=['已确认','待人工复核','未覆盖'].map(function(k){
   return '<div style="font-size:12px;margin:7px 0"><b>'+k+'</b><ul>'+
    (sec[k]||[]).map(function(x){return '<li>'+tkEsc(x)+'</li>';}).join('')+'</ul></div>';}).join('');
  tkEl('tk-p-concl').innerHTML='<div style="font-size:12px;color:#4a5058">run_id '+tkEsc(TK.run)+
   ' ｜ 工件列表：</div>'+(arts||'<div class="tk-empty">暂无工件</div>')+
   '<div style="margin-top:10px">'+tri+'</div>'+
   (d.errors||[]).map(function(x){return '<div class="tk-msg">'+tkEsc(x)+'</div>';}).join('')+
   '<div class="tk-note" style="margin-top:10px">结论仅供复核，不得作为放行依据；最终由人签字。</div>';
  tkEl('tk-p-trace').innerHTML=(d.trace||[]).map(function(t){
   return '<div class="tk-ev">#'+tkEsc(t.step)+' ['+tkEsc(t.state)+'] '+tkEsc(t.event)+' '+tkEsc(t.detail||'')+'</div>';
   }).join('')||'<div class="tk-empty">无轨迹</div>';
  if(['done','partial','failed','pending_review'].indexOf(d.status)>=0){clearInterval(TK.timer);TK.timer=null;}
 }).catch(function(e){tkEl('tk-msg').textContent='查询失败：'+e.message;});}
tkEl('tk-run').onclick=function(){
 var btn=this;btn.disabled=true;tkEl('tk-msg').textContent='任务已提交…';
 fetch('/api/tasks/run',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({task_id:TK.id,inputs:tkEl('tk-input').value.trim().split(/\s+/),
   report_date:tkEl('tk-date').value.trim(),formats:tkEl('tk-formats').value.trim().split(/\s+/),
   output_root:tkEl('tk-out').value.trim()})})
 .then(function(r){return r.json();}).then(function(d){
  btn.disabled=false;
  if(!d.ok){tkEl('tk-msg').textContent=d.error||'提交失败';return;}
  TK.run=d.run_id;tkBadge('running');tkEl('tk-msg').textContent='run_id '+d.run_id;
  var h=tkEl('tk-history');var row=document.createElement('div');row.className='tk-hist';
  row.dataset.run=d.run_id;row.innerHTML='<span>'+tkEsc(d.run_id)+'</span><em>running</em>'+
   '<button class="tk-del" title="从本机列表移除">×</button>';h.insertBefore(row,h.firstChild);
  clearInterval(TK.timer);TK.timer=setInterval(tkPoll,1200);tkPoll();
 }).catch(function(e){btn.disabled=false;tkEl('tk-msg').textContent='提交失败：'+e.message;});};
"""
    return tpl.page(title="任务 / 产物", active="tasks", body=body,
                    meta="业务任务台 · 只接 DEMO 假数据", extra_js=js)


# ------------------------------------------------------------------ API

@task_bp.post("/api/tasks/run")
def api_run():
    denied = _write_guard()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    root = str(data.get("output_root") or "runs").rstrip("/\\")
    if root not in _ALLOWED_ROOTS:
        return jsonify({"ok": False,
                        "error": f"output_root 只允许 {list(_ALLOWED_ROOTS)}（防任意路径写入）"}), 400
    try:
        req = TaskRequest(
            task_id=str(data.get("task_id") or ""),
            inputs=[str(x) for x in (data.get("inputs") or [])],
            report_date=str(data.get("report_date") or ""),
            formats=([str(x) for x in (data.get("formats") or [])]
                     or list(QCDailyConfig.load().export.get("formats", ["html", "pdf", "excel"]))),
            output_root=root, push=False)
        req.validate(TASKS.keys())
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}), 400

    run_id = f"T{date.today():%Y%m%d}-{uuid.uuid4().hex[:10]}"
    # TaskRunner 会按 run_id 建目录；这里预置一个 queued 状态供前端轮询，避免改 runner 主干
    with _LOCK:
        _JOBS[run_id] = {"run_id": run_id, "status": "running", "task_id": req.task_id,
                         "outputs": [], "trace": [], "notes": [], "errors": [],
                         "sections": {}, "metrics": {}, "artifacts": [], "root": root}

    def work():
        try:
            res = TaskRunner(config=QCDailyConfig.load()).run(req, run_id=run_id)
            with _LOCK:
                _JOBS[res.run_id] = {"run_id": res.run_id, "status": res.status,
                                     "task_id": res.task_id, "outputs": res.outputs,
                                     "trace": res.trace, "notes": res.notes,
                                     "errors": res.errors, "sections": res.sections,
                                     "metrics": res.metrics, "verify_ok": res.verify_ok,
                                     "input_digest": res.input_digest,
                                     "conclusion_digest": res.conclusion_digest,
                                     "data_checksum": res.data_checksum,
                                     "report_fingerprint": res.report_fingerprint,
                                     "root": root,
                                     "artifacts": list_artifacts(res.run_id, root)}
        except Exception as e:
            with _LOCK:
                _JOBS[run_id].update({"status": "failed",
                                      "errors": [f"运行中断（{type(e).__name__}）：{str(e)[:200]}"]})

    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True, "run_id": run_id, "status": "running"})


@task_bp.get("/api/tasks/<run_id>")
def api_status(run_id: str):
    d = _status(run_id)
    if not d:
        return jsonify({"ok": False, "error": "找不到这个 run_id"}), 404
    root = d.get("root") or (_find_root(run_id)[0] or "runs")
    try:
        d["artifacts"] = list_artifacts(run_id, root)
    except ValueError:
        d["artifacts"] = []
    d["ok"] = True
    return jsonify(d)


@task_bp.get("/api/tasks/<run_id>/artifact/<path:name>")
def api_artifact(run_id: str, name: str):
    root, _ = _find_root(run_id)
    if not root:
        return jsonify({"ok": False, "error": "run 目录不在白名单内"}), 404
    try:
        p = resolve_artifact(run_id, name, root)
    except (ValueError, FileNotFoundError) as e:
        return jsonify({"ok": False, "error": f"产物不可读：{e}"}), 400
    mime = {".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
            ".jsonl": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8",
            ".csv": "text/csv; charset=utf-8",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".pdf": "application/pdf"}.get(p.suffix.lower(), "application/octet-stream")
    return Response(p.read_bytes(), mimetype=mime)
