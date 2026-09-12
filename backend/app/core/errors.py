from __future__ import annotations

from fastapi import status

from app.schemas.errors import ErrorCode


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status_code: int = 400,
        field: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.field = field
        super().__init__(message)


def trip_not_found_error(trip_id: str) -> AppError:
    """Build the standard 404 error for an unknown trip_id.

    Centralized so every route/service that needs this error raises it with
    the exact same message/spacing instead of each duplicating its own
    f-string (which had drifted into inconsistent spacing in a few call
    sites).
    """
    return AppError(
        code=ErrorCode.TRIP_NOT_FOUND,
        message=f"Trip '{trip_id}' was not found.",
        status_code=status.HTTP_404_NOT_FOUND,
        field="trip_id",
    )


def lock_not_found_error(trip_id: str, lock_id: str) -> AppError:
    """Build the standard 404 error for an unknown lock_id on a known trip."""
    return AppError(
        code=ErrorCode.LOCK_NOT_FOUND,
        message=f"Lock '{lock_id}' was not found for trip '{trip_id}'.",
        status_code=status.HTTP_404_NOT_FOUND,
        field="lock_id",
    )


# Shared with app.models.planning_state.RegenerationAttempt's default
# `message` and app.services.regeneration_attempt_service so the audit trail
# can never drift from the error response it's recording.
REGENERATION_NOT_AVAILABLE_MESSAGE = (
    "Feedback-driven regeneration is not available yet. The "
    "regeneration engine has not been implemented, so no plan "
    "changes were made."
)


def regeneration_not_available_error() -> AppError:
    """Build the standard hard-refusal error for `POST /trips/{trip_id}/regenerate`.

    Feedback-driven regeneration has no real engine connected yet, so this
    endpoint always refuses rather than silently doing nothing or
    pretending to succeed. The code/message/status are identical on every
    call, for every trip, so callers can rely on this being stable rather
    than trip-specific.

    Step 174B: this is also the refusal returned for the one remaining
    case real regeneration cannot yet handle -- `confirm=true`, feedback
    exists, and there are zero active locks. That request shape is
    exactly Section 174's chosen MVP scope, but no engine call has been
    wired in yet (Step 174C does that); until then this transitional case
    reuses this same error rather than inventing a fourth code for a
    distinction with no real behavioral difference today.
    """
    return AppError(
        code=ErrorCode.REGENERATION_NOT_AVAILABLE,
        message=REGENERATION_NOT_AVAILABLE_MESSAGE,
        status_code=status.HTTP_409_CONFLICT,
        field="regeneration",
    )


# Step 174B: distinct refusal for confirm=true requests blocked specifically
# by an active lock, so a caller (and the audit trail) can tell "regeneration
# isn't implemented" apart from "regeneration would run, but a lock is in
# the way." Shared with RegenerationAttempt.reason_code the same way
# REGENERATION_NOT_AVAILABLE_MESSAGE already is, so the two can never drift.
REGENERATION_BLOCKED_BY_LOCKS_MESSAGE = (
    "Regeneration is blocked because one or more active locks exist. "
    "Remove all active locks before requesting regeneration."
)


def regeneration_blocked_by_locks_error() -> AppError:
    """Build the refusal for a confirmed regeneration request while at
    least one active lock exists.

    Locks today are bookkeeping only -- no planning stage service reads or
    respects them -- so a confirmed regeneration request must refuse
    outright rather than silently ignoring the lock or pretending it was
    honored.
    """
    return AppError(
        code=ErrorCode.REGENERATION_BLOCKED_BY_LOCKS,
        message=REGENERATION_BLOCKED_BY_LOCKS_MESSAGE,
        status_code=status.HTTP_409_CONFLICT,
        field="regeneration",
    )


# Step 174B: distinct refusal for confirm=true requests with no pending
# feedback -- there is nothing for a real regeneration to act on yet.
REGENERATION_NO_PENDING_FEEDBACK_MESSAGE = (
    "Regeneration requires at least one pending feedback item. Submit "
    "feedback via POST /trips/{trip_id}/feedback before requesting "
    "regeneration."
)


def regeneration_no_pending_feedback_error() -> AppError:
    """Build the refusal for a confirmed regeneration request with an empty
    `feedback_history`."""
    return AppError(
        code=ErrorCode.REGENERATION_NO_PENDING_FEEDBACK,
        message=REGENERATION_NO_PENDING_FEEDBACK_MESSAGE,
        status_code=status.HTTP_409_CONFLICT,
        field="regeneration",
    )


# Auth foundation (Step 184B). None of these are raised by any route yet --
# no route is auth-gated until Step 184D wires `get_current_user_id`
# (backend/app/auth/dependencies.py) and an owner check into
# `app/api/routes/trips.py`. Declared now so that dependency, and the
# `/auth/*` routes Step 184C adds, have real, tested error constructors
# ready to use, matching every other error in this file's centralized-
# constructor convention.


def authentication_required_error() -> AppError:
    """401 -- no session cookie present, or it failed to verify (missing,
    malformed, unsigned/tampered, or expired). Deliberately the same
    error for all of those cases -- never reveals *why* verification
    failed, which could otherwise help an attacker distinguish a stolen-
    but-expired cookie from a forged one."""
    return AppError(
        code=ErrorCode.AUTHENTICATION_REQUIRED,
        message="Authentication is required for this request.",
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


def forbidden_error() -> AppError:
    """403 -- a real, authenticated session, but not the resource's owner.
    Distinct from `trip_not_found_error` (404): once Step 184D wires this
    in, a non-owner gets 403 for a trip that exists (owned by someone
    else) so the two cases stay distinguishable by the caller, without
    the 403 response itself ever describing who the real owner is."""
    return AppError(
        code=ErrorCode.FORBIDDEN,
        message="You do not have access to this resource.",
        status_code=status.HTTP_403_FORBIDDEN,
    )


# Deliberately generic: never reveals whether the email is registered or
# the password was wrong, so a login attempt can't be used to enumerate
# registered accounts. Shared as a module-level constant so login-route
# tests (Step 184C) and this constructor can never drift.
INVALID_CREDENTIALS_MESSAGE = "Invalid email or password."


def invalid_credentials_error() -> AppError:
    """401 -- login failed. See `INVALID_CREDENTIALS_MESSAGE` for why the
    message never distinguishes "unknown email" from "wrong password.\""""
    return AppError(
        code=ErrorCode.INVALID_CREDENTIALS,
        message=INVALID_CREDENTIALS_MESSAGE,
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


def email_already_registered_error() -> AppError:
    """409 -- signup with an email that already has an account."""
    return AppError(
        code=ErrorCode.EMAIL_ALREADY_REGISTERED,
        message="An account with this email already exists.",
        status_code=status.HTTP_409_CONFLICT,
        field="email",
    )


def auth_not_configured_error() -> AppError:
    """503 -- session signing/verification was attempted while
    `Settings.session_secret_key` is unset. Distinct from
    `authentication_required_error` (401): this is a server
    misconfiguration, not "you're not logged in," and should be fixed by
    an operator setting `SESSION_SECRET_KEY` in a real `.env`, not by a
    user logging in again. Never includes the secret's value (there isn't
    one to include -- that's the whole point)."""
    return AppError(
        code=ErrorCode.AUTH_NOT_CONFIGURED,
        message="Authentication is not configured on this server.",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )
