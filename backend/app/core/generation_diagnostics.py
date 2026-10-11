"""Generation-scoped planning diagnostics (quality tuning; inactive by default).

Answers two questions a stored plan cannot: WHICH planner pass put a stop on
the itinerary (or moved or removed it), and what the day-composition stage
did (ran or declined, how many moves, whether its search budget ran out).

Like `core/performance`, a recorder is bound to ONE generation by a context
variable -- never a process global -- and nothing in the application
activates one: the evaluation tooling (`backend/scripts/canary_city.py`) does.
With no recorder active every call here is a no-op, and every call is
guarded: a diagnostic can never fail or change a generation.

Diagnostic only. Nothing may read this module to decide anything about a
plan, nothing here is written to `PlanningState`, and nothing reaches the
traveller. It records provider place ids, fixed stage labels and numbers
only -- never a place name, a prompt, model output, a URL or a key. Bounded:
a fixed number of snapshots of a bounded size.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Sequence

# Bounds. A generation makes at most a few planner passes of about a dozen
# stages each; anything beyond these is dropped, never an error.
_MAX_SNAPSHOTS = 96
_MAX_DAYS = 31
_MAX_STOPS_PER_DAY = 16
_MAX_ID_LENGTH = 256
_MAX_COMPOSITIONS = 8
_MAX_ALLOWANCES = 16
_MAX_LABEL_LENGTH = 64
_MAX_COUNT_KEYS = 64
_MAX_INTEREST_ENTRIES = 16
_MAX_REJECTED_CANDIDATES = 3
_MAX_DISCOVERY_REQUESTS = 48
_MAX_DISCOVERY_IDS = 40
_MAX_ROUTE_CHECKPOINTS = 40
_MAX_ROUTE_PAIRS = 24
_MAX_REPAIR_ATTEMPTS = 8

# How one routing request was answered (fixed labels; counted, never read by a
# decision): a live request that took one of the day-route allowance, a live
# alternate-mode request, a live request the provider definitively refused,
# an answer served from legs already known / from a remembered definitive
# failure (no request, no allowance), and a request refused locally because
# an allowance was spent.
ROUTE_REQUEST_KINDS = (
    "live", "live_alternate_mode", "live_definitive_failure", "served_from_known_legs",
    "served_from_failure_memo", "refused_by_allowance", "alternate_mode_refused_by_allowance",
)

# Routing checkpoints of one planner pass (fixed labels), in the order they occur.
ROUTE_STAGE_BEGIN = "routes_begin"  # before the pass's first route report is built
ROUTE_STAGE_ORDER_FINAL = "order_final"  # routed, sequenced and mode-adapted; before the repairs
ROUTE_STAGE_ROUTABILITY_REPAIR = "routability_repair"
ROUTE_STAGE_BURDEN_REPAIR = "route_burden_repair"

# Planner-pass triggers (fixed labels).
PASS_INITIAL = "initial"
PASS_AFTER_AI_REPAIR = "after_ai_repair"
PASS_USEFULNESS_FALLBACK = "usefulness_fallback"

# Composition fields kept (numbers, booleans and fixed labels only).
_COMPOSITION_NUMBERS = (
    "accepted_moves", "evaluations", "working_set_size", "scheduled_stops", "unused_candidates",
    "dispersed_days_before", "dispersed_days_after", "extended_days_before", "extended_days_after",
)
_COMPOSITION_FLAGS = (
    "ran", "changed", "budget_exhausted", "used_ai_reasoning", "replacement_allowed", "regrouping_allowed",
    "active_user_lock", "improving_replacement_existed",
)
_ALLOWANCE_KEYS = (
    "route_requests_left", "alternate_mode_requests_left", "recomposition_requests_left",
    "credits_used", "credit_budget",
)


def _label(value: object) -> str | None:
    text = str(value) if isinstance(value, str) else None
    if not text or len(text) > _MAX_LABEL_LENGTH or not all(c.isalnum() or c in "_-." for c in text):
        return None
    return text


def _counts(values: object) -> dict[str, int]:
    """Labelled whole numbers only, at most `_MAX_COUNT_KEYS` of them."""
    if not isinstance(values, dict):
        return {}
    kept: dict[str, int] = {}
    for key in sorted(values, key=str):
        value = values[key]
        if len(kept) >= _MAX_COUNT_KEYS:
            break
        if _label(key) is not None and isinstance(value, int) and not isinstance(value, bool):
            kept[str(key)] = value
    return kept


def _days(days: Sequence[Sequence[object]]) -> list[list[str]]:
    return [
        [str(place_id)[:_MAX_ID_LENGTH] for place_id in list(day)[:_MAX_STOPS_PER_DAY] if place_id]
        for day in list(days)[:_MAX_DAYS]
    ]


class GenerationDiagnostics:
    """What the planner passes of ONE generation did to the schedule."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._passes = 0
        self._triggers: dict[int, str] = {}
        self._snapshots: list[dict[str, Any]] = []
        self._compositions: list[dict[str, Any]] = []
        self._allowances: list[dict[str, Any]] = []
        self._interest_coverage: list[dict[str, Any]] = []
        self._discovery: list[dict[str, Any]] = []
        self._route_requests: dict[str, int] = dict.fromkeys(ROUTE_REQUEST_KINDS, 0)
        self._route_checkpoints: list[dict[str, Any]] = []
        # name -> provider id of the stops routed before the last routability repair (never in a snapshot)
        self.stops_before_repair: dict[str, str] = {}
        self._dropped = 0

    def begin_planner_pass(self, trigger: str) -> None:
        with self._lock:
            self._passes += 1
            self._triggers[self._passes] = trigger

    def note_schedule(self, stage: str, days: Sequence[Sequence[object]]) -> None:
        with self._lock:
            if len(self._snapshots) >= _MAX_SNAPSHOTS:
                self._dropped += 1
                return
            self._snapshots.append({"pass": self._passes, "stage": stage, "days": _days(days)})

    def note_composition(self, fields: dict[str, Any]) -> None:
        entry: dict[str, Any] = {"pass": self._passes}
        for key in _COMPOSITION_FLAGS:
            if key in fields:
                entry[key] = bool(fields[key])
        for key in _COMPOSITION_NUMBERS:
            if isinstance(fields.get(key), (int, float)) and not isinstance(fields.get(key), bool):
                entry[key] = fields[key]
        for key in ("status", "declined"):
            entry[key] = _label(fields.get(key))
        moves = fields.get("moves_by_kind")
        if isinstance(moves, dict):
            entry["moves_by_kind"] = {
                kind: int(count) for kind, count in moves.items() if _label(kind) and isinstance(count, int)
            }
        # Aggregate counts of what the search could draw on and why moves were
        # not generated or accepted: fixed labels and whole numbers only.
        entry["counts"] = _counts(fields.get("counts"))
        with self._lock:
            if len(self._compositions) < _MAX_COMPOSITIONS:
                self._compositions.append(entry)
            else:
                self._dropped += 1

    def note_interest_coverage(self, fields: dict[str, Any]) -> None:
        """What the requested-interest coverage pass did for ONE uncovered
        interest: counts of candidates and of the reasons none could take a
        slot, and at most a few of the strongest candidates that were tried
        (provider id, tier rank, a straight-line distance, reason counts)."""
        interest, outcome = _label(fields.get("interest")), _label(fields.get("outcome"))
        if interest is None or outcome is None:
            return
        rejected: list[dict[str, Any]] = []
        for item in list(fields.get("strongest_rejected") or [])[:_MAX_REJECTED_CANDIDATES]:
            if not isinstance(item, dict) or not item.get("place_id"):
                continue
            distance = item.get("nearest_day_km")
            rejected.append(
                {
                    "place_id": str(item["place_id"])[:_MAX_ID_LENGTH],
                    "tier_rank": item.get("tier_rank") if isinstance(item.get("tier_rank"), int) else None,
                    "nearest_day_km": round(float(distance), 2) if isinstance(distance, (int, float)) else None,
                    "reasons": _counts(item.get("reasons")),
                }
            )
        entry = {
            "pass": self._passes,
            "interest": interest,
            "outcome": outcome,
            "counts": _counts(fields.get("counts")),
            "strongest_rejected": rejected,
        }
        with self._lock:
            if len(self._interest_coverage) < _MAX_INTEREST_ENTRIES:
                self._interest_coverage.append(entry)
            else:
                self._dropped += 1

    def note_discovery_request(self, fields: dict[str, Any]) -> None:
        """One Places discovery request: what it was for (`kind`), where it
        looked (`source`: broad, destination-local, must-visit-local or an
        interest group), its category group, how many places it asked for
        and returned, and the provider ids it returned."""
        kind, source, group = _label(fields.get("kind")), _label(fields.get("source")), _label(fields.get("group"))
        if kind is None or source is None or group is None:
            return
        entry = {
            "kind": kind,
            "source": source,
            "group": group,
            "requested": int(fields.get("requested") or 0),
            "offset": int(fields.get("offset") or 0),
            "returned": int(fields.get("returned") or 0),
            "failed": bool(fields.get("failed")),
            "place_ids": [str(place_id)[:_MAX_ID_LENGTH] for place_id in list(fields.get("place_ids") or [])[:_MAX_DISCOVERY_IDS]],
        }
        with self._lock:
            if len(self._discovery) < _MAX_DISCOVERY_REQUESTS:
                self._discovery.append(entry)
            else:
                self._dropped += 1

    def note_route_request(self, kind: str) -> None:
        with self._lock:
            if kind in self._route_requests:
                self._route_requests[kind] += 1

    def note_route_checkpoint(self, stage: str, fields: dict[str, Any]) -> None:
        """The routing state at one point of the current planner pass: the
        allowances left, the request counters SO FAR in this generation
        (a reader takes differences), how many required legs are verified /
        definitively failed / otherwise unverified, the failed legs as
        provider-id pairs, and -- after the routability repair -- what each
        of its attempts did."""
        entry: dict[str, Any] = {"stage": stage}
        for key in ("route_requests_left", "alternate_mode_requests_left", "recomposition_requests_left",
                    "required_legs", "verified_legs", "failed_legs", "unverified_legs"):
            value = fields.get(key)
            entry[key] = value if isinstance(value, int) and not isinstance(value, bool) else None
        entry["failed_pairs"] = [
            {
                "day": int(pair.get("day") or 0),
                "from": str(pair.get("from") or "")[:_MAX_ID_LENGTH],
                "to": str(pair.get("to") or "")[:_MAX_ID_LENGTH],
                "reason": _label(pair.get("reason")),
            }
            for pair in list(fields.get("failed_pairs") or [])[:_MAX_ROUTE_PAIRS]
            if isinstance(pair, dict)
        ]
        attempts = fields.get("repair_attempts")
        if attempts is not None:
            entry["repair_attempts"] = [
                {
                    "day": int(attempt.get("day") or 0),
                    "reason": _label(attempt.get("reason")),
                    "accepted": bool(attempt.get("accepted")),
                    "failed_legs_before": int(attempt.get("failed_legs_before") or 0),
                    "failed_legs_after": int(attempt.get("failed_legs_after") or 0),
                    "relocalized_legs": int(attempt.get("relocalized_legs") or 0),
                    "verification_attempts": int(attempt.get("verification_attempts") or 0),
                    "suspect_protection": _label(attempt.get("suspect_protection")),
                    "suspect_was_grounded_anchor": bool(attempt.get("suspect_was_grounded_anchor")),
                    "suspect_place_id": (str(attempt["suspect_place_id"])[:_MAX_ID_LENGTH] if attempt.get("suspect_place_id") else None),
                }
                for attempt in list(attempts)[:_MAX_REPAIR_ATTEMPTS]
                if isinstance(attempt, dict)
            ]
        with self._lock:
            if len(self._route_checkpoints) >= _MAX_ROUTE_CHECKPOINTS:
                self._dropped += 1
                return
            entry["pass"] = self._passes
            entry["requests_so_far"] = dict(self._route_requests)
            self._route_checkpoints.append(entry)

    def note_allowances(self, stage: str, values: dict[str, Any]) -> None:
        entry: dict[str, Any] = {"stage": stage}
        for key in _ALLOWANCE_KEYS:
            if isinstance(values.get(key), (int, float)) and not isinstance(values.get(key), bool):
                entry[key] = values[key]
        with self._lock:
            if len(self._allowances) < _MAX_ALLOWANCES:
                self._allowances.append(entry)
            else:
                self._dropped += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "planner_passes": [
                    {"pass": number, "trigger": trigger} for number, trigger in sorted(self._triggers.items())
                ],
                "schedule_snapshots": [
                    {"pass": item["pass"], "stage": item["stage"], "days": [list(day) for day in item["days"]]}
                    for item in self._snapshots
                ],
                "composition": [dict(entry) for entry in self._compositions],
                "allowances": [dict(entry) for entry in self._allowances],
                "interest_coverage": [
                    {**entry, "counts": dict(entry["counts"]), "strongest_rejected": [dict(i) for i in entry["strongest_rejected"]]}
                    for entry in self._interest_coverage
                ],
                "discovery_requests": [{**entry, "place_ids": list(entry["place_ids"])} for entry in self._discovery],
                "route_requests": dict(self._route_requests),
                "route_checkpoints": [
                    {
                        **entry,
                        "requests_so_far": dict(entry["requests_so_far"]),
                        "failed_pairs": [dict(pair) for pair in entry["failed_pairs"]],
                        **(
                            {"repair_attempts": [dict(attempt) for attempt in entry["repair_attempts"]]}
                            if "repair_attempts" in entry
                            else {}
                        ),
                    }
                    for entry in self._route_checkpoints
                ],
                "dropped_entries": self._dropped,
            }


