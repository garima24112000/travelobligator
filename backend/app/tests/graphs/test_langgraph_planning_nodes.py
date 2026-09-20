from __future__ import annotations

from typing import Any

import pytest

from app.graphs.planning_graph_nodes import (
    build_ai_candidate_node,
    build_ai_itinerary_reasoning_node,
    build_ai_itinerary_repair_node,
    build_destination_context_node,
    build_experience_planning_node,
    build_final_state_node,
    build_provider_coverage_node,
    build_stay_transport_node,
    build_validation_node,
    route_after_repair,
    route_after_validation,
)
from app.graphs.planning_graph_state import build_initial_planning_graph_state
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest

# Tests for the LangGraph planning node skeleton (Step 171A). Every node
# here is exercised only with in-file fake/no-op service doubles -- no
# real provider/network call, no real LLM call (Groq/Anthropic/OpenAI/
# other), and no persistence.


class _FakeStageService:
    """Deterministic test double for a single-method (`run`) stage
    service. Never calls a provider, LLM, or network -- just marks that it
    ran, so tests can assert per-node behavior without depending on any
    real stage service's actual logic.
    """

    def __init__(self, marker: str, *, raises: bool = False) -> None:
        self.marker = marker
        self.raises = raises
        self.call_count = 0

    def run(self, planning_state: PlanningState) -> PlanningState:
        self.call_count += 1
        if self.raises:
            raise RuntimeError(f"simulated failure in {self.marker}")
        planning_state.data_sources_used = list(planning_state.data_sources_used) + [
            f"fake_{self.marker}"
        ]
        return planning_state


class _FakeAICandidatePromotionService:
    """Deterministic test double for `AICandidatePromotionService`. Never
    calls a provider, LLM, or network.
    """

    def __init__(self, *, raises: bool = False) -> None:
        self.raises = raises
        self.call_count = 0

    def apply_promotion(self, planning_state: PlanningState) -> PlanningState:
        self.call_count += 1
        if self.raises:
            raise RuntimeError("simulated ai_candidate failure")
        planning_state.data_sources_used = list(planning_state.data_sources_used) + [
            "fake_ai_candidate_promotion"
        ]
        return planning_state


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "New York",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _initial_state(trip_request: TripRequest | None = None) -> Any:
    trip_request = trip_request or _trip_request()
    planning_state = PlanningState(trip_request=trip_request)
    return build_initial_planning_graph_state(planning_state.trip_id, trip_request, planning_state)


# ---------------------------------------------------------------------------
# 3. Each node returns state and records completion.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "node_name, build_node",
    [
        ("destination_context", lambda service: build_destination_context_node(service)),
        ("stay_transport", lambda service: build_stay_transport_node(service)),
        ("experience_planning", lambda service: build_experience_planning_node(service)),
        ("validation", lambda service: build_validation_node(service)),
    ],
)
def test_service_backed_node_returns_state_and_records_completion(
    node_name: str, build_node: Any
) -> None:
    fake_service = _FakeStageService(node_name)
    node = build_node(fake_service)
    state = _initial_state()

    result = node(state)

    assert result["completed_nodes"] == [node_name]
    assert "failed_nodes" not in result
    assert fake_service.call_count == 1
    assert f"fake_{node_name}" in result["planning_state"].data_sources_used


def test_ai_candidate_node_with_injected_service_returns_state_and_records_completion() -> None:
    fake_service = _FakeAICandidatePromotionService()
    node = build_ai_candidate_node(fake_service)
    state = _initial_state()

    result = node(state)

    assert result["completed_nodes"] == ["ai_candidate"]
    assert fake_service.call_count == 1
    assert "fake_ai_candidate_promotion" in result["planning_state"].data_sources_used


def test_ai_candidate_node_default_is_a_pure_noop() -> None:
    """With no service injected (the default), ai_candidate_node never
    calls AICandidateDiscoveryService/an AI candidate proposal provider,
    and never mutates planning_state -- see build_ai_candidate_node's
    docstring."""
    node = build_ai_candidate_node()
    state = _initial_state()
    before = state["planning_state"].model_copy(deep=True)

    result = node(state)

    assert result == {"completed_nodes": ["ai_candidate"]}
    assert state["planning_state"] == before


