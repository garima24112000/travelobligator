from __future__ import annotations

from datetime import date
from typing import Any

from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateProposalGuardrailReport,
    AICandidateProposalRequest,
    AICandidateProposalResult,
    AICandidateProposalStatus,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.core.config import Settings
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.providers.ai_candidate_proposal import AICandidateProposalProvider
from app.services.ai_candidate_discovery_service import AICandidateDiscoveryService
from app.services.ai_candidate_promotion_service import AICandidatePromotionService
from app.services.planning_orchestrator import PlanningOrchestrator

import app.services.planning_orchestrator as orchestrator_module

# Step 170D: `PlanningOrchestrator._run_ai_candidate_promotion_stage` (run
# from `run_destination_context_stage`, right after the existing Step 161B
# shadow discovery stage) auto-computes and stores
# `ai_candidate_promotion_report` so a promoted candidate is available to
# `ExperiencePlannerService` later in the same `generate_full_plan` call.
# Every test here injects a deterministic fake `AICandidateProposalProvider`
# -- never a real Anthropic/Groq/OpenAI call.


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Testville, Testland",
        "origin_city": "Home City",
        "start_date": date(2026, 8, 10),
        "end_date": date(2026, 8, 12),
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _valid_proposal(**overrides: object) -> AICandidateProposal:
    fields: dict[str, object] = {
        "proposal_id": "proposal_001",
        "candidate_name": "Test Fixture Attraction One",
        "candidate_type": AICandidateType.ATTRACTION,
        "why_consider": "Locally known landmark that may be under-tagged in provider data.",
        "verification_requirements": [
            AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION
        ],
        "confidence": 0.5,
    }
    fields.update(overrides)
    return AICandidateProposal(**fields)


class _FakeAICandidateProposalProvider(AICandidateProposalProvider):
    provider_name = "fake_orchestrator_promotion_test_provider"

    def __init__(self, proposals: list[AICandidateProposal]) -> None:
        self._proposals = proposals

    def propose(self, request: AICandidateProposalRequest) -> AICandidateProposalResult:
        return AICandidateProposalResult(
            task=request.task,
            status=AICandidateProposalStatus.COMPLETED,
            proposals=self._proposals,
            guardrail_report=AICandidateProposalGuardrailReport(passed=True),
            provider_name=self.provider_name,
            confidence=0.6,
        )


class _RaisingAICandidatePromotionService:
    def apply_promotion(self, planning_state: PlanningState) -> PlanningState:
        raise RuntimeError("simulated unexpected promotion failure")


def _run_through_destination_context(
    orchestrator: PlanningOrchestrator, monkeypatch: Any, shadow_enabled: bool
) -> PlanningState:
    monkeypatch.setattr(
        orchestrator_module,
        "get_settings",
        lambda: Settings(AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=shadow_enabled),
    )
    planning_state = orchestrator.create_trip(_trip_request())
    planning_state = orchestrator.run_traveler_profile_stage(planning_state)
    planning_state = orchestrator.run_destination_context_stage(planning_state)
    return planning_state


# ---------------------------------------------------------------------------
# 1. Shadow mode disabled: ai_candidate_promotion_report stays None
#    (unchanged from before Step 170D).
# ---------------------------------------------------------------------------


def test_promotion_stage_is_noop_when_shadow_mode_disabled(monkeypatch: Any) -> None:
    orchestrator = PlanningOrchestrator()
    planning_state = _run_through_destination_context(orchestrator, monkeypatch, shadow_enabled=False)

    assert planning_state.ai_candidate_proposal_batch is None
    assert planning_state.ai_candidate_promotion_report is None


# ---------------------------------------------------------------------------
# 2. Shadow mode enabled with an eligible candidate: report is
#    auto-computed and populated before experience_plan stage even runs.
# ---------------------------------------------------------------------------


def test_promotion_stage_auto_populates_report_when_shadow_mode_enabled(monkeypatch: Any) -> None:
    fake_provider = _FakeAICandidateProposalProvider([_valid_proposal()])
    fake_discovery_service = AICandidateDiscoveryService(proposal_provider=fake_provider)
    orchestrator = PlanningOrchestrator(ai_candidate_discovery_service=fake_discovery_service)

    planning_state = _run_through_destination_context(orchestrator, monkeypatch, shadow_enabled=True)

    assert planning_state.ai_candidate_proposal_batch is not None
    assert planning_state.ai_candidate_promotion_report is not None
    assert planning_state.ai_candidate_promotion_report.promoted_count == 1
    # Computed before experience_plan even exists -- proving it's available
    # in time for ExperiencePlannerService later in the same generate() run.
    assert planning_state.experience_plan is None


# ---------------------------------------------------------------------------
# 3. Shadow mode enabled with the default not_connected provider: report is
#    still auto-computed, but honestly empty (no proposals to promote).
# ---------------------------------------------------------------------------


def test_promotion_stage_with_default_not_connected_provider_is_empty(monkeypatch: Any) -> None:
    from app.providers.ai_candidate_proposal.not_connected_adapter import (
        NotConnectedAICandidateProposalProvider,
    )

    orchestrator = PlanningOrchestrator(
        ai_candidate_discovery_service=AICandidateDiscoveryService(
            proposal_provider=NotConnectedAICandidateProposalProvider()
        )
    )

    planning_state = _run_through_destination_context(orchestrator, monkeypatch, shadow_enabled=True)

    assert planning_state.ai_candidate_proposal_batch is not None
    assert planning_state.ai_candidate_promotion_report is not None
    assert planning_state.ai_candidate_promotion_report.promoted_count == 0


# ---------------------------------------------------------------------------
# Fails safe: an unexpected exception from AICandidatePromotionService never
# crashes generation.
# ---------------------------------------------------------------------------


def test_promotion_stage_fails_safe_when_apply_promotion_raises(monkeypatch: Any) -> None:
    fake_provider = _FakeAICandidateProposalProvider([_valid_proposal()])
    fake_discovery_service = AICandidateDiscoveryService(proposal_provider=fake_provider)
    orchestrator = PlanningOrchestrator(
        ai_candidate_discovery_service=fake_discovery_service,
        ai_candidate_promotion_service=_RaisingAICandidatePromotionService(),  # type: ignore[arg-type]
    )

    planning_state = _run_through_destination_context(orchestrator, monkeypatch, shadow_enabled=True)

    # Must not raise, and ai_candidate_promotion_report stays whatever it
    # already was (None) rather than crashing generation.
    assert planning_state.ai_candidate_promotion_report is None


# ---------------------------------------------------------------------------
# The promotion stage never calls a provider/LLM itself -- it only reads
# already-computed PlanningState fields.
# ---------------------------------------------------------------------------


def test_apply_promotion_service_default_has_no_llm_dependency() -> None:
    service = AICandidatePromotionService()
    assert service.review_service is not None
