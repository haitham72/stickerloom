/* Particle effects (#/effects, a tool of Create). Reaction effects (#/reactions, one bank emoji) is reactions.js, not this file.
   An earlier E### run (#/effects/E###) is the reaction Telegram plays when you press an emoji, made for a PACK. docs/architecture.md ("Reactions").
   In a run: pick a pack and its stickers, see what bursts out of each (a vision model or a built-in table), then make the particles:
   Simulate (free: gravity / explosion / vortex sliders with a live preview rendered by the engine; the particles are the pack's own stickers, or an AI-drawn sheet) or
   Video (a text-only Kling clip; the price is shown first and the click is the go-ahead). Results are 3-second Telegram stickers; Add puts them into the pack tagged with the source emoji
   and saves them under their sticker. Everything here calls /api/effects; nothing is decided in the browser.
   TWO screens run an effect with the functions of this file (the burst maker, Motion and Finish, is the SET's and lives in particles.js: spBurstHtml): this pro screen (state FX) and the Studio's Particles tab (particles.js, state SP). Both states have the same shape (fxNew), the markup
   of an effect sits under a root with data-fxx="fx|sp", and every handler finds its state from that root (fxX): one implementation, so a slider, a warning or "Add" behaves the same in both. */
'use strict';
ICONS.fx='<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8"/><circle cx="12" cy="12" r="1.8"/>';
const FXS={};
/* dr: the drawn particles (the ideas the vision model offered per grid, the names chosen, the prices, the cells ticked); set: the particle set saved from this run (read from the server), whose burst the Motion step makes */
const fxNew=who=>({who,pack:'',sel:new Set(),mode:'video',grid:'2x2',note:'',eid:'',rec:null,past:[],set:null,est:{},pick:new Set(),timer:0,pvT:{},busy:{},gen:{},sig:{},last:'',
  dr:{grid:'2x2',opts:{},extra:[],chosen:[],est:{},busy:{},use:null,useN:0,redo:false,change:false,picked:''}});
const FX=FXS.fx=fxNew('fx');
const fxX=el=>{const r=el&&el.closest?el.closest('[data-fxx]'):null;return FXS[r?r.dataset.fxx:'fx']||FX};
const FXSL=[['magnitude','Energy',0,3,.05],['gravity','Float',-2,3,.05],['vortex','Swirl',-2,2,.05],['particle_size','Size',.25,3,.05],['count','Reactions',4,80,1],['spin','Spin',0,3,.05],['erosion_px','Erosion',0,6,1]];
const FXMAIN=['magnitude','gravity','vortex','particle_size'];
const FXPRESETS=['burst','fountain','vortex','rain','confetti'];
const FXWHY={effect_tail_faded:'Particles were still on screen at the end, so the last frames were faded out to end empty, like a Telegram effect.',effect_empty_start:'Something is on screen in the first frames.',
 effect_empty_end:'Something is still on screen in the last frames.',effect_has_burst:'Hardly anything bursts: the effect is nearly empty.',effect_not_a_still:'Nothing moves.',effect_inside_cell:'Particles leave their cell.',
 key_is_seamless:'The green screen has panels or patterns, so the keying may eat parts of the reactions. Use it anyway, or make another take.',
 grid_detected:'The sheet was cut anyway, but its grid lines were not clear: look at each cell.',sheet_size:'The sheet came back at an unusual size and was cut anyway.',
 size_budget:'Over Telegram’s size limit.',codec_vp9:'Not VP9.',dimensions:'Not 512 x 512.',fps:'Not 30 fps.',duration:'Longer than 3 seconds.',alpha_decoded:'No transparency came out.',cell_unreadable:'This cell could not be read.'};
/* a drawn cell's warnings (generate.js's WARNWHY speaks of "the character"; these speak of the particle) */
const FXCELL={chroma_risk:'Part of this particle is close to the green-screen colour, so some of it may have been cut away.',holes:'There is a hole inside this particle, probably a green part that the cut removed.',
 edge_trimmed:'The trim took off more than the thin rim.',duplicate_cell:'It looks almost the same as another cell.',single_subject:'Two separate shapes are in this cell.'};
const fxWords=id=>FXWHY[id]||FXCELL[id]||String(id).replace(/_/g,' ');
const fxCellWhy=c=>{c=c||{};if(!c.png)return['No picture came out of this cell'+(c.reason?' ('+String(c.reason).replace(/_/g,' ')+')':'')+', so it cannot be used.'];
 return[...((c.metrics||{}).warnings||[]).map(fxWords),...(c.status==='FAILED'?['Python flagged this cell: '+String(c.reason||'it failed').replace(/_/g,' ')+'. Use it anyway if it looks right.']:[])]};
const fxNum=v=>v!=null?+String(v).replace(/\D/g,'')||0:0;
const fxSet=e=>e&&e.set&&typeof e.set==='object'?e.set:null;
const fxSetGen=e=>{const s=fxSet(e);return s?fxNum(s.generation):0};
const fxCells=(X,n)=>{const r=X.gen[n];return r?(r.stickers||[]):[]};
const fxReady=(X,n)=>fxCells(X,n).filter(s=>s.status==='READY'&&s.png&&(s.review||{}).still!=='REJECTED');
const fxCutting=r=>!r||(!r.error&&(r.stage!=='sliced'||!!r.busy));
const fxJobBad=j=>!!j&&['FAILED','TIMEOUT'].includes(j.status);
/* the names of the ideas a suggest answer holds (strings, or objects with a name) */
const fxOptNames=j=>{const a=(j&&(j.options||j.suggestions||j.elements||j.names))||[];return[...new Set(a.map(x=>typeof x==='string'?x:(x&&(x.name||x.label||x.text))||'').map(s=>String(s).trim()).filter(Boolean))]};
const fxCols=grid=>grid==='3x3'?9:4;

