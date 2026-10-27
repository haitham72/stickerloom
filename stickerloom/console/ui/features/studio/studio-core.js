/* Generate: create {subject} -> create more -> add 1-n.
   A session is the batches (sheets) the user has created for one request. Each batch is a real generation; "Create more" makes the next one on purpose;
   Animate and Add act on every included batch. The review gates are still decided and recorded by the server behind those clicks:
   Generate approves the plan, the x on a tile is a human reject, Add approves what was kept and the pack. */
'use strict';

let GSTALE = false,
  GAI = { configured: false };

const GM = new Map();
// generation id -> its latest state
let SES = { prompt: '', gens: [], off: [], pack: '' },
  bg = 'checker',
  glast = '',
  MD = null,
  VG = null,
  GINP = [],
  GHEALTH = null,
  GPATHS = null;   // UX-06: {input,out,default} from /api/generations; `default` says whether this IS the real out/, never guessed from a port

const GS = { outline: 0, tile: 220, tab: 'stickers' };

let GD = null,
  GDWORK = false,
  GDP = null;
// GD: the browser-only pre-batch plan (never a G### nor a member of SES/GM); GDP: the Higgsfield sheet price last read for it {key,c}
try {
  const d = JSON.parse(localStorage.getItem('stickerloom.prompt-draft') || 'null');
  if (d && d.number === 'draft' && Array.isArray(d.stickers)) GD = d;
} catch (e) {}

const ANIM = new Set();
// batches the user pressed Animate on, until the server reports them animating
try {
  const o = localStorage.getItem('stickerloom.outline');
  if (o !== null && !isNaN(+o)) GS.outline = [0, 4, 8, 12, 16].includes(+o) ? +o : +o > 0 ? 12 : 0;
  const t = +localStorage.getItem('stickerloom.tile');
  if (t >= 130 && t <= 420) GS.tile = t;
  const s = JSON.parse(localStorage.getItem('stickerloom.session') || 'null');
  if (s && Array.isArray(s.gens))
    SES = {
      prompt: s.prompt || '',
      gens: s.gens.filter((g) => Number.isInteger(g) && g > 0),
      off: s.off || [],
      pack: s.pack || '',
      ...(s.run ? { run: s.run } : {}),
    };
} catch (e) {}
// a saved [null] / NaN (the old hopen clash) must not fire a 400 at every load
const gstore = (k, v) => {
  try {
    localStorage.setItem(k, v);
  } catch (e) {}
};

try {
  const v = localStorage.getItem('stickerloom.view');
  if (['stickers', 'sheet', 'both'].includes(v)) GS.view = v;
} catch (e) {}

try {
  GS.screen = localStorage.getItem('stickerloom.screen') === '1';
} catch (e) {}

const saveSes = () => gstore('stickerloom.session', JSON.stringify(SES));

const wait = (ms) => new Promise((f) => setTimeout(f, ms));

const BGS = [
  ['checker', 'Transparent'],
  ['light', 'Light'],
  ['dark', 'Dark'],
  ['wall', 'Wallpaper'],
];

const titleCase = (s) => s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

/* a step that has to wait for a running job waits quietly; it never scolds the user with "busy" */
async function postWait(u, b, label, h) {
  let r = await post(u, b, h);
  for (let n = 0; n < 180 && !r.ok && r.status === 409 && /busy/i.test(r.j.error || ''); n++) {
    say(label || 'Waiting for the previous step to finish…');
    await wait(1000);
    r = await post(u, b, h);
  }
  say('');
  return r;
}

/* ---------- live preview: the paired 3x3 video is played in the browser and keyed like the stickers, so Animate shows motion at once
   while the real 512x512 WEBMs encode on the server (one hidden video per batch) */
const PVS = new Map(),
  PVON = new Set();

function pvEnsure(g) {
  let p = PVS.get(g.number);
  if (p) return p;
  const v = document.createElement('video');
  v.muted = true;
  v.loop = true;
  v.playsInline = true;
  v.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none';
  v.src = `/src/${g.number}/video`;
  document.body.appendChild(v);
  v.play().catch(() => {});
  p = { v, cal: {}, t: -1 };
  PVS.set(g.number, p);
  return p;
}

function vcell(g, v, i) {
  const G = g.source.grid,
    sw = g.source.sheet_size;
  if (G && sw) {
    const r = G.rects[i - 1],
      kx = v.videoWidth / sw[0],
      ky = v.videoHeight / sw[1];
    return [r[0] * kx, r[1] * ky, r[2] * kx, r[3] * ky];
  }
  const n = g.grid ? g.grid[1] : 3,
    w = v.videoWidth / n,
    h = v.videoHeight / n;
  return [((i - 1) % n) * w, (((i - 1) / n) | 0) * h, w, h];
}

function pvCalibrate(g, v, i) {
  const [X, Y, cw, ch] = vcell(g, v, i),
    S = 96,
    c = document.createElement('canvas');
  c.width = c.height = S;
  const x = c.getContext('2d', { willReadFrequently: true });
  x.drawImage(v, X, Y, cw, ch, 0, 0, S, S);
  const d = x.getImageData(0, 0, S, S).data,
    D = [];
  for (let y = 0; y < S; y++)
    for (let xx = 0; xx < S; xx++) {
      if (y > 2 && y < S - 3 && xx > 2 && xx < S - 3) continue;
      const o = (y * S + xx) * 4,
        r = d[o],
        gg = d[o + 1],
        b = d[o + 2];
      D.push(gg - Math.max(r, b));
    }
  D.sort((a, b) => a - b);
  return Math.max(0.5 * D[D.length >> 1], 8);
}

function pvDraw(cv, g, p, i) {
  const v = p.v;
  if (!v || v.readyState < 2 || !v.videoWidth) return;
  const t = g.stickers[i - 1],
    m = t.metrics || {},
    c = m.cell,
    b = m.bbox,
    S = cv.width,
    [X, Y, cw, ch] = vcell(g, v, i);
  let fx = 0.5,
    fy = 0.5,
    fs = 1;
  if (c && b && m.scale) {
    fx = (b[0] + b[2]) / 2 / c[2];
    fy = (b[1] + b[3]) / 2 / c[3];
    fs = Math.min(3, 512 / m.scale / c[2]);
  }
  const side = (fs * (cw + ch)) / 2,
    cx = X + fx * cw,
    cy = Y + fy * ch;
  let sx = cx - side / 2,
    sy = cy - side / 2,
    sw = side,
    sh = side;
  const x0 = Math.max(sx, X),
    y0 = Math.max(sy, Y),
    x1 = Math.min(sx + sw, X + cw),
    y1 = Math.min(sy + sh, Y + ch),
    k = S / side;
  const ctx = cv.getContext('2d', { willReadFrequently: true });
  ctx.clearRect(0, 0, S, S);
  if (x1 <= x0 || y1 <= y0) return;
  ctx.drawImage(v, x0, y0, x1 - x0, y1 - y0, (x0 - sx) * k, (y0 - sy) * k, (x1 - x0) * k, (y1 - y0) * k);
  if (p.cal[i] === undefined) p.cal[i] = pvCalibrate(g, v, i);
  const T = p.cal[i],
    im = ctx.getImageData(0, 0, S, S),
    d = im.data;
  for (let o = 0; o < d.length; o += 4) {
    if (!d[o + 3]) continue;
    const r = d[o],
      gg = d[o + 1],
      bb = d[o + 2],
      mx = Math.max(r, bb);
    let a = (2 * (T - (gg - mx))) / T;
    a = a < 0 ? 0 : a > 1 ? 1 : a;
    if (a < 0.98 && a > 0 && gg > mx) d[o + 1] = mx;
    d[o + 3] = d[o + 3] * a;
  }
  ctx.putImageData(im, 0, 0);
  cv.dataset.d = 1;
}

let PVT = 0;

function pvLoop(now) {
  requestAnimationFrame(pvLoop);
  if (now - PVT < 80) return;
  PVT = now;
  document.querySelectorAll('canvas[data-pv]').forEach((cv) => {
    const g = GM.get(+cv.dataset.g),
      p = PVS.get(+cv.dataset.g);
    if (!g || !p) return;
    const fresh = p.v.currentTime !== p.t;
    if (fresh || !cv.dataset.d) pvDraw(cv, g, p, +cv.dataset.pv);
  });
  PVS.forEach((p) => (p.t = p.v.currentTime));
}

