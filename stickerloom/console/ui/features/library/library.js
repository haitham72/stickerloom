/* the packs of one batch group sit together, as the Studio sits the batches (pack.group from GET /api/library, flow/groups.pack_groups): entries {pack} or
   {group, packs} in the order the first pack of each appears (pure) */
const packEntries = (ps) => {
  const out = [],
    at = {};
  for (const p of ps || []) {
    if (!p.group) {
      out.push({ pack: p });
      continue;
    }
    if (at[p.group]) at[p.group].packs.push(p);
    else {
      at[p.group] = { group: p.group, packs: [p] };
      out.push(at[p.group]);
    }
  }
  for (const e of out)
    if (e.packs)
      e.packs.sort((a, b) => (b.lead ? 1 : 0) - (a.lead ? 1 : 0)); /* the parent (Assign as parent in the Studio) leads its group */
  return out;
};

const PKFOLD = new Map();
/* group -> open (true) / closed (false) as the person left it; untouched, a group is open while the pack in view is in it */
async function loadLib() {
  const r = await api('/api/library');
  if (r.ok) LIB = r.j;
  return LIB;
}

const media = (s, cls = '') =>
  s.type === 'animated'
    ? `<video class="${cls}" src="/lib/${encodeURIComponent(s.file)}" autoplay loop muted playsinline></video>`
    : `<img class="${cls}" src="/lib/${encodeURIComponent(s.file)}" loading=lazy>`;

const coverMedia = (p) => {
  const s = p.stickers.find((x) => x.id === p.cover) || p.stickers[0];
  return s ? media(s) : '<span class=mut>—</span>';
};

const packById = (id) => LIB.packs.find((p) => p.id === id);

/* ---------- Library (DESKTOP_01) */
let PACKS_ALL = false,
  LCL = [],
  LCI = null;

/* bulk selection (the square marker on a sticker), shared by the pack screen and My Stickers */
const SEL = new Map(),
  selKey = (p, i) => p + ':' + i;

const selRefresh = () => {
  if (route_ === 'pack') drawPack();
  else if (route_ === 'library') libBody();
};

ACT.lsel = (el) => {
  const k = selKey(el.dataset.p, el.dataset.id), kb = el === document.activeElement;
  if (SEL.has(k)) SEL.delete(k);
  else SEL.set(k, { pack_id: el.dataset.p, id: el.dataset.id });
  selRefresh();
  // the grid is redrawn: a keyboard toggle keeps focus on the same sticker's box (UX-03)
  if (kb) { const b = document.querySelector(`.selbox[data-p="${el.dataset.p}"][data-id="${el.dataset.id}"]`); if (b) b.focus(); }
};

ACT.lselall = () => {
  (route_ === 'pack'
    ? (packById(PACK_ID) || { stickers: [] }).stickers.map((s) => ({ pack_id: PACK_ID, id: s.id }))
    : LCL.map((s) => ({ pack_id: s.pack_id, id: s.id }))
  ).forEach((x) => SEL.set(selKey(x.pack_id, x.id), x));
  selRefresh();
};

ACT.lselnone = () => {
  SEL.clear();
  selRefresh();
};

/* the whole selection into one pack in ONE request (all or nothing); the selection is cleared only when it moved */
async function moveSelection(to) {
  const items = [...SEL.values()];
  if (!items.length) return false;
  const r = await post('/api/stickers/move', { to, items });
  if (!r.ok) {
    toast(r.j.error || 'Could not move', 1);
    return false;
  }
  SEL.clear();
  await loadLib();
  drawCol2();
  selRefresh();
  const p = packById(to);
  toast(
    `Moved ${r.j.moved} sticker${r.j.moved === 1 ? '' : 's'} to ${p ? p.name : 'the pack'}${r.j.skipped ? ` (${r.j.skipped} already there)` : ''}`,
  );
  return true;
}

ACT.lselmove = () => {
  if (SEL.size) pickPack((pid) => moveSelection(pid), `Move ${SEL.size} sticker${SEL.size === 1 ? '' : 's'} to`);
};

