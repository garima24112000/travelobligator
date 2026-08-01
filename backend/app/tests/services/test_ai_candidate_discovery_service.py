from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest

from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateProposalGuardrailReport,
    AICandidateProposalRequest,
    AICandidateProposalResult,
    AICandidateProposalStatus,
    AICandidateProposalTask,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.models.candidate_grounding import CandidateGroundingStatus
from app.models.planning_state import DestinationContext, PlanningState, TravelGroupType, TripRequest
from app.providers.ai_candidate_proposal import AICandidateProposalProvider
from app.services import ai_candidate_discovery_service as discovery_module
from app.services.ai_candidate_discovery_service import (
    AICandidateDiscoveryDryRunResult,
    AICandidateDiscoveryService,
)

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


class _FakeAICandidateProposalProvider(AICandidateProposalProvider):
    """Deterministic test double -- never calls an LLM or provider, and
    only ever echoes back the exact proposals it was constructed with.
    """

    provider_name = "fake_test_ai_candidate_proposal_provider"

    def __init__(self, proposals: list[AICandidateProposal]) -> None:
        self._proposals = proposals

    def propose(self, request: AICandidateProposalRequest) -> AICandidateProposalResult:
        return AICandidateProposalResult(
            task=request.task,
            status=AICandidateProposalStatus.COMPLETED,
            proposals=self._proposals,
            rejected_raw_items=[],
            guardrail_report=AICandidateProposalGuardrailReport(passed=True),
            provider_name=self.provider_name,
            model_name=None,
            confidence=0.6,
        )


