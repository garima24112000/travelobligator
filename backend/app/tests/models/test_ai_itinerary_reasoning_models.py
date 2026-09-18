from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningBatch,
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningRequest,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    CandidateOrigin,
    ItineraryCandidateReference,
    ItineraryReasoningCandidatePlacement,
    ItineraryReasoningCategory,
    ItineraryReasoningDayPlan,
    ItineraryReasoningStrategy,
    ItineraryReasoningTimeWindow,
    TravelerContextSummary,
    validate_result_against_request,
)
from app.models.common import DataStatus, GeoPoint

# Tests for the Section 193A itinerary-reasoning contract models
# (docs/14_backend_architecture.md section 141). No LLM/provider call is
# made anywhere in this file -- these are pure pydantic construction/
# validation tests.

_FORBIDDEN_MODEL_FIELD_NAMES = {
    "rating",
    "price",
    "opening_hours",
    "route_time",
    "route_distance",
    "booking_url",
    "review_count",
    "ticket_price",
    "availability",
    "safety_score",
}


def _candidate(candidate_id: str = "openstreetmap_places:way/1", **overrides: object) -> ItineraryCandidateReference:
    fields: dict[str, object] = {
        "candidate_id": candidate_id,
        "name": "Torre de Belem",
        "category": ItineraryReasoningCategory.ATTRACTION,
        "provider_name": "openstreetmap_places",
        "provider_place_id": "way/24341353",
        "coordinates": GeoPoint(lat=38.6916, lng=-9.2160),
        "data_status": DataStatus.LIVE,
        "quality_score": 0.73,
        "quality_tier": "good_candidate",
        "origin": CandidateOrigin.BROAD_PROVIDER_DISCOVERY,
    }
    fields.update(overrides)
    return ItineraryCandidateReference(**fields)


def _traveler_context(**overrides: object) -> TravelerContextSummary:
    fields: dict[str, object] = {
        "travelers_count": 2,
        "travel_group_type": "couple",
        "pace": "balanced",
        "interests": ["food", "history"],
    }
    fields.update(overrides)
    return TravelerContextSummary(**fields)


def _request(**overrides: object) -> AIItineraryReasoningRequest:
    fields: dict[str, object] = {
        "trip_id": "trip_001",
        "destination_name": "Lisbon, Portugal",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "trip_duration_days": 3,
        "traveler_context": _traveler_context(),
        "allowed_candidates": [_candidate()],
    }
    fields.update(overrides)
    return AIItineraryReasoningRequest(**fields)


def _guardrail(passed: bool, blocked_reasons: list[str] | None = None) -> AIItineraryReasoningGuardrailReport:
    return AIItineraryReasoningGuardrailReport(
        passed=passed, blocked_reasons=blocked_reasons or ([] if passed else ["blocked"])
    )


def _day(day_index: int = 1, candidate_ids: list[str] | None = None, **overrides: object) -> ItineraryReasoningDayPlan:
    fields: dict[str, object] = {
        "day_index": day_index,
        "candidate_ids": candidate_ids or ["openstreetmap_places:way/1"],
        "rationale": "Fits the traveler's stated interest in history.",
    }
    fields.update(overrides)
    return ItineraryReasoningDayPlan(**fields)


def _strategy(**overrides: object) -> ItineraryReasoningStrategy:
    fields: dict[str, object] = {
        "summary": "A relaxed history-and-food focused three-day plan.",
        "pace": "balanced",
        "reason": "Matches the traveler's stated pace and interests.",
    }
    fields.update(overrides)
    return ItineraryReasoningStrategy(**fields)


def _completed_result(**overrides: object) -> AIItineraryReasoningResult:
    fields: dict[str, object] = {
        "status": AIItineraryReasoningStatus.COMPLETED,
        "strategy": _strategy(),
        "days": [_day()],
        "guardrail_report": _guardrail(True),
        "confidence": 0.6,
    }
    fields.update(overrides)
    return AIItineraryReasoningResult(**fields)


