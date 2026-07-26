from __future__ import annotations

from fastapi.testclient import TestClient


def test_new_trip_has_empty_version_history(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}")
    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]
    assert planning_state["version_history"] == []


def test_generated_trip_has_one_version_history_item(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}")
    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]
    assert len(planning_state["version_history"]) == 1


def test_initial_version_item_has_expected_fields(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}")
    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]
    version_item = planning_state["version_history"][0]

    assert version_item["version_label"] == "v1"
    assert version_item["created_by"] == "system_generation"
    assert version_item["feedback_event_id"] is None
    assert version_item["preserved_sections"] == []
    assert set(version_item["changed_sections"]) == {
        "traveler_profile",
        "destination_context",
        "trip_strategy",
        "stay_transport",
        "experience_plan",
        "validation_report",
        "provider_coverage",
    }
    assert planning_state["metadata"]["current_version"] == "v1"

    # Only the documented bookkeeping fields exist -- no fake travel field
    # (destination/place/price/route) was invented alongside the version item.
    assert set(version_item.keys()) == {
        "version_id",
        "version_label",
        "created_by",
        "summary",
        "changed_sections",
        "preserved_sections",
        "feedback_event_id",
        "created_at",
    }


def test_calling_generate_again_does_not_duplicate_v1(
    client: TestClient, generated_trip_id: str
) -> None:
    before_response = client.get(f"/trips/{generated_trip_id}")
    before_version_history = before_response.json()["data"]["planning_state"][
        "version_history"
    ]
    assert len(before_version_history) == 1
    first_version_id = before_version_history[0]["version_id"]

    second_generate_response = client.post(f"/trips/{generated_trip_id}/generate")
    assert second_generate_response.status_code == 200
    after_version_history = second_generate_response.json()["data"]["planning_state"][
        "version_history"
    ]

    assert len(after_version_history) == 1
    assert after_version_history[0]["version_id"] == first_version_id


def test_feedback_submission_does_not_create_new_version(
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

    assert after_state["version_history"] == before_state["version_history"]
    assert (
        after_state["metadata"]["current_version"]
        == before_state["metadata"]["current_version"]
    )


def test_user_lock_create_and_delete_does_not_create_new_version(
    client: TestClient, generated_trip_id: str
) -> None:
    before_response = client.get(f"/trips/{generated_trip_id}")
    before_state = before_response.json()["data"]["planning_state"]

    lock_response = client.post(
        f"/trips/{generated_trip_id}/locks",
        json={
            "locked_item_type": "experience",
            "locked_item_id": "experience_test_1",
        },
    )
    assert lock_response.status_code == 201
    after_lock_state = lock_response.json()["data"]["planning_state"]

    assert after_lock_state["version_history"] == before_state["version_history"]
    assert (
        after_lock_state["metadata"]["current_version"]
        == before_state["metadata"]["current_version"]
    )

    lock_id = after_lock_state["user_locks"][0]["lock_id"]
    delete_response = client.delete(f"/trips/{generated_trip_id}/locks/{lock_id}")
    assert delete_response.status_code == 200
    after_delete_state = delete_response.json()["data"]["planning_state"]

    assert after_delete_state["version_history"] == before_state["version_history"]
    assert (
        after_delete_state["metadata"]["current_version"]
        == before_state["metadata"]["current_version"]
    )


def test_version_history_does_not_change_other_generated_sections(
    client: TestClient, generated_trip_id: str
) -> None:
    """Proves recording the initial version alongside a normal generate call
    never changes the sections it merely describes.
    """
    response = client.get(f"/trips/{generated_trip_id}")
    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]

    assert planning_state["experience_plan"] is not None
    assert planning_state["validation_report"]["readiness_status"] == "needs_review"
    assert planning_state["provider_coverage"]["places"] == "success"
    assert (
        planning_state["experience_plan"]["route_feasibility_context"][
            "daily_route_feasibility"
        ]
        == []
    )
    assert planning_state["feedback_history"] == []
    assert planning_state["pending_feedback_summary"]["status"] == "none"
    assert planning_state["user_locks"] == []
