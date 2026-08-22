# Manual Provider-Cache Smoke Test (Step 164D.1, extended in Steps 164F/164H)

## Purpose

`backend/scripts/manual_provider_cache_smoke.py` is a **manual, dev-only**
smoke test. It is not part of the application runtime: it is never imported
by `app.main`, by any backend service, or by the automated `pytest` suite,
and it never runs during `python -m compileall`, normal app startup, or CI.
It only runs when a developer explicitly executes the file by hand.

Its purpose is narrow: after Steps 164A-164E/164G wired `ProviderCacheStore`
into `OpenMeteoWeatherAdapter`, `NagerDateHolidaysAdapter`,
`FrankfurterCurrencyAdapter`, and `OpenStreetMapPlacesAdapter` (geocoding,
then Overpass POI search), confirm that all four adapters still work
against their **real public APIs**, and that the cache is actually
populated on a first call and actually read back on an identical second
call, for each provider this script covers.

**OSM coverage is geocoding plus one small, real Overpass POI search
(Step 164H).** This script calls `OpenStreetMapPlacesAdapter.resolve_coordinates`
(the real Nominatim destination lookup) and
`OpenStreetMapPlacesAdapter.search_attractions` (one respectful, single-
category Overpass search around the same known destination) -- nothing
else on that adapter. **It never calls `search_restaurants`,
`search_accommodation_pois`, or `search_must_visit_place`.** The POI check
is deliberately **structural only**: it confirms a real Overpass response
parses into the normal `NormalizedPlace` shape and that the cache is
populated/reused, not that any specific attraction, category, or
destination will keep returning results.

**This script makes real network calls.** Open-Meteo, Nager.Date,
Frankfurter, Nominatim, and Overpass are all free, keyless public APIs --
no API key is required or read by this script, and there is no cost
concern. No Groq, Anthropic, Kiwi/MCP, or scraping call is made anywhere
in this script.

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
   "Lisbon, Portugal" as both the geocode query text and the OSM/Overpass
   POI search destination), sharing that one temporary cache store.
   Open-Meteo, Nager.Date, and Frankfurter are each called twice directly.
   OSM geocoding (`resolve_coordinates`) and OSM/Overpass POI search
   (`search_attractions`) are each called twice through two separate,
   fresh adapter instances so a network-call counter can prove the second
   call made no additional live request -- neither method exposes a
   `data_status` field at the top level the way the other three providers'
   responses do (for POI search, caching happens per individual place
   inside the response, not at the envelope level).
3. Prints one summary line per provider:
   `provider=<name> live_path_ok=<bool> cache_path_ok=<bool>
   status=<status> cache_row_count=<int>`, for example:
   `provider=openstreetmap_poi live_path_ok=True cache_path_ok=True
   status=success cache_row_count=1`.
4. Prints a final `RESULT: PASS` or `RESULT: FAIL` line.

It never prints a full weather/holiday/currency/geocode/POI payload, a raw
query, a raw Overpass query string, an API URL with its query string, or
any secret.

## What a PASS result means

- Each provider's **first** call reached the real live API and returned a
  usable result (`status=success`/`partial`/`fallback_used`, or for OSM
  geocoding, a real coordinate).
- Each provider's **second**, identical call was served from the cache --
  for Open-Meteo/Nager.Date/Frankfurter that's `data_status="cached"`; for
  OSM geocoding and OSM/Overpass POI search it's a second call that needed
  no additional live HTTP request.
- Each provider (`open_meteo`, `nager_date`, `frankfurter`,
  `openstreetmap_geocode`, `openstreetmap_poi`) has at least one row in
  the temporary `ProviderCacheStore`.
- No cache row (`query_hash`, `payload_json`, or `metadata_json`) contains
  a secret-like marker (`api_key`, `token`, `secret`, `authorization`,
  `bearer`, `password`) -- expected, since none of these providers require
  an API key.
- No `query_hash` contains the raw destination/search text used to build
  the query, and the OSM geocode/POI cache rows have empty `metadata_json`
  -- `query_hash` is always an opaque SHA-256 digest, never the raw query
  itself, and no raw destination text is ever smuggled into `metadata`
  either.
- **The cached `openstreetmap_poi` payload contains no rating, price,
  opening-hours, availability, booking-link, or route-time marker** --
  checked directly against the stored bytes, not just the schema, since
  `NormalizedPlace` never carries any of those fields to begin with.
- **For OSM geocoding, PASS means a real live Nominatim response parsed
  successfully and the persistent cache was actually used on the second
  lookup.** **For OSM/Overpass POI search, PASS means one real Overpass
  category search for one known destination parsed into the normal
  `NormalizedPlace` shape and the persistent cache was actually used on
  the second, identical search.** Neither says anything about any other
  destination, category, or method.

## What a PASS result does not mean

- **It does not assert an exact weather value, holiday name, exchange
  rate, geocode coordinate, POI name, OSM ID, or display name.** The
  script only checks structure (`status`, presence/shape of a resolved
  value, presence of a cache row) -- never a specific temperature, a
  specific holiday's name, a specific exchange-rate number, or a specific
  latitude/longitude/OSM ID/place name. Live provider data changes day to
  day (and even a stable destination like "Lisbon, Portugal" is not
  certain to geocode or return the same attractions identically forever);
  asserting exact values here would make this script flaky for reasons
  that have nothing to do with the cache wiring it's meant to verify. **A
  PASS today does not mean this destination -- or any other -- will keep
  geocoding or returning POI results successfully forever.**
- **It does not validate every POI, category, or destination.** Only one
  Overpass method (`search_attractions`) for one known destination
  ("Lisbon, Portugal") is exercised. `search_restaurants`,
  `search_accommodation_pois`, and `search_must_visit_place` -- and every
  other destination/category combination -- are never called by this
  script. A PASS here says nothing about whether any of those currently
  work.
- **It does not validate ratings, prices, opening hours, booking links,
  availability, or route times.** `NormalizedPlace` never carries any of
  those fields, cached or live, so this script has nothing to check there
  beyond confirming the cached payload stays free of them -- it is not a
  claim that such data exists or was checked.
- **It does not mean the provider cache is wired into any other
  adapter.** `ProviderGateway` and `PlanningOrchestrator` are untouched by
  Steps 164A-164H and are not exercised by this script at all.
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
| `openstreetmap_poi` `live_path_ok=False` | Overpass returned nothing named/contained for "Lisbon, Portugal" attractions, or the request failed -- network issue, rate limit, outage, or (rarely) an actual regression. Re-run later before assuming a code defect. |
| `openstreetmap_poi` `live_path_ok=True` but `cache_path_ok=False` | The first search succeeded and should have written a cache row, but the second, fresh-instance search needed another live Overpass request -- either the result came entirely via fallback tags the first time (which caches independently per tag and can legitimately need a mix of live/cached calls on the next run) or `_try_query`'s cache wiring needs investigating. |
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
prints a raw payload or a raw Overpass query; the script uses a temporary
cache path rather than the app's real one; the script checks all five
expected providers (`open_meteo`, `nager_date`, `frankfurter`,
`openstreetmap_geocode`, `openstreetmap_poi`); and the script only ever
calls one Overpass method, `search_attractions` -- never
`search_restaurants`, `search_accommodation_pois`,
`search_must_visit_place`, or Overpass directly.
