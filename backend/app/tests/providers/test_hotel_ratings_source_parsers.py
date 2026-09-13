from __future__ import annotations

import ast
import inspect

import pytest

from app.models.scraping import ScrapingSourcePolicy
from app.providers.hotel_ratings import source_parsers as source_parsers_package
from app.providers.hotel_ratings.scraped_parser import HotelRatingsParseStatus
from app.providers.hotel_ratings.source_parsers import (
    generic,
    get_hotel_ratings_source_parser,
    get_hotel_ratings_source_parser_version,
    google_places_ratings,
    tripadvisor,
)

# Step 185E: source-specific hotel-ratings parser tests. Every fixture
# below is a local, hand-written, deliberately synthetic HTML string
# (TEST_ONLY_-prefixed property names) -- never a real website's markup,
# never fetched over a network, and never claimed to match any real
# site's current DOM.

_BRAND_MODULES = {
    "tripadvisor": tripadvisor,
    "google_places_ratings": google_places_ratings,
}


def _policy(source_id: str, **overrides: object) -> ScrapingSourcePolicy:
    fields: dict[str, object] = {
        "source_id": source_id,
        "source_name": f"Test source ({source_id})",
        "base_url": "file://local-test-only-source",
        "enabled": True,
        "approved_for_personal_use": True,
        "allows_reviews": True,
        "rate_limit_seconds": 10,
    }
    fields.update(overrides)
    return ScrapingSourcePolicy(**fields)


def _tagged_rating_html(
    brand: str, property_name: str = "TEST_ONLY_HOTEL", rating: str = "4.5", review_count: str = "88"
) -> str:
    return f"""
<html><body>
<div class="hotel-rating" data-source="{brand}">
  <span class="property-name">{property_name}</span>
  <span class="rating-value">{rating}</span>
  <span class="review-count">{review_count}</span>
</div>
</body></html>
"""


def _minimal_tagged_rating_html(brand: str, property_name: str = "TEST_ONLY_MINIMAL_HOTEL") -> str:
    """Only the property name + a rating value -- review_count must stay
    honestly missing, never invented."""
    return f"""
<html><body>
<div class="hotel-rating" data-source="{brand}">
  <span class="property-name">{property_name}</span>
  <span class="rating-value">3.0</span>
</div>
</body></html>
"""


def _untagged_rating_html(property_name: str = "TEST_ONLY_UNTAGGED_HOTEL") -> str:
    return f"""
<html><body>
<div class="hotel-rating">
  <span class="property-name">{property_name}</span>
  <span class="rating-value">4.0</span>
  <span class="review-count">10</span>
</div>
</body></html>
"""


def _other_brand_rating_html(other_brand: str, property_name: str = "TEST_ONLY_OTHER_BRAND_HOTEL") -> str:
    return f"""
<html><body>
<div class="hotel-rating" data-source="{other_brand}">
  <span class="property-name">{property_name}</span>
  <span class="rating-value">4.0</span>
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
        html=_tagged_rating_html(brand),
        source_policy=_policy(brand),
        source_url="https://example-test-only.test",
    )

    assert result.status == HotelRatingsParseStatus.SUCCESS
    assert len(result.records) == 1
    record = result.records[0]
    assert record.property_name == "TEST_ONLY_HOTEL"
    assert record.rating.value == 4.5
    assert record.rating.review_count == 88
    assert record.rating.data_status.value == "scraped_public_page"
    assert record.rating.provider == f"scraped:{brand}"
    assert record.rating.source_url == "https://example-test-only.test"


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_extracts_only_fields_present_in_fixture(brand: str) -> None:
    """A minimal, brand-tagged rating (name + rating value only) must
    leave review_count honestly missing -- never invented, never
    backfilled."""
    module = _BRAND_MODULES[brand]
    result = module.parse(
        html=_minimal_tagged_rating_html(brand),
        source_policy=_policy(brand),
    )

    assert result.status == HotelRatingsParseStatus.SUCCESS
    record = result.records[0]
    assert record.rating.value == 3.0
    assert record.rating.review_count is None


# ---------------------------------------------------------------------------
# Delegation to generic: an untagged rating is always included (never
# filtered out just because it lacks a data-source attribute).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_includes_untagged_ratings(brand: str) -> None:
    module = _BRAND_MODULES[brand]
    result = module.parse(html=_untagged_rating_html(), source_policy=_policy(brand))

    assert result.status == HotelRatingsParseStatus.SUCCESS
    assert len(result.records) == 1
    assert result.records[0].property_name == "TEST_ONLY_UNTAGGED_HOTEL"


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_excludes_a_rating_explicitly_tagged_for_a_different_brand(
    brand: str,
) -> None:
    other_brand = next(b for b in _BRAND_MODULES if b != brand)
    module = _BRAND_MODULES[brand]

    result = module.parse(html=_other_brand_rating_html(other_brand), source_policy=_policy(brand))

    # No matching record -- an honest "nothing here for this source",
    # never a fabricated rating borrowed from the other brand's card.
    assert result.status == HotelRatingsParseStatus.UNAVAILABLE
    assert result.records == []


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_mixed_html_only_returns_its_own_and_untagged_ratings(
    brand: str,
) -> None:
    other_brand = next(b for b in _BRAND_MODULES if b != brand)
    module = _BRAND_MODULES[brand]
    html = f"""
