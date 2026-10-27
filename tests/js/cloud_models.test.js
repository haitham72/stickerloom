const {readConsoleFile,statement:consoleStatement}=require('./console_source');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function boot(){
 const ctx=vm.createContext({console,setTimeout:()=>0,clearTimeout(){},Date,JSON,Math,Promise,
  document:{addEventListener(){},getElementById:()=>null,querySelector:()=>null},
  localStorage:{getItem:()=>null,setItem(){}},history:{replaceState(){}},location:{hash:''},matchMedia:()=>({matches:false}),
  ACT:{},ICONS:{},RENDER:{},ic:()=>'', $:()=>null,route_:'generate',toast(){},
  READS:[],api:async u=>{ctx.READS.push(u);return ctx.AGENT_REPLY||{ok:false,j:{}}},POSTS:[],post:async(u,b)=>{ctx.POSTS.push([u,b]);return ctx.SETTINGS_REPLY||{ok:false,j:{}}}});
 ctx.window=ctx;
 const src=readConsoleFile(path.join(__dirname,'../../stickerloom/console/agent.js'),'utf8');
 // Expose closure state only in the test's copy; execute the complete script.
 vm.runInContext(src.replace(/\}\)\(\);?\}\s*$/, ';globalThis.TEST_API={A,selectorDetails,saveSet,loadAgent,beRow};})()}'),ctx);
 vm.runInContext('const {A,selectorDetails,saveSet,loadAgent,beRow}=TEST_API;',ctx);
 return code=>vm.runInContext(code,ctx);
}

const report = {
  preference: 'auto', default: 'openai:gpt-6-luna', current: 'qwen', selected: null,
  aliases: {'gpt-5.6-luna':'openai:gpt-5.6-luna','gpt-6-luna':'openai:gpt-6-luna'},
  local: [{ id: 'local:qwen', model: 'qwen', label: 'qwen', provider: 'local', vision: null, free: true }],
  fallback: ['pixel-9-flash-free', 'pixel-2-flash'].map(model => ({ id: `fallback:${model}`, model, label: model, provider: 'fallback', vision: true, free: true })),
  openai: ['gpt-6-luna','gpt-6.1-sol','gpt-5.6-luna'].map(model=>({id:'openai:'+model,model,label:model,provider:'openai',vision:false,free:false})), models: [],
};

