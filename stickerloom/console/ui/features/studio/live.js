/* Live generation (Higgsfield): the credits chip, the style tiles, the model selector, the "make it" confirmation with its price,
   and the cards that follow a running sheet / Kling job. Everything here calls a real endpoint (/api/higgsfield, /api/models, /api/live/*);
   the engine and the review gates are unchanged: a finished sheet runs through the same stills run, a video only starts from an approved video sheet. */
'use strict';
const LIVE={hf:null,m:null,style:'flat_vector',grid:'2x2',img:{id:'',options:{}},vid:{id:'',options:{}},jobs:[],est:{},loop:false,fill:null,q:[],typ:{},dis:[]};
try{const s=JSON.parse(localStorage.getItem('stickerloom.live')||'null');if(s){LIVE.style=s.style||LIVE.style;LIVE.img=s.img||LIVE.img;LIVE.vid=s.vid||LIVE.vid;LIVE.jobs=Array.isArray(s.jobs)?s.jobs:[];LIVE.loop=!!s.loop;LIVE.grid=s.grid==='3x3'?'3x3':'2x2';LIVE.fill=typeof s.fill==='number'?s.fill:null;LIVE.dis=Array.isArray(s.dis)?s.dis:[]}}catch(e){}
/* New sheets default to 2x2; saved explicit 3x3 choices remain available. */
const cpGrid=()=>(typeof LIVE!=='undefined'&&LIVE.grid==='3x3')?'3x3':'2x2';
const lsave=()=>gstore('stickerloom.live',JSON.stringify({style:LIVE.style,img:LIVE.img,vid:LIVE.vid,jobs:LIVE.jobs,loop:LIVE.loop,grid:LIVE.grid,fill:LIVE.fill,dis:LIVE.dis.slice(-60)}));
const fcr=n=>n==null?'?':(+n).toLocaleString(undefined,{maximumFractionDigits:2});
const lkind=k=>k==='image'||k==='video'?k:(k==='sheet'||k==='single'?'image':'video');

/* ---------- models */
function lfind(kind,id){const m=LIVE.m;if(!m)return null;return(m[kind]||[]).find(x=>x.id===id)||((m.more||{})[kind]||[]).find(x=>x.id===id)||null}
function lsel(kind){const key=kind==='image'?'img':'vid',cur=LIVE[key];let m=lfind(kind,cur.id);
  if(!m&&LIVE.m){m=lfind(kind,LIVE.m.defaults[kind]);cur.id=m?m.id:'';cur.options={}}
  if(m){const o={};for(const op of m.options)o[op.name]=(cur.options||{})[op.name]&&op.choices.includes(String(cur.options[op.name]))?String(cur.options[op.name]):op.default;cur.options=o}
  return{model:m,sel:cur}}
const logoHtml=(m,cls='')=>`<span class="lv-logo mono ${cls}" title="${esc(m&&(m.provider||m.label)||'')}">${esc((m&&m.label||'?').slice(0,1))}</span>`
const optSummary=(m,sel)=>m?m.options.map(o=>sel.options[o.name]!=null?(o.name==='duration'?sel.options[o.name]+' s':sel.options[o.name]):'').filter(Boolean).join(' · '):'';

/* ---------- credits chip (bottom of the rail): the provider's balance, never an account balance */
async function refreshHf(){const r=await api('/api/higgsfield');if(r.ok){const was=LIVE.hf&&!LIVE.hf.error&&LIVE.hf.available;LIVE.hf=r.j;drawChip();if(!!was!==!!(r.j&&!r.j.error&&r.j.available)&&typeof composerDraw==='function')composerDraw()}}
function drawChip(){if(typeof cpDrawTop==='function')cpDrawTop();const rail=$('rail');if(!rail)return;let b=document.getElementById('lvchip');if(!b){b=document.createElement('button');b.id='lvchip';b.className='lv-chip';rail.appendChild(b)}
  const h=LIVE.hf;b.title=!h?'Higgsfield':h.error?h.error:h.provider==='fake'?'Fake provider: free offline renders, no credits.':`${fcr(h.credits)} credits left · ${fcr(h.spent_today)} spent today · ${h.plan||''} plan.`;
  b.className='lv-chip'+(h&&(h.error||h.available===false)?' off':'');
  b.innerHTML=!h?'<b>…</b><small>credits</small>':h.available===false?'<b>off</b><small>Higgsfield</small>':h.error?'<b>!</b><small>Higgsfield</small>':h.provider==='fake'?'<b>fake</b><small>provider</small>':`<b>${h.credits>=100?Math.round(h.credits).toLocaleString():fcr(Math.round(h.credits*10)/10)}</b><small>credits</small>`}
const _drawRail=drawRail;drawRail=function(){_drawRail();drawChip()};
/* ---------- styles + models panel under the request box */
function livePanel(){const g=document.querySelector('.gen2');if(!g||document.getElementById('lvpanel')||!LIVE.m)return;
  const opts=document.querySelector('.gopts'),el=document.createElement('div');el.id='lvpanel';(opts?opts.insertAdjacentElement('afterend',el):g.appendChild(el));drawPanel()}
function drawPanel(){const el=document.getElementById('lvpanel');if(!el||!LIVE.m)return;
  const im=lsel('image'),vi=lsel('video');
  el.innerHTML=`<div class=lv-row><span class=mut>Style</span><div class=lv-styles>${LIVE.m.styles.map(s=>`<button class="lv-style ${LIVE.style===s.id?'on':''}" data-act=lstyle data-id="${esc(s.id)}" title="${esc(s.hint)}"><img src="/assets/styles/${esc(s.id)}" alt="" loading=lazy><span>${esc(s.label)}</span></button>`).join('')}</div></div>
   <div class=lv-row><span class=mut>Models</span><button class="lv-model" data-act=lmodels>${logoHtml(im.model)}<span><small>Image</small><b>${esc(im.model?im.model.label:'?')}</b><em>${esc(optSummary(im.model,im.sel))}</em></span></button>
    <button class="lv-model" data-act=lmodels>${logoHtml(vi.model)}<span><small>Animation</small><b>${esc(vi.model?vi.model.label:'?')}</b><em>${esc(optSummary(vi.model,vi.sel))}</em></span></button>
    <span class=mut id=lvhint>${LIVE.hf&&LIVE.hf.available===false?'Higgsfield is not installed: only prepared sheets can be used.':'Used when no prepared sheet matches the request.'}</span></div>`}
ACT.lstyle=el=>{LIVE.style=el.dataset.id;lsave();drawPanel();if(typeof composerDraw==='function')composerDraw()};

/* ---------- the model dialog */
let LMD=null;
ACT.lmodels=()=>{LMD={};drawModels()};
function modelCard(kind,m,on){return`<button class="lv-mcard ${on?'on':''}" data-act=lmpick data-k=${kind} data-id="${esc(m.id)}">${logoHtml(m)}<span><b>${esc(m.label)}</b>${m.note?`<small>${esc(m.note)}</small>`:''}</span></button>`}
function modelSection(kind,title){const {model,sel}=lsel(kind),more=(LIVE.m.more||{})[kind]||[],isMore=model&&more.some(x=>x.id===model.id);
  return`<h3>${title}</h3><div class=lv-mgrid>${LIVE.m[kind].map(m=>modelCard(kind,m,model&&m.id===model.id)).join('')}</div>
   ${model?`<div class=lv-mopts>${model.options.map(o=>o.choices.length<2?`<span class=mut>${esc(o.label)}: <b>${esc(o.choices[0])}</b></span>`:`<label>${esc(o.label)} <select data-lvopt="${kind}|${esc(o.name)}">${o.choices.map(c=>`<option value="${esc(c)}" ${sel.options[o.name]===c?'selected':''}>${esc(c)}</option>`).join('')}</select></label>`).join('')}
    <span class=mut id=lvest-${kind}></span></div>`:''}
   ${more.length?`<details ${isMore?'open':''}><summary class=mut>All Higgsfield ${kind} models (${more.length} more${LIVE.m.counts&&LIVE.m.counts[kind]?` of ${LIVE.m.counts[kind]}`:''})</summary>
     <select data-lvmore="${kind}"><option value="">Choose another model…</option>${more.map(m=>`<option value="${esc(m.id)}" ${model&&m.id===model.id?'selected':''}>${esc(m.label)} (${esc(m.id)})</option>`).join('')}</select></details>`:''}`}
