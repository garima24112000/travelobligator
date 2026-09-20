from __future__ import annotations

import sys
import types
from typing import Any, Callable

import pytest

from app.core.config import Settings
from app.models.ai_itinerary_reasoning import (
    CandidateOrigin,
    ItineraryCandidateReference,
    ItineraryReasoningCategory,
    ItineraryReasoningDayPlan,
    TravelerContextSummary,
)
from app.models.ai_itinerary_repair import AIItineraryRepairIssue, AIItineraryRepairRequest, AIItineraryRepairStatus, RepairableIssueType
from app.models.common import DataStatus, GeoPoint, ValidationSeverity
from app.providers.ai_itinerary_reasoning import groq_adapter as groq_adapter_module
from app.providers.ai_itinerary_reasoning.groq_adapter import GroqAIItineraryReasoningProvider

# Safety tests for the Groq-backed itinerary-repair adapter (Section 194A,
# docs/14_backend_architecture.md, following section 143). Every test
# here uses only in-file fake clients that mimic `client.invoke(prompt)`
# -- no network call is ever made, and no real GROQ_API_KEY is required.

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


def _candidate(candidate_id: str, name: str) -> ItineraryCandidateReference:
    return ItineraryCandidateReference(
        candidate_id=candidate_id,
        name=name,
        category=ItineraryReasoningCategory.ATTRACTION,
        provider_name="openstreetmap_places",
        provider_place_id=candidate_id.split(":")[-1],
        coordinates=GeoPoint(lat=38.7, lng=-9.1),
        data_status=DataStatus.LIVE,
        quality_score=0.7,
        quality_tier="good_candidate",
        origin=CandidateOrigin.BROAD_PROVIDER_DISCOVERY,
    )


def _issue(day_index: int = 2) -> AIItineraryRepairIssue:
    return AIItineraryRepairIssue(
        issue_type=RepairableIssueType.GEOGRAPHIC_SPREAD,
        day_index=day_index,
        source_category="geographic_spread",
        message="Day 2 is geographically spread out.",
        severity=ValidationSeverity.WARNING,
    )


def _request(**overrides: object) -> AIItineraryRepairRequest:
    fields: dict[str, object] = {
        "trip_id": "trip_001",
        "destination_name": "Lisbon, Portugal",
        "start_date": "2026-09-10",
        "end_date": "2026-09-11",
        "trip_duration_days": 2,
        "traveler_context": TravelerContextSummary(
            travelers_count=2, travel_group_type="couple", pace="balanced"
        ),
        "allowed_candidates": [
            _candidate("openstreetmap_places:way/1", "Torre de Belem"),
            _candidate("openstreetmap_places:way/2", "Alfama"),
            _candidate("openstreetmap_places:way/3", "Belem Tower Area Cafe"),
        ],
        "original_days": [
            ItineraryReasoningDayPlan(
                day_index=1, candidate_ids=["openstreetmap_places:way/1"], rationale="Day one."
            ),
            ItineraryReasoningDayPlan(
                day_index=2,
                candidate_ids=["openstreetmap_places:way/2", "openstreetmap_places:way/3"],
                rationale="Day two, spread out.",
            ),
        ],
        "affected_days": [2],
        "issues": [_issue()],
    }
    fields.update(overrides)
    return AIItineraryRepairRequest(**fields)


def _valid_repaired_day_dict(day_index: int, candidate_ids: list[str]) -> dict[str, object]:
    return {
        "day_index": day_index,
        "candidate_ids": candidate_ids,
        "rationale": "Kept only the closer stop to reduce the day's spread.",
        "tradeoffs": None,
        "approximate_structure": [],
    }


