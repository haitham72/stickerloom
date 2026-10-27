/* The Animation tab before any animation exists. Per batch: the video sheet
   that would be sent (the frame's "To send" view, with the model, the price and Generate in its control row), the editable video prompt with the
   same footer as the Prompt tab (Generate video with my prompt), and each kept sticker with motion suggestions that add a phrase to the prompt. Nothing here spends
   without the price shown on the button (rule 13). Pure, except the two live.js helpers it reads when they exist. */
const ANIM_MOTIONS = ['bounces', 'waves', 'nods', 'jumps', 'spins', 'laughs', 'leans in', 'sparkles appear'];

function animCreate(gs) {
  return gs
    .map((g) => {
      const kept = keptStills(g),
        live = typeof liveReadyNow === 'function' && liveReadyNow(),
        base = `/out/${g.generation_id}/`;
      return `<section class=gbatch><div class=gbhead><b>${esc(g.generation_id)}</b><span class=mut>${kept.length ? `${kept.length} kept sticker${kept.length === 1 ? '' : 's'} to animate` : 'Keep at least one sticker on the Stickers view first'}</span>
    <span class=gbact>${typeof impBatchBtn === 'function' ? impBatchBtn(g, 'anim') : ''}${typeof motionRow === 'function' ? motionRow(g) : ''}${live ? '' : `<button class="btn sm" data-act=gvideo data-g=${g.number} ${kept.length ? '' : 'disabled'} title="Higgsfield is not connected: make the video from the sheet in your own tool">${ic('film')} Make a video…</button>`}</span></div>
   <div class=ganew><div class=ganew-l>${typeof sheetPanel === 'function' ? sheetPanel(g, 'anim', { noPrompt: true }) : ''}</div>
    <div class=ganew-r>${copyBox('Video prompt', pdText(g, 'video'), 'ap' + g.number, 6, { kind: 'video', g: g.number, foot: `<div class=pdfoot><button class="btn sm" data-act=pgreset data-g=${g.number} data-kind=video>Reset</button><small class=mut>Sent exactly as written when you press Generate.</small></div>` })}
     <div class=ganew-sts>${kept
       .map(
         (
           t,
         ) => `<div class=ganew-st>${t.png ? `<img src="${base + t.png}?e=${t.edited_at || t.rendered_at || 0}" alt="" loading=lazy>` : ''}<div><b>S${t.index} · ${esc(String(t.key || '').replace(/_/g, ' '))}</b>
       <div class=ganew-mo>${ANIM_MOTIONS.map((m) => `<button class=ganew-chip data-act=ganmo data-g=${g.number} data-m="${esc('S' + t.index + ' ' + m)}">${esc(m)}</button>`).join('')}</div></div></div>`,
       )
       .join('')}</div></div></div></section>`;
    })
    .join('');
}

/* a motion suggestion adds "S3 waves" to the batch's video prompt draft (the same draft the Prompt tab edits), so the person sees and can change what is sent */
ACT.ganmo = (el) => {
  const g = GM.get(+el.dataset.g);
  if (!g) return;
  const cur = pdText(g, 'video') || '';
  PD[pdKey(g.number, 'video')] = (cur.trim() ? cur.replace(/\s*$/, '') + '\n' : '') + el.dataset.m + '.';
  glast = '';
  tick(true);
};

/* ---------- Animate, Add: both act on the included batches of the session. */

ACT.ganimate = async (el) => {
  if (!(await stageOk('animation', () => ACT.ganimate(el)))) return;
  const todo = included().filter((g) => g.source.has_video && !animPhase(g) && !processing(g) && keptStills(g).length);
  if (!todo.length) return;
  todo.forEach((g) => {
    ANIM.add(g.number);
    PVON.add(g.number);
    if (g.source.video_path) pvEnsure(g);
  });
  GS.tab = 'anim';
  glast = '';
  tick(true);
  for (const g of todo) {
    const r = await postWait(`/api/generations/${g.number}/animate`, { scope: 'pack' }, 'Waiting for the previous animation…');
    if (!r.ok) {
      toast(r.j.error, 1);
      ANIM.delete(g.number);
    }
  }
};

