"""Phase 3 — duties engine, payments, daily digest, chat + notices."""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Payment
from app.scheduler import run_daily_digest

from .conftest import bootstrap_tenant, login_after_reset
from .test_onboarding import act, create_proposal, drive_to_el_approved, setup_firm


def _now():
    return datetime.now(timezone.utc)


def make_duty(client, ctx, cadence="monthly", days_until_due=10, service="VAT Filing", client_name="Al Dana"):
    due = (_now() + timedelta(days=days_until_due)).isoformat()
    r = client.post("/duties", json={
        "staff_id": ctx["staff"]["id"], "client_name": client_name, "service": service,
        "cadence": cadence, "next_due": due,
        "contact": {"name": "Omar", "email": "omar@aldana.ae"},
    }, headers=ctx["manager"]["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def complete(client, ctx, duty_id, method, expect=200, files=None, **fields):
    data = {"method": method, **{k: (json.dumps(v) if k == "record" else v) for k, v in fields.items()}}
    file_parts = [("evidence", (name, content, "application/pdf")) for name, content in (files or [])]
    r = client.post(f"/duties/{duty_id}/complete", data=data, files=file_parts, headers=ctx["staff"]["headers"])
    assert r.status_code == expect, f"complete/{method}: {r.status_code} {r.text}"
    return r.json()


# ---------- cadence rollforward ----------

def test_cadence_rollforward_and_late_completion(client):
    ctx = setup_firm(client)
    d = make_duty(client, ctx, cadence="monthly", days_until_due=-10)  # 10 days OVERDUE
    original_due = datetime.fromisoformat(d["next_due"])

    out = complete(client, ctx, d["id"], "declared", reason="Client confirmed no VAT due this period")
    h = out["history"][0]
    assert h["late_ms"] > 9 * 86400000  # ~10 days late
    # next_due advanced ONE CALENDAR MONTH FROM THE DUE DATE, not from completion
    new_due = datetime.fromisoformat(out["next_due"])
    assert new_due.day == original_due.day
    assert (new_due.year, new_due.month) == (
        (original_due.year + 1, 1) if original_due.month == 12 else (original_due.year, original_due.month + 1)
    )
    assert not out["closed"]
    assert any("Late completion does not shift the schedule" in e["text"] for e in out["events"])

    # on-time completion advances again by exactly one month
    out2 = complete(client, ctx, d["id"], "declared", reason="Nil return again")
    assert out2["history"][1]["late_ms"] == 0
    due3 = datetime.fromisoformat(out2["next_due"])
    assert (due3.month - new_due.month) % 12 == 1


def test_quarterly_and_one_time_cadence(client):
    ctx = setup_firm(client)
    q = make_duty(client, ctx, cadence="quarterly", days_until_due=5)
    out = complete(client, ctx, q["id"], "declared", reason="x")
    d0 = datetime.fromisoformat(q["next_due"])
    d1 = datetime.fromisoformat(out["next_due"])
    assert (d1.month - d0.month) % 12 == 3 and not out["closed"]

    once = make_duty(client, ctx, cadence="one-time", days_until_due=5, service="Audit Support")
    out = complete(client, ctx, once["id"], "declared", reason="done in kickoff meeting")
    assert out["closed"] is True
    assert any("One-time duty closed" in e["text"] for e in out["events"])
    # completing a closed duty is refused
    complete(client, ctx, once["id"], "declared", reason="again", expect=409)


# ---------- completion validation matrix ----------

def test_completion_validation_matrix(client):
    ctx = setup_firm(client)
    pdf = [("return.pdf", b"%PDF-1.4 fta ack")]

    vat = make_duty(client, ctx, service="VAT Filing")  # kind=vat
    complete(client, ctx, vat["id"], "proof", expect=422)                                  # no files
    complete(client, ctx, vat["id"], "proof", files=pdf, expect=422)                       # no record
    complete(client, ctx, vat["id"], "proof", files=pdf, record={"period": "Q2 2026"}, expect=422)  # no position
    out = complete(client, ctx, vat["id"], "proof", files=pdf,
                   record={"period": "Q2 2026", "position": "Payable", "net VAT (AED)": "14200"})
    assert out["history"][0]["record"]["position"] == "Payable"
    assert out["history"][0]["evidence"][0]["name"] == "return.pdf"

    ct = make_duty(client, ctx, service="Corporate Tax Filing")  # kind=ct
    complete(client, ctx, ct["id"], "proof", files=pdf, record={"position": "Nil"}, expect=422)  # no FY
    complete(client, ctx, ct["id"], "proof", files=pdf,
             record={"financial year": "FY2025", "position": "Nil", "small business relief": "Yes"})

    rep = make_duty(client, ctx, service="Financial Reporting (Quarterly)")  # kind=report
    complete(client, ctx, rep["id"], "sent", files=pdf, expect=422)                        # no emailed_to
    complete(client, ctx, rep["id"], "sent", emailed_to="omar@aldana.ae", expect=422)      # no files
    out = complete(client, ctx, rep["id"], "sent", files=pdf, emailed_to="omar@aldana.ae",
                   note="Q2 management accounts")
    assert out["history"][0]["emailed_to"] == "omar@aldana.ae"
    assert any("Deliverables email dispatched" in e["text"] for e in out["events"])

    other = make_duty(client, ctx, service="ESR advisory call")  # kind=other
    complete(client, ctx, other["id"], "declared", expect=422)                             # no reason
    complete(client, ctx, other["id"], "declared", reason="Advisory call held on 05 Jul")
    complete(client, ctx, other["id"], "bogus-method", reason="x", expect=422)


# ---------- tenancy + role ----------

def test_duty_tenancy_isolation_and_ownership(client):
    ctx_a = setup_firm(client)
    d = make_duty(client, ctx_a, service="VAT Filing")

    boot_b = bootstrap_tenant(client, email="hello@betabooks.ae", user_email_domain="betabooks.ae")
    admin_b = next(u for u in boot_b["users"] if u["role"] == "Admin")
    tokens_b = login_after_reset(client, admin_b["email"], admin_b["temp_password"])
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}

    # tenant B cannot see or complete tenant A's duty (B's own bootstrap-seeded duties may exist)
    b_duties = client.get("/duties", headers=headers_b).json()
    assert d["id"] not in {x["id"] for x in b_duties}
    r = client.post(f"/duties/{d['id']}/complete", data={"method": "declared", "reason": "x"}, headers=headers_b)
    assert r.status_code == 404

    # within tenant A: only the responsible staff can complete (manager gets 409)
    r = client.post(f"/duties/{d['id']}/complete", data={"method": "declared", "reason": "x"},
                    headers=ctx_a["manager"]["headers"])
    assert r.status_code == 409
    # staff sees own duties; manager sees all
    assert len(client.get("/duties", headers=ctx_a["staff"]["headers"]).json()) == 1
    assert len(client.get("/duties", headers=ctx_a["manager"]["headers"]).json()) == 1


# ---------- payments ----------

def el_sent_payments(client, ctx):
    pid = create_proposal(client, ctx)["id"]
    drive_to_el_approved(client, ctx, pid, advance_pct=0)
    out = act(client, ctx, "manager", pid, "el-send", {"to": "a@b.ae", "subject": "EL", "body": "x"})
    return pid, out


def test_payments_receipts_and_health(client):
    ctx = setup_firm(client)
    pid, out = el_sent_payments(client, ctx)
    client_id = out["proposal"]["client_id"]
    acct = ctx["accountant"]["headers"]

    rows = client.get("/payments", headers=acct).json()
    assert len(rows) == 2
    # staff are not allowed on the payments surface
    assert client.get("/payments", headers=ctx["staff"]["headers"]).status_code == 403

    quarterly = next(p for p in rows if "VAT Filing" in p["label"])  # due now → immediately overdue
    r = client.post(f"/payments/{quarterly['id']}/invoice-raised", headers=acct)
    assert r.status_code == 200 and r.json()["invoice_raised"] is True
    client.post(f"/payments/{quarterly['id']}/invoice-raised", headers=acct).status_code == 409

    # partial receipt with evidence → not done; health Watch (overdue < 30d)
    r = client.post(f"/payments/{quarterly['id']}/record-receipt", data={"amount": "2500"},
                    files={"evidence": ("receipt1.pdf", b"%PDF r1", "application/pdf")}, headers=acct)
    body = r.json()
    assert r.status_code == 200 and body["received"] == 2500 and body["done"] is False
    health = client.get(f"/payments/health/{client_id}", headers=acct).json()
    assert health["badge"] == "Watch" and health["overdue_count"] >= 1

    # age the payment past 30 days → At risk
    with SessionLocal() as db:
        db.execute(update(Payment).where(Payment.id == quarterly["id"])
                   .values(due_at=_now() - timedelta(days=40)))
        db.commit()
    health = client.get(f"/payments/health/{client_id}", headers=acct).json()
    assert health["badge"] == "At risk"

    # settle both payments → Good; full receipt writes the 'fully received' event
    r = client.post(f"/payments/{quarterly['id']}/record-receipt", data={"amount": "3500"}, headers=acct)
    assert r.json()["done"] is True
    assert any("Fully received" in e["text"] for e in r.json()["events"])
    monthly = next(p for p in rows if "Bookkeeping" in p["label"])
    with SessionLocal() as db:  # monthly is due +30d — bring it due and pay it
        db.execute(update(Payment).where(Payment.id == monthly["id"]).values(due_at=_now() - timedelta(days=1)))
        db.commit()
    client.post(f"/payments/{monthly['id']}/record-receipt", data={"amount": "2000"}, headers=acct)
    health = client.get(f"/payments/health/{client_id}", headers=acct).json()
    assert health["badge"] == "Good" and health["outstanding"] == 0


# ---------- daily digest ----------

def test_daily_digest_idempotent(client, capsys):
    ctx = setup_firm(client)
    make_duty(client, ctx, days_until_due=-3, service="VAT Filing", client_name="Al Dana")
    make_duty(client, ctx, days_until_due=-12, service="Financial Reporting (Quarterly)", client_name="Marwa")
    el_sent_payments(client, ctx)  # creates receivables (one due immediately, unraised)

    with SessionLocal() as db:
        result = run_daily_digest(db)
    assert result["ran"] is True
    assert result["duty_digests"] == 1       # one staff member with overdue duties
    assert result["receivables_digests"] == 1  # one accountant

    # notices rows were written for the staff member and the accountant
    staff_notices = client.get("/notices", headers=ctx["staff"]["headers"]).json()
    assert any("overdue" in n["text"] for n in staff_notices)
    acct_notices = client.get("/notices", headers=ctx["accountant"]["headers"]).json()
    assert any("receivable" in n["text"] for n in acct_notices)

    # idempotent: second run the same day is a no-op
    with SessionLocal() as db:
        again = run_daily_digest(db)
    assert again["ran"] is False and "already ran" in again["reason"]


# ---------- chat + notices ----------

def test_chat_and_notices(client):
    ctx = setup_firm(client)
    p = create_proposal(client, ctx)
    pid = p["id"]

    # both sides post; events land as kind=chat; counterpart is notified
    act(client, ctx, "manager", pid, "chat", {"text": "Client wants the fee confirmed by Thursday"})
    act(client, ctx, "staff", pid, "chat", {"text": "On it — draft ready tomorrow"})
    detail = client.get(f"/proposals/{pid}", headers=ctx["manager"]["headers"]).json()
    chats = [e for e in detail["events"] if e["kind"] == "chat"]
    assert len(chats) == 2 and 'Chat: "On it' in chats[1]["text"]

    # notices accumulated from workflow actions (assignment + chat) — staff has some unread
    notices = client.get("/notices", headers=ctx["staff"]["headers"]).json()
    assert notices and all(n["read"] is False for n in notices)
    assert any("You were assigned" in n["text"] for n in notices)

    # mark one read; unread_only filter respects it
    first = notices[0]["id"]
    r = client.post(f"/notices/{first}/read", headers=ctx["staff"]["headers"])
    assert r.status_code == 200
    unread = client.get("/notices?unread_only=true", headers=ctx["staff"]["headers"]).json()
    assert all(n["id"] != first for n in unread)
    # cannot read someone else's notice
    assert client.post(f"/notices/{first}/read", headers=ctx["manager"]["headers"]).status_code == 404

    # chat is blocked once the matter is closed
    act(client, ctx, "manager", pid, "chat", {"text": "still open"})  # fine while active
    # drive to lost: need signed → proposal_sent → mark-lost
    from .test_onboarding import drive_to_signed
    drive_to_signed(client, ctx, pid)
    act(client, ctx, "manager", pid, "send-client", {"to": "a@b.ae", "subject": "P", "body": "x"})
    act(client, ctx, "manager", pid, "mark-lost", {"note": "went with another firm"})
    r = act(client, ctx, "manager", pid, "chat", {"text": "too late"}, expect=409)
    assert "read-only" in r["detail"]["reason"]
