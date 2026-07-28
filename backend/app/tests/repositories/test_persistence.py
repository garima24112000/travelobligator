from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.models.planning_state import PlanningStage, PlanningState, TripRequest
from app.repositories.planning_state_repository import PlanningStateRepository
from app.repositories.trip_repository import TripRepository
from app.storage.local_json_store import LocalJsonStore


def _trip_request() -> TripRequest:
    return TripRequest.model_validate(
        {
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        }
    )


def test_trip_repository_reloads_from_disk_in_a_fresh_instance(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path / "state.json")
    first = TripRepository(store=store)
    first.create("trip_abc")

    second = TripRepository(store=store)
    record = second.get("trip_abc")

    assert record is not None
    assert record.trip_id == "trip_abc"
    assert record.status == "draft"


def test_trip_repository_persists_status_updates_across_instances(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path / "state.json")
    first = TripRepository(store=store)
    first.create("trip_abc")
    first.update_status("trip_abc", "generating")

    second = TripRepository(store=store)
    assert second.get("trip_abc").status == "generating"


def test_planning_state_repository_reloads_from_disk_in_a_fresh_instance(
    tmp_path: Path,
) -> None:
    store = LocalJsonStore(tmp_path / "state.json")
    first = PlanningStateRepository(store=store)
    planning_state = PlanningState(trip_request=_trip_request())
    first.save(planning_state)

    second = PlanningStateRepository(store=store)
    reloaded = second.get_by_trip_id(planning_state.trip_id)

    assert reloaded is not None
    assert reloaded.trip_id == planning_state.trip_id
    assert reloaded.trip_request.primary_destination == "Testville, Testland"
    # Round-tripping through JSON must not fabricate any generated content
    # that was never set.
    assert reloaded.destination_context is None
    assert reloaded.experience_plan is None
    assert reloaded.validation_report is None


def test_trip_and_planning_state_repositories_can_share_one_file(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path / "state.json")
    trip_repo = TripRepository(store=store)
    planning_repo = PlanningStateRepository(store=store)

    planning_state = PlanningState(trip_request=_trip_request())
    trip_repo.create(planning_state.trip_id)
    planning_repo.save(planning_state)

    # A completely fresh pair of repository instances pointed at the same
    # file recovers both records, proving the two collections coexist in
    # the same JSON document without clobbering each other.
    fresh_trip_repo = TripRepository(store=store)
    fresh_planning_repo = PlanningStateRepository(store=store)

    assert fresh_trip_repo.get(planning_state.trip_id) is not None
    assert (
        fresh_planning_repo.get_by_trip_id(planning_state.trip_id).trip_id
        == planning_state.trip_id
    )


def test_generated_planning_state_is_persisted_and_reloadable(client: TestClient) -> None:
    """End-to-end: a trip created and generated through the API is
    recoverable by a brand-new repository instance pointed at the same
    file, simulating a backend process restart.
    """
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    # Import here (not at module scope) so we always read the store the
    # per-test conftest fixture bound the singletons to for *this* test.
    from app.repositories.planning_state_repository import (
        planning_state_repository as live_planning_state_repository,
    )
    from app.repositories.trip_repository import (
        trip_repository as live_trip_repository,
    )

    fresh_trip_repo = TripRepository(store=live_trip_repository._store)
    fresh_planning_repo = PlanningStateRepository(
        store=live_planning_state_repository._store
    )

    reloaded_trip = fresh_trip_repo.get(trip_id)
    reloaded_state = fresh_planning_repo.get_by_trip_id(trip_id)

    assert reloaded_trip is not None
    assert reloaded_state is not None
    assert reloaded_state.trip_id == trip_id
    assert reloaded_state.destination_context is not None
    assert reloaded_state.experience_plan is not None
    assert reloaded_state.validation_report is not None

    # No fake data was fabricated by the persistence round-trip: scheduled
    # experiences still only carry provider-backed fields, with no
    # invented times/ratings/prices layered on by (de)serialization.
    all_experiences = [
        experience
        for day_plan in reloaded_state.experience_plan.daily_plans
        for experience in day_plan.experiences
    ]
    assert len(all_experiences) > 0
    for experience in all_experiences:
        assert experience.start_time is None
        assert experience.end_time is None
        assert experience.estimated_duration_minutes is None


