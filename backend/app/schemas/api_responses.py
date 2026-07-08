from __future__ import annotations

from datetime import datetime, timezone
from typing import Generic, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.schemas.errors import ApiError

T = TypeVar("T")


class ResponseMetadata(BaseModel):
    request_id: str = Field(default_factory=lambda: f"req_{uuid4().hex}")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    environment: str = Field(default_factory=lambda: get_settings().app_env)


class ApiResponse(BaseModel, Generic[T]):
    success: bool
    data: T | None = None
    message: str | None = None
    errors: list[ApiError] = Field(default_factory=list)
    metadata: ResponseMetadata = Field(default_factory=ResponseMetadata)
