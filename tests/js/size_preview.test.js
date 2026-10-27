const {readRawConsoleFile}=require('./console_source');
// Exercise the production Resize/frame controllers with fake I/O and a controlled clock.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=readRawConsoleFile(path.join(__dirname,'../../stickerloom/console/studio-core.js'),'utf8');
const controllers=source.slice(source.indexOf('/* Motion size'),source.indexOf("/* the batch's Download .zip"));
function setup(resize=true){
 const reads=[],writes=[],timers=new Map();let serial=0;
 const batch={number:7,resize,anim_scale:1,stickers:[{index:1,anim_status:'READY'}]};
 const ctx=vm.createContext({ACT:{},GM:new Map([[7,batch]]),glast:'',tick(){},toast(){},
  localStorage:{getItem(){return null},setItem(){}},addEventListener(){},document:{getElementById(){return null}},
  setTimeout(fn){const id=++serial;timers.set(id,fn);return id},clearTimeout(id){timers.delete(id)},
  api:async url=>{reads.push(url);return {ok:true,j:{url:'/frame.png',frame:0,frames:66}}},
  post:async(url,body)=>{writes.push([url,JSON.parse(JSON.stringify(body))]);return {ok:true,j:{}}}});
 vm.runInContext(controllers,ctx);vm.runInContext('vzCard=()=>{}',ctx);
 return {ctx,reads,writes,run:code=>vm.runInContext(code,ctx),flush:async()=>{for(const [id,fn] of [...timers]){timers.delete(id);await fn()}}};
}
test('Resize stages both values and reads a frame without writing until Apply',async()=>{
 for(const initial of [true,false]){
  const h=setup(initial);h.ctx.ACT.gresize({dataset:{g:'7'}});await h.flush();
  assert.equal(h.writes.length,0);assert.match(h.reads[0],new RegExp('resize='+Number(!initial)));
  await h.ctx.ACT.vzapply();assert.deepEqual(h.writes,[['/api/generations/7/resize',{on:!initial,reslice:true}]]);
 }
});
test('batch Size commits video_size once on Apply, with one recut when Resize also changed',async()=>{
 const h=setup();h.ctx.ACT.gresize({dataset:{g:'7'}});h.ctx.ACT.gvsize({dataset:{g:'7',f:'1.1'}});await h.flush();
 assert.equal(h.writes.length,0);assert.match(h.reads[0],/scale=1.1/);
 await h.ctx.ACT.vzapply();assert.deepEqual(h.writes,[['/api/generations/7/resize',{on:false,reslice:false}],['/api/generations/7/video_size',{scale:1.1,reslice:true}]]);
});
test('sticker Size and Position are one position POST on Apply',async()=>{
 const h=setup();h.ctx.ACT.vzedit({dataset:{g:'7',i:'1'}});h.ctx.ACT.vzsize({dataset:{d:'1'}});h.ctx.ACT.vzmove({dataset:{x:'1'}});await h.flush();
 assert.equal(h.writes.length,0);assert.match(h.reads[0],/dx=0.01&dy=0/);
 await h.ctx.ACT.vzapply();assert.deepEqual(h.writes,[['/api/generations/7/position',{index:1,offset:[0.01,0],scale:1.05,reslice:true}]]);
});
test('Cancel drops the staged batch and sticker edits without mutations',async()=>{
 for(const sticker of [false,true]){
  const h=setup();if(sticker){h.ctx.ACT.vzedit({dataset:{g:'7',i:'1'}});h.ctx.ACT.vzmove({dataset:{y:'-1'}})}else h.ctx.ACT.gresize({dataset:{g:'7'}});
  await h.flush();h.ctx.ACT.vzcancel();assert.equal(h.writes.length,0);assert.equal(h.run('VZ.g'),null);
 }
});
