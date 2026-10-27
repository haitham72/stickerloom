const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// The animations row in the frame's Video view: animation 1 … n = the generation's video sheets that have a video, ★ = the one in use,
// a click on another picks it (POST …/pick_video), x takes one out (POST …/remove_video, never the one in use), Report is a ticket on G###/A#.
// The statements are read out of generate.js and run with the few globals stubbed. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const gen = readConsoleFile(path.join(__dirname, '..', '..', 'stickerloom', 'console', 'generate.js'), 'utf8');
function statement(marker) {return consoleStatement(gen,marker);}

function load(sheets, { busy = false } = {}) {
  const calls = [];
  const G = { number: 104, generation_id: 'G104', source: {}, video_sheets: sheets, stickers: [{ index: 1, anim_status: busy ? 'PROCESSING' : 'READY' }] };
  const sb = {
    calls, glast: 'x', GM: new Map([[104, G]]),
    esc: s => String(s == null ? '' : s), ic: n => `<svg data-i=${n}></svg>`,
    processing: g => g.stickers.some(t => t.anim_status === 'PROCESSING'),
    sheetOf: g => [...g.video_sheets].reverse().find(v => v.status !== 'REJECTED'),
    post: async (u, b) => { calls.push(['post', u, b]); return { ok: true, j: { removed: b.sheet } }; },
    postWait: async (u, b) => { calls.push(['postWait', u, b]); return { ok: true, j: { changed: true } }; },
    toast: m => calls.push(['toast', m]), tick: () => calls.push(['tick']),
    confirmDlg: (msg, cb) => { calls.push(['confirm', msg]); return cb(); },
  };
  const body = ['const cutOf=', 'const animsOf=', 'function animRow(', 'ACT.gapick=', 'ACT.garm='].map(statement).join('\n');
  const f = new Function(...Object.keys(sb), 'const ACT={};\n' + body + '\nreturn {ACT,animRow};')(...Object.values(sb));
  return { ...f, ...sb, G };
}

const SHEETS = [
  { id: 'A1', status: 'SUPERSEDED', video: 'video_sheet/A1/video.mp4', model: 'kling3_0' },
  { id: 'A2', status: 'REJECTED', video: 'video_sheet/A2/video.mp4' },
  { id: 'A3', status: 'SLICED', video: 'video_sheet/A3/video.mp4', model: 'grok_video_v15_lite' },
  { id: 'A4', status: 'BUILT', video: null },
];

test('one chip per video sheet with a video, the one in use starred, a removed one gone', () => {
  const { animRow, G } = load(SHEETS);
  const h = animRow(G);
  assert.match(h, /animation 1/);
  assert.match(h, /★ animation 3/);
  assert.doesNotMatch(h, /animation 2/);                      // removed (REJECTED): out of the row
  assert.doesNotMatch(h, /animation 4/);                      // no video yet
  assert.match(h, /data-act=gapick data-g=104 data-a=A1/);
  assert.doesNotMatch(h, /data-act=gapick data-g=104 data-a=A3/); // the one in use is not a pick
  assert.match(h, /data-act=garm data-g=104 data-a=A1/);
  assert.doesNotMatch(h, /data-act=garm data-g=104 data-a=A3/);  // never remove the one in use
});

test('the star follows the picked (SLICED) animation, not the newest earlier one', () => {
  const { animRow, G } = load([
    { id: 'A1', status: 'SUPERSEDED', video: 'v1' }, { id: 'A2', status: 'SLICED', video: 'v2' },
    { id: 'A3', status: 'SUPERSEDED', video: 'v3' }, { id: 'A4', status: 'APPROVED', video: null }]);
  const h = animRow(G);
  assert.match(h, /★ animation 2/);
  assert.doesNotMatch(h, /★ animation 3/);
  assert.match(h, /data-act=gapick data-g=104 data-a=A3/);
});

test('nothing to show without an animation, and a prepared video has no row', () => {
  assert.equal(load([{ id: 'A1', status: 'BUILT', video: null }]).animRow(load([]).G), '');
  const p = load(SHEETS); p.G.source.video_path = 'x.mp4';
  assert.equal(p.animRow(p.G), '');
});

test('while a video is being cut, pick and remove wait', () => {
  const { animRow, G } = load(SHEETS, { busy: true });
  assert.match(animRow(G), /data-a=A1 disabled/);
});

test('pick and remove call the routes with the sheet id', async () => {
  const { ACT, calls } = load(SHEETS);
  await ACT.gapick({ dataset: { g: '104', a: 'A1' } });
  assert.deepEqual(calls[0], ['postWait', '/api/generations/104/pick_video', { sheet: 'A1' }]);
  await ACT.garm({ dataset: { g: '104', a: 'A1' } });
  assert.ok(calls.some(c => c[0] === 'post' && c[1] === '/api/generations/104/remove_video' && c[2].sheet === 'A1'));
});