FX.root=()=>$('s-effects');FX.alive=()=>route_==='effects';FX.draw=()=>fxDraw();
/* one earlier run, #/effects/E###; #/effects itself is the Studio's particle editor (particles.js wraps this RENDER and passes only an argument) */
RENDER.effects=async arg=>{await loadLib();clearTimeout(FX.timer);
 if(!/^E\d+$/i.test(arg||'')){FX.eid='';FX.rec=null;location.hash='#/effects';return}
 if(FX.eid!==arg.toUpperCase())fxReset(FX);FX.eid=arg.toUpperCase();await fxLoad(FX);
 fxDraw()};
ACT.fxback=()=>{FX.eid='';FX.rec=null;location.hash='#/effects'};
/* a state forgets everything about the effect it was showing (the pack and the mode stay) */
function fxReset(X){clearTimeout(X.timer);Object.assign(X,{rec:null,set:null,est:{},pick:new Set(),pvT:{},busy:{},gen:{},sig:{},last:'',dr:fxNew(X.who).dr})}
/* read the effect, and what its jobs and drawn sheets are doing; keep polling while something is in flight */
async function fxLoad(X){const eid=X.eid,r=await api('/api/effects/'+eid);if(X.eid!==eid)return false;      /* the person went elsewhere meanwhile */
 if(!r.ok){toast(r.j.error||'No such effect',1);X.eid='';X.rec=null;return false}
 const e=X.rec=r.j;
 for(const g of e.groups||[]){const v=(e.video||{})[g.id];if(v&&v.status==='REQUESTED'&&v.job){const j=await api('/api/jobs/'+v.job);if(j.ok)v.live=j.j}}
 const s=fxSet(e);if(s){if(s.status==='REQUESTED'&&s.job){const j=await api('/api/jobs/'+s.job);if(j.ok)s.live=j.j}
  const sn=fxSetGen(e);if(sn){const q=await api('/api/generations/'+sn);if(q.ok)X.gen[sn]=q.j}}
  const ids=e.sets||[];X.set=null;if(ids.length){const q=await api('/api/particles/'+ids[ids.length-1]);if(X.eid!==eid)return false;if(q.ok)X.set=q.j}                 /* the run's last saved set: its burst is the Motion step */
 fxPoll(X);return true}
function fxBusy(X){const e=X.rec;if(!e)return false;
 return e.status==='NEW'||Object.values(e.video||{}).some(v=>v.status==='REQUESTED'&&!fxJobBad(v.live))||fxDrBusy(X,e)}
const fxSig=X=>{const e=X.rec;if(!e)return'';const s=fxSet(e)||{};return JSON.stringify([e.status,s.status,s.generation,(s.live||{}).status,(s.live||{}).stage,Object.values(e.video||{}).map(v=>[v.status,(v.live||{}).status,(v.live||{}).stage]),(e.results||[]).length,
  Object.keys(X.gen).map(n=>[n,fxReady(X,n).length,(X.gen[n]||{}).stage,!!(X.gen[n]||{}).busy])])};
function fxPoll(X){clearTimeout(X.timer);if(!fxBusy(X)||!X.alive())return;
 X.timer=setTimeout(async()=>{if(!X.eid)return;await fxLoad(X);const sig=fxSig(X);if(X.alive()&&sig!==X.last){X.last=sig;X.draw()}},2200)}

/* ---------- one effect */
const fxSticker=(e,sid)=>{const p=packById(e.pack_id),s=p&&p.stickers.find(x=>x.id===sid);return s||null};
function fxPieces(X,g){return `<div class=fx-pieces>${g.elements.map((x,i)=>`<span class=fx-chip>${esc(x)}<button data-act=fxdelpiece data-g=${g.id} data-i=${i} aria-label="Remove ${esc(x)}">${ic('x')}</button></span>`).join('')}
 <input class=fx-add type=text placeholder="add a reaction" data-fxadd=${g.id}><button class="btn sm" data-act=fxaddpiece data-g=${g.id}>Add</button></div>`}
function fxVideoPanel(X,e,g){const v=(e.video||{})[g.id]||{},est=X.est[g.id+e.grid.join('x')];
 if(v.status==='REQUESTED'){const j=v.live||{};return `<div class=fx-job>${fxJobBad(j)?`<b class=bad>The video failed</b><div class=mut>${esc(j.error||'')}</div><button class="btn" data-act=fxvideo data-g=${g.id} ${est&&est.credits!=null?'':'disabled'}>Try again</button>`:`<span class=spin></span> <b>Making the video</b> <span class=mut>${esc(v.job||'')} · ${esc(j.stage||j.status||'waiting')}</span>`}</div>`}
 return `<div class=fx-job><span class=mut>${e.grid[0]} x ${e.grid[1]} cells, screen ${esc(g.key)}, Kling pro 3 s, no start image.</span>
  <div class=fx-price data-fxest=${g.id}>${est&&est.credits!=null?`about ${est.credits} credits`:est?'The price is unavailable.':'Getting the price…'}</div>${est&&est.credits==null?`<button class=link data-act=fxprice data-g=${g.id}>Retry price</button>`:''}
  <button class="btn pri" data-act=fxvideo data-g=${g.id} ${est&&est.credits!=null?'':'disabled'}>${ic('film')} Make the video</button>${v.status==='DONE'?`<small class=mut>Made (${v.cells} cells, ${v.cost!=null?v.cost+' credits':''}). Make another to get more takes.</small>`:''}</div>`}
