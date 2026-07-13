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


def _client_registry(db: Session, tenant_id):
    """client_for(duty): one client row per distinct client name (case-insensitive,
    whitespace-collapsed), resuming the CL-xxx sequence from what the tenant already has."""
    clients_by_norm: dict[str, Client] = {
        _norm_client(c.name): c
        for c in db.scalars(select(Client).where(Client.tenant_id == tenant_id))
    }
    counter = len(clients_by_norm)

    def client_for(d: DutyIn) -> Client:
        nonlocal counter
        key = _norm_client(d.client_name)
        c = clients_by_norm.get(key)
        if c is None:
            counter += 1
            c = Client(tenant_id=tenant_id, ref=f"CL-{counter:03d}",
                       name=" ".join(d.client_name.split()), contact=d.contact,
                       origin="pre_baton", confirmation_basis=PRE_BATON_BASIS)
            db.add(c)
            db.flush()
            clients_by_norm[key] = c
        return c

    return client_for


def _add_pre_baton_duty(db: Session, tenant_id, user: User, d: DutyIn, client_for) -> None:
    c = client_for(d)
    duty = Duty(
        tenant_id=tenant_id,
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
        db.add(DutyEvent(tenant_id=tenant_id, duty_id=duty.id, by_user=None,
                         text_=f"Linked to client {c.ref} — the client record keeps the contact "
                               f"from the first registered duty; this duty's own contact "
                               f"({d.contact.get('email') or d.contact.get('name') or 'on record'}) "
                               f"is retained on the duty."))


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

    client_for = _client_registry(db, tenant.id)
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
            _add_pre_baton_duty(db, tenant.id, user, d, client_for)
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


# ---------- first-login setup (operator-created firm, Admin self-serves the wizard) ----------

class CompleteSetupIn(BaseModel):
    firm: FirmIn
    services: list[str] = Field(min_length=1)  # "no activities configured" = setup incomplete
    templates: dict = Field(default_factory=dict)
    employees: list[EmployeeIn]  # includes the seed Admin (matched by email, updated in place)


@router.post("/complete-setup", status_code=201)
def complete_setup(body: CompleteSetupIn, admin: User = Depends(require_roles("Admin")),
                   db: Session = Depends(get_db)):
    """One-shot wizard completion for a firm the platform operator created with only the
    seed Admin. Applies firm details + activity catalog, updates employees whose email
    already exists (the admin — role and password untouched), creates the rest with
    one-time temp passwords, and registers pre-Baton duties. Refused once services are
    configured — from then on Firm settings / Employees & roles own these changes."""
    t = db.get(Tenant, admin.tenant_id)
    if t.services:
        raise HTTPException(status_code=409,
                            detail="Setup is already complete — use Firm settings and Employees & roles")
    for emp in body.employees:
        if emp.role not in ROLES:
            raise HTTPException(status_code=422, detail=f"Unknown role {emp.role!r} for {emp.email}")
    lowered = [e.email.lower() for e in body.employees]
    if len(set(lowered)) != len(lowered):
        raise HTTPException(status_code=422, detail="Duplicate employee emails in payload")

    # the operator entered only name/short/email — the admin completes (and may correct) the rest
    t.name, t.short = body.firm.name, body.firm.short
    t.address, t.trn, t.phone = body.firm.address, body.firm.trn, body.firm.phone
    t.email = str(body.firm.email)
    if body.firm.accent:
        t.accent = body.firm.accent
    t.services = body.services
    t.templates = body.templates

    existing = {u.email.lower(): u for u in db.scalars(select(User).where(User.tenant_id == t.id))}
    new_emps = [e for e in body.employees if e.email.lower() not in existing]

    # seat enforcement — same rule as POST /users, checked up front for the whole batch
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == t.id))
    if sub and sub.seats_limit:
        active_count = db.scalar(select(func.count()).select_from(User).where(
            User.tenant_id == t.id, User.active.is_(True)))
        if active_count + len(new_emps) > sub.seats_limit:
            raise HTTPException(status_code=409,
                                detail=f"Seat limit reached — your plan allows {sub.seats_limit} active "
                                       f"user(s) and this setup needs {active_count + len(new_emps)}. "
                                       f"Remove employees or ask the platform operator to raise the limit.")

    client_for = _client_registry(db, t.id)
    out_users: list[BootstrapUserOut] = []
    for emp in body.employees:
        user = existing.get(emp.email.lower())
        if user is None:
            temp_password = secrets.token_urlsafe(9)
            user = User(tenant_id=t.id, name=emp.name, designation=emp.designation,
                        email=str(emp.email), role=emp.role, signatory=emp.signatory,
                        sig_specimen=emp.sig, password_hash=hash_password(temp_password),
                        must_reset=True)
            db.add(user)
            db.flush()
            link = f"{get_settings().FRONTEND_ORIGIN}/set-password?token={create_set_password_token(user)}"
            emails.send_invite(user.email, user.name, t.short, link, temp_password)
            out_users.append(BootstrapUserOut(id=user.id, name=user.name, email=user.email,
                                              role=user.role, temp_password=temp_password))
        else:
            # the seed admin finishing their own row: profile updates; role/password stay
            user.name = emp.name
            user.designation = emp.designation or user.designation
            user.signatory = emp.signatory
            if emp.sig is not None:
                user.sig_specimen = emp.sig
        for d in emp.duties:
            _add_pre_baton_duty(db, t.id, user, d, client_for)

    db.commit()
    return {"tenant_id": t.id, "users": out_users}
