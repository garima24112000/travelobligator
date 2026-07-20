from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.common import ProviderCoverage, UnavailableDataItem
from app.models.planning_state import DestinationContext, HolidayContext, WeatherContext


class DestinationContextResponseData(BaseModel):
    trip_id: str
    destination_context: DestinationContext
    weather_context: WeatherContext | None = None
    holiday_context: HolidayContext | None = None
    provider_coverage: ProviderCoverage
    unavailable_data: list[UnavailableDataItem] = Field(default_factory=list)
    data_sources_used: list[str] = Field(default_factory=list)
