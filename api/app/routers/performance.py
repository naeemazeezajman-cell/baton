"""Firm-wide performance: duty-completion stars, per-client task history, the employee
performance roll-up, firm-definable targets (perf_config), and the manager-only
"pending across the firm" board. Management only — staff never see ratings."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (Client, Duty, DutyCompletion, HolderLog, Onboarding, Payment,
                      PerformanceConfig, Proposal, User)
from ..perf_config import (DEFAULTS, ConfigTimeline, band_stars, hold_stars, merged,
                           scale_texts, validate)
from ..security import require_roles
from ..tenancy import get_scoped_or_404, tenant_select
from .proposals import CLOSED_STATUSES

router = APIRouter(tags=["performance"])

DAY_MS = 86400000
COMPLETED_STATUSES = ("el_sent", "onboarding_complete")


def duty_stars(late_ms: int, method: str, grace_bands: list[float] | None = None) -> int:
    """Stars for one duty completion vs its due date; declared-without-proof caps at 3.
    The grace bands (days late for ★4/★3/★2) come from the firm's performance config."""
    return band_stars(late_ms, grace_bands or DEFAULTS["duty"]["grace_bands"], method == "declared")


def invoicing_stars(late_ms: int, declared: bool, grace_bands: list[float] | None = None) -> int:
    """Stars for one raised invoice — same band mechanics/cap as duties."""
    return band_stars(late_ms, grace_bands or DEFAULTS["invoicing"]["grace_bands"], declared)


def _invoice_late_ms(pay: Payment, inv_cfg: dict, el_sent_at: datetime | None) -> int:
    """Lateness anchor: the firm's target days from EL send when set (and the payment came
    from a proposal), else the payment's own due date — the original behavior."""
    target = inv_cfg.get("target_days")
    anchor = (el_sent_at + timedelta(days=target)) if (target is not None and el_sent_at is not None) \
        else pay.due_at
    return int((pay.invoice_raised_at - anchor).total_seconds() * 1000)


def timing_text(due_at, completed_at, late_ms: int) -> str:
    if late_ms > 0:
        return f"{max(1, round(late_ms / DAY_MS))}d late"
    early_days = round((due_at - completed_at).total_seconds() * 1000 / DAY_MS)
    return "on time" if early_days <= 0 else f"{early_days}d early"


def _cycle_per_employee(db: Session, p: Proposal, hold_target_days: float) -> list[dict]:
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
        out.append({"user_id": uid_, "stars": hold_stars(avg / DAY_MS, hold_target_days),
                    "total_held_ms": int(sum(durs)), "holdings": len(durs)})
    return out


# ---------- firm-definable targets (versioned; the version rows ARE the change log) ----------

class PerformanceConfigIn(BaseModel):
    config: dict
    note: str = Field(min_length=1)  # why the standard changed — kept on the version row


@router.get("/performance/config")
def get_performance_config(user: User = Depends(require_roles("Admin", "Manager")),
                           db: Session = Depends(get_db)):
    tl = ConfigTimeline(db, user.tenant_id)
    cfg, version = tl.active()
    users_by_id = {u.id: u.name for u in db.scalars(tenant_select(User, user)).all()}
    return {
        "config": cfg, "version": version, "defaults": DEFAULTS,
        "scale_texts": scale_texts(cfg),
        "history": [{"version": r.version, "at": r.created_at, "note": r.note,
                     "by": users_by_id.get(r.created_by), "config": merged(r.config)}
                    for r in reversed(tl.rows)],
    }


@router.put("/performance/config")
def update_performance_config(body: PerformanceConfigIn,
                              user: User = Depends(require_roles("Admin", "Manager")),
                              db: Session = Depends(get_db)):
    """Creates a NEW config version (append-only — the audit trail). Applies to future
    scoring only: items completed earlier keep the version active at their completion."""
    full = merged(body.config)
    errs = validate(full)
    if errs:
        raise HTTPException(status_code=422, detail="; ".join(errs))
    tl = ConfigTimeline(db, user.tenant_id)
    version = (tl.rows[-1].version + 1) if tl.rows else 1
    db.add(PerformanceConfig(tenant_id=user.tenant_id, version=version, config=full,
                             note=body.note.strip(), created_by=user.id))
    db.commit()
    return {"config": full, "version": version, "scale_texts": scale_texts(full)}


