"""Payments as proof-of-work — mirroring the duties philosophy.

Raising an invoice requires the invoice itself (file + number) and auto-emails it to the
client contact via secure links; a declared fallback ("raised outside Baton") needs a
mandatory reason and caps the accountant's invoicing stars like duties' declared.
Receipts require a date, method, and reference (cash requires a note instead).
Health per client: Good / Watch (≤30d overdue) / At risk (>30d) — overdue counts BOTH
unraised-overdue invoices and invoiced-but-unpaid amounts.
"""

import uuid
from datetime import date as date_type, datetime, timezone

from fastapi import APIRouter, Depends, File as FileParam, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import blobs, emails
from ..db import get_db
from ..models import Client, Payment, Tenant, User
from ..security import current_user, require_roles
from ..tenancy import get_scoped_or_404, tenant_select
from ..workflow import conflict, iso, now
from .files import attachments_or_links, store_upload

router = APIRouter(prefix="/payments", tags=["payments"])

RECEIPT_METHODS = ("bank_transfer", "cheque", "cash", "card", "other")
METHOD_LABELS = {"bank_transfer": "bank transfer", "cheque": "cheque", "cash": "cash",
                 "card": "card", "other": "other"}


def received_of(p: Payment) -> float:
    return sum(float(r.get("amount", 0)) for r in p.receipts)


def is_done(p: Payment) -> bool:
    return received_of(p) >= float(p.amount) - 0.5


def lifecycle_of(p: Payment) -> str:
    if is_done(p):
        return "settled"
    if p.due_at < now():
        return "overdue"
    if received_of(p) > 0:
        return "partially_received"
    if p.invoice_raised:
        return "invoiced"
    return "awaiting_invoice"


def _serialize(p: Payment) -> dict:
    received = received_of(p)
    overdue_days = max(0.0, (now() - p.due_at).total_seconds() / 86400) if not is_done(p) else 0.0
    inv = p.invoice or {}
    return {
        "id": p.id, "client_id": p.client_id, "proposal_id": p.proposal_id, "label": p.label,
        "amount": float(p.amount), "due_at": p.due_at, "invoice_raised": p.invoice_raised,
        "invoice_raised_at": p.invoice_raised_at,
        "invoice_number": inv.get("number"), "invoice_files": inv.get("files", []),
        "invoice_declared": bool(inv.get("declared")),
        "received": received, "done": is_done(p), "lifecycle": lifecycle_of(p),
        "overdue_days": round(overdue_days, 1),
        "receipts": p.receipts, "events": p.events,
    }


@router.get("")
def list_payments(user: User = Depends(require_roles("Admin", "Accountant", "Manager")),
                  db: Session = Depends(get_db)):
    rows = db.scalars(tenant_select(Payment, user).order_by(Payment.due_at)).all()
    return [_serialize(p) for p in rows]


@router.post("/{payment_id}/raise-invoice")
def raise_invoice(
    payment_id: uuid.UUID,
    invoice_number: str = Form(...),
    invoice_date: str = Form(""),  # ISO date; defaults to today
    declared_reason: str = Form(""),  # set → raised outside Baton, no files/email, stars capped
    invoice: list[UploadFile] = FileParam(default=[]),
    user: User = Depends(require_roles("Admin", "Accountant")),
    db: Session = Depends(get_db),
):
    p = get_scoped_or_404(db, Payment, payment_id, user)
    if p.invoice_raised:
        raise conflict("Invoice already raised for this payment")
    if not invoice_number.strip():
        raise HTTPException(status_code=422, detail="invoice_number is required")
    try:
        inv_date = date_type.fromisoformat(invoice_date) if invoice_date.strip() else now().date()
    except ValueError:
        raise HTTPException(status_code=422, detail="invoice_date must be an ISO date (YYYY-MM-DD)")

    number = invoice_number.strip()
    t0 = now()

    if declared_reason.strip():
        p.invoice = {"number": number, "date": inv_date.isoformat(), "files": [],
                     "by": str(user.id), "declared": True, "reason": declared_reason.strip()}
        p.invoice_raised = True
        p.invoice_raised_at = t0
        p.events = [*p.events, {"at": iso(t0), "by": str(user.id),
                                "text": f'Invoice {number} DECLARED raised outside Baton — reason: '
                                        f'"{declared_reason.strip()}". No invoice document on file.'}]
        db.commit()
        return _serialize(p)

    if not invoice:
        raise HTTPException(status_code=422, detail="The invoice file is required — attach the invoice PDF, "
                                                    "or use the declared fallback with a reason")
    client = db.get(Client, p.client_id) if p.client_id else None
    contact = (client.contact or {}) if client else {}
    contact_email = (contact.get("email") or "").strip()
    if not contact_email:
        raise conflict(f"No contact email on file for {client.name if client else 'this client'} — "
                       f"add one (PATCH /clients/{{id}}/contact) and raise the invoice again")
    contact_name = contact.get("contactPerson") or contact.get("name") or "Sir/Madam"

    stored = [store_upload(db, user, "payment", p.id, f) for f in invoice]
    attachments, link_lines = attachments_or_links(stored)

    firm = db.get(Tenant, user.tenant_id)
    body = (f"Dear {contact_name},\n\n"
            f"Please find attached invoice {number} for {p.label} — AED {float(p.amount):,.0f}, "
            f"due {p.due_at:%d %b %Y}.")
    if link_lines:
        body += (f"\n\n(The invoice was too large to attach — download within "
                 f"{blobs.LINK_TTL_MIN} minutes:)\n" + "\n".join(link_lines))
    body += (f"\n\nKindly arrange payment by the due date."
             f"\n\n{emails.reply_to_line(user.name, user.email)}"
             f"\n\nBest regards\n{firm.name}")
    emails.send_client(
        contact_email, f"{firm.short} — Invoice {number}: {p.label}", body,
        reply_to=(user.email, user.name), attachments=attachments,
        db=db, tenant_id=user.tenant_id,
    )

    p.invoice = {"number": number, "date": inv_date.isoformat(),
                 "files": [{"file_id": str(f.id), "name": f.name, "size": f.size} for f in stored],
                 "by": str(user.id), "declared": False, "reason": None}
    p.invoice_raised = True
    p.invoice_raised_at = t0
    p.events = [*p.events, {"at": iso(t0), "by": str(user.id),
                            "text": f"Invoice {number} raised and emailed to {contact_email} "
                                    f"({', '.join(f.name for f in stored)})"}]
    db.commit()
    return _serialize(p)


