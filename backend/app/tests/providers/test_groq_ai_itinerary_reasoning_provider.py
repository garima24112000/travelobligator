from __future__ import annotations

import sys
import types
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
from app.providers.ai_itinerary_reasoning import groq_adapter as groq_adapter_module
from app.providers.ai_itinerary_reasoning.groq_adapter import (
    GroqAIItineraryReasoningProvider,
    _GroqItineraryReasoningSchema,
)

# Safety tests for the Groq-backed itinerary-reasoning adapter (Section
# 193B, docs/14_backend_architecture.md section 142). Every test here uses
# only in-file fake clients that mimic `client.invoke(prompt)` -- no
# network call is ever made, and no real GROQ_API_KEY is required.

_FORBIDDEN_FIELD_NAMES = {
    "coordinates",
    "price",
    "rating",
    "opening_hours",
    "availability",
    "booking_url",
    "route_duration",
    "route_distance",
}


def _no_key_settings() -> Settings:
    return Settings(GROQ_API_KEY=None)


class _FakeGroqClient:
    def __init__(self, handler: Callable[[str], Any]) -> None:
        self._handler = handler

    def invoke(self, prompt: str) -> Any:
        return self._handler(prompt)


def _client_returning(response: Any) -> _FakeGroqClient:
    return _FakeGroqClient(lambda prompt: response)


def _client_raising(exc: BaseException) -> _FakeGroqClient:
    def _raise(prompt: str) -> Any:
        raise exc

    return _FakeGroqClient(_raise)


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
        "tradeoffs": None,
        "approximate_structure": [
            {"candidate_id": candidate_ids[0], "time_window": "morning"},
        ],
    }


def _valid_output(**overrides: object) -> dict[str, object]:
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
    monkeypatch.setattr(groq_adapter_module, "get_settings", _no_key_settings)
    provider = GroqAIItineraryReasoningProvider(api_key=None)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.NOT_CONNECTED
    assert result.days == []
    assert result.confidence == 0.0


def test_no_key_result_includes_provider_and_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(groq_adapter_module, "get_settings", _no_key_settings)
    provider = GroqAIItineraryReasoningProvider(api_key=None, model="openai/gpt-oss-20b")

    result = provider.reason(_request())

    assert result.provider_name == "groq_ai_itinerary_reasoning_provider"
    assert result.model_name == "openai/gpt-oss-20b"
    assert result.guardrail_report.passed is False


def test_no_api_key_path_does_not_import_langchain_groq(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(groq_adapter_module, "get_settings", _no_key_settings)
    monkeypatch.delitem(sys.modules, "langchain_groq", raising=False)

    provider = GroqAIItineraryReasoningProvider(api_key=None)
    provider.reason(_request())

    assert "langchain_groq" not in sys.modules


# ---------------------------------------------------------------------------
# 2. Valid response -> completed, candidate IDs/days/strategy/time windows.
# ---------------------------------------------------------------------------


def test_valid_response_produces_completed_result() -> None:
    client = _client_returning(_valid_output())
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.COMPLETED
    assert result.guardrail_report.passed is True
    assert result.strategy is not None
    assert result.strategy.pace == "balanced"
    assert len(result.days) == 1
    day = result.days[0]
    assert day.candidate_ids == ["openstreetmap_places:way/1"]
    assert day.approximate_structure[0].time_window.value == "morning"


def test_structured_output_schema_instance_is_handled() -> None:
    """The normal `with_structured_output` result is a
    `_GroqItineraryReasoningSchema` instance, not a plain dict."""
    schema_instance = _GroqItineraryReasoningSchema(**_valid_output())
    client = _client_returning(schema_instance)
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.COMPLETED


# ---------------------------------------------------------------------------
# 3. Unknown candidate ID -> rejected.
# ---------------------------------------------------------------------------


def test_unknown_candidate_id_is_rejected() -> None:
    client = _client_returning(
        _valid_output(days=[_valid_day_dict(1, ["made-up-provider:123"])])
    )
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.REJECTED
    assert result.days == []
    assert any("made-up-provider:123" in reason for reason in result.guardrail_report.blocked_reasons)


# ---------------------------------------------------------------------------
# 4. Duplicate candidate ID across days -> rejected.
# ---------------------------------------------------------------------------


def test_duplicate_candidate_across_days_is_rejected() -> None:
    client = _client_returning(
        _valid_output(
            days=[
                _valid_day_dict(1, ["openstreetmap_places:way/1"]),
                _valid_day_dict(2, ["openstreetmap_places:way/1"]),
            ]
        )
    )
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.REJECTED
    assert any("appears on both day" in reason for reason in result.guardrail_report.blocked_reasons)


# ---------------------------------------------------------------------------
# 5. Invalid day (exceeds trip_duration_days) -> rejected.
# ---------------------------------------------------------------------------


def test_invalid_day_index_is_rejected() -> None:
    client = _client_returning(
        _valid_output(days=[_valid_day_dict(9, ["openstreetmap_places:way/1"])])
    )
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request(trip_duration_days=3))

    assert result.status == AIItineraryReasoningStatus.REJECTED
    assert any("exceeds trip_duration_days" in reason for reason in result.guardrail_report.blocked_reasons)


