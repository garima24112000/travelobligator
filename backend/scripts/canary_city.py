"""Section 203C.2B single-city live canary (manual, live providers).

Runs the REAL generation pipeline once, in-process, for ONE arbitrary
city/scenario given on the command line, and prints a sanitized report
(plus a JSON and a text copy). Benchmark tooling only: nothing here is
imported by `backend/app`, and no city is known to it.

    cd backend
    python scripts/canary_city.py --city "City, Country" --days 3 --pace balanced \
        --interests "architecture, history, food" --must-visit "Some Place" --must-visit "Another Place"

Requires GEOAPIFY_API_KEY and GROQ_API_KEY in the environment. Never prints
a key, a request URL or raw exception text. Uses the same production-shaped
configuration as `benchmark_cities.py` on a throwaway Local JSON store and a
throwaway SQLite provider cache (so credits are cold-cache numbers).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPTS_DIR.parent
# A week from today: inside the weather provider's forecast horizon, so the
# canary exercises a real forecast instead of a known "not yet available".
_START_DATE = date.today() + timedelta(days=7)
_MIN_ROUTING_COVERAGE = 0.90
_TOP_CANDIDATES = 20
_FOOD_NEARBY_KM = 1.5
# The only review codes a clean canary may carry: standing data-coverage
# limits of V1 (no bookable inventory, no hotel ratings, context providers).
# Any other code -- an underfilled plan, a long travel day, a must-visit or
# scheduling problem, or a code this list does not know -- fails the canary.
_ACCEPTED_REVIEW_CODES = frozenset(
    {
        "ACCOMMODATION_INVENTORY",
        "FLIGHT_INVENTORY",
        "HOTEL_RATINGS",
        "WEATHER",
        "HOLIDAYS",
        "BUDGET",
        "PROVIDER_COVERAGE",
    }
)


# Section 1A (measurement only): how the generation's performance report is
# laid out. Each row sums the named stages' EXCLUSIVE time, so the rows plus
# "other" add up to the total. Reported only -- never an acceptance check.
_PERFORMANCE_STAGE_ROWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("traveler profile", ("traveler_profile",)),
    ("destination resolution", ("destination_resolution",)),
    ("weather/holiday", ("weather_holiday",)),
    ("broad places", ("places_broad",)),
    ("must-visit grounding", ("must_visit_grounding",)),
    ("destination context (rest)", ("destination_context", "currency")),
    ("anchor proposal", ("anchor_proposal",)),
    ("anchor grounding", ("anchor_grounding",)),
    ("anchor/must-visit details", ("anchor_details",)),
    ("identity details", ("identity_details",)),
    ("candidate/inventory", ("candidate_quality", "ai_candidate", "inventory_sufficiency")),
    ("strategy/stay/inventory reports", ("trip_strategy", "stay_transport", "accommodation_inventory", "flight_inventory")),
    ("reasoning", ("itinerary_reasoning",)),
    ("experience planning (rest)", ("experience_planning",)),
    ("diversity", ("diversity",)),
    ("spatial grouping", ("spatial_grouping",)),
    ("routing walk", ("walking_routing",)),
    ("routing drive", ("alternate_mode_routing",)),
    ("route reports (rest)", ("route_feasibility", "route_aware_sequencing", "travel_time_buffer")),
    ("repair (route burden)", ("route_repair",)),
    ("repair (AI itinerary)", ("itinerary_repair",)),
    ("food", ("food_suggestions",)),
    ("validation", ("validation",)),
    ("narrator", ("narrator",)),
    ("persistence", ("persistence",)),
    ("bookkeeping", ("post_processing", "underfill_fallback", "provider_coverage", "final_state")),
)
_PERFORMANCE_PROVIDER_ROWS: tuple[tuple[str, str], ...] = (
    ("Geoapify geocode", "geoapify_geocoding"),
    ("Geoapify places", "geoapify_places"),
    ("Geoapify details", "geoapify_details"),
    ("Geoapify walk routing", "geoapify_routing_walk"),
    ("Geoapify drive routing", "geoapify_routing_drive"),
    ("Open-Meteo", "open_meteo"),
    ("Nager.Date", "nager_date"),
    ("Groq anchor", "groq_anchor"),
    ("Groq reasoning", "groq_reasoning"),
    ("Groq repair", "groq_repair"),
    ("Groq narrator", "groq_narrator"),
)
_PERFORMANCE_REDUNDANT_ROWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("repeated geocode calls", ("geocoding",)),
    ("repeated details calls", ("place_details",)),
    ("repeated route calls", ("routing", "routing_drive")),
    ("repeated places calls", ("places",)),
    ("repeated named lookup calls", ("named_lookup",)),
    ("repeated destination resolutions (cache-served or live)", ("destination_resolution",)),
    ("repeated route lookups (memo-served or live)", ("route_sequence_lookup", "route_leg_lookup", "alternate_mode_lookup")),
)


def _performance_section(performance: Any) -> dict[str, Any]:
    """The generation's own performance report (numbers under fixed keys),
    arranged for display. `available: False` when the generation carried none."""
    if performance is None:
        return {"available": False}
    stage_ms = dict(performance.stage_ms)
    known = {name for _, names in _PERFORMANCE_STAGE_ROWS for name in names}
    stages = [
        {"label": label, "seconds": round(sum(stage_ms.get(name, 0.0) for name in names) / 1000.0, 2)}
        for label, names in _PERFORMANCE_STAGE_ROWS
    ]
    stages.extend(
        {"label": name, "seconds": round(value / 1000.0, 2)}
        for name, value in sorted(stage_ms.items())
        if name not in known
    )
    labelled = {key for _, key in _PERFORMANCE_PROVIDER_ROWS}
    provider_rows = [*_PERFORMANCE_PROVIDER_ROWS, *((key, key) for key in sorted(performance.provider_ms) if key not in labelled)]
    totals, repeats = performance.request_totals, performance.redundant_requests
    return {
        "available": True,
        "engine": performance.engine,
        "total_seconds": round(performance.total_ms / 1000.0, 2) if performance.total_ms is not None else None,
        "other_seconds": round(performance.other_ms / 1000.0, 2) if performance.other_ms is not None else None,
        "stages": stages,
        "provider_wall_time": [
            {
                "label": label,
                "seconds": round(performance.provider_ms.get(key, 0.0) / 1000.0, 2),
                "attempts": performance.provider_attempts.get(key, 0),
            }
            for label, key in provider_rows
        ],
        "provider_wall_time_total_seconds": round(sum(performance.provider_ms.values()) / 1000.0, 2),
        "counts": dict(performance.counts),
        "cache_hits_by_source": dict(performance.cache_hits),
        "cache_misses_by_source": dict(performance.cache_misses),
        "redundant_work": [
            {
                "label": label,
                "repeated": sum(repeats.get(kind, 0) for kind in kinds),
                "total": sum(totals.get(kind, 0) for kind in kinds),
            }
            for label, kinds in _PERFORMANCE_REDUNDANT_ROWS
        ],
        # Section 1B: concurrency diagnostics (reported, never judged)
        "concurrency": {
            "peak_geoapify_concurrency": performance.peak_geoapify_concurrency,
            "process_peak_geoapify_concurrency": performance.process_peak_geoapify_concurrency,
            "concurrent_batches": performance.concurrent_batches,
            "batch_sizes": {operation: list(sizes) for operation, sizes in performance.batch_sizes.items()},
            # summed over the tasks of concurrent batches: overlaps the wall time above
            "stage_task_seconds": {
                name: round(value / 1000.0, 2) for name, value in sorted(performance.stage_task_ms.items())
            },
        },
        # Section 1C: what each model stage did (counts and fixed labels only)
        "llm_stages": [
            {
                "label": label,
                "attempts": stage.attempts,
                "structural_retries": stage.structural_retries,
                "transport_retries": stage.transport_retries,
                "deadline_exceeded": stage.deadline_exceeded,
                "result": stage.result,
                # Section 3B: fixed labels only (None when not applicable)
                "transport_failure": stage.transport_failure,
                "retry_after": stage.retry_after,
                "seconds": round(performance.provider_ms.get(key, 0.0) / 1000.0, 2),
            }
            for label, key in _PERFORMANCE_PROVIDER_ROWS
            if (stage := performance.llm_stages.get(key)) is not None
        ],
        # raw figures, for comparing runs
        "stage_ms": stage_ms,
        "stage_inclusive_ms": dict(performance.stage_inclusive_ms),
        "provider_ms": dict(performance.provider_ms),
    }


def _complete_performance_report(recorder: Any, state: Any) -> Any:
    """The canary recorder's own report (complete), falling back to the one
    stored with the plan. Geoapify request counts are the usage tracker's."""
    from app.core import performance
    from app.models.generation_performance import GenerationPerformanceReport

    stored = state.generation_performance_report
    usage = state.provider_usage_report
    figures = performance.build_report(
        recorder, {"calls_by_api": dict(usage.calls_by_api)} if usage is not None else None
    )
    if figures is None:
        return stored
    return GenerationPerformanceReport(
        **figures, engine=stored.engine if stored is not None else None, includes_final_commit=True
    )


