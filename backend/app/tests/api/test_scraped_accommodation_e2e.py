from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.models.accommodation import AccommodationSearchRequest
from app.providers.accommodation import (
    NotConnectedAccommodationProvider,
    ScrapedAccommodationProvider,
    get_accommodation_provider,
)
from app.providers.accommodation import scraped_adapter as scraped_adapter_module
from app.providers.gateway import provider_gateway
from app.tests.conftest import create_trip_payload

# Step 168E: full end-to-end integration tests proving the config-gated
# scraped_local accommodation provider works through the real HTTP API --
# generation, provider coverage, validation, and serialization -- while
# still only ever reading a manually-supplied local HTML fixture. Never a
# real website, never a network call.

_TEST_HTML_ONE_PROPERTY = """
<html><body>
<div class="property-card" data-property-id="alpha-1">
  <h2 class="property-name">TEST_ONLY_SCRAPED_PROPERTY_ALPHA</h2>
  <span class="price" data-currency="USD">120.50</span>
  <span class="rating">4.2</span>
  <span class="availability">available</span>
  <a class="booking-link" href="https://example-test-only-travel-blog.test/book/alpha-1">Book</a>
</div>
</body></html>
"""


def _write_html(tmp_path: Path, content: str) -> str:
    html_file = tmp_path / "scraped_accommodation_fixture.html"
    html_file.write_text(content, encoding="utf-8")
    return str(html_file)


def _enable_scraped_local(monkeypatch: pytest.MonkeyPatch, html_path: str) -> None:
    """Enables the scraped_local provider for the *real*, shared
    `provider_gateway` singleton used by the actual `/trips` API routes --
    mirroring how `conftest.py`'s own `_deterministic_places_provider`
    fixture patches `provider_gateway.places` for the same reason."""
    settings = Settings(
        _env_file=None,
        scraping_enabled=True,
        scraped_accommodation_provider_enabled=True,
        scraped_accommodation_html_path=html_path,
        accommodation_provider="scraped_local",
    )
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        provider_gateway, "accommodation_inventory", ScrapedAccommodationProvider()
    )


def _generate_trip(client: TestClient) -> str:
    create_response = client.post("/trips", json=create_trip_payload())
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200
    return trip_id


# ---------------------------------------------------------------------------
# 1/2. Default generation remains not_connected/empty, and never reads any
# local HTML path.
# ---------------------------------------------------------------------------


def test_default_generation_is_unavailable_with_no_local_html_file(
    client: TestClient,
) -> None:
    """As of Step 168F, default config selects `scraped_local`
    (`ScrapedAccommodationProvider`) -- but with no file at its default
    local path (`.data/manual_scrapes/accommodations.html`, resolved
    against the backend project root), it never fabricates an offer:
    generation still succeeds and reports an honest `unavailable`."""
    trip_id = _generate_trip(client)

    response = client.get(f"/trips/{trip_id}")
    report = response.json()["data"]["planning_state"]["accommodation_inventory_report"]

    assert report["status"] == "unavailable"
    assert report["offers"] == []


def test_default_settings_select_scraped_local_provider() -> None:
    """Structural proof of the Step 168F default: `accommodation_provider`
    defaults to `"scraped_local"` with scraping enabled, and the
    factory/gateway resolve `ScrapedAccommodationProvider` by default --
    but that provider still never fabricates data (see the
    `unavailable`-by-default tests above and in
    test_scraped_accommodation_adapter.py)."""
    default_settings = Settings(_env_file=None)
    assert default_settings.scraping_enabled is True
    assert default_settings.scraped_accommodation_provider_enabled is True
    assert default_settings.accommodation_provider == "scraped_local"
    assert isinstance(get_accommodation_provider(), ScrapedAccommodationProvider)


def test_default_settings_can_still_explicitly_opt_out_to_not_connected() -> None:
    """An operator can still explicitly select `not_connected`, even with
    scraping config left at its new default-enabled state."""
    settings = Settings(_env_file=None, accommodation_provider="not_connected")
    assert isinstance(
        get_accommodation_provider(settings.accommodation_provider),
        NotConnectedAccommodationProvider,
    )


# ---------------------------------------------------------------------------
# 3/4. Explicit scraped_local config produces a success report, exposed
# through GET /trips/{id}.
# ---------------------------------------------------------------------------


def test_scraped_local_produces_success_report_through_full_generate_flow(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)

    response = client.get(f"/trips/{trip_id}")
    assert response.status_code == 200
    report = response.json()["data"]["planning_state"]["accommodation_inventory_report"]

    assert report["status"] == "success"
    assert len(report["offers"]) == 1
    assert report["offers"][0]["property_name"] == "TEST_ONLY_SCRAPED_PROPERTY_ALPHA"


# ---------------------------------------------------------------------------
# 5/6/7/8. API serialization preserves data_status, scraped_provenance,
# official_provider=false, and missing fields stay null/unknown.
# ---------------------------------------------------------------------------


