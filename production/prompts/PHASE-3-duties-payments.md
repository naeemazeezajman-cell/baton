# Phase 3 — Duties engine, payments, daily digest

Spec: src/baton-prototype.jsx (DutyCard/markDutyDone, payments) + production/db/schema.sql.

1. Duties router: GET /duties (mine / all for managers), POST /duties (Admin/Manager: assign new duty incl. contact, cadence, next_due), POST /duties/{id}/complete — multipart: method (sent|proof|declared) + fields (note, reason, emailed_to, record JSON) + evidence files. Server computes late_ms, writes duty_completions + duty_events, advances next_due from the DUE date by cadence (calendar months), closes one-time duties. Validation mirrors the prototype: vat requires proof+period+position; ct requires proof+FY+position; report/'sent' requires files+emailed_to; declared requires reason.
2. 'sent' method also dispatches the email via ACS with SAS links to the evidence files, logged in duty_events.
3. Payments router: list (accountant/admin), invoice-raised, record-receipt (amount, evidence file, partial allowed) — events appended; health computation endpoint per client (Good / Watch ≤30d overdue / At risk >30d).
4. Scheduler (APScheduler, 07:00 Asia/Dubai): per user — overdue duties digest email + notices rows; per accountant — receivables digest. Idempotent per day (record last-run).
5. Tests: cadence rollforward (incl. late completion not shifting schedule), completion validation matrix, tenancy isolation on duties.

Show test output and an example digest email body when done.
