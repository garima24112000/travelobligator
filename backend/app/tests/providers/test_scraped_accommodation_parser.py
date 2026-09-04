from __future__ import annotations

import ast
import inspect
from datetime import date

import pytest

from app.models.accommodation import (
    AccommodationAvailabilityStatus,
    AccommodationSearchRequest,
    AccommodationSearchStatus,
)
from app.models.common import DataStatus
from app.models.scraping import ScrapingSourcePolicy, ScrapingSourceType
from app.providers.accommodation.scraped_parser import parse_scraped_accommodation_html

# Step 168B: static HTML parser framework tests. Every fixture below is a
# local, hand-written HTML string -- never a real website's markup, never
# fetched over a network. Property names are deliberately fictional and
# prefixed TEST_ONLY_ so nobody mistakes them for a real lodging
# recommendation.

_TEST_SOURCE_ID = "example_test_only_travel_blog"
_TEST_SOURCE_NAME = "Example Test-Only Travel Blog"


def _policy(**overrides: object) -> ScrapingSourcePolicy:
    fields: dict[str, object] = {
        "source_id": _TEST_SOURCE_ID,
        "source_name": _TEST_SOURCE_NAME,
        "base_url": "https://example-test-only-travel-blog.test",
        "approved_for_personal_use": True,
        "allows_lodging": True,
        "rate_limit_seconds": 10,
    }
    fields.update(overrides)
    return ScrapingSourcePolicy(**fields)


def _enabled_policy(**overrides: object) -> ScrapingSourcePolicy:
    fields: dict[str, object] = {"enabled": True}
    fields.update(overrides)
    return _policy(**fields)


def _request() -> AccommodationSearchRequest:
    return AccommodationSearchRequest(
        destination="Testville, Testland",
        check_in_date=date(2026, 10, 10),
        check_out_date=date(2026, 10, 14),
        adults=2,
        rooms=1,
    )


# One fully-populated card (Alpha) and one minimal card with only the
# required property_id/name (Beta) -- Beta proves every optional field
# stays honestly missing rather than guessed.
_VALID_HTML_TWO_PROPERTIES = """
<html><body>
<div class="property-card" data-property-id="alpha-1">
  <h2 class="property-name">TEST_ONLY_SCRAPED_PROPERTY_ALPHA</h2>
  <span class="price" data-currency="USD">120.50</span>
  <span class="rating">4.2</span>
  <span class="availability">available</span>
  <span class="address">1 Test Only Way, Testville</span>
  <span class="amenity">WiFi</span>
  <span class="amenity">Breakfast</span>
  <a class="booking-link" href="https://example-test-only-travel-blog.test/book/alpha-1">Book</a>
</div>
<div class="property-card" data-property-id="beta-1">
  <h2 class="property-name">TEST_ONLY_SCRAPED_PROPERTY_BETA</h2>
</div>
</body></html>
"""

_HTML_WITH_NO_PROPERTY_CARDS = """
<html><body>
<p>TEST_ONLY: no property cards on this fixture page.</p>
</body></html>
"""

_HTML_WITH_UNIDENTIFIABLE_CARD = """
<html><body>
<div class="property-card">
  <span class="price" data-currency="USD">99</span>
</div>
</body></html>
"""

_HTML_WITH_INVALID_CURRENCY_CODE = """
<html><body>
<div class="property-card" data-property-id="gamma-1">
  <h2 class="property-name">TEST_ONLY_SCRAPED_PROPERTY_GAMMA</h2>
  <span class="price" data-currency="US">100</span>
</div>
</body></html>
"""


def _alpha_and_beta_offers(result):
    offers_by_id = {offer.provider_property_id: offer for offer in result.offers}
    return offers_by_id["alpha-1"], offers_by_id["beta-1"]


# ---------------------------------------------------------------------------
# 1. Parser refuses disabled source policy.
# ---------------------------------------------------------------------------


def test_parser_refuses_disabled_source_policy() -> None:
    result = parse_scraped_accommodation_html(
        _VALID_HTML_TWO_PROPERTIES, _policy(enabled=False), _request()
    )
    assert result.status == AccommodationSearchStatus.NOT_CONNECTED
    assert result.offers == []


# ---------------------------------------------------------------------------
# 2. Parser refuses unapproved source policy.
# ---------------------------------------------------------------------------


def test_parser_refuses_unapproved_source_policy() -> None:
    policy = _policy(enabled=False, approved_for_personal_use=False)
    assert policy.is_unsafe is True

    result = parse_scraped_accommodation_html(_VALID_HTML_TWO_PROPERTIES, policy, _request())

    assert result.status == AccommodationSearchStatus.NOT_CONNECTED
    assert result.offers == []


# ---------------------------------------------------------------------------
# 3. Parser refuses source with allows_lodging=False.
# ---------------------------------------------------------------------------


