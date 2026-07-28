from __future__ import annotations

import ast
import inspect

import pytest
from pydantic import ValidationError

import app.services.ai_reasoning_contract_builder as contract_builder_module
from app.models.ai_reasoning import AIReasoningTask
from app.models.common import DataStatus, ProviderStatus, ProviderStatusEntry, UnavailableDataItem
from app.models.planning_state import (
    DestinationContext,
    ExperiencePlan,
    DailyPlan,
    FeedbackEvent,
    PlanningStage,
    PlanningState,
    RegenerationStrategy,
    TravelerProfile,
    TravelGroupType,
    TripPace,
    TripRequest,
    UserLock,
    ValidationReport,
)
from app.services.ai_reasoning_contract_builder import AIReasoningContractBuilder


def _trip_request(**overrides) -> TripRequest:
    fields = {
        "primary_destination": "Lisbon, Portugal",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
        "pace": TripPace.BALANCED,
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _planning_state(**trip_request_overrides) -> PlanningState:
    return PlanningState(trip_request=_trip_request(**trip_request_overrides))


def _feedback_event(text: str = "Make this less packed") -> FeedbackEvent:
    return FeedbackEvent(
        feedback_text=text,
        feedback_type="pace_change",
        affected_stages=[PlanningStage.EXPERIENCE_PLAN],
        regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
    )


@pytest.fixture()
def builder() -> AIReasoningContractBuilder:
    return AIReasoningContractBuilder()


# ---------------------------------------------------------------------------
# 1. unavailable_constraints_from_state converts PlanningState.unavailable_data.
# ---------------------------------------------------------------------------


def test_unavailable_constraints_from_state_converts_unavailable_data(
    builder: AIReasoningContractBuilder,
) -> None:
    planning_state = _planning_state()
    planning_state.unavailable_data.append(
        UnavailableDataItem(
            field="hotel_prices",
            reason="No accommodation provider is connected.",
            data_status=DataStatus.NOT_CONNECTED,
            source="accommodation_provider",
        )
    )

    constraints = builder.unavailable_constraints_from_state(planning_state)

    assert len(constraints) == 1
    assert constraints[0].field == "hotel_prices"
    assert constraints[0].reason == "No accommodation provider is connected."
    assert constraints[0].data_status == "not_connected"
    assert constraints[0].source == "accommodation_provider"


# ---------------------------------------------------------------------------
# 2. provider_status unavailable_fields are converted into constraints.
# ---------------------------------------------------------------------------


def test_provider_status_unavailable_fields_are_converted_into_constraints(
    builder: AIReasoningContractBuilder,
) -> None:
    planning_state = _planning_state()
    planning_state.provider_status["routes_provider:routes"] = ProviderStatusEntry(
        provider_name="routes_provider",
        provider_type="routes",
        status=ProviderStatus.NOT_CONNECTED,
        data_status=DataStatus.NOT_CONNECTED,
        unavailable_fields=["route_time_minutes", "distance_km"],
        error_message="routes provider is not connected.",
    )

    constraints = builder.unavailable_constraints_from_state(planning_state)

    fields = {constraint.field for constraint in constraints}
    assert fields == {"route_time_minutes", "distance_km"}
    for constraint in constraints:
        assert constraint.source == "routes_provider"
        assert constraint.data_status == "not_connected"
        assert constraint.reason == "routes provider is not connected."


# ---------------------------------------------------------------------------
# 3. duplicate unavailable constraints are de-duplicated deterministically.
# ---------------------------------------------------------------------------


def test_duplicate_unavailable_constraints_are_deduplicated(
    builder: AIReasoningContractBuilder,
) -> None:
    planning_state = _planning_state()
    planning_state.unavailable_data.append(
        UnavailableDataItem(
            field="hotel_prices",
            reason="No accommodation provider is connected.",
            data_status=DataStatus.NOT_CONNECTED,
            source="accommodation_provider",
        )
    )
    # Same field + reason + data_status + source, reached via provider_status
    # instead of unavailable_data -- must collapse into the same entry.
    planning_state.provider_status["accommodation_provider:accommodations"] = ProviderStatusEntry(
        provider_name="accommodation_provider",
        provider_type="accommodation",
        status=ProviderStatus.NOT_CONNECTED,
        data_status=DataStatus.NOT_CONNECTED,
        unavailable_fields=["hotel_prices"],
        error_message="No accommodation provider is connected.",
    )
    # A second provider_status entry with the exact same unavailable field
    # (repeated across multiple call sites) must also collapse.
    planning_state.provider_status["accommodation_provider:routes"] = ProviderStatusEntry(
        provider_name="accommodation_provider",
        provider_type="accommodation",
        status=ProviderStatus.NOT_CONNECTED,
        data_status=DataStatus.NOT_CONNECTED,
        unavailable_fields=["hotel_prices"],
        error_message="No accommodation provider is connected.",
    )

    constraints = builder.unavailable_constraints_from_state(planning_state)

    assert len(constraints) == 1
    assert constraints[0].field == "hotel_prices"

    # Calling again returns the exact same deterministic result.
    assert builder.unavailable_constraints_from_state(planning_state) == constraints


# ---------------------------------------------------------------------------
# 4. build_request rejects empty input_sections through AIReasoningRequest
#    validation.
# ---------------------------------------------------------------------------


def test_build_request_rejects_empty_input_sections(builder: AIReasoningContractBuilder) -> None:
    planning_state = _planning_state()

    with pytest.raises(ValidationError):
        builder.build_request(
            planning_state, AIReasoningTask.FEEDBACK_INTERPRETATION, input_sections=[]
        )


def test_build_request_returns_valid_request_for_non_empty_input_sections(
    builder: AIReasoningContractBuilder,
) -> None:
    planning_state = _planning_state()

    request = builder.build_request(
        planning_state,
        AIReasoningTask.FEEDBACK_INTERPRETATION,
        input_sections=["feedback_history"],
        user_prompt="Make this less packed",
    )

    assert request.trip_id == planning_state.trip_id
    assert request.input_sections == ["feedback_history"]
    assert request.user_prompt == "Make this less packed"


# ---------------------------------------------------------------------------
# 5. traveler_preference_interpretation evidence refs only appear for
#    existing user inputs.
# ---------------------------------------------------------------------------


def test_traveler_preference_evidence_refs_empty_when_no_user_input(
    builder: AIReasoningContractBuilder,
) -> None:
    planning_state = _planning_state()

    refs = builder.evidence_refs_for_task(
        planning_state, AIReasoningTask.TRAVELER_PREFERENCE_INTERPRETATION
    )

    assert refs == []


def test_traveler_preference_evidence_refs_only_for_populated_fields(
    builder: AIReasoningContractBuilder,
) -> None:
    planning_state = _planning_state(
        free_text_preferences="I like quiet mornings.",
        interests=["museums"],
        constraints=[],
    )

    refs = builder.evidence_refs_for_task(
        planning_state, AIReasoningTask.TRAVELER_PREFERENCE_INTERPRETATION
    )

    fields = {ref.source_field for ref in refs}
    assert fields == {"free_text_preferences", "interests"}
    assert all(ref.source_section == "trip_request" for ref in refs)


# ---------------------------------------------------------------------------
# 6. feedback_interpretation evidence refs include latest feedback only
#    when feedback exists.
# ---------------------------------------------------------------------------


def test_feedback_interpretation_evidence_refs_empty_when_no_feedback_or_locks(
    builder: AIReasoningContractBuilder,
) -> None:
    planning_state = _planning_state()

    refs = builder.evidence_refs_for_task(planning_state, AIReasoningTask.FEEDBACK_INTERPRETATION)

    assert refs == []


def test_feedback_interpretation_evidence_refs_include_latest_feedback(
    builder: AIReasoningContractBuilder,
) -> None:
    planning_state = _planning_state()
    first = _feedback_event("Make this less packed")
    second = _feedback_event("Add more museums")
    planning_state.feedback_history.extend([first, second])

    refs = builder.evidence_refs_for_task(planning_state, AIReasoningTask.FEEDBACK_INTERPRETATION)

    feedback_text_refs = [ref for ref in refs if ref.source_field == "feedback_text"]
    assert len(feedback_text_refs) == 1
    assert feedback_text_refs[0].source_id == second.feedback_event_id

    summary_refs = [ref for ref in refs if ref.source_field == "total_feedback_items"]
    assert len(summary_refs) == 1
    assert summary_refs[0].source_section == "pending_feedback_summary"


def test_feedback_interpretation_evidence_refs_include_active_locks_only_when_present(
    builder: AIReasoningContractBuilder,
) -> None:
    planning_state = _planning_state()
    planning_state.user_locks.append(
        UserLock(locked_item_type="experience", locked_item_id="experience_1", is_active=False)
    )

    refs_without_active_lock = builder.evidence_refs_for_task(
        planning_state, AIReasoningTask.FEEDBACK_INTERPRETATION
    )
    assert refs_without_active_lock == []

    planning_state.user_locks.append(
        UserLock(locked_item_type="experience", locked_item_id="experience_2", is_active=True)
    )
    refs_with_active_lock = builder.evidence_refs_for_task(
        planning_state, AIReasoningTask.FEEDBACK_INTERPRETATION
    )
    assert len(refs_with_active_lock) == 1
    assert refs_with_active_lock[0].source_section == "user_locks"
    assert refs_with_active_lock[0].source_field == "active_locks"


# ---------------------------------------------------------------------------
# 7. stay_area_explanation includes accommodation unavailable evidence when
#    accommodation_options is unavailable.
# ---------------------------------------------------------------------------


def test_stay_area_explanation_includes_accommodation_unavailable_evidence(
    builder: AIReasoningContractBuilder,
) -> None:
    planning_state = _planning_state()
    planning_state.unavailable_data.append(
        UnavailableDataItem(
            field="accommodation_options",
            reason="No accommodation provider is connected.",
            data_status=DataStatus.NOT_CONNECTED,
            source="accommodation_provider",
        )
    )

    refs = builder.evidence_refs_for_task(planning_state, AIReasoningTask.STAY_AREA_EXPLANATION)

    matching = [
        ref
        for ref in refs
        if ref.source_section == "unavailable_data" and ref.source_field == "accommodation_options"
    ]
    assert len(matching) == 1


def test_stay_area_explanation_omits_accommodation_unavailable_evidence_when_available(
    builder: AIReasoningContractBuilder,
) -> None:
    planning_state = _planning_state()

    refs = builder.evidence_refs_for_task(planning_state, AIReasoningTask.STAY_AREA_EXPLANATION)

    assert not any(ref.source_section == "unavailable_data" for ref in refs)


# ---------------------------------------------------------------------------
# 8. evidence refs do not include forbidden travel-fact field names or
#    risky strings.
# ---------------------------------------------------------------------------


_FORBIDDEN_FIELD_TOKENS = (
    "price",
    "rating",
    "opening_hours",
    "booking_url",
    "reservation_url",
    "route_time",
    "safety_score",
)
_FORBIDDEN_CLAIM_SUBSTRINGS = (
    "$",
    "stars",
    "top" + " rated",
    "best hotel",
    "book now",
)


def _fully_populated_planning_state() -> PlanningState:
    planning_state = _planning_state(
        free_text_preferences="I like quiet mornings.",
        interests=["museums"],
        constraints=["no early mornings"],
    )
    planning_state.traveler_profile = TravelerProfile(
        travel_group_type=TravelGroupType.COUPLE,
        travelers_count=2,
        pace=TripPace.BALANCED,
    )
    planning_state.destination_context = DestinationContext(
        destination_name="Lisbon, Portugal",
        candidate_pois=[{"place_id": "poi_1", "name": "Test Attraction"}],
        candidate_accommodation_pois=[{"place_id": "acc_1", "name": "Test Accommodation POI"}],
    )
    planning_state.experience_plan = ExperiencePlan(
        daily_plans=[DailyPlan(day_number=1, date="2026-08-10")]
    )
    planning_state.validation_report = ValidationReport()
    planning_state.provider_coverage.places = "success"
    planning_state.provider_coverage.accommodations = "open_poi_available"
    planning_state.provider_coverage.routes = "not_connected"
    planning_state.feedback_history.append(_feedback_event())
    planning_state.user_locks.append(
        UserLock(locked_item_type="experience", locked_item_id="experience_1", is_active=True)
    )
    planning_state.unavailable_data.append(
        UnavailableDataItem(
            field="accommodation_options",
            reason="No accommodation provider is connected.",
            data_status=DataStatus.NOT_CONNECTED,
            source="accommodation_provider",
        )
    )
    return planning_state


def test_evidence_refs_never_contain_forbidden_field_names_or_risky_strings(
    builder: AIReasoningContractBuilder,
) -> None:
    planning_state = _fully_populated_planning_state()

    all_refs = []
    for task in AIReasoningTask:
        all_refs.extend(builder.evidence_refs_for_task(planning_state, task))

    assert len(all_refs) > 0
    for ref in all_refs:
        lowered_field = ref.source_field.lower()
        for token in _FORBIDDEN_FIELD_TOKENS:
            assert token not in lowered_field, f"source_field {ref.source_field!r} contains {token!r}"

        lowered_claim = ref.claim.lower()
        for token in _FORBIDDEN_CLAIM_SUBSTRINGS:
            if token == "$":
                assert "$" not in ref.claim, f"claim {ref.claim!r} contains a currency amount"
            else:
                assert token not in lowered_claim, f"claim {ref.claim!r} contains {token!r}"


# ---------------------------------------------------------------------------
# 9. build_request does not mutate PlanningState.
# ---------------------------------------------------------------------------


def test_build_request_does_not_mutate_planning_state(builder: AIReasoningContractBuilder) -> None:
    planning_state = _fully_populated_planning_state()
    before = planning_state.model_copy(deep=True)

    for task in AIReasoningTask:
        builder.build_request(planning_state, task, input_sections=["trip_request"])

    assert planning_state == before


# ---------------------------------------------------------------------------
# 10. no helper calls provider adapters, LLM clients, LangGraph, or
#     LangSmith.
# ---------------------------------------------------------------------------


def test_contract_builder_module_never_imports_llm_or_provider_code() -> None:
    """Parses the module's actual `import`/`from ... import` statements
    (via `ast`, not a raw substring search) so this doesn't false-positive
    on the module's own docstrings, which legitimately name "LangGraph"/
    "LangSmith" to explain that they are *not* called yet.
    """
    source = inspect.getsource(contract_builder_module)
    tree = ast.parse(source)

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_module_prefixes = (
        "langgraph",
        "langsmith",
        "openai",
        "anthropic",
        "google.generativeai",
        "app.providers",
        "httpx",
        "requests",
    )
    for module_name in imported_modules:
        lowered = module_name.lower()
        for forbidden in forbidden_module_prefixes:
            assert not lowered.startswith(forbidden), (
                f"Unexpected import of {module_name!r} in contract builder module."
            )

    # Separately confirm no code path reaches the shared provider gateway
    # singleton or a raw HTTP client -- checked against real identifiers
    # only (never bare "provider"/"http", which legitimately appear in
    # prose comments/docstrings describing what this module deliberately
    # does not do).
    assert "provider_gateway" not in source
    assert "requests." not in source
    assert "httpx." not in source
