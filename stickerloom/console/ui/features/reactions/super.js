/* Super reaction sandbox (Reactions 2.0): #/super. Two resizable screens, a desktop window with its menus and an iPhone; hold the
   reaction in either one's emoji box and small copies stream up, release and the whole screen plays: the dropped Lottie rising from the bottom, the
   screen lit with its colour, the big pop in the middle. The animation is made for THAT screen's shape (POST /api/particles/super with its
   width / height: the engine picks the nearest bucket, the player crops the rest with xMidYMid slice), so resizing a frame changes what it gets.
   Sources: the files in lottie, listed by name on every visit (GET /api/particles/super). Engine: engine/super_reaction.py.
   The library (flow/super_reactions.py): every file opens with its seeded random look until it is saved; Save / Save as (another version of the
   same file) / Remove (the saved version only), Shuffle (the next random look), presets to load on any file. Export uses what is saved. */
const SUP = {files: [], file: '', glow: 'auto', color: '#ff5a1f', c2: '#ffd23f', set: {}, start: {}, cache: {}, docs: {}, err: '', frame: 80,
  lib: {files: [], presets: []}, vid: '', seed: null, name: '', q: '', only: false, armed: '', busy: false};
const SUP_SETS = [['size', 'Size', 0.25, 3, 0.05], ['count', 'Amount', 4, 160, 1], ['rise', 'Rise', 0.2, 1.4, 0.05], ['float', 'Float', 0, 0.8, 0.05], ['sway', 'Sway', 0, 1.5, 0.05],
  ['spread', 'Spread', 10, 120, 1], ['glow_strength', 'Glow', 0, 1, 0.05]];
let supLib = null;
const supLottie = () => supLib || (supLib = typeof lsLib !== 'undefined' && lsLib ? lsLib : new Promise((ok, no) => {
  if (window.lottie) return ok(window.lottie);
  const s = document.createElement('script'); s.src = '/ui/vendor/lottie-5.12.2.min.js'; s.onload = () => ok(window.lottie); s.onerror = no; document.head.appendChild(s);
}));
const supClone = d => JSON.parse(JSON.stringify(d));

/* what the server is asked for one screen: the source, the screen's shape, the glow, the moved sliders */
function supBody(aspect) {
  const b = {file: SUP.file, aspect, glow: SUP.glow, settings: {...SUP.set, pop: SUP.set.pop ?? 1}};
  if (SUP.glow === 'solid') b.color = SUP.color;
  if (SUP.glow === 'gradient') b.colors = [SUP.color, SUP.c2];
  return b;
}
/* the editor's look as the library stores it: {settings, glow, color?, colors?, seed?} */
function supLook() {
  const l = {settings: {...SUP.set}, glow: SUP.glow};
  if (SUP.glow === 'solid') l.color = SUP.color;
  if (SUP.glow === 'gradient') l.colors = [SUP.color, SUP.c2];
  if (SUP.seed != null) l.seed = SUP.seed;
  return l;
}
function supApply(look) {
  SUP.set = {...((look && look.settings) || {})}; SUP.glow = (look && look.glow) || 'auto'; SUP.seed = look && look.seed != null ? look.seed : SUP.seed;
  if (look && look.color) SUP.color = look.color;
  if (look && look.colors) [SUP.color, SUP.c2] = look.colors;
}
const supRow = f => (SUP.lib.files || []).find(r => r.file === f);
const supVer = id => (SUP.lib.files || []).flatMap(r => r.versions).find(v => v.id === id);
const supSaved = () => { const v = SUP.vid && supVer(SUP.vid); return v ? v.look : (supRow(SUP.file) || {}).random };
const supDirty = () => { const a = supSaved(); if (!a) return false; const n = l => JSON.stringify({s: l.settings, g: l.glow, c: l.glow === 'solid' ? l.color : null, cs: l.glow === 'gradient' ? l.colors : null}); return n(a) !== n(supLook()) };
/* open a file (its first saved version, else its random look) or one version */
function supOpen(file, vid) {
  const row = supRow(file); if (!row) return;
  const v = vid ? row.versions.find(x => x.id === vid) : row.versions[0];
  SUP.file = file; SUP.vid = v ? v.id : ''; SUP.name = v ? v.name : row.name; SUP.seed = null; SUP.armed = '';
  supApply(v ? v.look : row.random);
  supStillClear(); supDraw(); supStill();
}

