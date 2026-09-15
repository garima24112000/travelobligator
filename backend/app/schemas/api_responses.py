from __future__ import annotations

from datetime import datetime, timezone
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.request_context import get_current_request_id, new_request_id
from app.schemas.errors import ApiError

T = TypeVar("T")


def _default_request_id() -> str:
    """Step 187C: inside a request, reuses the exact same id
    `RequestIdMiddleware` already set as the current request's
    correlation id (and already echoed as the `X-Request-Id` response
    header) -- so a response body's `metadata.request_id` and its own
    `X-Request-Id` header are always the same value, and both match any
    structured log line emitted while the route ran. Outside a request
    (a test/utility constructing `ResponseMetadata`/`ApiResponse`
    directly, exactly like before this step) falls back to generating a
    fresh id, matching this field's pre-187C behavior exactly.
    """
    return get_current_request_id() or new_request_id()


class ResponseMetadata(BaseModel):
    request_id: str = Field(default_factory=_default_request_id)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    environment: str = Field(default_factory=lambda: get_settings().app_env)


class ApiResponse(BaseModel, Generic[T]):
    success: bool
    data: T | None = None
    message: str | None = None
    errors: list[ApiError] = Field(default_factory=list)
    metadata: ResponseMetadata = Field(default_factory=ResponseMetadata)