ACT.lseldel = () => {
  const items = [...SEL.values()];
  if (!items.length) return;
  confirmDlg(`Delete ${items.length} sticker${items.length === 1 ? '' : 's'}? This cannot be undone.`, async () => {
    const r = await post('/api/stickers/delete', { items });
    if (!r.ok) return toast(r.j.error, 1);
    SEL.clear();
    await loadLib();
    drawCol2();
    selRefresh();
    toast(`Deleted ${r.j.deleted} sticker${r.j.deleted === 1 ? '' : 's'}`);
  });
};

/* Explorer-style selection on the pack screen and My Stickers: drag a box on empty space (a plain drag selects only what it touches, Shift adds,
   Ctrl un-selects what it touches again), Ctrl/Shift+click on a sticker toggles it, Ctrl+A selects all, Esc clears. A plain press on a sticker still
   opens it / drags it to reorder. */
const MQ = { m: null, quiet: 0 };

const selScroller = (el) => {
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    const o = getComputedStyle(n).overflowY;
    if ((o === 'auto' || o === 'scroll') && n.scrollHeight > n.clientHeight) return n;
  }
  return document.scrollingElement;
};

const cellKey = (c) => {
  const b = c.querySelector('.selbox');
  return b ? selKey(b.dataset.p, b.dataset.id) : null;
};

const cellItem = (c) => {
  const b = c.querySelector('.selbox');
  return b ? { pack_id: b.dataset.p, id: b.dataset.id } : null;
};

function mqRect(m) {
  const x0 = m.x0 - m.sc.scrollLeft,
    y0 = m.y0 - m.sc.scrollTop;
  return { l: Math.min(x0, m.cx), t: Math.min(y0, m.cy), r: Math.max(x0, m.cx), b: Math.max(y0, m.cy) };
}

function mqHits(m, R) {
  return [...m.grid.querySelectorAll('.cell')].filter((c) => {
    const r = c.getBoundingClientRect();
    return r.left < R.r && r.right > R.l && r.top < R.b && r.bottom > R.t;
  });
}

function mqResult(m, hits) {
  const out = new Map(m.mode === 'replace' ? [] : m.base);
  hits.forEach((c) => {
    const k = cellKey(c);
    if (!k) return;
    if (m.mode === 'toggle') {
      if (out.has(k)) out.delete(k);
      else out.set(k, cellItem(c));
    } else out.set(k, cellItem(c));
  });
  return out;
}

function mqUpdate() {
  const m = MQ.m;
  if (!m) return;
  const R = mqRect(m);
  if (!m.moved) {
    if (Math.hypot(m.cx - (m.x0 - m.sc.scrollLeft), m.cy - (m.y0 - m.sc.scrollTop)) < 5) return;
    m.moved = true;
    document.body.classList.add('marq');
    m.grid.classList.add('mdim');
    m.box = document.createElement('div');
    m.box.className = 'marquee';
    document.body.appendChild(m.box);
  }
  Object.assign(m.box.style, { left: R.l + 'px', top: R.t + 'px', width: R.r - R.l + 'px', height: R.b - R.t + 'px' });
  const res = mqResult(m, mqHits(m, R));
  m.grid.querySelectorAll('.cell').forEach((c) => c.classList.toggle('msel', res.has(cellKey(c))));
}

function mqScroll() {
  const m = MQ.m;
  if (!m) return;
  const r = m.sc === document.scrollingElement ? { top: 0, bottom: innerHeight } : m.sc.getBoundingClientRect();
  const d = m.cy < r.top + 44 ? -(r.top + 44 - m.cy) / 3 : m.cy > r.bottom - 44 ? (m.cy - (r.bottom - 44)) / 3 : 0;
  if (d && m.moved) {
    m.sc.scrollTop += d;
    mqUpdate();
  }
  m.raf = requestAnimationFrame(mqScroll);
}

function mqEnd(commit) {
  const m = MQ.m;
  if (!m) return;
  MQ.m = null;
  cancelAnimationFrame(m.raf);
  document.body.classList.remove('marq');
  m.grid.classList.remove('mdim');
  if (m.box) m.box.remove();
  if (m.moved && commit) {
    const res = mqResult(m, mqHits(m, mqRect(m)));
    SEL.clear();
    res.forEach((v, k) => SEL.set(k, v));
    MQ.quiet = Date.now() + 250;
    selRefresh();
  } else if (!m.moved && commit && m.mode === 'replace' && SEL.size) {
    SEL.clear();
    selRefresh();
  } else if (m.moved) selRefresh();
}

