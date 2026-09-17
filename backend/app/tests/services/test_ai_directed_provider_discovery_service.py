from __future__ import annotations

from typing import Any, Callable

from app.models.ai_candidate_proposal import (
    AICandidatePriorityHint,
    AICandidateProposal,
    AICandidateProposalType,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.models.ai_provider_discovery import AIProviderDiscoveryAttemptStatus
from app.models.candidate_grounding import ProviderCandidateForGrounding
from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.models.providers import NormalizedPlace, ProviderResponse
from app.providers.base import PlacesProvider, failed_response, not_connected_response, unavailable_response
from app.providers.gateway import ProviderGateway
from app.services.ai_directed_provider_discovery_service import AIDirectedProviderDiscoveryService

# Tests for the Section 192 AI-directed provider discovery service
# (docs/14_backend_architecture.md section 138). Every provider call here
# goes through an in-file fake `PlacesProvider` double -- no real network
# call, no real OpenStreetMap/Nominatim request.


class _FakePlacesProvider(PlacesProvider):
    provider_name = "fake_places_provider"

    def __init__(self, handler: Callable[[str, str], ProviderResponse[Any]]) -> None:
        self._handler = handler
        self.calls: list[tuple[str, str]] = []

    def search_must_visit_place(self, must_visit_term, primary_destination, filters=None):
        self.calls.append((must_visit_term, primary_destination))
        return self._handler(must_visit_term, primary_destination)


class _RaisingPlacesProvider(PlacesProvider):
    provider_name = "raising_places_provider"

    def search_must_visit_place(self, must_visit_term, primary_destination, filters=None):
        raise RuntimeError("simulated provider crash")


def _normalized_place(name: str = "Mercado da Ribeira", place_id: str = "way/123") -> NormalizedPlace:
    return NormalizedPlace(
        place_id=place_id,
        name=name,
        category="marketplace",
        coordinates=GeoPoint(lat=38.7071, lng=-9.1456),
        address="Av. 24 de Julho, Lisbon",
        source="openstreetmap_places",
        data_status=DataStatus.LIVE,
        confidence=0.5,
    )


def _found_response(place: NormalizedPlace | None = None) -> ProviderResponse[Any]:
    place = place or _normalized_place()
    return ProviderResponse[list[NormalizedPlace]](
        provider_name="openstreetmap_places",
        provider_type=PlacesProvider.provider_type,
        status=ProviderStatus.SUCCESS,
        data_status=DataStatus.LIVE,
        data=[place],
        confidence=0.5,
        message="found",
    )


def _gateway_returning(handler: Callable[[str, str], ProviderResponse[Any]]) -> ProviderGateway:
    return ProviderGateway(places=_FakePlacesProvider(handler))


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


def _planning_state(**trip_request_overrides: Any) -> PlanningState:
    return PlanningState(trip_request=_trip_request(**trip_request_overrides))


def _named_place(proposal_id: str, candidate_name: str, **overrides: Any) -> AICandidateProposal:
    fields: dict[str, Any] = {
        "proposal_id": proposal_id,
        "candidate_name": candidate_name,
        "candidate_type": AICandidateType.ATTRACTION,
        "why_consider": "A landmark worth checking.",
        "verification_requirements": [
            AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION
        ],
        "confidence": 0.5,
    }
    fields.update(overrides)
    return AICandidateProposal(**fields)


def _discovery_query(proposal_id: str, search_query: str, **overrides: Any) -> AICandidateProposal:
    fields: dict[str, Any] = {
        "proposal_id": proposal_id,
        "proposal_type": AICandidateProposalType.DISCOVERY_QUERY,
        "candidate_name": None,
        "search_query": search_query,
        "candidate_type": AICandidateType.FOOD_AREA,
        "why_consider": "Matches traveler's stated interest in food.",
        "verification_requirements": [
            AICandidateVerificationRequirement.MUST_NOT_USE_WITHOUT_PROVIDER_MATCH
        ],
        "confidence": 0.5,
    }
    fields.update(overrides)
    return AICandidateProposal(**fields)


def _provider_candidate(**overrides: Any) -> ProviderCandidateForGrounding:
    fields: dict[str, Any] = {
        "provider_name": "openstreetmap_places",
        "provider_place_id": "way/1",
        "name": "Belem Tower",
        "category": "attraction",
        "coordinates": GeoPoint(lat=38.6916, lng=-9.2160),
        "data_status": DataStatus.LIVE,
        "confidence": 0.9,
    }
    fields.update(overrides)
    return ProviderCandidateForGrounding(**fields)


# ---------------------------------------------------------------------------
# 1. Discovery query resolves via a real (fake) provider match.
# ---------------------------------------------------------------------------


def test_discovery_query_resolves_to_a_provider_match() -> None:
    proposal = _discovery_query("proposal_001", "historic food market")
    gateway = _gateway_returning(lambda term, dest: _found_response())
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), [proposal], provider_candidates=[])

    assert result.matched_count == 1
    assert result.searched_count == 1
    matches = result.matches_by_proposal_id()
    assert "proposal_001" in matches
    match = matches["proposal_001"]
    assert match.provider_place_id == "way/123"
    assert match.name == "Mercado da Ribeira"
    # Coordinates/name/provider fields must come from the fake provider
    # response, never from the proposal's own search_query text.
    assert match.name != proposal.search_query