/* the nearest bucket name for a shape, from the listing (the server decides; this only labels the frame) */
function supBucket(aspect) {
  let best = '', d = 1e9;
  for (const [k, v] of Object.entries(SUP.buckets || {})) { const e = Math.abs(Math.log(v / aspect)); if (e < d) { d = e; best = k } }
  return best;
}
async function supFetch(aspect) {
  const b = supBody(aspect), key = JSON.stringify({...b, aspect: supBucket(aspect)});
  if (!SUP.cache[key]) SUP.cache[key] = post('/api/particles/super', b).then(r => {
    if (!r.ok) { delete SUP.cache[key]; throw new Error(r.j.error || 'Could not make the super reaction') }
    return r.j;
  });
  return SUP.cache[key];
}
/* the source itself (the hold stream and the button): the dropped file, as is */
async function supDoc(file) {
  if (!SUP.docs[file]) SUP.docs[file] = api('/api/particles/reaction-handpicked/' + encodeURIComponent(file)).then(r => r.ok ? r.j : null);
  return SUP.docs[file];
}

/* the two screens mimic a desktop chat app and a phone (a neutral, made-up chat list) and reuse the Chat screen's own pieces (.chat, .chh,
   .chm bubbles, the .chc "Type a message" bar): the reaction sits in the bar where the app has its quick action */
const SUP_CHATS = [['Sticker pack', 'New stickers are ready', '7:22 AM'], ['Notes (You)', 'Weekend plans', '7:22 AM'], ['Ava', 'Look at this one!', '7:19 AM'],
  ['Leo', 'Video', '25/09'], ['Mia', 'sketch.png', '24/09'], ['Sam', 'Video', '28/08']];
function supConvo(phone) {
  return `<div class="chat${phone ? ' mobile' : ''} sup-chat"${phone ? '' : ' data-supstage'}>
    <div class=chh>${phone ? `<span class=sup-back>${ic('back')}</span>` : ''}<div class="cv chav">A</div><div style="flex:1"><b>Ava</b><small>online</small></div>
     <span class=sup-ico>${ic('search')}</span></div>
    <div class=chm>
     <div class="cm cin"><div class=bub>Did you see the new stickers?<span class=tm>01:22 PM</span></div></div>
     <div class="cm cout"><div class=bub>Hold the reaction in the bar 👇<span class=tm>01:23 PM</span></div></div>
    </div>
    <div class=chc><span class=sup-ico>${ic('sticker')}</span><span class=sup-input>Type a message</span>
     <span class=sup-ico>${ic('emoji')}</span><button class=sup-react aria-label="Hold for a super reaction" title="Hold, then release"><span class=sup-icon></span></button></div>
    ${phone ? '' : '<div class=sup-fx aria-hidden=true></div>'}
   </div>`;
}
/* where a screen's super reaction plays: the chat area on desktop (the rail and the Chats list stay above it, like the app's other panels and
   modals), the whole phone screen on the phone. Its width / height picks the bucket. */
const supStageOf = dev => dev.querySelector('[data-supstage]');
const supAspect = st => st.clientWidth / Math.max(1, st.clientHeight);
function supFrame(kind) {
  const phone = kind === 'phone';
  if (phone) return `<div class="sup-dev sup-phone" data-sup=phone>
   <div class=sup-screen data-supstage>
    <div class=sup-status><b>1:47</b><span class=sup-notch></span><span class=sup-sig><i></i><i></i><i></i><i></i></span></div>
    ${supConvo(true)}
    <div class=sup-fx aria-hidden=true></div>
   </div>
   <div class=sup-size></div>
  </div>`;
  return `<div class="sup-dev sup-desktop" data-sup=desktop>
   <div class=sup-bar><span class="cv chav sup-logo">S</span><b>Chat</b><span class=sp></span><i>–</i><i>▢</i><i>✕</i></div>
   <div class=sup-screen>
    <nav class=sup-rail>${['chat', 'emoji', 'star', 'lib', 'help'].map((n, i) => `<span class="sup-ico${i === 0 ? ' on' : ''}">${ic(n)}</span>`).join('')}</nav>
    <aside class=sup-menu><div class=sup-mh><b>Chats</b><span class=sup-ico>${ic('edit')}</span></div>
     <div class=sup-search>${ic('search')}<span>Search or start new chat</span></div>
     ${SUP_CHATS.map(([n, m, t], i) => `<div class="sup-row${i === 2 ? ' on' : ''}"><span class="cv chav">${esc(n[0])}</span><span class=sup-rt><b>${esc(n)}</b><small>${esc(m)}</small></span><small class=sup-t>${esc(t)}</small></div>`).join('')}
    </aside>
    ${supConvo(false)}
   </div>
   <div class=sup-size></div>
  </div>`;
}

