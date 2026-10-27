const {readConsoleFile}=require('./console_source');
// F-05 (T-003): the queue clock draws once per tick. Only one 1000-ms queue
// interval may exist: the retained one that refreshes relative times
// (data-lvt) and redraws only while work is active/running.
const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const src=readConsoleFile(path.join(__dirname,'../../stickerloom/console/live.js'),'utf8');

test('one queue 1000-ms interval registration, one drawLive per clock tick',()=>{
  const clocks=(src.match(/setInterval\(\(\)=>\{[^}]*drawLive\(\)\},1000\)/g)||[]);
  assert.equal(clocks.length,1,'the duplicate bare redraw interval is gone');
  assert.match(clocks[0],/data-lvt/,'the retained clock still refreshes relative times');
  assert.match(clocks[0],/QACTIVE/,'the retained clock keeps its active/run predicate');
});
