from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any, AsyncContextManager, AsyncIterator, Callable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Kiwi MCP client foundation (Step 178B, docs/12_provider_architecture.md,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
#
# This module is scoped to MCP *tool discovery* only -- it connects to a
# configured MCP endpoint, performs the standard MCP `initialize`/
# `list_tools` handshake, and returns a small, normalized result
# describing which tools the server advertises. It never calls a tool
# (no flight search, no other tool of any kind), never calls an LLM, and
# never constructs a `FlightOffer` or any other travel fact -- discovered
# tool names/descriptions/input schemas are inert metadata about the
# server's own capabilities, not flight data.
#
# Uses the official Model Context Protocol Python SDK (`mcp` on PyPI,
# https://modelcontextprotocol.io) via its high-level `mcp.Client`, rather
# than hand-rolling MCP's JSON-RPC/session/transport framing over raw
# httpx: MCP's Streamable HTTP transport involves session negotiation,
# redirect-origin checks, and (for long-lived servers) an SSE stream, all
# of which the official SDK already implements and tests -- reimplementing
# that by hand here would be strictly riskier and harder to verify than
# the small, documented `mcp` dependency this step adds (see
# backend/requirements.txt). The `mcp` package is imported lazily inside
# `_default_client_factory`, mirroring this codebase's existing
# anthropic/groq adapters -- so this module (and the app/test suite as a
# whole) still work even if `mcp` is not installed, as long as no code
# path actually tries to connect (every unit test injects a fake
# `client_factory` instead).
#
# Step 178C extends this module with `call_tool`, used to actually invoke
# one already-discovered tool (e.g. Kiwi's `search-flight`). `call_tool`
# itself has no idea what a "flight" is -- it is pure MCP transport: it
# sends a `tools/call` request with the exact arguments it was given and
# hands back the server's response's *structured* content untouched, for
# `app.providers.flights.kiwi_mcp_parser` to interpret. It never calls an
# LLM and never reads/parses the response's free-form `content` blocks
# (the human/LLM-readable text a tool may also return) -- only
# `structured_content`, the machine-readable JSON payload, is ever passed
# through.


class KiwiMcpDiscoveryStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"


class McpToolInfo(BaseModel):
    """Inert metadata about one tool an MCP server advertises -- a tool's
    name/description/input schema, never a tool's *output* or any data a
    tool call might return. Every field here is copied verbatim from the
    server's own `list_tools` response; nothing is guessed or derived.
    """

    name: str
    description: str | None = None
    input_schema: dict[str, Any] | None = None


class KiwiMcpToolDiscoveryResult(BaseModel):
    """Normalized result of one MCP tool-discovery attempt.

    `tools` may only be non-empty when `status == success` -- a `failed`
    discovery must never carry a leftover/fabricated tool list.
    """

    status: KiwiMcpDiscoveryStatus
    tools: list[McpToolInfo] = Field(default_factory=list)
    message: str | None = None
    # M2 Phase 2: why a FAILED discovery failed (`timeout` / `connection` /
    # `request_cap`).
    failure_kind: str | None = None
    # M2 Phase 2: HTTP requests this exchange put on the wire (None when the
    # transport was not observable, i.e. an injected test client).
    http_requests: int | None = None


class KiwiMcpToolCallStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"


class KiwiMcpToolCallResult(BaseModel):
    """Normalized, transport-level result of one MCP `tools/call` attempt.

    `status` reflects only whether a response was actually received from
    the server -- it says nothing about whether the *tool itself*
    succeeded; that is `is_error`'s job (an MCP-protocol-level flag the
    tool sets on its own result, e.g. for invalid arguments), and
    interpreting the tool's actual domain-level outcome (a real flight
    list vs. no results vs. a malformed payload) is deliberately left to
    `app.providers.flights.kiwi_mcp_parser` -- this class carries the
    server's response through unopinionated about its meaning.

    `structured_content` is whatever JSON-shaped value the server
    returned as the tool's machine-readable result (never the free-form
    `content` text blocks a tool may also return, which are meant for an
    LLM/human to read, not for this app to treat as data) -- typed loosely
    (`Any`) because validating its actual shape is the parser's job, not
    this transport-level wrapper's.
    """

    status: KiwiMcpToolCallStatus
    structured_content: Any = None
    is_error: bool = False
    message: str | None = None
    # M2 Phase 2: why a FAILED call failed (`timeout` / `connection` /
    # `request_cap`).
    failure_kind: str | None = None
    # M2 Phase 2: HTTP requests this exchange put on the wire (see above).
    http_requests: int | None = None


# M2 Phase 2: fixed labels for why a Kiwi MCP exchange failed.
FAILURE_TIMEOUT = "timeout"
FAILURE_CONNECTION = "connection"
FAILURE_REQUEST_CAP = "request_cap"
_REQUEST_CAP_MESSAGE = "The Kiwi MCP exchange needed more HTTP requests than its per-session limit allows."


class RequestCapReached(Exception):
    """Raised by `HttpRequestMeter` before a request beyond the cap is sent."""


class HttpRequestMeter:
    """Counts the HTTP requests ONE MCP session puts on the wire and refuses
    the request that would exceed `cap` before it is sent (M2 Phase 2).

    The MCP SDK talks over `httpx2` (its own HTTP package, separate from the
    `httpx` the rest of the app uses), so a guard on `httpx` never sees this
    traffic; the meter sits in the transport the client itself builds, which
    every MCP request (initialize, notifications, the GET stream, tool calls,
    the closing DELETE) goes through. Status codes are kept as numbers only.
    """

    def __init__(self, cap: int | None) -> None:
        self.cap = cap if cap is not None and cap > 0 else None
        self.sent = 0
        self.refused = 0
        self.statuses: Counter = Counter()

    def admit(self) -> None:
        if self.cap is not None and self.sent >= self.cap:
            self.refused += 1
            raise RequestCapReached
        self.sent += 1

    def observe(self, status_code: int) -> None:
        self.statuses[int(status_code)] += 1


def counting_transport(inner: Any, meter: HttpRequestMeter) -> Any:
    """An `httpx2.AsyncBaseTransport` that passes every request through
    `meter` and then to `inner` (the real network transport)."""
    import httpx2  # lazy, like `mcp` -- only a real connection needs it

    class _CountingTransport(httpx2.AsyncBaseTransport):
        async def __aenter__(self) -> Any:
            await inner.__aenter__()
            return self

        async def handle_async_request(self, request: Any) -> Any:
            meter.admit()
            response = await inner.handle_async_request(request)
            meter.observe(response.status_code)
            return response

        async def aclose(self) -> None:
            await inner.aclose()

    return _CountingTransport()

_DISCOVERY_FAILED_MESSAGE = "Could not reach the Kiwi MCP endpoint or list its tools."
_TIMED_OUT_MESSAGE = "The Kiwi MCP exchange did not finish within its total time limit."


def _tool_call_failed_message(tool_name: str) -> str:
    return f"Could not call the Kiwi MCP '{tool_name}' tool."


class KiwiMcpClient:
    """Thin wrapper around the official MCP SDK's high-level `mcp.Client`,
    scoped to tool discovery only (Step 178B).

    `discover_tools` is a synchronous method that bridges to the MCP
    SDK's async API via `asyncio.run` -- safe here because every caller in
    this codebase (`KiwiMcpFlightProvider.search_flights`, itself called
    by `FlightInventoryService.build_report`) is invoked synchronously
    from a thread with no already-running event loop: FastAPI's route
    handlers in this app are plain `def` (Starlette runs them in a worker
    thread pool, not the request-handling event loop), and this codebase's
    LangGraph engine calls `graph.invoke()`, never `ainvoke()`. A fresh
    event loop is created and torn down for each call, matching
    `asyncio.run`'s own documented behavior.

    `client_factory`, if given, replaces the real MCP connection entirely
    -- every unit test injects a fake async context manager here instead
    of contacting a real server, so no test requires network access.
    """

    def __init__(
        self,
        endpoint: str,
        timeout_seconds: float,
        *,
        client_factory: Callable[[], AsyncContextManager[Any]] | None = None,
        total_timeout_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_http_requests_per_session: int | None = None,
        http_transport_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory
        # M2 Phase 2: every session's HTTP requests are counted and capped in
        # the client's own transport. `http_transport_factory` replaces only
        # the innermost network transport (tests: an in-process server).
        self._max_http_requests = max_http_requests_per_session
        self._http_transport_factory = http_transport_factory
        self._meter: HttpRequestMeter | None = None
        self.http_requests_total = 0
        self.http_statuses: Counter = Counter()
        # M2 Phase 2: ONE wall-clock limit for everything this client does
        # (connect, discovery, the search call), counted from its first use.
        # `timeout_seconds` alone only bounds each read, so a slow connect or
        # a stream of slow reads could hold a generation far longer.
        self._total_timeout = total_timeout_seconds if total_timeout_seconds and total_timeout_seconds > 0 else None
        self._clock = clock
        self._deadline: float | None = None

    def _remaining(self) -> float | None:
        if self._total_timeout is None:
            return None
        if self._deadline is None:
            self._deadline = self._clock() + self._total_timeout
        return max(0.0, self._deadline - self._clock())

    async def _bounded(self, coroutine: Any) -> Any:
        """Runs `coroutine` within what is left of the total limit. A timeout
        cancels it, which closes the MCP session (`async with`) on the way out."""
        remaining = self._remaining()
        if remaining is None:
            return await coroutine
        if remaining <= 0:
            coroutine.close()
            raise asyncio.TimeoutError
        return await asyncio.wait_for(coroutine, timeout=remaining)

    @asynccontextmanager
    async def _default_client_factory(self) -> AsyncIterator[Any]:
        """A real MCP session over an `httpx2` client whose transport counts
        and caps every request (see `HttpRequestMeter`). Timeouts are the MCP
        SDK's own defaults; the total limit (`_bounded`) still applies."""
        import httpx2  # lazy imports -- see module docstring above.
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

        meter = self._meter if self._meter is not None else HttpRequestMeter(self._max_http_requests)
        inner = self._http_transport_factory() if self._http_transport_factory else httpx2.AsyncHTTPTransport()
        http_client = httpx2.AsyncClient(
            transport=counting_transport(inner, meter),
            timeout=httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT),
        )
        async with http_client:
            async with Client(
                streamable_http_client(self._endpoint, http_client=http_client),
                read_timeout_seconds=self._timeout_seconds,
            ) as client:
                yield client

    def _start_session(self) -> HttpRequestMeter | None:
        """A fresh meter for one real session; None for an injected client
        (its transport is not observable, so nothing is claimed about it)."""
        self._meter = None if self._client_factory is not None else HttpRequestMeter(self._max_http_requests)
        return self._meter

    def _end_session(self, meter: HttpRequestMeter | None) -> int | None:
        if meter is None:
            return None
        self.http_requests_total += meter.sent
        self.http_statuses.update(meter.statuses)
        return meter.sent

    async def _discover_tools_async(self) -> KiwiMcpToolDiscoveryResult:
        factory = self._client_factory or self._default_client_factory
        async with factory() as client:
            list_result = await client.list_tools()

        tools = [
            McpToolInfo(
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
            )
            for tool in list_result.tools
        ]
        return KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.SUCCESS,
            tools=tools,
            message=None,
        )

    def discover_tools(self) -> KiwiMcpToolDiscoveryResult:
        """Connects to the configured MCP endpoint, performs `initialize`/
        `list_tools`, and returns a normalized `KiwiMcpToolDiscoveryResult`.

        Never calls a tool. Never raises -- any connection failure,
        timeout, protocol error, or unexpected exception from the
        underlying client is caught and reported as an honest `failed`
        result with a generic, safe message (never raw exception text,
        which could echo connection/auth details).
        """
        meter = self._start_session()
        try:
            result = asyncio.run(self._bounded(self._discover_tools_async()))
            failed = None
        except asyncio.TimeoutError:
            failed = (FAILURE_TIMEOUT, _TIMED_OUT_MESSAGE)
        except Exception:
            failed = (FAILURE_CONNECTION, _DISCOVERY_FAILED_MESSAGE)
        sent = self._end_session(meter)
        if failed is None:
            return result.model_copy(update={"http_requests": sent})
        if meter is not None and meter.refused:
            # The cap, not the network, ended the exchange.
            failed = (FAILURE_REQUEST_CAP, _REQUEST_CAP_MESSAGE)
        logger.warning("Kiwi MCP tool discovery failed (kind=%s).", failed[0])
        return KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.FAILED,
            tools=[],
            message=failed[1],
            failure_kind=failed[0],
            http_requests=sent,
        )

    async def _call_tool_async(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> KiwiMcpToolCallResult:
        factory = self._client_factory or self._default_client_factory
        async with factory() as client:
            result = await client.call_tool(tool_name, arguments)

        return KiwiMcpToolCallResult(
            status=KiwiMcpToolCallStatus.SUCCESS,
            structured_content=getattr(result, "structured_content", None),
            is_error=bool(getattr(result, "is_error", False)),
            message=None,
        )

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> KiwiMcpToolCallResult:
        """Calls exactly one already-discovered tool by name, with exactly
        the arguments given -- never a different tool, never additional/
        substituted arguments. Applies the same `timeout_seconds`/
        synchronous-bridging contract as `discover_tools` (see that
        method's docstring).

        Never raises -- any connection failure, timeout, protocol error,
        or unexpected exception is caught and reported as an honest
        `failed` `KiwiMcpToolCallResult` with a generic, safe message
        (never raw exception text). This never calls an LLM and never
        interprets the response itself; that is
        `app.providers.flights.kiwi_mcp_parser`'s job.
        """
        meter = self._start_session()
        try:
            result = asyncio.run(self._bounded(self._call_tool_async(tool_name, arguments)))
            failed = None
        except asyncio.TimeoutError:
            failed = (FAILURE_TIMEOUT, _TIMED_OUT_MESSAGE)
        except Exception:
            failed = (FAILURE_CONNECTION, _tool_call_failed_message(tool_name))
        sent = self._end_session(meter)
        if failed is None:
            return result.model_copy(update={"http_requests": sent})
        if meter is not None and meter.refused:
            failed = (FAILURE_REQUEST_CAP, _REQUEST_CAP_MESSAGE)
        logger.warning("Kiwi MCP tool call to '%s' failed (kind=%s).", tool_name, failed[0])
        return KiwiMcpToolCallResult(
            status=KiwiMcpToolCallStatus.FAILED,
            structured_content=None,
            is_error=True,
            message=failed[1],
            failure_kind=failed[0],
            http_requests=sent,
        )
