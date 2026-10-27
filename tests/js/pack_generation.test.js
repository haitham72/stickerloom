const {readConsoleFile, statement} = require('./console_source');
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');
const source = readConsoleFile(path.join(__dirname, '../../stickerloom/console/generate.js'));

function wizard(view = [127], off = []) {
  const generation = (n, animated) => ({number: n, generation_id: `G${n}`, source: {subject: 'girl_viking'},
    stickers: [{index: 1, key: 'wave', emoji: '🙂', name: 'wave.png', png: 'wave.png',
      webm: 'wave.webm', status: 'READY', anim_status: animated ? 'READY' : null}]});
  const GM = new Map([[127, generation(127, false)], [128, generation(128, true)], [130, generation(130, false)]]);
  const pack = {id: 'g128-pack', name: 'Girl Viking', stickers: [
    {type: 'static', source: {generation: 'G128', index: 1}},
  ]};
  const radio = {value: 'new', checked: false};
  const nodes = {gpname: {value: 'Girl Viking 2', focus() {this.onfocus();}, select() {}},
    gpold: {value: pack.id}, pwbg: {}, pwprev: {}, pwtwins: {}, pwgo: {}};
  const posts = [], dialogs = [];
  const ctx = vm.createContext({
    ACT: {}, PW: null, PWBG: [['chat', 'Chat']], SES: {gens: view, off, pack: pack.id}, GM,
    FAM: new Map([[127, {picked: 'G128'}], [128, {picked: 'G128'}]]),
    LIB: {packs: [pack]}, sessionGens: () => view.map(n => GM.get(n)),
    keptOf: g => g.stickers.filter(t => t.status === 'READY'),
    loadLib: async () => {}, packById: id => id === pack.id ? pack : {id, name: 'New pack'},
    spGid: id => +String(id).replace(/\D/g, ''), titleCase: () => 'Girl Viking', esc: String,
    $: id => nodes[id], dlg: html => dialogs.push(html),
    document: {querySelector: selector => selector.includes('name=gp') ? radio : null, querySelectorAll: () => []},
    postWait: async (url, body) => {posts.push({url, body}); return {ok: true, j: {added: 1}};},
    saveSes() {}, tick() {}, toast() {},
  });
  vm.runInContext(['const mainNum =', 'const included =', 'const animPhase =', 'const rname =',
    'ACT.gadd =', 'function pwPack()', 'function pwSync()', 'async function gaddrun(']
    .map(marker => statement(source, marker)).join('\n'), ctx);
  return {ctx, posts, dialogs, run: code => vm.runInContext(code, ctx)};
}

test('G127 packs its viewed stills even when G128 is main, animated and already in a pack', async () => {
  const w = wizard();
  await w.run('ACT.gadd()');
  assert.equal(w.ctx.PW.rows[0].g, 127);
  assert.equal(w.ctx.PW.anim, false);
  assert.match(w.dialogs[0], /value=new checked/);
  assert.doesNotMatch(w.dialogs[0], /\/out\/G128\//);
  await w.run('gaddrun("new-pack", "replace", {})');
  assert.deepEqual(w.posts.map(p => p.url), ['/api/generations/127/add']);
  assert.equal(w.posts[0].body.pack_id, 'new-pack');
  assert.equal(w.run('included()[0].number'), 128, 'animation still uses the main generation');
});

test('G128 still defaults to its own pack and offers to upgrade existing stills', async () => {
  const w = wizard([128]);
  await w.run('ACT.gadd()');
  assert.equal(w.ctx.PW.rows[0].g, 128);
  assert.equal(w.ctx.PW.anim, true);
  assert.match(w.dialogs[0], /value=old checked/);
  w.run('document.querySelector("input[name=gp]:checked").value = "old"; pwSync()');
  assert.match(w.ctx.$('pwtwins').innerHTML, /Replace the still with the animated one/);
  await w.run('gaddrun("g128-pack", "replace", {})');
  assert.deepEqual(w.posts.map(p => p.url), ['/api/generations/128/add']);
});

test('explicit generation scope and excluded batches remain respected', async () => {
  const w = wizard([127, 130]);
  await w.run('ACT.gadd({dataset: {g: "G127"}})');
  assert.deepEqual(Array.from(w.ctx.PW.rows, r => r.g), [127]);
  const excluded = wizard([127, 130], [130]);
  await excluded.run('ACT.gadd()');
  assert.deepEqual(Array.from(excluded.ctx.PW.rows, r => r.g), [127]);
});

test('a manual batch defaults only to its explicitly planned pack', async () => {
  const w = wizard();
  w.ctx.GM.get(127).slots = {emoji_batch: {pack_id: 'g128-pack'}};
  await w.run('ACT.gadd()');
  assert.match(w.dialogs[0], /value=old checked/);
  assert.equal(w.ctx.PW.rows[0].g, 127);
  const unavailable = wizard();
  unavailable.ctx.GM.get(127).slots = {emoji_batch: {pack_id: 'removed-pack'}};
  await unavailable.run('ACT.gadd()');
  assert.match(unavailable.dialogs[0], /value=new checked/);
});