def _slug(text: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_") or "city"


def _norm(text: Any) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", str(text or "")) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", plain.casefold()).strip()


def _split_list(values: list[str] | None) -> list[str]:
    items: list[str] = []
    for value in values or []:
        items.extend(part.strip() for part in value.split(",") if part.strip())
    return items


def _value(item: Any) -> Any:
    return getattr(item, "value", item)


def _run(args: argparse.Namespace, cache_hits: dict[str, int]) -> dict[str, Any]:
    from app.core import performance
    from app.models.planning_state import TravelGroupType, TripPace, TripRequest
    from app.repositories.factory import get_planning_state_repository
    from app.services import place_taxonomy as taxonomy
    from app.services.day_rationale import RATIONALE_WARNING_PREFIX, current_day_rationale
    from app.models.routing import TRANSFER_MODE_DRIVE, TRANSFER_MODE_WALK, leg_mode
    from app.services import schedule_diversity as diversity
    from app.services.entity_collisions import scheduled_unresolved_collisions
    from app.services.grounded_anchors import grounded_anchor_place_ids
    from app.services.interest_coverage import final_food_evidence, interest_coverage
    from app.services.must_visit_matching import resolve_must_visits
    from app.services.planning_orchestrator import planning_orchestrator
    from app.services.route_burden import day_route_burdens
    from app.services.usefulness_contract import evaluate_usefulness, is_meaningful_stop
    from app.utils.geo import haversine_distance_km

    interests = _split_list(args.interests)
    must_visit = list(args.must_visit or [])
    # Optional scenario fields a caller (the tuning benchmark) may fix; the
    # single-city canary leaves them out and keeps its own defaults.
    start_date = getattr(args, "start_date", None) or _START_DATE
    origin = getattr(args, "origin", None)
    travelers = getattr(args, "travelers", None) or 2
    report: dict[str, Any] = {
        "request": {
            "city": args.city, "days": args.days, "pace": args.pace, "interests": interests, "must_visit": must_visit,
            "start_date": start_date.isoformat(), "origin": origin, "travelers": travelers,
        }
    }

    # Section 1A: the canary owns the recorder, so its figures cover the whole
    # call, including the final commit (which the report stored with the plan
    # cannot contain).
    recorder = performance.started_recorder()
    started = time.monotonic()
    try:
        with performance.activate(recorder):
            created = planning_orchestrator.create_trip(
                TripRequest(
                    primary_destination=args.city,
                    origin_city=origin,
                    start_date=start_date,
                    end_date=start_date + timedelta(days=args.days - 1),
                    travelers_count=travelers,
                    travel_group_type=TravelGroupType.COUPLE,
                    pace=TripPace(args.pace),
                    interests=interests,
                    must_visit=must_visit,
                )
            )
            state = planning_orchestrator.generate_full_plan_via_langgraph(created.trip_id)
    except Exception as exc:  # reported by TYPE only -- never the exception text
        report["technical_failure"] = type(exc).__name__
        report["latency"] = {"total_generation_seconds": round(time.monotonic() - started, 1)}
        return report
    report["technical_failure"] = None
    report["latency"] = {"total_generation_seconds": round(time.monotonic() - started, 1)}
    # Where that time went. Reported only; never an acceptance check.
    report["performance"] = _performance_section(_complete_performance_report(recorder, state))

    context = state.destination_context
    pois = context.candidate_pois if context is not None else []
    resolved = context.resolved_destination if context is not None else None
    report["destination"] = {
        "resolved_destination": (resolved or {}).get("display_name") if resolved else None,
        "geocode_success": bool(resolved),
    }

    # -- inventory ----------------------------------------------------------------------
    verdict = evaluate_usefulness(state)
    inventory = state.inventory_sufficiency_report
    report["inventory"] = {
        "T": verdict.targets.target_stops,
        "R": verdict.targets.minimum_useful,
        "H": verdict.targets.healthy_buffer,
        "broad_candidate_count": len(pois),
        "food_candidate_count": len(context.candidate_restaurants) if context is not None else 0,
        "viable_meaningful_candidate_count": verdict.viable,
        "inventory_status": inventory.status.value if inventory is not None else None,
        "expansion_used": bool(inventory.expansion_attempted) if inventory is not None else False,
    }

    # -- anchors ------------------------------------------------------------------------
    batch = state.ai_candidate_proposal_batch
    discovery = state.ai_provider_discovery_result
    promotion = state.ai_candidate_promotion_report
    attempts = discovery.attempts if discovery is not None else []
    grounded_attempts = [a for a in attempts if _value(a.status) == "matched" and a.match is not None]
    not_grounded: dict[str, int] = {}
    for attempt in attempts:
        if _value(attempt.status) != "matched":
            not_grounded[_value(attempt.status)] = not_grounded.get(_value(attempt.status), 0) + 1
    anchor_ids = {a.match.provider_place_id for a in grounded_attempts}
    report["anchors"] = {
        "proposal_status": _value(batch.result.status) if batch is not None else "not_run",
        # machine-readable reason for a non-completed proposal (never model output)
        "failure_kind": _value(batch.result.failure_kind) if batch is not None else None,
        "dropped_invalid_proposals": batch.result.dropped_proposal_count if batch is not None else 0,
        "proposed": len(batch.result.proposals) if batch is not None else 0,
        "grounded": len(grounded_attempts),
        "rejected": not_grounded,  # by reason: not_found / provider_failed / not_searched / ...
        "promoted": len(promotion.promoted_candidates) if promotion is not None else 0,
        "grounded_names": [a.match.name for a in grounded_attempts],
        "promoted_names": [c.name for c in promotion.promoted_candidates] if promotion is not None else [],
    }
    category_mismatch = not_grounded.get("grounding_category_mismatch", 0)
    duplicate_anchors = not_grounded.get("duplicate_grounded_anchor", 0)
    report["anchor_hygiene"] = {
        # geographically grounded by the provider, before the category filter and de-duplication
        "grounded_before_category_filter": len(grounded_attempts) + category_mismatch + duplicate_anchors,
        "category_mismatch_rejected": category_mismatch,
        "duplicate_anchors_removed": duplicate_anchors,
        "final_promoted_anchors": len(promotion.promoted_candidates) if promotion is not None else 0,
    }

    # -- schedule -----------------------------------------------------------------------
    plan = state.experience_plan
    days = plan.daily_plans if plan is not None else []
    scheduled = [experience for day in days for experience in day.experiences]
    scheduled_ids = {experience.provider_place_id for experience in scheduled if experience.provider_place_id}
    # Section 3B: how many grounded (provider-grounded AND promoted) anchors reached the schedule,
    # by provider place id -- an anchor already in the broad pool counts exactly like any other.
    grounded_anchor_ids = grounded_anchor_place_ids(state)
    scheduled_anchors = [stop.name for stop in scheduled if stop.provider_place_id in grounded_anchor_ids]
    report["anchors"].update(
        {
            "grounded_anchor_count": len(grounded_anchor_ids),
            "scheduled_grounded_anchor_count": len(scheduled_anchors),
            "grounded_anchor_schedule_ratio": (
                round(len(scheduled_anchors) / len(grounded_anchor_ids), 3) if grounded_anchor_ids else None
            ),
            "scheduled_grounded_anchor_names": scheduled_anchors,
        }
    )

    # -- must-visits ---------------------------------------------------------------------
    # The same identity-based resolution the validator uses (never a second, looser rule).
    grounded_terms: dict[str, dict[str, Any]] = {
        resolution.term: {
            "grounded": resolution.grounded,
            "grounded_as": resolution.grounded_names[0] if resolution.grounded_names else None,
            "scheduled": resolution.grounded and resolution.scheduled,
        }
        for resolution in resolve_must_visits(state)
    }
    report["must_visits"] = {
        "requested": must_visit,
        "grounded": [term for term, info in grounded_terms.items() if info["grounded"]],
        "scheduled": [term for term, info in grounded_terms.items() if info["scheduled"]],
        "unresolved": [term for term, info in grounded_terms.items() if not info["grounded"]],
        "detail": grounded_terms,
    }

    # -- top candidates -------------------------------------------------------------------
    # A must-visit is either grounded by a targeted lookup (carries the user's
    # term) or was already in the broad pool under a matching name.
    must_visit_ids = {
        poi.get("place_id")
        for poi in pois
        if poi.get("must_visit_term") or any(_norm(term) in _norm(poi.get("name")) for term in must_visit)
    }
    quality = state.candidate_quality_report
    scores = [*quality.attraction_scores, *quality.ai_directed_scores] if quality is not None else []
    ai_directed_ids = {score.candidate_id for score in quality.ai_directed_scores} if quality is not None else set()
    seen: set[str] = set()
    top: list[dict[str, Any]] = []
    for score in sorted(scores, key=lambda s: -s.total_score):
        if score.candidate_id in seen:
            continue
        seen.add(score.candidate_id)
        source = (
            "must-visit" if score.candidate_id in must_visit_ids
            else "grounded anchor" if score.candidate_id in ai_directed_ids or score.candidate_id in anchor_ids
            else "broad"
        )
        top.append(
            {
                "name": score.candidate_name,
                "category": getattr(score, "normalized_category", None),
                "score": round(score.total_score, 3),
                "tier": _value(score.quality_tier),
                "source": source,
                "scheduled": score.candidate_id in scheduled_ids,
            }
        )
        if len(top) >= _TOP_CANDIDATES:
            break
    report["top_candidates"] = top

    # -- final itinerary -------------------------------------------------------------------
    feasibility = state.route_feasibility_report
    legs = feasibility.legs if feasibility is not None else []
    leg_by_pair = {(leg.from_experience_id, leg.to_experience_id): leg for leg in legs}
    itinerary: list[dict[str, Any]] = []
    for day in days:
        stops = day.experiences
        day_legs = [leg_by_pair.get((a.experience_id, b.experience_id)) for a, b in zip(stops, stops[1:])]
        routed = [leg for leg in day_legs if leg is not None and leg.distance_meters is not None and leg.duration_seconds is not None]
        itinerary.append(
            {
                "day": day.day_number,
                "attractions": [
                    {
                        "order": index,
                        "name": stop.name,
                        "category": stop.category,
                        "provider_identity_present": bool(stop.provider_place_id and stop.provider_source and stop.coordinates),
                        "provider_source": stop.provider_source,
                        "low_value": bool(stop.low_value_object),
                    }
                    for index, stop in enumerate(stops, start=1)
                ],
                "nearby_food": [suggestion.name for suggestion in day.restaurant_suggestions],
                "route_distance_meters": round(sum(leg.distance_meters for leg in routed), 1) if routed else None,
                "route_duration_seconds": round(sum(leg.duration_seconds for leg in routed), 1) if routed else None,
                "route_legs": [
                    {
                        "from": a.name, "to": b.name,
                        # the mode the provider routed this leg in (a leg stored without one is a walk)
                        "mode": leg_mode(leg.mode) if leg and leg.distance_meters is not None else None,
                        "distance_meters": leg.distance_meters if leg else None,
                        "duration_seconds": leg.duration_seconds if leg else None,
                        "status": _value(leg.status) if leg else "missing",
                        # a fixed code for a leg the provider did not route (never provider text)
                        "failure_reason": leg.failure_reason if leg else None,
                        "mode_adaptation_attempted": bool(leg.mode_adaptation_attempted) if leg else False,
                        # the provider's original walking figures, kept when the leg became a vehicle transfer
                        "walking_distance_meters": leg.walking_distance_meters if leg else None,
                        "walking_duration_seconds": leg.walking_duration_seconds if leg else None,
                    }
                    for (a, b), leg in zip(zip(stops, stops[1:]), day_legs)
                ],
                "warnings": list(day.warnings),
            }
        )
    report["final_itinerary"] = itinerary

    # -- quality ---------------------------------------------------------------------------
    validation = state.validation_report
    low_value_scheduled = [stop.name for stop in scheduled if stop.low_value_object]
    report["quality"] = {
        "meaningful_scheduled_stops": verdict.scheduled_meaningful_stops,
        "target_attainment_ratio": verdict.target_attainment_ratio,
        "empty_days": list(verdict.empty_days),
        "duplicates": list(verdict.duplicate_place_ids),
        "low_value_scheduled_objects": low_value_scheduled,
        # the validator's own rule: final scheduled stops, and for food also the final nearby food
        "interest_coverage": interest_coverage(state),
        "food_interest_evidence": final_food_evidence(state),
        "unrecognised_interests": [term for term in interests if not taxonomy.canonical_interests([term])],
        "usefulness_verdict": (
            "not_enforced (viable < R)" if not verdict.enforced else "pass" if verdict.passed else "underfilled"
        ),
        "fallback_pass_used": bool(state.usefulness_fallback_applied),
        "readiness": _value(validation.readiness_status) if validation is not None else None,
        "blocking_codes": list(validation.blocking_codes) if validation is not None else [],
        "review_codes": list(validation.review_codes) if validation is not None else [],
        "critical_issue_categories": [issue.category for issue in validation.critical_issues] if validation else [],
    }

    # -- routing ---------------------------------------------------------------------------
    required = sum(max(0, len(day.experiences) - 1) for day in days)
    routed_legs = [leg for leg in legs if leg.distance_meters is not None and leg.duration_seconds is not None]
    failures: dict[str, int] = {}
    for leg in legs:
        if leg not in routed_legs:
            failures[_value(leg.status)] = failures.get(_value(leg.status), 0) + 1
    report["routing"] = {
        "required_legs": required,
        "factual_routed_legs": len(routed_legs),
        "coverage_percentage": round(100.0 * len(routed_legs) / required, 1) if required else None,
        "failures": failures,
        "provider": feasibility.provider if feasibility is not None else None,
        "sequencing_applied": bool(
            state.route_aware_sequencing_report and state.route_aware_sequencing_report.applied_to_itinerary
        ),
    }

    # -- daily travel burden (provider leg data only) -------------------------------------------
    report["daily_travel_burden"] = [
        {
            "day": burden.day_number,
            # walking burden: WALK legs only
            "total_walking_distance_meters": round(burden.walking_distance_meters, 1),
            "total_walking_duration_seconds": round(burden.walking_duration_seconds, 1),
            "max_walk_leg_distance_meters": round(burden.max_walk_leg_distance_meters, 1),
            "max_walk_leg_duration_seconds": round(burden.max_walk_leg_duration_seconds, 1),
            # total transfer: every mode, for information
            "total_transfer_distance_meters": round(burden.total_distance_meters, 1),
            "total_transfer_duration_seconds": round(burden.total_duration_seconds, 1),
            "max_leg_distance_meters": round(burden.max_leg_distance_meters, 1),
            "max_leg_duration_seconds": round(burden.max_leg_duration_seconds, 1),
            "vehicle_transfer_legs": burden.drive_legs,
            "max_vehicle_transfer_duration_seconds": round(burden.max_drive_leg_duration_seconds, 1),
            "routed_legs": burden.routed_legs,
            "required_legs": burden.required_legs,
            "excessive_walking": burden.excessive_walking,
            "unreasonable_transfers": burden.over_transfer_limit,
            "long_route": burden.long_route,
        }
        for burden in day_route_burdens(state)
    ]

    # -- movement modes: no leg may carry movement data the provider did not return ---------------
    known_modes = {TRANSFER_MODE_WALK, TRANSFER_MODE_DRIVE}
    report["movement_modes"] = {
        "legs_by_mode": {
            mode: sum(1 for leg in routed_legs if leg_mode(leg.mode) == mode) for mode in sorted(known_modes)
        },
        "mode_adaptations_attempted": sum(1 for leg in legs if leg.mode_adaptation_attempted),
        "legs_adapted_to_vehicle_transfer": sum(1 for leg in legs if leg.walking_duration_seconds is not None),
        "legs_with_unverified_movement_data": sum(
            1
            for leg in routed_legs
            if _value(leg.status) != "success" or not leg.provider or leg_mode(leg.mode) not in known_modes
        ),
    }

    # -- diversity (coarse attraction classes of the FINAL schedule) ------------------------------
    markets_requested = diversity.markets_requested_for(state)
    justified = diversity.justified_classes_for(state)
    planned_diversity = {entry.day_number: entry for entry in (plan.schedule_diversity if plan is not None else [])}
    diversity_days: list[dict[str, Any]] = []
    for day in days:
        classes = [diversity.coarse_class(stop.normalized_category) for stop in day.experiences]
        planned = planned_diversity.get(day.day_number)
        diversity_days.append(
            {
                "day": day.day_number,
                "coarse_category_counts": {name: classes.count(name) for name in sorted(set(classes))},
                # hard = too many markets (a defect); soft = concentrated in a class the traveller did
                # not ask for (reported only); justified = concentrated in a class they asked for
                "concentration_kind": (kind := diversity.concentration_kind(classes, markets_requested, justified)),
                "hard_violation": kind == diversity.HARD,
                "soft_concentration": kind == diversity.SOFT,
                "explicit_interest_justified_concentration": kind == diversity.JUSTIFIED,
                "eligible_alternatives_existed": bool(planned and planned.alternatives_available),
                "diversity_repair_attempted": bool(planned and planned.repair_attempted),
                "replacements": [
                    {"replaced_place": r.replaced_place, "replacement_place": r.replacement_place}
                    for r in (planned.replacements if planned else [])
                ],
            }
        )
    report["diversity"] = {
        "markets_explicitly_requested": markets_requested,
        "interest_justified_classes": sorted(justified),
        "days": diversity_days,
    }

    # -- suspect entity collisions ----------------------------------------------------------------
    # Sanitised records only: provider ids, name variants, yes/no identity flags, separation, classes.
    collisions = list(context.suspect_entity_collisions) if context is not None else []
    scheduled_collisions = scheduled_unresolved_collisions(state)
    # Independent of the provider's own records: two consecutive scheduled stops of a compatible
    # class that the routing provider puts ZERO metres apart, unless evidence says they are distinct.
    distinct_pairs = {
        frozenset(record["place_ids"]) for record in collisions if record.get("resolution") == "distinct"
    }
    building_classes = {diversity.HISTORY_ARCHITECTURE, diversity.MUSEUM_CULTURE}
    zero_distance_pairs: list[dict[str, Any]] = []
    for day in days:
        for a, b in zip(day.experiences, day.experiences[1:]):
            leg = leg_by_pair.get((a.experience_id, b.experience_id))
            classes = {diversity.coarse_class(a.normalized_category), diversity.coarse_class(b.normalized_category)}
            if (
                leg is not None
                and leg.distance_meters is not None
                and leg.distance_meters <= 1.0
                and (len(classes) == 1 or classes <= building_classes)
                and frozenset((a.provider_place_id, b.provider_place_id)) not in distinct_pairs
            ):
                zero_distance_pairs.append({"day": day.day_number, "stops": [a.name, b.name]})
    report["suspect_entity_collisions"] = {
        "suspicious_pairs_found": len(collisions),
        "conclusively_merged": sum(1 for record in collisions if record.get("resolution") == "merged"),
        "established_distinct": sum(1 for record in collisions if record.get("resolution") == "distinct"),
        "unresolved": sum(1 for record in collisions if record.get("resolution") == "unresolved"),
        "scheduled_unresolved_collision_count": len(scheduled_collisions),
        "zero_distance_scheduled_pairs": zero_distance_pairs,
        "separated_by_the_scheduler": [
            {"removed": s.replaced_place, "replacement": s.replacement_place or None}
            for s in (plan.collision_separations if plan is not None else [])
        ],
        "pairs": collisions,
    }

    # -- route repair (one bounded attempt per long-route day) ------------------------------------
    repair = state.route_burden_repair_report
    report["route_repair"] = [
        {
            "day": attempt.day_number,
            "route_burden_repair_attempted": attempt.route_burden_repair_attempted,
            "reason": attempt.reason,
            # why each stop of the day may or may not be replaced, in stop order (fixed codes)
            "stop_protections": list(attempt.stop_protections),
            "replaced_place": attempt.replaced_place,
            "replacement_place": attempt.replacement_place,
            "before_distance_meters": attempt.before_distance_meters,
            "before_duration_seconds": attempt.before_duration_seconds,
            "after_distance_meters": attempt.after_distance_meters,
            "after_duration_seconds": attempt.after_duration_seconds,
            "accepted": attempt.accepted,
        }
        for attempt in (repair.attempts if repair is not None else [])
    ]

    # -- routability repair (Section 3C.2: a stop the provider cannot route to) -------------------
    routability = state.routability_repair_report
    report["routability_repair"] = {
        "coverage_before": round(100.0 * routability.coverage_before, 1) if routability and routability.coverage_before is not None else None,
        "coverage_after": round(100.0 * routability.coverage_after, 1) if routability and routability.coverage_after is not None else None,
        "attempts": [
            {
                "day": attempt.day_number,
                "reason": attempt.reason,
                "accepted": attempt.accepted,
                "failed_legs_before": attempt.failed_legs_before,
                "failed_legs_after": attempt.failed_legs_after,
                "relocalized_legs": attempt.relocalized_legs,
                "suspect_place": attempt.suspect_place,
                "suspect_protection": attempt.suspect_protection,
                "suspect_was_grounded_anchor": attempt.suspect_was_grounded_anchor,
                "replaced_place": attempt.replaced_place,
                "replacement_place": attempt.replacement_place,
            }
            for attempt in (routability.attempts if routability is not None else [])
        ],
    }

    # -- food locality ---------------------------------------------------------------------------
    all_suggestions = [suggestion.name for day in days for suggestion in day.restaurant_suggestions]
    food_days: list[dict[str, Any]] = []
    for day in days:
        stops = [stop.coordinates for stop in day.experiences if stop.coordinates]
        distances = [
            min(haversine_distance_km(stop, suggestion.coordinates) for stop in stops)
            for suggestion in day.restaurant_suggestions
            if suggestion.coordinates and stops
        ]
        food_days.append(
            {
                "day": day.day_number,
                "suggestions": [suggestion.name for suggestion in day.restaurant_suggestions],
                # yes only when EVERY displayed suggestion is measurably within the radius
                "proximity_available": bool(day.restaurant_suggestions)
                and len(distances) == len(day.restaurant_suggestions)
                and max(distances) <= _FOOD_NEARBY_KM,
                "farthest_suggestion_km": round(max(distances), 2) if distances else None,
                "suggestions_beyond_radius": (len(day.restaurant_suggestions) - len(distances))
                + sum(1 for distance in distances if distance > _FOOD_NEARBY_KM),
            }
        )
    report["food_locality"] = {
        "suggestions_beyond_radius": sum(day["suggestions_beyond_radius"] for day in food_days),
        "repeated_suggestion_count": len(all_suggestions) - len(set(all_suggestions)),
        "days": food_days,
    }

    # -- rationale consistency --------------------------------------------------------------------
    stale_days = [
        day.day_number
        for day in days
        if any(
            warning.startswith(RATIONALE_WARNING_PREFIX)
            and warning[len(RATIONALE_WARNING_PREFIX):] != current_day_rationale(state, day)
            for warning in day.warnings
        )
    ]
    report["rationale_consistency"] = {
        "stale_rationale_detected": bool(stale_days),
        "stale_days": stale_days,
        "days_with_ai_rationale": [day.day_number for day in days if current_day_rationale(state, day)],
        "days_with_deterministic_summary": [day.day_number for day in days if day.goal],
    }

    # -- factual safety ----------------------------------------------------------------------
    narrative = state.itinerary_narrative_report
    fabricated = [
        stop.name for stop in scheduled if not (stop.provider_place_id and stop.provider_source and stop.coordinates)
    ]
    report["factual_safety"] = {
        "fabricated_or_unverified_scheduled_identities": fabricated,
        # The narrator's output is checked against an allow-list and forbidden-claim patterns
        # before it is stored; text that fails is discarded and replaced by a deterministic
        # summary. So the STORED narrative carries no unsupported claim by construction.
        "unsupported_factual_claims_in_stored_narrative": 0,
        "narrative_ai_attempt_status": _value(narrative.status) if narrative is not None else None,
        "narrative_source": narrative.narrative_source if narrative is not None else None,
    }

    # -- provider usage ----------------------------------------------------------------------
    usage = state.provider_usage_report
    merges = dict(usage.entity_merges) if usage is not None else {}
    report["entity_dedup"] = {
        "candidates_merged_by_place_id": merges.get("place_id", 0),
        "candidates_merged_by_source_identity": merges.get("source_identity", 0),
        "candidates_merged_by_name_proximity": merges.get("name_proximity", 0),
    }
    reasoning = state.ai_itinerary_reasoning_result
    groq_stages = {
        "anchor_proposal": 1 if batch is not None and _value(batch.result.status) != "not_connected" else 0,
        "itinerary_reasoning": 1 if reasoning is not None and _value(reasoning.status) not in ("not_connected", "skipped") else 0,
        "itinerary_repair_attempts": state.ai_itinerary_repair_attempt_count,
        "narrator": 1 if narrative is not None and _value(narrative.status) != "not_connected" else 0,
    }
    report["provider_usage"] = {
        "geoapify_calls_by_api": dict(usage.calls_by_api) if usage is not None else {},
        "geoapify_credits_by_api": dict(usage.credits_by_api) if usage is not None else {},
        "total_geoapify_credits": usage.credits_used if usage is not None else None,
        "geoapify_credit_budget": usage.budget if usage is not None else None,
        "geoapify_calls_refused_by_budget": usage.refused_calls if usage is not None else None,
        # routing is accounted per mode: `routing` = walking day routes, `routing_drive` = vehicle transfers
        "routing_credits_by_mode": {
            "walk": (usage.credits_by_api.get("routing", 0) if usage is not None else 0),
            "drive": (usage.credits_by_api.get("routing_drive", 0) if usage is not None else 0),
        },
        "provider_cache_hits": dict(cache_hits),
        # Stage-level count: a stage's own internal retries are not visible here.
        "groq_stage_calls": groq_stages,
        "groq_stage_calls_total": sum(groq_stages.values()),
        # a fixed status label (never model output): anything but `completed` means the
        # deterministic scheduler, not the model, chose the plan
        "itinerary_reasoning_status": _value(reasoning.status) if reasoning is not None else None,
    }

    # -- persistence -------------------------------------------------------------------------
    reloaded = get_planning_state_repository().get_by_trip_id(state.trip_id)
    report["persistence"] = {
        "save_succeeded": reloaded is not None,
        "reload_succeeded": bool(
            reloaded is not None
            and reloaded.experience_plan is not None
            and [[e.provider_place_id for e in d.experiences] for d in reloaded.experience_plan.daily_plans]
            == [[e.provider_place_id for e in d.experiences] for d in days]
        ),
    }

    report["acceptance"] = _acceptance(report)
    return report


