// reactions.js: Reaction effects (#/reactions) as the ANIMATION PRESET LIBRARY: a few presets {motion}_{NN} grouped by
// motion, an editor for one preset, and the wall of bank emojis where each emoji HOLDS one preset (hover plays it; Select + Assign).
// The whole file runs in a vm with the few globals it uses stubbed (console_vm.js), so the markup and the posts are checked for real. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadConsole } = require('./console_vm');

const BANK = "[{token:'kiss',emoji:'😘',preset:'burst',motion:'hearts pop'},{token:'cry',emoji:'😭',preset:'rain',motion:'tears fall'},{token:'happy',emoji:'😀',preset:'burst',motion:''}]";
const LIB = "{presets:[{id:'burst_01',motion:'burst',n:1,name:'Burst soft',settings:{magnitude:0.5,count:18}},{id:'burst_02',motion:'burst',n:2,name:'Burst normal',settings:{magnitude:0.7,count:28}},{id:'rain_01',motion:'rain',n:1,name:'Rain soft',settings:{magnitude:0.5,count:18}}],assign:{kiss:'burst_02'}}";
const J = (r, code) => JSON.parse(r(`JSON.stringify(${code})`));
const screen = () => {
  const r = loadConsole(['reactions.js', 'lottiestyle.js']);
  r(`RXS.bank=${BANK};RXS.lib=${LIB}`);
  return r;
};

test('preview cache deduplicates requests, retries failures and rejects invalidated completions', async () => {
  const r = screen();
  r(`globalThis.pending=[];
     globalThis.values={};globalThis.params={};
     globalThis.cache=rxPreviewCache(values,params,(u,b)=>new Promise(resolve=>pending.push(resolve)))`);
  const a=r("cache.load('a',{})"), b=r("cache.load('a',{})");
  assert.equal(r('pending.length'),1);
  r("pending.shift()({ok:false,j:{error:'temporary'}})");
  assert.equal(await a,null);assert.equal(await b,null);
  assert.equal(r("cache.entries.get('a').status"),'error');
  const retry=r("cache.load('a',{})");
  r("pending.shift()({ok:true,j:{url:'/retry.webp',params:{count:4}}})");
  assert.equal((await retry).url,'/retry.webp');
  assert.equal(r("values.a"),'/retry.webp');
  const old=r("cache.load('b',{})");
  r("cache.invalidate(k=>k==='b')");
  const fresh=r("cache.load('b',{})");
  r("pending.shift()({ok:true,j:{url:'/old.webp',params:{}}})");
  assert.equal(await old,null);
  assert.equal(r('values.b'),undefined);
  r("pending.shift()({ok:true,j:{url:'/new.webp',params:{}}})");
  assert.equal((await fresh).url,'/new.webp');
});

test('the library: one row per motion, collapsed (its ids in a line) until opened; an open motion shows its variations, each with an x', () => {
  const r = screen();
  let h = r('rxHtml()');
  assert.match(h, /Animation presets <small class=mut>3<\/small>/);
  for (const mo of ['burst', 'fountain', 'vortex', 'rain', 'confetti']) assert.match(h, new RegExp(`data-act=rxmotion data-m=${mo} aria-expanded=false`));
  assert.match(h, /<b>burst<\/b><span class=rx-mids>burst_01 \| burst_02<\/span><small class=mut>2 variations<\/small>/);
  assert.match(h, /data-act=rxmdel data-m=burst[^>]*title="Remove burst and its 2 variations"/, 'an x on the motion');
  assert.doesNotMatch(h, /data-rxcard=/, 'collapsed: no cards, no previews asked');
  r("ACT.rxmotion({dataset:{m:'burst'}})");
  h = r('rxLibHtml()');
  assert.match(h, /data-act=rxmotion data-m=burst aria-expanded=true/);
  assert.match(h, /data-rxcard=burst_01><button class=rx-x data-act=rxpdel data-id="burst_01"/, 'an x on every variation, no need to open it');
  assert.match(h, /<b>burst_01<\/b><small class=mut>1 emoji<\/small>/);
  assert.doesNotMatch(h, /data-rxcard=rain_01/, 'rain stays collapsed');
  assert.match(h, /data-act=rxzip/);
});