/* ---------- the drawn particles: ideas -> pick the names -> price -> draw -> tick the cells -> the simulation uses them
   POST suggest {grid, allow_vlm} (the vision model looks at the batch's sheet) -> particles_estimate {grid, elements} -> particles {grid, elements, go} -> the effect's `set` (REQUESTED, then DRAWN with
   its batch) -> the cells are the batch's stickers -> particles_pick {indexes}. Nothing is blocked: every cell with a picture can be used, a warning is a sentence. */
const fxDrKey=d=>d.grid+'|'+d.chosen.join('|');
/* the cells with a picture that are ticked (a cell with no picture cannot be used) */
const fxTicked=(X,e)=>fxCells(X,fxSetGen(e)).filter(c=>c.png&&X.dr.use&&X.dr.use.has(c.index)).map(c=>c.index);
const fxDrNames=d=>[...new Set([...(((d.opts[d.grid]||{}).list)||[]),...d.extra,...d.chosen])];      /* a chosen name never disappears from the chips, whatever list the grid has now */
/* a drawn sheet is linked with EVERY cell picked (so the burst works at once); the person's own pick is a PICK line in the history after that */
const fxPicked=(X,e)=>{const n=fxSetGen(e);if(!n)return false;if(X.dr.picked===e.id+':'+n)return true;const h=(e.history||[]).slice().reverse().find(x=>x.decision==='PICK'||x.decision==='LINK');return !!h&&h.decision==='PICK'};
function fxDrState(X,e){const s=fxSet(e),d=X.dr;
 if(d.redo||!s)return'make';
 if(s.status==='FAILED')return'failed';
 if(s.status==='REQUESTED')return fxJobBad(s.live)?'failed':'wait';
 const n=fxSetGen(e);if(!n)return'make';
 if(fxCutting(X.gen[n]))return'cut';
 return fxPicked(X,e)&&!d.change?'done':'pick'}
const fxDrBusy=(X,e)=>{if(e.mode!=='sim'||X.src==='own')return false;const st=fxDrState(X,e);return st==='wait'||st==='cut'};
function fxDrBtnHtml(X){const d=X.dr;return `${ic('fx')} Draw ${fxCols(d.grid)} reactions with AI`}
/* the price lives on its own line above the button, never inside it: the button says what it does, the line says what it costs */
function fxDrPriceHtml(X){const d=X.dr,est=d.est[fxDrKey(d)];
 if(!d.chosen.length)return'';
 if(!est)return'about to be priced…';
 if(est.credits!=null)return`about ${est.credits} credit${est.credits===1?'':'s'}`;
 return''}
function fxDrMake(X,e){const d=X.dr,N=fxCols(d.grid),o=d.opts[d.grid]||{},names=fxDrNames(d),ch=d.chosen,est=d.est[fxDrKey(d)],ok=ch.length&&est&&est.credits!=null,prev=fxSet(e);
 return `<div class=fx-pc-draw><span class=mut>Reactions on the sheet</span><div class=tabs style="margin:0;gap:6px"><button class="tab${d.grid==='2x2'?' on':''}" data-act=fxdgrid data-v=2x2>2 x 2 · 4</button><button class="tab${d.grid==='3x3'?' on':''}" data-act=fxdgrid data-v=3x3>3 x 3 · 9</button></div></div>
  <div class=fx-dr-ideas>${!o.state||o.state==='loading'?'<span class=fx-pc-st><span class=spin></span> <span class=mut>The vision model is looking at the stickers for ideas…</span></span>'
   :o.state==='err'?`<span class=fx-warn>${esc(o.err)}</span> <button class=link data-act=fxdretry>Try again</button>`
   :`<span class=mut>Ideas${o.by?' from '+(o.by==='vlm'?'the vision model':'the built-in table'):''}: pick up to ${N}. Click a name to take it or leave it.</span>${(o.notes||[]).map(n=>`<small class=mut>${esc(n)}</small>`).join('')}`}
   <div class=fx-opts>${names.map(n=>`<button class="fx-opt${ch.includes(n)?' on':''}" data-act=fxdchip data-v="${esc(n)}" aria-pressed=${ch.includes(n)}>${esc(n)}</button>`).join('')}</div>
   <div class=row><input type=text class=fx-add data-fxdown placeholder="add your own" maxlength=40 aria-label="Add your own reaction"><button class="btn sm" data-act=fxdadd>Add</button><small class=mut data-fxdcount>${ch.length} of ${N} chosen${ch.length&&ch.length<N?' · fewer is fine, the sheet repeats them in other sizes and angles':''}</small></div></div>
  <div class=fx-pc-draw><div class=fx-price data-fxdprice>${esc(fxDrPriceHtml(X))}</div><button class="btn pri" data-act=fxddraw ${ok?'':'disabled'}>${fxDrBtnHtml(X)}</button>${est&&est.credits==null&&est.error?`<span class=fx-warn data-fxdwhy>${esc(est.error)}</span>`:'<span class=mut data-fxdwhy>Nothing is spent until you press the button.</span>'}</div>
  ${prev&&prev.status==='DRAWN'?`<div class=row><small class=mut>Drawing again replaces the reactions you picked.</small>${X.dr.redo&&fxSetGen(e)?'<button class=link data-act=fxdcancel>Keep the ones I have</button>':''}</div>`:''}`}
