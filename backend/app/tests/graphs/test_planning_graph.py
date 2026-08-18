from __future__ import annotations

import ast
import datetime
import inspect
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.graphs.planning_graph as planning_graph_module
import app.services.planning_orchestrator as orchestrator_module
from app.graphs import PlanningGraphRunner, build_planning_graph, run_planning_graph
from app.models.candidate_quality import CandidateQualityReport
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.repositories.planning_state_repository import PlanningStateRepository
from app.repositories.trip_repository import TripRepository
from app.services.ai_candidate_discovery_service import AICandidateDiscoveryService

# Tests for the LangGraph skeleton around the deterministic planning
# pipeline (Step 162B, docs/13_llm_reasoning_pipeline.md section 42). Every
# test here uses only in-file fake stage-service doubles -- no real
# provider/network call, no real LLM call (Groq/Anthropic/other), and no
# persistence. This module is architecture/resume foundation only: it does
# not replace `PlanningOrchestrator.generate_full_plan`, is not imported by
# any API route, and does not change `/trips/{trip_id}/generate` behavior.


class _FakeStageService:
    """Deterministic test double for any single-method (`run`) stage
    service. Never calls a provider, LLM, or network -- just marks that it
    ran on the planning state, so tests can assert per-node mutation
    without depending on any real stage service's actual logic.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.call_count = 0

    def run(self, planning_state: PlanningState) -> PlanningState:
        self.call_count += 1
        planning_state.data_sources_used = list(planning_state.data_sources_used) + [
            f"fake_{self.name}"
        ]
        return planning_state


class _FakeCandidateQualityService:
    """Deterministic test double for `CandidateQualityService.build_report`."""

    def __init__(self) -> None:
        self.call_count = 0

    def build_report(self, planning_state: PlanningState) -> CandidateQualityReport:
        self.call_count += 1
        return CandidateQualityReport(
            destination_name=planning_state.trip_request.primary_destination,
            generated_at=datetime.datetime.now(datetime.timezone.utc),
        )


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Testville, Testland",
        "origin_city": "Home City",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _planning_state() -> PlanningState:
    return PlanningState(trip_request=_trip_request())


def _fake_services() -> dict[str, Any]:
    return {
        "traveler_profile_service": _FakeStageService("traveler_profile"),
        "destination_context_service": _FakeStageService("destination_context"),
        "candidate_quality_service": _FakeCandidateQualityService(),
        "trip_strategy_service": _FakeStageService("trip_strategy"),
        "stay_transport_service": _FakeStageService("stay_transport"),
        "experience_planner_service": _FakeStageService("experience_plan"),
        "plan_validator_service": _FakeStageService("validation"),
    }


def _create_trip_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "destination_scope": "single_city",
        "primary_destination": "Testville, Testland",
        "origin_city": "Home City",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": "couple",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 1. The graph compiles.
# ---------------------------------------------------------------------------


def test_planning_graph_compiles() -> None:
    runner = PlanningGraphRunner(**_fake_services())
    assert runner._graph is not None


def test_build_planning_graph_compiles_directly() -> None:
    services = _fake_services()
    graph = build_planning_graph(
        services["traveler_profile_service"],
        services["destination_context_service"],
        services["candidate_quality_service"],
        services["trip_strategy_service"],
        services["stay_transport_service"],
        services["experience_planner_service"],
        services["plan_validator_service"],
    )
    assert graph is not None


# ---------------------------------------------------------------------------
# 2. Nodes run in the exact expected order.
# ---------------------------------------------------------------------------


def test_graph_runs_nodes_in_exact_expected_order() -> None:
    services = _fake_services()
    runner = PlanningGraphRunner(**services)

    initial_state = {"planning_state": _planning_state(), "executed_nodes": [], "errors": []}
    result = runner._graph.invoke(initial_state)

    assert result["executed_nodes"] == [
        "traveler_profile",
        "destination_context",
        "candidate_quality",
        "ai_candidate_shadow_placeholder",
        "trip_strategy",
        "stay_transport",
        "experience_plan",
        "validation",
    ]


# ---------------------------------------------------------------------------
# 3. The graph returns a PlanningState.
# ---------------------------------------------------------------------------


def test_run_returns_a_planning_state() -> None:
    runner = PlanningGraphRunner(**_fake_services())
    result = runner.run(_planning_state())
    assert isinstance(result, PlanningState)


def test_run_planning_graph_convenience_function_returns_a_planning_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uses the module-level convenience function, but with a
    PlanningGraphRunner constructed from fakes injected via monkeypatching
    the class itself -- still no real provider/network call.
    """
    fake_runner = PlanningGraphRunner(**_fake_services())
    monkeypatch.setattr(
        planning_graph_module, "PlanningGraphRunner", lambda: fake_runner
    )

    result = run_planning_graph(_planning_state())

    assert isinstance(result, PlanningState)


