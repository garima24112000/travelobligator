from __future__ import annotations

import ast
import inspect
from datetime import date

import pytest

from app.models.common import DataStatus
from app.models.flight import FlightSearchRequest, FlightSearchStatus
from app.models.scraping import ScrapingSourcePolicy
from app.providers.flights import source_parsers as source_parsers_package
from app.providers.flights.source_parsers import (
    generic,
    get_flight_source_parser,
    get_flight_source_parser_version,
    google_flights,
    kiwi_manual,
    skyscanner,
)

# Step 185D: source-specific flight parser tests. Every fixture below is
# a local, hand-written, deliberately fictional HTML string
# (TEST_ONLY_-prefixed airlines/flight numbers, TST/DMO airport codes) --
# never a real website's markup, never fetched over a network, and never
# claimed to match any real site's current DOM.

_BRAND_MODULES = {
    "skyscanner": skyscanner,
    "google_flights": google_flights,
    "kiwi_manual": kiwi_manual,
}


def _policy(source_id: str, **overrides: object) -> ScrapingSourcePolicy:
    fields: dict[str, object] = {
        "source_id": source_id,
        "source_name": f"Test source ({source_id})",
        "base_url": "file://local-test-only-source",
        "enabled": True,
        "approved_for_personal_use": True,
        "allows_flights": True,
        "rate_limit_seconds": 10,
    }
    fields.update(overrides)
    return ScrapingSourcePolicy(**fields)


def _request() -> FlightSearchRequest:
    return FlightSearchRequest(
        origin="JFK",
        destination="LIS",
        departure_date=date(2026, 10, 10),
    )


def _tagged_offer_html(brand: str, offer_id: str = "brand-1") -> str:
    return f"""
<html><body>
<div class="flight-offer" data-offer-id="{offer_id}" data-source="{brand}">
  <div class="outbound-segment">
    <span class="origin-airport">TST</span>
    <span class="destination-airport">DMO</span>
    <span class="carrier-name">TEST_ONLY_{brand.upper()}_AIRLINE</span>
    <span class="flight-number">TEST_ONLY_{brand.upper()}_123</span>
  </div>
  <span class="total-price" data-currency="USD">300.00</span>
  <a class="booking-link" href="https://example-test-only-{brand}.test/book/{offer_id}">Book</a>
</div>
</body></html>
"""


def _minimal_tagged_offer_html(brand: str, offer_id: str = "brand-min-1") -> str:
    """Only the required offer_id + a bare outbound segment -- every
    other field must stay honestly missing, never invented."""
    return f"""
<html><body>
<div class="flight-offer" data-offer-id="{offer_id}" data-source="{brand}">
  <div class="outbound-segment">
    <span class="origin-airport">TST</span>
    <span class="destination-airport">DMO</span>
  </div>
</div>
</body></html>
"""


def _untagged_offer_html(offer_id: str = "untagged-1") -> str:
    return f"""
<html><body>
<div class="flight-offer" data-offer-id="{offer_id}">
  <div class="outbound-segment">
    <span class="origin-airport">TST</span>
    <span class="destination-airport">DMO</span>
  </div>
</div>
</body></html>
"""


