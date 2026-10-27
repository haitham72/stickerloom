/* Animated sticker editor (DESKTOP_04): preview loop, trim handles on a thumbnail timeline, frame rate, loop, export (WebM / WebP / GIF). */
'use strict';
const A={pid:null,sid:null,st:null,dur:0,fps:12,inn:0,out:0,loop:true,fmt:'webm',thumbs:[],tab:'frames',playing:true,v:null,drag:null,bgv:'checker',busy:false,token:0};
const fmtT=t=>{t=Math.max(0,t);return`${Math.floor(t/60)}:${(t%60).toFixed(2).padStart(5,'0')}`};
RENDER.animate=async arg=>{const[pid,sid]=(arg||'').split('/');await loadLib();const p=packById(pid),s=p&&p.stickers.find(x=>x.id===sid),el=$('s-animate');
 if(!s||s.type!=='animated'){el.innerHTML=`<div class=pnl style="margin:40px auto;padding:40px;width:420px;align-self:flex-start;text-align:center"><h2>No animated sticker selected</h2><p class=mut>Open an animated sticker from a pack (hover it, then the play button).</p><button class="btn pri" data-act=nav data-to=library>Library</button></div>`;return}
 const tk=++A.token;Object.assign(A,{pid,sid,st:s,thumbs:[],inn:0,out:0,dur:0,tab:'frames',playing:true,drag:null});
 el.innerHTML=`<div class="an-l pnl" id=anl></div><div class=an-c><div class="an-pv pnl"><div class="stage bg-${A.bgv}" id=anst><video id=anv src="/lib/${encodeURIComponent(s.file)}" muted playsinline preload=auto></video></div></div>
  <div class="pnl" style="padding:8px"><div class=an-ctl id=anctl></div></div><div class="tl pnl" id=antl></div></div><div class="an-r pnl" id=anr></div>`;
 const v=$('anv');A.v=v;
 v.onloadedmetadata=async()=>{A.dur=v.duration||s.dur||3;A.out=A.dur;drawAn();await v.play().catch(()=>{});await captureThumbs(tk)};
 v.onerror=()=>toast('Could not play this sticker in the browser',1);
 drawAn();requestAnimationFrame(anLoop)};
async function captureThumbs(tk){const src=document.createElement('video');src.muted=true;src.playsInline=true;src.preload='auto';src.src=A.v.src;await new Promise(r=>{src.onloadeddata=r;src.onerror=r});
 const N=Math.min(28,Math.max(8,Math.round(A.dur*10))),c=document.createElement('canvas');c.width=c.height=72;const x=c.getContext('2d');
 for(let i=0;i<N;i++){if(tk!==A.token||route_!=='animate')return;const t=Math.min(A.dur-.02,(i+.5)/N*A.dur);src.currentTime=t;await new Promise(r=>{src.onseeked=r;setTimeout(r,800)});x.clearRect(0,0,72,72);x.drawImage(src,0,0,72,72);A.thumbs.push({t,url:c.toDataURL('image/png')});
  if(i%4===3||i===N-1)drawAn(true)}}
