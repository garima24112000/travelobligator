from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.providers.flights.kiwi_mcp_client import (
    KiwiMcpClient,
    KiwiMcpDiscoveryStatus,
    KiwiMcpToolCallStatus,
    McpToolInfo,
)

# Step 178B: KiwiMcpClient tests. Every test injects a fake async
# client/session via `client_factory` -- never a real network call, never
# an import of the real `mcp` package (this whole suite passes even if
# `mcp` is not installed, since the fake factory always short-circuits
# `_default_client_factory`).


@dataclass
class _FakeTool:
    name: str
    description: str | None = None
    input_schema: dict[str, Any] | None = None


@dataclass
class _FakeListToolsResult:
    tools: list[_FakeTool] = field(default_factory=list)


@dataclass
class _FakeCallToolResult:
    structured_content: Any = None
    is_error: bool = False


class _FakeMcpClient:
    """Fake stand-in for `mcp.Client` -- an async context manager exposing
    only the methods `KiwiMcpClient` actually calls, `list_tools` and
    `call_tool`."""

    def __init__(
        self,
        tools: list[_FakeTool] | None = None,
        *,
        raise_on_enter: bool = False,
        call_tool_result: _FakeCallToolResult | None = None,
        raise_on_call_tool: bool = False,
    ) -> None:
        self._tools = tools or []
        self._raise_on_enter = raise_on_enter
        self._call_tool_result = call_tool_result
        self._raise_on_call_tool = raise_on_call_tool
        self.entered = False
        self.call_tool_calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> "_FakeMcpClient":
        if self._raise_on_enter:
            raise RuntimeError("simulated MCP connection failure")
        self.entered = True
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def list_tools(self) -> _FakeListToolsResult:
        return _FakeListToolsResult(tools=self._tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> _FakeCallToolResult:
        self.call_tool_calls.append((name, arguments))
        if self._raise_on_call_tool:
            raise RuntimeError("simulated call_tool failure")
        return self._call_tool_result or _FakeCallToolResult()


class _RaisingListToolsClient(_FakeMcpClient):
    async def list_tools(self) -> _FakeListToolsResult:
        raise RuntimeError("simulated list_tools failure")


# ---------------------------------------------------------------------------
# Discovery success maps real tool metadata verbatim into McpToolInfo.
# ---------------------------------------------------------------------------


def test_discover_tools_success_maps_tool_metadata() -> None:
    fake_client = _FakeMcpClient(
        tools=[
            _FakeTool(name="search-flight", description="Search for flights", input_schema={"type": "object"}),
            _FakeTool(name="feedback-to-devs", description="Send feedback"),
        ]
    )
    client = KiwiMcpClient(
        endpoint="https://mcp.kiwi.com", timeout_seconds=10.0, client_factory=lambda: fake_client
    )

    result = client.discover_tools()

    assert result.status == KiwiMcpDiscoveryStatus.SUCCESS
    assert result.message is None
    assert len(result.tools) == 2
    assert result.tools[0] == McpToolInfo(
        name="search-flight", description="Search for flights", input_schema={"type": "object"}
    )
    assert result.tools[1].name == "feedback-to-devs"
    assert fake_client.entered is True


def test_discover_tools_success_with_no_tools() -> None:
    fake_client = _FakeMcpClient(tools=[])
    client = KiwiMcpClient(
        endpoint="https://mcp.kiwi.com", timeout_seconds=10.0, client_factory=lambda: fake_client
    )

    result = client.discover_tools()

    assert result.status == KiwiMcpDiscoveryStatus.SUCCESS
    assert result.tools == []


# ---------------------------------------------------------------------------
# Discovery failure (connection failure or list_tools failure) never
# raises, and never returns a leftover/fabricated tool list.
# ---------------------------------------------------------------------------


def test_discover_tools_returns_failed_on_connection_error() -> None:
    fake_client = _FakeMcpClient(raise_on_enter=True)
    client = KiwiMcpClient(
        endpoint="https://mcp.kiwi.com", timeout_seconds=10.0, client_factory=lambda: fake_client
    )

    result = client.discover_tools()

    assert result.status == KiwiMcpDiscoveryStatus.FAILED
    assert result.tools == []
    assert result.message is not None


def test_discover_tools_returns_failed_on_list_tools_error() -> None:
    fake_client = _RaisingListToolsClient()
    client = KiwiMcpClient(
        endpoint="https://mcp.kiwi.com", timeout_seconds=10.0, client_factory=lambda: fake_client
    )

    result = client.discover_tools()

    assert result.status == KiwiMcpDiscoveryStatus.FAILED
    assert result.tools == []


def test_discover_tools_failure_message_never_contains_raw_exception_text() -> None:
    """The failure message must be a generic, safe string -- never the raw
    exception text, which could echo connection/auth details."""
    fake_client = _FakeMcpClient(raise_on_enter=True)
    client = KiwiMcpClient(
        endpoint="https://mcp.kiwi.com", timeout_seconds=10.0, client_factory=lambda: fake_client
    )

    result = client.discover_tools()

    assert "simulated MCP connection failure" not in (result.message or "")


def test_discover_tools_never_raises_when_factory_itself_raises() -> None:
    def _raising_factory() -> _FakeMcpClient:
        raise ValueError("factory construction failed")

    client = KiwiMcpClient(
        endpoint="https://mcp.kiwi.com", timeout_seconds=10.0, client_factory=_raising_factory
    )

    result = client.discover_tools()

    assert result.status == KiwiMcpDiscoveryStatus.FAILED
    assert result.tools == []


# ---------------------------------------------------------------------------
# No network call / no real mcp package touched when a fake factory is
# injected -- the real `_default_client_factory` (which lazily imports
# `mcp`) is never called.
# ---------------------------------------------------------------------------


def test_default_client_factory_is_never_used_when_factory_injected() -> None:
    fake_client = _FakeMcpClient(tools=[_FakeTool(name="search-flight")])
    client = KiwiMcpClient(
        endpoint="https://mcp.kiwi.com", timeout_seconds=10.0, client_factory=lambda: fake_client
    )

    def _fail_if_called() -> None:
        raise AssertionError("KiwiMcpClient must not use its real default client factory here")

    client._default_client_factory = _fail_if_called  # type: ignore[method-assign]

    result = client.discover_tools()

    assert result.status == KiwiMcpDiscoveryStatus.SUCCESS


# ---------------------------------------------------------------------------
# KiwiMcpToolDiscoveryResult's own model defaults, tested directly (not
# just as a side effect of KiwiMcpClient's behavior above).
# ---------------------------------------------------------------------------


def test_tool_discovery_result_defaults() -> None:
    from app.providers.flights.kiwi_mcp_client import KiwiMcpToolDiscoveryResult

    result = KiwiMcpToolDiscoveryResult(status=KiwiMcpDiscoveryStatus.FAILED)
    assert result.tools == []
    assert result.message is None


# ---------------------------------------------------------------------------
# Step 178C: KiwiMcpClient.call_tool tests. Every test injects a fake
# client -- never a real network call.
# ---------------------------------------------------------------------------


def test_call_tool_calls_requested_tool_with_exact_arguments() -> None:
    """Required test 1."""
    fake_client = _FakeMcpClient(
        call_tool_result=_FakeCallToolResult(structured_content={"itineraries": []})
    )
    client = KiwiMcpClient(
        endpoint="https://mcp.kiwi.com", timeout_seconds=10.0, client_factory=lambda: fake_client
    )

    arguments = {"flyFrom": "London", "flyTo": "Paris", "departureDate": "15/12/2026"}
    result = client.call_tool("search-flight", arguments)

    assert result.status == KiwiMcpToolCallStatus.SUCCESS
    assert fake_client.call_tool_calls == [("search-flight", arguments)]


def test_call_tool_maps_structured_content_and_is_error() -> None:
    fake_client = _FakeMcpClient(
        call_tool_result=_FakeCallToolResult(structured_content={"a": 1}, is_error=True)
    )
    client = KiwiMcpClient(
        endpoint="https://mcp.kiwi.com", timeout_seconds=10.0, client_factory=lambda: fake_client
    )

    result = client.call_tool("search-flight", {"flyFrom": "x"})

    assert result.status == KiwiMcpToolCallStatus.SUCCESS
    assert result.structured_content == {"a": 1}
    assert result.is_error is True


def test_call_tool_surfaces_connection_failure_safely() -> None:
    """Required test 2."""
    fake_client = _FakeMcpClient(raise_on_enter=True)
    client = KiwiMcpClient(
        endpoint="https://mcp.kiwi.com", timeout_seconds=10.0, client_factory=lambda: fake_client
    )

    result = client.call_tool("search-flight", {"flyFrom": "London"})

    assert result.status == KiwiMcpToolCallStatus.FAILED
    assert result.structured_content is None
    assert result.message is not None
    assert "simulated MCP connection failure" not in (result.message or "")


def test_call_tool_surfaces_call_failure_safely() -> None:
    fake_client = _FakeMcpClient(raise_on_call_tool=True)
    client = KiwiMcpClient(
        endpoint="https://mcp.kiwi.com", timeout_seconds=10.0, client_factory=lambda: fake_client
    )

    result = client.call_tool("search-flight", {"flyFrom": "London"})

    assert result.status == KiwiMcpToolCallStatus.FAILED
    assert result.structured_content is None
    assert "simulated call_tool failure" not in (result.message or "")


def test_call_tool_never_raises_when_factory_itself_raises() -> None:
    def _raising_factory() -> _FakeMcpClient:
        raise ValueError("factory construction failed")

    client = KiwiMcpClient(
        endpoint="https://mcp.kiwi.com", timeout_seconds=10.0, client_factory=_raising_factory
    )

    result = client.call_tool("search-flight", {"flyFrom": "London"})

    assert result.status == KiwiMcpToolCallStatus.FAILED


def test_call_tool_result_defaults() -> None:
    from app.providers.flights.kiwi_mcp_client import KiwiMcpToolCallResult

    result = KiwiMcpToolCallResult(status=KiwiMcpToolCallStatus.SUCCESS)
    assert result.structured_content is None
    assert result.is_error is False
    assert result.message is None
