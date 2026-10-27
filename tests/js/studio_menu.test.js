const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// The Studio menu (2026-10-03): the empty prepared-sheets line and the Prompt preview block are gone, and nothing still calls the removed preview.
// The scripts are read as text: the strings must not exist, and the composer must still draw its own chips. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const dir = path.join(__dirname, '..', '..', 'stickerloom', 'console');
const read = f => readConsoleFile(path.join(dir, f), 'utf8');
const gen = read('generate.js'), composer = read('composer.js'), live = read('live.js'), css = read('studio.css');

test('the empty-state line that leaked a watch-folder path is gone', () => {
  for (const [name, src] of [['generate.js', gen], ['composer.js', composer], ['live.js', live]]) {
    assert.doesNotMatch(src, /No prepared sheets found/, name);
    assert.doesNotMatch(src, /inputs\/Images_gen/, `${name} must not print a watch-folder name (rule 9)`);
  }
  for (const [name, src] of [['generate.js', gen], ['composer.js', composer]]) {
    assert.doesNotMatch(src, /Prepared sheets:|data-act=gsug/, `${name}: the prepared-sheets chip row is gone`);
  }
});

test('the Prompt preview block and everything that fed it are gone', () => {
  for (const [name, src] of [['generate.js', gen], ['composer.js', composer], ['live.js', live]]) {
    assert.doesNotMatch(src, /Prompt preview/, name);
    assert.doesNotMatch(src, /planPreview/, `${name}: nothing may still call the removed preview`);
    assert.doesNotMatch(src, /id=gplan>/, name);
  }
  assert.doesNotMatch(gen, /template \$\{esc\(r\.j\.template_id\)\}/, 'the template internals are not printed on the Studio screen');
  assert.doesNotMatch(css, /\.gpv\b/, 'the preview’s CSS went with it');
});

test('the composer still draws its own chips: Stroke and AI enhancer as cp-chip', () => {
  assert.match(composer, /class="cp-chip \$\{CP\.pop==='stroke'\?'open':''\}" data-act=cpstroke/);
  assert.match(composer, /class="cp-chip cp-ai \$\{aiOn\(\)\?'on':''\}" data-act=cpai/);
  assert.match(composer, /g\.querySelectorAll\(':scope > \.sh, :scope > \.gform, :scope > \.gopts, #lvpanel'\)\.forEach\(n=>n\.remove\(\)\)/, 'the old White outline pills are removed when the composer mounts');
});
