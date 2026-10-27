// UX-03: pack and library sticker tiles are keyboard operable. The tile stays a
// draggable div (marquee, drag reorder); selection, view/rename and the hover
// actions are native buttons with names, and the actions show on focus as well
// as hover (display:none would take them out of the Tab order).
const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const {readConsoleFile}=require('./console_source');
const read=f=>readConsoleFile(path.join(__dirname,'../../stickerloom/console',f));
const packs=read('packs.js'),library=read('library.js'),css=read('studio.css');

test('selection boxes are named toggle buttons, never clickable spans',()=>{
  for(const src of [packs,library]){
    assert.doesNotMatch(src,/<span class="selbox/);
    assert.match(src,/<button type=button class="selbox[^>]*aria-label="Select \$\{esc\(s\.name\)\}" aria-pressed=/);
  }
});

test('every pack tile action carries the sticker name, and the caption is a button',()=>{
  for(const act of ['stview','stedit','stanim','stcover','stdel'])
    assert.match(packs,new RegExp(`data-act=${act} data-id=\\$\\{s\\.id\\}[^>]*aria-label="[^"]*\\$\\{esc\\(s\\.name\\)\\}`),act);
  assert.doesNotMatch(packs,/<div class=cap/);
  assert.match(library,/<button type=button class=cap data-act=lcopen/);
});

test('tile actions are revealed on focus, and stay in the Tab order while hidden',()=>{
  assert.doesNotMatch(css,/\.cell \.hov\{[^}]*display:none/);
  assert.match(css,/\.cell:focus-within \.hov\{opacity:1;pointer-events:auto\}/);
  assert.match(css,/\.cell:focus-within \.selbox/);
});

test('a keyboard selection toggle keeps focus on the same sticker after the redraw',()=>{
  const vm=require('node:vm'),{statement}=require('./console_source');
  const fresh={focused:false,focus(){this.focused=true}},old={dataset:{p:'P1',id:'s3'}};
  let asked='';
  const doc={activeElement:old,querySelector:q=>{asked=q;return fresh}};
  const ctx=vm.createContext({ACT:{},SEL:new Map(),document:doc,selKey:(p,id)=>p+'/'+id,selRefresh(){}});
  vm.runInContext(statement(library,'ACT.lsel='),ctx);
  ctx.ACT.lsel(old);
  assert.ok(ctx.SEL.has('P1/s3'));
  assert.equal(asked,'.selbox[data-p="P1"][data-id="s3"]');assert.ok(fresh.focused);
  fresh.focused=false;doc.activeElement=null;ctx.ACT.lsel(old);
  assert.ok(!ctx.SEL.has('P1/s3'));assert.ok(!fresh.focused,'a mouse toggle does not move focus');
});

test('Enter or Space opens a reaction preset from its focusable preview stage',()=>{
  const vm=require('node:vm'),{statement}=require('./console_source');
  const panel=readConsoleFile(path.join(__dirname,'../../stickerloom/console/generate.js'));
  assert.match(read('reactions.js'),/<div class=rx-stage data-act=rxpopen data-id="\$\{esc\(p\.id\)\}" role=button tabindex=0 aria-label="Open/);
  let handler=null,opened=null;
  const ctx=vm.createContext({ACT:{rxpopen:el=>{opened=el}},document:{addEventListener:(t,f)=>{if(t==='keydown')handler=f}}});
  vm.runInContext(statement(panel,"document.addEventListener('keydown',e=>{const t=e.target,tag"),ctx);
  const stage={tagName:'DIV',dataset:{act:'rxpopen'},getAttribute:k=>k==='role'?'button':null};
  let prevented=false;handler({key:' ',target:stage,preventDefault(){prevented=true}});
  assert.equal(opened,stage);assert.ok(prevented);
  opened=null;handler({key:'Enter',target:{...stage,getAttribute:()=>null},preventDefault(){}});
  assert.equal(opened,null,'a plain div without role=button is not pressed');
});
