const test=require('node:test');
const assert=require('node:assert/strict');
const {loadConsole}=require('./console_vm');
const run=loadConsole(['effects.js','particles.js'],{studio:true});
test('Kling has a price line and cannot start without its quote',async()=>{
  run("SP.rec={id:'E001',grid:[2,2],video:{}};SP.eid='E001';SP.est={}");
  assert.match(run("fxVideoPanel(SP,SP.rec,{id:'g',key:'green'})"),/data-act=fxvideo data-g=g disabled/);
  const el={dataset:{g:'g'},closest:()=>({dataset:{fxx:'sp'}})};
  await run('ACT.fxvideo')(el);
  assert.equal(run('POSTS.length'),0);
  run("SP.est.g2x2={credits:4.5}");
  assert.match(run("fxVideoPanel(SP,SP.rec,{id:'g',key:'green'})"),/about 4.5 credits/);
  await run('ACT.fxvideo')(el);
  assert.equal(run('POSTS[0][1].go'),true);
});
test('Kling results can be kept as a simulatable set',()=>{
  run("SP.set=null");
  assert.match(run("fxSaveBlock(SP,{mode:'video',results:[{mode:'video',file:'cell.webm'}]})"),/From Kling video.*data-act=fxuseset/s);
});
test('kind chips name the source rather than raw provider internals',()=>{
  assert.equal(run("spKindChip({source:{kind:'video'}})"),'From Kling video');
  assert.equal(run("spKindChip({source:{kind:'drawn'}})"),'From AI sheet');
  assert.equal(run("spKindChip({source:{kind:'stickers'}})"),'From sprites');
});
