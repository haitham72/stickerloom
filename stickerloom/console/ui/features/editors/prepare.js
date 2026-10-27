/* Video / GIF Prepare & Edit (checkpoint 1E, DESKTOP_07): import -> preview -> trim -> GIF option -> background removal -> text / emoji / sticker layers
   with timing -> export or save to a pack. Preview = server-made frames drawn on a 512 canvas; the same layers are baked to PNG for the export. */
'use strict';
Object.assign(ICONS,{trim:'<path d="M7 4v16M17 4v16M7 12h10"/>',gif:'<rect x="3" y="6" width="18" height="12" rx="3"/><path d="M9.5 10.5a2 2 0 100 3M12.5 10v4M15 14v-4h2"/>',wand:'<path d="M5 19L17 7l2 2L7 21z"/><path d="M14 4v3M12.5 5.5h3M19 12v2M18 13h2"/>',film:'<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M4 9h16M4 15h16M9 4v16M15 4v16"/>'});
const PV_FONTS=['Inter','Segoe UI','Arial','Georgia','Impact','Trebuchet MS','Courier New'],PV_EMOJI=EMOJIS,CAP_S=3,GIF_CAP_S=10;
const P={studio:null,packEdit:null,id:null,p:null,n:0,imgs:[],masks:{},keyed:{},t:0,playing:true,tool:'trim',sel:null,drag:null,undo:[],redo:[],busy:false,bg:'checker',token:0,saved:'saved',last:0,stk:{},timer:null,calib:null,out:''};
const pms=s=>Math.round(s*1000),pfs=ms=>(ms/1000).toFixed(2);
const pdur=()=>P.p.source.duration*1000,pfrm=t=>Math.max(0,Math.min(P.n-1,Math.floor(t/1000*P.p.source.previewFps)));
const pL=()=>P.p.layers.find(l=>l.id===P.sel);
const pfmt=()=>P.p.export.format;

/* ---------- import / entry points */
async function importVideo(f){toast('Reading the video…');
 const r=await fetch('/api/projects?name='+encodeURIComponent(f.name),{method:'POST',body:f});let j={};try{j=await r.json()}catch(e){}
 if(!r.ok)return toast(j.error||'import failed',1);location.hash='#/prepare/'+j.id}
async function editAnimatedSticker(pack_id,sticker_id){const r=await post('/api/projects/from_sticker',{pack_id,sticker_id});if(!r.ok)return toast(r.j.error,1);
  P.packEdit={pack_id,sticker_id,project:r.j.id};P.studio=null;location.hash='#/prepare/'+r.j.id}
ACT.anproject=async()=>{const r=await post('/api/projects/from_sticker',{pack_id:A.pid,sticker_id:A.sid});if(!r.ok)return toast(r.j.error,1);location.hash='#/prepare/'+r.j.id};
ACT.openproj=el=>{location.hash='#/prepare/'+el.dataset.id};
ACT.delproj=el=>confirmDlg('Delete this video project and its source?',async()=>{const r=await post(`/api/projects/${el.dataset.id}/delete`);if(!r.ok)toast(r.j.error,1);RENDER.create()});

/* ---------- load / render shell */
RENDER.prepare=async id=>{const el=$('s-prepare');await loadLib();
 if(!id){el.innerHTML='<div class=pnl style="margin:40px auto;padding:40px;width:420px;align-self:flex-start;text-align:center"><h2>No video open</h2><p class=mut>Create → drop a video or GIF.</p><button class="btn pri" data-act=nav data-to=create>Create</button></div>';return}
 if(P.id===id&&P.p){pdraw();return}
 const r=await api('/api/projects/'+id);if(!r.ok){el.innerHTML=`<div class=pnl style="margin:40px auto;padding:40px;width:420px;align-self:flex-start"><h2>Project not found</h2><button class="btn pri" data-act=nav data-to=create>Create</button></div>`;return}
 if(P.studio&&P.studio.project!==id)P.studio=null;if(P.packEdit&&P.packEdit.project!==id)P.packEdit=null;
 const tk=++P.token;Object.assign(P,{id,p:r.j,t:r.j.video.trimStartMs,playing:true,tool:'trim',sel:null,undo:[],redo:[],keyed:{},masks:{},saved:'saved',out:'',stk:{}});
 P.n=P.p.source.frames;P.imgs=[];let ok=0;
 for(let i=0;i<P.n;i++){const im=new Image();im.onload=()=>{ok++};im.src=`/proj/${id}/f/${i}`;P.imgs.push(im)}
 P.calib=null;pdraw();P.last=performance.now();requestAnimationFrame(ploop)};

/* ---------- drawing the scene (preview and PNG bake share renderLayer) */
function fitRect(iw,ih,fit){const S=CV,r=iw/ih;let w,h;if(fit==='cover'){if(r>=1){h=S;w=S*r}else{w=S;h=S/r}}else{if(r>=1){w=S;h=S/r}else{h=S;w=S*r}}return[(S-w)/2,(S-h)/2,w,h]}
function keyFrame(i){const rm=P.p.videoBackgroundRemoval;if(rm.provider==='matte'){const m=P.masks[i]||(P.masks[i]=Object.assign(new Image(),{src:`/proj/${P.id}/mask/${i}.png`}));return m.complete&&m.naturalWidth?m:null}
 const im=P.imgs[i];if(!im||!im.complete||!im.naturalWidth)return null;if(P.keyed[i])return P.keyed[i];
 const c=document.createElement('canvas');c.width=im.naturalWidth;c.height=im.naturalHeight;const x=c.getContext('2d');x.drawImage(im,0,0);const d=x.getImageData(0,0,c.width,c.height),p=d.data,blue=rm.chroma==='blue';
 const df=k=>blue?p[k+2]-Math.max(p[k],p[k+1]):p[k+1]-Math.max(p[k],p[k+2]);
 if(!P.calib){const ring=[],w=c.width,h=c.height,b=Math.max(2,Math.floor(Math.min(w,h)/100));for(let yy=0;yy<h;yy++)for(let xx=0;xx<w;xx++)if(yy<b||yy>=h-b||xx<b||xx>=w-b){const k=(yy*w+xx)*4;ring.push(df(k))}ring.sort((a,b)=>a-b);P.calib=Math.max(.5*ring[ring.length>>1],8)}
 const t=P.calib;for(let k=0;k<p.length;k+=4){const a=Math.max(0,Math.min(1,2*(t-df(k))/t));p[k+3]=Math.round(a*255)}x.putImageData(d,0,0);return P.keyed[i]=c}
