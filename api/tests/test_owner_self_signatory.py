"""Small-firm owner is both drafter and senior signatory. A single-Admin firm must be able
to take a proposal from draft through BOTH signatures — the manager/drafter signature and
the senior counter-signature — to send-client, as two distinct, identity-confirmed,
separately-logged signature events with no baton pass to anyone else."""

from .conftest import login_after_reset

SOLO_FIRM = {
    "firm": {"name": "Solo Advisory FZE", "short": "Solo", "email": "owner@soloadvisory.ae"},
    "services": ["VAT Filing", "Bookkeeping (Monthly)"],
    "templates": {},
    "employees": [
        {"name": "Sara Owner", "designation": "Managing Partner", "email": "owner@soloadvisory.ae",
         "role": "Admin", "signatory": True, "sig": {"type": "typed", "text": "S.O."}},
    ],
}


def _solo_admin(client):
    r = client.post("/tenants/bootstrap", json=SOLO_FIRM)
    assert r.status_code == 201, r.text
    admin = r.json()["users"][0]
    tokens = login_after_reset(client, admin["email"], admin["temp_password"])
    return admin, {"Authorization": f"Bearer {tokens['access_token']}"}


def test_single_admin_drafts_and_counter_signs_through_send(client):
    admin, h = _solo_admin(client)

    # owner creates a proposal and assigns it to THEMSELVES: drafter == requester == signatory
    r = client.post("/proposals", json={
        "prospect": {"name": "Meridian Trading LLC", "email": "accounts@meridian.ae"},
        "services": [{"name": "VAT Filing", "fee": "6000", "basis": "per quarter"}],
        "assigned_to": admin["id"],
    }, headers=h)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    # draft v1 and submit to manager review — all as the same person
    g = client.post(f"/proposals/{pid}/generate", json={"draft": {
        "lines": [{"service": "VAT Filing", "fee": "6000", "basis": "per quarter"}],
        "payment_terms": "50% advance; balance within 14 days", "validity_days": 30, "scope": "As engaged",
    }, "note": "initial draft"}, headers=h)
    assert g.status_code == 200, g.text
    v = g.json()["version"]["v"]
    r = client.post(f"/proposals/{pid}/submit", json={"version": v}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "manager_review", r.text

    # (1)+(3): routing to yourself as senior signatory is allowed — no "cannot route to
    # yourself" 409, no empty-dropdown dead end
    r = client.post(f"/proposals/{pid}/sign-route",
                    json={"signatory_id": admin["id"], "note": "Owner will counter-sign"}, headers=h)
    assert r.status_code == 200, r.text
    routed = r.json()
    assert routed["status"] == "senior_review"
    assert routed["signatory_id"] == admin["id"]
    assert routed["signatures"]["manager"]["by"] == admin["id"]  # first signature applied
    assert routed["signatures"].get("senior") is None            # not yet — still a separate act
    assert routed["holder"] == admin["id"]                       # no baton pass to anyone else

    # (2): in the SAME session, immediately counter-sign as senior — a distinct explicit action
    r = client.post(f"/proposals/{pid}/senior-approve", json=None, headers=h)
    assert r.status_code == 200, r.text
    signed = r.json()
    assert signed["status"] == "signed"
    # two DISTINCT signatures now recorded, both by the owner, embedded on the signed version
    assert signed["signatures"]["manager"]["by"] == admin["id"]
    assert signed["signatures"]["senior"]["by"] == admin["id"]
    vsig = signed["versions"][-1]["signatures"]
    assert vsig["manager"]["by"] == admin["id"] and vsig["senior"]["by"] == admin["id"]

    # both signatures are separate logged events (identity re-confirmed each time)
    events = client.get(f"/proposals/{pid}", headers=h).json()["events"]
    texts = [e["text"] for e in events]
    assert any("digitally signed by Sara Owner" in t for t in texts)
    assert any("counter-signed by Sara Owner" in t and "locked" in t.lower() for t in texts)

    # the completed flow: the owner sends the signed proposal to the client
    r = client.post(f"/proposals/{pid}/send-client",
                    json={"to": "accounts@meridian.ae", "subject": "Your proposal", "body": "Attached."}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "proposal_sent", r.text


def test_non_signatory_admin_still_rejected(client):
    """The relaxation is scoped: self-routing is only valid for a qualified senior signatory.
    A non-signatory routing target is still refused."""
    admin, h = _solo_admin(client)
    # add a second Admin WITHOUT signing authority
    r = client.post("/users", json={"name": "No Sign", "email": "nosign@soloadvisory.ae",
                                    "role": "Admin", "signatory": False}, headers=h)
    assert r.status_code == 201, r.text
    other_id = r.json()["id"]

    r = client.post("/proposals", json={
        "prospect": {"name": "Probe LLC", "email": "p@probe.ae"},
        "services": [{"name": "VAT Filing", "fee": "6000", "basis": "per quarter"}],
        "assigned_to": admin["id"],
    }, headers=h)
    pid = r.json()["id"]
    g = client.post(f"/proposals/{pid}/generate", json={"draft": {
        "lines": [{"service": "VAT Filing", "fee": "6000", "basis": "per quarter"}],
        "payment_terms": "Net 14", "validity_days": 30, "scope": "As engaged",
    }, "note": "v1"}, headers=h)
    v = g.json()["version"]["v"]
    client.post(f"/proposals/{pid}/submit", json={"version": v}, headers=h)
    r = client.post(f"/proposals/{pid}/sign-route", json={"signatory_id": other_id}, headers=h)
    assert r.status_code == 409 and "signing authority" in r.text
