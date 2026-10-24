"""M2 Phase 1 bounded live verification (manual, development only).

Confirms, against the real services, what the M2 Phase 1 changes assume:

  1. Geoapify geocoding returns an ISO country code for each development city
     (through `describe_destination`, the exact path the destination stage uses).
  2. With that code: Nager.Date holidays, Frankfurter exchange rate and an
     Open-Meteo forecast, through the application's own adapters, and the
     structured `missing_reason` of anything missing.
  3. Two assumption probes: Nager.Date answers an uncovered country with 404
     (-> `country_not_supported`), and Frankfurter answers an unpublished currency
     with 404/422 (-> `currency_not_supported`).
  4. One end-to-end generation (the real pipeline, in-process, on a throwaway Local
     JSON store and SQLite cache -- the configuration `canary_city.py` uses), read
     back for the M2 fields: context reasons, flight availability reason,
     change-request capability, place-lookup completeness, route coverage.

    cd backend
    python scripts/verify_m2_integrations.py            # dry run: the plan and caps only, NO request
    python scripts/verify_m2_integrations.py --execute  # live, bounded

Keys are read from the environment (`GEOAPIFY_API_KEY`; `GROQ_API_KEY` and/or
`GEMINI_API_KEY` + `GEMINI_MODEL` for step 4), or a hidden prompt for the
Geoapify / Groq key -- never from a command-line argument. Refuses
`APP_ENV=production` and any city of the frozen benchmark sets. Production is
never touched: persistence is a throwaway Local JSON file and the provider cache a
throwaway SQLite file, both in a temporary directory.

Hard limits, enforced on EVERY httpx request of this process (the application's
adapters and the LLM SDKs alike): a host allowlist (any other host is refused
before a connection is made -- e.g. Kiwi MCP), a per-service request cap and a
total cap from the dry-run plan; the first 401/403 from any service, or a 429
from a data service, stops every further request. LLM 429s are left to the
application's own bounded failover. Geoapify credits are additionally capped by
the application (`GEOAPIFY_MAX_CREDITS_PER_GENERATION`) and, for step 1, by this
script's own usage tracker. Nothing retries beyond what the adapters already do.

Output is codes and counts only: country / currency codes, status and reason codes,
request counts by service, credits. Never a key, URL, coordinate, place name or
itinerary text (the development city labels given as input are printed).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPTS_DIR.parent
_REPO_DIR = _BACKEND_DIR.parent
for _path in (str(_BACKEND_DIR), str(_SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import httpx  # noqa: E402

# Development cities: in no tuning, holdout or quality set (enforced below). They
# cover a currency the old 11-name table missed (JPY, SEK, THB), the euro, and a
# country with two circulating currencies (Panama: no rate may be requested).
DEFAULT_CITIES = (
    "Nara, Japan",
    "Gothenburg, Sweden",
    "Ljubljana, Slovenia",
    "Chiang Mai, Thailand",
    "Panama City, Panama",
)
DEFAULT_E2E_CITY = "Gothenburg, Sweden"
E2E_DAYS = 2
E2E_PACE = "balanced"
# Probe inputs (explicit codes: no geocoding). Antarctica is outside Nager.Date;
# the Vietnamese dong is not an ECB reference currency.
PROBE_UNSUPPORTED_HOLIDAY_COUNTRY = "AQ"
PROBE_UNPUBLISHED_CURRENCY_COUNTRY = "VN"

GEOAPIFY_CREDITS_PER_GENERATION = 100  # the production value, set explicitly for step 4
LLM_REQUESTS_PER_GENERATION = 8  # 4 routed stages (anchor, reasoning, repair x1, narrator) x at most 2 requests

_FROZEN_FILES = (
    _SCRIPTS_DIR / "benchmark" / "cities.json",
    _SCRIPTS_DIR / "benchmark" / "quality_v1.json",
    # M2 Phase 3: the second unseen acceptance set
    _SCRIPTS_DIR / "benchmark" / "quality_v2.json",
)
_CITY_LIST_KEYS = frozenset({"tuning", "holdout", "holdout_stress"})

# Host -> service. Every other host is refused.
_SERVICE_HOSTS = {
    "api.geoapify.com": "geoapify",
    "api.open-meteo.com": "open_meteo",
    "date.nager.at": "nager_date",
    "api.frankfurter.app": "frankfurter",
    "api.frankfurter.dev": "frankfurter",  # the documented redirect target
    "api.groq.com": "llm",
    "generativelanguage.googleapis.com": "llm",
}
_DATA_SERVICES = frozenset({"geoapify", "open_meteo", "nager_date", "frankfurter"})


# -- refusals ------------------------------------------------------------------------


def normalize_city(text: str) -> str:
    """The city part (before the first comma), accent- and case-insensitive."""
    ascii_text = unicodedata.normalize("NFKD", text.split(",")[0]).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", ascii_text).strip().casefold()


def _walk_destinations(node: Any):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "destination" and isinstance(value, str):
                yield value
            elif key in _CITY_LIST_KEYS and isinstance(value, list):
                yield from (item for item in value if isinstance(item, str))
            if isinstance(value, (dict, list)):
                yield from _walk_destinations(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_destinations(item)


def frozen_destinations(paths=_FROZEN_FILES) -> set[str]:
    names: set[str] = set()
    for path in paths:
        names.update(normalize_city(name) for name in _walk_destinations(json.loads(path.read_text(encoding="utf-8"))))
    return names


def production_selected(environ: dict[str, str] | None = None) -> bool:
    """APP_ENV=production in the caller's environment or its `.env`. Unreadable
    configuration counts as production (refuse when unsure)."""
    env = os.environ if environ is None else environ
    if (env.get("APP_ENV") or "").strip().lower() == "production":
        return True
    try:
        from app.core.config import Settings

        return (Settings().app_env or "").strip().lower() == "production"
    except Exception:  # noqa: BLE001
        return True


def _outside_repository(path: Path) -> bool:
    target = path.expanduser().resolve()
    return not target.is_relative_to(_REPO_DIR.resolve())


# -- the plan -------------------------------------------------------------------------


PROBES = ("nager", "frankfurter", "open_meteo")


def budget_plan(cities: int, e2e: bool, probes: tuple[str, ...] = PROBES) -> dict[str, Any]:
    """Worst case: every request attempted, every retry/redirect taken, every year
    boundary crossed. Geoapify requests never exceed credits (each costs >= 1).
    Nager.Date: +1 per adapter instance for its covered-country list, read only
    after an empty (HTTP 204) answer."""
    per_city = {"geoapify": 1, "open_meteo": 2, "nager_date": 2, "frankfurter": 2}
    caps = {service: count * cities for service, count in per_city.items()}
    caps["nager_date"] += 1  # covered-country list (steps 1-3 adapter)
    if "nager" in probes:
        caps["nager_date"] += 2  # uncovered-country probe
    if "frankfurter" in probes:
        caps["frankfurter"] += 2  # unpublished-currency probe
    # the Open-Meteo probe is the beyond-horizon path: it sends nothing
    caps["llm"] = 0
    credits = {"geoapify_step1": cities, "geoapify_e2e": 0}
    if e2e:
        caps["geoapify"] += GEOAPIFY_CREDITS_PER_GENERATION
        caps["open_meteo"] += 2
        caps["nager_date"] += 3  # two years + the covered-country list (the generation's own adapter)
        caps["frankfurter"] += 2
        caps["llm"] = LLM_REQUESTS_PER_GENERATION
        credits["geoapify_e2e"] = GEOAPIFY_CREDITS_PER_GENERATION
    return {
        "cities": cities,
        "end_to_end": e2e,
        "probes": list(probes),
        "max_requests_by_service": caps,
        "max_requests_total": sum(caps.values()),
        "max_geoapify_credits": credits["geoapify_step1"] + credits["geoapify_e2e"],
        "geoapify_credits": credits,
        "max_llm_requests": caps["llm"],
        "notes": [
            "Open-Meteo: 1 request + 1 retry per lookup, only for a city whose coordinates resolved; "
            "Nager.Date: 1 per calendar year; Frankfurter: 1 request + 1 redirect. Same-currency and "
            "multi-currency countries send none.",
            "End-to-end Geoapify requests are bounded by the application's per-generation credit cap.",
        ],
    }


# -- the request guard ------------------------------------------------------------------


@dataclass
class RequestGuard:
    """Counts and limits every httpx request of this process."""

    caps: dict[str, int]
    sent: Counter = field(default_factory=Counter)
    refused: Counter = field(default_factory=Counter)
    statuses: Counter = field(default_factory=Counter)
    stopped_by: str | None = None

    def admit(self, host: str) -> str:
        service = _SERVICE_HOSTS.get(host)
        if service is None:
            self.refused["host_not_allowed"] += 1
            raise PermissionError("host not allowed")
        if self.stopped_by is not None:
            self.refused[f"{service}:stopped"] += 1
            raise PermissionError("stopped")
        if self.sent[service] >= self.caps.get(service, 0):
            self.refused[f"{service}:cap"] += 1
            raise PermissionError("cap reached")
        self.sent[service] += 1
        return service

    def observe(self, service: str, status_code: int) -> None:
        self.statuses[f"{service}:{status_code}"] += 1
        if status_code in (401, 403) or (status_code == 429 and service in _DATA_SERVICES):
            self.stopped_by = self.stopped_by or f"{service}:{status_code}"


def install_guard(guard: RequestGuard) -> None:
    """Every sync and async httpx request goes through `guard` (refusal raises a
    `ConnectError`, which every adapter already reports as a failed request)."""
    original_sync = httpx.HTTPTransport.handle_request
    original_async = httpx.AsyncHTTPTransport.handle_async_request

    def guarded_sync(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        try:
            service = guard.admit(request.url.host)
        except PermissionError as exc:
            raise httpx.ConnectError(f"refused locally: {exc}", request=request) from None
        response = original_sync(self, request)
        guard.observe(service, response.status_code)
        return response

    async def guarded_async(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        try:
            service = guard.admit(request.url.host)
        except PermissionError as exc:
            raise httpx.ConnectError(f"refused locally: {exc}", request=request) from None
        response = await original_async(self, request)
        guard.observe(service, response.status_code)
        return response

    httpx.HTTPTransport.handle_request = guarded_sync  # type: ignore[method-assign]
    httpx.AsyncHTTPTransport.handle_async_request = guarded_async  # type: ignore[method-assign]


# -- live steps --------------------------------------------------------------------------


def _configure(workdir: Path) -> None:
    """The canary's production-shaped configuration on throwaway storage, with
    every switch this verification must not change pinned explicitly."""
    from canary_city import _prepare_live_environment

    _prepare_live_environment(workdir)
    os.environ.update(
        {
            "APP_ENV": "development",
            "GEOAPIFY_MAX_CREDITS_PER_GENERATION": str(GEOAPIFY_CREDITS_PER_GENERATION),
            "TARGETED_REGENERATION_ENABLED": "false",
            "AI_FEEDBACK_INTERPRETER_ENABLED": "false",
            "KIWI_MCP_ENABLED": "false",
            "FLIGHT_PROVIDER": "scraped_local",
            "SCRAPED_FLIGHT_HTML_PATH": str(workdir / "no_flight_file.html"),
            "SCRAPED_FLIGHT_HTML_PATH_SKYSCANNER": str(workdir / "none_1.html"),
            "SCRAPED_FLIGHT_HTML_PATH_GOOGLE_FLIGHTS": str(workdir / "none_2.html"),
            "SCRAPED_FLIGHT_HTML_PATH_KIWI_MANUAL": str(workdir / "none_3.html"),
            "METRICS_ENABLED": "false",
        }
    )
    from app.core.config import get_settings

    get_settings.cache_clear()


def _dates(days: int) -> dict[str, str]:
    start = date.today() + timedelta(days=7)
    return {"start_date": start.isoformat(), "end_date": (start + timedelta(days=days - 1)).isoformat()}


def _context_result(response: Any, kind: str) -> dict[str, Any]:
    data = response.data
    result: dict[str, Any] = {
        "status": getattr(response.status, "value", str(response.status)),
        "reason": response.failure_reason,
    }
    if kind == "currency" and data is not None:
        result.update(currency=data.destination_currency, rate_present=data.exchange_rate is not None)
    elif kind == "holidays":
        result["holidays_in_range"] = len(data) if data is not None else None
    elif kind == "weather":
        result["forecast_days"] = len(data) if data is not None else None
    return result


def run_country_and_context_steps(
    cities: list[str], workdir: Path, probes: tuple[str, ...] = PROBES
) -> dict[str, Any]:
    from app.core.provider_usage import GenerationProviderContext, ProviderUsageTracker
    from app.providers.currency.frankfurter_adapter import FrankfurterCurrencyAdapter
    from app.providers.geocoding.geoapify_adapter import GeoapifyGeocoder
    from app.providers.holidays.nager_date_adapter import NagerDateHolidaysAdapter
    from app.providers.places.country_identity import normalized_country_code
    from app.providers.places.geoapify_places_adapter import GeoapifyPlacesAdapter
    from app.providers.weather.open_meteo_adapter import OpenMeteoWeatherAdapter
    from app.storage.provider_cache_store import ProviderCacheStore
    from app.utils.country_currency import currency_for_country

    store = ProviderCacheStore(workdir / "verification_cache.sqlite3")
    tracker = ProviderUsageTracker(budget=len(cities))
    places = GeoapifyPlacesAdapter(cache_store=store, geocoder=GeoapifyGeocoder()).bound_to(
        GenerationProviderContext(usage_tracker=tracker, route_requests_left=0)
    )
    holidays = NagerDateHolidaysAdapter(cache_store=store)
    currency = FrankfurterCurrencyAdapter(cache_store=store)
    weather = OpenMeteoWeatherAdapter(cache_store=store)
    dates = _dates(3)

    results: list[dict[str, Any]] = []
    first_coordinates = None
    for city in cities:
        described = places.describe_destination(city) or {}
        code = normalized_country_code(described.get("country_code"))
        entry: dict[str, Any] = {
            "city": city,
            "resolved": bool(described),
            "country_code": code.upper() if code else None,
            "expected_currency": currency_for_country(code).currency if code else None,
        }
        if code:
            entry["holidays"] = _context_result(holidays.get_public_holidays(city, dict(dates), country_code=code), "holidays")
            entry["currency"] = _context_result(currency.get_exchange_rate("USD", city, country_code=code), "currency")
        # Weather needs coordinates. Without them nothing is asked: a missing
        # location is not a forecast result (and not a horizon limit).
        coordinates = places.resolve_coordinates(city) if described else None  # the cached resolution
        if coordinates is not None:
            first_coordinates = first_coordinates or coordinates
            entry["weather"] = _context_result(weather.get_weather_forecast(city, dict(dates), coordinates=coordinates), "weather")
        else:
            entry["weather"] = {"skipped": "no_coordinates"}
        results.append(entry)

    probe_results: dict[str, Any] = {}
    if "nager" in probes:
        probe_results["nager_uncovered_country"] = _context_result(
            holidays.get_public_holidays("probe", dict(dates), country_code=PROBE_UNSUPPORTED_HOLIDAY_COUNTRY), "holidays"
        )
    if "frankfurter" in probes:
        probe_results["frankfurter_unpublished_currency"] = _context_result(
            currency.get_exchange_rate("USD", "probe", country_code=PROBE_UNPUBLISHED_CURRENCY_COUNTRY), "currency"
        )
    if "open_meteo" in probes:
        # The horizon path is only meaningful WITH coordinates (coordinates are
        # checked first); without any, the probe is skipped, not failed.
        probe_results["open_meteo_beyond_horizon"] = (
            _context_result(
                weather.get_weather_forecast(
                    "probe",
                    {"start_date": (date.today() + timedelta(days=60)).isoformat(),
                     "end_date": (date.today() + timedelta(days=61)).isoformat()},
                    coordinates=first_coordinates,
                ),
                "weather",
            )
            if first_coordinates is not None
            else {"skipped": "no_coordinates"}
        )
    return {
        "cities": results,
        "probes": probe_results,
        "step1_geoapify_credits": tracker.credits_used(),
    }


def run_end_to_end(city: str) -> dict[str, Any]:
    from app.models.planning_state import TravelGroupType, TripPace, TripRequest
    from app.services.planning_orchestrator import planning_orchestrator
    from app.services.usefulness_contract import place_lookup_incomplete

    start = date.today() + timedelta(days=7)
    try:
        created = planning_orchestrator.create_trip(
            TripRequest(
                primary_destination=city,
                origin_city="Copenhagen",
                start_date=start,
                end_date=start + timedelta(days=E2E_DAYS - 1),
                travelers_count=2,
                travel_group_type=TravelGroupType.COUPLE,
                pace=TripPace(E2E_PACE),
                interests=["history", "food"],
            )
        )
        state = planning_orchestrator.generate_full_plan_via_langgraph(created.trip_id)
    except Exception as exc:  # reported by TYPE only
        return {"technical_failure": type(exc).__name__}

    def context(value: Any, **extra: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        return {"data_status": getattr(value.data_status, "value", value.data_status),
                "missing_reason": value.missing_reason, **extra}

    route = state.route_feasibility_report
    legs = route.legs if route is not None else []
    flight = state.flight_inventory_report
    readiness = state.regeneration_readiness
    usage = state.provider_usage_report
    validation = state.validation_report
    checklist = state.experience_plan.readiness_checklist.items if state.experience_plan else []
    resolved = (state.destination_context.resolved_destination or {}) if state.destination_context else {}
    return {
        "technical_failure": None,
        "resolved_country_code": (resolved.get("country_code") or "").upper() or None,
        "weather": context(state.weather_context, forecast_days=len(state.weather_context.daily_weather)
                           if state.weather_context else None),
        "holidays": context(state.holiday_context, country_code=state.holiday_context.country_code
                            if state.holiday_context else None),
        "currency": context(state.currency_context, currency=state.currency_context.destination_currency
                            if state.currency_context else None,
                            rate_present=bool(state.currency_context and state.currency_context.exchange_rate)),
        "flight": {
            "status": getattr(flight.status, "value", None) if flight else None,
            "availability_reason": getattr(flight.availability_reason, "value", None) if flight else None,
        },
        "change_requests": {
            "can_regenerate": readiness.can_regenerate,
            "applicable": readiness.change_requests_applicable,
            "reason": readiness.change_requests_unavailable_reason,
        },
        "place_lookup_incomplete": place_lookup_incomplete(state),
        "places_status": {
            key: {"status": entry.status.value, "failure_reason": entry.failure_reason}
            for key, entry in state.provider_status.items()
            if key.endswith(":places") or key.endswith(":places_expansion")
        },
        "routes": {
            "report_status": getattr(route.status, "value", None) if route else None,
            "legs": len(legs),
            "legs_verified": sum(1 for leg in legs if getattr(leg.status, "value", leg.status) == "success"),
            "draft_context_status": state.experience_plan.route_feasibility_context.data_status.value
            if state.experience_plan else None,
            "draft_says_not_connected": any(
                "not connected" in item.explanation for item in checklist if "Route" in item.label or "Walking" in item.label
            ),
        },
        "readiness_status": validation.readiness_status.value if validation else None,
        "blocking_codes": list(validation.blocking_codes) if validation else [],
        "review_codes": list(validation.review_codes) if validation else [],
        "geoapify_credits_used": usage.credits_used if usage else None,
        "geoapify_credits_by_api": dict(usage.credits_by_api) if usage else {},
        "geoapify_refused_calls": usage.refused_calls if usage else None,
    }


def checks(report: dict[str, Any]) -> list[dict[str, Any]]:
    """What the M2 changes assumed, judged from the observed codes. An assumption
    that is not confirmed is reported, never smoothed over."""
    results: list[dict[str, Any]] = []

    def check(name: str, passed: bool | None, observed: Any) -> None:
        results.append({"check": name, "result": "n/a" if passed is None else ("pass" if passed else "FAIL"),
                        "observed": observed})

    steps = report.get("country_and_context") or {}
    for city in steps.get("cities", []):
        check(f"{city['city']}: geocoder returned a country code", bool(city.get("country_code")), city.get("country_code"))
        cur = city.get("currency") or {}
        if city.get("expected_currency") is None and city.get("country_code"):
            check(f"{city['city']}: multi-currency country sends no rate", cur.get("reason") == "currency_ambiguous", cur)
        elif cur:
            check(f"{city['city']}: exchange rate or an honest reason", bool(cur.get("rate_present")) or bool(cur.get("reason")), cur)
        weather = city.get("weather") or {}
        if "skipped" in weather:
            check(f"{city['city']}: forecast (skipped: no coordinates)", None, weather)
        elif weather:
            check(f"{city['city']}: forecast or an honest reason",
                  bool(weather.get("forecast_days")) or bool(weather.get("reason")), weather)
    probes = steps.get("probes") or {}
    if "nager_uncovered_country" in probes:
        check("Nager.Date uncovered country (204 or 404) -> country_not_supported",
              probes["nager_uncovered_country"].get("reason") == "country_not_supported", probes["nager_uncovered_country"])
    if "frankfurter_unpublished_currency" in probes:
        check("Frankfurter 404/422 -> currency_not_supported (assumption)",
              probes["frankfurter_unpublished_currency"].get("reason") == "currency_not_supported",
              probes["frankfurter_unpublished_currency"])
    if "open_meteo_beyond_horizon" in probes:
        horizon = probes["open_meteo_beyond_horizon"]
        check("Open-Meteo beyond horizon -> forecast_not_yet_available, no request",
              None if "skipped" in horizon else horizon.get("reason") == "forecast_not_yet_available", horizon)
    e2e = report.get("end_to_end")
    if e2e:
        ok = e2e.get("technical_failure") is None
        check("end-to-end generation completed", ok, e2e.get("technical_failure"))
        if ok:
            check("holiday lookup used the geocoder's country",
                  (e2e["holidays"] or {}).get("country_code") == e2e.get("resolved_country_code"), e2e["holidays"])
            check("flights: no source configured (never 'no offers found')",
                  e2e["flight"]["availability_reason"] == "no_source_configured", e2e["flight"])
            check("change requests reported as not applicable",
                  e2e["change_requests"]["applicable"] is False and e2e["change_requests"]["can_regenerate"] is False,
                  e2e["change_requests"])
            check("draft route diagnostics never say 'not connected'",
                  not e2e["routes"]["draft_says_not_connected"], e2e["routes"])
    return results


def _read_key(name: str, prompt: bool) -> str:
    """The key from the environment, else (interactively) a hidden prompt. A
    prompted key is put into this process's environment -- the application's
    settings read keys ONLY from the environment and this checkout's `.env`, so a
    key held in a local variable would never reach them (M2 live-run defect)."""
    value = (os.environ.get(name) or "").strip()
    if value or not prompt or not sys.stdin.isatty():
        return value
    value = getpass.getpass(f"{name} (input hidden, Enter to skip): ").strip()
    if value:
        os.environ[name] = value
    return value


def preflight(e2e: bool) -> dict[str, Any]:
    """What the APPLICATION's settings can see -- presence only, never a value.
    Makes no request. `settings_env_file` is the one dotenv file the settings
    read: `<this checkout>/.env`. A git worktree has its own (usually absent)
    `.env`, so a key kept in the main checkout's `.env` is NOT visible here."""
    from app.core import config
    from app.core.config import Settings

    settings = Settings()
    env_file = Path(config._REPO_ROOT_ENV_FILE)
    llm_visible = bool((settings.groq_api_key or "").strip()) or bool(
        (settings.gemini_api_key or "").strip() and (settings.gemini_model or "").strip()
    )
    result = {
        "settings_env_file": str(env_file),
        "settings_env_file_exists": env_file.exists(),
        "geoapify_key_visible": bool((settings.geoapify_api_key or "").strip()),
        "geocoding_provider": settings.geocoding_provider,
        "places_provider": settings.places_provider,
        "llm_key_visible": llm_visible,
    }
    problems = []
    if not result["geoapify_key_visible"]:
        problems.append("GEOAPIFY_API_KEY is not visible to the application settings")
    if e2e and not llm_visible:
        problems.append("no LLM key is visible (GROQ_API_KEY, or GEMINI_API_KEY with GEMINI_MODEL)")
    result["problems"] = problems
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M2 Phase 1 bounded live verification (development only).")
    parser.add_argument("--city", action="append", help="Development city 'City, Country' (repeatable).")
    parser.add_argument("--e2e-city", default=DEFAULT_E2E_CITY, help="City of the one end-to-end generation.")
    parser.add_argument("--skip-e2e", action="store_true", help="Run steps 1-3 only (no LLM key needed).")
    parser.add_argument(
        "--probe", action="append", choices=PROBES,
        help="Assumption probe to run (repeatable). Default: all. Omit a probe that already passed.",
    )
    parser.add_argument("--execute", action="store_true", help="Send live requests. Without it, a dry run that sends none.")
    parser.add_argument("--out", type=Path, help="Also write the JSON report here (must be outside the repository).")
    args = parser.parse_args(argv)

    if production_selected():
        print("Refused: APP_ENV is production (or the configuration could not be read).", file=sys.stderr)
        return 2
    cities = args.city or list(DEFAULT_CITIES)
    e2e = not args.skip_e2e
    frozen = frozen_destinations()
    blocked = [city for city in [*cities, *([args.e2e_city] if e2e else [])] if normalize_city(city) in frozen]
    if blocked:
        print(f"Refused: {len(blocked)} of the given cities belong to a frozen benchmark set.", file=sys.stderr)
        return 2
    if args.out is not None and not _outside_repository(args.out):
        print("Refused: --out must be outside the repository.", file=sys.stderr)
        return 2

    probes = tuple(dict.fromkeys(args.probe)) if args.probe else PROBES
    plan = budget_plan(len(cities), e2e, probes)
    header = {"cities": cities, "e2e_city": args.e2e_city if e2e else None, "plan": plan}
    if not args.execute:
        print(json.dumps({"dry_run": True, "requests_sent": 0, **header, "preflight": preflight(e2e)},
                         indent=2, ensure_ascii=False))
        return 0

    _read_key("GEOAPIFY_API_KEY", prompt=True)
    if e2e and not (os.environ.get("GEMINI_API_KEY") and os.environ.get("GEMINI_MODEL")):
        _read_key("GROQ_API_KEY", prompt=True)

    workdir = Path(tempfile.mkdtemp(prefix="travelobligator_m2_verify_"))
    _configure(workdir)
    # Refuse BEFORE any request when the application itself cannot see a key:
    # the first live run reported every city as not_connected for this reason.
    checked = preflight(e2e)
    if checked["problems"]:
        print("Refused before any request: " + "; ".join(checked["problems"]) + ". " + json.dumps(
            {key: checked[key] for key in ("settings_env_file", "settings_env_file_exists")}), file=sys.stderr)
        return 2
    guard = RequestGuard(caps=dict(plan["max_requests_by_service"]))
    install_guard(guard)

    report: dict[str, Any] = {"verification": "m2_phase1", "version": 2, **header, "preflight": checked}
    report["country_and_context"] = run_country_and_context_steps(cities, workdir, probes)
    if e2e:
        report["end_to_end"] = run_end_to_end(args.e2e_city) if guard.stopped_by is None else {"skipped": "stopped"}
    report["requests"] = {
        "sent_by_service": dict(sorted(guard.sent.items())),
        "sent_total": sum(guard.sent.values()),
        "refused": dict(sorted(guard.refused.items())),
        "http_statuses": dict(sorted(guard.statuses.items())),
        "stopped_by": guard.stopped_by,
    }
    report["checks"] = checks(report)
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    print(text)
    if args.out is not None:
        args.out.expanduser().resolve().write_text(text + "\n", encoding="utf-8")
    failed = any(item["result"] == "FAIL" for item in report["checks"])
    return 1 if failed or guard.stopped_by else 0


if __name__ == "__main__":
    raise SystemExit(main())
