"""Section 203C.2B arbitrary-city benchmark (manual, live providers).

Runs the REAL generation pipeline in-process for the fixed benchmark set in
`scripts/benchmark/cities.json` and records objective per-run metrics. It
never repairs a city, never prints a key or a URL, and is never imported by
`backend/app`.

    cd backend
    python scripts/benchmark_cities.py --set tuning
    python scripts/benchmark_cities.py --set holdout --allow-holdout
    python scripts/benchmark_cities.py --set stress  --allow-holdout

Requires GEOAPIFY_API_KEY and GROQ_API_KEY in the environment. Holdout
cities and the holdout stress scenarios refuse to run without
`--allow-holdout`, so they cannot be touched by accident while tuning.
Persistence is a throwaway Local JSON store and a throwaway SQLite provider
cache (so first-run credits are cold-cache numbers).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DATA_PATH = Path(__file__).resolve().parent / "benchmark" / "cities.json"
_START_DATE = date(2027, 3, 9)

# Release thresholds (docs/22_free_tier_deployment.md, Section 203C.2B).
_MIN_ROUTING_COVERAGE = 0.90


def _configure_environment(workdir: Path) -> None:
    """Production-shaped provider configuration on throwaway local storage.
    Explicitly set values win over a developer's own environment."""
    os.environ.update(
        {
            "APP_ENV": "development",
            "PERSISTENCE_BACKEND": "local_json",
            "LOCAL_STORAGE_PATH": str(workdir / "state.json"),
            "PROVIDER_CACHE_BACKEND": "sqlite",
            "PROVIDER_CACHE_PATH": str(workdir / "provider_cache.sqlite3"),
            "PLANNING_ENGINE_MODE": "langgraph",
            "GEOCODING_PROVIDER": "geoapify",
            "PLACES_PROVIDER": "geoapify",
            "ROUTING_PROVIDER": "geoapify",
            "INVENTORY_SUFFICIENCY_GATE_ENABLED": "true",
            "ROUTE_AWARE_SCHEDULING_ENABLED": "true",
            "AI_CANDIDATE_PROPOSAL_PROVIDER": "groq",
            "AI_CANDIDATE_DISCOVERY_ENABLED": "true",
            "AI_DIRECTED_PROVIDER_DISCOVERY_MAX_SEARCHES": "16",
            "AI_DIRECTED_PROVIDER_DISCOVERY_MAX_EXTRA_SEARCHES": "4",
            "AI_ITINERARY_REASONING_ENABLED": "true",
            "AI_ITINERARY_REASONING_PROVIDER": "groq",
            "AI_ITINERARY_REPAIR_ENABLED": "true",
            "ITINERARY_NARRATOR_ENABLED": "true",
            "ITINERARY_NARRATOR_PROVIDER": "groq",
            "ASYNC_GENERATION_ENABLED": "false",
        }
    )


def _scenarios(data: dict[str, Any], which: str) -> list[dict[str, Any]]:
    canonical = data["canonical"]
    def _canonical(destination: str, split: str) -> dict[str, Any]:
        return {"id": f"{split}:{destination}", "split": split, "destination": destination, **canonical}

    tuning = [_canonical(destination, "tuning") for destination in data["tuning"]]
    holdout = [_canonical(destination, "holdout") for destination in data["holdout"]]
    stress = [{**scenario, "split": "holdout_stress"} for scenario in data["holdout_stress"]]
    return {"tuning": tuning, "holdout": holdout, "stress": stress, "all": tuning + holdout + stress}[which]


