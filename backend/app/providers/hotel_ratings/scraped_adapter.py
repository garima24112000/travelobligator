from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, get_settings
from app.models.hotel_ratings import (
    HotelRatingLookupItem,
    HotelRatingsRequest,
    HotelRatingsResult,
    HotelRatingsStatus,
)
from app.models.scraping import ScrapingSourcePolicy, ScrapingSourceType
from app.providers.hotel_ratings.base import HotelRatingsProvider
from app.providers.hotel_ratings.scraped_parser import (
    HotelRatingsParseStatus,
    ParsedHotelRatingRecord,
)
from app.providers.hotel_ratings.source_parsers import (
    get_hotel_ratings_source_parser,
    get_hotel_ratings_source_parser_version,
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

# Config-gated local/manual hotel-ratings provider (Step 185E), mirroring
# `ScrapedAccommodationProvider`/`ScrapedLocalFlightProvider` (Steps
# 168C-185D) as closely as this provider's different request/response
# shape allows.
#
# This is NOT a live crawler, NOT browser automation, and NOT a real
# Tripadvisor/Google Places integration -- it only reads HTML file(s) the
# developer/operator has already saved locally (e.g. by manually saving a
# page from a browser) and hands each one to a `source_parsers` parser.
# It never opens a socket, never calls a website, and never imports
# httpx/requests/Playwright/Selenium.
#
# Unlike the accommodation/flight adapters (which build offers directly
# from a single search request), `HotelRatingsProvider.get_ratings` takes
# a *list* of already-known-property identity requests and must return
# exactly one `HotelRatingLookupItem` per request, echoing back its
# `offer_id`. So this adapter's job is two independent steps:
#
#   1. Parse every configured local file into a flat list of
#      `ParsedHotelRatingRecord` (property_name + rating), exactly like
#      the accommodation/flight adapters parse their own local files into
#      offers -- see `_parse_all_slots`.
#   2. Conservatively, exactly match each incoming request's
#      `property_name` against that list -- never fuzzy, mirroring
#      `HotelRatingEnrichmentService`'s own no-guessing philosophy. A
#      request with no `property_name`, no matching record, or an
#      *ambiguous* match (two or more distinct records share the same
#      normalized name, even across two different source files) is
#      returned `matched=False, rating=None` -- never a guessed rating,
#      and never attached to the wrong property. See
#      `_match_records_to_requests`.
#
# `Settings.hotel_ratings_provider` defaults to `"not_connected"` --
# selecting `"scraped_local"`/`"manual_html"` alone still never fabricates
# data: every local file path this provider checks (the original
# `scraped_hotel_ratings_html_path` plus two independent per-brand paths)
# defaults to a fixed local path resolved against the backend project
# root, never created automatically, and with no file actually present,
# this provider honestly reports `unavailable` with an empty `items`
# list -- never a placeholder/fallback rating.
#
# Cache (mirroring Step 168D/169D): a successful parse+match is cached in
# the same `ProviderCacheStore` real adapters already use, under source
# `"scraped_hotel_ratings"`, keyed by a hash of the normalized incoming
# `requests` list *and* every attempted slot's file identity (path,
# mtime, size). Because both the request identity and each file's
# mtime/size are part of the key, editing any one local file -- or asking
# about a different set of accommodation offers -- is never served stale
# cached data. Only a `success` result is ever cached -- `not_connected`/
# `unavailable`/`failed` are never cached.
#
# `_effective_source_identity_for_legacy_slot`/`_source_identity_for_
# brand_slot` resolve a non-"generic"/"other" `hotel_ratings_manual_html_
# source` label (or a per-brand slot's fixed brand name) against the
# real, populated `ScrapingSourceRegistry`
# (`app.providers.scraping_source_registry_defaults`) -- for display
# naming only, mirroring the accommodation/flight adapters' identical
# pattern. Every `ScrapingSourcePolicy` this module constructs for its
# own local-file-read operations always self-declares `enabled=True,
# approved_for_personal_use=True` because reading a file the operator
# already supplied is categorically safe regardless of which brand it's
# labeled after -- a completely different question from whether that
# brand's own live website is approved for scraping (it is not, for
# every real brand currently registered).

_CACHE_SOURCE = "scraped_hotel_ratings"

_BRAND_SOURCE_IDS: tuple[str, ...] = ("tripadvisor", "google_places_ratings")

# Maps a legacy `hotel_ratings_manual_html_source` label to the parser/
# registry key it actually selects -- only "google_places" needs
# remapping (to "google_places_ratings"); "tripadvisor" already equals
# its own parser key, and "generic"/"other" are handled by the earlier
# early-return in `_effective_source_identity_for_legacy_slot` before
# this mapping is even consulted for parser selection.
_LEGACY_LABEL_TO_PARSER_KEY: dict[str, str] = {
    "google_places": "google_places_ratings",
    "other": "generic",
}

_DEFAULT_SOURCE_ID = Settings.model_fields["scraped_hotel_ratings_source_id"].default
_DEFAULT_SOURCE_NAME = Settings.model_fields["scraped_hotel_ratings_source_name"].default


def _legacy_parser_key(label: str) -> str:
    return _LEGACY_LABEL_TO_PARSER_KEY.get(label, label)


def _effective_source_identity_for_legacy_slot(settings: Settings) -> tuple[str, str]:
    """Derives the `source_id`/`source_name` for the original single-file
    "legacy" slot, applying `Settings.hotel_ratings_manual_html_source`
    only when the operator has not already customized
    `scraped_hotel_ratings_source_id`/`scraped_hotel_ratings_source_name`
    away from their built-in defaults -- so anyone who already set their
    own naming is never overridden. A `"generic"`/`"other"` (default/no-
    named-brand) label leaves both fields completely unchanged. The
    generated name always says "labeled by user"/"not official ... data"
    -- this is provenance display text only, never a claim of a real
    provider connection.
    """
    label = settings.hotel_ratings_manual_html_source
    if label in ("generic", "other"):
        return settings.scraped_hotel_ratings_source_id, settings.scraped_hotel_ratings_source_name

    display = resolve_manual_source_policy("hotel_ratings", label).source_name

    if (
        settings.scraped_hotel_ratings_source_id != _DEFAULT_SOURCE_ID
        or settings.scraped_hotel_ratings_source_name != _DEFAULT_SOURCE_NAME
    ):
        return settings.scraped_hotel_ratings_source_id, settings.scraped_hotel_ratings_source_name

    return (
        f"manual_local_scraped_hotel_ratings_{label}",
        f"Manual/local HTML (labeled by user as {display}-derived; not official {display} data)",
    )


def _source_identity_for_brand_slot(brand: str) -> tuple[str, str]:
    """The `source_id`/`source_name` for one of the two independent
    per-brand slots (`scraped_hotel_ratings_html_path_<brand>`) --
    always registry-derived by direct lookup (each brand key here --
    `"tripadvisor"`/`"google_places_ratings"` -- already equals its own
    registry `source_id` exactly; there is no separate customizable
    per-brand source_id/name setting, unlike the legacy slot above).
    """
    policy = get_scraping_source_policy(brand)
    display = policy.source_name if policy is not None else brand
    return (
        f"manual_local_scraped_hotel_ratings_{brand}",
        f"Manual/local HTML (labeled by user as {display}-derived; not official {display} data)",
    )


@dataclass(frozen=True)
class _ResolvedSlot:
    """One local-file source to attempt this call -- the legacy single-
    file/label slot, or one of the two independent per-brand slots.
    `parser_key` selects which `source_parsers` module parses this
    slot's file (a brand name, or `"generic"`)."""

    parser_key: str
    source_id: str
    source_name: str
    path: Path | None


def _resolve_slots(settings: Settings) -> list[_ResolvedSlot]:
    """Builds the full list of local-file slots to attempt this call:
    the legacy `scraped_hotel_ratings_html_path`/`hotel_ratings_manual_
    html_source` pair, plus the two independent per-brand paths --
    deduplicated by resolved absolute path so the same real file is
    never parsed twice under two different labels (which would duplicate
    every rating record in it).

    Deterministic precedence: when the legacy slot's path exactly
    matches one of the two brand slots' own path, the dedicated brand
    slot wins (a more specific, deliberate configuration signal) and the
    legacy slot is dropped entirely; among the two brand slots
    themselves, the first one in `_BRAND_SOURCE_IDS` order wins any
    further path collision. With every setting left at its default, no
    collision is possible at all -- every slot's default path is a
    distinct filename.
    """
    brand_paths = settings.resolved_scraped_hotel_ratings_html_paths_by_source()

    legacy_path = settings.resolved_scraped_hotel_ratings_html_path()
    legacy_source_id, legacy_source_name = _effective_source_identity_for_legacy_slot(settings)
    legacy_parser_key = _legacy_parser_key(settings.hotel_ratings_manual_html_source)

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


def _normalize_property_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _match_records_to_requests(
    requests: list[HotelRatingsRequest],
    records: list[ParsedHotelRatingRecord],
) -> list[HotelRatingLookupItem]:
    """Conservative, exact-name matching only -- never fuzzy, mirroring
    `HotelRatingEnrichmentService`'s own no-guessing philosophy. A
    request with no `property_name`, zero matching records, or an
    *ambiguous* match (two or more distinct records share the same
    normalized name, even across two different source files) is returned
    `matched=False, rating=None` -- never a guessed rating, and never
    attached to the wrong property.
    """
    records_by_name: dict[str, list[ParsedHotelRatingRecord]] = {}
    for record in records:
        key = _normalize_property_name(record.property_name)
        records_by_name.setdefault(key, []).append(record)

    items: list[HotelRatingLookupItem] = []
    for request in requests:
        if not request.property_name:
            items.append(
                HotelRatingLookupItem(
                    offer_id=request.offer_id,
                    provider_property_id=request.provider_property_id,
                    matched=False,
                    rating=None,
                )
            )
            continue

        candidates = records_by_name.get(_normalize_property_name(request.property_name), [])
        if len(candidates) != 1:
            # Zero matches, or an ambiguous 2+ -- never guessed between.
            items.append(
                HotelRatingLookupItem(
                    offer_id=request.offer_id,
                    provider_property_id=request.provider_property_id,
                    matched=False,
                    rating=None,
                )
            )
            continue

        items.append(
            HotelRatingLookupItem(
                offer_id=request.offer_id,
                provider_property_id=request.provider_property_id,
                matched=True,
                rating=candidates[0].rating,
            )
        )

    return items


class ScrapedLocalHotelRatingsProvider(HotelRatingsProvider):
    """`HotelRatingsProvider` backed by the Step 185E static HTML parser
    framework (dispatched per source via
    `app.providers.hotel_ratings.source_parsers`) against one or more
    manually-supplied local HTML files. Every real fact on a returned
    rating is exactly what a parser extracted from that file -- this
    adapter adds, guesses, or backfills nothing itself, and matches a
    parsed record to a request only via exact, conservative name matching
    (see `_match_records_to_requests`).
    """

    provider_name = "scraped_hotel_ratings_provider"

    def __init__(self, cache_store: ProviderCacheStore | None = None) -> None:
        self._cache_store = cache_store

    def _resolve_cache_store(self, settings: Settings) -> ProviderCacheStore | None:
        if not settings.scraped_hotel_ratings_cache_enabled:
            return None
        if self._cache_store is None:
            self._cache_store = get_provider_cache_store(settings.resolved_provider_cache_path())
        return self._cache_store

    def get_ratings(self, requests: list[HotelRatingsRequest]) -> HotelRatingsResult:
        settings = get_settings()

        if not settings.scraping_enabled or not settings.scraped_hotel_ratings_provider_enabled:
            return self._empty_result(
                HotelRatingsStatus.NOT_CONNECTED,
                "Scraped hotel ratings provider is not enabled.",
            )

        slots = _resolve_slots(settings)
        file_states = {slot: _slot_file_state(slot.path) for slot in slots}

        query_hash = make_query_hash(
            {
                "requests": [
                    {
                        "offer_id": request.offer_id,
                        "provider_property_id": request.provider_property_id,
                        "provider": request.provider,
                        "property_name": request.property_name,
                        "address": request.address,
                        "latitude": request.latitude,
                        "longitude": request.longitude,
                        "source_name": request.source_name,
                    }
                    for request in requests
                ],
                "base_url": settings.scraped_hotel_ratings_base_url,
                "slots": [
                    {
                        "source_id": slot.source_id,
                        "parser_key": slot.parser_key,
                        "parser_version": get_hotel_ratings_source_parser_version(
                            slot.parser_key
                        ),
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

        result = self._parse_all_slots(settings, requests, slots, file_states)

        if cache_store is not None and result.status == HotelRatingsStatus.SUCCESS:
            self._write_cache(cache_store, query_hash, result, settings)

        return result

    def _parse_all_slots(
        self,
        settings: Settings,
        requests: list[HotelRatingsRequest],
        slots: list[_ResolvedSlot],
        file_states: dict[_ResolvedSlot, _SlotFileState],
    ) -> HotelRatingsResult:
        """Attempts every resolved slot independently, merging every
        successful slot's parsed records into one flat list before
        matching against `requests`. A missing, empty, or failed slot is
        recorded as a warning and never blocks another slot's success
        (mirroring `ScrapedLocalFlightProvider._parse_all_slots` exactly).
        """
        all_records: list[ParsedHotelRatingRecord] = []
        success_labels: list[str] = []
        warnings: list[str] = []
        failed_labels: list[str] = []
        checked_paths: list[str] = []
        checked_slot_messages: list[str] = []

        for slot in slots:
            state = file_states[slot]

            if slot.path is None:
                warnings.append(f"{slot.source_id}: no local file path configured.")
                continue

            checked_paths.append(str(slot.path))

            if not state.exists:
                reason = (
                    f"{slot.source_id}: configured scraped-hotel-ratings HTML path "
                    f"does not exist: {slot.path}."
                )
                warnings.append(reason)
                checked_slot_messages.append(reason)
                continue

            try:
                html = slot.path.read_text(encoding="utf-8")
                source_policy = self._build_source_policy(settings, slot)
            except Exception:
                logger.warning(
                    "ScrapedLocalHotelRatingsProvider failed to read the local HTML "
                    "file for source '%s'; treating this source as failed.",
                    slot.source_id,
                    exc_info=True,
                )
                failed_labels.append(slot.source_id)
                reason = f"{slot.source_id}: could not read or prepare its local HTML file."
                warnings.append(reason)
                checked_slot_messages.append(reason)
                continue

            parser_fn = get_hotel_ratings_source_parser(slot.parser_key)
            try:
                slot_result = parser_fn(
                    html=html,
                    source_policy=source_policy,
                    source_url=settings.scraped_hotel_ratings_base_url,
                )
            except Exception:
                # Defensive: `parse_scraped_hotel_ratings_html` already
                # catches its own internal parsing errors and returns
                # `FAILED` rather than raising, but this adapter must
                # never assume every current or future source_parsers
                # module upholds that -- one slot's parser misbehaving
                # must never crash the whole multi-source request or
                # block another slot's real success.
                logger.warning(
                    "ScrapedLocalHotelRatingsProvider's parser raised unexpectedly "
                    "for source '%s'; treating this source as failed.",
                    slot.source_id,
                    exc_info=True,
                )
                failed_labels.append(slot.source_id)
                reason = f"{slot.source_id}: its parser raised an unexpected error."
                warnings.append(reason)
                checked_slot_messages.append(reason)
                continue

            if slot_result.status == HotelRatingsParseStatus.SUCCESS:
                all_records.extend(slot_result.records)
                success_labels.append(slot.source_id)
            elif slot_result.status == HotelRatingsParseStatus.FAILED:
                failed_labels.append(slot.source_id)
                reason = f"{slot.source_id}: {slot_result.message or 'failed to parse.'}"
                warnings.append(reason)
                checked_slot_messages.append(reason)
            else:
                # NOT_CONNECTED (the constructed source_policy itself
                # refused -- not expected here since every policy this
                # method builds allows_reviews) or UNAVAILABLE (a valid
                # file with zero usable rating records) -- either way, an
                # honest, non-fatal "nothing here" for this one slot only.
                reason = f"{slot.source_id}: {slot_result.message or 'no data available.'}"
                warnings.append(reason)
                checked_slot_messages.append(reason)

        if all_records:
            items = _match_records_to_requests(requests, all_records)
            matched_count = sum(1 for item in items if item.matched)
            message = (
                f"Parsed {len(all_records)} rating record(s) from {len(success_labels)} "
                f"source(s): {', '.join(success_labels)}. Matched {matched_count} of "
                f"{len(requests)} requested propert(y/ies) by exact name. This is "
                "local/manual data, not official-provider data."
            )
            if warnings:
                message += f" {len(warnings)} other configured source(s) had no data."
            return HotelRatingsResult(
                provider=self.provider_name,
                status=HotelRatingsStatus.SUCCESS,
                items=items,
                message=message,
                warnings=warnings,
            )

        if failed_labels:
            if len(checked_paths) == 1:
                message = checked_slot_messages[0]
            else:
                message = (
                    f"{len(failed_labels)} configured source(s) could not be parsed: "
                    f"{', '.join(failed_labels)}."
                )
            return self._empty_result(HotelRatingsStatus.FAILED, message, warnings=warnings)

        if not checked_paths:
            message = "No local scraped-hotel-ratings HTML path is configured."
        elif len(checked_paths) == 1:
            message = checked_slot_messages[0]
        else:
            message = (
                "No local scraped-hotel-ratings HTML files were found. Checked: "
                + ", ".join(checked_paths)
                + "."
            )

        return self._empty_result(HotelRatingsStatus.UNAVAILABLE, message, warnings=warnings)

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
            base_url=settings.scraped_hotel_ratings_base_url or "file://local-scraped-hotel-ratings",
            source_type=ScrapingSourceType.SCRAPED_PUBLIC_PAGE,
            enabled=True,
            approved_for_personal_use=True,
            allows_reviews=True,
            requires_login=False,
            paywalled=False,
            captcha_expected=False,
            rate_limit_seconds=settings.scraping_default_rate_limit_seconds,
            notes=(
                "Manual/local static HTML file supplied via a Step 185E-configured "
                f"path for source '{slot.source_id}' -- no live fetch is ever "
                "performed by this provider."
            ),
        )

    def _empty_result(
        self,
        status: HotelRatingsStatus,
        message: str,
        warnings: list[str] | None = None,
    ) -> HotelRatingsResult:
        return HotelRatingsResult(
            provider=self.provider_name,
            status=status,
            items=[],
            message=message,
            warnings=warnings or [],
        )

    def _read_cache(
        self, cache_store: ProviderCacheStore, query_hash: str
    ) -> HotelRatingsResult | None:
        try:
            entry = cache_store.get(_CACHE_SOURCE, query_hash)
        except Exception:
            logger.warning(
                "Scraped hotel ratings provider cache read failed; falling back to "
                "re-parsing the local HTML file(s).",
                exc_info=True,
            )
            return None

        if entry is None:
            return None

        try:
            return HotelRatingsResult.model_validate(entry.payload)
        except Exception:
            logger.warning(
                "Scraped hotel ratings provider cache entry was unusable; falling "
                "back to re-parsing the local HTML file(s).",
                exc_info=True,
            )
            return None

    def _write_cache(
        self,
        cache_store: ProviderCacheStore,
        query_hash: str,
        result: HotelRatingsResult,
        settings: Settings,
    ) -> None:
        try:
            cache_store.set(
                _CACHE_SOURCE,
                query_hash,
                result.model_dump(mode="json"),
                ttl_seconds=settings.scraped_hotel_ratings_cache_ttl_seconds,
            )
        except Exception:
            logger.warning(
                "Scraped hotel ratings provider cache write failed; returning the "
                "freshly-parsed result anyway.",
                exc_info=True,
            )
