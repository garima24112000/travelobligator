from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.common import DataStatus
from app.models.flight import FlightSearchRequest, FlightSearchStatus
from app.models.scraping import ScrapingSourceType
from app.providers.flights import ScrapedLocalFlightProvider
from app.providers.flights import scraped_adapter as scraped_adapter_module
from app.storage.provider_cache_store import ProviderCacheStore

# Step 169D: parser+cache wiring tests for ScrapedLocalFlightProvider.
# Every test uses a real local temp file and a real ProviderCacheStore
# backed by a temp SQLite file (never the real dev cache, never a
# network call).

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

_TEST_HTML_DIFFERENT_OFFER = """
<html><body>
<div class="flight-offer" data-offer-id="TEST_ONLY_FLIGHT_OFFER_GAMMA">
  <div class="outbound-segment">
    <span class="origin-airport">DMO</span>
    <span class="destination-airport">TST</span>
  </div>
</div>
</body></html>
"""

_TEST_HTML_MINIMAL_OFFER = """
<html><body>
<div class="flight-offer" data-offer-id="TEST_ONLY_FLIGHT_OFFER_BETA">
  <div class="outbound-segment">
    <span class="origin-airport">TST</span>
    <span class="destination-airport">DMO</span>
  </div>
</div>
</body></html>
"""


def _request(**overrides: object) -> FlightSearchRequest:
    fields: dict[str, object] = {
        "origin": "JFK",
        "destination": "LIS",
        "departure_date": date(2026, 10, 10),
    }
    fields.update(overrides)
    return FlightSearchRequest(**fields)


def _enabled_settings(html_path: str | None, **overrides: object) -> Settings:
    fields: dict[str, object] = {
        "scraping_enabled": True,
        "scraped_flight_provider_enabled": True,
        "scraped_flight_html_path": html_path,
    }
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def _write_html(tmp_path: Path, content: str, name: str = "fixture.html") -> str:
    html_file = tmp_path / name
    html_file.write_text(content, encoding="utf-8")
    return str(html_file)


