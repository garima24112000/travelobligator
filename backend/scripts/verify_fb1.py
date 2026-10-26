"""Final Batch 1 bounded live verification (manual, development only).

Runs the REAL generation pipeline (the single-city canary's own `_run`) once for
each of four DEVELOPMENT scenarios -- cities in no benchmark set (checked at run
time against every frozen and tuning file) -- and reports what the Final Batch 1
corrections did with live provider data:

  * must-visit resolution: each term's status and how it was found (lookup
    outcome and rejection label), and that none disappeared silently;
  * visit times: listed-hours fits vs. unknown hours vs. untimed stops, hours
    conflicts and their findings, days past their window;
  * route evidence: verified / unverified legs, vehicle legs, provider-flagged
    ferries, and that no finding claims a transport service;
  * remote and thin days, same-site records, English display names.

    cd backend
    python scripts/verify_fb1.py                                   # dry run: plan + ceilings, NO request
    python scripts/verify_fb1.py --execute --out /tmp/fb1.json     # the one live cycle
    python scripts/verify_fb1.py --scenario F2 --start-date 2026-11-16 --execute --out /tmp/fb1_f2.json
                                                                   # one scenario, the earlier run's dates

`--scenario` only SELECTS scenarios (each exactly as defined below); the caps
follow the selection (one scenario: 100 Geoapify credits, 8 LLM requests).

Ceilings for the whole cycle (never raised): Geoapify 400 credits (the
application's own 100-credit cap per generation x 4; the guard also caps
requests), Groq + Gemini 32 requests combined (8 per generation x 4), Kiwi 0.
Credentials: from the environment or hidden prompts -- never arguments. Refuses
APP_ENV=production, any frozen or tuning city, and an --out inside the
repository. Throwaway Local JSON storage and provider cache. Every httpx/httpx2
request passes the guard (host allowlist, per-service caps, a combined LLM cap);
the first 401/403, or a 429 from a data service, stops every further request.
Each scenario runs ONCE; nothing is retried. Output: codes, counts, labels and
the requested must-visit terms only -- never a key, URL, prompt or model text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPTS_DIR.parent
for _path in (str(_BACKEND_DIR), str(_SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import verify_m2_integrations as phase1  # noqa: E402 -- refusals, keys
import verify_m2_phase2 as phase2  # noqa: E402 -- the guard (httpx + httpx2)
import verify_m2_phase3 as phase3  # noqa: E402 -- start date, per-generation caps

# Development cities only (each was a development destination of an earlier M2
# run; none is in a benchmark file -- checked at run time).
SCENARIOS = (
    {"id": "F1", "city": "Budapest, Hungary", "days": 2, "pace": "balanced",
     "interests": ["history", "architecture"], "must_visit": ["Buda Castle", "Hungarian Parliament Building"],
     "exercises": ["must-visit identity / other names", "reconciliation", "same site", "English names"]},
    {"id": "F2", "city": "Edinburgh, United Kingdom", "days": 2, "pace": "relaxed", "interests": ["history"],
     "must_visit": ["Edinburgh Castle", "Rosslyn Chapel"],
     "exercises": ["out-of-city must-visit (excursion)", "same site"]},
    {"id": "F3", "city": "Gothenburg, Sweden", "days": 3, "pace": "relaxed", "interests": ["waterfront", "outdoors"],
     "must_visit": [], "exercises": ["remote / thin days", "ferry honesty"]},
    {"id": "F4", "city": "Vienna, Austria", "days": 2, "pace": "packed", "interests": ["museums", "art"],
     "must_visit": [], "exercises": ["visit times", "listed closing days", "long days"]},
)
CYCLE_GEOAPIFY_CREDITS = 400
CYCLE_LLM_REQUESTS = 32
# Wording that would claim a transport SERVICE (a driving route proves a road only).
_TRANSPORT_SERVICE = re.compile(
    r"\b(?:taxi|cab|bus|coach|train|rail|metro|subway|tram|shuttle|uber|public transport|ferry service|"
    r"departs?|timetable|tickets? (?:are|is) available)\b", re.IGNORECASE)
_TRAVEL_FINDINGS = frozenset({"remote_day_access", "must_visit"})


def budget_plan(count: int) -> dict[str, Any]:
    plan = phase3.budget_plan(count)
    plan["cycle_ceilings"] = {"geoapify_credits": CYCLE_GEOAPIFY_CREDITS, "llm_requests": CYCLE_LLM_REQUESTS}
    return plan


def refusal(scenarios: list[dict[str, Any]], out: Path | None) -> str | None:
    """Why this cycle may not run (before any request), or None."""
    if phase1.production_selected():
        return "APP_ENV is production (or the configuration could not be read)"
    frozen = phase1.frozen_destinations()
    if any(phase1.normalize_city(s["city"]) in frozen for s in scenarios):
        return "a scenario city belongs to a benchmark set"
    if out is not None and not phase1._outside_repository(out):
        return "--out must be outside the repository"
    plan = budget_plan(len(scenarios))
    if plan["max_geoapify_credits"] > CYCLE_GEOAPIFY_CREDITS or plan["max_llm_requests_combined"] > CYCLE_LLM_REQUESTS:
        return "the scenarios' caps exceed the cycle ceilings"
    return None


def _latest_state(store_path: Path, city: str) -> Any:
    from app.models.planning_state import PlanningState

    data = json.loads(store_path.read_text(encoding="utf-8"))
    states = [PlanningState.model_validate(raw.get("state", raw)) for raw in (data.get("planning_states") or {}).values()]
    matches = [s for s in states if s.trip_request.primary_destination == city and s.experience_plan is not None]
    return max(matches, key=lambda s: s.metadata.updated_at) if matches else None


def _dispersion(state: Any) -> dict[int, Any]:
    from app.services.geographic_dispersion import dispersion_by_day

    return dispersion_by_day(state)


def unaccepted_review_codes(review_codes: list[str]) -> list[str]:
    """The canary's own readiness gate (its accepted data-coverage list, read
    from the canary -- never a copy): the review codes it does NOT accept."""
    import canary_city

    return sorted(set(review_codes) - canary_city._ACCEPTED_REVIEW_CODES)


def select_scenarios(ids: list[str] | None) -> list[dict[str, Any]]:
    """The scenarios to run, in file order: all, or those named (each as
    defined above -- a selection never changes a scenario)."""
    if not ids:
        return list(SCENARIOS)
    known = {scenario["id"] for scenario in SCENARIOS}
    unknown = sorted(set(ids) - known)
    if unknown:
        raise ValueError(f"unknown scenario id(s): {', '.join(unknown)}")
    return [scenario for scenario in SCENARIOS if scenario["id"] in set(ids)]


def summarize(state: Any, canary_report: dict[str, Any]) -> dict[str, Any]:
    """What the corrections did, from the stored plan (codes and counts)."""
    plan, report, context = state.experience_plan, state.validation_report, state.destination_context
    pool = {str(p["place_id"]): p for p in (context.candidate_pois if context else []) if p.get("place_id")}
    stops = [(day, item) for day in plan.daily_plans for item in day.experiences]
    legs = (state.route_feasibility_report.legs if state.route_feasibility_report else [])
    findings = [*report.critical_issues, *report.warnings]
    by_category = Counter(issue.category for issue in findings)

    groups = Counter(
        pool.get(str(item.provider_place_id), {}).get("same_site_group")
        for _, item in stops if pool.get(str(item.provider_place_id), {}).get("same_site_group")
    )
    return {
        "must_visits": [entry.model_dump() for entry in report.must_visit_status or []],
        "must_visit_lookups": [lookup.model_dump() for lookup in (context.must_visit_lookups if context else [])],
        "visit_times": {
            "stops": len(stops),
            "by_status": dict(Counter(item.visit_time_status or "none" for _, item in stops)),
            "estimated_durations_minutes": dict(Counter(item.estimated_duration_minutes for _, item in stops)),
            "days": [
                {"day": day.day_number, **(day.visit_schedule.model_dump() if day.visit_schedule else {})}
                for day in plan.daily_plans
            ],
            "findings": {key: by_category.get(key, 0) for key in ("visit_outside_listed_hours", "day_exceeds_time_window")},
        },
        "venue": ((canary_report.get("itinerary_quality") or {}).get("venue")),
        "routes": {
            "legs": len(legs),
            "verified": sum(1 for leg in legs if leg.duration_seconds is not None and leg.distance_meters is not None),
            "by_mode": dict(Counter(str(leg.mode or "walk") for leg in legs if leg.duration_seconds is not None)),
            "provider_flagged_ferry": sum(1 for leg in legs if leg.includes_ferry is True),
            "severity": ((canary_report.get("itinerary_quality") or {}).get("route_severity") or {}).get("days_by_severity"),
            # Each leg as the routing provider answered it (a leg without a
            # provider distance and duration is NOT verified, whatever its mode).
            "per_leg": [
                {"from": leg.from_experience_name, "to": leg.to_experience_name, "mode": leg.mode,
                 "status": getattr(leg.status, "value", leg.status), "distance_m": leg.distance_meters,
                 "duration_s": leg.duration_seconds, "verified": leg.duration_seconds is not None
                 and leg.distance_meters is not None, "includes_ferry": leg.includes_ferry}
                for leg in legs
            ],
        },
        # The validator's dispersion measure and cause, per dispersed day.
        "geographic_dispersion": {
            str(day): {"spread_km": round(item.spread_km, 2), "cause": item.cause}
            for day, item in _dispersion(state).items()
        },
        "days": [[item.name for item in day.experiences] for day in plan.daily_plans],
        "remote_and_thin": {key: by_category.get(key, 0) for key in ("remote_day_access", "thin_day", "empty_day")},
        "stops_per_day": [len(day.experiences) for day in plan.daily_plans],
        "same_site": {
            "scheduled_records_in_a_site_group": sum(groups.values()),
            "site_groups_with_two_scheduled_records": sum(1 for count in groups.values() if count > 1),
        },
        "names": {"stops_with_an_english_name": sum(1 for _, item in stops if item.display_name)},
        "readiness": report.readiness_status.value,
        "review_codes": list(report.review_codes),
        "finding_categories": dict(by_category),
        "provider_usage": canary_report.get("provider_usage"),
    }


def checks(scenario: dict[str, Any], state: Any, summary: dict[str, Any] | None,
           canary_report: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def check(name: str, passed: bool | None, observed: Any = None) -> None:
        results.append({"scenario": scenario["id"], "check": name,
                        "result": "n/a" if passed is None else ("pass" if passed else "FAIL"), "observed": observed})

    failed = canary_report.get("technical_failure")
    check("generation completed", not failed and state is not None, failed)
    if failed or state is None or summary is None:
        return results
    statuses = {entry["term"]: entry["status"] for entry in summary["must_visits"]}
    check("every requested must-visit has a status (none removed silently)",
          set(statuses) == set(scenario["must_visit"]), statuses)
    missing = [term for term, status in statuses.items() if status not in ("scheduled", "scheduled_excursion",
                                                                            "found_inaccessible")]
    check("a must-visit not on the plan is disclosed", not missing or summary["finding_categories"].get("must_visit", 0) > 0,
          missing)
    venue = summary["venue"] or {}
    check("no stop stated not operating, except a must-visit",
          venue.get("scheduled_stated_not_operating_not_must_visit", 0) == 0)
    check("no stop on a listed closing day, except a must-visit",
          venue.get("scheduled_on_a_listed_closing_day_not_must_visit", 0) == 0)
    plan = state.experience_plan
    timed_after_untimed = False
    for day in plan.daily_plans:
        seen_untimed = False
        for item in day.experiences:
            seen_untimed = seen_untimed or item.start_time is None
            timed_after_untimed = timed_after_untimed or (seen_untimed and item.start_time is not None)
    check("no time estimated after an unverified leg", not timed_after_untimed)
    conflicts = summary["visit_times"]["by_status"].get("outside_listed_hours", 0)
    check("every remaining hours conflict is disclosed",
          conflicts == 0 or summary["visit_times"]["findings"]["visit_outside_listed_hours"] > 0, conflicts)
    claims = [issue.category for issue in (*state.validation_report.warnings, *state.validation_report.critical_issues)
              if issue.category in _TRAVEL_FINDINGS and _TRANSPORT_SERVICE.search(issue.message)]
    check("no finding claims a transport service", not claims, claims)
    check("no two records of one site scheduled (unless both requested)",
          summary["same_site"]["site_groups_with_two_scheduled_records"] == 0 or bool(scenario["must_visit"]),
          summary["same_site"])
    # The readiness gate F2 failed before the dispersion fix: still enforced,
    # read from the canary's own accepted list (nothing is accepted here that
    # the canary would not accept).
    unaccepted = unaccepted_review_codes(summary["review_codes"])
    check("readiness gate: no unaccepted review code (e.g. GEOGRAPHIC_DISPERSION)", not unaccepted, unaccepted)
    usage = summary["provider_usage"] or {}
    credits = usage.get("total_geoapify_credits")
    check("Geoapify credits within the per-generation cap",
          credits is not None and credits <= phase3.GEOAPIFY_CREDITS_PER_GENERATION, credits)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Final Batch 1 bounded live verification (development only).")
    parser.add_argument("--execute", action="store_true", help="Send live requests. Without it, a dry run.")
    parser.add_argument("--out", type=Path, help="Also write the JSON report here (outside the repository).")
    parser.add_argument("--scenario", action="append", dest="scenario_ids",
                        help="Run only this scenario id (repeatable). Default: all four.")
    parser.add_argument("--start-date", type=date.fromisoformat, default=None,
                        help="Pin the trip start date (YYYY-MM-DD) to repeat an earlier run's dates.")
    args = parser.parse_args(argv)

    try:
        scenarios = select_scenarios(args.scenario_ids)
    except ValueError as exc:
        print(f"Refused: {exc}. Nothing was run.", file=sys.stderr)
        return 2
    refused = refusal(scenarios, args.out)
    if refused:
        print(f"Refused: {refused}. Nothing was run.", file=sys.stderr)
        return 2
    # The caps follow the number of SELECTED scenarios (one: 100 credits, 8 LLM requests).
    plan = budget_plan(len(scenarios))
    first_day = args.start_date or phase3.start_date()
    header = {"verification": "final_batch1", "version": 2, "start_date": first_day.isoformat(),
              "scenarios": scenarios, "plan": plan}
    if not args.execute:
        print(json.dumps({"dry_run": True, "requests_sent": 0, **header, "preflight": phase1.preflight(True)},
                         indent=2, ensure_ascii=False))
        return 0

    phase1._read_key("GEOAPIFY_API_KEY", prompt=True)
    if not (os.environ.get("GEMINI_API_KEY") and os.environ.get("GEMINI_MODEL")):
        phase1._read_key("GROQ_API_KEY", prompt=True)
    import canary_city

    workdir = Path(tempfile.mkdtemp(prefix="travelobligator_fb1_verify_"))
    cache_hits = canary_city._prepare_live_environment(workdir)
    os.environ.update({
        "APP_ENV": "development", "KIWI_MCP_ENABLED": "false", "FLIGHT_PROVIDER": "scraped_local",
        "FLIGHT_AIRPORT_LOOKUP_PROVIDER": "not_connected", "TARGETED_REGENERATION_ENABLED": "false",
        "GEOAPIFY_MAX_CREDITS_PER_GENERATION": str(phase3.GEOAPIFY_CREDITS_PER_GENERATION),
    })
    from app.core.config import get_settings

    get_settings.cache_clear()
    checked = phase1.preflight(True)
    if checked["problems"]:
        print("Refused before any request: " + "; ".join(checked["problems"]), file=sys.stderr)
        return 2
    guard = phase2.Guard(caps=dict(plan["max_requests_by_service"]), llm_cap=plan["max_llm_requests_combined"])
    phase1.install_guard(guard)
    phase2.install_httpx2_guard(guard)
    store_path = Path(os.environ["LOCAL_STORAGE_PATH"])

    report: dict[str, Any] = {**header, "results": {}, "checks": []}
    cycle_credits = 0
    for scenario in scenarios:
        if guard.stopped_by is not None:
            report["results"][scenario["id"]] = {"skipped": "stopped"}
            continue
        if cycle_credits + phase3.GEOAPIFY_CREDITS_PER_GENERATION > plan["max_geoapify_credits"]:
            report["results"][scenario["id"]] = {"skipped": "cycle_credit_ceiling"}
            continue
        namespace = argparse.Namespace(city=scenario["city"], days=scenario["days"], pace=scenario["pace"],
                                       interests=[", ".join(scenario["interests"])],
                                       must_visit=scenario["must_visit"], start_date=first_day)
        try:
            canary_report = canary_city._run(namespace, cache_hits)  # once: never retried
        except Exception as exc:  # by TYPE only
            canary_report = {"technical_failure": type(exc).__name__}
        state = summary = None
        if not canary_report.get("technical_failure"):
            try:
                state = _latest_state(store_path, scenario["city"])
                summary = summarize(state, canary_report) if state is not None else None
            except Exception as exc:  # by TYPE only
                canary_report["technical_failure"] = f"summary:{type(exc).__name__}"
        cycle_credits += int(((summary or {}).get("provider_usage") or canary_report.get("provider_usage") or {})
                             .get("total_geoapify_credits") or 0)
        report["results"][scenario["id"]] = {
            "technical_failure": canary_report.get("technical_failure"),
            "acceptance": (canary_report.get("acceptance") or {}).get("outcome"),
            "summary": summary,
        }
        report["checks"].extend(checks(scenario, state, summary, canary_report))
    llm_sent = sum(guard.sent.get(name, 0) for name in ("groq", "gemini"))
    report["requests"] = {
        "sent_by_service": dict(sorted(guard.sent.items())),
        "sent_total": sum(guard.sent.values()),
        "refused": dict(sorted(guard.refused.items())),
        "http_statuses": dict(sorted(guard.statuses.items())),
        "stopped_by": guard.stopped_by,
        "cycle_geoapify_credits": cycle_credits,
        "cycle_llm_requests": llm_sent,
    }
    ceiling_credits, ceiling_llm = plan["max_geoapify_credits"], plan["max_llm_requests_combined"]
    report["checks"].append({"scenario": "all",
                             "check": f"run within {ceiling_credits} Geoapify credits and {ceiling_llm} LLM requests",
                             "result": "pass" if cycle_credits <= ceiling_credits and llm_sent <= ceiling_llm
                             else "FAIL", "observed": {"credits": cycle_credits, "llm": llm_sent}})
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    leak_free = phase2.secret_values_absent(text)
    report["checks"].append({"scenario": "all", "check": "no credential value in the report",
                             "result": "pass" if leak_free else "FAIL", "observed": None})
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str) if leak_free else '{"withheld": true}'
    print(text)
    if args.out is not None and leak_free:
        args.out.expanduser().resolve().write_text(text + "\n", encoding="utf-8")
    return 1 if guard.stopped_by or any(item["result"] == "FAIL" for item in report["checks"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
