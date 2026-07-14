"""Email sending — Azure Communication Services when EMAIL_CONN is set, console log otherwise."""

import logging

from .config import get_settings

log = logging.getLogger("baton.emails")
logging.basicConfig(level=logging.INFO)


def _deliver(sender: str, conn: str, to: str, subject: str, body: str) -> None:
    """Hand the message to Azure Communication Services. May raise (bad connection string,
    network, throttling) — always called from within _send's guard, never directly."""
    from azure.communication.email import EmailClient

    client = EmailClient.from_connection_string(conn)
    message = {
        "senderAddress": sender,
        "recipients": {"to": [{"address": to}]},
        "content": {"subject": subject, "plainText": body},
    }
    client.begin_send(message)


def _send(to: str, subject: str, body: str) -> bool:
    """Send one email. NEVER raises — email delivery must not break core operations
    (firm creation, proposal/EL send, invites, reminders all proceed even if this fails).
    Returns True if handed to the provider (or logged in dev mode), False if it failed;
    a failure is logged as a WARNING naming sender, recipient and the underlying reason."""
    s = get_settings()
    if not s.EMAIL_CONN:
        log.info("[DEV EMAIL] to=%s subject=%r\n%s", to, subject, body)
        return True
    try:
        _deliver(s.EMAIL_FROM, s.EMAIL_CONN, to, subject, body)
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


def send_client(to: str, subject: str, body: str) -> bool:
    """Client-facing send (proposal / engagement letter emails)."""
    return _send(to, subject, body)


def send_reset(to: str, name: str, set_password_link: str) -> bool:
    body = (
        f"Hello {name},\n\n"
        f"A password reset was requested for your Baton account.\n\n"
        f"Reset your password (link valid 72 hours):\n{set_password_link}\n\n"
        "If you did not request this, you can ignore this email.\n\n— Baton"
    )
    return _send(to, "Baton password reset", body)