# ---------------------------------------------------------------------------
# 4. Nodes call injected fake services, never real ones.
# ---------------------------------------------------------------------------


def test_nodes_call_injected_fake_services_not_real_ones() -> None:
    services = _fake_services()
    runner = PlanningGraphRunner(**services)

    runner.run(_planning_state())

    assert services["traveler_profile_service"].call_count == 1
    assert services["destination_context_service"].call_count == 1
    assert services["candidate_quality_service"].call_count == 1
    assert services["trip_strategy_service"].call_count == 1
    assert services["stay_transport_service"].call_count == 1
    assert services["experience_planner_service"].call_count == 1
    assert services["plan_validator_service"].call_count == 1


def test_fake_service_mutations_are_visible_on_final_planning_state() -> None:
    runner = PlanningGraphRunner(**_fake_services())
    result = runner.run(_planning_state())

    assert result.data_sources_used == [
        "fake_traveler_profile",
        "fake_destination_context",
        "fake_trip_strategy",
        "fake_stay_transport",
        "fake_experience_plan",
        "fake_validation",
    ]
    assert result.candidate_quality_report is not None


# ---------------------------------------------------------------------------
# 5. AI candidate placeholder node is a no-op.
# ---------------------------------------------------------------------------


def test_ai_candidate_placeholder_node_does_not_call_discovery_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(self: AICandidateDiscoveryService, planning_state: object, **kwargs: Any) -> None:
        raise AssertionError("AICandidateDiscoveryService.dry_run must not be called in Step 162B")

    monkeypatch.setattr(AICandidateDiscoveryService, "dry_run", _fail)

    runner = PlanningGraphRunner(**_fake_services())
    runner.run(_planning_state())  # must not raise


def test_ai_candidate_placeholder_node_leaves_batches_unchanged() -> None:
    runner = PlanningGraphRunner(**_fake_services())
    planning_state = _planning_state()
    assert planning_state.ai_candidate_proposal_batch is None
    assert planning_state.candidate_grounding_batch is None

    result = runner.run(planning_state)

    assert result.ai_candidate_proposal_batch is None
    assert result.candidate_grounding_batch is None


def test_ai_candidate_placeholder_node_still_appears_in_executed_nodes() -> None:
    runner = PlanningGraphRunner(**_fake_services())
    initial_state = {"planning_state": _planning_state(), "executed_nodes": [], "errors": []}
    result = runner._graph.invoke(initial_state)
    assert "ai_candidate_shadow_placeholder" in result["executed_nodes"]


# ---------------------------------------------------------------------------
# 6. Graph runner never saves to any repository.
# ---------------------------------------------------------------------------


def test_graph_runner_never_saves_to_planning_state_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(self: PlanningStateRepository, planning_state: object) -> None:
        raise AssertionError("PlanningGraphRunner.run must never save to PlanningStateRepository")

    monkeypatch.setattr(PlanningStateRepository, "save", _fail)

    runner = PlanningGraphRunner(**_fake_services())
    runner.run(_planning_state())  # must not raise


def test_graph_runner_never_saves_to_trip_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(self: TripRepository, trip_id: object) -> None:
        raise AssertionError("PlanningGraphRunner.run must never call TripRepository.create")

    monkeypatch.setattr(TripRepository, "create", _fail)

    runner = PlanningGraphRunner(**_fake_services())
    runner.run(_planning_state())  # must not raise


# ---------------------------------------------------------------------------
# 7. Graph module is not imported by API routes or PlanningOrchestrator.
# ---------------------------------------------------------------------------


def _imported_names(nodes: list[ast.stmt]) -> list[str]:
    names: list[str] = []
    for node in nodes:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_planning_orchestrator_does_not_import_graph_module() -> None:
    source = inspect.getsource(orchestrator_module)
    tree = ast.parse(source)
    all_names = _imported_names(list(ast.walk(tree)))  # type: ignore[arg-type]
    assert not any("app.graphs" in name for name in all_names)
    assert "planning_graph" not in source
    assert "PlanningGraphRunner" not in source


def test_trips_route_does_not_import_graph_module() -> None:
    import app.api.routes.trips as trips_route_module

    source = inspect.getsource(trips_route_module)
    tree = ast.parse(source)
    all_names = _imported_names(list(ast.walk(tree)))  # type: ignore[arg-type]
    assert not any("app.graphs" in name for name in all_names)
    assert "planning_graph" not in source
    assert "PlanningGraphRunner" not in source


