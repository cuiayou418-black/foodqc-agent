"""FoodQC-Agent · 独立对话客户端

形态对标 FateMaster / Codex：一进来就是消息流 + 输入框，不挂在日报后台里。
本模块自包含 HTML/CSS/JS，**不复用 webtemplates.page()**（那套是品控日报的壳）。

路由：
    GET  /app                     独立客户端首页
    POST /api/client/chat         接 harness.run_harness()，返回三段式 + 真实工具步骤
    GET  /api/client/knowledge    知识库概览（复用 knowledge.overview）
上传沿用已有的 /api/knowledge/upload，不重复实现。

红线：只接受 DEMO- 假批次；不回传本机绝对路径；不发邮件、不改源数据；
      LLM 默认关闭；所有结论仅供复核，不得作为放行依据。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import threading
from typing import Any

from flask import Blueprint, jsonify, request

from . import knowledge as kb

client_bp = Blueprint("client", __name__)
_HARNESS_LOCK = threading.Lock()

_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FoodQC-Agent · 粮油原粮验收判定助手</title>
<style>
:root{
  --bg:#f5f6f8; --panel:#fff; --side:#fbfcfd; --ink:#1f2328; --ink2:#4a5058; --ink3:#7b828b;
  --line:#e3e6ea; --brand:#185fa5; --brand-soft:#eaf3fc;
  --ok:#0f6e56; --ok-bg:#e9f6f0; --warn:#8a5a06; --warn-bg:#fff6e5;
  --bad:#a32d2d; --bad-bg:#fdf0f0; --code:#f4f7fa;
}
body[data-theme="dark"]{
  --bg:#14171c; --panel:#1c2027; --side:#181b21; --ink:#e6edf3; --ink2:#b6bfc9; --ink3:#8b949e;
  --line:#30363d; --brand:#6cb0ff; --brand-soft:#1b2c40;
  --ok:#5dcaa5; --ok-bg:#16302a; --warn:#efc078; --warn-bg:#332a17;
  --bad:#f09595; --bad-bg:#331c1c; --code:#161a20;
}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--ink);font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.6}
.app{display:flex;height:100vh;overflow:hidden}
.side{width:248px;flex:0 0 248px;background:var(--side);border-right:1px solid var(--line);display:flex;flex-direction:column;padding:16px 14px}
.logo{display:flex;align-items:center;gap:9px;font-size:15px;font-weight:600;margin-bottom:4px}
.logo .dot{width:10px;height:10px;border-radius:3px;background:var(--brand)}
.sub{font-size:11.5px;color:var(--ink3);margin-bottom:18px;padding-left:19px}
.badge{display:inline-block;font-size:11px;padding:2px 8px;border-radius:12px;background:var(--brand-soft);color:var(--brand);margin-right:5px;margin-bottom:5px}
.badge.warn{background:var(--warn-bg);color:var(--warn)}
.sec{font-size:11px;color:var(--ink3);letter-spacing:.5px;margin:18px 0 8px;text-transform:uppercase}
.conv{background:var(--brand-soft);border-radius:7px;padding:9px 11px;font-size:13px;color:var(--brand);margin-bottom:6px}
.kb{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:11px 12px;font-size:12.5px}
.kb .n{font-size:20px;font-weight:600;color:var(--brand)}
.kb .lbl{font-size:11px;color:var(--ink3)}
.upload{margin-top:10px;display:flex;flex-direction:column;gap:6px}
.upload input[type=file]{font-size:11px;max-width:100%}
.btn{border:1px solid var(--line);background:var(--panel);color:var(--ink2);border-radius:6px;padding:6px 10px;font-size:12px;cursor:pointer}
.btn:hover{border-color:var(--brand);color:var(--brand)}
.btn.primary{background:var(--brand);color:#fff;border-color:var(--brand)}
.side-foot{margin-top:auto;font-size:11px;color:var(--ink3);border-top:1px solid var(--line);padding-top:12px;line-height:1.7}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.top{height:52px;flex:0 0 52px;border-bottom:1px solid var(--line);background:var(--panel);display:flex;align-items:center;padding:0 20px;gap:12px}
.top .t{font-size:14px;font-weight:600}
.top .sp{flex:1}
.thread{flex:1;overflow-y:auto;padding:22px 26px 10px}
.wrap-in{max-width:820px;margin:0 auto}
.msg{margin-bottom:18px}
.msg.user{display:flex;justify-content:flex-end}
.msg.user .bubble{background:var(--brand);color:#fff;border-radius:14px 14px 4px 14px;padding:10px 15px;max-width:76%;white-space:pre-wrap}
.who{font-size:11px;color:var(--ink3);margin-bottom:5px;display:flex;align-items:center;gap:6px}
.who .sq{width:8px;height:8px;border-radius:2px;background:var(--brand)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.meta{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:11px}
.meta span{font-size:11px;background:var(--code);border:1px solid var(--line);border-radius:12px;padding:2px 9px;color:var(--ink2)}
.h3{font-size:12.5px;font-weight:600;margin:12px 0 7px;color:var(--ink2)}
details{border:1px solid var(--line);border-radius:8px;background:var(--code);margin-bottom:8px}
summary{cursor:pointer;padding:8px 11px;font-size:12.5px;color:var(--ink2);list-style:none}
summary::-webkit-details-marker{display:none}
summary:before{content:"▸ ";color:var(--brand)}
details[open] summary:before{content:"▾ "}
.ev{padding:7px 11px;border-top:1px solid var(--line);font-family:Consolas,"Courier New",monospace;font-size:11.5px}
.ev .nm{color:var(--brand);font-weight:600}
.ev .rs{color:var(--ink2);display:block;margin-top:2px;white-space:pre-wrap;word-break:break-all}
.tri{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:10px}
.tri>div{border-radius:8px;padding:9px 11px;font-size:12px;min-height:66px}
.tri .a{background:var(--ok-bg);color:var(--ok)}
.tri .b{background:var(--warn-bg);color:var(--warn)}
.tri .c{background:var(--code);color:var(--ink2)}
.tri b{display:block;font-size:11px;margin-bottom:5px;opacity:.85}
.tri ul{margin:0;padding-left:15px}
.tri li{margin:2px 0;line-height:1.45}
.disclaim{margin-top:11px;background:var(--bad-bg);color:var(--bad);border-left:3px solid var(--bad);border-radius:0 6px 6px 0;padding:8px 11px;font-size:12px}
.trace{margin-top:8px;font-size:11px;color:var(--ink3);font-family:Consolas,monospace}
.composer{flex:0 0 auto;border-top:1px solid var(--line);background:var(--panel);padding:12px 26px 16px}
.chips{display:flex;gap:6px;flex-wrap:wrap;max-width:820px;margin:0 auto 9px}
.chip{border:1px solid var(--line);background:var(--panel);color:var(--ink2);border-radius:14px;padding:4px 11px;font-size:11.5px;cursor:pointer}
.chip:hover{border-color:var(--brand);color:var(--brand)}
.row{display:flex;gap:9px;max-width:820px;margin:0 auto;align-items:flex-end}
textarea{flex:1;resize:none;height:56px;border:1px solid var(--line);border-radius:9px;padding:10px 12px;font-size:13.5px;font-family:inherit;background:var(--bg);color:var(--ink)}
textarea:focus{outline:none;border-color:var(--brand)}
.send{height:56px;padding:0 20px;border:0;border-radius:9px;background:var(--brand);color:#fff;font-size:13.5px;cursor:pointer}
.send:disabled{opacity:.55;cursor:wait}
.hint{max-width:820px;margin:7px auto 0;font-size:11px;color:var(--ink3)}
.err{background:var(--bad-bg);color:var(--bad);border-radius:8px;padding:9px 12px;font-size:12.5px;margin-bottom:12px}
.empty{color:var(--ink3);text-align:center;padding:60px 20px;font-size:13px}
.empty b{color:var(--ink);font-size:16px;display:block;margin-bottom:8px}
</style>
</head>
<body data-theme="light">
<div class="app">
  <aside class="side">
    <div class="logo"><span class="dot"></span>FoodQC-Agent</div>
    <div class="sub">粮油原粮验收 · 判定型垂直 Agent</div>
    <div>
      <span class="badge" id="b-llm">LLM 关闭</span>
      <span class="badge warn">2009 限值待核对</span>
    </div>
    <div class="sec">会话</div>
    <div class="conv">本次演示会话</div>
    <div class="sec">知识库 / 记忆</div>
    <div class="kb">
      <div><span class="n" id="k-files">–</span> <span class="lbl">个文件</span>　<span class="n" id="k-entries">–</span> <span class="lbl">条目</span></div>
      <div class="lbl" style="margin-top:4px">待核对 <span id="k-pending">0</span>　公司材料 <span id="k-private">0</span></div>
      <div class="upload">
        <input type="file" id="k-file" accept=".md,.txt,.pdf,.csv,.json,.xlsx">
        <label style="font-size:11px;color:var(--ink3)"><input type="radio" name="src" value="public" checked> 公开标准　<input type="radio" name="src" value="private"> 公司内部</label>
        <button class="btn" id="k-up">上传并加入待核对区</button>
        <div id="k-msg" style="font-size:11px"></div>
      </div>
    </div>
    <div class="side-foot">
      只用 DEMO- 假数据 · 不发邮件 · 不上线<br>
      判定权在代码，结论仅供复核，<strong style="color:var(--bad)">不得作为放行依据</strong>
    </div>
  </aside>
  <main class="main">
    <div class="top">
      <div class="t">Agent 工作台</div>
      <span class="badge">判定型 · 窄工具面</span>
      <div class="sp"></div>
      <button class="btn" id="theme">切换深色</button>
    </div>
    <div class="thread" id="thread">
      <div class="wrap-in" id="msgs">
        <div class="empty" id="empty">
          <b>提一句，它自己决定查什么</b>
          例如「DEMO-001 这批稻谷出糙率 76.5 能收吗」。<br>
          每一步工具调用会展开在下面，判定由代码完成，模型不下结论。
        </div>
      </div>
    </div>
    <div class="composer">
      <div class="chips" id="chips"></div>
      <div class="row">
        <textarea id="q" placeholder="输入一个问题…（Enter 发送，Ctrl+Enter 换行）"></textarea>
        <button class="send" id="send">发送</button>
      </div>
      <div class="hint">只接受 DEMO- 假批次；真实批次号与实测值不会进入本系统。</div>
    </div>
  </main>
</div>
<script>
var CHIPS=[
 "DEMO-001 这批稻谷出糙率 76.5 能收吗",
 "DEMO-002 水分含量 1.45 有没有敲错",
 "DEMO-003 整精米率 80 比出糙率 72 还高，可疑吗",
 "出糙率 3 等限值是多少，依据在哪一条"
];
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
 return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function el(id){return document.getElementById(id);}
function scrollDown(){var t=el('thread');t.scrollTop=t.scrollHeight;}
function addUser(txt){
 var e=el('empty');if(e)e.remove();
 var d=document.createElement('div');d.className='msg user';
 d.innerHTML='<div class="bubble">'+esc(txt)+'</div>';
 el('msgs').appendChild(d);scrollDown();
}
function addErr(txt){
 var e=el('empty');if(e)e.remove();
 var d=document.createElement('div');d.className='err';d.textContent=txt;
 el('msgs').appendChild(d);scrollDown();
}
function tri(title,cls,arr){
 var lis=(arr||[]).map(function(x){return '<li>'+esc(x)+'</li>';}).join('')||'<li>（无）</li>';
 return '<div class="'+cls+'"><b>'+title+'</b><ul>'+lis+'</ul></div>';
}
function addAgent(d){
 var ev=(d.trace_events||[]).map(function(x){
   return '<div class="ev"><span class="nm">'+esc(x.tool)+' ('+esc(JSON.stringify(x.args||{}))+')</span>'+
          '<span class="rs">'+esc(x.result_brief||'')+'</span></div>';
 }).join('');
 var s=d.sections||{};
 var h='<div class="msg"><div class="who"><span class="sq"></span>FoodQC-Agent</div><div class="card">'+
  '<div class="meta"><span>批次 '+esc(d.batch_id)+'</span><span>终止 '+esc(d.stop_reason)+'</span>'+
  '<span>步骤 '+esc(d.steps)+'</span><span>LLM '+(d.llm_used?'启用':'关闭')+'</span>'+
  '<span>'+esc(d.data_scope||'DEMO-only')+'</span></div>'+
  '<details open><summary>执行过程（'+esc((d.trace_events||[]).length)+' 条真实工具调用）</summary>'+ev+'</details>'+
  '<div class="h3">三段式输出</div><div class="tri">'+
   tri('已确认','a',s['已确认'])+tri('待人工复核','b',s['待人工复核'])+tri('未覆盖','c',s['未覆盖'])+
  '</div>'+
  '<div class="disclaim">结论仅供复核，不得作为放行依据；最终由人签字。模型不算数，数字由代码算。</div>'+
  '<div class="trace">留痕 '+esc(d.trace_path)+'　|　'+esc(d.report_path||'（本次未生成报告）')+'</div>'+
 '</div></div>';
 var e=el('empty');if(e)e.remove();
 var t=document.createElement('template');t.innerHTML=h;
 el('msgs').appendChild(t.content);scrollDown();
}
async function send(q){
 q=String(q||'').trim();if(!q)return;
 addUser(q);
 var b=el('send');b.disabled=true;b.textContent='运行中…';
 try{
  var r=await fetch('/api/client/chat',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({question:q})});
  var d=await r.json();
  if(!r.ok||!d.ok){throw new Error(d.error||('HTTP '+r.status));}
  addAgent(d);
 }catch(e){addErr('运行失败：'+e.message);}
 finally{b.disabled=false;b.textContent='发送';el('q').value='';}
}
async function loadKB(){
 try{
  var r=await fetch('/api/knowledge/overview');var d=await r.json();var s=d.stats||{};
  el('k-files').textContent=s.builtin_files;el('k-entries').textContent=s.builtin_entries;
  el('k-pending').textContent=s.pending;el('k-private').textContent=s.private;
 }catch(e){el('k-msg').textContent='知识库读取失败';}
}
async function upload(){
 var f=el('k-file').files[0];
 if(!f){el('k-msg').textContent='请先选择文件';return;}
 var src=document.querySelector('input[name=src]:checked').value;
 var fd=new FormData();fd.append('file',f);fd.append('source',src);
 el('k-msg').textContent='上传中…';
 try{
  var r=await fetch('/api/knowledge/upload',{method:'POST',body:fd});var d=await r.json();
  if(!r.ok||!d.ok)throw new Error(d.error||'HTTP '+r.status);
  el('k-msg').textContent='已加入待核对区：'+d.record.name+'（'+d.record.entries+' 条目）';
  el('k-file').value='';loadKB();
  if(d.warning)el('k-msg').textContent+='　注意：'+d.warning;
 }catch(e){el('k-msg').textContent='上传失败：'+e.message;}
}
CHIPS.forEach(function(c){
 var b=document.createElement('button');b.className='chip';b.textContent=c;
 b.onclick=function(){send(c);};el('chips').appendChild(b);
});
el('send').onclick=function(){send(el('q').value);};
el('q').addEventListener('keydown',function(e){
 if(e.key==='Enter'&&!e.ctrlKey&&!e.metaKey){e.preventDefault();send(this.value);}
});
el('k-up').onclick=upload;
el('theme').onclick=function(){
 var b=document.body;b.dataset.theme=b.dataset.theme==='dark'?'light':'dark';
 el('theme').textContent=b.dataset.theme==='dark'?'切换浅色':'切换深色';
};
loadKB();
</script>
</body>
</html>
"""


