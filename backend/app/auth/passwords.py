"""Password hashing utilities (Step 184B).

`bcrypt` directly (not `passlib`, which currently has version-
compatibility friction with newer `bcrypt` releases) -- salted, adaptive
hashing only, never a custom scheme, never plain SHA/MD5. Not imported by
any route yet; foundation only, matching this codebase's established
"dependency/utility added before it's wired in" pattern (see e.g.
`app/db/session.py` from Step 183B).
"""

from __future__ import annotations

import bcrypt

# bcrypt's own hard limit (bytes, not characters) -- `bcrypt.hashpw`/
# `bcrypt.checkpw` raise ValueError above this. Enforced again here (and,
# for signup, at the request-model layer in `app.models.user.SignupRequest`)
# so this never surfaces as a raw, unhandled exception.
_MAX_PASSWORD_BYTES = 72


def hash_password(plain_password: str) -> str:
    """Returns a salted bcrypt hash as a `str` (bcrypt itself returns
    `bytes`) -- never equal to `plain_password` itself.

    Raises `ValueError` for a password longer than bcrypt's 72-byte
    limit. Never logs or includes `plain_password` in any exception
    message.
    """
    encoded = plain_password.encode("utf-8")
    if len(encoded) > _MAX_PASSWORD_BYTES:
        raise ValueError("Password exceeds the maximum supported length.")
    hashed = bcrypt.hashpw(encoded, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Returns whether `plain_password` matches `password_hash`.

    Returns `False` (never raises) for a wrong password, an over-length
    password, or a malformed/corrupt stored hash -- a corrupt stored
    value must fail closed, never crash the caller with a raw exception
    that could otherwise leak into an error response. Never logs or
    includes `plain_password` in any exception/log message.
    """
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False
