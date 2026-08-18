from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.providers.ai_candidate_proposal.anthropic_adapter as anthropic_adapter_module
import app.providers.ai_candidate_proposal.groq_adapter as groq_adapter_module
import app.services.planning_orchestrator as orchestrator_module
from app.core.config import Settings
from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateProposalGuardrailReport,
    AICandidateProposalRequest,
    AICandidateProposalResult,
    AICandidateProposalStatus,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.models.planning_state import DestinationContext, PlanningState, TravelGroupType, TripRequest
from app.providers.ai_candidate_proposal import AICandidateProposalProvider
from app.providers.ai_candidate_proposal.anthropic_adapter import AnthropicAICandidateProposalProvider
from app.providers.ai_candidate_proposal.groq_adapter import GroqAICandidateProposalProvider
from app.services.ai_candidate_discovery_service import AICandidateDiscoveryService
from app.services.planning_orchestrator import PlanningOrchestrator

# Tests for the config-gated AI candidate discovery shadow-mode integration
# (Step 161B, docs/13_llm_reasoning_pipeline.md section 40,
# docs/14_backend_architecture.md section 26). This wires
# `AICandidateDiscoveryService.dry_run` (Steps 157A-160E, hardened in Step
# 160D, extended with a real adapter in Step 161A) into
# `PlanningOrchestrator` for the first time, but only behind
# `Settings.ai_candidate_discovery_shadow_mode_enabled` (default `False`).
#
# The point of "shadow mode" is that it stores validated artifacts for
# inspection only -- it must never change what the plan actually contains
# or how ready it is judged to be. Every comparison test here proves that
# by running the same trip request through the full pipeline twice (shadow
# disabled, then enabled) and asserting the observable plan is identical.


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


