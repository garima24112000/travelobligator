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
    """As of Step 142, POST /regenerate is allowed to mutate exactly one
    thing: appending to `regeneration_attempts` (and the `metadata.updated_at`
    bump that comes with it). Every other section must stay byte-for-byte
    identical.
    """
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
    assert before_state["regeneration_attempts"] == []

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
    assert after_state["version_history"] == before_state["version_history"]
    assert after_state["plan_diff_preview"] == before_state["plan_diff_preview"]
    assert (
        after_state["regeneration_readiness"] == before_state["regeneration_readiness"]
    )

    # The only actual change: exactly one audit attempt was appended.
    assert len(after_state["regeneration_attempts"]) == 1


def test_repeated_regenerate_calls_append_multiple_audit_attempts(
    client: TestClient, generated_trip_id: str
) -> None:
    client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )

    before_state = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]
    assert before_state["regeneration_attempts"] == []

    first_response = client.post(f"/trips/{generated_trip_id}/regenerate")
    second_response = client.post(f"/trips/{generated_trip_id}/regenerate")
    third_response = client.post(f"/trips/{generated_trip_id}/regenerate")

    for response in (first_response, second_response, third_response):
        assert response.status_code == 409
        _assert_refusal_body(response.json())

    after_state = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]

    attempts = after_state["regeneration_attempts"]
    assert len(attempts) == 3
    # Each attempt gets its own attempt_id -- not the same record reused.
    assert len({attempt["attempt_id"] for attempt in attempts}) == 3
    for attempt in attempts:
        assert attempt["status"] == "blocked"
        assert attempt["reason_code"] == "REGENERATION_NOT_AVAILABLE"
        assert attempt["current_version"] == "v1"
        assert attempt["would_create_version"] == "v2"
        assert attempt["pending_feedback_count"] == 1
        assert attempt["active_lock_count"] == 0

    # Nothing besides regeneration_attempts (and the resulting updated_at
    # bump) changed across all three calls.
    assert after_state["experience_plan"] == before_state["experience_plan"]
    assert after_state["feedback_history"] == before_state["feedback_history"]
    assert after_state["user_locks"] == before_state["user_locks"]
    assert after_state["version_history"] == before_state["version_history"]
    assert after_state["plan_diff_preview"] == before_state["plan_diff_preview"]
    assert (
        after_state["regeneration_readiness"] == before_state["regeneration_readiness"]
    )


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


def _attempt_fields(attempt: dict) -> set[str]:
    return set(attempt.keys())


_EXPECTED_ATTEMPT_FIELDS = {
    "attempt_id",
    "status",
    "requested_at",
    "current_version",
    "would_create_version",
    "pending_feedback_count",
    "active_lock_count",
    "reason_code",
    "message",
}