# ---------------------------------------------------------------------------
# 6. Exact-time/factual overclaim in rationale -> rejected.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden_text",
    [
        "This place has a great rating",
        "The price is low",
        "opening_hours: 9am-5pm",
        "booking_url available soon",
        "meet at 09:15",
        "exact travel time is 10 minutes",
    ],
)
def test_forbidden_factual_claim_in_rationale_is_rejected(forbidden_text: str) -> None:
    day = _valid_day_dict(1, ["openstreetmap_places:way/1"])
    day["rationale"] = forbidden_text
    client = _client_returning(_valid_output(days=[day]))
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.REJECTED
    assert result.days == []


# ---------------------------------------------------------------------------
# 7. Fake client raising exception -> rejected, sanitized.
# ---------------------------------------------------------------------------


def test_client_raising_exception_causes_rejected_result() -> None:
    client = _client_raising(RuntimeError("simulated API failure"))
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.REJECTED
    assert result.days == []
    assert result.guardrail_report.passed is False


def test_api_key_never_leaks_into_rejected_message() -> None:
    real_groq_400_message = (
        "Error code: 400 - {'error': {'message': 'Failed to parse tool call arguments as "
        "JSON', 'type': 'invalid_request_error'}}"
    )
    client = _client_raising(RuntimeError(real_groq_400_message))
    provider = GroqAIItineraryReasoningProvider(client=client, api_key="gsk_fake_should_not_leak")

    result = provider.reason(_request())

    full_message = " ".join(result.guardrail_report.blocked_reasons)
    assert "gsk_fake_should_not_leak" not in full_message
    assert "Authorization" not in full_message
    assert "Bearer" not in full_message


# ---------------------------------------------------------------------------
# 8. Invalid JSON/schema -> honest rejected result.
# ---------------------------------------------------------------------------


def test_non_coercible_response_causes_rejected_result() -> None:
    client = _client_returning("just a plain text response, not structured output")
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.REJECTED
    assert result.days == []


def test_malformed_response_missing_strategy_causes_rejected_result() -> None:
    client = _client_returning({"days": [], "confidence": 0.5})
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.REJECTED


def test_empty_days_list_causes_rejected_result() -> None:
    client = _client_returning(_valid_output(days=[]))
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.REJECTED
    assert result.days == []


def test_malformed_day_entry_causes_rejected_result() -> None:
    client = _client_returning(_valid_output(days=["not-a-dict"]))
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())

    assert result.status == AIItineraryReasoningStatus.REJECTED


# ---------------------------------------------------------------------------
# 9. No forbidden provider-fact fields anywhere in the result.
# ---------------------------------------------------------------------------