_ACTIVE: ContextVar[GenerationDiagnostics | None] = ContextVar("generation_diagnostics_recorder", default=None)


def current() -> GenerationDiagnostics | None:
    return _ACTIVE.get()


@contextmanager
def activate(recorder: GenerationDiagnostics | None) -> Iterator[GenerationDiagnostics | None]:
    """Makes `recorder` the active one for the enclosed generation."""
    token = _ACTIVE.set(recorder)
    try:
        yield recorder
    finally:
        _ACTIVE.reset(token)


def begin_planner_pass(trigger: str) -> None:
    """A new run of the experience planner starts (see the `PASS_*` labels)."""
    try:
        recorder = _ACTIVE.get()
        if recorder is not None and _label(trigger) is not None:
            recorder.begin_planner_pass(trigger)
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


def schedule(stage: str, days: Sequence[Sequence[object]]) -> None:
    """The provider place ids on each day once `stage` has finished."""
    try:
        recorder = _ACTIVE.get()
        if recorder is not None and _label(stage) is not None:
            recorder.note_schedule(stage, days)
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


def schedule_from_state(stage: str, planning_state: Any) -> None:
    """`schedule` for the plan a stored state currently holds."""
    try:
        if _ACTIVE.get() is None:
            return
        plan = getattr(planning_state, "experience_plan", None)
        days = [
            [stop.provider_place_id for stop in day.experiences] for day in (plan.daily_plans if plan is not None else [])
        ]
        schedule(stage, days)
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