# ---------------------------------------------------------------------------
# 8. Existing /generate endpoint behavior remains unchanged.
# ---------------------------------------------------------------------------


def test_generate_endpoint_behavior_unchanged(client: TestClient) -> None:
    create_response = client.post("/trips", json=_create_trip_payload())
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200
    body = generate_response.json()
    assert body["success"] is True
    planning_state = body["data"]["planning_state"]
    assert planning_state["experience_plan"] is not None
    assert planning_state["validation_report"] is not None
    # The graph skeleton is not wired into generation -- these stay exactly
    # as before this step.
    assert planning_state["ai_candidate_proposal_batch"] is None
    assert planning_state["candidate_grounding_batch"] is None


# ---------------------------------------------------------------------------
# 9. No Groq/Anthropic/Kiwi/MCP/scraping calls are made -- static check.
# ---------------------------------------------------------------------------


def test_graph_module_has_no_disallowed_vendor_or_scraping_imports() -> None:
    source = inspect.getsource(planning_graph_module)
    tree = ast.parse(source)

    disallowed_substrings = (
        "groq",
        "anthropic",
        "openai",
        "gemini",
        "google.generativeai",
        "kiwi",
        "mcp",
        "requests",
        "beautifulsoup",
        "scrapy",
        "selenium",
        "playwright",
        "claude_code",
    )

    all_names = _imported_names(list(ast.walk(tree)))  # type: ignore[arg-type]
    for name in all_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"


def test_graph_module_never_constructs_ai_candidate_discovery_service() -> None:
    """Checks actual imports (never a raw substring scan, which would also
    flag this module's own explanatory comments about what the placeholder
    node deliberately does *not* do)."""
    source = inspect.getsource(planning_graph_module)
    tree = ast.parse(source)
    all_names = _imported_names(list(ast.walk(tree)))  # type: ignore[arg-type]

    assert not any("ai_candidate_discovery_service" in name.lower() for name in all_names)
    assert not any("ai_candidate_proposal" in name.lower() for name in all_names)


# ---------------------------------------------------------------------------
# 10. No LangSmith runtime import/configuration is added.
# ---------------------------------------------------------------------------


def test_graph_module_has_no_langsmith_import_or_configuration() -> None:
    source = inspect.getsource(planning_graph_module)
    tree = ast.parse(source)

    all_names = _imported_names(list(ast.walk(tree)))  # type: ignore[arg-type]
    assert not any("langsmith" in name.lower() for name in all_names)

    # No LangSmith tracing env var configuration either.
    for needle in ("LANGCHAIN_TRACING", "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"):
        assert needle not in source


# ---------------------------------------------------------------------------
# 11. Frontend remains untouched by this step.
# ---------------------------------------------------------------------------


def test_no_frontend_files_reference_planning_graph() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    needles = ("planning_graph", "PlanningGraphRunner", "PlanningGraphState", "langgraph")
    for subdir in ("frontend/app", "frontend/lib"):
        target = repo_root / subdir
        if not target.exists():
            continue
        for path in target.rglob("*"):
            if not path.is_file() or "node_modules" in path.parts:
                continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            for needle in needles:
                assert needle not in text, f"{path} unexpectedly references {needle}"


# ---------------------------------------------------------------------------
# 12. Existing full suite still passing is verified by running the full
#     pytest suite (see task verification steps) -- not something a single
#     test in this file can assert about other test files.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Extra: default (non-injected) construction uses the real stage services,
# proving dependency injection is optional, not required -- without
# actually running the real graph (which would need real provider/network
# access), only that construction and node wiring succeed.
# ---------------------------------------------------------------------------


def test_default_construction_uses_real_stage_services() -> None:
    from app.services.candidate_quality_service import CandidateQualityService
    from app.services.destination_context_service import DestinationContextService
    from app.services.experience_planner_service import ExperiencePlannerService
    from app.services.plan_validator_service import PlanValidatorService
    from app.services.stay_transport_service import StayTransportService
    from app.services.traveler_profile_service import TravelerProfileService
    from app.services.trip_strategy_service import TripStrategyService

    runner = PlanningGraphRunner()

    assert isinstance(runner.traveler_profile_service, TravelerProfileService)
    assert isinstance(runner.destination_context_service, DestinationContextService)
    assert isinstance(runner.candidate_quality_service, CandidateQualityService)
    assert isinstance(runner.trip_strategy_service, TripStrategyService)
    assert isinstance(runner.stay_transport_service, StayTransportService)
    assert isinstance(runner.experience_planner_service, ExperiencePlannerService)
    assert isinstance(runner.plan_validator_service, PlanValidatorService)
