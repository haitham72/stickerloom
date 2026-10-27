// effects.js holds ONE implementation of an effect for two screens (the pro particle studio, state FX, and the Studio's Particles tab, state SP): the state, the drawn particles (ideas, price, cells to
// tick), the size of the particles, the results. The whole file runs in a vm with the few globals it uses stubbed, so the markup and the state machine are checked for real.
// Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadConsole } = require('./console_vm');

const run0 = loadConsole(['effects.js']);
const J = v => (v === undefined ? v : JSON.parse(JSON.stringify(v)));       // a value from the vm has the vm's prototypes: compare it as data
const run = code => J(run0(code));
const REC = (o = {}) => ({ id: 'E001', pack_id: 'p1', pack_name: 'Fruits <b>', mode: 'sim', grid: [2, 2], status: 'READY', groups: [{ id: 'g1', subject: 'strawberry', elements: ['strawberries'], key: 'green', stickers: ['a1'], preset: {}, sprites: 'own' }],
  stickers: [{ sticker_id: 'a1', name: 'Berry', emoji: '🍓' }], results: [], video: {}, history: [], ...o });
const BATCH = (cells, o = {}) => ({ generation_id: 'G100', stage: 'sliced', busy: false, stickers: cells, verify: { sheet: [] }, ...o });
const cell = (i, o = {}) => ({ index: i, key: 'gold_bar', png: `slices/S${i}.png`, status: 'READY', review: {}, metrics: { warnings: [] }, ...o });
const html = (code, ctx = {}) => run(`(()=>{const X=fxNew('fx'),e=${JSON.stringify(ctx.rec || REC())};Object.assign(X.dr,${JSON.stringify(ctx.dr || {})});X.gen=${JSON.stringify(ctx.gen || {})};${ctx.pre || ''};return ${code}})()`);

test('every state is its own: two screens never share previews, ticks or ideas', () => {
  const a = run("fxNew('fx')"), b = run("fxNew('sp')");
  assert.notStrictEqual(a.pick, b.pick);
  assert.notStrictEqual(a.dr, b.dr);
  assert.equal(run("fxNew('sp').who"), 'sp');
  assert.equal(run("fxNew('fx').set"), null, 'the saved set of the run is read from the server, not remembered per screen');
  assert.equal(run("FXS.fx===FX"), true);
});

test('a handler finds its state from the root it sits under', () => {
  run("FXS.sp=fxNew('sp')");
  assert.equal(run("fxX({closest:()=>({dataset:{fxx:'sp'}})})===FXS.sp"), true);
  assert.equal(run("fxX({closest:()=>({dataset:{fxx:'fx'}})})===FX"), true);
  assert.equal(run("fxX({closest:()=>null})===FX"), true, 'no root: the pro screen');
});

test('the ideas of a suggest answer: strings or objects, clean, no repeats', () => {
  assert.deepEqual(run("fxOptNames({options:['gold bars',' diamonds ','gold bars','',{name:'coins'},{label:'sparks'},{text:'stars'},null,42]})"), ['gold bars', 'diamonds', 'coins', 'sparks', 'stars']);
  assert.deepEqual(run("fxOptNames({})"), []);
  assert.deepEqual(run("fxOptNames(null)"), []);
});

test('a drawn cell’s warnings are sentences, and a cell with no picture says why it cannot be used', () => {
  const w = run(`fxCellWhy(${JSON.stringify(cell(2, { metrics: { warnings: ['holes', 'chroma_risk', 'something_new'] } }))})`);
  assert.equal(w.length, 3);
  assert.match(w[0], /hole inside this particle/);
  assert.match(w[1], /close to the green-screen colour/);
  assert.equal(w[2], 'something new', 'an unknown id is still readable');
  assert.deepEqual(run(`fxCellWhy(${JSON.stringify(cell(1))})`), []);
  const flagged = run(`fxCellWhy(${JSON.stringify(cell(3, { status: 'FAILED', reason: 'inside_cell' }))})`);
  assert.match(flagged[0], /Python flagged this cell: inside cell\. Use it anyway/);
  const none = run(`fxCellWhy(${JSON.stringify(cell(4, { png: null, status: 'FAILED', reason: 'inside_cell' }))})`);
  assert.equal(none.length, 1);
  assert.match(none[0], /No picture came out of this cell \(inside cell\), so it cannot be used\./);
});

