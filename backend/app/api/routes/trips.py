from __future__ import annotations

from fastapi import APIRouter, status

from app.core.errors import AppError
from app.core.response import success_response
from app.models.planning_state import TripRequest
from app.repositories.planning_state_repository import planning_state_repository
from app.schemas.api_responses import ApiResponse
from app.schemas.destination_context import DestinationContextResponseData
from app.schemas.errors import ErrorCode
from app.schemas.provider_coverage import ProviderCoverageResponseData
from app.schemas.trips import TripResponseData
from app.services.planning_orchestrator import planning_orchestrator

router = APIRouter(prefix="/trips", tags=["trips"])


@router.post(
    "",
    response_model=ApiResponse[TripResponseData],
    status_code=status.HTTP_201_CREATED,
)
def create_trip(trip_request: TripRequest) -> ApiResponse[TripResponseData]:
    planning_state = planning_orchestrator.create_trip(trip_request)
    data = TripResponseData(trip_id=planning_state.trip_id, planning_state=planning_state)
    return success_response(data)


@router.get(
    "/{trip_id}",
    response_model=ApiResponse[TripResponseData],
)
def get_trip(trip_id: str) -> ApiResponse[TripResponseData]:
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise AppError(
            code=ErrorCode.TRIP_NOT_FOUND,
            message=f"Trip '{trip_id}' was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            field="trip_id",
        )

    data = TripResponseData(trip_id=trip_id, planning_state=planning_state)
    return success_response(data)


@router.post(
    "/{trip_id}/generate",
    response_model=ApiResponse[TripResponseData],
)
def generate_trip_plan(trip_id: str) -> ApiResponse[TripResponseData]:
    planning_state = planning_orchestrator.generate_full_plan(trip_id)
    data = TripResponseData(trip_id=trip_id, planning_state=planning_state)
    return success_response(data)


@router.get(
    "/{trip_id}/destination-context",
    response_model=ApiResponse[DestinationContextResponseData],
)
def get_destination_context(trip_id: str) -> ApiResponse[DestinationContextResponseData]:
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise AppError(
            code=ErrorCode.TRIP_NOT_FOUND,
            message=f"Trip '{trip_id}' was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            field="trip_id",
        )

    if planning_state.destination_context is None:
        raise AppError(
            code=ErrorCode.DATA_UNAVAILABLE,
            message=(
                f"Destination context for trip '{trip_id}' has not been generated yet. "
                "Call POST /trips/{trip_id}/generate first."
            ),
            status_code=status.HTTP_409_CONFLICT,
            field="destination_context",
        )

    data = DestinationContextResponseData(
        trip_id=trip_id,
        destination_context=planning_state.destination_context,
        provider_coverage=planning_state.provider_coverage,
        unavailable_data=planning_state.unavailable_data,
        data_sources_used=planning_state.data_sources_used,
    )
    return success_response(data)


@router.get(
    "/{trip_id}/provider-coverage",
    response_model=ApiResponse[ProviderCoverageResponseData],
)
def get_provider_coverage(trip_id: str) -> ApiResponse[ProviderCoverageResponseData]:
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise AppError(
            code=ErrorCode.TRIP_NOT_FOUND,
            message=f"Trip '{trip_id}' was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            field="trip_id",
        )

    data = ProviderCoverageResponseData(
        trip_id=trip_id,
        provider_coverage=planning_state.provider_coverage,
        provider_status=planning_state.provider_status,
        unavailable_data=planning_state.unavailable_data,
        data_sources_used=planning_state.data_sources_used,
    )
    return success_response(data)
