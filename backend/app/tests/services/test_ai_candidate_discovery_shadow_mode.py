from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
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
from app.models.candidate_grounding import (
    CandidateGroundingRequest,
    CandidateGroundingStatus,
    ProviderCandidateForGrounding,
)
from app.models.common import DataStatus, GeoPoint
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.providers.ai_candidate_proposal import factory as proposal_factory_module
from app.services import ai_candidate_discovery_service as discovery_module
from app.services import planning_orchestrator as orchestrator_module
from app.services.ai_candidate_discovery_service import (
    AICandidateDiscoveryDryRunResult,
    AICandidateDiscoveryService,
)
from app.services.candidate_grounding_service import CandidateGroundingService
from app.services.planning_orchestrator import PlanningOrchestrator

# Tests for the AI candidate discovery shadow stage (Step 161B,
# docs/13_llm_reasoning_pipeline.md section 40, docs/14_backend_
# architecture.md section 25). PlanningOrchestrator now imports
# AICandidateDiscoveryService (see test_ai_candidate_discovery_safety.py /
# test_ai_candidate_discovery_service.py for the updated Step 161B import
# assertion), but only to run it behind
# Settings.ai_candidate_discovery_shadow_mode_enabled, which defaults to
# False. This file proves:
#
# - The config flag defaults to disabled, and disabled leaves both Step
#   160C storage fields at None exactly as before (unchanged default
#   behavior).
# - Enabled with the default not_connected proposal provider, generation
#   still succeeds and stores an honest not_connected/skipped batch pair.
# - Enabled with a fake completed proposal/grounding result, both batches
#   are stored, but nothing about experience_plan, validation_report,
#   destination_context candidates, provider_coverage, or
#   data_sources_used changes -- and the batches are never fed to
#   ExperiencePlannerService or CandidateQualityService.
# - A missing ANTHROPIC_API_KEY (AI_CANDIDATE_PROPOSAL_PROVIDER=anthropic)
#   or an unexpected dry_run exception never crashes generation.
# - No forbidden factual field ever appears in a stored batch dump.

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

