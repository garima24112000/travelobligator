from __future__ import annotations

from app.models.accommodation import AccommodationSearchRequest, AccommodationSearchResult
from app.models.scraping import ScrapingSourcePolicy
from app.providers.accommodation import scraped_parser as _scraped_parser

# Step 185C: Airbnb-labeled local/manual accommodation parser. Mirrors
# `booking.py` exactly -- see that module's docstring for the full
# design rationale (synthetic `data-source="airbnb"` micro-format,
# delegates to generic for untagged cards, no live fetch, no browser
# automation). Airbnb is restricted from live scraping under this
# project's policy (see `app.providers.scraping_source_registry_
# defaults`'s `airbnb` entry) -- this parser exists only to label an
# already locally-supplied file, never to fetch anything from Airbnb's
# own site under any circumstance.

DATA_SOURCE = "airbnb"
PARSER_VERSION = "accommodation_source_parser_airbnb_v1"


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
