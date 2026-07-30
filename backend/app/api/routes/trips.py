from __future__ import annotations

from fastapi import APIRouter, status

from app.core.errors import (
    AppError,
    lock_not_found_error,
    regeneration_not_available_error,
    trip_not_found_error,
)
from app.core.response import success_response
from app.models.common import ReadinessStatus
from app.models.planning_state import TripRequest
from app.repositories.planning_state_repository import planning_state_repository
from app.schemas.api_responses import ApiResponse
from app.schemas.candidate_quality import CandidateQualityResponseData
from app.schemas.destination_context import DestinationContextResponseData
from app.schemas.errors import ErrorCode
from app.schemas.experience_plan import ExperiencePlanResponseData
from app.schemas.provider_coverage import ProviderCoverageResponseData
from app.schemas.regeneration_attempts import RegenerationAttemptsResponseData
from app.schemas.regeneration_readiness import RegenerationReadinessResponseData
from app.schemas.trip_summary import TripSummaryResponseData
from app.schemas.trips import FeedbackRequest, LockRequest, TripResponseData
from app.schemas.validation_report import ValidationReportResponseData
from app.services.plan_diff_preview_service import plan_diff_preview_service
from app.services.planning_orchestrator import planning_orchestrator
from app.services.regeneration_attempt_service import regeneration_attempt_service
from app.services.regeneration_readiness_service import regeneration_readiness_service
from app.services.user_lock_service import user_lock_service

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


@router.post(
    "/{trip_id}/regenerate",
    response_model=ApiResponse[None],
)
def regenerate_trip_plan(trip_id: str) -> ApiResponse[None]:
    """Hard-refusal endpoint (Step 138), now also recording a minimal audit
    trail of the attempt (Step 142). Feedback-driven regeneration has no
    real engine connected yet, so this always refuses instead of silently
    doing nothing or pretending to succeed. The *only* state mutation is
    appending one `RegenerationAttempt` to `regeneration_attempts` (plus
    the `metadata.updated_at` bump that comes with it) -- this never calls
    `POST /generate`, never reruns any planning stage, never creates a new
    plan version, and never touches `experience_plan`, `destination_context`,
    `validation_report`, `provider_coverage`, `route_feasibility_context`,
    `feedback_history`, `pending_feedback_summary`, `user_locks`,
    `version_history`, `plan_diff_preview`, or `regeneration_readiness`.
    """
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

    planning_state = regeneration_attempt_service.record_blocked_attempt(planning_state)
    planning_state_repository.save(planning_state)

    raise regeneration_not_available_error()


@router.post(
    "/{trip_id}/locks",
    response_model=ApiResponse[TripResponseData],
    status_code=status.HTTP_201_CREATED,
)
def create_trip_lock(trip_id: str, lock_request: LockRequest) -> ApiResponse[TripResponseData]:
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

    planning_state = user_lock_service.add_lock(
        planning_state,
        locked_item_type=lock_request.locked_item_type,
        locked_item_id=lock_request.locked_item_id,
        reason=lock_request.reason,
    )
    # Recomputed from scratch every time (Step 132) so it always reflects
    # the just-added lock.
    planning_state = plan_diff_preview_service.recompute(planning_state)
    # Recomputed from scratch every time (Step 135) so it always reflects
    # the just-added lock.
    planning_state = regeneration_readiness_service.recompute(planning_state)
    planning_state_repository.save(planning_state)

    data = TripResponseData(trip_id=trip_id, planning_state=planning_state)
    return success_response(data)


@router.delete(
    "/{trip_id}/locks/{lock_id}",
    response_model=ApiResponse[TripResponseData],
)
def delete_trip_lock(trip_id: str, lock_id: str) -> ApiResponse[TripResponseData]:
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

    if user_lock_service.find_lock(planning_state, lock_id) is None:
        raise lock_not_found_error(trip_id, lock_id)

    planning_state = user_lock_service.remove_lock(planning_state, lock_id)
    # Recomputed from scratch every time (Step 132) so it always reflects
    # the just-removed lock.
    planning_state = plan_diff_preview_service.recompute(planning_state)
    # Recomputed from scratch every time (Step 135) so it always reflects
    # the just-removed lock.
    planning_state = regeneration_readiness_service.recompute(planning_state)
    planning_state_repository.save(planning_state)

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
    "/{trip_id}/candidate-quality",
    response_model=ApiResponse[CandidateQualityResponseData],
)
def get_candidate_quality(trip_id: str) -> ApiResponse[CandidateQualityResponseData]:
    """Read-only deterministic pre-ranking metadata (Step 156A/156B,
    docs/18_candidate_quality.md). Always reflects whatever
    `candidate_quality_report` already holds -- recomputed on the
    destination-context write path only, never by this endpoint. If a
    destination context has not been generated yet, `candidate_quality_report`
    is honestly `null` rather than fabricated.
    """
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

    data = CandidateQualityResponseData(
        trip_id=trip_id,
        candidate_quality_report=planning_state.candidate_quality_report,
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


@router.get(
    "/{trip_id}/regeneration-readiness",
    response_model=ApiResponse[RegenerationReadinessResponseData],
)
def get_regeneration_readiness(trip_id: str) -> ApiResponse[RegenerationReadinessResponseData]:
    """Read-only gate explaining whether feedback-driven regeneration can
    run right now (Step 135). Always reflects the value already recomputed
    on the write paths (create/generate/feedback/lock create/lock remove)
    -- this endpoint never recomputes, mutates, or regenerates anything
    itself.
    """
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

    data = RegenerationReadinessResponseData(
        trip_id=trip_id,
        regeneration_readiness=planning_state.regeneration_readiness,
    )
    return success_response(data)


@router.get(
    "/{trip_id}/regeneration-attempts",
    response_model=ApiResponse[RegenerationAttemptsResponseData],
)
def get_regeneration_attempts(trip_id: str) -> ApiResponse[RegenerationAttemptsResponseData]:
    """Read-only audit trail of blocked `POST /trips/{trip_id}/regenerate`
    attempts (Step 142). Returns whatever has already been recorded, in
    stored order -- this endpoint never recomputes, mutates, or
    regenerates anything itself.
    """
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

    data = RegenerationAttemptsResponseData(
        trip_id=trip_id,
        regeneration_attempts=planning_state.regeneration_attempts,
    )
    return success_response(data)
