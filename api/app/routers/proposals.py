"""Onboarding workflow — ACTION endpoints, not generic PATCH (STRUCTURE.md §1).

Every transition validates caller role, caller-is-holder where required, and current
status; an invalid transition returns 409 {reason}. The prototype's reducer functions
(src/baton-prototype.jsx) are the behavioural spec — comments cite the source function.
"""

import uuid
from datetime import timezone
from typing import Literal

from fastapi import APIRouter, Depends, File as FileParam, Form, HTTPException, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import ai, emails
from ..db import get_db
from ..models import Client, Duty, HolderLog, Notice, Payment, Proposal, ProposalEvent, SignatureUse, User
from ..security import current_user, require_roles
from ..tenancy import get_scoped_or_404, tenant_select
from ..workflow import (
    TERMINAL,
    canon,
    conflict,
    default_basis,
    diff_drafts,
    iso,
    log_event,
    now,
    num,
    pass_holder,
    require_holder,
    require_status,
)
from .duties import fmt_dur as _fmt_dur
from .files import store_upload

router = APIRouter(prefix="/proposals", tags=["proposals"])
workload_router = APIRouter(prefix="/users", tags=["users"])

DAY_MS = 86400000


# ---------- schemas ----------

class ServiceIn(BaseModel):
    name: str
    fee: str | float | None = ""
    basis: str | None = None
    custom: bool = False


class ProspectIn(BaseModel):
    name: str
    email: EmailStr | None = None
    phone: str | None = None
    company: str | None = None
    contactPerson: str | None = None  # prototype field name, stored verbatim in prospect JSONB
    notes: str | None = None


class ProposalCreateIn(BaseModel):
    prospect: ProspectIn
    services: list[ServiceIn]
    assigned_to: uuid.UUID
    notes: str | None = None
    payment_terms_rough: str | None = None
    client_id: uuid.UUID | None = None  # existing-client mode: additional engagement for this client


class AssignIn(BaseModel):
    assigned_to: uuid.UUID


class SlotIn(BaseModel):
    kind: Literal["document", "data"]
    label: str


class RequestItemsIn(BaseModel):
    slots: list[SlotIn] = Field(min_length=1)


class ProvideItemIn(BaseModel):
    slot_id: str
    value: str | None = None
    file_id: uuid.UUID | None = None


class WaiveIn(BaseModel):
    slot_id: str
    action: Literal["request", "approve", "still-required"]
    reason: str | None = None


class SlotReasonIn(BaseModel):
    slot_id: str
    reason: str


class DraftLineIn(BaseModel):
    service: str
    fee: str | float | None = ""
    basis: str | None = None


class DraftIn(BaseModel):
    lines: list[DraftLineIn] = Field(min_length=1)
    payment_terms: str | None = ""
    validity_days: int = 30
    scope: str | None = ""


class GenerateIn(BaseModel):
    draft: DraftIn
    note: str = "draft"


class SubmitIn(BaseModel):
    version: int  # dirty-version guard: must be the latest generated version


class CommentIn(BaseModel):
    comment: str = Field(min_length=1)


class SignRouteIn(BaseModel):
    signatory_id: uuid.UUID
    note: str | None = None


class NoteIn(BaseModel):
    note: str = Field(min_length=1)


class OptionalNoteIn(BaseModel):
    note: str | None = None


class ClientMailIn(BaseModel):
    to: EmailStr
    subject: str
    body: str
    attach_version: int | None = None


class StaffActivityIn(BaseModel):
    service: str
    staff_id: uuid.UUID


class ELPlanIn(BaseModel):
    advance_pct: int = Field(ge=0, le=100)


class ELNoteIn(BaseModel):
    note: str = ""


class ELRouteIn(BaseModel):
    signatory_id: uuid.UUID


# ---------- helpers ----------

def _get(db: Session, pid: uuid.UUID, user: User) -> Proposal:
    return get_scoped_or_404(db, Proposal, pid, user)


def _notify(db: Session, p_or_tenant, user_id: uuid.UUID | None, text: str):
    if user_id is None:
        return
    tenant_id = p_or_tenant.tenant_id if hasattr(p_or_tenant, "tenant_id") else p_or_tenant
    db.add(Notice(tenant_id=tenant_id, user_id=user_id, text_=text))


def _user(db: Session, user: User, user_id) -> User:
    if user_id is None:
        raise HTTPException(status_code=422, detail="user id required")
    return get_scoped_or_404(db, User, uuid.UUID(str(user_id)), user)


def _require_requester(p: Proposal, user: User):
    if p.requested_by != user.id:
        raise conflict("Only the requesting manager can perform this action")


def _require_drafter(p: Proposal, user: User):
    if p.assigned_to != user.id:
        raise conflict("Only the assigned drafter can perform this action")


def _slot(p: Proposal, slot_id: str) -> tuple[list, dict]:
    """Copy the checklist BEFORE mutating — in-place edits to the loaded JSONB also mutate
    SQLAlchemy's committed-state snapshot, so the flush would see no change and skip the UPDATE.
    Returns (new_checklist, slot); the caller mutates slot then assigns p.checklist = new_checklist."""
    checklist = [dict(s) for s in p.checklist]
    for s in checklist:
        if s["id"] == slot_id:
            return checklist, s
    raise HTTPException(status_code=404, detail="checklist item not found")


def _latest_version(p: Proposal) -> dict:
    if not p.versions:
        raise conflict("No generated version exists yet")
    return p.versions[-1]


def _serialize(p: Proposal, db: Session | None = None, include_events: bool = True) -> dict:
    out = {
        "id": p.id, "ref": p.ref, "status": p.status, "prospect": p.prospect,
        "services": p.services, "assigned_to": p.assigned_to, "requested_by": p.requested_by,
        "holder": p.holder, "signatory_id": p.signatory_id, "client_id": p.client_id,
        "checklist": p.checklist, "versions": p.versions, "draft": p.draft,
        "signatures": p.signatures, "el": p.el, "revision_note": p.revision_note,
        "senior_note": p.senior_note, "last_rejection": p.last_rejection,
        "payment_terms_rough": p.payment_terms_rough, "payment_terms": p.payment_terms,
        "created_at": p.created_at, "proposal_sent_at": p.proposal_sent_at,
        "part1_completed_at": p.onboarding_completed_at,
    }
    if db is not None:
        if p.client_id:
            c = db.get(Client, p.client_id)
            out["client_ref"] = c.ref if c else None
        if include_events:
            events = db.scalars(
                select(ProposalEvent).where(ProposalEvent.proposal_id == p.id).order_by(ProposalEvent.at, ProposalEvent.id)
            ).all()
            out["events"] = [
                {"at": e.at, "by": e.by_user, "kind": e.kind, "text": e.text_, "meta": e.meta} for e in events
            ]
        holder_log = db.scalars(
            select(HolderLog).where(HolderLog.proposal_id == p.id).order_by(HolderLog.started_at, HolderLog.id)
        ).all()
        out["holder_log"] = [
            {"user_id": h.user_id, "started_at": h.started_at, "ended_at": h.ended_at, "reason": h.reason}
            for h in holder_log
        ]
    return out


