import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import emails
from ..config import get_settings
from ..db import get_db
from ..models import Client, Duty, DutyEvent, Subscription, Tenant, User
from ..security import create_set_password_token, hash_password

router = APIRouter(prefix="/tenants", tags=["tenants"])

ROLES = ("Admin", "Manager", "Staff", "Accountant")
PRE_BATON_BASIS = "pre-existing relationship (pre-Baton deployment)"


def _norm_client(s: str | None) -> str:
    return " ".join((s or "").split()).lower()


class FirmIn(BaseModel):
    name: str
    short: str
    address: str | None = None
    trn: str | None = None
    phone: str | None = None
    email: EmailStr
    accent: str | None = None


class DutyIn(BaseModel):
    client_name: str
    service: str
    kind: str
    cadence: str
    next_due: datetime
    contact: dict | None = None


class EmployeeIn(BaseModel):
    name: str
    designation: str | None = None
    email: EmailStr
    role: str
    signatory: bool = False
    sig: dict | None = None  # signature specimen: {type: "typed", text} or {type: "image", url: dataURL}
    duties: list[DutyIn] = Field(default_factory=list)


class BootstrapIn(BaseModel):
    firm: FirmIn
    services: list[str] = Field(default_factory=list)
    templates: dict = Field(default_factory=dict)
    employees: list[EmployeeIn]


class BootstrapUserOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    role: str
    temp_password: str


class BootstrapOut(BaseModel):
    tenant_id: uuid.UUID
    users: list[BootstrapUserOut]


def perform_bootstrap(body: BootstrapIn, db: Session) -> BootstrapOut:
    """Shared firm-creation logic: tenant + trial subscription + users (temp passwords
    returned ONCE, stored only as bcrypt hashes) + pre-Baton duties/clients. Flushes but
    does NOT commit — the caller owns the transaction (the operator console adjusts the
    subscription in the same transaction before committing)."""
    if db.scalar(select(Tenant).where(Tenant.email == str(body.firm.email))):
        raise HTTPException(status_code=409, detail="A tenant with this email already exists")
    for emp in body.employees:
        if emp.role not in ROLES:
            raise HTTPException(status_code=422, detail=f"Unknown role {emp.role!r} for {emp.email}")
    seen = {e.email.lower() for e in body.employees}
    if len(seen) != len(body.employees):
        raise HTTPException(status_code=422, detail="Duplicate employee emails in payload")

    tenant = Tenant(
        name=body.firm.name,
        short=body.firm.short,
        address=body.firm.address,
        trn=body.firm.trn,
        phone=body.firm.phone,
        email=str(body.firm.email),
        accent=body.firm.accent or "#14606B",
        services=body.services,
        templates=body.templates,
    )
    db.add(tenant)
    db.flush()

    # every new firm starts on a 30-day trial — the platform operator formalizes it later.
    # Seats: env default, but never below the deploying head-count (bootstrap must not self-block).
    default_seats = int(os.getenv("DEFAULT_TRIAL_SEATS", "10"))
    db.add(Subscription(tenant_id=tenant.id, plan_name="Trial", status="trial",
                        seats_limit=max(default_seats, len(body.employees)),
                        current_period_end=datetime.now(timezone.utc) + timedelta(days=30)))

    # pre-Baton duty clients become first-class client rows: one per DISTINCT client name
    # (case-insensitive, whitespace-collapsed) across every employee's pre-existing duties
    clients_by_norm: dict[str, Client] = {}

    def client_for(d: DutyIn) -> Client:
        key = _norm_client(d.client_name)
        c = clients_by_norm.get(key)
        if c is None:
            c = Client(tenant_id=tenant.id, ref=f"CL-{len(clients_by_norm) + 1:03d}",
                       name=" ".join(d.client_name.split()), contact=d.contact,
                       origin="pre_baton", confirmation_basis=PRE_BATON_BASIS)
            db.add(c)
            db.flush()
            clients_by_norm[key] = c
        return c

    out_users: list[BootstrapUserOut] = []
    for emp in body.employees:
        temp_password = secrets.token_urlsafe(9)
        user = User(
            tenant_id=tenant.id,
            name=emp.name,
            designation=emp.designation,
            email=str(emp.email),
            role=emp.role,
            signatory=emp.signatory,
            sig_specimen=emp.sig,
            password_hash=hash_password(temp_password),
            must_reset=True,
        )
        db.add(user)
        db.flush()
        for d in emp.duties:
            c = client_for(d)
            duty = Duty(
                tenant_id=tenant.id,
                staff_id=user.id,
                client_name=c.name,
                client_id=c.id,
                service=d.service,
                kind=d.kind,
                cadence=d.cadence,
                next_due=d.next_due,
                contact=d.contact,
            )
            db.add(duty)
            db.flush()
            # same client name with a different contact: the client record keeps the FIRST
            # contact; this duty's own contact stays on the duty — noted on the trail
            if d.contact and c.contact and d.contact != c.contact:
                db.add(DutyEvent(tenant_id=tenant.id, duty_id=duty.id, by_user=None,
                                 text_=f"Linked to client {c.ref} — the client record keeps the contact "
                                       f"from the first registered duty; this duty's own contact "
                                       f"({d.contact.get('email') or d.contact.get('name') or 'on record'}) "
                                       f"is retained on the duty."))
        link = f"{get_settings().FRONTEND_ORIGIN}/set-password?token={create_set_password_token(user)}"
        emails.send_invite(user.email, user.name, tenant.short, link, temp_password)
        out_users.append(
            BootstrapUserOut(id=user.id, name=user.name, email=user.email, role=user.role, temp_password=temp_password)
        )

    return BootstrapOut(tenant_id=tenant.id, users=out_users)


