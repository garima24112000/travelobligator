"""M2 Phase 2: the bounded live-verification script (version 3), hermetically
(no network, no key, no database)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _BACKEND_DIR / "scripts" / "verify_m2_phase2.py"
_FIXTURES = json.loads((_BACKEND_DIR / "app" / "tests" / "fixtures" / "kiwi_mcp_search_flight.json").read_text())


@pytest.fixture(scope="module")
def script() -> Any:
    name = "verify_m2_phase2_under_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(name, None)


def test_dry_run_sends_nothing(script: Any, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    assert script.main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["requests_sent"] == 0 and output["version"] == 4
    plan = output["plan"]
    assert plan["max_llm_requests_combined"] == 8 and plan["max_geoapify_credits"] == 100
    assert plan["max_requests_by_service"]["kiwi"] == plan["kiwi_sessions_max"] * script.KIWI_HTTP_PER_SESSION


def test_kiwi_only_plan_needs_no_key_and_states_its_caps(script: Any) -> None:
    plan = script.budget_plan(e2e=False)
    # 1 discovery + 2 routes x at most 2 searches = 5 sessions x 8 requests
    assert plan["kiwi_sessions_max"] == 5 and plan["max_requests_by_service"]["kiwi"] == 40
    assert plan["max_requests_total"] == 40
    assert plan["max_geoapify_credits"] == 0 and plan["max_llm_requests_combined"] == 0
    with_lookup = script.budget_plan(e2e=False, airport_lookup=True)
    # geocoding only (one request per side, 2 routes x 2 sides); the airport evidence is a local file
    assert with_lookup["max_requests_by_service"]["geoapify"] == 4 and with_lookup["max_geoapify_credits"] == 4
    assert with_lookup["max_requests_total"] == 44


_AIRPORTS_CSV = _BACKEND_DIR / "app" / "tests" / "fixtures" / "ourairports_sample.csv"


def test_the_lookup_needs_a_usable_airport_data_file(script: Any, capsys: pytest.CaptureFixture,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    assert script.main(["--skip-e2e", "--airport-lookup"]) == 2
    assert script.main(["--skip-e2e", "--airport-lookup", "--airport-data", "/nonexistent.csv"]) == 2
    capsys.readouterr()
    assert script.main(["--skip-e2e", "--airport-lookup", "--airport-data", str(_AIRPORTS_CSV)]) == 0
    output = json.loads(capsys.readouterr().out)
    data = output["airport_data"]
    assert data["version"].startswith("sha256:") and output["requests_sent"] == 0
    # seaplane bases, heliports and closed airports are never loaded
    assert set(data["by_type_with_scheduled_service"]) <= {"large_airport", "medium_airport", "small_airport"}


def test_the_kiwi_cap_matches_the_clients_own_session_cap(script: Any) -> None:
    from app.core.config import Settings

    assert script.KIWI_HTTP_PER_SESSION == Settings(_env_file=None).kiwi_mcp_max_http_requests_per_session


def test_the_e2e_city_is_not_frozen(script: Any) -> None:
    assert script.phase1.normalize_city(script.E2E_CITY) not in script.phase1.frozen_destinations()


def test_production_is_refused(script: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    assert script.main([]) == 2


def test_guard_allows_kiwi_refuses_others_and_caps_llm_together(script: Any) -> None:
    guard = script.Guard(caps={"kiwi": 2, "groq": 5, "gemini": 5}, llm_cap=2)
    assert guard.admit("mcp.kiwi.com", "httpx2") == "kiwi"
    with pytest.raises(PermissionError):
        guard.admit("evil.example")
    guard.admit("api.groq.com")
    guard.admit("generativelanguage.googleapis.com")
    with pytest.raises(PermissionError):
        guard.admit("api.groq.com")  # the COMBINED LLM cap is reached
    assert guard.sent["groq"] == 1 and guard.sent["gemini"] == 1
    assert guard.by_package["kiwi:httpx2"] == 1


def test_guard_stops_on_a_kiwi_429_but_leaves_llm_429_to_failover(script: Any) -> None:
    guard = script.Guard(caps={}, llm_cap=0)
    guard.observe("groq", 429)
    assert guard.stopped_by is None
    guard.observe("kiwi", 429)
    assert guard.stopped_by == "kiwi:429"


def test_the_httpx2_guard_sees_the_mcp_sdks_requests(script: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The v1 defect: Kiwi's traffic went through httpx2 and was never seen."""
    import asyncio

    import httpx2

    async def fake_send(self: Any, request: Any) -> Any:
        return httpx2.Response(200, request=request)

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", fake_send)
    guard = script.Guard(caps={"kiwi": 1}, llm_cap=0)
    assert script.install_httpx2_guard(guard) is True

    async def send_twice() -> list[Any]:
        transport = httpx2.AsyncHTTPTransport()
        request = httpx2.Request("POST", "https://mcp.kiwi.com/")
        first = await transport.handle_async_request(request)
        try:
            await transport.handle_async_request(request)
        except httpx2.ConnectError:
            return [first.status_code, "refused"]
        return [first.status_code, "sent"]

    assert asyncio.run(send_twice()) == [200, "refused"]
    assert guard.sent["kiwi"] == 1 and guard.refused["kiwi:cap"] == 1
    assert guard.statuses["kiwi:200"] == 1