def test_discovery_query_search_uses_search_query_and_destination() -> None:
    proposal = _discovery_query("proposal_001", "historic food market")
    places = _FakePlacesProvider(lambda term, dest: _found_response())
    gateway = ProviderGateway(places=places)
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    service.discover(_planning_state(), [proposal], provider_candidates=[])

    assert places.calls == [("historic food market", "Lisbon, Portugal")]


# ---------------------------------------------------------------------------
# 2. Discovery query with zero provider results.
# ---------------------------------------------------------------------------


def test_discovery_query_zero_results_stays_honestly_unmatched() -> None:
    proposal = _discovery_query("proposal_001", "obscure imaginary market")
    gateway = _gateway_returning(
        lambda term, dest: unavailable_response(
            "openstreetmap_places", PlacesProvider.provider_type, unavailable_fields=["must_visit_place"]
        )
    )
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), [proposal], provider_candidates=[])

    assert result.matched_count == 0
    assert result.attempts[0].status == AIProviderDiscoveryAttemptStatus.NOT_FOUND
    assert result.attempts[0].match is None
    assert result.matches_by_proposal_id() == {}


# ---------------------------------------------------------------------------
# 3. Provider failure is recorded honestly, never fabricated.
# ---------------------------------------------------------------------------


def test_provider_failed_response_is_recorded_honestly() -> None:
    proposal = _discovery_query("proposal_001", "historic food market")
    gateway = _gateway_returning(
        lambda term, dest: failed_response("openstreetmap_places", PlacesProvider.provider_type)
    )
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), [proposal], provider_candidates=[])

    assert result.attempts[0].status == AIProviderDiscoveryAttemptStatus.PROVIDER_FAILED
    assert result.matches_by_proposal_id() == {}


def test_provider_not_connected_response_is_recorded_honestly() -> None:
    proposal = _discovery_query("proposal_001", "historic food market")
    gateway = _gateway_returning(
        lambda term, dest: not_connected_response("openstreetmap_places", PlacesProvider.provider_type)
    )
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), [proposal], provider_candidates=[])

    assert result.attempts[0].status == AIProviderDiscoveryAttemptStatus.PROVIDER_NOT_CONNECTED


def test_provider_raising_exception_never_crashes_generation() -> None:
    proposal = _discovery_query("proposal_001", "historic food market")
    gateway = ProviderGateway(places=_RaisingPlacesProvider())
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), [proposal], provider_candidates=[])  # must not raise

    assert result.attempts[0].status == AIProviderDiscoveryAttemptStatus.PROVIDER_FAILED
    assert result.matches_by_proposal_id() == {}


# ---------------------------------------------------------------------------
# 4. Named-place fallback lookup.
# ---------------------------------------------------------------------------