/* every host on the page: the #/super screen, or the Super Emojis tab of Reaction effects and Library > Reactions */
function supDraw() {
  document.querySelectorAll('.sup-root').forEach(supDrawIn);
}
function supDrawIn(el) {
  const glow = ['auto', 'solid', 'gradient', 'none'].map(g => `<button class="btn sm${SUP.glow === g ? ' pri' : ''}" data-supglow=${g}>${g[0].toUpperCase() + g.slice(1)}</button>`).join('');
  const sliders = SUP_SETS.map(([k, label, lo, hi, st]) => {
    const v = SUP.set[k] ?? SUP.start[k] ?? lo;
    return `<label class=sup-sl><span>${label} <em>${v}</em></span><input type=range min=${lo} max=${hi} step=${st} value="${v}" data-supset=${k}></label>`;
  }).join('') + `<label class="sup-sl sup-frame"><span>Animation frame <em>${SUP.frame}%</em></span><input type=range min=0 max=100 step=1 value="${SUP.frame}" data-supframe></label>`;
  const q = SUP.q.trim().toLowerCase();
  const cards = (SUP.lib.files || []).filter(r => (!SUP.only || r.versions.length) && (!q || r.name.toLowerCase().includes(q) || r.versions.some(v => v.name.toLowerCase().includes(q))))
    .map(r => {
      const items = r.versions.length ? r.versions.map(v => ({vid: v.id, name: v.name, saved: true})) : [{vid: '', name: r.name, saved: false}];
      return `<div class=sup-card><div class=sup-thumb data-supthumb="${esc(r.file)}"></div><div class=sup-cmain><small class=sup-mut>${esc(r.name)}</small>${items.map(it =>
        `<button class="sup-item${r.file === SUP.file && it.vid === SUP.vid ? ' on' : ''}" data-supopen="${esc(r.file)}" data-supv="${esc(it.vid)}">${esc(it.name)}<span class="sup-chip${it.saved ? ' saved' : ''}">${it.saved ? 'saved' : 'random'}</span></button>`).join('')}</div></div>`;
    }).join('') || `<p class=sup-mut>${(SUP.lib.files || []).length ? 'No match.' : 'No files in lottie.'}</p>`;
  const nSaved = (SUP.lib.files || []).filter(r => r.versions.length).length, nAll = (SUP.lib.files || []).length;
  const presets = (SUP.lib.presets || []).map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');
  const dirty = supDirty();
  const lib = `<details class=sup-lib open><summary><b>Library</b> <span class=sup-mut>${nAll} files · ${nSaved} saved</span></summary>
    <div class=sup-libbar><input type=search id=sup-q placeholder="Search…" value="${esc(SUP.q)}" aria-label="Search the super reactions"><label class=sup-only><input type=checkbox id=sup-only${SUP.only ? ' checked' : ''}> Saved only</label></div>
    <div class=sup-cards>${cards}</div></details>
   <div class=sup-edit>
    <label class=sup-src>Name <input type=text id=sup-name value="${esc(SUP.name)}" maxlength=60></label>
    <span class="sup-chip${SUP.vid ? ' saved' : ''}">${SUP.vid ? 'saved ' + esc(SUP.vid) : 'random look'}</span>${dirty ? '<span class=sup-mut>· unsaved changes</span>' : ''}
    <button class="btn sm pri" data-supsave ${SUP.file && !SUP.busy ? '' : 'disabled'}>Save</button>
    <button class="btn sm" data-supsaveas ${SUP.file && !SUP.busy ? '' : 'disabled'} title="Another version of this file">Save as</button>
    <button class="btn sm" data-supremove ${SUP.vid && !SUP.busy ? '' : 'disabled'} title="Remove this saved version (the Lottie file stays)">${SUP.armed === SUP.vid && SUP.vid ? 'Remove? Click again' : 'Remove'}</button>
    <button class="btn sm" data-supshuffle ${SUP.file ? '' : 'disabled'} title="Another random look">Shuffle</button>
    <span class=sup-pre><select id=sup-preset aria-label="Load a preset"><option value="">Load preset…</option>${presets}</select>
     <input type=text id=sup-pname placeholder="Preset name" maxlength=60><button class="btn sm" data-suppsave title="Save these sliders as a preset">Save as preset</button>
     ${presets ? `<button class="btn sm" data-suppdel title="Remove the preset chosen in Load preset">Remove preset</button>` : ''}</span>
   </div>`;
  el.innerHTML = `<div class=sup-wrap>
   <div class=sup-head>${el.dataset.suptab ? '' : '<h1>Super reaction</h1>'}<span class=sup-mut>Pick a file or a saved version. Hold the reaction in a screen, then release. Drag a screen's corner to resize it. Moving a slider shows one frame (Animation frame) instead of playing.</span></div>
   ${lib}
   <div class=sup-tools>
    <span class=sup-glow>Light ${glow}
     ${SUP.glow === 'solid' || SUP.glow === 'gradient' ? `<input type=color id=sup-c1 value="${SUP.color}" title="${SUP.glow === 'gradient' ? 'Bottom' : 'Colour'}">` : ''}
     ${SUP.glow === 'gradient' ? `<input type=color id=sup-c2 value="${SUP.c2}" title=Top>` : ''}
     <span class=sup-auto id=sup-auto></span></span>
    <button class="btn sm" data-supreset>Reset</button>
    <span class=sup-exp><button class="btn sm" data-supdl=desktop title="The super reaction the desktop screen plays, for its shape">${ic('download')} JSON · desktop</button><button class="btn sm" data-supdl=phone title="The super reaction the phone plays, for its shape">${ic('download')} JSON · phone</button><button class="btn sm" data-supzip title="Every saved version (an unsaved file: its random look), for every screen shape, plus manifest.json">${ic('download')} Export all (.zip)</button></span>
   </div>
   <div class=sup-sliders>${sliders}</div>
   ${SUP.err ? `<p class=sup-err>${esc(SUP.err)}</p>` : ''}
   <div class=sup-stage>${supFrame('desktop')}${supFrame('phone')}</div>
  </div>`;
  supIcons();
  el.querySelectorAll('.sup-dev').forEach(supWatch);
  supThumbs(el);
}
/* a card's picture: one still frame of its source, drawn only when the card scrolls into view (50 animations at once would crawl) */
function supThumbs(el) {
  const boxes = [...el.querySelectorAll('[data-supthumb]')];
  const draw = async box => {
    const [L, doc] = await Promise.all([supLottie(), supDoc(box.dataset.supthumb)]);
    if (!doc || !box.isConnected) return;
    const a = L.loadAnimation({container: box, renderer: 'svg', loop: false, autoplay: false, animationData: supClone(doc)});
    const mid = () => a.goToAndStop(Math.floor((a.totalFrames || 1) / 2), true);
    a.addEventListener('DOMLoaded', mid); if (a.isLoaded) mid();
  };
  if (!window.IntersectionObserver) return boxes.forEach(draw);
  const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { io.unobserve(e.target); draw(e.target) } }));
  boxes.forEach(b => io.observe(b));
}

