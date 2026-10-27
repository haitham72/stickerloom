/* ---------- Plan: the prompts the sheet and video were made from, and the 1-5 tags per cell. */
const copyBox = (title, text, id, rows, edit) =>
  `<div class=pbox><div class=pbh><b>${title}</b><button class="btn sm" data-act=hcopy data-t=${id}>Copy</button></div><textarea ${edit ? `data-pd=${edit.kind} data-g=${edit.g}` : 'readonly'} id=${id} rows=${rows}>${esc(text || '')}</textarea>${edit ? edit.foot : ''}</div>`;

/* The Prompt tab writes the prompts too: what is typed here is what is SENT (the server keeps it on the batch: `custom_prompts`, `video_prompt_sent`). Drafts live in PD per batch and kind, so a re-render
   (the page refreshes while a batch works) never loses what was typed; the tick skips its re-render while one of these boxes has the focus. */
const PD = {};

if (GD && GD.edits) Object.assign(PD, GD.edits);

const pdKey = (g, kind) => `${g}:${kind}`;

const sentVideoPrompt = (g) => {
  const v = (g.video_sheets || [])
    .slice()
    .reverse()
    .find((x) => x.video_prompt_sent);
  return v ? v.video_prompt_sent : null;
};

const pdText = (g, kind) => {
  const d = PD[pdKey(g.number, kind)];
  return d !== undefined ? d : kind === 'sheet' ? g.sheet_prompt : sentVideoPrompt(g) || g.video_prompt;
};

const pdBase = (g, kind) => (kind === 'sheet' ? g.sheet_prompt : sentVideoPrompt(g) || g.video_prompt);

const pdCustom = (g, kind) => {
  const d = PD[pdKey(g.number, kind)];
  return d !== undefined && d.trim() !== '' && d !== pdBase(g, kind);
};

function pdFoot(g, kind) {
  const custom = pdCustom(g, kind),
    live = typeof liveReadyNow === 'function' && liveReadyNow();
  if (g.number === 'draft') return gdFoot(g, kind);
  const kept = typeof keptStills === 'function' ? keptStills(g).length : 0,
    sent = (g.video_sheets || []).some((v) => ['VIDEO_RETURNED', 'SLICED', 'SUPERSEDED'].includes(v.status));
  if (kind === 'video' && sent) return redoFoot(g, live, kept);
  if (kind === 'sheet' && !making(g)) return resheetFoot(g, live);
  const go =
    kind === 'sheet'
      ? `<button class="btn sm pri" data-act=pgsheet data-g=${g.number} ${live ? '' : 'disabled'} title="${live ? "Make a NEW sheet from this batch's own cells and tags, with this prompt" : 'Higgsfield is not connected'}">Generate sheet${custom ? ' with my prompt' : ''}<span class=lv-vp data-lvprice=image></span></button>`
      : `<button class="btn sm pri" data-act=pgvideo data-g=${g.number} ${live && kept && !sent ? '' : 'disabled'} title="${!live ? 'Higgsfield is not connected' : sent ? 'This sheet already has its video; the engine does not animate a sliced sheet twice. Make a new sheet first.' : kept ? 'Animate the kept stickers with this prompt' : 'Keep at least one sticker first'}">Generate video${custom ? ' with my prompt' : ''}<span class=lv-vp data-lvprice=video></span></button>`;
  return `<div class=pdfoot data-pdfoot=${kind}>${go}<button class="btn sm" data-act=pgreset data-g=${g.number} data-kind=${kind} ${PD[pdKey(g.number, kind)] !== undefined ? '' : 'hidden'}>Reset</button><small class=mut>${custom ? 'Your text is sent exactly as written.' : "This is the template's text; edit it to send your own."} The templates stay as they are.</small></div>`;
}

/* Regenerate video in the SAME batch: the text in the box is exactly what is sent, the model is folded above the button, the price is on it.
   The server retires the current video sheet (kept, each clip kept as a version), builds the next one from the same kept stickers and sends it; the stickers keep
   their current animations until the new ones are cut, then G4 asks again. Nothing else changes: same G###, same S#. */
