from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


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
    assert planning_state["validation_report"]["readiness_status"] == "blocked"


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
    for day_plan in experience_plan["daily_plans"]:
        assert day_plan["experiences"] == []

    assert body["data"]["validation_report"]["readiness_status"] == "blocked"


def test_validation_report_after_generate(client: TestClient, generated_trip_id: str) -> None:
    response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    validation_report = body["data"]["validation_report"]
    assert validation_report["readiness_status"] == "blocked"
    assert len(validation_report["critical_issues"]) > 0


def test_provider_coverage_after_generate(client: TestClient, generated_trip_id: str) -> None:
    response = client.get(f"/trips/{generated_trip_id}/provider-coverage")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    assert body["data"]["provider_coverage"]["places"] == "success"
