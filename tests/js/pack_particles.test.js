const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// The markup of the "Particles" section of the sticker view and of the pack grid's counter (packs.js). The pure statements are read out of the file and run with the few globals
// they use stubbed (esc, ic, packById, FXWHY), so the markup is checked for real. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const src = readConsoleFile(path.join(__dirname, '..', '..', 'stickerloom', 'console', 'packs.js'), 'utf8');

/** One whole top-level statement of packs.js: from `marker` to the next line that starts in column 0. */
function statement(marker) {return consoleStatement(src,marker);}

const PACKS = { p1: { id: 'p1', name: 'Fruits' }, p2: { id: 'p2', name: 'Elsewhere <b>' } };
const code = ['const ptWhy=', 'const ptKb=', 'const ptPackName=', 'function ptCard(', 'function ptGroupRuns(', 'function ptRunCard(', 'function ptRowMedia(', 'function ptRow(', 'function ptRowsHtml(', 'const ptCount=', 'function ptHtml(', 'function ptBody(', 'function ptBadge('].map(statement).join('\n');
const make = (extra = '') => new Function('esc', 'ic', 'packById', `${extra}\n${code}\nconst spKindChip=()=>"From sprites",spCredits=n=>n+" credits";return {ptHtml, ptBody, ptCard, ptBadge, ptKb, ptWhy, ptGroupRuns, ptRowsHtml};`)(
  s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
  n => `<svg data-i=${n}></svg>`,
  id => PACKS[id]);
const P = make('const FXWHY={effect_tail_faded:"Pieces were still on screen at the end.",size_budget:"Over Telegram size limit."};const PKPT={c:{}};');

const item = (o = {}) => ({ effect: 'E001', result: 'R001', mode: 'sim', status: 'READY', bytes: 52 * 1024, warnings: [], blocks: [], url: '/out/effects/E001/results/R001.webm', missing: false,
  added_to: null, created: 1, shared: false, group: 'g1', usable: true, ...o });
const S = { id: 'a1', pack_id: 'p1' };

test('while loading it shows a spinner, and an error shows its words', () => {
  assert.match(P.ptHtml(null, S), /class=spin/);
  assert.match(P.ptHtml({ error: 'no such pack' }, S), /no such pack/);
});

test('no particles: the empty state with one primary "Make particles" button', () => {
  const h = P.ptHtml({ created: [], saved: [], can_make: true }, S);
  assert.match(h, /No particles yet/);
  assert.match(h, /class="btn sm pri" data-act=ptmake data-p=p1 data-s=a1>/, 'the button carries its pack and sticker, so it works outside the library carousel too');
  assert.doesNotMatch(h, /<video/);
  assert.doesNotMatch(P.ptHtml({ created: [], saved: [], can_make: false }, S), /data-act=ptmake/);
});

test('a legacy run is one group with looping sprites and raw Add only inside details', () => {
  const h = P.ptHtml({ created: [item()], saved: [], can_make: true }, S);
  assert.match(h, /<video src="\/out\/effects\/E001\/results\/R001\.webm" autoplay loop muted/);
  assert.match(h, /E001 · R001/);
  assert.match(h, /52 KB/);
  assert.match(h, /data-act=ptadd data-e=E001 data-r=R001 data-p=p1 data-s=a1>Add to pack/);
  assert.match(h, /data-act=ptopen data-e=E001>Open effect/);
  assert.match(h, /0 saved/);
});

test('a saved take wears a "Saved in <pack>" badge, escaped, and has no Add button', () => {
  const h = P.ptHtml({ created: [item({ added_to: 'p2' })], saved: [], can_make: true }, S);
  assert.match(h, /Saved in Elsewhere &lt;b&gt;/);
  assert.doesNotMatch(h, /data-act=ptadd/);
});

test('warnings are said in words; a failed or missing take is flagged and cannot be added', () => {
  const warn = P.ptHtml({ created: [item({ warnings: ['effect_tail_faded', 'effect_unknown_thing'] })], saved: [] }, S);
  assert.match(warn, /Pieces were still on screen at the end\./);
  assert.match(warn, /effect unknown thing/, 'an unknown id is still readable');
  assert.match(warn, /data-act=ptadd/, 'a warning never blocks adding');
  const bad = P.ptHtml({ created: [item({ status: 'FAILED', usable: false, blocks: ['size_budget'], url: null, missing: true })], saved: [] }, S);
  assert.match(bad, /pk-pt-card bad/);
  assert.match(bad, /Failed/);
  assert.match(bad, /Over Telegram size limit\./);
  assert.doesNotMatch(bad, /<video|data-act=ptadd/);
  assert.match(P.ptHtml({ created: [item({ missing: true, usable: false })], saved: [] }, S), /File missing/);
});

