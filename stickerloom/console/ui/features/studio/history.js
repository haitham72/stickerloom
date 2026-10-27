/* History: the real watch folders (inputs/Images_gen and inputs/videos_gen), image and video side by side, with Remove.
   Remove moves a folder pair to the trash (out/trash) where it can be restored under its own name; the media is not in git. */
'use strict';
const HS={rows:[],trash:[],images:'',videos:''};
const bytes=n=>n>=1048576?(n/1048576).toFixed(1)+' MB':n>=1024?Math.round(n/1024)+' KB':n+' B';
const hAgo=ts=>{const m=Math.round((Date.now()/1000-ts)/60);return m<1?'just now':m<60?m+' min ago':m<1440?Math.round(m/60)+' h ago':new Date(ts*1000).toLocaleDateString()};
async function hLoad(){const r=await api('/api/watch');if(r.ok){Object.assign(HS,r.j);hDraw()}else toast(r.j.error||'Could not read the folders',1)}
RENDER.history=async()=>{$('s-history').innerHTML='<div class=hwrap><div class=sh>'+ic('hist')+' Watch folders</div><div class=mut>Loading the folders…</div></div>';await hLoad()};
function hSide(label,s,kind){
  if(!s)return`<div class=hside none>${label}: <span class=mut>${kind==='vid'?'no video yet':'missing'}</span></div>`;
  return`<div class=hside>${label}: <b>${esc(s.name)}</b> <span class=mut>${s.n_files} ${s.n_files===1?'file':'files'} · ${bytes(s.bytes)}</span></div>`}
function hDraw(){const el=$('s-history');if(!el||route_!=='history')return;
  const gens=r=>r.generations.length?`<div class=hgens>${r.generations.slice(-6).map(g=>`<button class=chip2 data-act=hsopen data-g=${+g.slice(1)}>${g}</button>`).join('')}${r.generations.length>6?`<span class=mut>+${r.generations.length-6} more</span>`:''}</div>`:'';
  el.innerHTML=`<div class=hwrap><div class=sh>${ic('hist')} Watch folders <span class=mut style="font-weight:500">History</span></div>
   <div class=mut>The sheet and video folders the Studio watches (not your conversations or earlier batches): what is in them now. <code>${esc(HS.images)}</code> and <code>${esc(HS.videos)}</code></div>
   <div class=plist style="margin-top:14px">${HS.rows.map(r=>`<div class=hrow>
      <div class=hthumb>${r.thumb?`<img src="${r.thumb}" loading=lazy alt="">`:ic('photo')}</div>
      <div class=hmain><b>${esc(r.subject.replace(/_/g,' '))}</b> <span class=mut>${r.number}</span>
        ${hSide('Images_gen',r.img,'img')}${hSide('videos_gen',r.vid,'vid')}${gens(r)}</div>
      <div class=hact><button class="btn sm pri" data-act=hgen data-s="${esc(r.subject)}" data-n=${r.number} ${r.img?'':'disabled'}>${ic('gen')} Generate</button>
        <button class="btn sm dng" data-act=hremove data-s="${esc(r.subject)}" data-n=${r.number}>${ic('trash')} Remove</button></div></div>`).join('')||'<div class="card" style="text-align:center;padding:36px"><h2>The watch folders are empty</h2><p class=mut>Put a sheet in <code>Images_gen/img-001-subject</code>. Its video goes in <code>videos_gen/vid-001-subject</code>.</p></div>'}</div>
   ${HS.trash.length?`<div class=sh style="margin-top:26px">${ic('trash')} Removed <span class=mut style="font-weight:500">kept until you delete them for good</span></div><div class=plist>${HS.trash.map(t=>`<div class=hrow trash>
      <div class=hmain><b>${esc(t.subject.replace(/_/g,' '))}</b> <span class=mut>${t.number} · ${hAgo(t.removed)} · ${bytes(t.bytes||0)}</span><div class=mut>${t.items.map(i=>esc(i.name)).join(' + ')}</div></div>
      <div class=hact><button class="btn sm" data-act=hrestore data-id="${t.id}">Restore</button><button class="btn sm dng" data-act=hpurge data-id="${t.id}">Delete for good</button></div></div>`).join('')}</div>`:''}</div>`}
ACT.hsopen=el=>{openGen(+el.dataset.g);location.hash='#/studio'};
ACT.hgen=async el=>{const subj=el.dataset.s,num=el.dataset.n;await loadInputs();const inp=GINP.find(x=>x.subject===subj),v=inp&&inp.variants.find(x=>String(x.folder)===String(num));
  const r=await post('/api/generations',{prompt:subj.replace(/_/g,' '),variant:v?v.variant:1,outline:GS.outline});if(!r.ok)return toast(r.j.error,1);openGen(r.j.id);location.hash='#/studio'};
ACT.hremove=el=>{const r=HS.rows.find(x=>x.number===el.dataset.n&&x.subject===el.dataset.s);if(!r)return;
  const names=[r.img&&r.img.name,r.vid&&r.vid.name].filter(Boolean),size=(r.img?r.img.bytes:0)+(r.vid?r.vid.bytes:0);
  confirmDlg(`Remove ${names.join(' and ')}? (${bytes(size)}) They move to the trash below and can be restored.`,async()=>{
    const x=await post('/api/watch/remove',{number:r.number,subject:r.subject});if(!x.ok)return toast(x.j.error,1);toast(`Removed ${names.join(' and ')}`);hLoad()},'Remove')};
ACT.hrestore=async el=>{const x=await post('/api/watch/restore',{id:el.dataset.id});if(!x.ok)return toast(x.j.error,1);toast(`Restored ${x.j.items.map(i=>i.name).join(' and ')}`);hLoad()};
ACT.hpurge=el=>{const t=HS.trash.find(x=>x.id===el.dataset.id);confirmDlg(`Delete ${t?t.items.map(i=>i.name).join(' and '):'this'} for good? This cannot be undone.`,async()=>{
  const x=await post('/api/watch/purge',{id:el.dataset.id});if(!x.ok)return toast(x.j.error,1);toast('Deleted');hLoad()},'Delete for good')};
setInterval(()=>{if(route_==='history')hLoad()},4000);
