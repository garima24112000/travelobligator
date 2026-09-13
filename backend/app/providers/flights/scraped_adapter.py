from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, get_settings
from app.models.flight import FlightOffer, FlightSearchRequest, FlightSearchResult, FlightSearchStatus
from app.models.scraping import ScrapingSourcePolicy, ScrapingSourceType
from app.providers.flights.base import FlightInventoryProvider
from app.providers.flights.source_parsers import (
    get_flight_source_parser,
    get_flight_source_parser_version,
)
from app.providers.scraping_source_registry_defaults import (
    get_scraping_source_policy,
    resolve_manual_source_policy,
)
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
# Amadeus/Duffel/Kiwi/Google Flights integration -- it only reads HTML
# file(s) the developer/operator has already saved locally (e.g. by
# manually saving a page from a browser) and hands each one to a
# `source_parsers` parser (Step 185D). It never opens a socket, never
# calls a website, and never imports httpx/requests/Playwright/Selenium.
#
# `Settings.flight_provider` defaults to `"scraped_local"` and
# `Settings.scraping_enabled`/`scraped_flight_provider_enabled` both
# default to `True` (matching Section 168's accommodation defaults) --
# so this is the default `FlightInventoryProvider` out of the box. That
# still never fabricates data: every local file path this provider
# checks (the original `scraped_flight_html_path` plus, as of Step 185D,
# three independent per-brand paths -- see below) defaults to a fixed
# local path resolved against the backend project root, never created
# automatically, and with no file actually present, this provider
# honestly reports `unavailable` with an empty `offers` list -- never a
# placeholder/fallback offer.
#
# Cache (Step 169D, mirroring Step 168D): a successful parse is cached in
# the same `ProviderCacheStore` real adapters already use, under source
# `"scraped_flight"`, keyed by a hash of the normalized request *and*
# every attempted slot's file identity (path, mtime, size) -- never the
# raw HTML content, and never a secret. Because each file's mtime/size is
# part of the key, editing any one local file is never served stale
# cached data. A cache hit returns the exact same normalized
# `FlightSearchResult` (including every offer's `scraped_provenance` and
# `data_status=scraped_public_page`) that the original parse produced --
# nothing is relabeled or re-derived on a hit. Cache reads/writes never
# fail this provider: a broken cache read falls back to re-parsing, and a
# broken cache write still returns the freshly-parsed result. Only a
# `success` result is ever cached -- `not_connected`/`unavailable`/
# `failed` are never cached, so a transient failure (or a not-yet-
# configured path) is always re-checked on the next call rather than
# sticking.
#
# No `ScrapingRateLimitGuard` (`app.models.scraping`) is used here --
# that guard exists for a *future live* scraper adapter (rate-limiting
# repeated network requests to the same source); this provider only ever
# reads local file(s), so there is nothing to rate-limit against.
#
# Step 185B: `_effective_source_identity_for_legacy_slot`/
# `_source_identity_for_brand_slot` resolve a non-"generic"/"other"
# `flight_manual_html_source` label (or a per-brand slot's fixed brand
# name) against the real, populated `ScrapingSourceRegistry`
# (`app.providers.scraping_source_registry_defaults`) -- for display
# naming only, mirroring `app.providers.accommodation.scraped_adapter`'s
# equivalent change. Every `ScrapingSourcePolicy` this module constructs
# for its own local-file-read operations always self-declares
# `enabled=True, approved_for_personal_use=True` because reading a file
# the operator already supplied is categorically safe regardless of
# which brand it's labeled after -- a completely different question from
# whether that brand's own live website is approved for scraping (it is
# not, for every real brand currently registered). This adapter never
# imports from, calls, or otherwise touches `kiwi_mcp_adapter`/
# `kiwi_mcp_client` -- `flight_provider="kiwi_mcp"` is a wholly separate
# adapter selected by `app.providers.flights.factory`, never this one.
#
# Step 185D: multi-source flight ingestion. This provider now attempts
# *every configured slot* in one call: the original single "legacy" file
# (`scraped_flight_html_path` + `flight_manual_html_source`) plus three
# independent per-brand files (`scraped_flight_html_path_skyscanner`/
# `_google_flights`/`_kiwi_manual`), each parsed by its own
# `source_parsers` module. A missing or empty slot never blocks another
# slot's success -- offers from every successful slot are merged into
# one result, and every missing/empty/failed slot is recorded in the new
# `FlightSearchResult.warnings` list rather than silently dropped or
# silently downgrading an otherwise-successful result. `"kiwi"` (the
# config label a user actually types via `FLIGHT_MANUAL_HTML_SOURCE=
# kiwi`) is mapped to the `"kiwi_manual"` parser/registry key by
# `_legacy_parser_key` below -- the config label and the canonical
# brand/module name are deliberately spelled differently so the
# distinction from `kiwi_mcp` stays visible in code, never just in
# comments. See `_resolve_slots` for the exact, deterministic precedence
# used when a per-brand path happens to resolve to the same real file as
# the legacy path (never duplicate offers from the same file under two
# labels).