/* Pack: a three-step modal. 1 the pack (and replace-or-add when stills of the same stickers are already in it), 2 how it looks on each
   background, 3 the names the library will show (name, emoji, and the generator's file name, which never changes) */
let PW = null;

const rname = (k) => {
  const t = String(k).replace(/[-_]+/g, ' ').trim();
  return (t.charAt(0).toUpperCase() + t.slice(1)).slice(0, 60);
};

const PWBG = [
  ['chat', 'Chat'],
  ['light', 'Light'],
  ['dark', 'Dark'],
  ['checker', 'Transparent'],
  ['wall', 'Wallpaper'],
];

ACT.gadd = async (el) => {
  await loadLib();
  const inc = el && el.dataset.g ? included(false).filter((g) => spGid(g.generation_id) === spGid(el.dataset.g)) : included(false),
    rows = [];
  for (const g of inc)
    for (const t of keptOf(g))
      rows.push({
        g: g.number,
        gen: g.generation_id,
        i: t.index,
        anim: animPhase(g),
        src: `/out/${g.generation_id}/${animPhase(g) ? `${t.webm}?v=${t.anim_at || 0}` : t.png}`,
        name: rname(t.key),
        emoji: t.emoji,
        file: t.name,
      });
  if (!rows.length) return;
  const ps = LIB.packs,
    gids = inc.map((g) => spGid(g.generation_id)),
    held = (p) => p.stickers.filter((s) => gids.includes(spGid((s.source || {}).generation))).length,
    home = ps
      .filter(held)
      .sort(
        (a, b) => (b.lead ? 1 : 0) - (a.lead ? 1 : 0) || held(b) - held(a),
      )[0] /* the pack this batch already lives in: its stills, upgraded, not a second pack */,
    target = inc.length === 1 ? ((inc[0].slots || {}).emoji_batch || {}).pack_id : '',
    def = target && ps.some((p) => p.id === target) ? target : home ? home.id : '',
    base = titleCase(inc[0].source.subject);
  let name = base,
    k = 2;
  while (ps.some((p) => p.name.toLowerCase() === name.toLowerCase())) name = `${base} ${k++}`;
  PW = { rows, anim: rows.some((r) => r.anim) };
  const prev = (r) => (r.anim ? `<video src="${r.src}" autoplay loop muted playsinline></video>` : `<img src="${r.src}">`);
  dlg(`<div class=pw><h2 style="margin:0">Add to a pack</h2><div class=mut>${rows.length} ${PW.anim ? 'animated stickers' : 'stickers'} from ${inc.length} batch${inc.length === 1 ? '' : 'es'}</div>
   <div class=pwgrid><div>
    <div class=pwstep><span class=pwn>1</span><b>Choose the pack</b></div>
    <label class=radio><input type=radio name=gp value=new ${def ? '' : 'checked'}> <b>New pack</b></label>
    <div class=fld><input type=text id=gpname value="${esc(name)}" maxlength=60 placeholder="Pack name" autocomplete=off></div>
    ${
      ps.length
        ? `<label class=radio><input type=radio name=gp value=old ${def ? 'checked' : ''}> <b>An existing pack</b></label>
    <div class=fld><select id=gpold>${ps.map((p) => `<option value="${p.id}" ${p.id === def ? 'selected' : ''}>${esc(p.name)} (${p.stickers.length})</option>`).join('')}</select></div>`
        : ''
    }
    <label class=pwlead title="This pack leads its group in the Library: its name and cover show for the group, the other packs fold under it"><input type=checkbox id=pwlead checked> <b>Assign as parent</b></label>
    <div id=pwtwins></div>
    <div class=pwstep style="margin-top:16px"><span class=pwn>2</span><b>How it looks</b><select id=pwbg style="width:auto;margin-left:auto;padding:4px 10px">${PWBG.map(([k, l]) => `<option value=${k}>${l}</option>`).join('')}</select></div>
    <div id=pwprev class="pwprev bg-chat">${rows.map((r) => `<div title="${esc(r.name)}">${prev(r)}<span>${esc(r.emoji)}</span></div>`).join('')}</div>
   </div><div>
    <div class=pwstep><span class=pwn>3</span><b>Names in your library</b></div>
    <div class=mut style="margin-bottom:8px">Rename if you like. Telegram needs at least one emoji per sticker. The file name is kept as it came from the generator.</div>
    <div class=pwrows>${rows.map((r, x) => `<div class=pwrow><div class=pwth>${prev(r)}</div><div style="flex:1;min-width:0"><div class=pwfields><input type=text data-r=${x} data-k=name value="${esc(r.name)}" maxlength=60><input type=text data-r=${x} data-k=emoji value="${esc(r.emoji)}" maxlength=20 class=pwemo></div><small class=mut title="${esc(r.file)}">${esc(r.file)}</small></div></div>`).join('')}</div>
   </div></div>
   <div class=row style="justify-content:flex-end;margin-top:14px"><button class=btn data-act=dlgx>Cancel</button><button class="btn pri" id=pwgo data-act=pwgo></button></div></div>`);
  $('pwbg').onchange = (e) => {
    $('pwprev').className = 'pwprev bg-' + e.target.value;
  };
  const nm = $('gpname'),
    old = $('gpold');
  nm.onfocus = () => {
    document.querySelector('input[name=gp][value=new]').checked = true;
    pwSync();
  };
  if (old) {
    old.onfocus = () => {
      document.querySelector('input[name=gp][value=old]').checked = true;
      pwSync();
    };
    old.onchange = pwSync;
  }
  document.querySelectorAll('input[name=gp]').forEach((r) => (r.onchange = pwSync));
  nm.oninput = pwSync;
  pwSync();
  if (!def) {
    nm.focus();
    nm.select();
  }
};