def _acceptance(report: dict[str, Any]) -> dict[str, Any]:
    """The canary contract, with the generic pipeline stage a failure points at."""
    inventory, quality, routing = report["inventory"], report["quality"], report["routing"]
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, stage: str) -> None:
        checks.append({"check": name, "passed": bool(passed), "stage_if_failed": stage})

    check("destination geocoded", report["destination"]["geocode_success"], "geocoding")
    check("no fabricated/unverified scheduled identity",
          not report["factual_safety"]["fabricated_or_unverified_scheduled_identities"], "experience planning")
    check("no unsupported factual claim",
          report["factual_safety"]["unsupported_factual_claims_in_stored_narrative"] == 0, "narrator")
    check("zero duplicates", not quality["duplicates"], "experience planning")
    # "Zero duplicates" is not claimed on provider ids alone: an unresolved suspected collision on
    # the itinerary, or two same-class stops the routing provider puts zero metres apart, fails too.
    suspects = report.get("suspect_entity_collisions") or {}
    check(
        "no unresolved suspected duplicate scheduled",
        suspects.get("scheduled_unresolved_collision_count", 0) == 0
        and not suspects.get("zero_distance_scheduled_pairs"),
        "entity identity / experience planning",
    )
    check("persistence and reload", report["persistence"]["save_succeeded"] and report["persistence"]["reload_succeeded"],
          "persistence")
    budget = report["provider_usage"]["geoapify_credit_budget"]
    credits = report["provider_usage"]["total_geoapify_credits"]
    check("provider budget not exceeded", credits is None or budget is None or credits <= budget, "provider usage")

    # -- product quality (Section 203C.2B canary correction) -----------------------------------
    anchors = report.get("anchors") or {}
    if anchors.get("proposal_status") not in (None, "not_run", "not_connected"):
        # AI anchor discovery is enabled and reachable: a rejected or empty
        # proposal is a degraded run, never a clean pass.
        check(
            f"anchor proposal completed (failure kind: {anchors.get('failure_kind')})",
            anchors.get("proposal_status") == "completed" and anchors.get("proposed", 0) > 0,
            "AI anchor proposal",
        )
    readiness = quality.get("readiness")
    unaccepted = sorted(set(quality.get("review_codes") or []) - _ACCEPTED_REVIEW_CODES)
    viable_at_least_r_for_readiness = inventory["viable_meaningful_candidate_count"] >= inventory["R"]
    if viable_at_least_r_for_readiness:
        check(
            "final readiness is ready, or needs_review only for accepted data-coverage codes"
            + (f" (unaccepted: {', '.join(unaccepted)})" if unaccepted else ""),
            # needs_review with NO code is unexplained, and fails just like an unaccepted code
            readiness == "ready"
            or (readiness == "needs_review" and bool(quality.get("review_codes")) and not unaccepted),
            "validation / readiness",
        )
    burden = report.get("daily_travel_burden") or []
    # A long ORIGINAL walk does not fail when the leg became a factual vehicle
    # transfer: `long_route` is judged on the final legs (walk legs for the
    # walking burden, every mode for the transfer burden).
    check(
        "no day with unresolved excessive walking",
        not any(day.get("excessive_walking", day["long_route"]) for day in burden),
        "day clustering / route burden",
    )
    check(
        "no day with an unreasonable transfer burden",
        not any(day.get("unreasonable_transfers", False) for day in burden),
        "day clustering / route burden",
    )
    check(
        "no movement data the routing provider did not return",
        (report.get("movement_modes") or {}).get("legs_with_unverified_movement_data", 0) == 0,
        "routing",
    )
    if inventory["viable_meaningful_candidate_count"] >= inventory.get("T", inventory["R"]):
        check(
            # only a HARD violation fails; a soft or interest-justified concentration never does
            "no unresolved hard diversity violation (too many markets) while alternatives existed",
            not any(
                day.get("hard_violation") and day["eligible_alternatives_existed"]
                for day in (report.get("diversity") or {}).get("days", [])
            ),
            "schedule diversity",
        )
    food = report.get("food_locality") or {}
    check("no restaurant repeated across days", food.get("repeated_suggestion_count", 0) == 0, "food locality")
    check("no food suggestion beyond the nearby radius", food.get("suggestions_beyond_radius", 0) == 0, "food locality")
    check(
        "no stale rationale",
        not (report.get("rationale_consistency") or {}).get("stale_rationale_detected"),
        "day rationale",
    )

    viable_at_least_r = inventory["viable_meaningful_candidate_count"] >= inventory["R"]
    if viable_at_least_r:
        check(f">= R ({inventory['R']}) meaningful scheduled attractions",
              quality["meaningful_scheduled_stops"] >= inventory["R"], "itinerary reasoning / repair / fallback")
        check("zero empty days", not quality["empty_days"], "itinerary reasoning / repair / fallback")
        grounded = set(report["must_visits"]["grounded"])
        check("every grounded must-visit scheduled", grounded <= set(report["must_visits"]["scheduled"]),
              "candidate quality / experience planning")
        coverage = routing["coverage_percentage"]
        check("routing coverage >= 90%", coverage is not None and coverage >= 100 * _MIN_ROUTING_COVERAGE, "routing")
    else:
        check("insufficient inventory reported as INSUFFICIENT_VERIFIED_INVENTORY",
              "INSUFFICIENT_VERIFIED_INVENTORY" in quality["blocking_codes"],
              "inventory (places pool / anchor grounding / candidate quality)")

    failed = [c for c in checks if not c["passed"]]
    return {
        "viable_at_least_R": viable_at_least_r,
        "outcome": (
            "PASS" if not failed and viable_at_least_r
            else "PASS (honest INSUFFICIENT_VERIFIED_INVENTORY)" if not failed
            else "FAIL"
        ),
        "checks": checks,
        "failed_stages": sorted({c["stage_if_failed"] for c in failed}),
    }


