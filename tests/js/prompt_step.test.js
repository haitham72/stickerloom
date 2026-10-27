const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// The Studio's "Generate prompt" step (docs/architecture.md, 2026-10-03): the button reads Generate prompt and spends nothing; it opens the Prompt editor BEFORE any batch exists (no G### is allocated);
// "Generate sheet" inside it shows the Higgsfield price on its own line and is the only click that spends; the edited text is forwarded to the live sheet route as `sheet_prompt`; edits survive
// re-renders and a reload; with the AI enhancer On the plan is written through the engine the person chose (POST /api/plan {ai:true}), a failed model shows its reason in the step and the built-in plan, and nothing is ever refused. The statements are read out of generate.js /
// live.js and run with the few globals they use stubbed (nothing here touches a DOM, a server or a provider). Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = f => readConsoleFile(path.join(__dirname, '..', '..', 'stickerloom', 'console', f), 'utf8');
const gen = read('generate.js'), composer = read('composer.js'), live = read('live.js');

function statement(src,marker) {return consoleStatement(src,marker);}

const PLAN = {
  template_id: 'sheet_v1', template_version: 3, sheet_prompt: 'SHEET TEMPLATE', video_prompt: 'VIDEO TEMPLATE',
  slots: { subject_description: 'a bear', key_colour: 'green', style_id: 'flat_vector', cells: [
    { pos: 1, label: 'happy bear', tags: ['bear_happy', 'a'], emoji: 'x', motion: 'jumps' }, { pos: 2, label: 'sad bear', tags: ['bear_sad', 'b'], emoji: 'y' }] },
  stickers: [{ index: 1, emoji: 'x', key: 'happy_bear', prompt: 'cell one', tags: ['a'] }, { index: 2, emoji: 'y', key: 'sad_bear', prompt: 'cell two', tags: ['b'] }],
};

const flush = () => new Promise(r => setImmediate(r));

