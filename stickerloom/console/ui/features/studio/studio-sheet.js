/* the in-place marks on a sheet of size sheet_size: a dashed faded box over every cell that is not in the set, a solid thin outline over one kept with a check.
   Every cell is a full-cell hit area and does exactly one thing (cellOp): allow / take back / include / drop. It never opens a tile (opening belongs to the thumbnail on the right). The hover and the
   label say the reason in plain words, never a raw check id; keyboard: Tab to a cell, Enter to press it (the same actions the tile offers). */
function issueSvg(g, stage, k = 1) {
  const W = g.source.sheet_size ? g.source.sheet_size[0] : ((sheetOf(g) || {}).canvas || [2048])[0],
    sw = Math.max(2, W / 450) * k,
    kind = stage === 'anim' ? 'animation' : 'still';
  let body = '';
  g.stickers.forEach((t) => {
    const c = (stage === 'anim' && layoutOfCell(g, t.index)) || (t.metrics || {}).cell;
    if (!c) return;
    const m = mark(t, stage, g),
      o = cellOp(g, t, stage),
      r = `x="${c[0]}" y="${c[1]}" width="${c[2]}" height="${c[3]}"`,
      act = ` ${cellAct(g, t, stage)} style="pointer-events:all;cursor:pointer"`,
      act2 = o.op === 'allow' || o.op === 'unallow',
      fin = o.op === 'none' ? (ALW(g, kind).final || {})[t.index] : null;
    let label;
    if (m) {
      const col = CAT[m.cat][0];
      label = `S${t.index}: ${m.issues.map((i) => CAT[i.cat][1] + ' - ' + i.text).join('; ')}${m.accepted ? '' : ' (not in the set)'} · ${cellLabel(o, t)}`;
      if (fin && !label.includes(fin)) label += `. ${fin}`;
      const tip = `<title>${esc(label)}</title>`;
      body += m.accepted
        ? `<rect ${r} fill="${act2 ? col : 'none'}" fill-opacity="${act2 ? 0.06 : 0}" stroke="${col}" stroke-width="${sw * 1.1}"${act} tabindex="0" role="button" aria-label="${esc(label)}">${tip}</rect>`
        : `<rect ${r} fill="#fff" fill-opacity=".34" stroke="${col}" stroke-width="${sw * 2}" stroke-dasharray="${W / 55} ${W / 110}"${act} tabindex="0" role="button" aria-label="${esc(label)}">${tip}</rect>`;
    } else {
      label = `S${t.index}: ${stage === 'anim' ? CELLTXT[cellState(t)] : 'accepted'} · ${cellLabel(o, t)}`;
      body += `<rect ${r} class=sh-hit${act} tabindex="0" role="button" aria-label="${esc(label)}"><title>${esc(label)}</title></rect>`;
    }
  });
  return body;
}

const keptAnim = (g) =>
  g.stickers.filter((t) => t.anim_status === 'READY' && t.review.still !== 'REJECTED' && !['REJECTED', 'BLOCKED'].includes(t.review.anim));

const keptOf = (g) => (animPhase(g) ? keptAnim(g) : keptStills(g));

const sheetOf = (g) => [...g.video_sheets].reverse().find((v) => v.status !== 'REJECTED');

/* the sheet the animations are cut from now: a sheet retired by Regenerate video (SUPERSEDED) stays it until the new video is sliced (gates.cut_sheet) */
const cutOf = (g) => {
  const vs = [...g.video_sheets].reverse().filter((v) => v.video);
  return vs.find((v) => v.status === 'SLICED') || vs.find((v) => v.status === 'SUPERSEDED') || sheetOf(g);
};
// as gates.cut_sheet: the picked (SLICED) one first
const hasVid = (g) => !!(g.source.has_video || (cutOf(g) && cutOf(g).video));

const sessionGens = () => SES.gens.map((id) => GM.get(id)).filter(Boolean);

/* ---------- Green screen & cuts: the raw / keyed sheet, the measured cut lines, each sticker's boundary, and the analysis */
const SV = { g: null, view: 'raw', lines: true, boxes: true };

