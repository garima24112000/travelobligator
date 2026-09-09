from __future__ import annotations

import logging

from app.core.config import Settings, get_settings
from app.models.accommodation import (
    AccommodationSearchRequest,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.scraping import ScrapingSourcePolicy, ScrapingSourceType
from app.providers.accommodation.base import AccommodationInventoryProvider
from app.providers.accommodation.scraped_parser import parse_scraped_accommodation_html
from app.storage.provider_cache_store import (
    ProviderCacheStore,
    get_provider_cache_store,
    make_query_hash,
)

logger = logging.getLogger(__name__)

# Config-gated local/manual scraped accommodation provider (Step 168C,
# hardened with a cache in Step 168D, made the default provider in Step
# 168F). This is NOT a live crawler, NOT browser automation, and NOT a
# real Booking/Expedia/Hotelbeds/Hostelworld/Amadeus/Vrbo/Airbnb
# integration -- it only reads an HTML file the developer/operator has
# already saved locally (e.g. by manually saving a page from a browser)
# and hands it to the Step 168B static parser. It never opens a socket,
# never calls a website, and never imports httpx/requests/Playwright/
# Selenium.
#
# As of Step 168F, `Settings.scraping_enabled`,
# `Settings.scraped_accommodation_provider_enabled`, and
# `Settings.accommodation_provider` all default to enabling this provider
# (`True`/`True`/`"scraped_local"`) -- so this is the default
# `AccommodationInventoryProvider` out of the box. That still never
# fabricates data: `Settings.scraped_accommodation_html_path` defaults to
# a fixed local path (`.data/manual_scrapes/accommodations.html`,
# resolved against the backend project root, never created
# automatically), and with no file actually present there, this provider
# honestly reports `unavailable` with an empty `offers` list -- never a
# placeholder/fallback offer. Explicitly setting `scraping_enabled=False`
# or `scraped_accommodation_provider_enabled=False` still reports
# `not_connected` instead.
#
# Cache (Step 168D, docs/12_provider_architecture.md "Provider Cache
# Foundation" section): a successful parse is cached in the same
# `ProviderCacheStore` real adapters already use, under source
# `"scraped_accommodation"`, keyed by a hash of the normalized request
# *and* the local file's identity (path, mtime, size) -- never the raw
# HTML content, and never a secret. Because the file's mtime/size are
# part of the key, editing the local file is never served stale cached
# data: a changed file simply misses the old cache entry and gets
# reparsed. A cache hit returns the exact same normalized
# `AccommodationSearchResult` (including every offer's `scraped_
# provenance` and `data_status=scraped_public_page`) that the original
# parse produced -- nothing is relabeled or re-derived on a hit. Cache
# reads/writes never fail this provider: a broken cache read falls back
# to re-parsing the file, and a broken cache write still returns the
# freshly-parsed result. Only a `success` result is ever cached --
# `not_connected`/`unavailable`/`failed` are never cached, so a
# transient failure (or a not-yet-configured path) is always re-checked
# on the next call rather than sticking.

_PARSER_VERSION = "scraped_accommodation_provider_v1"
_CACHE_SOURCE = "scraped_accommodation"

# Step 182E: optional provenance "source label" display names, keyed by
# `Settings.accommodation_manual_html_source`. Purely cosmetic text for
# `source_id`/`source_name` (and therefore `scraped_provenance.source_id`/
# `source_name`, and the frontend's existing verbatim `source_name`
# display) -- never a claim that a real Booking/Expedia/Hotelbeds/
# Hostelworld/Vrbo/Airbnb API was called. `"generic"` has no entry here
# and is handled as "no relabeling" by `_effective_source_identity` below.
_MANUAL_HTML_SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "booking": "Booking.com",
    "expedia": "Expedia",
    "hotelbeds": "Hotelbeds",
    "hostelworld": "Hostelworld",
    "vrbo": "Vrbo",
    "airbnb": "Airbnb",
}

_DEFAULT_SOURCE_ID = Settings.model_fields["scraped_accommodation_source_id"].default
_DEFAULT_SOURCE_NAME = Settings.model_fields["scraped_accommodation_source_name"].default


def _effective_source_identity(settings: Settings) -> tuple[str, str]:
    """Derives the `source_id`/`source_name` this provider labels its
    parsed offers with, applying `Settings.accommodation_manual_html_source`
    (Step 182E) only when the operator has not already customized
    `scraped_accommodation_source_id`/`scraped_accommodation_source_name`
    away from their built-in defaults -- so anyone who already set their
    own naming is never overridden. A `"generic"` (default) or
    unrecognized label leaves both fields completely unchanged. The
    generated name always says "labeled by user"/"not official ... data"
    -- this is provenance display text only, never a claim of a real
    provider connection.
    """
    display = _MANUAL_HTML_SOURCE_DISPLAY_NAMES.get(settings.accommodation_manual_html_source)
    if display is None:
        return settings.scraped_accommodation_source_id, settings.scraped_accommodation_source_name

    if (
        settings.scraped_accommodation_source_id != _DEFAULT_SOURCE_ID
        or settings.scraped_accommodation_source_name != _DEFAULT_SOURCE_NAME
    ):
        return settings.scraped_accommodation_source_id, settings.scraped_accommodation_source_name

    return (
        f"manual_local_scraped_accommodation_{settings.accommodation_manual_html_source}",
        f"Manual/local HTML (labeled by user as {display}-derived; not official {display} data)",
    )


