const test=require('node:test');
const assert=require('node:assert/strict');
const {loadConsole}=require('./console_vm');
const setup=()=>loadConsole(['effects.js','particles.js'],{studio:true,extra:'const location={hash:""},CH={pending:null};'});
const owner="LIB.packs=[{id:'parent',name:'Parent',stickers:[{id:'target',name:'Target',source:{generation:'G001',index:1},particles:['P001']}]}];SP.scope={pack_id:'parent',sticker_id:'target'};SP.sel=new Set(['target']);SP.pack='parent';SP.entry=1;SP.target='P001'";
const set="{id:'P001',name:'Particles',owner:[{pack_id:'parent',sticker_id:'target'}],packs:['parent'],source:{kind:'drawn'},elements:['stars'],cells:[{n:1,picked:true,url:'/poster.png',clip_url:'/animated.webm'}],n_cells:1,n_picked:1,renders:[]}";

test('route ownership cancels timers and invalidates earlier reads without losing saved drafts',async()=>{
 const run=setup();
 run(`globalThis.callbacks=[];globalThis.cleared=[];
 setTimeout=fn=>{callbacks.push(fn);return callbacks.length};clearTimeout=id=>cleared.push(id);
 globalThis.calls=0;globalThis.scope=routeOwner(n=>n==='pack');
 scope.enter('pack');scope.schedule(()=>calls++,2500);scope.leave();scope.enter('home');callbacks[0]();`);
 assert.equal(run('calls'),0);
 assert.ok(run('cleared.includes(1)'));
 run(`SPL.detail.P001={id:'P001',name:'draft'};SPROUTES.enter('library');
 APIREPLIES.push(new Promise(resolve=>globalThis.finishRead=resolve));`);
 const pending=run('spLibSync(true)');
 run(`SPROUTES.leave();SPROUTES.enter('reactions');finishRead({ok:true,j:{sets:[{id:'old'}]}})`);
 await pending;
 assert.equal(run('SPL.sets'),null,'stale read cannot populate the new route');
 assert.equal(run('SPL.detail.P001.name'),'draft');
 assert.equal(run('SPL.busy'),false);
});

