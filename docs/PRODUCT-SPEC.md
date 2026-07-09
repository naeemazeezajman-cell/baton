# Baton — Product Specification (working notes)

Condensed record of the design decisions behind the prototype. Full rationale lives in the build log.

## Market & scope
UAE bookkeeping/tax firms; 100–200 clients, 30–50 technical staff. Services: bookkeeping, audit support, VAT, Corporate Tax filing/planning, ESR, periodic reporting. Multi-tenant SaaS.

## Roles
Admin (senior management / partners — counter-signatories), Manager (engagement managers — sign & route), Staff (technical), Accountant (in-house finance). Fixed matrix in v1; data model is role→permissions for future custom roles.

## Single-holder model
- One live holder per matter; holder log {userId, start, end, reason} powers time attribution, aging queues, and performance reports.
- Generating a document never transfers responsibility; only explicit send/route actions move the baton.
- Time with the client (proposal/EL sent) is excluded from employee ratings.

## Onboarding Part 1 lifecycle
assigned → docs_with_manager ⇄ waiver_review → drafting → manager_review → senior_review → signed → proposal_sent → (client-signed proposal upload = conversion gate) → el_staffing → el_senior_review → el_approved → el_sent (Part 1 milestone; trail remains open for Part 2) | lost.

Key rules:
- Checklist slots: pending / provided / rejected(reason) / waiver_requested(reason) → waived|still-required / withdrawn(reason, allowed even when the baton is with the other side).
- Manager review fork after senior rejection: return-to-drafter (mandatory instruction) or sign-&-route (optional note to signatory). Rejection banners pin until acted on.
- Senior may edit commercial terms on the proposal (regenerates + notifies); on the EL, senior may only sign or reject.
- EL fees locked to the client-signed proposal; payment plan defaults to proposal terms; any advance is explicit opt-in with a first-billing-period breakdown and warning.
- Dirty-form lock: send is disabled whenever form state ≠ latest generated version.
- Audit diffs: every regeneration logs exact field-level changes (old → new); EL advance/special-terms changes logged discretely.
- Version viewer (superseded = watermarked, unsigned) + side-by-side comparison report for Manager/Admin.

## Deadline engine
Duty {staff, client, service→kind(report|vat|ct|other), contact{name,email}, cadence, nextDue, history[], events[]}.
- nextDue advances by cadence from the DUE date on completion (statutory anchoring).
- Completion methods: sent (deliverables uploaded + emailed to contact), proof (filed return + structured record: VAT — period, position, net/output/input VAT, taxable sales per emirate; CT — FY, taxable income, CT payable, position, SBR), declared (mandatory reason).
- Daily overdue nags (in-system + email); manager compliance board; on-time ratios per duty.

## Payments (Tier 1)
Expected payments generated at EL send (advance/balance or per-basis first periods; monthly in arrears +30d, advance-basis due day 0). Accountant: mark invoice raised → record receipts (partial OK, evidence upload). Health: Good / Watch (≤30d overdue) / At risk (>30d).

## Performance reporting
Fires at FULL onboarding completion (Parts 1+2). Total cycle time; per-employee ★1–5 from average holding time (≤0.5d=5 … >7d=1, scale printed); drill-down per employee sorted by holding duration desc, longest flagged.

## Deployment wizard
Firm (+optional letterhead/proposal/EL template uploads) → Activities (service catalog; drives proposal requests; custom entries flagged to Admin) → Employees (+pre-Baton duties w/ cadence, anchor due date, client contact) → Roles (≥1 Admin) → Credentials (temp pw, invites, forced reset) → Signatures (≥1 senior specimen; encrypted vault, per-use logging).

## Explicit non-goals (v1)
No internal task deadlines (visibility instead). No financial automation (receipts manual). No editing/deleting audit entries by anyone.
