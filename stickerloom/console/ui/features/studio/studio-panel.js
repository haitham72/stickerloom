/* ---------- one sticker, larger */
function gmodal() {
  if (!MD) return;
  const g = GM.get(MD.g);
  if (!g) {
    MD = null;
    return;
  }
  const t = g.stickers[MD.i - 1],
    base = `/out/${g.generation_id}/`,
    ap = animPhase(g),
    v = t.edited_at || t.rendered_at || 0;
  const still = t.png
    ? `<img src="${base + t.png}?e=${v}">`
    : `<div class=mut style="padding:12px">${esc(t.status)}${t.reason ? ': ' + esc(t.reason) : ''}</div>`;
  const vid =
    t.webm && t.anim_status === 'READY'
      ? `<video src="${base + t.webm}?v=${t.anim_at || 0}" autoplay loop muted playsinline></video>`
      : `<div class=mut style="padding:12px">${t.anim_status === 'NOT_REQUESTED' ? 'Not animated yet' : esc(t.anim_status) + (t.anim_reason ? ': ' + esc(t.anim_reason) : '')}</div>`;
  const rows = (cs) =>
    (cs || [])
      .filter((r) => MD.all || !r.ok)
      .map(
        (r) =>
          `<div class=chk><i class=cdot style="--cc:${r.ok ? '#22c55e' : CAT[CATOF[r.name] || 'bad'][0]}"></i><span class="${r.ok ? 'p' : r.severity === 'WARN' ? 'w' : 'f'}">${r.ok ? '✓' : r.severity === 'WARN' ? '!' : '✗'} ${esc(r.name)}</span> <span class=mut>${esc(r.detail || '')}</span></div>`,
      )
      .join('');
  const mk = mark(t, ap ? 'anim' : 'still', g),
    m = t.metrics || {},
    am = t.anim_metrics || {},
    meas = [
      ['scale', m.scale],
      ['scale mode', m.scale_mode],
      ['key threshold', m.threshold],
      ['still KB', m.kb],
      ['holes', m.holes],
      ['frames', am.frames_out],
      ['fps', am.fps],
      ['animation KB', am.kb],
      ['crf', am.crf],
      ['loop seam', am.loop_seam !== undefined ? am.loop_seam + ' / ' + am.loop_limit : undefined],
    ].filter((x) => x[1] !== undefined && x[1] !== null && x[1] !== ''),
    off = ap && t.anim_status === 'READY' ? ['REJECTED', 'BLOCKED'].includes(t.review.anim) : t.review.still === 'REJECTED',
    stg = ap ? 'anim' : 'still',
    op = cellOp(g, t, stg),
    act =
      op.op === 'none'
        ? ''
        : `<button class="btn ${['allow', 'include'].includes(op.op) ? 'pri' : ''}" ${cellAct(g, t, stg)}>${cellVerb(op, t)}</button>`,
    allowAct = op.op === 'none' && op.why ? `<span class=mut>${esc(op.why)}</span>` : '',
    path = (t.history || [])
      .slice()
      .reverse()
      .map(
        (h) =>
          `<div class=hrow><span class=mut>${new Date(h.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span> <b>${esc(h.stage)}</b> ${esc(h.actor)} ${esc(h.decision)}${h.reason ? ` <span class=mut>· ${esc(h.reason)}</span>` : ''}</div>`,
      )
      .join('');
  const bad = (t.report || []).concat(t.anim_report || []).some((r) => !r.ok);
  const gmFresh = !$('modal').classList.contains('side');
  $('modal').innerHTML =
    `<div class="mbox gm-panel${gmFresh ? ' gm-new' : ''}" role=dialog aria-label="Sticker ${g.generation_id}/S${t.index}"><div class=mrow><button class="btn nav" data-act=gstep data-d=-1 aria-label="Previous sticker">‹</button>
   <div style="flex:1"><b>${esc(t.emoji)} ${esc(t.key.replace(/_/g, ' '))}</b><div class=mut>${esc(t.tags.join(' · '))}</div></div>
   <button class=idtag data-act=copyid data-v="${g.generation_id}/S${t.index}" title="${g.generation_id}/S${t.index} · click to copy the id">${g.generation_id}/S${t.index}</button>
    ${t.status === 'READY' ? `<button class=btn data-act=gedit title="${t.anim_status === 'READY' ? 'Text, emoji and trim over the animation; one Save updates the image and the animation' : 'Add text or emoji'}">${ic('edit')} Edit${t.anim_status === 'READY' ? ' (image + animation)' : ''}</button><button class=btn data-act=greplace data-g=${g.number} data-i=${t.index} title="Replace with a file you edited outside the app (same S#, re-checked, undoable)">Replace file</button><button class=btn data-act=grepundo data-g=${g.number} data-i=${t.index} title="Take back the last replace">Take back</button>` : ''}<button class="btn nav" data-act=gstep data-d=1>›</button><button class=btn data-act=gmclose>✕</button></div>
   ${mk ? `<div style="margin:6px 0 2px">${mk.issues.map((i) => `<div class=giss style="--cc:${CAT[i.cat][0]}"><i></i>${esc(CAT[i.cat][1])}: ${esc(i.text)}</div>`).join('')}</div>` : ''}
   <div class=mpanes><div class=pane><div class=mut>Sticker</div><div class="box bg-${bg}">${still}</div></div><div class=pane><div class=mut>Animation</div><div class="box bg-${bg}">${vid}</div></div></div>
   ${typeof edgeControlsHtml === 'function' && t.status === 'READY' ? `<div class="lv-edge lv-edge-m"><b>Edge</b>${edgeControlsHtml()}</div>` : ''}
   ${act || allowAct ? `<div class=row style="margin:8px 0 2px">${act}${allowAct}</div>` : ''}
   ${(() => {
     const tab = MD.tab || 'checks',
       tb = (k, l) => `<button type=button role=tab data-act=gmtab data-t=${k} aria-selected=${tab === k}>${l}</button>`;
     const body =
       tab === 'history'
         ? path || '<div class=mut>no decisions yet</div>'
         : tab === 'meas'
           ? meas.length
             ? `<div class="kv vkv">${meas.map((x) => `<span>${x[0]}</span><span>${esc(String(x[1]))}</span>`).join('')}</div>`
             : '<div class=mut>No measurements yet</div>'
           : `${rows(t.report)}${rows(t.anim_report) || ''}${rows(t.report) || rows(t.anim_report) ? '' : `<div class=mut>${(t.report || []).length || (t.anim_report || []).length ? 'Every check passed.' : 'nothing yet'}</div>`}<button class=link data-act=gmall>${MD.all ? 'Show only problems' : 'Show every check'}</button>`;
     return `<div class=gm-tabs role=tablist aria-label="About this sticker">${tb('checks', `Checks${bad ? ' <i class=gm-dot></i>' : ''}`)}${tb('history', 'History')}${tb('meas', 'Measurements')}</div><div class=gm-pane role=tabpanel>${body}</div>`;
   })()}
   <div class=mut style="margin-top:8px">${esc(t.prompt)}</div><div class=mut>← → to browse, Esc to close</div></div>`;
  /* the sticker panel (redesign phase 6): the same content, docked on the right; the Studio stays usable beside it, a click on another tile switches it */
  $('modal').classList.add('on', 'side');
  document.body.classList.add('gmside');
  $('modal').onclick = (e) => {
    if (e.target.id === 'modal') ACT.gmclose();
  };
  if (typeof egSync === 'function') {
    egSync();
    applyEdgePreview();
  }
}

