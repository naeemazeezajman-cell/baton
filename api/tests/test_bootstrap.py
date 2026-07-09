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