requestAnimationFrame(pvLoop);

/* ---------- state helpers */
const making = (g) => ['requested', 'sheet_picked', 'keyed'].includes(g.stage) && !g.error;

const animPhase = (g) => g.stickers.some((t) => ['READY', 'FAILED'].includes(t.anim_status));

const processing = (g) => g.stickers.some((t) => t.anim_status === 'PROCESSING');

const isOff = (g, t) => (animPhase(g) && t.anim_status === 'READY' ? t.review.anim === 'REJECTED' : t.review.still === 'REJECTED');

const keptStills = (g) => g.stickers.filter((t) => t.status === 'READY' && t.review.still !== 'REJECTED');

const isOob = (t) => t.anim_status === 'READY' && t.review.anim === 'BLOCKED';
// made, but the character leaves its cell: blocked for review, kept to look at
const oobNote = (t) => {
  const c = (t.anim_report || []).find((r) => r.name === 'inside_frame');
  return c ? c.detail : 'leaves its cell';
};

/* A batch is a family of generations (gen 1 … n); ONE is the main. SES.gens is what is IN VIEW per batch; FAM holds each batch's family (loaded once per batch, again after Regenerate / Make main / Delete),
   Animate acts on each batch's MAIN generation; Add to a pack uses the generation actually in view. */
const FAM = new Map();

const famSet = (f) => {
  for (const m of f.members || []) FAM.set(m.id, f);
  return f;
};

async function famLoad(id) {
  const r = await api(`/api/generations/${id}/family`);
  return r.ok ? famSet(r.j) : null;
}

const mainNum = (g) => {
  const f = FAM.get(g.number);
  return f ? +String(f.picked).replace(/\D/g, '') : g.number;
};

const included = (useMain = true) =>
  sessionGens()
    .filter((g) => !SES.off.includes(g.number))
    .map((g) => useMain ? GM.get(mainNum(g)) || g : g);

const nAdded = (g, pid) =>
  keptOf(g).filter((t) => ((g.added || {})[pid] || []).includes(`${animPhase(g) ? 'animated' : 'static'}:${t.index}`)).length;

/* ---------- the screen */
RENDER.generate = async () => {
  /* the composer already lives in the creation panel (composer.js studioCol): the workspace is drawn without the old prompt row, so #prompt exists once */
  const panel = !!(document.getElementById('cpwrap') || (typeof CPKEEP !== 'undefined' && CPKEEP));
  $('s-generate').innerHTML = `<div class=gen2>
   <div class=gtop><button class="btn gtop-create" data-act=c2tog aria-controls=col2 title="New request and its settings">${ic('panel')} Creation settings</button>
    <button class=btn data-act=bdopen aria-controls=bdrawer aria-expanded=false title="Your earlier batches and projects: open one to carry on">${ic('hist')} Batches</button></div>
   ${panel ? '' : `<div class=sh>${ic('gen')} Studio</div>
   <div class=gform>${ic('search')}<input id=prompt type=text placeholder="Describe the stickers, for example: teddy bear for school" autocomplete=off><button id=go class="btn pri gbig" data-act=gprompt>Generate prompt</button></div>
   <div class=gopts><span class=mut>White outline</span><div class=tabs id=opills></div><span class=mut id=ohint></span></div>`}
   <div id=msg class=gmsg></div><div id=ghealth></div><div id=gres></div></div>`;
  if (!panel) {
    $('prompt').value = gdOn() ? GD.prompt : SES.prompt || '';
    $('prompt').onkeydown = (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        ACT.ggo();
      }
    };
  }
  document.documentElement.style.setProperty('--tile', GS.tile + 'px');
  if (!panel) drawOutline();
  glast = '';
  await loadInputs();
  tick(true);
};

function drawOutline() {
  if (!$('opills')) return;
  const on = GS.outline > 0;
  $('opills').innerHTML = [
    [12, 'On'],
    [0, 'Off'],
  ]
    .map(([px, l]) => `<button class="tab ${(on ? 12 : 0) === px ? 'on' : ''}" data-act=goutline data-px=${px}>${l}</button>`)
    .join('');
  $('ohint').textContent = on ? 'white border around each sticker, also on the animation' : 'stickers are cut out with no border';
}

ACT.goutline = (el) => {
  GS.outline = +el.dataset.px;
  gstore('stickerloom.outline', GS.outline);
  drawOutline();
  glast = '';
  tick(true);
};

/* /api/ai: which engine the AI enhancer would use and what is available (never the key); asked again after the person changes the engine (AIENG, agent.js) */
async function aiRefresh() {
  const a = await api('/api/ai');
  if (a.ok) {
    GAI = a.j;
    if (typeof cpDrawBar === 'function') cpDrawBar();
  }
  return GAI;
}
// the chip and the engine control read GAI: redraw when it arrives (the composer can be drawn first)
async function loadInputs() {
  const r = await api('/api/inputs');
  if (r.ok) GINP = r.j.inputs;
  await aiRefresh();
  const h = await api('/api/generations');
  if (h.ok) {
    GHEALTH = h.j.health;
    if (h.j.paths) { GPATHS = h.j.paths; if (typeof drawRail === 'function') drawRail(); }
  }
}

/* a new request starts a session with Batch 1; "Create more" adds the next batch */
async function create(prompt, variant, more) {
  say('');
  const body = { prompt, variant: variant || undefined, outline: GS.outline };
  const r = await postWait('/api/generations', body, 'Finishing the previous sheet…');
  if (!r.ok) {
    if (r.status === 404 && typeof liveOffer === 'function' && liveOffer(prompt)) {
      say('');
      return false;
    }
    say(
      `${esc(r.j.error || 'Could not start')} ${r.status === 404 ? `<button class="btn sm" data-act=ghiggs>Get the Higgsfield prompt for this</button>` : ''}`,
    );
    return false;
  }
  gdHide();
  if (more) {
    SES.gens.push(r.j.id);
  } else {
    SES = { prompt, gens: [r.j.id], off: [], pack: '' };
    for (const p of PVS.values()) p.v.remove();
    PVS.clear();
    PVON.clear();
    ANIM.clear();
  }
  saveSes();
  glast = '';
  MD = null;
  tick(true);
  return true;
}

ACT.ggo = () => {
  if (typeof ptOn === 'function' && ptOn()) return spStudio();
  const p = $('prompt').value.trim();
  if (!p) {
    say('Write what you want first, for example <b>teddy bear for school</b>.');
    return;
  }
  create(p, 0, false);
};

function openGen(id) {
  gdHide();
  SES = { prompt: '', gens: [id], off: [], pack: '' };
  saveSes();
  glast = '';
  MD = null;
  tick(true);
}

ACT.gmore = async () => {
  const gs = sessionGens();
  if (!gs.length) return;
  const s = gs[0].source.subject,
    used = new Set(gs.map((g) => String(g.source.subject_id)));
  await loadInputs();
  const inp = GINP.find((x) => x.subject === s),
    next = inp && inp.variants.find((v) => !used.has(String(v.folder)));
  if (!next) {
    toast(
      `That is every prepared sheet of “${s.replace(/_/g, ' ')}”. Put another in Images_gen, or get the Higgsfield prompt to make a new one.`,
      1,
    );
    return;
  }
  create(SES.prompt || s.replace(/_/g, ' '), next.variant, true);
};

/* Next batch: POST /api/plan/next with the batches on screen answers the next UNCLAIMED batch (a preset pack's next
   grid, else the next bank actions none of them drew) as a prompt draft: the request box changes to it, the Prompt step shows it, nothing is spent until
   Generate sheet. The finished sheet joins this session as the next batch. Without Higgsfield it is the next prepared sheet of the subject, as before. */
ACT.gnext = async () => {
  const gs = sessionGens();
  if (!gs.length) return;
  if (typeof liveReadyNow !== 'function' || !liveReadyNow()) return ACT.gmore();
  const style = typeof LIVE !== 'undefined' ? LIVE.style : 'flat_vector',
    loop = !!(typeof LIVE !== 'undefined' && LIVE.loop);
  const r = await post('/api/plan/next', { gens: SES.gens, style_id: style, loop });
  if (!r.ok) return toast(r.j.error || 'Could not plan the next batch', 1);
  if (r.j.complete) {
    say(esc(r.j.message));
    return;
  }
  delete PD['draft:sheet'];
  delete PD['draft:video'];
  $('prompt').value = r.j.prompt;
  const what = r.j.next && r.j.next.kind === 'preset' ? `the ${r.j.next.preset} grid` : 'the next actions of the bank';
  GD = {
    ...r.j,
    number: 'draft',
    plan_source: `Next batch: ${what}`,
    video_sheets: [],
    reviews: {},
    active: true,
    tab: 'plan',
    gens: SES.gens.join(),
    style,
    loop,
    refs: [],
    next: true,
  };
  say('');
  gdSave();
  glast = '';
  await tick(true);
  gdPrice();
};