def _probe(**overrides: Any) -> dict[str, Any]:
    base = {
        "status": "success", "availability_reason": "offers_found", "failure_kind": None,
        "searched": ["London", "Porto"], "search_attempts": 1, "kiwi_http_requests_reported": 3,
        "location_resolution": [
            {"role": "origin", "method": "place_name", "searched": "London", "airport_name": None,
             "airport_code": None, "straight_line_km": None, "lookup_source": None},
        ],
        "offers": 2, "priced_offers": 2, "priced_offers_with_stated_currency": 2, "currencies": ["USD"],
    }
    base.update(overrides)
    return base


def _results(script: Any, report: dict[str, Any]) -> dict[str, str]:
    return {item["check"]: item["result"] for item in script.checks(report)}


@pytest.mark.parametrize(
    ("reason", "methods", "passed"),
    [
        ("location_unconfirmed", ["place_name"], True),
        ("no_searchable_airport", ["not_resolved"], True),
        ("search_completed_no_offers", ["airport_code", "airport_code"], True),
        ("search_completed_no_offers", ["place_name", "airport_code"], False),  # the v1 outcome
        ("provider_failed", ["place_name"], False),
    ],
)
def test_route_verdict(script: Any, reason: str, methods: list[str], passed: bool) -> None:
    sides = [{"role": "origin", "method": m, "airport_name": None, "lookup_source": None, "straight_line_km": None}
             for m in methods]
    assert script.route_verdict(_probe(availability_reason=reason, location_resolution=sides))[0] is passed


_GOOD_AIRPORT = {
    "role": "origin", "method": "nearby_airport", "searched": "AAA", "airport_name": "Some Airport",
    "airport_code": "AAA", "straight_line_km": 12.4, "lookup_source": "ourairports", "airport_type": "large_airport",
    "airport_lat": 1.0, "airport_lon": 2.0, "commercial_service_evidence": "ourairports:scheduled_service=yes",
    "selection_rationale": "why", "dataset_version": "sha256:x",
}


def test_offers_through_a_nearby_airport_need_evidence_and_provenance(script: Any) -> None:
    assert script.route_verdict(_probe(location_resolution=[_GOOD_AIRPORT]))[0] is True
    for broken in ({"lookup_source": None}, {"selection_rationale": None}, {"airport_lat": None}):
        assert script.route_verdict(_probe(location_resolution=[{**_GOOD_AIRPORT, **broken}]))[0] is False