def test_parser_refuses_source_that_does_not_allow_lodging() -> None:
    policy = _enabled_policy(allows_lodging=False)
    assert policy.is_unsafe is False

    result = parse_scraped_accommodation_html(_VALID_HTML_TWO_PROPERTIES, policy, _request())

    assert result.status == AccommodationSearchStatus.NOT_CONNECTED
    assert result.offers == []


# ---------------------------------------------------------------------------
# 4. Parser refuses unsafe login/paywall/captcha source, even disabled.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unsafe_field", ["requires_login", "paywalled", "captcha_expected"]
)
def test_parser_refuses_unsafe_source_even_when_disabled(unsafe_field: str) -> None:
    policy = _policy(enabled=False, **{unsafe_field: True})
    assert policy.is_unsafe is True

    result = parse_scraped_accommodation_html(_VALID_HTML_TWO_PROPERTIES, policy, _request())

    assert result.status == AccommodationSearchStatus.NOT_CONNECTED
    assert result.offers == []


# ---------------------------------------------------------------------------
# 5. Parser parses a valid static HTML fixture into
# AccommodationSearchResult status=success.
# ---------------------------------------------------------------------------


def test_parser_parses_valid_fixture_into_success_result() -> None:
    result = parse_scraped_accommodation_html(
        _VALID_HTML_TWO_PROPERTIES,
        _enabled_policy(),
        _request(),
        source_url="https://example-test-only-travel-blog.test/testville",
        parser_version="test-parser-v1",
    )

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 2
    assert {offer.provider_property_id for offer in result.offers} == {"alpha-1", "beta-1"}


# ---------------------------------------------------------------------------
# 6. Parsed offer includes only fields present in the HTML.
# ---------------------------------------------------------------------------


def test_alpha_offer_includes_only_fields_present_in_html() -> None:
    result = parse_scraped_accommodation_html(
        _VALID_HTML_TWO_PROPERTIES, _enabled_policy(), _request()
    )
    alpha, _beta = _alpha_and_beta_offers(result)

    assert alpha.property_name == "TEST_ONLY_SCRAPED_PROPERTY_ALPHA"
    assert alpha.nightly_price_amount == pytest.approx(120.50)
    assert alpha.currency == "USD"
    assert alpha.rating == pytest.approx(4.2)
    assert alpha.availability_status == AccommodationAvailabilityStatus.AVAILABLE
    assert alpha.address == "1 Test Only Way, Testville"
    assert alpha.amenities == ["WiFi", "Breakfast"]
    assert (
        alpha.booking_url
        == "https://example-test-only-travel-blog.test/book/alpha-1"
    )
    # Never present in the HTML -- stays honestly None.
    assert alpha.cancellation_policy is None
    assert alpha.total_price_amount is None


# ---------------------------------------------------------------------------
# 7/8/9/10. Missing price/rating/availability/booking_url remain
# None/unknown, never guessed.
# ---------------------------------------------------------------------------


def test_beta_offer_missing_price_stays_none() -> None:
    result = parse_scraped_accommodation_html(
        _VALID_HTML_TWO_PROPERTIES, _enabled_policy(), _request()
    )
    _alpha, beta = _alpha_and_beta_offers(result)
    assert beta.nightly_price_amount is None
    assert beta.total_price_amount is None
    assert beta.currency is None


def test_beta_offer_missing_rating_stays_none() -> None:
    result = parse_scraped_accommodation_html(
        _VALID_HTML_TWO_PROPERTIES, _enabled_policy(), _request()
    )
    _alpha, beta = _alpha_and_beta_offers(result)
    assert beta.rating is None


def test_beta_offer_missing_availability_stays_unknown() -> None:
    result = parse_scraped_accommodation_html(
        _VALID_HTML_TWO_PROPERTIES, _enabled_policy(), _request()
    )
    _alpha, beta = _alpha_and_beta_offers(result)
    assert beta.availability_status == AccommodationAvailabilityStatus.UNKNOWN


def test_beta_offer_missing_booking_url_stays_none() -> None:
    result = parse_scraped_accommodation_html(
        _VALID_HTML_TWO_PROPERTIES, _enabled_policy(), _request()
    )
    _alpha, beta = _alpha_and_beta_offers(result)
    assert beta.booking_url is None


def test_beta_offer_missing_amenities_and_address_stay_empty() -> None:
    result = parse_scraped_accommodation_html(
        _VALID_HTML_TWO_PROPERTIES, _enabled_policy(), _request()
    )
    _alpha, beta = _alpha_and_beta_offers(result)
    assert beta.amenities == []
    assert beta.address is None
    assert beta.cancellation_policy is None


# ---------------------------------------------------------------------------
# 11/12. Parsed data has provenance scraped_public_page and
# official_provider=False.
# ---------------------------------------------------------------------------


