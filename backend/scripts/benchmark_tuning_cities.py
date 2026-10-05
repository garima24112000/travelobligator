"""Section 3A 18-city tuning benchmark (manual, live providers).

Runs the single-city canary (`canary_city._run`, the REAL generation
pipeline) once per canonical tuning city, ONE CITY AT A TIME, with one fixed
scenario and one fixed start date, and writes per-city results plus an
aggregate summary. Benchmark tooling only: nothing here is imported by
`backend/app`, no planner logic lives here, and there is no overall score.

    cd backend
    python scripts/benchmark_tuning_cities.py --start-date 2026-10-10 \
        --out ../benchmark_results/tuning_run1 --start 1 --limit 6
    python scripts/benchmark_tuning_cities.py --start-date 2026-10-10 \
        --out ../benchmark_results/tuning_run1 --resume

Requires GEOAPIFY_API_KEY and GROQ_API_KEY in the environment. Storage is a
throwaway Local JSON store and a throwaway SQLite provider cache (no Redis,
no PostgreSQL). `--resume` skips every city whose result file in `--out` is
already complete; a city that failed technically is run again. The summary
is always rebuilt from every city result in `--out`, so batches add up.

Nothing written here carries a key, a connection URL, a request URL, a
prompt or a raw model response; `benchmark_results/` is git-ignored.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable

_SCRIPTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPTS_DIR.parent
_REPO_DIR = _BACKEND_DIR.parent
sys.path.insert(0, str(_SCRIPTS_DIR))

import canary_city  # noqa: E402  (the report builder and renderer this benchmark reuses)

SCHEMA_VERSION = 1

# The canonical tuning set, in run order. Positions (1-18) are what
# `--start` / `--limit` count.
CANONICAL_CITIES: tuple[str, ...] = (
    "New York, USA",
    "Mexico City, Mexico",
    "New Orleans, USA",
    "Buenos Aires, Argentina",
    "Medellín, Colombia",
    "Paris, France",
    "Lisbon, Portugal",
    "Kraków, Poland",
    "Córdoba, Argentina",
    "Cape Town, South Africa",
    "Marrakech, Morocco",
    "Nairobi, Kenya",
    "Jaipur, India",
    "Colombo, Sri Lanka",
    "Kyoto, Japan",
    "Hanoi, Vietnam",
    "Istanbul, Türkiye",
    "Melbourne, Australia",
)

# One scenario for every city. The start date is NOT here: it comes from the
# command line, so every city of a run uses the same trip dates.
DEFAULT_SCENARIO: dict[str, Any] = {
    "days": 3,
    "pace": "balanced",
    "travelers": 2,
    "interests": ["architecture", "history", "food"],
    "must_visit": [],
    "origin": "New York",
}

MIN_ROUTING_COVERAGE_PERCENT = 90.0
MAX_LATENCY_SECONDS = 90.0

STATUS_COMPLETED = "completed"
STATUS_TECHNICAL_FAILURE = "technical_failure"

# Manual-review flags (reasons a person should look at a city; never a score).
FLAG_TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
FLAG_ACCEPTANCE_FAIL = "ACCEPTANCE_FAIL"
FLAG_BELOW_R = "MEANINGFUL_STOPS_BELOW_R"
FLAG_EMPTY_DAY = "EMPTY_DAY"
FLAG_DUPLICATE = "DUPLICATE"
FLAG_FABRICATED = "FABRICATED_IDENTITY"
FLAG_UNSUPPORTED_CLAIM = "UNSUPPORTED_FACTUAL_CLAIM"
FLAG_ROUTING = "ROUTING_COVERAGE_BELOW_90"
FLAG_COLLISION = "UNRESOLVED_SCHEDULED_COLLISION"
FLAG_BUDGET_REFUSED = "PROVIDER_BUDGET_REFUSED_CALLS"
FLAG_LATENCY = "LATENCY_OVER_90S"
FLAG_LLM_DEADLINE = "LLM_STAGE_DEADLINE_EXCEEDED"
FLAG_NARRATOR_FALLBACK = "NARRATOR_FALLBACK"
FLAG_GENERIC_NAMES = "GENERIC_OR_LOW_VALUE_SCHEDULED_NAMES"
FLAG_UNCOVERED_INTEREST = "UNCOVERED_REQUESTED_INTEREST"
FLAG_LOW_ANCHOR_UTILIZATION = "LOW_GROUNDED_ANCHOR_UTILIZATION"

# LOW_GROUNDED_ANCHOR_UTILIZATION: the planner seeds one grounded anchor per
# day when that is feasible, so a multi-day plan that had SEVERAL grounded
# anchors to choose from (at least this many) is expected to schedule more
# than one. At most one scheduled is worth a look; fewer grounded anchors
# than this is too small a sample to call "low utilization".
LOW_ANCHOR_UTILIZATION_MIN_GROUNDED = 4
LOW_ANCHOR_UTILIZATION_MAX_SCHEDULED = 1

# -- secret safety ----------------------------------------------------------------------------

_FORBIDDEN_KEY = re.compile(
    r"api_?key|database_url|redis_url|prompt|raw_response|model_response|authorization|password|secret|token",
    re.IGNORECASE,
)
_SECRET_ENV_NAME = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|DATABASE_URL|REDIS_URL", re.IGNORECASE)
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_REDACTED = "[redacted]"
_URL_REMOVED = "[url removed]"


def _secret_values() -> list[str]:
    """The values of this process's secret-looking environment variables
    (longest first), so they can be removed from anything written."""
    values = {value for name, value in os.environ.items() if _SECRET_ENV_NAME.search(name) and len(value) >= 8}
    return sorted(values, key=len, reverse=True)


def _scrub_text(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, _REDACTED)
    return _URL.sub(_URL_REMOVED, text)


def scrub(value: Any, secrets: list[str] | None = None) -> Any:
    """A copy of `value` that is safe to keep on disk: secret-named fields
    dropped, secret values and every URL removed from strings."""
    secrets = _secret_values() if secrets is None else secrets
    if isinstance(value, dict):
        return {
            _scrub_text(str(key), secrets): scrub(item, secrets)
            for key, item in value.items()
            if not _FORBIDDEN_KEY.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [scrub(item, secrets) for item in value]
    if isinstance(value, str):
        return _scrub_text(value, secrets)
    return value


# -- selection --------------------------------------------------------------------------------


def city_slug(city: str) -> str:
    return canary_city._slug(city)


def select_cities(names: list[str] | None = None, start: int = 1, limit: int | None = None) -> list[str]:
    """The cities to run, always in canonical order. `names` picks canonical
    cities (full name, or the part before the comma); `start` is a 1-based
    position in that selection and `limit` a count from there."""
    selected = list(CANONICAL_CITIES)
    if names:
        wanted: set[str] = set()
        for name in names:
            key = canary_city._norm(name)
            matches = [
                city for city in CANONICAL_CITIES if key in (canary_city._norm(city), canary_city._norm(city.split(",")[0]))
            ]
            if not matches:
                raise ValueError(f"not a canonical tuning city: {name!r}")
            wanted.update(matches)
        selected = [city for city in CANONICAL_CITIES if city in wanted]
    if start < 1:
        raise ValueError("--start is a 1-based position")
    if limit is not None and limit < 1:
        raise ValueError("--limit must be at least 1")
    selected = selected[start - 1 :]
    return selected[:limit] if limit is not None else selected


# -- per-city metrics -------------------------------------------------------------------------


def _part(report: dict[str, Any], name: str) -> dict[str, Any]:
    value = report.get(name)
    return value if isinstance(value, dict) else {}


def _generic_or_low_value_names(scheduled: list[dict[str, Any]]) -> list[str]:
    """Scheduled stops the pipeline's OWN signals mark as doubtful: the
    low-value flag, no name, or a name that is just the stop's category
    (an unnamed object). No list of names lives here."""
    doubtful: list[str] = []
    for stop in scheduled:
        name = str(stop.get("name") or "").strip()
        category = str(stop.get("category") or "")
        if stop.get("low_value") or not name or canary_city._norm(name) == canary_city._norm(category):
            doubtful.append(name or "(unnamed)")
    return doubtful


def extract_metrics(report: dict[str, Any], *, city: str, scenario: dict[str, Any], run_timestamp: str) -> dict[str, Any]:
    """The benchmark's per-city record, taken from one canary report."""
    latency = _part(report, "latency").get("total_generation_seconds")
    metrics: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "city": city,
        "slug": city_slug(city),
        "position": CANONICAL_CITIES.index(city) + 1 if city in CANONICAL_CITIES else None,
        "run_timestamp": run_timestamp,
        "scenario": dict(scenario),
        "status": STATUS_COMPLETED,
        "technical_failure": report.get("technical_failure"),
        "performance": {"total_generation_seconds": latency},
    }
    if report.get("technical_failure") or "acceptance" not in report:
        metrics["status"] = STATUS_TECHNICAL_FAILURE
        metrics["technical_failure"] = report.get("technical_failure") or "IncompleteReport"
        metrics["acceptance"] = {"passed": False, "outcome": "FAIL"}
        metrics["manual_review_flags"] = manual_review_flags(metrics)
        return metrics

    acceptance, inventory, quality = _part(report, "acceptance"), _part(report, "inventory"), _part(report, "quality")
    routing, modes, safety = _part(report, "routing"), _part(report, "movement_modes"), _part(report, "factual_safety")
    anchors, suspects, food = _part(report, "anchors"), _part(report, "suspect_entity_collisions"), _part(report, "food_locality")
    usage, performance = _part(report, "provider_usage"), _part(report, "performance")
    concurrency = _part(performance, "concurrency")
    itinerary = report.get("final_itinerary") or []

    scheduled = [
        {
            "day": day.get("day"),
            "order": stop.get("order"),
            "name": stop.get("name"),
            "category": stop.get("category"),
            "low_value": bool(stop.get("low_value")),
            "provider_identity_present": bool(stop.get("provider_identity_present")),
        }
        for day in itinerary
        for stop in day.get("attractions") or []
    ]
    failed_checks = [check["check"] for check in acceptance.get("checks") or [] if not check.get("passed")]
    llm_stages = performance.get("llm_stages") or []
    required, routed = routing.get("required_legs") or 0, routing.get("factual_routed_legs") or 0
    legs_by_mode = modes.get("legs_by_mode") or {}
    reasoning_status = usage.get("itinerary_reasoning_status")
    narrative_source = safety.get("narrative_source")

    metrics.update(
        {
            "resolved_destination": _part(report, "destination").get("resolved_destination"),
            "destination_resolved": bool(_part(report, "destination").get("geocode_success")),
            "acceptance": {
                "passed": str(acceptance.get("outcome", "")).startswith("PASS"),
                "outcome": acceptance.get("outcome"),
                "failed_checks": failed_checks,
                "failed_stages": list(acceptance.get("failed_stages") or []),
                "readiness": quality.get("readiness"),
                "blocking_codes": list(quality.get("blocking_codes") or []),
                "review_codes": list(quality.get("review_codes") or []),
            },
            "inventory": {
                "T": inventory.get("T"),
                "R": inventory.get("R"),
                "H": inventory.get("H"),
                "broad_candidates": inventory.get("broad_candidate_count"),
                "viable_meaningful_candidates": inventory.get("viable_meaningful_candidate_count"),
                "inventory_status": inventory.get("inventory_status"),
            },
            "quality": {
                "meaningful_scheduled_stops": quality.get("meaningful_scheduled_stops"),
                "target_attainment_ratio": quality.get("target_attainment_ratio"),
                "empty_days": list(quality.get("empty_days") or []),
                "duplicate_count": len(quality.get("duplicates") or []),
                "low_value_scheduled_count": len(quality.get("low_value_scheduled_objects") or []),
                "interest_coverage": quality.get("interest_coverage"),
                # requested interests the final plan does not serve (the validator's own rule)
                "requested_interests_uncovered": sorted(
                    name for name, covered in (quality.get("interest_coverage") or {}).items() if covered is False
                ),
            },
            "factual_safety": {
                "fabricated_or_unverified_scheduled_identities": len(
                    safety.get("fabricated_or_unverified_scheduled_identities") or []
                ),
                "unsupported_factual_claims": safety.get("unsupported_factual_claims_in_stored_narrative") or 0,
            },
            "anchors": {
                "proposed": anchors.get("proposed"),
                "grounded": anchors.get("grounded"),
                "promoted": anchors.get("promoted"),
                "failure_kind": anchors.get("failure_kind"),
                # grounded = provider-grounded AND promoted; scheduled = of those, on the final plan
                "grounded_anchor_count": anchors.get("grounded_anchor_count"),
                "scheduled_grounded_anchor_count": anchors.get("scheduled_grounded_anchor_count"),
                "grounded_anchor_schedule_ratio": anchors.get("grounded_anchor_schedule_ratio"),
                # the kind of transport failure of the anchor proposal stage, when it had one
                "transport_failure": next(
                    (stage.get("transport_failure") for stage in llm_stages if stage.get("label") == "Groq anchor"),
                    None,
                ),
            },
            "routing": {
                "required_legs": required,
                "factual_routed_legs": routed,
                "coverage_percentage": routing.get("coverage_percentage"),
                "walk_legs": legs_by_mode.get("walk", 0),
                "vehicle_transfer_legs": legs_by_mode.get("drive", 0),
                # legs with no factual route, plus legs carrying data the provider did not return
                "unresolved_movement_data_failures": max(0, required - routed)
                + (modes.get("legs_with_unverified_movement_data") or 0),
                "failures_by_status": dict(routing.get("failures") or {}),
            },
            "diversity_collisions": {
                "hard_diversity_violations": sum(
                    1 for day in _part(report, "diversity").get("days") or [] if day.get("hard_violation")
                ),
                "unresolved_scheduled_collision_count": suspects.get("scheduled_unresolved_collision_count") or 0,
                "zero_distance_same_class_scheduled_pairs": len(suspects.get("zero_distance_scheduled_pairs") or []),
            },
            "food": {
                "repeated_suggestions": food.get("repeated_suggestion_count") or 0,
                "suggestions_beyond_radius": food.get("suggestions_beyond_radius") or 0,
            },
            "persistence": {
                "save": bool(_part(report, "persistence").get("save_succeeded")),
                "reload": bool(_part(report, "persistence").get("reload_succeeded")),
            },
            "providers": {
                "geoapify_credits_total": usage.get("total_geoapify_credits"),
                "geoapify_credit_budget": usage.get("geoapify_credit_budget"),
                "geoapify_calls_refused_by_budget": usage.get("geoapify_calls_refused_by_budget") or 0,
                "geoapify_calls_by_api": dict(usage.get("geoapify_calls_by_api") or {}),
                # request attempts per model stage when the generation recorded them,
                # otherwise the canary's stage-level count
                "groq_attempts_by_stage": (
                    {stage["label"]: stage.get("attempts", 0) for stage in llm_stages}
                    if llm_stages
                    else dict(usage.get("groq_stage_calls") or {})
                ),
                "deadline_exceeded_by_stage": {
                    stage["label"]: bool(stage.get("deadline_exceeded")) for stage in llm_stages
                },
            },
            "performance": {
                "total_generation_seconds": latency,
                "stage_seconds": {
                    stage["label"]: stage["seconds"] for stage in performance.get("stages") or [] if stage.get("seconds")
                },
                "generation_peak_geoapify_concurrency": concurrency.get("peak_geoapify_concurrency"),
                "process_peak_geoapify_concurrency": concurrency.get("process_peak_geoapify_concurrency"),
            },
            "narrative": {
                "source": narrative_source,
                "ai_attempt_status": safety.get("narrative_ai_attempt_status"),
                "fallback": narrative_source != "ai",
            },
            "reasoning": {"status": reasoning_status, "fallback": reasoning_status != "completed"},
            "scheduled_places": scheduled,
            "generic_or_low_value_scheduled_names": _generic_or_low_value_names(scheduled),
            # why a long-travel day could not be repaired (fixed codes only)
            "route_repair_failure_reasons": [
                {
                    "day": attempt.get("day"),
                    "reason": attempt.get("reason"),
                    "stop_protections": list(attempt.get("stop_protections") or []),
                }
                for attempt in report.get("route_repair") or []
                if not attempt.get("accepted")
            ],
            # Section 3C.2: what the routability repair did, and why each unrouted leg failed
            "routability_repair": [
                {
                    key: attempt.get(key)
                    for key in ("day", "reason", "accepted", "failed_legs_before", "failed_legs_after",
                                "suspect_protection", "suspect_was_grounded_anchor")
                }
                for attempt in _part(report, "routability_repair").get("attempts") or []
            ],
            "route_leg_failure_reasons": sorted(
                {
                    str(leg.get("failure_reason") or leg.get("status"))
                    for day in itinerary
                    for leg in day.get("route_legs") or []
                    if leg.get("distance_meters") is None
                }
            ),
            "canary_warnings": [
                *(f"acceptance check failed: {name}" for name in failed_checks),
                *(
                    f"day {day.get('day')}: {str(warning)[:200]}"
                    for day in itinerary
                    for warning in day.get("warnings") or []
                ),
            ],
        }
    )
    metrics["manual_review_flags"] = manual_review_flags(metrics)
    return metrics