ACT.gmall = () => {
  MD.all = !MD.all;
  gmodal();
};

ACT.gmtab = (el) => {
  MD.tab = el.dataset.t;
  gmodal();
};

ACT.gopen = (el) => {
  MD = { g: +el.dataset.g, i: +el.dataset.i, all: !!(MD && MD.all), tab: (MD && MD.tab) || 'checks' };
  if (typeof egPick === 'function') egPick(MD.g, MD.i);
  gmodal();
};

ACT.gmclose = () => {
  MD = null;
  $('modal').classList.remove('on', 'side');
  document.body.classList.remove('gmside');
};

ACT.gstep = (el) => {
  if (!MD) return;
  const g = GM.get(MD.g),
    n = g.stickers.length;
  MD.i = ((MD.i - 1 + +el.dataset.d + n) % n) + 1;
  gmodal();
};

ACT.gedit = () => {
  const g = GM.get(MD.g),
    t = g.stickers[MD.i - 1];
  ACT.gmclose();
  return studioEditSticker(g.number, t.index);
  Ed.openImage(`/out/${g.generation_id}/${t.png}?e=${t.edited_at || 0}`, {
    back: { gen: g.number, index: t.index },
    outlined: g.outline_px > 0,
    name: t.name,
    emoji: t.emoji,
  });
};

document.addEventListener('keydown', (e) => {
  if (!MD) return;
  if (e.key === 'Escape') ACT.gmclose();
  if (e.key === 'ArrowRight') ACT.gstep({ dataset: { d: 1 } });
  if (e.key === 'ArrowLeft') ACT.gstep({ dataset: { d: -1 } });
});

/* the sheet's cells are SVG rects, a preset's preview stage a div[role=button]: Enter or Space on a focused one presses it like a click (neither answers those keys by itself) */
document.addEventListener('keydown', (e) => {
  const t = e.target, tag = String((t && t.tagName) || '').toLowerCase();
  if ((e.key === 'Enter' || e.key === ' ') && t && t.dataset && t.dataset.act && (tag === 'rect' || (tag === 'div' && t.getAttribute('role') === 'button'))) {
    e.preventDefault();
    const f = ACT[t.dataset.act];
    if (f) f(t, e);
  }
});

/* ---------- Edit a sticker from the Studio, a pack or the library
   A sticker WITH an animation is edited as layers (text, emoji, stickers, with timing) over its ORIGINAL animation in the video editor; Save to sticker
   exports the layers to the animation AND the image together, and the pack copies follow. A sticker without one is edited in the image editor.
   The edit lives in the Studio (a project); the generator's files stay in source/orig. */
async function studioEditAnim(gnum, index) {
  const r = await postWait(`/api/generations/${gnum}/studio_edit`, { index, action: 'open' }, 'Waiting for the previous step…');
  if (!r.ok) return toast(r.j.error, 1);
  P.studio = { gen: gnum, index, project: r.j.project };
  P.packEdit = null;
  location.hash = '#/prepare/' + r.j.project;
}

async function studioEditSticker(gnum, index, to) {
  const r = await api('/api/generations/' + gnum);
  if (!r.ok) return toast('That batch is not available any more', 1);
  const g = r.j,
    t = g.stickers[index - 1],
    anim = t.anim_status === 'READY' && t.webm;
  if (to !== 'agent' && !SES.gens.includes(g.number)) {
    SES = { prompt: g.prompt || '', gens: [g.number], off: [], pack: '' };
    saveSes();
  }
  GS.tab = anim ? 'anim' : 'stickers'; /* opened from the AI: the Studio's session is left alone */
  if (anim) return studioEditAnim(g.number, index);
  Ed.openImage(`/out/${g.generation_id}/${t.png}?e=${t.edited_at || 0}`, {
    back: { gen: g.number, index, to },
    outlined: g.outline_px > 0,
    name: t.name,
    emoji: t.emoji,
  });
}
