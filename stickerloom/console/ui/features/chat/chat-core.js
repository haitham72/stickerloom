if (typeof document !== 'undefined' && typeof ACT !== 'undefined') {
  (() => {
    const A = {
      sid: null,
      sess: null,
      sessions: [],
      agent: null,
      llm: null,
      llmBusy: false,
      busy: false,
      sel: new Set(),
      open: new Set(),
      els: new Map(),
      shown: undefined,
      poll: 0,
      setOpen: false,
      engineOpen: false,
      since: 0,
      pre: '',
      stage: 'auto',
    };
    const SUGG = ['a teddy bear waving', 'falcon stickers', 'my dog as a banana', 'Eid mubarak greetings'];
    ICONS.send = '<path d="M12 19V5M6 11l6-6 6 6"/>';

    const store = {
      get(k) {
        try {
          return localStorage.getItem(k);
        } catch (e) {
          return null;
        }
      },
      set(k, v) {
        try {
          localStorage.setItem(k, v);
        } catch (e) {}
      },
    };
    A.pre = store.get('stickerloom.ai.style') || ''; // the style picked last: what a chat that has no session yet will start with

    /* ---------- the living background (ambient motion + the pointer) */
    let bgOn = false;
    function bgInit(root) {
      if (bgOn) return;
      bgOn = true;
      const calm = matchMedia('(prefers-reduced-motion:reduce)').matches;
      let tx = 0,
        ty = 0,
        x = 0,
        y = 0,
        raf = 0;
      const set = () => {
        root.style.setProperty('--gx', x.toFixed(1) + 'px');
        root.style.setProperty('--gy', y.toFixed(1) + 'px');
        root.style.setProperty('--px', (x / Math.max(1, root.clientWidth) - 0.5).toFixed(3));
        root.style.setProperty('--py', (y / Math.max(1, root.clientHeight) - 0.5).toFixed(3));
      };
      const tick = () => {
        x += (tx - x) * 0.11;
        y += (ty - y) * 0.11;
        set();
        raf = Math.abs(tx - x) > 0.4 || Math.abs(ty - y) > 0.4 ? requestAnimationFrame(tick) : 0;
      };
      const home = () => {
        tx = x = root.clientWidth * 0.62;
        ty = y = root.clientHeight * 0.26;
        set();
      };
      home();
      if (calm) return;
      root.addEventListener(
        'pointermove',
        (e) => {
          if (e.pointerType === 'touch') return;
          const r = root.getBoundingClientRect();
          tx = e.clientX - r.left;
          ty = e.clientY - r.top;
          if (!raf) raf = requestAnimationFrame(tick);
        },
        { passive: true },
      );
      root.addEventListener('pointerleave', () => {
        tx = root.clientWidth * 0.62;
        ty = root.clientHeight * 0.26;
        if (!raf) raf = requestAnimationFrame(tick);
      });
    }

    /* ---------- data */
    async function loadSessions() {
      const r = await api('/api/chat/sessions');
      if (r.ok) {
        A.sessions = r.j.sessions;
        A.unavailable = r.j.agent && r.j.agent.available === false ? r.j.agent.hint || 'install stickerloom[agent] to enable chat' : null;
      }
      return A.sessions;
    }
    async function loadStage() {
      const r = await api('/api/prefs');
      if (r.ok) A.stage = AIU.stageOf(r.j);
      drawStage();
    }
    async function loadAgent() {
      const sid = A.sid,
        r = await api('/api/chat/agent' + (sid ? '?session=' + encodeURIComponent(sid) : ''));
      if (sid !== A.sid) return;
      if (r.ok) A.agent = r.j;
      pill();
      drawBar();
      if (A.engineOpen) setEngine();
    }
    /* the local server's models for the engine row's dropdown (no count limit); the first call may wait while the server loads its model */
    async function loadModels() {
      const r = await api('/api/llm/models');
      A.llm = r.ok ? r.j : { models: [], current: '', ok: false, why: 'Could not read the model list' };
      engRedraw();
    }
    /* the engine control (Auto / Local / Cloud + the local model) is ONE implementation for two screens: the AI screen's gear panel and the Studio's AI enhancer (composer.js, cpDrawEngine).
   Both draw it with beRow() and both end up in engRedraw(); the Studio hands in its own source (GET /api/ai, shaped like GET /api/chat/agent) through AIENG.rows. */
    function engRedraw() {
      if (A.engineOpen) setEngine();
      if (typeof cpDrawEngine === 'function') cpDrawEngine();
      const el = $('set-ai-engine');
      if (el && A.settingsEngine) el.innerHTML = AIENG.rows(A.settingsEngine);
    }
    const engSync = () => (typeof aiRefresh === 'function' ? aiRefresh() : null); // the Studio's copy of the same facts (generate.js)

    async function loadSession(id, quiet) {
      const r = await api('/api/chat/sessions/' + id);
      if (!r.ok) {
        if (r.status === 404) {
          A.sid = null;
          A.sess = null;
          store.set('stickerloom.ai.sid', '');
          paint();
          return null;
        }
        if (!quiet) toast(r.j.error || 'Could not load the chat', 1);
        return null;
      }
      const changed =
        !A.sess || A.sess.id !== r.j.id || ((A.sess.settings || {}).models || {}).ai !== ((r.j.settings || {}).models || {}).ai;
      A.sess = r.j;
      paint();
      if (changed) loadAgent().catch(() => {});
      return r.j;
    }
    async function ensureSession() {
      if (A.sid) return A.sid;
      const r = await post('/api/chat/sessions', {});
      if (!r.ok) {
        toast(r.j.error || 'Could not start a chat', 1);
        return null;
      }
      A.sid = r.j.id;
      A.sess = Object.assign({ messages: [], subjects: [], interactions: [], working: false }, r.j);
      store.set('stickerloom.ai.sid', A.sid);
      history.replaceState(null, '', '#/agent/' + A.sid);
      await applyPre();
      return A.sid;
    }
    /* a chat starts with the style picked last (picking one before the first message must not create an empty chat) */
    async function applyPre() {
      const def = (A.agent && A.agent.default_style) || 'flat_vector';
      if (!A.pre || A.pre === def || !A.sid) return;
      const r = await post(`/api/chat/sessions/${A.sid}/settings`, { style_id: A.pre });
      if (r.ok && A.sess) A.sess.settings = r.j.settings;
    }

    /* ---------- the screen */
    RENDER.agent = async (arg) => {
      const root = $('s-agent');
      if (!root.dataset.ready) {
        root.dataset.ready = 1;
        root.innerHTML = `<div class=ai-bg><i class="ai-blob b1"></i><i class="ai-blob b2"></i><i class="ai-blob b3"></i><i class=ai-dots></i><i class=ai-glow></i></div>
  <div class=ai id=ai><div class=ai-main>
   <div class=ai-top><span class=ttl id=ai-ttl></span><span class=sp></span><span class=ag-engine id=ag-engine><button type=button class=ai-pill id=ai-pill data-act=agengine title="LLM model" aria-haspopup=dialog><i></i><span>…</span></button><div class="ai-set ai-engine-pop" id=ai-engine-set role=dialog aria-label="LLM model"></div></span>
    <button class=ai-ibtn data-act=agnew title="New chat" aria-label="New chat">${ic('plus')}</button></div>
   <div class=ai-scroll id=ai-scroll><div class=ai-col id=ai-col></div></div>
   <div class=ai-dock><div class=ai-dock-in>
    <div class=ai-set id=ai-set></div>
    <div class=ai-sel id=ai-sel></div>
    <form class=ai-box id=ai-box autocomplete=off><button type=button class=ai-ibtn data-act=agset title="Image and video models" aria-label="Image and video models" aria-haspopup=dialog>${ic('settings')}</button>
     <textarea id=ai-in rows=1 placeholder="Make or change stickers" aria-label="Message"></textarea>
     <span class=ag-stage id=ag-stage></span>
     <button class=ai-send id=ai-send type=submit aria-label="Send" disabled>${ic('send')}</button></form>
    <div class=ag-config id=ag-config><button type=button class="ag-chip ag-config-toggle" data-act=agconfig aria-expanded=false aria-controls=ag-config-body>Style and options</button>
     <div class=ag-config-body id=ag-config-body><div class=ag-bar id=ag-bar></div>
      <div class=ag-styles id=ag-styles role=radiogroup aria-label="Style"></div></div></div>
    <div class=ai-sugg id=ai-sugg></div>
   </div></div></div></div>`;
        bgInit(root);
        const ta = $('ai-in'),
          form = $('ai-box');
        const fit = () => {
          ta.style.height = 'auto';
          ta.style.height = Math.min(132, ta.scrollHeight) + 'px';
          $('ai-send').disabled = A.busy || !ta.value.trim();
        };
        ta.addEventListener('input', fit);
        ta.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
            e.preventDefault();
            form.requestSubmit();
          }
        });
        form.addEventListener('submit', (e) => {
          e.preventDefault();
          const t = ta.value;
          ta.value = '';
          fit();
          agSend(t);
        });
        root.addEventListener(
          'scroll',
          (e) => {
            const t = e.target;
            if (t.classList && t.classList.contains('car-track')) carSync(t);
          },
          true,
        );
        carDrag(root);
      }
      loadAgent().catch(
        () => {},
      ); /* not awaited: the answer includes the local model's readiness probe, which can take a moment while the server loads it; the pill and the tiles fill in when it arrives */
      await Promise.all([loadSessions(), loadStage()]);
      if (A.unavailable) {
        /* the chat agent is the optional [agent] extra: say so, keep the rest of the app working */
        const col = $('ai-col');
        if (col)
          col.innerHTML = `<div class=card style="max-width:520px;margin:56px auto;text-align:center"><h2>Chat is an optional extra</h2><p class=mut>${AIU.esc(A.unavailable)}</p><p><code>pip install "stickerloom[agent]"</code></p><button class="btn pri" data-act=nav data-to=generate>Open the Studio</button></div>`;
        const ta0 = $('ai-in');
        if (ta0) {
          ta0.disabled = true;
          ta0.placeholder = 'install stickerloom[agent] to enable chat';
        }
        return;
      }
      const want =
        AIU.sid(location.hash) ||
        (arg && /^S\d+$/.test(arg) ? arg : null) ||
        A.sid ||
        (location.hash.replace(/^#\/?/, '') === 'agent' ? null : null);
      if ((want && want !== A.sid) || (want && !A.sess)) {
        A.sid = want;
        store.set('stickerloom.ai.sid', want);
        A.els.clear();
        await loadSession(want, true);
      } else if (!want && !A.sid) {
        const last = store.get('stickerloom.ai.sid');
        if (last && A.sessions.some((s) => s.id === last) && location.hash.replace(/^#\/?/, '') !== 'agent/new') {
          A.sid = last;
          A.els.clear();
          await loadSession(last, true);
        }
      }
      paint();
      agList();
      setTimeout(() => {
        const t = $('ai-in');
        if (t && route_ === 'agent' && !A.stageOpen) t.focus({ preventScroll: true });
      }, 60);
      if (AIU.needPoll(A.sess)) startPoll();
    };
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && A.sess && AIU.needPoll(A.sess)) startPoll();
    });

    function pill() {
      const p = $('ai-pill');
      if (!p || !A.agent) return;
      const e = AIU.engine(A.agent);
      p.className = 'ai-pill' + (e.on ? ' up' : '');
      p.querySelector('span').textContent = e.label;
      p.title = e.title;
    }

    /* keep the bottom of a chat in view for a moment after it was opened: pictures and cards load and grow, and the page must not drift up and down while they do (stops at once when the person scrolls) */
    function pinBottom(sc, list) {
      if (typeof ResizeObserver === 'undefined') return;
      let stop = false;
      const off = () => {
        stop = true;
        ro.disconnect();
      };
      const ro = new ResizeObserver(() => {
        if (!stop) sc.scrollTop = sc.scrollHeight;
      });
      ro.observe(list);
      ['wheel', 'touchstart', 'keydown', 'mousedown'].forEach((e) => sc.addEventListener(e, off, { once: true, passive: true }));
      setTimeout(off, 1200);
    }
    /* ---------- painting (a keyed diff: only a message whose signature changed is rebuilt) */
    function paint() {
      const root = $('ai');
      if (!root) return;
      drawBar();
      const s = A.sess,
        msgs = (s && s.messages) || [],
        hero = !msgs.length;
      root.classList.toggle('is-hero', hero);
      $('ai-ttl').textContent = hero ? '' : s.title || '';
      const col = $('ai-col'),
        sc = $('ai-scroll');
      if (hero) {
        A.shown = A.sid;
        A.els.clear();
        if (!col.querySelector('.ai-hero'))
          col.innerHTML = `<div class="ai-hero ai-hero-in"><h1 class=ai-greet>What will you create today?</h1></div>`;
        $('ai-sugg').innerHTML = SUGG.map(
          (t) => `<button class=ai-chip data-act=agchip data-text="${AIU.esc(t)}">${AIU.esc(t)}</button>`,
        ).join('');
      } else {
        $('ai-sugg').innerHTML = '';
        let list = col.querySelector('.ai-msgs');
        if (!list) {
          col.innerHTML = '<div class=ai-msgs id=ai-msgs role=log aria-live=polite></div>';
          list = col.querySelector('.ai-msgs');
          A.els.clear();
        }
        const switched = A.shown !== A.sid; // a different chat than the one on screen: start clean, land at its bottom at once, no swipe
        if (switched) {
          list.innerHTML = '';
          A.els.clear();
          list.classList.remove('is-switched');
          void list.offsetWidth;
          list.classList.add('is-switched');
        }
        const near = switched || sc.scrollHeight - sc.scrollTop - sc.clientHeight < 160;
        let changed = false;
        /* ONE full card per batch: the newest message that carries it; an older card of the same batch is a one-line pointer (no second carousel, no sheet) */
        const last = new Map();
        msgs.forEach((m, i) =>
          (m.cards || []).forEach((c) => {
            if (c.type === 'generation' && c.generation) last.set(c.generation, i);
          }),
        );
        A.genLater = new Set();
        msgs.forEach((m, i) =>
          (m.cards || []).forEach((c) => {
            if (c.type === 'generation' && c.generation && last.get(c.generation) > i) A.genLater.add(m.id + ':' + c.generation);
          }),
        );
        msgs.forEach((m, i) => {
          const k =
            [...A.genLater].filter((x) => x.startsWith(m.id + ':')).join(',') +
            '|' +
            AIU.sig(m.cards_first ? Object.assign({}, m, { text: '' }) : m) +
            '|' +
            (A.open.has(m.id) ? 1 : 0) +
            [...A.open].filter((x) => x.startsWith(m.id + ':')).join(',') +
            ((m.chips || []).some((c) => c.setting) ? String(A.sess && A.sess.settings && A.sess.settings.allow_vlm) : '');
          let el = A.els.get(m.id);
          if (!el) {
            el = document.createElement('div');
            A.els.set(m.id, el);
            list.appendChild(el);
            changed = true;
          }
          if (el._k !== k) {
            const keep = [...el.querySelectorAll('.car-track')].map((t) => t.scrollLeft);
            el._k = k;
            el.className = 'ai-m ' + (m.role === 'user' ? 'user' : 'bot' + (m.status === 'working' ? ' working' : ''));
            el.innerHTML =
              m.role === 'user'
                ? `<div class=b>${AIU.esc(m.text)}</div>`
                : botHTML(m);
            el.querySelectorAll('.car-track').forEach((t, j) => {
              if (keep[j]) t.scrollLeft = keep[j];
              carSync(t);
            });
            changed = true;
          }
          if (m.cards_first && el._text !== m.text) {
            const text = el.querySelector('.ai-text');
            if (text) text.innerHTML = AIU.md(m.text || '');
            el._text = m.text;
            changed = true;
          }
        });
        for (const [id, el] of [...A.els])
          if (!msgs.some((m) => m.id === id)) {
            el.remove();
            A.els.delete(id);
          }
        if (switched) {
          A.shown = A.sid;
          sc.scrollTop = sc.scrollHeight;
          pinBottom(sc, list);
        } else if (changed && (near || msgs[msgs.length - 1].role === 'user'))
          requestAnimationFrame(() => sc.scrollTo({ top: sc.scrollHeight, behavior: 'smooth' }));
      }
      selChips();
      setSet();
      busyUi();
    }
    function busyUi() {
      const w = !!(A.sess && A.sess.working) || A.busy;
      const b = $('ai-send');
      if (!b) return;
      b.classList.toggle('busy', w);
      b.disabled = w || !$('ai-in').value.trim();
    }

    const {
      botHTML,
      traceHTML,
      chipHTML,
      cardHTML,
      problemHTML,
      runHTML,
      runAllowRow,
      carHTML,
      tileHTML,
      humanWhy,
      carSync,
      carDrag,
      stepCar,
    } = chatCardsInit({ A });

    /* a chip under a message: a go-ahead / action button, a suggestion, or (c.setting) a ONE-TIME switch that writes a setting through the settings route and makes no chat turn (UI/UX spec P9, P10).
   The switch reads the live setting: undecided it glows (subtle, rotating, at rest under reduced motion) on the right; decided it says "AI vision on" / "AI vision off" and flips on a press. */

    /* a sheet Python blocked: what happened in words, what was already paid, and one button that states the price (the click is the go-ahead) */

    /* the creator's run: where it stands, as a short list; what it stopped for is the message under it, with its buttons.
   The stop message carries the batch's own carousel (the backend attaches the generation card next to the run card), so the rejected stickers are visible
   where the buttons that decide about them are. The bulk pair below allows or takes back every allow-able sticker or animation of the run's batch. */

    /* the same pair the Studio has, on the creator's card, per kind: N counts what is allow-able NOW. It reads the batch's allow block (gates.allow_info) that rides on the generation card next to the run. */

    /* one tile of the chat's carousel. A blocked sticker is never dimmed and never hidden: it wears the locked red marks (hatched while it is not in the set,
   solid with a check once it was allowed by the person), its reason stays readable under it, and the allow button sits on the tile itself, per kind.
   `allow` is the batch's allow block (flow/gates.py allow_info) that the card's own data carries; without it a blocked tile says its reason and offers nothing. */

    /* ---------- carousel: arrows, dots, drag with a mouse, native swipe on touch */

    /* ---------- actions */
    ACT.agcar = (el) => {
      const t = el.closest('.car').querySelector('.car-track');
      stepCar(t, el.dataset.dir === 'next' ? 1 : -1);
    };
    ACT.agtile = (el) => {
      if (el.closest('.car-track') && el.closest('.car-track').dataset.dragged) return;
      const id = el.dataset.id;
      if (!id || id[0] === 'w') return;
      if (A.sel.has(id)) A.sel.delete(id);
      else A.sel.add(id);
      document.querySelectorAll(`.ag-tile[data-id="${CSS.escape(id)}"]`).forEach((t) => t.classList.toggle('is-sel', A.sel.has(id)));
      selChips();
    };
    ACT.agsel = () => {
      A.sel.clear();
      document.querySelectorAll('.ag-tile.is-sel').forEach((t) => t.classList.remove('is-sel'));
      selChips();
    };
    function selChips() {
      const el = $('ai-sel');
      if (!el) return;
      const n = A.sel.size;
      el.innerHTML = n
        ? `<span class=ai-selchip>${n} selected: say what to change<button data-act=agsel aria-label="Clear selection">${ic('x')}</button></span>`
        : '';
      const ta = $('ai-in');
      if (ta) ta.placeholder = n ? 'e.g. make these more energetic' : 'Make or change stickers';
    }
    ACT.agchip = (el) => agSend(el.dataset.text);
    ACT.agfill = (el) => {
      const t = $('ai-in');
      if (!t) return;
      t.value = el.dataset.text || '';
      t.dispatchEvent(new Event('input'));
      t.focus();
    };
    ACT.agedit = (el) => {
      if (typeof studioEditSticker === 'function') studioEditSticker(+String(el.dataset.g).replace(/\D/g, ''), +el.dataset.i, 'agent');
    }; /* the editor opens at once on that slice (no chat turn); its Save lands back here */
    ACT.agsetting = async (el) => {
      try {
        await saveSet(JSON.parse(el.dataset.set));
      } catch (e) {
        return;
      }
      paint();
    }; /* a one-time switch: the setting is saved and nothing is sent to the chat */
    ACT.agaction = (el) => {
      const a = { type: el.dataset.type };
      if (el.dataset.stage) {
        a.stage = el.dataset.stage;
        a.text = el.dataset.text || '';
        if (el.dataset.force) a.force = true;
      }
      if (el.dataset.g) a.generation = el.dataset.g;
      if (el.dataset.to) a.to = /^\d+$/.test(el.dataset.to) ? +el.dataset.to : el.dataset.to;
      if (el.dataset.i) {
        try {
          a.indexes = JSON.parse(el.dataset.i);
        } catch (e) {}
      }
      agSend('', a);
    };
    ACT.agcut = async (el) => {
      const r = await post(`/api/generations/${el.dataset.g}/recut`);
      if (!r.ok) return toast(r.j.error || 'Could not cut the sheet', 1);
      toast('Cutting the sheet…');
      if (A.sid) await loadSession(A.sid, true);
      startPoll();
    };
    ACT.aganimal = async (el) => {
      const n = +String(el.dataset.g || '').replace(/\D/g, '');
      if (!n) return;
      const r = await post(`/api/generations/${n}/animate`, { scope: 'slice', index: +el.dataset.i });
      if (!r.ok) return toast(r.j.error || 'Could not animate', 1);
      toast('Animating again');
      if (A.sid) await loadSession(A.sid, true);
      startPoll();
    };
    ACT.agretry = (el) => agSend('', { type: 'retry_sheet', generation: el.dataset.g });
    ACT.agtrace = (el) => {
      const k = el.dataset.m;
      if (A.open.has(k)) A.open.delete(k);
      else A.open.add(k);
      paint();
    };
    ACT.agstep = (el) => {
      const k = el.dataset.k;
      if (A.open.has(k)) A.open.delete(k);
      else A.open.add(k);
      paint();
    };
    ACT.agstudio = (el) => {
      const n = +String(el.dataset.g).replace(/\D/g, '');
      if (typeof SES !== 'undefined') {
        SES = { prompt: '', gens: [n], off: [], pack: '' };
        if (typeof saveSes === 'function') saveSes();
      }
      location.hash = '#/studio';
    };
    ACT.agallow = async (el) => {
      const gid = el.dataset.g,
        n = +String(gid || '').replace(/\D/g, '');
      if (!n) return;
      const r = await post(`/api/generations/${n}/allow`, {
        kind: el.dataset.kind === 'still' ? 'still' : 'animation',
        index: +el.dataset.i,
        allow: el.dataset.allow !== '0',
      });
      if (!r.ok) return toast(r.j.error || 'Could not change it', 1);
      toast(el.dataset.allow === '0' ? 'The permission is taken back' : 'Used anyway: cutting it again…');
      if (A.sid) await loadSession(A.sid, true);
      startPoll();
    };
    ACT.agallowall = async (el) => {
      const gid = el.dataset.g,
        n = +String(gid || '').replace(/\D/g, '');
      if (!n) return;
      const allow = el.dataset.allow === '1',
        kind = ['still', 'video_sheet'].includes(el.dataset.kind) ? el.dataset.kind : 'animation';
      if (el.disabled) return;
      const row = el.closest('.run-alw'),
        btns = row ? [...row.querySelectorAll('button[data-act=agallowall]')] : [el];
      btns.forEach((b) => {
        b.disabled = true;
        b.setAttribute('aria-busy', 'true');
      });
      el.textContent = 'Cutting again…';
      try {
        const r = await post(`/api/generations/${n}/allow`, { all: true, allow, kind });
        if (!r.ok) return toast(r.j.error || 'Could not change it', 1);
        toast(
          allow
            ? `Allowed ${r.j.indexes.length}: cutting their ${kind === 'still' ? 'pictures' : 'animations'}…`
            : `Took back ${r.j.indexes.length}`,
        );
        if (A.sid) await loadSession(A.sid, true);
        startPoll();
      } finally {
        btns.forEach((b) => {
          if (b.isConnected) {
            b.disabled = false;
            b.removeAttribute('aria-busy');
          }
        });
      }
    };
    /* the carousel's tiles are figures: Enter or Space on a focused one selects it like a click (a figure answers neither key by itself) */
    document.addEventListener('keydown', (e) => {
      const t = e.target;
      if (
        (e.key === 'Enter' || e.key === ' ') &&
        t &&
        t.dataset &&
        t.dataset.act === 'agtile' &&
        String(t.tagName || '').toLowerCase() === 'figure'
      ) {
        e.preventDefault();
        ACT.agtile(t, e);
      }
    });
    ACT.agnew = () => {
      A.sid = null;
      A.sess = null;
      A.sel.clear();
      A.els.clear();
      store.set('stickerloom.ai.sid', '');
      const c = $('ai-col');
      if (c) c.innerHTML = '';
      history.replaceState(null, '', '#/agent');
      paint();
      agList();
      loadAgent().catch(() => {});
      const t = $('ai-in');
      if (t) t.focus();
    };
    ACT.agopen = (el) => {
      location.hash = '#/agent/' + el.dataset.id;
    };
    const {
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
    } = chatStageInit({
      A,
      store,
      loadSessions: (...args) => loadSessions(...args),
      loadAgent: (...args) => loadAgent(...args),
      loadModels: (...args) => loadModels(...args),
      engRedraw: (...args) => engRedraw(...args),
      engSync: (...args) => engSync(...args),
      loadSession: (...args) => loadSession(...args),
      ensureSession: (...args) => ensureSession(...args),
      agList: (...args) => agList(...args),
    });

    /* the sheet size in the bar is ONE chip that flips on click (it used to be a native <select>, and .ag-sel in the settings panel is display:block;width:100%, which stretched this chip over its own row) */

    /* the sheet size in the bar is one chip that flips on click; the settings panel keeps its own side-by-side pair (ai-seg) below */
    /* ---------- under the box: what the next sheet will be made with, one click from changing it (the same settings as the gear, and the style tiles the Studio has, smaller) */

    /* the stage: a pill that opens the slider. A pick writes settings.stage through the settings route and makes no chat turn. Keyboard: Enter on the pill
   opens it with the current stop focused, Left / Right move along the slider (it stays open), Enter picks and closes, Esc closes. Opening the stage does not fetch model catalogs. */

    /* the range: dragging moves the thumb and names the stop; letting go with the pointer picks it and closes the panel; the arrow keys pick and keep it open (Enter closes) */

    /* the AI engine row: Auto keeps a working backend and only a failed call switches it; Local / Cloud are used as chosen. A backend that is not available says why. */

    /* the local model, part of the engine row: a dropdown of every model the local server lists (LM Studio, vLLM: GET /api/llm/models), the one in use selected, and one line saying why when it cannot answer.
   A pick is POST /api/ai/backend {model}; the first answer after a switch can take a moment while the server loads it. */

    /* a model selector: the chat's own pick (settings.models), "" = the server's default again */

    /* the stage range's pick (after the model drop-downs' listener, which tests take as the first) */

    /* what the Studio calls: rows(source) draws the same engine row and model row from its own source; ensure() reads the local server's models once; load() reads them again */

    /* the agentic creator's bypass. How far a request goes is the stage pill beside Send (it replaced the creator's on / scope switches); the Animation and Export
   stages run the creator, and this is whether it stops at each approval. Any rejection still stops it. */

    /* ---------- sending and polling */
    async function agSend(text, action) {
      text = (text || '').trim();
      if (!text && !action) return;
      if (A.busy || (A.sess && A.sess.working)) return toast('Still working on your last message.');
      const sid = await ensureSession();
      if (!sid) return;
      A.busy = true;
      busyUi();
      const r = await post(`/api/chat/sessions/${sid}/messages`, { text, action, selected: [...A.sel] });
      if (!r.ok) {
        A.busy = false;
        busyUi();
        return toast(r.status === 409 ? 'Still working on your last message.' : r.j.error || 'Could not send', 1);
      }
      A.sel.clear();
      selChips();
      A.setOpen = false;
      setSet();
      A.engineOpen = false;
      setEngine();
      try {
        await loadSession(sid, true);
      } catch (e) {} // a paint error must not stop this turn from being polled
      startPoll();
    }
    /* while a turn is working the page listens to GET /api/chat/sessions/{id}/stream (SSE: `turn` each time the last message changes, then `done`) instead of
   downloading the whole session every 700 ms; polling stays as the fallback (no EventSource, a dropped stream) and for what keeps running after the turn (jobs, cards) */
    function startStream() {
      const sid = A.sid;
      if (A.es) A.es.close();
      const es = (A.es = new EventSource('/api/chat/sessions/' + sid + '/stream'));
      es.addEventListener('turn', (ev) => {
        if (A.sid !== sid) {
          es.close();
          return;
        }
        let t;
        try {
          t = JSON.parse(ev.data);
        } catch (e) {
          return;
        }
        if (!A.sess) return;
        const ms = A.sess.messages || (A.sess.messages = []);
        if (t.message) {
          if (ms.length === t.count) ms[ms.length - 1] = t.message;
          else if (ms.length === t.count - 1) ms.push(t.message);
          else {
            loadSession(sid, true);
            return;
          }
        }
        A.sess.working = t.working;
        A.busy = t.working;
        try {
          paint();
        } catch (e) {}
      });
      es.addEventListener('done', async () => {
        es.close();
        if (A.es === es) A.es = null;
        A.busy = false;
        let s = null;
        try {
          s = await loadSession(sid, true);
        } catch (e) {}
        busyUi();
        if (s && AIU.needPoll(s)) {
          A.noStream = 1;
          startPoll();
          A.noStream = 0;
        } else {
          A.since = 0;
          await loadSessions();
          agList();
        }
      });
      es.onerror = () => {
        es.close();
        if (A.es === es) A.es = null;
        A.noStream = 1;
        startPoll();
        A.noStream = 0;
      };
    }
    function startPoll() {
      clearTimeout(A.poll);
      if (A.sid && !A.noStream && typeof EventSource !== 'undefined' && (A.busy || (A.sess && A.sess.working))) return startStream();
      A.since = A.since || Date.now();
      const tick = async () => {
        if (route_ !== 'agent' || document.hidden || !A.sid) {
          A.busy = false;
          return;
        }
        let s = null;
        try {
          s = await loadSession(A.sid, true);
        } catch (e) {
          s = null;
        } // one failed frame must never stop the polling (a paint error used to freeze the chat at "Thinking")
        if (!s) {
          if (A.sid) {
            A.busy = false;
            busyUi();
            A.poll = setTimeout(tick, 2500);
          }
          return;
        } // a failed read: try again (a 404 has already cleared A.sid)
        if (AIU.needPoll(s)) {
          A.busy = !!s.working;
          A.poll = setTimeout(tick, s.working ? 700 : Date.now() - A.since > 120000 ? 4000 : 1600);
        } else {
          A.busy = false;
          A.since = 0;
          busyUi();
          await loadSessions();
          agList();
        }
      };
      A.poll = setTimeout(tick, 500);
    }

    /* ---------- the second column: chats (the shared column; on narrow screens it is the shell's drawer, app.js) */
    function listHTML() {
      const rows = A.sessions
        .map(
          (s) =>
            `<div class="sess-row${s.id === A.sid ? ' on' : ''}" data-act=agopen data-id="${s.id}"><div class=t><b>${AIU.esc(s.title || 'New chat')}</b><small>${s.subjects.length ? AIU.esc(s.subjects.slice(0, 3).join(', ')) + ' · ' : ''}${AIU.rel(s.updated)}</small></div><button class=x data-act=agdel data-id="${s.id}" aria-label="Delete chat">${ic('trash')}</button></div>`,
        )
        .join('');
      return `<button class=sess-new data-act=agnew>${ic('plus')}New chat</button>${rows || '<div class=mut style="padding:14px 18px">Your chats will be here.</div>'}`;
    }
    window.agList = function agList() {
      if (route_ !== 'agent') return;
      const c2 = $('col2');
      if (c2) c2.innerHTML = `<div class=c2h><h1>Chats</h1></div><div class=c2l>${listHTML()}</div>`;
    };
    const agList = window.agList;
  })();
}
