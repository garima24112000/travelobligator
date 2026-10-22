"""M2 Phase 1: the bounded live-verification script, hermetically -- refusals,
the plan, the request guard, the checks, and steps 1-3 against an in-process
mock network (no key, no network, no database)."""

from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import get_settings

_BACKEND_DIR = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _BACKEND_DIR / "scripts" / "verify_m2_integrations.py"
SENTINEL_KEY = "SENTINEL-M2-VERIFY-KEY"


@pytest.fixture(scope="module")
def script() -> Any:
    import sys

    name = "verify_m2_integrations_under_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module  # dataclasses resolve their module through sys.modules
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(name, None)


# -- refusals and the plan -------------------------------------------------------------------


def test_default_cities_are_not_frozen(script: Any) -> None:
    frozen = script.frozen_destinations()
    assert frozen  # the benchmark files were read
    for city in (*script.DEFAULT_CITIES, script.DEFAULT_E2E_CITY):
        assert script.normalize_city(city) not in frozen


def test_dry_run_sends_nothing_and_prints_the_caps(script: Any, capsys: pytest.CaptureFixture) -> None:
    assert script.main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["dry_run"] is True and output["requests_sent"] == 0
    plan = output["plan"]
    assert plan["max_requests_total"] == sum(plan["max_requests_by_service"].values())
    assert plan["max_geoapify_credits"] == len(script.DEFAULT_CITIES) + script.GEOAPIFY_CREDITS_PER_GENERATION
    assert plan["max_llm_requests"] == 8


def test_skip_e2e_plan_has_no_llm_and_no_generation_credits(script: Any) -> None:
    plan = script.budget_plan(2, e2e=False)
    assert plan["max_requests_by_service"]["llm"] == 0
    assert plan["max_geoapify_credits"] == 2
    # nager: 2 years x 2 cities + the covered-country list + the 2-year probe
    assert plan["max_requests_by_service"] == {"geoapify": 2, "open_meteo": 4, "nager_date": 7, "frankfurter": 6, "llm": 0}


def test_a_probe_that_already_passed_can_be_left_out(script: Any) -> None:
    plan = script.budget_plan(2, e2e=False, probes=("nager", "open_meteo"))
    assert plan["probes"] == ["nager", "open_meteo"]
    assert plan["max_requests_by_service"]["frankfurter"] == 4  # cities only: no probe


@pytest.fixture()
def no_configuration(script: Any, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """`_configure` rewrites os.environ for a live run; tests must never let it."""
    calls: list[str] = []
    monkeypatch.setattr(script, "_configure", lambda workdir: calls.append("configure"))
    monkeypatch.setattr(script, "install_guard", lambda guard: calls.append("guard"))
    from app.core.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)  # this checkout's .env never leaks in
    return calls


