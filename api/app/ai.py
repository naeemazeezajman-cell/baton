"""AI drafting assistant — rewrites rough payment-terms notes into client-ready wording.

Server-side only (ANTHROPIC_API_KEY never reaches the browser). Strict: preserves every
figure/frequency/deadline; returns None on any failure so callers fall back to raw text.
"""

import logging

from .config import get_settings

log = logging.getLogger("baton.ai")

PROMPT = (
    "You are drafting for a professional accounting & tax consultancy in the UAE. "
    "Rewrite the rough payment-terms note below into polished, client-ready wording for a services proposal.\n\n"
    "Strict rules:\n"
    "- Preserve EVERY amount, percentage, frequency, service name and deadline exactly as given "
    "— do not add, drop or change any commercial fact.\n"
    "- One short professional paragraph or clean semicolon-separated clauses. Formal but plain English. "
    'Fix all spelling and grammar (e.g. "quaterly" → "quarterly", "Ct filling" → "Corporate Tax filing").\n'
    "- If the note is already professional, return it unchanged.\n"
    "- Return ONLY the rewritten text, no preamble, no quotes.\n\n"
    'Rough note: "{rough}"'
)


def polish_payment_terms(rough: str) -> str | None:
    rough = (rough or "").strip()
    if not rough or not get_settings().ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=get_settings().ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            temperature=0,
            messages=[{"role": "user", "content": PROMPT.format(rough=rough)}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        return text or None
    except Exception as exc:  # any failure → caller uses the raw text
        log.warning("payment-terms polish failed, using raw text: %s", exc)
        return None
