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
