// Handpicked Lottie files: the dashboard lists
// them with sizes and frame counts; Select + Assign gives emojis an origin file
// and an optional morph file; the wall shows what each emoji holds.
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadConsole } = require('./console_vm');

const BANK = "[{token:'kiss',emoji:'x',preset:'burst',motion:''},{token:'happy',emoji:'y',preset:'burst',motion:''}]";
const LIB = "{presets:[{id:'burst_01',motion:'burst',n:1,name:'B',settings:{}}],assign:{},handpicked:{kiss:{origin:'Sad Emoji.json',morph:'Star Strike Emoji.json'}}}";
const HP = "[{file:'Sad Emoji.json',name:'Sad Emoji',bytes:13278,w:1500,h:1500,fps:25,frames:54,layers:7,images:0,base64:false},{file:'Star Strike Emoji.json',name:'Star Strike Emoji',bytes:28227,w:1500,h:1500,fps:25,frames:45,layers:14,images:0,base64:false}]";
const J = (r, code) => JSON.parse(r(`JSON.stringify(${code})`));
const screen = () => {
  const r = loadConsole(['reactions.js', 'lottiestyle.js']);
  r(`RXS.bank=${BANK};RXS.lib=${LIB};RXS.hp=${HP}`);
  return r;
};

test('the Handpicked section lists every dropped file with its size and shape', () => {
  const h = screen()('rxHpHtml()');
  assert.match(h, /Handpicked Lottie files <small class=mut>2<\/small>/);
  assert.match(h, /<b>Sad Emoji<\/b><span class=mut>13 KB · 1500×1500 · 25 fps · 54 frames · 7 layers<\/span>/);
  assert.match(h, /href="\/api\/particles\/reaction-handpicked\/Sad%20Emoji.json" download="Sad Emoji.json">Download<\/a>/);
  const r = loadConsole(['reactions.js', 'lottiestyle.js']);
  r(`RXS.bank=${BANK};RXS.lib=${LIB};RXS.hp=[]`);
  assert.match(r('rxHpHtml()'), /No files yet: drop Lottie .json files into the <code>lottie\/<\/code> folder\./);
});

test('the wall shows origin then morph after the preset an emoji holds', () => {
  const r = screen();
  assert.equal(r("rxHpHold('kiss')"), 'Sad Emoji → Star Strike Emoji');
  assert.equal(r("rxHpHold('happy')"), '');
  assert.match(r("rxTile(rxRow('kiss'))"), /title="kiss · burst_01 · Sad Emoji → Star Strike Emoji"/);
  assert.match(r("rxTile(rxRow('happy'))"), /title="happy · burst_01"/);
});

test('Select plus Assign gives the ticked emojis the picked origin and morph', async () => {
  const r = screen();
  r('RXS.multi=true;RXS.sel.add("happy")');
  const bar = r('rxMany()');
  assert.match(bar, /<select data-rxhpo aria-label="Handpicked origin file"><option value="">none \(preset only\)<\/option>/);
  assert.match(bar, /<option value="Star Strike Emoji.json">Star Strike Emoji · 28 KB · 45f<\/option>/);
  r("POSTREPLIES.push({ok:true,j:{assigned:1}});POSTREPLIES.push({ok:true,j:{assigned:1,cleared:0}});APIREPLIES.push({ok:true,j:" + LIB + "})");
  r("RXS.hpo='Sad Emoji.json';RXS.hpm='Star Strike Emoji.json'");
  await r('ACT.rxmany()');
  assert.deepEqual(J(r, "POSTS.find(p=>p[0]==='/api/particles/reaction-handpicked/assign')"),
    ['/api/particles/reaction-handpicked/assign', { tokens: ['happy'], origin: 'Sad Emoji.json', morph: 'Star Strike Emoji.json' }]);
  assert.equal(r('RXS.sel.size'), 0, 'selection clears after assigning');
});

test('Origin none clears the files back to preset-only', async () => {
  const r = screen();
  r('RXS.multi=true;RXS.sel.add("kiss")');
  r("POSTREPLIES.push({ok:true,j:{assigned:1}});POSTREPLIES.push({ok:true,j:{assigned:0,cleared:1}});APIREPLIES.push({ok:true,j:" + LIB + "})");
  await r('ACT.rxmany()');
  assert.deepEqual(J(r, "POSTS.find(p=>p[0]==='/api/particles/reaction-handpicked/assign')")[1],
    { tokens: ['kiss'], origin: '', morph: null });
});