def _low_anchor_utilization(metrics: dict[str, Any]) -> bool:
    """Several grounded anchors existed for a multi-day plan, and almost
    none reached the schedule (see `LOW_ANCHOR_UTILIZATION_*`)."""
    anchors = metrics.get("anchors") or {}
    grounded, scheduled = anchors.get("grounded_anchor_count"), anchors.get("scheduled_grounded_anchor_count")
    return (
        grounded is not None
        and scheduled is not None
        and (metrics.get("scenario") or {}).get("days", 0) >= 2
        and grounded >= LOW_ANCHOR_UTILIZATION_MIN_GROUNDED
        and scheduled <= LOW_ANCHOR_UTILIZATION_MAX_SCHEDULED
    )


def manual_review_flags(metrics: dict[str, Any]) -> list[str]:
    """Why a person should look at this city. Fixed order; empty = no flag."""
    if metrics.get("status") != STATUS_COMPLETED:
        flags = [FLAG_TECHNICAL_FAILURE, FLAG_ACCEPTANCE_FAIL]
        latency = _part(metrics, "performance").get("total_generation_seconds")
        return flags + ([FLAG_LATENCY] if latency is not None and latency > MAX_LATENCY_SECONDS else [])

    quality, safety, routing = metrics["quality"], metrics["factual_safety"], metrics["routing"]
    collisions, providers = metrics["diversity_collisions"], metrics["providers"]
    meaningful, minimum = quality["meaningful_scheduled_stops"], metrics["inventory"]["R"]
    coverage, latency = routing["coverage_percentage"], metrics["performance"]["total_generation_seconds"]
    rules = (
        (FLAG_ACCEPTANCE_FAIL, not metrics["acceptance"]["passed"]),
        (FLAG_BELOW_R, meaningful is not None and minimum is not None and meaningful < minimum),
        (FLAG_EMPTY_DAY, bool(quality["empty_days"])),
        (FLAG_DUPLICATE, quality["duplicate_count"] > 0),
        (FLAG_FABRICATED, safety["fabricated_or_unverified_scheduled_identities"] > 0),
        (FLAG_UNSUPPORTED_CLAIM, safety["unsupported_factual_claims"] > 0),
        (FLAG_ROUTING, coverage is not None and coverage < MIN_ROUTING_COVERAGE_PERCENT),
        (
            FLAG_COLLISION,
            collisions["unresolved_scheduled_collision_count"] > 0
            or collisions["zero_distance_same_class_scheduled_pairs"] > 0,
        ),
        (FLAG_BUDGET_REFUSED, providers["geoapify_calls_refused_by_budget"] > 0),
        (FLAG_LATENCY, latency is not None and latency > MAX_LATENCY_SECONDS),
        (FLAG_LLM_DEADLINE, any(providers["deadline_exceeded_by_stage"].values())),
        (FLAG_NARRATOR_FALLBACK, metrics["narrative"]["fallback"]),
        (
            FLAG_GENERIC_NAMES,
            quality["low_value_scheduled_count"] > 0 or bool(metrics["generic_or_low_value_scheduled_names"]),
        ),
        (FLAG_UNCOVERED_INTEREST, bool(quality.get("requested_interests_uncovered"))),
        (FLAG_LOW_ANCHOR_UTILIZATION, _low_anchor_utilization(metrics)),
    )
    return [flag for flag, raised in rules if raised]


