const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// The chat's tiles and the creator's card carry the override (2026-10-03): a blocked tile shows its picture (never dimmed), the locked red marks
// (hatched while it is not in the set, solid with a check once allowed by the person), its reason in plain words, and the allow button on the tile itself,
// per kind. The creator's stop card gets the same bulk pair the Studio has, per kind, counting what is allow-able now. The statements are read out of
// agent.js and run with the few globals they use stubbed. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const DIR = path.join(__dirname, '..', '..', 'stickerloom', 'console');
const src = readConsoleFile(path.join(DIR, 'agent.js'), 'utf8');
const css = readConsoleFile(path.join(DIR, 'agent.css'), 'utf8');

/** One whole top-level statement of agent.js: from `marker` to the next line that starts in column 0. */
function statement(marker) {return consoleStatement(src,marker);}

function load(extra = '') {
  const A = { sel: new Set() };
  const sandbox = {
    AIU: { esc: s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])) },
    ic: n => `<svg data-i=${n}></svg>`,
    A,
  };
  const body = ['const humanWhy=', 'function tileHTML(', 'function carHTML(', 'function runAllowRow(', 'function runHTML('].map(statement).join('\n');
  const f = new Function(...Object.keys(sandbox), extra + '\n' + body + '\nreturn {tileHTML,carHTML,runHTML,runAllowRow};')(...Object.values(sandbox));
  return { ...f, A };
}

const ALLOW = {
  still: { can: [1], allowed: [3], undo: [3], why: { 1: 'the character touches the edge of its cell', 2: 'almost nothing was found in this cell' }, final: { 2: 'Nothing was cut from this cell, so there is no picture to use.' } },
  animation: { can: [4], allowed: [], undo: [], why: { 4: 'the loop does not close' }, final: {} },
};
const ST = (i, o = {}) => ({ id: `G012/S${i}`, index: i, key: 'cape', emoji: '🦸', status: 'READY', still: 'PENDING', png: '/out/G012/slices/S1.png', ...o });

test('a blocked still that may be allowed shows its picture, the red marks, the reason and the button', () => {
  const h = load();
  const t = h.tileHTML(ST(1, { status: 'FAILED', reason: 'inside_cell' }), 'G012', ALLOW);
  assert.match(t, /<img src="\/out\/G012\/slices\/S1\.png"/, 'the picture stays');
  assert.match(t, /is-bad/);
  assert.match(t, /ag-hatch/);
  assert.match(t, /the character touches the edge of its cell/);
  assert.doesNotMatch(t, /inside_cell/, 'never a raw check id');
  assert.match(t, /data-act=agallow data-g="G012" data-i=1 data-kind=still data-allow=1/);
  assert.match(t, />Use it anyway</);
});

test('a still that cannot be allowed has no button and says why in plain words', () => {
  const h = load();
  const t = h.tileHTML(ST(2, { status: 'FAILED', reason: 'empty_subject' }), 'G012', ALLOW);
  assert.match(t, /almost nothing was found/);
  assert.match(t, /no picture to use/);
  assert.doesNotMatch(t, /data-act=agallow/);
});

test('an allowed sticker reads "allowed by you", solid with a check, and offers taking it back', () => {
  const h = load();
  const t = h.tileHTML(ST(3, { status: 'FAILED', reason: 'inside_cell' }), 'G012', ALLOW);
  assert.match(t, /is-allowed/);
  assert.match(t, /class="ag-hatch is-on"/, 'solid, not hatched');
  assert.match(t, /title="Allowed by you"/);
  assert.match(t, /allowed by you/);
  assert.match(t, /data-kind=still data-allow=0>Take it back</);
  assert.doesNotMatch(t, /Use it anyway/);
});

