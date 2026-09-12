"""User/auth models (Step 184B).

Foundation only -- no repository, no route, no `owner_id` on trips yet
(Step 184C/184D's job). `UserRecord` is the full internal representation
(includes `password_hash`); `PublicUser`/`AuthResponse` are the
API-facing shapes and must never include it -- see
`test_user_models.py::test_public_user_and_auth_response_exclude_password_hash`.

Email is always normalized (trimmed, lowercased) before it reaches any
model field here, so "  User@Example.com " and "user@example.com" are
the same account -- comparison/uniqueness (Step 184C's job) can then
compare normalized strings directly with no separate case-folding step.
Deliberately does not depend on `pydantic[email]`/`email-validator` (not
already a dependency in this project) -- a small manual shape check
below is enough for "reject obviously blank/invalid input," which is all
this step needs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

_MIN_PASSWORD_LENGTH = 8
# bcrypt's own hard limit (see app/auth/passwords.py) -- enforced here too
# so an over-long password fails as a normal 422 validation error at the
# request boundary, not a raw ValueError from the hashing layer.
_MAX_PASSWORD_BYTES = 72


def _new_user_id() -> str:
    return f"user_{uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_email(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Email must be a string.")
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("Email must not be blank.")
    local_part, _, domain_part = normalized.partition("@")
    if not local_part or not domain_part or "." not in domain_part:
        raise ValueError("Email must be a valid email address.")
    return normalized


class UserRecord(BaseModel):
    """Full internal user representation, including `password_hash`.

    Never returned directly from an API route -- see `PublicUser`.
    """

    user_id: str = Field(default_factory=_new_user_id)
    email: str
    password_hash: str
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("email", mode="before")
    @classmethod
    def _validate_email(cls, value: object) -> str:
        return _normalize_email(value)

    def to_public(self) -> "PublicUser":
        return PublicUser(
            user_id=self.user_id,
            email=self.email,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class PublicUser(BaseModel):
    """API-facing user shape -- deliberately excludes `password_hash`."""

    user_id: str
    email: str
    created_at: datetime
    updated_at: datetime


class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=_MIN_PASSWORD_LENGTH)

    @field_validator("email", mode="before")
    @classmethod
    def _validate_email(cls, value: object) -> str:
        return _normalize_email(value)

    @field_validator("password", mode="after")
    @classmethod
    def _validate_password_length(cls, value: str) -> str:
        if len(value.encode("utf-8")) > _MAX_PASSWORD_BYTES:
            raise ValueError(
                f"Password must be at most {_MAX_PASSWORD_BYTES} bytes long."
            )
        return value


class LoginRequest(BaseModel):
    """No password length/shape constraint here (unlike `SignupRequest`) --
    login must always attempt real hash verification against whatever was
    typed, since the account may have been created under different rules;
    a malformed/too-long password just fails verification normally (see
    `app.auth.passwords.verify_password`) rather than being rejected
    before it ever reaches that check."""

    email: str
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def _validate_email(cls, value: object) -> str:
        return _normalize_email(value)


class AuthResponse(BaseModel):
    """Response shape for signup/login/current-user endpoints (Step
    184C+). Never includes `password_hash` -- only ever wraps a
    `PublicUser`."""

    user: PublicUser
