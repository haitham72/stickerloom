const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// The pack's particle studio and the sticker's one line (2026-10-03, docs/architecture.md ("Reactions")): one set belongs to the pack, so the pack page carries the
// particle studio (its sets as shared cards, its bursts with Add, Make reactions for this pack / Use an existing set) and a sticker shows one line
// linking to it. The statements are read out of packs.js and run with the few globals they use stubbed. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const DIR = path.join(__dirname, '..', '..', 'stickerloom', 'console');
const src = readConsoleFile(path.join(DIR, 'packs.js'), 'utf8');
const app = readConsoleFile(path.join(DIR, 'app.js'), 'utf8');

/** One whole top-level statement of packs.js: from `marker` to the next line that starts in column 0. */
function statement(marker) {return consoleStatement(src,marker);}

function load(pkpt) {
  const counts = [];
  const sandbox = {
    esc: s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    ic: n => `<svg data-i=${n}></svg>`,
    PKPT: pkpt,
    ptBody: (j,s) => '<owner-gallery>'+(j.rows||[]).map(x=>x.id).join(',')+'</owner-gallery>',
    ptCount: j => (j.rows||[]).length + (j.drafts||[]).length,
    ptCounts: pid => { counts.push(pid); },
    spSetCard: s => `<setcard data-for=${s.id}>`,
  };
  const body = ['const pkIsReaction=', 'const pkSplit=', 'const ptWhy=', 'const ptPackName=', 'function pkPsHtml(', 'function pkBurst(', 'function pkStickerLine('].map(statement).join('\n');
  const f = new Function(...Object.keys(sandbox), body + '\nreturn {pkPsHtml,pkBurst,pkStickerLine,pkIsReaction,pkSplit};')(...Object.values(sandbox));
  return { ...f, counts };
}

const SETS = [{ id: 'P001', name: 'Barbie <hearts>',owner:[{sticker_id:'a1'}] }, { id: 'P002', name: 'Bats',owner:[{sticker_id:'a2'}] }];
const BURSTS = [{ set: 'P001', id: 'R001', set_name: 'Barbie hearts', preset: 'burst', status: 'READY', url: '/out/particles/P001/renders/R001.webm', warnings: ['effect_tail_faded'], blocks: [], added_to: null }];

