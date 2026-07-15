"""Seed (and re-seed) "Baton Demo Co" — the publicly-credentialed showcase firm.

    python -m app.demo_seed            # create, or top up if already there
    python -m app.demo_seed --reset    # wipe the demo tenant's data and rebuild it
    python -m app.demo_seed --status   # print what's there, change nothing

Run it wherever DATABASE_URL points, so against production it is:

    az containerapp exec -n baton-api -g baton-prod --command "python -m app.demo_seed --reset"

which needs no local database credentials and no firewall exception.

Design notes:

* The tenant is keyed on DEMO_TENANT_EMAIL, so re-running is safe and idempotent.
* Every timestamp is derived from now(), so the aging clocks, overdue banners and holder
  timers are alive on any day it is run rather than frozen at seed time.
* Nothing here references a file blob it did not create. The seed ships in the API image,
  which has no demo-assets/ (the Docker build context is api/), so a hand-written
  {"file_id": ...} would render a download link that 404s. States that would need a real
  document instead use the product's own no-document paths — the engagement is confirmed
  via confirm-unsigned (email approval), and pending document slots stay pending, which is
  the checklist mechanic the demo is meant to show anyway. The one real file is the VAT
  reconciliation workbook, which _reconcile() genuinely generates.
* The VAT filing is built by calling the engine's own _reconcile(), not by hand-writing a
  recon dict — the demo's numbers are therefore produced by the same code that runs in
  production. The import is lazy and optional because the VAT engine is a removable module
  (REMOVING-VAT-ENGINE.md); without it the seed just skips the filing.
"""

import argparse
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import (Client, Duty, DutyEvent, HolderLog, Onboarding, OnboardingEvent,
                     OnboardingItem, Payment, Proposal, ProposalEvent, Subscription,
                     Tenant, User)
from .security import hash_password
from .workflow import default_basis, iso, now

log = logging.getLogger("baton.demo_seed")

DEMO_TENANT_EMAIL = "hello@batondemo.co"
DEMO_TENANT_NAME = "Baton Demo Co"
DEMO_TENANT_SHORT = "Baton Demo"

# Published on a CV. Fixed, shared, and must_reset=False — see demo.py for why nobody is
# allowed to change them from inside the app.
DEMO_PASSWORD = "BatonDemo2026!"

SERVICES = [
    "Bookkeeping (Monthly)",
    "VAT Return Filing (Quarterly)",
    "Corporate Tax Filing (Annual)",
    "ESR Notification",
    "Payroll (Monthly)",
]

# (key, name, designation, email, role, signatory)
DEMO_USERS = [
    ("admin", "Layla Haddad", "Managing Partner", "demo.admin@batondemo.co", "Admin", True),
    ("manager", "Omar Farouk", "Client Services Manager", "demo.manager@batondemo.co", "Manager", False),
    ("staff", "Priya Nair", "Senior Accountant", "demo.staff@batondemo.co", "Staff", False),
    ("staff2", "Daniel Mwangi", "Tax Associate", "demo.staff2@batondemo.co", "Staff", False),
    ("accountant", "Sara Ahmed", "Finance & Billing", "demo.accountant@batondemo.co", "Accountant", False),
]

# Tenant-owned tables, children before parents. Used only by --reset, only ever with a
# tenant_id predicate, and only after the caller has proven the tenant is flagged demo.
WIPE_ORDER = [
    "vat_filing_items", "vat_filing_events", "vat_extraction_drafts", "vat_client_requests",
    "vat_filings", "vat_client_profiles",
    "holder_log", "proposal_events",
    "onboarding_items", "onboarding_events", "onboardings",
    "duty_events", "duty_completions", "duties",
    "payments", "notices", "signature_uses", "files", "performance_config",
    "clients", "proposals", "users", "subscriptions",
]


def _ts(days_ago: float) -> datetime:
    return now() - timedelta(days=days_ago)


# ---------- wipe ----------

def wipe_demo_data(db: Session, tenant: Tenant) -> dict:
    """Delete the demo tenant's rows, keeping the tenants row itself.

    Two independent guards, because this is a DELETE loop running against the production
    database that also holds real firms: the tenant must be flagged demo, and every
    statement carries its tenant_id. A table that has gone missing (the VAT engine is
    removable) is skipped, not an error.
    """
    if not tenant.demo:
        raise RuntimeError(
            f"Refusing to wipe {tenant.name} ({tenant.id}) — tenants.demo is false. "
            f"This command only ever touches the demo firm."
        )
    deleted = {}
    for table in WIPE_ORDER:
        if db.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar() is None:
            continue
        n = db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :tid"),  # noqa: S608 — fixed allowlist
                       {"tid": str(tenant.id)}).rowcount
        if n:
            deleted[table] = n
    db.flush()
    return deleted


# ---------- build ----------

