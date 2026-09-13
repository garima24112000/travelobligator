from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.flight import FlightSearchRequest, FlightSearchStatus
from app.providers.flights import ScrapedLocalFlightProvider
from app.providers.flights import scraped_adapter as scraped_adapter_module
from app.providers.flights import scraped_parser as scraped_parser_module
from app.storage.provider_cache_store import ProviderCacheStore

# Step 185D: multi-source flight ingestion tests --
# ScrapedLocalFlightProvider attempting several independent local files
# in one call. Every test uses a real local temp file -- never a real
# website, never a network call.


def _offer_html(offer_id: str, carrier: str) -> str:
    return f"""
<html><body>
<div class="flight-offer" data-offer-id="{offer_id}">
  <div class="outbound-segment">
    <span class="origin-airport">TST</span>
    <span class="destination-airport">DMO</span>
    <span class="carrier-name">{carrier}</span>
  </div>
</div>
</body></html>
"""


def _request() -> FlightSearchRequest:
    return FlightSearchRequest(origin="JFK", destination="LIS", departure_date=date(2026, 10, 10))


def _write_html(path: Path, content: str) -> str:
    path.write_text(content, encoding="utf-8")
    return str(path)


def _no_files_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Every source path points at a real, distinct tmp_path location
    (so nothing accidentally reads the real dev
    `.data/manual_scrapes/` directory during this test file), but none
    of the files are actually written -- the honest, fully-empty
    baseline every test builds on."""
    fields: dict[str, object] = {
        "scraping_enabled": True,
        "scraped_flight_provider_enabled": True,
        "scraped_flight_html_path": str(tmp_path / "generic.html"),
        "scraped_flight_html_path_skyscanner": str(tmp_path / "skyscanner.html"),
        "scraped_flight_html_path_google_flights": str(tmp_path / "google_flights.html"),
        "scraped_flight_html_path_kiwi_manual": str(tmp_path / "kiwi_manual.html"),
    }
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


# ---------------------------------------------------------------------------
# Backward compatibility: single generic file, no per-source files present.
# ---------------------------------------------------------------------------


def test_generic_only_behavior_is_unchanged_when_no_brand_files_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "generic.html", _offer_html("g-1", "TEST_ONLY_GENERIC_AIRLINE"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].outbound_segments[0].carrier_name == "TEST_ONLY_GENERIC_AIRLINE"


def test_all_missing_files_returns_unavailable_with_clear_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []
    assert result.message is not None
    assert len(result.warnings) == 4


# ---------------------------------------------------------------------------
# Multi-source: several source files present at once produce merged,
# distinctly-labeled offers.
# ---------------------------------------------------------------------------


def test_multiple_source_files_produce_merged_distinct_offers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "skyscanner.html", _offer_html("sk-1", "TEST_ONLY_SKYSCANNER_AIRLINE"))
    _write_html(
        tmp_path / "google_flights.html", _offer_html("gf-1", "TEST_ONLY_GOOGLE_FLIGHTS_AIRLINE")
    )
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 2
    carriers = {offer.outbound_segments[0].carrier_name for offer in result.offers}
    assert carriers == {"TEST_ONLY_SKYSCANNER_AIRLINE", "TEST_ONLY_GOOGLE_FLIGHTS_AIRLINE"}

    source_names = {offer.scraped_provenance.source_name for offer in result.offers}
    assert any("Skyscanner" in name for name in source_names)
    assert any("Google Flights" in name for name in source_names)
    for offer in result.offers:
        assert offer.scraped_provenance.official_provider is False
        assert "not official" in offer.scraped_provenance.source_name
        assert offer.provider.startswith("scraped:")
        assert offer.provider != "kiwi_mcp"


def test_missing_optional_source_file_does_not_block_successful_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "skyscanner.html", _offer_html("sk-1", "TEST_ONLY_SKYSCANNER_AIRLINE"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].outbound_segments[0].carrier_name == "TEST_ONLY_SKYSCANNER_AIRLINE"
    assert len(result.warnings) >= 1
    assert any("google_flights" in warning for warning in result.warnings)


def test_all_three_brand_sources_plus_generic_can_coexist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    brands = ["skyscanner", "google_flights", "kiwi_manual"]
    for brand in brands:
        _write_html(tmp_path / f"{brand}.html", _offer_html(f"{brand}-1", f"TEST_ONLY_{brand.upper()}"))
    _write_html(tmp_path / "generic.html", _offer_html("g-1", "TEST_ONLY_GENERIC"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 4
    carriers = {offer.outbound_segments[0].carrier_name for offer in result.offers}
    assert carriers == {"TEST_ONLY_GENERIC"} | {f"TEST_ONLY_{b.upper()}" for b in brands}
    assert result.warnings == []


def test_kiwi_manual_source_file_never_produces_kiwi_mcp_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "kiwi_manual.html", _offer_html("km-1", "TEST_ONLY_KIWI_MANUAL_AIRLINE"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.SUCCESS
    offer = result.offers[0]
    assert offer.provider != "kiwi_mcp"
    assert offer.provider.startswith("scraped:")
    assert "kiwi_manual" in offer.provider


def test_legacy_kiwi_label_resolves_to_kiwi_manual_parser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FLIGHT_MANUAL_HTML_SOURCE=kiwi (the legacy single-file label) must
    still route through the kiwi_manual parser/provenance, not a bare
    'kiwi' identity, and must never resemble kiwi_mcp."""
    _write_html(tmp_path / "generic.html", _offer_html("g-1", "TEST_ONLY_KIWI_LABELED_AIRLINE"))
    settings = _no_files_settings(
        tmp_path,
        flight_manual_html_source="kiwi",
        scraped_flight_html_path_skyscanner=None,
        scraped_flight_html_path_google_flights=None,
        scraped_flight_html_path_kiwi_manual=None,
    )
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.SUCCESS
    offer = result.offers[0]
    assert offer.provider != "kiwi_mcp"
    assert "not official Kiwi data" in offer.scraped_provenance.source_name


