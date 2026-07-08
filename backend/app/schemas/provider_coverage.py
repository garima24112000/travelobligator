from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.common import ProviderCoverage, ProviderStatusEntry, UnavailableDataItem


class ProviderCoverageResponseData(BaseModel):
    trip_id: str
    provider_coverage: ProviderCoverage
    provider_status: dict[str, ProviderStatusEntry] = Field(default_factory=dict)
    unavailable_data: list[UnavailableDataItem] = Field(default_factory=list)
    data_sources_used: list[str] = Field(default_factory=list)