# -- aggregation ------------------------------------------------------------------------------


def percentile(values: list[float], percent: float) -> float | None:
    """Nearest-rank percentile (an observed value, never an interpolation)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percent / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _rate(count: int, of: int) -> dict[str, Any]:
    return {"count": count, "of": of, "rate": round(count / of, 3) if of else None}


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Run-level figures over the cities that have a result. Every rate is
    over the cities run (a technical failure counts against it); the pass
    count is over the whole canonical set. There is no overall score."""
    completed = [record for record in records if record.get("status") == STATUS_COMPLETED]
    latencies = [
        value for record in records if (value := _part(record, "performance").get("total_generation_seconds")) is not None
    ]
    credits = [
        value for record in completed if (value := record["providers"]["geoapify_credits_total"]) is not None
    ]

    def cities(flag: str) -> list[str]:
        return [record["city"] for record in records if flag in record.get("manual_review_flags", [])]

    run = len(records)
    passed = sum(1 for record in completed if record["acceptance"]["passed"])
    return {
        # Progress of a (possibly partial) run. A city is COMPLETED when its
        # generation finished, whatever its acceptance; a technical failure is
        # not completed and stays in `remaining_count` until it is run again.
        "canonical_total": len(CANONICAL_CITIES),
        "completed_count": len(completed),
        "technical_failure_count": run - len(completed),
        "acceptance_pass_count": passed,
        "acceptance_fail_count": len(completed) - passed,
        "remaining_count": len(CANONICAL_CITIES) - len(completed),
        "cities_run": run,
        "cities_not_run": [city for city in CANONICAL_CITIES if city not in {record["city"] for record in records}],
        "technical_failures": [record["city"] for record in records if record.get("status") != STATUS_COMPLETED],
        "destination_resolution": _rate(sum(1 for record in completed if record["destination_resolved"]), run),
        "factual_safety_pass": _rate(
            sum(
                1
                for record in completed
                if record["factual_safety"]["fabricated_or_unverified_scheduled_identities"] == 0
                and record["factual_safety"]["unsupported_factual_claims"] == 0
            ),
            run,
        ),
        "routing_at_least_90_percent": _rate(
            sum(
                1
                for record in completed
                if (coverage := record["routing"]["coverage_percentage"]) is not None
                and coverage >= MIN_ROUTING_COVERAGE_PERCENT
            ),
            run,
        ),
        "persistence_success": _rate(
            sum(1 for record in completed if record["persistence"]["save"] and record["persistence"]["reload"]), run
        ),
        "latency_seconds": {
            "average": round(float(mean(latencies)), 1) if latencies else None,
            "median": round(float(median(latencies)), 1) if latencies else None,
            "p95": percentile(latencies, 95),
        },
        "geoapify_credits": {
            "average": round(float(mean(credits)), 1) if credits else None,
            "max": max(credits) if credits else None,
        },
        "narrator_fallback_count": sum(1 for record in completed if record["narrative"]["fallback"]),
        "reasoning_fallback_count": sum(1 for record in completed if record["reasoning"]["fallback"]),
        "cities_below_R": cities(FLAG_BELOW_R),
        "cities_with_empty_days": cities(FLAG_EMPTY_DAY),
        "cities_with_suspicious_collision_outcomes": cities(FLAG_COLLISION),
        "cities_flagged_for_manual_review": [record["city"] for record in records if record.get("manual_review_flags")],
    }


