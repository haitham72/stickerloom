const {readConsoleFile, statement} = require('./console_source');
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');

test('manual duplicate and pack choices retain string ids, while batch numbers remain numeric', () => {
  const sent = [];
  const ctx = vm.createContext({ACT:{}, agSend:(text, action)=>sent.push(action)});
  const source = readConsoleFile(path.join(__dirname,'../../stickerloom/console/agent.js'));
  vm.runInContext(statement(source,'ACT.agaction ='),ctx);
  for (const [type,to] of [['emoji_batch_choice','overwrite'], ['emoji_batch_pack','P127'], ['batch_more','3']])
    ctx.ACT.agaction({dataset:{type,to,i:'[1]'}});
  assert.equal(sent[0].to,'overwrite');
  assert.equal(sent[1].to,'P127');
  assert.equal(sent[2].to,3);
  assert.deepEqual(Array.from(sent[0].indexes),[1]);
});
