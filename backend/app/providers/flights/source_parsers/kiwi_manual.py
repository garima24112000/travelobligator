from __future__ import annotations

from app.models.flight import FlightSearchRequest, FlightSearchResult
from app.models.scraping import ScrapingSourcePolicy
from app.providers.flights import scraped_parser as _scraped_parser

# Step 185D: Kiwi-labeled local/manual flight parser -- named
# `kiwi_manual` (never bare `kiwi`) specifically so it is never confused,
# in code, in tests, or in any log/message, with the real, live
# `flight_provider="kiwi_mcp"` integration
# (`app.providers.flights.kiwi_mcp_adapter`/`kiwi_mcp_client`). This
# module NEVER imports anything from those `kiwi_mcp_*` modules, never
# calls Kiwi's hosted MCP server, and never produces an offer whose
# `provider` field is `"kiwi_mcp"` -- every offer this parser (like every
# other parser in this package) produces always has `provider` starting
# with `"scraped:"` (see `scraped_parser.parse_scraped_flight_html`).
#
# This is NOT a live Kiwi.com integration, NOT browser automation, and
# does NOT claim to match Kiwi.com's real, current DOM in any way -- it
# is the same Step 169C static HTML micro-format every other source
# parser in this package uses, optionally annotated with
# `data-source="kiwi_manual"` per card for a synthetic test fixture's own
# clarity. A card explicitly tagged for a *different* brand is skipped;
# an untagged card is always included ("delegate to generic when
# source-specific markers are absent"). No socket is ever opened, no
# file is ever read by this module itself.
#
# `FLIGHT_MANUAL_HTML_SOURCE=kiwi` (the config label a user actually
# types) resolves to this module via the flight adapter's own legacy-
# label-to-parser-key mapping -- see
# `app.providers.flights.scraped_adapter`'s own docstring for why the
# config label ("kiwi") and this module's/the registry's canonical name
# ("kiwi_manual") are deliberately spelled differently.

DATA_SOURCE = "kiwi_manual"
PARSER_VERSION = "flight_source_parser_kiwi_manual_v1"


def parse(
    html: str,
    source_policy: ScrapingSourcePolicy,
    request: FlightSearchRequest,
    source_url: str | None = None,
    parser_version: str = PARSER_VERSION,
) -> FlightSearchResult:
    result = _scraped_parser.parse_scraped_flight_html(
        html=html,
        source_policy=source_policy,
        request=request,
        source_url=source_url,
        parser_version=parser_version,
        required_data_source=DATA_SOURCE,
    )
    # Defensive, structural guarantee (never expected to trip): this
    # parser must never produce an offer that could be mistaken for real
    # Kiwi MCP data.
    for offer in result.offers:
        assert offer.provider != "kiwi_mcp"
    return result
