from __future__ import annotations

import ast
import inspect
from datetime import date
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.accommodation import (
    AccommodationAvailabilityStatus,
    AccommodationSearchRequest,
    AccommodationSearchStatus,
)
from app.models.common import DataStatus
from app.providers.accommodation import (
    NotConnectedAccommodationProvider,
    ScrapedAccommodationProvider,
    get_accommodation_provider,
)
from app.providers.accommodation import factory as factory_module
from app.providers.accommodation import scraped_adapter as scraped_adapter_module
from app.providers.gateway import ProviderGateway
from app.services.accommodation_inventory_service import AccommodationInventoryService

# Step 168C: config-gated local/manual scraped accommodation provider
# tests. Every test here uses a real local temp file written by the test
# itself -- never a real website, never a network call.

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

_TEST_HTML_MINIMAL_PROPERTY = """
<html><body>
<div class="property-card" data-property-id="beta-1">
  <h2 class="property-name">TEST_ONLY_SCRAPED_PROPERTY_BETA</h2>
</div>
</body></html>
"""


def _request() -> AccommodationSearchRequest:
    return AccommodationSearchRequest(
        destination="Testville, Testland",
        check_in_date=date(2026, 10, 10),
        check_out_date=date(2026, 10, 14),
        adults=2,
        rooms=1,
    )


def _enabled_settings(html_path: str | None, **overrides: object) -> Settings:
    fields: dict[str, object] = {
        "scraping_enabled": True,
        "scraped_accommodation_provider_enabled": True,
        "scraped_accommodation_html_path": html_path,
    }
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def _write_html(tmp_path: Path, content: str) -> str:
    html_file = tmp_path / "scraped_accommodation_fixture.html"
    html_file.write_text(content, encoding="utf-8")
    return str(html_file)


# ---------------------------------------------------------------------------
# 1/14. As of Step 168F, default config selects ScrapedAccommodationProvider
# (accommodation_provider="scraped_local"), and the factory resolves it by
# default -- but explicitly selecting "not_connected" still works.
# ---------------------------------------------------------------------------


def test_default_settings_accommodation_provider_is_scraped_local() -> None:
    settings = Settings(_env_file=None)
    assert settings.accommodation_provider == "scraped_local"


def test_factory_default_returns_scraped_local_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ACCOMMODATION_PROVIDER", raising=False)
    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings(_env_file=None))

    provider = get_accommodation_provider()

    assert isinstance(provider, ScrapedAccommodationProvider)


# ---------------------------------------------------------------------------
# 2. Scraped provider is used by default (Step 168F), but explicitly
# selecting "not_connected" still opts back out even with scraping
# config enabled.
# ---------------------------------------------------------------------------


def test_factory_returns_not_connected_when_explicitly_selected_even_if_scraping_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator can still explicitly opt out of the default
    scraped_local provider by setting accommodation_provider="not_connected",
    even with scraping_enabled/scraped_accommodation_provider_enabled both
    True."""
    monkeypatch.setattr(
        factory_module,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            scraping_enabled=True,
            scraped_accommodation_provider_enabled=True,
            accommodation_provider="not_connected",
        ),
    )

    provider = get_accommodation_provider()

    assert isinstance(provider, NotConnectedAccommodationProvider)


# ---------------------------------------------------------------------------
# 3. Scraped provider returns not_connected when scraping_enabled=False.
# ---------------------------------------------------------------------------


def test_scraped_provider_returns_not_connected_when_scraping_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = _enabled_settings(html_path, scraping_enabled=False)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.NOT_CONNECTED
    assert result.offers == []


# ---------------------------------------------------------------------------
# 4. Scraped provider returns not_connected when
# scraped_accommodation_provider_enabled=False.
# ---------------------------------------------------------------------------


def test_scraped_provider_returns_not_connected_when_provider_flag_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = _enabled_settings(html_path, scraped_accommodation_provider_enabled=False)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.NOT_CONNECTED
    assert result.offers == []


def test_scraped_provider_returns_unavailable_by_default_settings_with_no_local_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Truly default `Settings()` (Step 168F: scraping_enabled=True,
    scraped_accommodation_provider_enabled=True, and a default local HTML
    path) still never fabricates an offer -- with no file actually
    present at that default path, this honestly reports `unavailable`."""
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: Settings(_env_file=None))

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.UNAVAILABLE
    assert result.offers == []


