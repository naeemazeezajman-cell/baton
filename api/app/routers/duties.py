"""Deadline engine — src/baton-prototype.jsx (DutyCard / markDutyDone) is the spec.

Completion methods: sent (deliverables uploaded + emailed to the client contact),
proof (filed return + structured record), declared (mandatory reason).
next_due advances by cadence in calendar months FROM THE DUE DATE (statutory
anchoring) — late completion does not shift the schedule. One-time duties close.
"""

import calendar
import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File as FileParam, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import blobs, emails
from ..db import get_db
from ..models import Client, Duty, DutyCompletion, DutyEvent, User
from ..security import current_user, require_roles
from ..tenancy import get_scoped_or_404, tenant_select
from ..workflow import conflict, now
from .files import attachments_or_links, store_upload

router = APIRouter(prefix="/duties", tags=["duties"])

CADENCE_MONTHS = {"monthly": 1, "quarterly": 3, "half-yearly": 6, "annual": 12}
CADENCES = (*CADENCE_MONTHS, "one-time")


def duty_kind(service: str) -> str:
    if re.search(r"vat", service, re.I):
        return "vat"
    if re.search(r"corporate tax|\bct\b", service, re.I):
        return "ct"
    if re.search(r"bookkeep|report|account", service, re.I):
        return "report"
    return "other"


def add_cadence(due: datetime, cadence: str) -> datetime | None:
    """Advance by calendar months from the DUE date; None for one-time (no next occurrence)."""
    months = CADENCE_MONTHS.get(cadence)
    if not months:
        return None
    m = due.month - 1 + months
    year, month = due.year + m // 12, m % 12 + 1
    day = min(due.day, calendar.monthrange(year, month)[1])
    return due.replace(year=year, month=month, day=day)


def fmt_d(dt: datetime) -> str:
    return dt.strftime("%d %b %Y")


def fmt_dur(ms: float) -> str:
    d = ms / 86400000
    if d >= 1:
        return f"{d:.1f}d"
    h = ms / 3600000
    if h >= 1:
        return f"{h:.1f}h"
    return f"{max(1, round(ms / 60000))}m"


class ContactIn(BaseModel):
    name: str = ""
    email: str = ""


class DutyCreateIn(BaseModel):
    staff_id: uuid.UUID
    client_name: str
    client_id: uuid.UUID | None = None
    service: str
    cadence: str
    next_due: datetime
    contact: ContactIn | None = None


def _log(db: Session, d: Duty, by_user: uuid.UUID | None, text: str):
    db.add(DutyEvent(tenant_id=d.tenant_id, duty_id=d.id, by_user=by_user, text_=text))


def _serialize(d: Duty, db: Session) -> dict:
    history = db.scalars(
        select(DutyCompletion).where(DutyCompletion.duty_id == d.id).order_by(DutyCompletion.completed_at)
    ).all()
    events = db.scalars(select(DutyEvent).where(DutyEvent.duty_id == d.id).order_by(DutyEvent.at, DutyEvent.id)).all()
    return {
        "id": d.id, "staff_id": d.staff_id, "client_name": d.client_name, "client_id": d.client_id,
        "service": d.service, "kind": d.kind, "contact": d.contact, "cadence": d.cadence,
        "next_due": d.next_due, "closed": d.closed,
        "history": [
            {"due_at": h.due_at, "completed_at": h.completed_at, "late_ms": h.late_ms, "method": h.method,
             "emailed_to": h.emailed_to, "reason": h.reason, "note": h.note, "record": h.record,
             "evidence": h.evidence}
            for h in history
        ],
        "events": [{"at": e.at, "by": e.by_user, "text": e.text_} for e in events],
    }


