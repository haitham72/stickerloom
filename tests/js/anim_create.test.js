const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// The Animation tab before any animation exists: the video sheet preview, the sheet panel
// (with the model, price and Generate), the editable video prompt with the Prompt tab's footer, and each kept sticker with motion suggestions that add to that prompt.
// The statements are read out of generate.js and run with the few globals stubbed. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const gen = readConsoleFile(path.join(__dirname, '..', '..', 'stickerloom', 'console', 'generate.js'), 'utf8');
function statement(marker) {return consoleStatement(gen,marker);}

function load({ live = true } = {}) {
  const log = { ticks: 0 };
  const G = { number: 104, generation_id: 'G104', stickers: [
    { index: 1, key: 'victory_stance', png: 'slices/S1.png', status: 'READY', review: {} },
    { index: 2, key: 'jump', png: 'slices/S2.png', status: 'READY', review: { still: 'REJECTED' } }] };
  const PD = {};
  const sb = {
    log, PD, GM: new Map([[104, G]]), glast: 'x',
    esc: s => String(s == null ? '' : s), ic: n => `<svg data-i=${n}></svg>`,
    keptStills: g => g.stickers.filter(t => t.status === 'READY' && t.review.still !== 'REJECTED'),
    liveReadyNow: () => live, previewUrl: (g, f) => `/api/generations/${g.number}/sheet_preview?fill=${f}`, fillNow: () => 0.5,
    sheetPanel: (g, mode, opt) => `<sheetpanel ${g.generation_id} ${mode} ${opt && opt.noPrompt ? 'noprompt' : ''}>`,
    pdKey: (g, k) => `${g}:${k}`, pdText: (g, k) => PD[`${g.number}:${k}`] !== undefined ? PD[`${g.number}:${k}`] : 'Animate this sheet.',
    pdFoot: (g, k) => `<pdfoot ${k}>`,
    copyBox: (title, text, id, rows, edit) => `<box ${title}|${id}|${edit.kind}>${text}</box>${edit.foot}`,
    tick: () => { log.ticks++; },
  };
  const body = ['const ANIM_MOTIONS=', 'function animCreate(', 'ACT.ganmo='].map(statement).join('\n');
  const f = new Function(...Object.keys(sb), 'const ACT={};\n' + body + '\nreturn {ACT,animCreate};')(...Object.values(sb));
  return { ...f, ...sb, G };
}

test('the Animation tab shows the whole create flow: the frame (To send, Generate), the editable video prompt and each kept sticker', () => {
  const h = load();
  const html = h.animCreate([h.G]);
  assert.match(html, /1 kept sticker to animate/);
  assert.match(html, /<sheetpanel G104 anim noprompt>/, 'the one frame (its To send view and its control row: model, price, Generate); the prompt is beside it, not toggled');
  assert.doesNotMatch(html, /sheet_preview/, 'no second preview: the frame has it');
  assert.match(html, /<box Video prompt\|ap104\|video>Animate this sheet\.<\/box><div class=pdfoot><button class="btn sm" data-act=pgreset data-g=104 data-kind=video>Reset/, 'the prompt with Reset only: Generate is under the frame');
  assert.equal((html.match(/class=ganew-st>/g) || []).length, 1, 'only the kept stickers, not the rejected one');
  assert.match(html, /S1 · victory stance/);
  assert.match(html, /data-act=ganmo data-g=104 data-m="S1 waves">waves</);
  assert.doesNotMatch(html, /data-act=gvideo/, 'connected: no "Make a video…" detour');
  assert.match(load({ live: false }).animCreate([h.G]), /data-act=gvideo data-g=104 /, 'not connected: the way to bring your own video');
});

test('a motion suggestion adds one line to the video prompt draft and redraws', () => {
  const h = load();
  h.ACT.ganmo({ dataset: { g: '104', m: 'S1 waves' } });
  assert.equal(h.PD['104:video'], 'Animate this sheet.\nS1 waves.');
  h.ACT.ganmo({ dataset: { g: '104', m: 'S1 jumps' } });
  assert.equal(h.PD['104:video'], 'Animate this sheet.\nS1 waves.\nS1 jumps.');
  assert.equal(h.log.ticks, 2);
});
