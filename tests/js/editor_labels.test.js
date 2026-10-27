// UX-04: every image-editor control has a descriptive accessible name, with its
// unit where it has one; the visible label above a slider/number pair is not
// associated with either input, and "B"/"I" alone do not say Bold/Italic.
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),path=require('node:path');
const {readConsoleFile,statement}=require('./console_source');
const src=readConsoleFile(path.join(__dirname,'../../stickerloom/console/editor.js'));

function rig(){
  const ctx=vm.createContext({esc:s=>String(s??''),FONTS:['Inter','Rubik'],E:{border:{on:true,color:'#fff',w:4,shadow:true,blur:6},outlined:false}});
  for(const m of ['const sl=','const tg=','function textCtl(','function transform(','function borderCtl('])vm.runInContext(statement(src,m),ctx);
  return ctx;
}
const controls=html=>html.match(/<(input|select|textarea)\b[^>]*>/g)||[];
const nameOf=tag=>(tag.match(/aria-label="([^"]*)"/)||[])[1];

test('text, transform and border controls are all named',()=>{
  const ctx=rig();
  const html=vm.runInContext(`textCtl({text:'hi',font:'Inter',size:64,bold:0,italic:0,color:'#000',stroke:'#fff',sw:3})+transform({s:1,r:0})+borderCtl()`,ctx);
  const tags=controls(html);
  assert.ok(tags.length>=14,'every control was rendered');
  for(const t of tags)assert.ok(nameOf(t),`unnamed control: ${t}`);
  const names=tags.map(nameOf);
  for(const n of ['Text','Font','Text size in px','Bold','Italic','Fill colour','Outline colour','Scale (%)','Scale in %','Rotate (degrees)','Rotate in degrees','Border','Border colour','Border width in px','Shadow','Shadow blur (px)'])
    assert.ok(names.includes(n),n);
});

test('the names do not change what the controls send',()=>{
  const ctx=rig(),html=vm.runInContext(`transform({s:1.5,r:-30})`,ctx);
  assert.deepEqual(controls(html).map(t=>t.match(/data-inp=(\w+)/)[1]),['scale','scale','rot','rot']);
  assert.match(html,/type=range data-inp=scale min=10 max=400 value=150 /);
});

test('the Save sticker labels point at their fields',()=>{
  for(const id of ['xn','xe','xp','xnn'])assert.match(src,new RegExp(`<label for=${id}>`),id);
});