function fxDrCells(X,e,pick){const n=fxSetGen(e),r=X.gen[n],cells=(r&&r.stickers)||[],d=X.dr;
 if(d.use==null||d.useN!==n){const pk=(fxSet(e)||{}).picked;d.use=new Set(Array.isArray(pk)&&pk.length?pk.map(fxNum):cells.map(c=>c.index));d.useN=n}
 const sw=((r&&r.verify&&r.verify.sheet)||[]).filter(c=>!c.ok&&c.severity!=='BLOCK').map(c=>fxWords(c.name)),N=fxTicked(X,e).length;
 return `<div class=fx-pc-st><b>${cells.filter(c=>c.png).length} of ${cells.length} reactions drawn</b> <span class=mut>${esc((r||{}).generation_id||'')} · ${X.src==='drawn'?'tick the ones to use, then press Use':'all of them are used until you say otherwise: untick the ones you do not want, then press Use'}</span></div>
  ${sw.length?`<ul class=pk-pt-w>${sw.map(w=>`<li>${esc(w)}</li>`).join('')}</ul>`:''}
  <div class=fx-cells>${cells.map(c=>{const has=!!c.png,on=has&&d.use.has(c.index),why=fxCellWhy(c);
   return `<div class="fx-cell${on?' on':''}${has?'':' off'}"><button class=fx-cb data-act=fxdcell data-i=${c.index} aria-pressed=${on} ${has?'':'disabled'} title="${has?(on?'Click to leave it out':'Click to use it'):'No picture came out of this cell'}"><span class=fx-ck>${on?ic('check'):''}</span><span class=fx-cm>${has?`<img src="/out/${esc(r.generation_id)}/${esc(c.png)}" alt="" loading=lazy>`:'<span class=mut>No picture</span>'}</span></button>
    <b>${c.index}${c.key?' · '+esc(String(c.key).replace(/_/g,' ')):''}</b>${why.length?`<ul class=pk-pt-w>${why.map(w=>`<li>${esc(w)}</li>`).join('')}</ul>`:''}</div>`}).join('')}</div>
  <div class=row><button class="btn pri" data-act=fxduse ${N?'':'disabled'}>Use ${N} reaction${N===1?'':'s'}</button><button class=link data-act=fxdall data-v=1>all</button><button class=link data-act=fxdall data-v=0>none</button><button class=link data-act=fxdredo>Draw again</button>
   <span class=mut>${pick==='change'?'Pick again: ':''}the stickers burst with the ticked reactions.</span></div>`}
function fxDrDone(X,e){const n=fxSetGen(e),r=X.gen[n],s=fxSet(e)||{},idx=Array.isArray(s.picked)&&s.picked.length?s.picked.map(fxNum):fxReady(X,n).map(c=>c.index),cells=fxCells(X,n).filter(c=>idx.includes(c.index)&&c.png);
 return `<div class=fx-pc-st><b>Using ${cells.length} drawn reaction${cells.length===1?'':'s'}</b> <span class=mut>${esc((r||{}).generation_id||'')}</span></div>
  <div class=fx-pc-ths>${cells.map(c=>`<span class=fx-pc-th title="${esc(c.key||'')}"><img src="/out/${esc(r.generation_id)}/${esc(c.png)}" alt="${esc(c.key||'')}" loading=lazy></span>`).join('')}</div>
  <div class=row><button class="btn sm" data-act=fxdchange>Change which ones</button><button class="btn sm" data-act=fxdredo>Draw again</button></div>`}
function fxDrawn(X,e){const st=fxDrState(X,e),s=fxSet(e)||{},j=s.live||{};let body;
 if(st==='make')body=fxDrMake(X,e);
 else if(st==='wait')body=`<div class=fx-pc-st><span class=spin></span> <b>Drawing the reactions</b> <span class=mut>${esc(s.job||'')} · ${esc(j.stage||j.status||'waiting')}</span></div>`;
 else if(st==='failed')body=`<div class=fx-pc-st><b class=bad>The sheet failed</b><span class=mut>${esc(j.error||s.error||s.status||'')}</span></div><div class=row><button class="btn" data-act=fxdredo>Try again</button></div>`;
 else if(st==='cut')body=`<div class=fx-pc-st><span class=spin></span> <b>Cutting the reactions</b> <span class=mut>G${String(fxSetGen(e)).padStart(3,'0')}</span></div>`;
 else if(st==='pick')body=fxDrCells(X,e,X.dr.change?'change':'');
 else body=fxDrDone(X,e);
 return `<div class="fx-pc fx-dr" data-fxdr=${st}><div class=fx-pc-h><b>Drawn reactions</b><span class=mut>An AI draws a sheet of different small reactions that burst out of the stickers. It costs credits once; every change after that is free.</span></div>${body}</div>`}
