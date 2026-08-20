#!/usr/bin/env python3
"""MANUAL-ONLY dev smoke test -- Step 164D.1.

This script is not part of the application runtime and is never imported by
`app.main`, any service, or the automated test suite. It exists purely so a
developer can, by hand, confirm that the three provider-cache-wired
adapters -- Open-Meteo (Step 164B), Nager.Date (Step 164C), and Frankfurter
(Step 164D) -- still work against their real public APIs, and that the
`ProviderCacheStore` foundation (Step 164A) actually populates and is read
back correctly for each of them.

WARNING: running this script with the required env var set makes real
network calls to Open-Meteo, Nager.Date, and Frankfurter -- three free,
keyless public APIs (no API key is required or read by this script). It is
never invoked by pytest, by `python -m compileall`, by CI, or by normal
`uvicorn`/app startup -- it only runs when a human explicitly executes this
file.

No Groq, Anthropic, Kiwi/MCP, or scraping call is made anywhere in this
script -- only the three providers above.

Required environment variable (or this script exits without calling
anything):
    RUN_LIVE_PROVIDER_CACHE_SMOKE=true

Run from the repo root:
    RUN_LIVE_PROVIDER_CACHE_SMOKE=true \\
    python backend/scripts/manual_provider_cache_smoke.py

See docs/21_manual_provider_cache_smoke.md for what a PASS/FAIL result does
and does not mean.

Output safety: this script only ever prints a short per-provider summary
(provider name, live_path_ok, cache_path_ok, status, cache_row_count) plus a
final PASS/FAIL line. It never prints a full weather/holiday/currency
payload, a raw query, or any secret, and it never writes to the app's real
provider cache (`Settings.provider_cache_path`) or trip storage -- it always
uses its own temporary, clearly-named `ProviderCacheStore` file that is
deleted when the script exits.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# Running this file directly (`python backend/scripts/manual_provider_cache_smoke.py`
# from the repo root) puts this script's own directory on `sys.path[0]`, not
# `backend/`, so `app.*` would not otherwise be importable without the
# developer manually setting `PYTHONPATH=backend` first. This insertion is
# pure local path bookkeeping -- it makes no network call and reads no
# secret -- so it is safe to run unconditionally at import time, before the
# env-var guard below.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

_REQUIRED_ENV_VAR = "RUN_LIVE_PROVIDER_CACHE_SMOKE"

# Known, fixed real-world inputs -- never resolved via a geocoding/places
# lookup, so this script depends on nothing beyond the three providers under
# test. Lisbon, Portugal is used consistently across all three so a single
# temporary cache file/instance exercises Open-Meteo, Nager.Date, and
# Frankfurter with one coherent destination.
_KNOWN_LATITUDE = 38.7223
_KNOWN_LONGITUDE = -9.1393
_KNOWN_DESTINATION = "Lisbon, Portugal"
_KNOWN_BASE_CURRENCY = "USD"

_EXPECTED_SOURCES = ("open_meteo", "nager_date", "frankfurter")

# Checked against stored query_hash/payload_json/metadata_json text as a
# defense-in-depth assertion -- none of these three providers require an API
# key, so none of these should ever legitimately appear in a cache row.
_FORBIDDEN_SECRET_SUBSTRINGS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "authorization",
    "bearer",
    "password",
)


def _exit_gracefully(message: str) -> None:
    print(f"[manual-smoke] {message}")
    print("[manual-smoke] Exiting without calling any live provider.")
    sys.exit(0)


def _check_guardrail() -> bool:
    """Reads only the one required env var and returns True if it's set to
    a truthy value, or False if this script should exit gracefully having
    already printed why. This function never imports `app.*`, never opens a
    network connection, and never touches the filesystem beyond reading an
    env var.
    """
    flag = os.environ.get(_REQUIRED_ENV_VAR, "")
    if flag.strip().lower() not in ("1", "true", "yes"):
        _exit_gracefully(
            f"{_REQUIRED_ENV_VAR} is not set to a truthy value (got {flag!r}). "
            f"Set {_REQUIRED_ENV_VAR}=true to run this manual smoke test. "
            "This script is manual-only: it is never run by pytest or CI."
        )
        return False
    return True


def _cache_row_count(db_path: Path, source: str) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM provider_cache WHERE source = ?", (source,)
        ).fetchone()
    return int(row[0]) if row else 0


def _query_hashes_leak_raw_destination_text(db_path: Path, source: str) -> bool:
    """True if the stored `query_hash` for `source` contains the raw
    destination string -- which should never happen, since `query_hash` is
    always an opaque SHA-256 hex digest (`make_query_hash`), never the raw
    query text itself. This only inspects the hash column, never
    `payload_json` -- a legitimate holiday name or exchange-rate payload may
    honestly reference "Portugal"/"Lisbon" (e.g. a real Portuguese holiday
    name), and that is not a leak worth flagging.
    """
    needles = ("lisbon", "portugal")
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT query_hash FROM provider_cache WHERE source = ?", (source,)
        ).fetchall()
    combined = " ".join(str(row[0]) for row in rows).lower()
    return any(needle in combined for needle in needles)


def _cache_rows_contain_secret_markers(db_path: Path, source: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT query_hash, payload_json, metadata_json FROM provider_cache WHERE source = ?",
            (source,),
        ).fetchall()
    combined = " ".join(" ".join(str(value) for value in row) for row in rows).lower()
    return any(needle in combined for needle in _FORBIDDEN_SECRET_SUBSTRINGS)


def _run_smoke_test(db_path: Path) -> bool:
    """Calls each of the three cache-wired adapters twice with identical,
    known inputs, sharing one `ProviderCacheStore` pointed at `db_path` (a
    temporary file, never the app's real provider cache). The first call is
    expected to populate the cache from a live provider request; the second
    is expected to be served from the cache (`data_status="cached"`).
    Returns True on PASS, False on FAIL. Never asserts an exact weather
    value, holiday name, or exchange rate -- only structure and cache
    behavior.
    """
    from app.models.common import DataStatus, GeoPoint, ProviderStatus
    from app.providers.currency.frankfurter_adapter import FrankfurterCurrencyAdapter
    from app.providers.holidays.nager_date_adapter import NagerDateHolidaysAdapter
    from app.providers.weather.open_meteo_adapter import OpenMeteoWeatherAdapter
    from app.storage.provider_cache_store import ProviderCacheStore

    store = ProviderCacheStore(db_path)
    checks: list[tuple[str, bool]] = []

    def _report(source: str, first_response: Any, second_response: Any) -> None:
        live_path_ok = first_response.status == ProviderStatus.SUCCESS
        cache_path_ok = live_path_ok and second_response.data_status == DataStatus.CACHED
        row_count = _cache_row_count(db_path, source)

        print(
            f"provider={source} live_path_ok={live_path_ok} "
            f"cache_path_ok={cache_path_ok} status={first_response.status.value} "
            f"cache_row_count={row_count}"
        )

        checks.append((f"{source} live path ok", live_path_ok))
        checks.append((f"{source} cache path ok", cache_path_ok))
        checks.append((f"{source} has at least one cache row", row_count >= 1))

    today = date.today()
    weather_dates = {
        "start_date": (today + timedelta(days=1)).isoformat(),
        "end_date": (today + timedelta(days=3)).isoformat(),
    }
    holiday_dates = {
        "start_date": f"{today.year}-01-01",
        "end_date": f"{today.year}-12-31",
    }

    # --- Open-Meteo ---
    weather_adapter = OpenMeteoWeatherAdapter(cache_store=store)
    coordinates = GeoPoint(lat=_KNOWN_LATITUDE, lng=_KNOWN_LONGITUDE)
    weather_first = weather_adapter.get_weather_forecast(
        _KNOWN_DESTINATION, weather_dates, coordinates=coordinates
    )
    weather_second = weather_adapter.get_weather_forecast(
        _KNOWN_DESTINATION, weather_dates, coordinates=coordinates
    )
    _report("open_meteo", weather_first, weather_second)

    # --- Nager.Date ---
    holiday_adapter = NagerDateHolidaysAdapter(cache_store=store)
    holiday_first = holiday_adapter.get_public_holidays(_KNOWN_DESTINATION, holiday_dates)
    holiday_second = holiday_adapter.get_public_holidays(_KNOWN_DESTINATION, holiday_dates)
    _report("nager_date", holiday_first, holiday_second)

    # --- Frankfurter ---
    currency_adapter = FrankfurterCurrencyAdapter(cache_store=store)
    currency_first = currency_adapter.get_exchange_rate(_KNOWN_BASE_CURRENCY, _KNOWN_DESTINATION)
    currency_second = currency_adapter.get_exchange_rate(_KNOWN_BASE_CURRENCY, _KNOWN_DESTINATION)
    _report("frankfurter", currency_first, currency_second)

    no_secrets = not any(
        _cache_rows_contain_secret_markers(db_path, source) for source in _EXPECTED_SOURCES
    )
    checks.append(("no secret markers stored in any cache row", no_secrets))

    no_raw_destination_in_hash = not any(
        _query_hashes_leak_raw_destination_text(db_path, source) for source in _EXPECTED_SOURCES
    )
    checks.append(("no raw destination text stored in any query_hash", no_raw_destination_in_hash))

    return _print_final(checks)


def _print_final(checks: list[tuple[str, bool]]) -> bool:
    all_passed = all(passed for _, passed in checks)
    for label, passed in checks:
        print(f"  [{'ok' if passed else 'FAIL'}] {label}")
    print(f"RESULT: {'PASS' if all_passed else 'FAIL'}")
    return all_passed


def main() -> int:
    if not _check_guardrail():
        return 0

    temp_dir = Path(tempfile.mkdtemp(prefix="travelobligator_manual_provider_cache_smoke_"))
    db_path = temp_dir / "manual_provider_cache_smoke.sqlite3"
    print(f"[manual-smoke] Using temporary cache path: {db_path}")
    print(
        "[manual-smoke] This is a throwaway file, not the app's real provider "
        "cache and not production trip storage. It is deleted when this "
        "script exits."
    )

    try:
        passed = _run_smoke_test(db_path)
    except Exception:
        # Never let a raw exception (which could echo request/response
        # details) reach stdout -- only a generic failure line is printed.
        print("[manual-smoke] Smoke test raised an unexpected exception.")
        print("RESULT: FAIL")
        return 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
