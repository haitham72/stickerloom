const {readConsoleFile,statement:consoleStatement}=require('./console_source');
// The stage control's builder (pure half of agent.js): a pill that opens the task slider on the right; image/video model selectors on the left.
// Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const U = require(require('./console_source').resolveConsoleFile(require('node:path').join(__dirname,'../../stickerloom/console/agent.js')));
const icon = name => `<svg data-icon="${name}"></svg>`;
const fs = require('node:fs');
const path = require('node:path');
const { loadConsole } = require('./console_vm');

function handlers() {
  const source = readConsoleFile(path.join(__dirname, '../../stickerloom/console/agent.js'), 'utf8')
    .replace(/\}\)\(\);?\}\s*$/,  ';globalThis.ChatTest={A,drawStage,loadStage,botHTML,setSet,setEngine,drawBar};})()}');
  return loadConsole([], { extra: `
    const window=globalThis,EVENTS={};
    document.addEventListener=(type,fn)=>(EVENTS[type]||=[]).push(fn);
    const RANGE={focus(){document.activeElement=this}};
    const STAGE={id:'ag-stage',innerHTML:'',contains(el){return el===RANGE},querySelector(){return RANGE}};
    DOMSTUB['ag-stage']=STAGE;
    const SETTINGS={id:'ai-set',innerHTML:'',classList:{toggle(){}},setAttribute(){}};
    DOMSTUB['ai-set']=SETTINGS;
    const ENGINE={innerHTML:'',classList:{toggle(){}}};DOMSTUB['ai-engine-set']=ENGINE;
    ${source}
    ChatTest.A.cat={};ChatTest.A.llm={models:[]};
  ` });
}

test('the opening click stays inside the stage even after drawing detaches the original button', async () => {
  const run = handlers();
  await run('ACT.agstage()');
  assert.equal(run('ChatTest.A.stageOpen'), true);
  assert.equal(run('APICALLS'), 0, 'opening the task slider never fetches model lists');
  run(`EVENTS.click.forEach(fn=>fn({target:{closest:()=>null},composedPath:()=>[{id:''},STAGE,document]}))`);
  assert.equal(run('ChatTest.A.stageOpen'), true, 'the original event path keeps the popover open');
  assert.match(run('STAGE.innerHTML'), /class=ag-stage-pop/);
  run(`EVENTS.click.forEach(fn=>fn({target:{closest:()=>null},composedPath:()=>[document]}))`);
  assert.equal(run('ChatTest.A.stageOpen'), false, 'a real outside click closes it');
});

test('a new chat reads the person’s stage; picking it saves preferences without creating an empty chat', async () => {
  const run = handlers();
  run(`APIREPLIES.push({ok:true,j:{stage:'animation'}})`);
  await run('ChatTest.loadStage()');
  assert.match(run('STAGE.innerHTML'), /aria-label="Task stage: Animation\. Change stage"/);
  run(`POSTREPLIES.push({ok:true,j:{stage:'emojis'}})`);
  await run(`ACT.agstagepick({dataset:{v:'emojis'}})`);
  assert.equal(run('ChatTest.A.sid'), null);
  assert.equal(run('POSTS.length'), 1);
  assert.equal(run('POSTS[0][0]'), '/api/prefs');
  assert.match(run('STAGE.innerHTML'), /aria-label="Task stage: Stickers\. Change stage"/);
  run(`ChatTest.A.sess={settings:{stage:'prompt'}};ChatTest.drawStage()`);
  assert.match(run('STAGE.innerHTML'), /aria-label="Task stage: Prompt\. Change stage"/, 'an opened session shows its effective saved stage');
});

test('descriptions put the sticker card before the streamed explanation', () => {
  const run = handlers();
  const html = run(`ChatTest.botHTML({status:'working',text:'He is reading',cards_first:true,
    cards:[{type:'stickers',stickers:[]}],steps:[]})`);
  assert.ok(html.indexOf('car') < html.indexOf('He is reading'));
});

test('closed it is one themed icon pill, named for assistive technology, with no slider', () => {
  const h = U.stageHTML('emojis', false, icon);
  assert.match(h, /aria-label="Task stage: Stickers\. Change stage"/);
  assert.match(h, /<svg data-icon="emoji"><\/svg>/);
  assert.match(h, /<svg data-icon="down"><\/svg>/);
  assert.doesNotMatch(h, />Stickers</, 'the closed control uses the icon instead of a text label');
  assert.doesNotMatch(h, /ag-range|ag-slider|ag-stage-pop/, 'the slider is not on screen until the pill is clicked');
});

