from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.ai_candidate_proposal import AICandidateProposalRequest, AICandidateProposalTask
from app.models.common import DataStatus, ProviderStatus, ProviderStatusEntry, UnavailableDataItem
from app.models.planning_state import (
    DestinationContext,
    PlanningState,
    TravelerProfile,
    TravelGroupType,
    TripPace,
    TripRequest,
)
from app.services import ai_candidate_proposal_request_builder as builder_module
from app.services.ai_candidate_proposal_request_builder import AICandidateProposalRequestBuilder

_FORBIDDEN_MODEL_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "ticket_price",
    "availability",
    "safety_score",
}


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Lisbon, Portugal",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _planning_state(
    with_destination_context: bool = False,
    candidate_pois: list[dict[str, Any]] | None = None,
    candidate_restaurants: list[dict[str, Any]] | None = None,
    candidate_accommodation_pois: list[dict[str, Any]] | None = None,
    **trip_request_overrides: Any,
) -> PlanningState:
    planning_state = PlanningState(trip_request=_trip_request(**trip_request_overrides))
    if with_destination_context or candidate_pois or candidate_restaurants or candidate_accommodation_pois:
        planning_state.destination_context = DestinationContext(
            destination_name=planning_state.trip_request.primary_destination,
            candidate_pois=candidate_pois or [],
            candidate_restaurants=candidate_restaurants or [],
            candidate_accommodation_pois=candidate_accommodation_pois or [],
        )
    return planning_state


@pytest.fixture()
def builder() -> AICandidateProposalRequestBuilder:
    return AICandidateProposalRequestBuilder()


# ---------------------------------------------------------------------------
# 1. Builds AICandidateProposalRequest.
# ---------------------------------------------------------------------------


def test_build_request_returns_valid_request(builder: AICandidateProposalRequestBuilder) -> None:
    planning_state = _planning_state()

    request = builder.build_request(planning_state)

    assert isinstance(request, AICandidateProposalRequest)
    assert request.task == AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY


# ---------------------------------------------------------------------------
# 2. Reads trip_id and destination_name from PlanningState.
# ---------------------------------------------------------------------------


