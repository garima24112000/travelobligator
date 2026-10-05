from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.providers import llm_structured_clients
from app.providers.ai_failure import (
    AIProviderFailureKind,
    LLMStructuredOutputError,
    classify_ai_provider_exception,
    health_failure_kind,
    is_transient_failure,
    provider_reset_seconds,
)
from app.providers.llm_structured_clients import GeminiStructuredClient
from app.tests.providers.llm_failover_support import (
    GEMINI_KEY,
    GEMINI_MODEL,
    gemini_body,
    gemini_error_body,
    json_response,
    real_gemini_client,
)

# The REAL `google-genai` client over a mock HTTP transport: one invocation
# is one HTTP request, no tool is ever sent, and the answer is validated
# against the stage's wire schema locally. No network.


class _Schema(BaseModel):
    name: str
    count: int


def _recording(handler: Any) -> tuple[list[httpx.Request], Any]:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    return requests, handle


def test_a_valid_answer_becomes_an_instance_of_the_wire_schema() -> None:
    requests, handler = _recording(lambda request: json_response(gemini_body('{"name": "a", "count": 2}', total_tokens=31)))
    client = real_gemini_client(_Schema, handler, timeout=4.5)

    answer = client.invoke("the prompt")

    assert answer == _Schema(name="a", count=2)
    assert client.total_tokens == 31  # real usage metadata, never an estimate
    assert len(requests) == 1


def test_the_request_carries_the_schema_and_no_tool_of_any_kind() -> None:
    requests, handler = _recording(lambda request: json_response(gemini_body('{"name": "a", "count": 2}')))
    real_gemini_client(_Schema, handler, timeout=4.5).invoke("the prompt")

    (request,) = requests
    body = json.loads(request.content)
    # no Google Search / Maps grounding, no function calling, no retrieval
    assert not {"tools", "toolConfig", "tool_config", "cachedContent"} & set(body)
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == _Schema.model_json_schema()
    assert (config["temperature"], config["maxOutputTokens"]) == (0.2, 512)
    # the model is whatever was configured -- nothing is hardcoded
    assert request.url.path.endswith(f"/models/{GEMINI_MODEL}:generateContent")
    # the key travels as a header only, never in the URL
    assert request.headers["x-goog-api-key"] == GEMINI_KEY and GEMINI_KEY not in str(request.url)
    # the attempt's own timeout (what the stage budget had left)
    assert request.extensions["timeout"]["read"] == 4.5


def test_the_sdk_is_told_to_make_exactly_one_attempt() -> None:
    client = real_gemini_client(_Schema, lambda request: json_response(gemini_body("{}")))
    assert client._client._api_client._http_options.retry_options.attempts == 1


@pytest.mark.parametrize(
    ("status", "name", "kind", "health"),
    [
        (503, "UNAVAILABLE", AIProviderFailureKind.PROVIDER_ERROR, "provider_unavailable"),
        (500, "INTERNAL", AIProviderFailureKind.PROVIDER_ERROR, "server_error"),
        (429, "RESOURCE_EXHAUSTED", AIProviderFailureKind.RATE_LIMITED, "rate_limit"),
        (408, "DEADLINE_EXCEEDED", AIProviderFailureKind.TIMEOUT_OR_NETWORK, "timeout"),
        (401, "UNAUTHENTICATED", AIProviderFailureKind.AUTHENTICATION, "authentication_error"),
        (403, "PERMISSION_DENIED", AIProviderFailureKind.AUTHENTICATION, "authentication_error"),
        (404, "NOT_FOUND", AIProviderFailureKind.PROVIDER_ERROR, "unknown_transport"),
        (400, "INVALID_ARGUMENT", AIProviderFailureKind.PROVIDER_ERROR, "unknown_transport"),
    ],
)
def test_a_failed_request_is_made_once_and_classified_from_its_status(
    status: int, name: str, kind: AIProviderFailureKind, health: str
) -> None:
    """Every status the SDK would retry by default (408, 429, 5xx) reaches
    the application after ONE request: the stage budget owns the retry."""
    requests, handler = _recording(lambda request: json_response(gemini_error_body(status, name), status))
    client = real_gemini_client(_Schema, handler)

    with pytest.raises(Exception) as raised:
        client.invoke("the prompt")

    assert len(requests) == 1
    assert classify_ai_provider_exception(raised.value) == kind
    assert health_failure_kind(raised.value) == health