# -- output -----------------------------------------------------------------------------------

_CSV_COLUMNS: tuple[str, ...] = (
    "position", "city", "resolved_destination", "run_timestamp", "status", "technical_failure", "result", "outcome",
    "readiness", "blocking_codes", "review_codes", "T", "R", "H", "broad_candidates", "viable_meaningful_candidates",
    "inventory_status", "meaningful_stops", "target_attainment_ratio", "empty_days", "duplicates",
    "low_value_scheduled", "fabricated_identities", "unsupported_claims", "anchors_proposed", "anchors_grounded",
    "anchors_promoted", "anchor_failure_kind", "anchor_transport_failure", "grounded_anchor_count",
    "scheduled_grounded_anchor_count", "grounded_anchor_schedule_ratio", "requested_interests_uncovered",
    "route_repair_failure_reasons", "required_legs", "routed_legs", "routing_coverage_percent", "walk_legs",
    "vehicle_transfer_legs", "unresolved_movement_data_failures", "hard_diversity_violations",
    "unresolved_scheduled_collisions", "zero_distance_same_class_pairs", "food_repeated", "food_beyond_radius",
    "save", "reload", "geoapify_credits", "geoapify_refused_calls", "latency_seconds", "generation_peak_concurrency",
    "process_peak_concurrency", "llm_deadline_exceeded_stages", "narrative_source", "narrator_fallback",
    "reasoning_status", "reasoning_fallback", "manual_review_flags",
)


