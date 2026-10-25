// The demo patients every "Try an example" entry point offers, in demo order (Raj, Marcus, Rosa).
// One list on the server: remy/examples.py, served at GET /api/examples. An example whose scenario isn't on this
// server yet comes back with available: false and a note; pages show it disabled instead of failing.
window.Examples = {
  async load() {
    try {
      const r = await fetch('/api/examples');
      if (r.ok) return await r.json();
    } catch (e) {}
    return [];
  },
  // Starts the example's case; resolves to the case id. household_size isn't on a bill, so it comes from the case.
  async start(ex) {
    const q = new URLSearchParams({ scenario: ex.scenario });
    if (ex.household_size) q.set('household_size', String(ex.household_size));
    const r = await fetch('/api/cases?' + q, { method: 'POST', body: new FormData() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const { case_id } = await r.json();
    if (!case_id) throw new Error('no case id');
    return case_id;
  },
  esc: (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])),
};