def test_provider_coverage_node_returns_state_and_records_completion() -> None:
    node = build_provider_coverage_node()
    state = _initial_state()

    result = node(state)

    assert result == {"completed_nodes": ["provider_coverage"]}


def test_final_state_node_returns_state_and_records_completion() -> None:
    node = build_final_state_node()
    state = _initial_state()

    result = node(state)

    assert result["completed_nodes"] == ["final_state"]
    assert result["warnings"] == []


def test_final_state_node_reports_honest_warning_when_earlier_nodes_failed() -> None:
    node = build_final_state_node()
    state = _initial_state()
    state["failed_nodes"] = ["destination_context"]

    result = node(state)

    assert result["completed_nodes"] == ["final_state"]
    assert len(result["warnings"]) == 1
    assert "destination_context" in result["warnings"][0]


# ---------------------------------------------------------------------------
# 4-5. Node failure records failed_nodes and a safe error message, and
#      does not fabricate PlanningState data.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "node_name, build_node",
    [
        ("destination_context", lambda service: build_destination_context_node(service)),
        ("stay_transport", lambda service: build_stay_transport_node(service)),
        ("experience_planning", lambda service: build_experience_planning_node(service)),
        ("validation", lambda service: build_validation_node(service)),
    ],
)
def test_service_backed_node_failure_records_failed_node_and_safe_error(
    node_name: str, build_node: Any
) -> None:
    fake_service = _FakeStageService(node_name, raises=True)
    node = build_node(fake_service)
    state = _initial_state()
    before = state["planning_state"].model_copy(deep=True)

    result = node(state)

    assert result["failed_nodes"] == [node_name]
    assert "completed_nodes" not in result
    assert len(result["errors"]) == 1
    error_message = result["errors"][0]
    # Safe, generic, secret-free -- never the raw exception text.
    assert "simulated failure" not in error_message
    assert node_name in error_message
    # No PlanningState data fabricated: state passed back to the graph
    # (i.e. what the caller already had) is left completely untouched.
    assert state["planning_state"] == before


def test_ai_candidate_node_failure_records_failed_node_and_safe_error() -> None:
    fake_service = _FakeAICandidatePromotionService(raises=True)
    node = build_ai_candidate_node(fake_service)
    state = _initial_state()
    before = state["planning_state"].model_copy(deep=True)

    result = node(state)

    assert result["failed_nodes"] == ["ai_candidate"]
    assert len(result["errors"]) == 1
    assert "simulated" not in result["errors"][0]
    assert state["planning_state"] == before


# ---------------------------------------------------------------------------
# 8-10. No direct LLM client/provider/network-library imports from any
#       node in this module. As of Step 191A
#       (docs/14_backend_architecture.md section 135),
#       `ai_candidate_discovery_service` is a legitimate, intentional
#       import (the live `ai_candidate` node's only path to
#       `AICandidateDiscoveryService`) -- `app.providers`/Groq/Anthropic/
#       OpenAI/LangSmith stay fully banned: the node reaches a real LLM
#       only through that service's own layered provider abstraction,
#       never a direct import here.
# ---------------------------------------------------------------------------


def test_nodes_module_has_no_llm_or_network_imports() -> None:
    import ast
    import inspect

    import app.graphs.planning_graph_nodes as nodes_module

    source = inspect.getsource(nodes_module)
    tree = ast.parse(source)
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    disallowed_substrings = (
        "anthropic",
        "groq",
        "openai",
        "langsmith",
        "ai_candidate_proposal_provider",
        "app.providers",
        "requests",
        "httpx",
    )
    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"

    assert any("ai_candidate_discovery_service" in name for name in imported_names)


# ---------------------------------------------------------------------------
# Step 191A: live AI candidate discovery, gated by
# Settings.ai_candidate_discovery_enabled (default False). These tests
# exercise build_ai_candidate_node directly with deterministic fakes --
# never a real Groq/Anthropic/OpenAI call.
# ---------------------------------------------------------------------------


class _FakeAICandidateDiscoveryService:
    """Deterministic test double standing in for the whole
    `AICandidateDiscoveryService` -- exposes only `dry_run`, matching
    every other pre-191A fake built for the shadow-stage tests.
    """

    def __init__(
        self,
        result: Any = None,
        *,
        raises: bool = False,
    ) -> None:
        self._result = result
        self.raises = raises
        self.call_count = 0

    def dry_run(self, planning_state: PlanningState, **kwargs: Any) -> Any:
        self.call_count += 1
        if self.raises:
            raise RuntimeError("simulated discovery failure")
        return self._result


