const {readRawConsoleFile}=require('./console_source');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const src=readRawConsoleFile(path.join(__dirname,'../../stickerloom/console/live.js'),'utf8');
const boot=s=>{const c=vm.createContext({localStorage:{getItem:()=>s&&JSON.stringify(s)},gstore(){}});vm.runInContext(src.slice(0,src.indexOf('/* ---------- models')),c);return code=>vm.runInContext(code,c)};
test('a fresh Studio defaults to 2x2 and a saved explicit 3x3 is preserved',()=>{
 assert.equal(boot(null)('cpGrid()'),'2x2');
 assert.equal(boot({})('cpGrid()'),'2x2');
 assert.equal(boot({grid:'invalid'})('cpGrid()'),'2x2');
 assert.equal(boot({grid:'3x3'})('cpGrid()'),'3x3');
 assert.equal(boot({grid:'2x2'})('cpGrid()'),'2x2');
});
