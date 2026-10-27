/* Local Echo contact: replies/reactions test the sent sticker's current assigned particle settings.
   Messages live in this browser. Preview selection and rendering belong to the engine. */
'use strict';
if(typeof ICONS!=='undefined'){ICONS.phone='<rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 18h2"/>';
 ICONS.desktop='<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M9 20h6M12 16v4"/>';
 ICONS.clock='<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'}
const CH={msgs:[],tray:false,pk:null,typing:false,draft:'',pending:null,epoch:0,seq:0,play:{},requests:{},view:'desktop',q:'',popEl:null};
try{if(localStorage.getItem('stickerloom.chat.view')==='mobile')CH.view='mobile'}catch(e){}   /* a per-browser look: the chat as on a phone, or full width */
try{CH.msgs=JSON.parse(localStorage.getItem('stickerloom.chat')||'[]')}catch(e){}
if(!Array.isArray(CH.msgs))CH.msgs=[];
const chSave=()=>{try{localStorage.setItem('stickerloom.chat',JSON.stringify(CH.msgs.slice(-200)))}catch(e){}};
const hm=t=>new Date(t).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
const chPreview=m=>!m?'No messages yet':m.kind==='sticker'?'Sticker':esc(m.text);
const chSticker=(s,pack_id)=>({id:s.id,pack_id:s.pack_id||pack_id,type:s.type,file:s.file,name:s.name});
/* drawn INSIDE the reaction badge with the burst's own origin (a preset's, e.g. a fountain's low start) on the badge's centre, so it comes out of the heart
   whatever the badge's size, as Telegram plays a reaction's effect (studio.css .ch-particle) */
const chParticleH=m=>{const p=CH.play[m.id];if(!p)return'';const o=Array.isArray(p.origin)&&p.origin.length===2&&p.origin.every(Number.isFinite)?p.origin.map(v=>Math.min(1,Math.max(0,v))):[0.5,0.5];
 return`<img class=ch-particle src="${esc(p.url)}" alt="" aria-hidden=true data-particle-set="${esc(p.set)}" style="transform:translate(${(-o[0]*100).toFixed(1)}%,${(-o[1]*100).toFixed(1)}%)">`};
const chPlayable=m=>m.kind==='sticker'&&m.s&&m.s.id&&m.s.pack_id;
/* a reaction on a sticker replays its burst; Echo's own sticker can be liked (the heart on hover, or a double-click, as Telegram's double-tap) and then plays it too */
const chReactionH=m=>m.react?(chPlayable(m)?`<button type=button class=react data-act=chreplay data-id="${esc(m.id)}" title="Replay reaction" aria-label="Replay reaction">${esc(m.react)}${chParticleH(m)}</button>`:`<span class=react>${esc(m.react)}</span>`)
 :m.from==='in'&&chPlayable(m)?`<button type=button class="react like" data-act=chlike data-id="${esc(m.id)}" title="Like" aria-label="Like this sticker">♡</button>`:'';
function chList(){const m=CH.msgs[CH.msgs.length-1];
 $('col2').innerHTML=`<div class=c2h><h1>Chats</h1></div><div class=c2s><input type=search placeholder="Search chats…" disabled></div>
  <div class=c2l><div class="crow on"><div class="cv chav">E</div><div style="min-width:0"><b>Echo</b><small>${m&&m.from==='out'?'You: ':''}${chPreview(m)}</small></div><span class=meta>${m?hm(m.t):''}</span></div></div>`}
const chMsgH=m=>{const t=`<span class=tm>${hm(m.t)}${m.from==='out'?' ✓✓':''}</span>`;
 return m.kind==='sticker'?`<div class="cm c${m.from}"><div class="bub stk" data-chmsg="${esc(m.id)}">${media(m.s)}${chReactionH(m)}${t}</div></div>`
  :`<div class="cm c${m.from}"><div class=bub>${esc(m.text)}${chReactionH(m)}${t}</div></div>`};
/* the sticker panel, as Telegram's: a small card over the chat, above the composer. Search (name or emoji tag, across every pack), the emoji tags the
   stickers actually carry as one-tap filters, a borderless 5-column grid, and the packs along the bottom with Recent (the stickers sent here) first */
const chAll=()=>LIB.packs.flatMap(p=>p.stickers.map(s=>({...s,pack_id:p.id})));
function chRecent(){const seen=new Set(),out=[];
 for(const m of [...CH.msgs].reverse())if(m.kind==='sticker'&&m.from==='out'&&m.s&&!seen.has(m.s.id)){const p=packById(m.s.pack_id),s=p&&p.stickers.find(x=>x.id===m.s.id);if(s){seen.add(s.id);out.push({...s,pack_id:p.id})}}
 return out.slice(0,20)}
