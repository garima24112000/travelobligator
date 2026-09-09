from __future__ import annotations

from datetime import date, datetime, timezone

from app.models.common import ProviderStatus
from app.models.planning_state import (
    DailyPlan,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    TravelGroupType,
    TripRequest,
)
from app.models.routing import (
    RouteFeasibilityReport,
    RouteFeasibilityStatus,
    RouteLegFeasibility,
)

# Backward-compatibility model tests for Section 172 (Step 172E, final
# Section 172 step) and Section 173 (Step 173A). `PlanningStateRepository.
# __init__` (backend/app/repositories/planning_state_repository.py) loads
# every persisted trip via `PlanningState.model_validate(record)` -- the
# exact call these tests exercise directly -- so a trip generated before
# Step 172A (no `day_number`/`stop_order`/`route_aware_provenance` on any
# `ExperienceItem`, no `route_aware_sequencing_report`/
# `route_feasibility_report`/`travel_time_buffer_report` on
# `PlanningState`) or before Step 173A (a `route_feasibility_report`/
# `travel_time_buffer_report` that exists, but whose legs/buffers have no
# `route_geometry` key at all) must still load without raising, with
# every new field honestly `None` rather than fabricated. This is what
# makes an old persisted trip "old-trip-safe" for both the backend and,
# since the frontend reads exactly these fields, for `frontend/lib/
# types.ts`'s nullable typing too (docs/16_frontend_architecture.md
# sections 39.18-39.21).


def _trip_request() -> TripRequest:
    return TripRequest(
        primary_destination="Testville, Testland",
        origin_city="Home City",
        start_date="2026-08-10",
        end_date="2026-08-12",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )


def test_experience_item_without_step_172a_fields_defaults_to_none() -> None:
    """Simulates one scheduled experience exactly as it would have been
    persisted before Step 172A -- no `day_number`/`stop_order`/
    `route_aware_provenance` key at all, not even `null`."""
    old_record = {
        "experience_id": "experience_old_1",
        "name": "Old Museum",
        "category": "museum",
    }

    experience = ExperienceItem.model_validate(old_record)

    assert experience.day_number is None
    assert experience.stop_order is None
    assert experience.route_aware_provenance is None
    # Step 170D fields, already optional before 172A, stay unaffected.
    assert experience.promoted_from_ai is False


def test_planning_state_without_step_166_172_reports_defaults_to_none() -> None:
    """Simulates a whole `PlanningState` persisted before Steps 166C/172A
    -- no `route_aware_sequencing_report`/`route_feasibility_report`/
    `travel_time_buffer_report` key at all."""
    trip_request = _trip_request()
    old_record = {
        "trip_id": "trip_old_1",
        "trip_request": trip_request.model_dump(mode="json"),
    }

    planning_state = PlanningState.model_validate(old_record)

    assert planning_state.route_aware_sequencing_report is None
    assert planning_state.route_feasibility_report is None
    assert planning_state.travel_time_buffer_report is None


def test_full_planning_state_round_trip_survives_stripped_step_172a_keys() -> None:
    """Builds a real, current `PlanningState` with a scheduled
    `experience_plan` (so `ExperienceItem.day_number`/`stop_order` are
    genuinely set), dumps it exactly like `PlanningStateRepository.save`
    does (`model_dump(mode="json")`), strips every Step 172A/166C key a
    pre-172A file would never have had, and confirms
    `PlanningState.model_validate` -- the exact call
    `PlanningStateRepository.__init__` makes when loading from disk --
    still succeeds, with every stripped field honestly `None` again
    rather than the loader crashing or guessing a replacement value.
    """
    trip_request = _trip_request()
    experience = ExperienceItem(
        experience_id="experience_old_2",
        name="Old Landmark",
        category="attraction",
        day_number=1,
        stop_order=1,
    )
    planning_state = PlanningState(
        trip_id="trip_old_2",
        trip_request=trip_request,
        experience_plan=ExperiencePlan(
            daily_plans=[
                DailyPlan(day_number=1, date=date(2026, 8, 10), experiences=[experience])
            ]
        ),
    )

    dumped = planning_state.model_dump(mode="json")
    stripped_experience = dumped["experience_plan"]["daily_plans"][0]["experiences"][0]
    del stripped_experience["day_number"]
    del stripped_experience["stop_order"]
    del stripped_experience["route_aware_provenance"]
    del dumped["route_aware_sequencing_report"]
    del dumped["route_feasibility_report"]
    del dumped["travel_time_buffer_report"]

    reloaded = PlanningState.model_validate(dumped)

    assert reloaded.route_aware_sequencing_report is None
    assert reloaded.route_feasibility_report is None
    assert reloaded.travel_time_buffer_report is None
    reloaded_experience = reloaded.experience_plan.daily_plans[0].experiences[0]
    assert reloaded_experience.day_number is None
    assert reloaded_experience.stop_order is None
    assert reloaded_experience.route_aware_provenance is None
    # Everything else on the experience survives the round trip unchanged.
    assert reloaded_experience.name == "Old Landmark"
    assert reloaded_experience.experience_id == "experience_old_2"


