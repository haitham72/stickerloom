/* Create (DESKTOP_02), Editor (DESKTOP_03) and Export (DESKTOP_06). One editor route, contextual inspector, one project model. */
'use strict';
const CV=512,EMOJIS='😀😂🥹😍😎🤩😭😡🥳🤔👍👏🙏💪❤️🔥✨🎉⭐🇦🇪🌴🕌🦅🐪☕🎂🎁💯✅❌⚡🌙'.match(/\p{Extended_Pictographic}(?:️|‍\p{Extended_Pictographic})*|\p{Regional_Indicator}{2}/gu);
const FONTS=['Segoe UI','Tahoma','Arial Black','Impact','Comic Sans MS','Georgia','Courier New','Trebuchet MS'];
const Ed={targetPack:null};
const cssv=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const E={layers:[],sel:null,tool:'select',zoom:1,undo:[],redo:[],border:{on:true,w:12,color:'#ffffff',shadow:true,blur:8},name:'sticker',emoji:'🙂',pack:null,bgv:'checker',
 srcBlob:null,info:null,brush:40,uid:1,pool:{},mode:null,ptr:null,active:false,mainId:null,outlined:false,dataUrl:''};
const clone=c=>{const n=document.createElement('canvas');n.width=c.width;n.height=c.height;n.getContext('2d',{willReadFrequently:true}).drawImage(c,0,0);return n};
const off=()=>{const c=document.createElement('canvas');c.width=c.height=CV;return c};
const comp=off(),sil=off(),outl=off(),cctx=comp.getContext('2d'),sctx=sil.getContext('2d'),octx=outl.getContext('2d');
const scratch=document.createElement('canvas').getContext('2d');

/* ---------- layers */
const cur_=()=>E.layers.find(l=>l.id===E.sel)||null;
function mkSubject(img,o={}){const cv=document.createElement('canvas');cv.width=img.width;cv.height=img.height;cv.getContext('2d',{willReadFrequently:true}).drawImage(img,0,0);
 const fit=o.outlined?1:Math.min(2,.775*CV/Math.max(cv.width,cv.height));
 const L={id:E.uid++,type:'subject',name:o.name||'Sticker',cv,orig:clone(cv),x:CV/2,y:CV/2,s:fit,r:0,vis:true,lock:false,ver:0,hist:{},adj:{b:100,c:100,s:100}};E.pool[L.id]=L;return L}
const mkText=t=>({id:E.uid++,type:'text',name:'Text',text:t||'Text',font:'Segoe UI',size:72,bold:true,italic:false,color:'#ffffff',stroke:'#111111',sw:10,x:CV/2,y:CV*.78,s:1,r:0,vis:true,lock:false});
const mkEmoji=ch=>({id:E.uid++,type:'emoji',name:'Emoji',ch,size:110,x:CV/2,y:CV/2,s:1,r:0,vis:true,lock:false});
const label=L=>L.type==='text'?'Text · '+L.text.replace(/\n/g,' ').slice(0,14):L.type==='emoji'?'Emoji · '+L.ch:L.name;
const fontStr=L=>`${L.italic?'italic ':''}${L.bold?'700':'400'} ${L.size}px "${L.font}", "Segoe UI", Tahoma, sans-serif`;
function bounds(L){if(L.type==='subject')return{w:L.cv.width,h:L.cv.height};
 if(L.type==='emoji')return{w:L.size*1.25,h:L.size*1.25};
 scratch.font=fontStr(L);const ls=L.text.split('\n'),w=Math.max(...ls.map(t=>scratch.measureText(t||' ').width))+L.sw*2;return{w,h:ls.length*L.size*1.2+L.sw*2}}
function drawLayer(ctx,L){if(!L.vis)return;ctx.save();ctx.translate(L.x,L.y);ctx.rotate(L.r*Math.PI/180);ctx.scale(L.s,L.s);
 if(L.type==='subject'){const a=L.adj;if(a.b!==100||a.c!==100||a.s!==100)ctx.filter=`brightness(${a.b}%) contrast(${a.c}%) saturate(${a.s}%)`;ctx.drawImage(L.cv,-L.cv.width/2,-L.cv.height/2)}
 else if(L.type==='emoji'){ctx.font=`${L.size}px "Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif`;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(L.ch,0,0)}
 else{ctx.font=fontStr(L);ctx.textAlign='center';ctx.textBaseline='middle';ctx.direction=/[֐-ࣿ]/.test(L.text)?'rtl':'ltr';ctx.lineJoin='round';
  const ls=L.text.split('\n'),lh=L.size*1.2,y0=-(ls.length-1)*lh/2;ls.forEach((t,i)=>{if(L.sw>0){ctx.lineWidth=L.sw;ctx.strokeStyle=L.stroke;ctx.strokeText(t,0,y0+i*lh)}ctx.fillStyle=L.color;ctx.fillText(t,0,y0+i*lh)})}
 ctx.restore()}
function compose(ctx,withBorder){ctx.clearRect(0,0,CV,CV);cctx.clearRect(0,0,CV,CV);E.layers.forEach(L=>drawLayer(cctx,L));
 const B=E.border;
 if(withBorder&&B.on&&B.w>0){sctx.globalCompositeOperation='source-over';sctx.clearRect(0,0,CV,CV);sctx.drawImage(comp,0,0);sctx.globalCompositeOperation='source-in';sctx.fillStyle=B.color;sctx.fillRect(0,0,CV,CV);sctx.globalCompositeOperation='source-over';
  octx.clearRect(0,0,CV,CV);for(const rr of [B.w*.5,B.w])for(let a=0;a<32;a++)octx.drawImage(sil,Math.cos(a/32*6.2832)*rr,Math.sin(a/32*6.2832)*rr);octx.drawImage(sil,0,0);
  ctx.save();if(B.shadow){ctx.shadowColor='rgba(0,0,0,.35)';ctx.shadowBlur=B.blur;ctx.shadowOffsetY=3}ctx.drawImage(outl,0,0);ctx.restore()}
 ctx.drawImage(comp,0,0)}
