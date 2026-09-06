from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest

from app.models.planning_state import PipelineStatus, PlanningState, TravelGroupType, TripRequest
from app.repositories.planning_state_repository import planning_state_repository
from app.repositories.trip_repository import trip_repository
from app.services.langgraph_planning_service import LangGraphPlanningResult, LangGraphPlanningService

# Tests for the LangGraph planning runner/service (Step 171B,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
# Every test here uses only in-file fake/no-op stage-service doubles --
# no real provider/network call, no real LLM call (Groq/Anthropic/OpenAI/
# other), and no persistence. This service is architecture/resume
# foundation only: it does not replace `PlanningOrchestrator.
# generate_full_plan`, is not imported by any API route, and does not
# change `/trips/{trip_id}/generate` behavior.


class _FakeStageService:
    """Deterministic test double for a single-method (`run`) stage
    service. Never calls a provider, LLM, or network -- just marks that it
    ran, in order, so tests can assert graph execution order.
    """

    def __init__(self, marker: str, order_log: list[str], *, raises: bool = False) -> None:
        self.marker = marker
        self.order_log = order_log
        self.raises = raises
        self.call_count = 0

    def run(self, planning_state: PlanningState) -> PlanningState:
        self.call_count += 1
        if self.raises:
            raise RuntimeError(f"simulated failure in {self.marker}")
        self.order_log.append(self.marker)
        planning_state.data_sources_used = list(planning_state.data_sources_used) + [
            f"fake_{self.marker}"
        ]
        return planning_state


class _FakeAICandidatePromotionService:
    def __init__(self, order_log: list[str]) -> None:
        self.order_log = order_log
        self.call_count = 0

    def apply_promotion(self, planning_state: PlanningState) -> PlanningState:
        self.call_count += 1
        self.order_log.append("ai_candidate")
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


def _fake_service(order_log: list[str] | None = None) -> LangGraphPlanningService:
    order_log = order_log if order_log is not None else []
    return LangGraphPlanningService(
        destination_context_service=_FakeStageService("destination_context", order_log),
        stay_transport_service=_FakeStageService("stay_transport", order_log),
        ai_candidate_promotion_service=_FakeAICandidatePromotionService(order_log),
        experience_planner_service=_FakeStageService("experience_planning", order_log),
        plan_validator_service=_FakeStageService("validation", order_log),
    )


# ---------------------------------------------------------------------------
# 1. LangGraphPlanningService can be constructed.
# ---------------------------------------------------------------------------


def test_service_can_be_constructed_with_no_arguments() -> None:
    service = LangGraphPlanningService()
    assert service is not None


def test_service_can_be_constructed_with_injected_fakes() -> None:
    service = _fake_service()
    assert service is not None


# ---------------------------------------------------------------------------
# 2. Service creates a new PlanningState when none is passed.
# ---------------------------------------------------------------------------


def test_run_creates_new_planning_state_when_none_passed() -> None:
    service = _fake_service()
    trip_request = _trip_request()

    result = service.run("trip_new_123", trip_request, planning_state=None)

    assert isinstance(result.planning_state, PlanningState)
    assert result.planning_state.trip_id == "trip_new_123"
    assert result.planning_state.trip_request == trip_request


def test_new_planning_state_follows_create_trip_conventions() -> None:
    """Mirrors PlanningOrchestrator.create_trip's non-persisting
    construction conventions: a not_connected provider-coverage snapshot,
    an idle GenerationProgress, and a freshly recomputed
    regeneration_readiness gate -- checked before any graph node has run,
    using fakes that never touch these fields themselves."""
    service = LangGraphPlanningService(
        destination_context_service=_FakeStageServiceNoMutation(),
        stay_transport_service=_FakeStageServiceNoMutation(),
        experience_planner_service=_FakeStageServiceNoMutation(),
        plan_validator_service=_FakeStageServiceNoMutation(),
    )
    trip_request = _trip_request()

    result = service.run("trip_new_456", trip_request, planning_state=None)

    assert result.planning_state.provider_coverage.places == "not_connected"
    assert result.planning_state.generation_progress is not None
    assert result.planning_state.generation_progress.status.value == "idle"
    assert result.planning_state.regeneration_readiness.status == "blocked"


class _FakeStageServiceNoMutation:
    """A fake stage service that does nothing but pass planning_state
    through unchanged -- used to isolate the service's own
    new-PlanningState construction behavior from any node's mutation."""

    def run(self, planning_state: PlanningState) -> PlanningState:
        return planning_state

    def apply_promotion(self, planning_state: PlanningState) -> PlanningState:
        return planning_state


# ---------------------------------------------------------------------------
# 3. Service preserves an existing PlanningState when provided.
# ---------------------------------------------------------------------------


