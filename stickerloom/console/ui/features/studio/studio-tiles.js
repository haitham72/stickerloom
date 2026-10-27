/* ---------- issue colours: ONE colour per kind of problem, the same on the tile, the chip, the marks on the sheets and in the legend.
   [colour, name, text colour on that colour]. DASHED + FADED = will not be exported (never disabled: it stays clickable and can be brought back or allowed); solid = exported, with a check. */
const CAT = {
  bounds: ['#f97316', 'Out of bounds', '#fff'],
  key: ['#a855f7', 'Bad green screen', '#fff'],
  loop: ['#eab308', 'Bad loop', '#422006'],
  look: ['#ec4899', 'Look or motion', '#fff'],
  spec: ['#3b82f6', 'File or Telegram limit', '#fff'],
  bad: ['#ef4444', 'Dropped or blocked', '#fff'],
};

const CATORDER = Object.keys(CAT);

const CATOF = {
  inside_cell: 'bounds',
  inside_slot: 'bounds',
  inside_frame: 'bounds',
  cross_slot: 'bounds',
  edge_trimmed: 'bounds',
  loop_seam: 'loop',
  no_spill: 'key',
  chroma_risk: 'key',
  holes: 'key',
  background_is_key: 'key',
  background_flat: 'key',
  alpha_stable: 'key',
  transparent_corners: 'key',
  identity_kept: 'look',
  motion_present: 'look',
  single_subject: 'look',
  duplicate_cell: 'look',
  sharpness: 'look',
  size_budget: 'spec',
  codec_vp9: 'spec',
  dimensions: 'spec',
  fps: 'spec',
  duration: 'spec',
  no_audio: 'spec',
  alpha_mode_tag: 'spec',
  alpha_decoded: 'spec',
  static_file: 'spec',
  telegram_sticker: 'spec',
  telegram_stroke: 'spec',
};

/* warnings in plain words: what Python noticed, and what you can do. The sticker stays in your set; nothing is removed for you. */
const WARNWHY = {
  duplicate_cell: (t, c) =>
    `looks almost the same as S${(c && c.data && c.data.of) || 'another sticker'} (the same pose drawn twice?). Kept: drop one with the x if you do not want both`,
  single_subject: () =>
    'two separate shapes in this cell (a second character, or a neighbour bleeding in). Kept: look at it and drop it if it is wrong',
  chroma_risk: (t, c) =>
    `part of the character is close to the green-screen colour${c && c.value != null ? ` (${(c.value * 100).toFixed(0)}% of it)` : ''}, so some of it may have been cut away. Kept: check the edges`,
  holes: () => 'a hole inside the character, probably a green part that the green-screen cut removed. Kept: check it',
  edge_trimmed: () => 'the trim took off more than the thin rim. Kept: check the edge or lower the trim',
};

const plainWarn = (t, w) => {
  const c = (t.report || []).find((r) => r.name === w),
    f = WARNWHY[w];
  return f ? f(t, c) : 'kept, check: ' + w;
};

const ANIMWHY = {
  inside_slot: 'the character leaves its own slot on the sheet',
  cross_slot: "the character reaches into a neighbour's slot",
  empty_subject: 'nothing was found in its slot',
  loop_seam: 'the loop does not close',
  size_budget: 'the file is over the size limit',
};

/* "Use it anyway": a sticker or an animation Python blocked as a judgement call (a character touching its cell, a hole, a slot, a loop) may be allowed by a person, with a click that is recorded
   and can be taken back. Telegram's own limits (format, size, codec) and a cell with no picture stay final. The SERVER says which (`g.allow`, flow/gates.py allow_info): per kind, 'still' | 'animation',
   `can` = may be allowed now, `allowed` = carries a permission, `undo` = the permission may be taken back now, `why` = the plain-words reason of every blocked sticker, `final` = why one cannot be allowed. */
