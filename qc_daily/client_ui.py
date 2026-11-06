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
  --bg:#fff; --panel:#fff; --side:#eef7f3; --ink:#1f2328; --ink2:#4a5058; --ink3:#8b949e;
  --line:#e4ebe7; --brand:#0f6e56; --brand-soft:#dcefe8;
  --ok:#0f6e56; --ok-bg:#e9f6f0; --warn:#8a5a06; --warn-bg:#fff6e5;
  --bad:#a32d2d; --bad-bg:#fdf0f0; --code:#f6f8f7;
}
body[data-theme="dark"]{
  --bg:#14171c; --panel:#1c2027; --side:#16211d; --ink:#e6edf3; --ink2:#b6bfc9; --ink3:#8b949e;
  --line:#30363d; --brand:#5dcaa5; --brand-soft:#1b332b;
  --ok:#5dcaa5; --ok-bg:#16302a; --warn:#efc078; --warn-bg:#332a17;
  --bad:#f09595; --bad-bg:#331c1c; --code:#161a20;
}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--ink);font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.6}
.app{display:flex;height:100vh;overflow:hidden}
.side{width:252px;flex:0 0 252px;background:var(--side);border-right:1px solid var(--line);display:flex;flex-direction:column;padding:14px 12px 10px}
.logo{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:600;margin:2px 0 14px;padding-left:4px}
.logo .dot{width:12px;height:12px;border-radius:4px;background:var(--brand)}
.ver{font-size:9px;letter-spacing:1px;color:var(--ink3);border:1px solid var(--line);border-radius:3px;padding:0 4px}
.newtask{width:100%;text-align:left;border:0;background:transparent;color:var(--ink);font-size:13px;padding:7px 8px;border-radius:6px;cursor:pointer;margin-bottom:2px}
.newtask:hover{background:var(--brand-soft)}
.side-link{width:100%;text-align:left;border:0;background:transparent;color:var(--ink2);font-size:13px;padding:7px 8px;border-radius:6px;cursor:pointer}
.side-link:hover{background:var(--brand-soft);color:var(--brand)}
.tabs{display:flex;gap:14px;border-bottom:1px solid var(--line);margin:12px 4px 8px;padding-bottom:8px;font-size:12px;color:var(--ink3)}
.tabs .on{color:var(--brand);font-weight:600;border-bottom:2px solid var(--brand);padding-bottom:6px;margin-bottom:-8px}
.side-scroll{flex:1;overflow-y:auto}
.group{font-size:11px;color:var(--ink3);margin:12px 8px 6px;display:flex;justify-content:space-between}
.proj{font-size:12.5px;color:var(--ink2);padding:5px 8px;display:flex;gap:6px}
.conv{display:flex;align-items:center;gap:6px;padding:6px 8px;border-radius:6px;font-size:12.5px;color:var(--ink2);cursor:pointer;margin-bottom:2px}
.conv:hover{background:var(--brand-soft)}
.conv.on{background:var(--brand-soft);color:var(--brand)}
.conv .cv-t{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.conv .cv-time{font-size:10px;color:var(--ink3)}
.cv-x{border:0;background:transparent;color:var(--ink3);cursor:pointer;font-size:13px;line-height:1;padding:0 2px}
.cv-x:hover{color:var(--bad)}
.kb{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:10px 11px;font-size:12.5px;margin:10px 4px}
.kb .n{font-size:18px;font-weight:600;color:var(--brand)}
.kb .lbl{font-size:11px;color:var(--ink3)}
.upload{margin-top:8px;display:flex;flex-direction:column;gap:6px}
.upload input[type=file]{font-size:11px;max-width:100%}
.btn{border:1px solid var(--line);background:var(--panel);color:var(--ink2);border-radius:6px;padding:5px 10px;font-size:11.5px;cursor:pointer}
.btn:hover{border-color:var(--brand);color:var(--brand)}
.btn.primary{background:var(--brand);color:#fff;border-color:var(--brand)}
.side-user{border-top:1px solid var(--line);padding-top:10px;display:flex;align-items:center;gap:8px;font-size:12px;color:var(--ink2)}
.avatar{width:28px;height:28px;border-radius:8px;background:var(--brand);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:600}
.side-foot{font-size:10px;color:var(--ink3);line-height:1.5;margin-top:8px}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.winbar{height:44px;flex:0 0 44px;display:flex;align-items:center;padding:0 22px;gap:14px;border-bottom:1px solid var(--line)}
.winbar .wb{font-size:12px;color:var(--ink3)}
.winbar .sp{flex:1}
.badge{font-size:11px;padding:2px 9px;border-radius:12px;background:var(--brand-soft);color:var(--brand)}
.badge.warn{background:var(--warn-bg);color:var(--warn)}
.thread{flex:1;overflow-y:auto;padding:10px 26px 6px;display:flex;flex-direction:column}
.wrap-in{max-width:860px;width:100%;margin:0 auto}
.welcome{margin:auto;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px 0 34px;text-align:center}
.wtitle{font-size:30px;font-weight:600;letter-spacing:2px;display:flex;align-items:center;gap:12px}
.wtitle .dot{width:26px;height:26px;border-radius:8px;background:var(--brand)}
.preview{font-size:10px;letter-spacing:0;background:var(--brand-soft);color:var(--brand);border-radius:10px;padding:2px 7px;font-weight:400}
.wsub{font-size:12.5px;color:var(--ink3);margin:8px 0 30px}
.wcards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;max-width:800px;width:100%}
.wcard{text-align:left;border:1px solid var(--line);background:var(--panel);border-radius:10px;padding:14px 13px;cursor:pointer;min-height:96px;color:var(--ink)}
.wcard:hover{border-color:var(--brand);background:var(--brand-soft)}
.wcard .ic{font-size:15px;color:var(--brand);display:block;margin-bottom:8px}
.wcard b{display:block;font-size:12.5px;margin-bottom:4px}
.wcard small{font-size:11px;color:var(--ink3);line-height:1.45}
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
.composer{flex:0 0 auto;padding:8px 26px 16px}
.cbox{max-width:860px;margin:0 auto;border:1px solid var(--line);border-radius:14px;background:var(--panel);padding:10px 14px 9px}
.cbar{display:flex;align-items:center;gap:14px;font-size:11.5px;color:var(--ink2);padding-bottom:6px}
.cbar .sp{flex:1}
.cbar .hist{color:var(--ink3)}
textarea{width:100%;resize:none;height:48px;border:0;outline:none;padding:6px 2px;font-size:13.5px;font-family:inherit;background:transparent;color:var(--ink)}
.ctools{display:flex;align-items:center;gap:8px;border-top:1px solid var(--line);padding-top:8px}
.ctools .sp{flex:1}
.cbtn{font-size:11px;color:var(--ink2);border:1px solid var(--line);border-radius:12px;padding:2px 9px;cursor:pointer}
.cbtn:hover{border-color:var(--brand);color:var(--brand)}
.model{font-size:11px;color:var(--ink3)}
.send{width:30px;height:30px;border:0;border-radius:50%;background:var(--brand);color:#fff;font-size:15px;cursor:pointer;line-height:1}
.send:disabled{opacity:.55;cursor:wait}
.hint{max-width:860px;margin:7px auto 0;font-size:10.5px;color:var(--ink3)}
.err{background:var(--bad-bg);color:var(--bad);border-radius:8px;padding:9px 12px;font-size:12.5px;margin-bottom:12px}
.empty{color:var(--ink3);text-align:center;padding:60px 20px;font-size:13px}
.empty b{color:var(--ink);font-size:16px;display:block;margin-bottom:8px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:0 auto 9px}
.chip{border:1px solid var(--line);background:var(--panel);color:var(--ink2);border-radius:14px;padding:4px 11px;font-size:11.5px;cursor:pointer}
.chip:hover{border-color:var(--brand);color:var(--brand)}
.mini{border:1px solid var(--line);background:var(--panel);color:var(--brand);border-radius:5px;padding:2px 7px;font-size:11px;cursor:pointer}
</style>
</head>
<body data-theme="light">
<div class="app">
  <aside class="side">
    <div class="logo"><span class="dot"></span>FoodQC-Agent <span class="ver">HARNESS</span></div>
    <button class="newtask" id="new-conv">＋ 新建任务</button>
    <button class="side-link" id="kb-toggle">≡ 知识库 / 记忆</button>
    <div class="tabs"><span class="on">任务</span><span>频道</span><span>定时</span></div>
    <div class="side-scroll">
      <div class="group">置顶 <span>▾</span></div>
      <div class="group">项目 <span>▾</span></div>
      <div class="proj">▸ default-workspace</div>
      <div class="group">最近 <span>▾</span></div>
      <div id="conv-list"></div>
      <div class="kb" id="kb-box" style="display:none">
        <div><span class="n" id="k-files">–</span> <span class="lbl">个文件</span>　<span class="n" id="k-entries">–</span> <span class="lbl">条目</span></div>
        <div class="lbl" style="margin-top:4px">待核对 <span id="k-pending">0</span>　公司材料 <span id="k-private">0</span></div>
        <div class="upload">
          <input type="file" id="k-file" accept=".md,.txt,.pdf,.csv,.json,.xlsx">
          <label style="font-size:11px;color:var(--ink3)"><input type="radio" name="src" value="public" checked> 公开标准　<input type="radio" name="src" value="private"> 公司内部</label>
          <button class="btn" id="k-up">上传并加入待核对区</button>
          <div id="k-msg" style="font-size:11px"></div>
        </div>
      </div>
    </div>
    <div class="side-user">
      <span class="avatar">Q</span>
      <div>
        <div>小崔 · 品管部</div>
        <div class="side-foot">只读 · 不放行 · 不签字<br>DEMO 假数据 ｜ GB 1350—2009 限值待核对</div>
      </div>
    </div>
  </aside>
  <main class="main">
    <div class="winbar">
      <span class="wb">应用　　编辑</span>
      <div class="sp"></div>
      <span class="badge" id="conv-title">新对话</span>
      <span class="badge warn">判定型</span>
      <button class="btn" id="theme">切换深色</button>
    </div>
    <div class="thread" id="thread">
      <div class="welcome" id="welcome">
        <div class="wtitle"><span class="dot"></span>探索未至之境<span class="preview">预览版</span></div>
        <div class="wsub">粮油原粮验收 · 判定型垂直 Agent ｜ 提一句，它自己决定查什么</div>
        <div class="wcards" id="wcards"></div>
      </div>
      <div class="wrap-in" id="msgs"></div>
    </div>
    <div class="composer">
      <div class="cbox">
        <div class="cbar">
          <span>默认工作区 ▾</span><span>判定模式 ▾</span>
          <div class="sp"></div><span class="hist">历史 ↑↓</span>
        </div>
        <textarea id="q" placeholder="描述你要判定的批次 / 想查的标准条款…（Enter 发送，Ctrl+Enter 换行）"></textarea>
        <div class="ctools">
          <span class="cbtn" id="kb-toggle2">＋ 知识库</span>
          <span class="cbtn">只读 · 不放行</span>
          <span class="cbtn">DEMO-only</span>
          <div class="sp"></div>
          <span class="model">DeepSeek · 关闭</span>
          <button class="send" id="send">↑</button>
        </div>
      </div>
      <div class="hint">只接受 DEMO- 假批次；真实批次号与实测值不会进入本系统。结论仅供复核，不得作为放行依据。</div>
    </div>
  </main>
</div>
<script>
var CHIPS=[
 "你能干什么",
 "DEMO-001 这批稻谷出糙率 76.5 能收吗",
 "DEMO-002 水分含量 1.45 有没有敲错",
 "DEMO-003 整精米率 80 比出糙率 72 还高，可疑吗",
 "出糙率 3 等限值是多少，依据在哪一条"
];
var STORE='foodqc.sessions.v1', ACTIVE='foodqc.active.v1';
var sessions=[], activeId=null;
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
 return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function el(id){return document.getElementById(id);}
function scrollDown(){var t=el('thread');t.scrollTop=t.scrollHeight;}
function uid(){return 's'+Date.now().toString(36)+Math.random().toString(36).slice(2,6);}
function loadStore(){
 try{
  var raw=localStorage.getItem(STORE);
  sessions=raw?JSON.parse(raw):[];
  activeId=localStorage.getItem(ACTIVE);
 }catch(e){sessions=[];activeId=null;}
 if(!Array.isArray(sessions))sessions=[];
 if(!sessions.length)sessions=[{id:uid(),title:'新对话',created:Date.now(),msgs:[]}];
 if(!sessions.some(function(s){return s.id===activeId;}))activeId=sessions[0].id;
}
function save(){
 try{
  var trim=sessions.slice(0,12).map(function(s){
   return {id:s.id,title:s.title,created:s.created,msgs:s.msgs.slice(-40)};
  });
  localStorage.setItem(STORE,JSON.stringify(trim));
  localStorage.setItem(ACTIVE,activeId||'');
 }catch(e){}
}
function cur(){return sessions.filter(function(s){return s.id===activeId;})[0]||sessions[0];}
function renderList(){
 var box=el('conv-list');box.innerHTML='';
 sessions.forEach(function(s){
  var row=document.createElement('div');
  row.className='conv'+(s.id===activeId?' on':'');
  var tm='今天';
  if(s.created){var mins=(Date.now()-Number(s.created))/60000;
   tm=mins<2?'刚刚':(mins<60?Math.floor(mins)+'分前':(mins<1440?Math.floor(mins/60)+'时前':'更早'));}
  row.innerHTML='<span class="cv-t">'+esc(s.title)+'</span><span class="cv-time">'+esc(tm)+'</span>'+
   '<button class="cv-x" title="删除该会话">×</button>';
  row.onclick=function(){activeId=s.id;save();renderAll();};
  row.querySelector('.cv-x').onclick=function(ev){ev.stopPropagation();delSession(s.id);};
  box.appendChild(row);
 });
}
function renderTitle(){el('conv-title').textContent=cur().title;}
function renderMsgs(){
 var box=el('msgs');box.innerHTML='';
 var w=el('welcome');
 var list=cur().msgs||[];
 if(!list.length){if(w)w.style.display='flex';return;}
 if(w)w.style.display='none';
 list.forEach(function(m){
  if(m.role==='user')box.appendChild(buildUser(m.text));
  else if(m.role==='err')box.appendChild(buildErr(m.text));
  else if(m.mode==='chat')box.appendChild(buildChat(m.data.answer||{}));
  else box.appendChild(buildRun(m.data));
 });
 scrollDown();
}
function renderAll(){renderList();renderTitle();renderMsgs();}
function newSession(){
 var s={id:uid(),title:'新对话',created:Date.now(),msgs:[]};
 sessions.unshift(s);activeId=s.id;save();renderAll();el('q').value='';
}
function delSession(id){
 sessions=sessions.filter(function(s){return s.id!==id;});
 if(!sessions.length)sessions=[{id:uid(),title:'新对话',created:Date.now(),msgs:[]}];
 if(activeId===id)activeId=sessions[0].id;
 save();renderAll();
}
function push(m){
 var s=cur();s.msgs.push(m);
 if(m.role==='user'&&s.title==='新对话'){
  s.title=m.text.slice(0,18)+(m.text.length>18?'…':'');
 }
 save();renderTitle();renderList();
}
function buildUser(txt){
 var d=document.createElement('div');d.className='msg user';
 d.innerHTML='<div class="bubble">'+esc(txt)+'</div>';return d;
}
function buildErr(txt){
 var d=document.createElement('div');d.className='err';d.textContent=txt;return d;
}
function tri(title,cls,arr){
 var lis=(arr||[]).map(function(x){return '<li>'+esc(x)+'</li>';}).join('')||'<li>（无）</li>';
 return '<div class="'+cls+'"><b>'+title+'</b><ul>'+lis+'</ul></div>';
}
function buildChat(a){
 var lines=(a.lines||[]).map(function(x){return '<li>'+esc(x)+'</li>';}).join('');
 var bd=(a.boundary||[]).map(function(x){return '<li>'+esc(x)+'</li>';}).join('');
 var ex=(a.examples||[]).map(function(x){return '<button class="chip" data-q="'+esc(x)+'">'+esc(x)+'</button>';}).join('');
 var h='<div class="msg"><div class="who"><span class="sq"></span>FoodQC-Agent</div><div class="card">'+
  '<div class="h3" style="font-size:14px;margin:0 0 9px">'+esc(a.title)+'</div>'+
  '<ul style="margin:0;padding-left:18px;font-size:12.5px;line-height:1.7">'+lines+'</ul>'+
  (bd?'<div class="disclaim" style="margin-top:11px"><ul style="margin:0;padding-left:16px">'+bd+'</ul></div>':'')+
  (ex?'<div class="h3">试试这样问</div><div class="chips" style="margin:0">'+ex+'</div>':'')+
 '</div></div>';
 var t=document.createElement('template');t.innerHTML=h;
 t.content.querySelectorAll('[data-q]').forEach(function(b){b.onclick=function(){send(b.dataset.q);};});
 return t.content;
}
function buildRun(d){
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
 var t=document.createElement('template');t.innerHTML=h;return t.content;
}
function addUser(txt){push({role:'user',text:txt});renderMsgs();}
function addErr(txt){push({role:'err',text:txt});renderMsgs();}
function addChat(a){push({role:'agent',mode:'chat',data:{answer:a}});renderMsgs();}
function addAgent(d){
 if(d.mode==='chat'){addChat(d.answer||{});return;}
 push({role:'agent',mode:'run',data:d});renderMsgs();
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
var CARDS=[
 {ic:'◈',t:'判定一批稻谷',d:'逐项判 7 项指标，带 GB 出处',q:CHIPS[1]},
 {ic:'◇',t:'录入错误自查',d:'5 类可疑数，只标疑点不改数',q:CHIPS[2]},
 {ic:'▤',t:'查标准依据',d:'从 corpus 检索带出处条款',q:CHIPS[4]},
 {ic:'?',t:'看我能做什么',d:'能力清单与边界',q:CHIPS[0]}
];
CARDS.forEach(function(c){
 var b=document.createElement('button');b.className='wcard';
 b.innerHTML='<span class="ic">'+c.ic+'</span><b>'+esc(c.t)+'</b><small>'+esc(c.d)+'</small>';
 b.onclick=function(){send(c.q);};el('wcards').appendChild(b);
});
function toggleKB(){var b=el('kb-box');b.style.display=(b.style.display==='none'?'block':'none');}
el('kb-toggle').onclick=toggleKB;
el('kb-toggle2').onclick=toggleKB;
el('send').onclick=function(){send(el('q').value);};
el('q').addEventListener('keydown',function(e){
 if(e.key==='Enter'&&!e.ctrlKey&&!e.metaKey){e.preventDefault();send(this.value);}
});
el('k-up').onclick=upload;
el('new-conv').onclick=newSession;
el('theme').onclick=function(){
 var b=document.body;b.dataset.theme=b.dataset.theme==='dark'?'light':'dark';
 el('theme').textContent=b.dataset.theme==='dark'?'切换浅色':'切换深色';
};
loadStore();renderAll();loadKB();
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

    # 入口理解层：先试可选 LLM 意图识别（只出白名单意图+检索词，不产数字/结论），
    # 没 key / 未开 / 超时 / 异常一律静默退回规则路由。判定请求才进 harness。
    from . import intent
    action, payload = intent.llm_route(question)
    used_llm = action != "fallback"
    if action == "fallback":
        routed = intent.route(question)
        payload = {"answer": routed["answer"]} if routed else None
    elif action == "judge":
        payload = None
    if payload is not None:
        return jsonify({
            "ok": True, "mode": "chat", "question": question,
            "answer": payload["answer"],
            "router": "llm" if used_llm else "rules",
            "batch_id": "", "stop_reason": "chat_route", "steps": 0,
            "tools_called": [], "llm_used": used_llm,
            "data_scope": "入口理解层（未进入判定链）",
            "sections": {}, "trace_events": [],
            "trace_path": "", "summary_path": "", "report_path": "",
        })

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
        "mode": "run",
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
