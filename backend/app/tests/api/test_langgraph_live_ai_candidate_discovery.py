from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.services.planning_orchestrator as orchestrator_module
from app.core.config import Settings, get_settings
from app.core.logging_config import APP_LOGGER_NAME
from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateProposalGuardrailReport,
    AICandidateProposalRequest,
    AICandidateProposalResult,
    AICandidateProposalStatus,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.providers.ai_candidate_proposal import AICandidateProposalProvider
from app.services.ai_candidate_discovery_service import AICandidateDiscoveryService
from app.services.langgraph_planning_service import LangGraphPlanningService

# Tests for Step 191A -- live AI candidate discovery as a real LangGraph
# `/generate` dependency, gated by `Settings.ai_candidate_discovery_enabled`
# (default `False`, a separate flag from the legacy-only
# `Settings.ai_candidate_discovery_shadow_mode_enabled`). Every test here
# injects a deterministic fake `AICandidateProposalProvider` -- never a
# real Groq/Anthropic/OpenAI call. See docs/14_backend_architecture.md
# section 135.
#
# Reuses the exact same deterministic test-fixture places provider
# (autouse in conftest.py) every other API test already relies on: real
# destination-context candidates named "Test Fixture Attraction One"/"Two"
# (place_id test/attraction/1, test/attraction/2), "Test Fixture
# Restaurant One" (test/restaurant/1), and "Test Fixture Accommodation
# One" (test/accommodation/1).


def _create_trip_payload() -> dict[str, Any]:
    return {
        "destination_scope": "single_city",
        "primary_destination": "Testville, Testland",
        "origin_city": "Home City",
        "start_date": "2026-08-10",
        "end_date": "2026-08-14",
        "travelers_count": 2,
        "travel_group_type": "couple",
    }


def _create_trip(client: TestClient) -> str:
    response = client.post("/trips", json=_create_trip_payload())
    assert response.status_code == 201
    return response.json()["data"]["trip_id"]


@pytest.fixture(autouse=True)
def _reset_settings_cache_after_test() -> Any:
    yield
    get_settings.cache_clear()


def _enable_live_discovery(
    monkeypatch: pytest.MonkeyPatch, proposal_provider: AICandidateProposalProvider
) -> None:
    """Enables the Step 191A live flag and swaps the singleton
    orchestrator's `langgraph_planning_service` for one built with a fake
    proposal provider -- mirrors the established pattern
    test_ai_candidate_discovery_shadow_mode.py already uses for the legacy
    engine (monkeypatching `orchestrator_module.planning_orchestrator`'s
    own attribute), adapted for the fact that the LangGraph graph's node
    closures are built once at `LangGraphPlanningService.__init__` time --
    so the swap has to replace the whole service, not just the discovery
    service attribute the legacy path reads fresh on every call."""
    monkeypatch.setenv("AI_CANDIDATE_DISCOVERY_ENABLED", "true")
    get_settings.cache_clear()
    fake_discovery_service = AICandidateDiscoveryService(proposal_provider=proposal_provider)
    monkeypatch.setattr(
        orchestrator_module.planning_orchestrator,
        "langgraph_planning_service",
        LangGraphPlanningService(ai_candidate_discovery_service=fake_discovery_service),
    )


def _attraction_typed_proposal(candidate_name: str, **overrides: object) -> AICandidateProposal:
    fields: dict[str, object] = {
        "proposal_id": "proposal_001",
        "candidate_name": candidate_name,
        "candidate_type": AICandidateType.ATTRACTION,
        "why_consider": "Locally known landmark that may be under-tagged in provider data.",
        "verification_requirements": [
            AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION
        ],
        "confidence": 0.5,
    }
    fields.update(overrides)
    return AICandidateProposal(**fields)


