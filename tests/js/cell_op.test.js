const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// Phase 1 of the UI/UX spec (2026-10-03): ONE decision per cell, ONE handler. P1: a click on a cell of the image or video sheet only allows / takes back / includes / drops, it never
// opens a tile (opening belongs to the thumbnail on the right). P2: "Use it anyway", the tile's x / + and the sheet cell are the same control: they press the same handler, which
// asks cellOp what to do from the server's word and the review state, so they send the same request and leave the same decision row. The statements are read out of generate.js
// and run with the few globals they use stubbed. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const src = readConsoleFile(path.join(__dirname, '..', '..', 'stickerloom', 'console', 'generate.js'), 'utf8');

function statement(marker) {return consoleStatement(src,marker);}

function load(opts = {}) {
  const posts = [], toasts = [];
  const GM = new Map();
  const sandbox = {
    esc: s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    ic: n => `<svg data-i=${n}></svg>`, ACT: {}, GM, posts, toasts,
    postWait: opts.postWait ? async (url, body) => { posts.push([url, body]); return opts.postWait(url, body); } : async (url, body) => { posts.push([url, body]); return { ok: true, j: {} }; },
    toast: (m, bad) => toasts.push([m, !!bad]), tick: async () => {}, bg: 'checker', PVON: new Set(), GS: {},
  };
  const body = ['let glast="",MD=null;', 'const animPhase=', 'const CAT=', 'const CATORDER=', 'const CATOF=', 'const WARNWHY=', 'const plainWarn=', 'const ANIMWHY=', 'const ALW=', 'const whyOf=',
    'const canAllow=', 'const hasAllowed=', 'const clickAllow=', 'function cellOp(', 'const cellVerb=', 'const cellTitle=', 'const cellLabel=', 'const cellAct=', 'const isOob=', 'const oobNote=', 'const cellState=', 'const CELLTXT=', 'function issuesOf(', 'function mark(', 'function chip(',
    'const sheetOf=', 'const cutOf=', 'const LAY=', 'const layoutOfCell=', 'const ALQ=', 'const alPending=', 'async function allowCall(', 'async function dropCall(', 'function cellRun(', 'ACT.gcell=',
    'function blockedBox(', 'function blockedAnimOverlay(', 'function issueSvg(', 'function tileHtml('].map(s => s.startsWith('let ') ? s : statement(s)).join('\n');
  const f = new Function(...Object.keys(sandbox), body + '\nreturn {cellOp,cellLabel,cellRun,issueSvg,tileHtml,blockedBox,chip,ACT,LAY,mark};')(...Object.values(sandbox));
  return { ...f, posts, toasts, GM };
}

const CELL = [0, 0, 100, 100];
const ALLOW = {
  still: { can: [1], allowed: [3], undo: [3], why: { 1: 'the character touches the edge of its cell', 2: 'almost nothing was found in this cell' }, final: { 2: 'Nothing was cut from this cell, so there is no picture to use.' } },
  animation: { can: [4], allowed: [6], undo: [6], why: { 4: 'the loop does not close' }, final: {} },
};
const T = (i, o = {}) => ({ index: i, key: 'k' + i, emoji: 'x', status: 'READY', metrics: { cell: CELL }, review: { still: 'APPROVED', anim: 'NONE' }, anim_status: 'NOT_REQUESTED', anim_report: [], report: [], ...o });
const G = (stickers, allow = ALLOW) => ({ number: 7, generation_id: 'G007', source: { sheet_size: [900, 900] }, video_sheets: [], allow, stickers });

