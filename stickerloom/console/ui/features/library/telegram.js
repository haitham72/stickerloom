/* Send to Telegram (checkpoint 1H): ONE click from a pack. If the bot is connected the pack is sent at once (what is new only), Telegram opens on the new
   set, and a small card offers Open / Copy link. Nothing to review unless a sticker breaks Telegram's rules (then only those are listed).
   Connecting is a one-time form (bot token + your user id); the token is sent to this PC's server, saved in out/telegram.json, and never shown again. */
'use strict';
let TG={plan:null,done:null};
const tgKind=k=>k==='video'?'animated':'static';
const tgN=(n,k)=>`${n} ${tgKind(k)} sticker${n===1?'':'s'}`;
/* tg:// opens the Telegram app straight on the "Add stickers" screen; the https link (t.me) is the fallback and what gets copied */
const tgApp=name=>`tg://addstickers?set=${encodeURIComponent(name)}`;
function tgOpenApp(name){const a=document.createElement('a');a.href=tgApp(name);document.body.appendChild(a);a.click();a.remove()}
async function tgLoad(){const p=packById(PACK_ID),r=await api(`/api/packs/${PACK_ID}/telegram?name=${encodeURIComponent(p?p.name:'')}`);if(!r.ok){toast(r.j.error||'Could not read the pack',1);return false}TG.plan=r.j;return true}
function tgConnectForm(msg){return`<h3>Connect your Telegram bot (once)</h3>
  <ol class=mut style="margin:6px 0 10px 18px;padding:0"><li>In Telegram, message <b>@BotFather</b>, send <code>/newbot</code>, and copy the token it gives you.</li>
  <li>Open your new bot and press <b>Start</b> (Telegram only lets a bot create packs for people who started it).</li>
  <li>Message <b>@userinfobot</b> to see your numeric user id.</li></ol>
  <div class=fld><label>Bot token</label><input type=text id=tgtok placeholder="123456789:AAH…" autocomplete=off spellcheck=false></div>
  <div class=fld><label>Your Telegram user id</label><input type=text id=tguid placeholder="123456789" inputmode=numeric autocomplete=off></div>
  ${msg?`<div class=warn>${esc(msg)}</div>`:''}
  <div class=mut>Saved on this PC only (<code>out/telegram.json</code>) and sent only to Telegram.</div>
  <div class=row style="justify-content:space-between">${PACK_ID&&route_==='pack'?`<a class=link href="/api/packs/${PACK_ID}/telegram.zip" download>or download for @stickers (zip)</a>`:'<span></span>'}<span><button class=btn data-act=dlgx>Cancel</button> <button class="btn pri" data-act=tgconnect>Connect${PACK_ID&&route_==='pack'?' and send':''}</button></span></div>`}
/* the whole flow after the click */
async function tgFlow(){const P=TG.plan;if(!P)return;const st=P.status||{};
  if(!st.configured){dlg(`<div class=tgdlg><h2>${ic('telegram')} Send “${esc(P.pack)}” to Telegram</h2>${tgConnectForm()}</div>`);return}
  if(!P.sets.length){toast('This pack has no stickers yet',1);return}
  if(P.blocked.length){dlg(`<div class=tgdlg><h2>${ic('telegram')} Not sent</h2><div class=warn style="border-color:var(--bad)"><b>${P.blocked.length} problem${P.blocked.length===1?'':'s'} Telegram would refuse:</b><br>${P.blocked.slice(0,8).map(esc).join('<br>')}${P.blocked.length>8?'<br>…':''}</div>
    <div class=row style="justify-content:space-between"><a class=link href="/api/packs/${PACK_ID}/telegram.zip" download>download for @stickers instead (zip)</a><button class="btn pri" data-act=dlgx>Close</button></div></div>`);return}
  const newN=P.sets.reduce((a,s)=>a+s.new.length,0);
  if(!newN){tgOpenApp(P.sets[0].name);toast('Already on Telegram: opening it');return}
  const rt=await retimeAsk(await packShort(PACK_ID),'telegram');if(rt===null)return;          // clips under 3 s: stretch them to 3 s or keep their length
  dlg(`<div class=tgdlg style="text-align:center;padding:14px 8px"><div class=spin style="margin:0 auto 12px"></div><h2 style="margin:0">Sending ${newN} sticker${newN===1?'':'s'} to Telegram…</h2><div class=mut style="margin-top:6px">${newN>20?'A big pack takes about a minute. ':''}Telegram opens when it is ready.</div></div>`);
  const r=await post(`/api/packs/${PACK_ID}/telegram`,{name:P.pack,retime:rt});
  if(!r.ok){closeDlg();toast(r.j.error,1);return}
  tgWatch(r.j.id,PACK_ID,0)}
