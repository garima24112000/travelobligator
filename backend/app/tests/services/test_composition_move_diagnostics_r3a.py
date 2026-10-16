from __future__ import annotations

import json
from typing import Any

import pytest

from app.core import generation_diagnostics
from app.services import day_composition as composition
from app.services import experience_planner_service as planner_module
from app.services.experience_planner_service import ExperiencePlannerService, _composition_move_records
from app.tests.services.test_batch1_tuning_fixes_3b import _scheduled
from app.tests.services.test_day_composition_q3 import _NATURE, _ai_state, _core, _place, _run, _stop

# Remediation R3a: every accepted day-composition move is recorded (kind,
# reason, provider ids, each one's tier rank and evidence band) for the
# evaluation tooling only. Diagnostics never change a plan. Synthetic fixtures;
# no city or landmark name.

_REMOTE_KM = 30.0


def _recorded(composed: composition.Composition, **fields: Any) -> dict[str, Any]:
    """The composition entry the recorder keeps for `composed` (the planner's
    own move translation, then the collector)."""
    recorder = generation_diagnostics.GenerationDiagnostics()
    with generation_diagnostics.activate(recorder):
        generation_diagnostics.composition(moves=_composition_move_records(composed), **fields)
    return recorder.snapshot()["composition"][0]


def _place_fact(key: str, tier: int, band: int) -> dict[str, Any]:
    return {"place_id": key, "tier_rank": tier, "evidence_band": band}


# -- what each reason records ----------------------------------------------------------------


def test_a_coverage_replacement_records_the_pair_with_tier_and_band() -> None:
    day = [_stop("h0", band=2, tier=4), _stop("h1", 0.3, band=2, tier=4), _stop("h2", 0.6, band=3, tier=4)]
    park = _stop("park", 1.0, band=0, tier=3, cls=_NATURE, interests=("outdoors",))
    composed = _run([day], [park], requested_interests=frozenset({"outdoors"}))
    assert composed.moves and composed.moves[0].reason == composition.REASON_COVERAGE

    move = _recorded(composed)["moves"][0]
    assert move["kind"] == "replace" and move["reason"] == "coverage" and "moved" not in move
    (pair,) = move["replaced"]
    outgoing_key = composed.moves[0].outgoing[0]
    stops = {stop.key: stop for stop in day}
    assert pair["outgoing"] == _place_fact(outgoing_key, stops[outgoing_key].tier_rank, stops[outgoing_key].evidence_band)
    # the lower-evidence place that came in is visible as such
    assert pair["incoming"] == _place_fact("park", 3, 0)


def test_a_geography_replacement_records_its_reason() -> None:
    day = [_stop("kept", must_visit=True), _stop("far", 2.0, band=2, tier=4)]
    composed = _run([day], [_stop("nearer", 1.0, band=1, tier=4)], per_day=2)

    move = _recorded(composed)["moves"][0]
    assert (move["kind"], move["reason"]) == ("replace", "geography")
    assert move["replaced"] == [{"outgoing": _place_fact("far", 4, 2), "incoming": _place_fact("nearer", 4, 1)}]


def test_a_rebuild_records_index_aligned_pairs() -> None:
    start = [[_stop("a0", band=2), _stop("b0", 20.0, band=3), _stop("c0", 40.0, band=1)]]
    composed = _run(start, [_stop("a1", 0.3, band=1), _stop("a2", 0.6, band=0)])
    assert [move.kind for move in composed.moves] == ["rebuild"]

    move = _recorded(composed)["moves"][0]
    recorded_pairs = [(pair["outgoing"]["place_id"], pair["incoming"]["place_id"]) for pair in move["replaced"]]
    assert recorded_pairs == list(zip(composed.moves[0].outgoing, composed.moves[0].incoming))
    assert {pair["outgoing"]["place_id"] for pair in move["replaced"]} == {"b0", "c0"}


def test_a_variety_regrouping_records_the_moved_stops_and_nothing_replaced() -> None:
    historic = [_stop(f"h{index}", 0.1 * index) for index in range(3)]
    parks = [_stop(f"p{index}", 0.3 + 0.1 * index, cls=_NATURE) for index in range(3)]
    composed = _run([historic, parks], justified=frozenset())
    assert composed.moves[0].reason == composition.REASON_VARIETY

    move = _recorded(composed)["moves"][0]
    assert move["kind"] in {"swap", "relocate"} and move["reason"] == "variety"
    assert "replaced" not in move
    assert [place["place_id"] for place in move["moved"]] == list(composed.moves[0].outgoing)
    assert all(place["tier_rank"] == 3 and place["evidence_band"] == 1 for place in move["moved"])