def _make_users(db: Session, tenant: Tenant) -> dict[str, User]:
    users: dict[str, User] = {}
    for key, name, designation, email, role, signatory in DEMO_USERS:
        u = User(
            tenant_id=tenant.id, name=name, designation=designation, email=email, role=role,
            signatory=signatory,
            sig_specimen={"type": "typed", "text": name} if signatory else None,
            password_hash=hash_password(DEMO_PASSWORD),
            must_reset=False,  # the credentials are published — there is no first-login reset
            active=True,
            last_login_at=_ts(0.2),
        )
        db.add(u)
        users[key] = u
    db.flush()
    return users


def _draft(lines: list[dict], terms: str, validity: int = 30, scope: str = "") -> dict:
    return {"lines": [{"service": l["service"], "fee": l["fee"],
                       "basis": l.get("basis") or default_basis(l["service"])} for l in lines],
            "payment_terms": terms, "validity_days": validity, "scope": scope}


def _version(v: int, data: dict, by: User, at: datetime, note: str = "draft", **extra) -> dict:
    return {"v": v, "at": iso(at), "by": str(by.id), "data": data, "note": note,
            "polished_from": None, "signatures": {}, **extra}


def _hold(db: Session, p: Proposal, user: User, since: datetime, reason: str) -> None:
    """Open the current holding span. Non-terminal proposals must have exactly one open row
    whose user_id matches Proposal.holder — the aging boards read this, not created_at."""
    db.add(HolderLog(tenant_id=p.tenant_id, proposal_id=p.id, user_id=user.id,
                     started_at=since, reason=reason))
    p.holder = user.id


def _closed_hold(db: Session, p: Proposal, user: User, start: datetime, end: datetime, reason: str) -> None:
    db.add(HolderLog(tenant_id=p.tenant_id, proposal_id=p.id, user_id=user.id,
                     started_at=start, ended_at=end, reason=reason))


def _ev(db: Session, p: Proposal, by: User | None, at: datetime, txt: str,
        kind: str = "log", meta: dict | None = None) -> None:
    db.add(ProposalEvent(tenant_id=p.tenant_id, proposal_id=p.id, at=at,
                         by_user=by.id if by else None, kind=kind, text_=txt, meta=meta))