function drawModels(){if(!LIVE.m)return;
  dlg(`<div class="vdlg lv-md"><h2>Models</h2><div class=mut>What makes the sheet and what animates it. The sheet's price is on its own line in the Prompt step, above Generate sheet.</div>
   ${modelSection('image','Images')}${modelSection('video','Animation')}
   <div class=row style="justify-content:flex-end"><button class="btn pri" data-act=lmdone>Done</button></div></div>`);
  for(const k of ['image','video'])lcost(k)}
ACT.lmpick=el=>{const key=el.dataset.k==='image'?'img':'vid';LIVE[key]={id:el.dataset.id,options:{}};lsave();drawModels()};
ACT.lmdone=()=>{drawPanel();if(typeof composerDraw==='function')composerDraw();closeDlg()};
document.addEventListener('change',e=>{const t=e.target;if(t.dataset&&t.dataset.lvvid!==undefined){      /* the marker has no value (data-lvvid=""): test that it is there, not that it is truthy */LIVE.vid={id:t.value,options:{}};lsave();lsel('video');glast='';if(typeof tick==='function')tick(true);return}
  if(t.dataset&&t.dataset.lvimg!==undefined){LIVE.img={id:t.value,options:{}};lsave();lsel('image');glast='';if(typeof tick==='function')tick(true);return}
  if(t.dataset&&t.dataset.lvopt){const [k,n]=t.dataset.lvopt.split('|'),key=k==='image'?'img':'vid';LIVE[key].options[n]=t.value;lsave();lcost(k).then(()=>fillPrices())}
  if(t.dataset&&t.dataset.lvmore&&t.value){const key=t.dataset.lvmore==='image'?'img':'vid';LIVE[key]={id:t.value,options:{}};lsave();drawModels()}});
async function lcost(kind,quiet){const {model,sel}=lsel(kind);if(!model)return null;const key=kind+model.id+JSON.stringify(sel.options);
  if(LIVE.est[key]===undefined){const r=await post('/api/live/cost',{kind,model:model.id,options:sel.options});LIVE.est[key]=r.ok?r.j.credits:null}
  const e=document.getElementById('lvest-'+kind);if(e&&!quiet)e.textContent=LIVE.est[key]==null?'price not available':`≈ ${fcr(LIVE.est[key])} credits per ${kind==='image'?'sheet':'clip'}`;
  return LIVE.est[key]}

/* ---------- run: no questions. The sheet's price is shown on its own line in the Prompt step (Generate prompt is free); the selection is the one made before; Generate sheet just starts */
const liveReadyNow=()=>!!(LIVE.hf&&LIVE.hf.available&&!LIVE.hf.error&&LIVE.m);
function liveOffer(prompt){if(!liveReadyNow())return false;liveStart('sheet',{prompt,ai:typeof aiOn==='function'&&aiOn(),refs:[]});return true}
async function liveStart(kind,ctx){const isSheet=kind==='sheet',im=lsel('image'),vi=lsel('video'),est=await lcost(isSheet?'image':'video',true);
  if(est!=null&&LIVE.hf&&LIVE.hf.credits!=null&&est>LIVE.hf.credits){toast(`Not enough credits: this costs ${fcr(est)} and ${fcr(LIVE.hf.credits)} are left`,1);return false}
  const body=isSheet?{prompt:ctx.prompt,grid:ctx.grid||cpGrid(),style_id:ctx.style_id||LIVE.style,ai:!!ctx.ai,outline:GS.outline,loop:ctx.loop===undefined?!!LIVE.loop:!!ctx.loop,model:im.sel.id,options:im.sel.options,refs:ctx.refs||[],
      ...(ctx.from_generation?{from_generation:ctx.from_generation}:{}),...(ctx.parent?{parent:ctx.parent}:{}),...(ctx.regen_of?{regen_of:ctx.regen_of}:{}),...(ctx.sheet_prompt?{sheet_prompt:ctx.sheet_prompt}:{}),...(ctx.plan?{plan:ctx.plan}:{})}      /* the Prompt tab: this batch's own plan, and the text the person wrote */
    :{kind:'video',generation:ctx.g,model:vi.sel.id,options:vi.sel.options,slot_fill:fillNow(),loop:!!LIVE.loop,...(ctx.video_prompt?{video_prompt:ctx.video_prompt}:{}),...(ctx.redo?{redo:true}:{}),...(egDirty()?{outline:egVals().o,erode:egVals().e}:{})};
  const r=await postWait(isSheet?'/api/live/sheet':'/api/live/video',body,'Finishing the previous step…',{'Idempotency-Key':ikey()});
  if(!r.ok){toast(r.j.error||'Could not start',1);return false}
  const m=lfind(isSheet?'image':'video',r.j.model);
  LIVE.jobs.push({...(ctx.mode?{mode:ctx.mode,batch:ctx.batch||null,run:ctx.run||null}:{}),id:r.j.job,kind:isSheet?'sheet':'video',label:isSheet?ctx.prompt:`Batch ${ctx.g}`,model:m?m.label:r.j.model,est:r.j.estimate,t0:Date.now(),gen:isSheet?null:ctx.g,ai:r.j.expanded_by==='ai'});
  if(isSheet&&ctx.ai&&r.j.expanded_by==='transformation')toast('Transformation: the built-in template wrote the cells, so the AI enhancer was not asked');
  else if(isSheet&&ctx.ai&&r.j.expanded_by!=='ai')toast(`The AI enhancer could not be used (${r.j.expand_error||'no answer'}): the built-in prompt was sent instead`,1);
  if(!isSheet&&egDirty())egClear();
  lsave();say('');drawLive();liveTick();refreshHf();return true}

/* the video job running for a batch right now (this page's own, or one the server's queue knows), else null */
function videoRun(g){const qj=(LIVE.q||[]).find(j=>j.kind==='video'&&String(j.generation)===g.generation_id&&QACTIVE.includes(j.status));
  return LIVE.jobs.find(j=>j.kind==='video'&&j.gen===g.number&&!j.error)||(qj&&{model:(lfind('video',qj.model)||{label:qj.model||'the model'}).label,t0:(qj.claimed_at||qj.created_at)*1000})||null}
/* The model picker folded into one line: the summary says what made the current
   result (`current`, from the batch), then the next model, its settings and the price; opening it shows
   the curated models and the settings that have a choice. The choice is the same one the Models dialog writes (LIVE.img / LIVE.vid), so it sticks. */
