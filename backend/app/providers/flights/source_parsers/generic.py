from __future__ import annotations

from app.models.flight import FlightSearchRequest, FlightSearchResult
from app.models.scraping import ScrapingSourcePolicy
from app.providers.flights import scraped_parser as _scraped_parser

# Step 185D: the "generic" entry in `app.providers.flights.
# source_parsers` -- a thin wrapper around the pre-existing Step 169C
# generic parser, with no `data-source` filter applied at all (every
# card is accepted, exactly as before this step). This is what keeps
# `FLIGHT_MANUAL_HTML_SOURCE=generic` (the default) and every pre-185D
# fixture/test byte-for-byte unchanged.
#
# Imports `app.providers.flights.scraped_parser` as a module (rather
# than `from ... import parse_scraped_flight_html`) so tests can
# monkeypatch `app.providers.flights.scraped_parser.
# parse_scraped_flight_html` (an attribute lookup resolved at call time)
# and have every source_parsers module -- this one included -- pick it
# up, mirroring `app.providers.accommodation.source_parsers.generic`'s
# identical Step 185C convention.

PARSER_VERSION = "flight_source_parser_generic_v1"


def parse(
    html: str,
    source_policy: ScrapingSourcePolicy,
    request: FlightSearchRequest,
    source_url: str | None = None,
    parser_version: str = PARSER_VERSION,
) -> FlightSearchResult:
    """No brand is claimed by this parser -- every card in `html` is
    accepted regardless of any `data-source` attribute it may carry.
    """
    return _scraped_parser.parse_scraped_flight_html(
        html=html,
        source_policy=source_policy,
        request=request,
        source_url=source_url,
        parser_version=parser_version,
    )