def test_named_place_with_existing_broad_match_is_never_searched() -> None:
    proposal = _named_place("proposal_001", "Belem Tower")
    places = _FakePlacesProvider(lambda term, dest: _found_response())
    gateway = ProviderGateway(places=places)
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(
        _planning_state(), [proposal], provider_candidates=[_provider_candidate(name="Belem Tower")]
    )

    assert places.calls == []
    assert result.attempts == []
    assert result.searched_count == 0


def test_named_place_without_broad_match_falls_back_to_provider_lookup() -> None:
    proposal = _named_place("proposal_001", "Miradouro Obscuro")
    gateway = _gateway_returning(
        lambda term, dest: _found_response(_normalized_place(name="Miradouro Obscuro", place_id="way/999"))
    )
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(
        _planning_state(), [proposal], provider_candidates=[_provider_candidate(name="Belem Tower")]
    )

    assert result.matched_count == 1
    match = result.matches_by_proposal_id()["proposal_001"]
    assert match.provider_place_id == "way/999"


def test_named_place_ambiguous_broad_match_also_falls_back() -> None:
    proposal = _named_place("proposal_001", "Belem Tower")
    gateway = _gateway_returning(lambda term, dest: _found_response())
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(
        _planning_state(),
        [proposal],
        provider_candidates=[
            _provider_candidate(provider_place_id="way/1", name="Belem Tower"),
            _provider_candidate(provider_place_id="way/2", name="belem tower"),
        ],
    )

    assert result.matched_count == 1  # the fallback lookup, not the ambiguous broad pool


def test_named_place_fallback_lookup_failure_stays_unmatched() -> None:
    proposal = _named_place("proposal_001", "Miradouro Obscuro")
    gateway = _gateway_returning(
        lambda term, dest: unavailable_response("openstreetmap_places", PlacesProvider.provider_type)
    )
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), [proposal], provider_candidates=[])

    assert result.matched_count == 0
    assert result.attempts[0].status == AIProviderDiscoveryAttemptStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# 5. Search bound.
# ---------------------------------------------------------------------------


def test_search_bound_limits_number_of_real_lookups() -> None:
    proposals = [_discovery_query(f"proposal_{i:03d}", f"query {i}") for i in range(5)]
    places = _FakePlacesProvider(lambda term, dest: _found_response())
    gateway = ProviderGateway(places=places)
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), proposals, provider_candidates=[], max_searches=2)

    assert len(places.calls) == 2
    assert result.searched_count == 2
    not_searched = [
        a for a in result.attempts if a.status == AIProviderDiscoveryAttemptStatus.NOT_SEARCHED
    ]
    assert len(not_searched) == 3


def test_search_bound_zero_means_no_lookups_at_all() -> None:
    proposal = _discovery_query("proposal_001", "historic food market")
    places = _FakePlacesProvider(lambda term, dest: _found_response())
    gateway = ProviderGateway(places=places)
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), [proposal], provider_candidates=[], max_searches=0)

    assert places.calls == []
    assert result.attempts[0].status == AIProviderDiscoveryAttemptStatus.NOT_SEARCHED


def test_search_bound_defaults_from_settings(monkeypatch) -> None:
    from app.core.config import Settings
    import app.services.ai_directed_provider_discovery_service as module

    monkeypatch.setattr(module, "get_settings", lambda: Settings(ai_directed_provider_discovery_max_searches=1))
    proposals = [_discovery_query(f"proposal_{i:03d}", f"query {i}") for i in range(3)]
    places = _FakePlacesProvider(lambda term, dest: _found_response())
    gateway = ProviderGateway(places=places)
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), proposals, provider_candidates=[])

    assert len(places.calls) == 1
    assert result.searched_count == 1


# ---------------------------------------------------------------------------
# 6. Provider facts only -- never AI proposal fields.
# ---------------------------------------------------------------------------


def test_match_fields_originate_from_provider_response_not_proposal() -> None:
    proposal = _discovery_query("proposal_001", "totally different search text")
    place = _normalized_place(name="Real Provider Name", place_id="way/555")
    gateway = _gateway_returning(lambda term, dest: _found_response(place))
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), [proposal], provider_candidates=[])

    match = result.matches_by_proposal_id()["proposal_001"]
    assert match.name == "Real Provider Name"
    assert match.provider_place_id == "way/555"
    assert match.coordinates == place.coordinates
    assert match.name != proposal.search_query


