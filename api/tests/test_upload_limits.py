"""Upload limits: the per-file size cap and the per-tenant storage quota.

These close the path that publishing demo credentials would otherwise open — an anonymous
visitor pushing unbounded files into the firm's Blob account through a single-replica API.

The cap has to hold on every door, not just the tidy one: two storage helpers, plus three
handlers that read the raw bytes before storing them (VAT ledger, VAT register, AI
extraction). A cap in files.store_upload alone would leave those three wide open, so the VAT
paths are tested explicitly rather than assumed.
"""

import io

import pytest
from sqlalchemy import func, select

from app import uploads
from app.config import Settings
from app.db import SessionLocal
from app.demo_seed import DEMO_PASSWORD, DEMO_TENANT_EMAIL, seed_demo
from app.models import File, Tenant

from .conftest import bootstrap_tenant, login_after_reset


def _tune(monkeypatch, **overrides):
    """Shrink the limits so a test can cross them with kilobytes instead of megabytes."""
    base = Settings()
    monkeypatch.setattr(uploads, "get_settings", lambda: base.model_copy(update=overrides))


@pytest.fixture
def firm(client):
    boot = bootstrap_tenant(client)
    admin = next(u for u in boot["users"] if u["role"] == "Admin")
    tokens = login_after_reset(client, admin["email"], admin["temp_password"], "Upl0ad-pass!")
    return {"headers": {"Authorization": f"Bearer {tokens['access_token']}"},
            "entity_id": admin["id"]}


def _upload(client, firm, blob: bytes, name="scan.pdf"):
    return client.post("/files", data={"entity": "proposal", "entity_id": firm["entity_id"]},
                       files={"file": (name, blob, "application/pdf")}, headers=firm["headers"])


def _stored_count(tenant_email="hello@alphaledger.ae"):
    db = SessionLocal()
    try:
        tid = db.scalar(select(Tenant.id).where(Tenant.email == tenant_email))
        return db.scalar(select(func.count()).select_from(File).where(File.tenant_id == tid))
    finally:
        db.close()


# ---------- per-file cap ----------

def test_oversized_upload_is_rejected_with_413_and_stores_nothing(client, firm, monkeypatch):
    _tune(monkeypatch, MAX_UPLOAD_MB=1)
    r = _upload(client, firm, b"x" * (2 * 1024 * 1024), name="huge.pdf")
    assert r.status_code == 413, r.text
    assert r.json()["detail"]["code"] == "FILE_TOO_LARGE"
    assert "huge.pdf" in r.json()["detail"]["message"]
    # a refused upload must leave no files row and no blob behind
    assert _stored_count() == 0


def test_upload_within_the_cap_still_works(client, firm, monkeypatch):
    _tune(monkeypatch, MAX_UPLOAD_MB=1)
    r = _upload(client, firm, b"x" * (512 * 1024))
    assert r.status_code == 201, r.text
    assert r.json()["size"] == 512 * 1024
    assert _stored_count() == 1


def test_upload_exactly_at_the_cap_is_allowed(client, firm, monkeypatch):
    """The limit is a ceiling, not a fence — off-by-one here would reject legitimate files."""
    _tune(monkeypatch, MAX_UPLOAD_MB=1)
    r = _upload(client, firm, b"x" * (1024 * 1024))
    assert r.status_code == 201, r.text


def test_oversized_file_is_refused_without_being_read_into_memory(monkeypatch):
    """The actual requirement: reject before materializing the bytes.

    A stub upload whose .size is over the cap and whose read() detonates. read_capped must
    raise 413 off .size alone — if it ever reaches for the bytes first, this fails loudly.
    """
    _tune(monkeypatch, MAX_UPLOAD_MB=1)

    class Detonator:
        def read(self, *a):
            raise AssertionError("read_capped read the file instead of refusing it on size")

        def seek(self, *a):
            pass

    class StubUpload:
        filename = "enormous.pdf"
        size = 50 * 1024 * 1024
        file = Detonator()

    with pytest.raises(Exception) as exc:
        uploads.read_capped(StubUpload())
    assert exc.value.status_code == 413
    assert exc.value.detail["code"] == "FILE_TOO_LARGE"


def test_cap_holds_when_size_is_absent(monkeypatch):
    """Chunked transfer-encoding leaves .size unset; the streaming backstop must still stop."""
    _tune(monkeypatch, MAX_UPLOAD_MB=1)

    class StubUpload:
        filename = "streamed.pdf"
        size = None
        file = io.BytesIO(b"x" * (3 * 1024 * 1024))

    with pytest.raises(Exception) as exc:
        uploads.read_capped(StubUpload())
    assert exc.value.status_code == 413


