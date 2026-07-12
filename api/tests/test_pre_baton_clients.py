"""Pre-Baton duty clients become first-class clients: bootstrap dedupe + linking, the
orphan-duty backfill, the existing-client duplicate guard, and the additional-engagement
flow for a pre-Baton client."""

from sqlalchemy import text as sql

from app.client_backfill import backfill_pre_baton_clients
from app.db import engine
from .conftest import BOOTSTRAP_PAYLOAD, login_after_reset
from .test_existing_client import _el_send, create_for_client
from .test_onboarding import drive_to_el_approved, setup_firm

H = lambda ctx, who: ctx[who]["headers"]  # noqa: E731

DUTY = {"service": "VAT Filing", "kind": "vat", "cadence": "quarterly", "next_due": "2026-08-28T00:00:00Z"}


def test_bootstrap_creates_deduped_clients_and_links_duties(client):
    payload = {
        **BOOTSTRAP_PAYLOAD,
        "firm": {**BOOTSTRAP_PAYLOAD["firm"], "email": "hello@prebaton.ae"},
        "employees": [
            {"name": "Ayesha Khan", "designation": "Managing Partner", "email": "ayesha@prebaton.ae",
             "role": "Admin", "signatory": True},
            {"name": "Priya Nair", "designation": "Senior Accountant", "email": "priya@prebaton.ae",
             "role": "Staff", "duties": [
                 {**DUTY, "client_name": "Gulf Horizon", "contact": {"email": "a@gh.ae"}},
                 # same client, different case/whitespace AND a different contact → dedupe + note
                 {**DUTY, "client_name": "  gulf   HORIZON ", "service": "Corporate Tax Filing",
                  "kind": "ct", "contact": {"email": "b@gh.ae"}},
                 {**DUTY, "client_name": "Al Dana", "service": "Bookkeeping (Monthly)",
                  "kind": "report", "cadence": "monthly", "contact": {"email": "c@ad.ae"}},
             ]},
        ],
    }
    r = client.post("/tenants/bootstrap", json=payload)
    assert r.status_code == 201, r.text
    admin = next(u for u in r.json()["users"] if u["role"] == "Admin")
    tokens = login_after_reset(client, admin["email"], admin["temp_password"])
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    clients = client.get("/clients", headers=headers).json()
    assert [(c["ref"], c["name"]) for c in clients] == [("CL-001", "Gulf Horizon"), ("CL-002", "Al Dana")]
    gh = clients[0]
    assert gh["origin"] == "pre_baton"
    assert gh["confirmation_basis"] == "pre-existing relationship (pre-Baton deployment)"
    assert gh["contact"] == {"email": "a@gh.ae"}  # first duty's contact wins

    duties = client.get("/duties", headers=headers).json()
    assert len(duties) == 3 and all(d["client_id"] for d in duties)
    gh_duties = [d for d in duties if d["client_id"] == gh["id"]]
    assert len(gh_duties) == 2  # multiple duties, one client row
    ct = next(d for d in gh_duties if d["kind"] == "ct")
    assert ct["contact"] == {"email": "b@gh.ae"}  # the duty keeps its own contact
    assert any("keeps the contact from the first registered duty" in e["text"] for e in ct["events"])


def test_backfill_links_orphan_duties_dedup_and_idempotent(client):
    ctx = setup_firm(client)  # no pre-existing duties → no clients
    for name, contact in (("Ivory Gate", {"email": "x@ig.ae"}),
                          ("IVORY  gate", {"email": "y@ig.ae"}),
                          ("Nimbus", {"email": "z@nb.ae"})):
        r = client.post("/duties", json={**DUTY, "staff_id": ctx["staff"]["id"],
                                         "client_name": name, "contact": {"name": "", **contact}},
                        headers=H(ctx, "manager"))
        assert r.status_code == 201, r.text
    assert client.get("/clients", headers=H(ctx, "manager")).json() == []

    with engine.begin() as conn:
        assert backfill_pre_baton_clients(conn) == 2  # deduped case-insensitively

    clients = client.get("/clients", headers=H(ctx, "manager")).json()
    assert {(c["ref"], c["name"], c["origin"]) for c in clients} == \
        {("CL-001", "Ivory Gate", "pre_baton"), ("CL-002", "Nimbus", "pre_baton")}
    duties = client.get("/duties", headers=H(ctx, "manager")).json()
    assert all(d["client_id"] for d in duties)
    ig = next(c for c in clients if c["name"] == "Ivory Gate")
    assert ig["contact"]["email"] == "x@ig.ae"  # first duty's contact
    dup = next(d for d in duties if d["contact"].get("email") == "y@ig.ae")
    assert dup["client_id"] == ig["id"]
    assert any("keeps the contact from the first registered duty" in e["text"] for e in dup["events"])

    with engine.begin() as conn:
        assert backfill_pre_baton_clients(conn) == 0  # idempotent
        n = conn.execute(sql("SELECT count(*) FROM clients")).scalar()
    assert n == 2


def make_pre_baton_client(client, ctx, name="Ivory Gate Real Estate Brokerage LLC"):
    r = client.post("/duties", json={**DUTY, "staff_id": ctx["staff"]["id"], "client_name": name,
                                     "contact": {"name": "Khalid", "email": "kh@ivorygate.ae"}},
                    headers=H(ctx, "manager"))
    assert r.status_code == 201
    with engine.begin() as conn:
        backfill_pre_baton_clients(conn)
    return client.get("/clients", headers=H(ctx, "manager")).json()[0]


def test_duplicate_guard_catches_pre_baton_client_names(client):
    ctx = setup_firm(client)
    cl = make_pre_baton_client(client, ctx)
    r = client.post("/proposals", json={
        "prospect": {"name": "  ivory gate REAL ESTATE brokerage llc ", "email": "kh@ivorygate.ae"},
        "services": [{"name": "VAT Filing", "fee": "6000", "basis": "per quarter"}],
        "assigned_to": ctx["staff"]["id"],
    }, headers=H(ctx, "manager"))
    assert r.status_code == 409
    reason = r.json()["detail"]["reason"]
    assert f'is an existing client ({cl["ref"]})' in reason
    assert "use Existing client mode to add an engagement" in reason


def test_existing_client_proposal_for_pre_baton_client_to_el_sent(client):
    ctx = setup_firm(client)
    cl = make_pre_baton_client(client, ctx)
    p = create_for_client(client, ctx, cl["id"])  # existing-client mode lists pre-Baton clients too
    assert p["client_id"] == cl["id"] and p["prospect"]["name"] == cl["name"]
    drive_to_el_approved(client, ctx, p["id"], advance_pct=0)
    detail = client.get(f"/proposals/{p['id']}", headers=H(ctx, "manager")).json()
    assert any(f"Additional engagement confirmed for {cl['ref']}" in e["text"] for e in detail["events"])
    assert not any("prospect converted to CLIENT" in e["text"] for e in detail["events"])
    _el_send(client, ctx, p["id"])
    clients = client.get("/clients", headers=H(ctx, "manager")).json()
    assert len(clients) == 1 and clients[0]["origin"] == "pre_baton"  # NO second client row
    # onboarding + payments keyed off the pre-Baton client row as normal
    obs = client.get("/onboardings", headers=H(ctx, "staff")).json()
    assert obs and all(o["client_ref"] == cl["ref"] for o in obs)