function load({ stored = null, connected = true, price = 12.5, ai = false, planOk = true, gate = null, planAi = 'ok', gai = { configured: true, provider: 'local', model: 'atelier-4b:2' } } = {}) {
  const log = { posts: [], says: [], toasts: [], ticks: 0, lcost: 0, painted: [], store: {}, removed: [], started: [] };
  const DOM = { prompt: { value: 'spiderman in paris' }, go: { disabled: false } };
  const sb = {
    log, DOM, state: { connected, price, planOk, gate, planAi }, GAI: gai,
    esc: s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    ic: n => `<svg data-i=${n}></svg>`,
    $: id => DOM[id] || null,
    say: t => log.says.push(t), toast: (m, bad) => log.toasts.push([m, !!bad]),
    SES: { prompt: '', gens: [3], off: [], pack: '' }, GM: new Map(), GS: { tab: 'stickers', outline: 12 },
    CP: { refs: [], ai }, aiOn: () => sb.CP.ai,
    LIVE: { style: 'flat_vector', loop: false, est: {}, jobs: [], hf: { credits: 100 } },
    liveReadyNow: () => sb.state.connected,
    lsel: () => ({ model: { id: 'nano' }, sel: { id: 'nano', options: { res: '2k' } } }),
    lfind: () => ({ id: 'nano', label: 'Nano' }),
    lcost: async () => { log.lcost++; if (sb.state.gate) await sb.state.gate; const k = 'imagenano' + JSON.stringify({ res: '2k' }); if (sb.LIVE.est[k] === undefined) sb.LIVE.est[k] = sb.state.price; return sb.LIVE.est[k]; },
    fcr: n => String(n), ikey: () => 'k1', egDirty: () => false, fillNow: () => 0.74, lsave: () => {}, drawLive: () => {}, liveTick: () => {}, refreshHf: () => {},
    post: async (url, body) => {
      log.posts.push([url, body]);
      if (url === '/api/plan') {
        if (!sb.state.planOk) return { ok: false, j: { error: 'nope' } };
        const j = JSON.parse(JSON.stringify(PLAN));
        if (body.ai && sb.state.planAi === 'ok') Object.assign(j, { expanded_by: 'ai', expand_model: 'atelier-4b', sheet_prompt: 'AI SHEET TEXT' });
        else if (body.ai && sb.state.planAi === 'error') Object.assign(j, { expanded_by: 'deterministic', expand_error: 'No AI key (add OPENAI_API_KEY to .env): using the built-in sets.' });
        else if (body.ai && sb.state.planAi === 'transformation') Object.assign(j, { expanded_by: 'transformation' });
        else j.expanded_by = 'deterministic';
        return { ok: true, j };
      }
      if (url === '/api/plan/more') return { ok: true, j: { max: 4, plans: Array.from({ length: body.n }, (_, i) => ({ ...JSON.parse(JSON.stringify(PLAN)), prompt: `bear more ${i + 1}` })) } };
      return { ok: true, j: { job: 'J1', model: 'nano', estimate: 12.5, expanded_by: null } };
    },
    postWait: async (url, body) => sb.post(url, body),
    tick: async () => { log.ticks++; },
    cpDrawBar: () => {}, gstore: (k, v) => { log.store[k] = v; },
    localStorage: { removeItem: k => log.removed.push(k) },
    document: {
      querySelector: sel => sel === '[data-pdfoot=sheet]' ? { querySelector: () => ({}), replaceWith: n => log.painted.push(n) } : null,
      createElement: () => ({ set innerHTML(h) { this.firstChild = h; } }),
    },
    stored,
  };
  const body = [
    'let glast=\'\'', statement(gen, 'let GD=null'),
    stored ? 'GD=JSON.parse(stored)' : '',
    statement(gen, 'const PD={}'), statement(gen, 'if(GD&&GD.edits)Object.assign(PD,GD.edits)'),
    statement(gen, 'const pdKey='), statement(gen, 'const sentVideoPrompt='), statement(gen, 'const pdText='), statement(gen, 'const pdBase='), statement(gen, 'const pdCustom='),
    statement(gen, 'const copyBox='), statement(gen, 'function pdFoot(g,kind)'), statement(gen, 'function planView(g)'),
    statement(gen, 'const gdOn='), statement(gen, 'function gdSave('), statement(gen, 'function gdHide('), statement(gen, 'function gdDrop('), statement(gen, 'const gdKey='),
    statement(gen, 'const gdNb='), statement(gen, 'ACT.gdnb='), statement(gen, 'function gdPriceLine('), statement(gen, 'function gdFoot('), statement(gen, 'function gdPaint('), statement(gen, 'async function gdPrice('), statement(gen, 'function gdView('),
    statement(gen, 'ACT.gdtab='), statement(gen, 'ACT.gddiscard='), statement(gen, 'ACT.gdpriceretry='), statement(gen, 'const gdEngineName='), statement(gen, 'ACT.gprompt='), statement(gen, 'ACT.gpromptfree='), statement(gen, 'async function gdPlan('), statement(gen, 'function gdNote('), statement(gen, 'ACT.gdsheet='), statement(gen, 'async function gdMore('),
    statement(live, 'const cpGrid='), statement(live, 'async function liveStart('),
  ].join('\n');
  const f = new Function(...Object.keys(sb), 'const ACT={};\n' + body + '\nreturn {ACT,PD,pdText,pdCustom,gdOn,gdView,gdFoot,gdSave,gdPrice,gdNote,liveStart,get GD(){return GD},get GDP(){return GDP}};')(...Object.values(sb));
  return Object.assign(sb, { api: f });
}

const sheetPosts = h => h.log.posts.filter(([u]) => u === '/api/live/sheet');
const edit = (h, text) => { h.api.PD['draft:sheet'] = text; h.api.gdSave(); };

test('the Studio button reads Generate prompt and no Generate button is left on the composer or the plain form', () => {
  assert.match(composer, /id=go class=cp-go data-act=gprompt[^>]*>Generate prompt /);
  assert.match(gen, /<button id=go class="btn pri gbig" data-act=gprompt>Generate prompt<\/button>/);
  assert.doesNotMatch(composer, /data-act=ggo/, 'the composer bar has no button that starts a sheet at once');
  assert.doesNotMatch(gen, /<button[^>]*data-act=ggo/, 'nor does the plain form');
});

