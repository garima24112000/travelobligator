from __future__ import annotations

from fastapi.testclient import TestClient


def test_new_trip_regeneration_readiness_is_blocked(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}/regeneration-readiness")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    readiness = body["data"]["regeneration_readiness"]

    assert readiness["status"] == "blocked"
    assert readiness["can_regenerate"] is False
    assert readiness["current_version"] is None
    assert readiness["would_create_version"] is None
    assert readiness["pending_feedback_count"] == 0
    assert readiness["active_lock_count"] == 0
    assert readiness["available_inputs"] == []
    assert "generated_plan" in readiness["missing_capabilities"]
    assert "regeneration_engine" in readiness["missing_capabilities"]
    assert len(readiness["blocked_by"]) > 0
    assert "generate the initial plan" in readiness["next_step"].lower()


def test_new_trip_visible_from_get_trip_too(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}")
    assert response.status_code == 200
    readiness = response.json()["data"]["planning_state"]["regeneration_readiness"]
    assert readiness["status"] == "blocked"
    assert readiness["current_version"] is None


def test_generated_trip_with_no_feedback_is_blocked_with_v1(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/regeneration-readiness")
    assert response.status_code == 200
    readiness = response.json()["data"]["regeneration_readiness"]

    assert readiness["status"] == "blocked"
    assert readiness["can_regenerate"] is False
    assert readiness["current_version"] == "v1"
    assert readiness["would_create_version"] is None
    assert readiness["pending_feedback_count"] == 0
    assert "generated_plan" in readiness["available_inputs"]
    assert "version_history" in readiness["available_inputs"]
    assert "regeneration_engine" in readiness["missing_capabilities"]


def test_generated_trip_with_feedback_is_blocked_with_v2_hint(
    client: TestClient, generated_trip_id: str
) -> None:
    feedback_response = client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    assert feedback_response.status_code == 200

    response = client.get(f"/trips/{generated_trip_id}/regeneration-readiness")
    assert response.status_code == 200
    readiness = response.json()["data"]["regeneration_readiness"]

    assert readiness["status"] == "blocked"
    assert readiness["can_regenerate"] is False
    assert readiness["current_version"] == "v1"
    assert readiness["would_create_version"] == "v2"
    assert readiness["pending_feedback_count"] == 1
    assert readiness["missing_capabilities"] == ["regeneration_engine"]


def test_user_lock_create_and_delete_updates_active_lock_count(
    client: TestClient, generated_trip_id: str
) -> None:
    lock_response = client.post(
        f"/trips/{generated_trip_id}/locks",
        json={
            "locked_item_type": "experience",
            "locked_item_id": "experience_test_1",
        },
    )
    assert lock_response.status_code == 201

    readiness_after_lock = client.get(
        f"/trips/{generated_trip_id}/regeneration-readiness"
    ).json()["data"]["regeneration_readiness"]
    assert readiness_after_lock["active_lock_count"] == 1
    assert "user_locks" in readiness_after_lock["available_inputs"]

    lock_id = lock_response.json()["data"]["planning_state"]["user_locks"][0]["lock_id"]
    delete_response = client.delete(f"/trips/{generated_trip_id}/locks/{lock_id}")
    assert delete_response.status_code == 200

    readiness_after_delete = client.get(
        f"/trips/{generated_trip_id}/regeneration-readiness"
    ).json()["data"]["regeneration_readiness"]
    assert readiness_after_delete["active_lock_count"] == 0
    assert "user_locks" not in readiness_after_delete["available_inputs"]


def test_regeneration_readiness_unknown_trip_returns_404(client: TestClient) -> None:
    response = client.get("/trips/does-not-exist/regeneration-readiness")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["errors"][0]["code"] == "TRIP_NOT_FOUND"
    assert body["errors"][0]["message"] == "Trip 'does-not-exist' was not found."


def test_regeneration_readiness_does_not_change_other_sections(
    client: TestClient, generated_trip_id: str
) -> None:
    before_response = client.get(f"/trips/{generated_trip_id}")
    before_state = before_response.json()["data"]["planning_state"]

    feedback_response = client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    assert feedback_response.status_code == 200
    after_state = feedback_response.json()["data"]["planning_state"]

    assert after_state["experience_plan"] == before_state["experience_plan"]
    assert (
        after_state["validation_report"]["readiness_status"]
        == before_state["validation_report"]["readiness_status"]
    )
    assert after_state["provider_coverage"] == before_state["provider_coverage"]
    assert (
        after_state["experience_plan"]["route_feasibility_context"]
        == before_state["experience_plan"]["route_feasibility_context"]
    )
    assert after_state["user_locks"] == before_state["user_locks"]
    assert after_state["version_history"] == before_state["version_history"]


def test_no_fake_travel_fields_in_regeneration_readiness(
    client: TestClient, generated_trip_id: str
) -> None:
    client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    response = client.get(f"/trips/{generated_trip_id}/regeneration-readiness")
    readiness = response.json()["data"]["regeneration_readiness"]

    assert set(readiness.keys()) == {
        "status",
        "can_regenerate",
        "current_version",
        "would_create_version",
        "pending_feedback_count",
        "active_lock_count",
        "required_inputs",
        "available_inputs",
        "missing_capabilities",
        "blocked_by",
        "next_step",
    }
