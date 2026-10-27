const {readRawConsoleFile}=require('./console_source');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=readRawConsoleFile(path.join(__dirname,'../../stickerloom/console/studio-core.js'),'utf8');
const controllers=source.slice(source.indexOf('/* Motion size'),source.indexOf("/* the batch's Download .zip"));
function setup(){
 const writes=[],timers=new Map(),events={};let serial=0,reply=async()=>({ok:true,j:{}});
 const sticker={index:1,anim_status:'READY'},batch={number:7,anim_scale:1,stickers:[sticker]};
 const ctx=vm.createContext({ACT:{},GM:new Map([[7,batch]]),glast:'',tick(){},toast(){},localStorage:{getItem(){return null},setItem(){}},document:{getElementById(){return null}},
 addEventListener(n,f){(events[n]??=[]).push(f)},setTimeout(fn,ms){const id=++serial;timers.set(id,{fn,ms});return id},clearTimeout(id){timers.delete(id)},post:async(url,body)=>{writes.push([url,JSON.parse(JSON.stringify(body))]);return reply(url,body)}});
 vm.runInContext(controllers,ctx);
 const values={scale:{value:'100'},dx:{value:'0'},dy:{value:'0'}},readout={textContent:''};
 const box={dataset:{g:'7',i:'1'},querySelector:s=>values[s.match(/data-k=(\w+)/)[1]]};
 const input={tagName:'INPUT',dataset:{k:'scale'},matches(){return true},closest(){return box},parentElement:{querySelector(){return readout}},get value(){return values.scale.value}};
 return{ctx,writes,timers,sticker,values,readout,run:s=>vm.runInContext(s,ctx),reply:f=>reply=f,input(v){values.scale.value=String(v);events.input.forEach(f=>f({target:input}))},release(){events.change.forEach(f=>f({target:input}))},fire(){const [id,t]=timers.entries().next().value;timers.delete(id);return t.fn()},flush:async()=>{for(const [id,t] of [...timers]){timers.delete(id);await t.fn()}}};
}
test('a changed release sends once after 500ms; untouched release does not write',async()=>{
 const h=setup();h.release();await h.flush();assert.equal(h.writes.length,0);h.input(110);assert.equal(h.writes.length,0);assert.match(h.readout.textContent,/110%/);
 h.run(`ACT.vslopen({dataset:{g:'7',i:'1'}})`);
 assert.match(h.run('vsizeRow(GM.get(7),GM.get(7).stickers[0])'),/value=110/,'pending values survive a redraw');h.release();assert.equal([...h.timers.values()][0].ms,500);await h.flush();assert.deepEqual(h.writes,[['/api/generations/7/position',{index:1,offset:null,scale:1.1,reslice:true}]]);
});
test('a second released value is retained while the first request is in flight',async()=>{
 for(const last of [120,100]){const h=setup();let resolve;h.reply(()=>new Promise(r=>resolve=r));h.input(110);h.release();const first=h.fire();h.input(last);h.release();await h.fire();
 h.reply(async()=>({ok:true,j:{}}));resolve({ok:true,j:{}});await first;await h.flush();assert.deepEqual(h.writes.map(x=>x[1].scale),[1.1,last/100]);assert.equal(h.run('vslPend["7:1"]'),undefined);}
});
test('Reset cancels a scheduled edit and clears both fields with one recut',async()=>{
 const h=setup();h.sticker.anim_scale=.9;h.sticker.anim_offset=[.1,.1];h.input(110);h.release();await h.ctx.ACT.vslreset({dataset:{g:'7',i:'1'}});await h.flush();
 assert.deepEqual(h.writes,[['/api/generations/7/position',{index:1,offset:null,reslice:false}],['/api/generations/7/video_size',{index:1,scale:null,reslice:true}]]);
});
test('a network failure releases the busy guard so the next edit can be sent',async()=>{
 const h=setup();h.reply(async()=>{throw Error('offline')});h.input(110);h.release();await h.fire();assert.equal(h.run('vslBusy.size'),0);h.reply(async()=>({ok:true,j:{}}));h.input(120);h.release();await h.flush();assert.equal(h.writes.length,2);
});

test('a newer unreleased drag is not applied by a queued earlier release',async()=>{
 const h=setup();let resolve;h.reply(()=>new Promise(r=>resolve=r));h.input(110);h.release();const first=h.fire();h.input(120);h.release();await h.fire();h.input(130);
 h.reply(async()=>({ok:true,j:{}}));resolve({ok:true,j:{}});await first;await h.flush();assert.deepEqual(h.writes.map(x=>x[1].scale),[1.1,1.2]);assert.equal(h.run('vslPend["7:1"].scale'),1.3);h.release();await h.flush();assert.deepEqual(h.writes.map(x=>x[1].scale),[1.1,1.2,1.3]);
});
test('sliders hide behind Adjust per sticker; toggling reveals them',()=>{
 const h=setup(),tile=()=>h.run('vsizeRow(GM.get(7),GM.get(7).stickers[0])');
 assert.doesNotMatch(tile(),/data-k=scale/,'compact by default');
 assert.match(tile(),/data-act=vslopen/);
 h.run(`ACT.vslopen({dataset:{g:'7',i:'1'}})`);
 assert.match(tile(),/data-k=scale/,'revealed');
 assert.match(tile(),/aria-expanded=true/);
 h.run(`ACT.vslopen({dataset:{g:'7',i:'1'}})`);
 assert.doesNotMatch(tile(),/data-k=scale/,'hidden again');
});
test('a 409 busy waits and sends once more instead of erroring',async()=>{
 const h=setup();let n=0;
 h.reply(()=> (++n===1)?{ok:false,status:409,j:{error:'busy: a job is running, wait for it to finish'}}:{ok:true,j:{}});
 const tick=()=>new Promise(r=>setImmediate(r));
 h.input(110);h.release();
 const p=h.fire();await tick();await tick();
 assert.equal(h.writes.length,1,'first attempt refused');
 h.fire();await tick();await tick();await p;
 assert.equal(h.writes.length,2,'retried once it cleared');
 assert.deepEqual(h.writes[1][1],{index:1,offset:null,scale:1.1,reslice:true});
 assert.equal(h.run('vslBusy.size'),0);
});
