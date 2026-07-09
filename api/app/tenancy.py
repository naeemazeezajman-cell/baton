"""Tenant scoping helpers — EVERY query must be filtered by current_user.tenant_id."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import User


def tenant_select(model, user: User):
    """Base SELECT for a tenant-owned model, scoped to the caller's tenant."""
    return select(model).where(model.tenant_id == user.tenant_id)


def get_scoped_or_404(db: Session, model, entity_id, user: User):
    """Fetch one row by id within the caller's tenant; anything else is a 404 (not a 403 —
    existence in another tenant must not be revealed)."""
    row = db.scalar(tenant_select(model, user).where(model.id == entity_id))
    if row is None:
        raise HTTPException(status_code=404, detail=f"{model.__tablename__[:-1]} not found")
    return row
