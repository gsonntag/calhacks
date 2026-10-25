// Admin key for actions that place a call, spend credits or wipe state (remy/auth.py).
// Open any page once with ?key=<REMY_ADMIN_TOKEN>: it's kept in this browser (localStorage, so tabs the
// app opens get it too), removed from the address bar, and sent as X-Remy-Key on same-origin requests.
// Pages that only read (replays, Cases, Hospitals) work without it. A refused action asks for the key once.
(() => {
  const KEY = 'remy-admin-key';
  const get = () => { try { return localStorage.getItem(KEY) || localStorage.getItem('billback-admin-key') || ''; } catch (e) { return ''; } };
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
