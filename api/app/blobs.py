"""File storage — Azure Blob Storage when AZURE_BLOB_CONN is set, local filesystem otherwise.

Blob path convention: tenant-files/{tenant_id}/{entity}/{uuid}-{name} (STRUCTURE.md §6).
Downloads only via short-lived links from GET /files/{id}/link after a tenancy check —
Azure: 15-minute SAS; local dev: signed 15-minute token on /files/{id}/download.
"""

import base64
import mimetypes
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from .config import get_settings

CONTAINER = "tenant-files"
LINK_TTL_MIN = 15


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\- ]", "_", os.path.basename(name or "file"))


def blob_path_for(tenant_id, entity: str, name: str) -> str:
    return f"{tenant_id}/{entity}/{uuid.uuid4()}-{_safe_name(name)}"


def _local_root() -> Path:
    root = Path(get_settings().FILES_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_blob(blob_path: str, data: bytes) -> None:
    s = get_settings()
    if s.AZURE_BLOB_CONN:
        from azure.storage.blob import BlobServiceClient

        svc = BlobServiceClient.from_connection_string(s.AZURE_BLOB_CONN)
        svc.get_blob_client(CONTAINER, blob_path).upload_blob(data, overwrite=False)
        return
    target = _local_root() / blob_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def read_blob(blob_path: str) -> bytes:
    s = get_settings()
    if s.AZURE_BLOB_CONN:
        from azure.storage.blob import BlobServiceClient

        svc = BlobServiceClient.from_connection_string(s.AZURE_BLOB_CONN)
        return svc.get_blob_client(CONTAINER, blob_path).download_blob().readall()
    return (_local_root() / blob_path).read_bytes()


def sas_link(blob_path: str) -> str | None:
    """15-minute read-only SAS URL; None in local dev mode (caller issues a token link)."""
    s = get_settings()
    if not s.AZURE_BLOB_CONN:
        return None
    from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas

    svc = BlobServiceClient.from_connection_string(s.AZURE_BLOB_CONN)
    sas = generate_blob_sas(
        account_name=svc.account_name,
        container_name=CONTAINER,
        blob_name=blob_path,
        account_key=svc.credential.account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(minutes=LINK_TTL_MIN),
    )
    # percent-encode the path (blob names may contain spaces, e.g. "VAT Ledger Template.xlsx")
    # so the URL is a single unbroken token — an unencoded space truncates the link in email
    # clients and drops the SAS query string, making the request anonymous.
    return f"{svc.url}{CONTAINER}/{quote(blob_path)}?{sas}"


def file_attachment(name: str, blob_path: str) -> dict:
    """Build an ACS email attachment from a stored blob: the raw bytes, base64-encoded, with
    a content type inferred from the filename. Reads from blob storage or the local fallback."""
    data = read_blob(blob_path)
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return {
        "name": name,
        "contentType": content_type,
        "contentInBase64": base64.b64encode(data).decode("ascii"),
    }