def _seed_proposals(db: Session, tenant: Tenant, u: dict[str, User]) -> dict[str, Proposal]:
    """Four proposals, one per interesting stage of the Part 1 chain."""
    admin, mgr, staff, staff2 = u["admin"], u["manager"], u["staff"], u["staff2"]
    out: dict[str, Proposal] = {}

    # --- P-001: docs_with_manager — checklist out for collection, baton with the manager ---
    p1 = Proposal(
        tenant_id=tenant.id, ref="P-001", status="docs_with_manager",
        prospect={"name": "Nimbus Freight Services LLC", "email": "finance@nimbusfreight.ae",
                  "phone": "+971 4 812 4470", "company": None, "contactPerson": "Hassan Darwish",
                  "notes": "Inbound from the DMCC free-zone desk. Wants VAT live before the next quarter."},
        services=[{"name": "Bookkeeping (Monthly)", "fee": "3200", "basis": "per month", "custom": False},
                  {"name": "VAT Return Filing (Quarterly)", "fee": "4500", "basis": "per quarter", "custom": False}],
        payment_terms_rough="30 days from invoice, vat quaterly in advance",
        payment_terms="",
        assigned_to=staff.id, requested_by=mgr.id, signatory_id=None,
        checklist=[
            {"id": str(uuid.uuid4()), "kind": "document", "label": "Trade licence copy",
             "status": "pending", "value": "", "file_name": "", "file_id": None, "reason": ""},
            {"id": str(uuid.uuid4()), "kind": "document", "label": "VAT registration certificate",
             "status": "pending", "value": "", "file_id": None, "file_name": "", "reason": ""},
            {"id": str(uuid.uuid4()), "kind": "data", "label": "Expected monthly transaction volume",
             "status": "pending", "value": "", "file_name": "", "file_id": None, "reason": ""},
        ],
        versions=[], el={}, draft=_draft(
            [{"service": "Bookkeeping (Monthly)", "fee": "3200"},
             {"service": "VAT Return Filing (Quarterly)", "fee": "4500"}],
            "30 days from invoice, vat quaterly in advance"),
        signatures={}, created_at=_ts(6),
    )
    db.add(p1)
    db.flush()
    _ev(db, p1, mgr, _ts(6), f"Proposal request created and assigned to {staff.name}")
    _closed_hold(db, p1, staff, _ts(6), _ts(4.5), "drafting the requirement list")
    _ev(db, p1, staff, _ts(4.5), "Requested 3 item(s) from the requester — baton passed back")
    _hold(db, p1, mgr, _ts(4.5), "collecting the requested items from the prospect")
    out["p1"] = p1

    # --- P-002: senior_review — manager has signed and routed, waiting on the partner ---
    d2 = _draft([{"service": "Bookkeeping (Monthly)", "fee": "2750"},
                 {"service": "Payroll (Monthly)", "fee": "1200"}],
                "Fees are payable within 14 days of the invoice date. Payroll is billed monthly in arrears.",
                scope="Excludes prior-period corrections before the engagement start date.")
    p2 = Proposal(
        tenant_id=tenant.id, ref="P-002", status="senior_review",
        prospect={"name": "Cedar Interiors LLC", "email": "accounts@cedarinteriors.ae",
                  "phone": "+971 6 745 1180", "company": None, "contactPerson": "Maya Rizk",
                  "notes": "Fit-out contractor, 40 staff. Payroll is the pain point."},
        services=[{"name": "Bookkeeping (Monthly)", "fee": "2750", "basis": "per month", "custom": False},
                  {"name": "Payroll (Monthly)", "fee": "1200", "basis": "per month", "custom": False}],
        payment_terms_rough="14 days, payroll monthly in arrears",
        payment_terms=d2["payment_terms"],
        assigned_to=staff2.id, requested_by=mgr.id, signatory_id=admin.id,
        checklist=[
            {"id": str(uuid.uuid4()), "kind": "data", "label": "Headcount at engagement start",
             "status": "provided", "value": "41 (38 full-time, 3 part-time)", "file_name": "",
             "file_id": None, "reason": ""},
            {"id": str(uuid.uuid4()), "kind": "document", "label": "Audited financials FY2025",
             "status": "waived", "value": "", "file_name": "", "file_id": None,
             "reason": "First year of operations — no audited set exists yet."},
        ],
        versions=[], el={}, draft=d2, signatures={},
        senior_note={"by": str(mgr.id), "at": iso(_ts(1.2)),
                     "text": "Long-standing referral source — please prioritise."},
        created_at=_ts(11),
    )
    p2.versions = [
        _version(1, _draft([{"service": "Bookkeeping (Monthly)", "fee": "3000"},
                            {"service": "Payroll (Monthly)", "fee": "1200"}],
                           "14 days, payroll monthly in arrears"),
                 staff2, _ts(8), note="first pass"),
        _version(2, d2, staff2, _ts(1.4), note="fee revised after manager feedback"),
    ]
    p2.versions[-1]["signatures"] = {"manager": {"by": str(mgr.id), "at": iso(_ts(1.2)),
                                                 "specimen_ref": None}}
    p2.signatures = {"manager": {"by": str(mgr.id), "at": iso(_ts(1.2))}}
    db.add(p2)
    db.flush()
    _ev(db, p2, mgr, _ts(11), f"Proposal request created and assigned to {staff2.name}")
    _closed_hold(db, p2, staff2, _ts(11), _ts(1.2), "drafting")
    _ev(db, p2, staff2, _ts(8), "Version 1 generated", kind="log")
    _ev(db, p2, mgr, _ts(6),
        "Sent back for revision — client pushed back on the bookkeeping fee, try AED 2,750")
    _ev(db, p2, staff2, _ts(1.4), "Version 2 generated", kind="diff",
        meta={"v": 2, "prev_v": 1, "changes": ["Bookkeeping (Monthly): fee AED 3,000 → AED 2,750"]})
    _ev(db, p2, mgr, _ts(1.2), f"Signed and routed to {admin.name} for counter-signature")
    _hold(db, p2, admin, _ts(1.2), "senior counter-signature")
    out["p2"] = p2

    # --- P-003: proposal_sent — with the client, awaiting their signature ---
    d3 = _draft([{"service": "Corporate Tax Filing (Annual)", "fee": "6500"},
                 {"service": "ESR Notification", "fee": "1500"}],
                "50% on engagement, balance on filing. Fees are payable within 30 days of the invoice date.",
                validity=21)
    p3 = Proposal(
        tenant_id=tenant.id, ref="P-003", status="proposal_sent",
        prospect={"name": "Al Dana Holding LLC", "email": "cfo@aldanaholding.ae",
                  "phone": "+971 2 667 9014", "company": None, "contactPerson": "Faisal Al Dana",
                  "notes": "Group of four entities; this proposal covers the holdco only."},
        services=[{"name": "Corporate Tax Filing (Annual)", "fee": "6500", "basis": "per annum", "custom": False},
                  {"name": "ESR Notification", "fee": "1500", "basis": "per annum", "custom": False}],
        payment_terms_rough="50% upfront balance on filing, 30 days",
        payment_terms=d3["payment_terms"],
        assigned_to=staff.id, requested_by=mgr.id, signatory_id=admin.id,
        checklist=[
            {"id": str(uuid.uuid4()), "kind": "data", "label": "Financial year end",
             "status": "provided", "value": "31 December", "file_name": "", "file_id": None, "reason": ""},
        ],
        versions=[], el={}, draft=d3,
        signatures={"manager": {"by": str(mgr.id), "at": iso(_ts(9))},
                    "senior": {"by": str(admin.id), "at": iso(_ts(8.6))}},
        proposal_sent_at=_ts(8.4), created_at=_ts(16),
    )
    p3.versions = [_version(1, d3, staff, _ts(10), note="draft")]
    p3.versions[-1]["signatures"] = {
        "manager": {"by": str(mgr.id), "at": iso(_ts(9)), "specimen_ref": None},
        "senior": {"by": str(admin.id), "at": iso(_ts(8.6)), "specimen_ref": str(admin.id)},
    }
    db.add(p3)
    db.flush()
    _ev(db, p3, mgr, _ts(16), f"Proposal request created and assigned to {staff.name}")
    _closed_hold(db, p3, staff, _ts(16), _ts(9), "drafting")
    _ev(db, p3, staff, _ts(10), "Version 1 generated")
    _ev(db, p3, mgr, _ts(9), f"Signed and routed to {admin.name} for counter-signature")
    _closed_hold(db, p3, admin, _ts(9), _ts(8.6), "senior counter-signature")
    _ev(db, p3, admin, _ts(8.6), "Counter-signed — proposal released for sending")
    _ev(db, p3, mgr, _ts(8.4),
        'Email confirmed & sent to cfo@aldanaholding.ae — subject: "Baton Demo Co — proposal for '
        'Al Dana Holding LLC" (signed proposal PDF attached)',
        kind="email", meta={"to": "cfo@aldanaholding.ae",
                            "subject": "Baton Demo Co — proposal for Al Dana Holding LLC",
                            "attach_version": 1})
    _hold(db, p3, mgr, _ts(8.4), "awaiting client signature")
    out["p3"] = p3

    return out


