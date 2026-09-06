from __future__ import annotations

from typing import Any

from app.graphs.planning_graph_state import PlanningGraphState, build_initial_planning_graph_state
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest

# Tests for the LangGraph planning state schema (Step 171A). This module
# only builds a plain TypedDict from already-existing trip_id/TripRequest/
# PlanningState values -- no provider/LLM/network call, no persistence, no
# invented trip data.


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "New York",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
    }
    fields.update(overrides)
    return TripRequest(**fields)


# ---------------------------------------------------------------------------
# 1. Graph state can be created from trip_id, TripRequest, PlanningState.
# ---------------------------------------------------------------------------


def test_build_initial_state_from_trip_id_trip_request_planning_state() -> None:
    trip_request = _trip_request()
    planning_state = PlanningState(trip_request=trip_request)

    state = build_initial_planning_graph_state(
        planning_state.trip_id, trip_request, planning_state
    )

    assert state["trip_id"] == planning_state.trip_id
    assert state["trip_request"] is trip_request
    assert state["planning_state"] is planning_state


def test_build_initial_state_never_invents_trip_id() -> None:
    trip_request = _trip_request()
    planning_state = PlanningState(trip_request=trip_request)

    state = build_initial_planning_graph_state("trip_explicit_id", trip_request, planning_state)

    # The caller's own trip_id is used verbatim -- never replaced by
    # planning_state.trip_id or any other derived/invented value.
    assert state["trip_id"] == "trip_explicit_id"


# ---------------------------------------------------------------------------
# 2. Graph state starts with empty errors/warnings/completed_nodes/failed_nodes.
# ---------------------------------------------------------------------------


def test_initial_state_has_empty_bookkeeping_lists() -> None:
    trip_request = _trip_request()
    planning_state = PlanningState(trip_request=trip_request)

    state = build_initial_planning_graph_state(
        planning_state.trip_id, trip_request, planning_state
    )

    assert state["errors"] == []
    assert state["warnings"] == []
    assert state["completed_nodes"] == []
    assert state["failed_nodes"] == []


def test_planning_graph_state_typed_dict_has_expected_keys() -> None:
    assert set(PlanningGraphState.__annotations__.keys()) == {
        "trip_id",
        "trip_request",
        "planning_state",
        "errors",
        "warnings",
        "completed_nodes",
        "failed_nodes",
    }
