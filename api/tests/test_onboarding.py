"""Phase 2 — onboarding workflow API. The prototype's reducer logic is the spec."""

from datetime import datetime

from .conftest import BOOTSTRAP_PAYLOAD, login_after_reset

FIRM_USERS = [
    {"name": "Rashid Al Mansoori", "designation": "Engagement Manager", "email": "rashid@alphaledger.ae",
     "role": "Manager", "signatory": True},
    {"name": "Priya Nair", "designation": "Senior Accountant", "email": "priya@alphaledger.ae", "role": "Staff"},
    {"name": "Ayesha Khan", "designation": "Managing Partner", "email": "ayesha@alphaledger.ae",
     "role": "Admin", "signatory": True},
    {"name": "Fatima Zahran", "designation": "Finance Executive", "email": "fatima@alphaledger.ae",
     "role": "Accountant"},
]

SERVICES = [
    {"name": "Bookkeeping (Monthly)", "fee": "2000", "basis": "per month"},
    {"name": "VAT Filing", "fee": "6000", "basis": "per quarter"},
]

# same lines in the draft schema's shape (service, not name)
DLINES = [{"service": s["name"], "fee": s["fee"], "basis": s["basis"]} for s in SERVICES]


def setup_firm(client):
    payload = {**BOOTSTRAP_PAYLOAD, "employees": FIRM_USERS}
    r = client.post("/tenants/bootstrap", json=payload)
    assert r.status_code == 201, r.text
    boot = r.json()
    ctx = {}
    for u in boot["users"]:
        tokens = login_after_reset(client, u["email"], u["temp_password"], f"Xx-{u['role']}-pass1!")
        ctx[u["role"].lower()] = {
            "id": u["id"],
            "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
        }
    return ctx


def act(client, ctx, who, pid, action, json=None, expect=200, **kw):
    r = client.post(f"/proposals/{pid}/{action}", json=json, headers=ctx[who]["headers"], **kw)
    assert r.status_code == expect, f"{action}: {r.status_code} {r.text}"
    return r.json()


