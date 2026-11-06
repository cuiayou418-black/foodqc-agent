"""Agent 工作台 · 对话式演示页

形态对标 FateMaster / Codex：提一句话 → 看它自己选工具 → 逐步展开结果 → 出结论。
步骤来自 qa.run_qa() 的真实输出（捕获 stdout 后解析），不是模拟动画。
"""
from __future__ import annotations

from . import webtemplates as tpl

_BODY = """
<div style="background:#eaf3fc;border-left:4px solid #185fa5;border-radius:8px;padding:14px 18px;margin-bottom:16px;font-size:14px;line-height:1.7">
  <b>Agent 工作台 · 像算命 Agent 那样，只管提一句</b><br>
  你输入一句话，系统<strong>自己决定调哪个工具</strong>，过程逐步展开，最后出带出处的结论。<br>
  <strong>判定权在代码</strong>：模型（若开启）只是解释层；数字由 <code>models.compare()</code> 算，查不到限值就拒答，安全项强制转人工。<br>
  <span style="color:#a32d2d">⚠️ 演示只用 <code>DEMO-</code> 假数据；结论仅供复核，<strong>不得作为放行依据</strong>；本页不发邮件、不改源数据。</span>
</div>

<div style="background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:18px;margin-bottom:16px">
  <div style="font-size:16px;font-weight:600;margin-bottom:8px">提一个问题</div>
  <textarea id="ag-q" rows="3" placeholder="例如：DEMO-001 这批稻谷出糙率 76.5 能收吗"
            style="width:100%;box-sizing:border-box;border:1px solid #cbd5e1;border-radius:6px;padding:10px;font-size:14px;font-family:inherit"></textarea>
  <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px">
    <button id="ag-run" style="background:#185fa5;color:#fff;border:0;border-radius:6px;padding:9px 20px;cursor:pointer;font-size:14px">运行</button>
    <span style="font-size:12px;color:#7b828b">快捷提问：</span>
    <button class="ag-quick" style="border:1px solid #cbd5e1;background:#fff;border-radius:14px;padding:5px 11px;cursor:pointer;font-size:12px">DEMO-001 这批稻谷出糙率 76.5 能收吗</button>
    <button class="ag-quick" style="border:1px solid #cbd5e1;background:#fff;border-radius:14px;padding:5px 11px;cursor:pointer;font-size:12px">出糙率 3 等限值是多少</button>
    <button class="ag-quick" style="border:1px solid #cbd5e1;background:#fff;border-radius:14px;padding:5px 11px;cursor:pointer;font-size:12px">今天有哪些录入疑点要复核</button>
    <button class="ag-quick" style="border:1px solid #cbd5e1;background:#fff;border-radius:14px;padding:5px 11px;cursor:pointer;font-size:12px">给我今天的日报摘要</button>
  </div>
  <div id="ag-status" style="margin-top:10px"></div>
</div>

<div style="background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:18px;margin-bottom:16px">
  <div style="font-size:16px;font-weight:600;margin-bottom:4px">执行过程</div>
  <div style="font-size:12px;color:#7b828b;margin-bottom:10px">
    下面每一步都是 <code>qa.run_qa()</code> 真实调用 <code>TOOLS_IMPL</code> 的输出，不是模拟。<span id="ag-mode"></span>
  </div>
  <div id="ag-steps" style="font-size:13px;color:#7b828b">还没有运行。输入一个问题，或点上面的快捷提问。</div>
</div>

<div style="background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:18px">
  <div style="font-size:16px;font-weight:600;margin-bottom:10px">代码结论</div>
  <div id="ag-conclusion" style="font-size:13px;color:#7b828b">运行后这里显示结论。</div>
</div>
"""

