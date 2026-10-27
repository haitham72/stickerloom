/* Shared preview presentation only. Controllers own units, requests and staged mutations. */
'use strict';
const TESTCARD = {
  /* Non-modal (UX-05): no focus trap. `focus` moves focus in on open (a button launched it);
     a slider-launched card leaves focus on its slider. Escape on the card or its launcher is Cancel;
     closing returns focus to the launcher when focus was inside the card. */
  open({id, apply, cancel, stageClass = '', stageExtra = '', controls = '', focus = false}) {
    let card = document.getElementById(id);
    if (card) return card;
    const active = document.activeElement, launcher = active && active !== document.body ? active : null;
    card = document.createElement('div'); card.id = id; card.className = 'test-card'; card.tabIndex = -1;
    card.setAttribute('role', 'dialog'); card.setAttribute('aria-label', 'Test preview');
    card.innerHTML = `<div class="egcimg bg-checker ${stageClass}"><img alt="Preview">${stageExtra}<span class=egbadge>test</span><span class=spin></span></div>${controls}<div class=egccap aria-live=polite></div><div class=egcbtn><button class="btn sm pri" data-act=${apply}>Apply</button><button class="btn sm" data-act=${cancel}>Cancel</button></div>`;
    const onKey = e => { if (e.key !== 'Escape' || document.getElementById(id) !== card) return; e.preventDefault(); e.stopPropagation(); TESTCARD.cancel(card); };
    card.addEventListener('keydown', onKey);
    if (launcher) launcher.addEventListener('keydown', onKey);
    card._tc = {cancel, launcher, unbind: () => launcher && launcher.removeEventListener('keydown', onKey)};
    document.body.appendChild(card);
    if (focus) card.focus({preventScroll: true});
    return card;
  },
  cancel(card) { const b = card.querySelector(`[data-act="${card._tc.cancel}"]`); if (b && !b.disabled) b.click(); },
  close(id) {
    const card = document.getElementById(id); if (!card) return;
    const active = document.activeElement, back = !active || active === document.body || card.contains(active);
    card._tc.unbind(); card.remove();
    if (back) TESTCARD.restore(card._tc.launcher);
  },
  /* the launcher may have been redrawn while the card was open: find its replacement by its action and address */
  restore(el) {
    if (el && !el.isConnected && el.dataset && el.dataset.act) {
      const d = el.dataset, q = ['act', 'g', 'i'].filter(k => d[k] != null).map(k => `[data-${k}="${d[k]}"]`).join('');
      el = document.querySelector(q);
    }
    if (el && el.isConnected && el.focus) el.focus({preventScroll: true});
  },
  sync(card, {src, caption, busy = false, loading = false}) {
    const image = card.querySelector('img');
    if (src && image.getAttribute('src') !== src) image.src = src;
    card.querySelector('.spin').style.display = loading ? '' : 'none';
    card.querySelector('.egccap').textContent = caption;
    card.querySelectorAll('.egcbtn .btn').forEach(button => { button.disabled = busy; });
    card.setAttribute('aria-busy', String(busy || loading));
  },
  place(card, anchor, width = 320, reserve = 190) {
    const r = anchor ? anchor.getBoundingClientRect() : null, w = Math.min(width, innerWidth - 24);
    card.style.width = w + 'px';
    card.style.left = Math.max(12, Math.min(innerWidth - w - 12, r ? r.left : 12)) + 'px';
    // Controls wrap at 44px targets; the rendered height can exceed width + reserve.
    const h = Math.min(card.getBoundingClientRect().height || w + reserve, innerHeight - 24);
    card.style.top = Math.max(12, Math.min(innerHeight - h - 12, r ? r.bottom + 8 : 90)) + 'px';
  }
};
