"""M2 Phase 2 bounded live verification (manual, development only). Version 4.

Version 1 found three things: Kiwi answered London -> Porto with offers in the
currency it stated; "Jersey City" -> "Orlando" came back empty with no error;
and the request counter said 0 although Kiwi answered -- the MCP SDK sends over
`httpx2`, which the `httpx` guard never saw, so its Kiwi cap was not enforced.
Version 2 guards and counts BOTH packages, and cross-checks the guard's count
against the count the Kiwi client keeps in its own transport. Its lookup run
picked the nearest Geoapify `airport.international` features -- a seaplane base
and an executive airport -- and an empty search between them was reported as
"no offers". Version 3 selects airports from the OurAirports data set (local
file, `--airport-data`) by evidence of scheduled airline service, and checks the
QUALITY of every substituted airport, not just that it has a code.

What it establishes, against the real services:

  1. Kiwi's live `search-flight` input schema (property names + descriptions --
     inert server metadata), from one discovery session.
  2. Two routes through the application's own `FlightInventoryService`:
       * control: London -> Porto;
       * a place without an airport of its own: "Jersey City, NJ" -> "Orlando, FL".
     A search by place NAME that finds nothing is `location_unconfirmed`, never
     "no offers". With the commercial-airport lookup on (`--airport-lookup
     --airport-data <airports.csv>`, needs a Geoapify key for geocoding), the
     best airport with scheduled airline service is searched next, with its
     evidence and rationale recorded.
  3. Optionally (without `--skip-e2e`) one end-to-end generation (Porto, 2 days,
     from London) with Kiwi switched on IN THIS PROCESS ONLY, on throwaway
     storage: what reached the stored plan.

    cd backend
    python scripts/verify_m2_phase2.py --skip-e2e                            # dry run: plan + caps, NO request
    python scripts/verify_m2_phase2.py --skip-e2e --execute                  # Kiwi only, no key
    python scripts/verify_m2_phase2.py --skip-e2e --airport-lookup --airport-data <airports.csv> --execute
    python scripts/verify_m2_phase2.py --execute --out /tmp/m2p2.json        # + one generation (keys)
    python scripts/verify_m2_phase2.py --e2e-only --airport-data <airports.csv> \
        --ui-snapshot /tmp/m2p2_ui.json --execute --out /tmp/m2p2_e2e.json  # version 4: see below

Version 4 (`--e2e-only`): the Kiwi probes already passed, so they are not repeated.
ONE generation (Jersey City, NJ -> Orlando, Florida, 2 days) runs through the
application's real HTTP routes in-process (sign-up, POST /trips, POST
/generate, the job), on throwaway Local JSON storage, with Kiwi and the
commercial-airport lookup switched on in this process only. The checks read the
SAVED trip back (repository and API). `--ui-snapshot` writes the API responses
the page loads, which the offline browser check replays in both views
(`frontend/e2e-replay/`, no provider request).

Credentials: from the environment (`GEOAPIFY_API_KEY`; `GROQ_API_KEY` and/or
`GEMINI_API_KEY` + `GEMINI_MODEL`) or hidden prompts -- never arguments. Kiwi's
MCP server needs no key. Refuses APP_ENV=production and frozen benchmark cities.
Every `httpx` AND `httpx2` request of the process passes the guard (host
allowlist, per-service caps, a combined LLM cap); the first 401/403, or a 429
from a data service or Kiwi, stops every further request. In addition the Kiwi
client refuses any MCP session beyond `KIWI_MCP_MAX_HTTP_REQUESTS_PER_SESSION`.
Output: codes, counts and Kiwi's own schema text only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPTS_DIR.parent
for _path in (str(_BACKEND_DIR), str(_SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import verify_m2_integrations as phase1  # noqa: E402 -- shared refusals, preflight, configuration

VERSION = 4
# Version 4: the end-to-end trip. Jersey City has no airport of its own, so the
# flight search exercises the commercial-airport substitution; neither city is
# in a frozen benchmark set (checked at run time).
E2E_ONLY_CITY = "Orlando, Florida"
E2E_ONLY_ORIGIN = "Jersey City, NJ"
# What the page loads for one trip (frontend/lib/api.ts), recorded for the replay.
UI_ENDPOINTS = (
    "/auth/me", "/auth/registration", "/trips",
    "/trips/{trip_id}", "/trips/{trip_id}/summary", "/trips/{trip_id}/destination-context",
    "/trips/{trip_id}/experience-plan", "/trips/{trip_id}/validation-report",
    "/trips/{trip_id}/provider-coverage", "/trips/{trip_id}/regeneration-readiness",
    "/trips/{trip_id}/regeneration-attempts", "/trips/{trip_id}/ai-candidate-review",
    "/trips/{trip_id}/branches", "/trips/{trip_id}/jobs", "/trips/{trip_id}/generation-progress",
)
_JOB_POLLS = 120
KIWI_PROBES = (
    ("control", "London", "Porto"),
    ("no_airport_in_place", "Jersey City, NJ", "Orlando, FL"),
)
E2E_CITY = "Porto, Portugal"
E2E_ORIGIN = "London"
E2E_DAYS = 2
# The Kiwi client's own per-session cap (KIWI_MCP_MAX_HTTP_REQUESTS_PER_SESSION, pinned below).
KIWI_HTTP_PER_SESSION = 8
# Flight searches per route at most: the name search + one nearby-airport search.
SEARCHES_PER_ROUTE = 2
# Commercial-airport lookup: one geocoder request per side, two sides (the
# airport evidence is a local file: no request).
LOOKUP_REQUESTS_PER_ROUTE = 2
_LAND_AIRPORT_TYPES = frozenset({"large_airport", "medium_airport", "small_airport"})
GEOAPIFY_CREDITS_PER_GENERATION = phase1.GEOAPIFY_CREDITS_PER_GENERATION
LLM_REQUESTS_PER_GENERATION = phase1.LLM_REQUESTS_PER_GENERATION

_SERVICE_HOSTS = {
    "api.geoapify.com": "geoapify",
    "api.open-meteo.com": "open_meteo",
    "date.nager.at": "nager_date",
    "api.frankfurter.app": "frankfurter",
    "api.frankfurter.dev": "frankfurter",
    "mcp.kiwi.com": "kiwi",
    "api.groq.com": "groq",
    "generativelanguage.googleapis.com": "gemini",
}
_LLM = frozenset({"groq", "gemini"})
_STOP_ON_429 = frozenset({"geoapify", "open_meteo", "nager_date", "frankfurter", "kiwi"})
_HONEST_NO_OFFER = frozenset({"location_unconfirmed", "no_searchable_airport"})


def budget_plan(e2e: bool, airport_lookup: bool = False, probes: bool = True) -> dict[str, Any]:
    # Kiwi sessions: 1 discovery (reused in-process afterwards) + up to two
    # searches per route (+ the e2e route).
    routes = (len(KIWI_PROBES) if probes else 0) + (1 if e2e else 0)
    sessions = 1 + routes * SEARCHES_PER_ROUTE
    caps = {"kiwi": sessions * KIWI_HTTP_PER_SESSION, "geoapify": 0, "open_meteo": 0, "nager_date": 0,
            "frankfurter": 0, "groq": 0, "gemini": 0}
    probe_lookup = len(KIWI_PROBES) * LOOKUP_REQUESTS_PER_ROUTE if (airport_lookup and probes) else 0
    caps["geoapify"] = probe_lookup
    llm_total = 0
    if e2e:
        caps.update(open_meteo=2, nager_date=3, frankfurter=2,
                    groq=LLM_REQUESTS_PER_GENERATION, gemini=LLM_REQUESTS_PER_GENERATION)
        # the generation's own lookup shares that generation's credit cap
        caps["geoapify"] += GEOAPIFY_CREDITS_PER_GENERATION
        llm_total = LLM_REQUESTS_PER_GENERATION
    total = sum(value for key, value in caps.items() if key not in _LLM) + llm_total
    return {
        "end_to_end": e2e,
        "kiwi_probes": probes,
        "airport_lookup": airport_lookup,
        "kiwi_sessions_max": sessions,
        "kiwi_http_per_session_max": KIWI_HTTP_PER_SESSION,
        "max_requests_by_service": caps,
        "max_llm_requests_combined": llm_total,
        "max_requests_total": total,
        # a lookup request is one geocode: 1 credit
        "max_geoapify_credits": probe_lookup + (GEOAPIFY_CREDITS_PER_GENERATION if e2e else 0),
        "notes": [
            "Kiwi MCP needs no key. Its HTTP requests are counted twice: by this guard (httpx AND httpx2) and by "
            "the Kiwi client's own transport, which also refuses a session beyond its per-session cap.",
            f"A route makes at most {SEARCHES_PER_ROUTE} Kiwi searches (place names, then airports with scheduled "
            f"airline service) and at most {LOOKUP_REQUESTS_PER_ROUTE} Geoapify geocoding requests; airport "
            "evidence comes from the local OurAirports file.",
            "LLM: Groq and Gemini are counted apart, under ONE combined cap (4 stages x at most 2 requests).",
        ],
    }


@dataclass
class Guard:
    caps: dict[str, int]
    llm_cap: int
    sent: Counter = field(default_factory=Counter)
    refused: Counter = field(default_factory=Counter)
    statuses: Counter = field(default_factory=Counter)
    by_package: Counter = field(default_factory=Counter)
    stopped_by: str | None = None

    def admit(self, host: str, package: str = "httpx") -> str:
        service = _SERVICE_HOSTS.get(host)
        if service is None:
            self.refused["host_not_allowed"] += 1
            raise PermissionError("host not allowed")
        if self.stopped_by is not None:
            self.refused[f"{service}:stopped"] += 1
            raise PermissionError("stopped")
        llm_used = sum(self.sent[name] for name in _LLM)
        if self.sent[service] >= self.caps.get(service, 0) or (service in _LLM and llm_used >= self.llm_cap):
            self.refused[f"{service}:cap"] += 1
            raise PermissionError("cap reached")
        self.sent[service] += 1
        self.by_package[f"{service}:{package}"] += 1
        return service

    def observe(self, service: str, status_code: int) -> None:
        self.statuses[f"{service}:{status_code}"] += 1
        if status_code in (401, 403) or (status_code == 429 and service in _STOP_ON_429):
            self.stopped_by = self.stopped_by or f"{service}:{status_code}"


def install_httpx2_guard(guard: Guard) -> bool:
    """The same guard on `httpx2` (the MCP SDK's HTTP package). Returns False
    when `httpx2` is not installed (then no Kiwi request can be sent either)."""
    try:
        import httpx2
    except ImportError:
        return False
    original_sync = httpx2.HTTPTransport.handle_request
    original_async = httpx2.AsyncHTTPTransport.handle_async_request

    def guarded_sync(self: Any, request: Any) -> Any:
        try:
            service = guard.admit(request.url.host, "httpx2")
        except PermissionError as exc:
            raise httpx2.ConnectError(f"refused locally: {exc}", request=request) from None
        response = original_sync(self, request)
        guard.observe(service, response.status_code)
        return response

    async def guarded_async(self: Any, request: Any) -> Any:
        try:
            service = guard.admit(request.url.host, "httpx2")
        except PermissionError as exc:
            raise httpx2.ConnectError(f"refused locally: {exc}", request=request) from None
        response = await original_async(self, request)
        guard.observe(service, response.status_code)
        return response

    httpx2.HTTPTransport.handle_request = guarded_sync
    httpx2.AsyncHTTPTransport.handle_async_request = guarded_async
    return True


def _flight_summary(result: Any) -> dict[str, Any]:
    offers = list(result.offers or [])
    priced = [offer for offer in offers if offer.total_price_amount is not None]
    return {
        "status": getattr(result.status, "value", result.status),
        "availability_reason": getattr(result.availability_reason, "value", result.availability_reason),
        "failure_kind": result.failure_kind,
        "searched": [result.origin, result.destination],
        "search_attempts": result.search_attempts,
        "kiwi_http_requests_reported": result.provider_http_requests,
        "location_resolution": [
            {key: getattr(entry, key, None) for key in (
                "role", "method", "searched", "airport_name", "airport_code", "straight_line_km", "lookup_source",
                "airport_type", "airport_lat", "airport_lon", "commercial_service_evidence", "selection_rationale",
                "dataset_version")}
            for entry in (result.location_resolution or [])
        ],
        "offers": len(offers),
        "priced_offers": len(priced),
        "priced_offers_with_stated_currency": sum(1 for offer in priced if offer.currency),
        "currencies": sorted({offer.currency for offer in priced if offer.currency}),
        "departure_airports": sorted({
            offer.outbound_segments[0].origin_airport
            for offer in offers
            if offer.outbound_segments and offer.outbound_segments[0].origin_airport
        }),
    }


def discover_schema() -> dict[str, Any]:
    """One discovery session through the adapter (which caches it for the
    searches that follow): the flight tool's input schema, as Kiwi states it."""
    from app.core.config import get_settings
    from app.providers.flights.kiwi_mcp_adapter import KiwiMcpFlightProvider, _find_flight_search_tool

    settings = get_settings()
    provider = KiwiMcpFlightProvider()
    result = provider._discover(provider._resolve_client(settings), settings)
    tool = _find_flight_search_tool(result.tools, settings.kiwi_mcp_tool_name) if result.tools else None
    schema = (tool.input_schema or {}) if tool else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    return {
        "status": getattr(result.status, "value", result.status),
        "failure_kind": result.failure_kind,
        "kiwi_http_requests_reported": result.http_requests,
        "tools": sorted(t.name for t in result.tools),
        "flight_tool": tool.name if tool else None,
        "required": schema.get("required"),
        "properties": {
            name: {"type": spec.get("type"), "enum": spec.get("enum"),
                   "description": str(spec.get("description") or "")[:300]}
            for name, spec in sorted(properties.items()) if isinstance(spec, dict)
        },
    }


def run_kiwi_probes() -> dict[str, Any]:
    from app.core.provider_usage import GenerationProviderContext
    from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
    from app.providers.gateway import ProviderGateway
    from app.services.flight_inventory_service import FlightInventoryService

    gateway = ProviderGateway()  # built now: Kiwi and the lookup as configured by main()
    service = FlightInventoryService(gateway=gateway)
    departure = date.today() + timedelta(days=45)
    results: dict[str, Any] = {}
    for label, origin, destination in KIWI_PROBES:
        context = GenerationProviderContext.new(trip_days=1)
        state = PlanningState(
            trip_id=f"m2p2-probe-{label}",
            trip_request=TripRequest(primary_destination=destination, origin_city=origin, start_date=departure,
                                     end_date=departure, travelers_count=1,
                                     travel_group_type=TravelGroupType.SOLO, budget_currency="USD"),
        )
        summary = _flight_summary(service.build_report(state, provider_context=context))
        summary["geoapify_credits_used"] = context.usage_tracker.credits_used()
        results[label] = summary
    return results


def _llm_records(state: Any) -> dict[str, Any]:
    report = state.generation_performance_report
    stages = getattr(report, "llm_stages", None) or {}
    records = {}
    for name, stage in stages.items():
        data = stage.model_dump() if hasattr(stage, "model_dump") else dict(stage)
        records[name] = {
            "result": data.get("result"),
            "attempted": data.get("attempted_providers"),
            "attempts": [
                {key: attempt.get(key) for key in ("provider", "result", "transport_failure_kind", "structural_validation_result")}
                for attempt in data.get("provider_attempts") or []
            ],
            "retry_after": data.get("retry_after"),
            "failover_reason": data.get("failover_reason"),
        }
    return records


def run_end_to_end() -> dict[str, Any]:
    from app.models.planning_state import TravelGroupType, TripPace, TripRequest
    from app.providers.flights.airport_lookup import get_airport_lookup_provider
    from app.providers.flights.factory import get_flight_provider
    from app.providers.gateway import provider_gateway
    from app.services.planning_orchestrator import planning_orchestrator

    # The gateway picks its providers when it is built; re-resolve them now
    # that this process switched Kiwi (and the lookup) on -- development only.
    provider_gateway.flight_inventory = get_flight_provider()
    provider_gateway.airport_lookup = get_airport_lookup_provider()
    flight_provider_used = getattr(provider_gateway.flight_inventory, "provider_name", None)
    start = date.today() + timedelta(days=7)
    try:
        created = planning_orchestrator.create_trip(
            TripRequest(
                primary_destination=E2E_CITY, origin_city=E2E_ORIGIN, start_date=start,
                end_date=start + timedelta(days=E2E_DAYS - 1), travelers_count=2,
                travel_group_type=TravelGroupType.COUPLE, pace=TripPace("balanced"), interests=["history", "food"],
            )
        )
        state = planning_orchestrator.generate_full_plan_via_langgraph(created.trip_id)
    except Exception as exc:  # by TYPE only
        return {"technical_failure": type(exc).__name__}

    route = state.route_feasibility_report
    legs = route.legs if route is not None else []
    context = state.experience_plan.route_feasibility_context if state.experience_plan else None
    flight = state.flight_inventory_report
    accommodation = state.accommodation_inventory_report

    def ctx(value: Any) -> dict[str, Any] | None:
        return None if value is None else {"data_status": getattr(value.data_status, "value", value.data_status),
                                           "missing_reason": value.missing_reason}

    return {
        "technical_failure": None,
        "flight_provider_used": flight_provider_used,
        "flight": _flight_summary(flight) if flight is not None else None,
        "accommodation": {
            "status": getattr(accommodation.status, "value", None) if accommodation else None,
            "availability_reason": accommodation.availability_reason if accommodation else None,
        },
        "routes": {
            "report_status": getattr(route.status, "value", None) if route else None,
            "legs": len(legs),
            "legs_verified": sum(1 for leg in legs if getattr(leg.status, "value", leg.status) == "success"),
            "context_status": getattr(context.data_status, "value", None) if context else None,
            "context_segments": sum(len(day.segments) for day in context.daily_route_feasibility) if context else 0,
        },
        "weather": ctx(state.weather_context),
        "holidays": ctx(state.holiday_context),
        "currency": ctx(state.currency_context),
        "llm_stages": _llm_records(state),
        "readiness_status": state.validation_report.readiness_status.value if state.validation_report else None,
        "geoapify_credits_used": state.provider_usage_report.credits_used if state.provider_usage_report else None,
    }


def _offer_detail(offer: dict[str, Any]) -> dict[str, Any]:
    segments = offer.get("outbound_segments") or []
    return {
        "priced": offer.get("total_price_amount") is not None,
        "currency": offer.get("currency"),
        "booking_link_https": str(offer.get("booking_url") or "").startswith("https://"),
        "departs": segments[0].get("origin_airport") if segments else None,
        "arrives": segments[-1].get("destination_airport") if segments else None,
    }


def run_api_end_to_end(client: Any, *, origin: str, destination: str, days: int, signup: bool = True,
                       start: date | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """One generation through the application's real HTTP routes (`client` is
    a Starlette TestClient on the app: in-process, so only the PROVIDER
    requests touch the network, through the guard). Returns the report part
    and the UI snapshot (the API responses the page loads, keyed by path)."""
    from app.providers.flights.airport_lookup import get_airport_lookup_provider
    from app.providers.flights.factory import get_flight_provider
    from app.providers.gateway import provider_gateway
    from app.repositories.factory import get_planning_state_repository

    # The gateway picks its providers when it is built; re-resolve them now
    # that this process switched Kiwi and the lookup on (development only).
    provider_gateway.flight_inventory = get_flight_provider()
    provider_gateway.airport_lookup = get_airport_lookup_provider()
    if signup:
        email = f"m2p2-{os.urandom(6).hex()}@example.invalid"
        response = client.post("/auth/signup", json={"email": email, "password": os.urandom(12).hex()})
        if response.status_code != 201:
            return {"technical_failure": f"signup_http_{response.status_code}"}, {}
    first_day = start or (date.today() + timedelta(days=21))
    created = client.post("/trips", json={
        "primary_destination": destination, "origin_city": origin,
        "start_date": first_day.isoformat(), "end_date": (first_day + timedelta(days=days - 1)).isoformat(),
        "travelers_count": 2, "travel_group_type": "couple", "pace": "balanced",
        "interests": ["history", "food"], "budget_currency": "USD",
    })
    if created.status_code != 201:
        return {"technical_failure": f"create_http_{created.status_code}"}, {}
    trip_id = created.json()["data"]["trip_id"]
    started = client.post(f"/trips/{trip_id}/generate")
    job_status = None
    if started.status_code == 202:
        job_id = started.json()["data"]["job_id"]
        for _ in range(_JOB_POLLS):
            job = client.get(f"/trips/{trip_id}/jobs/{job_id}").json()["data"]
            job_status = job.get("status")
            if job_status in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(1)
    elif started.status_code == 200:
        job_status = "succeeded"
    else:
        return {"technical_failure": f"generate_http_{started.status_code}"}, {}

    snapshot: dict[str, Any] = {"trip_id": trip_id, "responses": {}}
    for template in UI_ENDPOINTS:
        path = template.format(trip_id=trip_id)
        response = client.get(path)
        snapshot["responses"][path] = {"status": response.status_code, "body": response.json()}
    # Developer view also lists each branch's revisions.
    branches = (snapshot["responses"][f"/trips/{trip_id}/branches"]["body"].get("data") or {}).get("branches") or []
    for branch in branches:
        path = f"/trips/{trip_id}/branches/{branch['branch_id']}/revisions"
        response = client.get(path)
        snapshot["responses"][path] = {"status": response.status_code, "body": response.json()}
    api_state = snapshot["responses"][f"/trips/{trip_id}"]["body"].get("data", {}).get("planning_state") or {}
    saved = get_planning_state_repository().get_by_trip_id(trip_id)

    flight = api_state.get("flight_inventory_report") or {}
    offers = flight.get("offers") or []
    route = api_state.get("route_feasibility_report") or {}
    legs = route.get("legs") or []
    plan = api_state.get("experience_plan") or {}
    context = plan.get("route_feasibility_context") or {}
    accommodation = api_state.get("accommodation_inventory_report") or {}
    usage = api_state.get("provider_usage_report") or {}
    from app.models.flight import FlightSearchResult
    from app.models.planning_state import PlanningState

    state_model = PlanningState.model_validate(api_state) if api_state else None
    summary = _flight_summary(FlightSearchResult.model_validate(flight)) if flight else None
    return {
        "technical_failure": None if job_status == "succeeded" else f"job_{job_status}",
        "job_status": job_status,
        "trip": {"origin": origin, "destination": destination, "days": days},
        "saved_state_found": saved is not None,
        "saved_offers": len(saved.flight_inventory_report.offers) if saved and saved.flight_inventory_report else None,
        "flight": summary,
        "flight_offers_detail": [_offer_detail(offer) for offer in offers],
        "accommodation": {
            "status": accommodation.get("status"),
            "availability_reason": accommodation.get("availability_reason"),
            "message": accommodation.get("message"),
        },
        "routes": {
            "report_status": route.get("status"),
            "legs": len(legs),
            "legs_verified": sum(1 for leg in legs if leg.get("status") == "success"),
            "context_status": context.get("data_status"),
            "context_segments": sum(len(day.get("segments") or []) for day in context.get("daily_route_feasibility") or []),
        },
        "weather": {k: (api_state.get("weather_context") or {}).get(k) for k in ("data_status", "missing_reason")},
        "holidays": {k: (api_state.get("holiday_context") or {}).get(k) for k in ("data_status", "missing_reason")},
        "currency": {k: (api_state.get("currency_context") or {}).get(k) for k in ("data_status", "missing_reason")},
        "llm_stages": _llm_records(state_model) if state_model is not None else {},
        "readiness_status": (api_state.get("validation_report") or {}).get("readiness_status"),
        "geoapify_credits_used": usage.get("credits_used"),
        "ui_endpoints_ok": all(entry["status"] == 200 for entry in snapshot["responses"].values()),
    }, snapshot


_ACCOMMODATION_REASONS = frozenset({"no_source_configured", "local_source_no_matching_offers", "provider_failed",
                                     "offers_found", "search_not_possible"})


def e2e_detail_checks(e2e: dict[str, Any], plan: dict[str, Any]) -> list[tuple[str, bool | None, Any]]:
    """Version-4 checks of the SAVED trip (each: name, passed, observed)."""
    out: list[tuple[str, bool | None, Any]] = []
    flight = e2e.get("flight") or {}
    details = e2e.get("flight_offers_detail") or []
    out.append(("the trip was saved and the API returns the same flight offers",
                e2e.get("saved_state_found") is True and e2e.get("saved_offers") == len(details),
                {"saved": e2e.get("saved_offers"), "api": len(details)}))
    out.append(("every UI endpoint answered 200", e2e.get("ui_endpoints_ok") is True, None))
    usable = [d for d in details if d["priced"] and d["currency"] and len(d["currency"]) == 3
              and d["currency"].isupper() and d["booking_link_https"] and d["departs"]]
    out.append(("Kiwi offers are usable: price, currency, https booking link, departure airport (all offers)",
                bool(details) and len(usable) == len(details), {"offers": len(details), "usable": len(usable)}))
    sides = flight.get("location_resolution") or []
    moved = [side for side in sides if side.get("method") == "nearby_airport"]
    problems = [p for side in sides for p in airport_quality_problems(side)]
    out.append(("airport provenance: the place without an airport was substituted with full evidence",
                bool(moved) and not problems, {"substituted": [m.get("airport_code") for m in moved],
                                                "problems": problems}))
    origin = next((m for m in moved if m.get("role") == "origin"), None)
    if origin is not None:
        out.append(("every offer departs from the substituted origin airport",
                    bool(details) and all(d["departs"] == origin.get("airport_code") for d in details),
                    sorted({d["departs"] for d in details if d["departs"]})))
    accommodation = e2e.get("accommodation") or {}
    message = str(accommodation.get("message") or "")
    # "scrape"/"scraped"/"scraping" as a WORD (an internal source id such as
    # `manual_local_scraped_..._default` is one identifier, not a claim)
    implies_scraping = re.search(r"\bscrap(e|ed|es|er|ers|ing)\b", message, re.IGNORECASE) is not None
    out.append(("accommodation states an honest reason and never implies live scraping",
                accommodation.get("availability_reason") in _ACCOMMODATION_REASONS and not implies_scraping,
                {k: accommodation.get(k) for k in ("status", "availability_reason")}))
    credits = e2e.get("geoapify_credits_used")
    cap = plan.get("max_geoapify_credits")
    out.append(("Geoapify credits within the cap", credits is not None and cap is not None and credits <= cap,
                {"used": credits, "cap": cap}))
    attempts = sum(len(stage.get("attempts") or []) for stage in (e2e.get("llm_stages") or {}).values())
    out.append(("LLM requests within the combined cap", attempts <= plan.get("max_llm_requests_combined", 0),
                {"attempts": attempts, "cap": plan.get("max_llm_requests_combined")}))
    out.append(("LLM stage outcomes (informational)", None, {
        name: {k: stage.get(k) for k in ("result", "attempted", "failover_reason", "retry_after")}
        for name, stage in (e2e.get("llm_stages") or {}).items()
    }))
    return out


def secret_values_absent(text: str, names: tuple[str, ...] = ("GEOAPIFY_API_KEY", "GROQ_API_KEY",
                                                               "GEMINI_API_KEY", "SESSION_SECRET_KEY")) -> bool:
    """True when no credential of this process appears in `text` (the values
    are compared, never printed)."""
    return not any(value and len(value) >= 8 and value in text
                   for value in (os.environ.get(name, "").strip() for name in names))


def airport_quality_problems(side: dict[str, Any]) -> list[str]:
    """Why a substituted airport is NOT acceptable for an ordinary commercial
    flight (empty = acceptable): evidence of scheduled airline service, a land
    airport type, a code, a location, a source and a rationale are all needed."""
    if side.get("method") != "nearby_airport":
        return []
    problems = []
    if not side.get("commercial_service_evidence"):
        problems.append("no scheduled-service evidence")
    if side.get("airport_type") not in _LAND_AIRPORT_TYPES:
        problems.append(f"not a land airport with service ({side.get('airport_type')})")
    if not side.get("airport_code"):
        problems.append("no IATA code")
    if side.get("airport_lat") is None or side.get("airport_lon") is None:
        problems.append("no location")
    if not side.get("lookup_source") or not side.get("selection_rationale"):
        problems.append("no source or rationale")
    return problems


def route_verdict(summary: dict[str, Any]) -> tuple[bool, str]:
    """PASS/FAIL for a route whose place may have no airport of its own."""
    reason = summary["availability_reason"]
    sides = summary["location_resolution"]
    problems = [p for s in sides for p in airport_quality_problems(s)]
    if problems:
        return False, "a substituted airport is not acceptable: " + "; ".join(problems)
    if reason == "offers_found":
        return True, "offers; every substituted airport has scheduled-service evidence and provenance"
    if reason in _HONEST_NO_OFFER:
        return True, f"no offers, honestly classified ({reason})"
    if reason == "search_completed_no_offers":
        loose = [s for s in sides if s["method"] not in ("airport_code", "nearby_airport")]
        return (not loose), ("a true empty search between commercial airports" if not loose
                             else "a search by NAME was reported as no flights")
    return False, f"unexpected outcome ({reason}, {summary['failure_kind']})"


def checks(report: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def check(name: str, passed: bool | None, observed: Any) -> None:
        results.append({"check": name, "result": "n/a" if passed is None else ("pass" if passed else "FAIL"),
                        "observed": observed})

    schema = report.get("kiwi_schema")
    if schema is not None:
        check("kiwi discovery succeeded and names a flight tool", schema["flight_tool"] is not None,
              {k: schema[k] for k in ("status", "failure_kind", "tools", "flight_tool")})

    probes = report.get("kiwi") or {}
    for label, summary in probes.items():
        check(f"kiwi {label}: no provider or request-cap failure",
              summary["availability_reason"] != "provider_failed",
              {k: summary[k] for k in ("status", "availability_reason", "failure_kind")})
        check(f"kiwi {label}: every price carries the currency Kiwi stated",
              summary["priced_offers"] == summary["priced_offers_with_stated_currency"], summary["currencies"])
        check(f"kiwi {label}: at most {SEARCHES_PER_ROUTE} searches",
              (summary["search_attempts"] or 0) <= SEARCHES_PER_ROUTE, summary["search_attempts"])
    if "control" in probes:
        check("kiwi control: offers found", probes["control"]["availability_reason"] == "offers_found",
              probes["control"])
    if "no_airport_in_place" in probes:
        passed, why = route_verdict(probes["no_airport_in_place"])
        check("kiwi no_airport_in_place: offers with provenance, or an honest no-offer reason", passed,
              {"why": why, **probes["no_airport_in_place"]})
        found = probes["no_airport_in_place"]["availability_reason"] == "offers_found"
        if (report.get("plan") or {}).get("airport_lookup"):
            # The objective: with reliable airport data, USEFUL offers -- an honest
            # "unconfirmed" is not success here.
            check("kiwi no_airport_in_place with the airport lookup: offers found", found,
                  probes["no_airport_in_place"]["location_resolution"])
        else:
            check("kiwi no_airport_in_place: offers found (informational, no lookup)", None, found)

    requests = report.get("requests") or {}
    reported = report.get("kiwi_http_requests_reported_total")
    observed = (requests.get("sent_by_service") or {}).get("kiwi", 0)
    if requests:
        check("kiwi HTTP requests: the guard saw exactly the requests the client counted",
              reported is not None and observed == reported and observed > 0,
              {"guard_observed": observed, "client_reported": reported, "by_package": requests.get("by_package")})
        cap = (report.get("plan") or {}).get("max_requests_by_service", {}).get("kiwi")
        check("kiwi HTTP requests within the planned cap", cap is not None and observed <= cap,
              {"observed": observed, "cap": cap})

    e2e = report.get("end_to_end")
    if e2e and "skipped" not in e2e:
        ok = e2e.get("technical_failure") is None
        check("end-to-end generation completed", ok, e2e.get("technical_failure"))
        if ok:
            flight = e2e["flight"] or {}
            check("flight offers reached the stored itinerary", flight.get("offers", 0) > 0, flight)
            routes = e2e["routes"]
            consistent = (routes["context_status"] == "live") == (routes["legs"] > 0 and routes["legs"] == routes["legs_verified"])
            check("route context agrees with the final route report", consistent, routes)
            check("route context segments == verified legs", routes["context_segments"] == routes["legs_verified"], routes)
            wasted = [
                name for name, stage in e2e["llm_stages"].items()
                if [a["transport_failure_kind"] for a in stage["attempts"]][-2:] == ["rate_limit", "rate_limit"]
                and len({a["provider"] for a in stage["attempts"][-2:]}) == 1
            ]
            check("no same-provider retry refused again after a 429", not wasted, wasted)
            check("accommodation reports why it has no offers",
                  e2e["accommodation"]["availability_reason"] is not None, e2e["accommodation"])
            if "flight_offers_detail" in e2e:
                for name, passed, observed in e2e_detail_checks(e2e, report.get("plan") or {}):
                    check(name, passed, observed)
    return results


def reported_total(report: dict[str, Any]) -> int | None:
    """The Kiwi client's own count over every exchange of this run (None when
    no exchange was observable)."""
    parts = [(report.get("kiwi_schema") or {}).get("kiwi_http_requests_reported")]
    parts += [s.get("kiwi_http_requests_reported") for s in (report.get("kiwi") or {}).values()]
    parts.append(((report.get("end_to_end") or {}).get("flight") or {}).get("kiwi_http_requests_reported"))
    known = [p for p in parts if p is not None]
    return sum(known) if known else None


def airport_dataset_summary(path: Path | None) -> dict[str, Any]:
    """Reads the local OurAirports file (no request): its fingerprint and how
    many airports have scheduled airline service, or the problem with it."""
    from app.providers.flights.airport_lookup import AirportDataUnavailable, load_dataset

    if path is None:
        return {"problem": "--airport-lookup needs --airport-data <path to OurAirports airports.csv>."}
    try:
        dataset = load_dataset(path)
    except AirportDataUnavailable as exc:
        return {"problem": f"the airport data file cannot be used ({exc.kind})."}
    scheduled = [row for row in dataset.rows if row.scheduled]
    return {
        "version": dataset.version,
        "land_airports_with_iata": len(dataset.rows),
        "with_scheduled_service": len(scheduled),
        "by_type_with_scheduled_service": dict(Counter(row.airport_type for row in scheduled)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M2 Phase 2 bounded live verification (development only).")
    parser.add_argument("--skip-e2e", action="store_true", help="Kiwi probes only (no LLM key needed).")
    parser.add_argument("--airport-lookup", action="store_true",
                        help="Look up airports with scheduled airline service (Geoapify key for geocoding; "
                             "at most 4 requests / 4 credits) -- needs --airport-data.")
    parser.add_argument("--airport-data", type=Path,
                        help="A local OurAirports airports.csv (public domain; downloaded by you, never by this script).")
    parser.add_argument("--e2e-only", action="store_true",
                        help="Version 4: ONE generation through the HTTP routes, no Kiwi probes (needs --airport-data).")
    parser.add_argument("--ui-snapshot", type=Path,
                        help="With --e2e-only: write the API responses the page loads (outside the repository).")
    parser.add_argument("--execute", action="store_true", help="Send live requests. Without it, a dry run.")
    parser.add_argument("--out", type=Path, help="Also write the JSON report here (outside the repository).")
    args = parser.parse_args(argv)

    if phase1.production_selected():
        print("Refused: APP_ENV is production (or the configuration could not be read).", file=sys.stderr)
        return 2
    if args.e2e_only and args.skip_e2e:
        print("Refused: --e2e-only and --skip-e2e exclude each other.", file=sys.stderr)
        return 2
    if args.ui_snapshot is not None and not args.e2e_only:
        print("Refused: --ui-snapshot needs --e2e-only.", file=sys.stderr)
        return 2
    if args.ui_snapshot is not None and not phase1._outside_repository(args.ui_snapshot):
        print("Refused: --ui-snapshot must be outside the repository.", file=sys.stderr)
        return 2
    e2e = not args.skip_e2e
    probes = not args.e2e_only
    lookup = args.airport_lookup or args.e2e_only
    dataset: dict[str, Any] = {}
    if lookup:
        dataset = airport_dataset_summary(args.airport_data)
        if dataset.get("problem"):
            print(f"Refused: {dataset['problem']}", file=sys.stderr)
            return 2
    e2e_cities = (E2E_ONLY_CITY, E2E_ONLY_ORIGIN) if args.e2e_only else (E2E_CITY,)
    if e2e and any(phase1.normalize_city(city) in phase1.frozen_destinations() for city in e2e_cities):
        print("Refused: the end-to-end city belongs to a frozen benchmark set.", file=sys.stderr)
        return 2
    if args.out is not None and not phase1._outside_repository(args.out):
        print("Refused: --out must be outside the repository.", file=sys.stderr)
        return 2
    plan = budget_plan(e2e, lookup, probes)
    header = {"version": VERSION, "kiwi_probes": [list(probe) for probe in KIWI_PROBES] if probes else [],
              "e2e_city": (E2E_ONLY_CITY if args.e2e_only else E2E_CITY) if e2e else None,
              "e2e_origin": (E2E_ONLY_ORIGIN if args.e2e_only else E2E_ORIGIN) if e2e else None, "plan": plan,
              "airport_data": dataset if lookup else None}
    if not args.execute:
        print(json.dumps({"dry_run": True, "requests_sent": 0, **header,
                          "preflight": phase1.preflight(e2e) if e2e else None}, indent=2, ensure_ascii=False))
        return 0

    if (lookup or e2e) and not phase1._read_key("GEOAPIFY_API_KEY", prompt=True):
        print("Refused: the airport lookup and the generation need GEOAPIFY_API_KEY.", file=sys.stderr)
        return 2
    if e2e and not (os.environ.get("GEMINI_API_KEY") and os.environ.get("GEMINI_MODEL")):
        phase1._read_key("GROQ_API_KEY", prompt=True)
    workdir = Path(tempfile.mkdtemp(prefix="travelobligator_m2p2_verify_"))
    phase1._configure(workdir)
    # Kiwi (and the lookup) ON for this process only; nothing persists beyond it.
    os.environ.update({
        "FLIGHT_PROVIDER": "kiwi_mcp", "KIWI_MCP_ENABLED": "true",
        "KIWI_MCP_MAX_HTTP_REQUESTS_PER_SESSION": str(KIWI_HTTP_PER_SESSION),
        "KIWI_MCP_DISCOVERY_CACHE_SECONDS": "3600",
        "FLIGHT_AIRPORT_LOOKUP_PROVIDER": "ourairports_file" if lookup else "not_connected",
    })
    if lookup:
        os.environ["FLIGHT_AIRPORT_DATA_PATH"] = str(args.airport_data.expanduser().resolve())
    if args.e2e_only:
        # The HTTP routes need a session secret: a throwaway one, this process only.
        os.environ.setdefault("SESSION_SECRET_KEY", os.urandom(32).hex())
        os.environ["SIGNUP_MODE"] = "open"  # throwaway Local JSON store of this process only
    from app.core.config import get_settings

    get_settings.cache_clear()
    if e2e:
        checked = phase1.preflight(True)
        if checked["problems"]:
            print("Refused before any request: " + "; ".join(checked["problems"]), file=sys.stderr)
            return 2
    guard = Guard(caps=dict(plan["max_requests_by_service"]), llm_cap=plan["max_llm_requests_combined"])
    phase1.install_guard(guard)
    if not install_httpx2_guard(guard):
        print("Refused: httpx2 (the MCP SDK's HTTP package) is not importable, so Kiwi cannot be guarded.",
              file=sys.stderr)
        return 2

    report: dict[str, Any] = {"verification": "m2_phase2", **header}
    snapshot: dict[str, Any] = {}
    if probes:
        report["kiwi_schema"] = discover_schema()
        report["kiwi"] = run_kiwi_probes() if guard.stopped_by is None else {}
    if e2e and guard.stopped_by is not None:
        report["end_to_end"] = {"skipped": "stopped"}
    elif e2e and args.e2e_only:
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            report["end_to_end"], snapshot = run_api_end_to_end(
                client, origin=E2E_ONLY_ORIGIN, destination=E2E_ONLY_CITY, days=E2E_DAYS)
    elif e2e:
        report["end_to_end"] = run_end_to_end()
    report["kiwi_http_requests_reported_total"] = reported_total(report)
    report["requests"] = {
        "sent_by_service": dict(sorted(guard.sent.items())),
        "sent_total": sum(guard.sent.values()),
        "by_package": dict(sorted(guard.by_package.items())),
        "refused": dict(sorted(guard.refused.items())),
        "http_statuses": dict(sorted(guard.statuses.items())),
        "stopped_by": guard.stopped_by,
    }
    report["checks"] = checks(report)
    snapshot_text = json.dumps(snapshot, ensure_ascii=False, default=str) if snapshot else ""
    leak_free = secret_values_absent(json.dumps(report, default=str) + snapshot_text)
    report["checks"].append({"check": "no credential value in the report or the UI snapshot",
                             "result": "pass" if leak_free else "FAIL", "observed": None})
    if args.ui_snapshot is not None and snapshot and leak_free:
        args.ui_snapshot.expanduser().resolve().write_text(snapshot_text + "\n", encoding="utf-8")
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    print(text)
    if args.out is not None:
        args.out.expanduser().resolve().write_text(text + "\n", encoding="utf-8")
    return 1 if guard.stopped_by or any(item["result"] == "FAIL" for item in report["checks"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
