from __future__ import annotations

import inspect
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.services.planning_orchestrator as orchestrator_module
from app.core.config import Settings, get_settings
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

# API tests for the AI candidate promotion endpoint
# (`POST /trips/{trip_id}/ai-candidate-promotions`, Step 170C, extended
# with safe scheduling integration in Step 170D). This endpoint
# materializes Step 170B's already-computed deterministic eligibility
# verdicts into `PlanningState.ai_candidate_promotion_report` -- it never
# calls a provider/LLM itself. These tests enable the existing,
# config-gated Step 161B shadow stage with deterministic fake proposal
# providers (never a real Anthropic/Groq/OpenAI call).
#
# As of Step 170D, `PlanningOrchestrator` auto-computes
# `ai_candidate_promotion_report` right after the shadow discovery stage
# (see `_run_ai_candidate_promotion_stage`), so whenever shadow mode is
# enabled the report already exists immediately after `POST /generate` --
# the explicit `POST /ai-candidate-promotions` endpoint remains available
# and idempotent for a manual recompute, but is no longer the *only* way
# the report gets populated. When shadow mode is disabled (the default),
# `ai_candidate_proposal_batch` stays `None` and so does
# `ai_candidate_promotion_report` -- unchanged from Step 170C.


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


def _restaurant_sourced_proposal(**overrides: object) -> AICandidateProposal:
    """Matches `DeterministicTestPlacesProvider`'s real restaurant fixture
    ("Test Fixture Restaurant One") -- a place that is never normally
    scheduled as an itinerary attraction (restaurants only ever produce
    day-level `restaurant_suggestions`). Used to prove Step 170D actually
    lets a genuinely-new promoted candidate join the attraction scheduling
    pool, not just a duplicate of an already-scheduled real attraction.
    """
    fields: dict[str, object] = {
        "proposal_id": "proposal_003",
        "candidate_name": "Test Fixture Restaurant One",
        "candidate_type": AICandidateType.FOOD_AREA,
        "why_consider": "A notable food market worth visiting, not just eating at.",
        "verification_requirements": [
            AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION
        ],
        "confidence": 0.5,
    }
    fields.update(overrides)
    return AICandidateProposal(**fields)


class _FakeAICandidateProposalProvider(AICandidateProposalProvider):
    """Deterministic test double -- never calls an LLM or provider."""

    provider_name = "fake_ai_candidate_promotion_test_provider"

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
    """As of Step 171E, the config-gated Step 161B AI candidate discovery
    shadow stage (and the Step 170D promotion stage that only ever runs
    once a shadow proposal batch exists) remains a
    `PlanningOrchestrator.generate_full_plan`-specific (legacy engine)
    integration -- it is intentionally not part of the LangGraph engine's
    stage graph (see `build_ai_candidate_node`'s docstring in
    `planning_graph_nodes.py` for why). `PLANNING_ENGINE_MODE=legacy` is
    pinned here so `POST /generate` -- now defaulting to the LangGraph
    engine -- still exercises that legacy-path-specific integration.
    """
    monkeypatch.setenv("PLANNING_ENGINE_MODE", "legacy")
    get_settings.cache_clear()
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
# 13-14. POST stores the report on PlanningState and returns promoted
#        candidates.
# ---------------------------------------------------------------------------


def test_post_promotions_stores_report_on_planning_state_and_returns_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_shadow_mode(client, monkeypatch, [_valid_proposal()])

    response = client.post(f"/trips/{trip_id}/ai-candidate-promotions")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    report = body["data"]["ai_candidate_promotion_report"]
    assert report["status"] == "promoted"
    assert report["promoted_count"] == 1
    promoted = report["promoted_candidates"][0]
    assert promoted["name"] == "Test Fixture Attraction One"
    assert promoted["promoted"] is True
    assert promoted["original_ai_candidate_id"] == "proposal_001"

    trip_response = client.get(f"/trips/{trip_id}")
    stored_report = trip_response.json()["data"]["planning_state"]["ai_candidate_promotion_report"]
    assert stored_report == report


# ---------------------------------------------------------------------------
# 15. POST returns empty report when no candidates are eligible.
# ---------------------------------------------------------------------------