document.addEventListener('mousedown', (e) => {
  if (e.button !== 0 || MQ.m) return;
  const scr = e.target.closest('#s-pack,#s-library');
  if (!scr) return;
  const grid = e.target.closest('.selgrid');
  if (!grid)
    return; /* only inside the sticker grid (My Stickers: all its pack boxes) (its gaps, or Shift/Ctrl on a sticker): the rest of the screen keeps the mouse */
  if (e.target.closest('button,input,select,textarea,a,label,.selbox,.selbar,.hov,.cap,.phead,.tabs,.fab,.seeall')) return;
  const mod = e.shiftKey || e.ctrlKey || e.metaKey;
  if (e.target.closest('.cell') && !mod) return;
  e.preventDefault();
  const sc = selScroller(grid);
  MQ.m = {
    grid,
    sc,
    x0: e.clientX + sc.scrollLeft,
    y0: e.clientY + sc.scrollTop,
    cx: e.clientX,
    cy: e.clientY,
    mode: e.ctrlKey || e.metaKey ? 'toggle' : e.shiftKey ? 'add' : 'replace',
    moved: false,
    base: new Map(SEL),
    box: null,
    raf: 0,
  };
  MQ.m.raf = requestAnimationFrame(mqScroll);
});

document.addEventListener('mousemove', (e) => {
  if (!MQ.m) return;
  MQ.m.cx = e.clientX;
  MQ.m.cy = e.clientY;
  mqUpdate();
});

document.addEventListener('mouseup', () => mqEnd(true));

window.addEventListener('blur', () => mqEnd(false));

document.addEventListener(
  'click',
  (e) => {
    if (Date.now() < MQ.quiet) {
      e.stopPropagation();
      e.preventDefault();
      return;
    }
    if (!(e.ctrlKey || e.metaKey || e.shiftKey)) return;
    const c = e.target.closest('.selgrid .cell');
    if (!c || e.target.closest('.hov,.selbox')) return;
    const k = cellKey(c);
    if (!k) return;
    e.stopPropagation();
    e.preventDefault();
    if (SEL.has(k)) SEL.delete(k);
    else SEL.set(k, cellItem(c));
    selRefresh();
  },
  true,
);

document.addEventListener('keydown', (e) => {
  if (
    !['pack', 'library'].includes(route_) ||
    /input|textarea|select/i.test((document.activeElement || {}).tagName || '') ||
    $('dlg').classList.contains('on') ||
    $('modal').classList.contains('on')
  )
    return;
  if (e.key === 'Escape' && SEL.size) {
    SEL.clear();
    selRefresh();
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a' && document.querySelector('.selgrid')) {
    e.preventDefault();
    ACT.lselall();
  }
});

function selBarHtml(total) {
  return SEL.size
    ? `<div class=selbar><b>${SEL.size} selected</b><button class=link data-act=lselall>Select all ${total}</button><button class=link data-act=lselnone>Clear</button><button class="btn sm" data-act=lselmove title="Move all the selected stickers into one pack (or a new one)">${ic('lib')} Move ${SEL.size} to…</button><button class="btn dng sm" data-act=lseldel>${ic('trash')} Delete ${SEL.size}</button></div>`
    : '';
}

RENDER.library = async () => {
  await loadLib();
  drawCol2();
  $('s-library').innerHTML = `<div class=page><div class=ph><h1>Sticker Library</h1></div>
   <input type=search id=libq placeholder="Search stickers or packs…" value="${esc(LIBQ)}" style="margin-bottom:14px">
   <div class=tabs><button class="tab ${LIBTAB === 'recent' ? 'on' : ''}" data-act=libtab data-t=recent>Recent</button><button class="tab ${LIBTAB === 'mine' ? 'on' : ''}" data-act=libtab data-t=mine>My Stickers</button><button class="tab ${LIBTAB === 'particles' ? 'on' : ''}" data-act=libtab data-t=particles>Reactions</button></div>
   <div id=libbody></div><div class=fab><button class="btn pri" data-act=nav data-to=create>${ic('plus')} Create</button></div></div>`;
  $('libq').oninput = (e) => {
    LIBQ = e.target.value;
    libBody();
  };
  libBody();
};