const ALW = (g, kind) => Object.assign({ can: [], allowed: [], undo: [], why: {}, final: {} }, (g && g.allow && g.allow[kind]) || {});

const whyOf = (g, t, kind) => ALW(g, kind).why[t.index];

const canAllow = (g, t, kind = 'animation') => ALW(g, kind).can.includes(t.index);

const hasAllowed = (g, t, kind = 'animation') => ALW(g, kind).allowed.includes(t.index);

const clickAllow = (g, t, kind = 'animation') => canAllow(g, t, kind) || hasAllowed(g, t, kind);

/* ONE decision per cell (the UI/UX spec P1, P2). Every control on a cell is the same control: the sheet's cell, the tile's x / +, Use it anyway, Include anyway, Take it back, Bring back, Drop from the
   set. They all press ACT.gcell, which asks cellOp what to do from the server's word (`g.allow`, flow/gates.py allow_info) and the review state, so the same click sends the same request and leaves
   the same decision row. op: 'allow' | 'unallow' (a Python block that is a judgement call: POST .../allow), 'include' | 'drop' (a human decision on a sticker or animation that is made: POST .../drop;
   out of bounds is OFF by default and 'include' is its "Include anyway", HANDOFF 3 stays), 'none' (final: it says why, it never opens anything). `stage` is 'still' | 'anim'. */
function cellOp(g, t, stage) {
  const kind = stage === 'anim' ? 'animation' : 'still',
    A = ALW(g, kind);
  if (canAllow(g, t, kind)) return { op: 'allow', kind };
  if (hasAllowed(g, t, kind) && A.undo.includes(t.index)) return { op: 'unallow', kind };
  if (stage === 'still' && animPhase(g) && t.anim_status === 'READY')
    return { op: 'none', why: 'This sticker is animated: decide on its animation.' };
  if (t.status !== 'READY')
    return {
      op: 'none',
      why: A.final[t.index] || A.why[t.index] || (t.reason ? 'Blocked: ' + String(t.reason).replace(/_/g, ' ') : 'Nothing to change here.'),
    };
  if (stage === 'anim') {
    if (t.anim_status !== 'READY')
      return { op: 'none', why: A.final[t.index] || A.why[t.index] || 'There is no animation to decide on yet.' };
    return { op: t.review.still === 'REJECTED' || ['REJECTED', 'BLOCKED'].includes(t.review.anim) ? 'include' : 'drop' };
  }
  return { op: t.review.still === 'REJECTED' ? 'include' : 'drop' };
}

const cellVerb = (o, t) =>
  ({ allow: 'Use it anyway', unallow: 'Take it back', include: isOob(t) ? 'Include anyway' : 'Bring back', drop: 'Drop from the set' })[
    o.op
  ] || '';

const cellTitle = (o, t) =>
  ({
    allow: 'Use it anyway',
    unallow: 'Take it back',
    include: isOob(t) ? 'Include this animation anyway' : 'Bring this one back',
    drop: 'Drop this one from the set',
  })[o.op] || '';

/* what hovering a cell says it will do, in plain words */
const cellLabel = (o, t) =>
  ({
    allow: 'click to use it anyway',
    unallow: 'click to take the permission back',
    include: isOob(t) ? 'click to include it anyway' : 'click to bring it back',
    drop: 'click to drop it from the set',
  })[o.op] ||
  o.why ||
  '';

const cellAct = (g, t, stage) => `data-act=gcell data-g=${g.number} data-i=${t.index} data-stage=${stage}`;

