const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// The markup of the Earlier-batches column in live.js (the DOM half runs only in a browser). The statements are read out of the file and run with the few
// globals they use stubbed, so the markup is checked for real: ONE picture per batch (the first sticker, P4 of the UI/UX spec), the plain title, one row per batch, and what drawHist redraws (the column; under the batch the Particles section).
// Run: node --test tests/js     (STICKERLOOM_LIVE_JS points the test at another copy of live.js)
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const FILE = process.env.STICKERLOOM_LIVE_JS || path.join(__dirname, '..', '..', 'stickerloom', 'console', 'live.js');
const src = readConsoleFile(FILE, 'utf8');

/** One whole top-level statement of live.js: from `marker` to the next line that starts in column 0. */
function statement(marker) {return consoleStatement(src,marker);}

const BATCH = (grid, animated = [], noPng = []) => ({
  id: 5, generation_id: 'G005', prompt: 'a sad_owl', stage: 'sliced', ready: grid[0] * grid[1], animated: animated.length,
  edited: 0, created: 0, outline_px: 2, grid,
  cells: Array.from({ length: grid[0] * grid[1] }, (_, n) => ({
    index: n + 1, row: Math.floor(n / grid[1]), col: n % grid[1],
    png: noPng.includes(n + 1) ? null : `slices/S${n + 1}.png?e=7`,
    status: noPng.includes(n + 1) ? 'PENDING' : 'READY', animated: animated.includes(n + 1),
  })),
});

function load(route = 'library') {
  const sandbox = {
    esc: s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    titleCase: s => s.replace(/(^|\s)([a-z])/g, (_, a, b) => a + b.toUpperCase()),
    ago: () => '2 d ago',
    ic: n => `<svg data-i=${n}></svg>`,
    SES: { gens: [] },
    HB: { items: [], more: false, total: 0, loaded: true },
    route_: route,
    calls: { col: 0, sec: 0 },
    histCol: () => { sandbox.calls.col++; },
    spSecDraw: () => { sandbox.calls.sec++; },
  };
  const body = ['const histTitle=', 'const histInfo=', 'const histThumb=', 'const histVars=', 'const histBatches=', 'const histAll=', 'const histShown=', 'const histPackTitle=', 'const histEntry=', 'const histRow=', 'function histColHTML', 'function drawHist'].map(statement).join('\n');
  const api = new Function(...Object.keys(sandbox), body + '\nreturn {histThumb,histRow,histEntry,histColHTML,drawHist};')(...Object.values(sandbox));
  return { ...sandbox, ...api, env: sandbox };          // env: the globals the statements closed over (mutate them, not the copy)
}

test('a batch is ONE picture, the first sticker that has one, whatever its grid (a 3x3 is not nine thumbnails, a 2x2 not four)', () => {
  const hx = load();
  for (const grid of [[3, 3], [2, 2]]) {
    const h = hx.histRow(BATCH(grid, [1, 5]));
    assert.match(h, /class=lv-hth/);
    assert.equal((h.match(/<img /g) || []).length, 1, `${grid.join('x')}: one image`);
    assert.match(h, /src="\/out\/G005\/slices\/S1\.png\?e=7"/, 'the first sticker');
    assert.doesNotMatch(h, /--c:/, 'no column count: there is no grid');
  }
  assert.match(hx.histRow(BATCH([3, 3], [1])), /title="S1 · animated"/);
  assert.doesNotMatch(hx.histRow(BATCH([3, 3], [5])), /· animated"/, 'S1 is not the animated one');
});

test('the row says which batch it is; when the first cell has no picture the next one with a picture is shown, and a batch with none is a checkerboard, not a broken picture', () => {
  const hx = load();
  const html = hx.histRow(BATCH([3, 3], [1, 5, 9], [1]));
  assert.match(html, /A Sad Owl/);
  assert.match(html, /G005 · 9 stickers · 3 animated · edited 2 d ago/);
  assert.equal((html.match(/<img /g) || []).length, 1);
  assert.match(html, /slices\/S2\.png/, 'S1 has no picture yet: the first one that has is shown');
  const none = hx.histRow(BATCH([2, 2], [], [1, 2, 3, 4]));
  assert.equal((none.match(/<img /g) || []).length, 0);
  assert.match(none, /<span class=lv-hnoimg title="pending"/);
  assert.ok(!/src="[^"]*null/.test(html + none), 'no src built from a missing png');
});