function chItems(){const q=CH.q.trim().toLowerCase();if(q)return chAll().filter(s=>((s.name||'')+' '+(s.emoji||'')).toLowerCase().includes(q));
 if(CH.pk==='recent')return chRecent();const p=packById(CH.pk);return p?p.stickers.map(s=>({...s,pack_id:p.id})):[]}
const chGridH=()=>{const it=chItems();return it.map(s=>`<button type=button class=tgs data-act=chpick data-p="${esc(s.pack_id)}" data-id="${esc(s.id)}" title="${esc(((s.emoji||'')+' '+(s.name||'')).trim())}">${media(s)}</button>`).join('')
 ||`<div class="mut tgempty">${CH.q?'No sticker matches.':CH.pk==='recent'?'The stickers you send show here.':'No stickers in this pack.'}</div>`};
function chTrayH(){const ps=LIB.packs.filter(p=>p.stickers.length);
 if(!ps.length)return`<div class=tgtabs><span class=on>Stickers</span></div><div class="mut tgempty">No stickers yet.<br><button class="btn sm" data-act=nav data-to=create>${ic('create')} Create one</button></div>`;
 if(CH.pk!=='recent'&&!ps.some(p=>p.id===CH.pk))CH.pk=chRecent().length?'recent':ps[0].id;
 const ems=[...new Set(chAll().map(s=>s.emoji).filter(Boolean))].slice(0,8),on=k=>!CH.q&&CH.pk===k?' on':'';
 return`<div class=tgtabs><span class=on>Stickers</span></div>
  <div class=tgsearch>${ic('search')}<input type=search id=chq placeholder=Search value="${esc(CH.q)}" autocomplete=off aria-label="Search stickers"><span class=tgems>${ems.map(e=>`<button type=button class="tgem${CH.q===e?' on':''}" data-act=chem data-e="${esc(e)}" title="Stickers tagged ${esc(e)}">${esc(e)}</button>`).join('')}</span></div>
  <div class=tggrid id=chgrid>${chGridH()}</div>
  <div class=tgpacks><button type=button class="tgp${on('recent')}" data-act=chpack data-id=recent title=Recent aria-label=Recent>${ic('clock')}</button>${ps.map(p=>`<button type=button class="tgp${on(p.id)}" data-act=chpack data-id="${esc(p.id)}" title="${esc(p.name)}" aria-label="${esc(p.name)}">${coverMedia(p)}</button>`).join('')}</div>`}
/* the panel is ONE element per opening: a message, the typing dots or a sent sticker redraw the chat around it, never the panel (so it does not pop in
   again); switching packs or searching changes only what is inside it. It slides up when it opens and down when it closes. */
function chPop(){if(!CH.popEl){const el=document.createElement('div');el.className='tgpop';el.setAttribute('role','dialog');el.setAttribute('aria-label','Stickers');CH.popEl=el;chPopFill()}return CH.popEl}
function chPopFill(){const el=CH.popEl;if(!el)return;el.innerHTML=chTrayH();const sq=el.querySelector('#chq');
 if(sq)sq.oninput=e=>{CH.q=e.target.value;const g=el.querySelector('#chgrid');if(g)g.innerHTML=chGridH();el.querySelectorAll('.tgem').forEach(b=>b.classList.toggle('on',b.dataset.e===CH.q))}}
function chPopClose(){CH.tray=false;const el=CH.popEl;CH.popEl=null;const b=document.querySelector('[data-act=chtray]');if(b)b.classList.remove('on');
 if(!el)return;el.classList.add('out');setTimeout(()=>el.remove(),180)}
function chDraw(){if(route_!=='chat')return;const el=$('s-chat'),inp=$('chin');if(inp)CH.draft=inp.value;
 el.innerHTML=`<div class="chat${CH.view==='mobile'?' mobile':''}"><div class=chh><div class="cv chav">E</div><div style="flex:1"><b>Echo</b><small>${CH.typing?'typing…':'online'}</small></div><button class=iconbtn data-act=chview title="${CH.view==='mobile'?'Desktop view':'Mobile view'}" aria-label="${CH.view==='mobile'?'Switch to desktop view':'Switch to mobile view'}">${ic(CH.view==='mobile'?'desktop':'phone')}</button><button class=iconbtn data-act=chclear title="Clear chat">${ic('trash')}</button></div>
  <div class=chm id=chm>${CH.msgs.map(chMsgH).join('')||'<div class="mut chempty">Say hi or send a sticker. It comes back to you and your message gets a like.</div>'}${CH.typing?'<div class="cm in"><div class="bub typing"><i></i><i></i><i></i></div></div>':''}</div>
  ${CH.tray?'<div id=tgslot></div>':''}
  <div class=chc><button class="iconbtn ${CH.tray?'on':''}" data-act=chtray title=Stickers>${ic('sticker')}</button><input type=text id=chin placeholder="Type a message" autocomplete=off value="${esc(CH.draft)}"><button class="btn pri" data-act=chsend>${ic('chat')} Send</button></div></div>`;
 const m=$('chm');m.scrollTop=m.scrollHeight;const i=$('chin');i.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();ACT.chsend()}};
 const slot=$('tgslot');if(slot){const pop=chPop();slot.replaceWith(pop);pop.querySelectorAll('video').forEach(v=>{if(v.paused)v.play().catch(()=>0)})}   /* a moved video pauses: play it on */
 const sq=$('chq'),caret=x=>{x.focus();x.setSelectionRange(x.value.length,x.value.length)};
 caret(sq&&CH.q?sq:i);drawCol2()}