class _FakeAICandidateProposalProviderForNodeTest:
    """Deterministic fake `AICandidateProposalProvider` -- never a real
    Groq/Anthropic/OpenAI call. Proposes exactly the candidate
    `_state_with_destination_context` also seeds as a real destination-
    context provider candidate, so grounding actually completes end to
    end (mirrors the proven pattern in
    test_planning_orchestrator_ai_candidate_promotion.py).
    """

    provider_name = "fake_node_test_provider"

    def propose(self, request: Any) -> Any:
        from app.models.ai_candidate_proposal import (
            AICandidateProposal,
            AICandidateProposalGuardrailReport,
            AICandidateProposalResult,
            AICandidateProposalStatus,
            AICandidateType,
            AICandidateVerificationRequirement,
        )

        proposal = AICandidateProposal(
            proposal_id="proposal_001",
            candidate_name="Test Fixture Attraction One",
            candidate_type=AICandidateType.ATTRACTION,
            why_consider="Locally known landmark that may be under-tagged in provider data.",
            verification_requirements=[
                AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION
            ],
            confidence=0.5,
        )
        return AICandidateProposalResult(
            task=request.task,
            status=AICandidateProposalStatus.COMPLETED,
            proposals=[proposal],
            guardrail_report=AICandidateProposalGuardrailReport(passed=True),
            provider_name=self.provider_name,
            confidence=0.6,
        )


def _state_with_destination_context() -> Any:
    from app.models.planning_state import DestinationContext

    state = _initial_state()
    state["planning_state"].destination_context = DestinationContext(
        destination_name="Testville, Testland",
        candidate_pois=[
            {
                "name": "Test Fixture Attraction One",
                "coordinates": {"lat": 1.0, "lng": 2.0},
                "category": "attraction",
                "provider_name": "test_places_provider",
                "provider_place_id": "test/attraction/1",
                "data_status": "live",
                "confidence": 0.9,
            }
        ],
        candidate_restaurants=[],
        candidate_accommodation_pois=[],
    )
    return state


def test_ai_candidate_node_live_disabled_by_default_never_calls_discovery_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings

    import app.graphs.planning_graph_nodes as nodes_module

    monkeypatch.setattr(nodes_module, "get_settings", lambda: Settings(_env_file=None))
    fake_discovery = _FakeAICandidateDiscoveryService()
    node = build_ai_candidate_node(discovery_service=fake_discovery)
    state = _state_with_destination_context()
    before = state["planning_state"].model_copy(deep=True)

    result = node(state)

    assert result == {"completed_nodes": ["ai_candidate"]}
    assert fake_discovery.call_count == 0
    assert state["planning_state"] == before