@router.post("/{payment_id}/record-receipt")
def record_receipt(
    payment_id: uuid.UUID,
    amount: float = Form(gt=0),
    received_date: str = Form(""),  # ISO date; defaults to today
    method: str = Form(...),
    reference: str = Form(""),  # mandatory except cash
    note: str = Form(""),  # mandatory for cash
    evidence: UploadFile | None = FileParam(default=None),
    user: User = Depends(require_roles("Admin", "Accountant")),
    db: Session = Depends(get_db),
):
    p = get_scoped_or_404(db, Payment, payment_id, user)
    if is_done(p):
        raise conflict("Payment is already fully received")
    if method not in RECEIPT_METHODS:
        raise HTTPException(status_code=422, detail=f"method must be one of {RECEIPT_METHODS}")
    if method == "cash":
        if not note.strip():
            raise HTTPException(status_code=422, detail="Cash receipts require a note "
                                                        "(who paid, where it was received)")
    elif not reference.strip():
        raise HTTPException(status_code=422, detail="A reference (transaction ID / cheque no.) is required")
    try:
        rec_date = date_type.fromisoformat(received_date) if received_date.strip() else now().date()
    except ValueError:
        raise HTTPException(status_code=422, detail="received_date must be an ISO date (YYYY-MM-DD)")

    receipt = {"amount": amount, "at": iso(now()), "received_date": rec_date.isoformat(),
               "method": method, "reference": reference.strip() or None, "note": note.strip() or None,
               "by": str(user.id)}
    evt_suffix = ""
    if evidence is not None:
        f = store_upload(db, user, "payment", p.id, evidence)
        receipt.update(file_id=str(f.id), file_name=f.name)
        evt_suffix = f" — evidence: {f.name}"
    p.receipts = [*p.receipts, receipt]
    ref_txt = (f"note: \"{note.strip()}\"" if method == "cash" else f"ref {reference.strip()}")
    events = [*p.events, {"at": iso(now()), "by": str(user.id),
                          "text": f"Receipt recorded: AED {round(amount):,} received {rec_date:%d %b %Y} "
                                  f"by {METHOD_LABELS[method]}, {ref_txt}{evt_suffix}"}]
    if is_done(p):
        events.append({"at": iso(now()), "by": "system", "text": "Fully received — reminders stopped"})
    p.events = events
    db.commit()
    return _serialize(p)


@router.get("/health/{client_id}")
def client_health(client_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Good / Watch (≤30d overdue) / At risk (>30d). Overdue includes both unraised-overdue
    invoices and invoiced-but-unpaid amounts — anything past due and not settled."""
    client = get_scoped_or_404(db, Client, client_id, user)
    rows = db.scalars(tenant_select(Payment, user).where(Payment.client_id == client.id)).all()
    t = now()
    overdue = [p for p in rows if not is_done(p) and p.due_at < t and received_of(p) < float(p.amount)]
    outstanding = sum(float(p.amount) - received_of(p) for p in overdue)
    worst = max(((t - p.due_at).total_seconds() / 86400 for p in overdue), default=0.0)
    badge = "Good" if not overdue else ("Watch" if worst <= 30 else "At risk")
    return {"client_id": client.id, "ref": client.ref, "badge": badge,
            "outstanding": round(outstanding, 2), "overdue_count": len(overdue),
            "worst_overdue_days": round(worst, 1)}
