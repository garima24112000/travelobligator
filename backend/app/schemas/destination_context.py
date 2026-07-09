from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.common import ProviderCoverage, UnavailableDataItem
from app.models.planning_state import DestinationContext


class DestinationContextResponseData(BaseModel):
    trip_id: str
    destination_context: DestinationContext
    provider_coverage: ProviderCoverage
    unavailable_data: list[UnavailableDataItem] = Field(default_factory=list)
    data_sources_used: list[str] = Field(default_factory=list)