/* a send runs on the server (services/telegram.py queue): ask its state until it is done or failed */
async function tgWatch(id,pid,tries=0){let r={ok:false,status:0,j:{}};try{r=await api('/api/telegram/sends/'+encodeURIComponent(id))}catch(e){}
 const here=()=>route_==='pack'&&PACK_ID===pid;
 if(r.ok&&r.j.state==='done'){TG.done=r.j.result;if(here())return tgDone(r.j.result);const n=r.j.result.sets.reduce((a,s)=>a+(s.added||s.total),0);return toast(`Sent ${n} sticker${n===1?'':'s'} to Telegram`)}
 if(r.ok&&r.j.state==='failed'){if(here())closeDlg();return toast(`Send to Telegram failed: ${r.j.error||'unknown error'}`,1)}
 if(r.status===404||tries>720){if(here())closeDlg();return toast('Send status unknown: reopen Send to check',1)}setTimeout(()=>tgWatch(id,pid,tries+1),2500)}
function tgDone(d){const first=d.sets[0];
  dlg(`<div class=tgdlg><h2>${ic('check')} Sent to Telegram</h2>
    ${d.sets.map(s=>`<div class=row style="justify-content:space-between;margin:8px 0"><span><b>${esc(tgN(s.added||s.total,s.kind))}</b> <span class=mut>${s.added?'added':'already there'}</span></span>
      <span><button class="btn sm" data-act=tgcopy data-l="${esc(s.link)}">Copy link</button> <a class="btn pri sm" href="${esc(tgApp(s.name))}">Open in Telegram</a></span></div>`).join('')}
    ${d.notified?`<div class=mut>The bot also wrote you the link in your Telegram chat with @${esc(d.bot)}.</div>`:d.notify_error?`<div class=mut>${esc(d.notify_error)}</div>`:''}
    ${d.warnings.length?`<details class=mut style="margin-top:8px"><summary>${d.warnings.length} optional note${d.warnings.length===1?'':'s'} (they do not stop Telegram)</summary><div style="margin-top:6px">${d.warnings.map(esc).join('<br>')}</div></details>`:''}
    <div class=row style="justify-content:flex-end"><button class="btn" data-act=dlgx>Done</button></div></div>`);
  if(first)tgOpenApp(first.name)}
ACT.tgsend=async()=>{TG={plan:null,done:null};if(await tgLoad())tgFlow()};
ACT.tgconnect=async()=>{const r=await post('/api/telegram/config',{token:$('tgtok').value,user_id:$('tguid').value});if(!r.ok){$('dlg').querySelector('.tgdlg').innerHTML=`<h2>${ic('telegram')} Connect Telegram</h2>`+tgConnectForm(r.j.error);return}
  toast(`Connected to @${r.j.bot}`);if(route_==='settings'){closeDlg();RENDER.settings();return}
  if(PACK_ID&&route_==='pack'&&await tgLoad())tgFlow();else closeDlg()};
ACT.tgdisconnect=async()=>{await post('/api/telegram/disconnect');if(route_==='settings')RENDER.settings()};
ACT.tgcopy=async el=>{try{await navigator.clipboard.writeText(el.dataset.l);toast('Link copied')}catch(e){toast('Copy failed',1)}};
/* Settings card */
async function tgSettingsCard(){const r=await api('/api/telegram');if(!r.ok)return'';const s=r.j;
  return`<div class=card style="margin-top:16px"><h2 style="display:flex;gap:8px;align-items:center">${ic('telegram')} Telegram</h2>
   ${s.configured?`<div class=kv><span>bot</span><span><b>@${esc(s.bot||'?')}</b>${s.from_env?' (from the environment)':''}</span><span>your user id</span><span>${esc(s.user_id)}</span><span>service</span><span>${esc(s.api)}</span></div>
     <div class=row>${s.from_env?'':`<button class="btn sm dng" data-act=tgdisconnect>Disconnect</button>`}<span class=mut>Send a pack with one click: Library → a pack → Send to Telegram.</span></div>`
   :`<div class=mut>Not connected. Stickers can be sent to Telegram in one click once you connect a bot.</div><div class=row><button class="btn pri sm" data-act=tgsettings>Connect Telegram</button></div>`}</div>`}
ACT.tgsettings=()=>dlg(`<div class=tgdlg><h2>${ic('telegram')} Connect Telegram</h2>${tgConnectForm()}</div>`);
if(typeof module!=='undefined')module.exports={tgWatch};
