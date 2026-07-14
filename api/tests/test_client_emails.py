"""Client-facing emails: replies route to the acting staff member (Reply-To + body line),
and documents are attached as real files rather than SAS download links."""

import base64

from app import emails

from .test_duties_payments import complete, make_duty
from .test_onboarding import create_proposal, drive_to_signed, setup_firm


def _capture(monkeypatch):
    """Force the provider branch and capture every delivery's args (no real ACS call)."""
    sent = []
    real = emails.get_settings()
    monkeypatch.setattr(
        emails, "get_settings",
        lambda: real.model_copy(update={"EMAIL_CONN": "endpoint=https://x.communication.azure.com/;accesskey=bogus"}),
    )
    monkeypatch.setattr(
        emails, "_deliver",
        lambda sender, conn, to, subject, body, reply_to=None, attachments=None: sent.append(
            {"sender": sender, "to": to, "subject": subject, "body": body,
             "reply_to": reply_to, "attachments": attachments}),
    )
    return sent


def test_deliver_builds_acs_message_with_replyto_and_attachments(monkeypatch):
    """_deliver assembles the exact ACS message shape: replyTo address + base64 attachments."""
    captured = {}

    class FakeClient:
        @classmethod
        def from_connection_string(cls, conn):
            return cls()

        def begin_send(self, message):
            captured["msg"] = message

    import azure.communication.email as ace
    monkeypatch.setattr(ace, "EmailClient", FakeClient)

    emails._deliver(
        "DoNotReply@baton.net", "conn", "client@x.ae", "Subject", "Body text",
        reply_to=("staff@firm.ae", "Staff Member"),
        attachments=[{"name": "doc.pdf", "contentType": "application/pdf", "contentInBase64": "QUJD"}],
    )
    m = captured["msg"]
    assert m["senderAddress"] == "DoNotReply@baton.net"          # From stays the technical sender
    assert m["replyTo"] == [{"address": "staff@firm.ae", "displayName": "Staff Member"}]
    assert m["attachments"] == [{"name": "doc.pdf", "contentType": "application/pdf", "contentInBase64": "QUJD"}]


def test_duty_deliverable_email_attaches_file_and_sets_reply_to(client, monkeypatch):
    ctx = setup_firm(client)
    sent = _capture(monkeypatch)
    d = make_duty(client, ctx, cadence="one-time", days_until_due=5,
                  service="VAT Filing", client_name="Al Dana")
    complete(client, ctx, d["id"], "sent",
             files=[("VAT Return Q2.pdf", b"%PDF-1.4 filed return bytes")],
             emailed_to="client@aldana.ae")

    assert len(sent) == 1
    msg = sent[0]
    assert msg["to"] == "client@aldana.ae"
    # Reply-To routes to the acting staff member (Priya Nair), and the body names them
    assert msg["reply_to"] == ("priya@alphaledger.ae", "Priya Nair")
    assert "reply to this email to reach Priya Nair directly at priya@alphaledger.ae" in msg["body"]
    # the actual filed return is attached and decodes back to the uploaded bytes — no link
    assert msg["attachments"] and len(msg["attachments"]) == 1
    att = msg["attachments"][0]
    assert att["name"] == "VAT Return Q2.pdf"
    assert att["contentType"] == "application/pdf"
    assert base64.b64decode(att["contentInBase64"]) == b"%PDF-1.4 filed return bytes"
    assert "download" not in msg["body"].lower() and "http" not in msg["body"]


def test_invoice_email_attaches_pdf_and_reply_to(client, monkeypatch):
    """The accountant raises an invoice — the invoice PDF is attached and replies route to
    the accountant who raised it."""
    from .test_duties_payments import el_sent_payments
    ctx = setup_firm(client)
    _, out = el_sent_payments(client, ctx)
    pay = next(p for p in out["payments"] if "VAT Filing" in p["label"])

    sent = _capture(monkeypatch)
    r = client.post(f"/payments/{pay['id']}/raise-invoice",
                    data={"invoice_number": "INV-001", "invoice_date": "2026-07-14"},
                    files=[("invoice", ("Invoice INV-001.pdf", b"%PDF-1.4 invoice", "application/pdf"))],
                    headers=ctx["accountant"]["headers"])
    assert r.status_code == 200, r.text
    assert len(sent) == 1
    msg = sent[0]
    # Fatima Zahran is the in-house accountant who raised it
    assert msg["reply_to"] == ("fatima@alphaledger.ae", "Fatima Zahran")
    assert "reach Fatima Zahran directly at fatima@alphaledger.ae" in msg["body"]
    att = msg["attachments"][0]
    assert att["name"] == "Invoice INV-001.pdf" and att["contentType"] == "application/pdf"
    assert base64.b64decode(att["contentInBase64"]) == b"%PDF-1.4 invoice"


def test_proposal_send_sets_reply_to_acting_manager(client, monkeypatch):
    ctx = setup_firm(client)
    pid = create_proposal(client, ctx)["id"]
    drive_to_signed(client, ctx, pid)
    sent = _capture(monkeypatch)
    r = client.post(f"/proposals/{pid}/send-client",
                    json={"to": "accounts@gulfhorizon.ae", "subject": "Your proposal",
                          "body": "Please find our proposal."},
                    headers=ctx["manager"]["headers"])
    assert r.status_code == 200, r.text
    assert len(sent) == 1
    # replies route to the requesting manager, named in the body
    assert sent[0]["reply_to"] == ("rashid@alphaledger.ae", "Rashid Al Mansoori")
    assert "reach Rashid Al Mansoori directly at rashid@alphaledger.ae" in sent[0]["body"]
    # no server-side proposal PDF exists to attach, and none was requested → body-only
    assert not sent[0]["attachments"]
