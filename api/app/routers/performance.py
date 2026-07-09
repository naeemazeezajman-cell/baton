"""Firm-wide performance: duty-completion stars, per-client task history, and the
employee performance roll-up. Management only — staff never see ratings."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Client, Duty, DutyCompletion, HolderLog, Payment, Proposal, User
from ..security import require_roles
from ..tenancy import get_scoped_or_404, tenant_select
from .proposals import CLOSED_STATUSES, STARS_SCALE_TEXT as PROPOSAL_STARS_SCALE_TEXT, stars_for

router = APIRouter(tags=["performance"])

DAY_MS = 86400000
COMPLETED_STATUSES = ("el_sent", "onboarding_complete")

DUTY_STARS_SCALE_TEXT = ("completed on/before due ★5 · ≤1d late ★4 · ≤3d late ★3 · ≤7d late ★2 · beyond ★1 · "
                         "declared without proof capped at ★3")
INVOICING_STARS_SCALE_TEXT = ("invoice raised on/before due ★5 · ≤1d late ★4 · ≤3d late ★3 · ≤7d late ★2 · "
                              "beyond ★1 · declared raised outside Baton capped at ★3")


def invoicing_stars(late_ms: int, declared: bool) -> int:
    """Stars for one raised invoice vs the payment's due date — same scale/cap as duties."""
    return duty_stars(late_ms, "declared" if declared else "proof")


def duty_stars(late_ms: int, method: str) -> int:
    """Stars for one duty completion vs its due date; declared-without-proof caps at 3."""
    days_late = late_ms / DAY_MS
    if late_ms <= 0:
        s = 5
    elif days_late <= 1:
        s = 4
    elif days_late <= 3:
        s = 3
    elif days_late <= 7:
        s = 2
    else:
        s = 1
    return min(s, 3) if method == "declared" else s


def timing_text(due_at, completed_at, late_ms: int) -> str:
    if late_ms > 0:
        return f"{max(1, round(late_ms / DAY_MS))}d late"
    early_days = round((due_at - completed_at).total_seconds() * 1000 / DAY_MS)
    return "on time" if early_days <= 0 else f"{early_days}d early"


def _cycle_per_employee(db: Session, p: Proposal) -> list[dict]:
    """Per-employee stars for one completed proposal cycle (client-held rows excluded)."""
    rows = db.scalars(select(HolderLog).where(HolderLog.proposal_id == p.id)).all()
    per: dict = {}
    for h in rows:
        if h.user_id is None:
            continue
        ended = h.ended_at or p.onboarding_completed_at
        per.setdefault(h.user_id, []).append((ended - h.started_at).total_seconds() * 1000)
    out = []
    for uid_, durs in per.items():
        avg = sum(durs) / len(durs)
        out.append({"user_id": uid_, "stars": stars_for(avg / DAY_MS),
                    "total_held_ms": int(sum(durs)), "holdings": len(durs)})
    return out


@router.get("/clients/{client_id}/performance")
def client_performance(client_id: uuid.UUID, user: User = Depends(require_roles("Admin", "Manager")),
                       db: Session = Depends(get_db)):
    """The client's full task record — every duty completion, newest first — plus the
    originating proposal cycle summary once that matter completed at EL sent."""
    client = get_scoped_or_404(db, Client, client_id, user)
    users_by_id = {u.id: u for u in db.scalars(tenant_select(User, user)).all()}

    duties = db.scalars(tenant_select(Duty, user).where(
        (Duty.client_id == client.id) | (Duty.client_name == client.name)
    )).all()
    duty_by_id = {d.id: d for d in duties}
    comps = []
    if duty_by_id:
        comps = db.scalars(
            select(DutyCompletion).where(DutyCompletion.duty_id.in_(list(duty_by_id)))
            .order_by(DutyCompletion.completed_at.desc())
        ).all()
    tasks = []
    for c in comps:
        d = duty_by_id[c.duty_id]
        staff = users_by_id.get(d.staff_id)
        rec = c.record or {}
        tasks.append({
            "service": d.service, "kind": d.kind,
            "staff_id": d.staff_id, "staff_name": staff.name if staff else "—",
            "period": rec.get("period") or rec.get("financial year"),
            "due_at": c.due_at, "completed_at": c.completed_at,
            "timing": timing_text(c.due_at, c.completed_at, c.late_ms),
            "late_ms": c.late_ms, "method": c.method,
            "stars": duty_stars(c.late_ms, c.method),
        })

    cycle = None
    if client.from_proposal:
        p = db.get(Proposal, client.from_proposal)
        if p and p.status in COMPLETED_STATUSES and p.onboarding_completed_at:
            per = _cycle_per_employee(db, p)
            for e in per:
                u = users_by_id.get(e["user_id"])
                e["name"] = u.name if u else "—"
            per.sort(key=lambda e: (-e["stars"], e["total_held_ms"]))
            cycle = {
                "ref": p.ref,
                "total_ms": int((p.onboarding_completed_at - p.created_at).total_seconds() * 1000),
                "completed_at": p.onboarding_completed_at,
                "per_employee": per,
            }

    return {
        "client": {"id": client.id, "ref": client.ref, "name": client.name},
        "proposal_cycle": cycle,
        "tasks": tasks,
        "duty_stars_scale_text": DUTY_STARS_SCALE_TEXT,
        "proposal_stars_scale_text": PROPOSAL_STARS_SCALE_TEXT,
    }