def _join(values: Any) -> str:
    return "; ".join(str(value) for value in values or [])


def csv_row(record: dict[str, Any]) -> dict[str, Any]:
    """One flat row per city (the same figures as the per-city metrics)."""
    row: dict[str, Any] = {
        "position": record.get("position"),
        "city": record["city"],
        "run_timestamp": record.get("run_timestamp"),
        "status": record.get("status"),
        "technical_failure": record.get("technical_failure"),
        "result": "PASS" if record["acceptance"]["passed"] else "FAIL",
        "outcome": record["acceptance"].get("outcome"),
        "latency_seconds": _part(record, "performance").get("total_generation_seconds"),
        "manual_review_flags": _join(record.get("manual_review_flags")),
    }
    if record.get("status") != STATUS_COMPLETED:
        return row
    acceptance, inventory, quality = record["acceptance"], record["inventory"], record["quality"]
    routing, collisions, providers = record["routing"], record["diversity_collisions"], record["providers"]
    row.update(
        {
            "resolved_destination": record["resolved_destination"],
            "readiness": acceptance["readiness"],
            "blocking_codes": _join(acceptance["blocking_codes"]),
            "review_codes": _join(acceptance["review_codes"]),
            "T": inventory["T"],
            "R": inventory["R"],
            "H": inventory["H"],
            "broad_candidates": inventory["broad_candidates"],
            "viable_meaningful_candidates": inventory["viable_meaningful_candidates"],
            "inventory_status": inventory["inventory_status"],
            "meaningful_stops": quality["meaningful_scheduled_stops"],
            "target_attainment_ratio": quality["target_attainment_ratio"],
            "empty_days": len(quality["empty_days"]),
            "duplicates": quality["duplicate_count"],
            "low_value_scheduled": quality["low_value_scheduled_count"],
            "fabricated_identities": record["factual_safety"]["fabricated_or_unverified_scheduled_identities"],
            "unsupported_claims": record["factual_safety"]["unsupported_factual_claims"],
            "anchors_proposed": record["anchors"]["proposed"],
            "anchors_grounded": record["anchors"]["grounded"],
            "anchors_promoted": record["anchors"]["promoted"],
            "anchor_failure_kind": record["anchors"]["failure_kind"],
            # Section 3B diagnostics (absent from results written before they existed)
            "anchor_transport_failure": record["anchors"].get("transport_failure"),
            "grounded_anchor_count": record["anchors"].get("grounded_anchor_count"),
            "scheduled_grounded_anchor_count": record["anchors"].get("scheduled_grounded_anchor_count"),
            "grounded_anchor_schedule_ratio": record["anchors"].get("grounded_anchor_schedule_ratio"),
            "requested_interests_uncovered": _join(quality.get("requested_interests_uncovered")),
            "route_repair_failure_reasons": _join(
                f"day {item.get('day')}: {item.get('reason')}" for item in record.get("route_repair_failure_reasons") or []
            ),
            "required_legs": routing["required_legs"],
            "routed_legs": routing["factual_routed_legs"],
            "routing_coverage_percent": routing["coverage_percentage"],
            "walk_legs": routing["walk_legs"],
            "vehicle_transfer_legs": routing["vehicle_transfer_legs"],
            "unresolved_movement_data_failures": routing["unresolved_movement_data_failures"],
            "hard_diversity_violations": collisions["hard_diversity_violations"],
            "unresolved_scheduled_collisions": collisions["unresolved_scheduled_collision_count"],
            "zero_distance_same_class_pairs": collisions["zero_distance_same_class_scheduled_pairs"],
            "food_repeated": record["food"]["repeated_suggestions"],
            "food_beyond_radius": record["food"]["suggestions_beyond_radius"],
            "save": record["persistence"]["save"],
            "reload": record["persistence"]["reload"],
            "geoapify_credits": providers["geoapify_credits_total"],
            "geoapify_refused_calls": providers["geoapify_calls_refused_by_budget"],
            "generation_peak_concurrency": record["performance"]["generation_peak_geoapify_concurrency"],
            "process_peak_concurrency": record["performance"]["process_peak_geoapify_concurrency"],
            "llm_deadline_exceeded_stages": _join(
                stage for stage, exceeded in providers["deadline_exceeded_by_stage"].items() if exceeded
            ),
            "narrative_source": record["narrative"]["source"],
            "narrator_fallback": record["narrative"]["fallback"],
            "reasoning_status": record["reasoning"]["status"],
            "reasoning_fallback": record["reasoning"]["fallback"],
        }
    )
    return row


