from __future__ import annotations

from app.models.accommodation import AccommodationSearchRequest, AccommodationSearchResult
from app.models.scraping import ScrapingSourcePolicy
from app.providers.accommodation import scraped_parser as _scraped_parser

# Step 185C: Booking.com-labeled local/manual accommodation parser.
#
# This is NOT a live Booking.com integration, NOT browser automation, and
# does NOT claim to match Booking.com's real, current DOM in any way --
# it is the same Step 168B static HTML micro-format (`property-card`
# elements) every other source parser in this package uses, optionally
# annotated with `data-source="booking"` per card for a synthetic test
# fixture's own clarity. A card explicitly tagged for a *different* brand
# (e.g. `data-source="expedia"`) is skipped; an untagged card is always
# included ("delegate to generic when source-specific markers are
# absent"). No socket is ever opened, no file is ever read by this
# module itself -- `html` must already be a string the caller (the
# accommodation adapter) obtained from a local file the operator supplied.
#
# Booking.com is restricted from live scraping under this project's
# policy (see `app.providers.scraping_source_registry_defaults`'s
# `booking` entry) -- this parser exists only to label an already
# locally-supplied file, never to fetch anything from booking.com itself.

DATA_SOURCE = "booking"
PARSER_VERSION = "accommodation_source_parser_booking_v1"


def parse(
    html: str,
    source_policy: ScrapingSourcePolicy,
    request: AccommodationSearchRequest,
    source_url: str | None = None,
    parser_version: str = PARSER_VERSION,
) -> AccommodationSearchResult:
    return _scraped_parser.parse_scraped_accommodation_html(
        html=html,
        source_policy=source_policy,
        request=request,
        source_url=source_url,
        parser_version=parser_version,
        required_data_source=DATA_SOURCE,
    )
