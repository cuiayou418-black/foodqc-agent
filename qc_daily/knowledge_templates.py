"""知识库管理页 · 单页模板

查看现有库、上传文件、成功/失败反馈、确认纳入，全部在同一界面完成。
用 webtemplates.page() 包一层，保证顶栏导航与现有 Web 应用一致。
"""
from __future__ import annotations

from . import webtemplates as tpl

_BODY = """
<div style="font-size:12px;color:#7b828b;margin-bottom:10px">
  Agent 设置 › <b style="color:#1f2328">知识库 / 记忆</b>
  &nbsp;·&nbsp; <a href="/app" style="color:#185fa5;text-decoration:none">返回对话客户端 →</a>
  &nbsp;·&nbsp; 这里只管理 Agent 能看到的资料，不改动现有业务功能
</div>

<div style="background:#fff6e5;border-left:4px solid #ba7517;border-radius:8px;padding:14px 18px;margin-bottom:16px;font-size:14px;line-height:1.7">
  <b>⚠️ 安全边界（先读这一条）</b><br>
  本页只管理知识库文件，<strong>绝不改写</strong> <code>limits.json</code> / <code>chunks.jsonl</code> / <code>corpus/*.md</code>。<br>
  上传原件落在 <code>corpus/_inbox/</code>，整个目录已被 <code>.gitignore</code> 排除，<strong>连 git status 都看不见</strong>。<br>
  <span style="color:#a32d2d"><strong>公司内部材料仅本机暂存，禁止外传、禁止 push 到公开 GitHub。</strong>
  另外 <code>python -m qc_daily web</code> 默认监听 0.0.0.0（同网段可访问），上传公司材料时请改用
  <code>--host 127.0.0.1</code>。</span>
</div>

<div id="kb-stats" style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px"></div>

<div style="background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:18px;margin-bottom:16px">
  <div style="font-size:16px;font-weight:600;margin-bottom:10px">上传本地文件到知识库</div>
  <form id="kb-form" style="display:flex;gap:12px;flex-wrap:wrap;align-items:center">
    <input type="file" id="kb-file" name="file"
           accept=".md,.txt,.pdf,.csv,.json,.xlsx" style="max-width:340px">
    <label style="font-size:14px"><input type="radio" name="source" value="public" checked> 公开标准 / 自写笔记</label>
    <label style="font-size:14px;color:#a32d2d"><input type="radio" name="source" value="private"> 公司内部材料（禁止外传）</label>
    <button type="submit" id="kb-submit" style="background:#185fa5;color:#fff;border:0;border-radius:6px;padding:9px 18px;cursor:pointer;font-size:14px">上传并加入待核对区</button>
  </form>
  <div id="kb-private-warn" style="display:none;background:#fdf0f0;border-left:4px solid #a32d2d;padding:10px 14px;margin-top:12px;font-size:13px;color:#a32d2d">
    ⚠️ 该文件将存入独立隔离目录 <code>corpus/_inbox/private/</code>，仅在索引里登记文件名与条目数，不进 git、不进公开仓库、不参与自动判定。请确认已获准在本机暂存。
  </div>
  <div id="kb-msg" style="margin-top:12px"></div>
  <div style="font-size:12px;color:#7b828b;margin-top:10px">
    支持 .md / .txt / .pdf / .csv / .json / .xlsx，单文件 ≤ 20MB。.docx 请另存为 .md 或 .pdf 后上传。
  </div>
</div>

<div style="background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:18px;margin-bottom:16px">
  <div style="font-size:16px;font-weight:600;margin-bottom:4px">已纳入的知识库（只读）</div>
  <div style="font-size:12px;color:#7b828b;margin-bottom:10px">来自 corpus/ 与 gb-bot/，展示文件、条目数与基本信息；本页不会修改它们。</div>
  <div style="overflow-x:auto"><table id="kb-builtin" style="width:100%;border-collapse:collapse;font-size:13px"></table></div>
</div>

<div style="background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:18px;margin-bottom:16px">
  <div id="kb-inbox-section" style="font-size:16px;font-weight:600;margin-bottom:4px">待核对区（上传后立刻显示在这里）</div>
  <div style="font-size:12px;color:#7b828b;margin-bottom:10px">
    新上传的文件状态为「待核对」，<strong>不参与检索与判定</strong>。人工核对后点「确认纳入」；
    该动作只更新状态，限值仍需另行逐格核对才能写入判定链。
  </div>
  <div style="overflow-x:auto"><table id="kb-inbox" style="width:100%;border-collapse:collapse;font-size:13px"></table></div>
</div>

<div style="background:#eaf3fc;border-left:4px solid #185fa5;border-radius:8px;padding:14px 18px;font-size:13px;line-height:1.7">
  <b>为什么要有「待核对」这一步？</b><br>
  corpus/README.md 的铁律是「所有数字必须从国标 PDF 原文核对，读不准就不写」。
  如果上传一份没人核对过的文件就直接进检索库，等于<strong>给幻觉发了一张出处凭证</strong>。
  所以这里把「上传」和「生效」拆开：上传只做保存、抽取文本、切块预览；生效必须有人点确认。
</div>
"""