def _place(
    name: str = "Old Town Waterfront",
    *,
    lat: float = 38.7223,
    lng: float = -9.1393,
    category: str | None = "neighborhood",
    confidence: float = 0.8,
    source: str = "openstreetmap_places",
    data_status: str = "live",
    place_id: str = "way/12345",
) -> dict[str, Any]:
    return {
        "name": name,
        "category": category,
        "coordinates": {"lat": lat, "lng": lng},
        "source": source,
        "data_status": data_status,
        "confidence": confidence,
        "place_id": place_id,
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
    candidate_pois: list[dict[str, Any]] | None = None,
    with_destination_context: bool = False,
    **trip_request_overrides: Any,
) -> PlanningState:
    planning_state = PlanningState(trip_request=_trip_request(**trip_request_overrides))
    if with_destination_context or candidate_pois:
        planning_state.destination_context = DestinationContext(
            destination_name=planning_state.trip_request.primary_destination,
            candidate_pois=candidate_pois or [],
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
def service() -> AICandidateDiscoveryService:
    return AICandidateDiscoveryService()


# ---------------------------------------------------------------------------
# 1. dry_run returns AICandidateDiscoveryDryRunResult.
# ---------------------------------------------------------------------------


def test_dry_run_returns_dry_run_result(service: AICandidateDiscoveryService) -> None:
    planning_state = _planning_state()

    result = service.dry_run(planning_state)

    assert isinstance(result, AICandidateDiscoveryDryRunResult)


# ---------------------------------------------------------------------------
# 2. Uses AICandidateProposalRequestBuilder output.
# ---------------------------------------------------------------------------


def test_dry_run_uses_proposal_request_builder_output(
    service: AICandidateDiscoveryService,
) -> None:
    planning_state = _planning_state(primary_destination="Porto, Portugal")

    result = service.dry_run(planning_state)

    assert result.proposal_request.trip_id == planning_state.trip_id
    assert result.proposal_request.destination_name == "Porto, Portugal"
    assert result.proposal_request.task == AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY


# ---------------------------------------------------------------------------
# 3. Default proposal provider returns NOT_CONNECTED.
# ---------------------------------------------------------------------------


def test_default_proposal_result_is_not_connected(service: AICandidateDiscoveryService) -> None:
    planning_state = _planning_state()

    result = service.dry_run(planning_state)

    assert result.proposal_result.status == AICandidateProposalStatus.NOT_CONNECTED


# ---------------------------------------------------------------------------
# 4. Default proposal_result.proposals is empty.
# ---------------------------------------------------------------------------


def test_default_proposal_result_has_no_proposals(service: AICandidateDiscoveryService) -> None:
    planning_state = _planning_state()

    result = service.dry_run(planning_state)

    assert result.proposal_result.proposals == []


# ---------------------------------------------------------------------------
# 5. Grounding request receives proposal_result.proposals exactly.
# ---------------------------------------------------------------------------


def test_grounding_request_receives_proposal_result_proposals_exactly() -> None:
    proposals = [
        _proposal(proposal_id="proposal_001", candidate_name="Old Town Waterfront"),
        _proposal(proposal_id="proposal_002", candidate_name="Mystery Garden"),
    ]
    service = AICandidateDiscoveryService(
        proposal_provider=_FakeAICandidateProposalProvider(proposals)
    )
    planning_state = _planning_state()

    result = service.dry_run(planning_state)

    assert result.grounding_request.proposals == proposals
    assert result.grounding_request.proposals == result.proposal_result.proposals


# ---------------------------------------------------------------------------
# 6. Grounding request still includes provider_candidates from PlanningState
#    destination_context when present.
# ---------------------------------------------------------------------------


def test_grounding_request_includes_destination_context_provider_candidates(
    service: AICandidateDiscoveryService,
) -> None:
    planning_state = _planning_state(candidate_pois=[_place(name="Old Town Waterfront")])

    result = service.dry_run(planning_state)

    # No proposals from the default not-connected provider, but the
    # grounding request still carries the supplied provider candidates.
    assert result.grounding_request.proposals == []
    assert len(result.grounding_request.provider_candidates) == 1
    assert result.grounding_request.provider_candidates[0].name == "Old Town Waterfront"


# ---------------------------------------------------------------------------
# 7. Default grounding_result is SKIPPED when proposal list is empty.
# ---------------------------------------------------------------------------


def test_default_grounding_result_is_skipped(service: AICandidateDiscoveryService) -> None:
    planning_state = _planning_state(candidate_pois=[_place()])

    result = service.dry_run(planning_state)

    assert result.grounding_result.status == CandidateGroundingStatus.SKIPPED


# ---------------------------------------------------------------------------
# 8. Default grounding_result has no grounded_candidates and no
#    rejected_proposals.
# ---------------------------------------------------------------------------


def test_default_grounding_result_has_no_candidates(service: AICandidateDiscoveryService) -> None:
    planning_state = _planning_state()

    result = service.dry_run(planning_state)

    assert result.grounding_result.grounded_candidates == []
    assert result.grounding_result.rejected_proposals == []


# ---------------------------------------------------------------------------
# 9. dry_run respects task argument.
# ---------------------------------------------------------------------------


def test_dry_run_respects_task_argument(service: AICandidateDiscoveryService) -> None:
    planning_state = _planning_state()

    result = service.dry_run(
        planning_state, task=AICandidateProposalTask.REPLACEMENT_CANDIDATE_DISCOVERY
    )

    assert result.proposal_request.task == AICandidateProposalTask.REPLACEMENT_CANDIDATE_DISCOVERY
    assert result.proposal_result.task == AICandidateProposalTask.REPLACEMENT_CANDIDATE_DISCOVERY


# ---------------------------------------------------------------------------
# 10. dry_run respects max_candidates argument.
# ---------------------------------------------------------------------------


def test_dry_run_respects_max_candidates_argument(service: AICandidateDiscoveryService) -> None:
    planning_state = _planning_state()

    result = service.dry_run(planning_state, max_candidates=7)

    assert result.proposal_request.max_candidates == 7


# ---------------------------------------------------------------------------
# 11. Does not mutate PlanningState.
# ---------------------------------------------------------------------------


def test_dry_run_does_not_mutate_planning_state(service: AICandidateDiscoveryService) -> None:
    planning_state = _planning_state(candidate_pois=[_place()])
    before = planning_state.model_copy(deep=True)

    service.dry_run(planning_state)

    assert planning_state == before


# ---------------------------------------------------------------------------
# 12. Does not create fake proposals when provider is not connected.
# ---------------------------------------------------------------------------


def test_does_not_fabricate_proposals_when_not_connected(
    service: AICandidateDiscoveryService,
) -> None:
    planning_state = _planning_state(candidate_pois=[_place()])

    result = service.dry_run(planning_state)

    assert result.proposal_result.proposals == []
    assert result.proposal_result.guardrail_report.passed is False


# ---------------------------------------------------------------------------
# 13. Does not create fake grounded candidates.
# ---------------------------------------------------------------------------


def test_does_not_fabricate_grounded_candidates(service: AICandidateDiscoveryService) -> None:
    planning_state = _planning_state(candidate_pois=[_place()])

    result = service.dry_run(planning_state)

    assert result.grounding_result.grounded_candidates == []


# ---------------------------------------------------------------------------
# 14. Does not persist or touch repositories.
# ---------------------------------------------------------------------------


def test_service_module_does_not_import_repositories() -> None:
    source = inspect.getsource(discovery_module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    assert not any("app.repositories" in name for name in imported_names)


# ---------------------------------------------------------------------------
# 15. Does not import provider adapters, real LLM clients, LangGraph,
#     LangSmith, httpx, requests, OpenAI, Anthropic, Gemini, or
#     PlanningOrchestrator. `app.providers.ai_candidate_proposal` (the
#     Step 157B contract boundary + NotConnected adapter) is explicitly
#     required and allowed -- it makes no network call itself.
# ---------------------------------------------------------------------------


def test_service_module_has_no_disallowed_imports() -> None:
    source = inspect.getsource(discovery_module)
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
        "planning_orchestrator",
        "app.providers.places",
        "app.providers.routes",
        "app.providers.transit",
        "app.providers.accommodation",
        "app.providers.weather",
        "app.providers.holidays",
        "app.providers.currency",
        "app.providers.gateway",
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

    # The only provider import allowed is the AI candidate proposal
    # contract boundary itself.
    assert "app.providers.ai_candidate_proposal" in imported_names


# ---------------------------------------------------------------------------
# 16. Does not modify scheduling/validation/regeneration modules.
# ---------------------------------------------------------------------------


def test_service_module_does_not_import_scheduling_validation_or_regeneration() -> None:
    source = inspect.getsource(discovery_module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    disallowed_substrings = (
        "experience_planner_service",
        "plan_validator_service",
        "versioning_service",
        "regeneration",
        "feedback_service",
    )
    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"


# ---------------------------------------------------------------------------
# 17. Allows dependency injection of a custom fake proposal provider in
#     tests.
# ---------------------------------------------------------------------------


def test_allows_injected_fake_proposal_provider() -> None:
    fake_provider = _FakeAICandidateProposalProvider([_proposal()])
    service = AICandidateDiscoveryService(proposal_provider=fake_provider)
    planning_state = _planning_state()

    result = service.dry_run(planning_state)

    assert result.proposal_result.provider_name == "fake_test_ai_candidate_proposal_provider"
    assert result.proposal_result.status == AICandidateProposalStatus.COMPLETED


# ---------------------------------------------------------------------------
# 18. With an injected deterministic test proposal provider and matching
#     destination_context candidate, dry_run can produce a completed
#     grounding_result.
# ---------------------------------------------------------------------------


def test_dry_run_produces_completed_grounding_with_matching_candidate() -> None:
    proposals = [_proposal(candidate_name="Old Town Waterfront")]
    service = AICandidateDiscoveryService(
        proposal_provider=_FakeAICandidateProposalProvider(proposals)
    )
    planning_state = _planning_state(candidate_pois=[_place(name="Old Town Waterfront")])

    result = service.dry_run(planning_state)

    assert result.grounding_result.status == CandidateGroundingStatus.COMPLETED
    assert len(result.grounding_result.grounded_candidates) == 1


# ---------------------------------------------------------------------------
# 19. With an injected deterministic test proposal provider and no matching
#     provider candidate, dry_run produces rejected grounding_result.
# ---------------------------------------------------------------------------


def test_dry_run_produces_rejected_grounding_without_matching_candidate() -> None:
    proposals = [_proposal(candidate_name="Mystery Garden")]
    service = AICandidateDiscoveryService(
        proposal_provider=_FakeAICandidateProposalProvider(proposals)
    )
    planning_state = _planning_state(candidate_pois=[_place(name="Old Town Waterfront")])

    result = service.dry_run(planning_state)

    assert result.grounding_result.status == CandidateGroundingStatus.REJECTED
    assert result.grounding_result.grounded_candidates == []
    assert len(result.grounding_result.rejected_proposals) == 1


# ---------------------------------------------------------------------------
# 20. Result dump contains no forbidden factual fields.
# ---------------------------------------------------------------------------


def test_dry_run_result_dump_has_no_forbidden_factual_keys() -> None:
    proposals = [_proposal(candidate_name="Old Town Waterfront")]
    service = AICandidateDiscoveryService(
        proposal_provider=_FakeAICandidateProposalProvider(proposals)
    )
    planning_state = _planning_state(candidate_pois=[_place(name="Old Town Waterfront")])

    result = service.dry_run(planning_state)
    dumped = result.model_dump()

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
    assert overlap == set(), f"Dry-run result dump has forbidden key(s): {overlap}"


# ---------------------------------------------------------------------------
# Step 160C: AICandidateDiscoveryService is still not called by
# PlanningOrchestrator or any API route, and no scheduling/validation/
# regeneration module imports it.
# ---------------------------------------------------------------------------


def _imported_module_names(module: object) -> list[str]:
    source = inspect.getsource(module)  # type: ignore[arg-type]
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
            imported_names.extend(alias.name for alias in node.names)
    return imported_names


def test_planning_orchestrator_does_not_import_discovery_service() -> None:
    import app.services.planning_orchestrator as orchestrator_module

    imported_names = _imported_module_names(orchestrator_module)

    assert not any("ai_candidate_discovery_service" in name for name in imported_names)
    assert not any(name == "AICandidateDiscoveryService" for name in imported_names)


def test_api_routes_do_not_import_discovery_service() -> None:
    import app.api.routes.trips as trips_routes_module

    imported_names = _imported_module_names(trips_routes_module)
    assert not any("ai_candidate_discovery_service" in name for name in imported_names)
    assert not any(name == "AICandidateDiscoveryService" for name in imported_names)


def test_scheduling_validation_and_regeneration_modules_do_not_import_discovery_service() -> None:
    import app.services.experience_planner_service as experience_planner_module
    import app.services.feedback_service as feedback_service_module
    import app.services.plan_validator_service as plan_validator_module
    import app.services.regeneration_readiness_service as regeneration_readiness_module
    import app.services.versioning_service as versioning_module

    for module in (
        experience_planner_module,
        plan_validator_module,
        versioning_module,
        regeneration_readiness_module,
        feedback_service_module,
    ):
        imported_names = _imported_module_names(module)
        assert not any("ai_candidate_discovery_service" in name for name in imported_names)
        assert not any(name == "AICandidateDiscoveryService" for name in imported_names)


# ---------------------------------------------------------------------------
# Step 160E: default provider construction is resolved through the
# config-gated factory, and an explicitly injected provider bypasses it.
# ---------------------------------------------------------------------------


def test_default_provider_construction_uses_the_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel_provider = _FakeAICandidateProposalProvider([])

    def _fake_factory(provider_name: str | None = None) -> _FakeAICandidateProposalProvider:
        return sentinel_provider

    monkeypatch.setattr(discovery_module, "get_ai_candidate_proposal_provider", _fake_factory)

    service = AICandidateDiscoveryService()

    assert service.proposal_provider is sentinel_provider


def test_injected_provider_bypasses_the_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_factory(provider_name: str | None = None) -> None:
        raise AssertionError("get_ai_candidate_proposal_provider must not be called when a provider is injected")

    monkeypatch.setattr(discovery_module, "get_ai_candidate_proposal_provider", _fail_factory)

    injected_provider = _FakeAICandidateProposalProvider([_proposal()])
    service = AICandidateDiscoveryService(proposal_provider=injected_provider)

    assert service.proposal_provider is injected_provider
