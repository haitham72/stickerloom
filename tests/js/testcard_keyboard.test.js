// UX-05: the frame test card is non-modal. Opened from a button it takes focus;
// Escape (on the card or on its launcher) presses Cancel; closing returns focus
// to the launcher, or to its redrawn replacement found by action and address.
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),path=require('node:path');
const {readConsoleFile}=require('./console_source');
const src=readConsoleFile(path.join(__dirname,'../../stickerloom/console/ui-testcard.js'));

function rig(){
  const byId={},doc={body:null,activeElement:null,getElementById:id=>byId[id]||null,querySelector:()=>null};
  class El{
    constructor(tag){this.tag=tag;this.on={};this.dataset={};this.isConnected=false;this.kids=[];this.attrs={};this.style={}}
    addEventListener(t,f){(this.on[t]=this.on[t]||[]).push(f)}
    removeEventListener(t,f){this.on[t]=(this.on[t]||[]).filter(x=>x!==f)}
    key(k){const e={key:k,defaultPrevented:false,preventDefault(){this.defaultPrevented=true},stopPropagation(){}};(this.on.keydown||[]).forEach(f=>f(e));return e}
    focus(){doc.activeElement=this}
    setAttribute(k,v){this.attrs[k]=v}
    contains(x){return x===this||this.kids.includes(x)}
    set innerHTML(v){const b=new El('button');b.isConnected=true;b.click=()=>doc.cancels.push(b);this.kids=[b];this.cancelBtn=b}
    querySelector(q){return /data-act="vzcancel"/.test(q)?this.cancelBtn:null}
    remove(){this.isConnected=false;delete byId[this.id];this.kids.forEach(k=>k.isConnected=false)}
  }
  doc.body=new El('body');doc.activeElement=doc.body;doc.cancels=[];
  doc.body.appendChild=c=>{c.isConnected=true;byId[c.id]=c;c.kids.forEach(k=>k.isConnected=true)};
  doc.createElement=t=>new El(t);
  const ctx=vm.createContext({document:doc,innerWidth:1440,innerHeight:900});
  vm.runInContext(src+';this.TESTCARD=TESTCARD',ctx);
  return {ctx,doc,El};
}
const OPTS={id:'vzcard',apply:'vzapply',cancel:'vzcancel',focus:true};

test('opening from a button moves focus onto the card',()=>{
  const {ctx,doc,El}=rig(),launch=new El('button');launch.isConnected=true;launch.focus();
  const card=ctx.TESTCARD.open(OPTS);
  assert.equal(doc.activeElement,card);
  assert.equal(card.tabIndex,-1);
});

test('Escape on the card presses Cancel, never Apply',()=>{
  const {ctx,doc,El}=rig(),launch=new El('button');launch.isConnected=true;launch.focus();
  const card=ctx.TESTCARD.open(OPTS),e=card.key('Escape');
  assert.equal(doc.cancels.length,1);assert.ok(e.defaultPrevented);
  card.key('a');assert.equal(doc.cancels.length,1,'other keys do nothing');
});

test('Escape on a slider launcher cancels a card that did not take focus',()=>{
  const {ctx,doc,El}=rig(),slider=new El('input');slider.isConnected=true;slider.focus();
  ctx.TESTCARD.open({...OPTS,focus:false});
  assert.equal(doc.activeElement,slider,'focus stays on the slider being dragged');
  slider.key('Escape');assert.equal(doc.cancels.length,1);
  ctx.TESTCARD.close('vzcard');slider.key('Escape');assert.equal(doc.cancels.length,1,'the launcher listener is gone after close');
});

test('closing returns focus to the launcher, or to its redrawn replacement',()=>{
  const {ctx,doc,El}=rig(),launch=new El('button');launch.isConnected=true;launch.dataset={act:'vzedit',g:'146',i:'2'};launch.focus();
  ctx.TESTCARD.open(OPTS);ctx.TESTCARD.close('vzcard');
  assert.equal(doc.activeElement,launch);
  ctx.TESTCARD.open(OPTS);launch.isConnected=false;
  const fresh=new El('button');fresh.isConnected=true;let asked='';doc.querySelector=q=>{asked=q;return fresh};
  ctx.TESTCARD.close('vzcard');
  assert.equal(asked,'[data-act="vzedit"][data-g="146"][data-i="2"]');
  assert.equal(doc.activeElement,fresh);
});

test('closing does not pull focus back when the person already moved elsewhere',()=>{
  const {ctx,doc,El}=rig(),launch=new El('button');launch.isConnected=true;launch.focus();
  ctx.TESTCARD.open(OPTS);const other=new El('button');other.isConnected=true;other.focus();
  ctx.TESTCARD.close('vzcard');assert.equal(doc.activeElement,other);
});
