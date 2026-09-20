from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.models.ai_feedback_interpretation import (
    AIFeedbackInterpretationRequest,
    AIFeedbackInterpretationStatus,
    AIFeedbackItineraryItemContext,
)
from app.models.ai_itinerary_reasoning import TravelerContextSummary
from app.providers.ai_feedback_interpreter.groq_adapter import GroqAIFeedbackInterpreterProvider

# Section 196 (docs/14_backend_architecture.md, following section 146):
# Groq feedback-interpreter adapter tests. Every test uses a fake/
# injected client -- never a real network call.


def _item(experience_id: str, name: str, day_index: int) -> AIFeedbackItineraryItemContext:
    return AIFeedbackItineraryItemContext(
        experience_id=experience_id, name=name, category="attraction", day_index=day_index
    )


def _request(**overrides: Any) -> AIFeedbackInterpretationRequest:
    fields: dict[str, Any] = {
        "trip_id": "trip_001",
        "destination_name": "Lisbon, Portugal",
        "start_date": "2026-09-10",
        "end_date": "2026-09-12",
        "trip_duration_days": 3,
        "traveler_context": TravelerContextSummary(travelers_count=2, travel_group_type="couple", pace="balanced"),
        "feedback_text": "Remove Belem Tower.",
        "current_items": [
            _item("exp_a", "Torre de Belem", 2),
            _item("exp_b", "Castelo de Sao Jorge", 1),
        ],
    }
    fields.update(overrides)
    return AIFeedbackInterpretationRequest(**fields)


class _FakeClient:
    def __init__(self, response: Any) -> None:
        self._response = response

    def invoke(self, prompt: str) -> Any:
        return self._response


class _RaisingClient:
    def invoke(self, prompt: str) -> Any:
        raise RuntimeError("simulated network/timeout failure")


def _base_output(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "status": "completed",
        "scope": "single_experience",
        "actions": [
            {
                "type": "remove_experience",
                "experience_id": "exp_a",
                "target_day_index": None,
                "pace": None,
                "category": None,
                "direction": None,
                "day_index": None,
                "note": None,
            }
        ],
        "new_place_requests": [],
        "preserve": {"day_indices": [], "experience_ids": []},
        "clarification": None,
        "summary": "Removing Torre de Belem.",
        "confidence": 0.8,
    }
    fields.update(overrides)
    return fields


def test_returns_not_connected_when_no_key_configured() -> None:
    provider = GroqAIFeedbackInterpreterProvider(api_key=None)

    result = provider.interpret(_request())

    assert result.status == AIFeedbackInterpretationStatus.NOT_CONNECTED
    assert "GROQ_API_KEY" in " ".join(result.blocked_reasons)


def test_returns_completed_for_well_formed_remove_action() -> None:
    provider = GroqAIFeedbackInterpreterProvider(client=_FakeClient(_base_output()))

    result = provider.interpret(_request())

    assert result.status == AIFeedbackInterpretationStatus.COMPLETED
    assert len(result.actions) == 1
    assert result.actions[0].type.value == "remove_experience"
    assert result.actions[0].experience_id == "exp_a"


def test_move_experience_action_parses_correctly() -> None:
    output = _base_output(
        actions=[
            {
                "type": "move_experience",
                "experience_id": "exp_b",
                "target_day_index": 3,
                "pace": None,
                "category": None,
                "direction": None,
                "day_index": None,
                "note": None,
            }
        ]
    )
    provider = GroqAIFeedbackInterpreterProvider(client=_FakeClient(output))

    result = provider.interpret(_request(feedback_text="Move the castle to day 3."))

    assert result.status == AIFeedbackInterpretationStatus.COMPLETED
    assert result.actions[0].target_day_index == 3


def test_preserve_and_regenerate_day_parse_correctly() -> None:
    output = _base_output(
        scope="single_day",
        actions=[
            {
                "type": "regenerate_day",
                "experience_id": None,
                "target_day_index": None,
                "pace": None,
                "category": None,
                "direction": None,
                "day_index": 2,
                "instruction": "Make it less packed.",
                "note": None,
            }
        ],
        preserve={"day_indices": [1], "experience_ids": []},
        summary="Redoing day 2, keeping day 1.",
    )
    provider = GroqAIFeedbackInterpreterProvider(client=_FakeClient(output))

    result = provider.interpret(_request(feedback_text="Redo day 2 but don't change day 1."))

    assert result.status == AIFeedbackInterpretationStatus.COMPLETED
    assert result.preserve.day_indices == [1]
    assert result.actions[0].day_index == 2