@router.get("/performance/employees")
def employees_performance(user: User = Depends(require_roles("Admin", "Manager")),
                          db: Session = Depends(get_db)):
    """Per-employee roll-up. overall_avg is the mean of ALL individual star events from
    both sources — not the mean of the two averages."""
    users = db.scalars(tenant_select(User, user).where(User.active.is_(True))).all()

    # proposal star events: one per employee per completed matter
    prop_events: dict = {}
    completed = db.scalars(tenant_select(Proposal, user).where(
        Proposal.status.in_(COMPLETED_STATUSES), Proposal.onboarding_completed_at.is_not(None)
    )).all()
    for p in completed:
        for e in _cycle_per_employee(db, p):
            prop_events.setdefault(e["user_id"], []).append({
                "source": "proposal", "label": f"Proposal {p.ref} — {p.prospect.get('name')}",
                "at": p.onboarding_completed_at, "stars": e["stars"],
            })

    # duty star events: one per completion
    duty_events: dict = {}
    rows = db.execute(
        select(DutyCompletion, Duty).join(Duty, DutyCompletion.duty_id == Duty.id)
        .where(Duty.tenant_id == user.tenant_id)
    ).all()
    for c, d in rows:
        duty_events.setdefault(d.staff_id, []).append({
            "source": "duty", "label": f"{d.service} — {d.client_name}",
            "at": c.completed_at, "stars": duty_stars(c.late_ms, c.method),
        })

    # invoicing star events: one per raised invoice, credited to the raiser
    invoicing_events: dict = {}
    clients_by_id = {c.id: c for c in db.scalars(tenant_select(Client, user)).all()}
    for pay in db.scalars(tenant_select(Payment, user).where(Payment.invoice_raised_at.is_not(None))).all():
        inv = pay.invoice or {}
        if not inv.get("by"):
            continue
        raiser = uuid.UUID(inv["by"])
        late_ms = int((pay.invoice_raised_at - pay.due_at).total_seconds() * 1000)
        cl = clients_by_id.get(pay.client_id)
        invoicing_events.setdefault(raiser, []).append({
            "source": "invoicing",
            "label": f"Invoice {inv.get('number', '—')} — {cl.name if cl else pay.label}",
            "at": pay.invoice_raised_at,
            "stars": invoicing_stars(late_ms, bool(inv.get("declared"))),
        })

    open_props = db.scalars(tenant_select(Proposal, user).where(Proposal.status.notin_(CLOSED_STATUSES))).all()
    open_duties = db.scalars(tenant_select(Duty, user).where(Duty.closed.is_(False))).all()

    employees = []
    for u in users:
        pe = prop_events.get(u.id, [])
        de = duty_events.get(u.id, [])
        ie = invoicing_events.get(u.id, [])
        all_events = sorted([*pe, *de, *ie], key=lambda e: e["at"], reverse=True)
        mean = lambda xs: (sum(xs) / len(xs)) if xs else None  # noqa: E731
        held = sum(1 for p in open_props if p.holder == u.id)
        open_d = sum(1 for d in open_duties if d.staff_id == u.id)
        employees.append({
            "user_id": u.id, "name": u.name, "designation": u.designation, "role": u.role,
            "proposal_avg_stars": mean([e["stars"] for e in pe]),
            "proposal_count": len(pe),
            "duties_avg_stars": mean([e["stars"] for e in de]),
            "duty_count": len(de),
            "invoicing_avg_stars": mean([e["stars"] for e in ie]),
            "invoicing_count": len(ie),
            "overall_avg": mean([e["stars"] for e in all_events]),
            "event_count": len(all_events),
            "open_workload": {"held_proposals": held, "open_duties": open_d, "total": held + open_d},
            "recent_events": all_events[:20],
        })
    employees.sort(key=lambda e: (e["overall_avg"] is None, -(e["overall_avg"] or 0), e["name"]))

    return {
        "employees": employees,
        "proposal_stars_scale_text": PROPOSAL_STARS_SCALE_TEXT,
        "duty_stars_scale_text": DUTY_STARS_SCALE_TEXT,
        "invoicing_stars_scale_text": INVOICING_STARS_SCALE_TEXT,
    }
