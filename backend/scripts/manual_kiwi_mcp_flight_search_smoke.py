#!/usr/bin/env python3
"""MANUAL-ONLY dev smoke test -- Step 178C.

This script is not part of the application runtime and is never imported by
`app.main`, any service, or the automated test suite. It exists purely so a
developer can, by hand, confirm that `KiwiMcpFlightProvider` (Step 178C)
can perform a real flight search against Kiwi's hosted MCP server and
parse the result into `FlightOffer`s.

WARNING: running this script with the required env vars set makes a real
network call to the configured Kiwi MCP endpoint (`https://mcp.kiwi.com`
by default), which calls Kiwi's real `search-flight` tool. It is never
invoked by pytest, by `python -m compileall`, by CI, or by normal
`uvicorn`/app startup -- it only runs when a human explicitly executes
this file.

This script only ever calls `KiwiMcpFlightProvider.search_flights` --
the exact same code path `FlightInventoryService`/`PlanningOrchestrator`
use -- with a small, fixed, safe search input. It never calls
`feedback-to-devs` or any other tool, never calls an LLM, and never
creates or saves fake data: every value it prints is read directly off
the real, parsed `FlightSearchResult`/`FlightOffer` this run actually
produced.

Required environment variables (both, or this script exits without
calling anything):
    FLIGHT_PROVIDER=kiwi_mcp
    KIWI_MCP_ENABLED=true

Optional:
    KIWI_MCP_ENDPOINT   (defaults to Settings.kiwi_mcp_endpoint, https://mcp.kiwi.com)
    KIWI_MCP_TIMEOUT_SECONDS   (defaults to Settings.kiwi_mcp_timeout_seconds, 10.0)

Run from the repo root:
    FLIGHT_PROVIDER=kiwi_mcp KIWI_MCP_ENABLED=true \\
    python backend/scripts/manual_kiwi_mcp_flight_search_smoke.py

Output safety: this script never prints a full booking URL (only its
host, or "present"/"not present"), never prints a raw exception, and
never writes any file. A search that legitimately finds nothing is
reported as PASS with "status: unavailable" -- that is Kiwi honestly
reporting no offers, not a failure of this script or the provider.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from urllib.parse import urlparse


def _exit_gracefully(message: str) -> None:
    print(f"[manual-smoke] {message}")
    print("[manual-smoke] Exiting without calling the Kiwi MCP endpoint.")
    sys.exit(0)


def _check_guardrails() -> bool:
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


def _booking_url_summary(url: str | None) -> str:
    if not url:
        return "not present"
    host = urlparse(url).netloc or "present (unparseable host)"
    return f"present (host: {host})"


def _run_smoke_test() -> bool:
    """Calls `KiwiMcpFlightProvider.search_flights` directly with a small,
    fixed, safe one-way search (a well-known city pair, ~60 days out, so
    this never depends on the current date being near any specific
    holiday/season). Returns True on PASS, False on FAIL. A legitimate
    zero-offer result (`status=unavailable`) is a PASS, not a failure."""
    from app.core.config import get_settings
    from app.models.flight import FlightSearchRequest, FlightSearchStatus
    from app.providers.flights.kiwi_mcp_adapter import KiwiMcpFlightProvider

    settings = get_settings()
    print(f"endpoint: {settings.kiwi_mcp_endpoint}")
    print(f"timeout_seconds: {settings.kiwi_mcp_timeout_seconds}")

    request = FlightSearchRequest(
        origin="London",
        destination="Paris",
        departure_date=date.today() + timedelta(days=60),
    )
    print(f"search: {request.origin} -> {request.destination} on {request.departure_date}")

    provider = KiwiMcpFlightProvider()
    result = provider.search_flights(request)

    print(f"provider: {result.provider}")
    print(f"status: {result.status.value}")
    if result.message:
        print(f"message: {result.message}")
    print(f"offer_count: {len(result.offers)}")

    if result.status == FlightSearchStatus.FAILED:
        print("RESULT: FAIL")
        return False

    if not result.offers:
        # A real, honest "Kiwi searched and found nothing" -- not a
        # failure of this script or the provider.
        print("RESULT: PASS (no offers -- reported honestly as unavailable)")
        return True

    first_offer = result.offers[0]
    print(f"first_offer_source: {first_offer.source_name}")
    print(f"first_offer_provider: {first_offer.provider}")
    print(f"first_offer_data_status: {first_offer.data_status.value}")
    print(f"price_present: {first_offer.total_price_amount is not None}")
    print(f"booking_url: {_booking_url_summary(first_offer.booking_url)}")
    print(f"outbound_segment_count: {len(first_offer.outbound_segments)}")
    print(f"return_segment_count: {len(first_offer.return_segments)}")

    print("RESULT: PASS")
    return True


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
