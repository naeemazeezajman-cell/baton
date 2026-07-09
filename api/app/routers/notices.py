import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Notice, User
from ..security import current_user

router = APIRouter(prefix="/notices", tags=["notices"])


@router.get("")
def my_notices(unread_only: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Notice).where(Notice.tenant_id == user.tenant_id, Notice.user_id == user.id)
    if unread_only:
        q = q.where(Notice.read.is_(False))
    rows = db.scalars(q.order_by(Notice.at.desc(), Notice.id.desc())).all()
    return [{"id": n.id, "at": n.at, "text": n.text_, "read": n.read} for n in rows]


@router.post("/{notice_id}/read")
def mark_read(notice_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    n = db.scalar(select(Notice).where(Notice.id == notice_id, Notice.tenant_id == user.tenant_id,
                                       Notice.user_id == user.id))
    if n is None:
        raise HTTPException(status_code=404, detail="notice not found")
    n.read = True
    db.commit()
    return {"id": n.id, "read": True}
