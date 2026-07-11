"""Backfill: derive onboarding holder_log spans from the append-only onboarding_events
trail, and store per-participant star ratings on completed onboardings.

Connection-level (raw SQL) so it runs identically from the alembic data migration and
from tests. The derivation mirrors the live relay mechanics: staff holds from creation;
"open request(s) sent to" passes the baton to the manager; "baton auto-returned" passes
it back; the completion event ends the open span."""

import json

from sqlalchemy import text

DAY_MS = 86400000
# the proposal starsFor scale, exactly (routers/proposals.py STARS_SCALE)
STARS_SCALE = [(0.5, 5), (1, 4.5), (2, 4), (3, 3.5), (5, 3), (7, 2), (None, 1)]


def stars_for(avg_days: float) -> float:
    for max_days, stars in STARS_SCALE:
        if max_days is None or avg_days <= max_days:
            return stars
    return 1


def spans_from_events(staff_id, manager_id, created_at, completed_at, events) -> list[tuple]:
    """(user_id, started_at, ended_at) spans; ended_at None means the span is still open."""
    spans = []
    cur_user, cur_start = staff_id, created_at
    for at, txt in events:
        if "open request(s) sent to" in txt:
            spans.append((cur_user, cur_start, at))
            cur_user, cur_start = manager_id, at
        elif "baton auto-returned" in txt:
            spans.append((cur_user, cur_start, at))
            cur_user, cur_start = staff_id, at
        elif "ONBOARDING COMPLETE" in txt or txt.startswith("Onboarding complete"):
            spans.append((cur_user, cur_start, at))
            cur_user = None
    if cur_user is not None:
        spans.append((cur_user, cur_start, completed_at))
    return spans


def stars_from_spans(spans, completed_at) -> list[dict]:
    per: dict = {}
    for user_id, start, end in spans:
        if user_id is None:
            continue
        ended = end or completed_at
        if ended is None:
            continue
        per.setdefault(str(user_id), []).append((ended - start).total_seconds() * 1000)
    out = []
    for uid, durs in per.items():
        avg = sum(durs) / len(durs)
        out.append({"user_id": uid, "stars": stars_for(avg / DAY_MS),
                    "total_held_ms": int(sum(durs)), "holdings": len(durs)})
    out.sort(key=lambda e: (-e["stars"], e["total_held_ms"]))
    return out


def backfill_onboarding_holder_log(conn) -> int:
    """Insert derived spans for every onboarding that has no holder_log rows yet; store
    stars on completed ones. Idempotent — returns how many onboardings were backfilled."""
    obs = conn.execute(text(
        "SELECT o.id, o.tenant_id, o.staff_id, o.status, o.created_at, o.completed_at, "
        "       p.requested_by AS manager_id "
        "FROM onboardings o LEFT JOIN proposals p ON p.id = o.proposal_id "
        "WHERE NOT EXISTS (SELECT 1 FROM holder_log h WHERE h.onboarding_id = o.id)"
    )).mappings().all()
    for ob in obs:
        events = conn.execute(text(
            "SELECT at, text FROM onboarding_events WHERE onboarding_id = :o ORDER BY at, id"
        ), {"o": ob["id"]}).all()
        spans = spans_from_events(ob["staff_id"], ob["manager_id"], ob["created_at"],
                                  ob["completed_at"], [(e[0], e[1]) for e in events])
        for user_id, start, end in spans:
            if user_id is None:
                continue
            conn.execute(text(
                "INSERT INTO holder_log (tenant_id, onboarding_id, user_id, started_at, ended_at, reason) "
                "VALUES (:t, :o, :u, :s, :e, 'backfilled from onboarding trail')"
            ), {"t": ob["tenant_id"], "o": ob["id"], "u": user_id, "s": start, "e": end})
        if ob["status"] == "complete" and ob["completed_at"]:
            stars = stars_from_spans(spans, ob["completed_at"])
            conn.execute(text("UPDATE onboardings SET stars = :s WHERE id = :id"),
                         {"s": json.dumps(stars), "id": ob["id"]})
    return len(obs)
