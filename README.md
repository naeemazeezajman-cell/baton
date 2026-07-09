# Baton

**CRM & employee performance tracking for bookkeeping and tax firms.**

Client work is a relay: at any moment, exactly one person holds the baton. Baton makes every handoff visible, timed, and attributable — because firms don't miss statutory deadlines when people work slowly; they miss them when work sits *between* people with nobody accountable.

> Status: working interactive prototype (single-file React). Module 1 (Org & Users), the full Client Onboarding Part 1 chain, and the Statutory Deadline Engine are implemented. Production build (FastAPI + PostgreSQL multi-tenant backend) is the next phase.

---

## The problem

UAE bookkeeping and tax firms (typically 100–200 clients, 30–50 technical staff) run on informal handoffs. The result:

1. **Missed statutory deadlines** (VAT, Corporate Tax, ESR) with real penalties.
2. **No attribution** — when something is late, nobody can show *which desk it sat on, and for how long*.
3. **Client document chaos** — requirements collected by memory, nothing enforced.

## The core mechanic: single-holder accountability

- Every matter has **exactly one live holder**. Time is logged per holder ("holder log").
- Dashboards are **aging-sorted**: whoever holds something oldest sees it first, every day, until they act.
- **No internal deadlines** — instead, relentless visibility: daily reminder banners, red timers, "who holds the baton?" boards for managers.
- On module completion the process **audit trail closes** and a **performance report** is generated: total cycle time, per-employee ★ ratings from average holding time, and per-person task drill-downs sorted longest-first.

## What's implemented (prototype)

### Client Onboarding — Part 1 (proposal → engagement)
- Proposal requests with real document uploads and staff assignment (auto-email + daily reminders).
- **Checklist-enforced collection**: named requirement slots (document / information). The baton cannot pass back until every slot is provided, answered, waived (with reason), or withdrawn (with reason). Itemized rejection loops, unlimited rounds.
- **AI-professionalized drafting** (Claude API): rough manager shorthand ("Vat quaterly in advance, Ct filling annually") is rewritten into client-ready wording at every generation — figures preserved exactly, the rough original kept on the audit record.
- **Generate ≠ send**: drafters preview the rendered document; a dirty-form lock makes it impossible to send a version that doesn't match the form.
- **Dual digital-signature governance**: manager signs & routes (with optional note), senior management counter-signs with identity re-confirmation; rejections carry mandatory notes that pin as banners until acted on. Encrypted signature vault — preview-only specimens, every application logged.
- **Client confirmation is an artifact, not a button**: uploading the client-countersigned proposal is the gate that converts prospect → client and auto-prepares the engagement letter (commercial terms locked to what the client signed).
- **Per-activity staffing** with each candidate's live workload (open proposals + existing duties) shown at selection.
- Engagement letter routed for senior signature (sign / reject-with-note only), emailed via edit-confirm drafts. EL sent = Part 1 milestone; the trail stays open into Part 2.
- **Version history & comparison reports**: any superseded version viewable (watermarked, unsigned); side-by-side diffs with changed cells highlighted; audit entries state exact changes ("Bookkeeping: fee AED 2,500 → AED 3,000").

### Statutory Deadline Engine
- Recurring duties (VAT / CT / bookkeeping / custom) with cadence + anchor due date. **Deadlines auto-compute forward from statutory dates — late completion never shifts the schedule.**
- **Proof-of-work completion**: bookkeeping duties complete only when reports are uploaded *and emailed to the client contact*; VAT/CT duties require the filed FTA return plus structured filing records (period, net position, taxable sales per emirate / FY, taxable income). "Declared without proof" is allowed — with a mandatory, permanently logged reason.
- Per-duty append-only audit trails and filing-history records; firm-wide compliance board for managers; on-time ratios feed performance evaluation.

### Payments (Tier 1, manual by design)
- Expected-payment schedules generated from engagement terms; the in-house accountant marks invoices raised and records receipts with evidence. Daily nags until each receipt status is updated. Client payment-health badges (Good / Watch / At risk).

### Deployment
- Six-step first-run wizard: **Firm** (incl. optional letterhead / proposal / EL template uploads) → **Activities** (firm service catalog) → **Employees** (incl. pre-Baton duties for brownfield deployments) → **Roles** (permission matrix) → **Credentials** (temp passwords, forced reset) → **Signatures** (specimen capture for management).

## Running the prototype

The prototype is a single-file React component (`src/baton-prototype.jsx`, Tailwind classes, in-memory state).

- **Fastest:** paste the file into a [Claude](https://claude.ai) chat as an artifact — it renders and runs immediately. The AI rewording feature works natively there.
- Or drop it into any React + Tailwind sandbox (Vite, CodeSandbox). Note: the AI-professionalization call targets the Anthropic API endpoint available inside Claude artifacts; outside that environment it fails gracefully (manual wording is kept).

On first run you'll see the deployment welcome screen. Use the **Crescent Bay demo shortcut** (2 partners · 3 managers · 8 technical staff · 1 in-house accountant, with pre-existing client duties) or run the setup wizard. `demo-assets/` contains fictitious client documents and specimen signatures for walking the full onboarding chain, including a client-countersigned proposal for the confirmation gate.

Simulation controls (+1 day / +7 days) advance the clock to exercise aging, overdue nags, and deadline rollforward.

## Design principles

- **Audit trail is append-only, everywhere.** State changes, documents, signatures, emails, chat — timestamped and attributed; nobody, including Admin, can edit or delete.
- **Documents are generated from structured data**, never hand-edited; every change produces a version; signatures apply only via the signatory's own identity-confirmed action.
- **Evidence over claims**: client confirmation = signed artifact; duty completion = proof of work; exceptions demand logged reasons.
- **Financial automation is out of scope by choice** — invoices live in the firm's accounting software; Baton tracks facts.

## Tech

Prototype: React (single file), Tailwind, Claude API (in-artifact) for drafting assistance.
Production target: Python/FastAPI + SQLAlchemy + PostgreSQL (multi-tenant), Next.js/TypeScript, per-tenant encryption for the signature vault, transactional email, real 2FA.

## Roadmap

Onboarding Part 2 (client documentation master) → recurring-work absorption of pre-Baton duties → document template rendering (uploaded firm formats) → PDF export of generated documents → firm-wide performance analytics → accounting-software integrations (Zoho / QBO / Xero) for Tier 3 payment sync.

---

*All demo data — firms, people, clients, documents, signatures — is fictitious and watermarked as such.*
