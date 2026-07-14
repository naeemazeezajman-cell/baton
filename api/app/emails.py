"""Email sending — Azure Communication Services when EMAIL_CONN is set, console log otherwise.

Client-facing sends carry two extras (see the routers):
  * reply_to = (email, name) — the From stays the technical azurecomm.net sender, but replies
    route to the real acting staff member. A body line names them too.
  * attachments = [{name, contentType, contentInBase64}] — real file attachments (built by
    blobs.file_attachment) instead of SAS download links. Combined size is capped just under
    the ACS ~10 MB message limit; the caller falls back to a link when over (see
    files.attachments_or_links)."""

import logging

from .config import get_settings

log = logging.getLogger("baton.emails")
logging.basicConfig(level=logging.INFO)

# ACS caps the whole message (body + base64 attachments + headers) at ~10 MB. Guard the
# combined base64 length below that with headroom; over this the caller sends a link instead.
ATTACHMENT_LIMIT_BASE64 = 9_000_000


def reply_to_line(name: str, email: str) -> str:
    """The in-body pointer to the real person, mirroring the Reply-To header."""
    return f"Please reply to this email to reach {name} directly at {email}."


def _deliver(sender: str, conn: str, to: str, subject: str, body: str,
             reply_to: tuple[str, str] | None = None,
             attachments: list[dict] | None = None) -> None:
    """Hand the message to Azure Communication Services. May raise (bad connection string,
    network, throttling) — always called from within _send's guard, never directly."""
    from azure.communication.email import EmailClient

    client = EmailClient.from_connection_string(conn)
    message = {
        "senderAddress": sender,
        "recipients": {"to": [{"address": to}]},
        "content": {"subject": subject, "plainText": body},
    }
    if reply_to:
        message["replyTo"] = [{"address": reply_to[0], "displayName": reply_to[1] or reply_to[0]}]
    if attachments:
        message["attachments"] = [
            {"name": a["name"], "contentType": a["contentType"], "contentInBase64": a["contentInBase64"]}
            for a in attachments
        ]
    client.begin_send(message)


def _send(to: str, subject: str, body: str,
          reply_to: tuple[str, str] | None = None,
          attachments: list[dict] | None = None) -> bool:
    """Send one email. NEVER raises — email delivery must not break core operations
    (firm creation, proposal/EL send, invites, reminders all proceed even if this fails).
    Returns True if handed to the provider (or logged in dev mode), False if it failed;
    a failure is logged as a WARNING naming sender, recipient and the underlying reason."""
    s = get_settings()
    if not s.EMAIL_CONN:
        extra = ""
        if reply_to:
            extra += f" reply_to={reply_to[0]}"
        if attachments:
            extra += f" attachments={[a['name'] for a in attachments]}"
        log.info("[DEV EMAIL] to=%s subject=%r%s\n%s", to, subject, extra, body)
        return True
    try:
        _deliver(s.EMAIL_FROM, s.EMAIL_CONN, to, subject, body, reply_to, attachments)
        return True
    except Exception as exc:  # noqa: BLE001 — email must never be fatal to the caller
        log.warning(
            "EMAIL SEND FAILED (non-fatal) — sender=%s recipient=%s subject=%r reason=%s: %s",
            s.EMAIL_FROM, to, subject, type(exc).__name__, exc,
        )
        return False


def send_invite(to: str, name: str, firm: str, set_password_link: str, temp_password: str | None = None) -> bool:
    body = (
        f"Hello {name},\n\n"
        f"You have been invited to {firm}'s Baton workspace.\n\n"
        f"Set your password (link valid 72 hours):\n{set_password_link}\n"
    )
    if temp_password:
        body += f"\nTemporary password (you will be asked to change it on first login): {temp_password}\n"
    body += "\n— Baton"
    return _send(to, f"You've been invited to {firm} on Baton", body)


def send_client(to: str, subject: str, body: str,
                reply_to: tuple[str, str] | None = None,
                attachments: list[dict] | None = None) -> bool:
    """Client-facing send (proposals, ELs, invoices, VAT documents, duty deliverables).
    reply_to routes replies to the acting staff member; attachments are real files."""
    return _send(to, subject, body, reply_to=reply_to, attachments=attachments)


def send_reset(to: str, name: str, set_password_link: str) -> bool:
    body = (
        f"Hello {name},\n\n"
        f"A password reset was requested for your Baton account.\n\n"
        f"Reset your password (link valid 72 hours):\n{set_password_link}\n\n"
        "If you did not request this, you can ignore this email.\n\n— Baton"
    )
    return _send(to, "Baton password reset", body)