def create_proposal(client, ctx, services=SERVICES, terms="50% advance, blance on delivery, quaterly VAT filing"):
    r = client.post("/proposals", json={
        "prospect": {"name": "Gulf Horizon Trading LLC", "email": "accounts@gulfhorizon.ae"},
        "services": services,
        "assigned_to": ctx["staff"]["id"],
        "payment_terms_rough": terms,
    }, headers=ctx["manager"]["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def drive_to_drafting(client, ctx, pid):
    """assigned → docs_with_manager → waiver_review → drafting, exercising the slot lifecycle."""
    p = act(client, ctx, "staff", pid, "request-items", {"slots": [
        {"kind": "data", "label": "Confirm TRN"},
        {"kind": "document", "label": "Trade license copy"},
    ]})
    assert p["status"] == "docs_with_manager"
    data_slot, doc_slot = p["checklist"]

    act(client, ctx, "manager", pid, "provide-item", {"slot_id": data_slot["id"], "value": "TRN 100-1234-5678-901"})
    act(client, ctx, "manager", pid, "waive", {"slot_id": doc_slot["id"], "action": "request",
                                               "reason": "License renewal in progress at DED"})
    p = act(client, ctx, "manager", pid, "return-checklist")
    assert p["status"] == "waiver_review"
    assert p["holder"] == ctx["staff"]["id"]

    p = act(client, ctx, "staff", pid, "waive", {"slot_id": doc_slot["id"], "action": "approve"})
    assert {s["status"] for s in p["checklist"]} == {"provided", "waived"}
    p = act(client, ctx, "staff", pid, "start-drafting")
    assert p["status"] == "drafting"
    return p


def drive_to_signed(client, ctx, pid):
    drive_to_drafting(client, ctx, pid)
    g = act(client, ctx, "staff", pid, "generate", {"draft": {
        "lines": DLINES, "payment_terms": "50% in advance; balance within 14 days",
        "validity_days": 30, "scope": "As per engaged services",
    }, "note": "initial draft"})
    v = g["version"]["v"]
    p = act(client, ctx, "staff", pid, "submit", {"version": v})
    assert p["status"] == "manager_review"
    p = act(client, ctx, "manager", pid, "sign-route", {"signatory_id": ctx["admin"]["id"], "note": "Standard terms"})
    assert p["status"] == "senior_review"
    assert p["signatures"]["manager"]["by"] == ctx["manager"]["id"]
    p = act(client, ctx, "admin", pid, "senior-approve")
    assert p["status"] == "signed"
    return p


def drive_to_el_approved(client, ctx, pid, advance_pct):
    drive_to_signed(client, ctx, pid)
    act(client, ctx, "manager", pid, "send-client", {"to": "accounts@gulfhorizon.ae",
                                                     "subject": "Proposal", "body": "Please find attached."})
    r = act(client, ctx, "manager", pid, "upload-signed", expect=200, json=None,
            files={"file": ("Signed Proposal.pdf", b"%PDF-1.4 signed", "application/pdf")})
    assert r["proposal"]["status"] == "el_staffing"
    for svc in [s["name"] for s in SERVICES]:
        act(client, ctx, "manager", pid, "staff-activity", {"service": svc, "staff_id": ctx["staff"]["id"]})
    if advance_pct:
        act(client, ctx, "manager", pid, "el-plan", {"advance_pct": advance_pct})
    p = act(client, ctx, "manager", pid, "el-route", {"signatory_id": ctx["admin"]["id"]})
    assert p["status"] == "el_senior_review"
    p = act(client, ctx, "admin", pid, "el-sign")
    assert p["status"] == "el_approved"
    return r["client"]


def test_full_happy_path_request_to_el_sent(client):
    ctx = setup_firm(client)
    p = create_proposal(client, ctx)
    pid = p["id"]
    assert p["ref"] == "P-001"
    assert p["status"] == "assigned"
    assert p["holder"] == ctx["staff"]["id"]

    made_client = drive_to_el_approved(client, ctx, pid, advance_pct=50)
    assert made_client["ref"] == "CL-001"

    out = act(client, ctx, "manager", pid, "el-send", {"to": "accounts@gulfhorizon.ae",
                                                       "subject": "Engagement Letter", "body": "EL attached."})
    p = out["proposal"]
    assert p["status"] == "el_sent"
    assert p["holder"] is None
    assert p["el"]["sent_at"]

    # payment schedule per the prototype's advance-basis rules: first_bill = 8000
    pays = {x["label"]: x for x in out["payments"]}
    assert len(pays) == 4
    t0 = datetime.fromisoformat(p["el"]["sent_at"])
    day = lambda label: round((datetime.fromisoformat(pays[label]["due_at"]) - t0).total_seconds() / 86400)  # noqa: E731
    assert pays["Advance (50%) — first billing period"]["amount"] == 4000 and day("Advance (50%) — first billing period") == 0
    assert pays["Balance (50%) — first billing period"]["amount"] == 4000 and day("Balance (50%) — first billing period") == 14
    assert pays["Recurring — Bookkeeping (Monthly) (next month)"]["amount"] == 2000 and day("Recurring — Bookkeeping (Monthly) (next month)") == 30
    assert pays["Recurring — VAT Filing (next quarter)"]["amount"] == 6000 and day("Recurring — VAT Filing (next quarter)") == 90

    # audit trail: full event history is present and Part 1 completion is recorded
    detail = client.get(f"/proposals/{pid}", headers=ctx["manager"]["headers"]).json()
    texts = [e["text"] for e in detail["events"]]
    assert any("ONBOARDING PART 1 COMPLETE" in t for t in texts)
    assert any(e["kind"] == "email" for e in detail["events"])
    # every holder-log row is closed at completion
    assert all(h["ended_at"] for h in detail["holder_log"])


def test_no_advance_payment_basis(client):
    ctx = setup_firm(client)
    pid = create_proposal(client, ctx)["id"]
    drive_to_el_approved(client, ctx, pid, advance_pct=0)
    out = act(client, ctx, "manager", pid, "el-send", {"to": "a@b.ae", "subject": "EL", "body": "x"})
    pays = {x["label"]: x for x in out["payments"]}
    assert len(pays) == 2
    t0 = datetime.fromisoformat(out["proposal"]["el"]["sent_at"])
    day = lambda label: round((datetime.fromisoformat(pays[label]["due_at"]) - t0).total_seconds() / 86400)  # noqa: E731
    # monthly in arrears (+30d); quarterly billed in advance (due now)
    assert day("First period — Bookkeeping (Monthly) (per month)") == 30
    assert day("First period — VAT Filing (per quarter)") == 0


def test_rejection_loop_voids_manager_signature(client):
    ctx = setup_firm(client)
    pid = create_proposal(client, ctx)["id"]
    drive_to_drafting(client, ctx, pid)
    g = act(client, ctx, "staff", pid, "generate", {"draft": {"lines": DLINES, "payment_terms": "Net 14",
                                                              "validity_days": 30, "scope": ""}, "note": "v1"})
    act(client, ctx, "staff", pid, "submit", {"version": g["version"]["v"]})
    act(client, ctx, "manager", pid, "sign-route", {"signatory_id": ctx["admin"]["id"]})

    p = act(client, ctx, "admin", pid, "senior-reject", {"note": "Fee for VAT Filing is below our floor"})
    assert p["status"] == "manager_review"
    assert p["signatures"]["manager"] is None  # voided
    assert p["last_rejection"]["stage"] == "proposal"
    assert p["holder"] == ctx["manager"]["id"]

    # manager review fork: return-to-drafter with mandatory instruction
    p = act(client, ctx, "manager", pid, "send-for-revision", {"comment": "Raise VAT Filing to 7500 as agreed"})
    assert p["status"] == "drafting" and p["revision_note"]["text"].startswith("Raise VAT")

    revised = [dict(DLINES[0]), {**DLINES[1], "fee": "7500"}]
    g2 = act(client, ctx, "staff", pid, "generate", {"draft": {"lines": revised, "payment_terms": "Net 14",
                                                               "validity_days": 30, "scope": ""}, "note": "revised"})
    act(client, ctx, "staff", pid, "submit", {"version": g2["version"]["v"]})
    act(client, ctx, "manager", pid, "sign-route", {"signatory_id": ctx["admin"]["id"]})
    p = act(client, ctx, "admin", pid, "senior-approve")
    assert p["status"] == "signed"

    # the regeneration logged an exact field-level diff
    detail = client.get(f"/proposals/{pid}", headers=ctx["manager"]["headers"]).json()
    diffs = [e for e in detail["events"] if e["kind"] == "diff"]
    assert any("VAT Filing: fee AED 6,000 → AED 7,500" in e["text"] for e in diffs)


def test_dirty_version_guard(client):
    ctx = setup_firm(client)
    pid = create_proposal(client, ctx)["id"]
    drive_to_drafting(client, ctx, pid)
    act(client, ctx, "staff", pid, "generate", {"draft": {"lines": DLINES, "payment_terms": "Net 30",
                                                          "validity_days": 30, "scope": ""}, "note": "v1"})
    act(client, ctx, "staff", pid, "generate", {"draft": {"lines": DLINES, "payment_terms": "Net 14",
                                                          "validity_days": 30, "scope": ""}, "note": "v2"})
    # submitting the superseded version is refused
    r = act(client, ctx, "staff", pid, "submit", {"version": 1}, expect=409)
    assert "not the latest" in r["detail"]["reason"]
    p = act(client, ctx, "staff", pid, "submit", {"version": 2})
    assert p["status"] == "manager_review"


def test_conversion_on_upload_signed(client):
    ctx = setup_firm(client)
    pid = create_proposal(client, ctx)["id"]
    drive_to_signed(client, ctx, pid)
    act(client, ctx, "manager", pid, "send-client", {"to": "a@b.ae", "subject": "Proposal", "body": "x"})
    out = act(client, ctx, "manager", pid, "upload-signed",
              files={"file": ("Client Signed.pdf", b"%PDF-1.4 client signed", "application/pdf")})
    assert out["client"]["ref"] == "CL-001"
    p = out["proposal"]
    assert p["status"] == "el_staffing"
    assert p["client_id"] == out["client"]["id"]
    assert p["el"] == {"note": "", "advance_pct": 0, "signatory_id": None, "signature": None,
                       "sent_at": None, "assignments": {}}
    # converting twice is refused
    act(client, ctx, "manager", pid, "upload-signed", expect=409,
        files={"file": ("dup.pdf", b"%PDF", "application/pdf")})


def test_role_holder_and_status_guards(client):
    ctx = setup_firm(client)
    pid = create_proposal(client, ctx)["id"]
    # drafter cannot sign-route; manager cannot request items
    act(client, ctx, "staff", pid, "sign-route", {"signatory_id": ctx["admin"]["id"]}, expect=409)
    act(client, ctx, "manager", pid, "request-items", {"slots": [{"kind": "data", "label": "x"}]}, expect=409)
    # manager cannot return the checklist while items are outstanding
    p = act(client, ctx, "staff", pid, "request-items", {"slots": [{"kind": "data", "label": "Confirm TRN"}]})
    act(client, ctx, "manager", pid, "return-checklist", expect=409)
    # drafter may withdraw while the baton is with the manager
    slot = p["checklist"][0]
    p = act(client, ctx, "staff", pid, "withdraw-item", {"slot_id": slot["id"], "reason": "No longer needed"})
    assert p["checklist"][0]["status"] == "withdrawn"
    # signature routing rejects non-signatories: accountant is not an Admin signatory
    act(client, ctx, "manager", pid, "return-checklist")  # nothing outstanding now → drafting
    g = act(client, ctx, "staff", pid, "generate", {"draft": {"lines": DLINES, "payment_terms": "",
                                                              "validity_days": 30, "scope": ""}, "note": "v1"})
    act(client, ctx, "staff", pid, "submit", {"version": g["version"]["v"]})
    act(client, ctx, "manager", pid, "sign-route", {"signatory_id": ctx["accountant"]["id"]}, expect=409)


def test_files_and_workload(client):
    ctx = setup_firm(client)
    pid = create_proposal(client, ctx)["id"]
    # upload a supporting document, then answer a checklist slot with it
    p = act(client, ctx, "staff", pid, "request-items", {"slots": [{"kind": "document", "label": "Trade license"}]})
    r = client.post("/files", data={"entity": "proposal", "entity_id": pid},
                    files={"file": ("Trade License.pdf", b"%PDF-1.4 license", "application/pdf")},
                    headers=ctx["manager"]["headers"])
    assert r.status_code == 201, r.text
    file_id = r.json()["id"]
    p = act(client, ctx, "manager", pid, "provide-item", {"slot_id": p["checklist"][0]["id"], "file_id": file_id})
    assert p["checklist"][0]["file_name"] == "Trade License.pdf"

    # local dev mode: /link returns a signed download URL that serves the bytes
    link = client.get(f"/files/{file_id}/link", headers=ctx["manager"]["headers"]).json()
    assert link["url"].startswith(f"/files/{file_id}/download?token=")
    dl = client.get(link["url"])
    assert dl.status_code == 200 and dl.content == b"%PDF-1.4 license"

    # workload summary counts the active proposal against the drafter
    wl = client.get("/users/workload", headers=ctx["manager"]["headers"]).json()
    staff_row = next(u for u in wl if u["id"] == ctx["staff"]["id"])
    assert staff_row["active_proposals"] == 1 and staff_row["workload"] >= 1
