// The project map (projmap.js, redesign phase 4): every control calls the action it always called; nothing new spends. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadConsole } = require('./console_vm');

const EXTRA = `
function histCol(){}
function gview(){return '<div class=ghead></div>'}
async function histLoad(){}
const gdOn=()=>false,gdHide=()=>{},saveSes=()=>{},PVS=new Map(),PVON=new Set(),ANIM=new Set();
`;
const MAP = {
  project: { id: 'G104', title: 'Superhero paris', request: 'superhero paris' }, current: 'G105',
  batches: [
    { root: 'G104', title: 'Superhero paris', picked: 'G105', preset: null, sheets: [
      { id: 'G104', number: 104, n: 1, label: 'First try', relation: null, prompt: 'superhero paris', prompt_changed: false, status: 'ready', thumb: '/out/G104/source/sheet.png',
        counts: { stickers: 9, ready: 9, blocked: 0, animated: 0 }, videos: [], jobs: [] },
      { id: 'G105', number: 105, n: 2, label: 'Edited', relation: 'edit', prompt: 'superhero paris', prompt_changed: false, status: 'ready', thumb: '/out/G105/source/sheet.png',
        counts: { stickers: 9, ready: 8, blocked: 1, animated: 8 }, jobs: [],
        videos: [{ id: 'A1', status: 'SUPERSEDED', used: false, slots: 9, thumb: '/out/G105/video_sheet/A1/sheet.png' }, { id: 'A2', status: 'SLICED', used: true, slots: 9, thumb: null }] }] },
    { root: 'G107', title: 'Superhero paris', picked: 'G107', preset: 'social-v1', sheets: [
      { id: 'G107', number: 107, n: 1, label: 'First try', relation: null, prompt: 'x', prompt_changed: false, status: 'making', thumb: null,
        counts: { stickers: 0, ready: 0, blocked: 0, animated: 0 }, videos: [], jobs: [{ id: 'J9', kind: 'sheet', status: 'CLAIMED' }] }] }],
};

function load() {
  const run = loadConsole(['projmap.js'], { studio: true, extra: EXTRA });
  run(`SES.gens=[105];PM.data=${JSON.stringify(MAP)};PM.id=105;`);
  return run;
}

test('the open sheet carries its menu, its videos and the branches; each is an existing action', () => {
  const h = load()('pmHTML()');
  assert.match(h, /<b>Superhero paris<\/b>/);
  assert.match(h, /class="pm-s ready on"/);
  for (const act of ['gmain data-g=105', 'hleave data-id=105', 'data-act=ggroup', 'gdel data-g=105',
    'gapick data-g=105 data-a=A1', 'garm data-g=105 data-a=A1', 'gtab data-t=anim', 'pgsheet data-g=105', 'gtab data-t=plan', 'data-act=gnext', 'hunpack data-id=107'])
    assert.ok(h.includes('data-act=' + act) || h.includes(act), act);
  assert.doesNotMatch(h, /gapick data-g=105 data-a=A2/, 'the video in use has no Use button');
  assert.match(h, /<b>Video A2<\/b><small>in use/);
});

test('another sheet of the same batch switches with ggen; a sheet of another batch opens with pmopen', () => {
  const h = load()('pmHTML()');
  assert.match(h, /data-act=ggen data-g=105 data-to=104/);
  assert.match(h, /data-act=pmopen data-g=107/);
  assert.match(h, /A sheet is being made…/, 'a job in flight shows on its sheet');
  assert.doesNotMatch(h, /data-act=gdel data-g=104/, 'only the open sheet has the destructive menu');
});

test('the breadcrumb names the project, the batch and the sheet', () => {
  const c = load()('pmCrumbs()');
  assert.match(c, /data-act=pmlist[^>]*>Superhero paris<\/button><i>›<\/i><span>Batch 1<\/span><i>›<\/i><span>Edited<\/span>/);
});
