"""Platform Operator console: operator auth + scope isolation both directions,
subscription enforcement (suspension, grace, seats), trial auto-creation, logged
subscription changes, and the hard no-tenant-content rule."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sql

from app.db import engine
from app.security import hash_password
from .conftest import BOOTSTRAP_PAYLOAD, bootstrap_tenant, login_after_reset

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


CREATE_FIRM_PAYLOAD = {
    "firm": {"name": "Beta Books LLC", "short": "BetaBooks", "email": "admin@betabooks.ae"},
    "employees": [
        {"name": "Admin One", "email": "admin@betabooks.ae", "role": "Admin", "signatory": True},
        {"name": "Staffer Two", "email": "staff@betabooks.ae", "role": "Staff"},
    ],
    "subscription": {"plan_name": "Professional", "status": "active", "seats_limit": 5},
}


def test_operator_creates_firm_with_subscription(client):
    op = op_headers(client)
    r = client.post("/platform/firms", json=CREATE_FIRM_PAYLOAD, headers=op)
    assert r.status_code == 201, r.text
    out = r.json()
    # temp passwords surface exactly once, for every created user
    assert len(out["users"]) == 2 and all(u["temp_password"] for u in out["users"])
    sub = out["subscription"]
    assert sub["plan_name"] == "Professional" and sub["status"] == "active"
    assert sub["seats_limit"] == 5
    assert sub["current_period_end"] is None  # active with no period end = open-ended
    # the firm shows up in the list with its subscription
    firms = client.get("/platform/firms", headers=op).json()
    firm = next(f for f in firms if f["name"] == "Beta Books LLC")
    assert firm["subscription"]["status"] == "active" and firm["seats_used"] == 2
    # creation is on the platform log, attributed to the operator
    log = client.get("/platform/log", headers=op).json()
    assert any("firm created" in e["text"] and "Beta Books" in e["text"] for e in log)
    # the admin's temp password works and the forced-reset gate applies
    admin = next(u for u in out["users"] if u["role"] == "Admin")
    tokens = login_after_reset(client, admin["email"], admin["temp_password"])
    assert tokens["access_token"]


def test_create_firm_rejects_non_operator_tokens(client):
    assert client.post("/platform/firms", json=CREATE_FIRM_PAYLOAD).status_code == 401
    boot, tenant_headers = tenant_with_admin(client)
    assert client.post("/platform/firms", json=CREATE_FIRM_PAYLOAD,
                       headers=tenant_headers).status_code == 401
    # a new firm can never start suspended/cancelled
    op = op_headers(client)
    bad = {**CREATE_FIRM_PAYLOAD, "subscription": {"status": "suspended"}}
    assert client.post("/platform/firms", json=bad, headers=op).status_code == 422


def test_bootstrap_key_gates_the_public_endpoint(client, monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_KEY", "top-secret-key")
    assert client.post("/tenants/bootstrap", json=BOOTSTRAP_PAYLOAD).status_code == 403
    assert client.post("/tenants/bootstrap", json=BOOTSTRAP_PAYLOAD,
                       headers={"X-Bootstrap-Key": "wrong"}).status_code == 403
    r = client.post("/tenants/bootstrap", json=BOOTSTRAP_PAYLOAD,
                    headers={"X-Bootstrap-Key": "top-secret-key"})
    assert r.status_code == 201, r.text
    # the operator path is untouched by the key gate (it authenticates by operator JWT)
    op = op_headers(client)
    assert client.post("/platform/firms", json=CREATE_FIRM_PAYLOAD, headers=op).status_code == 201


SETUP_PAYLOAD = {
    "firm": {"name": "Gamma Tax Consultancy LLC", "short": "GammaTax", "address": "DIFC, Dubai",
             "trn": "TRN 100-9999-8888-777", "phone": "+971 4 000 0000",
             "email": "owner@gammatax.ae", "accent": "#123456"},
    "services": ["VAT Filing", "Bookkeeping (Monthly)"],
    "templates": {},
    "employees": [
        {"name": "Owner One", "designation": "Managing Partner", "email": "owner@gammatax.ae",
         "role": "Admin", "signatory": True, "sig": {"type": "typed", "text": "O.O."}},
        {"name": "New Staff", "email": "staff@gammatax.ae", "role": "Staff",
         "duties": [{"client_name": "Desert Rose Trading", "service": "VAT Filing", "kind": "filing",
                     "cadence": "quarterly", "next_due": "2026-09-30T00:00:00Z"}]},
    ],
}


def operator_created_admin(client, seats=3):
    """Operator creates a firm with ONLY the seed Admin; returns the admin's live session."""
    op = op_headers(client)
    r = client.post("/platform/firms", json={
        "firm": {"name": "Gamma Tax LLC", "short": "GammaTax", "email": "owner@gammatax.ae"},
        "employees": [{"name": "Owner One", "email": "owner@gammatax.ae", "role": "Admin", "signatory": True}],
        "subscription": {"plan_name": "Trial", "status": "trial", "seats_limit": seats},
    }, headers=op)
    assert r.status_code == 201, r.text
    out = r.json()
    admin = out["users"][0]
    tokens = login_after_reset(client, admin["email"], admin["temp_password"])
    return out, {"Authorization": f"Bearer {tokens['access_token']}"}


