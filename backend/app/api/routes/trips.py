from __future__ import annotations

from fastapi import APIRouter, status

from app.core.errors import AppError, trip_not_found_error
from app.core.response import success_response
from app.models.common import ReadinessStatus
from app.models.planning_state import TripRequest
from app.repositories.planning_state_repository import planning_state_repository
from app.schemas.api_responses import ApiResponse
from app.schemas.destination_context import DestinationContextResponseData
from app.schemas.errors import ErrorCode
from app.schemas.experience_plan import ExperiencePlanResponseData
from app.schemas.provider_coverage import ProviderCoverageResponseData
from app.schemas.trip_summary import TripSummaryResponseData
from app.schemas.trips import FeedbackRequest, TripResponseData
from app.schemas.validation_report import ValidationReportResponseData
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
        raise trip_not_found_error(trip_id)

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


@router.post(
    "/{trip_id}/feedback",
    response_model=ApiResponse[TripResponseData],
)
def submit_trip_feedback(
    trip_id: str, feedback_request: FeedbackRequest
) -> ApiResponse[TripResponseData]:
    planning_state = planning_orchestrator.apply_feedback(
        trip_id, feedback_request.feedback_text
    )
    data = TripResponseData(trip_id=trip_id, planning_state=planning_state)
    return success_response(data)


@router.get(
    "/{trip_id}/destination-context",
    response_model=ApiResponse[DestinationContextResponseData],
)
def get_destination_context(trip_id: str) -> ApiResponse[DestinationContextResponseData]:
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

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
        weather_context=planning_state.weather_context,
        holiday_context=planning_state.holiday_context,
        currency_context=planning_state.currency_context,
        provider_coverage=planning_state.provider_coverage,
        unavailable_data=planning_state.unavailable_data,
        data_sources_used=planning_state.data_sources_used,
    )
    return success_response(data)


@router.get(
    "/{trip_id}/experience-plan",
    response_model=ApiResponse[ExperiencePlanResponseData],
)
def get_experience_plan(trip_id: str) -> ApiResponse[ExperiencePlanResponseData]:
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

    if planning_state.experience_plan is None:
        raise AppError(
            code=ErrorCode.DATA_UNAVAILABLE,
            message=(
                f"Experience plan for trip '{trip_id}' has not been generated yet. "
                "Call POST /trips/{trip_id}/generate first."
            ),
            status_code=status.HTTP_409_CONFLICT,
            field="experience_plan",
        )

    data = ExperiencePlanResponseData(
        trip_id=trip_id,
        experience_plan=planning_state.experience_plan,
        validation_report=planning_state.validation_report,
        provider_coverage=planning_state.provider_coverage,
        unavailable_data=planning_state.unavailable_data,
        data_sources_used=planning_state.data_sources_used,
    )
    return success_response(data)


@router.get(
    "/{trip_id}/validation-report",
    response_model=ApiResponse[ValidationReportResponseData],
)
def get_validation_report(trip_id: str) -> ApiResponse[ValidationReportResponseData]:
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

    if planning_state.validation_report is None:
        raise AppError(
            code=ErrorCode.DATA_UNAVAILABLE,
            message=(
                f"Validation report for trip '{trip_id}' has not been generated yet. "
                "Call POST /trips/{trip_id}/generate first."
            ),
            status_code=status.HTTP_409_CONFLICT,
            field="validation_report",
        )

    data = ValidationReportResponseData(
        trip_id=trip_id,
        validation_report=planning_state.validation_report,
        provider_coverage=planning_state.provider_coverage,
        unavailable_data=planning_state.unavailable_data,
        data_sources_used=planning_state.data_sources_used,
    )
    return success_response(data)


@router.get(
    "/{trip_id}/summary",
    response_model=ApiResponse[TripSummaryResponseData],
)
def get_trip_summary(trip_id: str) -> ApiResponse[TripSummaryResponseData]:
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

    destination_context = planning_state.destination_context
    experience_plan = planning_state.experience_plan
    validation_report = planning_state.validation_report

    scheduled_experiences_count = (
        sum(len(day_plan.experiences) for day_plan in experience_plan.daily_plans)
        if experience_plan
        else 0
    )

    validation_status: str | None = None
    main_blocking_reason: str | None = None
    main_review_reason: str | None = None
    if validation_report is not None:
        validation_status = validation_report.readiness_status.value
        if (
            validation_report.readiness_status == ReadinessStatus.BLOCKED
            and validation_report.critical_issues
        ):
            main_blocking_reason = validation_report.critical_issues[0].message
        elif (
            validation_report.readiness_status == ReadinessStatus.NEEDS_REVIEW
            and validation_report.warnings
        ):
            main_review_reason = validation_report.warnings[0].message

    data = TripSummaryResponseData(
        trip_id=trip_id,
        primary_destination=planning_state.trip_request.primary_destination,
        start_date=planning_state.trip_request.start_date,
        end_date=planning_state.trip_request.end_date,
        pipeline_status=planning_state.metadata.pipeline_status,
        active_stage=planning_state.metadata.active_stage,
        provider_coverage=planning_state.provider_coverage,
        destination_context_generated=destination_context is not None,
        experience_plan_generated=experience_plan is not None,
        validation_report_generated=validation_report is not None,
        candidate_pois_count=(
            len(destination_context.candidate_pois) if destination_context else 0
        ),
        candidate_restaurants_count=(
            len(destination_context.candidate_restaurants) if destination_context else 0
        ),
        candidate_accommodation_pois_count=(
            len(destination_context.candidate_accommodation_pois)
            if destination_context
            else 0
        ),
        scheduled_experiences_count=scheduled_experiences_count,
        validation_status=validation_status,
        main_blocking_reason=main_blocking_reason,
        main_review_reason=main_review_reason,
    )
    return success_response(data)


@router.get(
    "/{trip_id}/provider-coverage",
    response_model=ApiResponse[ProviderCoverageResponseData],
)
def get_provider_coverage(trip_id: str) -> ApiResponse[ProviderCoverageResponseData]:
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

    data = ProviderCoverageResponseData(
        trip_id=trip_id,
        provider_coverage=planning_state.provider_coverage,
        provider_status=planning_state.provider_status,
        unavailable_data=planning_state.unavailable_data,
        data_sources_used=planning_state.data_sources_used,
    )
    return success_response(data)