function pwPack() {
  const mode = (document.querySelector('input[name=gp]:checked') || {}).value || 'new';
  return mode === 'old' && $('gpold') ? { old: true, id: $('gpold').value } : { old: false, name: $('gpname').value.trim() };
}

/* the pack changed: ask replace-or-add when it already holds the still of an animated sticker, and name the button */
function pwSync() {
  const p = pwPack(),
    pk = p.old ? packById(p.id) : null;
  const twins =
    !PW.anim || !pk
      ? 0
      : PW.rows.filter((r) =>
          pk.stickers.some((s) => s.type === 'static' && s.source && s.source.generation === r.gen && s.source.index === r.i),
        ).length;
  const keep = (document.querySelector('input[name=pwmode]:checked') || {}).value || 'replace';
  $('pwtwins').innerHTML = twins
    ? `<div class=pwtw><b>${twins} ${twins === 1 ? 'is' : 'are'} in “${esc(pk.name)}” as ${twins === 1 ? 'a still' : 'stills'} already.</b>
    <label class=radio><input type=radio name=pwmode value=replace ${keep === 'replace' ? 'checked' : ''}> Replace the ${twins === 1 ? 'still' : 'stills'} with the animated ${twins === 1 ? 'one' : 'ones'}</label>
    <label class=radio><input type=radio name=pwmode value=add ${keep === 'add' ? 'checked' : ''}> Add next to ${twins === 1 ? 'it' : 'them'}</label></div>`
    : '';
  $('pwgo').textContent = `Add ${PW.rows.length} to “${p.old ? pk.name : p.name || 'new pack'}”`;
}

ACT.pwgo = async () => {
  const p = pwPack();
  let pid;
  if (p.old) pid = p.id;
  else {
    if (!p.name) {
      toast('Give the pack a name', 1);
      return;
    }
    const r = await post('/api/packs', { name: p.name });
    if (!r.ok) return toast(r.j.error, 1);
    pid = r.j.id;
  }
  const mode = (document.querySelector('input[name=pwmode]:checked') || {}).value === 'add' ? 'add' : 'replace',
    names = {};
  document.querySelectorAll('.pwrows input').forEach((i) => {
    const r = PW.rows[+i.dataset.r];
    (names[r.g] = names[r.g] || {})[r.i] = Object.assign(names[r.g][r.i] || {}, { [i.dataset.k]: i.value.trim() });
  });
  const lead = $('pwlead') && $('pwlead').checked;
  closeDlg();
  await gaddrun(pid, mode, names);
  if (lead) {
    const r = await post('/api/packs/' + pid, { lead: true });
    if (r.ok) await loadLib();
  }
};

