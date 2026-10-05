"""Shared fakes for the Groq <-> Gemini resilience tests. No network, no real
waiting: a fake clock that the scripted clients advance, and an HTTP mock
transport for the one place the real Gemini SDK is exercised."""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from app.core.config import get_settings
from app.providers import ai_stage_budget
from app.providers.ai_candidate_proposal import groq_adapter as anchor_module
from app.providers.ai_itinerary_reasoning import groq_adapter as reasoning_module
from app.providers.ai_stage_budget import StageRun
from app.providers.itinerary_narrator import groq_adapter as narrator_module
from app.providers.llm_provider_health import get_llm_provider_health
from app.providers.llm_structured_clients import GeminiStructuredClient

GROQ_KEY = "SENTINEL_GROQ_KEY_FAILOVER_0001"
GEMINI_KEY = "SENTINEL_GEMINI_KEY_FAILOVER_0002"
GEMINI_MODEL = "gemini-test-model"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class _Response:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


class StatusError(Exception):
    """A provider SDK error as the classifier sees one: an HTTP status, an
    optional provider error code and optional response headers. Its text
    must never reach a result, a log line or a report."""

    def __init__(self, status_code: int, code: str | None = None, headers: dict[str, str] | None = None) -> None:
        super().__init__("RAW PROVIDER BODY org_SECRET must never surface")
        self.status_code = status_code
        self.code = code
        if headers is not None:
            self.response = _Response(headers)


def structural_error() -> Exception:
    return StatusError(400, "json_validate_failed")


class ScriptedClient:
    """`invoke` takes `seconds` of fake time, then returns / raises the step."""

    def __init__(self, clock: FakeClock | None = None, *steps: tuple[float, Any], total_tokens: int | None = None) -> None:
        self._clock = clock
        self._steps = list(steps)
        self.prompts: list[str] = []
        self.total_tokens = total_tokens

    def invoke(self, prompt: str) -> Any:
        self.prompts.append(prompt)
        seconds, outcome = self._steps.pop(0)
        if self._clock is not None:
            self._clock.now += seconds
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def configure_pair(monkeypatch: pytest.MonkeyPatch, *, groq: bool = True, gemini: bool = True, **env: str) -> None:
    """Both providers of the pair configured (or one), plus any extra settings."""
    monkeypatch.setenv("GROQ_API_KEY", GROQ_KEY if groq else "")
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_KEY if gemini else "")
    monkeypatch.setenv("GEMINI_MODEL", GEMINI_MODEL if gemini else "")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def use_clock(monkeypatch: pytest.MonkeyPatch, clock: FakeClock) -> None:
    """Every stage budget and the provider-health record run on `clock`,
    with the real (1 s) transport backoff taken from it, not slept."""
    monkeypatch.setattr(ai_stage_budget, "TRANSPORT_RETRY_BACKOFF_SECONDS", 1.0)
    for module in (anchor_module, reasoning_module, narrator_module):
        monkeypatch.setattr(
            module, "StageRun", lambda *args, **kwargs: StageRun(*args, clock=clock, sleep=clock.sleep, **kwargs)
        )
    monkeypatch.setattr(get_llm_provider_health(), "_clock", clock)


def install_clients(
    monkeypatch: pytest.MonkeyPatch,
    provider_cls: Any,
    *,
    groq: Any = None,
    gemini: Any = None,
    groq_method: str = "_build_client",
) -> list[tuple[str, float | None]]:
    """Makes `provider_cls` hand out the given scripted clients, recording
    which provider each request attempt was built for and its timeout."""
    built: list[tuple[str, float | None]] = []

    def build_groq(self: Any, *args: Any, timeout: float | None = None, **kwargs: Any) -> Any:
        built.append(("groq", timeout))
        return groq

    def build_gemini(self: Any, *args: Any, **kwargs: Any) -> Any:
        built.append(("gemini", args[-1] if args else kwargs.get("timeout")))
        return gemini

    monkeypatch.setattr(provider_cls, groq_method, build_groq)
    monkeypatch.setattr(provider_cls, "_build_gemini_client", build_gemini)
    return built


def gemini_body(text: str, total_tokens: int = 12) -> dict[str, Any]:
    """A Gemini `generateContent` success body carrying `text`."""
    return {
        "candidates": [{"content": {"parts": [{"text": text}], "role": "model"}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 5, "totalTokenCount": total_tokens},
    }


def gemini_error_body(status: int, name: str) -> dict[str, Any]:
    return {"error": {"code": status, "message": "RAW PROVIDER BODY must never surface", "status": name}}


def real_gemini_client(
    schema: Any, handler: Callable[[httpx.Request], httpx.Response], *, timeout: float = 5.0
) -> GeminiStructuredClient:
    """The REAL Gemini client over a mock HTTP transport (no network)."""
    return GeminiStructuredClient(
        schema,
        api_key=GEMINI_KEY,
        model=GEMINI_MODEL,
        temperature=0.2,
        max_tokens=512,
        timeout=timeout,
        httpx_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def json_response(body: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers, content=json.dumps(body).encode(), extensions={})