def _install_counting_parse(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Wraps the real parser with a call counter so tests can prove
    whether a given `search_flights` call actually re-parsed the HTML or
    was satisfied entirely from cache."""
    call_count = {"count": 0}
    real_parse = scraped_adapter_module.parse_scraped_flight_html

    def _counting_parse(*args: object, **kwargs: object):
        call_count["count"] += 1
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(scraped_adapter_module, "parse_scraped_flight_html", _counting_parse)
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
# 1. Default config still selects scraped_local.
# ---------------------------------------------------------------------------


def test_default_settings_flight_provider_is_scraped_local() -> None:
    settings = Settings(_env_file=None)
    assert settings.flight_provider == "scraped_local"


# ---------------------------------------------------------------------------
# 2. Returns unavailable when default local flight HTML file is missing.
# ---------------------------------------------------------------------------


def test_returns_unavailable_when_default_html_file_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


# ---------------------------------------------------------------------------
# 3/4. Returns not_connected when scraping disabled at either gate.
# ---------------------------------------------------------------------------


def test_returns_not_connected_when_scraping_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    settings = _enabled_settings(html_path, scraping_enabled=False)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.NOT_CONNECTED
    assert result.offers == []


def test_returns_not_connected_when_provider_flag_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    settings = _enabled_settings(html_path, scraped_flight_provider_enabled=False)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.NOT_CONNECTED
    assert result.offers == []


# ---------------------------------------------------------------------------
# 5/6. Parses local HTML when the file exists, status=success with valid
# offers.
# ---------------------------------------------------------------------------


def test_parses_local_html_when_file_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider(cache_store=cache_store).search_flights(_request())

    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].offer_id == "TEST_ONLY_FLIGHT_OFFER_ALPHA"


# ---------------------------------------------------------------------------
# 7/8/9. Parsed offers carry scraped_public_page provenance with
# official_provider=False.
# ---------------------------------------------------------------------------


def test_parsed_offers_have_scraped_public_page_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider(cache_store=cache_store).search_flights(_request())

    offer = result.offers[0]
    assert offer.data_status == DataStatus.SCRAPED_PUBLIC_PAGE
    assert offer.scraped_provenance is not None
    assert offer.scraped_provenance.official_provider is False
    assert offer.scraped_provenance.provenance == ScrapingSourceType.SCRAPED_PUBLIC_PAGE
    assert offer.scraped_provenance.source_type == ScrapingSourceType.SCRAPED_PUBLIC_PAGE
    assert offer.scraped_provenance.parser_version == "scraped_flight_provider_v1"
    assert offer.scraped_provenance.source_id == settings.scraped_flight_source_id
    assert offer.scraped_provenance.source_name == settings.scraped_flight_source_name


# ---------------------------------------------------------------------------
# Step 182E: FLIGHT_MANUAL_HTML_SOURCE relabels source_id/source_name for
# provenance display only -- it never changes any real fact the parser
# extracted, is completely distinct from the live Kiwi MCP adapter even
# when the label is "kiwi", and never applies when the operator has
# already customized the source id/name themselves.
# ---------------------------------------------------------------------------


def test_manual_html_source_label_relabels_source_id_and_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path, flight_manual_html_source="skyscanner")
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider(cache_store=cache_store).search_flights(_request())
    offer = result.offers[0]

    assert offer.scraped_provenance is not None
    assert offer.scraped_provenance.official_provider is False
    assert offer.scraped_provenance.source_id == "manual_local_scraped_flight_skyscanner"
    assert "Skyscanner" in offer.scraped_provenance.source_name
    assert "labeled by user" in offer.scraped_provenance.source_name
    assert "not official Skyscanner data" in offer.scraped_provenance.source_name
    # Every real fact the parser extracted is unaffected by the label.
    assert offer.offer_id == "TEST_ONLY_FLIGHT_OFFER_ALPHA"
    assert offer.total_price_amount == Decimal("452.10")


def test_manual_html_source_label_kiwi_is_distinct_from_kiwi_mcp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The "kiwi" manual-html label is a provenance display string only --
    the resulting offer's `provider` field still starts with "scraped:",
    never "kiwi_mcp", so the frontend's `isKiwiMcpFlightOffer` check
    (which matches on `provider == "kiwi_mcp"` exactly) never mistakes a
    manually-labeled offer for a real, live Kiwi MCP offer."""
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path, flight_manual_html_source="kiwi")
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider(cache_store=cache_store).search_flights(_request())
    offer = result.offers[0]

    assert offer.provider.startswith("scraped:")
    assert offer.provider != "kiwi_mcp"
    assert "not official Kiwi data" in offer.scraped_provenance.source_name


def test_manual_html_source_label_generic_leaves_defaults_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path, flight_manual_html_source="generic")
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider(cache_store=cache_store).search_flights(_request())
    offer = result.offers[0]

    assert offer.scraped_provenance.source_id == "manual_local_scraped_flight"
    assert offer.scraped_provenance.source_name == "Manual local scraped flight source"


def test_manual_html_source_label_never_overrides_a_customized_source_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(
        html_path,
        flight_manual_html_source="skyscanner",
        scraped_flight_source_id="my_own_flight_source",
        scraped_flight_source_name="My Own Local Flight Export",
    )
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider(cache_store=cache_store).search_flights(_request())
    offer = result.offers[0]

    assert offer.scraped_provenance.source_id == "my_own_flight_source"
    assert offer.scraped_provenance.source_name == "My Own Local Flight Export"


# ---------------------------------------------------------------------------
# 10/11/12. Missing price/booking_url/carrier/flight-number/airport/time/
# duration/baggage/cancellation/availability remain missing after the
# provider flow.
# ---------------------------------------------------------------------------


def test_missing_fields_remain_missing_after_provider_flow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_MINIMAL_OFFER)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider(cache_store=cache_store).search_flights(_request())

    offer = result.offers[0]
    assert offer.total_price_amount is None
    assert offer.currency is None
    assert offer.booking_url is None
    assert offer.availability_status is None
    assert offer.baggage_policy is None
    assert offer.cancellation_policy is None

    segment = offer.outbound_segments[0]
    assert segment.carrier_name is None
    assert segment.carrier_code is None
    assert segment.flight_number is None
    assert segment.departure_time is None
    assert segment.arrival_time is None
    assert segment.duration_minutes is None


# ---------------------------------------------------------------------------
# 13/14. Provider writes normalized success result to cache, and a second
# identical request is served from cache without reparsing.
# ---------------------------------------------------------------------------