function modelPick(kind,current){if(!LIVE.m)return'';const cur=current?(lfind(kind,current)||{label:current}):null,{model,sel}=lsel(kind),attr=kind==='image'?'data-lvimg':'data-lvvid';
  const c=lcached(kind);if(c===undefined)lcost(kind,true).then(fillPrices);
  return`<span class=lv-mrow>${cur&&model&&cur.id!==model.id?`<span class=mut title="What made the current one">${esc(cur.label)} →</span>`:''}<select ${attr} aria-label="${kind==='image'?'Image':'Animation'} model" title="${esc(model&&model.note||'')}">${LIVE.m[kind].map(m=>`<option value="${esc(m.id)}" ${model&&m.id===model.id?'selected':''}>${esc(m.label)}</option>`).join('')}</select>
   ${model?model.options.filter(o=>o.choices.length>1).map(o=>`<select data-lvopt="${kind}|${esc(o.name)}" aria-label="${esc(o.label)}" title="${esc(o.label)}">${o.choices.map(x=>`<option value="${esc(x)}" ${sel.options[o.name]===x?'selected':''}>${esc(o.name==='duration'?x+' s':x)}</option>`).join('')}</select>`).join(''):''}</span>`}

/* ---------- the animation box under the green screen: a model drop-down and one priced button (no dialog, no confirmation) */
const lcached=kind=>{const {model,sel}=lsel(kind);return model?LIVE.est[kind+model.id+JSON.stringify(sel.options)]:undefined};
const fillNow=()=>LIVE.fill!=null?LIVE.fill:(LIVE.m&&LIVE.m.slot_fill)||0.74;
const gapPct=f=>Math.round((1-f)*100);
const previewUrl=(g,f)=>`/api/generations/${g.number}/sheet_preview?fill=${f.toFixed(3)}&px=520&k=${typeof keptStills==='function'?keptStills(g).map(t=>t.index).join(''):''}`;
let GPT=null;
document.addEventListener('input',e=>{const t=e.target;if(!(t.dataset&&t.dataset.lvgap!==undefined))return;
  LIVE.fill=1-(+t.value)/100;t.nextElementSibling.textContent=t.value+'%';clearTimeout(GPT);
  GPT=setTimeout(()=>{const g=GM.get(+t.dataset.g),box=t.closest('.gsheet,.pdfoot'),img=box&&box.querySelector('[data-lvprev]');if(g&&img)img.src=previewUrl(g,LIVE.fill)},120)});
document.addEventListener('change',e=>{const t=e.target;if(t.dataset&&t.dataset.lvgap!==undefined)lsave();
  if(t.dataset&&t.dataset.lvloop!==undefined){LIVE.loop=t.checked;lsave();if(typeof composerDraw==='function')composerDraw()}});
function fillPrices(){for(const kind of ['video','image']){const c=lcached(kind);document.querySelectorAll(`[data-lvprice=${kind}]`).forEach(e=>e.textContent=c==null?'':'◈ '+fcr(c));if(c===undefined&&document.querySelector(`[data-lvprice=${kind}]`))lcost(kind,true).then(()=>fillPrices())}}

