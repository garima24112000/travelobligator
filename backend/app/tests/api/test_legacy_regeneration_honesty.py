from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.repositories.planning_state_repository import planning_state_repository
from app.services import regeneration_mutation_service

# Section 202B.1 (Tasks 1-4, 17, 21): the 202A baseline measured legacy
# regeneration reporting "applied" for free-text requests it could not
# possibly honor (0 of 9 requested changes happened). These tests run
# against the REAL production registry (no deterministic legacy operation
# is registered, and NO synthetic fixture is requested) through the real
# HTTP routes.

_STALE = "not been implemented"


def _plan_ids(trip_id: str) -> list[list[str]]:
    state = planning_state_repository.get_by_trip_id(trip_id)
    assert state is not None and state.experience_plan is not None
    return [[e.experience_id for e in d.experiences] for d in state.experience_plan.daily_plans]


def _branch_head(client: TestClient, trip_id: str) -> tuple[str, int]:
    branches = client.get(f"/trips/{trip_id}/branches").json()["data"]["branches"]
    active = next(b for b in branches if b["is_active"])
    revisions = client.get(f"/trips/{trip_id}/branches/{active['branch_id']}/revisions").json()["data"]["revisions"]
    return active["head_revision_id"], len(revisions)


def _submit(client: TestClient, trip_id: str, text: str) -> None:
    assert client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": text}).status_code == 200


@pytest.mark.parametrize(
    "feedback_text",
    [
        "Remove the first museum from the itinerary.",
        "Make day 2 less packed.",
        "I want more local food.",
    ],
)
def test_free_text_feedback_is_refused_honestly_and_changes_nothing(
    client: TestClient, generated_trip_id: str, feedback_text: str
) -> None:
    before_ids = _plan_ids(generated_trip_id)
    before_state = planning_state_repository.get_by_trip_id(generated_trip_id)
    head_before, revisions_before = _branch_head(client, generated_trip_id)
    _submit(client, generated_trip_id, feedback_text)

    response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    error = response.json()["errors"][0]
    assert error["code"] == "REGENERATION_FEEDBACK_NOT_INTERPRETABLE"
    assert _STALE not in error["message"]
    assert "AI" in error["message"]

    after = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert after is not None
    # No rerun happened at all: not one experience id changed, no new version.
    assert _plan_ids(generated_trip_id) == before_ids
    assert after.metadata.current_version == before_state.metadata.current_version
    assert len(after.version_history) == len(before_state.version_history)
    # Feedback stays pending/unapplied.
    event = after.feedback_history[-1]
    assert event.applied_at is None
    assert event.applied_in_version is None
    assert event.handling_status != "applied"
    # Audit is truthful.
    attempt = after.regeneration_attempts[-1]
    assert attempt.status == "blocked"
    assert attempt.reason_code == "REGENERATION_FEEDBACK_NOT_INTERPRETABLE"
    assert _STALE not in attempt.message
    # No new revision, branch head unchanged.
    assert _branch_head(client, generated_trip_id) == (head_before, revisions_before)


def test_refused_feedback_remains_pending_in_readiness(client: TestClient, generated_trip_id: str) -> None:
    _submit(client, generated_trip_id, "Remove the first museum.")
    client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})
    state = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert state is not None
    assert state.pending_feedback_summary.total_feedback_items == 1


def test_async_mode_refuses_synchronously_and_starts_no_job(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()
    _submit(client, generated_trip_id, "Remove the first museum.")

    response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_FEEDBACK_NOT_INTERPRETABLE"
    jobs = client.get(f"/trips/{generated_trip_id}/jobs")
    if jobs.status_code == 200:
        assert [j for j in jobs.json()["data"]["jobs"] if j["job_type"] == "regenerate"] == []


def test_registered_operation_with_true_postcondition_succeeds(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        regeneration_mutation_service.LEGACY_SUPPORTED_OPERATIONS,
        "pace_change",
        lambda before, after, event: True,
    )
    _submit(client, generated_trip_id, "Make this less packed, it is too much walking.")

    response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "applied"
    state = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert state is not None and state.feedback_history[-1].applied_in_version == "v2"


def test_registered_operation_with_failed_postcondition_is_a_no_effect_refusal(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        regeneration_mutation_service.LEGACY_SUPPORTED_OPERATIONS,
        "pace_change",
        lambda before, after, event: False,
    )
    before_ids = _plan_ids(generated_trip_id)
    head_before, revisions_before = _branch_head(client, generated_trip_id)
    _submit(client, generated_trip_id, "Make this less packed, it is too much walking.")

    response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_NO_EFFECT"
    after = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert after is not None
    assert after.metadata.current_version == "v1"
    assert after.feedback_history[-1].applied_at is None
    assert after.regeneration_attempts[-1].reason_code == "REGENERATION_NO_EFFECT"
    # The pre-rerun state was restored: ids unchanged, no revision recorded.
    assert _plan_ids(generated_trip_id) == before_ids
    assert _branch_head(client, generated_trip_id) == (head_before, revisions_before)


def test_no_regeneration_refusal_ever_uses_stale_engine_copy(client: TestClient, generated_trip_id: str) -> None:
    bodies = []
    bodies.append(client.post(f"/trips/{generated_trip_id}/regenerate").json())  # confirm missing
    bodies.append(client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True}).json())  # no feedback
    _submit(client, generated_trip_id, "Remove the first museum.")
    bodies.append(client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True}).json())
    for body in bodies:
        assert _STALE not in str(body)
    state = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert state is not None
    assert all(_STALE not in attempt.message for attempt in state.regeneration_attempts)
