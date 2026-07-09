"""Onboarding module — per-activity documentation relay, credentials, registry, duty bridge."""

from datetime import datetime, timedelta, timezone

from .conftest import bootstrap_tenant, login_after_reset
from .test_onboarding import SERVICES, act, create_proposal, drive_to_el_approved, setup_firm


def start_onboardings(client, ctx):
    """el-send with both services staffed to Priya → two onboardings."""
    pid = create_proposal(client, ctx)["id"]
    drive_to_el_approved(client, ctx, pid, advance_pct=0)
    act(client, ctx, "manager", pid, "el-send", {"to": "a@b.ae", "subject": "EL", "body": "x"})
    obs = client.get("/onboardings", headers=ctx["staff"]["headers"]).json()
    return pid, obs


def ob_act(client, ctx, who, oid, path, json=None, expect=200, **kw):
    r = client.post(f"/onboardings/{oid}/{path}", json=json, headers=ctx[who]["headers"], **kw)
    assert r.status_code == expect, f"{path}: {r.status_code} {r.text}"
    return r.json()


def test_autocreation_at_el_send(client):
    ctx = setup_firm(client)
    pid, obs = start_onboardings(client, ctx)
    assert len(obs) == 2
    assert {o["service"] for o in obs} == {s["name"] for s in SERVICES}
    for o in obs:
        assert o["status"] == "in_progress"
        assert o["staff_id"] == ctx["staff"]["id"] and o["holder"] == ctx["staff"]["id"]
        assert o["manager_id"] == ctx["manager"]["id"]
        assert o["client_ref"] == "CL-001"
    detail = client.get(f"/onboardings/{obs[0]['id']}", headers=ctx["staff"]["headers"]).json()
    assert any("Onboarding started" in e["text"] for e in detail["events"])
    # the proposal audit notes the handover with the NEW copy (old events untouched)
    pdetail = client.get(f"/proposals/{pid}", headers=ctx["manager"]["headers"]).json()
    texts = [e["text"] for e in pdetail["events"]]
    assert any("Client documentation proceeds in Onboarding." in t for t in texts)
    assert any("Onboarding started for 2 activities" in t for t in texts)


def test_full_relay_round_trip(client):
    ctx = setup_firm(client)
    _, obs = start_onboardings(client, ctx)
    oid = next(o["id"] for o in obs if o["service"] == "VAT Filing")

    # staff can't send with no items; manager can't add items
    ob_act(client, ctx, "staff", oid, "send-requests", expect=409)
    ob_act(client, ctx, "manager", oid, "items",
           {"items": [{"label": "x", "kind": "document"}]}, expect=409)

    o = ob_act(client, ctx, "staff", oid, "items", {"items": [
        {"label": "Trade license copy", "kind": "document"},
        {"label": "FTA portal login", "kind": "credential", "note": "needed for filing"},
        {"label": "Confirm VAT registration date", "kind": "information"},
    ]})
    assert o["open_items"] == 3
    o = ob_act(client, ctx, "staff", oid, "send-requests")
    assert o["holder"] == ctx["manager"]["id"]
    mgr_notices = client.get("/notices", headers=ctx["manager"]["headers"]).json()
    assert any("requested 3 item(s)" in n["text"] and "baton is with you" in n["text"] for n in mgr_notices)

    items = {i["label"]: i for i in o["items"]}
    # manager cannot resolve while... staff cannot provide at all
    ob_act(client, ctx, "staff", oid, f"items/{items['Trade license copy']['id']}/provide",
           expect=409, data={"answer_text": "x"}, json=None)

    # provide document with qualifier
    r = client.post(f"/onboardings/{oid}/items/{items['Trade license copy']['id']}/provide",
                    data={"qualifier": "copy"},
                    files=[("evidence", ("Trade License.pdf", b"%PDF tl", "application/pdf"))],
                    headers=ctx["manager"]["headers"])
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["holder"] == ctx["manager"]["id"]  # two items still open — baton stays
    # answer the information item
    r = client.post(f"/onboardings/{oid}/items/{items['Confirm VAT registration date']['id']}/provide",
                    data={"answer_text": "Registered 12 Mar 2024"}, headers=ctx["manager"]["headers"])
    assert r.status_code == 200
    # answer the credential — resolves the last open item → baton auto-returns
    r = client.post(f"/onboardings/{oid}/items/{items['FTA portal login']['id']}/provide",
                    data={"answer_text": "user: gulfhorizon / pass: Fta!2026"}, headers=ctx["manager"]["headers"])
    o = r.json()
    assert o["open_items"] == 0
    assert o["holder"] == ctx["staff"]["id"]  # auto-returned
    staff_notices = client.get("/notices", headers=ctx["staff"]["headers"]).json()
    assert any("all requested items resolved" in n["text"] for n in staff_notices)
    assert any("baton auto-returned" in e["text"] for e in o["events"])

    # staff accepts one and re-requests another with a reason
    doc_item = next(i for i in o["items"] if i["kind"] == "document")
    o = ob_act(client, ctx, "staff", oid, f"items/{doc_item['id']}/accept")
    assert next(i for i in o["items"] if i["id"] == doc_item["id"])["accepted_at"]
    info_item = next(i for i in o["items"] if i["kind"] == "information")
    o = ob_act(client, ctx, "staff", oid, f"items/{info_item['id']}/re-request",
               {"reason": "Need the exact TRN certificate date, not the registration month"})
    assert next(i for i in o["items"] if i["id"] == info_item["id"])["status"] == "requested"
    assert o["open_items"] == 1


