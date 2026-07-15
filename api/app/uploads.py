"""Upload limits — one per-file size cap and one per-tenant storage quota.

Both exist because "Baton Demo Co" publishes its credentials (demo.py), which makes every
upload endpoint reachable by anyone on the internet. Without these, a visitor could push
arbitrarily large files into the firm's Blob account, and the API — pinned at a single
replica — would materialize each one in memory on the way through.

Everything that accepts an UploadFile must go through read_capped(). There are two storage
helpers (files.store_upload and vat_engine._store_upload) and three handlers that read the
raw bytes *before* storing them (the VAT ledger and register parsers, and AI extraction), so
capping the storage helpers alone would leave those three paths uncapped. read_capped is the
one door.

Scope, deliberately: the quota is charged against user uploads only. Server-generated
artifacts (reconciliation workbooks, computation PDFs, ledger templates) still count toward
a firm's usage but are never refused — a real firm hitting its quota must not have a filing
break halfway through document generation. The attack surface is the upload endpoints.
"""

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .demo import is_demo_tenant
from .models import File

CHUNK = 1024 * 1024  # 1 MiB


def max_upload_bytes() -> int:
    return get_settings().MAX_UPLOAD_MB * 1024 * 1024


def quota_bytes(db: Session, tenant_id) -> int:
    s = get_settings()
    mb = s.DEMO_STORAGE_QUOTA_MB if is_demo_tenant(db, tenant_id) else s.TENANT_STORAGE_QUOTA_MB
    return mb * 1024 * 1024


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.0f} MB"


def read_capped(upload: UploadFile) -> bytes:
    """The bytes of one upload, or 413 if it is over the per-file cap.

    Never holds more than the cap in memory: UploadFile.size is filled in by the multipart
    parser, so an oversized file is refused without a single read; the chunked loop is the
    backstop for when size is absent (chunked transfer-encoding) and stops the moment the
    running total crosses the line rather than at end-of-file.

    Leaves the cursor at 0 — callers read the same upload again (the VAT handlers parse the
    bytes, then hand the same UploadFile to the storage helper).
    """
    limit = max_upload_bytes()
    if limit <= 0:
        upload.file.seek(0)
        return upload.file.read()

    if upload.size is not None and upload.size > limit:
        raise _too_large(upload.filename, upload.size, limit)

    upload.file.seek(0)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = upload.file.read(CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            upload.file.seek(0)
            raise _too_large(upload.filename, None, limit)
        chunks.append(chunk)
    upload.file.seek(0)
    return b"".join(chunks)


def _too_large(name: str | None, size: int | None, limit: int) -> HTTPException:
    actual = f" ({_mb(size)})" if size is not None else ""
    return HTTPException(status_code=413, detail={
        "code": "FILE_TOO_LARGE",
        "message": f"{name or 'That file'}{actual} is over the {_mb(limit)} limit for a single "
                   f"upload. Split it, or compress the scan, and try again.",
    })


def tenant_storage_bytes(db: Session, tenant_id) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(File.size), 0))
                         .where(File.tenant_id == tenant_id)) or 0)


def check_quota(db: Session, tenant_id, incoming: int) -> None:
    """413 when this upload would take the firm past its storage quota."""
    limit = quota_bytes(db, tenant_id)
    if limit <= 0:
        return
    used = tenant_storage_bytes(db, tenant_id)
    if used + incoming > limit:
        raise HTTPException(status_code=413, detail={
            "code": "STORAGE_QUOTA_EXCEEDED",
            "message": f"This firm has used {_mb(used)} of its {_mb(limit)} storage allowance, "
                       f"and this upload needs another {_mb(incoming)}. Remove what you no "
                       f"longer need, or ask for the allowance to be raised.",
        })
