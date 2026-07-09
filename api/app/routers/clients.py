"""Read endpoints backing the production frontend: clients, signature vault, admin export."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Client, Duty, File, Onboarding, OnboardingItem, Payment, Proposal, SignatureUse, User
from ..security import current_user, require_roles
from ..tenancy import get_scoped_or_404, tenant_select

router = APIRouter(tags=["clients"])


def _unaudited_client_ids(db: Session, tenant_id) -> set:
    rows = db.execute(
        select(Onboarding.client_id).join(OnboardingItem, OnboardingItem.onboarding_id == Onboarding.id)
        .where(Onboarding.tenant_id == tenant_id, OnboardingItem.qualifier == "unaudited")
    ).all()
    return {r[0] for r in rows}


@router.get("/clients")
def list_clients(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(tenant_select(Client, user).order_by(Client.created_at)).all()
    unaudited = _unaudited_client_ids(db, user.tenant_id)
    return [
        {"id": c.id, "ref": c.ref, "name": c.name, "contact": c.contact,
         "from_proposal": c.from_proposal, "confirmation_basis": c.confirmation_basis,
         "unaudited_on_file": c.id in unaudited,
         "created_at": c.created_at}
        for c in rows
    ]


from pydantic import BaseModel, EmailStr  # noqa: E402


class ContactPatchIn(BaseModel):
    name: str | None = None
    contactPerson: str | None = None
    email: EmailStr | None = None
    phone: str | None = None


@router.patch("/clients/{client_id}/contact")
def patch_contact(client_id: uuid.UUID, body: ContactPatchIn,
                  user: User = Depends(require_roles("Admin", "Accountant")),
                  db: Session = Depends(get_db)):
    """Accountants keep contact details current — required before invoices can be emailed."""
    client = get_scoped_or_404(db, Client, client_id, user)
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if body.email is not None:
        patch["email"] = str(body.email)
    client.contact = {**(client.contact or {}), **patch}
    db.commit()
    return {"id": client.id, "ref": client.ref, "contact": client.contact}


@router.get("/clients/{client_id}/documents")
def client_documents(client_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Everything on file for a client — proposal-stage uploads plus every onboarding item
    document — each with source, uploader, date, and qualifier."""
    client = get_scoped_or_404(db, Client, client_id, user)
    users_by_id = {u.id: u for u in db.scalars(tenant_select(User, user)).all()}
    docs = []

    proposal_ids = {p.id: p.ref for p in db.scalars(
        tenant_select(Proposal, user).where((Proposal.client_id == client.id) | (Proposal.id == client.from_proposal))
    ).all()}
    if proposal_ids:
        for f in db.scalars(select(File).where(File.tenant_id == user.tenant_id, File.entity == "proposal",
                                               File.entity_id.in_(list(proposal_ids))).order_by(File.at)).all():
            uploader = users_by_id.get(f.uploaded_by)
            docs.append({"file_id": f.id, "name": f.name, "size": f.size,
                         "source": f"Proposal & Engagement ({proposal_ids[f.entity_id]})",
                         "uploaded_by": uploader.name if uploader else "—",
                         "at": f.at, "qualifier": None})

    obs = db.scalars(tenant_select(Onboarding, user).where(Onboarding.client_id == client.id)).all()
    for ob in obs:
        items = db.scalars(select(OnboardingItem).where(OnboardingItem.onboarding_id == ob.id)).all()
        for it in items:
            for fref in it.files:
                docs.append({"file_id": fref["file_id"], "name": fref["name"], "size": fref.get("size"),
                             "source": f"Onboarding — {ob.service}", "uploaded_by": "—",
                             "at": it.resolved_at, "qualifier": it.qualifier})
    # resolve onboarding uploader names via the files table
    file_ids = [d["file_id"] for d in docs if d["uploaded_by"] == "—"]
    if file_ids:
        frows = {str(f.id): f for f in db.scalars(select(File).where(File.id.in_(file_ids))).all()}
        for d in docs:
            f = frows.get(str(d["file_id"]))
            if f:
                uploader = users_by_id.get(f.uploaded_by)
                d["uploaded_by"] = uploader.name if uploader else "—"
                d["at"] = d["at"] or f.at
    docs.sort(key=lambda d: (d["at"] is None, d["at"]), reverse=True)
    return {"client": {"id": client.id, "ref": client.ref, "name": client.name},
            "unaudited_on_file": any(d["qualifier"] == "unaudited" for d in docs),
            "documents": docs}


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
