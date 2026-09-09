from __future__ import annotations

import logging

from app.core.config import Settings, get_settings
from app.models.flight import FlightSearchRequest, FlightSearchResult, FlightSearchStatus
from app.models.scraping import ScrapingSourcePolicy, ScrapingSourceType
from app.providers.flights.base import FlightInventoryProvider
from app.providers.flights.scraped_parser import parse_scraped_flight_html
from app.storage.provider_cache_store import (
    ProviderCacheStore,
    get_provider_cache_store,
    make_query_hash,
)

logger = logging.getLogger(__name__)

# Config-gated local/manual scraped flight provider (Step 169B stub,
# wired to the Step 169C parser and a Step 168D-style cache in Step
# 169D), mirroring `ScrapedAccommodationProvider`
# (`backend/app/providers/accommodation/scraped_adapter.py`).
#
# This is NOT a live crawler, NOT browser automation, and NOT a real
# Amadeus/Duffel/Kiwi/Google Flights integration -- it only reads an HTML
# file the developer/operator has already saved locally (e.g. by manually
# saving a page from a browser) and hands it to the Step 169C static
# parser. It never opens a socket, never calls a website, and never
# imports httpx/requests/Playwright/Selenium.
#
# `Settings.flight_provider` defaults to `"scraped_local"` and
# `Settings.scraping_enabled`/`scraped_flight_provider_enabled` both
# default to `True` (matching Section 168's accommodation defaults) --
# so this is the default `FlightInventoryProvider` out of the box. That
# still never fabricates data: `Settings.scraped_flight_html_path`
# defaults to a fixed local path (`.data/manual_scrapes/flights.html`,
# resolved against the backend project root, never created
# automatically), and with no file actually present there, this provider
# honestly reports `unavailable` with an empty `offers` list -- never a
# placeholder/fallback offer.
#
# Cache (Step 169D, mirroring Step 168D): a successful parse is cached in
# the same `ProviderCacheStore` real adapters already use, under source
# `"scraped_flight"`, keyed by a hash of the normalized request *and* the
# local file's identity (path, mtime, size) -- never the raw HTML
# content, and never a secret. Because the file's mtime/size are part of
# the key, editing the local file is never served stale cached data: a
# changed file simply misses the old cache entry and gets reparsed. A
# cache hit returns the exact same normalized `FlightSearchResult`
# (including every offer's `scraped_provenance` and
# `data_status=scraped_public_page`) that the original parse produced --
# nothing is relabeled or re-derived on a hit. Cache reads/writes never
# fail this provider: a broken cache read falls back to re-parsing the
# file, and a broken cache write still returns the freshly-parsed result.
# Only a `success` result is ever cached -- `not_connected`/
# `unavailable`/`failed` are never cached, so a transient failure (or a
# not-yet-configured path) is always re-checked on the next call rather
# than sticking.
#
# No `ScrapingRateLimitGuard` (`app.models.scraping`) is used here --
# that guard exists for a *future live* scraper adapter (rate-limiting
# repeated network requests to the same source); this provider only ever
# reads a local file, so there is nothing to rate-limit against.

_PARSER_VERSION = "scraped_flight_provider_v1"
_CACHE_SOURCE = "scraped_flight"

# Step 182E: optional provenance "source label" display names, keyed by
# `Settings.flight_manual_html_source`, mirroring
# `app.providers.accommodation.scraped_adapter`'s
# `_MANUAL_HTML_SOURCE_DISPLAY_NAMES`. Purely cosmetic text for
# `source_id`/`source_name` (and therefore `scraped_provenance.source_id`/
# `source_name`, and the frontend's existing verbatim `source_name`
# display) -- never a claim that a real Skyscanner/Google Flights/Kiwi
# API was called, and completely unrelated to the real, live
# `flight_provider="kiwi_mcp"` adapter. `"generic"`/`"other"` have no
# entry here and are handled as "no relabeling" by
# `_effective_source_identity` below.
_MANUAL_HTML_SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "skyscanner": "Skyscanner",
    "google_flights": "Google Flights",
    "kiwi": "Kiwi",
}

