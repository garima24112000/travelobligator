"""Phase Q0 itinerary-quality metrics (benchmark / canary reporting only).

Reads ONE stored `PlanningState` and reports the generic quality figures of
`docs/24_itinerary_quality_contract.md` that can be computed honestly from
what the pipeline already stored: requested-interest coverage against the
verified supply, must-visit identity, per-day geographic spread, route
burden, limited-slot use and day composition.

Benchmark tooling only: nothing here is imported by `backend/app`, no city or
place is known to it, and it decides nothing -- no figure here is an
acceptance input of the planner. It makes no provider, routing or model call
and never changes the state. Every measure and boundary is the pipeline's own
(`day_spread_km`, `day_route_burdens`, `interest_coverage`,
`resolve_must_visits`, the candidate quality tiers); nothing is re-defined
here. A tier is the pipeline's internal pre-ranking tier -- never a
popularity, a rating or a tourist ranking.
"""

from __future__ import annotations

from statistics import median
from typing import Any

SCHEMA_VERSION = 1

# Contract metrics that CANNOT be computed from stored state yet. They are
# named so a report never implies they were measured; none is estimated.
FUTURE_METRICS: tuple[str, ...] = (
    "same_excursion_or_complex_redundancy",
    "tourist_value_ranking_and_obscure_filler_share",
    "repeated_crossing_between_distant_clusters",
    "recognisable_day_purpose",
    "lower_burden_alternative_existed",
    "first_and_last_day_fit",
)

# Best first. The pipeline's own tiers (`CandidateQualityTier` values).
_TIER_ORDER: tuple[str, ...] = ("primary_anchor", "good_candidate", "secondary_candidate", "low_priority", "rejected")
_UNKNOWN_TIER = "unknown"


def _value(item: Any) -> Any:
    return getattr(item, "value", item)


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