def test_must_visit_moves_record_an_insertion_and_a_replacement() -> None:
    missing = _stop("m", 0.4, must_visit=True, band=0, tier=4)
    added = _recorded(_run([[_stop("a0"), _stop("a1", 0.2)]], [missing]))["moves"][0]
    assert (added["kind"], added["reason"]) == ("add", "must_visit")
    assert added["replaced"] == [{"outgoing": None, "incoming": _place_fact("m", 4, 0)}]

    full = _run([[_stop("a0"), _stop("a1", 0.2), _stop("far", 20.0, band=2)]], [missing])
    replaced = _recorded(full)["moves"][0]
    assert (replaced["kind"], replaced["reason"]) == ("replace", "must_visit")
    assert replaced["replaced"] == [{"outgoing": _place_fact("far", 3, 2), "incoming": _place_fact("m", 4, 0)}]


# -- the collector: sanitising, bounds, isolation --------------------------------------------


def _collect(moves: Any) -> dict[str, Any]:
    recorder = generation_diagnostics.GenerationDiagnostics()
    with generation_diagnostics.activate(recorder):
        generation_diagnostics.composition(status="changed", accepted_moves=1, moves=moves)
    return recorder.snapshot()["composition"][0]


def test_labels_and_numbers_are_sanitised() -> None:
    entry = _collect(
        [
            {"kind": "replace", "reason": "has spaces!", "outgoing": [], "incoming": []},  # bad label
            {"kind": "x" * 65, "reason": "coverage"},  # overlong label
            {
                "kind": "replace",
                "reason": "coverage",
                "outgoing": [{"place_id": "geoapify/a", "tier_rank": True, "evidence_band": 2.0}],
                "incoming": [{"place_id": "geoapify/b", "tier_rank": "4", "evidence_band": None, "name": "Some Place"}],
                "note": "free text from anywhere",
            },
        ]
    )
    assert entry["moves_malformed"] == 2 and entry["moves_dropped"] == 0
    (move,) = entry["moves"]
    assert move == {
        "kind": "replace",
        "reason": "coverage",
        "replaced": [
            {
                "outgoing": {"place_id": "geoapify/a", "tier_rank": None, "evidence_band": None},
                "incoming": {"place_id": "geoapify/b", "tier_rank": None, "evidence_band": None},
            }
        ],
    }


def test_an_invalid_or_overlong_id_is_nulled_never_shortened() -> None:
    overlong = "geoapify/" + "x" * 300
    entry = _collect(
        [
            {
                "kind": "replace",
                "reason": "geography",
                "outgoing": [
                    {"place_id": overlong, "tier_rank": 4, "evidence_band": 1},
                    {"place_id": "", "tier_rank": 4, "evidence_band": 1},
                ],
                "incoming": [
                    {"place_id": 12345, "tier_rank": 3, "evidence_band": 0},
                    {"place_id": "geoapify/ok", "tier_rank": 3, "evidence_band": 0},
                ],
            }
        ]
    )
    pairs = entry["moves"][0]["replaced"]
    assert [pair["outgoing"]["place_id"] for pair in pairs] == [None, None]
    assert [pair["incoming"]["place_id"] for pair in pairs] == [None, "geoapify/ok"]
    payload = json.dumps(entry)
    assert "xxxxxxxxxx" not in payload and "12345" not in payload  # nothing derived from a bad id survives


def test_the_move_list_is_bounded_and_the_rest_counted_in_order() -> None:
    moves = [
        {"kind": "replace", "reason": "geography", "outgoing": [{"place_id": f"geoapify/o{index}"}], "incoming": []}
        for index in range(40)
    ]
    entry = _collect(moves)
    assert len(entry["moves"]) == generation_diagnostics._MAX_COMPOSITION_MOVES == 32
    assert entry["moves_dropped"] == 8 and entry["moves_malformed"] == 0
    assert [move["replaced"][0]["outgoing"]["place_id"] for move in entry["moves"]] == [
        f"geoapify/o{index}" for index in range(32)
    ]


def test_malformed_and_over_bound_moves_are_counted_apart() -> None:
    """Malformed moves are counted wherever they occur; the bound applies to
    well-formed moves only, in their original order."""
    valid = [
        {"kind": "rebuild", "reason": "geography", "outgoing": [{"place_id": f"geoapify/v{index}"}], "incoming": []}
        for index in range(35)
    ]
    mixed: list[Any] = [*valid[:10], None, *valid[10:33], {"kind": "bad label!"}, *valid[33:], 42]
    entry = _collect(mixed)

    assert entry["moves_malformed"] == 3
    assert len(entry["moves"]) == 32 and entry["moves_dropped"] == 3  # v32, v33, v34
    assert [move["replaced"][0]["outgoing"]["place_id"] for move in entry["moves"]] == [
        f"geoapify/v{index}" for index in range(32)
    ]
    assert len(entry["moves"]) + entry["moves_dropped"] + entry["moves_malformed"] == len(mixed)