test('Enter keeps its old behaviour: the box still calls ACT.ggo, which still starts the paid sheet', () => {
  assert.match(composer, /p\.onkeydown=e=>\{if\(e\.key==='Enter'&&!e\.shiftKey\)\{e\.preventDefault\(\);ACT\.ggo\(\)\}\}/);
  assert.match(gen, /\$\('prompt'\)\.onkeydown=e=>\{if\(e\.key==='Enter'\)\{e\.preventDefault\(\);ACT\.ggo\(\)\}\}/);
  assert.match(composer, /liveStart\('sheet',\{prompt:p,ai:aiOn\(\),refs:/);
});

test('Generate prompt (enhancer Off) is free: one /api/plan with ai:false, no sheet route, no batch allocated', async () => {
  const h = load();
  await h.api.ACT.gprompt();
  assert.deepEqual(h.log.posts.map(p => p[0]), ['/api/plan'], 'nothing but the free planner is called');
  assert.equal(h.log.posts[0][1].ai, false);
  assert.equal(h.log.posts[0][1].prompt, 'spiderman in paris');
  assert.equal(sheetPosts(h).length, 0, 'no /api/live/sheet call before the click');
  assert.equal(h.api.GD.number, 'draft', 'the draft is not a G###');
  assert.deepEqual(h.SES.gens, [3], 'the session is untouched: no batch was allocated or opened');
  assert.equal(h.GM.size, 0);
  assert.equal(h.api.gdOn(), true);
  assert.match(h.api.gdView(), /Prompt, before the sheet/);
  assert.match(h.api.gdView(), /SHEET TEMPLATE/, 'the existing editor shows the sheet prompt');
  assert.match(h.api.gdView(), /data-pd=sheet data-g=draft/, 'it is the Prompt tab\'s copyBox textarea, not a second editor');
  assert.match(h.api.gdView(), /No batch exists yet/);
});

test('an empty request or a failed plan makes no draft and no spend', async () => {
  const h = load({ planOk: false });
  await h.api.ACT.gprompt();
  assert.equal(h.api.GD, null);
  assert.deepEqual(h.log.toasts[0], ['nope', true]);
  const e = load();
  e.DOM.prompt.value = '   ';
  await e.api.ACT.gprompt();
  assert.equal(e.log.posts.length, 0);
  assert.match(e.log.says[0], /Write what you want first/);
});

test('Generate sheet shows the Higgsfield price on its own line, outside the button, and is off until the price is known', async () => {
  let release;
  const h = load({ gate: new Promise(r => { release = r; }) });
  await h.api.ACT.gprompt();
  const before = h.api.gdFoot(h.api.GD, 'sheet');
  assert.match(before, /<div class=mut id=gdprice>Higgsfield sheet price: checking…<\/div>/);
  assert.match(before, /data-act=gdsheet disabled/, 'no price shown, no spend');
  release();
  await flush();
  const after = h.api.gdFoot(h.api.GD, 'sheet');
  assert.match(after, /<div class=mut id=gdprice>Higgsfield sheet price: ◈ 12.5 credits<\/div>/);
  assert.doesNotMatch(after, /data-act=gdsheet disabled/);
  const btn = after.match(/<button[^>]*data-act=gdsheet[^>]*>([^<]*)<\/button>/)[1];
  assert.equal(btn, 'Generate sheet', 'the price is not inside the button');
  assert.equal(h.log.painted.length, 1, 'the footer repaints by itself when the price arrives');
});

test('a second Generate prompt, with the price already known, leaves Generate sheet clickable (it was drawn disabled while busy)', async () => {
  const h = load();
  await h.api.ACT.gprompt();
  await flush();
  const before = h.log.painted.length;
  await h.api.ACT.gprompt();
  await flush();
  assert.ok(h.log.painted.length > before, 'repainted after the second plan, not left as drawn while busy');
  const last = h.log.painted[h.log.painted.length - 1];
  assert.match(last, /Higgsfield sheet price: ◈ 12.5 credits/);
  assert.doesNotMatch(last, /data-act=gdsheet disabled/, 'the footer is repainted once the busy flag is cleared');
});

test('an unavailable price offers a Retry that asks again; a click without a price spends nothing', async () => {
  const h = load({ price: null });
  await h.api.ACT.gprompt();
  await flush();
  const foot = h.api.gdFoot(h.api.GD, 'sheet');
  assert.match(foot, /Higgsfield sheet price: unavailable<\/div>/);
  assert.match(foot, /data-act=gdpriceretry>Retry the price<\/button>/);
  assert.match(foot, /data-act=gdsheet disabled/);
  await h.api.ACT.gdsheet({ disabled: false });
  assert.equal(sheetPosts(h).length, 0, 'never spend against a price that was not shown');
  assert.match(h.log.toasts.at(-1)[0], /price is not known/);
  const calls = h.log.lcost;
  h.state.price = 9;
  h.api.ACT.gdpriceretry();
  await flush();
  assert.equal(h.log.lcost, calls + 1, 'the cached failure was dropped and the price asked again');
  assert.match(h.api.gdFoot(h.api.GD, 'sheet'), /◈ 9 credits/);
});

test('without Higgsfield the sheet button is off and says why', async () => {
  const h = load({ connected: false });
  await h.api.ACT.gprompt();
  const foot = h.api.gdFoot(h.api.GD, 'sheet');
  assert.match(foot, /unavailable, Higgsfield is not connected/);
  assert.match(foot, /data-act=gdsheet disabled/);
  await h.api.ACT.gdsheet({ disabled: false });
  assert.equal(sheetPosts(h).length, 0);
});

test('Batches: one Generate makes this batch and the next ones, each its own sheet, all joining one session', async () => {
  const h = load();
  await h.api.ACT.gprompt();
  await flush();
  h.api.GD.batches_max = 4;
  assert.match(h.api.gdFoot(h.api.GD, 'sheet'), /data-act=gdnb data-n=4/);
  assert.match(h.api.gdFoot(h.api.GD, 'sheet'), />Generate sheet</);
  h.api.ACT.gdnb({ dataset: { n: '3' } });
  const foot = h.api.gdFoot(h.api.GD, 'sheet');
  assert.match(foot, />Generate 3 sheets</);
  assert.match(foot, /3 sheets · ◈ 37.5 credits in all/, 'the total is shown before the click');
  await h.api.ACT.gdsheet({ disabled: false });
  const more = h.log.posts.find(([u]) => u === '/api/plan/more');
  assert.equal(more[1].n, 2);
  const sheets = sheetPosts(h);
  assert.equal(sheets.length, 3);
  assert.deepEqual(sheets.map(([, b]) => b.prompt), ['spiderman in paris', 'bear more 1', 'bear more 2']);
  const runs = h.LIVE.jobs.map(j => [j.mode, j.run]);
  assert.equal(new Set(runs.map(r => r[1])).size, 1, 'one run key');
  assert.ok(runs.every(r => r[0] === 'pack' && r[1]));
});

test('Next batch counts from the session: 1 batch on screen offers 2-4, 3 batches only 4', async () => {
  const h = load();
  await h.api.ACT.gprompt();
  await flush();
  h.SES.gens = [7];
  Object.assign(h.api.GD, { next: true, existing: 1, batches_max: 3, gens: '7' });
  let foot = h.api.gdFoot(h.api.GD, 'sheet');
  assert.deepEqual([...foot.matchAll(/data-act=gdnb data-n=\d+ aria-pressed=\w+ title="[^"]*">(\d)</g)].map(m => m[1]), ['2', '3', '4']);
  assert.match(foot, /This session has 1 batch/);
  h.api.ACT.gdnb({ dataset: { n: '3' } });                     // the "4" button: batches 2, 3 and 4
  assert.match(h.api.gdFoot(h.api.GD, 'sheet'), />Generate 3 sheets</);
  await h.api.ACT.gdsheet({ disabled: false });
  const more = h.log.posts.find(([u]) => u === '/api/plan/more');
  assert.deepEqual([more[1].n, more[1].gens], [2, ['7']], 'the session goes along, so its own grids are skipped');
  assert.ok(h.LIVE.jobs.every(j => j.mode === 'next'), 'they join the session on screen');
  const h3 = load();
  await h3.api.ACT.gprompt();
  await flush();
  Object.assign(h3.api.GD, { next: true, existing: 3, batches_max: 1, gens: '7,8,9' });
  foot = h3.api.gdFoot(h3.api.GD, 'sheet');
  assert.deepEqual([...foot.matchAll(/data-act=gdnb data-n=\d+ aria-pressed=\w+ title="[^"]*">(\d)</g)].map(m => m[1]), ['4']);
});

test('one batch stays exactly as before: no plan/more call, no run key', async () => {
  const h = load();
  await h.api.ACT.gprompt();
  await flush();
  await h.api.ACT.gdsheet({ disabled: false });
  assert.equal(h.log.posts.filter(([u]) => u === '/api/plan/more').length, 0);
  assert.equal(sheetPosts(h).length, 1);
  assert.equal(h.LIVE.jobs[0].mode, undefined);
});

test('only the Generate sheet click spends, and it forwards the edited text as sheet_prompt to the live sheet route', async () => {
  const h = load();
  await h.api.ACT.gprompt();
  await flush();
  edit(h, 'My own sheet wording');
  assert.equal(h.api.pdCustom(h.api.GD, 'sheet'), true);
  assert.match(h.api.gdFoot(h.api.GD, 'sheet'), /Generate sheet with my prompt/);
  assert.equal(sheetPosts(h).length, 0, 'editing spends nothing');
  const el = { disabled: false };
  await h.api.ACT.gdsheet(el);
  const [url, body] = sheetPosts(h)[0];
  assert.equal(url, '/api/live/sheet');
  assert.equal(body.sheet_prompt, 'My own sheet wording');
  assert.equal(body.prompt, 'spiderman in paris');
  assert.equal(body.ai, false, 'the draft never asks the paid enhancer');
  assert.equal(body.style_id, 'flat_vector');
  assert.equal(body.grid, '2x2');
  assert.equal(sheetPosts(h).length, 1, 'exactly one paid request');
  assert.equal(h.api.GD, null, 'the draft is consumed once the sheet is queued');
  assert.ok(h.log.removed.includes('stickerloom.prompt-draft'));
});

test('an unedited draft sends no sheet_prompt (the template route); a refused start keeps the draft and its edits', async () => {
  const h = load();
  await h.api.ACT.gprompt();
  await flush();
  await h.api.ACT.gdsheet({ disabled: false });
  assert.equal(sheetPosts(h).length, 1);
  assert.equal('sheet_prompt' in sheetPosts(h)[0][1], false);
  const refuse = load();
  await refuse.api.ACT.gprompt();
  await flush();
  edit(refuse, 'my words');
  refuse.LIVE.hf.credits = 1;                      // not enough credits: liveStart refuses before any request
  await refuse.api.ACT.gdsheet({ disabled: false });
  assert.equal(sheetPosts(refuse).length, 0);
  assert.notEqual(refuse.api.GD, null, 'a refused start keeps the prompt');
  assert.equal(refuse.api.pdText(refuse.api.GD, 'sheet'), 'my words', 'and its edits');
});

test('edits persist across re-renders and a reload; Reset and a new plan clear them', async () => {
  const h = load();
  await h.api.ACT.gprompt();
  edit(h, 'kept wording');
  assert.match(h.api.gdView(), /kept wording/);
  assert.match(h.api.gdView(), /kept wording/, 'a second render shows the same text');
  const saved = h.log.store['stickerloom.prompt-draft'];
  assert.ok(saved, 'the draft is stored for a reload');
  const again = load({ stored: saved });
  assert.equal(again.api.pdText(again.api.GD, 'sheet'), 'kept wording', 'the editor text is back after a reload');
  assert.equal(again.api.pdCustom(again.api.GD, 'sheet'), true);
  again.SES.gens = [3];
  again.api.GD.active = true;
  again.api.GD.gens = '3';
  assert.equal(again.api.gdOn(), true);
  await again.api.ACT.gprompt();                  // a new request writes a new plan: the old edit is replaced, not carried over
  assert.equal(again.api.pdText(again.api.GD, 'sheet'), 'SHEET TEMPLATE');
});

test('another batch taking the Studio hides the draft, and the composer can bring it back; Discard forgets it', async () => {
  const h = load();
  await h.api.ACT.gprompt();
  assert.equal(h.api.gdOn(), true);
  h.SES.gens = [9];                                // a finished sheet or an opened batch
  assert.equal(h.api.gdOn(), false);
  assert.match(composer, /\$\{GD&&!gdOn\(\)\?'<button class="btn sm" data-act=gdtab data-t=plan/);
  h.api.ACT.gdtab({ dataset: { t: 'plan' } });
  assert.equal(h.api.gdOn(), true);
  h.api.ACT.gddiscard();
  assert.equal(h.api.GD, null);
  assert.ok(h.log.removed.includes('stickerloom.prompt-draft'));
});

test('enhancer On: Generate prompt plans through the chosen engine (ai:true), shows the AI text, and never touches the sheet route', async () => {
  const h = load({ ai: true });
  await h.api.ACT.gprompt();
  assert.deepEqual(h.log.posts.map(p => p[0]), ['/api/plan'], 'one plan call, nothing else');
  assert.equal(h.log.posts[0][1].ai, true);
  assert.equal(sheetPosts(h).length, 0, 'planning never spends: no /api/live/sheet');
  assert.match(h.log.says[0], /Asking the AI enhancer \(local · atelier-4b\)/, 'a slow local model is announced while it thinks');
  assert.equal(h.log.says.at(-1), '', 'and the line is cleared when the plan is there');
  assert.equal(h.api.GD.number, 'draft');
  assert.equal(h.api.GD.plan_source, 'the AI enhancer (atelier-4b)');
  const view = h.api.gdView();
  assert.match(view, /AI SHEET TEXT/, 'the step shows what the model wrote');
  assert.match(view, /written by the AI enhancer \(atelier-4b\) · no Higgsfield credits spent/);
  assert.match(view, /Written by the AI enhancer \(atelier-4b\)\./);
  assert.match(view, /data-act=gpromptfree>Use the built-in prompt instead<\/button>/, 'a quiet way out');
  assert.doesNotMatch(view + h.log.says.join(' '), /price decision|no confirmed price|still pending/);
});

test('enhancer On, the model fails or has no key: the reason is one line in the step, the built-in plan is shown, nothing is blocked', async () => {
  const h = load({ ai: true, planAi: 'error' });
  await h.api.ACT.gprompt();
  assert.equal(h.log.posts.length, 1);
  assert.equal(h.api.GD.number, 'draft', 'the step opens anyway');
  assert.match(h.api.gdView(), /SHEET TEMPLATE/, 'the built-in plan is what is shown');
  const note = h.api.gdNote(h.api.GD);
  assert.match(note, /class="gdnote bad"/);
  assert.match(note, /The AI enhancer did not answer: No AI key \(add OPENAI_API_KEY to \.env\): using the built-in sets\. This is the built-in prompt instead\./);
  assert.match(h.api.gdView(), /written by the free built-in planner/);
  assert.equal(h.CP.ai, true, 'the saved enhancer setting is untouched');
  assert.equal(sheetPosts(h).length, 0);
  const t = load({ ai: true, planAi: 'transformation' });
  await t.api.ACT.gprompt();
  assert.match(t.api.gdNote(t.api.GD), /changes one character, so the built-in template wrote the cells/);
  const off = load();
  await off.api.ACT.gprompt();
  assert.equal(off.api.gdNote(off.api.GD), '', 'enhancer Off: no note at all');
});

test('"Use the built-in prompt instead" plans again with ai:false and leaves the enhancer setting alone', async () => {
  const h = load({ ai: true });
  await h.api.ACT.gprompt();
  await h.api.ACT.gpromptfree();
  assert.deepEqual(h.log.posts.map(p => [p[0], p[1].ai]), [['/api/plan', true], ['/api/plan', false]]);
  assert.match(h.api.gdView(), /SHEET TEMPLATE/);
  assert.equal(h.api.gdNote(h.api.GD), '');
  assert.equal(h.CP.ai, true);
  assert.equal('stickerloom.ai' in h.log.store, false, 'nothing was written to the saved enhancer setting');
});

test('an AI-written draft is sent as shown (ai:false, the text as sheet_prompt): the enhancer is never asked a second time', async () => {
  const h = load({ ai: true });
  await h.api.ACT.gprompt();
  await flush();
  assert.equal(h.api.pdCustom(h.api.GD, 'sheet'), false, 'unedited');
  assert.doesNotMatch(h.api.gdView(), /built-in set/, 'the old disclaimer about tags and emoji is gone');
  assert.match(h.api.gdView(), /text is sent exactly as shown/);
  await h.api.ACT.gdsheet({ disabled: false });
  const [url, body] = sheetPosts(h)[0];
  assert.equal(url, '/api/live/sheet');
  assert.equal(body.ai, false);
  assert.equal(body.sheet_prompt, 'AI SHEET TEXT');
  assert.equal(h.log.posts.filter(p => p[0] === '/api/plan').length, 1, 'one model call in all');
  // the previewed plan travels with the click, so the batch keeps the cells, tags and emoji the person saw (the server validates it again)
  assert.deepEqual(body.plan, { template_id: 'sheet_v1', expanded_by: 'ai', expand_model: 'atelier-4b', slots: { subject_description: 'a bear', key_colour: 'green', cells: PLAN.slots.cells } });
});

test('a built-in draft sends its previewed plan too; liveStart puts `plan` in the sheet request only when given', async () => {
  const h = load();
  await h.api.ACT.gprompt();
  await flush();
  await h.api.ACT.gdsheet({ disabled: false });
  const body = sheetPosts(h)[0][1];
  assert.equal(body.ai, false);
  assert.equal(body.plan.expanded_by, 'deterministic');
  assert.deepEqual(body.plan.slots.cells.map(c => c.tags[0]), ['bear_happy', 'bear_sad']);
  assert.match(live, /\.\.\.\(ctx\.plan\?\{plan:ctx\.plan\}:\{\}\)/);
  assert.doesNotMatch(gen, /come from the built-in set/);
});

test('a failed plan with the enhancer On makes no draft, clears the waiting line and reports the error', async () => {
  const h = load({ ai: true, planOk: false });
  await h.api.ACT.gprompt();
  assert.equal(h.api.GD, null);
  assert.equal(h.log.says.at(-1), '');
  assert.deepEqual(h.log.toasts[0], ['nope', true]);
});

test('the sheet start of a draft never asks the enhancer, and the old "pending price" refusal is gone from the code', () => {
  const block = ['async function gdPlan(', 'ACT.gdsheet=', 'async function gdMore('].map(m=>statement(gen,m)).join('\n');
  assert.ok(block.length > 500);
  const sheet = statement(gen, 'ACT.gdsheet=');
  assert.match(sheet, /ai:false/);
  assert.doesNotMatch(sheet, /ai:\s*true|ai:\s*aiOn|expand_ai/);
  assert.doesNotMatch(block, /CP\.ai\s*=|gstore\('stickerloom\.ai'/, 'the enhancer setting is not written from the prompt step');
  assert.match(statement(gen, 'async function gdPlan('), /ai:!!ai/, 'the planner gets the enhancer flag as the caller decided it');
  assert.doesNotMatch(gen + composer + live, /price decision|no confirmed price|still pending|Generate a free built-in prompt/);
});
