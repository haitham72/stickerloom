const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// F-06 (T-002): every retime dialog cancellation settles its promise. Backdrop,
// Cancel and replacement dialogs resolve null exactly once; an explicit answer
// resolves true/false. ACT.rtans clears the dismissal before closing so the
// answer is not overwritten by it.
const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),vm=require('node:vm');
const appSrc=readConsoleFile(path.join(__dirname,'../../stickerloom/console/app.js'),'utf8');
const packsSrc=readConsoleFile(path.join(__dirname,'../../stickerloom/console/packs.js'),'utf8');
const appSt=m=>consoleStatement(appSrc,m), packsSt=m=>consoleStatement(packsSrc,m);
function rig(){
  const dialogs=[];
  const ctx=vm.createContext({
    ACT:{}, dialogs,
    esc:s=>String(s??''),
    $:()=>({innerHTML:'',classList:{add(){},remove(){}},addEventListener(){}}),
  });
  const run=s=>vm.runInContext(s,ctx);
  run(appSt('let DLG_CB='));
  run(appSt('function dlg('));
  run(appSt('function dlgForget('));
  run(appSt('function closeDlg('));
  run(packsSt('let RTA='));
  run(packsSt('function retimeAsk('));
  run(packsSt('ACT.rtans='));
  return {ctx,run,dialogs};
}
const SHORT=[{emoji:'',name:'a',seconds:1.2}];

test('an explicit answer resolves true; Cancel resolves null',async()=>{
  const {run}=rig();
  const p1=run(`retimeAsk(${JSON.stringify(SHORT)},'zip')`);
  run(`ACT.rtans({dataset:{v:'1'}})`);
  assert.equal(await p1,true);
  const {run:run2}=rig();
  const p2=run2(`retimeAsk(${JSON.stringify(SHORT)},'zip')`);
  run2(`ACT.rtans({dataset:{v:'x'}})`);
  assert.equal(await p2,null);
});

test('backdrop close (closeDlg) settles the pending promise null exactly once',async()=>{
  const {run}=rig();
  const p=run(`retimeAsk(${JSON.stringify(SHORT)},'zip')`);
  run(`closeDlg()`);
  assert.equal(await p,null);
  run(`closeDlg()`);
  assert.equal(await p,null,'a second close cannot change the settled value');
});

test('opening a replacement dialog cancels the prior request',async()=>{
  const {run}=rig();
  const p1=run(`retimeAsk(${JSON.stringify(SHORT)},'zip')`);
  const p2=run(`retimeAsk(${JSON.stringify(SHORT)},'zip')`);
  assert.equal(await p1,null,'prior request cancelled');
  run(`ACT.rtans({dataset:{v:'0'}})`);
  assert.equal(await p2,false);
});

test('wiring: Cancel buttons and the backdrop share the settling close path',()=>{
  assert.match(appSrc,/ACT\.dlgx\s*=\s*closeDlg/,'Cancel buttons close through closeDlg');
  assert.match(appSrc,/\$\('dlg'\)\.addEventListener\('mousedown'[^;]*closeDlg\(\)/,'backdrop mousedown closes through closeDlg');
  assert.match(packsSrc,/onDismiss:\(\)=>settle\(null\)/,'retime registers the dismissal settlement');
  assert.match(packsSrc,/dlgForget\(\);closeDlg\('answer'\)/,'an answer clears the dismissal before closing');
});


test('Escape settles retime and does not close a covered dialog or underlying viewer',async()=>{
 const {ctx,run}=rig();const classes={dlg:new Set()};
 ctx.$=id=>({innerHTML:'',addEventListener(){},classList:{add:v=>classes[id].add(v),remove:v=>classes[id].delete(v),contains:v=>classes[id].has(v)}});
 run(appSt('function dlgEscape('));const promise=run(`retimeAsk(${JSON.stringify(SHORT)},'zip')`);
 let prevented=0,stopped=0;ctx.EVENT={key:'Escape',preventDefault(){prevented++},stopImmediatePropagation(){stopped++}};
 assert.equal(run('typeof RTA'),'function');assert.equal(run('dlgEscape(EVENT)'),true);assert.equal(await promise,null);
 assert.equal(prevented,1);assert.equal(stopped,1);assert.equal(run('dlgEscape(EVENT)'),false);
});

test('dialog dismissal restores the visible summary when its opener menu folded',()=>{
 const {ctx,run}=rig();let focused='';
 const summary={focus(){focused='summary'}};
 const opener={isConnected:true,closest(selector){return selector==='details:not([open])'?{querySelector(){return summary}}:null},focus(){focused='hidden opener'}};
 ctx.document={activeElement:opener};run("dlg('<h2>Rename</h2>')");run('closeDlg()');assert.equal(focused,'summary');
});
