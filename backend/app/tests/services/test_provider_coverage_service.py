from __future__ import annotations

from app.models.common import DataStatus, ProviderStatus
from app.models.planning_state import (
    PlanningState,
    TravelGroupType,
    TripPace,
    TripRequest,
)
from app.models.providers import ProviderResponse, ProviderType
from app.services.provider_coverage_service import ProviderCoverageService


def _response(
    status: ProviderStatus,
    data_status: DataStatus,
    unavailable_fields: list[str] | None = None,
    fallback_used: bool = False,
) -> ProviderResponse[None]:
    return ProviderResponse[None](
        provider_name="openstreetmap_places",
        provider_type=ProviderType.PLACES,
        status=status,
        data_status=data_status,
        data=None,
        unavailable_fields=unavailable_fields or [],
        fallback_used=fallback_used,
        confidence=0.6,
        message="test",
    )


def _planning_state() -> PlanningState:
    return PlanningState(
        trip_request=TripRequest(
            primary_destination="Testville, Testland",
            start_date="2026-08-10",
            end_date="2026-08-12",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
            pace=TripPace.BALANCED,
        )
    )


def test_places_success_preserved_when_accommodation_fails_later() -> None:
    service = ProviderCoverageService()
    planning_state = _planning_state()

    service.record_provider_result(
        planning_state, _response(ProviderStatus.SUCCESS, DataStatus.LIVE), "places"
    )
    service.record_provider_result(
        planning_state,
        _response(ProviderStatus.FALLBACK_USED, DataStatus.FALLBACK_USED, fallback_used=True),
        "restaurants",
    )
    service.record_provider_result(
        planning_state,
        _response(
            ProviderStatus.FAILED, DataStatus.FAILED, unavailable_fields=["accommodation_pois"]
        ),
        "accommodations",
    )

    places_entry = planning_state.provider_status["openstreetmap_places:places"]
    restaurants_entry = planning_state.provider_status["openstreetmap_places:restaurants"]
    accommodations_entry = planning_state.provider_status["openstreetmap_places:accommodations"]

    # Each field gets its own entry rather than the later accommodation
    # failure overwriting the earlier successful/fallback entries.
    assert places_entry.status == ProviderStatus.SUCCESS
    assert restaurants_entry.status == ProviderStatus.FALLBACK_USED
    assert restaurants_entry.fallback_used is True
    assert accommodations_entry.status == ProviderStatus.FAILED

    # provider_coverage (per-field) is unaffected by this change and stays correct.
    assert planning_state.provider_coverage.places == "success"
    assert planning_state.provider_coverage.restaurants == "fallback_used"
    assert planning_state.provider_coverage.accommodations == "failed"

    # No entry is invented for a provider/field pair that was never called.
    assert "openstreetmap_places" not in planning_state.provider_status
    assert len(planning_state.provider_status) == 3


def test_distinct_providers_for_same_field_keep_distinct_keys() -> None:
    service = ProviderCoverageService()
    planning_state = _planning_state()

    service.record_provider_result(
        planning_state, _response(ProviderStatus.SUCCESS, DataStatus.LIVE), "accommodations"
    )
    other_provider_response = ProviderResponse[None](
        provider_name="accommodation_provider",
        provider_type=ProviderType.ACCOMMODATION,
        status=ProviderStatus.NOT_CONNECTED,
        data_status=DataStatus.NOT_CONNECTED,
        data=None,
        unavailable_fields=["accommodation_options"],
        confidence=0.0,
        message="test",
    )
    service.record_provider_result(planning_state, other_provider_response, "accommodations")

    assert (
        planning_state.provider_status["openstreetmap_places:accommodations"].status
        == ProviderStatus.SUCCESS
    )
    assert (
        planning_state.provider_status["accommodation_provider:accommodations"].status
        == ProviderStatus.NOT_CONNECTED
    )


# ---------------------------------------------------------------------------
# Step 152: data_sources_used consistency
# ---------------------------------------------------------------------------