ACT.libtab = (el) => {
  LIBTAB = el.dataset.t;
  RENDER.library();
};

ACT.pkmerge = (el) => {
  const a = packById(el.dataset.id),
    b = packById(el.dataset.into);
  if (!a || !b) return;
  confirmDlg(
    `Merge “${a.name}” into “${b.name}”? Its animated stickers replace their stills there (names, emoji and particles stay), the others move over, and “${a.name}” goes to the trash (you can restore it).`,
    async () => {
      const r = await post(`/api/packs/${a.id}/merge`, { into: b.id });
      if (!r.ok) return toast(r.j.error || 'Could not merge', 1);
      await loadLib();
      toast(`${r.j.upgraded} upgraded to animated, ${r.j.moved} moved into “${b.name}”`);
      location.hash = '#/pack/' + b.id;
      drawCol2();
    },
    'Merge',
  );
};

ACT.pkunnest = async (el) => {
  const a = packById(el.dataset.id);
  if (!a) return;
  const r = await post(`/api/packs/${a.id}`, { parent: '' });
  if (!r.ok) return toast(r.j.error || 'Could not take it out', 1);
  await loadLib();
  drawCol2();
  toast(`“${a.name}” stands on its own again`);
};

ACT.pkgrp = (el) => {
  const g = el.dataset.g,
    open = el.getAttribute('aria-expanded') === 'true';
  PKFOLD.set(g, !open);
  drawCol2();
};

ACT.seeall = (el) => {
  if (el.dataset.k === 'st') {
    LIBTAB = 'mine';
  } else PACKS_ALL = !PACKS_ALL;
  RENDER.library();
};

/* My Stickers, one box per pack. ONE selection area over all the boxes, so a drag box
   crosses packs; the carousel index stays the position in the whole list (LCL) */
function libCountLabel(pack, fallback) {
  if (pack && Number.isInteger(pack.sticker_count) && Number.isInteger(pack.reaction_count))
    return pack.sticker_count+' sticker'+(pack.sticker_count===1?'':'s')+(pack.reaction_count?' · '+pack.reaction_count+' reaction'+(pack.reaction_count===1?'':'s'):'');
  return fallback+' item'+(fallback===1?'':'s');
}
function libByPackHtml(all) {
  if (!all.length) return '<span class=mut>No matches.</span>';
  const by = new Map();
  all.forEach((s, i) => {
    if (!by.has(s.pack_id)) by.set(s.pack_id, []);
    by.get(s.pack_id).push([s, i]);
  });
  const cell = ([s, i]) => {
    const on = SEL.has(selKey(s.pack_id, s.id));
    return `<div class="cell ${on ? 'sel' : ''}" data-act=lcopen data-i=${i}><button type=button class="selbox ${on ? 'on' : ''}" data-act=lsel data-p=${s.pack_id} data-id=${s.id} title="Select" aria-label="Select ${esc(s.name)}" aria-pressed=${on}></button>${media(s)}<button type=button class=cap data-act=lcopen data-i=${i} title="View">${esc(s.emoji)} ${esc(s.name)}</button></div>`;
  };
  return `<div class="selgrid lib-bypack ${SEL.size ? 'selmode' : ''}">${[...by].map(([pid, xs]) => `<section class=lib-pk><div class=lib-pkh><b>${esc(xs[0][0].pack)}</b><small class=mut>${libCountLabel((typeof LIBQ==='undefined'||!LIBQ.trim())&&typeof LIB!=='undefined'?(LIB.packs||[]).find(p=>p.id===pid):null,xs.length)}</small><button class=link data-act=openpack data-id=${esc(pid)}>Open pack</button></div><div class=grid>${xs.map(cell).join('')}</div></section>`).join('')}</div>`;
}

