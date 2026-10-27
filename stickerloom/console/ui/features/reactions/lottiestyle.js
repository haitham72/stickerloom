/* Reaction effects, Lottie style: every file in lottie/ is a reaction whose particles are that whole Lottie animation.
   Emoji style (reactions.js) stays; the Style switch on #/reactions picks one (kept in the browser). The viewer plays the EXACT export JSON
   (POST .../{file}/preview with the editor's unsaved values) in the bundled lottie-web player, 512 x 512, so viewer = export.
   Routes: GET /api/particles/lottie-reactions, POST .../{file} (save), POST .../{file}/preview, GET .../{file}/export.json, GET .../lottie-reactions.zip.
   Engine fields: engine/particles.py ParticleParams; settings list: flow/lottie_reactions.py SETTINGS. */

/* [key, label, min, max, step, unit, choices?], in Trapcode Particular's groups:
   Emitter -> Particle -> Physics -> Aux system, plus stickerloom's Morph. A setting with choices is a drop-down (its value is the choice's index). */
const LSC={emitter:['Point','Ring','Box','Line'],size_curve:['None','Grow','Shrink','Swell','Pulse'],opacity_curve:['None','Fade in','Flicker','Blink out'],
 time_sampling:['Start at birth, once','Loop','Random start, loop','Random still'],aux_at:['At the morph','At death','At bounce','Trail (along its life)']};
const LSG=[
 ['Emitter',[['emitter','Type',0,3,1,'',LSC.emitter],['origin_x','Position X',0,1,.01,'%'],['origin_y','Position Y',0,1,.01,'%'],['emitter_w','Emitter size X',0,1,.01,'%'],['emitter_h','Emitter size Y',0,1,.01,'%'],
  ['count','Particles',4,80,1,''],['emit_dur','Stream for',0,2,.05,'s'],['direction','Direction',-180,180,5,'°'],['spread','Direction spread',10,360,5,'°'],
  ['magnitude','Velocity',0,3,.05,'×'],['speed_var','Velocity random',0,1,.05,'%'],['vel_dist','Velocity distribution',0,1,.05,'%']]],
 ['Particle',[['lifetime','Life',.3,1.6,.05,'s'],['life_var','Life random',0,1,.05,'%'],['particle_size','Size',.25,3,.05,'×'],['size_var','Size random',0,1,.05,'%'],
  ['size_curve','Size over life',0,4,1,'',LSC.size_curve],['size_in_dur','Size in',0,1.5,.05,'s'],['size_out_dur','Size out',0,1.5,.05,'s'],
  ['opacity_curve','Opacity over life',0,3,1,'',LSC.opacity_curve],['fade_start','Fade out start',0,2,.05,'s'],['fade_dur','Fade out duration',0,2,.05,'s'],['fade_var','Fade out random',0,1,.05,'s'],
  ['spin','Rotation',0,3,.05,'×'],['flip','Flip (cheap spin)',0,4,.05,'/s'],['flip_var','Flip random',0,1,.05,'%'],['time_sampling','Time sampling',0,3,1,'',LSC.time_sampling]]],
 ['Physics',[['gravity','Gravity',-2,3,.05,''],['air','Air resistance',0,3,.05,''],['wind_x','Wind X',-2,2,.05,''],['wind_y','Wind Y',-2,2,.05,''],['vortex','Spin field',-2,2,.05,''],
  ['turbulence','Turbulence: position',0,2,.05,''],['turb_size','Turbulence: size',0,1,.05,'%'],['turb_scale','Turbulence scale',.5,6,.1,''],['turb_speed','Evolution speed',0,3,.05,''],
  ['speed_pulse','Speed pulse',0,1,.05,'%'],['pulse_hz','Pulse rate',.2,6,.1,'/s'],['bounce','Bounce',0,1,.05,'%'],['floor_y','Floor',.3,1,.01,'%'],['slide','Slide',0,1,.05,'%']]],
 ['Morph',[['morph_at','Morph at',0,2,.05,'s'],['morph_var','Morph random',0,1,.05,'s']]],
 ['Aux system',[['aux_at','Emit',0,3,1,'',LSC.aux_at],['children','Aux particles',0,12,1,''],['child_size','Aux size',.1,3,.05,'×'],['child_size_var','Aux size random',0,1,.05,'%'],
  ['aux_inherit','Inherit velocity',0,1,.05,'%'],['aux_life','Aux life',0,2,.05,'s']]]];