_CACHE_SOURCE = "scraped_flight"

_BRAND_SOURCE_IDS: tuple[str, ...] = ("skyscanner", "google_flights", "kiwi_manual")

# Maps a legacy `flight_manual_html_source` label to the parser/registry
# key it actually selects -- only "kiwi" needs remapping (to
# "kiwi_manual"); every other label already equals its own parser key
# ("skyscanner", "google_flights") or is handled by the earlier
# generic/other early-return in `_effective_source_identity_for_legacy_
# slot` before this mapping is even consulted for parser selection.
_LEGACY_LABEL_TO_PARSER_KEY: dict[str, str] = {"kiwi": "kiwi_manual", "other": "generic"}

_DEFAULT_SOURCE_ID = Settings.model_fields["scraped_flight_source_id"].default
_DEFAULT_SOURCE_NAME = Settings.model_fields["scraped_flight_source_name"].default


def _legacy_parser_key(label: str) -> str:
    return _LEGACY_LABEL_TO_PARSER_KEY.get(label, label)


def _effective_source_identity_for_legacy_slot(settings: Settings) -> tuple[str, str]:
    """Derives the `source_id`/`source_name` for the original single-file
    "legacy" slot, applying `Settings.flight_manual_html_source` (Step
    182E, registry-backed as of Step 185B) only when the operator has not
    already customized `scraped_flight_source_id`/
    `scraped_flight_source_name` away from their built-in defaults -- so
    anyone who already set their own naming is never overridden. A
    `"generic"`/`"other"` (default/no-named-brand) label leaves both
    fields completely unchanged. The generated name always says "labeled
    by user"/"not official ... data" -- this is provenance display text
    only, never a claim of a real provider connection, and the `"kiwi"`
    label in particular never means live Kiwi MCP data (see
    `flight_provider="kiwi_mcp"`/`kiwi_mcp_enabled` for that, a completely
    separate config surface this adapter never reads).
    """
    label = settings.flight_manual_html_source
    if label in ("generic", "other"):
        return settings.scraped_flight_source_id, settings.scraped_flight_source_name

    display = resolve_manual_source_policy("flight", label).source_name

    if (
        settings.scraped_flight_source_id != _DEFAULT_SOURCE_ID
        or settings.scraped_flight_source_name != _DEFAULT_SOURCE_NAME
    ):
        return settings.scraped_flight_source_id, settings.scraped_flight_source_name

    return (
        f"manual_local_scraped_flight_{label}",
        f"Manual/local HTML (labeled by user as {display}-derived; not official {display} data)",
    )


def _source_identity_for_brand_slot(brand: str) -> tuple[str, str]:
    """Step 185D: the `source_id`/`source_name` for one of the three
    independent per-brand slots (`scraped_flight_html_path_<brand>`) --
    always registry-derived by direct lookup (each brand key here --
    `"skyscanner"`/`"google_flights"`/`"kiwi_manual"` -- already equals
    its own registry `source_id` exactly; there is no separate
    customizable per-brand source_id/name setting, unlike the legacy slot
    above).
    """
    policy = get_scraping_source_policy(brand)
    display = policy.source_name if policy is not None else brand
    return (
        f"manual_local_scraped_flight_{brand}",
        f"Manual/local HTML (labeled by user as {display}-derived; not official {display} data)",
    )


@dataclass(frozen=True)
class _ResolvedSlot:
    """One local-file source to attempt this call (Step 185D) -- the
    legacy single-file/label slot, or one of the three independent
    per-brand slots. `parser_key` selects which `source_parsers` module
    parses this slot's file (a brand name, or `"generic"`)."""

    parser_key: str
    source_id: str
    source_name: str
    path: Path | None


