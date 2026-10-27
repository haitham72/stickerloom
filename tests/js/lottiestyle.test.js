// lottiestyle.js: Reaction effects, Lottie style. The Style switch, the file grid, the editor's sliders and morph targets,
// and what Save posts. Runs in the console vm (console_vm.js) with the few globals stubbed. Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadConsole } = require('./console_vm');

const FILES = "[{file:'Sad Emoji.json',name:'Sad Emoji',w:1500,h:1500,fps:25,frames:54,saved:false,settings:{},morph_targets:[]},{file:'Star.json',name:'Star',w:512,h:512,fps:30,frames:45,saved:true,settings:{count:10},morph_targets:[]}]";
const START = "{magnitude:1,speed_var:0,speed_pulse:0,pulse_hz:2,gravity:1,vortex:0,spin:1,flip:0,flip_var:0,count:28,spread:360,particle_size:1,size_var:.375,size_in_dur:0,size_out_dur:0,fade_start:0,fade_dur:0,fade_var:0,morph_at:0,morph_var:0,children:0,child_size:.5,child_size_var:0,seed:1}";

test('Library selection redraws the visible screen and mounts its player despite a hidden reaction editor', async () => {
  const r=loadConsole(['reactions.js','lottiestyle.js']);
  r(`route_='library';LS.files=${FILES};LS.start=${START};RXS.tab='lottie';
    let redraws=0,mounted=null;
    function spLibRefresh(){redraws++}
    const visible={isConnected:true,querySelector:()=>null},hidden={isConnected:true};
    DOMSTUB['s-library']={querySelector:()=>visible};
    DOMSTUB['s-reactions']={querySelector:()=>hidden};
    document.querySelector=()=>hidden;
    globalThis.window={lottie:{loadAnimation:o=>{mounted=o;return {destroy(){}}}}};
    ACT.lspick({dataset:{f:'Star.json'}});`);
  assert.equal(r('redraws'),1);
  assert.equal(r('LS.sel'),'Star.json');
  await r('Promise.resolve()');
  r('LS.doc={layers:[]};');
  await r('lsMount()');
  assert.equal(r('mounted.container===visible'),true);
  assert.equal(r('mounted.autoplay'),true);
});

test('the Style switch: Emoji style and Lottie style, both always offered', () => {
  const r = loadConsole(['reactions.js', 'lottiestyle.js']);
  const h = r('rxStyleBar()');
  assert.match(h, /data-act=rxstyle data-s=emoji/);
  assert.match(h, /data-act=rxstyle data-s=lottie/);
});

test('the grid lists every dropped file by name; a click opens the editor on the neutral start with every slider group', () => {
  const r = loadConsole(['reactions.js', 'lottiestyle.js']);
  r(`LS.files=${FILES};LS.start=${START}`);
  let h = r('lsHtml()');
  assert.match(h, /Lottie files <small class=mut>2<\/small>/);
  assert.match(h, /data-act=lspick data-f="Sad Emoji.json"/);
  assert.match(h, /title="Star · 512×512 · 30 fps · 45 frames"><span class=ls-th data-lsthumb="Star.json" aria-hidden=true><\/span><b>Star<\/b><small class=mut>1.5 s · saved<\/small>/);
  r("LS.sel='Star.json';LS.ed={settings:{...LS.start,...LS.files[1].settings},morph_targets:[]}");
  h = r('lsEditor()');
 for (const g of ['Emitter', 'Particle', 'Physics', 'Morph', 'Aux system']) assert.match(h, new RegExp(`<b>${g}`));
 for (const k of ['emitter', 'lifetime', 'gravity', 'morph_at', 'aux_at', 'children']) assert.match(h, new RegExp(`data-lso=${k}`));
 assert.match(h, /data-lso=count aria-label="Particles"><output>10<\/output>/, 'the saved value, not the start');
  assert.match(h, /data-act=lstg data-f="Sad Emoji.json"/, 'the other files are the morph targets, as thumbnail tiles');
  assert.doesNotMatch(h, /data-act=lstg data-f="Star.json"/, 'never itself');
  assert.match(h, /data-act=lsmode data-v=0[^>]*>Turns into \(1:1\)/);
  assert.match(h, /data-act=lsmode data-v=1[^>]*>Bursts into children/);
  assert.match(r('lsHtml()'), /data-lsthumb="Star.json"/, 'the file grid shows each animation');
});