test('the words for the effect checks include the seamless-screen warning, as a warning', () => {
  assert.equal(run("FXWHY.key_is_seamless"), 'The green screen has panels or patterns, so the keying may eat parts of the reactions. Use it anyway, or make another take.');
  assert.match(run("fxWords('effect_tail_faded')"), /last frames were faded out/);
  assert.equal(run("fxWords('effect_unknown_thing')"), 'effect unknown thing');
  assert.ok(!/pieces/i.test(run("Object.values(FXWHY).join(' ')+Object.values(FXCELL).join(' ')")), 'the person reads reactions');
});

test('the drawn panel is a small state machine over the effect’s set', () => {
  const st = (rec, dr = {}, gen = {}) => html('fxDrState(X,e)', { rec, dr, gen });
  assert.equal(st(REC()), 'make', 'no set yet');
  assert.equal(st(REC({ set: { status: 'SUGGESTED', options: ['a'], grid: [2, 2] } })), 'make', 'ideas only, nothing drawn');
  assert.equal(st(REC({ set: { status: 'REQUESTED', job: 'J1' } })), 'wait');
  assert.equal(st(REC({ set: { status: 'REQUESTED', job: 'J1', live: { status: 'FAILED' } } })), 'failed');
  assert.equal(st(REC({ set: { status: 'REQUESTED', job: 'J1', live: { status: 'TIMEOUT' } } })), 'failed');
  assert.equal(st(REC({ set: { status: 'FAILED' } })), 'failed');
  const drawn = REC({ set: { status: 'DRAWN', generation: 100, picked: [1, 2] } });
  assert.equal(st(drawn), 'cut', 'the batch is not read yet');
  assert.equal(st(drawn, {}, { 100: BATCH([cell(1)], { stage: 'sheet_picked' }) }), 'cut', 'still being cut');
  assert.equal(st(drawn, {}, { 100: BATCH([cell(1)]) }), 'pick', 'cut, the person has not said which');
  assert.equal(st({ ...drawn, history: [{ decision: 'LINK' }] }, {}, { 100: BATCH([cell(1)]) }), 'pick', 'linked by Python with every cell: still the person’s pick');
  assert.equal(st({ ...drawn, history: [{ decision: 'LINK' }, { decision: 'PICK' }] }, {}, { 100: BATCH([cell(1)]) }), 'done', 'the person picked');
  assert.equal(st({ ...drawn, history: [{ decision: 'PICK' }, { decision: 'LINK' }] }, {}, { 100: BATCH([cell(1)]) }), 'pick', 'a newer sheet was linked after the pick');
  assert.equal(st(drawn, { picked: 'E001:100' }, { 100: BATCH([cell(1)]) }), 'done', 'picked in this browser');
  assert.equal(st(drawn, { picked: 'E001:100', change: true }, { 100: BATCH([cell(1)]) }), 'pick', '"Change which ones"');
  assert.equal(st(drawn, { redo: true }, { 100: BATCH([cell(1)]) }), 'make', '"Draw again"');
});

