"""Email delivery must be non-fatal to core operations. The bug: with EMAIL_CONN set,
a failing/misconfigured email client 500'd POST /platform/firms (and would break proposal
sends, invites and reminders too). These tests drive the real endpoints while the email
provider raises, and assert the core action still succeeds and persists."""

from app import emails

from .test_onboarding import create_proposal, drive_to_signed, setup_firm
from .test_platform import CREATE_FIRM_PAYLOAD, op_headers


def _force_email_failure(monkeypatch):
    """Reproduce a broken EMAIL_CONN in prod: force _send down the provider branch and make
    the provider hand-off raise. _send must swallow it — nothing here may propagate."""
    real = emails.get_settings()
    monkeypatch.setattr(
        emails, "get_settings",
        lambda: real.model_copy(update={"EMAIL_CONN": "endpoint=https://x.communication.azure.com/;accesskey=bogus"}),
    )

    def boom(*args, **kwargs):
        raise RuntimeError("Azure Communication Services unreachable")

    monkeypatch.setattr(emails, "_deliver", boom)


def test_email_send_reports_failure_instead_of_raising(monkeypatch):
    _force_email_failure(monkeypatch)
    # every public send returns False on failure and never raises
    assert emails.send_invite("x@y.ae", "X", "Firm", "http://link", "temp123") is False
    assert emails.send_client("x@y.ae", "subject", "body") is False
    assert emails.send_reset("x@y.ae", "X", "http://link") is False


def test_create_firm_succeeds_when_email_client_raises(client, monkeypatch):
    op = op_headers(client)
    _force_email_failure(monkeypatch)
    r = client.post("/platform/firms", json=CREATE_FIRM_PAYLOAD, headers=op)
    assert r.status_code == 201, r.text
    out = r.json()
    # the firm + admin were created and the temp password issued despite the failed invite
    assert out["users"] and all(u["temp_password"] for u in out["users"])
    # and it truly persisted — it appears on the firm list
    firms = client.get("/platform/firms", headers=op).json()
    assert any(f["name"] == CREATE_FIRM_PAYLOAD["firm"]["name"] for f in firms)
    # the created admin can log in — the account is real, not half-written and rolled back
    from .conftest import login_after_reset
    admin = next(u for u in out["users"] if u["role"] == "Admin")
    assert login_after_reset(client, admin["email"], admin["temp_password"])["access_token"]


def test_proposal_send_succeeds_when_email_client_raises(client, monkeypatch):
    ctx = setup_firm(client)
    pid = create_proposal(client, ctx)["id"]
    drive_to_signed(client, ctx, pid)
    _force_email_failure(monkeypatch)
    r = client.post(
        f"/proposals/{pid}/send-client",
        json={"to": "accounts@gulfhorizon.ae", "subject": "Your proposal", "body": "Attached."},
        headers=ctx["manager"]["headers"],
    )
    assert r.status_code == 200, r.text
    # the send advanced the matter despite the failed email — not rolled back, not 500
    assert r.json()["status"] == "proposal_sent"
    # re-fetch confirms the transition committed
    got = client.get(f"/proposals/{pid}", headers=ctx["manager"]["headers"]).json()
    assert got["status"] == "proposal_sent"
