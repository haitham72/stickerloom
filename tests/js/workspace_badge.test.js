const {readConsoleFile, statement} = require('./console_source');
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');
const source = readConsoleFile(path.join(__dirname, '../../stickerloom/console/app.js'));

// UX-06: the rail logo must say "Test workspace" whenever /api/generations' paths.default is false,
// and say nothing extra when it is the real out/ (GPATHS null, the field missing, or default:true).
function run(gpaths) {
  const rail = {innerHTML: ''};
  const ctx = vm.createContext({
    ACT: {}, RME: false, route_: 'home', RAIL: [], RAILOF: {},
    esc: s => String(s ?? ''), ic: () => '', CTAB: 'agent',
    GPATHS: gpaths, meOf: () => null, meInitials: () => 'Y', rmeMenu: () => '',
    $: id => (id === 'rail' ? rail : {innerHTML: ''}),
  });
  vm.runInContext(statement(source, 'function drawRail()'), ctx);
  vm.runInContext('drawRail()', ctx);
  return rail.innerHTML;
}

test('a copied/task out/ (paths.default === false) shows the Test badge, never silently', () => {
  const html = run({input: '/in', out: '/tmp/outcopy', default: false});
  assert.match(html, /class=ws-badge/);
  assert.match(html, /Test workspace: \/tmp\/outcopy/);
});

test('the real out/ (default: true), and GPATHS not loaded yet, show no badge', () => {
  assert.doesNotMatch(run({input: '/in', out: '/real/out', default: true}), /ws-badge/);
  assert.doesNotMatch(run(null), /ws-badge/);
});