/* the emoji box's button shows the source itself, looping */
async function supIcons() {
  if (!SUP.file) return;
  const [L, doc] = await Promise.all([supLottie(), supDoc(SUP.file)]);
  document.querySelectorAll('.sup-root .sup-icon').forEach(box => {
    box.innerHTML = ''; if (!doc) return;
    L.loadAnimation({container: box, renderer: 'svg', loop: true, autoplay: true, animationData: supClone(doc)});
  });
}
/* the frame's size readout: px and the bucket it will get */
function supWatch(dev) {
  const scr = supStageOf(dev), out = dev.querySelector('.sup-size');
  const show = () => { const w = scr.clientWidth, h = scr.clientHeight; if (w && h) out.textContent = `${w} × ${h} · ${supBucket(w / h)}` };
  show();
  if (window.ResizeObserver) new ResizeObserver(show).observe(scr);
}

/* one frame instead of the whole animation (like the emoji pack's first-frame preview): moving a slider shows, in every screen, the
   super reaction made for that screen's shape, stopped at Animation frame. A new frame of the same animation is instant (goToAndStop);
   other settings ask the server again (cached by settings and shape), a little after the slider stops moving. Hold / release clears it. */
const SUPSTILL = {timer: 0, seq: 0};
function supStillClear() { clearTimeout(SUPSTILL.timer); SUPSTILL.seq++; document.querySelectorAll('.sup-root .sup-still').forEach(b => { if (b._anim) b._anim.destroy(); b.remove() }) }
function supStillFrame(box) { const a = box._anim; if (a) a.goToAndStop(Math.round((a.totalFrames - 1) * SUP.frame / 100), true) }
function supStill(now) {
  clearTimeout(SUPSTILL.timer);
  if (now) { document.querySelectorAll('.sup-root .sup-still').forEach(supStillFrame); return }     // the frame slider: same animation, another frame
  SUPSTILL.timer = setTimeout(async () => {
    const seq = ++SUPSTILL.seq;
    if (!SUP.file) return;
    const L = await supLottie();
    await Promise.all([...document.querySelectorAll('.sup-root .sup-dev')].map(async dev => {
      const scr = supStageOf(dev), fx = scr.querySelector('.sup-fx');
      let r;
      try { r = await supFetch(supAspect(scr)) } catch (e) { return toast(e.message, 1) }
      if (seq !== SUPSTILL.seq) return;                                              // a newer move won
      let box = fx.querySelector('.sup-still');
      if (box && box._doc === r.lottie) return supStillFrame(box);
      if (box) { box._anim && box._anim.destroy(); box.remove() }
      box = document.createElement('div'); box.className = 'sup-full sup-still'; fx.appendChild(box);
      box._doc = r.lottie;
      box._anim = L.loadAnimation({container: box, renderer: 'svg', loop: false, autoplay: false, animationData: supClone(r.lottie),
        rendererSettings: {preserveAspectRatio: 'xMidYMid slice'}});
      box._anim.addEventListener('DOMLoaded', () => supStillFrame(box));
      if (box._anim.isLoaded) supStillFrame(box);
      supAuto(r);
    }));
  }, 140);
}
const supAuto = r => { const auto = $('sup-auto'); if (auto && r.color) auto.innerHTML = `<i style="background:${r.color}"></i>${SUP.glow === 'auto' ? 'auto ' : ''}${r.color}` };