const toCanvas=(L,lx,ly)=>{const c=Math.cos(L.r*Math.PI/180),s=Math.sin(L.r*Math.PI/180);return[L.x+(lx*c-ly*s)*L.s,L.y+(lx*s+ly*c)*L.s]};
const toLocal=(L,px,py)=>{const c=Math.cos(-L.r*Math.PI/180),s=Math.sin(-L.r*Math.PI/180),dx=px-L.x,dy=py-L.y;return[(dx*c-dy*s)/L.s,(dx*s+dy*c)/L.s]};
function hit(px,py){for(let i=E.layers.length-1;i>=0;i--){const L=E.layers[i];if(!L.vis||L.lock)continue;const[b,[lx,ly]]=[bounds(L),toLocal(L,px,py)];
  if(Math.abs(lx)<=b.w/2&&Math.abs(ly)<=b.h/2){if(L.type!=='subject')return L;const d=L.cv.getContext('2d',{willReadFrequently:true}).getImageData(Math.max(0,Math.min(L.cv.width-1,lx+b.w/2|0)),Math.max(0,Math.min(L.cv.height-1,ly+b.h/2|0)),1,1).data;if(d[3]>16)return L}}return null}
function handles(L){const b=bounds(L),hw=b.w/2,hh=b.h/2;return{corners:[[-hw,-hh],[hw,-hh],[hw,hh],[-hw,hh]].map(p=>toCanvas(L,...p)),rot:toCanvas(L,0,-hh-30/(E.zoom*L.s)),c:[L.x,L.y]}}
function overlay(){const o=$('ovl');if(!o)return;const x=o.getContext('2d');x.clearRect(0,0,CV,CV);const L=cur_();
 if(L&&L.vis&&E.tool!=='erase'&&E.tool!=='restore'){const h=handles(L),k=1/E.zoom;x.lineWidth=1.5*k;x.strokeStyle=cssv('--pri');x.fillStyle='#fff';x.beginPath();h.corners.forEach((p,i)=>i?x.lineTo(...p):x.moveTo(...p));x.closePath();x.stroke();
  x.beginPath();x.moveTo(...toCanvas(L,0,-bounds(L).h/2));x.lineTo(...h.rot);x.stroke();
  [h.corners[2],h.rot].forEach(p=>{x.beginPath();x.arc(p[0],p[1],6*k,0,7);x.fill();x.stroke()})}
 if((E.tool==='erase'||E.tool==='restore')&&E.ptr){x.lineWidth=1.5/E.zoom;x.strokeStyle=E.tool==='erase'?cssv('--bad'):cssv('--pri');x.beginPath();x.arc(E.ptr[0],E.ptr[1],E.brush/2,0,7);x.stroke()}}
let rq=0;function redraw(){if(rq)return;rq=requestAnimationFrame(()=>{rq=0;const c=$('edc');if(c){compose(c.getContext('2d'),true);overlay()}})}

/* ---------- history */
function thumbOf(){const c=off();compose(c.getContext('2d'),true);const t=document.createElement('canvas');t.width=t.height=72;t.getContext('2d').drawImage(c,0,0,72,72);return t.toDataURL('image/png')}
function snapshot(){return{thumb:thumbOf(),layers:E.layers.map(L=>{const{cv,orig,hist,...rest}=L;if(L.type==='subject'&&!L.hist[L.ver])L.hist[L.ver]=clone(L.cv);return JSON.parse(JSON.stringify(rest))}),sel:E.sel,border:{...E.border}}}
function restore(sn){E.layers=sn.layers.map(o=>{if(o.type==='subject'){const P=E.pool[o.id];Object.assign(P,o);P.cv=clone(P.hist[o.ver]);return P}return{...o}});E.sel=sn.sel;E.border={...sn.border}}
function commit(){E.undo.push(snapshot());if(E.undo.length>60)E.undo.shift();E.redo=[]}
function undo(){if(E.undo.length<2)return;E.redo.push(E.undo.pop());restore(E.undo[E.undo.length-1]);ui();redraw()}
function redo(){if(!E.redo.length)return;const s=E.redo.pop();E.undo.push(s);restore(s);ui();redraw()}
const pushLayer=L=>{E.layers.push(L);E.sel=L.id;commit();ui();redraw()};

/* ---------- sessions */
function startSession(layers,o={}){Object.assign(E,{layers:[],sel:null,tool:'select',undo:[],redo:[],pool:E.pool,name:o.name||'sticker',emoji:o.emoji||'🙂',pack:o.pack||Ed.targetPack||null,
  srcBlob:o.srcBlob||null,info:o.info||null,back:o.back||null,outlined:!!o.outlined,active:true,border:{on:!o.outlined,w:12,color:'#ffffff',shadow:true,blur:8}});
 E.layers=layers;E.sel=layers.length?layers[layers.length-1].id:null;E.mainId=layers[0]&&layers[0].type==='subject'?layers[0].id:null;
 commit();location.hash='#/editor';if(route_==='editor')RENDER.editor()}
