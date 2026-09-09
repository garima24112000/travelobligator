from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, status

from app.core.config import get_settings
from app.core.errors import (
    REGENERATION_BLOCKED_BY_LOCKS_MESSAGE,
    REGENERATION_NO_PENDING_FEEDBACK_MESSAGE,
    REGENERATION_NOT_AVAILABLE_MESSAGE,
    AppError,
    lock_not_found_error,
    regeneration_blocked_by_locks_error,
    regeneration_no_pending_feedback_error,
    regeneration_not_available_error,
    trip_not_found_error,
)
from app.core.response import success_response
from app.models.common import ReadinessStatus
from app.models.itinerary_narrative import ItineraryNarrativeStatus
from app.models.planning_state import GenerationProgress, TripRequest
from app.repositories.planning_state_repository import planning_state_repository
from app.schemas.ai_candidate_promotion import AICandidatePromotionResponseData
from app.schemas.ai_candidate_review import AICandidateReviewResponseData
from app.schemas.api_responses import ApiResponse
from app.schemas.candidate_quality import CandidateQualityResponseData
from app.schemas.destination_context import DestinationContextResponseData
from app.schemas.errors import ErrorCode
from app.schemas.experience_plan import ExperiencePlanResponseData
from app.schemas.generation_progress import GenerationProgressResponseData
from app.schemas.langgraph_shadow_run import LangGraphShadowRunResponseData
from app.schemas.provider_coverage import ProviderCoverageResponseData
from app.schemas.regeneration_attempts import RegenerationAttemptsResponseData
from app.schemas.regeneration_readiness import RegenerationReadinessResponseData
from app.schemas.regeneration_result import RegenerateResponseData
from app.schemas.trip_summary import TripSummaryResponseData
from app.schemas.trips import (
    FeedbackRequest,
    LockRequest,
    RegenerateRequest,
    TripResponseData,
)
from app.schemas.validation_report import ValidationReportResponseData
from app.services.ai_candidate_promotion_service import ai_candidate_promotion_service
from app.services.ai_candidate_review_service import ai_candidate_review_service
from app.services.feedback_service import (
    derive_pending_affected_stages,
    feedback_service,
    pending_feedback_events,
)
from app.services.itinerary_narrative_service import itinerary_narrative_service
from app.services.langgraph_planning_service import LangGraphPlanningService
from app.services.plan_diff_preview_service import plan_diff_preview_service
from app.services.planning_orchestrator import planning_orchestrator
from app.services.regeneration_attempt_service import regeneration_attempt_service
from app.services.regeneration_readiness_service import regeneration_readiness_service
from app.services.user_lock_service import user_lock_service
from app.services.versioning_service import versioning_service