<html><body>
{_tagged_rating_html(brand, property_name="OWN_HOTEL")}
{_other_brand_rating_html(other_brand, property_name="OTHER_HOTEL")}
{_untagged_rating_html(property_name="UNTAGGED_HOTEL")}
</body></html>
"""

    result = module.parse(html=html, source_policy=_policy(brand))

    assert result.status == HotelRatingsParseStatus.SUCCESS
    names = {record.property_name for record in result.records}
    assert names == {"OWN_HOTEL", "UNTAGGED_HOTEL"}


# ---------------------------------------------------------------------------
# No source-specific parser ever produces official-provider data.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_never_produces_official_provider_data(brand: str) -> None:
    module = _BRAND_MODULES[brand]
    result = module.parse(html=_tagged_rating_html(brand), source_policy=_policy(brand))
    record = result.records[0]
    assert record.rating.data_status.value == "scraped_public_page"
    assert record.rating.provider.startswith("scraped:")


# ---------------------------------------------------------------------------
# Malformed HTML / invalid values never fabricate a fallback rating.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_html_with_no_ratings_returns_unavailable(brand: str) -> None:
    module = _BRAND_MODULES[brand]
    result = module.parse(
        html="<html><body><p>No ratings here.</p></body></html>",
        source_policy=_policy(brand),
    )
    assert result.status == HotelRatingsParseStatus.UNAVAILABLE
    assert result.records == []


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_name_only_card_is_skipped_not_fabricated(brand: str) -> None:
    """A card with a property name but neither a rating value nor a
    review count carries no usable fact -- dropped entirely, never given
    an invented rating."""
    module = _BRAND_MODULES[brand]
    html = f"""
<html><body>
<div class="hotel-rating" data-source="{brand}">
  <span class="property-name">TEST_ONLY_NAME_ONLY_HOTEL</span>
</div>
</body></html>
"""
    result = module.parse(html=html, source_policy=_policy(brand))
    assert result.status == HotelRatingsParseStatus.UNAVAILABLE
    assert result.records == []


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_invalid_rating_value_stays_absent_not_fabricated(
    brand: str,
) -> None:
    """An out-of-range or unparseable rating-value never becomes a
    fabricated fallback rating -- it stays None, and the record is still
    produced only because review_count is present."""
    module = _BRAND_MODULES[brand]
    html = f"""
<html><body>
<div class="hotel-rating" data-source="{brand}">
  <span class="property-name">TEST_ONLY_BAD_RATING_HOTEL</span>
  <span class="rating-value">not-a-number</span>
  <span class="review-count">42</span>
</div>
</body></html>
"""
    result = module.parse(html=html, source_policy=_policy(brand))
    assert result.status == HotelRatingsParseStatus.SUCCESS
    record = result.records[0]
    assert record.rating.value is None
    assert record.rating.review_count == 42


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_out_of_range_rating_value_stays_absent(brand: str) -> None:
    module = _BRAND_MODULES[brand]
    html = f"""