test('open, six themed icons retain stage names, tooltips and the selected state', () => {
  const h = U.stageHTML('animation', true, icon);
  assert.match(h, /aria-expanded=true/);
  assert.match(h, /<input type=range class=ag-range data-agrange min=0 max=5 step=1 value=3 aria-label="How far a new request goes" aria-valuetext="Animation">/);
  assert.equal((h.match(/<i class="on"><\/i>/g) || []).length, 4, 'the ticks up to the thumb are filled');
  const stops = [...h.matchAll(/class="ag-stop[^"]*" data-act=agstagepick data-v=(\w+) aria-label="([^"]+)" aria-pressed=(true|false) title="([^"]+)"[^>]*><span[^>]*><svg data-icon="([^"]+)"/g)];
  assert.deepEqual(stops.map(m => m[1] + ':' + m[2] + ':' + m[5]),
    ['auto:Auto:ai', 'prompt:Prompt:text', 'emojis:Stickers:emoji', 'animation:Animation:play', 'export:Telegram:telegram', 'api:Export:upload']);
  assert.deepEqual(stops.filter(m => m[3] === 'true').map(m => m[1]), ['animation']);
  assert.ok(stops.every(m => m[4].startsWith(m[2] + ':')));
  assert.match(h, /title="Animation: animate the sticker"/);
  assert.match(h, /class=ag-stage-caption aria-live=polite><b>Animation:<\/b><span>animate the sticker<\/span>/);
  assert.match(h, /class="ag-stop on" data-act=agstagepick data-v=animation/);
  assert.doesNotMatch(h, /Models|Options|<select|role=switch|sp-hint|sp-h>/);
});

test('the left selector loads the shared catalog and shows image/video models and creation options at any task stage', async () => {
  const run = handlers();
  run(`ChatTest.A.cat=null;ChatTest.A.sid='S001';ChatTest.A.sess={settings:{stage:'prompt',models:{image:'nano_banana_pro'}}};
    APIREPLIES.push({ok:true,j:{image:[{id:'nano_banana_flash',label:'Nano Banana 2'},{id:'nano_banana_pro',label:'Nano Banana Pro'}],
      video:[{id:'kling3',label:'Kling 3.0'}],more:{},defaults:{image:'nano_banana_flash',video:'kling3'}}})`);
  await run('ACT.agset()');
  const html = run('SETTINGS.innerHTML');
  assert.equal(run('APICALLS'), 1, 'one shared image/video catalog request');
  assert.match(html, /data-agm=image aria-label="Image model"/);
  assert.match(html, /value="nano_banana_pro" selected/);
  assert.match(html, /Default · Nano Banana 2/);
  assert.match(html, /data-agm=video aria-label="Video model"/);
  assert.doesNotMatch(html, /data-agm=ai|data-agmodel|ag-range|data-act=agbe|data-act=agsetgrid/);
  assert.match(html, /data-act=agsetact data-v=predefined class="on">Predefined/);
  assert.match(html, /data-act=agsetask role=switch/);
  run(`POSTREPLIES.push({ok:true,j:{settings:{stage:'prompt',models:{image:'nano_banana_pro',video:'kling3'}}}})`);
  await run(`EVENTS.change[1]({target:{dataset:{agm:'video'},value:'kling3'}})`);
  assert.equal(run("POSTS[0][0]"), '/api/chat/sessions/S001/settings');
  assert.equal(run("JSON.stringify(POSTS[0][1])"), '{"models":{"video":"kling3"}}');
  assert.equal(run('ChatTest.A.sess.settings.stage'), 'prompt', 'a model pick leaves the task stage unchanged');
  assert.equal(run('ChatTest.A.sess.settings.models.image'), 'nano_banana_pro');
  run('ACT.agstage()');
  assert.equal(run('ChatTest.A.setOpen'), false, 'opening the right slider dismisses the left selector');
});

test('the top-right LLM selector uses its own popup, without creation options or a second model selector', async () => {
  const run=handlers();
  run(`APIREPLIES.push({ok:true,j:{ok:true,current:'qwen',models:[{id:'qwen'},{id:'other'}]}})`);
  await run('ACT.agengine()');
  assert.equal(run('ChatTest.A.engineOpen'),true);
  assert.equal(run('ChatTest.A.setOpen'),false);
  assert.match(run('ENGINE.innerHTML'), /data-agm=ai aria-label="Chat model"/);
  assert.equal((run('ENGINE.innerHTML').match(/<select/g)||[]).length,1);
  assert.doesNotMatch(run('ENGINE.innerHTML'), /data-agm=image|data-agm=video|ag-range|data-act=agsetact|data-act=agsetask|data-act=agsetgrid/);
  await run('ACT.agset()');
  assert.equal(run('ChatTest.A.engineOpen'),false);
  assert.equal(run('ChatTest.A.setOpen'),true);
  run(`DOMSTUB['ag-bar']={innerHTML:''};DOMSTUB['ag-styles']={innerHTML:''};ChatTest.drawBar()`);
  assert.match(run("DOMSTUB['ag-bar'].innerHTML"), /data-act="aggridtoggle"/);
  assert.doesNotMatch(run("DOMSTUB['ag-bar'].innerHTML"), /data-act=agsetask/);
});

test('an unknown stage reads as Auto, and old creator switches migrate like the server', () => {
  assert.match(U.stageHTML('bogus', false, icon), /aria-label="Task stage: Auto\. Change stage"/, 'unknown or unset: Auto');
  assert.equal(U.stageOf({ stage: 'prompt' }), 'prompt');
  assert.equal(U.stageOf({ stage: 'api' }), 'api');
  assert.equal(U.stageOf({}), 'auto');
  assert.equal(U.stageOf(null), 'auto');
  assert.equal(U.stageOf({ creator: { on: true, scope: 'video' } }), 'export');
  assert.equal(U.stageOf({ creator: { on: true, scope: 'images' } }), 'auto');
  assert.equal(U.stageOf({ stage: 'emojis', creator: { on: true, scope: 'video' } }), 'emojis');
});