def _place(
    name: str = "Test Fixture Attraction One",
    *,
    lat: float = 0.0,
    lng: float = 0.0,
    category: str | None = "attraction",
    confidence: float = 0.6,
    source: str = "openstreetmap_places",
    data_status: str = "live",
    place_id: str = "test/attraction/1",
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


def _planning_state_with_destination_context() -> PlanningState:
    planning_state = PlanningState(trip_request=_trip_request())
    planning_state.destination_context = DestinationContext(
        destination_name=planning_state.trip_request.primary_destination,
        candidate_pois=[_place()],
    )
    return planning_state


def _valid_proposal(**overrides: object) -> AICandidateProposal:
    fields: dict[str, object] = {
        "proposal_id": "proposal_001",
        "candidate_name": "Test Fixture Attraction One",
        "candidate_type": AICandidateType.ATTRACTION,
        "why_consider": "Locally known landmark that may be under-tagged in provider data.",
        "verification_requirements": [
            AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION
        ],
        "confidence": 0.5,
    }
    fields.update(overrides)
    return AICandidateProposal(**fields)


class _FakeAICandidateProposalProvider(AICandidateProposalProvider):
    """Deterministic test double -- never calls an LLM or provider, and
    only ever echoes back the exact proposals it was constructed with.
    """

    provider_name = "fake_shadow_mode_test_ai_candidate_proposal_provider"

    def __init__(self, proposals: list[AICandidateProposal]) -> None:
        self._proposals = proposals

    def propose(self, request: AICandidateProposalRequest) -> AICandidateProposalResult:
        return AICandidateProposalResult(
            task=request.task,
            status=AICandidateProposalStatus.COMPLETED,
            proposals=self._proposals,
            guardrail_report=AICandidateProposalGuardrailReport(passed=True),
            provider_name=self.provider_name,
            confidence=0.6,
        )


class _RaisingAICandidateDiscoveryService:
    """Test double simulating a broken discovery service -- `dry_run`
    always raises, used to prove the shadow stage fails safe.
    """

    def dry_run(self, planning_state: PlanningState, **kwargs: Any) -> Any:
        raise RuntimeError("simulated dry_run failure")


def _run_full_pipeline(orchestrator: PlanningOrchestrator, trip_request: TripRequest) -> PlanningState:
    planning_state = orchestrator.create_trip(trip_request)
    for run_stage in (
        orchestrator.run_traveler_profile_stage,
        orchestrator.run_destination_context_stage,
        orchestrator.run_trip_strategy_stage,
        orchestrator.run_stay_transport_stage,
        orchestrator.run_experience_plan_stage,
        orchestrator.run_validation_stage,
    ):
        planning_state = run_stage(planning_state)
    return planning_state


def _strip_ids(value: Any) -> Any:
    """Recursively strips any dict key ending in `_id` (all of these are
    freshly `uuid4`-generated per run) so two otherwise-identical pipeline
    runs can be compared for structural equality.
    """
    if isinstance(value, dict):
        return {key: _strip_ids(val) for key, val in value.items() if not key.endswith("_id")}
    if isinstance(value, list):
        return [_strip_ids(item) for item in value]
    return value


def _disabled_and_enabled_states(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[PlanningState, PlanningState]:
    monkeypatch.setattr(orchestrator_module, "get_settings", lambda: Settings())
    disabled_state = _run_full_pipeline(PlanningOrchestrator(), _trip_request())

    monkeypatch.setattr(
        orchestrator_module,
        "get_settings",
        lambda: Settings(AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=True),
    )
    enabled_state = _run_full_pipeline(PlanningOrchestrator(), _trip_request())

    return disabled_state, enabled_state


def _generate_with_fake_completed_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> str:
    monkeypatch.setattr(
        orchestrator_module,
        "get_settings",
        lambda: Settings(AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=True),
    )
    fake_provider = _FakeAICandidateProposalProvider(
        [_valid_proposal(candidate_name="Test Fixture Attraction One")]
    )
    fake_service = AICandidateDiscoveryService(proposal_provider=fake_provider)
    monkeypatch.setattr(
        orchestrator_module.planning_orchestrator, "ai_candidate_discovery_service", fake_service
    )

    create_response = client.post("/trips", json=_create_trip_payload())
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200
    return trip_id


# ---------------------------------------------------------------------------
# 1. Config default is False.
# ---------------------------------------------------------------------------


def test_config_default_is_false() -> None:
    field_info = Settings.model_fields["ai_candidate_discovery_shadow_mode_enabled"]
    assert field_info.default is False
    assert field_info.alias == "AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED"
    assert Settings(_env_file=None).ai_candidate_discovery_shadow_mode_enabled is False


# ---------------------------------------------------------------------------
# 2-3. Default POST /trips/{id}/generate leaves both batch fields None.
# ---------------------------------------------------------------------------


def test_default_generate_leaves_both_batch_fields_none(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orchestrator_module, "get_settings", lambda: Settings())

    create_response = client.post("/trips", json=_create_trip_payload())
    trip_id = create_response.json()["data"]["trip_id"]
    generate_response = client.post(f"/trips/{trip_id}/generate")

    assert generate_response.status_code == 200
    planning_state = generate_response.json()["data"]["planning_state"]
    assert planning_state["ai_candidate_proposal_batch"] is None
    assert planning_state["candidate_grounding_batch"] is None


def test_shadow_stage_is_noop_when_disabled_even_with_destination_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orchestrator_module, "get_settings", lambda: Settings())
    orchestrator = PlanningOrchestrator()
    planning_state = _planning_state_with_destination_context()
    before = planning_state.model_copy(deep=True)

    result_state = orchestrator._run_ai_candidate_discovery_shadow_stage(planning_state)

    assert result_state.ai_candidate_proposal_batch is None
    assert result_state.candidate_grounding_batch is None
    assert result_state == before


# ---------------------------------------------------------------------------
# 4-6. Shadow enabled + default not_connected provider.
# ---------------------------------------------------------------------------


def _enable_shadow_mode_with_not_connected_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers.ai_candidate_proposal.not_connected_adapter import (
        NotConnectedAICandidateProposalProvider,
    )

    monkeypatch.setattr(
        orchestrator_module,
        "get_settings",
        lambda: Settings(AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=True),
    )
    monkeypatch.setattr(
        orchestrator_module.planning_orchestrator,
        "ai_candidate_discovery_service",
        AICandidateDiscoveryService(proposal_provider=NotConnectedAICandidateProposalProvider()),
    )


def test_shadow_enabled_default_provider_generation_succeeds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_shadow_mode_with_not_connected_provider(monkeypatch)

    create_response = client.post("/trips", json=_create_trip_payload())
    trip_id = create_response.json()["data"]["trip_id"]
    generate_response = client.post(f"/trips/{trip_id}/generate")

    assert generate_response.status_code == 200


def test_shadow_enabled_default_provider_stores_not_connected_proposal_batch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_shadow_mode_with_not_connected_provider(monkeypatch)

    create_response = client.post("/trips", json=_create_trip_payload())
    trip_id = create_response.json()["data"]["trip_id"]
    generate_response = client.post(f"/trips/{trip_id}/generate")

    proposal_batch = generate_response.json()["data"]["planning_state"]["ai_candidate_proposal_batch"]
    assert proposal_batch is not None
    assert proposal_batch["result"]["status"] == "not_connected"
    assert proposal_batch["result"]["proposals"] == []


def test_shadow_enabled_default_provider_stores_skipped_grounding_batch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_shadow_mode_with_not_connected_provider(monkeypatch)

    create_response = client.post("/trips", json=_create_trip_payload())
    trip_id = create_response.json()["data"]["trip_id"]
    generate_response = client.post(f"/trips/{trip_id}/generate")

    grounding_batch = generate_response.json()["data"]["planning_state"]["candidate_grounding_batch"]
    assert grounding_batch is not None
    assert grounding_batch["result"]["status"] == "skipped"
    assert grounding_batch["result"]["grounded_candidates"] == []
    assert grounding_batch["result"]["rejected_proposals"] == []


# ---------------------------------------------------------------------------
# 7-10. Shadow mode changes nothing observable about the plan itself.
# ---------------------------------------------------------------------------


def test_shadow_mode_does_not_change_experience_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    disabled_state, enabled_state = _disabled_and_enabled_states(monkeypatch)

    assert enabled_state.ai_candidate_proposal_batch is not None
    assert _strip_ids(disabled_state.experience_plan.model_dump()) == _strip_ids(
        enabled_state.experience_plan.model_dump()
    )


def test_shadow_mode_does_not_change_validation_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    disabled_state, enabled_state = _disabled_and_enabled_states(monkeypatch)

    assert enabled_state.ai_candidate_proposal_batch is not None
    assert (
        disabled_state.validation_report.readiness_status
        == enabled_state.validation_report.readiness_status
    )
    assert disabled_state.validation_report.critical_issues == enabled_state.validation_report.critical_issues


def test_shadow_mode_does_not_change_destination_context_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disabled_state, enabled_state = _disabled_and_enabled_states(monkeypatch)

    assert enabled_state.ai_candidate_proposal_batch is not None
    assert _strip_ids(disabled_state.destination_context.model_dump()) == _strip_ids(
        enabled_state.destination_context.model_dump()
    )


def test_shadow_mode_does_not_add_ai_sources_to_data_sources_used_or_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disabled_state, enabled_state = _disabled_and_enabled_states(monkeypatch)

    assert enabled_state.ai_candidate_proposal_batch is not None
    ai_related_substrings = ("ai_candidate", "candidate_grounding", "anthropic")
    for source in enabled_state.data_sources_used:
        assert not any(substring in source.lower() for substring in ai_related_substrings)

    assert set(disabled_state.data_sources_used) == set(enabled_state.data_sources_used)
    assert disabled_state.provider_coverage.model_dump() == enabled_state.provider_coverage.model_dump()


# ---------------------------------------------------------------------------
# 11. Shadow mode batches persist through a repository round-trip.
# ---------------------------------------------------------------------------


def test_shadow_mode_batches_persist_through_repository_round_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_shadow_mode_with_not_connected_provider(monkeypatch)

    create_response = client.post("/trips", json=_create_trip_payload())
    trip_id = create_response.json()["data"]["trip_id"]
    generate_response = client.post(f"/trips/{trip_id}/generate")
    generated_state = generate_response.json()["data"]["planning_state"]
    assert generated_state["ai_candidate_proposal_batch"] is not None
    assert generated_state["candidate_grounding_batch"] is not None

    get_response = client.get(f"/trips/{trip_id}")
    assert get_response.status_code == 200
    loaded_state = get_response.json()["data"]["planning_state"]

    assert loaded_state["ai_candidate_proposal_batch"] == generated_state["ai_candidate_proposal_batch"]
    assert loaded_state["candidate_grounding_batch"] == generated_state["candidate_grounding_batch"]


# ---------------------------------------------------------------------------
# 12. Shadow mode with provider=anthropic and no ANTHROPIC_API_KEY does not
#     crash generation.
# ---------------------------------------------------------------------------


def test_shadow_mode_with_anthropic_provider_missing_api_key_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        anthropic_adapter_module, "get_settings", lambda: Settings(ANTHROPIC_API_KEY=None)
    )
    monkeypatch.setattr(
        orchestrator_module,
        "get_settings",
        lambda: Settings(AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=True),
    )
    provider = AnthropicAICandidateProposalProvider(api_key=None)
    service = AICandidateDiscoveryService(proposal_provider=provider)
    orchestrator = PlanningOrchestrator(ai_candidate_discovery_service=service)

    planning_state = orchestrator.create_trip(_trip_request())
    planning_state = orchestrator.run_traveler_profile_stage(planning_state)
    planning_state = orchestrator.run_destination_context_stage(planning_state)  # must not raise

    assert planning_state.ai_candidate_proposal_batch is not None
    assert planning_state.ai_candidate_proposal_batch.result.status == AICandidateProposalStatus.NOT_CONNECTED
    assert planning_state.candidate_grounding_batch is not None