/* repaint only the drawn panel (a chip, a tick) so nothing else on the page moves */
function fxDrRefresh(X){const e=X.rec,r=X.root(),el=r&&r.querySelector('.fx-dr');if(!e||!el||!X.alive())return;el.outerHTML=fxDrawn(X,e);fxDrNeeds(X)}
function fxDrNeeds(X){const e=X.rec;if(!e||e.mode!=='sim'||X.src==='own'||e.status==='NEW'||e.status==='ERROR'||fxDrState(X,e)!=='make')return;
 const s=fxSet(e),d=X.dr,o=d.opts[d.grid];       /* the ideas the server stored for this grid are not asked for again */
 if(!o&&s&&Array.isArray(s.options)&&s.options.length&&Array.isArray(s.grid)&&s.grid.join('x')===d.grid){d.opts[d.grid]={state:'ok',list:fxOptNames({options:s.options}),by:s.by||'',notes:[]};if(!d.chosen.length)d.chosen=d.opts[d.grid].list.slice(0,fxCols(d.grid));fxDrRefresh(X);return}
 fxSuggest(X);fxDrEstimate(X)}
function fxDrBtn(X){const r=X.root(),b=r&&r.querySelector('[data-act=fxddraw]');if(!b)return;const d=X.dr,est=d.est[fxDrKey(d)];
 b.innerHTML=fxDrBtnHtml(X);b.disabled=!(d.chosen.length&&est&&est.credits!=null);
 const pr=r.querySelector('[data-fxdprice]');if(pr)pr.textContent=fxDrPriceHtml(X);
 const w=r.querySelector('[data-fxdwhy]');if(w){w.className=est&&est.credits==null&&est.error?'fx-warn':'mut';w.textContent=est&&est.credits==null&&est.error?est.error:'Nothing is spent until you press the button.'}
 const c=r.querySelector('[data-fxdcount]'),N=fxCols(d.grid);if(c)c.textContent=`${d.chosen.length} of ${N} chosen${d.chosen.length&&d.chosen.length<N?' · fewer is fine, the sheet repeats them in other sizes and angles':''}`}
async function fxSuggest(X,force){const d=X.dr,g=d.grid,o=d.opts[g]=d.opts[g]||{},eid=X.eid;if(!eid||o.state==='loading'||(o.state&&!force))return;
 o.state='loading';fxDrRefresh(X);
 const r=await post(`/api/effects/${eid}/suggest`,{grid:g,allow_vlm:vlmState()==='1'});if(X.eid!==eid)return;
 if(r.ok){o.list=fxOptNames(r.j);o.by=r.j.by||'';o.notes=r.j.notes||[];o.state='ok';if(!d.chosen.length)d.chosen=o.list.slice(0,fxCols(g))}else{o.state='err';o.err=r.j.error||'Could not get ideas'}
 fxDrRefresh(X)}
async function fxDrEstimate(X){const d=X.dr,k=fxDrKey(d),eid=X.eid;if(!eid||!d.chosen.length||d.est[k]||d.busy[k])return;d.busy[k]=1;
 const r=await post(`/api/effects/${eid}/particles_estimate`,{grid:d.grid,elements:d.chosen});d.busy[k]=0;
 d.est[k]=r.ok?{credits:r.j.credits!=null?r.j.credits:null,error:r.j.cost_error||(r.j.credits==null?'The price is not available.':null)}:{credits:null,error:r.j.error||'The price is not available.'};
 if(X.eid===eid&&k===fxDrKey(X.dr))fxDrBtn(X)}
ACT.fxdgrid=el=>{const X=fxX(el),d=X.dr;d.grid=el.dataset.v;d.chosen=d.chosen.slice(0,fxCols(d.grid));fxDrRefresh(X)};
ACT.fxdretry=el=>{const X=fxX(el);fxSuggest(X,true)};
ACT.fxdchip=el=>{const X=fxX(el),d=X.dr,v=el.dataset.v,i=d.chosen.indexOf(v);
 if(i>=0)d.chosen.splice(i,1);else if(d.chosen.length>=fxCols(d.grid))return toast(`A ${d.grid==='3x3'?'3 x 3':'2 x 2'} sheet holds ${fxCols(d.grid)}: take one off first`,1);else d.chosen.push(v);
 const on=d.chosen.includes(v);el.classList.toggle('on',on);el.setAttribute('aria-pressed',on);fxDrBtn(X);clearTimeout(X.pvT._est);X.pvT._est=setTimeout(()=>fxDrEstimate(X),350)};
function fxDrAdd(X,v){v=String(v||'').trim().slice(0,40);if(!v)return;const d=X.dr;if(!fxDrNames(d).includes(v))d.extra.push(v);if(!d.chosen.includes(v)&&d.chosen.length<fxCols(d.grid))d.chosen.push(v);fxDrRefresh(X);clearTimeout(X.pvT._est);X.pvT._est=setTimeout(()=>fxDrEstimate(X),350)}
ACT.fxdadd=el=>{const X=fxX(el),i=X.root().querySelector('[data-fxdown]');fxDrAdd(X,i?i.value:'')};
document.addEventListener('keydown',e=>{const t=e.target;if(e.key==='Enter'&&t&&t.dataset){if(t.dataset.fxadd){e.preventDefault();fxAddPiece(fxX(t),t.dataset.fxadd,t.value)}else if(t.dataset.fxdown!==undefined){e.preventDefault();fxDrAdd(fxX(t),t.value)}}});
ACT.fxddraw=async el=>{const X=fxX(el),d=X.dr,est=d.est[fxDrKey(d)];if(!est||est.credits==null||!d.chosen.length)return;el.disabled=true;
 const r=await post(`/api/effects/${X.eid}/particles`,{grid:d.grid,elements:d.chosen,go:true});if(!r.ok){el.disabled=false;return toast(r.j.error||'Could not start',1)}
 d.redo=false;d.change=false;d.picked='';d.use=null;toast(`Drawing ${fxCols(d.grid)} reactions: ${r.j.estimate!=null?r.j.estimate+' credits':'the price is shown in your ledger'}`);await fxLoad(X);X.draw()};