function textMetrics(x,L){const q=L.payload,fs=q.size||64;x.font=`${q.bold?700:500} ${fs}px "${q.font||'Inter'}",sans-serif`;const lines=String(q.text||'').split('\n'),lh=fs*1.15;let w=0;for(const s of lines)w=Math.max(w,x.measureText(s).width);return{lines,lh,w:Math.max(w,20)+(q.outlineW||0)*2,h:lines.length*lh+(q.outlineW||0)*2,fs}}
function renderLayer(x,L,sel){const tf=L.transform,q=L.payload;x.save();x.translate(tf.x,tf.y);x.rotate(tf.rotation*Math.PI/180);x.scale(tf.scale,tf.scale);x.globalAlpha=L.opacity;let w=0,h=0;
 if(L.type==='text'){const m=textMetrics(x,L);w=m.w;h=m.h;x.textBaseline='middle';x.textAlign=q.align||'center';x.lineJoin='round';const ax=q.align==='left'?-m.w/2+(q.outlineW||0):q.align==='right'?m.w/2-(q.outlineW||0):0,y0=-(m.lines.length-1)*m.lh/2;
  m.lines.forEach((s,i)=>{const y=y0+i*m.lh;if(q.outlineW>0){x.strokeStyle=q.outline||'#000';x.lineWidth=q.outlineW*2;x.strokeText(s,ax,y)}if(q.shadow){x.shadowColor='rgba(0,0,0,.45)';x.shadowBlur=10;x.shadowOffsetY=4}x.fillStyle=q.color||'#fff';x.fillText(s,ax,y);x.shadowColor='transparent'})}
 else if(L.type==='emoji'){const fs=q.size||120;x.font=`${fs}px "Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif`;x.textAlign='center';x.textBaseline='middle';w=fs*1.15;h=fs*1.15;x.fillText(q.char||'🙂',0,fs*.06)}
 else if(L.type==='sticker'){const im=P.stk[q.file]||(P.stk[q.file]=Object.assign(new Image(),{src:'/lib/'+encodeURIComponent(q.file)}));const s=q.size||200;if(im.complete&&im.naturalWidth){w=s;h=s*im.naturalHeight/im.naturalWidth;x.drawImage(im,-w/2,-h/2,w,h)}else{w=h=s}}
 L._b={w,h};
 if(sel){x.globalAlpha=1;x.strokeStyle=cssv('--pri');x.lineWidth=2/tf.scale;x.setLineDash([6/tf.scale,4/tf.scale]);x.strokeRect(-w/2,-h/2,w,h);x.setLineDash([]);x.fillStyle='#fff';x.beginPath();x.arc(w/2,h/2,9/tf.scale,0,7);x.fill();x.stroke()}
 x.restore()}
const inTime=(L,t)=>!L.timing||(t>=L.timing.startMs&&t<=L.timing.endMs);
function scene(x,t,chrome){x.clearRect(0,0,CV,CV);const i=pfrm(t),im=P.imgs[i],rm=P.p.videoBackgroundRemoval;let src=im;
 if(rm.enabled&&(rm.status==='READY'||rm.status==='READY_WITH_MASK')){const k=keyFrame(i);src=k||im}
 if(src&&(src.complete===undefined||src.complete)){const w=src.naturalWidth||src.width,h=src.naturalHeight||src.height;if(w){const[dx,dy,dw,dh]=fitRect(P.p.source.aspect,1,P.p.canvas.fit);x.save();if(P.p.canvas.fit==='cover'){x.beginPath();x.rect(0,0,CV,CV);x.clip()}x.globalAlpha=src===im&&rm.enabled&&rm.provider==='matte'?.4:1;x.drawImage(src,dx,dy,dw,dh);x.restore()}}
 [...P.p.layers].sort((a,b)=>a.zIndex-b.zIndex).forEach(L=>{if(L.visible&&inTime(L,t))renderLayer(x,L,chrome&&L.id===P.sel)})}
function bake(L){const c=document.createElement('canvas');c.width=c.height=CV;renderLayer(c.getContext('2d'),L,false);return c.toDataURL('image/png')}

/* ---------- playback loop */
function ploop(now){if(route_!=='prepare'||!P.p)return;const v=P.p.video,dt=now-P.last;P.last=now;
 if(P.playing&&P.n){P.t+=dt;if(P.t>=v.trimEndMs||P.t<v.trimStartMs)P.t=v.trimStartMs}
 const cv=$('pcv');if(cv)scene(cv.getContext('2d'),P.t,true);ptime();requestAnimationFrame(ploop)}
function ptime(){const d=pdur()||1,pc=t=>(t/d*100)+'%',h=$('pph');if(h)h.style.left=pc(P.t);const m=$('ptm');if(m)m.textContent=`${fmtT(P.t/1000)} / ${fmtT(d/1000)}`}

