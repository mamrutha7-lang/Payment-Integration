"""Best-effort PII redaction for free-text we persist (Assistant V2 Phase D).

The assistant's feedback note is a product signal, not a data store — a student
might paste an email, a phone number, or a USN into it. ``redact_pii`` strips the
obvious cases before the note is written so we don't quietly accumulate PII in a
low-attention table. It is deliberately conservative (better to leave an odd
token than to mangle useful feedback) and is reused by later Phase-D work.

Not a security boundary — the egress gate (app/ai/llm.py) is. This is hygiene on
stored free text.
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"

# Emails: local@domain.tld — matched first so their local part isn't chewed up
# by the phone/USN passes.
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# Phone numbers: an optional +CC / 0 prefix then 10 digits, tolerating spaces or
# hyphens between groups (e.g. "+91 98765 43210", "9876543210", "080-1234-5678").
_PHONE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s-]?)?(?:\d[\s-]?){9,12}\d(?!\w)"
)

# USN: a VTU-style 10-character alnum id (e.g. "1BG21CS001"). Require both a
# letter and a digit so ordinary 10-letter words are left alone.
_USN = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]{10}(?![A-Za-z0-9]))[A-Za-z0-9]{10}")


def _redact_usn(match: re.Match) -> str:
    tok = match.group(0)
    if any(c.isalpha() for c in tok) and any(c.isdigit() for c in tok):
        return REDACTED
    return tok


def redact_pii(text: str | None) -> str | None:
    """Return ``text`` with obvious emails, phone numbers and 10-char USNs
    replaced by ``[redacted]``. ``None``/empty passes straight through."""
    if not text:
        return text
    out = _EMAIL.sub(REDACTED, text)
    out = _USN.sub(_redact_usn, out)
    out = _PHONE.sub(REDACTED, out)
    return out