def _resolve_slots(settings: Settings) -> list[_ResolvedSlot]:
    """Builds the full list of local-file slots to attempt this call:
    the legacy `scraped_flight_html_path`/`flight_manual_html_source`
    pair, plus the three independent per-brand paths -- deduplicated by
    resolved absolute path so the same real file is never parsed twice
    under two different labels (which would duplicate every offer in
    it).

    Deterministic precedence: when the legacy slot's path exactly
    matches one of the three brand slots' own path, the dedicated brand
    slot wins (a more specific, deliberate configuration signal) and the
    legacy slot is dropped entirely; among the three brand slots
    themselves, the first one in `_BRAND_SOURCE_IDS` order wins any
    further path collision. With every setting left at its Step 185D
    default, no collision is possible at all -- every slot's default
    path is a distinct filename -- so this dedup only ever matters for a
    deliberately unusual configuration.
    """
    brand_paths = settings.resolved_scraped_flight_html_paths_by_source()

    legacy_path = settings.resolved_scraped_flight_html_path()
    legacy_source_id, legacy_source_name = _effective_source_identity_for_legacy_slot(settings)
    legacy_parser_key = _legacy_parser_key(settings.flight_manual_html_source)

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


class ScrapedLocalFlightProvider(FlightInventoryProvider):
    """`FlightInventoryProvider` backed by the Step 169C static HTML
    parser framework (Step 185D: dispatched per source via
    `app.providers.flights.source_parsers`) against one or more manually-
    supplied local HTML files. Every real fact on a returned offer is
    exactly what a parser extracted from that file -- this adapter adds,
    guesses, or backfills nothing itself. Never imports, calls, or
    otherwise touches the separate, real, live `flight_provider=
    "kiwi_mcp"` adapter.
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

        slots = _resolve_slots(settings)
        file_states = {slot: _slot_file_state(slot.path) for slot in slots}

        query_hash = make_query_hash(
            {
                "origin": request.origin,
                "destination": request.destination,
                "departure_date": request.departure_date.isoformat(),
                "return_date": request.return_date.isoformat() if request.return_date else None,
                "adults": request.adults,
                "children": request.children,
                "cabin_class": request.cabin_class,
                "currency": request.currency,
                "base_url": settings.scraped_flight_base_url,
                "slots": [
                    {
                        "source_id": slot.source_id,
                        "parser_key": slot.parser_key,
                        "parser_version": get_flight_source_parser_version(slot.parser_key),
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

        if cache_store is not None and result.status == FlightSearchStatus.SUCCESS:
            self._write_cache(cache_store, query_hash, result, settings)

        return result

    def _parse_all_slots(
        self,
        settings: Settings,
        request: FlightSearchRequest,
        slots: list[_ResolvedSlot],
        file_states: dict[_ResolvedSlot, _SlotFileState],
    ) -> FlightSearchResult:
        """Attempts every resolved slot independently, merging every
        successful slot's offers into one result. A missing, empty, or
        failed slot is recorded as a warning and never blocks another
        slot's success (Step 185D's core multi-source aggregation rule,
        mirroring `ScrapedAccommodationProvider._parse_all_slots`
        exactly) -- see this module's own docstring for the full status-
        mapping table this implements.
        """
        success_offers: list[FlightOffer] = []
        success_labels: list[str] = []
        warnings: list[str] = []
        failed_labels: list[str] = []
        checked_paths: list[str] = []
        # Step 185D: parallel to `checked_paths` -- one entry per slot
        # that had a real configured path (`path is not None`, whether
        # or not the file actually exists), used only so a call with
        # exactly one such slot (the overwhelmingly common pre-185D
        # single-source case) surfaces that slot's own precise reason as
        # the top-level `message`, rather than the more generic
        # multi-source "checked: a, b, c" summary -- this is what keeps
        # every pre-185D single-source message assertion accurate.
        checked_slot_messages: list[str] = []

        for slot in slots:
            state = file_states[slot]

            if slot.path is None:
                warnings.append(f"{slot.source_id}: no local file path configured.")
                continue

            checked_paths.append(str(slot.path))

            if not state.exists:
                reason = (
                    f"{slot.source_id}: configured scraped-flight HTML path does not "
                    f"exist: {slot.path}."
                )
                warnings.append(reason)
                checked_slot_messages.append(reason)
                continue

            try:
                html = slot.path.read_text(encoding="utf-8")
                source_policy = self._build_source_policy(settings, slot)
            except Exception:
                logger.warning(
                    "ScrapedLocalFlightProvider failed to read the local HTML file for "
                    "source '%s'; treating this source as failed.",
                    slot.source_id,
                    exc_info=True,
                )
                failed_labels.append(slot.source_id)
                reason = f"{slot.source_id}: could not read or prepare its local HTML file."
                warnings.append(reason)
                checked_slot_messages.append(reason)
                continue

            parser_fn = get_flight_source_parser(slot.parser_key)
            try:
                slot_result = parser_fn(
                    html=html,
                    source_policy=source_policy,
                    request=request,
                    source_url=settings.scraped_flight_base_url,
                )
            except Exception:
                # Defensive: `parse_scraped_flight_html` already catches
                # its own internal parsing errors and returns `FAILED`
                # rather than raising, but this adapter must never assume
                # every current or future source_parsers module upholds
                # that -- one slot's parser misbehaving must never crash
                # the whole multi-source request or block another slot's
                # real success.
                logger.warning(
                    "ScrapedLocalFlightProvider's parser raised unexpectedly for "
                    "source '%s'; treating this source as failed.",
                    slot.source_id,
                    exc_info=True,
                )
                failed_labels.append(slot.source_id)
                reason = f"{slot.source_id}: its parser raised an unexpected error."
                warnings.append(reason)
                checked_slot_messages.append(reason)
                continue

            if slot_result.status == FlightSearchStatus.SUCCESS:
                success_offers.extend(slot_result.offers)
                success_labels.append(slot.source_id)
            elif slot_result.status == FlightSearchStatus.FAILED:
                failed_labels.append(slot.source_id)
                reason = f"{slot.source_id}: {slot_result.message or 'failed to parse.'}"
                warnings.append(reason)
                checked_slot_messages.append(reason)
            else:
                # NOT_CONNECTED (the constructed source_policy itself
                # refused, e.g. disallows flight data -- not expected
                # here since every policy this method builds allows_
                # flights) or UNAVAILABLE (a valid file with zero flight
                # offers) -- either way, an honest, non-fatal "nothing
                # here" for this one slot only.
                reason = f"{slot.source_id}: {slot_result.message or 'no data available.'}"
                warnings.append(reason)
                checked_slot_messages.append(reason)

        if success_offers:
            message = (
                f"Parsed {len(success_offers)} offer(s) from {len(success_labels)} "
                f"source(s): {', '.join(success_labels)}."
            )
            if warnings:
                message += f" {len(warnings)} other configured source(s) had no data."
            return FlightSearchResult(
                provider=self.provider_name,
                status=FlightSearchStatus.SUCCESS,
                offers=success_offers,
                message=message,
                warnings=warnings,
                origin=request.origin,
                destination=request.destination,
                departure_date=request.departure_date,
                return_date=request.return_date,
                adults=request.adults,
                children=request.children,
                currency=request.currency,
            )

        if failed_labels:
            if len(checked_paths) == 1:
                message = checked_slot_messages[0]
            else:
                message = (
                    f"{len(failed_labels)} configured source(s) could not be parsed: "
                    f"{', '.join(failed_labels)}."
                )
            return self._empty_result(
                request, FlightSearchStatus.FAILED, message, warnings=warnings
            )

        if not checked_paths:
            message = "No local scraped-flight HTML path is configured."
        elif len(checked_paths) == 1:
            # Exactly one source was configured (the overwhelmingly
            # common pre-185D case) -- surface its own precise reason
            # directly rather than the more generic multi-source
            # summary below.
            message = checked_slot_messages[0]
        else:
            message = (
                "No local scraped-flight HTML files were found. Checked: "
                + ", ".join(checked_paths)
                + "."
            )

        return self._empty_result(
            request, FlightSearchStatus.UNAVAILABLE, message, warnings=warnings
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
                "Manual/local static HTML file supplied via a Step 185D-configured "
                f"path for source '{slot.source_id}' -- no live fetch is ever "
                "performed by this provider."
            ),
        )

    def _empty_result(
        self,
        request: FlightSearchRequest,
        status: FlightSearchStatus,
        message: str,
        warnings: list[str] | None = None,
    ) -> FlightSearchResult:
        return FlightSearchResult(
            provider=self.provider_name,
            status=status,
            offers=[],
            message=message,
            warnings=warnings or [],
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
        falls back to re-parsing rather than failing."""
        try:
            entry = cache_store.get(_CACHE_SOURCE, query_hash)
        except Exception:
            logger.warning(
                "Scraped flight provider cache read failed; falling back to "
                "re-parsing the local HTML file(s).",
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
                "to re-parsing the local HTML file(s).",
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
