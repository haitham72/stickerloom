const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const path = require('node:path');
const {readConsoleFile, statement} = require('./console_source');
const read = name => readConsoleFile(path.join(__dirname, '../../stickerloom/console', name));
const composer = read('composer.js'), live = read('live.js');

test('Studio retains the exact composer and draft through leaving and returning', () => {
  let attached = true, mounts = 0, draws = 0;
  const host = {insertBefore(node) { attached=true; node.parentElement=this; }};
  const col = {dataset:{k:'studio'}};
  const node = {draft:'Unsent request', refs:['reference'], open:true, parentElement:host,
    closest:()=>col, remove(){attached=false;this.parentElement=null;}};
  const ctx = vm.createContext({
    document:{getElementById:id=>({col2:col,c2create:host,cpwrap:attached?node:null})[id]},
    composerDraw(){draws++;},composerMount(){mounts++;},histCol(){},
  });
  vm.runInContext(['let CPKEEP','function cpStash','function studioCol'].map(s=>statement(composer,s)).join(';'),ctx);
  vm.runInContext('cpStash()',ctx);
  assert.equal(attached,false);
  assert.equal(vm.runInContext('CPKEEP',ctx),node);
  vm.runInContext('studioCol();studioCol()',ctx);
  assert.equal(node.parentElement,host);
  assert.equal(node.draft,'Unsent request');
  assert.deepEqual(node.refs,['reference']);
  assert.equal(node.open,true);
  assert.equal(mounts,0);
  assert.equal(draws,1);
  assert.equal(vm.runInContext('CPKEEP',ctx),null);
});

test('Batches toggles visibility, expanded state and restores a replaced launcher', () => {
  let focused='';
  const launcher={isConnected:true,setAttribute(k,v){this[k]=v},focus(){focused='launcher'}};
  const replacement={focus(){focused='replacement'}};
  const first={focus(){focused='first'}};
  const drawer={hidden:true,querySelector:()=>first};
  const scrim={hidden:true};
  const ctx=vm.createContext({
    ACT:{},document:{getElementById:id=>id==='bdrawer'?drawer:scrim,
      querySelectorAll:()=>[launcher],querySelector:()=>replacement},
    bdEl:()=>drawer,histCol(){},
  });
  vm.runInContext(['const BD','function bdSync','ACT.bdopen=','function bdClose','ACT.bdclose=']
    .map(s=>statement(live,s)).join(';'),ctx);
  ctx.launcher=launcher;
  vm.runInContext('ACT.bdopen(launcher)',ctx);
  assert.equal(drawer.hidden,false);
  assert.equal(scrim.hidden,false);
  assert.equal(launcher['aria-expanded'],'true');
  assert.equal(focused,'first');
  vm.runInContext('ACT.bdclose()',ctx);
  assert.equal(focused,'launcher');
  assert.equal(drawer.hidden,true);
  assert.equal(scrim.hidden,true);
  assert.equal(launcher['aria-expanded'],'false');
  vm.runInContext('ACT.bdopen(launcher)',ctx);
  launcher.isConnected=false;
  vm.runInContext('bdClose(true)',ctx);
  assert.equal(focused,'replacement');
});

test('Batches renders in its drawer only on Studio', () => {
  const drawer={},col={};
  const ctx=vm.createContext({route_:'generate',bdEl(){},
    document:{getElementById:id=>id==='bdbody'?drawer:col}});
  vm.runInContext(statement(live,'const histHost'),ctx);
  assert.equal(vm.runInContext('histHost()',ctx),drawer);
  ctx.route_='create';
  assert.equal(vm.runInContext('histHost()',ctx),col);
});