def test_not_available_reason_mandatory_and_withdraw(client):
    ctx = setup_firm(client)
    _, obs = start_onboardings(client, ctx)
    oid = obs[0]["id"]
    o = ob_act(client, ctx, "staff", oid, "items",
               {"items": [{"label": "MOA copy", "kind": "document"},
                          {"label": "UBO details", "kind": "information"}]})
    item_ids = [i["id"] for i in o["items"]]
    ob_act(client, ctx, "staff", oid, "send-requests")

    # missing reason → 422 (validation)
    r = client.post(f"/onboardings/{oid}/items/{item_ids[0]}/not-available", json={},
                    headers=ctx["manager"]["headers"])
    assert r.status_code == 422
    o = ob_act(client, ctx, "manager", oid, f"items/{item_ids[0]}/not-available",
               {"reason": "Company predates UBO regs — no MOA on record with sponsor"})
    assert next(i for i in o["items"] if i["id"] == item_ids[0])["status"] == "not_available"
    assert o["holder"] == ctx["manager"]["id"]  # one still open

    # staff withdraws the other while the baton is with the manager → auto-return fires
    o = ob_act(client, ctx, "staff", oid, f"items/{item_ids[1]}/withdraw",
               {"reason": "Manager confirmed in chat it's not needed"})
    assert next(i for i in o["items"] if i["id"] == item_ids[1])["status"] == "withdrawn"
    assert o["holder"] == ctx["staff"]["id"]


def test_credential_masked_and_reveal_logged(client):
    ctx = setup_firm(client)
    _, obs = start_onboardings(client, ctx)
    oid = obs[0]["id"]
    o = ob_act(client, ctx, "staff", oid, "items",
               {"items": [{"label": "FTA login", "kind": "credential"}]})
    item_id = o["items"][0]["id"]
    ob_act(client, ctx, "staff", oid, "send-requests")
    client.post(f"/onboardings/{oid}/items/{item_id}/provide",
                data={"answer_text": "user: gh / pass: Secret!9"}, headers=ctx["manager"]["headers"])

    # masked by default for everyone
    detail = client.get(f"/onboardings/{oid}", headers=ctx["staff"]["headers"]).json()
    it = detail["items"][0]
    assert it["credential_masked"] is True and "Secret" not in it["answer_text"]

    # reveal returns the value, writes the event, notifies the manager
    r = client.get(f"/onboardings/{oid}/items/{item_id}/reveal", headers=ctx["staff"]["headers"])
    assert r.status_code == 200 and r.json()["value"] == "user: gh / pass: Secret!9"
    detail = client.get(f"/onboardings/{oid}", headers=ctx["staff"]["headers"]).json()
    assert any('Credential "FTA login" viewed by Priya Nair' in e["text"] for e in detail["events"])
    mgr_notices = client.get("/notices", headers=ctx["manager"]["headers"]).json()
    assert any("viewed by Priya Nair" in n["text"] for n in mgr_notices)
    # accountant may not reveal
    assert client.get(f"/onboardings/{oid}/items/{item_id}/reveal",
                      headers=ctx["accountant"]["headers"]).status_code == 409


