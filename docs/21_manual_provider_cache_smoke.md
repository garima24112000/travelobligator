# Manual Provider-Cache Smoke Test (Step 164D.1, extended in Steps 164F/164H/165D)

## Purpose

`backend/scripts/manual_provider_cache_smoke.py` is a **manual, dev-only**
smoke test. It is not part of the application runtime: it is never imported
by `app.main`, by any backend service, or by the automated `pytest` suite,
and it never runs during `python -m compileall`, normal app startup, or CI.
It only runs when a developer explicitly executes the file by hand.

Its purpose is narrow: after Steps 164A-164E/164G wired `ProviderCacheStore`
into `OpenMeteoWeatherAdapter`, `NagerDateHolidaysAdapter`,
`FrankfurterCurrencyAdapter`, and `OpenStreetMapPlacesAdapter` (geocoding,
then Overpass POI search), and Step 165C wired it into `OSRMRoutingAdapter`,
confirm that all of these adapters still work against their **real public
APIs**, and that the cache is actually populated on a first call and
actually read back on an identical second call, for each provider this
script covers.

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

**OSRM coverage is one tiny, fixed route lookup, called twice (Step
165D).** This script calls `OSRMRoutingAdapter.get_route` for one small,
stable origin/destination pair near the same known Lisbon destination used
by the other providers, against a real OSRM instance -- by default the
public OSRM demo server (https://router.project-osrm.org), or
`OSRM_BASE_URL` if set. This check is also deliberately **structural
only**: it confirms a real OSRM response parses into a usable
`RouteResult` (`status=success`, a positive numeric `distance_meters` and
`duration_seconds`) and that the route cache is populated/reused -- it
**never asserts an exact distance, duration, or geometry**, does **not**
validate every route/profile/destination pair, and does **not** mean route
data is consumed by itinerary scheduling or validation anywhere in the
app (it still isn't, as of this step).

**This script makes real network calls.** Open-Meteo, Nager.Date,
Frankfurter, Nominatim, Overpass, and an OSRM routing instance are all
free, keyless public services -- no API key is required or read by this
script, and there is no cost concern. No Groq, Anthropic, Kiwi/MCP, or
scraping call is made anywhere in this script.

## Required environment variable

```bash
RUN_LIVE_PROVIDER_CACHE_SMOKE=true
```

If this is missing or not truthy (`true`/`1`/`yes`), the script prints why
and exits immediately (`exit 0`) without importing `app.*` or opening any
network connection.

## Optional environment variable (Step 165D)

```bash
OSRM_BASE_URL=<your own OSRM instance>
```

If unset, the OSRM route check defaults to the public OSRM demo server
(`https://router.project-osrm.org`). This is used only for this manual
smoke context -- it never changes `Settings.osrm_base_url`'s app-wide
default, which stays unset unless a developer configures it separately for
real use.

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
2. Calls each of the adapters with the same known, fixed inputs
   (Lisbon, Portugal coordinates for Open-Meteo; Lisbon, Portugal
   country/current year for Nager.Date; USD -> EUR for Frankfurter;
   "Lisbon, Portugal" as both the geocode query text and the OSM/Overpass
   POI search destination; a tiny, fixed driving route between two nearby
   Lisbon coordinates for OSRM), sharing that one temporary cache store.
   Open-Meteo, Nager.Date, and Frankfurter are each called twice directly.
   OSM geocoding (`resolve_coordinates`) and OSM/Overpass POI search
   (`search_attractions`) are each called twice through two separate,
   fresh adapter instances so a network-call counter can prove the second
   call made no additional live request -- neither method exposes a
   `data_status` field at the top level the way the other three providers'
   responses do (for POI search, caching happens per individual place
   inside the response, not at the envelope level). OSRM
   (`OSRMRoutingAdapter.get_route`) is also called twice through two
   separate, fresh adapter instances sharing the same cache store; a cache
   hit and a live call return the identical normalized `RouteResult`
   shape, so equal values on the second call plus a persisted cache row is
   the observable proof of reuse.
3. Prints one summary line per provider:
   `provider=<name> live_path_ok=<bool> cache_path_ok=<bool>
   status=<status> cache_row_count=<int>`, for example:
   `provider=openstreetmap_poi live_path_ok=True cache_path_ok=True
   status=success cache_row_count=1`.
4. Prints a final `RESULT: PASS` or `RESULT: FAIL` line.

It never prints a full weather/holiday/currency/geocode/POI/route payload,
a raw query, a raw Overpass query string, a raw route/coordinate URL, an
API URL with its query string, or any secret.

## What a PASS result means

- Each provider's **first** call reached the real live API and returned a
  usable result (`status=success`/`partial`/`fallback_used`, or for OSM
  geocoding, a real coordinate).
- Each provider's **second**, identical call was served from the cache --
  for Open-Meteo/Nager.Date/Frankfurter that's `data_status="cached"`; for
  OSM geocoding and OSM/Overpass POI search it's a second call that needed
  no additional live HTTP request; for OSRM it's a second call that
  returned the identical `distance_meters`/`duration_seconds` values as
  the first, with a cache row present.
- Each provider (`open_meteo`, `nager_date`, `frankfurter`,
  `openstreetmap_geocode`, `openstreetmap_poi`, `osrm_route`) has at least
  one row in the temporary `ProviderCacheStore`.
- No cache row (`query_hash`, `payload_json`, or `metadata_json`) contains
  a secret-like marker (`api_key`, `token`, `secret`, `authorization`,
  `bearer`, `password`) -- expected, since none of these providers require
  an API key.
- No `query_hash` contains the raw destination/search text used to build
  the query, and the OSM geocode/POI/OSRM route cache rows have empty
  `metadata_json` -- `query_hash` is always an opaque SHA-256 digest,
  never the raw query itself, and no raw destination text, coordinate
  value, or route URL is ever smuggled into `metadata` either.
- **The cached `openstreetmap_poi` payload contains no rating, price,
  opening-hours, availability, booking-link, or route-time marker** --
  checked directly against the stored bytes, not just the schema, since
  `NormalizedPlace` never carries any of those fields to begin with.
- **The cached `osrm_route` payload contains only normalized route fields**
  (`distance_meters`, `duration_seconds`, `geometry`, `confidence`) -- no
  raw coordinate query text or request URL.
- **For OSM geocoding, PASS means a real live Nominatim response parsed
  successfully and the persistent cache was actually used on the second
  lookup.** **For OSM/Overpass POI search, PASS means one real Overpass
  category search for one known destination parsed into the normal
  `NormalizedPlace` shape and the persistent cache was actually used on
  the second, identical search.** **For OSRM routing, PASS means one real
  route lookup for one known origin/destination pair parsed into a usable
  `RouteResult` (a positive numeric distance and duration) and the
  persistent cache was actually used on the second, identical lookup.**
  None of these say anything about any other destination, category,
  route, profile, or method.

## What a PASS result does not mean

- **It does not assert an exact weather value, holiday name, exchange
  rate, geocode coordinate, POI name, OSM ID, display name, route
  distance, route duration, or route geometry.** The script only checks
  structure (`status`, presence/shape of a resolved value, positivity of a
  numeric distance/duration, presence of a cache row) -- never a specific
  temperature, a specific holiday's name, a specific exchange-rate number,
  a specific latitude/longitude/OSM ID/place name, or a specific route
  distance/duration/geometry value. Live provider data changes day to day
  (and even a stable destination/route like "Lisbon, Portugal" is not
  certain to geocode, return the same attractions, or route identically
  forever); asserting exact values here would make this script flaky for
  reasons that have nothing to do with the cache wiring it's meant to
  verify. **A PASS today does not mean this destination or route -- or any
  other -- will keep geocoding, returning POI results, or routing
  successfully forever.**
- **It does not validate every POI, category, or destination.** Only one
  Overpass method (`search_attractions`) for one known destination
  ("Lisbon, Portugal") is exercised. `search_restaurants`,
  `search_accommodation_pois`, and `search_must_visit_place` -- and every
  other destination/category combination -- are never called by this
  script. A PASS here says nothing about whether any of those currently
  work.
- **It does not validate every route, profile, or origin/destination
  pair.** Only one fixed, tiny driving route near Lisbon is exercised
  against `OSRMRoutingAdapter.get_route`. A PASS here says nothing about
  whether walking/cycling profiles, longer routes, other regions, or any
  other origin/destination pair currently work.
- **It does not validate ratings, prices, opening hours, booking links,
  availability, or route times.** `NormalizedPlace` never carries any of
  those fields, cached or live, so this script has nothing to check there
  beyond confirming the cached payload stays free of them -- it is not a
  claim that such data exists or was checked.
- **It does not mean route data is used in itinerary scheduling or
  validation.** `PlanningOrchestrator`, `ExperiencePlannerService`, and
  `PlanValidatorService` still do not call `OSRMRoutingAdapter`/
  `ProviderGateway.get_route` anywhere -- a PASS here verifies only that
  OSRM parsing and its cache work when called directly, not that routing
  is consumed anywhere in the planning pipeline yet.
- **It does not mean the provider cache is wired into any other
  adapter.** `ProviderGateway` delegates routing lookups through to
  `OSRMRoutingAdapter` (Step 165B/165C), but no other adapter beyond the
  ones this script covers is cache-wired, and none of this is exercised
  through `ProviderGateway` or `PlanningOrchestrator` by this script.
- **It does not mean `PlanningOrchestrator` or full trip generation was
  exercised.** This script calls the adapters directly with known, fixed
  inputs -- it never creates a trip, never calls `/generate`, and never
  touches `PlanningOrchestrator`.
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
| `osrm_route` `live_path_ok=False` | The OSRM instance (public demo, or `OSRM_BASE_URL` if set) returned `NoRoute`/an error/no usable route for the fixed Lisbon coordinates, or the request failed -- network issue, rate limit, outage, or (rarely) an actual regression. Re-run later before assuming a code defect. |
| `osrm_route` `live_path_ok=True` but `cache_path_ok=False` | The first route lookup succeeded and should have written a cache row, but the second, fresh-instance lookup returned different values or needed another live OSRM request -- worth investigating `OSRMRoutingAdapter`'s cache wiring (Step 165C). |
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
prints a raw payload, a raw Overpass query, a raw route URL, or a raw
coordinate query string; the script uses a temporary cache path rather
than the app's real one; the script checks all six expected providers
(`open_meteo`, `nager_date`, `frankfurter`, `openstreetmap_geocode`,
`openstreetmap_poi`, `osrm_route`); the script only ever calls one
Overpass method, `search_attractions` -- never `search_restaurants`,
`search_accommodation_pois`, `search_must_visit_place`, or Overpass
directly; and the OSRM route check only ever asserts structural success
(`distance_meters > 0`, `duration_seconds > 0`) -- never an exact
distance/duration/geometry value.
