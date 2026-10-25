// The case rail: every case (GET /api/dashboard) down the left of the case page and the new-case chat (a strip on
// phones), cases that need the patient first. Each page sets Rail.active and Rail.pick; the case page also sets
// Rail.live so the open case shows what its live stream says rather than the last poll.
const Rail = (() => {
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const money = (n) => '$' + Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
  // status -> [label, dot]
  const STATUS = {
    created: ['Starting', 'live'], reviewing: ['Reviewing', 'live'], deciding: ['Planning', 'live'], awaiting_call_approval: ['Needs your OK', 'amber'],
    calling: ['On a call', 'live'], planning: ['Planning', 'live'], done: ['Resolved', 'green'], interrupted: ['Interrupted', ''], error: ['Error', ''],
  };
  const savedOf = (c) => Math.max(0, (c.original || 0) - (c.current_balance || 0)) + (c.refund || 0);
  // needs the patient: a call or payment plan waiting for approval, or a follow-up past due
  function needsYou(c) {
    if (c.status === 'awaiting_call_approval') return 'Approve the call';
    if (c.needs_plan_ok) return 'Approve the plan';
    if (c.next && c.next.overdue) return 'Follow-up overdue';
    return '';
  }
  function statusOf(c) {
    if (c.status === 'done' && !c.confirmed) return ['Call done', 'amber'];  // billing hasn't confirmed the lower balance yet
    return STATUS[c.status] || [c.status, ''];
  }
  const R = {
    cases: [], active: null, list: null, newPage: false,  // newPage: this is the new-case chat (its item is current)
    pick: (id) => { location.href = `case.html?id=${encodeURIComponent(id)}`; },
    live: null,  // (case) => [label, dot] | null: the open case's live stage, when it differs from the poll
    byId: (id) => R.cases.find((c) => c.id === id),
    needsYou, savedOf,
    async load() {
      const list = await (await fetch('/api/dashboard')).json();
      // newest first from the server; what needs you floats to the top (stable sort keeps the rest in order)
      R.cases = [...list].sort((a, b) => !needsYou(b) - !needsYou(a));
      return R.cases;
    },
    render() {
      const el = R.list || (R.list = document.getElementById('tabList'));
      if (!el) return;
      // "New case" is always the first item (pinned at the left of the phone strip) so it never scrolls out of reach
      const add = `<li class="rail-new"><a class="rc rc-new" href="new.html"${R.newPage ? ' aria-current="page"' : ''}>
          <span class="material-symbols-rounded" aria-hidden="true">add</span><span class="rc-name">New case</span></a></li>`;
      const html = add + R.cases.map((c) => {
        const on = c.id === R.active, saved = savedOf(c);
        // the open case's own stream may still be playing earlier steps: then it, not the poll, says what's needed
        const live = R.live && R.live(c), [label, dot] = live || statusOf(c), need = live ? '' : needsYou(c);
        return `<li><button type="button" class="rc${need ? ' needs' : ''}" data-tab="${esc(c.id)}"${on ? ' aria-current="page"' : ''}>
          <span class="rc-top"><span class="rc-name">${esc(c.provider)}</span>${saved ? `<span class="rc-saved">−${money(saved)}</span>` : ''}</span>
          <span class="rc-sub"><span class="dot ${dot}"></span><span>${esc(label)} · ${esc(c.patient)}</span></span>
          ${need ? `<span class="m3-status warn">${esc(need)}</span>` : ''}</button></li>`;
      }).join('');
      if (el._html !== html) { el.innerHTML = html; el._html = html; }
      const cur = el.querySelector('[aria-current="page"]');
      if (cur && !el._scrolled) { el._scrolled = true; cur.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
    },
    async refresh() { try { await R.load(); } catch (e) { return; } R.render(); },
  };
  document.addEventListener('click', (e) => {
    const b = e.target.closest('.rc[data-tab]'); if (!b || b.dataset.tab === R.active) return;
    R.pick(b.dataset.tab);
  });
  return R;
})();