def test_parsed_offer_has_scraped_public_page_provenance() -> None:
    result = parse_scraped_accommodation_html(
        _VALID_HTML_TWO_PROPERTIES,
        _enabled_policy(),
        _request(),
        source_url="https://example-test-only-travel-blog.test/testville",
        parser_version="test-parser-v1",
    )
    alpha, _beta = _alpha_and_beta_offers(result)

    assert alpha.data_status == DataStatus.SCRAPED_PUBLIC_PAGE
    assert alpha.scraped_provenance is not None
    assert alpha.scraped_provenance.source_type == ScrapingSourceType.SCRAPED_PUBLIC_PAGE
    assert alpha.scraped_provenance.provenance == ScrapingSourceType.SCRAPED_PUBLIC_PAGE
    assert alpha.scraped_provenance.source_id == _TEST_SOURCE_ID
    assert alpha.scraped_provenance.parser_version == "test-parser-v1"
    assert (
        alpha.scraped_provenance.source_url
        == "https://example-test-only-travel-blog.test/testville"
    )


def test_parsed_offer_official_provider_is_false() -> None:
    result = parse_scraped_accommodation_html(
        _VALID_HTML_TWO_PROPERTIES, _enabled_policy(), _request()
    )
    alpha, beta = _alpha_and_beta_offers(result)
    assert alpha.scraped_provenance.official_provider is False
    assert beta.scraped_provenance.official_provider is False


# ---------------------------------------------------------------------------
# 13. Parser returns unavailable with empty offers when no valid property
# cards exist.
# ---------------------------------------------------------------------------


def test_parser_returns_unavailable_when_no_property_cards_present() -> None:
    result = parse_scraped_accommodation_html(
        _HTML_WITH_NO_PROPERTY_CARDS, _enabled_policy(), _request()
    )
    assert result.status == AccommodationSearchStatus.UNAVAILABLE
    assert result.offers == []


def test_parser_returns_unavailable_when_card_missing_id_and_name() -> None:
    """A card with no property_id/name can't be safely identified, so it
    is skipped entirely rather than given a fabricated id/name."""
    result = parse_scraped_accommodation_html(
        _HTML_WITH_UNIDENTIFIABLE_CARD, _enabled_policy(), _request()
    )
    assert result.status == AccommodationSearchStatus.UNAVAILABLE
    assert result.offers == []


# ---------------------------------------------------------------------------
# 14. Parser returns failed safely on malformed/unparseable input.
# ---------------------------------------------------------------------------


def test_parser_returns_failed_on_invalid_currency_code() -> None:
    """An invalid (non-3-letter) currency code fails `AccommodationOffer`'s
    own validation -- the parser catches this instead of crashing, and
    never falls back to a guessed/placeholder currency."""
    result = parse_scraped_accommodation_html(
        _HTML_WITH_INVALID_CURRENCY_CODE, _enabled_policy(), _request()
    )
    assert result.status == AccommodationSearchStatus.FAILED
    assert result.offers == []
    assert result.message is not None
    # The safe message never leaks a raw exception/traceback string.
    assert "Traceback" not in result.message
    assert "ValidationError" not in result.message


# ---------------------------------------------------------------------------
# 15. Parser does not call network.
# ---------------------------------------------------------------------------


def test_parser_makes_no_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("parse_scraped_accommodation_html must not open a real httpx.Client")

    monkeypatch.setattr(httpx, "Client", _fail_if_called)

    result = parse_scraped_accommodation_html(
        _VALID_HTML_TWO_PROPERTIES, _enabled_policy(), _request()
    )

    assert result.status == AccommodationSearchStatus.SUCCESS


# ---------------------------------------------------------------------------
# 16. Parser does not import requests/httpx/browser automation.
# ---------------------------------------------------------------------------


def test_parser_module_has_no_disallowed_imports() -> None:
    import app.providers.accommodation.scraped_parser as scraped_parser_module

    source = inspect.getsource(scraped_parser_module)
    tree = ast.parse(source)

    disallowed_substrings = (
        "langgraph",
        "langsmith",
        "httpx",
        "requests",
        "bs4",
        "beautifulsoup",
        "selenium",
        "playwright",
        "groq",
        "anthropic",
        "openai",
        "gemini",
        "google.generativeai",
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


def test_provider_gateway_does_not_reference_scraped_parser() -> None:
    import app.providers.gateway as gateway_module

    source = inspect.getsource(gateway_module)
    assert "scraped_parser" not in source
    assert "parse_scraped_accommodation_html" not in source


def test_planning_orchestrator_does_not_reference_scraped_parser() -> None:
    import app.services.planning_orchestrator as orchestrator_module

    source = inspect.getsource(orchestrator_module)
    assert "scraped_parser" not in source
    assert "parse_scraped_accommodation_html" not in source


# ---------------------------------------------------------------------------
# Never fabricates a property that isn't in the fixture.
# ---------------------------------------------------------------------------


def test_parser_never_invents_a_property_not_present_in_html() -> None:
    result = parse_scraped_accommodation_html(
        _VALID_HTML_TWO_PROPERTIES, _enabled_policy(), _request()
    )
    property_names = {offer.property_name for offer in result.offers}
    assert property_names == {
        "TEST_ONLY_SCRAPED_PROPERTY_ALPHA",
        "TEST_ONLY_SCRAPED_PROPERTY_BETA",
    }
