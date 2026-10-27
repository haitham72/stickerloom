/* The project map (redesign phase 4): the Studio's left column.
   Project = one idea · Batch = one set of stickers · Sheet = one try at it · Video = one animation of a sheet, read in ONE call (GET /api/generations/{id}/map,
   flow/projects.py). Above it the project switcher opens every project (the Earlier-batches list of live.js, unchanged: paging, drag and drop, Removed batches).
   Every control calls the action it always called: a sheet of the open batch is `ggen`, another batch's sheet `pmopen`, a video `gapick` / `garm`, the sheet menu
   `gmain` / `hleave` / `ggroup` / `gdel`, the batch menu `hunpack`, the branches `pgsheet` / `gtab` (the Prompt and Motion views, with their prices) /
   `gnext`. This file wraps histCol (the column) and gview (the breadcrumb) the way live.js wraps drawRail. Prefix: pm. */
const PM={data:null,id:null,t:0,loading:false,list:false};
const pmCur=()=>(typeof SES!=='undefined'&&SES.gens||[]);
const pmOpen=()=>route_==='generate'&&pmCur().length&&!(typeof gdOn==='function'&&gdOn());
async function pmLoad(force){const g=pmCur()[0];if(!g){PM.data=null;PM.id=null;return}
  if(PM.loading||(!force&&PM.id===g&&Date.now()-PM.t<8000))return;
  PM.loading=true;const r=await api(`/api/generations/${g}/map`);PM.loading=false;PM.t=Date.now();PM.id=g;PM.data=r.ok?r.j:null;
  if(route_==='generate'){histCol();pmCrumbsSync()}}
const PMSTATUS={making:'Being made…',failed:'Failed',not_cut:'Not cut',ready:''};
const pmCount=s=>s.status!=='ready'?PMSTATUS[s.status]:`${s.counts.ready} sticker${s.counts.ready===1?'':'s'}${s.counts.blocked?` · ${s.counts.blocked} held`:''}${s.counts.animated?` · ${s.counts.animated} animated`:''}`;
/* one row menu: a <details>, so it needs no state; its items are the actions as they are */
const pmMenu=(label,items)=>items.length?`<details class=pm-menu><summary aria-label="${esc(label)}" title="${esc(label)}">⋯</summary><div class=pm-pop role=menu>${items.join('')}</div></details>`:'';
const pmItem=(attrs,text)=>`<button role=menuitem ${attrs}>${text}</button>`;
function pmSheet(s,b,cur){const on=cur.includes(s.number),inBatch=b.sheets.some(x=>cur.includes(x.number)),from=b.sheets.find(x=>cur.includes(x.number));
  const open=on?'aria-current=true':inBatch?`data-act=ggen data-g=${from.number} data-to=${s.number}`:`data-act=pmopen data-g=${s.number}`;
  const menu=on?pmMenu('This sheet',[pmItem(`data-act=gmain data-g=${s.number}`,s.id===b.picked?'The main sheet ✓':'Make this the main sheet'),
    pmItem(`data-act=hleave data-id=${s.number}`,'Make it its own batch'),pmItem('data-act=ggroup','Move to another batch…'),
pmItem(`data-act=gdel data-g=${s.number} class=dng`,'Remove')]):'';
  const vids=s.videos.map(v=>`<div class="pm-v${v.used?' used':''}">${v.thumb?`<img src="${esc(v.thumb)}" alt="" loading=lazy>`:'<span class=pm-noimg></span>'}<span><b>Video ${esc(v.id)}</b><small>${v.used?'in use':v.status==='SUPERSEDED'?'earlier':esc(String(v.status||'').toLowerCase())}${v.blocked?' · held':''}</small></span>
    ${on&&!v.used?`<button class="btn sm" data-act=gapick data-g=${s.number} data-a=${esc(v.id)} title="Cut the stickers from this video again (free)">Use</button><button class="pm-x" data-act=garm data-g=${s.number} data-a=${esc(v.id)} title="Take this video out (its files stay)" aria-label="Remove video ${esc(v.id)}">${ic('x')}</button>`:''}</div>`).join('');
  const jobs=s.jobs.map(j=>`<div class=pm-job><span class=spin></span>${j.kind==='video'?'A video is being made…':'A sheet is being made…'}</div>`).join('');
  const branch=on&&s.status==='ready'?`<div class=pm-branch>${s.videos.length?`<button data-act=gtab data-t=anim>+ Another video of this sheet</button>`:`<button data-act=gtab data-t=anim>+ Make a video</button>`}
    <button data-act=pgsheet data-g=${s.number} title="A new sheet of this batch from the same prompt (the price is shown before anything is spent)">+ Another sheet, same prompt</button>
    <button data-act=gtab data-t=plan>+ Change the prompt, then a new sheet</button></div>`:'';
  return`<div class="pm-s ${s.status}${on?' on':''}"><div class=pm-srow><button class=pm-sbtn ${open} title="${esc(s.id)}${s.prompt_changed?' · the prompt was changed':''}">${s.thumb?`<img src="${esc(s.thumb)}" alt="" loading=lazy>`:'<span class=pm-noimg></span>'}<span><b>${s.id===b.picked&&b.sheets.length>1?'★ ':''}${esc(s.label)}${s.prompt_changed?' · new prompt':''}</b><small>${esc(s.id)} · ${pmCount(s)}</small></span></button>${menu}</div>${vids}${jobs}${branch}</div>`}
