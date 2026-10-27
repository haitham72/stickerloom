const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const path = require('node:path');
const {readConsoleFile, statement} = require('./console_source');
const source = readConsoleFile(path.join(__dirname, '../../stickerloom/console/composer.js'));

test('New request stays visible for new and reopened batches without a disclosure toggle', () => {
  for (const gens of [[], [146]]) {
    let mounted;
    const prompt = {value:'Draft to preserve',scrollHeight:80,style:{},addEventListener(){}};
    const box = {addEventListener(){}};
    const group = {querySelectorAll(){return []},insertBefore(node){mounted=node}};
    const ctx = vm.createContext({
      document:{querySelector(){return group},getElementById(){return null},createElement(tag){return {tagName:tag.toUpperCase(),setAttribute(k,v){this[k]=v}}}},
      LIVE:{m:{}},SES:{gens,prompt:'Saved prompt'},CP:{},gdOn:()=>false,
      $:id=>id==='prompt'?prompt:box,
      composerDraw(){},
    });
    vm.runInContext(statement(source,'let CPKEEP')+';'+statement(source,'const cpHost')+';'+statement(source,'function composerMount'),ctx);
    vm.runInContext('composerMount()',ctx);
    assert.equal(mounted.tagName,'SECTION','composer must remain available, including after opening a batch');
    assert.equal(mounted['aria-label'],'New request');
    assert.doesNotMatch(mounted.innerHTML,/<summary\b/,'New request is not a collapsible control');
    assert.match(mounted.innerHTML,/New request/);
    assert.match(mounted.innerHTML,/id=prompt/);
    assert.equal(prompt.value,'Saved prompt','mounting does not discard the draft');
  }
});