@pytest.mark.parametrize(
    "change",
    [
        # what the version-2 lookup run selected: a seaplane base and an executive airport
        {"airport_type": "seaplane_base", "commercial_service_evidence": None},
        {"airport_type": "medium_airport", "commercial_service_evidence": None},
        {"airport_type": "heliport"},
        {"airport_code": None},
    ],
)
def test_selection_quality_fails_an_unsuitable_airport_whatever_the_outcome(script: Any, change: dict) -> None:
    bad = {**_GOOD_AIRPORT, **change}
    for reason in ("offers_found", "search_completed_no_offers", "location_unconfirmed"):
        passed, why = script.route_verdict(_probe(availability_reason=reason, location_resolution=[bad]))
        assert passed is False and "not acceptable" in why


def test_with_the_lookup_on_offers_are_required_not_just_an_honest_reason(script: Any) -> None:
    unconfirmed = _probe(availability_reason="location_unconfirmed", location_resolution=[])
    with_lookup = {"plan": script.budget_plan(e2e=False, airport_lookup=True),
                   "kiwi": {"no_airport_in_place": unconfirmed}}
    assert _results(script, with_lookup)["kiwi no_airport_in_place with the airport lookup: offers found"] == "FAIL"
    without = {"plan": script.budget_plan(e2e=False), "kiwi": {"no_airport_in_place": unconfirmed}}
    assert _results(script, without)["kiwi no_airport_in_place: offers found (informational, no lookup)"] == "n/a"


def test_accounting_check_requires_the_guard_and_the_client_to_agree(script: Any) -> None:
    plan = script.budget_plan(e2e=False)
    agree = {"plan": plan, "kiwi_http_requests_reported_total": 8,
             "requests": {"sent_by_service": {"kiwi": 8}, "by_package": {"kiwi:httpx2": 8}}}
    results = _results(script, agree)
    assert results["kiwi HTTP requests: the guard saw exactly the requests the client counted"] == "pass"
    assert results["kiwi HTTP requests within the planned cap"] == "pass"
    v1 = {**agree, "requests": {"sent_by_service": {}, "by_package": {}}}  # what version 1 reported
    assert _results(script, v1)["kiwi HTTP requests: the guard saw exactly the requests the client counted"] == "FAIL"
    unobservable = {**agree, "kiwi_http_requests_reported_total": None}
    assert _results(script, unobservable)[
        "kiwi HTTP requests: the guard saw exactly the requests the client counted"] == "FAIL"


def test_checks_flag_a_price_without_a_stated_currency(script: Any) -> None:
    results = _results(script, {"kiwi": {"control": _probe(priced_offers_with_stated_currency=1)}})
    assert results["kiwi control: every price carries the currency Kiwi stated"] == "FAIL"


def test_reported_total_sums_every_observed_exchange(script: Any) -> None:
    report = {"kiwi_schema": {"kiwi_http_requests_reported": 2},
              "kiwi": {"control": _probe(), "no_airport_in_place": _probe(kiwi_http_requests_reported=None)}}
    assert script.reported_total(report) == 5
    assert script.reported_total({"kiwi": {}}) is None


def _e2e(**overrides: Any) -> dict[str, Any]:
    base = {
        "technical_failure": None,
        "flight": {"offers": 3},
        "routes": {"legs": 4, "legs_verified": 4, "context_status": "live", "context_segments": 4},
        "llm_stages": {"groq_reasoning": {"attempts": [{"provider": "groq", "transport_failure_kind": None}]}},
        "accommodation": {"availability_reason": "no_source_configured"},
    }
    base.update(overrides)
    return base


def test_e2e_checks_pass_for_a_consistent_run_and_catch_inconsistencies(script: Any) -> None:
    assert "FAIL" not in _results(script, {"end_to_end": _e2e()}).values()
    rate_limited = [{"provider": "groq", "transport_failure_kind": "rate_limit"}] * 2
    results = _results(script, {"end_to_end": _e2e(
        routes={"legs": 4, "legs_verified": 4, "context_status": "unavailable", "context_segments": 0},
        llm_stages={"groq_reasoning": {"attempts": rate_limited}},
        flight={"offers": 0},
    )})
    assert results["route context agrees with the final route report"] == "FAIL"
    assert results["no same-provider retry refused again after a 429"] == "FAIL"
    assert results["flight offers reached the stored itinerary"] == "FAIL"