function redoFoot(g, live, kept) {
  const run = typeof videoRun === 'function' ? videoRun(g) : null,
    cutting = (g.video_sheets || []).some((v) => v.status === 'VIDEO_RETURNED') || processing(g);
  const why = !live
    ? 'Higgsfield is not connected'
    : run
      ? 'A video is being made for this batch'
      : cutting
        ? 'The video is being cut into animations: wait for it to finish'
        : !kept
          ? 'Keep at least one sticker first'
          : '';
  const f = typeof fillNow === 'function' ? fillNow() : null,
    gap =
      f != null && typeof previewUrl === 'function' && live && !run && kept
        ? `<div class=lv-regap><label class=lv-gap title="Space between the stickers on the video sheet that is sent: smaller gap = bigger stickers, bigger gap = safer. The new video sheet is built at this gap">Gap <input type=range min=8 max=50 step=1 value="${gapPct(f)}" data-lvgap data-g=${g.number}><output>${gapPct(f)}%</output></label>
   <img class=lv-vprev data-lvprev src="${previewUrl(g, f)}" alt="The video sheet that will be sent" loading=lazy></div>`
        : '';
  return `<div class=pdfoot data-pdfoot=video>${gap}${typeof modelPick === 'function' && live ? modelPick('video', (cutOf(g) || {}).model) : ''}
   ${
     run
       ? `<div class="lv-vgen run"><div class=spin></div><span>Animating with ${esc(run.model)}…<small data-lvt="${run.t0}">${Math.round((Date.now() - run.t0) / 1000)}s</small></span></div>`
       : `<button class="btn sm pri" data-act=pgredo data-g=${g.number} ${why ? 'disabled' : ''} title="${esc(why || 'A new animation of this generation from the same kept stickers, with the prompt above')}">Regenerate<span class=lv-vp data-lvprice=video></span></button>`
   }
   <button class="btn sm" data-act=pgreset data-g=${g.number} data-kind=video ${PD[pdKey(g.number, 'video')] !== undefined ? '' : 'hidden'}>Reset</button></div>`;
}

/* Regenerate: ONE button. A new generation of this batch from the text above (sent as written) and the model folded above it
   ({current} -> {next}); it joins the batch's row as the next generation (parent + regen_of: the batch's family) and becomes the picked one. */
function resheetFoot(g, live) {
  const why = !live ? 'Higgsfield is not connected' : '';
  return `<div class=pdfoot data-pdfoot=sheet>${typeof modelPick === 'function' && live ? modelPick('image', g.sheet_model) : ''}
   <button class="btn sm pri" data-act=pgresheet data-g=${g.number} ${why ? 'disabled' : ''} title="${esc(why || 'A new generation of this batch with the prompt above')}">Regenerate<span class=lv-vp data-lvprice=image></span></button>
   <button class="btn sm" data-act=pgreset data-g=${g.number} data-kind=sheet ${PD[pdKey(g.number, 'sheet')] !== undefined ? '' : 'hidden'}>Reset</button></div>`;
}

ACT.pgresheet = async (el) => {
  const g = GM.get(+el.dataset.g);
  if (!g || typeof liveStart !== 'function') return;
  const text = (pdText(g, 'sheet') || '').trim();
  if (!text) {
    toast('Write a sheet prompt first', 1);
    return;
  }
  if (typeof lcached === 'function' && lcached('image') == null) {
    toast('The price is not known yet: wait for it, then regenerate', 1);
    return;
  }
  el.disabled = true;
  const ok = await liveStart('sheet', {
    prompt: g.prompt || SES.prompt || '',
    ai: false,
    refs: [],
    from_generation: g.number,
    parent: (FAM.get(g.number) || {}).root || g.generation_id,
    regen_of: g.generation_id,
    sheet_prompt: pdCustom(g, 'sheet') ? text : undefined,
    mode: 'regen',
    batch: g.number,
  });
  if (!ok) el.disabled = false;
  else delete PD[pdKey(g.number, 'sheet')];
  glast = '';
  tick(true);
};

ACT.pgredo = async (el) => {
  if (!(await stageOk('animation', () => ACT.pgredo(el)))) return;
  const g = GM.get(+el.dataset.g);
  if (!g || typeof liveStart !== 'function') return;
  const text = (pdText(g, 'video') || '').trim();
  if (!text) {
    toast('Write a video prompt first', 1);
    return;
  }
  if (typeof lcached === 'function' && lcached('video') == null) {
    toast('The price is not known yet: wait for it, then regenerate', 1);
    return;
  } // never spend against a price the person has not been shown
  el.disabled = true;
  const ok = await liveStart('video', { g: g.number, video_prompt: text, redo: true });
  if (!ok) el.disabled = false;
  else delete PD[pdKey(g.number, 'video')];
  glast = '';
  tick(true);
};

