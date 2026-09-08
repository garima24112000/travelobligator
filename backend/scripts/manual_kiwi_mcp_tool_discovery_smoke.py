#!/usr/bin/env python3
"""MANUAL-ONLY dev smoke test -- Step 178B.

This script is not part of the application runtime and is never imported by
`app.main`, any service, or the automated test suite. It exists purely so a
developer can, by hand, confirm that `KiwiMcpClient` (Step 178B) can
actually connect to Kiwi's hosted MCP server and list its tools.

WARNING: running this script with the required env vars set makes a real
network call to the configured Kiwi MCP endpoint (`https://mcp.kiwi.com`
by default). It is never invoked by pytest, by `python -m compileall`, by
CI, or by normal `uvicorn`/app startup -- it only runs when a human
explicitly executes this file.

This script performs tool *discovery only* -- it calls `list_tools`, never
a search tool, and never constructs a `FlightOffer` or any other flight
fact. It only prints tool names/descriptions/input schemas (server
capability metadata), never flight data, since none is ever requested.

This is a backend-runtime check, distinct from Claude Code's own local
MCP setup (`claude mcp add --transport http kiwi-com-flight-search
https://mcp.kiwi.com`), which configures the Claude Code CLI's own MCP
client for interactive coding sessions and has no bearing on whether this
backend can connect on its own.

Required environment variables (both, or this script exits without
calling anything):
    FLIGHT_PROVIDER=kiwi_mcp
    KIWI_MCP_ENABLED=true

Optional:
    KIWI_MCP_ENDPOINT   (defaults to Settings.kiwi_mcp_endpoint, https://mcp.kiwi.com)
    KIWI_MCP_TIMEOUT_SECONDS   (defaults to Settings.kiwi_mcp_timeout_seconds, 10.0)

Run from the repo root:
    FLIGHT_PROVIDER=kiwi_mcp KIWI_MCP_ENABLED=true \\
    python backend/scripts/manual_kiwi_mcp_tool_discovery_smoke.py

Output safety: this script only ever prints tool names, descriptions, and
input schemas returned by the MCP server's own `list_tools` response
(server capability metadata, not flight data), plus a short PASS/FAIL
summary. It never prints a raw exception, and it never writes any file.
"""

from __future__ import annotations

import os
import sys


def _exit_gracefully(message: str) -> None:
    print(f"[manual-smoke] {message}")
    print("[manual-smoke] Exiting without calling the Kiwi MCP endpoint.")
    sys.exit(0)


def _check_guardrails() -> bool:
    """Reads only the two required env vars and returns True if both
    guards pass, or False if this script should exit gracefully having
    already printed why."""
    flight_provider = os.environ.get("FLIGHT_PROVIDER")
    if flight_provider != "kiwi_mcp":
        _exit_gracefully(
            "FLIGHT_PROVIDER is not set to 'kiwi_mcp' "
            f"(got {flight_provider!r}). Set FLIGHT_PROVIDER=kiwi_mcp to run "
            "this manual smoke test."
        )
        return False

    enabled_flag = os.environ.get("KIWI_MCP_ENABLED", "")
    if enabled_flag.strip().lower() not in ("1", "true", "yes"):
        _exit_gracefully(
            "KIWI_MCP_ENABLED is not enabled "
            f"(got {enabled_flag!r}). Set KIWI_MCP_ENABLED=true to run this "
            "manual smoke test."
        )
        return False

    return True


def _run_smoke_test() -> bool:
    """Calls `KiwiMcpClient.discover_tools()` directly against the
    configured endpoint (never through the full HTTP app stack, since
    this check is scoped to the MCP connection itself, not a trip). No
    flight search of any kind is ever performed. Returns True on PASS,
    False on FAIL."""
    from app.core.config import get_settings
    from app.providers.flights.kiwi_mcp_client import KiwiMcpClient, KiwiMcpDiscoveryStatus

    settings = get_settings()
    print(f"endpoint: {settings.kiwi_mcp_endpoint}")
    print(f"timeout_seconds: {settings.kiwi_mcp_timeout_seconds}")

    client = KiwiMcpClient(
        endpoint=settings.kiwi_mcp_endpoint,
        timeout_seconds=settings.kiwi_mcp_timeout_seconds,
    )
    result = client.discover_tools()

    print(f"status: {result.status.value}")
    if result.message:
        print(f"message: {result.message}")

    if result.status != KiwiMcpDiscoveryStatus.SUCCESS:
        print("RESULT: FAIL")
        return False

    print(f"tool_count: {len(result.tools)}")
    for tool in result.tools:
        print(f"  - name: {tool.name}")
        if tool.description:
            print(f"    description: {tool.description}")
        if tool.input_schema:
            print(f"    input_schema: {tool.input_schema}")

    has_tools = len(result.tools) > 0
    print(f"  [{'ok' if has_tools else 'FAIL'}] at least one tool discovered")
    print(f"RESULT: {'PASS' if has_tools else 'FAIL'}")
    return has_tools


def main() -> int:
    if not _check_guardrails():
        return 0

    try:
        passed = _run_smoke_test()
    except Exception:
        # Never let a raw exception (which could echo connection/auth
        # details) reach stdout -- only a generic failure line is printed.
        print("[manual-smoke] Smoke test raised an unexpected exception.")
        print("RESULT: FAIL")
        return 1

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
