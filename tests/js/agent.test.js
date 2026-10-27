const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// The pure half of agent.js (the DOM half runs only in a browser). Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const U = require(require('./console_source').resolveConsoleFile(require('node:path').join(__dirname,'../../stickerloom/console/agent.js')));

test('the model text is never trusted as HTML', () => {
  assert.equal(U.md('<img src=x onerror=alert(1)> **hi**'), '&lt;img src=x onerror=alert(1)&gt; <b>hi</b>');
  assert.equal(U.md('a\nb'), 'a<br>b');
  assert.equal(U.esc('"\'&<>'), '&quot;&#39;&amp;&lt;&gt;');
  assert.equal(U.esc(null), '');
});

test('prices read like a person says them', () => {
  assert.equal(U.credits(null), 'free');
  assert.equal(U.credits(0), 'free');
  assert.equal(U.credits(1), 'about 1 credit');
  assert.equal(U.credits(2), 'about 2 credits');
  assert.equal(U.credits(8.25), 'about 8.3 credits');
});

test('the collapsed trace says how many steps and how it ended', () => {
  const steps = [{ kind: 'task' }, { kind: 'step' }, { kind: 'note' }, { kind: 'final', label: 'plan ready' }];
  assert.equal(U.stepSummary(steps), '3 steps · plan ready');
  assert.equal(U.stepSummary([{ kind: 'task' }]), '1 step');
  assert.equal(U.stepSummary([]), '0 steps');
});

test('the carousel index follows the scroll and never leaves the list', () => {
  assert.equal(U.nearest(0, 200, 12, 9), 0);
  assert.equal(U.nearest(212, 200, 12, 9), 1);
  assert.equal(U.nearest(230, 200, 12, 9), 1);
  assert.equal(U.nearest(99999, 200, 12, 9), 8);
  assert.equal(U.nearest(-50, 200, 12, 9), 0);
  assert.deepEqual(U.atEnds(0, 600, 1900), { start: true, end: false });
  assert.deepEqual(U.atEnds(1300, 600, 1900), { start: false, end: true });
  assert.deepEqual(U.atEnds(0, 600, 600), { start: true, end: true });
});

test('relative times', () => {
  assert.equal(U.rel(100, 130), 'just now');
  assert.equal(U.rel(0, 600), '10 min');
  assert.equal(U.rel(0, 7300), '2 h');
  assert.equal(U.rel(0, 3 * 86400), '3 d');
});

test('a message signature changes with what the page shows, and with nothing else', () => {
  const m = { id: 'm2', status: 'working', text: '', steps: [{ kind: 'task', label: 'generating teddy', status: 'running' }], cards: [], chips: [] };
  const a = U.sig(m);
  assert.equal(U.sig({ ...m }), a);
  assert.notEqual(U.sig({ ...m, steps: [...m.steps, { kind: 'step', label: 'expand prompt', status: 'done' }] }), a);
  assert.notEqual(U.sig({ ...m, status: 'done' }), a);
  const g = { type: 'generation', generation: 'G001', data: { stickers: [{ id: 'G001/S1', status: 'PENDING' }] } };
  const g2 = { type: 'generation', generation: 'G001', data: { stickers: [{ id: 'G001/S1', status: 'READY', png: '/out/x.png' }] } };
  assert.notEqual(U.sig({ ...m, cards: [g] }), U.sig({ ...m, cards: [g2] }));
});

test('the page polls while anything still moves and stops when it does not', () => {
  const pend = { type: 'generation', job: 'J001', job_status: 'CLAIMED' };
  const failed = { type: 'generation', job: 'J001', job_status: 'FAILED' };
  const cut = { type: 'generation', generation: 'G001', data: { stickers: [{ status: 'PENDING' }] } };
  const done = { type: 'generation', generation: 'G001', data: { stickers: [{ status: 'READY', anim_status: 'NOT_REQUESTED' }] } };
  const anim = { type: 'generation', generation: 'G001', animating: true, data: { stickers: [{ status: 'READY', anim_status: 'RUNNING' }] } };
  assert.equal(U.cardLive(pend), true);
  assert.equal(U.cardLive(failed), false);
  assert.equal(U.cardLive(cut), true);
  assert.equal(U.cardLive(done), false);
  assert.equal(U.cardLive(anim), true);
  assert.equal(U.cardLive({ type: 'plan' }), false);
  assert.equal(U.needPoll({ working: true, messages: [] }), true);
  assert.equal(U.needPoll({ working: false, messages: [{ cards: [done] }] }), false);
  assert.equal(U.needPoll({ working: false, messages: [{ cards: [done, pend] }] }), true);
  assert.equal(U.needPoll(null), false);
});

test('only the last assistant message holds the pending plan card', () => {
  const ms = [{ id: 'm1', role: 'user' }, { id: 'm2', role: 'assistant' }, { id: 'm3', role: 'user' }, { id: 'm4', role: 'assistant' }];
  assert.equal(U.lastBot(ms), ms[3]);          // the newest plan card is the live one
  assert.equal(U.lastBot([ms[0], ms[1], ms[2]]), ms[1]);
  assert.equal(U.lastBot([{ id: 'm1', role: 'user' }]), null);
  assert.equal(U.lastBot([]), null);
  assert.equal(U.lastBot(null), null);         // nothing to hang on: the card renders as done
});

test('the session id is read from the hash', () => {
  assert.equal(U.sid('#/agent/S012'), 'S012');
  assert.equal(U.sid('#/agent'), null);
  assert.equal(U.sid('#/library'), null);
  assert.equal(U.sid(''), null);
});