function planView(g) {
  const pl = g.reviews && g.reviews.plan,
    sv = sentVideoPrompt(g);
  return `<section class=gplan><div class=pcols><div>${copyBox('Sheet prompt', pdText(g, 'sheet'), 'pp1', 11, { kind: 'sheet', g: g.number, foot: pdFoot(g, 'sheet') })}${copyBox(sv && PD[pdKey(g.number, 'video')] === undefined ? 'Video prompt (the one sent)' : 'Video prompt', pdText(g, 'video'), 'pp2', 6, { kind: 'video', g: g.number, foot: pdFoot(g, 'video') })}
   <p class=mut>Template <b>${esc(g.template_id || 'hand-written plan')}</b>${g.template_version ? ' v' + g.template_version : ''} · plan from ${esc(g.plan_source || '')}${pl ? ` · ${esc(pl.decision.toLowerCase())}d by ${esc(pl.by)}` : ''}</p></div>
   <ul class=pcells>${g.stickers.map((t) => `<li><b>${t.index}. ${esc(t.emoji)} ${esc(t.key.replace(/_/g, ' '))}</b><div class=mut>${esc(t.prompt)}</div><div class=ptags>${(t.tags || []).map((x) => `<span>${esc(x)}</span>`).join('')}</div></li>`).join('')}</ul></div></section>`;
}

document.addEventListener('input', (e) => {
  const t = e.target;
  if (!t.dataset || t.dataset.pd === undefined) return;
  const g = t.dataset.g === 'draft' ? GD : GM.get(+t.dataset.g);
  if (!g) return;
  PD[pdKey(g.number, t.dataset.pd)] = t.value;
  if (g === GD) gdSave();
  const foot = t.closest('.pbox').querySelector('[data-pdfoot]');
  if (foot) {
    const tmp = document.createElement('div');
    tmp.innerHTML = pdFoot(g, t.dataset.pd);
    foot.replaceWith(tmp.firstChild);
    if (typeof fillPrices === 'function') fillPrices();
    if (g === GD) gdPrice();
  }
});

ACT.pgreset = (el) => {
  const n = el.dataset.g === 'draft' ? 'draft' : +el.dataset.g;
  delete PD[pdKey(n, el.dataset.kind)];
  if (n === 'draft') gdSave();
  glast = '';
  tick(true);
};

/* The same Prompt editor, before a sheet exists (docs/architecture.md, the Generate prompt step). "Generate prompt" is POST /api/plan: no G### is allocated, nothing enters SES or GM, no Higgsfield credit is spent.
   With the AI enhancer On the plan is written through the engine the person chose (ai:true; Local is free, Cloud is one small OpenAI call); a model that fails never blocks, the server answers the built-in plan with expand_error and the step says why.
   "Generate sheet" is the one click that spends: the Higgsfield price sits on its own line above it, the edited text goes to the live sheet route as `sheet_prompt` and the previewed plan (its cells, tags and emoji) as `plan`, which the server re-validates (rule 13). */
const gdOn = () => !!(GD && GD.active && GD.gens === SES.gens.join());
// another batch taking the Studio (an opened batch, a finished sheet) ends this screen; the composer's "Prompt draft" brings it back
function gdSave() {
  if (GD) {
    GD.edits = Object.fromEntries(Object.entries(PD).filter(([k]) => k.startsWith('draft:')));
    gstore('stickerloom.prompt-draft', JSON.stringify(GD));
  }
}

function gdHide() {
  if (GD) {
    GD.active = false;
    gdSave();
  }
}

function gdDrop() {
  GD = null;
  GDP = null;
  delete PD['draft:sheet'];
  delete PD['draft:video'];
  try {
    localStorage.removeItem('stickerloom.prompt-draft');
  } catch (e) {}
}

const gdKey = () => {
  const { model, sel } = lsel('image');
  return model ? model.id + JSON.stringify(sel.options) : '';
};

function gdPriceLine() {
  if (typeof liveReadyNow !== 'function' || !liveReadyNow())
    return { t: 'Higgsfield sheet price: unavailable, Higgsfield is not connected', ok: false };
  if (!GDP || GDP.key !== gdKey()) return { t: 'Higgsfield sheet price: checking…', ok: false };
  return GDP.c == null
    ? { t: 'Higgsfield sheet price: unavailable', ok: false, retry: true }
    : { t: `Higgsfield sheet price: ◈ ${fcr(GDP.c)} credits`, ok: true };
}