/* ---------- history + autosave */
const snap=()=>JSON.stringify({v:P.p.video,g:P.p.gif,f:P.p.canvas.fit,e:P.p.videoBackgroundRemoval.enabled,x:P.p.export,l:P.p.layers.map(({_b,...r})=>r)});
function prestore(s){const o=JSON.parse(s);P.p.video=o.v;P.p.gif=o.g;P.p.canvas.fit=o.f;P.p.videoBackgroundRemoval.enabled=o.e;P.p.export=o.x;P.p.layers=o.l;if(!pL())P.sel=null;P.keyed={};pdraw();saveSoon()}
let LASTSNAP='';
function pcommit(){const s=snap();if(s===LASTSNAP)return;if(LASTSNAP){P.undo.push(LASTSNAP);if(P.undo.length>60)P.undo.shift()}LASTSNAP=s;P.redo=[];saveSoon()}
function pundo(d){const from=d<0?P.undo:P.redo,to=d<0?P.redo:P.undo;if(!from.length)return;to.push(LASTSNAP);LASTSNAP=from.pop();prestore(LASTSNAP)}
ACT.pundo=()=>pundo(-1);ACT.predo=()=>pundo(1);
function saveSoon(){P.saved='saving';const s=$('psave');if(s)s.textContent='Saving…';clearTimeout(P.timer);P.timer=setTimeout(saveNow,500)}
async function saveNow(){clearTimeout(P.timer);if(!P.p)return true;const p=P.p,body={name:p.name,canvas:{fit:p.canvas.fit},video:{...p.video,currentTimeMs:Math.round(P.t)},gif:p.gif,
  videoBackgroundRemoval:{enabled:p.videoBackgroundRemoval.enabled},layers:p.layers.map(({_b,...r})=>r),export:p.export};
 const r=await post('/api/projects/'+P.id,body);if(!r.ok){P.saved='error';toast(r.j.error,1);const s=$('psave');if(s)s.textContent='Not saved';return false}
 Object.assign(p.videoBackgroundRemoval,{status:r.j.videoBackgroundRemoval.status,error:r.j.videoBackgroundRemoval.error});P.saved='saved';const s=$('psave');if(s)s.textContent='Saved';return true}

/* ---------- UI */
const PTOOLS=[['trim','trim','Trim'],['gif','gif','GIF'],['bg','wand','Background'],['text','text','Text'],['emoji','emoji','Emoji'],['sticker','sticker','Sticker']];
function pdraw(){if(route_!=='prepare'||!P.p)return;const p=P.p,el=$('s-prepare');
 if(!$('pcv')){el.innerHTML=`<div class="an-l pnl" id=pl></div><div class=an-c><div class="an-pv pnl"><div class="stage bg-${P.bg}" id=pstage><canvas id=pcv width=512 height=512></canvas></div></div>
  <div class=pnl style="padding:8px"><div class=an-ctl id=pctl></div></div><div class="tl pnl" id=ptl></div></div><div class="an-r pnl" id=pr></div>`;LASTSNAP=snap();bindTl()}
 $('pstage').className='stage bg-'+P.bg;
 $('pl').innerHTML=`<div class=row style="margin:0 0 6px">${P.studio?`<button class="btn sm" data-act=pstudioback>${ic('back')} Studio</button>`:P.packEdit?`<button class="btn sm" data-act=ppackback>${ic('back')} Pack</button>`:`<button class="btn sm" data-act=nav data-to=create>${ic('back')} Create</button>`}</div>
  <div class=ph style="padding:0 4px">${ic('film')} <input type=text id=pname value="${esc(p.name)}" style="border:0;background:none;font-weight:600;width:170px"></div>
  <div class=ptools>${PTOOLS.map(([k,i,l])=>`<button class="tool ${P.tool===k?'on':''}" data-act=ptool data-t=${k}>${ic(i)}<span>${l}</span></button>`).join('')}</div>
  <div class=ph style="margin:14px 4px 6px">Layers</div><div class=frl>${[...p.layers].sort((a,b)=>b.zIndex-a.zIndex).map(L=>`<div class="lay ${L.id===P.sel?'on':''}" data-act=psel data-id=${L.id}><span class=th>${L.type==='emoji'?esc(L.payload.char):L.type==='text'?ic('text'):ic('sticker')}</span><span class=nm>${esc(L.type==='text'?String(L.payload.text||'').split('\n')[0]||'Text':L.type==='emoji'?'Emoji':'Sticker')}${L.timing?` <small class=mut>${pfs(L.timing.startMs)}–${pfs(L.timing.endMs)}s</small>`:''}</span>
   <button class=iconbtn data-act=pvis data-id=${L.id} title="Show / hide">${ic(L.visible?'eye':'eyeoff')}</button><button class=iconbtn data-act=plock data-id=${L.id} title="Lock">${ic(L.locked?'lock':'unlock')}</button><button class=iconbtn data-act=pdel data-id=${L.id} title=Delete>${ic('trash')}</button></div>`).join('')||'<div class=mut style="padding:6px 4px">No layers yet. Pick Text, Emoji or Sticker.</div>'}</div>`;
 $('pname').onchange=e=>{p.name=e.target.value.trim()||p.name;saveSoon()};
 $('pctl').innerHTML=`<button class=iconbtn data-act=pundo title=Undo ${P.undo.length?'':'disabled'}>${ic('undo')}</button><button class=iconbtn data-act=predo title=Redo ${P.redo.length?'':'disabled'}>${ic('redo')}</button>
  <button class=iconbtn data-act=pstep data-d=-1 title="Previous frame">${ic('prev')}</button><button class="btn pri" data-act=pplay style="min-width:96px;justify-content:center">${P.playing?'Pause':'Play'}</button><button class=iconbtn data-act=pstep data-d=1 title="Next frame">${ic('next')}</button>
  <select id=pbg style="width:auto"><option ${P.bg==='checker'?'selected':''}>checker</option><option ${P.bg==='light'?'selected':''}>light</option><option ${P.bg==='dark'?'selected':''}>dark</option></select><span class=mut id=psave style="min-width:56px">${P.saved==='saving'?'Saving…':P.saved==='error'?'Not saved':'Saved'}</span>`;
 $('pbg').onchange=e=>{P.bg=e.target.value;$('pstage').className='stage bg-'+P.bg};
 ptl();pright()}
