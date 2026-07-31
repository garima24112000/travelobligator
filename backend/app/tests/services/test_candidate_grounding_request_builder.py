from __future__ import annotations

import ast
import copy
import inspect
from typing import Any

import pytest

from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.models.candidate_grounding import CandidateGroundingResult, CandidateGroundingStatus
from app.models.common import DataStatus, UnavailableDataItem
from app.models.planning_state import DestinationContext, PlanningState, TravelGroupType, TripRequest
from app.services import candidate_grounding_request_builder as builder_module
from app.services.candidate_grounding_request_builder import CandidateGroundingRequestBuilder
from app.services.candidate_grounding_service import CandidateGroundingService

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


def _place(
    name: str | None = "Old Town Waterfront",
    *,
    lat: float | None = 38.7223,
    lng: float | None = -9.1393,
    category: str | None = "neighborhood",
    confidence: float | None = 0.8,
    source: str | None = "openstreetmap_places",
    data_status: str | None = "live",
    place_id: str | None = "way/12345",
) -> dict[str, Any]:
    place: dict[str, Any] = {
        "name": name,
        "category": category,
        "coordinates": {"lat": lat, "lng": lng} if lat is not None and lng is not None else None,
        "source": source,
        "data_status": data_status,
        "confidence": confidence,
        "place_id": place_id,
    }
    return place


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
    candidate_pois: list[dict[str, Any]] | None = None,
    candidate_restaurants: list[dict[str, Any]] | None = None,
    candidate_accommodation_pois: list[dict[str, Any]] | None = None,
    with_destination_context: bool = True,
    **trip_request_overrides: Any,
) -> PlanningState:
    planning_state = PlanningState(trip_request=_trip_request(**trip_request_overrides))
    if with_destination_context:
        planning_state.destination_context = DestinationContext(
            destination_name=planning_state.trip_request.primary_destination,
            candidate_pois=candidate_pois or [],
            candidate_restaurants=candidate_restaurants or [],
            candidate_accommodation_pois=candidate_accommodation_pois or [],
        )
    return planning_state


def _proposal(**overrides: object) -> AICandidateProposal:
    fields: dict[str, object] = {
        "proposal_id": "proposal_001",
        "candidate_name": "Old Town Waterfront",
        "candidate_type": AICandidateType.NEIGHBORHOOD,
        "why_consider": "Locally known for evening walks, may be under-tagged in provider data.",
        "verification_requirements": [
            AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION
        ],
        "confidence": 0.5,
    }
    fields.update(overrides)
    return AICandidateProposal(**fields)


@pytest.fixture()
def builder() -> CandidateGroundingRequestBuilder:
    return CandidateGroundingRequestBuilder()


# ---------------------------------------------------------------------------
# 1. Builds CandidateGroundingRequest with provided proposals unchanged.
# ---------------------------------------------------------------------------


def test_build_request_includes_proposals_unchanged(
    builder: CandidateGroundingRequestBuilder,
) -> None:
    planning_state = _planning_state()
    proposals = [_proposal(proposal_id="proposal_001"), _proposal(proposal_id="proposal_002")]

    request = builder.build_request(planning_state, proposals)

    assert request.proposals == proposals


# ---------------------------------------------------------------------------
# 2. Reads trip_id and destination_name from PlanningState.
# ---------------------------------------------------------------------------


def test_build_request_reads_trip_id_and_destination_name(
    builder: CandidateGroundingRequestBuilder,
) -> None:
    planning_state = _planning_state(primary_destination="Porto, Portugal")

    request = builder.build_request(planning_state, [])

    assert request.trip_id == planning_state.trip_id
    assert request.destination_name == "Porto, Portugal"


# ---------------------------------------------------------------------------
# 3. Converts destination_context.candidate_pois with name + coordinates
#    into ProviderCandidateForGrounding.
# ---------------------------------------------------------------------------


def test_build_request_converts_candidate_pois() -> None:
    builder = CandidateGroundingRequestBuilder()
    planning_state = _planning_state(candidate_pois=[_place(name="Old Town Waterfront")])

    request = builder.build_request(planning_state, [])

    assert len(request.provider_candidates) == 1
    candidate = request.provider_candidates[0]
    assert candidate.name == "Old Town Waterfront"
    assert candidate.category == "neighborhood"
    assert candidate.coordinates.lat == pytest.approx(38.7223)
    assert candidate.provider_name == "openstreetmap_places"
    assert candidate.provider_place_id == "way/12345"
    assert candidate.data_status == DataStatus.LIVE
    assert candidate.confidence == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# 4. Converts restaurants if candidate_restaurants exists.
# ---------------------------------------------------------------------------


