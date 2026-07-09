import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import emails
from ..config import get_settings
from ..db import get_db
from ..models import Tenant, User
from ..security import create_set_password_token, current_user, hash_password, require_roles
from ..tenancy import get_scoped_or_404, tenant_select

router = APIRouter(prefix="/users", tags=["users"])

ROLES = ("Admin", "Manager", "Staff", "Accountant")


class UserOut(BaseModel):
    id: uuid.UUID
    name: str
    designation: str | None
    email: str
    role: str
    signatory: bool
    sig_specimen: dict | None = None
    must_reset: bool
    active: bool

    model_config = {"from_attributes": True}


class UserCreateIn(BaseModel):
    name: str
    designation: str | None = None
    email: EmailStr
    role: str
    signatory: bool = False


class UserUpdateIn(BaseModel):
    name: str | None = None
    designation: str | None = None
    role: str | None = None
    signatory: bool | None = None


def _validate_role(role: str):
    if role not in ROLES:
        raise HTTPException(status_code=422, detail=f"Role must be one of {ROLES}")


@router.get("", response_model=list[UserOut])
def list_users(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(tenant_select(User, user).order_by(User.created_at)).all()


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return get_scoped_or_404(db, User, user_id, user)


@router.post("", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreateIn,
    admin: User = Depends(require_roles("Admin")),
    db: Session = Depends(get_db),
):
    _validate_role(body.role)
    exists = db.scalar(tenant_select(User, admin).where(User.email == str(body.email)))
    if exists:
        raise HTTPException(status_code=409, detail="A user with this email already exists")
    temp_password = secrets.token_urlsafe(9)
    new = User(
        tenant_id=admin.tenant_id,
        name=body.name,
        designation=body.designation,
        email=str(body.email),
        role=body.role,
        signatory=body.signatory,
        password_hash=hash_password(temp_password),
        must_reset=True,
    )
    db.add(new)
    db.flush()
    tenant = db.get(Tenant, admin.tenant_id)
    link = f"{get_settings().FRONTEND_ORIGIN}/set-password?token={create_set_password_token(new)}"
    emails.send_invite(new.email, new.name, tenant.short, link, temp_password)
    db.commit()
    return new


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    body: UserUpdateIn,
    admin: User = Depends(require_roles("Admin")),
    db: Session = Depends(get_db),
):
    target = get_scoped_or_404(db, User, user_id, admin)
    if body.role is not None:
        _validate_role(body.role)
        target.role = body.role
    if body.name is not None:
        target.name = body.name
    if body.designation is not None:
        target.designation = body.designation
    if body.signatory is not None:
        target.signatory = body.signatory
    db.commit()
    return target


@router.post("/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(
    user_id: uuid.UUID,
    admin: User = Depends(require_roles("Admin")),
    db: Session = Depends(get_db),
):
    target = get_scoped_or_404(db, User, user_id, admin)
    if target.id == admin.id:
        raise HTTPException(status_code=409, detail="You cannot deactivate your own account")
    target.active = False
    db.commit()
    return target


@router.post("/{user_id}/resend-invite", status_code=204)
def resend_invite(
    user_id: uuid.UUID,
    admin: User = Depends(require_roles("Admin")),
    db: Session = Depends(get_db),
):
    target = get_scoped_or_404(db, User, user_id, admin)
    if not target.must_reset:
        raise HTTPException(status_code=409, detail="User has already set their password")
    temp_password = secrets.token_urlsafe(9)
    target.password_hash = hash_password(temp_password)
    tenant = db.get(Tenant, admin.tenant_id)
    link = f"{get_settings().FRONTEND_ORIGIN}/set-password?token={create_set_password_token(target)}"
    emails.send_invite(target.email, target.name, tenant.short, link, temp_password)
    db.commit()