# ---------- firm health (COUNTS + ratings — management only) ----------

@router.get("/clients/{client_id}/performance")
def client_performance(client_id: uuid.UUID, user: User = Depends(require_roles("Admin", "Manager")),
                       db: Session = Depends(get_db)):
    """The client's full task record — every duty completion, newest first — plus the
    originating proposal cycle summary once that matter completed at EL sent."""
    client = get_scoped_or_404(db, Client, client_id, user)
    users_by_id = {u.id: u for u in db.scalars(tenant_select(User, user)).all()}
    tl = ConfigTimeline(db, user.tenant_id)
    active_cfg, _ = tl.active()

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
        cfg_c, ver_c = tl.at(c.completed_at)
        tasks.append({
            "service": d.service, "kind": d.kind,
            "staff_id": d.staff_id, "staff_name": staff.name if staff else "—",
            "period": rec.get("period") or rec.get("financial year"),
            "due_at": c.due_at, "completed_at": c.completed_at,
            "timing": timing_text(c.due_at, c.completed_at, c.late_ms),
            "late_ms": c.late_ms, "method": c.method,
            "stars": duty_stars(c.late_ms, c.method, cfg_c["duty"]["grace_bands"]),
            "config_version": ver_c,
        })

    # every engagement for this client — the original conversion proposal plus any
    # additional-engagement proposals linked at creation
    props = {p.id: p for p in db.scalars(tenant_select(Proposal, user).where(
        (Proposal.client_id == client.id) | (Proposal.id == client.from_proposal)
    )).all()}
    cycles = []
    for p in props.values():
        if p.status not in COMPLETED_STATUSES or not p.onboarding_completed_at:
            continue
        cfg_p, ver_p = tl.at(p.onboarding_completed_at)
        per = _cycle_per_employee(db, p, cfg_p["proposal"]["hold_target_days"])
        for e in per:
            u = users_by_id.get(e["user_id"])
            e["name"] = u.name if u else "—"
        per.sort(key=lambda e: (-e["stars"], e["total_held_ms"]))
        cycles.append({
            "ref": p.ref,
            "services": [s.get("name") for s in (p.services or [])],
            "total_ms": int((p.onboarding_completed_at - p.created_at).total_seconds() * 1000),
            "completed_at": p.onboarding_completed_at,
            "per_employee": per,
            "config_version": ver_p,
        })
    cycles.sort(key=lambda c: c["completed_at"])

    # completed onboardings — service, staff, duration, per-participant holding-time stars
    onboardings = []
    completed_obs = db.scalars(tenant_select(Onboarding, user).where(
        Onboarding.client_id == client.id, Onboarding.status == "complete")).all()
    for ob in sorted(completed_obs, key=lambda o: o.completed_at or o.created_at):
        per = []
        for e in ob.stars or []:
            u = users_by_id.get(uuid.UUID(e["user_id"]))
            per.append({**e, "name": u.name if u else "—"})
        staff = users_by_id.get(ob.staff_id)
        onboardings.append({
            "service": ob.service,
            "staff_id": ob.staff_id, "staff_name": staff.name if staff else "—",
            "completed_at": ob.completed_at,
            "total_ms": int(((ob.completed_at - ob.created_at).total_seconds() * 1000)
                            if ob.completed_at else 0),
            "per_participant": per,
        })

    texts = scale_texts(active_cfg)
    return {
        "client": {"id": client.id, "ref": client.ref, "name": client.name},
        "proposal_cycle": cycles[0] if cycles else None,  # original engagement (back-compat)
        "proposal_cycles": cycles,
        "onboardings": onboardings,
        "tasks": tasks,
        "duty_stars_scale_text": texts["duty_stars_scale_text"],
        "proposal_stars_scale_text": texts["proposal_stars_scale_text"],
        "onboarding_stars_scale_text": texts["onboarding_stars_scale_text"],
    }


