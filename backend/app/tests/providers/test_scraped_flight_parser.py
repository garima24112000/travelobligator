from __future__ import annotations

import ast
import inspect
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models.common import DataStatus
from app.models.flight import FlightSearchRequest, FlightSearchStatus
from app.models.scraping import ScrapingSourcePolicy, ScrapingSourceType
from app.providers.flights.scraped_parser import parse_scraped_flight_html

# Step 169C: static HTML parser framework tests for scraped flight data.
# Every fixture below is a local, hand-written HTML string -- never a
# real website's markup, never fetched over a network. Airlines, flight
# numbers, and airport codes are deliberately fictional and prefixed/
# marked TEST_ONLY/TST/DMO so nobody mistakes them for a real flight
# recommendation.

_TEST_SOURCE_ID = "example_test_only_flight_search_page"
_TEST_SOURCE_NAME = "Example Test-Only Flight Search Page"
_TEST_BASE_URL = "https://example-test-only-flight-search-page.test"


def _policy(**overrides: object) -> ScrapingSourcePolicy:
    fields: dict[str, object] = {
        "source_id": _TEST_SOURCE_ID,
        "source_name": _TEST_SOURCE_NAME,
        "base_url": _TEST_BASE_URL,
        "approved_for_personal_use": True,
        "allows_flights": True,
        "rate_limit_seconds": 10,
    }
    fields.update(overrides)
    return ScrapingSourcePolicy(**fields)


def _enabled_policy(**overrides: object) -> ScrapingSourcePolicy:
    fields: dict[str, object] = {"enabled": True}
    fields.update(overrides)
    return _policy(**fields)


def _request(**overrides: object) -> FlightSearchRequest:
    fields: dict[str, object] = {
        "origin": "JFK",
        "destination": "LIS",
        "departure_date": date(2026, 10, 10),
    }
    fields.update(overrides)
    return FlightSearchRequest(**fields)


# One fully-populated one-way offer (Alpha) and one minimal offer with
# only offer_id + a bare outbound segment (Beta) -- Beta proves every
# optional field stays honestly missing rather than guessed.
_VALID_HTML_ONE_WAY = f"""
<html><body>
<div class="flight-offer" data-offer-id="alpha-1">
  <div class="outbound-segment">
    <span class="origin-airport">TST</span>
    <span class="destination-airport">DMO</span>
    <span class="departure-time">2026-10-10T20:30:00+00:00</span>
    <span class="arrival-time">2026-10-11T08:45:00+00:00</span>
    <span class="carrier-name">TEST_ONLY_AIRLINE_ALPHA</span>
    <span class="carrier-code">TA</span>
    <span class="flight-number">TEST_ONLY_FLIGHT_123</span>
    <span class="duration-minutes">435</span>
  </div>
  <span class="total-price" data-currency="USD">452.10</span>
  <span class="availability-status">available</span>
  <span class="baggage-policy">1 checked bag included</span>
  <span class="cancellation-policy">Non-refundable</span>
  <a class="booking-link" href="{_TEST_BASE_URL}/book/alpha-1">Book</a>
</div>
<div class="flight-offer" data-offer-id="beta-1">
  <div class="outbound-segment">
    <span class="origin-airport">TST</span>
    <span class="destination-airport">DMO</span>
  </div>
</div>
</body></html>
"""

_VALID_HTML_ROUND_TRIP = """
<html><body>
<div class="flight-offer" data-offer-id="gamma-1">
  <div class="outbound-segment">
    <span class="origin-airport">TST</span>
    <span class="destination-airport">DMO</span>
    <span class="carrier-name">TEST_ONLY_AIRLINE_ALPHA</span>
    <span class="flight-number">TEST_ONLY_FLIGHT_123</span>
  </div>
  <div class="return-segment">
    <span class="origin-airport">DMO</span>
    <span class="destination-airport">TST</span>
    <span class="carrier-name">TEST_ONLY_AIRLINE_ALPHA</span>
    <span class="flight-number">TEST_ONLY_FLIGHT_456</span>
  </div>
  <span class="currency">EUR</span>
  <span class="total-price">610</span>
</div>
</body></html>
"""

_HTML_WITH_NO_FLIGHT_OFFERS = """
<html><body>
<p>TEST_ONLY: no flight offers on this fixture page.</p>
</body></html>
"""

_HTML_WITH_UNIDENTIFIABLE_CARD = """
<html><body>
<div class="flight-offer">
  <span class="total-price" data-currency="USD">199</span>
</div>
</body></html>
"""

_HTML_WITH_OFFER_ID_BUT_NO_SEGMENT = """
<html><body>
<div class="flight-offer" data-offer-id="delta-1">
  <span class="total-price" data-currency="USD">199</span>
</div>
</body></html>
"""

