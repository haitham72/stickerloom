/* stickerloom Studio: shell, router, Library, Generate (lifecycle console), Settings. No external libraries. */
'use strict';

const $ = (id) => document.getElementById(id);

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const api = async (u, o) => {
  const r = await fetch(u, o);
  let j = {};
  try {
    j = await r.json();
  } catch (e) {}
  return { ok: r.ok, status: r.status, j };
};

const post = (u, b, h) =>
  api(u, { method: 'POST', headers: { 'Content-Type': 'application/json', ...(h || {}) }, body: JSON.stringify(b || {}) });

/* one key per click: a retry or a second tab with the same key is answered with the first result, never run (and paid) twice */
const ikey = () => (self.crypto && crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));

let toastT = 0;
function toast(m, bad) {
  const t = $('toast');
  t.textContent = m || '';
  t.className = (m ? 'on ' : '') + (bad ? 'bad' : '');
  clearTimeout(toastT);
  if (m) toastT = setTimeout(() => (t.className = ''), bad ? 6000 : 3500);
}

const say = (t) => {
  const m = $('msg');
  if (m) m.innerHTML = t || '';
};
// callers escape what they pass
const ICONS = {
  home: '<path d="M4 11 12 4l8 7v8a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z"/>',
  ai: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 3.5l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6zM5.5 16l.6 1.6 1.6.6-1.6.6L5.5 20l-.6-1.7-1.6-.6 1.6-.6z"/>',
  panel: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/>',
  gen: '<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  lib: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
  create: '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.3a2.5 2.5 0 0 1 4.9.8c0 1.7-2.5 2.2-2.5 3.8"/><path d="M12 17.2v.1"/>',
  settings:
    '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 010 12h-3"/>',
  redo: '<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 000 12h3"/>',
  text: '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
  emoji: '<circle cx="12" cy="12" r="9"/><path d="M8.5 14a4 4 0 007 0M9 9.5h.01M15 9.5h.01"/>',
  sticker: '<path d="M4 6a2 2 0 012-2h12a2 2 0 012 2v8l-6 6H6a2 2 0 01-2-2z"/><path d="M14 20v-4a2 2 0 012-2h4"/>',
  border: '<rect x="4" y="4" width="16" height="16" rx="4" stroke-dasharray="3 3"/>',
  adjust: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 010 18z" fill="currentColor"/>',
  erase: '<path d="M20 20H9L4 15l9-9 7 7-6 6"/><path d="M8 11l5 5"/>',
  restore: '<path d="M3 12a9 9 0 109-9 9 9 0 00-7 3.4L3 8"/><path d="M3 3v5h5"/>',
  chat: '<path d="M4 5a2 2 0 012-2h12a2 2 0 012 2v10a2 2 0 01-2 2h-7l-5 4v-4H6a2 2 0 01-2-2z"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  eyeoff:
    '<path d="M3 3l18 18M10.6 6.2A9.6 9.6 0 0112 5c6 0 10 7 10 7a17 17 0 01-3 3.7M6.6 6.7A17 17 0 002 12s4 7 10 7a9.7 9.7 0 004.3-1"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/>',
  unlock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 017-2.5"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  chev: '<path d="M9 6l6 6-6 6"/>',
  download: '<path d="M12 4v11M7 11l5 5 5-5M5 20h14"/>',
  upload: '<path d="M12 16V5M7 9l5-5 5 5M5 20h14"/>',
  star: '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  up: '<path d="M6 15l6-6 6 6"/>',
  down: '<path d="M6 9l6 6 6-6"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  hist: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  folder: '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>',
  film: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M8 4v16M16 4v16M3 9h5M16 9h5M3 15h5M16 15h5"/>',
  telegram: '<path d="M21 4L3 11l6 2 2 6 3-4 5 4z"/><path d="M9 13l8-6"/>',
  play: '<path d="M8 5l11 7-11 7z"/>',
  photo: '<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
  eyeb: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  check: '<path d="M5 12l5 5 9-10"/>',
  first: '<path d="M6 5v14M18 6l-9 6 9 6z"/>',
  last: '<path d="M18 5v14M6 6l9 6-9 6z"/>',
  prev: '<path d="M15 6l-6 6 6 6"/>',
  next: '<path d="M9 6l6 6-6 6"/>',
  users:
    '<circle cx="9" cy="8" r="3.2"/><path d="M3 19c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5"/><path d="M16 4.8a3.2 3.2 0 0 1 0 6.4M17.5 13.6c2.1.6 3.5 2.6 3.5 5.4"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
};

