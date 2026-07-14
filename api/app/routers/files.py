import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import blobs, emails
from ..config import get_settings
from ..db import get_db
from ..models import File, User
from ..security import current_user
from ..tenancy import get_scoped_or_404

router = APIRouter(prefix="/files", tags=["files"])


class FileOut(BaseModel):
    id: uuid.UUID
    entity: str
    entity_id: uuid.UUID
    name: str
    size: int | None
    uploaded_by: uuid.UUID | None = None
    at: datetime | None = None

    model_config = {"from_attributes": True}


def store_upload(db: Session, user: User, entity: str, entity_id: uuid.UUID, upload: UploadFile) -> File:
    """Save an upload to blob storage and register the files row (no commit)."""
    data = upload.file.read()
    path = blobs.blob_path_for(user.tenant_id, entity, upload.filename)
    blobs.save_blob(path, data)
    row = File(
        tenant_id=user.tenant_id, entity=entity, entity_id=entity_id,
        name=upload.filename, size=len(data), blob_path=path, uploaded_by=user.id,
    )
    db.add(row)
    db.flush()
    return row


@router.get("", response_model=list[FileOut])
def list_files(
    entity: str,
    entity_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from sqlalchemy import select
    rows = db.scalars(
        select(File).where(File.tenant_id == user.tenant_id, File.entity == entity,
                           File.entity_id == entity_id).order_by(File.at)
    ).all()
    return rows


@router.post("", response_model=FileOut, status_code=201)
def upload_file(
    entity: str = Form(...),
    entity_id: uuid.UUID = Form(...),
    file: UploadFile = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if file is None:
        raise HTTPException(status_code=422, detail="file is required")
    row = store_upload(db, user, entity, entity_id, file)
    db.commit()
    return row


def _download_token(file_id: uuid.UUID) -> str:
    s = get_settings()
    return jwt.encode(
        {"sub": str(file_id), "type": "file", "exp": datetime.now(timezone.utc) + timedelta(minutes=blobs.LINK_TTL_MIN)},
        s.JWT_SECRET, algorithm="HS256",
    )


def file_download_url(row: File) -> str:
    """Short-lived download URL for a stored file — Azure SAS, or a signed local-dev URL."""
    url = blobs.sas_link(row.blob_path)
    if url is None:  # local dev / no blob storage
        url = f"{get_settings().FRONTEND_ORIGIN}/files/{row.id}/download?token={_download_token(row.id)}"
    return url


def attachments_or_links(rows: list[File]) -> tuple[list[dict], list[str]]:
    """Turn stored files into email attachments, or — when their combined size would exceed
    the ACS message limit — into download-link body lines instead. Returns
    (attachments, link_lines): exactly one side is populated. Attachments carry no expiry and
    need no storage public access; the link fallback keeps oversized sends working."""
    attachments = [blobs.file_attachment(r.name, r.blob_path) for r in rows]
    total = sum(len(a["contentInBase64"]) for a in attachments)
    if total <= emails.ATTACHMENT_LIMIT_BASE64:
        return attachments, []
    links = [f"- {r.name}: {file_download_url(r)}" for r in rows]
    return [], links


@router.get("/{file_id}/link")
def file_link(file_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Short-lived download link after tenancy check — Azure SAS, or a signed local-dev URL."""
    row = get_scoped_or_404(db, File, file_id, user)
    url = blobs.sas_link(row.blob_path)
    if url is None:  # local dev mode
        url = f"/files/{row.id}/download?token={_download_token(row.id)}"
    return {"url": url, "expires_in_minutes": blobs.LINK_TTL_MIN, "name": row.name}


@router.get("/{file_id}/download")
def download_file(file_id: uuid.UUID, token: str, db: Session = Depends(get_db)):
    """Local-dev download endpoint — access only via the signed token from /link."""
    try:
        payload = jwt.decode(token, get_settings().JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired link")
    if payload.get("type") != "file" or payload.get("sub") != str(file_id):
        raise HTTPException(status_code=401, detail="Invalid link")
    row = db.get(File, file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="file not found")
    data = blobs.read_blob(row.blob_path)
    return Response(
        content=data, media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{row.name}"'},
    )