# ---------------------------------------------------------------------------
# 12b. Shadow mode with provider=groq and no GROQ_API_KEY does not crash
#      generation (Step 162A).
# ---------------------------------------------------------------------------


def test_shadow_mode_with_groq_provider_missing_api_key_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(groq_adapter_module, "get_settings", lambda: Settings(GROQ_API_KEY=None))
    monkeypatch.setattr(
        orchestrator_module,
        "get_settings",
        lambda: Settings(AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=True),
    )
    provider = GroqAICandidateProposalProvider(api_key=None)
    service = AICandidateDiscoveryService(proposal_provider=provider)
    orchestrator = PlanningOrchestrator(ai_candidate_discovery_service=service)

    planning_state = orchestrator.create_trip(_trip_request())
    planning_state = orchestrator.run_traveler_profile_stage(planning_state)
    planning_state = orchestrator.run_destination_context_stage(planning_state)  # must not raise

    assert planning_state.ai_candidate_proposal_batch is not None
    assert planning_state.ai_candidate_proposal_batch.result.status == AICandidateProposalStatus.NOT_CONNECTED
    assert planning_state.ai_candidate_proposal_batch.result.proposals == []
    assert planning_state.candidate_grounding_batch is not None
    from app.models.candidate_grounding import CandidateGroundingStatus

    assert planning_state.candidate_grounding_batch.result.status == CandidateGroundingStatus.SKIPPED