@router.post("/bootstrap", response_model=BootstrapOut, status_code=201)
def bootstrap(body: BootstrapIn, db: Session = Depends(get_db),
              x_bootstrap_key: str | None = Header(default=None)):
    """One-time firm setup from the setup wizard. When env BOOTSTRAP_KEY is set (always, in
    production) the X-Bootstrap-Key header must match — otherwise anyone could create firms.
    The operator console creates firms via POST /platform/firms (operator JWT) instead."""
    required = os.getenv("BOOTSTRAP_KEY", "")
    if required and not secrets.compare_digest(x_bootstrap_key or "", required):
        raise HTTPException(status_code=403, detail="Invalid or missing X-Bootstrap-Key")
    out = perform_bootstrap(body, db)
    db.commit()
    return out


# ---------- firm settings (STRUCTURE.md: tenants.py — bootstrap, firm settings, catalog) ----------

from ..security import current_user, require_roles  # noqa: E402
from ..tenancy import get_scoped_or_404  # noqa: E402


class FirmUpdateIn(BaseModel):
    name: str | None = None
    short: str | None = None
    address: str | None = None
    trn: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    accent: str | None = None
    services: list[str] | None = None
    templates: dict | None = None


def _firm_out(t: Tenant) -> dict:
    return {"id": t.id, "name": t.name, "short": t.short, "address": t.address, "trn": t.trn,
            "phone": t.phone, "email": t.email, "accent": t.accent, "services": t.services,
            "templates": t.templates, "created_at": t.created_at}


@router.get("/me")
def get_firm(user=Depends(current_user), db: Session = Depends(get_db)):
    out = _firm_out(db.get(Tenant, user.tenant_id))
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == user.tenant_id))
    if sub:
        days_left = None
        if sub.current_period_end is not None:
            end = sub.current_period_end if sub.current_period_end.tzinfo else \
                sub.current_period_end.replace(tzinfo=timezone.utc)
            days_left = (end - datetime.now(timezone.utc)).days
        seats_used = db.scalar(select(func.count()).select_from(User).where(
            User.tenant_id == user.tenant_id, User.active.is_(True)))
        out["subscription"] = {"plan_name": sub.plan_name, "status": sub.status,
                               "seats_limit": sub.seats_limit, "seats_used": seats_used,
                               "current_period_end": sub.current_period_end,
                               "days_left": days_left,
                               "expiring_soon": days_left is not None and days_left <= 14
                               and sub.status in ("trial", "active")}
    else:
        out["subscription"] = None
    return out


@router.patch("/me")
def update_firm(body: FirmUpdateIn, user=Depends(require_roles("Admin")), db: Session = Depends(get_db)):
    t = db.get(Tenant, user.tenant_id)
    for field in ("name", "short", "address", "trn", "phone", "accent", "services", "templates"):
        value = getattr(body, field)
        if value is not None:
            setattr(t, field, value)
    if body.email is not None:
        t.email = str(body.email)
    db.commit()
    return _firm_out(t)
