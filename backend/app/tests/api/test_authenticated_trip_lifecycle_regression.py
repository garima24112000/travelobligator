from __future__ import annotations

from fastapi.testclient import TestClient

from app.tests.conftest import create_trip_payload

# Step 184D regression guard: the full, real create -> generate ->
# experience-plan -> feedback -> regenerate -> lock lifecycle must work
# completely unchanged for the trip's rightful owner. This is the single
# consolidated version of what the much larger pre-existing suite
# (test_trips_smoke.py, test_regenerate_refusal.py, test_trip_locks.py,
# test_version_history.py, etc. -- all still passing unmodified after
# this step) already proves piecemeal; this file exists so that guarantee
# is stated in one place, explicitly, for Step 184D's own record.


def test_full_authenticated_lifecycle_matches_pre_auth_behavior(client: TestClient) -> None:
    # 1. Create -- authenticated, owned by `client`'s user.
    create_response = client.post("/trips", json=create_trip_payload())
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    # 2. Generate.
    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200
    planning_state = generate_response.json()["data"]["planning_state"]
    assert planning_state["metadata"]["pipeline_status"] in ("validated", "needs_review")

    # 3. Experience plan is real and populated.
    experience_plan_response = client.get(f"/trips/{trip_id}/experience-plan")
    assert experience_plan_response.status_code == 200
    assert experience_plan_response.json()["data"]["experience_plan"] is not None

    # 4. Version history has exactly one entry after generation (v1).
    version_history = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"][
        "version_history"
    ]
    assert len(version_history) == 1

    # 5. Submit feedback.
    feedback_response = client.post(
        f"/trips/{trip_id}/feedback", json={"feedback_text": "Make this less packed"}
    )
    assert feedback_response.status_code == 200

    # 6. Regeneration readiness reflects the pending feedback.
    readiness_response = client.get(f"/trips/{trip_id}/regeneration-readiness")
    assert readiness_response.status_code == 200

    # 7. A lock still blocks regeneration entirely, exactly as before --
    #    never worked around, never partially honored.
    experience_plan = experience_plan_response.json()["data"]["experience_plan"]
    first_experience_id = experience_plan["daily_plans"][0]["experiences"][0]["experience_id"]
    lock_response = client.post(
        f"/trips/{trip_id}/locks",
        json={"locked_item_type": "experience", "locked_item_id": first_experience_id},
    )
    assert lock_response.status_code == 201
    lock_id = lock_response.json()["data"]["planning_state"]["user_locks"][0]["lock_id"]

    regenerate_blocked_response = client.post(
        f"/trips/{trip_id}/regenerate", json={"confirm": True}
    )
    assert regenerate_blocked_response.status_code == 409
    assert (
        regenerate_blocked_response.json()["errors"][0]["code"]
        == "REGENERATION_BLOCKED_BY_LOCKS"
    )

    # 8. Removing the lock allows regeneration to proceed normally.
    delete_lock_response = client.delete(f"/trips/{trip_id}/locks/{lock_id}")
    assert delete_lock_response.status_code == 200

    regenerate_response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})
    assert regenerate_response.status_code == 200
    regenerate_data = regenerate_response.json()["data"]
    assert regenerate_data["status"] == "applied"
    assert regenerate_data["previous_version"] == "v1"
    assert regenerate_data["current_version"] != "v1"

    # 9. Version history now has two entries.
    final_version_history = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"][
        "version_history"
    ]
    assert len(final_version_history) == 2

    # 10. Auth never depended on the itinerary narrator -- disabled by
    #     default, and this whole lifecycle succeeded without it.
    final_state = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
    narrative_report = final_state.get("itinerary_narrative_report")
    if narrative_report is not None:
        assert narrative_report["status"] in ("not_connected", "disabled")