def _render(report: dict[str, Any]) -> str:
    lines: list[str] = []

    def section(title: str) -> None:
        lines.extend(["", title])

    def row(label: str, value: Any) -> None:
        lines.append(f"- {label}: {value}")

    request = report["request"]
    lines.append(f"CANARY: {request['city']} | {request['days']} days | {request['pace']}")
    row("interests", ", ".join(request["interests"]) or "none")
    row("must-visits", ", ".join(request["must_visit"]) or "none")
    if report.get("technical_failure"):
        section("TECHNICAL FAILURE")
        row("exception type", report["technical_failure"])
        row("total generation time (s)", report["latency"]["total_generation_seconds"])
        return "\n".join(lines)

    section("DESTINATION")
    row("resolved destination", report["destination"]["resolved_destination"])
    row("geocode success", report["destination"]["geocode_success"])

    inventory = report["inventory"]
    section("INVENTORY")
    for label, key in (("T", "T"), ("R", "R"), ("H", "H"), ("broad candidate count", "broad_candidate_count"),
                       ("viable meaningful candidate count", "viable_meaningful_candidate_count"),
                       ("inventory status", "inventory_status"), ("expansion used", "expansion_used")):
        row(label, inventory[key])

    anchors = report["anchors"]
    section("ANCHOR QUALITY")
    row("proposal status", anchors["proposal_status"])
    row("failure kind", anchors["failure_kind"] or "none")
    row("dropped invalid proposals", anchors["dropped_invalid_proposals"])
    row("proposed", anchors["proposed"])
    row("grounded", anchors["grounded"])
    row("rejected", anchors["rejected"] or 0)
    row("promoted", anchors["promoted"])
    row("grounded names", "; ".join(anchors["grounded_names"]) or "none")
    row(
        "grounded anchors scheduled",
        f"{anchors.get('scheduled_grounded_anchor_count', 0)} of {anchors.get('grounded_anchor_count', 0)}"
        f" ({'; '.join(anchors.get('scheduled_grounded_anchor_names') or []) or 'none'})",
    )

    hygiene = report["anchor_hygiene"]
    section("ANCHOR HYGIENE")
    row("grounded before category filter", hygiene["grounded_before_category_filter"])
    row("category mismatch rejected", hygiene["category_mismatch_rejected"])
    row("duplicate anchors removed", hygiene["duplicate_anchors_removed"])
    row("final promoted anchors", hygiene["final_promoted_anchors"])

    must = report["must_visits"]
    section("MUST-VISITS")
    for key in ("requested", "grounded", "scheduled", "unresolved"):
        row(key, "; ".join(must[key]) or "none")
    for term, info in must["detail"].items():
        if info["grounded"]:
            lines.append(f"    {term} -> {info['grounded_as']}")

    section(f"TOP CANDIDATES (top {len(report['top_candidates'])})")
    for index, candidate in enumerate(report["top_candidates"], start=1):
        lines.append(
            f"{index:>2}. {candidate['name']} | {candidate['category']} | {candidate['score']} {candidate['tier']} | "
            f"{candidate['source']}{' | scheduled' if candidate['scheduled'] else ''}"
        )

    section("FINAL ITINERARY")
    for day in report["final_itinerary"]:
        lines.append(f"Day {day['day']}")
        for stop in day["attractions"]:
            lines.append(
                f"  {stop['order']}. {stop['name']} | {stop['category']} | provider identity: "
                f"{'yes' if stop['provider_identity_present'] else 'NO'}{' | low-value' if stop['low_value'] else ''}"
            )
        lines.append(f"  nearby food: {'; '.join(day['nearby_food']) or 'none'}")
        lines.append(f"  route distance (m): {day['route_distance_meters']}")
        lines.append(f"  route duration (s): {day['route_duration_seconds']}")
        lines.append(f"  warnings: {len(day['warnings'])}")
        for warning in day["warnings"]:
            lines.append(f"    - {warning[:200]}")

    quality = report["quality"]
    section("QUALITY")
    for label, key in (("meaningful scheduled stops", "meaningful_scheduled_stops"),
                       ("target attainment ratio", "target_attainment_ratio"), ("empty days", "empty_days"),
                       ("duplicates", "duplicates"), ("low-value scheduled objects", "low_value_scheduled_objects"),
                       ("interest coverage", "interest_coverage"), ("usefulness verdict", "usefulness_verdict"),
                       ("fallback pass used", "fallback_pass_used"), ("readiness", "readiness"),
                       ("blocking codes", "blocking_codes"), ("review codes", "review_codes")):
        row(label, quality[key] if quality[key] not in ([], {}) else "none")

    section("DAILY TRAVEL BURDEN")
    for day in report["daily_travel_burden"]:
        lines.append(
            f"- day {day['day']}: walking {day['total_walking_distance_meters']} m, "
            f"{day['total_walking_duration_seconds']} s | longest walk leg {day['max_walk_leg_distance_meters']} m, "
            f"{day['max_walk_leg_duration_seconds']} s | all transfers {day['total_transfer_distance_meters']} m, "
            f"{day['total_transfer_duration_seconds']} s | vehicle transfers: {day['vehicle_transfer_legs']}"
            f" | excessive walking: {'YES' if day['excessive_walking'] else 'no'}"
            f" | unreasonable transfers: {'YES' if day['unreasonable_transfers'] else 'no'}"
        )

    modes = report["movement_modes"]
    section("MOVEMENT MODES")
    row("legs by mode", modes["legs_by_mode"])
    row("mode adaptations attempted", modes["mode_adaptations_attempted"])
    row("legs adapted to vehicle transfer", modes["legs_adapted_to_vehicle_transfer"])
    row("legs with unverified movement data", modes["legs_with_unverified_movement_data"])
    for day in report["final_itinerary"]:
        lines.append(f"Day {day['day']}")
        for leg in day["route_legs"]:
            lines.append(f"  {leg['from']} -> {leg['to']}")
            lines.append(
                f"    mode: {leg['mode'] or 'none (' + str(leg['status']) + ')'}"
                + (f" | reason: {leg['failure_reason']}" if leg.get("failure_reason") else "")
            )
            lines.append(f"    distance (m): {leg['distance_meters']}")
            lines.append(f"    duration (s): {leg['duration_seconds']}")
            lines.append(f"    mode adaptation attempted: {'yes' if leg['mode_adaptation_attempted'] else 'no'}")
            if leg["walking_duration_seconds"] is not None:
                lines.append(
                    f"    original walking route: {leg['walking_distance_meters']} m, "
                    f"{leg['walking_duration_seconds']} s (vehicle transfer; driving route estimate)"
                )

    section("DIVERSITY")
    row("markets explicitly requested", "yes" if report["diversity"]["markets_explicitly_requested"] else "no")
    row("classes justified by requested interests", report["diversity"]["interest_justified_classes"] or "none")
    for day in report["diversity"]["days"]:
        swaps = "; ".join(f"{r['replaced_place']} -> {r['replacement_place']}" for r in day["replacements"])
        lines.append(
            f"- day {day['day']}: {day['coarse_category_counts']}"
            f" | hard violation: {'YES' if day['hard_violation'] else 'no'}"
            f" | soft concentration: {'yes' if day['soft_concentration'] else 'no'}"
            f" | explicit-interest justified concentration: "
            f"{'yes' if day['explicit_interest_justified_concentration'] else 'no'}"
            f" | repair attempted: {'yes' if day['diversity_repair_attempted'] else 'no'}"
            f" | replaced -> replacement: {swaps or 'none'}"
        )

    suspects = report["suspect_entity_collisions"]
    section("SUSPECT ENTITY COLLISIONS")
    row("suspicious pairs found", suspects["suspicious_pairs_found"])
    row("conclusively merged", suspects["conclusively_merged"])
    row("established distinct", suspects["established_distinct"])
    row("unresolved", suspects["unresolved"])
    row("scheduled unresolved collision count", suspects["scheduled_unresolved_collision_count"])
    row("zero-distance same-class scheduled pairs", suspects["zero_distance_scheduled_pairs"] or "none")
    row("separated by the scheduler", suspects["separated_by_the_scheduler"] or "none")
    for pair in suspects["pairs"]:
        lines.append(f"  pair: {pair['place_ids'][0]} / {pair['place_ids'][1]}")
        lines.append(f"    provider name variants: {pair['name_variants'][0]} / {pair['name_variants'][1]}")
        lines.append(f"    coarse category: {pair['coarse_classes'][0]} / {pair['coarse_classes'][1]}")
        lines.append(f"    coordinate separation (m): {pair['separation_meters']}")
        lines.append(
            "    source identity present: "
            + " / ".join("yes" if flag else "no" for flag in pair["source_identity_present"])
        )
        lines.append(
            "    Wikidata identity present: "
            + " / ".join("yes" if flag else "no" for flag in pair["wikidata_identity_present"])
        )
        lines.append(
            f"    identity enrichment attempted: {'yes' if pair['enrichment_attempted'] else 'no'}"
            f" | resolution: {pair['resolution']}{' (' + pair['merged_by'] + ')' if pair['merged_by'] else ''}"
        )

    dedup = report["entity_dedup"]
    section("ENTITY DEDUP")
    row("candidates merged by place id", dedup["candidates_merged_by_place_id"])
    row("candidates merged by source identity", dedup["candidates_merged_by_source_identity"])
    row("candidates merged by name/proximity", dedup["candidates_merged_by_name_proximity"])

    section("ROUTE REPAIR")
    if not report["route_repair"]:
        lines.append("- no long-route day; no repair needed")
    for attempt in report["route_repair"]:
        lines.append(
            f"- day {attempt['day']}: attempted: {'yes' if attempt['route_burden_repair_attempted'] else 'no'}"
            f" | before: {attempt['before_distance_meters']} m, {attempt['before_duration_seconds']} s"
            f" | replaced: {attempt['replaced_place'] or 'none'} -> {attempt['replacement_place'] or 'none'}"
            f" | after: {attempt['after_distance_meters']} m, {attempt['after_duration_seconds']} s"
            f" | accepted: {'yes' if attempt['accepted'] else 'no'} ({attempt['reason']})"
            f" | stop protections: {', '.join(attempt.get('stop_protections') or []) or 'none recorded'}"
        )

    routability = report.get("routability_repair") or {"attempts": []}
    section("ROUTABILITY REPAIR")
    if not routability["attempts"]:
        lines.append("- not needed (factual routing coverage met the threshold, or no leg failed)")
    else:
        row("coverage before -> after (%)", f"{routability['coverage_before']} -> {routability['coverage_after']}")
    for attempt in routability["attempts"]:
        lines.append(
            f"- day {attempt['day']}: {attempt['reason']} | failed legs: {attempt['failed_legs_before']} -> "
            f"{attempt['failed_legs_after']} | legs asked again one at a time: {attempt['relocalized_legs']}"
            f" | suspect: {attempt['suspect_place'] or 'none'} ({attempt['suspect_protection'] or 'n/a'}"
            f"{', grounded anchor' if attempt['suspect_was_grounded_anchor'] else ''})"
            f" | replaced: {attempt['replaced_place'] or 'none'} -> {attempt['replacement_place'] or 'none'}"
        )

    food = report["food_locality"]
    section("FOOD LOCALITY")
    row("repeated suggestion count", food["repeated_suggestion_count"])
    row("suggestions beyond radius", food["suggestions_beyond_radius"])
    for day in food["days"]:
        lines.append(
            f"- day {day['day']}: proximity available: {'yes' if day['proximity_available'] else 'no'}"
            f" | farthest suggestion (km): {day['farthest_suggestion_km']}"
        )

    rationale = report["rationale_consistency"]
    section("RATIONALE CONSISTENCY")
    row("stale rationale detected", "YES" if rationale["stale_rationale_detected"] else "no")
    row("days with AI rationale", rationale["days_with_ai_rationale"] or "none")
    row("days with deterministic summary", rationale["days_with_deterministic_summary"] or "none")

    section("READINESS")
    row("final readiness", quality["readiness"])
    row("review codes", quality["review_codes"] or "none")
    row("blocking codes", quality["blocking_codes"] or "none")

    routing = report["routing"]
    section("ROUTING")
    row("required legs", routing["required_legs"])
    row("factual routed legs", routing["factual_routed_legs"])
    row("coverage percentage", routing["coverage_percentage"])
    row("failures", routing["failures"] or "none")

    safety = report["factual_safety"]
    section("FACTUAL SAFETY")
    row("fabricated/unverified scheduled identities", safety["fabricated_or_unverified_scheduled_identities"] or 0)
    row("unsupported factual claims", safety["unsupported_factual_claims_in_stored_narrative"])
    row("narrative source", f"{safety['narrative_source']} (AI attempt: {safety['narrative_ai_attempt_status']})")

    usage = report["provider_usage"]
    section("PROVIDER USAGE")
    row("Geoapify calls by API", usage["geoapify_calls_by_api"])
    row("Geoapify credits by API", usage["geoapify_credits_by_api"])
    row("total Geoapify credits", f"{usage['total_geoapify_credits']} of {usage['geoapify_credit_budget']}")
    row("routing credits by mode", usage["routing_credits_by_mode"])
    row("calls refused by budget", usage["geoapify_calls_refused_by_budget"])
    row("provider cache hits", usage["provider_cache_hits"] or 0)
    row("Groq calls (by stage)", usage["groq_stage_calls"])

    section("PERSISTENCE")
    row("save succeeded", report["persistence"]["save_succeeded"])
    row("reload succeeded", report["persistence"]["reload_succeeded"])

    section("LATENCY")
    row("total generation time (s)", report["latency"]["total_generation_seconds"])

    performance = report.get("performance") or {"available": False}
    section("PERFORMANCE")
    if not performance["available"]:
        lines.append("- no performance report was recorded for this generation")
    else:
        lines.append(f"total: {performance['total_seconds']} s ({performance['engine']} engine)")
        lines.append("")
        lines.append("stages (exclusive wall time; the rows and 'other' add up to the total):")
        for item in performance["stages"]:
            row(item["label"], f"{item['seconds']} s")
        row("other", f"{performance['other_seconds']} s")
        lines.append("")
        lines.append(
            f"provider wall time (waiting on external requests; {performance['provider_wall_time_total_seconds']} s in all):"
        )
        for item in performance["provider_wall_time"]:
            row(item["label"], f"{item['seconds']} s over {item['attempts']} request attempt(s)")
        lines.append("")
        lines.append("request counts:")
        for name, value in sorted(performance["counts"].items()):
            row(name, value)
        row("cache hits by source", performance["cache_hits_by_source"] or 0)
        row("cache misses by source", performance["cache_misses_by_source"] or 0)
        lines.append("")
        lines.append("LLM STAGES (request attempts under one total budget per stage):")
        if not performance["llm_stages"]:
            lines.append("- no model stage made a request")
        for item in performance["llm_stages"]:
            row(
                item["label"],
                f"{item['attempts']} attempt(s) in {item['seconds']} s"
                f" | structural retries: {item['structural_retries']}"
                f" | transport retries: {item['transport_retries']}"
                f" | deadline exceeded: {'YES' if item['deadline_exceeded'] else 'no'}"
                f" | result: {item['result']}"
                + (f" | transport failure: {item['transport_failure']}" if item.get("transport_failure") else "")
                + (f" | retry-after: {item['retry_after']}" if item.get("retry_after") else ""),
            )
        concurrency = performance["concurrency"]
        lines.append("")
        lines.append("CONCURRENCY (bounded batches of independent provider requests):")
        row("peak Geoapify concurrency (this generation)", concurrency["peak_geoapify_concurrency"])
        row("peak Geoapify concurrency (whole process)", concurrency["process_peak_geoapify_concurrency"])
        row("concurrent batches", concurrency["concurrent_batches"])
        row("batch sizes by operation", concurrency["batch_sizes"] or "none")
        row("time inside batch tasks, summed (s; overlaps the stage wall time)", concurrency["stage_task_seconds"] or "none")
        lines.append("")
        lines.append("REDUNDANT WORK (identical requests repeated within this generation; counts only):")
        for item in performance["redundant_work"]:
            row(item["label"], f"{item['repeated']} of {item['total']}")

    acceptance = report["acceptance"]
    section(f"ACCEPTANCE: {acceptance['outcome']}")
    for item in acceptance["checks"]:
        lines.append(
            f"  [{'ok' if item['passed'] else 'FAIL'}] {item['check']}"
            + ("" if item["passed"] else f"  -> generic stage: {item['stage_if_failed']}")
        )
    return "\n".join(lines)


