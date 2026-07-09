"""Payments as proof-of-work: invoice upload + auto-email, receipt references,
accountant invoicing stars."""

import logging
import uuid as _uuid

from sqlalchemy import update

from app.db import SessionLocal
from app.models import Client, Payment
from app.routers.performance import DAY_MS, invoicing_stars

from .test_duties_payments import el_sent_payments
from .test_onboarding import setup_firm


def raise_inv(client, ctx, pay_id, expect=200, files=None, **fields):
    data = {"invoice_number": "INV-7001", **fields}
    file_parts = [("invoice", (n, c, "application/pdf")) for n, c in (files or [])]
    r = client.post(f"/payments/{pay_id}/raise-invoice", data=data, files=file_parts,
                    headers=ctx["accountant"]["headers"])
    assert r.status_code == expect, f"raise-invoice: {r.status_code} {r.text}"
    return r.json()


def test_invoicing_star_boundaries_and_declared_cap():
    assert invoicing_stars(-2 * DAY_MS, False) == 5   # raised early
    assert invoicing_stars(0, False) == 5
    assert invoicing_stars(DAY_MS, False) == 4
    assert invoicing_stars(3 * DAY_MS, False) == 3
    assert invoicing_stars(7 * DAY_MS, False) == 2
    assert invoicing_stars(7 * DAY_MS + 1, False) == 1
    assert invoicing_stars(0, True) == 3              # declared caps at 3
    assert invoicing_stars(8 * DAY_MS, True) == 1     # cap never improves a worse score


def test_raise_invoice_validation_and_auto_email(client, caplog):
    ctx = setup_firm(client)
    _, out = el_sent_payments(client, ctx)
    pay = next(p for p in out["payments"] if "VAT Filing" in p["label"])

    # validation: number and file are mandatory
    r = client.post(f"/payments/{pay['id']}/raise-invoice", data={"invoice_number": "  "},
                    files={"invoice": ("i.pdf", b"%PDF", "application/pdf")},
                    headers=ctx["accountant"]["headers"])
    assert r.status_code == 422
    raise_inv(client, ctx, pay["id"], expect=422)  # no file, not declared

    with caplog.at_level(logging.INFO, logger="baton.emails"):
        p = raise_inv(client, ctx, pay["id"], invoice_number="INV-7001",
                      files=[("INV-7001.pdf", b"%PDF invoice")])
    assert p["invoice_raised"] is True and p["invoice_raised_at"]
    assert p["invoice_number"] == "INV-7001" and len(p["invoice_files"]) == 1
    assert p["lifecycle"] == "overdue"  # due now, unpaid — invoiced but past due
    assert any("Invoice INV-7001 raised and emailed to accounts@gulfhorizon.ae" in e["text"]
               for e in p["events"])
    mail = caplog.text
    assert "AlphaLedger — Invoice INV-7001" in mail
    assert "INV-7001.pdf:" in mail and "token=" in mail  # secure link, local dev mode
    assert "due" in mail and "Kindly arrange payment" in mail

    # invoiced-but-unpaid still drives health (both overdue classes count)
    health = client.get(f"/payments/health/{p['client_id']}", headers=ctx["accountant"]["headers"]).json()
    assert health["badge"] == "Watch" and health["overdue_count"] >= 1


def test_raise_invoice_no_contact_409_then_patch(client):
    ctx = setup_firm(client)
    _, out = el_sent_payments(client, ctx)
    pay = out["payments"][0]
    # strip the contact email
    cid = _uuid.UUID(out["proposal"]["client_id"])
    with SessionLocal() as db:
        c = db.get(Client, cid)
        c.contact = {"name": c.name}
        db.commit()
    r = client.post(f"/payments/{pay['id']}/raise-invoice", data={"invoice_number": "INV-1"},
                    files={"invoice": ("i.pdf", b"%PDF", "application/pdf")},
                    headers=ctx["accountant"]["headers"])
    assert r.status_code == 409 and "No contact email on file" in r.json()["detail"]["reason"]

    # accountant PATCHes the contact, then the raise succeeds
    r = client.patch(f"/clients/{cid}/contact", json={"email": "billing@gulfhorizon.ae"},
                     headers=ctx["accountant"]["headers"])
    assert r.status_code == 200 and r.json()["contact"]["email"] == "billing@gulfhorizon.ae"
    # staff may not patch contacts
    assert client.patch(f"/clients/{cid}/contact", json={"email": "x@y.ae"},
                        headers=ctx["staff"]["headers"]).status_code == 403
    p = raise_inv(client, ctx, pay["id"], invoice_number="INV-1",
                  files=[("i.pdf", b"%PDF")])
    assert any("emailed to billing@gulfhorizon.ae" in e["text"] for e in p["events"])