test('make: the grid, the ideas as chips, your own, and the price on its own line', () => {
  const rec = REC();
  const dr = { grid: '2x2', opts: { '2x2': { state: 'ok', list: ['gold bars', 'diamonds <i>', 'coins', 'sparks', 'stars'], by: 'vlm', notes: ['a note'] } }, chosen: ['gold bars', 'coins'], est: { '2x2|gold bars|coins': { credits: 1.4 } } };
  const h = html('fxDrawn(X,e)', { rec, dr });
  assert.match(h, /data-fxdr=make/);
  assert.match(h, /2 x 2 · 4<\/button>/);
  assert.match(h, /3 x 3 · 9<\/button>/);
  assert.match(h, /Ideas from the vision model: pick up to 4/);
  assert.match(h, /<small class=mut>a note<\/small>/);
  assert.equal((h.match(/class="fx-opt/g) || []).length, 5);
  assert.match(h, /class="fx-opt on" data-act=fxdchip data-v="gold bars" aria-pressed=true/);
  assert.match(h, /class="fx-opt" data-act=fxdchip data-v="sparks" aria-pressed=false/);
  assert.match(h, /data-v="diamonds &lt;i&gt;"/, 'names are escaped');
  assert.match(h, /2 of 4 chosen · fewer is fine/);
  assert.match(h, /<button class="btn pri" data-act=fxddraw >.*Draw 4 reactions with AI<\/button>/s, 'the button says what it does, not what it costs');
  assert.match(h, /data-fxdprice>about 1\.4 credits<\//, 'the price is on its own line above the button');
  assert.match(h, /data-fxdown/);
  assert.match(h, /Nothing is spent until you press the button/);
});

test('make: no price, no spending: the button waits for the estimate, and says why when there is none', () => {
  const base = { grid: '2x2', opts: { '2x2': { state: 'ok', list: ['a'] } }, chosen: ['a'] };
  const wait = html('fxDrawn(X,e)', { dr: base });
  assert.match(wait, /data-act=fxddraw disabled>.*Draw 4 reactions with AI<\/button>/s);
  assert.match(wait, /data-fxdprice>about to be priced…<\//);
  const none = html('fxDrawn(X,e)', { dr: { ...base, est: { '2x2|a': { credits: null, error: 'The Higgsfield CLI is not installed' } } } });
  assert.match(none, /data-act=fxddraw disabled>/);
  assert.match(none, /class=fx-warn data-fxdwhy>The Higgsfield CLI is not installed/);
  const nothing = html('fxDrawn(X,e)', { dr: { ...base, chosen: [] } });
  assert.match(nothing, /data-act=fxddraw disabled>.*Draw 4 reactions with AI<\/button>/s, 'no particle chosen: no price either');
  assert.match(html('fxDrawn(X,e)', { dr: { ...base, grid: '3x3' } }), /Draw 9 reactions with AI/);
});

test('make: loading and failing ideas never block the person (they can type their own)', () => {
  assert.match(html('fxDrawn(X,e)', { dr: { grid: '2x2', opts: {} } }), /The vision model is looking at the stickers for ideas…/);
  const err = html('fxDrawn(X,e)', { dr: { grid: '2x2', opts: { '2x2': { state: 'err', err: 'no picture' } } } });
  assert.match(err, /no picture/);
  assert.match(err, /data-act=fxdretry>Try again/);
  assert.match(err, /data-fxdown/);
});

test('pick: every cell with a picture is ticked and can be used; a cell with no picture is disabled; warnings are words', () => {
  const rec = REC({ set: { status: 'DRAWN', generation: 100, picked: [1, 2, 3, 4] } });
  const gen = { 100: BATCH([cell(1), cell(2, { metrics: { warnings: ['holes'] } }), cell(3, { status: 'FAILED', reason: 'inside_cell' }), cell(4, { png: null, status: 'FAILED', reason: 'inside_cell' })], { verify: { sheet: [{ name: 'key_is_seamless', ok: false, severity: 'WARN' }] } }) };
  const h = html('fxDrawn(X,e)', { rec, gen });
  assert.match(h, /data-fxdr=pick/);
  assert.match(h, /3 of 4 reactions drawn/);
  assert.match(h, /The green screen has panels or patterns/, 'a sheet-level warning is a sentence too');
  assert.equal((h.match(/class="fx-cell on"/g) || []).length, 3);
  assert.match(h, /class="fx-cell off"/);
  assert.match(h, /data-act=fxdcell data-i=4 aria-pressed=false disabled/);
  assert.doesNotMatch(h, /data-act=fxdcell data-i=3[^>]*disabled/, 'a flagged cell is never blocked');
  assert.match(h, /hole inside this particle/);
  assert.match(h, /Python flagged this cell: inside cell\. Use it anyway/);
  assert.match(h, /<img src="\/out\/G100\/slices\/S1\.png"/);
  assert.match(h, /data-act=fxduse >Use 3 reactions<\/button>/);
  assert.match(h, /all of them are used until you say otherwise/, 'the pro screen says what is used by default');
});

test('pick: with nothing ticked the button says 0 and is off; the Studio’s drawn flow says to tick', () => {
  const rec = REC({ set: { status: 'DRAWN', generation: 100, picked: [1] } });
  const gen = { 100: BATCH([cell(1), cell(2)]) };
  const h = html('fxDrawn(X,e)', { rec, gen, pre: "X.src='drawn';X.dr.use=new Set();X.dr.useN=100" });
  assert.match(h, /data-act=fxduse disabled>Use 0 reactions</);
  assert.match(h, /tick the ones to use, then press Use/);
  const one = html('fxDrawn(X,e)', { rec, gen, pre: "X.dr.use=new Set([2]);X.dr.useN=100" });
  assert.match(one, /Use 1 reaction</);
});

test('done: the picked reactions as thumbnails, change or draw again', () => {
  const rec = REC({ set: { status: 'DRAWN', generation: 100, picked: [2] }, history: [{ decision: 'PICK' }] });
  const gen = { 100: BATCH([cell(1), cell(2)]) };
  const h = html('fxDrawn(X,e)', { rec, gen });
  assert.match(h, /data-fxdr=done/);
  assert.match(h, /Using 1 drawn reaction/);
  assert.equal((h.match(/class=fx-pc-th /g) || []).length, 1);
  assert.match(h, /data-act=fxdchange>Change which ones/);
  assert.match(h, /data-act=fxdredo>Draw again/);
});

test('results: a warning is "Use it anyway", never a block; only a failed file cannot be used; saved under the sticker is said', () => {
  const rec = REC({ results: [
    { id: 'R001', mode: 'sim', sticker_id: 'a1', file: 'results/R001.webm', bytes: 100 * 1024, status: 'READY', checks: [] },
    { id: 'R002', mode: 'sim', sticker_id: 'a1', file: 'results/R002.webm', bytes: 100 * 1024, status: 'READY', checks: [{ id: 'effect_tail_faded', verdict: 'WARN' }, { id: 'key_is_seamless', verdict: 'WARN' }] },
    { id: 'R003', mode: 'sim', sticker_id: 'a1', file: null, bytes: 0, status: 'FAILED', checks: [{ id: 'size_budget', verdict: 'BLOCK' }] },
  ] });
  const h = html('fxResults(X,e)', { rec, pre: "X.pick.add('R001')" });
  assert.match(h, /data-act=fxpick data-id=R001>Selected</);
  assert.match(h, /data-act=fxpick data-id=R002>Use it anyway</);
  assert.match(h, /The green screen has panels or patterns/);
  assert.match(h, /class="fx-r bad"/);
  assert.match(h, /Over Telegram’s size limit\./);
  assert.doesNotMatch(h, /data-act=fxpick data-id=R003/, 'a file Telegram would reject cannot be picked');
  assert.match(h, /data-act=fxadd >Add 1 sprite clips</, 'raw clips are a secondary action inside Original sprite clips');
  assert.match(h, /saved under that sticker/);
  assert.match(html('fxResults(X,e)', { rec }), /data-act=fxpick data-id=R001>Use</, 'no warning: just Use');
  assert.equal(html('fxResults(X,e)', { rec: REC() }), '');
});

test('the body of a simulated effect: the drawn panel, the way to save it, and ONE motion section for the pack (no row, no preview per sticker); a video keeps its chips', () => {
  const pre = "LIB.packs=[{id:'p1',stickers:[{id:'a1',name:'Berry'}]}]";
  const h = html('fxBody(X,e)', { rec: REC(), pre });
  assert.match(h, /data-fxdr=make/);
  assert.doesNotMatch(h, /fx-sim|class=fx-size|data-sid=|fxpv-|data-fxp=/, 'docs/architecture.md ("Reactions"): one preview for the pack, not a row per sticker');
  assert.doesNotMatch(h, /fx-chip/, 'a simulation has no per-group chips: the particles are chosen in the drawn panel');
  assert.equal((h.match(/class="fx-g fx-mo"/g) || []).length, 1, 'one motion section for the whole run, whatever the number of stickers or groups');
  assert.match(h, /Save the reactions as a set first/, 'the burst is made from the SET, so the motion waits for it');
  assert.doesNotMatch(h, /Particles from/, 'where the particles come from is the wizard’s first steps and “Use an existing set”');
  const own = html('fxBody(X,e)', { rec: REC(), pre: "X.src='own';" + pre });
  assert.doesNotMatch(own, /fx-dr/, 'the pack-stickers flow has no drawn panel');
  assert.match(own, /data-act=fxuseset data-v=stickers>/, 'it goes straight to a set of the pack’s own stickers');
  const v = html('fxBody(X,e)', { rec: REC({ mode: 'video' }) });
  assert.match(v, /fx-chip/);
  assert.match(v, /Make the video/);
  assert.doesNotMatch(v, /fx-dr/); assert.match(v, /fx-mo/);
});

test('"Draw again" can be taken back: the particles you have stay', () => {
  const rec = REC({ set: { status: 'DRAWN', generation: 100, picked: [1] } });
  const gen = { 100: BATCH([cell(1)]) };
  const again = html('fxDrawn(X,e)', { rec, gen, dr: { redo: true } });
  assert.match(again, /data-fxdr=make/);
  assert.match(again, /Drawing again replaces the reactions you picked\./);
  assert.match(again, /data-act=fxdcancel>Keep the ones I have/);
  assert.doesNotMatch(html('fxDrawn(X,e)', { rec: REC({ set: { status: 'SUGGESTED', options: ['a'] } }) }), /fxdcancel/, 'nothing drawn yet: nothing to keep');
});

test('a name you chose stays among the chips when the ideas of another grid arrive', () => {
  const h = html('fxDrawn(X,e)', { dr: { grid: '3x3', opts: { '3x3': { state: 'ok', list: ['x', 'y'] } }, chosen: ['gold bars'], extra: ['tiny crown'] } });
  for (const n of ['x', 'y', 'gold bars', 'tiny crown']) assert.match(h, new RegExp(`data-v="${n}"`));
  assert.equal((h.match(/class="fx-opt/g) || []).length, 4);
});

test('the way to a set: a drawn sheet is kept with Use as particle set, the pack’s own stickers with their own button, and a saved run shows its motion instead', () => {
  const none = html('fxSaveBlock(X,e)', { rec: REC() });
  assert.match(none, /data-act=fxuseset data-v=stickers>Use the pack’s stickers/, 'no drawn sheet: the pack’s own stickers can be the set (free, nothing is drawn)');
  assert.match(none, /Free/);
  const drawn = REC({ set: { status: 'DRAWN', generation: 100, picked: [1, 2] } });
  const h = html('fxSaveBlock(X,e)', { rec: drawn });
  assert.match(h, /data-act=fxuseset data-v=drawn>Use in simulator · free</);
  assert.doesNotMatch(h, /Save the 2 drawn particles as a particle set/);
  assert.doesNotMatch(h, /it lives in the Library/);
  assert.match(html('fxSaveBlock(X,e)', { rec: REC({ set: { status: 'DRAWN', generation: 100, picked: [] } }) }), /data-v=stickers/, 'no picked cell: nothing durable to save, the stickers are still an option');
  const saved = "X.set={id:'P001',name:'Fruits particles',source:{kind:'drawn',generation:'G100'}}";
  assert.match(html('fxSaveBlock(X,e)', { rec: REC({ ...drawn, sets: ['P001'] }), pre: saved }), /data-act=fxopenset>Open in simulator/, 'saved run opens the editor without another import');
  assert.match(html('fxSaveBlock(X,e)', { rec: REC({ set: { status: 'DRAWN', generation: 101, picked: [1] }, sets: ['P001'] }), pre: saved }), /data-v=drawn>Use in simulator · free</, 'a newer sheet is a new set to keep');
  const body = html('fxBody(X,e)', { rec: drawn, gen: { 100: BATCH([cell(1)]) }, pre: "X.src='drawn';X.dr.picked='E001:100';LIB.packs=[{id:'p1',stickers:[{id:'a1',name:'Berry'}]}]" });
  assert.match(body, /data-act=fxuseset data-v=drawn>Use in simulator · free</, 'the finish sits in the shared body, so both wizards have it');
});

test('Motion: locked until the run is a set, then the set’s own section (the burst maker is particles.js’s)', () => {
  const locked = html('fxMotion(X,e)', { rec: REC() });
  assert.match(locked, /class="fx-g fx-mo"/);
  assert.match(locked, /Save the reactions as a set first/);
  const open = html('fxMotion(X,e)', { rec: REC({ sets: ['P001'] }), pre: "X.set={id:'P001',name:'Fruits <b> particles',n_picked:3,used_in:[{id:'p1',name:'Fruits <b>'}]}" });
  assert.match(open, /Fruits &lt;b&gt; particles/, 'names are escaped');
  assert.match(open, /P001 · 3 picked · used in Fruits &lt;b&gt;/);
  assert.doesNotMatch(open, /Save the reactions as a set first/);
});

test('the video price is on its own line too, and the estimator fills that line', () => {
  const h = html('fxVideoPanel(X,e,e.groups[0])', { rec: REC({ mode: 'video' }) });
  assert.match(h, /Make the video<\/button>/);
  assert.doesNotMatch(h, /Make the video ·/, 'no price inside the button');
  assert.match(h, /data-fxest=g1/);
});
