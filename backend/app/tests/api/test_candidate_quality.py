from __future__ import annotations

from fastapi.testclient import TestClient


def test_new_trip_candidate_quality_report_is_none(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}")
    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]
    assert planning_state["candidate_quality_report"] is None


def test_new_trip_candidate_quality_endpoint_returns_null_report(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}/candidate-quality")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["trip_id"] == created_trip_id
    assert body["data"]["candidate_quality_report"] is None


def test_generate_populates_candidate_quality_report(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}")
    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]

    report = planning_state["candidate_quality_report"]
    assert report is not None
    assert report["destination_name"]
    assert report["generated_at"]


def test_candidate_quality_counts_match_destination_context_candidates(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}")
    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]

    destination_context = planning_state["destination_context"]
    report = planning_state["candidate_quality_report"]

    assert len(report["attraction_scores"]) == len(destination_context["candidate_pois"])
    assert len(report["restaurant_scores"]) == len(destination_context["candidate_restaurants"])
    assert len(report["accommodation_poi_scores"]) == len(
        destination_context["candidate_accommodation_pois"]
    )
    # The deterministic test places provider (conftest.py) always returns 2
    # attractions, 1 restaurant, and 1 accommodation POI.
    assert len(report["attraction_scores"]) == 2
    assert len(report["restaurant_scores"]) == 1
    assert len(report["accommodation_poi_scores"]) == 1


def test_candidate_quality_endpoint_returns_the_stored_report(
    client: TestClient, generated_trip_id: str
) -> None:
    trip_response = client.get(f"/trips/{generated_trip_id}")
    stored_report = trip_response.json()["data"]["planning_state"]["candidate_quality_report"]

    endpoint_response = client.get(f"/trips/{generated_trip_id}/candidate-quality")
    assert endpoint_response.status_code == 200
    body = endpoint_response.json()
    assert body["data"]["trip_id"] == generated_trip_id
    assert body["data"]["candidate_quality_report"] == stored_report


def test_candidate_quality_unknown_trip_returns_404(client: TestClient) -> None:
    response = client.get("/trips/does-not-exist/candidate-quality")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["errors"][0]["code"] == "TRIP_NOT_FOUND"


def test_candidate_quality_endpoint_is_read_only(
    client: TestClient, generated_trip_id: str
) -> None:
    before_response = client.get(f"/trips/{generated_trip_id}")
    before_state = before_response.json()["data"]["planning_state"]

    client.get(f"/trips/{generated_trip_id}/candidate-quality")
    client.get(f"/trips/{generated_trip_id}/candidate-quality")

    after_response = client.get(f"/trips/{generated_trip_id}")
    after_state = after_response.json()["data"]["planning_state"]

    assert after_state == before_state
    assert after_state["metadata"]["updated_at"] == before_state["metadata"]["updated_at"]
    assert after_state["candidate_quality_report"] == before_state["candidate_quality_report"]
    assert after_state["experience_plan"] == before_state["experience_plan"]
    assert after_state["validation_report"] == before_state["validation_report"]


def test_candidate_quality_report_has_no_forbidden_factual_fields(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/candidate-quality")
    report = response.json()["data"]["candidate_quality_report"]

    forbidden_field_names = {
        "price",
        "rating",
        "opening_hours",
        "route_time",
        "booking_url",
        "review_count",
        "safety_score",
    }
    for score_list_key in ("attraction_scores", "restaurant_scores", "accommodation_poi_scores"):
        for score in report[score_list_key]:
            assert set(score.keys()) & forbidden_field_names == set()
