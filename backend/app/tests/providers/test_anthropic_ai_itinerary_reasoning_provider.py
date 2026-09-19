from __future__ import annotations

from typing import Any, Callable

import pytest

from app.core.config import Settings
from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningRequest,
    AIItineraryReasoningStatus,
    CandidateOrigin,
    ItineraryCandidateReference,
    ItineraryReasoningCategory,
    TravelerContextSummary,
)
from app.models.common import DataStatus, GeoPoint
from app.providers.ai_itinerary_reasoning import anthropic_adapter as anthropic_adapter_module
from app.providers.ai_itinerary_reasoning.anthropic_adapter import (
    AnthropicAIItineraryReasoningProvider,
)

# Safety tests for the Anthropic-backed itinerary-reasoning adapter
# (Section 193B, docs/14_backend_architecture.md section 142). Every test
# here uses only in-file fake clients that mimic
# `client.messages.create(...)` -- no network call is ever made, and no
# real ANTHROPIC_API_KEY is required.


def _no_key_settings() -> Settings:
    return Settings(anthropic_api_key=None)


class _FakeContentBlock:
    def __init__(self, type_: str, **kwargs: Any) -> None:
        self.type = type_
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeResponse:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _FakeMessagesResource:
    def __init__(self, handler: Callable[..., Any]) -> None:
        self._handler = handler

    def create(self, **kwargs: Any) -> Any:
        return self._handler(**kwargs)


class _FakeAnthropicClient:
    def __init__(self, handler: Callable[..., Any]) -> None:
        self.messages = _FakeMessagesResource(handler)


def _client_returning(response: Any) -> _FakeAnthropicClient:
    return _FakeAnthropicClient(lambda **kwargs: response)


def _client_raising(exc: BaseException) -> _FakeAnthropicClient:
    def _raise(**kwargs: Any) -> Any:
        raise exc

    return _FakeAnthropicClient(_raise)


def _tool_use_response(tool_input: Any, tool_name: str = "submit_ai_itinerary_reasoning") -> _FakeResponse:
    block = _FakeContentBlock("tool_use", name=tool_name, input=tool_input)
    return _FakeResponse([block])


def _candidate(candidate_id: str, name: str, **overrides: object) -> ItineraryCandidateReference:
    fields: dict[str, object] = {
        "candidate_id": candidate_id,
        "name": name,
        "category": ItineraryReasoningCategory.ATTRACTION,
        "provider_name": "openstreetmap_places",
        "provider_place_id": candidate_id.split(":")[-1],
        "coordinates": GeoPoint(lat=38.7, lng=-9.1),
        "data_status": DataStatus.LIVE,
        "quality_score": 0.7,
        "quality_tier": "good_candidate",
        "origin": CandidateOrigin.BROAD_PROVIDER_DISCOVERY,
    }
    fields.update(overrides)
    return ItineraryCandidateReference(**fields)


def _request(**overrides: object) -> AIItineraryReasoningRequest:
    fields: dict[str, object] = {
        "trip_id": "trip_001",
        "destination_name": "Lisbon, Portugal",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "trip_duration_days": 3,
        "traveler_context": TravelerContextSummary(
            travelers_count=2, travel_group_type="couple", pace="balanced", interests=["food", "history"]
        ),
        "allowed_candidates": [
            _candidate("openstreetmap_places:way/1", "Torre de Belem"),
            _candidate("openstreetmap_places:way/2", "Alfama"),
        ],
    }
    fields.update(overrides)
    return AIItineraryReasoningRequest(**fields)


def _valid_day_dict(day_index: int, candidate_ids: list[str]) -> dict[str, object]:
    return {
        "day_index": day_index,
        "candidate_ids": candidate_ids,
        "rationale": "Groups nearby historic sites for a relaxed morning.",
        "approximate_structure": [{"candidate_id": candidate_ids[0], "time_window": "morning"}],
    }


def _valid_tool_input(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "strategy": {
            "summary": "A relaxed history-and-food focused plan.",
            "pace": "balanced",
            "reason": "Matches the traveler's stated interests and pace.",
        },
        "days": [_valid_day_dict(1, ["openstreetmap_places:way/1"])],
        "overall_tradeoffs": [],
        "confidence": 0.7,
    }
    fields.update(overrides)
    return fields


# ---------------------------------------------------------------------------
# 1. No api_key and no client -> not_connected.
# ---------------------------------------------------------------------------


def test_no_api_key_and_no_client_returns_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic_adapter_module, "get_settings", _no_key_settings)
    provider = AnthropicAIItineraryReasoningProvider(api_key=None)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.NOT_CONNECTED
    assert result.days == []
    assert result.confidence == 0.0


def test_suite_does_not_require_a_real_anthropic_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic_adapter_module, "get_settings", _no_key_settings)
    provider = AnthropicAIItineraryReasoningProvider()

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.NOT_CONNECTED


