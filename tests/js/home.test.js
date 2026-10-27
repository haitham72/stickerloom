const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// Home (console/home.js): the filters, the hero's pick, and the page drawn from the caller's real library; the logo opens Home. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { loadConsole } = require('./console_vm');

const st = (id, type = 'still', o = {}) => ({ id, name: 'Cat ' + id, emoji: '😺', type, file: id + (type === 'animated' ? '.webm' : '.png'), ...o });
const packs = [
  { id: 'p1', name: 'Cats', created: 1, cover: 'a', stickers: [st('a', 'animated'), st('b')] },
  { id: 'p2', name: 'Falcon', created: 3, stickers: [st('c', 'animated', { particles: ['P001'] })], telegram: { sets: [{ link: 'https://t.me/addstickers/falcon_by_bot' }] } },
  { id: 'p3', name: 'Dogs', created: 2, stickers: [st('d', 'still', { emoji: '🐶' })] },
];
const STUB = `document.querySelectorAll=()=>[];const location={hash:''};let reducedMotion=false;const matchMedia=()=>({matches:reducedMotion});
const box=()=>({innerHTML:'',hidden:false,value:'',oninput:null,scrollIntoView(){},scrollTo(){}});['s-home','hm-q','hm-gal','hm-gal-more','hm-empty','hm-disc'].forEach(k=>DOMSTUB[k]=box());`;
const load = () => loadConsole(['home.js'], { extra: STUB });

test('a pack is animated, static, with particles, or several of them; the Telegram link is its first set', () => {
  const run = load();
  run(`LIB={packs:${JSON.stringify(packs)}}`);
  assert.deepEqual(JSON.parse(run(`JSON.stringify(LIB.packs.map(p=>hmPackInfo(p).tags))`)), [['animated', 'static'], ['animated', 'particles'], ['static']]);
  assert.equal(run(`hmPackInfo(LIB.packs[0]).label`), 'Mixed pack');
  assert.equal(run(`hmPackInfo(LIB.packs[1]).telegram`), 'https://t.me/addstickers/falcon_by_bot');
});

test('the hero shows up to three stickers, newest pack first, its cover when it has one', () => {
  const run = load();
  assert.deepEqual(JSON.parse(run(`JSON.stringify(hmHero(${JSON.stringify(packs)}).map(s=>s.id))`)), ['c', 'd', 'a']);
  assert.equal(run(`hmHero([]).length`), 0);
});

test('Home draws individual stickers opening their packs; an empty library asks for a first pack', async () => {
  const run = load();
  run(`LIB={packs:${JSON.stringify(packs)}}`);
  await run(`RENDER.home()`);
  const page = run(`$('s-home').innerHTML`), gal = run(`$('hm-gal').innerHTML`);
  assert.match(page, /3 packs/);
  assert.match(page, /4 stickers/);
  for (const id of ['p1', 'p2', 'p3']) assert.match(gal, new RegExp(`data-act=openpack data-id="${id}"`));
  for (const id of ['a', 'b', 'c', 'd']) assert.match(gal, new RegExp(`data-m=${id}`));
  assert.equal((gal.match(/class=hm-card /g) || []).length, 4);
  assert.doesNotMatch(gal, /hm-film|\.mp4/, 'no film or video is bundled');
  assert.match(gal, /href="https:\/\/t.me\/addstickers\/falcon_by_bot"/);
  for (const to of ['effects', 'library']) assert.match(page, new RegExp(`data-act=nav data-to=${to}`));
 assert.match(page, /data-act=railcreate data-to=generate/, 'the Studio entry goes through railcreate');
  assert.match(page, /data-act=hmai/);
  assert.doesNotMatch(page, /hmfilm|hmonboarding/);
  assert.doesNotMatch(page, /preview/i, 'nothing on Home is a preview');
  run(`LIB={packs:[]}`);
  await run(`RENDER.home()`);
  assert.match(run(`$('s-home').innerHTML`), /Make your first pack/);
  assert.match(run(`$('s-home').innerHTML`), /No packs yet/, 'a CSS-only empty state, no media');
});

