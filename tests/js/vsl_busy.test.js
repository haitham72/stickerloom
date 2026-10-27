// Slider/frame-editor sends wait out a 409 busy and retry. Runs in a vm with stubbed timers/server. Run: node --test tests/js
const test = require('node:test'), assert = require('node:assert/strict'), path = require('node:path'), vm = require('node:vm');
const { readRawConsoleFile } = require('./console_source');
const source = readRawConsoleFile(path.join(__dirname, '../../stickerloom/console/studio-core.js'), 'utf8');
const controllers = source.slice(source.indexOf('/* Motion size'), source.indexOf("/* the batch's Download .zip"));
function setup() {
  const writes = [], timers = new Map(), events = {}; let serial = 0, reply = async () => ({ ok: true, j: {} });
  const sticker = { index: 1, anim_status: 'READY' }, batch = { number: 7, anim_scale: 1, stickers: [sticker] };
  const ctx = vm.createContext({ ACT: {}, GM: new Map([[7, batch]]), glast: '', tick() {}, toast() {}, localStorage: { getItem() { return null }, setItem() {} }, document: { getElementById() { return null } },
    addEventListener(n, f) { (events[n] ??= []).push(f) }, setTimeout(fn, ms) { const id = ++serial; timers.set(id, { fn, ms }); return id }, clearTimeout(id) { timers.delete(id) }, post: async (url, body) => { writes.push([url, JSON.parse(JSON.stringify(body))]); return reply(url, body) } });
  vm.runInContext(controllers, ctx);
  const values = { scale: { value: '100' }, dx: { value: '0' }, dy: { value: '0' } };
  const box = { dataset: { g: '7', i: '1' }, querySelector: s => values[s.match(/data-k=(\w+)/)[1]] };
  const input = { tagName: 'INPUT', dataset: { k: 'scale' }, matches() { return true }, closest() { return box }, parentElement: { querySelector() { return { textContent: '' } } }, get value() { return values.scale.value } };
  return { ctx, writes, timers, run: s => vm.runInContext(s, ctx), reply: f => reply = f,
    input(v) { values.scale.value = String(v); events.input.forEach(f => f({ target: input })) }, release() { events.change.forEach(f => f({ target: input })) },
    fire() { const [id, t] = timers.entries().next().value; timers.delete(id); return t.fn() } };
}
const tick = () => new Promise(r => setImmediate(r));
test('a 409 busy waits and sends once more instead of erroring', async () => {
  const h = setup(); let n = 0;
  h.reply(() => (++n === 1) ? { ok: false, status: 409, j: { error: 'busy: a job is running, wait for it to finish' } } : { ok: true, j: {} });
  h.input(110); h.release();
  const p = h.fire(); await tick(); await tick();
  assert.equal(h.writes.length, 1, 'first attempt refused');
  h.fire(); await tick(); await tick(); await p;
  assert.equal(h.writes.length, 2, 'retried once it cleared');
  assert.deepEqual(h.writes[1][1], { index: 1, offset: null, scale: 1.1, reslice: true });
  assert.equal(h.run('vslBusy.size'), 0);
});
test('anything but 409 is final: no retry', async () => {
  const h = setup();
  h.reply(async () => ({ ok: false, status: 400, j: { error: 'bad' } }));
  h.input(110); h.release();
  await h.fire(); await tick(); await tick();
  assert.equal(h.writes.length, 1);
});