@pytest.mark.parametrize("moves", [None, "not a list", 7, [None, 3, "x", [], {"kind": "replace"}]])
def test_malformed_move_metadata_is_dropped_never_raised(moves: Any) -> None:
    entry = _collect(moves)
    assert entry["moves"] == [] and entry["moves_dropped"] == 0
    assert entry["status"] == "changed" and entry["accepted_moves"] == 1  # the summary is kept


def test_the_summary_is_unchanged_when_no_moves_are_passed() -> None:
    recorder = generation_diagnostics.GenerationDiagnostics()
    with generation_diagnostics.activate(recorder):
        generation_diagnostics.composition(status="changed", accepted_moves=2, moves_by_kind={"swap": 2})
    entry = recorder.snapshot()["composition"][0]
    assert "moves" not in entry and entry["moves_by_kind"] == {"swap": 2}


def test_without_a_recorder_nothing_is_recorded_and_nothing_raises() -> None:
    assert generation_diagnostics.current() is None
    generation_diagnostics.composition(status="changed", moves=[{"kind": "replace", "reason": "coverage"}])
    assert generation_diagnostics.current() is None


# -- through the planner -----------------------------------------------------------------------


def _remote_state() -> Any:
    """The model put a remote place on day 1; composition replaces it."""
    pool = [*_core(8), _place("r0", _REMOTE_KM)]
    return _ai_state(pool, [["c0", "c1", "r0"], ["c2", "c3", "c4"]])


def _traced(state: Any) -> tuple[list[list[str]], dict[str, Any]]:
    recorder = generation_diagnostics.GenerationDiagnostics()
    with generation_diagnostics.activate(recorder):
        ExperiencePlannerService().run(state)
    return _scheduled(state), recorder.snapshot()


def test_a_planner_run_records_its_moves_without_changing_the_plan() -> None:
    plain = _remote_state()
    ExperiencePlannerService().run(plain)
    plan, trace = _traced(_remote_state())

    assert plan == _scheduled(plain)  # identical days with and without the recorder
    entry = trace["composition"][0]
    assert entry["accepted_moves"] == sum(entry["moves_by_kind"].values())
    assert entry["accepted_moves"] == len(entry["moves"]) + entry["moves_dropped"] + entry["moves_malformed"]
    outgoing = [
        pair["outgoing"]["place_id"] for move in entry["moves"] for pair in move.get("replaced", []) if pair["outgoing"]
    ]
    assert "geoapify/r0" in outgoing
    for move in entry["moves"]:
        assert move["reason"] in {"coverage", "geography", "variety", "must_visit"}
        for pair in move.get("replaced", []):
            for place in (pair["outgoing"], pair["incoming"]):
                if place is not None:
                    assert isinstance(place["tier_rank"], int) and isinstance(place["evidence_band"], int)
    # provider ids, fixed labels and numbers only: never a place name
    assert "Museum" not in json.dumps(trace["composition"])


def test_recorded_moves_are_deterministic() -> None:
    first = _traced(_remote_state())[1]["composition"]
    second = _traced(_remote_state())[1]["composition"]
    assert [entry["moves"] for entry in first] == [entry["moves"] for entry in second]


def test_malformed_diagnostic_metadata_never_changes_or_fails_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = _remote_state()
    ExperiencePlannerService().run(plain)
    expected = _scheduled(plain)

    real_compose = composition.compose

    class _Exploding(dict):
        def get(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("malformed diagnostic metadata")

    def compose_with_bad_facts(*args: Any, **kwargs: Any) -> composition.Composition:
        result = real_compose(*args, **kwargs)
        result.move_stop_facts = _Exploding()
        return result

    # 1. the planner-side translation raises internally: the move list is empty, the plan stands
    monkeypatch.setattr(planner_module.composition, "compose", compose_with_bad_facts)
    plan, trace = _traced(_remote_state())
    assert plan == expected
    assert trace["composition"][0]["moves"] == [] and trace["composition"][0]["accepted_moves"] >= 1
    monkeypatch.setattr(planner_module.composition, "compose", real_compose)

    # 2. the collector itself raises: the plan still stands
    def broken(_values: Any) -> Any:
        raise RuntimeError("collector failure")

    monkeypatch.setattr(generation_diagnostics, "_composition_moves", broken)
    plan, _ = _traced(_remote_state())
    assert plan == expected
