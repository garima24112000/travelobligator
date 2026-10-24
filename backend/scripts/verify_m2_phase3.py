"""M2 Phase 3 bounded live verification (manual, development only).

Runs the REAL generation pipeline (the single-city canary's own `_run`, so the
report and its `itinerary_quality` figures are exactly the canary's) for at
most two DEVELOPMENT scenarios, and answers the questions Phase 3 could not
answer offline:

  * how much venue evidence the live provider records carry -- opening hours,
    lifecycle tags, check dates -- for the places that are actually scheduled;
  * whether any scheduled stop that is not a must-visit is stated as not
    operating, or sits on a day its listed hours close it (target zero);
  * remote days, route severity and the model stages' outcomes.

    cd backend
    python scripts/verify_m2_phase3.py                              # dry run: plan + caps, NO request
    python scripts/verify_m2_phase3.py --scenarios 1 --execute      # the smallest live run (one city)
    python scripts/verify_m2_phase3.py --execute --out /tmp/m2p3.json

Credentials: from the environment (`GEOAPIFY_API_KEY`; `GROQ_API_KEY` and/or
`GEMINI_API_KEY` + `GEMINI_MODEL`) or hidden prompts -- never arguments.
Refuses APP_ENV=production and every city of a frozen benchmark set (including
the Phase 3 unseen set). Throwaway Local JSON storage and provider cache (the
canary's configuration); Kiwi, the airport lookup and targeted regeneration stay
off. Every `httpx` and `httpx2` request passes the guard (host allowlist,
per-service caps, a combined LLM cap); the first 401/403, or a 429 from a data
service, stops every further request. Output: codes, counts and labels only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPTS_DIR.parent
for _path in (str(_BACKEND_DIR), str(_SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import verify_m2_integrations as phase1  # noqa: E402 -- refusals, keys
import verify_m2_phase2 as phase2  # noqa: E402 -- the guard (httpx + httpx2)

# Development scenarios only: in no benchmark set (checked at run time). The
# trip starts on a Monday three weeks out, so weekly museum closures are in play.
DEV_SCENARIOS = (
    {"id": "D1", "city": "Vienna, Austria", "days": 3, "pace": "balanced", "interests": ["museums", "history"],
     "must_visit": []},
    {"id": "D2", "city": "Edinburgh, United Kingdom", "days": 2, "pace": "relaxed",
     "interests": ["history", "outdoors"], "must_visit": ["Edinburgh Castle"]},
)
GEOAPIFY_CREDITS_PER_GENERATION = phase1.GEOAPIFY_CREDITS_PER_GENERATION
LLM_REQUESTS_PER_GENERATION = phase1.LLM_REQUESTS_PER_GENERATION
CONTEXT_REQUESTS = {"open_meteo": 2, "nager_date": 3, "frankfurter": 2}


def start_date(today: date | None = None) -> date:
    base = (today or date.today()) + timedelta(days=21)
    return base + timedelta(days=(7 - base.weekday()) % 7)  # the next Monday


def budget_plan(count: int) -> dict[str, Any]:
    caps = {
        "geoapify": GEOAPIFY_CREDITS_PER_GENERATION * count,  # a request costs at least one credit
        "groq": LLM_REQUESTS_PER_GENERATION * count,
        "gemini": LLM_REQUESTS_PER_GENERATION * count,
        "kiwi": 0,
        **{service: value * count for service, value in CONTEXT_REQUESTS.items()},
    }
    llm = LLM_REQUESTS_PER_GENERATION * count
    return {
        "scenarios": count,
        "max_requests_by_service": caps,
        "max_llm_requests_combined": llm,
        "max_requests_total": sum(v for k, v in caps.items() if k not in ("groq", "gemini")) + llm,
        "max_geoapify_credits": GEOAPIFY_CREDITS_PER_GENERATION * count,
        "notes": [
            "Geoapify: each generation has its own credit cap (the application's), and the guard caps requests.",
            "No Place Details or other extra request is added by Phase 3: venue evidence comes from the "
            "records the generation already fetches.",
            "LLM: Groq and Gemini under ONE combined cap (4 stages x at most 2 requests per generation).",
        ],
    }


def scenario_checks(scenario: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def check(name: str, passed: bool | None, observed: Any) -> None:
        results.append({"scenario": scenario["id"], "check": name,
                        "result": "n/a" if passed is None else ("pass" if passed else "FAIL"), "observed": observed})

    failed = report.get("technical_failure")
    check("generation completed", not failed, failed)
    if failed:
        return results
    quality = report.get("itinerary_quality") or {}
    venue = quality.get("venue")
    check("venue figures were measured", isinstance(venue, dict), None)
    if not isinstance(venue, dict):
        return results
    check("no stop stated as not operating, except a must-visit",
          venue["scheduled_stated_not_operating_not_must_visit"] == 0, venue["scheduled_stated_not_operating"])
    check("no stop on a listed closing day, except a must-visit",
          venue["scheduled_on_a_listed_closing_day_not_must_visit"] == 0, venue["scheduled_on_a_listed_closing_day"])
    # informational: what the live records actually carry (the Phase 3 evidence question)
    check("venue evidence of the scheduled stops (informational)", None, venue["stops_by_evidence"])
    check("share of stops with hours unchecked (informational)", None, venue["share_with_hours_unchecked"])
    # version 2: why hours are unknown, and where the stops' records came from
    check("causes of unknown hours (informational)", None, venue.get("hours_unknown_by_cause"))
    check("stops by record origin (informational)", None, venue.get("stops_by_record"))
    check("no promoted anchor lost its venue evidence on the way to the plan",
          not (venue.get("hours_unknown_by_cause") or {}).get("no_record"), venue.get("hours_unknown_by_cause"))
    check("remote days without a requested place (informational)", None, venue["remote_days_without_a_requested_place"])
    severity = (quality.get("route_severity") or {}).get("days_by_severity")
    check("route severity by day (informational)", None, severity)
    acceptance = report.get("acceptance") or {}
    check("canary acceptance (informational; venue findings are not on its accepted list)", None,
          acceptance.get("outcome"))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M2 Phase 3 bounded live verification (development only).")
    parser.add_argument("--scenarios", type=int, choices=[1, 2], default=2, help="how many development scenarios")
    parser.add_argument("--execute", action="store_true", help="Send live requests. Without it, a dry run.")
    parser.add_argument("--out", type=Path, help="Also write the JSON report here (outside the repository).")
    args = parser.parse_args(argv)

    if phase1.production_selected():
        print("Refused: APP_ENV is production (or the configuration could not be read).", file=sys.stderr)
        return 2
    scenarios = list(DEV_SCENARIOS[: args.scenarios])
    frozen = phase1.frozen_destinations()
    if any(phase1.normalize_city(s["city"]) in frozen for s in scenarios):
        print("Refused: a development city belongs to a frozen benchmark set.", file=sys.stderr)
        return 2
    if args.out is not None and not phase1._outside_repository(args.out):
        print("Refused: --out must be outside the repository.", file=sys.stderr)
        return 2
    plan = budget_plan(len(scenarios))
    first_day = start_date()
    header = {"verification": "m2_phase3", "version": 2, "start_date": first_day.isoformat(),
              "scenarios": scenarios, "plan": plan}
    if not args.execute:
        print(json.dumps({"dry_run": True, "requests_sent": 0, **header, "preflight": phase1.preflight(True)},
                         indent=2, ensure_ascii=False))
        return 0

    phase1._read_key("GEOAPIFY_API_KEY", prompt=True)
    if not (os.environ.get("GEMINI_API_KEY") and os.environ.get("GEMINI_MODEL")):
        phase1._read_key("GROQ_API_KEY", prompt=True)
    import canary_city

    workdir = Path(tempfile.mkdtemp(prefix="travelobligator_m2p3_verify_"))
    cache_hits = canary_city._prepare_live_environment(workdir)
    os.environ.update({
        "APP_ENV": "development", "KIWI_MCP_ENABLED": "false", "FLIGHT_PROVIDER": "scraped_local",
        "FLIGHT_AIRPORT_LOOKUP_PROVIDER": "not_connected", "TARGETED_REGENERATION_ENABLED": "false",
        "GEOAPIFY_MAX_CREDITS_PER_GENERATION": str(GEOAPIFY_CREDITS_PER_GENERATION),
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

    report: dict[str, Any] = {**header, "results": {}, "checks": []}
    for scenario in scenarios:
        if guard.stopped_by is not None:
            report["results"][scenario["id"]] = {"skipped": "stopped"}
            continue
        namespace = argparse.Namespace(city=scenario["city"], days=scenario["days"], pace=scenario["pace"],
                                       interests=[", ".join(scenario["interests"])],
                                       must_visit=scenario["must_visit"], start_date=first_day)
        try:
            canary_report = canary_city._run(namespace, cache_hits)
        except Exception as exc:  # by TYPE only
            canary_report = {"technical_failure": type(exc).__name__}
        quality = canary_report.get("itinerary_quality") or {}
        report["results"][scenario["id"]] = {
            "technical_failure": canary_report.get("technical_failure"),
            "acceptance": (canary_report.get("acceptance") or {}).get("outcome"),
            "venue": quality.get("venue"),
            "route_severity": (quality.get("route_severity") or {}).get("days_by_severity"),
            # version 2: why a day is elevated (the classification's own reasons)
            "elevated_days": [
                {key: day.get(key) for key in ("day", "elevated_because", "vehicle_legs",
                                               "longest_vehicle_leg_seconds", "walking_seconds")}
                for day in (quality.get("route_severity") or {}).get("days") or []
                if day.get("severity") == "elevated"
            ],
            "provider_usage": canary_report.get("provider_usage"),
        }
        report["checks"].extend(scenario_checks(scenario, canary_report))
    report["requests"] = {
        "sent_by_service": dict(sorted(guard.sent.items())),
        "sent_total": sum(guard.sent.values()),
        "refused": dict(sorted(guard.refused.items())),
        "http_statuses": dict(sorted(guard.statuses.items())),
        "stopped_by": guard.stopped_by,
    }
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