function ptl(){const p=P.p,v=p.video,d=pdur()||1,pc=t=>(t/d*100)+'%',N=Math.min(24,P.n),th=[];for(let i=0;i<N;i++){const k=Math.min(P.n-1,Math.floor((i+.5)/N*P.n));th.push(k)}
 const L=pL(),cap=pfmt()==='gif'?GIF_CAP_S:CAP_S,len=(v.trimEndMs-v.trimStartMs)/1000,frames=Math.round(Math.min(len,cap)*v.fps);
 $('ptl').innerHTML=`<div class=row style="margin:0;justify-content:space-between"><div><b>Timeline</b> <span class=mut>/ trim</span></div><span class=mut>${len.toFixed(2)}s${len>cap?` (clipped to ${cap}s on export)`:''} · ${frames} frames @ ${v.fps} fps</span></div>
  <div class=strip2 id=pstrip><div class=thumbs2>${th.map(k=>`<img src="/proj/${P.id}/f/${k}">`).join('')}</div><div class=dim style="left:0;width:${pc(v.trimStartMs)}"></div><div class=dim style="left:${pc(v.trimEndMs)};right:0"></div>
   <div class=hd style="left:${pc(v.trimStartMs)}" data-h=in></div><div class=hd style="left:${pc(v.trimEndMs)}" data-h=out></div><div class=ph2 id=pph></div></div>
  <div class=ltrack id=pltrack>${L?`<div class=lbar style="left:${pc(L.timing?L.timing.startMs:0)};width:${pc(L.timing?L.timing.endMs-L.timing.startMs:d)}" data-h=lmove><i data-h=lin></i><i data-h=lout></i><span>${esc(L.type==='text'?String(L.payload.text||'Text').slice(0,20):L.type)}</span></div>`:'<span class=mut style="font-size:12px">Select a layer to set when it appears.</span>'}</div>
  <div class=tm><span id=ptm></span><span>drag the handles to trim · click to seek · the bar under it is the selected layer's visibility range</span></div>`}
function bindTl(){const tl=$('ptl');const tAt=e=>{const r=$('pstrip').getBoundingClientRect();return Math.max(0,Math.min(pdur(),(e.clientX-r.left)/r.width*pdur()))};
 tl.addEventListener('pointerdown',e=>{const h=e.target.dataset&&e.target.dataset.h;if(h){P.drag={h,t0:tAt(e),L:pL()&&JSON.parse(JSON.stringify(pL().timing||{startMs:0,endMs:pdur()}))}}else if(e.target.closest('#pstrip')){P.drag={h:'seek'};P.playing=false;P.t=Math.max(P.p.video.trimStartMs,Math.min(P.p.video.trimEndMs,tAt(e)))}else return;tl.setPointerCapture(e.pointerId);e.preventDefault()});
 tl.addEventListener('pointermove',e=>{const D=P.drag;if(!D)return;const t=tAt(e),v=P.p.video;
  if(D.h==='seek')P.t=Math.max(v.trimStartMs,Math.min(v.trimEndMs,t));
  else if(D.h==='in'){v.trimStartMs=Math.round(Math.min(t,v.trimEndMs-100));if(P.t<v.trimStartMs)P.t=v.trimStartMs}
  else if(D.h==='out'){v.trimEndMs=Math.round(Math.max(t,v.trimStartMs+100));if(P.t>v.trimEndMs)P.t=v.trimEndMs}
  else if(D.L){const L=pL(),dt=t-D.t0,d=pdur();if(D.h==='lmove'){const w=D.L.endMs-D.L.startMs,s=Math.max(0,Math.min(d-w,D.L.startMs+dt));L.timing={startMs:Math.round(s),endMs:Math.round(s+w)}}else if(D.h==='lin')L.timing={startMs:Math.round(Math.max(0,Math.min(D.L.endMs-100,D.L.startMs+dt))),endMs:D.L.endMs};else L.timing={startMs:D.L.startMs,endMs:Math.round(Math.min(d,Math.max(D.L.startMs+100,D.L.endMs+dt)))}}
  ptl();pright(true)});
 const end=()=>{if(!P.drag)return;const h=P.drag.h;P.drag=null;if(h!=='seek'){pcommit();pdraw()}};tl.addEventListener('pointerup',end);tl.addEventListener('pointercancel',end)}
const fld=(l,h)=>`<div class=fld><label>${l}</label>${h}</div>`,rng=(k,v,a,b,s)=>`<input type=range data-pk=${k} min=${a} max=${b} step=${s} value=${v}>`;
function pinsp(L){const tf=L.transform,tm=L.timing||{startMs:0,endMs:pdur()};
 return`<div class=ph style="margin-top:16px">Layer</div>${fld('Scale',rng('scale',tf.scale,.1,4,.05))}${fld('Rotation',rng('rotation',tf.rotation,-180,180,1))}${fld('Opacity',rng('opacity',L.opacity,0,1,.05))}
  <div class=fld><label>Visible from – to (seconds)</label><div class=row style="margin:0"><input type=number step=.1 min=0 data-pk=tstart value=${pfs(tm.startMs)}><input type=number step=.1 min=0 data-pk=tend value=${pfs(tm.endMs)}></div>
   <div class=row style="margin:6px 0 0"><button class="btn sm" data-act=pt data-k=in>Start = playhead</button><button class="btn sm" data-act=pt data-k=out>End = playhead</button><button class="btn sm" data-act=pt data-k=all>Whole clip</button></div></div>`}