# ---------------------------------------------------------------------------
# 7. Multiple proposals, mixed outcomes.
# ---------------------------------------------------------------------------


def test_mixed_proposals_produce_independent_outcomes() -> None:
    already_matched = _named_place("proposal_001", "Belem Tower")
    needs_lookup = _discovery_query("proposal_002", "historic food market")

    def handler(term: str, dest: str) -> ProviderResponse[Any]:
        return _found_response()

    places = _FakePlacesProvider(handler)
    gateway = ProviderGateway(places=places)
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(
        _planning_state(),
        [already_matched, needs_lookup],
        provider_candidates=[_provider_candidate(name="Belem Tower")],
    )

    assert places.calls == [("historic food market", "Lisbon, Portugal")]
    assert result.matched_count == 1
    assert "proposal_002" in result.matches_by_proposal_id()
    assert "proposal_001" not in result.matches_by_proposal_id()


# ---------------------------------------------------------------------------
# Section 192.1: the search bound limits real provider calls, not merely
# proposal/eligible counts, and selection is deterministic.
# ---------------------------------------------------------------------------


def test_exact_limit_fifteen_eligible_five_max_calls_exactly_five() -> None:
    proposals = [_discovery_query(f"proposal_{i:03d}", f"query {i}") for i in range(15)]
    places = _FakePlacesProvider(lambda term, dest: _found_response())
    gateway = ProviderGateway(places=places)
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), proposals, provider_candidates=[], max_searches=5)

    assert len(places.calls) == 5
    assert result.searched_count == 5
    not_searched = [a for a in result.attempts if a.status == AIProviderDiscoveryAttemptStatus.NOT_SEARCHED]
    assert len(not_searched) == 10


def test_under_limit_three_eligible_five_max_calls_exactly_three() -> None:
    proposals = [_discovery_query(f"proposal_{i:03d}", f"query {i}") for i in range(3)]
    places = _FakePlacesProvider(lambda term, dest: _found_response())
    gateway = ProviderGateway(places=places)
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), proposals, provider_candidates=[], max_searches=5)

    assert len(places.calls) == 3
    assert result.searched_count == 3
    assert all(a.status != AIProviderDiscoveryAttemptStatus.NOT_SEARCHED for a in result.attempts)


def test_named_place_and_discovery_query_share_one_budget() -> None:
    """3 named_place fallback lookups + 7 discovery_query lookups, with
    max_searches=5, must total <= 5 real calls -- proving the two proposal
    types are not given independent budgets.
    """
    named_place_proposals = [
        _named_place(f"named_{i:03d}", f"Obscure Place {i}") for i in range(3)
    ]
    discovery_query_proposals = [
        _discovery_query(f"discovery_{i:03d}", f"query {i}") for i in range(7)
    ]
    proposals = named_place_proposals + discovery_query_proposals
    places = _FakePlacesProvider(lambda term, dest: _found_response())
    gateway = ProviderGateway(places=places)
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), proposals, provider_candidates=[], max_searches=5)

    assert len(places.calls) == 5
    assert result.searched_count == 5
    searched_proposal_ids = {
        a.proposal_id for a in result.attempts if a.status != AIProviderDiscoveryAttemptStatus.NOT_SEARCHED
    }
    assert len(searched_proposal_ids) == 5