def render_csv(records: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow(csv_row(record))
    return buffer.getvalue()


def _cell(value: Any) -> str:
    return "-" if value is None or value == "" else str(value).replace("|", "/").replace("\n", " ")


def progress_lines(figures: dict[str, Any]) -> list[str]:
    """Acceptance is stated over the cities that COMPLETED; "P / 18" appears
    only once every canonical city has completed (never for a partial run)."""
    total, completed = figures["canonical_total"], figures["completed_count"]
    lines = [
        f"Acceptance: {figures['acceptance_pass_count']} / {completed} completed cities passed",
        f"Progress: {completed} / {total} tuning cities completed",
    ]
    if figures["technical_failure_count"]:
        lines.append(f"Technical failures: {figures['technical_failure_count']} (not completed; run again)")
    if completed < total:
        lines.append(f"Remaining: {figures['remaining_count']}")
    else:
        lines.append(f"Acceptance: {figures['acceptance_pass_count']} / {total}")
    return lines


def render_markdown(summary: dict[str, Any]) -> str:
    records, figures = summary["cities"], summary["aggregate"]
    scenario = summary["scenario"]
    lines = [
        "# Tuning benchmark summary",
        "",
        f"- generated: {summary['generated_at']}",
        f"- trip start date: {summary['start_date']} | {scenario['days']} days | {scenario['pace']} | "
        f"{scenario['travelers']} travelers | origin: {scenario['origin']}",
        f"- interests: {', '.join(scenario['interests']) or 'none'} | must-visits: "
        f"{', '.join(scenario['must_visit']) or 'none'}",
        "",
        "| City | PASS/FAIL | Meaningful stops | Empty days | Duplicates | Routing % | Fabricated | Unsupported claims "
        "| Credits | Latency (s) | Narrative fallback | Readiness |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for record in records:
        row = csv_row(record)
        result = row["result"] if record.get("status") == STATUS_COMPLETED else f"FAIL ({record.get('technical_failure')})"
        stops = f"{row.get('meaningful_stops')}/{row.get('T')} (R={row.get('R')})" if row.get("T") is not None else None
        fallback = None if row.get("narrator_fallback") is None else ("yes" if row["narrator_fallback"] else "no")
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    record["city"], result, stops, row.get("empty_days"), row.get("duplicates"),
                    row.get("routing_coverage_percent"), row.get("fabricated_identities"),
                    row.get("unsupported_claims"), row.get("geoapify_credits"), row.get("latency_seconds"),
                    fallback, row.get("readiness"),
                )
            )
            + " |"
        )
    for city in figures["cities_not_run"]:
        lines.append(f"| {city} | not run | - | - | - | - | - | - | - | - | - | - |")

    def rate(key: str) -> str:
        item = figures[key]
        return f"{item['count']} / {item['of']}" + (f" ({item['rate']:.1%})" if item["rate"] is not None else "")

    latency, credits = figures["latency_seconds"], figures["geoapify_credits"]
    lines += [
        "",
        "## Aggregate",
        "",
        *(f"- {line}" for line in progress_lines(figures)),
        f"- destination resolution rate: {rate('destination_resolution')}",
        f"- factual-safety pass rate: {rate('factual_safety_pass')}",
        f"- routing >= 90% rate: {rate('routing_at_least_90_percent')}",
        f"- persistence success rate: {rate('persistence_success')}",
        f"- latency (s): average {_cell(latency['average'])} / median {_cell(latency['median'])} / p95 {_cell(latency['p95'])}",
        f"- Geoapify credits: average {_cell(credits['average'])} / max {_cell(credits['max'])}",
        f"- narrator fallback count: {figures['narrator_fallback_count']}",
        f"- reasoning fallback count: {figures['reasoning_fallback_count']}",
        f"- cities below R: {_join(figures['cities_below_R']) or 'none'}",
        f"- cities with empty days: {_join(figures['cities_with_empty_days']) or 'none'}",
        f"- cities with suspicious collision outcomes: {_join(figures['cities_with_suspicious_collision_outcomes']) or 'none'}",
        f"- technical failures: {_join(figures['technical_failures']) or 'none'}",
        "",
        "## Manual review",
        "",
    ]
    flagged = [record for record in records if record.get("manual_review_flags")]
    if not flagged:
        lines.append("No city was flagged.")
    for record in flagged:
        lines.append(f"- **{record['city']}**: {', '.join(record['manual_review_flags'])}")
        doubtful = record.get("generic_or_low_value_scheduled_names") or []
        if doubtful:
            lines.append(f"  - generic / low-value names: {_cell(_join(doubtful))}")
        uncovered = (record.get("quality") or {}).get("requested_interests_uncovered") or []
        if uncovered:
            lines.append(f"  - requested interests not covered: {_cell(_join(uncovered))}")
        anchors = record.get("anchors") or {}
        if anchors.get("grounded_anchor_count"):
            lines.append(
                f"  - grounded anchors scheduled: {anchors.get('scheduled_grounded_anchor_count')}"
                f" of {anchors['grounded_anchor_count']}"
            )
        if anchors.get("transport_failure"):
            lines.append(f"  - anchor proposal transport failure: {_cell(anchors['transport_failure'])}")
        if record.get("route_leg_failure_reasons"):
            lines.append(f"  - unrouted leg reasons: {_cell(_join(record['route_leg_failure_reasons']))}")
        for item in record.get("routability_repair") or []:
            lines.append(
                f"  - routability repair, day {item.get('day')}: {_cell(item.get('reason'))}"
                f" (failed legs {item.get('failed_legs_before')} -> {item.get('failed_legs_after')})"
            )
        for item in record.get("route_repair_failure_reasons") or []:
            lines.append(
                f"  - route repair, day {item.get('day')}: {_cell(item.get('reason'))}"
                f" (stops: {_cell(_join(item.get('stop_protections')) or 'not recorded')})"
            )
    lines += ["", "## Scheduled places (for manual quality review)", ""]
    for record in records:
        places = record.get("scheduled_places") or []
        lines.append(f"### {record['city']}")
        if not places:
            lines.append("- none scheduled")
        for place in places:
            lines.append(
                f"- day {place['day']}: {_cell(place['name'])} ({_cell(place['category'])})"
                + (" [low-value]" if place.get("low_value") else "")
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write(path: Path, text: str) -> None:
    """Written whole or not at all, so an interrupted run never leaves a
    half-written file that `--resume` could mistake for a result."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1, default=str) + "\n"


def load_city_record(out_dir: Path, city: str) -> dict[str, Any] | None:
    """A city's stored metrics, or None when there is no readable result."""
    try:
        stored = json.loads((out_dir / "cities" / f"{city_slug(city)}.json").read_text(encoding="utf-8"))
        record = stored["metrics"]
        return record if record.get("city") == city and "acceptance" in record else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def is_completed(out_dir: Path, city: str) -> bool:
    record = load_city_record(out_dir, city)
    return record is not None and record.get("status") == STATUS_COMPLETED


def write_summary(out_dir: Path, *, scenario: dict[str, Any], start_date: date, generated_at: str) -> dict[str, Any]:
    """Rebuilds summary.json / .csv / .md from every city result in `out_dir`
    (canonical order), so separate batches into one directory add up."""
    records = [record for city in CANONICAL_CITIES if (record := load_city_record(out_dir, city)) is not None]
    summary = scrub(
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "start_date": start_date.isoformat(),
            "scenario": dict(scenario),
            "aggregate": aggregate(records),
            "cities": records,
        }
    )
    _write(out_dir / "summary.json", _dump(summary))
    _write(out_dir / "summary.csv", render_csv(summary["cities"]))
    _write(out_dir / "summary.md", render_markdown(summary))
    return summary


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- the runner -------------------------------------------------------------------------------


