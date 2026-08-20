# Manual Provider-Cache Smoke Test (Step 164D.1)

## Purpose

`backend/scripts/manual_provider_cache_smoke.py` is a **manual, dev-only**
smoke test. It is not part of the application runtime: it is never imported
by `app.main`, by any backend service, or by the automated `pytest` suite,
and it never runs during `python -m compileall`, normal app startup, or CI.
It only runs when a developer explicitly executes the file by hand.

Its purpose is narrow: after Steps 164A-164D wired `ProviderCacheStore`
into `OpenMeteoWeatherAdapter`, `NagerDateHolidaysAdapter`, and
`FrankfurterCurrencyAdapter`, confirm that all three adapters still work
against their **real public APIs**, and that the cache is actually
populated on a first call and actually read back on an identical second
call, for all three providers.

**This script makes real network calls.** Open-Meteo, Nager.Date, and
Frankfurter are all free, keyless public APIs -- no API key is required or
read by this script, and there is no cost concern. No Groq, Anthropic,
Kiwi/MCP, or scraping call is made anywhere in this script.

## Required environment variable

```bash
RUN_LIVE_PROVIDER_CACHE_SMOKE=true
```

If this is missing or not truthy (`true`/`1`/`yes`), the script prints why
and exits immediately (`exit 0`) without importing `app.*` or opening any
network connection.

## How to run manually

From the repo root:

```bash
RUN_LIVE_PROVIDER_CACHE_SMOKE=true \
python backend/scripts/manual_provider_cache_smoke.py
```

The script:

1. Creates its own temporary, clearly-named `ProviderCacheStore` SQLite
   file (`tempfile.mkdtemp(prefix="travelobligator_manual_provider_cache_smoke_")`)
   -- **never** the app's real `Settings.provider_cache_path`
   (`backend/.data/provider_cache.sqlite3`) and never any trip storage.
   This file is deleted when the script exits.
2. Calls each of the three adapters twice with the same known, fixed
   inputs (Lisbon, Portugal coordinates for Open-Meteo; Lisbon, Portugal
   country/current year for Nager.Date; USD -> EUR for Frankfurter),
   sharing that one temporary cache store.
3. Prints one summary line per provider:
   `provider=<name> live_path_ok=<bool> cache_path_ok=<bool>
   status=<status> cache_row_count=<int>`.
4. Prints a final `RESULT: PASS` or `RESULT: FAIL` line.

It never prints a full weather/holiday/currency payload, a raw query, or
any secret.

## What a PASS result means

- Each provider's **first** call reached the real live API and returned
  `status=success`.
- Each provider's **second**, identical call was served from the cache
  (`data_status="cached"`), not from a second live request.
- Each provider (`open_meteo`, `nager_date`, `frankfurter`) has at least
  one row in the temporary `ProviderCacheStore`.
- No cache row (`query_hash`, `payload_json`, or `metadata_json`) contains
  a secret-like marker (`api_key`, `token`, `secret`, `authorization`,
  `bearer`, `password`) -- expected, since none of these three providers
  require an API key.
- No `query_hash` contains the raw destination text used to build the
  query -- `query_hash` is always an opaque SHA-256 digest, never the raw
  query itself.

## What a PASS result does not mean

- **It does not assert an exact weather value, holiday name, or exchange
  rate.** The script only checks structure (`status`, `data_status`,
  presence of a cache row) -- never a specific temperature, a specific
  holiday's name, or a specific exchange-rate number. Live provider data
  changes day to day; asserting exact values here would make this script
  flaky for reasons that have nothing to do with the cache wiring it's
  meant to verify.
- **It does not mean the provider cache is wired into any other
  adapter.** OpenStreetMap/Overpass+Nominatim, `ProviderGateway`, and
  `PlanningOrchestrator` are untouched by Steps 164A-164D and are not
  exercised by this script at all.
- **It does not mean `PlanningOrchestrator` or full trip generation was
  exercised.** This script calls the three adapters directly with known,
  fixed inputs -- it never creates a trip, never calls `/generate`, and
  never touches `PlanningOrchestrator`.
- **It does not mean the frontend was exercised.** No frontend file is
  touched by this script.
- **It is not CI, and it is not run by CI.** This script is never run
  automatically by `pytest`, `python -m compileall`, GitHub Actions, or any
  other automated process -- it only runs when a human explicitly executes
  it, and a live-API hiccup on a given day is not a code regression by
  itself.

## Troubleshooting

| Observation | Meaning |
| --- | --- |
| Script exits immediately saying `RUN_LIVE_PROVIDER_CACHE_SMOKE is not set` | The script never called anything; set the env var to proceed. |
| `live_path_ok=False` for one provider | That provider's real API call failed or returned a non-`success` status (network issue, rate limit, outage) -- not necessarily a code defect. Re-run later. |
| `live_path_ok=True` but `cache_path_ok=False` | The first call succeeded and (for a normal run) should have written a cache row, but the second identical call didn't read it back as `"cached"` -- worth investigating the relevant adapter's cache wiring. |
| `cache_row_count=0` despite `live_path_ok=True` | The live call succeeded but nothing was written to the cache -- check `provider_cache_enabled` and the adapter's cache-write path. |
| `RESULT: FAIL` with no per-provider detail printed | An unexpected exception occurred; the script deliberately swallows exception details to avoid echoing request/response data, printing only a generic failure line. |

## Automated test coverage

`backend/app/tests/scripts/test_manual_provider_cache_smoke_script.py`
covers this script's safety boundary only -- it never calls a real
provider and never requires `RUN_LIVE_PROVIDER_CACHE_SMOKE`. It verifies:
the script exits safely and makes no network call when the env var is
missing/falsy; the script has a manual-only guard and documents it is
never used in CI; the script's top-level imports are stdlib-only (so
importing the module itself never touches the network); the script
contains no disallowed LLM/scraping framework import; the script never
prints a raw payload; the script uses a temporary cache path rather than
the app's real one; and the script checks all three expected providers
(`open_meteo`, `nager_date`, `frankfurter`).
