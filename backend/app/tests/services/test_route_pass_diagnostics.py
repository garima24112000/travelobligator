from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from app.core import generation_diagnostics
from app.core.config import get_settings
from app.tests.providers.test_geoapify_places_routing_203c2b import configured  # noqa: F401  (fixture)
from app.tests.services.test_final_quality_correction_203c2b import _state
from app.tests.services.test_route_failure_memo_lifecycle import _A, _B, _C, _D, _E, _NEAR, _X, _Lifecycle

# Routing diagnostics by planner pass (`generation_diagnostics.route_checkpoint` /
# `route_request` / `route_passes`). They record what each pass did with the generation's
# day-route allowance; they ask no provider, take no allowance and change no plan. The fixture
# runs the real Geoapify routing adapter over an in-process HTTP mock. Synthetic places only.

_X_ID, _NEAR_ID = _X["place_id"], _NEAR["place_id"]


@pytest.fixture(autouse=True)
def _scheduling_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTE_AWARE_SCHEDULING_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _replan(run: _Lifecycle, days: list[list[dict[str, Any]]], unused: tuple[dict[str, Any], ...]) -> None:
    """What a re-entered planner does when it comes back with DIFFERENT days."""
    run._planned = copy.deepcopy(_state([poi for day in days for poi in day] + list(unused), days).experience_plan)


def _two_passes(run: _Lifecycle, second_plan: list[list[dict[str, Any]]] | None, recorder: Any = None) -> dict[str, Any]:
    with generation_diagnostics.activate(recorder):
        generation_diagnostics.begin_planner_pass(generation_diagnostics.PASS_INITIAL)
        run.run_pass()
        first = run.snapshot()
        if second_plan is not None:
            _replan(run, second_plan, (_NEAR,))
        generation_diagnostics.begin_planner_pass(generation_diagnostics.PASS_AFTER_AI_REPAIR)
        run.run_pass()
    return {"first": first, "second": run.snapshot(), "requests": run.requests, "left": run.context.route_requests_left}


_FIRST_PLAN = [[_A, _B, _C], [_D, _X, _E]]
# The planner comes back with the unroutable place on a day in another order: a different
# request, so nothing the first pass learned about the WHOLE day answers it.
_SECOND_PLAN = [[_A, _B, _C], [_E, _X, _D]]


@pytest.mark.parametrize("engine", ["graph", "legacy"])
def test_a_second_pass_that_meets_the_same_unroutable_place_is_reported(
    engine: str, monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path  # noqa: F811
) -> None:
    recorder = generation_diagnostics.GenerationDiagnostics()
    run = _Lifecycle(monkeypatch, tmp_path, engine, _FIRST_PLAN, unused=(_NEAR,))
    allowance = run.context.route_requests_left
    outcome = _two_passes(run, _SECOND_PLAN, recorder)
    trace = recorder.snapshot()
    summary = generation_diagnostics.route_passes(trace)
    first, second = summary["passes"]

    # pass 1: two day routes, the two failed legs asked one at a time, one verification
    assert (first["pass"], first["trigger"]) == (1, "initial") and first["route_requests_left_before"] == allowance
    built = first["requests_building_routes"]
    # (the sequencing step asks again for what feasibility just learned: answered from memory, free)
    assert (built["live"], built["live_definitive_failure"]) == (2, 1) and "refused_by_allowance" not in built
    assert first["requests_in_routability_repair"]["live"] == 3
    assert first["legs_before_repairs"] == {"required_legs": 4, "verified_legs": 2, "failed_legs": 2, "unverified_legs": 0}
    assert first["coverage_before_repairs"] == 50.0 and first["coverage_after_repairs"] == 100.0
    [attempt] = first["routability_repair_attempts"]
    assert (attempt["reason"], attempt["accepted"], attempt["relocalized_legs"], attempt["verification_attempts"]) == ("accepted", True, 2, 1)
    assert not first["repair_allowance_exhausted"] and not first["an_earlier_pass_ended_more_routable"]
    assert first["initial_failed_places_already_failing_in_an_earlier_pass"] == []

    # pass 2: the same place fails again; everything it spends is on something already seen failing
    assert (second["pass"], second["trigger"]) == (2, "after_ai_repair")
    assert second["route_requests_left_before"] == first["route_requests_left_after"]
    assert second["requests_building_routes"].get("served_from_known_legs", 0) >= 1  # day 1, free
    assert second["requests_building_routes"].get("live") == 1  # day 2 in its new order
    assert _X_ID in second["initial_failed_places_already_failing_in_an_earlier_pass"]
    # the first pass had already singled this place out and replaced it; the second proved it again
    assert attempt["suspect_place_id"] == _X_ID and second["suspects_already_identified_in_an_earlier_pass"] == [_X_ID]
    assert second["requests_in_routability_repair"]["live_definitive_failure"] == 2
    assert second["repair_allowance_exhausted"] and second["an_earlier_pass_ended_more_routable"] is True
    assert second["route_requests_used"] == first["route_requests_left_after"] - outcome["left"] > 0
    assert summary["route_requests_used_before_the_final_pass"] == first["route_requests_used"]
    assert summary["route_requests_used_by_all_passes"] == allowance - outcome["left"]
    # every live request the recorder counted reached the provider, and none it did not
    answered = summary["requests_by_answer"]
    assert answered["live"] + answered["live_alternate_mode"] == len(outcome["requests"])
    assert answered["refused_by_allowance"] == 0 or second["repair_allowance_exhausted"]


