from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.providers.flights import (
    NotConnectedFlightProvider,
    ScrapedLocalFlightProvider,
    get_flight_provider,
)
from app.providers.flights import scraped_adapter as scraped_adapter_module
from app.providers.gateway import provider_gateway
from app.tests.conftest import create_trip_payload

# Step 169E: full end-to-end integration tests proving the default
# scraped_local flight provider works through the real HTTP API --
# generation, provider coverage, validation, and serialization -- while
# still only ever reading a manually-supplied local HTML fixture. Never a
# real website, never a network call.

_TEST_HTML_ONE_OFFER = """
<html><body>
<div class="flight-offer" data-offer-id="TEST_ONLY_FLIGHT_OFFER_ALPHA">
  <div class="outbound-segment">
    <span class="origin-airport">TST</span>
    <span class="destination-airport">DMO</span>
    <span class="carrier-name">TEST_ONLY_AIRLINE_ALPHA</span>
    <span class="flight-number">TEST_ONLY_FLIGHT_123</span>
  </div>
  <span class="total-price" data-currency="USD">452.10</span>
  <a class="booking-link" href="https://example-test-only-flight-search-page.test/book/alpha-1">Book</a>
</div>
</body></html>
"""


def _write_html(tmp_path: Path, content: str) -> str:
    html_file = tmp_path / "scraped_flight_fixture.html"
    html_file.write_text(content, encoding="utf-8")
    return str(html_file)


def _enable_scraped_local(monkeypatch: pytest.MonkeyPatch, html_path: str) -> None:
    """Enables the scraped_local flight provider for the *real*, shared
    `provider_gateway` singleton used by the actual `/trips` API routes --
    mirroring how test_scraped_accommodation_e2e.py patches
    `provider_gateway.accommodation_inventory` for the same reason."""
    settings = Settings(
        _env_file=None,
        scraping_enabled=True,
        scraped_flight_provider_enabled=True,
        scraped_flight_html_path=html_path,
        flight_provider="scraped_local",
    )
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    monkeypatch.setattr(provider_gateway, "flight_inventory", ScrapedLocalFlightProvider())


def _generate_trip(client: TestClient) -> str:
    create_response = client.post("/trips", json=create_trip_payload())
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200
    return trip_id


# ---------------------------------------------------------------------------
# Default generation remains unavailable, and never reads any local HTML
# path.
# ---------------------------------------------------------------------------


def test_default_generation_is_unavailable_with_no_local_html_file(
    client: TestClient,
) -> None:
    """Default config selects `scraped_local` (`ScrapedLocalFlightProvider`)
    -- but with no file at its default local path
    (`.data/manual_scrapes/flights.html`, resolved against the backend
    project root), it never fabricates an offer: generation still
    succeeds and reports an honest `unavailable`."""
    trip_id = _generate_trip(client)

    response = client.get(f"/trips/{trip_id}")
    report = response.json()["data"]["planning_state"]["flight_inventory_report"]

    assert report["status"] == "unavailable"
    assert report["offers"] == []


def test_default_settings_select_scraped_local_provider() -> None:
    default_settings = Settings(_env_file=None)
    assert default_settings.scraping_enabled is True
    assert default_settings.scraped_flight_provider_enabled is True
    assert default_settings.flight_provider == "scraped_local"
    assert isinstance(get_flight_provider(), ScrapedLocalFlightProvider)


def test_default_settings_can_still_explicitly_opt_out_to_not_connected() -> None:
    settings = Settings(_env_file=None, flight_provider="not_connected")
    assert isinstance(
        get_flight_provider(settings.flight_provider),
        NotConnectedFlightProvider,
    )


# ---------------------------------------------------------------------------
# Explicit scraped_local config produces a success report, exposed
# through GET /trips/{id}.
# ---------------------------------------------------------------------------


def test_scraped_local_produces_success_report_through_full_generate_flow(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)

    response = client.get(f"/trips/{trip_id}")
    assert response.status_code == 200
    report = response.json()["data"]["planning_state"]["flight_inventory_report"]

    assert report["status"] == "success"
    assert len(report["offers"]) == 1
    assert report["offers"][0]["offer_id"] == "TEST_ONLY_FLIGHT_OFFER_ALPHA"


# ---------------------------------------------------------------------------
# API serialization preserves data_status, scraped_provenance,
# official_provider=false, and missing fields stay null/unknown.
# ---------------------------------------------------------------------------


def test_api_serialization_preserves_scraped_provenance_and_data_status(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)
    response = client.get(f"/trips/{trip_id}")
    offer = response.json()["data"]["planning_state"]["flight_inventory_report"]["offers"][0]

    assert offer["data_status"] == "scraped_public_page"
    provenance = offer["scraped_provenance"]
    assert provenance is not None
    assert provenance["source_type"] == "scraped_public_page"
    assert provenance["provenance"] == "scraped_public_page"
    assert provenance["confidence"] == "experimental"
    assert provenance["official_provider"] is False