# ---------------------------------------------------------------------------
# 13. Monkeypatched fake AICandidateDiscoveryService returning a valid
#     completed proposal/grounding result -- shadow mode stores both.
# ---------------------------------------------------------------------------


def test_shadow_mode_with_fake_completed_service_stores_both_batches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_fake_completed_service(client, monkeypatch)

    response = client.get(f"/trips/{trip_id}")
    planning_state = response.json()["data"]["planning_state"]

    proposal_batch = planning_state["ai_candidate_proposal_batch"]
    grounding_batch = planning_state["candidate_grounding_batch"]
    assert proposal_batch is not None
    assert proposal_batch["result"]["status"] == "completed"
    assert grounding_batch is not None
    assert grounding_batch["result"]["status"] == "completed"
    assert len(grounding_batch["result"]["grounded_candidates"]) == 1


# ---------------------------------------------------------------------------
# 14. Even with a fake completed grounding result, ExperiencePlannerService
#     does not schedule AI-grounded candidates.
# ---------------------------------------------------------------------------


def test_experience_planner_does_not_schedule_ai_grounded_candidates(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_fake_completed_service(client, monkeypatch)

    response = client.get(f"/trips/{trip_id}/experience-plan")
    experience_plan = response.json()["data"]["experience_plan"]
    all_experiences = [
        experience
        for day_plan in experience_plan["daily_plans"]
        for experience in day_plan["experiences"]
    ]
    scheduled_names = {experience["name"] for experience in all_experiences}

    # Only real deterministic test-fixture candidates are ever scheduled --
    # never a name that only exists because of the fake AI proposal/grounding.
    assert scheduled_names <= {"Test Fixture Attraction One", "Test Fixture Attraction Two"}

    import app.services.experience_planner_service as experience_planner_module

    source = inspect.getsource(experience_planner_module)
    assert "GroundedCandidate" not in source
    assert "AICandidateProposal" not in source
    assert "ai_candidate_proposal_batch" not in source
    assert "candidate_grounding_batch" not in source


# ---------------------------------------------------------------------------
# 15. CandidateQualityService does not consume GroundedCandidate from the
#     shadow batch yet.
# ---------------------------------------------------------------------------


def test_candidate_quality_service_does_not_consume_grounded_candidates(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_fake_completed_service(client, monkeypatch)

    response = client.get(f"/trips/{trip_id}/candidate-quality")
    assert response.status_code == 200

    import app.services.candidate_quality_service as candidate_quality_module

    source = inspect.getsource(candidate_quality_module)
    assert "GroundedCandidate" not in source
    assert "AICandidateProposal" not in source
    assert "ai_candidate_proposal_batch" not in source
    assert "candidate_grounding_batch" not in source


# ---------------------------------------------------------------------------
# 16. No frontend files reference the new shadow-mode feature.
# ---------------------------------------------------------------------------


def test_no_frontend_files_reference_shadow_mode_feature() -> None:
    """Strict rule: 'Do not modify frontend.' `git diff --stat` (run
    separately per the task's verification steps) is the authoritative
    check that no frontend file changed at all; this test additionally
    confirms the frontend source tree has no coupling to the new
    config key/helper name.
    """
    repo_root = Path(__file__).resolve().parents[4]
    needles = (
        "ai_candidate_discovery_shadow_mode_enabled",
        "AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED",
        "_run_ai_candidate_discovery_shadow_stage",
    )
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
# 17. No OpenAI/LangGraph/LangSmith/Gemini/Claude Code CLI import was added.
# ---------------------------------------------------------------------------


def test_planning_orchestrator_has_no_disallowed_llm_framework_imports() -> None:
    source = inspect.getsource(orchestrator_module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    disallowed_substrings = (
        "langgraph",
        "langsmith",
        "openai",
        "gemini",
        "google.generativeai",
        "claude_code",
    )
    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"


# ---------------------------------------------------------------------------
# 18. Existing safety/provider tests still passing is verified by running
#     the full pytest suite (see task verification steps) -- not something
#     a single test in this file can assert about other test files.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 19. No forbidden factual fields appear in stored batch dumps.
# ---------------------------------------------------------------------------

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


def test_shadow_mode_stored_batches_have_no_forbidden_factual_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_fake_completed_service(client, monkeypatch)

    response = client.get(f"/trips/{trip_id}")
    planning_state = response.json()["data"]["planning_state"]
    proposal_batch = planning_state["ai_candidate_proposal_batch"]
    grounding_batch = planning_state["candidate_grounding_batch"]

    def _collect_keys(value: object, keys: set[str]) -> None:
        if isinstance(value, dict):
            keys.update(value.keys())
            for nested in value.values():
                _collect_keys(nested, keys)
        elif isinstance(value, list):
            for item in value:
                _collect_keys(item, keys)

    all_keys: set[str] = set()
    _collect_keys(proposal_batch, all_keys)
    _collect_keys(grounding_batch, all_keys)
    overlap = all_keys & _FORBIDDEN_MODEL_FIELD_NAMES
    assert overlap == set(), f"Stored batch dump has forbidden key(s): {overlap}"


# ---------------------------------------------------------------------------
# Extra: fail-safe behavior when dry_run raises.
# ---------------------------------------------------------------------------


def test_shadow_mode_fails_safe_when_dry_run_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        orchestrator_module,
        "get_settings",
        lambda: Settings(AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=True),
    )
    orchestrator = PlanningOrchestrator(
        ai_candidate_discovery_service=_RaisingAICandidateDiscoveryService()  # type: ignore[arg-type]
    )

    planning_state = orchestrator.create_trip(_trip_request())
    planning_state = orchestrator.run_traveler_profile_stage(planning_state)
    planning_state = orchestrator.run_destination_context_stage(planning_state)  # must not raise

    assert planning_state.ai_candidate_proposal_batch is None
    assert planning_state.candidate_grounding_batch is None
