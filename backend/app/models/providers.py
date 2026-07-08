from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from app.models.common import DataStatus, GeoPoint, ProviderStatus

T = TypeVar("T")


class ProviderType(str, Enum):
    PLACES = "places"
    ROUTES = "routes"
    TRANSIT = "transit"
    ACCOMMODATION = "accommodation"
    FLIGHT = "flight"
    WEATHER = "weather"
    HOLIDAY = "holiday"
    CURRENCY = "currency"
    AI_REASONING = "ai_reasoning"


class ProviderResponse(BaseModel, Generic[T]):
    """Standard normalized shape returned by every provider call.

    Matches docs/12_provider_architecture.md section 5.
    """

    provider_name: str
    provider_type: ProviderType
    status: ProviderStatus
    data_status: DataStatus
    data: T | None = None
    unavailable_fields: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    fallback_provider: str | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    message: str | None = None


class NormalizedPlace(BaseModel):
    """Real place data returned by a PlacesProvider adapter.

    Matches docs/12_provider_architecture.md section 10. Only fields that
    the underlying source actually returned are populated; rating, opening
    hours, and price level are intentionally not modeled here because
    OpenStreetMap/Overpass does not reliably supply them.
    """

    place_id: str
    name: str
    category: str | None = None
    coordinates: GeoPoint | None = None
    address: str | None = None
    source: str
    data_status: DataStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