def test_scraped_provider_returns_not_connected_when_explicitly_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, scraping_enabled=False)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.NOT_CONNECTED


# ---------------------------------------------------------------------------
# 5. Scraped provider returns unavailable when HTML path is missing.
# ---------------------------------------------------------------------------


def test_scraped_provider_returns_unavailable_when_path_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _enabled_settings(None)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.UNAVAILABLE
    assert result.offers == []


# ---------------------------------------------------------------------------
# 6. Scraped provider returns unavailable when HTML file does not exist.
# ---------------------------------------------------------------------------


def test_scraped_provider_returns_unavailable_when_file_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_path = str(tmp_path / "does_not_exist.html")
    settings = _enabled_settings(missing_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.UNAVAILABLE
    assert result.offers == []


# ---------------------------------------------------------------------------
# 7. Scraped provider parses a local HTML fixture when explicitly enabled.
# ---------------------------------------------------------------------------


def test_scraped_provider_parses_local_fixture_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].property_name == "TEST_ONLY_SCRAPED_PROPERTY_ALPHA"


# ---------------------------------------------------------------------------
# 8/9. Parsed offers have data_status=scraped_public_page, scraped
# provenance, and official_provider=False.
# ---------------------------------------------------------------------------


def test_scraped_provider_offer_has_scraped_public_page_data_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())
    offer = result.offers[0]

    assert offer.data_status == DataStatus.SCRAPED_PUBLIC_PAGE


def test_scraped_provider_offer_has_provenance_with_official_provider_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())
    offer = result.offers[0]

    assert offer.scraped_provenance is not None
    assert offer.scraped_provenance.official_provider is False
    assert offer.scraped_provenance.source_id == "manual_local_scraped_accommodation"


# ---------------------------------------------------------------------------
# Step 182E: ACCOMMODATION_MANUAL_HTML_SOURCE relabels source_id/
# source_name for provenance display only -- it never changes any real
# fact the parser extracted, and it never applies when the operator has
# already customized the source id/name themselves.
# ---------------------------------------------------------------------------


def test_manual_html_source_label_relabels_source_id_and_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = _enabled_settings(html_path, accommodation_manual_html_source="booking")
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())
    offer = result.offers[0]

    assert offer.scraped_provenance is not None
    assert offer.scraped_provenance.official_provider is False
    assert offer.scraped_provenance.source_id == "manual_local_scraped_accommodation_booking"
    assert "Booking.com" in offer.scraped_provenance.source_name
    assert "labeled by user" in offer.scraped_provenance.source_name
    assert "not official Booking.com data" in offer.scraped_provenance.source_name
    # Every real fact the parser extracted is unaffected by the label.
    assert offer.property_name == "TEST_ONLY_SCRAPED_PROPERTY_ALPHA"
    assert offer.nightly_price_amount == 120.50


def test_manual_html_source_label_generic_leaves_defaults_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = _enabled_settings(html_path, accommodation_manual_html_source="generic")
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())
    offer = result.offers[0]

    assert offer.scraped_provenance.source_id == "manual_local_scraped_accommodation"
    assert offer.scraped_provenance.source_name == "Manual local scraped accommodation source"


def test_manual_html_source_label_never_overrides_a_customized_source_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An operator who already set their own source_id/source_name keeps
    it exactly -- the label is only ever a default-naming convenience, not
    something imposed on an already-customized identity."""
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = _enabled_settings(
        html_path,
        accommodation_manual_html_source="booking",
        scraped_accommodation_source_id="my_own_source",
        scraped_accommodation_source_name="My Own Local Export",
    )
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())
    offer = result.offers[0]

    assert offer.scraped_provenance.source_id == "my_own_source"
    assert offer.scraped_provenance.source_name == "My Own Local Export"


def test_manual_html_source_label_change_busts_the_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cache key includes the manual-html source label, so switching
    labels never serves a stale, differently-labeled cached result."""
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    generic_settings = _enabled_settings(html_path, accommodation_manual_html_source="generic")
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: generic_settings)
    provider = ScrapedAccommodationProvider()

    generic_result = provider.search_accommodations(_request())
    assert generic_result.offers[0].scraped_provenance.source_id == (
        "manual_local_scraped_accommodation"
    )

    booking_settings = _enabled_settings(html_path, accommodation_manual_html_source="booking")
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: booking_settings)

    booking_result = provider.search_accommodations(_request())
    assert booking_result.offers[0].scraped_provenance.source_id == (
        "manual_local_scraped_accommodation_booking"
    )


