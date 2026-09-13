from __future__ import annotations

from types import ModuleType
from typing import Callable

from app.models.flight import FlightSearchRequest, FlightSearchResult
from app.models.scraping import ScrapingSourcePolicy
from app.providers.flights.source_parsers import (
    generic,
    google_flights,
    kiwi_manual,
    skyscanner,
)

# Step 185D: source-specific flight parser package. Every module here is
# a pure HTML-string -> `FlightSearchResult` transform -- none opens a
# file, opens a socket, imports requests/httpx/Playwright/Selenium, or
# claims to match any real site's current live DOM. Each non-generic
# module recognizes the same Step 169C generic `flight-offer` micro-
# format, optionally annotated per-card with a synthetic
# `data-source="<brand>"` attribute; an untagged card always "delegates
# to generic" (is still included), so every existing generic fixture/
# test is unaffected by this package's existence.
#
# `kiwi_manual` (never bare `kiwi`) is a deliberately separate concept
# from the real, live `flight_provider="kiwi_mcp"` integration
# (`app.providers.flights.kiwi_mcp_adapter`) -- see `kiwi_manual.py`'s
# own docstring.

FlightSourceParser = Callable[..., FlightSearchResult]

# Maps source_id -> the actual module object (not a captured function/
# constant) so `get_flight_source_parser`/`get_flight_source_parser_
# version` always resolve `.parse`/`.PARSER_VERSION` fresh at call time
# -- this keeps every module individually monkeypatchable in tests,
# mirroring `app.providers.accommodation.source_parsers`'s identical
# Step 185C convention.
_PARSER_MODULES: dict[str, ModuleType] = {
    "skyscanner": skyscanner,
    "google_flights": google_flights,
    "kiwi_manual": kiwi_manual,
    "generic": generic,
}


def get_flight_source_parser(source_id: str) -> FlightSourceParser:
    """Resolves `source_id` (e.g. `"skyscanner"`, `"kiwi_manual"`,
    `"generic"`) to its source-specific parser function, falling back to
    the generic parser for an unknown/unrecognized `source_id` -- never
    raises, never crashes. Selecting a parser here never changes
    provenance into official-provider data, and never selects (or
    resembles) the real, live `kiwi_mcp` adapter: every parser in this
    package (generic included) produces offers via the same underlying
    `parse_scraped_flight_html`, whose `provider` field always starts
    with `"scraped:"` and whose `scraped_provenance.official_provider`
    is structurally fixed to `False`.
    """
    module = _PARSER_MODULES.get(source_id, generic)
    return module.parse


def get_flight_source_parser_version(source_id: str) -> str:
    """Resolves `source_id` to its parser module's own `PARSER_VERSION`
    string, falling back to the generic parser's version for an unknown/
    unrecognized `source_id`. Used only to keep the flight provider's
    cache key sensitive to per-source parser-logic changes.
    """
    module = _PARSER_MODULES.get(source_id, generic)
    return module.PARSER_VERSION


__all__ = [
    "FlightSourceParser",
    "get_flight_source_parser",
    "get_flight_source_parser_version",
    "generic",
    "google_flights",
    "kiwi_manual",
    "skyscanner",
]