logger = logging.getLogger(__name__)

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
    # Step 171D: config-gated engine selection; Step 171E made "langgraph"
    # the default once it reached stage parity with the legacy path (see
    # PlanningOrchestrator.generate_full_plan_via_langgraph's docstring).
    # An explicit "legacy", or any unrecognized value, always calls
    # generate_full_plan instead -- the original hand-written orchestrator
    # loop, completely unmodified by this branch. Either way, this route
    # only ever reaches a method already owned by the planning_orchestrator
    # singleton, never the underlying planning service directly.
    if get_settings().planning_engine_mode == "langgraph":
        planning_state = planning_orchestrator.generate_full_plan_via_langgraph(trip_id)
    else:
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
    response_model=ApiResponse[RegenerateResponseData],
)
def regenerate_trip_plan(
    trip_id: str, regenerate_request: RegenerateRequest | None = None
) -> ApiResponse[RegenerateResponseData]:
    """Feedback-driven regeneration (Step 138 hard refusal; Step 174B added
    the real request contract and guardrails; Step 174C added the one
    real mutation path for Section 174's audited MVP scope; Step 174D
    wires the applied-feedback lifecycle around it so repeating the same
    request after success is honestly refused rather than re-applying the
    same feedback again).

    Backward compatible by construction: an absent request body behaves
    identically to an explicit `{"confirm": false}` -- both hit the first
    guard below and return today's exact `REGENERATION_NOT_AVAILABLE`
    refusal, byte-for-byte unchanged since before Step 174B (see
    docs/17_regeneration_manual_qa.md).

    "Pending feedback" everywhere in this function means
    `feedback_service.pending_feedback_events` -- `applied_at is None`.
    An event a previous successful regeneration already applied is never
    reconsidered.

    Five outcomes, in order:

    1. `confirm` missing or `false` -- original blanket refusal.
    2. `confirm=true` with at least one active lock -- distinct
       `REGENERATION_BLOCKED_BY_LOCKS` refusal. Locks are bookkeeping only
       (no planning stage service reads or respects them), so a confirmed
       request must refuse outright rather than silently ignoring one.
    3. `confirm=true` with no pending feedback (none ever submitted, or
       every event already applied by a prior regeneration) -- distinct
       `REGENERATION_NO_PENDING_FEEDBACK` refusal.
    4. `confirm=true`, pending feedback exists, zero active locks, but no
       affected stage can be derived from that feedback (e.g. every
       pending event is unclassified `general_feedback`) or no plan has
       ever been generated for this trip -- falls back to the original
       `REGENERATION_NOT_AVAILABLE` refusal rather than rerunning nothing
       or regenerating a trip that was never generated.
    5. `confirm=true`, pending feedback exists, zero active locks, and at
       least one real affected stage -- Section 174's MVP scope. Reruns
       exactly those stages via the existing, unmodified
       `PlanningOrchestrator.rerun_affected_stages` (never
       `LangGraphPlanningService`, never a fresh `generate_full_plan`),
       records a new `VersionHistoryItem`
       (`VersioningService.create_version_after_feedback`), marks exactly
       the pending feedback events used as applied (`applied_at`/
       `applied_in_version`/`handling_status="applied"`), recomputes
       `pending_feedback_summary`/`plan_diff_preview`/
       `regeneration_readiness`, and returns `200` with a minimal
       `RegenerateResponseData`. If the rerun itself raises unexpectedly,
       no version is created, no feedback is marked applied, and a
       `status="failed"` audit attempt is recorded instead of a `200`.

    Every outcome appends exactly one `RegenerationAttempt` with a
    `reason_code`/`status` matching what actually happened, so the audit
    trail and the HTTP response can never disagree. `feedback_history`
    itself is never deleted or shortened -- applied events stay in place,
    just no longer counted as pending, so a repeated `{"confirm": true}`
    call right after a success (with no new feedback submitted since)
    correctly hits outcome 3 rather than creating another version from
    the same feedback.
    """
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

    request = regenerate_request or RegenerateRequest()

    if not request.confirm:
        planning_state = regeneration_attempt_service.record_blocked_attempt(planning_state)
        planning_state_repository.save(planning_state)
        raise regeneration_not_available_error()

    active_lock_count = sum(1 for lock in planning_state.user_locks if lock.is_active)
    if active_lock_count > 0:
        planning_state = regeneration_attempt_service.record_blocked_attempt(
            planning_state,
            reason_code=ErrorCode.REGENERATION_BLOCKED_BY_LOCKS.value,
            message=REGENERATION_BLOCKED_BY_LOCKS_MESSAGE,
        )
        planning_state_repository.save(planning_state)
        raise regeneration_blocked_by_locks_error()

    pending_events = pending_feedback_events(planning_state.feedback_history)
    if not pending_events:
        planning_state = regeneration_attempt_service.record_blocked_attempt(
            planning_state,
            reason_code=ErrorCode.REGENERATION_NO_PENDING_FEEDBACK.value,
            message=REGENERATION_NO_PENDING_FEEDBACK_MESSAGE,
        )
        planning_state_repository.save(planning_state)
        raise regeneration_no_pending_feedback_error()

    # confirm=true, pending feedback exists, zero active locks: Section
    # 174's MVP scope. Two more safe-refusal conditions before any
    # mutation is even attempted -- neither of these widens the MVP
    # scope, both just avoid acting on a request this deterministic path
    # cannot safely serve yet.
    affected_stages = derive_pending_affected_stages(planning_state.feedback_history)
    if not affected_stages or planning_state.experience_plan is None:
        planning_state = regeneration_attempt_service.record_blocked_attempt(planning_state)
        planning_state_repository.save(planning_state)
        raise regeneration_not_available_error()

    previous_version = planning_state.metadata.current_version
    applied_feedback_event_ids = [event.feedback_event_id for event in pending_events]

    try:
        planning_state = planning_orchestrator.rerun_affected_stages(
            planning_state, affected_stages
        )
    except Exception:
        logger.warning(
            "PlanningOrchestrator.rerun_affected_stages failed unexpectedly during "
            "POST /trips/%s/regenerate; recording a failed attempt instead of "
            "creating a new version or marking any feedback applied.",
            trip_id,
            exc_info=True,
        )
        planning_state = regeneration_attempt_service.record_blocked_attempt(
            planning_state,
            message=REGENERATION_NOT_AVAILABLE_MESSAGE,
            status="failed",
        )
        planning_state_repository.save(planning_state)
        raise regeneration_not_available_error()

    changed_sections = [stage.value for stage in affected_stages]

    # Step 182F: refresh the optional, read-only LLM narrator after the
    # real affected-stage rerun above -- ItineraryNarrativeService.generate
    # never raises, so a disabled or failing narrator never blocks
    # regeneration. Only reported in changed_sections when it actually
    # produced a new narrative (status == success); a disabled/
    # not_connected/failed refresh changes no other field, so it isn't
    # reported as a changed section either.
    planning_state = itinerary_narrative_service.generate(planning_state)
    if (
        planning_state.itinerary_narrative_report is not None
        and planning_state.itinerary_narrative_report.status == ItineraryNarrativeStatus.SUCCESS
    ):
        changed_sections.append("itinerary_narrative")

    # Locks are disallowed entirely for this MVP scope (guarded above), so
    # there is never a locked item for this regeneration to have preserved.
    preserved_sections: list[str] = []

    planning_state = versioning_service.create_version_after_feedback(
        planning_state,
        # VersionHistoryItem.feedback_event_id is a single reference field;
        # the most recent pending event is recorded there as the one that
        # most directly triggered this regeneration, while every pending
        # event's id is still reported in the response's
        # applied_feedback_event_ids (all of them were considered, since
        # this MVP scope has no per-event partial selection).
        feedback_event_id=applied_feedback_event_ids[-1],
        changed_sections=changed_sections,
        preserved_sections=preserved_sections,
        summary=(
            "Plan regenerated from pending feedback. Rerun stages: "
            f"{', '.join(changed_sections)}."
        ),
    )

    # Step 174D: mark exactly the feedback events used in this run as
    # applied, using the version just created -- re-looked-up by id on the
    # `planning_state` returned by the calls above rather than relying on
    # object identity surviving `rerun_affected_stages`/
    # `create_version_after_feedback`. This is the only place
    # `FeedbackEvent.applied_at`/`applied_in_version` are ever set; a
    # blocked or failed attempt never reaches here, so those fields stay
    # `None` on every event any refusal path touched.
    new_version_label = planning_state.metadata.current_version
    applied_at = datetime.now(timezone.utc)
    applied_event_id_set = set(applied_feedback_event_ids)
    for event in planning_state.feedback_history:
        if event.feedback_event_id in applied_event_id_set:
            event.applied_at = applied_at
            event.applied_in_version = new_version_label
            event.handling_status = "applied"

    # Recomputed from scratch, in this order, so each reflects the
    # just-created version and the feedback just marked applied -- same
    # recompute-from-scratch pattern every other write path in this
    # router already follows.
    planning_state = feedback_service.recompute_pending_feedback_summary(planning_state)
    planning_state = plan_diff_preview_service.recompute(planning_state)
    planning_state = regeneration_readiness_service.recompute(planning_state)
    planning_state = regeneration_attempt_service.record_applied_attempt(planning_state)
    planning_state_repository.save(planning_state)

    data = RegenerateResponseData(
        trip_id=trip_id,
        status="applied",
        previous_version=previous_version,
        current_version=new_version_label,
        changed_sections=changed_sections,
        preserved_sections=preserved_sections,
        applied_feedback_event_ids=applied_feedback_event_ids,
        active_lock_count=active_lock_count,
        message=(
            "Regeneration applied. Rerun stages: "
            f"{', '.join(changed_sections)}."
        ),
    )
    return success_response(data)


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