/* the wizard's own rows decide what is added (the included batches of the session) */
async function gaddrun(pid, mode, names) {
  let added = 0,
    replaced = 0,
    err = '';
  const edge = typeof egDirty === 'function' && egDirty() ? { outline: egVals().o, erode: egVals().e } : {},
    gens = [...new Set(PW.rows.map((r) => r.g))].map((n) => GM.get(n)).filter(Boolean);
  for (const g of gens) {
    if (!keptOf(g).length) continue;
    const r = await postWait(`/api/generations/${g.number}/add`, { pack_id: pid, mode, names: (names || {})[g.number] || {} });
    if (r.ok) {
      added += r.j.added;
      replaced += r.j.replaced || 0;
    } else err = r.j.error;
  }
  await loadLib();
  PW.pid = pid;
  SES.pack = pid;
  saveSes();
  glast = '';
  tick(true);
  if (!err && typeof spAfterPack === 'function') await spAfterPack();
  if (err) toast(err, 1);
  else
    toast(
      replaced
        ? `Replaced ${replaced} still${replaced === 1 ? '' : 's'} with animated stickers in “${packById(pid).name}”`
        : added
          ? `Added ${added} to “${packById(pid).name}”`
          : 'Those are already in that pack',
    );
}

/* Open the pack this view just added to */
ACT.gopenpack = () => {
  const pid = (PW && PW.pid) || SES.pack;
  if (pid) location.hash = '#/pack/' + pid;
};

/* ---------- Make a video...: the one multi-step path, for a batch without a prepared video */
ACT.gvideo = async (el) => {
  const id = +el.dataset.g,
    r = await postWait(`/api/generations/${id}/quick_sheet`);
  glast = '';
  await tick(true);
  if (!r.ok && !sheetOf(GM.get(id) || { video_sheets: [] })) {
    toast(r.j.error, 1);
    return;
  }
  VG = id;
  drawVdlg();
};

function drawVdlg() {
  if (VG === null) return;
  const g = GM.get(VG);
  if (!g) {
    VG = null;
    return;
  }
  const v = sheetOf(g);
  if (!v) {
    VG = null;
    closeDlg();
    return;
  }
  if (v.status === 'SLICED') {
    VG = null;
    closeDlg();
    toast('Animations are ready');
    glast = '';
    return;
  }
  const base = `/out/${g.generation_id}/`,
    st = v.status,
    work = st === 'VIDEO_RETURNED';
  const row = (n, done, h) =>
    `<div class=vstep><span class="vn ${done ? 'ok' : ''}">${done ? ic('check') : n}</span><div style="flex:1">${h}</div></div>`;
  dlg(`<div class=vdlg><h2>Make a video from this sheet</h2>${SR.bulk(g)}<div class=vgrid>${SR.picture(g, v)}
   <div>${row(1, false, `<b>Download the sheet</b><div class=mut>${v.slots.length} of ${g.stickers.length} stickers on a flat green background, no outline.</div><a class="btn sm" href="${base + v.file}" download="${g.generation_id}-${v.id}-sheet.png">${ic('download')} Download sheet</a>`)}
   ${row(2, false, `<b>Animate it in your tool</b><div class=mut>Image to video, sheet as the first frame.</div><button class="btn sm" data-act=gcopyprompt>Copy video prompt</button>`)}
   ${row(
     3,
     st === 'SLICED',
     `<b>Upload the video</b><div class=mut>${work ? 'Python is slicing the video…' : st === 'VIDEO_BLOCKED' ? `<span style="color:var(--bad)">This video was not accepted (${esc(v.block || '')}). Upload a corrected one.</span>` : 'It is cut into one animation per sticker.'}</div>
     <input type=file id=gvfile accept="video/*,.mp4,.mov,.webm,.mkv" hidden><button class="btn pri sm" data-act=gpickvideo ${work || v.blocked ? 'disabled' : ''}>${ic('plus')} ${st === 'VIDEO_BLOCKED' ? 'Upload another video' : 'Upload video'}</button>${st === 'BUILT' && !v.blocked ? `<button class="btn sm" data-act=gsheetapprove data-g=${g.number}>Approve sheet</button>` : ''}`,
   )}</div></div>
   <div class=row style="justify-content:flex-end"><button class=btn data-act=gvclose>Close</button></div></div>`);
  const f = $('gvfile');
  if (f)
    f.onchange = async () => {
      const file = f.files[0];
      if (!file) return;
      const r = await fetch(`/api/generations/${g.number}/video_sheet/${v.id}/video?name=${encodeURIComponent(file.name)}`, {
        method: 'POST',
        body: file,
      });
      if (!r.ok) {
        let j = {};
        try {
          j = await r.json();
        } catch (e) {}
        toast(j.error || 'Upload failed', 1);
      }
      glast = '';
      tick(true);
    };
}

