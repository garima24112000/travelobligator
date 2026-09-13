from __future__ import annotations

from app.models.accommodation import AccommodationSearchRequest, AccommodationSearchResult
from app.models.scraping import ScrapingSourcePolicy
from app.providers.accommodation import scraped_parser as _scraped_parser

# Step 185C: the "generic" entry in `app.providers.accommodation.
# source_parsers` -- a thin wrapper around the pre-existing Step 168B
# generic parser, with no `data-source` filter applied at all (every
# card is accepted, exactly as before this step). This is what keeps
# `ACCOMMODATION_MANUAL_HTML_SOURCE=generic` (the default) and every
# pre-185C fixture/test byte-for-byte unchanged.
#
# Imports `app.providers.accommodation.scraped_parser` as a module
# (rather than `from ... import parse_scraped_accommodation_html`) so
# tests can monkeypatch `source_parsers.generic.parse_scraped_
# accommodation_html` (an attribute lookup resolved at call time) the
# same way pre-185C tests already monkeypatched the adapter's own
# directly-imported copy of the name -- see
# `test_scraped_accommodation_cache.py`'s `_install_counting_parse`.

PARSER_VERSION = "accommodation_source_parser_generic_v1"


def parse(
    html: str,
    source_policy: ScrapingSourcePolicy,
    request: AccommodationSearchRequest,
    source_url: str | None = None,
    parser_version: str = PARSER_VERSION,
) -> AccommodationSearchResult:
    """No brand is claimed by this parser -- every card in `html` is
    accepted regardless of any `data-source` attribute it may carry.
    """
    return _scraped_parser.parse_scraped_accommodation_html(
        html=html,
        source_policy=source_policy,
        request=request,
        source_url=source_url,
        parser_version=parser_version,
    )
