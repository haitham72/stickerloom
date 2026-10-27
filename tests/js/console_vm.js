// A helper of the node tests (it is not a test: only *.test.js run): load whole console scripts into ONE vm context, the way the browser shares one global scope, with the few globals they use
// (app.js, generate.js, live.js, packs.js) stubbed. `run(code)` evaluates in that context, so the top-level `const`s of the scripts are reachable. Nothing here touches a DOM or a server.
const vm = require('node:vm');
const path = require('node:path');
const { GROUPS, readConsoleFile, readRawConsoleFile, statement } = require('./console_source');

const CONSOLE = path.join(__dirname, '..', '..', 'stickerloom', 'console');
const BASE = `
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ic=n=>'<svg data-i='+n+'></svg>';
const ICONS={},RENDER={},ACT={};
let route_='generate';
const DOMSTUB={},$=id=>DOMSTUB[id]||null;
let APICALLS=0;
const APIREPLIES=[],POSTREPLIES=[];
const POSTS=[];                                    // every post a handler sends: [url, body]
const api=async()=>{APICALLS++;return APIREPLIES.shift()||{ok:false,status:404,j:{}}},post=async(u,b)=>{POSTS.push([u,b]);return POSTREPLIES.shift()||{ok:false,status:404,j:{}}};
const DIALOGS=[],toast=()=>{},dlg=html=>DIALOGS.push(html),closeDlg=()=>{};
const media=s=>'<img data-m='+s.id+'>',coverMedia=p=>'<img data-c='+p.id+'>';
let LIB={packs:[]};
const packById=id=>LIB.packs.find(p=>p.id===id);
const loadLib=async()=>LIB;
const VLM={then:null},vlmState=()=>'0',vlmSet=()=>{};
`;
const shellSource=readConsoleFile(path.join(CONSOLE,'app.js'));
const routeScopes=['const ROUTELIFE','function routeOwner'].map(marker=>statement(shellSource,marker)).join('\n');
const studioSource=readConsoleFile(path.join(CONSOLE,'studio-core.js'));
const studioExtensions=['const GSTUDIO','function gExtraBody','function gExtraSteps'].map(marker=>statement(studioSource,marker)).join('\n');
const STUDIO = studioExtensions + `
const GS={tab:'stickers',outline:12};
const GM=new Map();
let SES={prompt:'',gens:[],off:[],pack:''},glast='';
const sessionGens=()=>SES.gens.map(id=>GM.get(id)).filter(Boolean);
const tick=()=>{};
function gbodyHtml(gs,c){return gExtraBody(GS.tab,gs,c)??'ORIGINAL BODY'}
function stepsHtml(c){return 'ORIGINAL STEPS'}
const PKPT={d:{},c:{}};
function ptBody(j,s){return '<ptbody data-for='+(s?s.id:'')+'>'+(j?'data':'null')+'</ptbody>'}
function ptRow(r,s,draft){return '<div class="pk-row" data-particle-row='+r.id+'>'+(draft?'Draft':'v'+r.version)+'</div>'}
const ptCount=j=>(j.rows||[]).length+(j.drafts||[]).length+(j.created||[]).length+(j.saved||[]).length;
async function ptLoad(){}
`;
function loadConsole(files, { studio = false, extra = '' } = {}) {
  const ctx = vm.createContext({
    console, setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, Date, JSON, Math,
    document: { addEventListener() {}, getElementById: () => null, querySelector: () => null, hidden: false },
    localStorage: { getItem: () => null, setItem() {} },
  });
  vm.runInContext(BASE + routeScopes + (studio ? STUDIO : '') + extra, ctx, { filename: 'stubs.js' });
  for (const f of files.flatMap(f=>GROUPS[f]||[f])) vm.runInContext(readRawConsoleFile(path.join(CONSOLE, f), 'utf8'), ctx, { filename: f });
  return code => vm.runInContext(code, ctx);
}
module.exports = { loadConsole };
