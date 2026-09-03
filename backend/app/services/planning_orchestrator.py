from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.config import get_settings
from app.core.errors import trip_not_found_error
from app.models.accommodation import AccommodationSearchResult, AccommodationSearchStatus
from app.models.ai_candidate_proposal import AICandidateProposalBatch
from app.models.candidate_grounding import CandidateGroundingBatch
from app.models.common import ProviderStatus
from app.models.planning_state import (
    GENERATION_STAGE_KEYS,
    GenerationProgress,
    GenerationStageStatus,
    PipelineStatus,
    PlanningStage,
    PlanningState,
    TripRequest,
)
from app.models.routing import (
    MovementDataProvenance,
    RouteAwareSequencingReport,
    RouteFeasibilityReport,
    TravelTimeBufferReport,
)
from app.providers.gateway import provider_gateway
from app.repositories.planning_state_repository import (
    PlanningStateRepository,
    planning_state_repository,
)
from app.repositories.trip_repository import TripRepository, trip_repository
from app.services.accommodation_inventory_service import AccommodationInventoryService
from app.services.ai_candidate_discovery_service import AICandidateDiscoveryService
from app.services.candidate_quality_service import CandidateQualityService
from app.services.destination_context_service import DestinationContextService
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.feedback_service import FeedbackService
from app.services.plan_diff_preview_service import PlanDiffPreviewService
from app.services.plan_validator_service import PlanValidatorService
from app.services.regeneration_readiness_service import RegenerationReadinessService
from app.services.route_aware_sequencing_service import RouteAwareSequencingService
from app.services.route_feasibility_service import RouteFeasibilityService
from app.services.stay_transport_service import StayTransportService
from app.services.travel_time_buffer_service import TravelTimeBufferService
from app.services.traveler_profile_service import TravelerProfileService
from app.services.trip_strategy_service import TripStrategyService
from app.services.versioning_service import VersioningService

logger = logging.getLogger(__name__)

_READINESS_TO_PIPELINE_STATUS = {
    "ready": PipelineStatus.VALIDATED,
    "needs_review": PipelineStatus.NEEDS_REVIEW,
    "blocked": PipelineStatus.BLOCKED,
}

# Maps RouteFeasibilityReport.status (Step 165E) onto the existing
# ProviderCoverage.routes string field -- honest reporting only. "success"
# is set only when every scheduled leg's RouteResult.status == success;
# anything else (partial/not_connected/failed/unavailable) is reported
# exactly as such, never upgraded to imply route data is available when it
# isn't.
_ROUTE_STATUS_TO_COVERAGE_VALUE = {
    ProviderStatus.SUCCESS: "success",
    ProviderStatus.PARTIAL: "partial",
    ProviderStatus.NOT_CONNECTED: "not_connected",
    ProviderStatus.FAILED: "failed",
    ProviderStatus.UNAVAILABLE: "unavailable",
}

# Maps AccommodationSearchResult.status (Step 167D) onto the existing
# ProviderCoverage.hotel_prices string field -- honest reporting only.
# `hotel_prices` (not `accommodations`) is used deliberately: `accommodations`
# already carries the OSM-backed accommodation-location-candidate coverage
# value ("open_poi_available"/"not_connected", set by StayTransportService/
# DestinationContextService via ProviderCoverageService) -- an OSM POI is
# never a bookable offer, so this bookable-inventory result is never
# written to that same field. "success" is only ever set when the provider
# both reported success *and* returned at least one real offer -- a
# `success` result with zero offers is reported "unavailable" instead,
# never upgraded to imply bookable inventory exists when it doesn't.
_ACCOMMODATION_STATUS_TO_COVERAGE_VALUE = {
    AccommodationSearchStatus.SUCCESS: "success",
    AccommodationSearchStatus.NOT_CONNECTED: "not_connected",
    AccommodationSearchStatus.FAILED: "failed",
    AccommodationSearchStatus.UNAVAILABLE: "unavailable",
}


def _accommodation_coverage_value(result: AccommodationSearchResult) -> str:
    if result.status == AccommodationSearchStatus.SUCCESS and not result.offers:
        return "unavailable"
    return _ACCOMMODATION_STATUS_TO_COVERAGE_VALUE.get(result.status, "not_connected")

