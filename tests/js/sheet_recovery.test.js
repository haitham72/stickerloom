const {readConsoleFile,statement:consoleStatement}=require('./console_source');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'../../stickerloom/console'),SR=require(require('./console_source').resolveConsoleFile(path.join(root,'sheet-recovery.js')));
const g={number:7,generation_id:'G007',allow:{video_sheet:{can:['A1'],allowed:['A2'],undo:['A2'],why:{A1:'outline <ring>'},final:{A3:'File cannot decode'}}}};
test('the sheet picture itself retains the picture and offers the recorded decision with escaped reason',()=>{
 const h=SR.picture(g,{id:'A1',file:'sheet.png'});
 assert.match(h,/\/out\/G007\/sheet.png/);assert.match(h,/blocked/);assert.match(h,/outline &lt;ring&gt;/);
 assert.match(h,/data-act=ssallow/);assert.match(h,/data-sheet="A1"/);assert.match(h,/Use it anyway/);
 assert.match(SR.controls(g,{id:'A2'}),/Take it back/);
 assert.doesNotMatch(SR.controls(g,{id:'A3'}),/data-act=ssallow/);
});
test('bulk pair counts current eligibility and never hides a one-sheet decision',()=>{
 assert.match(SR.bulk(g),/Use all anyway \(1\)/);assert.match(SR.bulk(g),/Take all back \(1\)/);
 assert.match(SR.bulk(g),/data-all=1/);
});
test('all Studio and chat surfaces share the sheet controls, including creator bulk controls',()=>{
 const src=readConsoleFile(path.join(root,'generate.js'),'utf8'),chat=readConsoleFile(path.join(root,'agent.js'),'utf8');
 const segment=(a,b)=>src.slice(src.indexOf(a),src.indexOf(b,src.indexOf(a)+1));
 assert.match(segment('function drawVdlg()','ACT.gpickvideo='),/SR.picture\(g,v\)/);
 assert.match(segment('function videoBox(','function videoPanel('),/SR.controls\(g,v\)/);
 assert.match(segment('function videoPanel(','const AV='),/SR.bulk\(g\)/);
 assert.match(chat,/SR.picture\(c.data,v\)/);assert.match(chat,/\['video_sheet','video sheet'\]/);
 assert.match(readConsoleFile(path.join(root,'index.html'),'utf8'),/sheet-recovery.js/);
});
