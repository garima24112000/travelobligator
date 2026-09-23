from __future__ import annotations

from fastapi.testclient import TestClient

from app.repositories.planning_state_repository import planning_state_repository

# Section 199B.1 (Task 7/8): proves the regeneration-time workspace-
# consistency guard through the REAL production HTTP routes -- both
# legacy sync regeneration and the real error-code mapping for targeted
# regeneration's new `WORKSPACE_CONFLICT` status. Fork + activate a
# branch, drift its live content under the SAME version label (never
# creating a new revision), submit feedback, then attempt to regenerate
# -- must be refused with `BRANCH_STATE_CONFLICT`, never silently
# proceed.


def _main_branch_id(client: TestClient, trip_id: str) -> str:
    return client.get(f"/trips/{trip_id}/branches").json()["data"]["active_branch_id"]


def _head_revision_id(client: TestClient, trip_id: str, branch_id: str) -> str:
    branches = client.get(f"/trips/{trip_id}/branches").json()["data"]["branches"]
    return next(b for b in branches if b["branch_id"] == branch_id)["head_revision_id"]


def test_legacy_regeneration_refused_on_same_label_content_drift(
    client: TestClient, generated_trip_id: str
) -> None:
    main_branch_id = _main_branch_id(client, generated_trip_id)
    head_revision_id = _head_revision_id(client, generated_trip_id, main_branch_id)

    fork_response = client.post(
        f"/trips/{generated_trip_id}/branches",
        json={
            "source_revision_id": head_revision_id,
            "display_name": "Drifted",
            "activate_after_create": True,
        },
    )
    assert fork_response.status_code == 201

    # Drift: mutate the live persisted state's content directly, WITHOUT
    # going through any real regeneration/version-creation path -- the
    # same-version-label content drift this section exists to catch.
    live_state = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert live_state is not None
    assert live_state.experience_plan is not None
    for day in live_state.experience_plan.daily_plans:
        if day.experiences:
            day.experiences[0].name = "DRIFTED NAME, NEVER RECORDED AS A NEW REVISION"
            break
    planning_state_repository.save(live_state)

    feedback_response = client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed, it's too much walking"},
    )
    assert feedback_response.status_code == 200

    regen_response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})
    assert regen_response.status_code == 409
    assert regen_response.json()["errors"][0]["code"] == "BRANCH_STATE_CONFLICT"

    # Nothing was consumed/changed.
    reloaded = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert reloaded is not None
    assert reloaded.feedback_history[-1].applied_at is None
    assert reloaded.metadata.current_version == "v1"