test('while the pack’s sets are not read yet the studio says so and asks for nothing', () => {
  const h = load({ sets: {}, bursts: {} });
  const s = h.pkPsHtml({ id: 'p1', name: 'Barbie' });
  assert.match(s, /Reaction studio/);
  assert.match(s, /Reading the pack's reaction sets/);
  assert.doesNotMatch(s, /data-act=/);
});

test('the studio shows the pack’s sets as shared cards and both ways to get one', () => {
  const h = load({ sets: { p1: SETS }, bursts: {} });
  const s = h.pkPsHtml({ id: 'p1', name: 'Barbie' });
  assert.doesNotMatch(s, /reactions made for the stickers in this pack/, 'the header stays clean: title, scope, actions');
  assert.match(s, /data-act=psmakepack data-p=p1>Make reactions for this pack</);
  assert.match(s, /data-act=pspickpack data-p=p1>Use reactions of another sticker</);
  assert.match(s, /<setcard data-for=P001>/);
  assert.match(s, /<setcard data-for=P002>/);
  const empty = load({ sets: { p1: [] }, bursts: {} }).pkPsHtml({ id: 'p1', name: 'Barbie' });
  assert.match(empty, /No reactions made for these stickers yet/);
  assert.match(empty, /data-act=psmakepack/);
});

test('bursts are wired to the set’s routes: a ready one is added with a click, an added one says so, a failed one says why and cannot be added', () => {
  const ok = { ...BURSTS[0], id: 'R002', warnings: [] };
  const done = { ...BURSTS[0], id: 'R003', warnings: [], added_to: 'p1' };
  const bad = { ...BURSTS[0], id: 'R004', status: 'FAILED', blocks: ['size_budget'], warnings: [], url: null };
  const h = load({ sets: { p1: [] }, bursts: { p1: [BURSTS[0], ok, done, bad] } });
  const s = h.pkPsHtml({ id: 'p1', name: 'Barbie' });
  assert.match(s, /Bursts rendered for this pack/);
  assert.match(s, /<video src="\/out\/particles\/P001\/renders\/R001\.webm" autoplay loop muted/);
  assert.match(s, /Barbie hearts/);
  assert.match(s, /burst · ready/);
  assert.match(s, /data-act=psbadd data-id=P001 data-r=R002 data-p=p1>Add to this pack</, 'the same handler as the set card’s burst maker');
  assert.match(s, /data-act=psbadd data-id=P001 data-r=R001 data-p=p1>Add anyway</, 'a warning is a warning: the person decides');
  assert.doesNotMatch(s, /data-r=R003/, 'already in the pack: no second Add');
  assert.doesNotMatch(s, /data-r=R004/, 'a file Telegram would reject cannot be added');
  assert.match(s, /size_budget|size budget/, 'and it says why');
  assert.doesNotMatch(s, /data-act=ptadd|data-act=psdel/, 'no per-sticker Add and no delete of a burst');
});

test('a burst’s warnings are sentences, a missing file says so', () => {
  const h = load({ sets: {}, bursts: {} });
  assert.match(h.pkBurst(BURSTS[0]), /effect tail faded/, 'FXWHY lives in effects.js: here the id stays readable');
  assert.match(h.pkBurst({ ...BURSTS[0], url: null, missing: true }), /file missing/);
});

test('an added burst is a reaction, not a sticker: split out of the grid and loop-free',()=>{
 const h=load({sets:{},bursts:{}});
 const pack={id:'p1',stickers:[{id:'a1'},{id:'r1',source:{particle_set:'P001',preset:'burst'}}]};
 assert.deepEqual(h.pkSplit(pack).stickers.map(s=>s.id),['a1']);
 assert.deepEqual(h.pkSplit(pack).reactions.map(s=>s.id),['r1']);
 assert.equal(h.pkIsReaction({id:'a1'}),false);
 const line=h.pkStickerLine({id:'r1',pack_id:'p1',source:{particle_set:'P001',preset:'burst'}});
 assert.match(line,/Reaction burst/);assert.match(line,/P001/);
 assert.doesNotMatch(line,/data-act=ptmake|Create reactions|New version/,'a reaction never offers reactions of its own');
});

test('a sticker shows only its own reactions and scoped creation',()=>{
 const h=load({sets:{},bursts:{}});assert.match(h.pkStickerLine({id:'a1',pack_id:'p1'}),/Reading the sticker/);
 const none=load({sets:{p1:[]},bursts:{}});assert.match(none.pkStickerLine({id:'a1',pack_id:'p1'}),/data-act=ptmake data-p=p1 data-s=a1>Create reactions/);
 const reading=load({sets:{p1:SETS},bursts:{}}).pkStickerLine({id:'a1',pack_id:'p1'});
 assert.match(reading,/Reading the sticker/);assert.match(reading,/>New version</);
 const some=load({sets:{p1:SETS},bursts:{},d:{a1:{rows:[{id:'P001',version:1}],drafts:[]}}}),line=some.pkStickerLine({id:'a1',pack_id:'p1'});
 assert.match(line,/1 saved version/);assert.doesNotMatch(line,/Bats|P002|No particles yet/);assert.match(line,/New version/);assert.match(line,/<owner-gallery>P001/);
});

test('the Library has a Reactions tab on the shared switch, and every new action has a handler', () => {  assert.match(app, /data-act=libtab data-t=particles>Reactions</, 'the third Library tab');
  const all = src + readConsoleFile(path.join(DIR, 'particles.js'), 'utf8') + readConsoleFile(path.join(DIR, 'effects.js'), 'utf8');
  for (const act of ['psopen', 'psassign', 'psassignsave', 'psunassign', 'psdup', 'psrename', 'psdel', 'psrestore', 'pspickcell', 'pspickcancel', 'pspicksave', 'pspickset', 'psmakepack', 'pspickpack', 'fxuseset']) {
    assert.match(all, new RegExp(`ACT\\.${act}=`), `${act} has a handler`);
    assert.match(all, new RegExp(`data-act=${act}\\b`), `${act} is used`);
  }
});

test('the chrome says particles, not pieces, on every screen', () => {
  const bad = [];
  for (const f of require('./console_source').consoleFiles()) {
    let code = readConsoleFile(path.join(DIR, f), 'utf8');
    code = code.replace(/\/\*.*?\*\//gs, '');
    code = code.replace(/fx-pieces|\w*[Pp]iece\w*/g, '');
    const m = code.match(/\bpieces?\b/i);
    if (m) bad.push(`${f}: ${m[0]}`);
  }
  assert.deepEqual(bad, [], 'a person reads reactions everywhere (identifiers like fxPieces are not chrome)');
});
