/* Chat chat-stage.js: shares the core's state through explicit callbacks. */
function chatStageInit({ A, store, loadSessions, loadAgent, loadModels, engRedraw, engSync, loadSession, ensureSession, agList }) {
  let ENGSRC = null;
  ACT.agconfig = el => {
    const open = el.getAttribute('aria-expanded') !== 'true';
    el.setAttribute('aria-expanded', String(open));
    $('ag-config').classList.toggle('open', open);
  };
  ACT.agdel = (el) => {
    const id = el.dataset.id;
    confirmDlg(
      'Delete this chat? The stickers it made stay in the Studio.',
      async () => {
        await post(`/api/chat/sessions/${id}/delete`);
        if (A.sid === id) ACT.agnew();
        await loadSessions();
        agList();
      },
      'Delete',
    );
  };
  ACT.agset = async () => {
    closeStage(false);
    A.engineOpen = false;
    setEngine();
    A.setOpen = !A.setOpen;
    setSet();
    if (A.setOpen && !A.cat) {
      const r = await api('/api/models');
      A.cat = r.ok ? r.j : {};
      setSet();
    }
  };
  ACT.agengine = async () => {
    closeStage(false);
    A.setOpen = false;
    setSet();
    A.engineOpen = !A.engineOpen;
    setEngine();
    if (A.engineOpen) {
      await loadModels();
      setEngine();
    }
  };
  ACT.agsetgrid = async (el) => saveSet({ grid: el.dataset.v });
  ACT.aggridtoggle = async (el) =>
    saveSet({ grid: ((A.sess && A.sess.settings && A.sess.settings.grid) || '2x2') === '2x2' ? '3x3' : '2x2' });
  const styleNow = () =>
    (A.sess && A.sess.settings && A.sess.settings.style_id) || A.pre || (A.agent && A.agent.default_style) || 'flat_vector';
  function aiModelDetails(llm, selected, pref = 'auto') {
    const allGroups = llm.local ? { 'LM Studio': llm.local, 'OpenAI': llm.openai || [], 'Fallback': llm.fallback || [] } : null;
    let groups = allGroups;
    if (groups) {
      if (pref === 'local') {
        groups = { 'LM Studio': groups['LM Studio'] };
      } else if (pref === 'cloud') {
        groups = { 'OpenAI': groups['OpenAI'], 'Fallback': groups['Fallback'] };
      }
    }
    const list = groups ? Object.values(groups).flat() : (llm.models || []).map((x) => ({ id: x.id, label: x.id }));
    const saved = (llm.aliases || {})[selected] || selected || '';
    const cur = (list.find((x) => x.id === saved || x.model === saved) || {}).id || saved;
    const fallback = llm.preference === 'local' ? llm.current : llm.default || llm.current;
    return list.length ? { list, groups, cur, def: fallback || '' } : null;
  }
  function selectorDetails() {
    const st = (A.sess && A.sess.settings) || { grid: '2x2', ask_before_spending: true },
      mine = st.models || {},
      cat = A.cat || {},
      llm = A.llm || {};
    const pref = (A.agent || {}).preference || 'auto';
    const kind = (k) => {
      const list = [...(cat[k] || []), ...((cat.more || {})[k] || [])].map((x) => ({ id: x.id, label: x.label }));
      const def = (list.find((x) => x.id === (cat.defaults || {})[k]) || {}).label || '';
      return list.length ? { list, cur: mine[k] || '', def } : null;
    };
    return {
      models: { ai: aiModelDetails(llm, mine.ai, pref), image: kind('image'), video: kind('video') },
      grid: st.grid,
      ask: st.ask_before_spending !== false,
      bypass: !!(st.creator && st.creator.bypass),
      actions: st.actions || 'predefined',
    };
  }
  function drawStage() {
    const el = $('ag-stage');
    if (!el) return;
    el.innerHTML = AIU.stageHTML(A.sess ? AIU.stageOf(A.sess.settings) : A.stage, A.stageOpen, ic);
    if (A.stageOpen && !el.contains(document.activeElement)) {
      const r = el.querySelector('.ag-range');
      if (r) r.focus({ preventScroll: true });
    }
  }
  function closeStage(focus) {
    if (!A.stageOpen) return;
    A.stageOpen = false;
    drawStage();
    if (focus) {
      const b = document.querySelector('#ag-stage .ag-stage-btn');
      if (b) b.focus();
    }
  }
  ACT.agstage = () => {
    A.setOpen = false;
    setSet();
    A.engineOpen = false;
    setEngine();
    A.stageOpen = !A.stageOpen;
    drawStage();
  };
  ACT.agsetact = (el) => saveSet({ actions: el.dataset.v === 'predefined' ? 'predefined' : 'creative' });
  ACT.agstagepick = async (el, ev, keep) => {
    const v = el.dataset.v;
    if (!AIU.STAGES.some((s) => s[0] === v)) return;
    const prior = A.stage;
    A.stage = v;
    if (A.sess) A.sess.settings = Object.assign({}, A.sess.settings, { stage: v });
    if (!keep) A.stageOpen = false;
    drawStage();
    if (A.sess) await saveSet({ stage: v });
    else {
      const r = await post('/api/prefs', { stage: v });
      if (!r.ok) {
        A.stage = prior;
        drawStage();
        toast(r.j.error || 'Could not save the stage', 1);
      }
    }
    const b = document.querySelector(keep ? '#ag-stage .ag-range' : '#ag-stage .ag-stage-btn');
    if (b) b.focus();
  };
  document.addEventListener('keydown', (e) => {
    if (!e.target.closest || !e.target.closest('#ag-stage')) return;
    if (A.stageOpen && e.key === 'Escape') {
      e.preventDefault();
      closeStage(true);
    } else if (A.stageOpen && e.key === 'Enter' && e.target.classList.contains('ag-range')) {
      e.preventDefault();
      closeStage(true);
    }
  });
  document.addEventListener('pointerdown', (e) => {
    if (e.target.classList && e.target.classList.contains('ag-range')) A.stageDrag = true;
  });
  document.addEventListener('input', (e) => {
    const r = e.target;
    if (!r.dataset || r.dataset.agrange === undefined) return;
    const k = +r.value,
      s = AIU.STAGES[k];
    const box = r.closest('.ag-slider');
    if (box) box.style.setProperty('--k', k);
    box && box.querySelectorAll('.ag-ticks i').forEach((x, i) => x.classList.toggle('on', i <= k));
    r.setAttribute('aria-valuetext', s[1]);
    document.querySelectorAll('#ag-stage .ag-names .ag-stop').forEach((b, i) => {
      b.classList.toggle('on', i === k);
      b.setAttribute('aria-pressed', i === k);
    });
    const caption = document.querySelector('#ag-stage .ag-stage-caption');
    if (caption) caption.innerHTML = `<b>${AIU.esc(s[1])}</b><span>${AIU.esc(s[2])}</span>`;
  });
  document.addEventListener('click', (e) => {
    const inside = e.composedPath().some((el) => el.id === 'ag-stage');
    if (A.stageOpen && !inside) closeStage(false);
  });
  document.addEventListener('focusin', (e) => {
    if (A.stageOpen && !e.target.closest('#ag-stage')) closeStage(false);
  });
  function drawBar() {
    if (typeof drawStage === 'function') drawStage();
    const bar = $('ag-bar'),
      box = $('ag-styles');
    if (!bar || !box) return;
    const st = (A.sess && A.sess.settings) || { grid: '2x2', ask_before_spending: true },
      list = (A.agent && A.agent.styles) || [],
      cur = styleNow();
    const now = list.find((s) => s.id === cur);
    const open =
      A.stylesOpen ?? !A.pre; /* the tiles until a style is picked, then only the chosen one (a chip); the chip opens them again */
    bar.innerHTML = `${now ? `<button type=button class="ag-chip ag-cur" data-act=agstyles aria-expanded=${open} title="The style of the next sheet. Click to ${open ? 'close' : 'see every style'}."><img src="/assets/styles/${AIU.esc(now.id)}" alt="">${AIU.esc(now.label)}<i aria-hidden=true>${open ? '▴' : '▾'}</i></button>` : ''}<button type="button" class="ag-chip ag-grid" data-act="aggridtoggle" title="How many stickers in one sheet: nine (3×3) or four (2×2). Click to change.">${ic('lib')}${st.grid === '2x2' ? '2×2 sheet' : '3×3 sheet'}</button>`;
    box.hidden = !open;
    box.innerHTML = !open
      ? ''
      : list
          .map(
            (s) =>
              `<button type=button class="ag-st${s.id === cur ? ' on' : ''}" role=radio aria-checked=${s.id === cur} data-act=agstyle data-id="${AIU.esc(s.id)}" title="${AIU.esc(s.label)}: ${AIU.esc(s.hint)}"><img src="/assets/styles/${AIU.esc(s.id)}" alt="" loading=lazy><b>${AIU.esc(s.label)}</b></button>`,
          )
          .join('');
  }
  ACT.agstyle = async (el) => {
    A.pre = el.dataset.id;
    A.stylesOpen = false;
    store.set('stickerloom.ai.style', A.pre);
    if (A.sess) await saveSet({ style_id: A.pre });
    else drawBar();
  };
  ACT.agstyles = () => {
    A.stylesOpen = !(A.stylesOpen ?? !A.pre);
    drawBar();
  };
  ACT.agsetask = async () => saveSet({ ask_before_spending: !(A.sess ? A.sess.settings.ask_before_spending : true) });
  ACT.agbe = async (el) => {
    const r = await post('/api/ai/backend', { backend: el.dataset.v });
    if (r.ok) {
      await Promise.all([loadAgent(), loadModels(), engSync()]);
      engRedraw();
    } else toast(r.j.error || 'Could not change the AI engine', 1);
  };
  async function saveSet(p) {
    const sid = await ensureSession();
    if (!sid) return;
    const r = await post(`/api/chat/sessions/${sid}/settings`, p);
    if (r.ok) {
      A.sess.settings = r.j.settings;
      A.stage = AIU.stageOf(r.j.settings);
      if (p.models && Object.hasOwn(p.models, 'ai')) await loadAgent();
      setSet();
      setEngine();
      drawBar();
    } else {
      if (p.stage) await loadSession(sid, true);
      toast(r.j.error, 1);
    }
  }
  function beRow() {
    const a = ENGSRC || A.agent || {},
      av = a.availability || {},
      pref = a.preference || 'auto';
    const b = (v, label) => {
      const ok = v === 'auto' || (av[v] && av[v].ok);
      const why =
        v === 'auto'
          ? 'Use the local model when LM Studio answers, otherwise the cloud; keep what works'
          : (av[v] && av[v].why) || (av[v] && av[v].model) || '';
      return `<button data-act=agbe data-v=${v} class="${pref === v ? 'on' : ''}${ok ? '' : ' is-off'}" title="${AIU.esc(ok && v !== 'auto' ? av[v].model : why)}">${label}</button>`;
    };
    const now =
      a.agent && a.agent.provider !== 'none' && !AIU.engine(a).rules
        ? `now: ${a.agent.provider === 'local' ? 'local' : 'cloud'} · ${AIU.esc((a.agent.model || '').replace(/:\d+$/, ''))}`
        : `now: ${AIU.esc(a.noModel || 'rules only')}`;
    const warn =
      pref !== 'auto' && av[pref] && !av[pref].ok ? ` · <span class=ai-err>${AIU.esc(av[pref].why || 'not available')}</span>` : '';
    return `<div class=r><div><b>AI engine</b><small>${now}${warn}</small></div><div class=ai-seg>${b('auto', 'Auto')}${b('local', 'Local')}${b('cloud', 'Cloud')}</div></div>${modelRow()}`;
  }
  function modelRow() {
    const m = A.llm,
      pref = (ENGSRC || A.agent || {}).preference || 'auto';
    if (A.engineOpen && !ENGSRC) {
      const d = selectorDetails().models.ai;
      if (!d) return '<div class="r ag-mrow"><small>No models available. Check the provider connection and refresh.</small></div>';
      return AIU.modelSel('ai', 'Chat', d);
    }
    if (m && (m.local || m.openai || m.fallback)) {
      const details = aiModelDetails(m, pref === 'local' ? 'local:' + m.current : m.selected || '', pref);
      if (!details) return '<div class="r ag-mrow"><small>No models available. Check the provider connection and refresh.</small></div>';
      return AIU.modelSel('ai', 'Chat', details).replace('data-agm=ai', 'data-agmodel');
    }
    if (A.llmBusy || !m)
      return `<div class="r ag-mrow"><div><small>${A.llmBusy ? 'Switching the local model: the first answer can take a moment…' : 'Checking the local model…'}</small></div></div>`;
    if (!m.models.length && pref === 'cloud') return '';
    const sel = m.models.length
      ? `<select class=ag-sel data-agmodel aria-label="Local model">${m.models.map((x) => `<option value="${AIU.esc(x.id)}"${x.id === m.current ? ' selected' : ''}>${AIU.esc(x.id)}${x.loaded ? ' · loaded' : ''}</option>`).join('')}</select>`
      : '';
    return `<div class="r ag-mrow"><div><b>Local model</b><small class="${m.ok ? '' : 'ai-err'}" title="${AIU.esc(m.why || '')}">${AIU.esc(AIU.modelNote(m))}</small></div>${sel}</div>`;
  }
  document.addEventListener('change', async (e) => {
    const s = e.target;
    if (!s || !s.dataset || s.dataset.agmodel === undefined) return;
    A.llmBusy = true;
    engRedraw();
    const r = await post('/api/ai/backend', s.value ? { model: s.value } : { model: '' });
    if (!r.ok) toast((r.j && r.j.error) || 'Could not change the model', 1);
    await Promise.all([loadModels(), loadAgent(), engSync()]);
    A.llmBusy = false;
    engRedraw();
  });
  document.addEventListener('change', async (e) => {
    const s = e.target;
    if (!s || !s.dataset || s.dataset.agm === undefined) return;
    await saveSet({ models: { [s.dataset.agm]: s.value } });
  });
  document.addEventListener('change', (e) => {
    const r = e.target;
    if (!r.dataset || r.dataset.agrange === undefined) return;
    const drag = A.stageDrag;
    A.stageDrag = false;
    ACT.agstagepick({ dataset: { v: AIU.STAGES[+r.value][0] } }, null, !drag);
  });
  globalThis.AIENG = {
    rows: (src) => {
      ENGSRC = src || null;
      try {
        return beRow();
      } finally {
        ENGSRC = null;
      }
    },
    ensure: () => {
      if (!A.llm && !A.llmLoading) {
        A.llmLoading = true;
        loadModels().finally(() => {
          A.llmLoading = false;
        });
      }
    },
    load: loadModels,
    settings: (src) => {
      A.settingsEngine = src;
      const el = $('set-ai-engine');
      if (el) el.innerHTML = AIENG.rows(src);
      loadModels();
    },
  };
  function crRows(st) {
    const c = Object.assign({ on: false, scope: 'images', bypass: false }, st.creator || {});
    return `<div class=r><div><b>Approve everything for me</b><small>Animation and Export stages. ${c.bypass ? 'On: no questions on the way. Any rejection still stops it' : 'Off: it stops at each approval and waits for one click'}</small></div><button type=button class="ai-sw${c.bypass ? ' on' : ''}" data-act=agcr data-k=bypass role=switch aria-checked="${c.bypass}" aria-label="Approve everything automatically"></button></div>`;
  }
  ACT.agcr = async (el) => {
    const cur = Object.assign({ on: false, scope: 'images', bypass: false }, (A.sess && A.sess.settings.creator) || {}),
      k = el.dataset.k;
    const next = k === 'scope' ? { scope: el.dataset.v } : { [k]: !cur[k] };
    await saveSet({ creator: Object.assign({}, cur, next) });
  };
  function setEngine() {
    const el = $('ai-engine-set');
    if (!el) return;
    el.classList.toggle('on', A.engineOpen);
    const button = $('ai-pill');
    if (button) button.setAttribute('aria-expanded', !!A.engineOpen);
    if (A.engineOpen) el.innerHTML = `${beRow()}`;
  }
  function setSet() {
    const el = $('ai-set');
    if (!el) return;
    el.classList.toggle('on', A.setOpen);
    if (!A.setOpen) return;
    const st = (A.sess && A.sess.settings) || { ask_before_spending: true, actions: 'predefined' },
      d = selectorDetails();
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', 'Image and video models and options');
    el.innerHTML =
      AIU.modelSel('image', 'Image', d.models.image) +
      AIU.modelSel('video', 'Video', d.models.video) +
      (!A.cat
        ? '<p class=sp-hint>Loading models…</p>'
        : !d.models.image && !d.models.video
          ? '<p class=sp-hint>No image or video models available.</p>'
          : '') +
      `<div class=r><div><b>Sticker actions</b></div><div class=ai-seg><button type=button data-act=agsetact data-v=creative class="${d.actions === 'creative' ? 'on' : ''}">Creative</button><button type=button data-act=agsetact data-v=predefined class="${d.actions === 'creative' ? '' : 'on'}">Predefined</button></div></div>
  ${crRows(st)}
  <div class=r><div><b>Ask before spending</b></div><button type=button class="ai-sw${st.ask_before_spending !== false ? ' on' : ''}" data-act=agsetask role=switch aria-checked="${st.ask_before_spending !== false}" aria-label="Ask before spending"></button></div>`;
  }
  document.addEventListener('click', (e) => {
    const path = e.composedPath();
    if (A.setOpen && !path.some((el) => el.id === 'ai-set' || (el.dataset && el.dataset.act === 'agset'))) {
      A.setOpen = false;
      setSet();
    }
    if (A.engineOpen && !path.some((el) => el.id === 'ag-engine')) {
      A.engineOpen = false;
      setEngine();
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && (A.setOpen || A.engineOpen)) {
      const engine = A.engineOpen;
      A.setOpen = false;
      A.engineOpen = false;
      setSet();
      setEngine();
      const b = document.querySelector(`[data-act=${engine ? 'agengine' : 'agset'}]`);
      if (b) b.focus();
    }
  });
  return {
    ENGSRC,
    styleNow,
    aiModelDetails,
    selectorDetails,
    drawStage,
    closeStage,
    drawBar,
    saveSet,
    beRow,
    modelRow,
    crRows,
    setEngine,
    setSet,
  };
}