def extract_quality_metrics(state: Any) -> dict[str, Any]:
    """The itinerary-quality figures of one stored planning state."""
    from app.services import place_taxonomy as taxonomy
    from app.services import schedule_diversity as diversity
    from app.services.day_order_heuristics import GEOGRAPHIC_SPREAD_THRESHOLD_KM, day_spread_km, misplaced_stops
    from app.services.entity_collisions import scheduled_unresolved_collisions
    from app.services.grounded_anchors import grounded_anchor_place_ids
    from app.services.interest_coverage import interest_coverage
    from app.services.must_visit_matching import resolve_must_visits
    from app.services.route_burden import LONG_TRAVEL_DAY, day_route_burdens, max_day_seconds
    from app.services.pace_targets import pace_of, pace_targets_for

    # the usefulness contract's own definition of a viable candidate (never a second one)
    from app.services.usefulness_contract import _VIABLE_TIERS, evaluate_usefulness

    plan = state.experience_plan
    days = list(plan.daily_plans) if plan is not None else []
    scheduled = [stop for day in days for stop in day.experiences]
    scheduled_ids = {stop.provider_place_id for stop in scheduled if stop.provider_place_id}
    verdict = evaluate_usefulness(state)
    targets = pace_targets_for(state)
    viable_at_least_t = verdict.viable >= targets.target_stops

    # -- candidate pool (the stored quality report; one entry per candidate) --------------------
    quality = state.candidate_quality_report
    scores: dict[str, Any] = {}
    for score in (*quality.attraction_scores, *quality.ai_directed_scores) if quality is not None else ():
        scores.setdefault(score.candidate_id, score)
    viable = [
        score for score in scores.values() if score.quality_tier in _VIABLE_TIERS and not score.low_value_object
    ]

    # -- requested interests ------------------------------------------------------------------
    profile = state.traveler_profile
    terms = list(profile.interests if profile else state.trip_request.interests)
    coverage = interest_coverage(state)
    supply = {
        interest: sum(1 for score in viable if interest in score.matched_interests) for interest in coverage
    }
    unused_supply = {
        interest: sum(
            1 for score in viable if interest in score.matched_interests and score.candidate_id not in scheduled_ids
        )
        for interest in coverage
    }
    uncovered = sorted(interest for interest, covered in coverage.items() if not covered)
    interests = {
        "requested_terms": terms,
        "canonical": list(coverage),
        # a term the taxonomy maps to nothing is not tracked at all (so it is never "uncovered")
        "unrecognised_terms": [term for term in terms if not taxonomy.canonical_interests([term])],
        "covered": sorted(interest for interest, covered in coverage.items() if covered),
        "uncovered": uncovered,
        "viable_supply_by_interest": supply,
        "unused_viable_supply_by_interest": unused_supply,
        # supply unavailable vs. the planner not using available supply
        "uncovered_without_supply": [interest for interest in uncovered if supply[interest] == 0],
        "uncovered_with_supply": [interest for interest in uncovered if supply[interest] > 0],
    }

    # -- must-visits (the validator's own identity resolution) -----------------------------------
    resolutions = resolve_must_visits(state)
    terms_detail = []
    for resolution in resolutions:
        hits = [stop.name for stop in scheduled if stop.provider_place_id in resolution.grounded_place_ids]
        terms_detail.append(
            {
                "term": resolution.term,
                "grounded": resolution.grounded,
                "grounded_place_count": len(resolution.grounded_place_ids),
                "scheduled": bool(resolution.grounded and resolution.scheduled),
                "scheduled_place_count": len(hits),
                "scheduled_place_names": hits,
            }
        )
    must_visits = {
        "requested": [entry["term"] for entry in terms_detail],
        "grounded": [entry["term"] for entry in terms_detail if entry["grounded"]],
        "ungrounded": [entry["term"] for entry in terms_detail if not entry["grounded"]],
        "grounded_scheduled": [entry["term"] for entry in terms_detail if entry["scheduled"]],
        "grounded_unscheduled": [entry["term"] for entry in terms_detail if entry["grounded"] and not entry["scheduled"]],
        # one user term taking several itinerary slots through its grounded places
        "terms_with_multiple_scheduled_places": [
            entry["term"] for entry in terms_detail if entry["scheduled_place_count"] > 1
        ],
        "terms_with_multiple_grounded_places": [
            entry["term"] for entry in terms_detail if entry["grounded_place_count"] > 1
        ],
        "detail": terms_detail,
    }

    # -- geography (the validator's own measure and boundary) -------------------------------------
    validation = state.validation_report
    issues = [*validation.critical_issues, *validation.warnings] if validation is not None else []
    day_points = [[stop.coordinates for stop in day.experiences] for day in days]
    spreads = {day.day_number: day_spread_km(points) for day, points in zip(days, day_points)}
    measured = [spread for spread in spreads.values() if spread is not None]
    misplaced = misplaced_stops(day_points)
    geography = {
        "measure": "sum of straight-line distances between consecutive located stops (a proxy, never a route)",
        "boundary_km": GEOGRAPHIC_SPREAD_THRESHOLD_KM,
        "day_spread_km": {str(day): _round(spread) for day, spread in spreads.items()},
        "max_day_spread_km": _round(max(measured)) if measured else None,
        "median_day_spread_km": _round(median(measured)) if measured else None,
        "days_over_boundary": [day for day, spread in spreads.items() if spread is not None and spread > GEOGRAPHIC_SPREAD_THRESHOLD_KM],
        "geographic_spread_findings": sum(1 for issue in issues if issue.category == "geographic_spread"),
        # stops far from their own day and much closer to another day's stops (coordinates only)
        "stops_closer_to_another_day": len(misplaced),
        "days_with_a_stop_closer_to_another_day": sorted({days[day_index].day_number for day_index, _, _ in misplaced}),
    }

    # -- route burden (the routing provider's own legs; the existing limits) -----------------------
    pace = pace_of(state)
    burdens = day_route_burdens(state)
    repair = state.route_burden_repair_report
    long_days = [burden.day_number for burden in burdens if burden.long_route]
    burden_days = [
        {
            "day": burden.day_number,
            "stops": len(days[index].experiences),
            "required_legs": burden.required_legs,
            "routed_legs": burden.routed_legs,
            "total_transfer_seconds": _round(burden.total_duration_seconds, 1),
            "walking_seconds": _round(burden.walking_duration_seconds, 1),
            "max_leg_seconds": _round(burden.max_leg_duration_seconds, 1),
            "max_walk_leg_seconds": _round(burden.max_walk_leg_duration_seconds, 1),
            "excessive_walking": burden.excessive_walking,
            "unreasonable_transfers": burden.over_transfer_limit,
            "long_route": burden.long_route,
        }
        for index, burden in enumerate(burdens)
    ]
    required = sum(burden.required_legs for burden in burdens)
    routed = sum(burden.routed_legs for burden in burdens)
    route_burden = {
        "pace": _value(pace),
        "pace_walking_limit_seconds_per_day": max_day_seconds(pace),
        "days": burden_days,
        "max_single_leg_seconds": _round(max((burden.max_leg_duration_seconds for burden in burdens), default=0.0), 1),
        "max_single_walk_leg_seconds": _round(
            max((burden.max_walk_leg_duration_seconds for burden in burdens), default=0.0), 1
        ),
        "max_day_transfer_seconds": _round(max((burden.total_duration_seconds for burden in burdens), default=0.0), 1),
        "long_travel_days": long_days,
        "long_travel_day_count": len(long_days),
        "long_travel_day_code_present": bool(validation is not None and LONG_TRAVEL_DAY in validation.review_codes),
        # contract A3: a severe finding that survived although the inventory was sufficient
        "viable_at_least_T": viable_at_least_t,
        "long_travel_days_with_viable_at_least_T": long_days if viable_at_least_t else [],
        # the repair's own fixed reason codes (why a long-travel day was or was not changed)
        "repair_attempts": [
            {"day": attempt.day_number, "reason": attempt.reason, "accepted": attempt.accepted}
            for attempt in (repair.attempts if repair is not None else [])
        ],
        "routing_coverage_percentage": _round(100.0 * routed / required, 1) if required else None,
    }

    # -- limited slots (the pipeline's internal tiers; never popularity or rating) -----------------
    def tier_of(candidate_tier: Any) -> str:
        tier = _value(candidate_tier)
        return tier if tier in _TIER_ORDER else _UNKNOWN_TIER

    scheduled_tiers = [tier_of(stop.quality_tier) for stop in scheduled]
    known = [_TIER_ORDER.index(tier) for tier in scheduled_tiers if tier != _UNKNOWN_TIER]
    lowest_scheduled = max(known) if known else None
    unused_viable = [score for score in viable if score.candidate_id not in scheduled_ids]
    unused_tiers = [tier_of(score.quality_tier) for score in unused_viable]
    anchor_ids = grounded_anchor_place_ids(state)
    scheduled_anchor_count = sum(1 for stop in scheduled if stop.provider_place_id in anchor_ids)
    slots = {
        "tier_meaning": "internal pre-ranking tier of the candidate quality stage; not popularity, rating or ranking",
        "scheduled_by_tier": {tier: scheduled_tiers.count(tier) for tier in (*_TIER_ORDER, _UNKNOWN_TIER) if tier in scheduled_tiers},
        "unused_viable_by_tier": {tier: unused_tiers.count(tier) for tier in _TIER_ORDER if tier in unused_tiers},
        "lowest_scheduled_tier": _TIER_ORDER[lowest_scheduled] if lowest_scheduled is not None else None,
        "unused_viable_above_lowest_scheduled_tier": (
            sum(1 for tier in unused_tiers if _TIER_ORDER.index(tier) < lowest_scheduled)
            if lowest_scheduled is not None
            else 0
        ),
        "grounded_anchor_count": len(anchor_ids),
        "scheduled_grounded_anchor_count": scheduled_anchor_count,
        "scheduled_grounded_anchor_rate": _round(scheduled_anchor_count / len(anchor_ids), 3) if anchor_ids else None,
    }

    # -- day composition (coarse classes of the final schedule) -------------------------------------
    markets_requested = diversity.markets_requested_for(state)
    justified = diversity.justified_classes_for(state)
    composition_days = []
    for day in days:
        classes = [diversity.coarse_class(stop.normalized_category) for stop in day.experiences]
        composition_days.append(
            {
                "day": day.day_number,
                "stops": len(classes),
                "coarse_class_counts": {name: classes.count(name) for name in sorted(set(classes))},
                "distinct_classes": len(set(classes)),
                "concentration_kind": diversity.concentration_kind(classes, markets_requested, justified),
            }
        )
    composition = {"days": composition_days}

    # -- pace fit (stop count AND burden) -----------------------------------------------------------
    pace_fit = {
        "pace": _value(pace),
        "target_stops_per_day": targets.per_day,
        "T": targets.target_stops,
        "R": targets.minimum_useful,
        "stops_per_day": [len(day.experiences) for day in days],
        "days_over_pace_target": [day.day_number for day in days if len(day.experiences) > targets.per_day],
        "days_over_pace_walking_limit": [
            burden.day_number for burden in burdens if burden.walking_duration_seconds > max_day_seconds(pace)
        ],
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "note": "reported only; computed from stored state; never an acceptance input of the planner",
        "interests": interests,
        "must_visits": must_visits,
        "geography": geography,
        "route_burden": route_burden,
        "limited_slots": slots,
        "day_composition": composition,
        "pace_fit": pace_fit,
        "baseline": {
            "viable_candidates": verdict.viable,
            "meaningful_scheduled_stops": verdict.scheduled_meaningful_stops,
            "empty_day_count": len(verdict.empty_days),
            "duplicate_scheduled_place_count": len(verdict.duplicate_place_ids),
            "scheduled_unresolved_collision_count": len(scheduled_unresolved_collisions(state)),
        },
        "future_metrics_not_computed": list(FUTURE_METRICS),
    }


