# Manual Provider-Cache Smoke Test (Step 164D.1, extended in Step 164F)

## Purpose

`backend/scripts/manual_provider_cache_smoke.py` is a **manual, dev-only**
smoke test. It is not part of the application runtime: it is never imported
by `app.main`, by any backend service, or by the automated `pytest` suite,
and it never runs during `python -m compileall`, normal app startup, or CI.
It only runs when a developer explicitly executes the file by hand.

Its purpose is narrow: after Steps 164A-164E wired `ProviderCacheStore`
into `OpenMeteoWeatherAdapter`, `NagerDateHolidaysAdapter`,
`FrankfurterCurrencyAdapter`, and (geocoding only)
`OpenStreetMapPlacesAdapter`, confirm that all four still work against
their **real public APIs**, and that the cache is actually populated on a
first call and actually read back on an identical second call, for all
four.

**OSM coverage is geocoding only (Step 164F).** This script calls
`OpenStreetMapPlacesAdapter.resolve_coordinates` -- the real Nominatim
destination lookup -- and nothing else on that adapter. **It does not call
and does not validate Overpass POI search** (attractions, restaurants,
accommodation POIs). As of Step 164G, that path *is* cache-wired
(docs/12_provider_architecture.md section 30) -- but it still has no
manual live-smoke coverage in this script.

**This script makes real network calls.** Open-Meteo, Nager.Date,
Frankfurter, and Nominatim are all free, keyless public APIs -- no API key
is required or read by this script, and there is no cost concern. No Groq,
Anthropic, Kiwi/MCP, or scraping call is made anywhere in this script.

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
2. Calls each of the four adapters with the same known, fixed inputs
   (Lisbon, Portugal coordinates for Open-Meteo; Lisbon, Portugal
   country/current year for Nager.Date; USD -> EUR for Frankfurter;
   "Lisbon, Portugal" as the geocode query text for OSM/Nominatim),
   sharing that one temporary cache store. Open-Meteo, Nager.Date, and
   Frankfurter are each called twice directly; OSM geocoding
   (`resolve_coordinates`) is called twice through two separate, fresh
   adapter instances so a network-call counter can prove the second call
   made no additional live request (`resolve_coordinates` returns a plain
   coordinate, not a response object with a `data_status` field the way
   the other three do).
3. Prints one summary line per provider:
   `provider=<name> live_path_ok=<bool> cache_path_ok=<bool>
   status=<status> cache_row_count=<int>`, for example:
   `provider=openstreetmap_geocode live_path_ok=True cache_path_ok=True
   status=success cache_row_count=1`.
4. Prints a final `RESULT: PASS` or `RESULT: FAIL` line.

It never prints a full weather/holiday/currency/geocode payload, a raw
query, an API URL with its query string, or any secret.

## What a PASS result means

- Each provider's **first** call reached the real live API and returned a
  usable result (`status=success`, or for OSM geocoding, a real
  coordinate).
- Each provider's **second**, identical call was served from the cache --
  for Open-Meteo/Nager.Date/Frankfurter that's `data_status="cached"`; for
  OSM geocoding it's a second call that needed no additional live HTTP
  request.
- Each provider (`open_meteo`, `nager_date`, `frankfurter`,
  `openstreetmap_geocode`) has at least one row in the temporary
  `ProviderCacheStore`.
- No cache row (`query_hash`, `payload_json`, or `metadata_json`) contains
  a secret-like marker (`api_key`, `token`, `secret`, `authorization`,
  `bearer`, `password`) -- expected, since none of these four providers
  require an API key.
- No `query_hash` contains the raw destination/search text used to build
  the query, and OSM geocoding's cache row has empty `metadata_json` --
  `query_hash` is always an opaque SHA-256 digest, never the raw query
  itself, and no raw destination text is ever smuggled into `metadata`
  either.
- **For OSM specifically, PASS means a real live Nominatim geocode
  response parsed successfully and the persistent cache was actually used
  on the second lookup** -- it says nothing about any other destination
  string, and nothing about Overpass.

## What a PASS result does not mean

- **It does not assert an exact weather value, holiday name, exchange
  rate, geocode coordinate, OSM ID, or display name.** The script only
  checks structure (`status`, presence/shape of a resolved value, presence
  of a cache row) -- never a specific temperature, a specific holiday's
  name, a specific exchange-rate number, or a specific latitude/longitude/
  OSM ID/place name. Live provider data changes day to day (and even a
  stable destination like "Lisbon, Portugal" is not certain to geocode
  identically forever); asserting exact values here would make this script
  flaky for reasons that have nothing to do with the cache wiring it's
  meant to verify. **A PASS today does not mean this destination -- or any
  other -- will keep geocoding successfully forever.**
- **It does not validate OSM/Overpass POI search at all.** Only
  `OpenStreetMapPlacesAdapter.resolve_coordinates` (geocoding) is called.
  `search_attractions`, `search_restaurants`, `search_accommodation_pois`,
  and `search_must_visit_place` -- all of which call Overpass, not just
  Nominatim -- are never exercised by this script. Overpass POI search
  *is* cache-wired as of Step 164G, but still has no manual live-smoke
  coverage here. A PASS here says nothing about whether Overpass POI
  search currently works.
- **It does not mean the provider cache is wired into any other
  adapter.** `ProviderGateway` and `PlanningOrchestrator` are untouched by
  Steps 164A-164F and are not exercised by this script at all.
- **It does not mean `PlanningOrchestrator` or full trip generation was
  exercised.** This script calls the four adapters directly with known,
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
| `openstreetmap_geocode` `live_path_ok=False` | Nominatim returned nothing usable, an implausible match, or the request failed for "Lisbon, Portugal" specifically -- network issue, rate limit, or a real geocoding regression. Re-run later before assuming a code defect. |
| `openstreetmap_geocode` `live_path_ok=True` but `cache_path_ok=False` | The first lookup succeeded and should have written a cache row, but the second lookup (via a fresh adapter instance) needed another live HTTP call instead of reading the persisted entry -- worth investigating `_resolve_destination`'s cache wiring. |
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
the app's real one; the script checks all four expected providers
(`open_meteo`, `nager_date`, `frankfurter`, `openstreetmap_geocode`); and
the script never calls OSM/Overpass POI search
(`search_attractions`/`search_restaurants`/`search_accommodation_pois`/
`search_must_visit_place`/`_query_overpass`).