class _CompletedProposalProvider(AICandidateProposalProvider):
    """Deterministic fake -- always returns the given proposals as a
    COMPLETED result. Never a real network/LLM call."""

    provider_name = "fake_live_discovery_test_provider"

    def __init__(self, proposals: list[AICandidateProposal]) -> None:
        self._proposals = proposals
        self.call_count = 0

    def propose(self, request: AICandidateProposalRequest) -> AICandidateProposalResult:
        self.call_count += 1
        return AICandidateProposalResult(
            task=request.task,
            status=AICandidateProposalStatus.COMPLETED,
            proposals=self._proposals,
            guardrail_report=AICandidateProposalGuardrailReport(passed=True),
            provider_name=self.provider_name,
            confidence=0.6,
        )


class _InvalidSchemaProposalProvider(AICandidateProposalProvider):
    """Deterministic fake simulating a real adapter's own honest handling
    of malformed/invalid LLM output: attempting to build the result raises
    `pydantic.ValidationError` (an invalid `status` value that doesn't
    match `AICandidateProposalStatus`) before any proposal is ever
    returned -- the same "raises before a valid result exists" shape
    `test_ai_candidate_discovery_safety.py`'s
    `_BrokenSchemaAICandidateProposalProvider` already established for the
    legacy shadow-stage tests, reused here for the live LangGraph path.
    """

    provider_name = "fake_invalid_schema_provider"

    def __init__(self) -> None:
        self.call_count = 0

    def propose(self, request: AICandidateProposalRequest) -> AICandidateProposalResult:
        self.call_count += 1
        return AICandidateProposalResult(
            task=request.task,
            status="not_a_real_status",  # type: ignore[arg-type]
            proposals=[],
            guardrail_report=AICandidateProposalGuardrailReport(passed=True),
            provider_name=self.provider_name,
            confidence=0.0,
        )


class _RaisingProposalProvider(AICandidateProposalProvider):
    """Deterministic fake simulating a provider-level failure (timeout,
    missing API key surfaced as an exception, network error) -- raises
    instead of returning a result."""

    provider_name = "fake_raising_provider"

    def __init__(self) -> None:
        self.call_count = 0

    def propose(self, request: AICandidateProposalRequest) -> AICandidateProposalResult:
        self.call_count += 1
        raise RuntimeError("simulated provider failure with secret sk-should-not-leak")


# ---------------------------------------------------------------------------
# 1. Live discovery enabled: proposal + grounding batches are stored, and
#    a real generation still completes successfully.
# ---------------------------------------------------------------------------


def test_live_discovery_enabled_stores_proposal_and_grounding_batches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _CompletedProposalProvider(
        [_attraction_typed_proposal("Test Fixture Restaurant One")]
    )
    _enable_live_discovery(monkeypatch, provider)

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    assert provider.call_count == 1
    planning_state = response.json()["data"]["planning_state"]
    proposal_batch = planning_state["ai_candidate_proposal_batch"]
    grounding_batch = planning_state["candidate_grounding_batch"]
    assert proposal_batch is not None
    assert proposal_batch["result"]["status"] == "completed"
    assert grounding_batch is not None
    assert grounding_batch["result"]["status"] == "completed"
    assert planning_state["destination_context"] is not None
    assert planning_state["experience_plan"] is not None


# ---------------------------------------------------------------------------
# 2. A grounded, quality-eligible candidate is actually promoted.
# ---------------------------------------------------------------------------


def test_live_discovery_enabled_promotes_grounded_candidate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _CompletedProposalProvider(
        [_attraction_typed_proposal("Test Fixture Restaurant One")]
    )
    _enable_live_discovery(monkeypatch, provider)

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    promotion_report = response.json()["data"]["planning_state"]["ai_candidate_promotion_report"]
    assert promotion_report is not None
    assert promotion_report["status"] == "promoted"
    assert promotion_report["promoted_count"] == 1
    assert promotion_report["promoted_candidates"][0]["name"] == "Test Fixture Restaurant One"