const LSOPEN=(()=>{try{return new Set(JSON.parse(localStorage.getItem('ls.open')||'["Emitter"]'))}catch(e){return new Set(['Emitter'])}})();
const LSO=LSG.flatMap(g=>g[1]);
const LS={files:null,start:null,sel:'',ed:null,doc:null,anim:null,t:0,err:'',busy:false,seq:0,lib:null};
const rxTab=()=>RXS.tab;
const rxStyleBar=()=>{const s=rxTab();
 return `<div class=tabs role=tablist aria-label="Reaction style"><button class="tab ${s==='emoji'?'on':''}" data-act=rxstyle data-s=emoji role=tab aria-selected=${s==='emoji'}>Emoji style</button>`+
  `<button class="tab ${s==='lottie'?'on':''}" data-act=rxstyle data-s=lottie role=tab aria-selected=${s==='lottie'}>Lottie style</button>`+
  `<button class="tab ${s==='super'?'on':''}" data-act=rxstyle data-s=super role=tab aria-selected=${s==='super'}>Super Emojis</button>`+
  `<button class="tab ${s==='packs'?'on':''}" data-act=rxstyle data-s=packs role=tab aria-selected=${s==='packs'}>Packs</button></div>`};
const lsFile=f=>(LS.files||[]).find(x=>x.file===f);
const lsName=f=>String(f||'').replace(/\.json$/i,'');

/* ---------- markup ---------- */
function lsHtml(){
 if(!LS.files)return `<div class=fx-job><span class=spin></span> <span class=mut>Reading lottie/…</span></div>`;
 return `${lsEditor()}<section class=rx-made data-lsfiles><div class=rx-ph><div class=sh>Lottie files <small class=mut>${LS.files.length}</small></div>`+
  `<small class=mut>Every <code>.json</code> in <code>lottie/</code>; click one to make it a reaction</small><span class=sp></span>`+
  `<button class="btn sm" data-act=lszip title="Every file with its saved settings, one self-contained Lottie each, plus manifest.json">${ic('download')} Export all (.zip)</button></div>`+
  (LS.files.length?`<div class=ls-files>${LS.files.map(f=>`<button class="ls-f${LS.sel===f.file?' on':''}" data-act=lspick data-f="${esc(f.file)}" aria-pressed=${LS.sel===f.file} title="${esc(f.name)} · ${f.w}×${f.h} · ${f.fps} fps · ${f.frames} frames">`+
   `${lsThumb(f.file)}<b>${esc(f.name)}</b><small class=mut>${Math.round(f.frames/(f.fps||30)*10)/10} s${f.saved?' · saved':''}</small></button>`).join('')}</div>`
   :`<div class=mut>No files yet: drop Lottie .json files into <code>lottie/</code>.</div>`)+`</section>`}
function lsSlider(o){const v=LS.ed.settings[o[0]];
 if(o[6])return `<label class=rx-sl><span class=mut>${o[1]}</span><select data-lso=${o[0]} aria-label="${o[1]}">${o[6].map((c,k)=>`<option value=${k} ${+v===k?'selected':''}>${c}</option>`).join('')}</select></label>`;
 return `<label class=rx-sl><span class=mut>${o[1]}</span><input type=range min=${o[2]} max=${o[3]} step=${o[4]} value="${v}" data-lso=${o[0]} aria-label="${o[1]}"><output>${rxFmt(o,v)}</output></label>`}
/* a closed group says what in it differs from the start, so nothing hides */
function lsSummary(os){const st=LS.start||{},d=os.filter(o=>LS.ed.settings[o[0]]!=null&&+LS.ed.settings[o[0]]!==+st[o[0]]);
 return d.length?d.slice(0,3).map(o=>`${o[1].toLowerCase()} ${o[6]?o[6][+LS.ed.settings[o[0]]].toLowerCase():rxFmt(o,LS.ed.settings[o[0]])}`).join(' · ')+(d.length>3?` · +${d.length-3}`:''):'as default'}