ACT.fxdredo=el=>{const X=fxX(el);X.dr.redo=true;X.dr.change=false;fxDrRefresh(X)};
ACT.fxdcancel=el=>{const X=fxX(el);X.dr.redo=false;fxDrRefresh(X)};
ACT.fxdchange=el=>{const X=fxX(el);X.dr.change=true;X.dr.use=null;fxDrRefresh(X)};
ACT.fxdcell=el=>{const X=fxX(el),d=X.dr,i=+el.dataset.i;d.use.has(i)?d.use.delete(i):d.use.add(i);fxDrRefresh(X)};
ACT.fxdall=el=>{const X=fxX(el),e=X.rec,d=X.dr;d.use=new Set(el.dataset.v==='1'?fxCells(X,fxSetGen(e)).filter(c=>c.png).map(c=>c.index):[]);fxDrRefresh(X)};
ACT.fxduse=async el=>{const X=fxX(el),d=X.dr,idx=fxTicked(X,X.rec);if(!idx.length)return;el.disabled=true;
 const r=await post(`/api/effects/${X.eid}/particles_pick`,{indexes:idx});if(!r.ok){el.disabled=false;return toast(r.j.error||'Could not use them',1)}
 d.picked=X.rec.id+':'+fxSetGen(X.rec);d.change=false;await fxLoad(X);X.draw();toast(`Using ${idx.length} reaction${idx.length===1?'':'s'}: the stickers burst with them`)};

/* ---------- the body of an effect: the drawn particles, the way to a set and the set's Motion (a simulation), or per group its elements (a video), then the results */
function fxGroupHtml(X,e,g){
 return `<section class=fx-g><header><b>${esc(g.subject)}</b><span class="fx-key ${esc(g.key)}">screen ${esc(g.key)}</span><small class=mut>reactions chosen by ${g.by==='vlm'?'the vision model':g.by==='you'?'you':'the built-in table'}</small></header>
  ${fxPieces(X,g)}<div class=fx-gst>${g.stickers.map(sid=>{const s=fxSticker(e,sid);return s?`<span class=fx-th title="${esc(s.name)}">${media(s)}</span>`:''}).join('')}</div>${fxVideoPanel(X,e,g)}</section>`}
function fxResults(X,e){const rs=e.results||[];if(!rs.length)return '';const n=X.pick.size;
 return `<details class=fx-original><summary>Original sprite clips</summary><div class=sh style="margin-top:22px">Results <button class=link data-act=fxpickall>select all that can be added</button></div><div class=fx-res>${rs.map(r=>{
  const ok=r.status==='READY',ws=(r.checks||[]).filter(c=>c.verdict==='WARN'),bl=(r.checks||[]).filter(c=>c.verdict==='BLOCK'),on=X.pick.has(r.id);
  return `<div class="fx-r${on?' on':''}${ok?'':' bad'}"><div class=fx-rv>${r.file?`<video src="/out/effects/${e.id}/${r.file}" autoplay loop muted playsinline></video>`:'<span class=mut>no file</span>'}</div>
   <div class=fx-rm><b>${esc(r.id)} · ${r.mode==='video'?'cell '+r.cell:esc((fxSticker(e,r.sticker_id)||{}).name||'')}</b><small>${Math.round((r.bytes||0)/1024)} KB · ${esc(String(r.status).toLowerCase())}</small>
   ${bl.map(c=>`<span class="fx-w bad" title="${esc(c.detail||'')}">${esc(fxWords(c.id))}</span>`).join('')}${ws.map(c=>`<span class=fx-w title="${esc(c.detail||'')}">${esc(fxWords(c.id))}</span>`).join('')}
   ${r.added_to?'<small class=mut>added to the pack</small>':''}${ok?`<button class="btn sm${on?' pri':''}" data-act=fxpick data-id=${r.id}>${on?'Selected':ws.length?'Use it anyway':'Use'}</button>`:''}</div></div>`}).join('')}</div>
  <div class=row><button class="btn pri gbig" data-act=fxadd ${n?'':'disabled'}>Add ${n||''} sprite clips</button><span class=mut>Warnings are only warnings: you decide. A reaction sticker keeps the emoji of its source and is saved under that sticker.</span></div></details>`}
function fxBody(X,e){const sim=e.mode==='sim';
 return (e.notes||[]).map(n=>`<div class=mut style="margin-top:6px">${esc(n)}</div>`).join('')+(sim&&X.src!=='own'?fxDrawn(X,e):'')+(sim?fxSaveBlock(X,e)+fxMotion(X,e):(e.groups||[]).map(g=>fxGroupHtml(X,e,g)).join('')+fxSaveBlock(X,e)+fxMotion(X,e))+fxResults(X,e)}