ACT.gpickvideo = () => {
  const f = $('gvfile');
  if (f) f.click();
};

ACT.gsheetapprove = async (el) => {
  const r = await postWait(`/api/generations/${+el.dataset.g}/quick_sheet`);
  if (!r.ok) toast(r.j.error, 1);
  glast = '';
  await tick(true);
  drawVdlg();
};

ACT.gvclose = () => {
  VG = null;
  closeDlg();
};

ACT.gcopyprompt = async () => {
  const g = GM.get(VG),
    v = g && sheetOf(g);
  try {
    await navigator.clipboard.writeText((v && v.video_prompt) || (g && g.video_prompt) || '');
    toast('Video prompt copied');
  } catch (e) {
    toast('Copy failed', 1);
  }
};

/* animations made before the border check existed have no verdict: judge them once (the server decodes the source cells, no re-encode) */
const RECHK = new Set();

function autoRecheck(g) {
  if (RECHK.has(g.number) || processing(g) || making(g)) return;
  const old = (t) =>
    t.anim_status === 'READY' &&
    !t.bounds_checked &&
    !(t.anim_report || []).some((c) => c.name === 'inside_frame' || c.name === 'inside_slot') &&
    (((t.anim_metrics || {}).source || '') === '3x3 mp4' || String((t.anim_metrics || {}).source || '').startsWith('clip:'));
  if (!g.stickers.some(old)) return;
  RECHK.add(g.number);
  post(`/api/generations/${g.number}/recheck`).then((r) => {
    if (!r.ok && r.status === 409) {
      RECHK.delete(g.number);
      return;
    }
    if (r.ok && r.j.flagged.length)
      toast(
        `${g.generation_id}: ${r.j.flagged.map((i) => 'S' + i).join(', ')} leave${r.j.flagged.length === 1 ? 's' : ''} the cell and ${r.j.flagged.length === 1 ? 'is' : 'are'} now blocked (checked now, these were made before the check existed)`,
        1,
      );
    glast = '';
    tick(true);
  });
}

/* send a created sticker back to the Studio: its batch, on the right view */
ACT.openstudio = async (el) => {
  const gid = +String(el.dataset.gen || '').replace(/\D/g, '');
  const r = await api('/api/generations/' + gid);
  if (!r.ok) return toast('That batch is not available any more', 1);
  const t = r.j.stickers[+el.dataset.i - 1];
  SES = { prompt: r.j.prompt || '', gens: [gid], off: [], pack: '' };
  saveSes();
  GS.tab = el.dataset.view || (t && t.anim_status === 'READY' ? 'anim' : 'stickers');
  glast = '';
  location.hash = '#/studio';
};

ACT.lcstudioedit = (el) => {
  const s = LCL[LCI];
  lcClose();
  studioEditSticker(+String(s.source.generation).replace(/\D/g, ''), s.source.index);
};

ACT.lcopenstudio = () => {
  const s = LCL[LCI];
  lcClose();
  ACT.openstudio({ dataset: { gen: s.source.generation, i: s.source.index } });
};