def test_preference_change_parses_pace_and_interest_actions() -> None:
    output = _base_output(
        scope="preferences_only",
        actions=[
            {
                "type": "change_pace",
                "experience_id": None,
                "target_day_index": None,
                "pace": "relaxed",
                "category": None,
                "direction": None,
                "day_index": None,
                "note": None,
            },
            {
                "type": "adjust_interest",
                "experience_id": None,
                "target_day_index": None,
                "pace": None,
                "category": "food",
                "direction": "more",
                "day_index": None,
                "note": None,
            },
        ],
        summary="Making the trip more relaxed with more food stops.",
    )
    provider = GroqAIFeedbackInterpreterProvider(client=_FakeClient(output))

    result = provider.interpret(_request(feedback_text="Make the trip more relaxed and add more food."))

    assert result.status == AIFeedbackInterpretationStatus.COMPLETED
    assert result.actions[0].pace == "relaxed"
    assert result.actions[1].category == "food"
    assert result.actions[1].direction == "more"


def test_new_place_request_never_carries_factual_fields() -> None:
    output = _base_output(
        scope="new_place_request",
        actions=[],
        new_place_requests=[{"query": "Sintra", "note": None}],
        summary="Noting a request to visit Sintra.",
    )
    provider = GroqAIFeedbackInterpreterProvider(client=_FakeClient(output))

    result = provider.interpret(_request(feedback_text="I also want to visit Sintra."))

    assert result.status == AIFeedbackInterpretationStatus.COMPLETED
    assert len(result.new_place_requests) == 1
    assert result.new_place_requests[0].query == "Sintra"
    assert result.new_place_requests[0].requires_provider_lookup is True
    dumped = result.model_dump()
    forbidden = {"provider_place_id", "coordinates", "price", "rating", "opening_hours", "booking_url"}
    assert forbidden.isdisjoint(dumped.keys())
    assert forbidden.isdisjoint(dumped["new_place_requests"][0].keys())


def test_removal_of_nonexistent_place_is_rejected() -> None:
    output = _base_output(
        actions=[
            {
                "type": "remove_experience",
                "experience_id": "made-up-eiffel-tower",
                "target_day_index": None,
                "pace": None,
                "category": None,
                "direction": None,
                "day_index": None,
                "note": None,
            }
        ]
    )
    provider = GroqAIFeedbackInterpreterProvider(client=_FakeClient(output))

    result = provider.interpret(_request(feedback_text="Remove Eiffel Tower."))

    assert result.status == AIFeedbackInterpretationStatus.REJECTED
    assert result.actions == []


def test_ambiguous_reference_returns_needs_clarification() -> None:
    output = _base_output(
        status="needs_clarification",
        scope=None,
        actions=[],
        clarification={
            "reason": "Two museums are currently scheduled.",
            "possible_experience_ids": ["exp_a", "exp_b"],
        },
        summary=None,
    )
    provider = GroqAIFeedbackInterpreterProvider(client=_FakeClient(output))

    result = provider.interpret(_request(feedback_text="Remove the museum."))

    assert result.status == AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION
    assert result.clarification is not None
    assert result.clarification.possible_experience_ids == ["exp_a", "exp_b"]
    assert result.actions == []


def test_user_factual_claim_produces_only_a_remove_action() -> None:
    output = _base_output(summary="Removing Torre de Belem as requested.")
    provider = GroqAIFeedbackInterpreterProvider(client=_FakeClient(output))

    result = provider.interpret(_request(feedback_text="Remove Torre de Belem because it is closed."))

    assert result.status == AIFeedbackInterpretationStatus.COMPLETED
    assert len(result.actions) == 1
    assert result.actions[0].type.value == "remove_experience"
    dumped = result.model_dump()
    assert "opening_status" not in dumped
    assert "opening_hours" not in str(dumped).lower()


def test_conflicting_remove_and_move_is_rejected() -> None:
    output = _base_output(
        actions=[
            {
                "type": "remove_experience",
                "experience_id": "exp_a",
                "target_day_index": None,
                "pace": None,
                "category": None,
                "direction": None,
                "day_index": None,
                "note": None,
            },
            {
                "type": "move_experience",
                "experience_id": "exp_a",
                "target_day_index": 1,
                "pace": None,
                "category": None,
                "direction": None,
                "day_index": None,
                "note": None,
            },
        ]
    )
    provider = GroqAIFeedbackInterpreterProvider(client=_FakeClient(output))

    result = provider.interpret(_request())

    assert result.status == AIFeedbackInterpretationStatus.REJECTED


def test_day_specific_complaint_widened_to_whole_itinerary_is_rejected() -> None:
    """Section 196.1: an explicitly day-scoped complaint must not survive
    as a `completed` global pace change, even if the model's raw output is
    otherwise schema-valid."""
    output = _base_output(
        scope="whole_itinerary",
        actions=[
            {
                "type": "change_pace",
                "experience_id": None,
                "target_day_index": None,
                "pace": "relaxed",
                "category": None,
                "direction": None,
                "day_index": None,
                "instruction": None,
                "note": None,
            }
        ],
        summary="Making the whole trip more relaxed.",
    )
    provider = GroqAIFeedbackInterpreterProvider(client=_FakeClient(output))

    result = provider.interpret(_request(feedback_text="Day 2 is too packed. Make it more relaxed."))

    assert result.status == AIFeedbackInterpretationStatus.REJECTED
    assert any("explicitly named day(s)" in reason for reason in result.blocked_reasons)


