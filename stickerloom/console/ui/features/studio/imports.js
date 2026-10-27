/* Free sheet/video uploads and Higgsfield history. Processing stays in flow/imports.py. */
'use strict';
const IMPV=(()=>{
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const rows=jobs=>jobs.map(j=>`<div class=row><span><b>${esc(j.kind)}</b> · ${esc(j.model||'')}<br><small>${esc(j.prompt||j.id)}</small></span><button class=btn data-act=imphf data-id="${esc(j.id)}" ${j.known||j.status==='completed'?'':'disabled'}>${j.known?'Open existing':'Import'}</button></div>`).join('')||'<p class=mut>No jobs found.</p>';
 return {rows};
})();
if(typeof globalThis!=='undefined')globalThis.IMPV=IMPV;
if(typeof document!=='undefined'&&typeof ACT!=='undefined'){
 let IMP={file:null,job:null,options:null,busy:false};
 const owner=()=>true;
 globalThis.impButtons=()=>owner()?'<button class="btn sm" data-act=impopen>Use my own sheet</button><button class="btn sm" data-act=imphistory>Import from Higgsfield</button>':'';
 const options=()=>({prompt:($('imp-prompt')||{}).value||'',generation:($('imp-generation')||{}).value||undefined,sheet:($('imp-sheet')||{}).value||undefined,grid:($('imp-grid')||{}).value||undefined});
 const gridField=(id='imp-grid')=>`<div class=fld><label for=${id}>Sheet grid</label><select id=${id}><option value="">Auto detect</option><option value=2x2>2 × 2</option><option value=3x3>3 × 3</option></select><small class=mut>Detect from the image or video's first frame, or choose the grid manually.</small></div>`;
 ACT.newphoto=()=>dlg('<h2>From a photo</h2><p>What would you like to create?</p><div class=row><button class="btn pri" data-act=newphotosticker>Sticker</button><button class=btn data-act=impemojis>Emojis · image sheet</button><button class=btn data-act=dlgx>Cancel</button></div>');
 ACT.newphotosticker=()=>{closeDlg();location.hash='#/create'};
 const newSheet=video=>{IMP={file:null,job:null,options:null,busy:false,localJob:null,asNew:true};dlg(`<h2>Import ${video?'video':'image'} sheet</h2><p class=mut>The sheet opens in Studio for review after cutting.</p><input id=imp-file type=file accept="${video?'.mp4,.mov,.webm':'.png,.jpg,.jpeg,.webp'}"><div class=fld><label for=imp-prompt>Description (optional)</label><input id=imp-prompt maxlength=2000></div>${gridField()}<div class=row><button class="btn pri" data-act=impupload>Import into Studio</button><button class=btn data-act=dlgx>Cancel</button></div>`)};
 ACT.impemojis=()=>newSheet(false);
 ACT.newvideo=()=>newSheet(true);
 const form=()=>`${gridField()}<div class=fld><label>Description (optional)</label><input id=imp-prompt maxlength=2000 value="${esc(($('prompt')||{}).value||'')}"></div><div class=fld><label>Destination batch for a video</label><input id=imp-generation placeholder="G104" value="${SES.gens.length?'G'+String(SES.gens[0]).padStart(3,'0'):''}"></div><div class=fld><label>Video sheet (optional)</label><input id=imp-sheet placeholder="First approved sheet"></div>`;
  async function result(r){if(!r.ok){if(r.j&&r.j.candidates&&r.j.candidates.length>1){chooseDlg(r.j.candidates);return}toast(r.j.error||'Import failed',1);return}const j=r.j;
  if(j.duplicate&&j.recoverable&&j.import){dlg(`<h2>This import needs recovery</h2><p>Retry processing the saved batch. No provider job is created.</p><button class=btn data-act=impretry>Retry import</button><button class=btn data-act=dlgx>Close</button>`);return}
  if(j.duplicate&&!IMP.again&&IMP.file){dlg('<h2>This file is already imported</h2><p>Open the existing batch, or import the same file as a new batch. No provider job is created.</p><div class=row><button class="btn pri" data-act=impexisting>Open existing batch</button><button class=btn data-act=impreimport>Import again as a new batch</button><button class=btn data-act=dlgx>Cancel</button></div>');IMP.existing=r;return}
  closeDlg();const gid=j.id||+(String(j.generation||'').replace(/^G/i,''));
  if(gid){SES={prompt:IMP.options&&IMP.options.prompt||'',gens:[+gid],off:[],pack:''};saveSes();glast='';GS.tab=j.kind==='video'?'anim':'stickers';location.hash='#/studio';if(route_==='generate')await tick(true);toast(j.recovered?`Recovered ${j.job||''}: linked to the failed job, no second charge`:(j.duplicate?'Opened the existing batch':'Imported; processing the file'));return}
  if(j.effect){location.hash='#/effects/'+j.effect;return}
  toast(j.job?`Already in Queue: ${j.job}${j.recoverable?' (still processing or needs recovery)':''}`:j.task?`Already in the Inbox: task ${j.task}`:'This file is already imported')}
  async function send(retry=false){if(IMP.busy)return;IMP.busy=true;try{const o={...IMP.options,retry,...(IMP.again?{again:1,as_new:1}:{})};let r;
  if(IMP.localJob){if(IMP.job)o.local_job=IMP.localJob}
  if(IMP.job)r=await post('/api/higgsfield/import',{id:IMP.job,...o});
  else{const q=new URLSearchParams();Object.entries({...o,name:IMP.file.name}).forEach(([k,v])=>{if(v!==undefined&&v!=='')q.set(k,String(v))});if(IMP.localJob)q.set('job',IMP.localJob);if(IMP.asNew)q.set('as_new','1');r=await api('/api/import?'+q,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:IMP.file})}
  await result(r)}finally{IMP.busy=false}}
  const TICKET_RX=/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
  const chooseDlg=(cs,preview)=>dlg(`<h2>Is this the result of …?</h2><p class=mut>This file names a provider job that matches ${cs.length} of your failed jobs. Pick the one it completes (no second charge), or import it as a new batch.</p>${cs.map(c=>`<button class=row data-act=impchoose data-j="${esc(c.job)}" style="width:100%;text-align:left;gap:10px">${c.thumb?`<img src="${esc(c.thumb)}" style="width:72px;height:72px;object-fit:cover" alt="">`:preview?`<img src="${esc(preview)}" style="width:72px;height:72px;object-fit:cover" alt="">`:''}<span><b>${esc(c.job)} · ${esc(c.kind||'')}</b><br><small>${esc(c.prompt||'no description')}${c.at?' · '+new Date(c.at*1000).toLocaleString():''}${c.generation?' · '+esc(c.generation):''}</small></span></button>`).join('')}<div class=row><button class=btn data-act=impnew>Import as a new batch</button><button class=btn data-act=dlgx>Cancel</button></div>`);
  ACT.impopen=()=>{IMP={file:null,job:null,options:null,busy:false,localJob:null,asNew:false};dlg(`<h2>Import a sheet or video</h2><p class=mut>A sheet becomes a Studio batch. A video goes to the destination batch's approved video sheet; with no destination it becomes a batch of its own (its first frame is the sheet). This is free.</p><input id=imp-file type=file accept=".png,.jpg,.jpeg,.webp,.mp4,.mov,.webm">${form()}<div class=row><button class="btn pri" data-act=impupload>Import</button><button class=btn data-act=dlgx>Cancel</button></div>`)};
  // Library > + > Import pack: a sheet or a video on its own, no destination; it is cut (a video also animated) and waits in the Studio for review
  ACT.impackopen=()=>{IMP={file:null,job:null,options:null,busy:false,localJob:null,asNew:false};dlg(`<h2>Import a pack</h2><p class=mut>A sticker sheet (PNG, JPEG, WebP) is cut into stickers. An animated sheet (MP4, MOV, WebM) is cut too: its first frame gives the stills and the video animates them. Both wait in the Studio for your review. This is free.</p><input id=imp-file type=file accept=".png,.jpg,.jpeg,.webp,.mp4,.mov,.webm"><div class=fld><label>What is it? (optional)</label><input id=imp-prompt maxlength=2000 placeholder="generic emojis"></div>${gridField()}<div class=row><button class="btn pri" data-act=impupload>Import</button><button class=btn data-act=dlgx>Cancel</button></div>`)};
  ACT.impupload=async()=>{const f=$('imp-file').files[0];if(!f)return toast('Choose a file first',1);IMP.file=f;IMP.options=options();IMP.localJob=null;
    const m=(f.name||'').match(TICKET_RX);
    if(m&&!IMP.asNew&&!IMP.options.generation&&!IMP.options.sheet){const r=await api('/api/imports/candidates?ticket='+encodeURIComponent(m[0].toLowerCase()));const cs=r.ok?(r.j.candidates||[]):[];
      if(cs.length>1){chooseDlg(cs);return}}
    send()};
  ACT.impchoose=el=>{IMP.localJob=el.dataset.j;send()};
  ACT.impexisting=()=>{IMP.again=true;return result(IMP.existing)};
  ACT.impreimport=()=>{IMP.again=true;IMP.asNew=true;IMP.localJob=null;IMP.options={...IMP.options,generation:undefined,sheet:undefined};return send()};
  ACT.impnew=()=>{IMP.asNew=true;IMP.localJob=null;send()};
 ACT.impretry=()=>send(true);
   ACT.imphistory=async()=>{IMP={file:null,job:null,options:null,busy:false,localJob:null,asNew:false};dlg('<h2>Higgsfield history</h2><p>Loading completed jobs…</p>');const r=await api('/api/higgsfield/history');if(!r.ok)return dlg(`<h2>Higgsfield history</h2><p>${esc(r.j.error||'Could not load history')}</p><button class=btn data-act=dlgx>Close</button>`);dlg(`<h2>Higgsfield history</h2><p class=mut>Importing downloads an existing result. No credits are spent.</p>${form()}${IMPV.rows(r.j.jobs)}<button class=btn data-act=dlgx>Close</button>`)};
 ACT.imphf=el=>{IMP.job=el.dataset.id;IMP.options=options();send()};
 /* Import INSIDE a batch (flow/imports._into_batch): on Stickers a picture becomes the batch's next generation (its own cells, tags and naming), on Animation
    a video becomes its next animation; the server reads which sheet the video was animated from (image sheet or video sheet) and says which one it used */
 globalThis.impBatchBtn=(g,mode)=>owner()&&!making(g)&&g.kind!=='particles'?`<button class="btn sm" data-act=impbatch data-g=${g.number} data-m=${mode==='anim'?'anim':'still'} title="${mode==='anim'?'Import a video made from this batch: it becomes the next animation':'Import a picture: it becomes the next generation of this batch, cut with its own cells and tags'}">${ic('plus')} Import ${mode==='anim'?'video':'picture'}</button>`:'';
 /* a video of the image sheet whose first frame shows another grid than the batch's: the server asks (409, hint.detected_grid) and the person may cut it
    with the batch's grid anyway (grid_ok=1, rule 10); the file waits in BIMP until they answer */
 let BIMP=null;
 const anim0=f=>/\.(mp4|mov|webm)$/i.test(f.name||'');
 /* ok: cut with the batch's grid anyway; again: the same bytes once more (into the batch, or with asNew as a batch of its own) */
 const batchImport=async(n,file,ok,again,asNew,grid)=>{const g=GM.get(n);if(!g)return;toast('Importing…');
  const q=`name=${encodeURIComponent(file.name)}${asNew?'&as_new=1':`&batch=${encodeURIComponent(g.generation_id)}`}${ok?'&grid_ok=1':''}${again?'&again=1':''}${grid?'&grid='+encodeURIComponent(grid):''}`;
  const r=await api(`/api/import?${q}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});
  if((asNew||!r.j.batch)&&r.ok&&!r.j.duplicate){closeDlg();const gid=r.j.id||+(String(r.j.generation||'').replace(/^G/i,''));if(gid){SES.gens=[...SES.gens,+gid];saveSes()}GS.tab=r.j.kind==='video'?'anim':'stickers';toast(r.j.message||'Imported as a new batch');glast='';return tick(true)}
  if(!r.ok&&r.j.detected_grid){const[dr,dc]=r.j.detected_grid,[br,bc]=r.j.batch_grid;BIMP={n,file,again,grid};
   return dlg(`<h2>Another grid?</h2><p>This video's first frame looks like a ${dr}×${dc} grid, but ${esc(g.generation_id)} is ${br}×${bc} (${r.j.cells} stickers).</p><div class=row style="justify-content:flex-end;flex-wrap:wrap"><button class=btn data-act=dlgx>Cancel</button>${dr===dc&&(dr===2||dr===3)?`<button class=btn data-act=impdetected data-grid=${dr}x${dc}>New ${dr}×${dc} batch</button>`:''}<button class="btn pri" data-act=impgrid>Cut it as ${br}×${bc} anyway</button></div>`)}
  if(!r.ok)return toast(r.j.error||'Import failed',1);
  if(r.j.duplicate){BIMP={n,file,grid};const was=r.j.generation?esc(r.j.generation):'this batch';
   return dlg(`<h2>Already imported</h2><p>This file is already in stickerloom as ${was}. Import it again? Nothing is removed and nothing is spent.</p><div class=row style="justify-content:flex-end;flex-wrap:wrap"><button class=btn data-act=dlgx>Cancel</button><button class=btn data-act=impagain data-new=1 title="A batch of its own, cut and waiting in the Studio">As a new batch</button><button class="btn pri" data-act=impagain title="${anim0(file)?'Its next animation':'Its next generation'}">Again into ${esc(g.generation_id)}</button></div>`)}
  if(r.j.kind==='sheet'&&r.j.id){FAM.clear();SES.gens=SES.gens.map(x=>x===n?r.j.id:x);saveSes()}
  GS.tab=r.j.kind==='video'?'anim':'stickers';toast(r.j.message||'Imported');glast='';tick(true)};
 ACT.impbatch=el=>{const n=+el.dataset.g,anim=el.dataset.m==='anim',f=document.createElement('input');f.type='file';f.hidden=true;f.accept=anim?'.mp4,.mov,.webm':'.png,.jpg,.jpeg,.webp';
  f.onchange=()=>{const file=f.files[0];f.remove();if(file){BIMP={n,file};dlg(`<h2>Import ${anim?'video':'image'} sheet</h2><p>${esc(file.name)}</p>${gridField('bimp-grid')}<p class=mut>A different grid starts a new batch so the existing stickers keep their cells.</p><div class=row><button class="btn pri" data-act=impbatchgo>Import into Studio</button><button class=btn data-act=dlgx>Cancel</button></div>`)}};
  document.body.appendChild(f);f.click()};
 ACT.impbatchgo=()=>{const b=BIMP,grid=($('bimp-grid')||{}).value||undefined;BIMP=null;closeDlg();if(b)batchImport(b.n,b.file,false,false,false,grid)};
 ACT.impdetected=el=>{const b=BIMP;BIMP=null;closeDlg();if(b)batchImport(b.n,b.file,false,b.again,true,el.dataset.grid)};
 ACT.impgrid=()=>{const b=BIMP;BIMP=null;closeDlg();if(b)batchImport(b.n,b.file,true,b.again,false,b.grid)};
 ACT.impagain=el=>{const b=BIMP;BIMP=null;closeDlg();if(b)batchImport(b.n,b.file,false,true,!!el.dataset.new,b.grid)};
}