test('a row is one button that presents the batch; the batch the Studio presents is marked', () => {
  const hx = load();
  const it = BATCH([3, 3]);
  assert.match(hx.histRow(it), /^<div class=lv-hfam draggable=true data-hid=5 title="[^"]*"><button class="lv-hrow" data-act=hopen data-id=5 aria-pressed=false/, 'one button in a draggable entry (drop it on another batch to join its group)');
  hx.SES.gens = [5];
  assert.match(hx.histRow(it), /class="lv-hrow on"[^>]*aria-pressed=true/);
  hx.SES.gens = [6];
  assert.ok(!/lv-hrow on/.test(hx.histRow(it)), 'another batch is presented: this row is not marked');
  assert.ok(!hx.histRow(it).includes('data-act=hbx'), 'no second way to open a batch');
});

test('the column title is plain text, every batch is a row and nothing says Load more', () => {
  const hx = load();
  hx.env.HB.items = Array.from({ length: 120 }, (_, n) => ({ ...BATCH([3, 3]), id: n + 1, generation_id: `G${n + 1}` }));
  hx.env.HB.total = 120;
  const html = hx.histColHTML();
  assert.match(html, /<h1>Projects<\/h1>/);   // the list of every project (D2, 2026-10-11): it was "Earlier batches"
  assert.match(html, /120 in total/);
  assert.equal((html.match(/class="lv-hrow/g) || []).length, 120, 'the whole list is rendered, not one page');
  assert.ok(!/hmore|Load more/.test(html), 'no pagination button');
  assert.ok(!/<button[^>]*lv-hh/.test(html), 'the title is not a button');
  hx.env.HB.items = [];
  hx.env.HB.total = 0;
  assert.match(hx.histColHTML(), /No batches yet/);
});

test('drawHist redraws the column on the Studio and Create, and always the batch\u2019s Particles section; the old history block is gone', () => {
  const lib = load('library');
  lib.drawHist();                                  // the library has its own column: only the Particles section is redrawn
  assert.deepEqual(lib.env.calls, { col: 0, sec: 1 });
  for (const r of ['generate', 'create']) {
    const hx = load(r);
    hx.drawHist();
    assert.deepEqual(hx.env.calls, { col: 1, sec: 1 }, r);
  }
  assert.ok(!/ghist|hxBatch|History of/.test(statement('function drawHist')), 'nothing about the old per-sticker history');
});

test('a family is ONE plain entry in Earlier batches (no strip); its generations are the generations row in the Studio', () => {
  const hx = load();
  const root = { ...BATCH([2, 2]), id: 104, generation_id: 'G104', prompt: 'superhero paris' };
  const edit = { ...BATCH([2, 2]), id: 105, generation_id: 'G105', prompt: 'superhero paris' };
  const fam = { ...root, variants: [root, edit] };
  hx.env.SES.gens = [105];
  const row = hx.histRow(fam);
  assert.match(row, /^<div class=lv-hfam draggable=true data-hid=104 title="[^"]*"><button class="lv-hrow on" data-act=hopen data-id=105/, 'the entry opens the variation in view');
  assert.match(row, /<b>Superhero Paris<\/b>/, 'the parent\'s title');
  assert.match(row, /· 2 variations<\/small>/);
  assert.doesNotMatch(row, /lv-hvar|gvar|data-act=hleave|›/, 'no strip in the column');
});

test('a pack is ONE entry with its batches under it, Batch 1 … n; the head opens them all, each batch is draggable and can be taken out', () => {
  const hx = load();
  const b = (id, prompt) => ({ ...BATCH([3, 3]), id, generation_id: `G${id}`, prompt });
  const h = hx.histEntry({ ...b(123, 'generic emojis core-v1'), batches: [b(123, 'generic emojis core-v1'), b(122, 'generic emojis social-v1')] });
  assert.match(h, /data-act=hopenpack data-ids="123,122"/);
  assert.match(h, /<b>Generic Emojis<\/b><small>2 batches · 18 stickers/, 'the pack title has no grid name');
  assert.match(h, /Batch 1 · /);
  assert.match(h, /Batch 2 · /);
  assert.equal((h.match(/data-act=hunpack/g) || []).length, 2);
  assert.equal((h.match(/draggable=true data-hid=/g) || []).length, 2, 'every batch can be dragged onto another entry');
  assert.equal(hx.histEntry(b(5, 'one')), hx.histRow(b(5, 'one')), 'a pack of one batch is the plain row');
});

