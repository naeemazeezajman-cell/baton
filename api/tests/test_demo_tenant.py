"""The demo tenant: isolation, email suppression, and the write-guards.

The isolation tests are the point of this file. "Baton Demo Co" has published credentials,
so its Admin is effectively an anonymous internet user holding the most powerful role in the
product — and it shares a database with every real firm. These tests assert, across every
resource type the API exposes, that the boundary holds in both directions:

  * a demo user cannot read or write any other tenant's rows, and
  * no other tenant can read or write the demo firm's rows.

They also pin the thing that makes the whole arrangement safe to reason about: the demo flag
narrows what a user may do, and never widens what a user may see.
"""

import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.demo_seed import DEMO_PASSWORD, DEMO_TENANT_EMAIL, seed_demo
from app.models import Client, Duty, File, Onboarding, Payment, Proposal, Tenant, User
from app.routers.vat_engine import VatClientProfile, VatFiling

from .conftest import bootstrap_tenant, login_after_reset

DEMO_ADMIN = "demo.admin@batondemo.co"
DEMO_MANAGER = "demo.manager@batondemo.co"
DEMO_STAFF = "demo.staff@batondemo.co"


@pytest.fixture
def demo(client):
    """Seed the demo firm and hand back its ids plus a logged-in Admin header."""
    db = SessionLocal()
    try:
        seed_demo(db, reset=False)
        tenant = db.scalar(select(Tenant).where(Tenant.email == DEMO_TENANT_EMAIL))
        ids = {
            "tenant_id": tenant.id,
            "user": db.scalar(select(User.id).where(User.tenant_id == tenant.id)),
            "client": db.scalar(select(Client.id).where(Client.tenant_id == tenant.id)),
            "proposal": db.scalar(select(Proposal.id).where(Proposal.tenant_id == tenant.id)),
            "duty": db.scalar(select(Duty.id).where(Duty.tenant_id == tenant.id)),
            "onboarding": db.scalar(select(Onboarding.id).where(Onboarding.tenant_id == tenant.id)),
            "payment": db.scalar(select(Payment.id).where(Payment.tenant_id == tenant.id)),
            "file": db.scalar(select(File.id).where(File.tenant_id == tenant.id)),
            "filing": db.scalar(select(VatFiling.id).where(VatFiling.tenant_id == tenant.id)),
            "vat_client": db.scalar(select(VatClientProfile.client_id)
                                    .where(VatClientProfile.tenant_id == tenant.id)),
        }
        assert all(v is not None for v in ids.values()), \
            f"the seed left a resource type empty, so this file would not really test it: {ids}"
    finally:
        db.close()
    r = client.post("/auth/login", json={"email": DEMO_ADMIN, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    ids["headers"] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return ids


@pytest.fixture
def real_and_demo(client):
    """Two fully-populated firms, exactly one of them flagged demo.

    Built by seeding, demoting that firm to an ordinary tenant (renaming it out of the way),
    then seeding a fresh demo firm. The point is the demo→real direction: a bootstrapped
    tenant only owns users, clients and duties, so it cannot answer "can a demo user reach a
    real firm's onboarding / VAT filing / file / payment?" — the direction that actually
    matters when the demo password is printed on a CV. Both firms here own one of everything.
    """
    db = SessionLocal()
    try:
        seed_demo(db, reset=False)
        real = db.scalar(select(Tenant).where(Tenant.email == DEMO_TENANT_EMAIL))
        real.demo = False
        real.name, real.short = "Ledgerline Advisory LLC", "Ledgerline"
        real.email = "hello@ledgerline.ae"
        for u in db.scalars(select(User).where(User.tenant_id == real.id)):
            u.email = u.email.replace("@batondemo.co", "@ledgerline.ae")
        db.commit()
        assert real.demo is False

        ids = {
            "tenant_id": real.id,
            "user": db.scalar(select(User.id).where(User.tenant_id == real.id)),
            "client": db.scalar(select(Client.id).where(Client.tenant_id == real.id)),
            "proposal": db.scalar(select(Proposal.id).where(Proposal.tenant_id == real.id)),
            "duty": db.scalar(select(Duty.id).where(Duty.tenant_id == real.id)),
            "onboarding": db.scalar(select(Onboarding.id).where(Onboarding.tenant_id == real.id)),
            "payment": db.scalar(select(Payment.id).where(Payment.tenant_id == real.id)),
            "file": db.scalar(select(File.id).where(File.tenant_id == real.id)),
            "filing": db.scalar(select(VatFiling.id).where(VatFiling.tenant_id == real.id)),
            # the client that actually owns the VAT profile — NOT the tenant's first client,
            # or the profile read would 404 for want of a profile and prove nothing
            "vat_client": db.scalar(select(VatClientProfile.client_id)
                                    .where(VatClientProfile.tenant_id == real.id)),
        }
        assert all(v is not None for v in ids.values()), f"the 'real' firm is not populated: {ids}"

        seed_demo(db, reset=False)  # a fresh demo firm alongside it
        demo_tenant = db.scalar(select(Tenant).where(Tenant.email == DEMO_TENANT_EMAIL))
        assert demo_tenant.demo is True and demo_tenant.id != real.id
        ids["demo_tenant_id"] = demo_tenant.id
    finally:
        db.close()

    r = client.post("/auth/login", json={"email": DEMO_ADMIN, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    ids["demo_headers"] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return ids


def test_demo_user_cannot_touch_a_populated_real_firm_across_every_resource(client, real_and_demo):
    """The direction that matters: demo Admin (public password, strongest role) against a real
    firm that owns one of every resource type."""
    real = real_and_demo
    h = real["demo_headers"]

    reads = [
        (f"/users/{real['user']}", "real user"),
        (f"/proposals/{real['proposal']}", "real proposal"),
        (f"/proposals/{real['proposal']}/report", "real proposal report"),
        (f"/clients/{real['client']}/documents", "real client documents"),
        (f"/clients/{real['client']}/performance", "real client performance"),
        (f"/onboardings/{real['onboarding']}", "real onboarding"),
        (f"/payments/health/{real['client']}", "real payment health"),
        (f"/files/{real['file']}/link", "real file link"),
        (f"/vat-engine/filings/{real['filing']}", "real vat filing"),
        (f"/vat-engine/filings/{real['filing']}/recon-workbook", "real vat recon workbook"),
        (f"/vat-engine/clients/{real['vat_client']}/profile", "real vat client profile"),
    ]
    for path, what in reads:
        _assert_blocked(client.get(path, headers=h), f"GET {what}")

    # Positive control. Without this the 404s above would prove nothing — a typo'd URL or a
    # dead id 404s just as cheerfully as a tenant check. The real firm's own Admin must see
    # every one of those exact URLs, which pins the 404s to tenancy and nothing else.
    r = client.post("/auth/login", json={"email": "demo.admin@ledgerline.ae", "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    owner = {"Authorization": f"Bearer {r.json()['access_token']}"}
    # Not "== 200": some of these carry their own business rules (a proposal report 409s until
    # the matter is far enough along). 404 is the only status that means "no such row in your
    # tenant", so the owner merely has to not get one.
    for path, what in reads:
        rr = client.get(path, headers=owner)
        assert rr.status_code != 404, \
            f"{what}: the owning tenant also got 404 — the 404 above was not about tenancy"

    writes = [
        ("patch", f"/users/{real['user']}", {"name": "Hacked"}, "rename real user"),
        ("post", f"/users/{real['user']}/deactivate", None, "deactivate real user"),
        ("post", f"/proposals/{real['proposal']}/chat", {"text": "hi"}, "chat on real proposal"),
        ("post", f"/proposals/{real['proposal']}/mark-lost", {"reason": "x"}, "mark real proposal lost"),
        ("patch", f"/clients/{real['client']}/contact", {"email": "x@y.ae"}, "edit real client contact"),
        ("post", f"/onboardings/{real['onboarding']}/items",
         {"items": [{"label": "x", "kind": "document"}]}, "add item to real onboarding"),
        ("post", f"/vat-engine/filings/{real['filing']}/reopen-reconciliation", {"reason": "x"},
         "reopen real reconciliation"),
        ("post", f"/vat-engine/clients/{real['vat_client']}/profile",
         {"business_category": "Trading", "flags": {}}, "create profile on real client"),
    ]
    for verb, path, body, what in writes:
        r = getattr(client, verb)(path, json=body, headers=h) if body is not None \
            else getattr(client, verb)(path, headers=h)
        _assert_blocked(r, f"{verb.upper()} {what}")

    _assert_blocked(
        client.post(f"/payments/{real['payment']}/raise-invoice",
                    data={"invoice_number": "INV-HACK", "declared_reason": "x"}, headers=h),
        "POST raise invoice on real payment")
    _assert_blocked(
        client.post(f"/duties/{real['duty']}/complete",
                    data={"method": "declared", "reason": "x"}, headers=h),
        "POST complete real duty")


def test_demo_lists_never_include_a_real_firms_rows(client, real_and_demo):
    """Including /performance/*, which had no cross-tenant coverage at all before this."""
    h = real_and_demo["demo_headers"]
    for path in ("/users", "/proposals", "/duties", "/onboardings", "/clients", "/payments",
                 "/vat-engine/filings", "/notices", "/performance/employees",
                 "/performance/pending", "/performance/config"):
        r = client.get(path, headers=h)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:140]}"
        assert "ledgerline" not in r.text.lower(), f"{path} leaked the real firm's rows"
        assert "Ledgerline Advisory" not in r.text, f"{path} leaked the real firm's name"


def test_real_firm_lists_never_include_demo_rows(client, real_and_demo):
    """The mirror image, from the real firm's side."""
    r = client.post("/auth/login", json={"email": "demo.admin@ledgerline.ae",
                                         "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    for path in ("/users", "/proposals", "/duties", "/onboardings", "/clients", "/payments",
                 "/vat-engine/filings", "/notices", "/performance/employees",
                 "/performance/pending", "/performance/config"):
        r = client.get(path, headers=h)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:140]}"
        assert "batondemo.co" not in r.text, f"{path} leaked demo rows into a real firm"
        assert "Baton Demo Co" not in r.text, f"{path} leaked the demo firm name"


def _other_tenant(client):
    """A normal (non-demo) firm, with its Admin logged in and one proposal created."""
    boot = bootstrap_tenant(client, email="hello@betabooks.ae", user_email_domain="betabooks.ae")
    admin = next(u for u in boot["users"] if u["role"] == "Admin")
    tokens = login_after_reset(client, admin["email"], admin["temp_password"], "Beta-pass!1")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    r = client.post("/proposals", json={
        "prospect": {"name": "Northwind Trading LLC", "email": "ap@northwind.ae"},
        "services": [{"name": "VAT Filing", "fee": "3000"}],
        "assigned_to": boot["users"][1]["id"],
    }, headers=headers)
    assert r.status_code == 201, r.text
    db = SessionLocal()
    try:
        tid = uuid.UUID(boot["users"][0]["id"])
        tenant_id = db.get(User, tid).tenant_id
        ids = {
            "tenant_id": tenant_id,
            "headers": headers,
            "user": tid,
            "proposal": uuid.UUID(r.json()["id"]),
            "client": db.scalar(select(Client.id).where(Client.tenant_id == tenant_id)),
            "duty": db.scalar(select(Duty.id).where(Duty.tenant_id == tenant_id)),
        }
    finally:
        db.close()
    return ids


def _assert_blocked(r, what):
    """Cross-tenant access must 404 (never 403 — existence must not leak, per tenancy.py)."""
    assert r.status_code == 404, f"{what}: expected 404, got {r.status_code} — {r.text[:160]}"


# ---------- direction 1: nobody else can reach the demo firm ----------

def test_other_tenant_cannot_reach_demo_data_across_every_resource(client, demo):
    """The demo firm holds one of every resource type, so this walks the whole surface."""
    other = _other_tenant(client)
    h = other["headers"]

    reads = [
        (f"/users/{demo['user']}", "user"),
        (f"/proposals/{demo['proposal']}", "proposal"),
        (f"/proposals/{demo['proposal']}/report", "proposal report"),
        (f"/clients/{demo['client']}/documents", "client documents"),
        (f"/clients/{demo['client']}/performance", "client performance"),
        (f"/onboardings/{demo['onboarding']}", "onboarding"),
        (f"/payments/health/{demo['client']}", "payment health"),
        (f"/files/{demo['file']}/link", "file link"),
        (f"/vat-engine/filings/{demo['filing']}", "vat filing"),
        (f"/vat-engine/filings/{demo['filing']}/recon-workbook", "vat recon workbook"),
        (f"/vat-engine/clients/{demo['vat_client']}/profile", "vat client profile"),
    ]
    for path, what in reads:
        _assert_blocked(client.get(path, headers=h), f"GET {what}")

    writes = [
        ("patch", f"/users/{demo['user']}", {"name": "Hacked"}, "rename demo user"),
        ("post", f"/users/{demo['user']}/deactivate", None, "deactivate demo user"),
        ("post", f"/proposals/{demo['proposal']}/chat", {"text": "hello"}, "chat on demo proposal"),
        ("post", f"/proposals/{demo['proposal']}/mark-lost", {"reason": "x"}, "mark demo proposal lost"),
        ("patch", f"/clients/{demo['client']}/contact", {"email": "x@y.ae"}, "edit demo client contact"),
        ("post", f"/onboardings/{demo['onboarding']}/items",
         {"items": [{"label": "x", "kind": "document"}]}, "add demo onboarding item"),
        ("post", f"/vat-engine/filings/{demo['filing']}/reopen-reconciliation", {"reason": "x"},
         "reopen demo reconciliation"),
    ]
    for verb, path, body, what in writes:
        r = getattr(client, verb)(path, json=body, headers=h) if body is not None \
            else getattr(client, verb)(path, headers=h)
        _assert_blocked(r, f"{verb.upper()} {what}")

    # raise-invoice is a multipart Form endpoint — send it a VALID body, or a 422 from
    # request validation would mask whether the tenant check runs at all
    _assert_blocked(
        client.post(f"/payments/{demo['payment']}/raise-invoice",
                    data={"invoice_number": "INV-HACK", "declared_reason": "x"}, headers=h),
        "POST raise demo invoice")


def test_demo_rows_never_appear_in_another_tenants_lists(client, demo):
    other = _other_tenant(client)
    h = other["headers"]
    for path in ("/users", "/proposals", "/duties", "/onboardings", "/clients", "/payments",
                 "/vat-engine/filings", "/notices", "/performance/employees",
                 "/performance/pending", "/performance/config"):
        r = client.get(path, headers=h)
        assert r.status_code == 200, f"{path}: {r.text[:120]}"
        blob = r.text
        assert "batondemo.co" not in blob, f"{path} leaked a demo email"
        assert "Baton Demo Co" not in blob, f"{path} leaked the demo firm name"
        assert "Gulf Horizon Trading LLC" not in blob, f"{path} leaked a demo client"


# ---------- direction 2: the demo firm cannot reach anybody else ----------

def test_demo_user_cannot_reach_another_tenants_data(client, demo):
    other = _other_tenant(client)
    h = demo["headers"]  # demo Admin — the strongest role, and its password is public

    for path, what in [
        (f"/users/{other['user']}", "other user"),
        (f"/proposals/{other['proposal']}", "other proposal"),
        (f"/proposals/{other['proposal']}/report", "other proposal report"),
        (f"/clients/{other['client']}/documents", "other client documents"),
        (f"/clients/{other['client']}/performance", "other client performance"),
        (f"/payments/health/{other['client']}", "other payment health"),
    ]:
        _assert_blocked(client.get(path, headers=h), f"GET {what}")

    for verb, path, body, what in [
        ("patch", f"/users/{other['user']}", {"name": "Hacked"}, "rename other user"),
        ("post", f"/users/{other['user']}/deactivate", None, "deactivate other user"),
        ("post", f"/proposals/{other['proposal']}/chat", {"text": "hi"}, "chat on other proposal"),
        ("patch", f"/clients/{other['client']}/contact", {"email": "x@y.ae"}, "edit other client"),
    ]:
        r = getattr(client, verb)(path, json=body, headers=h) if body is not None \
            else getattr(client, verb)(path, headers=h)
        _assert_blocked(r, f"{verb.upper()} {what}")


def test_demo_user_cannot_smuggle_another_tenants_client_onto_a_duty(client, demo):
    """Regression — duties.create_duty used to write body.client_id unvalidated, and
    vat_engine.serialize resolved it with a bare db.get(Client, ...). An Admin could point a
    VAT duty at a foreign clients row and read its name, ref and contact back out of the
    filing. Both ends are scoped now; this pins the write end and the read end together."""
    other = _other_tenant(client)
    h = demo["headers"]
    staff_id = str(demo["user"])

    r = client.post("/duties", json={
        "staff_id": staff_id,
        "client_name": "Looks innocent",
        "client_id": str(other["client"]),      # <- another tenant's client
        "service": "VAT Return Filing (Quarterly)",
        "cadence": "quarterly",
        "next_due": "2026-09-28T00:00:00Z",
    }, headers=h)
    _assert_blocked(r, "POST /duties with a foreign client_id")

    # and the same FK cannot be smuggled in via the duty the demo firm legitimately owns
    r = client.post("/vat-engine/filings/open", json={"duty_id": str(other["duty"])}, headers=h)
    _assert_blocked(r, "POST /vat-engine/filings/open on a foreign duty")


# ---------- the flag narrows, never widens ----------

def test_demo_flag_does_not_widen_visibility(client, demo):
    """A demo user sees exactly its own tenant — the same rule as everyone else. If the flag
    ever grew a 'demo can see more' branch, this is what would catch it."""
    other = _other_tenant(client)
    h = demo["headers"]

    r = client.get("/users", headers=h)
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()}
    assert all(e.endswith("@batondemo.co") for e in emails), emails
    assert not any("betabooks.ae" in e for e in emails)

    r = client.get("/clients", headers=h)
    assert r.status_code == 200
    assert "Northwind" not in r.text

    # the demo tenant is not special-cased in the tenant list either — there is no such endpoint,
    # and /tenants/me returns only the caller's own firm
    r = client.get("/tenants/me", headers=h)
    assert r.status_code == 200
    assert r.json()["name"] == "Baton Demo Co"


# ---------- email suppression ----------

def test_demo_tenant_email_is_suppressed_and_real_tenant_email_is_not(client, demo, caplog, monkeypatch):
    """The demo firm must never emit an outbound message; a real firm still must."""
    import app.emails as emails_mod

    delivered = []
    monkeypatch.setattr(emails_mod, "_deliver",
                        lambda *a, **k: delivered.append(a[2]))  # a[2] = recipient
    monkeypatch.setattr(emails_mod, "get_settings",
                        lambda: type("S", (), {"EMAIL_CONN": "endpoint=https://fake/;accesskey=x",
                                               "EMAIL_FROM": "DoNotReply@test"})())

    boot = bootstrap_tenant(client, email="hello@betabooks.ae", user_email_domain="betabooks.ae")
    db = SessionLocal()
    try:
        demo_tenant_id = demo["tenant_id"]
        other_tenant_id = db.get(User, uuid.UUID(boot["users"][0]["id"])).tenant_id
        # bootstrap just emailed its own invites for real — that is the correct behaviour and
        # not what this test is measuring, so start counting from here
        delivered.clear()

        sent_demo = emails_mod.send_client("stranger@example.com", "Demo says hi", "body",
                                           db=db, tenant_id=demo_tenant_id)
        sent_real = emails_mod.send_client("client@example.com", "Real invoice", "body",
                                           db=db, tenant_id=other_tenant_id)
    finally:
        db.close()

    assert sent_demo is True, "suppressed sends still report success so the caller's flow completes"
    assert sent_real is True
    assert "stranger@example.com" not in delivered, "the demo tenant reached the provider"
    assert delivered == ["client@example.com"], f"real tenant delivery broke: {delivered}"


def test_demo_proposal_send_does_not_email_the_recipient(client, demo, monkeypatch):
    """End-to-end: driving the product's own 'send to client' from a demo login reaches no one."""
    import app.emails as emails_mod
    delivered = []
    monkeypatch.setattr(emails_mod, "_deliver", lambda *a, **k: delivered.append(a[2]))
    monkeypatch.setattr(emails_mod, "get_settings",
                        lambda: type("S", (), {"EMAIL_CONN": "endpoint=https://fake/;accesskey=x",
                                               "EMAIL_FROM": "DoNotReply@test"})())

    r = client.post("/auth/login", json={"email": DEMO_MANAGER, "password": DEMO_PASSWORD})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    db = SessionLocal()
    try:
        p = db.scalar(select(Proposal).where(Proposal.tenant_id == demo["tenant_id"],
                                             Proposal.status == "signed"))
        pid = p.id if p else None
    finally:
        db.close()
    if pid is None:  # the seed parks P-003 at proposal_sent, past 'signed' — invite instead
        r = client.post("/users", json={"name": "Nosy Visitor", "email": "victim@example.com",
                                        "role": "Staff"},
                        headers=demo["headers"])
        assert r.status_code == 201, r.text
        assert delivered == [], f"a demo invite reached the provider: {delivered}"
        return
    r = client.post(f"/proposals/{pid}/send-client",
                    json={"to": "victim@example.com", "subject": "s", "body": "b"}, headers=h)
    assert r.status_code in (200, 409)
    assert delivered == [], f"a demo proposal send reached the provider: {delivered}"


# ---------- write-guards ----------

def test_demo_credentials_cannot_be_changed_or_retired(client, demo):
    h = demo["headers"]

    r = client.post("/auth/reset-password", json={"new_password": "hijacked-123"}, headers=h)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "DEMO_READ_ONLY"

    # ...and the original password still works, which is the property that actually matters
    r = client.post("/auth/login", json={"email": DEMO_ADMIN, "password": DEMO_PASSWORD})
    assert r.status_code == 200

    db = SessionLocal()
    try:
        staff_id = db.scalar(select(User.id).where(User.email == DEMO_STAFF))
    finally:
        db.close()

    for verb, path, body in [
        ("post", f"/users/{staff_id}/deactivate", None),
        ("patch", f"/users/{staff_id}", {"role": "Admin"}),
        ("post", f"/users/{staff_id}/resend-invite", None),
    ]:
        r = getattr(client, verb)(path, json=body, headers=h) if body is not None \
            else getattr(client, verb)(path, headers=h)
        assert r.status_code == 403, f"{verb.upper()} {path}: {r.status_code} {r.text[:120]}"
        assert r.json()["detail"]["code"] == "DEMO_READ_ONLY"

    r = client.post("/auth/login", json={"email": DEMO_STAFF, "password": DEMO_PASSWORD})
    assert r.status_code == 200, "the demo staff login survived every attempt to break it"


def test_real_tenants_keep_their_password_and_roster_controls(client):
    """The guards are demo-only — a real firm's admin must be unaffected."""
    boot = bootstrap_tenant(client)
    admin = next(u for u in boot["users"] if u["role"] == "Admin")
    tokens = login_after_reset(client, admin["email"], admin["temp_password"], "Real-pass!1")
    h = {"Authorization": f"Bearer {tokens['access_token']}"}

    r = client.post("/auth/reset-password", json={"new_password": "Another-pass!2"}, headers=h)
    assert r.status_code == 200, r.text

    staff = next(u for u in boot["users"] if u["role"] == "Staff")
    r = client.post(f"/users/{staff['id']}/deactivate", headers=h)
    assert r.status_code == 200, r.text


# ---------- the seed itself ----------

def test_seeded_logins_work_and_need_no_reset(client, demo):
    for email in (DEMO_ADMIN, DEMO_MANAGER, DEMO_STAFF):
        r = client.post("/auth/login", json={"email": email, "password": DEMO_PASSWORD})
        assert r.status_code == 200, f"{email}: {r.text}"
        assert r.json()["must_reset"] is False, f"{email} would be forced through a reset"


def test_demo_subscription_never_expires(client, demo):
    """A trial would silently lock the published logins out 37 days after seeding."""
    r = client.get("/tenants/me", headers=demo["headers"])
    assert r.status_code == 200
    sub = r.json()["subscription"]
    assert sub["status"] == "active"
    assert sub["current_period_end"] is None


def test_seed_is_idempotent_and_refuses_to_wipe_a_real_tenant(client):
    db = SessionLocal()
    try:
        seed_demo(db, reset=False)
        with pytest.raises(RuntimeError, match="already has data"):
            seed_demo(db, reset=False)
        out = seed_demo(db, reset=True)  # the supported way to rebuild
        assert out["created"] is False
        assert out["wiped"]

        # the wipe guard: a tenant that is not flagged demo is untouchable
        from app.demo_seed import wipe_demo_data
        real = Tenant(name="Real Firm", short="Real", email="hello@realfirm.ae")
        db.add(real)
        db.flush()
        with pytest.raises(RuntimeError, match="tenants.demo is false"):
            wipe_demo_data(db, real)
        db.rollback()
    finally:
        db.close()
