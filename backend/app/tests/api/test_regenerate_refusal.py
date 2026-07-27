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


# ---------------------------------------------------------------------------
# Step 146: regeneration safety test hardening
# ---------------------------------------------------------------------------

_FORBIDDEN_AUDIT_SUBSTRINGS = (
    "experience_plan",
    "daily_plans",
    "destination_context",
    "candidate_pois",
    "coordinates",
    "price",
    "rating",
    "opening_hours",
    "route",
    "duration_minutes",
    "provider_coverage",
    "user_locks",
    "feedback_history",
    "version_history",
)


def _assert_only_allowed_keys_changed(
    before: dict, after: dict, allowed_top_level_keys: set[str]
) -> None:
    """Generic deep-snapshot comparison: every top-level PlanningState key
    must be byte-for-byte identical before/after, except the explicitly
    allowed ones and `metadata.updated_at` (which is expected to bump
    whenever `regeneration_attempts` changes). This is deliberately
    key-driven off `before` rather than hand-picking sections, so a new
    PlanningState field added later is covered automatically instead of
    silently escaping this check.
    """
    assert set(before.keys()) == set(after.keys())
    for key in before:
        if key in allowed_top_level_keys:
            continue
        if key == "metadata":
            before_metadata = dict(before["metadata"])
            after_metadata = dict(after["metadata"])
            before_metadata.pop("updated_at", None)
            after_metadata.pop("updated_at", None)
            assert after_metadata == before_metadata, "metadata changed beyond updated_at"
            continue
        assert after[key] == before[key], f"Unexpected change in top-level key '{key}'"


def test_regenerate_deep_snapshot_only_allows_attempts_and_updated_at(
    client: TestClient, generated_trip_id: str
) -> None:
    """Invariant 2: for a generated trip with feedback and an active lock,
    a full deep snapshot of PlanningState before/after POST /regenerate
    must be identical except for `regeneration_attempts` and
    `metadata.updated_at`.
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

    regenerate_response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert regenerate_response.status_code == 409

    after_state = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]

    _assert_only_allowed_keys_changed(
        before_state, after_state, allowed_top_level_keys={"regeneration_attempts"}
    )
    assert len(after_state["regeneration_attempts"]) == 1
    assert after_state["metadata"]["updated_at"] != before_state["metadata"]["updated_at"]


def test_get_regeneration_attempts_is_fully_read_only(
    client: TestClient, generated_trip_id: str
) -> None:
    """Invariant 3: GET /regeneration-attempts must not append attempts,
    must not bump metadata.updated_at, and must not mutate anything else --
    calling it repeatedly leaves the whole PlanningState byte-for-byte
    identical.
    """
    # Seed exactly one real attempt so the audit list isn't trivially empty.
    client.post(f"/trips/{generated_trip_id}/regenerate")

    before_state = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]
    assert len(before_state["regeneration_attempts"]) == 1

    for _ in range(3):
        response = client.get(f"/trips/{generated_trip_id}/regeneration-attempts")
        assert response.status_code == 200

    after_state = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]
    assert after_state == before_state
    assert len(after_state["regeneration_attempts"]) == 1


def test_regeneration_readiness_unchanged_by_regenerate(
    client: TestClient, generated_trip_id: str
) -> None:
    """Invariant 4: regeneration_readiness must be exactly the same before
    and after POST /regenerate, read via its own dedicated endpoint.
    """
    client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )

    before_readiness = client.get(
        f"/trips/{generated_trip_id}/regeneration-readiness"
    ).json()["data"]["regeneration_readiness"]
    assert before_readiness["would_create_version"] == "v2"

    regenerate_response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert regenerate_response.status_code == 409

    after_readiness = client.get(
        f"/trips/{generated_trip_id}/regeneration-readiness"
    ).json()["data"]["regeneration_readiness"]

    assert after_readiness == before_readiness


def test_plan_diff_preview_unchanged_by_regenerate(
    client: TestClient, generated_trip_id: str
) -> None:
    """Invariant 5: plan_diff_preview must be exactly the same before and
    after POST /regenerate.
    """
    client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )

    before_preview = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]["plan_diff_preview"]
    assert before_preview["would_create_version"] == "v2"

    regenerate_response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert regenerate_response.status_code == 409

    after_preview = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]["plan_diff_preview"]

    assert after_preview == before_preview


def test_version_history_stays_exactly_v1_after_regenerate(
    client: TestClient, generated_trip_id: str
) -> None:
    """Invariant 6: exactly one v1 remains, current_version stays v1, and
    no v2 version_label ever appears, even with feedback and a lock in
    place.
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

    client.post(f"/trips/{generated_trip_id}/regenerate")
    client.post(f"/trips/{generated_trip_id}/regenerate")

    state = client.get(f"/trips/{generated_trip_id}").json()["data"]["planning_state"]

    assert len(state["version_history"]) == 1
    assert state["version_history"][0]["version_label"] == "v1"
    assert state["metadata"]["current_version"] == "v1"
    assert not any(
        version["version_label"] == "v2" for version in state["version_history"]
    )


