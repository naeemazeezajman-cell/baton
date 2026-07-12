from sqlalchemy import select

from app.db import SessionLocal
from app.models import Duty, User

from .conftest import BOOTSTRAP_PAYLOAD, bootstrap_tenant


def test_bootstrap_creates_tenant_users_duties(client):
    boot = bootstrap_tenant(client)
    assert len(boot["users"]) == 2
    assert all(u["temp_password"] for u in boot["users"])

    with SessionLocal() as db:
        users = db.scalars(select(User)).all()
        assert {u.email for u in users} == {"ayesha@alphaledger.ae", "priya@alphaledger.ae"}
        assert all(u.must_reset for u in users)
        # passwords are stored hashed, never plaintext
        temp_passwords = {u["temp_password"] for u in boot["users"]}
        assert all(u.password_hash not in temp_passwords for u in users)
        assert all(u.password_hash.startswith("$2") for u in users)

        duties = db.scalars(select(Duty)).all()
        assert len(duties) == 1
        assert duties[0].client_name == "Gulf Horizon"
        priya = next(u for u in users if u.email == "priya@alphaledger.ae")
        assert duties[0].staff_id == priya.id
        assert duties[0].tenant_id == priya.tenant_id


def test_wizard_temp_password_full_cycle(client):
    """The DISPLAYED (returned) temp password is exactly what the stored hash matches:
    temp login → forced reset → old temp dead, new password lives. Email lookup is
    case-insensitive (CITEXT)."""
    boot = bootstrap_tenant(client)
    u = next(x for x in boot["users"] if x["email"] == "priya@alphaledger.ae")

    # the displayed temp password logs in, and forces a reset
    r = client.post("/auth/login", json={"email": u["email"], "password": u["temp_password"]})
    assert r.status_code == 200, r.text
    assert r.json()["must_reset"] is True
    # case-variant email still resolves the same account
    r2 = client.post("/auth/login", json={"email": "PRIYA@AlphaLedger.AE", "password": u["temp_password"]})
    assert r2.status_code == 200 and r2.json()["must_reset"] is True
    # a wizard-style locally-invented password is rejected — only the server-issued one works
    assert client.post("/auth/login", json={"email": u["email"], "password": "abcd-efgh"}).status_code == 401

    # forced reset, then: old temp dead, new password lives, must_reset cleared
    tokens = r.json()
    r = client.post("/auth/reset-password", json={"new_password": "Fresh-pass-9!"},
                    headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 200 and r.json()["must_reset"] is False
    assert client.post("/auth/login", json={"email": u["email"], "password": u["temp_password"]}).status_code == 401
    r = client.post("/auth/login", json={"email": u["email"], "password": "Fresh-pass-9!"})
    assert r.status_code == 200 and r.json()["must_reset"] is False


def test_resend_invite_reissues_temp_password(client):
    """Recovery path when a temp password is lost before first login: Admin resend-invite
    re-hashes a fresh temp password (dev mode prints the invite email to the console)."""
    boot = bootstrap_tenant(client)
    admin = next(x for x in boot["users"] if x["email"] == "ayesha@alphaledger.ae")
    priya = next(x for x in boot["users"] if x["email"] == "priya@alphaledger.ae")
    from .conftest import login_after_reset
    tokens = login_after_reset(client, admin["email"], admin["temp_password"])
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    r = client.post(f"/users/{priya['id']}/resend-invite", headers=headers)
    assert r.status_code == 204
    # the old temp password is dead; the account still awaits its first login
    assert client.post("/auth/login", json={"email": priya["email"],
                                            "password": priya["temp_password"]}).status_code == 401
    with SessionLocal() as db:
        row = db.scalars(select(User).where(User.email == priya["email"])).first()
        assert row.must_reset is True


def test_bootstrap_refuses_duplicate_tenant_email(client):
    bootstrap_tenant(client)
    r = client.post("/tenants/bootstrap", json=BOOTSTRAP_PAYLOAD)
    assert r.status_code == 409


def test_bootstrap_rejects_unknown_role(client):
    payload = {
        **BOOTSTRAP_PAYLOAD,
        "employees": [{**BOOTSTRAP_PAYLOAD["employees"][0], "role": "Overlord"}],
    }
    r = client.post("/tenants/bootstrap", json=payload)
    assert r.status_code == 422
