const {readRawConsoleFile}=require('./console_source');
const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// The Settings bar is built from a registry and rebuilt after the render wrappers mount,
// so it only offers mounted cards.
const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),vm=require('node:vm'),fs=require('node:fs');
const src=readConsoleFile(path.join(__dirname,'../../stickerloom/console/settings.js'),'utf8');
function statement(marker){return consoleStatement(src,marker);}
function rig(present){
  const presentSet=new Set(present);
  const ctx=vm.createContext({
    document:{
      querySelector:()=>null,
      getElementById(id){return presentSet.has(id)?{id}:null;},
    },
  });
  // stub querySelector to return a mutable nav holder
  vm.runInContext('let __nav=null;document.querySelector=()=>__nav;',ctx);
  const run=s=>vm.runInContext(s,ctx);
  run(statement('let SET_SECTIONS='));
  run(statement('function setNavHtml('));
  run(statement('function setNavSync('));
  return run;
}

test('the registry holds every section, with stable ids and actions',()=>{
  const run=rig([]);
  const html=run(`setNavHtml([['set-appear','Appearance'],['set-health','System health'],['trx-box','Trash']])`);
  assert.match(html,/<nav class="gseg set-nav" aria-label="Settings sections">/);
  for(const id of ['set-appear','set-health','trx-box'])
    assert.match(html,new RegExp(`<button type=button data-act=setgo data-id=${id}>`));
  assert.doesNotMatch(src,/au-me|tk-box|Tickets|Account/,'no account or ticket sections');
  assert.match(src,/ACT\.setgo=el=>/,'the section action is unchanged');
});

test('the bar keeps only mounted cards',()=>{
  const run=rig(['set-appear','set-health','set-ai','set-tg','trx-box']);
  run(`SET_SECTIONS=[['set-appear','A'],['set-gone','Gone'],['trx-box','Trash']];__nav={outerHTML:''};setNavSync();`);
  const html=run('__nav.outerHTML');
  assert.doesNotMatch(html,/data-id=set-gone/,'no shortcut to a card that never mounted');
  assert.match(html,/data-id=trx-box/,'Trash stays');
});

test('the trash wrapper rebuilds the bar after mounting',()=>{
  const s=readRawConsoleFile(path.join(__dirname,'../../stickerloom/console','trash.js'),'utf8');
  assert.match(s,/if\(typeof setNavSync==='function'\)setNavSync\(\);/,'trash.js rebuilds the bar');
});
