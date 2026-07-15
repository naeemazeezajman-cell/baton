"""Demo-tenant guardrails.

"Baton Demo Co" (see demo_seed.py) is a real tenant whose credentials are published, so
anyone on the internet can drive it. Two guardrails follow from that, and NOTHING else:

  1. Outbound email is suppressed (emails._send) — a visitor typing a stranger's address
     into a proposal send must not turn the firm's verified ACS sender into a relay.
  2. Credentials and the roster are frozen (deny_if_demo) — the published logins must keep
     working for the next visitor, so nobody can rotate a password or deactivate an account.

The flag deliberately does NOT touch data visibility. A demo user reaches other tenants'
rows exactly as much as any other user does: not at all, via the same tenant_id scoping in
tenancy.py. There is no branch anywhere that widens a query because a tenant is demo, and
test_demo_tenant.py asserts that across every resource type.
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import Tenant, User


def is_demo_tenant(db: Session, tenant_id) -> bool:
    """True when tenant_id is the showcase firm. Tolerates a missing/None tenant (False):
    the caller is an email send that must not crash on a stale id."""
    if tenant_id is None:
        return False
    t = db.get(Tenant, tenant_id)
    return bool(t and t.demo)


def is_demo_user(db: Session, user: User) -> bool:
    return is_demo_tenant(db, user.tenant_id)


def deny_if_demo(db: Session, user: User, what: str) -> None:
    """Refuse an action that would break the demo for the next visitor. 403 with a message
    written to be read by an interviewer poking at the UI, not by an operator."""
    if is_demo_user(db, user):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "DEMO_READ_ONLY",
                "message": f"{what} is disabled in the Baton demo firm — the shared demo "
                           f"logins have to keep working for the next visitor. Everything "
                           f"else in the product is yours to explore.",
            },
        )
