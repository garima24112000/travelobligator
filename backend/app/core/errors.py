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
