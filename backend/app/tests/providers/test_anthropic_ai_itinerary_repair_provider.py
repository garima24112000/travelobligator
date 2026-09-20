from __future__ import annotations

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
from app.providers.ai_itinerary_reasoning import anthropic_adapter as anthropic_adapter_module
from app.providers.ai_itinerary_reasoning.anthropic_adapter import AnthropicAIItineraryReasoningProvider

# Safety tests for the Anthropic-backed itinerary-repair adapter (Section
# 194A, docs/14_backend_architecture.md, following section 143). Every
# test here uses only in-file fake clients that mimic
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


def _tool_use_response(tool_input: Any, tool_name: str = "submit_ai_itinerary_repair") -> _FakeResponse:
    block = _FakeContentBlock("tool_use", name=tool_name, input=tool_input)
    return _FakeResponse([block])


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


def _valid_tool_input(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "repaired_days": [
            {
                "day_index": 2,
                "candidate_ids": ["openstreetmap_places:way/2"],
                "rationale": "Kept only the closer stop to reduce the day's spread.",
            }
        ],
        "repair_summary": "Dropped the cafe stop from day 2 to reduce geographic spread.",
        "addressed_issue_types": ["geographic_spread"],
        "confidence": 0.7,
    }
    fields.update(overrides)
    return fields


def test_no_api_key_and_no_client_returns_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic_adapter_module, "get_settings", _no_key_settings)
    provider = AnthropicAIItineraryReasoningProvider(api_key=None)

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.NOT_CONNECTED
    assert result.repaired_days == []


def test_valid_tool_response_produces_completed_result() -> None:
    client = _client_returning(_tool_use_response(_valid_tool_input()))
    provider = AnthropicAIItineraryReasoningProvider(client=client)

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.COMPLETED
    assert result.repaired_days[0].day_index == 2
    assert result.repaired_days[0].candidate_ids == ["openstreetmap_places:way/2"]


def test_missing_tool_use_block_is_rejected() -> None:
    client = _client_returning(_FakeResponse([_FakeContentBlock("text", text="no tool call")]))
    provider = AnthropicAIItineraryReasoningProvider(client=client)

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.REJECTED


def test_unknown_candidate_id_is_rejected() -> None:
    tool_input = _valid_tool_input(
        repaired_days=[
            {"day_index": 2, "candidate_ids": ["made-up-provider:999"], "rationale": "Invented."}
        ]
    )
    client = _client_returning(_tool_use_response(tool_input))
    provider = AnthropicAIItineraryReasoningProvider(client=client)

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.REJECTED


def test_repairing_an_unaffected_day_is_rejected() -> None:
    tool_input = _valid_tool_input(
        repaired_days=[
            {
                "day_index": 1,
                "candidate_ids": ["openstreetmap_places:way/1"],
                "rationale": "Changed the unaffected day.",
            }
        ]
    )
    client = _client_returning(_tool_use_response(tool_input))
    provider = AnthropicAIItineraryReasoningProvider(client=client)

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.REJECTED


def test_forbidden_factual_claim_is_rejected() -> None:
    tool_input = _valid_tool_input(repair_summary="This is a cheap, highly rated change.")
    client = _client_returning(_tool_use_response(tool_input))
    provider = AnthropicAIItineraryReasoningProvider(client=client)

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.REJECTED


def test_client_raising_exception_causes_rejected_result() -> None:
    client = _client_raising(RuntimeError("simulated API failure"))
    provider = AnthropicAIItineraryReasoningProvider(client=client)

    result = provider.repair(_request())

    assert result.status == AIItineraryRepairStatus.REJECTED


def test_adapter_does_not_mutate_request() -> None:
    client = _client_returning(_tool_use_response(_valid_tool_input()))
    provider = AnthropicAIItineraryReasoningProvider(client=client)
    request = _request()
    before = request.model_dump()

    provider.repair(request)

    assert request.model_dump() == before
