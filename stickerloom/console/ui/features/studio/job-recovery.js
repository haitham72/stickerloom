/* Shared by the queue, Studio job panels and chat cards. Cheapest action first. */
const JR=(()=>{
  const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const stalled=j=>['FAILED','TIMEOUT'].includes(j.status);
  const message=j=>j.provider_check?.message||(j.status==='TIMEOUT'?'Waiting stalled; the provider may still finish this ticket.':j.error||'This job stalled.');
  const controls=j=>`<div class=job-recovery><span>${escape(message(j))}</span><div>${[
    ['jrrefresh','Refresh','Local state + SSE; no provider call',false],
    ['jrcheck','Check · free','One read-only provider check; download a completed result without paying again',!j.external_task_id],
    ['jrcontinue','Continue · same ticket','Wait again on the same ticket; cannot double-charge',!j.external_task_id],
    ['jrretry','Retry · SPENDS','A new paid request; its price is shown before you confirm',false]
  ].map(([act,label,title,disabled])=>`<button class="btn sm" data-act=${act} data-id="${escape(j.id)}" title="${title}" ${disabled?'disabled':''}>${label}</button>`).join('')}</div></div>`;
  return{stalled,message,controls};
})();
if(typeof module!=='undefined')module.exports=JR;
if(typeof document!=='undefined'){
  const recoveryStreams=new Map();
  const localRefresh=async id=>{
    await qRefresh();
    const r=await api('/api/jobs/'+id);if(!r.ok)return;
    const old=LIVE.jobs.find(j=>j.id===id);if(old){delete old.error;old.status=r.j.status;lsave()}
    const gid=r.j.generation&&+String(r.j.generation).replace(/\D/g,'');
    if(gid){const g=await api('/api/generations/'+gid);if(g.ok){GM.set(gid,g.j);glast='';const root=document.getElementById('gres');if(root)root.innerHTML=gview()}
      recoveryStreams.get(id)?.close();
      const stream=new EventSource('/api/generations/'+gid+'/events');recoveryStreams.set(id,stream);
      stream.onmessage=()=>qRefresh();
      for(const name of ['sticker_ready','animation_ready','generation_failed','pack_complete'])stream.addEventListener(name,()=>qRefresh());
    }
    if(typeof loadSession==='function'&&typeof A!=='undefined'&&A.sid)await loadSession(A.sid,true);
  };
  ACT.jrrefresh=el=>localRefresh(el.dataset.id);
  const action=async(el,verb)=>{el.disabled=true;try{const r=await post(`/api/jobs/${el.dataset.id}/${verb}`);if(!r.ok)toast(r.j.error||'Recovery did not finish',1);await localRefresh(el.dataset.id)}finally{el.disabled=false}};
  ACT.jrcheck=el=>action(el,'check');
  ACT.jrcontinue=el=>action(el,'continue');
  ACT.jrretry=async el=>{const id=el.dataset.id,r=await post(`/api/jobs/${id}/retry_estimate`);if(!r.ok)return toast(r.j.error||'Could not price Retry',1);
    if(!confirm(`Retry ${id} starts a NEW paid request and SPENDS ${r.j.credits} credits. The original ticket may also be charged. Continue?`))return;
    const paid=await post(`/api/jobs/${id}/retry`,{go:true,estimate:r.j.credits});if(!paid.ok)return toast(paid.j.error||'Retry did not start',1);
    LIVE.dis=LIVE.dis.filter(x=>x!==id);await qRefresh();toast('New paid request '+paid.j.id);
  };
}