def test_api_serialization_preserves_missing_fields_as_null_or_unknown(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    """This fixture's offer has no `availability-status`, `baggage-policy`,
    or `cancellation-policy` elements -- those must stay null end to end
    through the API, never guessed."""
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)
    response = client.get(f"/trips/{trip_id}")
    offer = response.json()["data"]["planning_state"]["flight_inventory_report"]["offers"][0]

    assert offer["availability_status"] is None
    assert offer["baggage_policy"] is None
    assert offer["cancellation_policy"] is None
    assert offer["outbound_segments"][0]["carrier_code"] is None
    assert offer["outbound_segments"][0]["departure_time"] is None
    assert offer["outbound_segments"][0]["arrival_time"] is None
    assert offer["outbound_segments"][0]["duration_minutes"] is None


# ---------------------------------------------------------------------------
# Provider coverage: flights reflects scraped success only with offers.
# ---------------------------------------------------------------------------


def test_provider_coverage_flights_reflects_scraped_success(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)
    response = client.get(f"/trips/{trip_id}/provider-coverage")
    coverage = response.json()["data"]["provider_coverage"]

    assert coverage["flights"] == "success"


# ---------------------------------------------------------------------------
# Validator treats scraped inventory as non-official and non-blocking.
# ---------------------------------------------------------------------------


def test_validation_report_labels_scraped_inventory_as_non_official_and_non_blocking(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)
    response = client.get(f"/trips/{trip_id}/validation-report")
    validation_report = response.json()["data"]["validation_report"]

    flight_warnings = [
        warning
        for warning in validation_report["warnings"]
        if warning["category"] == "flight_inventory"
    ]
    assert len(flight_warnings) == 1
    message_lower = flight_warnings[0]["message"].lower()
    assert "scraped_public_page" in message_lower
    assert "not official-provider data" in message_lower
    assert "has not been verified" in message_lower

    assert not any(
        issue["category"] == "flight_inventory" for issue in validation_report["critical_issues"]
    )
    assert validation_report["readiness_status"] != "blocked"


# ---------------------------------------------------------------------------
# Scraped local flight provider does not change itinerary scheduling.
# ---------------------------------------------------------------------------


def test_scraped_local_does_not_change_itinerary_scheduling(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)
    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    experience_plan = response.json()["data"]["experience_plan"]

    scheduled_names = [
        experience["name"]
        for day_plan in experience_plan["daily_plans"]
        for experience in day_plan["experiences"]
    ]
    assert "TEST_ONLY_FLIGHT_OFFER_ALPHA" not in scheduled_names
    assert "TEST_ONLY_AIRLINE_ALPHA" not in scheduled_names
    # The deterministic test places provider still schedules its usual
    # fixture attractions -- scraped flight data never displaces or adds a
    # scheduled experience.
    assert len(scheduled_names) > 0


# ---------------------------------------------------------------------------
# Regeneration refusal and LangGraph-not-wired behavior unchanged.
# ---------------------------------------------------------------------------


def test_regeneration_still_refused_with_scraped_local_enabled(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)
    response = client.post(f"/trips/{trip_id}/regenerate")

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_NOT_AVAILABLE"


def test_full_generate_with_scraped_local_still_succeeds_without_langgraph(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    """LangGraph-not-wired-into-/generate is already asserted at the
    import level by test_generation_progress.py/test_planning_graph.py --
    this just confirms the full generate flow still succeeds end to end
    with scraped_local flight inventory enabled."""
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)

    response = client.get(f"/trips/{trip_id}/generation-progress")
    assert response.status_code == 200
    assert response.json()["data"]["generation_progress"]["status"] == "completed"


# ---------------------------------------------------------------------------
# No network/website call for the scraped flight path specifically
# (isolated to the provider itself, not the whole generate flow, since
# weather/holiday/currency adapters legitimately attempt their own real
# HTTP calls during a full generate and are not in scope here).
# ---------------------------------------------------------------------------


def test_scraped_flight_provider_itself_makes_no_network_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("ScrapedLocalFlightProvider must not open a real httpx.Client")

    monkeypatch.setattr(httpx, "Client", _fail_if_called)

    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    settings = Settings(
        _env_file=None,
        scraping_enabled=True,
        scraped_flight_provider_enabled=True,
        scraped_flight_html_path=html_path,
    )
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    from datetime import date

    from app.models.flight import FlightSearchRequest

    request = FlightSearchRequest(destination="LIS", departure_date=date(2026, 10, 10))
    result = ScrapedLocalFlightProvider().search_flights(request)

    assert result.status.value == "success"
