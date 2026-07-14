import { useState, useMemo, useEffect, useRef } from "react";
import { api, registerFile, rawFromUrl, openFileLink } from "./api.js";
import { AuthProvider, useAuth, LoginScreen, ForcedResetScreen } from "./auth.jsx";
import { DataProvider, useData } from "./state.jsx";
import { VatEngineNav, VatEngineScreen } from "./vat-engine/index.jsx"; // VAT-ENGINE (removable module — see REMOVING-VAT-ENGINE.md)
import { filterGroups, groupClientActivities, rowNeedsAttention, slimClientActivities } from "./selectors.js";

/* ------------------------------------------------------------------ */
/*  Baton — CRM & employee performance tracker for bookkeeping firms    */
/*  Scope: Module 1 + Client Onboarding Part 1 (full chain):           */
/*  proposal → requirements loop → dual signatures → proposal email →  */
/*  client-signed proposal upload (= confirmation & client conversion) */
/*  → per-activity staffing (workload-aware) → EL senior signature →   */
/*  EL email = Part 1 complete → accountant invoicing & receipts       */
/* ------------------------------------------------------------------ */

const DAY = 86400000;
const uid = () => Math.random().toString(36).slice(2, 9);

const SERVICES = [
  "Bookkeeping (Monthly)",
  "VAT Filing",
  "Corporate Tax Filing",
  "Corporate Tax Planning & Consultation",
  "ESR Filing",
  "Audit Support",
  "Financial Reporting (Quarterly)",
  "Financial Reporting (Annual)",
];

const SEED_USERS = [
  { id: "u1", name: "Khalid Al Nuaimi", role: "Admin", designation: "Managing Partner", email: "khalid@crescentbay.ae", signatory: true },
  { id: "u2", name: "Mariam Al Suwaidi", role: "Admin", designation: "Partner — Tax & Compliance", email: "mariam@crescentbay.ae", signatory: true },
  { id: "u3", name: "Imran Choudhury", role: "Manager", designation: "Engagement Manager", email: "imran@crescentbay.ae", signatory: true },
  { id: "u4", name: "Layla Haddad", role: "Manager", designation: "Client Relations Manager", email: "layla@crescentbay.ae", signatory: true },
  { id: "u5", name: "Vikram Menon", role: "Manager", designation: "Compliance Manager", email: "vikram@crescentbay.ae", signatory: true },
  { id: "u6", name: "Priya Nair", role: "Staff", designation: "Senior Accountant", email: "priya@crescentbay.ae", signatory: false, existingActivities: [{ id: "a1", client: "Al Reem Foods Trading LLC", service: "Monthly bookkeeping & reporting", cadence: "monthly", dueInDays: 5, contact: { name: "Huda Al Marzooqi", email: "huda@alreemfoods.ae" } }, { id: "a2", client: "Zenith Marine Services FZE", service: "Quarterly VAT filing", cadence: "quarterly", dueInDays: 21, contact: { name: "Rajesh Kumar", email: "rajesh@zenithmarine.ae" } }] },
  { id: "u7", name: "Omar Farouk", role: "Staff", designation: "Accountant", email: "omar@crescentbay.ae", signatory: false, existingActivities: [{ id: "a3", client: "Pearl Route Logistics LLC", service: "Monthly bookkeeping", cadence: "monthly", dueInDays: 12, contact: { name: "Tariq Aziz", email: "accounts@pearlroute.ae" } }] },
  { id: "u8", name: "Sneha Pillai", role: "Staff", designation: "VAT Executive", email: "sneha@crescentbay.ae", signatory: false, existingActivities: [{ id: "a4", client: "Al Reem Foods Trading LLC", service: "Quarterly VAT filing", cadence: "quarterly", dueInDays: 21, contact: { name: "Huda Al Marzooqi", email: "huda@alreemfoods.ae" } }, { id: "a5", client: "Oasis Interiors Est.", service: "Quarterly VAT filing", cadence: "quarterly", dueInDays: 2, contact: { name: "Lina Qassem", email: "lina@oasisinteriors.ae" } }, { id: "a6", client: "Danat Auto Spares LLC", service: "VAT registration & filing", cadence: "quarterly", dueInDays: 40, contact: { name: "Yousef Darwish", email: "finance@danatauto.ae" } }] },
  { id: "u9", name: "Ahmed Bassiouni", role: "Staff", designation: "Tax Associate", email: "ahmed@crescentbay.ae", signatory: false, existingActivities: [{ id: "a7", client: "Zenith Marine Services FZE", service: "Corporate Tax filing FY2025", cadence: "annual", dueInDays: 55, contact: { name: "Rajesh Kumar", email: "rajesh@zenithmarine.ae" } }] },
  { id: "u10", name: "Grace Fernandes", role: "Staff", designation: "Bookkeeper", email: "grace@crescentbay.ae", signatory: false, existingActivities: [{ id: "a8", client: "Oasis Interiors Est.", service: "Monthly bookkeeping", cadence: "monthly", dueInDays: 8, contact: { name: "Lina Qassem", email: "lina@oasisinteriors.ae" } }, { id: "a9", client: "Danat Auto Spares LLC", service: "Monthly bookkeeping", cadence: "monthly", dueInDays: 15, contact: { name: "Yousef Darwish", email: "finance@danatauto.ae" } }] },
  { id: "u11", name: "Noor Al Balushi", role: "Staff", designation: "Junior Accountant", email: "noor@crescentbay.ae", signatory: false },
  { id: "u12", name: "Daniel Mathews", role: "Staff", designation: "Audit Support Executive", email: "daniel@crescentbay.ae", signatory: false },
  { id: "u13", name: "Ritika Sharma", role: "Staff", designation: "Corporate Tax Analyst", email: "ritika@crescentbay.ae", signatory: false },
  { id: "u14", name: "Fatima Zahran", role: "Accountant", designation: "Finance Executive (in-house)", email: "fatima@crescentbay.ae", signatory: false },
];

const SEED_FIRM = {
  name: "Crescent Bay Accounting & Tax Consultants LLC",
  short: "Crescent Bay",
  address: "Suite 908, Amber Gem Tower, Sheikh Khalifa Bin Zayed St, Ajman, UAE",
  trn: "TRN 100-4471-8820-553",
  phone: "+971 6 748 2210",
  email: "info@crescentbay.ae",
  accent: "#14606B",
  services: [...SERVICES],
  templates: { letterhead: null, proposal: null, el: null },
};

/* Verified against the ACTUAL endpoint guards (api/app/routers/*) — the matrix mirrors
   the code. ✓* = scoped to the specific assignment (assigned drafter / assigned staff /
   engagement manager on that matter), not the role at large. */
const ROLE_COLS = ["Admin", "Manager", "Technical Staff", "In-house Accountant"];
const ROLE_MATRIX = [
  ["Proposals & engagement", [
    ["Create proposal requests", 1, 1, 0, 0],
    ["Draft & edit commercial terms (assigned drafter; signatory at senior review)", 1, 1, "*", 0],
    ["Sign — own signature only", 1, 1, 0, 0],
    ["Counter-sign & approve/reject ELs (Admin signatories)", 1, 0, 0, 0],
    ["Record client confirmation / conversion", 1, 1, 0, 0],
    ["Staff activities with workload view", 1, 1, 0, 0],
  ]],
  ["Onboarding", [
    ["Request client documents & credentials", 0, 0, "*", 0],
    ["Provide documents / answer requests (the engagement manager)", "*", "*", 0, 0],
    ["Reveal stored credentials — always logged", 1, "*", "*", 0],
    ["Complete onboarding → create recurring duty (assigned staff only)", 0, 0, "*", 0],
  ]],
  ["Duties & deadlines", [
    ["Complete duties with proof of work (whoever is assigned)", "*", "*", "*", "*"],
    ["Create / assign duties", 1, 1, 0, 0],
    ["Firm-wide compliance board", 1, 1, 0, 0],
  ]],
  ["VAT engine", [
    ["Run filings — profile interview, uploads, reconciliation, computation", 0, 0, "*", 0],
    ["Edit the client VAT profile", 1, 1, "*", 0],
    ["Approve computation dispatch to client", 0, 0, "*", 0],
    ["View all filings (and open a period read-only)", 1, 1, 0, 0],
  ]],
  ["Payments", [
    ["Raise invoices (upload + auto-email) & record receipts with references", 1, 0, 0, 1],
    ["Keep client contact details current", 1, 0, 0, 1],
    ["View payment health", 1, 1, 0, 1],
  ]],
  ["Performance", [
    ["★ Performance tab & per-client performance", 1, 1, 0, 0],
  ]],
  ["Administration", [
    ["Firm settings, service catalog, templates", 1, 0, 0, 0],
    ["Employees & roles", 1, 0, 0, 0],
    ["Signature vault", 1, 0, 0, 0],
    ["Data export", 1, 0, 0, 0],
  ]],
];

