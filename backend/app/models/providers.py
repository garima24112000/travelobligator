from __future__ import annotations

from datetime import date, datetime, timezone
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


class NormalizedDailyWeather(BaseModel):
    """Real weather data for one day, returned by a WeatherProvider adapter
    (docs/12_provider_architecture.md section 15). Only fields the
    underlying source actually returned are populated; no weather
    description/condition, humidity, UV, or alert is fabricated when the
    source doesn't supply it.
    """

    date: date
    temperature_max_c: float | None = None
    temperature_min_c: float | None = None
    precipitation_probability_max: float | None = None
    precipitation_sum_mm: float | None = None
    weather_code: int | None = None
    source: str
    data_status: DataStatus


class NormalizedExchangeRate(BaseModel):
    """Real exchange-rate data returned by a CurrencyProvider adapter
    (docs/12_provider_architecture.md section 17). Only fields the
    underlying source actually returned are populated; no trip cost,
    budget, hotel price, restaurant price, attraction price, fee, tax, or
    total-cost value is ever fabricated -- the source doesn't supply any of
    that, and this only ever converts one unit of currency, never a trip
    total.
    """

    base_currency: str
    destination_currency: str
    exchange_rate: float
    rate_date: date | None = None
    source: str
    data_status: DataStatus


class NormalizedHoliday(BaseModel):
    """Real public holiday data for one day, returned by a HolidayProvider
    adapter (docs/12_provider_architecture.md section 16). Only fields the
    underlying source actually returned are populated; no closure, crowd,
    opening-hour, event, festival, strike, or risk assessment is ever
    fabricated -- the source doesn't supply any of that.
    """

    date: date
    local_name: str
    name: str
    country_code: str
    is_global: bool
    counties: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    source: str
    data_status: DataStatus
