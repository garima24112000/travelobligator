from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.accommodation import AccommodationSearchRequest, AccommodationSearchStatus
from app.providers.accommodation import ScrapedAccommodationProvider
from app.providers.accommodation import scraped_adapter as scraped_adapter_module
from app.providers.accommodation import scraped_parser as scraped_parser_module
from app.storage.provider_cache_store import ProviderCacheStore

# Step 185C: multi-source accommodation ingestion tests --
# ScrapedAccommodationProvider attempting several independent local files
# in one call. Every test uses a real local temp file -- never a real
# website, never a network call.


def _card_html(property_id: str, name: str) -> str:
    return f"""
<html><body>
<div class="property-card" data-property-id="{property_id}">
  <h2 class="property-name">{name}</h2>
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


def _write_html(path: Path, content: str) -> str:
    path.write_text(content, encoding="utf-8")
    return str(path)


def _no_files_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Every source path points at a real, distinct tmp_path location
    (so nothing accidentally reads the real dev `.data/manual_scrapes/`
    directory during this test file), but none of the files are actually
    written -- the honest, fully-empty baseline every test builds on."""
    fields: dict[str, object] = {
        "scraping_enabled": True,
        "scraped_accommodation_provider_enabled": True,
        "scraped_accommodation_html_path": str(tmp_path / "generic.html"),
        "scraped_accommodation_html_path_booking": str(tmp_path / "booking.html"),
        "scraped_accommodation_html_path_expedia": str(tmp_path / "expedia.html"),
        "scraped_accommodation_html_path_hotelbeds": str(tmp_path / "hotelbeds.html"),
        "scraped_accommodation_html_path_hostelworld": str(tmp_path / "hostelworld.html"),
        "scraped_accommodation_html_path_vrbo": str(tmp_path / "vrbo.html"),
        "scraped_accommodation_html_path_airbnb": str(tmp_path / "airbnb.html"),
    }
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


# ---------------------------------------------------------------------------
# Backward compatibility: single generic file, no per-source files present.
# ---------------------------------------------------------------------------


def test_generic_only_behavior_is_unchanged_when_no_brand_files_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "generic.html", _card_html("g-1", "TEST_ONLY_GENERIC_PROPERTY"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].property_name == "TEST_ONLY_GENERIC_PROPERTY"


def test_all_missing_files_returns_unavailable_with_clear_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.UNAVAILABLE
    assert result.offers == []
    assert result.message is not None
    assert len(result.warnings) >= 6
    # Step 190C: the message names which configured *sources* were
    # checked (a safe, low-cardinality label), never the real absolute
    # local filesystem path each one resolved to.
    assert "booking" in result.message
    assert str(tmp_path) not in result.message
    assert "booking.html" not in result.message
    for warning in result.warnings:
        assert str(tmp_path) not in warning


# ---------------------------------------------------------------------------
# Multi-source: several source files present at once produce merged,
# distinctly-labeled offers.
# ---------------------------------------------------------------------------


def test_multiple_source_files_produce_merged_distinct_offers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "booking.html", _card_html("bk-1", "TEST_ONLY_BOOKING_PROPERTY"))
    _write_html(tmp_path / "expedia.html", _card_html("ex-1", "TEST_ONLY_EXPEDIA_PROPERTY"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 2
    names = {offer.property_name for offer in result.offers}
    assert names == {"TEST_ONLY_BOOKING_PROPERTY", "TEST_ONLY_EXPEDIA_PROPERTY"}

    source_names = {offer.scraped_provenance.source_name for offer in result.offers}
    assert any("Booking.com" in name for name in source_names)
    assert any("Expedia" in name for name in source_names)
    # Every offer stays labeled manual/local and not official.
    for offer in result.offers:
        assert offer.scraped_provenance.official_provider is False
        assert "not official" in offer.scraped_provenance.source_name


def test_missing_optional_source_file_does_not_block_successful_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "booking.html", _card_html("bk-1", "TEST_ONLY_BOOKING_PROPERTY"))
    # expedia.html, hotelbeds.html, etc. are never written.
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].property_name == "TEST_ONLY_BOOKING_PROPERTY"
    # The missing sources are recorded, not silently dropped or allowed
    # to downgrade the overall result.
    assert len(result.warnings) >= 1
    assert any("expedia" in warning for warning in result.warnings)