/* hold: small copies of the source rise from the button, faster the longer it is held */
function supStream(btn) {
  const fx = btn.closest('[data-supstage]').querySelector('.sup-fx'), t0 = performance.now();
  let alive = true, timer = 0;
  const spawn = async () => {
    if (!alive) return;
    const [L, doc] = await Promise.all([supLottie(), supDoc(SUP.file)]);
    if (!doc || !alive) return;
    const sr = fx.getBoundingClientRect(), br = btn.getBoundingClientRect(), d = document.createElement('div');
    const size = 26 + Math.random() * 18;
    d.className = 'sup-bit';
    d.style.cssText = `left:${br.left - sr.left + br.width / 2 - size / 2}px;top:${br.top - sr.top - size / 2}px;width:${size}px;height:${size}px;--dx:${(Math.random() - .5) * 90}px;--dy:${-(120 + Math.random() * Math.min(260, sr.height * .45))}px`;
    fx.appendChild(d);
    const a = L.loadAnimation({container: d, renderer: 'svg', loop: false, autoplay: true, animationData: supClone(doc)});
    setTimeout(() => { a.destroy(); d.remove() }, 1300);
    const held = performance.now() - t0;
    timer = setTimeout(spawn, Math.max(70, 220 - held / 8));
  };
  spawn();
  return () => { alive = false; clearTimeout(timer) };
}

/* release: the whole screen plays, made for this screen's shape */
async function supPlay(dev) {
  const scr = supStageOf(dev), fx = scr.querySelector('.sup-fx');
  const aspect = supAspect(scr);
  try {
    const [L, r] = await Promise.all([supLottie(), supFetch(aspect)]);
    const box = document.createElement('div');
    box.className = 'sup-full';
    fx.appendChild(box);
    const a = L.loadAnimation({container: box, renderer: 'svg', loop: false, autoplay: true, animationData: supClone(r.lottie),
      rendererSettings: {preserveAspectRatio: 'xMidYMid slice'}});
    a.addEventListener('complete', () => { a.destroy(); box.remove() });
    supAuto(r);
  } catch (e) { toast(e.message || 'Could not play', 1) }
}