/* the box of a sticker (or an animation) that Python blocked: why in plain words, then 'Use it anyway' when it may be allowed, else why it cannot be */
function blockedBox(g, t, kind) {
  const A = ALW(g, kind),
    still = kind === 'still',
    why = A.why[t.index] || (still ? t.reason || t.status : ANIMWHY[t.anim_reason] || t.anim_reason || 'failed');
  return `<div class=gbadmsg><b>${still ? 'Blocked' : 'No animation'}</b><div class=mut style="margin:4px 0 8px">${esc(why)}</div>${A.can.includes(t.index) ? `<button class="btn sm pri" ${cellAct(g, t, still ? 'still' : 'anim')} title="Python's check is a judgement call: you decide. It is recorded, and you can take it back">Use it anyway</button>` : A.final[t.index] ? `<div class=mut>${esc(A.final[t.index])}</div>` : ''}</div>`;
}

/* A blocked animation still has a finished clip on disk. Keep that picture in place, dimmed by .gt.nx, and put the reason/action over it. */
function blockedAnimOverlay(g, t) {
  const A = ALW(g, 'animation'),
    why = A.why[t.index] || ANIMWHY[t.anim_reason] || t.anim_reason || 'failed';
  return `<div class=gblockover><b>Blocked</b><span>${esc(why)}</span>${A.can.includes(t.index) ? `<button class="btn sm pri" ${cellAct(g, t, 'anim')} title="Python's check is a judgement call: you decide. It is recorded, and you can take it back">Use it anyway</button>` : A.final[t.index] ? `<span>${esc(A.final[t.index])}</span>` : ''}</div>`;
}

/* what is wrong with a sticker at a stage ('still' | 'anim'): hard ones first, then by kind */
function issuesOf(t, stage, g) {
  const out = [],
    add = (id, text, soft) => out.push({ cat: CATOF[id] || 'bad', id, text, soft: !!soft });
  if (stage === 'anim') {
    if (t.status !== 'READY' || t.review.still === 'REJECTED') return out;
    if (t.anim_status === 'FAILED')
      add(t.anim_reason, 'no animation: ' + (whyOf(g, t, 'animation') || ANIMWHY[t.anim_reason] || t.anim_reason || 'failed'));
    if (t.anim_status === 'READY')
      (t.anim_report || [])
        .filter((c) => !c.ok)
        .forEach((c) => add(c.name, c.name === 'inside_frame' ? oobNote(t) : c.detail || c.name, c.severity === 'WARN'));
    if (t.review.anim === 'REJECTED') add('dropped', 'dropped from the set');
  } else {
    if (t.status === 'FAILED') add(t.reason, 'blocked: ' + (whyOf(g, t, 'still') || t.reason || 'failed'));
    else {
      ((t.metrics || {}).warnings || []).forEach((w) => add(w, plainWarn(t, w), true));
      (t.report || [])
        .filter((c) => !c.ok && ((t.metrics || {}).waived || []).includes(c.name))
        .forEach((c) => add(c.name, c.detail || c.name, true));
    }
    if (t.review.still === 'REJECTED') add('dropped', 'dropped from the set');
  }
  return out.sort((a, b) => a.soft - b.soft || CATORDER.indexOf(a.cat) - CATORDER.indexOf(b.cat));
}

function mark(t, stage, g) {
  const is = issuesOf(t, stage, g);
  if (!is.length) return null;
  const inSet =
    stage === 'anim'
      ? t.anim_status === 'READY' && !['REJECTED', 'BLOCKED'].includes(t.review.anim)
      : t.status === 'READY' && t.review.still !== 'REJECTED';
  return { cat: is[0].cat, issues: is, accepted: inSet };
}

function chip(g, t, stage) {
  const m = mark(t, stage, g),
    kind = stage === 'anim' ? 'animation' : 'still',
    allow = clickAllow(g, t, kind);
  let cls,
    title,
    st = '';
  if (m) {
    cls = 'iss' + (m.accepted ? ' soft' : '');
    st = ` style="--cc:${CAT[m.cat][0]}"`;
    title = `S${t.index}: ${m.issues.map((i) => CAT[i.cat][1] + ' - ' + i.text).join('; ')}${m.accepted ? '' : ' (not in the set)'}`;
  } else if (stage === 'anim') {
    const c = cellState(t);
    cls = c;
    title = `S${t.index}: ${CELLTXT[c]}`;
  } else {
    cls = 'ok';
    title = `S${t.index}: accepted`;
  }
  return `<button class="vchip ${cls}"${st} ${allow ? cellAct(g, t, stage) : `data-act=gopen data-g=${g.number} data-i=${t.index}`} title="${esc(title + (allow ? (canAllow(g, t, kind) ? ' · click to use it anyway' : ' · click to take the permission back') : ''))}">${t.index}</button>`;
}