function gdFoot(g, kind) {
  const reset = `<button class="btn sm" data-act=pgreset data-g=draft data-kind=${kind} ${PD[pdKey('draft', kind)] !== undefined ? '' : 'hidden'}>Reset</button>`;
  if (kind === 'video')
    return `<div class=pdfoot data-pdfoot=video>${reset}<small class=mut>This video prompt is for later: the animation is made after the sheet and its stickers exist.</small></div>`;
  const P = gdPriceLine(),
    custom = pdCustom(g, 'sheet'),
    mx = Math.max(1, +g.batches_max || 1),
    nb = gdNb(g),
    ex = Math.max(0, +g.existing || 0);
  /* the buttons are BATCH NUMBERS of the session: a new request offers 1-4; a session that has 1 batch offers 2-4 (this draft is batch 2), one that has 3 only 4.
     Pressing k makes batches ex+1 .. k (k - ex sheets). */
  const pick =
    mx > 1 || ex
      ? `<div class=gdnb><span class=mut>${ex ? `This session has ${ex} batch${ex === 1 ? '' : 'es'}. Make up to batch` : 'Batches'}</span>${Array.from(
          { length: mx },
          (_, i) => ex + i + 1,
        )
          .map(
            (k) =>
              `<button class="tab${k - ex === nb ? ' on' : ''}" data-act=gdnb data-n=${k - ex} aria-pressed=${k - ex === nb} title="${k - ex === 1 ? `Only batch ${k}` : `Batches ${ex + 1} to ${k}: ${9 * (k - ex)} new stickers, every one a different action`}">${k}</button>`,
          )
          .join(
            '',
          )}${nb > 1 && GDP && GDP.c != null ? `<span class=mut>${nb} sheets · ◈ ${fcr(GDP.c * nb)} credits in all</span>` : ''}</div>`
      : '';
  return `<div class=pdfoot data-pdfoot=sheet>${pick}<div class=mut id=gdprice>${P.t}</div>${P.retry ? '<button class="btn sm" data-act=gdpriceretry>Retry the price</button>' : ''}<button class="btn sm pri" data-act=gdsheet ${P.ok && !GDWORK ? '' : 'disabled'} title="Spends the Higgsfield credits shown above">${nb > 1 ? `Generate ${nb} sheets` : 'Generate sheet'}${custom ? ' with my prompt' : ''}</button>${reset}<small class=mut>${custom ? 'Your text is sent exactly as written.' : g.expanded_by === 'ai' ? "The AI enhancer's text is sent exactly as shown." : "This is the template's text; edit it to send your own."} Generating the prompt spent no Higgsfield credits; this click is what spends.</small></div>`;
}

/* Batches: one Generate makes this batch and the next ones of the pack (the next preset grids of an
   emoji pack, else the next unused actions of the bank), each its own sheet with its own price; the total is on the button. GD.nb is the choice. */
const gdNb = (g) => Math.min(Math.max(1, +g.batches_max || 1), Math.max(1, +g.nb || 1));

ACT.gdnb = (el) => {
  if (!GD) return;
  GD.nb = +el.dataset.n;
  gdSave();
  gdPaint();
};

function gdPaint() {
  const foot = document.querySelector('[data-pdfoot=sheet]');
  if (!gdOn() || !foot || !foot.querySelector('[data-act=gdsheet]')) return;
  const tmp = document.createElement('div');
  tmp.innerHTML = gdFoot(GD, 'sheet');
  foot.replaceWith(tmp.firstChild);
}

async function gdPrice() {
  if (!gdOn() || typeof liveReadyNow !== 'function' || !liveReadyNow()) return;
  const key = gdKey();
  if (GDP && GDP.key === key) return gdPaint(); // the price is known: still repaint, the button was drawn disabled while Generate prompt was busy
  const c = await lcost('image', true);
  GDP = { key, c };
  gdPaint();
}

function gdView() {
  const tab = GD.tab === 'request' ? 'request' : 'plan';
  return `<div class=ghead><div><h2 style="margin:0">Prompt, before the sheet</h2><div class=mut>No batch exists yet · written by ${esc(GD.plan_source || 'the free built-in planner')} · ${GD.expanded_by === 'ai' ? 'no Higgsfield credits spent' : 'nothing spent'}</div></div><button class="btn sm" data-act=gddiscard title="Forget this prompt and its edits. Nothing was created, so nothing is removed">Discard prompt</button></div>
   <div class=gsteps>${[
     ['Request', 'done', esc(GD.prompt), 'request'],
     ['Prompt', 'todo', 'editable, nothing spent', 'plan'],
     ['Stickers', 'todo', 'after the sheet'],
     ['Animation', 'todo', 'after the stickers'],
     ['Pack', 'todo', 'after the stickers'],
   ]
     .map(
       ([l, st, sub, t], i) =>
         `<button class="gst ${st} ${t === tab ? 'cur' : ''}" ${t ? `data-act=gdtab data-t=${t}` : 'disabled'}><span class=gsm>${st === 'done' ? ic('check') : i + 1}</span><span class=gsl><b>${l}</b><small title="${sub.replace(/<[^>]+>/g, '')}">${sub}</small></span></button>`,
     )
     .join('')}</div>
   ${gdNote(GD)}${tab === 'request' ? `<section class=gplan><p>${esc(GD.prompt)}</p><button class=btn data-act=gdtab data-t=plan>Back to the prompt</button> <small class=mut>To change the request, edit the box above and press Generate prompt again; your edits stay until the new prompt is ready.</small></section>` : planView(GD)}`;
}

