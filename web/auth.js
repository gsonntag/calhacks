// Admin key for actions that place a call, spend credits or wipe state (remy/auth.py).
// Open any page once with ?key=<REMY_ADMIN_TOKEN>: it's kept in this browser (localStorage, so tabs the
// app opens get it too), removed from the address bar, and sent as X-Remy-Key on same-origin requests.
// Pages that only read (replays, Cases, Hospitals) work without it. A refused action asks for the key once.
(() => {
  const KEY = 'remy-admin-key';
  const get = () => { try { return localStorage.getItem(KEY) || ''; } catch (e) { return ''; } };
  const set = (v) => { try { localStorage.setItem(KEY, v); } catch (e) {} };
  const url = new URL(location.href);
  if (url.searchParams.has('key')) {
    set(url.searchParams.get('key'));
    url.searchParams.delete('key');
    history.replaceState(history.state, '', url.pathname + url.search + url.hash);
  }
  const plain = window.fetch.bind(window);
  const send = (input, init, key) => {
    const target = new URL(input instanceof Request ? input.url : String(input), location.href);
    if (target.origin !== location.origin || !key) return plain(input, init);
    const headers = new Headers(init && init.headers || (input instanceof Request ? input.headers : undefined));
    headers.set('X-Remy-Key', key);
    return plain(input, { ...init, headers });
  };
  window.fetch = async (input, init) => {
    const res = await send(input, init, get());
    if (res.status !== 401 || res.headers.get('X-Remy-Auth') !== 'required') return res;
    const key = (window.prompt('This action needs the Remy admin key:') || '').trim();
    if (!key) return res;
    set(key);
    return send(input, init, key);
  };
})();

// No-wifi demo (REMY_OFFLINE=1, remy/offline.py): a chip in the top bar on every page, so mocks are never taken
// for live data.
(() => {
  const show = () => fetch('/api/mode').then((r) => r.json()).then((m) => {
    const bar = document.querySelector('.m3-top-app-bar');
    if (!m.offline || !bar || bar.querySelector('.offline-chip')) return;
    const c = document.createElement('span');
    c.className = 'm3-status warn offline-chip';
    c.title = 'No network: sample data, scripted billing and demo voice. Nothing here is live.';
    c.textContent = 'Offline demo';
    c.style.marginLeft = 'auto';
    bar.appendChild(c);
  }).catch(() => {});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', show); else show();
})();