_HEAD = """
<thead><tr style="background:#f0f3f6;text-align:left">
<th style="padding:8px 10px;border-bottom:1px solid #e3e6ea">名称</th>
<th style="padding:8px 10px;border-bottom:1px solid #e3e6ea">类型 / 来源</th>
<th style="padding:8px 10px;border-bottom:1px solid #e3e6ea">条目数</th>
<th style="padding:8px 10px;border-bottom:1px solid #e3e6ea">大小</th>
<th style="padding:8px 10px;border-bottom:1px solid #e3e6ea">状态</th>
<th style="padding:8px 10px;border-bottom:1px solid #e3e6ea">更新时间</th>
<th style="padding:8px 10px;border-bottom:1px solid #e3e6ea">备注 / 操作</th>
</tr></thead>
"""

_JS = """
function kbEsc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function kbSize(n){n=Number(n||0);return n<1024?n+' B':n<1048576?(n/1024).toFixed(1)+' KB':(n/1048576).toFixed(1)+' MB';}
function kbMsg(text,kind){
  var colors={ok:['#e9f6f0','#0f6e56'],warn:['#fff6e5','#8a5a06'],err:['#fdf0f0','#a32d2d']};
  var c=colors[kind]||colors.ok;
  document.getElementById('kb-msg').innerHTML='<div style="background:'+c[0]+';color:'+c[1]+
    ';border-left:4px solid '+c[1]+';border-radius:6px;padding:10px 14px;font-size:14px">'+kbEsc(text)+'</div>';
}
function kbBadge(s){
  var pending=String(s).indexOf('待核对')>=0;
  var bg=pending?'#fff6e5':'#e9f6f0',fg=pending?'#8a5a06':'#0f6e56';
  return '<span style="background:'+bg+';color:'+fg+';padding:2px 8px;border-radius:12px;font-size:12px;white-space:nowrap">'+kbEsc(s)+'</span>';
}
function kbRow(r,withOps){
  var warn=r.warning?'<div style="color:#a32d2d;font-size:12px;margin-top:3px">'+kbEsc(r.warning)+'</div>':'';
  var ops='';
  if(withOps){
    ops='<button onclick="kbPreview(\\''+r.id+'\\')" style="border:1px solid #cbd5e1;background:#fff;border-radius:5px;padding:3px 9px;cursor:pointer;font-size:12px;margin-right:5px">预览</button>';
    if(r.status.indexOf('待核对')>=0){
      ops+='<button onclick="kbConfirm(\\''+r.id+'\\')" style="border:0;background:#0f6e56;color:#fff;border-radius:5px;padding:4px 9px;cursor:pointer;font-size:12px">确认纳入</button>';
    }else{ops+='<span style="font-size:12px;color:#7b828b">已确认</span>';}
  }
  var originStyle=r.private?'color:#a32d2d;font-weight:600':'';
  return '<tr style="border-bottom:1px solid #eef1f4">'+
    '<td style="padding:8px 10px"><b>'+kbEsc(r.name)+'</b>'+warn+'</td>'+
    '<td style="padding:8px 10px;'+originStyle+'">'+kbEsc(r.kind)+'<br><span style="font-size:12px">'+kbEsc(r.origin)+'</span></td>'+
    '<td style="padding:8px 10px">'+kbEsc(r.entries)+'</td>'+
    '<td style="padding:8px 10px">'+kbSize(r.size)+'</td>'+
    '<td style="padding:8px 10px">'+kbBadge(r.status)+'</td>'+
    '<td style="padding:8px 10px">'+kbEsc(r.mtime)+'</td>'+
    '<td style="padding:8px 10px"><span style="font-size:12px;color:#4a5058">'+kbEsc(r.note)+'</span><div style="margin-top:5px">'+ops+'</div></td>'+
  '</tr>';
}
function kbRender(data){
  var s=data.stats||{};
  document.getElementById('kb-stats').innerHTML=
    '<div style="flex:1;min-width:150px;background:#fff;border:1px solid #e3e6ea;border-radius:9px;padding:12px 14px"><div style="font-size:12px;color:#7b828b">已纳入文件</div><div style="font-size:22px;font-weight:600">'+kbEsc(s.builtin_files)+'</div></div>'+
    '<div style="flex:1;min-width:150px;background:#fff;border:1px solid #e3e6ea;border-radius:9px;padding:12px 14px"><div style="font-size:12px;color:#7b828b">知识库条目</div><div style="font-size:22px;font-weight:600">'+kbEsc(s.builtin_entries)+'</div></div>'+
    '<div style="flex:1;min-width:150px;background:#fff;border:1px solid #e3e6ea;border-radius:9px;padding:12px 14px"><div style="font-size:12px;color:#7b828b">待核对上传</div><div style="font-size:22px;font-weight:600;color:#8a5a06">'+kbEsc(s.pending)+'</div></div>'+
    '<div style="flex:1;min-width:150px;background:#fff;border:1px solid #e3e6ea;border-radius:9px;padding:12px 14px"><div style="font-size:12px;color:#7b828b">公司内部材料</div><div style="font-size:22px;font-weight:600;color:#a32d2d">'+kbEsc(s.private)+'</div></div>';
  var b=data.builtin||[];
  document.getElementById('kb-builtin').innerHTML=kbHead()+'<tbody>'+
    (b.length?b.map(function(r){return kbRow(r,false);}).join(''):'<tr><td colspan="7" style="padding:14px;color:#7b828b">暂无文件</td></tr>')+'</tbody>';
  var q=data.inbox||[];
  document.getElementById('kb-inbox').innerHTML=kbHead()+'<tbody>'+
    (q.length?q.map(function(r){return kbRow(r,true);}).join(''):'<tr><td colspan="7" style="padding:14px;color:#7b828b">还没有上传过文件。上传后这里会立刻出现新条目。</td></tr>')+'</tbody>';
}
function kbHead(){return __KB_HEAD__;}
async function kbLoad(){
  try{
    var r=await fetch('/api/knowledge/overview');
    if(!r.ok)throw new Error('HTTP '+r.status);
    kbRender(await r.json());
  }catch(e){kbMsg('读取知识库失败：'+e.message,'err');}
}
async function kbPreview(id){
  try{
    var r=await fetch('/api/knowledge/preview/'+id);
    var d=await r.json();
    if(!r.ok)throw new Error(d.error||'HTTP '+r.status);
    alert('前 3 个条目预览：\\n\\n'+(d.text||'（无可预览文本）'));
  }catch(e){kbMsg('预览失败：'+e.message,'err');}
}
async function kbConfirm(id){
  try{
    var r=await fetch('/api/knowledge/confirm/'+id,{method:'POST'});
    var d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||'HTTP '+r.status);
    kbMsg('已确认纳入：'+d.record.name+'。注意：仅标记为已核对，限值仍需人工核对后才会进判定链。','ok');
    kbRender(d.overview);
  }catch(e){kbMsg('确认失败：'+e.message,'err');}
}
document.getElementById('kb-form').addEventListener('submit',async function(e){
  e.preventDefault();
  var f=document.getElementById('kb-file').files[0];
  if(!f){kbMsg('请先选择要上传的文件','err');return;}
  var src=document.querySelector('input[name=source]:checked').value;
  var fd=new FormData();fd.append('file',f);fd.append('source',src);
  var btn=document.getElementById('kb-submit');btn.disabled=true;btn.textContent='上传中…';
  kbMsg('正在上传并解析：'+f.name,'warn');
  try{
    var r=await fetch('/api/knowledge/upload',{method:'POST',body:fd});
    var d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||'HTTP '+r.status);
    var extra=d.warning?'（注意：'+d.warning+'）':'';
    kbMsg('上传成功：'+d.record.name+'，切出 '+d.record.entries+' 个条目，已同步显示在下方待核对区。'+extra,
          d.warning?'warn':'ok');
    document.getElementById('kb-form').reset();
    document.getElementById('kb-private-warn').style.display='none';
    kbRender(d.overview);
  }catch(err){kbMsg('上传失败：'+err.message,'err');}
  finally{btn.disabled=false;btn.textContent='上传并加入待核对区';}
});
document.querySelectorAll('input[name=source]').forEach(function(el){
  el.addEventListener('change',function(){
    document.getElementById('kb-private-warn').style.display=
      document.querySelector('input[name=source]:checked').value==='private'?'block':'none';
  });
});
kbLoad();
"""


def knowledge_page() -> str:
    import json
    js = _JS.replace("__KB_HEAD__", json.dumps(_HEAD, ensure_ascii=False))
    return tpl.page("知识库管理", "knowledge", _BODY,
                    meta="知识库 · 只读现有库 + 本地待核对区", extra_js=js)
