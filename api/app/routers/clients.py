"""Read endpoints backing the production frontend: clients, signature vault, admin export."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Client, Duty, Payment, Proposal, SignatureUse, User
from ..security import current_user, require_roles
from ..tenancy import tenant_select

router = APIRouter(tags=["clients"])


@router.get("/clients")
def list_clients(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(tenant_select(Client, user).order_by(Client.created_at)).all()
    return [
        {"id": c.id, "ref": c.ref, "name": c.name, "contact": c.contact,
         "from_proposal": c.from_proposal, "confirmation_basis": c.confirmation_basis,
         "created_at": c.created_at}
        for c in rows
    ]


@router.get("/signature-uses")
def signature_uses(user: User = Depends(require_roles("Admin")), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(SignatureUse).where(SignatureUse.tenant_id == user.tenant_id)
        .order_by(SignatureUse.at.desc())
    ).all()
    return [{"id": s.id, "by": s.user_id, "document": s.document, "context": s.context, "at": s.at} for s in rows]


@router.get("/admin/export")
def export_tenant(user: User = Depends(require_roles("Admin")), db: Session = Depends(get_db)):
    """Full tenant backup (JSON). Password hashes are never exported."""
    users = db.scalars(tenant_select(User, user)).all()
    proposals = db.scalars(tenant_select(Proposal, user)).all()
    clients = db.scalars(tenant_select(Client, user)).all()
    duties = db.scalars(tenant_select(Duty, user)).all()
    payments = db.scalars(tenant_select(Payment, user)).all()
    return {
        "exported_by": str(user.id),
        "users": [{"id": u.id, "name": u.name, "designation": u.designation, "email": u.email,
                   "role": u.role, "signatory": u.signatory, "active": u.active} for u in users],
        "proposals": [{"id": p.id, "ref": p.ref, "status": p.status, "prospect": p.prospect,
                       "services": p.services, "checklist": p.checklist, "versions": p.versions,
                       "el": p.el, "signatures": p.signatures, "created_at": p.created_at} for p in proposals],
        "clients": [{"id": c.id, "ref": c.ref, "name": c.name, "contact": c.contact,
                     "created_at": c.created_at} for c in clients],
        "duties": [{"id": d.id, "staff_id": d.staff_id, "client_name": d.client_name, "service": d.service,
                    "kind": d.kind, "cadence": d.cadence, "next_due": d.next_due, "closed": d.closed} for d in duties],
        "payments": [{"id": x.id, "client_id": x.client_id, "label": x.label, "amount": float(x.amount),
                      "due_at": x.due_at, "invoice_raised": x.invoice_raised, "receipts": x.receipts} for x in payments],
    }