test('a blocked animation gets the animation button, a rejected one without allow info says its reason', () => {
  const h = load();
  const t = h.tileHTML(ST(4, { anim_status: 'FAILED', anim_reason: 'loop_seam' }), 'G012', ALLOW);
  assert.match(t, /data-kind=animation data-allow=1/);
  assert.match(t, /the loop does not close/);
  const bare = h.tileHTML(ST(4, { anim_status: 'FAILED', anim_reason: 'loop_seam' }), 'G012', null);
  assert.match(bare, /is-bad/);
  assert.match(bare, /loop seam/, 'the raw id is humanised until the allow block arrives');
  assert.doesNotMatch(bare, /data-act=agallow/, 'no button without the server’s word');
});

test('the carousel threads the allow block into every tile', () => {
  const h = load();
  const c = h.carHTML([ST(1, { status: 'FAILED', reason: 'inside_cell' }), ST(2)], 'G012', ALLOW);
  assert.match(c, /data-kind=still data-allow=1/);
  assert.equal((c.match(/ag-why/g) || []).length, 1, 'only the blocked tile explains itself');
});

test('the creator’s card carries the bulk pair per kind, counting what is allow-able now', () => {
  const h = load();
  const run = { subject: 'Barbie', scope: 'video', bypass: false, status: 'stopped', generation: 'G012', steps: [], stop: { why: 'Python blocked S1.' } };
  const r = h.runHTML(run, ALLOW);
  assert.match(r, /Python blocked S1\./, 'what it stopped for stays');
  assert.match(r, /data-act=agallowall data-g="G012" data-kind=still data-allow=1/);
  assert.match(r, /Use all anyway \(1\)/);
  assert.match(r, /data-kind=still data-allow=0/);
  assert.match(r, /Take all back \(1\)/);
  assert.match(r, /data-kind=animation data-allow=1/);
  assert.match(r, /or allow one sticker below/);
  const bare = load().runHTML({ ...run });
  assert.doesNotMatch(bare, /agallowall/, 'no allow block on the card: no pair, and the stop still says why');
  assert.match(bare, /Python blocked S1\./);
});

test('a verdict never dims a picture, and the red marks are the locked ones', () => {  assert.doesNotMatch(css, /\.ag-tile\.is-bad \.ag-ph\{opacity/, 'no dimming rule for a rejected tile');
  assert.match(css, /\.ag-tile\.is-bad \.ag-ph\{box-shadow:inset 0 0 0 2px #ef4444\}/, 'blocked wears the locked red');
  assert.match(css, /\.ag-tile \.ag-hatch\{[^}]*repeating-linear-gradient/, 'hatched while not in the set');
  assert.match(css, /\.ag-tile\.is-allowed \.ag-hatch\{background:rgba\(239,68,68/, 'solid once allowed by the person');
});

test('every new chat action has a handler', () => {
  for (const act of ['agallow', 'agallowall']) assert.match(src, new RegExp(`ACT\\.${act}=`), `${act} has a handler`);
});

test('no bulk pair when nothing is allow-able, and none on a finished run', () => {
  const h = load();
  const none = { still: { can: [], allowed: [], undo: [], why: {}, final: {} }, animation: { can: [], allowed: [], undo: [], why: {}, final: {} } };
  const run = { subject: 'Barbie', scope: 'video', bypass: false, status: 'stopped', generation: 'G012', steps: [], stop: { why: 'Python blocked S1.' } };
  assert.doesNotMatch(h.runHTML(run, none), /run-bulk/);
  assert.doesNotMatch(h.runHTML({ ...run, status: 'done' }, ALLOW), /run-bulk/);
});

test('the chat reads the allow block from the card’s own data: no second request, no cache, no polling of the batch', () => {
  assert.doesNotMatch(src, /allowCache|syncCardAllow|agAllowRev/, 'the hydration fetch is gone');
  assert.match(src, /\(c\.data&&c\.data\.allow\)\|\|null/, 'the tile gets the block from the card data');
  assert.match(src, /c\.data&&c\.data\.allow,c\.run&&/, 'a change of the block repaints the message');
  assert.doesNotMatch(src, /api\('\/api\/generations\/'/, 'the chat does not fetch the batch itself');
});