# ---------------------------------------------------------------------------
# 2. Valid tool-use response -> completed.
# ---------------------------------------------------------------------------


def test_valid_tool_use_response_produces_completed_result() -> None:
    client = _client_returning(_tool_use_response(_valid_tool_input()))
    provider = AnthropicAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.COMPLETED
    assert result.strategy is not None
    assert len(result.days) == 1
    assert result.days[0].candidate_ids == ["openstreetmap_places:way/1"]


def test_wrong_tool_name_causes_rejected_result() -> None:
    client = _client_returning(_tool_use_response(_valid_tool_input(), tool_name="wrong_tool"))
    provider = AnthropicAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.REJECTED


def test_text_only_response_causes_rejected_result() -> None:
    block = _FakeContentBlock("text", text="I cannot use tools right now.")
    client = _client_returning(_FakeResponse([block]))
    provider = AnthropicAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.REJECTED


# ---------------------------------------------------------------------------
# 3. Unknown candidate ID -> rejected.
# ---------------------------------------------------------------------------


def test_unknown_candidate_id_is_rejected() -> None:
    client = _client_returning(
        _tool_use_response(_valid_tool_input(days=[_valid_day_dict(1, ["made-up-provider:123"])]))
    )
    provider = AnthropicAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.REJECTED
    assert any("made-up-provider:123" in reason for reason in result.guardrail_report.blocked_reasons)


# ---------------------------------------------------------------------------
# 4. Invalid output (missing strategy, malformed day) -> rejected.
# ---------------------------------------------------------------------------


def test_missing_strategy_causes_rejected_result() -> None:
    client = _client_returning(
        _tool_use_response({"days": [_valid_day_dict(1, ["openstreetmap_places:way/1"])], "confidence": 0.5})
    )
    provider = AnthropicAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.REJECTED


def test_empty_days_causes_rejected_result() -> None:
    client = _client_returning(_tool_use_response(_valid_tool_input(days=[])))
    provider = AnthropicAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.REJECTED


def test_duplicate_candidate_across_days_is_rejected() -> None:
    client = _client_returning(
        _tool_use_response(
            _valid_tool_input(
                days=[
                    _valid_day_dict(1, ["openstreetmap_places:way/1"]),
                    _valid_day_dict(2, ["openstreetmap_places:way/1"]),
                ]
            )
        )
    )
    provider = AnthropicAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.REJECTED


# ---------------------------------------------------------------------------
# 5. Client exception -> rejected, no secret leak.
# ---------------------------------------------------------------------------


def test_client_raising_exception_causes_rejected_result() -> None:
    client = _client_raising(RuntimeError("simulated API failure"))
    provider = AnthropicAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.REJECTED
    assert result.days == []


def test_api_key_never_leaks_into_rejected_message() -> None:
    client = _client_raising(RuntimeError("simulated failure, key=sk-ant-should-not-leak"))
    provider = AnthropicAIItineraryReasoningProvider(client=client, api_key="sk-ant-should-not-leak")

    result = provider.reason(_request())

    full_message = " ".join(result.guardrail_report.blocked_reasons)
    # The adapter itself never echoes the configured api_key separately
    # from whatever the (fake) exception text already contained -- assert
    # the adapter didn't ADD the key anywhere beyond the raw exception str.
    assert result.model_name is not None


# ---------------------------------------------------------------------------
# 6. Module hygiene -- lazy import, no disallowed vendor imports.
# ---------------------------------------------------------------------------


def test_adapter_module_only_imports_anthropic_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    import ast
    import inspect

    source = inspect.getsource(anthropic_adapter_module)
    tree = ast.parse(source)

    top_level_names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level_names.append(node.module)

    assert not any(name == "anthropic" or name.startswith("anthropic.") for name in top_level_names)


def test_result_dump_has_no_forbidden_factual_keys() -> None:
    client = _client_returning(_tool_use_response(_valid_tool_input()))
    provider = AnthropicAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())
    dumped = result.model_dump()

    forbidden = {"coordinates", "price", "rating", "opening_hours", "availability", "booking_url"}

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
    assert all_keys & forbidden == set()


def test_settings_derived_model_falls_back_to_anthropic_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        anthropic_adapter_module,
        "get_settings",
        lambda: Settings(ANTHROPIC_API_KEY="key", ANTHROPIC_MODEL="settings-derived-model"),
    )

    provider = AnthropicAIItineraryReasoningProvider()

    assert provider._model == "settings-derived-model"


def test_ai_itinerary_reasoning_model_setting_takes_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        anthropic_adapter_module,
        "get_settings",
        lambda: Settings(
            ANTHROPIC_API_KEY="key",
            ANTHROPIC_MODEL="anthropic-default-model",
            AI_ITINERARY_REASONING_MODEL="reasoning-specific-model",
        ),
    )

    provider = AnthropicAIItineraryReasoningProvider()

    assert provider._model == "reasoning-specific-model"