def test_all_six_brand_sources_plus_generic_can_coexist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    brands = ["booking", "expedia", "hotelbeds", "hostelworld", "vrbo", "airbnb"]
    for brand in brands:
        _write_html(tmp_path / f"{brand}.html", _card_html(f"{brand}-1", f"TEST_ONLY_{brand.upper()}"))
    _write_html(tmp_path / "generic.html", _card_html("g-1", "TEST_ONLY_GENERIC"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 7
    names = {offer.property_name for offer in result.offers}
    assert names == {"TEST_ONLY_GENERIC"} | {f"TEST_ONLY_{b.upper()}" for b in brands}
    assert result.warnings == []


# ---------------------------------------------------------------------------
# Malformed source file: failed, not fabricated.
# ---------------------------------------------------------------------------


def test_malformed_source_file_does_not_fabricate_a_fallback_offer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    booking_path = tmp_path / "booking.html"
    _write_html(booking_path, _card_html("bk-1", "TEST_ONLY_BOOKING_PROPERTY"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    def _raise(*args: object, **kwargs: object) -> None:
        raise ValueError("simulated malformed HTML")

    monkeypatch.setattr(scraped_parser_module, "parse_scraped_accommodation_html", _raise)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.FAILED
    assert result.offers == []
    assert "booking" in (result.message or "")


def test_one_malformed_source_does_not_block_another_successful_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A per-slot parser call is individually isolated -- one slot's
    parser raising unexpectedly must never crash the whole multi-source
    request or prevent another slot's genuine success."""
    _write_html(tmp_path / "booking.html", _card_html("bk-1", "TEST_ONLY_BOOKING_PROPERTY"))
    _write_html(tmp_path / "expedia.html", _card_html("ex-1", "TEST_ONLY_EXPEDIA_PROPERTY"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    from app.providers.accommodation.source_parsers import booking as booking_parser_module

    def _raise(*args: object, **kwargs: object) -> None:
        raise ValueError("simulated malformed booking HTML")

    monkeypatch.setattr(booking_parser_module, "parse", _raise)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].property_name == "TEST_ONLY_EXPEDIA_PROPERTY"
    assert any("booking" in warning for warning in result.warnings)


# ---------------------------------------------------------------------------
# Provenance stays manual/local and not official for every slot; booking
# links are never confirmations.
# ---------------------------------------------------------------------------


def test_booking_links_are_source_supplied_never_confirmations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html = """
<html><body>
<div class="property-card" data-property-id="bk-1">
  <h2 class="property-name">TEST_ONLY_BOOKING_PROPERTY</h2>
  <a class="booking-link" href="https://example-test-only-booking.test/book/bk-1">Book</a>
</div>
</body></html>
"""
    _write_html(tmp_path / "booking.html", html)
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    offer = result.offers[0]
    assert offer.booking_url == "https://example-test-only-booking.test/book/bk-1"
    # The model itself has no "confirmed"/"booked" field at all -- there
    # is nothing here that could ever claim a reservation exists.
    assert not hasattr(offer, "booking_confirmed")
    assert not hasattr(offer, "is_booked")


# ---------------------------------------------------------------------------
# Cache: per-source path contributes to the query hash.
# ---------------------------------------------------------------------------


def test_cache_separates_booking_from_expedia_for_the_same_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "booking.html", _card_html("bk-1", "TEST_ONLY_BOOKING_PROPERTY"))
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedAccommodationProvider(cache_store=cache_store)
    first_result = provider.search_accommodations(_request())
    assert len(first_result.offers) == 1

    # Now also add an Expedia file -- the aggregate query hash must
    # change (a new slot's file identity is now part of it), so this is
    # never served from the first call's cache entry.
    _write_html(tmp_path / "expedia.html", _card_html("ex-1", "TEST_ONLY_EXPEDIA_PROPERTY"))
    second_result = provider.search_accommodations(_request())

    assert len(second_result.offers) == 2


def test_cache_key_changes_when_a_brand_file_is_edited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    booking_path = tmp_path / "booking.html"
    _write_html(booking_path, _card_html("bk-1", "TEST_ONLY_BOOKING_PROPERTY_V1"))
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedAccommodationProvider(cache_store=cache_store)
    first_result = provider.search_accommodations(_request())
    assert first_result.offers[0].property_name == "TEST_ONLY_BOOKING_PROPERTY_V1"

    _write_html(booking_path, _card_html("bk-1", "TEST_ONLY_BOOKING_PROPERTY_V2"))
    second_result = provider.search_accommodations(_request())

    assert second_result.offers[0].property_name == "TEST_ONLY_BOOKING_PROPERTY_V2"


def test_clearing_the_shared_cache_source_still_works_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "booking.html", _card_html("bk-1", "TEST_ONLY_BOOKING_PROPERTY"))
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedAccommodationProvider(cache_store=cache_store)
    provider.search_accommodations(_request())

    deleted = cache_store.clear_source("scraped_accommodation")
    assert deleted >= 1


# ---------------------------------------------------------------------------
# No network call is attempted anywhere in multi-source mode.
# ---------------------------------------------------------------------------


def test_multi_source_flow_makes_no_network_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("ScrapedAccommodationProvider must not open a real httpx.Client")

    monkeypatch.setattr(httpx, "Client", _fail_if_called)

    for brand in ["booking", "expedia", "hotelbeds"]:
        _write_html(tmp_path / f"{brand}.html", _card_html(f"{brand}-1", f"TEST_ONLY_{brand.upper()}"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 3


# ---------------------------------------------------------------------------
# Deterministic precedence: a per-brand path identical to the legacy
# path is never double-counted.
# ---------------------------------------------------------------------------


def test_identical_legacy_and_brand_path_is_not_duplicated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shared_path = tmp_path / "shared.html"
    _write_html(shared_path, _card_html("shared-1", "TEST_ONLY_SHARED_PROPERTY"))

    settings = _no_files_settings(
        tmp_path,
        scraped_accommodation_html_path=str(shared_path),
        accommodation_manual_html_source="booking",
        scraped_accommodation_html_path_booking=str(shared_path),
    )
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 1
