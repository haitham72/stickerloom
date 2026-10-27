const test = require('node:test');
const assert = require('node:assert/strict');
const {readConsoleFile} = require('./console_source');
const css = readConsoleFile(require('node:path').join(__dirname, '../../stickerloom/console/theme.css'));

// Test palette roles and readable states, not a particular set of brand hex values.
const dark = css.match(/html\[data-theme=dark\]\s*\{([^}]+)\}/)[1];
const tokens = Object.fromEntries([...dark.matchAll(/(--[\w-]+)\s*:\s*([^;]+);?/g)].map(m => [m[1], m[2].trim()]));
function resolve(value) {
  for (let i = 0; i < 8 && /^var\(/.test(value || ''); i++) value = tokens[value.match(/^var\((--[\w-]+)\)/)[1]];
  assert.match(value || '', /^#[\da-f]{6}$/i, 'palette role must resolve to a solid six-digit color');
  return [1,3,5].map(i => parseInt(value.slice(i, i + 2), 16) / 255);
}
function luminance(value) {
  return resolve(value).map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4)
    .reduce((n, c, i) => n + c * [.2126, .7152, .0722][i], 0);
}
function contrast(ink, bg) {
  const a = luminance(ink), b = luminance(bg);
  return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
}
function declarations(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const matches = [...css.matchAll(new RegExp(escaped + '\\s*\\{([^}]+)\\}', 'g'))];
  assert.ok(matches.length, 'missing scoped state: ' + selector);
  const values = new Map();
  for (const match of matches) for (const declaration of match[1].split(';')) {
    const colon = declaration.indexOf(':');
    if (colon > 0) values.set(declaration.slice(0, colon).trim(), declaration.slice(colon + 1).trim());
  }
  return [...values].map(([key, value]) => key + ':' + value).join(';');
}

test('dark surfaces have neutral depth while text, selection and message roles remain readable', () => {
  const levels = ['--bg', '--sf', '--raise', '--fill'].map(k => luminance(tokens[k]));
  levels.forEach((value, i) => {if (i) assert.ok(value > levels[i - 1], 'surface tiers must have increasing depth');});
  for (const key of ['--bg', '--sf', '--wash']) {
    const rgb = resolve(tokens[key]);
    assert.ok(Math.max(...rgb) - Math.min(...rgb) < .07, key + ' must not tint the entire workspace');
  }
  for (const [ink,bg] of [['--tx','--sf'],['--mut','--sf'],['--mut2','--fill'],['--ink','--wash'],['--ink3','--sf'],['--on-pri','--pri'],['--on-pri','--pri-d'],['--tx','--chat-user-bg']]) {
    assert.ok(contrast(tokens[ink], tokens[bg]) >= 4.5, ink + ' on ' + bg + ' needs readable text contrast');
  }
  assert.ok(luminance(tokens['--chat-user-bg']) > luminance(tokens['--wash']), 'user messages need a distinguishable surface');
});

test('dark primary hover retains an action surface with contrasting text', () => {
  const rule = declarations('html[data-theme=dark] .btn.pri:hover');
  const background = rule.match(/background\s*:\s*([^;]+)/)?.[1].trim();
  const color = rule.match(/(?:^|;)\s*color\s*:\s*([^;]+)/)?.[1].trim() || tokens['--on-pri'];
  assert.ok(contrast(color, background) >= 4.5);
  assert.notDeepEqual(resolve(background), resolve(tokens['--fill']), 'primary hover cannot become a secondary surface');
});

test('dark Chat separates messages and composer without the light aurora dominating reading', () => {
  assert.match(declarations('html[data-theme=dark] .ai-m.user .b'), /background:\s*var\(--chat-user-bg\)/);
  const ambient = declarations('html[data-theme=dark] .ai-blob');
  assert.ok(Number(ambient.match(/opacity:\s*([.\d]+)/)?.[1]) <= .12, 'ambient art must remain behind the reading surface');
  const composer = declarations('html[data-theme=dark] .ai-box');
  assert.match(composer, /background:\s*var\(--raise\)/);
  assert.match(composer, /border/);
});