_DEFAULT_SOURCE_ID = Settings.model_fields["scraped_flight_source_id"].default
_DEFAULT_SOURCE_NAME = Settings.model_fields["scraped_flight_source_name"].default


def _effective_source_identity(settings: Settings) -> tuple[str, str]:
    """Derives the `source_id`/`source_name` this provider labels its
    parsed offers with, applying `Settings.flight_manual_html_source`
    (Step 182E) only when the operator has not already customized
    `scraped_flight_source_id`/`scraped_flight_source_name` away from
    their built-in defaults -- so anyone who already set their own naming
    is never overridden. A `"generic"`/`"other"` (or unrecognized) label
    leaves both fields completely unchanged. The generated name always
    says "labeled by user"/"not official ... data" -- this is provenance
    display text only, never a claim of a real provider connection, and
    the `"kiwi"` label in particular never means live Kiwi MCP data (see
    `flight_provider="kiwi_mcp"`/`kiwi_mcp_enabled` for that, a completely
    separate config surface this adapter never reads).
    """
    display = _MANUAL_HTML_SOURCE_DISPLAY_NAMES.get(settings.flight_manual_html_source)
    if display is None:
        return settings.scraped_flight_source_id, settings.scraped_flight_source_name

    if (
        settings.scraped_flight_source_id != _DEFAULT_SOURCE_ID
        or settings.scraped_flight_source_name != _DEFAULT_SOURCE_NAME
    ):
        return settings.scraped_flight_source_id, settings.scraped_flight_source_name

    return (
        f"manual_local_scraped_flight_{settings.flight_manual_html_source}",
        f"Manual/local HTML (labeled by user as {display}-derived; not official {display} data)",
    )


