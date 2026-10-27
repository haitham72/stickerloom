const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// P8 of the UI/UX spec: after a slice is edited and saved, the batch's sheet is rebuilt as one sheet with that slice fixed (source/sheet_fixed.png); the Studio's sheet panel can show it as a
// third view next to Raw and Keyed, and only when there is one. The helpers are read out of generate.js. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const src = readConsoleFile(path.join(__dirname, '..', '..', 'stickerloom', 'console', 'generate.js'), 'utf8');
function statement(marker) {return consoleStatement(src,marker);}
const f = new Function(['const SHK=', 'const sheetViews=', 'const sheetView='].map(statement).join('\n') + '\nreturn {SHK,sheetViews,sheetView};')();
const G = (source, n = 7) => ({ number: n, generation_id: 'G007', source: { sheet_copy: 'source/sheet.png', ...source } });

test('the views a sheet offers: raw always, keyed and fixed only when they exist', () => {
  assert.deepEqual(f.sheetViews(G({})).map(v => v.id), ['raw']);
  assert.deepEqual(f.sheetViews(G({ keyed: 'source/keyed.png' })).map(v => v.id), ['raw', 'keyed']);
  const all = f.sheetViews(G({ keyed: 'source/keyed.png', sheet_fixed: 'source/sheet_fixed.png', sheet_fixed_cells: [2, 5] }));
  assert.deepEqual(all.map(v => v.id), ['raw', 'keyed', 'fixed']);
  assert.equal(all[2].file, 'source/sheet_fixed.png');
  assert.match(all[2].label, /Fixed \(S2, S5\)/, 'it says which slices were fixed');
});

test('the chosen view is remembered per batch and falls back to raw when it is not there', () => {
  const g = G({ keyed: 'source/keyed.png', sheet_fixed: 'source/sheet_fixed.png', sheet_fixed_cells: [2] });
  assert.equal(f.sheetView(g).id, 'raw');
  f.SHK.set(7, 'fixed');
  assert.equal(f.sheetView(g).id, 'fixed');
  assert.equal(f.sheetView(G({ keyed: 'source/keyed.png' })).id, 'raw', 'a batch with no fixed sheet shows the raw one even if "fixed" was chosen');
  f.SHK.delete(7);
});

test('the panel draws the tabs from the views and the handler sets the view', () => {
  assert.match(src, /sheetViews\(g\)\.map\(v=>`<button class="tab \$\{v\.id===cur\.id\?'on':''\}" data-act=gshk data-g=\$\{g\.number\} data-k=\$\{v\.id\}>/);
  assert.match(src, /ACT\.gshk=el=>\{const n=\+el\.dataset\.g,M=GS\.tab==='anim'\?AVK:SHK;/, 'each view (Stickers, Animation) remembers its own tab');
  assert.match(src, /canSend\(g\)\?tab\('send','To send'\):''\}\$\{vidOf\(g\)\?tab\('video','Video'\):''\}/, 'one frame: the sheet views, then To send and Video');
});
