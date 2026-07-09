"""Firm-wide performance: duty-completion stars, client task history, employee roll-up."""

from app.routers.performance import DAY_MS, duty_stars

from .test_duties_payments import complete, make_duty
from .test_onboarding import act, create_proposal, drive_to_el_approved, setup_firm


def el_sent(client, ctx, pid):
    drive_to_el_approved(client, ctx, pid, advance_pct=0)
    return act(client, ctx, "manager", pid, "el-send", {"to": "a@b.ae", "subject": "EL", "body": "x"})


# ---------- duty star boundaries ----------

def test_duty_star_boundaries_including_declared_cap():
    assert duty_stars(0, "proof") == 5
    assert duty_stars(-5 * DAY_MS, "sent") == 5          # early
    assert duty_stars(1, "proof") == 4                    # any lateness at all
    assert duty_stars(DAY_MS, "proof") == 4               # exactly 1d late
    assert duty_stars(DAY_MS + 1, "proof") == 3
    assert duty_stars(3 * DAY_MS, "proof") == 3
    assert duty_stars(3 * DAY_MS + 1, "proof") == 2
    assert duty_stars(7 * DAY_MS, "proof") == 2
    assert duty_stars(7 * DAY_MS + 1, "proof") == 1
    # declared caps at 3 — no proof of work — but never raises a worse score
    assert duty_stars(0, "declared") == 3
    assert duty_stars(DAY_MS, "declared") == 3
    assert duty_stars(8 * DAY_MS, "declared") == 1


# ---------- employee aggregation ----------

def test_employee_aggregation_and_guards(client):
    ctx = setup_firm(client)
    pid = create_proposal(client, ctx)["id"]
    el_sent(client, ctx, pid)  # everyone's proposal stars are 5 in a seconds-long test run

    # staff completes two one-time duties (they close): on-time proof (5★), on-time declared (capped 3★)
    d1 = make_duty(client, ctx, cadence="one-time", days_until_due=5, service="VAT Filing", client_name="Al Dana")
    complete(client, ctx, d1["id"], "proof", files=[("r.pdf", b"%PDF")],
             record={"period": "Q2 2026", "position": "Nil"})
    d2 = make_duty(client, ctx, cadence="one-time", days_until_due=5, service="ESR advisory call", client_name="Marwa")
    complete(client, ctx, d2["id"], "declared", reason="advisory call held")
    # open workload: one more open duty (not completed)
    make_duty(client, ctx, days_until_due=10, service="Audit Support", client_name="Marwa")

    # staff must not see the roll-up
    assert client.get("/performance/employees", headers=ctx["staff"]["headers"]).status_code == 403

    r = client.get("/performance/employees", headers=ctx["manager"]["headers"]).json()
    staff_row = next(e for e in r["employees"] if e["name"] == "Priya Nair")
    assert staff_row["proposal_count"] == 1 and staff_row["proposal_avg_stars"] == 5
    assert staff_row["duty_count"] == 2 and abs(staff_row["duties_avg_stars"] - 4.0) < 1e-9  # (5+3)/2
    # overall = mean of ALL events, not mean of the two averages: (5+5+3)/3
    assert abs(staff_row["overall_avg"] - (5 + 5 + 3) / 3) < 1e-9
    assert staff_row["event_count"] == 3
    assert staff_row["open_workload"]["open_duties"] == 1
    assert staff_row["open_workload"]["held_proposals"] == 0  # P-001 is closed
    labels = [e["label"] for e in staff_row["recent_events"]]
    assert "VAT Filing — Al Dana" in labels and any(l.startswith("Proposal P-001") for l in labels)

    # sorted overall desc, employees with no events at the bottom
    overalls = [e["overall_avg"] for e in r["employees"]]
    scored = [x for x in overalls if x is not None]
    assert scored == sorted(scored, reverse=True)
    assert all(x is not None for x in overalls[:len(scored)])
    assert "declared without proof capped" in r["duty_stars_scale_text"]
    assert r["proposal_stars_scale_text"].startswith("≤½ day")

    # a held open proposal counts toward workload
    p2 = client.post("/proposals", json={
        "prospect": {"name": "Marwa Boutique LLC", "email": "m@marwa.ae"},
        "services": [{"name": "VAT Filing", "fee": "5000", "basis": "per quarter"}],
        "assigned_to": ctx["staff"]["id"],
    }, headers=ctx["manager"]["headers"])
    assert p2.status_code == 201
    r = client.get("/performance/employees", headers=ctx["manager"]["headers"]).json()
    staff_row = next(e for e in r["employees"] if e["name"] == "Priya Nair")
    assert staff_row["open_workload"]["held_proposals"] == 1
    assert staff_row["open_workload"]["total"] == 2


# ---------- client task history ----------

def test_client_performance_history(client):
    ctx = setup_firm(client)
    pid = create_proposal(client, ctx)["id"]  # prospect "Gulf Horizon Trading LLC"
    el_sent(client, ctx, pid)

    # a duty for this client completed 2 days late with a filing record
    d = make_duty(client, ctx, days_until_due=-2, service="VAT Filing",
                  client_name="Gulf Horizon Trading LLC")
    complete(client, ctx, d["id"], "proof", files=[("fta-ack.pdf", b"%PDF")],
             record={"period": "Q2 2026", "position": "Payable"})

    cl = client.get("/clients", headers=ctx["manager"]["headers"]).json()[0]
    # staff must not see client performance
    assert client.get(f"/clients/{cl['id']}/performance", headers=ctx["staff"]["headers"]).status_code == 403

    r = client.get(f"/clients/{cl['id']}/performance", headers=ctx["manager"]["headers"]).json()
    assert r["client"]["ref"] == "CL-001"
    # originating proposal cycle summary present after el_sent
    cyc = r["proposal_cycle"]
    assert cyc["ref"] == "P-001" and cyc["total_ms"] > 0
    assert {e["name"] for e in cyc["per_employee"]} >= {"Priya Nair", "Rashid Al Mansoori", "Ayesha Khan"}
    assert all(e["stars"] == 5 for e in cyc["per_employee"])
    # task record
    t = r["tasks"][0]
    assert t["service"] == "VAT Filing" and t["staff_name"] == "Priya Nair"
    assert t["period"] == "Q2 2026" and t["method"] == "proof"
    assert t["timing"] == "2d late" and t["stars"] == 3
    assert "capped" in r["duty_stars_scale_text"]
