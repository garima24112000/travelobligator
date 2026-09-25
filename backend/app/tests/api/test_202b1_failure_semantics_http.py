from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.models.ai_feedback_interpretation import (
    AIFeedbackInterpretationResult,
    AIFeedbackInterpretationStatus,
)
from app.models.common import DataStatus, ProviderStatus
from app.models.providers import ProviderResponse
from app.providers.gateway import provider_gateway
from app.repositories.planning_state_repository import planning_state_repository
from app.services.targeted_regeneration_application_service import (
    targeted_regeneration_application_service,
)
from conftest import DeterministicTestPlacesProvider, create_trip_payload

# Section 202B.1 (Tasks 15-20, 21) through the real HTTP routes.

_STALE = "not been implemented"


# -- destination unresolved ---------------------------------------------------------


class _UnresolvedDestinationPlaces(DeterministicTestPlacesProvider):
    """Stands in for the real adapter reporting that it could not resolve
    the destination (structural `failure_reason`)."""

    def _unresolved(self, field: str) -> ProviderResponse[Any]:
        return ProviderResponse[Any](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.UNAVAILABLE,
            data_status=DataStatus.UNAVAILABLE,
            data=None,
            unavailable_fields=[field],
            message="Could not confidently resolve a location.",
            failure_reason="destination_unresolved",
        )

    def search_attractions(self, destination: str, filters: Any = None) -> ProviderResponse[Any]:
        return self._unresolved("attractions")

    def search_restaurants(self, area: str, filters: Any = None) -> ProviderResponse[Any]:
        return self._unresolved("restaurants")

    def search_accommodation_pois(self, destination: str, filters: Any = None) -> ProviderResponse[Any]:
        return self._unresolved("accommodation_pois")


class _EmptyButResolvedPlaces(_UnresolvedDestinationPlaces):
    def _unresolved(self, field: str) -> ProviderResponse[Any]:
        response = super()._unresolved(field)
        response.failure_reason = None
        return response


def test_unresolved_destination_is_an_actionable_error_not_a_successful_empty_plan(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _UnresolvedDestinationPlaces())
    trip_id = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]

    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 422
    error = response.json()["errors"][0]
    assert error["code"] == "DESTINATION_UNRESOLVED"
    assert "city and country" in error["message"]
    # The blocked state is still persisted and honest.
    state = planning_state_repository.get_by_trip_id(trip_id)
    assert state is not None
    assert state.destination_context is not None
    assert state.destination_context.destination_resolution == "unresolved"
    categories = [issue.category for issue in state.validation_report.critical_issues]
    assert "destination_unresolved" in categories
    assert "provider_coverage" not in categories
    assert sum(len(d.experiences) for d in state.experience_plan.daily_plans) == 0


def test_resolved_destination_with_no_candidates_keeps_the_ordinary_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _EmptyButResolvedPlaces())
    trip_id = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]

    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    state = planning_state_repository.get_by_trip_id(trip_id)
    assert state is not None
    assert state.destination_context.destination_resolution is None
    assert "provider_coverage" in [i.category for i in state.validation_report.critical_issues]


def test_async_generation_reports_unresolved_destination_as_a_failed_job(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(provider_gateway, "places", _UnresolvedDestinationPlaces())
    trip_id = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]

    start = client.post(f"/trips/{trip_id}/generate")

    assert start.status_code == 202
    job = client.get(f"/trips/{trip_id}/jobs/{start.json()['data']['job_id']}").json()["data"]
    assert job["status"] == "failed"
    assert job["error_code"] == "DESTINATION_UNRESOLVED"


# -- AI provider failures in targeted regeneration ---------------------------------------


class _FakeInterpreter:
    def __init__(self, result: AIFeedbackInterpretationResult) -> None:
        self._result = result

    def interpret(self, planning_state: Any, feedback_text: str) -> AIFeedbackInterpretationResult:
        return self._result


def _targeted_client_with(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, result: AIFeedbackInterpretationResult
) -> str:
    monkeypatch.setattr(get_settings(), "targeted_regeneration_enabled", True, raising=False)
    monkeypatch.setattr(targeted_regeneration_application_service, "interpreter_service", _FakeInterpreter(result))
    trip_id = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    assert client.post(f"/trips/{trip_id}/generate").status_code == 200
    assert client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Remove the first attraction."}).status_code == 200
    return trip_id