async function blobToImg(b){return await createImageBitmap(b)}
Ed.openBlob=async(blob,o={})=>{try{const img=await blobToImg(blob);startSession([mkSubject(img,o)],o)}catch(e){toast('Could not read that image: '+e.message,1)}};
Ed.openImage=async(url,o={})=>{try{const r=await fetch(url);if(!r.ok)throw new Error('HTTP '+r.status);await Ed.openBlob(await r.blob(),o)}catch(e){toast('Could not open sticker: '+e.message,1)}};
Ed.newText=()=>{startSession([mkText('Text')],{name:'text sticker'});E.tool='text';ui()};
async function importFile(f){
 if(!f)return;
 if(f.type.startsWith('video/')||f.type==='image/gif'||/\.(mp4|mov|m4v|webm|mkv|avi|gif)$/i.test(f.name))return importVideo(f);
 if(!f.type.startsWith('image/'))return toast('Unsupported file type: '+(f.type||f.name),1);
 toast('Cutting out the subject…');
 const r=await fetch('/api/cutout?method='+(($('cutm')||{}).value||'auto'),{method:'POST',body:f});
 if(!r.ok){let m='cutout failed';try{m=(await r.json()).error}catch(e){}return toast(m,1)}
 let info={};try{info=JSON.parse(r.headers.get('X-Cutout')||'{}')}catch(e){}
 toast(`Cutout: ${info.method}${info.warning?' — '+info.warning:''}`,!!info.warning);
 await Ed.openBlob(await r.blob(),{name:f.name.replace(/\.[^.]+$/,''),srcBlob:f,info})}

/* ---------- Create (DESKTOP_02) */
RENDER.create=async()=>{await loadLib();const tp=Ed.targetPack&&packById(Ed.targetPack);
 $('s-create').innerHTML=`<div class=page><div class=ph><h1>Create Sticker</h1></div><div class=mut>${tp?`Adding to pack <b>${esc(tp.name)}</b> · <a href="#" data-act=cleartarget>clear</a>`:'Photo → auto cutout → edit → border → save to a pack.'}</div>
  <div class=drop id=drop style="margin-top:14px">${ic('photo').replace('<svg','<svg width=56 height=56')}<h2>Drag & drop a photo, video or GIF</h2><div class=mut>Photos: auto cutout, then the sticker editor. Videos and GIFs: trim, add text, save as an animated sticker or GIF.</div>
   <div class=row style="justify-content:center"><button class="btn pri" data-act=pickfile>${ic('photo')} Photo</button><button class=btn data-act=pickfile>${ic('film')} Video / GIF</button><button class=btn data-act=newtext>${ic('text')} Text sticker</button></div><input type=file id=fin accept="image/*,video/*,.gif" hidden>
   <div class=row style="justify-content:center;margin-top:8px"><span class=mut>Photo cutout</span><select id=cutm style="width:auto" aria-label="Photo cutout"><option value=auto>Auto</option><option value=matte>AI matte</option><option value=grabcut>GrabCut (simple)</option></select><span class=mut id=cutn></span></div></div>
  <div id=projs></div>
  <div class=row style="justify-content:space-between;margin-top:22px"><h2>Recent stickers</h2></div>
  <div class=strip>${LIB.recent.filter(s=>s.type==='static').map(s=>`<div class=st data-act=editrecent data-f="${esc(s.file)}" data-n="${esc(s.name)}" data-e="${esc(s.emoji)}" title="Edit a copy">${media(s)}</div>`).join('')||'<span class=mut>Nothing yet.</span>'}</div></div>`;
 fetch('/api/generations').then(r=>r.json()).then(j=>{const m=(j.health||{}).matte,n=$('cutn');if(n)n.textContent=m&&m.ok?'AI matte: '+m.model:'AI matte off ('+(m?m.reason:'?')+')'}).catch(()=>{});
 api('/api/projects').then(r=>{const ps=(r.j.projects||[]);if(ps.length&&$('projs'))$('projs').innerHTML=`<div class=row style="justify-content:space-between;margin-top:22px"><h2>My video projects</h2></div><div class=strip>${ps.map(q=>`<div class=st data-act=openproj data-id=${q.id} title="${esc(q.name)}"><img src="/proj/${q.id}/f/0" style="width:100%;height:100%;object-fit:contain"><span class=em style="right:2px;bottom:2px;left:auto" data-act=delproj data-id=${q.id} title=Delete>✕</span></div>`).join('')}</div>`});
 const d=$('drop');d.ondragover=e=>{e.preventDefault();d.classList.add('over')};d.ondragleave=()=>d.classList.remove('over');d.ondrop=e=>{e.preventDefault();d.classList.remove('over');importFile(e.dataTransfer.files[0])};
 $('fin').onchange=e=>{importFile(e.target.files[0]);e.target.value=''}};
ACT.pickfile=()=>$('fin').click();ACT.newtext=()=>Ed.newText();ACT.cleartarget=()=>{Ed.targetPack=null;RENDER.create()};
ACT.editrecent=el=>Ed.openImage('/lib/'+encodeURIComponent(el.dataset.f),{outlined:true,name:el.dataset.n+' copy',emoji:el.dataset.e});

/* ---------- Editor (DESKTOP_03) */
const TOOLS=[['select','sticker','Sticker'],['text','text','Text'],['emoji','emoji','Emoji'],['border','border','Border'],['adjust','adjust','Adjust'],['erase','erase','Erase'],['restore','restore','Restore']];
RENDER.editor=()=>{const el=$('s-editor');
 if(!E.active){el.innerHTML=`<div class=pnl style="margin:40px auto;text-align:center;padding:40px;width:420px;align-self:flex-start"><h2>No sticker open</h2><p class=mut>Start from a photo, a text sticker or a generated sticker.</p><button class="btn pri" data-act=nav data-to=create>${ic('create')} Create</button> <button class=btn data-act=nav data-to=generate>${ic('gen')} Generate</button></div>`;return}
 el.innerHTML=`<div class="ed-l pnl" id=edl></div><div class=ed-c><div class="ed-bar pnl" id=edbar></div><div class="ed-view pnl vbg-${E.bgv}" id=edv><div class=wrap id=edw><canvas id=edc width=${CV} height=${CV}></canvas><canvas id=ovl width=${CV} height=${CV} style="position:absolute;left:0;top:0;pointer-events:none"></canvas></div></div><div class="ed-hist pnl" id=edh></div></div><div class="ed-r pnl" id=edr></div>`;
 ui();zoomTo(edFit(E.zoom));ui();redraw()};
