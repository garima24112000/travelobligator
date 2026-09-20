from __future__ import annotations

from typing import Any

from app.models.ai_feedback_interpretation import (
    AIFeedbackInterpretationRequest,
    AIFeedbackInterpretationStatus,
    AIFeedbackItineraryItemContext,
)
from app.models.ai_itinerary_reasoning import TravelerContextSummary
from app.providers.ai_feedback_interpreter.anthropic_adapter import (
    AnthropicAIFeedbackInterpreterProvider,
)

# Section 196 (docs/14_backend_architecture.md, following section 146):
# Anthropic feedback-interpreter adapter tests. Every test uses a fake/
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


class _FakeToolUseBlock:
    def __init__(self, name: str, tool_input: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = name
        self.input = tool_input


class _FakeResponse:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _FakeMessages:
    def __init__(self, response: Any = None, exception: Exception | None = None) -> None:
        self._response = response
        self._exception = exception

    def create(self, **kwargs: Any) -> Any:
        if self._exception is not None:
            raise self._exception
        return self._response


class _FakeClient:
    def __init__(self, response: Any = None, exception: Exception | None = None) -> None:
        self.messages = _FakeMessages(response=response, exception=exception)


def _base_tool_input(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "status": "completed",
        "scope": "single_experience",
        "actions": [{"type": "remove_experience", "experience_id": "exp_a"}],
        "new_place_requests": [],
        "preserve": {"day_indices": [], "experience_ids": []},
        "summary": "Removing Torre de Belem.",
        "confidence": 0.8,
    }
    fields.update(overrides)
    return fields


def test_returns_not_connected_when_no_key_configured() -> None:
    provider = AnthropicAIFeedbackInterpreterProvider(api_key=None)

    result = provider.interpret(_request())

    assert result.status == AIFeedbackInterpretationStatus.NOT_CONNECTED
    assert "ANTHROPIC_API_KEY" in " ".join(result.blocked_reasons)


def test_returns_completed_for_well_formed_tool_response() -> None:
    client = _FakeClient(
        response=_FakeResponse([_FakeToolUseBlock("submit_ai_feedback_interpretation", _base_tool_input())])
    )
    provider = AnthropicAIFeedbackInterpreterProvider(client=client)

    result = provider.interpret(_request())

    assert result.status == AIFeedbackInterpretationStatus.COMPLETED
    assert result.actions[0].experience_id == "exp_a"


def test_new_place_request_never_carries_factual_fields() -> None:
    tool_input = _base_tool_input(
        scope="new_place_request",
        actions=[],
        new_place_requests=[{"query": "Sintra"}],
    )
    client = _FakeClient(
        response=_FakeResponse([_FakeToolUseBlock("submit_ai_feedback_interpretation", tool_input)])
    )
    provider = AnthropicAIFeedbackInterpreterProvider(client=client)

    result = provider.interpret(_request(feedback_text="I also want to visit Sintra."))

    assert result.status == AIFeedbackInterpretationStatus.COMPLETED
    assert result.new_place_requests[0].query == "Sintra"
    dumped = result.model_dump()
    forbidden = {"provider_place_id", "coordinates", "price", "rating", "opening_hours"}
    assert forbidden.isdisjoint(dumped["new_place_requests"][0].keys())


def test_ambiguous_reference_returns_needs_clarification() -> None:
    tool_input = _base_tool_input(
        status="needs_clarification",
        scope=None,
        actions=[],
        clarification={"reason": "Two museums are scheduled.", "possible_experience_ids": ["exp_a", "exp_b"]},
        summary=None,
    )
    client = _FakeClient(
        response=_FakeResponse([_FakeToolUseBlock("submit_ai_feedback_interpretation", tool_input)])
    )
    provider = AnthropicAIFeedbackInterpreterProvider(client=client)

    result = provider.interpret(_request(feedback_text="Remove the museum."))

    assert result.status == AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION
    assert result.actions == []


def test_removal_of_nonexistent_place_is_rejected() -> None:
    tool_input = _base_tool_input(actions=[{"type": "remove_experience", "experience_id": "made-up-id"}])
    client = _FakeClient(
        response=_FakeResponse([_FakeToolUseBlock("submit_ai_feedback_interpretation", tool_input)])
    )
    provider = AnthropicAIFeedbackInterpreterProvider(client=client)

    result = provider.interpret(_request(feedback_text="Remove Eiffel Tower."))

    assert result.status == AIFeedbackInterpretationStatus.REJECTED


def test_conflicting_remove_and_move_is_rejected() -> None:
    tool_input = _base_tool_input(
        actions=[
            {"type": "remove_experience", "experience_id": "exp_a"},
            {"type": "move_experience", "experience_id": "exp_a", "target_day_index": 1},
        ]
    )
    client = _FakeClient(
        response=_FakeResponse([_FakeToolUseBlock("submit_ai_feedback_interpretation", tool_input)])
    )
    provider = AnthropicAIFeedbackInterpreterProvider(client=client)

    result = provider.interpret(_request())

    assert result.status == AIFeedbackInterpretationStatus.REJECTED


def test_missing_tool_use_block_is_rejected() -> None:
    client = _FakeClient(response=_FakeResponse([]))
    provider = AnthropicAIFeedbackInterpreterProvider(client=client)

    result = provider.interpret(_request())

    assert result.status == AIFeedbackInterpretationStatus.REJECTED


def test_client_raising_exception_causes_rejected_result() -> None:
    client = _FakeClient(exception=RuntimeError("simulated API failure"))
    provider = AnthropicAIFeedbackInterpreterProvider(client=client)

    result = provider.interpret(_request())

    assert result.status == AIFeedbackInterpretationStatus.REJECTED


def test_adapter_does_not_mutate_request() -> None:
    client = _FakeClient(
        response=_FakeResponse([_FakeToolUseBlock("submit_ai_feedback_interpretation", _base_tool_input())])
    )
    provider = AnthropicAIFeedbackInterpreterProvider(client=client)
    request = _request()
    before = request.model_dump()

    provider.interpret(request)

    assert request.model_dump() == before