def composition(**fields: Any) -> None:
    """What the day-composition stage did in the current planner pass."""
    try:
        recorder = _ACTIVE.get()
        if recorder is not None:
            recorder.note_composition(fields)
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


def interest_coverage(**fields: Any) -> None:
    """What the interest-coverage pass did for one uncovered interest."""
    try:
        recorder = _ACTIVE.get()
        if recorder is not None:
            recorder.note_interest_coverage(fields)
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


def discovery_request(**fields: Any) -> None:
    """One Places discovery request and what it returned (ids and counts)."""
    try:
        recorder = _ACTIVE.get()
        if recorder is not None:
            recorder.note_discovery_request(fields)
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


def allowances(stage: str, provider_context: Any) -> None:
    """The routing allowances and credits left once `stage` has finished."""
    try:
        recorder = _ACTIVE.get()
        if recorder is None or provider_context is None or _label(stage) is None:
            return
        usage = provider_context.usage_tracker.snapshot()
        recorder.note_allowances(
            stage,
            {
                "route_requests_left": provider_context.route_requests_left,
                "alternate_mode_requests_left": provider_context.alternate_mode_requests_left,
                "recomposition_requests_left": provider_context.recomposition_requests_left,
                "credits_used": usage.get("credits_used"),
                "credit_budget": usage.get("budget"),
            },
        )
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


