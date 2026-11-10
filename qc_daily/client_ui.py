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
import re
import threading
from datetime import date
from pathlib import Path
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
.tabs .off{opacity:.4;cursor:not-allowed}
.side-scroll{flex:1;overflow-y:auto}
.group{font-size:11px;color:var(--ink3);margin:12px 8px 6px;display:flex;justify-content:space-between;cursor:pointer;user-select:none}
.group:hover{color:var(--brand)}
.group .ar{transition:transform .15s;display:inline-block}
.group.folded .ar{transform:rotate(-90deg)}
.grp-body.folded{display:none}
.proj{font-size:12.5px;color:var(--ink2);padding:5px 8px;display:flex;gap:6px}
.proj .lock{font-size:10px;color:var(--ink3)}
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
.disclaim{margin-top:10px;border-left:2px solid var(--line);padding:2px 0 2px 10px;font-size:11px;color:var(--ink3);line-height:1.65}
.disclaim ul{margin:0;padding-left:15px}
.rtabs{display:flex;gap:2px;border-bottom:1px solid var(--line);margin:2px 0 12px}
.rtabs button{border:0;background:transparent;color:var(--ink3);font-size:12px;font-family:inherit;padding:5px 12px 7px;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
.rtabs button:hover{color:var(--brand)}
.rtabs button.on{color:var(--brand);border-bottom-color:var(--brand);font-weight:600}
.rview[hidden]{display:none}
.evlist{border:1px solid var(--line);border-radius:8px;background:var(--code);margin-bottom:8px}
.evlist .ev:first-child{border-top:0}
.prose{font-size:13.5px;line-height:1.85;color:var(--ink);max-width:720px}
.prose p{margin:0 0 9px}
.prose p:last-child{margin-bottom:0}
.toggles{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.tg{border:1px solid var(--line);background:var(--panel);color:var(--ink2);font-family:inherit;font-size:11.5px;border-radius:14px;padding:4px 12px;cursor:pointer}
.tg:hover{border-color:var(--brand);color:var(--brand)}
.tg.on{background:var(--brand-soft);color:var(--brand);border-color:var(--brand)}
.pane{margin-top:12px}
.pane[hidden]{display:none}
.footline{margin-top:13px;padding-top:9px;border-top:1px solid var(--line);font-size:10.5px;color:var(--ink3);line-height:1.7}
.cpbox{margin:14px 0 2px;border:1px solid var(--warn);border-left:4px solid var(--warn);background:var(--warn-bg);border-radius:0 10px 10px 0;padding:12px 14px}
.cp-t{font-size:12.5px;font-weight:600;color:var(--warn);margin-bottom:8px}
.cp{border-top:1px dashed var(--line);padding:9px 0}
.cp:first-of-type{border-top:0;padding-top:2px}
.cp-h{font-size:12.5px;font-weight:600;color:var(--ink);margin-bottom:3px}
.cp-d{font-size:11.5px;color:var(--ink2);line-height:1.6;margin-bottom:7px}
.cp-go{display:inline-block;border:1px solid var(--warn);background:var(--panel);color:var(--warn);font-family:inherit;font-size:11.5px;border-radius:6px;padding:4px 11px;cursor:pointer;text-decoration:none}
.cp-go:hover{background:var(--warn);color:#fff}
.cp-off{font-size:11px;color:var(--ink3)}
.modal{position:fixed;inset:0;z-index:200;display:flex;align-items:center;justify-content:center}
.modal[hidden]{display:none}
.modal-bg{position:absolute;inset:0;background:rgba(0,0,0,.42)}
.modal-box{position:relative;width:min(720px,92vw);max-height:82vh;overflow-y:auto;background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:0 24px 60px rgba(0,0,0,.28);padding:18px 20px}
.modal-h{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.modal-h b{font-size:14px;flex:1;color:var(--ink)}
.modal-x{border:0;background:transparent;color:var(--ink3);font-size:20px;cursor:pointer;line-height:1}
.rec-src{font-size:11.5px;color:var(--ink2);background:var(--code);border:1px solid var(--line);border-radius:8px;padding:9px 11px;margin-bottom:12px;line-height:1.6}
.rec-note{font-size:11.5px;color:var(--warn);background:var(--panel);border-left:3px solid var(--warn);border-radius:0 6px 6px 0;padding:9px 11px;margin-top:12px;line-height:1.65}
.rec-tb{width:100%;border-collapse:collapse;font-size:12px}
.rec-tb th,.rec-tb td{border:1px solid var(--line);padding:6px 9px;text-align:left}
.rec-tb th{background:var(--code);color:var(--ink2);font-weight:600}
.rec-tb td.num{text-align:right;font-variant-numeric:tabular-nums}
.rec-tb tr.bad td{background:var(--bad-bg);color:var(--bad);font-weight:600}
.trace{margin-top:8px;font-size:11px;color:var(--ink3);font-family:Consolas,monospace}
.composer{flex:0 0 auto;padding:8px 26px 16px}
.cbox{position:relative;max-width:860px;margin:0 auto;border:1px solid var(--line);border-radius:14px;background:var(--panel);padding:10px 14px 9px}
.cmdpanel{position:absolute;bottom:calc(100% + 8px);left:0;width:min(520px,100%);max-height:300px;overflow-y:auto;background:var(--panel);border:1px solid var(--line);border-radius:11px;box-shadow:0 14px 34px rgba(0,0,0,.16);padding:6px;z-index:60;display:none}
.cmdpanel.show{display:block}
.cmdpanel .ci{display:flex;align-items:baseline;gap:12px;width:100%;text-align:left;border:0;background:transparent;color:var(--ink);font-family:inherit;padding:7px 10px;border-radius:7px;cursor:pointer}
.cmdpanel .ci:hover,.cmdpanel .ci.on{background:var(--brand-soft)}
.cmdpanel .ci .k{font-family:Consolas,"Courier New",monospace;font-size:12px;color:var(--brand);min-width:62px}
.cmdpanel .ci .d{font-size:11.5px;color:var(--ink3);flex:1}
.cmdpanel .ct{font-size:10.5px;color:var(--ink3);padding:5px 10px 7px;border-bottom:1px solid var(--line);margin-bottom:4px}
.cmdpanel .cn{font-size:12px;color:var(--ink3);padding:12px;text-align:center}
.cmdhint{font-size:10.5px;color:var(--ink3)}
.toast{position:fixed;left:50%;bottom:112px;transform:translateX(-50%);background:#2b3138;color:#fff;font-size:12px;padding:8px 15px;border-radius:20px;opacity:0;pointer-events:none;transition:opacity .22s;z-index:100;max-width:80vw;text-align:center}
.toast.show{opacity:.93}
.cbar{display:flex;align-items:center;gap:10px;font-size:11.5px;color:var(--ink2);padding-bottom:6px}
.cbar .sp{flex:1}
.dd{position:relative;display:inline-block}
.dd>button{border:0;background:transparent;color:var(--ink2);font-size:11.5px;font-family:inherit;cursor:pointer;padding:2px 5px;border-radius:5px}
.dd>button:hover{background:var(--brand-soft);color:var(--brand)}
.ddmenu{position:absolute;bottom:calc(100% + 6px);left:0;min-width:158px;background:var(--panel);border:1px solid var(--line);border-radius:9px;box-shadow:0 10px 26px rgba(0,0,0,.14);padding:5px;z-index:50;display:none}
.dd.open .ddmenu{display:block}
.ddmenu button{display:block;width:100%;text-align:left;border:0;background:transparent;color:var(--ink2);font-size:12px;font-family:inherit;padding:6px 9px;border-radius:6px;cursor:pointer}
.ddmenu button:hover{background:var(--brand-soft);color:var(--brand)}
.ddmenu button.on{color:var(--brand);font-weight:600}
.ddmenu .note{font-size:10.5px;color:var(--ink3);padding:3px 9px 6px;line-height:1.45}
.hist-btn{border:1px solid var(--line);background:transparent;color:var(--ink3);font-size:10.5px;border-radius:10px;padding:1px 7px;cursor:pointer;font-family:inherit}
.hist-btn:hover:not(:disabled){border-color:var(--brand);color:var(--brand)}
.hist-btn:disabled{opacity:.35;cursor:default}
textarea{width:100%;resize:none;height:48px;border:0;outline:none;padding:6px 2px;font-size:13.5px;font-family:inherit;background:transparent;color:var(--ink)}
.ctools{display:flex;align-items:center;gap:8px;border-top:1px solid var(--line);padding-top:8px}
.ctools .sp{flex:1}
.cbtn{font-size:11px;color:var(--ink2);border:1px solid var(--line);border-radius:12px;padding:2px 9px;cursor:pointer}
.fchip{font-size:11px;background:#e9f6f0;color:#0f6e56;border-radius:12px;padding:2px 9px;
max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fchip b{font-weight:600}
.composer.dragover .cbox{border-color:#b81c22;background:#fef7f7}
/* 任务卡：对话里的任务不跳页、不刷屏，卡片原地从「在做」变成「做完了」 */
.tcard{border:1px solid var(--line);border-radius:12px;background:#fff;padding:14px 16px;
margin:2px 0 14px;max-width:760px}
.tcard .th{display:flex;align-items:center;gap:9px;margin-bottom:9px}
.tcard .th b{font-size:13.5px;font-weight:600}
.tcard .st{font-size:11px;padding:2px 9px;border-radius:11px;background:#eef0f2;color:#6b7280}
.tcard .st.run{background:#eef4ff;color:#1d4ed8}
.tcard .st.ok{background:#e9f6f0;color:#0f6e56}
.tcard .st.warn{background:#fff6e5;color:#8a5a06}
.tcard .st.bad{background:#fef0ef;color:#a32d2d}
.tcard .hl{font-size:14.5px;line-height:1.65;color:var(--ink);margin:4px 0 10px}
.tcard .hl.warn{color:#8a5a06}
.tcard .al{background:#fff6e5;border-left:3px solid #c2771a;padding:7px 10px;font-size:12.5px;
margin:7px 0;line-height:1.6;color:#6b4a06}
.tcard .fs{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 6px}
.tcard .fs a{text-decoration:none;font-size:12.5px;padding:8px 15px;border-radius:8px;
border:1px solid #e6b8b6;color:#b81c22;background:#fff}
.tcard .fs a.pri{background:#b81c22;color:#fff;border-color:#b81c22}
.tcard .gp{margin-top:13px}
.tcard .gp>.gt{font-size:12.5px;font-weight:600;margin-bottom:5px}
.tcard .gp>.gt .n{font-size:10.5px;font-weight:500;padding:1px 6px;border-radius:9px;
background:#eef0f2;color:#6b7280;margin-left:6px}
.tcard .li{font-size:12.5px;line-height:1.7;padding:4px 0 4px 11px;border-left:2px solid #eef0f2;
color:#374151;margin-bottom:1px}
.tcard .li .fl{font-size:10.5px;background:#fef0ef;color:#a32d2d;padding:1px 6px;border-radius:4px;margin-right:5px}
.tcard .li .tp{font-size:10.5px;color:#9ca3af;margin-left:5px;cursor:help}
.tcard .em{font-size:12px;color:#9ca3af;padding:3px 0 3px 11px}
.tcard .tech{margin-top:12px;border-top:1px solid #eef0f2;padding-top:9px}
.tcard .tech summary{font-size:11.5px;color:#9ca3af;cursor:pointer}
.tcard .tech .in{font-size:11px;color:#6b7280;line-height:1.9;margin-top:6px;
font-family:Consolas,monospace;word-break:break-all}
.cbtn:hover{border-color:var(--brand);color:var(--brand)}
.tag{font-size:11px;color:var(--ink3);border:1px dashed var(--line);border-radius:12px;padding:2px 9px;cursor:default}
.model{border:1px solid var(--line);background:transparent;color:var(--ink3);font-size:11px;font-family:inherit;border-radius:12px;padding:2px 9px;cursor:pointer}
.model:hover{border-color:var(--brand);color:var(--brand)}
.model.on{color:var(--brand);border-color:var(--brand);background:var(--brand-soft)}
.model.blocked{color:var(--warn);border-color:var(--warn);background:var(--warn-bg)}
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
    <a class="side-link" href="/" style="display:block;text-decoration:none">⌂ 品控日报后台</a>
    <div class="tabs"><span class="on">任务</span><span class="off" title="频道功能尚未开放">频道</span><span class="off" title="定时功能尚未开放">定时</span></div>
    <div class="side-scroll">
      <div class="group" data-fold="pin">置顶 <span class="ar">▾</span></div>
      <div class="grp-body" id="body-pin"><div class="proj" style="color:var(--ink3);font-size:11.5px">暂无置顶任务</div></div>
      <div class="group" data-fold="proj">项目 <span class="ar">▾</span></div>
      <div class="grp-body" id="body-proj"><div class="proj">▸ default-workspace <span class="lock">唯一工作区</span></div></div>
      <div class="group" data-fold="recent">最近 <span class="ar">▾</span></div>
      <div class="grp-body" id="body-recent"><div id="conv-list"></div></div>
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
      <span class="wb">FoodQC-Agent · 客户端</span>
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
    <div class="composer" id="composer">
      <div class="cbox">
        <div class="cmdpanel" id="cmd-panel"></div>
        <div class="cbar">
          <span class="tag" title="当前只有一个工作区，多工作区未开放">默认工作区</span>
          <div class="dd" id="mode-dd">
            <button id="mode-btn" title="选择这次提问走哪条路">自动 ▾</button>
            <div class="ddmenu" id="mode-menu"></div>
          </div>
          <div class="sp"></div>
          <span class="cmdhint">输入 / 唤起指令</span>
          <button class="hist-btn" id="hist-up" title="上一条提问" disabled>↑</button>
          <button class="hist-btn" id="hist-down" title="下一条提问" disabled>↓</button>
        </div>
        <textarea id="q" placeholder="说一句话，或者把验收表拖进来…（Enter 发送，Ctrl+Enter 换行）"></textarea>
        <div class="ctools">
          <span class="cbtn" id="file-btn" title="选一张验收表，或者直接把文件拖到这里">＋ 表格</span>
          <input type="file" id="q-file" accept=".csv,.xlsx,.xls" style="display:none">
          <span class="fchip" id="fchip" hidden></span>
          <span class="cbtn" id="kb-toggle2">＋ 知识库</span>
          <span class="tag">只读 · 不放行</span>
          <span class="tag">DEMO-only</span>
          <div class="sp"></div>
          <button class="model" id="llm-btn" title="点一下切换本次提问是否调用大模型">DeepSeek · 关闭</button>
          <button class="send" id="send">↑</button>
        </div>
      </div>
      <div class="hint">只接受 DEMO- 假批次；真实批次号与实测值不会进入本系统。结论仅供复核，不得作为放行依据。</div>
    </div>
  </main>
</div>
<div class="toast" id="toast"></div>
<div class="modal" id="rec-modal" hidden>
  <div class="modal-bg" id="rec-bg"></div>
  <div class="modal-box">
    <div class="modal-h"><b id="rec-title">原始记录</b><button class="modal-x" id="rec-x">×</button></div>
    <div id="rec-body"></div>
  </div>
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
var K_MODE='foodqc.mode.v1', K_LLM='foodqc.llm.v1', K_HIST='foodqc.history.v1', K_FOLD='foodqc.folds.v1';
var MODE='auto', LLM_OVERRIDE=null, LLM_STATE={enabled:false,env:false,has_key:false,source:'env'};
var HIST=[], HIST_POS=-1, FOLDS={};
var MODE_LABEL={auto:'自动',judge:'判定模式',chat:'问答模式',lookup:'只查标准'};
var MODE_DESC={
 auto:'按问题自己判断：该查就查，该判就进判定链',
 judge:'直接进判定链，逐项判 7 项指标并留痕',
 chat:'只做检索与解释，不判定、不写留痕',
 lookup:'只查标准条款与限值出处，不判合格'};
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
   return {id:s.id,title:s.title,created:s.created,msgs:s.msgs.slice(-40).map(function(m){
    // 任务卡的内容不落本地存储（几十 KB 一张，撑爆 5MB 会把所有会话一起弄丢），
    // 只留 run_id，下次打开按需回读
    if(m.mode==='task'&&m.data){
     return {role:m.role,mode:'task',data:{runId:m.data.runId,taskName:m.data.taskName,
      say:m.data.say,terminal:m.data.terminal,refetch:true}};
    }
    return m;
   })};
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
  else if(m.mode==='chat')box.appendChild(buildChat(m.data.answer||{},m.data.meta||{}));
  else if(m.mode==='task')box.appendChild(buildTask(m));
  else if(m.mode==='pick')box.appendChild(buildPick(m));
  else box.appendChild(buildRun(m.data));
 });
 list.forEach(function(m){
  if(m.mode==='task'&&m.data&&m.data.refetch&&!m.data.view)refreshTask(m);
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
/* —— 人工核对指路：独立的琥珀卡片，不跟「逐项明细 / 执行过程 / 校验」混在一起 —— */
function cpBlock(cps){
 if(!cps||!cps.length)return '';
 var items=cps.map(function(c){
  var link=c.action
   ? (c.kind==='record'
      ? '<button class="cp-go" data-rec="'+esc(c.action)+'">'+esc(c.label)+' ↗</button>'
      : '<a class="cp-go" href="'+esc(c.action)+'" target="_blank" rel="noopener">'+esc(c.label)+' ↗</a>')
   : '<span class="cp-off">系统外 · 不用点这里</span>';
  return '<div class="cp"><div class="cp-h">'+esc(c.title)+'</div>'+
         '<div class="cp-d">'+esc(c.desc)+'</div>'+link+'</div>';
 }).join('');
 return '<div class="cpbox"><div class="cp-t">⚑ 这几处需要人工核对 · 按下面指的位置去</div>'+
        items+'</div>';
}
function bindCP(root){
 root.querySelectorAll('[data-rec]').forEach(function(b){
  b.onclick=function(){openRecord(b.dataset.rec);};
 });
}
async function openRecord(url){
 var m=el('rec-modal'),body=el('rec-body');
 m.hidden=false;body.textContent='读取中…';
 try{
  var r=await fetch(url),d=await r.json();
  if(!d.ok)throw new Error(d.error||'读取失败');
  el('rec-title').textContent=d.batch_id+' · 判定链实际使用的原始记录（只读）';
  var rows=(d.rows||[]).map(function(x){
   var bad=(String(x.unit||'').indexOf('%')>=0&&Number(x.value)>100);
   return '<tr'+(bad?' class="bad"':'')+'><td>'+esc(x.item)+'</td>'+
    '<td class="num">'+esc(x.value)+'</td><td>'+esc(x.unit||'')+'</td>'+
    '<td>'+esc(x.direction||'')+' '+esc(x.limit_value)+'</td>'+
    '<td>'+esc(x.standard||'')+'</td></tr>';
  }).join('');
  body.innerHTML='<div class="rec-src"><b>来源：</b>'+esc(d.source)+'</div>'+
   '<table class="rec-tb"><thead><tr><th>检测项目</th><th>实测值</th><th>单位</th>'+
   '<th>限值</th><th>标准号</th></tr></thead><tbody>'+rows+'</tbody></table>'+
   '<div class="rec-note">'+esc(d.note)+'</div>';
 }catch(e){body.textContent='读取失败：'+e.message;}
}
function closeRecord(){el('rec-modal').hidden=true;}

function buildChat(a,meta){
 var foot=(meta&&meta.mode_used)?('<div class="footline">模式 '+esc(MODE_LABEL[meta.mode_used]||meta.mode_used)+' · '+
  (meta.router==='llm'?'模型识别':(meta.router==='mode'?'模式强制':'规则路由'))+
  ' · 模型 '+(meta.llm_used?'已调用':'未调用')+'</div>'):'';
 var isProse=!!a.prose;
 var lines=isProse
  ? (a.lines||[]).map(function(x){return '<p>'+esc(x)+'</p>';}).join('')
  : (a.lines||[]).map(function(x){return '<li>'+esc(x)+'</li>';}).join('');
 var body=isProse
  ? '<div class="prose" style="font-size:13px">'+lines+'</div>'
  : '<ul style="margin:0;padding-left:18px;font-size:12.5px;line-height:1.7">'+lines+'</ul>';
 var bd=(a.boundary||[]).map(function(x){return '<li>'+esc(x)+'</li>';}).join('');
 var ex=(a.examples||[]).map(function(x){return '<button class="chip" data-q="'+esc(x)+'">'+esc(x)+'</button>';}).join('');
 var cps=cpBlock(meta&&meta.cps);
 var h='<div class="msg"><div class="who"><span class="sq"></span>FoodQC-Agent</div><div class="card">'+
  '<div class="h3" style="font-size:14px;margin:0 0 10px">'+esc(a.title)+'</div>'+
  body+cps+
  (bd?'<div class="disclaim"><ul style="margin:0;padding-left:16px">'+bd+'</ul></div>':'')+
  (ex?'<div class="h3">试试这样问</div><div class="chips" style="margin:0">'+ex+'</div>':'')+
  foot+
 '</div></div>';
 var t=document.createElement('template');t.innerHTML=h;
 t.content.querySelectorAll('[data-q]').forEach(function(b){b.onclick=function(){send(b.dataset.q);};});
 bindCP(t.content);
 return t.content;
}
function buildRun(d){
 var n=(d.trace_events||[]).length;
 var ev=(d.trace_events||[]).map(function(x){
   return '<div class="ev"><span class="nm">'+esc(x.tool)+' ('+esc(JSON.stringify(x.args||{}))+')</span>'+
          '<span class="rs">'+esc(x.result_brief||'')+'</span></div>';
 }).join('')||'<div class="cn">本次没有工具调用留痕</div>';
 var s=d.sections||{},brief=d.brief||{},c=brief.counts||{};
 var paras=(brief.paras||[]).map(function(x){return '<p>'+esc(x)+'</p>';}).join('')
   ||'<p>逐项判定跑完了，明细在下面。</p>';
 var total=(c.confirmed||0)+(c.review||0)+(c.uncovered||0);
 var detail='<div class="h3" style="margin-top:0">三段式输出</div><div class="tri">'+
   tri('已确认','a',s['已确认'])+tri('待人工复核','b',s['待人工复核'])+tri('未覆盖','c',s['未覆盖'])+
  '</div>'+
  '<div class="disclaim">结论仅供复核，不得作为放行依据；最终由人签字。模型不算数，数字由代码算。</div>';
 var trace='<div class="evlist">'+ev+'</div>'+
  '<div class="disclaim">以上每一步都是真实调用；模型只决定调哪个工具，不产数字、不定结论。</div>';
 var foot=esc(d.batch_id)+' · '+esc(d.steps)+' 步 · '+
  (d.router==='llm'?'模型识别':(d.router==='mode'?'模式强制':'规则路由'))+
  ' · 模型 '+(d.llm_used?'已调用':'未调用')+' · '+esc(d.data_scope||'DEMO-only')+
  (d.trace_path?'<br>留痕 '+esc(d.trace_path)+(d.report_path?'　|　'+esc(d.report_path):''):'');
 var h='<div class="msg"><div class="who"><span class="sq"></span>FoodQC-Agent</div><div class="card">'+
  '<div class="prose">'+paras+'</div>'+cpBlock(d.checkpoints)+
  '<div class="toggles">'+
   '<button class="tg" data-p="detail">逐项明细（'+esc(total)+'）▾</button>'+
   '<button class="tg" data-p="trace">执行过程（'+esc(n)+'）▾</button>'+
  '</div>'+
  '<div class="pane" data-p="detail" hidden>'+detail+'</div>'+
  '<div class="pane" data-p="trace" hidden>'+trace+'</div>'+
  '<div class="footline">'+foot+'</div>'+
 '</div></div>';
 var t=document.createElement('template');t.innerHTML=h;
 t.content.querySelectorAll('.tg').forEach(function(b){
  b.onclick=function(){togglePane(b);};
 });
 bindCP(t.content);
 return t.content;
}
function togglePane(b){
 var card=b.parentNode.parentNode,key=b.dataset.p;
 var pane=card.querySelector('.pane[data-p="'+key+'"]');
 if(!pane)return;
 var closing=!pane.hidden;
 pane.hidden=closing;
 b.classList.toggle('on',!closing);
 b.textContent=b.textContent.replace(/[▾▴]/,closing?'▾':'▴');
}
function showRunPane(card,key){
 var b=card.querySelector('.tg[data-p="'+key+'"]');
 if(!b)return;
 var pane=card.querySelector('.pane[data-p="'+key+'"]');
 if(pane&&pane.hidden){pane.hidden=false;b.classList.add('on');
  b.textContent=b.textContent.replace(/[▾▴]/,'▴');}
 if(pane)pane.scrollIntoView({behavior:'smooth',block:'center'});
}
function addUser(txt){push({role:'user',text:txt});renderMsgs();}
function addErr(txt){push({role:'err',text:txt});renderMsgs();}
function addChat(a,meta){push({role:'agent',mode:'chat',data:{answer:a,meta:meta||{}}});renderMsgs();}
function addAgent(d){
 if(d.mode==='chat'){addChat(d.answer||{},{mode_used:d.mode_used,router:d.router,llm_used:d.llm_used,cps:d.checkpoints});return;}
 push({role:'agent',mode:'run',data:d});renderMsgs();
}

// ---------------------------------------------------------------- 对话里做任务
// 一句话 + 一张表 → 后台跑 → 卡片从「正在读表格」原地变成「做完了」+ 下载按钮
var TFILES=[];
function renderChip(){
 var c=el('fchip');if(!c)return;
 if(!TFILES.length){c.hidden=true;c.innerHTML='';return;}
 var f=TFILES[0];
 c.hidden=false;
 c.innerHTML='<b>'+esc(f.name)+'</b> '+esc(f.rows)+' 行'+
  (TFILES.length>1?' · 共 '+TFILES.length+' 个':'')+
  ' <span id="fchip-x" style="cursor:pointer;color:#a32d2d;font-weight:600">×</span>';
 var x=el('fchip-x');if(x)x.onclick=function(){TFILES=[];renderChip();};
}
async function uploadTable(f){
 if(!f)return;
 el('fchip').hidden=false;el('fchip').textContent='正在读 '+f.name+'…';
 var fd=new FormData();fd.append('file',f);
 try{
  var r=await fetch('/api/tasks/upload',{method:'POST',body:fd});
  var d=await r.json();
  if(!r.ok||!d.ok)throw new Error(d.error||('HTTP '+r.status));
  TFILES.push({path:d.path,name:d.name,rows:d.rows,date:d.data_date||''});
  renderChip();
  toast('已读入 '+d.name+'（'+d.rows+' 行'+(d.data_date?'，日期 '+d.data_date:'')+'），说一句要做什么');
 }catch(e){el('fchip').hidden=true;addErr('这张表没读进来：'+e.message);}
}
async function sendTask(q,taskId,label){
 var txt=q||(label?('就按「'+label+'」做'):'把这张表做成今天的验收日报');
 var body={question:txt,files:TFILES.map(function(f){return f.path;})};
 if(taskId)body.task_id=taskId;
 addUser(txt);pushHistory(txt);
 var b=el('send');b.disabled=true;b.textContent='…';
 try{
  var r=await fetch('/api/client/task',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)});
  var d=await r.json();
  if(!r.ok||!d.ok)throw new Error(d.error||('HTTP '+r.status));
  if(d.need==='choose'){push({role:'agent',mode:'pick',data:d});renderMsgs();return;}
  TFILES=[];renderChip();
  push({role:'agent',mode:'task',data:{runId:d.run_id,taskName:d.task_name,
    say:d.say+'　数据日期取自'+d.date_from+'。',view:null,terminal:false}});
  renderMsgs();
  pollTask(cur().msgs[cur().msgs.length-1]);
 }catch(e){addErr('任务没跑起来：'+e.message);}
 finally{b.disabled=false;b.textContent='↑';el('q').value='';closeCmd();el('q').focus();}
}
function refreshTask(m){
 if(!m.data||m.data._busy)return;
 m.data._busy=true;
 fetch('/api/client/task/'+encodeURIComponent(m.data.runId)).then(function(r){return r.json();})
  .then(function(d){
   delete m.data._busy;
   if(!d.ok)throw new Error(d.error||'读不到');
   m.data.view=d.view;m.data.terminal=!!d.terminal;m.data.refetch=false;
   renderMsgs();
  }).catch(function(){delete m.data._busy;});
}
function pollTask(m){
 var tick=0;
 var timer=setInterval(function(){
  tick++;
  fetch('/api/client/task/'+encodeURIComponent(m.data.runId)).then(function(r){return r.json();})
   .then(function(d){
    if(!d.ok)throw new Error(d.error||'读不到状态');
    m.data.view=d.view;m.data.terminal=!!d.terminal;
    renderMsgs();
    if(d.terminal){clearInterval(timer);save();}
   }).catch(function(e){
    clearInterval(timer);
    m.data.err='任务状态读不到了：'+e.message;
    renderMsgs();
   });
  if(tick>240)clearInterval(timer);
 },1200);
}
function buildTask(m){
 var v=m.data.view||{};
 var done=!!m.data.terminal;
 var tone=done?(v.status_tone||'ok'):'run';
 var st=done?(v.status_cn||'做完了'):'正在做';
 var hl=done?(v.headline||''):(m.data.say||'正在读表…');
 var h='<div class="msg"><div class="who"><span class="sq"></span>FoodQC-Agent</div>'+
  '<div class="tcard"><div class="th"><b>'+esc(m.data.taskName||v.task_name||'任务')+'</b>'+
  '<span class="st '+esc(tone)+'">'+esc(st)+'</span></div>'+
  '<div class="hl'+(done&&v.headline_tone==='warn'?' warn':'')+'">'+esc(hl)+'</div>';
 (v.alerts||[]).forEach(function(a){h+='<div class="al">'+esc(a)+'</div>';});
 if(m.data.err)h+='<div class="al">'+esc(m.data.err)+'</div>';
 if((v.files||[]).length){
  h+='<div class="fs">';
  v.files.forEach(function(f){
   h+='<a class="'+(f.primary?'pri':'')+'" href="'+esc(f.href)+'" target="_blank">'+esc(f.label)+'</a>';
  });
  h+='</div>';
 }
 (v.groups||[]).forEach(function(g){
  if(!g.count)return;
  h+='<div class="gp"><div class="gt">'+esc(g.label)+'<span class="n">'+g.count+'</span></div>';
  (g.items||[]).forEach(function(it){
   h+='<div class="li">'+(it.flag?'<span class="fl">'+esc(it.flag)+'</span>':'')+esc(it.text)+
    (it.tip?'<span class="tp" title="'+esc(it.tip)+'">出处</span>':'')+'</div>';
  });
  h+='</div>';
 });
 if(done&&!(v.groups||[]).some(function(g){return g.count;})){
  h+='<div class="em">这次没有可读的逐条结论，展开下面技术信息看原因。</div>';
 }
 var t=v.tech||{};
 h+='<details class="tech"><summary>技术信息（复核用）</summary><div class="in">'+
  'run_id　'+esc(t.run_id||m.data.runId)+'<br>'+
  (t.digests||[]).map(function(d){return esc(d[0])+'　'+esc(d[1]);}).join('<br>')+
  ((t.all_files||[]).length?'<br>产物　'+esc(t.all_files.join(' / ')):'')+
  '</div></details>';
 h+='</div></div>';
 var tp=document.createElement('template');tp.innerHTML=h;return tp.content;
}
function buildPick(m){
 var d=m.data;
 var h='<div class="msg"><div class="who"><span class="sq"></span>FoodQC-Agent</div><div class="card">'+
  '<div class="prose"><p>'+esc(d.reason||'选一个：')+'</p></div>'+
  '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">';
 (d.candidates||[]).forEach(function(c,i){
  h+='<button class="cbtn picksel" data-i="'+i+'" style="padding:7px 13px">'+esc(c.name)+'</button>';
 });
 h+='</div></div></div>';
 var tp=document.createElement('template');tp.innerHTML=h;
 tp.content.querySelectorAll('.picksel').forEach(function(b){
  b.onclick=function(){
   var c=d.candidates[+b.dataset.i];
   b.disabled=true;b.textContent=c.name+'（已选）';
   sendTask(null,c.task_id,c.name);
  };
 });
 return tp.content;
}
async function send(q){
 q=String(q||'').trim();
 if(!q&&!TFILES.length)return;
 if(TFILES.length){return sendTask(q||null);}
 addUser(q);pushHistory(q);
 var b=el('send');b.disabled=true;b.textContent='…';
 try{
  var r=await fetch('/api/client/chat',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({question:q,mode:MODE,use_llm:LLM_OVERRIDE})});
  var d=await r.json();
  if(!r.ok||!d.ok){throw new Error(d.error||('HTTP '+r.status));}
  if(d.llm_state)applyLlmState(d.llm_state);
  addAgent(d);
 }catch(e){addErr('运行失败：'+e.message);}
 finally{b.disabled=false;b.textContent='↑';el('q').value='';closeCmd();el('q').focus();}
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
 {ic:'▦',t:'把表格做成日报',d:'拖一张验收表进来，直接出报告',file:true},
 {ic:'◈',t:'判定一批稻谷',d:'逐项判 7 项指标，带 GB 出处',q:CHIPS[1]},
 {ic:'◇',t:'录入错误自查',d:'5 类可疑数，只标疑点不改数',q:CHIPS[2]},
 {ic:'▤',t:'查标准依据',d:'从 corpus 检索带出处条款',q:CHIPS[4]},
 {ic:'?',t:'看我能做什么',d:'能力清单与边界',q:CHIPS[0]}
];
CARDS.forEach(function(c){
 var b=document.createElement('button');b.className='wcard';
 b.innerHTML='<span class="ic">'+c.ic+'</span><b>'+esc(c.t)+'</b><small>'+esc(c.d)+'</small>';
 b.onclick=function(){
  if(c.file){toast('选一张 Excel 或 CSV，选完直接说一句要做什么');el('q-file').click();return;}
  send(c.q);
 };
 el('wcards').appendChild(b);
});
var TOAST_T=null;
function toast(msg){
 var t=el('toast');if(!t)return;
 t.textContent=msg;t.classList.add('show');
 clearTimeout(TOAST_T);
 TOAST_T=setTimeout(function(){t.classList.remove('show');},2200);
}

/* —— 斜杠指令：每条都真的执行一个动作，不是一张菜单贴图 —— */
var CMDS=[
 {k:'/判定',d:'切到判定模式，逐项判 DEMO 假批次',run:function(){setMode('judge');toast('已切到判定模式');}},
 {k:'/查标准',d:'只检索 corpus 条款，不进判定链',run:function(){setMode('lookup');toast('已切到只查标准');}},
 {k:'/问答',d:'只解释不判定，不写留痕',run:function(){setMode('chat');toast('已切到问答模式');}},
 {k:'/自动',d:'按问题自己判断该查还是该判',run:function(){setMode('auto');toast('已切回自动');}},
 {k:'/能力',d:'直接问「你能干什么」',run:function(){send('你能干什么');}},
 {k:'/留痕',d:'展开最近一次执行的真实工具调用',run:function(){showLastTrace();}},
 {k:'/新建',d:'开一个新对话',run:function(){newSession();toast('新对话已开');}}
];
var CMD_OPEN=false,CMD_SEL=0,CMD_LIST=[];
function closeCmd(){CMD_OPEN=false;el('cmd-panel').classList.remove('show');}
function cmdFilter(q){
 var body=q.trim().slice(1).toLowerCase();
 return CMDS.filter(function(c){return !body||c.k.slice(1).indexOf(body)>=0||c.d.indexOf(body)>=0;});
}
function renderCmd(){
 var p=el('cmd-panel'),q=el('q').value;
 if(!q||q.charAt(0)!=='/'){closeCmd();p.innerHTML='';return;}
 CMD_LIST=cmdFilter(q);
 CMD_OPEN=CMD_LIST.length>0;
 p.classList.add('show');
 if(!CMD_LIST.length){
  p.innerHTML='<div class="ct">指令</div><div class="cn">没有匹配的指令，按 Esc 关闭</div>';return;
 }
 if(CMD_SEL>=CMD_LIST.length)CMD_SEL=0;
 var html='<div class="ct">指令 · ↑↓ 选择 · Enter 执行 · Esc 关闭</div>';
 CMD_LIST.forEach(function(c,i){
  html+='<button class="ci'+(i===CMD_SEL?' on':'')+'" data-i="'+i+'">'+
        '<span class="k">'+esc(c.k)+'</span><span class="d">'+esc(c.d)+'</span></button>';
 });
 p.innerHTML=html;
 p.querySelectorAll('.ci').forEach(function(b){
  b.onmousedown=function(e){e.preventDefault();execCmd(Number(b.dataset.i));};
 });
}
function execCmd(i){
 var c=CMD_LIST[i];if(!c)return;
 el('q').value='';closeCmd();el('cmd-panel').innerHTML='';
 c.run();
}
function showLastTrace(){
 var last=null;
 el('msgs').querySelectorAll('.card').forEach(function(c){
  if(c.querySelector('.tg[data-p="trace"]'))last=c;
 });
 if(!last){toast('这个对话还没跑过判定，先发一句 DEMO- 批次');return;}
 showRunPane(last,'trace');
 toast('已展开最近一次的执行过程');
}

function toggleKB(){var b=el('kb-box');b.style.display=(b.style.display==='none'?'block':'none');}

/* —— 判定模式下拉：真的会改变后端走哪条路 —— */
function renderMode(){
 el('mode-btn').textContent=MODE_LABEL[MODE]+' ▾';
 var m=el('mode-menu');m.innerHTML='';
 Object.keys(MODE_LABEL).forEach(function(k){
  var b=document.createElement('button');
  b.textContent=MODE_LABEL[k];b.className=(k===MODE?'on':'');
  b.onclick=function(){setMode(k);el('mode-dd').classList.remove('open');};
  m.appendChild(b);
 });
 var n=document.createElement('div');n.className='note';n.textContent=MODE_DESC[MODE];
 m.appendChild(n);
}
function setMode(k){
 if(!MODE_LABEL[k])return;
 MODE=k;try{localStorage.setItem(K_MODE,k);}catch(e){}
 renderMode();el('q').focus();
}

/* —— DeepSeek 开关：显示的是服务端真实状态，不是贴纸 —— */
function applyLlmState(s){if(s)LLM_STATE=s;renderLlm();}
function llmOn(){
 return LLM_OVERRIDE===null ? !!(LLM_STATE&&LLM_STATE.enabled) : LLM_OVERRIDE;
}
function renderLlm(){
 var b=el('llm-btn');b.className='model';var on=llmOn();
 if(on&&!(LLM_STATE&&LLM_STATE.has_key)){
  b.classList.add('blocked');b.textContent='DeepSeek · 无Key';
  b.title='服务端没有 DEEPSEEK_API_KEY，开了也会自动降级回规则路由';return;
 }
 if(on){
  b.classList.add('on');b.textContent='DeepSeek · 开启';
  b.title='本次会调模型做意图识别；判定数字与结论仍由代码算';
 }else{
  b.textContent='DeepSeek · 关闭';
  b.title='本次不调模型，走规则路由（离线可用）';
 }
}
function toggleLlm(){
 LLM_OVERRIDE=!llmOn();
 try{localStorage.setItem(K_LLM,LLM_OVERRIDE?'on':'off');}catch(e){}
 renderLlm();
}

/* —— 历史 ↑↓：翻本机发过的提问 —— */
function pushHistory(q){
 if(!HIST.length||HIST[0]!==q)HIST.unshift(q);
 HIST=HIST.slice(0,50);HIST_POS=-1;
 try{localStorage.setItem(K_HIST,JSON.stringify(HIST));}catch(e){}
 renderHist();
}
function renderHist(){
 el('hist-up').disabled=!HIST.length;
 el('hist-down').disabled=!(HIST_POS>=0);
}
function histPrev(){
 if(!HIST.length)return;
 if(HIST_POS<HIST.length-1)HIST_POS++;
 el('q').value=HIST[HIST_POS];renderHist();el('q').focus();
}
function histNext(){
 if(HIST_POS<0)return;
 HIST_POS--;
 el('q').value=HIST_POS<0?'':HIST[HIST_POS];
 renderHist();el('q').focus();
}

/* —— 左栏分组真的能折叠 —— */
function renderFolds(){
 document.querySelectorAll('.group[data-fold]').forEach(function(g){
  var k=g.dataset.fold,body=el('body-'+k),folded=!!FOLDS[k];
  g.classList.toggle('folded',folded);
  if(body)body.classList.toggle('folded',folded);
 });
}
function toggleFold(k){
 FOLDS[k]=!FOLDS[k];
 try{localStorage.setItem(K_FOLD,JSON.stringify(FOLDS));}catch(e){}
 renderFolds();
}

function loadPrefs(){
 try{
  var m=localStorage.getItem(K_MODE);if(m&&MODE_LABEL[m])MODE=m;
  var l=localStorage.getItem(K_LLM);
  LLM_OVERRIDE = l==='on' ? true : (l==='off' ? false : null);
  var h=localStorage.getItem(K_HIST);HIST=h?JSON.parse(h):[];
  if(!Array.isArray(HIST))HIST=[];
  var f=localStorage.getItem(K_FOLD);FOLDS=f?JSON.parse(f):{};
  if(!FOLDS||typeof FOLDS!=='object')FOLDS={};
 }catch(e){HIST=[];FOLDS={};LLM_OVERRIDE=null;}
 HIST_POS=-1;
}
async function loadState(){
 try{
  var r=await fetch('/api/client/state');var d=await r.json();
  if(d&&d.ok)applyLlmState(d.llm);
 }catch(e){}
}

el('kb-toggle').onclick=toggleKB;
el('kb-toggle2').onclick=toggleKB;
el('send').onclick=function(){send(el('q').value);};
el('file-btn').onclick=function(){el('q-file').click();};
el('q-file').onchange=function(){uploadTable(this.files[0]);this.value='';};
(function(){
 var cz=el('composer');
 ['dragenter','dragover'].forEach(function(ev){
  cz.addEventListener(ev,function(e){e.preventDefault();cz.classList.add('dragover');});
 });
 ['dragleave','drop'].forEach(function(ev){
  cz.addEventListener(ev,function(e){e.preventDefault();cz.classList.remove('dragover');});
 });
 cz.addEventListener('drop',function(e){
  var f=e.dataTransfer&&e.dataTransfer.files&&e.dataTransfer.files[0];
  if(f)uploadTable(f);
 });
})();
el('q').addEventListener('keydown',function(e){
 if(CMD_OPEN){
  if(e.key==='ArrowDown'){e.preventDefault();CMD_SEL=Math.min(CMD_SEL+1,CMD_LIST.length-1);renderCmd();return;}
  if(e.key==='ArrowUp'){e.preventDefault();CMD_SEL=Math.max(CMD_SEL-1,0);renderCmd();return;}
  if(e.key==='Enter'){e.preventDefault();execCmd(CMD_SEL);return;}
  if(e.key==='Escape'){e.preventDefault();closeCmd();el('cmd-panel').innerHTML='';return;}
 }
 if(e.key==='Enter'&&!e.ctrlKey&&!e.metaKey){e.preventDefault();send(this.value);}
});
el('q').addEventListener('input',function(){CMD_SEL=0;renderCmd();});
el('k-up').onclick=upload;
el('new-conv').onclick=newSession;
el('theme').onclick=function(){
 var b=document.body;b.dataset.theme=b.dataset.theme==='dark'?'light':'dark';
 el('theme').textContent=b.dataset.theme==='dark'?'切换浅色':'切换深色';
};
el('mode-btn').onclick=function(e){
 e.stopPropagation();el('mode-dd').classList.toggle('open');
};
el('llm-btn').onclick=toggleLlm;
el('hist-up').onclick=histPrev;
el('hist-down').onclick=histNext;
el('rec-x').onclick=closeRecord;
el('rec-bg').onclick=closeRecord;
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeRecord();});
document.addEventListener('click',function(e){
 var dd=el('mode-dd');if(dd&&!dd.contains(e.target))dd.classList.remove('open');
 var cp=el('cmd-panel');
 if(cp&&e.target!==el('q')&&!cp.contains(e.target)){closeCmd();}
});
document.querySelectorAll('.group[data-fold]').forEach(function(g){
 g.onclick=function(){toggleFold(g.dataset.fold);};
});

loadStore();loadPrefs();renderAll();renderMode();renderLlm();renderFolds();renderHist();
loadKB();loadState();
</script>
</body>
</html>
"""


def _brief(result: dict) -> dict:
    """把三段式内部结构压成一段能读的话。

    数字只来自 sections 的条数统计，措辞是固定模板，模型不参与。
    重要：这里依然不给「能收/不能收」——恰恰相反，要说清为什么不给。
    """
    s = result.get("sections") or {}
    ok = [str(x) for x in (s.get("已确认") or [])]
    rv = [str(x) for x in (s.get("待人工复核") or [])]
    ms = [str(x) for x in (s.get("未覆盖") or [])]
    passed = sum(1 for x in ok if "判定「合格」" in x)
    suspect = next((x for x in rv if "录入疑点" in x), "")
    version = next((x for x in rv if "标准版本" in x), "")

    m = re.match(r"^([^：:]+)[：:]", suspect)
    ind = m.group(1).strip() if m else "有一项指标"
    batch = result.get("batch_id") or "这个批次"

    tools = [str(t) for t in (result.get("tools_called") or [])]
    if "judge_batch" not in tools:
        # 一个工具拒绝就整链中止：判定根本没跑，旧版却说「逐项判定跑完了」（夜测 S4）。
        # 对要签字的人来说，「没查」和「查过没问题」是两件完全不同的事。
        #
        # 旧版还有一句写死的「没识别出你要问的批次和指标」：实测给了批次号
        # DEMO-999 和没给批次号 DEMO-001，两句逐字相同，打 DEMO-999 的人会照提示
        # 重打同一个号，永远出不来（夜测 11-10 r02）。手里有 batch_id 就别再说「没识别出」。
        where = "只跑到查限值就停了" if "lookup_limit" in tools else "没进入逐项判定"
        if batch and batch != "这个批次":
            cause = (f"系统拿到的批次是 {batch}，但没有可判的指标记录，{where}。"
                     "（如果你并没有写批次号，这是系统用的默认演示批次，不是你的来料。）")
            nxt = "下一步：换一个 DEMO 假批次，或把该批次的检测项目与实测值补进 DEMO 数据后再问。"
        else:
            cause = f"没识别出你要问的批次和指标，{where}。"
            nxt = ("下一步：带上 DEMO 批次号和具体指标再问一次，"
                   "例如「DEMO-001 这批稻谷出糙率 76.5 能收吗」。")
        has_concl = bool(ok or rv or ms)
        second = ("下面有逐项结论，但本次没跑完整条判定链，不给「能收 / 不能收」。"
                  if has_concl else
                  "下面没有逐项判定结论，也不给「能收 / 不能收」。这不是「查过没问题」，是根本没查。")
        return {
            "paras": [f"这次判定没跑成 —— {cause}", second, nxt],
            "counts": {"confirmed": len(ok), "review": len(rv),
                       "uncovered": len(ms), "passed": 0},
        }

    paras: list[str] = []
    if passed:
        paras.append(f"{batch} 我逐项比过了，{passed} 项数值按代码判定落在限值内。")
    else:
        paras.append(f"{batch} 的逐项判定跑完了，明细在下面。")
    if suspect:
        paras.append(
            f"{ind}那格填的数不对劲：百分类不该超过 100，看着像小数点或单位敲错了。"
            "这条我只标了疑点，没拿去判合格不合格，麻烦翻一下原始记录核对。"
        )
    if version:
        vers = re.findall(r"GB\s*1350[—-]\d{4}", version)
        if len(vers) >= 2:
            paras.append(
                f"还有个前提得说在前面：现在执行的是 {vers[0]}，语料里的 {vers[1]} 还没实施，"
                "限值也还没人工核对过。"
            )
        else:
            paras.append("还有个前提：当前执行的版本与语料里的新版不是一回事，限值也还没人工核对过。")
    paras.append("所以我不给「能收 / 不能收」，这里只摆事实和待复核的原因，放行得你复核完签字。")

    return {
        "paras": paras,
        "counts": {"confirmed": len(ok), "review": len(rv),
                   "uncovered": len(ms), "passed": passed},
    }


# 出现这些字样，就说明这次回答把球踢给了人；必须同时给出「去哪、看什么」。
_NEED_CHECK = ("需要人工核对", "待人工复核", "仍须人工核对", "尚未人工核对",
               "翻一下原始记录", "复核完签字", "人工复核签字", "翻原始记录")


def _answer_text(a: dict) -> str:
    """把结构化回答摊平，只用来判断有没有「需要人工核对」的标记。"""
    return "\n".join([str(a.get("title") or "")] +
                     [str(x) for x in (a.get("lines") or [])] +
                     [str(x) for x in (a.get("boundary") or [])])


def _checkpoints(text: str, batch_id: str, mode: str, in_chain: bool = False) -> list[dict]:
    """把「需要人工核对」的空话变成可直达的入口。

    in_chain 表示这次真的跑了判定链（此时 mode 是 judge/auto，不等于返回体的 "run"）。
    只指向真实存在的位置；系统外的步骤明说系统外，不伪装成按钮。
    """
    t = text or ""
    has_mark = any(k in t for k in _NEED_CHECK)
    if in_chain and has_mark:
        out: list[dict] = []
        if batch_id:
            out.append({
                "kind": "record",
                "title": f"先看 {batch_id} 这批的原始记录",
                "desc": "判定链实际读的是 qc_daily/harness.py 里的 _DEMO_PLAN。"
                        "点开能直接看到这批的实测值，只读，不会改动任何数据。",
                "action": f"/api/client/record/{batch_id}",
                "label": f"打开 {batch_id} 原始数据",
            })
        out.append({
            "kind": "knowledge",
            "title": "去知识库核对限值",
            "desc": "当前执行的 GB 1350—2009 限值还没人工核对。"
                    "在 /knowledge 页面下半部分的「待核对区」上传或确认资料。",
            "action": "/knowledge#kb-inbox-section",
            "label": "打开知识库 · 待核对区",
        })
        out.append({
            "kind": "human",
            "title": "放行签字这一步在系统外",
            "desc": "本系统不放行、不签字。按汇报线找班长或主任，由人复核后签字。",
            "action": "",
            "label": "",
        })
        return out
    if mode == "lookup" and has_mark:
        return [{
            "kind": "knowledge",
            "title": "限值要人工核对后才能用",
            "desc": "在 /knowledge 页面下半部分的「待核对区」核对标准原文，确认后才纳入检索与判定。",
            "action": "/knowledge#kb-inbox-section",
            "label": "打开知识库 · 待核对区",
        }]
    return []


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


_MODES = ("auto", "judge", "chat", "lookup")


def _norm_mode(raw: Any) -> str:
    m = str(raw or "auto").strip().lower()
    return m if m in _MODES else "auto"


def _norm_use_llm(raw: Any) -> bool | None:
    """三态：True 强制开 / False 强制关 / None 跟随服务端环境变量。"""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        v = raw.strip().lower()
        if v in ("1", "true", "on", "yes", "开", "开启"):
            return True
        if v in ("0", "false", "off", "no", "关", "关闭"):
            return False
    return None


def _mode_notice(question: str) -> dict:
    """问答模式下挡回判定请求：说明为什么没判，以及怎么切。"""
    return {
        "title": "这是判定类问题，但当前是「问答模式」，我没有进判定链",
        "lines": [
            "问答模式只做检索与解释，不判合格、不算等级、不写留痕。",
            "要逐项判这批货，把上方「问答模式 ▾」切到「判定模式」，再发一次。",
        ],
        "boundary": ["判定必须是一次显式动作，不能被顺手触发",
                     "演示只用 DEMO- 开头的假批次，真实批次号与实测值不进本系统"],
        "examples": ["DEMO-001 这批稻谷出糙率 76.5 能收吗", "出糙率 3 等限值是多少"],
    }


def _decide(question: str, mode: str) -> tuple[dict | None, bool, str]:
    """入口分流。返回 (payload | None, 是否真的调了模型, 路由器)。

    payload 为 None = 交给判定链；否则直接渲染这段回答。
    """
    from . import intent

    if mode == "lookup":                       # 只查标准：不进判定，只检索语料
        return {"answer": intent.knowledge_answer(question)}, False, "rules"
    if mode == "judge":                        # 判定模式：跳过意图层直接进链
        return None, False, "mode"

    action, payload = intent.llm_route(question)
    used_llm = action != "fallback"
    if action == "fallback":
        routed = intent.route(question)
        if routed:
            return {"answer": routed["answer"]}, used_llm, "rules"
        if mode == "chat":                     # 规则层认为该判定，但用户在问答模式
            return {"answer": _mode_notice(question)}, False, "mode"
        return None, used_llm, "rules"         # 自动模式：判定请求进链
    if action == "judge":
        if mode == "chat":
            return {"answer": _mode_notice(question)}, used_llm, "mode"
        return None, used_llm, "llm"
    if payload and payload.get("answer"):
        return {"answer": payload["answer"]}, used_llm, "llm"
    return None, used_llm, "llm"


@client_bp.post("/api/client/chat")
def client_chat():
    data = request.get_json(silent=True) or {}
    question = str(data.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "请输入一个问题"}), 400
    if len(question) > 300:
        return jsonify({"ok": False, "error": "问题请控制在 300 字以内"}), 400

    from . import flags

    mode = _norm_mode(data.get("mode"))
    token = flags.set_llm(_norm_use_llm(data.get("use_llm")))
    state = flags.llm_state()          # 生效状态要先取，finally 里会还原
    try:
        # 入口理解层：先试可选 LLM 意图识别（只出白名单意图+检索词，不产数字/结论），
        # 没 key / 未开 / 超时 / 异常一律静默退回规则路由。判定请求才进 harness。
        payload, used_llm, router = _decide(question, mode)
        if payload is not None:
            return jsonify({
                "ok": True, "mode": "chat", "question": question,
                "answer": payload["answer"],
                "router": router,
                "mode_used": mode,
                "llm_state": state,
                "checkpoints": _checkpoints(_answer_text(payload["answer"]), "", mode),
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
        brief = _brief(result)
        run_text = "\n".join(brief["paras"] +
                             [str(x) for x in (result.get("sections", {}).get("待人工复核") or [])])
        return jsonify({
            "ok": True,
            "mode": "run",
            "mode_used": mode,
            "router": router,
            "llm_state": state,
            "checkpoints": _checkpoints(run_text, result.get("batch_id", ""), mode, in_chain=True),
            "question": question,
            "batch_id": result.get("batch_id", ""),
            "brief": brief,
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
    finally:
        flags.reset_llm(token)


# ------------------------------------------------------------------ 对话里做任务
# 目标：在同一个输入框里，拖一张表 + 说一句话 → 后台跑 → 结果原地变成可下载的卡片。
# 边界：只吃上传目录里的表（防任意路径读取）；判定仍走 task runner，不在这里另写一套。

_REPO_ROOT = Path(__file__).resolve().parent.parent
_UPLOAD_ROOT = (_REPO_ROOT / "qc_daily_data" / "task_uploads").resolve()
_INPUT_EXT = {".csv", ".xlsx", ".xls"}
#: 对话端的默认交付物：网页看 + Excel 存。PDF 导出当前不产出，硬配会变成「只做了一部分」
_CHAT_FORMATS = ["html", "excel"]
#: 只吃上传表的两件事。批次判定读的是内置 DEMO，附表时不能放进来（会拿假数据冒充你的表）
_FILE_TASKS = ("grain_daily_report", "quality_record_audit")


def _pretty_name(p) -> str:
    """上传时加了时间戳前缀防重名，显示时去掉，别让人看见一串数字。"""
    return re.sub(r"^\d{8}-\d{6}_", "", Path(p).name)


def _safe_inputs(raw: list) -> tuple[list[str], str]:
    """把前端传来的路径收敛到上传目录内，越界一律拒。"""
    out: list[str] = []
    for item in raw or []:
        p = Path(str(item)).expanduser()
        p = (p if p.is_absolute() else (_REPO_ROOT / p)).resolve()
        try:
            p.relative_to(_UPLOAD_ROOT)
        except ValueError:
            return [], f"只接受通过「＋ 表格」上传的文件，这个路径不在上传目录里：{p.name}"
        if p.suffix.lower() not in _INPUT_EXT:
            return [], f"只收 Excel / CSV，你给的是 {p.suffix or '(无扩展名)'}"
        if not p.is_file():
            return [], f"文件已经不在了：{p.name}"
        out.append(str(p))
    return out, ""


@client_bp.post("/api/client/task")
def client_task_start():
    """一句话 + 若干张表 → 白名单任务。识别不了就回候选，不硬猜。"""
    from . import intent
    from . import task_web
    from .tasks.registry import TASKS
    from .tasks.request import TaskRequest

    data = request.get_json(silent=True) or {}
    question = str(data.get("question") or "").strip()[:300]
    inputs, err = _safe_inputs(data.get("files") or [])
    if err:
        return jsonify({"ok": False, "error": err}), 400
    if not inputs:
        return jsonify({"ok": False, "error": "还没收到表格"}), 400

    pick = str(data.get("task_id") or "").strip()
    if pick:
        if pick not in _FILE_TASKS:
            return jsonify({"ok": False, "error": f"这个任务不吃上传的表：{pick}"}), 400
        got = {"ok": True, "task_id": pick, "params": {"inputs": inputs, "router": "pick"}}
    else:
        got = intent.understand_task(question, inputs)
        if got.get("ok") and got.get("task_id") not in _FILE_TASKS:
            return jsonify({
                "ok": True, "need": "choose",
                "reason": ("「逐项判定」现在读的是内置 DEMO 假批次，不会用你上传的表——"
                           "拿假数据答你的表，比答不出来更糟。要判表里的批次，用「出验收日报」，"
                           "它按批逐项判，结论一样带出处。"),
                "candidates": [c for c in intent.understand_task("", [])["candidates"]
                               if c["task_id"] in _FILE_TASKS],
                "files": [_pretty_name(p) for p in inputs],
            })
        if not got.get("ok"):
            return jsonify({
                "ok": True, "need": "choose",
                "reason": got.get("reason") or "没看明白要做什么，选一个：",
                "candidates": [c for c in (got.get("candidates") or [])
                               if c["task_id"] in _FILE_TASKS],
                "files": [_pretty_name(p) for p in inputs],
            })

    task_id = got["task_id"]
    # 日期从表里读，读不到才用今天。让使用人填日期 = 填错就出一份空报告，且他不知道怪谁
    rows, data_date = task_web._peek_table(Path(inputs[0]))
    report_date = data_date or date.today().isoformat()
    try:
        req = TaskRequest(task_id=task_id, inputs=inputs, report_date=report_date,
                          formats=list(_CHAT_FORMATS), output_root="runs", push=False)
        req.validate(TASKS.keys())
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}), 400

    run_id = task_web.start_background(req)
    return jsonify({
        "ok": True, "run_id": run_id, "task_id": task_id,
        "task_name": TASKS[task_id].name,
        "report_date": report_date,
        "date_from": ("表里的日期" if data_date else "表里没读到日期，按今天算"),
        "rows": rows,
        "inputs": [_pretty_name(p) for p in inputs],
        "say": (f"读到你给的 {_pretty_name(inputs[0])}（{rows} 行，数据日期 {report_date}），"
                f"按「{TASKS[task_id].name}」开始做。"),
    })


@client_bp.get("/api/client/task/<run_id>")
def client_task_status(run_id: str):
    """轮询一张任务卡的全部内容。前端只渲染，不再自己判业务。"""
    from . import task_web
    from .tasks.runner import TERMINAL

    d = task_web._status(run_id)
    if not d:
        return jsonify({"ok": False, "error": "没找到这次执行"}), 404
    view = task_web._build_view(d, run_id)
    return jsonify({"ok": True, "view": view, "terminal": d.get("status") in TERMINAL,
                    "run_id": run_id})


@client_bp.get("/api/client/state")
def client_state():
    """前端初始化用：真实的服务端状态，避免再挂一个假标签。"""
    from . import flags
    return jsonify({"ok": True, "llm": flags.llm_state(),
                    "modes": [{"key": k, "label": v} for k, v in
                              (("auto", "自动"), ("judge", "判定模式"),
                               ("chat", "问答模式"), ("lookup", "只查标准"))]})


@client_bp.get("/api/client/knowledge")
def client_knowledge():
    return jsonify(kb.overview())


@client_bp.get("/api/client/record/<batch_id>")
def client_record(batch_id: str):
    """只读查看判定链实际使用的 DEMO 原始记录。

    指路用：让「翻原始记录核对」有个真能点开的地方，不改变任何源数据。
    """
    bid = str(batch_id or "").strip().upper()
    if not bid.startswith("DEMO-"):
        return jsonify({"ok": False, "error": "这里只提供 DEMO 假批次的只读查看"}), 400
    try:
        from . import harness
        rows = harness._demo_records(bid)
    except Exception as e:
        return jsonify({"ok": False, "error": f"读取失败（{type(e).__name__}）"}), 500
    if not rows:
        return jsonify({"ok": False, "error": f"没有 {bid} 的演示记录"}), 404
    return jsonify({
        "ok": True, "batch_id": bid, "readonly": True,
        "source": "qc_daily/harness.py → _DEMO_PLAN（判定链唯一读取的原始数据）",
        "rows": rows,
        "note": "注意：samples/稻谷验收_假数据.csv 和 qc_source.db 里也有同名批次，"
                "那是另一份演练数据（水分写的是 1.45），不要和判定链这份混用。",
    })