@router.get("/performance/employees")
def employees_performance(user: User = Depends(require_roles("Admin", "Manager")),
                          db: Session = Depends(get_db)):
    """Per-employee roll-up. overall_avg is the mean of ALL individual star events from
    both sources — not the mean of the two averages. Every star event carries the
    config_version that governed it (the version active when the work completed)."""
    users = db.scalars(tenant_select(User, user).where(User.active.is_(True))).all()
    tl = ConfigTimeline(db, user.tenant_id)
    active_cfg, active_version = tl.active()

    # proposal star events: one per employee per completed matter
    prop_events: dict = {}
    completed = db.scalars(tenant_select(Proposal, user).where(
        Proposal.status.in_(COMPLETED_STATUSES), Proposal.onboarding_completed_at.is_not(None)
    )).all()
    for p in completed:
        cfg_p, ver_p = tl.at(p.onboarding_completed_at)
        for e in _cycle_per_employee(db, p, cfg_p["proposal"]["hold_target_days"]):
            prop_events.setdefault(e["user_id"], []).append({
                "source": "proposal", "label": f"Proposal {p.ref} — {p.prospect.get('name')}",
                "at": p.onboarding_completed_at, "stars": e["stars"], "config_version": ver_p,
            })

    # duty star events: one per completion
    duty_events: dict = {}
    rows = db.execute(
        select(DutyCompletion, Duty).join(Duty, DutyCompletion.duty_id == Duty.id)
        .where(Duty.tenant_id == user.tenant_id)
    ).all()
    for c, d in rows:
        cfg_c, ver_c = tl.at(c.completed_at)
        duty_events.setdefault(d.staff_id, []).append({
            "source": "duty", "label": f"{d.service} — {d.client_name}",
            "at": c.completed_at, "stars": duty_stars(c.late_ms, c.method, cfg_c["duty"]["grace_bands"]),
            "config_version": ver_c,
        })

    clients_by_id = {c.id: c for c in db.scalars(tenant_select(Client, user)).all()}

    # onboarding star events: one per participant per completed onboarding (sealed at
    # completion, with the config version that applied — never recomputed)
    onboarding_events: dict = {}
    for ob in db.scalars(tenant_select(Onboarding, user).where(
            Onboarding.status == "complete", Onboarding.stars.is_not(None))).all():
        cl = clients_by_id.get(ob.client_id)
        for e in ob.stars or []:
            onboarding_events.setdefault(uuid.UUID(e["user_id"]), []).append({
                "source": "onboarding", "label": f"Onboarding — {ob.service}, {cl.name if cl else '—'}",
                "at": ob.completed_at, "stars": e["stars"],
                "config_version": e.get("config_version"),
            })

    # invoicing star events: one per raised invoice, credited to the raiser
    el_sent_by_proposal = dict(db.execute(
        select(Proposal.id, Proposal.onboarding_completed_at)
        .where(Proposal.tenant_id == user.tenant_id)).all())
    invoicing_events: dict = {}
    for pay in db.scalars(tenant_select(Payment, user).where(Payment.invoice_raised_at.is_not(None))).all():
        inv = pay.invoice or {}
        if not inv.get("by"):
            continue
        raiser = uuid.UUID(inv["by"])
        cfg_i, ver_i = tl.at(pay.invoice_raised_at)
        late_ms = _invoice_late_ms(pay, cfg_i["invoicing"],
                                   el_sent_by_proposal.get(pay.proposal_id))
        cl = clients_by_id.get(pay.client_id)
        invoicing_events.setdefault(raiser, []).append({
            "source": "invoicing",
            "label": f"Invoice {inv.get('number', '—')} — {cl.name if cl else pay.label}",
            "at": pay.invoice_raised_at,
            "stars": invoicing_stars(late_ms, bool(inv.get("declared")), cfg_i["invoicing"]["grace_bands"]),
            "config_version": ver_i,
        })

    open_props = db.scalars(tenant_select(Proposal, user).where(Proposal.status.notin_(CLOSED_STATUSES))).all()
    open_duties = db.scalars(tenant_select(Duty, user).where(Duty.closed.is_(False))).all()

    employees = []
    for u in users:
        pe = prop_events.get(u.id, [])
        de = duty_events.get(u.id, [])
        oe = onboarding_events.get(u.id, [])
        ie = invoicing_events.get(u.id, [])
        all_events = sorted([*pe, *de, *oe, *ie], key=lambda e: e["at"], reverse=True)
        mean = lambda xs: (sum(xs) / len(xs)) if xs else None  # noqa: E731
        held = sum(1 for p in open_props if p.holder == u.id)
        open_d = sum(1 for d in open_duties if d.staff_id == u.id)
        employees.append({
            "user_id": u.id, "name": u.name, "designation": u.designation, "role": u.role,
            "proposal_avg_stars": mean([e["stars"] for e in pe]),
            "proposal_count": len(pe),
            "duties_avg_stars": mean([e["stars"] for e in de]),
            "duty_count": len(de),
            "onboarding_avg_stars": mean([e["stars"] for e in oe]),
            "onboarding_count": len(oe),
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
        "targets": active_cfg,
        "config_version": active_version,
        **scale_texts(active_cfg),
    }


# ---------- pending across the firm (managers see everything currently waiting) ----------

@router.get("/performance/pending")
def pending_board(user: User = Depends(require_roles("Admin", "Manager")),
                  db: Session = Depends(get_db)):
    """Everything currently waiting on someone, firm-wide, grouped by person: held
    proposals, in-progress onboardings, open duties, and the in-house accountant's
    unraised invoices / unrecorded receipts. `over_target` flags items held beyond the
    firm's configured targets; `overdue` flags hard deadlines already passed."""
    nw = datetime.now(timezone.utc)
    tl = ConfigTimeline(db, user.tenant_id)
    cfg, version = tl.active()
    users = db.scalars(tenant_select(User, user).where(User.active.is_(True))).all()
    clients_by_id = {c.id: c for c in db.scalars(tenant_select(Client, user)).all()}

    def _aware(dt):
        return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def age_ms(since):
        return int((nw - _aware(since)).total_seconds() * 1000) if since else None

    items_by_user: dict = {}

    def add(holder_id, item):
        items_by_user.setdefault(holder_id, []).append(item)

    # held proposals — hold time from the open holder-log span
    open_props = db.scalars(tenant_select(Proposal, user).where(
        Proposal.status.notin_(CLOSED_STATUSES))).all()
    open_spans = {}
    if open_props:
        for h in db.scalars(select(HolderLog).where(
                HolderLog.proposal_id.in_([p.id for p in open_props]),
                HolderLog.ended_at.is_(None), HolderLog.user_id.is_not(None))).all():
            open_spans[h.proposal_id] = h.started_at
    hold_target = cfg["proposal"]["hold_target_days"]
    cycle_target = cfg["proposal"]["cycle_target_days"]
    for p in open_props:
        if p.holder is None:  # client-held — nobody in the firm is sitting on it
            continue
        since = open_spans.get(p.id) or p.created_at
        a = age_ms(since)
        cycle_ms = age_ms(p.created_at)
        add(p.holder, {
            "type": "proposal", "ref": p.ref, "label": p.prospect.get("name") or p.ref,
            "sublabel": p.status.replace("_", " "),
            "pending_since": since, "age_ms": a, "due_at": None,
            "overdue": bool(cycle_target and cycle_ms > cycle_target * DAY_MS),
            "over_target": a > hold_target * DAY_MS,
        })

    # in-progress onboardings — holder + age
    ob_hold_target = cfg["onboarding"]["hold_target_days"]
    ob_cycle_target = cfg["onboarding"]["cycle_target_days"]
    for ob in db.scalars(tenant_select(Onboarding, user).where(
            Onboarding.status == "in_progress")).all():
        if ob.holder is None:
            continue
        cl = clients_by_id.get(ob.client_id)
        since = ob.holder_since or ob.created_at
        a = age_ms(since)
        cycle_ms = age_ms(ob.created_at)
        add(ob.holder, {
            "type": "onboarding", "ref": str(ob.id), "label": f"{cl.name if cl else '—'}",
            "sublabel": f"onboarding — {ob.service}",
            "pending_since": since, "age_ms": a, "due_at": None,
            "overdue": bool(ob_cycle_target and cycle_ms > ob_cycle_target * DAY_MS),
            "over_target": a > ob_hold_target * DAY_MS,
        })

    # open duties — next-due countdown; overdue once the statutory deadline passes
    for d in db.scalars(tenant_select(Duty, user).where(Duty.closed.is_(False))).all():
        due_in = int((_aware(d.next_due) - nw).total_seconds() * 1000)
        add(d.staff_id, {
            "type": "duty", "ref": str(d.id), "label": d.client_name,
            "sublabel": f"{d.service} · {d.cadence}",
            "pending_since": None, "age_ms": max(0, -due_in), "due_at": d.next_due,
            "overdue": due_in < 0, "over_target": False,
        })

    # accountant work — unraised invoices and invoices awaiting receipts
    accountants = [u for u in users if u.role == "Accountant"]
    acct_key = accountants[0].id if len(accountants) == 1 else None
    inv_cfg = cfg["invoicing"]
    el_sent_by_proposal = dict(db.execute(
        select(Proposal.id, Proposal.onboarding_completed_at)
        .where(Proposal.tenant_id == user.tenant_id)).all())
    for pay in db.scalars(tenant_select(Payment, user)).all():
        cl = clients_by_id.get(pay.client_id)
        cname = cl.name if cl else pay.label
        if not pay.invoice_raised:
            el_at = _aware(el_sent_by_proposal.get(pay.proposal_id))
            target_at = (el_at + timedelta(days=inv_cfg["target_days"])
                         if inv_cfg["target_days"] is not None and el_at else None)
            since = el_at
            add(acct_key, {
                "type": "invoice", "ref": str(pay.id), "label": cname,
                "sublabel": f"invoice not raised — {pay.label} · AED {float(pay.amount):,.0f}",
                "pending_since": since, "age_ms": age_ms(since) or 0, "due_at": pay.due_at,
                "overdue": _aware(pay.due_at) < nw,
                "over_target": bool(target_at and nw > target_at),
            })
        else:
            received = sum(float(r.get("amount") or 0) for r in (pay.receipts or []))
            outstanding = float(pay.amount) - received
            if outstanding <= 0:
                continue
            since = pay.invoice_raised_at
            add(acct_key, {
                "type": "receipt", "ref": str(pay.id), "label": cname,
                "sublabel": f"receipt not recorded — {pay.label} · AED {outstanding:,.0f} outstanding",
                "pending_since": since, "age_ms": age_ms(since) or 0, "due_at": pay.due_at,
                "overdue": _aware(pay.due_at) < nw,
                "over_target": False,
            })

    users_by_id = {u.id: u for u in users}
    people = []
    for holder_id, items in items_by_user.items():
        items.sort(key=lambda i: -(i["age_ms"] or 0))
        u = users_by_id.get(holder_id)
        if holder_id is not None and u is None:  # inactive/unknown holder — still show it
            u = db.get(User, holder_id)
        people.append({
            "user_id": holder_id,
            "name": u.name if u else "In-house accounts",
            "role": u.role if u else "Accountant",
            "designation": u.designation if u else None,
            "counts": {"total": len(items),
                       "overdue": sum(1 for i in items if i["overdue"] or i["over_target"])},
            "items": items,
        })
    people.sort(key=lambda p: (-p["counts"]["overdue"], -p["counts"]["total"], p["name"]))

    return {"as_of": nw, "targets": cfg, "config_version": version, "people": people}