def test_second_identical_request_is_served_from_cache_without_reparsing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    call_count = _install_counting_parse(monkeypatch)

    provider = ScrapedLocalFlightProvider(cache_store=cache_store)

    first_result = provider.search_flights(_request())
    second_result = provider.search_flights(_request())

    assert call_count["count"] == 1
    assert first_result.status == FlightSearchStatus.SUCCESS
    assert second_result.status == FlightSearchStatus.SUCCESS
    assert (
        second_result.offers[0].offer_id
        == first_result.offers[0].offer_id
        == "TEST_ONLY_FLIGHT_OFFER_ALPHA"
    )


# ---------------------------------------------------------------------------
# 15/16/17. Cache round-trip preserves data_status, scraped_provenance,
# and official_provider=False.
# ---------------------------------------------------------------------------


def test_cache_round_trip_preserves_provenance_and_data_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedLocalFlightProvider(cache_store=cache_store)
    provider.search_flights(_request())
    cached_result = provider.search_flights(_request())

    offer = cached_result.offers[0]
    assert offer.data_status == DataStatus.SCRAPED_PUBLIC_PAGE
    assert offer.scraped_provenance is not None
    assert offer.scraped_provenance.official_provider is False
    assert offer.scraped_provenance.provenance == ScrapingSourceType.SCRAPED_PUBLIC_PAGE
    assert offer.scraped_provenance.source_type == ScrapingSourceType.SCRAPED_PUBLIC_PAGE
    assert offer.scraped_provenance.parser_version == "scraped_flight_provider_v1"
    assert offer.scraped_provenance.source_id == settings.scraped_flight_source_id
    assert offer.scraped_provenance.source_name == settings.scraped_flight_source_name


# ---------------------------------------------------------------------------
# 18-25. Cache key changes when any relevant request/parser field changes.
# ---------------------------------------------------------------------------


def test_cache_key_changes_when_origin_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    call_count = _install_counting_parse(monkeypatch)

    provider = ScrapedLocalFlightProvider(cache_store=cache_store)
    provider.search_flights(_request())
    provider.search_flights(_request(origin="LAX"))

    assert call_count["count"] == 2


def test_cache_key_changes_when_destination_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    call_count = _install_counting_parse(monkeypatch)

    provider = ScrapedLocalFlightProvider(cache_store=cache_store)
    provider.search_flights(_request())
    provider.search_flights(_request(destination="OPO"))

    assert call_count["count"] == 2


@pytest.mark.parametrize(
    "override",
    [
        {"departure_date": date(2026, 10, 11)},
        {"return_date": date(2026, 10, 20)},
        {"adults": 3},
        {"children": 2},
        {"cabin_class": "business"},
        {"currency": "EUR"},
    ],
)
def test_cache_key_changes_for_each_relevant_request_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, override: dict[str, object]
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    call_count = _install_counting_parse(monkeypatch)

    provider = ScrapedLocalFlightProvider(cache_store=cache_store)
    provider.search_flights(_request())
    provider.search_flights(_request(**override))

    assert call_count["count"] == 2


def test_cache_key_changes_when_parser_version_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    call_count = _install_counting_parse(monkeypatch)

    provider = ScrapedLocalFlightProvider(cache_store=cache_store)
    provider.search_flights(_request())

    monkeypatch.setattr(scraped_adapter_module, "_PARSER_VERSION", "scraped_flight_provider_v2")
    provider.search_flights(_request())

    assert call_count["count"] == 2


# ---------------------------------------------------------------------------
# 26. Cache invalidates when the local HTML file content/mtime/size
# changes -- a stale cache never silently overrides a changed file.
# ---------------------------------------------------------------------------


def test_cache_invalidates_when_html_file_content_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    call_count = _install_counting_parse(monkeypatch)

    provider = ScrapedLocalFlightProvider(cache_store=cache_store)
    first_result = provider.search_flights(_request())

    # Overwrite the same path with different content (different size) --
    # the old cache entry must never be served for the new file content.
    Path(html_path).write_text(_TEST_HTML_DIFFERENT_OFFER, encoding="utf-8")
    second_result = provider.search_flights(_request())

    assert call_count["count"] == 2
    assert first_result.offers[0].offer_id == "TEST_ONLY_FLIGHT_OFFER_ALPHA"
    assert second_result.offers[0].offer_id == "TEST_ONLY_FLIGHT_OFFER_GAMMA"