ACT.gdtab = (el) => {
  if (!GD) return;
  GD.active = true;
  GD.gens = SES.gens.join();
  GD.tab = el.dataset.t === 'request' ? 'request' : 'plan';
  gdSave();
  glast = '';
  if (typeof cpDrawBar === 'function') cpDrawBar();
  tick(true);
};

ACT.gddiscard = () => {
  gdDrop();
  glast = '';
  if (typeof cpDrawBar === 'function') cpDrawBar();
  tick(true);
};

ACT.gdpriceretry = () => {
  const { model, sel } = lsel('image');
  if (model) delete LIVE.est['image' + model.id + JSON.stringify(sel.options)];
  GDP = null;
  gdPaint();
  gdPrice();
};

/* Generate prompt: with the AI enhancer On the plan is written by the model the person chose (the same engine control as the AI screen: Auto / Local / Cloud, composer.js); a model that fails never blocks, the server answers the built-in plan with `expand_error` and the step says why */
const gdEngineName = () => {
  const g = typeof GAI !== 'undefined' ? GAI : null;
  return g && g.provider && g.provider !== 'none'
    ? `${g.provider === 'openai' ? 'cloud' : 'local'} · ${String(g.model || '').replace(/:\d+$/, '')}`
    : '';
};

ACT.gprompt = () => (typeof ptOn === 'function' && ptOn() ? spStudio() : gdPlan(typeof aiOn === 'function' && aiOn()));
/* the Particles choice (composer.js) takes the particle route (particles.js) */
ACT.gpromptfree = () => gdPlan(false);
// "Use the built-in prompt instead": plans again without the model; the saved enhancer setting is not changed
async function gdPlan(ai) {
  const p = (($('prompt') || {}).value || '').trim();
  if (!p) {
    say('Write what you want first, for example <b>teddy bear for school</b>.');
    return;
  }
  if (GDWORK) return;
  if (typeof CP !== 'undefined' && CP.refs.some((r) => r.busy)) {
    toast('Wait for the reference images to finish uploading', 1);
    return;
  }
  GDWORK = true;
  const b = $('go');
  if (b) b.disabled = true;
  if (ai) say(`Asking the AI enhancer${gdEngineName() ? ` (${esc(gdEngineName())})` : ''}… a local model can take a moment.`);
  try {
    const style = typeof LIVE !== 'undefined' ? LIVE.style : 'flat_vector',
      loop = !!(typeof LIVE !== 'undefined' && LIVE.loop);
    const r = await post('/api/plan', {
      prompt: p,
      grid: typeof cpGrid === 'function' ? cpGrid() : '3x3',
      style_id: style,
      loop,
      ai: !!ai,
      actions: (typeof CP !== 'undefined' && CP.actions) || 'creative',
    });
    if (!r.ok) {
      say('');
      toast(r.j.error || 'Could not generate the prompt', 1);
      return;
    }
    delete PD['draft:sheet'];
    delete PD['draft:video'];
    const byAi = r.j.expanded_by === 'ai';
    GD = {
      ...r.j,
      number: 'draft',
      prompt: p,
      ai_asked: !!ai,
      plan_source: byAi ? `the AI enhancer${r.j.expand_model ? ` (${r.j.expand_model})` : ''}` : 'the free built-in planner',
      video_sheets: [],
      reviews: {},
      active: true,
      tab: 'plan',
      gens: SES.gens.join(),
      style,
      loop,
      refs: typeof CP !== 'undefined' ? CP.refs.filter((x) => x.id).map((x) => x.id) : [],
    };
    say('');
    gdSave();
    glast = '';
    await tick(true);
    const gr = $('gres');
    if (gr && gr.scrollIntoView) gr.scrollIntoView({ behavior: 'smooth', block: 'start' }); // the step sits below the style tiles: bring it into view so the click visibly does something
  } catch (e) {
    say('');
    toast(e.message || 'Could not generate the prompt', 1);
  } finally {
    GDWORK = false;
    if (b) b.disabled = false;
    if (typeof cpDrawBar === 'function') cpDrawBar();
    gdPrice();
  }
}