def test_data_sources_used_is_persisted_and_reloadable(client: TestClient) -> None:
    """Step 152: `data_sources_used` (derived incrementally by
    ProviderCoverageService.record_provider_result as each stage runs)
    survives a simulated backend process restart, the same as every other
    PlanningState field.
    """
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200
    data_sources_used_before_reload = generate_response.json()["data"]["planning_state"][
        "data_sources_used"
    ]
    # The test-double places provider (conftest.py) always succeeds, so this
    # reliably appears regardless of live network availability for the
    # other (weather/holiday/currency) providers.
    assert "openstreetmap_places" in data_sources_used_before_reload

    from app.repositories.planning_state_repository import (
        planning_state_repository as live_planning_state_repository,
    )

    fresh_planning_repo = PlanningStateRepository(store=live_planning_state_repository._store)
    reloaded_state = fresh_planning_repo.get_by_trip_id(trip_id)

    assert reloaded_state is not None
    assert reloaded_state.data_sources_used == data_sources_used_before_reload


def test_feedback_history_is_persisted_and_reloadable(client: TestClient) -> None:
    """End-to-end: feedback submitted through the API survives a simulated
    backend process restart, recoverable by a brand-new repository instance
    pointed at the same local JSON file.
    """
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    feedback_response = client.post(
        f"/trips/{trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    assert feedback_response.status_code == 200

    # Import here (not at module scope) so we always read the store the
    # per-test conftest fixture bound the singleton to for *this* test.
    from app.repositories.planning_state_repository import (
        planning_state_repository as live_planning_state_repository,
    )

    fresh_planning_repo = PlanningStateRepository(
        store=live_planning_state_repository._store
    )
    reloaded_state = fresh_planning_repo.get_by_trip_id(trip_id)

    assert reloaded_state is not None
    assert len(reloaded_state.feedback_history) == 1

    reloaded_feedback_event = reloaded_state.feedback_history[0]
    assert reloaded_feedback_event.feedback_text == "Make this less packed"

    # The deterministic rule-based interpretation (feedback_type,
    # affected_stages, and the `interpretation` dict) round-trips through
    # the JSON store instead of being lost or regenerated on reload.
    assert reloaded_feedback_event.feedback_type == "pace_change"
    assert reloaded_feedback_event.affected_stages == [
        PlanningStage.EXPERIENCE_PLAN,
        PlanningStage.VALIDATION,
    ]
    assert reloaded_feedback_event.interpretation is not None
    assert reloaded_feedback_event.interpretation["method"] == "deterministic_rule_based"
    assert reloaded_feedback_event.interpretation["applied_to_plan"] is False
    assert reloaded_feedback_event.interpretation["matched_labels"] == ["pace_change"]

    # The change_preview foundation (Step 121) also round-trips through the
    # JSON store instead of being lost or silently regenerated on reload.
    change_preview = reloaded_feedback_event.interpretation["change_preview"]
    assert change_preview["preview_status"] == "not_applied"
    assert change_preview["would_require_regeneration"] is True
    assert change_preview["likely_changes"] == [
        "Adjust daily pacing or number of scheduled experiences.",
        "Re-run experience planning before changing the itinerary.",
        "Re-run validation after any future itinerary change.",
    ]
    assert change_preview["unchanged_sections"] == [
        "traveler_profile",
        "destination_context",
        "trip_strategy",
        "stay_transport",
        "experience_plan",
        "validation_report",
        "provider_coverage",
        "route_feasibility_context",
    ]
    assert change_preview["blocked_by"] == [
        "Feedback regeneration is not implemented yet.",
        "No AI interpretation provider is connected.",
        "No plan sections are modified by the feedback capture endpoint.",
    ]


def test_pending_feedback_summary_is_persisted_and_reloadable(client: TestClient) -> None:
    """End-to-end: the plan-level pending feedback summary (Step 124)
    survives a simulated backend process restart, recoverable by a
    brand-new repository instance pointed at the same local JSON file.
    """
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    feedback_response = client.post(
        f"/trips/{trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    assert feedback_response.status_code == 200

    from app.repositories.planning_state_repository import (
        planning_state_repository as live_planning_state_repository,
    )

    fresh_planning_repo = PlanningStateRepository(
        store=live_planning_state_repository._store
    )
    reloaded_state = fresh_planning_repo.get_by_trip_id(trip_id)

    assert reloaded_state is not None
    summary = reloaded_state.pending_feedback_summary
    assert summary.status == "captured_not_applied"
    assert summary.total_feedback_items == 1
    assert summary.feedback_type_counts == {"pace_change": 1}
    assert summary.affected_stages == [
        PlanningStage.EXPERIENCE_PLAN,
        PlanningStage.VALIDATION,
    ]
    assert summary.requires_regeneration is True
    assert summary.latest_feedback_at is not None
    assert len(summary.summary_items) == 1
    assert summary.summary_items[0].feedback_type == "pace_change"
    assert summary.summary_items[0].example_feedback == "Make this less packed"


def test_user_lock_is_persisted_and_reloadable(client: TestClient) -> None:
    """End-to-end: a user lock created through the API survives a simulated
    backend process restart, recoverable by a brand-new repository instance
    pointed at the same local JSON file.
    """
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    lock_response = client.post(
        f"/trips/{trip_id}/locks",
        json={
            "locked_item_type": "experience",
            "locked_item_id": "experience_test_1",
            "reason": "user_requested_keep",
        },
    )
    assert lock_response.status_code == 201
    lock_id = lock_response.json()["data"]["planning_state"]["user_locks"][0]["lock_id"]

    delete_response = client.delete(f"/trips/{trip_id}/locks/{lock_id}")
    assert delete_response.status_code == 200

    from app.repositories.planning_state_repository import (
        planning_state_repository as live_planning_state_repository,
    )

    fresh_planning_repo = PlanningStateRepository(
        store=live_planning_state_repository._store
    )
    reloaded_state = fresh_planning_repo.get_by_trip_id(trip_id)

    assert reloaded_state is not None
    assert len(reloaded_state.user_locks) == 1

    reloaded_lock = reloaded_state.user_locks[0]
    assert reloaded_lock.lock_id == lock_id
    assert reloaded_lock.locked_item_type == "experience"
    assert reloaded_lock.locked_item_id == "experience_test_1"
    assert reloaded_lock.reason == "user_requested_keep"
    # The lock's deactivation (via DELETE) round-trips through the JSON
    # store instead of being lost or reset on reload.
    assert reloaded_lock.is_active is False
    assert reloaded_lock.removed_at is not None


def test_version_history_is_persisted_and_reloadable(client: TestClient) -> None:
    """End-to-end: the initial "v1" version item (Step 131) recorded by
    generating a trip survives a simulated backend process restart,
    recoverable by a brand-new repository instance pointed at the same
    local JSON file.
    """
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    from app.repositories.planning_state_repository import (
        planning_state_repository as live_planning_state_repository,
    )

    fresh_planning_repo = PlanningStateRepository(
        store=live_planning_state_repository._store
    )
    reloaded_state = fresh_planning_repo.get_by_trip_id(trip_id)

    assert reloaded_state is not None
    assert len(reloaded_state.version_history) == 1

    reloaded_version = reloaded_state.version_history[0]
    assert reloaded_version.version_label == "v1"
    assert reloaded_version.created_by == "system_generation"
    assert reloaded_version.feedback_event_id is None
    assert reloaded_version.preserved_sections == []
    assert set(reloaded_version.changed_sections) == {
        "traveler_profile",
        "destination_context",
        "trip_strategy",
        "stay_transport",
        "experience_plan",
        "validation_report",
        "provider_coverage",
    }
    assert reloaded_state.metadata.current_version == "v1"


def test_plan_diff_preview_is_persisted_and_reloadable(client: TestClient) -> None:
    """End-to-end: the plan diff preview (Step 132) recomputed after
    generating a trip and submitting feedback survives a simulated backend
    process restart, recoverable by a brand-new repository instance pointed
    at the same local JSON file.
    """
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    feedback_response = client.post(
        f"/trips/{trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    assert feedback_response.status_code == 200

    from app.repositories.planning_state_repository import (
        planning_state_repository as live_planning_state_repository,
    )

    fresh_planning_repo = PlanningStateRepository(
        store=live_planning_state_repository._store
    )
    reloaded_state = fresh_planning_repo.get_by_trip_id(trip_id)

    assert reloaded_state is not None
    preview = reloaded_state.plan_diff_preview
    assert preview.preview_status == "ready_for_future_regeneration_preview"
    assert preview.from_version == "v1"
    assert preview.would_create_version == "v2"
    assert preview.pending_feedback_count == 1
    assert preview.triggered_by_feedback_event_ids == [
        reloaded_state.feedback_history[0].feedback_event_id
    ]
    assert preview.regeneration_available is False
    assert preview.to_version is None


def test_regeneration_readiness_is_persisted_and_reloadable(client: TestClient) -> None:
    """End-to-end: the regeneration readiness gate (Step 135) recomputed
    after generating a trip and submitting feedback survives a simulated
    backend process restart, recoverable by a brand-new repository
    instance pointed at the same local JSON file.
    """
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    feedback_response = client.post(
        f"/trips/{trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    assert feedback_response.status_code == 200

    from app.repositories.planning_state_repository import (
        planning_state_repository as live_planning_state_repository,
    )

    fresh_planning_repo = PlanningStateRepository(
        store=live_planning_state_repository._store
    )
    reloaded_state = fresh_planning_repo.get_by_trip_id(trip_id)

    assert reloaded_state is not None
    readiness = reloaded_state.regeneration_readiness
    assert readiness.status == "blocked"
    assert readiness.can_regenerate is False
    assert readiness.current_version == "v1"
    assert readiness.would_create_version == "v2"
    assert readiness.pending_feedback_count == 1
    assert readiness.missing_capabilities == ["regeneration_engine"]


def test_regeneration_attempts_are_persisted_and_reloadable(client: TestClient) -> None:
    """End-to-end: the regeneration attempt audit trail (Step 142) recorded
    by calling POST /regenerate survives a simulated backend process
    restart, recoverable by a brand-new repository instance pointed at the
    same local JSON file.
    """
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    feedback_response = client.post(
        f"/trips/{trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    assert feedback_response.status_code == 200

    regenerate_response = client.post(f"/trips/{trip_id}/regenerate")
    assert regenerate_response.status_code == 409

    from app.repositories.planning_state_repository import (
        planning_state_repository as live_planning_state_repository,
    )

    fresh_planning_repo = PlanningStateRepository(
        store=live_planning_state_repository._store
    )
    reloaded_state = fresh_planning_repo.get_by_trip_id(trip_id)

    assert reloaded_state is not None
    assert len(reloaded_state.regeneration_attempts) == 1

    attempt = reloaded_state.regeneration_attempts[0]
    assert attempt.status == "blocked"
    assert attempt.current_version == "v1"
    assert attempt.would_create_version == "v2"
    assert attempt.pending_feedback_count == 1
    assert attempt.active_lock_count == 0
    assert attempt.reason_code == "REGENERATION_NOT_AVAILABLE"
    assert attempt.message == (
        "Feedback-driven regeneration is not available yet. The "
        "regeneration engine has not been implemented, so no plan "
        "changes were made."
    )

    # Everything else this step must not touch also round-trips unchanged.
    assert len(reloaded_state.version_history) == 1
    assert reloaded_state.metadata.current_version == "v1"
    assert len(reloaded_state.feedback_history) == 1


def test_get_unknown_trip_id_still_returns_404(client: TestClient) -> None:
    response = client.get("/trips/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["errors"][0]["code"] == "TRIP_NOT_FOUND"
