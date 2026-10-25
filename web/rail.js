// The case rail: every case (GET /api/dashboard) down the left of the case page and the new-case chat (a strip on
// phones), cases that need the patient first. Each page sets Rail.active and Rail.pick; the case page also sets
// Rail.live so the open case shows what its live stream says rather than the last poll.
// Each case has a ⋯ menu: stop its agents, archive it (into a folded "Archived" list at the end) or delete it.
const Rail = (() => {
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const money = (n) => '$' + Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
  // status -> [label, dot]
  const STATUS = {
    created: ['Starting', 'live'], reviewing: ['Reviewing', 'live'], deciding: ['Planning', 'live'], awaiting_call_approval: ['Needs your OK', 'amber'],
    calling: ['On a call', 'live'], planning: ['Planning', 'live'], done: ['Resolved', 'green'], interrupted: ['Interrupted', ''], error: ['Error', ''],
  };
  const savedOf = (c) => Math.max(0, (c.original || 0) - (c.current_balance || 0)) + (c.refund || 0);
  // needs the patient: a call or a call plan waiting for approval, or a follow-up past due
  function needsYou(c) {
    if (c.status === 'awaiting_call_approval') return 'Approve the call';
    if (c.needs_plan_ok) return 'Approve the call plan';
    if (c.next && c.next.overdue) return 'Follow-up overdue';
    return '';
  }
  function statusOf(c) {
    if (c.status === 'done' && !c.confirmed) return ['Call done', 'amber'];  // billing hasn't confirmed the lower balance yet
    return STATUS[c.status] || [c.status, ''];
  }
  const ACTIVE = new Set(['created', 'reviewing', 'deciding', 'awaiting_call_approval', 'planning']);  // stoppable (not mid-call)
  const R = {
    cases: [], active: null, list: null, newPage: false,  // newPage: this is the new-case chat (its item is current)
    showArchived: false,
    pick: (id) => { location.href = `case.html?id=${encodeURIComponent(id)}`; },
    live: null,  // (case) => [label, dot] | null: the open case's live stage, when it differs from the poll
    byId: (id) => R.cases.find((c) => c.id === id),
    needsYou, savedOf,
    async load() {
      const list = await (await fetch('/api/dashboard')).json();
      // newest first from the server; what needs you floats to the top (stable sort keeps the rest in order)
      R.cases = [...list].sort((a, b) => !needsYou(a) - !needsYou(b));
      return R.cases;
    },
    render() {
      const el = R.list || (R.list = document.getElementById('tabList'));
      if (!el) return;
      // "New case" is always the first item (pinned at the left of the phone strip) so it never scrolls out of reach
      const add = `<li class="rail-new"><a class="rc rc-new" href="new.html"${R.newPage ? ' aria-current="page"' : ''}>
          <span class="material-symbols-rounded" aria-hidden="true">add</span><span class="rc-name">New case</span></a></li>`;
      const item = (c) => {
        const on = c.id === R.active, saved = savedOf(c);
        // the open case's own stream may still be playing earlier steps: then it, not the poll, says what's needed
        const live = R.live && R.live(c), [label, dot] = live || statusOf(c), need = live ? '' : needsYou(c);
        return `<li class="rc-item"><button type="button" class="rc${need ? ' needs' : ''}" data-tab="${esc(c.id)}"${on ? ' aria-current="page"' : ''}>
          <span class="rc-top"><span class="rc-name">${esc(c.provider)}</span>${saved ? `<span class="rc-saved">−${money(saved)}</span>` : ''}</span>
          <span class="rc-sub"><span class="dot ${dot}"></span><span>${esc(label)} · ${esc(c.patient)}</span></span>
          ${need ? `<span class="m3-status warn">${esc(need)}</span>` : ''}</button>
          <button type="button" class="m3-icon-btn rc-more" data-more="${esc(c.id)}" aria-haspopup="menu" aria-label="More for ${esc(c.provider)}">
            <span class="material-symbols-rounded" aria-hidden="true">more_vert</span></button></li>`;
      };
      const archived = R.cases.filter((c) => c.archived);
      const open = R.showArchived || archived.some((c) => c.id === R.active);  // the open case is never hidden
      const html = add + R.cases.filter((c) => !c.archived).map(item).join('') + (archived.length ? `
        <li class="rail-archived"><button type="button" class="rc-fold" aria-expanded="${open}">
          <span class="material-symbols-rounded" aria-hidden="true">${open ? 'expand_less' : 'inventory_2'}</span>Archived (${archived.length})</button></li>
        ${open ? archived.map(item).join('') : ''}` : '');
      if (el._html !== html) { el.innerHTML = html; el._html = html; }
      const cur = el.querySelector('[aria-current="page"]');
      if (cur && !el._scrolled) { el._scrolled = true; cur.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
    },
    async refresh() { try { await R.load(); } catch (e) { return; } R.render(); },
  };

  // ---- the ⋯ menu ----
  let menu = null, snackTimer = 0;
  function closeMenu() {
    if (!menu) return;
    const btn = menu._btn; menu.remove(); menu = null;
    if (btn && btn.isConnected) { btn.setAttribute('aria-expanded', 'false'); btn.focus(); }
  }
  function snack(text, undo) {
    document.querySelector('.rc-snack')?.remove(); clearTimeout(snackTimer);
    const el = document.createElement('div');
    el.className = 'm3-snackbar rc-snack'; el.setAttribute('role', 'status');
    el.innerHTML = `<span>${esc(text)}</span>${undo ? '<button type="button" class="m3-btn text">Undo</button>' : ''}`;
    if (undo) el.querySelector('button').onclick = () => { el.remove(); undo(); };
    document.body.appendChild(el);
    snackTimer = setTimeout(() => el.remove(), 6000);
  }
  async function act(c, what) {
    const call = (path, method, body) => fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body && JSON.stringify(body) });
    const id = encodeURIComponent(c.id);
    if (what === 'delete' && !window.confirm(`Delete the ${c.provider} case for ${c.patient}? This can't be undone.`)) return;
    let res;
    try {
      res = what === 'delete' ? await call(`/api/cases/${id}`, 'DELETE')
        : what === 'stop' ? await call(`/api/cases/${id}/stop`, 'POST')
          : await call(`/api/cases/${id}/archive`, 'POST', { archived: what === 'archive' });
    } catch (e) { snack("Couldn't reach Remy. Try again."); return; }
    if (!res.ok) { let m = ''; try { m = (await res.json()).error; } catch (e) {} snack(m || `That didn't work (${res.status})`); return; }
    if (what === 'delete' && c.id === R.active) { location.href = '/'; return; }  // its page is gone: home shows what's left
    if (R.onChange) R.onChange(c.id, what);
    await R.refresh();
    if (what === 'archive') snack('Case archived', () => act(c, 'unarchive'));
    else if (what === 'unarchive') snack('Case moved back');
    else if (what === 'stop') snack('Agents stopped');
    else snack('Case deleted');
  }
  function openMenu(btn) {
    const c = R.byId(btn.dataset.more); if (!c) return;
    closeMenu();
    const items = [
      ACTIVE.has(c.status) && ['stop', 'stop_circle', 'Stop agents'],
      c.archived ? ['unarchive', 'unarchive', 'Move back to cases'] : ['archive', 'archive', 'Archive'],
      ['delete', 'delete', 'Delete case', 'danger'],
    ].filter(Boolean);
    menu = document.createElement('div');
    menu.className = 'rc-menu'; menu.setAttribute('role', 'menu'); menu._btn = btn;
    menu.innerHTML = items.map(([k, icon, label, cls]) => `<button type="button" role="menuitem" class="${cls || ''}" data-act="${k}">
      <span class="material-symbols-rounded" aria-hidden="true">${icon}</span>${label}</button>`).join('');
    document.body.appendChild(menu);
    const r = btn.getBoundingClientRect(), w = menu.offsetWidth, h = menu.offsetHeight;
    menu.style.left = `${Math.max(8, Math.min(r.right - w, innerWidth - w - 8))}px`;
    menu.style.top = `${r.bottom + 4 + h > innerHeight - 8 ? r.top - h - 4 : r.bottom + 4}px`;
    btn.setAttribute('aria-expanded', 'true');
    menu.querySelector('button').focus();
    menu.addEventListener('click', (e) => { const b = e.target.closest('[data-act]'); if (!b) return; closeMenu(); act(c, b.dataset.act); });
    menu.addEventListener('keydown', (e) => {
      const bs = [...menu.querySelectorAll('button')], i = bs.indexOf(document.activeElement);
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); bs[(i + (e.key === 'ArrowDown' ? 1 : bs.length - 1)) % bs.length].focus(); }
      else if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); closeMenu(); }
    });
  }
  addEventListener('resize', closeMenu);
  addEventListener('scroll', closeMenu, true);

  document.addEventListener('click', (e) => {
    if (menu && !menu.contains(e.target) && !e.target.closest('.rc-more')) closeMenu();
    const more = e.target.closest('.rc-more');
    if (more) { if (menu && menu._btn === more) closeMenu(); else openMenu(more); return; }
    if (e.target.closest('.rc-fold')) { R.showArchived = !R.showArchived; R.render(); return; }
    const b = e.target.closest('.rc[data-tab]'); if (!b || b.dataset.tab === R.active) return;
    R.pick(b.dataset.tab);
  });
  return R;
})();