ACT.gbdrop = (el) => {
  const id = +el.dataset.g;
  SES.gens = SES.gens.filter((x) => x !== id);
  SES.off = SES.off.filter((x) => x !== id);
  saveSes();
  glast = '';
  tick(true);
};

ACT.ginc = (el) => {
  const id = +el.dataset.g;
  SES.off = el.checked ? SES.off.filter((x) => x !== id) : SES.off.concat(id);
  saveSes();
  glast = '';
  tick(true);
};

document.addEventListener('change', (e) => {
  if (e.target.classList && e.target.classList.contains('ginc')) ACT.ginc(e.target);
  if (e.target.id === 'gbgsel') {
    bg = e.target.value;
    glast = '';
    tick(true);
    if (MD) gmodal();
  }
});

document.addEventListener('input', (e) => {
  if (e.target.id === 'gsize') {
    GS.tile = +e.target.value;
    document.documentElement.style.setProperty('--tile', GS.tile + 'px');
    gstore('stickerloom.tile', GS.tile);
  }
});

/* the generations row of a batch: gen 1 … n (★ = the main), click = view it; the one in view carries Make main / Delete / Report */
function gensHtml(g) {
  const f = FAM.get(g.number),
    ms = f && f.members && f.members.length ? f.members : [{ id: g.number, generation_id: g.generation_id, n: 1 }],
    main = f ? f.picked : g.generation_id;
  return `<span class=ggens>${ms.map((m) => `<button class="ggen${m.id === g.number ? ' on' : ''}${m.generation_id === main ? ' main' : ''}" data-act=ggen data-g=${g.number} data-to=${m.id} aria-pressed=${m.id === g.number} title="${esc(m.generation_id)}${m.generation_id === main ? ' · the main generation' : ' · a backup'}${m.sheet_model ? ' · ' + esc(m.sheet_model) : ''}">${m.generation_id === main ? '★ ' : ''}gen ${m.n}</button>`).join('')}</span>
   <details class="gh-more ggact"><summary class="btn sm" aria-label="Generation options">⋯</summary><div class=gh-pop role=menu>${g.generation_id === main ? `<span class=keychip title="Animate and Add to pack use this generation">Main</span>` : `<button class="btn sm" data-act=gmain data-g=${g.number} title="Make this the batch's main generation; the others stay as backups">Make main</button>`}
    <button class="btn sm" data-act=gdel data-g=${g.number} title="Move this generation to the trash (restorable)">Delete</button></div></details>`;
}

ACT.ggen = (el) => {
  const from = +el.dataset.g,
    to = +el.dataset.to;
  if (from === to) return;
  SES.gens = SES.gens.map((x) => (x === from ? to : x));
  SES.off = SES.off.map((x) => (x === from ? to : x));
  saveSes();
  glast = '';
  tick(true);
};

ACT.gmain = async (el) => {
  const n = +el.dataset.g,
    r = await post(`/api/generations/${n}/pick`, {});
  if (!r.ok) return toast(r.j.error || 'Could not make it the main generation', 1);
  famSet(r.j);
  const m = r.j.members.find((x) => x.id === n);
  toast(`gen ${m ? m.n : ''} is the main generation now; the others stay as backups`);
  glast = '';
  tick(true);
};

ACT.gdel = (el) => {
  const n = +el.dataset.g,
    g = GM.get(n),
    f = FAM.get(n),
    m = f && f.members.find((x) => x.id === n);
  if (!g) return;
  confirmDlg(
    `Delete gen ${m ? m.n : 1} (${g.generation_id})? It moves to the trash and you can restore it from “Removed batches”. Stickers already in a pack stay there.`,
    async () => {
      const r = await post(`/api/generations/${n}/remove`, {});
      if (!r.ok) return toast(r.j.error, 1);
      GM.delete(n);
      FAM.delete(n);
      const left = (r.j.batch && r.j.batch.members) || [];
      if (!left.length) {
        SES.gens = SES.gens.filter((x) => x !== n);
        SES.off = SES.off.filter((x) => x !== n);
      } else {
        const nf = await famLoad(+String(left[0]).replace(/\D/g, ''));
        const to = nf ? +String(nf.picked).replace(/\D/g, '') : +String(left[0]).replace(/\D/g, '');
        SES.gens = SES.gens.map((x) => (x === n ? to : x));
      }
      saveSes();
      histLoad(false);
      glast = '';
      tick(true);
    },
    'Delete',
  );
};

function batchHtml(g, k, total, mode) {
  const inc = !SES.off.includes(g.number),
    s = g.source,
    staff = true;
  const head = `<header class=gbhead><div class=gbidentity>${total > 1 ? `<label class=gbinc title="Include this batch when you Animate or Add"><input type=checkbox class=ginc data-g=${g.number} ${inc ? 'checked' : ''}> <b>Batch ${k + 1}</b></label>` : `<b>Batch ${k + 1}</b>`}
    ${gensHtml(g)}
    ${g.key_colour === 'blue' ? `<span class=keychip title="This sheet has a blue screen, so it was keyed as blue (and the video sheet is blue too). Nothing to do.">Blue key</span>` : ''}
    ${s.prepared && staff ? `<span class=keychip title="Served from a prepared sheet in the watch folder: 0 credits, no provider call.">Prepared</span>` : ''}
    </div><div class=gbact>${s.has_video || making(g) || (typeof liveReadyNow === 'function' && liveReadyNow()) ? '' : `<button class="btn sm" data-act=gvideo data-g=${g.number} ${keptStills(g).length ? '' : 'disabled'} title="This sheet has no prepared video: make one from the sheet in your own tool">${ic('film')} Make a video…</button>`}
    ${typeof impBatchBtn === 'function' ? impBatchBtn(g, mode) : ''}
    <details class="gh-more batch-options"><summary class="btn sm" aria-label="Batch options">⋯</summary><div class=gh-pop role=menu><button class="btn sm" data-act=gopenfolder data-g=${g.number} title="Open this batch's folder in the file manager: a properly named folder with the sheet, stickers, animations and prompts">${ic('folder')} Open folder</button>
    ${total > 1 ? `<button class="btn sm" data-act=gbdrop data-g=${g.number} title="Take this batch out of the session">${ic('x')}</button>` : ''}</div></details></div>${mode === 'anim' ? `<div class=gbmotion><span class=mut>Motion</span>${motionRow(g)}</div>` : ''}${allowAllRow(g, mode === 'anim' ? 'animation' : 'still')}</header>`;
  if (making(g))
    return `<section class=gbatch>${head}<div class=gwork><div class=spin></div><b>Making your stickers…</b><div class=mut>${g.stage === 'requested' ? 'Reading the sheet' : g.stage === 'sheet_picked' ? 'Removing the background' : 'Cutting and checking each sticker'}</div></div></section>`;
  const sheetErr = g.stickers.every((t) => t.status === 'FAILED') && (g.verify.sheet || []).find((c) => !c.ok && c.severity !== 'WARN');
  if (sheetErr) {
    const p =
      g.problem; /* the server explains a blocked sheet in words (flow/explain.py): what happened, what was paid, what to do; Python's block stays final */
    return `<section class=gbatch>${head}<div class=gwork><b>${esc(p ? p.title : 'This sheet cannot be used')}</b><div class=mut>${esc(p ? p.why : sheetErr.detail || sheetErr.name)}</div>
     ${
       p
         ? `<div class=mut>${esc(p.fix)}</div>${p.received ? `<div class=mut>The sheet was received${p.received.job ? ' (' + esc(p.received.job) + ')' : ''}${p.received.cost ? ' and paid for (' + p.received.cost + ' credits)' : ''}: nothing is lost, this batch just has no stickers. It stays in History.</div>` : ''}
     ${p.cut_anyway ? `<button class="btn pri" data-act=gcutany data-g=${g.number} title="Cut the sheet that was received, as it is, for free: you decide on every cell yourself">Cut it anyway</button>` : ''}
     <button class="btn${p.cut_anyway ? '' : ' pri'}" data-act=gretrysheet data-g=${g.number}>Try the sheet again</button>`
         : ''
     }</div></section>`;
  }
  return `<section class="gbatch ${inc ? '' : 'excl'}">${head}${g.error ? `<div class=warn>${esc(g.error)}</div>` : ''}<div class="gbody gv-${GS.view || 'both'}">${(GS.view || 'both') === 'stickers' ? '' : sheetPanel(g, mode === 'anim' ? 'anim' : 'still')}${GS.view === 'sheet' ? '' : `<div class=gtiles>${g.stickers.map((t) => tileHtml(g, t, mode)).join('')}</div>`}</div></section>`;
}

