const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// P5 of the UI/UX spec: a sticker's name never carries its id. The id (G103/S2) stays what it is (rule 11: an id survives a rename), the title and the key stay their own fields, and the header
// shows "superhero jump spin 🚀💥" with the id as a SEPARATE affordance: hoverable, and one click copies it. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const DIR = path.join(__dirname, '..', '..', 'stickerloom', 'console');
const agent = readConsoleFile(path.join(DIR, 'agent.js'), 'utf8');
const gen = readConsoleFile(path.join(DIR, 'generate.js'), 'utf8');
const app = readConsoleFile(path.join(DIR, 'app.js'), 'utf8');

function statement(srcText,marker) {return consoleStatement(srcText,marker);}

const sandbox = {
  AIU: { esc: s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])) },
  ic: n => `<svg data-i=${n}></svg>`, A: { sel: new Set() },
};
const { tileHTML } = new Function(...Object.keys(sandbox), ['const humanWhy=', 'function tileHTML('].map(m => statement(agent, m)).join('\n') + '\nreturn {tileHTML};')(...Object.values(sandbox));
const S = (o = {}) => ({ id: 'G103/S2', index: 2, key: 'superhero_jump_spin', title: 'superhero jump spin', emoji: ['🚀', '💥'], status: 'READY', still: 'PENDING', png: '/out/G103/slices/S2.png', ...o });

test('the chat tile’s name is the title and the emoji, never the id; the id is its own hoverable, copyable control', () => {
  const t = tileHTML(S(), 'G103', null);
  const cap = t.match(/<figcaption class=ag-nm>([\s\S]*?)<\/figcaption>/)[1];
  assert.match(cap, /superhero jump spin 🚀💥/);
  assert.doesNotMatch(cap, /G103|\/S2|ag-id/, 'the name header carries no id');
  assert.match(t, /<button class=ag-id data-act=copyid data-v="G103\/S2" title="G103\/S2 · click to copy the id" aria-label="Copy the id G103\/S2">G103\/S2<\/button>/);
});

test('the title, the key and the id stay three fields: the title wins for the name, the key is the fallback, the id is never a fallback', () => {
  assert.match(tileHTML(S({ title: null }), 'G103', null), /<span>superhero jump spin 🚀💥<\/span>/, 'no title: the key, humanised');
  const named = tileHTML(S({ title: 'Iron man <b>', key: 'k' }), 'G103', null);
  assert.match(named, /Iron man &lt;b&gt;/, 'escaped');
  assert.doesNotMatch(named.match(/<figcaption[\s\S]*?<\/figcaption>/)[0], /G103/);
  const bare = tileHTML(S({ id: '', title: null, key: null, name: null }), 'G103', null);
  assert.doesNotMatch(bare, /ag-id/, 'a tile without an id offers no copy button and shows no id in its place');
});

test('the Studio’s sticker header shows the name and the id apart, and copying is one shared handler', () => {
  const head = gen.match(/<div style="flex:1"><b>\$\{esc\(t\.emoji\)\} \$\{esc\(t\.key[^`]*?<\/b>[^`]*?<\/div>/)[0];
  assert.doesNotMatch(head, /generation_id|g\.number|\/S\$\{/, 'the name block has no id in it');
  assert.match(gen, /data-act=copyid data-v="\$\{g\.generation_id\}\/S\$\{t\.index\}" title="\$\{g\.generation_id\}\/S\$\{t\.index\} · click to copy the id"/);
  assert.equal((app.match(/ACT\.copyid=/g) || []).length, 1, 'one handler for every id affordance');
  assert.doesNotMatch(agent + gen, /ACT\.copyid=/);
});