def _other_brand_offer_html(other_brand: str, offer_id: str = "other-1") -> str:
    return f"""
<html><body>
<div class="flight-offer" data-offer-id="{offer_id}" data-source="{other_brand}">
  <div class="outbound-segment">
    <span class="origin-airport">TST</span>
    <span class="destination-airport">DMO</span>
  </div>
</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# Happy path: each source-specific parser extracts only the fields
# present in its own synthetic, brand-tagged fixture.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_happy_path(brand: str) -> None:
    module = _BRAND_MODULES[brand]
    result = module.parse(
        html=_tagged_offer_html(brand),
        source_policy=_policy(brand),
        request=_request(),
        source_url="https://example-test-only.test",
    )

    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 1
    offer = result.offers[0]
    assert offer.outbound_segments[0].carrier_name == f"TEST_ONLY_{brand.upper()}_AIRLINE"
    assert offer.outbound_segments[0].flight_number == f"TEST_ONLY_{brand.upper()}_123"
    assert offer.total_price_amount == 300.00
    assert offer.currency == "USD"
    assert offer.data_status == DataStatus.SCRAPED_PUBLIC_PAGE
    assert offer.scraped_provenance is not None
    assert offer.scraped_provenance.official_provider is False
    assert offer.scraped_provenance.parser_version == module.PARSER_VERSION
    # provider always starts with "scraped:" -- never "kiwi_mcp", even
    # for the kiwi_manual parser.
    assert offer.provider.startswith("scraped:")
    assert offer.provider != "kiwi_mcp"


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_extracts_only_fields_present_in_fixture(brand: str) -> None:
    """A minimal, brand-tagged offer (id + bare outbound segment) must
    leave every other field honestly missing -- never invented, never
    backfilled."""
    module = _BRAND_MODULES[brand]
    result = module.parse(
        html=_minimal_tagged_offer_html(brand),
        source_policy=_policy(brand),
        request=_request(),
    )

    assert result.status == FlightSearchStatus.SUCCESS
    offer = result.offers[0]
    assert offer.total_price_amount is None
    assert offer.currency is None
    assert offer.booking_url is None
    assert offer.availability_status is None
    assert offer.baggage_policy is None
    assert offer.cancellation_policy is None
    assert offer.return_segments == []
    segment = offer.outbound_segments[0]
    assert segment.carrier_name is None
    assert segment.carrier_code is None
    assert segment.flight_number is None
    assert segment.departure_time is None
    assert segment.arrival_time is None
    assert segment.duration_minutes is None


# ---------------------------------------------------------------------------
# Delegation to generic: an untagged offer is always included (never
# filtered out just because it lacks a data-source attribute).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_includes_untagged_offers(brand: str) -> None:
    module = _BRAND_MODULES[brand]
    result = module.parse(
        html=_untagged_offer_html(),
        source_policy=_policy(brand),
        request=_request(),
    )

    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].offer_id == "untagged-1"


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_excludes_an_offer_explicitly_tagged_for_a_different_brand(
    brand: str,
) -> None:
    other_brand = next(b for b in _BRAND_MODULES if b != brand)
    module = _BRAND_MODULES[brand]

    result = module.parse(
        html=_other_brand_offer_html(other_brand),
        source_policy=_policy(brand),
        request=_request(),
    )

    # No matching offer -- an honest "nothing here for this source",
    # never a fabricated offer borrowed from the other brand's card.
    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_mixed_html_only_returns_its_own_and_untagged_offers(
    brand: str,
) -> None:
    other_brand = next(b for b in _BRAND_MODULES if b != brand)
    module = _BRAND_MODULES[brand]
    html = f"""
<html><body>
{_tagged_offer_html(brand, offer_id="own-1")}
{_other_brand_offer_html(other_brand, offer_id="other-1")}
{_untagged_offer_html(offer_id="untagged-1")}
</body></html>
"""

    result = module.parse(html=html, source_policy=_policy(brand), request=_request())

    assert result.status == FlightSearchStatus.SUCCESS
    offer_ids = {offer.offer_id for offer in result.offers}
    assert offer_ids == {"own-1", "untagged-1"}


# ---------------------------------------------------------------------------
# No source-specific parser ever sets official_provider=True, and
# kiwi_manual never produces provider="kiwi_mcp".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_never_sets_official_provider_true(brand: str) -> None:
    module = _BRAND_MODULES[brand]
    result = module.parse(
        html=_tagged_offer_html(brand),
        source_policy=_policy(brand),
        request=_request(),
    )
    assert result.offers[0].scraped_provenance.official_provider is False


def test_kiwi_manual_offer_provider_never_equals_kiwi_mcp() -> None:
    result = kiwi_manual.parse(
        html=_tagged_offer_html("kiwi_manual"),
        source_policy=_policy("kiwi_manual"),
        request=_request(),
    )
    assert result.offers[0].provider != "kiwi_mcp"
    assert result.offers[0].provider.startswith("scraped:")


def test_kiwi_manual_module_never_imports_kiwi_mcp_modules() -> None:
    source = inspect.getsource(kiwi_manual)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        assert "kiwi_mcp" not in name.lower()


# ---------------------------------------------------------------------------
# Malformed HTML / invalid values never fabricate a fallback offer.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_html_with_no_offers_returns_unavailable(brand: str) -> None:
    module = _BRAND_MODULES[brand]
    result = module.parse(
        html="<html><body><p>No flight offers here.</p></body></html>",
        source_policy=_policy(brand),
        request=_request(),
    )
    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_offer_with_no_outbound_segment_is_skipped_not_fabricated(
    brand: str,
) -> None:
    """A card missing a required outbound segment is dropped entirely --
    never given an invented segment."""
    module = _BRAND_MODULES[brand]
    html = f"""
<html><body>
<div class="flight-offer" data-offer-id="no-segment-1" data-source="{brand}">
  <span class="total-price" data-currency="USD">99.00</span>
