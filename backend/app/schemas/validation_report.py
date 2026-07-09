from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.common import ProviderCoverage, UnavailableDataItem
from app.models.planning_state import ValidationReport


class ValidationReportResponseData(BaseModel):
    trip_id: str
    validation_report: ValidationReport
    provider_coverage: ProviderCoverage
    unavailable_data: list[UnavailableDataItem] = Field(default_factory=list)
    data_sources_used: list[str] = Field(default_factory=list)
