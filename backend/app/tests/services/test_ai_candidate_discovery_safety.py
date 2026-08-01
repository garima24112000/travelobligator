from __future__ import annotations

import ast
import inspect
import sys
from typing import Any, Callable

import pytest
from pydantic import ValidationError

from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateProposalBatch,
    AICandidateProposalGuardrailReport,
    AICandidateProposalRequest,
    AICandidateProposalResult,
    AICandidateProposalStatus,
    AICandidateProposalTask,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.models.candidate_grounding import CandidateGroundingRejectReason, CandidateGroundingStatus
from app.models.common import DataStatus, GeoPoint
from app.models.planning_state import DestinationContext, PlanningState, TravelGroupType, TripRequest
from app.providers.ai_candidate_proposal import AICandidateProposalProvider
from app.services.ai_candidate_discovery_service import AICandidateDiscoveryService
from app.services.candidate_grounding_service import CandidateGroundingService

# Safety end-to-end tests for the candidate-discovery flow (Step 160D,
# itinerary-generator-build-spec.md Stages 5-6, docs/13_llm_reasoning_
# pipeline.md section 37). This file is deliberately test-only: it adds no
# production code (unless a real safety gap were discovered here, which it
# was not). It proves, using only in-file deterministic fake proposal
# providers, that:
#
# - AI-like output containing a forbidden factual claim can never become a
#   valid `AICandidateProposal` in the first place, so it can never reach
#   `CandidateGroundingService.ground`.
# - A structurally invalid `AICandidateProposalResult` (empty
#   verification_requirements, blank name, a completed/not_connected
#   status inconsistent with its own proposals/guardrail) is rejected by
#   the same models before grounding ever runs.
# - A schema-valid but unmatched or ambiguous proposal is honestly
#   rejected by `CandidateGroundingService`, never silently dropped and
#   never force-matched.
# - A matching proposal only ever grounds using the exact
#   `ProviderCandidateForGrounding` evidence already supplied through
#   `PlanningState.destination_context` -- no coordinate, provider name,
#   or place id is ever invented.
# - `AICandidateDiscoveryService.dry_run` never mutates `PlanningState`,
#   never populates its Step 160C storage fields, and never touches
#   persistence.
# - No production module (`PlanningOrchestrator`, API routes, scheduling,
#   validation, or regeneration/feedback/versioning services) imports
#   `AICandidateDiscoveryService`.

_FORBIDDEN_MODEL_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "ticket_price",
    "availability",
    "safety_score",
}

_FORBIDDEN_TEXT_CASES = [
    "This place has a great rating",
    "The price is low",
    "opening_hours: 9am-5pm",
    "route_time is short",
    "booking_url available soon",
    "review_count is high",
    "ticket_price is affordable",
    "availability looks good",
    "safety_score is high",
    "book now before it fills up",
    "highly rated by locals",
    "guaran" + "teed to be a great time",
    "exact travel time is 10 minutes",
]

_UNSAFE_TEXT_FIELDS = ["candidate_name", "suggested_area", "why_consider", "fit_with_user_preferences"]


def _valid_proposal_kwargs(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "proposal_id": "proposal_001",
        "candidate_name": "Old Town Waterfront",
        "candidate_type": AICandidateType.NEIGHBORHOOD,
        "suggested_area": "Old Town",
        "why_consider": "Locally known for evening walks, may be under-tagged in provider data.",
        "fit_with_user_preferences": ["matches interest in walking tours"],
        "verification_requirements": [
            AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION
        ],
        "confidence": 0.5,
    }
    fields.update(overrides)
    return fields


def _valid_proposal(**overrides: object) -> AICandidateProposal:
    return AICandidateProposal(**_valid_proposal_kwargs(**overrides))


def _place(
    name: str = "Old Town Waterfront",
    *,
    lat: float = 38.7223,
    lng: float = -9.1393,
    category: str | None = "neighborhood",
    confidence: float = 0.85,
    source: str = "openstreetmap_places",
    data_status: str = "live",
    place_id: str = "way/12345",
) -> dict[str, Any]:
    return {
        "name": name,
        "category": category,
        "coordinates": {"lat": lat, "lng": lng},
        "source": source,
        "data_status": data_status,
        "confidence": confidence,
        "place_id": place_id,
    }


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Lisbon, Portugal",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _planning_state(
    candidate_pois: list[dict[str, Any]] | None = None,
    with_destination_context: bool = False,
    **trip_request_overrides: Any,
) -> PlanningState:
    planning_state = PlanningState(trip_request=_trip_request(**trip_request_overrides))
    if with_destination_context or candidate_pois:
        planning_state.destination_context = DestinationContext(
            destination_name=planning_state.trip_request.primary_destination,
            candidate_pois=candidate_pois or [],
        )
    return planning_state


