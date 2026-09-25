from __future__ import annotations

from typing import Any

import pytest

from app.providers.ai_itinerary_reasoning.anthropic_adapter import AnthropicAIItineraryReasoningProvider
from app.providers.ai_itinerary_reasoning.groq_adapter import GroqAIItineraryReasoningProvider
from test_groq_ai_itinerary_reasoning_provider import _request  # type: ignore[import-not-found]

# Section 202C.1A: the reasoning adapters record WHY a provider call failed from
# the structured taxonomy (never message text), so a rate limit during the
# reasoning stage of a targeted regeneration can be reported as one.


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("raw provider body org_SECRET must never surface")
        self.status_code = status_code


class _RaisingClient:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.messages = self

    def invoke(self, prompt: Any) -> Any:
        raise self._exc

    def create(self, **kwargs: Any) -> Any:
        raise self._exc


@pytest.mark.parametrize("provider_cls", [GroqAIItineraryReasoningProvider, AnthropicAIItineraryReasoningProvider])
def test_reasoning_adapters_record_the_structured_failure_kind(provider_cls: Any) -> None:
    for status, kind in ((429, "rate_limited"), (401, "authentication"), (504, "timeout_or_network")):
        provider = provider_cls(client=_RaisingClient(_StatusError(status)), api_key="fake")
        result = provider.reason(_request())
        assert result.status.value == "rejected"
        assert result.failure_kind == kind
        assert "SECRET" not in " ".join(result.guardrail_report.blocked_reasons)


def test_reasoning_adapter_leaves_failure_kind_empty_for_a_model_output_rejection() -> None:
    class _NoStructure:
        def invoke(self, prompt: Any) -> Any:
            return "not structured"

    result = GroqAIItineraryReasoningProvider(client=_NoStructure(), api_key="fake").reason(_request())
    assert result.status.value == "rejected" and result.failure_kind is None