/* ---------- running jobs */
/* The queue: every job the server knows, with its real state, so a long video can be followed (and survives a reload). Typical durations come from the ledger. */
const QACTIVE=['REQUESTED','CLAIMED'];
const mmss=sec=>{sec=Math.max(0,Math.round(sec));return Math.floor(sec/60)+':'+String(sec%60).padStart(2,'0')};
const hhmm=ts=>new Date(ts*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
function qRows(){const now=Date.now()/1000;
  return(LIVE.q||[]).filter(j=>!j.dismissed&&!LIVE.dis.includes(j.id)&&(QACTIVE.includes(j.status)||(j.completed_at||0)>now-900||['FAILED','TIMEOUT'].includes(j.status)))
    .sort((a,b)=>(b.created_at||0)-(a.created_at||0))}
function qState(j){const now=Date.now()/1000,typ=(LIVE.typ||{})[(j.kind==='video'?'video:':'image:')+j.model],started=j.claimed_at||j.created_at,el=(j.completed_at||now)-started,
    num=j.generation?+String(j.generation).replace(/\D/g,''):null,g=num?GM.get(num):null;
  if(j.status==='REQUESTED')return{t:'Waiting to start',pct:3,cls:'run',el};
  if(j.status==='CLAIMED'){const dl=j.stage==='downloading',pct=dl?96:typ?Math.min(94,5+el/typ*89):null;
    return{t:dl?'Downloading the result':'Higgsfield is working',pct,cls:'run',el,typ}}
  if(j.provider_check&&j.provider_check.classification==='DIVERGENCE')return{t:j.provider_check.message+(j.status==='DONE'?' Result recovered on the same ticket.':''),pct:100,cls:j.status==='DONE'?'done':'bad',el};
  if(j.status==='DONE'){
    if(j.kind==='video'&&g){const n=g.stickers.filter(t=>t.status==='READY').length,doneN=g.stickers.filter(t=>['READY','FAILED'].includes(t.anim_status)).length,work=g.stickers.some(t=>['PROCESSING','STALE'].includes(t.anim_status));
      if(work)return{t:`Cutting the animations: ${doneN} of ${n}`,pct:96+4*doneN/Math.max(1,n),cls:'run',el};
      return{t:`Done · ${g.stickers.filter(t=>t.anim_status==='READY').length} of ${n} animated`,pct:100,cls:'done',el}}
    return{t:j.kind==='sheet'?'Done · sheet received':'Done',pct:100,cls:'done',el}}
  const why=String(j.error||''),hint=/nsfw/i.test(why)?'Higgsfield\'s content filter refused this request (status nsfw): change the wording':/\b50[0-4]\b|unavailable/i.test(why)?'Higgsfield had a temporary problem (HTTP 5xx)':why;
  return{t:j.status==='TIMEOUT'?'Timed out waiting':'Failed: '+hint,pct:100,cls:'bad',el,retry:!/nsfw/i.test(why),ticket:!!j.external_task_id}}
const QOPEN=(()=>{try{return localStorage.getItem('stickerloom.qopen')==='1'}catch(e){return false}})();
let QO=QOPEN;
function ensureQueue(){let el=document.getElementById('lvqueue');if(!el){el=document.createElement('aside');el.id='lvqueue';document.body.appendChild(el)}return el}
function qRow(j){const st=qState(j),m=lfind(j.kind==='video'?'video':'image',j.model),lab=(j.request&&j.request.label)||'',
    opts=j.params?Object.entries(j.params).filter(([k])=>!['aspect_ratio','sound'].includes(k)).map(([k,v])=>k==='duration'?v+' s':v).join(' · '):'';
  return`<div class="lv-qr ${st.cls}"><span class=lv-qi>${ic(j.kind==='video'?'film':'photo')}</span><div class=lv-qm>
    <b>${j.kind==='video'?'Animation':'Sheet'}${lab?` · “${esc(lab.length>34?lab.slice(0,34)+'…':lab)}”`:''}</b>
    <div class=lv-qbar>${st.pct==null?'<i class=ind></i>':`<i style="width:${st.pct.toFixed(0)}%"></i>`}</div>
    <small><span class=lv-qs>${esc(st.t)}</span>${st.cls==='run'||st.cls==='done'?` · ${mmss(st.el)}${st.typ&&j.status==='CLAIMED'?` of about ${mmss(st.typ)}`:''}`:''}</small>
    <small class=mut>${esc(j.id)} · ${esc(m?m.label:(j.model||''))}${opts?' · '+esc(opts):''}${j.cost||j.cost_estimate?` · ◈ ${fcr(j.cost||j.cost_estimate)}`:''} · ${hhmm(j.claimed_at||j.created_at)}${j.generation?` · ${esc(j.generation)}`:''}${j.external_task_id?` · <code>${esc(String(j.external_task_id).slice(0,8))}</code> <button class=link data-act=qcopy data-t="${esc(j.external_task_id)}">copy id</button>`:''}</small>
    ${JR.stalled(j)?JR.controls(j):''}</div>${QACTIVE.includes(j.status)?'':`<button class="iconbtn" data-act=ljdismiss data-id="${esc(j.id)}" title="Remove from the list">${ic('x')}</button>`}</div>`}
function drawLive(){const el=ensureQueue(),rows=qRows();
  document.body.classList.toggle('hasq',rows.length>0);      // the fixed pill reserves room at the foot of every screen (studio.css body.hasq)
  if(!rows.length){el.innerHTML='';el.className='';return}
  const run=rows.filter(j=>QACTIVE.includes(j.status)),bad=rows.filter(j=>qState(j).cls==='bad').length,first=run[0],fs=first&&qState(first);
  const head=run.length?`<span class=spin></span><span><b>${run.length} running</b><small>${first.kind==='video'?'Animation':'Sheet'} · ${esc(fs.t)} · ${mmss(fs.el)}${fs.typ&&first.status==='CLAIMED'?` of ~${mmss(fs.typ)}`:''}</small></span>`
    :`<span class="lv-dot ${bad?'bad':'ok'}"></span><span><b>Queue</b><small>${bad?`${bad} need attention`:'all done'}</small></span>`;
  el.className='on'+(QO?' open':'');
  el.innerHTML=`<button class=lv-qhead data-act=qtoggle aria-expanded=${QO}>${head}<i class=lv-caret></i></button>${QO?`<div class=lv-qlist>${rows.map(qRow).join('')}</div>`:''}`}
ACT.qtoggle=()=>{QO=!QO;try{localStorage.setItem('stickerloom.qopen',QO?'1':'0')}catch(e){}drawLive()};
async function qRefresh(){const r=await api('/api/jobs');if(r.ok){LIVE.q=r.j.jobs;LIVE.typ=r.j.typical||{};
    /* a × clicked before dismissal was kept on the server lived only in this browser: send it once, so it holds in every browser from now on */
    for(const j of LIVE.q)if(!j.dismissed&&LIVE.dis.includes(j.id)&&!QACTIVE.includes(j.status)&&/^J\d+$/.test(j.id))post(`/api/jobs/${j.id}/dismiss`,{}).then(x=>{if(x.ok)j.dismissed=x.j.dismissed})}
  drawLive();if(typeof cpDrawTop==='function')cpDrawTop()}
ACT.qretry=el=>ACT.jrcontinue(el); // old saved markup uses the same-ticket action
ACT.qcopy=async el=>{try{await navigator.clipboard.writeText(el.dataset.t);toast('Higgsfield job id copied')}catch(e){toast('Copy failed',1)}};
ACT.ljdismiss=async el=>{const id=el.dataset.id;LIVE.dis.push(id);LIVE.jobs=LIVE.jobs.filter(j=>j.id!==id);lsave();drawLive();   /* hidden at once here; the server keeps it off for good, in every browser */
  if(/^J\d+$/.test(id)){const r=await post(`/api/jobs/${id}/dismiss`,{});if(!r.ok)toast(r.j.error||'Could not remove it for good',1);else{const q=(LIVE.q||[]).find(j=>j.id===id);if(q)q.dismissed=r.j.dismissed}}};
let LTB=false;
async function liveTick(){if(LTB)return;LTB=true;try{
  for(const j of [...LIVE.jobs]){if(j.error)continue;const r=await api('/api/jobs/'+j.id);if(!r.ok){if(r.status===404){LIVE.jobs=LIVE.jobs.filter(x=>x.id!==j.id)}continue}
    const s=r.j;j.status=s.status==='REQUESTED'?'starting':s.status==='CLAIMED'?'Higgsfield is working':s.status.toLowerCase();
    if(s.status==='FAILED'||s.status==='TIMEOUT'){j.error=s.error||s.status.toLowerCase();refreshHf()}
    else if(s.status==='DONE'&&(j.kind==='video'||s.generation)){
      LIVE.jobs=LIVE.jobs.filter(x=>x.id!==j.id);await refreshHf();
      toast(`${j.kind==='sheet'?'Sheet':'Animation'} ready: ${fcr(s.cost)} credits used${LIVE.hf&&LIVE.hf.credits!=null?`, ${fcr(LIVE.hf.credits)} left`:''}`);
      const nid=+String(s.generation||'').replace(/\D/g,'');
      if(j.kind==='sheet'&&j.mode==='regen'&&SES.gens.includes(j.batch)){SES.gens=SES.gens.map(x=>x===j.batch?nid:x);saveSes();GS.tab='stickers';glast='';histReload()}       // a regeneration takes its batch's place in view: the new generation of the same batch
      else if(j.kind==='sheet'&&j.mode==='pack'&&SES.run===j.run&&SES.gens.length){if(!SES.gens.includes(nid))SES.gens.push(nid);saveSes();GS.tab='stickers';glast='';histReload()}       // a multi-batch Generate: the 2nd .. nth sheet joins the session the first one opened
      else if(j.kind==='sheet'&&j.mode==='next'&&SES.gens.length){if(!SES.gens.includes(nid))SES.gens.push(nid);saveSes();GS.tab='stickers';glast='';histReload()}          // Next batch: one more batch of this session
      else if(j.kind==='sheet'){SES={prompt:j.label,gens:[nid],off:[],pack:'',...(j.run?{run:j.run}:{})};saveSes();GS.tab='stickers';glast='';
        for(const p of PVS.values())p.v.remove();PVS.clear();PVON.clear();ANIM.clear();location.hash='#/studio';histReload()}
      else{GS.tab='anim';glast=''}
      if(typeof tick==='function')tick(true)}}
  lsave();await qRefresh()}finally{LTB=false}}
setInterval(liveTick,2500);
setInterval(()=>{document.querySelectorAll('[data-lvt]').forEach(e=>e.textContent=Math.round((Date.now()-(+e.dataset.lvt))/1000)+'s');if(qRows().some(j=>QACTIVE.includes(j.status)||qState(j).cls==='run'))drawLive()},1000);

/* ---------- start-up and hooks into the Studio screen */
const _rg=RENDER.generate;
const showPanel=()=>{typeof composerMount==='function'?composerMount():livePanel();ensureBars();histReload();egSync()};
RENDER.generate=async function(){await _rg.apply(this,arguments);showPanel();drawLive()};
qRefresh();
async function liveInit(){const [m,h]=await Promise.all([api('/api/models'),api('/api/higgsfield')]);if(m.ok){LIVE.m=m.j;if(!m.j.styles.some(s=>s.id===LIVE.style))LIVE.style=m.j.default_style;lsel('image');lsel('video')}
  if(h.ok)LIVE.hf=h.j;drawChip();if(route_==='generate'){showPanel();drawLive()};liveTick()}
setInterval(refreshHf,20000);
liveInit();


/* ---------- the edge (stroke and trim) is a PREVIEW until it is applied. Dragging a slider changes ONE thumbnail only: the open one, else the last picked, else the first
   (nothing is stored or re-rendered). Apply saves a snapshot of the edge and applies it to the whole batch; Undo drops the preview, or restores the previous snapshot.
   The edge is also applied (and snapshotted) when the video is generated from the image and when the stickers go into a pack. */
const EG={outline:null,erode:null,pick:null,busy:false,drag:false};
const edgeBatches=()=>typeof included==='function'?included().filter(g=>g.stickers.some(t=>t.status==='READY')):[];
function ensureBars(){const g=document.querySelector('.gen2');if(!g)return;
  if(!document.getElementById('gedge')){const e=document.createElement('details');e.id='gedge';const ref=document.getElementById('gres');ref?ref.insertAdjacentElement('beforebegin',e):g.appendChild(e)}
  if(!document.getElementById('gpart')){const h=document.createElement('section');h.id='gpart';g.appendChild(h)}}
/* Undo replays the log as a stack: an applied edge is pushed, an undo entry pops it, so Undo always steps back one real snapshot (and never flips between two) */
function egCommitted(){const g=edgeBatches()[0];if(!g)return{o:0,e:0,hist:[],prev:null};
  const st=[];(g.edge_history||[]).forEach(h=>{if(h.via==='undo')st.length>1&&st.pop();else st.push(h)});
  return{o:g.outline_px||0,e:g.erode_px||0,hist:g.edge_history||[],prev:st.length>1?st[st.length-2]:null}}
const egVals=()=>{const c=egCommitted();return{o:EG.outline!=null?EG.outline:c.o,e:EG.erode!=null?EG.erode:c.e}};
const egDirty=()=>{const c=egCommitted(),v=egVals();return edgeBatches().length>0&&(v.o!==c.o||v.e!==c.e)};
const egClear=()=>{EG.outline=null;EG.erode=null};
function egPick(g,i){EG.pick={g,i}}
/* the one thumbnail that shows the preview */
function egTarget(){const ok=(g,i)=>{const b=GM.get(g),t=b&&b.stickers[i-1];return !!(t&&t.status==='READY'&&t.png)};
  if(typeof MD!=='undefined'&&MD&&ok(MD.g,MD.i))return{g:MD.g,i:MD.i};
  if(EG.pick&&ok(EG.pick.g,EG.pick.i)&&edgeBatches().some(b=>b.number===EG.pick.g))return EG.pick;
  for(const b of edgeBatches()){const t=b.stickers.find(t=>t.status==='READY'&&t.png);if(t)return{g:b.number,i:t.index}}return null}
function edgeControlsHtml(){return`<label>Stroke <input type=range min=0 max=24 step=1 value=0 data-edge=outline><output>0 px</output></label>
  <label>Trim <input type=range min=0 max=6 step=1 value=0 data-edge=erode><output>0 px</output></label>
  <span class=lv-eact><button class="btn sm pri" data-act=egapply>Apply</button><button class="btn sm" data-act=egundo>Undo</button></span><span class=lv-est data-eghint></span>`}
/* bring every edge control (the bar and the open thumbnail's) in line with the state; never rebuild or move the slider that is being dragged */
function egSync(){const bar=document.getElementById('gedge'),gs=edgeBatches();
  if(bar){if(!gs.length){bar.innerHTML='';bar.className='';bar.removeAttribute('data-built')}      // no batch, no bar: the empty strip (a bordered white box) must not stay behind after Remove batch
    else if(!bar.dataset.built){bar.dataset.built='1';bar.className='lv-edge';bar.innerHTML='<summary>Edge <small data-egsummary>Stroke and trim</small></summary><div class=lv-edge-controls>'+edgeControlsHtml()+'</div>'}}
  const c=egCommitted(),v=egVals(),dirty=egDirty(),prev=c.prev,t=egTarget(),
    working=gs.some(x=>x.stickers.some(s=>['PROCESSING','STALE'].includes(s.anim_status)));
  const summary=bar&&bar.querySelector('[data-egsummary]');if(summary)summary.textContent=(dirty?'Testing: ':'')+'stroke '+v.o+' px · trim '+v.e+' px';
  document.querySelectorAll('[data-edge]').forEach(i=>{const k=i.dataset.edge,val=k==='outline'?v.o:v.e;if(!(EG.drag&&i===document.activeElement)){i.value=val;if(i.nextElementSibling)i.nextElementSibling.textContent=val+' px'}});
  document.querySelectorAll('[data-act=egapply]').forEach(b=>b.disabled=!dirty||EG.busy);
  document.querySelectorAll('[data-act=egundo]').forEach(b=>{b.disabled=EG.busy||!(dirty||prev);b.textContent=dirty?'Cancel':'Undo';b.title=dirty?'Drop the test':prev?`Go back to the previous snapshot (stroke ${prev.outline} px, trim ${prev.erode} px)`:'Nothing to undo'});
  document.querySelectorAll('[data-eghint]').forEach(h=>{h.textContent=EG.busy?'Applying…':working?'Updating the animations…':dirty?`Testing on S${t?t.i:'?'} only. Apply saves a snapshot and uses it on all.`
    :`Applied: stroke ${c.o} px, trim ${c.e} px${c.hist.length>1?` · ${c.hist.filter(h=>h.via!=='undo').length} snapshots`:''}`})}
/* the preview itself. Outside the open view it is a floating card under the sliders (the grid is never touched): the target sticker, large, on the checkerboard,
   with Apply and Cancel on it. In the open view the big sticker pane is the preview. Both refresh in place while a slider moves. */
let EGT=null;
function egCard(show,url,t,v){
  if(!show)return TESTCARD.close('egcard');
  const c=TESTCARD.open({id:'egcard',apply:'egapply',cancel:'egundo'});
  TESTCARD.sync(c,{src:url,busy:EG.busy,caption:`S${t.i} · stroke ${v.o} px, trim ${v.e} px · Apply uses it on all`});
  TESTCARD.place(c,document.getElementById('gedge'),300,110);
}
function applyEdgePreview(){const dirty=egDirty(),t=dirty?egTarget():null,v=egVals(),modal=typeof MD!=='undefined'&&MD,
    url=t?`/api/generations/${t.g}/edge_preview?index=${t.i}&outline=${v.o}&erode=${v.e}&px=${modal?520:360}`:'',
    box=modal?document.querySelector('#modal .mpanes .pane:first-child .box'):null;
  document.querySelectorAll('.egprev,.egbadge').forEach(x=>{if(x.closest('#egcard'))return;if(!(t&&box&&x.parentElement===box))x.remove()});
  if(t&&box){let im=box.querySelector(':scope > .egprev');if(!im){im=document.createElement('img');im.className='egprev';im.alt='';box.appendChild(im);const b=document.createElement('span');b.className='egbadge';b.textContent='test';box.appendChild(b)}
    if(im.getAttribute('src')!==url)im.src=url}
  egCard(!!t&&!modal,url,t,v)}
const egRefresh=()=>{egSync();clearTimeout(EGT);EGT=setTimeout(applyEdgePreview,40)};
addEventListener('scroll',()=>{if(document.getElementById('egcard'))applyEdgePreview()},{passive:true});addEventListener('resize',()=>{if(document.getElementById('egcard'))applyEdgePreview()});
document.addEventListener('input',e=>{const t=e.target;if(!(t.dataset&&t.dataset.edge))return;EG.drag=true;EG[t.dataset.edge]=+t.value;if(t.nextElementSibling)t.nextElementSibling.textContent=t.value+' px';egRefresh()});
document.addEventListener('change',e=>{if(e.target.dataset&&e.target.dataset.edge){EG.drag=false;egSync()}});
ACT.egapply=async()=>{if(!egDirty()||EG.busy)return;const v=egVals();EG.busy=true;egSync();
  try{for(const g of edgeBatches()){const r=await postWait(`/api/generations/${g.number}/edge`,{outline:v.o,erode:v.e,via:'apply'},'Finishing the previous step…');if(!r.ok){toast(r.j.error||'Could not apply the edge',1);return}}
    egClear();toast('Edge applied to all: snapshot saved')}
  finally{EG.busy=false;glast='';if(typeof tick==='function')await tick(true);egSync();applyEdgePreview()}};
ACT.egundo=async()=>{if(EG.busy)return;
  if(egDirty()){egClear();egSync();applyEdgePreview();return}                                  // drop the preview
  const c=egCommitted(),prev=c.prev;if(!prev)return;     // go back to the previous snapshot
  EG.busy=true;egSync();
  try{for(const g of edgeBatches()){const r=await postWait(`/api/generations/${g.number}/edge`,{outline:prev.outline,erode:prev.erode,via:'undo'},'Finishing the previous step…');if(!r.ok){toast(r.j.error||'Could not undo',1);return}}
    toast(`Back to stroke ${prev.outline} px, trim ${prev.erode} px`)}
  finally{EG.busy=false;glast='';if(typeof tick==='function')await tick(true);egSync();applyEdgePreview()}};
['pointerup','keyup','blur'].forEach(ev=>document.addEventListener(ev,e=>{if(EG.drag&&e.target&&e.target.dataset&&e.target.dataset.edge)EG.drag=false},true));
setInterval(()=>{egSync();applyEdgePreview()},800);       // egSync leaves the slider that is being dragged alone

/* ---------- persistent history of batches: every batch ever made, in the shared second column. The API is paged (50 at a time, it reads one result.json per batch);
   the column asks for the next page by itself when it is scrolled near the end, so the person sees one list that scrolls and no "Load more". Page 1 is read again now and then and merged
   over what is already loaded (nothing that was loaded is dropped). */
const HB={items:[],more:false,total:0,loading:false,page:50,loaded:false,tried:false,sig:''};
const ago=ts=>{if(!ts)return'';const s=Math.max(0,Date.now()/1000-ts);return s<90?'just now':s<5400?Math.round(s/60)+' min ago':s<129600?Math.round(s/3600)+' h ago':s<2592000?Math.round(s/86400)+' d ago':new Date(ts*1000).toLocaleDateString([],{day:'numeric',month:'short',year:'numeric'})};
const hbSig=()=>HB.items.map(x=>[x.id,x.edited,x.ready,x.animated,histBatches(x).map(b=>b.id).join('+')].join(':')).join(',')+'|'+HB.total;
async function histLoad(more,quiet){if(HB.loading)return;HB.loading=true;
  const r=await api(`/api/history?offset=${more?HB.items.length:0}&limit=${HB.page}`);HB.loading=false;
  if(r.ok){
    if(more){const seen=new Set(HB.items.map(x=>x.id));HB.items=HB.items.concat(r.j.items.filter(x=>!seen.has(x.id)))}
    else{const fresh=new Set(r.j.items.flatMap(histBatches).flatMap(x=>[x.id,...(x.variants||[]).map(v=>v.id)]));HB.items=   /* a batch that joined a group is now a variation of a fresh row: its old row goes */r.j.items.concat(HB.items.filter(x=>!fresh.has(x.id)))}
    HB.total=r.j.total;HB.more=HB.items.length<HB.total;HB.loaded=true}
  const sig=hbSig(),same=sig===HB.sig;HB.sig=sig;
  if(!(quiet&&same)){drawHist();if(typeof spSecSync==='function')spSecSync();if(typeof cpDrawTop==='function')cpDrawTop()}}
const histReload=()=>histLoad(false);
/* while the Studio or Create is showing, the column is read again every 10 s (a batch that was edited or finished moves to the top); it redraws only when something changed */
setInterval(()=>{if(!document.hidden&&['generate','create'].includes(route_))histLoad(false,true)},10000);
/* A batch is one entry of the column (histRow: ONE picture, the first sticker, from `cells` of GET /api/history, with its title, G###, counts and edited time). A click on it
   makes it THE batch the Studio presents (ACT.hopen: the Studio's own view of it, nothing else beside it). Under that view sits the batch's Particles section (particles.js, spSecDraw): what was
   made for each of its stickers that is in a pack. The decisions on a batch's stickers are not shown here: they stay in its result.json and in GET /api/generations/<id>/history. */
/* "Allow AI vision of generated media?": asked once, the answer is remembered in this browser (localStorage stickerloom.allow_vlm = 1 / 0). The server enforces it too: the request itself carries allow_vlm.
   VLM.then is what runs once the person has said yes (the particle effects screen asks before the vision model looks at stickers). */
const VLM={then:null};
const vlmState=()=>{try{return localStorage.getItem('stickerloom.allow_vlm')}catch(e){return null}};
const vlmSet=v=>{try{localStorage.setItem('stickerloom.allow_vlm',v)}catch(e){}};
ACT.vlmyes=()=>{vlmSet('1');closeDlg();const f=VLM.then;VLM.then=null;if(f)f()};
const histTitle=it=>esc(titleCase(String(it.prompt||'').replace(/_/g,' '))||it.generation_id);
const histInfo=it=>`${esc(it.generation_id)} · ${it.ready} sticker${it.ready===1?'':'s'}${it.animated?` · ${it.animated} animated`:''} · edited ${ago(it.edited||it.created)}`;
/* a batch is ONE picture: the first of its stickers that has one (P4 of the UI/UX spec: not the sheet's 4 or 9 cells); a batch with no picture yet is the checkerboard */
const histThumb=it=>{const c=(it.cells||[]).find(x=>x.png);return`<span class=lv-hth>${c?`<img src="/out/${esc(it.generation_id)}/${esc(c.png)}" loading=lazy alt="" title="S${c.index}${c.animated?' · animated':''}">`:`<span class=lv-hnoimg title="${esc(String(((it.cells||[])[0]||{}).status||it.stage||'').toLowerCase())}"></span>`}</span>`};
/* one entry of the column: a click presents the batch in the Studio. `on` = it is the batch the Studio presents now. */
/* a family (flow/groups.py: a batch, its edits, redos and the batches added to it) is ONE entry here: the root's title and the variation in view (else the newest).
   Any entry can be dragged onto another: the one dropped on is the parent (POST /api/generations/{id}/join). The generations themselves are chosen in the Studio, in each batch's
   generations row (generate.js gensHtml), not in this column. */
const histVars=it=>(it.variants&&it.variants.length?it.variants:[it]);
/* a pack (pipeline._packs: one request's batches by one person, or batches dropped onto it) is ONE entry of the column with its batches under it, Batch 1 … n
   (an emoji pack in grid order). histBatches = the batches of an entry; histAll = every generation of every batch of the column. */
const histBatches=it=>(it.batches&&it.batches.length?it.batches:[it]);
const histAll=()=>HB.items.flatMap(histBatches).flatMap(histVars);
const histShown=b=>{const vs=histVars(b);return vs.find(v=>SES.gens.includes(v.id))||vs[vs.length-1]};
const histPackTitle=it=>String(it.prompt||'').replace(/\s*\b(core|social|reactions|daily)[\s_-]?v1\b/gi,'');
const histEntry=it=>{const bs=histBatches(it);if(bs.length<2)return histRow(bs[0]);
  const on=bs.some(b=>histVars(b).some(v=>SES.gens.includes(v.id))),n=bs.reduce((a,b)=>a+(histShown(b).ready||0),0);
  return`<div class="lv-hpack${on?' on':''}"><button class=lv-hphead data-act=hopenpack data-ids="${bs.map(b=>histShown(b).id).join(',')}" title="Open the whole pack in the Studio: every batch, one under the other">${histThumb(histShown(bs[0]))}<span class=lv-hmeta><b>${histTitle({prompt:histPackTitle(it)})}</b><small>${bs.length} batches · ${n} stickers · edited ${ago(it.edited||it.created)}</small></span></button>
   <div class=lv-hpbs>${bs.map((b,k)=>histRow(b,k+1,true)).join('')}</div></div>`};
const histRow=(it,k,inPack)=>{const vs=histVars(it),cur=vs.find(v=>SES.gens.includes(v.id)),on=!!cur,shown=cur||vs[vs.length-1];
  return`<div class=lv-hfam draggable=true data-hid=${it.id} title="Drag it onto another entry to put it in that pack">${inPack?`<button class="link lv-hout" data-act=hunpack data-id=${it.id} title="Take this batch out of the pack">${ic('x')}</button>`:''}<button class="lv-hrow${on?' on':''}" data-act=hopen data-id=${shown.id} aria-pressed=${on} title="Show ${esc(shown.generation_id)} in the Studio">${histThumb(shown)}<span class=lv-hmeta><b>${k?`Batch ${k} · `:''}${histTitle(it)}</b><small>${histInfo(shown)}${vs.length>1?` · ${vs.length} variations`:''}</small></span>${shown.kind==='particles'?`<span class=lv-pbadge title="Particles: no sticker owns them. Use, render and export them as they are">${ic('fx')}</span>`:''}</button></div>`};
async function histJoin(id,to){if(!id||!to||id===to)return;const r=await post(`/api/generations/${id}/join`,{to});if(!r.ok)return toast(r.j.error||'Could not add it to the group',1);
  toast(`Added to ${r.j.root}'s group`);HB.items=HB.items.filter(x=>x.id!==id);histLoad(false);if(typeof spSecSync==='function')spSecSync(true)}
/* drag and drop in the column: the dropped batch becomes ANOTHER BATCH of the pack it was dropped on (POST …/pack, flow/groups.set_pack),
   never a regeneration of it; the x on a batch of a pack takes it out again (a pack of its own) */
async function histPack(id,to){if(!id||!to||id===to)return;const r=await post(`/api/generations/${id}/pack`,{to});if(!r.ok)return toast(r.j.error||'Could not move it',1);
  toast(`${r.j.id} is in the pack now`);histLoad(false)}
ACT.hunpack=async el=>{const r=await post(`/api/generations/${el.dataset.id}/pack`,{to:null});if(!r.ok)return toast(r.j.error||'Could not take it out',1);toast(`${r.j.id} is a pack of its own now`);histLoad(false)};
ACT.hopenpack=el=>{const ids=String(el.dataset.ids||'').split(',').map(Number).filter(Boolean),first=histAll().find(x=>x.id===ids[0]);if(!first)return;
  gdHide();SES={prompt:first.prompt||'',gens:ids,off:[],pack:''};saveSes();GS.tab='stickers';glast='';MD=null;
  for(const p of PVS.values())p.v.remove();PVS.clear();PVON.clear();ANIM.clear();
  if(location.hash!=='#/studio')location.hash='#/studio';if(typeof tick==='function')tick(true);drawHist();if(typeof spSecSync==='function')spSecSync();if(typeof cpDrawTop==='function')cpDrawTop()};
ACT.hleave=async el=>{const r=await post(`/api/generations/${el.dataset.id}/leave`,{});if(!r.ok)return toast(r.j.error||'Could not take it out',1);toast(`${r.j.id} is on its own again`);histLoad(false);if(typeof spSecSync==='function')spSecSync(true)};
if(typeof document!=='undefined'&&document.addEventListener){
  document.addEventListener('dragstart',ev=>{const f=ev.target&&ev.target.closest&&ev.target.closest('[data-hid]');if(!f)return;ev.dataTransfer.setData('text/x-stickerloom-batch',f.dataset.hid);ev.dataTransfer.effectAllowed='move'});
  /* `hdrop`, never `drop`: `.drop` is the upload area's style (300 px tall), which made the entry under the pointer jump (2026-10-09). The highlight goes only when the
     pointer really leaves the entry, not when it crosses one of its children. */
  document.addEventListener('dragover',ev=>{const f=ev.target&&ev.target.closest&&ev.target.closest('[data-hid]');if(!f||!ev.dataTransfer.types.includes('text/x-stickerloom-batch'))return;ev.preventDefault();ev.dataTransfer.dropEffect='move';
    document.querySelectorAll('.hdrop').forEach(x=>{if(x!==f)x.classList.remove('hdrop')});f.classList.add('hdrop')});
  document.addEventListener('dragleave',ev=>{const f=ev.target&&ev.target.closest&&ev.target.closest('[data-hid]');if(f&&!(ev.relatedTarget&&f.contains(ev.relatedTarget)))f.classList.remove('hdrop')});
  document.addEventListener('dragend',()=>document.querySelectorAll('.hdrop').forEach(x=>x.classList.remove('hdrop')));
  document.addEventListener('drop',ev=>{const f=ev.target&&ev.target.closest&&ev.target.closest('[data-hid]');if(!f)return;const id=ev.dataTransfer.getData('text/x-stickerloom-batch');if(!id)return;ev.preventDefault();
    document.querySelectorAll('.hdrop').forEach(x=>x.classList.remove('hdrop'));histPack(+id,+f.dataset.hid)})}
/* the column: a title and the whole list, newest edit first; the next page is asked for when the list is scrolled near its end (no "Load more") */
function histColHTML(){return`<div class=c2h><h1>Projects</h1><span class=c2n>${HB.loaded?`${HB.total} in total`:''}</span></div>
  <div class="c2l lv-hcol" id=c2hist>${HB.items.map(histEntry).join('')||`<div class=mut style="padding:14px 18px">${HB.loaded?'No batches yet. Describe stickers in the Studio to make the first one.':'Reading the batches…'}</div>`}</div><div class=c2rem id=c2rem></div>`}
/* the trash of batches (GET /api/generations/removed, owner only): Restore puts a batch back under its own number; the list is empty (and hidden) for anyone else */
const REM={items:[],tried:false};
async function remLoad(){const r=await api('/api/generations/removed');REM.items=r.ok?r.j.batches:[];REM.tried=true;remDraw()}
function remDraw(){const el=document.getElementById('c2rem');if(!el)return;const was=el.querySelector('details')&&el.querySelector('details').open;
  el.innerHTML=remHtml(REM.items,was)}
/* each removed batch: Restore (back under its own number) or Remove (gone for good: files and database rows, flow/purge.py); Remove all = every removed batch, trashed packs stay */
function remHtml(items,open){return items.length?`<details ${open?'open':''}><summary>Removed batches (${items.length})</summary>${items.map(b=>`<div class=rrow><span><b>${esc(b.id)}</b> ${b.subject?esc(String(b.subject).replace(/_/g,' ')):''} <span class=mut>${ago(b.removed)}</span></span><span class=row><button class="btn sm" data-act=grestore data-n=${b.number}>Restore</button><button class="btn sm dng" data-act=gpurge data-id=${esc(b.id)}>Remove</button></span></div>`).join('')}<div class=row style="justify-content:flex-end;padding:8px 0"><button class="btn sm dng" data-act=gpurgeall>Remove all (${items.length})</button></div></details>`:''}
async function remPurge(url,body){const r=await post(url,body);if(!r.ok){toast(r.j.error||'Nothing was removed',1);return null}
  let t=r.j;for(let i=0;t&&t.status==='running'&&t.id&&i<240;i++){await new Promise(res=>setTimeout(res,500));const q=await api('/api/trash/purges/'+t.id);if(q.ok)t=q.j}
  const done=(t&&t.done)||0,refused=(t&&t.refused||[]).length;toast(t&&t.status==='failed'?(t.error||'The removal stopped'):`${done} removed for good${refused?`, ${refused} kept (they need their own decision in Settings > Trash)`:''}`,t&&t.status==='failed');
  await remLoad();return t}
ACT.gpurge=el=>{const id=el.dataset.id;confirmDlg(`Remove ${id} for good? Its files and database rows are deleted. This cannot be undone.`,async()=>{const r=await post('/api/trash/purge',{type:'batch',id});
  if(!r.ok&&r.status===409&&/Confirm to go on/.test(r.j.error||''))return confirmDlg(r.j.error,()=>remPurge('/api/trash/purge',{type:'batch',id,confirm_shared:true}),'Remove anyway');
  if(!r.ok)return toast(r.j.error||'Nothing was removed',1);let t=r.j;for(let i=0;t.status==='running'&&t.id&&i<240;i++){await new Promise(res=>setTimeout(res,500));const q=await api('/api/trash/purges/'+t.id);if(q.ok)t=q.j}
  toast(t.status==='failed'?(t.error||'The removal stopped'):`${id} removed for good`,t.status==='failed');await remLoad()},'Remove')};
ACT.gpurgeall=async()=>{const r=await api('/api/trash');if(!r.ok)return toast(r.j.error||'Could not read the trash',1);const pb=r.j.purge_batches||{count:0};
  if(!pb.count)return toast('Every removed batch needs its own decision: use Remove on it',1);
  dlg(`<h2>Remove all ${pb.count} removed batch${pb.count===1?'':'es'} for good?</h2><p>Their files and database rows are deleted. This cannot be undone. Type “${esc(pb.phrase)}” to go on.</p><input type=text id=rem-typed autocomplete=off placeholder="${esc(pb.phrase)}"><div class=row style="justify-content:flex-end"><button class=btn data-act=dlgx>Cancel</button><button class="btn dng" data-act=gpurgeallgo data-phrase="${esc(pb.phrase)}">Remove all</button></div>`);
  const i=document.getElementById('rem-typed');if(i)i.focus()};
ACT.gpurgeallgo=el=>{const i=document.getElementById('rem-typed'),typed=i?i.value.trim().toLowerCase():'';if(typed!==el.dataset.phrase)return toast(`Type exactly “${el.dataset.phrase}” to go on.`,1);
  closeDlg();remPurge('/api/trash/purge_all',{confirm:typed,kind:'batch'})};
ACT.grestore=async el=>{const r=await post(`/api/generations/${el.dataset.n}/restore`,{});if(!r.ok){toast(r.j.error,1);return}toast(`${r.j.id} is back`);await remLoad();histLoad(false)};
/* Batches: in the Studio the earlier batches open in a labelled drawer from the workspace, the left column being the creation panel.
   It is non-modal over the workspace: focus moves in, Escape or Close returns to the Batches button; opening a batch closes it. Create elsewhere keeps the column. */
const BD={open:false,back:null};
function bdEl(){let d=document.getElementById('bdrawer');if(d)return d;
  d=document.createElement('aside');d.id='bdrawer';d.className='bdrawer';d.hidden=true;d.tabIndex=-1;
  d.setAttribute('role','dialog');d.setAttribute('aria-modal','false');d.setAttribute('aria-labelledby','bdtitle');
  d.innerHTML=`<div class=bd-head><h2 id=bdtitle>Batches</h2><button class=iconbtn data-act=bdclose aria-label="Close batches" title="Close">${ic('x')}</button></div><div class=bd-body id=bdbody></div>`;
  const sc=document.createElement('div');sc.id='bdscrim';sc.dataset.act='bdclose';sc.hidden=true;
  document.body.append(sc,d);return d}
function bdSync(){document.querySelectorAll('[data-act=bdopen]').forEach(b=>b.setAttribute('aria-expanded',String(BD.open)))}
ACT.bdopen=el=>{const d=bdEl();if(BD.open)return bdClose(true);BD.open=true;BD.back=el||document.activeElement;d.hidden=false;document.getElementById('bdscrim').hidden=false;
  histCol();bdSync();const first=d.querySelector('#bdbody button');(first||d).focus({preventScroll:true})};
function bdClose(restore){const d=document.getElementById('bdrawer');if(!d||!BD.open)return;BD.open=false;d.hidden=true;document.getElementById('bdscrim').hidden=true;bdSync();
  if(restore){let b=BD.back;if(!b||!b.isConnected)b=document.querySelector('[data-act=bdopen]');if(b)b.focus({preventScroll:true})}BD.back=null}
ACT.bdclose=()=>bdClose(true);
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&BD.open&&!document.getElementById('dlg').classList.contains('on')){e.stopPropagation();bdClose(true)}});
/* where the batch list (and projmap.js's project map) draws: the Batches drawer in the Studio, the second column on Create */
const histHost=()=>route_==='generate'?(bdEl(),document.getElementById('bdbody')):document.getElementById('col2');
function histCol(){const c2=histHost();if(!c2)return;
  if(!c2.querySelector('#c2hist')||c2.dataset.k!=='batches'){c2.dataset.k='batches';c2.innerHTML=histColHTML();const l=document.getElementById('c2hist');
    l.addEventListener('scroll',()=>{if(HB.more&&!HB.loading&&l.scrollTop+l.clientHeight>l.scrollHeight-320)histLoad(true)})}
  else{const l=document.getElementById('c2hist'),top=l.scrollTop;c2.querySelector('.c2n').textContent=HB.loaded?`${HB.total} in total`:'';l.innerHTML=HB.items.map(histEntry).join('')||l.innerHTML;l.scrollTop=top}
  if(!REM.tried){REM.tried=true;remLoad()}else remDraw();
  if(!HB.loaded){if(!HB.tried){HB.tried=true;histLoad(false)}}
  else{const l=document.getElementById('c2hist');if(HB.more&&!HB.loading&&l.scrollHeight<=l.clientHeight+320)histLoad(true)}}
