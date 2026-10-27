const {readConsoleFile,statement:consoleStatement}=require('./console_source');
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=readConsoleFile(path.join(__dirname,'..','..','stickerloom','console','chat.js'),'utf8');

function harness(){
  const calls=[],replies=[],timers=[];
  const context=vm.createContext({Date,JSON,Math,encodeURIComponent,
    localStorage:{getItem:()=>null,setItem(){}},
    setTimeout:(fn,delay)=>{timers.push({fn,delay});return timers.length},
    api:async url=>{calls.push(url);return replies.shift()||{ok:true,j:{set:null,url:null}}},
    esc:s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),
    media:s=>`<video data-sticker="${s.id}"></video>`,ic:()=>'',
    $:()=>null,confirmDlg:(_text,fn)=>fn(),drawCol2(){},loadLib:async()=>{}});
  vm.runInContext("const ACT={},RENDER={};let route_='chat',LIB={packs:[]};const packById=id=>LIB.packs.find(p=>p.id===id);",context);
  vm.runInContext(source,context);
  vm.runInContext('chDraw=()=>{}',context);
  const run=code=>vm.runInContext(code,context);
  return {run,calls,replies,timers,reply:()=>timers.find(t=>t.delay===1500).fn()};
}
const setReply=(set,url='/out/particles/P001/previews/preview.webp')=>({ok:true,j:{set,url,motion:{preset:'vortex'},params:{vortex:2}}});

test('pending/carousel and tray sends preserve library sticker and parent IDs',async()=>{
  const h=harness();
  h.run("CH.pending={id:'s1',pack_id:'p1',type:'animated',file:'one.webm',name:'One'}");
  await h.run('RENDER.chat()');
  assert.equal(h.run('CH.msgs[0].s.id'),'s1');
  assert.equal(h.run('CH.msgs[0].s.pack_id'),'p1');
  h.run("LIB.packs=[{id:'p2',stickers:[{id:'s2',type:'static',file:'two.png',name:'Two'}]}];CH.pk='p2';ACT.chpick({dataset:{id:'s2'}})");
  assert.equal(h.run('CH.msgs[1].s.id'),'s2');
  assert.equal(h.run('CH.msgs[1].s.pack_id'),'p2');
});

test('Echo looks up current assignment and overlays the outgoing replied-to sticker',async()=>{
  const h=harness();h.replies.push(setReply('P001'));
  h.run("chSend({kind:'sticker',s:{id:'s1',pack_id:'p1',type:'static',file:'one.png'}})");
  h.reply();await new Promise(setImmediate);
  assert.deepEqual(h.calls,['/api/packs/p1/stickers/s1/particle-preview']);
  assert.equal(h.run('CH.msgs[1].reply_to'),h.run('CH.msgs[0].id'));
  assert.match(h.run('chMsgH(CH.msgs[0])'),/ch-particle.*data-particle-set="P001"/s);
  assert.match(h.run('chMsgH(CH.msgs[0])'),/data-act=chreplay/);
  assert.doesNotMatch(h.run('chMsgH(CH.msgs[1])'),/ch-particle/);
  assert.equal(h.run('CH.msgs[0].s.particle_set'),undefined,'message does not store stale assignment');
});

test('replay fetches updated assignment/settings and never uses unrelated session state',async()=>{
  const h=harness();h.run("CH.msgs=[{id:'m1',from:'out',kind:'sticker',s:{id:'s1',pack_id:'p1'}}]");
  h.replies.push(setReply('P001'));await h.run("chPlay('m1')");
  h.replies.push(setReply('P002','/out/particles/P002/previews/saved-settings.webp'));await h.run("ACT.chreplay({dataset:{id:'m1'}})");
  assert.equal(h.calls.length,2);
  assert.match(h.run('chParticleH(CH.msgs[0])'),/saved-settings\.webp.*data-particle-set="P002"/s);
  h.replies.push({ok:true,j:{set:null,url:null}});await h.run("chPlay('m1')");
  assert.equal(h.run('chParticleH(CH.msgs[0])'),'');
});

test('clear cancels pending Echo timers and late particle-preview responses',async()=>{
  const h=harness();
  h.run("chSend({kind:'sticker',s:{id:'s1',pack_id:'p1'}})");
  h.run('ACT.chclear()');h.timers.filter(t=>t.delay<=1500).forEach(t=>t.fn());
  assert.equal(h.run('CH.msgs.length'),0);assert.equal(h.calls.length,0);
  h.run("CH.msgs=[{id:'m1',from:'out',kind:'sticker',s:{id:'s1',pack_id:'p1'}}]");
  let resolve;h.replies.push(new Promise(r=>resolve=r));const pending=h.run("chPlay('m1')");
  h.run('chClear()');resolve(setReply('P001'));await pending;
  assert.equal(h.run('Object.keys(CH.play).length'),0);
});

test('late responses cannot overwrite newer replay or another route',async()=>{
  const h=harness();h.run("CH.msgs=[{id:'m1',from:'out',kind:'sticker',s:{id:'s1',pack_id:'p1'}}]");
  let resolve;h.replies.push(new Promise(r=>resolve=r));const old=h.run("chPlay('m1')");
  h.replies.push(setReply('P002'));await h.run("chPlay('m1')");
  resolve(setReply('P001'));await old;assert.equal(h.run('CH.play.m1.set'),'P002');
  let finish;h.replies.push(new Promise(r=>finish=r));const away=h.run("chPlay('m1')");
  h.run("route_='library'");finish(setReply('P003'));await away;
  assert.equal(h.run('CH.play.m1'),undefined);
});

test('old messages without identity and text reactions never ask for a particle set',async()=>{
  const h=harness();h.run("CH.msgs=[{id:'old',from:'out',kind:'sticker',s:{file:'old.png'}},{id:'txt',from:'out',kind:'text',text:'Hi'}]");
  await h.run("chPlay('old')");await h.run("chPlay('txt')");
  assert.equal(h.calls.length,0);
  assert.doesNotMatch(h.run('chMsgH({...CH.msgs[0],react:"❤️"})'),/chreplay|ch-particle/);
});

test("Echo's own sticker can be liked, and the like plays its burst",async()=>{
  const h=harness();h.run("CH.msgs=[{id:'m1e',from:'in',kind:'sticker',s:{id:'s1',pack_id:'p1'}},{id:'t1',from:'in',kind:'text',text:'Hi'}]");
  assert.match(h.run('chMsgH(CH.msgs[0])'),/data-act=chlike data-id="m1e"/);
  assert.doesNotMatch(h.run('chMsgH(CH.msgs[1])'),/chlike/,'text has no like');
  h.replies.push(setReply('P001'));await h.run("ACT.chlike({dataset:{id:'m1e'}})");
  assert.equal(h.run('CH.msgs[0].react'),'❤️');
  assert.deepEqual(h.calls,['/api/packs/p1/stickers/s1/particle-preview']);
  assert.match(h.run('chMsgH(CH.msgs[0])'),/data-act=chreplay[^>]*>❤️<img class=ch-particle/,'the burst is drawn inside the heart badge');
  assert.match(h.run('chParticleH(CH.msgs[0])'),/translate\(-50\.0%,-50\.0%\)/,'a centred origin sits on the heart');
  h.run("CH.play.m1e.origin=[0.5,0.8]");assert.match(h.run('chParticleH(CH.msgs[0])'),/translate\(-50\.0%,-80\.0%\)/,'a fountain starts from the heart too');
});
