from __future__ import annotations

import inspect
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.providers.ai_candidate_proposal.anthropic_adapter import AnthropicAICandidateProposalProvider
from app.providers.ai_candidate_proposal.groq_adapter import GroqAICandidateProposalProvider
from app.services.ai_candidate_discovery_service import AICandidateDiscoveryService
from app.services.planning_orchestrator import PlanningOrchestrator, planning_orchestrator

# Tests for the config-gated LangGraph `/generate` mode (Step 171D), made
# the default engine once it reached stage parity with legacy generation
# (Step 171E -- docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). `Settings.planning_engine_mode`
# (default `"langgraph"` as of this step, or explicit `"legacy"`) is the
# only config surface here -- `"legacy"` (or any unrecognized value) makes
# `POST /trips/{trip_id}/generate` call the original, completely
# unmodified `PlanningOrchestrator.generate_full_plan`; the default (and
# explicit `"langgraph"`) calls the new
# `PlanningOrchestrator.generate_full_plan_via_langgraph` (Step 171B's
# `LangGraphPlanningService` under the hood, now covering the same major
# planning-stage outputs as legacy generation -- see
# `planning_graph.py`'s module docstring for the exact stage order and the
# one documented, intentional gap). Every test here relies on the same
# deterministic, network-free test fixtures (autouse in conftest.py) the
# rest of the suite already uses -- no real provider/LLM/network call in
# either mode.


def _set_engine_mode(monkeypatch: pytest.MonkeyPatch, mode: str | None) -> None:
    if mode is None:
        monkeypatch.delenv("PLANNING_ENGINE_MODE", raising=False)
    else:
        monkeypatch.setenv("PLANNING_ENGINE_MODE", mode)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_settings_cache_after_test() -> Any:
    yield
    get_settings.cache_clear()


def _create_trip_payload() -> dict[str, Any]:
    return {
        "destination_scope": "single_city",
        "primary_destination": "Testville, Testland",
        "origin_city": "Home City",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": "couple",
    }


def _create_trip(client: TestClient) -> str:
    response = client.post("/trips", json=_create_trip_payload())
    assert response.status_code == 201
    return response.json()["data"]["trip_id"]


# ---------------------------------------------------------------------------
# 1-4. Config surface: default is now "langgraph", explicit "legacy" and
#      "langgraph" both work, and an unrecognized value safely falls back
#      to legacy.
# ---------------------------------------------------------------------------


def test_planning_engine_mode_defaults_to_langgraph() -> None:
    field_info = Settings.model_fields["planning_engine_mode"]
    assert field_info.default == "langgraph"
    assert field_info.alias == "PLANNING_ENGINE_MODE"


def test_settings_accepts_legacy_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("PLANNING_ENGINE_MODE", "legacy")

    settings = Settings()

    assert settings.planning_engine_mode == "legacy"


def test_settings_accepts_langgraph_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("PLANNING_ENGINE_MODE", "langgraph")

    settings = Settings()

    assert settings.planning_engine_mode == "langgraph"


def test_settings_accepts_unrecognized_value_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors the existing accommodation_provider/flight_provider/
    routing_provider convention: an unrecognized string is stored as-is by
    Settings (never a Pydantic validation error) -- the safe fallback to
    "legacy" behavior happens in the consuming route code, not here."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("PLANNING_ENGINE_MODE", "some_future_engine")

    settings = Settings()

    assert settings.planning_engine_mode == "some_future_engine"