def test_ai_candidate_node_live_enabled_calls_discovery_and_promotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end at the node level: a fake (but structurally real)
    proposal provider proposes a candidate that matches a real
    destination-context provider candidate by name, so grounding actually
    completes and the candidate is actually promoted -- proving
    proposal -> grounding -> promotion all really run, not just that
    dry_run was called once."""
    from app.core.config import Settings
    from app.services.ai_candidate_discovery_service import AICandidateDiscoveryService
    from app.services.ai_candidate_promotion_service import AICandidatePromotionService

    import app.graphs.planning_graph_nodes as nodes_module

    monkeypatch.setattr(
        nodes_module, "get_settings", lambda: Settings(_env_file=None, AI_CANDIDATE_DISCOVERY_ENABLED=True)
    )
    real_discovery = AICandidateDiscoveryService(
        proposal_provider=_FakeAICandidateProposalProviderForNodeTest()
    )
    real_promotion = AICandidatePromotionService()
    node = build_ai_candidate_node(real_promotion, real_discovery)
    state = _state_with_destination_context()
    # candidate_quality_report is required for promotion eligibility (Rule
    # 4) -- populate it the same way CandidateQualityService's own output
    # shape would, with an accepted tier for the matching candidate.
    from datetime import datetime, timezone

    from app.models.candidate_quality import (
        CandidateQualityReport,
        CandidateQualityScore,
        CandidateQualityTier,
        CandidateUseCase,
    )

    state["planning_state"].candidate_quality_report = CandidateQualityReport(
        destination_name="Testville, Testland",
        generated_at=datetime.now(timezone.utc),
        attraction_scores=[
            CandidateQualityScore(
                candidate_id="test/attraction/1",
                candidate_name="Test Fixture Attraction One",
                use_case=CandidateUseCase.ATTRACTION,
                quality_tier=CandidateQualityTier.PRIMARY_ANCHOR,
                total_score=0.9,
            )
        ],
        restaurant_scores=[],
        accommodation_poi_scores=[],
    )

    result = node(state)

    assert result["completed_nodes"] == ["ai_candidate"]
    planning_state = result["planning_state"]
    assert planning_state.ai_candidate_proposal_batch is not None
    assert planning_state.ai_candidate_proposal_batch.result.status.value == "completed"
    assert planning_state.candidate_grounding_batch is not None
    assert planning_state.candidate_grounding_batch.result.status.value == "completed"
    assert planning_state.ai_candidate_promotion_report is not None
    assert planning_state.ai_candidate_promotion_report.promoted_count == 1


def test_ai_candidate_node_live_enabled_discovery_failure_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings

    import app.graphs.planning_graph_nodes as nodes_module

    monkeypatch.setattr(
        nodes_module, "get_settings", lambda: Settings(_env_file=None, AI_CANDIDATE_DISCOVERY_ENABLED=True)
    )
    fake_discovery = _FakeAICandidateDiscoveryService(raises=True)
    fake_promotion = _FakeAICandidatePromotionService()
    node = build_ai_candidate_node(fake_promotion, fake_discovery)
    state = _state_with_destination_context()

    result = node(state)

    # apply_discovery_to_state swallows the exception -- node still
    # "succeeds" (it never crashes generation), but no batch is stored and
    # promotion has nothing to promote (ai_candidate_proposal_batch stays
    # None so apply_promotion still runs but reports "no_candidate_data").
    assert "failed_nodes" not in result
    assert result["planning_state"].ai_candidate_proposal_batch is None


# ---------------------------------------------------------------------------
# Section 193C, Task 18: the ai_itinerary_reasoning node's disabled-by-
# default path -- the injected provider must never be called, and the
# node must still report completion with an honest not_connected result
# rather than skip populating the field.
# ---------------------------------------------------------------------------


class _FakeAIItineraryReasoningProvider:
    """Deterministic test double for `AIItineraryReasoningProvider`. Never
    calls a network/LLM -- just records whether it was invoked, so tests
    can assert the disabled path truly never resolves/calls a provider.
    """

    provider_name = "fake_itinerary_reasoning_provider"

    def __init__(self) -> None:
        self.call_count = 0

    def reason(self, request: Any) -> Any:
        self.call_count += 1
        from app.models.ai_itinerary_reasoning import (
            AIItineraryReasoningGuardrailReport,
            AIItineraryReasoningResult,
            AIItineraryReasoningStatus,
            ItineraryReasoningDayPlan,
            ItineraryReasoningStrategy,
        )

        allowed_ids = request.allowed_candidate_ids()
        if not allowed_ids:
            # No real candidate universe available for this fake state --
            # report an honest not_connected result rather than inventing
            # a candidate_id that isn't in request.allowed_candidates.
            return AIItineraryReasoningResult(
                status=AIItineraryReasoningStatus.NOT_CONNECTED,
                days=[],
                guardrail_report=AIItineraryReasoningGuardrailReport(
                    passed=False, blocked_reasons=["no candidates available"]
                ),
                provider_name=self.provider_name,
                confidence=0.0,
            )
        return AIItineraryReasoningResult(
            status=AIItineraryReasoningStatus.COMPLETED,
            strategy=ItineraryReasoningStrategy(
                summary="A fake test strategy.", pace="balanced", reason="Matches test fixture."
            ),
            days=[
                ItineraryReasoningDayPlan(
                    day_index=1,
                    candidate_ids=[next(iter(allowed_ids))],
                    rationale="Fake test rationale.",
                )
            ],
            guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
            provider_name=self.provider_name,
            confidence=0.5,
        )


def test_ai_itinerary_reasoning_node_disabled_by_default_never_calls_provider() -> None:
    from app.services.ai_itinerary_reasoning_service import AIItineraryReasoningService

    fake_provider = _FakeAIItineraryReasoningProvider()
    service = AIItineraryReasoningService(provider=fake_provider)
    node = build_ai_itinerary_reasoning_node(service)
    state = _initial_state()

    result = node(state)

    assert result["completed_nodes"] == ["ai_itinerary_reasoning"]
    assert fake_provider.call_count == 0
    reasoning_result = result["planning_state"].ai_itinerary_reasoning_result
    assert reasoning_result is not None
    assert reasoning_result.status.value == "not_connected"
    assert reasoning_result.days == []


def test_ai_itinerary_reasoning_node_enabled_calls_provider_and_stores_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings
    from app.services.ai_itinerary_reasoning_service import AIItineraryReasoningService

    from datetime import datetime, timezone

    from app.models.candidate_quality import (
        CandidateQualityReport,
        CandidateQualityScore,
        CandidateQualityTier,
        CandidateUseCase,
    )

    monkeypatch.setattr(
        "app.services.ai_itinerary_reasoning_service.get_settings",
        lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True),
    )
    from app.models.planning_state import DestinationContext

    fake_provider = _FakeAIItineraryReasoningProvider()
    service = AIItineraryReasoningService(provider=fake_provider)
    node = build_ai_itinerary_reasoning_node(service)
    state = _initial_state()
    state["planning_state"].destination_context = DestinationContext(
        destination_name="Testville, Testland",
        candidate_pois=[
            {
                "place_id": "test/attraction/1",
                "name": "Test Fixture Attraction One",
                "coordinates": {"lat": 1.0, "lng": 2.0},
                "category": "attraction",
                "source": "test_places_provider",
                "data_status": "live",
                "confidence": 0.9,
            }
        ],
        candidate_restaurants=[],
        candidate_accommodation_pois=[],
    )
    state["planning_state"].candidate_quality_report = CandidateQualityReport(
        destination_name="Testville, Testland",
        generated_at=datetime.now(timezone.utc),
        attraction_scores=[
            CandidateQualityScore(
                candidate_id="test/attraction/1",
                candidate_name="Test Fixture Attraction One",
                use_case=CandidateUseCase.ATTRACTION,
                quality_tier=CandidateQualityTier.PRIMARY_ANCHOR,
                total_score=0.9,
            )
        ],
        restaurant_scores=[],
        accommodation_poi_scores=[],
    )

    result = node(state)

    assert result["completed_nodes"] == ["ai_itinerary_reasoning"]
    assert fake_provider.call_count == 1
    reasoning_result = result["planning_state"].ai_itinerary_reasoning_result
    assert reasoning_result is not None
    assert reasoning_result.status.value == "completed"


def test_ai_itinerary_reasoning_node_failure_is_safe() -> None:
    class _RaisingService:
        def apply(self, planning_state: PlanningState) -> PlanningState:
            raise RuntimeError("simulated reasoning failure")

    node = build_ai_itinerary_reasoning_node(_RaisingService())  # type: ignore[arg-type]
    state = _initial_state()
    before = state["planning_state"].model_copy(deep=True)

    result = node(state)

    # apply_itinerary_reasoning_safely swallows the exception -- the node
    # itself never even sees it, so this is a normal completion with the
    # planning_state left exactly as it was for this call.
    assert result["completed_nodes"] == ["ai_itinerary_reasoning"]
    assert "failed_nodes" not in result
    assert result["planning_state"] == before


# ---------------------------------------------------------------------------
# Section 194B: ai_itinerary_repair node and route_after_validation/
# route_after_repair conditional-edge functions, at the node/router unit
# level (docs/14_backend_architecture.md, following section 144). Full
# end-to-end loop behavior (real geographic_spread trigger, real
# multi-pass reruns, real merge) is covered separately in
# test_ai_itinerary_repair_loop.py.
# ---------------------------------------------------------------------------


def _repair_completed_result(candidate_ids: list[Any]) -> Any:
    from app.models.ai_itinerary_reasoning import (
        AIItineraryReasoningGuardrailReport,
        ItineraryReasoningDayPlan,
    )
    from app.models.ai_itinerary_repair import AIItineraryRepairResult, AIItineraryRepairStatus

    return AIItineraryRepairResult(
        status=AIItineraryRepairStatus.COMPLETED,
        repaired_days=[
            ItineraryReasoningDayPlan(day_index=1, candidate_ids=candidate_ids, rationale="Repaired.")
        ],
        repair_summary="Repaired day 1.",
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        provider_name="fake_repair_provider",
        confidence=0.7,
    )


def test_ai_itinerary_repair_node_disabled_never_calls_provider() -> None:
    from app.core.config import Settings
    from app.services.ai_itinerary_repair_service import AIItineraryRepairService

    class _FakeRepairProvider:
        provider_name = "fake_repair_provider"

        def __init__(self) -> None:
            self.call_count = 0

        def reason(self, request: Any) -> Any:
            raise NotImplementedError

        def repair(self, request: Any) -> Any:
            self.call_count += 1
            return _repair_completed_result(["osm:a"])

    fake_provider = _FakeRepairProvider()
    service = AIItineraryRepairService(provider=fake_provider)
    node = build_ai_itinerary_repair_node(service)
    state = _initial_state()

    import app.services.ai_itinerary_repair_service as repair_service_module

    original_get_settings = repair_service_module.get_settings
    repair_service_module.get_settings = lambda: Settings(_env_file=None)
    try:
        result = node(state)
    finally:
        repair_service_module.get_settings = original_get_settings

    assert result["completed_nodes"] == ["ai_itinerary_repair"]
    assert fake_provider.call_count == 0
    assert result["planning_state"].ai_itinerary_repair_attempt_count == 0
    assert result["planning_state"].ai_itinerary_repair_result is not None
    assert result["planning_state"].ai_itinerary_repair_result.status.value == "not_connected"


def test_ai_itinerary_repair_node_failure_is_safe() -> None:
    class _RaisingService:
        request_builder = None

        def apply(self, planning_state: PlanningState, attempt_number: int = 1) -> PlanningState:
            raise RuntimeError("simulated repair failure")

    node = build_ai_itinerary_repair_node(_RaisingService())  # type: ignore[arg-type]
    state = _initial_state()
    before = state["planning_state"].model_copy(deep=True)

    result = node(state)

    assert result["failed_nodes"] == ["ai_itinerary_repair"]
    assert "completed_nodes" not in result
    assert state["planning_state"] == before


def test_route_after_validation_returns_provider_coverage_when_disabled() -> None:
    from app.core.config import Settings

    import app.graphs.planning_graph_nodes as nodes_module

    original_get_settings = nodes_module.get_settings
    nodes_module.get_settings = lambda: Settings(_env_file=None)
    try:
        assert route_after_validation(_initial_state()) == "provider_coverage"
    finally:
        nodes_module.get_settings = original_get_settings


def test_route_after_validation_returns_provider_coverage_when_no_reasoning_result() -> None:
    from app.core.config import Settings

    import app.graphs.planning_graph_nodes as nodes_module

    original_get_settings = nodes_module.get_settings
    nodes_module.get_settings = lambda: Settings(
        _env_file=None, AI_ITINERARY_REPAIR_ENABLED=True, AI_ITINERARY_REASONING_ENABLED=True
    )
    try:
        state = _initial_state()
        assert state["planning_state"].ai_itinerary_reasoning_result is None
        assert route_after_validation(state) == "provider_coverage"
    finally:
        nodes_module.get_settings = original_get_settings


def test_route_after_repair_routes_completed_to_experience_planning() -> None:
    state = _initial_state()
    state["planning_state"].ai_itinerary_repair_result = _repair_completed_result(["osm:a"])
    assert route_after_repair(state) == "experience_planning"


def test_route_after_repair_routes_non_completed_to_provider_coverage() -> None:
    from app.models.ai_itinerary_reasoning import AIItineraryReasoningGuardrailReport
    from app.models.ai_itinerary_repair import AIItineraryRepairResult, AIItineraryRepairStatus

    state = _initial_state()
    state["planning_state"].ai_itinerary_repair_result = AIItineraryRepairResult(
        status=AIItineraryRepairStatus.REJECTED,
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=False, blocked_reasons=["bad"]),
    )
    assert route_after_repair(state) == "provider_coverage"


def test_route_after_repair_routes_absent_result_to_provider_coverage() -> None:
    state = _initial_state()
    assert state["planning_state"].ai_itinerary_repair_result is None
    assert route_after_repair(state) == "provider_coverage"
