"""Step 148: code-level regeneration implementation guardrails.

Unlike test_regenerate_refusal.py (which checks observable behavior),
these tests monkeypatch the exact collaborators `POST /trips/{trip_id}/
regenerate` holds references to and assert they are never called except
where explicitly expected. The goal is to fail loudly and immediately if a
future change accidentally wires real or partial regeneration into this
endpoint, rather than relying only on behavioral assertions that a subtly
broken implementation might still happen to satisfy.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.routes.trips as trips_route


def _raise_assertion(*args, **kwargs):
    raise AssertionError("This must not be called by POST /regenerate.")


def test_regenerate_does_not_call_orchestrator_generation_or_feedback_paths(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /regenerate must never rerun planning stages or reinterpret
    feedback -- its only write-side collaborator is RegenerationAttemptService.
    """
    feedback_response = client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    assert feedback_response.status_code == 200

    monkeypatch.setattr(
        trips_route.planning_orchestrator, "generate_full_plan", _raise_assertion
    )
    monkeypatch.setattr(
        trips_route.planning_orchestrator, "apply_feedback", _raise_assertion
    )

    response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response.status_code == 409

    state = client.get(f"/trips/{generated_trip_id}").json()["data"]["planning_state"]
    assert len(state["regeneration_attempts"]) == 1


def test_regenerate_does_not_recompute_readiness_or_plan_diff_preview(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /regenerate must never recompute `regeneration_readiness` or
    `plan_diff_preview` -- both stay exactly what the last write path
    (create/generate/feedback/lock) already computed.
    """
    client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )

    before_state = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]

    monkeypatch.setattr(
        trips_route.regeneration_readiness_service, "recompute", _raise_assertion
    )
    monkeypatch.setattr(
        trips_route.plan_diff_preview_service, "recompute", _raise_assertion
    )

    response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response.status_code == 409

    after_state = client.get(f"/trips/{generated_trip_id}").json()["data"][
        "planning_state"
    ]
    assert len(after_state["regeneration_attempts"]) == 1
    assert (
        after_state["regeneration_readiness"] == before_state["regeneration_readiness"]
    )
    assert after_state["plan_diff_preview"] == before_state["plan_diff_preview"]


def test_regenerate_uses_regeneration_attempt_service_exactly_once_per_call(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RegenerationAttemptService.record_blocked_attempt is the only
    write-side service POST /regenerate is allowed to use, and it must be
    called exactly once per request -- never zero, never more than once,
    even across repeated calls.
    """
    calls: list[str] = []
    original = trips_route.regeneration_attempt_service.record_blocked_attempt

    def spy(planning_state):
        calls.append(planning_state.trip_id)
        return original(planning_state)

    monkeypatch.setattr(
        trips_route.regeneration_attempt_service, "record_blocked_attempt", spy
    )

    response_one = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response_one.status_code == 409
    assert len(calls) == 1

    response_two = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response_two.status_code == 409
    assert len(calls) == 2

    state = client.get(f"/trips/{generated_trip_id}").json()["data"]["planning_state"]
    assert len(state["regeneration_attempts"]) == 2


def test_regenerate_unknown_trip_does_not_call_regeneration_attempt_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown trip_id must 404 before ever reaching
    RegenerationAttemptService -- no audit attempt is ever recorded for a
    trip that does not exist.
    """
    monkeypatch.setattr(
        trips_route.regeneration_attempt_service,
        "record_blocked_attempt",
        _raise_assertion,
    )

    response = client.post("/trips/does-not-exist/regenerate")
    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "TRIP_NOT_FOUND"


def test_regenerate_does_not_call_generate_full_plan_with_no_feedback(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards against a future "regenerate by just rerunning generate"
    shortcut, even in the simplest case (no feedback, no locks) where such
    a shortcut might look harmless.
    """
    monkeypatch.setattr(
        trips_route.planning_orchestrator, "generate_full_plan", _raise_assertion
    )

    response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response.status_code == 409

    state = client.get(f"/trips/{generated_trip_id}").json()["data"]["planning_state"]
    assert len(state["regeneration_attempts"]) == 1
