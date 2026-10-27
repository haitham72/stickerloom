/* Home (#/home): the first screen and the logo's target, drawn from the real library (GET /api/library): the featured stickers, the shortcuts into the app,
   the packs with search and filters, how it works. Nothing here is a preview: every card opens the real pack, every shortcut opens the real screen. */
'use strict';
const HM={q:'',filter:'all',pub:false,feed:null};
/* the first start of a browser session opens Home, whatever screen the tab or bookmark was on. A link to one thing (#/pack/<id>, #/agent/S012) still opens
   that thing. Runs before the first route() (animate.js) */
const hmFirstStart=(store,hash)=>{try{if(store.getItem('stickerloom.home.seen')==='1')return false;store.setItem('stickerloom.home.seen','1')}catch(e){return false}
 return!hash.replace(/^#\/?/,'').includes('/')};
try{if(hmFirstStart(sessionStorage,location.hash))history.replaceState(null,'','#/home')}catch(e){}
const HM_TINTS=['ice','lilac','cream','lavender','mint','rose','sand','green'];
/* what a pack is, for the filters and the card's subtitle (pure) */
const hmPackInfo=p=>{const st=p.stickers||[],animated=st.filter(s=>s.type==='animated').length,particles=st.some(s=>Array.isArray(s.particles)&&s.particles.length);
 const tags=[animated?'animated':null,st.length-animated?'static':null,particles?'particles':null].filter(Boolean);
 return{animated,still:st.length-animated,particles,tags,telegram:((p.telegram||{}).sets||[]).map(x=>x.link).find(Boolean)||'',
  label:animated?(st.length-animated?'Mixed pack':'Animated'):'Static pack',
  text:(p.name+' '+st.map(s=>s.name+' '+(s.emoji||'')).join(' ')).toLowerCase()}};
/* up to three stickers for the hero, newest pack first, animated first (pure) */
const hmHero=packs=>{const out=[];for(const p of [...packs].sort((a,b)=>(b.created||0)-(a.created||0))){
  const st=p.stickers||[],s=st.find(x=>x.id===p.cover)||st.find(x=>x.type==='animated')||st[0];if(s)out.push(s);if(out.length===3)break}return out};
const hmIc=n=>`<span class=hm-ti>${ic(n)}</span>`;
/* The landing page shows individual stickers; Library keeps the complete collection. */
const hmStickers=(packs,q,filter)=>[...packs].sort((a,b)=>(b.created||0)-(a.created||0))
 .flatMap(p=>(p.stickers||[]).filter(s=>!((s.source||{}).particle_set)&&(
  (filter==='all'||filter==='animated'&&s.type==='animated'||filter==='static'&&s.type!=='animated'||filter==='particles'&&s.particles?.length)
  &&(!q||`${p.name} ${s.name||''} ${s.emoji||''}`.toLowerCase().includes(q)))).map(s=>({p,s}))).slice(0,24);
const hmColumns=width=>width>=1220?6:width>=1000?5:width>=780?4:width>=560?3:2;
const hmBands=(items,cols)=>[items.slice(0,cols*2),items.slice(cols*2,cols*4)];
let hmResize=null;
function hmCard({p,s},i){const animated=s.type==='animated',telegram=hmPackInfo(p).telegram,open='openpack';
 return`<article class=hm-card data-id="${esc(p.id)}"><button class="hm-art hm-${HM_TINTS[i%HM_TINTS.length]}" data-act=${open} data-id="${esc(p.id)}" aria-label="Open ${esc(s.name||'sticker')} in ${esc(p.name)}"><span class=hm-type>${animated?ic('film'):''}${animated?'Animated':'Static sticker'}</span>${media(s)}<span class=hm-caption><b>${esc(s.name||s.emoji||'Sticker')}</b><span>${esc(p.name)}</span></span><span class=hm-open>Open pack ↗</span></button>
  ${telegram?`<a class=hm-telegram href="${esc(telegram)}" target=_blank rel="noopener noreferrer" aria-label="Open ${esc(p.name)} in Telegram">${ic('telegram')}</a>`:''}</article>`}
function hmGallery(){const box=$('hm-gal');if(!box)return;const src=LIB.packs,st=hmStickers(src,HM.q.trim().toLowerCase(),HM.filter);
 const width=typeof box.getBoundingClientRect==='function'?box.getBoundingClientRect().width:1440,bands=hmBands(st,hmColumns(width));
 [box,$('hm-gal-more')].forEach((band,i)=>{if(band)band.innerHTML=bands[i].map((item,k)=>hmCard(item,k+i*hmColumns(width)*2)).join('')});
 $('hm-empty').hidden=!!st.length||!src.length;
 document.querySelectorAll('#s-home [data-act=hmfilter]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.f===HM.filter)))}
RENDER.home=async()=>{await loadLib();const ps=LIB.packs,feed=ps;
 const st=ps.flatMap(p=>p.stickers),anim=st.filter(s=>s.type==='animated').length,hero=hmHero(feed),
  canImport=typeof ACT.impackopen==='function';
 $('s-home').innerHTML=`<div class=hm>
 <div class=hm-intro><div><div class=hm-eye>Your creative playground${ps.length?` · ${ps.length} pack${ps.length===1?'':'s'}`:''}</div><h1>A little more expressive.</h1><p>Turn the things you love into stickers worth sending.</p>
  ${ps.length?`<div class=hm-sum><span>${ps.length} pack${ps.length===1?'':'s'}</span><span>${LIB.sticker_count??st.length} sticker${(LIB.sticker_count??st.length)===1?'':'s'}</span>${LIB.reaction_count>0?`<span>${LIB.reaction_count} reaction${LIB.reaction_count===1?'':'s'}</span>`:''}<span>${anim} animated${LIB.reaction_count>0?' items':''}</span></div>`:''}</div>
  <div class=hm-intro-end><label class=hm-search>${ic('search')}<input id=hm-q type=search placeholder="Search your library" aria-label="Search your packs" value="${esc(HM.q)}"></label><button class="btn pri" data-act=hmai>${ic('ai')} Start creating</button></div></div>
 <div class=hm-feat>
  <article class=hm-hero><div class=hm-copy><span class=hm-badge><i></i>Made for your imagination</span><h2>Big feelings.<br><em>Little stickers.</em></h2><p>A character, an inside joke, a whole mood. Make it yours, then bring it to life.</p><button class="btn pri" data-act=hmai>${ps.length?'Make a new pack':'Make your first pack'} ${ic('chev')}</button></div>
   <div class=hm-heroart aria-hidden=true>${hero.map((s,i)=>media(s,'hm-hc hm-hc'+i)).join('')}<span class="hm-bub hm-b1">made by you ♡</span><span class="hm-bub hm-b2">your little universe.</span><span class=hm-spark>✦</span></div></article>
  <div class=hm-side>
   <article class="hm-mini hm-lilac"><span class=hm-badge>Give it a little life</span><h3>From cute to moving.</h3><p>Turn a still sticker into a tiny moment.</p><button class=hm-go data-act=hmfilter data-f=animated data-scroll=1>Explore animation ${ic('chev')}</button>${hero[1]||hero[0]?media(hero[1]||hero[0],'hm-miniart'):''}</article>
   <article class="hm-mini hm-peach"><span class=hm-badge>The finishing touch</span><h3>A sprinkle of magic.</h3><p>Add reactions that make every reaction pop.</p><button class=hm-go data-act=nav data-to=effects>Try reaction effects ${ic('chev')}</button><div class=hm-orbit aria-hidden=true></div></article>
  </div></div>
 <div class=hm-tools>
  <button class=hm-tool data-act=hmai>${hmIc('ai')}<div><h3>Create with AI</h3><p>Start with a little idea</p></div>${ic('chev')}</button>
  <button class=hm-tool data-act=railcreate data-to=generate>${hmIc('gen')}<div><h3>Sticker Studio</h3><p>Make every detail yours</p></div>${ic('chev')}</button>
  ${canImport?`<button class=hm-tool data-act=impackopen>${hmIc('upload')}<div><h3>Bring your own</h3><p>Start from an existing sheet</p></div>${ic('chev')}</button>`:`<button class=hm-tool data-act=nav data-to=create>${hmIc('create')}<div><h3>From a photo</h3><p>Turn a picture into a sticker</p></div>${ic('chev')}</button>`}
  <button class=hm-tool data-act=nav data-to=library>${hmIc('lib')}<div><h3>Your library</h3><p>Every sticker, every pack</p></div>${ic('chev')}</button></div>
 <section class=hm-disc id=hm-disc><div class=hm-head><div><h2>Made by you. Ready to express.</h2><p>Your characters, your reactions, your little universe.</p></div><button class=hm-link data-act=nav data-to=library>View all packs ${ic('chev')}</button></div>
  ${feed.length?`<div class=hm-filters role=group aria-label="Filter stickers"><button class=hm-filter data-act=hmfilter data-f=all>All stickers</button><button class=hm-filter data-act=hmfilter data-f=animated>Animated</button><button class=hm-filter data-act=hmfilter data-f=static>Static stickers</button><button class=hm-filter data-act=hmfilter data-f=particles>With reactions</button></div>
  <p class=hm-empty id=hm-empty role=status hidden>No stickers match. Try a different search or filter.</p>`
  :`<div class=hm-none><h3>No packs yet</h3><p>Describe an idea to the AI, or open the Studio: your first pack lands here.</p><button class="btn pri" data-act=hmai>${ic('ai')} Create with AI</button></div><p id=hm-empty hidden></p>`}
  <div class=hm-showcase><div class=hm-gal id=hm-gal></div><div class=hm-gal id=hm-gal-more></div></div></section>
 <section class=hm-journey><div><div class=hm-eye>An idea is all you need</div><h2>Your imagination.<br>Delivered as a sticker.</h2><p>From the first thought to the final pack, make something that feels like you.</p></div>
  <div class=hm-steps><article class=hm-step><span class=hm-num>01 / IMAGINE</span>${hmIc('ai')}<h3>Tell your idea</h3><p>Describe a character, a feeling, or a moment.</p></article><article class=hm-step><span class=hm-num>02 / MAKE IT YOURS</span>${hmIc('lib')}<h3>Shape the details</h3><p>Review your stickers. Add motion and a little magic.</p></article><article class=hm-step><span class=hm-num>03 / SHARE</span>${hmIc('telegram')}<h3>Send a little joy</h3><p>Export your pack, ready for your next conversation.</p></article></div></section>
 <footer class=hm-foot><span class=hm-brand><img src=/assets/brand/logo.svg alt="">stickerloom<b>.</b></span><span>Small stickers. Endless expression.</span></footer></div>`;
 const i=$('hm-q');i.oninput=e=>{HM.q=e.target.value;hmGallery()};hmGallery();
 if(hmResize)hmResize.disconnect();if(typeof ResizeObserver!=='undefined'){hmResize=new ResizeObserver(()=>{if(route_==='home')hmGallery()});hmResize.observe($('hm-gal'))}};
ACT.hmfilter=el=>{HM.filter=el.dataset.f;hmGallery();
 if(el.dataset.scroll){const d=$('hm-disc');if(d)d.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'})}};
/* a clean AI chat: what the logo did before Home (the next message starts a new conversation) */
ACT.hmai=()=>{try{localStorage.setItem('stickerloom.ai.sid','')}catch(e){}if(typeof A!=='undefined'){A.sid=null;A.sess=null}
 if(route_==='agent'&&typeof ACT.agnew==='function')ACT.agnew();else location.hash='#/agent'};
/* the home button: the logo opens Home */
ACT.home=()=>{if(route_==='home'){$('s-home').scrollTo({top:0,behavior:'smooth'});RENDER.home()}else location.hash='#/home'};
