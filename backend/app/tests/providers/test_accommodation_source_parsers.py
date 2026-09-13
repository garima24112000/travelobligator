from __future__ import annotations

import ast
import inspect
from datetime import date

import pytest

from app.models.accommodation import AccommodationSearchRequest, AccommodationSearchStatus
from app.models.common import DataStatus
from app.models.scraping import ScrapingSourcePolicy
from app.providers.accommodation import source_parsers as source_parsers_package
from app.providers.accommodation.source_parsers import (
    airbnb,
    booking,
    expedia,
    generic,
    get_accommodation_source_parser,
    get_accommodation_source_parser_version,
    hostelworld,
    hotelbeds,
    vrbo,
)

# Step 185C: source-specific accommodation parser tests. Every fixture
# below is a local, hand-written, deliberately fictional HTML string
# (TEST_ONLY_-prefixed) -- never a real website's markup, never fetched
# over a network, and never claimed to match any real site's current DOM.

_BRAND_MODULES = {
    "booking": booking,
    "expedia": expedia,
    "hotelbeds": hotelbeds,
    "hostelworld": hostelworld,
    "vrbo": vrbo,
    "airbnb": airbnb,
}


def _policy(source_id: str, **overrides: object) -> ScrapingSourcePolicy:
    fields: dict[str, object] = {
        "source_id": source_id,
        "source_name": f"Test source ({source_id})",
        "base_url": "file://local-test-only-source",
        "enabled": True,
        "approved_for_personal_use": True,
        "allows_lodging": True,
        "rate_limit_seconds": 10,
    }
    fields.update(overrides)
    return ScrapingSourcePolicy(**fields)


def _request() -> AccommodationSearchRequest:
    return AccommodationSearchRequest(
        destination="Testville, Testland",
        check_in_date=date(2026, 10, 10),
        check_out_date=date(2026, 10, 14),
        adults=2,
        rooms=1,
    )


def _tagged_card_html(brand: str, property_id: str = "brand-1") -> str:
    return f"""
<html><body>
<div class="property-card" data-property-id="{property_id}" data-source="{brand}">
  <h2 class="property-name">TEST_ONLY_{brand.upper()}_PROPERTY</h2>
  <span class="price" data-currency="USD">150.00</span>
  <span class="availability">available</span>
  <a class="booking-link" href="https://example-test-only-{brand}.test/book/{property_id}">Book</a>
</div>
</body></html>
"""


def _minimal_tagged_card_html(brand: str, property_id: str = "brand-min-1") -> str:
    """Only the required property_id/name -- every other field must stay
    honestly missing, never invented."""
    return f"""
<html><body>
<div class="property-card" data-property-id="{property_id}" data-source="{brand}">
  <h2 class="property-name">TEST_ONLY_{brand.upper()}_MINIMAL</h2>
</div>
</body></html>
"""


def _untagged_card_html(property_id: str = "untagged-1") -> str:
    return f"""
<html><body>
<div class="property-card" data-property-id="{property_id}">
  <h2 class="property-name">TEST_ONLY_UNTAGGED_PROPERTY</h2>
</div>
</body></html>
"""


def _other_brand_card_html(other_brand: str, property_id: str = "other-1") -> str:
    return f"""
<html><body>
<div class="property-card" data-property-id="{property_id}" data-source="{other_brand}">
  <h2 class="property-name">TEST_ONLY_OTHER_BRAND_PROPERTY</h2>
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
        html=_tagged_card_html(brand),
        source_policy=_policy(brand),
        request=_request(),
        source_url="https://example-test-only.test",
    )

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 1
    offer = result.offers[0]
    assert offer.property_name == f"TEST_ONLY_{brand.upper()}_PROPERTY"
    assert offer.nightly_price_amount == 150.00
    assert offer.currency == "USD"
    assert offer.data_status == DataStatus.SCRAPED_PUBLIC_PAGE
    assert offer.scraped_provenance is not None
    assert offer.scraped_provenance.official_provider is False
    assert offer.scraped_provenance.parser_version == module.PARSER_VERSION


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_extracts_only_fields_present_in_fixture(brand: str) -> None:
    """A minimal, brand-tagged card (name/id only) must leave every other
    field honestly missing -- never invented, never backfilled."""
    module = _BRAND_MODULES[brand]
    result = module.parse(
        html=_minimal_tagged_card_html(brand),
        source_policy=_policy(brand),
        request=_request(),
    )

    assert result.status == AccommodationSearchStatus.SUCCESS
    offer = result.offers[0]
    assert offer.nightly_price_amount is None
    assert offer.currency is None
    assert offer.rating is None
    assert offer.address is None
    assert offer.amenities == []
    assert offer.cancellation_policy is None
    assert offer.booking_url is None
    assert offer.availability_status.value == "unknown"


# ---------------------------------------------------------------------------
# Delegation to generic: an untagged card is always included (never
# filtered out just because it lacks a data-source attribute).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_includes_untagged_cards(brand: str) -> None:
    module = _BRAND_MODULES[brand]
    result = module.parse(
        html=_untagged_card_html(),
        source_policy=_policy(brand),
        request=_request(),
    )

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].property_name == "TEST_ONLY_UNTAGGED_PROPERTY"


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_excludes_a_card_explicitly_tagged_for_a_different_brand(
    brand: str,
) -> None:
    other_brand = next(b for b in _BRAND_MODULES if b != brand)
    module = _BRAND_MODULES[brand]

    result = module.parse(
        html=_other_brand_card_html(other_brand),
        source_policy=_policy(brand),
        request=_request(),
    )

    # No matching card -- an honest "nothing here for this source",
    # never a fabricated offer borrowed from the other brand's card.
    assert result.status == AccommodationSearchStatus.UNAVAILABLE
    assert result.offers == []


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_mixed_html_only_returns_its_own_and_untagged_cards(
    brand: str,
) -> None:
    other_brand = next(b for b in _BRAND_MODULES if b != brand)
    module = _BRAND_MODULES[brand]
    html = f"""