test('the decision of a cell, from the server’s word and the review state', () => {
  const h = load();
  const g = G([]);
  assert.deepEqual(h.cellOp(g, T(1, { status: 'FAILED' }), 'still'), { op: 'allow', kind: 'still' });
  assert.deepEqual(h.cellOp(g, T(3, { status: 'FAILED' }), 'still'), { op: 'unallow', kind: 'still' });
  assert.deepEqual(h.cellOp(g, T(4, { anim_status: 'FAILED' }), 'anim'), { op: 'allow', kind: 'animation' });
  assert.deepEqual(h.cellOp(g, T(6, { anim_status: 'FAILED' }), 'anim'), { op: 'unallow', kind: 'animation' });
  assert.deepEqual(h.cellOp(g, T(8), 'still'), { op: 'drop' }, 'a kept sticker: the click drops it');
  assert.deepEqual(h.cellOp(g, T(8, { review: { still: 'REJECTED', anim: 'NONE' } }), 'still'), { op: 'include' }, 'a dropped one: the click brings it back');
  assert.deepEqual(h.cellOp(g, T(8, { anim_status: 'READY', review: { still: 'APPROVED', anim: 'APPROVED' } }), 'anim'), { op: 'drop' });
  assert.deepEqual(h.cellOp(g, T(8, { anim_status: 'READY', review: { still: 'APPROVED', anim: 'BLOCKED' } }), 'anim'), { op: 'include' }, 'out of bounds is off by default: the click includes it anyway (HANDOFF §3 stays)');
  const none = h.cellOp(g, T(2, { status: 'FAILED' }), 'still');
  assert.equal(none.op, 'none');
  assert.match(none.why, /no picture to use/, 'a cell that cannot be changed says why');
});

test('a sticker that is already animated is decided on the animation, not on the still sheet', () => {
  const h = load();
  const g = G([T(8, { anim_status: 'READY' })]);
  const o = h.cellOp(g, g.stickers[0], 'still');
  assert.equal(o.op, 'none');
  assert.match(o.why, /animat/i);
});

test('NO cell of the sheet opens a tile: every cell is the one cell control, kept or blocked or dropped', () => {
  const h = load();
  const g = G([T(1, { status: 'FAILED' }), T(2, { status: 'FAILED' }), T(3, { status: 'FAILED' }), T(5), T(9, { review: { still: 'REJECTED', anim: 'NONE' } })]);
  const svg = h.issueSvg(g, 'still');
  assert.doesNotMatch(svg, /gopen/, 'the sheet never opens a tile');
  assert.equal((svg.match(/data-act=gcell data-g=7 data-i=\d+ data-stage=still/g) || []).length, 5, 'all five cells carry the same action');
  assert.doesNotMatch(svg, /gsallow|gallow|gdrop|gunallow/, 'no parallel handler');
  assert.match(svg, /data-i=5[^>]*>[\s\S]*?click to drop it from the set/);
  assert.match(svg, /click to bring it back/);
  assert.match(svg, /click to use it anyway/);
  assert.match(svg, /click to take the permission back/);
});

test('the video sheet’s cells are the same control with the animation stage', () => {
  const h = load();
  h.LAY.set('70', { slots: [{ slot: 4, rect: [0, 0, 50, 50] }, { slot: 8, rect: [50, 0, 50, 50] }] });
  const g = G([T(4, { anim_status: 'FAILED' }), T(8, { anim_status: 'READY', review: { still: 'APPROVED', anim: 'APPROVED' } })]);
  g.video_sheets = [{ id: 0, status: 'READY' }];
  const svg = h.issueSvg(g, 'anim');
  assert.doesNotMatch(svg, /gopen/);
  assert.equal((svg.match(/data-act=gcell data-g=7 data-i=\d+ data-stage=anim/g) || []).length, 2);
});

test('the tile’s x / + and its Use it anyway are the same action with the same label as the sheet', () => {
  const h = load();
  const g = G([]);
  const blocked = h.tileHtml(g, T(1, { status: 'FAILED', reason: 'inside_cell' }), 'still');
  assert.equal((blocked.match(/data-act=gcell data-g=7 data-i=1 data-stage=still/g) || []).length >= 2, true, 'the + and the Use it anyway button press the same thing');
  assert.doesNotMatch(blocked, /gsallow|gallow|gdrop|gunallow|gsunallow/);
  assert.match(blocked, /title="Use it anyway"/);
  const kept = h.tileHtml(g, T(8), 'still');
  assert.match(kept, /class=gx data-act=gcell data-g=7 data-i=8 data-stage=still title="Drop this one from the set"/);
  assert.match(kept, /data-act=gopen data-g=7 data-i=8/, 'the thumbnail itself opens the tile');
  const dropped = h.tileHtml(g, T(8, { review: { still: 'REJECTED', anim: 'NONE' } }), 'still');
  assert.match(dropped, /class=gx data-act=gcell[^>]*title="Bring this one back"/);
});

