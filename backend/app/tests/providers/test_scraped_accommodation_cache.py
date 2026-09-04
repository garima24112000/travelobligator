from __future__ import annotations

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
from app.models.scraping import ScrapingSourceType
from app.providers.accommodation import ScrapedAccommodationProvider
from app.providers.accommodation import scraped_adapter as scraped_adapter_module
from app.storage.provider_cache_store import ProviderCacheStore

# Step 168D: cache/provenance-round-trip tests for
# ScrapedAccommodationProvider. Every test uses a real local temp file and
# a real ProviderCacheStore backed by a temp SQLite file (never the real
# dev cache, never a network call).

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

_TEST_HTML_DIFFERENT_PROPERTY = """
<html><body>
<div class="property-card" data-property-id="gamma-1">
  <h2 class="property-name">TEST_ONLY_SCRAPED_PROPERTY_GAMMA</h2>
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


def _request(**overrides: object) -> AccommodationSearchRequest:
    fields: dict[str, object] = {
        "destination": "Testville, Testland",
        "check_in_date": date(2026, 10, 10),
        "check_out_date": date(2026, 10, 14),
        "adults": 2,
        "rooms": 1,
    }
    fields.update(overrides)
    return AccommodationSearchRequest(**fields)


def _enabled_settings(html_path: str | None, **overrides: object) -> Settings:
    fields: dict[str, object] = {
        "scraping_enabled": True,
        "scraped_accommodation_provider_enabled": True,
        "scraped_accommodation_html_path": html_path,
    }
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def _write_html(tmp_path: Path, content: str, name: str = "fixture.html") -> str:
    html_file = tmp_path / name
    html_file.write_text(content, encoding="utf-8")
    return str(html_file)


def _install_counting_parse(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Wraps the real parser with a call counter so tests can prove
    whether a given `search_accommodations` call actually re-parsed the
    HTML or was satisfied entirely from cache."""
    call_count = {"count": 0}
    real_parse = scraped_adapter_module.parse_scraped_accommodation_html

    def _counting_parse(*args: object, **kwargs: object):
        call_count["count"] += 1
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(scraped_adapter_module, "parse_scraped_accommodation_html", _counting_parse)
    return call_count


