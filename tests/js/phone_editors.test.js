// UX-01: on a narrow screen the image/animation editors stack with the canvas
// first, and the image canvas opens at the largest zoom step that fits its view.
// Save sticker stacks its preview over its form. Nothing is hidden.
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),path=require('node:path');
const {readConsoleFile,statement}=require('./console_source');
const ed=readConsoleFile(path.join(__dirname,'../../stickerloom/console/editor.js'));
const css=readConsoleFile(path.join(__dirname,'../../stickerloom/console/studio.css'));

function fit(width){
  const ctx=vm.createContext({CV:512,$:id=>id==='edv'?{clientWidth:width}:null});
  vm.runInContext(statement(ed,'const ZOOMS=')+';'+statement(ed,'function edFit('),ctx);
  return z=>vm.runInContext(`edFit(${z})`,ctx);
}

test('a phone opens the canvas at the largest step that fits',()=>{
  assert.equal(fit(366)(1),0.5,'390px phone: 512 does not fit, 384 does not, 256 does');
  assert.equal(fit(420)(1),0.75);
  assert.equal(fit(300)(2),0.5);
});

test('a zoom that already fits is the person\'s own and is kept',()=>{
  assert.equal(fit(900)(1),1);
  assert.equal(fit(900)(0.5),0.5);
  assert.equal(fit(0)(1.5),1.5,'an unmeasured view changes nothing');
});

test('editors stack under 1000px with the canvas first and every panel kept',()=>{
  const m=css.match(/@media \(max-width:1000px\)\{#s-editor\.on,#s-animate\.on\{flex-direction:column[\s\S]*?\}\}/);
  assert.ok(m,'stacking block present');
  assert.match(m[0],/#s-editor \.ed-c,#s-animate \.an-c\{order:-1/);
  assert.doesNotMatch(m[0],/display:none/,'no tool or panel is hidden');
});

test('Save sticker stacks its preview over its form on a phone',()=>{
  assert.match(ed,/<div class=xgrid><div class="pvbox bg-checker">/);
  assert.doesNotMatch(ed,/grid-template-columns:1fr 340px/);
  assert.match(css,/@media \(max-width:760px\)\{\.xgrid\{grid-template-columns:minmax\(0,1fr\)\}/);
});

test('UX-09: a watch-folder row stacks on a phone and the screen says what it is',()=>{
  const hist=readConsoleFile(path.join(__dirname,'../../stickerloom/console/history.js'));
  assert.match(css,/@media \(max-width:760px\)\{\.hrow\{flex-wrap:wrap\}\.hthumb\{[^}]*\}\.hmain\{flex:1 1 calc\(100% - 80px\)\}\.hact\{flex:1 1 100%/);
  assert.match(hist,/Watch folders <span class=mut/);
  assert.match(hist,/not your conversations or earlier batches/);
});
