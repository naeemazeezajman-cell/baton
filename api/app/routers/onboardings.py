"""Onboarding module — per-activity client documentation relay, created automatically at
EL send. Mirrors the proposal checklist mechanics: staff request items, the baton passes to
the engagement manager (the proposal's requester), items resolve, the baton auto-returns.
Every action writes an append-only onboarding_event."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File as FileParam, Form, HTTPException, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Client, Duty, DutyEvent, Onboarding, OnboardingEvent, OnboardingItem, Notice, Proposal, User
from ..security import current_user
from ..tenancy import get_scoped_or_404, tenant_select
from ..workflow import conflict, iso, now
from .duties import CADENCES, add_cadence, duty_kind, fmt_d
from .files import store_upload

router = APIRouter(prefix="/onboardings", tags=["onboardings"])

QUALIFIERS = ("audited", "unaudited", "draft", "copy")
MASK = "••••••••"


class ItemIn(BaseModel):
    label: str = Field(min_length=1)
    kind: Literal["document", "information", "credential"]
    note: str | None = None


class ItemsIn(BaseModel):
    items: list[ItemIn] = Field(min_length=1)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1)


class CompleteIn(BaseModel):
    cadence: str
    first_due: str  # ISO datetime
    contact_name: str = ""
    contact_email: EmailStr


def _log(db: Session, ob: Onboarding, by_user, text: str):
    db.add(OnboardingEvent(tenant_id=ob.tenant_id, onboarding_id=ob.id, by_user=by_user, text_=text))


def _notify(db: Session, ob: Onboarding, user_id, text: str):
    if user_id:
        db.add(Notice(tenant_id=ob.tenant_id, user_id=user_id, text_=text))


def _get(db: Session, oid: uuid.UUID, user: User) -> Onboarding:
    return get_scoped_or_404(db, Onboarding, oid, user)


def _manager_id(db: Session, ob: Onboarding):
    p = db.get(Proposal, ob.proposal_id) if ob.proposal_id else None
    return p.requested_by if p else None


def _items(db: Session, ob: Onboarding) -> list[OnboardingItem]:
    return db.scalars(select(OnboardingItem).where(OnboardingItem.onboarding_id == ob.id)
                      .order_by(OnboardingItem.requested_at, OnboardingItem.id)).all()


def _item(db: Session, ob: Onboarding, item_id: uuid.UUID) -> OnboardingItem:
    it = db.scalar(select(OnboardingItem).where(OnboardingItem.onboarding_id == ob.id,
                                                OnboardingItem.id == item_id))
    if it is None:
        raise HTTPException(status_code=404, detail="onboarding item not found")
    return it


def _open_items(items) -> list:
    return [i for i in items if i.status == "requested"]


def _pass_baton(db: Session, ob: Onboarding, to_user_id, by: User, note_user_id=None, note_text=None):
    ob.holder = to_user_id
    ob.holder_since = now()
    if note_text and note_user_id:
        _notify(db, ob, note_user_id, note_text)


def serialize_item(i: OnboardingItem, reveal: bool = False) -> dict:
    masked = i.kind == "credential" and i.answer_text and not reveal
    return {
        "id": i.id, "label": i.label, "kind": i.kind, "status": i.status,
        "requested_by": i.requested_by, "note": i.note,
        "answer_text": MASK if masked else i.answer_text,
        "credential_masked": bool(masked),
        "qualifier": i.qualifier, "files": i.files, "reason": i.reason,
        "requested_at": i.requested_at, "resolved_at": i.resolved_at, "accepted_at": i.accepted_at,
    }


def serialize(db: Session, ob: Onboarding, detail: bool = False) -> dict:
    client = db.get(Client, ob.client_id)
    staff = db.get(User, ob.staff_id)
    manager_id = _manager_id(db, ob)
    out = {
        "id": ob.id, "client_id": ob.client_id,
        "client_ref": client.ref if client else None, "client_name": client.name if client else None,
        "client_contact": client.contact if client else None,
        "proposal_id": ob.proposal_id, "service": ob.service,
        "staff_id": ob.staff_id, "staff_name": staff.name if staff else None,
        "manager_id": manager_id,
        "status": ob.status, "holder": ob.holder, "holder_since": ob.holder_since,
        "duty_id": ob.duty_id, "created_at": ob.created_at, "completed_at": ob.completed_at,
    }
    items = _items(db, ob)
    out["open_items"] = len(_open_items(items))
    out["item_count"] = len(items)
    if detail:
        out["items"] = [serialize_item(i) for i in items]
        events = db.scalars(select(OnboardingEvent).where(OnboardingEvent.onboarding_id == ob.id)
                            .order_by(OnboardingEvent.at, OnboardingEvent.id)).all()
        out["events"] = [{"at": e.at, "by": e.by_user, "text": e.text_} for e in events]
    return out


def create_for_el_send(db: Session, p: Proposal, manager: User) -> list[Onboarding]:
    """Called from /el-send: one onboarding per staffed activity."""
    created = []
    for service, staff_id in (p.el or {}).get("assignments", {}).items():
        sid = uuid.UUID(staff_id)
        staff = db.get(User, sid)
        ob = Onboarding(tenant_id=p.tenant_id, client_id=p.client_id, proposal_id=p.id,
                        service=service, staff_id=sid, status="in_progress",
                        holder=sid, holder_since=now())
        db.add(ob)
        db.flush()
        _log(db, ob, None, f"Onboarding started for \"{service}\" at EL send — client documentation relay "
                           f"between {staff.name if staff else 'staff'} and {manager.name} (engagement manager). "
                           f"Request the documents and information needed to begin recurring work.")
        created.append(ob)
    return created


# ---------- read ----------

@router.get("")
def list_onboardings(client_id: uuid.UUID | None = None, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    q = tenant_select(Onboarding, user)
    if client_id:
        q = q.where(Onboarding.client_id == client_id)
    rows = db.scalars(q.order_by(Onboarding.created_at)).all()
    if user.role not in ("Admin", "Manager"):
        rows = [ob for ob in rows if ob.staff_id == user.id or ob.holder == user.id]
    return [serialize(db, ob) for ob in rows]


@router.get("/{oid}")
def get_onboarding(oid: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return serialize(db, _get(db, oid, user), detail=True)


# ---------- staff: request rounds ----------

@router.post("/{oid}/items")
def add_items(oid: uuid.UUID, body: ItemsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ob = _get(db, oid, user)
    if ob.status != "in_progress":
        raise conflict("This onboarding is complete")
    if ob.staff_id != user.id:
        raise conflict("Only the assigned staff member can request items")
    if ob.holder != user.id:
        raise conflict("You are not holding this onboarding — wait for the manager to respond")
    for it in body.items:
        db.add(OnboardingItem(tenant_id=ob.tenant_id, onboarding_id=ob.id, label=it.label,
                              kind=it.kind, note=it.note, requested_by=user.id))
    _log(db, ob, user.id, f"Items requested: {', '.join(i.label + ' (' + i.kind + ')' for i in body.items)}")
    db.commit()
    return serialize(db, ob, detail=True)


@router.post("/{oid}/send-requests")
def send_requests(oid: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ob = _get(db, oid, user)
    if ob.status != "in_progress":
        raise conflict("This onboarding is complete")
    if ob.staff_id != user.id or ob.holder != user.id:
        raise conflict("Only the assigned staff member holding the baton can send requests")
    open_items = _open_items(_items(db, ob))
    if not open_items:
        raise conflict("No open items to send — add requested items first")
    manager_id = _manager_id(db, ob)
    if manager_id is None:
        raise conflict("No engagement manager on record for this onboarding")
    manager = db.get(User, manager_id)
    _pass_baton(db, ob, manager_id, user)
    _log(db, ob, user.id, f"{len(open_items)} open request(s) sent to {manager.name} — baton passes to them")
    _notify(db, ob, manager_id, f"Onboarding · {ob.service} for {serialize(db, ob)['client_name']}: "
                                f"{user.name} requested {len(open_items)} item(s) — the baton is with you")
    db.commit()
    return serialize(db, ob, detail=True)


# ---------- manager: resolve items ----------

def _require_manager_turn(db: Session, ob: Onboarding, user: User):
    if ob.status != "in_progress":
        raise conflict("This onboarding is complete")
    manager_id = _manager_id(db, ob)
    if user.id != manager_id:
        raise conflict("Only the engagement manager can resolve requested items")
    if ob.holder != user.id:
        raise conflict("You are not holding this onboarding")


def _maybe_autoreturn(db: Session, ob: Onboarding, by: User):
    items = _items(db, ob)
    if not _open_items(items):
        staff = db.get(User, ob.staff_id)
        _pass_baton(db, ob, ob.staff_id, by)
        _log(db, ob, None, f"All open items resolved — baton auto-returned to {staff.name if staff else 'staff'}")
        _notify(db, ob, ob.staff_id, f"Onboarding · {ob.service} for {serialize(db, ob)['client_name']}: "
                                     f"all requested items resolved — review and continue")


@router.post("/{oid}/items/{item_id}/provide")
def provide_item(
    oid: uuid.UUID, item_id: uuid.UUID,
    answer_text: str = Form(""),
    qualifier: str = Form(""),
    evidence: list[UploadFile] = FileParam(default=[]),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ob = _get(db, oid, user)
    _require_manager_turn(db, ob, user)
    it = _item(db, ob, item_id)
    if it.status != "requested":
        raise conflict(f"Item is {it.status} — only requested items can be provided")
    if qualifier and qualifier not in QUALIFIERS:
        raise HTTPException(status_code=422, detail=f"qualifier must be one of {QUALIFIERS}")

    if it.kind == "document":
        if not evidence:
            raise HTTPException(status_code=422, detail="A document upload is required for this item")
        stored = [store_upload(db, user, "onboarding", ob.id, f) for f in evidence]
        it.files = [*it.files, *[{"file_id": str(f.id), "name": f.name, "size": f.size} for f in stored]]
        it.qualifier = qualifier or None
        it.status = "provided"
        q_txt = f" (qualifier: {qualifier})" if qualifier else ""
        _log(db, ob, user.id, f'Document provided for "{it.label}": '
                              f"{', '.join(f.name for f in stored)}{q_txt}")
    else:
        if not answer_text.strip():
            raise HTTPException(status_code=422, detail="An answer is required for this item")
        it.answer_text = answer_text.strip()
        it.status = "answered"
        if it.kind == "credential":
            _log(db, ob, user.id, f'Credential provided for "{it.label}" — stored server-side, '
                                  f"returned masked; every reveal is logged")
        else:
            _log(db, ob, user.id, f'Information provided for "{it.label}"')
    it.reason = None
    it.resolved_at = now()
    _maybe_autoreturn(db, ob, user)
    db.commit()
    return serialize(db, ob, detail=True)


@router.post("/{oid}/items/{item_id}/not-available")
def not_available(oid: uuid.UUID, item_id: uuid.UUID, body: ReasonIn,
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    ob = _get(db, oid, user)
    _require_manager_turn(db, ob, user)
    it = _item(db, ob, item_id)
    if it.status != "requested":
        raise conflict(f"Item is {it.status}")
    it.status = "not_available"
    it.reason = body.reason.strip()
    it.resolved_at = now()
    _log(db, ob, user.id, f'Item "{it.label}" marked NOT AVAILABLE — reason: "{it.reason}"')
    _maybe_autoreturn(db, ob, user)
    db.commit()
    return serialize(db, ob, detail=True)


# ---------- staff: accept / re-request / withdraw ----------

def _require_staff(ob: Onboarding, user: User):
    if ob.status != "in_progress":
        raise conflict("This onboarding is complete")
    if ob.staff_id != user.id:
        raise conflict("Only the assigned staff member can do this")


@router.post("/{oid}/items/{item_id}/accept")
def accept_item(oid: uuid.UUID, item_id: uuid.UUID, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    ob = _get(db, oid, user)
    _require_staff(ob, user)
    it = _item(db, ob, item_id)
    if it.status not in ("provided", "answered", "not_available"):
        raise conflict(f"Item is {it.status} — nothing to accept")
    it.accepted_at = now()
    _log(db, ob, user.id, f'Item "{it.label}" accepted by {user.name}')
    db.commit()
    return serialize(db, ob, detail=True)


@router.post("/{oid}/items/{item_id}/re-request")
def re_request(oid: uuid.UUID, item_id: uuid.UUID, body: ReasonIn,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    ob = _get(db, oid, user)
    _require_staff(ob, user)
    it = _item(db, ob, item_id)
    if it.status not in ("provided", "answered", "not_available"):
        raise conflict(f"Item is {it.status} — nothing to re-request")
    it.status = "requested"
    it.reason = body.reason.strip()
    it.resolved_at = None
    it.accepted_at = None
    _log(db, ob, user.id, f'Item "{it.label}" RE-REQUESTED — reason: "{it.reason}"')
    db.commit()
    return serialize(db, ob, detail=True)


@router.post("/{oid}/items/{item_id}/withdraw")
def withdraw_item(oid: uuid.UUID, item_id: uuid.UUID, body: ReasonIn,
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Staff withdraws a request — allowed even while the baton is with the manager."""
    ob = _get(db, oid, user)
    _require_staff(ob, user)
    it = _item(db, ob, item_id)
    if it.status == "withdrawn":
        raise conflict("Item is already withdrawn")
    it.status = "withdrawn"
    it.reason = body.reason.strip()
    it.resolved_at = now()
    _log(db, ob, user.id, f'Item "{it.label}" WITHDRAWN — reason: "{it.reason}"')
    _notify(db, ob, _manager_id(db, ob), f'Onboarding · {ob.service}: {user.name} withdrew "{it.label}"')
    if ob.holder != ob.staff_id:
        _maybe_autoreturn(db, ob, user)
    db.commit()
    return serialize(db, ob, detail=True)