function PermMatrix({ compact = false }) {
  return (
    <>
      <table className={`w-full ${compact ? "text-[11px]" : "text-xs"}`}>
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wider" style={{ color: "var(--mut)" }}>
            <th className={compact ? "py-0.5" : "py-1"}>Permission</th>
            {ROLE_COLS.map((r) => <th key={r} className="text-center px-1 whitespace-nowrap">{r}</th>)}
          </tr>
        </thead>
        {ROLE_MATRIX.map(([header, rows]) => (
          <tbody key={header}>
            <tr>
              <td colSpan={5} className={`${compact ? "pt-1.5" : "pt-2.5"} pb-0.5 text-[10px] uppercase tracking-wider font-bold`} style={{ color: "var(--accent)" }}>{header}</td>
            </tr>
            {rows.map(([perm, ...cells], i) => (
              <tr key={i} className="border-t" style={{ borderColor: "var(--line)" }}>
                <td className={`${compact ? "py-0.5" : "py-1"} pr-2`}>{perm}</td>
                {cells.map((x, j) => (
                  <td key={j} className="text-center">
                    {x === "*"
                      ? <span title="Scoped to the specific assignment, not the role at large" style={{ color: "var(--accent)" }}>✓*</span>
                      : x ? <span style={{ color: "var(--accent)" }}>✓</span> : <span style={{ color: "var(--line)" }}>—</span>}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        ))}
      </table>
      <div className="mt-1.5 text-[10px]" style={{ color: "var(--mut)" }}>
        ✓* = scoped to the specific assignment (assigned drafter / assigned staff / engagement manager on that matter) — not the role at large. Verified against the API endpoint guards.
      </div>
    </>
  );
}

const BASIS = ["per month", "per quarter", "per annum", "one-time"];
const CADENCES = ["monthly", "quarterly", "half-yearly", "annual", "one-time"];
const dutyKind = (service) =>
  /vat/i.test(service) ? "vat" : /corporate tax|\bct\b/i.test(service) ? "ct" : /bookkeep|report|account/i.test(service) ? "report" : "other";
const EMIRATES = ["AUH", "DXB", "SHJ", "AJM", "UAQ", "RAK", "FUJ"];
const addCadence = (t, cadence) => {
  const d = new Date(t);
  const m = { monthly: 1, quarterly: 3, "half-yearly": 6, annual: 12 }[cadence];
  if (!m) return null; // one-time: no next occurrence
  d.setMonth(d.getMonth() + m);
  return d.getTime();
};
const defaultBasis = (svc) =>
  /monthly/i.test(svc) ? "per month" : /quarterly/i.test(svc) ? "per quarter" : /annual|filing|audit|esr/i.test(svc) ? "per annum" : "one-time";

const fmtDT = (t) => new Date(t).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
const fmtD = (t) => new Date(t).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
const days = (ms) => ms / DAY;
const fmtDur = (ms) => {
  const d = days(ms);
  if (d >= 1) return `${d.toFixed(1)}d`;
  const h = ms / 3600000;
  if (h >= 1) return `${h.toFixed(1)}h`;
  return `${Math.max(1, Math.round(ms / 60000))}m`;
};
const num = (x) => parseFloat(String(x).replace(/[^\d.]/g, "")) || 0;
const money = (n) => "AED " + Math.round(n).toLocaleString();
const fmtSize = (b) => (b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB");

/* human-readable field-level diff between two generated versions — audit entries
   must state exactly what changed, not merely that a change occurred */
function diffDrafts(prev, next) {
  const out = [];
  const pl = Object.fromEntries(prev.lines.map((l) => [l.service, l]));
  const nl = Object.fromEntries(next.lines.map((l) => [l.service, l]));
  next.lines.forEach((l) => {
    const o = pl[l.service];
    if (!o) { out.push(`service added: ${l.service} at AED ${num(l.fee).toLocaleString()} ${l.basis || ""}`); return; }
    if (num(o.fee) !== num(l.fee)) out.push(`${l.service}: fee AED ${num(o.fee).toLocaleString()} → AED ${num(l.fee).toLocaleString()}`);
    if ((o.basis || defaultBasis(l.service)) !== (l.basis || defaultBasis(l.service))) out.push(`${l.service}: billing basis "${o.basis || defaultBasis(l.service)}" → "${l.basis || defaultBasis(l.service)}"`);
  });
  prev.lines.forEach((l) => { if (!nl[l.service]) out.push(`service removed: ${l.service}`); });
  if ((prev.paymentTerms || "").trim() !== (next.paymentTerms || "").trim()) out.push(`payment terms: "${prev.paymentTerms}" → "${next.paymentTerms}"`);
  if (String(prev.validityDays) !== String(next.validityDays)) out.push(`validity: ${prev.validityDays} → ${next.validityDays} days`);
  if ((prev.scope || "").trim() !== (next.scope || "").trim()) out.push(`scope notes: "${prev.scope || "—"}" → "${next.scope || "—"}"`);
  return out;
}

/* The AI drafting assistant runs SERVER-SIDE in production (POST /proposals/{id}/polish-terms
   via actions.polishTerms) — the API key never reaches the browser. */
/* real file picker — files kept in browser memory for the session */
function FilePick({ label = "Choose file(s)", multiple = false, onFiles, small = false }) {
  const id = useMemo(uid, []);
  return (
    <>
      <input id={id} type="file" multiple={multiple} accept=".pdf,.png,.jpg,.jpeg,.xlsx,.docx,.csv" className="hidden"
        onChange={(e) => {
          // production: keep the raw File in the registry so actions can upload it to /files
          const fs = Array.from(e.target.files || []).map((f) => {
            const url = URL.createObjectURL(f);
            registerFile(url, f);
            return { name: f.name, size: f.size, url };
          });
          if (fs.length) onFiles(fs);
          e.target.value = "";
        }} />
      <label htmlFor={id} className={`inline-flex items-center gap-1.5 rounded-md border font-medium cursor-pointer hover:bg-gray-50 ${small ? "px-2.5 py-1.5 text-xs" : "px-3 py-2 text-sm"}`} style={{ borderColor: "var(--line)" }}>
        📎 {label}
      </label>
    </>
  );
}

const FileLink = ({ name, url, size }) => {
  // production: stored files ("api://file/{id}") open via a short-lived link from the API
  const stored = typeof url === "string" && url.startsWith("api://file/");
  return (
    <a href={stored ? "#" : url} target={stored ? undefined : "_blank"} rel="noreferrer"
       onClick={stored ? (e) => { e.preventDefault(); openFileLink(url.slice("api://file/".length)); } : undefined}
       className="underline decoration-dotted hover:decoration-solid" style={{ color: "var(--ink)" }} title="Open document">
      📄 {name}{size ? <span style={{ color: "var(--mut)" }}> · {fmtSize(size)}</span> : null}
    </a>
  );
};

const SigBlock = ({ user, at, role }) => (
  <div className="inline-block mr-10 align-top">
    <div className="font-disp italic text-lg" style={{ color: "var(--ink)" }}>{user.name.split(" ").map((x) => x[0]).join(". ")}.</div>
    <div className="border-t mt-1 pt-1 text-[11px]" style={{ borderColor: "var(--ink)", color: "var(--mut)" }}>
      <b style={{ color: "var(--ink)" }}>{user.name}</b> · {role}<br />Digitally signed {fmtDT(at)}
    </div>
  </div>
);

/* ================================================================== */

/* ================================================================== */
/*  Production entry — real auth + server data (Phase 4 storage swap). */
/*  Everything below this section is verbatim prototype UI: the        */
/*  behavioural spec (src/baton-prototype.jsx) rendered against the    */
/*  API instead of in-memory state.                                    */
/* ================================================================== */

export default function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  );
}

function Gate() {
  const auth = useAuth();

  if (auth.status === "loading")
    return (
      <Frame accent="#1E6E56">
        <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--ink)" }}>
          <div className="font-disp text-white text-2xl tracking-tight">Baton</div>
        </div>
      </Frame>
    );
  if (auth.status === "must_reset")
    return (
      <Frame accent="#1E6E56">
        <ForcedResetScreen />
      </Frame>
    );
  if (auth.status !== "authed" || !auth.me)
    return (
      <Frame accent="#1E6E56">
        <LoginScreen />
      </Frame>
    );
  // Operator-created firm, not yet set up (no activities configured): the Admin lands in
  // the wizard right after the forced reset and self-serves the whole configuration.
  if (auth.me.role === "Admin" && (auth.firm?.services || []).length === 0)
    return (
      <Frame accent="#1E6E56">
        <CompleteSetupHost />
      </Frame>
    );
  return (
    <DataProvider me={auth.me} firm={auth.firm} onFirmChanged={auth.reloadFirm}>
      <Shell />
    </DataProvider>
  );
}

/* First Admin login on an operator-created firm — the wizard UI is verbatim; completion
   POSTs /tenants/complete-setup (Admin JWT) to configure the EXISTING tenant, and shows
   the server-issued temporary credentials for NEW employees exactly once. */
function CompleteSetupHost() {
  const auth = useAuth();
  const [result, setResult] = useState(null);
  const [err, setErr] = useState("");

  const deploy = async (newFirm, newUsers) => {
    setErr("");
    try {
      const sigOf = async (u) => {
        if (!u.sigSpecimen) return null;
        if (u.sigSpecimen.type === "typed") return { type: "typed", text: u.sigSpecimen.text };
        if (u.sigSpecimen.url?.startsWith("data:")) return { type: "image", url: u.sigSpecimen.url };
        const raw = rawFromUrl(u.sigSpecimen.url);
        if (!raw) return null;
        const dataUrl = await new Promise((res, rej) => {
          const r = new FileReader();
          r.onload = () => res(r.result);
          r.onerror = rej;
          r.readAsDataURL(raw);
        });
        return { type: "image", url: dataUrl };
      };
      const employees = [];
      for (const u of newUsers) {
        employees.push({
          name: u.name, designation: u.designation || null, email: u.email, role: u.role,
          signatory: !!u.signatory, sig: await sigOf(u),
          duties: (u.existingActivities || []).map((a) => ({
            client_name: a.client, service: a.service, kind: dutyKind(a.service), cadence: a.cadence,
            next_due: new Date(a.due + "T12:00:00").toISOString(),
            contact: a.contact || null,
          })),
        });
      }
      const out = await api.post("/tenants/complete-setup", {
        firm: {
          name: newFirm.name, short: newFirm.short, address: newFirm.address || null,
          trn: newFirm.trn || null, phone: newFirm.phone || null, email: newFirm.email, accent: newFirm.accent,
        },
        services: newFirm.services,
        templates: Object.fromEntries(
          Object.entries(newFirm.templates || {}).filter(([, v]) => v).map(([k, v]) => [k, { name: v.name }])
        ),
        employees,
      });
      setResult(out);
    } catch (e) {
      // retry after a lost response: the server already completed setup — proceed, don't dead-end
      if (e.status === 409 && /already complete/i.test(e.message)) return auth.reloadFirm();
      setErr(e.message);
    }
  };

  if (result)
    return (
      <div className="min-h-screen py-10 px-6" style={{ background: "var(--paper)" }}>
        <div className="max-w-3xl mx-auto">
          <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>🚀 {result.users.length ? "Setup complete — credentials issued" : "Setup complete"}</h1>
          <p className="text-sm mt-1" style={{ color: "var(--mut)" }}>
            {result.users.length
              ? <>Invite emails with these temporary passwords were sent to the new employees. They are shown here <b>once</b> — passwords are stored hashed and cannot be recovered. First login forces a reset.</>
              : <>Your firm is configured. You can add employees any time under Admin → Employees & roles.</>}
          </p>
          <div className="mt-5 space-y-1.5">
            {result.users.map((u) => (
              <div key={u.id} className="flex gap-3 items-center text-sm border rounded-md px-3 py-2 bg-white" style={{ borderColor: "var(--line)" }}>
                <span className="flex-1 font-medium">{u.name} <span className="font-normal text-xs" style={{ color: "var(--mut)" }}>{u.email} · {u.role}</span></span>
                <span className="font-mono2 text-xs px-2 py-1 rounded" style={{ background: "var(--paper)" }}>{u.temp_password}</span>
              </div>
            ))}
          </div>
          <button onClick={() => auth.reloadFirm()} className="mt-6 px-5 py-2.5 rounded-lg text-white text-sm font-semibold" style={{ background: "var(--accent)" }}>
            Enter Baton →
          </button>
        </div>
      </div>
    );

  const sig = auth.me.sig_specimen;
  const initial = {
    firm: {
      name: auth.firm?.name || "", short: auth.firm?.short || "", address: auth.firm?.address || "",
      trn: auth.firm?.trn || "", phone: auth.firm?.phone || "", email: auth.firm?.email || "",
      accent: auth.firm?.accent || "#1E6E56",
    },
    emps: [{
      id: uid(), name: auth.me.name, designation: auth.me.designation || "", email: auth.me.email,
      role: "Admin", tempPw: "", signatory: !!auth.me.signatory,
      sig: sig ? (sig.type === "typed" ? { type: "typed", text: sig.text } : { type: "image", url: sig.url }) : null,
      acts: [], actsOpen: false, locked: true,
    }],
  };

  return (
    <>
      {err && (
        <div className="max-w-3xl mx-auto mt-4 px-4">
          <div className="text-sm px-3 py-2.5 rounded-lg font-medium" style={{ background: "var(--red-soft)", color: "var(--red)" }}>⚠️ {err}</div>
        </div>
      )}
      <SetupWizard onCancel={auth.logout} cancelLabel="Log out" onDone={deploy} initial={initial}
        seatsLimit={auth.firm?.subscription?.seats_limit ?? null} />
    </>
  );
}

/* FirmSetup edits live per keystroke in the prototype; here edits land in local state and
   are saved to the API 800ms after the last change. */
function FirmSetupHost({ firm, save }) {
  const [draft, setDraftState] = useState(firm);
  const timer = useRef(null);
  const setFirm = (next) => {
    setDraftState(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => save(next), 800);
  };
  useEffect(() => () => clearTimeout(timer.current), []);
  return <FirmSetup firm={draft} setFirm={setFirm} />;
}

function Shell() {
  const auth = useAuth();
  const {
    ready, me, firm, users, proposals, clients, duties, payments, notices, sigUses, toast,
    onboardings, actions, markDutyDone, raiseInvoice, recordReceipt, markNoticesRead,
    setUsersShim, setFirmShim, refetchDetail, uuidOf, setFocus,
  } = useData();
  const [route, setRoute] = useState({ screen: "dashboard" });
  const now = () => Date.now();
  const byId = (id) => users.find((u) => u.id === id) || { id, name: "—", role: "", designation: "", email: "", signatory: false };

  useEffect(() => {
    setFocus({ screen: route.screen, detailRef: route.id || null }); // steers the 30s poll
    if (route.screen === "detail" && route.id) {
      const u = uuidOf(route.id);
      if (u) refetchDetail(u);
    }
  }, [route.screen, route.id]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ---------------- derived (verbatim from the prototype) ---------------- */

  const TERMINAL = ["el_sent", "lost"];
  const batonQueue = me
    ? proposals.filter((p) => p.holder === me.id && !TERMINAL.includes(p.status))
        .map((p) => ({ p, since: p.holderLog.find((h) => !h.end)?.start ?? p.createdAt }))
        .sort((a, b) => a.since - b.since)
    : [];
  const myNotices = notices;
  const myOpenTasks = me ? proposals.filter((p) => p.assignedTo === me.id && !TERMINAL.includes(p.status)) : [];
  const myActivities = me
    ? [
        ...proposals.flatMap((p) => Object.entries(p.el?.assignments || {}).filter(([, uId]) => uId === me.id).map(([svc]) => {
          const ob = onboardings.find((o) => o.proposalId === p.uuid && o.service === svc && o.staffId === me.id);
          return { pid: p.id, client: p.prospect.name, service: svc, live: p.status === "el_sent",
                   onboardingId: ob?.id || null, onboardingComplete: ob?.status === "complete",
                   holder: ob?.holder || null, holderSince: ob?.holderSince || null,
                   itemCount: ob?.itemCount ?? 0 };
        })),
      ]
    : [];
  const onbQueue = me
    ? onboardings.filter((o) => o.holder === me.id && o.status === "in_progress")
        .sort((a, b) => (a.holderSince || 0) - (b.holderSince || 0))
    : [];
  const duePayments = payments.filter((x) => !x.done && x.dueAt <= now());

  const healthOf = (clientId) => {
    const list = payments.filter((x) => x.clientId === clientId);
    const overdue = list.filter((x) => !x.done && x.dueAt < now() && x.received < x.amount);
    const outstanding = overdue.reduce((a, x) => a + (x.amount - x.received), 0);
    const worst = overdue.reduce((a, x) => Math.max(a, days(now() - x.dueAt)), 0);
    const badge = overdue.length === 0 ? ["Good", "var(--accent)"] : worst <= 30 ? ["Watch", "var(--amber)"] : ["At risk", "var(--red)"];
    return { badge, outstanding, overdueCount: overdue.length };
  };

  const workloadOf = (uId) => ({
    proposals: proposals.filter((p) => p.assignedTo === uId && !TERMINAL.includes(p.status)),
    activities: [
      ...proposals.flatMap((p) => Object.entries(p.el?.assignments || {}).filter(([, x]) => x === uId).map(([svc]) => ({ pid: p.id, client: p.prospect.name, service: svc }))),
      ...duties.filter((dd) => dd.staffId === uId && !dd.closed).map((dd) => ({ pid: null, client: dd.client, service: dd.service, legacy: true })),
    ],
  });

  const isMgr = me.role === "Manager" || me.role === "Admin";
  const isAcct = me.role === "Accountant";

  if (!ready)
    return (
      <Frame accent={firm.accent}>
        <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--paper)" }}>
          <div className="font-disp text-lg" style={{ color: "var(--mut)" }}>Loading {firm.short}…</div>
        </div>
      </Frame>
    );

  return (
    <Frame accent={firm.accent}>
      <div className="flex h-screen overflow-hidden" style={{ background: "var(--paper)" }}>
        <aside className="w-56 shrink-0 flex flex-col" style={{ background: "var(--ink)" }}>
          <div className="px-5 pt-6 pb-5">
            <div className="font-disp text-white text-lg leading-tight tracking-tight">{firm.short}</div>
            <div className="text-[11px] mt-0.5" style={{ color: "#8FA3B8" }}>Practice control</div>
          </div>
          <nav className="flex-1 px-3 space-y-1 text-sm">
            <NavBtn label="Dashboard" active={route.screen === "dashboard"} onClick={() => setRoute({ screen: "dashboard" })} />
            {!isAcct && <NavBtn label={isMgr ? "Clients & staffing" : "My clients"} active={route.screen === "myclients"} onClick={() => setRoute({ screen: "myclients" })} />}
            {!isAcct && <NavBtn label="Proposals" active={["proposals", "detail"].includes(route.screen)} onClick={() => setRoute({ screen: "proposals" })} />}
            {isMgr && <NavBtn label="New proposal request" active={route.screen === "new"} onClick={() => setRoute({ screen: "new" })} />}
            {(isMgr || isAcct) && <NavBtn label="Clients" active={route.screen === "clients"} onClick={() => setRoute({ screen: "clients" })} />}
            {isMgr && <NavBtn label="★ Performance" active={route.screen === "performance"} onClick={() => setRoute({ screen: "performance" })} />}
            {isMgr && <NavBtn label="⏳ Pending board" active={route.screen === "pending"} onClick={() => setRoute({ screen: "pending" })} />}
            {isMgr && <NavBtn label="Performance settings" active={route.screen === "perfsettings"} onClick={() => setRoute({ screen: "perfsettings" })} />}
            <VatEngineNav active={route.screen === "vat"} onClick={() => setRoute({ screen: "vat" })} /> {/* VAT-ENGINE (removable) */}
            {isAcct && <NavBtn label="Payments" active={route.screen === "payments"} onClick={() => setRoute({ screen: "payments" })} />}
            {me.role === "Admin" && (
              <>
                <div className="pt-4 pb-1 px-3 text-[10px] uppercase tracking-widest" style={{ color: "#5D7288" }}>Administration</div>
                <NavBtn label="Employees & roles" active={route.screen === "employees"} onClick={() => setRoute({ screen: "employees" })} />
                <NavBtn label="Firm & letterhead" active={route.screen === "firm"} onClick={() => setRoute({ screen: "firm" })} />
                <NavBtn label="Signature vault" active={route.screen === "signatures"} onClick={() => setRoute({ screen: "signatures" })} />
              </>
            )}
          </nav>
          <div className="p-4 text-[11px]" style={{ color: "#5D7288" }}>Baton · production</div>
        </aside>

        <div className="flex-1 flex flex-col min-w-0">
          <header className="h-14 shrink-0 flex items-center justify-between px-6 border-b bg-white" style={{ borderColor: "var(--line)" }}>
            <div className="text-sm" style={{ color: "var(--mut)" }}>
              Signed in as <span className="font-semibold" style={{ color: "var(--ink)" }}>{me.name}</span>
              <span className="ml-2 px-2 py-0.5 rounded-full text-[11px] font-medium" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>{me.role}</span>
            </div>
            <div className="flex items-center gap-2">
              <Bell notices={myNotices} onRead={markNoticesRead} />
              <button onClick={auth.logout} className="text-xs px-3 py-1.5 rounded-md text-white font-medium" style={{ background: "var(--ink)" }}>Log out</button>
            </div>
          </header>

          <main className="flex-1 overflow-y-auto p-6">
            {me.role === "Admin" && firm.subscription?.expiring_soon && (
              <div className="max-w-5xl mx-auto mb-4 rounded-xl border px-4 py-3 text-sm flex items-center gap-3" style={{ background: "var(--amber-soft)", borderColor: "#E4C99A" }}>
                <span className="text-lg">⏳</span>
                <span style={{ color: "#6B5A38" }}>
                  <b>{firm.subscription.plan_name} subscription ends in {firm.subscription.days_left} day{firm.subscription.days_left !== 1 && "s"}</b> — after a 7-day
                  grace period, logins are blocked. Contact the platform operator to renew.
                </span>
              </div>
            )}
            {route.screen === "dashboard" && !isAcct && (
              <Dashboard me={me} isMgr={isMgr} batonQueue={batonQueue} myOpenTasks={myOpenTasks} myActivities={myActivities} proposals={proposals} duties={duties} markDutyDone={markDutyDone} byId={byId} now={now} open={(id) => setRoute({ screen: "detail", id })} gotoNew={() => setRoute({ screen: "new" })} onbQueue={onbQueue} openOnb={(id) => setRoute({ screen: "onboarding", id })} gotoMyClients={() => setRoute({ screen: "myclients" })} />
            )}
            {route.screen === "myclients" && !isAcct && (
              <MyClients me={me} isMgr={isMgr} users={users} onboardings={onboardings} duties={duties} byId={byId} now={now}
                openOnb={(id) => setRoute({ screen: "onboarding", id })}
                openVat={(dutyId) => setRoute({ screen: "vat", dutyId })}
                gotoDashboard={() => setRoute({ screen: "dashboard" })} />
            )}
            {(route.screen === "payments" || (route.screen === "dashboard" && isAcct)) && (
              <Payments payments={payments} duePayments={duePayments} clients={clients} now={now} byId={byId} raiseInvoice={raiseInvoice} recordReceipt={recordReceipt} healthOf={healthOf} />
            )}
            {route.screen === "proposals" && <ProposalList proposals={proposals} byId={byId} now={now} open={(id) => setRoute({ screen: "detail", id })} />}
            {route.screen === "clients" && <Clients clients={clients} healthOf={healthOf} byId={byId} proposals={proposals} duties={duties} now={now} openP={(id) => setRoute({ screen: "detail", id })} canPerf={isMgr} />}
            {route.screen === "performance" && isMgr && <PerformanceScreen />}
            {route.screen === "pending" && isMgr && (
              <PendingBoard byId={byId} goto={(item) => {
                if (item.type === "proposal") setRoute({ screen: "detail", id: item.ref });
                else if (item.type === "onboarding") setRoute({ screen: "onboarding", id: item.ref });
                else if (item.type === "duty") setRoute({ screen: "myclients" });
                else setRoute({ screen: "payments" });
              }} />
            )}
            {route.screen === "perfsettings" && isMgr && <PerformanceSettings />}
            {route.screen === "vat" && <VatEngineScreen initialDutyId={route.dutyId || null} />} {/* VAT-ENGINE (removable) */}
            {route.screen === "onboarding" && route.id && <OnboardingView oid={route.id} me={me} byId={byId} back={() => setRoute({ screen: "dashboard" })} />}
            {route.screen === "new" && isMgr && <NewRequest users={users} me={me} firm={firm} clients={clients} onCreate={(form) => { actions.createRequest(form).then(() => setRoute({ screen: "dashboard" })).catch(() => {}); }} />}
            {route.screen === "detail" && (
              <Detail p={proposals.find((x) => x.id === route.id)} me={me} byId={byId} now={now} firm={firm} users={users} clients={clients} workloadOf={workloadOf}
                actions={actions}
                back={() => setRoute({ screen: "dashboard" })} />
            )}
            {route.screen === "employees" && me.role === "Admin" && <Employees users={users} setUsers={setUsersShim} />}
            {route.screen === "firm" && me.role === "Admin" && <FirmSetupHost firm={firm} save={setFirmShim} />}
            {route.screen === "signatures" && me.role === "Admin" && <Signatures users={users} sigUses={sigUses} byId={byId} />}
          </main>
        </div>
      </div>
      {toast && <div className="fixed bottom-5 left-1/2 -translate-x-1/2 px-4 py-2.5 rounded-lg text-sm text-white shadow-lg z-50" style={{ background: "var(--ink)" }}>{toast}</div>}
    </Frame>
  );
}

/* ================================================================== */

function Frame({ children, accent = "#1E6E56" }) {
  return (
    <div className="font-body antialiased" style={{ color: "#22303E" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@110,500..800&family=IBM+Plex+Mono:wght@400;500&display=swap');
        :root{--ink:#16233B;--paper:#F4F6F8;--line:#E2E7ED;--mut:#5C6B7A;--accent:${accent};--accent-soft:${accent}1A;--amber:#A8690F;--amber-soft:#A8690F14;--red:#A8332E;--red-soft:#A8332E12;}
        .font-disp{font-family:'Archivo',sans-serif;font-variation-settings:'wdth' 110;}
        .font-body{font-family:ui-sans-serif,system-ui,-apple-system,sans-serif;}
        .font-mono2{font-family:'IBM Plex Mono',monospace;}
        ::-webkit-scrollbar{width:10px;height:10px}::-webkit-scrollbar-thumb{background:#C9D2DB;border-radius:6px}
        button{cursor:pointer} input,textarea,select{outline:none}
        input:focus,textarea:focus,select:focus{box-shadow:0 0 0 2px var(--accent-soft);border-color:var(--accent)!important}
      `}</style>
      {children}
    </div>
  );
}

function NavBtn({ label, active, onClick }) {
  return (
    <button onClick={onClick} className="w-full text-left px-3 py-2 rounded-md transition-colors" style={active ? { background: "rgba(255,255,255,.12)", color: "#fff", fontWeight: 600 } : { color: "#A9BACB" }}>
      {label}
    </button>
  );
}

function Bell({ notices, onRead }) {
  const [open, setOpen] = useState(false);
  const unread = notices.filter((n) => !n.read).length;
  return (
    <div className="relative">
      <button onClick={() => { setOpen(!open); if (!open) onRead(); }} className="relative w-9 h-9 rounded-md border flex items-center justify-center hover:bg-gray-50" style={{ borderColor: "var(--line)" }}>
        <span>🔔</span>
        {unread > 0 && <span className="absolute -top-1.5 -right-1.5 min-w-[18px] h-[18px] px-1 rounded-full text-[10px] text-white flex items-center justify-center font-bold" style={{ background: "var(--red)" }}>{unread}</span>}
      </button>
      {open && (
        <div className="absolute right-0 top-11 w-80 max-h-96 overflow-y-auto bg-white border rounded-lg shadow-xl z-20 p-2" style={{ borderColor: "var(--line)" }}>
          {notices.length === 0 && <div className="p-3 text-sm" style={{ color: "var(--mut)" }}>No notifications yet.</div>}
          {notices.map((n) => (
            <div key={n.id} className="p-2.5 text-sm border-b last:border-0" style={{ borderColor: "var(--line)" }}>
              <div>{n.text}</div>
              <div className="text-[11px] mt-0.5 font-mono2" style={{ color: "var(--mut)" }}>{fmtDT(n.at)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ================================================================== */

const STATUS_MAP = {
  assigned: ["Awaiting staff review", "var(--accent)"],
  docs_with_manager: ["Requirements pending — manager", "var(--amber)"],
  waiver_review: ["Waiver decision — staff", "var(--amber)"],
  drafting: ["Drafting — staff", "var(--accent)"],
  manager_review: ["Manager review", "var(--ink)"],
  senior_review: ["Senior review & signature", "var(--amber)"],
  signed: ["Signed — send to client", "var(--accent)"],
  proposal_sent: ["Sent — awaiting client-signed proposal", "var(--mut)"],
  el_staffing: ["Client confirmed — staffing & EL prep", "var(--ink)"],
  el_senior_review: ["EL — senior signature", "var(--amber)"],
  el_approved: ["EL signed — send to client", "var(--accent)"],
  el_sent: ["Proposal & Engagement ✓ — onboarding underway", "var(--accent)"],
  onboarding_complete: ["Onboarding complete ✓✓", "var(--accent)"],
  lost: ["Lost", "var(--red)"],
};
function StatusChip({ s }) {
  const [t, c] = STATUS_MAP[s] || [s, "var(--mut)"];
  return <span className="text-[11px] px-2 py-0.5 rounded-full font-medium whitespace-nowrap" style={{ background: c + "18", color: c }}>{t}</span>;
}

/* ================================================================== */

function Dashboard({ me, isMgr, batonQueue, myOpenTasks, myActivities, proposals, duties, markDutyDone, byId, now, open, gotoNew, onbQueue = [], openOnb = () => {}, gotoMyClients = () => {} }) {
  const myDuties = duties.filter((d) => d.staffId === me.id && !d.closed).sort((a, b) => a.nextDue - b.nextDue);
  const myOverdue = myDuties.filter((d) => d.nextDue < now());
  const allOpenDuties = duties.filter((d) => !d.closed).sort((a, b) => a.nextDue - b.nextDue);
  return (
    <div className="max-w-5xl mx-auto">
      {myOpenTasks.length > 0 && me.role === "Staff" && (
        <div className="mb-5 rounded-xl border p-4 flex items-start gap-3" style={{ background: "var(--amber-soft)", borderColor: "#E4C99A" }}>
          <div className="text-xl">📌</div>
          <div className="text-sm">
            <div className="font-semibold" style={{ color: "var(--amber)" }}>Daily reminder — open proposals assigned to you</div>
            <div className="mt-1" style={{ color: "#6B5A38" }}>
              Reappears every day until each proposal is completed:{" "}
              {myOpenTasks.map((p, i) => (
                <span key={p.id}><button className="underline font-medium" onClick={() => open(p.id)}>{p.id} · {p.prospect.name}</button>{i < myOpenTasks.length - 1 ? ", " : ""}</span>
              ))}
            </div>
          </div>
        </div>
      )}

      {myOverdue.length > 0 && (
        <div className="mb-5 rounded-xl border p-4" style={{ background: "var(--red-soft)", borderColor: "#DCA9A6" }}>
          <div className="font-semibold text-sm" style={{ color: "var(--red)" }}>🔁 Daily reminder — {myOverdue.length} deadline{myOverdue.length > 1 ? "s" : ""} OVERDUE</div>
          <div className="text-xs mt-1" style={{ color: "#7A4340" }}>
            {myOverdue.map((d) => `${d.client} — ${d.service} (due ${fmtD(d.nextDue)})`).join(" · ")}. An email reminder is also sent daily until each is completed.
          </div>
        </div>
      )}

      {myDuties.length > 0 && (
        <div className="mb-5">
          <h2 className="font-disp text-lg font-bold" style={{ color: "var(--ink)" }}>My duties & deadlines</h2>
          <p className="text-xs mt-0.5" style={{ color: "var(--mut)" }}>Recurring compliance duties assigned to you. Deadlines are computed automatically from each duty's statutory schedule — completing late never shifts the next date. Every duty keeps its own audit trail; on-time history feeds your performance record.</p>
          <div className="mt-2 space-y-2">
            {myDuties.map((d) => <DutyCard key={d.id} d={d} now={now} byId={byId} onDone={(note) => markDutyDone(d.id, note)} mine />)}
          </div>
        </div>
      )}

      {myActivities.length > 0 && (() => {
        /* the dashboard is the TO-DO list: only what needs attention; the directory lives
           in My clients. Completed onboardings never show here. */
        const slim = slimClientActivities(myActivities, 8);
        return (
          <div className="mb-5 rounded-xl border p-4" style={{ background: "var(--accent-soft)", borderColor: "var(--accent)" }}>
            <div className="font-semibold text-sm" style={{ color: "var(--accent)" }}>🧾 Your client activities — needing attention</div>
            {slim.allComplete ? (
              <div className="mt-1.5 text-xs" style={{ color: "var(--mut)" }}>
                All onboardings complete — see <button onClick={gotoMyClients} className="underline font-semibold" style={{ color: "var(--accent)" }}>My clients</button>.
              </div>
            ) : (
              <div className="mt-2 space-y-1">
                {slim.visible.map((a, i) => {
                  const withMe = a.holder === me.id;
                  const age = a.holderSince ? fmtDur(now() - a.holderSince) : null;
                  return (
                    <div key={i} className="bg-white rounded-lg px-3 py-2 text-sm flex items-center gap-3">
                      {a.pid ? <button className="font-medium underline decoration-dotted" onClick={() => (a.onboardingId ? openOnb(a.onboardingId) : open(a.pid))}>{a.client}</button> : <span className="font-medium">{a.client}</span>}
                      <span className="flex-1 text-xs" style={{ color: "var(--mut)" }}>{a.service}</span>
                      {a.onboardingId ? (
                        <>
                          <span className="text-[11px] px-2 py-0.5 rounded-full font-medium" style={withMe ? { background: "var(--accent-soft)", color: "var(--accent)" } : { background: "var(--amber-soft)", color: "var(--amber)" }}>
                            {a.itemCount === 0 ? "not started" : withMe ? `baton with you · ${age}` : `awaiting ${byId(a.holder).name.split(" ")[0]} · ${age}`}
                          </span>
                          <button onClick={() => openOnb(a.onboardingId)} className="text-[11px] font-semibold underline" style={{ color: "var(--accent)" }}>Open onboarding →</button>
                        </>
                      ) : (
                        <span className="text-[11px] px-2 py-0.5 rounded-full font-medium" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>Pending EL — not started</span>
                      )}
                    </div>
                  );
                })}
                {slim.more > 0 && (
                  <button onClick={gotoMyClients} className="text-[11px] underline font-semibold" style={{ color: "var(--accent)" }}>+ {slim.more} more — see My clients</button>
                )}
              </div>
            )}
          </div>
        );
      })()}

      {isMgr && onbQueue.length > 0 && (
        <div className="mb-5 bg-white rounded-xl border overflow-hidden" style={{ borderColor: "var(--line)" }}>
          <div className="px-5 pt-4 pb-2">
            <h2 className="font-disp text-lg font-bold" style={{ color: "var(--ink)" }}>Onboarding requests — baton with you</h2>
            <p className="text-xs mt-0.5" style={{ color: "var(--mut)" }}>Staff are waiting on these documentation requests, oldest first. The clock runs until every open item is resolved.</p>
          </div>
          {onbQueue.map((o, i) => (
            <button key={o.id} onClick={() => openOnb(o.id)} className="w-full text-left flex items-center gap-4 px-5 py-3.5 border-t hover:bg-gray-50 text-sm" style={{ borderColor: "var(--line)" }}>
              <div className="font-mono2 text-xs w-6 text-right" style={{ color: "var(--mut)" }}>{i + 1}</div>
              <div className="w-20 shrink-0">
                <div className="font-mono2 font-medium" style={{ color: days(now() - (o.holderSince || now())) >= 3 ? "var(--red)" : "var(--amber)" }}>{fmtDur(now() - (o.holderSince || now()))}</div>
                <div className="text-[10px] uppercase tracking-wider" style={{ color: "var(--mut)" }}>waiting</div>
              </div>
              <div className="flex-1 min-w-0">
                <div className="font-semibold truncate">{o.clientName} <span className="font-normal text-xs" style={{ color: "var(--mut)" }}>· {o.service}</span></div>
                <div className="text-xs mt-0.5" style={{ color: "var(--mut)" }}>requested by {o.staffName} · {o.openItems} open item{o.openItems !== 1 && "s"}</div>
              </div>
              <div className="text-xs font-medium" style={{ color: "var(--accent)" }}>Open →</div>
            </button>
          ))}
        </div>
      )}

      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>Baton with you</h1>
          <p className="text-sm mt-1" style={{ color: "var(--mut)" }}>Everything waiting on <b>you</b>, oldest first — proposals, signatures, engagement letters. The baton stays with you until you pass it.</p>
        </div>
        {isMgr && <button onClick={gotoNew} className="px-4 py-2 rounded-lg text-white text-sm font-semibold" style={{ background: "var(--accent)" }}>+ New proposal request</button>}
      </div>

      <div className="mt-4 bg-white rounded-xl border overflow-hidden" style={{ borderColor: "var(--line)" }}>
        {batonQueue.length === 0 ? (
          <div className="p-10 text-center text-sm" style={{ color: "var(--mut)" }}>Nothing is waiting on you right now.</div>
        ) : (
          batonQueue.map(({ p, since }, i) => {
            const age = now() - since;
            const hot = days(age) >= 3;
            return (
              <button key={p.id} onClick={() => open(p.id)} className="w-full text-left flex items-center gap-4 px-5 py-4 border-b last:border-0 hover:bg-gray-50" style={{ borderColor: "var(--line)" }}>
                <div className="font-mono2 text-xs w-6 text-right" style={{ color: "var(--mut)" }}>{i + 1}</div>
                <div className="w-24 shrink-0">
                  <div className="font-mono2 text-lg font-medium" style={{ color: hot ? "var(--red)" : "var(--amber)" }}>{fmtDur(age)}</div>
                  <div className="text-[10px] uppercase tracking-wider" style={{ color: "var(--mut)" }}>waiting</div>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-semibold truncate">{p.id} — {p.prospect.name}</div>
                  <div className="text-xs mt-0.5 flex items-center gap-1.5 flex-wrap" style={{ color: "var(--mut)" }}><StatusChip s={p.status} /> requested by {byId(p.requestedBy).name} · drafter {byId(p.assignedTo).name}</div>
                </div>
                <div className="text-xs font-medium" style={{ color: "var(--accent)" }}>Open →</div>
              </button>
            );
          })
        )}
      </div>

      {isMgr && allOpenDuties.length > 0 && (
        <>
          <h2 className="font-disp text-lg font-bold mt-8" style={{ color: "var(--ink)" }}>Compliance deadlines — all staff</h2>
          <p className="text-xs mt-1" style={{ color: "var(--mut)" }}>Every tracked duty across the firm, nearest deadline first. Red means the statutory date has passed and the assigned staff member is being reminded daily.</p>
          <div className="mt-3 bg-white rounded-xl border overflow-hidden" style={{ borderColor: "var(--line)" }}>
            {allOpenDuties.map((d) => {
              const overdue = d.nextDue < now();
              const daysLeft = Math.ceil(days(d.nextDue - now()));
              return (
                <div key={d.id} className="flex items-center gap-4 px-5 py-3 border-b last:border-0 text-sm" style={{ borderColor: "var(--line)" }}>
                  <span className="w-32 shrink-0 font-medium truncate">{byId(d.staffId).name}</span>
                  <span className="flex-1 truncate">{d.client} <span className="text-xs" style={{ color: "var(--mut)" }}>— {d.service} · {d.cadence}</span></span>
                  <span className="font-mono2 text-xs" style={{ color: "var(--mut)" }}>{fmtD(d.nextDue)}</span>
                  <span className="text-[11px] px-2 py-0.5 rounded-full font-bold w-28 text-center" style={overdue ? { background: "var(--red-soft)", color: "var(--red)" } : daysLeft <= 7 ? { background: "var(--amber-soft)", color: "var(--amber)" } : { background: "var(--paper)", color: "var(--mut)" }}>
                    {overdue ? `${Math.ceil(days(now() - d.nextDue))}d OVERDUE` : `in ${daysLeft}d`}
                  </span>
                  {d.history.length > 0 && <span className="font-mono2 text-[10px] w-20 text-right" style={{ color: "var(--mut)" }}>{d.history.filter((h) => !h.lateMs).length}/{d.history.length} on time</span>}
                </div>
              );
            })}
          </div>
        </>
      )}

      {isMgr && proposals.some((p) => ["proposal_sent", "el_staffing", "el_senior_review", "el_approved", "el_sent"].includes(p.status)) && (
        <>
          <h2 className="font-disp text-lg font-bold mt-8" style={{ color: "var(--ink)" }}>Client-side tracker — sent vs confirmed</h2>
          <p className="text-xs mt-1" style={{ color: "var(--mut)" }}>Every proposal that has left the building: what's with the client, what they've confirmed (client-signed proposal on file), and where the engagement letter stands.</p>
          <div className="mt-3 bg-white rounded-xl border overflow-hidden" style={{ borderColor: "var(--line)" }}>
            {proposals.filter((p) => ["proposal_sent", "el_staffing", "el_senior_review", "el_approved", "el_sent"].includes(p.status)).map((p) => {
              const stage = {
                proposal_sent: [`Sent ${p.proposalSentAt ? fmtD(p.proposalSentAt) : ""} — awaiting client-signed proposal`, "var(--amber)", p.proposalSentAt],
                el_staffing: ["✓ Client confirmed — staffing & EL prep", "var(--accent)", p.clientSignedProposal?.at],
                el_senior_review: ["✓ Client confirmed — EL at senior signature", "var(--accent)", p.clientSignedProposal?.at],
                el_approved: ["✓ Client confirmed — EL signed, ready to send", "var(--accent)", p.clientSignedProposal?.at],
                el_sent: ["✓✓ EL sent — Proposal & Engagement complete", "var(--accent)", p.el?.sentAt],
              }[p.status];
              return (
                <button key={p.id} onClick={() => open(p.id)} className="w-full text-left flex items-center gap-4 px-5 py-3 border-b last:border-0 hover:bg-gray-50 text-sm" style={{ borderColor: "var(--line)" }}>
                  <span className="font-mono2 text-xs" style={{ color: "var(--mut)" }}>{p.id}</span>
                  <span className="flex-1 truncate font-medium">{p.prospect.name}</span>
                  <span className="text-xs font-medium" style={{ color: stage[1] }}>{stage[0]}</span>
                  {p.status === "proposal_sent" && p.proposalSentAt && <span className="font-mono2 text-xs" style={{ color: days(now() - p.proposalSentAt) >= 7 ? "var(--red)" : "var(--mut)" }}>{fmtDur(now() - p.proposalSentAt)} with client</span>}
                </button>
              );
            })}
          </div>
        </>
      )}

      {isMgr && proposals.length > 0 && (
        <>
          <h2 className="font-disp text-lg font-bold mt-8" style={{ color: "var(--ink)" }}>All open matters — who holds the baton?</h2>
          <div className="mt-3 bg-white rounded-xl border overflow-hidden" style={{ borderColor: "var(--line)" }}>
            {proposals.filter((p) => !["el_sent", "lost"].includes(p.status)).map((p) => {
              const since = p.holderLog.find((h) => !h.end)?.start ?? p.createdAt;
              return (
                <button key={p.id} onClick={() => open(p.id)} className="w-full text-left flex items-center gap-4 px-5 py-3 border-b last:border-0 hover:bg-gray-50 text-sm" style={{ borderColor: "var(--line)" }}>
                  <span className="font-mono2 text-xs" style={{ color: "var(--mut)" }}>{p.id}</span>
                  <span className="flex-1 truncate font-medium">{p.prospect.name}</span>
                  <StatusChip s={p.status} />
                  <span className="text-xs" style={{ color: "var(--mut)" }}>baton with <b style={{ color: "var(--ink)" }}>{p.holder ? byId(p.holder).name : "client / —"}</b> for <span className="font-mono2">{fmtDur(now() - since)}</span></span>
                </button>
              );
            })}
            {proposals.filter((p) => !["el_sent", "lost"].includes(p.status)).length === 0 && <div className="p-6 text-sm text-center" style={{ color: "var(--mut)" }}>No open matters.</div>}
          </div>
        </>
      )}
    </div>
  );
}

/* ================================================================== */

function DutyCard({ d, now, byId, onDone, mine }) {
  const [openTrail, setOpenTrail] = useState(false);
  const [doneMode, setDoneMode] = useState(false);
  const [declareMode, setDeclareMode] = useState(false);
  const [files, setFiles] = useState([]);
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [mail, setMail] = useState(null);
  const [rec, setRec] = useState({});
  const overdue = d.nextDue < now();
  const daysLeft = Math.ceil(days(d.nextDue - now()));
  const onTime = d.history.filter((h) => !h.lateMs).length;
  const kind = d.kind || dutyKind(d.service);
  const setR = (k, v) => setRec((r) => ({ ...r, [k]: v }));
  const reset = () => { setDoneMode(false); setDeclareMode(false); setFiles([]); setNote(""); setReason(""); setRec({}); };

  const vatOK = files.length > 0 && rec.period && rec.position;
  const ctOK = files.length > 0 && rec["financial year"] && rec.position;

  const openReportMail = () => setMail({
    to: d.contact?.email || "",
    subject: `${d.service} — ${d.client}`,
    body: `Dear ${d.contact?.name || "Sir/Madam"},\n\nPlease find attached the following for ${d.client}: ${files.map((f) => f.name).join(", ")}.\n\nKindly let us know if you have any questions.\n\nBest regards`,
  });

  const smallInp = (ph, k, w = "w-full") => (
    <input placeholder={ph} value={rec[k] || ""} onChange={(e) => setR(k, e.target.value)} className={`border rounded-md px-2 py-1.5 text-xs ${w}`} style={{ borderColor: "var(--line)" }} />
  );

  return (
    <div className="bg-white border rounded-xl p-4" style={{ borderColor: overdue ? "#DCA9A6" : "var(--line)" }}>
      <div className="flex items-center gap-3 flex-wrap text-sm">
        <div className="flex-1 min-w-[220px]">
          <b>{d.client}</b> <span className="text-xs" style={{ color: "var(--mut)" }}>— {d.service}</span>
          <div className="text-[11px] mt-0.5" style={{ color: "var(--mut)" }}>
            {d.cadence} · deadlines auto-computed{d.contact?.email && <> · contact: {d.contact.name} &lt;{d.contact.email}&gt;</>}{d.history.length > 0 && <> · <span className="font-mono2">{onTime}/{d.history.length} on time</span></>}
          </div>
        </div>
        <span className="font-mono2 text-xs" style={{ color: "var(--mut)" }}>due {fmtD(d.nextDue)}</span>
        <span className="text-[11px] px-2 py-0.5 rounded-full font-bold" style={overdue ? { background: "var(--red-soft)", color: "var(--red)" } : daysLeft <= 7 ? { background: "var(--amber-soft)", color: "var(--amber)" } : { background: "var(--paper)", color: "var(--mut)" }}>
          {overdue ? `${Math.ceil(days(now() - d.nextDue))}d OVERDUE` : `in ${daysLeft}d`}
        </span>
        {mine && !doneMode && <button onClick={() => setDoneMode(true)} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold" style={{ background: "var(--accent)" }}>Complete…</button>}
        <button onClick={() => setOpenTrail(!openTrail)} className="px-2.5 py-1.5 rounded-md border text-xs font-medium" style={{ borderColor: "var(--line)", color: "var(--mut)" }}>Trail ({d.events.length}) {openTrail ? "▾" : "▸"}</button>
      </div>

      {doneMode && (
        <div className="mt-3 pt-3 border-t space-y-2" style={{ borderColor: "var(--line)" }}>
          {kind === "report" && !declareMode && (
            <>
              <div className="text-[11px] font-semibold" style={{ color: "var(--mut)" }}>Work is complete when the deliverables reach the client: upload the report(s), then the CRM drafts the email to {d.contact?.name || "the client contact"} — sending it records completion with proof.</div>
              <div className="flex items-center gap-2 flex-wrap">
                <FilePick small multiple label="Upload report file(s)" onFiles={(fs) => setFiles([...files, ...fs])} />
                {files.map((f, i) => <span key={i} className="text-xs font-mono2 px-2 py-1 rounded border" style={{ borderColor: "var(--line)" }}><FileLink {...f} /> <button onClick={() => setFiles(files.filter((_, j) => j !== i))} style={{ color: "var(--red)" }}>×</button></span>)}
              </div>
              <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional) — e.g. May 2026 management accounts + VAT summary" className="w-full border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
              <div className="flex gap-2 items-center flex-wrap">
                <button disabled={files.length === 0} onClick={openReportMail} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>✉️ Draft email to client & complete</button>
                <button onClick={() => setDeclareMode(true)} className="text-[11px] underline" style={{ color: "var(--mut)" }}>Complete without sending reports (client doesn't require them)…</button>
                <button onClick={reset} className="text-xs" style={{ color: "var(--mut)" }}>cancel</button>
              </div>
            </>
          )}

          {kind === "vat" && !declareMode && (
            <>
              <div className="text-[11px] font-semibold" style={{ color: "var(--mut)" }}>Upload the filed VAT return / FTA acknowledgement as proof, and record the filing facts — Baton keeps the full filing history per client.</div>
              <div className="flex items-center gap-2 flex-wrap">
                <FilePick small multiple label="Upload filed return / acknowledgement *" onFiles={(fs) => setFiles([...files, ...fs])} />
                {files.map((f, i) => <span key={i} className="text-xs font-mono2 px-2 py-1 rounded border" style={{ borderColor: "var(--line)" }}><FileLink {...f} /> <button onClick={() => setFiles(files.filter((_, j) => j !== i))} style={{ color: "var(--red)" }}>×</button></span>)}
              </div>
              <div className="flex gap-2 flex-wrap">
                {smallInp("Tax period * — e.g. Q2 2026", "period", "w-40")}
                <select value={rec.position || ""} onChange={(e) => setR("position", e.target.value)} className="border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }}>
                  <option value="">Net position *</option><option>Payable</option><option>Refundable</option><option>Nil</option>
                </select>
                {smallInp("Net VAT amount (AED)", "net VAT (AED)", "w-36")}
                {smallInp("Output VAT (AED)", "output VAT (AED)", "w-32")}
                {smallInp("Input VAT (AED)", "input VAT (AED)", "w-32")}
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider font-bold mb-1" style={{ color: "var(--mut)" }}>Taxable sales per emirate (AED, optional)</div>
                <div className="flex gap-1.5 flex-wrap">
                  {EMIRATES.map((em) => (
                    <span key={em} className="flex items-center gap-1 text-[10px]" style={{ color: "var(--mut)" }}>{em}
                      <input value={rec["sales " + em] || ""} onChange={(e) => setR("sales " + em, e.target.value)} className="w-20 border rounded-md px-1.5 py-1 text-xs font-mono2" style={{ borderColor: "var(--line)" }} />
                    </span>
                  ))}
                </div>
              </div>
              <div className="flex gap-2 items-center">
                <button disabled={!vatOK} onClick={() => { onDone({ method: "proof", files, note, record: rec }); reset(); }} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>Record filing & complete {overdue ? "(LATE)" : "(on time)"}</button>
                <button onClick={() => setDeclareMode(true)} className="text-[11px] underline" style={{ color: "var(--mut)" }}>Complete without proof…</button>
                <button onClick={reset} className="text-xs" style={{ color: "var(--mut)" }}>cancel</button>
              </div>
            </>
          )}

          {kind === "ct" && !declareMode && (
            <>
              <div className="text-[11px] font-semibold" style={{ color: "var(--mut)" }}>Upload the filed Corporate Tax return / acknowledgement as proof, and record the filing facts.</div>
              <div className="flex items-center gap-2 flex-wrap">
                <FilePick small multiple label="Upload filed return / acknowledgement *" onFiles={(fs) => setFiles([...files, ...fs])} />
                {files.map((f, i) => <span key={i} className="text-xs font-mono2 px-2 py-1 rounded border" style={{ borderColor: "var(--line)" }}><FileLink {...f} /> <button onClick={() => setFiles(files.filter((_, j) => j !== i))} style={{ color: "var(--red)" }}>×</button></span>)}
              </div>
              <div className="flex gap-2 flex-wrap">
                {smallInp("Financial year * — e.g. FY2025", "financial year", "w-36")}
                {smallInp("Taxable income (AED)", "taxable income (AED)", "w-40")}
                {smallInp("CT payable (AED)", "CT payable (AED)", "w-36")}
                <select value={rec.position || ""} onChange={(e) => setR("position", e.target.value)} className="border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }}>
                  <option value="">Position *</option><option>Payable</option><option>Nil</option><option>Refund</option>
                </select>
                <select value={rec["small business relief"] || ""} onChange={(e) => setR("small business relief", e.target.value)} className="border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }}>
                  <option value="">Small business relief?</option><option>Yes</option><option>No</option>
                </select>
              </div>
              <div className="flex gap-2 items-center">
                <button disabled={!ctOK} onClick={() => { onDone({ method: "proof", files, note, record: rec }); reset(); }} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>Record filing & complete {overdue ? "(LATE)" : "(on time)"}</button>
                <button onClick={() => setDeclareMode(true)} className="text-[11px] underline" style={{ color: "var(--mut)" }}>Complete without proof…</button>
                <button onClick={reset} className="text-xs" style={{ color: "var(--mut)" }}>cancel</button>
              </div>
            </>
          )}

          {kind === "other" && !declareMode && (
            <div className="flex gap-2 items-center flex-wrap">
              <FilePick small multiple label="Upload proof (optional)" onFiles={(fs) => setFiles([...files, ...fs])} />
              {files.map((f, i) => <span key={i} className="text-xs font-mono2 px-2 py-1 rounded border" style={{ borderColor: "var(--line)" }}>{f.name}</span>)}
              <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Completion note" className="flex-1 min-w-[180px] border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
              <button onClick={() => { files.length ? onDone({ method: "proof", files, note, record: null }) : setDeclareMode(true); }} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold" style={{ background: "var(--accent)" }}>Complete</button>
              <button onClick={reset} className="text-xs" style={{ color: "var(--mut)" }}>cancel</button>
            </div>
          )}

          {declareMode && (
            <div className="flex gap-2 items-center">
              <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Mandatory reason for completing without proof — recorded permanently in the duty trail" className="flex-1 border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
              <button disabled={!reason.trim()} onClick={() => { onDone({ method: "declared", reason: reason.trim(), note }); reset(); }} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold disabled:opacity-40" style={{ background: "var(--amber)" }}>Confirm — declared complete</button>
              <button onClick={() => setDeclareMode(false)} className="text-xs" style={{ color: "var(--mut)" }}>back</button>
            </div>
          )}
        </div>
      )}

      {openTrail && (
        <div className="mt-3 pt-3 border-t" style={{ borderColor: "var(--line)" }}>
          {d.history.some((h) => h.record || (h.evidence || []).length) && (
            <div className="mb-3">
              <div className="text-[10px] uppercase tracking-wider font-bold mb-1.5" style={{ color: "var(--mut)" }}>Filing / delivery records held by Baton</div>
              {d.history.map((h, i) => (
                <div key={i} className="text-xs border rounded-md p-2 mb-1.5" style={{ borderColor: "var(--line)", background: "var(--paper)" }}>
                  <div className="font-mono2" style={{ color: h.lateMs ? "var(--red)" : "var(--accent)" }}>due {fmtD(h.dueAt)} → done {fmtD(h.completedAt)} {h.lateMs ? `(${fmtDur(h.lateMs)} late)` : "(on time)"} · {h.method === "sent" ? `emailed to ${h.emailedTo}` : h.method === "proof" ? "proof on file" : "declared"}</div>
                  {h.record && <div className="mt-1">{Object.entries(h.record).filter(([, v]) => v).map(([k, v]) => <span key={k} className="inline-block mr-3"><span style={{ color: "var(--mut)" }}>{k}:</span> <b className="font-mono2">{v}</b></span>)}</div>}
                  {(h.evidence || []).length > 0 && <div className="mt-1 flex gap-2 flex-wrap">{h.evidence.map((f, j) => <FileLink key={j} {...f} />)}</div>}
                </div>
              ))}
            </div>
          )}
          <div className="text-[10px] uppercase tracking-wider font-bold mb-1.5" style={{ color: "var(--mut)" }}>Duty audit trail — append-only</div>
          {[...d.events].reverse().map((e, i) => (
            <div key={i} className="text-xs py-1 border-b last:border-0" style={{ borderColor: "var(--line)" }}>
              <span className="font-mono2" style={{ color: "var(--mut)" }}>{fmtDT(e.at)} · {e.by === "system" ? "SYSTEM" : byId(e.by).name}</span> — {e.text}
            </div>
          ))}
        </div>
      )}

      {mail && (
        <EmailModal mail={mail} setMail={setMail} onSend={() => { onDone({ method: "sent", files, note, emailedTo: mail.to }); setMail(null); reset(); }} />
      )}
    </div>
  );
}

function ProposalList({ proposals, byId, now, open }) {
  return (
    <div className="max-w-5xl mx-auto">
      <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>Proposals & engagements</h1>
      <div className="mt-4 bg-white rounded-xl border overflow-hidden" style={{ borderColor: "var(--line)" }}>
        <div className="grid grid-cols-[70px_1fr_200px_140px_100px] gap-3 px-5 py-2.5 text-[11px] uppercase tracking-wider border-b" style={{ color: "var(--mut)", borderColor: "var(--line)" }}>
          <div>Ref</div><div>Prospect / client</div><div>Status</div><div>Baton with</div><div>Total age</div>
        </div>
        {proposals.length === 0 && <div className="p-8 text-center text-sm" style={{ color: "var(--mut)" }}>No proposals yet.</div>}
        {proposals.map((p) => (
          <button key={p.id} onClick={() => open(p.id)} className="w-full grid grid-cols-[70px_1fr_200px_140px_100px] gap-3 px-5 py-3.5 text-sm text-left border-b last:border-0 hover:bg-gray-50 items-center" style={{ borderColor: "var(--line)" }}>
            <span className="font-mono2 text-xs">{p.id}</span>
            <span className="font-medium truncate">{p.prospect.name}</span>
            <StatusChip s={p.status} />
            <span className="text-xs" style={{ color: "var(--mut)" }}>{p.holder ? byId(p.holder).name : "—"}</span>
            <span className="font-mono2 text-xs" style={{ color: "var(--mut)" }}>{fmtDur(now() - p.createdAt)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ================================================================== */

function Clients({ clients, healthOf, byId, proposals, duties = [], now, openP, canPerf }) {
  const [perfOpen, setPerfOpen] = useState(null);
  const [docsOpen, setDocsOpen] = useState(null);
  return (
    <div className="max-w-5xl mx-auto">
      <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>Clients</h1>
      <p className="text-sm mt-1" style={{ color: "var(--mut)" }}>Created the moment the client-signed proposal is uploaded. Health reflects payment behaviour.</p>
      <div className="mt-4 bg-white rounded-xl border overflow-hidden" style={{ borderColor: "var(--line)" }}>
        <div className="grid grid-cols-[70px_1fr_190px_140px_120px_100px] gap-3 px-5 py-2.5 text-[11px] uppercase tracking-wider border-b" style={{ color: "var(--mut)", borderColor: "var(--line)" }}>
          <div>Code</div><div>Client</div><div>Team (per activity)</div><div>Outstanding</div><div>Since</div><div>Health</div>
        </div>
        {clients.length === 0 && <div className="p-8 text-center text-sm" style={{ color: "var(--mut)" }}>No clients yet — upload a client-signed proposal to convert a prospect.</div>}
        {clients.map((c) => {
          const h = healthOf(c.id);
          const engagements = proposals.filter((p) => p.clientId === c.id);
          const clientDuties = duties.filter((d) => d.clientId === c.id && !d.closed);
          const preBaton = c.origin === "pre_baton";
          const services = c.services.length ? c.services : [...new Set(clientDuties.map((d) => d.service))];
          const team = engagements.length
            ? [...new Map(engagements.flatMap((p) => Object.entries(p.el?.assignments || {}))).entries()]
            : clientDuties.map((d) => [d.service, d.staffId]);
          return (
            <div key={c.id} className="border-b last:border-0" style={{ borderColor: "var(--line)" }}>
            <div onClick={() => c.pid && openP(c.pid)} role="button" tabIndex={0} className={`w-full grid grid-cols-[70px_1fr_190px_140px_120px_100px] gap-3 px-5 py-3.5 text-sm text-left items-center ${c.pid ? "hover:bg-gray-50 cursor-pointer" : "cursor-default"}`}>
            <span className="font-mono2 text-xs">{c.code}</span>
            <span className="font-medium truncate">{c.name}
              {preBaton && (
                <span className="ml-1.5 text-[10px] font-normal px-1.5 py-0.5 rounded-full align-middle whitespace-nowrap" style={{ background: "var(--paper)", color: "var(--mut)", border: "1px solid var(--line)" }}>pre-Baton client</span>
              )}
              {!preBaton && c.confirmationBasis && c.confirmationBasis !== "signed_upload" && (
                <span className="ml-1.5 text-[10px] font-normal px-1.5 py-0.5 rounded-full align-middle whitespace-nowrap" style={{ background: "var(--paper)", color: "var(--mut)", border: "1px solid var(--line)" }}>confirmed without signed proposal</span>
              )}
              {c.unauditedOnFile && (
                <span className="ml-1.5 text-[10px] font-bold px-1.5 py-0.5 rounded-full align-middle whitespace-nowrap" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>unaudited financials on file</span>
              )}
              <div className="text-[11px] font-normal truncate" style={{ color: "var(--mut)" }}>{services.join(" · ")}</div></span>
            <span className="text-[11px]" style={{ color: "var(--mut)" }}>
              {team.length === 0 ? "Unassigned" : team.map(([svc, uId]) => <div key={svc} className="truncate">{svc.split(" (")[0]}: <b style={{ color: "var(--ink)" }}>{byId(uId).name.split(" ")[0]}</b></div>)}
            </span>
            <span className="font-mono2 text-xs" style={{ color: h.outstanding > 0 ? "var(--red)" : "var(--mut)" }}>{h.outstanding > 0 ? money(h.outstanding) + " overdue" : "—"}</span>
            <span className="font-mono2 text-xs" style={{ color: "var(--mut)" }}>{fmtD(c.engagedAt)}</span>
            <span className="text-[11px] px-2 py-0.5 rounded-full font-bold text-center" style={{ background: h.badge[1] + "18", color: h.badge[1] }}>{h.badge[0]}</span>
            </div>
            {engagements.length > 0 ? (
              <div className="px-5 pb-1 -mt-1 flex gap-1.5 flex-wrap items-center">
                <span className="text-[10px] uppercase tracking-wider font-bold" style={{ color: "var(--mut)" }}>Engagements ({engagements.length})</span>
                {engagements.map((p) => (
                  <button key={p.id} onClick={() => openP(p.id)} className="text-[11px] px-2 py-0.5 rounded-full border font-mono2 hover:bg-gray-50" style={{ borderColor: "var(--line)", color: "var(--ink)" }}>
                    {p.id} · {STATUS_MAP[p.status]?.[0] || p.status}
                  </button>
                ))}
              </div>
            ) : preBaton ? (
              <div className="px-5 pb-1 -mt-1 text-[11px]" style={{ color: "var(--mut)" }}>
                Relationship predates Baton — engaged via deployment record. No proposal history.
              </div>
            ) : null}
            <div className="px-5 pb-2 flex gap-4">
              {canPerf && (
                <button onClick={() => setPerfOpen(perfOpen === c.id ? null : c.id)} className="text-[11px] underline" style={{ color: "var(--mut)" }}>
                  {perfOpen === c.id ? "Hide performance & task history ▾" : "★ Performance & task history ▸"}
                </button>
              )}
              <button onClick={() => setDocsOpen(docsOpen === c.id ? null : c.id)} className="text-[11px] underline" style={{ color: "var(--mut)" }}>
                {docsOpen === c.id ? "Hide documents ▾" : "📁 Documents ▸"}
              </button>
            </div>
            {canPerf && perfOpen === c.id && <div className="px-5 pb-4"><ClientPerformance clientId={c.id} /></div>}
            {docsOpen === c.id && <div className="px-5 pb-4"><ClientDocuments clientId={c.id} /></div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ================================================================== */

/* "My clients" (staff) / "Clients & staffing" (managers): the DIRECTORY — one row per
   client-activity, grouped by client. The dashboard stays the to-do list; the per-service
   tabs (e.g. the VAT engine) stay the working ledgers — this tab links into them. */
function MyClients({ me, isMgr, users, onboardings, duties, byId, now, openOnb, openVat, gotoDashboard }) {
  const [query, setQuery] = useState("");
  const [needsOnly, setNeedsOnly] = useState(false);
  const [staffFilter, setStaffFilter] = useState("");
  const [vatFilings, setVatFilings] = useState([]);
  useEffect(() => {
    api.get("/vat-engine/filings").then(setVatFilings).catch(() => setVatFilings([])); // 404 = engine off
  }, []);
  const liveVatByDuty = Object.fromEntries(vatFilings.filter((x) => x.status !== "complete").map((x) => [x.duty_id, x]));

  const groups = filterGroups(
    groupClientActivities({ onboardings, duties, meId: me.id, role: me.role, staffFilter: staffFilter || null }),
    { query, needsOnly, nowMs: now() },
  );
  const staffIds = [...new Set([...onboardings.map((o) => o.staffId), ...duties.map((d) => d.staffId)])];

  return (
    <div className="max-w-5xl mx-auto">
      <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>
        {isMgr ? "Clients & staffing" : "My clients"}
      </h1>
      <p className="text-sm mt-1" style={{ color: "var(--mut)" }}>
        Every client-activity {isMgr ? "across the firm" : "you are staffed on"} — onboarding state, the recurring duty once born, and where to act. The dashboard shows only what needs attention; this is the full directory.
      </p>

      <div className="mt-3 flex gap-2 items-center flex-wrap">
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search client, code or activity…" className="border rounded-md px-3 py-2 text-sm w-72" style={{ borderColor: "var(--line)" }} />
        {[["all", "All", false], ["needs", "Needs attention", true]].map(([k, l, v]) => (
          <button key={k} onClick={() => setNeedsOnly(v)} className="px-3 py-1.5 rounded-full text-xs font-semibold border"
            style={needsOnly === v ? { background: "var(--accent)", color: "#fff", borderColor: "var(--accent)" } : { borderColor: "var(--line)", color: "var(--mut)" }}>{l}</button>
        ))}
        {isMgr && (
          <select value={staffFilter} onChange={(e) => setStaffFilter(e.target.value)} className="border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }}>
            <option value="">All staff</option>
            {staffIds.map((sid) => <option key={sid} value={sid}>{byId(sid).name}</option>)}
          </select>
        )}
      </div>

      <div className="mt-4 space-y-3">
        {groups.map((g) => (
          <div key={g.clientKey} className="bg-white rounded-xl border overflow-hidden" style={{ borderColor: "var(--line)" }}>
            <div className="px-4 py-2.5 border-b flex items-center gap-2" style={{ borderColor: "var(--line)", background: "var(--paper)" }}>
              <span className="font-semibold text-sm">{g.clientName}</span>
              {g.clientRef && <span className="font-mono2 text-xs" style={{ color: "var(--accent)" }}>{g.clientRef}</span>}
            </div>
            {g.rows.map((r) => {
              const ob = r.onboarding;
              const d = r.duty;
              const vat = d && liveVatByDuty[d.id];
              const overdue = d && d.nextDue < now();
              return (
                <div key={r.key} className="px-4 py-2.5 border-b last:border-0 flex items-center gap-3 text-sm flex-wrap" style={{ borderColor: "var(--line)" }}>
                  <span className="w-52 shrink-0 font-medium truncate">{r.service}</span>
                  {isMgr && <span className="text-[11px] w-24 shrink-0 truncate" style={{ color: "var(--mut)" }}>{byId(r.staffId).name.split(" ")[0]}</span>}
                  {ob ? (
                    ob.status === "complete"
                      ? <span className="text-[11px] px-2 py-0.5 rounded-full font-medium" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>✓ onboarded {ob.completedAt ? fmtD(ob.completedAt) : ""}</span>
                      : <span className="text-[11px] px-2 py-0.5 rounded-full font-medium" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>
                          onboarding · {ob.itemCount === 0 ? "not started" : `with ${byId(ob.holder).name.split(" ")[0]} · ${fmtDur(now() - (ob.holderSince || now()))}`}
                        </span>
                  ) : (
                    <span className="text-[11px] px-2 py-0.5 rounded-full font-medium" style={{ background: "var(--paper)", color: "var(--mut)", border: "1px solid var(--line)" }}>pre-Baton</span>
                  )}
                  {d && (
                    <span className="text-[11px] px-2 py-0.5 rounded-full font-mono2 font-medium" style={overdue ? { background: "var(--red-soft, #F7E4E2)", color: "var(--red)" } : { background: "var(--paper)", color: "var(--mut)", border: "1px solid var(--line)" }}>
                      due {fmtD(d.nextDue)} · {overdue ? `${fmtDur(now() - d.nextDue)} OVERDUE` : `in ${fmtDur(d.nextDue - now())}`}
                    </span>
                  )}
                  <span className="flex-1" />
                  <span className="flex gap-3 text-[11px] font-semibold">
                    {ob && <button onClick={() => openOnb(ob.id)} className="underline" style={{ color: "var(--accent)" }}>onboarding →</button>}
                    {d && d.kind === "vat" && !d.closed && (
                      <button onClick={() => openVat(d.id)} className="underline" style={{ color: "var(--accent)" }}>
                        {vat ? `VAT period (${STAGES_SHORT[vat.status] || vat.status}) →` : "VAT engine →"}
                      </button>
                    )}
                    {d && d.kind !== "vat" && <button onClick={gotoDashboard} className="underline" style={{ color: "var(--mut)" }}>duty on dashboard →</button>}
                  </span>
                </div>
              );
            })}
          </div>
        ))}
        {groups.length === 0 && (
          <div className="bg-white rounded-xl border p-8 text-center text-sm" style={{ borderColor: "var(--line)", color: "var(--mut)" }}>
            {needsOnly ? "Nothing needs attention — switch to All to see the full directory." : "No client activities yet."}
          </div>
        )}
      </div>
    </div>
  );
}

const STAGES_SHORT = { ledgers_pending: "ledger", invoices_pending: "invoices", reconciled: "recon",
  computation_draft: "computation", awaiting_client_approval: "client approval", ready_to_file: "file at FTA" };

function Payments({ payments, duePayments, clients, now, byId, raiseInvoice, recordReceipt, healthOf }) {
  const groups = (list) => {
    const m = new Map();
    list.forEach((x) => { const k = x.clientId || "unlinked"; if (!m.has(k)) m.set(k, []); m.get(k).push(x); });
    return [...m.entries()];
  };
  const renderGroups = (list, actionable) => groups(list).map(([cid, rows]) => {
    const h = cid !== "unlinked" ? healthOf(cid) : null;
    return (
      <div key={cid}>
        <div className="flex items-center gap-2 mt-1 mb-1.5">
          <span className="text-xs font-semibold" style={{ color: "var(--ink)" }}>{rows[0].clientName || "—"}</span>
          {h && <span className="text-[10px] px-2 py-0.5 rounded-full font-bold" style={{ background: h.badge[1] + "18", color: h.badge[1] }}>{h.badge[0]}</span>}
        </div>
        <div className="space-y-2">
          {rows.map((x) => <PayRow key={x.id} x={x} now={now} raiseInvoice={raiseInvoice} recordReceipt={recordReceipt} actionable={actionable} />)}
        </div>
      </div>
    );
  });
  return (
    <div className="max-w-5xl mx-auto">
      {duePayments.length > 0 && (
        <div className="mb-5 rounded-xl border p-4" style={{ background: "var(--red-soft)", borderColor: "#DCA9A6" }}>
          <div className="font-semibold text-sm" style={{ color: "var(--red)" }}>🔁 Daily reminder — receipts pending your update ({duePayments.length})</div>
          <div className="text-xs mt-1" style={{ color: "#7A4340" }}>
            An email reminder is also sent to you every day for each item below, and both continue until you update the receipt status. Nothing here can silently expire.
          </div>
        </div>
      )}
      <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>Payments & receipts</h1>
      <p className="text-sm mt-1" style={{ color: "var(--mut)" }}>Proof of work applies here too: raising an invoice requires the invoice itself (it is emailed to the client automatically), and every receipt carries a method and reference.</p>

      <Section title={`Due now / overdue (${duePayments.length})`}>
        {duePayments.length === 0 && <Empty t="Nothing due. Upcoming items will move here on their due date." />}
        {renderGroups(duePayments, true)}
      </Section>

      <Section title="Upcoming">
        {payments.filter((x) => !x.done && x.dueAt > now()).length === 0 && <Empty t="No upcoming expected payments." />}
        {renderGroups(payments.filter((x) => !x.done && x.dueAt > now()).sort((a, b) => a.dueAt - b.dueAt), true)}
      </Section>

      <Section title="Settled">
        {payments.filter((x) => x.done).length === 0 && <Empty t="No settled payments yet." />}
        {renderGroups(payments.filter((x) => x.done), false)}
      </Section>
    </div>
  );
}

const Section = ({ title, children }) => (
  <section className="mt-6">
    <h2 className="font-disp text-lg font-bold" style={{ color: "var(--ink)" }}>{title}</h2>
    <div className="mt-2 space-y-2">{children}</div>
  </section>
);
const Empty = ({ t }) => <div className="bg-white border rounded-xl p-5 text-sm text-center" style={{ borderColor: "var(--line)", color: "var(--mut)" }}>{t}</div>;

const LIFECYCLE_CHIP = {
  awaiting_invoice: ["Awaiting invoice", "var(--amber)"],
  invoiced: ["Invoiced — awaiting payment", "var(--ink)"],
  partially_received: ["Partially received", "var(--accent)"],
  settled: ["Settled ✓", "var(--accent)"],
};

function PayRow({ x, now, raiseInvoice, recordReceipt, actionable }) {
  const [amt, setAmt] = useState("");
  const [invNo, setInvNo] = useState("");
  const [invDate, setInvDate] = useState("");
  const [invFiles, setInvFiles] = useState([]);
  const [declMode, setDeclMode] = useState(false);
  const [declReason, setDeclReason] = useState("");
  const [rcDate, setRcDate] = useState("");
  const [method, setMethod] = useState("bank_transfer");
  const [reference, setReference] = useState("");
  const [rcNote, setRcNote] = useState("");
  const [rcFile, setRcFile] = useState(null);
  const overdueDays = Math.floor(days(now() - x.dueAt));
  const remaining = x.amount - x.received;
  const chip = x.lifecycle === "overdue"
    ? [`Overdue — ${Math.max(1, overdueDays)}d`, "var(--amber)"]
    : LIFECYCLE_CHIP[x.lifecycle] || [x.lifecycle || "—", "var(--mut)"];
  const receiptValid = num(amt) > 0 && (method === "cash" ? rcNote.trim() : reference.trim());
  const doReceipt = () => {
    recordReceipt(x.id, { amount: Math.min(num(amt), remaining), date: rcDate, method, reference: reference.trim(), note: rcNote.trim(), file: rcFile })
      .then(() => { setAmt(""); setReference(""); setRcNote(""); setRcFile(null); })
      .catch(() => {});
  };
  return (
    <div className="bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
      <div className="flex items-center gap-3 flex-wrap text-sm">
        <div className="flex-1 min-w-[220px]">
          <b>{x.clientName}</b> <span className="font-mono2 text-xs" style={{ color: "var(--mut)" }}>({x.pid})</span>
          <div className="text-xs mt-0.5" style={{ color: "var(--mut)" }}>{x.label}{x.invoiceNumber && <span className="font-mono2"> · Invoice {x.invoiceNumber}{x.invoiceDeclared ? " (declared)" : ""}</span>}</div>
        </div>
        <div className="font-mono2 text-sm">{money(x.amount)}{x.received > 0 && !x.done && <span className="text-xs" style={{ color: "var(--accent)" }}> · {money(x.received)} received</span>}</div>
        <div className="text-xs font-mono2" style={{ color: x.done ? "var(--accent)" : x.dueAt <= now() ? "var(--red)" : "var(--mut)" }}>
          {x.done ? "✓ settled" : x.dueAt <= now() ? `due ${fmtD(x.dueAt)} · ${overdueDays}d overdue` : `due ${fmtD(x.dueAt)}`}
        </div>
        <div className="text-[11px] px-2 py-0.5 rounded-full font-medium" style={{ background: chip[1] + "18", color: chip[1] }}>{chip[0]}</div>
      </div>

      {actionable && !x.done && !x.invoiceRaised && (
        <div className="mt-3 pt-3 border-t space-y-2" style={{ borderColor: "var(--line)" }}>
          <div className="text-[11px] font-semibold" style={{ color: "var(--mut)" }}>Raise the invoice — the document is required and is emailed to the client automatically with secure links.</div>
          <div className="flex items-center gap-2 flex-wrap">
            <FilePick small multiple label="Attach invoice PDF *" onFiles={(fs) => setInvFiles([...invFiles, ...fs])} />
            {invFiles.map((f, i) => <span key={i} className="text-xs font-mono2 px-2 py-1 rounded border" style={{ borderColor: "var(--line)" }}>{f.name} <button onClick={() => setInvFiles(invFiles.filter((_, j) => j !== i))} style={{ color: "var(--red)" }}>×</button></span>)}
            <input value={invNo} onChange={(e) => setInvNo(e.target.value)} placeholder="Invoice number *" className="border rounded-md px-2.5 py-1.5 text-xs w-36 font-mono2" style={{ borderColor: "var(--line)" }} />
            <input type="date" value={invDate} onChange={(e) => setInvDate(e.target.value)} title="Invoice date (defaults to today)" className="border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
            <button disabled={!invNo.trim() || invFiles.length === 0} onClick={() => raiseInvoice(x.id, { number: invNo.trim(), date: invDate, files: invFiles }).then(() => { setInvNo(""); setInvFiles([]); }).catch(() => {})} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold disabled:opacity-40" style={{ background: "var(--ink)" }}>
              Raise invoice & email client ✉️
            </button>
          </div>
          {!declMode ? (
            <button onClick={() => setDeclMode(true)} className="text-[11px] underline" style={{ color: "var(--mut)" }}>Raised outside Baton? Declare it (reason required — capped for ratings)…</button>
          ) : (
            <div className="flex gap-2 items-center flex-wrap">
              <input value={invNo} onChange={(e) => setInvNo(e.target.value)} placeholder="Invoice number *" className="border rounded-md px-2.5 py-1.5 text-xs w-36 font-mono2" style={{ borderColor: "var(--line)" }} />
              <input value={declReason} onChange={(e) => setDeclReason(e.target.value)} placeholder="Mandatory reason — why is the invoice not on file?" className="flex-1 min-w-[220px] border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
              <button disabled={!invNo.trim() || !declReason.trim()} onClick={() => raiseInvoice(x.id, { number: invNo.trim(), date: invDate, declaredReason: declReason.trim() }).then(() => { setDeclMode(false); setInvNo(""); setDeclReason(""); }).catch(() => {})} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold disabled:opacity-40" style={{ background: "var(--amber)" }}>Declare raised</button>
              <button onClick={() => setDeclMode(false)} className="text-xs" style={{ color: "var(--mut)" }}>cancel</button>
            </div>
          )}
        </div>
      )}

      {actionable && !x.done && x.invoiceRaised && (
        <div className="mt-3 pt-3 border-t space-y-2" style={{ borderColor: "var(--line)" }}>
          <div className="flex items-center gap-2 flex-wrap">
            <input value={amt} onChange={(e) => setAmt(e.target.value)} placeholder={`Amount received (remaining ${money(remaining)})`} className="border rounded-md px-2.5 py-1.5 text-xs w-56 font-mono2" style={{ borderColor: "var(--line)" }} />
            <input type="date" value={rcDate} onChange={(e) => setRcDate(e.target.value)} title="Date received (defaults to today)" className="border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
            <select value={method} onChange={(e) => setMethod(e.target.value)} className="border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }}>
              <option value="bank_transfer">Bank transfer</option><option value="cheque">Cheque</option><option value="cash">Cash</option><option value="card">Card</option><option value="other">Other</option>
            </select>
            {method === "cash" ? (
              <input value={rcNote} onChange={(e) => setRcNote(e.target.value)} placeholder="Note * — who paid, where received" className="flex-1 min-w-[200px] border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
            ) : (
              <input value={reference} onChange={(e) => setReference(e.target.value)} placeholder="Reference * — transaction ID / cheque no." className="flex-1 min-w-[200px] border rounded-md px-2.5 py-1.5 text-xs font-mono2" style={{ borderColor: "var(--line)" }} />
            )}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <FilePick small label={rcFile ? `Evidence: ${rcFile.name}` : "Attach evidence (optional)"} onFiles={(fs) => setRcFile(fs[0])} />
            <button disabled={!receiptValid} onClick={doReceipt} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>
              Record receipt
            </button>
          </div>
        </div>
      )}

      {(x.invoiceFiles || []).length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2 text-xs">
          {x.invoiceFiles.map((f, i) => <span key={i} className="px-2 py-1 rounded-md border font-mono2" style={{ borderColor: "var(--line)" }}>🧾 <FileLink {...f} /></span>)}
        </div>
      )}
      {x.evidence.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2 text-xs">
          {x.evidence.map((e, i) => <span key={i} className="px-2 py-1 rounded-md border font-mono2" style={{ borderColor: "var(--line)" }}><FileLink {...e} /> · {money(e.amount)}</span>)}
        </div>
      )}
    </div>
  );
}

/* ================================================================== */

function NewRequest({ users, me, firm, clients = [], onCreate }) {
  const CATALOG = firm.services && firm.services.length ? firm.services : SERVICES;
  const [mode, setMode] = useState("prospect"); // "prospect" | "client"
  const [clientId, setClientId] = useState(null);
  const [prospect, setProspect] = useState({ name: "", contactPerson: "", email: "", phone: "" });
  const [sel, setSel] = useState([]);
  const [custom, setCustom] = useState("");
  const [fees, setFees] = useState({});
  const [paymentTerms, setPaymentTerms] = useState("");
  const [notes, setNotes] = useState("");
  const [docs, setDocs] = useState([]);
  const [assignedTo, setAssignedTo] = useState("");
  const staff = users.filter((u) => u.role === "Staff" || u.id === me.id);

  const toggle = (s) => setSel((x) => (x.includes(s) ? x.filter((y) => y !== s) : [...x, s]));
  const addCustom = () => { if (custom.trim()) { setSel((x) => [...x, custom.trim() + "‡"]); setCustom(""); } };
  const services = sel.map((s) => ({ name: s.replace("‡", ""), custom: s.endsWith("‡"), fee: fees[s]?.amt || "", basis: fees[s]?.basis || defaultBasis(s) }));
  const selClient = clients.find((c) => c.id === clientId) || null;
  const pickClient = (c) => {
    setClientId(c.id);
    setProspect({
      name: c.name,
      contactPerson: c.contact?.contactPerson || "",
      email: c.contact?.email || "",
      phone: c.contact?.phone || "",
    });
  };
  const clearClient = () => {
    setClientId(null);
    setProspect({ name: "", contactPerson: "", email: "", phone: "" });
  };
  const switchMode = (m) => {
    setMode(m);
    clearClient();
  };
  const valid = prospect.name.trim() && prospect.email.trim() && sel.length > 0 && assignedTo
    && (mode === "prospect" || clientId);

  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>New proposal request</h1>
      <p className="text-sm mt-1" style={{ color: "var(--mut)" }}>Raised after a prospect meeting. Assignment sends an auto-email and starts the clock.</p>

      <Card title="1 · Who is this for?">
        <div className="flex gap-2 mb-3">
          {[["prospect", "New prospect"], ["client", "Existing client"]].map(([m, l]) => (
            <button key={m} onClick={() => switchMode(m)} className="px-3.5 py-1.5 rounded-full text-xs font-semibold border transition"
              style={mode === m ? { background: "var(--accent)", color: "#fff", borderColor: "var(--accent)" } : { borderColor: "var(--line)", color: "var(--mut)" }}>{l}</button>
          ))}
        </div>

        {mode === "client" && (
          <div className="mb-3">
            <label className="text-xs font-semibold" style={{ color: "var(--mut)" }}>Search the firm's clients</label>
            <div className="mt-1">
              <ClientCombobox clients={clients} selected={selClient} onSelect={pickClient} onClear={clearClient} />
            </div>
            {selClient && (
              <div className="mt-1.5 text-[11px]" style={{ color: "var(--mut)" }}>
                Additional engagement for {selClient.code} — the client name is locked to the record; contact details below are editable.
              </div>
            )}
          </div>
        )}

        {(mode === "prospect" || selClient) && (
          <div className="grid grid-cols-2 gap-3">
            {mode === "client" ? (
              <div>
                <label className="text-xs font-semibold" style={{ color: "var(--mut)" }}>Client name (from the client record)</label>
                <input value={prospect.name} disabled className="mt-1 w-full border rounded-md px-3 py-2 text-sm opacity-70" style={{ borderColor: "var(--line)", background: "var(--paper)" }} />
              </div>
            ) : (
              <Inp label="Prospect / company name *" v={prospect.name} set={(v) => setProspect({ ...prospect, name: v })} ph="e.g. Gulf Horizon Trading FZE" />
            )}
            <Inp label="Contact person" v={prospect.contactPerson} set={(v) => setProspect({ ...prospect, contactPerson: v })} />
            <Inp label="Contact email * (proposal & EL will be emailed here)" v={prospect.email} set={(v) => setProspect({ ...prospect, email: v })} />
            <Inp label="Phone" v={prospect.phone} set={(v) => setProspect({ ...prospect, phone: v })} />
          </div>
        )}
      </Card>

      <Card title="2 · Services to include *" sub="Pick from the firm's catalog, or type a custom service. Custom entries are flagged to Admin as catalog candidates.">
        <div className="flex flex-wrap gap-2">
          {CATALOG.map((s) => (
            <button key={s} onClick={() => toggle(s)} className="px-3 py-1.5 rounded-full text-xs font-medium border transition" style={sel.includes(s) ? { background: "var(--accent)", color: "#fff", borderColor: "var(--accent)" } : { borderColor: "var(--line)", color: "var(--mut)" }}>{s}</button>
          ))}
        </div>
        <div className="flex gap-2 mt-3">
          <input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="Custom service, e.g. Payroll & WPS processing" className="flex-1 border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
          <button onClick={addCustom} className="px-3 py-2 rounded-md border text-sm font-medium" style={{ borderColor: "var(--line)" }}>Add custom</button>
        </div>
        {sel.filter((s) => s.endsWith("‡")).map((s) => (
          <div key={s} className="mt-2 text-xs flex items-center gap-2">
            <span className="px-2 py-1 rounded-full font-medium" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>{s.replace("‡", "")} · custom — flagged for catalog review</span>
            <button className="underline" style={{ color: "var(--mut)" }} onClick={() => toggle(s)}>remove</button>
          </div>
        ))}
        {sel.length > 0 && (
          <div className="mt-4 space-y-2">
            <div className="text-xs font-semibold" style={{ color: "var(--mut)" }}>Indicative fee per service (optional — staff can request it later). Every fee carries a billing basis so nothing is ambiguous.</div>
            {sel.map((s) => (
              <div key={s} className="flex items-center gap-3 text-sm">
                <span className="flex-1">{s.replace("‡", "")}</span>
                <input value={fees[s]?.amt || ""} onChange={(e) => setFees({ ...fees, [s]: { amt: e.target.value, basis: fees[s]?.basis || defaultBasis(s) } })} placeholder="AED" className="w-28 border rounded-md px-2 py-1.5 text-sm font-mono2" style={{ borderColor: "var(--line)" }} />
                <select value={fees[s]?.basis || defaultBasis(s)} onChange={(e) => setFees({ ...fees, [s]: { amt: fees[s]?.amt || "", basis: e.target.value } })} className="border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }}>
                  {BASIS.map((b) => <option key={b}>{b}</option>)}
                </select>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="3 · Payment terms & meeting notes" sub="Optional at this stage — the drafter can request anything missing through the checklist.">
        <Inp label="Payment terms" v={paymentTerms} set={setPaymentTerms} ph="e.g. 50% advance, balance on delivery; monthly retainer billed in advance" />
        <div className="mt-3">
          <label className="text-xs font-semibold" style={{ color: "var(--mut)" }}>Notes from the meeting</label>
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} className="mt-1 w-full border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} placeholder="Context the drafter should know…" />
        </div>
      </Card>

      <Card title="4 · Documents received from the prospect" sub="Upload whatever you collected at the meeting — PDF, images, spreadsheets. Files open in a new tab so the drafter can inspect them.">
        <FilePick label="Upload documents" multiple onFiles={(fs) => setDocs([...docs, ...fs])} />
        <div className="mt-3 flex flex-wrap gap-2">
          {docs.map((d, i) => (
            <span key={i} className="text-xs px-2.5 py-1.5 rounded-md border font-mono2 flex items-center gap-2 bg-white" style={{ borderColor: "var(--line)" }}>
              <FileLink {...d} /> <button onClick={() => setDocs(docs.filter((_, j) => j !== i))} style={{ color: "var(--red)" }} title="Remove">×</button>
            </span>
          ))}
          {docs.length === 0 && <span className="text-xs" style={{ color: "var(--mut)" }}>No files attached yet.</span>}
        </div>
      </Card>

      <Card title="5 · Assign technical staff *" sub="Assignment triggers an auto-email and a daily on-screen reminder for the assignee until the proposal is completed. No deadline — elapsed time is tracked instead.">
        <div className="grid grid-cols-2 gap-2">
          {staff.map((u) => (
            <button key={u.id} onClick={() => setAssignedTo(u.id)} className="text-left p-3 rounded-lg border transition" style={assignedTo === u.id ? { borderColor: "var(--accent)", background: "var(--accent-soft)" } : { borderColor: "var(--line)" }}>
              <div className="font-semibold text-sm">{u.name}{u.id === me.id ? " (me)" : ""}</div>
              <div className="text-xs" style={{ color: "var(--mut)" }}>{u.designation}</div>
            </button>
          ))}
        </div>
      </Card>

      <div className="mt-5 flex justify-end">
        <button disabled={!valid} onClick={() => onCreate({ prospect, services, paymentTerms, notes, docs, assignedTo, clientId: mode === "client" ? clientId : null })} className="px-5 py-2.5 rounded-lg text-white text-sm font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>
          Create request & assign
        </button>
      </div>
    </div>
  );
}

/* Searchable client combobox — closed by default; filters live on name OR code; capped
   dropdown with "+N more"; arrows/Enter/Esc; selection renders a chip with × to clear.
   Reuse this anywhere a client is picked. */
function ClientCombobox({ clients, selected, onSelect, onClear, placeholder = "Search clients by name or code, e.g. Gulf Horizon or CL-001" }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const query = q.trim().toLowerCase();
  const matches = query
    ? clients.filter((c) => `${c.code} ${c.name}`.toLowerCase().includes(query))
    : [...clients].sort((a, b) => (b.engagedAt || 0) - (a.engagedAt || 0)).slice(0, 5);
  const visible = matches.slice(0, 8);
  const more = matches.length - visible.length;
  const pick = (c) => { onSelect(c); setQ(""); setOpen(false); setHi(0); };

  if (selected) {
    return (
      <span className="inline-flex items-center gap-2 text-xs px-3 py-1.5 rounded-full font-medium max-w-full" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
        <span className="font-mono2 font-bold shrink-0">{selected.code}</span>
        <span className="truncate">{selected.name}</span>
        {selected.contact?.email && <span className="truncate" style={{ opacity: 0.75 }}>· {selected.contact.email}</span>}
        <button onClick={onClear} title="Clear and search again" className="font-bold leading-none px-0.5 shrink-0" style={{ color: "var(--red)" }}>×</button>
      </span>
    );
  }
  return (
    <div className="relative">
      <input
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); setHi(0); }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setHi((h) => Math.min(h + 1, Math.max(visible.length - 1, 0))); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)); }
          else if (e.key === "Enter") { e.preventDefault(); if (open && visible[hi]) pick(visible[hi]); }
          else if (e.key === "Escape") setOpen(false);
        }}
        placeholder={placeholder}
        className="w-full border rounded-md px-3 py-2 text-sm"
        style={{ borderColor: "var(--line)" }}
      />
      {open && (
        <div className="absolute z-30 mt-1 w-full bg-white border rounded-lg shadow-lg overflow-y-auto" style={{ borderColor: "var(--line)", maxHeight: "19rem" }}
          onMouseDown={(e) => e.preventDefault() /* keep input focus so blur doesn't eat the click */}>
          {!query && visible.length > 0 && (
            <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wider font-bold" style={{ color: "var(--mut)" }}>Recent clients</div>
          )}
          {visible.map((c, i) => (
            <button key={c.id} onClick={() => pick(c)} onMouseEnter={() => setHi(i)}
              className="w-full text-left px-3 py-2 text-sm flex items-center gap-2"
              style={{ background: i === hi ? "var(--accent-soft)" : "transparent" }}>
              <span className="font-mono2 text-xs shrink-0" style={{ color: "var(--accent)" }}>{c.code}</span>
              <span className="flex-1 truncate font-medium">{c.name}</span>
              <span className="text-[11px] truncate" style={{ color: "var(--mut)" }}>{c.contact?.email || ""}</span>
            </button>
          ))}
          {visible.length === 0 && <div className="px-3 py-3 text-xs" style={{ color: "var(--mut)" }}>No client matches "{q}".</div>}
          {more > 0 && (
            <div className="px-3 py-2 text-[11px] border-t" style={{ color: "var(--mut)", borderColor: "var(--line)" }}>
              + {more} more — keep typing to narrow down
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const Card = ({ title, sub, children }) => (
  <section className="mt-5 bg-white rounded-xl border p-5" style={{ borderColor: "var(--line)" }}>
    <h3 className="font-disp font-bold" style={{ color: "var(--ink)" }}>{title}</h3>
    {sub && <p className="text-xs mt-0.5 mb-3" style={{ color: "var(--mut)" }}>{sub}</p>}
    {!sub && <div className="mb-3" />}
    {children}
  </section>
);
const Inp = ({ label, v, set, ph }) => (
  <div>
    <label className="text-xs font-semibold" style={{ color: "var(--mut)" }}>{label}</label>
    <input value={v} onChange={(e) => set(e.target.value)} placeholder={ph} className="mt-1 w-full border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
  </div>
);

/* ================================================================== */
/*  Proposal detail                                                    */
/* ================================================================== */

function Detail({ p, me, byId, now, firm, users, clients, workloadOf, actions, back }) {
  const [tab, setTab] = useState("work");
  const [wsDraft, setWsDraft] = useState(p ? p.draft : null);
  useEffect(() => { if (p) setWsDraft(structuredClone(p.draft)); }, [p?.versions.length]);
  if (!p) return <div>Not found.</div>;
  const canon = (d) => JSON.stringify({ l: d.lines.map((x) => [x.service, String(x.fee).trim(), x.basis || defaultBasis(x.service)]), t: (d.paymentTerms || "").trim(), v: String(d.validityDays), s: (d.scope || "").trim() });
  const formDirty = p.versions.length > 0 && wsDraft && canon(wsDraft) !== canon(p.draft);
  const iAmDrafter = me.id === p.assignedTo;
  const iAmRequester = me.id === p.requestedBy;
  const iHoldBaton = p.holder === me.id;
  const client = clients.find((c) => c.pid === p.id) || (p.clientId ? clients.find((c) => c.id === p.clientId) : null) || null;
  const isAdditional = !!client && client.pid !== p.id;
  const showEngTab = ["signed", "proposal_sent", "el_staffing", "el_senior_review", "el_approved", "el_sent", "lost"].includes(p.status);

  const attribution = useMemo(() => {
    const tally = {};
    p.holderLog.forEach((h) => { const end = h.end ?? now(); tally[h.userId] = (tally[h.userId] || 0) + (end - h.start); });
    const total = Object.values(tally).reduce((a, b) => a + b, 0) || 1;
    return { tally, total };
  }, [p, now()]);

  return (
    <div className="max-w-5xl mx-auto">
      <button onClick={back} className="text-xs font-medium mb-3" style={{ color: "var(--mut)" }}>← Back</button>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>
            {p.id} — {p.prospect.name} {client && <span className="font-mono2 text-base" style={{ color: "var(--accent)" }}>· {client.code}</span>}
          </h1>
          <div className="text-sm mt-1 flex items-center gap-2 flex-wrap" style={{ color: "var(--mut)" }}>
            <StatusChip s={p.status} />
            {isAdditional && (
              <span className="text-[11px] px-2 py-0.5 rounded-full font-bold" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
                Additional engagement for {client.code}
              </span>
            )}
            <span>Requested by <b style={{ color: "var(--ink)" }}>{byId(p.requestedBy).name}</b></span>·
            <span>Drafter <b style={{ color: "var(--ink)" }}>{byId(p.assignedTo).name}</b></span>·
            {p.holder ? <span>baton with <b style={{ color: "var(--ink)" }}>{byId(p.holder).name}</b></span> : <span>{p.status === "proposal_sent" ? "awaiting client confirmation" : p.status === "el_sent" ? "Proposal & Engagement complete" : "closed"}</span>}
          </div>
        </div>
      </div>

      {p.revisionNote && ["drafting", "assigned", "docs_with_manager", "waiver_review"].includes(p.status) && (
        <div className="mt-4 rounded-xl border p-4 flex items-start gap-3" style={{ background: "var(--amber-soft)", borderColor: "#E4C99A" }}>
          <div className="text-xl">🛠️</div>
          <div className="text-sm flex-1">
            <div className="font-semibold" style={{ color: "var(--amber)" }}>Revision instructed by {byId(p.revisionNote.by).name} · {fmtDT(p.revisionNote.at)}</div>
            <div className="mt-1" style={{ color: "#6B5A38" }}>“{p.revisionNote.text}”</div>
            <div className="text-[11px] mt-1.5" style={{ color: "#6B5A38" }}>Update the draft fields, regenerate, preview and re-submit — this banner clears when the revised version is sent to the manager.</div>
          </div>
        </div>
      )}

      {p.lastRejection && (
        <div className="mt-4 rounded-xl border p-4 flex items-start gap-3" style={{ background: "var(--red-soft)", borderColor: "#DCA9A6" }}>
          <div className="text-xl">↩️</div>
          <div className="text-sm flex-1">
            <div className="font-semibold" style={{ color: "var(--red)" }}>
              {p.lastRejection.stage === "proposal" ? "Proposal rejected at senior review" : "Engagement letter rejected"} by {byId(p.lastRejection.by).name} · {fmtDT(p.lastRejection.at)}
            </div>
            <div className="mt-1" style={{ color: "#7A4340" }}>
              Note: “{p.lastRejection.note}”
            </div>
            <div className="text-[11px] mt-1.5" style={{ color: "#7A4340" }}>
              {p.lastRejection.stage === "proposal"
                ? "Your signature was voided. Revise the terms on the Proposal tab and re-route for counter-signature — this banner clears when you do."
                : "Revise on the Engagement tab and re-route for approval — this banner clears when you do."}
            </div>
          </div>
        </div>
      )}

      <div className="mt-4 bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
        <div className="text-[11px] uppercase tracking-wider font-semibold mb-2" style={{ color: "var(--mut)" }}>Time attribution — who held the baton</div>
        <div className="flex h-3 rounded-full overflow-hidden" style={{ background: "var(--line)" }}>
          {Object.entries(attribution.tally).map(([u2, ms]) => (
            <div key={u2} style={{ width: `${(ms / attribution.total) * 100}%`, background: byId(u2).role === "Staff" ? "var(--accent)" : byId(u2).signatory && u2 !== p.requestedBy ? "#5C4A8A" : "var(--amber)" }} title={byId(u2).name} />
          ))}
        </div>
        <div className="flex gap-4 mt-2 text-xs flex-wrap">
          {Object.entries(attribution.tally).map(([u2, ms]) => (
            <span key={u2} className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm inline-block" style={{ background: byId(u2).role === "Staff" ? "var(--accent)" : byId(u2).signatory && u2 !== p.requestedBy ? "#5C4A8A" : "var(--amber)" }} />
              {byId(u2).name} ({byId(u2).role}) — <span className="font-mono2">{fmtDur(ms)}</span>
            </span>
          ))}
        </div>
      </div>

      <div className="mt-5 flex gap-1 border-b overflow-x-auto" style={{ borderColor: "var(--line)" }}>
        {[["work", "Workspace"], ["doc", `Proposal ${p.versions.length ? `(v${p.versions.length})` : ""}`], ...(showEngTab ? [["eng", "Engagement"]] : []), ...(["el_sent", "onboarding_complete"].includes(p.status) && (me.role === "Manager" || me.role === "Admin") ? [["report", "★ Performance report"]] : []), ["chat", `Chat (${p.chat.length})`], ["audit", `Audit trail (${p.events.length})`]].map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} className="px-4 py-2.5 text-sm font-medium -mb-px border-b-2 transition whitespace-nowrap" style={tab === k ? { borderColor: "var(--accent)", color: "var(--accent)" } : { borderColor: "transparent", color: "var(--mut)" }}>{l}</button>
        ))}
      </div>

      {tab === "work" && <Workspace p={p} me={me} byId={byId} actions={actions} iAmDrafter={iAmDrafter} iAmRequester={iAmRequester} iHoldBaton={iHoldBaton} gotoDoc={() => setTab("doc")} draft={wsDraft} setDraft={setWsDraft} dirty={formDirty} existingClient={isAdditional ? client : null} />}
      {tab === "doc" && <DocTab p={p} byId={byId} firm={firm} me={me} users={users} actions={actions} iAmRequester={iAmRequester} formDirty={formDirty} />}
      {tab === "eng" && showEngTab && <EngTab p={p} byId={byId} firm={firm} me={me} users={users} client={client} workloadOf={workloadOf} actions={actions} iAmRequester={iAmRequester} now={now} />}
      {tab === "report" && ["el_sent", "onboarding_complete"].includes(p.status) && (me.role === "Manager" || me.role === "Admin") && <PerfReportHost p={p} byId={byId} />}
      {tab === "chat" && <ChatTab p={p} me={me} byId={byId} send={(t) => actions.sendChat(p.id, t)} closed={["el_sent", "onboarding_complete", "lost"].includes(p.status)} />}
      {tab === "audit" && <AuditTab p={p} byId={byId} closed={["el_sent", "onboarding_complete"].includes(p.status)} />}
    </div>
  );
}

/* ---------- workspace (unchanged mechanics from v1) ---------- */

function Workspace({ p, me, byId, actions, iAmDrafter, iAmRequester, iHoldBaton, gotoDoc, draft, setDraft, dirty, existingClient = null }) {
  const [slots, setSlots] = useState([]);
  const [label, setLabel] = useState("");
  const [kind, setKind] = useState("document");
  const [genBusy, setGenBusy] = useState(false);

  const outstanding = p.checklist.filter((s) => ["pending", "rejected"].includes(s.status));
  const waivers = p.checklist.filter((s) => s.status === "waiver_requested");
  const managerCanReturn = p.status === "docs_with_manager" && outstanding.length === 0;

  return (
    <div className="grid grid-cols-5 gap-5 mt-5">
      <div className="col-span-2 space-y-5">
        <section className="bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
          <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Documents on file ({p.docs.length})</h3>
          <div className="mt-2 space-y-1.5">
            {p.docs.map((d) => (
              <div key={d.id} className="text-xs font-mono2 flex items-center gap-2 px-2.5 py-1.5 rounded-md border" style={{ borderColor: "var(--line)" }}>
                <span className="flex-1 truncate"><FileLink name={d.name} url={d.url} size={d.size} /></span>
                <span className="shrink-0" style={{ color: "var(--mut)" }}>{byId(d.by).name.split(" ")[0]} · {fmtDT(d.at)}</span>
              </div>
            ))}
            {p.docs.length === 0 && <div className="text-xs" style={{ color: "var(--mut)" }}>Nothing attached yet.</div>}
          </div>
        </section>
        {existingClient && (
          <section className="bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
            <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Already on file — {existingClient.code}</h3>
            <p className="text-xs mt-0.5 mb-2" style={{ color: "var(--mut)" }}>The client's document registry, read-only — check here before requesting anything the firm already holds.</p>
            <ClientDocuments clientId={existingClient.id} />
          </section>
        )}
        <section className="bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
          <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Request brief</h3>
          <div className="mt-2 text-sm space-y-1.5">
            <div><span style={{ color: "var(--mut)" }}>Services: </span>{p.services.map((s) => s.name + (s.custom ? " (custom)" : "")).join(", ")}</div>
            {p.prospect.contactPerson && <div><span style={{ color: "var(--mut)" }}>Contact: </span>{p.prospect.contactPerson} · {p.prospect.email} · {p.prospect.phone}</div>}
            {p.notes && <div><span style={{ color: "var(--mut)" }}>Notes: </span>{p.notes}</div>}
          </div>
        </section>
      </div>

      <div className="col-span-3 space-y-5">
        <section className="bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
          <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Requirements checklist</h3>
          <p className="text-xs mt-0.5" style={{ color: "var(--mut)" }}>The formal state machine: the baton cannot pass back until every slot is provided or waived. The system counts — not memory.</p>
          <div className="mt-3 space-y-2">
            {p.checklist.map((s) => <Slot key={s.id} s={s} p={p} me={me} byId={byId} actions={actions} iAmDrafter={iAmDrafter} iAmRequester={iAmRequester} />)}
            {p.checklist.length === 0 && <div className="text-xs py-2" style={{ color: "var(--mut)" }}>No requirements raised{iAmDrafter && p.status === "assigned" ? " — review the documents; if anything is missing, build a request below." : "."}</div>}
          </div>

          {iAmDrafter && iHoldBaton && ["assigned", "drafting", "waiver_review"].includes(p.status) && (
            <div className="mt-4 pt-4 border-t" style={{ borderColor: "var(--line)" }}>
              <div className="text-xs font-semibold mb-2" style={{ color: "var(--mut)" }}>Request more from the manager</div>
              <div className="flex gap-2">
                <select value={kind} onChange={(e) => setKind(e.target.value)} className="border rounded-md px-2 py-2 text-sm" style={{ borderColor: "var(--line)" }}>
                  <option value="document">Document</option><option value="data">Information</option>
                </select>
                <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={kind === "document" ? "e.g. MOA copy / UBO details / Bank statements Jan–Mar" : "e.g. Confirm annual fee for VAT filing"} className="flex-1 border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
                <button onClick={() => { if (label.trim()) { setSlots([...slots, { kind, label: label.trim() }]); setLabel(""); } }} className="px-3 py-2 rounded-md border text-sm font-medium" style={{ borderColor: "var(--line)" }}>Add</button>
              </div>
              {slots.length > 0 && (
                <div className="mt-2 space-y-1">
                  {slots.map((s, i) => (
                    <div key={i} className="text-xs flex items-center gap-2 px-2.5 py-1.5 rounded-md" style={{ background: "var(--accent-soft)" }}>
                      <span className="uppercase font-bold text-[9px] tracking-wider" style={{ color: "var(--accent)" }}>{s.kind}</span>
                      <span className="flex-1">{s.label}</span>
                      <button onClick={() => setSlots(slots.filter((_, j) => j !== i))} style={{ color: "var(--red)" }}>×</button>
                    </div>
                  ))}
                  <button onClick={() => { actions.sendChecklist(p.id, slots); setSlots([]); }} className="mt-2 px-4 py-2 rounded-lg text-white text-sm font-semibold" style={{ background: "var(--amber)" }}>
                    Send request to {byId(p.requestedBy).name} — baton passes to them
                  </button>
                </div>
              )}
            </div>
          )}

          {iAmRequester && p.status === "docs_with_manager" && iHoldBaton && (
            <div className="mt-4 pt-4 border-t" style={{ borderColor: "var(--line)" }}>
              {managerCanReturn ? (
                <button onClick={() => actions.managerReturn(p.id)} className="px-4 py-2 rounded-lg text-white text-sm font-semibold" style={{ background: "var(--accent)" }}>
                  Return to {byId(p.assignedTo).name} — all items answered
                </button>
              ) : (
                <div className="text-xs px-3 py-2.5 rounded-lg font-medium" style={{ background: "var(--red-soft)", color: "var(--red)" }}>
                  🔒 {outstanding.length} item(s) still unanswered. This cannot return to the drafter until every requested item is attached, answered, or marked not-available with a reason. The baton — and the clock — stay with you.
                </div>
              )}
            </div>
          )}

          {iAmDrafter && p.status === "waiver_review" && iHoldBaton && waivers.length === 0 && (
            outstanding.length > 0 ? (
              <button onClick={() => actions.staffSendBack(p.id)} className="mt-4 px-4 py-2 rounded-lg text-white text-sm font-semibold" style={{ background: "var(--amber)" }}>
                Send {outstanding.length} outstanding item(s) back to manager
              </button>
            ) : (
              <button onClick={() => actions.startDrafting(p.id)} className="mt-4 px-4 py-2 rounded-lg text-white text-sm font-semibold" style={{ background: "var(--accent)" }}>
                Checklist satisfied — start drafting
              </button>
            )
          )}
        </section>

        {iAmDrafter && iHoldBaton && ["assigned", "drafting"].includes(p.status) && (
          <section className="bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
            <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Draft the proposal</h3>
            <p className="text-xs mt-0.5 mb-3" style={{ color: "var(--mut)" }}>Structured fields — the document is generated from these, never typed by hand.</p>
            <DraftForm draft={draft} setDraft={setDraft} onPolish={(rough) => actions.polishTerms(p.id, rough)} />
            <div className="flex items-center gap-2 mt-3 flex-wrap">
              <button onClick={async () => { setGenBusy(true); await actions.generateVersion(p.id, draft, p.versions.length ? `redrafted by ${me.name.split(" ")[0]}` : `drafted by ${me.name.split(" ")[0]}`); setGenBusy(false); gotoDoc(); }} disabled={genBusy || draft.lines.some((l) => !l.fee) || !draft.paymentTerms} className="px-4 py-2 rounded-lg text-white text-sm font-semibold disabled:opacity-40" style={{ background: "var(--ink)" }}>
                {genBusy ? "Generating — professionalizing wording…" : `Generate proposal v${p.versions.length + 1} — preview first`}
              </button>
              {p.versions.length > 0 && !genBusy && (
                <button disabled={dirty} onClick={() => actions.submitToManager(p.id)} className="px-4 py-2 rounded-lg text-white text-sm font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>
                  Send v{p.versions.length} to {byId(p.requestedBy).name} →
                </button>
              )}
            </div>
            {dirty && (
              <div className="mt-2 text-[11px] px-2.5 py-2 rounded-md font-medium" style={{ background: "var(--red-soft)", color: "var(--red)" }}>
                🔒 Your form has edits that are NOT in v{p.versions.length}. Generate v{p.versions.length + 1} first — the system will not let an outdated document be sent.
              </div>
            )}
            <p className="text-[11px] mt-2" style={{ color: "var(--mut)" }}>
              On generation the CRM automatically rewrites rough payment-term notes into professional, client-ready language (figures untouched; the original stays in the audit trail). Review the document — nothing moves to the manager until you explicitly send it.
            </p>
            {(draft.lines.some((l) => !l.fee) || !draft.paymentTerms) && (
              <div className="mt-2 p-3 rounded-lg" style={{ background: "var(--amber-soft)" }}>
                <div className="text-[11px] font-medium" style={{ color: "var(--amber)" }}>
                  Missing: {[...draft.lines.filter((l) => !l.fee).map((l) => `fee for ${l.service}`), ...(!draft.paymentTerms ? ["payment terms"] : [])].join(", ")}.
                </div>
                <button onClick={() => {
                  const s2 = [...draft.lines.filter((l) => !l.fee).map((l) => ({ kind: "data", label: `Confirm fee for ${l.service} (${l.basis || defaultBasis(l.service)})` })), ...(!draft.paymentTerms ? [{ kind: "data", label: "Confirm payment terms" }] : [])];
                  actions.sendChecklist(p.id, s2);
                }} className="mt-2 px-3 py-1.5 rounded-md text-white text-xs font-semibold" style={{ background: "var(--amber)" }}>
                  Request missing items from {byId(p.requestedBy).name} — baton passes to them
                </button>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

function Slot({ s, p, me, byId, actions, iAmDrafter, iAmRequester }) {
  const [val, setVal] = useState("");
  const [naReason, setNaReason] = useState("");
  const [naMode, setNaMode] = useState(false);
  const [rejReason, setRejReason] = useState("");
  const [rejMode, setRejMode] = useState(false);
  const [wdReason, setWdReason] = useState("");
  const [wdMode, setWdMode] = useState(false);
  const managerTurn = iAmRequester && p.status === "docs_with_manager" && p.holder === me.id;
  const staffTurn = iAmDrafter && p.holder === me.id;
  const canWithdraw = iAmDrafter && ["pending", "rejected", "waiver_requested"].includes(s.status) && !["el_sent", "lost"].includes(p.status);

  const chip = {
    pending: ["Pending", "var(--amber)"],
    rejected: ["Rejected — redo", "var(--red)"],
    provided: ["Provided", "var(--accent)"],
    waiver_requested: ["Not available — waiver requested", "var(--amber)"],
    waived: ["Waived", "var(--mut)"],
    withdrawn: ["Withdrawn by drafter", "var(--mut)"],
  }[s.status];

  return (
    <div className="border rounded-lg p-3" style={{ borderColor: "var(--line)" }}>
      <div className="flex items-center gap-2 text-sm">
        <span className="text-[9px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>{s.kind}</span>
        <span className="flex-1 font-medium">{s.label}</span>
        <span className="text-[11px] px-2 py-0.5 rounded-full font-medium" style={{ background: chip[1] + "18", color: chip[1] }}>{chip[0]}</span>
      </div>
      {(s.fileName || s.value) && (
        <div className="text-xs mt-1.5 font-mono2" style={{ color: "var(--mut)" }}>
          ↳ {s.fileName ? <FileLink name={s.fileName} url={s.fileUrl} size={s.fileSize} /> : s.value}
        </div>
      )}
      {s.reason && <div className="text-xs mt-1" style={{ color: "var(--red)" }}>Reason: {s.reason}</div>}

      {managerTurn && ["pending", "rejected"].includes(s.status) && (
        <div className="mt-2.5">
          {!naMode ? (
            <div className="flex gap-2 items-center">
              {s.kind === "document" ? (
                <FilePick small label="Upload document" onFiles={(fs) => { const f = fs[0]; actions.fulfillSlot(p.id, s.id, { status: "provided", fileName: f.name, fileUrl: f.url, fileSize: f.size, reason: "" }, `Checklist item "${s.label}" attached: ${f.name}`); }} />
              ) : (
                <>
                  <input value={val} onChange={(e) => setVal(e.target.value)} placeholder="Type the information" className="flex-1 border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
                  <button onClick={() => { if (!val.trim()) return; actions.fulfillSlot(p.id, s.id, { status: "provided", value: val.trim(), reason: "" }, `Checklist item "${s.label}" answered`); setVal(""); }} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold" style={{ background: "var(--accent)" }}>Answer</button>
                </>
              )}
              <button onClick={() => setNaMode(true)} className="px-2.5 py-1.5 rounded-md border text-xs" style={{ borderColor: "var(--line)", color: "var(--mut)" }}>Not available</button>
            </div>
          ) : (
            <div className="flex gap-2">
              <input value={naReason} onChange={(e) => setNaReason(e.target.value)} placeholder="Mandatory reason — why can't this be obtained?" className="flex-1 border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
              <button onClick={() => { if (!naReason.trim()) return; actions.fulfillSlot(p.id, s.id, { status: "waiver_requested", reason: naReason.trim() }, `Item "${s.label}" marked NOT AVAILABLE — reason: ${naReason.trim()} (waiver requested)`); setNaMode(false); }} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold" style={{ background: "var(--amber)" }}>Request waiver</button>
              <button onClick={() => setNaMode(false)} className="text-xs" style={{ color: "var(--mut)" }}>cancel</button>
            </div>
          )}
        </div>
      )}

      {staffTurn && s.status === "provided" && p.status === "waiver_review" && (
        <div className="mt-2.5">
          {!rejMode ? (
            <button onClick={() => setRejMode(true)} className="text-xs underline" style={{ color: "var(--red)" }}>Reject this item (wrong / unusable)</button>
          ) : (
            <div className="flex gap-2">
              <input value={rejReason} onChange={(e) => setRejReason(e.target.value)} placeholder="Why is it unusable?" className="flex-1 border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
              <button onClick={() => { if (!rejReason.trim()) return; actions.fulfillSlot(p.id, s.id, { status: "rejected", reason: rejReason.trim() }, `Item "${s.label}" REJECTED by drafter — ${rejReason.trim()}`); setRejMode(false); }} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold" style={{ background: "var(--red)" }}>Reject</button>
            </div>
          )}
        </div>
      )}

      {staffTurn && s.status === "waiver_requested" && (
        <div className="mt-2.5 flex gap-2">
          <button onClick={() => actions.fulfillSlot(p.id, s.id, { status: "waived" }, `Waiver ACCEPTED for "${s.label}" — proceeding without it`)} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold" style={{ background: "var(--accent)" }}>Accept waiver — proceed without it</button>
          <button onClick={() => actions.fulfillSlot(p.id, s.id, { status: "pending", reason: "Waiver rejected — item is required to proceed" }, `Waiver REJECTED for "${s.label}" — item remains required`)} className="px-3 py-1.5 rounded-md border text-xs font-semibold" style={{ borderColor: "var(--red)", color: "var(--red)" }}>Reject — still required</button>
        </div>
      )}

      {canWithdraw && (
        <div className="mt-2">
          {!wdMode ? (
            <button onClick={() => setWdMode(true)} className="text-[11px] underline" style={{ color: "var(--mut)" }}>Withdraw this request (no longer needed)</button>
          ) : (
            <div className="flex gap-2">
              <input value={wdReason} onChange={(e) => setWdReason(e.target.value)} placeholder="Mandatory reason — e.g. manager confirmed in chat it's not required for this engagement" className="flex-1 border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
              <button onClick={() => { if (!wdReason.trim()) return; actions.withdrawSlot(p.id, s.id, wdReason.trim()); setWdMode(false); setWdReason(""); }} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold" style={{ background: "var(--mut)" }}>Withdraw</button>
              <button onClick={() => setWdMode(false)} className="text-xs" style={{ color: "var(--mut)" }}>cancel</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function DraftForm({ draft, setDraft, onPolish }) {
  const [polishing, setPolishing] = useState(false);
  const [suggest, setSuggest] = useState(null);

  const polish = async () => {
    setPolishing(true); setSuggest(null);
    const text = await onPolish(draft.paymentTerms);
    setSuggest(text || "__err");
    setPolishing(false);
  };

  return (
    <div className="space-y-3">
      <div>
        <div className="text-xs font-semibold mb-1.5" style={{ color: "var(--mut)" }}>Service lines & fees (AED)</div>
        {draft.lines.map((l, i) => (
          <div key={i} className="flex gap-2 mb-1.5 items-center text-sm">
            <span className="flex-1">{l.service}</span>
            <input value={l.fee} onChange={(e) => { const lines = [...draft.lines]; lines[i] = { ...l, fee: e.target.value }; setDraft({ ...draft, lines }); }} placeholder="AED" className="w-28 border rounded-md px-2.5 py-1.5 text-sm font-mono2" style={{ borderColor: "var(--line)" }} />
            <select value={l.basis || defaultBasis(l.service)} onChange={(e) => { const lines = [...draft.lines]; lines[i] = { ...l, basis: e.target.value }; setDraft({ ...draft, lines }); }} className="border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }}>
              {BASIS.map((b) => <option key={b}>{b}</option>)}
            </select>
          </div>
        ))}
      </div>
      <div>
        <div className="flex items-center justify-between">
          <label className="text-xs font-semibold" style={{ color: "var(--mut)" }}>Payment terms *</label>
          <button onClick={polish} disabled={!draft.paymentTerms || polishing} className="text-[11px] px-2.5 py-1 rounded-md border font-semibold disabled:opacity-40" style={{ borderColor: "var(--line)", color: "var(--accent)" }}>
            {polishing ? "Rewording…" : "✨ Reword professionally"}
          </button>
        </div>
        <input value={draft.paymentTerms} onChange={(e) => setDraft({ ...draft, paymentTerms: e.target.value })} placeholder="Rough notes are fine — e.g. 'bookkeeping monthly last date of month, VAT quarterly in advance'" className="mt-1 w-full border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
        {suggest === "__err" && (
          <div className="mt-1.5 text-[11px] px-2.5 py-2 rounded-md" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>
            Couldn't reach the drafting assistant — keep your wording or edit it manually.
          </div>
        )}
        {suggest && suggest !== "__err" && (
          <div className="mt-1.5 border rounded-lg p-3" style={{ borderColor: "var(--accent)", background: "var(--accent-soft)" }}>
            <div className="text-[10px] uppercase tracking-wider font-bold" style={{ color: "var(--accent)" }}>Suggested wording — verify every figure before accepting</div>
            <div className="text-sm mt-1.5">{suggest}</div>
            <div className="flex gap-2 mt-2.5">
              <button onClick={() => { setDraft({ ...draft, paymentTerms: suggest }); setSuggest(null); }} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold" style={{ background: "var(--accent)" }}>Use this wording</button>
              <button onClick={() => setSuggest(null)} className="px-3 py-1.5 rounded-md border text-xs font-semibold bg-white" style={{ borderColor: "var(--line)" }}>Keep mine</button>
            </div>
          </div>
        )}
      </div>
      <div className="flex gap-3">
        <div>
          <label className="text-xs font-semibold" style={{ color: "var(--mut)" }}>Validity (days)</label>
          <input type="number" value={draft.validityDays} onChange={(e) => setDraft({ ...draft, validityDays: e.target.value })} className="mt-1 w-28 border rounded-md px-3 py-2 text-sm font-mono2" style={{ borderColor: "var(--line)" }} />
        </div>
        <div className="flex-1">
          <label className="text-xs font-semibold" style={{ color: "var(--mut)" }}>Scope notes (optional)</label>
          <input value={draft.scope} onChange={(e) => setDraft({ ...draft, scope: e.target.value })} placeholder="Any scope clarifications to print" className="mt-1 w-full border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
        </div>
      </div>
    </div>
  );
}

/* ---------- proposal document tab with dual-signature flow ---------- */

function DocTab({ p, byId, firm, me, users, actions, iAmRequester, formDirty }) {
  const latest = p.versions[p.versions.length - 1];
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState(p.draft);
  const [genBusy, setGenBusy] = useState(false);
  const [revComment, setRevComment] = useState("");
  const [seniorComment, setSeniorComment] = useState("");
  const [signatoryPick, setSignatoryPick] = useState("");
  const [confirmSig, setConfirmSig] = useState(false);
  const [rejNote, setRejNote] = useState("");
  const [rejMode, setRejMode] = useState(false);
  const [viewV, setViewV] = useState(null);
  const [cmp, setCmp] = useState(null);
  const [cmpA, setCmpA] = useState(1);
  const [cmpB, setCmpB] = useState(2);
  const [approveOldMode, setApproveOldMode] = useState(false);
  const [confirmSigOld, setConfirmSigOld] = useState(false);

  const managerMayAct = iAmRequester && p.status === "manager_review";
  const iAmSenior = me.id === p.signatoryId && p.status === "senior_review";
  const signatories = users.filter((u) => u.role === "Admin" && u.signatory && u.id !== me.id); // counter-signature = senior management only
  const canReview = me.role === "Manager" || me.role === "Admin";

  if (!latest) return <div className="mt-8 text-sm text-center" style={{ color: "var(--mut)" }}>No document generated yet — the drafter generates v1 from the workspace once fees and terms are in place.</div>;

  const shown = (viewV && p.versions.find((v) => v.v === viewV)) || latest;
  const viewingOld = shown.v !== latest.v;
  const d = edit ? draft : shown.data;
  const totals = d.lines.reduce((acc, l) => { const b = l.basis || defaultBasis(l.service); acc[b] = (acc[b] || 0) + num(l.fee); return acc; }, {});
  const locked = !!p.signatures.senior;

  return (
    <div className="grid grid-cols-3 gap-5 mt-5">
      <div className="col-span-2">
        {viewingOld && (
          <div className="mb-3 rounded-xl border p-3 flex items-center gap-3 text-sm" style={{ background: "var(--amber-soft)", borderColor: "#E4C99A" }}>
            <span>📜</span>
            <span className="flex-1" style={{ color: "#6B5A38" }}><b>Viewing superseded v{shown.v}</b> ({shown.note} — {byId(shown.by).name}, {fmtDT(shown.at)}). The current version is v{latest.v}. Superseded versions are immutable and shown unsigned.</span>
            <button onClick={() => setViewV(null)} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold whitespace-nowrap" style={{ background: "var(--amber)" }}>Back to current v{latest.v}</button>
          </div>
        )}
        <div className="bg-white border rounded-xl overflow-hidden shadow-sm" style={{ borderColor: "var(--line)", opacity: viewingOld ? 0.94 : 1 }}>
          <div className="px-8 pt-7 pb-5 border-b-4" style={{ borderColor: firm.accent }}>
            <div className="font-disp text-xl font-bold" style={{ color: "var(--ink)" }}>{firm.name}</div>
            <div className="text-[11px] mt-1" style={{ color: "var(--mut)" }}>{firm.address} · {firm.phone} · {firm.email} · {firm.trn}</div>
          </div>
          <div className="px-8 py-6 text-sm leading-relaxed">
            <div className="flex justify-between text-xs font-mono2" style={{ color: "var(--mut)" }}>
              <span>Ref: {p.id}/v{shown.v}{viewingOld ? " · SUPERSEDED" : locked ? " · LOCKED" : ""}</span><span>{fmtD(shown.at)}</span>
            </div>
            <h2 className="font-disp text-lg font-bold mt-4" style={{ color: "var(--ink)" }}>Proposal for Professional Services</h2>
            <p className="mt-3">To: <b>{p.prospect.name}</b>{p.prospect.contactPerson && <> — Attn: {p.prospect.contactPerson}</>}</p>
            <p className="mt-2">We are pleased to submit our proposal for the following professional services:</p>
            <table className="w-full mt-4 text-sm">
              <thead><tr className="text-left text-[11px] uppercase tracking-wider border-b" style={{ color: "var(--mut)", borderColor: "var(--line)" }}><th className="py-1.5">Service</th><th className="py-1.5 text-right">Professional fee (AED)</th></tr></thead>
              <tbody>
                {d.lines.map((l, i) => (
                  <tr key={i} className="border-b" style={{ borderColor: "var(--line)" }}>
                    <td className="py-2">{l.service}</td>
                    <td className="py-2 text-right font-mono2">{l.fee ? <>{num(l.fee).toLocaleString()} <span className="text-[11px]" style={{ color: "var(--mut)" }}>{l.basis || defaultBasis(l.service)}</span></> : "—"}</td>
                  </tr>
                ))}
                {Object.entries(totals).map(([b, t]) => (
                  <tr key={b}><td className="py-1.5 font-bold text-xs pt-3">Total — {b === "one-time" ? "one-time fees" : "recurring, " + b}</td><td className="py-1.5 pt-3 text-right font-mono2 font-bold">{t.toLocaleString()} <span className="text-[11px] font-normal" style={{ color: "var(--mut)" }}>{b}</span></td></tr>
                ))}
              </tbody>
            </table>
            <p className="mt-4"><b>Payment terms:</b> {d.paymentTerms}</p>
            {d.scope && <p className="mt-2"><b>Scope notes:</b> {d.scope}</p>}
            <p className="mt-2"><b>Validity:</b> This proposal is valid for {d.validityDays} days from the date above.</p>
            <p className="mt-6" style={{ color: "var(--mut)" }}>For and on behalf of {firm.name}</p>
            <div className="mt-3 min-h-[70px]">
              {viewingOld ? <span className="text-xs italic" style={{ color: "var(--mut)" }}>— superseded version · signatures apply to the current version only —</span> : (
                <>
                  {p.signatures.manager && <SigBlock user={byId(p.signatures.manager.by)} at={p.signatures.manager.at} role="Engagement Manager" />}
                  {p.signatures.senior && <SigBlock user={byId(p.signatures.senior.by)} at={p.signatures.senior.at} role="Senior Management" />}
                  {!p.signatures.manager && !p.signatures.senior && <span className="text-xs italic" style={{ color: "var(--mut)" }}>— unsigned draft —</span>}
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="space-y-4">
        {!viewingOld && me.id === p.assignedTo && p.holder === me.id && ["assigned", "drafting"].includes(p.status) && (
          <section className="bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
            <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Drafter preview — v{latest.v}</h3>
            <p className="text-[11px] mt-0.5 mb-3" style={{ color: "var(--mut)" }}>This is exactly what the manager will see. If something's off, go back to the Workspace, fix the fields and regenerate. Nothing has been sent yet.</p>
            {formDirty && (
              <div className="mb-2 text-[11px] px-2.5 py-2 rounded-md font-medium" style={{ background: "var(--red-soft)", color: "var(--red)" }}>
                🔒 Your Workspace form has edits that are NOT in this v{latest.v}. Regenerate first — the system won't send an outdated document.
              </div>
            )}
            <button disabled={formDirty} onClick={() => actions.submitToManager(p.id)} className="w-full px-3 py-2 rounded-md text-white text-sm font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>
              Looks right — send v{latest.v} to {byId(p.requestedBy).name} →
            </button>
          </section>
        )}

        {!viewingOld && managerMayAct && (
          <section className="bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
            <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Manager review</h3>
            <p className="text-[11px] mt-0.5 mb-3" style={{ color: "var(--mut)" }}>After client discussions you have three moves: adjust terms yourself, return to the drafter with an instruction, or route to the senior — with your comment travelling either way.</p>
            {!edit ? (
              <div className="flex flex-col gap-2">
                <button onClick={() => { setDraft(structuredClone(latest.data)); setEdit(true); }} className="px-3 py-2 rounded-md border text-sm font-semibold" style={{ borderColor: "var(--line)" }}>Edit fees / terms myself</button>

                <div className="pt-3 border-t" style={{ borderColor: "var(--line)" }}>
                  <div className="text-[11px] font-bold uppercase tracking-wider" style={{ color: "var(--amber)" }}>Option 1 · Return to drafter</div>
                  <textarea value={revComment} onChange={(e) => setRevComment(e.target.value)} rows={2} placeholder={`Instruction to ${byId(p.assignedTo).name} — e.g. "Spoke with client: bookkeeping confirmed at AED 3,000/month. Revise and regenerate."`} className="mt-1.5 w-full border rounded-md px-2.5 py-2 text-xs" style={{ borderColor: "var(--line)" }} />
                  <button disabled={!revComment.trim()} onClick={() => actions.sendForRevision(p.id, revComment.trim())} className="mt-1.5 w-full px-3 py-2 rounded-md text-white text-sm font-semibold disabled:opacity-40" style={{ background: "var(--amber)" }}>
                    Send to {byId(p.assignedTo).name} for revision →
                  </button>
                  <div className="text-[10px] mt-1" style={{ color: "var(--mut)" }}>Comment is mandatory — the drafter must know what was agreed.</div>
                </div>

                <div className="pt-3 border-t" style={{ borderColor: "var(--line)" }}>
                  <div className="text-[11px] font-bold uppercase tracking-wider" style={{ color: "var(--accent)" }}>Option 2 · Sign & route to senior</div>
                  <textarea value={seniorComment} onChange={(e) => setSeniorComment(e.target.value)} rows={2} placeholder={`Note to the signatory (optional) — e.g. "Discussed with client: AED 2,500 is final for this engagement. Please proceed."`} className="mt-1.5 w-full border rounded-md px-2.5 py-2 text-xs" style={{ borderColor: "var(--line)" }} />
                  <select value={signatoryPick} onChange={(e) => setSignatoryPick(e.target.value)} className="mt-1.5 w-full border rounded-md px-2 py-2 text-sm" style={{ borderColor: "var(--line)" }}>
                    <option value="">Select senior signatory…</option>
                    {signatories.map((u) => <option key={u.id} value={u.id}>{u.name} — {u.designation}</option>)}
                  </select>
                  <label className="flex items-start gap-2 mt-2 text-[11px]" style={{ color: "var(--mut)" }}>
                    <input type="checkbox" checked={confirmSig} onChange={(e) => setConfirmSig(e.target.checked)} className="mt-0.5" />
                    I re-confirm my identity and authorize applying <b>my own</b> signature to this document (in production: password / 2FA re-entry).
                  </label>
                  <button disabled={!signatoryPick || !confirmSig} onClick={() => actions.managerSignRoute(p.id, signatoryPick, seniorComment.trim())} className="mt-2 w-full px-3 py-2 rounded-md text-white text-sm font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>
                    Approve, sign & route ✍️
                  </button>
                </div>
              </div>
            ) : (
              <>
                <DraftForm draft={draft} setDraft={setDraft} onPolish={(rough) => actions.polishTerms(p.id, rough)} />
                <div className="flex gap-2 mt-3">
                  <button disabled={genBusy} onClick={async () => { setGenBusy(true); await actions.generateVersion(p.id, draft, `terms revised by ${me.name.split(" ")[0]}`); setGenBusy(false); setEdit(false); }} className="px-3 py-2 rounded-md text-white text-sm font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>{genBusy ? "Professionalizing…" : `Regenerate v${p.versions.length + 1}`}</button>
                  <button onClick={() => setEdit(false)} className="px-3 py-2 rounded-md border text-sm" style={{ borderColor: "var(--line)" }}>Cancel</button>
                </div>
              </>
            )}
          </section>
        )}

        {!viewingOld && iAmSenior && (
          <section className="bg-white border rounded-xl p-4" style={{ borderColor: "#D8CBEF", background: "#F8F5FE" }}>
            <h3 className="font-disp font-bold text-sm" style={{ color: "#5C4A8A" }}>Senior review & counter-signature</h3>
            {p.seniorNote && (
              <div className="mt-2 rounded-lg p-2.5 text-xs bg-white border" style={{ borderColor: "#D8CBEF" }}>
                <b style={{ color: "#5C4A8A" }}>Note from {byId(p.seniorNote.by).name}:</b> “{p.seniorNote.text}”
              </div>
            )}
            <p className="text-[11px] mt-2 mb-3" style={{ color: "var(--mut)" }}>You may adjust pricing & payment terms before signing. Any edit regenerates the document and notifies the manager and drafter.</p>
            {!edit ? (
              <div className="flex flex-col gap-2">
                <button onClick={() => { setDraft(structuredClone(latest.data)); setEdit(true); }} className="px-3 py-2 rounded-md border text-sm font-semibold bg-white" style={{ borderColor: "var(--line)" }}>Edit pricing / payment terms</button>
                <label className="flex items-start gap-2 text-[11px]" style={{ color: "var(--mut)" }}>
                  <input type="checkbox" checked={confirmSig} onChange={(e) => setConfirmSig(e.target.checked)} className="mt-0.5" />
                  I re-confirm my identity and authorize applying <b>my own</b> signature (in production: password / 2FA re-entry).
                </label>
                <button disabled={!confirmSig} onClick={() => actions.seniorApprove(p.id)} className="px-3 py-2 rounded-md text-white text-sm font-semibold disabled:opacity-40" style={{ background: "#5C4A8A" }}>
                  Approve & counter-sign ✍️ — lock document
                </button>
                {!rejMode ? (
                  <button onClick={() => setRejMode(true)} className="px-3 py-2 rounded-md border text-sm font-semibold bg-white" style={{ borderColor: "var(--red)", color: "var(--red)" }}>Reject with note…</button>
                ) : (
                  <div className="flex flex-col gap-2">
                    <input value={rejNote} onChange={(e) => setRejNote(e.target.value)} placeholder="Mandatory note — what must change?" className="border rounded-md px-2.5 py-2 text-xs bg-white" style={{ borderColor: "var(--line)" }} />
                    <button onClick={() => { if (rejNote.trim()) actions.seniorReject(p.id, rejNote.trim()); }} className="px-3 py-2 rounded-md text-white text-xs font-semibold" style={{ background: "var(--red)" }}>Confirm rejection — back to manager (voids manager signature)</button>
                  </div>
                )}
              </div>
            ) : (
              <>
                <DraftForm draft={draft} setDraft={setDraft} onPolish={(rough) => actions.polishTerms(p.id, rough)} />
                <div className="flex gap-2 mt-3">
                  <button disabled={genBusy} onClick={async () => { setGenBusy(true); await actions.generateVersion(p.id, draft, `revised at senior review by ${me.name.split(" ")[0]}`); setGenBusy(false); setEdit(false); }} className="px-3 py-2 rounded-md text-white text-sm font-semibold disabled:opacity-40" style={{ background: "#5C4A8A" }}>{genBusy ? "Professionalizing…" : `Regenerate v${p.versions.length + 1}`}</button>
                  <button onClick={() => setEdit(false)} className="px-3 py-2 rounded-md border text-sm bg-white" style={{ borderColor: "var(--line)" }}>Cancel</button>
                </div>
              </>
            )}
          </section>
        )}

        {viewingOld && iAmSenior && (
          <section className="border rounded-xl p-4" style={{ borderColor: "#D8CBEF", background: "#F8F5FE" }}>
            <h3 className="font-disp font-bold text-sm" style={{ color: "#5C4A8A" }}>Approve these earlier terms</h3>
            {shown.signatures?.manager ? (
              !approveOldMode ? (
                <>
                  <p className="text-[11px] mt-2 mb-2" style={{ color: "var(--mut)" }}>
                    v{shown.v} carried {byId(shown.signatures.manager.by).name}'s signature when it was routed.
                    Approving it re-issues the identical content as v{latest.v + 1}, re-applies their signature
                    (content unchanged from what they signed), and applies your counter-signature — the document
                    locks exactly as a normal approval.
                  </p>
                  <button onClick={() => { setApproveOldMode(true); setConfirmSigOld(false); }} className="w-full px-3 py-2 rounded-md text-white text-sm font-semibold" style={{ background: "#5C4A8A" }}>
                    Approve these terms instead — issues v{latest.v + 1} identical to v{shown.v}
                  </button>
                </>
              ) : (
                <div className="mt-2 flex flex-col gap-2">
                  <div className="text-[11px] px-2.5 py-2 rounded-md bg-white border" style={{ borderColor: "#D8CBEF", color: "var(--mut)" }}>
                    Confirm: issue <b>v{latest.v + 1}</b> with content identical to <b>v{shown.v}</b>,
                    re-apply {byId(shown.signatures.manager.by).name}'s signature, counter-sign and lock.
                    v{latest.v} becomes superseded. {byId(shown.signatures.manager.by).name} is notified.
                  </div>
                  <label className="flex items-start gap-2 text-[11px]" style={{ color: "var(--mut)" }}>
                    <input type="checkbox" checked={confirmSigOld} onChange={(e) => setConfirmSigOld(e.target.checked)} className="mt-0.5" />
                    I re-confirm my identity and authorize applying <b>my own</b> signature (in production: password / 2FA re-entry).
                  </label>
                  <button disabled={!confirmSigOld} onClick={() => { actions.approveVersion(p.id, shown.v); setApproveOldMode(false); setViewV(null); }} className="px-3 py-2 rounded-md text-white text-sm font-semibold disabled:opacity-40" style={{ background: "#5C4A8A" }}>
                    Confirm — approve v{shown.v} terms ✍️
                  </button>
                  <button onClick={() => setApproveOldMode(false)} className="text-xs underline text-left" style={{ color: "var(--mut)" }}>cancel</button>
                </div>
              )
            ) : (
              <p className="text-[11px] mt-2" style={{ color: "var(--mut)" }}>
                v{shown.v} was never manager-signed when routed, so it cannot be approved directly.
                Reject the current version with a note, or have the manager re-route these terms.
              </p>
            )}
          </section>
        )}

        <section className="bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
          <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Version history</h3>
          {canReview && p.versions.length > 1 && (
            <div className="mt-2 pb-3 border-b flex items-center gap-1.5 text-xs" style={{ borderColor: "var(--line)" }}>
              <span style={{ color: "var(--mut)" }}>Compare</span>
              <select value={cmpA} onChange={(e) => setCmpA(Number(e.target.value))} className="border rounded-md px-1.5 py-1" style={{ borderColor: "var(--line)" }}>
                {p.versions.map((v) => <option key={v.v} value={v.v}>v{v.v}</option>)}
              </select>
              <span style={{ color: "var(--mut)" }}>vs</span>
              <select value={cmpB} onChange={(e) => setCmpB(Number(e.target.value))} className="border rounded-md px-1.5 py-1" style={{ borderColor: "var(--line)" }}>
                {p.versions.map((v) => <option key={v.v} value={v.v}>v{v.v}</option>)}
              </select>
              <button disabled={cmpA === cmpB} onClick={() => setCmp({ a: p.versions.find((v) => v.v === cmpA), b: p.versions.find((v) => v.v === cmpB) })} className="ml-auto px-2.5 py-1 rounded-md text-white font-semibold disabled:opacity-40" style={{ background: "var(--ink)" }}>
                Comparison report
              </button>
            </div>
          )}
          <div className="mt-2 space-y-2">
            {[...p.versions].reverse().map((v) => (
              <div key={v.v} className="text-xs border rounded-md p-2.5" style={{ borderColor: shown.v === v.v ? "var(--accent)" : "var(--line)", background: shown.v === v.v ? "var(--accent-soft)" : "#fff" }}>
                <div className="flex justify-between items-center font-mono2">
                  <b>v{v.v}
                    {v.v === latest.v
                      ? <span className="ml-1.5 font-sans font-medium text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: "var(--accent)", color: "#fff" }}>current</span>
                      : <span className="ml-1.5 font-sans font-medium text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: "var(--paper)", color: "var(--mut)", border: "1px solid var(--line)" }}>superseded</span>}
                  </b>
                  <span style={{ color: "var(--mut)" }}>{fmtDT(v.at)}</span>
                </div>
                <div className="mt-0.5" style={{ color: "var(--mut)" }}>{v.note} — {byId(v.by).name}</div>
                {v.rejection && (
                  <div className="mt-1 font-medium" style={{ color: "var(--red)" }}>
                    ↩️ rejected by {byId(v.rejection.by).name}: “{v.rejection.note}”
                  </div>
                )}
                {v.revertedFrom && (
                  <div className="mt-1 font-medium" style={{ color: "var(--accent)" }}>
                    ⤴️ re-issued from v{v.revertedFrom} — terms approved as originally signed
                  </div>
                )}
                <div className="mt-0.5 font-mono2" style={{ color: "var(--mut)" }}>Σ line fees AED {v.data.lines.reduce((a, l) => a + num(l.fee), 0).toLocaleString()} (mixed basis)</div>
                {canReview && (
                  <div className="mt-1.5 flex gap-2">
                    {v.v !== shown.v && <button onClick={() => setViewV(v.v === latest.v ? null : v.v)} className="underline font-medium" style={{ color: "var(--accent)" }}>View this version</button>}
                    {v.v > 1 && <button onClick={() => setCmp({ a: p.versions.find((x) => x.v === v.v - 1), b: v })} className="underline font-medium" style={{ color: "var(--mut)" }}>Compare with v{v.v - 1}</button>}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>

        {cmp && <CompareModal a={cmp.a} b={cmp.b} byId={byId} onClose={() => setCmp(null)} />}
      </div>
    </div>
  );
}

/* ---------- engagement tab ---------- */

function EngTab({ p, byId, firm, me, users, client, workloadOf, actions, iAmRequester, now }) {
  const [mail, setMail] = useState(null);
  const [rejNote, setRejNote] = useState("");
  const [rejMode, setRejMode] = useState(false);
  const [confirmSig, setConfirmSig] = useState(false);
  const [signatoryPick, setSignatoryPick] = useState("");
  const [lostMode, setLostMode] = useState(false);
  const [lostNote, setLostNote] = useState("");
  const [unsignedMode, setUnsignedMode] = useState(false);
  const [confBasis, setConfBasis] = useState("email_approval");
  const [confNote, setConfNote] = useState("");
  const [confFiles, setConfFiles] = useState([]);
  const [confIdentity, setConfIdentity] = useState(false);
  const [elNote, setElNote] = useState(p.el?.note || "");
  useEffect(() => { setElNote(p.el?.note || ""); }, [p.id, p.status]);

  const d = p.versions[p.versions.length - 1]?.data;
  const iAmELSenior = p.el && me.id === p.el.signatoryId && p.status === "el_senior_review";
  const signatories = users.filter((u) => u.role === "Admin" && u.signatory && u.id !== p.requestedBy); // EL signature = senior management only
  const staff = users.filter((u) => u.role === "Staff");
  const firstBill = d ? d.lines.reduce((a, l) => a + num(l.fee), 0) : 0;
  const assignments = p.el?.assignments || {};
  const unassigned = d ? d.lines.filter((l) => !assignments[l.service]) : [];

  const openMail = (kind) => {
    const isProp = kind === "proposal";
    setMail({
      kind,
      to: p.prospect.email,
      subject: isProp ? `Proposal for professional services — ${firm.short} (Ref ${p.id})` : `Engagement letter — ${firm.short} (Ref ${p.id})`,
      body: isProp
        ? `Dear ${p.prospect.contactPerson || "Sir/Madam"},\n\nThank you for meeting with us. Please find attached our signed proposal (Ref ${p.id}) covering: ${d.lines.map((l) => l.service).join(", ")}.\n\nThe proposal is valid for ${d.validityDays} days. To confirm your acceptance, kindly sign and return a copy of the proposal.\n\nKind regards,\n${byId(p.requestedBy).name}\n${firm.name}`
        : `Dear ${p.prospect.contactPerson || "Sir/Madam"},\n\nFurther to your confirmation of our proposal (Ref ${p.id}), please find attached our signed engagement letter setting out the terms of our engagement. Our team has been assigned and work will commence per the letter.\n\nWe look forward to serving you.\n\nKind regards,\n${byId(p.requestedBy).name}\n${firm.name}`,
    });
  };

  return (
    <div className="mt-5 space-y-5">
      {/* stage rail */}
      <div className="bg-white border rounded-xl p-4 flex items-center gap-2 text-[11px] font-medium overflow-x-auto" style={{ borderColor: "var(--line)" }}>
        {[["signed", "Proposal signed"], ["proposal_sent", "Sent to client"], ["el_staffing", "Client confirmed · staffing"], ["el_senior_review", "EL — senior signature"], ["el_approved", "EL signed"], ["el_sent", "EL sent — complete"]].map(([k, l], i, arr) => {
          const order = arr.findIndex(([kk]) => kk === p.status);
          const done = i <= (order === -1 ? (p.status === "lost" ? -1 : 99) : order);
          return (
            <span key={k} className="flex items-center gap-2 whitespace-nowrap">
              <span className="px-2 py-1 rounded-full" style={done ? { background: "var(--accent-soft)", color: "var(--accent)" } : { background: "var(--paper)", color: "var(--mut)" }}>{l}</span>
              {i < arr.length - 1 && <span style={{ color: "var(--line)" }}>→</span>}
            </span>
          );
        })}
      </div>

      {/* SIGNED: send proposal to client */}
      {p.status === "signed" && iAmRequester && (
        <ActionCard title="Send the signed proposal to the client" sub={`The CRM drafts the email to the defined contact (${p.prospect.email}) with the signed proposal PDF attached. Edit, then confirm — nothing goes without your approval.`}>
          <button onClick={() => openMail("proposal")} className="px-4 py-2 rounded-lg text-white text-sm font-semibold" style={{ background: "var(--accent)" }}>✉️ Send to client</button>
        </ActionCard>
      )}

      {/* PROPOSAL SENT: client confirmation via signed-proposal upload */}
      {p.status === "proposal_sent" && iAmRequester && (
        <ActionCard title="Awaiting client confirmation" sub={`Sent ${p.proposalSentAt ? fmtDT(p.proposalSentAt) : ""}. Client confirmation is established by uploading the client-signed proposal — that upload converts the prospect into a client and auto-prepares the engagement letter.`}>
          <div className="flex gap-2 flex-wrap items-center">
            <FilePick label="Upload client-signed proposal — confirm client" onFiles={(fs) => actions.uploadSignedProposal(p.id, fs[0])} />
            {!lostMode ? (
              <button onClick={() => setLostMode(true)} className="px-4 py-2 rounded-lg border text-sm font-semibold" style={{ borderColor: "var(--red)", color: "var(--red)" }}>Mark as lost…</button>
            ) : (
              <>
                <input value={lostNote} onChange={(e) => setLostNote(e.target.value)} placeholder="Reason (kept in audit trail)" className="border rounded-md px-2.5 py-2 text-xs flex-1 min-w-[200px]" style={{ borderColor: "var(--line)" }} />
                <button onClick={() => actions.markLost(p.id, lostNote.trim())} className="px-3 py-2 rounded-md text-white text-xs font-semibold" style={{ background: "var(--red)" }}>Confirm lost</button>
              </>
            )}
          </div>
          {!unsignedMode ? (
            <button onClick={() => setUnsignedMode(true)} className="mt-3 text-[11px] underline" style={{ color: "var(--mut)" }}>
              Client confirmed without signing? Record the confirmation…
            </button>
          ) : (
            <div className="mt-3 pt-3 border-t space-y-2" style={{ borderColor: "var(--line)" }}>
              <div className="text-[11px] font-semibold" style={{ color: "var(--amber)" }}>
                Record a confirmation received without a signed proposal — same discipline as completing a duty
                without proof: a basis and a mandatory note, permanently logged. The signed engagement letter
                will serve as the binding client acceptance record.
              </div>
              <div className="flex gap-2 flex-wrap items-center">
                <select value={confBasis} onChange={(e) => setConfBasis(e.target.value)} className="border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }}>
                  <option value="email_approval">Approval received by email</option>
                  <option value="message_approval">Approval by message (WhatsApp/SMS)</option>
                  <option value="verbal_instruction">Verbal instruction to proceed</option>
                  <option value="advance_payment">Advance payment received</option>
                  <option value="other">Other (describe in the note)</option>
                </select>
                <FilePick small multiple label="Attach evidence — email PDF / screenshot (optional)" onFiles={(fs) => setConfFiles([...confFiles, ...fs])} />
                {confFiles.map((f, i) => (
                  <span key={i} className="text-xs font-mono2 px-2 py-1 rounded border" style={{ borderColor: "var(--line)" }}>
                    <FileLink {...f} /> <button onClick={() => setConfFiles(confFiles.filter((_, j) => j !== i))} style={{ color: "var(--red)" }}>×</button>
                  </span>
                ))}
              </div>
              <input value={confNote} onChange={(e) => setConfNote(e.target.value)} placeholder={'Mandatory note — exactly how did the client confirm? e.g. "Email from Mariam 09 Jul: please proceed on v2 terms"'} className="w-full border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
              <label className="flex items-start gap-2 text-[11px]" style={{ color: "var(--mut)" }}>
                <input type="checkbox" checked={confIdentity} onChange={(e) => setConfIdentity(e.target.checked)} className="mt-0.5" />
                I re-confirm my identity and take responsibility for recording this client confirmation (in production: password / 2FA re-entry).
              </label>
              <div className="flex gap-2 items-center">
                <button disabled={!confNote.trim() || !confIdentity} onClick={() => { actions.confirmUnsigned(p.id, { basis: confBasis, note: confNote.trim(), files: confFiles }); setUnsignedMode(false); }} className="px-3 py-2 rounded-md text-white text-xs font-semibold disabled:opacity-40" style={{ background: "var(--amber)" }}>
                  Record confirmation & convert — logged as unsigned
                </button>
                <button onClick={() => setUnsignedMode(false)} className="text-xs" style={{ color: "var(--mut)" }}>cancel</button>
              </div>
            </div>
          )}
        </ActionCard>
      )}

      {/* EL preview & stage actions */}
      {p.el && p.status !== "lost" && (
        <div className="grid grid-cols-3 gap-5">
          <div className="col-span-2">
            <div className="bg-white border rounded-xl overflow-hidden shadow-sm" style={{ borderColor: "var(--line)" }}>
              <div className="px-8 pt-7 pb-5 border-b-4" style={{ borderColor: firm.accent }}>
                <div className="font-disp text-xl font-bold" style={{ color: "var(--ink)" }}>{firm.name}</div>
                <div className="text-[11px] mt-1" style={{ color: "var(--mut)" }}>{firm.address} · {firm.phone} · {firm.email} · {firm.trn}</div>
              </div>
              <div className="px-8 py-6 text-sm leading-relaxed">
                <div className="flex justify-between text-xs font-mono2" style={{ color: "var(--mut)" }}><span>Engagement Letter · Ref {p.id}-EL</span><span>{fmtD(now())}</span></div>
                <h2 className="font-disp text-lg font-bold mt-4" style={{ color: "var(--ink)" }}>Engagement Letter</h2>
                <p className="mt-3">To: <b>{p.prospect.name}</b>{p.prospect.contactPerson && <> — Attn: {p.prospect.contactPerson}</>}</p>
                <p className="mt-2">We refer to our proposal (Ref {p.id}) and your confirmation thereof by counter-signature. This letter confirms the terms of our engagement to provide the following professional services:</p>
                <table className="w-full mt-3 text-sm">
                  <tbody>
                    {d.lines.map((l, i) => (
                      <tr key={i} className="border-b" style={{ borderColor: "var(--line)" }}>
                        <td className="py-1.5">{l.service}</td>
                        <td className="py-1.5 text-right font-mono2">{num(l.fee).toLocaleString()} <span className="text-[11px]" style={{ color: "var(--mut)" }}>{l.basis || defaultBasis(l.service)}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="mt-3 text-xs" style={{ color: "var(--mut)" }}>Commercial terms above are locked to the client-confirmed proposal and cannot be edited on this letter.</p>
                {p.el.advancePct === 0 ? (
                  <p className="mt-3"><b>Payment plan:</b> Fees are billed in accordance with the payment terms of the confirmed proposal: {d.paymentTerms}</p>
                ) : (
                  <p className="mt-3"><b>Payment plan:</b> {p.el.advancePct}% advance ({money((p.el.advancePct / 100) * firstBill)}) payable on signing; balance {money(firstBill - (p.el.advancePct / 100) * firstBill)} within 14 days — computed on the first billing period of {money(firstBill)} ({d.lines.map((l) => `${l.service.split(" (")[0]} ${num(l.fee).toLocaleString()}`).join(" + ")}). Thereafter, fees are billed per the payment terms of the confirmed proposal: <span className="text-xs" style={{ color: "var(--mut)" }}>{d.paymentTerms}</span></p>
                )}
                {p.el.note && <p className="mt-2"><b>Special terms:</b> {p.el.note}</p>}
                <p className="mt-6" style={{ color: "var(--mut)" }}>For and on behalf of {firm.name}</p>
                <div className="mt-3 min-h-[70px]">
                  {p.el.signature ? <SigBlock user={byId(p.el.signature.by)} at={p.el.signature.at} role="Senior Management" /> : <span className="text-xs italic" style={{ color: "var(--mut)" }}>— pending senior signature —</span>}
                </div>
                {p.clientSignedProposal && (
                  <div className="mt-5 pt-4 border-t text-xs font-mono2" style={{ borderColor: "var(--line)", color: "var(--accent)" }}>
                    ✓ Client-signed proposal on file: <FileLink {...p.clientSignedProposal} /> · uploaded {fmtDT(p.clientSignedProposal.at)}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="space-y-4">
            {p.status === "el_staffing" && iAmRequester && (
              <>
                <section className="bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
                  <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Assign technical staff — per activity</h3>
                  <p className="text-[11px] mt-0.5 mb-3" style={{ color: "var(--mut)" }}>Every service needs an owner before the EL can route for signature. Each candidate's current duties are listed so the choice is informed, not blind. Staff are notified on assignment.</p>
                  <div className="space-y-3">
                    {d.lines.map((l) => (
                      <div key={l.service} className="border rounded-lg p-3" style={{ borderColor: assignments[l.service] ? "var(--accent)" : "var(--line)", background: assignments[l.service] ? "var(--accent-soft)" : "#fff" }}>
                        <div className="text-sm font-semibold flex items-center justify-between">
                          <span>{l.service}</span>
                          {assignments[l.service] && <span className="text-xs font-medium" style={{ color: "var(--accent)" }}>→ {byId(assignments[l.service]).name}</span>}
                        </div>
                        <div className="mt-2 space-y-1.5">
                          {staff.map((u) => {
                            const w = workloadOf(u.id);
                            const sel = assignments[l.service] === u.id;
                            return (
                              <button key={u.id} onClick={() => actions.assignActivity(p.id, l.service, u.id)} className="w-full text-left border rounded-md p-2 bg-white hover:bg-gray-50" style={{ borderColor: sel ? "var(--accent)" : "var(--line)" }}>
                                <div className="text-xs font-semibold flex items-center justify-between">
                                  <span>{u.name} <span className="font-normal" style={{ color: "var(--mut)" }}>· {u.designation}</span></span>
                                  {sel && <span style={{ color: "var(--accent)" }}>✓ assigned</span>}
                                </div>
                                <div className="text-[10px] mt-1" style={{ color: "var(--mut)" }}>
                                  {w.proposals.length === 0 && w.activities.length === 0 ? "No current client work." : (
                                    <>
                                      {w.proposals.map((pp) => <div key={pp.id}>• {pp.id} {pp.prospect.name} — {STATUS_MAP[pp.status]?.[0] || pp.status}</div>)}
                                      {w.activities.map((a, i2) => <div key={i2}>• {a.client} — {a.service}{a.legacy ? " (pre-existing)" : ""}</div>)}
                                    </>
                                  )}
                                </div>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                </section>

                <section className="bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
                  <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Payment plan, terms & routing</h3>
                  <p className="text-[11px] mt-0.5 mb-3" style={{ color: "var(--mut)" }}>Fees are locked to the client-confirmed proposal. Set the plan, then route — the senior can only sign or reject with a note.</p>
                  <label className="text-xs font-semibold" style={{ color: "var(--mut)" }}>Advance on signing</label>
                  <select value={p.el.advancePct} onChange={(e) => actions.setELAdvance(p.id, Number(e.target.value))} className="mt-1 w-full border rounded-md px-2 py-2 text-sm" style={{ borderColor: "var(--line)" }}>
                    <option value={0}>No advance — bill per the proposal payment terms (default)</option>
                    {[25, 50, 75, 100].map((x) => <option key={x} value={x}>{x}% advance{x < 100 ? " · balance in 14 days" : " (full advance)"}</option>)}
                  </select>
                  {p.el.advancePct > 0 && (
                    <div className="mt-1.5 text-[11px] px-2.5 py-2 rounded-md" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>
                      ⚠️ This adds an advance the client did not sign in the proposal — computed on the first billing period: <b>{money(firstBill)}</b> ({d.lines.map((l) => `${l.service.split(" (")[0]} ${num(l.fee).toLocaleString()}`).join(" + ")}). Confirm the client has agreed, or use special terms to record the discussion.
                    </div>
                  )}
                  <label className="text-xs font-semibold mt-3 block" style={{ color: "var(--mut)" }}>Special terms (optional — recorded in the audit trail on change)</label>
                  <textarea value={elNote} onChange={(e) => setElNote(e.target.value)} onBlur={() => actions.setELNote(p.id, elNote)} rows={3} className="mt-1 w-full border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} placeholder="e.g. Engagement commences on receipt of advance; either party may terminate with 30 days' notice…" />
                  <label className="text-xs font-semibold mt-3 block" style={{ color: "var(--mut)" }}>Route for signature to</label>
                  <select value={signatoryPick} onChange={(e) => setSignatoryPick(e.target.value)} className="mt-1 w-full border rounded-md px-2 py-2 text-sm" style={{ borderColor: "var(--line)" }}>
                    <option value="">Select senior signatory…</option>
                    {signatories.map((u) => <option key={u.id} value={u.id}>{u.name} — {u.designation}</option>)}
                  </select>
                  {unassigned.length > 0 && (
                    <div className="mt-2 text-[11px] px-2.5 py-2 rounded-md font-medium" style={{ background: "var(--red-soft)", color: "var(--red)" }}>
                      🔒 Cannot route yet — {unassigned.length} activit{unassigned.length === 1 ? "y" : "ies"} unassigned: {unassigned.map((l) => l.service).join(", ")}
                    </div>
                  )}
                  <button disabled={!signatoryPick || unassigned.length > 0} onClick={() => actions.routeEL(p.id, signatoryPick)} className="mt-3 w-full px-3 py-2 rounded-md text-white text-sm font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>
                    Route to senior management ✍️
                  </button>
                </section>
              </>
            )}

            {p.el && Object.keys(assignments).length > 0 && p.status !== "el_staffing" && (
              <section className="bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
                <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Assigned team</h3>
                <div className="mt-2 space-y-1 text-xs">
                  {Object.entries(assignments).map(([svc, uId]) => <div key={svc} className="flex justify-between"><span style={{ color: "var(--mut)" }}>{svc}</span><b>{byId(uId).name}</b></div>)}
                </div>
              </section>
            )}

            {iAmELSenior && (
              <section className="bg-white border rounded-xl p-4" style={{ borderColor: "#D8CBEF", background: "#F8F5FE" }}>
                <h3 className="font-disp font-bold text-sm" style={{ color: "#5C4A8A" }}>Sign engagement letter</h3>
                <p className="text-[11px] mt-0.5 mb-3" style={{ color: "var(--mut)" }}>Sign and return to the manager, or reject with a note. No edits at this stage.</p>
                <label className="flex items-start gap-2 text-[11px]" style={{ color: "var(--mut)" }}>
                  <input type="checkbox" checked={confirmSig} onChange={(e) => setConfirmSig(e.target.checked)} className="mt-0.5" />
                  I re-confirm my identity and authorize applying <b>my own</b> signature (in production: password / 2FA re-entry).
                </label>
                <button disabled={!confirmSig} onClick={() => actions.elApprove(p.id)} className="mt-2 w-full px-3 py-2 rounded-md text-white text-sm font-semibold disabled:opacity-40" style={{ background: "#5C4A8A" }}>Sign & return to manager ✍️</button>
                {!rejMode ? (
                  <button onClick={() => setRejMode(true)} className="mt-2 w-full px-3 py-2 rounded-md border text-sm font-semibold bg-white" style={{ borderColor: "var(--red)", color: "var(--red)" }}>Reject with note…</button>
                ) : (
                  <div className="mt-2 flex flex-col gap-2">
                    <input value={rejNote} onChange={(e) => setRejNote(e.target.value)} placeholder="Mandatory note — sent back to the manager" className="border rounded-md px-2.5 py-2 text-xs bg-white" style={{ borderColor: "var(--line)" }} />
                    <button onClick={() => { if (rejNote.trim()) actions.elReject(p.id, rejNote.trim()); }} className="px-3 py-2 rounded-md text-white text-xs font-semibold" style={{ background: "var(--red)" }}>Confirm rejection</button>
                  </div>
                )}
              </section>
            )}

            {p.status === "el_approved" && iAmRequester && (
              <ActionCard title="Send the signed engagement letter" sub={`Draft goes to ${p.prospect.email} with the signed EL PDF attached — check, edit if needed, and approve to send. This completes Proposal & Engagement.`}>
                <button onClick={() => openMail("el")} className="px-4 py-2 rounded-lg text-white text-sm font-semibold" style={{ background: "var(--accent)" }}>✉️ Send to client</button>
              </ActionCard>
            )}

            {p.status === "el_sent" && client && (
              <section className="bg-white border rounded-xl p-4" style={{ borderColor: "var(--accent)", background: "var(--accent-soft)" }}>
                <h3 className="font-disp font-bold text-sm" style={{ color: "var(--accent)" }}>🎉 Proposal & Engagement complete — client {client.code}</h3>
                <div className="text-xs mt-2 space-y-1" style={{ color: "var(--ink)" }}>
                  <div>✓ Client-signed proposal on file</div>
                  <div>✓ Engagement letter signed & sent {p.el.sentAt && fmtDT(p.el.sentAt)}</div>
                  <div>✓ Team assigned per activity — staff notified their duties are live</div>
                  <div>✓ Payment schedule with the accountant — daily reminders until each receipt is updated</div>
                </div>
                <p className="text-[11px] mt-3" style={{ color: "var(--mut)" }}>This trail is sealed and the performance report is available to management. Client documentation proceeds in Onboarding — each staffed activity now runs its own documentation relay from the dashboard.</p>
              </section>
            )}
          </div>
        </div>
      )}

      {mail && (
        <EmailModal mail={mail} setMail={setMail} onSend={() => { actions.sendClientEmail(p.id, mail.kind, mail); setMail(null); }} />
      )}
    </div>
  );
}

const ActionCard = ({ title, sub, children }) => (
  <section className="bg-white border rounded-xl p-5" style={{ borderColor: "var(--line)" }}>
    <h3 className="font-disp font-bold" style={{ color: "var(--ink)" }}>{title}</h3>
    <p className="text-xs mt-0.5 mb-3" style={{ color: "var(--mut)" }}>{sub}</p>
    {children}
  </section>
);

function EmailModal({ mail, setMail, onSend }) {
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-40 p-4">
      <div className="bg-white rounded-xl w-full max-w-xl p-5 shadow-2xl">
        <h3 className="font-disp font-bold" style={{ color: "var(--ink)" }}>Draft email — edit · confirm · send</h3>
        <p className="text-[11px] mt-0.5 mb-3" style={{ color: "var(--mut)" }}>Prepared by the CRM. Nothing is sent without your confirmation. The send is logged in the audit trail.</p>
        <label className="text-xs font-semibold" style={{ color: "var(--mut)" }}>To</label>
        <input value={mail.to} onChange={(e) => setMail({ ...mail, to: e.target.value })} className="mt-1 w-full border rounded-md px-3 py-2 text-sm font-mono2" style={{ borderColor: "var(--line)" }} />
        <label className="text-xs font-semibold mt-3 block" style={{ color: "var(--mut)" }}>Subject</label>
        <input value={mail.subject} onChange={(e) => setMail({ ...mail, subject: e.target.value })} className="mt-1 w-full border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
        <label className="text-xs font-semibold mt-3 block" style={{ color: "var(--mut)" }}>Body <span className="font-normal">(attachment: {mail.kind === "proposal" ? "signed proposal PDF" : "signed engagement letter PDF"} — added automatically)</span></label>
        <textarea value={mail.body} onChange={(e) => setMail({ ...mail, body: e.target.value })} rows={9} className="mt-1 w-full border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
        <div className="flex justify-end gap-2 mt-4">
          <button onClick={() => setMail(null)} className="px-4 py-2 rounded-md border text-sm font-medium" style={{ borderColor: "var(--line)" }}>Cancel</button>
          <button onClick={onSend} className="px-4 py-2 rounded-md text-white text-sm font-semibold" style={{ background: "var(--accent)" }}>Confirm & send ✉️</button>
        </div>
      </div>
    </div>
  );
}

function CompareModal({ a, b, byId, onClose }) {
  // ensure a is the earlier version
  const [va, vb] = a.v <= b.v ? [a, b] : [b, a];
  const services = [...new Set([...va.data.lines.map((l) => l.service), ...vb.data.lines.map((l) => l.service)])];
  const changes = diffDrafts(va.data, vb.data);
  const cell = (val, changed) => (
    <td className="py-2 px-3 font-mono2 text-right" style={changed ? { background: "var(--amber-soft)", color: "var(--amber)", fontWeight: 700 } : {}}>{val}</td>
  );
  const feeOf = (v, svc) => { const l = v.data.lines.find((x) => x.service === svc); return l ? `${num(l.fee).toLocaleString()} ${l.basis || defaultBasis(svc)}` : "— not included —"; };
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-40 p-4" onClick={onClose}>
      <div className="bg-white rounded-xl w-full max-w-3xl p-6 shadow-2xl max-h-[85vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between">
          <div>
            <h3 className="font-disp font-bold text-lg" style={{ color: "var(--ink)" }}>Comparison report — v{va.v} vs v{vb.v}</h3>
            <div className="text-xs mt-1" style={{ color: "var(--mut)" }}>
              v{va.v}: {va.note} — {byId(va.by).name}, {fmtDT(va.at)} &nbsp;·&nbsp; v{vb.v}: {vb.note} — {byId(vb.by).name}, {fmtDT(vb.at)}
            </div>
          </div>
          <button onClick={onClose} className="px-3 py-1.5 rounded-md border text-sm font-medium" style={{ borderColor: "var(--line)" }}>Close</button>
        </div>

        <table className="w-full mt-4 text-sm border rounded-lg overflow-hidden" style={{ borderColor: "var(--line)" }}>
          <thead>
            <tr className="text-left text-[11px] uppercase tracking-wider" style={{ background: "var(--paper)", color: "var(--mut)" }}>
              <th className="py-2 px-3">Field</th><th className="py-2 px-3 text-right">v{va.v}</th><th className="py-2 px-3 text-right">v{vb.v}</th>
            </tr>
          </thead>
          <tbody>
            {services.map((svc) => {
              const changed = feeOf(va, svc) !== feeOf(vb, svc);
              return (
                <tr key={svc} className="border-t" style={{ borderColor: "var(--line)" }}>
                  <td className="py-2 px-3">{svc}</td>
                  {cell(feeOf(va, svc), changed)}{cell(feeOf(vb, svc), changed)}
                </tr>
              );
            })}
            <tr className="border-t" style={{ borderColor: "var(--line)" }}>
              <td className="py-2 px-3">Payment terms</td>
              <td className="py-2 px-3 text-xs" style={(va.data.paymentTerms || "").trim() !== (vb.data.paymentTerms || "").trim() ? { background: "var(--amber-soft)" } : {}}>{va.data.paymentTerms || "—"}</td>
              <td className="py-2 px-3 text-xs" style={(va.data.paymentTerms || "").trim() !== (vb.data.paymentTerms || "").trim() ? { background: "var(--amber-soft)", fontWeight: 600 } : {}}>{vb.data.paymentTerms || "—"}</td>
            </tr>
            <tr className="border-t" style={{ borderColor: "var(--line)" }}>
              <td className="py-2 px-3">Validity</td>
              {cell(`${va.data.validityDays} days`, String(va.data.validityDays) !== String(vb.data.validityDays))}
              {cell(`${vb.data.validityDays} days`, String(va.data.validityDays) !== String(vb.data.validityDays))}
            </tr>
            <tr className="border-t" style={{ borderColor: "var(--line)" }}>
              <td className="py-2 px-3">Scope notes</td>
              <td className="py-2 px-3 text-xs" style={(va.data.scope || "").trim() !== (vb.data.scope || "").trim() ? { background: "var(--amber-soft)" } : {}}>{va.data.scope || "—"}</td>
              <td className="py-2 px-3 text-xs" style={(va.data.scope || "").trim() !== (vb.data.scope || "").trim() ? { background: "var(--amber-soft)", fontWeight: 600 } : {}}>{vb.data.scope || "—"}</td>
            </tr>
            <tr className="border-t font-bold" style={{ borderColor: "var(--line)", background: "var(--paper)" }}>
              <td className="py-2 px-3">Σ line fees (mixed basis)</td>
              {cell(`AED ${va.data.lines.reduce((x, l) => x + num(l.fee), 0).toLocaleString()}`, va.data.lines.reduce((x, l) => x + num(l.fee), 0) !== vb.data.lines.reduce((x, l) => x + num(l.fee), 0))}
              {cell(`AED ${vb.data.lines.reduce((x, l) => x + num(l.fee), 0).toLocaleString()}`, va.data.lines.reduce((x, l) => x + num(l.fee), 0) !== vb.data.lines.reduce((x, l) => x + num(l.fee), 0))}
            </tr>
          </tbody>
        </table>

        <div className="mt-4 rounded-lg border p-3" style={{ borderColor: "var(--line)", background: "var(--paper)" }}>
          <div className="text-[11px] uppercase tracking-wider font-bold" style={{ color: "var(--mut)" }}>Summary of changes ({changes.length})</div>
          {changes.length === 0 ? (
            <div className="text-sm mt-1.5" style={{ color: "var(--mut)" }}>No commercial differences between these versions.</div>
          ) : (
            <ul className="mt-1.5 space-y-1 text-sm">
              {changes.map((c, i) => <li key={i} className="flex gap-2"><span style={{ color: "var(--amber)" }}>●</span><span>{c}</span></li>)}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------- performance report (on module completion) ---------- */

function starsFor(avgDays) {
  if (avgDays <= 0.5) return 5;
  if (avgDays <= 1) return 4.5;
  if (avgDays <= 2) return 4;
  if (avgDays <= 3) return 3.5;
  if (avgDays <= 5) return 3;
  if (avgDays <= 7) return 2;
  return 1;
}
const Stars = ({ n }) => (
  <span className="font-mono2 tracking-tight" style={{ color: "var(--amber)" }} title={`${n} / 5`}>
    {"★".repeat(Math.floor(n))}{n % 1 ? "½" : ""}{"☆".repeat(5 - Math.ceil(n))}
    <span className="ml-1.5 text-[11px]" style={{ color: "var(--mut)" }}>{n.toFixed(1)}</span>
  </span>
);

/* ---------- Onboarding module: per-activity documentation relay ---------- */

const QUAL_STYLE = {
  audited: { background: "var(--accent-soft)", color: "var(--accent)" },
  unaudited: { background: "var(--amber-soft)", color: "var(--amber)" },
  draft: { background: "var(--amber-soft)", color: "var(--amber)" },
  copy: { background: "var(--paper)", color: "var(--mut)", border: "1px solid var(--line)" },
};
const OB_CHIP = {
  requested: ["Requested", "var(--amber)"],
  provided: ["Provided", "var(--accent)"],
  answered: ["Answered", "var(--accent)"],
  not_available: ["Not available", "var(--red)"],
  withdrawn: ["Withdrawn", "var(--mut)"],
};

function ClientDocuments({ clientId }) {
  const [r, setR] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    api.get(`/clients/${clientId}/documents`).then(setR).catch((e) => setErr(e.message));
  }, [clientId]);
  if (err) return <div className="text-xs" style={{ color: "var(--mut)" }}>{err}</div>;
  if (!r) return <div className="text-xs" style={{ color: "var(--mut)" }}>Loading documents…</div>;
  return (
    <div className="rounded-lg border p-3" style={{ borderColor: "var(--line)", background: "var(--paper)" }}>
      <div className="flex items-center gap-2 mb-1.5">
        <div className="text-[10px] uppercase tracking-wider font-bold" style={{ color: "var(--mut)" }}>Documents on file ({r.documents.length})</div>
        {r.unaudited_on_file && <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>unaudited financials on file</span>}
      </div>
      {r.documents.length === 0 && <div className="text-xs" style={{ color: "var(--mut)" }}>Nothing on file yet.</div>}
      {r.documents.map((d, i) => (
        <div key={i} className="bg-white border rounded-md px-3 py-2 mb-1.5 flex items-center gap-3 text-xs flex-wrap" style={{ borderColor: "var(--line)" }}>
          <span className="flex-1 min-w-[160px]"><FileLink name={d.name} url={`api://file/${d.file_id}`} size={d.size} /></span>
          <span style={{ color: "var(--mut)" }}>{d.source}</span>
          <span style={{ color: "var(--mut)" }}>{d.uploaded_by}{d.at ? ` · ${fmtD(new Date(d.at).getTime())}` : ""}</span>
          {d.qualifier && <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold" style={QUAL_STYLE[d.qualifier] || {}}>{d.qualifier}</span>}
        </div>
      ))}
    </div>
  );
}

function CopyBtn({ text, label }) {
  const [ok, setOk] = useState(false);
  return (
    <button
      onClick={async () => { try { await navigator.clipboard.writeText(text); setOk(true); setTimeout(() => setOk(false), 1200); } catch { /* clipboard unavailable */ } }}
      className="text-[10px] px-1.5 py-0.5 rounded border font-sans"
      style={{ borderColor: "var(--line)", color: ok ? "var(--accent)" : "var(--mut)" }}
    >
      {ok ? "✓ copied" : `copy ${label}`}
    </button>
  );
}

function CredentialCard({ it, ob, byId, run, oid, revealed, setRevealed }) {
  const rev = revealed[it.id]; // structured: full credential payload; legacy: { value }
  const providerName = byId(ob.manager_id).name;
  const doReveal = () => {
    if (!window.confirm(`Reveal this credential?\n\nViewing is logged on the trail and ${providerName} (who provided it) is notified.`)) return;
    run(async () => {
      const r = await api.get(`/onboardings/${oid}/items/${it.id}/reveal`);
      setRevealed((m) => ({ ...m, [it.id]: r.credential || { value: r.value } }));
    });
  };
  const hide = () => setRevealed((m) => { const n = { ...m }; delete n[it.id]; return n; });

  if (it.credential_legacy) {
    return (
      <div className="text-xs mt-1.5 font-mono2 flex items-center gap-2 flex-wrap">
        <span className="text-[9px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded font-sans" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>legacy note</span>
        ↳ {rev ? rev.value : it.answer_text}
        {!rev
          ? <button onClick={doReveal} className="underline font-sans" style={{ color: "var(--amber)" }}>Reveal (logged)</button>
          : <button onClick={hide} className="underline font-sans" style={{ color: "var(--mut)" }}>hide again</button>}
      </div>
    );
  }
  const c = it.credential;
  if (!c) return null;
  return (
    <div className="mt-2 rounded-lg border p-2.5 text-xs" style={{ borderColor: "var(--line)", background: "var(--paper)" }}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[9px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>🔐 portal</span>
        <span className="font-medium">{c.portal_label || it.label}</span>
      </div>
      <div className="mt-1.5 space-y-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="w-8" style={{ color: "var(--mut)" }}>user</span>
          <span className="font-mono2">{c.username}</span>
          {rev && <CopyBtn text={rev.username} label="username" />}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="w-8" style={{ color: "var(--mut)" }}>pass</span>
          <span className="font-mono2">{rev ? rev.password : "••••••"}</span>
          {!rev ? (
            <button onClick={doReveal} className="underline" style={{ color: "var(--amber)" }}>Reveal (logged)</button>
          ) : (
            <>
              <CopyBtn text={rev.password} label="password" />
              <button onClick={hide} className="underline" style={{ color: "var(--mut)" }}>hide again</button>
            </>
          )}
        </div>
        {c.extra_note && <div style={{ color: "var(--mut)" }}>note: {c.extra_note}</div>}
      </div>
    </div>
  );
}

function ObItem({ it, ob, me, byId, run, oid, revealed, setRevealed }) {
  const iAmStaff = me.id === ob.staff_id;
  const iAmManager = me.id === ob.manager_id;
  const managerTurn = iAmManager && ob.holder === me.id && it.status === "requested" && ob.status === "in_progress";
  const [val, setVal] = useState("");
  const [qual, setQual] = useState("");
  const [cred, setCred] = useState({ portal: "", user: "", pass: "", note: "" });
  const [showPw, setShowPw] = useState(false);
  const [files, setFiles] = useState([]);
  const [naMode, setNaMode] = useState(false);
  const [naReason, setNaReason] = useState("");
  const [rrMode, setRrMode] = useState(false);
  const [rrReason, setRrReason] = useState("");
  const [wdMode, setWdMode] = useState(false);
  const [wdReason, setWdReason] = useState("");
  const chip = OB_CHIP[it.status] || [it.status, "var(--mut)"];
  // requested but the baton hasn't passed — the manager can't see it and got no notice yet
  const unsent = it.status === "requested" && ob.status === "in_progress" && ob.holder === ob.staff_id;

  const provide = () => run(async () => {
    const fd = new FormData();
    if (it.kind === "credential") {
      fd.append("portal_label", cred.portal.trim());
      fd.append("username", cred.user.trim());
      fd.append("password", cred.pass);
      fd.append("extra_note", cred.note.trim());
    } else if (val.trim()) fd.append("answer_text", val.trim());
    if (qual) fd.append("qualifier", qual);
    for (const f of files) {
      const raw = rawFromUrl(f.url);
      if (raw) fd.append("evidence", raw, f.name);
    }
    await api.postForm(`/onboardings/${oid}/items/${it.id}/provide`, fd);
  });

  return (
    <div className="border rounded-lg p-3" style={{ borderColor: "var(--line)" }}>
      <div className="flex items-center gap-2 text-sm flex-wrap">
        <span className="text-[9px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>{it.kind}</span>
        <span className="flex-1 font-medium">{it.label}</span>
        {it.accepted_at && <span className="text-[10px] font-bold" style={{ color: "var(--accent)" }}>✓ accepted</span>}
        {unsent && <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>not sent yet</span>}
        <span className="text-[11px] px-2 py-0.5 rounded-full font-medium" style={{ background: chip[1] + "18", color: chip[1] }}>{chip[0]}</span>
      </div>
      {it.note && <div className="text-xs mt-1" style={{ color: "var(--mut)" }}>Note: {it.note}</div>}
      {it.reason && <div className="text-xs mt-1" style={{ color: "var(--red)" }}>Reason: {it.reason}</div>}
      {it.files.length > 0 && (
        <div className="text-xs mt-1.5 flex gap-2 flex-wrap items-center">
          {it.files.map((f, i) => <FileLink key={i} name={f.name} url={`api://file/${f.file_id}`} size={f.size} />)}
          {it.qualifier && <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold" style={QUAL_STYLE[it.qualifier] || {}}>{it.qualifier}</span>}
        </div>
      )}
      {it.kind !== "credential" && it.answer_text && <div className="text-xs mt-1.5 font-mono2">↳ {it.answer_text}</div>}
      {it.kind === "credential" && (it.credential || it.answer_text) && (
        <CredentialCard it={it} ob={ob} byId={byId} run={run} oid={oid} revealed={revealed} setRevealed={setRevealed} />
      )}

      {managerTurn && (
        <div className="mt-2.5 space-y-1.5">
          {!naMode ? (
            <div className="flex gap-2 items-center flex-wrap">
              {it.kind === "document" ? (
                <>
                  <FilePick small multiple label="Upload document(s)" onFiles={(fs) => setFiles([...files, ...fs])} />
                  {files.map((f, i) => <span key={i} className="text-xs font-mono2 px-2 py-1 rounded border" style={{ borderColor: "var(--line)" }}>{f.name} <button onClick={() => setFiles(files.filter((_, j) => j !== i))} style={{ color: "var(--red)" }}>×</button></span>)}
                  <select value={qual} onChange={(e) => setQual(e.target.value)} className="border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }}>
                    <option value="">No qualifier</option><option value="audited">Audited</option><option value="unaudited">Unaudited</option><option value="draft">Draft</option><option value="copy">Copy</option>
                  </select>
                  <button disabled={files.length === 0} onClick={provide} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>Provide</button>
                </>
              ) : it.kind === "credential" ? (
                <>
                  <input value={cred.portal} onChange={(e) => setCred({ ...cred, portal: e.target.value })} placeholder={'Portal / label — e.g. "EmaraTax — FTA portal"'} className="flex-1 min-w-[200px] border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
                  <input value={cred.user} onChange={(e) => setCred({ ...cred, user: e.target.value })} placeholder="User ID" autoComplete="off" className="min-w-[140px] border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
                  <span className="relative inline-flex min-w-[150px]">
                    <input value={cred.pass} onChange={(e) => setCred({ ...cred, pass: e.target.value })} type={showPw ? "text" : "password"} placeholder="Password" autoComplete="new-password" className="w-full border rounded-md px-2.5 py-1.5 pr-8 text-xs" style={{ borderColor: "var(--line)" }} />
                    <button type="button" onClick={() => setShowPw((s) => !s)} title={showPw ? "Hide password" : "Show password"} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-sm leading-none">{showPw ? "🙈" : "👁️"}</button>
                  </span>
                  <input value={cred.note} onChange={(e) => setCred({ ...cred, note: e.target.value })} placeholder="Optional note — TRN, security question…" className="flex-1 min-w-[200px] border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
                  <button disabled={!cred.user.trim() || !cred.pass.trim()} onClick={provide} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>Provide credential</button>
                </>
              ) : (
                <>
                  <input value={val} onChange={(e) => setVal(e.target.value)} placeholder="Type the information" className="flex-1 min-w-[220px] border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
                  <button disabled={!val.trim()} onClick={provide} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>Answer</button>
                </>
              )}
              <button onClick={() => setNaMode(true)} className="px-2.5 py-1.5 rounded-md border text-xs" style={{ borderColor: "var(--line)", color: "var(--mut)" }}>Not available</button>
            </div>
          ) : (
            <div className="flex gap-2">
              <input value={naReason} onChange={(e) => setNaReason(e.target.value)} placeholder="Mandatory reason — why can't this be obtained?" className="flex-1 border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
              <button disabled={!naReason.trim()} onClick={() => { run(() => api.post(`/onboardings/${oid}/items/${it.id}/not-available`, { reason: naReason.trim() })); setNaMode(false); }} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold disabled:opacity-40" style={{ background: "var(--amber)" }}>Mark not available</button>
              <button onClick={() => setNaMode(false)} className="text-xs" style={{ color: "var(--mut)" }}>cancel</button>
            </div>
          )}
        </div>
      )}

      {iAmStaff && ob.status === "in_progress" && ["provided", "answered", "not_available"].includes(it.status) && (
        <div className="mt-2 flex gap-3 items-center flex-wrap text-xs">
          {!it.accepted_at && <button onClick={() => run(() => api.post(`/onboardings/${oid}/items/${it.id}/accept`))} className="underline font-medium" style={{ color: "var(--accent)" }}>Accept</button>}
          {!rrMode ? (
            <button onClick={() => setRrMode(true)} className="underline" style={{ color: "var(--amber)" }}>Re-request…</button>
          ) : (
            <span className="flex gap-2 flex-1 min-w-[240px]">
              <input value={rrReason} onChange={(e) => setRrReason(e.target.value)} placeholder="Why is this insufficient?" className="flex-1 border rounded-md px-2 py-1 text-xs" style={{ borderColor: "var(--line)" }} />
              <button disabled={!rrReason.trim()} onClick={() => { run(() => api.post(`/onboardings/${oid}/items/${it.id}/re-request`, { reason: rrReason.trim() })); setRrMode(false); }} className="px-2.5 py-1 rounded-md text-white font-semibold disabled:opacity-40" style={{ background: "var(--amber)" }}>Re-request</button>
            </span>
          )}
        </div>
      )}
      {iAmStaff && ob.status === "in_progress" && ["requested", "provided", "answered", "not_available"].includes(it.status) && (
        <div className="mt-1.5">
          {!wdMode ? (
            <button onClick={() => setWdMode(true)} className="text-[11px] underline" style={{ color: "var(--mut)" }}>Withdraw this request (no longer needed)</button>
          ) : (
            <div className="flex gap-2">
              <input value={wdReason} onChange={(e) => setWdReason(e.target.value)} placeholder="Mandatory reason" className="flex-1 border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
              <button disabled={!wdReason.trim()} onClick={() => { run(() => api.post(`/onboardings/${oid}/items/${it.id}/withdraw`, { reason: wdReason.trim() })); setWdMode(false); }} className="px-3 py-1.5 rounded-md text-white text-xs font-semibold disabled:opacity-40" style={{ background: "var(--mut)" }}>Withdraw</button>
              <button onClick={() => setWdMode(false)} className="text-xs" style={{ color: "var(--mut)" }}>cancel</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function OnboardingView({ oid, me, byId, back }) {
  const { pushToast, refetchAll } = useData();
  const [ob, setOb] = useState(null);
  const [err, setErr] = useState(null);
  const [docs, setDocs] = useState(null);
  const [newLabel, setNewLabel] = useState("");
  const [newKind, setNewKind] = useState("document");
  const [trailOpen, setTrailOpen] = useState(false);
  const [completeMode, setCompleteMode] = useState(false);
  const [cadence, setCadence] = useState("monthly");
  const [firstDue, setFirstDue] = useState("");
  const [cName, setCName] = useState("");
  const [cEmail, setCEmail] = useState("");
  const [revealed, setRevealed] = useState({});

  const load = async () => {
    try {
      const o = await api.get(`/onboardings/${oid}`);
      setOb(o);
      setCName((v) => v || o.client_contact?.contactPerson || o.client_contact?.name || "");
      setCEmail((v) => v || o.client_contact?.email || "");
      api.get(`/clients/${o.client_id}/documents`).then(setDocs).catch(() => setDocs({ documents: [], unaudited_on_file: false }));
    } catch (e) {
      setErr(e.message);
    }
  };
  useEffect(() => { load(); }, [oid]); // eslint-disable-line react-hooks/exhaustive-deps
  const run = async (fn) => {
    try {
      await fn();
      await load();
      refetchAll();
    } catch (e) {
      pushToast(`⚠️ ${e.message}`);
    }
  };

  if (err) return <div className="max-w-4xl mx-auto text-sm" style={{ color: "var(--mut)" }}>{err}</div>;
  if (!ob) return <div className="max-w-4xl mx-auto text-sm" style={{ color: "var(--mut)" }}>Loading onboarding…</div>;

  const iAmStaff = me.id === ob.staff_id;
  const iHold = ob.holder === me.id;
  const openItems = (ob.items || []).filter((i) => i.status === "requested");
  const proposalDocs = (docs?.documents || []).filter((d) => d.source.startsWith("Proposal & Engagement"));

  return (
    <div className="max-w-4xl mx-auto">
      <button onClick={back} className="text-xs font-medium mb-3" style={{ color: "var(--mut)" }}>← Back to dashboard</button>
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>
            {ob.client_name} <span className="font-mono2 text-base" style={{ color: "var(--accent)" }}>· {ob.client_ref}</span>
          </h1>
          <div className="text-sm mt-1 flex items-center gap-2 flex-wrap" style={{ color: "var(--mut)" }}>
            <span className="font-medium" style={{ color: "var(--ink)" }}>Onboarding — {ob.service}</span>
            {ob.status === "complete"
              ? <span className="text-[11px] px-2 py-0.5 rounded-full font-medium" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>Complete ✓</span>
              : <span className="text-[11px] px-2 py-0.5 rounded-full font-medium" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>In progress</span>}
            {docs?.unaudited_on_file && <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>unaudited financials on file</span>}
            <span>staff <b style={{ color: "var(--ink)" }}>{ob.staff_name}</b></span>·
            <span>manager <b style={{ color: "var(--ink)" }}>{byId(ob.manager_id).name}</b></span>
            {ob.status === "in_progress" && ob.holder && (
              <span>· baton with <b style={{ color: "var(--ink)" }}>{byId(ob.holder).name}</b> for <span className="font-mono2">{fmtDur(Date.now() - new Date(ob.holder_since).getTime())}</span></span>
            )}
          </div>
        </div>
      </div>

      <section className="mt-4 bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
        <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Already on file from Proposal & Engagement ({proposalDocs.length})</h3>
        <div className="mt-2 space-y-1.5">
          {proposalDocs.map((d, i) => (
            <div key={i} className="text-xs font-mono2 flex items-center gap-2 px-2.5 py-1.5 rounded-md border" style={{ borderColor: "var(--line)" }}>
              <span className="flex-1 truncate"><FileLink name={d.name} url={`api://file/${d.file_id}`} size={d.size} /></span>
              <span className="shrink-0" style={{ color: "var(--mut)" }}>{d.uploaded_by}{d.at ? ` · ${fmtDT(new Date(d.at).getTime())}` : ""}</span>
            </div>
          ))}
          {proposalDocs.length === 0 && <div className="text-xs" style={{ color: "var(--mut)" }}>Nothing carried over.</div>}
        </div>
      </section>

      <section className="mt-4 bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
        <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Documentation checklist</h3>
        <p className="text-xs mt-0.5" style={{ color: "var(--mut)" }}>The same relay discipline as the proposal: requests pass the baton to the manager; when every open item is resolved it returns automatically. Every step is on the trail.</p>
        <div className="mt-3 space-y-2">
          {(ob.items || []).map((it) => (
            <ObItem key={it.id} it={it} ob={ob} me={me} byId={byId} run={run} oid={oid} revealed={revealed} setRevealed={setRevealed} />
          ))}
          {(ob.items || []).length === 0 && <div className="text-xs py-2" style={{ color: "var(--mut)" }}>No items requested yet{iAmStaff ? " — build your first request round below." : "."}</div>}
        </div>

        {iAmStaff && iHold && ob.status === "in_progress" && (
          <div className="mt-4 pt-4 border-t" style={{ borderColor: "var(--line)" }}>
            <div className="text-xs font-semibold mb-2" style={{ color: "var(--mut)" }}>Request from {byId(ob.manager_id).name}</div>
            <div className="flex gap-2">
              <select value={newKind} onChange={(e) => setNewKind(e.target.value)} className="border rounded-md px-2 py-2 text-sm" style={{ borderColor: "var(--line)" }}>
                <option value="document">Document</option><option value="information">Information</option><option value="credential">Credential</option>
              </select>
              <input value={newLabel} onChange={(e) => setNewLabel(e.target.value)} placeholder={newKind === "credential" ? "e.g. FTA portal login" : newKind === "document" ? "e.g. Trade license copy / FY2025 financials" : "e.g. Confirm VAT registration date"} className="flex-1 border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
              <button disabled={!newLabel.trim()} onClick={() => { run(() => api.post(`/onboardings/${oid}/items`, { items: [{ label: newLabel.trim(), kind: newKind }] })); setNewLabel(""); }} className="px-3 py-2 rounded-md border text-sm font-medium disabled:opacity-40" style={{ borderColor: "var(--line)" }}>Add</button>
            </div>
            {openItems.length > 0 && (
              <>
                <div className="mt-3 rounded-lg border px-3 py-2 text-xs font-medium" style={{ background: "var(--amber-soft)", borderColor: "#E4C99A", color: "#6B5A38" }}>
                  ⚠️ {openItems.length} request{openItems.length !== 1 && "s"} drafted but NOT sent — {byId(ob.manager_id).name} sees nothing and gets no notice until you press Send.
                </div>
                <button onClick={() => run(() => api.post(`/onboardings/${oid}/send-requests`))} className="mt-2 px-4 py-2 rounded-lg text-white text-sm font-semibold" style={{ background: "var(--amber)" }}>
                  Send {openItems.length} request{openItems.length !== 1 && "s"} to {byId(ob.manager_id).name} — baton passes to them
                </button>
              </>
            )}
          </div>
        )}

        {iAmStaff && ob.status === "in_progress" && openItems.length === 0 && (ob.items || []).length > 0 && (
          <div className="mt-4 pt-4 border-t" style={{ borderColor: "var(--line)" }}>
            {!completeMode ? (
              <button onClick={() => setCompleteMode(true)} className="px-4 py-2 rounded-lg text-white text-sm font-semibold" style={{ background: "var(--accent)" }}>
                Documentation complete — start recurring work
              </button>
            ) : (
              <div className="space-y-2">
                <div className="text-xs font-semibold" style={{ color: "var(--mut)" }}>Create the recurring duty — deadlines are computed automatically from the first statutory due date. One-off services use cadence "one-time".</div>
                <div className="flex gap-2 flex-wrap items-center">
                  <select value={cadence} onChange={(e) => setCadence(e.target.value)} className="border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }}>
                    {["monthly", "quarterly", "half-yearly", "annual", "one-time"].map((c) => <option key={c}>{c}</option>)}
                  </select>
                  <input type="date" value={firstDue} onChange={(e) => setFirstDue(e.target.value)} title="First statutory due date" className="border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
                  <input value={cName} onChange={(e) => setCName(e.target.value)} placeholder="Client contact person" className="border rounded-md px-2 py-1.5 text-xs w-44" style={{ borderColor: "var(--line)" }} />
                  <input value={cEmail} onChange={(e) => setCEmail(e.target.value)} placeholder="Contact email" className="border rounded-md px-2 py-1.5 text-xs w-52" style={{ borderColor: "var(--line)" }} />
                </div>
                <div className="flex gap-2 items-center">
                  <button disabled={!firstDue || !cEmail.trim()} onClick={() => run(async () => {
                    await api.post(`/onboardings/${oid}/complete`, {
                      cadence, first_due: new Date(firstDue + "T12:00:00").toISOString(),
                      contact_name: cName.trim(), contact_email: cEmail.trim(),
                    });
                    pushToast(`Onboarding complete — ${ob.service} is now under deadline tracking`);
                    setCompleteMode(false);
                  })} className="px-4 py-2 rounded-lg text-white text-sm font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>
                    Complete & create duty
                  </button>
                  <button onClick={() => setCompleteMode(false)} className="text-xs" style={{ color: "var(--mut)" }}>cancel</button>
                </div>
              </div>
            )}
          </div>
        )}

        {ob.status === "complete" && (
          <div className="mt-4 rounded-lg border p-3.5" style={{ background: "var(--accent-soft)", borderColor: "var(--accent)" }}>
            <div className="text-sm font-bold" style={{ color: "var(--accent)" }}>🔒 ONBOARDING COMPLETE — trail sealed</div>
            <div className="text-xs mt-1" style={{ color: "var(--ink)" }}>
              Documentation closed in <b className="font-mono2">{fmtDur(new Date(ob.completed_at).getTime() - new Date(ob.created_at).getTime())}</b> — the
              recurring duty is live in the deadline engine. No further changes are possible; the trail below is read-only
              (credential reveals stay available and are still logged).
            </div>
            {ob.stars && ob.stars.length > 0 && (
              <div className="mt-2.5 flex gap-4 flex-wrap items-center text-xs">
                <span className="text-[10px] uppercase tracking-wider font-bold" style={{ color: "var(--mut)" }}>Holding-time stars</span>
                {ob.stars.map((s) => (
                  <span key={s.user_id} className="flex items-center gap-1.5">
                    {byId(s.user_id).name} <Stars n={s.stars} />
                    <span className="font-mono2" style={{ color: "var(--mut)" }}>({fmtDur(s.total_held_ms)} over {s.holdings} pass{s.holdings !== 1 ? "es" : ""})</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      <section className="mt-4 bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
        <button onClick={() => setTrailOpen(!trailOpen)} className="w-full text-left flex items-center justify-between">
          <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Trail ({(ob.events || []).length})</h3>
          <span style={{ color: "var(--mut)" }}>{trailOpen ? "▾" : "▸"}</span>
        </button>
        {trailOpen && (
          <div className="mt-3">
            {(ob.events || []).map((e, i) => (
              <div key={i} className="text-xs py-1.5 border-b last:border-0" style={{ borderColor: "var(--line)" }}>
                <span className="font-mono2" style={{ color: "var(--mut)" }}>{fmtDT(new Date(e.at).getTime())} · {e.by ? byId(e.by).name : "SYSTEM"}</span> — {e.text}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

/* ---------- firm-wide performance (management only) ---------- */

/* One-line summary of the firm's active performance standard (feature: firm-definable
   targets) — shown on the Performance tab and the pending board so the standard is public. */
function targetsSummary(t, v) {
  const bits = [];
  if (t.proposal.cycle_target_days != null) bits.push(`proposal cycle ≤${t.proposal.cycle_target_days}d`);
  bits.push(`proposal hold ≤${t.proposal.hold_target_days}d`);
  if (t.onboarding.cycle_target_days != null) bits.push(`onboarding cycle ≤${t.onboarding.cycle_target_days}d`);
  bits.push(`onboarding hold ≤${t.onboarding.hold_target_days}d`);
  bits.push(`duty grace ${t.duty.grace_bands.join("/")}d`);
  if (t.invoicing.target_days != null) bits.push(`invoice within ${t.invoicing.target_days}d of EL send`);
  bits.push(`invoice grace ${t.invoicing.grace_bands.join("/")}d`);
  return `Firm standard (v${v}): ${bits.join(" · ")}`;
}

const fmtAge = (ms) => {
  if (ms == null) return "—";
  const d = Math.floor(ms / 86400000);
  if (d >= 1) return `${d}d ${Math.floor((ms % 86400000) / 3600000)}h`;
  const h = Math.floor(ms / 3600000);
  return h >= 1 ? `${h}h` : `${Math.max(1, Math.floor(ms / 60000))}m`;
};

function PerformanceScreen() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [openU, setOpenU] = useState(null);
  useEffect(() => {
    api.get("/performance/employees").then(setData).catch((e) => setErr(e.message));
  }, []);
  if (err) return <div className="max-w-5xl mx-auto text-sm" style={{ color: "var(--mut)" }}>{err}</div>;
  if (!data) return <div className="max-w-5xl mx-auto text-sm" style={{ color: "var(--mut)" }}>Loading performance…</div>;
  const half = (n) => Math.round(n * 2) / 2;
  const avgCell = (avg, count) =>
    avg == null
      ? <span className="text-xs" style={{ color: "var(--mut)" }}>—</span>
      : <span className="flex items-center gap-1.5"><Stars n={half(avg)} /><span className="text-[10px]" style={{ color: "var(--mut)" }}>({count})</span></span>;
  return (
    <div className="max-w-5xl mx-auto">
      <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>Employee performance</h1>
      <p className="text-sm mt-1" style={{ color: "var(--mut)" }}>Firm-wide ratings computed from every completed proposal cycle and duty completion. Visible to management only. Click a row for the person's recent star events.</p>
      <div className="mt-4 bg-white rounded-xl border overflow-hidden" style={{ borderColor: "var(--line)" }}>
        <div className="grid grid-cols-[1fr_115px_115px_115px_115px_185px_105px] gap-3 px-5 py-2.5 text-[11px] uppercase tracking-wider border-b" style={{ color: "var(--mut)", borderColor: "var(--line)" }}>
          <div>Employee</div><div>Proposals</div><div>Duties</div><div>Onboarding</div><div>Invoicing</div><div>Overall</div><div>Open workload</div>
        </div>
        {data.employees.map((e) => {
          const isOpen = openU === e.user_id;
          return (
            <div key={e.user_id} className="border-b last:border-0" style={{ borderColor: "var(--line)" }}>
              <button onClick={() => setOpenU(isOpen ? null : e.user_id)} className="w-full grid grid-cols-[1fr_115px_115px_115px_115px_185px_105px] gap-3 px-5 py-3.5 text-sm text-left items-center hover:bg-gray-50">
                <span><b>{e.name}</b><div className="text-[11px] font-normal" style={{ color: "var(--mut)" }}>{e.designation} · {e.role}</div></span>
                <span>{avgCell(e.proposal_avg_stars, e.proposal_count)}</span>
                <span>{avgCell(e.duties_avg_stars, e.duty_count)}</span>
                <span>{avgCell(e.onboarding_avg_stars, e.onboarding_count)}</span>
                <span>{avgCell(e.invoicing_avg_stars, e.invoicing_count)}</span>
                <span>
                  {e.overall_avg == null
                    ? <span className="text-xs" style={{ color: "var(--mut)" }}>no completed work yet</span>
                    : <span className="flex items-center gap-2"><span className="font-mono2 text-lg font-bold" style={{ color: "var(--accent)" }}>{e.overall_avg.toFixed(2)}</span><Stars n={half(e.overall_avg)} /><span className="text-[10px]" style={{ color: "var(--mut)" }}>({e.event_count})</span></span>}
                </span>
                <span className="text-xs font-mono2" style={{ color: e.open_workload.total > 0 ? "var(--ink)" : "var(--mut)" }}>{e.open_workload.held_proposals} prop · {e.open_workload.open_duties} duty</span>
              </button>
              {isOpen && (
                <div className="border-t px-5 py-3" style={{ borderColor: "var(--line)", background: "var(--paper)" }}>
                  <div className="text-[10px] uppercase tracking-wider font-bold mb-2" style={{ color: "var(--mut)" }}>Recent star events — newest first</div>
                  {e.recent_events.length === 0 && <div className="text-xs" style={{ color: "var(--mut)" }}>No completed work yet.</div>}
                  {e.recent_events.map((ev, i) => (
                    <div key={i} className="flex items-center gap-3 py-1.5 text-xs border-b last:border-0" style={{ borderColor: "var(--line)" }}>
                      <span className="w-20 text-[10px] uppercase font-bold" style={{ color: ev.source === "proposal" ? "var(--accent)" : ev.source === "onboarding" ? "#5C4A8A" : ev.source === "invoicing" ? "var(--ink)" : "var(--amber)" }}>{ev.source}</span>
                      <span className="flex-1">{ev.label}</span>
                      <span className="font-mono2" style={{ color: "var(--mut)" }}>{fmtDT(new Date(ev.at).getTime())}</span>
                      <Stars n={ev.stars} />
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {data.targets && (
        <div className="mt-3 text-[11px] px-3 py-2 rounded-md font-medium" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
          {targetsSummary(data.targets, data.config_version)} — changed under Performance settings; new targets apply to future scoring only.
        </div>
      )}
      <div className="mt-3 text-[10px] space-y-0.5" style={{ color: "var(--mut)" }}>
        <div>Proposal cycles: {data.proposal_stars_scale_text}</div>
        <div>Duty completions: {data.duty_stars_scale_text}</div>
        <div>Onboarding relays: {data.onboarding_stars_scale_text}</div>
        <div>Invoicing: {data.invoicing_stars_scale_text}</div>
      </div>
    </div>
  );
}

/* ---------- Pending across the firm (manager/admin) ---------- */

const PENDING_TYPE = {
  proposal: ["PROPOSAL", "var(--accent)"],
  onboarding: ["ONBOARDING", "#5C4A8A"],
  duty: ["DUTY", "var(--amber)"],
  invoice: ["INVOICE", "var(--ink)"],
  receipt: ["RECEIPT", "var(--ink)"],
};

function PendingBoard({ goto }) {
  const [b, setB] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    api.get("/performance/pending").then(setB).catch((e) => setErr(e.message));
  }, []);
  if (err) return <div className="max-w-5xl mx-auto text-sm" style={{ color: "var(--mut)" }}>{err}</div>;
  if (!b) return <div className="max-w-5xl mx-auto text-sm" style={{ color: "var(--mut)" }}>Loading pending work…</div>;

  const dueText = (item) => {
    if (!item.due_at) return null;
    const ms = new Date(item.due_at).getTime() - Date.now();
    const d = Math.floor(Math.abs(ms) / 86400000);
    return ms < 0 ? `${d}d overdue` : `due in ${d}d`;
  };

  return (
    <div className="max-w-5xl mx-auto">
      <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>Pending across the firm</h1>
      <p className="text-sm mt-1" style={{ color: "var(--mut)" }}>
        Everything currently waiting on someone, aging-sorted and grouped by person: held proposals,
        onboardings in progress, open duties, and the in-house accountant's invoicing queue.
        Red rows are overdue or beyond the firm's target.
      </p>
      <div className="mt-2 text-[11px] px-3 py-2 rounded-md font-medium" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
        {targetsSummary(b.targets, b.config_version)}
      </div>

      {b.people.length === 0 && <div className="mt-6 text-sm" style={{ color: "var(--mut)" }}>Nothing is pending anywhere. 🎉</div>}
      {b.people.map((p) => (
        <div key={p.user_id || "accounts"} className="mt-4 bg-white rounded-xl border overflow-hidden" style={{ borderColor: "var(--line)" }}>
          <div className="flex items-center gap-3 px-5 py-3 border-b" style={{ borderColor: "var(--line)", background: "var(--paper)" }}>
            <span className="flex-1 text-sm"><b>{p.name}</b>
              <span className="ml-2 text-xs" style={{ color: "var(--mut)" }}>{p.designation ? `${p.designation} · ` : ""}{p.role}</span></span>
            <span className="text-xs font-mono2" style={{ color: "var(--mut)" }}>{p.counts.total} pending</span>
            {p.counts.overdue > 0 && (
              <span className="text-[11px] px-2 py-0.5 rounded-full font-bold" style={{ background: "var(--red-soft)", color: "var(--red)" }}>
                {p.counts.overdue} overdue / over target
              </span>
            )}
          </div>
          {p.items.map((item, i) => {
            const [badge, badgeColor] = PENDING_TYPE[item.type] || [item.type, "var(--mut)"];
            const hot = item.overdue || item.over_target;
            const due = dueText(item);
            return (
              <button key={i} onClick={() => goto(item)} className="w-full flex items-center gap-3 px-5 py-2.5 text-left text-sm border-b last:border-0 hover:bg-gray-50"
                style={{ borderColor: "var(--line)", background: hot ? "var(--red-soft)" : undefined }}>
                <span className="w-24 shrink-0 text-[9px] font-bold tracking-wider" style={{ color: badgeColor }}>{badge}</span>
                <span className="flex-1 min-w-0">
                  <span className="font-medium">{item.label}</span>
                  <span className="ml-2 text-xs" style={{ color: "var(--mut)" }}>{item.sublabel}</span>
                </span>
                {item.pending_since && (
                  <span className="text-xs font-mono2 whitespace-nowrap" style={{ color: item.over_target ? "var(--red)" : "var(--mut)" }}>
                    held {fmtAge(item.age_ms)}{item.over_target && " · over target"}
                  </span>
                )}
                {due && (
                  <span className="text-xs font-mono2 whitespace-nowrap font-semibold"
                    style={{ color: item.overdue ? "var(--red)" : (new Date(item.due_at).getTime() - Date.now() < 3 * 86400000 ? "var(--amber)" : "var(--mut)") }}>
                    {due}
                  </span>
                )}
                <span className="text-xs underline shrink-0" style={{ color: "var(--accent)" }}>open</span>
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}

/* ---------- Performance settings (firm-definable targets, manager/admin) ---------- */

function PerfNum({ label, value, set, step = 1, placeholder, hint }) {
  return (
    <label className="text-xs font-semibold block" style={{ color: "var(--mut)" }}>{label}
      <input type="number" min="0" step={step} value={value} placeholder={placeholder}
        onChange={(e) => set(e.target.value)}
        className="mt-1 w-full border rounded-md px-3 py-2 text-sm font-normal" style={{ borderColor: "var(--line)" }} />
      {hint && <div className="mt-0.5 text-[10px] font-normal">{hint}</div>}
    </label>
  );
}

function PerformanceSettings() {
  const [data, setData] = useState(null);
  const [f, setF] = useState(null);
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api.get("/performance/config").then((d) => {
    setData(d);
    const c = d.config;
    setF({
      p_cycle: c.proposal.cycle_target_days ?? "", p_hold: c.proposal.hold_target_days,
      o_cycle: c.onboarding.cycle_target_days ?? "", o_hold: c.onboarding.hold_target_days,
      d1: c.duty.grace_bands[0], d2: c.duty.grace_bands[1], d3: c.duty.grace_bands[2],
      i_target: c.invoicing.target_days ?? "", i1: c.invoicing.grace_bands[0], i2: c.invoicing.grace_bands[1], i3: c.invoicing.grace_bands[2],
    });
  }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (err && !f) return <div className="max-w-3xl mx-auto text-sm" style={{ color: "var(--mut)" }}>{err}</div>;
  if (!f) return <div className="max-w-3xl mx-auto text-sm" style={{ color: "var(--mut)" }}>Loading targets…</div>;

  const opt = (v) => (v === "" ? null : Number(v));
  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      await api.put("/performance/config", {
        config: {
          proposal: { cycle_target_days: opt(f.p_cycle), hold_target_days: Number(f.p_hold) },
          onboarding: { cycle_target_days: opt(f.o_cycle), hold_target_days: Number(f.o_hold) },
          duty: { grace_bands: [Number(f.d1), Number(f.d2), Number(f.d3)] },
          invoicing: { target_days: opt(f.i_target), grace_bands: [Number(f.i1), Number(f.i2), Number(f.i3)] },
        },
        note: note.trim(),
      });
      setNote("");
      await load();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const set = (k) => (v) => setF({ ...f, [k]: v });
  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>Performance settings</h1>
      <p className="text-sm mt-1" style={{ color: "var(--mut)" }}>
        The firm's time targets that drive star ratings. The computation is fixed and applied server-side —
        only these thresholds change, every change is logged, and new targets apply to <b>future</b> scoring
        only (sealed history keeps the standard that was active when the work completed). Currently v{data.version}.
      </p>

      <Card title="Proposal → engagement cycle" sub="How fast a matter should move. The hold target drives each holder's stars; the cycle target flags slow matters on the pending board.">
        <div className="grid grid-cols-2 gap-3">
          <PerfNum label="Per-holder target hold time (days)" value={f.p_hold} set={set("p_hold")} step={0.5}
            hint="★5 at or under this; the rest of the scale stretches with it" />
          <PerfNum label="Cycle target — complete within (days)" value={f.p_cycle} set={set("p_cycle")}
            placeholder="no target" hint="optional; empty = no cycle target" />
        </div>
      </Card>

      <Card title="Duty completion — grace bands" sub="Duties are always scored against the statutory deadline (not configurable). The firm sets only how much lateness maps to each star.">
        <div className="grid grid-cols-3 gap-3">
          <PerfNum label="★4 up to (days late)" value={f.d1} set={set("d1")} step={0.5} />
          <PerfNum label="★3 up to (days late)" value={f.d2} set={set("d2")} step={0.5} />
          <PerfNum label="★2 up to (days late)" value={f.d3} set={set("d3")} step={0.5} />
        </div>
        <div className="mt-2 text-[10px]" style={{ color: "var(--mut)" }}>On/before due is always ★5; beyond the last band is ★1; declared-without-proof stays capped at ★3.</div>
      </Card>

      <Card title="Onboarding relay" sub="Same mechanics as proposals — target durations for the documentation relay.">
        <div className="grid grid-cols-2 gap-3">
          <PerfNum label="Per-holder target hold time (days)" value={f.o_hold} set={set("o_hold")} step={0.5} />
          <PerfNum label="Cycle target — complete within (days)" value={f.o_cycle} set={set("o_cycle")} placeholder="no target" />
        </div>
      </Card>

      <Card title="Invoicing (accountant)" sub="Target days from EL send to invoice-raised; when set it becomes the ★5 anchor (otherwise the payment's own due date is used).">
        <div className="grid grid-cols-4 gap-3">
          <PerfNum label="Target (days from EL send)" value={f.i_target} set={set("i_target")} placeholder="use due date" />
          <PerfNum label="★4 up to (days late)" value={f.i1} set={set("i1")} step={0.5} />
          <PerfNum label="★3 up to (days late)" value={f.i2} set={set("i2")} step={0.5} />
          <PerfNum label="★2 up to (days late)" value={f.i3} set={set("i3")} step={0.5} />
        </div>
      </Card>

      <div className="mt-4 flex gap-2 items-center">
        <input value={note} onChange={(e) => setNote(e.target.value)}
          placeholder="Mandatory note — why is the standard changing? (kept on the change log)"
          className="flex-1 border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
        <button disabled={busy || !note.trim()} onClick={save}
          className="px-5 py-2 rounded-lg text-white text-sm font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>
          {busy ? "Saving…" : "Save as new version"}
        </button>
      </div>
      {err && <div className="mt-2 text-xs px-2.5 py-2 rounded-md font-medium" style={{ background: "var(--red-soft)", color: "var(--red)" }}>⚠️ {err}</div>}

      <Card title="Change log" sub="Every version, newest first — who changed the standard, when, and why.">
        {data.history.length === 0 && <div className="text-xs" style={{ color: "var(--mut)" }}>No changes yet — the built-in defaults (v0) apply.</div>}
        {data.history.map((h) => (
          <div key={h.version} className="flex gap-3 items-baseline py-1.5 text-xs border-b last:border-0" style={{ borderColor: "var(--line)" }}>
            <span className="font-mono2 font-bold w-8">v{h.version}</span>
            <span className="w-36 font-mono2" style={{ color: "var(--mut)" }}>{fmtDT(new Date(h.at).getTime())}</span>
            <span className="w-32 truncate" style={{ color: "var(--mut)" }}>{h.by || "—"}</span>
            <span className="flex-1">"{h.note}"</span>
          </div>
        ))}
      </Card>
    </div>
  );
}

function ClientPerformance({ clientId }) {
  const [r, setR] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    api.get(`/clients/${clientId}/performance`).then(setR).catch((e) => setErr(e.message));
  }, [clientId]);
  if (err) return <div className="text-xs" style={{ color: "var(--mut)" }}>{err}</div>;
  if (!r) return <div className="text-xs" style={{ color: "var(--mut)" }}>Loading task history…</div>;
  const t = (x) => new Date(x).getTime();
  return (
    <div className="rounded-lg border p-3 space-y-3" style={{ borderColor: "var(--line)", background: "var(--paper)" }}>
      {(r.proposal_cycles || (r.proposal_cycle ? [r.proposal_cycle] : [])).map((cycle) => (
        <div key={cycle.ref} className="bg-white rounded-md border p-3" style={{ borderColor: "var(--line)" }}>
          <div className="flex items-center justify-between flex-wrap gap-2 text-xs">
            <b>Onboarding cycle — {cycle.ref}{cycle.services?.length ? <span className="font-normal" style={{ color: "var(--mut)" }}> · {cycle.services.join(", ")}</span> : null}</b>
            <span className="font-mono2 font-bold" style={{ color: "var(--accent)" }}>{fmtDur(cycle.total_ms)} request → EL sent</span>
          </div>
          <div className="mt-1.5 flex gap-4 flex-wrap text-xs">
            {cycle.per_employee.map((e) => (
              <span key={e.user_id} className="flex items-center gap-1.5">{e.name} <Stars n={e.stars} /></span>
            ))}
          </div>
        </div>
      ))}
      {(r.onboardings || []).length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wider font-bold mb-1.5" style={{ color: "var(--mut)" }}>Completed onboardings ({r.onboardings.length})</div>
          {r.onboardings.map((o, i) => (
            <div key={i} className="bg-white border rounded-md px-3 py-2 mb-1.5 flex items-center gap-3 text-xs flex-wrap" style={{ borderColor: "var(--line)" }}>
              <span className="flex-1 min-w-[150px]"><b>Onboarding — {o.service}</b><div style={{ color: "var(--mut)" }}>{o.staff_name}</div></span>
              <span className="font-mono2" style={{ color: "var(--mut)" }}>{fmtDur(o.total_ms)} → duty live {fmtD(t(o.completed_at))}</span>
              <span className="flex gap-3 flex-wrap">
                {o.per_participant.map((e) => (
                  <span key={e.user_id} className="flex items-center gap-1">{e.name} <Stars n={e.stars} /></span>
                ))}
              </span>
            </div>
          ))}
        </div>
      )}
      <div>
        <div className="text-[10px] uppercase tracking-wider font-bold mb-1.5" style={{ color: "var(--mut)" }}>Task record — newest first ({r.tasks.length})</div>
        {r.tasks.length === 0 && <div className="text-xs" style={{ color: "var(--mut)" }}>No completed duties on record for this client yet.</div>}
        {r.tasks.map((task, i) => (
          <div key={i} className="bg-white border rounded-md px-3 py-2 mb-1.5 flex items-center gap-3 text-xs flex-wrap" style={{ borderColor: "var(--line)" }}>
            <span className="flex-1 min-w-[160px]"><b>{task.service}</b>{task.period && <span style={{ color: "var(--mut)" }}> · {task.period}</span>}<div style={{ color: "var(--mut)" }}>{task.staff_name}</div></span>
            <span className="font-mono2" style={{ color: "var(--mut)" }}>due {fmtD(t(task.due_at))} → done {fmtD(t(task.completed_at))}</span>
            <span className="font-mono2 font-bold" style={{ color: task.late_ms > 0 ? "var(--red)" : "var(--accent)" }}>{task.timing}</span>
            {task.method === "declared" ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: "var(--paper)", color: "var(--mut)", border: "1px solid var(--line)" }}>declared · capped — no proof</span>
            ) : (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>{task.method}</span>
            )}
            <Stars n={task.stars} />
          </div>
        ))}
      </div>
      <div className="text-[10px]" style={{ color: "var(--mut)" }}>{r.duty_stars_scale_text}</div>
    </div>
  );
}

/* Fetches the server-computed report (client-held time excluded server-side) and adapts it
   onto the verbatim PerfReport component's expected shape. */
function PerfReportHost({ p, byId }) {
  const { uuidOf } = useData();
  const [report, setReport] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let on = true;
    setReport(null);
    api.get(`/proposals/${uuidOf(p.id)}/report`)
      .then((r) => { if (on) setReport(r); })
      .catch((e) => { if (on) setErr(e.message); });
    return () => { on = false; };
  }, [p.id, p.status]); // eslint-disable-line react-hooks/exhaustive-deps
  if (err) return <div className="mt-6 text-sm" style={{ color: "var(--mut)" }}>{err}</div>;
  if (!report) return <div className="mt-6 text-sm" style={{ color: "var(--mut)" }}>Generating report…</div>;
  const t = (x) => new Date(x).getTime();
  const p2 = {
    ...p,
    createdAt: t(report.created_at),
    onboardingCompletedAt: t(report.completed_at),
    holderLog: report.per_employee.flatMap((e) =>
      e.holdings.map((h) => ({ userId: e.user_id, start: t(h.started), end: t(h.ended), reason: h.reason }))),
  };
  return <PerfReport p={p2} byId={byId}
    scaleText={`Rating reflects average time to pass the baton while holding it (${report.stars_scale_text}). Click an employee to see their tasks, longest first.`} />;
}

function PerfReport({ p, byId, scaleText }) {
  const [openUser, setOpenUser] = useState(null);
  const total = (p.onboardingCompletedAt || p.el?.sentAt || p.events[p.events.length - 1].at) - p.createdAt;

  const perUser = {};
  p.holderLog.forEach((h) => {
    const dur = (h.end ?? h.start) - h.start;
    (perUser[h.userId] ||= { periods: [], total: 0 }).periods.push({ ...h, dur });
    perUser[h.userId].total += dur;
  });
  const rows = Object.entries(perUser).map(([uId, x]) => {
    const avgD = days(x.total / x.periods.length);
    return { uId, ...x, avgD, stars: starsFor(avgD), longest: Math.max(...x.periods.map((q) => q.dur)) };
  }).sort((a, b) => b.stars - a.stars || a.total - b.total);

  return (
    <div className="max-w-4xl mt-5 space-y-5">
      <section className="bg-white border rounded-xl p-5" style={{ borderColor: "var(--accent)" }}>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h3 className="font-disp font-bold text-lg" style={{ color: "var(--ink)" }}>Proposal → engagement letter — performance report</h3>
            <div className="text-xs mt-1" style={{ color: "var(--mut)" }}>Generated automatically at completion · audit trail sealed · visible to management only</div>
          </div>
          <div className="text-right">
            <div className="font-mono2 text-2xl font-bold" style={{ color: "var(--accent)" }}>{fmtDur(total)}</div>
            <div className="text-[10px] uppercase tracking-wider" style={{ color: "var(--mut)" }}>request → EL sent · {fmtD(p.createdAt)} → {fmtD(p.onboardingCompletedAt || p.el.sentAt)}</div>
          </div>
        </div>
      </section>

      <section className="bg-white border rounded-xl p-5" style={{ borderColor: "var(--line)" }}>
        <h4 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Employee performance — responsiveness ratings</h4>
        <p className="text-[11px] mt-0.5" style={{ color: "var(--mut)" }}>{scaleText || "Rating reflects average time to pass the baton while holding it (≤½ day ★5 · ≤1d ★4½ · ≤2d ★4 · ≤3d ★3½ · ≤5d ★3 · ≤7d ★2 · beyond ★1). Click an employee to see their tasks, longest first."}</p>
        <div className="mt-3 space-y-2">
          {rows.map((r) => {
            const u = byId(r.uId);
            const isOpen = openUser === r.uId;
            return (
              <div key={r.uId} className="border rounded-lg overflow-hidden" style={{ borderColor: isOpen ? "var(--accent)" : "var(--line)" }}>
                <button onClick={() => setOpenUser(isOpen ? null : r.uId)} className="w-full flex items-center gap-4 px-4 py-3 text-sm text-left hover:bg-gray-50">
                  <div className="flex-1">
                    <b>{u.name}</b> <span className="text-xs" style={{ color: "var(--mut)" }}>· {u.designation} · {u.role}</span>
                  </div>
                  <Stars n={r.stars} />
                  <div className="text-xs font-mono2 text-right w-40" style={{ color: "var(--mut)" }}>
                    held {fmtDur(r.total)} · {r.periods.length} task{r.periods.length !== 1 && "s"}<br />avg {r.avgD.toFixed(1)}d / task
                  </div>
                  <span style={{ color: "var(--mut)" }}>{isOpen ? "▾" : "▸"}</span>
                </button>
                {isOpen && (
                  <div className="border-t px-4 py-3" style={{ borderColor: "var(--line)", background: "var(--paper)" }}>
                    <div className="text-[10px] uppercase tracking-wider font-bold mb-2" style={{ color: "var(--mut)" }}>Tasks held — most time taken first</div>
                    {[...r.periods].sort((a, b) => b.dur - a.dur).map((q, i) => (
                      <div key={i} className="flex items-center gap-3 py-1.5 text-xs border-b last:border-0" style={{ borderColor: "var(--line)" }}>
                        <span className="font-mono2 w-14 text-right font-bold" style={{ color: i === 0 && r.periods.length > 1 ? "var(--red)" : "var(--ink)" }}>{fmtDur(q.dur)}</span>
                        <span className="flex-1">{q.reason || "responsibility held"}{i === 0 && r.periods.length > 1 && <span className="ml-2 px-1.5 py-0.5 rounded-full text-[9px] font-bold" style={{ background: "var(--red-soft)", color: "var(--red)" }}>LONGEST</span>}</span>
                        <span className="font-mono2" style={{ color: "var(--mut)" }}>{fmtDT(q.start)} → {fmtDT(q.end)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <p className="text-[10px] mt-3" style={{ color: "var(--mut)" }}>Note: time spent awaiting the client (proposal / EL with the client) is excluded from employee ratings — only internally-held periods count.</p>
      </section>
    </div>
  );
}

/* ---------- chat & audit ---------- */

function ChatTab({ p, me, byId, send, closed }) {
  const [text, setText] = useState("");
  const other = me.id === p.assignedTo ? p.requestedBy : p.assignedTo;
  return (
    <div className="max-w-2xl mt-5">
      <div className="bg-white border rounded-xl p-4 min-h-[280px] flex flex-col" style={{ borderColor: "var(--line)" }}>
        <div className="text-xs pb-3 border-b" style={{ color: "var(--mut)", borderColor: "var(--line)" }}>
          Direct chat between {byId(p.requestedBy).name} and {byId(p.assignedTo).name} on {p.id}. Informal channel — never changes task state; fully captured in the audit trail.
        </div>
        <div className="flex-1 py-3 space-y-2.5 overflow-y-auto">
          {p.chat.map((m) => (
            <div key={m.id} className={`max-w-[75%] ${m.by === me.id ? "ml-auto" : ""}`}>
              <div className="text-sm px-3 py-2 rounded-xl" style={m.by === me.id ? { background: "var(--accent)", color: "#fff" } : { background: "var(--paper)" }}>{m.text}</div>
              <div className={`text-[10px] mt-0.5 font-mono2 ${m.by === me.id ? "text-right" : ""}`} style={{ color: "var(--mut)" }}>{byId(m.by).name.split(" ")[0]} · {fmtDT(m.at)}</div>
            </div>
          ))}
          {p.chat.length === 0 && <div className="text-xs text-center py-8" style={{ color: "var(--mut)" }}>No messages yet.</div>}
        </div>
        {closed ? (
          <div className="pt-2 border-t text-xs text-center py-2.5 rounded-md" style={{ borderColor: "var(--line)", color: "var(--mut)", background: "var(--paper)" }}>
            🔒 Process closed — the audit trail is sealed and no further messages can be added.
          </div>
        ) : (
          <div className="flex gap-2 pt-2 border-t" style={{ borderColor: "var(--line)" }}>
            <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && text.trim()) { send(text.trim()); setText(""); } }} placeholder={`Message ${byId(other).name.split(" ")[0]}…`} className="flex-1 border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
            <button onClick={() => { if (text.trim()) { send(text.trim()); setText(""); } }} className="px-4 py-2 rounded-md text-white text-sm font-semibold" style={{ background: "var(--ink)" }}>Send</button>
          </div>
        )}
      </div>
    </div>
  );
}

function AuditTab({ p, byId, closed }) {
  return (
    <div className="max-w-3xl mt-5">
      {closed && (
        <div className="mb-3 rounded-xl border p-3 text-sm flex items-center gap-2" style={{ background: "var(--accent-soft)", borderColor: "var(--accent)", color: "var(--accent)" }}>
          🔒 <b>Trail sealed</b> — proposal → engagement letter process complete; this record is final. Client documentation proceeds as a separate onboarding workflow.
        </div>
      )}
      <div className="bg-white border rounded-xl p-5" style={{ borderColor: "var(--line)" }}>
        <div className="text-xs mb-4" style={{ color: "var(--mut)" }}>
          Append-only. Every state change, document event, signature application, email send, and chat message — timestamped and attributed. No role, including Admin, can edit or delete entries.
        </div>
        <div className="relative pl-5">
          <div className="absolute left-1.5 top-1 bottom-1 w-px" style={{ background: "var(--line)" }} />
          {p.events.map((e) => (
            <div key={e.id} className="relative pb-4">
              <div className="absolute -left-5 top-1 w-3 h-3 rounded-full border-2 bg-white" style={{ borderColor: e.by === "system" ? "var(--mut)" : "var(--accent)" }} />
              <div className="text-[11px] font-mono2" style={{ color: "var(--mut)" }}>{fmtDT(e.at)} · {e.by === "system" ? "SYSTEM" : byId(e.by).name}</div>
              <div className="text-sm mt-0.5">{e.text}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ================================================================== */
/*  First-deployment setup wizard                                      */
/* ================================================================== */

const genTempPw = () => Math.random().toString(36).slice(2, 6) + "-" + Math.random().toString(36).slice(2, 6);

function ActAdder({ onAdd }) {
  const [client, setClient] = useState("");
  const [service, setService] = useState("");
  const [cadence, setCadence] = useState("monthly");
  const [due, setDue] = useState("");
  const [cName, setCName] = useState("");
  const [cEmail, setCEmail] = useState("");
  return (
    <div className="mt-1">
      <div className="flex gap-1.5">
        <input placeholder="Client name" value={client} onChange={(e) => setClient(e.target.value)} className="flex-1 border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
        <input placeholder="Duty, e.g. VAT filing" value={service} onChange={(e) => setService(e.target.value)} className="flex-1 border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
        <select value={cadence} onChange={(e) => setCadence(e.target.value)} className="border rounded-md px-1.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }}>
          {CADENCES.map((c) => <option key={c}>{c}</option>)}
        </select>
        <input type="date" value={due} onChange={(e) => setDue(e.target.value)} title="Next due date from today — the anchor for all auto-computed future deadlines" className="border rounded-md px-1.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
      </div>
      <div className="flex gap-1.5 mt-1.5">
        <input placeholder="Client contact person" value={cName} onChange={(e) => setCName(e.target.value)} className="flex-1 border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
        <input placeholder="Contact email — deliverables & reports are emailed here" value={cEmail} onChange={(e) => setCEmail(e.target.value)} className="flex-1 border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
        <button onClick={() => { if (client.trim() && service.trim() && due) { onAdd({ client: client.trim(), service: service.trim(), cadence, due, contact: { name: cName.trim(), email: cEmail.trim() } }); setClient(""); setService(""); setDue(""); setCName(""); setCEmail(""); } }} className="px-2.5 py-1.5 rounded-md border text-xs font-medium" style={{ borderColor: "var(--line)" }}>Add</button>
      </div>
      <div className="text-[10px] mt-1" style={{ color: "var(--mut)" }}>Next due date (as of Baton implementation) is required — every subsequent deadline is computed from it per the cadence. The contact receives report deliveries; completion requires proof of work (or an explicit declared reason).</div>
    </div>
  );
}

function SetupWizard({ onCancel, onDone, initial = null, seatsLimit = null, cancelLabel = "Cancel setup" }) {
  const [step, setStep] = useState(1);
  const [f, setF] = useState(initial?.firm ?? { name: "", short: "", address: "", trn: "", phone: "", email: "", accent: "#1E6E56" });
  const [emps, setEmps] = useState(initial?.emps ?? [{ id: uid(), name: "", designation: "", email: "", role: "", tempPw: genTempPw(), signatory: false, sig: null, acts: [], actsOpen: false }]);
  const [services, setServices] = useState([...SERVICES]);
  const [newSvc, setNewSvc] = useState("");
  const [templates, setTemplates] = useState({ letterhead: null, proposal: null, el: null });

  const setEmp = (id, patch) => setEmps((es) => es.map((e) => (e.id === id ? { ...e, ...patch } : e)));
  const validEmp = (e) => e.name.trim() && e.email.trim();
  const overSeats = seatsLimit != null && emps.length > seatsLimit;

  const stepOK = {
    1: f.name.trim() && f.short.trim() && f.email.trim(),
    2: services.length > 0,
    3: emps.length > 0 && emps.every(validEmp) && !overSeats,
    4: emps.every((e) => e.role) && emps.some((e) => e.role === "Admin"),
    5: true, // credentials are issued server-side at deployment — nothing to do here
    6: emps.filter((e) => e.signatory).length >= 1 && emps.filter((e) => e.signatory).every((e) => e.sig),
  }[step];

  const finish = () => {
    const newUsers = emps.map((e) => ({ id: e.id, name: e.name.trim(), designation: e.designation.trim() || e.role, email: e.email.trim(), role: e.role, signatory: e.signatory, sigSpecimen: e.sig, existingActivities: e.acts || [] }));
    onDone({ ...f, name: f.name.trim(), short: f.short.trim(), services, templates }, newUsers);
  };

  const STEPS = ["Firm", "Activities", "Employees", "Roles", "Credentials", "Signatures"];

  return (
    <div className="min-h-screen py-10 px-6" style={{ background: "var(--paper)" }}>
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between">
          <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>First deployment — firm setup</h1>
          <button onClick={onCancel} className="text-xs underline" style={{ color: "var(--mut)" }}>{cancelLabel}</button>
        </div>
        <div className="mt-4 flex items-center gap-2 text-[11px] font-medium">
          {STEPS.map((s, i) => (
            <span key={s} className="flex items-center gap-2">
              <span className="px-2.5 py-1 rounded-full" style={i + 1 === step ? { background: "var(--accent)", color: "#fff" } : i + 1 < step ? { background: "var(--accent-soft)", color: "var(--accent)" } : { background: "#fff", color: "var(--mut)", border: "1px solid var(--line)" }}>
                {i + 1 < step ? "✓ " : `${i + 1} · `}{s}
              </span>
              {i < STEPS.length - 1 && <span style={{ color: "var(--line)" }}>—</span>}
            </span>
          ))}
        </div>

        {step === 1 && (
          <Card title="1 · Define the firm" sub="These details drive the letterhead on every generated proposal and engagement letter, and the brand accent across the CRM.">
            <div className="grid grid-cols-2 gap-3">
              <Inp label="Registered name *" v={f.name} set={(v) => setF({ ...f, name: v })} ph="e.g. Emirates Ledger Consultancy LLC" />
              <Inp label="Short name *" v={f.short} set={(v) => setF({ ...f, short: v })} ph="e.g. Emirates Ledger" />
              <Inp label="Address" v={f.address} set={(v) => setF({ ...f, address: v })} ph="Office, building, emirate" />
              <Inp label="TRN" v={f.trn} set={(v) => setF({ ...f, trn: v })} ph="TRN 100-XXXX-XXXX-XXX" />
              <Inp label="Phone" v={f.phone} set={(v) => setF({ ...f, phone: v })} />
              <Inp label="Firm email *" v={f.email} set={(v) => setF({ ...f, email: v })} />
              <div>
                <label className="text-xs font-semibold" style={{ color: "var(--mut)" }}>Brand accent</label>
                <input type="color" value={f.accent} onChange={(e) => setF({ ...f, accent: e.target.value })} className="mt-1 h-9 w-20 border rounded-md" style={{ borderColor: "var(--line)" }} />
              </div>
            </div>
            <div className="mt-4 pt-4 border-t" style={{ borderColor: "var(--line)" }}>
              <div className="text-[11px] font-bold uppercase tracking-wider" style={{ color: "var(--mut)" }}>Document formats — optional</div>
              <p className="text-[11px] mt-1 mb-2" style={{ color: "var(--mut)" }}>If the firm already has its own letterhead, proposal format, or engagement letter format, upload them here — generated documents will follow these templates. If skipped, Baton's standard layouts are used with your firm details and accent.</p>
              {[["letterhead", "Letterhead"], ["proposal", "Proposal format"], ["el", "Engagement letter format"]].map(([k, label]) => (
                <div key={k} className="flex items-center gap-3 text-sm py-1.5">
                  <span className="w-48" style={{ color: "var(--mut)" }}>{label}</span>
                  {templates[k] ? (
                    <span className="flex items-center gap-2 text-xs">
                      <FileLink {...templates[k]} />
                      <button onClick={() => setTemplates({ ...templates, [k]: null })} className="underline" style={{ color: "var(--red)" }}>remove</button>
                    </span>
                  ) : (
                    <FilePick small label={`Upload ${label.toLowerCase()} (optional)`} onFiles={(fs) => setTemplates({ ...templates, [k]: fs[0] })} />
                  )}
                </div>
              ))}
            </div>
          </Card>
        )}

        {step === 2 && (
          <Card title="2 · Define the firm's activities" sub="The service catalog — everything the firm offers to clients. This list drives proposal requests; managers can still type custom services, which get flagged back to Admin as catalog candidates.">
            <div className="flex flex-wrap gap-2">
              {services.map((sv) => (
                <span key={sv} className="px-3 py-1.5 rounded-full text-xs font-medium border flex items-center gap-2" style={{ borderColor: "var(--accent)", color: "var(--accent)", background: "var(--accent-soft)" }}>
                  {sv}
                  <button onClick={() => setServices(services.filter((x) => x !== sv))} style={{ color: "var(--red)" }}>×</button>
                </span>
              ))}
            </div>
            <div className="flex gap-2 mt-3">
              <input value={newSvc} onChange={(e) => setNewSvc(e.target.value)} placeholder="Add activity — e.g. Payroll & WPS Processing, ESR Filing, Company Liquidation" className="flex-1 border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
              <button onClick={() => { const v = newSvc.trim(); if (v && !services.includes(v)) { setServices([...services, v]); setNewSvc(""); } }} className="px-3 py-2 rounded-md border text-sm font-medium" style={{ borderColor: "var(--line)" }}>Add</button>
            </div>
            {services.length === 0 && <div className="mt-3 text-[11px] px-2.5 py-2 rounded-md font-medium" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>At least one activity is required.</div>}
          </Card>
        )}

        {step === 3 && (
          <Card title="3 · Add the employees" sub="Everyone who will work in Baton — partners, managers, technical staff, in-house accountants. For a firm that's already running, record each person's ongoing client duties so workload visibility is accurate from day one.">
            <div className="space-y-2">
              {emps.map((e) => (
                <div key={e.id} className="border rounded-lg p-2.5" style={{ borderColor: "var(--line)" }}>
                  <div className="flex gap-2 items-center">
                    <input placeholder="Full name *" value={e.name} onChange={(ev) => setEmp(e.id, { name: ev.target.value })} className="flex-1 border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
                    <input placeholder="Designation" value={e.designation} onChange={(ev) => setEmp(e.id, { designation: ev.target.value })} className="w-40 border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
                    <input placeholder="Work email *" value={e.email} disabled={!!e.locked} title={e.locked ? "Your own account — email cannot change during setup" : undefined} onChange={(ev) => setEmp(e.id, { email: ev.target.value })} className="w-52 border rounded-md px-3 py-2 text-sm disabled:opacity-60" style={{ borderColor: "var(--line)" }} />
                    <button onClick={() => setEmp(e.id, { actsOpen: !e.actsOpen })} className="px-2.5 py-2 rounded-md border text-xs font-medium whitespace-nowrap" style={{ borderColor: (e.acts || []).length ? "var(--accent)" : "var(--line)", color: (e.acts || []).length ? "var(--accent)" : "var(--mut)" }}>
                      Duties ({(e.acts || []).length}) {e.actsOpen ? "▾" : "▸"}
                    </button>
                    {emps.length > 1 && !e.locked && <button onClick={() => setEmps(emps.filter((x) => x.id !== e.id))} style={{ color: "var(--red)" }}>×</button>}
                  </div>
                  {e.actsOpen && (
                    <div className="mt-2 pt-2 border-t" style={{ borderColor: "var(--line)" }}>
                      <div className="text-[10px] uppercase tracking-wider font-bold mb-1.5" style={{ color: "var(--mut)" }}>Ongoing duties already assigned (pre-Baton)</div>
                      {(e.acts || []).map((a) => (
                        <div key={a.id} className="flex items-center gap-2 text-xs py-1">
                          <span className="flex-1">{a.client} — {a.service} <span style={{ color: "var(--mut)" }}>· {a.cadence} · next due {a.due}</span></span>
                          <button onClick={() => setEmp(e.id, { acts: e.acts.filter((x) => x.id !== a.id) })} style={{ color: "var(--red)" }}>×</button>
                        </div>
                      ))}
                      <ActAdder onAdd={(act) => setEmp(e.id, { acts: [...(e.acts || []), { id: uid(), ...act }] })} />
                    </div>
                  )}
                </div>
              ))}
            </div>
            <button onClick={() => setEmps([...emps, { id: uid(), name: "", designation: "", email: "", role: "", tempPw: genTempPw(), signatory: false, sig: null, acts: [], actsOpen: false }])} className="mt-3 px-3 py-2 rounded-md border text-sm font-medium" style={{ borderColor: "var(--line)" }}>+ Add employee</button>
            {seatsLimit != null && (
              <div className="mt-3 text-[11px] px-2.5 py-2 rounded-md font-medium" style={overSeats ? { background: "var(--red-soft)", color: "var(--red)" } : { background: "var(--accent-soft)", color: "var(--accent)" }}>
                {emps.length}/{seatsLimit} seats — {overSeats
                  ? "over your subscription's seat limit. Remove employees, or ask the platform operator to raise it."
                  : "your subscription's seat limit."}
              </div>
            )}
          </Card>
        )}

        {step === 4 && (
          <Card title="4 · Assign roles" sub="Roles set what each person can do. At least one Admin is required — they manage the firm, employees and signature vault.">
            <div className="space-y-2">
              {emps.map((e) => (
                <div key={e.id} className="flex gap-3 items-center text-sm">
                  <span className="flex-1 font-medium">{e.name || "—"} <span className="font-normal text-xs" style={{ color: "var(--mut)" }}>{e.email}{e.locked && " · you"}</span></span>
                  {["Admin", "Manager", "Staff", "Accountant"].map((r) => (
                    <button key={r} disabled={!!e.locked} title={e.locked ? "Your own role stays Admin during setup" : undefined} onClick={() => setEmp(e.id, { role: r, signatory: r === "Admin" || r === "Manager" ? true : e.signatory })} className="px-2.5 py-1.5 rounded-full text-xs font-medium border disabled:opacity-50" style={e.role === r ? { background: "var(--accent)", color: "#fff", borderColor: "var(--accent)" } : { borderColor: "var(--line)", color: "var(--mut)" }}>{r}</button>
                  ))}
                </div>
              ))}
            </div>
            {!emps.some((e) => e.role === "Admin") && <div className="mt-3 text-[11px] px-2.5 py-2 rounded-md font-medium" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>At least one Admin is required to proceed.</div>}
            <div className="mt-4 pt-4 border-t" style={{ borderColor: "var(--line)" }}>
              <div className="text-[11px] font-bold uppercase tracking-wider mb-2" style={{ color: "var(--mut)" }}>Permission matrix applied</div>
              <PermMatrix compact />
            </div>
          </Card>
        )}

        {step === 5 && (
          <Card title="5 · Login credentials" sub="Temporary passwords are generated by the server AT DEPLOYMENT — not here. First login forces a password reset; 2FA is available for signatories.">
            <div className="space-y-1.5">
              {emps.map((e) => (
                <div key={e.id} className="flex gap-3 items-center text-sm border rounded-md px-3 py-2" style={{ borderColor: "var(--line)" }}>
                  <span className="flex-1 font-medium">{e.name} <span className="font-normal text-xs" style={{ color: "var(--mut)" }}>{e.email} · {e.role}</span></span>
                  <span className="font-mono2 text-xs px-2 py-1 rounded" style={{ background: "var(--paper)", color: "var(--mut)" }}>{e.locked ? "already active (you)" : "issued at deployment"}</span>
                </div>
              ))}
            </div>
            <div className="mt-4 text-sm px-3 py-2.5 rounded-lg font-medium" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
              On launch, each employee receives an invite email with their temporary password, and the
              passwords are shown to you <b>once</b> on the deployment screen — copy them there. They are
              stored only as hashes and can never be recovered (an Admin can re-issue via "resend invite").
            </div>
          </Card>
        )}

        {step === 6 && (
          <Card title="6 · Digital signatures — managers & senior management" sub="Signatories' specimens go into the encrypted vault: preview-only, never downloadable, applied exclusively via the signatory's own identity-confirmed approval, with every use logged.">
            <div className="space-y-2">
              {emps.map((e) => (
                <div key={e.id} className="border rounded-lg p-3" style={{ borderColor: e.signatory ? "var(--accent)" : "var(--line)" }}>
                  <div className="flex items-center gap-3 text-sm">
                    <label className="flex items-center gap-2 flex-1">
                      <input type="checkbox" checked={e.signatory} onChange={(ev) => setEmp(e.id, { signatory: ev.target.checked, sig: ev.target.checked ? e.sig : null })} />
                      <span className="font-medium">{e.name}</span> <span className="text-xs" style={{ color: "var(--mut)" }}>· {e.role}</span>
                    </label>
                    {e.signatory && (
                      e.sig ? (
                        <span className="flex items-center gap-2">
                          {e.sig.type === "image" ? <img src={e.sig.url} alt="specimen" className="h-9 border rounded px-1 bg-white" style={{ borderColor: "var(--line)" }} /> : <span className="font-disp italic text-lg">{e.sig.text}</span>}
                          <button onClick={() => setEmp(e.id, { sig: null })} className="text-xs underline" style={{ color: "var(--red)" }}>replace</button>
                        </span>
                      ) : (
                        <span className="flex items-center gap-2">
                          <FilePick small label="Upload specimen" onFiles={(fs) => setEmp(e.id, { sig: { type: "image", url: fs[0].url, name: fs[0].name } })} />
                          <button onClick={() => setEmp(e.id, { sig: { type: "typed", text: e.name.split(" ").map((x) => x[0]).join(". ") + "." } })} className="px-2.5 py-1.5 rounded-md border text-xs font-medium" style={{ borderColor: "var(--line)" }}>Type-to-sign (initials)</button>
                        </span>
                      )
                    )}
                  </div>
                </div>
              ))}
            </div>
            {emps.filter((e) => e.signatory).length === 0 && <div className="mt-3 text-[11px] px-2.5 py-2 rounded-md font-medium" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>At least one signatory is required — proposals and engagement letters cannot be signed otherwise.</div>}
          </Card>
        )}

        <div className="mt-5 flex justify-between">
          <button disabled={step === 1} onClick={() => setStep(step - 1)} className="px-4 py-2 rounded-lg border text-sm font-medium disabled:opacity-40" style={{ borderColor: "var(--line)" }}>← Back</button>
          {step < 6 ? (
            <button disabled={!stepOK} onClick={() => setStep(step + 1)} className="px-5 py-2 rounded-lg text-white text-sm font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>Continue →</button>
          ) : (
            <button disabled={!stepOK} onClick={finish} className="px-5 py-2 rounded-lg text-white text-sm font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>🚀 Launch {f.short || "the firm"} — go to login</button>
          )}
        </div>
      </div>
    </div>
  );
}

/* ================================================================== */
/*  Admin screens                                                      */
/* ================================================================== */

function Employees({ users, setUsers }) {
  const [f, setF] = useState({ name: "", designation: "", email: "", role: "Staff" });
  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>Employees & roles</h1>
      <div className="mt-4 bg-white border rounded-xl overflow-hidden" style={{ borderColor: "var(--line)" }}>
        {users.map((u) => (
          <div key={u.id} className="flex items-center gap-4 px-5 py-3.5 border-b last:border-0 text-sm" style={{ borderColor: "var(--line)" }}>
            <div className="flex-1"><b>{u.name}</b><span className="ml-2" style={{ color: "var(--mut)" }}>{u.designation}</span></div>
            <span className="text-xs" style={{ color: "var(--mut)" }}>{u.email}</span>
            <span className="text-[11px] px-2 py-0.5 rounded-full font-medium" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>{u.role}</span>
            {u.signatory && <span className="text-[11px] px-2 py-0.5 rounded-full font-medium" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>Signatory</span>}
          </div>
        ))}
      </div>

      <Card title="Add employee" sub="An invite email with a temporary password would be sent; forced reset on first login.">
        <div className="grid grid-cols-4 gap-2">
          <input placeholder="Full name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} className="border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
          <input placeholder="Designation" value={f.designation} onChange={(e) => setF({ ...f, designation: e.target.value })} className="border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
          <input placeholder="Email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} className="border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
          <select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })} className="border rounded-md px-2 py-2 text-sm" style={{ borderColor: "var(--line)" }}>
            <option>Admin</option><option>Manager</option><option>Staff</option><option>Accountant</option>
          </select>
        </div>
        <button onClick={() => { if (f.name && f.email) { setUsers([...users, { ...f, id: uid(), signatory: false }]); setF({ name: "", designation: "", email: "", role: "Staff" }); } }} className="mt-3 px-4 py-2 rounded-lg text-white text-sm font-semibold" style={{ background: "var(--accent)" }}>
          Add & send invite
        </button>
      </Card>

      <Card title="Role permission matrix" sub="Fixed roles in v1; the data model is role → permissions so custom roles can be exposed later. Every row mirrors the actual API endpoint guards.">
        <PermMatrix />
      </Card>
    </div>
  );
}

function CatalogEditor({ firm, setFirm }) {
  const [v, setV] = useState("");
  const list = firm.services || [];
  return (
    <>
      <div className="flex flex-wrap gap-2">
        {list.map((sv) => (
          <span key={sv} className="px-3 py-1.5 rounded-full text-xs font-medium border flex items-center gap-2" style={{ borderColor: "var(--accent)", color: "var(--accent)", background: "var(--accent-soft)" }}>
            {sv}
            <button onClick={() => setFirm({ ...firm, services: list.filter((x) => x !== sv) })} style={{ color: "var(--red)" }}>×</button>
          </span>
        ))}
      </div>
      <div className="flex gap-2 mt-3">
        <input value={v} onChange={(e) => setV(e.target.value)} placeholder="Add activity to the catalog" className="flex-1 border rounded-md px-3 py-2 text-sm" style={{ borderColor: "var(--line)" }} />
        <button onClick={() => { const x = v.trim(); if (x && !list.includes(x)) { setFirm({ ...firm, services: [...list, x] }); setV(""); } }} className="px-3 py-2 rounded-md border text-sm font-medium" style={{ borderColor: "var(--line)" }}>Add</button>
      </div>
    </>
  );
}

function FirmSetup({ firm, setFirm }) {
  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>Firm & letterhead</h1>
      <Card title="Firm details" sub="Used across all generated documents.">
        <div className="grid grid-cols-2 gap-3">
          <Inp label="Registered name" v={firm.name} set={(v) => setFirm({ ...firm, name: v })} />
          <Inp label="Short name" v={firm.short} set={(v) => setFirm({ ...firm, short: v })} />
          <Inp label="Address" v={firm.address} set={(v) => setFirm({ ...firm, address: v })} />
          <Inp label="TRN" v={firm.trn} set={(v) => setFirm({ ...firm, trn: v })} />
          <Inp label="Phone" v={firm.phone} set={(v) => setFirm({ ...firm, phone: v })} />
          <Inp label="Email" v={firm.email} set={(v) => setFirm({ ...firm, email: v })} />
          <div>
            <label className="text-xs font-semibold" style={{ color: "var(--mut)" }}>Brand accent</label>
            <input type="color" value={firm.accent} onChange={(e) => setFirm({ ...firm, accent: e.target.value })} className="mt-1 h-9 w-20 border rounded-md" style={{ borderColor: "var(--line)" }} />
          </div>
        </div>
      </Card>
      <Card title="Firm activities — service catalog" sub="Drives proposal requests. Custom services typed by managers are flagged here as catalog candidates for you to promote.">
        <CatalogEditor firm={firm} setFirm={setFirm} />
      </Card>
      <Card title="Document formats on file" sub="Optional uploaded templates from deployment — generated documents follow these; otherwise Baton's standard layout applies.">
        {[["letterhead", "Letterhead"], ["proposal", "Proposal format"], ["el", "Engagement letter format"]].map(([k, label]) => (
          <div key={k} className="flex items-center gap-3 text-sm py-1.5">
            <span className="w-48" style={{ color: "var(--mut)" }}>{label}</span>
            {firm.templates?.[k] ? (
              <span className="flex items-center gap-2 text-xs">
                <FileLink {...firm.templates[k]} />
                <button onClick={() => setFirm({ ...firm, templates: { ...firm.templates, [k]: null } })} className="underline" style={{ color: "var(--red)" }}>remove</button>
              </span>
            ) : (
              <FilePick small label={`Upload ${label.toLowerCase()}`} onFiles={(fs) => setFirm({ ...firm, templates: { ...(firm.templates || {}), [k]: fs[0] } })} />
            )}
          </div>
        ))}
      </Card>
      <Card title="Letterhead preview" sub="Every generated proposal and engagement letter renders on this.">
        <div className="border rounded-lg overflow-hidden" style={{ borderColor: "var(--line)" }}>
          <div className="px-6 pt-5 pb-4 border-b-4 bg-white" style={{ borderColor: firm.accent }}>
            <div className="font-disp text-lg font-bold" style={{ color: "var(--ink)" }}>{firm.name}</div>
            <div className="text-[11px] mt-1" style={{ color: "var(--mut)" }}>{firm.address} · {firm.phone} · {firm.email} · {firm.trn}</div>
          </div>
          <div className="px-6 py-6 text-xs bg-white" style={{ color: "var(--line)" }}>— document body —</div>
        </div>
      </Card>
    </div>
  );
}

function Signatures({ users, sigUses, byId }) {
  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>Signature vault</h1>
      <p className="text-sm mt-1" style={{ color: "var(--mut)" }}>
        Specimens are encrypted, never downloadable, and only ever applied through the signatory's own identity-confirmed approval. Every use is logged below.
      </p>
      <div className="mt-4 space-y-3">
        {users.filter((u) => u.signatory).map((u) => {
          const uses = sigUses.filter((s) => s.by === u.id);
          return (
            <div key={u.id} className="bg-white border rounded-xl p-4" style={{ borderColor: "var(--line)" }}>
              <div className="flex items-center gap-4">
                <div className="w-28 h-14 border rounded-md flex items-center justify-center text-2xl italic font-disp select-none overflow-hidden" style={{ borderColor: "var(--line)", color: "var(--ink)", opacity: 0.65 }}>
                  {u.sigSpecimen?.type === "image" ? <img src={u.sigSpecimen.url} alt="specimen" className="max-h-12 max-w-full" /> : u.sigSpecimen?.type === "typed" ? u.sigSpecimen.text : u.name.split(" ").map((x) => x[0]).join(". ") + "."}
                </div>
                <div className="flex-1">
                  <div className="font-semibold text-sm">{u.name} <span className="font-normal text-xs" style={{ color: "var(--mut)" }}>· {u.designation}</span></div>
                  <div className="text-xs mt-0.5" style={{ color: "var(--mut)" }}>Specimen on file (low-res preview only) · identity re-confirmation required at each use</div>
                </div>
                <div className="text-xs px-2.5 py-1 rounded-full font-medium" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>{uses.length} use{uses.length !== 1 && "s"} logged</div>
              </div>
              {uses.length > 0 && (
                <div className="mt-3 pt-3 border-t space-y-1" style={{ borderColor: "var(--line)" }}>
                  {uses.map((s) => (
                    <div key={s.id} className="text-xs font-mono2 flex justify-between" style={{ color: "var(--mut)" }}>
                      <span>✍️ {s.doc}</span><span>{fmtDT(s.at)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
