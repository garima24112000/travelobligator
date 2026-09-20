from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.models.ai_feedback_interpretation import (
    AIFeedbackInterpretationRequest,
    AIFeedbackInterpretationResult,
    AIFeedbackInterpretationStatus,
    RemoveExperienceAction,
)
from app.models.planning_state import (
    DailyPlan,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    TravelGroupType,
    TripRequest,
)
from datetime import date

from app.providers.ai_feedback_interpreter.base import AIFeedbackInterpreterProvider
from app.services import ai_feedback_interpreter_service as service_module
from app.services.ai_feedback_interpreter_service import AIFeedbackInterpreterService

# Tests for the Section 196 service layer (docs/14_backend_architecture.md,
# following section 146). No real provider/network call anywhere here.


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Lisbon, Portugal",
        "start_date": "2026-09-10",
        "end_date": "2026-09-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _planning_state() -> PlanningState:
    planning_state = PlanningState(trip_request=_trip_request())
    planning_state.experience_plan = ExperiencePlan(
        daily_plans=[
            DailyPlan(
                day_number=1,
                date=date(2026, 9, 10),
                experiences=[ExperienceItem(experience_id="exp_a", name="Torre de Belem", category="attraction")],
            )
        ]
    )
    return planning_state


def _completed_result() -> AIFeedbackInterpretationResult:
    return AIFeedbackInterpretationResult(
        status=AIFeedbackInterpretationStatus.COMPLETED,
        actions=[RemoveExperienceAction(experience_id="exp_a")],
        summary="Removing Torre de Belem.",
        provider_name="fake_provider",
        confidence=0.8,
    )


class _FakeProvider(AIFeedbackInterpreterProvider):
    provider_name = "fake_ai_feedback_interpreter_provider"

    def __init__(self, result: AIFeedbackInterpretationResult | None = None, *, raises: bool = False) -> None:
        self._result = result
        self._raises = raises
        self.call_count = 0
        self.last_request: AIFeedbackInterpretationRequest | None = None

    def interpret(self, request: AIFeedbackInterpretationRequest) -> AIFeedbackInterpretationResult:
        self.call_count += 1
        self.last_request = request
        if self._raises:
            raise RuntimeError("simulated provider crash")
        return self._result if self._result is not None else _completed_result()


# ---------------------------------------------------------------------------
# Task 38: disabled by default -- never calls the provider.
# ---------------------------------------------------------------------------


def test_disabled_by_default_never_calls_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "get_settings", lambda: Settings(_env_file=None))
    fake_provider = _FakeProvider()
    service = AIFeedbackInterpreterService(provider=fake_provider)
    planning_state = _planning_state()

    result = service.interpret(planning_state, "Remove Belem Tower.")

    assert result.status == AIFeedbackInterpretationStatus.NOT_CONNECTED
    assert fake_provider.call_count == 0


def test_enabled_calls_provider_and_returns_its_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_FEEDBACK_INTERPRETER_ENABLED=True)
    )
    fake_provider = _FakeProvider()
    service = AIFeedbackInterpreterService(provider=fake_provider)
    planning_state = _planning_state()

    result = service.interpret(planning_state, "Remove Belem Tower.")

    assert result.status == AIFeedbackInterpretationStatus.COMPLETED
    assert fake_provider.call_count == 1
    assert fake_provider.last_request is not None
    assert fake_provider.last_request.feedback_text == "Remove Belem Tower."


# ---------------------------------------------------------------------------
# Task 37: provider failure -- honest status, no mutation, no fabrication.
# ---------------------------------------------------------------------------


def test_provider_exception_propagates_and_never_fabricates(monkeypatch: pytest.MonkeyPatch) -> None:
    """`interpret` itself does not swallow a provider exception (mirrors
    `AIItineraryReasoningService.reason`/`AIItineraryRepairService.repair`
    -- no fail-safe wrapper exists for this pure, read-only service since
    nothing calls it automatically yet; a future caller in Section 197
    is responsible for its own try/except if it wants one)."""
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_FEEDBACK_INTERPRETER_ENABLED=True)
    )
    fake_provider = _FakeProvider(raises=True)
    service = AIFeedbackInterpreterService(provider=fake_provider)
    planning_state = _planning_state()

    with pytest.raises(RuntimeError, match="simulated provider crash"):
        service.interpret(planning_state, "Remove Belem Tower.")


def test_rejected_provider_result_is_returned_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    rejected = AIFeedbackInterpretationResult(
        status=AIFeedbackInterpretationStatus.REJECTED,
        blocked_reasons=["Groq API call failed."],
        confidence=0.0,
    )
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_FEEDBACK_INTERPRETER_ENABLED=True)
    )
    fake_provider = _FakeProvider(result=rejected)
    service = AIFeedbackInterpreterService(provider=fake_provider)
    planning_state = _planning_state()

    result = service.interpret(planning_state, "Remove Belem Tower.")

    assert result.status == AIFeedbackInterpretationStatus.REJECTED
    assert result.actions == []


# ---------------------------------------------------------------------------
# Task 39: read-only proof.
# ---------------------------------------------------------------------------


def test_interpret_never_mutates_planning_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_FEEDBACK_INTERPRETER_ENABLED=True)
    )
    fake_provider = _FakeProvider()
    service = AIFeedbackInterpreterService(provider=fake_provider)
    planning_state = _planning_state()
    before = planning_state.model_copy(deep=True)

    result = service.interpret(planning_state, "Remove Belem Tower.")

    assert planning_state.model_dump() == before.model_dump()
    assert planning_state.itinerary_narrative_report is None
    assert planning_state.ai_itinerary_repair_result is None
    assert planning_state.version_history == []
    assert result.status == AIFeedbackInterpretationStatus.COMPLETED


def test_injected_provider_bypasses_the_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_factory(provider_name: str | None = None) -> None:
        raise AssertionError("get_ai_feedback_interpreter_provider must not be called when injected")

    monkeypatch.setattr(service_module, "get_ai_feedback_interpreter_provider", _fail_factory)
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_FEEDBACK_INTERPRETER_ENABLED=True)
    )
    fake_provider = _FakeProvider()
    service = AIFeedbackInterpreterService(provider=fake_provider)
    planning_state = _planning_state()

    result = service.interpret(planning_state, "Remove Belem Tower.")

    assert result.status == AIFeedbackInterpretationStatus.COMPLETED
    assert fake_provider.call_count == 1
