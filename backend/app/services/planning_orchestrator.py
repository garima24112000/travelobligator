from __future__ import annotations

from app.core.config import get_settings
from app.core.errors import trip_not_found_error
from app.models.ai_candidate_proposal import AICandidateProposalBatch
from app.models.candidate_grounding import CandidateGroundingBatch
from app.models.planning_state import PipelineStatus, PlanningStage, PlanningState, TripRequest
from app.providers.gateway import provider_gateway
from app.repositories.planning_state_repository import (
    PlanningStateRepository,
    planning_state_repository,
)
from app.repositories.trip_repository import TripRepository, trip_repository
from app.services.ai_candidate_discovery_service import AICandidateDiscoveryService
from app.services.candidate_quality_service import CandidateQualityService
from app.services.destination_context_service import DestinationContextService
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.feedback_service import FeedbackService
from app.services.plan_diff_preview_service import PlanDiffPreviewService
from app.services.plan_validator_service import PlanValidatorService
from app.services.regeneration_readiness_service import RegenerationReadinessService
from app.services.stay_transport_service import StayTransportService
from app.services.traveler_profile_service import TravelerProfileService
from app.services.trip_strategy_service import TripStrategyService
from app.services.versioning_service import VersioningService

# AI candidate discovery shadow stage (Step 161B, docs/13_llm_reasoning_
# pipeline.md section 40, docs/14_backend_architecture.md section 25).
# PlanningOrchestrator now imports AICandidateDiscoveryService (Step 160B)
# -- unlike Steps 160D/160E, which deliberately kept it unwired -- but only
# to run it behind Settings.ai_candidate_discovery_shadow_mode_enabled
# (default False, docs/13_llm_reasoning_pipeline.md section 40). Disabled
# (the default), _run_ai_candidate_discovery_shadow_stage is a no-op and
# every Step 160C storage field stays None exactly as before. Enabled, it
# only ever stores AICandidateDiscoveryService.dry_run's already-validated
# AICandidateProposalBatch/CandidateGroundingBatch on planning_state -- it
# never feeds ExperiencePlannerService, CandidateQualityService, validation
# readiness, or regeneration, and never mutates any other planning_state
# field. See PlanningOrchestrator._run_ai_candidate_discovery_shadow_stage.

_READINESS_TO_PIPELINE_STATUS = {
    "ready": PipelineStatus.VALIDATED,
    "needs_review": PipelineStatus.NEEDS_REVIEW,
    "blocked": PipelineStatus.BLOCKED,
}


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
        experience_planner_service: ExperiencePlannerService | None = None,
        plan_validator_service: PlanValidatorService | None = None,
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
        self.experience_planner_service = (
            experience_planner_service or ExperiencePlannerService()
        )
        self.plan_validator_service = plan_validator_service or PlanValidatorService()
        self.feedback_service = feedback_service or FeedbackService()
        self.versioning_service = versioning_service or VersioningService()
        self.plan_diff_preview_service = plan_diff_preview_service or PlanDiffPreviewService()
        self.regeneration_readiness_service = (
            regeneration_readiness_service or RegenerationReadinessService()
        )
        # Shadow-stage-only dependency (Step 161B) -- constructing this here
        # never makes a network/LLM call; AICandidateDiscoveryService.dry_run
        # is only ever invoked from _run_ai_candidate_discovery_shadow_stage,
        # and only when shadow mode is enabled.
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
        # Recomputed from scratch every time (Step 135); on a brand-new trip
        # this just confirms the "blocked, no generated plan yet" gate.
        planning_state = self.regeneration_readiness_service.recompute(planning_state)

        self.trip_repository.create(planning_state.trip_id)
        self.planning_state_repository.save(planning_state)
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
        # Shadow artifact stage only (Step 161B), not a planning stage: a
        # config-gated no-op unless Settings.ai_candidate_discovery_shadow_
        # mode_enabled is True. See _run_ai_candidate_discovery_shadow_stage.
        planning_state = self._run_ai_candidate_discovery_shadow_stage(planning_state)
        planning_state.set_pipeline_status(PipelineStatus.DESTINATION_CONTEXT_CREATED)
        return planning_state

    def _run_ai_candidate_discovery_shadow_stage(self, planning_state: PlanningState) -> PlanningState:
        """AI candidate discovery shadow stage (Step 161B,
        docs/13_llm_reasoning_pipeline.md section 40). Not a planning stage
        -- it never sets pipeline_status/active_stage itself, and it always
        runs after destination_context already exists.

        No-ops (leaves both Step 160C fields exactly as they already were)
        unless `Settings.ai_candidate_discovery_shadow_mode_enabled` is
        True. When enabled, this stores
        `AICandidateDiscoveryService.dry_run`'s already-validated
        `AICandidateProposalBatch`/`CandidateGroundingBatch` on
        `planning_state` -- nothing else. It never passes proposals or
        grounded candidates to `ExperiencePlannerService`/
        `CandidateQualityService`, never touches `experience_plan`,
        `validation_report`, `provider_coverage`, or `data_sources_used`,
        and never fabricates a proposal or grounded candidate. If
        `dry_run` (or assembling the batches from its result) raises
        unexpectedly, this is caught here so a shadow-only failure can
        never crash trip generation -- both fields are simply left as they
        already were, never partially populated.
        """
        if not get_settings().ai_candidate_discovery_shadow_mode_enabled:
            return planning_state

        try:
            dry_run_result = self.ai_candidate_discovery_service.dry_run(planning_state)
            proposal_batch = AICandidateProposalBatch(
                request=dry_run_result.proposal_request,
                result=dry_run_result.proposal_result,
            )
            grounding_batch = CandidateGroundingBatch(
                request=dry_run_result.grounding_request,
                result=dry_run_result.grounding_result,
            )
        except Exception:
            return planning_state

        planning_state.ai_candidate_proposal_batch = proposal_batch
        planning_state.candidate_grounding_batch = grounding_batch
        return planning_state

    def run_trip_strategy_stage(self, planning_state: PlanningState) -> PlanningState:
        planning_state = self.trip_strategy_service.run(planning_state)
        planning_state.set_pipeline_status(PipelineStatus.STRATEGY_CREATED)
        return planning_state

    def run_stay_transport_stage(self, planning_state: PlanningState) -> PlanningState:
        planning_state = self.stay_transport_service.run(planning_state)
        planning_state.set_pipeline_status(PipelineStatus.STAY_TRANSPORT_CREATED)
        return planning_state

    def run_experience_plan_stage(self, planning_state: PlanningState) -> PlanningState:
        planning_state = self.experience_planner_service.run(planning_state)
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
        self.planning_state_repository.save(planning_state)

        stage_runners = (
            self.run_traveler_profile_stage,
            self.run_destination_context_stage,
            self.run_trip_strategy_stage,
            self.run_stay_transport_stage,
            self.run_experience_plan_stage,
            self.run_validation_stage,
        )
        for run_stage in stage_runners:
            planning_state = run_stage(planning_state)
            self.planning_state_repository.save(planning_state)

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
        self.planning_state_repository.save(planning_state)

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