def test_planning_state_repository_style_load_of_mixed_old_and_new_trips(tmp_path) -> None:
    """End-to-end version of the two tests above, through the exact
    `PlanningStateRepository` load path (`LocalJsonStore` ->
    `PlanningState.model_validate`), with one old-style (missing keys)
    and one current-style trip in the same store -- mirroring a real
    local JSON file that accumulated trips across app versions.
    """
    from app.repositories.planning_state_repository import PlanningStateRepository
    from app.storage.local_json_store import LocalJsonStore

    trip_request = _trip_request()
    current_state = PlanningState(trip_id="trip_current", trip_request=trip_request)
    current_record = current_state.model_dump(mode="json")

    old_state_dump = PlanningState(
        trip_id="trip_old_3", trip_request=trip_request
    ).model_dump(mode="json")
    old_record = {
        key: value
        for key, value in old_state_dump.items()
        if key
        not in {
            "route_aware_sequencing_report",
            "route_feasibility_report",
            "travel_time_buffer_report",
        }
    }

    store_path = tmp_path / "planning_states.json"
    store = LocalJsonStore(store_path)
    store.write_collection(
        "planning_states",
        {"trip_current": current_record, "trip_old_3": old_record},
    )

    repository = PlanningStateRepository(store=store)

    assert repository.get_by_trip_id("trip_current") is not None
    old_loaded = repository.get_by_trip_id("trip_old_3")
    assert old_loaded is not None
    assert old_loaded.route_aware_sequencing_report is None
    assert old_loaded.route_feasibility_report is None
    assert old_loaded.travel_time_buffer_report is None


# ---------------------------------------------------------------------------
# Step 173A: a trip generated during Section 172 (after 166C/172A, but
# before route geometry existed at all) has a real
# route_feasibility_report/travel_time_buffer_report whose legs/buffers
# simply have no `route_geometry` key -- this must load exactly like any
# other backward-compatible field, defaulting to `None`.
# ---------------------------------------------------------------------------


def test_route_leg_feasibility_without_step_173a_geometry_key_defaults_to_none() -> None:
    old_leg_record = {
        "from_experience_id": "experience_a",
        "from_experience_name": "A",
        "to_experience_id": "experience_b",
        "to_experience_name": "B",
        "provider": "osrm",
        "status": ProviderStatus.SUCCESS.value,
        "distance_meters": 1500.0,
        "duration_seconds": 900.0,
        "feasibility_status": RouteFeasibilityStatus.FEASIBLE.value,
    }

    leg = RouteLegFeasibility.model_validate(old_leg_record)

    assert leg.route_geometry is None
    # Everything else on the leg survives untouched.
    assert leg.distance_meters == 1500.0


def test_planning_state_with_step_172_report_missing_step_173a_geometry_key() -> None:
    """Simulates a `PlanningState` generated at the very end of Section
    172 -- `route_feasibility_report` exists (Step 165E/172A) and has a
    real leg, but that leg dict has no `route_geometry` key at all
    (Step 173A didn't exist yet)."""
    trip_request = _trip_request()
    old_report_record = {
        "status": ProviderStatus.SUCCESS.value,
        "legs": [
            {
                "from_experience_id": "experience_a",
                "from_experience_name": "A",
                "to_experience_id": "experience_b",
                "to_experience_name": "B",
                "provider": "osrm",
                "status": ProviderStatus.SUCCESS.value,
                "distance_meters": 1500.0,
                "duration_seconds": 900.0,
                "feasibility_status": RouteFeasibilityStatus.FEASIBLE.value,
            }
        ],
        "provider": "osrm",
        "route_data_source": "osrm",
    }
    old_record = {
        "trip_id": "trip_old_4",
        "trip_request": trip_request.model_dump(mode="json"),
        "route_feasibility_report": old_report_record,
    }

    planning_state = PlanningState.model_validate(old_record)

    assert planning_state.route_feasibility_report is not None
    assert planning_state.route_feasibility_report.legs[0].route_geometry is None
    # Confirmed independently against the standalone model too.
    assert RouteFeasibilityReport.model_validate(old_report_record).legs[0].route_geometry is None


def test_planning_state_without_step_182f_narrative_report_defaults_to_none() -> None:
    """Simulates a `PlanningState` persisted before Step 182F -- no
    `itinerary_narrative_report` key at all, not even `null`. Must load
    without raising, with the new field honestly `None` rather than
    fabricated -- exactly what `PlanningStateRepository.__init__`'s
    `PlanningState.model_validate(record)` call must tolerate for every
    trip generated before this step."""
    trip_request = _trip_request()
    old_record = {
        "trip_id": "trip_old_182f",
        "trip_request": trip_request.model_dump(mode="json"),
    }

    planning_state = PlanningState.model_validate(old_record)

    assert planning_state.itinerary_narrative_report is None
