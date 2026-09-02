// mayhem-tuesday-state — shared state + private dataset for the Tuesday Run board
// KV binding: TUE_STATE   Vars: PAGE_KEY, ADMIN_SECRET
// Storage: two single keys (KV list() is eventually consistent — never per-customer keys)
//   tue:data  = { run_date, week_label, kpis, collections[], sections[] , built }
//   tue:state = { customers: { "<name>": { step, notes:[{date,text,who}], removed:bool } }, updated }

const J = (o, s = 200) =>
  new Response(JSON.stringify(o), {
    status: s,
    headers: {
      'content-type': 'application/json;charset=utf-8',
      'cache-control': 'no-store',
      'access-control-allow-origin': '*',
      'access-control-allow-headers': 'content-type,x-tue-key,x-tue-admin',
      'access-control-allow-methods': 'GET,POST,OPTIONS',
    },
  });

const today = () =>
  new Date().toLocaleDateString('en-CA', { timeZone: 'America/Denver' });

async function getState(env) {
  const raw = await env.TUE_STATE.get('tue:state');
  const s = raw ? JSON.parse(raw) : { customers: {} };
  if (!s.customers) s.customers = {};
  return s;
}
async function putState(env, s) {
  s.updated = new Date().toISOString();
  await env.TUE_STATE.put('tue:state', JSON.stringify(s));
}
function slot(s, who) {
  if (!s.customers[who]) s.customers[who] = { step: 'Not started', notes: [] };
  if (!Array.isArray(s.customers[who].notes)) s.customers[who].notes = [];
  return s.customers[who];
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const p = url.pathname.replace(/\/+$/, '') || '/';
    const pageOk = (r) =>
      (url.searchParams.get('k') || r.headers.get('x-tue-key')) === env.PAGE_KEY;
    const adminOk = (r) => r.headers.get('x-tue-admin') === env.ADMIN_SECRET;

    if (req.method === 'OPTIONS') return J({ ok: 1 });
    if (p === '/health') return J({ ok: 'ok', ts: new Date().toISOString() });

    // ---- read everything the board needs in one shot ----
    if (p === '/all' && req.method === 'GET') {
      if (!pageOk(req)) return J({ error: 'no key' }, 401);
      const [d, s] = await Promise.all([
        env.TUE_STATE.get('tue:data'),
        getState(env),
      ]);
      return J({ data: d ? JSON.parse(d) : null, state: s });
    }

    // ---- state only (used by the weekly build before it pushes) ----
    if (p === '/state' && req.method === 'GET') {
      if (!pageOk(req) && !adminOk(req)) return J({ error: 'no key' }, 401);
      return J(await getState(env));
    }

    // ---- weekly dataset push ----
    if (p === '/data' && req.method === 'POST') {
      if (!adminOk(req)) return J({ error: 'admin only' }, 401);
      let body;
      try { body = await req.json(); } catch (e) { return J({ error: 'bad json' }, 400); }
      if (!body || !body.run_date) return J({ error: 'run_date required' }, 400);
      body.built = new Date().toISOString();
      await env.TUE_STATE.put('tue:data', JSON.stringify(body));
      return J({ ok: 1, run_date: body.run_date });
    }

    // ---- chase step ----
    if (p === '/step' && req.method === 'POST') {
      if (!pageOk(req)) return J({ error: 'no key' }, 401);
      const b = await req.json();
      if (!b.customer) return J({ error: 'customer required' }, 400);
      const s = await getState(env);
      const c = slot(s, b.customer);
      c.step = String(b.step || 'Not started').slice(0, 40);
      c.ts = new Date().toISOString();
      c.by = String(b.who || '').slice(0, 20);
      await putState(env, s);
      return J({ ok: 1, customer: b.customer, step: c.step });
    }

    // ---- append a dated note (never overwrites) ----
    if (p === '/note' && req.method === 'POST') {
      if (!pageOk(req)) return J({ error: 'no key' }, 401);
      const b = await req.json();
      const text = String(b.text || '').trim().slice(0, 1200);
      if (!b.customer || !text) return J({ error: 'customer + text required' }, 400);
      const s = await getState(env);
      const c = slot(s, b.customer);
      c.notes.push({
        date: b.date || today(),
        text,
        who: String(b.who || '').slice(0, 20),
      });
      if (/remove from tuesday list/i.test(text)) c.removed = true;
      c.ts = new Date().toISOString();
      await putState(env, s);
      return J({ ok: 1, notes: c.notes });
    }

    // ---- delete one note by index (typos) ----
    if (p === '/note/delete' && req.method === 'POST') {
      if (!pageOk(req)) return J({ error: 'no key' }, 401);
      const b = await req.json();
      const s = await getState(env);
      const c = slot(s, b.customer);
      const i = Number(b.idx);
      if (!(i >= 0 && i < c.notes.length)) return J({ error: 'bad idx' }, 400);
      c.notes.splice(i, 1);
      c.removed = c.notes.some((n) => /remove from tuesday list/i.test(n.text));
      await putState(env, s);
      return J({ ok: 1, notes: c.notes });
    }

    // ---- park / un-park a customer (rule 7, as a button) ----
    if (p === '/remove' && req.method === 'POST') {
      if (!pageOk(req)) return J({ error: 'no key' }, 401);
      const b = await req.json();
      const s = await getState(env);
      const c = slot(s, b.customer);
      const on = b.removed !== false;
      c.removed = on;
      c.notes.push({
        date: b.date || today(),
        text: on
          ? 'Remove from Tuesday list' + (b.reason ? ' — ' + String(b.reason).slice(0, 300) : '')
          : 'Put back on the Tuesday list',
        who: String(b.who || '').slice(0, 20),
      });
      await putState(env, s);
      return J({ ok: 1, removed: c.removed });
    }

    // ---- bulk state import (migration / repair) ----
    if (p === '/state/import' && req.method === 'POST') {
      if (!adminOk(req)) return J({ error: 'admin only' }, 401);
      const b = await req.json();
      if (!b || !b.customers) return J({ error: 'customers required' }, 400);
      const s = b.replace ? { customers: {} } : await getState(env);
      for (const [name, v] of Object.entries(b.customers)) {
        const c = slot(s, name);
        if (v.step) c.step = v.step;
        const seen = new Set(c.notes.map((n) => n.date + '|' + n.text));
        for (const n of v.notes || []) {
          const k = n.date + '|' + n.text;
          if (!seen.has(k)) { c.notes.push(n); seen.add(k); }
        }
        c.notes.sort((a, z) => String(a.date).localeCompare(String(z.date)));
        c.removed = c.notes.some((n) => /remove from tuesday list/i.test(n.text));
      }
      await putState(env, s);
      return J({ ok: 1, customers: Object.keys(s.customers).length });
    }

    return J({ error: 'not found', path: p }, 404);
  },
};