# ---------------------------------------------------------------------------
# 1. Candidate reference: valid, blank-rejecting.
# ---------------------------------------------------------------------------


def test_valid_candidate_reference_is_accepted() -> None:
    candidate = _candidate()
    assert candidate.origin == CandidateOrigin.BROAD_PROVIDER_DISCOVERY


@pytest.mark.parametrize("field", ["candidate_id", "name", "provider_name", "provider_place_id"])
def test_candidate_reference_rejects_blank_required_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        _candidate(**{field: "   "})


def test_candidate_reference_carries_no_forbidden_provider_fact_fields() -> None:
    field_names = set(ItineraryCandidateReference.model_fields.keys())
    forbidden = {"price", "rating", "availability", "booking_url", "opening_hours"}
    assert field_names & forbidden == set()


# ---------------------------------------------------------------------------
# 2. Request: allowed_candidates uniqueness, blank rejection.
# ---------------------------------------------------------------------------


def test_request_rejects_duplicate_candidate_ids() -> None:
    with pytest.raises(ValidationError):
        _request(allowed_candidates=[_candidate(), _candidate()])


def test_request_allowed_candidate_ids_helper() -> None:
    request = _request()
    assert request.allowed_candidate_ids() == {"openstreetmap_places:way/1"}


def test_request_default_reasoning_instructions_are_populated() -> None:
    request = _request()
    assert len(request.reasoning_instructions) > 0


@pytest.mark.parametrize("field", ["trip_id", "destination_name"])
def test_request_rejects_blank_required_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        _request(**{field: "   "})


# ---------------------------------------------------------------------------
# 3. Day plan: candidate_ids non-blank/unique, placements reference own day.
# ---------------------------------------------------------------------------


def test_day_plan_rejects_duplicate_candidate_within_day() -> None:
    with pytest.raises(ValidationError):
        _day(candidate_ids=["candidate-1", "candidate-1"])


def test_day_plan_requires_at_least_one_candidate() -> None:
    with pytest.raises(ValidationError):
        ItineraryReasoningDayPlan(day_index=1, candidate_ids=[], rationale="Some rationale.")


def test_day_plan_placement_must_reference_own_candidate() -> None:
    with pytest.raises(ValidationError):
        ItineraryReasoningDayPlan(
            day_index=1,
            candidate_ids=["candidate-1"],
            rationale="Some rationale.",
            approximate_structure=[
                ItineraryReasoningCandidatePlacement(
                    candidate_id="candidate-not-on-this-day",
                    time_window=ItineraryReasoningTimeWindow.MORNING,
                )
            ],
        )


def test_day_plan_placement_referencing_own_candidate_is_valid() -> None:
    day = ItineraryReasoningDayPlan(
        day_index=1,
        candidate_ids=["candidate-1"],
        rationale="Some rationale.",
        approximate_structure=[
            ItineraryReasoningCandidatePlacement(
                candidate_id="candidate-1", time_window=ItineraryReasoningTimeWindow.MORNING
            )
        ],
    )
    assert day.approximate_structure[0].time_window == ItineraryReasoningTimeWindow.MORNING


@pytest.mark.parametrize(
    "forbidden_text",
    [
        "This place has a great rating",
        "The price is low",
        "opening_hours: 9am-5pm",
        "booking_url available soon",
        "exact travel time is 10 minutes",
        "at 09:15",
    ],
)
def test_day_plan_rationale_rejects_forbidden_claims(forbidden_text: str) -> None:
    with pytest.raises(ValidationError):
        _day(rationale=forbidden_text)


# ---------------------------------------------------------------------------
# 4. Result status consistency (mirrors every other AI-facing result model).
# ---------------------------------------------------------------------------


def test_completed_result_requires_days() -> None:
    with pytest.raises(ValidationError):
        AIItineraryReasoningResult(
            status=AIItineraryReasoningStatus.COMPLETED,
            strategy=_strategy(),
            days=[],
            guardrail_report=_guardrail(True),
            confidence=0.5,
        )


