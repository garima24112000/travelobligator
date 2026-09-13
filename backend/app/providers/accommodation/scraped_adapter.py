from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, get_settings
from app.models.accommodation import (
    AccommodationOffer,
    AccommodationSearchRequest,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.scraping import ScrapingSourcePolicy, ScrapingSourceType
from app.providers.accommodation.base import AccommodationInventoryProvider
from app.providers.accommodation.source_parsers import (
    get_accommodation_source_parser,
    get_accommodation_source_parser_version,
)
from app.providers.scraping_source_registry_defaults import resolve_manual_source_policy
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
# integration -- it only reads HTML file(s) the developer/operator has
# already saved locally (e.g. by manually saving a page from a browser)
# and hands each one to a `source_parsers` parser (Step 185C). It never
# opens a socket, never calls a website, and never imports httpx/
# requests/Playwright/Selenium.
#
# As of Step 168F, `Settings.scraping_enabled`,
# `Settings.scraped_accommodation_provider_enabled`, and
# `Settings.accommodation_provider` all default to enabling this provider
# (`True`/`True`/`"scraped_local"`) -- so this is the default
# `AccommodationInventoryProvider` out of the box. That still never
# fabricates data: every local file path this provider checks (the
# original `scraped_accommodation_html_path` plus, as of Step 185C, six
# independent per-brand paths -- see below) defaults to a fixed local
# path resolved against the backend project root, never created
# automatically, and with no file actually present, this provider
# honestly reports `unavailable` with an empty `offers` list -- never a
# placeholder/fallback offer. Explicitly setting `scraping_enabled=False`
# or `scraped_accommodation_provider_enabled=False` still reports
# `not_connected` instead.
#
# Cache (Step 168D, docs/12_provider_architecture.md "Provider Cache
# Foundation" section): a successful parse is cached in the same
# `ProviderCacheStore` real adapters already use, under source
# `"scraped_accommodation"`, keyed by a hash of the normalized request
# *and* every attempted slot's file identity (path, mtime, size) -- never
# the raw HTML content, and never a secret. Because each file's mtime/
# size is part of the key, editing any one local file is never served
# stale cached data: a changed file simply misses the old cache entry and
# gets reparsed (and only that changed slot's content actually differs in
# the freshly-parsed result). A cache hit returns the exact same
# normalized `AccommodationSearchResult` (including every offer's
# `scraped_provenance` and `data_status=scraped_public_page`) that the
# original parse produced -- nothing is relabeled or re-derived on a hit.
# Cache reads/writes never fail this provider: a broken cache read falls
# back to re-parsing, and a broken cache write still returns the
# freshly-parsed result. Only a `success` result is ever cached --
# `not_connected`/`unavailable`/`failed` are never cached, so a transient
# failure (or a not-yet-configured path) is always re-checked on the next
# call rather than sticking.
#
# Step 185B: `_effective_source_identity_for_legacy_slot`/
# `_source_identity_for_brand_slot` resolve a non-"generic"
# `accommodation_manual_html_source` label (or a per-brand slot's fixed
# brand name) against the real, populated `ScrapingSourceRegistry`
# (`app.providers.scraping_source_registry_defaults`) -- for display
# naming only. Every `ScrapingSourcePolicy` this module constructs for
# its own local-file-read operations always self-declares
# `enabled=True, approved_for_personal_use=True` because reading a file
# the operator already supplied is categorically safe regardless of
# which brand it's labeled after -- that is a completely different
# question from whether the labeled brand's own live website is approved
# for scraping (it is not, for every real brand currently registered).
#
# Step 185C: multi-source accommodation ingestion. This provider now
# attempts *every configured slot* in one call: the original single
# "legacy" file (`scraped_accommodation_html_path` +
# `accommodation_manual_html_source`) plus six independent per-brand
# files (`scraped_accommodation_html_path_booking`/`_expedia`/
# `_hotelbeds`/`_hostelworld`/`_vrbo`/`_airbnb`), each parsed by its own
# `source_parsers` module. A missing or empty slot never blocks another
# slot's success -- offers from every successful slot are merged into one
# result, and every missing/empty/failed slot is recorded in the new
# `AccommodationSearchResult.warnings` list rather than silently dropped
# or silently downgrading an otherwise-successful result. See
# `_resolve_slots` for the exact, deterministic precedence used when a
# per-brand path happens to resolve to the same real file as the legacy
# path (never duplicate offers from the same file under two labels).

_CACHE_SOURCE = "scraped_accommodation"

_BRAND_SOURCE_IDS: tuple[str, ...] = (
    "booking",
    "expedia",
    "hotelbeds",
    "hostelworld",
    "vrbo",
    "airbnb",
)

_DEFAULT_SOURCE_ID = Settings.model_fields["scraped_accommodation_source_id"].default
_DEFAULT_SOURCE_NAME = Settings.model_fields["scraped_accommodation_source_name"].default


def _effective_source_identity_for_legacy_slot(settings: Settings) -> tuple[str, str]:
    """Derives the `source_id`/`source_name` for the original single-file
    "legacy" slot, applying `Settings.accommodation_manual_html_source`
    (Step 182E, registry-backed as of Step 185B) only when the operator
    has not already customized `scraped_accommodation_source_id`/
    `scraped_accommodation_source_name` away from their built-in
    defaults -- so anyone who already set their own naming is never
    overridden. A `"generic"` (default) label leaves both fields
    completely unchanged. The generated name always says "labeled by
    user"/"not official ... data" -- this is provenance display text
    only, never a claim of a real provider connection.
    """
    label = settings.accommodation_manual_html_source
    if label == "generic":
        return settings.scraped_accommodation_source_id, settings.scraped_accommodation_source_name

    display = resolve_manual_source_policy("accommodation", label).source_name

    if (
        settings.scraped_accommodation_source_id != _DEFAULT_SOURCE_ID
        or settings.scraped_accommodation_source_name != _DEFAULT_SOURCE_NAME
    ):
        return settings.scraped_accommodation_source_id, settings.scraped_accommodation_source_name

    return (
        f"manual_local_scraped_accommodation_{label}",
        f"Manual/local HTML (labeled by user as {display}-derived; not official {display} data)",
    )


def _source_identity_for_brand_slot(brand: str) -> tuple[str, str]:
    """Step 185C: the `source_id`/`source_name` for one of the six
    independent per-brand slots (`scraped_accommodation_html_path_
    <brand>`) -- always registry-derived. Unlike the legacy slot above,
    there is no separate customizable per-brand source_id/name setting,
    so there is nothing to "already be customized away from."
    """
    display = resolve_manual_source_policy("accommodation", brand).source_name
    return (
        f"manual_local_scraped_accommodation_{brand}",
        f"Manual/local HTML (labeled by user as {display}-derived; not official {display} data)",
    )


@dataclass(frozen=True)
class _ResolvedSlot:
    """One local-file source to attempt this call (Step 185C) -- the
    legacy single-file/label slot, or one of the six independent
    per-brand slots. `parser_key` selects which `source_parsers` module
    parses this slot's file (a brand name, or `"generic"`)."""

    parser_key: str
    source_id: str
    source_name: str
    path: Path | None


def _resolve_slots(settings: Settings) -> list[_ResolvedSlot]:
    """Builds the full list of local-file slots to attempt this call:
    the legacy `scraped_accommodation_html_path`/
    `accommodation_manual_html_source` pair, plus the six independent
    per-brand paths -- deduplicated by resolved absolute path so the
    same real file is never parsed twice under two different labels
    (which would duplicate every offer in it).

    Deterministic precedence: when the legacy slot's path exactly
    matches one of the six brand slots' own path, the dedicated brand
    slot wins (a more specific, deliberate configuration signal) and the
    legacy slot is dropped entirely; among the six brand slots
    themselves, the first one in `_BRAND_SOURCE_IDS` order wins any
    further path collision. With every setting left at its Step 185C
    default, no collision is possible at all -- every slot's default
    path is a distinct filename -- so this dedup only ever matters for a
    deliberately unusual configuration.
    """
    brand_paths = settings.resolved_scraped_accommodation_html_paths_by_source()

    legacy_path = settings.resolved_scraped_accommodation_html_path()
    legacy_source_id, legacy_source_name = _effective_source_identity_for_legacy_slot(settings)
    legacy_parser_key = settings.accommodation_manual_html_source

    slots: list[_ResolvedSlot] = []
    seen_paths: set[Path] = set()

    if legacy_path is None or legacy_path not in brand_paths.values():
        slots.append(
            _ResolvedSlot(legacy_parser_key, legacy_source_id, legacy_source_name, legacy_path)
        )
        if legacy_path is not None:
            seen_paths.add(legacy_path)

    for brand in _BRAND_SOURCE_IDS:
        brand_path = brand_paths.get(brand)
        if brand_path is not None and brand_path in seen_paths:
            continue
        source_id, source_name = _source_identity_for_brand_slot(brand)
        slots.append(_ResolvedSlot(brand, source_id, source_name, brand_path))
        if brand_path is not None:
            seen_paths.add(brand_path)

    return slots


@dataclass(frozen=True)
class _SlotFileState:
    exists: bool
    mtime_ns: int | None
    size: int | None


def _slot_file_state(path: Path | None) -> _SlotFileState:
    if path is None:
        return _SlotFileState(exists=False, mtime_ns=None, size=None)
    try:
        file_stat = path.stat()
    except OSError:
        return _SlotFileState(exists=False, mtime_ns=None, size=None)
    if not path.is_file():
        return _SlotFileState(exists=False, mtime_ns=None, size=None)
    return _SlotFileState(exists=True, mtime_ns=file_stat.st_mtime_ns, size=file_stat.st_size)


class ScrapedAccommodationProvider(AccommodationInventoryProvider):
    """`AccommodationInventoryProvider` backed by the Step 168B static HTML
    parser framework (Step 185C: dispatched per source via
    `app.providers.accommodation.source_parsers`) against one or more
    manually-supplied local HTML files. Every real fact on a returned
    offer is exactly what a parser extracted from that file -- this
    adapter adds, guesses, or backfills nothing itself.
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

        slots = _resolve_slots(settings)
        file_states = {slot: _slot_file_state(slot.path) for slot in slots}

        query_hash = make_query_hash(
            {
                "destination": request.destination,
                "check_in_date": request.check_in_date.isoformat(),
                "check_out_date": request.check_out_date.isoformat(),
                "adults": request.adults,
                "children": request.children,
                "rooms": request.rooms,
                "currency": request.currency,
                "base_url": settings.scraped_accommodation_base_url,
                "slots": [
                    {
                        "source_id": slot.source_id,
                        "parser_key": slot.parser_key,
                        "parser_version": get_accommodation_source_parser_version(slot.parser_key),
                        "path": str(slot.path) if slot.path is not None else None,
                        "exists": file_states[slot].exists,
                        "mtime_ns": file_states[slot].mtime_ns,
                        "size": file_states[slot].size,
                    }
                    for slot in slots
                ],
            }
        )

        cache_store = self._resolve_cache_store(settings)
        if cache_store is not None:
            cached_result = self._read_cache(cache_store, query_hash)
            if cached_result is not None:
                return cached_result

        result = self._parse_all_slots(settings, request, slots, file_states)

        if cache_store is not None and result.status == AccommodationSearchStatus.SUCCESS:
            self._write_cache(cache_store, query_hash, result, settings)

        return result

    def _parse_all_slots(
        self,
        settings: Settings,
        request: AccommodationSearchRequest,
        slots: list[_ResolvedSlot],
        file_states: dict[_ResolvedSlot, _SlotFileState],
    ) -> AccommodationSearchResult:
        """Attempts every resolved slot independently, merging every
        successful slot's offers into one result. A missing, empty, or
        failed slot is recorded as a warning and never blocks another
        slot's success (Step 185C's core multi-source aggregation rule)
        -- see this module's own docstring for the full status-mapping
        table this implements.
        """
        success_offers: list[AccommodationOffer] = []
        success_labels: list[str] = []
        warnings: list[str] = []
        failed_labels: list[str] = []
        checked_paths: list[str] = []

        for slot in slots:
            state = file_states[slot]

            if slot.path is None:
                warnings.append(f"{slot.source_id}: no local file path configured.")
                continue

            checked_paths.append(str(slot.path))

            if not state.exists:
                warnings.append(f"{slot.source_id}: no local file found at {slot.path}.")
                continue

            try:
                html = slot.path.read_text(encoding="utf-8")
                source_policy = self._build_source_policy(settings, slot)
            except Exception:
                logger.warning(
                    "ScrapedAccommodationProvider failed to read the local HTML file for "
                    "source '%s'; treating this source as failed.",
                    slot.source_id,
                    exc_info=True,
                )
                failed_labels.append(slot.source_id)
                warnings.append(
                    f"{slot.source_id}: could not read or prepare its local HTML file."
                )
                continue

            parser_fn = get_accommodation_source_parser(slot.parser_key)
            try:
                slot_result = parser_fn(
                    html=html,
                    source_policy=source_policy,
                    request=request,
                    source_url=settings.scraped_accommodation_base_url,
                )
            except Exception:
                # Defensive: `parse_scraped_accommodation_html` already
                # catches its own internal parsing errors and returns
                # `FAILED` rather than raising, but this adapter must
                # never assume every current or future source_parsers
                # module upholds that -- one slot's parser misbehaving
                # must never crash the whole multi-source request or
                # block another slot's real success.
                logger.warning(
                    "ScrapedAccommodationProvider's parser raised unexpectedly for "
                    "source '%s'; treating this source as failed.",
                    slot.source_id,
                    exc_info=True,
                )
                failed_labels.append(slot.source_id)
                warnings.append(f"{slot.source_id}: its parser raised an unexpected error.")
                continue

            if slot_result.status == AccommodationSearchStatus.SUCCESS:
                success_offers.extend(slot_result.offers)
                success_labels.append(slot.source_id)
            elif slot_result.status == AccommodationSearchStatus.FAILED:
                failed_labels.append(slot.source_id)
                warnings.append(f"{slot.source_id}: {slot_result.message or 'failed to parse.'}")
            else:
                # NOT_CONNECTED (the constructed source_policy itself
                # refused, e.g. disallows lodging -- not expected here
                # since every policy this method builds allows_lodging)
                # or UNAVAILABLE (a valid file with zero property cards)
                # -- either way, an honest, non-fatal "nothing here" for
                # this one slot only.
                warnings.append(f"{slot.source_id}: {slot_result.message or 'no data available.'}")

        if success_offers:
            message = (
                f"Parsed {len(success_offers)} offer(s) from {len(success_labels)} "
                f"source(s): {', '.join(success_labels)}."
            )
            if warnings:
                message += f" {len(warnings)} other configured source(s) had no data."
            return AccommodationSearchResult(
                provider=self.provider_name,
                status=AccommodationSearchStatus.SUCCESS,
                offers=success_offers,
                message=message,
                warnings=warnings,
            )

        if failed_labels:
            return AccommodationSearchResult(
                provider=self.provider_name,
                status=AccommodationSearchStatus.FAILED,
                offers=[],
                message=(
                    f"{len(failed_labels)} configured source(s) could not be parsed: "
                    f"{', '.join(failed_labels)}."
                ),
                warnings=warnings,
            )

        if checked_paths:
            message = (
                "No local scraped-accommodation HTML files were found. Checked: "
                + ", ".join(checked_paths)
                + "."
            )
        else:
            message = "No local scraped-accommodation HTML path is configured."

        return AccommodationSearchResult(
            provider=self.provider_name,
            status=AccommodationSearchStatus.UNAVAILABLE,
            offers=[],
            message=message,
            warnings=warnings,
        )

    def _build_source_policy(self, settings: Settings, slot: _ResolvedSlot) -> ScrapingSourcePolicy:
        """The `ScrapingSourcePolicy` for this slot's *local-file-read
        operation* -- always self-declared safe/enabled regardless of
        which brand the slot is labeled after, because reading a file
        the operator already supplied is categorically safe. This is a
        completely different question from whether that brand's own
        live website is approved for scraping (see
        `app.providers.scraping_source_registry_defaults`, where every
        real brand is `approved_for_personal_use=False`) -- this policy
        object is never derived from that registry's safety verdict.
        """
        return ScrapingSourcePolicy(
            source_id=slot.source_id,
            source_name=slot.source_name,
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
                "Manual/local static HTML file supplied via a Step 185C-configured "
                f"path for source '{slot.source_id}' -- no live fetch is ever "
                "performed by this provider."
            ),
        )

    def _read_cache(
        self, cache_store: ProviderCacheStore, query_hash: str
    ) -> AccommodationSearchResult | None:
        """Returns the cached, fully-reconstructed result, or `None` on a
        cache miss/expiry or a broken cache read -- either way, the caller
        falls back to re-parsing rather than failing."""
        try:
            entry = cache_store.get(_CACHE_SOURCE, query_hash)
        except Exception:
            logger.warning(
                "Scraped accommodation provider cache read failed; falling back to "
                "re-parsing the local HTML file(s).",
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
                "to re-parsing the local HTML file(s).",
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