def test_unknown_action_type_is_rejected() -> None:
    output = _base_output(
        actions=[
            {
                "type": "teleport_experience",
                "experience_id": "exp_a",
                "target_day_index": None,
                "pace": None,
                "category": None,
                "direction": None,
                "day_index": None,
                "note": None,
            }
        ]
    )
    provider = GroqAIFeedbackInterpreterProvider(client=_FakeClient(output))

    result = provider.interpret(_request())

    assert result.status == AIFeedbackInterpretationStatus.REJECTED


def test_client_raising_exception_causes_rejected_result() -> None:
    provider = GroqAIFeedbackInterpreterProvider(client=_RaisingClient())

    result = provider.interpret(_request())

    assert result.status == AIFeedbackInterpretationStatus.REJECTED


def test_api_key_never_leaks_into_rejected_message() -> None:
    real_groq_400_message = (
        "Error code: 400 - {'error': {'message': 'Failed to parse tool call arguments as "
        "JSON', 'type': 'invalid_request_error'}}"
    )
    client = _RaisingClient()

    class _RealisticRaisingClient:
        def invoke(self, prompt: str) -> Any:
            raise RuntimeError(real_groq_400_message)

    provider = GroqAIFeedbackInterpreterProvider(
        client=_RealisticRaisingClient(), api_key="gsk_fake_should_not_leak"
    )

    result = provider.interpret(_request())

    assert "gsk_fake_should_not_leak" not in " ".join(result.blocked_reasons)


def test_result_dump_has_no_forbidden_factual_keys() -> None:
    provider = GroqAIFeedbackInterpreterProvider(client=_FakeClient(_base_output()))

    result = provider.interpret(_request())
    dumped = result.model_dump(mode="json")
    forbidden_field_names = {
        "price",
        "rating",
        "review_count",
        "route_duration",
        "travel_time",
        "booking_url",
        "opening_hours",
        "coordinates",
        "availability",
    }

    def _collect_keys(value: Any, keys: set[str]) -> None:
        if isinstance(value, dict):
            keys.update(value.keys())
            for nested in value.values():
                _collect_keys(nested, keys)
        elif isinstance(value, list):
            for item in value:
                _collect_keys(item, keys)

    all_keys: set[str] = set()
    _collect_keys(dumped, all_keys)
    assert all_keys & forbidden_field_names == set()


# ---------------------------------------------------------------------------
# Structured Outputs request shape -- json_schema, strict=True, no
# tools/tool_choice (Task 18, the Section 191A.1 lesson).
# ---------------------------------------------------------------------------


class _KwargCapturingFakeChatGroq:
    captured_init_kwargs: dict[str, Any]
    captured_structured_output_args: tuple[Any, ...]
    captured_structured_output_kwargs: dict[str, Any]

    def __init__(self, **kwargs: Any) -> None:
        type(self).captured_init_kwargs = kwargs

    def with_structured_output(self, *args: Any, **kwargs: Any) -> "_KwargCapturingFakeChatGroq":
        type(self).captured_structured_output_args = args
        type(self).captured_structured_output_kwargs = kwargs
        return self


def _install_fake_langchain_groq(monkeypatch: Any) -> type[_KwargCapturingFakeChatGroq]:
    import sys
    import types

    fake_module = types.ModuleType("langchain_groq")
    fake_module.ChatGroq = _KwargCapturingFakeChatGroq  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_groq", fake_module)
    return _KwargCapturingFakeChatGroq


def test_build_client_uses_structured_outputs_not_tool_calling(monkeypatch: Any) -> None:
    fake_cls = _install_fake_langchain_groq(monkeypatch)

    provider = GroqAIFeedbackInterpreterProvider(api_key="fake-key", model="openai/gpt-oss-20b")
    provider._build_client()

    assert fake_cls.captured_structured_output_kwargs["method"] == "json_schema"
    assert fake_cls.captured_structured_output_kwargs["strict"] is True
    all_kwargs = {**fake_cls.captured_init_kwargs, **fake_cls.captured_structured_output_kwargs}
    assert "tools" not in all_kwargs
    assert "tool_choice" not in all_kwargs


def test_settings_derived_api_key_and_model_used_when_omitted(monkeypatch: Any) -> None:
    import app.providers.ai_feedback_interpreter.groq_adapter as groq_adapter_module

    monkeypatch.setattr(
        groq_adapter_module,
        "get_settings",
        lambda: Settings(GROQ_API_KEY="settings-derived-key", GROQ_MODEL="settings-derived-model"),
    )

    provider = GroqAIFeedbackInterpreterProvider()

    assert provider._api_key == "settings-derived-key"
    assert provider._model == "settings-derived-model"


def test_adapter_does_not_mutate_request() -> None:
    provider = GroqAIFeedbackInterpreterProvider(client=_FakeClient(_base_output()))
    request = _request()
    before = request.model_dump()

    provider.interpret(request)

    assert request.model_dump() == before