test('the shared selector groups all providers and preserves a saved model on reopen', () => {
  const run = boot();
  run(`A.llm=${JSON.stringify(report)}; A.sess={settings:{models:{ai:'fallback:pixel-2-flash'}}};`);
  const draw = () => run(`AIU.modelSel('ai','Chat',selectorDetails().models.ai)`);
  const html = draw();
  for (const group of ['LM Studio', 'Fallback', 'OpenAI']) assert.match(html, new RegExp(`optgroup label="${group}"`));
  assert.match(html, /fallback:pixel-2-flash" selected/);
  assert.match(html, /pixel-9-flash-free/);
  assert.match(html, /Default · openai:gpt-6-luna/);
  assert.match(html, /value="openai:gpt-6-luna"/);
  assert.match(html, /value="openai:gpt-6.1-sol"/);
  assert.equal(draw(), html);
  run(`A.sess.settings.models.ai='';`);
  assert.match(draw(), /option value="" selected/);
  run(`A.sess.settings.models.ai='fallback:removed-model';`);
  assert.match(draw(), /removed-model" selected disabled/);
  run(`A.sess.settings.models.ai='gpt-5.6-luna';`);
  assert.match(draw(), /openai:gpt-5.6-luna" selected/);
  assert.doesNotMatch(draw(), /value="fallback:gpt-5.6-luna"/);
});

test('session choices save the qualified id and the stage/composer contain no AI selector', async () => {
  const run = boot();
  run(`A.llm=${JSON.stringify(report)}; A.sid='S001'; A.sess={id:'S001',settings:{models:{}}};`);
  await run(`saveSet({models:{ai:'openai:gpt-5.6-luna'}})`);
  const posts = JSON.parse(run('JSON.stringify(POSTS)'));
  assert.deepEqual(posts[0], ['/api/chat/sessions/S001/settings', { models: { ai: 'openai:gpt-5.6-luna' } }]);
  assert.doesNotMatch(run(`AIU.stageHTML('emojis',true)`), /data-agm|optgroup|model/);
  const source = readConsoleFile(path.join(__dirname, '../../stickerloom/console/agent.js'), 'utf8');
  const composerPanel = source.slice(source.indexOf('function setSet()'), source.indexOf("document.addEventListener('click'", source.indexOf('function setSet()')));
  assert.doesNotMatch(composerPanel, /modelSel\('ai'/);
});

test('Settings and the enhancer share the grouped global selector', () => {
  const run = boot();
  run(`A.llm=${JSON.stringify({ ...report, selected: 'fallback:pixel-2-flash' })};`);
  const source = { preference: 'cloud', agent: { provider: 'openai', model: 'fallback:pixel-2-flash' }, availability: {} };
  const html = run(`AIENG.rows(${JSON.stringify(source)})`);
  assert.match(html, /data-agmodel/);
  assert.doesNotMatch(html, /data-agm=ai/);
  assert.match(html, /fallback:pixel-2-flash" selected/);
  const settings = readConsoleFile(path.join(__dirname, '../../stickerloom/console/app.js'), 'utf8');
  assert.match(settings, /id=set-ai-engine/);
  assert.match(settings, /AIENG.settings\(/);
});

test('choosing Luna refreshes the session badge and engine status instead of showing the global fallback model', async () => {
  const run = boot();
  run(`A.llm=${JSON.stringify(report)}; A.sid='S001'; A.sess={id:'S001',settings:{models:{ai:'fallback:pixel-9-flash'}}};
    A.agent={preference:'cloud',agent:{provider:'openai',model:'pixel-9-flash'}};
    globalThis.LABEL={textContent:''}; globalThis.PILL={querySelector:()=>LABEL}; $=id=>id==='ai-pill'?PILL:null;
    SETTINGS_REPLY={ok:true,j:{settings:{models:{ai:'openai:gpt-5.6-luna'}}}};
    AGENT_REPLY={ok:true,j:{preference:'cloud',agent:{provider:'openai',model:'openai:gpt-5.6-luna'},agent_status:{fallback:false},availability:{cloud:{ok:true,model:'openai:gpt-5.6-luna'}}}};`);
  await run(`saveSet({models:{ai:'openai:gpt-5.6-luna'}})`);
  assert.equal(run('READS[0]'), '/api/chat/agent?session=S001');
  assert.match(run('LABEL.textContent'), /luna/);
  run('A.engineOpen=true');
  assert.match(run('beRow()'), /<small>now: cloud · openai:gpt-5.6-luna<\/small>/);
  assert.match(run('beRow()'), /value="openai:gpt-5.6-luna" selected/);
  run(`A.sid=null; A.sess=null; AGENT_REPLY={ok:true,j:{preference:'cloud',agent:{provider:'openai',model:'pixel-9-flash'}}};`);
  await run('loadAgent()');
  assert.equal(run('READS[1]'), '/api/chat/agent');
  assert.match(run('LABEL.textContent'), /pixel/);
});

test('a late status reply from another chat cannot replace the current model', async () => {
  const run = boot();
  run(`A.sid='S001'; A.agent={agent:{provider:'openai',model:'openai:gpt-5.6-luna'}};
    api=()=>new Promise(resolve=>globalThis.RESOLVE=resolve); globalThis.PENDING=loadAgent(); A.sid='S002';
    RESOLVE({ok:true,j:{agent:{provider:'openai',model:'pixel-9-flash'}}});`);
  await run('PENDING');
  assert.equal(run('A.agent.agent.model'), 'openai:gpt-5.6-luna');
});
