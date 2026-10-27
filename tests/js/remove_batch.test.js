const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// Remove batch (2026-10-03, "like remove pack"): the button in the batch header, the confirm text that says what happens before it happens, the request it sends, what the page forgets
// afterwards, and the Removed batches list with Restore under Earlier batches. The statements are read out of generate.js / live.js and run with the few globals stubbed.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = f => readConsoleFile(path.join(__dirname, '..', '..', 'stickerloom', 'console', f), 'utf8');
const gen = read('generate.js'), live = read('live.js');

function statement(src,marker) {return consoleStatement(src,marker);}

function load(over = {}) {
  const log = { posts: [], toasts: [], dialogs: [], saved: 0, ticks: 0, hist: 0 };
  const GM = new Map([[7, { number: 7, generation_id: 'G007' }], [8, { number: 8, generation_id: 'G008' }]]);
  const sb = {
    log, GM, HB: { items: [{ id: 7 }, { id: 8 }, { id: 3 }] }, SES: { gens: [7, 8], off: [], prompt: '', pack: '' }, glast: 'x',
    esc: s => String(s == null ? '' : s), ago: () => 'just now',
    sessionGens: () => [GM.get(7), GM.get(8)].filter(Boolean),
    confirmDlg: (msg, cb, ok) => { log.dialogs.push([msg, ok]); log.pending = cb(); },       // the page runs the callback when the person confirms: the test awaits it through log.pending
    post: async (url, body) => { log.posts.push([url, body]); return over.post ? over.post(url) : { ok: true, j: {} }; },
    api: async url => over.api ? over.api(url) : { ok: true, j: { batches: [] } },
    toast: (m, bad) => log.toasts.push([m, !!bad]),
    saveSes: () => { log.saved++; }, tick: () => { log.ticks++; }, histLoad: () => { log.hist++; },
    document: { getElementById: id => (id === 'rem-typed' ? over.typed : over.el) || null },
    dlg: html => log.dialogs.push([html, 'dlg']), closeDlg: () => { log.closed = (log.closed || 0) + 1; },
  };
  const body = [statement(gen, 'const grmText='), statement(gen, 'ACT.grm='), statement(live, 'const REM='), statement(live, 'async function remLoad('),
    statement(live, 'function remDraw('), statement(live, 'function remHtml('), statement(live, 'async function remPurge('), statement(live, 'ACT.gpurge='),
    statement(live, 'ACT.gpurgeall='), statement(live, 'ACT.gpurgeallgo='), statement(live, 'ACT.grestore=')].join('\n');
  const f = new Function(...Object.keys(sb), 'const ACT={};\n' + body + '\nreturn {ACT,REM,remDraw,remLoad,remHtml,grmText};')(...Object.values(sb));
  return { ...f, ...sb };
}

test('the header carries a Remove batch button wired to ACT.grm, and the confirm says what happens first', () => {
  assert.match(gen, /class="btn dng" data-act=grm[^>]*>\$\{ic\('trash'\)\} Remove batch<\/button>/);
  const h = load();
  const text = h.grmText([{ generation_id: 'G007' }]);
  assert.match(text, /^Remove G007\? It moves to the trash, nothing is deleted/);
  assert.match(text, /Removed batches/);
  assert.match(text, /stay in their packs/);
  assert.match(h.grmText([{ generation_id: 'G007' }, { generation_id: 'G008' }]), /^Remove G007, G008\? They move/);
});

test('confirming removes every shown batch through the route and the page forgets them', async () => {
  const h = load();
  h.ACT.grm(); await h.log.pending;
  assert.deepEqual(h.log.dialogs.map(d => d[1]), ['Remove'], 'the dialog button says Remove, not Delete');
  assert.deepEqual(h.log.posts, [['/api/generations/7/remove', {}], ['/api/generations/8/remove', {}]]);
  assert.deepEqual(h.SES.gens, [], 'the Studio no longer presents them');
  assert.equal(h.GM.size, 0);
  assert.deepEqual(h.HB.items, [{ id: 3 }], 'the Earlier-batches column drops them at once');
  assert.ok(h.log.saved >= 1 && h.log.ticks >= 1 && h.log.hist >= 1);
});

