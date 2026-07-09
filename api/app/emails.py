"""Email sending — Azure Communication Services when EMAIL_CONN is set, console log otherwise."""

import logging

from .config import get_settings

log = logging.getLogger("baton.emails")
logging.basicConfig(level=logging.INFO)


def _send(to: str, subject: str, body: str) -> None:
    s = get_settings()
    if not s.EMAIL_CONN:
        log.info("[DEV EMAIL] to=%s subject=%r\n%s", to, subject, body)
        return
    from azure.communication.email import EmailClient

    client = EmailClient.from_connection_string(s.EMAIL_CONN)
    message = {
        "senderAddress": s.EMAIL_FROM,
        "recipients": {"to": [{"address": to}]},
        "content": {"subject": subject, "plainText": body},
    }
    client.begin_send(message)


def send_invite(to: str, name: str, firm: str, set_password_link: str, temp_password: str | None = None) -> None:
    body = (
        f"Hello {name},\n\n"
        f"You have been invited to {firm}'s Baton workspace.\n\n"
        f"Set your password (link valid 72 hours):\n{set_password_link}\n"
    )
    if temp_password:
        body += f"\nTemporary password (you will be asked to change it on first login): {temp_password}\n"
    body += "\n— Baton"
    _send(to, f"You've been invited to {firm} on Baton", body)


def send_client(to: str, subject: str, body: str) -> None:
    """Client-facing send (proposal / engagement letter emails)."""
    _send(to, subject, body)


def send_reset(to: str, name: str, set_password_link: str) -> None:
    body = (
        f"Hello {name},\n\n"
        f"A password reset was requested for your Baton account.\n\n"
        f"Reset your password (link valid 72 hours):\n{set_password_link}\n\n"
        "If you did not request this, you can ignore this email.\n\n— Baton"
    )
    _send(to, "Baton password reset", body)