def test_regenerate_ungenerated_trip_records_one_blocked_attempt(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.post(f"/trips/{created_trip_id}/regenerate")
    assert response.status_code == 409

    state = client.get(f"/trips/{created_trip_id}").json()["data"]["planning_state"]
    attempts = state["regeneration_attempts"]
    assert len(attempts) == 1

    attempt = attempts[0]
    assert _attempt_fields(attempt) == _EXPECTED_ATTEMPT_FIELDS
    assert attempt["status"] == "blocked"
    assert attempt["current_version"] is None
    assert attempt["would_create_version"] is None
    assert attempt["pending_feedback_count"] == 0
    assert attempt["active_lock_count"] == 0
    assert attempt["reason_code"] == "REGENERATION_NOT_AVAILABLE"
    assert attempt["message"] == (
        "Feedback-driven regeneration is not available yet. The "
        "regeneration engine has not been implemented, so no plan "
        "changes were made."
    )
    assert attempt["attempt_id"].startswith("regen_attempt_")


def test_regenerate_generated_trip_no_feedback_records_v1_and_no_would_create_version(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response.status_code == 409

    state = client.get(f"/trips/{generated_trip_id}").json()["data"]["planning_state"]
    attempt = state["regeneration_attempts"][0]

    assert attempt["current_version"] == "v1"
    assert attempt["would_create_version"] is None
    assert attempt["pending_feedback_count"] == 0
    assert attempt["active_lock_count"] == 0


def test_regenerate_generated_trip_with_feedback_records_v1_and_would_create_v2(
    client: TestClient, generated_trip_id: str
) -> None:
    client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )

    response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response.status_code == 409

    state = client.get(f"/trips/{generated_trip_id}").json()["data"]["planning_state"]
    attempt = state["regeneration_attempts"][0]

    assert attempt["current_version"] == "v1"
    assert attempt["would_create_version"] == "v2"
    assert attempt["pending_feedback_count"] == 1
    assert attempt["active_lock_count"] == 0


def test_regenerate_with_feedback_and_active_lock_records_active_lock_count(
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

    response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response.status_code == 409

    state = client.get(f"/trips/{generated_trip_id}").json()["data"]["planning_state"]
    attempt = state["regeneration_attempts"][0]

    assert attempt["active_lock_count"] == 1
    assert attempt["pending_feedback_count"] == 1


def test_regenerate_removed_lock_not_counted_as_active(
    client: TestClient, generated_trip_id: str
) -> None:
    lock_response = client.post(
        f"/trips/{generated_trip_id}/locks",
        json={
            "locked_item_type": "experience",
            "locked_item_id": "experience_test_1",
        },
    )
    lock_id = lock_response.json()["data"]["planning_state"]["user_locks"][0]["lock_id"]
    client.delete(f"/trips/{generated_trip_id}/locks/{lock_id}")

    response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response.status_code == 409

    state = client.get(f"/trips/{generated_trip_id}").json()["data"]["planning_state"]
    attempt = state["regeneration_attempts"][0]
    assert attempt["active_lock_count"] == 0


def test_get_regeneration_attempts_unknown_trip_returns_404(client: TestClient) -> None:
    response = client.get("/trips/does-not-exist/regeneration-attempts")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["errors"][0]["code"] == "TRIP_NOT_FOUND"
    assert body["errors"][0]["message"] == "Trip 'does-not-exist' was not found."


def test_get_regeneration_attempts_returns_empty_list_before_any_attempt(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}/regeneration-attempts")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["trip_id"] == created_trip_id
    assert body["data"]["regeneration_attempts"] == []


def test_get_regeneration_attempts_returns_attempts_in_stored_order(
    client: TestClient, generated_trip_id: str
) -> None:
    client.post(f"/trips/{generated_trip_id}/regenerate")
    client.post(f"/trips/{generated_trip_id}/regenerate")
    client.post(f"/trips/{generated_trip_id}/regenerate")

    response = client.get(f"/trips/{generated_trip_id}/regeneration-attempts")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["trip_id"] == generated_trip_id

    attempts = body["data"]["regeneration_attempts"]
    assert len(attempts) == 3
    # Stored order == request order: requested_at is non-decreasing.
    requested_at_values = [attempt["requested_at"] for attempt in attempts]
    assert requested_at_values == sorted(requested_at_values)

    # This read endpoint is itself read-only: calling it does not append or
    # otherwise mutate the audit trail.
    second_read = client.get(
        f"/trips/{generated_trip_id}/regeneration-attempts"
    ).json()
    assert second_read["data"]["regeneration_attempts"] == attempts


def test_get_regeneration_attempts_reflects_planning_state_too(
    client: TestClient, generated_trip_id: str
) -> None:
    client.post(f"/trips/{generated_trip_id}/regenerate")

    dedicated_endpoint = client.get(
        f"/trips/{generated_trip_id}/regeneration-attempts"
    ).json()["data"]["regeneration_attempts"]
    via_get_trip = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]["regeneration_attempts"]

    assert dedicated_endpoint == via_get_trip