function legend(g, stage) {
  const cs = new Set();
  g.stickers.forEach((t) => {
    const m = mark(t, stage, g);
    if (m) m.issues.forEach((i) => cs.add(i.cat));
  });
  return cs.size
    ? `<div class=lgrow>${CATORDER.filter((c) => cs.has(c))
        .map((c) => `<span class=lgd style="--cc:${CAT[c][0]}"><i></i>${CAT[c][1]}</span>`)
        .join(
          '',
        )}</div><div class="mut lgnote">dashed and faded = will NOT be exported (click it to allow it when it can be); solid = exported, with a check</div>`
    : '';
}

/* ---------- the result */
function tileHtml(g, t, mode) {
  const base = `/out/${g.generation_id}/`,
    anim = mode === 'anim',
    ap = animPhase(g),
    off = anim ? t.review.anim === 'REJECTED' || t.review.still === 'REJECTED' : t.review.still === 'REJECTED',
    blk = anim && isOob(t),
    mk = mark(t, anim ? 'anim' : 'still', g),
    hard = mk && !mk.accepted,
    kind = anim ? 'animation' : 'still',
    can = canAllow(g, t, kind),
    allowedNow = hasAllowed(g, t, kind),
    live = anim && !off && PVON.has(g.number) && t.status === 'READY' && g.source.video_path && !t.webm,
    blockedClip = anim && t.webm && t.anim_status === 'FAILED',
    hasV = t.webm && (t.anim_status === 'READY' || blockedClip);
  let m;
  const raw = !anim && GS.screen && g.source.grid && g.source.sheet_copy && g.source.sheet_size && (g.source.grid.rects || [])[t.index - 1];
  if (raw) {
    const [x, y, w, h] = raw,
      [W, H] = g.source.sheet_size; /* "with the screen": the cell cut from the raw sheet, its green or blue screen behind the character */
    m = `<span class=gtrawbox><span class=gtraw style="aspect-ratio:${w}/${h};${w >= h ? 'width:100%' : 'height:100%'};background-image:url('${base + g.source.sheet_copy}');background-size:${((W / w) * 100).toFixed(3)}% ${((H / h) * 100).toFixed(3)}%;background-position:${W > w ? ((x / (W - w)) * 100).toFixed(3) : 0}% ${H > h ? ((y / (H - h)) * 100).toFixed(3) : 0}%" role=img aria-label="S${t.index} on its screen"></span></span>`;
  } else if (!anim)
    m = t.png
      ? `<img src="${base + t.png}?e=${t.edited_at || t.rendered_at || 0}" loading=lazy>`
      : t.status === 'FAILED'
        ? blockedBox(g, t, 'still')
        : `<div class=gbadmsg>${esc(t.reason || t.status)}</div>`;
  else
    m = hasV
      ? `<video src="${base + t.webm}?v=${t.anim_at || 0}" autoplay loop muted playsinline></video>`
      : live
        ? `<canvas data-g=${g.number} data-pv=${t.index} width=288 height=288></canvas><span class=livebadge>live preview</span>`
        : t.anim_status === 'FAILED'
          ? blockedBox(g, t, 'animation')
          : t.anim_status === 'STALE'
            ? `<div class="gbadmsg mut">${esc(t.anim_reason || 'Animate again')}</div>`
            : t.status === 'READY'
              ? `<div class="gbadmsg mut">Not animated yet</div>`
              : t.status === 'FAILED'
                ? blockedBox(g, t, 'still')
                : `<div class=gbadmsg>${esc(t.reason || t.status)}</div>`;
  const stg = anim ? 'anim' : 'still',
    op = cellOp(g, t, stg),
    canX = op.op !== 'none' && (anim ? ap : !ap || op.op === 'allow' || op.op === 'unallow');
  const lines = mk
    ? mk.issues
        .slice(0, 2)
        .map((i) => `<div class=giss style="--cc:${CAT[i.cat][0]}"><i></i>${esc(CAT[i.cat][1])}: ${esc(i.text)}</div>`)
        .join('')
    : '';
  const pend = alPending(g, kind, t.index);
  return `<div class="gt ${off ? 'off' : ''} ${off || hard || blk ? 'nx' : ''} ${blk ? 'blk' : ''} ${hard ? 'iss' : mk ? 'issw' : ''}${pend !== undefined ? ' pend' : ''}"${pend !== undefined ? ` data-pend="${pend ? 'Using it…' : 'Taking it back…'}"` : ''}${mk ? ` style="--cc:${CAT[mk.cat][0]};--ct:${CAT[mk.cat][2]}"` : ''}><div class="gtv bg-${bg}" data-act=gopen data-g=${g.number} data-i=${t.index} title="${esc(t.key.replace(/_/g, ' '))}${mk ? ' · ' + esc(mk.issues.map((i) => CAT[i.cat][1]).join(', ')) : ''}">${m}${blockedClip ? blockedAnimOverlay(g, t) : hard && !off ? `<span class="isstag${can ? ' clk' : ''}" ${can ? `${cellAct(g, t, stg)} title="Click to use it anyway"` : ''}>${esc(CAT[mk.cat][1])}${can ? ' · click to use it anyway' : ''}</span>` : ''}${off ? '<span class="isstag offtag">Not in the set</span>' : ''}<div class=gtinfo aria-hidden=true><b>${esc(t.key.replace(/_/g, ' '))}</b>${lines}</div></div><span class=gem>${esc(t.emoji)}</span>
    ${canX ? `<button class=gx ${cellAct(g, t, stg)} title="${cellTitle(op, t)}">${['allow', 'include'].includes(op.op) ? ic('plus') : ic('x')}</button>` : ''}
    <div class=gcap>${anim && hasV && typeof vsizeRow === 'function' ? vsizeRow(g, t) : ''}${blk ? `<div class=giss style="--cc:${CAT[mk.cat][0]}">Off by default, not added. <button class="btn sm gincl" ${cellAct(g, t, stg)}>Include anyway</button></div>` : ''}${t.edited ? '<div class=gwarn style="color:var(--pri-d)">edited</div>' : ''}${t.anim_from_previous && t.webm ? `<div class=gwarn style="color:var(--pri-d)">The animation is from the previous picture <button class="btn sm" data-act=ganimslice data-g=${g.number} data-i=${t.index}>Animate again</button></div>` : ''}${allowedNow ? `<div class=gwarn style="color:var(--pri-d)">allowed by you${mk&&mk.issues.length?' — '+esc(mk.issues.slice(0,2).map(i=>i.text).join('; ')):''}${ALW(g, kind).undo.includes(t.index) ? ` · <button class=link ${cellAct(g, t, stg)}>Take it back</button>` : ''}</div>` : ''}${off && !mk ? '<div class=gwarn>Dropped</div>' : ''}</div></div>`;
}