# Human-readable labels for GENERATION_STAGE_KEYS (Step 163B). Purely
# cosmetic text for `GenerationProgress.current_stage_label` -- never a
# travel fact, never route/flight/booking wording.
_GENERATION_STAGE_LABELS: dict[str, str] = {
    "traveler_profile": "Building traveler profile",
    "destination_context": "Gathering destination context",
    "candidate_quality": "Scoring candidate quality",
    "ai_candidate_shadow": "Running AI candidate shadow check",
    "trip_strategy": "Building trip strategy",
    "stay_transport": "Choosing stay and transport",
    "experience_plan": "Building experience plan",
    "validation": "Validating plan",
    "post_processing": "Finalizing plan bookkeeping",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Step 166D hardening: safe fallback reports for when
# RouteFeasibilityService/RouteAwareSequencingService/TravelTimeBufferService
# raise an unexpected exception (as opposed to an honest non-`success`
# `RouteResult`, which every real routing adapter already returns on its
# own failure). Each stage service already contains an exception from its
# own `ProviderGateway.get_route` call (see each service's own
# `_safe_get_route` helper); these fallbacks are a second line of defense
# for a genuinely unexpected bug elsewhere in a service's `build_report`/
# `apply_report` (e.g. constructing the report object itself), so
# `generate_full_plan` never fails just because route-dependent reporting
# did. Every fallback is `status=failed`, empty, and carries no raw
# exception text or provider payload -- never a fabricated leg,
# suggestion, or buffer.
def _failed_route_feasibility_report(provider_name: str) -> RouteFeasibilityReport:
    return RouteFeasibilityReport(
        status=ProviderStatus.FAILED,
        legs=[],
        provider=provider_name,
        route_data_source="not_connected",
        generated_at=_utc_now(),
        movement_data_provenance=MovementDataProvenance.FAILED,
    )


def _failed_route_aware_sequencing_report() -> RouteAwareSequencingReport:
    return RouteAwareSequencingReport(
        status=ProviderStatus.FAILED,
        suggestions=[],
        generated_at=_utc_now(),
        is_shadow_only=True,
        applied_to_itinerary=False,
        movement_data_provenance=MovementDataProvenance.FAILED,
    )


def _failed_travel_time_buffer_report() -> TravelTimeBufferReport:
    return TravelTimeBufferReport(
        status=ProviderStatus.FAILED,
        buffers=[],
        generated_at=_utc_now(),
        uses_provider_backed_routes=True,
        movement_data_provenance=MovementDataProvenance.FAILED,
    )


def _failed_accommodation_inventory_result(provider_name: str) -> AccommodationSearchResult:
    return AccommodationSearchResult(
        provider=provider_name,
        status=AccommodationSearchStatus.FAILED,
        offers=[],
        message="Accommodation inventory computation failed unexpectedly.",
    )


class PlanningOrchestrator:
    """Controls the full planning pipeline (docs/14_backend_architecture.md
    section 7).

    The orchestrator holds no provider-specific or product logic itself. It
    loads/saves PlanningState, runs stage services in the documented order,
    tracks pipeline status, and persists after each stage
    (docs/14_backend_architecture.md section 28).
    """

    def __init__(
        self,
        traveler_profile_service: TravelerProfileService | None = None,
        destination_context_service: DestinationContextService | None = None,
        candidate_quality_service: CandidateQualityService | None = None,
        trip_strategy_service: TripStrategyService | None = None,
        stay_transport_service: StayTransportService | None = None,
        accommodation_inventory_service: AccommodationInventoryService | None = None,
        experience_planner_service: ExperiencePlannerService | None = None,
        plan_validator_service: PlanValidatorService | None = None,
        route_feasibility_service: RouteFeasibilityService | None = None,
        route_aware_sequencing_service: RouteAwareSequencingService | None = None,
        travel_time_buffer_service: TravelTimeBufferService | None = None,
        feedback_service: FeedbackService | None = None,
        versioning_service: VersioningService | None = None,
        plan_diff_preview_service: PlanDiffPreviewService | None = None,
        regeneration_readiness_service: RegenerationReadinessService | None = None,
        ai_candidate_discovery_service: AICandidateDiscoveryService | None = None,
        planning_state_repo: PlanningStateRepository | None = None,
        trip_repo: TripRepository | None = None,
    ) -> None:
        self.traveler_profile_service = traveler_profile_service or TravelerProfileService()
        self.destination_context_service = (
            destination_context_service or DestinationContextService()
        )
        self.candidate_quality_service = candidate_quality_service or CandidateQualityService()
        self.trip_strategy_service = trip_strategy_service or TripStrategyService()
        self.stay_transport_service = stay_transport_service or StayTransportService()
        self.accommodation_inventory_service = (
            accommodation_inventory_service or AccommodationInventoryService()
        )
        self.experience_planner_service = (
            experience_planner_service or ExperiencePlannerService()
        )
        self.plan_validator_service = plan_validator_service or PlanValidatorService()
        self.route_feasibility_service = route_feasibility_service or RouteFeasibilityService()
        self.route_aware_sequencing_service = (
            route_aware_sequencing_service or RouteAwareSequencingService()
        )
        self.travel_time_buffer_service = (
            travel_time_buffer_service or TravelTimeBufferService()
        )
        self.feedback_service = feedback_service or FeedbackService()
        self.versioning_service = versioning_service or VersioningService()
        self.plan_diff_preview_service = plan_diff_preview_service or PlanDiffPreviewService()
        self.regeneration_readiness_service = (
            regeneration_readiness_service or RegenerationReadinessService()
        )
        self.ai_candidate_discovery_service = (
            ai_candidate_discovery_service or AICandidateDiscoveryService()
        )
        self.planning_state_repository = planning_state_repo or planning_state_repository
        self.trip_repository = trip_repo or trip_repository

    def create_trip(self, trip_request: TripRequest) -> PlanningState:
        planning_state = PlanningState(trip_request=trip_request)
        planning_state.set_active_stage(PlanningStage.CREATE_TRIP)
        planning_state.set_pipeline_status(PipelineStatus.DRAFT)
        planning_state.provider_coverage = provider_gateway.default_provider_coverage()
        # Idle backend pipeline stage-progress bookkeeping (Step 163B) --
        # every new trip starts with an explicit idle GenerationProgress
        # rather than leaving the field None, so GET
        # /trips/{trip_id}/generation-progress always has real state to read.
        planning_state.generation_progress = GenerationProgress()
        # Recomputed from scratch every time (Step 135); on a brand-new trip
        # this just confirms the "blocked, no generated plan yet" gate.
        planning_state = self.regeneration_readiness_service.recompute(planning_state)

        self.trip_repository.create(planning_state.trip_id)
        self.planning_state_repository.save(planning_state)
        return planning_state

    # -- Step 163B: backend pipeline stage-progress bookkeeping helpers --
    # These only ever mutate `planning_state.generation_progress`. They
    # never touch any other PlanningState field, never call a provider/AI/
    # LangGraph, and never change stage ordering or stage outputs -- see
    # generate_full_plan for how they're threaded around the existing,
    # unmodified stage calls.

    def _start_generation_progress(self, planning_state: PlanningState) -> PlanningState:
        planning_state.generation_progress = GenerationProgress(
            status=GenerationStageStatus.GENERATING,
            message="Backend pipeline generation started.",
        )
        return planning_state

    def _mark_stage_started(self, planning_state: PlanningState, stage_key: str) -> PlanningState:
        progress = planning_state.generation_progress
        if progress is None:
            progress = GenerationProgress()
            planning_state.generation_progress = progress
        label = _GENERATION_STAGE_LABELS.get(stage_key, stage_key)
        progress.status = GenerationStageStatus.GENERATING
        progress.current_stage = stage_key
        progress.current_stage_label = label
        progress.message = f"Running backend pipeline stage: {label}."
        progress.updated_at = _utc_now()
        return planning_state

    def _mark_stage_finished(self, planning_state: PlanningState, stage_key: str) -> PlanningState:
        progress = planning_state.generation_progress
        if progress is None:
            progress = GenerationProgress()
            planning_state.generation_progress = progress
        label = _GENERATION_STAGE_LABELS.get(stage_key, stage_key)
        if stage_key not in progress.completed_stages:
            progress.completed_stages.append(stage_key)
        progress.current_stage = stage_key
        progress.current_stage_label = label
        total_stages = progress.total_stages or len(GENERATION_STAGE_KEYS)
        progress.progress_percent = min(
            100, round(100 * len(progress.completed_stages) / total_stages)
        )
        progress.message = f"Finished backend pipeline stage: {label}."
        progress.updated_at = _utc_now()
        return planning_state

    def _finish_generation_progress(self, planning_state: PlanningState) -> PlanningState:
        progress = planning_state.generation_progress
        if progress is None:
            progress = GenerationProgress()
            planning_state.generation_progress = progress
        progress.status = GenerationStageStatus.COMPLETED
        progress.progress_percent = 100
        progress.message = "Backend pipeline generation completed."
        progress.updated_at = _utc_now()
        return planning_state

    def _fail_generation_progress(self, planning_state: PlanningState) -> PlanningState:
        progress = planning_state.generation_progress
        if progress is None:
            progress = GenerationProgress()
            planning_state.generation_progress = progress
        # `current_stage`/`current_stage_label` are deliberately left as-is:
        # they still point at whichever stage was running when the failure
        # happened, which is more useful than clearing them.
        progress.status = GenerationStageStatus.FAILED
        progress.message = "Backend pipeline generation failed."
        progress.updated_at = _utc_now()
        return planning_state

    def run_traveler_profile_stage(self, planning_state: PlanningState) -> PlanningState:
        planning_state = self.traveler_profile_service.run(planning_state)
        planning_state.set_pipeline_status(PipelineStatus.PROFILE_CREATED)
        return planning_state

    def run_destination_context_stage(self, planning_state: PlanningState) -> PlanningState:
        planning_state = self.destination_context_service.run(planning_state)
        # Deterministic pre-ranking metadata only (Step 156A/156B,
        # docs/18_candidate_quality.md) -- scores destination_context's
        # existing candidates; never a provider/AI/LLM/LangGraph/LangSmith
        # call, never mutates
        # candidate_pois/candidate_restaurants/candidate_accommodation_pois.
        # ExperiencePlannerService later consumes this report (when present)
        # for quality-aware, trust-over-fullness scheduling (Step 156C/156E).
        planning_state.candidate_quality_report = self.candidate_quality_service.build_report(
            planning_state
        )
        planning_state.set_pipeline_status(PipelineStatus.DESTINATION_CONTEXT_CREATED)
        # Optional, config-gated shadow-mode integration (Step 161B) -- a
        # pure no-op unless AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED is set.
        # See _run_ai_candidate_discovery_shadow_stage's docstring.
        planning_state = self._run_ai_candidate_discovery_shadow_stage(planning_state)
        return planning_state

    def _run_ai_candidate_discovery_shadow_stage(self, planning_state: PlanningState) -> PlanningState:
        """Optional, config-gated shadow-mode integration (Step 161B,
        docs/13_llm_reasoning_pipeline.md section 40,
        docs/14_backend_architecture.md section 26).

        Gated by `Settings.ai_candidate_discovery_shadow_mode_enabled`
        (default False). When disabled, this is a pure no-op:
        `planning_state.ai_candidate_proposal_batch` and
        `.candidate_grounding_batch` stay `None`, exactly as before this
        step. When enabled and `destination_context` already exists, it
        runs `AICandidateDiscoveryService.dry_run` and stores the validated
        request/result pairs it returns, for inspection only.

        It never mutates `destination_context` candidates, never feeds
        proposals or `GroundedCandidate`s into `ExperiencePlannerService` or
        `CandidateQualityService`, never changes `validation_report`
        readiness or regeneration, and never touches
        `provider_coverage`/`data_sources_used`. If `dry_run` raises for any
        reason, this fails safe: the exception is swallowed, nothing is
        stored, and generation continues completely unaffected -- never a
        fabricated proposal or grounded candidate.
        """
        if not get_settings().ai_candidate_discovery_shadow_mode_enabled:
            return planning_state

        if planning_state.destination_context is None:
            return planning_state

        try:
            dry_run_result = self.ai_candidate_discovery_service.dry_run(planning_state)
        except Exception:
            return planning_state

        planning_state.ai_candidate_proposal_batch = AICandidateProposalBatch(
            request=dry_run_result.proposal_request,
            result=dry_run_result.proposal_result,
        )
        planning_state.candidate_grounding_batch = CandidateGroundingBatch(
            request=dry_run_result.grounding_request,
            result=dry_run_result.grounding_result,
        )
        return planning_state

    def run_trip_strategy_stage(self, planning_state: PlanningState) -> PlanningState:
        planning_state = self.trip_strategy_service.run(planning_state)
        planning_state.set_pipeline_status(PipelineStatus.STRATEGY_CREATED)
        return planning_state

    def _build_accommodation_inventory_report_safe(self, planning_state: PlanningState) -> None:
        """Builds and stores `accommodation_inventory_report` plus the
        derived `ProviderCoverage.hotel_prices` value (Step 167D), failing
        safe (mirroring Step 166D's route-report hardening): an unexpected
        exception from `AccommodationInventoryService.build_report` is
        never allowed to crash generation. On such a failure, a safe
        `status=failed` result with no offers is stored instead -- never a
        fabricated property/price/rating/availability/booking link, and
        never raw exception text or a provider payload in any stored
        field.
        """
        try:
            planning_state.accommodation_inventory_report = (
                self.accommodation_inventory_service.build_report(planning_state)
            )
        except Exception:
            logger.warning(
                "AccommodationInventoryService.build_report failed unexpectedly; storing a "
                "failed result so generation can continue.",
                exc_info=True,
            )
            provider_name = getattr(
                self.accommodation_inventory_service.gateway.accommodation_inventory,
                "provider_name",
                "accommodation_inventory_provider",
            )
            planning_state.accommodation_inventory_report = _failed_accommodation_inventory_result(
                provider_name
            )
        planning_state.provider_coverage.hotel_prices = _accommodation_coverage_value(
            planning_state.accommodation_inventory_report
        )

    def run_stay_transport_stage(self, planning_state: PlanningState) -> PlanningState:
        planning_state = self.stay_transport_service.run(planning_state)
        # Step 167D: bookable accommodation inventory report, computed
        # after StayTransportService (which still cannot recommend a real
        # stay area/accommodation option without a connected provider) and
        # before ExperiencePlannerService/PlanValidatorService run. Never
        # schedules lodging into the itinerary and never adds hotel
        # recommendation logic -- this only records an honest inventory
        # status. Saved alongside stay_transport by generate_full_plan's
        # existing save-after-each-stage cadence; no extra save call
        # needed here.
        self._build_accommodation_inventory_report_safe(planning_state)
        planning_state.set_pipeline_status(PipelineStatus.STAY_TRANSPORT_CREATED)
        return planning_state

    def _build_route_feasibility_report_safe(self, planning_state: PlanningState) -> None:
        """Builds and stores `route_feasibility_report` plus the derived
        `ProviderCoverage.routes` value (Step 165E), failing safe (Step
        166D hardening): an unexpected exception from
        `RouteFeasibilityService.build_report` is never allowed to crash
        generation. On such a failure, a safe `status=failed` report with
        no legs is stored instead -- never a fabricated leg, and never raw
        exception text or a provider payload in any stored field.
        """
        try:
            planning_state.route_feasibility_report = self.route_feasibility_service.build_report(
                planning_state
            )
        except Exception:
            logger.warning(
                "RouteFeasibilityService.build_report failed unexpectedly; storing a "
                "failed report so generation can continue.",
                exc_info=True,
            )
            provider_name = getattr(
                self.route_feasibility_service.gateway.routing, "provider_name", "routing_provider"
            )
            planning_state.route_feasibility_report = _failed_route_feasibility_report(provider_name)
        planning_state.provider_coverage.routes = _ROUTE_STATUS_TO_COVERAGE_VALUE.get(
            planning_state.route_feasibility_report.status, "not_connected"
        )

    def run_experience_plan_stage(self, planning_state: PlanningState) -> PlanningState:
        planning_state = self.experience_planner_service.run(planning_state)
        # Step 165E: route feasibility for consecutive scheduled experiences
        # within each day, computed after experience_plan exists and before
        # PlanValidatorService runs. Never reorders/drops a scheduled
        # experience -- see RouteFeasibilityReport's docstring for the
        # route-aware-scheduling boundary (Section 166). Saved alongside
        # experience_plan by generate_full_plan's existing
        # save-after-each-stage cadence; no extra save call needed here.
        self._build_route_feasibility_report_safe(planning_state)

        # Step 166A: shadow/report-only route-aware day-sequencing
        # suggestions, computed after route_feasibility_report and before
        # PlanValidatorService runs. Never reorders/drops a scheduled
        # experience and never fed back into ExperiencePlannerService --
        # see RouteAwareSequencingReport's own docstring
        # (is_shadow_only=True, applied_to_itinerary=False, always). Fails
        # safe (Step 166D): an unexpected exception is never allowed to
        # crash generation.
        try:
            planning_state.route_aware_sequencing_report = (
                self.route_aware_sequencing_service.build_report(planning_state)
            )
        except Exception:
            logger.warning(
                "RouteAwareSequencingService.build_report failed unexpectedly; storing a "
                "failed report so generation can continue.",
                exc_info=True,
            )
            planning_state.route_aware_sequencing_report = _failed_route_aware_sequencing_report()

        # Step 166B: config-gated application of the above report onto the
        # real schedule. Disabled by default (Settings.
        # route_aware_scheduling_enabled=False) -- when disabled, this is a
        # pure no-op and the scheduled itinerary order stays exactly as
        # ExperiencePlannerService left it. When enabled,
        # RouteAwareSequencingService.apply_report only ever reorders a day
        # whose suggestion is provider-backed, successful, and past the
        # configured minimum real improvement -- see its own docstring for
        # the full safety contract. Fails safe (Step 166D): an unexpected
        # exception here leaves the scheduled order exactly as it was and
        # never crashes generation.
        settings = get_settings()
        if settings.route_aware_scheduling_enabled:
            try:
                applied = self.route_aware_sequencing_service.apply_report(
                    planning_state,
                    planning_state.route_aware_sequencing_report,
                    settings.route_aware_scheduling_min_improvement_seconds,
                )
            except Exception:
                logger.warning(
                    "RouteAwareSequencingService.apply_report failed unexpectedly; "
                    "leaving the scheduled order unchanged.",
                    exc_info=True,
                )
                applied = False
            if applied:
                # Keep route_feasibility_report consistent with the
                # now-reordered schedule rather than leaving it stale --
                # legs are built from consecutive scheduled pairs, which
                # just changed for at least one day.
                self._build_route_feasibility_report_safe(planning_state)

        # Step 166C: provider-backed travel-time buffer reporting for
        # consecutive scheduled experiences, computed after
        # route_feasibility_report and after any Step 166B config-gated
        # route-aware-scheduling application above -- so buffers always
        # reflect this run's final scheduled order. Never reorders/drops a
        # scheduled experience, never inserts a fake travel segment, and
        # never fabricates a duration/distance/buffer -- see
        # TravelTimeBufferReport's own docstring. Fails safe (Step 166D):
        # an unexpected exception is never allowed to crash generation.
        try:
            planning_state.travel_time_buffer_report = self.travel_time_buffer_service.build_report(
                planning_state
            )
        except Exception:
            logger.warning(
                "TravelTimeBufferService.build_report failed unexpectedly; storing a "
                "failed report so generation can continue.",
                exc_info=True,
            )
            planning_state.travel_time_buffer_report = _failed_travel_time_buffer_report()

        planning_state.set_pipeline_status(PipelineStatus.EXPERIENCE_PLAN_CREATED)
        return planning_state

    def run_validation_stage(self, planning_state: PlanningState) -> PlanningState:
        planning_state = self.plan_validator_service.run(planning_state)
        report = planning_state.validation_report
        pipeline_status = (
            _READINESS_TO_PIPELINE_STATUS.get(report.readiness_status.value, PipelineStatus.NEEDS_REVIEW)
            if report is not None
            else PipelineStatus.NEEDS_REVIEW
        )
        planning_state.set_pipeline_status(pipeline_status)
        return planning_state

    def generate_full_plan(self, trip_id: str, force_regenerate: bool = False) -> PlanningState:
        planning_state = self.planning_state_repository.get_by_trip_id(trip_id)
        if planning_state is None:
            raise trip_not_found_error(trip_id)

        planning_state.set_pipeline_status(PipelineStatus.GENERATING)
        # Fresh progress bookkeeping for this run (Step 163B) -- resetting
        # completed_stages/progress_percent here means re-generating an
        # already-generated trip always reports this run's progress, never
        # stale counts appended on top of a previous run's.
        planning_state = self._start_generation_progress(planning_state)
        self.planning_state_repository.save(planning_state)

        try:
            # Stage order/outputs are unchanged from before Step 163B -- the
            # (stage_key, run_stage) pairing only adds progress bookkeeping
            # around each existing call, in the same order, with the same
            # save-after-each-stage cadence.
            stage_runners = (
                ("traveler_profile", self.run_traveler_profile_stage),
                ("destination_context", self.run_destination_context_stage),
                ("trip_strategy", self.run_trip_strategy_stage),
                ("stay_transport", self.run_stay_transport_stage),
                ("experience_plan", self.run_experience_plan_stage),
                ("validation", self.run_validation_stage),
            )
            for stage_key, run_stage in stage_runners:
                planning_state = self._mark_stage_started(planning_state, stage_key)
                planning_state = run_stage(planning_state)
                planning_state = self._mark_stage_finished(planning_state, stage_key)
                # "destination_context" also covers two real sub-steps
                # (candidate_quality scoring, then the optional AI candidate
                # shadow stage) that run_destination_context_stage already
                # performs internally -- recorded here, without touching
                # run_destination_context_stage itself, since both are
                # already finished by the time it returns.
                if stage_key == "destination_context":
                    planning_state = self._mark_stage_finished(planning_state, "candidate_quality")
                    planning_state = self._mark_stage_finished(planning_state, "ai_candidate_shadow")
                self.planning_state_repository.save(planning_state)

            planning_state = self._mark_stage_started(planning_state, "post_processing")
            # Idempotent: records the "v1" version item only the first time a
            # trip is generated. Calling generate again for the same trip does
            # not append a duplicate v1 entry (see VersioningService.create_initial_version).
            planning_state = self.versioning_service.create_initial_version(planning_state)
            # Recomputed from scratch every time (Step 132) so it always
            # reflects the just-recorded version_history alongside any existing
            # feedback_history/user_locks.
            planning_state = self.plan_diff_preview_service.recompute(planning_state)
            # Recomputed from scratch every time (Step 135) so it always
            # reflects the just-recorded version_history.
            planning_state = self.regeneration_readiness_service.recompute(planning_state)
            planning_state = self._mark_stage_finished(planning_state, "post_processing")
            planning_state = self._finish_generation_progress(planning_state)
            self.planning_state_repository.save(planning_state)
        except Exception:
            # Mark failed before re-raising -- never swallow or replace the
            # original exception, and never change existing error behavior
            # otherwise (Step 163B).
            planning_state = self._fail_generation_progress(planning_state)
            self.planning_state_repository.save(planning_state)
            raise

        return planning_state

    def apply_feedback(self, trip_id: str, feedback_text: str) -> PlanningState:
        planning_state = self.planning_state_repository.get_by_trip_id(trip_id)
        if planning_state is None:
            raise trip_not_found_error(trip_id)

        planning_state = self.feedback_service.apply_feedback(planning_state, feedback_text)
        # Recomputed from scratch every time (Step 132) so it always
        # reflects the just-appended feedback_history.
        planning_state = self.plan_diff_preview_service.recompute(planning_state)
        # Recomputed from scratch every time (Step 135) so it always
        # reflects the just-appended feedback_history.
        planning_state = self.regeneration_readiness_service.recompute(planning_state)
        self.planning_state_repository.save(planning_state)
        return planning_state

    def rerun_affected_stages(
        self, planning_state: PlanningState, affected_stages: list[PlanningStage]
    ) -> PlanningState:
        stage_runner_by_stage = {
            PlanningStage.TRAVELER_PROFILE: self.run_traveler_profile_stage,
            PlanningStage.DESTINATION_CONTEXT: self.run_destination_context_stage,
            PlanningStage.TRIP_STRATEGY: self.run_trip_strategy_stage,
            PlanningStage.STAY_TRANSPORT: self.run_stay_transport_stage,
            PlanningStage.EXPERIENCE_PLAN: self.run_experience_plan_stage,
            PlanningStage.VALIDATION: self.run_validation_stage,
        }

        for stage in affected_stages:
            run_stage = stage_runner_by_stage.get(stage)
            if run_stage is None:
                continue
            planning_state = run_stage(planning_state)
            self.planning_state_repository.save(planning_state)

        return planning_state


planning_orchestrator = PlanningOrchestrator()
