const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// The Trash panel (console/trash.js, Settings > Trash): the pure builders under node. Written 2026-10-03 and NOT RUN when it was written (testing was deferred). Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const TRX = require(require('./console_source').resolveConsoleFile(require('node:path').join(__dirname,'../../stickerloom/console/trash.js')));

const batch = (o = {}) => ({ type: 'batch', id: 'G012', number: 12, subject: 'Falcon', removed: 1790000000, by: 'local', stickers: 9, files_total: 40, bytes: 5 * 1048576,
  db: { available: true, indexed: 9, vectors: 9 }, copies_in_packs: [{ id: 'p1', name: 'Gold', stickers: 2, trashed: false }], in_flight: [], needs_confirm: false, confirm_words: null, blocked: null, ...o });
const pack = (o = {}) => ({ type: 'pack', id: 'abcd1234', name: 'Cats', deleted: 1790000100, by: 'local', stickers: 3, bytes: 2048, files: [{ file: 'a', missing: false, shared: false }, { file: 'b', missing: true, shared: false }, { file: 'c', missing: false, shared: true }],
  shared: [], particle_sets: ['Hearts'], needs_confirm: false, confirm_words: null, blocked: null, ...o });
const data = (o = {}) => ({ batches: [batch()], packs: [pack()], totals: { items: 2, batches: 1, packs: 1, files: 41, bytes: 5244928 }, purge_all: { count: 2, phrase: 'purge 2', skipped: [] }, purge: null, ...o });

test('the list shows every item with what a purge removes, Restore and Delete for good', () => {
  const h = TRX.html(data());
  assert.match(h, /G012 · Falcon/);
  assert.match(h, /Pack “Cats”/);
  assert.match(h, /9 stickers, 40 files, 5\.0 MB/);
  assert.match(h, /9 search entries \(9 with vectors\) go; the review history stays/);
  assert.match(h, /Copies in “Gold” stay/);
  assert.match(h, /3 stickers, 1 file, 2 KB\./, 'a missing file and a shared file are not counted as removed');
  assert.match(h, /Particle sets “Hearts” stay in the Library/);
  assert.equal((h.match(/data-act=trrestore/g) || []).length, 2);
  assert.equal((h.match(/data-act=trpurge /g) || []).length, 2);
  assert.match(h, /Delete all \(2\)/);
});

test('an empty trash says so, and Delete all is disabled when nothing can go', () => {
  assert.match(TRX.html(data({ batches: [], packs: [], totals: { items: 0 } })), /The trash is empty/);
  assert.match(TRX.html(data({ purge_all: { count: 0, phrase: 'purge 0', skipped: [{ kind: 'batch', id: 'G012', why: 'x' }] } })), /data-act=trpurgeall disabled/);
});

test('a database that is off is said in words; a blocked item cannot be deleted and says why; a shared one shows its sentence', () => {
  assert.match(TRX.html(data({ batches: [batch({ db: { available: false } })] })), /database is not reachable or not written to, so only files go/);
  const h = TRX.html(data({ batches: [batch({ blocked: 'G012 cannot be deleted for good while J001 is still working on it.' })], packs: [pack({ needs_confirm: true, confirm_words: '“Cat” is also in Beta. Confirm to go on.' })] }));
  assert.match(h, /still working on it/);
  assert.match(h, /data-act=trpurge [^>]*disabled/);
  assert.match(h, /also in Beta/);
});

test('everything shown is escaped', () => {
  const h = TRX.html(data({ batches: [batch({ subject: '<img src=x onerror=1>' })], packs: [pack({ name: '"><script>' })] }));
  assert.doesNotMatch(h, /<img src=x/);
  assert.doesNotMatch(h, /<script>/);
});

test('the confirmation of one item says what goes and that it cannot be undone; delete all is typed and names the count', () => {
  assert.match(TRX.confirmOne(batch()), /Delete G012 for good\? .*cannot be undone/);
  assert.match(TRX.confirmOne(pack({ needs_confirm: true, confirm_words: 'Beta uses a file. Confirm to go on.' })), /Beta uses a file/);
  const pa = { count: 7, phrase: 'purge 7' };
  assert.match(TRX.allText(pa), /deletes 7 items for good\. Type “purge 7”/);
  assert.equal(TRX.typedOk('  Purge   7 ', pa), true);
  assert.equal(TRX.typedOk('purge 6', pa), false);
  assert.equal(TRX.typedOk('', pa), false);
});

test('a finished purge is told in one sentence, failures and skipped items included', () => {
  assert.equal(TRX.outcome({ nothing_to_do: true }), 'Nothing to delete.');
  const t = TRX.outcome({ status: 'failed', total: 3, results: [{ ok: true }, { ok: true }, { ok: false, id: 'G002', error: 'disk said no' }], refused: [{ id: 'G005' }] });
  assert.match(t, /2 of 3/);
  assert.match(t, /G002 \(disk said no\).*run it again/);
  assert.match(t, /need their own confirmation: G005/);
});

test('a purge that outlasts the 3-minute poll says so instead of going quiet', () => {
  assert.match(TRX.outcome({ status: 'running', timedout: true, total: 9, results: [] }), /Still deleting after 3 minutes/);
});

test('the script follows the console rules: names prefixed, every data-act has a handler, one ACT object', () => {
  const src = readConsoleFile(path.join(__dirname, '..', '..', 'stickerloom', 'console', 'trash.js'), 'utf8');
  const top = [...src.matchAll(/^(?:const|let|function|class)\s+([A-Za-z_$][\w$]*)/gm)].map(m => m[1]);
  assert.deepEqual(top.filter(n => !/^(TRX|trx)/.test(n)), []);
  const acts = new Set([...src.matchAll(/data-act=([A-Za-z0-9_]+)/g)].map(m => m[1]));
  const defined = new Set([...src.matchAll(/\bACT\.([A-Za-z0-9_]+)\s*=(?!=)/g)].map(m => m[1]));
  for (const a of acts) assert.ok(defined.has(a) || a === 'dlgx', `data-act=${a} has a handler`);
  const html = readConsoleFile(path.join(__dirname, '..', '..', 'stickerloom', 'console', 'index.html'), 'utf8');
  assert.match(html, /<script src=\/ui\/trash\.js><\/script>/);
  assert.ok(require('./console_source').consoleFiles().includes('trash.js'), 'the served asset manifest includes the script');
});

test('a batch row shows the subject the way the Earlier-batches column does, not the raw key', () => {
  const h = TRX.html(data({ batches: [batch({ subject: 'dog_as_banana' })] }));
  assert.match(h, /G012 · Dog As Banana/);
  assert.doesNotMatch(h, /dog_as_banana/);
  assert.equal(TRX.nice('angel_reading_newspaper'), 'Angel Reading Newspaper');
  assert.match(TRX.html(data({ batches: [batch({ subject: null })] })), /<b>G012<\/b>/);
});