function drawAn(keep){if(route_!=='animate'||!$('anl'))return;const s=A.st,n=Math.max(1,Math.round((A.out-A.inn)*A.fps));
 $('anl').innerHTML=`<div class=ph style="padding:6px 6px 0">Animation</div><div class=seg><button class="${A.tab==='frames'?'on':''}" data-act=antab data-t=frames>Frames</button><button class="${A.tab==='settings'?'on':''}" data-act=antab data-t=settings>Settings</button></div>
  <div class=frl>${A.tab==='frames'?(A.thumbs.map((f,i)=>`<div class="frr ${f.t>=A.inn-.001&&f.t<=A.out+.001?'':'hid'}" data-act=anseek data-t=${f.t} style="${f.t>=A.inn-.001&&f.t<=A.out+.001?'':'opacity:.4'}"><img src="${f.url}"><span class=t>${f.t.toFixed(2)}s</span><span class=d>#${i+1}</span></div>`).join('')||'<div class=mut style="padding:8px">Reading frames…</div>'):
   `<div class=fld><label>Name</label><input type=text value="${esc(s.name)}" disabled></div><div class=kv style="margin:8px 4px"><span>file</span><span style="word-break:break-all">${esc(s.file)}</span><span>size</span><span>${s.kb}KB</span><span>duration</span><span>${A.dur?A.dur.toFixed(2)+'s':'…'}</span><span>emoji tag</span><span>${esc(s.emoji)}</span></div>
   <div class=fld><label>Preview background</label><select id=anbg><option ${A.bgv==='checker'?'selected':''}>checker</option><option ${A.bgv==='light'?'selected':''}>light</option><option ${A.bgv==='dark'?'selected':''}>dark</option><option ${A.bgv==='chat'?'selected':''}>chat</option></select></div>`}</div>`;
 const bg=$('anbg');if(bg)bg.onchange=e=>{A.bgv=e.target.value;$('anst').className='stage bg-'+A.bgv};
 $('anctl').innerHTML=`<button class=iconbtn data-act=anjump data-k=in title="Jump to trim start">${ic('first')}</button><button class=iconbtn data-act=anstep data-d=-1 title="Previous frame">${ic('prev')}</button>
  <button class="btn pri" data-act=anplay style="min-width:96px;justify-content:center">${A.playing?'Pause':'Play'}</button><button class=iconbtn data-act=anstep data-d=1 title="Next frame">${ic('next')}</button><button class=iconbtn data-act=anjump data-k=out title="Jump to trim end">${ic('last')}</button>`;
 $('antl').innerHTML=`<div class=row style="margin:0;justify-content:space-between"><div><b>Timeline</b> <span class=mut>/ Trim</span></div><span class=mut>${(A.out-A.inn).toFixed(2)}s · ${n} frames @ ${A.fps} fps</span></div>
  <div class=strip2 id=anstrip><div class=thumbs2>${A.thumbs.map(f=>`<img src="${f.url}">`).join('')}</div><div class=dim id=dl></div><div class=dim id=dr></div><div class=hd id=hin data-h=in></div><div class=hd id=hout data-h=out></div><div class=ph2 id=anph></div></div>
  <div class=tm><span id=antm>${fmtT(A.v?A.v.currentTime:0)} / ${fmtT(A.dur)}</span><span>drag the green handles to trim · click to seek</span></div>`;
 $('anr').innerHTML=`<div class=ph>Properties</div><div class=tgrow><b>Loop</b><input class=tgl type=checkbox data-an=loop ${A.loop?'checked':''}></div>
  <div class=fld><label>Frame rate</label><select data-an=fps>${[30,24,15,12,10,8].map(f=>`<option ${f===A.fps?'selected':''}>${f}</option>`).join('')}</select></div>
  <div class=fld><label>Canvas size</label><input type=text value="512 × 512" disabled></div>
  <div class=fld><label>Export format</label>
   <label class=radio><input type=radio name=anfmt data-an=fmt value=webm ${A.fmt==='webm'?'checked':''}> WebM (Telegram, VP9 + alpha)</label>
   <label class=radio><input type=radio name=anfmt data-an=fmt value=webp ${A.fmt==='webp'?'checked':''}> WebP (WhatsApp, ≤500KB)</label>
   <label class=radio><input type=radio name=anfmt data-an=fmt value=gif ${A.fmt==='gif'?'checked':''}> GIF (no soft edges)</label></div>
  <div class=row><button class="btn pri" data-act=anexport style="flex:1;justify-content:center" ${A.busy?'disabled':''}>${ic('download')} ${A.busy?'Encoding…':'Export'}</button></div>
  <div class=row><button class=btn data-act=ansave style="flex:1;justify-content:center" ${A.busy||A.fmt!=='webm'?'disabled':''} title="${A.fmt!=='webm'?'Only WebM can be saved back into a pack':'Save the trimmed clip as a new sticker in this pack'}">Save as new sticker</button></div>
  <div class=row><button class=btn data-act=anproject style="flex:1;justify-content:center" title="Open this clip as a video project: add text, emoji and stickers with timing, remove the background">${ic('text')} Add text / effects…</button></div>
  <div class=row><button class="btn sm" data-act=anback>${ic('back')} Back to pack</button></div><div class=mut id=anout style="font-size:12px"></div>`;
 posTl()}
function posTl(){const d=A.dur||1,pc=t=>(t/d*100)+'%';const g=id=>document.getElementById(id);if(!g('hin'))return;g('hin').style.left=pc(A.inn);g('hout').style.left=pc(A.out);g('dl').style.cssText=`left:0;width:${pc(A.inn)}`;g('dr').style.cssText=`left:${pc(A.out)};right:0`;
 const t=A.v?A.v.currentTime:0;g('anph').style.left=pc(t);const m=g('antm');if(m)m.textContent=`${fmtT(t)} / ${fmtT(A.dur)}`}
function anLoop(){if(route_!=='animate'||!A.v)return;const v=A.v;
 if(A.playing&&A.dur){if(v.currentTime>=A.out-.03||v.currentTime<A.inn-.05){if(A.loop||v.currentTime<A.inn-.05){v.currentTime=A.inn;if(v.paused)v.play().catch(()=>{})}else{v.pause();A.playing=false;drawAn()}}}
 posTl();requestAnimationFrame(anLoop)}
ACT.antab=el=>{A.tab=el.dataset.t;drawAn()};
ACT.anseek=el=>{A.v.pause();A.playing=false;A.v.currentTime=+el.dataset.t;drawAn()};
ACT.anplay=()=>{A.playing=!A.playing;if(A.playing){if(A.v.currentTime>=A.out-.03||A.v.currentTime<A.inn)A.v.currentTime=A.inn;A.v.play()}else A.v.pause();drawAn()};
ACT.anjump=el=>{A.v.currentTime=el.dataset.k==='in'?A.inn:Math.max(A.inn,A.out-.04)};
ACT.anstep=el=>{A.v.pause();A.playing=false;const dt=1/A.fps;A.v.currentTime=Math.min(A.out-.001,Math.max(A.inn,A.v.currentTime+(+el.dataset.d)*dt));drawAn()};
ACT.anback=()=>{location.hash='#/pack/'+A.pid};
$('s-animate').addEventListener('change',e=>{const k=e.target.dataset&&e.target.dataset.an;if(!k)return;if(k==='loop')A.loop=e.target.checked;else if(k==='fps')A.fps=+e.target.value;else if(k==='fmt')A.fmt=e.target.value;drawAn()});
const tAt=e=>{const r=$('anstrip').getBoundingClientRect();return Math.max(0,Math.min(A.dur,(e.clientX-r.left)/r.width*A.dur))};
$('s-animate').addEventListener('pointerdown',e=>{const st=e.target.closest('#anstrip');if(!st)return;const h=e.target.dataset&&e.target.dataset.h;A.drag=h||'seek';st.setPointerCapture(e.pointerId);
 if(A.drag==='seek'){A.v.pause();A.playing=false;A.v.currentTime=tAt(e)}});
$('s-animate').addEventListener('pointermove',e=>{if(!A.drag||!$('anstrip'))return;const t=tAt(e);
 if(A.drag==='in'){A.inn=Math.min(t,A.out-.1);if(A.v.currentTime<A.inn)A.v.currentTime=A.inn}
 else if(A.drag==='out'){A.out=Math.max(t,A.inn+.1);if(A.v.currentTime>A.out)A.v.currentTime=A.out-.04}
 else A.v.currentTime=t;posTl()});
const anEnd=()=>{if(A.drag&&A.drag!=='seek')drawAn();A.drag=null};
$('s-animate').addEventListener('pointerup',anEnd);$('s-animate').addEventListener('pointercancel',anEnd);
async function anCall(save){if(A.busy)return;A.busy=true;drawAn();const body={start:A.inn,end:A.out,fps:A.fps,format:A.fmt,loop:A.loop,save,name:save?A.st.name+' trimmed':undefined};
 const r=await fetch(`/api/packs/${A.pid}/stickers/${A.sid}/animate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 A.busy=false;
 if(!r.ok){let m='export failed';try{m=(await r.json()).error}catch(e){}drawAn();const o=$('anout');if(o)o.textContent=m;return toast(m,1)}
 if(save){const j=await r.json();await loadLib();drawAn();toast(`Saved "${j.name}" (${j.kb}KB)`);const o=$('anout');if(o)o.textContent=`Saved as ${j.file}`;return}
 let info={};try{info=JSON.parse(r.headers.get('X-Animate')||'{}')}catch(e){}
 const blob=await r.blob(),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`${A.st.name.replace(/[^\w-]+/g,'_')}.${A.fmt}`;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),4000);
 drawAn();const o=$('anout');if(o)o.textContent=`Exported ${info.kb}KB · ${info.frames} frames @ ${info.fps} fps${info.crf?' · crf '+info.crf:''}${info.quality?' · q'+info.quality:''}`;toast(`Exported ${A.fmt.toUpperCase()} (${info.kb}KB)`)}
ACT.anexport=()=>anCall(false);ACT.ansave=()=>anCall(true);
route();