const lsPresets=()=>`<div class=ls-pre><small class=mut>Presets</small>${Object.keys(LS.presets||{}).map(n=>`<button class="btn sm" data-act=lspreset data-p="${esc(n)}" title="Fill the sliders with ${esc(n)} (the file and its morph targets stay)">${esc(n)}</button>`).join('')}</div>`;
/* a file's own animation as a small tile picture: lottie-web, still on its middle frame, playing while pointed at (lsThumbs mounts them) */
const lsThumb=f=>`<span class=ls-th data-lsthumb="${esc(f)}" aria-hidden=true></span>`;
function lsTargets(){const others=(LS.files||[]).filter(f=>f.file!==LS.sel),b=+LS.ed.settings.morph_burst?1:0;
 const mode=`<div class=tabs role=tablist aria-label="At the morph"><button class="tab ${b?'':'on'}" data-act=lsmode data-v=0 aria-selected=${!b}>Turns into (1:1)</button>`+
  `<button class="tab ${b?'on':''}" data-act=lsmode data-v=1 aria-selected=${!!b}>Bursts into children</button></div>`;
 if(!others.length)return mode+'<small class=mut>Drop a second file to morph into it.</small>';
 return mode+`<div class=ls-tg>${others.map(f=>{const on=LS.ed.morph_targets.includes(f.file);
   return `<button class="ls-f sm${on?' on':''}" data-act=lstg data-f="${esc(f.file)}" aria-pressed=${on} title="${esc(f.name)}">${lsThumb(f.file)}<b>${esc(f.name)}</b></button>`}).join('')}</div>`+
  `<small class=mut>${b?'At Morph at each particle fades out and its Children burst out as one of the picked files.':'At Morph at each particle turns into one of the picked files; Children (if any) burst out of it there.'} Morph at 0 = no morph.</small>`}
const lsStage=()=>`<div class=ls-player data-lsplayer></div>${LS.doc?rxDbgHtml([.5,.5]):''}${LS.doc||LS.err?'':'<span class=spin></span>'}`;
function lsEditor(){
 if(!LS.ed)return '<div data-lsed></div>';
 const f=lsFile(LS.sel)||{name:lsName(LS.sel)};
 return `<section class=rx-ed data-lsed><div class=rx-stage data-lsstage>${lsStage()}</div><div class=rx-ctl>
  <div class=rx-h><b>${esc(f.name)}</b><small class=mut>2 s · 512 × 512${LS.doc?` · ${LS.doc.layers.length} layers`:''}</small>${rxDbgToggle()}<button class=link data-act=lsclose aria-label="Close">${ic('x')}</button></div>
  ${lsPresets()}
  <div class=rx-groups>${LSG.map(([t,os])=>`<details class=rx-grp data-lsg="${t}" ${LSOPEN.has(t)?'open':''}><summary class=rx-gh><b>${t}</b><small class=mut>${lsSummary(os)}</small></summary>${os.map(lsSlider).join('')}${t==='Morph'?lsTargets():''}</details>`).join('')}
   <div class=rx-grp><label class=rx-sl><span class=mut>Random seed</span><b>${LS.ed.settings.seed}</b><button class="btn sm" data-act=lsseed title="The same settings with other random draws">Re-roll</button></label></div></div>
  ${LS.err?`<span class=fx-warn data-lserr>${esc(LS.err)}</span>`:'<span data-lserr></span>'}
  <div class=rx-foot><button class="btn pri" data-act=lssave ${LS.busy?'disabled':''}>Save</button><button class=btn data-act=lsdl ${LS.doc?'':'disabled'}>${ic('download')} JSON</button>`+
  `<button class=link data-act=lsreset>Reset to default</button></div></div></section>`}