/* Motion (a simulation): ONE section for the whole run, whatever its stickers or groups (docs/architecture.md ("Reactions")). The burst is made from the SET for the PACK, so it waits for the run to be saved as a set;
   then the panel is the set's own (spBurstHtml: one preview, the presets, Energy / Float / Swirl, Render, Add). */
function fxMotion(X,e){const s=X.set;
 if(!s)return `<section class="fx-g fx-mo"><header><b>Motion</b></header><div class=mut>Save the reactions as a set first (above): the burst is made from the set, for the whole pack, with one preview.</div></section>`;
 if(typeof spBurstState==='function')spBurstState(s,e.pack_id);
 return `<section class="fx-g fx-mo"><header><b>${esc(s.name||s.id)}</b><span class=mut>${esc(s.id)} · ${s.n_picked||0} picked · ${(s.used_in||[]).length?'used in '+s.used_in.map(p=>esc(p.name||p.id)).join(', '):'stand-alone'}</span></header>${typeof spBurstHtml==='function'?spBurstHtml(s):''}</section>`}
function fxRecord(){const e=FX.rec;if(!e)return '';
 const head=`<div class=ph><h1>${esc(e.title||e.id)}</h1><small class=mut>${esc(e.id)} · ${e.mode==='sim'?'simulated':'video from scratch'}</small></div><button class=link data-act=fxback>${ic('back')} all effects</button>`;
 if(e.status==='NEW')return head+`<div class=fx-job style="margin-top:16px"><span class=spin></span> <b>Looking at your stickers…</b> <span class=mut>choosing what bursts out of each</span></div>`;
 if(e.status==='ERROR')return head+`<div class=warn>${esc(e.error||'The analysis failed')} <button class=btn data-act=fxback>Back</button></div>`;
 return head+fxBody(FX,e)}
function fxDraw(){const el=$('s-effects');if(!el||!FX.eid)return;el.innerHTML=`<div class="page fx" data-fxx=fx>${fxRecord()}</div>`;FX.last=fxSig(FX);fxAfterDraw(FX)}
/* what a freshly drawn effect asks for: the ideas and the price of the drawn sheet, the first preview of the set's burst, the price of a video */
function fxAfterDraw(X){if(X.who==='sp'&&typeof spAfterDraw==='function')return spAfterDraw();const e=X.rec;if(!e||!['READY','RESULTS','VIDEO_REQUESTED','DONE'].includes(e.status))return;
 if(e.mode==='sim')fxDrNeeds(X);if(X.set){if(X.set&&typeof spBurstPreview==='function'){const b=typeof SPB!=='undefined'?SPB[X.set.id]:null;if(!b||!b.pv)spBurstPreview(X.set.id)}}
 if(e.mode==='video')for(const g of e.groups)fxEstimate(X,g.id)}
async function fxEstimate(X,gid){const e=X.rec,k=gid+e.grid.join('x');if(X.est[k]||X.busy[k])return;X.busy[k]=1;
 const r=await post(`/api/effects/${e.id}/estimate`,{group:gid,...(X.who==='sp'&&X.note?{elements:spPromptElements(X.note,X.grid),subject:X.note}: {})});X.busy[k]=0;
 X.est[k]=r.ok?r.j:{credits:null};if(X.alive()&&X.rec&&X.rec.id===e.id){if(X.who==='sp')return X.draw();const b=X.root().querySelector(`[data-fxest=${gid}]`);if(b&&r.ok&&r.j.credits!=null){b.textContent=`about ${r.j.credits} credits`;const btn=X.root().querySelector(`[data-act=fxvideo][data-g="${gid}"]`);if(btn)btn.disabled=false}}}
ACT.fxprice=el=>{const X=fxX(el);delete X.est[el.dataset.g+X.rec.grid.join('x')];fxEstimate(X,el.dataset.g).then(()=>X.draw())};

/* ---------- editing the elements of a video */
async function fxPlan(X,body){const r=await post(`/api/effects/${X.eid}/plan`,body);if(!r.ok)return toast(r.j.error||'Not possible',1);X.rec=r.j;X.est={};await fxLoad(X);X.draw()}
ACT.fxdelpiece=el=>{const X=fxX(el),g=X.rec.groups.find(x=>x.id===el.dataset.g);if(!g)return;const els=g.elements.filter((_,i)=>i!==+el.dataset.i);if(!els.length)return toast('Keep at least one reaction',1);fxPlan(X,{group:g.id,elements:els})};
function fxAddPiece(X,gid,value){const g=X.rec.groups.find(x=>x.id===gid),v=String(value||'').trim();if(!g||!v)return;fxPlan(X,{group:g.id,elements:g.elements.concat(v)})}
ACT.fxaddpiece=el=>{const X=fxX(el),i=X.root().querySelector(`[data-fxadd="${el.dataset.g}"]`);fxAddPiece(X,el.dataset.g,i?i.value:'')};
ACT.fxvideo=async el=>{const X=fxX(el),g=el.dataset.g,est=X.est[g+X.rec.grid.join('x')];if(!est||est.credits==null)return;el.disabled=true;
 const r=await post(`/api/effects/${X.eid}/video`,{group:g,go:true});if(!r.ok){el.disabled=false;return toast(r.j.error||'Could not start',1)}
 toast(`Started: ${r.j.estimate!=null?r.j.estimate+' credits':'the price is shown in your ledger'}`);await fxLoad(X);X.draw()};

