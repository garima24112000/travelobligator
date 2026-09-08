from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any, AsyncContextManager, Callable

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


_DISCOVERY_FAILED_MESSAGE = "Could not reach the Kiwi MCP endpoint or list its tools."


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
    ) -> None:
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory

    def _default_client_factory(self) -> AsyncContextManager[Any]:
        from mcp import Client  # lazy import -- see module docstring above.

        return Client(self._endpoint, read_timeout_seconds=self._timeout_seconds)

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
        try:
            return asyncio.run(self._discover_tools_async())
        except Exception:
            logger.warning(
                "Kiwi MCP tool discovery failed unexpectedly.", exc_info=True
            )
            return KiwiMcpToolDiscoveryResult(
                status=KiwiMcpDiscoveryStatus.FAILED,
                tools=[],
                message=_DISCOVERY_FAILED_MESSAGE,
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
        try:
            return asyncio.run(self._call_tool_async(tool_name, arguments))
        except Exception:
            logger.warning(
                "Kiwi MCP tool call to '%s' failed unexpectedly.", tool_name, exc_info=True
            )
            return KiwiMcpToolCallResult(
                status=KiwiMcpToolCallStatus.FAILED,
                structured_content=None,
                is_error=True,
                message=_tool_call_failed_message(tool_name),
            )