def test_completed_result_requires_strategy() -> None:
    with pytest.raises(ValidationError):
        AIItineraryReasoningResult(
            status=AIItineraryReasoningStatus.COMPLETED,
            strategy=None,
            days=[_day()],
            guardrail_report=_guardrail(True),
            confidence=0.5,
        )


def test_completed_result_is_valid() -> None:
    result = _completed_result()
    assert result.status == AIItineraryReasoningStatus.COMPLETED


def test_not_connected_result_requires_zero_confidence_and_no_days() -> None:
    with pytest.raises(ValidationError):
        AIItineraryReasoningResult(
            status=AIItineraryReasoningStatus.NOT_CONNECTED,
            days=[_day()],
            guardrail_report=_guardrail(False, ["not connected"]),
            confidence=0.0,
        )
    with pytest.raises(ValidationError):
        AIItineraryReasoningResult(
            status=AIItineraryReasoningStatus.NOT_CONNECTED,
            days=[],
            guardrail_report=_guardrail(False, ["not connected"]),
            confidence=0.5,
        )


def test_rejected_result_requires_blocked_reasons() -> None:
    with pytest.raises(ValidationError):
        AIItineraryReasoningResult(
            status=AIItineraryReasoningStatus.REJECTED,
            days=[],
            guardrail_report=AIItineraryReasoningGuardrailReport.model_construct(
                passed=False, blocked_reasons=[], checked_fields=[]
            ),
            confidence=0.0,
        )


# ---------------------------------------------------------------------------
# 5. Candidate-ID safety validator (Task 7) -- unknown/duplicate/invalid day.
# ---------------------------------------------------------------------------


def test_validator_accepts_result_referencing_only_allowed_candidates() -> None:
    request = _request()
    result = _completed_result()
    assert validate_result_against_request(request, result) == []


def test_validator_rejects_unknown_candidate_id() -> None:
    request = _request()
    result = _completed_result(days=[_day(candidate_ids=["made-up-place"])])
    violations = validate_result_against_request(request, result)
    assert any("made-up-place" in v and "not in request.allowed_candidates" in v for v in violations)


def test_validator_rejects_candidate_on_more_than_one_day() -> None:
    request = _request(
        allowed_candidates=[_candidate(), _candidate(candidate_id="openstreetmap_places:way/2")]
    )
    result = _completed_result(
        days=[
            _day(day_index=1, candidate_ids=["openstreetmap_places:way/1"]),
            _day(day_index=2, candidate_ids=["openstreetmap_places:way/1"]),
        ]
    )
    violations = validate_result_against_request(request, result)
    assert any("appears on both day" in v for v in violations)


def test_validator_rejects_day_index_beyond_trip_duration() -> None:
    request = _request(trip_duration_days=3)
    result = _completed_result(days=[_day(day_index=5)])
    violations = validate_result_against_request(request, result)
    assert any("exceeds trip_duration_days" in v for v in violations)


def test_validator_rejects_duplicate_day_index() -> None:
    request = _request(
        allowed_candidates=[_candidate(), _candidate(candidate_id="openstreetmap_places:way/2")]
    )
    result = _completed_result(
        days=[
            _day(day_index=1, candidate_ids=["openstreetmap_places:way/1"]),
            _day(day_index=1, candidate_ids=["openstreetmap_places:way/2"]),
        ]
    )
    violations = validate_result_against_request(request, result)
    assert any("used by more than one day" in v for v in violations)


def test_batch_construction_raises_for_unsafe_result() -> None:
    request = _request()
    unsafe_result = _completed_result(days=[_day(candidate_ids=["made-up-place"])])
    with pytest.raises(ValidationError):
        AIItineraryReasoningBatch(request=request, result=unsafe_result)


def test_batch_construction_succeeds_for_safe_result() -> None:
    request = _request()
    batch = AIItineraryReasoningBatch(request=request, result=_completed_result())
    assert batch.result is not None


