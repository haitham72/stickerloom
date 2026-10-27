const {readRawConsoleFile}=require('./console_source');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
// Surface repairs with no behavior change (F-24/T-065, F-36/T-086, F-30/T-074):
// the cover badge never intercepts clicks, the phone viewer gives metadata a
// full-width row, and welcome slides use their own readable ink in both themes.
const css=readRawConsoleFile(path.join(__dirname,'../../stickerloom/console/studio.css'),'utf8');
const theme=readRawConsoleFile(path.join(__dirname,'../../stickerloom/console/theme.css'),'utf8');

test('the #pkgrid cover badge keeps position/z-order but never intercepts a click',()=>{
  assert.match(css,/#pkgrid \.cell \.badge2\{pointer-events:none\}/);
  assert.match(css,/\.cell \.badge2\{position:absolute;left:7px;top:7px;/,'position and text styling untouched');
});

test('phone viewer: metadata wraps full-width on row one, controls on row two',()=>{
  const block=css.slice(css.indexOf('#modal .mbox{max-height'));
  assert.ok(block.length>0,'phone grid rule exists');
  assert.match(block,/grid-template-columns:44px minmax\(0,1fr\) 44px/);
  assert.match(block,/\.mbox>\.mrow>div:nth-child\(2\)\{grid-column:1\/3;grid-row:1;min-width:0/);
  assert.match(block,/\[data-act=lcclose\]\{grid-column:3;grid-row:1/);
  assert.match(block,/\[data-act=lcprev\]\{grid-column:1;grid-row:2/);
  assert.match(block,/\.toggles\{grid-column:2;grid-row:2;display:grid;grid-template-columns:1fr 1fr/);
  assert.match(block,/\[data-act=lcnext\]\{grid-column:3;grid-row:2/);
  assert.match(block,/#modal \.mbox\{max-height:calc\(100dvh - 24px\);overflow-y:auto\}/,'actions stay reachable');
  assert.match(css,/\.mrow\{display:flex;gap:8px/,'desktop flex layout untouched');
});

test('welcome slides use their own ink in both themes; dots are 44px targets',()=>{
  assert.match(css,/--wl-ink:#123546;--wl-ink2:#345969;/,'light tokens');
  assert.match(theme,/--wl-ink:#123546;--wl-ink2:#345969;/,'dark tokens match the same pale artwork');
  assert.match(css,/\.wl-t h2\{[^}]*color:var\(--wl-ink\)/);
  assert.match(css,/\.wl-t p\{margin:0;color:var\(--wl-ink2\)/);
  assert.match(css,/\.wl-d\{[^}]*height:44px;[^}]*justify-content:center\}/,'dot hit area without a big glyph');
  assert.match(css,/\.wl-d i\{[^}]*margin-top:-2\.5px;[^}]*height:5px/,'the 5px progress bar stays small and centered');
});