_HTML_WITH_INVALID_CURRENCY_CODE = """
<html><body>
<div class="flight-offer" data-offer-id="epsilon-1">
  <div class="outbound-segment">
    <span class="origin-airport">TST</span>
  </div>
  <span class="total-price" data-currency="US">100</span>
</div>
</body></html>
"""


def _alpha_and_beta_offers(result):
    offers_by_id = {offer.offer_id: offer for offer in result.offers}
    return offers_by_id["alpha-1"], offers_by_id["beta-1"]


# ---------------------------------------------------------------------------
# 1. Parser refuses disabled source policy.
# ---------------------------------------------------------------------------


def test_parser_refuses_disabled_source_policy() -> None:
    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _policy(enabled=False), _request())
    assert result.status == FlightSearchStatus.NOT_CONNECTED
    assert result.offers == []


# ---------------------------------------------------------------------------
# 2. Parser refuses unapproved/unsafe source policy.
# ---------------------------------------------------------------------------


def test_parser_refuses_unapproved_source_policy() -> None:
    policy = _policy(enabled=False, approved_for_personal_use=False)
    assert policy.is_unsafe is True

    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, policy, _request())

    assert result.status == FlightSearchStatus.NOT_CONNECTED
    assert result.offers == []


# ---------------------------------------------------------------------------
# 3. Parser refuses login-required/paywalled/captcha-expected policy, even
#    disabled.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("unsafe_field", ["requires_login", "paywalled", "captcha_expected"])
def test_parser_refuses_unsafe_source_even_when_disabled(unsafe_field: str) -> None:
    policy = _policy(enabled=False, **{unsafe_field: True})
    assert policy.is_unsafe is True

    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, policy, _request())

    assert result.status == FlightSearchStatus.NOT_CONNECTED
    assert result.offers == []


# ---------------------------------------------------------------------------
# 4. Parser refuses source policy when flights are not allowed.
# ---------------------------------------------------------------------------


def test_parser_refuses_source_that_does_not_allow_flights() -> None:
    policy = _enabled_policy(allows_flights=False)
    assert policy.is_unsafe is False

    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, policy, _request())

    assert result.status == FlightSearchStatus.NOT_CONNECTED
    assert result.offers == []


def test_allows_flights_defaults_to_false_on_the_model() -> None:
    """Adding `allows_flights` must not silently grant flight-scraping
    permission to any existing/default `ScrapingSourcePolicy`."""
    policy = ScrapingSourcePolicy(
        source_id="unrelated_source",
        source_name="Unrelated Source",
        base_url="https://example.test",
        rate_limit_seconds=10,
    )
    assert policy.allows_flights is False


# ---------------------------------------------------------------------------
# 5. Parser parses a valid static one-way flight HTML fixture into
#    FlightSearchResult status=success.
# ---------------------------------------------------------------------------


def test_parser_parses_valid_one_way_fixture_into_success_result() -> None:
    result = parse_scraped_flight_html(
        _VALID_HTML_ONE_WAY,
        _enabled_policy(),
        _request(),
        source_url=f"{_TEST_BASE_URL}/search",
        parser_version="test-parser-v1",
    )

    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 2
    assert {offer.offer_id for offer in result.offers} == {"alpha-1", "beta-1"}


# ---------------------------------------------------------------------------
# 6. Parser parses a valid round-trip fixture with outbound and return
#    segments.
# ---------------------------------------------------------------------------


def test_parser_parses_round_trip_fixture_with_outbound_and_return_segments() -> None:
    result = parse_scraped_flight_html(
        _VALID_HTML_ROUND_TRIP, _enabled_policy(), _request(return_date=date(2026, 10, 17))
    )

    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 1
    offer = result.offers[0]
    assert len(offer.outbound_segments) == 1
    assert len(offer.return_segments) == 1
    assert offer.outbound_segments[0].flight_number == "TEST_ONLY_FLIGHT_123"
    assert offer.return_segments[0].flight_number == "TEST_ONLY_FLIGHT_456"
    assert offer.outbound_segments[0].origin_airport == "TST"
    assert offer.return_segments[0].origin_airport == "DMO"
    # `currency` element used since total-price has no data-currency attr.
    assert offer.currency == "EUR"
    assert offer.total_price_amount == Decimal("610")


def test_one_way_offer_has_no_return_segments() -> None:
    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())
    alpha, beta = _alpha_and_beta_offers(result)
    assert alpha.return_segments == []
    assert beta.return_segments == []


# ---------------------------------------------------------------------------
# 7. Parsed offer includes only fields present in the HTML.
# ---------------------------------------------------------------------------