def render_lines(metrics: dict[str, Any]) -> list[str]:
    """A short text block for the canary report (figures only)."""
    interests, must, geography = metrics["interests"], metrics["must_visits"], metrics["geography"]
    burden, slots = metrics["route_burden"], metrics["limited_slots"]

    def joined(values: Any) -> str:
        return ", ".join(str(value) for value in values) or "none"

    return [
        f"- requested interests covered: {joined(interests['covered'])}",
        f"- uncovered although verified supply exists: {joined(interests['uncovered_with_supply'])}",
        f"- uncovered, no verified supply: {joined(interests['uncovered_without_supply'])}",
        f"- interest terms the taxonomy does not recognise (not tracked): {joined(interests['unrecognised_terms'])}",
        f"- grounded must-visits not scheduled: {joined(must['grounded_unscheduled'])}",
        f"- must-visit terms holding more than one scheduled place: {joined(must['terms_with_multiple_scheduled_places'])}",
        f"- day spread km (straight-line proxy; boundary {geography['boundary_km']}): "
        + joined(f"day {day} {spread}" for day, spread in geography["day_spread_km"].items()),
        f"- max / median day spread km: {geography['max_day_spread_km']} / {geography['median_day_spread_km']}",
        f"- stops closer to another day's stops: {geography['stops_closer_to_another_day']}",
        f"- longest single leg (s): {burden['max_single_leg_seconds']} | longest walking leg (s): {burden['max_single_walk_leg_seconds']}",
        f"- long-travel days: {joined(burden['long_travel_days'])}"
        f" | of those with viable >= T: {joined(burden['long_travel_days_with_viable_at_least_T'])}",
        f"- scheduled stops by internal tier: {slots['scheduled_by_tier'] or 'none'}",
        f"- unused viable candidates above the lowest scheduled tier: {slots['unused_viable_above_lowest_scheduled_tier']}",
        f"- grounded anchors scheduled: {slots['scheduled_grounded_anchor_count']} of {slots['grounded_anchor_count']}",
        f"- not computed yet (future metrics): {joined(metrics['future_metrics_not_computed'])}",
    ]
