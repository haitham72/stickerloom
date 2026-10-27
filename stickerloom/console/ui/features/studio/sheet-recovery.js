/* G3's rule-10 decision, shared by the Studio, pictures and chat. The server's allow block owns eligibility. */
const SR=(()=>{
  const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const info=g=>(g.allow&&g.allow.video_sheet)||{can:[],allowed:[],undo:[],why:{},final:{}};
  const button=(g,aid,allow,all=false)=>`<button class="btn sm${allow?' pri':''}" data-act=ssallow data-g="${esc(g.number||g.generation)}" data-sheet="${esc(aid||'')}" data-all=${all?1:0} data-allow=${allow?1:0}>${all?(allow?'Use all anyway':'Take all back')+' ('+(allow?info(g).can.length:info(g).undo.length)+')':allow?'Use it anyway':'Take it back'}</button>`;
  function controls(g,v){const a=info(g),id=v.id,can=a.can.includes(id),undo=a.undo.includes(id),allowed=a.allowed.includes(id),why=a.why[id]||a.final[id];
    if(!why&&!allowed)return '';
    const warnings=[...(v.verify||[]),...(v.video_checks||[])].filter(c=>!c.ok&&c.severity==='WARN');
    return `<div class=sheet-recovery><span>${esc(why||(allowed?'Allowed by you':'Blocked'))}</span>${can?button(g,id,true):''}${undo?button(g,id,false):''}${warnings.map(c=>`<small>WARN · ${esc(c.detail||c.name)}</small>`).join('')}${a.final[id]?`<small>${esc(a.final[id])}</small>`:''}</div>`;}
  function picture(g,v){const a=info(g),blocked=!!a.why[v.id],url=v.picture||`/out/${g.generation_id||g.generation}/${v.file}`;
    return `<div class="sheet-picture${blocked?' blocked':''}"><img src="${esc(url)}" alt="Video sheet ${esc(v.id)}">${controls(g,v)}</div>`;}
  function bulk(g){const a=info(g);return a.can.length||a.undo.length?`<div class=lv-allow>${a.can.length?button(g,'',true,true):''}${a.undo.length?button(g,'',false,true):''}</div>`:'';}
  return {controls,picture,bulk};
})();
if(typeof module!=='undefined')module.exports=SR;
if(typeof ACT!=='undefined')ACT.ssallow=async el=>{
  const gid=+String(el.dataset.g||'').replace(/\D/g,''),allow=el.dataset.allow==='1';if(!gid)return;
  el.disabled=true;
  try{const body={kind:'video_sheet',allow,...(el.dataset.all==='1'?{all:true}:{sheet:el.dataset.sheet})},r=await post(`/api/generations/${gid}/allow`,body);
    if(!r.ok){toast(r.j.error||'Could not change the sheet',1);return;}
    toast(allow?'Video sheet allowed · free':'Video-sheet permission taken back');
    if(typeof glast!=='undefined')glast='';if(typeof tick==='function')await tick(true);
    if(typeof A!=='undefined'&&A.sid)await loadSession(A.sid,true);if(typeof startPoll==='function')startPoll();
  }finally{el.disabled=false;}
};