/* the one line under the step's title about the enhancer: why the built-in prompt is shown (the model failed or has no key: the server's own reason), or that the model wrote it and how to leave it */
function gdNote(g) {
  if (!g.ai_asked) return '';
  if (g.expand_error)
    return `<div class="gdnote bad" role=status><span>The AI enhancer did not answer: ${esc(g.expand_error)} This is the built-in prompt instead.</span></div>`;
  if (g.expanded_by === 'transformation')
    return `<div class=gdnote role=status><span>This request changes one character, so the built-in template wrote the cells and the AI enhancer was not asked.</span></div>`;
  if (g.expanded_by === 'ai')
    return `<div class=gdnote role=status><span>Written by the AI enhancer${g.expand_model ? ` (${esc(g.expand_model)})` : ''}.</span><button class="btn sm" data-act=gpromptfree>Use the built-in prompt instead</button></div>`;
  return '';
}

ACT.gdsheet = async (el) => {
  if (!gdOn() || GDWORK) return;
  const g = GD,
    text = (pdText(g, 'sheet') || '').trim();
  if (!text) {
    toast('Write a sheet prompt first', 1);
    return;
  }
  if (typeof liveReadyNow !== 'function' || !liveReadyNow()) {
    toast('Connect Higgsfield first', 1);
    return;
  }
  if (!gdPriceLine().ok) {
    toast('The Higgsfield price is not known yet: wait for it or retry it, then generate', 1);
    return;
  } // never spend against a price the person has not been shown
  GDWORK = true;
  el.disabled = true;
  const run = g.next ? { mode: 'next' } : gdNb(g) > 1 ? { mode: 'pack', run: 'R' + Date.now().toString(36) } : {};
  try {
    const ok = await liveStart('sheet', {
      prompt: g.prompt,
      ai: false,
      refs: g.refs || [],
      style_id: g.style,
      loop: g.loop,
      ...run,
      sheet_prompt: pdCustom(g, 'sheet') || g.expanded_by === 'ai' ? pdText(g, 'sheet') : undefined,
      plan: g.slots
        ? {
            template_id: g.template_id,
            expanded_by: g.expanded_by,
            expand_model: g.expand_model,
            slots: { subject_description: g.slots.subject_description, key_colour: g.slots.key_colour, cells: g.slots.cells },
          }
        : undefined,
    }); // the previewed plan goes with the click: the server validates it and builds the batch from the cells the person saw (ai:false: the enhancer is never asked a second time)
    if (ok && gdNb(g) > 1) await gdMore(g, run);
    if (ok) {
      gdDrop();
      glast = '';
      await tick(true);
    }
  } finally {
    GDWORK = false;
    if (GD) {
      gdSave();
      gdPaint();
    }
    if (typeof cpDrawBar === 'function') cpDrawBar();
  }
};

/* the batches after the first: their plans come from the server (POST /api/plan/more: the next preset grids or bank actions), each is started like the
   first, and every finished sheet joins the same session (mode pack, one run key; a Next-batch draft keeps adding to the session on screen) */
async function gdMore(g, run) {
  const r = await post('/api/plan/more', {
    prompt: g.prompt,
    n: gdNb(g) - 1,
    style_id: g.style,
    loop: g.loop,
    ...(g.next && g.gens ? { gens: String(g.gens).split(',').filter(Boolean) } : {}),
    plan: {
      slots: g.slots,
      grid: g.grid,
      expanded_by: g.expanded_by,
      stickers: (g.stickers || []).map((t) => ({ key: t.key, tags: t.tags })),
    },
  });
  if (!r.ok) return toast(r.j.error || 'Could not plan the other batches', 1);
  let n = 1;
  for (const p of r.j.plans) {
    const ok = await liveStart('sheet', {
      prompt: p.prompt,
      ai: false,
      refs: g.refs || [],
      style_id: g.style,
      loop: g.loop,
      ...run,
      plan: {
        template_id: p.template_id,
        expanded_by: p.expanded_by,
        slots: { subject_description: p.slots.subject_description, key_colour: p.slots.key_colour, cells: p.slots.cells },
      },
    });
    if (!ok) break;
    n++;
  }
  toast(`${n} sheet${n === 1 ? '' : 's'} started: each batch joins this session when it is ready`);
}

ACT.pgsheet = async (el) => {
  const g = GM.get(+el.dataset.g);
  if (!g || typeof liveStart !== 'function') return;
  el.disabled = true;
  const ok = await liveStart('sheet', {
    prompt: g.prompt || SES.prompt || '',
    ai: false,
    refs: [],
    from_generation: g.number,
    sheet_prompt: pdCustom(g, 'sheet') ? PD[pdKey(g.number, 'sheet')] : undefined,
  });
  if (!ok) el.disabled = false;
  else delete PD[pdKey(g.number, 'sheet')];
  glast = '';
  tick(true);
};