# ---------------------------------------------------------------------------
# 27. Cache-disabled path parses directly and never writes to cache.
# ---------------------------------------------------------------------------


def test_cache_disabled_always_reparses_and_never_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    settings = _enabled_settings(html_path, scraped_flight_cache_enabled=False)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    call_count = _install_counting_parse(monkeypatch)

    def _fail_if_called(path: object) -> None:
        raise AssertionError("get_provider_cache_store must not be called when cache is disabled")

    monkeypatch.setattr(scraped_adapter_module, "get_provider_cache_store", _fail_if_called)

    provider = ScrapedLocalFlightProvider()
    provider.search_flights(_request())
    provider.search_flights(_request())

    assert call_count["count"] == 2


# ---------------------------------------------------------------------------
# 28/29. Cache read/write failures do not crash the provider.
# ---------------------------------------------------------------------------


def test_cache_read_failure_falls_back_to_reparsing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedLocalFlightProvider(cache_store=_FailingGetCacheStore())

    result = provider.search_flights(_request())

    assert result.status == FlightSearchStatus.SUCCESS
    assert result.offers[0].offer_id == "TEST_ONLY_FLIGHT_OFFER_ALPHA"


def test_cache_write_failure_still_returns_parsed_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedLocalFlightProvider(cache_store=_FailingSetCacheStore())

    result = provider.search_flights(_request())

    assert result.status == FlightSearchStatus.SUCCESS
    assert result.offers[0].offer_id == "TEST_ONLY_FLIGHT_OFFER_ALPHA"


def test_non_success_result_is_never_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An `unavailable` result (missing file) never reaches the
    cache-write step at all -- confirmed by a spy that fails the test if
    `set` is invoked."""
    missing_path = str(tmp_path / "does_not_exist.html")
    real_cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    class _SpyCacheStore:
        def get(self, source: str, query_hash: str, now: object = None):
            return real_cache_store.get(source, query_hash)

        def set(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("set must never be called for a non-success result")

    settings = _enabled_settings(missing_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedLocalFlightProvider(cache_store=_SpyCacheStore())
    result = provider.search_flights(_request())

    assert result.status == FlightSearchStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# 30/31. Missing-path/disabled-scraping behavior is unchanged from 169B.
# ---------------------------------------------------------------------------


def test_missing_path_still_returns_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _enabled_settings(None)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


def test_disabled_scraping_still_returns_not_connected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    settings = _enabled_settings(html_path, scraping_enabled=False)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.NOT_CONNECTED
    assert result.offers == []


# ---------------------------------------------------------------------------
# 32/33. Provider never calls network; modules import no disallowed
# dependency.
# ---------------------------------------------------------------------------


def test_provider_makes_no_network_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("ScrapedLocalFlightProvider must not open a real httpx.Client")

    monkeypatch.setattr(httpx, "Client", _fail_if_called)

    html_path = _write_html(tmp_path, _TEST_HTML_ONE_OFFER)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider(cache_store=cache_store).search_flights(_request())

    assert result.status == FlightSearchStatus.SUCCESS


def test_scraped_adapter_module_has_no_disallowed_imports() -> None:
    import ast
    import inspect

    source = inspect.getsource(scraped_adapter_module)
    tree = ast.parse(source)

    disallowed_substrings = (
        "langgraph",
        "langsmith",
        "requests",
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
# Not wired into ProviderGateway or PlanningOrchestrator yet.
# ---------------------------------------------------------------------------


def test_provider_gateway_default_flight_inventory_is_scraped_local() -> None:
    """Step 169E: constructing `ProviderGateway()` with no explicit
    `flight_inventory=` resolves the same default the factory itself uses
    (`scraped_local`) -- confirming the gateway wiring didn't change the
    underlying default. This still never fabricates data: with no local
    HTML file present, the provider reports `unavailable`, never a fake
    offer. See test_provider_gateway_flights.py for further gateway
    behavior tests."""
    from app.providers.gateway import ProviderGateway

    gateway = ProviderGateway()

    assert isinstance(gateway.flight_inventory, ScrapedLocalFlightProvider)


def test_planning_orchestrator_does_not_reference_flight_scraped_adapter() -> None:
    import inspect

    import app.services.planning_orchestrator as orchestrator_module

    source = inspect.getsource(orchestrator_module)
    assert "ScrapedLocalFlightProvider" not in source
    assert "app.providers.flights" not in source
