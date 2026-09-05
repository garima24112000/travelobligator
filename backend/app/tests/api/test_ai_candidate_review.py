from __future__ import annotations

import inspect
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.services.planning_orchestrator as orchestrator_module
from app.core.config import Settings
from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateProposalGuardrailReport,
    AICandidateProposalRequest,
    AICandidateProposalResult,
    AICandidateProposalStatus,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.providers.ai_candidate_proposal import AICandidateProposalProvider
from app.providers.ai_candidate_proposal.anthropic_adapter import AnthropicAICandidateProposalProvider
from app.providers.ai_candidate_proposal.groq_adapter import GroqAICandidateProposalProvider
from app.services.ai_candidate_discovery_service import AICandidateDiscoveryService

# API tests for the read-only AI candidate review endpoint
# (`GET /trips/{trip_id}/ai-candidate-review`, Step 170A, extended with
# deterministic promotion-eligibility rules in Step 170B). This endpoint
# only ever reads whatever the existing, config-gated Step 161B shadow
# stage already stored on `PlanningState` (plus the existing
# `candidate_quality_report`) -- these tests enable that shadow stage with
# deterministic fake proposal providers (never a real Anthropic/Groq/OpenAI
# call) to exercise the review report, including eligibility, end to end.

_FORBIDDEN_TEXT = (
    "best hotels",
    "top rated",
    "booking-ready",
    "guaranteed",
    "verified route time",
    "safe area",
    "final recommendation",
    "updated plan",
    "diff generated",
    "travel-ready",
)


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


def _unmatched_proposal(**overrides: object) -> AICandidateProposal:
    fields: dict[str, object] = {
        "proposal_id": "proposal_002",
        "candidate_name": "Completely Unmatched Fictional Place",
        "candidate_type": AICandidateType.NEIGHBORHOOD,
        "why_consider": "Mentioned by travelers online as an under-documented area.",
        "verification_requirements": [
            AICandidateVerificationRequirement.MUST_GROUND_BY_DESTINATION_CONTAINMENT
        ],
        "confidence": 0.4,
    }
    fields.update(overrides)
    return AICandidateProposal(**fields)


def _accommodation_proposal(**overrides: object) -> AICandidateProposal:
    """Matches `DeterministicTestPlacesProvider`'s real accommodation POI
    fixture ("Test Fixture Accommodation One", category "hotel") -- used to
    prove the category-based accommodation exclusion rule (Rule 7) against
    a real, fully-grounded pipeline run, not just a hand-built fixture.
    """
    fields: dict[str, object] = {
        "proposal_id": "proposal_003",
        "candidate_name": "Test Fixture Accommodation One",
        "candidate_type": AICandidateType.NEIGHBORHOOD,
        "why_consider": "Mentioned as a potential lodging area worth reviewing.",
        "verification_requirements": [
            AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION
        ],
        "confidence": 0.5,
    }
    fields.update(overrides)
    return AICandidateProposal(**fields)


class _FakeAICandidateProposalProvider(AICandidateProposalProvider):
    """Deterministic test double -- never calls an LLM or provider."""

    provider_name = "fake_ai_candidate_review_test_provider"

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


def _create_trip_payload() -> dict[str, Any]:
    return {
        "destination_scope": "single_city",
        "primary_destination": "Testville, Testland",
        "origin_city": "Home City",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": "couple",
    }


def _generate_with_shadow_mode(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    proposals: list[AICandidateProposal],
) -> str:
    monkeypatch.setattr(
        orchestrator_module,
        "get_settings",
        lambda: Settings(AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=True),
    )
    fake_provider = _FakeAICandidateProposalProvider(proposals)
    fake_service = AICandidateDiscoveryService(proposal_provider=fake_provider)
    monkeypatch.setattr(
        orchestrator_module.planning_orchestrator, "ai_candidate_discovery_service", fake_service
    )

    create_response = client.post("/trips", json=_create_trip_payload())
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200
    return trip_id


# ---------------------------------------------------------------------------
# 1. Empty trip / no AI candidates returns an empty review report.
# ---------------------------------------------------------------------------


def test_new_trip_ai_candidate_review_endpoint_returns_empty_report(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}/ai-candidate-review")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    report = body["data"]["ai_candidate_review_report"]
    assert report["status"] == "no_candidate_data"
    assert report["total_ai_candidates"] == 0
    assert report["grounded_candidates"] == 0
    assert report["ungrounded_candidates"] == 0
    assert report["eligible_for_promotion"] == 0
    assert report["items"] == []


def test_generated_trip_without_shadow_mode_returns_empty_report(
    client: TestClient, generated_trip_id: str
) -> None:
    """Shadow mode is disabled by default (conftest's `_isolate_ai_candidate_proposal_env`),
    so a normal `POST /generate` leaves `ai_candidate_proposal_batch` `None`
    and the review report must honestly reflect that -- never a fabricated
    candidate.
    """
    response = client.get(f"/trips/{generated_trip_id}/ai-candidate-review")
    assert response.status_code == 200
    report = response.json()["data"]["ai_candidate_review_report"]
    assert report["status"] == "no_candidate_data"
    assert report["items"] == []