/* what a set of batches stands at right now: the counts the header and the bottom bar read. One implementation, used by the header and the bottom bar. */
function gstats(gs) {
  const inc = gs.filter((g) => !SES.off.includes(g.number)),
    n = inc.reduce((a, g) => a + keptOf(g).length, 0),
    ready = gs.every((g) => !making(g));
  const todoAnim = inc.filter(
    (g) =>
      g.source.has_video &&
      (!animPhase(g) || g.stickers.some((t) => t.anim_status === 'STALE')) &&
      !processing(g) &&
      !ANIM.has(g.number) &&
      keptStills(g).length,
  );
  const busyAnim = gs.some((g) => processing(g) || (ANIM.has(g.number) && !animPhase(g)));
  const done = inc.reduce((a, g) => a + g.stickers.filter((t) => ['READY', 'FAILED'].includes(t.anim_status)).length, 0),
    tot = inc.reduce((a, g) => a + (hasVid(g) ? keptStills(g).length : 0), 0);
  const pk = SES.pack && packById(SES.pack),
    added = pk ? inc.reduce((a, g) => a + nAdded(g, SES.pack), 0) : 0;
  return {
    gs,
    inc,
    n,
    ready,
    busyAnim,
    todoAnim,
    done,
    tot,
    pk,
    added,
    allAdded: !!(pk && n > 0 && added >= n),
    kind: inc.some((g) => animPhase(g)) ? 'animated ' : '',
  };
}

/* the bottom bar (Animate / Add to a pack) */
function barHtml(s) {
  const { n, ready, busyAnim, todoAnim, done, tot, pk, allAdded, kind, inc } = s;
  if (!ready) return `<span class=gstat>Making your stickers…</span>`;
  if (busyAnim) return `<span class=gstat>Animating ${done} of ${tot}…</span><button class="btn pri gbig" disabled>Animating…</button>`;
  if (todoAnim.length)
    return `<span class=gstat>${n} sticker${n === 1 ? '' : 's'} in ${inc.length} batch${inc.length === 1 ? '' : 'es'}</span><button class=link data-act=gadd ${n ? '' : 'disabled'}>or add the stills</button><button class="btn pri gbig" data-act=ganimate>${ic('play')} Animate${inc.length > 1 ? ` ${todoAnim.length} batch${todoAnim.length === 1 ? '' : 'es'}` : ''}</button>`;
  return `<span class=gstat>${n} ${kind}sticker${n === 1 ? '' : 's'} in ${inc.length} batch${inc.length === 1 ? '' : 'es'}${allAdded ? ` · added to “${esc(pk.name)}”` : ''}</span><button class="btn pri gbig" data-act=gadd ${n && !allAdded ? '' : 'disabled'}>${allAdded ? 'Added ✓' : `Add ${n} to a pack`}</button>${allAdded ? `<button class="btn gbig" data-act=gopenpack>Open pack</button><button class="btn gbig" data-act=gpackzip title="Download this pack's files as one .zip (PNG stills, WebM animations, manifest.json)">Export pack</button>` : ''}`;
}

