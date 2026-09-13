from __future__ import annotations

from app.models.flight import FlightSearchRequest, FlightSearchResult
from app.models.scraping import ScrapingSourcePolicy
from app.providers.flights import scraped_parser as _scraped_parser

# Step 185D: Google Flights-labeled local/manual flight parser. Mirrors
# `skyscanner.py` exactly -- see that module's docstring for the full
# design rationale (synthetic `data-source="google_flights"`
# micro-format, delegates to generic for untagged cards, no live fetch,
# no browser automation). Google Flights is restricted from live
# scraping under this project's policy (see
# `app.providers.scraping_source_registry_defaults`'s `google_flights`
# entry) -- this parser exists only to label an already locally-supplied
# file, never to fetch anything from Google Flights' own site under any
# circumstance.

DATA_SOURCE = "google_flights"
PARSER_VERSION = "flight_source_parser_google_flights_v1"


def parse(
    html: str,
    source_policy: ScrapingSourcePolicy,
    request: FlightSearchRequest,
    source_url: str | None = None,
    parser_version: str = PARSER_VERSION,
) -> FlightSearchResult:
    return _scraped_parser.parse_scraped_flight_html(
        html=html,
        source_policy=source_policy,
        request=request,
        source_url=source_url,
        parser_version=parser_version,
        required_data_source=DATA_SOURCE,
    )
