// Parse actual production statements, regardless of line breaks or the feature file they now live in.
// Acorn 8.15.0 is vendored under its MIT license; no installation or network access is needed for tests.
const fs = require('node:fs');
const path = require('node:path');
const acorn = require('./vendor/acorn.cjs');
const DIR = path.join(__dirname, '../../stickerloom/console');
const ASSETS = JSON.parse(fs.readFileSync(path.join(DIR,'ui/manifest.json'),'utf8'));
const consoleFiles = () => Object.keys(ASSETS).filter(n=>n.endsWith('.js')&&!n.startsWith('vendor/'));
const resolveConsoleFile = file => path.resolve(path.dirname(file))===path.resolve(DIR)&&ASSETS[path.basename(file)]
  ? path.join(DIR,'ui',ASSETS[path.basename(file)]) : file;
const readRawConsoleFile = (file,encoding='utf8') => fs.readFileSync(resolveConsoleFile(file),encoding);
const GROUPS = {
  'generate.js': ['studio-core.js','studio-tiles.js','studio-sheet.js','studio-prompt.js','studio-animate.js','studio-panel.js'],
  'app.js': ['lifecycle.js','app.js','library.js','settings.js'],
  'reactions.js': ['reaction-preview-cache.js','reactions.js'],
  'agent.js': ['agent.js','chat-cards.js','chat-stage.js','chat-core.js'],
};
const parse = source => acorn.parse(source, {ecmaVersion:'latest'});
function compact(source) {
  let out = '', previous = '', previousType = '';
  const tokens=[...acorn.tokenizer(source, {ecmaVersion:'latest'})], skip=new Set();
  tokens.forEach((t,i)=>{
    if (t.type.label==='(' && tokens[i+1]?.type.label==='name' && tokens[i+2]?.type.label===')' && tokens[i+3]?.type.label==='=>') {skip.add(i);skip.add(i+2);}
    if (t.type.label===';' && tokens[i+1]?.type.label==='}') skip.add(i);
    if (t.type.label===',' && [')',']','}'].includes(tokens[i+1]?.type.label) && ![',','['].includes(tokens[i-1]?.type.label)) skip.add(i);
  });
  for (const [i,t] of tokens.entries()) {
    if (skip.has(i)) continue;
    const raw = source.slice(t.start,t.end);
    if (previousType !== 'template' && t.type.label !== 'template' && (/[\w$]$/.test(previous) && /^[\w$]/.test(raw) || /[+]$/.test(previous) && /^[+]/.test(raw) || /[-]$/.test(previous) && /^[-]/.test(raw))) out += ' ';
    out += raw;
    previous = raw;
    previousType = t.type.label;
  }
  return out;
}
function canonical(source) {
  return parse(source).body.map(n => compact(source.slice(n.start,n.end))).join('\n');
}
function readConsoleFile(file, encoding='utf8') {
  const name=path.basename(file);
  if (path.resolve(path.dirname(file)) === path.resolve(DIR) && name.endsWith('.js')) {
    return (GROUPS[name]||[name]).map(f=>canonical(readRawConsoleFile(path.join(DIR,f),'utf8'))).join('\n');
  }
  return readRawConsoleFile(file,encoding);
}
const statementsCache = new Map();
function statement(source, marker) {
  const wanted=compact(marker);
  let nodes=statementsCache.get(source);
  if (!nodes) {
    nodes=[];
  function visit(node) {
    if (!node || typeof node !== 'object') return;
    if (typeof node.type === 'string' && /(?:Statement|Declaration)$/.test(node.type)) {
      nodes.push(node);
    }
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === 'object') visit(value);
    }
  }
  visit(parse(source));
    nodes.sort((a,b)=>(a.end-a.start)-(b.end-b.start));
    statementsCache.set(source,nodes);
  }
  const candidates=nodes.filter(n=>source.startsWith(wanted,n.start)).map(n=>source.slice(n.start,n.end));
  if (!candidates.length) throw Error('Missing production statement: '+marker);
  return candidates.sort((a,b)=>a.length-b.length)[0];
}
module.exports={readConsoleFile,readRawConsoleFile,resolveConsoleFile,consoleFiles,statement,canonical,GROUPS};
if (require.main===module) {
  const names=consoleFiles();
  names.push('generate.js');
  process.stdout.write(JSON.stringify(Object.fromEntries(names.map(n=>[n,readConsoleFile(path.join(DIR,n))]))));
}