/* pointer handling on the emoji box buttons: a short tap is a bounce, a hold streams and releases the super reaction */
document.addEventListener('pointerdown', e => {
  const btn = e.target.closest && e.target.closest('.sup-root .sup-react');
  if (!btn || !SUP.file) return;
  e.preventDefault();
  supStillClear();
  const dev = btn.closest('.sup-dev');
  supFetch(supAspect(supStageOf(dev))).catch(() => 0);      // ready by the release
  btn.classList.add('held');
  const stop = supStream(btn), t0 = performance.now();
  btn.setPointerCapture && btn.setPointerCapture(e.pointerId);
  const end = ev => {
    btn.removeEventListener('pointerup', end); btn.removeEventListener('pointercancel', end);
    stop(); btn.classList.remove('held');
    const inside = ev.type === 'pointerup' && document.elementFromPoint(ev.clientX, ev.clientY) && btn.contains(document.elementFromPoint(ev.clientX, ev.clientY));
    if (inside && performance.now() - t0 >= 250) supPlay(dev);
  };
  btn.addEventListener('pointerup', end); btn.addEventListener('pointercancel', end);
});
document.addEventListener('contextmenu', e => { if (e.target.closest && e.target.closest('.sup-root .sup-react')) e.preventDefault() });
const supSave = (blob, name) => { const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000) };
const supName = () => String(SUP.file || '').replace(/\.json$/i, '');
async function supDownload(kind, root) {
  const dev = root.querySelector(`.sup-dev[data-sup=${kind}]`);
  if (!dev || !SUP.file) return;
  try {
    const r = await supFetch(supAspect(supStageOf(dev)));
    supSave(new Blob([JSON.stringify(r.lottie)], {type: 'application/json'}), `${supName()}-superReaction-${r.bucket}.json`);
  } catch (e) { toast(e.message || 'Could not make the JSON', 1) }
}
async function supZip(btn) {
  const b = {};
  btn.disabled = true;
  try {
    const r = await fetch('/api/particles/super/export.zip', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
    if (!r.ok) { let j = {}; try { j = await r.json() } catch (x) {} return toast(j.error || 'Could not export', 1) }
    supSave(await r.blob(), 'superReactions.zip');
  } finally { btn.disabled = false }
}
async function supCall(url, body, ok) {
  SUP.busy = true; supDraw();
  try {
    const r = await post(url, body);
    if (!r.ok) return toast(r.j.error || 'Could not save', 1);
    await supLoad(); if (ok) ok(r.j);
  } finally { SUP.busy = false; supDraw(); supStill() }
}
document.addEventListener('click', e => {
  const on = sel => e.target.closest && e.target.closest('.sup-root ' + sel);
  const op = on('[data-supopen]');
  if (op) return supOpen(op.dataset.supopen, op.dataset.supv);
  if (on('[data-supsave]')) {
    const name = SUP.name;
    return SUP.vid ? supCall('/api/particles/super/versions/' + SUP.vid, {name, look: supLook()}, () => toast('Saved'))
      : supCall('/api/particles/super/versions', {file: SUP.file, name, look: supLook()}, j => { SUP.vid = j.id; toast('Saved ' + j.id) });
  }
  if (on('[data-supsaveas]')) {
    const row = supRow(SUP.file) || {versions: []}, base = SUP.name || row.name || supName();
    const name = row.versions.some(v => v.name === base) ? `${base} ${row.versions.length + 1}` : base;
    return supCall('/api/particles/super/versions', {file: SUP.file, name, look: supLook()}, j => { SUP.vid = j.id; SUP.name = j.name; toast('Saved as ' + j.name) });
  }
  if (on('[data-supremove]')) {
    if (SUP.armed !== SUP.vid) { SUP.armed = SUP.vid; return supDraw() }
    const vid = SUP.vid; SUP.armed = '';
    return supCall(`/api/particles/super/versions/${vid}/delete`, {}, () => { toast('Removed ' + vid + ' (the Lottie file stays)'); supOpen(SUP.file) });
  }
  if (on('[data-supshuffle]')) {
    const seed = (SUP.seed != null ? SUP.seed : 0) + 1;
    return post('/api/particles/super/random', {file: SUP.file, seed}).then(r => { if (!r.ok) return toast(r.j.error || 'Could not shuffle', 1); supApply(r.j); SUP.seed = r.j.seed; supDraw(); supStill() });
  }
  if (on('[data-suppsave]')) {
    const name = (document.getElementById('sup-pname') || {}).value || '';
    return supCall('/api/particles/super/presets', {name, look: supLook()}, j => toast('Preset ' + j.name + ' saved'));
  }
  if (on('[data-suppdel]')) {
    const pid = (document.getElementById('sup-preset') || {}).value;
    if (!pid) return toast('Choose the preset in Load preset first', 1);
    return supCall(`/api/particles/super/presets/${pid}/delete`, {}, () => toast('Preset removed'));
  }
  const dl = e.target.closest && e.target.closest('.sup-root [data-supdl]');
  if (dl) return supDownload(dl.dataset.supdl, dl.closest('.sup-root'));
  const zp = e.target.closest && e.target.closest('.sup-root [data-supzip]');
  if (zp) return supZip(zp);
  const g = e.target.closest && e.target.closest('.sup-root [data-supglow]');
  if (g) { SUP.glow = g.dataset.supglow; supDraw(); return supStill() }
  if (e.target.closest && e.target.closest('.sup-root [data-supreset]')) { SUP.frame = 80; SUP.armed = ''; supApply(supSaved()); supStillClear(); supDraw(); return supStill() }   /* back to the saved look (or the random one) */
});
document.addEventListener('input', e => {
  const t = e.target;
  if (!t.closest || !t.closest('.sup-root')) return;
  if (t.dataset && t.dataset.supset) { SUP.set[t.dataset.supset] = +t.value; const em = t.parentElement.querySelector('em'); if (em) em.textContent = t.value; return supStill() }
  if (t.dataset && t.dataset.supframe !== undefined) {
    SUP.frame = +t.value; const em = t.parentElement.querySelector('em'); if (em) em.textContent = t.value + '%';
    return document.querySelector('.sup-root .sup-still') ? supStill(true) : supStill();
  }
  if (t.id === 'sup-name') { SUP.name = t.value; return }
  if (t.id === 'sup-q') { SUP.q = t.value; const p = t.selectionStart; supDraw(); const i = document.getElementById('sup-q'); if (i) { i.focus(); i.setSelectionRange(p, p) } return }
  if (t.id === 'sup-c1') { SUP.color = t.value; supStill() }
  if (t.id === 'sup-c2') { SUP.c2 = t.value; supStill() }
});
document.addEventListener('change', e => {
  if (e.target.id === 'sup-only') { SUP.only = e.target.checked; return supDraw() }
  if (e.target.id === 'sup-preset' && e.target.value) {          /* Load preset: its sliders (and its Light, as saved) on this file; Save keeps them */
    const p = (SUP.lib.presets || []).find(x => x.id === e.target.value);
    if (p) { const seed = SUP.seed; supApply(p.look); SUP.seed = seed; toast('Loaded ' + p.name + ': Save to keep it'); supDraw(); supStill() }
  }
});

/* the folder is read on every visit: a file dropped in lottie appears by its name */
async function supLoad() {
  const r = await api('/api/particles/super');
  if (!r.ok) { SUP.err = r.j.error || 'Could not list the Lottie files'; return }
  SUP.err = '';
  SUP.files = r.j.files || []; SUP.buckets = r.j.buckets || {}; SUP.start = r.j.start || {};
  SUP.lib = r.j.library || {files: [], presets: []};
  if (!SUP.files.some(f => f.file === SUP.file)) {                 /* first visit (or the file left the folder): the first file, its look */
    const first = (SUP.lib.files || [])[0];
    SUP.file = first ? first.file : ''; SUP.vid = ''; SUP.name = first ? first.name : '';
    if (first) { const v = first.versions[0]; SUP.vid = v ? v.id : ''; SUP.name = v ? v.name : first.name; supApply(v ? v.look : first.random) }
  } else if (SUP.vid && !supVer(SUP.vid)) SUP.vid = '';
}
/* a tab host (Reaction effects, Library > Reactions): drawn now from what is known, refreshed once the folder is read */
let supLoading = null;
function supMount() {
  supDraw();
  if (!supLoading) supLoading = supLoad().then(supDraw).finally(() => { supLoading = null });
}
const supTabHtml = () => '<div class=sup-root data-suptab=1></div>';

RENDER.super = async () => {
  const el = $('s-super');
  if (el && !el.querySelector('.sup-root')) el.innerHTML = '<div class=sup-root></div>';
  await supLoad();
  supDraw();
};