def test_refusal_response_never_leaks_v2_even_though_readiness_and_diff_do(
    client: TestClient, generated_trip_id: str
) -> None:
    """Invariant 7: even though both regeneration_readiness.would_create_version
    and plan_diff_preview.would_create_version are "v2" once feedback
    exists, the POST /regenerate error response body must never contain
    "v2" anywhere -- v2 is only ever visible via the readiness/diff/audit
    readouts, never implied by the refusal itself.
    """
    client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )

    readiness = client.get(
        f"/trips/{generated_trip_id}/regeneration-readiness"
    ).json()["data"]["regeneration_readiness"]
    assert readiness["would_create_version"] == "v2"

    preview = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]["plan_diff_preview"]
    assert preview["would_create_version"] == "v2"

    regenerate_response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert regenerate_response.status_code == 409
    _assert_refusal_body(regenerate_response.json())

    # Check the raw response text too, not just the parsed dict's repr.
    assert "v2" not in regenerate_response.text


def test_unknown_trip_regenerate_repeated_calls_stay_404_and_create_nothing(
    client: TestClient,
) -> None:
    """Invariant 8: repeated POST /regenerate calls against an unknown
    trip_id keep returning TRIP_NOT_FOUND and never create any state (there
    is no PlanningState to attach an audit record to).
    """
    for _ in range(3):
        response = client.post("/trips/does-not-exist/regenerate")
        assert response.status_code == 404
        assert response.json()["errors"][0]["code"] == "TRIP_NOT_FOUND"

    # Confirms the unknown-trip id never became a real trip as a side effect.
    get_response = client.get("/trips/does-not-exist")
    assert get_response.status_code == 404
    assert get_response.json()["errors"][0]["code"] == "TRIP_NOT_FOUND"


def test_regeneration_attempt_field_contract_no_leaked_sections(
    client: TestClient, generated_trip_id: str
) -> None:
    """Invariant 1: the recorded attempt contains exactly the nine
    documented fields (already checked via _EXPECTED_ATTEMPT_FIELDS
    elsewhere) and, even with feedback and an active lock in play, no
    substring of any real PlanningState section name/travel-fact field
    leaks into the attempt.
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
    client.post(f"/trips/{generated_trip_id}/regenerate")

    attempts = client.get(
        f"/trips/{generated_trip_id}/regeneration-attempts"
    ).json()["data"]["regeneration_attempts"]
    assert len(attempts) == 1
    attempt = attempts[0]

    assert set(attempt.keys()) == _EXPECTED_ATTEMPT_FIELDS

    attempt_text = str(attempt)
    for forbidden in _FORBIDDEN_AUDIT_SUBSTRINGS:
        assert forbidden not in attempt_text, f"'{forbidden}' leaked into attempt: {attempt}"