def test_deterministic_selection_prioritizes_high_before_medium_before_low() -> None:
    proposals = [
        _discovery_query("low_a", "query low a", priority_hint=AICandidatePriorityHint.LOW),
        _discovery_query("high_a", "query high a", priority_hint=AICandidatePriorityHint.HIGH),
        _discovery_query("medium_a", "query medium a", priority_hint=AICandidatePriorityHint.MEDIUM),
        _discovery_query("high_b", "query high b", priority_hint=AICandidatePriorityHint.HIGH),
        _discovery_query("unknown_a", "query unknown a", priority_hint=AICandidatePriorityHint.UNKNOWN),
        _discovery_query("medium_b", "query medium b", priority_hint=AICandidatePriorityHint.MEDIUM),
        _discovery_query("low_b", "query low b", priority_hint=AICandidatePriorityHint.LOW),
    ]
    places = _FakePlacesProvider(lambda term, dest: _found_response())
    gateway = ProviderGateway(places=places)
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), proposals, provider_candidates=[], max_searches=3)

    searched_ids = {
        a.proposal_id for a in result.attempts if a.status != AIProviderDiscoveryAttemptStatus.NOT_SEARCHED
    }
    # Both high-priority proposals, then the first medium (original order
    # is the tie-break) -- never the low/unknown ones.
    assert searched_ids == {"high_a", "high_b", "medium_a"}


def test_deterministic_selection_is_identical_across_repeated_calls() -> None:
    proposals = [
        _discovery_query(f"proposal_{i:03d}", f"query {i}", priority_hint=priority)
        for i, priority in enumerate(
            [
                AICandidatePriorityHint.LOW,
                AICandidatePriorityHint.HIGH,
                AICandidatePriorityHint.MEDIUM,
                AICandidatePriorityHint.HIGH,
                AICandidatePriorityHint.UNKNOWN,
                AICandidatePriorityHint.MEDIUM,
                AICandidatePriorityHint.LOW,
            ]
        )
    ]
    places = _FakePlacesProvider(lambda term, dest: _found_response())
    gateway = ProviderGateway(places=places)
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    first_run = service.discover(_planning_state(), proposals, provider_candidates=[], max_searches=3)
    second_run = service.discover(_planning_state(), proposals, provider_candidates=[], max_searches=3)

    first_searched = [a.proposal_id for a in first_run.attempts if a.status != AIProviderDiscoveryAttemptStatus.NOT_SEARCHED]
    second_searched = [a.proposal_id for a in second_run.attempts if a.status != AIProviderDiscoveryAttemptStatus.NOT_SEARCHED]
    assert first_searched == second_searched


def test_not_searched_status_is_distinguishable_from_other_outcomes() -> None:
    proposals = [_discovery_query(f"proposal_{i:03d}", f"query {i}") for i in range(4)]
    # The provider would happily return not_found/failed/not_connected for
    # anything actually searched -- proving NOT_SEARCHED is a distinct
    # status this service assigns *before* ever calling the provider, not
    # a relabeled provider outcome.
    places = _FakePlacesProvider(
        lambda term, dest: unavailable_response("fake_places_provider", PlacesProvider.provider_type)
    )
    gateway = ProviderGateway(places=places)
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), proposals, provider_candidates=[], max_searches=1)

    statuses = {a.proposal_id: a.status for a in result.attempts}
    searched_status = [s for pid, s in statuses.items() if s != AIProviderDiscoveryAttemptStatus.NOT_SEARCHED]
    not_searched_status = [s for s in statuses.values() if s == AIProviderDiscoveryAttemptStatus.NOT_SEARCHED]
    assert len(searched_status) == 1
    assert searched_status[0] == AIProviderDiscoveryAttemptStatus.NOT_FOUND
    assert len(not_searched_status) == 3
    assert len(places.calls) == 1


def test_skipped_proposals_never_produce_a_match() -> None:
    proposals = [_discovery_query(f"proposal_{i:03d}", f"query {i}") for i in range(5)]
    places = _FakePlacesProvider(lambda term, dest: _found_response())
    gateway = ProviderGateway(places=places)
    service = AIDirectedProviderDiscoveryService(gateway=gateway)

    result = service.discover(_planning_state(), proposals, provider_candidates=[], max_searches=2)

    not_searched_ids = {
        a.proposal_id for a in result.attempts if a.status == AIProviderDiscoveryAttemptStatus.NOT_SEARCHED
    }
    assert len(not_searched_ids) == 3
    matches = result.matches_by_proposal_id()
    assert not_searched_ids.isdisjoint(matches.keys())
    assert result.matched_count == 2
