/* ---------- Settings */
function settingsAppearance() {
  const dark = typeof thmIsDark === 'function' ? thmIsDark() : false;
  return `<div class=card id=set-appear style="margin-top:12px"><h2>Appearance</h2><div class=row style="justify-content:space-between"><span>Theme <span class=mut>(this browser only)</span></span><div class=ai-seg role=group aria-label="Theme">${['dark', 'light'].map((v) => `<button type=button data-act=thmset data-v=${v} class="${(v === 'dark') === dark ? 'on' : ''}" aria-pressed=${(v === 'dark') === dark}>${v === 'dark' ? 'Dark' : 'Light'}</button>`).join('')}</div></div></div>`;
}

/* the section bar of Settings (redesign phase 9): a registry of every section,
   rebuilt after the render wrappers mount their cards so it only offers sections
   that are actually on the page. Rebuilding never renders: no recursion, wrapper order kept. */
let SET_SECTIONS = [];
function setNavHtml(sections) {
  return `<nav class="gseg set-nav" aria-label="Settings sections">${sections.map(([id, l]) => `<button type=button data-act=setgo data-id=${id}>${l}</button>`).join('')}</nav>`;
}
function setNavSync() {
  const nav = document.querySelector('#s-settings .set-nav');
  if (!nav) return;
  nav.outerHTML = setNavHtml(SET_SECTIONS.filter(([id]) => document.getElementById(id)));
}
RENDER.settings = async () => {
  $('s-settings').innerHTML =
    `<div class="page set-page"><div class=ph><h1>Settings</h1></div>${settingsAppearance()}<p class=mut role=status>Loading system settings…</p></div>`;
  const r = await api('/api/generations'),
    h = r.j.health || {},
    p = r.j.paths || {},
    tgc = await tgSettingsCard(),
    ai = (await api('/api/ai')).j || {},
    staff = true,
    prep = await api('/api/prepared/setting'),
    pp = prep.ok && prep.j.prefer;
  $('s-settings').innerHTML = `<div class="page set-page"><div class=ph><h1>Settings</h1></div>
  <nav class="gseg set-nav" aria-label="Settings sections">${(SET_SECTIONS = [['set-appear', 'Appearance'], ['set-health', 'System health'], ...(staff ? [['set-prep', 'Prepared sheets']] : []), ['set-ai', 'AI'], ['set-tg', 'Telegram'], ['trx-box', 'Trash']]).map(([id, l]) => `<button type=button data-act=setgo data-id=${id}>${l}</button>`).join('')}</nav>
  ${settingsAppearance()}
  <div class=card id=set-health style="margin-top:12px"><h2>System health</h2><div class=kv>
  <span>watch folder (read-only)</span><span>${esc(p.input)}</span><span>output</span><span>${esc(p.out)}</span>${
    staff
      ? `<span>ffmpeg</span><span>${esc(h.ffmpeg || 'not found')}</span>
  <span>VP9 + alpha encoder</span><span>${h.vp9 ? '<b style="color:var(--pri-d)">ready</b>' : '<b style="color:var(--bad)">missing</b>: final WEBM encodes will fail (live preview still works). Run <code>python -m stickerloom doctor</code>'}</span>`
      : ''
  }</div></div>
  ${staff ? `<div class=card id=set-prep style="margin-top:16px"><h2>Prepared sheets</h2><div class=row style="justify-content:space-between"><span>Prefer the prepared set <span class=mut>(${pp ? 'on' : 'off'}: a request that names a prepared subject is served from the watch folder, 0 credits, instead of a paid call)</span></span><button class="btn sm" data-act=prepset data-v=${pp ? '0' : '1'}>${pp ? 'Turn off' : 'Turn on'}</button></div><div class=mut><code>STICKERLOOM_PREFER_PREPARED</code> in <code>.env</code> wins over this switch when it is set.</div></div>` : ''}
  <div class=card id=set-ai style="margin-top:16px"><h2>AI expansion</h2><div id=set-ai-engine></div>${ai.configured ? `<div class=kv><span>model</span><span><b>${esc(ai.model)}</b></span></div><div class=mut>Type a subject and the AI expands it into the full set, with a key name, tags and emoji for every sticker (Studio, Prompt).</div>` : `<div class=mut>Off. Start LM Studio, or add <code>OPENAI_API_KEY=…</code> for official OpenAI, or <code>STICKERLOOM_FALLBACK_URL=…</code> for any OpenAI-compatible endpoint (plus <code>STICKERLOOM_FALLBACK_KEY</code>/<code>STICKERLOOM_FALLBACK_MODEL</code> as needed, <code>STICKERLOOM_LLM_MODEL</code>, <code>STICKERLOOM_LLM_FALLBACK</code>) to <code>.env</code> (git-ignored) and restart the server: the AI then expands a subject and names every sticker. Until then the built-in sets are used.</div>`}</div>
  <div id=set-tg>${tgc}</div><p class=mut style="margin-top:16px">Photo cutout uses the engine's chroma key for green/blue screens and OpenCV GrabCut otherwise (offline). A learned matte model is planned for Phase 3C.</p></div>`;
  if (typeof AIENG !== 'undefined')
    AIENG.settings({
      agent: { provider: ai.provider || 'none', model: ai.model || '' },
      availability: ai.availability || {},
      preference: ai.preference || 'auto',
      noModel: 'no model configured',
    });
};

/* each section button brings its card into view; the bar only offers mounted cards (setNavSync, rebuilt by the trash wrapper after it mounts) */
ACT.setgo = (el) => {
  const c = $(el.dataset.id);
  if (c) (c.closest('.card') || c).scrollIntoView({ behavior: 'smooth', block: 'start' });
};

ACT.prepset = async (el) => {
  const r = await post('/api/prepared/setting', { prefer: el.dataset.v === '1' });
  if (!r.ok) return toast(r.j.error || 'Could not change the setting', 1);
  RENDER.settings();
};

/* the theme is a browser preference (theme.js): switching it repaints at once through the data-theme attribute, no reload */
ACT.thmset = (el) => {
  if (typeof thmSet === 'function') thmSet(el.dataset.v);
};