@router.get(
    "/{trip_id}/generation-progress",
    response_model=ApiResponse[GenerationProgressResponseData],
)
def get_generation_progress(trip_id: str) -> ApiResponse[GenerationProgressResponseData]:
    """Read-only backend `PlanningOrchestrator` pipeline stage-progress
    readout (Step 163B, docs/13_llm_reasoning_pipeline.md section 44).

    This reflects real backend stage progress only -- never a real flight
    route, real route/travel time, booking status, or any other travel
    fact. It never triggers generation and never mutates state; if
    `generation_progress` hasn't been set yet (a planning state persisted
    before this step), an idle default is returned instead of null. The
    Step 163A decorative frontend loading animation is not wired to this
    endpoint yet.
    """
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

    data = GenerationProgressResponseData(
        trip_id=trip_id,
        generation_progress=planning_state.generation_progress or GenerationProgress(),
    )
    return success_response(data)


@router.get(
    "/{trip_id}/ai-candidate-review",
    response_model=ApiResponse[AICandidateReviewResponseData],
)
def get_ai_candidate_review(trip_id: str) -> ApiResponse[AICandidateReviewResponseData]:
    """Read-only AI candidate discovery/grounding/eligibility review report
    (Step 170A, extended with deterministic eligibility rules in Step
    170B, docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).

    Built on every call, purely from `planning_state.ai_candidate_proposal_batch`/
    `candidate_grounding_batch`/`candidate_quality_report` -- the same
    shadow-mode state the Step 161B stage already stores when
    `AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED` is set. This endpoint
    never triggers new AI candidate discovery, never calls a provider/LLM,
    and never mutates `planning_state` -- with shadow mode off (the
    default), it honestly reports `status="no_candidate_data"` and an
    empty candidate list rather than fabricating one. `eligible_for_promotion`
    can be `True` when Step 170B's deterministic rules pass, but this
    endpoint never applies that eligibility -- it never sets
    `planning_state.ai_candidate_promotion_report`. Only
    `POST /trips/{trip_id}/ai-candidate-promotions` does that.
    """
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

    report = ai_candidate_review_service.build_report(planning_state)
    data = AICandidateReviewResponseData(
        trip_id=trip_id,
        ai_candidate_review_report=report,
    )
    return success_response(data)


