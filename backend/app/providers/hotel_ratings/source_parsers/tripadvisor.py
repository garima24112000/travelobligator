from __future__ import annotations

from app.models.scraping import ScrapingSourcePolicy
from app.providers.hotel_ratings import scraped_parser as _scraped_parser
from app.providers.hotel_ratings.scraped_parser import HotelRatingsParseResult

# Step 185E: Tripadvisor-labeled local/manual hotel-ratings parser.
#
# This is NOT a live Tripadvisor integration, NOT browser automation, and
# does NOT claim to match Tripadvisor's real, current DOM in any way -- it
# is the same synthetic `hotel-rating` static HTML micro-format every
# other source parser in this package uses, optionally annotated with
# `data-source="tripadvisor"` per card for a synthetic test fixture's own
# clarity. A card explicitly tagged for a *different* brand (e.g.
# `data-source="google_places_ratings"`) is skipped; an untagged card is
# always included ("delegate to generic when source-specific markers are
# absent"). No socket is ever opened, no file is ever read by this module
# itself -- `html` must already be a string the caller (the hotel-ratings
# adapter) obtained from a local file the operator supplied.
#
# Tripadvisor is restricted from live scraping under this project's policy
# (CLAUDE.md Core Rules: "Do not scrape restricted providers" names
# Tripadvisor explicitly; see also `app.providers.scraping_source_registry_
# defaults`'s `tripadvisor` entry) -- this parser exists only to label an
# already locally-supplied file, never to fetch anything from
# tripadvisor.com itself. No review text is ever extracted -- only a
# rating value and a review count, never a claim that a review has been
# verified.

DATA_SOURCE = "tripadvisor"
PARSER_VERSION = "hotel_ratings_source_parser_tripadvisor_v1"


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