test('removing: one variation, a whole motion or the selected ones, in ONE request', async () => {
  const r = screen();
  r("globalThis.confirmDlg=(t,f)=>{globalThis.CONF=t;f()};POSTREPLIES.push({ok:true,j:{deleted:['burst_01','burst_02'],unassigned:1}});APIREPLIES.push({ok:true,j:" + LIB + "})");
  await r("ACT.rxmdel({dataset:{m:'burst'}})");
  assert.deepEqual(J(r, 'POSTS.at(-1)'), ['/api/particles/reaction-presets/delete', { ids: ['burst_01', 'burst_02'] }]);
  assert.match(r('CONF'), /Remove burst and its 2 variations \(burst_01, burst_02\)\?/);
  r("ACT.rxpsel({dataset:{id:'burst_01'}},{});ACT.rxpsel({dataset:{id:'rain_01'}},{shiftKey:true})");
  assert.deepEqual(J(r, '[...RXS.psel]'), ['burst_01', 'burst_02', 'rain_01'], 'Shift ticks the range in library order');
  assert.match(r('rxLibHtml()'), /data-act=rxpremove>.*Remove 3/);
  r("POSTREPLIES.push({ok:true,j:{deleted:['burst_01','burst_02','rain_01'],unassigned:0}});APIREPLIES.push({ok:true,j:" + LIB + "})");
  await r('ACT.rxpremove()');
  assert.deepEqual(J(r, 'POSTS.at(-1)')[1], { ids: ['burst_01', 'burst_02', 'rain_01'] });
  assert.equal(r('RXS.psel.size'), 0);
});

test('each emoji holds a preset: its own, else its motion\'s first variation; the hover plays it with that emoji, without hue', () => {
  const r = screen();
  assert.equal(r("rxPresetOf('kiss')"), 'burst_02');
  assert.equal(r("rxPresetOf('cry')"), 'rain_01');
  assert.equal(r("rxPresetOf('happy')"), 'burst_01');
  assert.deepEqual(J(r, "rxFor('cry')"), { token: 'cry', magnitude: 0.5, count: 18, preset: 'rain', hue_var: 0 });
  r("RXS.hover='cry';const k=rxHovKey('cry');RXS.pv[k]='/r.webp';RXS.par[k]={origin:[0.5,0.1]}");
  assert.match(r("rxTile(rxRow('cry'))"), /title="cry · rain_01"[\s\S]*class=rx-fx src="\/r.webp" style="width:64px;height:64px;left:calc\(90% - 32px\);top:calc\(90% - 6px\)"/,
    "the preset's origin sits on the sticker's bottom-right, 90% / 90%, as Telegram plays it");
});

test('clicking an emoji opens the preset it holds, on it; Save updates that preset; Save as new adds the next variation of its motion', async () => {
  const r = screen();
  r("ACT.rxpick({dataset:{t:'kiss'}})");
  assert.equal(r('RXS.ek.id'), 'burst_02');
  assert.equal(r('RXS.sample'), 'kiss');
  const ed = r('rxEditor()');
  assert.match(ed, /data-act=rxsave[^>]*>Save burst_02<\/button><button class="btn" data-act=rxcreate[^>]*>Save as new burst variation/);
  assert.doesNotMatch(ed, /Hue/, 'no hue: a preset carries no picture');
  r("RXS.ek.sim.count=40;POSTREPLIES.push({ok:true,j:{id:'burst_02'}});APIREPLIES.push({ok:true,j:" + LIB + "})");
  await r('ACT.rxsave()');
  const save = J(r, "POSTS.find(p=>p[0]==='/api/particles/reaction-presets/burst_02')");
  assert.equal(save[1].settings.count, 40);
  assert.equal(save[1].settings.preset, undefined, 'the motion is the group, not a setting');
  r("POSTREPLIES.push({ok:true,j:{id:'burst_03',name:'Burst 03'}});APIREPLIES.push({ok:true,j:" + LIB + "})");
  await r('ACT.rxcreate()');
  assert.equal(J(r, "POSTS.find(p=>p[0]==='/api/particles/reaction-presets')")[1].motion, 'burst');
  assert.equal(r('RXS.ek.id'), 'burst_03');
});