@router.post(
    "/{trip_id}/ai-candidate-promotions",
    response_model=ApiResponse[AICandidatePromotionResponseData],
)
def promote_ai_candidates(trip_id: str) -> ApiResponse[AICandidatePromotionResponseData]:
    """Materializes Step 170B's deterministic eligibility verdicts into a
    dedicated `AICandidatePromotionReport`, stored on
    `planning_state.ai_candidate_promotion_report` (Step 170C,
    docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).

    A promoted candidate is **not** an itinerary stop -- it is a
    provider-grounded, quality-approved candidate now recorded as safe for
    a future scheduling step (170D) to consider. This endpoint never
    schedules anything into `experience_plan.daily_plans`, never calls a
    provider/LLM/AI candidate proposal provider, and only ever mutates
    `planning_state.ai_candidate_promotion_report` (plus the
    `metadata.updated_at` bump that comes with it) -- every other section
    of `planning_state` is untouched. Calling this endpoint again
    recomputes and replaces the report rather than appending to it, so
    repeated calls never duplicate a promoted candidate.
    """
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

    planning_state = ai_candidate_promotion_service.apply_promotion(planning_state)
    planning_state_repository.save(planning_state)

    assert planning_state.ai_candidate_promotion_report is not None
    data = AICandidatePromotionResponseData(
        trip_id=trip_id,
        ai_candidate_promotion_report=planning_state.ai_candidate_promotion_report,
    )
    return success_response(data)


@router.post(
    "/{trip_id}/langgraph-shadow-run",
    response_model=ApiResponse[LangGraphShadowRunResponseData],
)
def run_langgraph_shadow(trip_id: str) -> ApiResponse[LangGraphShadowRunResponseData]:
    """Read-only LangGraph shadow-run endpoint (Step 171C,
    docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).

    Executes the Step 171A/171B LangGraph planning graph
    (`LangGraphPlanningService`) against an isolated deep copy of this
    trip's current `PlanningState`, purely for inspection/comparison. This
    is **not** the official generation path -- `POST /trips/{trip_id}/generate`
    (`PlanningOrchestrator.generate_full_plan`) remains that -- and the
    result is never persisted:

    - `planning_state_repository.save` is never called.
    - The trip's cached `PlanningState` object is never mutated -- a
      `model_copy(deep=True)` is passed into the graph, never the same
      instance `planning_state_repository.get_by_trip_id` returned.
    - No version history, regeneration attempt, lock, or generation-progress
      bookkeeping is touched.
    - `persisted` is always `False` in the response (structurally
      enforced by the schema), confirming this honestly.

    Every node the graph runs is one of the same already-existing
    deterministic stage services `PlanningOrchestrator` itself uses (or,
    for `ai_candidate`, nothing at all by default -- see
    `build_ai_candidate_node`'s docstring). This endpoint never calls
    Groq/Anthropic/OpenAI, an AI candidate proposal provider, or any other
    LLM, and it never calls `POST /generate` internally.
    """
    planning_state = planning_state_repository.get_by_trip_id(trip_id)
    if planning_state is None:
        raise trip_not_found_error(trip_id)

    shadow_planning_state = planning_state.model_copy(deep=True)
    result = LangGraphPlanningService().run(
        trip_id, planning_state.trip_request, shadow_planning_state
    )

    data = LangGraphShadowRunResponseData(
        trip_id=trip_id,
        status="completed" if not result.failed_nodes else "completed_with_failures",
        planning_state=result.planning_state,
        completed_nodes=result.completed_nodes,
        failed_nodes=result.failed_nodes,
        errors=result.errors,
        warnings=result.warnings,
        persisted=False,
    )
    return success_response(data)