def test_declared_invoice_fallback(client, caplog):
    ctx = setup_firm(client)
    _, out = el_sent_payments(client, ctx)
    pay = out["payments"][0]
    with caplog.at_level(logging.INFO, logger="baton.emails"):
        p = raise_inv(client, ctx, pay["id"], invoice_number="INV-9",
                      declared_reason="Raised in Zoho before Baton go-live")
    assert p["invoice_raised"] is True and p["invoice_declared"] is True
    assert p["invoice_files"] == []
    assert any("DECLARED raised outside Baton" in e["text"]
               and "Zoho" in e["text"] for e in p["events"])
    assert "Invoice INV-9" not in caplog.text  # no client email on the declared path


def test_receipt_reference_rules(client):
    ctx = setup_firm(client)
    _, out = el_sent_payments(client, ctx)
    pay = out["payments"][0]
    raise_inv(client, ctx, pay["id"], files=[("i.pdf", b"%PDF")])
    acct = ctx["accountant"]["headers"]

    # bank transfer without a reference → 422
    r = client.post(f"/payments/{pay['id']}/record-receipt",
                    data={"amount": "1000", "method": "bank_transfer"}, headers=acct)
    assert r.status_code == 422
    # unknown method → 422
    r = client.post(f"/payments/{pay['id']}/record-receipt",
                    data={"amount": "1000", "method": "barter", "reference": "x"}, headers=acct)
    assert r.status_code == 422
    # cash without a note → 422; with a note → OK, event carries the note
    r = client.post(f"/payments/{pay['id']}/record-receipt",
                    data={"amount": "1000", "method": "cash"}, headers=acct)
    assert r.status_code == 422
    r = client.post(f"/payments/{pay['id']}/record-receipt",
                    data={"amount": "1000", "method": "cash", "note": "collected at client office"},
                    headers=acct)
    assert r.status_code == 200
    assert any("by cash" in e["text"] and "collected at client office" in e["text"]
               for e in r.json()["events"])
    # bank transfer with reference → event carries method + ref
    r = client.post(f"/payments/{pay['id']}/record-receipt",
                    data={"amount": "500", "method": "bank_transfer", "reference": "FT-2231"}, headers=acct)
    assert any("by bank transfer, ref FT-2231" in e["text"] for e in r.json()["events"])
    assert r.json()["lifecycle"] in ("overdue", "partially_received")


def test_accountant_invoicing_in_performance(client):
    ctx = setup_firm(client)
    _, out = el_sent_payments(client, ctx)
    pays = out["payments"]
    monthly = next(p for p in pays if "Bookkeeping" in p["label"])   # due +30d → raised early → 5★
    quarterly = next(p for p in pays if "VAT Filing" in p["label"])  # due now → declared → capped 3★
    raise_inv(client, ctx, monthly["id"], invoice_number="INV-100", files=[("a.pdf", b"%PDF")])
    raise_inv(client, ctx, quarterly["id"], invoice_number="INV-101",
              declared_reason="raised in legacy system")

    r = client.get("/performance/employees", headers=ctx["manager"]["headers"]).json()
    fatima = next(e for e in r["employees"] if e["name"] == "Fatima Zahran")
    assert fatima["invoicing_count"] == 2
    assert abs(fatima["invoicing_avg_stars"] - 4.0) < 1e-9  # (5 + 3) / 2
    assert fatima["proposal_count"] == 0 and fatima["proposal_avg_stars"] is None
    assert fatima["duty_count"] == 0 and fatima["duties_avg_stars"] is None
    assert abs(fatima["overall_avg"] - 4.0) < 1e-9  # invoicing events are her only stars
    labels = [e["label"] for e in fatima["recent_events"]]
    assert any(l.startswith("Invoice INV-100") for l in labels)
    assert all(e["source"] == "invoicing" for e in fatima["recent_events"])
    assert "declared raised outside Baton capped" in r["invoicing_stars_scale_text"]