def _run_one(scenario: dict[str, Any]) -> dict[str, Any]:
    from app.models.planning_state import PlanningState, TravelGroupType, TripPace, TripRequest
    from app.repositories.factory import get_planning_state_repository
    from app.services.planning_orchestrator import planning_orchestrator
    from app.services.usefulness_contract import evaluate_usefulness, is_meaningful_stop

    record: dict[str, Any] = {
        "id": scenario["id"],
        "split": scenario["split"],
        "destination": scenario["destination"],
        "trip_days": scenario["trip_days"],
        "pace": scenario["pace"],
    }
    started = time.monotonic()
    try:
        trip_request = TripRequest(
            primary_destination=scenario["destination"],
            start_date=_START_DATE,
            end_date=_START_DATE + timedelta(days=scenario["trip_days"] - 1),
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
            pace=TripPace(scenario["pace"]),
            interests=list(scenario.get("interests", [])),
            must_visit=list(scenario.get("must_visit", [])),
        )
        created = planning_orchestrator.create_trip(trip_request)
        state: PlanningState = planning_orchestrator.generate_full_plan_via_langgraph(created.trip_id)
    except Exception as exc:  # a technical failure is recorded by TYPE only -- never its text
        record.update({"technical_failure": type(exc).__name__, "latency_seconds": round(time.monotonic() - started, 1)})
        return record
    record["latency_seconds"] = round(time.monotonic() - started, 1)
    record["technical_failure"] = None

    context = state.destination_context
    record["destination_resolved"] = bool(context is not None and context.resolved_destination)
    record["candidate_count"] = len(context.candidate_pois) if context is not None else 0
    record["food_candidate_count"] = len(context.candidate_restaurants) if context is not None else 0

    batch = state.ai_candidate_proposal_batch
    discovery = state.ai_provider_discovery_result
    promotion = state.ai_candidate_promotion_report
    record["ai_anchors_proposed"] = len(batch.result.proposals) if batch is not None else 0
    record["ai_anchors_grounded"] = discovery.matched_count if discovery is not None else 0
    record["ai_anchors_promoted"] = len(promotion.promoted_candidates) if promotion is not None else 0

    inventory = state.inventory_sufficiency_report
    verdict = evaluate_usefulness(state)
    record.update(
        {
            "inventory_status": inventory.status.value if inventory is not None else None,
            "viable_candidates": verdict.viable,
            "T": verdict.targets.target_stops,
            "R": verdict.targets.minimum_useful,
            "H": verdict.targets.healthy_buffer,
            "scheduled_meaningful_stops": verdict.scheduled_meaningful_stops,
            "target_attainment_ratio": verdict.target_attainment_ratio,
            "empty_days": list(verdict.empty_days),
            "duplicates": list(verdict.duplicate_place_ids),
        }
    )

    plan = state.experience_plan
    scheduled = [experience for day in (plan.daily_plans if plan else []) for experience in day.experiences]
    record["stops_per_day"] = [len(day.experiences) for day in (plan.daily_plans if plan else [])]
    record["fabricated_identities"] = sum(
        1 for experience in scheduled if not (experience.provider_place_id and experience.provider_source and experience.coordinates)
    )
    record["scheduled_low_value"] = sum(1 for experience in scheduled if not is_meaningful_stop(experience))

    must_visit = [term.lower() for term in scenario.get("must_visit", [])]
    if must_visit:
        pois = context.candidate_pois if context is not None else []
        grounded = {str(poi.get("must_visit_term") or "").lower() for poi in pois} | {
            term for term in must_visit if any(term in str(poi.get("name") or "").lower() for poi in pois)
        }
        scheduled_ids = {experience.provider_place_id for experience in scheduled}
        record["must_visit"] = {
            term: {
                "grounded": term in grounded,
                "scheduled": any(
                    poi.get("place_id") in scheduled_ids
                    for poi in pois
                    if str(poi.get("must_visit_term") or "").lower() == term or term in str(poi.get("name") or "").lower()
                ),
            }
            for term in must_visit
        }

    feasibility = state.route_feasibility_report
    legs = feasibility.legs if feasibility is not None else []
    covered = sum(1 for leg in legs if leg.distance_meters is not None and leg.duration_seconds is not None)
    record["routing_legs"] = len(legs)
    record["routing_coverage"] = round(covered / len(legs), 3) if legs else None

    record["provider_failures"] = sorted(
        key for key, entry in state.provider_status.items() if getattr(entry.status, "value", entry.status) == "failed"
    )
    validation = state.validation_report
    record["readiness"] = validation.readiness_status.value if validation is not None else None
    record["blocking_codes"] = list(validation.blocking_codes) if validation is not None else []
    record["review_codes"] = list(validation.review_codes) if validation is not None else []
    narrative = state.itinerary_narrative_report
    record["narrative_status"] = getattr(getattr(narrative, "status", None), "value", None)
    # a narrative the grounding guardrail rejected is the only place an unsupported claim could arise
    record["unsupported_claims"] = 1 if record["narrative_status"] == "rejected" else 0

    usage = state.provider_usage_report
    record["credits"] = usage.credits_used if usage is not None else None
    record["credits_by_api"] = dict(usage.credits_by_api) if usage is not None else {}
    record["budget_refusals"] = usage.refused_calls if usage is not None else None

    reloaded = get_planning_state_repository().get_by_trip_id(state.trip_id)
    record["persisted_and_reloaded"] = bool(
        reloaded is not None
        and reloaded.experience_plan is not None
        and [len(day.experiences) for day in reloaded.experience_plan.daily_plans] == record["stops_per_day"]
    )
    return record