def test_alpha_offer_includes_only_fields_present_in_html() -> None:
    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())
    alpha, _beta = _alpha_and_beta_offers(result)

    assert len(alpha.outbound_segments) == 1
    segment = alpha.outbound_segments[0]
    assert segment.origin_airport == "TST"
    assert segment.destination_airport == "DMO"
    assert segment.departure_time == datetime(2026, 10, 10, 20, 30, tzinfo=timezone.utc)
    assert segment.arrival_time == datetime(2026, 10, 11, 8, 45, tzinfo=timezone.utc)
    assert segment.carrier_name == "TEST_ONLY_AIRLINE_ALPHA"
    assert segment.carrier_code == "TA"
    assert segment.flight_number == "TEST_ONLY_FLIGHT_123"
    assert segment.duration_minutes == 435

    assert alpha.total_price_amount == Decimal("452.10")
    assert alpha.currency == "USD"
    assert alpha.availability_status == "available"
    assert alpha.baggage_policy == "1 checked bag included"
    assert alpha.cancellation_policy == "Non-refundable"
    assert alpha.booking_url == f"{_TEST_BASE_URL}/book/alpha-1"


# ---------------------------------------------------------------------------
# 8-16. Missing fields on the minimal (Beta) offer stay None/unknown,
#       never guessed.
# ---------------------------------------------------------------------------


def test_beta_offer_missing_price_stays_none() -> None:
    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())
    _alpha, beta = _alpha_and_beta_offers(result)
    assert beta.total_price_amount is None
    assert beta.currency is None


def test_beta_offer_missing_booking_url_stays_none() -> None:
    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())
    _alpha, beta = _alpha_and_beta_offers(result)
    assert beta.booking_url is None


def test_beta_offer_missing_carrier_fields_stay_none() -> None:
    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())
    _alpha, beta = _alpha_and_beta_offers(result)
    segment = beta.outbound_segments[0]
    assert segment.carrier_name is None
    assert segment.carrier_code is None


def test_beta_offer_missing_flight_number_stays_none() -> None:
    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())
    _alpha, beta = _alpha_and_beta_offers(result)
    assert beta.outbound_segments[0].flight_number is None


def test_beta_offer_missing_airport_fields_present_since_beta_has_them() -> None:
    """Beta's segment does carry origin/destination airport text -- this
    confirms present fields still parse even on the otherwise-minimal
    offer, while the truly-absent fields below stay honestly None."""
    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())
    _alpha, beta = _alpha_and_beta_offers(result)
    assert beta.outbound_segments[0].origin_airport == "TST"
    assert beta.outbound_segments[0].destination_airport == "DMO"


def test_beta_offer_missing_times_stay_none() -> None:
    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())
    _alpha, beta = _alpha_and_beta_offers(result)
    segment = beta.outbound_segments[0]
    assert segment.departure_time is None
    assert segment.arrival_time is None


def test_beta_offer_missing_duration_stays_none() -> None:
    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())
    _alpha, beta = _alpha_and_beta_offers(result)
    assert beta.outbound_segments[0].duration_minutes is None


def test_beta_offer_missing_baggage_and_cancellation_policy_stay_none() -> None:
    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())
    _alpha, beta = _alpha_and_beta_offers(result)
    assert beta.baggage_policy is None
    assert beta.cancellation_policy is None


def test_beta_offer_missing_availability_stays_none() -> None:
    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())
    _alpha, beta = _alpha_and_beta_offers(result)
    assert beta.availability_status is None


# ---------------------------------------------------------------------------
# 17/18/19. Parsed data has data_status=scraped_public_page,
# scraped_provenance, and official_provider=False.
# ---------------------------------------------------------------------------


def test_parsed_offer_has_scraped_public_page_provenance() -> None:
    result = parse_scraped_flight_html(
        _VALID_HTML_ONE_WAY,
        _enabled_policy(),
        _request(),
        source_url=f"{_TEST_BASE_URL}/search",
        parser_version="test-parser-v1",
    )
    alpha, _beta = _alpha_and_beta_offers(result)

    assert alpha.data_status == DataStatus.SCRAPED_PUBLIC_PAGE
    assert alpha.scraped_provenance is not None
    assert alpha.scraped_provenance.source_type == ScrapingSourceType.SCRAPED_PUBLIC_PAGE
    assert alpha.scraped_provenance.provenance == ScrapingSourceType.SCRAPED_PUBLIC_PAGE
    assert alpha.scraped_provenance.source_id == _TEST_SOURCE_ID
    assert alpha.scraped_provenance.source_name == _TEST_SOURCE_NAME
    assert alpha.scraped_provenance.parser_version == "test-parser-v1"
    assert alpha.scraped_provenance.source_url == f"{_TEST_BASE_URL}/search"
    from app.models.scraping import ScrapingExtractionMethod

    assert alpha.scraped_provenance.extraction_method == ScrapingExtractionMethod.STATIC_HTML_PARSER