/* UX-01: on a narrow screen the canvas opens at the largest zoom step that fits its view, so the whole sticker is visible; a person's own pick is kept when it fits */
const ZOOMS=[.5,.75,1,1.5,2];
function edFit(z){const v=$('edv');if(!v||!v.clientWidth)return z;const room=v.clientWidth-24;if(CV*z<=room)return z;const fit=ZOOMS.filter(x=>CV*x<=room);return fit.length?fit[fit.length-1]:ZOOMS[0]}
function zoomTo(z){E.zoom=z;['edc','ovl'].forEach(i=>{const c=$(i);if(c){c.style.width=CV*z+'px';c.style.height=CV*z+'px'}});const w=$('edw');if(w){w.style.width=CV*z+'px';w.style.height=CV*z+'px'}redraw()}
function layThumb(l){if(l.type==='subject')return`<img src="${l.cv.toDataURL('image/png')}">`;if(l.type==='emoji')return l.ch;return`<b style="font-size:15px;color:var(--mut)">T</b>`}
function ui(){if(!E.active||!$('edl'))return;const L=cur_();
 $('edbar').innerHTML=`<div class=ttl>${ic('sticker')} Sticker Editor <span class=mut style="font-weight:400">· ${esc(E.name)}</span></div>
  <button class=iconbtn data-act=edundo ${E.undo.length>1?'':'disabled'} title="Undo (Ctrl+Z)">${ic('undo')}</button><button class=iconbtn data-act=edredo ${E.redo.length?'':'disabled'} title="Redo (Ctrl+Y)">${ic('redo')}</button>
  <select id=edzoom aria-label="Zoom">${ZOOMS.map(z=>`<option value=${z} ${z===E.zoom?'selected':''}>${z*100}%</option>`).join('')}</select>
  <select id=edpv title="Preview background" aria-label="Preview background">${['checker','light','dark','chat'].map(b=>`<option value=${b} ${b===E.bgv?'selected':''}>${b}</option>`).join('')}</select>
  <button class="btn pri" data-act=edsave style="padding:8px 26px" title="${E.back?'Save the changes to this sticker and go back to '+(E.back.to==='agent'?'the AI':'the Studio'):'Save to a pack'}">${E.back?'Save to sticker':'Save'}</button><button class=iconbtn data-act=edback title="${E.back?'Back to '+(E.back.to==='agent'?'the AI':'the Studio')+' without saving':'Close'}">${ic('x')}</button>`;
 $('edzoom').onchange=e=>zoomTo(+e.target.value);$('edpv').onchange=e=>{E.bgv=e.target.value;$('edv').className='ed-view pnl vbg-'+E.bgv};
 $('edl').innerHTML=`<div class=ph style="padding:8px 10px 0">Layers</div>${TOOLS.map(([k,i,l])=>`<button class="tool ${E.tool===k?'on':''}" data-act=edtool data-t=${k}>${ic(i)}${l}</button>`).join('')}
  <div class=divl></div><div class=row style="justify-content:flex-end;margin:0 6px"><button class=iconbtn data-act=addtext title="Add text layer">${ic('plus')}</button></div>
  ${E.layers.slice().reverse().map(l=>`<div class="lay ${l.id===E.sel?'on':''} ${l.vis?'':'hid'}" data-act=laysel data-id=${l.id}><div class=th>${layThumb(l)}</div><span class=nm>${esc(label(l))}</span>
   <span class=ops><button data-act=layvis data-id=${l.id} title=Visible>${ic(l.vis?'eye':'eyeoff')}</button><button data-act=laylock data-id=${l.id} title=Lock>${ic(l.lock?'lock':'unlock')}</button><button data-act=layup data-id=${l.id} title="Bring forward">${ic('up')}</button><button data-act=laydown data-id=${l.id} title="Send back">${ic('down')}</button><button data-act=laydel data-id=${l.id} title=Delete>${ic('trash')}</button></span></div>`).join('')||'<div class=mut style="padding:8px">No layers</div>'}`;
 const hs=E.undo.concat(E.redo.slice().reverse()),now=E.undo.length-1;
 $('edh').innerHTML=`<div class=hh><b>History</b><span>/ Layers</span><span style="margin-left:auto">${now} edit${now===1?'':'s'} · click a thumbnail to jump back or forward</span></div><div class=film>${hs.map((h,i)=>`<div class="fr ${i===now?'on':''}" data-act=histgo data-i=${i} style="background-image:url(${h.thumb})" title="${i===0?'Start':'Edit '+i}"></div>`).join('')}</div>`;
 /* bring the current thumbnail into the strip's own view only: scrollIntoView would also scroll the stacked phone screen down past the canvas (UX-01) */
 const f=$('edh').querySelector('.fr.on'),fm=f&&f.parentElement;if(fm){const l=f.offsetLeft-fm.offsetLeft,r=l+f.offsetWidth;if(l<fm.scrollLeft)fm.scrollLeft=l;else if(r>fm.scrollLeft+fm.clientWidth)fm.scrollLeft=r-fm.clientWidth}
 $('edr').innerHTML=`<div class=ph>Properties</div>`+inspector(L)}
