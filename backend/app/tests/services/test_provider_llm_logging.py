from __future__ import annotations

import json
import logging
from typing import Any

import pytest

import app.services.itinerary_narrative_service as narrative_service_module
import app.services.planning_orchestrator as orchestrator_module
from app.core.config import Settings
from app.core.logging_config import APP_LOGGER_NAME, JsonFormatter
from app.models.itinerary_narrative import ItineraryNarrativeReport, ItineraryNarrativeStatus
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.services.itinerary_narrative_service import ItineraryNarrativeService
from app.services.planning_orchestrator import PlanningOrchestrator

# Tests for the Step 187F LLM-backed subsystem service-boundary logs
# (docs/14_backend_architecture.md section 125): `ItineraryNarrativeService`
# and `PlanningOrchestrator._run_ai_candidate_discovery_shadow_stage` both
# call their provider directly, never through `ProviderGateway`, so this
# step logs at the service boundary instead. Every test attaches a small
# capture handler directly to the shared "app" logger (`propagate=False`,
# matching Steps 187C-187F's established pattern).


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture()
def capture() -> _CaptureHandler:
    app_logger = logging.getLogger(APP_LOGGER_NAME)
    handler = _CaptureHandler()
    app_logger.addHandler(handler)
    try:
        yield handler
    finally:
        app_logger.removeHandler(handler)


def _records_with(capture: _CaptureHandler, **attrs: object) -> list[logging.LogRecord]:
    return [
        record
        for record in capture.records
        if all(getattr(record, key, None) == value for key, value in attrs.items())
    ]


_FORBIDDEN_SNIPPETS = (
    "A trip to",
    "Narrative text",
    "prompt",
    "destination_context",
    "Testville",
)


def _assert_no_forbidden_content(record: logging.LogRecord) -> None:
    rendered = JsonFormatter().format(record)
    for snippet in _FORBIDDEN_SNIPPETS:
        assert snippet not in rendered


# ---------------------------------------------------------------------------
# Itinerary narrator
# ---------------------------------------------------------------------------


def _planning_state() -> PlanningState:
    trip_request = TripRequest(
        primary_destination="Lisbon, Portugal",
        start_date="2026-10-10",
        end_date="2026-10-11",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )
    return PlanningState(trip_request=trip_request)


class _FakeSuccessProvider:
    provider_name = "fake_success_narrator_provider"

    def narrate(self, request: Any) -> ItineraryNarrativeReport:
        return ItineraryNarrativeReport(
            status=ItineraryNarrativeStatus.SUCCESS,
            provider=self.provider_name,
            model="fake-model",
            summary=f"A trip to {request.destination}.",
            daily_narratives=[],
        )


class _FakeRaisingProvider:
    provider_name = "fake_raising_narrator_provider"

    def narrate(self, request: Any) -> ItineraryNarrativeReport:
        raise RuntimeError("simulated provider crash with a secret-looking token sk-should-not-leak")


def test_narrator_disabled_by_default_produces_no_log(capture: _CaptureHandler) -> None:
    service = ItineraryNarrativeService(provider=_FakeSuccessProvider())

    service.generate(_planning_state())

    assert _records_with(capture, stage="itinerary_narrator") == []


def test_narrator_success_logs_safe_info_fields(
    capture: _CaptureHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        narrative_service_module,
        "get_settings",
        lambda: Settings(_env_file=None, itinerary_narrator_enabled=True),
    )
    service = ItineraryNarrativeService(provider=_FakeSuccessProvider())

    result = service.generate(_planning_state())

    assert result.itinerary_narrative_report.status == ItineraryNarrativeStatus.SUCCESS
    matching = _records_with(capture, stage="itinerary_narrator", status="success")
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "INFO"
    assert record.provider == "fake_success_narrator_provider"
    assert isinstance(record.duration_ms, float)
    assert record.duration_ms >= 0
    assert not hasattr(record, "error_code")
    _assert_no_forbidden_content(record)


