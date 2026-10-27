/* The Trash panel (Settings > Trash): what Remove batch / Delete pack left behind, exactly what a purge would remove, Restore, and the one real delete (flow/purge.py).
   A card appended to the Settings screen; no new screen. Top-level names start with TRX / trx (the console scripts share one global scope). The pure builders are exported for node. */
const TRX=(()=>{
  const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const size=b=>b>=1048576?(b/1048576).toFixed(1)+' MB':b>=1024?Math.round(b/1024)+' KB':(b||0)+' B';
  const when=t=>t?new Date(t*1000).toLocaleString():'';
  const plural=(n,w)=>`${n} ${n===1?w:(/y$/.test(w)?w.slice(0,-1)+'ies':w+'s')}`;
  /* the sentence under an item: what a purge removes, in plain words */
  const detail=i=>i.type==='batch'
    ?`${plural(i.stickers,'sticker')}, ${plural(i.files_total,'file')}, ${size(i.bytes)}.`+(i.db&&i.db.available?` Database: ${plural(i.db.indexed||0,'search entry')} (${i.db.vectors||0} with vectors) go; the review history stays.`:' The database is not reachable or not written to, so only files go.')+((i.copies_in_packs||[]).length?` Copies in ${i.copies_in_packs.map(c=>'“'+c.name+'”').join(', ')} stay.`:'')
    :`${plural(i.stickers,'sticker')}, ${plural(i.files.filter(f=>!f.missing&&!f.shared).length,'file')}, ${size(i.bytes)}.`+((i.particle_sets||[]).length?` Particle sets ${i.particle_sets.map(n=>'“'+n+'”').join(', ')} stay in the Library.`:'');
  /* the subject as the Earlier-batches column writes it: underscores to spaces, each word capitalised */
  const nice=v=>String(v||'').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
  const row=i=>{const name=i.type==='batch'?`${i.id}${i.subject?' · '+nice(i.subject):''}`:`Pack “${i.name}”`,at=i.type==='batch'?i.removed:i.deleted;
    return `<div class=trx-row data-type=${i.type} data-id="${escape(i.id)}" style="display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap;padding:10px 0;border-top:1px solid var(--bd)"><div class=trx-main style="flex:1 1 280px;min-width:0"><b>${escape(name)}</b> <small class=mut>${escape(i.type==='batch'?'batch':'pack')} · removed ${escape(when(at))}${i.by?' by '+escape(i.by):''}</small>
      <div class=mut>${escape(detail(i))}</div>${i.blocked?`<div class=mut style="color:var(--bad)">${escape(i.blocked)}</div>`:''}${i.needs_confirm?`<div class=mut>${escape(i.confirm_words)}</div>`:''}</div>
      <div class=row><button class="btn sm" data-act=trrestore data-type=${i.type} data-id="${escape(i.id)}">Restore</button><button class="btn sm dng" data-act=trpurge data-type=${i.type} data-id="${escape(i.id)}" ${i.blocked?'disabled':''}>Delete for good</button></div></div>`};
  const html=d=>{
    if(!d)return '<div class=mut>Loading the trash…</div>';
    const items=[...(d.batches||[]),...(d.packs||[])],t=d.totals||{},pa=d.purge_all||{count:0};
    if(!items.length)return '<div class=mut>The trash is empty.</div>';
    const run=d.purge&&d.purge.status==='running'?`<div class=mut>A purge is running (${d.purge.done} of ${d.purge.total}).</div>`:d.purge&&d.purge.status==='interrupted'?`<div class=mut style="color:var(--bad)">${escape(d.purge.error)}</div>`:'';
    return `<div class=mut>${plural(t.items,'item')} in the trash: ${plural(t.batches,'batch')}, ${plural(t.packs,'pack')}, ${plural(t.files,'file')}, ${size(t.bytes)}. Restore brings an item back untouched; “Delete for good” cannot be undone.</div>${run}
      <div class=trx-list>${items.map(row).join('')}</div>
      <div class=row style="justify-content:flex-end"><button class="btn dng" data-act=trpurgeall ${pa.count?'':'disabled'}>Delete all (${pa.count})</button></div>`+
      (pa.skipped&&pa.skipped.length?`<div class=mut>${plural(pa.skipped.length,'item')} will not be part of “delete all” until you decide on it by itself: ${escape(pa.skipped.map(s=>s.id).join(', '))}.</div>`:'');
  };
  /* the plain confirmation of one item; a shared one repeats the sentence that names the packs */
  const confirmOne=i=>i.type==='batch'
    ?`Delete ${i.id} for good? ${detail(i)} This cannot be undone.`+(i.needs_confirm?' '+i.confirm_words:'')
    :`Delete the pack “${i.name}” for good? ${detail(i)} This cannot be undone.`+(i.needs_confirm?' '+i.confirm_words:'');
  /* the typed confirmation of delete all: it names the count and the server compares it with the trash as it is now */
  const allText=pa=>`This deletes ${plural(pa.count,'item')} for good. Type “${pa.phrase}” to confirm.`;
  const typedOk=(typed,pa)=>String(typed||'').trim().toLowerCase().replace(/\s+/g,' ')===pa.phrase;
  /* a purge task in one sentence */
  const outcome=t=>{if(!t)return '';if(t.nothing_to_do)return 'Nothing to delete.';if(t.timedout)return 'Still deleting after 3 minutes: Refresh in a bit to see the rest.';
    const ok=(t.results||[]).filter(r=>r.ok).length,bad=(t.results||[]).filter(r=>!r.ok);
    return `${t.status==='done'?'Deleted':'Deleted so far'}: ${ok} of ${t.total}.`+(bad.length?' Could not delete '+bad.map(r=>r.id+' ('+r.error+')').join(', ')+'; they are still in the trash, run it again.':'')+((t.refused||[]).length?` Left alone, they need their own confirmation: ${t.refused.map(r=>r.id).join(', ')}.`:'')};
  return{escape,size,nice,detail,row,html,confirmOne,allText,typedOk,outcome};
})();
if(typeof module!=='undefined')module.exports=TRX;
if(typeof document!=='undefined'){
  let trxData=null,trxBusy=false;
  const trxBox=()=>document.getElementById('trx-box');
  const trxDraw=()=>{const b=trxBox();if(b)b.innerHTML=TRX.html(trxData)};
  async function trxLoad(){const r=await api('/api/trash');if(!r.ok){const b=trxBox();if(b)b.innerHTML=`<div class=mut>${TRX.escape(r.status===403?'The trash is for the owner account.':(r.j&&r.j.error)||'Could not read the trash.')}</div>`;return}trxData=r.j;trxDraw()}
  /* a purge answers 200 when it finished within the request, 202 while it runs: then poll its task until it is done */
  async function trxWait(t){let n=0;while(t&&t.status==='running'&&t.id&&n<150){n++;await new Promise(r=>setTimeout(r,1200));const r=await api('/api/trash/purges/'+t.id);if(!r.ok)break;t=r.j}if(t&&t.status==='running'&&n>=150)t.timedout=true;return t}
  async function trxRun(url,body,shared){if(trxBusy)return;trxBusy=true;
    try{const r=await post(url,body);if(!r.ok){if(r.status===409&&!shared&&body.type&&/Confirm to go on/.test(r.j.error||'')){trxAskShared(body,r.j.error);return}toast(r.j.error||'The purge did not start',1);return}
      const t=await trxWait(r.j);toast(TRX.outcome(t),t&&t.status==='failed')}finally{trxBusy=false;await trxLoad()}}
  function trxAskShared(body,words){confirmDlg(words,()=>trxRun('/api/trash/purge',{...body,confirm_shared:true},true),'Delete anyway')}
  const trxItem=el=>{const all=[...((trxData||{}).batches||[]),...((trxData||{}).packs||[])];return all.find(i=>i.type===el.dataset.type&&i.id===el.dataset.id)};
  ACT.trrestore=async el=>{const t=el.dataset.type,id=el.dataset.id,r=await post(t==='batch'?`/api/generations/${+String(id).replace(/\D/g,'')}/restore`:`/api/packs/${id}/restore`,{});
    if(!r.ok)return toast(r.j.error||'Could not restore',1);toast((t==='batch'?id:'The pack')+' is back');if(typeof loadLib==='function')loadLib();await trxLoad()};
  ACT.trpurge=el=>{const i=trxItem(el);if(!i)return;confirmDlg(TRX.confirmOne(i),()=>trxRun('/api/trash/purge',{type:i.type,id:i.id,confirm_shared:false},false),'Delete for good')};
  ACT.trpurgeall=()=>{const pa=(trxData||{}).purge_all;if(!pa||!pa.count)return;
    dlg(`<h2>Delete everything in the trash?</h2><p>${TRX.escape(TRX.allText(pa))}</p><input type=text id=trx-typed autocomplete=off placeholder="${TRX.escape(pa.phrase)}"><div class=row style="justify-content:flex-end"><button class=btn data-act=dlgx>Cancel</button><button class="btn pri" data-act=trpurgeallgo>Delete all</button></div>`);
    const i=document.getElementById('trx-typed');if(i)i.focus()};
  ACT.trpurgeallgo=()=>{const pa=(trxData||{}).purge_all,i=document.getElementById('trx-typed'),typed=i?i.value:'';
    if(!pa||!TRX.typedOk(typed,pa))return toast(`Type exactly “${pa?pa.phrase:''}” to go on.`,1);closeDlg();trxRun('/api/trash/purge_all',{confirm:typed},false)};
  ACT.trrefresh=()=>trxLoad();
  /* appended to Settings after the screen has drawn itself (the way live.js wraps drawRail) */
  const trxOrig=RENDER.settings;
  RENDER.settings=async(...a)=>{const r=await trxOrig(...a);const page=document.querySelector('#s-settings .page');
    if(page&&!document.getElementById('trx-box')){const c=document.createElement('div');c.className='card';c.style.marginTop='16px';
      c.innerHTML='<div class=row style="justify-content:space-between"><h2 style="margin:0">Trash</h2><button class="btn sm" data-act=trrefresh>Refresh</button></div><div id=trx-box></div>';page.appendChild(c);trxData=null;trxDraw();trxLoad();if(typeof setNavSync==='function')setNavSync();}
    return r};
}