@router.get("")
def list_duties(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Staff see their own duties; Managers/Admins see all in the tenant."""
    q = tenant_select(Duty, user)
    if user.role not in ("Admin", "Manager"):
        q = q.where(Duty.staff_id == user.id)
    return [_serialize(d, db) for d in db.scalars(q.order_by(Duty.next_due)).all()]


@router.post("", status_code=201)
def create_duty(body: DutyCreateIn, user: User = Depends(require_roles("Admin", "Manager")), db: Session = Depends(get_db)):
    if body.cadence not in CADENCES:
        raise HTTPException(status_code=422, detail=f"cadence must be one of {CADENCES}")
    staff = get_scoped_or_404(db, User, body.staff_id, user)
    # client_id arrives from the request body — scope it, or a duty (and the VAT filing
    # opened from it) would carry an FK into another tenant's clients row.
    client = get_scoped_or_404(db, Client, body.client_id, user) if body.client_id else None
    kind = duty_kind(body.service)
    d = Duty(
        tenant_id=user.tenant_id, staff_id=staff.id, client_name=body.client_name,
        client_id=client.id if client else None, service=body.service, kind=kind,
        contact=body.contact.model_dump() if body.contact else None,
        cadence=body.cadence, next_due=body.next_due,
    )
    db.add(d)
    db.flush()
    contact_txt = (f" · client contact: {body.contact.name} <{body.contact.email}>"
                   if body.contact and body.contact.email else "")
    _log(db, d, user.id,
         f"Duty registered — {body.cadence} · first tracked deadline {fmt_d(body.next_due)}{contact_txt}. "
         f"All future deadlines are computed automatically from this anchor. Completion requires proof of work "
         f"(deliverables / filed returns) unless explicitly declared otherwise with a reason.")
    db.commit()
    return _serialize(d, db)


def _validate_completion(kind: str, method: str, files: list, record: dict | None,
                         emailed_to: str, reason: str):
    if method == "declared":
        if not reason.strip():
            raise HTTPException(status_code=422, detail="A mandatory reason is required to complete without proof")
        return
    if method == "sent":
        if not files:
            raise HTTPException(status_code=422, detail="'sent' completion requires the deliverable file(s)")
        if not emailed_to.strip():
            raise HTTPException(status_code=422, detail="'sent' completion requires emailed_to (the client contact)")
        return
    if method == "proof":
        if not files:
            raise HTTPException(status_code=422, detail="'proof' completion requires the filed return / acknowledgement file(s)")
        rec = record or {}
        if kind == "vat" and not (rec.get("period") and rec.get("position")):
            raise HTTPException(status_code=422, detail="VAT filing record requires 'period' and 'position'")
        if kind == "ct" and not (rec.get("financial year") and rec.get("position")):
            raise HTTPException(status_code=422, detail="Corporate Tax filing record requires 'financial year' and 'position'")
        return
    raise HTTPException(status_code=422, detail="method must be sent, proof, or declared")


def apply_completion(db: Session, d: Duty, user: User, method: str, stored: list,
                     record_obj: dict | None = None, note: str = "", reason: str = "",
                     emailed_to: str = ""):
    """Core completion: completion row, events, 'sent' email, schedule advance. No commit.
    `stored` is already-persisted File rows (evidence). Used by the /complete endpoint and
    by modules that finish a duty as their last step (e.g. the VAT Filing Engine)."""
    _validate_completion(d.kind, method, stored, record_obj, emailed_to, reason)

    done_at = now()
    due_at = d.next_due if d.next_due.tzinfo else d.next_due.replace(tzinfo=timezone.utc)
    late_ms = max(0, int((done_at - due_at).total_seconds() * 1000))
    late_txt = f"{fmt_dur(late_ms)} LATE" if late_ms > 0 else "on time"

    evidence_meta = [{"file_id": str(f.id), "name": f.name, "size": f.size} for f in stored]
    names = ", ".join(f.name for f in stored)

    db.add(DutyCompletion(
        tenant_id=d.tenant_id, duty_id=d.id, due_at=d.next_due, completed_at=done_at,
        late_ms=late_ms, method=method, emailed_to=emailed_to or None, reason=reason or None,
        note=note or None, record=record_obj, evidence=evidence_meta,
    ))

    note_txt = f' · note: "{note}"' if note else ""
    if method == "sent":
        contact_name = (d.contact or {}).get("name") or "Sir/Madam"
        attachments, link_lines = attachments_or_links(stored)
        body = (f"Dear {contact_name},\n\nPlease find attached the following for {d.client_name}:\n"
                + "\n".join(f"- {f.name}" for f in stored))
        if link_lines:
            body += (f"\n\n(The files were too large to attach — download within "
                     f"{blobs.LINK_TTL_MIN} minutes:)\n" + "\n".join(link_lines))
        body += ("\n\nKindly let us know if you have any questions."
                 f"\n\n{emails.reply_to_line(user.name, user.email)}\n\nBest regards")
        emails.send_client(emailed_to, f"{d.service} — {d.client_name}", body,
                           reply_to=(user.email, user.name), attachments=attachments,
                           db=db, tenant_id=user.tenant_id)
        how = "download link(s)" if link_lines else "attached"
        _log(db, d, user.id, f"Completed ({late_txt}) — {len(stored)} deliverable(s) uploaded and emailed to "
                             f"{emailed_to}: {names}{note_txt}. Due date was {fmt_d(due_at)}.")
        _log(db, d, None, f"Deliverables email dispatched to {emailed_to} ({how}); "
                          f"replies route to {user.name} <{user.email}>.")
    elif method == "proof":
        rec_txt = ""
        if record_obj:
            pairs = " · ".join(f"{k}: {v}" for k, v in record_obj.items() if v not in ("", None))
            rec_txt = f" Record: {pairs}."
        note_txt2 = f' Note: "{note}"' if note else ""
        _log(db, d, user.id, f"Completed ({late_txt}) — filing proof uploaded: {names}.{rec_txt}"
                             f"{note_txt2} Due date was {fmt_d(due_at)}.")
    else:
        _log(db, d, user.id, f'Completed ({late_txt}) — DECLARED without document proof. '
                             f'Mandatory reason: "{reason}". Due date was {fmt_d(due_at)}.')

    nxt = add_cadence(due_at, d.cadence)
    if nxt:
        d.next_due = nxt
        _log(db, d, None, f"Next deadline auto-computed from the statutory schedule: {fmt_d(nxt)} "
                          f"({d.cadence}). Late completion does not shift the schedule.")
    else:
        d.closed = True
        _log(db, d, None, "One-time duty closed.")


@router.post("/{duty_id}/complete")
def complete_duty(
    duty_id: uuid.UUID,
    method: str = Form(...),
    note: str = Form(""),
    reason: str = Form(""),
    emailed_to: str = Form(""),
    record: str = Form(""),  # JSON object string
    evidence: list[UploadFile] = FileParam(default=[]),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    d = get_scoped_or_404(db, Duty, duty_id, user)
    if d.staff_id != user.id:
        raise conflict("Only the responsible staff member can complete this duty")
    if d.closed:
        raise conflict("This duty is closed")
    try:
        record_obj = json.loads(record) if record.strip() else None
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="record must be a JSON object")
    _validate_completion(d.kind, method, evidence, record_obj, emailed_to, reason)
    stored = [store_upload(db, user, "duty", d.id, f) for f in evidence]
    apply_completion(db, d, user, method, stored, record_obj=record_obj, note=note,
                     reason=reason, emailed_to=emailed_to)
    db.commit()
    return _serialize(d, db)
