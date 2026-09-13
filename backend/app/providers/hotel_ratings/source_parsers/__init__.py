from __future__ import annotations

from types import ModuleType
from typing import Callable

from app.models.scraping import ScrapingSourcePolicy
from app.providers.hotel_ratings.scraped_parser import HotelRatingsParseResult
from app.providers.hotel_ratings.source_parsers import (
    generic,
    google_places_ratings,
    tripadvisor,
)

# Step 185E: source-specific hotel-ratings parser package. Every module
# here is a pure HTML-string -> `HotelRatingsParseResult` transform --
# none opens a file, opens a socket, imports requests/httpx/Playwright/
# Selenium, or claims to match any real site's current live DOM. Each
# non-generic module recognizes the same synthetic `hotel-rating` micro-
# format, optionally annotated per-card with a synthetic
# `data-source="<brand>"` attribute; an untagged card always "delegates
# to generic" (is still included), so every generic fixture/test is
# unaffected by this package's existence.

HotelRatingsSourceParser = Callable[..., HotelRatingsParseResult]

# Maps source_id -> the actual module object (not a captured function/
# constant) so `get_hotel_ratings_source_parser`/`get_hotel_ratings_
# source_parser_version` always resolve `.parse`/`.PARSER_VERSION` fresh
# at call time -- this keeps every module individually monkeypatchable in
# tests, mirroring `app.providers.accommodation.source_parsers`/
# `app.providers.flights.source_parsers`'s identical convention.
_PARSER_MODULES: dict[str, ModuleType] = {
    "tripadvisor": tripadvisor,
    "google_places_ratings": google_places_ratings,
    "generic": generic,
}


def get_hotel_ratings_source_parser(source_id: str) -> HotelRatingsSourceParser:
    """Resolves `source_id` (e.g. `"tripadvisor"`, `"google_places_
    ratings"`, `"generic"`) to its source-specific parser function,
    falling back to the generic parser for an unknown/unrecognized
    `source_id` -- never raises, never crashes. Selecting a parser here
    never changes provenance into official-provider data: every parser in
    this package (generic included) produces records via the same
    underlying `parse_scraped_hotel_ratings_html`, whose every
    `AccommodationRating.data_status` is `scraped_public_page`.
    """
    module = _PARSER_MODULES.get(source_id, generic)
    return module.parse


def get_hotel_ratings_source_parser_version(source_id: str) -> str:
    """Resolves `source_id` to its parser module's own `PARSER_VERSION`
    string, falling back to the generic parser's version for an unknown/
    unrecognized `source_id`. Used only to keep the hotel-ratings
    provider's cache key sensitive to per-source parser-logic changes.
    """
    module = _PARSER_MODULES.get(source_id, generic)
    return module.PARSER_VERSION


__all__ = [
    "HotelRatingsSourceParser",
    "get_hotel_ratings_source_parser",
    "get_hotel_ratings_source_parser_version",
    "generic",
    "google_places_ratings",
    "tripadvisor",
]
