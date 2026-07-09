import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import emails
from ..config import get_settings
from ..db import get_db
from ..models import Duty, Tenant, User
from ..security import create_set_password_token, hash_password

router = APIRouter(prefix="/tenants", tags=["tenants"])

ROLES = ("Admin", "Manager", "Staff", "Accountant")


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


@router.post("/bootstrap", response_model=BootstrapOut, status_code=201)
def bootstrap(body: BootstrapIn, db: Session = Depends(get_db)):
    """One-time firm setup from the setup wizard. Returns each user's temp password ONCE —
    they are stored only as bcrypt hashes."""
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
            password_hash=hash_password(temp_password),
            must_reset=True,
        )
        db.add(user)
        db.flush()
        for d in emp.duties:
            db.add(
                Duty(
                    tenant_id=tenant.id,
                    staff_id=user.id,
                    client_name=d.client_name,
                    service=d.service,
                    kind=d.kind,
                    cadence=d.cadence,
                    next_due=d.next_due,
                    contact=d.contact,
                )
            )
        link = f"{get_settings().FRONTEND_ORIGIN}/set-password?token={create_set_password_token(user)}"
        emails.send_invite(user.email, user.name, tenant.short, link, temp_password)
        out_users.append(
            BootstrapUserOut(id=user.id, name=user.name, email=user.email, role=user.role, temp_password=temp_password)
        )

    db.commit()
    return BootstrapOut(tenant_id=tenant.id, users=out_users)