# ---------- credentials ----------

@router.get("/{oid}/items/{item_id}/reveal")
def reveal_credential(oid: uuid.UUID, item_id: uuid.UUID, user: User = Depends(current_user),
                      db: Session = Depends(get_db)):
    ob = _get(db, oid, user)
    manager_id = _manager_id(db, ob)
    if user.id not in (ob.staff_id, manager_id) and user.role != "Admin":
        raise conflict("Only the assigned staff, the engagement manager, or an Admin can reveal credentials")
    it = _item(db, ob, item_id)
    if it.kind != "credential" or not it.answer_text:
        raise conflict("This item is not a stored credential")
    _log(db, ob, user.id, f'Credential "{it.label}" viewed by {user.name}')
    if manager_id and manager_id != user.id:
        _notify(db, ob, manager_id, f'Onboarding · {ob.service}: credential "{it.label}" was viewed by {user.name}')
    db.commit()
    return {"id": it.id, "label": it.label, "value": it.answer_text}


# ---------- completion → duty creation (the bridge) ----------

@router.post("/{oid}/complete")
def complete_onboarding(oid: uuid.UUID, body: CompleteIn, user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    from datetime import datetime

    ob = _get(db, oid, user)
    _require_staff(ob, user)
    open_items = _open_items(_items(db, ob))
    if open_items:
        raise conflict(f"{len(open_items)} item(s) still open — resolve every request before completing")
    if body.cadence not in CADENCES:
        raise HTTPException(status_code=422, detail=f"cadence must be one of {CADENCES}")
    try:
        first_due = datetime.fromisoformat(body.first_due.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail="first_due must be an ISO datetime")

    client = db.get(Client, ob.client_id)
    duty = Duty(
        tenant_id=ob.tenant_id, staff_id=ob.staff_id, client_name=client.name, client_id=client.id,
        service=ob.service, kind=duty_kind(ob.service),
        contact={"name": body.contact_name.strip(), "email": str(body.contact_email)},
        cadence=body.cadence, next_due=first_due,
    )
    db.add(duty)
    db.flush()
    db.add(DutyEvent(tenant_id=ob.tenant_id, duty_id=duty.id, by_user=None,
                     text_=f"Duty created from completed onboarding — {body.cadence} · first tracked deadline "
                           f"{fmt_d(first_due)} · client contact: {body.contact_name.strip()} "
                           f"<{body.contact_email}>. All future deadlines are computed automatically."))
    ob.status = "complete"
    ob.completed_at = now()
    ob.holder = None
    ob.duty_id = duty.id
    _log(db, ob, user.id, f"Onboarding complete — recurring duty created: {ob.service}, {body.cadence}, "
                          f"first due {fmt_d(first_due)}")
    manager_id = _manager_id(db, ob)
    _notify(db, ob, manager_id, f"Onboarding complete · {ob.service} for {client.name} — recurring duty "
                                f"created ({body.cadence}, first due {fmt_d(first_due)})")
    db.commit()
    return {"onboarding": serialize(db, ob, detail=True),
            "duty": {"id": duty.id, "service": duty.service, "cadence": duty.cadence,
                     "next_due": duty.next_due, "contact": duty.contact}}
