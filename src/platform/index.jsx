/* Baton Platform — the operator console at /platform. Visually distinct dark theme,
   its own token (scope=platform), its own fetch wrapper. NOTHING here can show tenant
   business content — the API only serves firm metadata, subscriptions and counts. */

import { useEffect, useRef, useState } from "react";

const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";
const TOKEN_KEY = "baton.platform_token";

const C = {
  bg: "#0B1220", panel: "#121B2E", line: "#233149", ink: "#E7EDF6", mut: "#7D8FA8",
  accent: "#4FD1A5", amber: "#E8B75B", red: "#E8756B",
};

async function pfetch(path, { method = "GET", json } = {}) {
  const headers = {};
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;
  if (json !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(`${BASE}${path}`, { method, headers, body: json !== undefined ? JSON.stringify(json) : undefined });
  let data = null;
  try { data = await r.json(); } catch { /* no body */ }
  if (!r.ok) {
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail : detail?.message || r.statusText;
    const err = new Error(msg);
    err.status = r.status;
    err.code = typeof detail === "object" ? detail?.code : null;
    throw err;
  }
  return data;
}

const fmtD = (x) => (x ? new Date(x).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : "—");
const fmtDT = (x) => new Date(x).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
const fmtBytes = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${(n / 1024).toFixed(0)} KB` : `${n} B`);
const daysLeft = (end) => (end ? Math.floor((new Date(end).getTime() - Date.now()) / 86400000) : null);

const STATUS_COLORS = { trial: C.amber, active: C.accent, suspended: C.red, cancelled: C.mut };

function SubChip({ sub }) {
  if (!sub) return <span className="text-[11px] px-2 py-0.5 rounded-full border" style={{ borderColor: C.line, color: C.mut }}>no subscription</span>;
  const d = daysLeft(sub.current_period_end);
  return (
    <span className="text-[11px] px-2 py-0.5 rounded-full font-semibold" style={{ background: STATUS_COLORS[sub.status] + "22", color: STATUS_COLORS[sub.status] }}>
      {sub.plan_name} · {sub.status}{d !== null && ` · ${d >= 0 ? `${d}d left` : `${-d}d past end`}`}
    </span>
  );
}

const inputCls = "w-full rounded-md px-3 py-2 text-sm border bg-transparent";
const inputStyle = { borderColor: C.line, color: C.ink, background: "#0E1626" };

export default function PlatformApp() {
  const [authed, setAuthed] = useState(() => !!localStorage.getItem(TOKEN_KEY));
  const [mustReset, setMustReset] = useState(false);
  const [email, setEmail] = useState(localStorage.getItem("baton.platform_email") || "");

  const onLogin = (out) => {
    localStorage.setItem(TOKEN_KEY, out.access_token);
    localStorage.setItem("baton.platform_email", out.email);
    setEmail(out.email);
    setMustReset(out.must_reset);
    setAuthed(true);
  };
  const logout = () => { localStorage.removeItem(TOKEN_KEY); setAuthed(false); setMustReset(false); };

  return (
    <div className="min-h-screen font-sans antialiased" style={{ background: C.bg, color: C.ink }}>
      {!authed ? <OpLogin onLogin={onLogin} />
        : mustReset ? <OpReset onDone={(out) => onLogin(out)} logout={logout} />
        : <Console email={email} logout={logout} onAuthLost={logout} onMustReset={() => setMustReset(true)} />}
    </div>
  );
}

function Brand() {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-2xl font-bold tracking-tight" style={{ color: C.ink }}>Baton</span>
      <span className="text-2xl font-light tracking-tight" style={{ color: C.accent }}>Platform</span>
    </div>
  );
}

function OpLogin({ onLogin }) {
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    setErr("");
    try { onLogin(await pfetch("/platform/auth/login", { method: "POST", json: { email, password: pw } })); }
    catch (x) { setErr(x.message); }
  };
  return (
    <div className="min-h-screen flex items-center justify-center px-6">
      <form onSubmit={submit} className="w-full max-w-sm rounded-2xl border p-8" style={{ background: C.panel, borderColor: C.line }}>
        <Brand />
        <div className="text-xs mt-1 mb-6" style={{ color: C.mut }}>Operator console — above all tenants. Tenant logins do not work here.</div>
        <label className="block text-[11px] font-semibold mb-1" style={{ color: C.mut }}>Operator email</label>
        <input value={email} onChange={(e) => setEmail(e.target.value)} className={inputCls} style={inputStyle} autoComplete="username" />
        <label className="block text-[11px] font-semibold mb-1 mt-3" style={{ color: C.mut }}>Password</label>
        <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} className={inputCls} style={inputStyle} autoComplete="current-password" />
        {err && <div className="mt-3 text-xs" style={{ color: C.red }}>{err}</div>}
        <button type="submit" className="mt-5 w-full py-2.5 rounded-lg font-semibold text-sm" style={{ background: C.accent, color: "#08131f" }}>
          Sign in to the platform
        </button>
      </form>
    </div>
  );
}

function OpReset({ onDone, logout }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    try { onDone(await pfetch("/platform/auth/reset-password", { method: "POST", json: { new_password: pw } })); }
    catch (x) { setErr(x.message); }
  };
  return (
    <div className="min-h-screen flex items-center justify-center px-6">
      <form onSubmit={submit} className="w-full max-w-sm rounded-2xl border p-8" style={{ background: C.panel, borderColor: C.line }}>
        <Brand />
        <div className="text-xs mt-1 mb-6" style={{ color: C.amber }}>First login — set a new operator password (min 8 characters).</div>
        <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="New password" className={inputCls} style={inputStyle} />
        {err && <div className="mt-3 text-xs" style={{ color: C.red }}>{err}</div>}
        <button type="submit" disabled={pw.length < 8} className="mt-5 w-full py-2.5 rounded-lg font-semibold text-sm disabled:opacity-40" style={{ background: C.accent, color: "#08131f" }}>
          Set password & continue
        </button>
        <button type="button" onClick={logout} className="mt-3 w-full text-xs underline" style={{ color: C.mut }}>back to login</button>
      </form>
    </div>
  );
}

function Console({ email, logout, onAuthLost, onMustReset }) {
  const [tab, setTab] = useState("firms");
  const [firms, setFirms] = useState(null);
  const [detail, setDetail] = useState(null);
  const [focusSub, setFocusSub] = useState(false);
  const [creating, setCreating] = useState(false);
  const [log, setLog] = useState(null);
  const [err, setErr] = useState("");

  const guard = (e) => {
    if (e.status === 401) onAuthLost();
    else if (e.code === "MUST_RESET") onMustReset();
    else setErr(e.message);
  };
  const loadFirms = () => pfetch("/platform/firms").then(setFirms).catch(guard);
  const loadLog = () => pfetch("/platform/log").then(setLog).catch(guard);
  useEffect(() => { loadFirms(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (tab === "log") loadLog(); }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps

  const openFirm = (tid, focus = false) =>
    pfetch(`/platform/firms/${tid}`).then((d) => { setDetail(d); setFocusSub(focus); setErr(""); }).catch(guard);

  // suspend/reactivate straight from a row — same mandatory-note rule as the detail view
  const quickStatus = (f, status, label) => {
    const note = window.prompt(`${label} ${f.name} — mandatory note (logged):`);
    if (!note?.trim()) return;
    pfetch(`/platform/firms/${f.tenant_id}/subscription`, { method: "PATCH", json: { status, note: note.trim() } })
      .then(() => { setErr(""); loadFirms(); }).catch(guard);
  };

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between">
        <Brand />
        <div className="flex items-center gap-3 text-xs" style={{ color: C.mut }}>
          <span>{email}</span>
          <button onClick={logout} className="px-3 py-1.5 rounded-md border" style={{ borderColor: C.line, color: C.ink }}>Log out</button>
        </div>
      </div>
      <div className="mt-6 flex gap-1 border-b" style={{ borderColor: C.line }}>
        {[["firms", "Firms"], ["log", "Platform log"]].map(([k, l]) => (
          <button key={k} onClick={() => { setTab(k); setDetail(null); }} className="px-4 py-2 text-sm font-medium -mb-px border-b-2"
            style={tab === k && !detail ? { borderColor: C.accent, color: C.accent } : { borderColor: "transparent", color: C.mut }}>{l}</button>
        ))}
      </div>
      {err && <div className="mt-4 text-xs px-3 py-2 rounded-md" style={{ background: C.red + "22", color: C.red }}>{err}</div>}

      {detail ? (
        <FirmDetail d={detail} back={() => { setDetail(null); setFocusSub(false); loadFirms(); }}
          reload={() => openFirm(detail.tenant_id, focusSub)} guard={guard} focusSub={focusSub} />
      ) : tab === "firms" ? (
        creating ? (
          <CreateFirm guard={guard}
            done={(tid) => { setCreating(false); loadFirms(); if (tid) openFirm(tid); }}
            cancel={() => setCreating(false)} />
        ) : (
          <>
            <div className="mt-4 flex justify-end">
              <button onClick={() => setCreating(true)} className="px-4 py-2 rounded-lg text-sm font-semibold"
                style={{ background: C.accent, color: "#08131f" }}>+ Create firm</button>
            </div>
            <FirmsList firms={firms} open={openFirm} quick={quickStatus} />
          </>
        )
      ) : (
        <LogView log={log} />
      )}
      <div className="mt-10 text-[10px]" style={{ color: C.mut }}>
        Operator scope sees firm metadata, subscriptions and activity counts only — never tenant business content.
      </div>
    </div>
  );
}

function StatCell({ label, value }) {
  return (
    <span className="text-center">
      <div className="font-mono text-sm font-semibold" style={{ color: C.ink }}>{value}</div>
      <div className="text-[9px] uppercase tracking-wider" style={{ color: C.mut }}>{label}</div>
    </span>
  );
}

function RowAction({ color = null, onClick, children }) {
  return (
    <button onClick={onClick} className="px-2.5 py-1 rounded-md text-[11px] font-semibold border"
      style={{ borderColor: color || C.line, color: color || C.ink }}>{children}</button>
  );
}

function FirmsList({ firms, open, quick }) {
  if (!firms) return <div className="mt-6 text-sm" style={{ color: C.mut }}>Loading firms…</div>;
  return (
    <div className="mt-2 space-y-2">
      {firms.map((f) => (
        <div key={f.tenant_id} className="rounded-xl border p-4 flex items-center gap-4" style={{ background: C.panel, borderColor: C.line }}>
          <button onClick={() => open(f.tenant_id)} className="flex-1 min-w-0 text-left hover:brightness-110">
            <div className="font-semibold truncate">{f.name} <span className="font-normal text-xs" style={{ color: C.mut }}>({f.short})</span></div>
            <div className="text-[11px] mt-0.5 flex items-center gap-2 flex-wrap" style={{ color: C.mut }}>
              <SubChip sub={f.subscription} />
              <span>since {fmtD(f.created_at)}</span>
              <span>seats {f.seats_used}/{f.subscription?.seats_limit ?? "—"}</span>
            </div>
          </button>
          <span className="hidden lg:flex gap-5">
            <StatCell label="active 7d" value={f.stats.active_users_7d} />
            <StatCell label="open props" value={f.stats.open_proposals} />
            <StatCell label="open duties" value={f.stats.open_duties} />
            <StatCell label="filings" value={f.stats.filings_in_progress} />
            <StatCell label="storage" value={fmtBytes(f.stats.storage_bytes)} />
          </span>
          <span className="flex flex-col sm:flex-row gap-1.5 shrink-0">
            <RowAction onClick={() => open(f.tenant_id)}>View</RowAction>
            <RowAction onClick={() => open(f.tenant_id, true)}>Subscription</RowAction>
            {f.subscription?.status === "suspended"
              ? <RowAction color={C.accent} onClick={() => quick(f, "active", "Reactivate")}>Reactivate…</RowAction>
              : <RowAction color={C.red} onClick={() => quick(f, "suspended", "Suspend")}>Suspend…</RowAction>}
          </span>
        </div>
      ))}
      {firms.length === 0 && <div className="mt-6 text-sm" style={{ color: C.mut }}>No firms yet — create the first one.</div>}
    </div>
  );
}

function FirmDetail({ d, back, reload, guard, focusSub }) {
  const sub = d.subscription;
  const subRef = useRef(null);
  useEffect(() => { if (focusSub) subRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }, [focusSub]);
  const [form, setForm] = useState({
    plan_name: sub?.plan_name || "Trial", status: sub?.status || "trial",
    seats_limit: sub?.seats_limit || 10,
    current_period_end: sub?.current_period_end ? sub.current_period_end.slice(0, 10) : "",
    note: "",
  });
  const [busy, setBusy] = useState(false);
  const patch = async (body) => {
    setBusy(true);
    try {
      await pfetch(`/platform/firms/${d.tenant_id}/subscription`, { method: "PATCH", json: body });
      await reload();
    } catch (e) { guard(e); } finally { setBusy(false); }
  };
  const save = () => patch({
    plan_name: form.plan_name, status: form.status, seats_limit: Number(form.seats_limit),
    current_period_end: form.current_period_end ? new Date(form.current_period_end + "T23:59:59Z").toISOString() : null,
    note: form.note.trim(),
  });
  const quick = (status, label) => {
    const note = window.prompt(`${label} ${d.name} — mandatory note (logged):`);
    if (note?.trim()) patch({ status, note: note.trim() });
  };

  return (
    <div className="mt-4">
      <button onClick={back} className="text-xs underline" style={{ color: C.mut }}>← All firms</button>
      <div className="mt-3 rounded-xl border p-5" style={{ background: C.panel, borderColor: C.line }}>
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="text-lg font-bold">{d.name}</h2>
          <SubChip sub={sub} />
          <span className="text-xs" style={{ color: C.mut }}>since {fmtD(d.created_at)} · {d.email}</span>
        </div>
        <div className="mt-4 grid grid-cols-3 sm:grid-cols-6 gap-3">
          <StatCell label="seats" value={`${d.seats_used}/${sub?.seats_limit ?? "—"}`} />
          <StatCell label="active 7d" value={d.stats.active_users_7d} />
          <StatCell label="clients" value={d.stats.clients} />
          <StatCell label="open proposals" value={d.stats.open_proposals} />
          <StatCell label="open duties" value={d.stats.open_duties} />
          <StatCell label="storage" value={fmtBytes(d.stats.storage_bytes)} />
        </div>

        <div ref={subRef} className="mt-5 border-t pt-4" style={{ borderColor: C.line }}>
          <div className="text-[11px] uppercase tracking-wider font-bold mb-2" style={{ color: C.mut }}>Subscription</div>
          <div className="flex gap-2 flex-wrap items-end">
            <label className="text-[10px]" style={{ color: C.mut }}>Plan
              <input value={form.plan_name} onChange={(e) => setForm({ ...form, plan_name: e.target.value })} className={inputCls + " mt-1 w-32"} style={inputStyle} /></label>
            <label className="text-[10px]" style={{ color: C.mut }}>Status
              <select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} className={inputCls + " mt-1 w-32"} style={inputStyle}>
                {["trial", "active", "suspended", "cancelled"].map((s) => <option key={s}>{s}</option>)}
              </select></label>
            <label className="text-[10px]" style={{ color: C.mut }}>Seats
              <input type="number" min="1" value={form.seats_limit} onChange={(e) => setForm({ ...form, seats_limit: e.target.value })} className={inputCls + " mt-1 w-20"} style={inputStyle} /></label>
            <label className="text-[10px]" style={{ color: C.mut }}>Period end
              <input type="date" value={form.current_period_end} onChange={(e) => setForm({ ...form, current_period_end: e.target.value })} className={inputCls + " mt-1 w-40"} style={inputStyle} /></label>
          </div>
          <div className="mt-2 flex gap-2 items-center flex-wrap">
            <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="Mandatory note — why is this changing? (appended to the platform log)" className={inputCls + " flex-1 min-w-[260px]"} style={inputStyle} />
            <button disabled={busy || !form.note.trim()} onClick={save} className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40" style={{ background: C.accent, color: "#08131f" }}>Save changes</button>
            {sub?.status !== "suspended"
              ? <button disabled={busy} onClick={() => quick("suspended", "Suspend")} className="px-3 py-2 rounded-lg text-sm font-semibold border" style={{ borderColor: C.red, color: C.red }}>Suspend…</button>
              : <button disabled={busy} onClick={() => quick("active", "Reactivate")} className="px-3 py-2 rounded-lg text-sm font-semibold border" style={{ borderColor: C.accent, color: C.accent }}>Reactivate…</button>}
          </div>
          {sub?.notes && <div className="mt-2 text-[11px]" style={{ color: C.mut }}>Last note: "{sub.notes}"</div>}
        </div>

        <div className="mt-5 border-t pt-4" style={{ borderColor: C.line }}>
          <div className="text-[11px] uppercase tracking-wider font-bold mb-2" style={{ color: C.mut }}>Operator actions on this firm</div>
          {(d.events || []).map((e, i) => (
            <div key={i} className="text-xs py-1.5 border-b last:border-0" style={{ borderColor: C.line, color: C.ink }}>
              <span className="font-mono text-[10px] mr-2" style={{ color: C.mut }}>{fmtDT(e.at)}</span>{e.text}
            </div>
          ))}
          {(d.events || []).length === 0 && <div className="text-xs" style={{ color: C.mut }}>No operator actions yet.</div>}
        </div>
      </div>
    </div>
  );
}

function CreateFirm({ done, cancel, guard }) {
  const [f, setF] = useState({ name: "", short: "", adminName: "", adminEmail: "" });
  const [sub, setSub] = useState({ plan_name: "Trial", status: "trial", seats_limit: 10, current_period_end: "" });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null); // {tenant_id, users:[{..temp_password}]}

  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const valid = f.name.trim() && f.short.trim() && f.adminName.trim() && /.+@.+\..+/.test(f.adminEmail);

  const submit = async () => {
    setBusy(true);
    try {
      const out = await pfetch("/platform/firms", {
        method: "POST",
        json: {
          firm: { name: f.name.trim(), short: f.short.trim(), email: f.adminEmail.trim() },
          // only the seed Admin — they add employees themselves in the setup wizard
          employees: [{ name: f.adminName.trim(), email: f.adminEmail.trim(), role: "Admin", signatory: true }],
          subscription: {
            plan_name: sub.plan_name.trim() || "Trial", status: sub.status,
            seats_limit: Number(sub.seats_limit) || 1,
            current_period_end: sub.current_period_end ? new Date(sub.current_period_end + "T23:59:59Z").toISOString() : null,
          },
        },
      });
      setResult(out);
    } catch (e) { guard(e); } finally { setBusy(false); }
  };

  if (result) {
    return (
      <div className="mt-4 rounded-xl border p-5" style={{ background: C.panel, borderColor: C.accent }}>
        <div className="text-sm font-bold" style={{ color: C.accent }}>Firm created — one-time temporary passwords</div>
        <div className="text-xs mt-1 mb-3" style={{ color: C.mut }}>
          Shown ONCE and never again (stored only as hashes). Copy them now — invite emails were
          sent only if email delivery is configured. Every user must reset on first login.
        </div>
        {result.users.map((u) => (
          <div key={u.id} className="flex items-center gap-3 py-1.5 border-b last:border-0 text-xs" style={{ borderColor: C.line }}>
            <span className="w-40 truncate font-semibold">{u.name}</span>
            <span className="w-16" style={{ color: C.mut }}>{u.role}</span>
            <span className="flex-1 truncate" style={{ color: C.mut }}>{u.email}</span>
            <code className="font-mono px-2 py-0.5 rounded" style={{ background: "#0E1626", color: C.amber }}>{u.temp_password}</code>
          </div>
        ))}
        <button onClick={() => done(result.tenant_id)} className="mt-4 px-4 py-2 rounded-lg text-sm font-semibold"
          style={{ background: C.accent, color: "#08131f" }}>I've copied the passwords — open the firm</button>
      </div>
    );
  }

  return (
    <div className="mt-4 rounded-xl border p-5" style={{ background: C.panel, borderColor: C.line }}>
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold">Create firm</h2>
        <button onClick={cancel} className="text-xs underline" style={{ color: C.mut }}>cancel</button>
      </div>

      <div className="mt-3 grid sm:grid-cols-2 gap-3">
        <label className="text-[10px]" style={{ color: C.mut }}>Firm name
          <input value={f.name} onChange={set("name")} className={inputCls + " mt-1"} style={inputStyle} placeholder="AlphaLedger Accounting & Tax Consultancy LLC" /></label>
        <label className="text-[10px]" style={{ color: C.mut }}>Short name
          <input value={f.short} onChange={set("short")} className={inputCls + " mt-1"} style={inputStyle} placeholder="AlphaLedger" /></label>
        <label className="text-[10px]" style={{ color: C.mut }}>Admin contact name
          <input value={f.adminName} onChange={set("adminName")} className={inputCls + " mt-1"} style={inputStyle} /></label>
        <label className="text-[10px]" style={{ color: C.mut }}>Admin email <span style={{ color: C.mut }}>(also the firm's contact email)</span>
          <input type="email" value={f.adminEmail} onChange={set("adminEmail")} className={inputCls + " mt-1"} style={inputStyle} /></label>
      </div>

      <div className="mt-2 text-[11px]" style={{ color: C.mut }}>
        The admin signs in with a one-time password, is forced to reset it, and lands in the
        setup wizard — firm details, activities, employees, roles and signatures are self-served.
      </div>

      <div className="mt-5 border-t pt-4" style={{ borderColor: C.line }}>
        <div className="text-[11px] uppercase tracking-wider font-bold mb-2" style={{ color: C.mut }}>Initial subscription</div>
        <div className="flex gap-2 flex-wrap items-end">
          <label className="text-[10px]" style={{ color: C.mut }}>Plan
            <input value={sub.plan_name} onChange={(e) => setSub({ ...sub, plan_name: e.target.value })} className={inputCls + " mt-1 w-32"} style={inputStyle} /></label>
          <label className="text-[10px]" style={{ color: C.mut }}>Status
            <select value={sub.status} onChange={(e) => setSub({ ...sub, status: e.target.value })} className={inputCls + " mt-1 w-32"} style={inputStyle}>
              <option value="trial">trial</option><option value="active">active</option>
            </select></label>
          <label className="text-[10px]" style={{ color: C.mut }}>Seats
            <input type="number" min="1" value={sub.seats_limit} onChange={(e) => setSub({ ...sub, seats_limit: e.target.value })} className={inputCls + " mt-1 w-20"} style={inputStyle} /></label>
          <label className="text-[10px]" style={{ color: C.mut }}>Period end
            <input type="date" value={sub.current_period_end} onChange={(e) => setSub({ ...sub, current_period_end: e.target.value })} className={inputCls + " mt-1 w-40"} style={inputStyle} /></label>
        </div>
        <div className="mt-1 text-[10px]" style={{ color: C.mut }}>
          Leave period end empty: trial defaults to 30 days; active runs open-ended until you set one.
        </div>
      </div>

      <button disabled={busy || !valid} onClick={submit} className="mt-5 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40"
        style={{ background: C.accent, color: "#08131f" }}>{busy ? "Creating…" : "Create firm"}</button>
    </div>
  );
}

function LogView({ log }) {
  if (!log) return <div className="mt-6 text-sm" style={{ color: C.mut }}>Loading log…</div>;
  return (
    <div className="mt-4 rounded-xl border p-4" style={{ background: C.panel, borderColor: C.line }}>
      {log.map((e, i) => (
        <div key={i} className="text-xs py-1.5 border-b last:border-0 flex gap-2" style={{ borderColor: C.line }}>
          <span className="font-mono text-[10px] shrink-0" style={{ color: C.mut }}>{fmtDT(e.at)}</span>
          <span>{e.text}</span>
        </div>
      ))}
      {log.length === 0 && <div className="text-xs" style={{ color: C.mut }}>Nothing logged yet.</div>}
    </div>
  );
}