async function chPlay(id){const m=CH.msgs.find(x=>x.id===id);if(!m||!chPlayable(m))return;
 const epoch=CH.epoch,token=(CH.requests[id]||0)+1;CH.requests[id]=token;
 delete CH.play[id];chDraw();
 let r;try{r=await api(`/api/packs/${encodeURIComponent(m.s.pack_id)}/stickers/${encodeURIComponent(m.s.id)}/particle-preview`)}catch(e){return}
 if(epoch!==CH.epoch||CH.requests[id]!==token||!CH.msgs.includes(m)||route_!=='chat'||!r.ok||!r.j.set||!r.j.url)return;
 const url=String(r.j.url),sep=url.includes('?')?'&':'?';CH.play[id]={set:r.j.set,url:url+sep+'echo='+encodeURIComponent(id+'-'+token),origin:(r.j.params||{}).origin};chDraw();
 setTimeout(()=>{if(epoch===CH.epoch&&CH.requests[id]===token){delete CH.play[id];chDraw()}},3200)}
function chSend(m){const o={id:Date.now().toString(36)+'-'+(++CH.seq),t:Date.now(),from:'out',...m},epoch=CH.epoch;CH.msgs.push(o);chSave();chDraw();
 setTimeout(()=>{if(epoch!==CH.epoch||!CH.msgs.includes(o))return;CH.typing=true;chDraw()},450);
 setTimeout(()=>{if(epoch!==CH.epoch||!CH.msgs.includes(o))return;CH.typing=false;o.react=o.kind==='sticker'?'❤️':'👍';CH.msgs.push({...o,id:o.id+'e',reply_to:o.id,t:Date.now(),from:'in',react:undefined});chSave();chDraw();if(o.kind==='sticker')chPlay(o.id)},1500)}
RENDER.chat=async()=>{await loadLib();chDraw();if(CH.pending){const s=CH.pending;CH.pending=null;chSend({kind:'sticker',s:chSticker(s)})}};
ACT.chsend=()=>{const i=$('chin'),v=i.value.trim();if(!v)return;CH.draft='';i.value='';chSend({kind:'text',text:v})};
ACT.chtray=()=>{if(CH.tray)return chPopClose();CH.tray=true;chDraw()};ACT.chpack=el=>{CH.pk=el.dataset.id;CH.q='';chPopFill()};
ACT.chem=el=>{CH.q=CH.q===el.dataset.e?'':el.dataset.e;chPopFill()};
/* the panel closes like Telegram's: a click outside it (not on its own button) or Esc */
if(typeof document!=='undefined'&&document.addEventListener){
 document.addEventListener('click',e=>{if(!CH.tray||route_!=='chat'||!e.target.closest||e.target.closest('.tgpop,[data-act=chtray]'))return;chPopClose()},true);
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&CH.tray&&route_==='chat')chPopClose()})}
ACT.chpick=el=>{const p=packById(el.dataset.p||CH.pk),s=p&&p.stickers.find(x=>x.id===el.dataset.id);if(!s)return;if(CH.tray&&typeof document!=='undefined')chPopClose();chSend({kind:'sticker',s:chSticker(s,p.id)})};     /* a pick sends and closes the panel, as Telegram does */
ACT.chreplay=el=>chPlay(el.dataset.id);
function chLike(id){const m=CH.msgs.find(x=>x.id===id&&x.from==='in');if(!m||!chPlayable(m))return;if(!m.react){m.react='❤️';chSave()}return chPlay(id)}
ACT.chlike=el=>chLike(el.dataset.id);
if(typeof document!=='undefined'&&document.addEventListener)document.addEventListener('dblclick',e=>{if(route_!=='chat'||!e.target.closest)return;const b=e.target.closest('.cm.cin [data-chmsg]');if(b){e.preventDefault();chLike(b.dataset.chmsg)}});
ACT.chview=()=>{CH.view=CH.view==='mobile'?'desktop':'mobile';try{localStorage.setItem('stickerloom.chat.view',CH.view)}catch(e){}chDraw()};
function chClear(){CH.epoch++;CH.msgs=[];CH.play={};CH.requests={};CH.typing=false;CH.pending=null;chSave();chDraw()}
ACT.chclear=()=>confirmDlg('Clear this chat?',chClear);