def _seed_won_engagement(db: Session, tenant: Tenant, u: dict[str, User]) -> tuple[Proposal, Client]:
    """P-004 — the whole Part 1 chain finished: client confirmed by email approval, EL sent,
    onboarding and payment schedule live. Terminal, so holder is None and no span is open."""
    admin, mgr, staff, acct = u["admin"], u["manager"], u["staff"], u["accountant"]
    services = [{"service": "VAT Return Filing (Quarterly)", "fee": "4800", "basis": "per quarter"},
                {"service": "Bookkeeping (Monthly)", "fee": "3500", "basis": "per month"}]
    d4 = _draft(services, "50% advance on engagement, balance quarterly in arrears. "
                          "Fees are payable within 30 days of the invoice date.")
    p4 = Proposal(
        tenant_id=tenant.id, ref="P-004", status="el_sent",
        prospect={"name": "Meridian Logistics FZ-LLC", "email": "finance@meridian-log.ae",
                  "phone": "+971 4 553 8820", "company": None, "contactPerson": "Rania Haddad",
                  "notes": "Freight forwarder, DMCC. Wants VAT live before the Q1 filing."},
        services=[{"name": s["service"], "fee": s["fee"], "basis": s["basis"], "custom": False}
                  for s in services],
        payment_terms_rough="50% advance, balance quarterly in arrears, 30 days",
        payment_terms=d4["payment_terms"],
        assigned_to=staff.id, requested_by=mgr.id, signatory_id=admin.id,
        checklist=[
            {"id": str(uuid.uuid4()), "kind": "data", "label": "Free-zone authority",
             "status": "provided", "value": "DMCC", "file_name": "", "file_id": None, "reason": ""},
        ],
        versions=[], draft=d4,
        signatures={"manager": {"by": str(mgr.id), "at": iso(_ts(38))},
                    "senior": {"by": str(admin.id), "at": iso(_ts(37))}},
        proposal_sent_at=_ts(36), created_at=_ts(44), onboarding_completed_at=_ts(30),
        holder=None,
    )
    p4.versions = [_version(1, d4, staff, _ts(39), note="draft")]
    p4.versions[-1]["signatures"] = {
        "manager": {"by": str(mgr.id), "at": iso(_ts(38)), "specimen_ref": None},
        "senior": {"by": str(admin.id), "at": iso(_ts(37)), "specimen_ref": str(admin.id)},
    }
    db.add(p4)
    db.flush()

    client = Client(tenant_id=tenant.id, ref="CL-001", name=p4.prospect["name"],
                    contact=dict(p4.prospect), origin="proposal", from_proposal=p4.id,
                    confirmation_basis="client approval received by email", created_at=_ts(32))
    db.add(client)
    db.flush()
    p4.client_id = client.id
    p4.el = {
        "note": "Fees exclude FTA penalties arising from pre-engagement periods.",
        "advance_pct": 50,
        "signatory_id": str(admin.id),
        "signature": {"by": str(admin.id), "at": iso(_ts(31))},
        "sent_at": iso(_ts(30)),
        "assignments": {"VAT Return Filing (Quarterly)": str(staff.id),
                        "Bookkeeping (Monthly)": str(u["staff2"].id)},
        "client_confirmation": {
            "basis": "email_approval",
            "label": "client approval received by email",
            "note": "Rania confirmed both activities at the quoted fees by email.",
            "at": iso(_ts(32)),
            "evidence": [],
        },
    }
    for at, by, txt in [
        (_ts(44), mgr, f"Proposal request created and assigned to {staff.name}"),
        (_ts(39), staff, "Version 1 generated"),
        (_ts(38), mgr, f"Signed and routed to {admin.name} for counter-signature"),
        (_ts(37), admin, "Counter-signed — proposal released for sending"),
        (_ts(32), mgr, "Client confirmation recorded (client approval received by email) — "
                       "prospect converted to client CL-001; engagement letter prepared"),
        (_ts(31), admin, "Engagement letter counter-signed"),
    ]:
        _ev(db, p4, by, at, txt)
    _ev(db, p4, mgr, _ts(30),
        'Email confirmed & sent to finance@meridian-log.ae — subject: "Baton Demo Co — engagement '
        'letter" (signed engagement letter PDF attached)',
        kind="email", meta={"to": "finance@meridian-log.ae", "subject": "Baton Demo Co — engagement letter"})
    _closed_hold(db, p4, staff, _ts(44), _ts(38), "drafting")
    _closed_hold(db, p4, admin, _ts(38), _ts(37), "senior counter-signature")
    _closed_hold(db, p4, mgr, _ts(37), _ts(30), "client confirmation and engagement letter")

    # payment schedule, as el-send generates it: advance + the first quarterly balance
    db.add(Payment(
        tenant_id=tenant.id, client_id=client.id, proposal_id=p4.id,
        label="Advance (50%) — VAT Return Filing (Quarterly), Bookkeeping (Monthly)",
        amount=8300, due_at=_ts(23), invoice_raised=True, invoice_raised_at=_ts(28),
        invoice={"number": "INV-2026-0412", "date": _ts(28).date().isoformat(), "files": [],
                 "by": str(acct.id), "declared": True,
                 "reason": "Raised directly in the firm's accounting software — Baton tracks the fact."},
        receipts=[{"amount": 8300.0, "at": iso(_ts(22)), "received_date": _ts(23).date().isoformat(),
                   "method": "bank_transfer", "reference": "FT26060199231", "note": None,
                   "by": str(acct.id)}],
        events=[{"at": iso(_ts(30)), "by": "system", "text": "Expected payment created from engagement terms"},
                {"at": iso(_ts(28)), "by": str(acct.id), "text": "Invoice INV-2026-0412 declared as raised"},
                {"at": iso(_ts(22)), "by": "system", "text": "Fully received — reminders stopped"}],
    ))
    # deliberately still outstanding — this is what lights up the Payments board and the
    # accountant's daily receivables digest
    db.add(Payment(
        tenant_id=tenant.id, client_id=client.id, proposal_id=p4.id,
        label="Balance — Bookkeeping (Monthly), quarter 1",
        amount=4200, due_at=_ts(3), invoice_raised=False, invoice=None, receipts=[],
        events=[{"at": iso(_ts(30)), "by": "system", "text": "Expected payment created from engagement terms"}],
    ))
    db.flush()
    return p4, client