def route_request(kind: str) -> None:
    """How one routing request was answered (see `ROUTE_REQUEST_KINDS`)."""
    try:
        recorder = _ACTIVE.get()
        if recorder is not None:
            recorder.note_route_request(kind)
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


def route_checkpoint(stage: str, planning_state: Any, provider_context: Any) -> None:
    """The routing state of the stored plan once `stage` (a `ROUTE_STAGE_*`
    label) has finished. Reads the state and the context; asks no provider,
    takes no allowance and changes nothing."""
    try:
        recorder = _ACTIVE.get()
        if recorder is None or _label(stage) is None:
            return
        plan = getattr(planning_state, "experience_plan", None)
        report = getattr(planning_state, "route_feasibility_report", None)
        legs = {(leg.from_experience_id, leg.to_experience_id): leg for leg in (report.legs if report is not None else [])}
        required = verified = failed = 0
        failed_pairs: list[dict[str, Any]] = []
        place_id_by_name: dict[str, str] = {}
        for day in plan.daily_plans if plan is not None else []:
            for stop in day.experiences:
                place_id_by_name.setdefault(stop.name, stop.provider_place_id)
            for a, b in zip(day.experiences, day.experiences[1:]):
                required += 1
                leg = legs.get((a.experience_id, b.experience_id))
                status = getattr(getattr(leg, "status", None), "value", None)
                if status == "success" and leg.distance_meters is not None and leg.duration_seconds is not None:
                    verified += 1
                    continue
                failed += status == "failed"
                failed_pairs.append(
                    {
                        "day": day.day_number,
                        "from": a.provider_place_id,
                        "to": b.provider_place_id,
                        "reason": getattr(leg, "failure_reason", None) or (status or "no_leg"),
                    }
                )
        fields: dict[str, Any] = {
            "route_requests_left": getattr(provider_context, "route_requests_left", None),
            "alternate_mode_requests_left": getattr(provider_context, "alternate_mode_requests_left", None),
            "recomposition_requests_left": getattr(provider_context, "recomposition_requests_left", None),
            "required_legs": required,
            "verified_legs": verified,
            "failed_legs": failed,
            "unverified_legs": required - verified - failed,
            "failed_pairs": failed_pairs,
        }
        if stage == ROUTE_STAGE_ORDER_FINAL:
            # Remembered only to name a suspect the repair then takes off the plan.
            recorder.stops_before_repair = dict(place_id_by_name)
        if stage == ROUTE_STAGE_ROUTABILITY_REPAIR:
            place_id_by_name = {**recorder.stops_before_repair, **place_id_by_name}
            repair = getattr(planning_state, "routability_repair_report", None)
            fields["repair_attempts"] = [
                {
                    "day": attempt.day_number,
                    "reason": attempt.reason,
                    "accepted": attempt.accepted,
                    "failed_legs_before": attempt.failed_legs_before,
                    "failed_legs_after": attempt.failed_legs_after,
                    "relocalized_legs": attempt.relocalized_legs,
                    "verification_attempts": attempt.verification_attempts,
                    "suspect_protection": attempt.suspect_protection,
                    "suspect_was_grounded_anchor": attempt.suspect_was_grounded_anchor,
                    "suspect_place_id": place_id_by_name.get(attempt.suspect_place or ""),
                }
                for attempt in (repair.attempts if repair is not None else [])
            ]
        recorder.note_route_checkpoint(stage, fields)
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


