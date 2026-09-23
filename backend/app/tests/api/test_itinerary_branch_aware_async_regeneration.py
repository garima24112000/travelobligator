from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.repositories.planning_state_repository import planning_state_repository

# Section 199B Task 30: async legacy regeneration (`ASYNC_GENERATION_
# ENABLED=true`) is branch-aware -- a job started while an activated fork
# is the active branch must record its result onto THAT fork, never Main,
# exactly like the synchronous path already proven in
# `test_itinerary_fork_and_activation_api.py`. Mirrors `test_async_
# regenerate.py`'s own `_enable_async_generation` convention (enabled
# strictly after `generated_trip_id`'s own synchronous setup).


def _enable_async_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()


def _main_branch_id(client: TestClient, trip_id: str) -> str:
    return client.get(f"/trips/{trip_id}/branches").json()["data"]["active_branch_id"]


def _head_revision_id(client: TestClient, trip_id: str, branch_id: str) -> str:
    branches = client.get(f"/trips/{trip_id}/branches").json()["data"]["branches"]
    return next(b for b in branches if b["branch_id"] == branch_id)["head_revision_id"]


def test_async_legacy_regeneration_on_activated_fork_updates_only_that_branch(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    main_branch_id = _main_branch_id(client, generated_trip_id)
    main_head_before = _head_revision_id(client, generated_trip_id, main_branch_id)

    fork_response = client.post(
        f"/trips/{generated_trip_id}/branches",
        json={
            "source_revision_id": main_head_before,
            "display_name": "Async Alternate",
            "activate_after_create": True,
        },
    )
    assert fork_response.status_code == 201
    branch_id = fork_response.json()["data"]["branch"]["branch_id"]
    assert _main_branch_id(client, generated_trip_id) == branch_id

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
    assert job_data["status"] == "succeeded"

    reloaded_state = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert reloaded_state is not None
    assert reloaded_state.metadata.active_branch_id == branch_id

    # Main's head is completely untouched by the async job.
    main_head_after = _head_revision_id(client, generated_trip_id, main_branch_id)
    assert main_head_after == main_head_before

    # The fork's head advanced, parented on the fork's own base.
    fork_head_after = _head_revision_id(client, generated_trip_id, branch_id)
    assert fork_head_after != main_head_before
    revision_data = client.get(
        f"/trips/{generated_trip_id}/revisions/{fork_head_after}"
    ).json()["data"]
    assert revision_data["branch_id"] == branch_id
    assert revision_data["parent_revision_id"] == main_head_before
