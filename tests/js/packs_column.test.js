const {readConsoleFile, statement} = require('./console_source');
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');
const source = readConsoleFile(path.join(__dirname, '../../stickerloom/console/app.js'));

function column(packs) {
  const nodes = {};
  const col = {set innerHTML(value) {
    this.html = value;
    nodes['c2-packs'] = {scrollTop: 0};
    nodes.c2q = {focus() {}, setSelectionRange() {}};
  }};
  nodes.col2 = col;
  nodes.c2tog = {setAttribute() {}};
  const ctx = vm.createContext({
    LIB: {packs}, route_: 'library', PACK_ID: null, C2Q: '', PKFOLD: new Map(),
    COL2: ['library', 'pack'], $: id => nodes[id],
    document: {body: {classList: {toggle() {}}}},
    esc: String, ic: () => '', coverMedia: () => '',
  });
  vm.runInContext(statement(source, 'const packEntries =') + '\n' + statement(source, 'function drawCol2()'), ctx);
  return {ctx, nodes, draw: () => vm.runInContext('drawCol2()', ctx),
    ids: () => [...col.html.matchAll(/data-act=openpack data-id=([^>\s]+)/g)].map(m => m[1])};
}

const pack = (id, created, extra = {}) => ({id, name: id, created, stickers: [], ...extra});

test('Packs lists newest first without changing library order or group parent ownership', () => {
  const packs = [pack('old', 1), pack('parent', 2, {group: 'g', lead: true}),
    pack('middle', 3), pack('recent-child', 4, {group: 'g'})];
  const c = column(packs);
  c.ctx.PKFOLD.set('g', true);
  c.draw();
  assert.deepEqual(c.ids(), ['parent', 'recent-child', 'middle', 'old']);
  assert.deepEqual(packs.map(p => p.id), ['old', 'parent', 'middle', 'recent-child']);
});

test('opening packs preserves the list scroll through both router and async pack redraws', () => {
  const c = column([pack('old', 1), pack('new', 2)]);
  c.draw();
  c.nodes['c2-packs'].scrollTop = 840;
  c.ctx.route_ = 'pack';
  c.draw();
  c.ctx.PACK_ID = 'old';
  c.draw();
  assert.equal(c.nodes['c2-packs'].scrollTop, 840);
  c.ctx.PACK_ID = 'new';
  c.draw();
  assert.equal(c.nodes['c2-packs'].scrollTop, 840);
  c.nodes.c2q.oninput({target: {value: 'new', selectionStart: 3}});
  assert.equal(c.nodes['c2-packs'].scrollTop, 0, 'a new search starts at its first result');
  assert.deepEqual(c.ids(), ['new']);
});


test('Library count wording uses backend classifications and neutral filtered fallback',()=>{
 const {readConsoleFile,statement}=require('./console_source');const vm=require('node:vm'),path=require('node:path');
 const source=readConsoleFile(path.join(__dirname,'../../stickerloom/console/library.js'));
 const ctx=vm.createContext({});vm.runInContext(statement(source,'function libCountLabel('),ctx);
 assert.equal(ctx.libCountLabel({sticker_count:2,reaction_count:1},3),'2 stickers · 1 reaction');
 assert.equal(ctx.libCountLabel(null,1),'1 item');assert.equal(ctx.libCountLabel({},3),'3 items');
});