ACT.ganimslice = async (el) => {
  const r = await postWait(
    `/api/generations/${el.dataset.g}/animate`,
    { scope: 'slice', index: +el.dataset.i },
    'Animating the sticker again…',
  );
  if (!r.ok) {
    toast(r.j.error, 1);
    return;
  }
  glast = '';
  tick(true);
};

let GREP = { g: 0, i: 0 };

ACT.greplace = (el) => {
  GREP = { g: +el.dataset.g, i: +el.dataset.i };
  let f = $('grep-file');
  if (!f) {
    f = document.createElement('input');
    f.type = 'file';
    f.id = 'grep-file';
    f.accept = '.png,.jpg,.jpeg,.webp';
    f.hidden = true;
    f.onchange = grepSend;
    document.body.appendChild(f);
  }
  f.click();
};

async function grepSend(e) {
  const f = e.target.files[0];
  e.target.value = '';
  if (!f) return;
  const du = await new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result);
    r.onerror = rej;
    r.readAsDataURL(f);
  });
  const r = await postWait(`/api/generations/${GREP.g}/replace`, { index: GREP.i, png: du }, 'Replacing the sticker…');
  if (!r.ok) {
    toast(r.j.error, 1);
    return;
  }
  toast(r.j.anim_from_previous ? 'Replaced; the animation still shows the previous picture' : 'Replaced');
  glast = '';
  tick(true);
}

