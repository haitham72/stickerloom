const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm');
const {readConsoleFile}=require('./console_source');
const path=require('node:path');
const src=readConsoleFile(path.join(__dirname,'../../stickerloom/console/ui-testcard.js'));
test('wrapped preview actions stay inside the desktop viewport',()=>{
 const card={style:{},getBoundingClientRect:()=>({height:674})};
 const c=vm.createContext({innerWidth:1440,innerHeight:844,card,anchor:{getBoundingClientRect:()=>({left:858,bottom:600})}});
 vm.runInContext(src+';TESTCARD.place(card,anchor)',c);
 assert.ok(parseFloat(card.style.top)+674<=832);
 assert.equal(card.style.left,'858px');
});
test('a tall preview pins to the top safe margin for internal scrolling',()=>{
 const card={style:{},getBoundingClientRect:()=>({height:900})};
 const c=vm.createContext({innerWidth:900,innerHeight:600,card});
 vm.runInContext(src+';TESTCARD.place(card,null)',c);assert.equal(card.style.top,'12px');
});
