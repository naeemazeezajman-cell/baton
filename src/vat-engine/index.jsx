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

function FilingView({ f, me, byId, run, busy, pushToast, back }) {
  const [modal, setModal] = useState(null); // {type, item?}
  const [trailOpen, setTrailOpen] = useState(false);
  const iAmStaff = me.id === f.staff_id;
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
                 sub="Auto-drafted from the included ledger rows.">
          <ComputationTable c={f.computation} />
          {f.status === "computation_draft" && iAmStaff && (
            <div className="mt-3"><Btn disabled={busy} onClick={() => run(() => api.post(`/vat-engine/filings/${f.id}/confirm-computation`))}>Confirm computation → send for client approval</Btn></div>
          )}
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

function ComputationTable({ c }) {
  return (
    <div className="text-xs">
      <div className="grid grid-cols-2 gap-x-6 gap-y-1 max-w-md">
        <span style={{ color: "var(--mut)" }}>Taxable sales (net)</span><b className="font-mono2 text-right">{money(c.taxable_sales)}</b>
        <span style={{ color: "var(--mut)" }}>Output VAT</span><b className="font-mono2 text-right">{money(c.output_vat)}</b>
        <span style={{ color: "var(--mut)" }}>Input VAT (recoverable)</span><b className="font-mono2 text-right">{money(c.input_vat)}</b>
        <span className="font-bold" style={{ color: "var(--ink)" }}>NET {c.position?.toUpperCase()}</span>
        <b className="font-mono2 text-right text-sm" style={{ color: c.position === "payable" ? "var(--red)" : "var(--accent)" }}>{money(c.net)}</b>
      </div>
      <div className="mt-3 text-[10px] uppercase tracking-wider font-bold" style={{ color: "var(--mut)" }}>Taxable sales per emirate</div>
      {Object.entries(c.per_emirate || {}).map(([em, v]) => (
        <div key={em} className="flex gap-4 py-0.5 border-b last:border-0 max-w-md" style={{ borderColor: "var(--line)" }}>
          <span className="flex-1">{em}</span>
          <span className="font-mono2">{money(v.taxable_sales)}</span>
          <span className="font-mono2" style={{ color: "var(--mut)" }}>VAT {money(v.output_vat)} · {v.rows} inv</span>
        </div>
      ))}
      <div className="mt-2" style={{ color: "var(--mut)" }}>
        Basis: {c.counts?.included} ledger rows included ({c.counts?.output_rows} output / {c.counts?.input_rows} input) ·
        {" "}{c.counts?.matched} matches · {c.counts?.excluded} excluded · {c.counts?.out_of_window} out of window.
      </div>
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
