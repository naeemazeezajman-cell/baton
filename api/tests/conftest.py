"""Test setup: a dedicated baton_test database on the dev Docker Postgres (port 5433),
schema built fresh per session, truncated between tests."""

import os

os.environ["DATABASE_URL"] = "postgresql+psycopg://baton:baton@localhost:5433/baton_test"
os.environ["EMAIL_CONN"] = ""  # force console email mode in tests
os.environ["ANTHROPIC_API_KEY"] = ""  # tests must exercise the raw-text fallback, never the live API

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

ADMIN_URL = "postgresql+psycopg://baton:baton@localhost:5433/baton"


def _ensure_test_db():
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = 'baton_test'"))
        if not exists:
            conn.execute(text("CREATE DATABASE baton_test"))
    admin.dispose()


_ensure_test_db()

from app.db import Base, engine  # noqa: E402 — after env vars are set
from app import models  # noqa: F401, E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _clean_tables(_schema):
    yield
    with engine.begin() as conn:
        tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def client():
    return TestClient(app)


BOOTSTRAP_PAYLOAD = {
    "firm": {
        "name": "AlphaLedger Accounting & Tax Consultancy LLC",
        "short": "AlphaLedger",
        "address": "Office 1204, Corniche Tower, Ajman, UAE",
        "trn": "TRN 100-2233-4455-667",
        "phone": "+971 6 512 3456",
        "email": "hello@alphaledger.ae",
        "accent": "#14606B",
    },
    "services": ["Bookkeeping (Monthly)", "VAT Filing", "Corporate Tax Filing"],
    "templates": {"proposal": {"footer": "AlphaLedger standard terms"}},
    "employees": [
        {
            "name": "Ayesha Khan",
            "designation": "Managing Partner",
            "email": "ayesha@alphaledger.ae",
            "role": "Admin",
            "signatory": True,
        },
        {
            "name": "Priya Nair",
            "designation": "Senior Accountant",
            "email": "priya@alphaledger.ae",
            "role": "Staff",
            "duties": [
                {
                    "client_name": "Gulf Horizon",
                    "service": "VAT Filing",
                    "kind": "filing",
                    "cadence": "quarterly",
                    "next_due": "2026-08-28T00:00:00Z",
                    "contact": {"email": "accounts@gulfhorizon.ae"},
                }
            ],
        },
    ],
}


def bootstrap_tenant(client, email="hello@alphaledger.ae", user_email_domain="alphaledger.ae"):
    payload = {
        **BOOTSTRAP_PAYLOAD,
        "firm": {**BOOTSTRAP_PAYLOAD["firm"], "email": email},
        "employees": [
            {**e, "email": e["email"].split("@")[0] + "@" + user_email_domain}
            for e in BOOTSTRAP_PAYLOAD["employees"]
        ],
    }
    r = client.post("/tenants/bootstrap", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def login_after_reset(client, email, temp_password, new_password="S3cure-pass!"):
    """Login with temp password, pass the must_reset gate via reset-password, return fresh tokens."""
    r = client.post("/auth/login", json={"email": email, "password": temp_password})
    assert r.status_code == 200, r.text
    tokens = r.json()
    assert tokens["must_reset"] is True
    r = client.post(
        "/auth/reset-password",
        json={"new_password": new_password},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert r.status_code == 200, r.text
    return r.json()