def test_batch_without_result_is_valid() -> None:
    batch = AIItineraryReasoningBatch(request=_request())
    assert batch.result is None


def test_batch_skips_validation_for_non_completed_result() -> None:
    """A not_connected/rejected/skipped result carries no days -- nothing
    to validate, and it must never be rejected just because it doesn't
    reference any allowed candidate (it has none)."""
    request = _request()
    not_connected_result = AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.NOT_CONNECTED,
        days=[],
        guardrail_report=_guardrail(False, ["No provider connected."]),
        confidence=0.0,
    )
    batch = AIItineraryReasoningBatch(request=request, result=not_connected_result)
    assert batch.result is not None


# ---------------------------------------------------------------------------
# 6. No forbidden factual field names anywhere in the contract.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_cls",
    [
        ItineraryCandidateReference,
        AIItineraryReasoningRequest,
        ItineraryReasoningDayPlan,
        AIItineraryReasoningResult,
        AIItineraryReasoningBatch,
    ],
)
def test_models_never_contain_forbidden_field_names(model_cls) -> None:
    field_names = set(model_cls.model_fields.keys())
    overlap = field_names & _FORBIDDEN_MODEL_FIELD_NAMES
    assert overlap == set(), f"{model_cls.__name__} has forbidden field(s): {overlap}"


# ---------------------------------------------------------------------------
# 7. PlanningState storage (Task 14): backward compatible, default None.
# ---------------------------------------------------------------------------


def test_planning_state_defaults_ai_itinerary_reasoning_result_to_none() -> None:
    from app.models.planning_state import PlanningState, TravelGroupType, TripRequest

    trip_request = TripRequest(
        primary_destination="Lisbon, Portugal",
        start_date="2026-08-10",
        end_date="2026-08-12",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )
    planning_state = PlanningState(trip_request=trip_request)
    assert planning_state.ai_itinerary_reasoning_result is None


def test_old_planning_state_payload_without_the_field_still_validates() -> None:
    """Simulates a PlanningState JSON payload persisted before this
    field existed -- it must still deserialize cleanly (backward
    compatibility, Task 14)."""
    from app.models.planning_state import PlanningState, TravelGroupType, TripRequest

    trip_request = TripRequest(
        primary_destination="Lisbon, Portugal",
        start_date="2026-08-10",
        end_date="2026-08-12",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )
    old_payload = PlanningState(trip_request=trip_request).model_dump(mode="json")
    assert "ai_itinerary_reasoning_result" in old_payload
    del old_payload["ai_itinerary_reasoning_result"]

    reconstructed = PlanningState.model_validate(old_payload)
    assert reconstructed.ai_itinerary_reasoning_result is None


def test_planning_state_accepts_a_valid_completed_batch_result() -> None:
    from app.models.planning_state import PlanningState, TravelGroupType, TripRequest

    trip_request = TripRequest(
        primary_destination="Lisbon, Portugal",
        start_date="2026-08-10",
        end_date="2026-08-12",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )
    planning_state = PlanningState(
        trip_request=trip_request, ai_itinerary_reasoning_result=_completed_result()
    )
    assert planning_state.ai_itinerary_reasoning_result is not None
    assert planning_state.ai_itinerary_reasoning_result.status == AIItineraryReasoningStatus.COMPLETED


def test_module_does_not_import_planning_state_or_llm_clients() -> None:
    """This module must stay a leaf contract model -- `planning_state.py`
    imports FROM it (for `ai_itinerary_reasoning_result`), never the other
    way around, or the two modules would form a circular import."""
    import ast
    import inspect

    import app.models.ai_itinerary_reasoning as module

    source = inspect.getsource(module)
    tree = ast.parse(source)

    disallowed_substrings = (
        "planning_state",
        "langgraph",
        "langsmith",
        "httpx",
        "requests",
        "openai",
        "anthropic",
        "gemini",
        "app.providers",
        "app.services",
    )

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"
