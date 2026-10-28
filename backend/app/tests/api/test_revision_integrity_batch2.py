from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.repositories.factory import get_planning_state_repository
from app.services.generation_job_service import has_usable_generated_plan
from app.services.revision_lineage_service import revision_lineage_service
from app.services.revision_snapshot_service import build_workspace_consistency_projection

# Final Batch 2: no user action may change a trip's saved itinerary content without the revision that
# records it. `check_branch_head_consistency` compares the saved workspace with its active branch's head
# revision (feedback, locks, readiness and progress are excluded by design), so it must stay True after
# every action a user can take on a generated trip.
#
# Both defects below were first reproduced by these tests failing with `content_drift`:
#   * the Developer-view "Refresh AI promotion report" (POST /ai-candidate-promotions) rewrote
#     `ai_candidate_promotion_report` -- revision content -- with a fresh timestamp and no revision;
#   * POST /generate on a trip that already had a plan rebuilt it under the unchanged "v1" label, whose
#     snapshot is (by design) never re-captured.


def _state(trip_id: str):
    state = get_planning_state_repository().get_by_trip_id(trip_id)
    assert state is not None
    return state


def _consistent(trip_id: str) -> bool:
    return revision_lineage_service.check_branch_head_consistency(_state(trip_id))


def _production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()


def test_a_generated_trip_matches_its_revision(client: TestClient, generated_trip_id: str) -> None:
    assert _consistent(generated_trip_id)


# -- the AI promotion refresh is development-only ---------------------------------------------------


def test_the_promotion_refresh_does_not_exist_in_production(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = build_workspace_consistency_projection(_state(generated_trip_id))
    _production(monkeypatch)
    visitor = TestClient(app, raise_server_exceptions=False)
    for caller in (client, visitor):
        for target in (generated_trip_id, "trip_that_does_not_exist"):
            response = caller.post(f"/trips/{target}/ai-candidate-promotions")
            # the same answer as an unknown path, before the session or the trip is looked at
            assert (response.status_code, response.json()) == (404, {"detail": "Not Found"})
    assert build_workspace_consistency_projection(_state(generated_trip_id)) == before
    assert _consistent(generated_trip_id)


def test_the_review_tells_the_client_whether_the_refresh_exists(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    development = client.get(f"/trips/{generated_trip_id}/ai-candidate-review").json()["data"]
    assert development["promotion_refresh_available"] is True
    _production(monkeypatch)
    production = client.get(f"/trips/{generated_trip_id}/ai-candidate-review").json()["data"]
    assert production["promotion_refresh_available"] is False
    # the read-only review itself is unchanged
    assert production["ai_candidate_review_report"]["status"] == development["ai_candidate_review_report"]["status"]


def test_the_promotion_route_is_guarded_by_the_development_only_dependency() -> None:
    route = next(r for r in app.routes if getattr(r, "path", "").endswith("/ai-candidate-promotions"))
    assert [dependency.call.__name__ for dependency in route.dependant.dependencies][0] == "_development_only"


# -- POST /generate builds the FIRST plan only ---------------------------------------------------------


def test_generating_a_trip_that_already_has_a_plan_is_refused_and_changes_nothing(
    client: TestClient, generated_trip_id: str
) -> None:
    before = build_workspace_consistency_projection(_state(generated_trip_id))
    versions_before = [item.version_label for item in _state(generated_trip_id).version_history]

    response = client.post(f"/trips/{generated_trip_id}/generate")

    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False and body["errors"][0]["code"] == "PLAN_ALREADY_GENERATED"
    assert "Nothing was changed" in body["message"]
    assert build_workspace_consistency_projection(_state(generated_trip_id)) == before
    assert [item.version_label for item in _state(generated_trip_id).version_history] == versions_before
    assert _consistent(generated_trip_id)


def test_in_job_mode_the_refusal_admits_no_job(
    client: TestClient, generated_trip_id: str, async_generation_enabled: None
) -> None:
    jobs_before = client.get(f"/trips/{generated_trip_id}/jobs").json()["data"]["jobs"]
    response = client.post(f"/trips/{generated_trip_id}/generate")
    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "PLAN_ALREADY_GENERATED"
    assert client.get(f"/trips/{generated_trip_id}/jobs").json()["data"]["jobs"] == jobs_before


def test_a_first_generation_in_job_mode_still_runs(
    client: TestClient, created_trip_id: str, async_generation_enabled: None
) -> None:
    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 202


def test_a_generation_that_could_not_resolve_its_destination_can_be_tried_again(
    client: TestClient, generated_trip_id: str
) -> None:
    # e.g. a transient geocoder failure: the finished state holds v1 but no usable destination
    state = _state(generated_trip_id)
    state.destination_context.destination_resolution = "unresolved"
    get_planning_state_repository().save(state)
    assert has_usable_generated_plan(_state(generated_trip_id)) is False
    response = client.post(f"/trips/{generated_trip_id}/generate")
    assert response.status_code == 200  # rebuilt (the offline destination resolves this time)
    assert has_usable_generated_plan(_state(generated_trip_id)) is True


def test_a_trip_without_a_usable_plan_is_not_treated_as_generated(
    client: TestClient, generated_trip_id: str
) -> None:
    from app.tests.conftest import create_trip_payload

    never_generated = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    assert has_usable_generated_plan(_state(never_generated)) is False
    assert has_usable_generated_plan(_state(generated_trip_id)) is True
    for missing in ("experience_plan", "validation_report", "destination_context"):
        state = _state(generated_trip_id)
        setattr(state, missing, None)
        assert has_usable_generated_plan(state) is False, missing
    state = _state(generated_trip_id)
    state.version_history = []
    assert has_usable_generated_plan(state) is False


def test_regeneration_keeps_its_own_refusals(client: TestClient, generated_trip_id: str) -> None:
    """The supported way to change a plan is untouched: it answers with its own, named refusal."""
    response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})
    assert response.status_code != 200
    assert response.json()["errors"][0]["code"] != "PLAN_ALREADY_GENERATED"