/* the measured cut lines (dashed), the cell numbers and each sticker's boundary, as SVG over a sheet of size sheet_size */
function cutSvg(g, lines, boxes, k = 1, stage = 'still') {
  const s = g.source,
    [W, H] = s.sheet_size,
    G = s.grid,
    sw = Math.max(2, W / 450) * k;
  let svg = '';
  if (G && lines)
    svg +=
      G.xs
        .slice(1, -1)
        .map(
          (x) =>
            `<line x1="${x}" y1="0" x2="${x}" y2="${H}" stroke="#2563eb" stroke-width="${sw}" stroke-dasharray="${W / 50} ${W / 90}"/>`,
        )
        .join('') +
      G.ys
        .slice(1, -1)
        .map(
          (y) =>
            `<line x1="0" y1="${y}" x2="${W}" y2="${y}" stroke="#2563eb" stroke-width="${sw}" stroke-dasharray="${W / 50} ${W / 90}"/>`,
        )
        .join('');
  g.stickers.forEach((t) => {
    const m = t.metrics || {},
      c = m.cell,
      b = m.bbox;
    if (!c) return;
    if (lines)
      svg += `<text x="${c[0] + W / 90}" y="${c[1] + W / 22}" font-size="${W / 26}" font-weight="700" fill="#2563eb" stroke="#fff" stroke-width="${W / 380}" paint-order="stroke">${t.index}</text>`;
    const mk = boxes && mark(t, stage, g);
    if (boxes && b)
      svg += `<rect x="${c[0] + b[0]}" y="${c[1] + b[1]}" width="${b[2] - b[0]}" height="${b[3] - b[1]}" fill="none" stroke="${mk && !mk.accepted ? CAT[mk.cat][0] : '#facc15'}" stroke-width="${sw * 1.4}"/>`;
  });
  return svg + (boxes ? issueSvg(g, stage, k) : '');
}

/* The Animation view's left panel, the twin of the green-screen panel: the prepared video as one sheet with the same cut lines, a chip per
   cell showing how it came out, and the analysis at a glance; "Full analysis" has every number and every check that did not pass. */
const cellState = (t) =>
  t.status !== 'READY'
    ? 'skip'
    : isOob(t)
      ? 'oob'
      : t.anim_status === 'READY'
        ? 'ok'
        : t.anim_status === 'FAILED'
          ? 'fail'
          : t.anim_status === 'PROCESSING'
            ? 'busy'
            : 'todo';

const CELLTXT = { ok: 'ready', oob: 'out of bounds', fail: 'no animation', busy: 'working', todo: 'not animated', skip: 'no still' };

function vcutSvg(g, k) {
  return cutSvg(g, true, false, k) + issueSvg(g, 'anim', k);
}

function animStats(g) {
  const S = g.stickers.filter((t) => t.status === 'READY'),
    A = S.filter((t) => t.anim_status === 'READY'),
    kb = A.map((t) => (t.anim_metrics || {}).kb || 0);
  return {
    total: S.length,
    done: A.length,
    oob: S.filter(isOob),
    fail: S.filter((t) => t.anim_status === 'FAILED'),
    avg: kb.length ? Math.round(kb.reduce((a, b) => a + b, 0) / kb.length) : 0,
    max: Math.max(0, ...kb),
    ms: A.filter((t) => (t.anim_metrics || {}).cache !== 'hit').reduce((a, t) => a + (((t.anim_metrics || {}).ms || {}).finish || 0), 0),
    cached: A.filter((t) => (t.anim_metrics || {}).cache === 'hit').length,
  };
}

const LAY = new Map();
// a video sheet's layout.json, fetched once
function layoutOf(g, v) {
  const k = g.number + v.id;
  if (LAY.has(k)) return LAY.get(k);
  LAY.set(k, null);
  fetch(`/out/${g.generation_id}/${v.layout}`)
    .then((r) => r.json())
    .then((j) => {
      LAY.set(k, j);
      glast = '';
      tick(true);
    })
    .catch(() => {});
  return null;
}