def test_build_request_reads_trip_id_and_destination_name(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state(primary_destination="Porto, Portugal")

    request = builder.build_request(planning_state)

    assert request.trip_id == planning_state.trip_id
    assert request.destination_name == "Porto, Portugal"


# ---------------------------------------------------------------------------
# 3. Calculates inclusive trip_duration_days correctly.
# ---------------------------------------------------------------------------


def test_build_request_calculates_inclusive_trip_duration_days(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state(start_date="2026-08-10", end_date="2026-08-12")

    request = builder.build_request(planning_state)

    # Aug 10, 11, 12 inclusive = 3 days.
    assert request.trip_duration_days == 3


# ---------------------------------------------------------------------------
# 4. Uses minimum trip_duration_days of 1 for same-day edge cases.
# ---------------------------------------------------------------------------


def test_build_request_uses_minimum_duration_of_one_for_same_day_trip(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state(start_date="2026-08-10", end_date="2026-08-10")

    request = builder.build_request(planning_state)

    assert request.trip_duration_days == 1


# ---------------------------------------------------------------------------
# 5. Includes explicit interests from trip_request if present.
# ---------------------------------------------------------------------------


def test_build_request_includes_explicit_trip_request_interests(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state(interests=["museums", "food"])

    request = builder.build_request(planning_state)

    assert request.interests == ["museums", "food"]


# ---------------------------------------------------------------------------
# 6. Includes explicit must_visit from trip_request if present.
# ---------------------------------------------------------------------------


def test_build_request_includes_explicit_trip_request_must_visit(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state(must_visit=["Belem Tower"])

    request = builder.build_request(planning_state)

    assert request.must_visit == ["Belem Tower"]


# ---------------------------------------------------------------------------
# 7. Includes explicit constraints from trip_request if present.
# ---------------------------------------------------------------------------


def test_build_request_includes_explicit_trip_request_constraints(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state(constraints=["wheelchair accessible"])

    request = builder.build_request(planning_state)

    assert request.constraints == ["wheelchair accessible"]


# ---------------------------------------------------------------------------
# 8. Includes traveler_profile interests/must_visit/constraints only if
#    those fields exist and are explicit.
# ---------------------------------------------------------------------------


def test_build_request_includes_traveler_profile_fields_when_present(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state(interests=["museums"])
    planning_state.traveler_profile = TravelerProfile(
        travel_group_type=TravelGroupType.COUPLE,
        travelers_count=2,
        pace=TripPace.BALANCED,
        interests=["food"],
        must_visit=["Belem Tower"],
        constraints=["quiet mornings"],
    )

    request = builder.build_request(planning_state)

    assert request.interests == ["museums", "food"]
    assert request.must_visit == ["Belem Tower"]
    assert request.constraints == ["quiet mornings"]


def test_build_request_omits_traveler_profile_fields_when_absent(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state(interests=["museums"])
    assert planning_state.traveler_profile is None

    request = builder.build_request(planning_state)

    assert request.interests == ["museums"]
    assert request.must_visit == []
    assert request.constraints == []


# ---------------------------------------------------------------------------
# 9. Deduplicates interests/must_visit/constraints while preserving first
#    occurrence.
# ---------------------------------------------------------------------------


def test_build_request_deduplicates_preserving_first_occurrence(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state(interests=["museums", "food", "museums"])
    planning_state.traveler_profile = TravelerProfile(
        travel_group_type=TravelGroupType.COUPLE,
        travelers_count=2,
        pace=TripPace.BALANCED,
        interests=["food", "art"],
    )

    request = builder.build_request(planning_state)

    assert request.interests == ["museums", "food", "art"]


# ---------------------------------------------------------------------------
# 10. Does not invent interests/must_visit/constraints when absent.
# ---------------------------------------------------------------------------


def test_build_request_leaves_preferences_empty_when_absent(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state()

    request = builder.build_request(planning_state)

    assert request.interests == []
    assert request.must_visit == []
    assert request.constraints == []


# ---------------------------------------------------------------------------
# 11. Builds provider_candidate_summary from destination_context candidate
#     counts.
# ---------------------------------------------------------------------------


def test_build_request_provider_candidate_summary_from_counts(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state(
        candidate_pois=[{"name": "A"}, {"name": "B"}],
        candidate_restaurants=[{"name": "C"}],
        candidate_accommodation_pois=[],
    )

    request = builder.build_request(planning_state)

    assert request.provider_candidate_summary == {
        "attraction": 2,
        "restaurant": 1,
        "accommodation": 0,
    }


# ---------------------------------------------------------------------------
# 12. Handles missing destination_context with zero counts.
# ---------------------------------------------------------------------------


def test_build_request_zero_counts_when_no_destination_context(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state(with_destination_context=False)
    assert planning_state.destination_context is None

    request = builder.build_request(planning_state)

    assert request.provider_candidate_summary == {
        "attraction": 0,
        "restaurant": 0,
        "accommodation": 0,
    }


# ---------------------------------------------------------------------------
# 13. Carries unavailable_data field names from PlanningState.
# ---------------------------------------------------------------------------


def test_build_request_carries_unavailable_data(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state()
    planning_state.unavailable_data.append(
        UnavailableDataItem(
            field="hotel_prices",
            reason="No accommodation provider is connected.",
            data_status=DataStatus.NOT_CONNECTED,
            source="accommodation_provider",
        )
    )

    request = builder.build_request(planning_state)

    assert request.unavailable_data == ["hotel_prices"]


# ---------------------------------------------------------------------------
# 14. Carries provider_status unavailable_fields if present.
# ---------------------------------------------------------------------------


def test_build_request_carries_provider_status_unavailable_fields(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state()
    planning_state.provider_status["routes_provider:routes"] = ProviderStatusEntry(
        provider_name="routes_provider",
        provider_type="routes",
        status=ProviderStatus.NOT_CONNECTED,
        data_status=DataStatus.NOT_CONNECTED,
        unavailable_fields=["route_time_minutes", "distance_km"],
        error_message="routes provider is not connected.",
    )

    request = builder.build_request(planning_state)

    assert request.unavailable_data == ["route_time_minutes", "distance_km"]


# ---------------------------------------------------------------------------
# 15. Deduplicates unavailable_data while preserving order.
# ---------------------------------------------------------------------------


def test_build_request_deduplicates_unavailable_data(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state()
    planning_state.unavailable_data.append(
        UnavailableDataItem(
            field="hotel_prices",
            reason="No accommodation provider is connected.",
            data_status=DataStatus.NOT_CONNECTED,
            source="accommodation_provider",
        )
    )
    planning_state.provider_status["accommodation_provider:accommodations"] = ProviderStatusEntry(
        provider_name="accommodation_provider",
        provider_type="accommodation",
        status=ProviderStatus.NOT_CONNECTED,
        data_status=DataStatus.NOT_CONNECTED,
        unavailable_fields=["hotel_prices", "availability"],
        error_message="No accommodation provider is connected.",
    )

    request = builder.build_request(planning_state)

    assert request.unavailable_data == ["hotel_prices", "availability"]


# ---------------------------------------------------------------------------
# 16. Does not include raw candidate names/place lists in
#     provider_candidate_summary.
# ---------------------------------------------------------------------------


def test_provider_candidate_summary_contains_only_counts(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state(
        candidate_pois=[{"name": "Belem Tower", "place_id": "way/1"}],
    )

    request = builder.build_request(planning_state)

    for value in request.provider_candidate_summary.values():
        assert isinstance(value, int)
    assert "Belem Tower" not in str(request.provider_candidate_summary)
    assert "way/1" not in str(request.provider_candidate_summary)


# ---------------------------------------------------------------------------
# 17. Respects max_candidates argument.
# ---------------------------------------------------------------------------


def test_build_request_respects_max_candidates_argument(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state()

    request = builder.build_request(planning_state, max_candidates=10)

    assert request.max_candidates == 10


def test_build_request_defaults_max_candidates_to_fifteen(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state()

    request = builder.build_request(planning_state)

    assert request.max_candidates == 15


# ---------------------------------------------------------------------------
# 18. Invalid max_candidates still fails through AICandidateProposalRequest
#     validation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("invalid_max_candidates", [0, -1, 26])
def test_build_request_invalid_max_candidates_raises_validation_error(
    builder: AICandidateProposalRequestBuilder, invalid_max_candidates: int
) -> None:
    planning_state = _planning_state()

    with pytest.raises(ValidationError):
        builder.build_request(planning_state, max_candidates=invalid_max_candidates)


# ---------------------------------------------------------------------------
# 19. Does not mutate PlanningState.
# ---------------------------------------------------------------------------


def test_build_request_does_not_mutate_planning_state(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state(
        interests=["museums"],
        candidate_pois=[{"name": "Belem Tower"}],
    )
    planning_state.traveler_profile = TravelerProfile(
        travel_group_type=TravelGroupType.COUPLE,
        travelers_count=2,
        pace=TripPace.BALANCED,
        must_visit=["Belem Tower"],
    )
    before = planning_state.model_copy(deep=True)

    builder.build_request(planning_state)

    assert planning_state == before


# ---------------------------------------------------------------------------
# 20. Does not call AICandidateProposalProvider or
#     NotConnectedAICandidateProposalProvider.
# ---------------------------------------------------------------------------


def test_builder_module_does_not_import_ai_candidate_proposal_provider() -> None:
    source = inspect.getsource(builder_module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
            imported_names.extend(alias.name for alias in node.names)

    assert not any("app.providers" in name for name in imported_names)
    assert not any(name == "AICandidateProposalProvider" for name in imported_names)
    assert not any(name == "NotConnectedAICandidateProposalProvider" for name in imported_names)


# ---------------------------------------------------------------------------
# 21. Does not call CandidateGroundingService.
# ---------------------------------------------------------------------------


def test_builder_module_does_not_import_candidate_grounding_service() -> None:
    source = inspect.getsource(builder_module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
            imported_names.extend(alias.name for alias in node.names)

    assert not any("candidate_grounding_service" in name for name in imported_names)
    assert not any(name == "CandidateGroundingService" for name in imported_names)


# ---------------------------------------------------------------------------
# 22. Does not create AICandidateProposal, GroundedCandidate, or
#     RejectedCandidateProposal.
# ---------------------------------------------------------------------------


def test_builder_module_does_not_reference_proposal_or_grounding_result_models() -> None:
    """Checks for actual construction calls, not bare name mentions -- the
    module's own docstring legitimately names `GroundedCandidate`/
    `RejectedCandidateProposal` in prose to explain that the builder never
    creates them.
    """
    source = inspect.getsource(builder_module)
    assert "AICandidateProposal(" not in source
    assert "GroundedCandidate(" not in source
    assert "RejectedCandidateProposal(" not in source


# ---------------------------------------------------------------------------
# 23. Does not import provider adapters, LLM clients, LangGraph, LangSmith,
#     httpx, requests, OpenAI, Anthropic, Gemini, or PlanningOrchestrator.
# ---------------------------------------------------------------------------


def test_builder_module_has_no_disallowed_imports() -> None:
    source = inspect.getsource(builder_module)
    tree = ast.parse(source)

    disallowed_substrings = (
        "langgraph",
        "langsmith",
        "httpx",
        "requests",
        "openai",
        "anthropic",
        "gemini",
        "google.generativeai",
        "app.providers",
        "planning_orchestrator",
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


# ---------------------------------------------------------------------------
# 24. Builder output contains no forbidden factual fields.
# ---------------------------------------------------------------------------


def test_build_request_output_has_no_forbidden_factual_keys(
    builder: AICandidateProposalRequestBuilder,
) -> None:
    planning_state = _planning_state(
        interests=["museums"],
        must_visit=["Belem Tower"],
        constraints=["wheelchair accessible"],
        candidate_pois=[{"name": "Belem Tower"}],
        candidate_restaurants=[{"name": "Taverna do Bairro"}],
        candidate_accommodation_pois=[{"name": "Riverside Guesthouse"}],
    )
    planning_state.unavailable_data.append(
        UnavailableDataItem(
            field="hotel_prices",
            reason="No accommodation provider is connected.",
            data_status=DataStatus.NOT_CONNECTED,
            source="accommodation_provider",
        )
    )

    request = builder.build_request(planning_state)
    dumped = request.model_dump()

    def _collect_keys(value: object, keys: set[str]) -> None:
        if isinstance(value, dict):
            keys.update(value.keys())
            for nested in value.values():
                _collect_keys(nested, keys)
        elif isinstance(value, list):
            for item in value:
                _collect_keys(item, keys)

    all_keys: set[str] = set()
    _collect_keys(dumped, all_keys)
    overlap = all_keys & _FORBIDDEN_MODEL_FIELD_NAMES
    assert overlap == set(), f"Builder output has forbidden key(s): {overlap}"
