from __future__ import annotations

from fastapi import status

from app.core.errors import AppError
from app.models.planning_state import PipelineStatus, PlanningStage, PlanningState, TripRequest
from app.providers.gateway import provider_gateway
from app.repositories.planning_state_repository import (
    PlanningStateRepository,
    planning_state_repository,
)
from app.repositories.trip_repository import TripRepository, trip_repository
from app.schemas.errors import ErrorCode
from app.services.destination_context_service import DestinationContextService
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.feedback_service import FeedbackService
from app.services.plan_validator_service import PlanValidatorService
from app.services.stay_transport_service import StayTransportService
from app.services.traveler_profile_service import TravelerProfileService
from app.services.trip_strategy_service import TripStrategyService
from app.services.versioning_service import VersioningService

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
        trip_strategy_service: TripStrategyService | None = None,
        stay_transport_service: StayTransportService | None = None,
        experience_planner_service: ExperiencePlannerService | None = None,
        plan_validator_service: PlanValidatorService | None = None,
        feedback_service: FeedbackService | None = None,
        versioning_service: VersioningService | None = None,
        planning_state_repo: PlanningStateRepository | None = None,
        trip_repo: TripRepository | None = None,
    ) -> None:
        self.traveler_profile_service = traveler_profile_service or TravelerProfileService()
        self.destination_context_service = (
            destination_context_service or DestinationContextService()
        )
        self.trip_strategy_service = trip_strategy_service or TripStrategyService()
        self.stay_transport_service = stay_transport_service or StayTransportService()
        self.experience_planner_service = (
            experience_planner_service or ExperiencePlannerService()
        )
        self.plan_validator_service = plan_validator_service or PlanValidatorService()
        self.feedback_service = feedback_service or FeedbackService()
        self.versioning_service = versioning_service or VersioningService()
        self.planning_state_repository = planning_state_repo or planning_state_repository
        self.trip_repository = trip_repo or trip_repository

    def create_trip(self, trip_request: TripRequest) -> PlanningState:
        planning_state = PlanningState(trip_request=trip_request)
        planning_state.set_active_stage(PlanningStage.CREATE_TRIP)
        planning_state.set_pipeline_status(PipelineStatus.DRAFT)
        planning_state.provider_coverage = provider_gateway.default_provider_coverage()

        self.trip_repository.create(planning_state.trip_id)
        self.planning_state_repository.save(planning_state)
        return planning_state

    def run_traveler_profile_stage(self, planning_state: PlanningState) -> PlanningState:
        planning_state = self.traveler_profile_service.run(planning_state)
        planning_state.set_pipeline_status(PipelineStatus.PROFILE_CREATED)
        return planning_state

    def run_destination_context_stage(self, planning_state: PlanningState) -> PlanningState:
        planning_state = self.destination_context_service.run(planning_state)
        planning_state.set_pipeline_status(PipelineStatus.DESTINATION_CONTEXT_CREATED)
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
            raise AppError(
                code=ErrorCode.TRIP_NOT_FOUND,
                message=f"Trip '{trip_id}' was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
                field="trip_id",
            )

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

        if not planning_state.version_history:
            changed_sections = [
                section
                for section in (
                    "traveler_profile",
                    "destination_context",
                    "trip_strategy",
                    "stay_transport",
                    "experience_plan",
                    "validation_report",
                )
                if getattr(planning_state, section) is not None
            ]
            planning_state = self.versioning_service.create_initial_version(
                planning_state, changed_sections=changed_sections
            )
            self.planning_state_repository.save(planning_state)

        return planning_state

    def apply_feedback(self, trip_id: str, feedback_text: str) -> PlanningState:
        planning_state = self.planning_state_repository.get_by_trip_id(trip_id)
        if planning_state is None:
            raise AppError(
                code=ErrorCode.TRIP_NOT_FOUND,
                message=f"Trip '{trip_id}' was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
                field="trip_id",
            )

        planning_state = self.feedback_service.apply_feedback(planning_state, feedback_text)
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
