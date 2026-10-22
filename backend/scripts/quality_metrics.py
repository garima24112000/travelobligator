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

# 5 (quality tuning corrections): dispersed days with their cause and
# findings, and the kind of evidence that covers the food interest. Additive.
SCHEMA_VERSION = 5

# Q2: a candidate with at least this many stored usefulness evidences.
_HIGHER_EVIDENCE_BAND = 2

# Contract metrics that CANNOT be computed from stored state yet. They are
# named so a report never implies they were measured; none is estimated.
# Q2 reports how much stored usefulness EVIDENCE the scheduled stops carry;
# that is not a tourist-value ranking and a stop without evidence is not
# thereby "obscure filler", so that entry stays here.
FUTURE_METRICS: tuple[str, ...] = (
    "same_excursion_or_complex_redundancy",
    "tourist_value_ranking_and_obscure_filler_share",
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
    from app.models.common import GeoPoint
    from app.services import candidate_universe as universe
    from app.services import day_composition
    from app.services import place_taxonomy as taxonomy
    from app.services import schedule_diversity as diversity
    from app.services.ai_itinerary_reasoning_request_builder import AIItineraryReasoningRequestBuilder
    from app.services.candidate_usefulness import usefulness_by_place_id
    from app.services.day_order_heuristics import (
        GEOGRAPHIC_SPREAD_THRESHOLD_KM,
        day_extent_km,
        day_spread_km,
        misplaced_stops,
    )
    from app.services.entity_collisions import scheduled_unresolved_collisions
    from app.services.grounded_anchors import grounded_anchor_place_ids
    from app.services.geographic_dispersion import assess_days as assess_dispersion
    from app.services.interest_coverage import food_coverage_evidence, interest_coverage
    from app.services.must_visit_matching import must_visit_place_ids, resolve_must_visits
    from app.services.route_burden import LONG_TRAVEL_DAY, assess_days, day_route_burdens, max_day_seconds
    from app.utils.geo import haversine_distance_km
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
        # What covers the food interest in the final plan, by kind of evidence (the shared
        # definition: a verified culinary stop, or a nearby restaurant suggestion on the plan).
        "food_coverage_evidence": food_coverage_evidence(state) if "food" in coverage else None,
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
        # Days beyond the boundary with the reason the stored state supports, and whether the
        # validator said so. Three findings stay apart: geographic_dispersion (where the places
        # lie; routes verified and within limits), long_travel_day (provider-verified burden) and
        # geographic_spread (a leg unverified, or the routed day over its limits).
        "dispersed_days": [
            {"day": entry.day_number, "spread_km": _round(entry.spread_km), "cause": entry.cause}
            for entry in assess_dispersion(state)
        ],
        "geographic_dispersion_findings": [
            {"severity": _value(issue.severity), "day_section": issue.affected_section}
            for issue in issues
            if issue.category == "geographic_dispersion"
        ],
        # a day beyond the boundary that carries NO finding of any of the three kinds
        "dispersed_days_without_any_finding": [
            day
            for day, spread in spreads.items()
            if spread is not None
            and spread > GEOGRAPHIC_SPREAD_THRESHOLD_KM
            and not any(
                issue.category in ("geographic_dispersion", "geographic_spread", "long_travel_day")
                and issue.affected_section == f"experience_plan.daily_plans[{day}]"
                for issue in issues
            )
        ],
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

    # -- Q4 route severity and recomposition: VERIFIED provider figures, kept apart from proxies ----
    assessments = assess_days(state)
    severities = [assessment.severity for assessment in assessments]
    report = state.route_feasibility_report
    # provider route distance over the straight line between the same two stops: a ratio of two
    # distances that hints at a barrier or a detour. Not a duration and not a feasibility verdict.
    detours = [
        leg.distance_meters / straight_m
        for leg in (report.legs if report is not None else [])
        if leg.distance_meters is not None
        and None not in (leg.from_lat, leg.from_lon, leg.to_lat, leg.to_lon)
        and (
            straight_m := 1000.0
            * (
                haversine_distance_km(
                    GeoPoint(lat=leg.from_lat, lng=leg.from_lon), GeoPoint(lat=leg.to_lat, lng=leg.to_lon)
                )
                or 0.0
            )
        )
        >= 100.0
    ]
    route_severity = {
        "meaning": (
            "severity is judged from the routing provider's VERIFIED legs only (the existing route-burden "
            "limits): severe = those legs alone exceed a limit; unverified = not severe and a required leg "
            "has no provider route (feasibility unknown, never assumed either way)"
        ),
        "days": [
            {
                "day": assessment.day_number,
                "severity": assessment.severity,
                "fully_verified": assessment.fully_verified,
                "unverified_legs": assessment.unverified_legs,
                "legs_the_provider_definitively_could_not_route": assessment.definitive_failures,
                "legs_whose_provider_route_includes_a_ferry": assessment.ferry_legs,
                "verified_transfer_seconds": _round(assessment.burden.total_duration_seconds, 1),
            }
            for assessment in assessments
        ],
        "days_by_severity": {name: severities.count(name) for name in sorted(set(severities))},
        "severe_days_with_unverified_legs": [
            assessment.day_number for assessment in assessments if assessment.verified_severe and not assessment.fully_verified
        ],
        "max_route_distance_over_straight_line_ratio": _round(max(detours), 2) if detours else None,
        "recomposition": {
            "requests_used": repair.recomposition_requests_used if repair is not None else 0,
            "requests_cap": repair.recomposition_requests_cap if repair is not None else None,
            "attempts": [
                {
                    "day": attempt.day_number,
                    "reason": attempt.reason,
                    "accepted": attempt.accepted,
                    "operation": attempt.operation,
                    "severity_before": attempt.severity_before,
                    "severity_after": attempt.severity_after,
                    "unverified_legs_after": attempt.unverified_legs_after,
                    "candidates_shortlisted": attempt.candidates_shortlisted,
                    "candidates_verified": attempt.candidates_verified,
                    "candidates_skipped_for_budget": attempt.candidates_skipped_for_budget,
                    "routing_requests": attempt.routing_requests,
                }
                for attempt in (repair.attempts if repair is not None else [])
            ],
            # severe days a change was accepted for, and how many of those are still severe
            "accepted_changes": sum(1 for attempt in (repair.attempts if repair is not None else []) if attempt.accepted),
            "accepted_but_still_severe": sum(
                1
                for attempt in (repair.attempts if repair is not None else [])
                if attempt.accepted and attempt.severity_after == "severe"
            ),
        },
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

    # Q2 usefulness evidence (the planner's own assessment, recomputed from the stored state).
    assessed = usefulness_by_place_id(state)

    def bands(place_ids: Any) -> dict[str, int]:
        found = [assessed[place_id].evidence_band for place_id in place_ids if place_id in assessed]
        return {str(band): found.count(band) for band in sorted(set(found))}

    scheduled_assessed = [assessed[stop.provider_place_id] for stop in scheduled if stop.provider_place_id in assessed]
    unused_ids = [score.candidate_id for score in unused_viable]
    # A stop the plan had to hold whatever its evidence: a grounded must-visit, or the only scheduled
    # stop serving a requested interest. Every other scheduled stop was a discretionary choice.
    must_visit_ids = must_visit_place_ids(state)
    sole_cover_ids = {
        serving[0].provider_place_id
        for interest in coverage
        if len(serving := [stop for stop in scheduled if interest in stop.matched_interests]) == 1
    }
    discretionary = [
        assessed[stop.provider_place_id]
        for stop in scheduled
        if stop.provider_place_id in assessed
        and stop.provider_place_id not in must_visit_ids
        and stop.provider_place_id not in sole_cover_ids
    ]
    weakest_discretionary = min((item.evidence_band for item in discretionary), default=None)
    builder = AIItineraryReasoningRequestBuilder()
    bounded_ids = {candidate.provider_place_id for candidate in builder.build_request(state).allowed_candidates}
    slots = {
        "usefulness_evidence_band_meaning": (
            "count (0-3) of stored planning evidence on a candidate: grounded semantic anchor, strong provider "
            "significance, requested-interest fit. Band 0 means no such evidence is stored -- thin provider "
            "metadata or not proposed as an anchor -- and says nothing about a place being poor. Not "
            "popularity, rating or ranking."
        ),
        "scheduled_by_usefulness_evidence_band": bands(stop.provider_place_id for stop in scheduled),
        "unused_viable_by_usefulness_evidence_band": bands(unused_ids),
        "zero_usefulness_evidence_scheduled_share": (
            _round(sum(1 for item in scheduled_assessed if item.evidence_band == 0) / len(scheduled_assessed), 3)
            if scheduled_assessed
            else None
        ),
        "higher_evidence_available_count": sum(
            1 for score in viable if assessed[score.candidate_id].evidence_band >= _HIGHER_EVIDENCE_BAND
        ),
        "higher_evidence_scheduled_count": sum(
            1 for item in scheduled_assessed if item.evidence_band >= _HIGHER_EVIDENCE_BAND
        ),
        "discretionary_scheduled_count": len(discretionary),
        # An evidence-ordering observation, not an error: class caps, day fit and geography
        # legitimately choose a candidate with less stored evidence.
        "unused_with_more_evidence_than_weakest_discretionary_scheduled": (
            sum(1 for place_id in unused_ids if assessed[place_id].evidence_band > weakest_discretionary)
            if weakest_discretionary is not None
            else None
        ),
        # recomputed with the CURRENT candidate cap (the request itself is not stored)
        "eligible_outside_reasoning_bound_by_band": bands(
            score.candidate_id
            for score in scores.values()
            if score.quality_tier in _VIABLE_TIERS and score.candidate_id not in bounded_ids
        ),
        "reasoning_bound_must_visit_overflow": builder.must_visit_overflow(state),
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

    # -- Q3 day geography (straight-line proxies and computed areas; never a route or a duration) --
    pool_points: dict[str, Any] = {}
    for poi in universe.schedulable_broad_pois(state):
        coordinates = poi.get("coordinates") or {}
        if poi.get("place_id") and coordinates.get("lat") is not None and coordinates.get("lng") is not None:
            pool_points.setdefault(str(poi["place_id"]), GeoPoint(lat=coordinates["lat"], lng=coordinates["lng"]))
    for promoted in universe.resolve_promoted_candidates(state).accepted:
        if promoted.provider_place_id and promoted.coordinates is not None:
            pool_points.setdefault(str(promoted.provider_place_id), promoted.coordinates)
    for stop in scheduled:
        if stop.provider_place_id and stop.coordinates is not None:
            pool_points.setdefault(stop.provider_place_id, stop.coordinates)
    areas = day_composition.build_areas(
        [
            day_composition.Located(place_id, point, assessed[place_id].sort_key if place_id in assessed else ())
            for place_id, point in pool_points.items()
        ]
    )
    unused_points = [
        (score.candidate_id, pool_points[score.candidate_id])
        for score in viable
        if score.candidate_id not in scheduled_ids and score.candidate_id in pool_points
    ]
    near_km = day_composition.AREA_MAX_DIAMETER_KM
    geography_days = []
    area_days: dict[str, list[int]] = {}
    for day, points in zip(days, day_points):
        extent = day_extent_km(points)
        band = day_composition.geographic_band(extent, spreads[day.day_number])
        visited = [areas.area_of.get(stop.provider_place_id or "") for stop in day.experiences]
        visited = [area for area in visited if area is not None]
        distinct = list(dict.fromkeys(visited))
        for area in distinct:
            area_days.setdefault(area, []).append(day.day_number)
        # an area entered again after the day's order had left it (a -> b -> a)
        revisits = sum(
            1
            for index, area in enumerate(visited)
            if index > 0 and area != visited[index - 1] and area in visited[: index - 1]
        )
        # a discretionary stop for which an unused viable candidate of at least its usefulness
        # preference would have left the day's extent within the near proxy
        compact_alternative = False
        if band in (day_composition.EXTENDED, day_composition.DISPERSED):
            for index, stop in enumerate(day.experiences):
                if stop.provider_place_id in must_visit_ids or stop.provider_place_id not in assessed:
                    continue
                rest = [point for position, point in enumerate(points) if position != index]
                for place_id, point in unused_points:
                    if assessed[place_id].preference > assessed[stop.provider_place_id].preference:
                        continue
                    if (day_extent_km([*rest, point]) or 0.0) <= near_km:
                        compact_alternative = True
                        break
                if compact_alternative:
                    break
        geography_days.append(
            {
                "day": day.day_number,
                "extent_km": _round(extent),
                "band": band,
                "distinct_areas": len(distinct),
                "area_revisits_in_stop_order": revisits,
                "compact_alternative_of_equal_usefulness_existed": compact_alternative,
            }
        )
    band_values = [entry["band"] for entry in geography_days]
    split_areas = sorted(
        area
        for area, visiting in area_days.items()
        if len(visiting) > 1
        and any(entry["distinct_areas"] > 1 for entry in geography_days if entry["day"] in visiting)
    )
    day_geography = {
        "measure": (
            "extent = largest straight-line distance between two located stops of a day; areas = computed "
            "complete-linkage groups of the stored candidates' coordinates (opaque ids). Proxies only: never "
            "a route length, a walking distance or a travel duration"
        ),
        "near_proxy_km": near_km,
        "materiality_tolerance_km": day_composition.EXTENT_MATERIALITY_KM,
        "days": geography_days,
        "days_by_band": {band: band_values.count(band) for band in sorted(set(band_values))},
        "area_count_in_pool": len(areas.members),
        "days_within_one_area": sum(1 for entry in geography_days if entry["distinct_areas"] == 1),
        "area_revisits_in_stop_order": sum(entry["area_revisits_in_stop_order"] for entry in geography_days),
        # an area visited on several days although one of those days also goes elsewhere
        "areas_split_across_mixed_days": len(split_areas),
        "days_with_a_compact_alternative_of_equal_usefulness": [
            entry["day"] for entry in geography_days if entry["compact_alternative_of_equal_usefulness_existed"]
        ],
        # An observation, not an error: a grounded anchor is a preference, never a mandatory stop.
        "grounded_anchors_unscheduled": len(anchor_ids) - scheduled_anchor_count,
        "grounded_must_visit_places_beyond_pace_capacity": max(0, len(must_visit_ids) - targets.target_stops),
    }

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
        "route_severity": route_severity,
        "limited_slots": slots,
        "day_composition": composition,
        "day_geography": day_geography,
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
        f"- dispersed days (beyond the boundary) and their cause: "
        + joined(f"day {entry['day']} {entry['spread_km']} km {entry['cause']}" for entry in geography["dispersed_days"])
        + f" | dispersed days without any finding: {joined(geography['dispersed_days_without_any_finding'])}",
        f"- food interest covered by: {interests['food_coverage_evidence'] if interests['food_coverage_evidence'] is not None else 'not requested'}",
        f"- days by geographic band (straight-line proxy, not a route): {metrics['day_geography']['days_by_band'] or 'none'}"
        f" | days within one computed area: {metrics['day_geography']['days_within_one_area']}"
        f" | area revisits in stop order: {metrics['day_geography']['area_revisits_in_stop_order']}",
        f"- days where an unused candidate of equal usefulness would have kept the day compact: "
        f"{joined(metrics['day_geography']['days_with_a_compact_alternative_of_equal_usefulness'])}",
        f"- longest single leg (s): {burden['max_single_leg_seconds']} | longest walking leg (s): {burden['max_single_walk_leg_seconds']}",
        f"- route severity by day (verified provider legs only): {metrics['route_severity']['days_by_severity'] or 'none'}"
        f" | route recomposition: {metrics['route_severity']['recomposition']['accepted_changes']} accepted, "
        f"{metrics['route_severity']['recomposition']['accepted_but_still_severe']} of them still severe, "
        f"{metrics['route_severity']['recomposition']['requests_used']} routing requests",
        f"- long-travel days: {joined(burden['long_travel_days'])}"
        f" | of those with viable >= T: {joined(burden['long_travel_days_with_viable_at_least_T'])}",
        f"- scheduled stops by internal tier: {slots['scheduled_by_tier'] or 'none'}",
        f"- unused viable candidates above the lowest scheduled tier: {slots['unused_viable_above_lowest_scheduled_tier']}",
        f"- grounded anchors scheduled: {slots['scheduled_grounded_anchor_count']} of {slots['grounded_anchor_count']}",
        f"- scheduled stops by usefulness evidence band (0-3 stored evidences; not a ranking): "
        f"{slots['scheduled_by_usefulness_evidence_band'] or 'none'}",
        f"- candidates with 2+ evidences scheduled: {slots['higher_evidence_scheduled_count']} of "
        f"{slots['higher_evidence_available_count']} available",
        f"- unused candidates with more evidence than the weakest discretionary stop: "
        f"{slots['unused_with_more_evidence_than_weakest_discretionary_scheduled']}",
        f"- not computed yet (future metrics): {joined(metrics['future_metrics_not_computed'])}",
    ]