# ---------- the cap is not bypassable via the VAT engine's own upload paths ----------

def test_vat_ledger_upload_is_capped_too(client, monkeypatch):
    """/vat-engine/filings/{id}/ledger parses the raw bytes before storing them, so it never
    touched files.store_upload — the door a store_upload-only cap would have left open."""
    from .test_vat_engine import H, make_vat_duty, open_filing, setup_firm

    ctx = setup_firm(client)
    duty = make_vat_duty(client, ctx)
    f = open_filing(client, ctx, duty["id"])
    _tune(monkeypatch, MAX_UPLOAD_MB=1)
    r = client.post(f"/vat-engine/filings/{f['id']}/ledger",
                    files={"file": ("Ledger.xlsx", b"x" * (2 * 1024 * 1024),
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                    headers=H(ctx, "staff"))
    assert r.status_code == 413, r.text
    assert r.json()["detail"]["code"] == "FILE_TOO_LARGE"


# ---------- per-tenant storage quota ----------

def test_storage_quota_refuses_the_upload_that_would_cross_it(client, firm, monkeypatch):
    _tune(monkeypatch, MAX_UPLOAD_MB=1, TENANT_STORAGE_QUOTA_MB=1)

    r = _upload(client, firm, b"x" * (600 * 1024), name="first.pdf")
    assert r.status_code == 201, r.text

    r = _upload(client, firm, b"x" * (600 * 1024), name="second.pdf")
    assert r.status_code == 413, r.text
    assert r.json()["detail"]["code"] == "STORAGE_QUOTA_EXCEEDED"
    assert _stored_count() == 1, "the refused upload was stored anyway"

    # room remains for something that fits — the quota bounds the firm, it doesn't freeze it
    r = _upload(client, firm, b"x" * (100 * 1024), name="small.pdf")
    assert r.status_code == 201, r.text


def test_demo_firm_gets_a_tighter_quota_than_a_real_firm(client):
    """The per-demo-tenant guard: public credentials buy a smaller allowance."""
    db = SessionLocal()
    try:
        seed_demo(db, reset=False)
        demo_id = db.scalar(select(Tenant.id).where(Tenant.email == DEMO_TENANT_EMAIL))
        real = Tenant(name="Real Firm", short="Real", email="hello@realfirm.ae")
        db.add(real)
        db.flush()

        demo_quota = uploads.quota_bytes(db, demo_id)
        real_quota = uploads.quota_bytes(db, real.id)
        assert demo_quota < real_quota, (demo_quota, real_quota)
        assert demo_quota > 0
    finally:
        db.rollback()
        db.close()


def test_demo_visitor_cannot_fill_the_blob_account(client, monkeypatch):
    """End-to-end from a published login: the demo firm's allowance is enforced on the way in."""
    db = SessionLocal()
    try:
        seed_demo(db, reset=False)
    finally:
        db.close()
    r = client.post("/auth/login", json={"email": "demo.admin@batondemo.co",
                                         "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    db = SessionLocal()
    try:
        demo_tenant = db.scalar(select(Tenant).where(Tenant.email == DEMO_TENANT_EMAIL))
        entity_id = str(demo_tenant.id)
        used_before = uploads.tenant_storage_bytes(db, demo_tenant.id)
    finally:
        db.close()

    # the seed's recon workbook already counts against the allowance
    _tune(monkeypatch, MAX_UPLOAD_MB=1, DEMO_STORAGE_QUOTA_MB=1)
    assert used_before > 0

    r = client.post("/files", data={"entity": "proposal", "entity_id": entity_id},
                    files={"file": ("flood.pdf", b"x" * (900 * 1024), "application/pdf")},
                    headers=headers)
    assert r.status_code == 201, r.text
    r = client.post("/files", data={"entity": "proposal", "entity_id": entity_id},
                    files={"file": ("flood2.pdf", b"x" * (900 * 1024), "application/pdf")},
                    headers=headers)
    assert r.status_code == 413, r.text
    assert r.json()["detail"]["code"] == "STORAGE_QUOTA_EXCEEDED"


def test_limits_can_be_disabled_with_zero(client, firm, monkeypatch):
    """0 = unlimited, so an operator can lift either limit per environment without a deploy."""
    _tune(monkeypatch, MAX_UPLOAD_MB=0, TENANT_STORAGE_QUOTA_MB=0)
    r = _upload(client, firm, b"x" * (2 * 1024 * 1024))
    assert r.status_code == 201, r.text
