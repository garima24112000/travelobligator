from __future__ import annotations

from app.models.scraping import ScrapingSourcePolicy
from app.providers.hotel_ratings import scraped_parser as _scraped_parser
from app.providers.hotel_ratings.scraped_parser import HotelRatingsParseResult

# Step 185E: Google-Places-labeled local/manual hotel-ratings parser.
# Named `google_places_ratings` (mirroring the Step 185B registry entry of
# the same name) to keep it visually distinct in code from any future,
# separate, *official* Google Places API ratings adapter -- Google Places
# is never called live anywhere by this module.
#
# This is NOT a live Google Places API call, NOT browser automation, and
# does NOT claim to match any real Google Places response shape -- it is
# the same synthetic `hotel-rating` static HTML micro-format every other
# source parser in this package uses, optionally annotated with
# `data-source="google_places_ratings"` per card for a synthetic test
# fixture's own clarity. A card explicitly tagged for a *different* brand
# is skipped; an untagged card is always included ("delegate to generic
# when source-specific markers are absent"). No socket is ever opened, no
# file is ever read by this module itself.
#
# Google Places is not on this project's restricted-provider list, but no
# live fetch path is implemented or approved here -- see
# `app.providers.scraping_source_registry_defaults`'s
# `google_places_ratings` entry, which tracks a real, future *official
# API* adapter (reusing the existing `GOOGLE_PLACES_API_KEY`) as the
# honest long-term path, not scraping. No review text is ever extracted --
# only a rating value and a review count, never a claim that a review
# has been verified.

DATA_SOURCE = "google_places_ratings"
PARSER_VERSION = "hotel_ratings_source_parser_google_places_ratings_v1"


def parse(
    html: str,
    source_policy: ScrapingSourcePolicy,
    source_url: str | None = None,
    parser_version: str = PARSER_VERSION,
) -> HotelRatingsParseResult:
    return _scraped_parser.parse_scraped_hotel_ratings_html(
        html=html,
        source_policy=source_policy,
        source_url=source_url,
        parser_version=parser_version,
        required_data_source=DATA_SOURCE,
    )