def _seed_onboarding(db: Session, tenant: Tenant, u: dict[str, User], p4: Proposal,
                     client: Client) -> Onboarding:
    """The documentation relay for one of the won engagement's activities — mid-flight, with
    the baton aged past the digest threshold so the aging boards have something to show."""
    staff, mgr = u["staff"], u["manager"]
    ob = Onboarding(tenant_id=tenant.id, client_id=client.id, proposal_id=p4.id,
                    service="VAT Return Filing (Quarterly)", staff_id=staff.id,
                    status="in_progress", holder=mgr.id, holder_since=_ts(5),
                    created_at=_ts(30))
    db.add(ob)
    db.flush()
    db.add(HolderLog(tenant_id=tenant.id, onboarding_id=ob.id, user_id=staff.id,
                     started_at=_ts(30), ended_at=_ts(5), reason="listing the required items"))
    db.add(HolderLog(tenant_id=tenant.id, onboarding_id=ob.id, user_id=mgr.id,
                     started_at=_ts(5), reason="collecting the outstanding items from the client"))
    items = [
        ("Trade licence copy", "document", "requested", {}),
        ("VAT registration certificate (TRN)", "document", "requested", {}),
        ("Bank statements — most recent quarter", "document", "requested", {}),
        ("Financial year end", "information", "answered", {"answer_text": "31 December"}),
        ("Tax period stagger confirmed with the FTA", "information", "answered",
         {"answer_text": "Quarterly, Jan/Apr/Jul/Oct"}),
        ("FTA EmaraTax portal login", "credential", "answered",
         {"credential": {"portal_label": "FTA EmaraTax", "username": "meridian.tax",
                         "password": "Aut0mate!2026", "extra_note": "OTP goes to Rania's mobile."}}),
        ("Prior-year audited financials", "information", "not_available",
         {"reason": "First year of operations in the UAE — no audited set exists yet."}),
    ]
    for label, kind, status, extra in items:
        resolved = status != "requested"
        db.add(OnboardingItem(
            tenant_id=tenant.id, onboarding_id=ob.id, label=label, kind=kind, status=status,
            requested_by=staff.id, requested_at=_ts(29),
            resolved_at=_ts(6) if resolved else None,
            accepted_at=_ts(6) if status in ("provided", "answered", "not_available") else None,
            **extra,
        ))
    db.add(OnboardingEvent(tenant_id=tenant.id, onboarding_id=ob.id, at=_ts(29), by_user=staff.id,
                           text_=f"{len(items)} item(s) requested for {ob.service}"))
    db.add(OnboardingEvent(tenant_id=tenant.id, onboarding_id=ob.id, at=_ts(5), by_user=staff.id,
                           text_="Baton passed to Omar Farouk — 3 document(s) still outstanding "
                                 "with the client"))
    db.flush()
    return ob