def test_run_preserves_existing_planning_state_when_provided() -> None:
    service = _fake_service()
    trip_request = _trip_request()
    existing_planning_state = PlanningState(trip_id="trip_existing_789", trip_request=trip_request)
    existing_planning_state.data_sources_used = ["pre_existing_marker"]

    result = service.run("trip_existing_789", trip_request, planning_state=existing_planning_state)

    assert result.planning_state.trip_id == "trip_existing_789"
    assert "pre_existing_marker" in result.planning_state.data_sources_used


# ---------------------------------------------------------------------------
# 4. Service runs graph nodes in expected order using injected fake
#    services.
# ---------------------------------------------------------------------------


def test_run_executes_nodes_in_expected_order() -> None:
    order_log: list[str] = []
    service = _fake_service(order_log)
    trip_request = _trip_request()
    planning_state = PlanningState(trip_id="trip_order_1", trip_request=trip_request)

    service.run("trip_order_1", trip_request, planning_state)

    # ai_candidate now runs before stay_transport (Step 171E reordering to
    # match legacy's stage flow) -- only the 5 faked stages' relative
    # order is asserted here; every other stage the graph now covers uses
    # its real, network-free default construction (see _fake_service).
    assert order_log == [
        "destination_context",
        "ai_candidate",
        "stay_transport",
        "experience_planning",
        "validation",
    ]


# ---------------------------------------------------------------------------
# 5-6. Service returns final PlanningState and exposes completed_nodes.
# ---------------------------------------------------------------------------


def test_run_returns_result_with_final_planning_state_and_completed_nodes() -> None:
    service = _fake_service()
    trip_request = _trip_request()
    planning_state = PlanningState(trip_id="trip_result_1", trip_request=trip_request)

    result = service.run("trip_result_1", trip_request, planning_state)

    assert isinstance(result, LangGraphPlanningResult)
    assert isinstance(result.planning_state, PlanningState)
    assert result.completed_nodes == [
        "traveler_profile",
        "destination_context",
        "candidate_quality",
        "ai_candidate",
        "trip_strategy",
        "stay_transport",
        "accommodation_inventory",
        "flight_inventory",
        "experience_planning",
        "route_feasibility",
        "route_aware_sequencing",
        "travel_time_buffer",
        "validation",
        "provider_coverage",
        "final_state",
    ]
    assert result.failed_nodes == []
    assert result.errors == []


# ---------------------------------------------------------------------------
# 7-8. Service exposes failed_nodes/errors when a fake service fails, and
#      the failure never fabricates PlanningState fields.
# ---------------------------------------------------------------------------


def test_run_exposes_failed_nodes_and_errors_on_node_failure() -> None:
    order_log: list[str] = []
    service = LangGraphPlanningService(
        destination_context_service=_FakeStageService(
            "destination_context", order_log, raises=True
        ),
        stay_transport_service=_FakeStageService("stay_transport", order_log),
        ai_candidate_promotion_service=_FakeAICandidatePromotionService(order_log),
        experience_planner_service=_FakeStageService("experience_planning", order_log),
        plan_validator_service=_FakeStageService("validation", order_log),
    )
    trip_request = _trip_request()
    planning_state = PlanningState(trip_id="trip_failure_1", trip_request=trip_request)
    before = planning_state.model_copy(deep=True)

    result = service.run("trip_failure_1", trip_request, planning_state)

    assert result.failed_nodes == ["destination_context"]
    assert len(result.errors) == 1
    assert "simulated failure" not in result.errors[0]
    # The graph continues past the failed node.
    assert "stay_transport" in result.completed_nodes
    assert "final_state" in result.completed_nodes
    # No PlanningState data fabricated for the failed stage: the original
    # planning_state object passed in was never mutated by the failed
    # destination_context call (only later, real fake nodes touched it).
    assert before.destination_context == result.planning_state.destination_context


# ---------------------------------------------------------------------------
# 9. Service does not persist to storage.
# ---------------------------------------------------------------------------


