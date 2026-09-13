from __future__ import annotations

from app.models.flight import FlightSearchRequest, FlightSearchResult
from app.models.scraping import ScrapingSourcePolicy
from app.providers.flights import scraped_parser as _scraped_parser

# Step 185D: Skyscanner-labeled local/manual flight parser.
#
# This is NOT a live Skyscanner integration, NOT browser automation, and
# does NOT claim to match Skyscanner's real, current DOM in any way -- it
# is the same Step 169C static HTML micro-format (`flight-offer`
# elements with `outbound-segment`/`return-segment` children) every other
# source parser in this package uses, optionally annotated with
# `data-source="skyscanner"` per card for a synthetic test fixture's own
# clarity. A card explicitly tagged for a *different* brand (e.g.
# `data-source="google_flights"`) is skipped; an untagged card is always
# included ("delegate to generic when source-specific markers are
# absent"). No socket is ever opened, no file is ever read by this
# module itself -- `html` must already be a string the caller (the
# flight adapter) obtained from a local file the operator supplied.
#
# Skyscanner is not on this project's restricted-provider list, but no
# human has reviewed a live fetch path for it yet (see
# `app.providers.scraping_source_registry_defaults`'s `skyscanner`
# entry) -- this parser exists only to label an already locally-supplied
# file, never to fetch anything from Skyscanner's own site.

DATA_SOURCE = "skyscanner"
PARSER_VERSION = "flight_source_parser_skyscanner_v1"


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
