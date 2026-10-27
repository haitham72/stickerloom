const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// P11a of the UI/UX spec: a transformation or a cleaning is the EDITOR's job. The AI offers it ("want me to boot up the editor?") with a chip that opens the image editor on that slice at once
// (no chat turn, nothing spent); when it is saved the person lands back in the AI, not in the Studio, because the session was in the AI. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const DIR = path.join(__dirname, '..', '..', 'stickerloom', 'console');
const agent = readConsoleFile(path.join(DIR, 'agent.js'), 'utf8');
const editor = readConsoleFile(path.join(DIR, 'editor.js'), 'utf8');
const gen = readConsoleFile(path.join(DIR, 'generate.js'), 'utf8');
function statement(srcText,marker) {return consoleStatement(srcText,marker);}
const sandbox = { AIU: { esc: s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])) }, A: { sess: { settings: {} } } };
const { chipHTML } = new Function(...Object.keys(sandbox), statement(agent, 'function chipHTML(') + '\nreturn {chipHTML};')(...Object.values(sandbox));

test('the editor chip opens the editor on that slice and is not a chat message', () => {
  const h = chipHTML({ label: 'Open the editor', editor: { generation: 'G012', index: 2 } });
  assert.match(h, /^<button class="ai-chip pri" data-act=agedit data-g="G012" data-i=2>Open the editor<\/button>$/);
  assert.doesNotMatch(h, /agaction|agchip|agsetting/);
});

test('agedit opens the editor from the AI and the editor remembers to return there', () => {
  assert.match(agent, /ACT\.agedit=el=>\{[^}]*studioEditSticker\(\+String\(el\.dataset\.g\)\.replace\(\/\\D\/g,''\),\+el\.dataset\.i,'agent'\)/);
  assert.match(gen, /async function studioEditSticker\(gnum,index,to\)/);
  assert.match(gen, /back:\{gen:g\.number,index,to\}/, 'the editor is told where it was opened from');
  assert.match(editor, /location\.hash=b\.to==='agent'\?'#\/agent':'#\/studio'/, 'Save returns to where the person was');
  assert.match(editor, /ACT\.edback=\(\)=>\{if\(E\.back\)\{const to=E\.back\.to;[^}]*location\.hash=to==='agent'\?'#\/agent':'#\/studio'/, 'Back too');
});

test('a sticker with an animation keeps its own editor (the Studio’s), the chat does not pretend to edit a video', () => {
  assert.match(gen, /if\(anim\)return studioEditAnim\(g\.number,index\)/);
});
