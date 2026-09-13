from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from html.parser import HTMLParser
from typing import Any

from app.models.common import DataStatus
from app.models.hotel_ratings import AccommodationRating
from app.models.scraping import (
    ScrapedDataConfidence,
    ScrapingExtractionMethod,
    ScrapingSourcePolicy,
)

logger = logging.getLogger(__name__)

# Static HTML parser framework for approved scraped hotel-ratings sources
# (Step 185E, docs/12_provider_architecture.md, docs/14_backend_architecture.md),
# mirroring `app.providers.accommodation.scraped_parser`/
# `app.providers.flights.scraped_parser` (Steps 168B/169C).
#
# This module only transforms an already-provided HTML string into a
# normalized list of rating records -- it never fetches a URL, never opens
# a socket, never drives a browser, and never imports httpx/requests/a
# browser-automation library. `source_url` is accepted only as metadata to
# label the result; it is never used to construct or follow a live
# request.
#
# Unlike the accommodation/flight parsers (which build a self-contained
# search result from a request), hotel-ratings identity matching happens
# one layer up, in `ScrapedLocalHotelRatingsProvider` -- this module has no
# concept of a `HotelRatingsRequest`/offer at all. It only ever answers
# "what rating records does this HTML file contain," never "does record X
# belong to offer Y" -- that conservative, exact-name matching is the
# adapter's job (mirroring `HotelRatingEnrichmentService`'s own
# no-fuzzy-matching philosophy).
#
# Expected HTML micro-format (test fixtures only -- no real site is known
# to use this exact shape): a "hotel rating" is any element carrying the
# class `hotel-rating`. Inside it, fields are recognized by class name on a
# plain leaf element:
#
#   property-name   -- required; text content is the property name, used
#                       only for later conservative exact-name matching
#                       against an already-known `AccommodationOffer.
#                       property_name` -- never itself a claim of identity.
#   rating-value      -- optional; text content is a numeric rating on a
#                       0-5 scale; out-of-range or unparseable text is
#                       treated as absent, never clamped/guessed.
#   review-count       -- optional; text content is a non-negative integer
#                       review count.
#
# A record is only produced when `property-name` is present AND at least
# one of `rating-value`/`review-count` is present and parses successfully
# -- a name with neither is not useful data and is dropped entirely,
# rather than producing a record that carries nothing. Any field not
# present in the HTML stays `None` on the resulting `AccommodationRating`
# -- never guessed, estimated, or backfilled. No review text is ever
# extracted or carried -- this app only ever surfaces a rating value and a
# review count, never raw review text, and never a claim that a property
# ranks above others or that a review has been verified.


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class HotelRatingsParseStatus(str, Enum):
    SUCCESS = "success"
    NOT_CONNECTED = "not_connected"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True)
class ParsedHotelRatingRecord:
    """One rating record extracted from a local/manual HTML file --
    `property_name` is used only for later conservative, exact-name
    matching against an `AccommodationOffer`; it is never itself a claim
    that the match is correct. `rating` is a fully-built
    `AccommodationRating` snapshot, already carrying honest provenance
    (`provider`/`source_name`/`data_status=scraped_public_page`) -- never
    official-provider data, never a guessed value.
    """

    property_name: str
    rating: AccommodationRating


@dataclass(frozen=True)
class HotelRatingsParseResult:
    status: HotelRatingsParseStatus
    records: list[ParsedHotelRatingRecord] = field(default_factory=list)
    message: str | None = None


_CARD_CLASS = "hotel-rating"
_FIELD_CLASS_TO_KEY: dict[str, str] = {
    "property-name": "name",
    "rating-value": "rating_value",
    "review-count": "review_count",
}


