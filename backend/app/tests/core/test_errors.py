from __future__ import annotations

from fastapi import status

from app.core.errors import trip_not_found_error
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
