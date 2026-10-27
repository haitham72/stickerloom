const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// The model drop-downs folded into the control row (live.js modelPick) carry a marker with no value (<select data-lvvid>, dataset.lvvid === "").
// A change must still be saved as the next model: before 2026-10-09 an empty marker read as false, so the pick showed but the job went out with the old model.
// The change handler is read out of live.js and run with the few globals stubbed. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const live = readConsoleFile(path.join(__dirname, '..', '..', 'stickerloom', 'console', 'live.js'), 'utf8');
function statement(marker) {return consoleStatement(live,marker);}

function load() {
  const log = { saved: 0, ticks: 0 };
  let handler = null;
  const sb = {
    log, LIVE: { vid: { id: 'kling3_0', options: {} }, img: { id: 'nano_banana_flash', options: {} } }, glast: 'x',
    document: { addEventListener: (ev, fn) => { if (ev === 'change') handler = fn; } },
    lsave: () => { log.saved++; }, lsel: () => {}, lcost: async () => {}, fillPrices: () => {}, tick: () => { log.ticks++; },
  };
  new Function(...Object.keys(sb), statement("document.addEventListener('change',e=>{const t=e.target;if(t.dataset&&t.dataset.lvvid"))(...Object.values(sb));
  return { ...sb, change: target => handler({ target }) };
}

test('picking a video model in the control row is saved even though its marker has no value', () => {
  const h = load();
  h.change({ dataset: { lvvid: '' }, value: 'grok_video_v15_lite' });
  assert.equal(h.LIVE.vid.id, 'grok_video_v15_lite');
  assert.equal(h.log.saved, 1);
});

test('the same for the image model', () => {
  const h = load();
  h.change({ dataset: { lvimg: '' }, value: 'seedream_v4' });
  assert.equal(h.LIVE.img.id, 'seedream_v4');
});

test('another select is not taken for a model pick', () => {
  const h = load();
  h.change({ dataset: {}, value: 'x' });
  assert.equal(h.LIVE.vid.id, 'kling3_0');
  assert.equal(h.LIVE.img.id, 'nano_banana_flash');
});