def test_result_dump_has_no_forbidden_factual_keys() -> None:
    client = _client_returning(_valid_output())
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.reason(_request())
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
    overlap = all_keys & _FORBIDDEN_FIELD_NAMES
    assert overlap == set(), f"Result dump has forbidden key(s): {overlap}"


# ---------------------------------------------------------------------------
# 10. Structured Outputs request shape -- json_schema, strict=True, no
#     tools/tool_choice (Task 5/18, the Section 191A.1 lesson).
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

    def bind_tools(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "GroqAIItineraryReasoningProvider must never call bind_tools/tool-calling when "
            "using Structured Outputs (method='json_schema')."
        )


def _install_fake_langchain_groq(monkeypatch: pytest.MonkeyPatch) -> type[_KwargCapturingFakeChatGroq]:
    fake_module = types.ModuleType("langchain_groq")
    fake_module.ChatGroq = _KwargCapturingFakeChatGroq  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_groq", fake_module)
    return _KwargCapturingFakeChatGroq


def test_build_client_uses_structured_outputs_not_tool_calling(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_cls = _install_fake_langchain_groq(monkeypatch)

    provider = GroqAIItineraryReasoningProvider(api_key="fake-key", model="openai/gpt-oss-20b")
    provider._build_client()

    assert fake_cls.captured_init_kwargs["model"] == "openai/gpt-oss-20b"
    assert fake_cls.captured_structured_output_kwargs["method"] == "json_schema"
    assert fake_cls.captured_structured_output_kwargs["strict"] is True


def test_build_client_never_sends_tools_or_tool_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_cls = _install_fake_langchain_groq(monkeypatch)

    provider = GroqAIItineraryReasoningProvider(api_key="fake-key")
    provider._build_client()

    all_captured_kwargs = {
        **fake_cls.captured_init_kwargs,
        **fake_cls.captured_structured_output_kwargs,
    }
    assert "tools" not in all_captured_kwargs
    assert "tool_choice" not in all_captured_kwargs


def test_build_client_does_not_leak_api_key_into_structured_output_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_cls = _install_fake_langchain_groq(monkeypatch)
    secret_key = "gsk_should_not_leak_into_structured_output_call"

    provider = GroqAIItineraryReasoningProvider(api_key=secret_key)
    provider._build_client()

    assert fake_cls.captured_init_kwargs["api_key"] == secret_key
    structured_output_values = list(fake_cls.captured_structured_output_kwargs.values()) + list(
        fake_cls.captured_structured_output_args
    )
    assert secret_key not in structured_output_values
    assert not any(secret_key in str(value) for value in structured_output_values)


# ---------------------------------------------------------------------------
# 11. Model name/config-derived defaults.
# ---------------------------------------------------------------------------


def test_settings_derived_api_key_and_model_used_when_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        groq_adapter_module,
        "get_settings",
        lambda: Settings(GROQ_API_KEY="settings-derived-key", GROQ_MODEL="settings-derived-model"),
    )

    provider = GroqAIItineraryReasoningProvider()

    assert provider._api_key == "settings-derived-key"
    assert provider._model == "settings-derived-model"


def test_ai_itinerary_reasoning_model_setting_takes_priority_over_groq_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        groq_adapter_module,
        "get_settings",
        lambda: Settings(
            GROQ_API_KEY="key",
            GROQ_MODEL="groq-default-model",
            AI_ITINERARY_REASONING_MODEL="reasoning-specific-model",
        ),
    )

    provider = GroqAIItineraryReasoningProvider()

    assert provider._model == "reasoning-specific-model"


# ---------------------------------------------------------------------------
# 12. Adapter never mutates its input request.
# ---------------------------------------------------------------------------


def test_adapter_does_not_mutate_request() -> None:
    client = _client_returning(_valid_output())
    provider = GroqAIItineraryReasoningProvider(client=client)
    request = _request()
    before = request.model_dump()

    provider.reason(request)

    assert request.model_dump() == before