test('new AI choice shows image and animated sprite choices with prompt and both grids',()=>{
 const run=setup();run(owner+";spKind('video')");const html=run('spSetupHtml(SP,LIB.packs,[])');
 for(const label of ['AI image sprites','Kling animated · from scratch','Reaction prompt','2 × 2','3 × 3'])assert.ok(html.includes(label),label);
 assert.match(html,/class="sp-mode on" data-act=spkind data-v=video/,'Kling is its own card, lit when chosen');
 assert.doesNotMatch(html,/Nothing is spent|starts and ends|ownership|ledger/i);
});
test('image-origin and recovered sets both allow Kling Add more',()=>{
 const run=setup();for(const kind of ['drawn','stickers','video']){
 const html=run(`spMoreHtml({id:'${kind}',source:{kind:'${kind}'},elements:['stars']})`);
 for(const label of ['Image sprites','Animated sprites · Kling','Use existing sprites'])assert.ok(html.includes(label),`${kind}: ${label}`);
 }
});
test('animated sprite selection loops the clip while still sprites remain images',()=>{
 const run=setup();const html=run(`spEditorCells(${set})`);
 assert.match(html,/<video src="\/animated.webm" poster="\/poster.png" autoplay loop muted playsinline/);
 assert.doesNotMatch(html,/<img src="\/poster.png"/);
 assert.match(run("spCellMedia({url:'/still.png'})"),/<img src="\/still.png"/);
});
test('selection save sends selected cell numbers without the former ReferenceError',async()=>{
 const run=setup();run(owner+`;SP.set=${set};SPL.detail.P001=SP.set;SPL.pick.P001=new Set([1]);POSTREPLIES.push({ok:true,j:SP.set})`);
 await run("ACT.pspicksave({dataset:{id:'P001'}})");
 assert.equal(run('JSON.stringify(POSTS[0])'),JSON.stringify(['/api/particles/P001',{picked:[1]}]));
 assert.equal(run('SPL.pick.P001'),undefined);
});
test('existing completed video enters same set and simulator without any paid call',async()=>{
 const run=setup();run(owner+`;SP.eid='E002';SP.rec={id:'E002',mode:'video',results:[{mode:'video',cell:2,file:'results/R002.webm'}]};POSTREPLIES.push({ok:true,j:${set}})`);
 await run('spImport(SP.rec)');
 const body=JSON.parse(run('JSON.stringify(POSTS[0][1])'));
 assert.equal(body.from_effect,'E002');assert.equal(body.target,'P001');assert.deepEqual(body.picked,[2]);assert.equal(body.go,undefined);
 assert.equal(run('SP.eid'),'');assert.equal(run('SP.set.id'),'P001');
});
test('old import reply cannot replace a newer target',async()=>{
 const run=setup();run(owner+`;SP.eid='E002';SP.rec={id:'E002',mode:'video',results:[{cell:1,file:'clip.webm'}]};POSTREPLIES.push({ok:true,j:${set}})`);
 const pending=run('spImport(SP.rec)');run("SP.entry=2;SP.target='P999';SP.eid=''");await pending;
 assert.equal(run('SP.target'),'P999');assert.equal(run('SP.set'),null);
});
test('Kling Add more quotes freely and carries quote effect context into paid click',async()=>{
 const run=setup();run(owner+`;SP.set=${set};SPL.sets=[SP.set];const m=spMoreState(SP.set);m.mode='video';POSTREPLIES.push({ok:true,j:{credits:4.5,effect:'E009'}})`);
 await run("spMoreEstimate('P001')");assert.equal(run('POSTS[0][1].estimate'),true);assert.equal(run('POSTS[0][1].go'),undefined);
 await run("ACT.psmoredraw({dataset:{id:'P001'}})");
 assert.equal(run('POSTS[1][1].effect'),'E009');assert.equal(run('POSTS[1][1].mode'),'video');assert.equal(run('POSTS[1][1].go'),true);
});
test('unavailable Add more quote shows retry and spends nothing',async()=>{
 const run=setup();run(owner+`;SP.set=${set};POSTREPLIES.push({ok:true,j:{credits:null,cost_error:'Unavailable'}})`);
 await run("spMoreEstimate('P001')");assert.match(run('spMoreHtml(SP.set)'),/Retry price/);
 await run("ACT.psmoredraw({dataset:{id:'P001'}})");assert.equal(run('POSTS.length'),1);
});
test('saved particle settings preserve motion and sprite size for another sticker or chat',async()=>{
 const run=setup();run(owner+`;SP.set=${set};SPL.detail.P001=SP.set;spBurstHtml(SP.set);SPB.P001.preset='vortex';SPB.P001.par={gravity:.5};SPB.P001.shown={seed:7,count:12};SPB.P001.size={px:148,scale:2};POSTREPLIES.push({ok:true,j:SP.set})`);
 await run("ACT.psbsave({dataset:{id:'P001'}})");
 assert.deepEqual(JSON.parse(run('JSON.stringify(POSTS[0][1].motion)')),{preset:'vortex',params:{seed:7,count:12,gravity:.5,sprite_px:148,scale:2}});
});
test('Save as new makes a new row with the edited motion in one call and keeps the original row independent',async()=>{
 const run=setup();run(owner+`;SP.set=${set};SPL.detail.P001=SP.set;spBurstHtml(SP.set);SPB.P001.preset='rain';POSTREPLIES.push({ok:true,j:{...SP.set,id:'P002',motion:{preset:'rain'},saved_at:2}})`);
 await run("ACT.psbsaveas({dataset:{id:'P001'}})");
 assert.equal(run('POSTS[0][0]'),'/api/particles/P001/save-as-new');assert.equal(run("POSTS.filter(p=>/duplicate|\\/api\\/particles\\/P00[12]$/.test(p[0])).length"),0,'one call: no duplicate-then-update pair');assert.equal(run('POSTS[0][1].motion.preset'),'rain');
 assert.equal(run('SP.target'),'P002');assert.equal(run('SPL.detail.P001.motion'),undefined);
});
test('Test in chat saves settings then sends the full owning sticker identity without rendering',async()=>{
 const run=setup();run(owner+`;SP.set=${set};SPL.detail.P001=SP.set;spBurstHtml(SP.set);POSTREPLIES.push({ok:true,j:SP.set})`);
 await run("ACT.pschat({dataset:{id:'P001'}})");
 assert.equal(run('CH.pending.id'),'target');assert.equal(run('CH.pending.pack_id'),'parent');assert.equal(run('location.hash'),'#/chat');
 assert.equal(run('POSTS.length'),1);assert.equal(run('POSTS[0][0]'),'/api/particles/P001');
});
test('reopening an imported effect retains simulator entry and never imports or pays again',()=>{
 const run=setup();run(owner+`;FX.rec={id:'E013',pack_id:'parent',mode:'sim',stickers:[{sticker_id:'target'}],set:{status:'DRAWN',generation:101,picked:[1]}};FX.set={...${set},source:{kind:'drawn',generation:'G101'}}`);
 assert.match(run('fxSaveBlock(FX,FX.rec)'),/data-act=fxopenset>Open in simulator/);
 run("ACT.fxopenset({closest:()=>({dataset:{fxx:'fx'}})})");
 assert.equal(run('SP.target'),'P001');assert.equal(run('SP.eid'),'');
 assert.equal(run("POSTS.some(([url])=>url==='/api/particles'||url.endsWith('/video')||url.endsWith('/more'))"),false);
});
test('preset followed immediately by Save does not retain previous preset dynamics',async()=>{
 const run=setup();run(owner+`;SP.set=${set};spBurstHtml(SP.set);SPB.P001.shown={gravity:2,count:80};SPB.P001.busy=1;POSTREPLIES.push({ok:true,j:SP.set})`);
 run("ACT.psbpreset({dataset:{id:'P001',n:'rain'}})");
 await run("ACT.psbsave({dataset:{id:'P001'}})");
 assert.deepEqual(JSON.parse(run('JSON.stringify(POSTS[0][1].motion)')),{preset:'rain',params:{sprite_px:100,scale:1}});
});
test('preview from previous preset cannot overwrite current preview or saved settings',async()=>{
 const run=setup();run(owner+`;SP.set=${set};spBurstHtml(SP.set);SPB.P001.preset='burst';POSTREPLIES.push({ok:true,j:{url:'/old.gif',params:{count:80,gravity:2}}},{ok:true,j:{url:'/current.gif',params:{count:12,gravity:.2}}})`);
 const pending=run("spBurstPreview('P001')");
 run("ACT.psbpreset({dataset:{id:'P001',n:'rain'}})");
 assert.equal(run('SPB.P001.shown.count'),undefined);
 await pending;
 assert.equal(run('SPB.P001.pv'),'/current.gif');assert.equal(run('SPB.P001.shown.count'),12);
 assert.equal(run('POSTS.length'),2);assert.equal(run('POSTS[1][1].preset'),'rain');
});
test('recovery without a parent has a reachable create action',()=>{
 const run=setup();run("LIB.packs=[];ACT.psrecover({dataset:{p:'source',s:'sprite'}})");
 assert.match(run('DIALOGS[0]'),/data-act=psrecovercreate>Create parent stickers/);
 run('ACT.psrecovercreate()');assert.equal(run('location.hash'),'#/studio');
});
test('mixed sources keep their Kling provenance visible',()=>{
 const run=setup();assert.match(run("spKindChip({source:{kind:'drawn'},cells:[{clip_url:'/clip.webm',job:'J039'}]})"),/Mixed sprites.*Kling/);
});


