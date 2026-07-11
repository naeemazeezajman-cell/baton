"""Onboarding closure: sealed trails, holder-log spans, holding-time stars, roll-up
inclusion, and backfill correctness."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text as sql

from app.db import engine
from app.onboarding_backfill import backfill_onboarding_holder_log
from .test_onboarding import setup_firm
from .test_onboarding_module import ob_act, start_onboardings


def run_two_round_relay(client, ctx, oid):
    """Scripted relay: staff→manager→staff, twice, then complete. Spans expected:
    staff 3 holdings, manager 2."""
    # round 1
    o = ob_act(client, ctx, "staff", oid, "items",
               {"items": [{"label": "Trade license", "kind": "document"}]})
    item_id = o["items"][0]["id"]
    ob_act(client, ctx, "staff", oid, "send-requests")
    r = client.post(f"/onboardings/{oid}/items/{item_id}/provide",
                    files=[("evidence", ("tl.pdf", b"%PDF", "application/pdf"))],
                    headers=ctx["manager"]["headers"])
    assert r.status_code == 200
    # round 2: re-request the same item, send again, manager provides again
    ob_act(client, ctx, "staff", oid, f"items/{item_id}/re-request", {"reason": "Need the renewed license"})
    ob_act(client, ctx, "staff", oid, "send-requests")
    r = client.post(f"/onboardings/{oid}/items/{item_id}/provide",
                    files=[("evidence", ("tl-renewed.pdf", b"%PDF", "application/pdf"))],
                    headers=ctx["manager"]["headers"])
    assert r.status_code == 200
    # complete
    first_due = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
    out = ob_act(client, ctx, "staff", oid, "complete",
                 {"cadence": "quarterly", "first_due": first_due,
                  "contact_name": "Mariam", "contact_email": "mariam@gulfhorizon.ae"})
    return out["onboarding"]


def test_sealing_blocks_all_mutations_read_stays_open(client):
    ctx = setup_firm(client)
    _, obs = start_onboardings(client, ctx)
    oid = obs[0]["id"]
    # resolve one credential item so reveal has something to return after sealing
    o = ob_act(client, ctx, "staff", oid, "items",
               {"items": [{"label": "FTA login", "kind": "credential"}]})
    item_id = o["items"][0]["id"]
    ob_act(client, ctx, "staff", oid, "send-requests")
    client.post(f"/onboardings/{oid}/items/{item_id}/provide",
                data={"username": "gh", "password": "Secret!9"}, headers=ctx["manager"]["headers"])
    first_due = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
    ob_act(client, ctx, "staff", oid, "complete",
           {"cadence": "quarterly", "first_due": first_due,
            "contact_name": "M", "contact_email": "m@gulfhorizon.ae"})

    # every mutation endpoint refuses on the sealed onboarding
    ob_act(client, ctx, "staff", oid, "items",
           {"items": [{"label": "x", "kind": "document"}]}, expect=409)
    ob_act(client, ctx, "staff", oid, "send-requests", expect=409)
    r = client.post(f"/onboardings/{oid}/items/{item_id}/provide",
                    data={"answer_text": "x"}, headers=ctx["manager"]["headers"])
    assert r.status_code == 409
    ob_act(client, ctx, "manager", oid, f"items/{item_id}/not-available", {"reason": "x"}, expect=409)
    ob_act(client, ctx, "staff", oid, f"items/{item_id}/accept", expect=409)
    ob_act(client, ctx, "staff", oid, f"items/{item_id}/re-request", {"reason": "x"}, expect=409)
    ob_act(client, ctx, "staff", oid, f"items/{item_id}/withdraw", {"reason": "x"}, expect=409)
    ob_act(client, ctx, "staff", oid, "complete",
           {"cadence": "quarterly", "first_due": first_due,
            "contact_name": "M", "contact_email": "m@gulfhorizon.ae"}, expect=409)

    # reads stay open: detail, and credential reveal (still needed for recurring work, still logged)
    assert client.get(f"/onboardings/{oid}", headers=ctx["staff"]["headers"]).status_code == 200
    r = client.get(f"/onboardings/{oid}/items/{item_id}/reveal", headers=ctx["staff"]["headers"])
    assert r.status_code == 200 and r.json()["credential"]["password"] == "Secret!9"


def test_star_math_two_round_relay_scores_both_participants(client):
    ctx = setup_firm(client)
    _, obs = start_onboardings(client, ctx)
    ob = next(o for o in obs if o["service"] == "VAT Filing")
    completed = run_two_round_relay(client, ctx, ob["id"])

    # sealing event present
    assert any("ONBOARDING COMPLETE — VAT Filing for Gulf Horizon Trading LLC in " in e["text"]
               and "Trail sealed." in e["text"] for e in completed["events"])

    # staff (completer) sees the sealed record but NOT the stars
    assert "stars" not in completed or completed.get("stars") is None
    staff_detail = client.get(f"/onboardings/{ob['id']}", headers=ctx["staff"]["headers"]).json()
    assert staff_detail.get("stars") is None

    # manager sees per-participant stars: staff 3 holdings, manager 2, sub-day avg → 5 stars
    detail = client.get(f"/onboardings/{ob['id']}", headers=ctx["manager"]["headers"]).json()
    stars = {s["user_id"]: s for s in detail["stars"]}
    assert set(stars) == {ctx["staff"]["id"], ctx["manager"]["id"]}
    assert stars[ctx["staff"]["id"]]["holdings"] == 3
    assert stars[ctx["manager"]["id"]]["holdings"] == 2
    for s in stars.values():
        assert s["stars"] == 5 and s["total_held_ms"] >= 0

    # the holder_log spans exist and are all closed
    with engine.begin() as conn:
        rows = conn.execute(sql(
            "SELECT user_id, started_at, ended_at FROM holder_log WHERE onboarding_id = :o "
            "ORDER BY started_at, id"), {"o": ob["id"]}).all()
    assert len(rows) == 5
    assert all(r[2] is not None for r in rows)


def test_overall_rollup_includes_onboarding_source(client):
    ctx = setup_firm(client)
    _, obs = start_onboardings(client, ctx)
    ob = next(o for o in obs if o["service"] == "VAT Filing")
    run_two_round_relay(client, ctx, ob["id"])

    emp = client.get("/performance/employees", headers=ctx["admin"]["headers"]).json()
    assert "onboarding_stars_scale_text" in emp
    staff_row = next(e for e in emp["employees"] if e["user_id"] == ctx["staff"]["id"])
    mgr_row = next(e for e in emp["employees"] if e["user_id"] == ctx["manager"]["id"])
    for row in (staff_row, mgr_row):
        assert row["onboarding_count"] == 1 and row["onboarding_avg_stars"] == 5
        onb_events = [ev for ev in row["recent_events"] if ev["source"] == "onboarding"]
        assert onb_events and onb_events[0]["label"] == "Onboarding — VAT Filing, Gulf Horizon Trading LLC"
        # overall is the mean of ALL star events across sources — onboarding included
        parts = [(row["proposal_avg_stars"], row["proposal_count"]),
                 (row["duties_avg_stars"], row["duty_count"]),
                 (row["onboarding_avg_stars"], row["onboarding_count"]),
                 (row["invoicing_avg_stars"], row["invoicing_count"])]
        total = sum(c for _, c in parts)
        expected = sum((a or 0) * c for a, c in parts) / total
        assert row["event_count"] == total
        assert row["overall_avg"] == pytest.approx(expected)

    # client performance lists the completed onboarding with participants
    cl = client.get("/clients", headers=ctx["manager"]["headers"]).json()[0]
    perf = client.get(f"/clients/{cl['id']}/performance", headers=ctx["manager"]["headers"]).json()
    row = next(o for o in perf["onboardings"] if o["service"] == "VAT Filing")
    assert row["staff_name"] == "Priya Nair" and row["total_ms"] > 0
    assert {p["name"] for p in row["per_participant"]} == {"Priya Nair", "Rashid Al Mansoori"}


def test_backfill_derives_spans_and_stars_from_trail(client):
    ctx = setup_firm(client)
    _, obs = start_onboardings(client, ctx)
    ob = next(o for o in obs if o["service"] == "VAT Filing")
    run_two_round_relay(client, ctx, ob["id"])
    live = client.get(f"/onboardings/{ob['id']}", headers=ctx["manager"]["headers"]).json()
    live_stars = {s["user_id"]: s for s in live["stars"]}

    # wipe the live spans + stars, as if this row predated holder logging
    with engine.begin() as conn:
        n = conn.execute(sql("DELETE FROM holder_log WHERE onboarding_id = :o"), {"o": ob["id"]}).rowcount
        assert n == 5  # the live implementation recorded every span
        conn.execute(sql("UPDATE onboardings SET stars = NULL WHERE id = :o"), {"o": ob["id"]})

    with engine.begin() as conn:
        assert backfill_onboarding_holder_log(conn) >= 1

    with engine.begin() as conn:
        rows = conn.execute(sql(
            "SELECT user_id, started_at, ended_at, reason FROM holder_log WHERE onboarding_id = :o "
            "ORDER BY started_at, id"), {"o": ob["id"]}).all()
        derived = conn.execute(sql("SELECT stars FROM onboardings WHERE id = :o"), {"o": ob["id"]}).scalar()

    # same span structure: staff, manager, staff, manager, staff — all closed
    assert len(rows) == 5
    assert [str(r[0]) for r in rows] == [ctx["staff"]["id"], ctx["manager"]["id"], ctx["staff"]["id"],
                                         ctx["manager"]["id"], ctx["staff"]["id"]]
    assert all(r[2] is not None for r in rows)
    assert all(r[3] == "backfilled from onboarding trail" for r in rows)

    # stars match the live computation (event timestamps ≈ span timestamps)
    derived_stars = {s["user_id"]: s for s in derived}
    assert set(derived_stars) == set(live_stars)
    for uid, s in derived_stars.items():
        assert s["holdings"] == live_stars[uid]["holdings"]
        assert s["stars"] == live_stars[uid]["stars"]
        assert abs(s["total_held_ms"] - live_stars[uid]["total_held_ms"]) < 5000

    # idempotent: a second run touches nothing
    with engine.begin() as conn:
        assert backfill_onboarding_holder_log(conn) == 0
        count = conn.execute(sql("SELECT count(*) FROM holder_log WHERE onboarding_id = :o"),
                             {"o": ob["id"]}).scalar()
    assert count == 5
