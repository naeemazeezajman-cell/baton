"""Firm-definable performance targets (versioned config drives star thresholds, applies
to future scoring only) + the manager-only firm-wide pending board."""

from .test_duties_payments import complete, make_duty
from .test_onboarding import act, create_proposal, drive_to_el_approved, setup_firm

DAY_MS = 86400000


def el_sent(client, ctx, pid):
    drive_to_el_approved(client, ctx, pid, advance_pct=0)
    return act(client, ctx, "manager", pid, "el-send", {"to": "a@b.ae", "subject": "EL", "body": "x"})


def put_config(client, ctx, config, note="tightening the standard", expect=200):
    r = client.put("/performance/config", json={"config": config, "note": note},
                   headers=ctx["manager"]["headers"])
    assert r.status_code == expect, r.text
    return r.json()


def test_config_drives_duty_star_thresholds_with_version_stamping(client):
    ctx = setup_firm(client)
    # 2 days late under the DEFAULTS: ≤3d grace band → 3★ (config version 0)
    d1 = make_duty(client, ctx, cadence="one-time", days_until_due=-2,
                   service="VAT Filing", client_name="Al Dana")
    complete(client, ctx, d1["id"], "proof", files=[("r.pdf", b"%PDF")],
             record={"period": "Q2 2026", "position": "Nil"})

    # the firm relaxes the bands: up to 5 days late still earns 4★
    out = put_config(client, ctx, {"duty": {"grace_bands": [5, 10, 15]}})
    assert out["version"] == 1

    # the same lateness AFTER the change → 4★, scored under version 1
    d2 = make_duty(client, ctx, cadence="one-time", days_until_due=-2,
                   service="VAT Filing", client_name="Marwa")
    complete(client, ctx, d2["id"], "proof", files=[("r.pdf", b"%PDF")],
             record={"period": "Q3 2026", "position": "Nil"})

    r = client.get("/performance/employees", headers=ctx["manager"]["headers"]).json()
    staff_row = next(e for e in r["employees"] if e["name"] == "Priya Nair")
    by_label = {e["label"]: e for e in staff_row["recent_events"] if e["source"] == "duty"}
    before = by_label["VAT Filing — Al Dana"]
    after = by_label["VAT Filing — Marwa"]
    # history is NOT rewritten — the older completion keeps the version that governed it
    assert before["stars"] == 3 and before["config_version"] == 0
    assert after["stars"] == 4 and after["config_version"] == 1
    # the active standard is visible on the roll-up for everyone to see
    assert r["config_version"] == 1
    assert r["targets"]["duty"]["grace_bands"] == [5, 10, 15]
    assert "≤5d late ★4" in r["duty_stars_scale_text"]


def test_config_validation_history_and_role_scoping(client):
    ctx = setup_firm(client)
    put_config(client, ctx, {"duty": {"grace_bands": [7, 3, 1]}}, expect=422)        # not increasing
    put_config(client, ctx, {"proposal": {"hold_target_days": -1}}, expect=422)
    r = client.put("/performance/config", json={"config": {}},
                   headers=ctx["manager"]["headers"])
    assert r.status_code == 422  # the note (why the standard changed) is mandatory

    put_config(client, ctx, {"proposal": {"hold_target_days": 1, "cycle_target_days": 10}},
               note="doubling hold allowance")
    out = put_config(client, ctx,
                     {"proposal": {"hold_target_days": 1, "cycle_target_days": 10},
                      "invoicing": {"target_days": 5}},
                     note="invoice within 5 days of EL send")
    assert out["version"] == 2

    g = client.get("/performance/config", headers=ctx["manager"]["headers"]).json()
    assert g["version"] == 2
    assert g["config"]["proposal"]["cycle_target_days"] == 10
    assert g["config"]["invoicing"]["target_days"] == 5
    assert g["config"]["duty"]["grace_bands"] == [1.0, 3.0, 7.0]  # untouched → defaults
    # append-only history, newest first, note + author preserved (the change log)
    assert [h["version"] for h in g["history"]] == [2, 1]
    assert g["history"][1]["note"] == "doubling hold allowance"
    assert g["history"][0]["by"] == "Rashid Al Mansoori"

    # staff can neither read nor write the firm standard
    assert client.get("/performance/config", headers=ctx["staff"]["headers"]).status_code == 403
    assert client.put("/performance/config", json={"config": {}, "note": "x"},
                      headers=ctx["staff"]["headers"]).status_code == 403


def test_pending_board_aggregates_all_work_types(client):
    ctx = setup_firm(client)
    # a held proposal: created and assigned to staff, still open (distinct prospect —
    # an open proposal for the same prospect would 409)
    r = client.post("/proposals", json={
        "prospect": {"name": "Marwa Boutique LLC", "email": "m@marwa.ae"},
        "services": [{"name": "VAT Filing", "fee": "5000", "basis": "per quarter"}],
        "assigned_to": ctx["staff"]["id"],
    }, headers=ctx["manager"]["headers"])
    assert r.status_code == 201, r.text
    # a full cycle to EL send: creates in-progress onboardings (held) + unraised payments
    pid = create_proposal(client, ctx)["id"]
    el_sent(client, ctx, pid)
    # an open duty already 3 days past its statutory deadline
    make_duty(client, ctx, days_until_due=-3, service="VAT Filing", client_name="Overdue Client")

    # staff cannot see the firm-wide board
    assert client.get("/performance/pending", headers=ctx["staff"]["headers"]).status_code == 403

    b = client.get("/performance/pending", headers=ctx["manager"]["headers"]).json()
    all_items = [i for p in b["people"] for i in p["items"]]
    assert {"proposal", "onboarding", "duty", "invoice"} <= {i["type"] for i in all_items}

    # the overdue duty is flagged, with ~3 days of overdue age and a due date
    duty_item = next(i for i in all_items if i["type"] == "duty" and i["label"] == "Overdue Client")
    assert duty_item["overdue"] is True and duty_item["due_at"] is not None
    assert 2.9 * DAY_MS < duty_item["age_ms"] < 3.1 * DAY_MS

    # held work is grouped under the staff holder, aging-sorted, with per-person counts
    staff_person = next(p for p in b["people"] if p["user_id"] == ctx["staff"]["id"])
    assert staff_person["counts"]["total"] == len(staff_person["items"]) >= 3
    assert staff_person["counts"]["overdue"] >= 1
    ages = [i["age_ms"] or 0 for i in staff_person["items"]]
    assert ages == sorted(ages, reverse=True)
    assert any(i["type"] == "proposal" and i["pending_since"] for i in staff_person["items"])
    assert any(i["type"] == "onboarding" for i in staff_person["items"])

    # unraised invoices sit with the in-house accountant
    acct_person = next(p for p in b["people"] if p["role"] == "Accountant")
    invoices = [i for i in acct_person["items"] if i["type"] == "invoice"]
    assert invoices and all("invoice not raised" in i["sublabel"] for i in invoices)

    # feature-1 targets drive the highlight: a near-zero hold target flags every held item
    assert all(i["over_target"] is False for i in all_items if i["type"] == "proposal")
    put_config(client, ctx, {"proposal": {"hold_target_days": 1e-9},
                             "onboarding": {"hold_target_days": 1e-9}}, note="strict SLA")
    b2 = client.get("/performance/pending", headers=ctx["manager"]["headers"]).json()
    items2 = [i for p in b2["people"] for i in p["items"]]
    assert all(i["over_target"] for i in items2 if i["type"] in ("proposal", "onboarding"))
    assert b2["config_version"] == 1
    assert b2["targets"]["proposal"]["hold_target_days"] == 1e-9