class _HotelRatingCardHTMLParser(HTMLParser):
    """Extracts a flat list of raw hotel-rating dicts from static HTML
    using the fixed micro-format documented above. Uses only the Python
    standard library `html.parser.HTMLParser` -- no BeautifulSoup, no
    external dependency. Never executes a `<script>`, never renders
    anything, never follows a link -- this walks the parsed tag/attribute/
    text stream exactly once and never makes any network call.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, Any]] = []
        self._current_card: dict[str, Any] | None = None
        self._card_open_tags = 0
        self._current_field_key: str | None = None
        self._current_field_depth = 0
        self._text_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        if self._current_card is None:
            if _CARD_CLASS in classes:
                self._current_card = {
                    # Step 185E: optional per-card brand tag (e.g.
                    # `data-source="tripadvisor"`), consumed only by
                    # `parse_scraped_hotel_ratings_html`'s optional
                    # `required_data_source` filter below -- absent on
                    # every generic fixture, so `None` (and thus ignored)
                    # for generic behavior.
                    "data_source": attr_dict.get("data-source"),
                    "fields": {},
                }
                self._card_open_tags = 0
            return

        self._card_open_tags += 1

        if self._current_field_key is not None:
            # Fixtures don't nest one recognized field inside another --
            # a nested tag inside an already-open field is just part of
            # that field's own markup and is ignored here.
            return

        matched_field = next(
            (key for class_name, key in _FIELD_CLASS_TO_KEY.items() if class_name in classes),
            None,
        )
        if matched_field is not None:
            self._current_field_key = matched_field
            self._current_field_depth = self._card_open_tags
            self._text_buffer = []

    def handle_data(self, data: str) -> None:
        if self._current_field_key is not None:
            self._text_buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._current_card is None:
            return

        if self._card_open_tags == 0:
            self.cards.append(self._current_card)
            self._current_card = None
            return

        if (
            self._current_field_key is not None
            and self._card_open_tags == self._current_field_depth
        ):
            text = "".join(self._text_buffer).strip()
            if text:
                self._current_card["fields"][self._current_field_key] = text
            self._current_field_key = None
            self._text_buffer = []

        self._card_open_tags -= 1


def _parse_optional_bounded_float(
    text: str | None, minimum: float, maximum: float
) -> float | None:
    """Parses a plain numeric string bounded to `[minimum, maximum]`, or
    returns `None` for anything missing, unparseable, or out of range --
    never clamps to a boundary, never guesses a number, never fabricates a
    fallback rating.
    """
    if text is None:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value < minimum or value > maximum:
        return None
    return value


def _parse_optional_non_negative_int(text: str | None) -> int | None:
    if text is None:
        return None
    cleaned = text.strip().replace(",", "")
    if not cleaned:
        return None
    try:
        value = int(cleaned)
    except ValueError:
        return None
    return value if value >= 0 else None


def _refusal_reason(source_policy: ScrapingSourcePolicy) -> str | None:
    """Returns a safe, honest reason this source will not be parsed, or
    `None` if parsing may proceed. Checked before any HTML is touched.
    """
    if source_policy.is_unsafe:
        return (
            f"Scraping source '{source_policy.source_id}' is unsafe "
            "(requires_login, paywalled, or captcha_expected is True, or it is "
            "not approved_for_personal_use) and will not be parsed."
        )
    if not source_policy.enabled:
        return f"Scraping source '{source_policy.source_id}' is not enabled."
    if not source_policy.allows_reviews:
        return f"Scraping source '{source_policy.source_id}' does not allow review data."
    return None


def _build_record_from_card(
    raw_card: dict[str, Any],
    source_policy: ScrapingSourcePolicy,
    source_url: str | None,
    parser_version: str,
    fetched_at: datetime,
) -> ParsedHotelRatingRecord | None:
    fields: dict[str, Any] = raw_card["fields"]
    property_name = fields.get("name")
    if not property_name:
        # Not enough to safely identify a property -- skip this card
        # entirely rather than inventing a name.
        return None

    rating_value = _parse_optional_bounded_float(fields.get("rating_value"), 0.0, 5.0)
    review_count = _parse_optional_non_negative_int(fields.get("review_count"))

    if rating_value is None and review_count is None:
        # Nothing usable was extracted -- never produce a record that
        # carries no real fact.
        return None

    rating = AccommodationRating(
        value=rating_value,
        review_count=review_count,
        provider=f"scraped:{source_policy.source_id}",
        source_name=source_policy.source_name,
        source_url=source_url,
        data_status=DataStatus.SCRAPED_PUBLIC_PAGE,
        retrieved_at=fetched_at,
    )
    return ParsedHotelRatingRecord(property_name=property_name, rating=rating)


def parse_scraped_hotel_ratings_html(
    html: str,
    source_policy: ScrapingSourcePolicy,
    source_url: str | None = None,
    parser_version: str = "unknown",
    required_data_source: str | None = None,
) -> HotelRatingsParseResult:
    """Transforms an already-provided static HTML string into a
    `HotelRatingsParseResult`, honoring `source_policy`'s safety rules.

    Never fetches `source_url` or anything else -- `html` must already be
    the full file content the caller obtained by reading a local file the
    operator supplied themselves. Refuses to parse (returns
    `status=not_connected`, `records=[]`) when `source_policy` is unsafe,
    not enabled, or does not allow review data -- the HTML is never even
    touched in that case. Returns `status=unavailable` when the HTML is
    valid but no usable rating record is found, and `status=failed`
    (never raising) if parsing fails unexpectedly. Every returned record's
    `rating` carries `data_status=scraped_public_page` and a
    `source_name`/`provider` that always reads as manual/local, non-
    official-provider data -- this module never claims a rating is a
    review that has been verified, a guarantee of quality, or an
    official-provider fact.

    `required_data_source` (optional, default `None`): when given, a card
    is included only if its own `data-source` attribute is absent
    (untagged cards always "delegate to generic", i.e. are always
    included) or matches this value exactly -- a card explicitly tagged
    for a *different* brand is skipped. `None` (the default, used by
    every generic caller) applies no filter at all.
    """
    provider_name = f"scraped:{source_policy.source_id}"

    refusal_message = _refusal_reason(source_policy)
    if refusal_message is not None:
        return HotelRatingsParseResult(
            status=HotelRatingsParseStatus.NOT_CONNECTED,
            records=[],
            message=refusal_message,
        )

    try:
        card_parser = _HotelRatingCardHTMLParser()
        card_parser.feed(html)
        card_parser.close()

        raw_cards = card_parser.cards
        if required_data_source is not None:
            raw_cards = [
                raw_card
                for raw_card in raw_cards
                if raw_card.get("data_source") in (None, required_data_source)
            ]

        fetched_at = _utc_now()
        records = [
            record
            for raw_card in raw_cards
            if (
                record := _build_record_from_card(
                    raw_card, source_policy, source_url, parser_version, fetched_at
                )
            )
            is not None
        ]
    except Exception:
        logger.warning(
            "parse_scraped_hotel_ratings_html failed unexpectedly for source '%s'; "
            "returning a failed result so callers never crash on malformed HTML.",
            source_policy.source_id,
            exc_info=True,
        )
        return HotelRatingsParseResult(
            status=HotelRatingsParseStatus.FAILED,
            records=[],
            message=f"{provider_name}: the scraped HTML could not be parsed.",
        )

    if not records:
        return HotelRatingsParseResult(
            status=HotelRatingsParseStatus.UNAVAILABLE,
            records=[],
            message=(
                f"No valid hotel rating records were found in the HTML from "
                f"'{source_policy.source_name}'."
            ),
        )

    return HotelRatingsParseResult(
        status=HotelRatingsParseStatus.SUCCESS,
        records=records,
        message=(
            f"Parsed from '{source_policy.source_name}' -- a scraped public page. "
            "This is experimental/fragile, non-official-provider data; treat it "
            "as unverified."
        ),
    )