ACT.pgvideo = async (el) => {
  if (!(await stageOk('animation', () => ACT.pgvideo(el)))) return;
  const g = GM.get(+el.dataset.g);
  if (!g || typeof liveStart !== 'function') return;
  el.disabled = true;
  const ok = await liveStart('video', { g: g.number, video_prompt: pdCustom(g, 'video') ? PD[pdKey(g.number, 'video')] : undefined });
  if (!ok) el.disabled = false;
  else delete PD[pdKey(g.number, 'video')];
  glast = '';
  tick(true);
};

/* Remove batch (like Delete pack, but into the trash): says what it does before it does it; stickers already added to a pack are copies and stay in their packs */
const grmText = (gs) =>
  `Remove ${gs.map((g) => g.generation_id).join(', ')}? ${gs.length === 1 ? 'It moves' : 'They move'} to the trash, nothing is deleted, and you can restore ${gs.length === 1 ? 'it' : 'them'} from “Removed batches” under Earlier batches. Stickers you already added to a pack stay in their packs.`;

/* Add to group: pick the batch (family) this one joins; the picked one is the parent (flow/groups.py) */
ACT.ggroup = () => {
  const g = sessionGens()[0];
  if (!g) return;
  const fams = (typeof HB !== 'undefined' ? HB.items : []).filter((it) => !(it.variants || [it]).some((v) => v.id === g.number));
  if (!fams.length) return toast('Scroll Earlier batches to load more batches first', 1);
  dlg(
    `<h2>Add ${esc(g.generation_id)} to the group of…</h2><div class=lv-hcol>${fams.map((it) => `<button class=lv-hrow data-act=ggroupgo data-from=${g.number} data-to=${it.id}>${histThumb(it)}<span class=lv-hmeta><b>${histTitle(it)}</b><small>${esc(it.generation_id)}${(it.variants || []).length > 1 ? ` · ${it.variants.length} variations` : ''}</small></span></button>`).join('')}</div><div class=row style="justify-content:flex-end"><button class=btn data-act=dlgx>Cancel</button></div>`,
  );
};

ACT.ggroupgo = (el) => {
  closeDlg();
  histJoin(+el.dataset.from, +el.dataset.to);
};

ACT.grm = () => {
  const gs = sessionGens();
  if (!gs.length) return;
  confirmDlg(
    grmText(gs),
    async () => {
      for (const g of gs) {
        const r = await post(`/api/generations/${g.number}/remove`, {});
        if (!r.ok) {
          toast(r.j.error, 1);
          return;
        }
      }
      const gone = gs.map((g) => g.number);
      SES.gens = SES.gens.filter((id) => !gone.includes(id));
      saveSes();
      gone.forEach((n) => GM.delete(n));
      HB.items = HB.items.filter((x) => !gone.includes(x.id));
      glast = '';
      await remLoad();
      histLoad(false);
      tick(true);
    },
    'Remove',
  );
};

/* the person's one slider: the Studio never guesses, so a button past the stop asks
   once: Proceed (set to that step) / Set to Auto / Cancel. Auto, and any step at or before the stop, goes straight on. */
const STEPS = ['prompt', 'emojis', 'animation', 'export', 'api'],
  STEPNAME = { auto: 'Auto', prompt: 'Prompt', emojis: 'Stickers', animation: 'Animation', export: 'Telegram', api: 'Export' };

let GSTAGEGO = null;

async function stageOk(need, again) {
  const r = await api('/api/prefs'),
    s = (r.ok && r.j.stage) || 'auto';
  if (s === 'auto' || STEPS.indexOf(need) <= STEPS.indexOf(s)) return true;
  GSTAGEGO = again;
  dlg(`<h2>This goes past ${STEPNAME[s]}</h2><p class=mut>Your slider is at <b>${STEPNAME[s]}</b>; this is <b>${STEPNAME[need]}</b>, the step after it. The slider is the same in the chat.</p>
   <div class=row style="justify-content:flex-end"><button class=btn data-act=dlgx>Cancel</button><button class=btn data-act=gstagego data-v=auto>Set to Auto</button><button class="btn pri" data-act=gstagego data-v=${need}>Proceed: set to ${STEPNAME[need]}</button></div>`);
  return false;
}

ACT.gstagego = async (el) => {
  const r = await post('/api/prefs', { stage: el.dataset.v });
  if (!r.ok) return toast(r.j.error || 'Could not change the slider', 1);
  closeDlg();
  toast(`The slider is at ${STEPNAME[el.dataset.v]} now (here and in the chat)`);
  const f = GSTAGEGO;
  GSTAGEGO = null;
  if (f) f();
};

ACT.gscreen = () => {
  GS.screen = !GS.screen;
  gstore('stickerloom.screen', GS.screen ? '1' : '0');
  glast = '';
  tick(true);
};

