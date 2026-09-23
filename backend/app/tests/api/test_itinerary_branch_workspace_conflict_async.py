from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.repositories.planning_state_repository import planning_state_repository

# Section 199B.1 (Task 8): the same workspace-consistency guard, through
# the async legacy regeneration job path -- `apply_regeneration_mutation`
# is the ONE shared boundary both the sync route and the async job
# runner already call (Task 8: "do not duplicate a second comparison in
# the job runner"), so this test exists to prove that sharing actually
# holds end-to-end through a real async job, not just read from the
# source.


def _enable_async_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()


def _main_branch_id(client: TestClient, trip_id: str) -> str:
    return client.get(f"/trips/{trip_id}/branches").json()["data"]["active_branch_id"]


def _head_revision_id(client: TestClient, trip_id: str, branch_id: str) -> str:
    branches = client.get(f"/trips/{trip_id}/branches").json()["data"]["branches"]
    return next(b for b in branches if b["branch_id"] == branch_id)["head_revision_id"]


def test_async_legacy_regeneration_refused_on_same_label_content_drift(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    main_branch_id = _main_branch_id(client, generated_trip_id)
    head_revision_id = _head_revision_id(client, generated_trip_id, main_branch_id)

    fork_response = client.post(
        f"/trips/{generated_trip_id}/branches",
        json={
            "source_revision_id": head_revision_id,
            "display_name": "Drifted Async",
            "activate_after_create": True,
        },
    )
    assert fork_response.status_code == 201

    live_state = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert live_state is not None
    live_state.experience_plan.daily_plans[0].experiences.append(
        live_state.experience_plan.daily_plans[0].experiences[0].model_copy(
            update={"experience_id": "exp_DRIFT_ASYNC"}
        )
    )
    planning_state_repository.save(live_state)

    feedback_response = client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed, it's too much walking"},
    )
    assert feedback_response.status_code == 200

    _enable_async_generation(monkeypatch)
    start_response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})
    assert start_response.status_code == 202
    job_id = start_response.json()["data"]["job_id"]

    job_data = client.get(f"/trips/{generated_trip_id}/jobs/{job_id}").json()["data"]
    assert job_data["status"] == "failed"
    assert job_data["error_code"] == "BRANCH_STATE_CONFLICT"

    reloaded = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert reloaded is not None
    assert reloaded.feedback_history[-1].applied_at is None