def test_qualifier_registry_and_unaudited_chip(client):
    ctx = setup_firm(client)
    _, obs = start_onboardings(client, ctx)
    oid = obs[0]["id"]
    o = ob_act(client, ctx, "staff", oid, "items",
               {"items": [{"label": "FY2025 financials", "kind": "document"}]})
    item_id = o["items"][0]["id"]
    ob_act(client, ctx, "staff", oid, "send-requests")
    client.post(f"/onboardings/{oid}/items/{item_id}/provide",
                data={"qualifier": "unaudited"},
                files=[("evidence", ("FY2025 Financials.xlsx", b"xlsx-bytes", "application/octet-stream"))],
                headers=ctx["manager"]["headers"])

    cl = client.get("/clients", headers=ctx["manager"]["headers"]).json()[0]
    assert cl["unaudited_on_file"] is True

    reg = client.get(f"/clients/{cl['id']}/documents", headers=ctx["manager"]["headers"]).json()
    assert reg["unaudited_on_file"] is True
    by_name = {d["name"]: d for d in reg["documents"]}
    # the client-signed proposal from Proposal & Engagement is aggregated too
    assert any(d["source"].startswith("Proposal & Engagement") for d in reg["documents"])
    fin = by_name["FY2025 Financials.xlsx"]
    assert fin["qualifier"] == "unaudited"
    assert fin["source"] == "Onboarding — Bookkeeping (Monthly)" or fin["source"].startswith("Onboarding — ")
    assert fin["uploaded_by"] == "Rashid Al Mansoori"


def test_completion_creates_duty_and_guards(client):
    ctx = setup_firm(client)
    _, obs = start_onboardings(client, ctx)
    oid = next(o["id"] for o in obs if o["service"] == "VAT Filing")
    o = ob_act(client, ctx, "staff", oid, "items",
               {"items": [{"label": "Trade license", "kind": "document"}]})
    item_id = o["items"][0]["id"]
    ob_act(client, ctx, "staff", oid, "send-requests")

    first_due = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
    body = {"cadence": "quarterly", "first_due": first_due,
            "contact_name": "Mariam", "contact_email": "mariam@gulfhorizon.ae"}
    # blocked while an item is open
    r = ob_act(client, ctx, "staff", oid, "complete", body, expect=409)
    assert "still open" in r["detail"]["reason"]

    client.post(f"/onboardings/{oid}/items/{item_id}/provide",
                files=[("evidence", ("tl.pdf", b"%PDF", "application/pdf"))],
                headers=ctx["manager"]["headers"])
    # manager cannot complete
    ob_act(client, ctx, "manager", oid, "complete", body, expect=409)
    out = ob_act(client, ctx, "staff", oid, "complete", body)
    ob = out["onboarding"]
    assert ob["status"] == "complete" and ob["holder"] is None and ob["duty_id"] == out["duty"]["id"]
    assert any("Onboarding complete — recurring duty created: VAT Filing, quarterly, first due" in e["text"]
               for e in ob["events"])

    # the duty landed in the deadline engine, correctly shaped
    duties = client.get("/duties", headers=ctx["staff"]["headers"]).json()
    d = next(x for x in duties if x["service"] == "VAT Filing" and x["client_id"] == ob["client_id"])
    assert d["cadence"] == "quarterly" and d["kind"] == "vat" and not d["closed"]
    assert d["contact"] == {"name": "Mariam", "email": "mariam@gulfhorizon.ae"}
    assert d["client_name"] == "Gulf Horizon Trading LLC"
    # completing again is refused
    ob_act(client, ctx, "staff", oid, "complete", body, expect=409)


def test_onboarding_tenancy_isolation(client):
    ctx = setup_firm(client)
    _, obs = start_onboardings(client, ctx)
    oid = obs[0]["id"]

    boot_b = bootstrap_tenant(client, email="hello@betabooks.ae", user_email_domain="betabooks.ae")
    admin_b = next(u for u in boot_b["users"] if u["role"] == "Admin")
    tokens_b = login_after_reset(client, admin_b["email"], admin_b["temp_password"])
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}
    assert client.get(f"/onboardings/{oid}", headers=headers_b).status_code == 404
    assert oid not in {o["id"] for o in client.get("/onboardings", headers=headers_b).json()}
