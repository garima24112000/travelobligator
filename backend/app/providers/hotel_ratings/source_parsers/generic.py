from __future__ import annotations

from app.models.scraping import ScrapingSourcePolicy
from app.providers.hotel_ratings import scraped_parser as _scraped_parser
from app.providers.hotel_ratings.scraped_parser import HotelRatingsParseResult

# Step 185E: the "generic" entry in `app.providers.hotel_ratings.
# source_parsers` -- a thin wrapper around `parse_scraped_hotel_ratings_
# html`, with no `data-source` filter applied at all (every card is
# accepted). This is what keeps `HOTEL_RATINGS_MANUAL_HTML_SOURCE=generic`
# (the default) and every generic fixture/test byte-for-byte unchanged.
#
# Imports `app.providers.hotel_ratings.scraped_parser` as a module (rather
# than `from ... import parse_scraped_hotel_ratings_html`) so tests can
# monkeypatch `source_parsers.generic.parse_scraped_hotel_ratings_html`
# (an attribute lookup resolved at call time), mirroring
# `app.providers.accommodation.source_parsers.generic`'s identical Step
# 185C convention.

PARSER_VERSION = "hotel_ratings_source_parser_generic_v1"


def parse(
    html: str,
    source_policy: ScrapingSourcePolicy,
    source_url: str | None = None,
    parser_version: str = PARSER_VERSION,
) -> HotelRatingsParseResult:
    """No brand is claimed by this parser -- every card in `html` is
    accepted regardless of any `data-source` attribute it may carry.
    """
    return _scraped_parser.parse_scraped_hotel_ratings_html(
        html=html,
        source_policy=source_policy,
        source_url=source_url,
        parser_version=parser_version,
    )
