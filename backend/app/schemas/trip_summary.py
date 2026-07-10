from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.models.common import ProviderCoverage
from app.models.planning_state import PipelineStatus, PlanningStage


class TripSummaryResponseData(BaseModel):
    trip_id: str
    primary_destination: str
    start_date: date
    end_date: date

    pipeline_status: PipelineStatus
    active_stage: PlanningStage | None

    provider_coverage: ProviderCoverage

    destination_context_generated: bool
    experience_plan_generated: bool
    validation_report_generated: bool

    candidate_pois_count: int
    candidate_restaurants_count: int
    candidate_accommodation_pois_count: int
    scheduled_experiences_count: int

    validation_status: str | None
    main_blocking_reason: str | None
    main_review_reason: str | None