# ---------------------------------------------------------------------------
# 3. The promoted candidate is available before experience planning runs,
#    and actually reaches the generated itinerary (a real ExperienceItem
#    with promoted_from_ai=True) -- proving Step 170D's existing merge
#    mechanism needs no changes for the live LangGraph stage.
# ---------------------------------------------------------------------------


def _scheduled_items(planning_state: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for day in planning_state["experience_plan"]["daily_plans"]:
        items.extend(day["experiences"])
    return items


def test_live_discovery_enabled_promoted_candidate_reaches_experience_plan(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _CompletedProposalProvider(
        [_attraction_typed_proposal("Test Fixture Restaurant One")]
    )
    _enable_live_discovery(monkeypatch, provider)

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]
    items = _scheduled_items(planning_state)
    promoted_items = [item for item in items if item.get("promoted_from_ai") is True]
    assert len(promoted_items) == 1
    assert promoted_items[0]["name"] == "Test Fixture Restaurant One"
    assert promoted_items[0]["original_ai_candidate_id"] == "proposal_001"
    # Real, provider-grounded coordinates carried over verbatim -- never
    # guessed for a promoted candidate.
    assert promoted_items[0]["provider_place_id"] == "test/restaurant/1"


# ---------------------------------------------------------------------------
# 4. Ungrounded proposal: recorded, rejected by grounding, never promoted,
#    never scheduled.
# ---------------------------------------------------------------------------


def test_live_discovery_enabled_ungrounded_proposal_never_promoted_or_scheduled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _CompletedProposalProvider(
        [_attraction_typed_proposal("Completely Imaginary Landmark Nobody Provided")]
    )
    _enable_live_discovery(monkeypatch, provider)

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]

    proposal_batch = planning_state["ai_candidate_proposal_batch"]
    assert proposal_batch is not None
    assert len(proposal_batch["result"]["proposals"]) == 1

    grounding_batch = planning_state["candidate_grounding_batch"]
    assert grounding_batch["result"]["status"] == "rejected"
    assert grounding_batch["result"]["grounded_candidates"] == []
    assert len(grounding_batch["result"]["rejected_proposals"]) == 1

    promotion_report = planning_state["ai_candidate_promotion_report"]
    assert promotion_report is not None
    assert promotion_report["promoted_count"] == 0

    items = _scheduled_items(planning_state)
    assert not any(item.get("promoted_from_ai") is True for item in items)
    assert not any(
        "Completely Imaginary Landmark Nobody Provided" == item.get("name") for item in items
    )


# ---------------------------------------------------------------------------
# 5. Provider failure (raises): generation continues honestly, no
#    fabricated candidate, real provider-backed itinerary still generates.
# ---------------------------------------------------------------------------


def test_live_discovery_enabled_provider_exception_does_not_break_generation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _RaisingProposalProvider()
    _enable_live_discovery(monkeypatch, provider)

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    assert provider.call_count == 1
    planning_state = response.json()["data"]["planning_state"]
    # apply_discovery_to_state fails safe: nothing is stored on exception.
    assert planning_state["ai_candidate_proposal_batch"] is None
    assert planning_state["candidate_grounding_batch"] is None
    assert planning_state["ai_candidate_promotion_report"] is None
    # The rest of generation is unaffected -- still a real, provider-backed
    # itinerary, never blocked or fabricated because of the AI failure.
    assert planning_state["destination_context"] is not None
    assert planning_state["experience_plan"] is not None
    assert planning_state["validation_report"] is not None


# ---------------------------------------------------------------------------
# 6. Invalid/malformed model output surfaced as an honest FAILED status
#    (not an exception) -- the real shape Groq/Anthropic adapters use.
# ---------------------------------------------------------------------------