function pmBatch(b,k,cur){const on=b.sheets.some(s=>cur.includes(s.number)),root=+b.root.replace(/\D/g,''),many=PM.data.batches.length>1;
  return`<section class="pm-b${on?' on':''}"><div class=pm-bh><span><b>Batch ${k+1}</b><small>${esc(b.title)}${b.preset?' · '+esc(String(b.preset).split('-')[0]):''}</small></span>
    ${many?pmMenu('This batch',[pmItem(`data-act=hunpack data-id=${root}`,'Move to its own project')]):''}</div>${b.sheets.map(s=>pmSheet(s,b,cur)).join('')}</section>`}
function pmHTML(){const d=PM.data,cur=pmCur(),first=d&&d.batches[0]&&d.batches[0].sheets[0];
  const head=`<div class="c2h pm-h"><button class=pm-switch data-act=pmlist aria-expanded=false title="Every project">${first&&first.thumb?`<img src="${esc(first.thumb)}" alt="">`:'<span class=pm-noimg></span>'}<span><small>Project</small><b>${d?esc(d.project.title):'…'}</b></span><i aria-hidden=true>▾</i></button></div>`;
  if(!d)return head+`<div class="c2l pm-map"><div class=mut style="padding:14px 10px">${PM.loading||PM.id!==cur[0]?'Reading the project…':'This batch has no map.'}</div></div>`;
  return head+`<div class="c2l pm-map" id=pmmap>${d.project.request?`<p class=pm-req title="The request">${esc(d.project.request)}</p>`:''}
   ${d.batches.map((b,k)=>pmBatch(b,k,cur)).join('')}
   <button class=pm-next data-act=gnext title="The next batch of this request, as a prompt you read before anything is spent">+ Next batch</button></div>`}
/* the column: the map when a batch is open in the Studio, else (or after the switcher) every project */
const _pmHistCol=histCol;
histCol=function(){const c2=histHost();if(!c2)return;
  if(PM.list&&pmCur()[0]!==PM.listFor)PM.list=false;          /* picking a project in the list (ACT.hopen / hopenpack) closes the list again */
  if(!pmOpen()||PM.list){if(c2.dataset.k==='map')c2.dataset.k='';_pmHistCol();
    if(pmOpen()&&PM.list&&!c2.querySelector('.pm-back')){const h=c2.querySelector('.c2h');if(h)h.insertAdjacentHTML('afterbegin',`<button class=pm-back data-act=pmlist title="Back to the open project">${ic('back')}</button>`)}
    return}
  const m=document.getElementById('pmmap'),top=m?m.scrollTop:0;c2.dataset.k='map';c2.innerHTML=pmHTML();const n=document.getElementById('pmmap');if(n)n.scrollTop=top;
  if(PM.id!==pmCur()[0]||!PM.data)pmLoad(true);else pmLoad(false)};
ACT.pmlist=el=>{PM.list=!PM.list;PM.listFor=pmCur()[0];const c2=histHost();if(c2)c2.dataset.k='';histCol();
  if(route_==='generate'&&!BD.open)ACT.bdopen(el)};   /* the breadcrumb's project opens the Batches drawer on the list */
/* a sheet of another batch of the project: present it (the same reset as a click in the project list, live.js ACT.hopen) */
ACT.pmopen=el=>{const id=+el.dataset.g;if(!id||pmCur().includes(id))return;gdHide();if(typeof bdClose==='function')bdClose(false);
  SES={prompt:(PM.data&&PM.data.project.request)||SES.prompt||'',gens:[id],off:[],pack:SES.pack||''};saveSes();GS.tab='stickers';glast='';MD=null;
  for(const p of PVS.values())p.v.remove();PVS.clear();PVON.clear();ANIM.clear();
  if(typeof tick==='function')tick(true);histCol();if(typeof spSecSync==='function')spSecSync();if(typeof cpDrawTop==='function')cpDrawTop()};
/* the breadcrumb above the Studio's header: Project › Batch n › Sheet (› Video when the Motion view is open) */
function pmCrumbs(){const d=PM.data,cur=pmCur();if(!d||PM.id!==cur[0])return'';
  const k=d.batches.findIndex(b=>b.sheets.some(s=>cur.includes(s.number))),b=d.batches[k];if(!b)return'';
  const s=b.sheets.find(x=>cur.includes(x.number)),v=GS.tab==='anim'&&s&&s.videos.find(x=>x.used);
  return`<nav class=pm-crumbs aria-label="Where you are"><button data-act=pmlist title="Every project">${esc(d.project.title)}</button><i>›</i><span>Batch ${k+1}</span><i>›</i><span>${esc(s?s.label:'')}</span>${v?`<i>›</i><span>Video ${esc(v.id)}</span>`:''}</nav>`}
/* the breadcrumb follows a map that arrives after the Studio drew itself, without redrawing the Studio */
function pmCrumbsSync(){const res=document.getElementById('gres');if(!res||!pmOpen())return;const old=res.querySelector('.pm-crumbs'),h=pmCrumbs();
  if(old){if(old.outerHTML!==h)old.outerHTML=h||''}else if(h&&res.firstElementChild)res.insertAdjacentHTML('afterbegin',h)}
/* a join, a leave, a removal or a restore reloads the project list (live.js histLoad): the map follows at once */
const _pmHistLoad=histLoad;
histLoad=async function(...a){const r=await _pmHistLoad(...a);if(pmOpen())pmLoad(true);return r};
const _pmGview=gview;
gview=function(){const h=_pmGview();return h&&pmOpen()?pmCrumbs()+h:h};
/* the map follows the Studio: live.js reloads the project list every 10 s on this screen, and the wrap of histLoad above reloads the map with it */