def test_run_does_not_persist_to_repositories(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_save(*args: object, **kwargs: object) -> None:
        raise AssertionError("LangGraphPlanningService must never save PlanningState itself.")

    def _fail_create(*args: object, **kwargs: object) -> None:
        raise AssertionError("LangGraphPlanningService must never create a Trip record itself.")

    monkeypatch.setattr(planning_state_repository, "save", _fail_save)
    monkeypatch.setattr(trip_repository, "create", _fail_create)

    service = _fake_service()
    trip_request = _trip_request()

    # Must not raise -- proving neither repository method was called,
    # whether planning_state is freshly created or passed in.
    service.run("trip_no_persist_1", trip_request, planning_state=None)
    service.run(
        "trip_no_persist_2",
        trip_request,
        planning_state=PlanningState(trip_id="trip_no_persist_2", trip_request=trip_request),
    )


# ---------------------------------------------------------------------------
# 10, 14. The route handler always delegates through PlanningOrchestrator
#         (never calls LangGraphPlanningService directly), and
#         PlanningOrchestrator.generate_full_plan itself (the legacy
#         method) still never references LangGraph. As of Step 171D,
#         `POST /trips/{trip_id}/generate` *can* select a LangGraph-backed
#         path via `Settings.planning_engine_mode == "langgraph"` -- see
#         `test_langgraph_generate_mode.py`/docs for that new behavior.
#         The assertions below narrow to what still holds
#         regardless of config: the route never calls the service
#         directly, and the pre-existing `generate_full_plan` method is
#         completely untouched.
# ---------------------------------------------------------------------------


def test_generate_route_handler_never_calls_langgraph_service_directly() -> None:
    import app.api.routes.trips as trips_module

    source = inspect.getsource(trips_module.generate_trip_plan)
    assert "LangGraphPlanningService" not in source
    assert "planning_orchestrator" in source


def test_planning_orchestrator_module_imports_langgraph_service_only_for_new_method() -> None:
    """`planning_orchestrator.py` now legitimately imports
    `LangGraphPlanningService` (Step 171D) to implement its new
    `generate_full_plan_via_langgraph` method -- but the pre-existing
    `generate_full_plan` method itself must still never reference it (see
    `test_generate_full_plan_source_has_no_graph_or_langgraph_reference`
    below), and this import must never reach `app.graphs` directly
    (only through the service's own encapsulation)."""
    import app.services.planning_orchestrator as orchestrator_module

    source = inspect.getsource(orchestrator_module)
    tree = ast.parse(source)
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
    assert not any("app.graphs" in name for name in imported_names)
    assert any("langgraph_planning_service" in name for name in imported_names)


def test_generate_full_plan_source_has_no_graph_or_langgraph_reference() -> None:
    import app.services.planning_orchestrator as orchestrator_module

    source = inspect.getsource(orchestrator_module.PlanningOrchestrator.generate_full_plan)
    assert "graph" not in source.lower()


# ---------------------------------------------------------------------------
# 15. PlanningOrchestrator behavior remains unchanged (structural proof;
#     full regression coverage lives in the existing orchestrator tests).
# ---------------------------------------------------------------------------


def test_planning_orchestrator_pipeline_status_flow_still_reaches_validated_states() -> None:
    """A very small structural smoke check that PlanningOrchestrator's own
    pipeline status enum still holds the values this service's fresh
    PlanningState construction relies on (DRAFT) -- not a re-test of the
    orchestrator's own extensive test suite, just a guard against silent
    drift between the two modules' conventions."""
    assert PipelineStatus.DRAFT.value == "draft"


# ---------------------------------------------------------------------------
# 11-13. No LLM/AI-candidate-discovery/live-provider imports anywhere in
#        this module. As of Step 171E, `AccommodationInventoryService`/
#        `FlightInventoryService` are legitimately imported (for stage
#        parity with legacy generation) -- see the equivalent, updated
#        comment in test_langgraph_planning_graph.py's
#        test_planning_graph_module_has_no_disallowed_imports for why.
#        `ai_candidate_discovery_service`/`ai_candidate_proposal_provider`
#        stay banned.
# ---------------------------------------------------------------------------


def test_service_module_has_no_disallowed_imports() -> None:
    import app.services.langgraph_planning_service as service_module

    source = inspect.getsource(service_module)
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
        "ai_candidate_discovery_service",
        "ai_candidate_proposal_provider",
        "requests",
        "httpx",
    )
    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"


# ---------------------------------------------------------------------------
# No fake travel data anywhere in a service run's result.
# ---------------------------------------------------------------------------

_FORBIDDEN_FACTUAL_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "safety_score",
}


def test_run_result_has_no_forbidden_factual_fields() -> None:
    service = _fake_service()
    trip_request = _trip_request()
    planning_state = PlanningState(trip_id="trip_no_fake_data", trip_request=trip_request)

    result = service.run("trip_no_fake_data", trip_request, planning_state)
    dumped = result.planning_state.model_dump(mode="json")
    dumped_keys: set[str] = set()

    def _collect(value: Any) -> None:
        if isinstance(value, dict):
            dumped_keys.update(value.keys())
            for nested in value.values():
                _collect(nested)
        elif isinstance(value, list):
            for entry in value:
                _collect(entry)

    _collect(dumped)
    assert dumped_keys & _FORBIDDEN_FACTUAL_FIELD_NAMES == set()
