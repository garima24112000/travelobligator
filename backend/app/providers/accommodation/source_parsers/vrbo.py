from __future__ import annotations

from app.models.accommodation import AccommodationSearchRequest, AccommodationSearchResult
from app.models.scraping import ScrapingSourcePolicy
from app.providers.accommodation import scraped_parser as _scraped_parser

# Step 185C: Vrbo-labeled local/manual accommodation parser. Mirrors
# `booking.py` exactly -- see that module's docstring for the full
# design rationale (synthetic `data-source="vrbo"` micro-format,
# delegates to generic for untagged cards, no live fetch, no browser
# automation). Vrbo is restricted from live scraping under this
# project's policy (see `app.providers.scraping_source_registry_
# defaults`'s `vrbo` entry). This parser has no lodging-type
# distinction (vacation rental vs. hotel) -- it extracts the exact same
# `AccommodationOffer` fields every other source parser in this package
# does, never a Vrbo-specific field this app's model doesn't have.

DATA_SOURCE = "vrbo"
PARSER_VERSION = "accommodation_source_parser_vrbo_v1"


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