def _seed_clients_and_duties(db: Session, tenant: Tenant, u: dict[str, User],
                             client1: Client) -> dict:
    """Two pre-Baton clients plus the recurring duties. Due dates straddle today on purpose:
    one overdue (red timer + daily nag), the rest upcoming."""
    staff, staff2 = u["staff"], u["staff2"]
    gulf = Client(tenant_id=tenant.id, ref="CL-002", name="Gulf Horizon Trading LLC",
                  contact={"name": "Aisha Rahman", "email": "accounts@gulfhorizon.ae",
                           "phone": "+971 4 331 7788", "contactPerson": "Aisha Rahman"},
                  origin="pre_baton",
                  confirmation_basis="pre-existing relationship (pre-Baton deployment)",
                  created_at=_ts(60))
    cedar = Client(tenant_id=tenant.id, ref="CL-003", name="Sahara Stone Works LLC",
                   contact={"name": "Tarek Nasser", "email": "admin@saharastone.ae",
                            "phone": "+971 6 552 3311", "contactPerson": "Tarek Nasser"},
                   origin="pre_baton",
                   confirmation_basis="pre-existing relationship (pre-Baton deployment)",
                   created_at=_ts(60))
    db.add_all([gulf, cedar])
    db.flush()

    duties = {}
    spec = [
        # overdue on purpose, and by enough that derive_period() lands on a quarter that has
        # actually ended: the period is pinned to the month before the due month, so a future
        # due date would mean reconciling an open quarter. A late VAT return held up by
        # unresolved reconciliation differences is also the sharpest thing the demo can show.
        ("vat", gulf, "VAT Return Filing (Quarterly)", "quarterly", staff, -45,
         {"name": "Aisha Rahman", "email": "accounts@gulfhorizon.ae"}),
        ("bookkeeping_overdue", cedar, "Bookkeeping (Monthly)", "monthly", staff2, -4,
         {"name": "Tarek Nasser", "email": "admin@saharastone.ae"}),
        ("ct", gulf, "Corporate Tax Filing (Annual)", "annual", staff2, 51,
         {"name": "Aisha Rahman", "email": "accounts@gulfhorizon.ae"}),
        ("bookkeeping_meridian", client1, "Bookkeeping (Monthly)", "monthly", staff2, 9,
         {"name": "Rania Haddad", "email": "finance@meridian-log.ae"}),
    ]
    for key, client, service, cadence, owner, due_in_days, contact in spec:
        from .routers.duties import duty_kind
        d = Duty(tenant_id=tenant.id, staff_id=owner.id, client_name=client.name,
                 client_id=client.id, service=service, kind=duty_kind(service),
                 contact=contact, cadence=cadence, next_due=now() + timedelta(days=due_in_days),
                 closed=False, created_at=_ts(58))
        db.add(d)
        db.flush()
        db.add(DutyEvent(tenant_id=tenant.id, duty_id=d.id, by_user=None, at=_ts(58),
                         text_=f"Duty registered — {service} for {client.name}, {cadence}, "
                               f"next due {d.next_due:%d %b %Y}"))
        duties[key] = d
    db.flush()
    return {"gulf": gulf, "cedar": cedar, "duties": duties}