function pright(soft){if(!$('pr'))return;const p=P.p,tool=P.tool,L=pL(),rm=p.videoBackgroundRemoval;let h='';
 if(soft&&document.activeElement&&$('pr').contains(document.activeElement)&&document.activeElement.type!=='range')return;
 if(tool==='trim'){const v=p.video;h=`<div class=ph>Video</div><div class=kv style="margin:0 4px 10px"><span>source</span><span>${p.source.kind} · ${p.source.w}×${p.source.h}</span><span>length</span><span>${p.source.duration.toFixed(2)}s @ ${p.source.fps} fps</span></div>
  <div class=fld><label>Trim start – end (seconds)</label><div class=row style="margin:0"><input type=number step=.1 min=0 data-pk=vstart value=${pfs(v.trimStartMs)}><input type=number step=.1 min=0 data-pk=vend value=${pfs(v.trimEndMs)}></div></div>
  ${fld('Frame rate',`<select data-pk=fps>${[6,8,10,12,15,20,24,30].map(f=>`<option ${f===v.fps?'selected':''}>${f}</option>`).join('')}</select>`)}
  ${fld('Canvas fit',`<label class=radio><input type=radio name=pfit data-pk=fit value=contain ${p.canvas.fit==='contain'?'checked':''}> Fit (keeps the whole frame)</label><label class=radio><input type=radio name=pfit data-pk=fit value=cover ${p.canvas.fit==='cover'?'checked':''}> Fill (crops to a square)</label>`)}
  <div class=mut style="font-size:12px">Stickers keep up to ${CAP_S}s (WebM / WebP). GIF keeps up to ${GIF_CAP_S}s.</div>`}
 else if(tool==='gif'){const g=p.gif;h=`<div class=ph>GIF</div><div class=tgrow><b>Convert to GIF</b><input class=tgl type=checkbox data-pk=gifon ${pfmt()==='gif'?'checked':''}></div><div class=mut style="font-size:12px;margin-bottom:10px">Turns the trimmed clip, with your text and stickers, into a GIF that plays anywhere. GIF has 1-bit transparency, so soft edges turn hard. Packs need WebM, use Save to pack for that.</div>
  ${pfmt()==='gif'?`<div class=tgrow><b>Loop forever</b><input class=tgl type=checkbox data-pk=gifloop ${g.loop?'checked':''}></div>${fld('Colours',`<select data-pk=gifq>${[256,128,64,32].map(q=>`<option value=${q} ${q===g.quality?'selected':''}>${q}${q===256?' (best)':q===32?' (smallest)':''}</option>`).join('')}</select>`)}`:''}`}
 else if(tool==='bg'){const st=rm.status,ok=['READY','READY_WITH_MASK','ERROR'].includes(st);
  h=`<div class=ph>Background</div><div class=pill data-s=${st}>${st==='READY_WITH_MASK'?'Ready (mask made)':st.toLowerCase().replace('_',' ')}</div>
   <div class=tgrow><b>Remove background</b><input class=tgl type=checkbox data-pk=bgon ${rm.enabled?'checked':''} ${ok?'':'disabled'}></div>
   ${ok?`<div class=mut style="font-size:12px">Provider: ${rm.provider==='matte'?'AI matte ('+esc(rm.model||'')+')':'green / blue screen key ('+esc(rm.chroma||'')+')'}. ${rm.provider==='matte'?'The preview loads one masked frame at a time from the server.':'Live preview.'}</div>`:`<div class=mut style="font-size:13px">${esc(rm.reason||'Not available for this clip.')}</div>`}
   ${st==='ERROR'?`<div class=errbox>We couldn't remove the background. Your video and edits are still saved.${rm.error?'<br><small>'+esc(rm.error)+'</small>':''}<div class=row><button class="btn sm pri" data-act=pbgretry>Try again</button><button class="btn sm" data-act=pbgskip>Continue without removal</button></div></div>`:''}`}
 else if(tool==='text'){const q=L&&L.type==='text'?L.payload:null;
  h=`<div class=ph>Text</div><div class=row style="margin:0 0 10px"><button class="btn pri" data-act=paddtext style="flex:1;justify-content:center">${ic('plus')} Add text</button></div>`+(q?`${fld('Content',`<textarea rows=2 data-pk=text>${esc(q.text)}</textarea>`)}${fld('Font',`<select data-pk=font>${PV_FONTS.map(f=>`<option ${f===q.font?'selected':''}>${f}</option>`).join('')}</select>`)}${fld('Size',rng('size',q.size,20,220,2))}
   <div class=row style="margin:0 0 10px"><div class=fld style="flex:1;margin:0"><label>Colour</label><input type=color data-pk=color value=${q.color}></div><div class=fld style="flex:1;margin:0"><label>Outline</label><input type=color data-pk=outline value=${q.outline}></div></div>${fld('Outline width',rng('outlineW',q.outlineW,0,16,1))}
   <div class=tgrow><b>Shadow</b><input class=tgl type=checkbox data-pk=shadow ${q.shadow?'checked':''}></div><div class=tgrow><b>Bold</b><input class=tgl type=checkbox data-pk=bold ${q.bold?'checked':''}></div>${fld('Align',`<select data-pk=align>${['left','center','right'].map(a=>`<option ${a===q.align?'selected':''}>${a}</option>`).join('')}</select>`)}`:'');
  if(q)h+=pinsp(L)}
 else if(tool==='emoji'){h=`<div class=ph>Emoji</div><div class=emo>${PV_EMOJI.map(c=>`<button data-act=paddemoji data-c="${c}">${c}</button>`).join('')}</div>`;if(L&&L.type==='emoji')h+=fld('Size',rng('size',L.payload.size,40,300,4))+pinsp(L)}
 else if(tool==='sticker'){const sts=LIB.packs.flatMap(k=>k.stickers).filter(s=>s.type==='static');h=`<div class=ph>Sticker</div><div class=mut style="margin-bottom:8px;font-size:12px">Add one of your static stickers on top of the video.</div><div class=emo>${sts.map(s=>`<button data-act=paddstk data-f="${esc(s.file)}" title="${esc(s.name)}"><img src="/lib/${encodeURIComponent(s.file)}" style="width:100%"></button>`).join('')||'<span class=mut>No static stickers yet.</span>'}</div>`;if(L&&L.type==='sticker')h+=fld('Size',rng('size',L.payload.size,60,512,4))+pinsp(L)}
 const e=p.export;
 if(P.studio)h+=`<div class=ph style="margin-top:20px;border-top:1px solid var(--bd);padding-top:14px">Save</div><div class=mut style="font-size:12px;margin-bottom:8px">Your layers live in the Studio on top of the original animation. <b>Save to sticker</b> exports them to the animation <b>and</b> the image together (text shows in the image whenever it is visible), updates its copies in packs, and goes back to the Studio.</div>
  <div class=row><button class="btn pri" data-act=pstudiosave style="flex:1;justify-content:center" ${P.busy?'disabled':''}>${ic('check')} ${P.busy?'Saving…':'Save to sticker'}</button></div><div class=mut id=pout style="font-size:12px">${esc(P.out)}</div>`;
 else if(P.packEdit)h+=`<div class=ph style="margin-top:20px;border-top:1px solid var(--bd);padding-top:14px">Save</div>
  <div class=row><button class="btn pri" data-act=ppackreplace style="flex:1;justify-content:center" ${P.busy?'disabled':''}>${ic('check')} ${P.busy?'Saving…':'Save to sticker'}</button></div><div class=mut style="font-size:12px;margin-bottom:8px">Replaces this sticker in its pack. Use Save as a new sticker below to keep both.</div>`;
 h+=`<div class=ph style="margin-top:20px;border-top:1px solid var(--bd);padding-top:14px">Export</div>
  ${fld('Format',['webm|WebM (Telegram, VP9 + alpha, ≤256KB)','webp|WebP (WhatsApp, ≤500KB)','gif|GIF (plays anywhere)'].map(s=>{const[k,l]=s.split('|');return`<label class=radio><input type=radio name=pfmt data-pk=fmt value=${k} ${e.format===k?'checked':''}> ${l}</label>`}).join(''))}
  <div class=row style="margin:0 0 10px"><div class=fld style="flex:1;margin:0"><label>Name</label><input type=text data-pk=ename value="${esc(e.name||p.name)}"></div><div class=fld style="width:70px;margin:0"><label>Emoji</label><input type=text data-pk=eemoji value="${esc(e.emoji)}"></div></div>
  <div class=row><button class="btn pri" data-act=prender style="flex:1;justify-content:center" ${P.busy?'disabled':''}>${ic('download')} ${P.busy?'Encoding…':'Download '+e.format.toUpperCase()}</button></div>
  <div class=row><button class=btn data-act=psavepack style="flex:1;justify-content:center" ${P.busy?'disabled':''} title="Encodes a WebM sticker and adds it to a pack">${ic('plus')} ${P.packEdit?'Save as a new sticker (WebM)':'Save to pack (WebM)'}</button></div><div class=mut id=pout style="font-size:12px">${esc(P.out)}</div>`;
 $('pr').innerHTML=h}

/* ---------- events */
ACT.ptool=el=>{P.tool=el.dataset.t;pdraw()};ACT.pplay=()=>{P.playing=!P.playing;if(P.playing&&(P.t>=P.p.video.trimEndMs))P.t=P.p.video.trimStartMs;pdraw()};
ACT.pstep=el=>{P.playing=false;const v=P.p.video,dt=1000/v.fps;P.t=Math.min(v.trimEndMs,Math.max(v.trimStartMs,P.t+(+el.dataset.d)*dt));pdraw()};
ACT.psel=el=>{P.sel=el.dataset.id;const L=pL();if(L&&P.tool==='trim')P.tool=L.type==='text'?'text':L.type==='emoji'?'emoji':'sticker';pdraw()};
ACT.pvis=el=>{const L=P.p.layers.find(l=>l.id===el.dataset.id);L.visible=!L.visible;pcommit();pdraw()};ACT.plock=el=>{const L=P.p.layers.find(l=>l.id===el.dataset.id);L.locked=!L.locked;pcommit();pdraw()};
ACT.pdel=el=>{P.p.layers=P.p.layers.filter(l=>l.id!==el.dataset.id);if(P.sel===el.dataset.id)P.sel=null;pcommit();pdraw()};
ACT.pt=el=>{const L=pL(),k=el.dataset.k;if(!L)return;if(k==='all')delete L.timing;else{const t=Math.round(P.t),tm=L.timing||{startMs:0,endMs:pdur()};L.timing=k==='in'?{startMs:t,endMs:Math.max(t+100,tm.endMs)}:{startMs:Math.min(tm.startMs,Math.max(0,t-100)),endMs:t}}pcommit();pdraw()};
const newId=()=>Math.random().toString(36).slice(2,9),topZ=()=>P.p.layers.reduce((m,l)=>Math.max(m,l.zIndex),0)+1;
function addLayer(type,payload){const L={id:newId(),type,visible:true,locked:false,zIndex:topZ(),transform:{x:256,y:type==='text'?430:256,scale:1,rotation:0},opacity:1,payload};if(type!=='text')L.transform.y=256;P.p.layers.push(L);P.sel=L.id;pcommit();pdraw()}
ACT.paddtext=()=>{P.tool='text';addLayer('text',{text:'Text',font:'Inter',size:88,color:'#ffffff',outline:'#000000',outlineW:6,shadow:true,bold:true,align:'center'})};
ACT.paddemoji=el=>{P.tool='emoji';addLayer('emoji',{char:el.dataset.c,size:140})};ACT.paddstk=el=>{P.tool='sticker';addLayer('sticker',{file:el.dataset.f,size:220})};
ACT.pbgretry=async()=>{const r=await post('/api/projects/'+P.id,{videoBackgroundRemoval:{enabled:true}});if(r.ok){P.p.videoBackgroundRemoval=r.j.videoBackgroundRemoval;pdraw()}else toast(r.j.error,1)};
ACT.pbgskip=async()=>{P.p.videoBackgroundRemoval.enabled=false;P.p.videoBackgroundRemoval.status='READY';pcommit();await saveNow();pdraw()};
$('s-prepare').addEventListener('input',e=>{const k=e.target.dataset&&e.target.dataset.pk;if(!k||!P.p)return;const p=P.p,v=p.video,L=pL(),t=e.target,num=+t.value,live=t.type==='range'||t.tagName==='TEXTAREA'||t.type==='color';
 if(['scale','rotation'].includes(k)&&L)L.transform[k]=num;else if(k==='opacity'&&L)L.opacity=num;
 else if(['text','font','color','outline','align'].includes(k)&&L)L.payload[k]=t.value;else if(['size','outlineW'].includes(k)&&L)L.payload[k]=num;
 else if(k==='ename'){p.export.name=t.value}else if(k==='eemoji'){p.export.emoji=t.value}else return;
 if(live&&k!=='ename'&&k!=='eemoji'){clearTimeout(P.ct);P.ct=setTimeout(()=>{pcommit();ptl();pdraw()},350)}else saveSoon()});
$('s-prepare').addEventListener('change',e=>{const k=e.target.dataset&&e.target.dataset.pk;if(!k||!P.p)return;const p=P.p,v=p.video,L=pL(),t=e.target,d=pdur();
 if(k==='vstart')v.trimStartMs=Math.round(Math.max(0,Math.min(v.trimEndMs-100,+t.value*1000)));else if(k==='vend')v.trimEndMs=Math.round(Math.min(d,Math.max(v.trimStartMs+100,+t.value*1000)));
 else if(k==='fps')v.fps=+t.value;else if(k==='fit')p.canvas.fit=t.value;
 else if(k==='tstart'&&L){const tm=L.timing||{startMs:0,endMs:d};L.timing={startMs:Math.round(Math.max(0,Math.min(tm.endMs-100,+t.value*1000))),endMs:tm.endMs}}
 else if(k==='tend'&&L){const tm=L.timing||{startMs:0,endMs:d};L.timing={startMs:tm.startMs,endMs:Math.round(Math.min(d,Math.max(tm.startMs+100,+t.value*1000)))}}
 else if(k==='shadow'||k==='bold'){if(L)L.payload[k]=t.checked}
 else if(k==='gifon'){p.export.format=t.checked?'gif':'webm'}else if(k==='gifloop')p.gif.loop=t.checked;else if(k==='gifq')p.gif.quality=+t.value;
 else if(k==='fmt'){p.export.format=t.value}
 else if(k==='bgon'){p.videoBackgroundRemoval.enabled=t.checked;P.keyed={};P.calib=null;if(t.checked&&p.videoBackgroundRemoval.provider==='matte'){for(let i=pfrm(v.trimStartMs);i<=pfrm(v.trimEndMs);i++)keyFrame(i)}}
 else return;pcommit();P.t=Math.max(v.trimStartMs,Math.min(v.trimEndMs,P.t));pdraw()});
document.addEventListener('keydown',e=>{if(route_!=='prepare'||!P.p)return;const tg=e.target.tagName;if(['INPUT','TEXTAREA','SELECT'].includes(tg))return;
 if(e.key===' '){e.preventDefault();ACT.pplay()}else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();pundo(e.shiftKey?1:-1)}else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='y'){e.preventDefault();pundo(1)}
 else if((e.key==='Delete'||e.key==='Backspace')&&P.sel){ACT.pdel({dataset:{id:P.sel}})}});
