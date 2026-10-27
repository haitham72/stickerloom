const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// P3 of the UI/UX spec: the pack screen offers Delete as a labelled button, and its confirmation says exactly what happens (nothing is destroyed silently). Delete pack is SOFT since
// 2026-10-03: the pack goes to the trash with its stickers and files, restorable and deletable for good from Settings > Trash; the stickers that came from a batch also stay in their batch,
// the ones that exist only in this pack (editor renders, bursts) are counted, the particle sets stay in the Library.
// The statements are read out of packs.js. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const src = readConsoleFile(path.join(__dirname, '..', '..', 'stickerloom', 'console', 'packs.js'), 'utf8');
function statement(marker) {return consoleStatement(src,marker);}
const sandbox = { esc: s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])) };
const { pkDelText } = new Function(...Object.keys(sandbox), statement('function pkDelText(') + '\nreturn {pkDelText};')(...Object.values(sandbox));

const st = (id, src = {}) => ({ id, name: id, source: src });

test('the confirmation says the pack goes to the trash and counts what came from batches and what exists only here', () => {
  const t = pkDelText({ name: 'Barbie', stickers: [st('a', { generation: 'G012', index: 1 }), st('b', { generation: 'G012', index: 2 }), st('c', { generation: 'G013', index: 4 }), st('d'), st('e', { effect: 'E001' })] });
  assert.match(t, /Delete the pack “Barbie”\?/);
  assert.match(t, /goes to the trash with its 5 stickers: nothing is deleted yet/);
  assert.match(t, /restore it, or delete it for good, from Settings, Trash/);
  assert.match(t, /3 of its stickers came from batches \(G012, G013\), which keep their own copies/);
  assert.match(t, /2 exist only in this pack/);
  assert.match(t, /particle sets stay in the Library/);
});

test('a pack of batch stickers does not claim anything is lost; an empty pack is short and still goes to the trash', () => {
  const t = pkDelText({ name: 'Cats', stickers: [st('a', { generation: 'G001', index: 1 })] });
  assert.match(t, /1 of its stickers came from batches/);
  assert.match(t, /its 1 sticker:/);
  assert.doesNotMatch(t, /exist only in this pack/);
  const e = pkDelText({ name: 'Empty', stickers: [] });
  assert.match(e, /has no stickers/);
  assert.match(e, /goes to the trash/);
});

test('the text is plain (confirmDlg escapes it once) and the pack screen shows a labelled Delete button', () => {
  assert.match(pkDelText({ name: '<b>x</b>', stickers: [] }), /“<b>x<\/b>”/, 'not escaped here: the dialog does it, and double escaping would show &lt;');
  assert.match(src, /data-act=pkdel[^>]*>\$\{ic\('trash'\)\} Delete pack</, 'a label, not only an icon');
  assert.match(src, /ACT\.pkdel=.*pkDelText\(/, 'the confirmation is the one that counts');
});

// The pack screen: Open in Studio | Download .zip | Send to Telegram | ⋯; everything else moved into ⋯; one group per batch.
test('the header is Open in Studio, Download .zip, Send to Telegram, then ⋯ with every other pack action', () => {
  const head = src.slice(src.indexOf('<div class=pk-acts>'), src.indexOf('Delete pack</button>'));
  const order = ['data-act=pkstudio', 'data-act=pkzip data-l=1', 'data-act=tgsend', 'class=gh-more'].map(k => head.indexOf(k));
  assert.ok(order.every(i => i >= 0) && order.every((v, i) => !i || v > order[i - 1]), 'the four in this order');
  const more = head.slice(head.indexOf('class=gh-more'));
  for (const k of ['pkrename', 'pkpreview', 'pkzip data-l=0', 'data-to=chat', 'pkadd']) assert.ok(more.includes(k), k + ' is in ⋯');
});

test('stickers are grouped per batch, in the order of their first sticker, the ones from outside the Studio last', () => {
  const { pkGroups } = new Function(statement('const pkGenOf=') + '\n' + statement('function pkGroups(') + '\nreturn {pkGroups};')();
  const g = pkGroups([st('a', { generation: 'G146', index: 1 }), st('b'), st('c', { generation: '12', index: 2 }), st('d', { generation: 'G146', index: 3 })]);
  assert.deepEqual(g.map(x => [x.gen, x.items.map(s => s.id).join('')]), [['G146', 'ad'], ['G012', 'c'], ['', 'b']]);
  assert.match(src, /data-act=pkgdel[^>]*>\$\{ic\('trash'\)\} Remove \$\{g\.gen\?'this batch':'these'\} from the pack/);
});