function videoBox(g, k) {
  const sz = g.source.sheet_size,
    v = cutOf(g);
  if (g.source.video_path && sz)
    return `<div class=sbox style="background:#111"><video src="/src/${g.number}/video" autoplay loop muted playsinline style="display:block;width:100%;aspect-ratio:${sz[0]}/${sz[1]};object-fit:fill"></video><svg viewBox="0 0 ${sz[0]} ${sz[1]}" preserveAspectRatio="none">${vcutSvg(g, k)}</svg></div>`;
  if (v && v.video) {
    const [W, H] = v.canvas || [1, 1],
      lay = layoutOf(g, v),
      sw = Math.max(2, W / 450) * k;
    const rects = lay
      ? lay.slots
          .map(
            (sl) =>
              `<rect x="${sl.rect[0]}" y="${sl.rect[1]}" width="${sl.rect[2]}" height="${sl.rect[3]}" fill="none" stroke="#2563eb" stroke-width="${sw}" stroke-dasharray="${W / 50} ${W / 90}" opacity="${sl.sticker ? 1 : 0.35}"/>`,
          )
          .join('')
      : '';
    return `<div class=sbox style="background:#111"><video src="/out/${g.generation_id}/${v.preview || v.video}" autoplay loop muted playsinline style="display:block;width:100%;aspect-ratio:${W}/${H};object-fit:fill"></video><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${rects}${issueSvg(g, 'anim', k)}</svg>${SR.controls(g, v)}</div>`;
  }
  if (v) return SR.picture(g, v);
  return `<div class="gbadmsg mut" style="padding:26px 10px">These animations come from pre-sliced clips, so there is no single video sheet for this batch.</div>`;
}

/* The animations row: animation 1 … n = this generation's video sheets that have a video (a removed one leaves the row, its files stay).
   ★ = the one the stickers are cut from now. Click another = pick it (re-cut from its stored video: free, no new video, G4 asks again); x = take it out of
   the row (never the one in use); Report = an issue note on the one in use (data-k=animation data-id=G###/A#). */
const animsOf = (g) => (g.video_sheets || []).filter((v) => v.video && ['SLICED', 'SUPERSEDED'].includes(v.status));

function animRow(g) {
  const as = animsOf(g),
    cur = cutOf(g);
  if (!as.length || g.source.video_path) return '';
  const busy = processing(g) || (g.video_sheets || []).some((v) => v.status === 'VIDEO_RETURNED'),
    n = (v) => +String(v.id).replace(/\D/g, '');
  return `<div class=ganims><span class=mut>Animations</span>${as
    .map((v) => {
      const on = cur && cur.id === v.id;
      return `<span class="ganim${on ? ' on' : ''}"><button class=ggen ${on ? '' : 'data-act=gapick '}data-g=${g.number} data-a=${v.id} ${busy && !on ? 'disabled' : ''} aria-pressed=${!!on} title="${esc(v.id)}${v.model ? ' · ' + esc(v.model) : ''}${on ? ' · in use: the stickers are cut from this one' : ' · click to use this one again (re-cut, free)'}">${on ? '★ ' : ''}animation ${n(v)}</button>${on ? '' : `<button class=gx2 data-act=garm data-g=${g.number} data-a=${v.id} ${busy ? 'disabled' : ''} title="Take ${esc(v.id)} out of the row (its files stay on disk)">${ic('x')}</button>`}</span>`;
    })
    .join('')}
</div>`;
}

ACT.gapick = async (el) => {
  const r = await postWait(`/api/generations/${el.dataset.g}/pick_video`, { sheet: el.dataset.a }, 'Waiting for the previous step…');
  if (!r.ok) return toast(r.j.error || 'Could not pick it', 1);
  toast(
    r.j.changed
      ? `Using animation ${String(el.dataset.a).replace(/\D/g, '')} again: cutting it into the stickers (free)…`
      : 'That animation is already in use',
  );
  glast = '';
  tick(true);
};

ACT.garm = (el) => {
  const a = el.dataset.a;
  confirmDlg(
    `Take animation ${a.replace(/\D/g, '')} (${a}) out of the row? Its video and clips stay on disk.`,
    async () => {
      const r = await post(`/api/generations/${el.dataset.g}/remove_video`, { sheet: a });
      if (!r.ok) return toast(r.j.error || 'Could not remove it', 1);
      toast(`Animation ${a.replace(/\D/g, '')} taken out of the row`);
      glast = '';
      tick(true);
    },
    'Remove',
  );
};

