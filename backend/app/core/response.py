from __future__ import annotations

from typing import TypeVar

from app.schemas.api_responses import ApiResponse
from app.schemas.errors import ApiError

T = TypeVar("T")


def success_response(data: T, message: str | None = None) -> ApiResponse[T]:
    return ApiResponse[T](success=True, data=data, message=message)


def error_response(errors: list[ApiError], message: str | None = None) -> ApiResponse[None]:
    return ApiResponse[None](success=False, data=None, message=message, errors=errors)