class ScrapedLocalFlightProvider(FlightInventoryProvider):
    """`FlightInventoryProvider` backed by the Step 169C static HTML
    parser (`parse_scraped_flight_html`) against a manually-supplied
    local HTML file. Every real fact on a returned offer is exactly what
    the parser extracted from that file -- this adapter adds, guesses, or
    backfills nothing itself.
    """

    provider_name = "scraped_flight_provider"

    def __init__(self, cache_store: ProviderCacheStore | None = None) -> None:
        self._cache_store = cache_store

    def _resolve_cache_store(self, settings: Settings) -> ProviderCacheStore | None:
        """Lazily resolves the shared cache store, or `None` when the
        scraped-flight cache is disabled. Injecting `cache_store` in the
        constructor bypasses this lazy resolution (used by tests).
        """
        if not settings.scraped_flight_cache_enabled:
            return None
        if self._cache_store is None:
            self._cache_store = get_provider_cache_store(settings.resolved_provider_cache_path())
        return self._cache_store

    def search_flights(self, request: FlightSearchRequest) -> FlightSearchResult:
        settings = get_settings()

        if not settings.scraping_enabled or not settings.scraped_flight_provider_enabled:
            return self._empty_result(
                request,
                FlightSearchStatus.NOT_CONNECTED,
                "Scraped flight provider is not enabled.",
            )

        path = settings.resolved_scraped_flight_html_path()
        if path is None:
            return self._empty_result(
                request,
                FlightSearchStatus.UNAVAILABLE,
                "No local scraped-flight HTML path is configured.",
            )

        try:
            file_stat = path.stat()
        except OSError:
            return self._empty_result(
                request,
                FlightSearchStatus.UNAVAILABLE,
                f"Configured scraped-flight HTML path does not exist: {path}",
            )
        if not path.is_file():
            return self._empty_result(
                request,
                FlightSearchStatus.UNAVAILABLE,
                f"Configured scraped-flight HTML path does not exist: {path}",
            )

        effective_source_id, effective_source_name = _effective_source_identity(settings)

        query_hash = make_query_hash(
            {
                "source_id": effective_source_id,
                "base_url": settings.scraped_flight_base_url,
                "origin": request.origin,
                "destination": request.destination,
                "departure_date": request.departure_date.isoformat(),
                "return_date": request.return_date.isoformat() if request.return_date else None,
                "adults": request.adults,
                "children": request.children,
                "cabin_class": request.cabin_class,
                "currency": request.currency,
                "parser_version": _PARSER_VERSION,
                "html_path": str(path),
                "html_mtime_ns": file_stat.st_mtime_ns,
                "html_size": file_stat.st_size,
            }
        )
        cache_store = self._resolve_cache_store(settings)

        if cache_store is not None:
            cached_result = self._read_cache(cache_store, query_hash)
            if cached_result is not None:
                return cached_result

        try:
            html = path.read_text(encoding="utf-8")
            source_policy = ScrapingSourcePolicy(
                source_id=effective_source_id,
                source_name=effective_source_name,
                base_url=settings.scraped_flight_base_url or "file://local-scraped-flight",
                source_type=ScrapingSourceType.SCRAPED_PUBLIC_PAGE,
                enabled=True,
                approved_for_personal_use=True,
                allows_flights=True,
                requires_login=False,
                paywalled=False,
                captcha_expected=False,
                rate_limit_seconds=settings.scraping_default_rate_limit_seconds,
                notes=(
                    "Manual/local static HTML file supplied via "
                    "SCRAPED_FLIGHT_HTML_PATH -- no live fetch is ever "
                    "performed by this provider."
                ),
            )
        except Exception:
            logger.warning(
                "ScrapedLocalFlightProvider failed to read the local HTML file or "
                "build its source policy; returning a failed result.",
                exc_info=True,
            )
            return self._empty_result(
                request,
                FlightSearchStatus.FAILED,
                "Could not read or prepare the local scraped-flight HTML file.",
            )

        result = parse_scraped_flight_html(
            html=html,
            source_policy=source_policy,
            request=request,
            source_url=settings.scraped_flight_base_url,
            parser_version=_PARSER_VERSION,
        )

        if cache_store is not None and result.status == FlightSearchStatus.SUCCESS:
            self._write_cache(cache_store, query_hash, result, settings)

        return result

    def _empty_result(
        self,
        request: FlightSearchRequest,
        status: FlightSearchStatus,
        message: str,
    ) -> FlightSearchResult:
        return FlightSearchResult(
            provider=self.provider_name,
            status=status,
            offers=[],
            message=message,
            origin=request.origin,
            destination=request.destination,
            departure_date=request.departure_date,
            return_date=request.return_date,
            adults=request.adults,
            children=request.children,
            currency=request.currency,
        )

    def _read_cache(
        self, cache_store: ProviderCacheStore, query_hash: str
    ) -> FlightSearchResult | None:
        """Returns the cached, fully-reconstructed result, or `None` on a
        cache miss/expiry or a broken cache read -- either way, the caller
        falls back to re-parsing the local file rather than failing."""
        try:
            entry = cache_store.get(_CACHE_SOURCE, query_hash)
        except Exception:
            logger.warning(
                "Scraped flight provider cache read failed; falling back to "
                "re-parsing the local HTML file.",
                exc_info=True,
            )
            return None

        if entry is None:
            return None

        try:
            return FlightSearchResult.model_validate(entry.payload)
        except Exception:
            logger.warning(
                "Scraped flight provider cache entry was unusable; falling back "
                "to re-parsing the local HTML file.",
                exc_info=True,
            )
            return None

    def _write_cache(
        self,
        cache_store: ProviderCacheStore,
        query_hash: str,
        result: FlightSearchResult,
        settings: Settings,
    ) -> None:
        """Best-effort cache write -- a failure here must never affect the
        already-computed parse result being returned to the caller."""
        try:
            cache_store.set(
                _CACHE_SOURCE,
                query_hash,
                result.model_dump(mode="json"),
                ttl_seconds=settings.scraped_flight_cache_ttl_seconds,
            )
        except Exception:
            logger.warning(
                "Scraped flight provider cache write failed; returning the "
                "freshly-parsed result anyway.",
                exc_info=True,
            )
