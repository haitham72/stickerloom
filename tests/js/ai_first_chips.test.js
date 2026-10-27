const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// P9 / P10 of the UI/UX spec: a one-time decision is not a creation control. The first answer of a chat shows Create it and, on the right, ONE AI vision switch with a subtle rotating glow while it
// is undecided. Pressing it writes the setting through the settings route and makes no chat turn (agsetting, never agaction / agchip). The switch then reads the live setting: on, off, or still
// undecided. The statements are read out of agent.js. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const DIR = path.join(__dirname, '..', '..', 'stickerloom', 'console');
const src = readConsoleFile(path.join(DIR, 'agent.js'), 'utf8');
const css = readConsoleFile(path.join(DIR, 'agent.css'), 'utf8');
function statement(marker) {return consoleStatement(src,marker);}
function load(allow) {
  const A = { sess: { settings: { allow_vlm: allow } } };
  const sandbox = { AIU: { esc: s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])) }, A };
  const { chipHTML } = new Function(...Object.keys(sandbox), statement('function chipHTML(') + '\nreturn {chipHTML};')(...Object.values(sandbox));
  return chipHTML;
}
const SW = { label: 'Allow AI vision', on: 'AI vision on', off: 'AI vision off', setting: { allow_vlm: true }, side: 'right', glow: true };

test('undecided: the switch glows, sits on the right, and saves a setting instead of making a turn', () => {
  const h = load(null)(SW);
  assert.match(h, /<span class="ai-vis glow"><button class="ai-chip" data-act=agsetting/, 'a chamfered box with its own class: the settings toggle .ai-sw (44x26 px) is not reused, that is what squashed it');
  assert.match(h, /data-act=agsetting data-set="\{&quot;allow_vlm&quot;:true\}"/);
  assert.match(h, />Allow AI vision</);
  assert.match(h, /aria-pressed=false/);
  assert.doesNotMatch(h, /agaction|agchip/, 'a setting is never a chat message');
});

test('on: no glow any more, it says so, and pressing it again switches vision off', () => {
  const h = load(true)(SW);
  assert.doesNotMatch(h, /glow/);
  assert.match(h, /is-on/);
  assert.match(h, />AI vision on</);
  assert.match(h, /aria-pressed=true/);
  assert.match(h, /data-set="\{&quot;allow_vlm&quot;:false\}"/);
});

test('off (the person refused or switched it off): it says so and offers turning it on', () => {
  const h = load(false)(SW);
  assert.match(h, />AI vision off</);
  assert.match(h, /data-set="\{&quot;allow_vlm&quot;:true\}"/);
  assert.doesNotMatch(h, /glow/);
});

test('the go-ahead and the suggestions are drawn exactly as before', () => {
  const chip = load(null);
  assert.match(chip({ label: 'Create it', action: 'confirm' }), /class="ai-chip pri" data-act=agaction data-type="confirm">Create it</);
  assert.match(chip({ label: 'Not yet', action: 'cancel' }), /class="ai-chip" data-act=agaction data-type="cancel">Not yet</);
  assert.match(chip({ label: 'falcon', text: 'make falcon stickers' }), /data-act=agchip data-text="make falcon stickers">falcon</);
});

test('the handler only saves the setting, the message repaints when the switch changes, and the glow rests under reduced motion', () => {
  assert.match(src, /ACT\.agsetting=async el=>\{[^}]*saveSet\(JSON\.parse\(el\.dataset\.set\)\)/);
  assert.doesNotMatch(statement('ACT.agsetting='), /agSend|post\(`\/api\/chat\/sessions\/\$\{A\.sid\}\/messages/, 'no message is sent');
  assert.match(src, /\(m\.chips\|\|\[\]\)\.some\(c=>c\.setting\)\?String\(A\.sess[^)]*allow_vlm\)/, 'the signature of a message with a switch includes the live setting');
  assert.match(css, /@property --ag-ang/);
  assert.match(css, /\.ai-vis\.glow \.ai-chip\{animation:ag-spin/);
  assert.match(css, /\.ai-vis \.ai-chip\{[^}]*clip-path:polygon/, 'chamfered corners');
  assert.match(css, /prefers-reduced-motion:reduce\)\{[^}]*\.ai-vis\.glow \.ai-chip\{animation:none/);
  assert.match(css, /\.ai-vis\{[^}]*margin-left:auto/, 'it sits on the right');
});