def test_build_request_converts_candidate_restaurants(
    builder: CandidateGroundingRequestBuilder,
) -> None:
    planning_state = _planning_state(
        candidate_restaurants=[_place(name="Taverna do Bairro", category=None, place_id="node/1")]
    )

    request = builder.build_request(planning_state, [])

    assert len(request.provider_candidates) == 1
    candidate = request.provider_candidates[0]
    assert candidate.name == "Taverna do Bairro"
    # No explicit category on the raw candidate -> falls back to the
    # source-collection default.
    assert candidate.category == "restaurant"


# ---------------------------------------------------------------------------
# 5. Converts accommodation POIs if candidate_accommodation_pois exists.
# ---------------------------------------------------------------------------


def test_build_request_converts_candidate_accommodation_pois(
    builder: CandidateGroundingRequestBuilder,
) -> None:
    planning_state = _planning_state(
        candidate_accommodation_pois=[
            _place(name="Riverside Guesthouse", category=None, place_id="node/2")
        ]
    )

    request = builder.build_request(planning_state, [])

    assert len(request.provider_candidates) == 1
    candidate = request.provider_candidates[0]
    assert candidate.name == "Riverside Guesthouse"
    assert candidate.category == "accommodation"


# ---------------------------------------------------------------------------
# 6. Skips candidates with missing coordinates.
# ---------------------------------------------------------------------------


def test_build_request_skips_candidates_missing_coordinates(
    builder: CandidateGroundingRequestBuilder,
) -> None:
    planning_state = _planning_state(
        candidate_pois=[_place(name="No Coordinates Place", lat=None, lng=None)]
    )

    request = builder.build_request(planning_state, [])

    assert request.provider_candidates == []
    assert request.provider_candidate_summary["attraction"] == 0


# ---------------------------------------------------------------------------
# 7. Skips candidates with blank/missing name.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank_name", [None, "", "   "])
def test_build_request_skips_candidates_with_blank_name(
    builder: CandidateGroundingRequestBuilder, blank_name: str | None
) -> None:
    planning_state = _planning_state(candidate_pois=[_place(name=blank_name)])

    request = builder.build_request(planning_state, [])

    assert request.provider_candidates == []


# ---------------------------------------------------------------------------
# 8. Uses existing provider/source/id fields when available.
# ---------------------------------------------------------------------------


def test_build_request_uses_existing_provider_and_place_id(
    builder: CandidateGroundingRequestBuilder,
) -> None:
    planning_state = _planning_state(
        candidate_pois=[_place(name="Old Town Waterfront", source="openstreetmap_places", place_id="way/999")]
    )

    request = builder.build_request(planning_state, [])

    candidate = request.provider_candidates[0]
    assert candidate.provider_name == "openstreetmap_places"
    assert candidate.provider_place_id == "way/999"


# ---------------------------------------------------------------------------
# 9. Uses deterministic internal reference when provider id is unavailable,
#    clearly prefixed with destination_context collection/index.
# ---------------------------------------------------------------------------


def test_build_request_uses_internal_reference_when_no_provider_id(
    builder: CandidateGroundingRequestBuilder,
) -> None:
    planning_state = _planning_state(
        candidate_pois=[
            _place(name="First Place", place_id=None, source=None),
            _place(name="Second Place", place_id=None, source=None, lat=10.0, lng=20.0),
        ]
    )

    request = builder.build_request(planning_state, [])

    assert len(request.provider_candidates) == 2
    assert request.provider_candidates[0].provider_place_id == "destination_context.candidate_pois[0]"
    assert request.provider_candidates[0].provider_name == "destination_context"
    assert request.provider_candidates[1].provider_place_id == "destination_context.candidate_pois[1]"


# ---------------------------------------------------------------------------
# 10. Deduplicates repeated candidates deterministically.
# ---------------------------------------------------------------------------


def test_build_request_deduplicates_repeated_candidates(
    builder: CandidateGroundingRequestBuilder,
) -> None:
    planning_state = _planning_state(
        candidate_pois=[
            _place(name="Old Town Waterfront", place_id="way/1"),
            _place(name="old town, waterfront!", place_id="way/1"),
            _place(name="Old Town Waterfront", place_id="way/1"),
        ]
    )

    request = builder.build_request(planning_state, [])

    assert len(request.provider_candidates) == 1
    assert request.provider_candidate_summary["attraction"] == 1


# ---------------------------------------------------------------------------
# 11. provider_candidate_summary counts only included candidates.
# ---------------------------------------------------------------------------


def test_build_request_summary_counts_only_included_candidates(
    builder: CandidateGroundingRequestBuilder,
) -> None:
    planning_state = _planning_state(
        candidate_pois=[
            _place(name="Included Attraction", place_id="way/1"),
            _place(name=None, place_id="way/2"),  # skipped: blank name
            _place(name="No Coords Attraction", lat=None, lng=None, place_id="way/3"),  # skipped
        ],
        candidate_restaurants=[_place(name="Included Restaurant", place_id="node/1")],
        candidate_accommodation_pois=[],
    )

    request = builder.build_request(planning_state, [])

    assert request.provider_candidate_summary == {
        "attraction": 1,
        "restaurant": 1,
        "accommodation": 0,
    }