class _FailingGetCacheStore:
    """Duck-typed cache store whose `get` always raises -- used to prove a
    broken cache read never crashes the provider."""

    def get(self, source: str, query_hash: str, now: object = None) -> None:
        raise RuntimeError("simulated cache read failure")

    def set(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated cache write failure")


class _FailingSetCacheStore:
    """Duck-typed cache store whose `get` behaves like an empty cache but
    `set` always raises -- used to prove a broken cache write never
    crashes the provider."""

    def get(self, source: str, query_hash: str, now: object = None) -> None:
        return None

    def set(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated cache write failure")


# ---------------------------------------------------------------------------
# 3/4. Scraped provider writes a normalized result to cache, and a second
# identical request is served from cache without reparsing.
# ---------------------------------------------------------------------------


def test_second_identical_request_is_served_from_cache_without_reparsing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    call_count = _install_counting_parse(monkeypatch)

    provider = ScrapedAccommodationProvider(cache_store=cache_store)

    first_result = provider.search_accommodations(_request())
    second_result = provider.search_accommodations(_request())

    assert call_count["count"] == 1
    assert first_result.status == AccommodationSearchStatus.SUCCESS
    assert second_result.status == AccommodationSearchStatus.SUCCESS
    assert (
        second_result.offers[0].property_name
        == first_result.offers[0].property_name
        == "TEST_ONLY_SCRAPED_PROPERTY_ALPHA"
    )


# ---------------------------------------------------------------------------
# 5/6/7/9. Cache round-trip preserves data_status, scraped_provenance
# (including parser_version/source_id/source_name/source_url), and
# official_provider=False.
# ---------------------------------------------------------------------------


def test_cache_round_trip_preserves_provenance_and_data_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedAccommodationProvider(cache_store=cache_store)
    provider.search_accommodations(_request())
    cached_result = provider.search_accommodations(_request())

    offer = cached_result.offers[0]
    assert offer.data_status == DataStatus.SCRAPED_PUBLIC_PAGE
    assert offer.scraped_provenance is not None
    assert offer.scraped_provenance.official_provider is False
    assert offer.scraped_provenance.provenance == ScrapingSourceType.SCRAPED_PUBLIC_PAGE
    assert offer.scraped_provenance.source_type == ScrapingSourceType.SCRAPED_PUBLIC_PAGE
    assert offer.scraped_provenance.parser_version == "scraped_accommodation_provider_v1"
    assert offer.scraped_provenance.source_id == settings.scraped_accommodation_source_id
    assert offer.scraped_provenance.source_name == settings.scraped_accommodation_source_name


# ---------------------------------------------------------------------------
# 17. Missing fields remain missing after cache round-trip.
# ---------------------------------------------------------------------------


def test_cache_round_trip_preserves_missing_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_MINIMAL_PROPERTY)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedAccommodationProvider(cache_store=cache_store)
    provider.search_accommodations(_request())
    cached_result = provider.search_accommodations(_request())

    offer = cached_result.offers[0]
    assert offer.nightly_price_amount is None
    assert offer.rating is None
    assert offer.availability_status == AccommodationAvailabilityStatus.UNKNOWN
    assert offer.booking_url is None
    assert offer.amenities == []
    assert offer.cancellation_policy is None


# ---------------------------------------------------------------------------
# 8. Cache key changes when destination/dates/travelers/currency change.
# ---------------------------------------------------------------------------


def test_cache_key_changes_when_request_fields_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    call_count = _install_counting_parse(monkeypatch)

    provider = ScrapedAccommodationProvider(cache_store=cache_store)
    provider.search_accommodations(_request())
    provider.search_accommodations(_request(destination="A Different Town, Testland"))

    assert call_count["count"] == 2


@pytest.mark.parametrize(
    "override",
    [
        {"check_in_date": date(2026, 10, 11)},
        {"check_out_date": date(2026, 10, 15)},
        {"adults": 4},
        {"currency": "EUR"},
    ],
)
def test_cache_key_changes_for_each_relevant_request_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, override: dict[str, object]
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    call_count = _install_counting_parse(monkeypatch)

    provider = ScrapedAccommodationProvider(cache_store=cache_store)
    provider.search_accommodations(_request())
    provider.search_accommodations(_request(**override))

    assert call_count["count"] == 2


# ---------------------------------------------------------------------------
# 9. Cache key changes when parser_version changes.
# ---------------------------------------------------------------------------


def test_cache_key_changes_when_parser_version_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    call_count = _install_counting_parse(monkeypatch)

    provider = ScrapedAccommodationProvider(cache_store=cache_store)
    provider.search_accommodations(_request())

    monkeypatch.setattr(scraped_adapter_module, "_PARSER_VERSION", "scraped_accommodation_provider_v2")
    provider.search_accommodations(_request())

    assert call_count["count"] == 2


# ---------------------------------------------------------------------------
# 10. Cache invalidates when the local HTML file content/mtime/size
# changes -- a stale cache never silently overrides a changed file.
# ---------------------------------------------------------------------------


def test_cache_invalidates_when_html_file_content_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    call_count = _install_counting_parse(monkeypatch)

    provider = ScrapedAccommodationProvider(cache_store=cache_store)
    first_result = provider.search_accommodations(_request())

    # Overwrite the same path with different content (different size) --
    # the old cache entry must never be served for the new file content.
    Path(html_path).write_text(_TEST_HTML_DIFFERENT_PROPERTY, encoding="utf-8")
    second_result = provider.search_accommodations(_request())

    assert call_count["count"] == 2
    assert first_result.offers[0].property_name == "TEST_ONLY_SCRAPED_PROPERTY_ALPHA"
    assert second_result.offers[0].property_name == "TEST_ONLY_SCRAPED_PROPERTY_GAMMA"


# ---------------------------------------------------------------------------
# 11. Cache-disabled path parses directly and never writes to cache.
# ---------------------------------------------------------------------------


def test_cache_disabled_always_reparses_and_never_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = _enabled_settings(html_path, scraped_accommodation_cache_enabled=False)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    call_count = _install_counting_parse(monkeypatch)

    # No cache_store injected and cache disabled -- get_provider_cache_store
    # must never even be consulted.
    def _fail_if_called(path: object) -> None:
        raise AssertionError("get_provider_cache_store must not be called when cache is disabled")

    monkeypatch.setattr(scraped_adapter_module, "get_provider_cache_store", _fail_if_called)

    provider = ScrapedAccommodationProvider()
    provider.search_accommodations(_request())
    provider.search_accommodations(_request())

    assert call_count["count"] == 2


# ---------------------------------------------------------------------------
# 12. Cache read failure does not crash the provider.
# ---------------------------------------------------------------------------


def test_cache_read_failure_falls_back_to_reparsing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedAccommodationProvider(cache_store=_FailingGetCacheStore())

    result = provider.search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert result.offers[0].property_name == "TEST_ONLY_SCRAPED_PROPERTY_ALPHA"


# ---------------------------------------------------------------------------
# 13. Cache write failure does not crash the provider.
# ---------------------------------------------------------------------------


def test_cache_write_failure_still_returns_parsed_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_PROPERTY)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedAccommodationProvider(cache_store=_FailingSetCacheStore())

    result = provider.search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert result.offers[0].property_name == "TEST_ONLY_SCRAPED_PROPERTY_ALPHA"


# ---------------------------------------------------------------------------
# Never fails/failed-or-unavailable results are never cached.
# ---------------------------------------------------------------------------


def test_unavailable_result_is_never_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An `unavailable` result (missing file) never reaches the
    cache-write step at all -- the function returns before `_write_cache`
    is ever called, confirmed by a spy that fails the test if `set` is
    invoked."""
    missing_path = str(tmp_path / "does_not_exist.html")
    real_cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    class _SpyCacheStore:
        def get(self, source: str, query_hash: str, now: object = None):
            return real_cache_store.get(source, query_hash)

        def set(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("set must never be called for a non-success result")

    settings = _enabled_settings(missing_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedAccommodationProvider(cache_store=_SpyCacheStore())
    result = provider.search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.UNAVAILABLE
