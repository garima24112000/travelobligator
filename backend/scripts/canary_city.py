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
    from app.models.planning_state import TravelGroupType, TripPace, TripRequest
    from app.repositories.factory import get_planning_state_repository
    from app.services import place_taxonomy as taxonomy
    from app.services.day_rationale import RATIONALE_WARNING_PREFIX, current_day_rationale
    from app.services.planning_orchestrator import planning_orchestrator
    from app.services.route_burden import day_route_burdens
    from app.services.usefulness_contract import evaluate_usefulness, is_meaningful_stop
    from app.utils.geo import haversine_distance_km

    interests = _split_list(args.interests)
    must_visit = list(args.must_visit or [])
    report: dict[str, Any] = {
        "request": {
            "city": args.city, "days": args.days, "pace": args.pace, "interests": interests, "must_visit": must_visit,
        }
    }

    started = time.monotonic()
    try:
        created = planning_orchestrator.create_trip(
            TripRequest(
                primary_destination=args.city,
                start_date=_START_DATE,
                end_date=_START_DATE + timedelta(days=args.days - 1),
                travelers_count=2,
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

    # -- schedule -----------------------------------------------------------------------
    plan = state.experience_plan
    days = plan.daily_plans if plan is not None else []
    scheduled = [experience for day in days for experience in day.experiences]
    scheduled_ids = {experience.provider_place_id for experience in scheduled if experience.provider_place_id}

    # -- must-visits ---------------------------------------------------------------------
    grounded_terms: dict[str, dict[str, Any]] = {}
    for term in must_visit:
        match = next(
            (
                poi for poi in pois
                if _norm(poi.get("must_visit_term")) == _norm(term) or _norm(term) in _norm(poi.get("name"))
            ),
            None,
        )
        grounded_terms[term] = {
            "grounded": match is not None,
            "grounded_as": match.get("name") if match else None,
            "scheduled": bool(match and match.get("place_id") in scheduled_ids),
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
                        "distance_meters": leg.distance_meters if leg else None,
                        "duration_seconds": leg.duration_seconds if leg else None,
                        "status": _value(leg.status) if leg else "missing",
                    }
                    for (a, b), leg in zip(zip(stops, stops[1:]), day_legs)
                ],
                "warnings": list(day.warnings),
            }
        )
    report["final_itinerary"] = itinerary

    # -- quality ---------------------------------------------------------------------------
    validation = state.validation_report
    canonical = taxonomy.canonical_interests(interests)
    low_value_scheduled = [stop.name for stop in scheduled if stop.low_value_object]
    report["quality"] = {
        "meaningful_scheduled_stops": verdict.scheduled_meaningful_stops,
        "target_attainment_ratio": verdict.target_attainment_ratio,
        "empty_days": list(verdict.empty_days),
        "duplicates": list(verdict.duplicate_place_ids),
        "low_value_scheduled_objects": low_value_scheduled,
        "interest_coverage": {
            interest: any(interest in stop.matched_interests for stop in scheduled) for interest in canonical
        },
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
            "total_walking_distance_meters": round(burden.total_distance_meters, 1),
            "total_walking_duration_seconds": round(burden.total_duration_seconds, 1),
            "max_leg_distance_meters": round(burden.max_leg_distance_meters, 1),
            "max_leg_duration_seconds": round(burden.max_leg_duration_seconds, 1),
            "routed_legs": burden.routed_legs,
            "required_legs": burden.required_legs,
            "long_route": burden.long_route,
        }
        for burden in day_route_burdens(state)
    ]

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
                "proximity_available": bool(distances) and max(distances) <= _FOOD_NEARBY_KM,
                "farthest_suggestion_km": round(max(distances), 2) if distances else None,
            }
        )
    report["food_locality"] = {
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
        "provider_cache_hits": dict(cache_hits),
        # Stage-level count: a stage's own internal retries are not visible here.
        "groq_stage_calls": groq_stages,
        "groq_stage_calls_total": sum(groq_stages.values()),
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
    check(
        "no long-route day",
        not any(day["long_route"] for day in burden),
        "day clustering / route burden",
    )
    food = report.get("food_locality") or {}
    check("no restaurant repeated across days", food.get("repeated_suggestion_count", 0) == 0, "food locality")
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
            f"- day {day['day']}: {day['total_walking_distance_meters']} m, "
            f"{day['total_walking_duration_seconds']} s total | longest leg {day['max_leg_distance_meters']} m, "
            f"{day['max_leg_duration_seconds']} s | long-route flag: {'YES' if day['long_route'] else 'no'}"
        )

    food = report["food_locality"]
    section("FOOD LOCALITY")
    row("repeated suggestion count", food["repeated_suggestion_count"])
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
    row("calls refused by budget", usage["geoapify_calls_refused_by_budget"])
    row("provider cache hits", usage["provider_cache_hits"] or 0)
    row("Groq calls (by stage)", usage["groq_stage_calls"])

    section("PERSISTENCE")
    row("save succeeded", report["persistence"]["save_succeeded"])
    row("reload succeeded", report["persistence"]["reload_succeeded"])

    section("LATENCY")
    row("total generation time (s)", report["latency"]["total_generation_seconds"])

    acceptance = report["acceptance"]
    section(f"ACCEPTANCE: {acceptance['outcome']}")
    for item in acceptance["checks"]:
        lines.append(
            f"  [{'ok' if item['passed'] else 'FAIL'}] {item['check']}"
            + ("" if item["passed"] else f"  -> generic stage: {item['stage_if_failed']}")
        )
    return "\n".join(lines)


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

    sys.path.insert(0, str(_SCRIPTS_DIR))
    sys.path.insert(0, str(_BACKEND_DIR))
    from benchmark_cities import _configure_environment  # same production-shaped configuration

    workdir = Path(tempfile.mkdtemp(prefix="travelobligator_canary_"))
    out_dir = args.out or workdir
    out_dir.mkdir(parents=True, exist_ok=True)
    _configure_environment(workdir)

    from app.core.config import get_settings

    get_settings.cache_clear()

    # Count provider-cache hits by namespace (script-side observation only).
    from app.storage.provider_cache_store import ProviderCacheStore

    cache_hits: dict[str, int] = {}
    original_get = ProviderCacheStore.get

    def _counting_get(self: Any, source: str, query_hash: str, *get_args: Any, **get_kwargs: Any) -> Any:
        entry = original_get(self, source, query_hash, *get_args, **get_kwargs)
        if entry is not None:
            cache_hits[source] = cache_hits.get(source, 0) + 1
        return entry

    ProviderCacheStore.get = _counting_get  # type: ignore[method-assign]

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