def test_probes_run_through_the_real_service(script: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings
    from app.providers.flights import kiwi_mcp_adapter
    from app.providers.flights.kiwi_mcp_client import (
        KiwiMcpDiscoveryStatus,
        KiwiMcpToolCallResult,
        KiwiMcpToolCallStatus,
        KiwiMcpToolDiscoveryResult,
        McpToolInfo,
    )

    schema = {"properties": {"flyFrom": {"description": "IATA code, city or airport name"}, "flyTo": {},
                             "departureDate": {}, "adults": {}, "currency": {}}}
    sent: list[dict[str, Any]] = []

    class _Fake:
        def discover_tools(self) -> Any:
            return KiwiMcpToolDiscoveryResult(status=KiwiMcpDiscoveryStatus.SUCCESS, http_requests=2,
                                              tools=[McpToolInfo(name="search-flight", input_schema=schema)])

        def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            sent.append(arguments)
            payload = _FIXTURES["success_with_currency"] if arguments["flyFrom"] == "London" else _FIXTURES["empty"]
            return KiwiMcpToolCallResult(status=KiwiMcpToolCallStatus.SUCCESS, structured_content=payload,
                                         http_requests=3)

    settings = Settings(_env_file=None, kiwi_mcp_enabled=True, flight_provider="kiwi_mcp")
    kiwi_mcp_adapter.clear_discovery_cache()
    monkeypatch.setattr(kiwi_mcp_adapter, "KiwiMcpClient", lambda **kwargs: _Fake())
    monkeypatch.setattr(kiwi_mcp_adapter, "get_settings", lambda: settings)
    from app.providers.flights import factory as flight_factory

    monkeypatch.setattr(flight_factory, "get_settings", lambda: settings, raising=False)
    try:
        results = script.run_kiwi_probes()
    finally:
        kiwi_mcp_adapter.clear_discovery_cache()
    assert [(a["flyFrom"], a["flyTo"]) for a in sent] == [("London", "Porto"), ("Jersey City", "Orlando")]
    assert results["control"]["availability_reason"] == "offers_found"
    no_airport = results["no_airport_in_place"]
    # the lookup is not connected: the empty NAME search stays unconfirmed, never "no flights"
    assert no_airport["availability_reason"] == "location_unconfirmed"
    assert script.route_verdict(no_airport)[0] is True


# -- version 4: one generation through the HTTP routes (hermetic: fake Kiwi, fake geocoder) ------


def _kiwi_payload(origin: str, destination: str, *, booking: bool = True) -> dict[str, Any]:
    def itinerary(n: int) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": f"v4-fixture-{n}", "price": 100.0 + n, "priceFormatted": f"{100 + n} USD",
            "outbound": {"segments": [{"from": origin, "to": destination, "departureTime": "2027-01-10T08:00:00",
                                       "arrivalTime": "2027-01-10T11:00:00", "durationSeconds": 10800,
                                       "carrier": "ZZ", "carrierName": "TEST_ONLY_CARRIER", "flightNumber": f"ZZ{n}"}]},
            "inbound": None,
        }
        if booking:
            item["bookingUrl"] = f"https://example.invalid/book/{n}"
        return item

    return {"currency": "USD", "resultsCount": 2, "itineraries": [itinerary(1), itinerary(2)], "error": None}