/* ---------- the burst's slider values (the burst maker is particles.js's): the keys of a params object the sliders show, and the values the person touched */
const fxPick=p=>{const o={seed:p.seed};FXSL.forEach(([k])=>o[k]=p[k]);return o};
const fxClean=p=>{const o={};if(p)Object.entries(p).forEach(([k,v])=>{if(k!=='touched'&&v!==''&&v!==undefined)o[k]=+v});return o};

/* ---------- results -> the pack (and under the sticker: its Particles section shows them) */
ACT.fxpick=el=>{const X=fxX(el),i=el.dataset.id;X.pick.has(i)?X.pick.delete(i):X.pick.add(i);X.draw()};
ACT.fxpickall=el=>{const X=fxX(el);(X.rec.results||[]).filter(r=>r.status==='READY'&&!r.added_to).forEach(r=>X.pick.add(r.id));X.draw()};
ACT.fxadd=async el=>{const X=fxX(el),r=await post(`/api/effects/${X.eid}/add`,{results:[...X.pick]});if(!r.ok)return toast(r.j.error||'Could not add',1);
 toast(`Added ${r.j.added.length} reaction sticker${r.j.added.length===1?'':'s'} to the pack: saved under the sticker${r.j.added.length===1?'':'s'}`);X.pick.clear();await loadLib();if(typeof ptForget==='function')ptForget();await fxLoad(X);X.draw();if(typeof spSecSync==='function')spSecSync(true)};

/* ---------- Use as particle set: the working session ends in a durable set P### (docs/architecture.md ("Reactions")). POST /api/particles {from_effect, name, packs, picked?}.
   One implementation for the pro screen and the Studio tab (the block sits in fxBody, the state comes from the root). A drawn sheet is saved with its picked cells (the set outlives the run); a run
   that drew nothing can save the pack's own stickers as the set (free). Either way the run goes on to Motion below with that set. Nothing is moved or deleted: the run stays as it is. */
const fxGen=n=>'G'+String(n).padStart(3,'0');
/* is the set saved from this run the CURRENT drawing? (a newer sheet is a new set to keep) */
function fxSaved(X,e){const t=X.set;if(!t)return false;const n=fxSetGen(e),s=fxSet(e);
 if(s&&s.status==='DRAWN'&&n&&Array.isArray(s.picked)&&s.picked.length)return (t.source||{}).generation===fxGen(n);
 return true}
function fxSaveBlock(X,e){if(fxSaved(X,e))return '<div class=fx-save><button class="btn pri" data-act=fxopenset>Open in simulator</button></div>';
 if(e.mode==='video'){const n=(e.results||[]).filter(r=>r.mode==='video'&&r.file).length;return n?`<div class=fx-save><b>From Kling video · ${n} cells</b><button class="btn pri" data-act=fxuseset data-v=video>Use in simulator · free</button></div>`:'';}
 const s=fxSet(e),n=s&&s.status==='DRAWN'&&Array.isArray(s.picked)?s.picked:[];
 if(n.length)return `<div class=fx-save><b>Keep these reactions</b><button class="btn pri" data-act=fxuseset data-v=drawn>Use in simulator · free</button></div>`;
 return `<div class=fx-save><b>Or use the pack’s own stickers</b><span class=mut>Free. Nothing is drawn: the stickers of ${esc(e.pack_name||'the pack')} fly out as the particles.</span><button class="btn" data-act=fxuseset data-v=stickers>Use the pack’s stickers as the set</button></div>`}
const fxPendingSet={eid:'',who:'fx',picked:null};
function fxOpenSimulator(X,set,owners){const e=X.rec;if(typeof spEnter==='function'){spEnter({pack_id:e.pack_id,sticker_ids:owners.map(o=>o.sticker_id)});SP.entry=(SP.entry||0)+1;SP.set=set;SP.target=set.id;SP.eid='';SPL.detail[set.id]=set;spDraw();spBurstPreview(set.id);return}X.set=set;X.draw()}
ACT.fxopenset=el=>{const X=fxX(el);if(!X.set)return;const owners=X.set.owner||[];fxOpenSimulator(X,X.set,owners.length?owners:(X.rec.stickers||[]).map(st=>({pack_id:X.rec.pack_id,sticker_id:st.sticker_id})))};
async function fxUseSetDlg(X,kind){const e=X.rec,s=fxSet(e);if(!e)return;
 const owners=X.who==='sp'?spOwners(SP):(e.stickers||[]).map(st=>({pack_id:e.pack_id,sticker_id:st.sticker_id}));
 const picked=kind==='drawn'&&s?s.picked:kind==='video'?(e.results||[]).filter(r=>r.file).map(r=>r.cell):null;
 const r=await post('/api/particles',{from_effect:e.id,owners,target:X.target||undefined,...(picked?{picked}:{})});
 if(!r.ok)return toast(r.j.error||'Could not use the sprites',1);
 if(typeof spSetsForget==='function')spSetsForget();await loadLib();
 fxOpenSimulator(X,r.j,owners)}
ACT.fxuseset=el=>fxUseSetDlg(fxX(el),el.dataset.v);
ACT.psusesave=()=>fxUseSetDlg(FXS[fxPendingSet.who],'drawn');
