/* VAT Filing Engine — SEPARATE, REMOVABLE frontend module (see REMOVING-VAT-ENGINE.md).
   All VAT UI lives in this folder. Integration surface: app-production.jsx imports
   { VatEngineNav, VatEngineScreen } and renders them in the nav + screen switch — two
   marked lines. When env VAT_ENGINE_ENABLED=false the API 404s and both render null. */

import { useEffect, useState } from "react";
import { api, openFileLink } from "../api.js";
import { useData } from "../state.jsx";

const line = { borderColor: "var(--line)" };
const mut = { color: "var(--mut)" };
const fmtD = (x) => new Date(x).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
const fmtDT = (x) => new Date(x).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
const money = (n) => `AED ${Number(n).toLocaleString("en-AE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const BUSINESS_CATEGORIES = ["Trading", "Services", "Real estate", "Used goods & vehicles", "Manufacturing",
  "Logistics & transport", "Education", "Healthcare", "Financial services", "Other"];

const STAGGER_OPTIONS = [
  ["jan_apr_jul_oct", "Quarters ending Jan / Apr / Jul / Oct"],
  ["feb_may_aug_nov", "Quarters ending Feb / May / Aug / Nov"],
  ["mar_jun_sep_dec", "Quarters ending Mar / Jun / Sep / Dec"],
  ["monthly", "Monthly tax periods"],
];

/* The practitioner interview — how a UAE VAT executive scopes a new filing client.
   Article references per card; summaries only (see the wizard footer disclaimer). */
const FLAG_QUESTIONS = [
  { key: "has_zero_rated", short: "Zero-rated (Art. 45)",
    q: "Does the client make zero-rated supplies? (Art. 45, Decree-Law)",
    what: "Exports of goods outside the UAE, international transport, certain international services, the first supply of new residential buildings (within 3 years), qualifying education/healthcare — charged at 0%. Zero-rating of goods exports is CONDITIONAL: export within 90 days with official and commercial evidence retained (customs exit + transport documents); without evidence the supply is standard-rated at 5%. Zero-rated ≠ exempt: zero-rated suppliers still recover input VAT.",
    example: "A MAINLAND Ajman trader shipping goods to India: 0% only with export evidence under the 90-day rule — otherwise 5%." },
  { key: "has_exempt", short: "Exempt (Art. 46)",
    q: "Any exempt income? (Art. 46, Decree-Law)",
    what: "Residential rent (other than the zero-rated first supply), bare land, local passenger transport, certain margin-based financial services. Exempt suppliers cannot recover related input VAT — with mixed supplies, input VAT apportionment applies.",
    example: "A landlord letting residential flats charges no VAT on that rent — and the related input VAT is not recoverable." },
  { key: "designated_zone", short: "Designated zone",
    q: "Does the client operate in or transact with DESIGNATED zones? (Cabinet Decision listed zones — JAFZA, KIZAD, etc.)",
    what: "Certain goods movements within/between designated zones can be OUT OF SCOPE under specific conditions — distinct from both zero-rated and exempt, and distinct from ordinary free zones, which follow normal VAT rules.",
    example: "Goods moved between two designated-zone companies without entering the mainland may fall outside the scope of UAE VAT." },
  { key: "margin_scheme", short: "Margin scheme (Art. 29 ER)",
    q: "Used goods / second-hand vehicles / antiques dealing? (Art. 29, Executive Regulations)",
    what: "Dealers may account for VAT on the profit margin (sale price minus purchase price), not the full sale value. Eligibility conditions apply: the goods were previously subject to VAT and were purchased from non-registrants or under the scheme.",
    example: "A used car bought from a private individual for AED 40,000, sold for AED 45,000 → VAT on the AED 5,000 margin — if eligibility holds." },
  { key: "rcm_imports", short: "Reverse charge (Art. 48)",
    q: "Imports of goods or services from abroad? (Art. 48, Decree-Law)",
    what: "The buyer self-assesses the VAT (reverse charge) — output VAT via the Box 3/6 mechanics and recoverable input VAT as applicable. Import VAT on goods flows through the customs-linked TRN.",
    example: "Software licences bought from a US vendor: the client reports the 5% itself instead of the supplier." },
  { key: "blocked_input_risk", short: "Blocked input",
    q: "Any blocked input categories in the client's spend?",
    what: "Some input VAT is never recoverable — entertainment expenses, and motor vehicles available for personal use. Claiming it exposes the client to assessments and penalties.",
    example: "VAT on a staff iftar dinner or on a company car the owner also drives privately: not recoverable." },
  { key: "open_fta_matters", short: "FTA history",
    q: "Any open FTA matters — voluntary disclosures, penalties, clarifications?",
    what: "Open FTA matters change how conservatively the return should be prepared. Also record: who did the prior filings, and are prior-period working papers available?",
    example: "e.g. A pending voluntary disclosure on Q4 last year, or a reconsideration request on a late-registration penalty." },
];

const WIZARD_DISCLAIMER = "Reference summaries only — verify against the Decree-Law and Executive Regulations as amended.";

const STAGES = [
  ["ledgers_pending", "1 · Ledger"],
  ["invoices_pending", "2 · Invoices"],
  ["reconciled", "3 · Reconcile"],
  ["computation_draft", "4 · Computation"],
  ["awaiting_client_approval", "5 · Client approval"],
  ["ready_to_file", "6 · File at FTA"],
  ["complete", "✓ Complete"],
];

/* ---------- enabled probe (module-scope cache; 404 → module hides itself) ---------- */

let ENABLED = null;
function useVatEnabled() {
  const [en, setEn] = useState(ENABLED);
  useEffect(() => {
    if (ENABLED === null) {
      api.get("/vat-engine/status")
        .then(() => { ENABLED = true; setEn(true); })
        .catch(() => { ENABLED = false; setEn(false); });
    }
  }, []);
  return en;
}

export function VatEngineNav({ active, onClick }) {
  const en = useVatEnabled();
  const { me } = useData();
  if (!en || me.role === "Accountant") return null;
  return (
    <button onClick={onClick} className="w-full text-left px-3 py-2 rounded-md transition-colors"
      style={active ? { background: "rgba(255,255,255,.12)", color: "#fff", fontWeight: 600 } : { color: "#A9BACB" }}>
      VAT filing engine
    </button>
  );
}

/* ---------- shared bits ---------- */

async function downloadTemplate(path, filename) {
  const blob = await api.getBlob(path);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function Btn({ children, onClick, disabled, tone = "accent" }) {
  const bg = { accent: "var(--accent)", amber: "var(--amber)", ink: "var(--ink)" }[tone];
  return (
    <button disabled={disabled} onClick={onClick}
      className="px-3 py-1.5 rounded-md text-white text-xs font-semibold disabled:opacity-40"
      style={{ background: bg }}>{children}</button>
  );
}

function GhostBtn({ children, onClick }) {
  return <button onClick={onClick} className="px-2.5 py-1.5 rounded-md border text-xs" style={{ ...line, ...mut }}>{children}</button>;
}

function Section({ title, sub, children }) {
  return (
    <section className="mt-4 bg-white border rounded-xl p-4" style={line}>
      <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>{title}</h3>
      {sub && <p className="text-xs mt-0.5" style={mut}>{sub}</p>}
      <div className="mt-2.5">{children}</div>
    </section>
  );
}

function EmailModal({ initial, onSend, onClose, sendLabel = "Send email" }) {
  const [to, setTo] = useState(initial.to || "");
  const [subject, setSubject] = useState(initial.subject || "");
  const [body, setBody] = useState(initial.body || "");
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-40" onClick={onClose}>
      <div className="bg-white rounded-xl border p-4 w-[560px] max-w-[92vw]" style={line} onClick={(e) => e.stopPropagation()}>
        <div className="font-disp font-bold text-sm mb-2" style={{ color: "var(--ink)" }}>{initial.title}</div>
        <label className="text-[11px] font-semibold" style={mut}>To</label>
        <input value={to} onChange={(e) => setTo(e.target.value)} className="w-full border rounded-md px-2.5 py-1.5 text-xs mb-2" style={line} />
        <label className="text-[11px] font-semibold" style={mut}>Subject</label>
        <input value={subject} onChange={(e) => setSubject(e.target.value)} className="w-full border rounded-md px-2.5 py-1.5 text-xs mb-2" style={line} />
        <label className="text-[11px] font-semibold" style={mut}>Body {initial.attachment && <span className="font-normal">— {initial.attachment} attached automatically</span>}</label>
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={7} className="w-full border rounded-md px-2.5 py-1.5 text-xs mb-3 font-mono2" style={line} />
        <div className="flex gap-2 justify-end">
          <GhostBtn onClick={onClose}>Cancel</GhostBtn>
          <Btn disabled={!to.trim() || !subject.trim() || !body.trim()} onClick={() => onSend({ to: to.trim(), subject: subject.trim(), body })}>{sendLabel}</Btn>
        </div>
      </div>
    </div>
  );
}

function RequestLog({ f, kind, onResend }) {
  const reqs = (f.client_requests || []).filter((r) => r.kind === kind);
  if (reqs.length === 0) return null;
  const last = reqs[reqs.length - 1];
  return (
    <div className="mt-2 text-[11px] flex items-center gap-2 flex-wrap" style={mut}>
      <span>✉️ Requested from client {reqs.length}× — last sent {fmtDT(last.sent_at)} to {last.to}</span>
      <button onClick={onResend} className="underline" style={{ color: "var(--amber)" }}>Resend</button>
    </div>
  );
}

/* ---------- the screen ---------- */

export function VatEngineScreen() {
  const en = useVatEnabled();
  // NB: byId is NOT in the DataProvider context (it's local to the App component) —
  // derive it here from `users`, which the context does provide.
  const { me, users, duties, pushToast, refetchAll } = useData();
  const byId = (id) => users.find((u) => u.id === id) || { id, name: "—", role: "" };
  const [filing, setFiling] = useState(null);
  const [busy, setBusy] = useState(false);
  if (!en) return null;

  const vatDuties = duties.filter((d) => d.kind === "vat" && !d.closed);
  const run = async (fn, { refetch = false } = {}) => {
    setBusy(true);
    try {
      const out = await fn();
      if (out?.filing) setFiling(out.filing);
      else if (out?.id) setFiling(out);
      if (refetch) refetchAll();
      return out;
    } catch (e) {
      const errors = e.detail?.errors;
      pushToast(`⚠️ ${e.message}${errors?.length ? ` — ${errors[0]}${errors.length > 1 ? ` (+${errors.length - 1} more)` : ""}` : ""}`);
      if (errors?.length) console.warn("VAT engine row errors:", errors);
      throw e;
    } finally {
      setBusy(false);
    }
  };

  if (filing) {
    return <FilingView f={filing} me={me} byId={byId} run={run} busy={busy} pushToast={pushToast}
                       back={() => { setFiling(null); refetchAll(); }} />;
  }
  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>VAT filing engine</h1>
      <p className="text-sm mt-1" style={mut}>Ledger → invoices → reconciliation → computation → client approval → filed at FTA. Completing a filing completes the linked duty; the schedule rolls forward automatically.</p>
      <div className="mt-4 grid grid-cols-2 gap-3">
        {vatDuties.map((d) => (
          <div key={d.id} className="bg-white border rounded-xl p-4" style={line}>
            <div className="font-semibold text-sm">{d.client}</div>
            <div className="text-xs mt-0.5" style={mut}>{d.service} · {d.cadence} · staff {byId(d.staffId).name}</div>
            <div className="text-xs mt-1 font-mono2" style={mut}>next due {fmtD(d.nextDue)}</div>
            <div className="mt-2.5">
              <Btn onClick={() => run(() => api.post("/vat-engine/filings/open", { duty_id: d.id }))}>
                Open period →
              </Btn>
            </div>
          </div>
        ))}
        {vatDuties.length === 0 && <div className="text-sm col-span-2" style={mut}>No open VAT duties{me.role === "Staff" ? " assigned to you" : ""} — VAT filings run on duties of kind "vat".</div>}
      </div>
    </div>
  );
}

function YesNoUnsure({ value, onPick }) {
  return (
    <div className="mt-3 flex gap-2">
      {[["yes", "Yes"], ["no", "No"], ["not_sure", "Not sure"]].map(([v, l]) => (
        <button key={v} onClick={() => onPick(v)}
          className="px-4 py-2 rounded-lg text-sm font-semibold border"
          style={value === v
            ? { background: v === "not_sure" ? "var(--amber)" : "var(--accent)", color: "#fff", borderColor: "transparent" }
            : { ...line, ...mut }}>{l}</button>
      ))}
    </div>
  );
}

/* The practitioner interview — one question per card, article-referenced explainer +
   concrete UAE example. Every answer (including "Not sure") is stored and versioned. */
function ProfileWizard({ clientId, clientName, existing, run, onDone, onCancel }) {
  const [step, setStep] = useState(0); // 0 registration · 1 nature · 2..N+1 flags · last review
  const [trn, setTrn] = useState(() => {
    const e = existing?.flags?.trn_confirmed;
    return { value: e?.value || null, note: e?.note || "" };
  });
  const [stagger, setStagger] = useState(existing?.tax_period_stagger || "");
  const [nature, setNature] = useState(existing?.nature_of_business || "");
  const [category, setCategory] = useState(existing?.business_category || "");
  const [fl, setFl] = useState(() => Object.fromEntries(FLAG_QUESTIONS.map((q) => {
    const e = existing?.flags?.[q.key];
    return [q.key, { value: e?.value || null, note: e?.note || "" }];
  })));
  const [otherNotes, setOtherNotes] = useState(existing?.other_notes || "");
  const last = FLAG_QUESTIONS.length + 2;
  const flag = step >= 2 && step <= FLAG_QUESTIONS.length + 1 ? FLAG_QUESTIONS[step - 2] : null;
  const canNext = step === 0 ? (!!trn.value && !!stagger)
    : step === 1 ? !!category
    : step === last ? true
    : !!fl[flag.key].value;

  const save = () => run(() => api[existing ? "patch" : "post"](`/vat-engine/clients/${clientId}/profile`, {
    nature_of_business: nature.trim(),
    business_category: category,
    tax_period_stagger: stagger || null,
    flags: {
      trn_confirmed: { value: trn.value || "no", note: trn.note.trim() || null },
      ...Object.fromEntries(Object.entries(fl).map(([k, v]) => [k, { value: v.value || "no", note: v.note.trim() || null }])),
    },
    other_notes: otherNotes.trim() || null,
  })).then(onDone).catch(() => {});

  const noteInput = (v, set) => (
    <input value={v.note} onChange={(e) => set({ ...v, note: e.target.value })}
      placeholder="Optional note — specifics worth remembering" className="mt-2.5 w-full border rounded-md px-2.5 py-1.5 text-xs" style={line} />
  );

  return (
    <div className="max-w-2xl mx-auto">
      <button onClick={onCancel} className="text-xs font-medium mb-3" style={mut}>← Back</button>
      <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>
        {existing ? "Edit VAT profile" : "Scoping"} {clientName}
      </h1>
      <p className="text-sm mt-1" style={mut}>
        {existing ? "Every change creates a new profile version and is logged on the filing trail." :
          "First VAT filing for this client — the interview a VAT executive runs before touching a return. Every answer is stored and versioned; it drives period deadlines and the compliance checks on every future filing. \"Not sure\" is fine — it stays amber until confirmed with the client."}
      </p>
      <div className="mt-3 flex gap-1">
        {Array.from({ length: last + 1 }, (_, i) => (
          <div key={i} className="h-1 flex-1 rounded-full" style={{ background: i <= step ? "var(--accent)" : "var(--line)" }} />
        ))}
      </div>

      <div className="mt-4 bg-white border rounded-xl p-5" style={line}>
        {step === 0 && (
          <>
            <h3 className="font-disp font-bold" style={{ color: "var(--ink)" }}>Registration & tax periods</h3>
            <div className="mt-2 text-xs rounded-lg border p-2.5" style={{ ...line, background: "var(--paper)" }}>
              <b>What this means:</b> The TRN on invoices and the FTA portal must match the registration certificate. The tax period stagger drives every deadline — get it wrong and every filing lands on the wrong month.
            </div>
            <div className="mt-3 text-sm font-medium" style={{ color: "var(--ink)" }}>TRN confirmed against the registration certificate?</div>
            <YesNoUnsure value={trn.value} onPick={(v) => setTrn({ ...trn, value: v })} />
            {trn.value === "not_sure" && <div className="mt-2 text-[11px]" style={{ color: "var(--amber)" }}>Stays amber on the profile until confirmed with the client.</div>}
            {noteInput(trn, setTrn)}
            <div className="mt-4 text-sm font-medium" style={{ color: "var(--ink)" }}>Tax period stagger *</div>
            <select value={stagger} onChange={(e) => setStagger(e.target.value)} className="mt-1.5 border rounded-md px-2.5 py-2 text-sm w-full" style={line}>
              <option value="">— select the FTA-assigned stagger —</option>
              {STAGGER_OPTIONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </>
        )}
        {step === 1 && (
          <>
            <h3 className="font-disp font-bold" style={{ color: "var(--ink)" }}>Nature of supplies</h3>
            <label className="block text-xs font-semibold mt-3" style={mut}>Describe the main revenue streams — what does the client actually invoice for?</label>
            <input value={nature} onChange={(e) => setNature(e.target.value)} placeholder='e.g. "Wholesale of electronics; occasional exports to East Africa; some repair services"' className="mt-1 w-full border rounded-md px-3 py-2 text-sm" style={line} />
            <label className="block text-xs font-semibold mt-3" style={mut}>Category *</label>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {BUSINESS_CATEGORIES.map((c) => (
                <button key={c} onClick={() => setCategory(c)} className="px-2.5 py-1.5 rounded-full text-xs font-medium border"
                  style={category === c ? { background: "var(--accent)", color: "#fff", borderColor: "var(--accent)" } : { ...line, ...mut }}>{c}</button>
              ))}
            </div>
          </>
        )}
        {flag && (
          <>
            <h3 className="font-disp font-bold" style={{ color: "var(--ink)" }}>{flag.q}</h3>
            <div className="mt-2 text-xs rounded-lg border p-2.5" style={{ ...line, background: "var(--paper)" }}>
              <b>What this means:</b> {flag.what}
              <div className="mt-1" style={mut}>{flag.example}</div>
            </div>
            <YesNoUnsure value={fl[flag.key].value} onPick={(v) => setFl({ ...fl, [flag.key]: { ...fl[flag.key], value: v } })} />
            {fl[flag.key].value === "not_sure" && (
              <div className="mt-2 text-[11px]" style={{ color: "var(--amber)" }}>Flagged amber on the profile — confirm with the client. Treated as Yes for the compliance warnings.</div>
            )}
            {noteInput(fl[flag.key], (v) => setFl({ ...fl, [flag.key]: v }))}
          </>
        )}
        {step === last && (
          <>
            <h3 className="font-disp font-bold" style={{ color: "var(--ink)" }}>Anything else worth remembering?</h3>
            <textarea value={otherNotes} onChange={(e) => setOtherNotes(e.target.value)} rows={3} placeholder="Other notes (optional)" className="mt-2 w-full border rounded-md px-2.5 py-1.5 text-xs" style={line} />
            <div className="mt-3 text-xs font-semibold" style={mut}>Profile summary</div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              <span className="px-2 py-1 rounded-full text-[11px] font-medium border" style={line}>{category}{nature.trim() ? ` — ${nature.trim()}` : ""}</span>
              {stagger && <span className="px-2 py-1 rounded-full text-[11px] font-medium border" style={line}>⏱ {STAGGER_OPTIONS.find(([k]) => k === stagger)?.[1]}</span>}
              {[{ key: "trn_confirmed", short: "TRN confirmed", v: trn }, ...FLAG_QUESTIONS.map((q) => ({ key: q.key, short: q.short, v: fl[q.key] }))]
                .filter((x) => x.v.value && x.v.value !== "no")
                .map((x) => (
                  <span key={x.key} className="px-2 py-1 rounded-full text-[11px] font-bold"
                    style={x.v.value === "yes" ? { background: "var(--accent-soft)", color: "var(--accent)" } : { background: "var(--amber-soft)", color: "var(--amber)" }}>
                    {x.short}{x.v.value === "not_sure" ? " — confirm with client" : ""}
                  </span>
                ))}
            </div>
          </>
        )}
        <div className="mt-4 flex justify-between">
          <button onClick={() => setStep(Math.max(0, step - 1))} disabled={step === 0} className="text-xs underline disabled:opacity-30" style={mut}>← previous</button>
          {step < last
            ? <Btn disabled={!canNext} onClick={() => setStep(step + 1)}>Next →</Btn>
            : <Btn onClick={save}>{existing ? "Save changes (new version)" : "Save profile & open the period"}</Btn>}
        </div>
      </div>
      <div className="mt-3 text-[10px] text-center" style={mut}>{WIZARD_DISCLAIMER}</div>
    </div>
  );
}

function FilingView({ f, me, byId, run, busy, pushToast, back }) {
  const [modal, setModal] = useState(null); // {type, item?}
  const [trailOpen, setTrailOpen] = useState(false);
  const [editProfile, setEditProfile] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const iAmStaff = me.id === f.staff_id;

  // first-time recognition: no VAT profile yet → the questionnaire replaces the period view
  if (f.client_id && (!f.profile || editProfile)) {
    return (
      <ProfileWizard clientId={f.client_id} clientName={f.client_name}
        existing={editProfile ? f.profile : null} run={run}
        onCancel={editProfile ? () => setEditProfile(false) : back}
        onDone={() => { setEditProfile(false); run(() => api.get(`/vat-engine/filings/${f.id}`)); }} />
    );
  }
  const stageIdx = STAGES.findIndex(([s]) => s === f.status);
  const contactEmail = f.client_contact?.email || "";
  const contactName = f.client_contact?.contactPerson || f.client_contact?.name || "Sir/Madam";
  const items = f.items || [];
  const diffs = items.filter((i) => ["ledger_only", "invoice_only"].includes(i.bucket));
  const unresolved = diffs.filter((i) => !["excluded", "resolved"].includes(i.resolution?.action));

  const uploadLedger = (file) => {
    const fd = new FormData();
    fd.append("file", file, file.name);
    run(() => api.postForm(`/vat-engine/filings/${f.id}/ledger`, fd));
  };
  const uploadInvoices = (file, pdfs) => {
    const fd = new FormData();
    fd.append("file", file, file.name);
    for (const p of pdfs) fd.append("evidence", p, p.name);
    run(() => api.postForm(`/vat-engine/filings/${f.id}/invoices`, fd));
  };

  const mailDefaults = {
    ledger: {
      title: "Request the VAT ledger from the client", attachment: "VAT Ledger Template.xlsx",
      to: contactEmail, subject: `VAT ledger required — ${f.client_name}, period ${f.period_label}`,
      body: `Dear ${contactName},\n\nWe are preparing your VAT return for ${f.period_label}. Kindly fill the attached VAT Ledger template with all sales (Output) and purchase (Input) invoices for the period and return it to us.\n\nBest regards,\n${byId(f.staff_id).name}`,
    },
    invoices: {
      title: "Request the invoice register from the client", attachment: "Invoice Register Template.xlsx",
      to: contactEmail, subject: `Invoice register required — ${f.client_name}, period ${f.period_label}`,
      body: `Dear ${contactName},\n\nTo reconcile your VAT return for ${f.period_label}, kindly fill the attached Invoice Register template with every invoice issued in the period and return it to us.\n\nBest regards,\n${byId(f.staff_id).name}`,
    },
  };

  return (
    <div className="max-w-4xl mx-auto">
      <button onClick={back} className="text-xs font-medium mb-3" style={mut}>← Back to VAT duties</button>
      <h1 className="font-disp text-2xl font-bold tracking-tight" style={{ color: "var(--ink)" }}>
        {f.client_name} {f.client_ref && <span className="font-mono2 text-base" style={{ color: "var(--accent)" }}>· {f.client_ref}</span>}
      </h1>
      <div className="text-sm mt-1 flex items-center gap-2 flex-wrap" style={mut}>
        <span className="font-medium" style={{ color: "var(--ink)" }}>VAT return · {f.period_label}</span>
        <span>· staff <b style={{ color: "var(--ink)" }}>{f.staff_name}</b> (holder)</span>
        <span>· duty due <span className="font-mono2">{fmtD(f.duty_next_due)}</span></span>
      </div>

      {/* profile summary bar — the engine's memory, applied to every period */}
      {f.profile && (
        <>
          <div className="mt-3 flex items-center gap-1.5 flex-wrap text-[11px]">
            <span className="px-2 py-1 rounded-full border font-medium" style={{ ...line, ...mut }}>
              🧾 {f.profile.business_category}{f.profile.nature_of_business ? ` — ${f.profile.nature_of_business}` : ""} · profile v{f.profile.version}
            </span>
            {f.profile.tax_period_stagger_label && (
              <span className="px-2 py-1 rounded-full border font-medium" style={{ ...line, ...mut }}>⏱ {f.profile.tax_period_stagger_label}</span>
            )}
            {[{ key: "trn_confirmed", short: "TRN confirmed" }, ...FLAG_QUESTIONS].map((q) => {
              const v = f.profile.flags?.[q.key]?.value;
              if (!v || v === "no") return null;
              return (
                <span key={q.key} className="px-2 py-1 rounded-full font-bold"
                  title={f.profile.flags[q.key]?.note || ""}
                  style={v === "yes" ? { background: "var(--accent-soft)", color: "var(--accent)" } : { background: "var(--amber-soft)", color: "var(--amber)" }}>
                  {q.short}{v === "not_sure" ? " — confirm with client" : ""}
                </span>
              );
            })}
            <button onClick={() => setEditProfile(true)} className="underline" style={mut}>Edit profile</button>
            <button onClick={() => setHistoryOpen(!historyOpen)} className="underline" style={mut}>
              History ({(f.profile.updated || []).length}) {historyOpen ? "▾" : "▸"}
            </button>
          </div>
          {historyOpen && (
            <div className="mt-2 rounded-lg border p-3 text-xs space-y-2" style={{ ...line, background: "var(--paper)" }}>
              {[...(f.profile.updated || [])].reverse().map((v, i) => (
                <div key={i}>
                  <div className="font-semibold" style={{ color: "var(--ink)" }}>
                    v{v.version} · {v.by_name} · {fmtDT(v.at)}
                  </div>
                  {(v.changes || []).map((c, j) => (
                    <div key={j} className="pl-3" style={mut}>
                      {c.field}: {c.old ?? "—"} → <b style={{ color: "var(--ink)" }}>{c.new ?? "—"}</b>
                      {c.note && <span> — "{c.note}"</span>}
                    </div>
                  ))}
                </div>
              ))}
              {(f.profile.updated || []).length === 0 && <div style={mut}>No history recorded.</div>}
            </div>
          )}
        </>
      )}

      {/* stage tracker */}
      <div className="mt-4 flex gap-1 flex-wrap">
        {STAGES.map(([s, label], i) => (
          <span key={s} className="text-[11px] px-2.5 py-1 rounded-full font-semibold"
            style={i < stageIdx ? { background: "var(--accent-soft)", color: "var(--accent)" }
              : i === stageIdx ? { background: "var(--accent)", color: "#fff" }
              : { background: "var(--paper)", color: "var(--mut)", border: "1px solid var(--line)" }}>
            {i < stageIdx ? "✓ " : ""}{label}
          </span>
        ))}
      </div>

      {f.status === "complete" && (
        <div className="mt-4 rounded-lg border p-3.5" style={{ background: "var(--accent-soft)", borderColor: "var(--accent)" }}>
          <div className="text-sm font-bold" style={{ color: "var(--accent)" }}>🔒 FILING COMPLETE — trail sealed</div>
          <div className="text-xs mt-1" style={{ color: "var(--ink)" }}>
            {f.computation?.period}: net {money(f.computation?.net)} {f.computation?.position?.toUpperCase()} — filed at the FTA
            {f.completed_at && <> on {fmtD(f.completed_at)}</>}. The linked duty is completed (method: proof) and the schedule
            rolled forward to <b className="font-mono2">{fmtD(f.duty_next_due)}</b>. The reconciliation workbook, computation PDF
            and FTA acknowledgement live on the client's document registry.
          </div>
        </div>
      )}

      {/* stage 1 — ledger */}
      {f.status === "ledgers_pending" && iAmStaff && (
        <Section title="Stage 1 · Collect the VAT ledger"
                 sub="One template covers two-ledger and single-ledger clients — each row carries Type = Output (sales) or Input (purchases). Uploads are validated hard against the template.">
          <div className="flex gap-2 items-center flex-wrap">
            <GhostBtn onClick={() => downloadTemplate("/vat-engine/templates/ledger", "VAT Ledger Template.xlsx")}>⬇ Download ledger template</GhostBtn>
            <label className="px-3 py-1.5 rounded-md text-white text-xs font-semibold cursor-pointer" style={{ background: "var(--accent)" }}>
              Upload filled ledger
              <input type="file" accept=".xlsx" className="hidden" onChange={(e) => e.target.files[0] && uploadLedger(e.target.files[0])} />
            </label>
            <GhostBtn onClick={() => setModal({ type: "ledger" })}>✉️ Request from client…</GhostBtn>
          </div>
          <RequestLog f={f} kind="ledger" onResend={() => setModal({ type: "ledger" })} />
        </Section>
      )}

      {/* stage 2 — invoices */}
      {f.status === "invoices_pending" && iAmStaff && (
        <Section title="Stage 2 · Collect the invoice register"
                 sub="Reconciliation runs automatically the moment the register uploads. Invoice PDFs are optional evidence.">
          <div className="text-xs mb-2" style={mut}>Ledger on file: <b style={{ color: "var(--ink)" }}>{f.ledger_file?.name}</b> — {f.ledger_file?.rows} rows (re-upload above replaces it: use the ledger upload again from this screen).</div>
          <InvoiceUpload onUpload={uploadInvoices} />
          <div className="flex gap-2 items-center flex-wrap mt-2">
            <GhostBtn onClick={() => downloadTemplate("/vat-engine/templates/invoice-register", "Invoice Register Template.xlsx")}>⬇ Download register template</GhostBtn>
            <GhostBtn onClick={() => setModal({ type: "invoices" })}>✉️ Request from client…</GhostBtn>
            <label className="px-2.5 py-1.5 rounded-md border text-xs cursor-pointer" style={{ ...line, ...mut }}>
              Re-upload ledger
              <input type="file" accept=".xlsx" className="hidden" onChange={(e) => e.target.files[0] && uploadLedger(e.target.files[0])} />
            </label>
          </div>
          <RequestLog f={f} kind="invoices" onResend={() => setModal({ type: "invoices" })} />
        </Section>
      )}

      {/* stage 3 — reconciliation */}
      {f.status === "reconciled" && (
        <Section title="Stage 3 · Reconciliation"
                 sub={`Match key: invoice number (normalized) + VAT amount ±0.01 — dates are never used for matching. Window rule: invoices dated before ${fmtD(f.prev_period_start)} are out of window (VAT rule).`}>
          <div className="flex gap-3 flex-wrap text-xs mb-3">
            {[["matched", "Matched", "var(--accent)"], ["ledger_only", "In ledger, not in invoices", "var(--amber)"],
              ["invoice_only", "In invoices, not in ledger", "var(--amber)"], ["out_of_window", "Out of window (VAT rule)", "var(--mut)"]].map(([k, label, color]) => (
              <span key={k} className="px-2.5 py-1.5 rounded-lg border font-medium" style={{ ...line, color }}>
                {label}: <b className="font-mono2">{f.recon?.[k] ?? 0}</b>
              </span>
            ))}
            {f.recon?.excel_file_id && (
              <button onClick={() => openFileLink(f.recon.excel_file_id)} className="underline text-xs" style={{ color: "var(--accent)" }}>
                ⬇ {f.recon.excel_name}
              </button>
            )}
          </div>
          {diffs.length > 0 && (
            <div className="space-y-1.5 mb-3">
              {diffs.map((i) => (
                <div key={i.id} className="border rounded-md px-3 py-2 text-xs flex items-center gap-3 flex-wrap" style={{ ...line, background: ["excluded", "resolved"].includes(i.resolution?.action) ? "var(--paper)" : "#FFF8EC" }}>
                  <span className="font-mono2 font-semibold">{i.invoice_no}</span>
                  <span className="flex-1">{i.party} · {i.emirate} · VAT {money(i.vat)}</span>
                  <span className="text-[10px] uppercase font-bold" style={{ color: "var(--amber)" }}>{i.bucket === "ledger_only" ? "in ledger, not in invoices" : "in invoices, not in ledger"}</span>
                  {i.resolution?.action === "excluded" && <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold" style={{ background: "var(--paper)", color: "var(--mut)", border: "1px solid var(--line)" }}>excluded — {i.resolution.reason}</span>}
                  {i.resolution?.action === "resolved" && <span className="text-[10px] font-bold" style={{ color: "var(--accent)" }}>✓ resolved</span>}
                  {i.resolution?.action === "requested" && <span className="text-[10px] font-bold" style={{ color: "var(--amber)" }}>requested from client</span>}
                  {iAmStaff && !["excluded", "resolved"].includes(i.resolution?.action) && (
                    <span className="flex gap-2">
                      <button onClick={() => setModal({ type: "missing_invoice", item: i })} className="underline" style={{ color: "var(--amber)" }}>request invoice</button>
                      {i.resolution?.action === "requested" && (
                        <button onClick={() => run(() => api.post(`/vat-engine/filings/${f.id}/items/${i.id}/resolve`))} className="underline" style={{ color: "var(--accent)" }}>mark resolved</button>
                      )}
                      <button onClick={() => {
                        const reason = window.prompt("Exclude from this filing — mandatory reason:");
                        if (reason?.trim()) run(() => api.post(`/vat-engine/filings/${f.id}/items/${i.id}/exclude`, { reason: reason.trim() }));
                      }} className="underline" style={mut}>exclude…</button>
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
          {iAmStaff && (
            <div className="flex items-center gap-3">
              <Btn disabled={busy || unresolved.length > 0} onClick={() => run(() => api.post(`/vat-engine/filings/${f.id}/draft-computation`))}>
                Draft the computation →
              </Btn>
              {unresolved.length > 0 && <span className="text-xs" style={{ color: "var(--amber)" }}>{unresolved.length} difference(s) must be resolved, requested→resolved, or excluded first.</span>}
            </div>
          )}
        </Section>
      )}

      {/* stage 4 — computation */}
      {["computation_draft", "awaiting_client_approval", "ready_to_file", "complete"].includes(f.status) && f.computation && (
        <Section title={`Stage 4 · Computation — ${f.computation.period}`}
                 sub={`Auto-drafted from the included ledger rows${f.computation.profile_version ? `, profile v${f.computation.profile_version} pre-applied` : ""}.`}>
          <Vat201 c={f.computation} />
          <ChecksPanel f={f} run={run} busy={busy} editable={f.status === "computation_draft" && iAmStaff} />
        </Section>
      )}

      {/* stage 5 — client approval */}
      {f.status === "awaiting_client_approval" && iAmStaff && (
        <Section title="Stage 5 · Client approval"
                 sub="Email the computation (clean PDF attached), then record the client's approval — evidence upload, or declared with a basis and mandatory note.">
          <div className="flex gap-2 items-center flex-wrap mb-3">
            <Btn tone="amber" onClick={() => setModal({ type: "computation" })}>✉️ Email computation to client…</Btn>
            {f.computation?.pdf_file_id && <button onClick={() => openFileLink(f.computation.pdf_file_id)} className="underline text-xs" style={{ color: "var(--accent)" }}>⬇ {f.computation.pdf_name}</button>}
          </div>
          <RequestLog f={f} kind="computation" onResend={() => setModal({ type: "computation" })} />
          <ApprovalForm onSubmit={(fd) => run(() => api.postForm(`/vat-engine/filings/${f.id}/client-approval`, fd))} busy={busy} />
        </Section>
      )}

      {/* stage 6 — file at FTA */}
      {f.status === "ready_to_file" && iAmStaff && (
        <Section title="Stage 6 · File at the FTA"
                 sub="Upload the FTA acknowledgement (required). This completes the linked duty with method=proof and a record pre-filled from the computation — the schedule rolls forward automatically.">
          <div className="text-xs mb-2" style={mut}>Client approval on record: {f.client_approval?.label}{f.client_approval?.note ? ` — "${f.client_approval.note}"` : ""}</div>
          <FtaForm onSubmit={(fd) => run(() => api.postForm(`/vat-engine/filings/${f.id}/file-at-fta`, fd), { refetch: true })} busy={busy} />
        </Section>
      )}

      {/* trail */}
      <section className="mt-4 bg-white border rounded-xl p-4" style={line}>
        <button onClick={() => setTrailOpen(!trailOpen)} className="w-full text-left flex items-center justify-between">
          <h3 className="font-disp font-bold text-sm" style={{ color: "var(--ink)" }}>Trail ({(f.events || []).length})</h3>
          <span style={mut}>{trailOpen ? "▾" : "▸"}</span>
        </button>
        {trailOpen && (f.events || []).map((e, i) => (
          <div key={i} className="text-xs py-1.5 border-b last:border-0" style={line}>
            <span className="font-mono2" style={mut}>{fmtDT(e.at)} · {e.by ? byId(e.by).name : "SYSTEM"}</span> — {e.text}
          </div>
        ))}
      </section>

      {modal && ["ledger", "invoices"].includes(modal.type) && (
        <EmailModal initial={mailDefaults[modal.type]} onClose={() => setModal(null)}
          onSend={(mail) => { run(() => api.post(`/vat-engine/filings/${f.id}/request-from-client`, { ...mail, kind: modal.type })).then(() => setModal(null)).catch(() => {}); }} />
      )}
      {modal?.type === "missing_invoice" && (
        <EmailModal onClose={() => setModal(null)}
          initial={{
            title: `Request invoice ${modal.item.invoice_no} from the client`, to: contactEmail,
            subject: `Missing invoice ${modal.item.invoice_no} — VAT return ${f.period_label}`,
            body: `Dear ${contactName},\n\nWhile reconciling your VAT return for ${f.period_label} we could not match invoice ${modal.item.invoice_no} (${modal.item.party}, VAT ${money(modal.item.vat)}). Kindly send us a copy of this invoice or confirm its details.\n\nBest regards,\n${byId(f.staff_id).name}`,
          }}
          onSend={(mail) => { run(() => api.post(`/vat-engine/filings/${f.id}/items/${modal.item.id}/request-invoice`, mail)).then(() => setModal(null)).catch(() => {}); }} />
      )}
      {modal?.type === "computation" && (
        <EmailModal onClose={() => setModal(null)}
          initial={{
            title: "Email the computation to the client", attachment: f.computation?.pdf_name, to: contactEmail,
            subject: `VAT return computation for your approval — ${f.period_label}`,
            body: `Dear ${contactName},\n\nPlease find attached the VAT return computation for ${f.period_label}: net ${money(f.computation?.net)} ${f.computation?.position}. Kindly review and reply with your approval so we can file at the FTA before the deadline.\n\nBest regards,\n${byId(f.staff_id).name}`,
          }}
          onSend={(mail) => { run(() => api.post(`/vat-engine/filings/${f.id}/send-computation`, mail)).then(() => setModal(null)).catch(() => {}); }} />
      )}
    </div>
  );
}

function InvoiceUpload({ onUpload }) {
  const [register, setRegister] = useState(null);
  const [pdfs, setPdfs] = useState([]);
  return (
    <div className="flex gap-2 items-center flex-wrap">
      <label className="px-2.5 py-1.5 rounded-md border text-xs cursor-pointer" style={{ borderColor: "var(--line)" }}>
        {register ? `📄 ${register.name}` : "Pick filled register (.xlsx)"}
        <input type="file" accept=".xlsx" className="hidden" onChange={(e) => setRegister(e.target.files[0] || null)} />
      </label>
      <label className="px-2.5 py-1.5 rounded-md border text-xs cursor-pointer" style={{ borderColor: "var(--line)", color: "var(--mut)" }}>
        {pdfs.length ? `${pdfs.length} invoice PDF(s)` : "+ invoice PDFs (optional)"}
        <input type="file" accept=".pdf" multiple className="hidden" onChange={(e) => setPdfs([...pdfs, ...e.target.files])} />
      </label>
      <Btn disabled={!register} onClick={() => onUpload(register, pdfs)}>Upload → auto-reconcile</Btn>
    </div>
  );
}

function Vat201({ c }) {
  const zr = c.zero_rated || { sales: 0, rows: 0 };
  const ex = c.exempt || { sales: 0, rows: 0 };
  const mg = c.margin || { sales: 0, output_vat: 0, rows: 0 };
  const rcm = c.rcm || { output_vat: 0, input_vat: 0, rows: 0 };
  const oos = c.out_of_scope || { sales: 0, rows: 0 };
  const Row = ({ label, sub, value, extra, strong }) => (
    <div className="flex gap-4 items-baseline py-1 border-b last:border-0" style={line}>
      <span className={`flex-1 ${strong ? "font-bold" : ""}`} style={strong ? { color: "var(--ink)" } : undefined}>
        {label}{sub && <span className="text-[10px] ml-1.5" style={mut}>{sub}</span>}
      </span>
      {extra && <span className="font-mono2 text-[11px]" style={mut}>{extra}</span>}
      <b className={`font-mono2 ${strong ? "text-sm" : ""}`}>{value}</b>
    </div>
  );
  return (
    <div className="text-xs max-w-xl">
      <Row label="Standard-rated sales (5%)" sub={`${Object.values(c.per_emirate || {}).reduce((a, v) => a + v.rows, 0)} inv`} extra={`output VAT ${money(c.output_vat - mg.output_vat - rcm.output_vat)}`} value={money(c.taxable_sales)} />
      {Object.entries(c.per_emirate || {}).map(([em, v]) => (
        <div key={em} className="flex gap-4 py-0.5 pl-4 text-[11px]" style={mut}>
          <span className="flex-1">{em} · {v.rows} inv</span>
          <span className="font-mono2">VAT {money(v.output_vat)}</span>
          <span className="font-mono2">{money(v.taxable_sales)}</span>
        </div>
      ))}
      <Row label="Zero-rated sales (0%)" sub={`${zr.rows} inv — on the return, no output VAT`} value={money(zr.sales)} />
      <Row label="Exempt supplies" sub={`${ex.rows} inv — outside output VAT, input apportionment applies`} value={money(ex.sales)} />
      {mg.rows > 0 && <Row label="Margin-scheme sales" sub={`${mg.rows} inv — VAT on margin, not sale price`} extra={`margin VAT ${money(mg.output_vat)}`} value={money(mg.sales)} />}
      {rcm.rows > 0 && <Row label="RCM imports (self-assessed)" sub={`${rcm.rows} row(s)`} extra={`+${money(rcm.output_vat)} output / +${money(rcm.input_vat)} input`} value="—" />}
      {oos.rows > 0 && <Row label="Out of scope (designated zone)" sub={`${oos.rows} inv — outside the return boxes, listed for completeness`} value={money(oos.sales)} />}
      <div className="mt-2" />
      <Row label="Output VAT (total)" value={money(c.output_vat)} />
      <Row label="Input VAT (recoverable)" value={money(c.input_vat)} />
      <div className="flex gap-4 items-baseline py-1.5">
        <span className="flex-1 font-bold" style={{ color: "var(--ink)" }}>NET VAT {c.position?.toUpperCase()}</span>
        <b className="font-mono2 text-base" style={{ color: c.position === "payable" ? "var(--red)" : "var(--accent)" }}>{money(c.net)}</b>
      </div>
      <div className="mt-1" style={mut}>
        Basis: {c.counts?.included} ledger rows included ({c.counts?.output_rows} output / {c.counts?.input_rows} input) ·
        {" "}{c.counts?.matched} matches · {c.counts?.excluded} excluded · {c.counts?.out_of_window} out of window.
      </div>
    </div>
  );
}

/* Compliance checks — profile × data. Warnings need an explicit proceed-despite-warning
   reason; confirmations are mandatory ticks. All logged with name + time server-side. */
function ChecksPanel({ f, run, busy, editable }) {
  const checks = f.computation?.checks || [];
  const warnings = checks.filter((c) => c.kind === "warning");
  const confs = checks.filter((c) => c.kind === "confirmation");
  const [ticks, setTicks] = useState({});
  const [note, setNote] = useState("");
  const ready = confs.every((c) => ticks[c.id]) && (warnings.length === 0 || note.trim());
  const confirm = () => run(() => api.post(`/vat-engine/filings/${f.id}/confirm-computation`, {
    confirmations: confs.filter((c) => ticks[c.id]).map((c) => c.id),
    warning_note: note.trim(),
  }));

  return (
    <div className="mt-4 border-t pt-3" style={line}>
      {checks.length > 0 && (
        <>
          <div className="text-[10px] uppercase tracking-wider font-bold mb-2" style={mut}>Compliance checks — from the client's VAT profile × this period's data</div>
          <div className="space-y-1.5">
            {warnings.map((c) => (
              <div key={c.id} className="rounded-lg border px-3 py-2 text-xs" style={{ background: "var(--amber-soft)", borderColor: "#E4C99A", color: "#6B5A38" }}>
                <b>⚠️ Warning:</b> {c.text}
                {c.acknowledged_by_name && <div className="mt-1 text-[10px]">Acknowledged by {c.acknowledged_by_name} · {fmtDT(c.acknowledged_at)}{f.computation.warning_note ? ` — "${f.computation.warning_note}"` : ""}</div>}
              </div>
            ))}
            {confs.map((c) => (
              <div key={c.id} className="rounded-lg border px-3 py-2 text-xs flex items-start gap-2" style={line}>
                {editable
                  ? <input type="checkbox" checked={!!ticks[c.id]} onChange={(e) => setTicks({ ...ticks, [c.id]: e.target.checked })} className="mt-0.5" />
                  : <span style={{ color: "var(--accent)" }}>✓</span>}
                <span>
                  <b>Confirm:</b> {c.text}
                  {c.ticked_by_name && <span className="block mt-1 text-[10px]" style={mut}>Ticked by {c.ticked_by_name} · {fmtDT(c.ticked_at)}</span>}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
      {editable && (
        <div className="mt-3 flex gap-2 items-center flex-wrap">
          {warnings.length > 0 && (
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Proceed despite warning — mandatory reason" className="flex-1 min-w-[260px] border rounded-md px-2.5 py-1.5 text-xs" style={line} />
          )}
          <Btn disabled={busy || !ready} onClick={confirm}>Confirm computation → send for client approval</Btn>
          {!ready && <span className="text-[11px]" style={{ color: "var(--amber)" }}>
            {confs.some((c) => !ticks[c.id]) ? "Tick every mandatory confirmation." : "A proceed-despite-warning reason is required."}
          </span>}
        </div>
      )}
    </div>
  );
}

function ApprovalForm({ onSubmit, busy }) {
  const [basis, setBasis] = useState("evidence_upload");
  const [note, setNote] = useState("");
  const [files, setFiles] = useState([]);
  const declared = basis !== "evidence_upload";
  return (
    <div className="border-t pt-3 mt-1" style={{ borderColor: "var(--line)" }}>
      <div className="text-xs font-semibold mb-1.5" style={{ color: "var(--mut)" }}>Record the client's approval</div>
      <div className="flex gap-2 items-center flex-wrap">
        <select value={basis} onChange={(e) => setBasis(e.target.value)} className="border rounded-md px-2 py-1.5 text-xs" style={{ borderColor: "var(--line)" }}>
          <option value="evidence_upload">Evidence upload (email/letter)</option>
          <option value="email_approval">Declared — email approval</option>
          <option value="message_approval">Declared — WhatsApp/SMS</option>
          <option value="verbal_instruction">Declared — verbal instruction</option>
        </select>
        <label className="px-2.5 py-1.5 rounded-md border text-xs cursor-pointer" style={{ borderColor: "var(--line)" }}>
          {files.length ? `${files.length} file(s)` : "+ approval evidence"}
          <input type="file" multiple className="hidden" onChange={(e) => setFiles([...files, ...e.target.files])} />
        </label>
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder={declared ? "Mandatory note — exactly how did the client approve?" : "Note (optional)"} className="flex-1 min-w-[220px] border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
        <Btn disabled={busy || (declared ? !note.trim() : files.length === 0)} onClick={() => {
          const fd = new FormData();
          fd.append("basis", basis);
          fd.append("note", note.trim());
          for (const x of files) fd.append("evidence", x, x.name);
          onSubmit(fd);
        }}>Record approval → ready to file</Btn>
      </div>
    </div>
  );
}

function FtaForm({ onSubmit, busy }) {
  const [ack, setAck] = useState([]);
  const [note, setNote] = useState("");
  return (
    <div className="flex gap-2 items-center flex-wrap">
      <label className="px-2.5 py-1.5 rounded-md border text-xs cursor-pointer" style={{ borderColor: "var(--line)" }}>
        {ack.length ? `${ack.length} acknowledgement file(s)` : "+ FTA acknowledgement (required)"}
        <input type="file" multiple className="hidden" onChange={(e) => setAck([...ack, ...e.target.files])} />
      </label>
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" className="flex-1 min-w-[200px] border rounded-md px-2.5 py-1.5 text-xs" style={{ borderColor: "var(--line)" }} />
      <Btn disabled={busy || ack.length === 0} onClick={() => {
        const fd = new FormData();
        fd.append("note", note.trim());
        for (const a of ack) fd.append("acknowledgement", a, a.name);
        onSubmit(fd);
      }}>Filed at FTA → complete filing & duty</Btn>
    </div>
  );
}