ACT.histgo=el=>{const i=+el.dataset.i;if(i<E.undo.length){while(E.undo.length>i+1)E.redo.push(E.undo.pop())}else{while(E.undo.length<=i)E.undo.push(E.redo.pop())}restore(E.undo[E.undo.length-1]);ui();redraw()};
/* every control carries its own name and unit (UX-04): the visible label above the pair is not associated with either input */
const sl=(lbl,inp,val,min,max,unit)=>`<div class=fld><label>${lbl}</label><div class=srow><input type=range data-inp=${inp} min=${min} max=${max} value=${val} aria-label="${lbl}${unit?` (${unit})`:''}"><input type=number data-inp=${inp} min=${min} max=${max} value=${val} aria-label="${lbl}${unit?` in ${unit}`:''}"></div></div>`;
const tg=(lbl,inp,on)=>`<div class=tgrow><b>${lbl}</b><input class=tgl type=checkbox data-inp=${inp} aria-label="${lbl}" ${on?'checked':''}></div>`;
function textCtl(L){return`<div class=ph style="margin-top:18px">Text</div><div class=fld><textarea rows=2 data-inp=text dir=auto aria-label="Text">${esc(L.text)}</textarea></div>
  <div class=line><select data-inp=font aria-label="Font">${FONTS.map(f=>`<option ${f===L.font?'selected':''}>${f}</option>`).join('')}</select><input type=number data-inp=tsize min=16 max=220 value=${L.size} aria-label="Text size in px" style="max-width:74px;background:var(--fill);border:1px solid var(--bd)"><label class=chipb title=Bold><input type=checkbox data-inp=bold aria-label="Bold" ${L.bold?'checked':''}>B</label><label class=chipb title=Italic style="font-style:italic"><input type=checkbox data-inp=italic aria-label="Italic" ${L.italic?'checked':''}>I</label></div>
  <div class=fld><div class=sw><input type=color data-inp=tcolor value="${L.color}" aria-label="Fill colour"><span class=mut>fill</span><input type=color data-inp=scolor value="${L.stroke}" aria-label="Outline colour"><span class=mut>outline</span></div></div>${sl('Outline width','sw',L.sw,0,30,'px')}`}
function transform(L){return sl('Scale','scale',Math.round(L.s*100),10,400,'%')+sl('Rotate','rot',Math.round(L.r),-180,180,'degrees')}
function borderCtl(){const B=E.border;return`<div class=tgrow><b>Border</b><input class=tgl type=checkbox data-inp=bon aria-label="Border" ${B.on?'checked':''}></div><div class=line><div class=sw><input type=color data-inp=bcolor value="${B.color}" aria-label="Border colour"></div><span class="mut fixed">Width</span><input type=number data-inp=bw min=0 max=40 value=${B.w} aria-label="Border width in px" style="background:var(--fill);border:1px solid var(--bd)"></div>
  ${tg('Shadow','bshadow',B.shadow)}${B.shadow?sl('Shadow blur','bblur',B.blur,0,30,'px'):''}${E.outlined?'<div class=mut style="font-size:12px">This sticker already has a baked outline; keep Border off unless you erased it.</div>':''}`}
function inspector(L){let h='';const t=E.tool,sub=E.layers.filter(l=>l.type==='subject');
 const layerCtl=()=>L?`<div class=ph style="font-size:14px;color:var(--mut);font-weight:500">${L.type==='subject'?'Sticker':L.type==='text'?'Text layer':'Emoji layer'}</div>`+transform(L):'<div class=mut>Click a layer on the canvas or in the list. Drag to move; corner handle scales, top handle rotates (Shift snaps 15°).</div>';
 if(t==='select'){h=layerCtl()+(L&&L.type==='text'?textCtl(L):L&&L.type==='emoji'?`<div class=ph style="margin-top:18px">Emoji</div><div class=line><div style="font-size:34px;line-height:1">${L.ch}</div><span class="mut fixed">Size</span><input type=number data-inp=esize min=24 max=300 value=${L.size} aria-label="Emoji size in px" style="background:var(--fill);border:1px solid var(--bd)"></div>`:'')+
   (L?`<div class=row><button class="btn sm" data-act=laydup>Duplicate</button><button class="btn sm dng" data-act=laydel data-id=${L.id}>${ic('trash')} Delete</button></div>`:'')+
   (L&&L.type==='subject'?`<div class=row><button class="btn sm" data-act=resetsub>Reset cutout edits</button>${E.srcBlob&&L.id===E.mainId?`<button class="btn sm" data-act=recut>Auto cutout again</button>`:''}</div>${E.info?`<div class=mut style="font-size:12px">cutout: ${esc(E.info.method)}${E.info.foreground?' · '+Math.round(E.info.foreground*100)+'% kept':''}</div>`:''}`:'')+`<div class=divl></div>`+borderCtl()}
 else if(t==='text'){h=(L&&L.type==='text'?transform(L)+textCtl(L):`<div class=mut>Add a text layer, or select one.</div><div class=row><button class="btn pri sm" data-act=addtext>${ic('plus')} Add text</button></div>`)}
 else if(t==='emoji'){h=`<div class=emo>${EMOJIS.map(c=>`<button data-act=addemoji data-c="${c}">${c}</button>`).join('')}</div>`+(L&&L.type==='emoji'?`<div class=ph style="margin-top:16px">Selected</div>`+transform(L)+sl('Size','esize',L.size,24,300):'')}
 else if(t==='border'){h=borderCtl()}
 else if(t==='adjust'){const S=L&&L.type==='subject'?L:sub[0];h=S?sl('Brightness','adjb',S.adj.b,40,180,'%')+sl('Contrast','adjc',S.adj.c,40,180,'%')+sl('Saturation','adjs',S.adj.s,0,220,'%')+`<div class=row><button class="btn sm" data-act=adjreset>Reset</button></div>`:'<div class=mut>No sticker layer.</div>'}
 else{h=(L&&L.type==='subject'?sl('Brush size','brush',E.brush,6,160,'px')+`<div class=mut style="font-size:12px">${t==='erase'?'Paint over background you want to remove.':'Paint to bring back parts that were removed.'} The circle shows the brush; edits are undoable.</div>`:'<div class=mut>Select a sticker layer first.</div>')}
 return h}
