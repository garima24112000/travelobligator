from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.routes.trips as trips_module
import app.services.itinerary_narrative_service as narrative_service_module
from app.core.config import Settings
from app.models.itinerary_narrative import (
    ItineraryNarrativeDayOutput,
    ItineraryNarrativeReport,
    ItineraryNarrativeStatus,
)
from app.services.itinerary_narrative_service import ItineraryNarrativeService
from app.services.planning_orchestrator import planning_orchestrator

# Step 182F: end-to-end API tests proving generation/regeneration always
# succeed regardless of the narrator's enabled/disabled/failed state, and
# that a real success report actually reaches PlanningState /
# GET /trips/{trip_id}. Every "enabled" test injects a fake provider via
# the constructor and monkeypatches `get_settings` -- never a real
# network call to Groq/Anthropic.


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


def _enable_narrator_with_provider(monkeypatch: pytest.MonkeyPatch, provider) -> None:
    monkeypatch.setattr(
        narrative_service_module,
        "get_settings",
        lambda: Settings(_env_file=None, itinerary_narrator_enabled=True),
    )
    fake_service = ItineraryNarrativeService(provider=provider)
    monkeypatch.setattr(planning_orchestrator, "itinerary_narrative_service", fake_service)
    monkeypatch.setattr(trips_module, "itinerary_narrative_service", fake_service)


def test_generation_succeeds_when_narrator_disabled_by_default(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}")
    assert response.status_code == 200

    report = response.json()["data"]["planning_state"]["itinerary_narrative_report"]
    assert report["status"] == "not_connected"
    assert report["daily_narratives"] == []


def test_generation_succeeds_and_attaches_a_real_narrative_when_enabled(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_narrator_with_provider(monkeypatch, _FakeSuccessProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}")
    report = response.json()["data"]["planning_state"]["itinerary_narrative_report"]
    assert report["status"] == "success"
    assert report["provider"] == "fake_test_provider"
    assert len(report["daily_narratives"]) > 0


def test_generation_succeeds_even_when_narrator_provider_fails(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_narrator_with_provider(monkeypatch, _FakeRaisingProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")

    assert generate_response.status_code == 200
    response = client.get(f"/trips/{created_trip_id}")
    report = response.json()["data"]["planning_state"]["itinerary_narrative_report"]
    assert report["status"] == "failed"


def test_generation_narrator_failure_does_not_affect_validation_report(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_narrator_with_provider(monkeypatch, _FakeRaisingProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    validation_response = client.get(f"/trips/{created_trip_id}/validation-report")
    assert validation_response.status_code == 200
    assert validation_response.json()["data"]["validation_report"]["readiness_status"] is not None


def test_regeneration_succeeds_when_narrator_disabled(
    client: TestClient, generated_trip_id: str
) -> None:
    feedback_response = client.post(
        f"/trips/{generated_trip_id}/feedback", json={"feedback_text": "Make this less packed"}
    )
    assert feedback_response.status_code == 200

    regenerate_response = client.post(
        f"/trips/{generated_trip_id}/regenerate", json={"confirm": True}
    )
    assert regenerate_response.status_code == 200
    assert "itinerary_narrative" not in regenerate_response.json()["data"]["changed_sections"]


def test_regeneration_succeeds_when_narrator_fails(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_narrator_with_provider(monkeypatch, _FakeRaisingProvider())

    feedback_response = client.post(
        f"/trips/{generated_trip_id}/feedback", json={"feedback_text": "Make this less packed"}
    )
    assert feedback_response.status_code == 200

    regenerate_response = client.post(
        f"/trips/{generated_trip_id}/regenerate", json={"confirm": True}
    )
    assert regenerate_response.status_code == 200
    assert "itinerary_narrative" not in regenerate_response.json()["data"]["changed_sections"]

    report = client.get(f"/trips/{generated_trip_id}")
    narrative_report = report.json()["data"]["planning_state"]["itinerary_narrative_report"]
    assert narrative_report["status"] == "failed"


def test_regeneration_reports_itinerary_narrative_in_changed_sections_when_it_succeeds(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_narrator_with_provider(monkeypatch, _FakeSuccessProvider())

    feedback_response = client.post(
        f"/trips/{generated_trip_id}/feedback", json={"feedback_text": "Make this less packed"}
    )
    assert feedback_response.status_code == 200

    regenerate_response = client.post(
        f"/trips/{generated_trip_id}/regenerate", json={"confirm": True}
    )
    assert regenerate_response.status_code == 200
    changed_sections = regenerate_response.json()["data"]["changed_sections"]
    assert "itinerary_narrative" in changed_sections
    # The real affected stages are still reported too -- the narrator
    # entry is additive, never a replacement.
    assert any(section != "itinerary_narrative" for section in changed_sections)
