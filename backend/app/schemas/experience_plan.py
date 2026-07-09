from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.common import ProviderCoverage, UnavailableDataItem
from app.models.planning_state import ExperiencePlan, ValidationReport


class ExperiencePlanResponseData(BaseModel):
    trip_id: str
    experience_plan: ExperiencePlan
    validation_report: ValidationReport | None = None
    provider_coverage: ProviderCoverage
    unavailable_data: list[UnavailableDataItem] = Field(default_factory=list)
    data_sources_used: list[str] = Field(default_factory=list)
