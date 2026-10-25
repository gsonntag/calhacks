// Ask Remy on every tab: the same button, hint and panel as the case page's agent chat (ask-remy.css), with only
// Remy answering. Remy sees every tab (remy/askremy.py): POST /api/remy/ask {question, page, case_id?}; the log of
// a tab with no case open comes from GET /api/remy/chat?page=. Load after auth.js (it sends the admin key):
//   <script src="/ask-remy.js" data-page="hospitals"></script>
// data-case-param="id": the page's ?id= is an open case (Under the hood), so Remy answers about it too.
(() => {
  const me = document.currentScript;
  const page = me.dataset.page || 'cases';
  const caseId = me.dataset.caseParam ? new URLSearchParams(location.search).get(me.dataset.caseParam) : null;
  const HINT = { cases: 'Questions about your cases?', hospitals: 'Questions about these hospitals?',
    ops: 'Questions about what the agents did?', start: 'Questions about getting started?' }[page] || 'Questions?';
  const PLACEHOLDER = { cases: 'Which bill first? What do I owe in total?', hospitals: 'Which hospital has the best discount?',
    ops: 'What did the agents just do?', start: 'What does Remy need from me?' }[page] || 'Ask anything';
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  function srcHTML(src) {
    const m = /^(https?:\S+) · (.+)$/.exec(src || '');
    if (m) return srcHTML(m[1]) + ' · ' + esc(m[2]);
    if (!/^https?:/.test(src || '')) return esc(src || '');
    let short = src; try { const u = new URL(src); short = u.hostname.replace(/^www\./, '') + (u.pathname.length > 1 ? u.pathname : ''); } catch (e) {}
    return `<a href="${esc(src)}" target="_blank" rel="noopener">${esc(short.length > 60 ? short.slice(0, 57) + '…' : short)}</a>`;
  }

  const root = document.createElement('div');
  root.innerHTML = `
<div class="chat-tip" id="arTip" role="status" hidden>
  <p><b>${esc(HINT)}</b> Ask Remy: it can see every tab.</p>
  <button type="button" id="arTipClose" aria-label="Dismiss"><span class="material-symbols-rounded" aria-hidden="true">close</span></button>
</div>
<button type="button" class="chat-fab" id="arFab" aria-expanded="false" aria-controls="arPanel">
  <span class="material-symbols-rounded" aria-hidden="true">auto_awesome</span><span id="arFabLabel">Ask Remy</span>
</button>
<section class="chat-panel" id="arPanel" role="dialog" aria-labelledby="arTitle" hidden>
  <div class="chat-head">
    <h2 id="arTitle">Ask Remy</h2>
    <button type="button" class="m3-icon-btn" id="arClose" aria-label="Close"><span class="material-symbols-rounded" aria-hidden="true">close</span></button>
  </div>
  <div class="chat-log" id="arLog" aria-live="polite"></div>
  <form class="chat-form" id="arForm">
    <input class="m3-input" id="arInput" autocomplete="off" maxlength="500" aria-label="Your message">
    <button type="submit" class="m3-btn filled" id="arSend">Send</button>
  </form>
</section>`;
  document.body.append(...root.children);
  const $ = (id) => document.getElementById(id);
  const st = { open: false, busy: false, err: '', items: [], loaded: false };

  // the hint shows until the chat is opened or dismissed, once per browser (shared with the case page)
  const TIP_KEY = 'remy-ask-tip-dismissed';
  const tipDismissed = () => { try { return localStorage.getItem(TIP_KEY) === '1'; } catch (e) { return false; } };
  const dismissTip = () => { try { localStorage.setItem(TIP_KEY, '1'); } catch (e) {} $('arTip').hidden = true; };

  function render() {
    $('arFab').setAttribute('aria-expanded', String(st.open));
    $('arFab').firstElementChild.textContent = st.open ? 'close' : 'auto_awesome';
    $('arFabLabel').textContent = st.open ? 'Close' : 'Ask Remy';
    $('arTip').hidden = st.open || tipDismissed();
    $('arPanel').hidden = !st.open;
    if (!st.open) return;
    const intro = `<ul class="chat-intro"><li class="on"><b>Remy</b> sees every tab: your cases, the hospitals, what the agents did and your profile.</li>
      <li class="note">Nothing here changes facts or takes actions. Calls and approvals happen on the case page.</li></ul>`;
    const log = st.items.map((x) => x.me
      ? `<div class="bubble-wrap me"><span class="bubble-who">You</span><span class="m3-bubble me">${esc(x.text)}</span></div>`
      : `<div class="bubble-wrap them"><span class="bubble-who">Remy</span><span class="m3-bubble them">${esc(x.text)}</span>
          ${(x.sources || []).length ? `<span class="chat-src">Source: ${x.sources.map(srcHTML).join(' · ')}</span>` : ''}</div>`).join('')
      + (st.busy ? '<div class="bubble-wrap them"><span class="bubble-who">Remy</span><span class="m3-bubble them">…</span></div>' : '')
      + (st.err ? `<p class="body-s" style="color:var(--md-error);margin:0">${esc(st.err)}</p>` : '');
    $('arLog').innerHTML = intro + log;
    $('arLog').scrollTop = 1e6;
    $('arInput').placeholder = PLACEHOLDER;
    $('arInput').disabled = $('arSend').disabled = st.busy;
  }

  async function load() {
    if (st.loaded || caseId) return;
    st.loaded = true;
    try {
      const rows = await (await fetch(`/api/remy/chat?page=${encodeURIComponent(page)}`)).json();
      st.items = rows.flatMap((r) => [{ me: true, text: r.question }, { text: r.answer, sources: r.sources }]).concat(st.items);
    } catch (e) {}
    render();
  }

  function setOpen(open) {
    if (open) { dismissTip(); load(); }
    st.open = open; render();
    if (open) $('arInput').focus(); else $('arFab').focus();
  }
  $('arTipClose').addEventListener('click', dismissTip);
  $('arFab').addEventListener('click', () => setOpen(!st.open));
  $('arClose').addEventListener('click', () => setOpen(false));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && st.open) setOpen(false); });
  $('arForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const q = $('arInput').value.trim();
    if (!q || st.busy) return;
    st.items.push({ me: true, text: q }); st.busy = true; st.err = ''; $('arInput').value = ''; render();
    try {
      const r = await fetch('/api/remy/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q, page, ...(caseId ? { case_id: caseId } : {}) }) });
      const out = await r.json();
      if (!r.ok) throw new Error(out.error || out.detail || `Error ${r.status}`);
      st.items.push({ text: out.answer, sources: out.sources });
    } catch (err) { st.err = err.message; st.items.pop(); $('arInput').value = q; }
    finally { st.busy = false; render(); $('arInput').focus(); }
  });
  render();
  if (!tipDismissed()) { $('arFab').classList.add('glow'); }
})();