_JS = """
function agEsc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function agStatus(text,kind){
  var c=kind==='err'?['#fdf0f0','#a32d2d']:kind==='run'?['#fff6e5','#8a5a06']:['#e9f6f0','#0f6e56'];
  document.getElementById('ag-status').innerHTML='<div style="background:'+c[0]+';color:'+c[1]+
    ';border-left:4px solid '+c[1]+';border-radius:6px;padding:9px 13px;font-size:13px">'+agEsc(text)+'</div>';
}
function agStep(s,i){
  if(s.kind==='sep'){
    return '<div style="margin:14px 0 8px;padding-top:10px;border-top:1px solid #e3e6ea;font-weight:600;color:#4a5058">'+agEsc(s.title)+'</div>';
  }
  if(s.kind==='tool'){
    var res=s.result?s.result.trim():'';
    return '<div style="border-left:3px solid #185fa5;background:#f7f9fc;border-radius:0 6px 6px 0;padding:9px 12px;margin:8px 0">'+
      '<div style="font-family:Consolas,monospace;font-size:12.5px;color:#0c447c"><span style="color:#7b828b">'+agEsc(i)+'.</span> '+agEsc(s.title)+'</div>'+
      (res?'<pre style="white-space:pre-wrap;word-break:break-all;margin:7px 0 0;font-size:12px;color:#1f2328;background:#fff;border:1px solid #e3e6ea;border-radius:5px;padding:8px 10px;max-height:190px;overflow:auto">'+agEsc(res)+'</pre>':'')+
    '</div>';
  }
  var warn=String(s.title).indexOf('拦截')>=0||String(s.title).indexOf('退回')>=0;
  return '<div style="padding:4px 0 4px 12px;border-left:3px solid '+(warn?'#ba7517':'#cbd5e1')+
    ';margin:5px 0;color:'+(warn?'#8a5a06':'#4a5058')+';font-size:12.5px">'+agEsc(s.title)+'</div>';
}
function agRender(d){
  var steps=d.steps||[];
  document.getElementById('ag-mode').innerHTML=d.llm
    ?'<span style="color:#8a5a06">当前 QC_USE_LLM 开启：模型可选工具，但判定仍在代码。</span>'
    :'<span style="color:#0f6e56">当前 QC_USE_LLM 关闭（默认）：纯代码路线，没 key / 断网也能跑。</span>';
  document.getElementById('ag-steps').innerHTML=steps.length
    ?steps.map(function(s,i){return agStep(s,i+1);}).join('')
    :'<span style="color:#7b828b">这次没有解析出工具步骤，请看下方结论。</span>';
  var c=d.conclusion||[];
  document.getElementById('ag-conclusion').innerHTML=
    '<div style="background:#fdf0f0;border-left:4px solid #a32d2d;border-radius:6px;padding:11px 14px;color:#a32d2d;font-size:13px;line-height:1.7">'+
    (c.length?c.map(function(x){return agEsc(x);}).join('<br>'):'（本次未产出代码结论）')+'</div>';
}
async function agRun(q){
  q=String(q||'').trim();
  if(!q){agStatus('请先输入一个问题','err');return;}
  if(q.length>300){agStatus('问题太长，请控制在 300 字以内','err');return;}
  var btn=document.getElementById('ag-run');btn.disabled=true;btn.textContent='运行中…';
  agStatus('正在跑 Workflow 主干 + 工具调用：'+q,'run');
  try{
    var r=await fetch('/api/agent/ask',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({question:q})});
    var d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||'HTTP '+r.status);
    agRender(d);
    agStatus('完成：'+d.steps.length+' 个步骤，'+d.raw_lines+' 行真实输出。结论仅供复核，不得作为放行依据。','ok');
  }catch(e){agStatus('运行失败：'+e.message,'err');}
  finally{btn.disabled=false;btn.textContent='运行';}
}
document.getElementById('ag-run').addEventListener('click',function(){
  agRun(document.getElementById('ag-q').value);});
document.getElementById('ag-q').addEventListener('keydown',function(e){
  if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){agRun(this.value);}});
document.querySelectorAll('.ag-quick').forEach(function(b){
  b.addEventListener('click',function(){
    document.getElementById('ag-q').value=this.textContent.trim();agRun(this.textContent.trim());});
});
"""


def agent_page() -> str:
    return tpl.page("Agent 工作台", "agent", _BODY,
                    meta="提一句 · 看它自己调工具 · 出带出处的结论", extra_js=_JS)
