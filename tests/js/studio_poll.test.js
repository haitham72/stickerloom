const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// F-05 (T-004): Studio refreshes serialize. Concurrent force queues one rerun,
// interval ticks never overlap, hidden tabs skip interval work while explicit
// force still runs, and both flags release on every failure. Cadence unchanged.
// (Production source is read in canonical form, which drops most spaces, so
// patterns below allow optional whitespace.)
const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const src=readConsoleFile(path.join(__dirname,'../../stickerloom/console/studio-core.js'),'utf8');
function statement(marker){return consoleStatement(src,marker);}
const ws=s=>new RegExp(s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&').replace(/ /g,'\\s*'));

test('tick serializes with in-flight and pending-force flags',()=>{
  const tick=statement('async function tick(');
  assert.match(src,ws('let tickBusy=false,tickForce=false'),'both flags start released');
  assert.match(tick,ws('if (force && tickBusy) { tickForce = true; return }'),'concurrent force queues one rerun');
  assert.match(tick,ws('if (!force && tickBusy) return;'),'interval ticks never overlap');
  assert.match(tick,/document\s*\.\s*hidden/,'hidden tabs skip interval work');
  assert.match(tick,ws('tickBusy = true;'),'the running read holds the flag');
  assert.match(tick,/finally\s*\{\s*tickBusy\s*=\s*false;\s*if\s*\(tickForce\)\s*\{\s*tickForce\s*=\s*false;\s*tick\(true\)\s*\}\s*\}/,'every settle releases and replays one queued force');
});

test('polling cadence and request surface are unchanged',()=>{
  assert.match(src,ws('setInterval(tick,700)'),'the 700-ms cadence is untouched');
  assert.match(src,/api\(\s*'\/api\/generations'\s*\)/,'the health read is untouched');
  assert.match(src,/SES\s*\.\s*gens\s*=\s*SES\s*\.\s*gens\s*\.\s*filter/,'404 session cleanup is untouched');
});