# ---------- read ----------

@router.get("")
def list_proposals(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(tenant_select(Proposal, user).order_by(Proposal.created_at)).all()
    return [_serialize(p, db, include_events=False) for p in rows]


@router.get("/{pid}")
def get_proposal(pid: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _serialize(_get(db, pid, user), db)


# ---------- create / assign (prototype createRequest) ----------

def _norm_name(s: str | None) -> str:
    return " ".join((s or "").split()).lower()


CLOSED_STATUSES = ("el_sent", "lost", "onboarding_complete")


@router.post("", status_code=201)
def create_proposal(
    body: ProposalCreateIn,
    user: User = Depends(require_roles("Admin", "Manager")),
    db: Session = Depends(get_db),
):
    drafter = _user(db, user, body.assigned_to)

    prospect = body.prospect.model_dump()
    existing_client = None
    prior_lost = []
    if body.client_id:
        # existing-client mode: the client's existence is never a duplicate — only an OPEN
        # proposal already linked to that client blocks a new engagement
        existing_client = get_scoped_or_404(db, Client, body.client_id, user)
        open_for_client = [
            x for x in db.scalars(tenant_select(Proposal, user)
                                  .where(Proposal.client_id == existing_client.id)).all()
            if x.status not in CLOSED_STATUSES
        ]
        if open_for_client:
            raise conflict(
                f"An open proposal already exists for client {existing_client.ref} — {existing_client.name}: "
                f"{open_for_client[0].ref} (status: {open_for_client[0].status}). "
                f"Open it instead of creating a duplicate."
            )
        prospect["name"] = existing_client.name  # locked to the client record
        for k in ("email", "phone", "contactPerson"):
            prospect[k] = prospect.get(k) or (existing_client.contact or {}).get(k)
    else:
        # duplicate-prospect guard: refuse while an open matter exists for the same prospect
        # (case-insensitive, whitespace-normalized); prior lost matters only flag a warning
        target = _norm_name(body.prospect.name)
        same_prospect = [
            x for x in db.scalars(tenant_select(Proposal, user)).all()
            if _norm_name((x.prospect or {}).get("name")) == target
        ]
        open_same = [x for x in same_prospect if x.status not in CLOSED_STATUSES]
        if open_same:
            raise conflict(
                f'An open proposal already exists for "{body.prospect.name.strip()}": '
                f"{open_same[0].ref} (status: {open_same[0].status}). Open it instead of creating a duplicate."
            )
        prior_lost = [x for x in same_prospect if x.status == "lost"]

    count = db.scalar(select(func.count()).select_from(Proposal).where(Proposal.tenant_id == user.tenant_id))
    ref = f"P-{count + 1:03d}"
    p = Proposal(
        tenant_id=user.tenant_id, ref=ref,
        prospect=prospect,
        services=[s.model_dump() for s in body.services],
        payment_terms_rough=body.payment_terms_rough,
        status="assigned", assigned_to=drafter.id, requested_by=user.id,
        client_id=existing_client.id if existing_client else None,
        checklist=[], versions=[], el={}, signatures={},
        draft={
            "lines": [
                {"service": s.name, "fee": s.fee or "", "basis": s.basis or default_basis(s.name)}
                for s in body.services
            ],
            "payment_terms": body.payment_terms_rough or "",
            "validity_days": 30, "scope": "",
        },
    )
    db.add(p)
    db.flush()
    if existing_client:
        log_event(db, p, user.id, f"Additional engagement proposal created for existing client "
                                  f"{existing_client.ref} — {existing_client.name}")
    else:
        log_event(db, p, user.id, f'Proposal request created for prospect "{prospect["name"]}"')
    svc = ", ".join(s.name + (" (custom)" if s.custom else "") for s in body.services)
    log_event(db, p, user.id, f"Services requested: {svc}")
    pass_holder(db, p, drafter, user, "assigned to draft the proposal")
    log_event(db, p, None, f'Auto-email sent to {drafter.email} — "You have been assigned proposal {ref}"', kind="email")
    _notify(db, p, drafter.id, f"You were assigned {ref} — proposal for {prospect['name']}")
    if prior_lost:
        log_event(db, p, None, f"Note: this prospect was previously proposed and LOST ({prior_lost[-1].ref}). "
                               f"Prior history remains on record.")
    db.commit()
    out = _serialize(p)
    if existing_client:
        out["client_ref"] = existing_client.ref
    if prior_lost:
        out["previously_lost"] = True
        out["prior_ref"] = prior_lost[-1].ref
    return out


@router.post("/{pid}/assign")
def assign(pid: uuid.UUID, body: AssignIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _get(db, pid, user)
    _require_requester(p, user)
    require_status(p, "assigned")
    drafter = _user(db, user, body.assigned_to)
    p.assigned_to = drafter.id
    log_event(db, p, user.id, f"Proposal reassigned to {drafter.name} for drafting")
    pass_holder(db, p, drafter, user, "assigned to draft the proposal")
    _notify(db, p, drafter.id, f"You were assigned {p.ref} — proposal for {p.prospect.get('name')}")
    db.commit()
    return _serialize(p)


# ---------- checklist lifecycle ----------

@router.post("/{pid}/request-items")
def request_items(pid: uuid.UUID, body: RequestItemsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Drafter requests items from the manager (prototype sendChecklist)."""
    p = _get(db, pid, user)
    _require_drafter(p, user)
    require_holder(p, user)
    require_status(p, "assigned", "drafting", "waiver_review")
    slots = [
        {"id": str(uuid.uuid4()), "kind": s.kind, "label": s.label,
         "status": "pending", "value": "", "file_name": "", "file_id": None, "reason": ""}
        for s in body.slots
    ]
    p.checklist = [*p.checklist, *slots]
    p.status = "docs_with_manager"
    log_event(db, p, user.id, f"Requirements requested from manager: {', '.join(s.label for s in body.slots)}")
    requester = db.get(User, p.requested_by)
    pass_holder(db, p, requester, user, "requirements checklist pending")
    _notify(db, p, p.requested_by, f"{user.name} requested {len(slots)} item(s) on {p.ref}")
    db.commit()
    return _serialize(p)


@router.post("/{pid}/provide-item")
def provide_item(pid: uuid.UUID, body: ProvideItemIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Manager answers a slot with data or an already-uploaded file (prototype fulfillSlot → provided)."""
    p = _get(db, pid, user)
    _require_requester(p, user)
    require_holder(p, user)
    require_status(p, "docs_with_manager")
    checklist, slot = _slot(p, body.slot_id)
    if slot["status"] not in ("pending", "rejected"):
        raise conflict(f"Item is {slot['status']} — only pending or rejected items can be provided")
    if body.file_id:
        from ..models import File
        f = get_scoped_or_404(db, File, body.file_id, user)
        slot.update(status="provided", file_name=f.name, file_id=str(f.id), reason="")
        log_event(db, p, user.id, f'Checklist item "{slot["label"]}" attached: {f.name}')
    elif body.value and body.value.strip():
        slot.update(status="provided", value=body.value.strip(), reason="")
        log_event(db, p, user.id, f'Checklist item "{slot["label"]}" answered')
    else:
        raise HTTPException(status_code=422, detail="Provide a value or a file_id")
    p.checklist = checklist
    db.commit()
    return _serialize(p)


@router.post("/{pid}/waive")
def waive(pid: uuid.UUID, body: WaiveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Waiver lifecycle: manager requests; drafter approves or marks still-required."""
    p = _get(db, pid, user)
    checklist, slot = _slot(p, body.slot_id)
    if body.action == "request":
        _require_requester(p, user)
        require_holder(p, user)
        require_status(p, "docs_with_manager")
        if slot["status"] not in ("pending", "rejected"):
            raise conflict(f"Item is {slot['status']} — cannot request a waiver")
        if not (body.reason and body.reason.strip()):
            raise HTTPException(status_code=422, detail="A reason is mandatory for a waiver request")
        slot.update(status="waiver_requested", reason=body.reason.strip())
        log_event(db, p, user.id, f'Item "{slot["label"]}" marked NOT AVAILABLE — reason: {body.reason.strip()} (waiver requested)')
    else:
        _require_drafter(p, user)
        require_holder(p, user)
        require_status(p, "waiver_review")
        if slot["status"] != "waiver_requested":
            raise conflict(f"Item is {slot['status']} — no waiver to decide")
        if body.action == "approve":
            slot.update(status="waived")
            log_event(db, p, user.id, f'Waiver ACCEPTED for "{slot["label"]}" — proceeding without it')
        else:  # still-required
            slot.update(status="pending", reason="Waiver rejected — item is required to proceed")
            log_event(db, p, user.id, f'Waiver REJECTED for "{slot["label"]}" — item remains required')
    p.checklist = checklist
    db.commit()
    return _serialize(p)


@router.post("/{pid}/reject-item")
def reject_item(pid: uuid.UUID, body: SlotReasonIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Drafter rejects a provided item as wrong/unusable (prototype fulfillSlot → rejected)."""
    p = _get(db, pid, user)
    _require_drafter(p, user)
    require_holder(p, user)
    require_status(p, "waiver_review")
    checklist, slot = _slot(p, body.slot_id)
    if slot["status"] != "provided":
        raise conflict(f"Item is {slot['status']} — only provided items can be rejected")
    slot.update(status="rejected", reason=body.reason.strip())
    log_event(db, p, user.id, f'Item "{slot["label"]}" REJECTED by drafter — {body.reason.strip()}')
    p.checklist = checklist
    db.commit()
    return _serialize(p)


@router.post("/{pid}/withdraw-item")
def withdraw_item(pid: uuid.UUID, body: SlotReasonIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Drafter withdraws a request — allowed even while the baton is with the other side."""
    p = _get(db, pid, user)
    _require_drafter(p, user)
    if p.status in TERMINAL:
        raise conflict("Matter is closed")
    checklist, slot = _slot(p, body.slot_id)
    if slot["status"] not in ("pending", "rejected", "waiver_requested"):
        raise conflict(f"Item is {slot['status']} — cannot be withdrawn")
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="A reason is mandatory for withdrawal")
    slot.update(status="withdrawn", reason=body.reason.strip())
    log_event(db, p, user.id, f'Checklist item "{slot["label"]}" WITHDRAWN by drafter — reason: "{body.reason.strip()}"')
    _notify(db, p, p.requested_by, f'{p.ref}: {user.name} withdrew the request "{slot["label"]}"')
    p.checklist = checklist
    db.commit()
    return _serialize(p)


@router.post("/{pid}/return-checklist")
def return_checklist(pid: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Manager returns the baton once every item is answered (prototype managerReturn)."""
    p = _get(db, pid, user)
    _require_requester(p, user)
    require_holder(p, user)
    require_status(p, "docs_with_manager")
    outstanding = [s for s in p.checklist if s["status"] in ("pending", "rejected")]
    if outstanding:
        raise conflict(f"{len(outstanding)} item(s) still pending/rejected — answer every item before returning")
    waivers = any(s["status"] == "waiver_requested" for s in p.checklist)
    p.status = "waiver_review" if waivers else "drafting"
    log_event(db, p, user.id, "Responses submitted — waiver decision required" if waivers
              else "All requested items provided — returned for drafting")
    drafter = db.get(User, p.assigned_to)
    pass_holder(db, p, drafter, user, "waiver review" if waivers else "checklist complete, drafting can proceed")
    _notify(db, p, p.assigned_to, f"{p.ref}: manager responded to your requirements checklist")
    db.commit()
    return _serialize(p)


@router.post("/{pid}/send-back")
def send_back(pid: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Drafter sends rejected/pending items back to the manager (prototype staffSendBack)."""
    p = _get(db, pid, user)
    _require_drafter(p, user)
    require_holder(p, user)
    require_status(p, "waiver_review")
    p.status = "docs_with_manager"
    log_event(db, p, user.id, "Outstanding checklist items returned to manager")
    requester = db.get(User, p.requested_by)
    pass_holder(db, p, requester, user, "rejected / pending items outstanding")
    _notify(db, p, p.requested_by, f"{p.ref}: items sent back — see rejection reasons")
    db.commit()
    return _serialize(p)


@router.post("/{pid}/start-drafting")
def start_drafting(pid: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _get(db, pid, user)
    _require_drafter(p, user)
    require_holder(p, user)
    require_status(p, "waiver_review")
    p.status = "drafting"
    log_event(db, p, user.id, "Checklist satisfied — drafting started")
    db.commit()
    return _serialize(p)


# ---------- document generation / review / signatures ----------

class PolishIn(BaseModel):
    rough_text: str = Field(min_length=1)


DRAFTING_STATUSES = ("assigned", "drafting", "waiver_review", "manager_review", "senior_review")


@router.post("/{pid}/polish-terms")
def polish_terms(pid: uuid.UUID, body: PolishIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """AI rewording (STRUCTURE.md §8) — server-side Anthropic call, key never reaches the
    browser. Graceful fallback: on any error the raw text comes back with polished=false."""
    p = _get(db, pid, user)
    require_holder(p, user)
    require_status(p, *DRAFTING_STATUSES)
    polished = ai.polish_payment_terms(body.rough_text)
    if polished:
        return {"polished_text": polished, "polished": True}
    return {"polished_text": body.rough_text, "polished": False}

@router.post("/{pid}/generate")
def generate(pid: uuid.UUID, body: GenerateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Compose the proposal document (prototype generateVersion). AI-professionalizes the
    payment terms server-side; falls back to raw text on any error. Stores version metadata
    and a field-level diff vs the previous version as a proposal_event kind=diff."""
    p = _get(db, pid, user)
    if user.id not in (p.assigned_to, p.requested_by, p.signatory_id):
        raise conflict("Only the drafter, requesting manager, or signatory can generate the document")
    if p.status in TERMINAL or p.status in ("proposal_sent", "el_staffing", "el_senior_review", "el_approved",
                                            "signed", "onboarding_complete"):
        raise conflict(f"Document is locked at status {p.status}")

    draft = body.draft.model_dump()
    for line in draft["lines"]:
        line["basis"] = line.get("basis") or default_basis(line["service"])
    rough = (draft.get("payment_terms") or "").strip()
    polished_from = None
    if rough:
        polished = ai.polish_payment_terms(rough)
        if polished and polished != rough:
            draft["payment_terms"] = polished
            polished_from = rough
    p.payment_terms_rough = rough or p.payment_terms_rough
    p.payment_terms = draft.get("payment_terms") or None

    prev = p.versions[-1] if p.versions else None
    v = len(p.versions) + 1
    version = {
        "v": v, "at": iso(now()), "by": str(user.id), "data": draft,
        "note": body.note, "polished_from": polished_from, "signatures": {},
    }
    p.versions = [*p.versions, version]
    p.draft = draft
    log_event(db, p, user.id, f"Proposal document generated — version v{v} ({body.note})")
    if prev:
        changes = diff_drafts(prev["data"], draft)
        if changes:
            log_event(db, p, user.id, f"Changes in v{v} vs v{prev['v']}: {'; '.join(changes)}",
                      kind="diff", meta={"v": v, "prev_v": prev["v"], "changes": changes})
        else:
            log_event(db, p, None, f"v{v} is identical in commercial content to v{prev['v']}", kind="diff",
                      meta={"v": v, "prev_v": prev["v"], "changes": []})
    if polished_from:
        log_event(db, p, None, f'Payment terms professionalized by the CRM drafting assistant. '
                               f'Original wording preserved on record: "{polished_from}"')
    if user.id == p.assigned_to:
        if p.status == "assigned":
            p.status = "drafting"
        log_event(db, p, None, f"v{v} is in drafter preview — not yet submitted to the manager")
    elif p.status == "senior_review":
        _notify(db, p, p.requested_by, f"{p.ref}: commercial terms revised by {user.name} at senior review — v{v}")
        _notify(db, p, p.assigned_to, f"{p.ref}: commercial terms revised by {user.name} — v{v}")
        log_event(db, p, user.id, "Note: manager & drafter notified of senior revision")
    else:
        _notify(db, p, p.assigned_to, f"{p.ref}: commercial terms revised by {user.name} — v{v} generated")
    db.commit()
    return {"version": version, "proposal": _serialize(p)}


@router.post("/{pid}/submit")
def submit(pid: uuid.UUID, body: SubmitIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Drafter submits to manager. Dirty-version guard: the submitted version must be the
    latest generated one and match the live form state."""
    p = _get(db, pid, user)
    _require_drafter(p, user)
    require_holder(p, user)
    require_status(p, "drafting")
    latest = _latest_version(p)
    if body.version != latest["v"]:
        raise conflict(f"Version v{body.version} is not the latest generated version (v{latest['v']}) — regenerate before submitting")
    if canon(p.draft) != canon(latest["data"]):
        raise conflict("Form state differs from the latest generated version — regenerate before submitting")
    p.status = "manager_review"
    p.revision_note = None
    log_event(db, p, user.id, f"Proposal v{latest['v']} reviewed by drafter and submitted to manager")
    requester = db.get(User, p.requested_by)
    pass_holder(db, p, requester, user, "proposal ready for review")
    _notify(db, p, p.requested_by, f"{p.ref}: proposal v{latest['v']} ready for your review")
    db.commit()
    return _serialize(p)


@router.post("/{pid}/send-for-revision")
def send_for_revision(pid: uuid.UUID, body: CommentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Manager review fork: return-to-drafter with a mandatory instruction (prototype sendForRevision)."""
    p = _get(db, pid, user)
    _require_requester(p, user)
    require_holder(p, user)
    require_status(p, "manager_review")
    p.status = "drafting"
    p.revision_note = {"by": str(user.id), "at": iso(now()), "text": body.comment}
    p.last_rejection = None
    log_event(db, p, user.id, f'Returned to drafter for revision — instruction: "{body.comment}"')
    drafter = db.get(User, p.assigned_to)
    pass_holder(db, p, drafter, user, "revision instructed after client discussion")
    _notify(db, p, p.assigned_to, f'{p.ref}: revision requested by {user.name} — "{body.comment}"')
    db.commit()
    return _serialize(p)


def _record_signature(db: Session, p: Proposal, user: User, doc_label: str, which: str):
    """Write signature_uses and embed the specimen ref into the latest version metadata."""
    db.add(SignatureUse(tenant_id=p.tenant_id, user_id=user.id, document=doc_label, context=p.ref))
    if p.versions:
        versions = [dict(v) for v in p.versions]
        sigs = dict(versions[-1].get("signatures") or {})
        sigs[which] = {
            "by": str(user.id), "at": iso(now()),
            "specimen_ref": str(user.id) if user.sig_specimen else None,
        }
        versions[-1]["signatures"] = sigs
        p.versions = versions


@router.post("/{pid}/sign-route")
def sign_route(pid: uuid.UUID, body: SignRouteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Manager signs and routes to a senior signatory (prototype managerSignRoute)."""
    p = _get(db, pid, user)
    _require_requester(p, user)
    require_holder(p, user)
    require_status(p, "manager_review")
    signatory = _user(db, user, body.signatory_id)
    if signatory.id == user.id:
        raise conflict("You cannot route the document to yourself for counter-signature")
    if signatory.role != "Admin" or not signatory.signatory:
        raise conflict("The signatory must be an Admin with signing authority")
    p.signatures = {**p.signatures, "manager": {"by": str(user.id), "at": iso(now())}}
    p.signatory_id = signatory.id
    p.status = "senior_review"
    p.last_rejection = None
    p.senior_note = {"by": str(user.id), "at": iso(now()), "text": body.note} if body.note else None
    _record_signature(db, p, user, f"Proposal {p.ref} v{len(p.versions)}", "manager")
    log_event(db, p, user.id, f"Proposal approved & digitally signed by {user.name} (identity re-confirmed)")
    if body.note:
        log_event(db, p, user.id, f'Note to signatory: "{body.note}"')
    pass_holder(db, p, signatory, user, "routed for senior review & counter-signature")
    _notify(db, p, signatory.id, f"{p.ref}: proposal awaiting your review & signature")
    db.commit()
    return _serialize(p)


@router.post("/{pid}/senior-approve")
def senior_approve(pid: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _get(db, pid, user)
    if p.signatory_id != user.id:
        raise conflict("Only the routed signatory can approve")
    require_holder(p, user)
    require_status(p, "senior_review")
    p.signatures = {**p.signatures, "senior": {"by": str(user.id), "at": iso(now())}}
    p.status = "signed"
    _record_signature(db, p, user, f"Proposal {p.ref} v{len(p.versions)}", "senior")
    log_event(db, p, user.id, f"Proposal approved & counter-signed by {user.name} (identity re-confirmed). Document locked.")
    requester = db.get(User, p.requested_by)
    pass_holder(db, p, requester, user, "signed proposal returned — ready to send to client")
    _notify(db, p, p.requested_by, f"{p.ref}: proposal signed by {user.name} — ready to send to client")
    db.commit()
    return _serialize(p)


@router.post("/{pid}/senior-reject")
def senior_reject(pid: uuid.UUID, body: NoteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Senior rejection voids the manager signature and pins a rejection banner (prototype seniorReject)."""
    p = _get(db, pid, user)
    if p.signatory_id != user.id:
        raise conflict("Only the routed signatory can reject")
    require_holder(p, user)
    require_status(p, "senior_review")
    p.signatures = {**p.signatures, "manager": None}
    p.status = "manager_review"
    p.last_rejection = {"by": str(user.id), "at": iso(now()), "note": body.note, "stage": "proposal"}
    # stamp the rejection on the version that was rejected, so the fate of every version
    # stays visible in the history even after last_rejection is cleared on re-route
    if p.versions:
        versions = [dict(v) for v in p.versions]
        versions[-1]["rejection"] = {"by": str(user.id), "at": iso(now()), "note": body.note}
        p.versions = versions
    log_event(db, p, user.id, f'Senior review REJECTED by {user.name} — note: "{body.note}". '
                              f"Manager signature voided; revision required.")
    requester = db.get(User, p.requested_by)
    pass_holder(db, p, requester, user, "senior rejection — revise and re-route")
    _notify(db, p, p.requested_by, f'{p.ref}: rejected at senior review — "{body.note}"')
    db.commit()
    return _serialize(p)


class ApproveVersionIn(BaseModel):
    version_no: int = Field(ge=1)


@router.post("/{pid}/approve-version")
def approve_version(pid: uuid.UUID, body: ApproveVersionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Senior approves an EARLIER version's terms: re-issues that version's content as v(n+1),
    re-applies the original manager's signature (content identical to what they signed), then
    counter-signs and locks exactly like senior-approve. One transaction."""
    import copy

    p = _get(db, pid, user)
    if p.signatory_id != user.id:
        raise conflict("Only the routed signatory can approve")
    require_holder(p, user)
    require_status(p, "senior_review")
    latest = _latest_version(p)
    if body.version_no >= latest["v"]:
        raise conflict(f"v{body.version_no} is the current version — use senior-approve, or pick an earlier version")
    chosen = next((v for v in p.versions if v["v"] == body.version_no), None)
    if chosen is None:
        raise conflict(f"Version v{body.version_no} does not exist")
    mgr_sig = (chosen.get("signatures") or {}).get("manager")
    if not mgr_sig:
        raise conflict(f"v{body.version_no} never carried a manager signature when routed — "
                       f"only manager-signed versions can be approved this way")

    manager = db.get(User, uuid.UUID(mgr_sig["by"]))
    manager_name = manager.name if manager else "the original manager"
    data = copy.deepcopy(chosen["data"])
    v = latest["v"] + 1

    # 1) revert the live draft and issue the identical re-issue version
    new_version = {
        "v": v, "at": iso(now()), "by": str(user.id), "data": data,
        "note": f"re-issued from v{chosen['v']} at senior review",
        "reverted_from": chosen["v"],
        "signatures": {"manager": {"by": mgr_sig["by"], "at": iso(now()),
                                   "specimen_ref": mgr_sig.get("specimen_ref"),
                                   "reapplied_from_v": chosen["v"]}},
    }
    p.versions = [*p.versions, new_version]
    p.draft = copy.deepcopy(data)
    p.payment_terms = data.get("payment_terms") or None
    log_event(db, p, user.id, f"Version v{v} issued — content identical to v{chosen['v']} "
                              f"(earlier terms approved at senior review)")
    changes = diff_drafts(latest["data"], data)
    log_event(db, p, user.id,
              f"Changes in v{v} vs v{latest['v']}: {'; '.join(changes)}" if changes
              else f"v{v} is identical in commercial content to v{latest['v']}",
              kind="diff", meta={"v": v, "prev_v": latest["v"], "changes": changes, "reverted_from": chosen["v"]})

    # 2) re-apply the original manager's signature ref
    p.signatures = {**p.signatures,
                    "manager": {"by": mgr_sig["by"], "at": iso(now()), "reapplied_from_v": chosen["v"]}}
    db.add(SignatureUse(tenant_id=p.tenant_id, user_id=uuid.UUID(mgr_sig["by"]),
                        document=f"Proposal {p.ref} v{v} (re-applied from v{chosen['v']})", context=p.ref))
    log_event(db, p, None, f"Manager signature re-applied — content identical to v{chosen['v']} "
                           f"previously signed by {manager_name}.")
    _notify(db, p, uuid.UUID(mgr_sig["by"]),
            f"{p.ref}: {user.name} approved the terms of v{chosen['v']} at senior review — re-issued as v{v} "
            f"with your signature re-applied (content identical to what you signed).")

    # 3) senior signature + lock, exactly like senior-approve
    p.signatures = {**p.signatures, "senior": {"by": str(user.id), "at": iso(now())}}
    p.status = "signed"
    _record_signature(db, p, user, f"Proposal {p.ref} v{v}", "senior")
    log_event(db, p, user.id, f"Proposal approved & counter-signed by {user.name} (identity re-confirmed). Document locked.")
    requester = db.get(User, p.requested_by)
    pass_holder(db, p, requester, user, "signed proposal returned — ready to send to client")
    _notify(db, p, p.requested_by, f"{p.ref}: proposal signed by {user.name} — ready to send to client")
    db.commit()
    return _serialize(p)


# ---------- client sends / conversion ----------

@router.post("/{pid}/send-client")
def send_client(pid: uuid.UUID, body: ClientMailIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Email the signed proposal to the client (prototype sendClientEmail kind=proposal)."""
    p = _get(db, pid, user)
    _require_requester(p, user)
    require_status(p, "signed")
    attach_v = body.attach_version or _latest_version(p)["v"]
    emails.send_client(str(body.to), body.subject, body.body)
    p.status = "proposal_sent"
    p.proposal_sent_at = now()
    log_event(db, p, user.id,
              f'Email confirmed & sent to {body.to} — subject: "{body.subject}" (signed proposal PDF attached)',
              kind="email", meta={"to": str(body.to), "subject": body.subject, "attach_version": attach_v})
    log_event(db, p, None, "Email delivery logged. Awaiting client confirmation — established by uploading the client-signed proposal.")
    db.commit()
    return _serialize(p)


@router.post("/{pid}/mark-lost")
def mark_lost(pid: uuid.UUID, body: OptionalNoteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _get(db, pid, user)
    _require_requester(p, user)
    require_status(p, "proposal_sent")
    p.status = "lost"
    log_event(db, p, user.id, f"Marked LOST — {body.note or 'client did not proceed'}")
    pass_holder(db, p, None, user, "")
    db.commit()
    return _serialize(p)


@router.post("/{pid}/upload-signed")
def upload_signed(pid: uuid.UUID, file: UploadFile, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Client-signed proposal upload = the conversion gate (prototype uploadSignedProposal):
    prospect → client row, status flip, EL prepared, events written."""
    p = _get(db, pid, user)
    _require_requester(p, user)
    require_status(p, "proposal_sent")
    latest = _latest_version(p)
    f = store_upload(db, user, "proposal", p.id, file)
    if p.client_id:
        # existing-client engagement: no new client row — link and log
        client = db.get(Client, p.client_id)
        log_event(db, p, user.id, f"Client-signed proposal uploaded: {f.name}. "
                                  f"Additional engagement confirmed for {client.ref} — {client.name}; "
                                  f"no new client record created.")
    else:
        count = db.scalar(select(func.count()).select_from(Client).where(Client.tenant_id == user.tenant_id))
        client = Client(
            tenant_id=user.tenant_id, ref=f"CL-{count + 1:03d}", name=p.prospect.get("name"),
            contact=p.prospect, from_proposal=p.id, confirmation_basis="signed_upload",
        )
        db.add(client)
        db.flush()
        p.client_id = client.id
        log_event(db, p, user.id, f"Client-signed proposal uploaded: {f.name}. Client confirmation established — "
                                  f"prospect converted to CLIENT {client.ref}.")
    p.el = {"note": "", "advance_pct": 0, "signatory_id": None, "signature": None, "sent_at": None,
            "assignments": {}, "client_signed": {"file_id": str(f.id), "name": f.name, "at": iso(now())}}
    p.status = "el_staffing"
    log_event(db, p, None, "Engagement letter auto-prepared from the signed proposal. "
                           "Next: assign technical staff per activity, then route for senior signature.")
    db.commit()
    return {"client": {"id": client.id, "ref": client.ref, "name": client.name}, "proposal": _serialize(p),
            "services": [l["service"] for l in latest["data"]["lines"]]}


CONFIRMATION_BASES = {
    "email_approval": "client approval received by email",
    "message_approval": "client approval received by message (WhatsApp/SMS)",
    "verbal_instruction": "verbal instruction to proceed",
    "advance_payment": "advance payment received",
    "other": "other (see note)",
}


@router.post("/{pid}/confirm-unsigned")
def confirm_unsigned(
    pid: uuid.UUID,
    basis: str = Form(...),
    note: str = Form(""),
    evidence: list[UploadFile] = FileParam(default=[]),
    user: User = Depends(require_roles("Admin", "Manager")),
    db: Session = Depends(get_db),
):
    """Declared client confirmation — the client confirmed without returning a signed copy
    (email reply, WhatsApp, verbal go-ahead). Same conversion as upload-signed, but the
    audit trail records the declared basis + mandatory note, mirroring duties' declared-
    without-proof discipline. The signed EL becomes the binding acceptance record."""
    p = _get(db, pid, user)
    require_status(p, "proposal_sent")
    if basis not in CONFIRMATION_BASES:
        raise HTTPException(status_code=422, detail=f"basis must be one of {sorted(CONFIRMATION_BASES)}")
    if not note.strip():
        raise conflict("A note describing exactly how the client confirmed is mandatory")
    label = CONFIRMATION_BASES[basis]
    latest = _latest_version(p)

    stored = [store_upload(db, user, "proposal", p.id, f) for f in evidence]
    ev_txt = f' Evidence on file: {", ".join(f.name for f in stored)}.' if stored else ""
    if p.client_id:
        # existing-client engagement: no new client row — link and log
        client = db.get(Client, p.client_id)
        log_event(db, p, user.id,
                  f'CLIENT CONFIRMATION RECORDED WITHOUT SIGNED PROPOSAL — basis: {label}; note: "{note.strip()}".'
                  f"{ev_txt} Additional engagement confirmed for {client.ref} — {client.name}; "
                  f"no new client record created.")
    else:
        count = db.scalar(select(func.count()).select_from(Client).where(Client.tenant_id == user.tenant_id))
        client = Client(
            tenant_id=user.tenant_id, ref=f"CL-{count + 1:03d}", name=p.prospect.get("name"),
            contact=p.prospect, from_proposal=p.id, confirmation_basis=basis,
        )
        db.add(client)
        db.flush()
        p.client_id = client.id
        log_event(db, p, user.id,
                  f'CLIENT CONFIRMATION RECORDED WITHOUT SIGNED PROPOSAL — basis: {label}; note: "{note.strip()}".'
                  f"{ev_txt} Client confirmation established — prospect converted to CLIENT {client.ref}.")
    p.el = {"note": "", "advance_pct": 0, "signatory_id": None, "signature": None, "sent_at": None,
            "assignments": {},
            "client_confirmation": {"basis": basis, "label": label, "note": note.strip(), "at": iso(now()),
                                    "evidence": [{"file_id": str(f.id), "name": f.name} for f in stored]}}
    p.status = "el_staffing"
    log_event(db, p, None, "Engagement letter auto-prepared. Next: assign technical staff per activity, "
                           "then route for senior signature. The signed engagement letter will serve as "
                           "the binding client acceptance record.")
    db.commit()
    return {"client": {"id": client.id, "ref": client.ref, "name": client.name}, "proposal": _serialize(p),
            "services": [l["service"] for l in latest["data"]["lines"]]}


# ---------- EL flow ----------

@router.post("/{pid}/staff-activity")
def staff_activity(pid: uuid.UUID, body: StaffActivityIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _get(db, pid, user)
    _require_requester(p, user)
    require_status(p, "el_staffing")
    services = [l["service"] for l in _latest_version(p)["data"]["lines"]]
    if body.service not in services:
        raise HTTPException(status_code=422, detail=f"Unknown activity {body.service!r} — engaged services: {services}")
    staff = _user(db, user, body.staff_id)
    p.el = {**p.el, "assignments": {**p.el.get("assignments", {}), body.service: str(staff.id)}}
    log_event(db, p, user.id, f'Activity "{body.service}" assigned to {staff.name} (workload reviewed at selection)')
    _notify(db, p, staff.id, f'{p.ref} · {p.prospect.get("name")}: you were assigned the activity "{body.service}"')
    db.commit()
    return _serialize(p)


@router.post("/{pid}/el-plan")
def el_plan(pid: uuid.UUID, body: ELPlanIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _get(db, pid, user)
    _require_requester(p, user)
    require_status(p, "el_staffing")
    old = p.el.get("advance_pct", 0)
    if old != body.advance_pct:
        lbl = lambda x: "no advance (bill per proposal terms)" if x == 0 else f"{x}% advance"  # noqa: E731
        p.el = {**p.el, "advance_pct": body.advance_pct}
        log_event(db, p, user.id, f"Engagement letter payment plan changed: {lbl(old)} → {lbl(body.advance_pct)}")
        db.commit()
    return _serialize(p)


@router.post("/{pid}/el-note")
def el_note(pid: uuid.UUID, body: ELNoteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _get(db, pid, user)
    _require_requester(p, user)
    require_status(p, "el_staffing")
    old = (p.el.get("note") or "").strip()
    new = (body.note or "").strip()
    if old != new:
        p.el = {**p.el, "note": body.note}
        prefix = f'"{old}" → ' if old else ""
        log_event(db, p, user.id, f'Engagement letter special terms {"changed" if old else "added"}: {prefix}"{new or "—"}"')
        db.commit()
    return _serialize(p)


@router.post("/{pid}/el-route")
def el_route(pid: uuid.UUID, body: ELRouteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Route the EL for senior signature — blocked until every activity is staffed (prototype routeEL)."""
    p = _get(db, pid, user)
    _require_requester(p, user)
    require_status(p, "el_staffing")
    services = [l["service"] for l in _latest_version(p)["data"]["lines"]]
    unassigned = [s for s in services if s not in p.el.get("assignments", {})]
    if unassigned:
        raise conflict(f"Assign staff to every activity before routing — unassigned: {', '.join(unassigned)}")
    signatory = _user(db, user, body.signatory_id)
    if signatory.role != "Admin" or not signatory.signatory:
        raise conflict("The signatory must be an Admin with signing authority")
    p.el = {**p.el, "signatory_id": str(signatory.id)}
    p.status = "el_senior_review"
    p.last_rejection = None
    log_event(db, p, user.id, f"Engagement letter routed to {signatory.name} for signature "
                              f"(approve / reject only — no edits at this stage)")
    pass_holder(db, p, signatory, user, "engagement letter pending approval & signature")
    _notify(db, p, signatory.id, f"{p.ref}: engagement letter awaiting your signature")
    db.commit()
    return _serialize(p)


@router.post("/{pid}/el-sign")
def el_sign(pid: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Senior signs the EL — approve or reject only, no edits (prototype elApprove)."""
    p = _get(db, pid, user)
    if p.el.get("signatory_id") != str(user.id):
        raise conflict("Only the routed signatory can sign the engagement letter")
    require_holder(p, user)
    require_status(p, "el_senior_review")
    p.el = {**p.el, "signature": {"by": str(user.id), "at": iso(now())}}
    p.status = "el_approved"
    db.add(SignatureUse(tenant_id=p.tenant_id, user_id=user.id, document=f"Engagement Letter {p.ref}", context=p.ref))
    log_event(db, p, user.id, f"Engagement letter APPROVED & digitally signed by {user.name} (identity re-confirmed). "
                              f"Returned to manager to send to client.")
    requester = db.get(User, p.requested_by)
    pass_holder(db, p, requester, user, "signed EL returned — ready to email to client")
    _notify(db, p, p.requested_by, f"{p.ref}: engagement letter signed by {user.name} — ready to send to client")
    db.commit()
    return _serialize(p)


@router.post("/{pid}/el-reject")
def el_reject(pid: uuid.UUID, body: NoteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _get(db, pid, user)
    if p.el.get("signatory_id") != str(user.id):
        raise conflict("Only the routed signatory can reject the engagement letter")
    require_holder(p, user)
    require_status(p, "el_senior_review")
    p.status = "el_staffing"
    p.last_rejection = {"by": str(user.id), "at": iso(now()), "note": body.note, "stage": "el"}
    log_event(db, p, user.id, f'Engagement letter REJECTED by {user.name} — note: "{body.note}"')
    requester = db.get(User, p.requested_by)
    pass_holder(db, p, requester, user, "EL rejected — revise and re-route")
    _notify(db, p, p.requested_by, f'{p.ref}: engagement letter rejected — "{body.note}"')
    db.commit()
    return _serialize(p)


@router.post("/{pid}/el-send")
def el_send(pid: uuid.UUID, body: ClientMailIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Send the signed EL — completes Onboarding Part 1. Writes the payment schedule per the
    prototype's basis rules (sendClientEmail kind=el, lines 397-418)."""
    p = _get(db, pid, user)
    _require_requester(p, user)
    require_status(p, "el_approved")
    d = _latest_version(p)["data"]
    first_bill = sum(num(l.get("fee")) for l in d["lines"])
    advance_pct = p.el.get("advance_pct", 0)
    t0 = now()
    from datetime import timedelta

    pays: list[Payment] = []

    def mk(label: str, amount: float, due_at):
        pays.append(Payment(
            tenant_id=p.tenant_id, client_id=p.client_id, proposal_id=p.id,
            label=label, amount=round(amount, 2), due_at=due_at,
            receipts=[], events=[{"at": iso(t0), "by": "system", "text": "Expected payment created from engagement terms"}],
        ))

    if advance_pct > 0:
        adv = advance_pct / 100 * first_bill
        mk(f"Advance ({advance_pct}%) — first billing period", adv, t0)
        if advance_pct < 100:
            mk(f"Balance ({100 - advance_pct}%) — first billing period", first_bill - adv, t0 + timedelta(days=14))
        for l in d["lines"]:
            b = l.get("basis") or default_basis(l["service"])
            if b == "per month":
                mk(f"Recurring — {l['service']} (next month)", num(l.get("fee")), t0 + timedelta(days=30))
            if b == "per quarter":
                mk(f"Recurring — {l['service']} (next quarter)", num(l.get("fee")), t0 + timedelta(days=90))
    else:
        # no advance: first period per service, honoring the billing basis —
        # quarterly/annual/one-time fees billed in advance (due now), monthly in arrears (+30d)
        for l in d["lines"]:
            b = l.get("basis") or default_basis(l["service"])
            mk(f"First period — {l['service']} ({b})", num(l.get("fee")),
               t0 + timedelta(days=30) if b == "per month" else t0)

    for pay in pays:
        db.add(pay)

    emails.send_client(str(body.to), body.subject, body.body)
    p.el = {**p.el, "sent_at": iso(t0)}
    p.status = "el_sent"
    p.onboarding_completed_at = t0  # part 1 completion stamp — the process closes here
    log_event(db, p, user.id,
              f'Email confirmed & sent to {body.to} — subject: "{body.subject}" (signed engagement letter PDF attached)',
              kind="email", meta={"to": str(body.to), "subject": body.subject})
    log_event(db, p, None, f"Payment schedule generated ({len(pays)} expected payment(s)) "
                           f"— accountant notified; daily email + in-system reminders run until each receipt status is updated.")
    duration_ms = (t0 - (p.created_at if p.created_at.tzinfo else p.created_at.replace(tzinfo=timezone.utc))).total_seconds() * 1000
    log_event(db, p, None, f"PROCESS COMPLETE — proposal to engagement letter in {_fmt_dur(duration_ms)}. "
                           f"Audit trail sealed; performance report available to management. "
                           f"Client documentation proceeds in Onboarding.")
    pass_holder(db, p, None, user, "")

    # the Onboarding module takes over: one documentation relay per staffed activity
    from .onboardings import create_for_el_send
    onboardings = create_for_el_send(db, p, user)
    if onboardings:
        log_event(db, p, None, f"Onboarding started for {len(onboardings)} activit"
                               f"{'y' if len(onboardings) == 1 else 'ies'}: "
                               f"{', '.join(ob.service for ob in onboardings)}.")

    accountants = db.scalars(
        tenant_select(User, user).where(User.role == "Accountant", User.active.is_(True))
    ).all()
    client = db.get(Client, p.client_id)
    client_label = f"{client.ref} — {p.prospect.get('name')}" if client else p.prospect.get("name")
    if accountants:
        schedule = sorted(pays, key=lambda x: x.due_at)
        first = schedule[0]
        lines = [f"- {x.label}: AED {float(x.amount):,.0f} due {x.due_at:%d %b %Y}" for x in schedule]
        for acct in accountants:
            _notify(db, p, acct.id,
                    f"Engagement live: {client_label}. {len(pays)} scheduled payment(s) — first: {first.label} "
                    f"AED {float(first.amount):,.0f} due {first.due_at:%d %b %Y}. "
                    f"Review the invoice timeline in Payments.")
            emails._send(
                acct.email,
                f"Baton — engagement live: {p.prospect.get('name')} ({len(pays)} scheduled payment(s))",
                f"Good morning {acct.name},\n\n"
                f"The engagement letter for {client_label} was sent — the payment schedule is now live:\n\n"
                + "\n".join(lines) +
                "\n\nMark each invoice raised in the accounting software and record receipts as they arrive. "
                "Daily reminders run until every receipt status is updated.\n\n— Baton",
            )
        names = ", ".join(a.name for a in accountants)
        log_event(db, p, None, f"Accountant notification dispatched to {names} — invoice timeline "
                               f"({len(pays)} payment(s)) delivered by email and in-system notice.")
    else:
        log_event(db, p, None, "No in-house accountant to notify — invoice timeline available in Payments.")
    for svc, staff_id in p.el.get("assignments", {}).items():
        _notify(db, p, uuid.UUID(staff_id), f'{p.prospect.get("name")}: engagement letter sent — your activity "{svc}" is live.')
    db.commit()
    return {"proposal": _serialize(p),
            "payments": [{"id": x.id, "label": x.label, "amount": float(x.amount), "due_at": x.due_at} for x in pays]}


# ---------- performance report (process closes at el_sent) ----------

# the prototype's starsFor scale, exactly (baton-prototype.jsx)
STARS_SCALE = [
    {"max_days": 0.5, "stars": 5},
    {"max_days": 1, "stars": 4.5},
    {"max_days": 2, "stars": 4},
    {"max_days": 3, "stars": 3.5},
    {"max_days": 5, "stars": 3},
    {"max_days": 7, "stars": 2},
    {"max_days": None, "stars": 1},
]
STARS_SCALE_TEXT = "≤½ day ★5 · ≤1d ★4½ · ≤2d ★4 · ≤3d ★3½ · ≤5d ★3 · ≤7d ★2 · beyond ★1"


def stars_for(avg_days: float) -> float:
    for step in STARS_SCALE:
        if step["max_days"] is None or avg_days <= step["max_days"]:
            return step["stars"]
    return 1


@router.get("/{pid}/report")
def performance_report(pid: uuid.UUID, user: User = Depends(require_roles("Admin", "Manager")),
                       db: Session = Depends(get_db)):
    """Management-only performance report, available once the process completes at el_sent.
    Computed from holder_log; client-held rows (user_id null) are excluded from all
    employee figures — only internally-held periods count."""
    p = _get(db, pid, user)
    if p.status != "el_sent":
        raise conflict("The performance report is available once the process completes at EL sent")
    completed = p.onboarding_completed_at
    created = p.created_at
    total_ms = int((completed - created).total_seconds() * 1000)

    rows = db.scalars(
        select(HolderLog).where(HolderLog.proposal_id == p.id).order_by(HolderLog.started_at, HolderLog.id)
    ).all()
    per: dict = {}
    for h in rows:
        if h.user_id is None:  # client-held — excluded from employee figures
            continue
        ended = h.ended_at or completed
        dur = int((ended - h.started_at).total_seconds() * 1000)
        per.setdefault(h.user_id, []).append({
            "started": h.started_at, "ended": ended, "duration_ms": dur,
            "reason": h.reason or "responsibility held",
        })

    per_employee = []
    for uid_, holdings in per.items():
        u = db.get(User, uid_)
        total_held = sum(h["duration_ms"] for h in holdings)
        avg = total_held / len(holdings)
        per_employee.append({
            "user_id": uid_,
            "name": u.name if u else "—",
            "designation": u.designation if u else None,
            "role": u.role if u else None,
            "total_held_ms": total_held,
            "holdings": sorted(holdings, key=lambda h: -h["duration_ms"]),
            "avg_holding_ms": int(avg),
            "stars": stars_for(avg / 86400000),
        })
    per_employee.sort(key=lambda r: (-r["stars"], r["total_held_ms"]))

    return {
        "ref": p.ref, "prospect": p.prospect.get("name"),
        "created_at": created, "completed_at": completed, "total_ms": total_ms,
        "per_employee": per_employee,
        "stars_scale": STARS_SCALE, "stars_scale_text": STARS_SCALE_TEXT,
    }


# ---------- chat ----------

class ChatIn(BaseModel):
    text: str = Field(min_length=1)


@router.post("/{pid}/chat")
def chat(pid: uuid.UUID, body: ChatIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Any tenant participant posts to the matter's chat (prototype sendChat) — stored as a
    proposal_event kind=chat. Closed matters refuse new messages."""
    p = _get(db, pid, user)
    if p.status in CLOSED_STATUSES:
        raise conflict(f"Matter is closed ({p.status}) — the chat is read-only")
    log_event(db, p, user.id, f'Chat: "{body.text}"', kind="chat")
    other = p.requested_by if user.id == p.assigned_to else p.assigned_to
    if other and other != user.id:
        _notify(db, p, other, f"{p.ref}: new message from {user.name}")
    db.commit()
    return {"ok": True, "proposal": p.ref}


# ---------- workload (GET /users/workload) ----------

@workload_router.get("/workload")
def workload(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Per-user workload: active proposals held/assigned + live EL activities + open duties
    (prototype workloadOf)."""
    users = db.scalars(tenant_select(User, user).where(User.active.is_(True))).all()
    proposals = db.scalars(tenant_select(Proposal, user).where(Proposal.status.notin_(TERMINAL))).all()
    duty_counts = dict(db.execute(
        select(Duty.staff_id, func.count()).where(Duty.tenant_id == user.tenant_id, Duty.closed.is_(False))
        .group_by(Duty.staff_id)
    ).all())
    out = []
    for u in users:
        active = sum(1 for p in proposals if p.assigned_to == u.id)
        activities = sum(
            1 for p in db.scalars(tenant_select(Proposal, user)).all()
            for sid in (p.el or {}).get("assignments", {}).values() if sid == str(u.id)
        )
        out.append({
            "id": u.id, "name": u.name, "role": u.role, "designation": u.designation,
            "active_proposals": active, "el_activities": activities,
            "open_duties": duty_counts.get(u.id, 0),
            "workload": active + activities + duty_counts.get(u.id, 0),
        })
    return out
