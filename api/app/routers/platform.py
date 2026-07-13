"""Platform Operator console — the developer's own login ABOVE all tenants.

Operators live in platform_operators (no tenant_id); their JWTs carry scope=platform and
are rejected by every tenant endpoint (and tenant tokens are rejected here). HARD RULE:
no endpoint in this router may return tenant business content — client names, documents,
trails. Firms are described by metadata, subscription state, and COUNTS only."""

import uuid
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import (Client, Duty, File, PlatformEvent, PlatformOperator, Proposal,
                      Subscription, Tenant, User)
from ..security import bearer, hash_password, verify_password
from .tenants import BootstrapIn, perform_bootstrap

router = APIRouter(prefix="/platform", tags=["platform"])

OPERATOR_TOKEN_TTL_HOURS = 12
SUBSCRIPTION_STATUSES = ("trial", "active", "suspended", "cancelled")
OPEN_PROPOSAL_EXCLUDED = ("el_sent", "lost", "onboarding_complete")


# ---------- operator auth (scope=platform) ----------

def create_operator_token(op: PlatformOperator) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    return pyjwt.encode({"sub": str(op.id), "scope": "platform", "type": "access",
                         "iat": now, "exp": now + timedelta(hours=OPERATOR_TOKEN_TTL_HOURS)},
                        s.JWT_SECRET, algorithm="HS256")


def _operator_from_credentials(credentials: HTTPAuthorizationCredentials | None,
                               db: Session) -> PlatformOperator:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = pyjwt.decode(credentials.credentials, get_settings().JWT_SECRET, algorithms=["HS256"])
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if payload.get("scope") != "platform" or payload.get("type") != "access":
        # tenant tokens are NEVER valid on operator endpoints
        raise HTTPException(status_code=401, detail="A platform operator token is required")
    op = db.get(PlatformOperator, uuid.UUID(payload["sub"]))
    if op is None or not op.active:
        raise HTTPException(status_code=401, detail="Operator not found or deactivated")
    return op


def current_operator(credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                     db: Session = Depends(get_db)) -> PlatformOperator:
    op = _operator_from_credentials(credentials, db)
    if op.must_reset:
        raise HTTPException(status_code=403, detail={"code": "MUST_RESET", "message": "Password reset required"})
    return op


class OperatorLoginIn(BaseModel):
    email: EmailStr
    password: str


class OperatorResetIn(BaseModel):
    new_password: str = Field(min_length=8)


@router.post("/auth/login")
def operator_login(body: OperatorLoginIn, db: Session = Depends(get_db)):
    op = db.scalar(select(PlatformOperator).where(PlatformOperator.email == str(body.email),
                                                  PlatformOperator.active.is_(True)))
    if op is None or not verify_password(body.password, op.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return {"access_token": create_operator_token(op), "token_type": "bearer",
            "must_reset": op.must_reset, "email": op.email}


@router.post("/auth/reset-password")
def operator_reset_password(body: OperatorResetIn,
                            credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                            db: Session = Depends(get_db)):
    op = _operator_from_credentials(credentials, db)  # allowed while must_reset
    op.password_hash = hash_password(body.new_password)
    op.must_reset = False
    db.add(PlatformEvent(operator_id=op.id, text_=f"Operator {op.email} set a new password"))
    db.commit()
    return {"access_token": create_operator_token(op), "token_type": "bearer",
            "must_reset": False, "email": op.email}


# ---------- firm health (COUNTS ONLY — never tenant business content) ----------

def _serialize_subscription(sub: Subscription | None) -> dict | None:
    if sub is None:
        return None
    return {"id": sub.id, "plan_name": sub.plan_name, "status": sub.status,
            "seats_limit": sub.seats_limit, "started_at": sub.started_at,
            "current_period_end": sub.current_period_end, "notes": sub.notes}


def _firm_summary(db: Session, t: Tenant) -> dict:
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    seats_used = db.scalar(select(func.count()).select_from(User).where(
        User.tenant_id == t.id, User.active.is_(True)))
    active_7d = db.scalar(select(func.count()).select_from(User).where(
        User.tenant_id == t.id, User.active.is_(True), User.last_login_at >= week_ago))
    open_props = db.scalar(select(func.count()).select_from(Proposal).where(
        Proposal.tenant_id == t.id, Proposal.status.notin_(OPEN_PROPOSAL_EXCLUDED)))
    open_duties = db.scalar(select(func.count()).select_from(Duty).where(
        Duty.tenant_id == t.id, Duty.closed.is_(False)))
    clients_n = db.scalar(select(func.count()).select_from(Client).where(Client.tenant_id == t.id))
    storage = db.scalar(select(func.coalesce(func.sum(File.size), 0)).where(File.tenant_id == t.id))
    # the VAT engine is a removable module — count via a table-existence guard, no import
    filings_in_progress = 0
    if db.execute(text("SELECT to_regclass('vat_filings')")).scalar() is not None:
        filings_in_progress = db.execute(text(
            "SELECT count(*) FROM vat_filings WHERE tenant_id = :t AND status != 'complete'"),
            {"t": str(t.id)}).scalar()
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == t.id))
    return {
        "tenant_id": t.id, "name": t.name, "short": t.short, "email": t.email,
        "created_at": t.created_at,
        "subscription": _serialize_subscription(sub),
        "seats_used": seats_used,
        "stats": {"active_users_7d": active_7d, "open_proposals": open_props,
                  "open_duties": open_duties, "filings_in_progress": filings_in_progress,
                  "clients": clients_n, "storage_bytes": int(storage or 0)},
    }


