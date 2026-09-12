from __future__ import annotations

from fastapi import status

from app.core.errors import (
    INVALID_CREDENTIALS_MESSAGE,
    auth_not_configured_error,
    authentication_required_error,
    email_already_registered_error,
    forbidden_error,
    invalid_credentials_error,
    trip_not_found_error,
)
from app.schemas.errors import ErrorCode


def test_trip_not_found_error_message_has_correct_spacing() -> None:
    error = trip_not_found_error("does-not-exist")

    # Exact spacing: one space before "was", one space before "not", one
    # space before "found." -- guards against the "Trip 'x'was not found."
    # / "Trip 'x' wasnot found." style typos this helper exists to prevent.
    assert error.message == "Trip 'does-not-exist' was not found."
    assert error.code == ErrorCode.TRIP_NOT_FOUND
    assert error.status_code == status.HTTP_404_NOT_FOUND
    assert error.field == "trip_id"


def test_trip_not_found_error_message_scales_with_trip_id() -> None:
    error = trip_not_found_error("trip_abc123")
    assert error.message == "Trip 'trip_abc123' was not found."


# ---------------------------------------------------------------------------
# Step 184B: auth error constructors. None of these are raised by any
# route yet -- no route is auth-gated until Step 184D. These only prove
# the constructors themselves are correct/stable, matching this file's
# existing style for every other centralized error constructor.
# ---------------------------------------------------------------------------


def test_authentication_required_error_is_401() -> None:
    error = authentication_required_error()
    assert error.code == ErrorCode.AUTHENTICATION_REQUIRED
    assert error.status_code == status.HTTP_401_UNAUTHORIZED


def test_forbidden_error_is_403() -> None:
    error = forbidden_error()
    assert error.code == ErrorCode.FORBIDDEN
    assert error.status_code == status.HTTP_403_FORBIDDEN


def test_invalid_credentials_error_is_401_with_generic_message() -> None:
    error = invalid_credentials_error()
    assert error.code == ErrorCode.INVALID_CREDENTIALS
    assert error.status_code == status.HTTP_401_UNAUTHORIZED
    assert error.message == INVALID_CREDENTIALS_MESSAGE
    # Never distinguishes "unknown email" from "wrong password" -- the
    # message mentions both generically, and `field` stays unset so a
    # caller can't infer which one from that either.
    assert error.field is None


def test_email_already_registered_error_is_409() -> None:
    error = email_already_registered_error()
    assert error.code == ErrorCode.EMAIL_ALREADY_REGISTERED
    assert error.status_code == status.HTTP_409_CONFLICT
    assert error.field == "email"


def test_auth_not_configured_error_is_503_and_never_includes_a_secret_value() -> None:
    error = auth_not_configured_error()
    assert error.code == ErrorCode.AUTH_NOT_CONFIGURED
    assert error.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "SESSION_SECRET_KEY" not in error.message or "=" not in error.message