def test_ai_candidate_review_unknown_trip_returns_404(client: TestClient) -> None:
    response = client.get("/trips/does-not-exist/ai-candidate-review")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["errors"][0]["code"] == "TRIP_NOT_FOUND"


# ---------------------------------------------------------------------------
# 2. The endpoint is read-only.
# ---------------------------------------------------------------------------


def test_ai_candidate_review_endpoint_is_read_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_shadow_mode(client, monkeypatch, [_valid_proposal()])

    before_response = client.get(f"/trips/{trip_id}")
    before_state = before_response.json()["data"]["planning_state"]

    client.get(f"/trips/{trip_id}/ai-candidate-review")
    client.get(f"/trips/{trip_id}/ai-candidate-review")

    after_response = client.get(f"/trips/{trip_id}")
    after_state = after_response.json()["data"]["planning_state"]

    assert after_state == before_state
    assert after_state["metadata"]["updated_at"] == before_state["metadata"]["updated_at"]
    assert after_state["ai_candidate_proposal_batch"] == before_state["ai_candidate_proposal_batch"]
    assert after_state["candidate_grounding_batch"] == before_state["candidate_grounding_batch"]
    assert after_state["experience_plan"] == before_state["experience_plan"]


# ---------------------------------------------------------------------------
# 3-4. The endpoint never triggers a new LLM/provider/AI candidate proposal
#      call of its own.
# ---------------------------------------------------------------------------


