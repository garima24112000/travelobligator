from __future__ import annotations

from fastapi import APIRouter, status

from app.core.errors import AppError
from app.core.response import success_response
from app.models.planning_state import (
    PipelineStatus,
    PlanningStage,
    PlanningState,
    TripRequest,
)
from app.providers.gateway import provider_gateway
from app.repositories.planning_state_repository import planning_state_repository
from app.repositories.trip_repository import trip_repository
from app.schemas.api_responses import ApiResponse
from app.schemas.errors import ErrorCode
from app.schemas.provider_coverage import ProviderCoverageResponseData
from app.schemas.trips import TripResponseData

router = APIRouter(prefix="/trips", tags=["trips"])


@router.post(
    "",
    response_model=ApiResponse[TripResponseData],
    status_code=status.HTTP_201_CREATED,
)
def create_trip(trip_request: TripRequest) -> ApiResponse[TripResponseData]:
    planning_state = PlanningState(trip_request=trip_request)
    planning_state.set_active_stage(PlanningStage.CREATE_TRIP)
    planning_state.set_pipeline_status(PipelineStatus.DRAFT)
    planning_state.provider_coverage = provider_gateway.default_provider_coverage()

    trip_repository.create(planning_state.trip_id)
    planning_state_repository.save(planning_state)

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