def test_live_discovery_enabled_invalid_output_does_not_fabricate_or_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _InvalidSchemaProposalProvider()
    _enable_live_discovery(monkeypatch, provider)

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    assert provider.call_count == 1
    planning_state = response.json()["data"]["planning_state"]
    # apply_discovery_to_state's try/except catches the ValidationError
    # raised while building the invalid result -- nothing is stored, never
    # a fabricated proposal/candidate.
    assert planning_state["ai_candidate_proposal_batch"] is None
    assert planning_state["candidate_grounding_batch"] is None
    assert planning_state["ai_candidate_promotion_report"] is None
    assert planning_state["experience_plan"] is not None


# ---------------------------------------------------------------------------
# 7. The proposal provider is called exactly once per /generate call --
#    no duplicate/retry invocation.
# ---------------------------------------------------------------------------


def test_live_discovery_enabled_provider_called_exactly_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _CompletedProposalProvider(
        [_attraction_typed_proposal("Test Fixture Restaurant One")]
    )
    _enable_live_discovery(monkeypatch, provider)

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    assert provider.call_count == 1


# ---------------------------------------------------------------------------
# 8. Observability: a safe structured log line identifies the live stage
#    (stage=ai_candidate_discovery, provider=..., status=..., duration_ms=...)
#    -- never a prompt, raw LLM output, or secret value.
# ---------------------------------------------------------------------------


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture()
def capture() -> Any:
    app_logger = logging.getLogger(APP_LOGGER_NAME)
    handler = _CaptureHandler()
    app_logger.addHandler(handler)
    try:
        yield handler
    finally:
        app_logger.removeHandler(handler)


def test_live_discovery_enabled_logs_safe_stage_metadata(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, capture: _CaptureHandler
) -> None:
    provider = _CompletedProposalProvider(
        [_attraction_typed_proposal("Test Fixture Restaurant One")]
    )
    _enable_live_discovery(monkeypatch, provider)

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")
    assert response.status_code == 200

    matching = [r for r in capture.records if getattr(r, "stage", None) == "ai_candidate_discovery"]
    assert len(matching) == 1
    record = matching[0]
    assert record.provider == "fake_live_discovery_test_provider"
    assert record.status == "completed"
    assert isinstance(record.duration_ms, float)
    assert record.duration_ms >= 0

    # Never a prompt, raw LLM output, or secret value in the log message.
    message = record.getMessage()
    assert "sk-" not in message
    assert "Test Fixture Restaurant One" not in message


def test_live_discovery_enabled_provider_exception_log_has_no_secret_leak(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, capture: _CaptureHandler
) -> None:
    provider = _RaisingProposalProvider()
    _enable_live_discovery(monkeypatch, provider)

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")
    assert response.status_code == 200

    matching = [
        r
        for r in capture.records
        if getattr(r, "stage", None) == "ai_candidate_discovery" and getattr(r, "status", None) == "failed"
    ]
    assert len(matching) == 1
    record = matching[0]
    assert record.error_code == "PROVIDER_FAILED"
    message = record.getMessage()
    assert "sk-should-not-leak" not in message


# ---------------------------------------------------------------------------
# 9. Engine mode: the legacy engine is completely unaffected by
#    ai_candidate_discovery_enabled -- that flag only ever gates the
#    LangGraph `ai_candidate` node.
# ---------------------------------------------------------------------------


def test_live_discovery_flag_has_no_effect_on_legacy_engine(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _CompletedProposalProvider(
        [_attraction_typed_proposal("Test Fixture Restaurant One")]
    )
    monkeypatch.setenv("AI_CANDIDATE_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("PLANNING_ENGINE_MODE", "legacy")
    get_settings.cache_clear()
    fake_discovery_service = AICandidateDiscoveryService(proposal_provider=provider)
    monkeypatch.setattr(
        orchestrator_module.planning_orchestrator,
        "langgraph_planning_service",
        LangGraphPlanningService(ai_candidate_discovery_service=fake_discovery_service),
    )

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    assert provider.call_count == 0
    planning_state = response.json()["data"]["planning_state"]
    assert planning_state["ai_candidate_proposal_batch"] is None