def test_unrecognized_engine_mode_falls_back_to_legacy_behavior(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.langgraph_planning_service as service_module

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("An unrecognized PLANNING_ENGINE_MODE must fall back to the legacy path.")

    monkeypatch.setattr(service_module.LangGraphPlanningService, "run", _fail)
    _set_engine_mode(monkeypatch, "some_future_engine")

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    assert response.json()["data"]["planning_state"]["experience_plan"] is not None


# ---------------------------------------------------------------------------
# 2-3, 5-6. Explicit "legacy" uses PlanningOrchestrator directly; default
#      and explicit "langgraph" both use LangGraphPlanningService; legacy
#      remains fully available end to end.
# ---------------------------------------------------------------------------


def test_explicit_legacy_mode_never_calls_langgraph_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.langgraph_planning_service as service_module

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("PLANNING_ENGINE_MODE=legacy must never call LangGraphPlanningService.run.")

    monkeypatch.setattr(service_module.LangGraphPlanningService, "run", _fail)
    _set_engine_mode(monkeypatch, "legacy")

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    assert response.json()["data"]["planning_state"]["experience_plan"] is not None


def test_explicit_legacy_mode_generate_still_works_end_to_end(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy fallback remains fully available and passing -- an explicit
    PLANNING_ENGINE_MODE=legacy trip generates exactly the fields
    generate_full_plan has always produced."""
    _set_engine_mode(monkeypatch, "legacy")

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")
    planning_state = response.json()["data"]["planning_state"]

    assert response.status_code == 200
    assert planning_state["traveler_profile"] is not None
    assert planning_state["destination_context"] is not None
    assert planning_state["trip_strategy"] is not None
    assert planning_state["stay_transport"] is not None
    assert planning_state["experience_plan"] is not None
    assert planning_state["validation_report"] is not None
    assert planning_state["accommodation_inventory_report"] is not None
    assert planning_state["flight_inventory_report"] is not None
    assert planning_state["route_feasibility_report"] is not None


def test_default_generate_calls_langgraph_planning_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As of Step 171E, the *default* config (no PLANNING_ENGINE_MODE set)
    calls LangGraphPlanningService -- this is the behavioral flip this
    step makes."""
    import app.services.langgraph_planning_service as service_module

    call_count = 0
    original_run = service_module.LangGraphPlanningService.run

    def _spy_run(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return original_run(self, *args, **kwargs)

    monkeypatch.setattr(service_module.LangGraphPlanningService, "run", _spy_run)
    _set_engine_mode(monkeypatch, None)
    assert get_settings().planning_engine_mode == "langgraph"

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    assert call_count == 1
    assert response.json()["data"]["planning_state"]["experience_plan"] is not None


def test_langgraph_mode_generate_calls_langgraph_planning_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.langgraph_planning_service as service_module

    call_count = 0
    original_run = service_module.LangGraphPlanningService.run

    def _spy_run(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return original_run(self, *args, **kwargs)

    monkeypatch.setattr(service_module.LangGraphPlanningService, "run", _spy_run)
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    assert call_count == 1
    assert response.json()["data"]["planning_state"]["experience_plan"] is not None


# ---------------------------------------------------------------------------
# 7-8. langgraph mode persists the result, and the response shape matches
#      GET /trips/{id}.
# ---------------------------------------------------------------------------


def test_langgraph_mode_generate_persists_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    get_response = client.get(f"/trips/{trip_id}")
    assert get_response.status_code == 200
    stored_state = get_response.json()["data"]["planning_state"]

    assert stored_state["destination_context"] is not None
    assert stored_state["experience_plan"] is not None
    assert stored_state == generate_response.json()["data"]["planning_state"]


def test_langgraph_mode_generate_response_shape_matches_get_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    generate_response = client.post(f"/trips/{trip_id}/generate")
    get_response = client.get(f"/trips/{trip_id}")

    generate_data = generate_response.json()["data"]
    get_data = get_response.json()["data"]

    assert set(generate_data.keys()) == {"trip_id", "planning_state"}
    assert set(get_data.keys()) == {"trip_id", "planning_state"}
    assert set(generate_data["planning_state"].keys()) == set(get_data["planning_state"].keys())


# ---------------------------------------------------------------------------
# 9-14. langgraph mode produces/preserves destination_context,
#       accommodation_inventory_report, flight_inventory_report,
#       experience_plan, provider_coverage, and validation_report.
# ---------------------------------------------------------------------------


def test_langgraph_mode_generate_produces_core_parity_fields(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")
    planning_state = response.json()["data"]["planning_state"]

    assert planning_state["traveler_profile"] is not None
    assert planning_state["destination_context"] is not None
    assert planning_state["trip_strategy"] is not None
    assert planning_state["stay_transport"] is not None
    assert planning_state["accommodation_inventory_report"] is not None
    assert planning_state["flight_inventory_report"] is not None
    assert planning_state["experience_plan"] is not None
    assert planning_state["route_feasibility_report"] is not None
    assert planning_state["route_aware_sequencing_report"] is not None
    assert planning_state["travel_time_buffer_report"] is not None
    assert planning_state["validation_report"] is not None
    assert planning_state["provider_coverage"] is not None
    assert planning_state["metadata"]["pipeline_status"] in (
        "validated",
        "needs_review",
        "blocked",
    )


# ---------------------------------------------------------------------------
# 15. regeneration_readiness is recomputed after a langgraph-mode generate.
# ---------------------------------------------------------------------------


def test_langgraph_mode_generate_recomputes_regeneration_readiness(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    before = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]["regeneration_readiness"]
    assert before["status"] == "blocked"

    response = client.post(f"/trips/{trip_id}/generate")
    after = response.json()["data"]["planning_state"]["regeneration_readiness"]

    assert after != before
    assert response.json()["data"]["planning_state"]["version_history"]


def test_langgraph_mode_generate_updates_generation_progress(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    before = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]["generation_progress"]
    assert before["status"] == "idle"

    response = client.post(f"/trips/{trip_id}/generate")
    progress = response.json()["data"]["planning_state"]["generation_progress"]

    assert progress["status"] == "completed"
    assert progress["progress_percent"] == 100
    # Same per-stage granularity generate_full_plan reports (Step 163B) --
    # see _GRAPH_NODES_BY_GENERATION_STAGE_KEY in planning_orchestrator.py.
    assert progress["completed_stages"] == [
        "traveler_profile",
        "destination_context",
        "candidate_quality",
        "ai_candidate_shadow",
        "trip_strategy",
        "stay_transport",
        "experience_plan",
        "validation",
        "post_processing",
    ]


# ---------------------------------------------------------------------------
# 16-18. Route feasibility, route-aware scheduling, and travel-time buffer
#        config behavior are all consistent with legacy.
# ---------------------------------------------------------------------------


def test_langgraph_mode_route_aware_scheduling_disabled_by_default(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settings.route_aware_scheduling_enabled defaults to False -- same
    config flag, read the same way, in both engines -- so a langgraph-mode
    generate's route_aware_sequencing_report stays shadow/report-only,
    never applied to the real schedule, exactly like legacy's default."""
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")
    report = response.json()["data"]["planning_state"]["route_aware_sequencing_report"]

    assert report["is_shadow_only"] is True
    assert report["applied_to_itinerary"] is False


def test_langgraph_mode_route_aware_scheduling_config_flag_read_identically(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enabling Settings.route_aware_scheduling_enabled must not crash a
    langgraph-mode generate -- it reads the exact same config flag legacy
    reads (see build_route_aware_sequencing_node)."""
    monkeypatch.setenv("ROUTE_AWARE_SCHEDULING_ENABLED", "true")
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]
    assert planning_state["route_aware_sequencing_report"] is not None
    assert planning_state["route_feasibility_report"] is not None


def test_langgraph_mode_generate_produces_travel_time_buffer_report(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")
    report = response.json()["data"]["planning_state"]["travel_time_buffer_report"]

    assert report is not None
    assert report["uses_provider_backed_routes"] is True


# ---------------------------------------------------------------------------
# 19. LangGraph node order matches the real planning flow (also directly
#     exercised in test_langgraph_planning_graph.py/test_langgraph_
#     planning_service.py/test_langgraph_shadow_run.py's completed_nodes
#     assertions).
# ---------------------------------------------------------------------------


def test_langgraph_mode_generate_calls_stages_in_planning_flow_order(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.planning_orchestrator as orchestrator_module

    call_order: list[str] = []
    original = (
        orchestrator_module.PlanningOrchestrator._mark_stage_finished
    )

    def _spy(self: Any, planning_state: Any, stage_key: str) -> Any:
        call_order.append(stage_key)
        return original(self, planning_state, stage_key)

    monkeypatch.setattr(
        orchestrator_module.PlanningOrchestrator, "_mark_stage_finished", _spy
    )
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    client.post(f"/trips/{trip_id}/generate")

    assert call_order == [
        "traveler_profile",
        "destination_context",
        "candidate_quality",
        "ai_candidate_shadow",
        "trip_strategy",
        "stay_transport",
        "experience_plan",
        "validation",
        "post_processing",
    ]


# ---------------------------------------------------------------------------
# 20-21. Shadow endpoint still returns persisted=false and never mutates
#        stored state, unaffected by engine mode.
# ---------------------------------------------------------------------------


def test_shadow_run_endpoint_unaffected_by_engine_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.routes.trips as trips_module

    source = inspect.getsource(trips_module.run_langgraph_shadow)
    assert "planning_engine_mode" not in source
    assert "get_settings" not in source

    for mode in ("legacy", "langgraph", "some_future_engine"):
        _set_engine_mode(monkeypatch, mode)
        trip_id = _create_trip(client)
        response = client.post(f"/trips/{trip_id}/langgraph-shadow-run")
        assert response.status_code == 200
        assert response.json()["data"]["persisted"] is False

        stored = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
        assert stored["experience_plan"] is None


# ---------------------------------------------------------------------------
# 6, 12. PlanningOrchestrator.generate_full_plan (the legacy method)
#        remains fully available and callable directly, regardless of
#        config.
# ---------------------------------------------------------------------------


def test_planning_orchestrator_generate_full_plan_still_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_engine_mode(monkeypatch, "langgraph")
    assert hasattr(planning_orchestrator, "generate_full_plan")
    assert callable(planning_orchestrator.generate_full_plan)


def test_planning_orchestrator_can_be_constructed_without_langgraph_service_override() -> None:
    """The langgraph_planning_service constructor parameter is optional,
    defaulting to a real LangGraphPlanningService -- existing call sites
    that construct PlanningOrchestrator() with no arguments (or with only
    pre-171D arguments) keep working unchanged."""
    orchestrator = PlanningOrchestrator()
    assert orchestrator.langgraph_planning_service is not None
    assert hasattr(orchestrator, "generate_full_plan_via_langgraph")


# ---------------------------------------------------------------------------
# 24. Regeneration refusal (409) is unaffected by engine mode.
# ---------------------------------------------------------------------------


def test_regeneration_still_refused_regardless_of_engine_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    client.post(f"/trips/{trip_id}/generate")

    response = client.post(f"/trips/{trip_id}/regenerate")
    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_NOT_AVAILABLE"


# ---------------------------------------------------------------------------
# 25. Accommodation/flight provider behavior remains unchanged -- same
#     status semantics regardless of engine mode.
# ---------------------------------------------------------------------------


def test_accommodation_and_flight_inventory_status_consistent_across_engines(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_engine_mode(monkeypatch, "legacy")
    legacy_trip_id = _create_trip(client)
    legacy_state = client.post(f"/trips/{legacy_trip_id}/generate").json()["data"]["planning_state"]

    _set_engine_mode(monkeypatch, "langgraph")
    langgraph_trip_id = _create_trip(client)
    langgraph_state = client.post(f"/trips/{langgraph_trip_id}/generate").json()["data"][
        "planning_state"
    ]

    assert (
        legacy_state["accommodation_inventory_report"]["status"]
        == langgraph_state["accommodation_inventory_report"]["status"]
    )
    assert (
        legacy_state["flight_inventory_report"]["status"]
        == langgraph_state["flight_inventory_report"]["status"]
    )
    assert (
        legacy_state["provider_coverage"]["hotel_prices"]
        == langgraph_state["provider_coverage"]["hotel_prices"]
    )
    assert (
        legacy_state["provider_coverage"]["flights"]
        == langgraph_state["provider_coverage"]["flights"]
    )


# ---------------------------------------------------------------------------
# 26. AI candidate review/promotion behavior remains unchanged --
#     available and unaffected regardless of which engine generated the
#     plan. The AI candidate discovery *shadow* stage itself is
#     intentionally not part of the LangGraph engine's stage graph (see
#     build_ai_candidate_node's docstring) -- with shadow mode off (the
#     only supported configuration for the LangGraph engine today), the
#     review/promotion endpoints honestly report no candidate data in
#     both engines, which is the documented, tested reason this is not a
#     parity gap for the default configuration.
# ---------------------------------------------------------------------------


def test_ai_candidate_review_and_promotion_endpoints_unaffected_by_engine_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    client.post(f"/trips/{trip_id}/generate")

    review_response = client.get(f"/trips/{trip_id}/ai-candidate-review")
    assert review_response.status_code == 200
    assert review_response.json()["data"]["ai_candidate_review_report"]["status"] == (
        "no_candidate_data"
    )

    promote_response = client.post(f"/trips/{trip_id}/ai-candidate-promotions")
    assert promote_response.status_code == 200
    report = promote_response.json()["data"]["ai_candidate_promotion_report"]
    assert report["total_reviewed_candidates"] == 0
    assert report["promoted_candidates"] == []


# ---------------------------------------------------------------------------
# 27-28. No Groq/Anthropic call, and no AICandidateDiscoveryService call,
#        in langgraph mode's default (no AI candidate service injected)
#        ai_candidate node.
# ---------------------------------------------------------------------------


def test_langgraph_mode_generate_does_not_call_anthropic_or_groq(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("Anthropic/Groq provider must never be called by langgraph-mode /generate.")

    monkeypatch.setattr(AnthropicAICandidateProposalProvider, "propose", _fail)
    monkeypatch.setattr(GroqAICandidateProposalProvider, "propose", _fail)
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")
    assert response.status_code == 200


def test_langgraph_mode_generate_does_not_call_ai_candidate_discovery_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(self: Any, *args: object, **kwargs: object) -> None:
        raise AssertionError(
            "AICandidateDiscoveryService.dry_run must never be called by langgraph-mode "
            "/generate's default ai_candidate node."
        )

    monkeypatch.setattr(AICandidateDiscoveryService, "dry_run", _fail)
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# 22-23. Node failure in langgraph mode is handled safely: /generate still
#        succeeds, and no field is fabricated for the failed stage.
# ---------------------------------------------------------------------------


def test_langgraph_mode_generate_survives_a_node_failure_without_fabrication(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.destination_context_service import DestinationContextService

    def _raise(self: Any, planning_state: Any) -> Any:
        raise RuntimeError("simulated destination_context failure")

    monkeypatch.setattr(DestinationContextService, "run", _raise)
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]
    assert planning_state["destination_context"] is None
    # PlanValidatorService.run() (called directly by the graph's validation
    # node) still runs despite the earlier node failure, and honestly
    # reports "blocked" readiness for a plan with no destination context --
    # generate_full_plan_via_langgraph then applies the exact same
    # readiness->pipeline_status mapping run_validation_stage already uses.
    assert planning_state["validation_report"] is not None
    assert planning_state["metadata"]["pipeline_status"] == "blocked"


# ---------------------------------------------------------------------------
# 33. No fake travel data in a langgraph-mode /generate response.
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


def test_langgraph_mode_generate_response_has_no_forbidden_factual_fields(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_engine_mode(monkeypatch, "langgraph")

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")
    dumped = response.json()["data"]["planning_state"]
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


# ---------------------------------------------------------------------------
# The route handler's own source never calls LangGraphPlanningService
# directly -- always through planning_orchestrator, in every mode.
# ---------------------------------------------------------------------------


def test_generate_route_source_always_delegates_through_orchestrator() -> None:
    import app.api.routes.trips as trips_module

    source = inspect.getsource(trips_module.generate_trip_plan)
    assert "planning_orchestrator.generate_full_plan_via_langgraph" in source
    assert "planning_orchestrator.generate_full_plan(" in source
    assert "LangGraphPlanningService" not in source