def _rejected(kind: str) -> AIFeedbackInterpretationResult:
    return AIFeedbackInterpretationResult(
        status=AIFeedbackInterpretationStatus.REJECTED, blocked_reasons=["x"], failure_kind=kind, confidence=0.0
    )


def _assert_nothing_changed(trip_id: str, reason_code: str) -> None:
    state = planning_state_repository.get_by_trip_id(trip_id)
    assert state is not None
    assert state.metadata.current_version == "v1"
    assert len(state.version_history) == 1
    assert state.feedback_history[-1].applied_at is None
    assert state.regeneration_attempts[-1].reason_code == reason_code
    assert state.regeneration_attempts[-1].status == "blocked"
    assert _STALE not in state.regeneration_attempts[-1].message


def test_ai_rate_limit_surfaces_accurately_and_changes_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _targeted_client_with(client, monkeypatch, _rejected("rate_limited"))
    ids_before = [e.experience_id for d in planning_state_repository.get_by_trip_id(trip_id).experience_plan.daily_plans for e in d.experiences]

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    error = response.json()["errors"][0]
    assert error["code"] == "REGENERATION_PROVIDER_RATE_LIMITED"
    assert "rate-limited" in error["message"]
    assert _STALE not in error["message"]
    assert "recover" not in error["message"].lower()  # never promises a recovery time
    _assert_nothing_changed(trip_id, "REGENERATION_PROVIDER_RATE_LIMITED")
    ids_after = [e.experience_id for d in planning_state_repository.get_by_trip_id(trip_id).experience_plan.daily_plans for e in d.experiences]
    assert ids_after == ids_before


@pytest.mark.parametrize(
    ("kind", "phrase"),
    [
        ("authentication", "rejected the configured credentials"),
        ("timeout_or_network", "could not be reached in time"),
        ("provider_error", "returned an error"),
        ("malformed_output", "could not be used"),
    ],
)
def test_other_ai_failures_have_their_own_truthful_copy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, kind: str, phrase: str
) -> None:
    trip_id = _targeted_client_with(client, monkeypatch, _rejected(kind))

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    error = response.json()["errors"][0]
    assert error["code"] == "REGENERATION_AI_UNAVAILABLE"
    assert phrase in error["message"]
    assert _STALE not in error["message"]
    _assert_nothing_changed(trip_id, "REGENERATION_AI_UNAVAILABLE")


def test_not_connected_interpreter_says_not_connected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    trip_id = _targeted_client_with(
        client,
        monkeypatch,
        AIFeedbackInterpretationResult(
            status=AIFeedbackInterpretationStatus.NOT_CONNECTED, blocked_reasons=["off"], confidence=0.0
        ),
    )

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    error = response.json()["errors"][0]
    assert error["code"] == "REGENERATION_AI_UNAVAILABLE"
    assert "not currently connected" in error["message"]


def test_async_targeted_rate_limit_fails_the_job_with_its_own_code(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _targeted_client_with(client, monkeypatch, _rejected("rate_limited"))
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "targeted_regeneration_enabled", True, raising=False)

    start = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert start.status_code == 202
    job = client.get(f"/trips/{trip_id}/jobs/{start.json()['data']['job_id']}").json()["data"]
    assert job["status"] == "failed"
    assert job["error_code"] == "REGENERATION_PROVIDER_RATE_LIMITED"
    assert _STALE not in str(job)


# -- Section 202B.2 (Task 33): resolved destination visibility ------------------------------


class _DescribingPlaces(DeterministicTestPlacesProvider):
    def describe_destination(self, destination: str) -> dict[str, str]:
        return {"display_name": "Córdoba, Andalusia, Spain", "city": "Córdoba", "country": "Spain"}


def test_generation_records_the_destination_the_provider_resolved(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _DescribingPlaces())
    trip_id = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    assert client.post(f"/trips/{trip_id}/generate").status_code == 200

    context = client.get(f"/trips/{trip_id}/destination-context").json()["data"]["destination_context"]

    assert context["resolved_destination"] == {
        "display_name": "Córdoba, Andalusia, Spain", "city": "Córdoba", "country": "Spain",
    }


def test_a_places_provider_that_cannot_describe_the_destination_records_nothing(
    client: TestClient,
) -> None:
    trip_id = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    client.post(f"/trips/{trip_id}/generate")
    context = client.get(f"/trips/{trip_id}/destination-context").json()["data"]["destination_context"]
    assert context["resolved_destination"] is None  # never invented
