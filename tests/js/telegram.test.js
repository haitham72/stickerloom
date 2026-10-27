// The send queue's watcher polls GET /api/telegram/sends/{tid} until the send is done or failed, then toasts. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const cs = require('./console_source');
globalThis.route_ = 'settings';                       // away from the pack: every outcome toasts, no dialog is touched
globalThis.PACK_ID = 'p1';
const toasts = [];
globalThis.toast = (m) => toasts.push(String(m));
let states = [];
globalThis.api = async (u) => {
  assert.match(u, /\/api\/telegram\/sends\/T1/);
  return { ok: true, j: states.length > 1 ? states.shift() : states[0] };
};
const timers = [];
globalThis.setTimeout = (fn) => { timers.push(fn); return timers.length; };
globalThis.ACT = {};                                      // the script registers its actions at load
const { tgWatch } = require(cs.resolveConsoleFile(path.join(__dirname, '../../stickerloom/console/telegram.js')));
async function drain() { while (timers.length) await timers.shift()(); }

test('a queued send is polled until done, then toasted when away from the pack', async () => {
  states = [{ state: 'queued' }, { state: 'sending' }, { state: 'done', result: { sets: [{ added: 2, total: 2 }] } }];
  toasts.length = 0; timers.length = 0;
  await tgWatch('T1', 'p1', 0);
  await drain();
  assert.equal(toasts.length, 1);
  assert.match(toasts[0], /Sent 2 stickers to Telegram/);
});

test('a failed send toasts its error', async () => {
  states = [{ state: 'failed', error: 'boom' }];
  toasts.length = 0; timers.length = 0;
  await tgWatch('T1', 'p1', 0);
  assert.equal(timers.length, 0);
  assert.equal(toasts.length, 1);
  assert.match(toasts[0], /Send to Telegram failed: boom/);
});

test('polling stops after 720 tries', async () => {
  states = [{ state: 'sending' }];
  toasts.length = 0; timers.length = 0;
  await tgWatch('T1', 'p1', 720);
  await drain();
  assert.equal(toasts.length, 1);
  assert.match(toasts[0], /status unknown/);
});
