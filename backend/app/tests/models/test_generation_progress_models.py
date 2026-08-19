from __future__ import annotations

from app.models.planning_state import (
    GENERATION_STAGE_KEYS,
    GenerationProgress,
    GenerationStageStatus,
    PlanningState,
    TravelGroupType,
    TripRequest,
)

# Model tests for the backend PlanningOrchestrator pipeline stage-progress
# model (Step 163B, docs/13_llm_reasoning_pipeline.md section 44). This is
# real backend stage progress only -- never real flight tracking, a real
# route, a real travel time, or a booking status.

_FORBIDDEN_FACTUAL_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "booking_status",
    "review_count",
    "ticket_price",
    "availability",
    "safety_score",
    "flight_number",
    "airline",
    "route",
    "flight_route",
    "departure_time",
    "arrival_time",
    "coordinates",
}


def _trip_request() -> TripRequest:
    return TripRequest(
        primary_destination="Testville, Testland",
        origin_city="Home City",
        start_date="2026-08-10",
        end_date="2026-08-12",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )


def test_default_generation_progress_is_idle() -> None:
    progress = GenerationProgress()

    assert progress.status == GenerationStageStatus.IDLE
    assert progress.current_stage is None
    assert progress.current_stage_label is None
    assert progress.completed_stages == []
    assert progress.total_stages == len(GENERATION_STAGE_KEYS)
    assert progress.progress_percent == 0
    assert progress.is_real_backend_stage_progress is True


def test_generation_progress_serializes_and_deserializes() -> None:
    progress = GenerationProgress(
        status=GenerationStageStatus.GENERATING,
        current_stage="destination_context",
        current_stage_label="Gathering destination context",
        completed_stages=["traveler_profile"],
        progress_percent=11,
        message="Running backend pipeline stage: Gathering destination context.",
    )

    dumped = progress.model_dump(mode="json")
    reloaded = GenerationProgress.model_validate(dumped)

    assert reloaded == progress


def test_planning_state_serializes_and_deserializes_generation_progress() -> None:
    planning_state = PlanningState(trip_request=_trip_request())
    planning_state.generation_progress = GenerationProgress(
        status=GenerationStageStatus.COMPLETED,
        completed_stages=list(GENERATION_STAGE_KEYS),
        progress_percent=100,
        message="Backend pipeline generation completed.",
    )

    dumped = planning_state.model_dump(mode="json")
    reloaded = PlanningState.model_validate(dumped)

    assert reloaded.generation_progress is not None
    assert reloaded.generation_progress.status == GenerationStageStatus.COMPLETED
    assert reloaded.generation_progress.completed_stages == list(GENERATION_STAGE_KEYS)
    assert reloaded.generation_progress.progress_percent == 100


def test_planning_state_generation_progress_defaults_to_none() -> None:
    planning_state = PlanningState(trip_request=_trip_request())

    assert planning_state.generation_progress is None


def test_older_planning_state_dump_without_generation_progress_still_loads() -> None:
    """A `PlanningState` record persisted before Step 163B existed (no
    `generation_progress` key at all) must still load, with the new field
    honestly defaulting to `None` rather than raising or fabricating a
    progress record.
    """
    planning_state = PlanningState(trip_request=_trip_request())
    dumped = planning_state.model_dump(mode="json")
    del dumped["generation_progress"]

    reloaded = PlanningState.model_validate(dumped)

    assert reloaded.generation_progress is None


def test_generation_progress_has_no_forbidden_travel_fact_fields() -> None:
    field_names = set(GenerationProgress.model_fields.keys())
    overlap = field_names & _FORBIDDEN_FACTUAL_FIELD_NAMES
    assert overlap == set(), f"GenerationProgress has forbidden field(s): {overlap}"


def test_generation_stage_keys_match_documented_allowed_list() -> None:
    assert GENERATION_STAGE_KEYS == (
        "traveler_profile",
        "destination_context",
        "candidate_quality",
        "ai_candidate_shadow",
        "trip_strategy",
        "stay_transport",
        "experience_plan",
        "validation",
        "post_processing",
    )


def test_generation_stage_status_values_are_exactly_the_documented_four() -> None:
    assert {member.value for member in GenerationStageStatus} == {
        "idle",
        "generating",
        "completed",
        "failed",
    }


def test_progress_percent_is_bounded_zero_to_hundred() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GenerationProgress(progress_percent=101)
    with pytest.raises(ValidationError):
        GenerationProgress(progress_percent=-1)