test('Select: Shift-click ticks the range, Assign gives a random variation of each one\'s own motion, instantly', async () => {
  const r = screen();
  r("ACT.rxmulti();ACT.rxpick({dataset:{t:'kiss'}},{});ACT.rxpick({dataset:{t:'happy'}},{shiftKey:true})");
  assert.deepEqual(J(r, '[...RXS.sel]'), ['kiss', 'cry', 'happy']);
  r("POSTREPLIES.push({ok:true,j:{assigned:3}});POSTREPLIES.push({ok:true,j:{assigned:0,cleared:0}});APIREPLIES.push({ok:true,j:" + LIB + "})");
  await r('ACT.rxmany()');
  assert.deepEqual(J(r, "POSTS.find(p=>p[0]==='/api/particles/reaction-presets/assign')"), ['/api/particles/reaction-presets/assign', { tokens: ['kiss', 'cry', 'happy'], preset_id: 'random' }]);
  r("RXQ.f='burst_02'");
  assert.deepEqual(J(r, 'rxList().map(m=>m.token)'), ['kiss'], 'the wall filters by the preset an emoji holds');
});

test('the wall draws one page at a time (Load more), and Select all ticks the whole list without drawing it', () => {
  const r = loadConsole(['reactions.js', 'lottiestyle.js']);
  r("RXS.bank=Array.from({length:250},(_,i)=>({token:'e'+i,emoji:'🙂',preset:'burst',motion:''}));RXS.lib={presets:[{id:'burst_01',motion:'burst',n:1,name:'b',settings:{}}],assign:{}}");
  const h = r('rxHtml()');
  assert.equal((h.match(/class="rx-e/g) || []).length, 120, 'one page, not 250');
  assert.match(h, /data-act=rxmore>Load more<\/button><small class=mut>120 of 250 shown · 130 more/);
  r('ACT.rxmore()');
  assert.equal(r('RXS.shown'), 240);
  r('RXS.multi=true;ACT.rxselall()');
  assert.equal(r('RXS.sel.size'), 250, 'every emoji of the list, drawn or not');
});

test('a pack sticker finds its predefined emoji (the longest match) and asks for the same fit preview as the wall hover', async () => {
  const r = loadConsole(['reactions.js', 'lottiestyle.js']);
  r(`RXS.bank=[{token:'heart',emoji:'❤️',preset:'burst'},{token:'heart_on_fire',emoji:'❤️‍🔥',preset:'burst'},{token:'laugh',emoji:'🤣',preset:'fountain'}];RXS.lib={presets:[],assign:{}}`);
  assert.equal(r("rxTokenOf('🤣')"), 'laugh');
  assert.equal(r("rxTokenOf('❤️‍🔥')"), 'heart_on_fire', 'the longest bank emoji the glyph starts with');
  assert.equal(r("rxTokenOf('❤')"), 'heart', 'with or without the variation selector');
  assert.equal(r("rxTokenOf('🐪')"), '', 'not a predefined emoji: no reaction');
  r("POSTREPLIES.push({ok:true,j:{url:'/l.webp',params:{origin:[0.5,0.5]}}})");
  const pv = JSON.parse(await r("rxHovPreview('laugh').then(JSON.stringify)"));
  assert.equal(pv.url, '/l.webp');
  const [[url, body]] = J(r, 'POSTS');
  assert.equal(url, '/api/particles/emoji-preview');
  assert.equal(body.fit, true);
  assert.equal(r("RXS.pv[rxHovKey('laugh')]"), '/l.webp', 'one cache shared with the wall');
});

test('current Emoji editor discloses advanced controls without changing its payload or losing a descriptor',()=>{
 const r=screen();r("ACT.rxpick({dataset:{t:'kiss'}})");const before=r('JSON.stringify(rxBody(RXS.ek))');const html=r('rxEditor()');
 assert.match(html,/<b>Motion<\/b>/);assert.match(html,/<b>Size<\/b>/);assert.match(html,/<details class=rx-grp data-rxadvanced>/);
 const advanced=html.slice(html.indexOf('<details class=rx-grp data-rxadvanced>'));assert.match(advanced,/rotation/);assert.match(advanced,/data-rxo=spin/);assert.match(advanced,/data-rxo=size_var/);
 const keys=J(r,'RXSIM.concat(RXO).map(o=>o[0])');for(const key of keys)assert.equal((html.match(new RegExp('data-rxo='+key+'(?=[ >])','g'))||[]).length,1,key);
 assert.equal(r('JSON.stringify(rxBody(RXS.ek))'),before);r('RXS.ek.sim.spin=2');assert.match(r('rxAdvSummary(RXS.ek)'),/rotation ×2/);
});