test('a filter redraws the gallery; the logo opens Home', async () => {
  const run = load();
  run(`LIB={packs:${JSON.stringify(packs)}}`);
  await run(`RENDER.home()`);
  run(`ACT.hmfilter({dataset:{f:'particles'}})`);
  const gal = run(`$('hm-gal').innerHTML`);
  assert.match(gal, /data-id="p2"/);
  assert.doesNotMatch(gal, /data-id="p1"/);
  run(`route_='agent';ACT.home()`);
  assert.equal(run(`location.hash`), '#/home');
  const app = readConsoleFile(path.join(__dirname, '..', '..', 'stickerloom', 'console', 'app.js'), 'utf8');
  assert.match(app, /\|\|'home'/, 'an empty hash opens Home');
});

test('the showcase filters individual stickers and bounds media to twenty-four', () => {
  const run = load();
  run(`LIB={packs:${JSON.stringify(packs)}}`);
  const ids = (q, f) => JSON.parse(run(`JSON.stringify(hmStickers(LIB.packs,${JSON.stringify(q)},${JSON.stringify(f)}).map(x=>x.s.id))`));
  assert.deepEqual(ids('', 'animated'), ['c', 'a']);
  assert.deepEqual(ids('', 'static'), ['d', 'b']);
  assert.deepEqual(ids('', 'particles'), ['c']);
  assert.deepEqual(ids('cat b', 'all'), ['b']);
  assert.equal(run(`hmStickers([{name:'Many',stickers:Array.from({length:30},(_,id)=>({id}))}],'','all').length`), 24);
  assert.deepEqual(JSON.parse(run(`JSON.stringify(hmStickers([{name:'P',stickers:[{id:'a'},{id:'r',source:{particle_set:'P001'}}]}],'','all').map(x=>x.s.id))`)), ['a'], 'an added burst is a reaction, not a sticker: never in the showcase');
});

test('each responsive band holds two rows of distinct stickers', async () => {
  const run = load();
  for (const [width, cols] of [[390,2],[560,3],[780,4],[1000,5],[1220,6]]) {
    assert.equal(run(`hmColumns(${width})`), cols);
    const bands = JSON.parse(run(`JSON.stringify(hmBands(Array.from({length:24},(_,i)=>i),${cols}))`));
    assert.deepEqual(bands.map(b=>b.length), [cols*2,cols*2]);
    assert.equal(new Set(bands.flat()).size, cols*4);
  }
  run(`LIB={packs:[{id:'many',name:'Many',stickers:Array.from({length:24},(_,i)=>({id:'s'+i}))}]}`);
  await run('RENDER.home()');
  const first=run(`$('hm-gal').innerHTML`), second=run(`$('hm-gal-more').innerHTML`);
  assert.equal((first.match(/class=hm-card /g)||[]).length,12);
  assert.equal((second.match(/class=hm-card /g)||[]).length,12);
  assert.match(first,/data-m=s0>/);
  assert.doesNotMatch(second,/data-m=s0>/);
  assert.match(second,/data-m=s12>/);
});

test('the first start of a session opens Home from any screen, once; a link to one thing keeps it', () => {
  const run = load();
  const first = h => run(`(()=>{const m={};const s={getItem:k=>m[k]||null,setItem:(k,v)=>{m[k]=v}};return [hmFirstStart(s,${JSON.stringify(h)}),hmFirstStart(s,${JSON.stringify(h)})].join()})()`);
  assert.equal(first('#/agent'), 'true,false', 'the old first screen goes to Home, and only on the first start');
  assert.equal(first(''), 'true,false');
  assert.equal(first('#/pack/20b742ee'), 'false,false', 'a deep link is kept');
  assert.equal(run(`hmFirstStart({getItem(){throw 0}},'')`), false, 'no storage: the hash decides, as before');
});

test('Home never asks the server for anything but the library', async () => {
  const run = load();
  run(`LIB={packs:${JSON.stringify(packs)}}`);
  await run(`RENDER.home()`);
  assert.equal(run('APICALLS'), 0);
  assert.match(run(`$('hm-gal').innerHTML`), /data-act=openpack data-id="p1"/);
  run(`LIB={packs:[]}`);
  await run(`RENDER.home()`);
  assert.equal(run('APICALLS'), 0, 'an empty library does not look for anyone else\'s packs');
});