def test_the_observed_high_demand_503_is_unavailability_not_quota() -> None:
    body = {"error": {"code": 503, "message": "This model is currently experiencing high demand.", "status": "UNAVAILABLE"}}
    requests, handler = _recording(lambda request: json_response(body, 503))

    with pytest.raises(Exception) as raised:
        real_gemini_client(_Schema, handler).invoke("the prompt")

    assert len(requests) == 1
    assert health_failure_kind(raised.value) == "provider_unavailable"
    assert classify_ai_provider_exception(raised.value) != AIProviderFailureKind.RATE_LIMITED
    assert is_transient_failure(raised.value) is True
    assert provider_reset_seconds(raised.value) is None  # no quota reset to wait for


def test_a_rate_limit_exposes_the_providers_structured_retry_delay() -> None:
    body = gemini_error_body(429, "RESOURCE_EXHAUSTED")
    body["error"]["details"] = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "34s"}]
    requests, handler = _recording(lambda request: json_response(body, 429))

    with pytest.raises(Exception) as raised:
        real_gemini_client(_Schema, handler).invoke("the prompt")

    assert len(requests) == 1 and provider_reset_seconds(raised.value) == 34.0


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError])
def test_a_transport_error_is_made_once_and_is_a_timeout_or_network_failure(error: type[Exception]) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise error("boom", request=request)

    with pytest.raises(Exception) as raised:
        real_gemini_client(_Schema, handler).invoke("the prompt")

    assert len(requests) == 1
    assert classify_ai_provider_exception(raised.value) == AIProviderFailureKind.TIMEOUT_OR_NETWORK
    assert is_transient_failure(raised.value) is True


@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        '{"name": "a"',  # truncated
        '{"name": "a"}',  # a required key is missing
        '{"name": "a", "count": "many"}',  # wrong type
        "[]",
        "   ",
        "",
    ],
)
def test_an_answer_that_is_not_valid_structured_output_is_rejected(text: str) -> None:
    requests, handler = _recording(lambda request: json_response(gemini_body(text)))
    client = real_gemini_client(_Schema, handler)

    with pytest.raises(LLMStructuredOutputError) as raised:
        client.invoke("the prompt")

    assert len(requests) == 1
    # a structural failure: the provider answered, so it is never a transport failure
    assert classify_ai_provider_exception(raised.value) == AIProviderFailureKind.MALFORMED_OUTPUT
    assert health_failure_kind(raised.value) == "schema_validation"
    assert is_transient_failure(raised.value) is False
    # the error never carries the model's text
    assert raised.value.__cause__ is None and (not text.strip() or text not in str(raised.value))
    # usage is still read, so the advisory token counter stays truthful
    assert client.total_tokens == 12


def test_an_answer_with_no_candidate_at_all_is_a_structural_failure() -> None:
    client = real_gemini_client(_Schema, lambda request: json_response({"candidates": []}))
    with pytest.raises(LLMStructuredOutputError):
        client.invoke("the prompt")
    assert client.total_tokens is None  # nothing reported, nothing invented


def test_extra_keys_in_the_answer_cannot_enter_through_the_wire_schema() -> None:
    text = '{"name": "a", "count": 2, "price": "$12", "rating": 4.8, "latitude": 38.7}'
    answer = real_gemini_client(_Schema, lambda request: json_response(gemini_body(text))).invoke("the prompt")
    assert answer.model_dump() == {"name": "a", "count": 2}


def test_the_google_sdk_is_imported_lazily_and_no_model_name_is_hardcoded() -> None:
    tree = ast.parse(Path(llm_structured_clients.__file__).read_text())
    module_level = {
        alias.name if isinstance(node, ast.Import) else node.module
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [node])
    }
    assert not any("google" in (name or "") for name in module_level)

    app_root = Path(llm_structured_clients.__file__).resolve().parents[1]
    for path in app_root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value.strip().lower()
                assert not (value.startswith("gemini-") and " " not in value), f"hardcoded model in {path.name}: {value}"


def test_the_client_needs_no_network_to_be_built() -> None:
    client = GeminiStructuredClient(
        _Schema, api_key=GEMINI_KEY, model=GEMINI_MODEL, temperature=0.0, max_tokens=10, timeout=0.0004
    )
    # a sub-millisecond budget is still a positive timeout, never "no timeout"
    assert client._client._api_client._http_options.timeout == 1