def test_api_serialization_preserves_scraped_provenance_and_data_status(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)
    response = client.get(f"/trips/{trip_id}")
    offer = response.json()["data"]["planning_state"]["accommodation_inventory_report"]["offers"][0]

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
    """This fixture's card has no `address`, `amenity`, or
    `cancellation-policy` elements -- those must stay null/empty end to
    end through the API, never guessed."""
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)
    response = client.get(f"/trips/{trip_id}")
    offer = response.json()["data"]["planning_state"]["accommodation_inventory_report"]["offers"][0]

    assert offer["address"] is None
    assert offer["amenities"] == []
    assert offer["cancellation_policy"] is None
    assert offer["total_price_amount"] is None


# ---------------------------------------------------------------------------
# 9/10. Provider coverage: hotel_prices reflects scraped success only with
# offers, and accommodations stays separate.
# ---------------------------------------------------------------------------


def test_provider_coverage_hotel_prices_reflects_scraped_success(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)
    response = client.get(f"/trips/{trip_id}/provider-coverage")
    coverage = response.json()["data"]["provider_coverage"]

    assert coverage["hotel_prices"] == "success"


def test_provider_coverage_accommodations_stays_separate_from_scraped_inventory(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)
    response = client.get(f"/trips/{trip_id}/provider-coverage")
    coverage = response.json()["data"]["provider_coverage"]

    # The OSM-backed accommodations coverage value is whatever the
    # deterministic test places provider produced -- never "success" as a
    # side effect of the unrelated scraped hotel_prices success.
    assert coverage["accommodations"] in {"not_connected", "open_poi_available"}


# ---------------------------------------------------------------------------
# 11/12. Validator treats scraped inventory as non-official and
# non-blocking.
# ---------------------------------------------------------------------------


def test_validation_report_labels_scraped_inventory_as_non_official_and_non_blocking(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)
    response = client.get(f"/trips/{trip_id}/validation-report")
    validation_report = response.json()["data"]["validation_report"]

    accommodation_warnings = [
        warning
        for warning in validation_report["warnings"]
        if warning["category"] == "accommodation_inventory"
    ]
    assert len(accommodation_warnings) == 1
    message_lower = accommodation_warnings[0]["message"].lower()
    assert "scraped_public_page" in message_lower
    assert "not official-provider data" in message_lower
    assert "has not been verified" in message_lower

    assert not any(
        issue["category"] == "accommodation_inventory"
        for issue in validation_report["critical_issues"]
    )
    assert validation_report["readiness_status"] != "blocked"


# ---------------------------------------------------------------------------
# 13. Scraped local provider does not change itinerary scheduling.
# ---------------------------------------------------------------------------


def test_scraped_local_does_not_change_itinerary_scheduling(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
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
    assert "TEST_ONLY_SCRAPED_PROPERTY_ALPHA" not in scheduled_names
    # The deterministic test places provider still schedules its usual
    # fixture attractions -- scraped accommodation data never displaces or
    # adds a scheduled experience.
    assert len(scheduled_names) > 0


# ---------------------------------------------------------------------------
# 14/15. Regeneration refusal and LangGraph-not-wired behavior unchanged.
# ---------------------------------------------------------------------------


def test_regeneration_still_refused_with_scraped_local_enabled(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)
    response = client.post(f"/trips/{trip_id}/regenerate")

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_NOT_AVAILABLE"


def test_full_generate_with_scraped_local_still_succeeds_without_langgraph(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    """LangGraph-not-wired-into-/generate is already asserted at the import
    level by test_generation_progress.py/test_planning_graph.py -- this
    just confirms the full generate flow still succeeds end to end with
    scraped_local enabled, i.e. enabling it doesn't change that."""
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    _enable_scraped_local(monkeypatch, html_path)

    trip_id = _generate_trip(client)

    response = client.get(f"/trips/{trip_id}/generation-progress")
    assert response.status_code == 200
    assert response.json()["data"]["generation_progress"]["status"] == "completed"


# ---------------------------------------------------------------------------
# No network/website call for the scraped accommodation path specifically
# (isolated to the provider itself, not the whole generate flow, since
# weather/holiday/currency adapters legitimately attempt their own real
# HTTP calls during a full generate and are not in scope here).
# ---------------------------------------------------------------------------


def test_scraped_accommodation_provider_itself_makes_no_network_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("ScrapedAccommodationProvider must not open a real httpx.Client")

    monkeypatch.setattr(httpx, "Client", _fail_if_called)

    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = Settings(
        _env_file=None,
        scraping_enabled=True,
        scraped_accommodation_provider_enabled=True,
        scraped_accommodation_html_path=html_path,
    )
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    request = AccommodationSearchRequest(
        destination="Testville, Testland",
        check_in_date=date(2026, 10, 10),
        check_out_date=date(2026, 10, 14),
        adults=2,
        rooms=1,
    )
    result = ScrapedAccommodationProvider().search_accommodations(request)

    assert result.status.value == "success"