/* canvas: select, move, scale by the corner handle */
const cpt=e=>{const r=$('pcv').getBoundingClientRect();return[(e.clientX-r.left)/r.width*CV,(e.clientY-r.top)/r.height*CV]};
function phit(L,x,y,pad=0){if(!L._b)return false;const tf=L.transform,a=-tf.rotation*Math.PI/180,dx=x-tf.x,dy=y-tf.y,lx=(dx*Math.cos(a)-dy*Math.sin(a))/tf.scale,ly=(dx*Math.sin(a)+dy*Math.cos(a))/tf.scale;return Math.abs(lx)<=L._b.w/2+pad&&Math.abs(ly)<=L._b.h/2+pad}
function onHandle(L,x,y){const tf=L.transform,a=tf.rotation*Math.PI/180,cx=L._b.w/2*tf.scale,cy=L._b.h/2*tf.scale,hx=tf.x+cx*Math.cos(a)-cy*Math.sin(a),hy=tf.y+cx*Math.sin(a)+cy*Math.cos(a);return Math.hypot(x-hx,y-hy)<16}
$('s-prepare').addEventListener('pointerdown',e=>{if(e.target.id!=='pcv')return;const[x,y]=cpt(e),cur=pL();
 if(cur&&!cur.locked&&cur._b&&onHandle(cur,x,y)){P.drag={h:'scale',s0:cur.transform.scale,d0:Math.hypot(x-cur.transform.x,y-cur.transform.y)}}
 else{const L=[...P.p.layers].sort((a,b)=>b.zIndex-a.zIndex).find(l=>l.visible&&inTime(l,P.t)&&phit(l,x,y,6));if(L){P.sel=L.id;if(!L.locked)P.drag={h:'move',ox:x-L.transform.x,oy:y-L.transform.y};if(P.tool==='trim')P.tool=L.type==='text'?'text':L.type==='emoji'?'emoji':'sticker';pdraw()}else{P.sel=null;pdraw()}}
 if(P.drag)$('pcv').setPointerCapture(e.pointerId)});