function libBody() {
  const q = LIBQ.trim().toLowerCase(),
    hit = (s) => !q || (s.name + ' ' + s.emoji + ' ' + (s.pack || '')).toLowerCase().includes(q);
  const stTile = (s, i) =>
    `<div class=st data-act=lcopen data-i=${i} title="${esc(s.name)}">${media(s)}<span class=em>${esc(s.emoji)}</span></div>`;
  let h = '';
  if (LIBTAB === 'particles') {
    h = typeof spLibHtml === 'function' ? spLibHtml() : '';
    if (typeof spLibSync === 'function') spLibSync();
  } else if (!LIB.total && !LIB.packs.length)
    h = `<div class="card" style="text-align:center;padding:40px"><h2>Nothing here yet</h2><p class=mut>Make a pack in the Studio, or create a sticker from a photo.</p><button class="btn pri" data-act=nav data-to=generate>${ic('gen')} Open Studio</button> <button class=btn data-act=nav data-to=create>${ic('create')} Create from photo</button></div>`;
  else if (LIBTAB === 'recent') {
    const rs = LIB.recent.filter(hit),
      ps = packEntries(LIB.packs.filter((p) => !q || p.name.toLowerCase().includes(q) || p.stickers.some(hit))),
      shown = PACKS_ALL ? ps : ps.slice(0, 4);
    LCL = rs;
    h = `<div class=row style="justify-content:space-between;margin:6px 0"><h2>Recent</h2><button class=seeall data-act=seeall data-k=st>See all ${ic('chev')}</button></div><div class=strip>${rs.map(stTile).join('') || '<span class=mut>No matches.</span>'}</div>
  <div class=row style="justify-content:space-between;margin:14px 0 8px"><h2>My Packs</h2>${ps.length > 4 ? `<button class=seeall data-act=seeall data-k=pk>${PACKS_ALL ? 'Show less' : 'See all'} ${ic('chev')}</button>` : ''}</div>
  <div class=plist>${
    shown
      .map((e) => {
        const row = (p) =>
          `<div class=packrow data-act=openpack data-id=${p.id}><div class=cover>${coverMedia(p)}</div><div style="flex:1"><b>${esc(p.name)}</b><br><small>${libCountLabel(p,(p.stickers||[]).length)}</small></div><button class=btn data-act=libpackstudio data-id=${p.id} ${(p.stickers||[]).some(s=>(s.source||{}).generation)?'':'disabled'}>Open in Studio</button>${ic('chev')}</div>`;
        return e.pack
          ? row(e.pack)
          : `<div class=pkgroup><div class=pkgh>${e.group.startsWith('P-') ? esc(e.packs[0].name) : `Group ${esc(e.group)}`} · ${e.packs.length} packs</div>${e.packs.map(row).join('')}</div>`;
      })
      .join('') || '<div class=mut style="padding:14px">No packs.</div>'
  }</div>`;
  } else {
    const all = LIB.packs.flatMap((p) => p.stickers.map((s) => ({ ...s, pack_id: p.id, pack: p.name }))).filter(hit);
    LCL = all;
    h = `<div class=mut style="margin-bottom:8px">Tick the square, or drag a box over the stickers (Shift adds, Ctrl un-selects). Then delete them together.</div>${selBarHtml(all.length)}${libByPackHtml(all)}`;
  }
  $('libbody').innerHTML = h;
  if(LIBTAB==='particles'&&typeof rxTab==='function'&&rxTab()==='lottie'){
    lsThumbs();lsMount();
  }
  if(LIBTAB==='particles'&&typeof rxTab==='function'&&rxTab()==='super'&&typeof supMount==='function')supMount();
}

ACT.openpack = (el) => {
  location.hash = '#/pack/' + el.dataset.id;
};

ACT.newpack = () =>
  ACT.impackopen
    ? dlg(
        `<h2>Add a pack</h2><div class=row><button class="btn pri" data-act=newpackname>New pack</button><button class=btn data-act=impackopen title="Upload a sticker sheet or its animated video: it is cut into stickers and waits in the Studio">Import pack</button></div><div class=row style="justify-content:flex-end"><button class=btn data-act=dlgx>Cancel</button></div>`,
      )
    : ACT.newpackname();

ACT.newpackname = () =>
  askText(
    'New pack name',
    'My Pack',
    async (n) => {
      const r = await post('/api/packs', { name: n });
      if (r.ok) {
        await loadLib();
        location.hash = '#/pack/' + r.j.id;
      } else toast(r.j.error, 1);
    },
    'Create',
  );