def run_benchmark(
    cities: list[str],
    *,
    out_dir: Path,
    scenario: dict[str, Any],
    start_date: date,
    run_city: Callable[[str], dict[str, Any]],
    resume: bool = False,
    pause_seconds: float = 0.0,
    render: Callable[[dict[str, Any]], str] | None = None,
    now: Callable[[], str] = _utc_now,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Runs `run_city` for each city, strictly one after another, writing each
    city's files as soon as it finishes. One city's failure is recorded and
    the run continues. Returns the rebuilt summary."""
    render = render or canary_city._render
    (out_dir / "cities").mkdir(parents=True, exist_ok=True)
    pending = [city for city in cities if not (resume and is_completed(out_dir, city))]
    for city in cities:
        if city not in pending:
            log(f"[skip] {city}: already completed in {out_dir.name}")
    for index, city in enumerate(pending):
        run_timestamp, started = now(), time.monotonic()
        try:
            report = run_city(city)
        except Exception as exc:  # recorded by TYPE only -- never the exception text
            report = {
                "technical_failure": type(exc).__name__,
                "latency": {"total_generation_seconds": round(time.monotonic() - started, 1)},
            }
        try:
            record = extract_metrics(report, city=city, scenario=scenario, run_timestamp=run_timestamp)
        except Exception as exc:  # an unreadable report is a failed city, not a failed benchmark
            report = {"technical_failure": f"MetricsExtraction{type(exc).__name__}", "latency": report.get("latency")}
            record = extract_metrics(report, city=city, scenario=scenario, run_timestamp=run_timestamp)
        try:
            detail = render(report)
        except Exception as exc:
            detail = f"(detailed canary report unavailable: {type(exc).__name__})"
        secrets = _secret_values()
        record = scrub(record, secrets)
        text = "\n".join(
            [
                f"TUNING BENCHMARK: {city} | run {run_timestamp} | trip start {start_date.isoformat()}",
                f"MANUAL REVIEW FLAGS: {', '.join(record['manual_review_flags']) or 'none'}",
                "",
                detail,
            ]
        )
        slug = city_slug(city)
        _write(out_dir / "cities" / f"{slug}.txt", _scrub_text(text, secrets) + "\n")
        # the JSON is written last: its presence is what marks the city as done
        _write(out_dir / "cities" / f"{slug}.json", _dump({"metrics": record, "canary_report": scrub(report, secrets)}))
        log(
            f"[{index + 1}/{len(pending)}] {city}: {'PASS' if record['acceptance']['passed'] else 'FAIL'}"
            f" | {record['performance']['total_generation_seconds']}s"
            f" | flags: {', '.join(record['manual_review_flags']) or 'none'}"
        )
        write_summary(out_dir, scenario=scenario, start_date=start_date, generated_at=now())
        if index < len(pending) - 1 and pause_seconds > 0:
            sleep(pause_seconds)
    return write_summary(out_dir, scenario=scenario, start_date=start_date, generated_at=now())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--start-date", required=True, type=date.fromisoformat,
        help="trip start date (YYYY-MM-DD) used for EVERY city of the run; keep it within the weather forecast horizon",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="result directory (default: <repo>/benchmark_results/tuning_<UTC timestamp>); reuse it across batches",
    )
    parser.add_argument("--cities", action="append", help="canonical cities to run; semicolon-separated and/or repeated")
    parser.add_argument("--start", type=int, default=1, help="1-based position of the first city to run (default 1)")
    parser.add_argument("--limit", type=int, default=None, help="run at most N cities from --start")
    parser.add_argument("--resume", action="store_true", help="skip cities already completed in --out")
    parser.add_argument("--pause-seconds", type=float, default=8.0, help="pause between cities (LLM rate limits)")
    args = parser.parse_args(argv)

    if args.resume and args.out is None:
        parser.error("--resume needs the --out directory of the run to continue")
    names = [part.strip() for value in args.cities or [] for part in value.split(";") if part.strip()]
    try:
        cities = select_cities(names, args.start, args.limit)
    except ValueError as exc:
        parser.error(str(exc))
    if not cities:
        parser.error("no city selected")

    missing = [name for name in ("GEOAPIFY_API_KEY", "GROQ_API_KEY") if not os.environ.get(name)]
    if missing:
        print("Missing required environment variable(s): " + ", ".join(missing) + ". Nothing was run.")
        return 2

    out_dir = args.out or _REPO_DIR / "benchmark_results" / datetime.now(timezone.utc).strftime("tuning_%Y%m%dT%H%M%SZ")
    workdir = Path(tempfile.mkdtemp(prefix="travelobligator_tuning_"))
    cache_hits = canary_city._prepare_live_environment(workdir)
    scenario = dict(DEFAULT_SCENARIO)

    def run_city(city: str) -> dict[str, Any]:
        cache_hits.clear()  # per-city figures
        return canary_city._run(
            argparse.Namespace(
                city=city, days=scenario["days"], pace=scenario["pace"], interests=list(scenario["interests"]),
                must_visit=list(scenario["must_visit"]), start_date=args.start_date, origin=scenario["origin"],
                travelers=scenario["travelers"],
            ),
            cache_hits,
        )

    summary = run_benchmark(
        cities, out_dir=out_dir, scenario=scenario, start_date=args.start_date, run_city=run_city,
        resume=args.resume, pause_seconds=args.pause_seconds,
    )
    figures = summary["aggregate"]
    print("\n" + "\n".join(progress_lines(figures)))
    print(f"Flagged for manual review: {len(figures['cities_flagged_for_manual_review'])}")
    print(f"Results: {out_dir / 'summary.md'}")
    selected = [record for record in summary["cities"] if record["city"] in cities]
    return 0 if len(selected) == len(cities) and all(record["acceptance"]["passed"] for record in selected) else 1


if __name__ == "__main__":
    raise SystemExit(main())