/* ---------- the player (lottie-web, bundled: no CDN) ---------- */
let lsLib=null;
LS.raw={};LS.th=[];
const lsRoot=()=>$(typeof route_!=='undefined'&&route_==='library'?'s-library':'s-reactions');
async function lsThumbs(){
 LS.th.forEach(a=>{try{a.destroy()}catch(e){}});LS.th=[];
 const root=lsRoot(),boxes=[...(root&&root.querySelectorAll?root.querySelectorAll('[data-lsthumb]'):[])];if(!boxes.length)return;
 const L=await lsLottie().catch(()=>null);if(!L)return;
 for(const box of boxes){const f=box.dataset.lsthumb;
  if(!LS.raw[f]){const r=await api(rxHpUrl(f));LS.raw[f]=r.ok?r.j:null}
  if(!LS.raw[f]||!box.isConnected||box.firstChild)continue;
  const a=L.loadAnimation({container:box,renderer:'svg',loop:true,autoplay:false,animationData:JSON.parse(JSON.stringify(LS.raw[f]))});
  const mid=()=>a.goToAndStop(Math.floor((a.totalFrames||2)/2),true);
  a.addEventListener('DOMLoaded',mid);LS.th.push(a);
  const host=box.closest('button')||box;host.addEventListener('mouseenter',()=>a.play());host.addEventListener('mouseleave',mid)}}
function lsLottie(){if(window.lottie)return Promise.resolve(window.lottie);
 if(!lsLib)lsLib=new Promise((ok,no)=>{const s=document.createElement('script');s.src='/ui/vendor/lottie-5.12.2.min.js';s.onload=()=>ok(window.lottie);s.onerror=no;document.head.appendChild(s)});
 return lsLib}
async function lsMount(){
 const root=lsRoot(),box=root&&root.querySelector&&root.querySelector('[data-lsplayer]'),doc=LS.doc,n=LS.seq;
 if(LS.anim){try{LS.anim.destroy()}catch(e){}LS.anim=null}
 if(!box||!LS.doc)return;
 const L=await lsLottie().catch(()=>null);if(!L){LS.err='The Lottie player could not be loaded.';return lsEdSync()}
 if(!box.isConnected||root!==lsRoot()||n!==LS.seq||doc!==LS.doc)return;
 LS.anim=L.loadAnimation({container:box,renderer:'svg',loop:true,autoplay:true,animationData:JSON.parse(JSON.stringify(doc))})}

/* ---------- server ---------- */
async function lsLoad(){const r=await api('/api/particles/lottie-reactions');
 if(r.ok){LS.files=r.j.files||[];LS.start=r.j.start||{};LS.presets=r.j.presets||{}}else{LS.files=[];LS.err=r.j.error||'The Lottie files could not be read.'}}
async function lsPreview(){
 if(!LS.ed)return;const n=++LS.seq;
 const r=await post(`/api/particles/lottie-reactions/${encodeURIComponent(LS.sel)}/preview`,{settings:LS.ed.settings,morph_targets:LS.ed.morph_targets});
 if(n!==LS.seq||!LS.ed)return;
 if(!r.ok){LS.err=r.j.error||'The preview could not be made.';return lsEdSync()}
 LS.err='';LS.doc=r.j;lsEdSync(true)}
const lsSoon=()=>{clearTimeout(LS.t);LS.t=setTimeout(lsPreview,250)};
function lsEdSync(remount){
 const root=lsRoot(),ed=root&&root.querySelector&&root.querySelector('[data-lsed]');if(!ed)return rxDraw();
 const h=ed.querySelector('.rx-h small');if(h)h.textContent=`2 s · 512 × 512${LS.doc?` · ${LS.doc.layers.length} layers`:''}`;
 const e=ed.querySelector('[data-lserr]');if(e){e.className=LS.err?'fx-warn':'';e.textContent=LS.err}
 const dl=ed.querySelector('[data-act=lsdl]');if(dl)dl.disabled=!LS.doc;
 if(remount){const st=ed.querySelector('[data-lsstage]');if(st){st.innerHTML=lsStage();lsMount()}}}
function lsOpen(file){const f=lsFile(file);if(!f)return;
 clearTimeout(LS.t);++LS.seq;
 LS.sel=file;LS.ed={settings:{...(LS.start||{}),...(f.settings||{})},morph_targets:[...(f.morph_targets||[])]};LS.doc=null;LS.err='';rxDraw();lsPreview()}