def _passes(record: dict[str, Any], credit_cap: int) -> tuple[bool, list[str]]:
    """The per-run release requirements. A valid city may legitimately end
    as INSUFFICIENT_VERIFIED_INVENTORY -- but it must still resolve."""
    problems: list[str] = []
    if record.get("technical_failure"):
        return False, [f"technical failure ({record['technical_failure']})"]
    if not record["destination_resolved"]:
        problems.append("destination not resolved")
    if record["fabricated_identities"]:
        problems.append("fabricated identity scheduled")
    if record["duplicates"]:
        problems.append("duplicate scheduled place")
    if record["unsupported_claims"]:
        problems.append("unsupported factual claim")
    if not record["persisted_and_reloaded"]:
        problems.append("persistence/reload failed")
    if record["credits"] is not None and record["credits"] > credit_cap:
        problems.append("provider budget exceeded")
    viable_at_least_r = record["viable_candidates"] >= record["R"]
    if viable_at_least_r:
        if record["empty_days"]:
            problems.append("empty day although viable >= R")
        if record["scheduled_meaningful_stops"] < record["R"]:
            problems.append("fewer than R meaningful stops although viable >= R")
        if record["routing_coverage"] is not None and record["routing_coverage"] < _MIN_ROUTING_COVERAGE:
            problems.append("routing coverage below 90%")
    elif "INSUFFICIENT_VERIFIED_INVENTORY" not in record["blocking_codes"]:
        problems.append("insufficient inventory not reported as such")
    return not problems, problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", choices=["tuning", "holdout", "stress", "all"], default="tuning")
    parser.add_argument("--allow-holdout", action="store_true", help="required for holdout/stress/all")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N scenarios")
    parser.add_argument("--pause-seconds", type=float, default=8.0, help="pause between runs (LLM rate limits)")
    parser.add_argument("--out", type=Path, default=None, help="directory for results (default: a temp dir)")
    args = parser.parse_args()

    if args.set != "tuning" and not args.allow_holdout:
        print("Refusing to run holdout data without --allow-holdout (holdout is not used while tuning).")
        return 2
    missing = [name for name in ("GEOAPIFY_API_KEY", "GROQ_API_KEY") if not os.environ.get(name)]
    if missing:
        print("Missing required environment variable(s): " + ", ".join(missing) + ". Nothing was run.")
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="travelobligator_benchmark_"))
    out_dir = args.out or workdir
    out_dir.mkdir(parents=True, exist_ok=True)
    _configure_environment(workdir)
    sys.path.insert(0, str(_BACKEND_DIR))

    from app.core.config import get_settings

    get_settings.cache_clear()
    credit_cap = get_settings().geoapify_max_credits_per_generation

    data = json.loads(_DATA_PATH.read_text())
    scenarios = _scenarios(data, args.set)[: args.limit]
    records: list[dict[str, Any]] = []
    for index, scenario in enumerate(scenarios):
        record = _run_one(scenario)
        record["passed"], record["problems"] = _passes(record, credit_cap)
        records.append(record)
        print(
            f"[{index + 1}/{len(scenarios)}] {record['id']}: "
            f"{'PASS' if record['passed'] else 'FAIL'}"
            f" | inventory={record.get('inventory_status')} | stops={record.get('scheduled_meaningful_stops')}"
            f"/{record.get('T')} (R={record.get('R')}) | credits={record.get('credits')}"
            f" | {record.get('latency_seconds')}s"
            + (f" | {'; '.join(record['problems'])}" if record["problems"] else "")
        )
        if index < len(scenarios) - 1:
            time.sleep(args.pause_seconds)

    completed = [r for r in records if not r.get("technical_failure")]
    usable = [r for r in completed if r["viable_candidates"] >= r["R"]]
    credits = [r["credits"] for r in completed if r.get("credits") is not None]
    summary = {
        "set": args.set,
        "runs": len(records),
        "passed": sum(1 for r in records if r["passed"]),
        "destination_resolution_rate": (
            round(sum(1 for r in completed if r["destination_resolved"]) / len(records), 3) if records else None
        ),
        "viable_at_least_R_rate": round(len(usable) / len(records), 3) if records else None,
        "median_target_attainment": (
            round(median(r["target_attainment_ratio"] for r in usable), 3) if usable else None
        ),
        "fabricated_identities": sum(r.get("fabricated_identities", 0) for r in completed),
        "duplicate_scheduled_places": sum(len(r.get("duplicates", [])) for r in completed),
        "unsupported_claims": sum(r.get("unsupported_claims", 0) for r in completed),
        "median_credits": median(credits) if credits else None,
        "max_credits": max(credits) if credits else None,
        "total_credits": sum(credits),
        "credit_cap": credit_cap,
    }
    result_path = out_dir / f"benchmark_{args.set}.json"
    result_path.write_text(json.dumps({"summary": summary, "runs": records}, ensure_ascii=False, indent=1, default=str))
    print("\nSummary: " + json.dumps(summary))
    print(f"Results written to: {result_path}")
    return 0 if records and summary["passed"] == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
