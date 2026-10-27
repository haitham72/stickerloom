const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// Item 4: execute the current chat-bar renderer and handlers, with local stubs only.
// No browser, HTTP server, provider or model is involved.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const AIU = require(require('./console_source').resolveConsoleFile(require('node:path').join(__dirname,'../../stickerloom/console/agent.js')));
const src = readConsoleFile(path.join(__dirname, '../../stickerloom/console/agent.js'), 'utf8');

function statement(marker) {return consoleStatement(src,marker);}

function fixture({ grid = '3x3', session = true, accept = true, styles = [] } = {}) {
  const settings = { grid, ask_before_spending: true, style_id: 'flat_vector' };
  const A = { sid: session ? 'S007' : null, sess: session ? { settings } : null,
    pre: '', agent: { default_style: 'flat_vector', styles } };
  const elements = { 'ag-bar': { innerHTML: '' }, 'ag-styles': { innerHTML: '' } };
  const calls = [], notices = [], saved = [], urls = [];
  let settingsPaints = 0;
  const post = async (url, body) => {
    calls.push([url, structuredClone(body)]);
    if (url === '/api/chat/sessions') {
      return { ok: true, j: { id: 'S008', settings: { ...settings } } };
    }
    assert.match(url, /^\/api\/chat\/sessions\/S00[78]\/settings$/,
      'a sheet-size click must never post a message or start generation');
    if (!accept) return { ok: false, j: { error: 'Settings are busy; try again.' } };
    return { ok: true, j: { settings: { ...A.sess.settings, ...body } } };
  };
  const ACT = {};
  const dependencies = { A, ACT, AIU, $: id => elements[id], post,
    ic: name => `<svg data-icon="${name}"></svg>`,
    setSet: () => { settingsPaints++; }, setEngine: () => {}, toast: error => notices.push(error),
    store: { set: (key, value) => saved.push([key, value]) },
    history: { replaceState: (_, __, url) => urls.push(url) } };
  const code = [
    'const styleNow=', 'function drawBar(', 'async function ensureSession(',
    'async function applyPre(', 'async function saveSet(',
    'ACT.aggridtoggle=', 'ACT.agsetgrid=',
  ].map(statement).join('\n');
  const { drawBar } = new Function(...Object.keys(dependencies),
    code + '\nreturn {drawBar};')(...Object.values(dependencies));
  return { A, ACT, elements, calls, notices, saved, urls, drawBar,
    settingsPaints: () => settingsPaints };
}

test('the sheet-size chip is one native button, defaults to 2×2, and explains both choices', () => {
  const f = fixture({ session: false });
  f.drawBar();
  const html = f.elements['ag-bar'].innerHTML;
  assert.match(html, /<button type="button" class="ag-chip ag-grid" data-act="aggridtoggle"/);
  assert.match(html, />2×2 sheet<\/button>/);
  assert.match(html, /nine \(3×3\) or four \(2×2\)\. Click to change\./);
  assert.equal((html.match(/data-act="aggridtoggle"/g) || []).length, 1);
  assert.doesNotMatch(html, /<select|data-aggrid|ag-sel/);
  assert.deepEqual(f.calls, [], 'rendering never creates a session');
});

test('each press swaps 3×3 and 2×2 through the settings route and repaints the saved choice', async () => {
  const f = fixture();
  await f.ACT.aggridtoggle({ dataset: {} });
  assert.equal(f.A.sess.settings.grid, '2x2');
  assert.match(f.elements['ag-bar'].innerHTML, />2×2 sheet<\/button>/);
  await f.ACT.aggridtoggle({ dataset: {} });
  assert.equal(f.A.sess.settings.grid, '3x3');
  assert.match(f.elements['ag-bar'].innerHTML, />3×3 sheet<\/button>/);
  assert.deepEqual(f.calls, [
    ['/api/chat/sessions/S007/settings', { grid: '2x2' }],
    ['/api/chat/sessions/S007/settings', { grid: '3x3' }],
  ]);
  assert.equal(f.A.sess.settings.ask_before_spending, true);
  assert.equal(f.settingsPaints(), 2, 'the gear is repainted from the same setting');
});

test('a saved 2×2 choice renders correctly and the gear uses the very same settings route', async () => {
  const f = fixture({ grid: '2x2' });
  f.drawBar();
  assert.match(f.elements['ag-bar'].innerHTML, />2×2 sheet<\/button>/);
  await f.ACT.agsetgrid({ dataset: { v: '3x3' } });
  assert.deepEqual(f.calls, [['/api/chat/sessions/S007/settings', { grid: '3x3' }]]);
  assert.match(f.elements['ag-bar'].innerHTML, />3×3 sheet<\/button>/);
});

test('before the first message a size press creates one session, saves the size, and sends no turn', async () => {
  const f = fixture({ session: false });
  await f.ACT.aggridtoggle({ dataset: {} });
  assert.deepEqual(f.calls, [
    ['/api/chat/sessions', {}],
    ['/api/chat/sessions/S008/settings', { grid: '3x3' }],
  ]);
  assert.equal(f.A.sid, 'S008');
  assert.equal(f.A.sess.settings.grid, '3x3');
  assert.deepEqual(f.A.sess.messages, []);
  assert.deepEqual(f.urls, ['#/agent/S008']);
  assert.deepEqual(f.saved, [['stickerloom.ai.sid', 'S008']]);
});

test('a refused settings update keeps the original size and reports the server sentence', async () => {
  const f = fixture({ accept: false });
  f.drawBar();
  await f.ACT.aggridtoggle({ dataset: {} });
  assert.equal(f.A.sess.settings.grid, '3x3');
  assert.match(f.elements['ag-bar'].innerHTML, />3×3 sheet<\/button>/);
  assert.deepEqual(f.notices, ['Settings are busy; try again.']);
  assert.equal(f.settingsPaints(), 0);
});

test('style metadata beside the grid chip is escaped, never interpreted as markup', () => {
  const malicious = { id: 'flat_vector', label: '<img onerror="bad">', hint: '" onclick="bad' };
  const f = fixture({ styles: [malicious] });
  f.drawBar();
  assert.match(f.elements['ag-bar'].innerHTML, /&lt;img onerror=&quot;bad&quot;&gt;<i/);
  assert.match(f.elements['ag-styles'].innerHTML, /&quot; onclick=&quot;bad/);
  assert.doesNotMatch(f.elements['ag-bar'].innerHTML, /<img onerror/);
  assert.doesNotMatch(f.elements['ag-styles'].innerHTML, /" onclick="bad/);
});