const INP={scale:e=>{const L=cur_();if(L)L.s=e.value/100},rot:e=>{const L=cur_();if(L)L.r=+e.value},text:e=>{const L=cur_();if(L)L.text=e.value||' '},font:e=>{cur_().font=e.value},tsize:e=>{cur_().size=+e.value},esize:e=>{cur_().size=+e.value},
 tcolor:e=>{cur_().color=e.value},scolor:e=>{cur_().stroke=e.value},sw:e=>{cur_().sw=+e.value},bold:e=>{cur_().bold=e.checked},italic:e=>{cur_().italic=e.checked},
 bon:e=>{E.border.on=e.checked},bw:e=>{E.border.w=+e.value},bcolor:e=>{E.border.color=e.value},bshadow:e=>{E.border.shadow=e.checked},bblur:e=>{E.border.blur=+e.value},
 brush:e=>{E.brush=+e.value},adjb:e=>{adjL().adj.b=+e.value},adjc:e=>{adjL().adj.c=+e.value},adjs:e=>{adjL().adj.s=+e.value}};
const adjL=()=>{const L=cur_();return L&&L.type==='subject'?L:E.layers.find(l=>l.type==='subject')};
const S_ED=$('s-editor');
S_ED.addEventListener('input',e=>{const n=e.target.dataset&&e.target.dataset.inp;if(!n||!INP[n])return;INP[n](e.target);redraw();if(e.target.type==='range'||e.target.type==='number')S_ED.querySelectorAll('[data-inp="'+n+'"]').forEach(x=>{if(x!==e.target&&(x.type==='range'||x.type==='number'))x.value=e.target.value});if(n==='text'||n==='tsize')ui2()});
S_ED.addEventListener('change',e=>{const n=e.target.dataset&&e.target.dataset.inp;if(!n||!INP[n])return;INP[n](e.target);redraw();commit();ui()});
function ui2(){const L=cur_();if(L&&$('edl')){const rows=$('edl').querySelectorAll('.lay');rows.forEach(r=>{if(+r.dataset.id===L.id)r.querySelector('.nm').textContent=label(L)})}}
ACT.edback=()=>{if(E.back){const to=E.back.to;E.back=null;E.active=false;location.hash=to==='agent'?'#/agent':'#/studio'}else location.hash='#/create'};ACT.edundo=undo;ACT.edredo=redo;

ACT.edtool=el=>{E.tool=el.dataset.t;if((E.tool==='erase'||E.tool==='restore')&&!(cur_()&&cur_().type==='subject')){const s=E.layers.find(l=>l.type==='subject');if(s)E.sel=s.id}ui();redraw();$('edc')&&($('edc').style.cursor=E.tool==='erase'||E.tool==='restore'?'crosshair':E.tool==='select'?'default':'default')};
ACT.laysel=el=>{E.sel=+el.dataset.id;ui();redraw()};
ACT.layvis=(el,e)=>{e.stopPropagation();const L=E.layers.find(l=>l.id===+el.dataset.id);L.vis=!L.vis;commit();ui();redraw()};
ACT.laylock=(el,e)=>{e.stopPropagation();const L=E.layers.find(l=>l.id===+el.dataset.id);L.lock=!L.lock;commit();ui()};
function moveLayer(id,d){const i=E.layers.findIndex(l=>l.id===id),j=i+d;if(j<0||j>=E.layers.length)return;const[x]=E.layers.splice(i,1);E.layers.splice(j,0,x);commit();ui();redraw()}
ACT.layup=(el,e)=>{e.stopPropagation();moveLayer(+el.dataset.id,1)};ACT.laydown=(el,e)=>{e.stopPropagation();moveLayer(+el.dataset.id,-1)};
ACT.laydel=(el,e)=>{e.stopPropagation();const id=+el.dataset.id;E.layers=E.layers.filter(l=>l.id!==id);if(E.sel===id)E.sel=null;commit();ui();redraw()};
ACT.laydup=()=>{const L=cur_();if(!L)return;let n;if(L.type==='subject'){n=mkSubject(L.cv,{name:L.name+' copy'});Object.assign(n,{x:L.x+24,y:L.y+24,s:L.s,r:L.r,adj:{...L.adj}})}else n={...L,id:E.uid++,x:L.x+24,y:L.y+24};pushLayer(n)};
ACT.addtext=()=>{E.tool='text';pushLayer(mkText('Text'))};ACT.addemoji=el=>pushLayer(mkEmoji(el.dataset.c));
ACT.resetsub=()=>{const L=cur_();if(!L||L.type!=='subject')return;L.ver++;const x=L.cv.getContext('2d');x.globalCompositeOperation='source-over';x.clearRect(0,0,L.cv.width,L.cv.height);x.drawImage(L.orig,0,0);commit();redraw();toast('Cutout edits reset')};
ACT.adjreset=()=>{const L=adjL();if(!L)return;L.adj={b:100,c:100,s:100};commit();ui();redraw()};
ACT.recut=async()=>{const L=E.pool[E.mainId];if(!L||!E.srcBlob)return;toast('Cutting out again…');const r=await fetch('/api/cutout',{method:'POST',body:E.srcBlob});if(!r.ok)return toast('cutout failed',1);
 try{E.info=JSON.parse(r.headers.get('X-Cutout')||'{}')}catch(e){}const img=await createImageBitmap(await r.blob());L.ver++;L.cv=document.createElement('canvas');L.cv.width=img.width;L.cv.height=img.height;L.cv.getContext('2d',{willReadFrequently:true}).drawImage(img,0,0);L.orig=clone(L.cv);commit();ui();redraw();toast('Cutout refreshed')};