test('a refused removal (a job in flight) says why in words and forgets nothing', async () => {
  const h = load({ post: () => ({ ok: false, j: { error: 'G007 cannot be removed while J042 is still working on it.' } }) });
  h.ACT.grm(); await h.log.pending;
  assert.deepEqual(h.log.toasts, [['G007 cannot be removed while J042 is still working on it.', true]]);
  assert.deepEqual(h.SES.gens, [7, 8]);
  assert.equal(h.GM.size, 2);
});

test('the Removed batches list shows each batch with a Restore button, and Restore calls the route', async () => {
  const el = { innerHTML: '', querySelector: () => null };
  const h = load({ el, post: () => ({ ok: true, j: { id: 'G003', restored: true } }), api: () => ({ ok: true, j: { batches: [{ id: 'G003', number: 3, subject: 'iron_man', removed: 1 }] } }) });
  await h.remLoad();
  assert.match(el.innerHTML, /Removed batches \(1\)/);
  assert.match(el.innerHTML, /<b>G003<\/b> iron man/);
  assert.match(el.innerHTML, /data-act=grestore data-n=3>Restore<\/button>/);
  await h.ACT.grestore({ dataset: { n: '3' } });
  assert.deepEqual(h.log.posts, [['/api/generations/3/restore', {}]]);
  assert.deepEqual(h.log.toasts, [['G003 is back', false]]);
});

test('an empty trash draws nothing (and a member, who cannot read it, sees nothing)', async () => {
  const el = { innerHTML: 'old', querySelector: () => null };
  const h = load({ el, api: () => ({ ok: false, j: {} }) });
  await h.remLoad();
  assert.equal(el.innerHTML, '');
});

test('with no batch left the Edge strip is emptied AND loses its box styling, so no blank bar stays under the Studio', () => {
  assert.match(live, /if\(!gs\.length\)\{bar\.innerHTML='';bar\.className='';bar\.removeAttribute\('data-built'\)\}/);
});

test('each removed batch has Restore and Remove, and the list has one Remove all', () => {
  const h = load();
  const html = h.remHtml([{ id: 'G006', number: 6, subject: 'cat_pack', removed: 1 }, { id: 'G007', number: 7, removed: 1 }], true);
  assert.match(html, /data-act=grestore data-n=6>Restore<\/button><button class="btn sm dng" data-act=gpurge data-id=G006>Remove<\/button>/);
  assert.equal((html.match(/data-act=gpurge /g) || []).length, 2);
  assert.match(html, /data-act=gpurgeall>Remove all \(2\)</);
  assert.equal(h.remHtml([], false), '', 'nothing removed: no list at all');
});

test('Remove deletes that batch for good through the purge route after a confirm', async () => {
  const h = load({ post: () => ({ ok: true, j: { status: 'done', done: 1 } }) });
  h.ACT.gpurge({ dataset: { id: 'G006' } }); await h.log.pending;
  assert.match(h.log.dialogs[0][0], /^Remove G006 for good\? Its files and database rows are deleted/);
  assert.deepEqual(h.log.posts, [['/api/trash/purge', { type: 'batch', id: 'G006' }]]);
  assert.deepEqual(h.log.toasts.at(-1), ['G006 removed for good', false]);
});

test('Remove all asks for the typed phrase of the batches only, then purges batches only', async () => {
  const h = load({ api: url => url === '/api/trash' ? { ok: true, j: { purge_batches: { count: 2, phrase: 'purge 2' } } } : { ok: true, j: { batches: [] } },
    post: () => ({ ok: true, j: { status: 'done', done: 2 } }), typed: { value: ' Purge 2 ', focus() {} } });
  await h.ACT.gpurgeall();
  assert.match(h.log.dialogs[0][0], /Remove all 2 removed batches for good\?[\s\S]*Type “purge 2”/);
  await h.ACT.gpurgeallgo({ dataset: { phrase: 'purge 2' } });
  await new Promise(r => setTimeout(r, 0));
  assert.deepEqual(h.log.posts, [['/api/trash/purge_all', { confirm: 'purge 2', kind: 'batch' }]]);
  const wrong = load({ api: () => ({ ok: true, j: {} }), typed: { value: 'purge 3', focus() {} } });
  wrong.ACT.gpurgeallgo({ dataset: { phrase: 'purge 2' } });
  assert.deepEqual(wrong.log.posts, [], 'a wrong phrase sends nothing');
});