/* the column is redrawn here; under the Studio's view the batch's Particles section is drawn by particles.js (spSecDraw) */
function drawHist(){if(['generate','create'].includes(route_))histCol();if(typeof spSecDraw==='function')spSecDraw()}
/* the credits pill's drop-down lists the recent batches and this is what opens one in the Studio, as does a row of the Earlier-batches column */
ACT.hopen=el=>{const it=histAll().find(x=>x.id===+el.dataset.id);if(!it)return;bdClose(false);
  if(it.kind==='particles'&&typeof spOpenBatch==='function')setTimeout(()=>spOpenBatch(it.generation_id||it.id));     /* a particle batch opens on its set (particles.js) */
  gdHide();
  SES={prompt:it.prompt||'',gens:[it.id],off:[],pack:''};saveSes();GS.tab=it.animated?'anim':'stickers';glast='';MD=null;egClear();EG.pick=null;
  for(const p of PVS.values())p.v.remove();PVS.clear();PVON.clear();ANIM.clear();
  if(typeof CP!=='undefined')CP.menu=false;if(location.hash!=='#/studio')location.hash='#/studio';
  if(typeof tick==='function')tick(true);const r=document.getElementById('gres');if(r)r.scrollIntoView({behavior:'smooth',block:'start'});drawHist();if(typeof spSecSync==='function')spSecSync();if(typeof cpDrawTop==='function')cpDrawTop()};