# ---------------------------------------------------------------------------
# Malformed source file: failed, not fabricated.
# ---------------------------------------------------------------------------


def test_malformed_source_file_does_not_fabricate_a_fallback_offer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "skyscanner.html", _offer_html("sk-1", "TEST_ONLY_SKYSCANNER_AIRLINE"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    def _raise(*args: object, **kwargs: object) -> None:
        raise ValueError("simulated malformed HTML")

    monkeypatch.setattr(scraped_parser_module, "parse_scraped_flight_html", _raise)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.FAILED
    assert result.offers == []
    assert "skyscanner" in (result.message or "")


def test_one_malformed_source_does_not_block_another_successful_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "skyscanner.html", _offer_html("sk-1", "TEST_ONLY_SKYSCANNER_AIRLINE"))
    _write_html(
        tmp_path / "google_flights.html", _offer_html("gf-1", "TEST_ONLY_GOOGLE_FLIGHTS_AIRLINE")
    )
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    from app.providers.flights.source_parsers import skyscanner as skyscanner_parser_module

    def _raise(*args: object, **kwargs: object) -> None:
        raise ValueError("simulated malformed skyscanner HTML")

    monkeypatch.setattr(skyscanner_parser_module, "parse", _raise)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].outbound_segments[0].carrier_name == "TEST_ONLY_GOOGLE_FLIGHTS_AIRLINE"
    assert any("skyscanner" in warning for warning in result.warnings)


# ---------------------------------------------------------------------------
# Provenance stays manual/local and not official for every slot; booking
# links are never confirmations.
# ---------------------------------------------------------------------------


def test_booking_links_are_source_supplied_never_confirmations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html = """
<html><body>
<div class="flight-offer" data-offer-id="sk-1">
  <div class="outbound-segment">
    <span class="origin-airport">TST</span>
    <span class="destination-airport">DMO</span>
  </div>
  <a class="booking-link" href="https://example-test-only-skyscanner.test/book/sk-1">Book</a>
</div>
</body></html>
"""
    _write_html(tmp_path / "skyscanner.html", html)
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    offer = result.offers[0]
    assert offer.booking_url == "https://example-test-only-skyscanner.test/book/sk-1"
    assert not hasattr(offer, "booking_confirmed")
    assert not hasattr(offer, "is_booked")


# ---------------------------------------------------------------------------
# Cache: per-source path contributes to the query hash.
# ---------------------------------------------------------------------------


def test_cache_separates_skyscanner_from_google_flights_for_the_same_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "skyscanner.html", _offer_html("sk-1", "TEST_ONLY_SKYSCANNER_AIRLINE"))
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedLocalFlightProvider(cache_store=cache_store)
    first_result = provider.search_flights(_request())
    assert len(first_result.offers) == 1

    _write_html(
        tmp_path / "google_flights.html", _offer_html("gf-1", "TEST_ONLY_GOOGLE_FLIGHTS_AIRLINE")
    )
    second_result = provider.search_flights(_request())

    assert len(second_result.offers) == 2


def test_cache_key_changes_when_a_brand_file_is_edited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skyscanner_path = tmp_path / "skyscanner.html"
    _write_html(skyscanner_path, _offer_html("sk-1", "TEST_ONLY_AIRLINE_V1"))
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedLocalFlightProvider(cache_store=cache_store)
    first_result = provider.search_flights(_request())
    assert first_result.offers[0].outbound_segments[0].carrier_name == "TEST_ONLY_AIRLINE_V1"

    _write_html(skyscanner_path, _offer_html("sk-1", "TEST_ONLY_AIRLINE_V2"))
    second_result = provider.search_flights(_request())

    assert second_result.offers[0].outbound_segments[0].carrier_name == "TEST_ONLY_AIRLINE_V2"


def test_clearing_the_shared_cache_source_still_works_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "skyscanner.html", _offer_html("sk-1", "TEST_ONLY_SKYSCANNER_AIRLINE"))
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedLocalFlightProvider(cache_store=cache_store)
    provider.search_flights(_request())

    deleted = cache_store.clear_source("scraped_flight")
    assert deleted >= 1


# ---------------------------------------------------------------------------
# No network call is attempted anywhere in multi-source mode.
# ---------------------------------------------------------------------------


def test_multi_source_flow_makes_no_network_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("ScrapedLocalFlightProvider must not open a real httpx.Client")

    monkeypatch.setattr(httpx, "Client", _fail_if_called)

    for brand in ["skyscanner", "google_flights", "kiwi_manual"]:
        _write_html(tmp_path / f"{brand}.html", _offer_html(f"{brand}-1", f"TEST_ONLY_{brand.upper()}"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 3


# ---------------------------------------------------------------------------
# Deterministic precedence: a per-brand path identical to the legacy
# path is never double-counted.
# ---------------------------------------------------------------------------


def test_identical_legacy_and_brand_path_is_not_duplicated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shared_path = tmp_path / "shared.html"
    _write_html(shared_path, _offer_html("shared-1", "TEST_ONLY_SHARED_AIRLINE"))

    settings = _no_files_settings(
        tmp_path,
        scraped_flight_html_path=str(shared_path),
        flight_manual_html_source="skyscanner",
        scraped_flight_html_path_skyscanner=str(shared_path),
    )
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 1