def _relative(path: str) -> str:
    """不把本机绝对路径回传前端。"""
    if not path:
        return ""
    try:
        return os.path.relpath(path, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).replace("\\", "/")
    except Exception:
        return os.path.basename(path)


def _read_trace(path: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path or not os.path.exists(path):
        return events
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                events.append({
                    "step": obj.get("step"),
                    "tool": obj.get("tool", ""),
                    "args": obj.get("args", {}),
                    "result_brief": str(obj.get("result_brief", ""))[:600],
                    "ts": obj.get("ts", ""),
                })
    except OSError:
        pass
    return events[:80]


@client_bp.get("/app")
def client_index():
    return _PAGE


@client_bp.post("/api/client/chat")
def client_chat():
    data = request.get_json(silent=True) or {}
    question = str(data.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "请输入一个问题"}), 400
    if len(question) > 300:
        return jsonify({"ok": False, "error": "问题请控制在 300 字以内"}), 400

    sink = io.StringIO()
    try:
        with _HARNESS_LOCK:
            from . import harness
            with contextlib.redirect_stdout(sink):   # 吞掉 CLI 式打印，不污染 Web 日志
                result = harness.run_harness(question)
    except ImportError:
        return jsonify({"ok": False, "error": "harness.py 尚未就绪"}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": f"运行失败（{type(e).__name__}）：{str(e)[:160]}"}), 500

    if result.get("reject") or result.get("ok") is False:
        return jsonify({"ok": False,
                        "error": "只接受 DEMO- 假批次；真实批次号与实测值不进本系统"}), 400

    trace_path = result.get("trace_path", "")
    return jsonify({
        "ok": True,
        "question": question,
        "batch_id": result.get("batch_id", ""),
        "stop_reason": result.get("stop_reason", ""),
        "steps": result.get("steps", 0),
        "tools_called": result.get("tools_called", []),
        "llm_used": bool(result.get("llm_used")),
        "data_scope": result.get("data_scope", "DEMO-only"),
        "sections": result.get("sections", {}),
        "trace_events": _read_trace(trace_path),
        "trace_path": _relative(trace_path),
        "summary_path": _relative(result.get("summary_path", "")),
        "report_path": _relative(result.get("report_path", "")),
    })


@client_bp.get("/api/client/knowledge")
def client_knowledge():
    return jsonify(kb.overview())