@router.get("/firms")
def list_firms(op: PlatformOperator = Depends(current_operator), db: Session = Depends(get_db)):
    tenants = db.scalars(select(Tenant).order_by(Tenant.created_at)).all()
    return [_firm_summary(db, t) for t in tenants]


@router.get("/firms/{tenant_id}")
def firm_detail(tenant_id: uuid.UUID, op: PlatformOperator = Depends(current_operator),
                db: Session = Depends(get_db)):
    t = db.get(Tenant, tenant_id)
    if t is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    out = _firm_summary(db, t)
    events = db.scalars(select(PlatformEvent).where(PlatformEvent.tenant_id == t.id)
                        .order_by(PlatformEvent.at.desc(), PlatformEvent.id.desc()).limit(100)).all()
    out["events"] = [{"at": e.at, "operator_id": e.operator_id, "text": e.text_} for e in events]
    return out


# ---------- subscription CRUD (every change logged, note mandatory) ----------

class SubscriptionPatchIn(BaseModel):
    plan_name: str | None = None
    status: str | None = None
    seats_limit: int | None = Field(default=None, ge=1)
    current_period_end: datetime | None = None
    note: str = Field(min_length=1)  # every change is on the platform log with a reason


@router.patch("/firms/{tenant_id}/subscription")
def update_subscription(tenant_id: uuid.UUID, body: SubscriptionPatchIn,
                        op: PlatformOperator = Depends(current_operator),
                        db: Session = Depends(get_db)):
    t = db.get(Tenant, tenant_id)
    if t is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    if sub is None:  # legacy tenant — the operator formalizes it now
        sub = Subscription(tenant_id=tenant_id, plan_name="Trial", status="trial", seats_limit=10)
        db.add(sub)
        db.flush()
    if body.status is not None and body.status not in SUBSCRIPTION_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {SUBSCRIPTION_STATUSES}")
    changes = []
    if body.plan_name is not None and body.plan_name != sub.plan_name:
        changes.append(f"plan {sub.plan_name}→{body.plan_name}")
        sub.plan_name = body.plan_name
    if body.status is not None and body.status != sub.status:
        changes.append(f"status {sub.status}→{body.status}")
        sub.status = body.status
    if body.seats_limit is not None and body.seats_limit != sub.seats_limit:
        changes.append(f"seats {sub.seats_limit}→{body.seats_limit}")
        sub.seats_limit = body.seats_limit
    if body.current_period_end is not None and body.current_period_end != sub.current_period_end:
        changes.append(f"period end →{body.current_period_end:%d %b %Y}")
        sub.current_period_end = body.current_period_end
    if not changes:
        raise HTTPException(status_code=409, detail="No change submitted")
    sub.notes = body.note.strip()
    db.add(PlatformEvent(operator_id=op.id, tenant_id=tenant_id,
                         text_=f"{t.name}: subscription updated by {op.email} — "
                               f"{'; '.join(changes)} — note: \"{body.note.strip()}\""))
    db.commit()
    return {"tenant_id": tenant_id, "subscription": _serialize_subscription(sub)}


# ---------- create firm (operator-authed bootstrap; BOOTSTRAP_KEY never leaves the server) ----------

class FirmSubscriptionIn(BaseModel):
    plan_name: str = "Trial"
    status: str = "trial"  # a new firm starts trial or active — never suspended/cancelled
    seats_limit: int = Field(default=10, ge=1)
    current_period_end: datetime | None = None  # empty + active = open-ended (never expires)


class FirmCreateIn(BootstrapIn):
    subscription: FirmSubscriptionIn = Field(default_factory=FirmSubscriptionIn)


@router.post("/firms", status_code=201)
def create_firm(body: FirmCreateIn, op: PlatformOperator = Depends(current_operator),
                db: Session = Depends(get_db)):
    """Create a firm from the operator console. Reuses the bootstrap logic in-process (the
    public /tenants/bootstrap key gate is not involved), then applies the operator's chosen
    subscription in the same transaction. Temp passwords are returned ONCE — the console
    must surface them, since email may not be configured."""
    if body.subscription.status not in ("trial", "active"):
        raise HTTPException(status_code=422, detail="a new firm starts as trial or active")
    boot = perform_bootstrap(body, db)
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == boot.tenant_id))
    s = body.subscription
    sub.plan_name = s.plan_name
    sub.status = s.status
    sub.seats_limit = max(s.seats_limit, len(body.employees))  # never below the head-count
    if s.current_period_end is not None:
        sub.current_period_end = s.current_period_end
    elif s.status == "active":
        sub.current_period_end = None  # open-ended until the operator sets a period
    db.add(PlatformEvent(operator_id=op.id, tenant_id=boot.tenant_id,
                         text_=f"{body.firm.name}: firm created by {op.email} — "
                               f"plan {sub.plan_name}, {sub.status}, seats {sub.seats_limit}, "
                               f"{len(boot.users)} user(s) invited"))
    db.commit()
    return {"tenant_id": boot.tenant_id,
            "users": [u.model_dump() for u in boot.users],
            "subscription": _serialize_subscription(sub)}


@router.get("/log")
def platform_log(op: PlatformOperator = Depends(current_operator), db: Session = Depends(get_db)):
    events = db.scalars(select(PlatformEvent)
                        .order_by(PlatformEvent.at.desc(), PlatformEvent.id.desc()).limit(500)).all()
    tenant_names = {t.id: t.name for t in db.scalars(select(Tenant)).all()}
    return [{"at": e.at, "operator_id": e.operator_id, "tenant_id": e.tenant_id,
             "tenant_name": tenant_names.get(e.tenant_id), "text": e.text_} for e in events]
