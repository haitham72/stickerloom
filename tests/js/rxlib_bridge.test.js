// #/reactions has three sub-tabs: Emoji Style | Lottie Style | Packs, where Packs is the
// Library's Reactions dumped as is — and Library Reactions shows the same three tabs. Runs in the console vm
// (console_vm.js). Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadConsole } = require('./console_vm');

const screen = () => loadConsole(['reactions.js', 'lottiestyle.js', 'effects.js', 'particles.js'], { studio: true });

test('the style bar offers Emoji style, Lottie style and Packs', () => {
  const r = screen();
  const h = r('rxStyleBar()');
  assert.match(h, /data-act=rxstyle data-s=emoji/);
  assert.match(h, /data-act=rxstyle data-s=lottie/);
  assert.match(h, /data-act=rxstyle data-s=packs/);
});

test('switching tabs keeps the choice and shows it on', () => {
  const r = screen();
  assert.equal(r('rxTab()'), 'emoji');
  r("ACT.rxstyle({dataset:{s:'packs'}})");
  assert.equal(r('RXS.tab'), 'packs');
  assert.match(r('rxStyleBar()'), /data-s=packs[^>]*aria-selected=true/);
  r("ACT.rxstyle({dataset:{s:'lottie'}})");
  assert.equal(r('rxTab()'), 'lottie');
  r("ACT.rxstyle({dataset:{s:'nope'}})");
  assert.equal(r('rxTab()'), 'emoji', 'an unknown tab falls back');
});

test('the Packs tab dumps the Library Reactions content as is', () => {
  const r = screen();
  r("RXS.tab='packs';SPL.sets=[];SPL.made=[{id:'L001',token:'kiss',emoji:'😘',preset:'burst_01',url:'/out/particles/emoji/kiss/renders/L001.lottie.json',preview:'/out/particles/emoji/kiss/renders/L001.webp'}]");
  const h = r('rxHtml()');
  assert.equal((h.match(/role=tablist/g)||[]).length, 1, 'one shared tab bar on Packs');
  assert.match(h, /data-s=packs[^>]*aria-selected=true/, 'the Packs tab is on');
  assert.match(h, /Made in Reaction effects/, 'with the made section');
  assert.match(h, /L001\.lottie\.json/, 'and the download');
});

test('Emoji and Lottie views render as before', () => {
  const r = screen();
  r("RXS.tab='emoji';RXS.bank=[];RXS.lib={presets:[],assign:{},handpicked:{}}");
  assert.match(r('rxHtml()'), /Animation presets/, 'the preset library');
  r("RXS.tab='lottie';LS.files=null");
  assert.match(r('rxHtml()'), /Reading lottie/, 'the Lottie files');
});

test('Library Reactions shows the same three tabs', () => {
  const r = screen();
  r("SPL.sets=[];SPL.made=[];RXS.tab='emoji';RXS.bank=[];RXS.lib={presets:[],assign:{},handpicked:{}}");
  let h = r('spLibHtml()');
  assert.match(h, /data-act=rxstyle data-s=packs/, 'the switch is there');
  assert.match(h, /Animation presets/, 'showing the Emoji style view');
  assert.doesNotMatch(h, /No reaction sets yet/, 'not the dumped list');
  r("RXS.tab='lottie';LS.files=null");
  h = r('spLibHtml()');
  assert.match(h, /data-s=lottie[^>]*aria-selected=true/, 'the Lottie tab is on');
  assert.match(h, /Reading lottie/, 'with its files');
  r("RXS.tab='packs'");
  assert.match(r('spLibHtml()'), /No reaction sets yet/, 'Packs is the dumped list, as before');
});

test('switching tabs inside the Library redraws the Library, not #/reactions', () => {
  const r = screen();
  r("route_='library';RXS.tab='packs'");
  r("ACT.rxstyle({dataset:{s:'emoji'}})");
  assert.equal(r('RXS.tab'), 'emoji');
});
