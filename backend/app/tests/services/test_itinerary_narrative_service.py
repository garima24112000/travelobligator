from __future__ import annotations

import pytest

from app.core.config import Settings
from app.models.itinerary_narrative import (
    ItineraryNarrativeDayOutput,
    ItineraryNarrativeReport,
    ItineraryNarrativeStatus,
)
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
import app.services.itinerary_narrative_service as service_module
from app.services.itinerary_narrative_service import ItineraryNarrativeService

# Step 182F: proves ItineraryNarrativeService.generate never raises, is
# disabled by default, and never mutates any PlanningState field other
# than itinerary_narrative_report. Every provider used here is a fake
# injected via the constructor -- never a real network call.


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
    def narrate(self, request):
        return ItineraryNarrativeReport(
            status=ItineraryNarrativeStatus.SUCCESS,
            provider="fake_test_provider",
            model="fake-model",
            summary=f"A trip to {request.destination}.",
            daily_narratives=[
                ItineraryNarrativeDayOutput(
                    day_number=day.day_number,
                    date=day.date,
                    title=f"Day {day.day_number}",
                    narrative="Narrative text.",
                )
                for day in request.days
            ],
        )


class _FakeRaisingProvider:
    def narrate(self, request):
        raise RuntimeError("simulated provider crash")


def test_generate_returns_not_connected_when_disabled_by_default() -> None:
    planning_state = _planning_state()
    service = ItineraryNarrativeService(provider=_FakeSuccessProvider())

    result = service.generate(planning_state)

    assert result.itinerary_narrative_report is not None
    assert result.itinerary_narrative_report.status == ItineraryNarrativeStatus.NOT_CONNECTED
    assert "disabled" in (result.itinerary_narrative_report.message or "").lower()
    # The fake provider must never even be reached when disabled.


def test_generate_never_calls_the_provider_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"count": 0}

    class _CountingProvider:
        def narrate(self, request):
            called["count"] += 1
            raise AssertionError("must not be called when disabled")

    service = ItineraryNarrativeService(provider=_CountingProvider())
    service.generate(_planning_state())

    assert called["count"] == 0


def test_generate_returns_success_when_enabled_with_a_working_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, itinerary_narrator_enabled=True)
    )
    service = ItineraryNarrativeService(provider=_FakeSuccessProvider())

    result = service.generate(_planning_state())

    assert result.itinerary_narrative_report.status == ItineraryNarrativeStatus.SUCCESS
    assert result.itinerary_narrative_report.provider == "fake_test_provider"


def test_generate_returns_failed_when_provider_raises_and_never_raises_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, itinerary_narrator_enabled=True)
    )
    service = ItineraryNarrativeService(provider=_FakeRaisingProvider())

    result = service.generate(_planning_state())  # must not raise

    assert result.itinerary_narrative_report.status == ItineraryNarrativeStatus.FAILED


def test_generate_never_mutates_any_other_planning_state_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, itinerary_narrator_enabled=True)
    )
    planning_state = _planning_state()
    before = planning_state.model_dump(exclude={"itinerary_narrative_report", "metadata"})

    service = ItineraryNarrativeService(provider=_FakeSuccessProvider())
    result = service.generate(planning_state)

    after = result.model_dump(exclude={"itinerary_narrative_report", "metadata"})
    assert before == after


def test_generate_disabled_never_mutates_any_other_planning_state_field() -> None:
    planning_state = _planning_state()
    before = planning_state.model_dump(exclude={"itinerary_narrative_report", "metadata"})

    service = ItineraryNarrativeService(provider=_FakeSuccessProvider())
    result = service.generate(planning_state)

    after = result.model_dump(exclude={"itinerary_narrative_report", "metadata"})
    assert before == after