def _custom_response(
    provider_name: str,
    provider_type: ProviderType,
    status: ProviderStatus,
    data_status: DataStatus,
) -> ProviderResponse[None]:
    return ProviderResponse[None](
        provider_name=provider_name,
        provider_type=provider_type,
        status=status,
        data_status=data_status,
        data=None,
        confidence=0.6,
        message="test",
    )


def test_success_live_entry_adds_provider_name_to_data_sources_used() -> None:
    service = ProviderCoverageService()
    planning_state = _planning_state()

    service.record_provider_result(
        planning_state, _response(ProviderStatus.SUCCESS, DataStatus.LIVE), "places"
    )

    assert planning_state.data_sources_used == ["openstreetmap_places"]


def test_duplicate_provider_name_appears_once() -> None:
    service = ProviderCoverageService()
    planning_state = _planning_state()

    service.record_provider_result(
        planning_state, _response(ProviderStatus.SUCCESS, DataStatus.LIVE), "places"
    )
    service.record_provider_result(
        planning_state, _response(ProviderStatus.SUCCESS, DataStatus.LIVE), "restaurants"
    )
    service.record_provider_result(
        planning_state,
        _response(ProviderStatus.FALLBACK_USED, DataStatus.FALLBACK_USED, fallback_used=True),
        "accommodations",
    )

    assert planning_state.data_sources_used == ["openstreetmap_places"]


def test_not_connected_provider_is_excluded_from_data_sources_used() -> None:
    service = ProviderCoverageService()
    planning_state = _planning_state()

    service.record_provider_result(
        planning_state,
        _custom_response(
            "accommodation_provider",
            ProviderType.ACCOMMODATION,
            ProviderStatus.NOT_CONNECTED,
            DataStatus.NOT_CONNECTED,
        ),
        "accommodations",
    )

    assert planning_state.data_sources_used == []


def test_failed_and_unavailable_providers_are_excluded_from_data_sources_used() -> None:
    service = ProviderCoverageService()
    planning_state = _planning_state()

    service.record_provider_result(
        planning_state,
        _custom_response(
            "routes_provider", ProviderType.ROUTES, ProviderStatus.FAILED, DataStatus.FAILED
        ),
        "routes",
    )
    service.record_provider_result(
        planning_state,
        _custom_response(
            "flaky_weather_provider",
            ProviderType.WEATHER,
            ProviderStatus.UNAVAILABLE,
            DataStatus.UNAVAILABLE,
        ),
        "weather",
    )

    assert planning_state.data_sources_used == []


def test_existing_explicit_data_sources_used_are_preserved_and_merged() -> None:
    service = ProviderCoverageService()
    planning_state = _planning_state()
    planning_state.data_sources_used = ["manually_recorded_source"]

    service.record_provider_result(
        planning_state, _response(ProviderStatus.SUCCESS, DataStatus.LIVE), "places"
    )

    assert planning_state.data_sources_used == [
        "manually_recorded_source",
        "openstreetmap_places",
    ]


def test_data_sources_used_order_is_deterministic_and_matches_call_order() -> None:
    service = ProviderCoverageService()
    planning_state = _planning_state()

    service.record_provider_result(
        planning_state,
        _custom_response(
            "open_meteo", ProviderType.WEATHER, ProviderStatus.SUCCESS, DataStatus.LIVE
        ),
        "weather",
    )
    service.record_provider_result(
        planning_state,
        _custom_response(
            "nager_date", ProviderType.HOLIDAY, ProviderStatus.SUCCESS, DataStatus.LIVE
        ),
        "holidays",
    )
    service.record_provider_result(
        planning_state,
        _custom_response(
            "frankfurter", ProviderType.CURRENCY, ProviderStatus.SUCCESS, DataStatus.LIVE
        ),
        "currency",
    )

    assert planning_state.data_sources_used == ["open_meteo", "nager_date", "frankfurter"]
