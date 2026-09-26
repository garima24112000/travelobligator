"""Secret redaction for log output (Section 200D).

Log call sites never intentionally pass secrets, but an exception's own message can echo one (a
driver error naming the connection URL, a client library echoing a key). Every rendered log line is
therefore scrubbed by `redact()` before it leaves the process:

  * credentials embedded in any URL (`scheme://user:password@host/db` -> `scheme://[REDACTED]`: the whole DSN goes, host and port included);
  * the offending input of a pydantic validation error (`input_value=...` -> `input_value=[REDACTED]`);
  * the exact values of configured secrets (DATABASE_URL, REDIS_URL, SESSION_SECRET_KEY and every
    `*_api_key` / `*_secret_key` setting) wherever they appear.

Only values of at least `_MIN_SECRET_LENGTH` characters are matched literally, so short config
values ("postgres", "true") never blank out ordinary words. `redact` never raises.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"
_MIN_SECRET_LENGTH = 6
_URL_CREDENTIALS = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)[^\s/@'\"]*@[^\s'\"]+")
# pydantic validation errors embed the offending INPUT ("input_value='...'"), which can be user
# content; the value is dropped, the error type/location stay.
_PYDANTIC_INPUT = re.compile(r"input_value=.*?(?=, input_type=|\]|$)", re.DOTALL)
_SECRET_FIELD_NAMES = ("database_url", "redis_url")
_SECRET_FIELD_SUFFIXES = ("_api_key", "_secret_key", "_password", "_token")


def configured_secret_values() -> list[str]:
    """The configured secret VALUES currently in effect (never logged, only matched)."""
    try:
        from app.core.config import get_settings

        settings = get_settings()
        values: list[str] = []
        for name in type(settings).model_fields:
            lowered = name.lower()
            if lowered in _SECRET_FIELD_NAMES or lowered.endswith(_SECRET_FIELD_SUFFIXES):
                raw = getattr(settings, name, None)
                if isinstance(raw, str) and len(raw) >= _MIN_SECRET_LENGTH:
                    values.append(raw)
                    # a URL secret is usually echoed in pieces: also match its password segment
                    match = re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://[^:/@]*:([^@/]+)@", raw)
                    if match and len(match.group(1)) >= _MIN_SECRET_LENGTH:
                        values.append(match.group(1))
        return sorted(set(values), key=len, reverse=True)
    except Exception:  # noqa: BLE001
        return []


def redact(text: str) -> str:
    try:
        cleaned = _URL_CREDENTIALS.sub(lambda m: m.group(1) + REDACTED, text)
        cleaned = _PYDANTIC_INPUT.sub("input_value=" + REDACTED, cleaned)
        for secret in configured_secret_values():
            cleaned = cleaned.replace(secret, REDACTED)
        return cleaned
    except Exception:  # noqa: BLE001
        return REDACTED