def test_an_exhausted_repair_is_explained_and_an_earlier_better_pass_is_flagged(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path  # noqa: F811
) -> None:
    recorder = generation_diagnostics.GenerationDiagnostics()
    run = _Lifecycle(monkeypatch, tmp_path, "graph", _FIRST_PLAN, unused=(_NEAR,))
    with generation_diagnostics.activate(recorder):
        generation_diagnostics.begin_planner_pass(generation_diagnostics.PASS_INITIAL)
        run.run_pass()
        assert run.snapshot()["repair"] == [(2, "accepted", True, 1)]
        _replan(run, _SECOND_PLAN, (_NEAR,))
        run.context.route_requests_left = 2  # what a longer first pass would have left
        generation_diagnostics.begin_planner_pass(generation_diagnostics.PASS_AFTER_AI_REPAIR)
        run.run_pass()
    assert run.snapshot()["repair"][0][1] == "route_budget_exhausted"
    second = generation_diagnostics.route_passes(recorder.snapshot())["passes"][1]
    assert second["repair_allowance_exhausted"] and second["failed_legs_left_unasked"] == 1
    assert second["route_requests_left_before"] == 2 and second["route_requests_left_after"] == 0
    assert second["requests_building_routes"]["live"] == 1 and second["requests_in_routability_repair"]["live"] == 1
    assert second["coverage_after_repairs"] < 100.0 and second["an_earlier_pass_ended_more_routable"] is True
    [attempt] = second["routability_repair_attempts"]
    assert attempt["reason"] == "route_budget_exhausted" and attempt["relocalized_legs"] == 1


@pytest.mark.parametrize("engine", ["graph", "legacy"])
def test_recording_changes_neither_the_plan_nor_the_requests_nor_the_allowance(
    engine: str, monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path  # noqa: F811
) -> None:
    plain = _two_passes(_Lifecycle(monkeypatch, tmp_path, engine, _FIRST_PLAN, unused=(_NEAR,)), _SECOND_PLAN)
    recorded = _two_passes(
        _Lifecycle(monkeypatch, tmp_path, engine, _FIRST_PLAN, unused=(_NEAR,)),
        _SECOND_PLAN,
        generation_diagnostics.GenerationDiagnostics(),
    )
    assert recorded == plain
    assert generation_diagnostics.current() is None


def test_an_identical_second_pass_costs_nothing_and_says_so(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path  # noqa: F811
) -> None:
    recorder = generation_diagnostics.GenerationDiagnostics()
    run = _Lifecycle(monkeypatch, tmp_path, "graph", _FIRST_PLAN, unused=(_NEAR,))
    _two_passes(run, None, recorder)
    second = generation_diagnostics.route_passes(recorder.snapshot())["passes"][1]
    assert second["route_requests_used"] == 0
    assert "live" not in second["requests_building_routes"] and "live" not in second["requests_in_routability_repair"]
    assert second["requests_building_routes"].get("served_from_failure_memo", 0) >= 1


def test_the_recorder_is_bounded_and_keeps_fixed_labels_ids_and_numbers_only() -> None:
    recorder = generation_diagnostics.GenerationDiagnostics()
    with generation_diagnostics.activate(recorder):
        generation_diagnostics.route_request("live")
        generation_diagnostics.route_request("not a known kind")
        generation_diagnostics.route_checkpoint("a stage with spaces", None, None)  # not a fixed label: dropped
        for _ in range(60):
            generation_diagnostics.route_checkpoint(generation_diagnostics.ROUTE_STAGE_BEGIN, None, None)
        generation_diagnostics.route_checkpoint(generation_diagnostics.ROUTE_STAGE_BEGIN, object(), object())  # never raises
    trace = recorder.snapshot()
    assert trace["route_requests"]["live"] == 1 and set(trace["route_requests"]) == set(generation_diagnostics.ROUTE_REQUEST_KINDS)
    assert len(trace["route_checkpoints"]) == 40 and trace["dropped_entries"] >= 20
    assert all(set(item["requests_so_far"]) == set(generation_diagnostics.ROUTE_REQUEST_KINDS) for item in trace["route_checkpoints"])
    # with no recorder active every call is a no-op
    generation_diagnostics.route_request("live")
    generation_diagnostics.route_checkpoint(generation_diagnostics.ROUTE_STAGE_BEGIN, None, None)
    assert generation_diagnostics.route_passes({}) == {
        "passes": [], "route_requests_used_by_all_passes": 0, "route_requests_used_before_the_final_pass": 0, "requests_by_answer": {},
    }
