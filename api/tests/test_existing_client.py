"""Existing-client engagements — additional proposals for an already-converted client:
no second client row, client-scoped duplicate guard, performance aggregation across cycles."""

from .test_onboarding import SERVICES, act, create_proposal, drive_to_el_approved, setup_firm


def _el_send(client, ctx, pid):
    act(client, ctx, "manager", pid, "el-send", {"to": "accounts@gulfhorizon.ae", "subject": "EL", "body": "x"})


def first_engagement(client, ctx):
    """New-prospect proposal driven to el_sent → client CL-001 exists."""
    pid = create_proposal(client, ctx)["id"]
    drive_to_el_approved(client, ctx, pid, advance_pct=0)
    _el_send(client, ctx, pid)
    cl = client.get("/clients", headers=ctx["manager"]["headers"]).json()[0]
    return pid, cl


def create_for_client(client, ctx, client_id, expect=201):
    r = client.post("/proposals", json={
        "prospect": {"name": "This Name Is Ignored", "email": "accounts@gulfhorizon.ae"},
        "services": SERVICES,
        "assigned_to": ctx["staff"]["id"],
        "client_id": client_id,
    }, headers=ctx["manager"]["headers"])
    assert r.status_code == expect, r.text
    return r.json()


def test_existing_client_end_to_end_no_second_client_row(client):
    ctx = setup_firm(client)
    pid1, cl = first_engagement(client, ctx)
    ob_count_before = len(client.get("/onboardings", headers=ctx["staff"]["headers"]).json())

    p2 = create_for_client(client, ctx, cl["id"])
    pid2 = p2["id"]
    assert p2["ref"] == "P-002"
    assert p2["client_id"] == cl["id"]
    assert p2["client_ref"] == cl["ref"]
    assert p2["prospect"]["name"] == cl["name"]  # locked to the client record
    detail = client.get(f"/proposals/{pid2}", headers=ctx["manager"]["headers"]).json()
    assert any("Additional engagement proposal created for existing client CL-001" in e["text"]
               for e in detail["events"])

    # identical flow: drafting → signatures → send → signed upload → EL → send
    drive_to_el_approved(client, ctx, pid2, advance_pct=0)
    detail = client.get(f"/proposals/{pid2}", headers=ctx["manager"]["headers"]).json()
    assert detail["client_id"] == cl["id"]
    assert any("Additional engagement confirmed for CL-001" in e["text"] for e in detail["events"])
    assert not any("prospect converted to CLIENT" in e["text"] for e in detail["events"])
    _el_send(client, ctx, pid2)

    # still exactly one client row
    clients = client.get("/clients", headers=ctx["manager"]["headers"]).json()
    assert len(clients) == 1 and clients[0]["ref"] == "CL-001"

    # EL send created onboardings for the new engagement and notified the accountant
    obs = client.get("/onboardings", headers=ctx["staff"]["headers"]).json()
    assert len(obs) == ob_count_before + len(SERVICES)
    assert all(o["client_ref"] == "CL-001" for o in obs)
    acct_notices = client.get("/notices", headers=ctx["accountant"]["headers"]).json()
    assert sum(1 for n in acct_notices if "Engagement live: CL-001" in n["text"]) == 2


def test_existing_client_duplicate_guard(client):
    ctx = setup_firm(client)
    _, cl = first_engagement(client, ctx)

    # the client's existence (and its settled el_sent proposal) is not a duplicate
    p2 = create_for_client(client, ctx, cl["id"])

    # but an OPEN proposal for that client blocks a new one
    r = create_for_client(client, ctx, cl["id"], expect=409)
    assert "An open proposal already exists for client CL-001" in r["detail"]["reason"]
    assert p2["ref"] in r["detail"]["reason"]

    # once the open one settles at el_sent, a further engagement is allowed again
    drive_to_el_approved(client, ctx, p2["id"], advance_pct=0)
    _el_send(client, ctx, p2["id"])
    p3 = create_for_client(client, ctx, cl["id"])
    assert p3["ref"] == "P-003"


def test_client_performance_aggregates_two_engagements(client):
    ctx = setup_firm(client)
    pid1, cl = first_engagement(client, ctx)
    p2 = create_for_client(client, ctx, cl["id"])
    drive_to_el_approved(client, ctx, p2["id"], advance_pct=0)
    _el_send(client, ctx, p2["id"])

    r = client.get(f"/clients/{cl['id']}/performance", headers=ctx["manager"]["headers"])
    assert r.status_code == 200, r.text
    perf = r.json()
    cycles = perf["proposal_cycles"]
    assert [c["ref"] for c in cycles] == ["P-001", "P-002"]
    for c in cycles:
        assert c["total_ms"] >= 0 and len(c["per_employee"]) > 0
    # back-compat single-cycle key still points at the original engagement
    assert perf["proposal_cycle"]["ref"] == "P-001"

    # the firm-wide roll-up counts one proposal star event per employee per completed matter
    emp = client.get("/performance/employees", headers=ctx["manager"]["headers"]).json()["employees"]
    staff_row = next(e for e in emp if e["user_id"] == ctx["staff"]["id"])
    assert staff_row["proposal_count"] == 2