def test_post_promotions_returns_empty_report_when_nothing_eligible(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_shadow_mode(client, monkeypatch, [_unmatched_proposal()])

    response = client.post(f"/trips/{trip_id}/ai-candidate-promotions")
    assert response.status_code == 200
    report = response.json()["data"]["ai_candidate_promotion_report"]

    assert report["status"] == "no_eligible_candidates"
    assert report["promoted_count"] == 0
    assert report["promoted_candidates"] == []
    assert report["skipped_candidate_ids"] == ["proposal_002"]


def test_post_promotions_on_trip_without_any_candidates_returns_empty_report(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.post(f"/trips/{created_trip_id}/ai-candidate-promotions")
    assert response.status_code == 200
    report = response.json()["data"]["ai_candidate_promotion_report"]

    assert report["status"] == "no_candidates_reviewed"
    assert report["promoted_candidates"] == []
    assert report["skipped_candidate_ids"] == []


# ---------------------------------------------------------------------------
# 16. GET review endpoint remains read-only and never stores the promotion
#     report itself.
# ---------------------------------------------------------------------------


def test_get_review_endpoint_never_creates_a_promotion_report_from_nothing(
    client: TestClient, generated_trip_id: str
) -> None:
    """With shadow mode disabled (the default -- `generated_trip_id` uses a
    plain `POST /generate`), `ai_candidate_proposal_batch` stays `None`, so
    `PlanningOrchestrator`'s Step 170D auto-promotion stage is a no-op and
    `ai_candidate_promotion_report` stays `None` too. Calling the read-only
    review endpoint must never conjure one into existence on its own.
    """
    client.get(f"/trips/{generated_trip_id}/ai-candidate-review")
    client.get(f"/trips/{generated_trip_id}/ai-candidate-review")

    trip_response = client.get(f"/trips/{generated_trip_id}")
    planning_state = trip_response.json()["data"]["planning_state"]
    assert planning_state["ai_candidate_promotion_report"] is None


def test_get_review_endpoint_never_changes_an_existing_promotion_report(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With shadow mode enabled, `ai_candidate_promotion_report` is already
    auto-computed by the time `POST /generate` returns (Step 170D). Calling
    the read-only review endpoint afterwards must never change it.
    """
    trip_id = _generate_with_shadow_mode(client, monkeypatch, [_valid_proposal()])

    before_response = client.get(f"/trips/{trip_id}")
    before_report = before_response.json()["data"]["planning_state"]["ai_candidate_promotion_report"]
    assert before_report is not None

    client.get(f"/trips/{trip_id}/ai-candidate-review")
    client.get(f"/trips/{trip_id}/ai-candidate-review")

    after_response = client.get(f"/trips/{trip_id}")
    after_report = after_response.json()["data"]["planning_state"]["ai_candidate_promotion_report"]
    assert after_report == before_report


def test_get_review_endpoint_source_never_applies_promotion() -> None:
    import app.api.routes.trips as trips_module

    source = inspect.getsource(trips_module.get_ai_candidate_review)
    for disallowed in (
        "ai_candidate_promotion_service",
        "apply_promotion",
        "AICandidatePromotionReport",
    ):
        assert disallowed not in source


# ---------------------------------------------------------------------------
# 17. Unknown trip returns existing 404 behavior.
# ---------------------------------------------------------------------------


def test_post_promotions_unknown_trip_returns_404(client: TestClient) -> None:
    response = client.post("/trips/does-not-exist/ai-candidate-promotions")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["errors"][0]["code"] == "TRIP_NOT_FOUND"


# ---------------------------------------------------------------------------
# 18-19. Endpoint never calls Groq/Anthropic/OpenAI or an AI candidate
#        proposal provider of its own.
# ---------------------------------------------------------------------------


def test_post_promotions_does_not_call_anthropic_or_groq(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_shadow_mode(client, monkeypatch, [_valid_proposal()])

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("Anthropic/Groq provider must never be called by the promotion endpoint.")

    monkeypatch.setattr(AnthropicAICandidateProposalProvider, "propose", _fail)
    monkeypatch.setattr(GroqAICandidateProposalProvider, "propose", _fail)

    response = client.post(f"/trips/{trip_id}/ai-candidate-promotions")
    assert response.status_code == 200


def test_post_promotions_route_handler_has_no_discovery_or_provider_references() -> None:
    import app.api.routes.trips as trips_module

    source = inspect.getsource(trips_module.promote_ai_candidates)
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
# Promotion never mutates the itinerary; POST only ever touches
# ai_candidate_promotion_report (+ metadata.updated_at).
# ---------------------------------------------------------------------------


def test_post_promotions_does_not_modify_itinerary_or_other_sections(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicitly re-`POST`ing promotions after generate (Step 170D:
    `ai_candidate_promotion_report` is already auto-computed by generate
    itself) must still only ever recompute that one field -- never
    `experience_plan`, `validation_report`, or the underlying AI
    proposal/grounding batches.
    """
    trip_id = _generate_with_shadow_mode(client, monkeypatch, [_valid_proposal()])

    before_response = client.get(f"/trips/{trip_id}")
    before_state = before_response.json()["data"]["planning_state"]
    # Step 170D: already populated by generate itself, before any explicit
    # POST call.
    assert before_state["ai_candidate_promotion_report"] is not None

    client.post(f"/trips/{trip_id}/ai-candidate-promotions")

    after_response = client.get(f"/trips/{trip_id}")
    after_state = after_response.json()["data"]["planning_state"]

    assert after_state["experience_plan"] == before_state["experience_plan"]
    assert after_state["validation_report"] == before_state["validation_report"]
    assert after_state["ai_candidate_proposal_batch"] == before_state["ai_candidate_proposal_batch"]
    assert after_state["candidate_grounding_batch"] == before_state["candidate_grounding_batch"]
    # Recomputed fresh (a new `generated_at`), but otherwise identical --
    # same promoted candidates, same counts.
    before_report = {k: v for k, v in before_state["ai_candidate_promotion_report"].items() if k != "generated_at"}
    after_report = {k: v for k, v in after_state["ai_candidate_promotion_report"].items() if k != "generated_at"}
    assert after_report == before_report


def test_post_promotions_is_idempotent_over_http(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _generate_with_shadow_mode(client, monkeypatch, [_valid_proposal()])

    first_response = client.post(f"/trips/{trip_id}/ai-candidate-promotions")
    second_response = client.post(f"/trips/{trip_id}/ai-candidate-promotions")

    first_report = first_response.json()["data"]["ai_candidate_promotion_report"]
    second_report = second_response.json()["data"]["ai_candidate_promotion_report"]

    assert first_report["promoted_count"] == second_report["promoted_count"] == 1
    assert [c["candidate_id"] for c in first_report["promoted_candidates"]] == [
        c["candidate_id"] for c in second_report["promoted_candidates"]
    ]


def test_post_promotions_of_a_real_candidate_does_not_duplicate_it_in_itinerary(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Test Fixture Attraction One" is both a real destination-context
    candidate and the AI proposal's exact name -- it was already going to
    be scheduled the normal way. Step 170D's dedup rule must prevent a
    second, promoted-duplicate entry for the same real place.
    """
    trip_id = _generate_with_shadow_mode(client, monkeypatch, [_valid_proposal()])

    promote_response = client.post(f"/trips/{trip_id}/ai-candidate-promotions")
    assert promote_response.json()["data"]["ai_candidate_promotion_report"]["promoted_count"] == 1

    response = client.get(f"/trips/{trip_id}/experience-plan")
    experience_plan = response.json()["data"]["experience_plan"]
    all_experiences = [
        experience
        for day_plan in experience_plan["daily_plans"]
        for experience in day_plan["experiences"]
    ]
    names = [experience["name"] for experience in all_experiences]
    # Exactly one entry for "Test Fixture Attraction One" -- never two.
    assert names.count("Test Fixture Attraction One") == 1
    assert set(names) <= {"Test Fixture Attraction One", "Test Fixture Attraction Two"}


# ---------------------------------------------------------------------------
# 3-4. A genuinely new promoted candidate (not already a scheduled real
#      attraction) can appear in the itinerary scheduling pool, preserving
#      promoted_from_ai/source metadata.
# ---------------------------------------------------------------------------


def test_promoted_candidate_from_restaurant_fixture_is_scheduled_with_provenance(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Test Fixture Restaurant One" is a real, provider-grounded,
    quality-approved place that is *never* normally scheduled as an
    itinerary attraction (only as a day-level restaurant suggestion). Once
    an AI proposal for it is grounded, quality-approved, and promoted
    (Step 170B/170C), Step 170D allows it to join the attraction
    scheduling pool for the first time -- proving promotion actually
    reaches scheduling, not just a report.
    """
    trip_id = _generate_with_shadow_mode(client, monkeypatch, [_restaurant_sourced_proposal()])

    promotion_report = client.post(f"/trips/{trip_id}/ai-candidate-promotions").json()["data"][
        "ai_candidate_promotion_report"
    ]
    assert promotion_report["promoted_count"] == 1

    response = client.get(f"/trips/{trip_id}/experience-plan")
    experience_plan = response.json()["data"]["experience_plan"]
    all_experiences = [
        experience
        for day_plan in experience_plan["daily_plans"]
        for experience in day_plan["experiences"]
    ]
    matching = [e for e in all_experiences if e["name"] == "Test Fixture Restaurant One"]
    assert len(matching) == 1
    scheduled = matching[0]

    assert scheduled["promoted_from_ai"] is True
    assert scheduled["original_ai_candidate_id"] == "proposal_003"
    assert scheduled["provider_place_id"] == "test/restaurant/1"
    assert scheduled["provider_source"] == "openstreetmap_places"
    # Never a fabricated fact: no rating/price/opening-hours/route/booking
    # field exists on ExperienceItem at all.
    forbidden_keys = {"rating", "price", "opening_hours", "route_time", "booking_url"}
    assert set(scheduled.keys()) & forbidden_keys == set()
