import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..demo import deny_if_demo
from ..models import User
from ..security import (
    SUBSCRIPTION_INACTIVE_MSG,
    _user_from_credentials,
    bearer,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    subscription_blocked,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    must_reset: bool


class RefreshIn(BaseModel):
    refresh_token: str


class ResetPasswordIn(BaseModel):
    new_password: str
    token: str | None = None  # one-time set-password token (invite/reset link); else Authorization header


def _tokens(user: User) -> TokenOut:
    return TokenOut(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
        must_reset=user.must_reset,
    )


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    # email is unique per tenant, not globally — match on password among active accounts
    candidates = db.scalars(select(User).where(User.email == body.email, User.active.is_(True))).all()
    user = next((u for u in candidates if verify_password(body.password, u.password_hash)), None)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if subscription_blocked(db, user.tenant_id):
        raise HTTPException(status_code=403, detail=SUBSCRIPTION_INACTIVE_MSG)
    from ..workflow import now
    user.last_login_at = now()  # feeds the operator's activity counts only
    db.commit()
    return _tokens(user)


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    payload = decode_token(body.refresh_token, "refresh")
    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.active:
        raise HTTPException(status_code=401, detail="User not found or deactivated")
    return _tokens(user)


@router.post("/reset-password", response_model=TokenOut)
def reset_password(
    body: ResetPasswordIn,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
):
    if len(body.new_password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")
    if body.token:
        payload = decode_token(body.token, "set_password")
        user = db.get(User, uuid.UUID(payload["sub"]))
        if user is None or not user.active:
            raise HTTPException(status_code=401, detail="User not found or deactivated")
    else:
        # Bearer path deliberately bypasses the must_reset gate — this is the one allowed call.
        user = _user_from_credentials(credentials, db)
    # The demo logins are published; whoever changed one would lock out every later visitor.
    # Guarded on both paths — the set-password token is mailable, so it is no weaker.
    deny_if_demo(db, user, "Changing your password")
    user.password_hash = hash_password(body.new_password)
    user.must_reset = False
    db.commit()
    return _tokens(user)
