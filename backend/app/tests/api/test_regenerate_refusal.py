from __future__ import annotations

from fastapi.testclient import TestClient


def _assert_refusal_body(body: dict) -> None:
    assert body["success"] is False
    assert body["data"] is None
    assert len(body["errors"]) == 1
    error = body["errors"][0]
    assert error["code"] == "REGENERATION_NOT_AVAILABLE"
    assert error["field"] == "regeneration"
    assert error["message"] == (
        "Feedback-driven regeneration is not available yet. The "
        "regeneration engine has not been implemented, so no plan "
        "changes were made."
    )
    # No generated plan content, fake diff, fake changed itinerary, or fake
    # v2 is ever smuggled into the refusal response.
    assert "daily_plans" not in str(body)
    assert "v2" not in str(body)


def test_regenerate_unknown_trip_returns_404_trip_not_found(client: TestClient) -> None:
    response = client.post("/trips/does-not-exist/regenerate")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["errors"][0]["code"] == "TRIP_NOT_FOUND"
    assert body["errors"][0]["message"] == "Trip 'does-not-exist' was not found."


def test_regenerate_ungenerated_trip_returns_refusal(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.post(f"/trips/{created_trip_id}/regenerate")
    assert response.status_code == 409
    _assert_refusal_body(response.json())


def test_regenerate_generated_trip_with_no_feedback_returns_refusal(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response.status_code == 409
    _assert_refusal_body(response.json())


def test_regenerate_generated_trip_with_feedback_returns_refusal(
    client: TestClient, generated_trip_id: str
) -> None:
    feedback_response = client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    assert feedback_response.status_code == 200

    response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response.status_code == 409
    _assert_refusal_body(response.json())


def test_regenerate_generated_trip_with_feedback_and_active_lock_returns_refusal(
    client: TestClient, generated_trip_id: str
) -> None:
    feedback_response = client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    assert feedback_response.status_code == 200

    lock_response = client.post(
        f"/trips/{generated_trip_id}/locks",
        json={
            "locked_item_type": "experience",
            "locked_item_id": "experience_test_1",
        },
    )
    assert lock_response.status_code == 201

    response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response.status_code == 409
    _assert_refusal_body(response.json())


def test_regenerate_does_not_create_v2_or_touch_version_history(
    client: TestClient, generated_trip_id: str
) -> None:
    client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )

    before_state = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]
    assert len(before_state["version_history"]) == 1
    assert before_state["metadata"]["current_version"] == "v1"

    regenerate_response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert regenerate_response.status_code == 409

    after_state = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]

    assert after_state["version_history"] == before_state["version_history"]
    assert len(after_state["version_history"]) == 1
    assert after_state["metadata"]["current_version"] == "v1"
    assert not any(
        version["version_label"] == "v2" for version in after_state["version_history"]
    )


def test_regenerate_does_not_change_other_sections(
    client: TestClient, generated_trip_id: str
) -> None:
    client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    client.post(
        f"/trips/{generated_trip_id}/locks",
        json={
            "locked_item_type": "experience",
            "locked_item_id": "experience_test_1",
        },
    )

    before_state = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]

    regenerate_response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert regenerate_response.status_code == 409

    after_state = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]

    assert after_state["experience_plan"] == before_state["experience_plan"]
    assert after_state["destination_context"] == before_state["destination_context"]
    assert (
        after_state["validation_report"]["readiness_status"]
        == before_state["validation_report"]["readiness_status"]
    )
    assert after_state["provider_coverage"] == before_state["provider_coverage"]
    assert (
        after_state["experience_plan"]["route_feasibility_context"]
        == before_state["experience_plan"]["route_feasibility_context"]
    )
    assert after_state["feedback_history"] == before_state["feedback_history"]
    assert (
        after_state["pending_feedback_summary"]
        == before_state["pending_feedback_summary"]
    )
    assert after_state["user_locks"] == before_state["user_locks"]
    assert after_state["plan_diff_preview"] == before_state["plan_diff_preview"]
    assert (
        after_state["regeneration_readiness"] == before_state["regeneration_readiness"]
    )
    assert after_state["metadata"]["updated_at"] == before_state["metadata"]["updated_at"]


def test_repeated_regenerate_calls_return_same_refusal_without_mutation(
    client: TestClient, generated_trip_id: str
) -> None:
    client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )

    before_state = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]

    first_response = client.post(f"/trips/{generated_trip_id}/regenerate")
    second_response = client.post(f"/trips/{generated_trip_id}/regenerate")
    third_response = client.post(f"/trips/{generated_trip_id}/regenerate")

    for response in (first_response, second_response, third_response):
        assert response.status_code == 409
        _assert_refusal_body(response.json())

    after_state = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]
    assert after_state == before_state


def test_regenerate_response_has_standard_api_shape(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.post(f"/trips/{created_trip_id}/regenerate")
    body = response.json()
    assert set(["success", "data", "message", "errors", "metadata"]).issubset(
        body.keys()
    )
    assert "request_id" in body["metadata"]
    assert "timestamp" in body["metadata"]