class _ValidProposalsProvider(AICandidateProposalProvider):
    """Deterministic test double returning exactly the schema-valid
    proposals it was constructed with -- never calls an LLM or provider.
    """

    provider_name = "safety_test_valid_proposals_provider"

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


class _UnsafeTextAICandidateProposalProvider(AICandidateProposalProvider):
    """Simulates a broken/adversarial LLM-backed provider attempting to
    hand back a proposal with a forbidden factual claim in one field.
    Constructing the `AICandidateProposal` itself raises `ValidationError`
    -- this test double never gets as far as returning a result.
    """

    provider_name = "safety_test_unsafe_text_provider"

    def __init__(self, unsafe_field: str, unsafe_text: str) -> None:
        self._unsafe_field = unsafe_field
        self._unsafe_text = unsafe_text

    def propose(self, request: AICandidateProposalRequest) -> AICandidateProposalResult:
        overrides: dict[str, object] = (
            {"fit_with_user_preferences": [self._unsafe_text]}
            if self._unsafe_field == "fit_with_user_preferences"
            else {self._unsafe_field: self._unsafe_text}
        )
        # Raises pydantic.ValidationError before a proposal object -- and
        # therefore an AICandidateProposalResult -- can ever exist.
        proposal = AICandidateProposal(**_valid_proposal_kwargs(**overrides))
        return AICandidateProposalResult(
            task=request.task,
            status=AICandidateProposalStatus.COMPLETED,
            proposals=[proposal],
            guardrail_report=AICandidateProposalGuardrailReport(passed=True),
            provider_name=self.provider_name,
            confidence=0.6,
        )


class _BrokenSchemaAICandidateProposalProvider(AICandidateProposalProvider):
    """Simulates a provider trying to return a structurally invalid
    `AICandidateProposalResult`. `build_result` raises `ValidationError`
    while constructing it -- this test double never gets as far as
    returning a valid result.
    """

    provider_name = "safety_test_broken_schema_provider"

    def __init__(
        self, build_result: Callable[[AICandidateProposalRequest, str], AICandidateProposalResult]
    ) -> None:
        self._build_result = build_result

    def propose(self, request: AICandidateProposalRequest) -> AICandidateProposalResult:
        return self._build_result(request, self.provider_name)