function videoPanel(g) {
  const s = g.source,
    vs = cutOf(g),
    vi = Object.assign({}, (vs && vs.video_info) || {}, s.video_info || {}),
    a = animStats(g),
    bad = a.oob.length + a.fail.length;
  return `${animRow(g)}${videoBox(g, 2)}
   ${SR.bulk(g)}
   <div class=vchips>${g.stickers.map((t) => chip(g, t, 'anim')).join('')}</div>
   <div class=gsfoot><span class="gcut ${bad ? 'warn' : 'ok'}">${a.done} of ${a.total} animated${a.oob.length ? ` · ${a.oob.map((t) => 'S' + t.index).join(', ')} out` : ''}${a.fail.length ? ` · ${a.fail.map((t) => 'S' + t.index).join(', ')} not animated` : ''}</span>
    <span class=mut>${esc(vi.mode || (s.video_path ? '3x3 mp4' : vs && vs.video ? 'video sheet' : 'clips'))}${vi.width ? ' · ' + vi.width + '×' + vi.height : ''}${a.done ? ` · ${a.avg} KB avg` : ''}</span><button class="link" data-act=gvsheet data-g=${g.number}>Full analysis</button></div>${legend(g, 'anim')}`;
}

const AV = { g: null };

ACT.gvsheet = (el) => {
  AV.g = +el.dataset.g;
  animDlg();
};

ACT.gaclose = () => {
  AV.g = null;
  closeDlg();
};

function animDlg() {
  const g = GM.get(AV.g);
  if (!g) return;
  const s = g.source,
    vi = s.video_info || {},
    a = animStats(g);
  const rows = g.stickers
    .map((t) => {
      const m = t.anim_metrics || {},
        ms = m.ms || {},
        ins = (t.anim_report || []).find((c) => c.name === 'inside_frame'),
        st = cellState(t),
        mk = mark(t, 'anim', g),
        col = mk ? CAT[mk.cat][0] : st === 'ok' ? 'var(--ok)' : 'var(--mut)';
      return `<tr><td>${t.index}</td><td style="color:${col}">${mk ? esc(CAT[mk.cat][1]) + (st === 'fail' ? ': ' + esc(t.anim_reason || '') : '') : CELLTXT[st]}</td><td>${m.frames_out || ''}</td><td>${m.fps || ''}</td><td>${m.kb || ''}</td><td>${m.crf || ''}</td>
      <td>${m.loop_seam !== undefined ? m.loop_seam + ' / ' + m.loop_limit : ''}</td><td>${ins ? ins.value + ' px' + (ins.data && ins.data.frames_over ? `, ${ins.data.frames_over} frames` : '') : ''}</td><td>${m.cache === 'hit' ? 'from the cache' : ms.finish !== undefined ? (ms.finish / 1000).toFixed(1) + ' s' : ''}</td></tr>`;
    })
    .join('');
  const bad = g.stickers
    .flatMap((t) =>
      (t.anim_report || [])
        .filter((c) => !c.ok)
        .map(
          (c) =>
            `<div class=chk><i class=cdot style="--cc:${CAT[CATOF[c.name] || 'bad'][0]}"></i><span class="${c.severity === 'WARN' ? 'w' : 'f'}">S${t.index} ${esc(c.name)}</span> <span class=mut>${esc(c.detail || '')}</span></div>`,
        ),
    )
    .join('');
  dlg(`<div class="sheetdlg animdlg"><div class=mrow><h2 style="margin:0">Video sheet & analysis <span class=mut style="font-weight:500">${g.generation_id} · sheet ${s.subject_id}</span></h2><button class=btn data-act=gaclose>✕</button></div>
   <div class=sgrid><div>${videoBox(g, 1.4)}${SR.bulk(g)}<div class=mut style="margin-top:8px">Blue dashed lines: where the video is cut into one animation per sticker.</div>${legend(g, 'anim')}</div>
    <div class=sside><h3>Source</h3><div class=kv style="margin:6px 0 12px"><span>mode</span><span>${esc(vi.mode || '')}</span>${vi.file ? `<span>file</span><span>${esc(vi.file)}</span><span>video</span><span>${esc(vi.size || '')} · ${vi.fps || ''} fps · ${vi.duration || ''} s · ${esc(vi.codec || '')}</span>` : `<span>clips</span><span>${esc(vi.clip_format || '')} (one transparent clip per sticker)</span>`}
     <span>animated</span><span>${a.done} of ${a.total}</span><span>work this time</span><span>${(a.ms / 1000).toFixed(1)} s${a.cached ? ` · ${a.cached} read back from the cache` : ''}</span></div>
     <h3>Every animation</h3><table class=stbl><tr><th>#</th><th>result</th><th>frames</th><th>fps</th><th>KB</th><th>crf</th><th>loop seam / limit</th><th>on the border</th><th>time</th></tr>${rows}</table>
     <h3 style="margin-top:12px">Checks that did not pass</h3>${bad || '<div class=mut>Every check passed.</div>'}</div></div></div>`);
}