/* canvas pointer interaction */
const canvasPt=e=>{const r=$('edc').getBoundingClientRect();return[(e.clientX-r.left)/r.width*CV,(e.clientY-r.top)/r.height*CV]};
function brushAt(L,p,q){const x=L.cv.getContext('2d'),r=E.brush/2/L.s,[ax,ay]=toLocal(L,...p),[bx,by]=q?toLocal(L,...q):[ax,ay],w=L.cv.width/2,h=L.cv.height/2;
 const n=Math.max(1,Math.ceil(Math.hypot(bx-ax,by-ay)/(r/2)));for(let i=0;i<=n;i++){const px=(ax+(bx-ax)*i/n)+w,py=(ay+(by-ay)*i/n)+h;x.save();x.beginPath();x.arc(px,py,r,0,7);x.clip();
  if(E.tool==='erase')x.clearRect(px-r,py-r,2*r,2*r);else x.drawImage(L.orig,0,0);x.restore()}}
S_ED.addEventListener('pointerdown',e=>{const c=$('edc');if(!c||e.target.closest('.ed-l,.ed-r,.ed-bar')||!e.target.closest('.ed-view'))return;
 if(e.button===1||E.space){const v=$('edv');E.mode={k:'pan',x:e.clientX,y:e.clientY,sl:v.scrollLeft,st:v.scrollTop};v.setPointerCapture(e.pointerId);e.preventDefault();return}
 if(e.button!==0)return;const p=canvasPt(e),L=cur_();
 if(E.tool==='erase'||E.tool==='restore'){if(L&&L.type==='subject'&&!L.lock){L.ver++;E.mode={k:'brush',last:p};brushAt(L,p);redraw();$('edv').setPointerCapture(e.pointerId)}return}
 if(L&&L.vis&&!L.lock){const h=handles(L),hr=12/E.zoom;
  if(Math.hypot(p[0]-h.rot[0],p[1]-h.rot[1])<hr){E.mode={k:'rot',a0:Math.atan2(p[1]-L.y,p[0]-L.x),r0:L.r};$('edv').setPointerCapture(e.pointerId);return}
  if(Math.hypot(p[0]-h.corners[2][0],p[1]-h.corners[2][1])<hr){E.mode={k:'scale',d0:Math.hypot(p[0]-L.x,p[1]-L.y),s0:L.s};$('edv').setPointerCapture(e.pointerId);return}}
 const H=hit(...p);if(H){E.sel=H.id;E.mode={k:'move',sx:p[0],sy:p[1],ox:H.x,oy:H.y,moved:false};$('edv').setPointerCapture(e.pointerId)}else{E.sel=null}
 ui();redraw()});
S_ED.addEventListener('pointermove',e=>{const c=$('edc');if(!c)return;const M=E.mode,L=cur_();
 if(M&&M.k==='pan'){const v=$('edv');v.scrollLeft=M.sl-(e.clientX-M.x);v.scrollTop=M.st-(e.clientY-M.y);return}
 const p=canvasPt(e);E.ptr=p;
 if(M&&L){if(M.k==='move'){L.x=M.ox+p[0]-M.sx;L.y=M.oy+p[1]-M.sy;M.moved=true}
  else if(M.k==='scale'){L.s=Math.max(.05,Math.min(6,M.s0*Math.hypot(p[0]-L.x,p[1]-L.y)/M.d0))}
  else if(M.k==='rot'){let r=M.r0+(Math.atan2(p[1]-L.y,p[0]-L.x)-M.a0)*180/Math.PI;r=((r+540)%360)-180;if(e.shiftKey)r=Math.round(r/15)*15;L.r=r}
  else if(M.k==='brush'){brushAt(L,p,M.last);M.last=p}redraw();return}
 if((E.tool==='erase'||E.tool==='restore')){redraw();return}
 if(L&&L.vis&&!L.lock){const h=handles(L),hr=12/E.zoom;c.style.cursor=Math.hypot(p[0]-h.rot[0],p[1]-h.rot[1])<hr?'grab':Math.hypot(p[0]-h.corners[2][0],p[1]-h.corners[2][1])<hr?'nwse-resize':hit(...p)?'move':'default'}});
const endMode=()=>{const M=E.mode;E.mode=null;if(!M||M.k==='pan')return;if(M.k!=='move'||M.moved){commit();ui()}};
S_ED.addEventListener('pointerup',endMode);S_ED.addEventListener('pointercancel',endMode);
S_ED.addEventListener('wheel',e=>{if(!e.ctrlKey||!$('edc'))return;e.preventDefault();const z=[.5,.75,1,1.5,2],i=z.indexOf(E.zoom),j=Math.max(0,Math.min(4,i+(e.deltaY<0?1:-1)));zoomTo(z[j]);ui()},{passive:false});
document.addEventListener('keydown',e=>{if(route_!=='editor'||!E.active)return;const t=e.target.tagName;if(t==='INPUT'||t==='TEXTAREA'||t==='SELECT')return;
 if(e.code==='Space'){E.space=true;e.preventDefault();return}
 if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();e.shiftKey?redo():undo();return}
 if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='y'){e.preventDefault();redo();return}
 const L=cur_();if(!L||L.lock)return;
 if(e.key==='Delete'||e.key==='Backspace'){E.layers=E.layers.filter(l=>l.id!==L.id);E.sel=null;commit();ui();redraw();return}
 const d={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[e.key];if(d){e.preventDefault();const k=e.shiftKey?10:1;L.x+=d[0]*k;L.y+=d[1]*k;redraw();clearTimeout(E.nt);E.nt=setTimeout(commit,400)}});