function gview() {
  if (gdOn()) return gdView();
  const gs = sessionGens();
  if (!gs.length) return '';
  const s = gstats(gs),
    { pk, allAdded, n } = s;
  const vw = GS.view || 'both',
    seg = (v, l, tip) => `<button type=button data-act=gvmode data-v=${v} aria-pressed=${vw === v} title="${tip}">${l}</button>`;
  /* the toolbar (redesign phase 5): the title, then the view (Stickers · Sheet · Both), Background, Size and ⋯ with the batch's other actions, as they were */
  return `<div class=ghead><div class=gh-title><h2>${esc(titleCase(gs[0].source.subject))}</h2><small class=mut>${gs.length} batch${gs.length === 1 ? '' : 'es'} · ${gs[0].outline_px ? gs[0].outline_px + ' px outline' : 'no outline'}${gs[0].source.prepared ? ' · prepared sheet, 0 credits' : ''}</small></div>
    <div class=gh-tools><div class=gseg role=group aria-label="What to show">${seg('stickers', 'Stickers', 'Only the sticker tiles')}${seg('sheet', 'Sheet', 'Only the whole sheet, with its cut lines and checks')}${seg('both', 'Both', 'The sheet beside the tiles')}</div>
    <button type=button class="btn sm gh-screen" data-act=gscreen aria-pressed=${!!GS.screen} title="${GS.screen ? 'Showing each sticker on its green / blue screen, cut from the raw sheet. Click to show the cut-out stickers.' : 'Show each sticker on its green / blue screen, as it was drawn on the raw sheet'}">${GS.screen ? 'Screen on' : 'Screen off'}</button>
    <label class=gh-bg title="Background behind the stickers"><span class=mut>Background</span><select id=gbgsel>${BGS.map(([k, l]) => `<option value=${k} ${bg === k ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
    <label class=gh-size title="Tile size">${ic('lib')}<input type=range id=gsize min=130 max=420 step=10 value=${GS.tile} aria-label="Tile size"></label>
    <button class="btn sm" data-act=gnext ${s.ready && !s.busyAnim ? '' : 'disabled'} title="The next batch of this request that has not been made yet, as a prompt you read before anything is spent">${ic('plus')} Next batch</button>
    <details class=gh-more><summary class="btn sm" aria-label="More for this batch" title="More for this batch">⋯</summary><div class=gh-pop role=menu>
    ${gs.length === 1 && gs[0].source.prepared && typeof liveReadyNow === 'function' && liveReadyNow() ? `<button class="btn" data-act=gnewlive title="Draw a fresh sheet with Higgsfield at the normal price instead of the prepared one">Make a new one</button>` : ''}
    ${gs.length === 1 ? `<button class=btn data-act=ggroup title="Put this batch in the group of another batch: that batch becomes its parent">Move to another batch…</button>` : ''}
    ${gs.length === 1 && s.ready ? `<button class=btn data-act=gzipdl data-g=${gs[0].number} data-l=1 title="Download the accepted stickers (animated where ready) as one .zip, compressed small (animated: every frame kept, the best quality down to crf 46 that fits ~64 KB each; crf and size per file are in manifest.json), plus a grey silhouette PNG beside each animated one ({its name}_grey.png)">${ic('download')} Download .zip</button><button class=link data-act=gzipdl data-g=${gs[0].number} data-l=0 title="The same zip with the full-size animation files">full quality</button>` : ''}${gs.length === 1 && s.ready && typeof colBtn === 'function' ? colBtn('generation', gs[0].generation_id, s.n, titleCase(gs[0].source.subject)) : ''}
    <button class="btn dng" data-act=grm title="Move ${gs.length === 1 ? 'this batch' : 'these batches'} to the trash. You can restore ${gs.length === 1 ? 'it' : 'them'} from Removed batches under Projects.">${ic('trash')} Remove batch</button></div></details></div></div>
   ${stepsHtml(s)}
   ${gbodyHtml(s.gs, { inc: s.inc, todoAnim: s.todoAnim, busyAnim: s.busyAnim, n: s.n })}
   <div class=gbar>${barHtml(s)}</div>`;
}

/* what the page shows under the header: each step is a real view. */
const GSTUDIO = new Map();
function gExtraBody(tab, gs, c) {
  const section = GSTUDIO.get(tab);
  return section && section.active(gs) ? section.body(gs, c) : null;
}
function gExtraSteps(steps, c) {
  for (const [tab, section] of GSTUDIO) {
    if (!c.gs.length || !section.active(c.gs)) continue;
    const at = steps.findIndex(step => step[3] === section.before);
    if (at >= 0) steps.splice(at, 0, [...section.step(c), tab]);
  }
  return steps;
}
function gbodyHtml(gs, c) {
  const t = GS.tab;
  const extra = gExtraBody(t, gs, c);
  if (extra !== null) return extra;
  if (t === 'request') return requestView(gs);
  if (t === 'plan') return planView(gs[0]);
  if (t === 'anim' && !gs.some(animPhase) && !c.busyAnim && !gs.some((g) => PVON.has(g.number)))
    return gs.some((g) => g.source.has_video) ? animEmpty(gs, c) : animCreate(gs);
  return gs.map((g, k) => batchHtml(g, k, gs.length, t === 'anim' ? 'anim' : 'still')).join('');
}

function animEmpty(gs, c) {
  const hasVid = gs.some((g) => g.source.has_video); // a prepared video; a Kling video has already produced the animations
  return `<section class=gplan style="text-align:center;padding:44px 16px"><h2 style="margin:0 0 6px">${hasVid ? 'Animate your stickers' : 'No video prepared for this sheet'}</h2>
   <div class=mut style="max-width:560px;margin:0 auto 18px">${
     hasVid
       ? `${c.n} sticker${c.n === 1 ? '' : 's'} will be animated from the prepared video. Each animation is checked frame by frame (size, loop, and whether the character stays inside its cell) and shows up here as soon as it is ready.`
       : typeof liveReadyNow === 'function' && liveReadyNow()
         ? 'Generate the animation on the Stickers view: the box under the green screen has the model and the price.'
         : 'Make a video from the sheet in your own tool, then add it with “Make a video…” on the Stickers view.'
   }</div>
   ${hasVid ? `<button class="btn pri gbig" data-act=ganimate ${c.todoAnim.length ? '' : 'disabled'}>${ic('play')} Animate</button>` : ''}</section>`;
}

/* Request: the original request, editable; generating again starts a new session */
function requestView(gs) {
  const g = gs[0],
    s = g.source,
    on = GS.outline > 0;
  return `<section class=gplan><div class=pcols><div><div class=pbh><b>Your request</b></div>
    <textarea id=greq data-req rows=3 style="width:100%;font:inherit;font-size:15px;background:var(--fill);border:0;border-radius:12px;padding:12px">${esc(SES.prompt || g.prompt || '')}</textarea>
    <div class=row><span class=mut>White outline</span><div class=tabs style="margin:0;gap:6px">${[
      [12, 'On'],
      [0, 'Off'],
    ]
      .map(
        ([px, l]) =>
          `<button class="tab ${(on ? 12 : 0) === px ? 'on' : ''}" data-act=goutline data-px=${px} style="padding:6px 16px;font-size:13px">${l}</button>`,
      )
      .join('')}</div></div>
    <div class=row style="margin-top:12px"><button class="btn pri gbig" data-act=greqgo>Generate again with this request</button></div>
    <div class=mut style="margin-top:6px">This starts a new session. The stickers you have now stay in History.</div></div>
   <ul class=pcells><li><b>Subject</b><div class=mut>${esc(titleCase(s.subject))}</div></li><li><b>Prepared sheets used</b><div class=mut>${gs.map((x) => `sheet ${x.source.subject_id} (${x.generation_id})`).join(', ')}</div></li>
    <li><b>Grid</b><div class=mut>${g.grid[0]}×${g.grid[1]}, ${g.stickers.length} stickers per batch</div></li><li><b>Template</b><div class=mut>${esc(g.template_id || 'hand-written plan')}${g.template_version ? ' v' + g.template_version : ''}</div></li>
    <li><b>Edge</b><div class=mut>${g.outline_px ? g.outline_px + ' px white outline' : 'no outline'}${g.erode_px ? `, ${g.erode_px} px trimmed` : ''}</div></li></ul></div></section>`;
}

/* "Cut it anyway": free, from the sheet already received (pipeline.recut); every cell is judged on its own and you decide at G2 */
ACT.gcutany = async (el) => {
  const r = await post(`/api/generations/${el.dataset.g}/recut`);
  if (!r.ok) return toast(r.j.error || 'Could not cut the sheet', 1);
  toast('Cutting the sheet…');
  glast = '';
  tick(true);
};

ACT.gretrysheet = (el) => {
  const g = GM.get(+el.dataset.g);
  if (!g) return;
  $('prompt').value = g.prompt || '';
  GS.tab = 'stickers';
  create(g.prompt || '', 0, false);
};
/* the same path as Generate: the live price is shown before anything is sent */
ACT.greqgo = (el) => {
  const p = (($('greq') || {}).value || '').trim();
  if (!p) {
    say('Write what you want first.');
    return;
  }
  $('prompt').value = p;
  GS.tab = 'stickers';
  create(p, 0, false);
};

/* ---------- the header: Request > Prompt > Stickers > Animation > Pack (where this request stands, from what the server reports).
   The current step is `GS.tab`. */
function stepsHtml(c) {
  const cur = GS.tab;
  const { gs, ready, busyAnim, done, tot, pk, allAdded, n } = c,
    g0 = gs[0],
    cnt = (f) => gs.reduce((a, g) => a + g.stickers.filter(f).length, 0);
  const good = cnt((t) => t.status === 'READY'),
    kept = cnt((t) => t.status === 'READY' && t.review.still !== 'REJECTED'),
    dropped = cnt((t) => t.review.still === 'REJECTED'),
    blocked = cnt((t) => t.status === 'FAILED'),
    oobN = cnt(isOob),
    animOk = cnt((t) => t.anim_status === 'READY' && !isOob(t) && t.review.anim !== 'REJECTED' && t.review.still !== 'REJECTED'),
    aDrop = cnt((t) => t.anim_status === 'READY' && t.review.anim === 'REJECTED' && t.review.still !== 'REJECTED'),
    animFail = cnt((t) => t.anim_status === 'FAILED');
  const anim = gs.some(animPhase),
    hasVid_ = gs.some(hasVid);
  const S = [
    ['Request', 'done', esc(SES.prompt || g0.prompt || ''), 'request'],
    [
      'Prompt',
      g0.sheet_prompt ? 'done' : 'todo',
      `${esc(g0.template_id || 'plan')}${g0.template_version ? ' v' + g0.template_version : ''} · tags`,
      'plan',
    ],
    [
      'Stickers',
      ready ? (good ? 'done' : 'warn') : 'run',
      ready ? `${kept} kept${dropped ? `, ${dropped} dropped` : ''}${blocked ? `, ${blocked} blocked` : ''}` : 'Making…',
      'stickers',
    ],
    [
      'Animation',
      busyAnim ? 'run' : anim ? (oobN || animFail ? 'warn' : 'done') : 'todo',
      busyAnim
        ? `Animating ${done} of ${tot}…`
        : anim
          ? `${animOk} ready${aDrop ? `, ${aDrop} dropped` : ''}${oobN ? `, ${oobN} out of bounds (off)` : ''}${animFail ? `, ${animFail} failed` : ''}`
          : hasVid_
            ? 'Not started'
            : 'No video prepared',
      'anim',
    ],
    ['Pack', allAdded ? 'done' : 'todo', allAdded ? `Added to “${esc(pk.name)}”` : 'Not added yet', 'pack'],
  ];
  /* the journey: Idea · Prompt · Stickers · Motion · Pack · Telegram, one slim track; each stop is the view it always was (gtab), Pack adds, Telegram opens the pack */
  const tg = pk && ((pk.telegram || {}).sets || []).map((x) => x.link).find(Boolean);
  S[0][0] = 'Idea';
  S[3][0] = 'Motion';
  S.push(['Telegram', tg ? 'done' : 'todo', tg ? 'Sent' : allAdded ? 'Send from the pack' : 'After the pack', 'telegram']);
  gExtraSteps(S, c);
  const mark = (st, i) => (st === 'done' ? ic('check') : st === 'run' ? '<span class=spin></span>' : st === 'warn' ? '!' : i + 1);
  return `<div class=gsteps role=tablist aria-label="Where this batch is">${S.map(([l, st, sub, tab], i) => `<button class="gst ${st} ${tab === cur ? 'cur' : ''}" ${tab === 'pack' ? `data-act=gadd ${ready && n ? '' : 'disabled'}` : tab === 'telegram' ? `data-act=gopenpack ${allAdded ? '' : 'disabled'}` : `data-act=gtab data-t=${tab}`}><span class=gsm>${mark(st, i)}</span><span class=gsl><b>${l}</b><small title="${sub.replace(/<[^>]+>/g, '')}">${sub}</small></span></button>`).join('')}</div>`;
}

ACT.gopenfolder = async (el) => {
  const r = await post(`/api/generations/${el.dataset.g}/reveal`);
  if (!r.ok) return toast(r.j.error || 'Could not open the folder', 1);
  toast('Opened ' + r.j.opened);
};

/* ---------- polling: only while this screen is open */
/* Motion size: one Resize box and one Size stepper on the batch row, and a frame editor
   per sticker (Size + Position on ONE frame, guide grid on/off). Every test renders one frame (GET /api/generations/{id}/anim_preview -> a PNG), so a
   click answers at once; Apply cuts the clips once, free, from the stored video. Resize on: each sticker is sized and placed by its hot spot (the pixels
   opaque in at least half the frames, so drops flying out do not count) matched to its approved still. Off: every sticker is cut from its cell at one
   scale, as on the sheet (POST /api/generations/{id}/resize). A sticker's own Size and Position: POST /api/generations/{id}/position. */
const VSTEP = 0.05,
  OSTEP = 0.01,
  pct = (f) => `${Math.round(f * 100)}%`;
const gResize = (g) => (g.resize != null ? !!g.resize : !g.same_scale);
const stScale = (g, t) => (t.anim_scale != null ? t.anim_scale : g.anim_scale || 1);
const stOff = (t) => t.anim_offset || [0, 0];
const VZ = { g: null, i: null, scale: null, resize: null, off: null, frame: 0, grid: true, src: '', meta: null, busy: false, n: 0, t: 0, drag: null };
try { VZ.grid = localStorage.getItem('vz.grid') !== '0'; } catch (e) {}
const vzOn = (g) => !!g && VZ.g === g.number;
const vzResize = (g) => (vzOn(g) && VZ.resize != null ? VZ.resize : gResize(g));

function motionRow(g) {
  if (!g || g.kind === 'particles' || making(g)) return '';
  const on = vzResize(g),
    f = vzOn(g) && VZ.i == null && VZ.scale != null ? VZ.scale : g.anim_scale || 1,
    b = `data-act=gvsize data-g=${g.number}`;
  return `<span class=vsize><label title="${on ? 'On: each sticker is sized and placed by where it spends most of the video (its hot spot), matched to its approved still. Drops or sparks that fly out do not shrink it' : 'Off: every sticker is cut from its cell at one scale, so the sizes stay as they are on the sheet'}"><input type=checkbox data-act=gresize data-g=${g.number} ${on ? 'checked' : ''}> Resize</label>
    <span class=mut title="Size of every animation in this batch (a sticker with its own size keeps it)">Size</span><button class="btn sm" ${b} data-f=${(f - VSTEP).toFixed(2)} ${f <= 0.5 ? 'disabled' : ''} aria-label="Smaller">−</button><b>${pct(f)}</b><button class="btn sm" ${b} data-f=${(f + VSTEP).toFixed(2)} ${f >= 1.5 ? 'disabled' : ''} aria-label="Larger">+</button>${f !== 1 ? `<button class="btn sm" ${b} data-f=1 title="Back to 100%">Reset</button>` : ''}</span>`;
}
/* under each animated tile: Size, X and Y sliders for that sticker.
   The readout moves on input; one POST /position goes out after the release (debounced), so a drag cuts the clip once, free.
   The frame-editor button stays: grid, frame scrubber and Centre live only there.
   Tiles stay compact: the sliders hide behind an Adjust toggle per sticker. */
const vslPend = {}, vslT = {}, vslBusy = new Set(), vslOpen = new Set();
const fmtOff = (v) => `${v > 0 ? '+' : ''}${Math.round(v * 100)}%`;
function vsizeRow(g, t) {
  const own = t.anim_scale != null || !!t.anim_offset;
  const key = `${g.number}:${t.index}`, pend = vslPend[key], open = vslOpen.has(key);
  const o = pend ? [pend.dx, pend.dy] : stOff(t), f = pend ? pend.scale : stScale(g, t);
  return `<div class=vsize><button class="btn sm" data-act=vzedit data-g=${g.number} data-i=${t.index} title="Size and position of this sticker, tried on one frame">${pct(f)}${t.anim_offset ? ' · moved' : ''}${own ? '' : ' · batch'}</button><button class="btn sm" data-act=vslopen data-g=${g.number} data-i=${t.index} aria-expanded=${open} title="Show this sticker's Size, X and Y sliders">Adjust</button>${own ? `<button class="btn sm" data-act=vslreset data-g=${g.number} data-i=${t.index} title="Back to the batch's size and position">Reset</button>` : ''}</div>`+
  (open ? `
  <div class=vsl data-g=${g.number} data-i=${t.index}>
   <label class=vsize><span class=mut>Size</span><input type=range min=50 max=150 step=5 value=${Math.round(f * 100)} data-k=scale aria-label="Size of this sticker in percent"><b>${pct(f)}</b></label>
   <label class=vsize><span class=mut>X</span><input type=range min=-50 max=50 step=1 value=${Math.round(o[0] * 100)} data-k=dx aria-label="Move this sticker left and right"><b>${fmtOff(o[0])}</b></label>
   <label class=vsize><span class=mut>Y</span><input type=range min=-50 max=50 step=1 value=${Math.round(o[1] * 100)} data-k=dy aria-label="Move this sticker up and down"><b>${fmtOff(o[1])}</b></label>
  </div>` : '');
}
ACT.vslopen = (el) => { const key = `${+el.dataset.g}:${+el.dataset.i}`; if (vslOpen.has(key)) vslOpen.delete(key); else vslOpen.add(key); glast = ''; tick(true); };

function vzTarget(g) {
  const ok = (i) => { const t = g.stickers[i - 1]; return !!(t && t.anim_status === 'READY'); };
  if (VZ.i && ok(VZ.i)) return VZ.i;
  if (typeof MD !== 'undefined' && MD && MD.g === g.number && ok(MD.i)) return MD.i;
  const t = g.stickers.find((x) => x.anim_status === 'READY');
  return t ? t.index : null;
}
function vzClear() { Object.assign(VZ, { g: null, i: null, scale: null, resize: null, off: null, frame: 0, src: '', meta: null, drag: null }); vzCard(false); }
function vzStart(g, i) { if (VZ.g !== g.number || (VZ.i || null) !== (i || null)) { vzClear(); VZ.g = g.number; VZ.i = i || null; } }
const vzOff = (g, i) => VZ.off || stOff(g.stickers[i - 1]);
const vzScale = (g, i) => (VZ.scale != null ? VZ.scale : VZ.i ? stScale(g, g.stickers[i - 1]) : g.anim_scale || 1);

/* the guide: thirds, the centre cross, the 512 safe edge, and the hot spot the cut measured (dashed) */
function vzGuide() {
  const m = VZ.meta || {}, c = m.core_canvas;
  return (VZ.grid ? '<path d="M170.7 0V512M341.3 0V512M0 170.7H512M0 341.3H512" class=g1/><path d="M256 236V276M236 256H276" class=g2/><rect x=8 y=8 width=496 height=496 class=g3/>' : '')
    + (c ? `<rect x=${c[0]} y=${c[1]} width=${Math.max(1, c[2] - c[0])} height=${Math.max(1, c[3] - c[1])} class=g4/>` : '');
}
function vzCard(show) {
  let c = document.getElementById('vzcard');
  if (!show) return TESTCARD.close('vzcard');
  const g = GM.get(VZ.g), i = g && vzTarget(g);
  if (!g || !i) return vzCard(false);
  if (!c) {
    c = TESTCARD.open({id:'vzcard',apply:'vzapply',cancel:'vzcancel',stageClass:'vzstage',focus:true,
      stageExtra:'<svg class=vzguide viewBox="0 0 512 512" aria-hidden=true></svg>',
      controls:'<div class=vzframe><input type=range min=0 value=0 aria-label="Frame"><small class=mut></small></div><div class=vzctl></div>'});
    const rg = c.querySelector('input[type=range]');
    rg.addEventListener('input', () => { VZ.frame = +rg.value; vzPreview(); });
    const st = c.querySelector('.vzstage'), img = st.querySelector('img');
    img.addEventListener('load', () => { img.style.transform = ''; });
    st.addEventListener('pointerdown', (e) => {                       /* drag the frame: Position, for one sticker only */
      if (!VZ.i) return;
      const gg = GM.get(VZ.g); if (!gg) return;
      VZ.drag = { x: e.clientX, y: e.clientY, off: vzOff(gg, VZ.i).slice(), w: st.getBoundingClientRect().width };
      st.setPointerCapture(e.pointerId);
    });
    st.addEventListener('pointermove', (e) => {
      const d = VZ.drag; if (!d) return;
      const dx = (e.clientX - d.x) / d.w, dy = (e.clientY - d.y) / d.w;
      VZ.off = [vzClamp(d.off[0] + dx), vzClamp(d.off[1] + dy)];
      img.style.transform = `translate(${(VZ.off[0] - d.off[0]) * 100}%, ${(VZ.off[1] - d.off[1]) * 100}%)`;
    });
    const end = () => { if (VZ.drag) { VZ.drag = null; vzPreview(); } };
    st.addEventListener('pointerup', end); st.addEventListener('pointercancel', end);
  }
  c.querySelector('.vzguide').innerHTML = vzGuide();
  const m = VZ.meta, rg = c.querySelector('input[type=range]');
  rg.max = m ? Math.max(0, m.frames - 1) : 0; rg.value = VZ.frame;
  c.querySelector('.vzframe small').textContent = m ? `frame ${m.frame + 1} of ${m.frames}` : '';
  const f = vzScale(g, i), o = vzOff(g, i), b = 'class="btn sm"';
  c.querySelector('.vzctl').innerHTML = (VZ.i
    ? `<span class=vsize>Size <button ${b} data-act=vzsize data-d=-1 ${f <= 0.5 ? 'disabled' : ''} aria-label="Smaller">−</button><b>${pct(f)}</b><button ${b} data-act=vzsize data-d=1 ${f >= 1.5 ? 'disabled' : ''} aria-label="Larger">+</button></span>
       <span class=vsize><button ${b} data-act=vzmove data-x=-1 aria-label="Left">←</button><button ${b} data-act=vzmove data-y=-1 aria-label="Up">↑</button><button ${b} data-act=vzmove data-y=1 aria-label="Down">↓</button><button ${b} data-act=vzmove data-x=1 aria-label="Right">→</button>
       <button ${b} data-act=vzcentre ${m && m.centre_canvas ? '' : 'disabled'} title="Put the hot spot's centre in the middle">Centre</button>${o[0] || o[1] ? `<button ${b} data-act=vzzero title="Back to where the cut puts it">Reset</button>` : ''}</span>`
    : '') + `<label class=vsize><input type=checkbox data-act=vzgrid ${VZ.grid ? 'checked' : ''}> Grid</label>`;
  const mode = m ? { hot: 'hot spot', same: 'one scale', kept: 'approved still', fit: 'fit motion' }[m.mode] || m.mode : '';
  const caption = `S${i} · ${pct(f)}${o[0] || o[1] ? ` · moved ${Math.round(o[0] * 100)}%, ${Math.round(o[1] * 100)}%` : ''}${mode ? ' · ' + mode : ''} · Apply uses it on ${VZ.i ? 'S' + VZ.i : 'the batch'}`;
  TESTCARD.sync(c,{src:VZ.src,caption,busy:VZ.busy,loading:!VZ.src});
  const anchor = document.querySelector(VZ.i ? `[data-act=vzedit][data-g="${g.number}"][data-i="${VZ.i}"]` : `[data-act=gvsize][data-g="${g.number}"]:not([data-i]),[data-act=gresize][data-g="${g.number}"]`);
  TESTCARD.place(c,anchor,320,190);
}
const vzClamp = (v) => Math.max(-0.5, Math.min(0.5, Math.round(v * 1000) / 1000));
/* ask for the test frame; a newer click wins over an older answer */
function vzPreview() {
  const g = GM.get(VZ.g), i = g && vzTarget(g);
  if (!g || !i) return vzCard(false);
  VZ.src = ''; vzCard(true);
  const n = ++VZ.n;
  clearTimeout(VZ.t);
  VZ.t = setTimeout(async () => {
    const q = `index=${i}&frame=${VZ.frame}${VZ.scale != null ? '&scale=' + VZ.scale : ''}${VZ.resize != null ? '&resize=' + (VZ.resize ? 1 : 0) : ''}${VZ.off ? `&dx=${VZ.off[0]}&dy=${VZ.off[1]}` : ''}`;
    const r = await api(`/api/generations/${g.number}/anim_preview?${q}`);
    if (n !== VZ.n) return;
    if (!r.ok) { toast(r.j.error || 'Could not preview this', 1); return vzClear(); }
    VZ.src = r.j.url; VZ.meta = r.j; VZ.frame = r.j.frame; vzCard(true);
  }, 120);
}

ACT.gvsize = (el) => {
  const g = GM.get(+el.dataset.g);
  if (!g) return;
  vzStart(g, null); VZ.scale = Math.round(+el.dataset.f * 100) / 100; vzPreview(); glast = ''; tick(true);
};
ACT.gresize = (el) => {
  const g = GM.get(+el.dataset.g);
  if (!g) return;
  vzStart(g, null);
  VZ.resize = !vzResize(g);
  if (VZ.resize === gResize(g) && VZ.scale == null) return vzClear(), (glast = ''), tick(true);
  vzPreview(); glast = ''; tick(true);
};
ACT.vzedit = (el) => { const g = GM.get(+el.dataset.g); if (!g) return; vzStart(g, +el.dataset.i); vzPreview(); };
ACT.vzsize = (el) => { const g = GM.get(VZ.g); if (!g || !VZ.i) return; VZ.scale = Math.max(0.5, Math.min(1.5, Math.round((vzScale(g, VZ.i) + +el.dataset.d * VSTEP) * 100) / 100)); vzPreview(); };
ACT.vzmove = (el) => { const g = GM.get(VZ.g); if (!g || !VZ.i) return; const o = vzOff(g, VZ.i); VZ.off = [vzClamp(o[0] + (+el.dataset.x || 0) * OSTEP), vzClamp(o[1] + (+el.dataset.y || 0) * OSTEP)]; vzPreview(); };
ACT.vzcentre = () => {
  const g = GM.get(VZ.g), m = VZ.meta; if (!g || !VZ.i || !m || !m.centre_canvas) return;
  const o = vzOff(g, VZ.i);
  VZ.off = [vzClamp(o[0] + (256 - m.centre_canvas[0]) / 512), vzClamp(o[1] + (256 - m.centre_canvas[1]) / 512)]; vzPreview();
};
ACT.vzzero = () => { VZ.off = [0, 0]; vzPreview(); };
ACT.vzgrid = () => { VZ.grid = !VZ.grid; try { localStorage.setItem('vz.grid', VZ.grid ? '1' : '0'); } catch (e) {} vzCard(true); };
addEventListener('keydown', (e) => {                                  /* arrows nudge the open sticker 1% */
  if (!VZ.i || !document.getElementById('vzcard') || /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || '')) return;
  const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
  if (!d) return;
  e.preventDefault();
  const g = GM.get(VZ.g); if (!g) return;
  const o = vzOff(g, VZ.i);
  VZ.off = [vzClamp(o[0] + d[0] * OSTEP), vzClamp(o[1] + d[1] * OSTEP)]; vzPreview();
});

ACT.vzapply = async () => {
  const g = GM.get(VZ.g);
  if (!g || VZ.busy) return;
  VZ.busy = true; vzCard(true);
  const redo = (g.stickers || []).some((s) => s.anim_status === 'READY');
  let r = { ok: true };
  if (VZ.i) {
    const o = vzOff(g, VZ.i);
    r = await postBusy(`/api/generations/${g.number}/position`, { index: VZ.i, offset: o[0] || o[1] ? o : null, ...(VZ.scale != null ? { scale: VZ.scale } : {}), reslice: redo });
  } else {
    if (VZ.resize != null && VZ.resize !== gResize(g)) r = await postBusy(`/api/generations/${g.number}/resize`, { on: VZ.resize, reslice: redo && VZ.scale == null });
    if (r.ok && VZ.scale != null) r = await postBusy(`/api/generations/${g.number}/video_size`, { scale: VZ.scale, reslice: redo });
  }
  VZ.busy = false;
  if (!r.ok) return toast(r.j.error || 'Could not change it', 1), vzCard(true);
  toast(`Applied to ${VZ.i ? 'S' + VZ.i : 'the batch'}${redo ? ': cutting again (free)' : ''}`); vzClear(); glast = ''; tick(true);
};
ACT.vzcancel = () => { vzClear(); glast = ''; tick(true); };
/* tile sliders: readout on input (kept in vslPend so a poll redraw mid-drag does not reset the drag), one send after release */
const vslBox = (el) => (el && el.closest ? el.closest('.vsl') : null);
addEventListener('input', (e) => {
  const t = e.target;
  if (!t || t.tagName !== 'INPUT' || !t.matches('.vsl input[type=range]')) return;
  const box = vslBox(t); if (!box) return;
  const q = (k) => box.querySelector(`input[data-k=${k}]`);
  vslPend[`${box.dataset.g}:${box.dataset.i}`] = { scale: +q('scale').value / 100, dx: +q('dx').value / 100, dy: +q('dy').value / 100 };
  const b = t.parentElement.querySelector('b');
  if (b) b.textContent = t.dataset.k === 'scale' ? pct(+t.value / 100) : fmtOff(+t.value / 100);
});
addEventListener('change', (e) => {
  const t = e.target;
  if (!t || t.tagName !== 'INPUT' || !t.matches('.vsl input[type=range]')) return;
  const box = vslBox(t); if (!box) return;
  const key = `${box.dataset.g}:${box.dataset.i}`;
  clearTimeout(vslT[key]);
  const value = vslPend[key];
  if (value) vslT[key] = setTimeout(() => vslSend(box, value), 500);
});
/* a 409 busy means a cut is still running: wait for it and send once more (bounded), so a quick second
   edit after a release is not lost with an error. Only 409 retries: anything else, and the server did its job. */
async function postBusy(url, body, tries = 12) {
  for (let n = 0; ; n++) {
    const r = await post(url, body);
    if (r.ok || r.status !== 409 || n >= tries) return r;
    await new Promise((ok) => setTimeout(ok, 1000));
  }
}
async function vslSend(box, value) {
  const key = `${box.dataset.g}:${box.dataset.i}`;
  if (!value) return;
  if (vslBusy.has(key)) { vslT[key] = setTimeout(() => vslSend(box, value), 500); return; }
  const g = GM.get(+box.dataset.g);
  if (!g) return;
  const i = +box.dataset.i, t = (g.stickers || [])[i - 1];
  if (!t) return;
  const v = value;
  const cur = stOff(t);
  if (Math.abs(v.scale - stScale(g, t)) < 0.001 && Math.abs(v.dx - cur[0]) < 0.0005 && Math.abs(v.dy - cur[1]) < 0.0005) { if (vslPend[key] === v) delete vslPend[key]; return; }
  vslBusy.add(key);
  const redo = (g.stickers || []).some((s) => s.anim_status === 'READY');
  try {
    const offset = v.dx || v.dy ? [v.dx, v.dy] : null;
    const r = await postBusy(`/api/generations/${g.number}/position`, { index: i, offset, scale: v.scale, reslice: redo });
    if (!r.ok) { toast(r.j.error || 'Could not change it', 1); return; }
    // Cache the acknowledged values until tick reads the canonical server result.
    t.anim_scale = v.scale; t.anim_offset = offset;
    if (vslPend[key] === v) delete vslPend[key];
    glast = ''; tick(true);
  } catch (e) { toast('Could not change it: ' + e.message, 1); }
  finally { vslBusy.delete(key); }
}
ACT.vslreset = async (el) => {
  const g = GM.get(+el.dataset.g);
  if (!g) return;
  const i = +el.dataset.i, key = `${g.number}:${i}`;
  if (vslBusy.has(key)) return;
  clearTimeout(vslT[key]); delete vslPend[key];
  vslBusy.add(key);
  const redo = (g.stickers || []).some((s) => s.anim_status === 'READY');
  try {
    let r = await postBusy(`/api/generations/${g.number}/position`, { index: i, offset: null, reslice: false });
    if (r.ok) r = await postBusy(`/api/generations/${g.number}/video_size`, { index: i, scale: null, reslice: redo });
    if (!r.ok) return toast(r.j.error || 'Could not reset it', 1);
    const t = (g.stickers || [])[i - 1]; if (t) { delete t.anim_scale; t.anim_offset = null; }
    toast('Back to the batch'); glast = ''; tick(true);
  } catch (e) { toast('Could not reset it: ' + e.message, 1); }
  finally { vslBusy.delete(key); }
};
addEventListener('scroll', () => { if (document.getElementById('vzcard') && !VZ.drag) vzCard(true); }, { passive: true });
addEventListener('resize', () => { if (document.getElementById('vzcard')) vzCard(true); });

/* the batch's Download .zip asks first when an animation is shorter than 3 s (packs.js retimeAsk): Retime to 3 s or keep their length */
ACT.gzipdl = async (el) => {
  const n = el.dataset.g,
    rt = await retimeAsk(genShort(n), 'zip');
  if (rt === null) return;
  location.href = `/api/generations/${n}/export.zip?light=${el.dataset.l}${rt ? '&retime=1' : ''}`;
};

/* WP9: the pack this view just added to, as one .zip (the pack page's own export route) */
ACT.gpackzip = () => {
  if (SES.pack) location.href = `/api/packs/${SES.pack}/export.zip?light=1`;
};

/* one Studio refresh at a time: a concurrent force queues one rerun,
   interval ticks never overlap, hidden tabs skip interval work */
let tickBusy=false,tickForce=false;
async function tick(force) {
  if (force && tickBusy) { tickForce = true; return; }
  if (!force && tickBusy) return;
  if (!force && typeof document !== 'undefined' && document.hidden) return;
  tickBusy = true;
  try {
    if (route_ !== 'generate' && !MD && VG === null && SV.g === null) return;
    const h = await api('/api/generations');
    if (h.ok) GHEALTH = h.j.health;
    GSTALE = !!(h.ok && h.j.stale);
    const hh = $('ghealth');
    if (hh)
      hh.innerHTML =
        (GSTALE
          ? '<div class=warn><b>This server is running older code than the files on disk.</b> New buttons may say “not found” and fixes will not apply until you restart it: press Ctrl+C in its terminal, then run <code>python -m stickerloom serve</code> from the <code>stickerloom</code> folder.</div>'
          : '') +
        (GHEALTH && GHEALTH.vp9 === false
          ? '<div class=warn>This ffmpeg cannot encode VP9, so animations will fail. Run <code>python -m stickerloom doctor</code>.</div>'
          : '');
    let key = '';
    for (const id of SES.gens) {
      const r = await api('/api/generations/' + id);
      if (r.ok) {
        GM.set(id, r.j);
        key += JSON.stringify(r.j);
        autoRecheck(r.j);
      } else if (r.status === 404) {
        SES.gens = SES.gens.filter((x) => x !== id);
        saveSes();
      }
    }
    for (const id of SES.gens) {
      if (!FAM.has(id)) await famLoad(id);
      const f = FAM.get(id),
        mn = f ? +String(f.picked).replace(/\D/g, '') : id;
      if (mn !== id) {
        const r = await api('/api/generations/' + mn);
        if (r.ok) GM.set(mn, r.j);
      }
    }
    key += SES.gens.map((id) => JSON.stringify(FAM.get(id) || '')).join();
    for (const id of [...ANIM]) {
      const g = GM.get(id);
      if (g && animPhase(g) && !processing(g)) ANIM.delete(id);
    }
    key += bg + SES.off.join() + SES.pack + [...ANIM].join() + (LIB.packs || []).length + JSON.stringify(GD);
    const typing = document.activeElement && document.activeElement.dataset && document.activeElement.dataset.pd !== undefined;
    if (force || (key !== glast && !typing)) {
      glast = key;
      const el = $('gres');
      if (el) el.innerHTML = gview();
      gdPrice();
      if (MD) gmodal();
      drawVdlg();
      if (SV.g !== null && document.querySelector('.sheetdlg')) sheetDlg();
      if (typeof applyEdgePreview === 'function') applyEdgePreview();
      if (typeof hxSync === 'function') hxSync();
    }
  } catch (e) {
    const m = $('msg');
    if (m) m.textContent = 'Something went wrong: ' + e.message;
  } finally {
    tickBusy = false;
    if (tickForce) { tickForce = false; tick(true); }
  }
}

setInterval(tick, 700);
loadLib();

const layoutOfCell = (g, i) => {
  const v = cutOf(g),
    lay = v && LAY.get(g.number + v.id);
  const sl = lay && lay.slots.find((x) => x.slot === i);
  return sl ? sl.rect : null;
};