/* the same sheet, always on the Generate page beside the stickers (Raw / Keyed per batch) */
const SHK = new Map();
// batch number -> the sheet view the person chose: 'keyed' | 'fixed' (raw is the default)
/* the views of a batch's sheet: the generator's raw sheet, the keyed one, and (after a slice was edited and saved) the sheet rebuilt with that slice fixed, same layout so the same S# */
const sheetViews = (g) => {
  const s = g.source,
    v = [{ id: 'raw', file: s.sheet_copy, label: 'Raw' }];
  if (s.keyed) v.push({ id: 'keyed', file: s.keyed, label: 'Keyed' });
  if (s.sheet_fixed)
    v.push({ id: 'fixed', file: s.sheet_fixed, label: `Fixed (${(s.sheet_fixed_cells || []).map((i) => 'S' + i).join(', ')})` });
  return v;
};

const sheetView = (g) => {
  const vs = sheetViews(g);
  return vs.find((v) => v.id === SHK.get(g.number)) || vs[0];
};

/* The batch's ONE frame: view tabs Raw · Keyed (· Fixed) · To send · Video
   over one picture, the cell controls of that view ON it (rule 10, design §9: still controls on the sheet views, animation controls on the video), one caption
   line, then ONE control row (genRow). "To send" is the video sheet that will be sent, at the Gap (the only view with the Gap slider). The Stickers view
   opens on Raw, the Animation view on Video (else To send); each view remembers its own choice per batch (SHK / AVK). */
const AVK = new Map();

const vidOf = (g) => !!(g.source.video_path || (cutOf(g) && cutOf(g).video));

const canSend = (g) => !g.source.has_video && keptStills(g).length > 0 && typeof previewUrl === 'function' && typeof fillNow === 'function';

function mediaView(g, mode) {
  const k = (mode === 'anim' ? AVK : SHK).get(g.number);
  if (k === 'video' && vidOf(g)) return 'video';
  if (k === 'send' && canSend(g)) return 'send';
  if (sheetViews(g).some((v) => v.id === k)) return k;
  return mode === 'anim' ? (vidOf(g) ? 'video' : canSend(g) ? 'send' : 'raw') : 'raw';
}

function sheetPanel(g, mode, opt) {
  const s = g.source,
    size = s.sheet_size;
  if (!size || !s.sheet_copy) return '';
  mode = mode === 'anim' ? 'anim' : 'still';
  const view = mediaView(g, mode),
    cur = sheetViews(g).find((v) => v.id === view) || { id: view },
    keyed = cur.id === 'keyed',
    base = `/out/${g.generation_id}/`,
    G = s.grid,
    [W, H] = size;
  const cut = G
    ? `<span class="gcut ${G.method === 'gutter' || G.method === 'single' ? 'ok' : 'warn'}">${G.method === 'gutter' ? 'cut at gutters' : 'cut: ' + esc(G.method)}</span>`
    : '';
  const tab = (id, label) =>
    `<button class="tab ${id === cur.id ? 'on' : ''}" data-act=gshk data-g=${g.number} data-k=${id}>${label}</button>`;
  const tabs = `<div class=tabs>${sheetViews(g)
    .map(
      (v) => `<button class="tab ${v.id === cur.id ? 'on' : ''}" data-act=gshk data-g=${g.number} data-k=${v.id}>${esc(v.label)}</button>`,
    )
    .join('')}${canSend(g) ? tab('send', 'To send') : ''}${vidOf(g) ? tab('video', 'Video') : ''}</div>`;
  let body;
  if (view === 'video') body = videoPanel(g);
  else if (view === 'send') {
    const f = fillNow();
    body = `<div class=sbox><img data-lvprev src="${esc(previewUrl(g, f))}" alt="The video sheet that will be sent" loading=lazy></div>
     <label class=lv-gap title="Space between the stickers on the video sheet that is sent: smaller gap = bigger stickers, bigger gap = safer">Gap <input type=range min=8 max=50 step=1 value="${gapPct(f)}" data-lvgap data-g=${g.number}><output>${gapPct(f)}%</output></label>
     <div class=gsfoot><span class=mut>What the video model gets: your ${keptStills(g).length} kept sticker${keptStills(g).length === 1 ? '' : 's'} at this gap</span></div>`;
  } else
    body = `<div class="sbox ${keyed ? 'bg-' + bg : ''}"><img src="${base + cur.file}" alt="${esc(cur.label)}"><svg viewBox="0 0 ${W} ${H}">${cutSvg(g, true, true, 2)}</svg></div>
   <div class=vchips>${g.stickers.map((t) => chip(g, t, 'still')).join('')}</div>
   <div class=gsfoot>${cut}<span class=mut>blue = cuts, yellow = sticker edge</span><button class="link" data-act=gsheet data-g=${g.number}>Full analysis</button></div>${legend(g, 'still')}`;
  return `<aside class=gsheet><div class=gshead>${tabs}</div>${body}${genRow(g, mode, opt)}</aside>`;
}