test('a running creator keeps the page polling, a stopped or finished one does not', () => {
  assert.equal(U.needPoll({ messages: [], creator_run: { status: 'running' } }), true);
  assert.equal(U.needPoll({ messages: [], creator_run: { status: 'stopped' } }), false);
  assert.equal(U.needPoll({ messages: [], creator_run: { status: 'waiting' } }), false);
  assert.equal(U.needPoll({ messages: [], creator_run: { status: 'done' } }), false);
});

const agentInfo = (over) => ({ agent: { provider: 'local', model: 'atelier-4b' }, preference: 'auto', vision: { model: 'atelier-4b' },
  agent_status: { fallback: false, reason: null }, availability: { local: { ok: true, model: 'atelier-4b', why: null }, cloud: { ok: false, model: 'gpt-4.1-mini', why: 'no key' } }, ...over });

test('the engine pill names the model when one answers', () => {
  const e = U.engine(agentInfo());
  assert.deepEqual([e.on, e.rules, e.label], [true, false, 'Auto · Local · atelier-4b']);
  assert.match(e.title, /atelier-4b \(local\)/);
  assert.equal(U.engine(agentInfo({ preference: 'local', agent: { provider: 'local', model: 'atelier-4b:2' } })).label, 'Local · atelier-4b');   // an instance suffix is not shown
  assert.equal(U.engine(agentInfo({ preference: 'cloud', agent: { provider: 'openai', model: 'gpt-4.1-mini' } })).label, 'Cloud · gpt-4.1-mini');
});

test('a local engine that cannot answer is "Rules only (local model not loaded)" with the reason in the tooltip, not just "Rules only"', () => {
  const why = 'LM Studio is running but the model could not answer: No models loaded. Load atelier-4b in LM Studio (or turn on Just-in-Time loading)';
  const e = U.engine(agentInfo({ agent_status: { fallback: true, reason: why }, availability: { local: { ok: false, model: 'atelier-4b', why }, cloud: { ok: false } } }));
  assert.deepEqual([e.on, e.rules, e.label], [false, true, 'Rules only (local model not loaded)']);
  assert.ok(e.title.includes('No models loaded'));
  const none = U.engine(agentInfo({ agent: { provider: 'none', model: null }, agent_status: { fallback: true, reason: why }, availability: { local: { ok: false, why }, cloud: { ok: false } } }));
  assert.equal(none.label, 'Rules only (local model not loaded)');                                    // the server is up but nothing answers: the same words
  const cloudChosen = U.engine(agentInfo({ preference: 'cloud', agent: { provider: 'none', model: null }, agent_status: { fallback: true, reason: 'no OPENAI_API_KEY in .env' }, availability: { local: { ok: false }, cloud: { ok: false } } }));
  assert.equal(cloudChosen.label, 'Rules only');                                                       // no local engine is wanted: nothing to say about it
  assert.ok(cloudChosen.title.includes('no OPENAI_API_KEY'));
  const lastCallFailed = U.engine(agentInfo({ agent_status: { fallback: true, reason: 'the last call to the model failed: timed out' } }));
  assert.deepEqual([lastCallFailed.rules, lastCallFailed.label], [true, 'Rules only']);                // the probe answers but the last real call did not: rules, with that reason
  assert.equal(U.engine(null).label, '…');
});

test('the model dropdown line says what runs, or why the local model cannot answer in the server\'s last sentence', () => {
  assert.equal(U.modelNote({ ok: true, current: 'atelier-4b' }), 'now: atelier-4b');
  assert.equal(U.modelNote({ ok: false, why: 'LM Studio is running but the model could not answer: No models loaded. Load atelier-4b in LM Studio (or turn on Just-in-Time loading)' }),
    'Local model not loaded: Load atelier-4b in LM Studio (or turn on Just-in-Time loading)');
  assert.equal(U.modelNote({ ok: false, why: 'LM Studio is not answering at http://localhost:1234/v1: start it and load x' }), 'Local model not loaded: LM Studio is not answering at http://localhost:1234/v1: start it and load x');
  assert.equal(U.modelNote(null), '');
});

test('a creator card is rebuilt when its run moves, a blocked sheet when its check appears', () => {
  const m = (run) => ({ id: 'm', status: 'done', text: '', steps: [], chips: [], cards: [{ type: 'creator', run }] });
  assert.notEqual(U.sig(m({ step: 'cut', status: 'running', updated: 1 })), U.sig(m({ step: 'look', status: 'running', updated: 2 })));
  assert.equal(U.sig(m({ step: 'cut', status: 'running', updated: 1 })), U.sig(m({ step: 'cut', status: 'running', updated: 1 })));
});

test('the chat draws the two particle cards: the plan with its price, and the started set with a way to the pack’s studio; names are escaped', () => {
  const src = readConsoleFile(require('node:path').join(__dirname, '..', '..', 'stickerloom', 'console', 'agent.js'), 'utf8');
  const plan = consoleStatement(src, "if(c.type==='particles_plan')");
  assert.match(plan, /AIU\.credits\(c\.estimate\)/, 'the price is on the plan');
  assert.match(plan, /AIU\.esc\(n\)/);
  assert.match(plan, /The price is shown before anything is spent/);
  const set = consoleStatement(src, "if(c.type==='particles')");
  assert.match(set, /#\/pack\/\$\{AIU\.esc\(c\.pack_id\)\}/);
  assert.match(set, /AIU\.esc\(c\.name\|\|c\.set\)/);
});