def _install_v4_fakes(monkeypatch: pytest.MonkeyPatch, *, booking: bool = True) -> list[dict[str, Any]]:
    from app.core.config import Settings
    from app.providers.flights import airport_lookup, kiwi_mcp_adapter
    from app.providers.flights import factory as flight_factory
    from app.providers.flights.kiwi_mcp_client import (
        KiwiMcpDiscoveryStatus,
        KiwiMcpToolCallResult,
        KiwiMcpToolCallStatus,
        KiwiMcpToolDiscoveryResult,
        McpToolInfo,
    )
    from app.providers.geocoding.base import GeocodeHit

    schema = {"properties": {"flyFrom": {"description": "IATA code, city or airport name"}, "flyTo": {},
                             "departureDate": {}, "adults": {}, "currency": {}}}
    searches: list[dict[str, Any]] = []

    class _Kiwi:
        def discover_tools(self) -> Any:
            return KiwiMcpToolDiscoveryResult(status=KiwiMcpDiscoveryStatus.SUCCESS, http_requests=2,
                                              tools=[McpToolInfo(name="search-flight", input_schema=schema)])

        def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            searches.append(arguments)
            codes = (arguments["flyFrom"], arguments["flyTo"])
            payload = _kiwi_payload(*codes, booking=booking) if codes == ("EWR", "MCO") else _FIXTURES["empty"]
            return KiwiMcpToolCallResult(status=KiwiMcpToolCallStatus.SUCCESS, structured_content=payload,
                                         http_requests=3)

    class _Geocoder:
        hits = {"Jersey City, NJ": (40.7178, -74.0431), "Orlando, Florida": (28.5384, -81.3789)}

        def bound_to(self, context: Any) -> "_Geocoder":
            return self

        def search_destination(self, client: Any, query: str) -> Any:
            lat, lon = self.hits[query]
            return GeocodeHit(lat=lat, lon=lon, name=query, display_name=query, source="test",
                              provider_place_id="test/x", feature_class="place")

    from app.providers.gateway import provider_gateway

    # the script re-resolves these on the shared gateway: restore them afterwards
    monkeypatch.setattr(provider_gateway, "flight_inventory", provider_gateway.flight_inventory)
    monkeypatch.setattr(provider_gateway, "airport_lookup", provider_gateway.airport_lookup)
    settings = Settings(_env_file=None, kiwi_mcp_enabled=True, flight_provider="kiwi_mcp")
    kiwi_mcp_adapter.clear_discovery_cache()
    airport_lookup.clear_dataset_cache()
    monkeypatch.setattr(kiwi_mcp_adapter, "KiwiMcpClient", lambda **kwargs: _Kiwi())
    monkeypatch.setattr(kiwi_mcp_adapter, "get_settings", lambda: settings)
    monkeypatch.setattr(flight_factory, "get_flight_provider", lambda *a, **k: kiwi_mcp_adapter.KiwiMcpFlightProvider())
    monkeypatch.setattr(airport_lookup, "get_airport_lookup_provider", lambda *a, **k: airport_lookup.OurAirportsLookup(
        geocoder=_Geocoder(), data_path=str(_AIRPORTS_CSV)))
    return searches