def _valid_output(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "repaired_days": [_valid_repaired_day_dict(2, ["openstreetmap_places:way/2"])],
        "repair_summary": "Dropped the cafe stop from day 2 to reduce geographic spread.",
        "addressed_issue_types": ["geographic_spread"],
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

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.NOT_CONNECTED
    assert result.repaired_days == []


# ---------------------------------------------------------------------------
# 2. Valid structured output -> completed result, only affected day
#    changed.
# ---------------------------------------------------------------------------


def test_valid_response_produces_completed_result() -> None:
    client = _client_returning(_valid_output())
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.COMPLETED
    assert len(result.repaired_days) == 1
    assert result.repaired_days[0].day_index == 2
    assert result.repaired_days[0].candidate_ids == ["openstreetmap_places:way/2"]
    assert result.repair_summary


# ---------------------------------------------------------------------------
# Task 24: hallucinated candidate is rejected.
# ---------------------------------------------------------------------------


def test_unknown_candidate_id_is_rejected() -> None:
    client = _client_returning(
        _valid_output(
            repaired_days=[_valid_repaired_day_dict(2, ["made-up-provider:999"])],
        )
    )
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.REJECTED
    assert result.repaired_days == []


# ---------------------------------------------------------------------------
# Task 23: repairing an unaffected day is rejected.
# ---------------------------------------------------------------------------


def test_repairing_an_unaffected_day_is_rejected() -> None:
    client = _client_returning(
        _valid_output(
            repaired_days=[_valid_repaired_day_dict(1, ["openstreetmap_places:way/1"])],
        )
    )
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.REJECTED


# ---------------------------------------------------------------------------
# Task 25: forbidden factual claim in rationale/summary is rejected.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("forbidden_text", ["highly rated", "cheap", "open until", "17-minute"])
def test_forbidden_factual_claim_in_rationale_is_rejected(forbidden_text: str) -> None:
    day = _valid_repaired_day_dict(2, ["openstreetmap_places:way/2"])
    day["rationale"] = f"This stop is {forbidden_text} according to reviews."
    client = _client_returning(_valid_output(repaired_days=[day]))
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.REJECTED


def test_forbidden_factual_claim_in_repair_summary_is_rejected() -> None:
    client = _client_returning(_valid_output(repair_summary="This is a cheap and highly rated change."))
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.REJECTED


# ---------------------------------------------------------------------------
# Failure paths.
# ---------------------------------------------------------------------------


def test_client_raising_exception_causes_rejected_result() -> None:
    client = _client_raising(RuntimeError("simulated API failure"))
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.REJECTED


def test_api_key_never_leaks_into_rejected_message() -> None:
    real_groq_400_message = (
        "Error code: 400 - {'error': {'message': 'Failed to parse tool call arguments as "
        "JSON', 'type': 'invalid_request_error'}}"
    )
    client = _client_raising(RuntimeError(real_groq_400_message))
    provider = GroqAIItineraryReasoningProvider(client=client, api_key="gsk_fake_should_not_leak")

    result = provider.repair(_request())

    assert "gsk_fake_should_not_leak" not in " ".join(result.guardrail_report.blocked_reasons)


def test_empty_repaired_days_causes_rejected_result() -> None:
    client = _client_returning(_valid_output(repaired_days=[]))
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.REJECTED


def test_missing_repair_summary_causes_rejected_result() -> None:
    output = _valid_output()
    del output["repair_summary"]
    client = _client_returning(output)
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.REJECTED


def test_result_dump_has_no_forbidden_factual_keys() -> None:
    client = _client_returning(_valid_output())
    provider = GroqAIItineraryReasoningProvider(client=client)

    result = provider.repair(_request())
    dumped = result.model_dump(mode="json")

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
    assert all_keys & _FORBIDDEN_FIELD_NAMES == set()


# ---------------------------------------------------------------------------
# Structured Outputs request shape -- json_schema, strict=True, no
# tools/tool_choice (same 191A.1 lesson as `reason`).
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


def _install_fake_langchain_groq(monkeypatch: pytest.MonkeyPatch) -> type[_KwargCapturingFakeChatGroq]:
    fake_module = types.ModuleType("langchain_groq")
    fake_module.ChatGroq = _KwargCapturingFakeChatGroq  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_groq", fake_module)
    return _KwargCapturingFakeChatGroq


def test_build_repair_client_uses_structured_outputs_not_tool_calling(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_cls = _install_fake_langchain_groq(monkeypatch)

    provider = GroqAIItineraryReasoningProvider(api_key="fake-key", model="openai/gpt-oss-20b")
    provider._build_repair_client()

    assert fake_cls.captured_init_kwargs["model"] == "openai/gpt-oss-20b"
    assert fake_cls.captured_structured_output_kwargs["method"] == "json_schema"
    assert fake_cls.captured_structured_output_kwargs["strict"] is True


def test_build_repair_client_never_sends_tools_or_tool_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_cls = _install_fake_langchain_groq(monkeypatch)

    provider = GroqAIItineraryReasoningProvider(api_key="fake-key")
    provider._build_repair_client()

    all_captured_kwargs = {**fake_cls.captured_init_kwargs, **fake_cls.captured_structured_output_kwargs}
    assert "tools" not in all_captured_kwargs
    assert "tool_choice" not in all_captured_kwargs


def test_adapter_does_not_mutate_request() -> None:
    client = _client_returning(_valid_output())
    provider = GroqAIItineraryReasoningProvider(client=client)
    request = _request()
    before = request.model_dump()

    provider.repair(request)

    assert request.model_dump() == before