# ---------------------------------------------------------------------------
# 12. Carries unavailable_data from PlanningState if present.
# ---------------------------------------------------------------------------


def test_build_request_carries_unavailable_data(
    builder: CandidateGroundingRequestBuilder,
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

    request = builder.build_request(planning_state, [])

    assert request.unavailable_data == ["hotel_prices"]


def test_build_request_unavailable_data_empty_when_absent(
    builder: CandidateGroundingRequestBuilder,
) -> None:
    planning_state = _planning_state()

    request = builder.build_request(planning_state, [])

    assert request.unavailable_data == []


# ---------------------------------------------------------------------------
# 13. Does not mutate PlanningState.
# ---------------------------------------------------------------------------


def test_build_request_does_not_mutate_planning_state(
    builder: CandidateGroundingRequestBuilder,
) -> None:
    planning_state = _planning_state(candidate_pois=[_place(name="Old Town Waterfront")])
    before = planning_state.model_copy(deep=True)

    builder.build_request(planning_state, [_proposal()])

    assert planning_state == before


# ---------------------------------------------------------------------------
# 14. Does not call CandidateGroundingService.ground.
# ---------------------------------------------------------------------------


def test_build_request_never_calls_grounding_service(
    monkeypatch: pytest.MonkeyPatch, builder: CandidateGroundingRequestBuilder
) -> None:
    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("CandidateGroundingService.ground must not be called by the builder")

    monkeypatch.setattr(CandidateGroundingService, "ground", _fail)

    planning_state = _planning_state(candidate_pois=[_place(name="Old Town Waterfront")])
    builder.build_request(planning_state, [_proposal()])


def test_builder_module_does_not_import_grounding_service() -> None:
    """Parses actual import statements (via `ast`) rather than searching
    the raw source text -- the module's own docstring legitimately names
    `CandidateGroundingService.ground` in prose to explain that the builder
    never calls it.
    """
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
# 15. Does not import provider adapters, LLM clients, LangGraph, LangSmith,
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
# 16. Does not create GroundedCandidate or RejectedCandidateProposal.
# ---------------------------------------------------------------------------


def test_builder_module_does_not_reference_grounded_or_rejected_models() -> None:
    source = inspect.getsource(builder_module)
    assert "GroundedCandidate" not in source
    assert "RejectedCandidateProposal" not in source


# ---------------------------------------------------------------------------
# 17. Resulting request can be passed into CandidateGroundingService.ground
#     and produce a completed grounding result for an exact supplied
#     proposal/candidate match.
# ---------------------------------------------------------------------------


def test_built_request_can_be_grounded_end_to_end(
    builder: CandidateGroundingRequestBuilder,
) -> None:
    planning_state = _planning_state(candidate_pois=[_place(name="Old Town Waterfront")])
    proposals = [_proposal(candidate_name="Old Town Waterfront")]

    request = builder.build_request(planning_state, proposals)
    result = CandidateGroundingService().ground(request)

    assert isinstance(result, CandidateGroundingResult)
    assert result.status == CandidateGroundingStatus.COMPLETED
    assert len(result.grounded_candidates) == 1


# ---------------------------------------------------------------------------
# 18. Builder output contains no forbidden factual fields.
# ---------------------------------------------------------------------------


def test_build_request_output_has_no_forbidden_factual_keys(
    builder: CandidateGroundingRequestBuilder,
) -> None:
    planning_state = _planning_state(
        candidate_pois=[_place(name="Old Town Waterfront")],
        candidate_restaurants=[_place(name="Taverna do Bairro", place_id="node/1")],
        candidate_accommodation_pois=[_place(name="Riverside Guesthouse", place_id="node/2")],
    )
    request = builder.build_request(planning_state, [_proposal()])
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


# ---------------------------------------------------------------------------
# Extra: builder is safe when destination_context is entirely absent, and
# falls back conservatively (never `live`) when data_status is missing.
# ---------------------------------------------------------------------------


def test_build_request_handles_missing_destination_context(
    builder: CandidateGroundingRequestBuilder,
) -> None:
    planning_state = _planning_state(with_destination_context=False)

    request = builder.build_request(planning_state, [])

    assert request.provider_candidates == []
    assert request.provider_candidate_summary == {
        "attraction": 0,
        "restaurant": 0,
        "accommodation": 0,
    }


def test_build_request_falls_back_conservatively_when_data_status_missing(
    builder: CandidateGroundingRequestBuilder,
) -> None:
    raw_candidate = _place(name="Undocumented Status Place")
    raw_candidate["data_status"] = None
    raw_candidate["confidence"] = None
    planning_state = _planning_state(candidate_pois=[raw_candidate])

    request = builder.build_request(planning_state, [])

    candidate = request.provider_candidates[0]
    assert candidate.data_status == DataStatus.UNAVAILABLE
    assert candidate.confidence == pytest.approx(0.5)
