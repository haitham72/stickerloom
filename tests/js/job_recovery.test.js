const {readConsoleFile,statement:consoleStatement}=require('./console_source');
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const JR=require(require('./console_source').resolveConsoleFile(require('node:path').join(__dirname,'../../stickerloom/console/job-recovery.js')));
test('four explicit recovery actions are ordered cheapest first and paid Retry is not primary',()=>{
  const html=JR.controls({id:'J048',status:'TIMEOUT',external_task_id:'paid-ticket'});
  const names=[...html.matchAll(/data-act=(\w+)/g)].map(m=>m[1]);
  assert.deepEqual(names,['jrrefresh','jrcheck','jrcontinue','jrretry']);
  assert.match(html,/Retry · SPENDS/);assert.doesNotMatch(html,/class="btn sm pri"/);
  assert.match(html,/no provider call/);assert.match(html,/cannot double-charge/);
});
test('divergence is named in words and malicious provider text is escaped',()=>{
  const html=JR.controls({id:'J048',status:'FAILED',provider_check:{message:'Divergence, not a failure: <img>'}});
  assert.match(html,/Divergence, not a failure/);assert.match(html,/&lt;img&gt;/);
});
test('all job surfaces use the shared controls and Refresh only reads local endpoints',()=>{
  const root=path.join(__dirname,'../../stickerloom/console');
  for(const file of ['live.js','generate.js','agent.js'])assert.match(readConsoleFile(path.join(root,file),'utf8'),/JR\.controls\(/);
  const src=readConsoleFile(path.join(root,'job-recovery.js'),'utf8');
  const refresh=src.slice(src.indexOf('const localRefresh='),src.indexOf('ACT.jrrefresh='));
  assert.match(refresh,/new EventSource/);assert.doesNotMatch(refresh,/refreshHf|higgsfield|\/live\/|post\(/);
});