test('taking an animation permission back keeps its finished clip dimmed with the reason over it', () => {
  const h = load();
  const g = G([]);
  const html = h.tileHtml(g, T(4, { anim_status: 'FAILED', anim_reason: 'loop_seam', webm: 'slices/S4.webm', review: { still: 'APPROVED', anim: 'BLOCKED' } }), 'anim');
  assert.match(html, /class="gt [^"]*nx[^"]*"/, 'the existing .gt.nx rule dims the clip');
  assert.match(html, /<video src="\/out\/G007\/slices\/S4\.webm\?v=\d+"/, 'the picture never disappears while the clip is still on disk');
  assert.match(html, /class=gblockover>[\s\S]*the loop does not close/, 'the reason is laid over the clip');
  assert.match(html, /Use it anyway/);
  assert.match(html, /data-act=gcell data-g=7 data-i=4 data-stage=anim/, 'the overlay uses the one cell decision handler');
  assert.doesNotMatch(html, /<b>No animation<\/b>/);
});

test('every entry point sends the same request and the same decision is recorded', async () => {
  const h = load();
  const g = G([T(1, { status: 'FAILED' }), T(2), T(3, { status: 'FAILED' }), T(4, { review: { still: 'REJECTED', anim: 'NONE' } })]);     // the handler finds a sticker by its place: index n is stickers[n-1]
  h.GM.set(7, g);
  const press = (i, stage = 'still') => h.ACT.gcell({ dataset: { g: '7', i: String(i), stage } });
  await press(1);                  // the sheet cell, the tile's +, the Use it anyway button: one handler, so one request
  await press(1);
  assert.deepEqual(h.posts, [['/api/generations/7/allow', { kind: 'still', index: 1, allow: true }], ['/api/generations/7/allow', { kind: 'still', index: 1, allow: true }]]);
  h.posts.length = 0;
  await press(3);
  assert.deepEqual(h.posts, [['/api/generations/7/allow', { kind: 'still', index: 3, allow: false }]], 'an allowed one is taken back');
  h.posts.length = 0;
  await press(2);
  assert.deepEqual(h.posts, [['/api/generations/7/drop', { index: 2, dropped: true }]]);
  h.posts.length = 0;
  await press(4);
  assert.deepEqual(h.posts, [['/api/generations/7/drop', { index: 4, dropped: false }]]);
});

test('a cell that cannot change says why and sends nothing', async () => {
  const h = load();
  const g = G([T(1), T(2, { status: 'FAILED' })]);
  h.GM.set(7, g);
  await h.ACT.gcell({ dataset: { g: '7', i: '2', stage: 'still' } });
  assert.deepEqual(h.posts, []);
  assert.match(h.toasts[0][0], /no picture to use/);
});

test('the old parallel handlers are gone from the file', () => {
  for (const a of ['gdrop', 'gallow', 'gsallow', 'gunallow', 'gsunallow']) assert.doesNotMatch(src, new RegExp(`ACT\\.${a}=`), `ACT.${a} was one of two handlers for the same control`);
  assert.match(src, /ACT\.gcell=/);
});

test('a click never waits for the one before: clicks while a request is on its way go together right after it, and a second click cancels', async () => {
  const gates = [];
  const h = load({ postWait: () => new Promise(r => gates.push(() => r({ ok: true, j: {} }))) });
  const failed = { status: 'FAILED', png: null };
  h.GM.set(7, G([T(1, failed), T(2, failed), T(3, failed), T(4, failed)], { still: { can: [1, 2, 3, 4], allowed: [], undo: [], why: { 1: 'a', 2: 'b', 3: 'c', 4: 'd' }, final: {} }, animation: ALLOW.animation }));
  const click = i => h.ACT.gcell({ dataset: { g: '7', i: String(i), stage: 'still' } });
  const first = click(1);
  assert.deepEqual(h.posts.map(p => p[1]), [{ kind: 'still', index: 1, allow: true }], 'the first click is sent at once, as always');
  await click(2); await click(4); await click(3); await click(3);           // 3 twice: cancelled before it was sent
  assert.equal(h.posts.length, 1, 'nothing else is sent while the first is on its way, and nothing waits for it either');
  gates.shift()();
  await new Promise(r => setImmediate(r));
  assert.deepEqual(h.posts[1][1], { kind: 'still', indexes: [2, 4], allow: true }, 'the waiting clicks go as ONE request the moment the first has answered');
  gates.shift()();
  await first;
  assert.equal(h.posts.length, 2);
});
