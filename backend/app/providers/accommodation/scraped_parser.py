from __future__ import annotations

import logging
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

from app.models.accommodation import (
    AccommodationAvailabilityStatus,
    AccommodationOffer,
    AccommodationSearchRequest,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.common import DataStatus
from app.models.scraping import (
    ScrapedDataConfidence,
    ScrapedDataProvenance,
    ScrapingExtractionMethod,
    ScrapingSourcePolicy,
)

logger = logging.getLogger(__name__)

# Static HTML parser framework for approved scraped accommodation sources
# (Step 168B, docs/12_provider_architecture.md section 47,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
#
# This module only transforms an already-provided HTML string into a
# normalized `AccommodationSearchResult` -- it never fetches a URL, never
# opens a socket, never drives a browser, and never imports httpx/requests/
# a browser-automation library. `AccommodationSearchRequest`/`source_url`
# are accepted only as metadata to label the result; they are never used
# to construct or follow a live request.
#
# Not wired into `ProviderGateway` or `PlanningOrchestrator` yet -- this is
# a standalone parsing function a future scraper adapter could call after
# it has already fetched a page's HTML through its own, separately-gated
# fetch path (none exists yet).
#
# Expected HTML micro-format (test fixtures only -- no real site is known
# to use this exact shape): a "property card" is any element carrying the
# class `property-card` and a `data-property-id` attribute. Inside it,
# fields are recognized by class name on a plain leaf element:
#
#   property-name        -- required; text content is the property name
#   price                 -- optional; text content is the nightly price,
#                            optional `data-currency="USD"` attribute
#   rating                -- optional; text content is a numeric rating
#   availability          -- optional; text content is "available"/
#                            "unavailable"/anything else -> unknown
#   address               -- optional; text content is a free-form address
#   amenity               -- optional, repeatable; text content is one
#                            amenity name
#   cancellation-policy   -- optional; text content is a free-form policy
#   booking-link (an `<a href="...">`) -- optional; its `href` is the
#                            booking URL
#
# Any field not present in the HTML stays `None`/empty on the resulting
# `AccommodationOffer` -- never guessed, estimated, or backfilled.


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_CARD_CLASS = "property-card"
_FIELD_CLASS_TO_KEY: dict[str, str] = {
    "property-name": "name",
    "price": "price",
    "rating": "rating",
    "availability": "availability",
    "address": "address",
    "amenity": "amenity",
    "cancellation-policy": "cancellation_policy",
}


class _PropertyCardHTMLParser(HTMLParser):
    """Extracts a flat list of raw property-card dicts from static HTML
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
        self._current_field_attrs: dict[str, str | None] = {}
        self._current_field_depth = 0
        self._text_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        if self._current_card is None:
            if _CARD_CLASS in classes:
                self._current_card = {
                    "property_id": attr_dict.get("data-property-id"),
                    "fields": {},
                    "amenities": [],
                }
                self._card_open_tags = 0
            return

        self._card_open_tags += 1

        if self._current_field_key is not None:
            # Fixtures don't nest one recognized field inside another --
            # a nested tag inside an already-open field is just part of
            # that field's own markup and is ignored here.
            return

        if "booking-link" in classes:
            self._current_card["fields"]["booking_url"] = attr_dict.get("href")
            return

        matched_field = next(
            (key for class_name, key in _FIELD_CLASS_TO_KEY.items() if class_name in classes),
            None,
        )
        if matched_field is not None:
            self._current_field_key = matched_field
            self._current_field_attrs = attr_dict
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
            field_key = self._current_field_key
            if field_key == "amenity":
                if text:
                    self._current_card["amenities"].append(text)
            elif field_key == "price":
                if text:
                    self._current_card["fields"]["price_text"] = text
                    currency = self._current_field_attrs.get("data-currency")
                    if currency:
                        self._current_card["fields"]["price_currency"] = currency
            elif text:
                self._current_card["fields"][field_key] = text
            self._current_field_key = None
            self._current_field_attrs = {}
            self._text_buffer = []

        self._card_open_tags -= 1


def _parse_optional_float(text: str | None) -> float | None:
    """Parses a plain numeric string, or returns `None` for anything
    missing/unparseable -- never guesses a number. Strips common currency
    symbols and thousands separators only; never invents a digit that
    wasn't present in the source text.
    """
    if text is None:
        return None
    cleaned = text.strip().lstrip("$€£").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value >= 0 else None


def _parse_availability(text: str | None) -> AccommodationAvailabilityStatus:
    if text is None:
        return AccommodationAvailabilityStatus.UNKNOWN
    normalized = text.strip().lower()
    if normalized == "available":
        return AccommodationAvailabilityStatus.AVAILABLE
    if normalized == "unavailable":
        return AccommodationAvailabilityStatus.UNAVAILABLE
    return AccommodationAvailabilityStatus.UNKNOWN


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
    if not source_policy.allows_lodging:
        return f"Scraping source '{source_policy.source_id}' does not allow lodging data."
    return None


def _build_offer_from_card(
    raw_card: dict[str, Any],
    source_policy: ScrapingSourcePolicy,
    source_url: str | None,
    parser_version: str,
    fetched_at: datetime,
) -> AccommodationOffer | None:
    fields: dict[str, Any] = raw_card["fields"]
    property_id = raw_card.get("property_id")
    name = fields.get("name")
    if not property_id or not name:
        # Not enough to safely identify a property -- skip this card
        # entirely rather than inventing an id or a name.
        return None

    nightly_price_amount = _parse_optional_float(fields.get("price_text"))
    currency = fields.get("price_currency") if nightly_price_amount is not None else None

    provenance = ScrapedDataProvenance(
        source_id=source_policy.source_id,
        source_name=source_policy.source_name,
        confidence=ScrapedDataConfidence.EXPERIMENTAL,
        fetched_at=fetched_at,
        parser_version=parser_version,
        source_url=source_url,
        extraction_method=ScrapingExtractionMethod.STATIC_HTML_PARSER,
    )

    return AccommodationOffer(
        provider=f"scraped:{source_policy.source_id}",
        provider_property_id=property_id,
        property_name=name,
        address=fields.get("address"),
        nightly_price_amount=nightly_price_amount,
        currency=currency,
        availability_status=_parse_availability(fields.get("availability")),
        booking_url=fields.get("booking_url") or None,
        rating=_parse_optional_float(fields.get("rating")),
        amenities=list(raw_card.get("amenities", [])),
        cancellation_policy=fields.get("cancellation_policy"),
        data_status=DataStatus.SCRAPED_PUBLIC_PAGE,
        source_name=source_policy.source_name,
        source_url=source_url,
        fetched_at=fetched_at,
        scraped_provenance=provenance,
    )


def parse_scraped_accommodation_html(
    html: str,
    source_policy: ScrapingSourcePolicy,
    request: AccommodationSearchRequest,
    source_url: str | None = None,
    parser_version: str = "unknown",
) -> AccommodationSearchResult:
    """Transforms an already-provided static HTML string into a normalized
    `AccommodationSearchResult`, honoring `source_policy`'s safety rules.

    Never fetches `source_url` or anything else -- `html` must already be
    the full page content the caller obtained through its own, separately
    gated fetch path (none exists in this codebase yet). `request` is used
    only to label the result's context; its fields are never used to
    construct or follow a live request either.

    Refuses to parse (returns `status=not_connected`, `offers=[]`) when
    `source_policy` is unsafe, not enabled, or does not allow lodging data
    -- the HTML is never even touched in that case. Returns
    `status=unavailable` when the HTML is valid but no property card is
    found, and `status=failed` (never raising) if parsing/normalizing the
    HTML fails unexpectedly. Every returned offer carries a
    `scraped_provenance` labeling it `scraped_public_page`/`experimental`,
    with `official_provider` structurally fixed to `False`.
    """
    provider_name = f"scraped:{source_policy.source_id}"

    refusal_message = _refusal_reason(source_policy)
    if refusal_message is not None:
        return AccommodationSearchResult(
            provider=provider_name,
            status=AccommodationSearchStatus.NOT_CONNECTED,
            offers=[],
            message=refusal_message,
        )

    try:
        card_parser = _PropertyCardHTMLParser()
        card_parser.feed(html)
        card_parser.close()

        fetched_at = _utc_now()
        offers = [
            offer
            for raw_card in card_parser.cards
            if (
                offer := _build_offer_from_card(
                    raw_card, source_policy, source_url, parser_version, fetched_at
                )
            )
            is not None
        ]
    except Exception:
        logger.warning(
            "parse_scraped_accommodation_html failed unexpectedly for source '%s'; "
            "returning a failed result so callers never crash on malformed HTML.",
            source_policy.source_id,
            exc_info=True,
        )
        return AccommodationSearchResult(
            provider=provider_name,
            status=AccommodationSearchStatus.FAILED,
            offers=[],
            message="The scraped HTML could not be parsed.",
        )

    if not offers:
        return AccommodationSearchResult(
            provider=provider_name,
            status=AccommodationSearchStatus.UNAVAILABLE,
            offers=[],
            message=(
                f"No valid property cards were found in the HTML from "
                f"'{source_policy.source_name}'."
            ),
        )

    return AccommodationSearchResult(
        provider=provider_name,
        status=AccommodationSearchStatus.SUCCESS,
        offers=offers,
        message=(
            f"Parsed from '{source_policy.source_name}' -- a scraped public page. "
            "This is experimental/fragile, non-official-provider data; treat it "
            "as unverified."
        ),
    )
