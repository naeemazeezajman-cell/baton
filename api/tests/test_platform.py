"""Platform Operator console: operator auth + scope isolation both directions,
subscription enforcement (suspension, grace, seats), trial auto-creation, logged
subscription changes, and the hard no-tenant-content rule."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sql

from app.db import engine
from app.security import hash_password
from .conftest import bootstrap_tenant, login_after_reset

MSG = "Your firm's Baton subscription is inactive — contact your administrator"


def make_operator(email="op@baton.dev", pw="Op-initial-9!", must_reset=False):
    with engine.begin() as conn:
        conn.execute(sql("INSERT INTO platform_operators (email, password_hash, must_reset) "
                         "VALUES (:e, :h, :m)"), {"e": email, "h": hash_password(pw), "m": must_reset})
    return email, pw


def op_headers(client):
    email, pw = make_operator()
    r = client.post("/platform/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def tenant_with_admin(client):
    boot = bootstrap_tenant(client)
    admin = next(u for u in boot["users"] if u["role"] == "Admin")
    tokens = login_after_reset(client, admin["email"], admin["temp_password"])
    return boot, {"Authorization": f"Bearer {tokens['access_token']}"}


def set_subscription(tenant_id, **cols):
    sets = ", ".join(f"{k} = :{k}" for k in cols)
    with engine.begin() as conn:
        conn.execute(sql(f"UPDATE subscriptions SET {sets} WHERE tenant_id = :t"),
                     {**cols, "t": tenant_id})


def test_operator_login_and_forced_reset_cycle(client):
    email, pw = make_operator(must_reset=True)
    r = client.post("/platform/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200 and r.json()["must_reset"] is True
    token = r.json()["access_token"]
    # console endpoints refuse until the reset happens
    assert client.get("/platform/firms", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    r = client.post("/platform/auth/reset-password", json={"new_password": "Fresh-op-pass-1!"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["must_reset"] is False
    # old password dead, new one lives
    assert client.post("/platform/auth/login", json={"email": email, "password": pw}).status_code == 401
    r = client.post("/platform/auth/login", json={"email": email, "password": "Fresh-op-pass-1!"})
    assert r.status_code == 200
    assert client.get("/platform/firms",
                      headers={"Authorization": f"Bearer {r.json()['access_token']}"}).status_code == 200


def test_scope_isolation_both_directions(client):
    boot, tenant_headers = tenant_with_admin(client)
    op = op_headers(client)
    # operator tokens are rejected by EVERY tenant endpoint
    for path in ("/clients", "/proposals", "/duties", "/onboardings", "/notices",
                 "/tenants/me", "/performance/employees", "/users"):
        r = client.get(path, headers=op)
        assert r.status_code == 401, f"{path} → {r.status_code}"
        assert "not valid for tenant endpoints" in r.json()["detail"]
    # tenant tokens are rejected by operator endpoints
    for path in ("/platform/firms", "/platform/log"):
        r = client.get(path, headers=tenant_headers)
        assert r.status_code == 401, f"{path} → {r.status_code}"


def test_bootstrap_creates_trial_subscription(client):
    boot, _ = tenant_with_admin(client)
    op = op_headers(client)
    firms = client.get("/platform/firms", headers=op).json()
    firm = next(f for f in firms if str(f["tenant_id"]) == str(boot["tenant_id"]))
    sub = firm["subscription"]
    assert sub["status"] == "trial" and sub["plan_name"] == "Trial"
    assert sub["seats_limit"] >= 2  # env default, never below the deploying head-count
    end = datetime.fromisoformat(sub["current_period_end"])
    days = (end - datetime.now(timezone.utc)).days
    assert 28 <= days <= 30
    assert firm["seats_used"] == 2
    assert firm["stats"]["active_users_7d"] >= 1  # the admin just logged in


def test_suspension_blocks_login_and_api_with_402(client):
    boot, tenant_headers = tenant_with_admin(client)
    admin = next(u for u in boot["users"] if u["role"] == "Admin")
    op = op_headers(client)
    r = client.patch(f"/platform/firms/{boot['tenant_id']}/subscription",
                     json={"status": "suspended", "note": "non-payment — 2 reminders ignored"},
                     headers=op)
    assert r.status_code == 200, r.text
    # existing session → 402 with the message on every tenant API call
    r = client.get("/proposals", headers=tenant_headers)
    assert r.status_code == 402 and r.json()["detail"] == MSG
    # fresh login → blocked with the same clear message
    r = client.post("/auth/login", json={"email": admin["email"], "password": "S3cure-pass!"})
    assert r.status_code == 403 and r.json()["detail"] == MSG
    # reactivate → everything works again
    r = client.patch(f"/platform/firms/{boot['tenant_id']}/subscription",
                     json={"status": "active", "note": "payment received"}, headers=op)
    assert r.status_code == 200
    assert client.get("/proposals", headers=tenant_headers).status_code == 200


def test_grace_window(client):
    boot, tenant_headers = tenant_with_admin(client)
    # 3 days past expiry → inside the 7-day grace, still working
    set_subscription(boot["tenant_id"],
                     current_period_end=datetime.now(timezone.utc) - timedelta(days=3))
    assert client.get("/proposals", headers=tenant_headers).status_code == 200
    # 8 days past → blocked (API 402, login 403)
    set_subscription(boot["tenant_id"],
                     current_period_end=datetime.now(timezone.utc) - timedelta(days=8))
    r = client.get("/proposals", headers=tenant_headers)
    assert r.status_code == 402 and r.json()["detail"] == MSG
    admin = next(u for u in boot["users"] if u["role"] == "Admin")
    r = client.post("/auth/login", json={"email": admin["email"], "password": "S3cure-pass!"})
    assert r.status_code == 403 and r.json()["detail"] == MSG
    # the expiring-soon banner data reaches tenant admins while still active
    set_subscription(boot["tenant_id"],
                     current_period_end=datetime.now(timezone.utc) + timedelta(days=10))
    firm = client.get("/tenants/me", headers=tenant_headers).json()
    assert firm["subscription"]["expiring_soon"] is True
    assert firm["subscription"]["days_left"] <= 14


def test_seat_limit_refusal_names_the_limit(client):
    boot, tenant_headers = tenant_with_admin(client)
    op = op_headers(client)
    r = client.patch(f"/platform/firms/{boot['tenant_id']}/subscription",
                     json={"seats_limit": 2, "note": "starter plan"}, headers=op)
    assert r.status_code == 200
    r = client.post("/users", json={"name": "Third Person", "email": "third@alphaledger.ae",
                                    "role": "Staff", "signatory": False}, headers=tenant_headers)
    assert r.status_code == 409
    assert "2 active user(s)" in r.json()["detail"]


def test_subscription_changes_are_logged_with_note(client):
    boot, _ = tenant_with_admin(client)
    op = op_headers(client)
    # the note is mandatory
    r = client.patch(f"/platform/firms/{boot['tenant_id']}/subscription",
                     json={"seats_limit": 25}, headers=op)
    assert r.status_code == 422
    r = client.patch(f"/platform/firms/{boot['tenant_id']}/subscription",
                     json={"plan_name": "Professional", "status": "active", "seats_limit": 25,
                           "note": "annual contract signed"}, headers=op)
    assert r.status_code == 200, r.text
    log = client.get("/platform/log", headers=op).json()
    entry = next(e for e in log if e["tenant_id"] == str(boot["tenant_id"]))
    assert "status trial→active" in entry["text"] and "seats" in entry["text"]
    assert 'note: "annual contract signed"' in entry["text"]
    assert entry["text"].startswith("AlphaLedger")
    # the firm detail shows the same events, still counts-only
    detail = client.get(f"/platform/firms/{boot['tenant_id']}", headers=op).json()
    assert any("annual contract signed" in e["text"] for e in detail["events"])


def test_operator_can_never_fetch_tenant_business_content(client):
    boot, tenant_headers = tenant_with_admin(client)
    op = op_headers(client)
    # the bootstrap payload registered a pre-Baton client named "Gulf Horizon" — that name
    # (tenant business content) must never appear in ANY operator-scope response
    assert client.get("/clients", headers=tenant_headers).json()[0]["name"] == "Gulf Horizon"
    firms = client.get("/platform/firms", headers=op)
    detail = client.get(f"/platform/firms/{boot['tenant_id']}", headers=op)
    log = client.get("/platform/log", headers=op)
    for r in (firms, detail, log):
        assert r.status_code == 200
        assert "Gulf Horizon" not in r.text
    # counts only — the detail carries numbers, not lists of business objects
    d = detail.json()
    assert d["stats"]["clients"] == 1 and isinstance(d["stats"]["open_duties"], int)
    assert "client_names" not in r.text and "prospect" not in detail.text
    # and direct attempts at tenant endpoints are 401 (scope isolation, re-asserted)
    assert client.get("/clients", headers=op).status_code == 401
    assert client.get("/proposals", headers=op).status_code == 401