ACT.grepundo = async (el) => {
  const r = await post(`/api/generations/${el.dataset.g}/replace`, { index: +el.dataset.i, undo: true });
  if (!r.ok) {
    toast(r.j.error, 1);
    return;
  }
  toast('Taken back');
  glast = '';
  tick(true);
};

/* "Use it anyway" / "Take it back": POST .../allow {kind, index, allow}. An animation (the default kind) is cut again from the stored video, a still from the stored sheet: free, a few seconds. */
/* "Use it anyway" / "Take it back" never makes the next click wait. A click when nothing of its
   batch and kind is on its way is sent at once, as always; clicks while one is on its way are queued (their tiles say so at once; a second click on the same
   sticker cancels it) and go as ONE request per direction (POST …/allow {indexes}) the moment the one before has answered. */
const ALQ = new Map();
// "<batch>:<kind>" -> {busy, m: Map(index -> allow)}
const alPending = (g, kind, i) => {
  const q = ALQ.get(g.number + ':' + kind);
  return q && q.m.has(i) ? q.m.get(i) : undefined;
};

async function allowCall(g, t, kind, allow) {
  const key = g.number + ':' + kind;
  let q = ALQ.get(key);
  if (!q) {
    q = { busy: false, m: new Map() };
    ALQ.set(key, q);
  }
  if (q.busy) {
    if (q.m.has(t.index)) q.m.delete(t.index);
    else q.m.set(t.index, allow);
    glast = '';
    if (typeof tick === 'function') tick(true);
    return;
  }
  q.busy = true;
  let body = { kind, index: t.index, allow };
  try {
    while (body) {
      const r = await postWait(`/api/generations/${g.number}/allow`, body, 'Finishing the previous step…'),
        n = body.indexes || [body.index],
        what = kind === 'still' ? 'picture' : 'animation';
      if (!r.ok) toast(r.j.error || 'Could not change it', 1);
      else
        toast(
          body.allow
            ? `${n.map((i) => 'S' + i).join(', ')} ${n.length > 1 ? 'are' : 'is'} used anyway: cutting the ${what}${n.length > 1 ? 's' : ''} again…`
            : `${n.map((i) => 'S' + i).join(', ')}: the permission is taken back`,
        );
      const yes = [...q.m].filter(([, a]) => a).map(([i]) => i),
        no = [...q.m].filter(([, a]) => !a).map(([i]) => i);
      q.m.clear();
      if (yes.length && no.length)
        q.m = new Map(no.map((i) => [i, false])); /* both directions waited: the uses go now, the take-backs right after */
      body = yes.length ? { kind, indexes: yes, allow: true } : no.length ? { kind, indexes: no, allow: false } : null;
      glast = '';
      if (typeof tick === 'function') tick(true);
    }
  } finally {
    q.busy = false;
    if (!q.m.size) ALQ.delete(key);
  }
}