/* The ONE control row under the frame: the model (a plain drop-down, "made with X →" once something exists), the settings that have a choice, Loop for a
   video, the button with its price, and a Prompt toggle that opens the text that will be sent (edited in place, Reset to the template). Stickers: Regenerate
   = a new generation of this batch. Animation: Generate the first time, then Regenerate = a new animation of this generation, at the Gap of "To send". */
const PO = new Set();

function genRow(g, mode, opt) {
  if (typeof liveReadyNow !== 'function' || !liveReadyNow() || making(g)) return '';
  const kind = mode === 'anim' ? 'video' : 'image',
    pk = kind === 'video' ? 'video' : 'sheet';
  if (kind === 'video') {
    if (g.source.has_video) return '';
    const stalled = (LIVE.q || []).find((j) => j.kind === 'video' && String(j.generation) === g.generation_id && JR.stalled(j));
    if (stalled) return JR.controls(stalled);
    const run = videoRun(g);
    if (run)
      return `<div class="lv-vgen run"><div class=spin></div><span>Animating with ${esc(run.model)}…<small data-lvt="${run.t0}">${Math.round((Date.now() - run.t0) / 1000)}s</small></span></div>`;
    if ((g.video_sheets || []).some((v) => v.status === 'VIDEO_RETURNED') || processing(g))
      return `<div class="lv-vgen run"><div class=spin></div><span>Cutting the video into animations…</span></div>`;
  }
  const again = kind === 'image' || (g.video_sheets || []).some((v) => v.video),
    kept = keptStills(g).length;
  const made = kind === 'video' ? (cutOf(g) || {}).model : g.sheet_model;
  const act = kind === 'image' ? 'pgresheet' : again ? 'pgredo' : 'pgvideo',
    off = kind === 'video' && !kept;
  const open = PO.has(g.number + ':' + pk) && !(opt && opt.noPrompt);
  return `<div class=gctl>${modelPick(kind, made)}${kind === 'video' ? `<label class=lv-chk title="On: the video model is told to loop and end on its first pose. Off: stickerloom closes the loop itself"><input type=checkbox data-lvloop ${LIVE.loop ? 'checked' : ''}> Loop</label>` : ''}
   <span class=gspace></span>${opt && opt.noPrompt ? '' : `<button class="btn sm" data-act=gptog data-g=${g.number} data-k=${pk} aria-expanded=${open}>Prompt</button>`}
   <button class="btn sm pri" data-act=${act} data-g=${g.number} ${off ? 'disabled title="Keep at least one sticker first"' : ''}>${again ? 'Regenerate' : 'Generate'}<span class=lv-vp data-lvprice=${kind}></span></button></div>
   ${open ? `<div class=gprompt>${copyBox(kind === 'image' ? 'Sheet prompt' : 'Video prompt', pdText(g, pk), 'gp' + pk + g.number, kind === 'image' ? 8 : 6, { kind: pk, g: g.number, foot: `<div class=pdfoot><button class="btn sm" data-act=pgreset data-g=${g.number} data-kind=${pk}>Reset</button><small class=mut>Sent exactly as written.</small></div>` })}</div>` : ''}`;
}

ACT.gptog = (el) => {
  const k = el.dataset.g + ':' + el.dataset.k;
  PO.has(k) ? PO.delete(k) : PO.add(k);
  glast = '';
  tick(true);
};

ACT.gshk = (el) => {
  const n = +el.dataset.g,
    M = GS.tab === 'anim' ? AVK : SHK;
  if (el.dataset.k === 'raw' && M === SHK) SHK.delete(n);
  else M.set(n, el.dataset.k);
  glast = '';
  tick(true);
};

ACT.gsheet = (el) => {
  SV.g = +el.dataset.g;
  SV.view = 'raw';
  SV.lines = true;
  SV.boxes = true;
  sheetDlg();
};

