const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// The Prompt tab writes the prompts (2026-10-02): drafts per batch and kind, "with my prompt" only when the text really differs from the template, the buttons' conditions.
// The helpers are read out of generate.js and run with the few globals they use stubbed. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const FILE = path.join(__dirname, '..', '..', 'stickerloom', 'console', 'generate.js');
const src = readConsoleFile(FILE, 'utf8');

function statement(marker) {return consoleStatement(src,marker);}

function load({ live = true } = {}) {
  const sandbox = {
    esc: s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    liveReadyNow: () => live,
    keptStills: g => g.stickers.filter(t => t.status === 'READY'),
  };
  const body = ['const making=', 'const processing=', 'const PD={}', 'const pdKey=', 'const sentVideoPrompt=', 'const pdText=', 'const pdBase=', 'const pdCustom=', 'function pdFoot(g,kind)', 'function redoFoot(', 'function resheetFoot('].map(statement).join('\n');
  const api = new Function(...Object.keys(sandbox), body + '\nreturn {PD,pdKey,pdText,pdCustom,pdFoot,sentVideoPrompt};')(...Object.values(sandbox));
  return { ...api };
}

const batch = (extra = {}) => ({ number: 5, sheet_prompt: 'SHEET TEMPLATE', video_prompt: 'VIDEO TEMPLATE', video_sheets: [], stickers: [{ status: 'READY' }, { status: 'READY' }], ...extra });

test('without a draft the box shows the template and nothing is custom', () => {
  const h = load();
  const g = batch();
  assert.equal(h.pdText(g, 'sheet'), 'SHEET TEMPLATE');
  assert.equal(h.pdCustom(g, 'sheet'), false);
  assert.match(h.pdFoot(g, 'sheet'), /data-act=pgresheet[^>]*>Regenerate<span/, 'one plain Regenerate');
  assert.doesNotMatch(h.pdFoot(g, 'sheet'), /data-act=pgsheet|A new batch/, 'a new batch is Next batch, not a second choice here');
  assert.match(h.pdFoot(g, 'sheet'), /data-act=pgreset[^>]* hidden/, 'nothing to reset');
});

test('a changed text is custom, an identical one or an empty one is not', () => {
  const h = load();
  const g = batch();
  h.PD[h.pdKey(5, 'sheet')] = 'My own wording';
  assert.equal(h.pdCustom(g, 'sheet'), true);
  assert.doesNotMatch(h.pdFoot(g, 'sheet'), /data-act=pgreset[^>]* hidden/, 'a draft can be reset');
  h.PD[h.pdKey(5, 'sheet')] = 'SHEET TEMPLATE';
  assert.equal(h.pdCustom(g, 'sheet'), false, 'typing the template back is the template');
  h.PD[h.pdKey(5, 'sheet')] = '   ';
  assert.equal(h.pdCustom(g, 'sheet'), false, 'an empty box is never sent as a prompt');
});

test('drafts are per batch and per kind', () => {
  const h = load();
  h.PD[h.pdKey(5, 'sheet')] = 'mine';
  assert.equal(h.pdText(batch({ number: 6 }), 'sheet'), 'SHEET TEMPLATE');
  assert.equal(h.pdText(batch(), 'video'), 'VIDEO TEMPLATE');
});

test('the video box shows the prompt that was actually sent once there is one', () => {
  const h = load();
  const g = batch({ video_sheets: [{ id: 'A1', status: 'SLICED', video_prompt_sent: 'WHAT WAS SENT' }] });
  assert.equal(h.pdText(g, 'video'), 'WHAT WAS SENT');
  assert.doesNotMatch(h.pdFoot(g, 'video'), /pgvideo/, 'a sliced sheet is never animated twice');
  assert.match(h.pdFoot(g, 'video'), /data-act=pgredo data-g=5[^>]*>Regenerate</, 'it is regenerated in the same batch instead');
  assert.doesNotMatch(h.pdFoot(g, 'video'), /pgredo[^>]* disabled/);
});

test('regenerating waits while a returned video is being cut, and a retired sheet still counts as sent', () => {
  const h = load();
  assert.match(h.pdFoot(batch({ video_sheets: [{ id: 'A1', status: 'SUPERSEDED' }, { id: 'A2', status: 'VIDEO_RETURNED' }] }), 'video'), /pgredo[^>]* disabled/);
  assert.doesNotMatch(h.pdFoot(batch({ video_sheets: [{ id: 'A1', status: 'SUPERSEDED' }, { id: 'A2', status: 'APPROVED' }] }), 'video'), /pgredo[^>]* disabled/, 'a failed redo can be tried again');
});

test('the buttons are off without Higgsfield and the video needs a kept sticker', () => {
  const off = load({ live: false });
  assert.match(off.pdFoot(batch(), 'sheet'), /pgresheet[^>]* disabled/);
  assert.match(off.pdFoot(batch(), 'video'), /pgvideo[^>]* disabled/);
  const on = load();
  assert.doesNotMatch(on.pdFoot(batch(), 'sheet'), /pgresheet[^>]* disabled/);
  assert.doesNotMatch(on.pdFoot(batch(), 'video'), /pgvideo[^>]* disabled/);
  assert.match(on.pdFoot(batch({ stickers: [{ status: 'FAILED' }] }), 'video'), /pgvideo[^>]* disabled/);
});