def test_ai_candidate_review_does_not_call_anthropic_or_groq(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_shadow_mode(client, monkeypatch, [_valid_proposal()])

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("Anthropic/Groq provider must never be called by the review endpoint.")

    monkeypatch.setattr(AnthropicAICandidateProposalProvider, "propose", _fail)
    monkeypatch.setattr(GroqAICandidateProposalProvider, "propose", _fail)

    response = client.get(f"/trips/{trip_id}/ai-candidate-review")
    assert response.status_code == 200


def test_ai_candidate_review_route_handler_has_no_discovery_or_provider_references() -> None:
    import app.api.routes.trips as trips_module

    source = inspect.getsource(trips_module.get_ai_candidate_review)
    disallowed = (
        "ai_candidate_discovery_service",
        "get_ai_candidate_proposal_provider",
        "AICandidateDiscoveryService",
        "anthropic",
        "groq",
        "openai",
    )
    lowered = source.lower()
    for needle in disallowed:
        assert needle.lower() not in lowered


# ---------------------------------------------------------------------------
# 5. Candidate counts reflect stored shadow-mode state.
# ---------------------------------------------------------------------------


def test_ai_candidate_review_includes_candidate_counts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_shadow_mode(
        client, monkeypatch, [_valid_proposal(), _unmatched_proposal()]
    )

    response = client.get(f"/trips/{trip_id}/ai-candidate-review")
    report = response.json()["data"]["ai_candidate_review_report"]

    assert report["status"] == "reviewed"
    assert report["total_ai_candidates"] == 2
    assert report["grounded_candidates"] == 1
    assert report["ungrounded_candidates"] == 1
    assert len(report["items"]) == 2


# ---------------------------------------------------------------------------
# 6. Ungrounded candidates are marked not eligible.
# ---------------------------------------------------------------------------


def test_ai_candidate_review_ungrounded_candidate_not_eligible(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_shadow_mode(client, monkeypatch, [_unmatched_proposal()])

    response = client.get(f"/trips/{trip_id}/ai-candidate-review")
    items = response.json()["data"]["ai_candidate_review_report"]["items"]
    assert len(items) == 1
    item = items[0]

    assert item["provider_grounded"] is False
    assert item["eligible_for_promotion"] is False
    assert "Candidate is not provider-grounded." in item["rejection_reasons"]


# ---------------------------------------------------------------------------
# 1 (API level). Grounded candidate with acceptable quality becomes
#    eligible for promotion (Step 170B) -- this is a real, fully-grounded
#    pipeline run, not a hand-built fixture: "Test Fixture Attraction One"
#    is both a real `DeterministicTestPlacesProvider` candidate (scored by
#    `CandidateQualityService`) and the AI proposal's exact name.
# ---------------------------------------------------------------------------


def test_ai_candidate_review_grounded_candidate_with_acceptable_quality_becomes_eligible(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_shadow_mode(client, monkeypatch, [_valid_proposal()])

    response = client.get(f"/trips/{trip_id}/ai-candidate-review")
    report = response.json()["data"]["ai_candidate_review_report"]
    items = report["items"]
    assert len(items) == 1
    item = items[0]

    assert item["provider_grounded"] is True
    assert item["eligible_for_promotion"] is True
    assert item["quality_bucket"] in {"primary_anchor", "good_candidate", "secondary_candidate"}
    assert item["rejection_reasons"] == []
    assert item["eligibility_reasons"]
    assert report["eligible_for_promotion"] == 1


# ---------------------------------------------------------------------------
# 8 (Step 170B). Accommodation/hotel candidate is not eligible for AI place
#    promotion, even though it is genuinely provider-grounded, proven
#    against a real generated pipeline (the accommodation POI fixture
#    carries category "hotel").
# ---------------------------------------------------------------------------


def test_ai_candidate_review_accommodation_candidate_not_eligible(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_shadow_mode(client, monkeypatch, [_accommodation_proposal()])

    response = client.get(f"/trips/{trip_id}/ai-candidate-review")
    report = response.json()["data"]["ai_candidate_review_report"]
    items = report["items"]
    assert len(items) == 1
    item = items[0]

    assert item["provider_grounded"] is True
    assert item["eligible_for_promotion"] is False
    assert any("accommodation/lodging" in reason for reason in item["rejection_reasons"])
    assert report["eligible_for_promotion"] == 0


# ---------------------------------------------------------------------------
# 2 (API level). Report eligible_for_promotion count reflects a mix of
#    eligible/ineligible candidates.
# ---------------------------------------------------------------------------


def test_ai_candidate_review_eligible_count_reflects_mixed_candidates(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_shadow_mode(
        client,
        monkeypatch,
        [_valid_proposal(), _unmatched_proposal(), _accommodation_proposal()],
    )

    response = client.get(f"/trips/{trip_id}/ai-candidate-review")
    report = response.json()["data"]["ai_candidate_review_report"]

    assert report["total_ai_candidates"] == 3
    assert report["grounded_candidates"] == 2
    assert report["ungrounded_candidates"] == 1
    assert report["eligible_for_promotion"] == 1
    eligible_names = {
        item["name"] for item in report["items"] if item["eligible_for_promotion"]
    }
    assert eligible_names == {"Test Fixture Attraction One"}


# ---------------------------------------------------------------------------
# 12. Endpoint returns eligible candidates in the review report but never
#     adds them to itinerary days.
# ---------------------------------------------------------------------------


def test_ai_candidate_review_never_adds_candidate_to_itinerary(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_shadow_mode(client, monkeypatch, [_valid_proposal()])

    # Prove the candidate is actually reported eligible (Step 170B) before
    # confirming that eligibility alone never schedules anything.
    review_response = client.get(f"/trips/{trip_id}/ai-candidate-review")
    assert review_response.json()["data"]["ai_candidate_review_report"]["eligible_for_promotion"] == 1

    # Calling the review endpoint (possibly repeatedly) must not affect the
    # scheduled itinerary.
    client.get(f"/trips/{trip_id}/ai-candidate-review")

    response = client.get(f"/trips/{trip_id}/experience-plan")
    experience_plan = response.json()["data"]["experience_plan"]
    all_experiences = [
        experience
        for day_plan in experience_plan["daily_plans"]
        for experience in day_plan["experiences"]
    ]
    scheduled_names = {experience["name"] for experience in all_experiences}
    # Only real deterministic test-fixture candidates are ever scheduled --
    # "Test Fixture Attraction One" may legitimately appear here because it
    # is *also* a real provider candidate (Step 170D's dedup rule prevents
    # a second, promoted-duplicate entry for the same real place), never
    # because merely reviewing it made a difference.
    assert scheduled_names <= {"Test Fixture Attraction One", "Test Fixture Attraction Two"}

    # ExperiencePlannerService is allowed to read the already-computed
    # promotion report (Step 170D) -- but it must never reach back into the
    # review/eligibility/proposal/grounding internals directly; only the
    # already-materialized PromotedAICandidate/AICandidatePromotionReport
    # models.
    import app.services.experience_planner_service as experience_planner_module

    source = inspect.getsource(experience_planner_module)
    for disallowed in (
        "GroundedCandidate",
        "AICandidateProposal",
        "ai_candidate_proposal_batch",
        "candidate_grounding_batch",
        "AICandidateReviewItem",
        "ai_candidate_review_service",
        "ai_candidate_promotion_eligibility_service",
    ):
        assert disallowed not in source


# ---------------------------------------------------------------------------
# Wording guardrails: none of the disallowed marketing/overclaiming phrases
# ever appear in a rendered review report.
# ---------------------------------------------------------------------------


def test_ai_candidate_review_report_has_no_disallowed_wording(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_shadow_mode(
        client, monkeypatch, [_valid_proposal(), _unmatched_proposal()]
    )

    response = client.get(f"/trips/{trip_id}/ai-candidate-review")
    body_text = response.text.lower()
    for phrase in _FORBIDDEN_TEXT:
        assert phrase not in body_text
