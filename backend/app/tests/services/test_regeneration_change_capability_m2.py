"""M2 Phase 1 (D3): regeneration readiness states whether THIS deployment can
apply a change request, using the same switches and registry the regenerate
route uses -- so "ready" is never shown for an action that would certainly
be refused. Production's configuration (targeted regeneration off, no
registered deterministic operation) is the default here."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.models.common import RegenerationStrategy
from app.models.planning_state import (
    ExperiencePlan,
    FeedbackEvent,
    PlanningStage,
    PlanningState,
    RegenerationReadiness,
    TravelGroupType,
    TripRequest,
)
from app.repositories.factory import get_planning_state_repository
from app.services.regeneration_readiness_service import (
    CHANGE_APPLICATION_NOT_SUPPORTED,
    CHANGE_INTERPRETATION_UNAVAILABLE,
    apply_change_capability,
    regeneration_readiness_service,
)
from app.services.versioning_service import VersioningService


def _generated_state_with_feedback(feedback_type: str | None = "pace_change") -> PlanningState:
    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Testville, Testland",
            start_date="2026-08-10",
            end_date="2026-08-12",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
        )
    )
    state.experience_plan = ExperiencePlan()
    state = VersioningService().create_initial_version(state)
    state.feedback_history.append(
        FeedbackEvent(
            feedback_text="Make this less packed",
            feedback_type=feedback_type,
            affected_stages=[PlanningStage.EXPERIENCE_PLAN] if feedback_type else [],
            regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
        )
    )
    return state


def _enable_targeted(monkeypatch: pytest.MonkeyPatch, *, interpreter: bool) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "targeted_regeneration_enabled", True, raising=False)
    monkeypatch.setattr(settings, "ai_feedback_interpreter_enabled", interpreter, raising=False)
    monkeypatch.setattr(settings, "ai_feedback_interpreter_provider", "groq", raising=False)
    monkeypatch.setattr(settings, "groq_api_key", "test-key-not-real", raising=False)


# -- the production configuration ---------------------------------------------------------


def test_production_configuration_never_reports_ready() -> None:
    readiness = regeneration_readiness_service.recompute(_generated_state_with_feedback()).regeneration_readiness

    assert readiness.can_regenerate is False
    assert readiness.status == "blocked"
    assert readiness.change_requests_applicable is False
    assert readiness.change_requests_unavailable_reason == CHANGE_APPLICATION_NOT_SUPPORTED
    assert "regeneration_engine" in readiness.missing_capabilities
    assert "regeneration_engine" not in readiness.available_inputs
    assert readiness.blocked_by and "saved" in readiness.blocked_by[0]


def test_capability_is_stated_even_before_any_request_is_saved() -> None:
    state = _generated_state_with_feedback()
    state.feedback_history.clear()
    readiness = regeneration_readiness_service.recompute(state).regeneration_readiness
    assert readiness.change_requests_applicable is False


def test_a_registered_operation_makes_it_ready(synthetic_legacy_regeneration_support: None) -> None:
    readiness = regeneration_readiness_service.recompute(_generated_state_with_feedback()).regeneration_readiness
    assert readiness.status == "ready" and readiness.can_regenerate is True
    assert readiness.change_requests_applicable is True
    assert readiness.change_requests_unavailable_reason is None


def test_targeted_without_an_interpreter_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_targeted(monkeypatch, interpreter=False)
    readiness = regeneration_readiness_service.recompute(_generated_state_with_feedback()).regeneration_readiness
    assert readiness.can_regenerate is False
    assert readiness.change_requests_unavailable_reason == CHANGE_INTERPRETATION_UNAVAILABLE


def test_targeted_with_an_interpreter_needs_its_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_targeted(monkeypatch, interpreter=True)
    monkeypatch.setattr(get_settings(), "groq_api_key", None, raising=False)
    readiness = regeneration_readiness_service.recompute(_generated_state_with_feedback()).regeneration_readiness
    assert readiness.change_requests_unavailable_reason == CHANGE_INTERPRETATION_UNAVAILABLE


def test_targeted_with_an_interpreter_is_ready_even_for_unclassified_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The targeted path interprets free text itself; the route sends it there
    # before the legacy classification check, so readiness does too.
    _enable_targeted(monkeypatch, interpreter=True)
    readiness = regeneration_readiness_service.recompute(
        _generated_state_with_feedback(feedback_type=None)
    ).regeneration_readiness
    assert readiness.status == "ready" and readiness.can_regenerate is True
    assert readiness.change_requests_applicable is True


def test_locks_still_block_whatever_the_capability(synthetic_legacy_regeneration_support: None) -> None:
    from app.models.planning_state import UserLock

    state = _generated_state_with_feedback()
    state.user_locks.append(UserLock(locked_item_type="experience", locked_item_id="x"))
    readiness = regeneration_readiness_service.recompute(state).regeneration_readiness
    assert readiness.can_regenerate is False
    assert readiness.active_lock_count == 1


def test_overlay_turns_a_stale_ready_into_a_refusal() -> None:
    stale = RegenerationReadiness(
        status="ready",
        can_regenerate=True,
        available_inputs=["generated_plan", "pending_feedback", "regeneration_engine"],
        missing_capabilities=[],
        blocked_by=[],
        next_step="Call POST /trips/{trip_id}/regenerate with confirm=true.",
    )
    updated = apply_change_capability(stale, [])
    assert updated.can_regenerate is False and updated.status == "blocked"
    assert updated.change_requests_applicable is False
    assert "regeneration_engine" not in updated.available_inputs
    # the original object is untouched (the overlay is a copy)
    assert stale.can_regenerate is True


# -- over HTTP: readiness and the route agree ------------------------------------------------


def test_http_readiness_and_regenerate_agree(client: TestClient, generated_trip_id: str) -> None:
    assert client.post(f"/trips/{generated_trip_id}/feedback", json={"feedback_text": "Make this less packed"}).status_code == 200

    readiness = client.get(f"/trips/{generated_trip_id}/regeneration-readiness").json()["data"]["regeneration_readiness"]
    assert readiness["can_regenerate"] is False
    assert readiness["change_requests_applicable"] is False
    assert readiness["change_requests_unavailable_reason"] == CHANGE_APPLICATION_NOT_SUPPORTED

    # The action readiness does not offer is indeed refused, and changes nothing.
    response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})
    assert response.status_code >= 400
    assert response.json()["errors"][0]["code"] == "REGENERATION_FEEDBACK_NOT_INTERPRETABLE"


def test_http_readiness_overlays_a_stored_ready_without_saving(client: TestClient, generated_trip_id: str) -> None:
    client.post(f"/trips/{generated_trip_id}/feedback", json={"feedback_text": "Make this less packed"})
    repository = get_planning_state_repository()
    state = repository.get_by_trip_id(generated_trip_id)
    # A readiness stored as "ready" by an older build (no capability fields).
    state.regeneration_readiness = RegenerationReadiness(
        status="ready", can_regenerate=True, missing_capabilities=[], blocked_by=[], next_step="go"
    )
    repository.save(state)

    readiness = client.get(f"/trips/{generated_trip_id}/regeneration-readiness").json()["data"]["regeneration_readiness"]
    assert readiness["can_regenerate"] is False
    assert readiness["change_requests_applicable"] is False

    stored = repository.get_by_trip_id(generated_trip_id).regeneration_readiness
    assert stored.can_regenerate is True and stored.change_requests_applicable is None


def test_saving_a_request_still_works_when_applying_is_unavailable(client: TestClient, generated_trip_id: str) -> None:
    response = client.post(f"/trips/{generated_trip_id}/feedback", json={"feedback_text": "More museums please"})
    assert response.status_code == 200
    state = response.json()["data"]["planning_state"]
    assert state["feedback_history"][-1]["feedback_text"] == "More museums please"
    assert state["regeneration_readiness"]["change_requests_applicable"] is False
