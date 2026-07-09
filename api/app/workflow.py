"""Onboarding workflow helpers — src/baton-prototype.jsx is the behavioural spec.

Status lifecycle (STATUS_MAP in the prototype):
assigned → docs_with_manager ⇄ waiver_review → drafting → manager_review → senior_review
→ signed → proposal_sent → (upload-signed = conversion) → el_staffing → el_senior_review
→ el_approved → el_sent | lost
"""

import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import HolderLog, Proposal, ProposalEvent, User

TERMINAL = ("el_sent", "lost")

STATUSES = (
    "assigned", "docs_with_manager", "waiver_review", "drafting", "manager_review",
    "senior_review", "signed", "proposal_sent", "el_staffing", "el_senior_review",
    "el_approved", "el_sent", "onboarding_complete", "lost",
)

BASIS = ("per month", "per quarter", "per annum", "one-time")


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def default_basis(service: str) -> str:
    if re.search(r"monthly", service, re.I):
        return "per month"
    if re.search(r"quarterly", service, re.I):
        return "per quarter"
    if re.search(r"annual|filing|audit|esr", service, re.I):
        return "per annum"
    return "one-time"


def num(x) -> float:
    try:
        return float(re.sub(r"[^\d.]", "", str(x)) or 0)
    except ValueError:
        return 0.0


def conflict(reason: str):
    """Invalid transition → 409 with reason (STRUCTURE.md pattern 1)."""
    return HTTPException(status_code=409, detail={"reason": reason})


def require_status(p: Proposal, *statuses: str):
    if p.status not in statuses:
        raise conflict(f"Action requires status {' or '.join(statuses)}; current status is {p.status}")


def require_holder(p: Proposal, user: User):
    if p.holder != user.id:
        raise conflict("You are not the current holder of this matter")


def log_event(db: Session, p: Proposal, by_user: uuid.UUID | None, text: str, kind: str = "log", meta: dict | None = None):
    db.add(ProposalEvent(tenant_id=p.tenant_id, proposal_id=p.id, by_user=by_user, kind=kind, text_=text, meta=meta))


def pass_holder(db: Session, p: Proposal, to_user: User | None, by_user: User, reason: str):
    """Close the open holder_log row, open the next, write the event — one transaction
    (the caller's session commit covers all three; STRUCTURE.md pattern 2)."""
    open_row = db.query(HolderLog).filter(
        HolderLog.proposal_id == p.id, HolderLog.ended_at.is_(None)
    ).first()
    if open_row:
        open_row.ended_at = now()
    if to_user is not None:
        db.add(HolderLog(
            tenant_id=p.tenant_id, proposal_id=p.id, user_id=to_user.id,
            started_at=now(), reason=reason or "responsibility held",
        ))
    p.holder = to_user.id if to_user else None
    if to_user is not None and reason:
        log_event(db, p, by_user.id, f"Responsibility passed to {to_user.name} — {reason}")


def canon(d: dict) -> str:
    """Canonical form of a draft — the dirty-version guard compares this (prototype line 1457)."""
    return json.dumps({
        "l": [[x.get("service"), str(x.get("fee", "")).strip(), x.get("basis") or default_basis(x.get("service", ""))]
              for x in d.get("lines", [])],
        "t": (d.get("payment_terms") or "").strip(),
        "v": str(d.get("validity_days", "")),
        "s": (d.get("scope") or "").strip(),
    })


def diff_drafts(prev: dict, nxt: dict) -> list[str]:
    """Human-readable field-level diff between two generated versions (prototype diffDrafts)."""
    out: list[str] = []
    pl = {l["service"]: l for l in prev.get("lines", [])}
    nl = {l["service"]: l for l in nxt.get("lines", [])}
    for l in nxt.get("lines", []):
        o = pl.get(l["service"])
        if not o:
            out.append(f"service added: {l['service']} at AED {num(l.get('fee')):,.0f} {l.get('basis') or ''}".rstrip())
            continue
        if num(o.get("fee")) != num(l.get("fee")):
            out.append(f"{l['service']}: fee AED {num(o.get('fee')):,.0f} → AED {num(l.get('fee')):,.0f}")
        ob = o.get("basis") or default_basis(l["service"])
        nb = l.get("basis") or default_basis(l["service"])
        if ob != nb:
            out.append(f"{l['service']}: billing basis \"{ob}\" → \"{nb}\"")
    for l in prev.get("lines", []):
        if l["service"] not in nl:
            out.append(f"service removed: {l['service']}")
    if (prev.get("payment_terms") or "").strip() != (nxt.get("payment_terms") or "").strip():
        out.append(f"payment terms: \"{prev.get('payment_terms')}\" → \"{nxt.get('payment_terms')}\"")
    if str(prev.get("validity_days")) != str(nxt.get("validity_days")):
        out.append(f"validity: {prev.get('validity_days')} → {nxt.get('validity_days')} days")
    if (prev.get("scope") or "").strip() != (nxt.get("scope") or "").strip():
        out.append(f"scope notes: \"{prev.get('scope') or '—'}\" → \"{nxt.get('scope') or '—'}\"")
    return out
