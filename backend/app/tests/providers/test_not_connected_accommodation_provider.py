from __future__ import annotations

from datetime import date

from app.models.accommodation import AccommodationSearchRequest, AccommodationSearchStatus
from app.providers.accommodation.not_connected_adapter import (
    NotConnectedAccommodationProvider,
)

# Behavior tests for Step 167B's default accommodation inventory adapter.
# Never calls a real network service and never requires any lodging
# provider configuration.


def _request(**overrides: object) -> AccommodationSearchRequest:
    fields: dict[str, object] = {
        "destination": "Lisbon, Portugal",
        "check_in_date": date(2026, 10, 10),
        "check_out_date": date(2026, 10, 14),
        "adults": 2,
        "rooms": 1,
    }
    fields.update(overrides)
    return AccommodationSearchRequest(**fields)


def test_returns_not_connected_status() -> None:
    provider = NotConnectedAccommodationProvider()
    result = provider.search_accommodations(_request())
    assert result.status == AccommodationSearchStatus.NOT_CONNECTED


def test_returns_empty_offers() -> None:
    provider = NotConnectedAccommodationProvider()
    result = provider.search_accommodations(_request())
    assert result.offers == []


def test_does_not_fabricate_message_is_honest() -> None:
    provider = NotConnectedAccommodationProvider()
    result = provider.search_accommodations(_request())
    assert result.message == "Accommodation inventory provider is not connected."
    assert result.provider == "accommodation_inventory_provider"


def test_output_is_stable_across_calls() -> None:
    provider = NotConnectedAccommodationProvider()
    first = provider.search_accommodations(_request())
    second = provider.search_accommodations(_request(destination="Porto, Portugal"))
    assert first.status == second.status == AccommodationSearchStatus.NOT_CONNECTED
    assert first.offers == second.offers == []
    assert first.provider == second.provider
    assert first.message == second.message


def test_never_returns_placeholder_offers_regardless_of_request_size() -> None:
    provider = NotConnectedAccommodationProvider()
    result = provider.search_accommodations(
        _request(adults=6, children=3, rooms=3, currency="EUR")
    )
    assert result.offers == []
    assert result.status == AccommodationSearchStatus.NOT_CONNECTED


def test_does_not_inspect_destination_to_invent_data() -> None:
    provider = NotConnectedAccommodationProvider()
    result_lisbon = provider.search_accommodations(_request(destination="Lisbon, Portugal"))
    result_tokyo = provider.search_accommodations(_request(destination="Tokyo, Japan"))

    assert result_lisbon.offers == result_tokyo.offers == []
    assert result_lisbon.status == result_tokyo.status == AccommodationSearchStatus.NOT_CONNECTED
    assert result_lisbon.message == result_tokyo.message