const ic = (n) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[n] || ''}</svg>`;

/* ---------- actions (event delegation: no inline handler names, which can collide with Element methods such as animate()) */
const ACT = {};

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const f = ACT[el.dataset.act];
  if (f) {
    e.preventDefault();
    // the creation panel stays open while its settings are used; its Generate, like any list row, closes the narrow-screen drawer
    if (el.closest('#col2') && (!el.closest('#cpwrap') || ['ggo', 'gprompt'].includes(el.dataset.act))) document.body.classList.remove('c2open');
    f(el, e);
  }
});

/* ---------- dialogs */
/* one active dismissal callback: retimeAsk registers here so every close path
   (backdrop, Cancel, replacement dialog) settles its promise exactly once */
let DLG_CB = null, DLG_FOCUS = null;
function dlg(html, opt) {
  if (DLG_CB) { const f = DLG_CB; DLG_CB = null; try { f('replaced'); } catch (e) {} }
  if (typeof document !== 'undefined' && !document.activeElement?.closest?.('#dlg')) DLG_FOCUS = document.activeElement;
  $('dlg').innerHTML = `<div class=dbox role=dialog aria-modal=true tabindex=-1>${html}</div>`;
  $('dlg').classList.add('on');
  if (opt && typeof opt.onDismiss === 'function') DLG_CB = opt.onDismiss;
  const box=$('dlg').querySelector?.('.dbox'),title=box?.querySelector('h1,h2,h3');
  if(title){title.id=title.id||'dlg-title';box.setAttribute('aria-labelledby',title.id)}
  box?.focus();
}
/* forget the dismissal without firing it: an explicit answer settles its own promise */
function dlgForget() { DLG_CB = null; }

function closeDlg(reason) {
  const f = DLG_CB; DLG_CB = null;
  $('dlg').classList.remove('on');
  $('dlg').innerHTML = '';
  if (DLG_FOCUS?.isConnected) {
    const folded = DLG_FOCUS.closest?.('details:not([open])');
    (folded?.querySelector('summary') || DLG_FOCUS).focus();
  }
  DLG_FOCUS = null;
  if (f) { try { f(reason === undefined ? 'dismiss' : reason); } catch (e) {} }
}

$('dlg').addEventListener('mousedown', (e) => {
  if (e.target.id === 'dlg') closeDlg();
});

ACT.dlgx = closeDlg;

let ASK = null;

function askText(title, value, cb, ok = 'Save') {
  ASK = cb;
  dlg(
    `<h2>${esc(title)}</h2><input type=text id=askv value="${esc(value)}"><div class=row style="justify-content:flex-end"><button class=btn data-act=dlgx>Cancel</button><button class="btn pri" data-act=askok>${ok}</button></div>`,
  );
  const i = $('askv');
  i.focus();
  i.select();
  i.onkeydown = (e) => {
    if (e.key === 'Enter') ACT.askok();
  };
}

ACT.askok = () => {
  const v = $('askv').value.trim();
  closeDlg();
  if (v && ASK) ASK(v);
};

/* a sticker's id (G103/S2) is its own control, never part of its name: hover shows it, one click copies it (P5 of the UI/UX spec) */
ACT.copyid = async (el) => {
  const v = el.dataset.v || '';
  if (!v) return;
  try {
    await navigator.clipboard.writeText(v);
    toast('Copied ' + v);
  } catch (e) {
    toast(v + ' (copy it from here)');
  }
};

function confirmDlg(msg, cb, ok = 'Delete') {
  ASK = cb;
  dlg(
    `<h2>${esc(msg)}</h2><div class=row style="justify-content:flex-end"><button class=btn data-act=dlgx>Cancel</button><button class="btn pri" data-act=cfok>${ok}</button></div>`,
  );
}

ACT.cfok = () => {
  closeDlg();
  if (ASK) ASK();
};

let PK = null;

async function pickPack(cb, title = 'Add to pack') {
  await loadLib();
  PK = cb;
  dlg(`<h2>${esc(title)}</h2>${LIB.packs.map((p) => `<div class=pl data-act=pk data-id=${p.id}><div class=cover>${coverMedia(p)}</div><div><b>${esc(p.name)}</b><br><small>${p.stickers.length} stickers</small></div></div>`).join('') || '<div class=mut>No packs yet: create one.</div>'}
  <div class=row><input type=text id=npn placeholder="New pack name"><button class="btn pri" data-act=pknew>Create</button></div><div class=row style="justify-content:flex-end"><button class=btn data-act=dlgx>Cancel</button></div>`);
}

ACT.pk = (el) => {
  closeDlg();
  PK(el.dataset.id);
};

ACT.pknew = async () => {
  const r = await post('/api/packs', { name: $('npn').value });
  if (!r.ok) return toast(r.j.error, 1);
  closeDlg();
  PK(r.j.id);
};

/* ---------- library cache + media helpers */
let LIB = { packs: [], recent: [], total: 0 },
  LIBQ = '',
  LIBTAB = 'recent';

/* ---------- router */
const SCREENS = [
    'home',
    'agent',
    'effects',
    'reactions',
    'super',
    'generate',
    'history',
    'library',
    'create',
    'editor',
    'pack',
    'export',
    'settings',
    'animate',
    'chat',
    'prepare',
  ],
  RENDER = {};

/* the rail of three: Home · Create · Library, and the avatar (Settings & health, Trash, Watch folders).
   Every old hash still opens; RAILOF says which item a screen lights. Create opens the tab used last (Chat or Studio). */
const RAIL = [
    ['home', 'home', 'Home'],
    ['agent', 'create', 'Create'],
    ['library', 'lib', 'Library'],
  ],
  RAILOF = {
    generate: 'agent',
    create: 'agent',
    effects: 'agent',
    reactions: 'agent',
    super: 'agent',
    editor: 'agent',
    export: 'agent',
    prepare: 'agent',
    pack: 'library',
    animate: 'library',
    chat: 'library',
    settings: 'me',
    history: 'me',
  };

const CREATE_TABS = ['agent', 'generate', 'create', 'effects', 'reactions', 'super'];

let route_ = 'home',
  PACK_ID = null,
  CTAB = 'agent',
  RME = false;


function rmeMenu() {
  return `<div id=rmenu role=menu aria-label="Settings">
  <button role=menuitem data-act=nav data-to=settings>${ic('settings')} Settings & health</button>
  <button role=menuitem data-act=rtrash>${ic('trash')} Trash</button><button role=menuitem data-act=nav data-to=history>${ic('folder')} Watch folders</button></div>`;
}

function drawRail() {
  $('c2tog').innerHTML = ic('panel');
  const on = RAILOF[route_] || route_;
  const notDefault = typeof GPATHS !== 'undefined' && GPATHS && GPATHS.default === false;  // UX-06: never hidden - a copied/task out/ must say so, not look like the real workspace
  $('rail').innerHTML =
    `<button class="logo ${route_ === 'home' ? 'on' : ''}" data-act=home title="${notDefault ? 'stickerloom home — Test workspace: ' + esc(GPATHS.out) : 'stickerloom home'}" aria-label="stickerloom home"><img src=/assets/brand/logo.svg alt="">${notDefault ? '<span class=ws-badge title="Not the real out/: a copy or a task workspace">Test</span>' : ''}</button>` +
    RAIL.map(
      ([k, i, l]) =>
        `<button class="rbtn ${on === k ? 'on' : ''}" data-act=${k === 'agent' ? 'railcreate' : 'nav'} data-to=${k === 'agent' ? CTAB : k}${on === k ? ' aria-current=page' : ''}>${ic(i)}<span>${l}</span></button>`,
    ).join('') +
    `<button class="rme ${on === 'me' ? 'on' : ''}" data-act=rme aria-haspopup=menu aria-expanded=${RME} title="Settings">Me</button>` +
    (RME ? rmeMenu() : '');
}

/* Create's tabs (D7): Chat | Studio over one project, and "+ New" for a photo or a particle effect; full-screen tools (editor, export, prepare) have none */
function drawCtabs() {
  const el = $('ctabs'),
    on = CREATE_TABS.includes(route_);
  document.body.classList.toggle('hasct', on);
  if (!on) {
    el.innerHTML = '';
    return;
  }
  const tab = (k, i, l) => `<button role=tab aria-selected=${route_ === k} data-act=nav data-to=${k}>${ic(i)}${l}</button>`;
  el.innerHTML = `<div class=ct-tabs role=tablist aria-label=Create>${tab('agent', 'chat', 'Chat')}${tab('generate', 'gen', 'Studio')}</div>
  <details class=ct-new><summary class="btn sm">${ic('plus')} New</summary><div class=ct-menu role=menu><button role=menuitem data-act=newphoto class="${route_ === 'create' ? 'on' : ''}">${ic('photo')} From a photo</button><button role=menuitem data-act=newvideo>${ic('film')} From a video</button><button role=menuitem data-act=nav data-to=reactions class="${route_ === 'reactions' ? 'on' : ''}">${ic('fx')} Reaction effects</button><button role=menuitem data-act=nav data-to=super class="${route_ === 'super' ? 'on' : ''}">${ic('fx')} Super reaction</button></div></details>
  ${route_ === 'create' || route_ === 'effects' || route_ === 'reactions' || route_ === 'super' ? `<span class=ct-here>${route_ === 'create' ? 'From a photo' : route_ === 'effects' ? 'Particle effects' : route_ === 'super' ? 'Super reaction' : 'Reaction effects'}</span>` : ''}`;
}

/* narrow screens: the second column is a drawer (studio.css, 'the shell on narrow screens') */
ACT.c2tog = () => document.body.classList.toggle('c2open');

ACT.nav = (el) => {
  if (el.closest('#rmenu')) rmeClose();
  location.hash = '#/' + (el.dataset.to === 'generate' ? 'studio' : el.dataset.to);
};

/* the rail's Create is always a NEW, empty start: the Studio without the last session's
   batches (it used to reopen the last batch and its green-screen sheet), the chat without the last conversation. The work is not lost: Earlier
   batches, the chat list and Open in Studio still reopen it, and the Chat | Studio tabs inside Create keep the current project. */
ACT.railcreate = (el) => {
  /* both sides start new, whichever opens */
  if (typeof SES !== 'undefined') {
    SES = { prompt: '', gens: [], off: [], pack: '' };
    if (typeof saveSes === 'function') saveSes();
    if (typeof glast !== 'undefined') glast = '';
  }
  if (typeof ACT.agnew === 'function') ACT.agnew(); /* a new chat: no session, the last one forgotten; it sets #/agent without a hashchange */
  const to = (el && el.dataset && el.dataset.to) || CTAB;
  if (to === 'generate') {
    if (location.hash === '#/studio') route();
    else location.hash = '#/studio';
  } else if (route_ !== 'agent' || location.hash !== '#/agent') route();
};

ACT.rme = () => {
  RME = !RME;
  drawRail();
  if (RME) {
    const b = document.querySelector('#rmenu [role=menuitem]');
    if (b) b.focus();
  }
};

ACT.thmtoggle = () => thmSet(thmIsDark() ? 'light' : 'dark');

const rmeClose = () => {
  if (RME) {
    RME = false;
    drawRail();
  }
};

document.addEventListener('click', (e) => {
  // drawRail replaces the clicked avatar; the original event path still contains the rail.
  if (RME && !e.composedPath().includes($('rail'))) rmeClose();
  const d = document.querySelector('#ctabs details[open]');
  if (d && (!e.target.closest('.ct-new') || e.target.closest('[role=menuitem]'))) d.open = false;
});

function dlgTab(e) {
  if(e.key!=='Tab'||!$('dlg').classList.contains('on'))return false;
  const box=$('dlg').querySelector('.dbox'),items=[...box.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex="0"]')].filter(el=>el.getClientRects().length);
  const first=items[0],last=items[items.length-1],active=document.activeElement;
  if(!first){e.preventDefault();return true}
  if(active===box||!box.contains(active)||e.shiftKey&&active===first||!e.shiftKey&&active===last){e.preventDefault();(e.shiftKey?last:first).focus();return true}
  return false;
}
function dlgEscape(e) {
  if (e.key !== 'Escape' || !$('dlg').classList.contains('on')) return false;
  closeDlg('escape'); e.preventDefault(); e.stopImmediatePropagation(); return true;
}
document.addEventListener('keydown', (e) => {
  if (dlgEscape(e) || dlgTab(e)) return;
  if (e.key === 'Escape' && RME) {
    rmeClose();
    const b = document.querySelector('#rail .rme');
    if (b) b.focus();
  }
});

/* the small ⋯ menus of the Studio (its toolbar, the project map) are <details>: a pick or a click outside closes them */
document.addEventListener('click', (e) => {
  document.querySelectorAll('details.pm-menu[open],details.gh-more[open]').forEach((d) => {
    if (!d.contains(e.target) || e.target.closest('[role=menu] button,[role=menu] a')) d.open = false;
  });
});

/* Trash lives at the end of Settings (trash.js): open Settings and bring the Trash card into view once it has drawn */
ACT.rtrash = () => {
  RME = false;
  let n = 0;
  const go = () => {
    const t = $('trx-box');
    if (t) return t.parentElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (++n < 40) setTimeout(go, 100);
  };
  if (route_ === 'settings') {
    drawRail();
    go();
  } else {
    location.hash = '#/settings';
    setTimeout(go, 100);
  }
};

function route() {
  ROUTELIFE.forEach(owner=>owner.leave());
  SEL.clear();
  const h = location.hash.replace(/^#\/?/, '') || 'home',
    ps = h.split('/'),
    n = ps[0] === 'studio' ? 'generate' : ps[0],
    a = ps.slice(1).join('/');
  route_ = SCREENS.includes(n) ? n : 'home';
  ROUTELIFE.forEach(owner=>owner.enter(route_));
  document.body.classList.remove('c2open');
  if (route_ === 'agent' || route_ === 'generate') CTAB = route_;
  RME = false;
  if (route_ !== 'generate' && typeof MD !== 'undefined' && MD && typeof ACT.gmclose === 'function')
    ACT.gmclose(); /* the sticker panel belongs to the Studio: leaving it closes the panel */
  SCREENS.forEach((s) => $('s-' + s).classList.toggle('on', s === route_));
  drawRail();
  drawCtabs();
  drawCol2();
  if (RENDER[route_]) RENDER[route_](a);
}

window.addEventListener('hashchange', route);

/* ---------- second column: packs (every row is a real pack) */
let C2Q = '';

/* the second column of a section: the list that belongs to it, in the same place on every screen. Studio and Create list the earlier batches (live.js); Settings and the full-screen tools (editor, export, prepare, animate) have no list */
const COL2 = ['agent', 'generate', 'library', 'pack', 'chat', 'create'];

function drawCol2() {
  const on = COL2.includes(route_), studio = route_ === 'generate';
  document.body.classList.toggle('col2', on);
  document.body.classList.toggle('studio-split', studio);
  $('c2tog').title = studio ? 'Creation settings' : 'Lists';
  $('c2tog').setAttribute('aria-label', studio ? 'Creation settings' : 'Lists');
  if (!studio) {
    if (typeof cpStash === 'function') cpStash(); /* keep the composer (and its draft) for the way back */
    if (typeof bdClose === 'function') bdClose(false);
  }
  if (!on) return;
  if (studio) return typeof studioCol === 'function' ? studioCol() : typeof histCol === 'function' ? histCol() : 0;
  if (route_ === 'create') return typeof histCol === 'function' ? histCol() : 0;
  if (route_ === 'chat') return chList();
  if (route_ === 'agent') return typeof agList === 'function' ? agList() : 0;
  const previousList = $('c2-packs'),
    scrollTop = previousList ? previousList.scrollTop : 0;
  const q = C2Q.trim().toLowerCase(),
    cur = route_ === 'pack' ? PACK_ID : null;
  $('col2').innerHTML = `<div class=c2h><h1>Packs</h1><button class=iconbtn data-act=newpack title="New pack">${ic('plus')}</button></div>
  <div class=c2s><input type=search id=c2q placeholder="Search packs…" value="${esc(C2Q)}"></div>
  <div class=c2l id=c2-packs>${
    packEntries(LIB.packs.filter((p) => !q || p.name.toLowerCase().includes(q))
      .sort((a, b) => (Number(b.created) || 0) - (Number(a.created) || 0)))
      .map((e) => {
        const row = (p, sub) =>
          `<div class="crow${sub ? ' sub' : ''} ${p.id === cur ? 'on' : ''}" data-act=openpack data-id=${p.id} draggable=true title="Click to open; drag onto another pack to put it there as a sub-pack"><div class=cv>${coverMedia(p)}</div><div><b>${esc(p.name)}</b><small>${(()=>{const r=(p.stickers||[]).filter(s=>(s.source||{}).particle_set).length,n=(p.stickers||[]).length-r;return n+' sticker'+(n===1?'':'s')+(r?' · '+r+' reaction'+(r===1?'':'s'):'')})()}</small></div><span class=meta>${p.stickers.some((s) => s.type === 'animated') ? 'animated' : ''}</span></div>`;
        if (e.pack) return row(e.pack);
        const open = PKFOLD.has(e.group) ? PKFOLD.get(e.group) : e.packs.some((p) => p.id === cur),
          f = e.packs[0];
        const sub = (p) =>
          row(p, true).replace(
            /<\/div>$/,
            `<button class="link pkmerge" data-act=pkmerge data-id=${p.id} data-into=${f.id} title="One pack: its animated stickers replace their stills in ${esc(f.name)}, the rest moves there, and this pack goes to the trash">Merge into parent</button>${p.parent ? `<button class="link pkunnest" data-act=pkunnest data-id=${p.id} title="Take this sub-pack out of the group: it stands on its own again, its stickers untouched">Take out</button>` : ''}</div>`,
          );
        return `<div class="crow pkgrp ${f.id === cur ? 'on' : ''}" data-act=openpack data-id=${f.id} draggable=true><div class=cv>${coverMedia(f)}</div><div><b>${esc(f.name)}</b><small>${e.packs.length} packs${e.group.startsWith('P-') ? '' : ` · group ${esc(e.group)}`}</small></div><button class=iconbtn data-act=pkgrp data-g=${esc(e.group)} title="${open ? 'Fold the group' : 'Show every pack of the group'}" aria-expanded=${open}>${ic('chev')}</button></div>${open ? e.packs.slice(1).map(sub).join('') : ''}`;
      })
      .join('') || '<div class=mut style="padding:14px">No packs yet.</div>'
  }</div>`;
  $('c2-packs').scrollTop = scrollTop;
  const i = $('c2q');
  i.oninput = (e) => {
    C2Q = e.target.value;
    const pos = e.target.selectionStart;
    $('c2-packs').scrollTop = 0;
    drawCol2();
    const n = $('c2q');
    n.focus();
    n.setSelectionRange(pos, pos);
  };
}