def _seed_vat_filing(db: Session, tenant: Tenant, u: dict[str, User], duty: Duty,
                     client: Client) -> bool:
    """A filing parked mid-reconciliation: the ledger and the client's issued-invoice register
    disagree, so there are differences to chase before the computation unlocks.

    Built by running the engine's real _reconcile(), so the buckets, the workbook and the
    audit line are all produced by production code rather than transcribed here.
    """
    try:
        from .routers import vat_engine as ve
    except Exception as exc:  # noqa: BLE001 — the VAT engine is a removable module
        log.warning("VAT engine unavailable (%s) — skipping the demo filing", exc)
        return False

    staff = u["staff"]
    prof = ve.VatClientProfile(
        tenant_id=tenant.id, client_id=client.id,
        nature_of_business="Import and re-export of building materials; occasional zero-rated exports.",
        business_category="Trading", tax_period_stagger="jan_apr_jul_oct",
        flags={"trn_confirmed": {"value": "yes", "note": "TRN verified on the FTA portal."},
               "has_zero_rated": {"value": "yes", "note": "Exports to Oman and KSA."},
               "has_exempt": {"value": "no", "note": None},
               "designated_zone": {"value": "no", "note": None},
               "margin_scheme": {"value": "no", "note": None},
               "rcm_imports": {"value": "yes", "note": "Regular imports via Jebel Ali."},
               "blocked_input_risk": {"value": "not_sure",
                                      "note": "Entertainment spend needs review each quarter."},
               "open_fta_matters": {"value": "no", "note": None}},
        version=1, created_by=staff.id, created_at=_ts(50), updated=[],
    )
    db.add(prof)
    db.flush()

    # the engine derives the period from the duty's due date and the profile's stagger —
    # hand-picked dates would drift out of alignment with the Jan/Apr/Jul/Oct quarters
    ps, pe, pps = ve.derive_period(duty, prof)
    f = ve.VatFiling(tenant_id=tenant.id, duty_id=duty.id, client_id=client.id, staff_id=staff.id,
                     period_start=ps, period_end=pe, prev_period_start=pps,
                     status="invoices_pending", created_at=_ts(9))
    db.add(f)
    db.flush()

    def item(source, row_no, no, day_offset, party, emirate, net, vat, type_=None, category="standard"):
        return ve.VatFilingItem(
            tenant_id=tenant.id, filing_id=f.id, source=source, row_no=row_no, invoice_no=no,
            invoice_no_norm=ve._norm_invoice_no(no),  # the engine's own key — matching depends on it
            invoice_date=ps + timedelta(days=day_offset), party=party, trn="100" + str(4000000 + row_no).zfill(9),
            emirate=emirate, net=net, vat=vat, type_=type_, category=category, origin="register",
        )

    # Output ledger rows; three of them have a matching register row, one does not (ledger_only)
    db.add_all([
        item("ledger", 2, "INV-2201", 4, "Al Futtaim Building Supplies", "Dubai", 42000, 2100, "Output"),
        item("ledger", 3, "INV-2202", 19, "Sharjah Cement Trading", "Sharjah", 18500, 925, "Output"),
        item("ledger", 4, "INV-2203", 33, "Oman Export Co", "Dubai", 61000, 0, "Output", "zero_rated"),
        item("ledger", 5, "INV-2204", 47, "Emirates Steel Distribution", "Abu Dhabi", 27400, 1370, "Output"),
        # input (purchase) rows — no register counterpart by design, they stay unbucketed
        item("ledger", 6, "PUR-9001", 12, "Jebel Ali Freight", "Dubai", 9800, 490, "Input"),
        item("ledger", 7, "PUR-9002", 40, "Gulf Office Supplies", "Dubai", 3100, 155, "Input"),
    ])
    db.add_all([
        item("invoice", 2, "INV-2201", 4, "Al Futtaim Building Supplies", "Dubai", 42000, 2100),
        item("invoice", 3, "INV-2202", 19, "Sharjah Cement Trading", "Sharjah", 18500, 925),
        item("invoice", 4, "INV-2203", 33, "Oman Export Co", "Dubai", 61000, 0, None, "zero_rated"),
        # in the client's register but never hit the ledger -> invoice_only difference to chase
        item("invoice", 5, "INV-2205", 52, "Ajman Hardware LLC", "Ajman", 14200, 710),
        # dated before the previous period start -> excluded by the window rule
        item("invoice", 6, "INV-2150", -120, "Al Futtaim Building Supplies", "Dubai", 8000, 400),
    ])
    db.flush()
    ve._log(db, f, staff.id, f"Filing opened for {ps:%d %b %Y} – {pe:%d %b %Y} "
                             f"(stagger Jan/Apr/Jul/Oct from profile v1).")
    ve._log(db, f, staff.id, "Client ledger uploaded and parsed — 6 row(s) registered.")
    ve._log(db, f, staff.id, "Client invoice register uploaded and parsed — 5 row(s) registered.")
    ve._reconcile(db, f, staff)  # sets status=reconciled, writes recon + the workbook
    db.flush()
    return True


