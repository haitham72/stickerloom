/* Chat chat-cards.js: shares the core's state through explicit callbacks. */
function chatCardsInit({ A }) {
  function botHTML(m) {
    const work = m.status === 'working',
      steps = m.steps || [];
    const tr = steps.length || work ? traceHTML(m, work) : '';
    const text =
      m.text || m.cards_first ? `<div class="ai-text${m.status === 'error' ? ' ai-err' : ''}">${AIU.md(m.text || '')}</div>` : '';
    const cards = (m.cards || []).map((c, i) => cardHTML(c, m, i)).join('');
    const chips = m.chips && m.chips.length && !work ? `<div class=ai-chips>${m.chips.map(chipHTML).join('')}</div>` : '';
    return `<div class=ai-av>${ic('ai')}</div><div class=ai-body>${tr}${m.cards_first ? cards + text : text + cards}${chips}</div>`;
  }
  function traceHTML(m, work) {
    const steps = m.steps || [],
      open = work || A.open.has(m.id);
    if (!open)
      return `<button class="tr-sum" data-act=agtrace data-m="${m.id}">${ic('chev')}<span>${AIU.esc(AIU.stepSummary(steps))}</span></button>`;
    const rows = steps.map((s) => ({ ...s }));
    const hasFinal = rows.length && rows[rows.length - 1].kind === 'final';
    if (work && !hasFinal) rows.push({ kind: 'think', label: 'Thinking' });
    const head = !work
      ? `<button class="tr-sum open" data-act=agtrace data-m="${m.id}">${ic('chev')}<span>${AIU.esc(AIU.stepSummary(steps))}</span></button>`
      : '';
    return (
      head +
      `<div class=tr>` +
      rows
        .map((s, i) => {
          const k = `${m.id}:${i}`,
            d = s.detail && s.detail.lines && s.detail.lines.length,
            isOpen = A.open.has(k);
          const cls = [
            'tr-row',
            s.kind === 'think' ? 'tr-think k-final' : 'k-' + s.kind,
            i === 0 ? 'is-first' : '',
            i === rows.length - 1 ? 'is-last' : '',
            s.status === 'error' ? 'k-error' : '',
            isOpen ? 'tr-open' : '',
          ]
            .filter(Boolean)
            .join(' ');
          const t = d
            ? `<button class=tr-t data-act=agstep data-k="${k}"><span>${AIU.esc(s.label)}</span>${ic('chev')}</button>`
            : `<span class=tr-t><span>${AIU.esc(s.label)}${s.kind === 'think' ? '<span class=dots></span>' : ''}</span></span>`;
          return `<div class="${cls}"><i class=tr-mk></i>${t}${d && isOpen ? `<div class=tr-d>${s.detail.lines.map((l) => `<div>${AIU.esc(l)}</div>`).join('')}</div>` : ''}</div>`;
        })
        .join('') +
      `</div>`
    );
  }
  function chipHTML(c) {
    if (c.setting) {
      const k = Object.keys(c.setting)[0],
        cur = A.sess && A.sess.settings ? A.sess.settings[k] : null,
        on = cur === true,
        off = cur === false,
        next = { [k]: !on };
      return `<span class="ai-vis${cur == null && c.glow ? ' glow' : ''}${on ? ' is-on' : ''}"><button class="ai-chip" data-act=agsetting data-set="${AIU.esc(JSON.stringify(next))}" aria-pressed=${on} title="${on ? 'Click to switch it off' : 'Click to switch it on'}">${AIU.esc(on ? c.on || c.label : off ? c.off || c.label : c.label)}</button></span>`;
    }
    if (c.fill != null)
      return `<button class=ai-chip data-act=agfill data-text="${AIU.esc(c.fill)}" title="Put the prompt in the box to change it">${AIU.esc(c.label)}</button>`;
    if (c.editor)
      return `<button class="ai-chip pri" data-act=agedit data-g="${AIU.esc(c.editor.generation)}" data-i=${+c.editor.index}>${AIU.esc(c.label)}</button>`;
    return c.action
      ? `<button class="ai-chip${c.action === 'confirm' || c.action === 'names_apply' ? ' pri' : ''}" data-act=agaction data-type="${AIU.esc(c.action)}"${c.generation ? ` data-g="${AIU.esc(c.generation)}"` : ''}${c.to != null ? ` data-to="${AIU.esc(c.to)}"` : ''}${c.stage ? ` data-stage="${AIU.esc(c.stage)}" data-text="${AIU.esc(c.text || '')}"${c.force ? ' data-force=1' : ''}` : ''}${c.indexes ? ` data-i="${AIU.esc(JSON.stringify(c.indexes))}"` : ''}>${AIU.esc(c.label)}</button>`
      : `<button class=ai-chip data-act=agchip data-text="${AIU.esc(c.text || c.label)}">${AIU.esc(c.label)}</button>`;
  }
  function cardHTML(c, m, i) {
    if (c.type === 'plan') {
      const last = AIU.lastBot((A.sess && A.sess.messages) || []),
        done = !(A.sess && A.sess.pending) || !last || m.id !== last.id;
      return `<div class="ai-card plan${done ? ' is-done' : ''}"><div class=ai-ch><b>${AIU.esc(c.subject)}</b><small>${c.count} stickers · ${AIU.esc(c.grid)} · ${AIU.esc(c.style)}${c.batch ? ' · ' + AIU.esc(c.batch.text) : ''}</small></div>
   <div class=plan-tags>${(c.names || []).map((n) => `<span>${AIU.esc(n)}</span>`).join('')}</div>
   <div class=plan-foot><div class=price>${c.free ? 'Free: no provider call.' : `Costs <b>${AIU.credits(c.estimate)}</b>${c.creator && c.creator.video ? ` (sheet ${+c.creator.sheet} + animation ${+c.creator.video})` : ''}${c.balance != null ? ` · balance ${+(+c.balance).toFixed(0)}` : ''}`}${c.creator ? `<br><small>${c.creator.end === 'animation' ? 'Then animated; nothing is packed or sent' : `Then straight to Telegram: ${c.creator.scope === 'video' ? 'animated' : 'static'}`}, ${c.creator.bypass ? 'approving for you, stopping at any rejection' : 'one click from you at each approval'}.</small>` : ''}${c.stage === 'prompt' ? '<br><small>Prompt stage: nothing is spent until you press Generate.</small>' : ''}</div>
    </div>${c.sheet_prompt ? `<details class=plan-prompt><summary>The sheet prompt</summary><p>${AIU.esc(c.sheet_prompt)}</p></details>` : ''}</div>`;
    } /* the go-ahead lives in the two chips under the message (Create it / Not yet), the same place as "Allow AI vision / Not now": one pair of buttons, never two */
    if (c.type === 'multi') {
      const last = AIU.lastBot((A.sess && A.sess.messages) || []),
        done = !(A.sess && A.sess.pending) || !last || m.id !== last.id;
      return `<div class="ai-card plan multi${done ? ' is-done' : ''}"><div class=ai-ch><b>${AIU.esc(c.title)}</b><small>${c.items.length} sheets · ${AIU.esc(c.grid)}</small></div>
   ${c.items
     .map(
       (
         it,
       ) => `<div class=multi-item><div class=multi-h><b>${AIU.esc(it.subject)}</b><small>${it.count} stickers · ${AIU.esc(it.style)}</small></div>${it.changes && it.changes.length ? `<div class=multi-ch>${it.changes.map((x) => `<span>${AIU.esc(x)}</span>`).join('')}</div>` : ''}
    <div class=plan-tags>${(it.names || []).map((n) => `<span>${AIU.esc(n)}</span>`).join('')}</div></div>`,
     )
     .join('')}
   ${(c.assumed || []).map((x) => `<div class=multi-assume>${AIU.esc(x)}</div>`).join('')}
   <div class=plan-foot><div class=price>${c.free ? 'Free: no provider call.' : `All together <b>${AIU.credits(c.estimate)}</b>${c.balance != null ? ` · balance ${+(+c.balance).toFixed(0)}` : ''}`}</div></div></div>`;
    }
    if (c.type === 'particles_approve')
      return `<div class="ai-card plan"><p>Approve this batch as a pack first, so the particles have a sticker to live in</p><button class="btn pri" data-act=agpapprove data-g=${AIU.esc(c.generation)}>Approve as a pack</button></div>`;
    if (c.type === 'particles_scope')
      return `<div class="ai-card plan"><b>Reactions · ${AIU.esc(c.pack)}</b><button class="btn pri" data-act=agpscope data-p=${AIU.esc(c.pack_id)} data-k=${AIU.esc(c.kind || 'drawn')} data-id=${AIU.esc(c.set || '')}>${c.kind === 'video' ? 'Video from scratch' : 'Create particles'}</button></div>`;
    if (c.type === 'effects')
      return `<div class="ai-card plan"><div class=ai-ch><b>Particle effects · ${AIU.esc(c.id)}</b><small>${AIU.esc(c.pack)} · ${c.count} stickers</small><span class=sp></span><a class=ai-link href="#/effects/${AIU.esc(c.id)}">Open effects</a></div></div>`;
    if (c.type === 'particles_plan')
      return `<div class="ai-card plan"><div class=ai-ch><b>${c.op === 'more' ? 'More reactions' : 'Particles'} · ${AIU.esc(c.op === 'more' ? c.name || c.set : c.op === 'alone' ? 'on their own' : c.pack)}</b><small>${AIU.esc(c.grid)} · ${(c.elements || []).length} particles</small></div>
   <div class=plan-tags>${(c.elements || []).map((n) => `<span>${AIU.esc(n)}</span>`).join('')}</div>
   <div class=plan-foot><div class=price>${c.estimate == null ? 'The price is shown before anything is spent.' : `<b>${AIU.credits(c.estimate)}</b>`}</div></div></div>`;
    if (c.type === 'particles')
      return `<div class="ai-card plan"><div class=ai-ch><b>Reactions · ${AIU.esc(c.name || c.set)}</b><small>${AIU.esc(c.set)}${c.drawing ? ' · drawing' : ''}</small><span class=sp></span>${c.pack_id ? `<a class=ai-link href="#/pack/${AIU.esc(c.pack_id)}">Open the pack's reaction studio</a>` : `<button class=link data-act=agpopen data-set="${AIU.esc(c.set)}">Open the reactions</button>`}</div></div>`;
    if (c.type === 'generation') {
      const st = (c.data && c.data.stickers) || [],
        ready = st.filter((x) => x.status === 'READY').length,
        gid = c.generation;
      if (gid && A.genLater && A.genLater.has(m.id + ':' + gid))
        return `<div class="ai-card gen ai-gen-ref"><div class=ai-ch><b>${AIU.esc(c.subject || 'Stickers')}</b><small>${AIU.esc(gid)} · ${ready} ready · shown in a newer message below</small><span class=sp></span><button class=ai-link data-act=agstudio data-g="${gid}">Open in Studio</button></div></div>`;
      let note = '';
      if (['FAILED', 'TIMEOUT'].includes(c.job_status))
        note = JR.controls(c.job_info || { id: c.job, status: c.job_status, error: c.job_error });
      else if (!gid) {
        note = `Drawing the sheet${c.job_stage ? ' · ' + AIU.esc(c.job_stage) : ''}…`;
      } else if (c.data && c.data.problem) note = problemHTML(c.data.problem, gid);
      else if (!st.length) note = 'Cutting the stickers…';
      else if (st.some((x) => x.status === 'PENDING')) note = 'Cutting the stickers…';
      else if (c.animating && st.some((x) => ['PENDING', 'RUNNING'].includes(x.anim_status))) note = 'Animating…';
      const meta = gid ? `${gid}${c.data && c.data.parent ? ' · from ' + c.data.parent : ''} · ${ready} ready` : '';
      return `<div class="ai-card gen"><div class=ai-ch><b>${AIU.esc(c.subject || 'Stickers')}</b><small>${c.batch ? AIU.esc(c.batch) + (meta ? ' · ' : '') : ''}${meta}</small><span class=sp></span>${gid ? `<button class=ai-link data-act=agstudio data-g="${gid}">Open in Studio</button>` : ''}</div>
   ${((c.data && c.data.video_sheets) || [])
     .filter((v) => v.status !== 'REJECTED' && SR.controls(c.data, v))
     .map((v) => SR.picture(c.data, v))
     .join(
       '',
     )}${c.data ? SR.bulk(c.data) : ''}${carHTML(st.length ? st : null, gid, (c.data && c.data.allow) || null)}${note ? `<div class=car-note>${note}</div>` : ''}</div>`;
    }
    if (c.type === 'creator') {
      const g = c.run && (m.cards || []).find((x) => x.type === 'generation' && x.generation === c.run.generation);
      return runHTML(c.run, (g && g.data && g.data.allow) || null);
    }
    if (c.type === 'stickers') {
      return `<div class="ai-card"><div class=ai-ch><b>${c.stickers.length === 1 ? 'Sticker' : 'Stickers'}</b><small>${c.stickers.length} found</small></div>${carHTML(
        c.stickers.map((x) => ({ ...x, status: 'READY', name: x.key })),
        null,
      )}</div>`;
    }
    return '';
  }
  function problemHTML(p, gid) {
    const got = p.received
      ? `The sheet was received${p.received.job ? ' (' + AIU.esc(p.received.job) + ')' : ''}${p.received.cost ? ' and paid for (' + AIU.credits(p.received.cost) + ')' : ''}; nothing is lost, this batch just has no stickers.`
      : '';
    return `<div class=ai-problem><b>${AIU.esc(p.title)}</b><span>${AIU.esc(p.why)}</span><span>${AIU.esc(p.fix)}</span><small>${got}</small>
  ${p.cut_anyway ? `<button class="ai-chip pri" data-act=agcut data-g="${AIU.esc(gid)}" title="Cut the sheet that was received, as it is, for free">Cut it anyway · free</button>` : ''}
  <button class="ai-chip${p.cut_anyway ? '' : ' pri'}" data-act=agretry data-g="${AIU.esc(gid)}">Try the sheet again · ${p.retry_estimate ? AIU.credits(p.retry_estimate) : 'price shown by the provider'}</button></div>`;
  }
  function runHTML(r, allow) {
    if (!r) return '';
    const rows = (r.steps || []).map((s) => `<li class="${s.state}"><i></i>${AIU.esc(s.label)}</li>`).join('');
    const st =
      r.status === 'running'
        ? 'Working…'
        : r.status === 'waiting'
          ? 'Waiting for you'
          : r.status === 'done'
            ? 'On Telegram'
            : r.status === 'failed'
              ? 'Failed'
              : 'Stopped';
    const why = r.stop
      ? `<div class=run-why>${AIU.esc(r.stop.why)}</div>`
      : r.waiting
        ? `<div class=run-why>${AIU.esc(r.waiting.why)}</div>`
        : '';
    return `<div class="ai-card ai-run is-${r.status}"><div class=ai-ch><b>${AIU.esc(r.subject)}</b><small>${r.scope === 'video' ? 'animated' : 'static'} pack · ${r.bypass ? 'auto-approve' : 'you approve'}</small><span class=sp></span><span class=run-st>${st}</span>
  ${r.status === 'running' ? `<button class=ai-link data-act=agaction data-type=creator_stop>Stop</button>` : ''}</div><ol class=run-steps>${rows}</ol>${why}${runAllowRow(r, allow)}</div>`;
  }
  function runAllowRow(r, al) {
    const gid = r.generation;
    if (!al || !gid || r.status === 'done') return '';
    const rows = [
      ['still', 'sticker'],
      ['animation', 'animation'],
      ['video_sheet', 'video sheet'],
    ]
      .map(([kind, what]) => {
        const a = al[kind] || { can: [], undo: [] },
          n = a.can.length,
          b = a.undo.length;
        if (!n && !b) return '';
        return `<span class=run-alw>${n ? `<button class="ai-chip pri" data-act=agallowall data-g="${AIU.esc(gid)}" data-kind=${kind} data-allow=1 title="Use every ${what} anyway that Python blocked as a judgement call">Use all anyway (${n})</button>` : ''}
   ${b ? `<button class=ai-chip data-act=agallowall data-g="${AIU.esc(gid)}" data-kind=${kind} data-allow=0 title="Block the ${what}s you allowed again">Take all back (${b})</button>` : ''}</span>`;
      })
      .join('');
    return rows ? `<div class=run-bulk>${rows}<span class=mut>or allow one sticker below</span></div>` : '';
  }
  function carHTML(st, gid, allow) {
    const tiles = st ? st : Array.from({ length: 6 }, (_, i) => ({ id: 'w' + i, index: i + 1, status: 'PENDING', wait: true }));
    const n = tiles.length;
    return `<div class=car><div class=car-vp><button class="car-nav prev" data-act=agcar data-dir=prev aria-label="Previous" disabled>${ic('prev')}</button>
  <div class=car-track tabindex=0 aria-label="Stickers, swipe or use the arrow keys">${tiles.map((s) => tileHTML(s, gid, allow)).join('')}</div>
  <button class="car-nav next" data-act=agcar data-dir=next aria-label="Next">${ic('next')}</button></div>
  <div class=car-dots>${tiles.map((_, i) => `<i class="${i === 0 ? 'on' : ''}"></i>`).join('')}</div></div>`;
  }
  function tileHTML(s, gid, allow) {
    const wait = s.wait || s.status === 'PENDING',
      id = s.id || '';
    const stB = s.status === 'FAILED' || s.still === 'REJECTED',
      anB = s.anim_status === 'FAILED' || s.anim === 'REJECTED',
      bad = stB || anB;
    const media =
      s.webm && (s.anim_status === 'READY' || !s.anim_status) && s.status === 'READY'
        ? `<video src="${AIU.esc(s.webm)}" autoplay loop muted playsinline preload=metadata></video>`
        : s.png
          ? `<img src="${AIU.esc(s.png)}" alt="${AIU.esc(s.key || '')}" draggable=false loading=lazy>`
          : '';
    const emo = Array.isArray(s.emoji) ? s.emoji.join('') : s.emoji || '';
    const A0 = (k) => (allow && allow[k]) || { can: [], allowed: [], undo: [], why: {}, final: {} };
    const btn = (k, take) =>
      `<button class="${take ? 'ai-chip' : 'ai-chip pri'}" data-act=agallow data-g="${AIU.esc(gid || '')}" data-i=${s.index} data-kind=${k} data-allow=${take ? 0 : 1}${take ? '' : ' title="Python\'s check is a judgement call: you decide. It is recorded, and you can take it back"'}>${take ? 'Take it back' : 'Use it anyway'}</button>`;
    const row = (k, blocked, rawWhy) => {
      const a = A0(k);
      if (s.index == null || !blocked) return null;
      if (a.can.includes(s.index)) return { btn: btn(k, false), whys: [a.why[s.index] || humanWhy(rawWhy)], allowed: false };
      if (a.undo.includes(s.index)) return { btn: btn(k, true), whys: ['allowed by you'], allowed: true };
      if (a.allowed.includes(s.index)) return { btn: '', whys: ['allowed by you'], allowed: true };
      const ws = [a.why[s.index] || humanWhy(rawWhy)];
      if (a.final[s.index] && ws.indexOf(a.final[s.index]) < 0) ws.push(a.final[s.index]);
      return { btn: '', whys: ws, allowed: false };
    };
    const rS = row('still', stB, s.reason),
      rA = row('animation', anB, s.anim_reason);
    const allowed = !!((rS && rS.allowed) || (rA && rA.allowed));
    const badge =
      s.still === 'APPROVED'
        ? `<span class=ag-bd title="Approved">${ic('check')}</span>`
        : allowed
          ? `<span class="ag-bd is-ok" title="Allowed by you">${ic('check')}</span>`
          : bad
            ? `<span class="ag-bd is-no" title="Rejected">${ic('x')}</span>`
            : '';
    const cap = s.wait
      ? ''
      : `<b>${s.index != null ? s.index : ''}</b><span>${AIU.esc(String(s.title || s.key || s.name || '').replace(/_/g, ' '))} ${AIU.esc(emo)}</span>`;
    const whys = [...((rS && rS.whys) || []), ...((rA && rA.whys) || [])].filter((w, i, arr) => w && arr.indexOf(w) === i);
    let btns = [rS && rS.btn, rA && rA.btn].filter(Boolean).join('');
    if (s.anim_from_previous && s.webm) {
      whys.push('the animation is from the previous picture');
      btns += `<button class=ai-chip data-act=aganimal data-g="${AIU.esc(gid || '')}" data-i=${s.index}>Animate again</button>`;
    }
    const stateWord = !bad ? '' : allowed ? ' (allowed by you)' : ' (rejected)';
    const albl = `S${s.index != null ? s.index : ''} ${String(s.title || s.key || s.name || '').replace(/_/g, ' ')}${stateWord}`;
    return `<figure class="ag-tile${A.sel.has(id) ? ' is-sel' : ''}${bad ? ' is-bad' : ''}${allowed ? ' is-allowed' : ''}${wait ? ' is-wait' : ''}" data-act=agtile data-id="${AIU.esc(id)}" tabindex="0" role="button" aria-label="${AIU.esc(albl)}"><div class=ag-ph>${media}${bad ? `<span class="ag-hatch${allowed ? ' is-on' : ''}" aria-hidden=true></span>` : ''}</div>${badge}<figcaption class=ag-nm>${cap}</figcaption>${id ? `<button class=ag-id data-act=copyid data-v="${AIU.esc(id)}" title="${AIU.esc(id)} · click to copy the id" aria-label="Copy the id ${AIU.esc(id)}">${AIU.esc(id)}</button>` : ''}
  ${whys.length ? `<div class=ag-why>${whys.map((w) => AIU.esc(w)).join('<br>')}</div>` : ''}${btns ? `<div class=ag-alw>${btns}</div>` : ''}</figure>`;
  }
  const humanWhy = (r) => String(r || 'failed').replace(/_/g, ' ');
  function carSync(t) {
    const car = t.closest('.car');
    if (!car) return;
    const tiles = t.querySelectorAll('.ag-tile');
    if (!tiles.length) return;
    const w = tiles[0].offsetWidth,
      i = AIU.nearest(t.scrollLeft, w, 12, tiles.length),
      e = AIU.atEnds(t.scrollLeft, t.clientWidth, t.scrollWidth);
    car.querySelectorAll('.car-dots i').forEach((d, k) => d.classList.toggle('on', k === i));
    const p = car.querySelector('.car-nav.prev'),
      n = car.querySelector('.car-nav.next');
    if (p) p.disabled = e.start;
    if (n) n.disabled = e.end;
  }
  function carDrag(root) {
    let d = null;
    root.addEventListener('pointerdown', (e) => {
      const t = e.target.closest && e.target.closest('.car-track');
      if (!t || e.pointerType !== 'mouse' || e.button !== 0) return;
      d = { t, x: e.clientX, l: t.scrollLeft, moved: false };
    });
    window.addEventListener('pointermove', (e) => {
      if (!d) return;
      const dx = e.clientX - d.x;
      if (!d.moved && Math.abs(dx) > 5) {
        d.moved = true;
        d.t.classList.add('is-drag');
      }
      if (d.moved) d.t.scrollLeft = d.l - dx;
    });
    window.addEventListener('pointerup', () => {
      if (!d) return;
      const t = d.t,
        moved = d.moved;
      d = null;
      if (moved) {
        t.classList.remove('is-drag');
        const w = t.querySelector('.ag-tile').offsetWidth + 12;
        t.scrollTo({ left: Math.round(t.scrollLeft / w) * w, behavior: 'smooth' });
        t.dataset.dragged = 1;
        setTimeout(() => delete t.dataset.dragged, 60);
      }
    });
    root.addEventListener('keydown', (e) => {
      const t = e.target.closest && e.target.closest('.car-track');
      if (!t) return;
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        stepCar(t, e.key === 'ArrowRight' ? 1 : -1);
      }
    });
  }
  function stepCar(t, dir) {
    const tile = t.querySelector('.ag-tile');
    if (!tile) return;
    const w = tile.offsetWidth + 12,
      per = Math.max(1, Math.floor(t.clientWidth / w));
    t.scrollBy({ left: dir * w * per, behavior: 'smooth' });
  }
  return {
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
  };
}