# -- reading a trace (pure; used by the evaluation tooling and its tests) ---------------------------


def stop_history(trace: dict[str, Any]) -> dict[str, Any]:
    """Which stage put each FINAL stop on the plan, which stage last moved it
    to another day, and which stops a stage took off again.

    Read from the last planner pass and whatever ran after it: an earlier
    pass was superseded as a whole, so its stages explain nothing about the
    final plan. A stop's `introduced_by` is the first stage of its last
    uninterrupted presence -- the selection stage for a place chosen at the
    start of the pass, a later stage for one that was added afterwards."""
    snapshots = [item for item in trace.get("schedule_snapshots") or [] if isinstance(item, dict)]
    if not snapshots:
        return {"final_pass": None, "stops": {}, "removed": []}
    planner_passes = [item["pass"] for item in snapshots if item.get("pass")]
    final_pass = max(planner_passes) if planner_passes else 0
    start = next((index for index, item in enumerate(snapshots) if item.get("pass") == final_pass), 0)
    timeline = snapshots[start:]

    def day_of(item: dict[str, Any]) -> dict[str, int]:
        return {place_id: index for index, day in enumerate(item.get("days") or []) for place_id in day}

    positions = [day_of(item) for item in timeline]
    final = positions[-1]
    stops: dict[str, Any] = {}
    for place_id, final_day in final.items():
        introduced = len(timeline) - 1
        while introduced > 0 and place_id in positions[introduced - 1]:
            introduced -= 1
        moved_by = None
        for index in range(introduced + 1, len(timeline)):
            if positions[index].get(place_id) != positions[index - 1].get(place_id):
                moved_by = timeline[index]["stage"]
        stops[place_id] = {
            "introduced_by": timeline[introduced]["stage"],
            "last_moved_by": moved_by,
            "final_day_index": final_day,
        }
    removed: list[dict[str, Any]] = []
    for index in range(1, len(timeline)):
        for place_id in positions[index - 1]:
            if place_id not in positions[index] and place_id not in final:
                removed.append({"place_id": place_id, "removed_by": timeline[index]["stage"]})
    triggers = {item.get("pass"): item.get("trigger") for item in trace.get("planner_passes") or []}
    return {
        "final_pass": final_pass,
        "final_pass_trigger": triggers.get(final_pass),
        "stops": stops,
        "removed": removed,
    }