def test_a_prompted_key_reaches_the_application_settings(
    script: Any, monkeypatch: pytest.MonkeyPatch, no_configuration: list[str]
) -> None:
    # The first live run: the key typed at the hidden prompt was never put into
    # the environment, so the settings saw none and every city was not_connected.
    monkeypatch.delenv("GEOAPIFY_API_KEY", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(script.getpass, "getpass", lambda prompt: SENTINEL_KEY)
    assert script._read_key("GEOAPIFY_API_KEY", prompt=True) == SENTINEL_KEY
    assert script.preflight(e2e=False)["geoapify_key_visible"] is True


def test_preflight_reports_presence_only(script: Any, monkeypatch: pytest.MonkeyPatch, no_configuration: list[str]) -> None:
    monkeypatch.delenv("GEOAPIFY_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    missing = script.preflight(e2e=True)
    assert missing["geoapify_key_visible"] is False and missing["llm_key_visible"] is False
    assert len(missing["problems"]) == 2
    assert missing["settings_env_file"].endswith(".env")

    monkeypatch.setenv("GEOAPIFY_API_KEY", SENTINEL_KEY)
    present = script.preflight(e2e=False)
    assert present["geoapify_key_visible"] is True and present["problems"] == []
    assert SENTINEL_KEY not in json.dumps(present)


def test_execute_refuses_before_any_request_when_settings_see_no_key(
    script: Any, monkeypatch: pytest.MonkeyPatch, no_configuration: list[str], capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.delenv("GEOAPIFY_API_KEY", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert script.main(["--execute", "--skip-e2e"]) == 2
    assert "guard" not in no_configuration  # never reached the request stage
    assert "GEOAPIFY_API_KEY is not visible" in capsys.readouterr().err


def test_dry_run_includes_the_preflight(script: Any, capsys: pytest.CaptureFixture, no_configuration: list[str]) -> None:
    assert script.main(["--skip-e2e"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert set(output["preflight"]) >= {"geoapify_key_visible", "settings_env_file", "problems"}
    assert no_configuration == []  # a dry run configures nothing


def test_frozen_city_is_refused(script: Any, capsys: pytest.CaptureFixture) -> None:
    frozen_city = sorted(script.frozen_destinations())[0].title()
    assert script.main(["--city", f"{frozen_city}, Somewhere"]) == 2
    assert script.main(["--e2e-city", f"{frozen_city}, Somewhere"]) == 2
    assert "frozen benchmark set" in capsys.readouterr().err


def test_production_is_refused(script: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    assert script.production_selected() is True
    assert script.main([]) == 2


def test_out_inside_the_repository_is_refused(script: Any) -> None:
    assert script.main(["--out", str(_BACKEND_DIR / "report.json")]) == 2


# -- the request guard -------------------------------------------------------------------------


def test_guard_refuses_unknown_hosts(script: Any) -> None:
    guard = script.RequestGuard(caps={"geoapify": 5})
    with pytest.raises(PermissionError):
        guard.admit("mcp.kiwi.com")
    assert guard.refused["host_not_allowed"] == 1


def test_guard_enforces_per_service_caps(script: Any) -> None:
    guard = script.RequestGuard(caps={"nager_date": 1})
    assert guard.admit("date.nager.at") == "nager_date"
    with pytest.raises(PermissionError):
        guard.admit("date.nager.at")
    with pytest.raises(PermissionError):
        guard.admit("api.open-meteo.com")  # no cap given = 0


def test_guard_stops_on_auth_and_data_rate_limits_but_not_llm_rate_limits(script: Any) -> None:
    guard = script.RequestGuard(caps={"llm": 5, "geoapify": 5})
    guard.observe("llm", 429)
    assert guard.stopped_by is None  # left to the application's bounded failover
    guard.observe("geoapify", 429)
    assert guard.stopped_by == "geoapify:429"
    with pytest.raises(PermissionError):
        guard.admit("api.geoapify.com")

    guard = script.RequestGuard(caps={"llm": 5})
    guard.observe("llm", 401)
    assert guard.stopped_by == "llm:401"


# -- the checks ---------------------------------------------------------------------------------


def test_checks_report_unconfirmed_assumptions_as_failures(script: Any) -> None:
    report = {
        "country_and_context": {
            "cities": [],
            "probes": {
                "nager_uncovered_country": {"status": "failed", "reason": "request_failed"},
                "frankfurter_unpublished_currency": {"status": "unavailable", "reason": "currency_not_supported"},
                "open_meteo_beyond_horizon": {"status": "unavailable", "reason": "forecast_not_yet_available"},
            },
        }
    }
    results = {item["check"]: item["result"] for item in script.checks(report)}
    assert results["Nager.Date uncovered country (204 or 404) -> country_not_supported"] == "FAIL"
    assert results["Frankfurter 404/422 -> currency_not_supported (assumption)"] == "pass"


def test_checks_judge_the_end_to_end_fields(script: Any) -> None:
    e2e = {
        "technical_failure": None,
        "resolved_country_code": "SE",
        "holidays": {"country_code": "SE"},
        "flight": {"availability_reason": "search_completed_no_offers"},
        "change_requests": {"applicable": False, "can_regenerate": False},
        "routes": {"draft_says_not_connected": False},
    }
    results = {item["check"]: item["result"] for item in script.checks({"end_to_end": e2e})}
    assert results["holiday lookup used the geocoder's country"] == "pass"
    assert results["flights: no source configured (never 'no offers found')"] == "FAIL"
    assert results["change requests reported as not applicable"] == "pass"


# -- steps 1-3 against a mock network --------------------------------------------------------------

_GEO = {
    "Nara, Japan": ("Nara", "Japan", "jp"),
    "Panama City, Panama": ("Panama City", "Panama", "pa"),
}


def _geocode(text: str) -> dict[str, Any] | None:
    if text not in _GEO:
        return None
    city, country, code = _GEO[text]
    return {
        "city": city, "country": country, "country_code": code, "lon": 135.8, "lat": 34.7,
        "result_type": "city", "formatted": f"{city}, {country}",
        "rank": {"confidence": 1, "match_type": "full_match"}, "place_id": f"id-{code}",
        "bbox": {"lon1": 135.7, "lat1": 34.6, "lon2": 135.9, "lat2": 34.8},
    }


def _network(sent: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        sent.append(host)
        if host == "api.geoapify.com" and path == "/v1/geocode/search":
            hit = _geocode(request.url.params.get("text") or "")
            return httpx.Response(200, json={"results": [hit] if hit else []})
        if host == "date.nager.at":
            if path.endswith("/AvailableCountries"):
                return httpx.Response(200, json=[{"countryCode": "JP", "name": "Japan"},
                                                 {"countryCode": "PA", "name": "Panama"}])
            if path.endswith("/AQ"):
                return httpx.Response(204)  # what the live service answered
            year, code = path.rsplit("/", 2)[-2:]
            return httpx.Response(200, json=[{"date": f"{year}-01-01", "localName": "New Year", "name": "New Year",
                                              "countryCode": code}])
        if "frankfurter" in host:
            target = request.url.params["to"]
            if target == "VND":
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, json={"base": "USD", "date": "2026-10-21", "rates": {target: 150.0}})
        if host == "api.open-meteo.com":
            days = [(date.today() + timedelta(days=7 + offset)).isoformat() for offset in range(3)]
            return httpx.Response(200, json={"daily": {
                "time": days, "temperature_2m_max": [20, 21, 22], "temperature_2m_min": [10, 11, 12],
                "precipitation_probability_max": [0, 0, 0], "precipitation_sum": [0, 0, 0], "weather_code": [1, 1, 1],
            }})
        return httpx.Response(404)

    return handler


def test_steps_one_to_three_wire_the_real_adapters(
    script: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GEOAPIFY_API_KEY", SENTINEL_KEY)
    monkeypatch.setenv("GEOCODING_PROVIDER", "geoapify")
    monkeypatch.setenv("PLACES_PROVIDER", "geoapify")
    get_settings.cache_clear()
    sent: list[str] = []
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(_network(sent)), **kwargs)
    )
    try:
        result = script.run_country_and_context_steps(["Nara, Japan", "Panama City, Panama"], tmp_path)
    finally:
        get_settings.cache_clear()

    nara, panama = result["cities"]
    assert nara["country_code"] == "JP" and nara["expected_currency"] == "JPY"
    assert nara["currency"] == {"status": "success", "reason": None, "currency": "JPY", "rate_present": True}
    assert nara["holidays"]["status"] == "success"
    assert nara["weather"]["forecast_days"] == 3
    assert panama["country_code"] == "PA"
    assert panama["currency"]["reason"] == "currency_ambiguous"
    assert result["probes"]["nager_uncovered_country"]["reason"] == "country_not_supported"
    assert result["probes"]["frankfurter_unpublished_currency"]["reason"] == "currency_not_supported"
    assert result["probes"]["open_meteo_beyond_horizon"]["reason"] == "forecast_not_yet_available"
    assert result["step1_geoapify_credits"] == 2  # one geocode per city; coordinates came from the cache
    assert sent.count("api.geoapify.com") == 2
    # Panama (two currencies) sent no exchange-rate request; Nara and the probe did.
    assert sum(1 for host in sent if "frankfurter" in host) == 2

    checks = {item["check"]: item["result"] for item in script.checks({"country_and_context": result})}
    assert "FAIL" not in checks.values()
    assert checks["Nager.Date uncovered country (204 or 404) -> country_not_supported"] == "pass"
    assert SENTINEL_KEY not in json.dumps(result)


def _configured_mock(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    monkeypatch.setenv("GEOAPIFY_API_KEY", SENTINEL_KEY)
    monkeypatch.setenv("GEOCODING_PROVIDER", "geoapify")
    monkeypatch.setenv("PLACES_PROVIDER", "geoapify")
    get_settings.cache_clear()
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))


def test_weather_waits_for_coordinates(script: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent: list[str] = []
    unresolved = _network(sent)

    def nothing_resolves(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/geocode/search":
            sent.append(request.url.host)
            return httpx.Response(200, json={"results": []})
        return unresolved(request)

    _configured_mock(monkeypatch, nothing_resolves)
    try:
        result = script.run_country_and_context_steps(["Nara, Japan"], tmp_path, probes=("open_meteo",))
    finally:
        get_settings.cache_clear()

    assert result["cities"][0]["weather"] == {"skipped": "no_coordinates"}
    assert result["probes"]["open_meteo_beyond_horizon"] == {"skipped": "no_coordinates"}
    assert "api.open-meteo.com" not in sent
    results = {item["check"]: item["result"] for item in script.checks({"country_and_context": result})}
    # a missing location is reported as skipped, never as a forecast-horizon failure
    assert results["Open-Meteo beyond horizon -> forecast_not_yet_available, no request"] == "n/a"
    assert results["Nara, Japan: forecast (skipped: no coordinates)"] == "n/a"
    assert results["Nara, Japan: geocoder returned a country code"] == "FAIL"


def test_left_out_probes_send_nothing(script: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent: list[str] = []
    _configured_mock(monkeypatch, _network(sent))
    try:
        result = script.run_country_and_context_steps(["Panama City, Panama"], tmp_path, probes=("nager",))
    finally:
        get_settings.cache_clear()
    assert set(result["probes"]) == {"nager_uncovered_country"}
    assert not any("frankfurter" in host for host in sent)  # Panama sends none; the probe was left out