# ---------- entry point ----------

def seed_demo(db: Session, reset: bool = False) -> dict:
    """Create the demo tenant, or refresh it. Returns a summary. Commits."""
    tenant = db.scalar(select(Tenant).where(Tenant.email == DEMO_TENANT_EMAIL))
    created = tenant is None
    if tenant is None:
        tenant = Tenant(name=DEMO_TENANT_NAME, short=DEMO_TENANT_SHORT,
                        address="Unit 1902, Boulevard Plaza Tower 1, Downtown Dubai, UAE",
                        trn="TRN 100-9988-7766-554", phone="+971 4 000 0000",
                        email=DEMO_TENANT_EMAIL, accent="#14606B", demo=True)
        db.add(tenant)
        db.flush()
    else:
        if not tenant.demo:
            raise RuntimeError(
                f"Tenant {tenant.name} ({tenant.id}) already owns {DEMO_TENANT_EMAIL} but is not "
                f"flagged demo. Refusing to touch it — a real firm may be sitting on that address."
            )
        existing = db.scalar(select(User).where(User.tenant_id == tenant.id).limit(1))
        if existing is not None and not reset:
            raise RuntimeError(
                f"The demo tenant already has data. Re-run with --reset to rebuild it "
                f"(that deletes the demo firm's rows and nothing else)."
            )

    wiped = wipe_demo_data(db, tenant) if reset and not created else {}

    tenant.name, tenant.short = DEMO_TENANT_NAME, DEMO_TENANT_SHORT
    tenant.services = SERVICES
    tenant.templates = {"proposal": {"footer": "Baton Demo Co — sample data, fictitious throughout."}}
    tenant.demo = True

    # active + no period end = never expires. A trial would silently lock the published
    # logins out 37 days from now (security.subscription_blocked: expiry + 7-day grace).
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant.id))
    if sub is None:
        sub = Subscription(tenant_id=tenant.id)
        db.add(sub)
    sub.plan_name, sub.status = "Demo", "active"
    sub.seats_limit = max(20, len(DEMO_USERS))
    sub.current_period_end = None
    sub.notes = "Portfolio demo firm — never expires."
    db.flush()

    u = _make_users(db, tenant)
    _seed_proposals(db, tenant, u)
    p4, client1 = _seed_won_engagement(db, tenant, u)
    _seed_onboarding(db, tenant, u, p4, client1)
    built = _seed_clients_and_duties(db, tenant, u, client1)
    vat_ok = _seed_vat_filing(db, tenant, u, built["duties"]["vat"], built["gulf"])

    db.commit()
    return {
        "tenant_id": str(tenant.id), "created": created, "wiped": wiped,
        "users": [{"email": e, "role": r, "password": DEMO_PASSWORD}
                  for _, _, _, e, r, _ in DEMO_USERS],
        "proposals": 4, "clients": 3, "duties": len(built["duties"]),
        "onboardings": 1, "vat_filing": vat_ok,
    }


def demo_status(db: Session) -> dict:
    tenant = db.scalar(select(Tenant).where(Tenant.email == DEMO_TENANT_EMAIL))
    if tenant is None:
        return {"exists": False}
    counts = {}
    for table in ("users", "clients", "proposals", "duties", "onboardings", "payments"):
        counts[table] = db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"),  # noqa: S608
                                   {"t": str(tenant.id)}).scalar()
    return {"exists": True, "tenant_id": str(tenant.id), "demo_flag": tenant.demo, "counts": counts}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Seed the Baton demo tenant.")
    parser.add_argument("--reset", action="store_true",
                        help="wipe the demo tenant's data first, then rebuild")
    parser.add_argument("--status", action="store_true", help="report what exists, change nothing")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from .db import SessionLocal
    db = SessionLocal()
    try:
        if args.status:
            print(demo_status(db))
            return 0
        out = seed_demo(db, reset=args.reset)
    except RuntimeError as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        return 2
    finally:
        db.close()

    print(f"\n  {DEMO_TENANT_NAME} {'created' if out['created'] else 're-seeded'} "
          f"— tenant {out['tenant_id']}")
    if out["wiped"]:
        print(f"  wiped: {', '.join(f'{k}={v}' for k, v in out['wiped'].items())}")
    print(f"  {out['proposals']} proposals · {out['clients']} clients · {out['duties']} duties · "
          f"{out['onboardings']} onboarding · VAT filing: {'yes' if out['vat_filing'] else 'skipped'}")
    print("\n  Logins (all share one password):\n")
    for row in out["users"]:
        print(f"    {row['role']:<11} {row['email']:<32} {row['password']}")
    print("\n  Outbound email from this tenant is suppressed (app/demo.py).\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