$('s-prepare').addEventListener('pointermove',e=>{const D=P.drag;if(!D||e.target.id!=='pcv')return;const L=pL(),[x,y]=cpt(e);if(!L)return;
 if(D.h==='move'){L.transform.x=Math.round(x-D.ox);L.transform.y=Math.round(y-D.oy)}else if(D.h==='scale')L.transform.scale=Math.max(.1,Math.min(20,D.s0*Math.hypot(x-L.transform.x,y-L.transform.y)/Math.max(8,D.d0)))});
$('s-prepare').addEventListener('pointerup',e=>{if(P.drag&&(P.drag.h==='move'||P.drag.h==='scale')){P.drag=null;pcommit();pright()}});

/* ---------- export */
async function prCall(save,pid){if(P.busy)return;P.busy=true;pright();if(!await saveNow()){P.busy=false;return pright()}
 const ov={};P.p.layers.forEach(L=>{if(L.visible)ov[L.id]=bake(L)});const e=P.p.export;
 const body={format:save?'webm':e.format,overlays:ov};if(save)body.save={pack_id:pid,name:e.name||P.p.name,emoji:e.emoji};
 const r=await fetch(`/api/projects/${P.id}/render`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});P.busy=false;
 if(!r.ok){let m='export failed';try{m=(await r.json()).error}catch(x){}P.out=m;const q=await api('/api/projects/'+P.id);if(q.ok){P.p.videoBackgroundRemoval=q.j.videoBackgroundRemoval;if(q.j.videoBackgroundRemoval.status==='ERROR')P.tool='bg'}pdraw();return toast(m,1)}
 if(save){const j=await r.json();await loadLib();P.out=`Saved "${j.name}" (${j.kb}KB) to ${packById(pid).name}`;P.p.packId=pid;pright();return toast(P.out)}
 let info={};try{info=JSON.parse(r.headers.get('X-Render')||'{}')}catch(x){}
 const blob=await r.blob(),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`${(e.name||P.p.name).replace(/[^\w-]+/g,'_')}.${e.format}`;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),4000);
 P.out=`Exported ${info.kb}KB · ${info.frames} frames @ ${info.fps} fps${info.clipped?' · clipped to '+(e.format==='gif'?GIF_CAP_S:CAP_S)+'s':''}${info.keyed?' · background removed':''}`;const q=await api('/api/projects/'+P.id);if(q.ok)P.p.videoBackgroundRemoval=q.j.videoBackgroundRemoval;pright();toast(`Exported ${e.format.toUpperCase()} (${info.kb}KB)`)}