def _spy_grounding_service_ground(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Patches `CandidateGroundingService.ground` to record whether it was
    called and raise if it is -- proves grounding never runs when the
    proposal/result never validly reaches it.
    """
    called: list[bool] = []

    def _fail(self: CandidateGroundingService, request: object) -> None:
        called.append(True)
        raise AssertionError("CandidateGroundingService.ground must not be called")

    monkeypatch.setattr(CandidateGroundingService, "ground", _fail)
    return called


# ---------------------------------------------------------------------------
# 1. Unsafe AI-like proposal text fails before grounding.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("unsafe_field", _UNSAFE_TEXT_FIELDS)
@pytest.mark.parametrize("forbidden_text", _FORBIDDEN_TEXT_CASES)
def test_unsafe_proposal_text_fails_before_grounding(
    monkeypatch: pytest.MonkeyPatch, unsafe_field: str, forbidden_text: str
) -> None:
    called = _spy_grounding_service_ground(monkeypatch)

    service = AICandidateDiscoveryService(
        proposal_provider=_UnsafeTextAICandidateProposalProvider(unsafe_field, forbidden_text)
    )
    planning_state = _planning_state(candidate_pois=[_place(name="Old Town Waterfront")])

    with pytest.raises(ValidationError):
        service.dry_run(planning_state)

    assert called == []


# ---------------------------------------------------------------------------
# 2. Invalid AI proposal schema fails before grounding.
# ---------------------------------------------------------------------------


def _build_empty_verification_requirements(
    request: AICandidateProposalRequest, provider_name: str
) -> AICandidateProposalResult:
    proposal = AICandidateProposal(**_valid_proposal_kwargs(verification_requirements=[]))
    return AICandidateProposalResult(
        task=request.task,
        status=AICandidateProposalStatus.COMPLETED,
        proposals=[proposal],
        guardrail_report=AICandidateProposalGuardrailReport(passed=True),
        provider_name=provider_name,
        confidence=0.5,
    )


def _build_blank_candidate_name(
    request: AICandidateProposalRequest, provider_name: str
) -> AICandidateProposalResult:
    proposal = AICandidateProposal(**_valid_proposal_kwargs(candidate_name="   "))
    return AICandidateProposalResult(
        task=request.task,
        status=AICandidateProposalStatus.COMPLETED,
        proposals=[proposal],
        guardrail_report=AICandidateProposalGuardrailReport(passed=True),
        provider_name=provider_name,
        confidence=0.5,
    )


def _build_completed_with_empty_proposals(
    request: AICandidateProposalRequest, provider_name: str
) -> AICandidateProposalResult:
    return AICandidateProposalResult(
        task=request.task,
        status=AICandidateProposalStatus.COMPLETED,
        proposals=[],
        guardrail_report=AICandidateProposalGuardrailReport(passed=True),
        provider_name=provider_name,
        confidence=0.5,
    )


def _build_completed_with_failed_guardrail(
    request: AICandidateProposalRequest, provider_name: str
) -> AICandidateProposalResult:
    proposal = _valid_proposal()
    return AICandidateProposalResult(
        task=request.task,
        status=AICandidateProposalStatus.COMPLETED,
        proposals=[proposal],
        guardrail_report=AICandidateProposalGuardrailReport(passed=False, blocked_reasons=["unsafe output"]),
        provider_name=provider_name,
        confidence=0.5,
    )


def _build_not_connected_with_proposals(
    request: AICandidateProposalRequest, provider_name: str
) -> AICandidateProposalResult:
    proposal = _valid_proposal()
    return AICandidateProposalResult(
        task=request.task,
        status=AICandidateProposalStatus.NOT_CONNECTED,
        proposals=[proposal],
        guardrail_report=AICandidateProposalGuardrailReport(
            passed=False, blocked_reasons=["No AI candidate proposal provider is connected yet."]
        ),
        provider_name=provider_name,
        confidence=0.0,
    )


_INVALID_SCHEMA_CASES = [
    ("empty_verification_requirements", _build_empty_verification_requirements),
    ("blank_candidate_name", _build_blank_candidate_name),
    ("completed_with_empty_proposals", _build_completed_with_empty_proposals),
    ("completed_with_failed_guardrail", _build_completed_with_failed_guardrail),
    ("not_connected_with_proposals", _build_not_connected_with_proposals),
]


@pytest.mark.parametrize("case_name, build_result", _INVALID_SCHEMA_CASES)
def test_invalid_proposal_schema_fails_before_grounding(
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    build_result: Callable[[AICandidateProposalRequest, str], AICandidateProposalResult],
) -> None:
    called = _spy_grounding_service_ground(monkeypatch)

    service = AICandidateDiscoveryService(
        proposal_provider=_BrokenSchemaAICandidateProposalProvider(build_result)
    )
    planning_state = _planning_state(candidate_pois=[_place(name="Old Town Waterfront")])

    with pytest.raises(ValidationError):
        service.dry_run(planning_state)

    assert called == []


def test_ai_candidate_proposal_batch_task_mismatch_raises_validation_error() -> None:
    """A `result.task` that doesn't match `request.task` is a structural
    "status/result mismatch" the batch model itself refuses -- this never
    goes through `dry_run` (which doesn't construct a batch at all, Step
    160C's storage field is filled in later, not by this flow), but it
    demonstrates the same never-trust-unvalidated-output principle at the
    model level.
    """
    request = AICandidateProposalRequest(
        task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
        trip_id="trip_001",
        destination_name="Lisbon",
        trip_duration_days=3,
    )
    mismatched_result = AICandidateProposalResult(
        task=AICandidateProposalTask.FEEDBACK_CANDIDATE_DISCOVERY,
        status=AICandidateProposalStatus.NOT_CONNECTED,
        proposals=[],
        guardrail_report=AICandidateProposalGuardrailReport(passed=False, blocked_reasons=["not connected"]),
        confidence=0.0,
    )
    with pytest.raises(ValidationError):
        AICandidateProposalBatch(request=request, result=mismatched_result)


# ---------------------------------------------------------------------------
# 3. Unmatched but schema-valid AI proposal is rejected by grounding.
# ---------------------------------------------------------------------------


def test_unmatched_valid_proposal_is_rejected_by_grounding() -> None:
    proposals = [_valid_proposal(candidate_name="Mystery Garden")]
    service = AICandidateDiscoveryService(proposal_provider=_ValidProposalsProvider(proposals))
    planning_state = _planning_state(candidate_pois=[_place(name="Old Town Waterfront")])
    before = planning_state.model_copy(deep=True)

    result = service.dry_run(planning_state)

    assert result.grounding_result.status == CandidateGroundingStatus.REJECTED
    assert result.grounding_result.grounded_candidates == []
    assert len(result.grounding_result.rejected_proposals) == 1
    assert result.grounding_result.rejected_proposals[0].reject_reason == (
        CandidateGroundingRejectReason.NO_PROVIDER_MATCH
    )
    assert planning_state == before


# ---------------------------------------------------------------------------
# 4. Ambiguous AI proposal is rejected by grounding.
# ---------------------------------------------------------------------------


def test_ambiguous_valid_proposal_is_rejected_by_grounding() -> None:
    proposals = [_valid_proposal(candidate_name="Old Town Waterfront")]
    service = AICandidateDiscoveryService(proposal_provider=_ValidProposalsProvider(proposals))
    planning_state = _planning_state(
        candidate_pois=[
            _place(name="Old Town Waterfront", place_id="way/1", lat=38.7223, lng=-9.1393),
            _place(name="old town, waterfront!", place_id="way/2", lat=38.7300, lng=-9.1420),
        ]
    )

    result = service.dry_run(planning_state)

    assert result.grounding_result.status == CandidateGroundingStatus.REJECTED
    assert result.grounding_result.grounded_candidates == []
    assert len(result.grounding_result.rejected_proposals) == 1
    assert result.grounding_result.rejected_proposals[0].reject_reason == (
        CandidateGroundingRejectReason.AMBIGUOUS_MATCH
    )


# ---------------------------------------------------------------------------
# 5. Matching AI proposal grounds only from supplied provider candidate
#    evidence.
# ---------------------------------------------------------------------------


def test_matching_valid_proposal_grounds_only_from_supplied_evidence() -> None:
    proposals = [_valid_proposal(candidate_name="Old Town Waterfront")]
    service = AICandidateDiscoveryService(proposal_provider=_ValidProposalsProvider(proposals))
    planning_state = _planning_state(
        candidate_pois=[
            _place(
                name="Old Town Waterfront",
                place_id="way/999",
                source="openstreetmap_places",
                data_status="live",
                confidence=0.9,
                lat=38.7223,
                lng=-9.1393,
            )
        ]
    )

    result = service.dry_run(planning_state)

    assert result.grounding_result.status == CandidateGroundingStatus.COMPLETED
    assert len(result.grounding_result.grounded_candidates) == 1
    grounded = result.grounding_result.grounded_candidates[0]
    evidence = grounded.evidence

    assert evidence.provider_name == "openstreetmap_places"
    assert evidence.provider_place_id == "way/999"
    assert evidence.matched_name == "Old Town Waterfront"
    assert evidence.data_status == DataStatus.LIVE
    assert evidence.coordinates == GeoPoint(lat=38.7223, lng=-9.1393)

    # AICandidateProposal itself can never carry a coordinate or provider
    # field -- the proposal's own words could never have supplied this
    # evidence, only the destination_context candidate could.
    proposal_field_names = set(AICandidateProposal.model_fields.keys())
    assert "coordinates" not in proposal_field_names
    assert "provider_name" not in proposal_field_names
    assert "provider_place_id" not in proposal_field_names


# ---------------------------------------------------------------------------
# 6. Dry-run remains storage-neutral.
# ---------------------------------------------------------------------------


def test_dry_run_does_not_populate_planning_state_storage_fields() -> None:
    proposals = [_valid_proposal(candidate_name="Old Town Waterfront")]
    service = AICandidateDiscoveryService(proposal_provider=_ValidProposalsProvider(proposals))
    planning_state = _planning_state(candidate_pois=[_place(name="Old Town Waterfront")])

    result = service.dry_run(planning_state)

    assert result.grounding_result.status == CandidateGroundingStatus.COMPLETED
    assert planning_state.ai_candidate_proposal_batch is None
    assert planning_state.candidate_grounding_batch is None


def test_dry_run_never_calls_planning_state_repository_save(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.repositories.planning_state_repository import planning_state_repository

    def _fail_save(*args: object, **kwargs: object) -> None:
        raise AssertionError("PlanningStateRepository.save must not be called by dry_run")

    monkeypatch.setattr(planning_state_repository, "save", _fail_save)

    proposals = [_valid_proposal(candidate_name="Old Town Waterfront")]
    service = AICandidateDiscoveryService(proposal_provider=_ValidProposalsProvider(proposals))
    planning_state = _planning_state(candidate_pois=[_place(name="Old Town Waterfront")])

    result = service.dry_run(planning_state)  # must not raise

    assert result is not None


def test_dry_run_service_has_no_repository_dependency() -> None:
    signature = inspect.signature(AICandidateDiscoveryService.__init__)
    param_names = set(signature.parameters.keys()) - {"self"}
    assert not any("repo" in name.lower() for name in param_names)


# ---------------------------------------------------------------------------
# 7. Runtime remains untouched.
# ---------------------------------------------------------------------------


def _imported_module_names(module: object) -> list[str]:
    source = inspect.getsource(module)  # type: ignore[arg-type]
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
            imported_names.extend(alias.name for alias in node.names)
    return imported_names


def _assert_no_discovery_service_import(module: object) -> None:
    imported_names = _imported_module_names(module)
    assert not any("ai_candidate_discovery_service" in name for name in imported_names)
    assert not any(name == "AICandidateDiscoveryService" for name in imported_names)


def test_planning_orchestrator_does_not_import_discovery_service() -> None:
    import app.services.planning_orchestrator as orchestrator_module

    _assert_no_discovery_service_import(orchestrator_module)


def test_api_routes_do_not_import_discovery_service() -> None:
    import app.api.routes.trips as trips_routes_module

    _assert_no_discovery_service_import(trips_routes_module)


def test_experience_planner_service_does_not_import_discovery_service() -> None:
    import app.services.experience_planner_service as module

    _assert_no_discovery_service_import(module)


def test_plan_validator_service_does_not_import_discovery_service() -> None:
    import app.services.plan_validator_service as module

    _assert_no_discovery_service_import(module)


def test_regeneration_feedback_versioning_modules_do_not_import_discovery_service() -> None:
    import app.services.feedback_service as feedback_module
    import app.services.regeneration_readiness_service as regeneration_module
    import app.services.versioning_service as versioning_module

    for module in (feedback_module, regeneration_module, versioning_module):
        _assert_no_discovery_service_import(module)


# ---------------------------------------------------------------------------
# 8. No forbidden factual fields in successful dry-run result dump.
# ---------------------------------------------------------------------------


def test_successful_dry_run_result_dump_has_no_forbidden_factual_keys() -> None:
    proposals = [_valid_proposal(candidate_name="Old Town Waterfront")]
    service = AICandidateDiscoveryService(proposal_provider=_ValidProposalsProvider(proposals))
    planning_state = _planning_state(candidate_pois=[_place(name="Old Town Waterfront")])

    result = service.dry_run(planning_state)
    assert result.grounding_result.status == CandidateGroundingStatus.COMPLETED
    dumped = result.model_dump()

    def _collect_keys(value: object, keys: set[str]) -> None:
        if isinstance(value, dict):
            keys.update(value.keys())
            for nested in value.values():
                _collect_keys(nested, keys)
        elif isinstance(value, list):
            for item in value:
                _collect_keys(item, keys)

    all_keys: set[str] = set()
    _collect_keys(dumped, all_keys)
    overlap = all_keys & _FORBIDDEN_MODEL_FIELD_NAMES
    assert overlap == set(), f"Successful dry-run result dump has forbidden key(s): {overlap}"


# ---------------------------------------------------------------------------
# 9. No disallowed imports in the new safety test helpers or service module.
# ---------------------------------------------------------------------------

_DISALLOWED_IMPORT_SUBSTRINGS = (
    "langgraph",
    "langsmith",
    "httpx",
    "requests",
    "openai",
    "anthropic",
    "gemini",
    "google.generativeai",
    "app.providers.places",
    "app.providers.routes",
    "app.providers.transit",
    "app.providers.accommodation",
    "app.providers.weather",
    "app.providers.holidays",
    "app.providers.currency",
    "app.providers.gateway",
)


def test_service_module_has_no_disallowed_imports() -> None:
    import app.services.ai_candidate_discovery_service as discovery_module

    imported_names = _imported_module_names(discovery_module)
    for name in imported_names:
        lowered = name.lower()
        for disallowed in _DISALLOWED_IMPORT_SUBSTRINGS:
            assert disallowed not in lowered, f"Disallowed import found: {name}"
    assert not any("planning_orchestrator" in name.lower() for name in imported_names)


def test_safety_test_module_itself_has_no_disallowed_imports() -> None:
    """Checks this file's own imports -- `planning_orchestrator`/API-route/
    scheduling-validation-regeneration module names are legitimately
    imported here (Category 7) purely to inspect them for the absence of
    an `AICandidateDiscoveryService` import, never to call them.
    """
    source = inspect.getsource(sys.modules[__name__])
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        for disallowed in _DISALLOWED_IMPORT_SUBSTRINGS:
            assert disallowed not in lowered, f"Disallowed import found: {name}"