class ScrapedAccommodationProvider(AccommodationInventoryProvider):
    """`AccommodationInventoryProvider` backed by the Step 168B static HTML
    parser (`parse_scraped_accommodation_html`) against a manually-supplied
    local HTML file. Every real fact on a returned offer is exactly what
    the parser extracted from that file -- this adapter adds, guesses, or
    backfills nothing itself.
    """

    provider_name = "scraped_accommodation_provider"

    def __init__(self, cache_store: ProviderCacheStore | None = None) -> None:
        self._cache_store = cache_store

    def _resolve_cache_store(self, settings: Settings) -> ProviderCacheStore | None:
        """Lazily resolves the shared cache store, or `None` when the
        scraped-accommodation cache is disabled. Injecting `cache_store`
        in the constructor bypasses this lazy resolution (used by tests).
        """
        if not settings.scraped_accommodation_cache_enabled:
            return None
        if self._cache_store is None:
            self._cache_store = get_provider_cache_store(settings.resolved_provider_cache_path())
        return self._cache_store

    def search_accommodations(
        self, request: AccommodationSearchRequest
    ) -> AccommodationSearchResult:
        settings = get_settings()

        if not settings.scraping_enabled or not settings.scraped_accommodation_provider_enabled:
            return AccommodationSearchResult(
                provider=self.provider_name,
                status=AccommodationSearchStatus.NOT_CONNECTED,
                offers=[],
                message="Scraped accommodation provider is not enabled.",
            )

        path = settings.resolved_scraped_accommodation_html_path()
        if path is None:
            return AccommodationSearchResult(
                provider=self.provider_name,
                status=AccommodationSearchStatus.UNAVAILABLE,
                offers=[],
                message="No local scraped-accommodation HTML path is configured.",
            )

        try:
            file_stat = path.stat()
        except OSError:
            return AccommodationSearchResult(
                provider=self.provider_name,
                status=AccommodationSearchStatus.UNAVAILABLE,
                offers=[],
                message=f"Configured scraped-accommodation HTML path does not exist: {path}",
            )
        if not path.is_file():
            return AccommodationSearchResult(
                provider=self.provider_name,
                status=AccommodationSearchStatus.UNAVAILABLE,
                offers=[],
                message=f"Configured scraped-accommodation HTML path does not exist: {path}",
            )

        effective_source_id, effective_source_name = _effective_source_identity(settings)

        query_hash = make_query_hash(
            {
                "source_id": effective_source_id,
                "base_url": settings.scraped_accommodation_base_url,
                "destination": request.destination,
                "check_in_date": request.check_in_date.isoformat(),
                "check_out_date": request.check_out_date.isoformat(),
                "adults": request.adults,
                "children": request.children,
                "rooms": request.rooms,
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
                base_url=settings.scraped_accommodation_base_url
                or "file://local-scraped-accommodation",
                source_type=ScrapingSourceType.SCRAPED_PUBLIC_PAGE,
                enabled=True,
                approved_for_personal_use=True,
                allows_lodging=True,
                allows_restaurants=False,
                allows_attractions=False,
                requires_login=False,
                paywalled=False,
                captcha_expected=False,
                rate_limit_seconds=settings.scraping_default_rate_limit_seconds,
                notes=(
                    "Manual/local static HTML file supplied via "
                    "SCRAPED_ACCOMMODATION_HTML_PATH -- no live fetch is ever "
                    "performed by this provider."
                ),
            )
        except Exception:
            logger.warning(
                "ScrapedAccommodationProvider failed to read the local HTML file or "
                "build its source policy; returning a failed result.",
                exc_info=True,
            )
            return AccommodationSearchResult(
                provider=self.provider_name,
                status=AccommodationSearchStatus.FAILED,
                offers=[],
                message="Could not read or prepare the local scraped-accommodation HTML file.",
            )

        result = parse_scraped_accommodation_html(
            html=html,
            source_policy=source_policy,
            request=request,
            source_url=settings.scraped_accommodation_base_url,
            parser_version=_PARSER_VERSION,
        )

        if cache_store is not None and result.status == AccommodationSearchStatus.SUCCESS:
            self._write_cache(cache_store, query_hash, result, settings)

        return result

    def _read_cache(
        self, cache_store: ProviderCacheStore, query_hash: str
    ) -> AccommodationSearchResult | None:
        """Returns the cached, fully-reconstructed result, or `None` on a
        cache miss/expiry or a broken cache read -- either way, the caller
        falls back to re-parsing the local file rather than failing."""
        try:
            entry = cache_store.get(_CACHE_SOURCE, query_hash)
        except Exception:
            logger.warning(
                "Scraped accommodation provider cache read failed; falling back to "
                "re-parsing the local HTML file.",
                exc_info=True,
            )
            return None

        if entry is None:
            return None

        try:
            return AccommodationSearchResult.model_validate(entry.payload)
        except Exception:
            logger.warning(
                "Scraped accommodation provider cache entry was unusable; falling back "
                "to re-parsing the local HTML file.",
                exc_info=True,
            )
            return None

    def _write_cache(
        self,
        cache_store: ProviderCacheStore,
        query_hash: str,
        result: AccommodationSearchResult,
        settings: Settings,
    ) -> None:
        """Best-effort cache write -- a failure here must never affect the
        already-computed parse result being returned to the caller."""
        try:
            cache_store.set(
                _CACHE_SOURCE,
                query_hash,
                result.model_dump(mode="json"),
                ttl_seconds=settings.scraped_accommodation_cache_ttl_seconds,
            )
        except Exception:
            logger.warning(
                "Scraped accommodation provider cache write failed; returning the "
                "freshly-parsed result anyway.",
                exc_info=True,
            )