ACT.prender=()=>prCall(false);ACT.psavepack=()=>pickPack(pid=>prCall(true,pid),'Save WebM sticker to pack');
/* ---------- Save to sticker: Studio edit (image + animation) and pack edit (replace) */
const prLayers=()=>{const ov={};P.p.layers.forEach(L=>{if(L.visible)ov[L.id]=bake(L)});return ov};
ACT.pstudiosave=async()=>{if(P.busy||!P.studio)return;P.busy=true;pright();if(!await saveNow()){P.busy=false;return pright()}
  const S=P.studio,r=await post(`/api/generations/${S.gen}/studio_edit`,{index:S.index,action:'commit',overlays:prLayers()});P.busy=false;
  if(!r.ok){P.out=r.j.error;pright();return dlg(`<h2>Could not save</h2><div class=warn>${esc(r.j.error)}</div><div class=row style="justify-content:flex-end"><button class="btn pri" data-act=dlgx>OK</button></div>`)}
  P.studio=null;await loadLib();toast(`Saved: image and animation updated${r.j.pack_copies?` · ${r.j.pack_copies} pack cop${r.j.pack_copies===1?'y':'ies'} refreshed`:''}`);location.hash='#/studio'};
ACT.ppackreplace=async()=>{if(P.busy||!P.packEdit)return;P.busy=true;pright();if(!await saveNow()){P.busy=false;return pright()}
  const E=P.packEdit,r=await fetch(`/api/projects/${P.id}/render`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({format:'webm',overlays:prLayers(),save:{replace:{pack_id:E.pack_id,sticker_id:E.sticker_id}}})});
  let j={};try{j=await r.json()}catch(e){}P.busy=false;
  if(!r.ok){P.out=j.error||'save failed';pright();return dlg(`<h2>Could not save</h2><div class=warn>${esc(P.out)}</div><div class=row style="justify-content:flex-end"><button class="btn pri" data-act=dlgx>OK</button></div>`)}
  P.packEdit=null;await loadLib();toast('Sticker updated');location.hash='#/pack/'+E.pack_id};
ACT.pstudioback=()=>{P.studio=null;location.hash='#/studio'};ACT.ppackback=()=>{const E=P.packEdit;P.packEdit=null;location.hash=E?'#/pack/'+E.pack_id:'#/library'};
