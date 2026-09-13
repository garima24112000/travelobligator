from __future__ import annotations

from app.models.accommodation import AccommodationSearchRequest, AccommodationSearchResult
from app.models.scraping import ScrapingSourcePolicy
from app.providers.accommodation import scraped_parser as _scraped_parser

# Step 185C: Hostelworld-labeled local/manual accommodation parser.
# Mirrors `booking.py` exactly -- see that module's docstring for the
# full design rationale (synthetic `data-source="hostelworld"`
# micro-format, delegates to generic for untagged cards, no live fetch,
# no browser automation). Hostelworld is not on this project's
# restricted-provider list, but no human has reviewed a live fetch path
# for it yet (see `app.providers.scraping_source_registry_defaults`'s
# `hostelworld` entry) -- this parser is manual/local-only.

DATA_SOURCE = "hostelworld"
PARSER_VERSION = "accommodation_source_parser_hostelworld_v1"


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
