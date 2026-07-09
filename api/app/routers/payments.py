"""Payments (Tier 1) — expected payments are generated at EL send (proposals.el_send).
The accountant marks invoices raised and records receipts (partial allowed, evidence upload).
Health per client: Good / Watch (worst overdue ≤ 30d) / At risk (> 30d) — prototype healthOf.
"""

import uuid

from fastapi import APIRouter, Depends, File as FileParam, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Client, Payment, User
from ..security import current_user, require_roles
from ..tenancy import get_scoped_or_404, tenant_select
from ..workflow import conflict, iso, now
from .files import store_upload

router = APIRouter(prefix="/payments", tags=["payments"])


def received_of(p: Payment) -> float:
    return sum(float(r.get("amount", 0)) for r in p.receipts)


def is_done(p: Payment) -> bool:
    return received_of(p) >= float(p.amount) - 0.5


def _serialize(p: Payment) -> dict:
    received = received_of(p)
    due = p.due_at
    overdue_days = max(0.0, (now() - due).total_seconds() / 86400) if not is_done(p) else 0.0
    return {
        "id": p.id, "client_id": p.client_id, "proposal_id": p.proposal_id, "label": p.label,
        "amount": float(p.amount), "due_at": p.due_at, "invoice_raised": p.invoice_raised,
        "received": received, "done": is_done(p), "overdue_days": round(overdue_days, 1),
        "receipts": p.receipts, "events": p.events,
    }


@router.get("")
def list_payments(user: User = Depends(require_roles("Admin", "Accountant")), db: Session = Depends(get_db)):
    rows = db.scalars(tenant_select(Payment, user).order_by(Payment.due_at)).all()
    return [_serialize(p) for p in rows]


@router.post("/{payment_id}/invoice-raised")
def invoice_raised(payment_id: uuid.UUID, user: User = Depends(require_roles("Admin", "Accountant")),
                   db: Session = Depends(get_db)):
    p = get_scoped_or_404(db, Payment, payment_id, user)
    if p.invoice_raised:
        raise conflict("Invoice already marked as raised")
    p.invoice_raised = True
    p.events = [*p.events, {"at": iso(now()), "by": str(user.id),
                            "text": "Invoice raised in external accounting software"}]
    db.commit()
    return _serialize(p)


@router.post("/{payment_id}/record-receipt")
def record_receipt(
    payment_id: uuid.UUID,
    amount: float = Form(gt=0),
    evidence: UploadFile | None = FileParam(default=None),
    user: User = Depends(require_roles("Admin", "Accountant")),
    db: Session = Depends(get_db),
):
    p = get_scoped_or_404(db, Payment, payment_id, user)
    if is_done(p):
        raise conflict("Payment is already fully received")
    receipt = {"amount": amount, "at": iso(now()), "by": str(user.id)}
    evt_suffix = ""
    if evidence is not None:
        f = store_upload(db, user, "payment", p.id, evidence)
        receipt.update(file_id=str(f.id), file_name=f.name)
        evt_suffix = f" — evidence: {f.name}"
    p.receipts = [*p.receipts, receipt]
    events = [*p.events, {"at": iso(now()), "by": str(user.id),
                          "text": f"Receipt recorded: AED {round(amount):,}{evt_suffix}"}]
    if is_done(p):
        events.append({"at": iso(now()), "by": "system", "text": "Fully received — reminders stopped"})
    p.events = events
    db.commit()
    return _serialize(p)


@router.get("/health/{client_id}")
def client_health(client_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Good / Watch (≤30d overdue) / At risk (>30d) per the prototype's healthOf."""
    client = get_scoped_or_404(db, Client, client_id, user)
    rows = db.scalars(tenant_select(Payment, user).where(Payment.client_id == client.id)).all()
    t = now()
    overdue = [p for p in rows if not is_done(p) and p.due_at < t and received_of(p) < float(p.amount)]
    outstanding = sum(float(p.amount) - received_of(p) for p in overdue)
    worst = max((( t - p.due_at).total_seconds() / 86400 for p in overdue), default=0.0)
    badge = "Good" if not overdue else ("Watch" if worst <= 30 else "At risk")
    return {"client_id": client.id, "ref": client.ref, "badge": badge,
            "outstanding": round(outstanding, 2), "overdue_count": len(overdue),
            "worst_overdue_days": round(worst, 1)}
