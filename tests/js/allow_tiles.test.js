const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// "Use it anyway" on a blocked tile (2026-10-03): the server says what may be allowed (`g.allow`, flow/gates.py allow_info), the page only draws it.
// The helpers are read out of generate.js and run with the few globals they use stubbed. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const FILE = path.join(__dirname, '..', '..', 'stickerloom', 'console', 'generate.js');
const src = readConsoleFile(FILE, 'utf8');

function statement(marker) {return consoleStatement(src,marker);}

function load() {
  const sandbox = { esc: s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])) };
  const body = ['const ANIMWHY=', 'const ALW=', 'const whyOf=', 'const canAllow=', 'const hasAllowed=', 'const clickAllow=', 'const cellAct=', 'function blockedBox(g,t,kind)'].map(statement).join('\n');
  return new Function(...Object.keys(sandbox), body + '\nreturn {ALW,whyOf,canAllow,hasAllowed,clickAllow,blockedBox};')(...Object.values(sandbox));
}

const ALLOW = {
  still: { can: [1], allowed: [3], undo: [3], why: { 1: 'the character touches the edge of its cell, so it may be cut off', 2: 'almost nothing was found in this cell' }, final: { 2: 'Nothing was cut from this cell (it is empty), so there is no picture to use.' } },
  animation: { can: [4], allowed: [], undo: [], why: { 4: 'the loop does not close: it jumps when it starts again', 5: 'the file is over Telegram\'s size limit for an animation' }, final: { 5: 'This one failed a technical check (size_budget: format, size or codec) and cannot be allowed.' } },
};
const batch = (allow = ALLOW) => ({ number: 7, allow, stickers: [] });

test('a blocked still that may be allowed says why in plain words and offers the button', () => {
  const h = load();
  const html = h.blockedBox(batch(), { index: 1, reason: 'inside_cell', status: 'FAILED' }, 'still');
  assert.match(html, /the character touches the edge of its cell/);
  assert.match(html, /data-act=gcell data-g=7 data-i=1 data-stage=still/);
  assert.match(html, />Use it anyway</);
});

test('a still that cannot be allowed has no button and says why', () => {
  const h = load();
  const html = h.blockedBox(batch(), { index: 2, reason: 'empty_subject', status: 'FAILED' }, 'still');
  assert.match(html, /almost nothing was found/);
  assert.doesNotMatch(html, /data-act=/, 'no button where nothing can be allowed');
  assert.match(html, /no picture to use/);
});

test('an animation uses the animation action and a technical block stays final', () => {
  const h = load();
  const can = h.blockedBox(batch(), { index: 4, anim_reason: 'loop_seam', anim_status: 'FAILED' }, 'animation');
  assert.match(can, /No animation/);
  assert.match(can, /data-act=gcell data-g=7 data-i=4 data-stage=anim/);
  const final = h.blockedBox(batch(), { index: 5, anim_reason: 'size_budget', anim_status: 'FAILED' }, 'animation');
  assert.doesNotMatch(final, /data-act=/);
  assert.match(final, /cannot be allowed/);
});

test('a server without the allow block (older code) offers nothing and falls back to the reason', () => {
  const h = load();
  const html = h.blockedBox({ number: 7, stickers: [] }, { index: 1, reason: 'inside_cell', status: 'FAILED' }, 'still');
  assert.doesNotMatch(html, /data-act=/);
  assert.match(html, /inside_cell/);
  const anim = h.blockedBox({ number: 7 }, { index: 1, anim_reason: 'loop_seam', anim_status: 'FAILED' }, 'animation');
  assert.match(anim, /the loop does not close|loop_seam/);
});

test('allowed, can-allow and take-back are read from the server per kind', () => {
  const h = load();
  const g = batch();
  assert.equal(h.canAllow(g, { index: 1 }, 'still'), true);
  assert.equal(h.canAllow(g, { index: 1 }), false, 'the default kind is the animation');
  assert.equal(h.hasAllowed(g, { index: 3 }, 'still'), true);
  assert.equal(h.clickAllow(g, { index: 4 }), true);
  assert.equal(h.clickAllow(g, { index: 9 }), false);
  assert.equal(h.whyOf(g, { index: 4 }, 'animation'), 'the loop does not close: it jumps when it starts again');
  assert.equal(h.whyOf({}, { index: 4 }, 'animation'), undefined);
});

test('the tiles use the box and the handlers exist', () => {
  assert.match(src, /blockedBox\(g,t,'still'\)/, 'a blocked still tile explains itself and offers the click');
  assert.match(src, /blockedBox\(g,t,'animation'\)/);
  assert.match(src, /ACT\.gcell=/, 'the one handler of every control on a cell');
  for (const act of ['gsallow', 'gsunallow', 'gallow', 'gunallow', 'gdrop']) assert.doesNotMatch(src, new RegExp(`ACT\\.${act}=`), `${act} was a parallel handler of the same control`);
  assert.match(src, /Take it back/);
  assert.doesNotMatch(src, /OVERRIDABLE/, 'the list of what may be allowed lives on the server only');
});