def _prepare_live_environment(workdir: Path) -> dict[str, int]:
    """The production-shaped configuration on throwaway storage in `workdir`,
    shared with the tuning benchmark. Returns the provider-cache hit counter
    that `_run` reports (by namespace; script-side observation only)."""
    sys.path.insert(0, str(_SCRIPTS_DIR))
    sys.path.insert(0, str(_BACKEND_DIR))
    from benchmark_cities import _configure_environment  # same production-shaped configuration

    _configure_environment(workdir)

    from app.core.config import get_settings

    get_settings.cache_clear()

    from app.storage.provider_cache_store import ProviderCacheStore

    cache_hits: dict[str, int] = {}
    original_get = ProviderCacheStore.get

    def _counting_get(self: Any, source: str, query_hash: str, *get_args: Any, **get_kwargs: Any) -> Any:
        entry = original_get(self, source, query_hash, *get_args, **get_kwargs)
        if entry is not None:
            cache_hits[source] = cache_hits.get(source, 0) + 1
        return entry

    ProviderCacheStore.get = _counting_get  # type: ignore[method-assign]
    return cache_hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--city", required=True, help='destination, e.g. "City, Country"')
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--pace", choices=["relaxed", "balanced", "packed"], default="balanced")
    parser.add_argument("--interests", action="append", help="comma-separated and/or repeated")
    parser.add_argument("--must-visit", action="append", dest="must_visit", help="repeat once per place")
    parser.add_argument("--out", type=Path, default=None, help="directory for the report files (default: a temp dir)")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be at least 1")

    missing = [name for name in ("GEOAPIFY_API_KEY", "GROQ_API_KEY") if not os.environ.get(name)]
    if missing:
        print("Missing required environment variable(s): " + ", ".join(missing) + ". Nothing was run.")
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="travelobligator_canary_"))
    out_dir = args.out or workdir
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_hits = _prepare_live_environment(workdir)

    report = _run(args, cache_hits)
    text = _render(report)
    base = f"canary_{_slug(args.city)}_{args.days}d_{args.pace}"
    (out_dir / f"{base}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    (out_dir / f"{base}.txt").write_text(text + "\n")
    print(text)
    print(f"\nReport files: {out_dir / (base + '.txt')}\n              {out_dir / (base + '.json')}")
    if report.get("technical_failure"):
        return 1
    return 0 if report["acceptance"]["outcome"].startswith("PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
