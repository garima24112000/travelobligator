from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningRequest,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    ItineraryReasoningDayPlan,
    ItineraryReasoningStrategy,
)
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.providers.ai_itinerary_reasoning.base import AIItineraryReasoningProvider
from app.services import ai_itinerary_reasoning_service as service_module
from app.services.ai_itinerary_reasoning_service import (
    AIItineraryReasoningService,
    apply_itinerary_reasoning_safely,
)

# Tests for the Section 193B service layer (docs/14_backend_architecture.md
# section 142). No real provider/network call anywhere here.


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


def _planning_state(**overrides: Any) -> PlanningState:
    return PlanningState(trip_request=_trip_request(**overrides))


def _completed_result(candidate_id: str = "openstreetmap_places:way/1") -> AIItineraryReasoningResult:
    return AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.COMPLETED,
        strategy=ItineraryReasoningStrategy(
            summary="A relaxed plan.", pace="balanced", reason="Matches traveler preferences."
        ),
        days=[
            ItineraryReasoningDayPlan(
                day_index=1, candidate_ids=[candidate_id], rationale="Groups nearby sites."
            )
        ],
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        provider_name="fake_provider",
        confidence=0.7,
    )


class _FakeProvider(AIItineraryReasoningProvider):
    provider_name = "fake_ai_itinerary_reasoning_provider"

    def __init__(self, result: AIItineraryReasoningResult | None = None, *, raises: bool = False) -> None:
        self._result = result
        self._raises = raises
        self.call_count = 0
        self.last_request: AIItineraryReasoningRequest | None = None

    def reason(self, request: AIItineraryReasoningRequest) -> AIItineraryReasoningResult:
        self.call_count += 1
        self.last_request = request
        if self._raises:
            raise RuntimeError("simulated provider crash")
        return self._result if self._result is not None else _completed_result()

    def repair(self, request: Any) -> Any:  # pragma: no cover - not exercised in this file
        raise NotImplementedError("This fake is only used for AIItineraryReasoningService.reason tests.")


# ---------------------------------------------------------------------------
# 1. Disabled by default -- never calls the provider.
# ---------------------------------------------------------------------------


def test_disabled_by_default_never_calls_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "get_settings", lambda: Settings(_env_file=None))
    fake_provider = _FakeProvider()
    service = AIItineraryReasoningService(provider=fake_provider)

    result = service.reason(_planning_state())

    assert result.status == AIItineraryReasoningStatus.NOT_CONNECTED
    assert fake_provider.call_count == 0


# ---------------------------------------------------------------------------
# 2. Enabled + fake provider -> full composition, request builder used.
# ---------------------------------------------------------------------------


def test_enabled_composes_request_builder_and_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )
    fake_provider = _FakeProvider()
    service = AIItineraryReasoningService(provider=fake_provider)

    result = service.reason(_planning_state())

    assert result.status == AIItineraryReasoningStatus.COMPLETED
    assert fake_provider.call_count == 1
    assert fake_provider.last_request is not None
    assert fake_provider.last_request.destination_name == "Lisbon, Portugal"


def test_reason_does_not_mutate_planning_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )
    service = AIItineraryReasoningService(provider=_FakeProvider())
    planning_state = _planning_state()

    service.reason(planning_state)

    assert planning_state.ai_itinerary_reasoning_result is None


# ---------------------------------------------------------------------------
# 3. apply() populates ai_itinerary_reasoning_result only, nothing else.
# ---------------------------------------------------------------------------


def test_apply_populates_only_ai_itinerary_reasoning_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )
    service = AIItineraryReasoningService(provider=_FakeProvider())
    planning_state = _planning_state()
    before = planning_state.model_copy(deep=True)

    result_state = service.apply(planning_state)

    assert result_state.ai_itinerary_reasoning_result is not None
    assert result_state.ai_itinerary_reasoning_result.status == AIItineraryReasoningStatus.COMPLETED
    assert result_state.experience_plan == before.experience_plan
    assert result_state.route_feasibility_report == before.route_feasibility_report
    assert result_state.validation_report == before.validation_report


def test_apply_returns_same_planning_state_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )
    service = AIItineraryReasoningService(provider=_FakeProvider())
    planning_state = _planning_state()

    result_state = service.apply(planning_state)

    assert result_state is planning_state


# ---------------------------------------------------------------------------
# 4. Provider failure -> state unchanged (via fail-safe wrapper), no
#    fabricated reasoning result.
# ---------------------------------------------------------------------------


def test_provider_exception_propagates_from_apply_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    """service.apply itself does not catch -- that is what
    apply_itinerary_reasoning_safely is for (mirrors
    apply_discovery_to_state's own split of responsibilities)."""
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )
    service = AIItineraryReasoningService(provider=_FakeProvider(raises=True))

    with pytest.raises(RuntimeError):
        service.apply(_planning_state())


def test_apply_safely_leaves_planning_state_unchanged_on_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )
    service = AIItineraryReasoningService(provider=_FakeProvider(raises=True))
    planning_state = _planning_state()
    before = planning_state.model_copy(deep=True)

    result_state = apply_itinerary_reasoning_safely(planning_state, service)  # must not raise

    assert result_state.ai_itinerary_reasoning_result is None
    assert result_state == before


def test_apply_safely_succeeds_for_a_working_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )
    service = AIItineraryReasoningService(provider=_FakeProvider())
    planning_state = _planning_state()

    result_state = apply_itinerary_reasoning_safely(planning_state, service)

    assert result_state.ai_itinerary_reasoning_result is not None
    assert result_state.ai_itinerary_reasoning_result.status == AIItineraryReasoningStatus.COMPLETED


def test_rejected_provider_result_is_stored_honestly_not_fabricated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )
    rejected_result = AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.REJECTED,
        days=[],
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=False, blocked_reasons=["bad output"]),
        confidence=0.0,
    )
    service = AIItineraryReasoningService(provider=_FakeProvider(result=rejected_result))
    planning_state = _planning_state()

    result_state = service.apply(planning_state)

    assert result_state.ai_itinerary_reasoning_result.status == AIItineraryReasoningStatus.REJECTED
    assert result_state.experience_plan is None


# ---------------------------------------------------------------------------
# 5. Default provider construction uses the factory.
# ---------------------------------------------------------------------------


def test_default_provider_construction_uses_the_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel_provider = _FakeProvider()

    def _fake_factory(provider_name: str | None = None) -> _FakeProvider:
        return sentinel_provider

    monkeypatch.setattr(service_module, "get_ai_itinerary_reasoning_provider", _fake_factory)
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )

    service = AIItineraryReasoningService()
    service.reason(_planning_state())

    assert sentinel_provider.call_count == 1


def test_injected_provider_bypasses_the_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_factory(provider_name: str | None = None) -> None:
        raise AssertionError("get_ai_itinerary_reasoning_provider must not be called when injected")

    monkeypatch.setattr(service_module, "get_ai_itinerary_reasoning_provider", _fail_factory)
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )

    service = AIItineraryReasoningService(provider=_FakeProvider())
    service.reason(_planning_state())  # must not raise