<html><body>
{_tagged_card_html(brand, property_id="own-1")}
{_other_brand_card_html(other_brand, property_id="other-1")}
{_untagged_card_html(property_id="untagged-1")}
</body></html>
"""

    result = module.parse(html=html, source_policy=_policy(brand), request=_request())

    assert result.status == AccommodationSearchStatus.SUCCESS
    property_ids = {offer.provider_property_id for offer in result.offers}
    assert property_ids == {"own-1", "untagged-1"}


# ---------------------------------------------------------------------------
# No source-specific parser ever sets official_provider=True -- it is
# structurally impossible (ScrapedDataProvenance.official_provider is
# typed Literal[False]), confirmed directly for every brand.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_never_sets_official_provider_true(brand: str) -> None:
    module = _BRAND_MODULES[brand]
    result = module.parse(
        html=_tagged_card_html(brand),
        source_policy=_policy(brand),
        request=_request(),
    )
    assert result.offers[0].scraped_provenance.official_provider is False


# ---------------------------------------------------------------------------
# Malformed HTML / invalid values never fabricate a fallback offer.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_html_with_no_cards_returns_unavailable(brand: str) -> None:
    module = _BRAND_MODULES[brand]
    result = module.parse(
        html="<html><body><p>No property cards here.</p></body></html>",
        source_policy=_policy(brand),
        request=_request(),
    )
    assert result.status == AccommodationSearchStatus.UNAVAILABLE
    assert result.offers == []


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_card_with_no_name_is_skipped_not_fabricated(brand: str) -> None:
    """A card missing the required property name is dropped entirely --
    never given an invented name."""
    module = _BRAND_MODULES[brand]
    html = f"""
<html><body>
<div class="property-card" data-property-id="no-name-1" data-source="{brand}">
  <span class="price" data-currency="USD">99.00</span>
</div>
</body></html>
"""
    result = module.parse(html=html, source_policy=_policy(brand), request=_request())
    assert result.status == AccommodationSearchStatus.UNAVAILABLE
    assert result.offers == []


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_refuses_when_source_policy_is_unsafe(brand: str) -> None:
    """An unsafe policy (e.g. requires_login) is refused before any HTML
    is even touched, exactly like the generic parser."""
    module = _BRAND_MODULES[brand]
    unsafe_policy = _policy(brand, requires_login=True, enabled=False)

    result = module.parse(
        html=_tagged_card_html(brand),
        source_policy=unsafe_policy,
        request=_request(),
    )

    assert result.status == AccommodationSearchStatus.NOT_CONNECTED
    assert result.offers == []


# ---------------------------------------------------------------------------
# Generic parser: no data-source filtering at all -- accepts every card
# regardless of tag, exactly like the pre-185C behavior.
# ---------------------------------------------------------------------------


def test_generic_parser_accepts_cards_regardless_of_data_source_tag() -> None:
    html = f"""
<html><body>
{_tagged_card_html("booking", property_id="b-1")}
{_tagged_card_html("expedia", property_id="e-1")}
{_untagged_card_html(property_id="u-1")}
</body></html>
"""
    result = generic.parse(
        html=html,
        source_policy=_policy("generic"),
        request=_request(),
    )

    assert result.status == AccommodationSearchStatus.SUCCESS
    property_ids = {offer.provider_property_id for offer in result.offers}
    assert property_ids == {"b-1", "e-1", "u-1"}


# ---------------------------------------------------------------------------
# Parser selection helper: unknown source uses generic parser; every
# named brand resolves to its own module.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_get_accommodation_source_parser_resolves_named_brands(brand: str) -> None:
    assert get_accommodation_source_parser(brand) is _BRAND_MODULES[brand].parse


def test_get_accommodation_source_parser_resolves_generic() -> None:
    assert get_accommodation_source_parser("generic") is generic.parse


@pytest.mark.parametrize("unknown_source", ["not_a_real_source", "", "BOOKING", "tripadvisor"])
def test_get_accommodation_source_parser_falls_back_to_generic_for_unknown_source(
    unknown_source: str,
) -> None:
    assert get_accommodation_source_parser(unknown_source) is generic.parse


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()) + ["generic"])
def test_get_accommodation_source_parser_version_matches_module_constant(brand: str) -> None:
    module = _BRAND_MODULES.get(brand, generic)
    assert get_accommodation_source_parser_version(brand) == module.PARSER_VERSION


def test_get_accommodation_source_parser_version_falls_back_to_generic_for_unknown_source() -> None:
    assert get_accommodation_source_parser_version("not_a_real_source") == generic.PARSER_VERSION


def test_unknown_source_parser_selection_never_crashes() -> None:
    # Exercises a range of inputs without raising -- purely a lookup
    # helper, never validation logic that could reject bad input.
    for source_id in ["booking", "nonsense", "", "GENERIC", "tripadvisor", "kiwi_manual"]:
        parser = get_accommodation_source_parser(source_id)
        result = parser(
            html="<html><body></body></html>",
            source_policy=_policy(source_id or "generic"),
            request=_request(),
        )
        assert result.status == AccommodationSearchStatus.UNAVAILABLE


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
        # "official-provider data" (hyphenated) is the actual negation
        # phrasing this codebase uses everywhere ("not official-provider
        # data") -- only the un-hyphenated, standalone claim is banned.
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

    disallowed_substrings = ("httpx", "requests", "selenium", "playwright", "bs4", "beautifulsoup")

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