def route_passes(trace: dict[str, Any]) -> dict[str, Any]:
    """What each planner pass did with the generation's routing allowance.

    Per pass: the day-route allowance before and after it, the routing
    requests it made by phase (building the routes / the routability repair /
    the burden repair) and how each was answered, the legs verified, failed
    and unverified before and after the repairs, and the repair attempts.
    Across passes: whether a pass spent requests on a leg or a stop an
    earlier pass had already seen fail, whether an earlier pass ended more
    routable, and what the passes before the final one cost. Read from the
    checkpoints only; it reports and judges nothing."""
    checkpoints = trace.get("route_checkpoints") or []
    triggers = {item.get("pass"): item.get("trigger") for item in trace.get("planner_passes") or []}

    def delta(later: dict[str, Any] | None, earlier: dict[str, Any] | None) -> dict[str, int]:
        if later is None or earlier is None:
            return {}
        after, before = later.get("requests_so_far") or {}, earlier.get("requests_so_far") or {}
        return {kind: after.get(kind, 0) - before.get(kind, 0) for kind in ROUTE_REQUEST_KINDS if after.get(kind, 0) != before.get(kind, 0)}

    def legs(checkpoint: dict[str, Any] | None) -> dict[str, Any] | None:
        if checkpoint is None:
            return None
        return {key: checkpoint.get(key) for key in ("required_legs", "verified_legs", "failed_legs", "unverified_legs")}

    def coverage(checkpoint: dict[str, Any] | None) -> float | None:
        if checkpoint is None or not checkpoint.get("required_legs"):
            return None
        return round(100.0 * (checkpoint.get("verified_legs") or 0) / checkpoint["required_legs"], 1)

    passes: list[dict[str, Any]] = []
    seen_failed_pairs: set[tuple[str, str]] = set()
    seen_failed_places: set[str] = set()
    seen_suspects: set[str] = set()
    best_earlier_coverage: float | None = None
    for number in sorted({item.get("pass") for item in checkpoints}, key=lambda value: value or 0):
        own = [item for item in checkpoints if item.get("pass") == number]

        def first(stage: str) -> dict[str, Any] | None:
            return next((item for item in own if item.get("stage") == stage), None)

        def last(stage: str) -> dict[str, Any] | None:
            return next((item for item in reversed(own) if item.get("stage") == stage), None)

        begin, ordered = first(ROUTE_STAGE_BEGIN), last(ROUTE_STAGE_ORDER_FINAL)
        repaired, finished = last(ROUTE_STAGE_ROUTABILITY_REPAIR), last(ROUTE_STAGE_BURDEN_REPAIR)
        start, end = begin or own[0], own[-1]
        allowance_before, allowance_after = start.get("route_requests_left"), end.get("route_requests_left")
        initial_pairs = [(pair["from"], pair["to"]) for pair in (ordered or {}).get("failed_pairs") or []]
        initial_places = {place for pair in initial_pairs for place in pair}
        attempts = (repaired or {}).get("repair_attempts") or []
        suspects = {attempt["suspect_place_id"] for attempt in attempts if attempt.get("suspect_place_id")}
        final_coverage = coverage(end)
        exhausted = [attempt for attempt in attempts if attempt.get("reason") == "route_budget_exhausted"]
        passes.append(
            {
                "pass": number,
                "trigger": triggers.get(number),
                "route_requests_left_before": allowance_before,
                "route_requests_left_after": allowance_after,
                "route_requests_used": (
                    allowance_before - allowance_after
                    if isinstance(allowance_before, int) and isinstance(allowance_after, int)
                    else None
                ),
                "requests_building_routes": delta(ordered, begin),
                "requests_in_routability_repair": delta(repaired, ordered),
                "requests_in_route_burden_repair": delta(finished, repaired),
                "legs_before_repairs": legs(ordered),
                "legs_after_repairs": legs(end),
                "coverage_before_repairs": coverage(ordered),
                "coverage_after_repairs": final_coverage,
                "initial_failed_pairs": [list(pair) for pair in initial_pairs],
                "routability_repair_attempts": attempts,
                # Requests spent on something an EARLIER pass had already seen fail.
                "initial_failed_pairs_already_failed_in_an_earlier_pass": sum(pair in seen_failed_pairs for pair in initial_pairs),
                "initial_failed_places_already_failing_in_an_earlier_pass": sorted(initial_places & seen_failed_places),
                "suspects_already_identified_in_an_earlier_pass": sorted(suspects & seen_suspects),
                "an_earlier_pass_ended_more_routable": (
                    best_earlier_coverage is not None and final_coverage is not None and best_earlier_coverage > final_coverage
                ),
                "repair_allowance_exhausted": bool(exhausted),
                # For an exhausted repair: the failed legs it never got to ask for on their own.
                "failed_legs_left_unasked": sum(
                    max(0, attempt.get("failed_legs_before", 0) - attempt.get("relocalized_legs", 0)) for attempt in exhausted
                ),
            }
        )
        seen_failed_pairs |= set(initial_pairs)
        seen_failed_places |= initial_places
        seen_suspects |= suspects
        if final_coverage is not None:
            best_earlier_coverage = final_coverage if best_earlier_coverage is None else max(best_earlier_coverage, final_coverage)

    used = [item["route_requests_used"] for item in passes if isinstance(item["route_requests_used"], int)]
    return {
        "passes": passes,
        "route_requests_used_by_all_passes": sum(used),
        "route_requests_used_before_the_final_pass": sum(used[:-1]),
        "requests_by_answer": dict(trace.get("route_requests") or {}),
    }