def test_parsed_offer_confidence_is_experimental() -> None:
    from app.models.scraping import ScrapedDataConfidence

    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())
    alpha, _beta = _alpha_and_beta_offers(result)
    assert alpha.scraped_provenance.confidence == ScrapedDataConfidence.EXPERIMENTAL


def test_parsed_offer_official_provider_is_false() -> None:
    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())
    alpha, beta = _alpha_and_beta_offers(result)
    assert alpha.scraped_provenance.official_provider is False
    assert beta.scraped_provenance.official_provider is False


def test_every_segment_on_parsed_offer_is_also_scraped_public_page() -> None:
    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())
    alpha, _beta = _alpha_and_beta_offers(result)
    assert alpha.outbound_segments[0].data_status == DataStatus.SCRAPED_PUBLIC_PAGE


# ---------------------------------------------------------------------------
# 20. Parser returns unavailable with empty offers when no valid flight
#     offers exist.
# ---------------------------------------------------------------------------


def test_parser_returns_unavailable_when_no_flight_offers_present() -> None:
    result = parse_scraped_flight_html(_HTML_WITH_NO_FLIGHT_OFFERS, _enabled_policy(), _request())
    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


def test_parser_returns_unavailable_when_card_missing_offer_id() -> None:
    result = parse_scraped_flight_html(
        _HTML_WITH_UNIDENTIFIABLE_CARD, _enabled_policy(), _request()
    )
    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


def test_parser_returns_unavailable_when_offer_has_no_outbound_segment() -> None:
    """An offer_id alone, with no outbound segment, can't safely represent
    a bookable flight -- skipped entirely rather than given a fabricated
    segment."""
    result = parse_scraped_flight_html(
        _HTML_WITH_OFFER_ID_BUT_NO_SEGMENT, _enabled_policy(), _request()
    )
    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


# ---------------------------------------------------------------------------
# 21. Parser returns failed safely on malformed/unparseable values.
# ---------------------------------------------------------------------------


def test_parser_returns_failed_on_invalid_currency_code() -> None:
    """An invalid (non-3-letter) currency code fails `FlightOffer`'s own
    validation -- the parser catches this instead of crashing, and never
    falls back to a guessed/placeholder currency."""
    result = parse_scraped_flight_html(
        _HTML_WITH_INVALID_CURRENCY_CODE, _enabled_policy(), _request()
    )
    assert result.status == FlightSearchStatus.FAILED
    assert result.offers == []
    assert result.message is not None
    # The safe message never leaks a raw exception/traceback string.
    assert "Traceback" not in result.message
    assert "ValidationError" not in result.message


# ---------------------------------------------------------------------------
# 22. Parser does not call network.
# ---------------------------------------------------------------------------


def test_parser_makes_no_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("parse_scraped_flight_html must not open a real httpx.Client")

    monkeypatch.setattr(httpx, "Client", _fail_if_called)

    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())

    assert result.status == FlightSearchStatus.SUCCESS


# ---------------------------------------------------------------------------
# 23. Parser module does not import requests/httpx/browser automation.
# ---------------------------------------------------------------------------


def test_parser_module_has_no_disallowed_imports() -> None:
    import app.providers.flights.scraped_parser as scraped_parser_module

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


def test_provider_gateway_does_not_reference_flight_scraped_parser() -> None:
    import app.providers.gateway as gateway_module

    source = inspect.getsource(gateway_module)
    assert "scraped_parser" not in source
    assert "parse_scraped_flight_html" not in source


def test_planning_orchestrator_does_not_reference_flight_scraped_parser() -> None:
    import app.services.planning_orchestrator as orchestrator_module

    source = inspect.getsource(orchestrator_module)
    assert "scraped_parser" not in source
    assert "parse_scraped_flight_html" not in source


def test_scraped_local_flight_provider_now_references_parser() -> None:
    """Step 169C added the parser standalone; Step 169D wires it into
    `ScrapedLocalFlightProvider` (see test_scraped_flight_cache.py for
    the provider-level behavior tests)."""
    import app.providers.flights.scraped_adapter as scraped_adapter_module

    source = inspect.getsource(scraped_adapter_module)
    assert "parse_scraped_flight_html" in source


# ---------------------------------------------------------------------------
# Never fabricates an offer/airline/flight number not present in the HTML.
# ---------------------------------------------------------------------------


def test_parser_never_invents_a_flight_not_present_in_html() -> None:
    result = parse_scraped_flight_html(_VALID_HTML_ONE_WAY, _enabled_policy(), _request())
    carrier_names = {
        segment.carrier_name
        for offer in result.offers
        for segment in offer.outbound_segments
        if segment.carrier_name is not None
    }
    assert carrier_names == {"TEST_ONLY_AIRLINE_ALPHA"}
