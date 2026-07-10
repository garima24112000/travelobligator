from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.providers.base import PlacesProvider, unavailable_response
from app.providers.gateway import provider_gateway


def assert_api_response_shape(body: dict[str, Any]) -> None:
    assert set(["success", "data", "message", "errors", "metadata"]).issubset(body.keys())
    assert isinstance(body["success"], bool)
    assert isinstance(body["errors"], list)
    assert isinstance(body["metadata"], dict)
    assert "request_id" in body["metadata"]
    assert "timestamp" in body["metadata"]


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "ok"


def test_create_trip(client: TestClient) -> None:
    response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert_api_response_shape(body)
    assert body["success"] is True
    assert body["data"]["trip_id"]
    assert body["data"]["planning_state"]["destination_context"] is None
    assert body["data"]["planning_state"]["experience_plan"] is None
    assert body["data"]["planning_state"]["validation_report"] is None


def test_get_trip(client: TestClient, created_trip_id: str) -> None:
    response = client.get(f"/trips/{created_trip_id}")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    assert body["data"]["trip_id"] == created_trip_id


def test_destination_context_before_generate_returns_409(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}/destination-context")
    assert response.status_code == 409
    body = response.json()
    assert_api_response_shape(body)
    assert body["success"] is False
    assert body["data"] is None
    assert body["errors"][0]["code"] == "DATA_UNAVAILABLE"
    assert "not been generated" in body["errors"][0]["message"]


def test_experience_plan_before_generate_returns_409(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 409
    body = response.json()
    assert_api_response_shape(body)
    assert body["success"] is False
    assert body["errors"][0]["code"] == "DATA_UNAVAILABLE"
    assert "not been generated" in body["errors"][0]["message"]


def test_validation_report_before_generate_returns_409(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}/validation-report")
    assert response.status_code == 409
    body = response.json()
    assert_api_response_shape(body)
    assert body["success"] is False
    assert body["errors"][0]["code"] == "DATA_UNAVAILABLE"
    assert "not been generated" in body["errors"][0]["message"]


def test_generate_trip_plan(client: TestClient, created_trip_id: str) -> None:
    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    planning_state = body["data"]["planning_state"]
    assert planning_state["destination_context"] is not None
    assert planning_state["experience_plan"] is not None
    assert planning_state["validation_report"] is not None
    # The deterministic test places provider returns real candidate_pois, so the
    # conservative scheduling step schedules them and the plan is no longer blocked;
    # it is still not "ready" because route/timing feasibility is not implemented.
    assert planning_state["validation_report"]["readiness_status"] == "needs_review"


def test_destination_context_after_generate(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/destination-context")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    destination_context = body["data"]["destination_context"]
    assert len(destination_context["candidate_pois"]) > 0
    assert len(destination_context["candidate_restaurants"]) > 0
    assert len(destination_context["candidate_accommodation_pois"]) > 0


def test_experience_plan_after_generate(client: TestClient, generated_trip_id: str) -> None:
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    experience_plan = body["data"]["experience_plan"]

    assert len(experience_plan["daily_plans"]) > 0

    # The deterministic test provider returns real candidate_pois, so at least
    # one day should have scheduled experiences drawn from those candidates.
    all_experiences = [
        experience
        for day_plan in experience_plan["daily_plans"]
        for experience in day_plan["experiences"]
    ]
    assert len(all_experiences) > 0

    for day_plan in experience_plan["daily_plans"]:
        # No route/timing/cost estimation is implemented yet at the day level.
        assert day_plan["estimated_walking_km"] is None
        assert day_plan["estimated_travel_time_minutes"] is None
        assert day_plan["estimated_cost"] is None

    for experience in all_experiences:
        # Scheduled experiences must come from real candidates with no invented
        # times, durations, or cost/route facts.
        assert experience["name"] in {
            "Test Fixture Attraction One",
            "Test Fixture Attraction Two",
        }
        assert experience["start_time"] is None
        assert experience["end_time"] is None
        assert experience["estimated_duration_minutes"] is None

    assert body["data"]["validation_report"]["readiness_status"] == "needs_review"


def test_validation_report_after_generate(client: TestClient, generated_trip_id: str) -> None:
    response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    validation_report = body["data"]["validation_report"]
    # Experiences were scheduled from real candidates, so the plan is no longer
    # blocked, but route/timing feasibility is not implemented yet, so it is
    # needs_review rather than ready.
    assert validation_report["readiness_status"] == "needs_review"
    assert validation_report["critical_issues"] == []
    assert len(validation_report["warnings"]) > 0
    assert any(
        "feasibility" in warning["category"] for warning in validation_report["warnings"]
    )


def test_trip_summary_before_generate(client: TestClient, created_trip_id: str) -> None:
    response = client.get(f"/trips/{created_trip_id}/summary")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    summary = body["data"]

    assert summary["trip_id"] == created_trip_id
    assert summary["primary_destination"] == "Testville, Testland"
    assert summary["destination_context_generated"] is False
    assert summary["experience_plan_generated"] is False
    assert summary["validation_report_generated"] is False
    assert summary["candidate_pois_count"] == 0
    assert summary["candidate_restaurants_count"] == 0
    assert summary["candidate_accommodation_pois_count"] == 0
    assert summary["scheduled_experiences_count"] == 0
    assert summary["validation_status"] is None
    assert summary["main_blocking_reason"] is None
    assert summary["main_review_reason"] is None


def test_trip_summary_after_generate(client: TestClient, generated_trip_id: str) -> None:
    response = client.get(f"/trips/{generated_trip_id}/summary")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    summary = body["data"]

    assert summary["trip_id"] == generated_trip_id
    assert summary["destination_context_generated"] is True
    assert summary["experience_plan_generated"] is True
    assert summary["validation_report_generated"] is True

    # The deterministic test places provider returns 2 attractions, 1 restaurant,
    # and 1 accommodation POI, all of which should be reflected honestly.
    assert summary["candidate_pois_count"] == 2
    assert summary["candidate_restaurants_count"] == 1
    assert summary["candidate_accommodation_pois_count"] == 1
    assert summary["scheduled_experiences_count"] > 0

    assert summary["validation_status"] == "needs_review"
    assert summary["main_blocking_reason"] is None
    assert summary["main_review_reason"] is not None
    assert "feasibility" in summary["main_review_reason"].lower()


def test_provider_coverage_after_generate(client: TestClient, generated_trip_id: str) -> None:
    response = client.get(f"/trips/{generated_trip_id}/provider-coverage")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    assert body["data"]["provider_coverage"]["places"] == "success"


class _NoCandidatesTestPlacesProvider(PlacesProvider):
    """Test-only double that reports no usable places, for the still-blocked case."""

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> Any:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["attractions"]
        )

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> Any:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> Any:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_validation_report_blocked_when_no_candidate_pois(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _NoCandidatesTestPlacesProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    body = response.json()
    experience_plan = body["data"]["experience_plan"]
    for day_plan in experience_plan["daily_plans"]:
        assert day_plan["experiences"] == []

    validation_report = body["data"]["validation_report"]
    assert validation_report["readiness_status"] == "blocked"

    summary_response = client.get(f"/trips/{created_trip_id}/summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()["data"]
    assert summary["candidate_pois_count"] == 0
    assert summary["scheduled_experiences_count"] == 0
    assert summary["validation_status"] == "blocked"
    assert summary["main_blocking_reason"] is not None
    assert summary["main_review_reason"] is None
