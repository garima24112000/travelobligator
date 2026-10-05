"""Structured-output clients behind the generation-time model stages.

Each stage adapter (`ai_candidate_proposal`, `ai_itinerary_reasoning`,
`itinerary_narrator`) calls `client.invoke(prompt)` and gets back an
instance of ITS OWN wire schema, whichever provider answered. Everything
after that call -- reference resolution, per-item validation, grounding,
sanitizing -- is the stage's single existing code path, so no provider can
bypass a check the other goes through.

  * Groq keeps the `ChatGroq(...).with_structured_output` runnable the
    adapters already build. `GroqQuotaCapture` only adds the rate-limit
    numbers of the response: they are read by an httpx response hook on the
    client handed to the Groq SDK, because `langchain_groq` itself discards
    the headers.
  * `GeminiStructuredClient` calls the official `google-genai` SDK and
    validates the returned JSON against the same wire schema locally.

Both make exactly ONE HTTP request per `invoke`: the SDKs' own retries are
off (`max_retries=0` / `HttpRetryOptions(attempts=1)`); the single recovery
attempt of a stage belongs to `app/providers/ai_stage_budget.StageRun`.

Gemini is a reasoning base only. No `tools` are ever sent (no Google Search
or Maps grounding, no function calling), the model name comes only from
`GEMINI_MODEL`, and the answer is never trusted because JSON was asked for.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.providers.ai_failure import LLMStructuredOutputError
from app.providers.llm_provider_health import GroqQuotaSnapshot, parse_groq_quota_headers


def _new_http_client(**kwargs: Any) -> httpx.Client:
    """The HTTP client handed to the Groq SDK (a seam for tests, which
    supply a mock transport here instead of reaching the network)."""
    return httpx.Client(**kwargs)


class GroqQuotaCapture:
    """An httpx client for ONE Groq request that remembers the response's
    rate-limit numbers (and nothing else of the response)."""

    def __init__(self) -> None:
        self.quota: GroqQuotaSnapshot | None = None
        self.http_client = _new_http_client(event_hooks={"response": [self._on_response]})

    def _on_response(self, response: httpx.Response) -> None:
        try:
            self.quota = parse_groq_quota_headers(response.headers) or self.quota
        except Exception:  # noqa: BLE001 - quota evidence is optional
            pass

    def close(self) -> None:
        try:
            self.http_client.close()
        except Exception:  # noqa: BLE001 - closing never fails a stage
            pass


# The capture of the Groq client being built / called in this context. A
# stage builds one client per attempt and calls it at once on the same
# thread, so the adapters keep returning the plain `with_structured_output`
# runnable and the route collects the numbers afterwards.
_GROQ_CAPTURE: ContextVar[GroqQuotaCapture | None] = ContextVar("groq_quota_capture", default=None)


def open_groq_quota_capture() -> GroqQuotaCapture:
    """A capture for the Groq client about to be built: pass its
    `http_client` to `ChatGroq`."""
    take_groq_quota()  # a capture left by a client that was never called
    capture = GroqQuotaCapture()
    _GROQ_CAPTURE.set(capture)
    return capture


def take_groq_quota() -> GroqQuotaSnapshot | None:
    """The rate-limit numbers of the Groq request just made in this
    context (None when there are none), closing its HTTP client."""
    capture = _GROQ_CAPTURE.get()
    if capture is None:
        return None
    _GROQ_CAPTURE.set(None)
    capture.close()
    return capture.quota


_NOT_STRUCTURED = "Gemini did not return output matching the required structure."


class GeminiStructuredClient:
    """One Gemini request answering through `schema` (a stage's wire model)."""

    def __init__(
        self,
        schema: type[BaseModel],
        *,
        api_key: str,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: float,
        httpx_client: httpx.Client | None = None,
    ) -> None:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("The 'google-genai' package is not installed.") from exc

        self._schema = schema
        self._model = model
        self._types = types
        self._temperature = temperature
        self._max_tokens = max_tokens
        self.total_tokens: int | None = None
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                # milliseconds; never more than the stage budget has left
                timeout=max(1, int(timeout * 1000)),
                # one invocation = one network attempt
                retry_options=types.HttpRetryOptions(attempts=1),
                httpx_client=httpx_client,
            ),
        )

    def invoke(self, prompt: str) -> BaseModel:
        types = self._types
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=self._temperature,
                    max_output_tokens=self._max_tokens,
                    response_mime_type="application/json",
                    response_json_schema=self._schema.model_json_schema(),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        finally:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001 - closing never fails a stage
                pass

        usage = getattr(response, "usage_metadata", None)
        total = getattr(usage, "total_token_count", None)
        self.total_tokens = total if isinstance(total, int) and not isinstance(total, bool) else None

        try:
            text = response.text
        except Exception:  # noqa: BLE001 - no readable text part is "no structured answer"
            text = None
        if not isinstance(text, str) or not text.strip():
            raise LLMStructuredOutputError(_NOT_STRUCTURED)
        try:
            return self._schema.model_validate_json(text)
        except ValidationError:
            # Deliberately not chained: the validation error quotes model output.
            raise LLMStructuredOutputError(_NOT_STRUCTURED) from None