test('UX-08: the set heading tag agrees with its real owner list, never a hard-coded "on their own"',()=>{
 const run=setup();
 run(`SP.scope={set:'P001'};SP.set={...${set},owner:[{pack_id:'parent',sticker_id:'a'},{pack_id:'parent',sticker_id:'b'}]}`);
 assert.match(run('spBody([])'),/<b>Particles<\/b><span class=mut>made for 2 stickers<\/span>/);
 run(`SP.set={...${set},owner:[]}`);
 assert.match(run('spBody([])'),/<b>Particles<\/b><span class=mut>on their own<\/span>/);
});
test('Open and save mode writes a new row and leaves the source row unchanged',async()=>{
 const run=setup();run(owner+`;SPBS.P001=${set};spBurstState(SPBS.P001,'parent');SP.saveAsNew='P001';POSTREPLIES.push({ok:true,j:{...SPBS.P001,id:'P002'}})`);
 await run("ACT.psbsave({dataset:{id:'P001'}})");
 assert.equal(run("POSTS.filter(([u])=>!u.endsWith('/preview')).length"),1);assert.equal(run('POSTS[0][0]'),'/api/particles/P001/save-as-new');
 assert.equal(run('SPBS.P001.id'),'P001');assert.equal(run('SP.set.id'),'P002');assert.equal(run('SP.saveAsNew'),false);
});
test('saved set disclosure keeps controls, omits zero spend and combines Advanced',()=>{
 const run=setup();const html=run(`spSetCard(${set})`);
 for(const act of ['psassign','psopennew','psdup','psrename','psdel'])assert.ok(html.includes('data-act='+act));
 assert.doesNotMatch(html,/nothing spent yet/);assert.match(html,/aria-label="Set options"/);
 run(`SPL.detail.P001=${set};SPL.open='P001'`);const open=run(`spSetCard(${set})`);
 assert.match(open,/Manage stickers/);assert.equal((open.match(/class=fx-adv/g)||[]).length,1);assert.match(open,/Motion: burst · Change/);
});