# ---------------------------------------------------------------------------
# 10. Missing price/rating/availability/booking_url remains missing/
# unknown.
# ---------------------------------------------------------------------------


def test_scraped_provider_leaves_missing_fields_honestly_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_MINIMAL_PROPERTY)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())
    offer = result.offers[0]

    assert offer.nightly_price_amount is None
    assert offer.rating is None
    assert offer.availability_status == AccommodationAvailabilityStatus.UNKNOWN
    assert offer.booking_url is None
    assert offer.amenities == []


# ---------------------------------------------------------------------------
# 11. Scraped provider never calls network.
# ---------------------------------------------------------------------------


def test_scraped_provider_makes_no_network_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("ScrapedAccommodationProvider must not open a real httpx.Client")

    monkeypatch.setattr(httpx, "Client", _fail_if_called)

    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.SUCCESS


# ---------------------------------------------------------------------------
# 12. Scraped provider does not import requests/httpx/browser automation.
# ---------------------------------------------------------------------------


def test_scraped_adapter_module_has_no_disallowed_imports() -> None:
    source = inspect.getsource(scraped_adapter_module)
    tree = ast.parse(source)

    disallowed_substrings = (
        "langgraph",
        "langsmith",
        "httpx",
        "requests",
        "bs4",
        "beautifulsoup",
        "selenium",
        "playwright",
        "groq",
        "anthropic",
        "openai",
        "gemini",
        "google.generativeai",
        "kiwi",
        "mcp",
    )

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"disallowed import found: {name}"


# ---------------------------------------------------------------------------
# 13. Factory can select scraped provider with explicit provider name.
# ---------------------------------------------------------------------------


def test_factory_returns_scraped_provider_for_explicit_name() -> None:
    provider = get_accommodation_provider("scraped_local")
    assert isinstance(provider, ScrapedAccommodationProvider)


def test_factory_uses_settings_when_provider_is_scraped_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        factory_module, "get_settings", lambda: Settings(accommodation_provider="scraped_local")
    )
    provider = get_accommodation_provider()
    assert isinstance(provider, ScrapedAccommodationProvider)


def test_factory_selecting_scraped_local_creates_no_file_access_by_itself(
    tmp_path: Path,
) -> None:
    """Merely selecting the adapter from the factory must not touch the
    filesystem or network -- only calling `search_accommodations` (with
    both config flags on) would ever read a file."""
    provider = get_accommodation_provider("scraped_local")
    assert isinstance(provider, ScrapedAccommodationProvider)


# ---------------------------------------------------------------------------
# 15. ProviderGateway can use the scraped provider (injected or
# factory-selected) without inventing data.
# ---------------------------------------------------------------------------


def test_gateway_uses_injected_scraped_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    gateway = ProviderGateway(accommodation_inventory=ScrapedAccommodationProvider())
    result = gateway.search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert result.offers[0].property_name == "TEST_ONLY_SCRAPED_PROPERTY_ALPHA"
    # Nothing not present in the HTML is invented.
    assert result.offers[0].cancellation_policy is None


def test_gateway_uses_factory_selected_scraped_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    gateway = ProviderGateway(accommodation_inventory=get_accommodation_provider("scraped_local"))
    result = gateway.search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 1


# ---------------------------------------------------------------------------
# 16. AccommodationInventoryService can store a scraped result when the
# provider returns success.
# ---------------------------------------------------------------------------


def test_accommodation_inventory_service_stores_scraped_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.models.planning_state import PlanningState, TravelGroupType, TripRequest

    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    gateway = ProviderGateway(accommodation_inventory=ScrapedAccommodationProvider())
    service = AccommodationInventoryService(gateway=gateway)
    trip_request = TripRequest(
        primary_destination="Testville, Testland",
        start_date=date(2026, 10, 10),
        end_date=date(2026, 10, 14),
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )
    planning_state = PlanningState(trip_request=trip_request)

    result = service.build_report(planning_state)

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].data_status == DataStatus.SCRAPED_PUBLIC_PAGE
    assert result.offers[0].scraped_provenance.official_provider is False
