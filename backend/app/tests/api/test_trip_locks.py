from __future__ import annotations

from fastapi.testclient import TestClient


def _lock_payload(
    locked_item_id: str = "experience_test_1",
    locked_item_type: str = "experience",
    reason: str | None = "user_requested_keep",
) -> dict[str, str | None]:
    payload: dict[str, str | None] = {
        "locked_item_type": locked_item_type,
        "locked_item_id": locked_item_id,
    }
    if reason is not None:
        payload["reason"] = reason
    return payload


def test_create_lock_creates_active_lock(client: TestClient, created_trip_id: str) -> None:
    response = client.post(f"/trips/{created_trip_id}/locks", json=_lock_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True

    user_locks = body["data"]["planning_state"]["user_locks"]
    assert len(user_locks) == 1
    lock = user_locks[0]
    assert lock["locked_item_type"] == "experience"
    assert lock["locked_item_id"] == "experience_test_1"
    assert lock["reason"] == "user_requested_keep"
    assert lock["is_active"] is True
    assert lock["removed_at"] is None

    # No fake travel fields were invented alongside the lock.
    assert set(lock.keys()) == {
        "lock_id",
        "locked_item_type",
        "locked_item_id",
        "reason",
        "is_active",
        "created_at",
        "removed_at",
    }


def test_lock_is_stored_in_planning_state_user_locks(
    client: TestClient, created_trip_id: str
) -> None:
    create_response = client.post(f"/trips/{created_trip_id}/locks", json=_lock_payload())
    assert create_response.status_code == 201

    get_response = client.get(f"/trips/{created_trip_id}")
    assert get_response.status_code == 200
    user_locks = get_response.json()["data"]["planning_state"]["user_locks"]
    assert len(user_locks) == 1
    assert user_locks[0]["locked_item_id"] == "experience_test_1"


def test_duplicate_lock_does_not_create_second_active_lock(
    client: TestClient, created_trip_id: str
) -> None:
    first_response = client.post(f"/trips/{created_trip_id}/locks", json=_lock_payload())
    assert first_response.status_code == 201

    second_response = client.post(f"/trips/{created_trip_id}/locks", json=_lock_payload())
    assert second_response.status_code == 201

    user_locks = second_response.json()["data"]["planning_state"]["user_locks"]
    active_locks = [lock for lock in user_locks if lock["is_active"]]
    assert len(active_locks) == 1

    # The existing lock_id is preserved rather than replaced.
    first_lock_id = first_response.json()["data"]["planning_state"]["user_locks"][0]["lock_id"]
    assert active_locks[0]["lock_id"] == first_lock_id


def test_delete_lock_marks_inactive_and_sets_removed_at(
    client: TestClient, created_trip_id: str
) -> None:
    create_response = client.post(f"/trips/{created_trip_id}/locks", json=_lock_payload())
    lock_id = create_response.json()["data"]["planning_state"]["user_locks"][0]["lock_id"]

    delete_response = client.delete(f"/trips/{created_trip_id}/locks/{lock_id}")
    assert delete_response.status_code == 200
    body = delete_response.json()
    assert body["success"] is True

    user_locks = body["data"]["planning_state"]["user_locks"]
    assert len(user_locks) == 1
    lock = user_locks[0]
    assert lock["lock_id"] == lock_id
    assert lock["is_active"] is False
    assert lock["removed_at"] is not None

    # The lock is deactivated, not physically removed from history.
    get_response = client.get(f"/trips/{created_trip_id}")
    assert len(get_response.json()["data"]["planning_state"]["user_locks"]) == 1


def test_create_lock_unknown_trip_id_returns_trip_not_found(client: TestClient) -> None:
    response = client.post("/trips/does-not-exist/locks", json=_lock_payload())
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["errors"][0]["code"] == "TRIP_NOT_FOUND"
    assert body["errors"][0]["message"] == "Trip 'does-not-exist' was not found."


def test_delete_lock_unknown_trip_id_returns_trip_not_found(client: TestClient) -> None:
    response = client.delete("/trips/does-not-exist/locks/lock_abc")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["errors"][0]["code"] == "TRIP_NOT_FOUND"
    assert body["errors"][0]["message"] == "Trip 'does-not-exist' was not found."


def test_delete_unknown_lock_id_on_existing_trip_returns_404(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.delete(f"/trips/{created_trip_id}/locks/lock_does_not_exist")
    assert response.status_code == 404
    body = response.json()
    assert set(["success", "data", "message", "errors", "metadata"]).issubset(body.keys())
    assert body["success"] is False
    assert body["data"] is None
    assert body["errors"][0]["code"] == "LOCK_NOT_FOUND"
    assert "lock_does_not_exist" in body["errors"][0]["message"]


def test_create_lock_blank_locked_item_id_is_rejected(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.post(
        f"/trips/{created_trip_id}/locks",
        json=_lock_payload(locked_item_id="   "),
    )
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["errors"][0]["code"] == "VALIDATION_ERROR"


def test_create_lock_invalid_locked_item_type_is_rejected(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.post(
        f"/trips/{created_trip_id}/locks",
        json=_lock_payload(locked_item_type="not_a_real_type"),
    )
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["errors"][0]["code"] == "VALIDATION_ERROR"


def test_create_lock_reason_defaults_when_omitted(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.post(
        f"/trips/{created_trip_id}/locks",
        json=_lock_payload(reason=None),
    )
    assert response.status_code == 201
    lock = response.json()["data"]["planning_state"]["user_locks"][0]
    assert lock["reason"] == "user_requested_keep"


def test_locking_does_not_modify_generated_plan_sections(
    client: TestClient, generated_trip_id: str
) -> None:
    before_response = client.get(f"/trips/{generated_trip_id}")
    before_state = before_response.json()["data"]["planning_state"]

    # Lock a real scheduled experience from the generated plan.
    experience_id = before_state["experience_plan"]["daily_plans"][0]["experiences"][0][
        "experience_id"
    ]
    lock_response = client.post(
        f"/trips/{generated_trip_id}/locks",
        json=_lock_payload(locked_item_id=experience_id),
    )
    assert lock_response.status_code == 201
    after_state = lock_response.json()["data"]["planning_state"]

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
    assert after_state["feedback_history"] == before_state["feedback_history"]
    assert after_state["pending_feedback_summary"] == before_state["pending_feedback_summary"]
    assert after_state["destination_context"] == before_state["destination_context"]