ACT.gvmode = (el) => {
  GS.view = el.dataset.v;
  gstore('stickerloom.view', GS.view);
  glast = '';
  tick(true);
};

ACT.gtab = (el) => {
  if (el.dataset.t === 'particles' && typeof ACT.spopen === 'function') return ACT.spopen(el);
  GS.tab = el.dataset.t;
  glast = '';
  tick(true);
};

/* ---------- "Get the Higgsfield prompt": when nothing prepared matches the request */
ACT.ghiggs = async () => {
  const p = $('prompt').value.trim();
  if (!p) return;
  const r = await post('/api/plan', {
    prompt: p,
    grid: typeof cpGrid === 'function' ? cpGrid() : '3x3',
    style_id: (typeof LIVE !== 'undefined' && LIVE.style) || 'flat_vector',
    loop: !!(typeof LIVE !== 'undefined' && LIVE.loop),
    ai: typeof aiOn === 'function' && aiOn(),
    actions: (typeof CP !== 'undefined' && CP.actions) || 'creative',
  });
  if (!r.ok) return toast(r.j.error, 1);
  dlg(`<div class=vdlg><h2>Prompt for Higgsfield</h2><div class=mut>Nothing prepared matches “${esc(p)}”. Generate the sheet, name the folder as shown below, and it appears here.</div>
   <h3>Sheet prompt</h3><textarea readonly rows=9 id=hp1>${esc(r.j.sheet_prompt)}</textarea><div class=row><button class="btn sm" data-act=hcopy data-t=hp1>Copy sheet prompt</button></div>
   <h3>Video prompt</h3><textarea readonly rows=5 id=hp2>${esc(r.j.video_prompt)}</textarea><div class=row><button class="btn sm" data-act=hcopy data-t=hp2>Copy video prompt</button></div>
   <div id=hres></div><div class=row style="justify-content:flex-end"><button class=btn data-act=dlgx>Close</button><button class=btn data-act=hgenop>No prepared sheet: generate it</button><button class="btn pri" data-act=hreserve>Reserve the folder names</button></div></div>`);
};

ACT.hcopy = async (el) => {
  try {
    await navigator.clipboard.writeText($(el.dataset.t).value);
    toast('Copied');
  } catch (e) {
    toast('Copy failed', 1);
  }
};

ACT.hgenop = async () => {
  const p = $('prompt').value.trim();
  if (!p) return;
  const r = await post('/api/jobs', { kind: 'sheet', request: { prompt: p, grid: typeof cpGrid === 'function' && cpGrid() === '2x2' ? [2, 2] : [3, 3] } });
  if (!r.ok) return toast(r.j.error, 1);
  const id = r.j.id,
    t0 = Date.now();
  $('hres').innerHTML =
    `<div class=card style="margin:10px 0">Job <b>${esc(id)}</b> requested — waiting for the generator (<span id=hjela>0s</span>). The operator claims it, calls Higgsfield, and completes it; the sheet then runs through the stills like a prepared one.</div>`;
  const iv = setInterval(async () => {
    const g = await api('/api/jobs/' + id),
      el = $('hjela');
    if (!g.ok || !el) {
      clearInterval(iv);
      return;
    }
    el.textContent = Math.round((Date.now() - t0) / 1000) + 's · ' + g.j.status;
    if (['DONE', 'FAILED', 'TIMEOUT'].includes(g.j.status)) {
      clearInterval(iv);
      if (el && el.parentElement)
        el.parentElement.innerHTML +=
          g.j.status === 'DONE'
            ? `<div class=mut>Sheet arrived: <code>${esc(g.j.result.file)}</code> (${Math.round(g.j.result.bytes / 1024)} KB).</div>`
            : JR.controls(g.j);
    }
  }, 5000);
};

ACT.hreserve = async () => {
  const r = await post('/api/tasks', {
    prompt: $('prompt').value.trim(),
    grid: typeof cpGrid === 'function' ? cpGrid() : '3x3',
    style_id: (typeof LIVE !== 'undefined' && LIVE.style) || 'flat_vector',
    loop: !!(typeof LIVE !== 'undefined' && LIVE.loop),
    ai: typeof aiOn === 'function' && aiOn(),
  });
  if (!r.ok) return toast(r.j.error, 1);
  $('hres').innerHTML =
    `<div class=card style="margin:10px 0"><b>Create these two folders and name the downloads into them</b><br><code>${esc(r.j.paths.img)}</code><br><code>${esc(r.j.paths.vid)}</code><div class=mut>The sheet goes in <b>${esc(r.j.folders.img)}</b>, the video in <b>${esc(r.j.folders.vid)}</b>. Then press Generate again.</div></div>`;
};
