import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import Subscription, User

bearer = HTTPBearer(auto_error=False)

SUBSCRIPTION_INACTIVE_MSG = "Your firm's Baton subscription is inactive — contact your administrator"
SUBSCRIPTION_GRACE_DAYS = 7


def subscription_blocked(db: Session, tenant_id) -> bool:
    """suspended/cancelled, or expired past the grace window → blocked. Tenants without a
    subscription row (legacy) are never blocked."""
    from sqlalchemy import select
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    if sub is None:
        return False
    if sub.status in ("suspended", "cancelled"):
        return True
    if sub.current_period_end is not None:
        end = sub.current_period_end if sub.current_period_end.tzinfo else \
            sub.current_period_end.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > end + timedelta(days=SUBSCRIPTION_GRACE_DAYS):
            return True
    return False


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def _create_token(user: User, kind: str, ttl: timedelta) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "type": kind,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, s.JWT_SECRET, algorithm="HS256")


def create_access_token(user: User) -> str:
    return _create_token(user, "access", timedelta(minutes=get_settings().ACCESS_TOKEN_TTL_MIN))


def create_refresh_token(user: User) -> str:
    return _create_token(user, "refresh", timedelta(days=get_settings().REFRESH_TOKEN_TTL_DAYS))


def create_set_password_token(user: User) -> str:
    return _create_token(user, "set_password", timedelta(hours=get_settings().SET_PASSWORD_TOKEN_TTL_HOURS))


def decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, get_settings().JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if payload.get("type") != expected_type:
        raise HTTPException(status_code=401, detail="Wrong token type")
    return payload


def _user_from_credentials(
    credentials: HTTPAuthorizationCredentials | None, db: Session, expected_type: str = "access"
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(credentials.credentials, expected_type)
    if payload.get("scope") == "platform":
        # platform operator tokens are NEVER valid on tenant endpoints
        raise HTTPException(status_code=401, detail="Platform operator tokens are not valid for tenant endpoints")
    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.active:
        raise HTTPException(status_code=401, detail="User not found or deactivated")
    return user


def current_user_allow_reset(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    """Authenticated user, WITHOUT the must_reset gate — only /auth/reset-password uses this."""
    return _user_from_credentials(credentials, db)


def current_user(
    user: User = Depends(current_user_allow_reset),
    db: Session = Depends(get_db),
) -> User:
    """Authenticated user. While must_reset is true every call except reset-password is
    refused; an inactive subscription (suspended/cancelled or >grace past expiry) turns
    every tenant API call into a 402."""
    if user.must_reset:
        raise HTTPException(status_code=403, detail={"code": "MUST_RESET", "message": "Password reset required"})
    if subscription_blocked(db, user.tenant_id):
        raise HTTPException(status_code=402, detail=SUBSCRIPTION_INACTIVE_MSG)
    return user


def require_roles(*roles: str):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user

    return dep