test('a video cell says it is shared by its group and which one is this sticker’s', () => {
  const h = P.ptCard(item({ mode: 'video', shared: true, cell: 2, assigned: true }));
  assert.match(h, /video, cell 2 \(the one this sticker gets\) · shared by its group/);
  assert.doesNotMatch(P.ptCard(item({ mode: 'video', shared: true, cell: 3, assigned: false })), /the one this sticker gets/);
});

test('saved raw clips stay inside one run details and open their pack', () => {
  const sv = { sticker_id: 'x1', name: 'Heart <i>', pack_id: 'p2', pack: 'Elsewhere', url: '/lib/a/b.webm', missing: false };
  const h = P.ptHtml({ created: [], saved: [sv], can_make: true }, S);
  assert.match(h, /Original sprite clips/);
  assert.match(h, /data-act=ptsaved data-id=p2/);
  assert.match(h, /title="Heart &lt;i&gt; in Elsewhere"/);
  assert.match(h, /<video src="\/lib\/a\/b\.webm"/);
});

test('the grid counter shows only when the sticker has particles', () => {
  const Q = make('const FXWHY={};const PKPT={c:{p1:{a1:{created:3,saved:1},a2:{created:0,saved:0}}}};');
  assert.match(Q.ptBadge('p1', 'a1'), /class=pk-pt-n title="3 particles made, 1 saved">.*4<\/span>/);
  assert.equal(Q.ptBadge('p1', 'a2'), '');
  assert.equal(Q.ptBadge('p1', 'zz'), '');
  assert.equal(Q.ptBadge('nope', 'a1'), '');
});

test('sizes read like a person says them', () => {
  assert.equal(P.ptKb(200), '200 B');
  assert.equal(P.ptKb(2048), '2 KB');
});

test('ptBody is the takes and the saved stickers without a heading or an empty state (the Studio section draws its own)', () => {
  const h = P.ptBody({ created: [item()], saved: [{ sticker_id: 'x1', name: 'Heart', pack_id: 'p2', pack: 'Elsewhere', url: '/lib/a.webm', missing: false }] }, S);
  assert.match(h, /pk-rows/);
  assert.match(h, /data-act=ptadd data-e=E001 data-r=R001 data-p=p1 data-s=a1>/);
  assert.match(h, /Original sprite clips/);
  assert.doesNotMatch(h, /pk-pt-h|No particles yet/);
  assert.equal(P.ptBody({ created: [], saved: [] }, S), '');
  assert.match(P.ptBody(null, S), /class=spin/);
  assert.match(P.ptBody({ error: 'no such pack' }, S), /no such pack/);
});

test('saved versions are numbered rows under the sticker; sprites stay inside their row; drafts are apart', () => {
  const row = (o = {}) => ({ id: 'P004', version: 1, label: 'Sprites', n_sprites: 1, credits: 0, job: null, shared_with: 0, in_pack: [], addable: null, preview: '/out/particles/P004/renders/R001.webm', sprites: [], ...o });
  const kling = row({ id: 'P005', version: 2, label: 'Kling from scratch', n_sprites: 4, credits: 4.5, job: 'J039', preview: null,
    sprites: [1, 2, 3, 4].map(n => ({ n, clip_url: `/out/particles/P005/cells/c0${n}.webm`, url: `/out/particles/P005/cells/c0${n}.png`, type: 'animated' })) });
  const h = P.ptHtml({ rows: [row({ addable: { render: 'R001', pack_id: 'p1' } }), kling], drafts: [row({ id: 'P006', version: undefined, label: 'AI image sprites' })], created: [], saved: [], can_make: true }, S);
  assert.match(h, /2 saved/);
  assert.match(h, /data-act=ptmake[^>]*>[^<]*<svg[^>]*><\/svg> New version</);
  assert.equal((h.match(/class="pk-row"/g) || []).length, 2, 'one row per saved version');
  assert.match(h, /<span class=pk-row-v>v1<\/span>[\s\S]*<span class=pk-row-v>v2<\/span>/);
  assert.match(h, /Kling from scratch[\s\S]*4 sprites · 4.5 credits · J039/);
  assert.equal((h.match(/data-particle-row=P005/g) || []).length, 1, 'the four Kling slices are inside ONE row, never four particles');
  assert.match(h, /data-act=psbadd data-id=P004 data-r=R001 data-p=p1 data-s=a1>Add to pack/);
  assert.match(h, /pk-row draft[\s\S]*>Draft</);
  assert.doesNotMatch(h.split('pk-row draft')[1], /data-act=psbadd/, 'a draft is saved before it can go into the pack');
  const inPack = P.ptRowsHtml({ rows: [row({ in_pack: [{ sticker_id: 'b1', pack_id: 'p1', render: 'R001' }], addable: null })] }, S);
  assert.match(inPack, /In pack ✓/);
});
