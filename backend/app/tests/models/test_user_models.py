from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.user import AuthResponse, LoginRequest, PublicUser, SignupRequest, UserRecord

# Tests for the Step 184B user/auth model layer
# (backend/app/models/user.py). No repository, route, or owner_id exists
# yet -- these test the models in complete isolation.


# ---------------------------------------------------------------------------
# Email normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("user@example.com", "user@example.com"),
        ("User@Example.com", "user@example.com"),
        ("  user@example.com  ", "user@example.com"),
        ("  USER@EXAMPLE.COM  ", "user@example.com"),
    ],
)
def test_signup_request_normalizes_email(raw: str, expected: str) -> None:
    request = SignupRequest(email=raw, password="longenough1")
    assert request.email == expected


def test_login_request_normalizes_email_the_same_way() -> None:
    request = LoginRequest(email="  User@Example.com ", password="anything")
    assert request.email == "user@example.com"


def test_user_record_normalizes_email() -> None:
    record = UserRecord(email="  User@Example.com ", password_hash="not-checked-here")
    assert record.email == "user@example.com"


@pytest.mark.parametrize("bad_email", ["", "   ", "not-an-email", "@example.com", "user@", "user@nodot"])
def test_signup_request_rejects_blank_or_invalid_email(bad_email: str) -> None:
    with pytest.raises(ValidationError):
        SignupRequest(email=bad_email, password="longenough1")


# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------


def test_signup_request_accepts_minimum_length_password() -> None:
    request = SignupRequest(email="a@b.com", password="12345678")
    assert request.password == "12345678"


@pytest.mark.parametrize("short_password", ["", "a", "1234567"])
def test_signup_request_rejects_password_shorter_than_minimum(short_password: str) -> None:
    with pytest.raises(ValidationError):
        SignupRequest(email="a@b.com", password=short_password)


def test_signup_request_rejects_password_longer_than_bcrypt_limit() -> None:
    too_long = "a" * 73
    with pytest.raises(ValidationError):
        SignupRequest(email="a@b.com", password=too_long)


def test_signup_request_accepts_password_at_exactly_the_byte_limit() -> None:
    exactly_72 = "a" * 72
    request = SignupRequest(email="a@b.com", password=exactly_72)
    assert request.password == exactly_72


def test_login_request_does_not_enforce_signup_password_rules() -> None:
    """Login must accept whatever was typed and let hash verification
    decide -- an account created under different historical rules must
    still be able to log in."""
    request = LoginRequest(email="a@b.com", password="x")
    assert request.password == "x"


# ---------------------------------------------------------------------------
# API-facing models never expose password_hash
# ---------------------------------------------------------------------------


def test_public_user_has_no_password_hash_field() -> None:
    assert "password_hash" not in PublicUser.model_fields


def test_auth_response_has_no_password_hash_field() -> None:
    assert "password_hash" not in AuthResponse.model_fields
    assert "password_hash" not in PublicUser.model_fields  # via nested `user`


def test_user_record_to_public_excludes_password_hash() -> None:
    record = UserRecord(email="a@b.com", password_hash="super-secret-hash-value")
    public = record.to_public()

    assert "password_hash" not in public.model_dump()
    assert "super-secret-hash-value" not in str(public.model_dump())
    assert public.user_id == record.user_id
    assert public.email == record.email


def test_auth_response_wraps_public_user_without_leaking_hash() -> None:
    record = UserRecord(email="a@b.com", password_hash="super-secret-hash-value")
    response = AuthResponse(user=record.to_public())

    dumped = response.model_dump()
    assert "password_hash" not in dumped["user"]
    assert "super-secret-hash-value" not in str(dumped)


# ---------------------------------------------------------------------------
# UserRecord defaults
# ---------------------------------------------------------------------------


def test_user_record_generates_a_unique_id_by_default() -> None:
    first = UserRecord(email="a@b.com", password_hash="h")
    second = UserRecord(email="c@d.com", password_hash="h")
    assert first.user_id != second.user_id
    assert first.user_id.startswith("user_")


def test_user_record_created_and_updated_at_default_to_now() -> None:
    record = UserRecord(email="a@b.com", password_hash="h")
    assert record.created_at is not None
    assert record.updated_at is not None
