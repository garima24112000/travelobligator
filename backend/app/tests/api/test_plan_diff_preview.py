from __future__ import annotations

from fastapi.testclient import TestClient


def test_new_trip_has_no_available_diff_preview(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}")
    assert response.status_code == 200
    preview = response.json()["data"]["planning_state"]["plan_diff_preview"]

    assert preview["preview_status"] == "not_available"
    assert preview["from_version"] is None
    assert preview["to_version"] is None
    assert preview["regeneration_available"] is False
    assert preview["would_create_version"] is None
    assert preview["triggered_by_feedback_event_ids"] == []
    assert preview["pending_feedback_count"] == 0
    assert preview["active_lock_count"] == 0
    assert preview["would_consider_sections"] == []
    assert preview["would_preserve_locked_items"] == []
    assert len(preview["blocked_by"]) > 0
    assert "no plan has been generated" in preview["note"].lower()


def test_generated_trip_with_no_feedback_has_from_version_v1(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}")
    assert response.status_code == 200
    preview = response.json()["data"]["planning_state"]["plan_diff_preview"]

    assert preview["from_version"] == "v1"
    assert preview["pending_feedback_count"] == 0
    assert preview["would_create_version"] is None
    assert "no pending feedback" in preview["note"].lower()


def test_feedback_submission_updates_diff_preview(
    client: TestClient, generated_trip_id: str
) -> None:
    feedback_response = client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    assert feedback_response.status_code == 200
    planning_state = feedback_response.json()["data"]["planning_state"]
    preview = planning_state["plan_diff_preview"]

    assert preview["preview_status"] == "ready_for_future_regeneration_preview"
    assert preview["from_version"] == "v1"
    assert preview["would_create_version"] == "v2"
    assert preview["pending_feedback_count"] == 1

    feedback_event_id = planning_state["feedback_history"][0]["feedback_event_id"]
    assert preview["triggered_by_feedback_event_ids"] == [feedback_event_id]

    # "Make this less packed" matches the pace_change rule, which affects
    # experience_plan and validation.
    assert preview["would_consider_sections"] == ["experience_plan", "validation"]
    assert preview["regeneration_available"] is False
    assert preview["to_version"] is None


def test_second_feedback_submission_increments_preview_counts(
    client: TestClient, generated_trip_id: str
) -> None:
    client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    second_response = client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "More museums please"},
    )
    assert second_response.status_code == 200
    planning_state = second_response.json()["data"]["planning_state"]
    preview = planning_state["plan_diff_preview"]

    assert preview["pending_feedback_count"] == 2
    feedback_ids = [event["feedback_event_id"] for event in planning_state["feedback_history"]]
    assert preview["triggered_by_feedback_event_ids"] == feedback_ids
    assert preview["would_create_version"] == "v2"


def test_feedback_submission_does_not_create_new_version(
    client: TestClient, generated_trip_id: str
) -> None:
    before_response = client.get(f"/trips/{generated_trip_id}")
    before_version_history = before_response.json()["data"]["planning_state"][
        "version_history"
    ]

    feedback_response = client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    assert feedback_response.status_code == 200
    after_version_history = feedback_response.json()["data"]["planning_state"][
        "version_history"
    ]

    assert after_version_history == before_version_history
    assert len(after_version_history) == 1


def test_creating_active_lock_updates_diff_preview(
    client: TestClient, generated_trip_id: str
) -> None:
    lock_response = client.post(
        f"/trips/{generated_trip_id}/locks",
        json={
            "locked_item_type": "experience",
            "locked_item_id": "experience_test_1",
            "reason": "user_requested_keep",
        },
    )
    assert lock_response.status_code == 201
    preview = lock_response.json()["data"]["planning_state"]["plan_diff_preview"]

    assert preview["active_lock_count"] == 1
    assert preview["would_preserve_locked_items"] == [
        {
            "locked_item_type": "experience",
            "locked_item_id": "experience_test_1",
            "reason": "user_requested_keep",
        }
    ]


def test_removing_lock_updates_diff_preview(
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
    lock_id = lock_response.json()["data"]["planning_state"]["user_locks"][0]["lock_id"]

    delete_response = client.delete(f"/trips/{generated_trip_id}/locks/{lock_id}")
    assert delete_response.status_code == 200
    preview = delete_response.json()["data"]["planning_state"]["plan_diff_preview"]

    assert preview["active_lock_count"] == 0
    assert preview["would_preserve_locked_items"] == []


def test_diff_preview_does_not_change_other_sections(
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


def test_no_fake_travel_fields_in_diff_preview(
    client: TestClient, generated_trip_id: str
) -> None:
    client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    lock_response = client.post(
        f"/trips/{generated_trip_id}/locks",
        json={
            "locked_item_type": "experience",
            "locked_item_id": "experience_test_1",
            "reason": "user_requested_keep",
        },
    )
    preview = lock_response.json()["data"]["planning_state"]["plan_diff_preview"]

    # Only the documented bookkeeping fields exist on the preview and on
    # each preserved-locked-item entry -- no destination, place, price,
    # rating, distance, or route-time field was invented.
    assert set(preview.keys()) == {
        "preview_status",
        "from_version",
        "to_version",
        "regeneration_available",
        "would_create_version",
        "triggered_by_feedback_event_ids",
        "pending_feedback_count",
        "active_lock_count",
        "would_consider_sections",
        "would_preserve_locked_items",
        "blocked_by",
        "note",
    }
    for preserved in preview["would_preserve_locked_items"]:
        assert set(preserved.keys()) == {
            "locked_item_type",
            "locked_item_id",
            "reason",
        }