</div>
</body></html>
"""
    result = module.parse(html=html, source_policy=_policy(brand), request=_request())
    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_refuses_when_source_policy_is_unsafe(brand: str) -> None:
    """An unsafe policy (e.g. requires_login) is refused before any HTML
    is even touched, exactly like the generic parser."""
    module = _BRAND_MODULES[brand]
    unsafe_policy = _policy(brand, requires_login=True, enabled=False)

    result = module.parse(
        html=_tagged_offer_html(brand),
        source_policy=unsafe_policy,
        request=_request(),
    )

    assert result.status == FlightSearchStatus.NOT_CONNECTED
    assert result.offers == []


# ---------------------------------------------------------------------------
# Generic parser: no data-source filtering at all -- accepts every offer
# regardless of tag, exactly like the pre-185D behavior.
# ---------------------------------------------------------------------------


def test_generic_parser_accepts_offers_regardless_of_data_source_tag() -> None:
    html = f"""
<html><body>
{_tagged_offer_html("skyscanner", offer_id="s-1")}
{_tagged_offer_html("google_flights", offer_id="g-1")}
{_untagged_offer_html(offer_id="u-1")}
</body></html>
"""
    result = generic.parse(
        html=html,
        source_policy=_policy("generic"),
        request=_request(),
    )

    assert result.status == FlightSearchStatus.SUCCESS
    offer_ids = {offer.offer_id for offer in result.offers}
    assert offer_ids == {"s-1", "g-1", "u-1"}


# ---------------------------------------------------------------------------
# Parser selection helper: unknown source uses generic parser; every
# named brand resolves to its own module.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_get_flight_source_parser_resolves_named_brands(brand: str) -> None:
    assert get_flight_source_parser(brand) is _BRAND_MODULES[brand].parse


def test_get_flight_source_parser_resolves_generic() -> None:
    assert get_flight_source_parser("generic") is generic.parse


@pytest.mark.parametrize("unknown_source", ["not_a_real_source", "", "SKYSCANNER", "kiwi", "kiwi_mcp"])
def test_get_flight_source_parser_falls_back_to_generic_for_unknown_source(
    unknown_source: str,
) -> None:
    """Note: bare "kiwi" (the config label) is itself "unknown" at this
    selection layer -- the adapter maps "kiwi" -> "kiwi_manual" before
    calling this helper; this helper itself only recognizes canonical
    keys."""
    assert get_flight_source_parser(unknown_source) is generic.parse


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()) + ["generic"])
def test_get_flight_source_parser_version_matches_module_constant(brand: str) -> None:
    module = _BRAND_MODULES.get(brand, generic)
    assert get_flight_source_parser_version(brand) == module.PARSER_VERSION


def test_get_flight_source_parser_version_falls_back_to_generic_for_unknown_source() -> None:
    assert get_flight_source_parser_version("not_a_real_source") == generic.PARSER_VERSION


def test_unknown_source_parser_selection_never_crashes() -> None:
    for source_id in ["skyscanner", "nonsense", "", "GENERIC", "kiwi", "kiwi_mcp"]:
        parser = get_flight_source_parser(source_id)
        result = parser(
            html="<html><body></body></html>",
            source_policy=_policy(source_id or "generic"),
            request=_request(),
        )
        assert result.status == FlightSearchStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# Safety: no source parser module's own source code (docstrings/comments
# included) contains language implying a booking confirmation, official-
# provider status, a guarantee, a verified review, "top rated"/"best
# hotels" framing, or a production-readiness claim.
# ---------------------------------------------------------------------------

_BANNED_PHRASES = (
    "confirmed booking",
    "booking confirmation",
    "official provider data",
    "guaranteed",
    "verified review",
    "top rated",
    "best hotels",
    "production-ready",
    "bypass captcha",
    "bypass paywall",
    "bypass login",
)

_ALL_PARSER_MODULES = list(_BRAND_MODULES.values()) + [generic]


@pytest.mark.parametrize("module", _ALL_PARSER_MODULES, ids=lambda m: m.__name__)
def test_source_parser_module_source_has_no_banned_overclaiming_phrases(module: object) -> None:
    source_lower = inspect.getsource(module).lower()
    for phrase in _BANNED_PHRASES:
        if phrase == "official provider data" and "official-provider data" in source_lower:
            continue
        assert phrase not in source_lower, f"banned phrase {phrase!r} found in {module.__name__}"


def test_source_parsers_package_has_no_disallowed_imports() -> None:
    source = inspect.getsource(source_parsers_package)
    tree = ast.parse(source)

    disallowed_substrings = (
        "httpx",
        "requests",
        "selenium",
        "playwright",
        "bs4",
        "beautifulsoup",
        "kiwi_mcp",
    )

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"disallowed import found: {name}"


@pytest.mark.parametrize("module", _ALL_PARSER_MODULES, ids=lambda m: m.__name__)
def test_each_source_parser_module_has_no_disallowed_imports(module: object) -> None:
    source = inspect.getsource(module)
    tree = ast.parse(source)

    disallowed_substrings = (
        "httpx",
        "requests",
        "selenium",
        "playwright",
        "bs4",
        "beautifulsoup",
        "kiwi_mcp",
    )

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"disallowed import found: {name}"
