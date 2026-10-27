const test = require('node:test');
const assert = require('node:assert/strict');
const {loadConsole} = require('./console_vm');
const run = loadConsole(['effects.js','particles.js'], {studio:true, extra:'const location={hash:""};'});
const packs=[{id:'a',name:'A',stickers:[{id:'s1',source:{generation:'G001',index:1}},{id:'s2',source:{generation:'G002',index:1}}]}];
run(`LIB.packs=${JSON.stringify(packs)}`);
test('batch entry clears unrelated old run and selects only that batch',()=>{
  run("SP.eid='E099';ACT.spopen({dataset:{g:'G001'}})");
  assert.equal(run('SP.eid'),'');
  assert.equal(run('JSON.stringify([...SP.sel])'),'["s1"]');
});
test('section and carousel entry carries the exact owner',()=>{
  run("SP.eid='E099';ACT.spmake({dataset:{p:'a',s:'s2'}})");
  assert.equal(run('SP.eid'),'');
  assert.equal(run('SP.scope.sticker_id'),'s2');
});
test('pack entry is explicit and clears the old run',()=>{
  run("SP.eid='E099';ACT.psmakepack({dataset:{p:'a'}})");
  assert.equal(run('SP.eid'),'');
  assert.equal(run('SP.scope.pack_id'),'a');
});
test('Library with no scope asks which sticker and has no wizard body',()=>{
  run("route_='library';ACT.spopen({dataset:{}})");
  assert.match(run('spBody([])'),/Reactions for which sticker/);
  assert.doesNotMatch(run('spBody([])'),/data-act=spstart/);
});
test('unpacked batch offers approval and no unrelated stickers',()=>{
  run("route_='generate';ACT.spopen({dataset:{g:'G003'}})");
  assert.match(run('spBody([])'),/Approve as a pack/);
  assert.doesNotMatch(run('spBody([])'),/data-id=s1/);
});
test('continue is visible only for the matching scope',()=>{
  run("spLast={eid:'E099',scope:{generation:'G001'}};SP.scope={generation:'G003'}");
  assert.doesNotMatch(run('spView([])'),/Continue the last run/);
  run("SP.scope={generation:'G001'}");
  assert.match(run('spView([])'),/Continue the last run/);
});