def test_admin_completes_setup_after_operator_creation(client):
    out, headers = operator_created_admin(client)
    # operator-created firm starts unconfigured — the wizard gate keys off empty services
    assert client.get("/tenants/me", headers=headers).json()["services"] == []
    r = client.post("/tenants/complete-setup", json=SETUP_PAYLOAD, headers=headers)
    assert r.status_code == 201, r.text
    # only the NEW employee gets a temp password; the admin is updated, never duplicated
    assert [u["email"] for u in r.json()["users"]] == ["staff@gammatax.ae"]
    users = client.get("/users", headers=headers).json()
    assert len(users) == 2
    admin_row = next(u for u in users if u["email"] == "owner@gammatax.ae")
    assert admin_row["role"] == "Admin" and admin_row["signatory"] is True
    assert admin_row["designation"] == "Managing Partner"
    # firm details + catalog landed
    me = client.get("/tenants/me", headers=headers).json()
    assert me["services"] == SETUP_PAYLOAD["services"]
    assert me["trn"] == "TRN 100-9999-8888-777" and me["name"] == "Gamma Tax Consultancy LLC"
    # the pre-Baton duty registered a first-class client
    assert any(c["name"] == "Desert Rose Trading" for c in client.get("/clients", headers=headers).json())
    # one-shot: once configured, the endpoint refuses — ongoing changes go through
    # Firm settings (PATCH /tenants/me) and Employees & roles (POST /users)
    assert client.post("/tenants/complete-setup", json=SETUP_PAYLOAD, headers=headers).status_code == 409


def test_complete_setup_enforces_seat_limit(client):
    out, headers = operator_created_admin(client, seats=2)
    over = {**SETUP_PAYLOAD, "employees": SETUP_PAYLOAD["employees"] + [
        {"name": "Extra Person", "email": "extra@gammatax.ae", "role": "Staff"},
    ]}  # existing admin + 2 new = 3 active > 2 seats
    r = client.post("/tenants/complete-setup", json=over, headers=headers)
    assert r.status_code == 409 and "Seat limit" in r.json()["detail"]
    # atomic: nothing was created and the firm is still unconfigured
    assert len(client.get("/users", headers=headers).json()) == 1
    assert client.get("/tenants/me", headers=headers).json()["services"] == []
    # trimming to the seat limit succeeds (admin + 1 new = 2)
    r = client.post("/tenants/complete-setup", json=SETUP_PAYLOAD, headers=headers)
    assert r.status_code == 201, r.text


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
