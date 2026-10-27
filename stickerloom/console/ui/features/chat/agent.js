/* AI: the agentic chat. A conversation with memory whose backend is the Studio itself (the same engine, the same gates).
   Everything here is rendering: the agent (rules + a small model + a LangGraph graph) lives on the server, `/api/chat/*`.
   The pure helpers at the top are exported for node tests (tests/js/agent.test.js); the DOM half only runs in the browser. */
'use strict';
const AIU = (() => {
  const esc = (s) =>
    String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  /* **bold** and line breaks, after escaping: the model's text is never trusted as HTML */
  const md = (s) =>
    esc(s)
      .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
      .replace(/\n/g, '<br>');
  const credits = (n) => (n == null || n === 0 ? 'free' : `about ${+(+n).toFixed(1)} credit${+n === 1 ? '' : 's'}`);
  const stepSummary = (steps) => {
    const s = steps || [],
      n = s.filter((x) => x.kind !== 'final').length,
      last = s[s.length - 1];
    return `${n} step${n === 1 ? '' : 's'}${last && last.kind === 'final' ? ' · ' + last.label : ''}`;
  };
  /* index of the tile the track is showing, from its scroll position */
  const nearest = (scrollLeft, tileW, gap, n) => Math.max(0, Math.min(n - 1, Math.round(scrollLeft / (tileW + gap))));
  const atEnds = (left, width, full) => ({ start: left < 4, end: left + width >= full - 4 });
  const rel = (ts, now) => {
    const d = Math.max(0, (now ?? Date.now() / 1000) - ts);
    return d < 60
      ? 'just now'
      : d < 3600
        ? Math.floor(d / 60) + ' min'
        : d < 86400
          ? Math.floor(d / 3600) + ' h'
          : Math.floor(d / 86400) + ' d';
  };
  /* a message's signature: the page only rebuilds a message whose signature changed (so carousels keep their scroll and videos keep playing) */
  const sig = (m) =>
    JSON.stringify([
      m.status,
      m.text,
      (m.steps || []).map((s) => [s.kind, s.label, s.status, s.detail && s.detail.lines && s.detail.lines.length]),
      m.chips,
      (m.cards || []).map((c) => [
        c.type,
        c.generation,
        c.job,
        c.job_status,
        c.job_stage,
        c.job_info,
        c.animating,
        c.estimate,
        c.names && c.names.length,
        ((c.data && c.data.stickers) || c.stickers || []).map((s) => [s.id, s.status, s.still, s.anim_status, !!s.png, !!s.webm]),
        c.data && c.data.problem && c.data.problem.check,
        c.data && c.data.allow,
        c.run && [c.run.step, c.run.status, c.run.updated],
      ]),
    ]);
  /* does anything on this card still move? (then the page keeps polling) */
  const cardLive = (c) => {
    if (c.type !== 'generation') return false;
    if (c.job && !c.generation) return !['FAILED', 'TIMEOUT'].includes(c.job_status);
    const st = (c.data && c.data.stickers) || [];
    if (!st.length) return !!c.generation;
    return (
      st.some((x) => x.status === 'PENDING') ||
      (!!c.animating && st.some((x) => x.status === 'READY' && ['PENDING', 'RUNNING'].includes(x.anim_status)))
    );
  };
  const needPoll = (s) =>
    !!s &&
    (s.working || (s.creator_run && s.creator_run.status === 'running') || (s.messages || []).some((m) => (m.cards || []).some(cardLive)));
  const sid = (h) => {
    const m = /^#?\/?agent\/(S\d+)/.exec(h || '');
    return m ? m[1] : null;
  };
  /* the last assistant message: only its plan card is the live one (a pending Create belongs to the newest plan) */
  const lastBot = (ms) => {
    const a = ms || [];
    for (let i = a.length - 1; i >= 0; i--) if (a[i] && a[i].role !== 'user') return a[i];
    return null;
  };
  /* what the engine pill says (GET /api/chat/agent). A model that answers: "Local · <model>". Rules only, and WHY: a local engine that is set up but cannot answer reads "Rules only (local model not loaded)"
    and the tooltip carries the server's own reason (`agent_status.reason`), never just "Rules only". */
  const engine = (a) => {
    if (!a || !a.agent) return { on: false, rules: false, label: '…', title: '' };
    const ag = a.agent,
      st = a.agent_status || {},
      loc = (a.availability || {}).local || {},
      pref = a.preference || 'auto',
      nm = String(ag.model || '')
        .split('/')
        .pop()
        .replace(/:\d+$/, '');
    if (ag.provider !== 'none' && !st.fallback)
      return {
        on: true,
        rules: false,
        label: (pref === 'auto' ? 'Auto · ' : '') + (ag.provider === 'local' ? 'Local · ' : 'Cloud · ') + nm,
        title: `The assistant runs on ${ag.model} (${ag.provider}), your choice: ${pref}. Vision checks: ${(a.vision && a.vision.model) || 'off'}. Click to change.`,
      };
    const why = String(st.reason || loc.why || 'No language model is reachable').replace(/\.+$/, '');
    return {
      on: false,
      rules: true,
      label: pref !== 'cloud' && loc.ok === false ? 'Rules only (local model not loaded)' : 'Rules only',
      title: `${why}. The assistant still works from its rules. Click for details.`,
    };
  };
  /* the one line under the model dropdown (GET /api/llm/models): the model in use, or why the local model cannot answer, in the server's last sentence ("Load a model in LM Studio ...") */
  const modelNote = (m) => {
    if (!m) return '';
    if (m.ok) return `now: ${m.current}`;
    const why = String(m.why || '');
    return 'Local model not loaded: ' + (why.split(/\.\s+/).pop() || why);
  };
  /* The right control is only the task-stage slider. Image/video model selectors live in the left control; the LLM selector is top-right;
    ids stay the server's: emojis = Stickers, export = Telegram, api = collection API export. */
  const STAGES = [
    ['auto', 'Auto', 'each message decides', 'ai'],
    ['prompt', 'Prompt', 'plan only, free', 'text'],
    ['emojis', 'Stickers', 'sheet and stickers', 'emoji'],
    ['animation', 'Animation', 'animate the sticker', 'play'],
    ['export', 'Telegram', '+ pack and Telegram', 'telegram'],
    ['api', 'Export', '+ send to the API', 'upload'],
  ];
  const stageOf = (st) => {
    const v = st && st.stage;
    if (STAGES.some((x) => x[0] === v)) return v;
    const c = (st && st.creator) || {};
    return c.on && c.scope === 'video' ? 'export' : 'auto';
  };
  /* The shared model selector: the chat's pick, or the catalog default. */
  const modelSel = (kind, label, m) => {
    if (!m || !(m.list || []).length) return '';
    const cur = m.cur || '';
    return `<label class=sp-r><span>${esc(label)}</span><select data-agm=${kind} aria-label="${esc(label)} model"><option value=""${cur ? '' : ' selected'}>Default${m.def ? ` · ${esc(m.def)}` : ''}</option>${cur && !m.list.some((x) => x.id === cur) ? `<option value="${esc(cur)}" selected disabled>${esc(cur)} · unavailable</option>` : ''}${
      m.groups
        ? Object.entries(m.groups)
            .map(([group, list]) =>
              list.length
                ? `<optgroup label="${esc(group)}">${list.map((x) => `<option value="${esc(x.id)}"${x.id === cur ? ' selected' : ''}>${esc(x.label || x.id)}</option>`).join('')}</optgroup>`
                : '',
            )
            .join('')
        : m.list.map((x) => `<option value="${esc(x.id)}"${x.id === cur ? ' selected' : ''}>${esc(x.label || x.id)}</option>`).join('')
    }</select></label>`;
  };
  const stageHTML = (cur, open, icon = () => '') => {
    let k = STAGES.findIndex((x) => x[0] === cur);
    if (k < 0) k = 0;
    const n = STAGES[k];
    const glyph = (name) => `<span class=ag-stage-icon aria-hidden=true>${icon(name)}</span>`;
    const pill = `<button type=button class=ag-stage-btn data-act=agstage aria-haspopup=dialog aria-expanded=${!!open} aria-label="Task stage: ${esc(n[1])}. Change stage" title="How far a new request goes: ${esc(n[1])}, ${esc(n[2])}. Click to change.">${glyph(n[3])}<i aria-hidden=true>${icon('down')}</i></button>`;
    if (!open) return pill;
    const names = STAGES.map(
      ([id, l, h, name], i) =>
        `<button type=button class="ag-stop${i === k ? ' on' : ''}" data-act=agstagepick data-v=${id} aria-label="${esc(l)}" aria-pressed=${i === k} title="${esc(l)}: ${esc(h)}">${glyph(name)}</button>`,
    ).join('');
    return (
      pill +
      `<div class=ag-stage-pop role=dialog aria-label="Task stage">
   <div class=ag-slider style="--k:${k}"><input type=range class=ag-range data-agrange min=0 max=${STAGES.length - 1} step=1 value=${k} aria-label="How far a new request goes" aria-valuetext="${esc(n[1])}"><div class=ag-ticks aria-hidden=true>${STAGES.map((_, i) => `<i class="${i <= k ? 'on' : ''}"></i>`).join('')}</div></div>
   <div class=ag-names>${names}</div><p class=ag-stage-caption aria-live=polite><b>${esc(n[1])}:</b><span>${esc(n[2])}</span></p></div>`
    );
  };
  return {
    esc,
    md,
    credits,
    stepSummary,
    nearest,
    atEnds,
    rel,
    sig,
    cardLive,
    needPoll,
    sid,
    lastBot,
    engine,
    modelNote,
    STAGES,
    stageOf,
    stageHTML,
    modelSel,
  };
})();
if (typeof module !== 'undefined') module.exports = AIU;