document.addEventListener('keyup',e=>{if(e.code==='Space')E.space=false});

/* ---------- Export (DESKTOP_06) */
function finalCanvas(){const c=off();compose(c.getContext('2d'),true);return c}
ACT.edsave=async()=>{if(!E.layers.some(l=>l.vis)){return toast('Nothing to save: every layer is hidden',1)}
 const c=finalCanvas();E.dataUrl=c.toDataURL('image/png');
 if(E.back){/* opened from Generate: the edit is saved in place and we go back, we never left that screen */
  const b=E.back,r=await post(`/api/generations/${b.gen}/edit`,{index:b.index,png:E.dataUrl});if(!r.ok)return dlg(`<h2>Could not save</h2><div class=warn>${esc(r.j.error||'The server did not answer. Restart it and try again.')}</div><div class=mut>Your edit is still open in the editor.</div><div class=row style="justify-content:flex-end"><button class="btn pri" data-act=dlgx>OK</button></div>`);
  E.back=null;E.active=false;E.dataUrl='';toast('Sticker saved');location.hash=b.to==='agent'?'#/agent':'#/studio';return}           /* back to where the person was: the AI, or the Studio */
 E.blob=await new Promise(r=>c.toBlob(r,'image/png'));E.saved=null;location.hash='#/export'};
RENDER.export=async()=>{await loadLib();const el=$('s-export');
 if(!E.dataUrl){el.innerHTML=`<div class=card style="margin:40px auto;width:420px;text-align:center;padding:30px"><h2>Nothing to export</h2><button class="btn pri" data-act=nav data-to=create>Create</button></div>`;return}
 const packs=LIB.packs,pid=E.pack&&packById(E.pack)?E.pack:packs.length?packs[packs.length-1].id:'__new',sv=E.saved;
 el.innerHTML=`<div class=page><div class=row style="margin-top:0"><button class="btn sm" data-act=exback>${ic('back')} Back to editor</button></div><h1>Save sticker</h1>
  <div class=xgrid><div class="pvbox bg-checker"><img src="${E.dataUrl}"></div>
  <div class=card><div class=fld><label for=xn>Name</label><input type=text id=xn value="${esc(E.name)}"></div><div class=fld><label for=xe>Emoji tag (Telegram requires at least one)</label><input type=text id=xe value="${esc(E.emoji)}"></div>
   <div class=fld><label for=xp>Pack</label><select id=xp>${packs.map(p=>`<option value=${p.id} ${p.id===pid?'selected':''}>${esc(p.name)} (${p.stickers.length})</option>`).join('')}<option value=__new ${pid==='__new'?'selected':''}>+ New pack…</option></select></div>
   <div class=fld id=xnew style="display:${pid==='__new'?'block':'none'}"><label for=xnn>New pack name</label><input type=text id=xnn value="My Pack"></div>
   <div class=kv style="margin:10px 0"><span>type</span><span>Static</span><span>size</span><span>512 × 512 · ${Math.ceil(E.blob.size/1024)}KB PNG (WebP if over the limit)</span></div>
   ${sv?`<div class=warn style="background:var(--ok-l);border-color:var(--pri)">${ic('check').replace('<svg','<svg width=14 height=14')} Saved as <b>${esc(sv.file)}</b> (${sv.kb}KB)</div>`:''}
   <div class=row><button class="btn pri" data-act=xsave style="flex:1;justify-content:center">${ic('check')} Save to stickerloom</button></div>
   <div class=row><button class=btn data-act=xdl style="flex:1;justify-content:center">${ic('download')} Download PNG</button></div>
   ${sv?`<div class=row><button class=btn data-act=xopen data-id=${sv.pack_id}>Open pack</button><button class=btn data-act=xmore>Make another</button></div>`:''}</div></div></div>`;
 $('xp').onchange=e=>{$('xnew').style.display=e.target.value==='__new'?'block':'none'}};
ACT.exback=()=>{location.hash='#/editor'};
ACT.xsave=async()=>{E.name=$('xn').value.trim()||'sticker';E.emoji=$('xe').value.trim()||'🙂';let pid=$('xp').value;
 if(pid==='__new'){const r=await post('/api/packs',{name:$('xnn').value});if(!r.ok)return toast(r.j.error,1);pid=r.j.id}
 const r=await fetch(`/api/packs/${pid}/render?name=${encodeURIComponent(E.name)}&emoji=${encodeURIComponent(E.emoji)}`,{method:'POST',body:E.blob});let j={};try{j=await r.json()}catch(e){}
 if(!r.ok)return toast(j.error||'save failed',1);E.pack=pid;E.saved={...j,pack_id:pid};await loadLib();toast(`Saved to ${packById(pid).name}`);RENDER.export()};
ACT.xdl=()=>{const a=document.createElement('a');a.href=E.dataUrl;a.download=(E.name||'sticker').replace(/[^\w-]+/g,'_')+'.png';document.body.appendChild(a);a.click();a.remove()};
ACT.xopen=el=>{location.hash='#/pack/'+el.dataset.id};ACT.xmore=()=>{E.active=false;E.dataUrl='';location.hash='#/create'};