_TRIP_PAYLOAD: dict[str, Any] = {
    "destination_scope": "single_city",
    "primary_destination": "Testville, Testland",
    "origin_city": "Home City",
    "start_date": "2026-08-10",
    "end_date": "2026-08-12",
    "travelers_count": 2,
    "travel_group_type": "couple",
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


def _enable_shadow_mode(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    fields: dict[str, Any] = {"ai_candidate_discovery_shadow_mode_enabled": True}
    fields.update(overrides)
    monkeypatch.setattr(orchestrator_module, "get_settings", lambda: Settings(**fields))


def _strip_ids(value: Any) -> Any:
    """Recursively drops every `*_id` dict key so two independently
    generated plans (random uuid4-based ids) can be compared structurally.
    """
    if isinstance(value, dict):
        return {key: _strip_ids(val) for key, val in value.items() if not key.endswith("_id")}
    if isinstance(value, list):
        return [_strip_ids(item) for item in value]
    return value


def _collect_keys(value: Any, keys: set[str]) -> None:
    if isinstance(value, dict):
        keys.update(value.keys())
        for nested in value.values():
            _collect_keys(nested, keys)
    elif isinstance(value, list):
        for item in value:
            _collect_keys(item, keys)


class _FakeCompletedAICandidateDiscoveryService:
    """Test double whose `dry_run` always returns a fixed, already-validated
    completed proposal_result and completed grounding_result -- exercises
    PlanningOrchestrator's shadow-mode wiring only, never a real LLM call.
    Reuses the real `CandidateGroundingService.ground` so the grounding
    result is genuinely, deterministically produced rather than hand-built.
    """

    def dry_run(
        self,
        planning_state: PlanningState,
        task: AICandidateProposalTask = AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
        max_candidates: int = 15,
    ) -> AICandidateDiscoveryDryRunResult:
        trip_id = planning_state.trip_id
        destination_name = planning_state.trip_request.primary_destination

        proposal = AICandidateProposal(
            proposal_id="shadow_proposal_001",
            candidate_name="Old Town Waterfront",
            candidate_type=AICandidateType.NEIGHBORHOOD,
            why_consider="Locally known for evening walks, may be under-tagged in provider data.",
            verification_requirements=[
                AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION
            ],
            confidence=0.6,
        )
        proposal_request = AICandidateProposalRequest(
            task=task,
            trip_id=trip_id,
            destination_name=destination_name,
            trip_duration_days=3,
        )
        proposal_result = AICandidateProposalResult(
            task=task,
            status=AICandidateProposalStatus.COMPLETED,
            proposals=[proposal],
            guardrail_report=AICandidateProposalGuardrailReport(passed=True),
            provider_name="fake_shadow_test_provider",
            confidence=0.6,
        )

        provider_candidate = ProviderCandidateForGrounding(
            provider_name="openstreetmap_places",
            provider_place_id="way/999",
            name="Old Town Waterfront",
            category="neighborhood",
            coordinates=GeoPoint(lat=38.7223, lng=-9.1393),
            data_status=DataStatus.LIVE,
            confidence=0.9,
        )
        grounding_request = CandidateGroundingRequest(
            trip_id=trip_id,
            destination_name=destination_name,
            proposals=[proposal],
            provider_candidates=[provider_candidate],
            provider_candidate_summary={"attraction": 0, "restaurant": 0, "accommodation": 0},
        )
        grounding_result = CandidateGroundingService().ground(grounding_request)

        return AICandidateDiscoveryDryRunResult(
            proposal_request=proposal_request,
            proposal_result=proposal_result,
            grounding_request=grounding_request,
            grounding_result=grounding_result,
        )


class _RaisingAICandidateDiscoveryService:
    """Test double simulating an unexpected dry_run failure."""

    def dry_run(self, planning_state: PlanningState, **kwargs: Any) -> AICandidateDiscoveryDryRunResult:
        raise RuntimeError("simulated unexpected discovery failure")


# ---------------------------------------------------------------------------
# 1. Config default is disabled.
# ---------------------------------------------------------------------------


def test_shadow_mode_config_default_is_disabled() -> None:
    assert Settings().ai_candidate_discovery_shadow_mode_enabled is False


# ---------------------------------------------------------------------------
# 2-3. Default config: generate still leaves both batch fields None.
# ---------------------------------------------------------------------------


def test_generate_with_default_config_leaves_batches_none(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]
    assert planning_state["ai_candidate_proposal_batch"] is None
    assert planning_state["candidate_grounding_batch"] is None


# ---------------------------------------------------------------------------
# 4-6. Shadow mode enabled + default not_connected provider: generation
#      succeeds and stores an honest not_connected/skipped batch pair.
# ---------------------------------------------------------------------------


def test_shadow_mode_enabled_with_default_provider_generation_succeeds(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_shadow_mode(monkeypatch)
    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 200


def test_shadow_mode_enabled_with_default_provider_stores_not_connected_proposal_batch(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_shadow_mode(monkeypatch)
    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]

    proposal_batch = planning_state["ai_candidate_proposal_batch"]
    assert proposal_batch is not None
    assert proposal_batch["result"]["status"] == "not_connected"
    assert proposal_batch["result"]["proposals"] == []


def test_shadow_mode_enabled_with_default_provider_stores_skipped_grounding_batch(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_shadow_mode(monkeypatch)
    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]

    grounding_batch = planning_state["candidate_grounding_batch"]
    assert grounding_batch is not None
    assert grounding_batch["result"]["status"] == "skipped"
    assert grounding_batch["result"]["grounded_candidates"] == []
    assert grounding_batch["result"]["rejected_proposals"] == []


# ---------------------------------------------------------------------------
# 7-10. Shadow mode changes nothing else about generation output.
# ---------------------------------------------------------------------------


def test_shadow_mode_does_not_change_generation_output(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_response = client.post("/trips", json=_TRIP_PAYLOAD)
    assert create_response.status_code == 201
    disabled_trip_id = create_response.json()["data"]["trip_id"]
    disabled_generate = client.post(f"/trips/{disabled_trip_id}/generate")
    assert disabled_generate.status_code == 200
    disabled_state = disabled_generate.json()["data"]["planning_state"]

    _enable_shadow_mode(monkeypatch)
    create_response = client.post("/trips", json=_TRIP_PAYLOAD)
    assert create_response.status_code == 201
    enabled_trip_id = create_response.json()["data"]["trip_id"]
    enabled_generate = client.post(f"/trips/{enabled_trip_id}/generate")
    assert enabled_generate.status_code == 200
    enabled_state = enabled_generate.json()["data"]["planning_state"]

    # 7. experience_plan is unaffected.
    assert _strip_ids(disabled_state["experience_plan"]) == _strip_ids(enabled_state["experience_plan"])
    # 8. validation_report.readiness_status is unaffected.
    assert (
        disabled_state["validation_report"]["readiness_status"]
        == enabled_state["validation_report"]["readiness_status"]
    )
    # 9. destination_context candidate lists are unaffected.
    assert _strip_ids(disabled_state["destination_context"]["candidate_pois"]) == _strip_ids(
        enabled_state["destination_context"]["candidate_pois"]
    )
    assert _strip_ids(disabled_state["destination_context"]["candidate_restaurants"]) == _strip_ids(
        enabled_state["destination_context"]["candidate_restaurants"]
    )
    assert _strip_ids(
        disabled_state["destination_context"]["candidate_accommodation_pois"]
    ) == _strip_ids(enabled_state["destination_context"]["candidate_accommodation_pois"])
    # 10. No AI provider name is added to data_sources_used/provider_coverage.
    assert enabled_state["data_sources_used"] == disabled_state["data_sources_used"]
    assert "anthropic" not in enabled_state["data_sources_used"]
    assert "anthropic_ai_candidate_proposal_provider" not in enabled_state["data_sources_used"]
    assert "ai_candidate_proposal_provider" not in enabled_state["data_sources_used"]
    assert enabled_state["provider_coverage"] == disabled_state["provider_coverage"]


# ---------------------------------------------------------------------------
# 11. Shadow mode batches persist through a repository round-trip.
# ---------------------------------------------------------------------------


def test_shadow_mode_batches_persist_in_repository_round_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_shadow_mode(monkeypatch)
    create_response = client.post("/trips", json=_TRIP_PAYLOAD)
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    get_response = client.get(f"/trips/{trip_id}")
    assert get_response.status_code == 200
    planning_state = get_response.json()["data"]["planning_state"]

    assert planning_state["ai_candidate_proposal_batch"] is not None
    assert planning_state["ai_candidate_proposal_batch"]["result"]["status"] == "not_connected"
    assert planning_state["candidate_grounding_batch"] is not None
    assert planning_state["candidate_grounding_batch"]["result"]["status"] == "skipped"


# ---------------------------------------------------------------------------
# 12. Missing ANTHROPIC_API_KEY with AI_CANDIDATE_PROPOSAL_PROVIDER=anthropic
#     never crashes the shadow stage.
# ---------------------------------------------------------------------------


def test_shadow_mode_with_anthropic_provider_and_no_api_key_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # AICandidateDiscoveryService resolves its default provider through the
    # factory at construction time, so the factory's settings lookup (not
    # the orchestrator's) must be patched for this to actually construct an
    # AnthropicAICandidateProposalProvider.
    monkeypatch.setattr(
        proposal_factory_module,
        "get_settings",
        lambda: Settings(ai_candidate_proposal_provider="anthropic", anthropic_api_key=None),
    )
    discovery_service = AICandidateDiscoveryService()
    orchestrator = PlanningOrchestrator(ai_candidate_discovery_service=discovery_service)
    _enable_shadow_mode(monkeypatch)

    planning_state = PlanningState(trip_request=_trip_request())
    planning_state = orchestrator.run_destination_context_stage(planning_state)  # must not raise

    assert planning_state.ai_candidate_proposal_batch is not None
    assert planning_state.ai_candidate_proposal_batch.result.status == AICandidateProposalStatus.NOT_CONNECTED
    assert planning_state.candidate_grounding_batch is not None
    assert planning_state.candidate_grounding_batch.result.status == CandidateGroundingStatus.SKIPPED


# ---------------------------------------------------------------------------
# 13. A fake completed AICandidateDiscoveryService stores both batches.
# ---------------------------------------------------------------------------


def test_shadow_mode_with_fake_completed_discovery_service_stores_both_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_shadow_mode(monkeypatch)
    orchestrator = PlanningOrchestrator(
        ai_candidate_discovery_service=_FakeCompletedAICandidateDiscoveryService()  # type: ignore[arg-type]
    )

    planning_state = PlanningState(trip_request=_trip_request())
    planning_state = orchestrator.run_destination_context_stage(planning_state)

    assert planning_state.ai_candidate_proposal_batch is not None
    assert planning_state.ai_candidate_proposal_batch.result.status == AICandidateProposalStatus.COMPLETED
    assert planning_state.candidate_grounding_batch is not None
    assert planning_state.candidate_grounding_batch.result.status == CandidateGroundingStatus.COMPLETED
    assert len(planning_state.candidate_grounding_batch.result.grounded_candidates) == 1


# ---------------------------------------------------------------------------
# 14-15. Even with a completed shadow grounding result, ExperiencePlannerService
#        and CandidateQualityService never consume it.
# ---------------------------------------------------------------------------


def test_shadow_mode_grounded_candidates_are_never_scheduled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_shadow_mode(monkeypatch)
    orchestrator = PlanningOrchestrator(
        ai_candidate_discovery_service=_FakeCompletedAICandidateDiscoveryService()  # type: ignore[arg-type]
    )

    created = orchestrator.create_trip(_trip_request())
    planning_state = orchestrator.generate_full_plan(created.trip_id)

    assert planning_state.candidate_grounding_batch is not None
    assert planning_state.candidate_grounding_batch.result.status == CandidateGroundingStatus.COMPLETED

    scheduled_names = {
        experience.name
        for day_plan in planning_state.experience_plan.daily_plans
        for experience in day_plan.experiences
    }
    # "Old Town Waterfront" is the fake shadow-grounded candidate's name --
    # it must never be scheduled, since ExperiencePlannerService only reads
    # destination_context, never ai_candidate_proposal_batch/
    # candidate_grounding_batch.
    assert "Old Town Waterfront" not in scheduled_names


def test_experience_planner_service_does_not_reference_shadow_batches() -> None:
    import app.services.experience_planner_service as module

    source = inspect.getsource(module)
    assert "candidate_grounding_batch" not in source
    assert "ai_candidate_proposal_batch" not in source
    assert "GroundedCandidate" not in source


def test_candidate_quality_service_does_not_reference_shadow_batches() -> None:
    import app.services.candidate_quality_service as module

    source = inspect.getsource(module)
    assert "candidate_grounding_batch" not in source
    assert "ai_candidate_proposal_batch" not in source
    assert "GroundedCandidate" not in source


# ---------------------------------------------------------------------------
# Disabled shadow mode ignores an injected discovery service entirely.
# ---------------------------------------------------------------------------


def test_shadow_mode_disabled_ignores_injected_discovery_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator_module,
        "get_settings",
        lambda: Settings(ai_candidate_discovery_shadow_mode_enabled=False),
    )
    orchestrator = PlanningOrchestrator(
        ai_candidate_discovery_service=_FakeCompletedAICandidateDiscoveryService()  # type: ignore[arg-type]
    )

    planning_state = PlanningState(trip_request=_trip_request())
    planning_state = orchestrator.run_destination_context_stage(planning_state)

    assert planning_state.ai_candidate_proposal_batch is None
    assert planning_state.candidate_grounding_batch is None


# ---------------------------------------------------------------------------
# An unexpected dry_run exception never crashes generation.
# ---------------------------------------------------------------------------


def test_shadow_mode_swallows_unexpected_dry_run_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_shadow_mode(monkeypatch)
    orchestrator = PlanningOrchestrator(
        ai_candidate_discovery_service=_RaisingAICandidateDiscoveryService()  # type: ignore[arg-type]
    )

    planning_state = PlanningState(trip_request=_trip_request())
    planning_state = orchestrator.run_destination_context_stage(planning_state)  # must not raise

    assert planning_state.ai_candidate_proposal_batch is None
    assert planning_state.candidate_grounding_batch is None
    assert planning_state.destination_context is not None


# ---------------------------------------------------------------------------
# 17. No disallowed vendor imports in PlanningOrchestrator.
# ---------------------------------------------------------------------------


def test_planning_orchestrator_module_has_no_disallowed_vendor_imports() -> None:
    source = inspect.getsource(orchestrator_module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    disallowed_substrings = ("langgraph", "langsmith", "openai", "gemini", "google.generativeai")
    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"

    # `anthropic` itself must never be imported directly by the orchestrator
    # -- only reachable indirectly through the config-gated factory/adapter.
    assert not any(name == "anthropic" or name.startswith("anthropic.") for name in imported_names)


# ---------------------------------------------------------------------------
# 20. No forbidden factual fields in stored shadow batch dumps.
# ---------------------------------------------------------------------------


def test_shadow_batches_dump_has_no_forbidden_factual_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_shadow_mode(monkeypatch)
    orchestrator = PlanningOrchestrator(
        ai_candidate_discovery_service=_FakeCompletedAICandidateDiscoveryService()  # type: ignore[arg-type]
    )

    planning_state = PlanningState(trip_request=_trip_request())
    planning_state = orchestrator.run_destination_context_stage(planning_state)

    assert planning_state.ai_candidate_proposal_batch is not None
    assert planning_state.candidate_grounding_batch is not None

    all_keys: set[str] = set()
    _collect_keys(planning_state.ai_candidate_proposal_batch.model_dump(), all_keys)
    _collect_keys(planning_state.candidate_grounding_batch.model_dump(), all_keys)
    overlap = all_keys & _FORBIDDEN_MODEL_FIELD_NAMES
    assert overlap == set(), f"Shadow batch dump has forbidden key(s): {overlap}"


# ---------------------------------------------------------------------------
# Reference sanity checks that discovery_module is still importable/unused
# elsewhere the way earlier steps established (belt-and-braces alongside
# test_ai_candidate_discovery_safety.py / test_ai_candidate_discovery_service.py).
# ---------------------------------------------------------------------------


def test_discovery_service_module_still_has_no_disallowed_vendor_imports() -> None:
    source = inspect.getsource(discovery_module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    disallowed_substrings = ("langgraph", "langsmith", "openai", "anthropic", "gemini", "google.generativeai")
    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"