<html><body>
<div class="hotel-rating" data-source="{brand}">
  <span class="property-name">TEST_ONLY_OUT_OF_RANGE_HOTEL</span>
  <span class="rating-value">9.9</span>
  <span class="review-count">5</span>
</div>
</body></html>
"""
    result = module.parse(html=html, source_policy=_policy(brand))
    assert result.status == HotelRatingsParseStatus.SUCCESS
    assert result.records[0].rating.value is None


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_source_specific_parser_refuses_when_source_policy_is_unsafe(brand: str) -> None:
    """An unsafe policy (e.g. requires_login) is refused before any HTML
    is even touched, exactly like the generic parser."""
    module = _BRAND_MODULES[brand]
    unsafe_policy = _policy(brand, requires_login=True, enabled=False)

    result = module.parse(html=_tagged_rating_html(brand), source_policy=unsafe_policy)

    assert result.status == HotelRatingsParseStatus.NOT_CONNECTED
    assert result.records == []


# ---------------------------------------------------------------------------
# Generic parser: no data-source filtering at all -- accepts every rating
# regardless of tag.
# ---------------------------------------------------------------------------


def test_generic_parser_accepts_ratings_regardless_of_data_source_tag() -> None:
    html = f"""
<html><body>
{_tagged_rating_html("tripadvisor", property_name="T_HOTEL")}
{_tagged_rating_html("google_places_ratings", property_name="G_HOTEL")}
{_untagged_rating_html(property_name="U_HOTEL")}
</body></html>
"""
    result = generic.parse(html=html, source_policy=_policy("generic"))

    assert result.status == HotelRatingsParseStatus.SUCCESS
    names = {record.property_name for record in result.records}
    assert names == {"T_HOTEL", "G_HOTEL", "U_HOTEL"}


# ---------------------------------------------------------------------------
# Parser selection helper: unknown source uses generic parser; every
# named brand resolves to its own module.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()))
def test_get_hotel_ratings_source_parser_resolves_named_brands(brand: str) -> None:
    assert get_hotel_ratings_source_parser(brand) is _BRAND_MODULES[brand].parse


def test_get_hotel_ratings_source_parser_resolves_generic() -> None:
    assert get_hotel_ratings_source_parser("generic") is generic.parse


@pytest.mark.parametrize(
    "unknown_source", ["not_a_real_source", "", "TRIPADVISOR", "google_places", "tripadvisor_mcp"]
)
def test_get_hotel_ratings_source_parser_falls_back_to_generic_for_unknown_source(
    unknown_source: str,
) -> None:
    """Note: the legacy config label "google_places" is itself "unknown"
    at this selection layer -- the adapter maps it to
    "google_places_ratings" before calling this helper; this helper
    itself only recognizes canonical registry keys."""
    assert get_hotel_ratings_source_parser(unknown_source) is generic.parse


@pytest.mark.parametrize("brand", list(_BRAND_MODULES.keys()) + ["generic"])
def test_get_hotel_ratings_source_parser_version_matches_module_constant(brand: str) -> None:
    module = _BRAND_MODULES.get(brand, generic)
    assert get_hotel_ratings_source_parser_version(brand) == module.PARSER_VERSION


def test_get_hotel_ratings_source_parser_version_falls_back_to_generic_for_unknown_source() -> None:
    assert get_hotel_ratings_source_parser_version("not_a_real_source") == generic.PARSER_VERSION


def test_unknown_source_parser_selection_never_crashes() -> None:
    for source_id in ["tripadvisor", "nonsense", "", "GENERIC", "google_places"]:
        parser = get_hotel_ratings_source_parser(source_id)
        result = parser(html="<html><body></body></html>", source_policy=_policy(source_id or "generic"))
        assert result.status == HotelRatingsParseStatus.UNAVAILABLE


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
        "kiwi",
        "mcp",
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
        "kiwi",
        "mcp",
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