/* ---------- actions ---------- */
ACT.rxstyle=el=>{const s=el.dataset.s;RXS.tab=s==='lottie'||s==='packs'||s==='super'?s:'emoji';try{localStorage.setItem('rx.tab',RXS.tab)}catch(e){}
 if(typeof route_!=='undefined'&&route_==='library'){if(typeof spLibRefresh==='function')spLibRefresh();}
 else if(typeof RENDER!=='undefined'&&RENDER.reactions)RENDER.reactions();};
ACT.lspick=el=>lsOpen(el.dataset.f);
ACT.lstg=el=>{if(!LS.ed)return;const f=el.dataset.f,m=LS.ed.morph_targets.filter(x=>x!==f);if(!LS.ed.morph_targets.includes(f))m.push(f);LS.ed.morph_targets=m;rxDraw();lsPreview()};
ACT.lsmode=el=>{if(!LS.ed)return;LS.ed.settings.morph_burst=+el.dataset.v?1:0;rxDraw();lsPreview()};
ACT.lsclose=()=>{clearTimeout(LS.t);++LS.seq;LS.sel='';LS.ed=null;LS.doc=null;if(LS.anim){try{LS.anim.destroy()}catch(e){}LS.anim=null}rxDraw()};
ACT.lspreset=el=>{if(!LS.ed)return;const pr=(LS.presets||{})[el.dataset.p];if(!pr)return;LS.ed.settings={...(LS.start||{}),...pr,seed:LS.ed.settings.seed};rxDraw();lsPreview()};
ACT.lsseed=()=>{if(!LS.ed)return;LS.ed.settings.seed=1+Math.floor(Math.random()*99999);rxDraw();lsPreview()};
document.addEventListener('toggle',ev=>{const d=ev.target;if(!d||!d.dataset||d.dataset.lsg===undefined)return;
 if(d.open)LSOPEN.add(d.dataset.lsg);else LSOPEN.delete(d.dataset.lsg);try{localStorage.setItem('ls.open',JSON.stringify([...LSOPEN]))}catch(e){}},true);
ACT.lsreset=()=>{if(!LS.ed)return;LS.ed.settings={...(LS.start||{})};LS.ed.morph_targets=[];rxDraw();lsPreview()};
ACT.lssave=async()=>{if(!LS.ed||LS.busy)return;LS.busy=true;
 const r=await post(`/api/particles/lottie-reactions/${encodeURIComponent(LS.sel)}`,{settings:LS.ed.settings,morph_targets:LS.ed.morph_targets});LS.busy=false;
 if(!r.ok){LS.err=r.j.error||'Could not save.';return lsEdSync()}
 const f=lsFile(LS.sel);if(f){f.saved=true;f.settings=r.j.settings;f.morph_targets=r.j.morph_targets}toast(`Saved ${lsName(LS.sel)}`);rxDraw()};
ACT.lsdl=()=>{if(!LS.doc)return;const a=document.createElement('a');
 a.href=URL.createObjectURL(new Blob([JSON.stringify(LS.doc)],{type:'application/json'}));a.download=`${lsName(LS.sel)}-reaction.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),4000)};
ACT.lszip=()=>{const a=document.createElement('a');a.href='/api/particles/lottie-reactions.zip';a.download='lottie-reactions.zip';a.click()};
document.addEventListener('input',ev=>{const t=ev.target;if(!t||!t.dataset||t.dataset.lso===undefined||!LS.ed)return;
 const o=LSO.find(x=>x[0]===t.dataset.lso);if(!o)return;LS.ed.settings[o[0]]=+t.value;
 const out=t.parentNode.querySelector('output');if(out)out.textContent=rxFmt(o,+t.value);
 const g=t.closest&&t.closest('details[data-lsg]'),sm=g&&g.querySelector('summary small'),grp=g&&LSG.find(x=>x[0]===g.dataset.lsg);if(sm&&grp)sm.textContent=lsSummary(grp[1]);lsSoon()});