function sheetDlg() {
  const g = GM.get(SV.g);
  if (!g) return;
  const s = g.source,
    base = `/out/${g.generation_id}/`,
    size = s.sheet_size,
    G = s.grid;
  if (!size || !s.sheet_copy) return toast('This sheet has not been read yet', 1);
  const [W, H] = size,
    keyedEv = [...g.events].reverse().find((e) => e.stage === 'keyed' && e.status === 'done'),
    kd = (keyedEv && keyedEv.detail) || {},
    th = (kd.threshold || []).slice().sort((a, b) => a - b);
  const img = SV.view === 'keyed' && s.keyed ? base + s.keyed : base + s.sheet_copy;
  const svg = cutSvg(g, SV.lines, SV.boxes);
  const checks = (g.verify.sheet || [])
    .map(
      (c) =>
        `<div class=chk><span class="${c.ok ? 'p' : c.severity === 'WARN' ? 'w' : 'f'}">${c.ok ? '✓' : c.severity === 'WARN' ? '!' : '✗'} ${esc(c.name)}</span> <span class=mut>${esc(c.detail || '')}</span></div>`,
    )
    .join('');
  const rows = g.stickers
    .map((t) => {
      const m = t.metrics || {};
      return `<tr><td>${t.index}</td><td>${t.status === 'READY' ? '<span style="color:var(--ok)">ready</span>' : `<span style="color:var(--bad)">${esc(t.reason || t.status)}</span>`}</td><td>${m.fg_px !== undefined ? m.fg_px : ''}</td><td>${m.threshold !== undefined ? m.threshold : ''}</td><td>${m.scale || ''}</td><td>${esc((m.warnings || []).join(', '))}</td></tr>`;
    })
    .join('');
  dlg(`<div class=sheetdlg><div class=mrow><h2 style="margin:0">${g.key_colour === 'blue' ? 'Blue' : 'Green'} screen & cuts <span class=mut style="font-weight:500">${g.generation_id} · sheet ${s.subject_id} · ${W}×${H}</span></h2><button class=btn data-act=gsclose>✕</button></div>
   <div class=gtoolbar><div class=tabs><button class="tab ${SV.view === 'raw' ? 'on' : ''}" data-act=gsview data-v=raw>Raw sheet</button><button class="tab ${SV.view === 'keyed' ? 'on' : ''}" data-act=gsview data-v=keyed ${s.keyed ? '' : 'disabled'}>Background removed</button></div>
    <label class=mut><input type=checkbox data-act=gstog data-k=lines ${SV.lines ? 'checked' : ''}> Cut lines</label><label class=mut><input type=checkbox data-act=gstog data-k=boxes ${SV.boxes ? 'checked' : ''}> Sticker boundaries</label></div>
   <div class=sgrid><div class="sbox ${SV.view === 'keyed' ? 'bg-' + bg : ''}"><img src="${img}" alt="Sheet"><svg viewBox="0 0 ${W} ${H}">${svg}</svg></div>
    <div class=sside><h3>Analysis</h3>${checks || '<div class=mut>No sheet checks recorded.</div>'}
     <div class=kv style="margin:10px 0"><span>background sampled</span><span>${kd.bg ? `rgb(${kd.bg.join(', ')})` : '?'}</span><span>key threshold</span><span>${th.length ? `${th[0]} to ${th[th.length - 1]} (per cell)` : '?'}</span><span>cut</span><span>${esc(kd.cut || (G && G.method) || '?')}${G && G.method !== 'gutter' && G.method !== 'single' ? ' (a character may cross a cut)' : ''}</span><span>grid</span><span>${g.grid[0]}×${g.grid[1]}</span></div>
     <table class=stbl><tr><th>#</th><th>result</th><th>subject px</th><th>threshold</th><th>scale</th><th>warnings</th></tr>${rows}</table>
     <div class=mut style="margin-top:8px">Blue dashed lines: where the sheet is cut (in the gaps between characters). Yellow boxes: each sticker's measured boundary.</div>${legend(g, 'still')}</div></div></div>`);
}

ACT.gsview = (el) => {
  SV.view = el.dataset.v;
  sheetDlg();
};

ACT.gstog = (el) => {
  SV[el.dataset.k] = el.checked;
  sheetDlg();
};

ACT.gsclose = () => {
  SV.g = null;
  closeDlg();
};