def _v4(script: Any, client: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    return script.run_api_end_to_end(client, origin=script.E2E_ONLY_ORIGIN, destination=script.E2E_ONLY_CITY,
                                     days=2, signup=False)


def test_v4_generation_reaches_the_saved_trip_and_every_check_passes(
    script: Any, client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    searches = _install_v4_fakes(monkeypatch)
    e2e, snapshot = _v4(script, client)
    assert e2e["technical_failure"] is None, e2e
    assert [(s["flyFrom"], s["flyTo"]) for s in searches] == [("Jersey City", "Orlando"), ("EWR", "MCO")]
    assert e2e["saved_state_found"] and e2e["saved_offers"] == 2
    plan = script.budget_plan(e2e=True, airport_lookup=True, probes=False)
    detail = {name: (passed, observed) for name, passed, observed in script.e2e_detail_checks(e2e, plan)}
    assert detail.pop("LLM stage outcomes (informational)")[0] is None
    assert all(passed for passed, _ in detail.values()), {k: v for k, v in detail.items() if not v[0]}
    # the snapshot holds every response the page loads, for the browser replay
    trip_id = snapshot["trip_id"]
    recorded = set(snapshot["responses"])
    assert {t.format(trip_id=trip_id) for t in script.UI_ENDPOINTS} <= recorded
    assert any(path.endswith("/revisions") for path in recorded)  # Developer view's branch history
    state = snapshot["responses"][f"/trips/{trip_id}"]["body"]["data"]["planning_state"]
    assert state["flight_inventory_report"]["availability_reason"] == "offers_found"
    # Developer aid: `M2P2_WRITE_SNAPSHOT=<path>` keeps this synthetic snapshot for the
    # offline browser replay (frontend/e2e-replay/README.md). Nothing is written otherwise.
    target = os.environ.get("M2P2_WRITE_SNAPSHOT")
    if target:
        Path(target).write_text(json.dumps(snapshot, default=str), encoding="utf-8")


def test_v4_checks_fail_an_offer_without_a_booking_link(script: Any, client: Any,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    _install_v4_fakes(monkeypatch, booking=False)
    e2e, _ = _v4(script, client)
    plan = script.budget_plan(e2e=True, airport_lookup=True, probes=False)
    results = {name: passed for name, passed, _ in script.e2e_detail_checks(e2e, plan)}
    assert results["Kiwi offers are usable: price, currency, https booking link, departure airport (all offers)"] is False


def test_v4_plan_skips_the_probes_and_states_its_caps(script: Any) -> None:
    plan = script.budget_plan(e2e=True, airport_lookup=True, probes=False)
    caps = plan["max_requests_by_service"]
    # 1 discovery + up to 2 searches = 3 sessions x 8; the generation's credit cap covers its geocodes
    assert plan["kiwi_sessions_max"] == 3 and caps["kiwi"] == 24
    assert caps["geoapify"] == 100 and plan["max_geoapify_credits"] == 100
    assert plan["max_llm_requests_combined"] == 8
    assert plan["max_requests_total"] == 24 + 100 + 2 + 3 + 2 + 8


def test_v4_refusals(script: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    assert script.main(["--e2e-only"]) == 2  # needs the airport data
    assert script.main(["--e2e-only", "--skip-e2e", "--airport-data", str(_AIRPORTS_CSV)]) == 2
    assert script.main(["--ui-snapshot", "/tmp/x.json"]) == 2  # only with --e2e-only
    inside = str(_BACKEND_DIR / "snapshot.json")
    assert script.main(["--e2e-only", "--airport-data", str(_AIRPORTS_CSV), "--ui-snapshot", inside]) == 2


def test_v4_dry_run(script: Any, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    assert script.main(["--e2e-only", "--airport-data", str(_AIRPORTS_CSV)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["requests_sent"] == 0 and output["version"] == 4 and output["kiwi_probes"] == []
    assert (output["e2e_origin"], output["e2e_city"]) == (script.E2E_ONLY_ORIGIN, script.E2E_ONLY_CITY)


def test_credential_values_never_reach_the_report(script: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_value_1234567890")
    assert script.secret_values_absent('{"ok": true}') is True
    assert script.secret_values_absent('{"leak": "gsk_test_value_1234567890"}') is False


@pytest.mark.parametrize(
    ("message", "honest"),
    [
        ("No manually supplied local accommodation file was found (no live site is ever fetched). "
         "Checked configured source(s): manual_local_scraped_accommodation_default.", True),
        ("No local scraped-accommodation HTML files were found.", False),  # the wording this phase removed
        ("Live scraping of hotel sites found nothing.", False),
    ],
)
def test_the_accommodation_check_reads_wording_not_identifiers(script: Any, message: str, honest: bool) -> None:
    e2e = {"accommodation": {"status": "unavailable", "availability_reason": "no_source_configured", "message": message}}
    results = {name: passed for name, passed, _ in script.e2e_detail_checks(e2e, {})}
    assert results["accommodation states an honest reason and never implies live scraping"] is honest