/* a human decision on a sticker or animation that is made: drop it from the set, or include it / bring it back. Never deletes. POST .../drop {index, dropped} */
async function dropCall(g, t, drop) {
  const r = await postWait(`/api/generations/${g.number}/drop`, { index: t.index, dropped: drop });
  if (!r.ok) toast(r.j.error, 1);
  else if (!drop && isOob(t)) toast(`S${t.index} is included anyway: it will be added with the rest (the marker stays)`);
  glast = '';
  await tick(true);
  if (MD) gmodal();
}

/* the one handler of every control on a cell */
function cellRun(g, t, stage) {
  const o = cellOp(g, t, stage);
  if (o.op === 'allow' || o.op === 'unallow') return allowCall(g, t, o.kind, o.op === 'allow');
  if (o.op === 'include' || o.op === 'drop') return dropCall(g, t, o.op === 'drop');
  toast(o.why || 'Nothing to change here.');
}

ACT.gcell = (el) => {
  const g = GM.get(+el.dataset.g),
    t = g && g.stickers[+el.dataset.i - 1];
  if (g && t) return cellRun(g, t, el.dataset.stage === 'anim' ? 'anim' : 'still');
};

/* one bulk control per batch, per kind, above the grid: "Use all anyway (N)" / "Take all back (N)", where N counts what is allow-able NOW, never everything.
   Stills live under the green-screen panel, animations under the video sheet. */
const ALWBUSY = new Set();
// "<batch number>:<kind>" while a bulk allow / take-back is on its way: the buttons wait instead of answering a second click with a "busy" 409
const alwBusy = (g, kind) => ALWBUSY.has(g.number + ':' + kind) || (kind !== 'still' && processing(g));

function allowAllRow(g, kind) {
  const A = ALW(g, kind),
    a = A.can.length,
    b = A.undo.length,
    what = kind === 'still' ? 'sticker' : 'animation';
  if (!a && !b) return '';
  const bz = alwBusy(g, kind),
    lock = bz ? ' disabled aria-busy=true' : '';
  return `<div class=lv-allow>${a ? `<button class="btn sm pri" data-act=gallowall data-g=${g.number} data-kind=${kind} data-allow=1${lock} title="Use every ${what} anyway that Python blocked as a judgement call">${bz ? 'Cutting again…' : `Use all anyway (${a})`}</button>` : ''}
   ${b ? `<button class="btn sm" data-act=gallowall data-g=${g.number} data-kind=${kind} data-allow=0${lock} title="Block the ${what}s you allowed again">${bz ? 'Cutting again…' : `Take all back (${b})`}</button>` : ''}<span class=mut>or click a cell on the sheet</span></div>`;
}

ACT.gallowall = async (el) => {
  const g = GM.get(+el.dataset.g);
  if (!g) return;
  const allow = el.dataset.allow === '1',
    kind = el.dataset.kind === 'still' ? 'still' : 'animation',
    key = g.number + ':' + kind;
  if (ALWBUSY.has(key) || el.disabled) return;
  ALWBUSY.add(key);
  const row = el.closest('.lv-allow');
  if (row)
    row.querySelectorAll('button[data-act=gallowall]').forEach((b) => {
      b.disabled = true;
      b.setAttribute('aria-busy', 'true');
      b.textContent = 'Cutting again…';
    });
  try {
    const r = await postWait(`/api/generations/${g.number}/allow`, { all: true, allow, kind }, 'Finishing the previous step…');
    if (!r.ok) return toast(r.j.error || 'Could not change it', 1);
    toast(
      allow
        ? `Allowed ${r.j.indexes.length}: cutting their ${kind === 'still' ? 'pictures' : 'animations'}…`
        : `Took back ${r.j.indexes.length}`,
    );
  } finally {
    ALWBUSY.delete(key);
    glast = '';
    if (typeof tick === 'function') tick(true);
  }
};
