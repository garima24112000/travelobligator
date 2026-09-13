from __future__ import annotations

from types import ModuleType
from typing import Callable

from app.models.accommodation import AccommodationSearchRequest, AccommodationSearchResult
from app.models.scraping import ScrapingSourcePolicy
from app.providers.accommodation.source_parsers import (
    airbnb,
    booking,
    expedia,
    generic,
    hostelworld,
    hotelbeds,
    vrbo,
)

# Step 185C: source-specific accommodation parser package. Every module
# here is a pure HTML-string -> `AccommodationSearchResult` transform --
# none opens a file, opens a socket, imports requests/httpx/Playwright/
# Selenium, or claims to match any real site's current live DOM. Each
# non-generic module recognizes the same Step 168B generic property-card
# micro-format, optionally annotated per-card with a synthetic
# `data-source="<brand>"` attribute; an untagged card always "delegates
# to generic" (is still included), so every existing generic fixture/test
# is unaffected by this package's existence.

AccommodationSourceParser = Callable[..., AccommodationSearchResult]

# Maps source_id -> the actual module object (not a captured function/
# constant) so `get_accommodation_source_parser`/
# `get_accommodation_source_parser_version` always resolve `.parse`/
# `.PARSER_VERSION` fresh at call time -- this keeps every module
# individually monkeypatchable in tests (e.g.
# `monkeypatch.setattr(source_parsers.booking, "parse", fake)`), matching
# this codebase's established `_install_counting_parse`-style testing
# convention rather than silently freezing a stale reference at import
# time.
_PARSER_MODULES: dict[str, ModuleType] = {
    "booking": booking,
    "expedia": expedia,
    "hotelbeds": hotelbeds,
    "hostelworld": hostelworld,
    "vrbo": vrbo,
    "airbnb": airbnb,
    "generic": generic,
}


def get_accommodation_source_parser(source_id: str) -> AccommodationSourceParser:
    """Resolves `source_id` (e.g. `"booking"`, `"generic"`) to its
    source-specific parser function, falling back to the generic parser
    for an unknown/unrecognized `source_id` -- never raises, never
    crashes. Selecting a parser here never changes provenance into
    official-provider data: every parser in this package (generic
    included) produces offers via the same underlying
    `parse_scraped_accommodation_html`, which always sets
    `scraped_provenance.official_provider=False` structurally.
    """
    module = _PARSER_MODULES.get(source_id, generic)
    return module.parse


def get_accommodation_source_parser_version(source_id: str) -> str:
    """Resolves `source_id` to its parser module's own `PARSER_VERSION`
    string, falling back to the generic parser's version for an unknown/
    unrecognized `source_id`. Used only to keep the accommodation
    provider's cache key sensitive to per-source parser-logic changes.
    """
    module = _PARSER_MODULES.get(source_id, generic)
    return module.PARSER_VERSION


__all__ = [
    "AccommodationSourceParser",
    "get_accommodation_source_parser",
    "get_accommodation_source_parser_version",
    "airbnb",
    "booking",
    "expedia",
    "generic",
    "hostelworld",
    "hotelbeds",
    "vrbo",
]