def test_narrator_failure_logs_safe_warning_fields_no_secret_leak(
    capture: _CaptureHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        narrative_service_module,
        "get_settings",
        lambda: Settings(_env_file=None, itinerary_narrator_enabled=True),
    )
    service = ItineraryNarrativeService(provider=_FakeRaisingProvider())

    result = service.generate(_planning_state())  # must not raise

    assert result.itinerary_narrative_report.status == ItineraryNarrativeStatus.FAILED
    matching = _records_with(capture, stage="itinerary_narrator", status="failed")
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "WARNING"
    assert record.error_code == "PROVIDER_FAILED"
    assert record.provider == "fake_raising_narrator_provider"

    # The *structured* fields (the only ones ALLOWED_EXTRA_FIELDS lets
    # JsonFormatter render) must never contain the exception text -- the
    # real traceback legitimately still reaches `exc_info`, server-side
    # only, matching Step 187D's own established precedent.
    structured_fields = {
        field: getattr(record, field)
        for field in ("provider", "stage", "status", "error_code", "duration_ms")
        if hasattr(record, field)
    }
    assert "sk-should-not-leak" not in json.dumps(structured_fields)


# ---------------------------------------------------------------------------
# AI candidate proposal (shadow mode)
# ---------------------------------------------------------------------------


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


def _run_through_destination_context(orchestrator: PlanningOrchestrator) -> PlanningState:
    planning_state = orchestrator.create_trip(_trip_request())
    planning_state = orchestrator.run_traveler_profile_stage(planning_state)
    planning_state = orchestrator.run_destination_context_stage(planning_state)
    return planning_state


def test_ai_candidate_proposal_disabled_by_default_produces_no_log(
    capture: _CaptureHandler,
) -> None:
    orchestrator = PlanningOrchestrator()
    _run_through_destination_context(orchestrator)

    assert _records_with(capture, stage="ai_candidate_proposal") == []


def test_ai_candidate_proposal_shadow_mode_logs_safe_fields(
    capture: _CaptureHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default shadow-mode provider is NotConnectedAICandidateProposalProvider
    -- a real, honest `not_connected` outcome, logged as a warning."""
    monkeypatch.setattr(
        orchestrator_module,
        "get_settings",
        lambda: Settings(_env_file=None, AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=True),
    )
    orchestrator = PlanningOrchestrator()

    _run_through_destination_context(orchestrator)

    matching = _records_with(capture, stage="ai_candidate_proposal")
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "WARNING"
    assert record.status == "not_connected"
    assert record.error_code == "PROVIDER_NOT_CONNECTED"
    assert isinstance(record.duration_ms, float)
    assert record.duration_ms >= 0
    _assert_no_forbidden_content(record)


class _RaisingAICandidateDiscoveryService:
    def dry_run(self, planning_state: PlanningState, **kwargs: Any) -> Any:
        raise RuntimeError("simulated dry_run failure with secret sk-should-not-leak")


def test_ai_candidate_proposal_exception_logs_safe_warning_no_secret_leak(
    capture: _CaptureHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        orchestrator_module,
        "get_settings",
        lambda: Settings(_env_file=None, AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=True),
    )
    orchestrator = PlanningOrchestrator(
        ai_candidate_discovery_service=_RaisingAICandidateDiscoveryService()  # type: ignore[arg-type]
    )

    planning_state = _run_through_destination_context(orchestrator)  # must not raise

    assert planning_state.ai_candidate_proposal_batch is None
    matching = _records_with(capture, stage="ai_candidate_proposal", status="failed")
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "WARNING"
    assert record.error_code == "PROVIDER_FAILED"
    assert record.provider == "ai_candidate_proposal_provider"

    structured_fields = {
        field: getattr(record, field)
        for field in ("provider", "stage", "status", "error_code", "duration_ms")
        if hasattr(record, field)
    }
    assert "sk-should-not-leak" not in json.dumps(structured_fields)
