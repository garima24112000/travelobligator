# Provider Architecture

## 1. Purpose

This document defines how TravelObligator connects to external data sources.

The goal is to keep provider logic separate from planning logic.

The planning pipeline should not directly depend on one specific API such as Google Places, OpenStreetMap, Amadeus, Mapbox, or OpenAI.

Instead, the backend should use provider interfaces.

This makes the system:

- easier to test
- easier to replace providers
- easier to handle unavailable data
- easier to avoid fake data
- easier to explain provider coverage to the user

---

## 2. Core Rule

Providers supply facts.

The planning pipeline interprets those facts.

AI may explain, summarize, classify, and reason from provider data, but AI must not invent provider-backed facts.

---

## 3. Provider Gateway

The backend should use a central `ProviderGateway`.

The ProviderGateway is responsible for:

- calling external providers
- retrying failed provider requests
- using fallback providers when available
- normalizing provider responses
- tracking provider status
- tracking provider coverage
- marking unavailable fields
- preventing fake fallback data
- returning consistent provider response objects

High-level flow:

```text
Planning Service
→ ProviderGateway
→ Provider Interface
→ Provider Adapter
→ External API / Open Data Source
→ Normalized Provider Response
→ Planning State
```

---

## 4. Provider Interface Pattern

Every provider should follow the same pattern.

```text
Interface
→ Adapter
→ Normalized Response
```

Example:

```text
PlacesProvider
→ OpenStreetMapPlacesAdapter
→ NormalizedPlace[]
```

Another example:

```text
RoutesProvider
→ GoogleRoutesAdapter
→ NormalizedRoute[]
```

The planning pipeline should depend on provider interfaces, not provider-specific APIs.

---

## 5. Standard Provider Response

Every provider call should return a normalized response shape.

```json
{
  "provider_name": "openstreetmap",
  "provider_type": "places",
  "status": "success",
  "data_status": "live",
  "data": [],
  "unavailable_fields": [],
  "fallback_used": false,
  "fallback_provider": null,
  "retrieved_at": "2026-07-03T18:00:00Z",
  "confidence": 0.86,
  "message": null
}
```

If the provider fails:

```json
{
  "provider_name": "google_routes",
  "provider_type": "routes",
  "status": "failed",
  "data_status": "unavailable",
  "data": null,
  "unavailable_fields": [
    "travel_time_minutes",
    "distance_km"
  ],
  "fallback_used": false,
  "fallback_provider": null,
  "retrieved_at": "2026-07-03T18:00:00Z",
  "confidence": 0.0,
  "message": "Route data could not be verified."
}
```

If fallback is used:

```json
{
  "provider_name": "mapbox_directions",
  "provider_type": "routes",
  "status": "fallback_used",
  "data_status": "fallback_used",
  "data": [],
  "unavailable_fields": [],
  "fallback_used": true,
  "fallback_provider": "mapbox_directions",
  "retrieved_at": "2026-07-03T18:00:00Z",
  "confidence": 0.72,
  "message": "Primary route provider failed. Fallback route provider was used."
}
```

Provider adapters must set `unavailable_fields` and `data_status`
explicitly and accurately, not leave them empty/default when data is
actually missing. The frontend's provider transparency panel
(docs/16_frontend_architecture.md section 28) now surfaces these values
directly to the user, grouped by `provider_type` — an adapter that omits
an unavailable field or reports an inaccurate `data_status` will show up
as a false transparency claim on screen, not just an internal bookkeeping
gap.

---

## 6. Provider Status Values

Allowed provider statuses:

```text
not_requested
success
retrying
fallback_used
partial
failed
unavailable
not_connected
```

---

## 7. Data Status Values

Allowed data statuses:

```text
live
cached
fallback_used
estimated
scheduled
user_provided
ai_inferred
unavailable
failed
not_connected
```

---

## 8. Provider Coverage

Provider coverage explains what data was actually available for a planning run.

Example:

```json
{
  "places": "available",
  "routes": "available",
  "restaurants": "open_data_available",
  "accommodations": "open_poi_available",
  "hotel_prices": "provider_available",
  "vacation_rentals": "not_connected",
  "airbnb": "not_connected",
  "flights": "not_enabled",
  "weather": "available"
}
```

Provider coverage should be stored in Planning State.

The frontend should use provider coverage to explain:

- what was searched
- what was not searched
- what was unavailable
- what was provider-backed
- what was open-data-backed
- what was user-provided
- what was estimated

---

## 9. Retry and Fallback Policy

When a provider call fails, the system should:

1. Retry the provider call when appropriate.
2. If retry fails, use a fallback provider when available.
3. If fallback data is used, label it clearly.
4. If no reliable data is available, mark the field as unavailable or low confidence.
5. Never replace missing provider data with mock data, scraped data, or AI-generated facts.

Provider failure should reduce confidence.

It should not create hallucinated certainty.

---

## 10. PlacesProvider

The PlacesProvider is responsible for place discovery and place metadata.

Used by:

- Destination Context
- Stay + Transport
- Experience Planner
- Plan Validator

### Methods

```text
search_places(destination, categories, filters)
get_place_details(place_id)
search_restaurants(area, filters)
search_attractions(destination, filters)
search_accommodation_pois(destination, filters)
```

### Possible Adapters

```text
OpenStreetMapPlacesAdapter
GooglePlacesAdapter
FoursquarePlacesAdapter
ApprovedPlacesProviderAdapter
```

### NormalizedPlace

```json
{
  "place_id": "",
  "name": "",
  "category": "",
  "coordinates": {},
  "address": "",
  "rating": {
    "value": null,
    "review_count": null,
    "data_status": "unavailable"
  },
  "opening_hours": {
    "value": null,
    "data_status": "unavailable"
  },
  "price_level": {
    "value": null,
    "data_status": "unavailable"
  },
  "source": "openstreetmap",
  "data_status": "live",
  "confidence": 0.8
}
```

### Rules

- Do not invent places.
- Do not invent ratings.
- Do not invent opening hours.
- Do not invent review counts.
- OpenStreetMap can provide real POIs, but ratings and review counts should be unavailable unless returned by a legitimate source.
- Provider-backed POIs must be geographically contained to the resolved
  destination (inside its geocoder-returned bounding box, or a
  conservative radius around the resolved point when no bounding box
  exists). A real, named place is still discarded if it falls outside
  that containment (Step 155C, fixing a bug where an unresolved/
  under-specified destination string silently anchored every subsequent
  POI search to an unrelated place in a different country).
- Broad token fallback is not allowed: if the full destination string
  can't be confidently geocoded, do not retry with a shorter or looser
  fragment of it (e.g. retrying "New York" with just "New").
- If containment can't be verified for a candidate result, it must be
  marked unavailable rather than used -- never presented as
  provider-backed for a destination it isn't actually located in.
- If provider metadata is missing, return unavailable fields explicitly.

---

## 11. RoutesProvider

The RoutesProvider is responsible for travel time, distance, and route feasibility.

Used by:

- Destination Context
- Stay + Transport
- Experience Planner
- Plan Validator
- Feedback Pipeline

### Methods

```text
get_route(origin, destination, mode)
get_route_matrix(origins, destinations, mode)
estimate_walking_distance(origin, destination)
estimate_transit_feasibility(origin, destination, date_time)
```

### Possible Adapters

```text
OpenTripPlannerAdapter
GoogleRoutesAdapter
MapboxDirectionsAdapter
OpenStreetMapRoutingAdapter
```

### NormalizedRoute

```json
{
  "origin": {},
  "destination": {},
  "mode": "walking",
  "distance_km": 2.4,
  "travel_time_minutes": 28,
  "route_geometry": null,
  "transit_details": null,
  "data_status": "live",
  "source": "routes_provider",
  "confidence": 0.86
}
```

### Rules

- AI must not invent exact travel times.
- AI must not invent exact walking distances.
- If route data is unavailable, return unavailable route fields.
- If missing route data affects feasibility, Plan Validator should flag the itinerary.
- Transit estimates should distinguish between live, scheduled, cached, and unavailable.

---

## 12. TransitProvider

The TransitProvider is responsible for scheduled or live transit feasibility.

Used by:

- Destination Context
- Stay + Transport
- Experience Planner
- Plan Validator

### Methods

```text
get_transit_options(origin, destination, date_time)
get_nearby_transit_stops(location)
check_transit_feasibility(area, destination_clusters)
```

### Possible Adapters

```text
OpenTripPlannerTransitAdapter
GTFSAdapter
TransitlandAdapter
GoogleTransitAdapter
```

### NormalizedTransitOption

```json
{
  "origin": {},
  "destination": {},
  "departure_time": "",
  "arrival_time": "",
  "duration_minutes": null,
  "transfers": null,
  "walking_to_stop_minutes": null,
  "data_status": "scheduled",
  "source": "gtfs",
  "confidence": 0.75
}
```

### Rules

- Do not invent transit lines.
- Do not invent stop names.
- Do not invent transfer times.
- If only scheduled data is available, label it as scheduled.
- If live transit is unavailable, do not imply live transit was checked.

---

## 13. AccommodationProvider

The AccommodationProvider is responsible for accommodation discovery, metadata, prices, and availability when available.

Used by:

- Stay + Transport
- Plan Validator
- Feedback Pipeline

### Methods

```text
search_accommodation_options(destination, area, filters)
get_accommodation_details(accommodation_id)
get_accommodation_price(accommodation_id, dates)
get_accommodation_availability(accommodation_id, dates)
```

### Possible Adapters

```text
OpenStreetMapAccommodationAdapter
AmadeusHotelsAdapter
BookingDemandAdapter
ExpediaRapidAdapter
HotelbedsAdapter
HostelworldAdapter
ApprovedAccommodationProviderAdapter
```

### NormalizedAccommodationOption

```json
{
  "accommodation_id": "",
  "name": "",
  "accommodation_type": "hotel",
  "area": "",
  "coordinates": {},
  "estimated_price_per_night": {
    "amount": null,
    "currency": "USD",
    "data_status": "unavailable",
    "source": null,
    "confidence": 0.0
  },
  "availability_status": {
    "available": null,
    "data_status": "unavailable",
    "source": null,
    "confidence": 0.0
  },
  "rating": {
    "value": null,
    "review_count": null,
    "data_status": "unavailable",
    "source": null,
    "confidence": 0.0
  },
  "amenities": [],
  "booking_url": {
    "url": null,
    "data_status": "unavailable",
    "source": null
  },
  "source": "openstreetmap",
  "confidence": 0.55
}
```

### Rules

- Recommend accommodation options, not final bookings.
- Do not guarantee price or availability unless confirmed by a provider.
- If only OpenStreetMap accommodation POIs are available, price, availability, rating, and review count should be marked unavailable unless returned by a legitimate source.
- Do not imply Airbnb, Booking.com, Expedia, Vrbo, Tripadvisor, or similar platforms were searched unless approved provider access exists.
- Airbnb-style inventory may only be shown through approved or official integration.

---

## 14. FlightProvider

Flights are optional for the core single-city MVP.

The FlightProvider is responsible for provider-backed flight options when flight planning is enabled.

Used by:

- Trip Strategy
- Stay + Transport
- Plan Validator
- Future booking handoff

### Methods

```text
search_flights(origin, destination, dates, travelers)
get_flight_details(flight_id)
```

### Possible Adapters

```text
AmadeusFlightsAdapter
DuffelAdapter
ApprovedFlightProviderAdapter
```

### NormalizedFlightOption

```json
{
  "flight_id": "",
  "airline": "",
  "origin": "",
  "destination": "",
  "departure_time": "",
  "arrival_time": "",
  "duration_minutes": null,
  "stops": null,
  "price": {
    "amount": null,
    "currency": "USD",
    "data_status": "unavailable",
    "source": null,
    "confidence": 0.0
  },
  "availability_status": {
    "available": null,
    "data_status": "unavailable"
  },
  "baggage_details": {
    "value": null,
    "data_status": "unavailable"
  },
  "booking_url": {
    "url": null,
    "data_status": "unavailable"
  },
  "source": "",
  "confidence": 0.0
}
```

### Rules

- Do not invent flight options.
- Do not invent prices.
- Do not invent schedules.
- Do not invent baggage rules.
- Do not scrape Google Flights or unsupported OTA pages.
- Google Flights should not be treated as a normal public backend API unless approved access exists.

---

## 15. WeatherProvider

The WeatherProvider is optional for MVP.

Used by:

- Trip Strategy
- Experience Planner
- Plan Validator

### Methods

```text
get_weather_forecast(destination, dates)
get_weather_alerts(destination, dates)
```

### Possible Adapters

```text
OpenMeteoAdapter
NOAAAdapter
ApprovedWeatherProviderAdapter
```

### NormalizedWeatherForecast

```json
{
  "date": "2026-08-10",
  "condition": "rain",
  "high_temperature": 82,
  "low_temperature": 70,
  "precipitation_probability": 0.6,
  "data_status": "live",
  "source": "open_meteo",
  "confidence": 0.8
}
```

### Rules

- Do not invent weather.
- If weather is unavailable, skip weather-specific reasoning.
- Do not reroute based on weather unless provider-backed weather data exists.

---

## 16. HolidayProvider

The HolidayProvider is optional for MVP.

Used by:

- Destination Context
- Experience Planner
- Plan Validator

### Methods

```text
get_public_holidays(country, dates)
get_city_events(destination, dates)
```

### Possible Adapters

```text
NagerDateAdapter
OfficialHolidayCalendarAdapter
TicketmasterAdapter
ApprovedEventProviderAdapter
```

### Rules

- Do not invent holidays.
- Do not invent closures.
- Do not invent event availability.
- If holiday or event data is missing, mark it unavailable.

---

## 17. CurrencyProvider

The CurrencyProvider is used for budget normalization.

Used by:

- Traveler Profile
- Trip Strategy
- Stay + Transport
- Experience Planner
- Plan Validator

### Methods

```text
convert_currency(amount, from_currency, to_currency)
get_exchange_rate(from_currency, to_currency)
```

### Possible Adapters

```text
FrankfurterAdapter
ApprovedCurrencyProviderAdapter
```

### Rules

- Do not invent exchange rates.
- Currency conversion should include freshness and source.
- If currency data is unavailable, budget validation should be lower confidence.

---

## 18. AIReasoningProvider

The AIReasoningProvider is responsible for structured reasoning and explanations.

Used by:

- Traveler Profile
- Trip Strategy
- Stay + Transport explanations
- Experience Planner explanations
- Plan Validator subjective reasoning
- Feedback Pipeline

### Methods

```text
generate_traveler_profile(input)
generate_trip_strategy(input)
generate_decision_card(input)
generate_experience_explanation(input)
generate_validation_reasoning(input)
interpret_feedback(input)
summarize_change(input)
```

### Possible Adapters

```text
OpenAIStructuredOutputsAdapter
ApprovedLLMProviderAdapter
```

### Rules

AI may:

- interpret preferences
- summarize provider-backed or open-data-backed facts
- explain tradeoffs
- classify feedback
- create user-facing explanation wording
- evaluate subjective travel quality from available evidence

AI must not:

- invent provider facts
- invent places
- invent restaurants
- invent accommodation options
- invent prices
- invent ratings
- invent review counts
- invent schedules
- invent opening hours
- invent route times
- invent safety ratings
- convert unavailable data into confident recommendations

AI output should be schema-validated before it is accepted.

---

## 19. Provider Coverage Tracker

The ProviderGateway should update provider coverage after each provider call.

Example:

```json
{
  "places": "available",
  "routes": "available",
  "restaurants": "open_data_available",
  "accommodations": "open_poi_available",
  "hotel_prices": "provider_available",
  "vacation_rentals": "not_connected",
  "airbnb": "not_connected",
  "flights": "not_enabled",
  "weather": "available"
}
```

Provider coverage should be returned to the frontend through Planning State.

---

## 20. Provider Logs

The backend should store provider logs for debugging and transparency.

Provider logs should include:

- provider name
- provider type
- request timestamp
- status
- fallback used
- unavailable fields
- error message
- response freshness
- related trip id
- related planning state id

Provider logs should not store secrets or API keys.

---

## 21. Development Behavior

During early development, providers can be implemented gradually.

However:

- do not return mock provider facts as production facts
- do not hardcode fake listings, fake prices, or fake ratings
- use unavailable fields when a provider is not implemented
- use small real open-data calls where possible
- keep adapters replaceable

Allowed development behavior:

```json
{
  "provider_name": "accommodation_provider",
  "status": "not_connected",
  "data_status": "not_connected",
  "data": null,
  "unavailable_fields": [
    "price",
    "availability",
    "rating"
  ],
  "confidence": 0.0
}
```

Not allowed:

```json
{
  "name": "Fake Luxury Hotel",
  "rating": 4.8,
  "price": 199
}
```

---

## 22. Implementation Order

Recommended provider implementation order:

1. AIReasoningProvider
2. PlacesProvider using OpenStreetMap / Overpass
3. Destination resolution using Nominatim or GeoNames
4. RoutesProvider using OpenTripPlanner / GTFS / OpenStreetMap where available
5. WeatherProvider using Open-Meteo
6. HolidayProvider using Nager.Date
7. CurrencyProvider using Frankfurter
8. AccommodationProvider using OpenStreetMap accommodation POIs
9. Amadeus hotel or approved accommodation provider when production access is available
10. Optional richer providers such as Google Places, Google Routes, Mapbox, Foursquare, or approved partner providers

---

## 23. Design Principles

The provider architecture should follow these principles:

- Providers supply facts.
- AI supplies reasoning and explanation.
- Provider adapters should be replaceable.
- Planning services should not depend on provider-specific response shapes.
- Every provider response should include status, source, confidence, and unavailable fields.
- Provider failures should be visible.
- Fallback data should be labeled clearly.
- Not-connected sources should be labeled clearly.
- Open data should be treated as real but limited data.
- Restricted providers should not be implied as searched unless connected.
- The system should never use mock, scraped, or AI-invented factual travel data in MVP or production outputs.

---

## 24. Candidate Quality (Step 156A)

Open-data POIs from a `PlacesProvider` (e.g. OpenStreetMap) are real, but
not every real POI is a useful itinerary anchor: districts, minor
memorials, schools, reservoirs, administrative/local objects, and generic
historic districts are common examples of real, provider-backed places
that still make weak itinerary anchors.

`backend/app/services/candidate_quality_service.py`
(`CandidateQualityService`, docs/18_candidate_quality.md) adds a
deterministic candidate quality scoring layer that may demote weak
categories before planning/scheduling uses them. It only classifies
candidates already present in `DestinationContext`; it never calls a
provider, never invents a place, and never attaches a price, rating,
opening hour, route time, review count, booking link, or safety score.

---

## 25. Provider Cache Foundation (Step 164A)

`backend/app/storage/provider_cache_store.py` (`ProviderCacheStore`) adds
a small local SQLite cache store so repeated dev runs against free/open-data
providers (OpenStreetMap/Overpass + Nominatim, Open-Meteo, Nager.Date,
Frankfurter, and future providers like OSRM, Wikivoyage/Wikipedia,
lodging, or Kiwi/MCP) become cheaper and safer over time.

**This is foundation only.** As of Step 164A:

- No provider adapter (`OpenStreetMapPlacesAdapter`, `OpenMeteoWeatherAdapter`,
  `NagerDateHolidaysAdapter`, `FrankfurterCurrencyAdapter`) reads from or
  writes to this store.
- `ProviderGateway` and `PlanningOrchestrator` do not import it either.
- No current provider result, `data_status`, `provider_coverage`, or
  planning behavior changes because of this step.
- No API endpoint exposes it.

### 25.1 Keying: source + query_hash

Cache rows are keyed by `(source, query_hash)` -- never by the raw query.
`source` is a short provider identifier (e.g. `"openstreetmap_places"`,
`"open_meteo"`, `"nager_date"`, `"frankfurter"`), matching the
`provider_name` values already used in `ProviderResponse`/
`ProviderStatusEntry`. `query_hash` is produced by
`make_query_hash(query)`, a deterministic SHA-256 hash of the query
normalized to canonical JSON (`sort_keys=True`) -- so the same logical
query always hashes the same way regardless of dict key order, and the
raw query text (which could be a full destination string, coordinates,
or date range) is never itself written to the cache row, logged, or
returned by any cache method. Only the opaque hex digest is persisted.

### 25.2 Cache miss is always honest

A cache miss -- no row for `(source, query_hash)`, or a row whose
`expires_at` has passed -- always returns `None` from `ProviderCacheStore.get`.
**The cache never fabricates, guesses, or backfills a payload on miss**;
callers (once wired, in a later step) would still need to fall back to a
real provider call or an honest `unavailable`/`not_connected` result,
exactly as today. An expired row is left in place until `prune_expired`
is called explicitly, so miss-on-expiry never depends on background
cleanup having run.

### 25.3 What can be cached

`payload` (the cached value) and `metadata` must both be JSON-serializable
and non-secret -- `ProviderCacheStore.set` raises `ProviderCacheValueError`
otherwise. Never store an API key, token, prompt, raw LLM response, or any
other secret in a cache row. Never cache user-private trip data through
this store unless a future step explicitly designs that (this store today
is meant for provider *responses*, which are shared/public data, not
per-trip planning state).

### 25.4 Recommended TTL guidance (not enforced yet)

`ProviderCacheStore.set`'s `ttl_seconds` parameter is generic -- Step 164A
does not hardcode a per-source policy. Recommended starting points for
whichever future step wires a given provider in:

```text
geocode (Nominatim resolution)         long / effectively permanent
OSM POI geometry (Overpass)            long / permanent-ish
Wikipedia / Wikivoyage summaries       months
weather forecast (Open-Meteo)          short (hours)
currency exchange rate (Frankfurter)   short/medium (hours to a day)
lodging / flight prices                very short, or do not cache at all
scraped/personal-dev-only providers    very short, and clearly labeled
                                        as such when introduced later
```

Public holidays (Nager.Date) are date-based and rarely change once
published for a given year/country, so a long TTL is also reasonable
there, but this is guidance only -- no provider reads `ttl_seconds` from
this table yet.

### 25.5 Config

`Settings.provider_cache_path` (`PROVIDER_CACHE_PATH`, default
`.data/provider_cache.sqlite3`) and `Settings.provider_cache_enabled`
(`PROVIDER_CACHE_ENABLED`, default `true`) exist so a later wiring step
doesn't need a config change first -- `provider_cache_enabled` is declared
but not read by any code path yet.

---

## 26. Open-Meteo Weather Provider Cache Wiring (Step 164B)

`OpenMeteoWeatherAdapter` (`backend/app/providers/weather/open_meteo_adapter.py`)
is now the first provider adapter wired to the Step 164A `ProviderCacheStore`
foundation. **No other provider is wired yet** -- `OpenStreetMapPlacesAdapter`,
`NagerDateHolidaysAdapter`, `FrankfurterCurrencyAdapter`, `ProviderGateway`,
and `PlanningOrchestrator` still do not import or call `ProviderCacheStore`,
and every call they make still goes out live exactly as before this step.

### 26.1 Cache key

Cache rows are stored under source `"open_meteo"` (matching
`OpenMeteoWeatherAdapter.provider_name`), keyed by `query_hash =
make_query_hash(query)` where `query` is the normalized request:

```json
{
  "latitude": 34.0522,
  "longitude": -118.2437,
  "start_date": "2026-08-10",
  "end_date": "2026-08-12",
  "timezone": "auto"
}
```

Only these normalized fields feed the hash -- the raw destination string,
trip ID, or any other user-private field is never included, matching the
existing "never store raw query text" rule (section 25.1). The fixed set
of daily weather fields requested (`temperature_2m_max`, etc.) is not part
of the query, since it never varies per call.

### 26.2 What is cached

The cache stores the normalized `NormalizedDailyWeather[]` payload --
provider response data, not `PlanningState` or any other user-private trip
content. `metadata` is always empty (`{}`); no API key, token, prompt, or
raw LLM response is ever written (Open-Meteo itself requires no API key).
Only a `status=success` response with usable daily data is cached --
`unavailable` (no usable data, or Open-Meteo reported an error) and
`failed` (request-level failure) responses are never cached, so a transient
provider problem can never be replayed as a false "success" later.

### 26.3 Cache hit/miss behavior

A cache hit returns the exact same `ProviderResponse[list[NormalizedDailyWeather]]`
shape a live call would return, just relabeled `data_status="cached"` (both
at the response level and on each `NormalizedDailyWeather.data_status`) --
it never fabricates a field the live path wouldn't have populated. A cache
miss (no row, or an expired row) calls the existing live Open-Meteo HTTP
path exactly as before, normalizes it exactly as before, then caches the
normalized result with TTL `Settings.open_meteo_cache_ttl_seconds`
(`OPEN_METEO_CACHE_TTL_SECONDS`, default `3600`).

Cache reads and writes are both best-effort and never fail weather
retrieval: a broken cache read is logged and treated as a miss (falls
through to the live path), and a broken cache write is logged but the
already-computed live result is still returned. Neither log line includes
the query payload.

### 26.4 Dependency injection

`OpenMeteoWeatherAdapter.__init__` accepts an optional `cache_store:
ProviderCacheStore | None` parameter for tests. When not supplied, the
adapter lazily resolves a shared store via `get_provider_cache_store`
using `Settings.provider_cache_path` -- but only if
`Settings.provider_cache_enabled` is `true`; when `false`, the cache is
skipped completely (every call goes live), even if a `cache_store` was
explicitly injected. Constructing `OpenMeteoWeatherAdapter()` with no
arguments remains fully backward compatible.

---

## 27. Nager.Date Holiday Provider Cache Wiring (Step 164C)

`NagerDateHolidaysAdapter` (`backend/app/providers/holidays/nager_date_adapter.py`)
is now the second provider adapter wired to the Step 164A `ProviderCacheStore`
foundation (Open-Meteo was the first, section 26). **No other provider is
wired yet** -- `OpenStreetMapPlacesAdapter`, `FrankfurterCurrencyAdapter`,
`ProviderGateway`, and `PlanningOrchestrator` still do not import or call
`ProviderCacheStore`, and every call they make still goes out live exactly
as before this step.

### 27.1 Cache key

Cache rows are stored under source `"nager_date"` (matching
`NagerDateHolidaysAdapter.provider_name`), keyed by `query_hash =
make_query_hash(query)` where `query` is the normalized request:

```json
{
  "country_code": "PT",
  "year": 2026
}
```

Unlike Open-Meteo's per-trip-date-range key, Nager.Date is cached **per
calendar year**, not per trip date range -- this matches Nager.Date's own
API shape (`GET /api/v3/PublicHolidays/{year}/{country_code}`, one HTTP
call per year) and lets a different trip in the same country/year reuse
the same cache entry regardless of its specific date range. The adapter
has no subdivision/region parameter today, so none is part of the key. The
raw destination string, trip ID, or trip date range is never included in
the hash.

### 27.2 What is cached

The cache stores the normalized `NormalizedHoliday[]` payload for one
`(country_code, year)` -- provider response data, not `PlanningState` or
any other user-private trip content. `metadata` is always empty (`{}`); no
API key, token, prompt, or raw LLM response is ever written (Nager.Date
itself requires no API key). A year is only cached if its live fetch
produced at least one usable holiday; a malformed or empty payload for a
year is never cached, and neither an overall `unavailable` (no country
code, no usable data for any year) nor `failed` (request-level failure)
response is ever cached.

### 27.3 Cache hit/miss behavior

Each requested year is looked up independently. A cache hit for a year
returns that year's holidays in the exact same `NormalizedHoliday` shape a
live call would produce, relabeled `data_status="cached"`; a miss for a
year (no row, or an expired row -- expiry behaves exactly like a miss)
calls the existing live Nager.Date HTTP path for that year exactly as
before, normalizes it exactly as before, then caches the result with TTL
`Settings.nager_date_cache_ttl_seconds`
(`NAGER_DATE_CACHE_TTL_SECONDS`, default `2592000` = 30 days -- public
holiday calendars change slowly once published for a given year/country).
The overall `ProviderResponse.data_status` is `"cached"` only when *every*
requested year came from the cache; if any year required a live fetch
(including a multi-year trip where only one year misses), the overall
response is labeled `"live"`, since it's honestly a mix.

Cache reads and writes are both best-effort and never fail holiday
retrieval: a broken cache read for a given year is logged and treated as a
miss for that year (falls through to the live path), and a broken cache
write is logged but the already-computed live result is still returned.
Neither log line includes the query payload.

### 27.4 Dependency injection

`NagerDateHolidaysAdapter.__init__` accepts an optional `cache_store:
ProviderCacheStore | None` parameter for tests, mirroring
`OpenMeteoWeatherAdapter` (section 26.4). When not supplied, the adapter
lazily resolves a shared store via `get_provider_cache_store` using
`Settings.provider_cache_path` -- but only if `Settings.provider_cache_enabled`
is `true`; when `false`, the cache is skipped completely (every call goes
live), even if a `cache_store` was explicitly injected. Constructing
`NagerDateHolidaysAdapter()` with no arguments remains fully backward
compatible.

---

## 28. Frankfurter Currency Provider Cache Wiring (Step 164D)

`FrankfurterCurrencyAdapter` (`backend/app/providers/currency/frankfurter_adapter.py`)
is now the third provider adapter wired to the Step 164A `ProviderCacheStore`
foundation, alongside Open-Meteo (section 26) and Nager.Date (section 27).
**Other providers are not wired yet** -- `OpenStreetMapPlacesAdapter`,
`ProviderGateway`, and `PlanningOrchestrator` still do not import or call
`ProviderCacheStore`, and every call they make still goes out live exactly
as before this step.

### 28.1 Cache key

Cache rows are stored under source `"frankfurter"` (matching
`FrankfurterCurrencyAdapter.provider_name`), keyed by `query_hash =
make_query_hash(query)` where `query` is the normalized request:

```json
{
  "base_currency": "USD",
  "destination_currency": "EUR",
  "query_type": "latest"
}
```

`query_type` is a fixed `"latest"` marker rather than a real date, since
this adapter only ever calls Frankfurter's `/latest` endpoint and never
requests a historical rate. `amount` is not part of the key: this adapter
always requests a single-unit rate (`amount=1` is Frankfurter's implicit
default, never sent as a parameter) and the normalized result
(`NormalizedExchangeRate.exchange_rate`) does not depend on it. The raw
destination string, trip ID, or any other trip-private field is never
included in the hash.

### 28.2 What is cached

The cache stores the normalized `NormalizedExchangeRate` payload for one
`(base_currency, destination_currency)` pair -- provider response data,
not `PlanningState` or any other user-private trip content. `metadata` is
always empty (`{}`); no API key, token, prompt, or raw LLM response is
ever written (Frankfurter itself requires no API key). Only a
`status=success` response fetched over the network is cached --
`unavailable`/`failed` responses are never cached. The same-currency
identity result (`base_currency == destination_currency`, `exchange_rate
=1.0`, no HTTP call made at all) is also never cached, since there is
nothing to save by caching a computation that already skips the network.

### 28.3 Cache hit/miss behavior

A cache hit returns the exact same `ProviderResponse[NormalizedExchangeRate]`
shape a live call would return, relabeled `data_status="cached"` (both at
the response level and on `NormalizedExchangeRate.data_status`) -- it
never fabricates a rate the live path wouldn't have populated. A cache
miss (no row, or an expired row, which is treated exactly like a miss)
calls the existing live Frankfurter HTTP path exactly as before,
normalizes it exactly as before, then caches the normalized result with
TTL `Settings.frankfurter_cache_ttl_seconds`
(`FRANKFURTER_CACHE_TTL_SECONDS`, default `21600` = 6 hours -- currency
data can change but not minute-by-minute for this app).

Cache reads and writes are both best-effort and never fail currency
retrieval: a broken cache read is logged and treated as a miss (falls
through to the live path), and a broken cache write is logged but the
already-computed live result is still returned. Neither log line includes
the query payload.

### 28.4 Dependency injection

`FrankfurterCurrencyAdapter.__init__` accepts an optional `cache_store:
ProviderCacheStore | None` parameter for tests, mirroring
`OpenMeteoWeatherAdapter` (section 26.4) and `NagerDateHolidaysAdapter`
(section 27.4). When not supplied, the adapter lazily resolves a shared
store via `get_provider_cache_store` using `Settings.provider_cache_path`
-- but only if `Settings.provider_cache_enabled` is `true`; when `false`,
the cache is skipped completely (every call goes live), even if a
`cache_store` was explicitly injected. Constructing
`FrankfurterCurrencyAdapter()` with no arguments remains fully backward
compatible.

---

## 29. OpenStreetMap Geocoding Cache Wiring (Step 164E)

`OpenStreetMapPlacesAdapter` (`backend/app/providers/places/openstreetmap_adapter.py`)
is now the fourth provider adapter wired to the Step 164A `ProviderCacheStore`
foundation, alongside Open-Meteo (section 26), Nager.Date (section 27), and
Frankfurter (section 28). **This step is geocoding only.** Only
`_resolve_destination` -- the Nominatim destination-lookup step used by
`_search` (attractions/restaurants/accommodation), `resolve_coordinates`,
and `search_must_visit_place` -- is cache-wired. **OSM/Overpass POI
searches (attractions, restaurants, accommodation POIs) are not cached by
this step** and still go out live on every call, exactly as before.

### 29.1 Cache key

Cache rows are stored under source `"openstreetmap_geocode"` -- distinct
from `OpenStreetMapPlacesAdapter.provider_name`
(`"openstreetmap_places"`, which still labels every `ProviderResponse`
this adapter returns) -- keyed by `query_hash = make_query_hash(query)`
where `query` is the normalized request:

```json
{
  "query": "los angeles",
  "format": "jsonv2",
  "limit": 1
}
```

`query` is the destination/search text normalized (stripped and
lowercased) before hashing; the live Nominatim request itself still sends
the original, un-normalized string, unchanged. `format` and `limit` are
included even though currently fixed, so the hash would correctly change
if either is ever varied later. No country code, bounding box, or language
parameter is sent by this adapter today, so none is part of the key. As
with every other cache-wired provider, the raw query text is never
persisted as its own column or metadata field -- only the opaque
`query_hash` digest is stored; the normalized dict above exists only to
produce that hash.

### 29.2 What is cached

The cache stores the resolved geocode result (`lat`, `lng`,
`bounding_box`, `display_name`) for one normalized destination string --
provider response data, not `PlanningState` or any other user-private trip
content. `metadata` is always empty (`{}`); no API key, token, prompt, or
raw LLM response is ever written (Nominatim itself requires no API key).
Only a successfully resolved, plausibility-checked destination
(`_is_plausible_geocode_match`) is cached -- an unresolved destination, an
implausible/rejected match, or a request failure is never cached, matching
the existing "no broad token-fallback retry, no guessed location" rule
(section 10). **Overpass POI results are never written to this cache in
this step.**

### 29.3 Cache hit/miss behavior

A cache hit returns the exact same internal `_ResolvedDestination` shape a
live geocode would -- no coordinate, place name, or OSM ID is ever
fabricated on a hit. A cache miss (no row, or an expired row, treated
exactly like a miss) calls the existing live Nominatim request exactly as
before, applies the same plausibility check exactly as before, then caches
the result with TTL `Settings.osm_geocode_cache_ttl_seconds`
(`OSM_GEOCODE_CACHE_TTL_SECONDS`, default `2592000` = 30 days -- geocoding
a given destination string changes slowly).

This persistent cache sits **underneath** the adapter's pre-existing
per-instance `self._destination_cache` dict (unchanged, still checked
first as a zero-cost shortcut for repeated lookups of the same destination
within one adapter instance/request). The persistent cache extends reuse
across separate adapter instances, process restarts, and dev runs, which
the in-memory dict alone cannot do.

Cache reads and writes are both best-effort and never fail geocoding: a
broken cache read is logged and treated as a miss (falls through to the
live path), and a broken cache write is logged but the already-computed
live result is still returned. Neither log line includes the query
payload.

### 29.4 Dependency injection

`OpenStreetMapPlacesAdapter.__init__` accepts an optional `cache_store:
ProviderCacheStore | None` parameter for tests, mirroring the other three
cache-wired adapters (sections 26.4, 27.4, 28.4). When not supplied, the
adapter lazily resolves a shared store via `get_provider_cache_store`
using `Settings.provider_cache_path` -- but only if
`Settings.provider_cache_enabled` is `true`; when `false`, the persistent
cache is skipped completely (every call goes live), even if a
`cache_store` was explicitly injected -- only the pre-existing in-memory
`self._destination_cache` dict still applies per instance. Constructing
`OpenStreetMapPlacesAdapter()` with no arguments remains fully backward
compatible.

### 29.5 Cache consumer summary

As of Step 164E, four real provider adapters are cache consumers: Open-Meteo
(weather), Nager.Date (holidays), Frankfurter (currency), and OpenStreetMap
(geocoding only -- Overpass POI search remains live-only as of this step).
No other provider (routes, transit, accommodation pricing, flights) is
cache-wired, and no LangGraph, Groq, Anthropic, or Kiwi/MCP call is cached
or otherwise touched by any of these four steps. **Step 164G (section 30)
extends OpenStreetMap's own wiring to cover Overpass POI search too.**

### 29.6 Manual live smoke coverage (Step 164F, extended in Step 164H)

`backend/scripts/manual_provider_cache_smoke.py` (manual/dev-only, never
run by CI or pytest; see docs/21_manual_provider_cache_smoke.md) covers
all four cache-wired providers against their real public APIs: Open-Meteo,
Nager.Date, Frankfurter, and OpenStreetMap/Nominatim geocoding (Step 164F)
-- extended in Step 164H to also cover one OSM/Overpass POI search
(`search_attractions`, for one known destination). It still does not call
`search_restaurants`, `search_accommodation_pois`, or
`search_must_visit_place`, and its POI coverage is deliberately structural
only (real Overpass response parses, cache populated/reused, no
rating/price/opening-hours/availability/booking/route-time claim in the
cached payload) -- it is not a claim that every POI, category, or
destination currently works.

---

## 30. OSM/Overpass POI Search Cache Wiring (Step 164G)

`OpenStreetMapPlacesAdapter` extends its Step 164E geocode cache wiring
(section 29) to also cover Overpass POI search -- the `_try_query` method
used by `search_attractions`, `search_restaurants`, and
`search_accommodation_pois` (both the primary query and every individual
fallback tag query). **`search_must_visit_place`'s Nominatim named-place
lookup (`_lookup_named_place`) is not cached by this step** -- it is a
targeted single-place lookup, not an Overpass POI search.

### 30.1 Scope: normalized POI provider responses only

Only normalized `NormalizedPlace` results already produced by the existing
live Overpass path are cached -- nothing new is added to what a place can
carry. No rating, price, opening hours, availability, booking link, or
route time is introduced by caching; those fields were never present on
`NormalizedPlace` before this step and still aren't. A cache hit and a
cache miss return the identical set of fields
(`place_id`, `name`, `category`, `coordinates`, `address`, `source`,
`data_status`, `confidence`) -- caching never fabricates a place, a
coordinate, or an OSM ID.

### 30.2 Cache key

Cache rows are stored under source `"openstreetmap_poi"`, keyed by
`query_hash = make_query_hash(query)` where `query` is the normalized
Overpass request:

```json
{
  "lat": 34.0522,
  "lon": -118.2437,
  "radius_meters": 6000,
  "tags": ["\"historic\"", "\"tourism\"~\"attraction|museum|gallery|viewpoint|artwork|zoo|theme_park\""],
  "limit": 20
}
```

`lat`/`lon` are the already-resolved destination point (itself geocode-
cache-backed, section 29), `radius_meters` distinguishes a primary query
(`_SEARCH_RADIUS_METERS` = 6000) from a fallback tag query
(`_FALLBACK_SEARCH_RADIUS_METERS` = 12000), `tags` is the sorted Overpass
tag filter list (so key order never affects the hash), and `limit` is the
fixed `_MAX_RESULTS` cap. One row is written per individual Overpass query
-- a primary query and each fallback tag query (queried one at a time,
per the existing fallback design) get separate cache entries, so a later
search for the same destination/category can reuse whichever of those
sub-queries it needs. The Overpass query string itself is never persisted
as raw metadata -- only its normalized (point, radius, tags, limit)
shape feeds the hash, and only the opaque digest is stored.

### 30.3 What is cached

Only a query that returns at least one named, destination-contained place
is cached -- an empty result (whether from unnamed-only elements, results
outside containment, or a genuinely POI-free area) and a request failure
are both left uncached, matching the "do not cache unavailable/error
responses" rule already applied to the other three cache-wired providers.
`metadata` is always empty (`{}`); no API key, token, prompt, or raw LLM
response is ever written (Overpass itself requires no API key).

### 30.4 Cache hit/miss behavior

A cache hit returns the exact same normalized `NormalizedPlace` list a
live Overpass query for that (point, radius, tags) would, with each
place's `data_status` relabeled `"cached"`. A cache miss (no row, or an
expired row, treated exactly like a miss) runs the existing live Overpass
HTTP path unchanged, applies the exact same containment filter as before,
then caches the result with TTL `Settings.osm_poi_cache_ttl_seconds`
(`OSM_POI_CACHE_TTL_SECONDS`, default `604800` = 7 days -- shorter than
the 30-day geocode TTL, since POI data changes more often than geocoding
but not every minute).

**The overall `ProviderResponse.status`/`data_status` returned by
`search_attractions`/`search_restaurants`/`search_accommodation_pois` is
unaffected by caching.** That envelope-level status still reflects only
whether fallback was needed (`SUCCESS`/`PARTIAL` vs `FALLBACK_USED`),
exactly as before this step -- only each individual cached place's
`data_status` field differs. Nothing in `CandidateQualityService`,
`ExperiencePlannerService`, `destination_context_service.py`'s
`candidate_pois`/`candidate_restaurants`/`candidate_accommodation_pois`
construction, or `provider_coverage`/`data_sources_used` filters or gates
on an individual place's `data_status`, so this cannot silently change
scheduling, candidate quality scoring, validation, or provider coverage
reporting.

Cache reads and writes are both best-effort and never fail POI search: a
broken cache read is logged and treated as a miss (falls through to the
live Overpass path), and a broken cache write is logged but the
already-computed live result is still returned. Neither log line includes
the query payload.

### 30.5 Dependency injection

`OpenStreetMapPlacesAdapter` reuses the exact same `cache_store`
constructor parameter and `_resolve_cache_store()` lazy-resolution helper
already added for geocoding (section 29.4) -- no new constructor
parameter was needed. When `Settings.provider_cache_enabled` is `false`,
both the geocode cache and the POI cache are skipped completely (every
call goes live), even if a `cache_store` was explicitly injected.

### 30.6 Test isolation fix

Wiring POI search into the same lazily-resolved, process-wide cache
singleton surfaced a latent cross-test contamination risk: any test that
constructs a real provider adapter without an explicit `cache_store` (not
just in `test_openstreetmap_adapter.py`, but anywhere in the suite, e.g.
an API test that monkeypatches `provider_gateway.places` with a real
`OpenStreetMapPlacesAdapter()` to exercise containment logic against a
fake HTTP client) would otherwise share one real cache store across the
whole test session. `backend/app/tests/conftest.py` now has an autouse
`_isolate_provider_cache_store` fixture, mirroring the pre-existing
`_reset_in_memory_repositories` fixture, that points every cache-wired
adapter module's `get_provider_cache_store` at a fresh, throwaway,
per-test store instead.

---

## 31. OSRM Routing Provider Skeleton (Step 165A)

Step 165A adds a backend routing provider **contract and skeleton
adapter** for point-to-point route data (distance, duration) between two
coordinates, matching the `RoutesProvider` concept sketched in section 11
above. **This is a skeleton only, not wired into planning.** As of this
step:

- No file under `backend/app/services/` (including `PlanningOrchestrator`,
  `ExperiencePlannerService`, `PlanValidatorService`) imports or calls
  anything in this subsystem.
- `ProviderGateway` (`backend/app/providers/gateway.py`) is unchanged --
  its `routes` slot still defaults to the generic `RoutesProvider()` base
  interface from `app.providers.base` (always `not_connected`), exactly as
  before this step. The new OSRM subsystem is a separate module tree, the
  same way the AI candidate-proposal subsystem (sections 28-29 of
  docs/13_llm_reasoning_pipeline.md) was built standalone for many steps
  before ever being wired in.
- No `ProviderCoverage`/`provider_status` field changes -- nothing writes
  to either yet.
- No caching -- `ProviderCacheStore` (Step 164A) is not used by this
  subsystem.
- **No fake route time, distance, or geometry is ever produced.** Missing
  or unusable route data is always reported `not_connected`,
  `unavailable`, or `failed` -- never guessed, and never backfilled from
  `haversine_distance_km` (a straight-line estimate used elsewhere in this
  codebase, e.g. `OpenStreetMapPlacesAdapter`'s containment checks, and
  explicitly documented there as "not a route, walking, or travel-time
  distance").

### 31.1 Contract models

`backend/app/models/routing.py` defines the contract, mirroring the
`AICandidateProposalRequest`/`AICandidateProposalResult` pattern
(docs/13_llm_reasoning_pipeline.md section 28) rather than the generic
`ProviderResponse[T]` envelope every other real provider adapter in this
codebase returns -- a routing caller almost always wants exactly one
point-to-point result, not a list, so `status` lives directly on the
result:

```python
class RoutingProfile(str, Enum):
    DRIVING = "driving"
    WALKING = "walking"
    CYCLING = "cycling"

class RouteRequest(BaseModel):
    origin_lat: float       # -90..90, same bounds as GeoPoint
    origin_lon: float       # -180..180
    destination_lat: float
    destination_lon: float
    profile: RoutingProfile = RoutingProfile.DRIVING

class RouteResult(BaseModel):
    provider: str
    status: ProviderStatus  # not_connected | unavailable | failed | success
    distance_meters: float | None = None
    duration_seconds: float | None = None
    geometry: str | None = None   # not populated by this step
    source: str
    confidence: float = 0.0
    message: str | None = None
```

`RouteRequest` takes coordinates only -- never a free-text address; a
caller resolves real coordinates via a `PlacesProvider` first (the same
pattern `WeatherProvider`/`HolidayProvider`/`CurrencyProvider` already
follow), and out-of-range latitude/longitude is rejected by pydantic
validation, matching `GeoPoint`'s own bounds. `distance_meters`/
`duration_seconds` on `RouteResult` are `None` whenever the provider
didn't return a usable value -- never a guess.

### 31.2 Provider boundary and adapters

`backend/app/providers/routing/` follows the `abc.ABC` provider-boundary
pattern already used for `AICandidateProposalProvider` (not the
`app.providers.base` interfaces, which default to an honest
`not_connected` response) -- every concrete adapter must explicitly
implement `get_route`, with no silent default to fall back on:

- `base.py` -- `RoutingProvider(ABC)`, one abstract method, `get_route(request: RouteRequest) -> RouteResult`.
- `not_connected_adapter.py` -- `NotConnectedRoutingProvider`, the default. `get_route` never calls a network service and always returns an honest `not_connected` `RouteResult` -- empty distance/duration, zero confidence.
- `osrm_adapter.py` -- `OSRMRoutingAdapter`, the first real (non-`not_connected`) adapter. Calls an OSRM-compatible route service (`GET {base_url}/route/v1/{profile}/{origin_lon},{origin_lat};{destination_lon},{destination_lat}?overview=false`) via `httpx`.
- `factory.py` -- `get_routing_provider(provider_name=None)`, config-gated selection mirroring `get_ai_candidate_proposal_provider` (docs/13_llm_reasoning_pipeline.md section 38): `"not_connected"` (default) and `"osrm"` are the only supported names; an unsupported/unrecognized name falls back to `NotConnectedRoutingProvider` rather than raising or guessing.

### 31.3 OSRM adapter behavior

- **If `Settings.osrm_base_url` is unset (the default), `get_route` returns
  `not_connected` without making any network call at all** -- no public
  demo or self-hosted OSRM instance is assumed.
- On a real call, only `code == "Ok"` with a non-empty `routes` list is
  treated as usable. `distance_meters`/`duration_seconds` are read
  directly from the first route's own `distance`/`duration` fields (OSRM
  already returns meters/seconds, so no unit conversion is invented) --
  both stay `None`, and the result is `unavailable`, if the route entry
  supplies neither.
- `NoRoute` (or any other non-`"Ok"` `code`), an empty `routes` list, a
  non-dict top-level response, or a non-dict first route entry are all
  reported `unavailable` -- an honest "the provider was reached but had
  nothing usable," never a guess.
- A request-level failure (network error, timeout, non-2xx status, or a
  response body that isn't valid JSON) is reported `failed`.
- `geometry` is never parsed by this step -- the request always asks for
  `overview=false`, so no geometry payload is fetched or discarded.
- No raw route payload is ever logged; a request failure logs only the
  exception, matching the existing pattern in every other real adapter in
  this codebase.

### 31.4 Config

```text
ROUTING_PROVIDER          default "not_connected"; "osrm" also supported
OSRM_BASE_URL              default unset (None) -- conservative by design
OSRM_TIMEOUT_SECONDS       default 15.0
OSRM_PROFILE                default "driving"
```

`osrm_base_url` defaults to `None` rather than a public OSRM demo
URL -- a deliberately conservative choice (mirroring `anthropic_api_key`/
`groq_api_key` defaulting to `None`) so no OSRM instance, public or
self-hosted, is silently used without an explicit developer choice.
`OSRMRoutingAdapter` stays `not_connected` even if `ROUTING_PROVIDER=osrm`
is set but `OSRM_BASE_URL` is not.

---

## 32. Routing Provider Exposed Through ProviderGateway (Step 165B)

Step 165B wires the Step 165A routing provider factory into
`ProviderGateway` (`backend/app/providers/gateway.py`) -- the single
central access point every other provider already goes through
(docs/14_backend_architecture.md section 18). **This is exposure only, not
consumption:** the routing lookup is not called by `PlanningOrchestrator`,
`ExperiencePlannerService`, or `PlanValidatorService`. Scheduling and
validation behavior are completely unchanged by this step.

### 32.1 What changed

- `ProviderGateway.__init__` gained a new optional `routing: RoutingProvider
  | None = None` constructor parameter, defaulting to
  `app.providers.routing.factory.get_routing_provider()` -- the exact same
  factory Step 165A already built and tested. This is additive and
  backward compatible: every existing `ProviderGateway(...)` call site
  (including the pre-existing `routes=` parameter for the unrelated,
  still-unused `app.providers.base.RoutesProvider` stub) is unaffected.
- `ProviderGateway.get_route(request: RouteRequest) -> RouteResult` is a
  new method that delegates entirely to `self.routing.get_route(request)`.
  The gateway adds, guesses, or backfills nothing -- it is a pure
  pass-through, and it never falls back to a straight-line (haversine)
  distance when a route is unavailable.
- With the default configuration (`Settings.routing_provider=
  "not_connected"`), `provider_gateway.get_route(...)` returns an honest
  `not_connected` `RouteResult` -- no network call, no fabricated distance
  or duration -- exactly like calling
  `NotConnectedRoutingProvider().get_route(...)` directly would.

### 32.2 What did not change

- `ProviderGateway.routes` (the pre-existing, generic `RoutesProvider`
  stub attribute) is completely untouched -- still always `not_connected`,
  still unrelated to the new `routing` attribute. The two coexist
  deliberately; a future step may consolidate them, but this step does
  not.
- `ProviderGateway.default_provider_coverage()` and `ProviderCoverage`'s
  existing `routes` field are unchanged -- this step adds no new coverage
  metadata, active or inactive, since nothing in the coverage-tracking
  path (`DestinationContextService`, `StayTransportService`,
  `ExperiencePlannerService`) reads `ProviderGateway.routing`/`get_route`
  at all yet.
- No caching -- `ProviderGateway.get_route` never reads from or writes to
  `ProviderCacheStore`.
- No file under `backend/app/services/` was touched by this step --
  confirmed by dedicated tests that `PlanningOrchestrator`,
  `ExperiencePlannerService`, and `PlanValidatorService`'s source contains
  no reference to `osrm`, `RoutingProvider`, `get_routing_provider`,
  `gateway.routing`, or `gateway.get_route`.

### 32.3 Design notes

The gateway itself never knows an OSRM base URL, timeout, or profile
default -- those stay entirely inside `app.providers.routing.factory`/
`OSRMRoutingAdapter` (section 31), matching the existing pattern where
`ProviderGateway` never knows an Overpass URL, Nominatim URL, or any other
adapter-specific config either. Dependency injection is fully supported
for tests: `ProviderGateway(routing=<any RoutingProvider>)` lets a test
substitute a fake, in-memory `RoutingProvider` and assert the gateway
delegates to it correctly, without needing a real or fake HTTP layer at
all.

## 33. OSRM Route Cache Wiring (Step 165C)

Step 165C wires `OSRMRoutingAdapter` (section 31) to the Step 164A
`ProviderCacheStore` foundation, mirroring the cache-wiring pattern already
used for Open-Meteo (section 27), Nager.Date (section 28), Frankfurter
(section 26), OSM geocoding (section 29), and OSM/Overpass POI search
(section 30). Only `osrm_adapter.py` was touched -- `ProviderGateway`
needed no change, since it already delegates `get_route` straight through
to whatever `RoutingProvider` it holds (section 32).

**Cache key.** The query hash (`make_query_hash`, source label
`"osrm_route"`) is derived only from the normalized route request:
`origin_lat`, `origin_lon`, `destination_lat`, `destination_lon`, and the
*resolved* `profile` string (the request's `profile` if set, else
`Settings.osrm_profile`) -- the exact same values that get sent to OSRM.
`RouteRequest` has no route-options field today, so there is nothing
further to add to the key. The raw coordinate query text and the request
URL are never stored -- only the opaque hash.

**What is cached.** Only a `RouteResult` with `status == SUCCESS` is
cached -- `not_connected`, `unavailable`, `failed`, `NoRoute`, and
malformed responses are never written to the cache. The cached payload
holds only the normalized route fields (`distance_meters`,
`duration_seconds`, `geometry`, `confidence`) -- no API key, prompt, raw
LLM response, or user trip data, and cache metadata is always empty for
this source.

**Cache hit/miss behavior.** A cache hit returns a `RouteResult` with the
identical shape a live OSRM call would produce (only `message` is
relabeled to note it came from cache) -- it never fabricates a route
duration or distance. An expired entry is treated exactly like a miss and
triggers a fresh OSRM call, which then refreshes the cache entry. A broken
cache read falls back to the live OSRM HTTP path rather than failing the
request; a broken cache write still returns the live route result that was
just computed. TTL is `Settings.osrm_route_cache_ttl_seconds` (default
86400 seconds / 24 hours, env var `OSRM_ROUTE_CACHE_TTL_SECONDS`) --
shorter than the geocode TTL since road conditions can shift, but still
configurable.

**Dependency injection.** `OSRMRoutingAdapter.__init__` gained an optional
`cache_store: ProviderCacheStore | None = None` parameter for tests,
exactly like the other cache-wired adapters. When not injected and
`Settings.provider_cache_enabled` is true, the store is lazily resolved
via the process-wide `get_provider_cache_store(settings.resolved_provider_cache_path())`
singleton on first use. When `provider_cache_enabled` is false, the cache
is skipped entirely and every call goes straight to HTTP.

Routing is still not consumed by scheduling or validation as of this step
-- this step only changes how `OSRMRoutingAdapter` itself answers a
repeated route lookup, nothing about when or whether that lookup happens.
(Step 165E, section 35 below, is the step that starts consuming routing
for feasibility reporting -- still not scheduling.)

## 34. Manual Live Smoke Coverage for OSRM Route Cache (Step 165D)

`backend/scripts/manual_provider_cache_smoke.py` (docs/21_manual_provider_cache_smoke.md)
now also covers `OSRMRoutingAdapter` (section 31) and its route cache
(section 33), alongside its existing Open-Meteo/Nager.Date/Frankfurter/OSM
coverage. This is manual-only: never run by pytest, `python -m compileall`,
or CI -- only by a human with `RUN_LIVE_PROVIDER_CACHE_SMOKE=true` set.

The script calls `OSRMRoutingAdapter.get_route` twice for one small, fixed
origin/destination pair near the same known Lisbon destination the script
already uses, against a real OSRM instance (the public OSRM demo server by
default, or `OSRM_BASE_URL` if set), sharing the script's one temporary
`ProviderCacheStore`. It checks only structure: the first call reached OSRM
and returned `status=success` with a positive numeric `distance_meters`
and `duration_seconds`; the second, identical call returned the same
values (proving cache reuse) and a cache row exists under source
`"osrm_route"`. **It never asserts an exact distance, duration, or
geometry** -- the route cache does not fabricate route duration or
distance, cached or live, and this script's job is only to confirm live
OSRM parsing and cache reuse actually work, not to pin a specific route's
values. It does not validate every route, profile, or destination pair,
and a PASS does not mean route data is used in itinerary scheduling or
validation -- it still isn't, as of this step.

## 35. Route Feasibility for Scheduled Experiences (Step 165E)

Step 165E is the first step that consumes real routing data: provider-backed
routing (Step 165B, cache-backed when configured, Step 165C) now feeds
itinerary **feasibility checks** between consecutive scheduled experiences
within each day. **This is not full route-aware scheduling yet** -- that
is Section 166's job. This step never reorders, adds, or drops a scheduled
experience; it only reports, honestly, whether a real route exists between
experiences the `ExperiencePlannerService` (straight-line/haversine
proximity, Step 156C) already scheduled.

`RouteFeasibilityService` (`backend/app/services/route_feasibility_service.py`)
runs inside `PlanningOrchestrator.run_experience_plan_stage`, after
`experience_plan` exists and before `PlanValidatorService` runs. For every
pair of consecutive experiences within the same day, it calls
`ProviderGateway.get_route` (the same Step 165B/165C call path -- cache-backed
OSRM when `Settings.routing_provider="osrm"` and `Settings.osrm_base_url`
are configured, otherwise the default `not_connected` provider) and builds a
`RouteLegFeasibility` (`backend/app/models/routing.py`):

- **Cache-backed OSRM duration/distance is used only when the provider
  returns `RouteResult.status == success`.** A successful leg is reported
  `feasibility_status=feasible` with the provider's real
  `distance_meters`/`duration_seconds` -- never a fabricated value, and
  never a straight-line/haversine estimate presented as route data.
- A leg where either experience is missing coordinates is
  `feasibility_status=unavailable` (`status=unavailable`) without ever
  calling the routing provider for it -- not guessed, not computed some
  other way.
- A leg where the routing provider is `not_connected`/`unavailable`/
  `failed` is `feasibility_status=needs_review`, matching the provider's
  own honest status -- never upgraded to `feasible`.
- If a real schedule time gap between two experiences is known (their
  `end_time`/`start_time`), a successful route whose duration clearly
  exceeds that gap is also `needs_review` -- but no threshold is invented
  when no schedule timestamps exist, which is the case for every plan this
  app currently generates (`ExperienceItem.start_time`/`end_time` stay
  unset, Step 156).

All legs across the plan roll up into one `RouteFeasibilityReport`
(`PlanningState.route_feasibility_report`), whose `status` is an honest
aggregate (`success` only when every leg succeeded, `partial` when some
did, `not_connected`/`failed`/`unavailable` otherwise) and whose
`route_data_source` names the actual provider used, or `"not_connected"`.
This report also updates `ProviderCoverage.routes` honestly -- `"success"`
only when `RouteFeasibilityReport.status == success`, never upgraded
otherwise.

`PlanValidatorService` consumes this report (when present) to replace its
previous blanket "route ordering/timing/feasibility checks are not
implemented yet" warning with a message that names what was actually
found -- but the plan's `readiness_status` never becomes `ready` from this
alone; route timing, opening-hours, and full route-aware scheduling remain
Section 166's job.

## 36. Route-Aware Sequencing Suggestions, Shadow-Mode Only (Step 166A)

Step 166A is the first Section 166 step, and it starts conservatively:
the same OSRM route data Step 165E feeds into feasibility checks can now
also support a **shadow-only** day-sequencing suggestion -- it does not
yet change scheduling.

`RouteAwareSequencingService`
(`backend/app/services/route_aware_sequencing_service.py`) runs inside
`PlanningOrchestrator.run_experience_plan_stage`, immediately after
`RouteFeasibilityService` (section 35) and before `PlanValidatorService`.
For each scheduled day with two or more experiences, it calls
`ProviderGateway.get_route` (the same Step 165B/165C call path) between
every pair of that day's coordinate-backed experiences, then:

- Sums the real route duration along the day's *current* scheduled order
  to get an "original" total, and separately computes a candidate
  reordering using a conservative nearest-next-by-real-route-duration
  walk.
- Reports the candidate order's own total duration/distance, and the
  difference against the original total, in a `RouteAwareSequenceSuggestion`
  (`backend/app/models/routing.py`) -- but **no fake route time is ever
  produced**: these totals are populated only when every route lookup they
  depend on returned `RouteResult.status == success`. A straight-line
  (haversine) distance is never substituted for a real route
  duration/distance anywhere in this step.
- A day with fewer than two coordinate-backed experiences is
  `unavailable` without the routing provider ever being called for it. A
  day where some, but not all, needed route lookups succeeded (or some,
  but not all, scheduled experiences are missing coordinates) is
  `partial`, with no duration/distance total reported.

All of this rolls up into `PlanningState.route_aware_sequencing_report`
(`RouteAwareSequencingReport`), which is **shadow/report-only**:
`is_shadow_only=True` and `applied_to_itinerary=False`, always. This step
never reorders, adds, or drops a scheduled experience --
`ExperiencePlannerService`'s straight-line/haversine scheduling (Step
156C) is completely untouched, and the suggested order is not fed back
into scheduling, `PlanValidatorService`, or `ProviderCoverage`. With the
default `Settings.routing_provider="not_connected"`, every day (and the
report as a whole) honestly reports `not_connected` with no network call,
exactly like `route_feasibility_report`. Full route-aware scheduling that
actually changes itinerary order remains a later Section 166 step, not
this one.

## 37. Config-Gated Route-Aware Scheduling Application (Step 166B)

Step 166B lets the same OSRM-backed route data optionally affect
scheduling order -- but **only when explicitly enabled**, via
`Settings.route_aware_scheduling_enabled` (default `False`,
`ROUTE_AWARE_SCHEDULING_ENABLED`). With the default off,
`PlanningOrchestrator` never calls
`RouteAwareSequencingService.apply_report`, and the scheduled itinerary
order stays exactly as `ExperiencePlannerService` left it -- identical to
every step before 166B.

When enabled, `apply_report` only ever reorders a day whose suggestion is
provider-backed and `success` (never `partial`/`unavailable`/
`not_connected`/`failed`), whose real improvement exceeds
`Settings.route_aware_scheduling_min_improvement_seconds` (default
`0.0`), and whose `suggested_order` is verified to be an exact
permutation of that day's real, current scheduled experience IDs. **No
fake route time is ever produced** by this application path either --
`apply_report` makes no new provider call of its own; it only ever acts
on duration/distance/improvement figures Step 166A's `build_report`
already computed from real, successful `RouteResult`s, and it never
substitutes a straight-line (haversine) estimate for a route duration. No
experience is ever added, removed, or duplicated, and no experience field
other than schedule order is ever changed.

If a reorder happens, `PlanningOrchestrator` recomputes
`route_feasibility_report`/`ProviderCoverage.routes` against the new
order immediately afterward, so route feasibility never goes stale
relative to the schedule it describes.

## 38. Provider-Backed Travel-Time Buffer Reporting (Step 166C)

OSRM-backed route durations (Step 165B, cache-backed when configured,
Step 165C) can now also support travel-time buffer reporting between
consecutive scheduled experiences, via `TravelTimeBufferService`
(`backend/app/services/travel_time_buffer_service.py`), run right after
`RouteFeasibilityService` and any Step 166B route-aware-scheduling
application.

**No fake buffer or route duration is ever produced.** A `TravelTimeBuffer`'s
`recommended_buffer_seconds` is only ever an exact restatement of a real,
successful `RouteResult.duration_seconds` -- never an invented padding
percentage, safety margin, or straight-line (haversine) estimate. A leg
missing a coordinate is `not_computable` without the routing provider
ever being called for it; a leg whose provider call is `not_connected`/
`unavailable`/`failed` mirrors that status exactly, with no duration,
distance, or buffer populated. `available_gap_seconds` (the real gap
between a schedule's `end_time`/`start_time`) is only ever a real, parsed
value -- `None` whenever no such timestamp exists, which is the case for
every plan this app currently generates, so sufficiency
(`buffer_status`) is honestly left `not_computable` rather than guessed.

This never reorders or drops a scheduled experience, and it does not
change `Settings.route_aware_scheduling_enabled`'s default (`False`) or
`RouteAwareSequencingService.apply_report`'s own safety contract.

## 39. Routing Provider Fallback Hardening (Step 166D)

Step 166D hardens how every consumer of `ProviderGateway.get_route`
(`RouteFeasibilityService`, `RouteAwareSequencingService`,
`TravelTimeBufferService`) reacts when routing is unavailable, partial,
or fails -- **unavailable and not-connected route data is never
converted into a fake duration or distance.**

Every real routing adapter (e.g. the OSRM adapter, Step 165A) already
converts its own failure modes -- no base URL configured, a network
error, a timeout, a malformed response -- into an honest
`RouteResult(status=not_connected/unavailable/failed)` without raising.
This step adds a second line of defense: each of the three services
wraps its own `get_route` call in a self-contained `_safe_get_route`
helper, so a genuinely unexpected exception (a bug, not a normal
provider failure mode) is also converted into an honest `status=failed`
result with a generic, safe message -- never the raw exception text,
never a provider payload, and never a straight-line/haversine estimate
substituted in its place. `PlanningOrchestrator` adds one more layer on
top of that, storing an honest empty `status=failed` report if a whole
`build_report`/`apply_report` call still raises unexpectedly, so
`/generate` never fails just because routing did.

Missing coordinates remain `not_computable`/`unavailable` (the routing
provider is never even called for that leg); a not-connected routing
provider remains `not_connected`/`unavailable` (no network call is ever
made); partial route data remains `partial` and is never applied to
itinerary order -- `RouteAwareSequencingService.apply_report` only ever
applies a suggestion whose `status == success`, which by construction
requires every route lookup that suggestion depends on to have
succeeded.

## 40. Movement-Data Provenance (Step 166E)

Step 166E, the final Section 166 step, adds a shared
`MovementDataProvenance` enum (`backend/app/models/routing.py`) so every
route-dependent model in this subsystem carries one explicit,
cross-cutting label describing where its movement data actually came
from -- alongside, never instead of, its own existing status field:

- **`provider_backed`**: a real, successful `RouteResult` exists, and any
  duration/distance/buffer/reorder shown came directly from it.
- **`not_connected`**: no routing provider is configured.
- **`unavailable`**: the provider responded but returned no usable route
  (or a mixed/`partial` result).
- **`not_computable`**: a required input (most commonly, missing
  coordinates) meant the provider was never even called.
- **`failed`**: the request failed, or an unexpected exception was
  safely contained (Step 166D).
- **`not_applied`**: (route-aware sequencing suggestions only) real,
  provider-backed data may exist, but it was never applied to the actual
  schedule -- e.g. because `Settings.route_aware_scheduling_enabled` is
  `False` (the default).

**No fake route duration, distance, buffer, or feasibility is ever
created to satisfy this labeling.** The provenance value is always
derived *from* an already-computed status (via
`movement_data_provenance_from_status`/`route_aware_suggestion_provenance`),
never the other way around -- provider-backed vs. unavailable movement
data is reported exactly as honestly as it already was before this
step; this step only makes that distinction explicit and consistently
labeled across `RouteFeasibilityReport`, `RouteAwareSequencingReport`,
and `TravelTimeBufferReport` (and their legs/suggestions/buffers).

---

## 41. Accommodation Provider Foundation (Step 167A)

Step 167A adds a backend accommodation inventory provider **contract
and model foundation only**, matching the `AccommodationProvider`
concept sketched in section 13 above, and mirroring how the OSRM
routing subsystem started as a standalone contract before ever being
wired in (section 31). **This is a contract only, not wired into
planning.** As of this step:

- No file under `backend/app/services/` (including
  `PlanningOrchestrator`, `StayTransportService`, `PlanValidatorService`)
  imports or calls anything in this subsystem.
- `ProviderGateway` (`backend/app/providers/gateway.py`) is unchanged --
  its `accommodation` slot still defaults to the pre-existing
  `AccommodationProvider()` base interface from `app.providers.base`
  (always `not_connected`), exactly as before this step.
- No `ProviderCoverage`/`provider_status` field changes -- nothing writes
  to either yet.
- No caching -- `ProviderCacheStore` (Step 164A) is not used by this
  subsystem.
- No real Booking.com, Expedia, Hotelbeds, Hostelworld, Amadeus, Vrbo, or
  Airbnb integration is added. No live lodging API is called. No
  scraping.
- **No fake hotel, room, nightly/total price, availability, rating,
  amenity, cancellation policy, or booking link is ever produced.**
  Missing or unusable inventory is always reported `not_connected`,
  `unavailable`, or `failed` with an empty offer list -- never guessed.

### 41.1 Bookable inventory vs. OSM accommodation POIs

This contract is deliberately a separate concept from
`DestinationContext.candidate_accommodation_pois` (open-data OSM
location candidates, section 10 above, and
`planning_state.AccommodationSuggestion`) and from the existing
day-level `AccommodationSuggestion`/plan-level `StayAreaGuidance`
built from those POIs. An OSM accommodation POI is a real place that
exists on the map, but it carries no price, no availability window, no
rating, and no booking link -- and this step does not change that. The
new `AccommodationOffer` model (bookable, provider-backed lodging
inventory) and the existing OSM-backed accommodation POI candidates
must never be merged or presented as the same kind of data.

### 41.2 Contract models

`backend/app/models/accommodation.py` defines the contract:

```python
class AccommodationAvailabilityStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    NOT_CONNECTED = "not_connected"

class AccommodationSearchStatus(str, Enum):
    SUCCESS = "success"
    NOT_CONNECTED = "not_connected"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"

class AccommodationSearchRequest(BaseModel):
    destination: str
    check_in_date: date
    check_out_date: date        # must be after check_in_date
    adults: int                 # > 0
    children: int | None = None # >= 0 if present
    rooms: int                  # > 0
    currency: str | None = None
    latitude: float | None = None   # -90..90, same bounds as GeoPoint
    longitude: float | None = None  # -180..180
    radius_meters: float | None = None

class AccommodationOffer(BaseModel):
    provider: str
    provider_property_id: str
    property_name: str
    latitude: float | None = None
    longitude: float | None = None
    address: str | None = None
    nightly_price_amount: float | None = None  # >= 0 if present
    total_price_amount: float | None = None    # >= 0 if present
    currency: str | None = None
    availability_status: AccommodationAvailabilityStatus = UNKNOWN
    booking_url: str | None = None
    rating: float | None = None   # >= 0 if present, no scale enforced
    amenities: list[str] = []
    cancellation_policy: str | None = None
    data_status: DataStatus
    source_name: str | None = None
    source_url: str | None = None
    fetched_at: datetime

class AccommodationSearchResult(BaseModel):
    provider: str
    status: AccommodationSearchStatus
    offers: list[AccommodationOffer] = []  # must be empty unless status == success
    message: str | None = None
    generated_at: datetime
```

`AccommodationSearchRequest` rejects `check_out_date <= check_in_date`,
non-positive `adults`/`rooms`, negative `children`, and out-of-range
coordinates via pydantic validation. `AccommodationOffer` rejects
negative price/rating fields, and every optional fact field (`rating`,
`nightly_price_amount`, `total_price_amount`, `booking_url`,
`amenities`, `cancellation_policy`) stays at its honest empty/`None`
default unless a real adapter sets it -- no default is ever a
plausible-looking fake value. `AccommodationSearchResult` enforces that
`offers` can only be non-empty when `status == success`, so a
`not_connected`/`unavailable`/`failed` result can never carry a
fabricated or stale offer.

### 41.3 Provider boundary

`backend/app/providers/accommodation/` follows the same `abc.ABC`
provider-boundary pattern already used for `RoutingProvider` (section
31.2) -- not the `app.providers.base` interfaces, which default to an
honest `not_connected` response -- so a concrete adapter must
explicitly implement `search_accommodations`, with no silent default
to fall back on:

- `base.py` -- `AccommodationInventoryProvider(ABC)`, one abstract
  method, `search_accommodations(request: AccommodationSearchRequest)
  -> AccommodationSearchResult`.
- `__init__.py` -- re-exports `AccommodationInventoryProvider`.

No concrete adapter (not_connected default, OSM-backed, or a real
lodging provider) is added by this step -- that is future work, the
same way `NotConnectedRoutingProvider`/`OSRMRoutingAdapter` came one
step after the routing contract (Step 165A vs. this one).

`AccommodationInventoryProvider` is deliberately named differently
from the pre-existing `app.providers.base.AccommodationProvider` stub
interface used by `ProviderGateway`'s `accommodation` slot -- the same
way `RoutingProvider` is kept distinct from the pre-existing
`RoutesProvider` stub (section 31). The two are unrelated contracts
today; this step does not replace, deprecate, or wire either into the
other.

## 42. Accommodation Not-Connected Provider and Factory (Step 167B)

Step 167B adds the first concrete `AccommodationInventoryProvider`
implementation and a config-gated factory to select it, mirroring how
the OSRM routing subsystem added `NotConnectedRoutingProvider` and
`get_routing_provider` one step after its own contract (section 31,
Step 165A). **This is still provider infrastructure only, not wired
into planning:**

- `backend/app/providers/accommodation/not_connected_adapter.py` --
  `NotConnectedAccommodationProvider(AccommodationInventoryProvider)`.
  `search_accommodations` is deterministic: for any request it always
  returns `AccommodationSearchResult(provider="accommodation_inventory_
  provider", status=NOT_CONNECTED, offers=[], message="Accommodation
  inventory provider is not connected.")`. It never calls a network
  service, never inspects `request` beyond accepting it, and never
  invents a property, price, availability, rating, amenity,
  cancellation policy, or booking link.
- `backend/app/providers/accommodation/factory.py` --
  `get_accommodation_provider(provider_name: str | None = None)`
  resolves `provider_name`, or `Settings.accommodation_provider`
  (default `"not_connected"`) when omitted, against a small supported-
  provider map. Today that map has one entry, `"not_connected"` ->
  `NotConnectedAccommodationProvider`. Any unsupported/unrecognized
  name -- including an empty string or a real lodging brand name --
  falls back to the same `NotConnectedAccommodationProvider`, never
  raises, and never fabricates inventory.
- `Settings.accommodation_provider` (alias `ACCOMMODATION_PROVIDER`,
  default `"not_connected"`) is the only new config field. No timeout
  or cache-TTL field is added -- unlike `osrm_timeout_seconds`/
  `osrm_route_cache_ttl_seconds`, there is no concrete networked
  adapter yet for such settings to govern.
- **Default lodging inventory status remains `not_connected`.**
  Constructing `get_accommodation_provider()` with no configuration,
  or with an unrecognized `ACCOMMODATION_PROVIDER` value, never makes a
  network call and never creates fake lodging data.
- `ProviderGateway` and `PlanningOrchestrator` are both untouched by
  this step -- neither imports `app.providers.accommodation` nor
  references `get_accommodation_provider`/`AccommodationInventoryProvider`.
  `ProviderGateway.accommodation` still defaults to the pre-existing,
  always-`not_connected` `app.providers.base.AccommodationProvider()`
  stub, exactly as in Step 167A.
- This factory/adapter pair is separate from, and must never be
  conflated with, `DestinationContext.candidate_accommodation_pois`
  (OSM-backed location candidates, section 41.1) -- an OSM
  accommodation POI is a real place, never a bookable offer, and this
  step does not change that boundary.

## 43. Accommodation Lookup Exposed Through ProviderGateway (Step 167C)

Step 167C wires the Step 167B factory into `ProviderGateway`, exposing
accommodation inventory lookup through a single gateway method the same
way routing lookup was exposed one step after its own factory (Step
165A -> 165B, section 31/42 pattern):

- `ProviderGateway.__init__` gains a new optional
  `accommodation_inventory: AccommodationInventoryProvider | None`
  constructor parameter, defaulting to
  `app.providers.accommodation.factory.get_accommodation_provider()`
  when omitted -- which itself resolves `Settings.accommodation_provider`
  (`"not_connected"` at the time this step was written; Step 168F later
  changed the default to `"scraped_local"`, section 51). Existing
  `ProviderGateway()` construction with no arguments continues to work
  unchanged.
- `ProviderGateway.search_accommodations(request:
  AccommodationSearchRequest) -> AccommodationSearchResult` delegates
  entirely to `self.accommodation_inventory.search_accommodations`. The
  gateway itself adds, guesses, or backfills nothing -- no property,
  price, availability, rating, amenity, cancellation policy, or booking
  link -- and never inspects `request.destination` to invent anything.
- **Default remains `not_connected`.** With no explicit
  `accommodation_inventory=` injected and no `ACCOMMODATION_PROVIDER`
  configured, `search_accommodations` returns an honest `not_connected`
  result with an empty `offers` list and makes no network call.
- The new `accommodation_inventory` slot is kept separate from the
  pre-existing `accommodation` slot (the generic, always-`not_connected`
  `app.providers.base.AccommodationProvider` stub) -- mirroring how
  `routing` was kept distinct from `routes`. Neither slot is removed or
  merged into the other.
- **Still not consumed downstream.** `PlanningOrchestrator`,
  `StayTransportService`, and `PlanValidatorService` do not call
  `search_accommodations` or reference `accommodation_inventory` --
  itinerary scheduling, validation, and `ProviderCoverage` reporting are
  all unchanged by this step. `ProviderGateway.default_provider_coverage()`
  still reports `accommodations: "not_connected"` exactly as before.
- No real Booking.com/Expedia/Hotelbeds/Hostelworld/Amadeus/Vrbo/Airbnb
  integration is added, no live lodging API is called, and no scraping
  occurs. OSM accommodation-like POIs
  (`DestinationContext.candidate_accommodation_pois`, section 41.1)
  remain a wholly separate, non-bookable concept from what this gateway
  method returns.

## 44. Accommodation Inventory Report, Coverage, and Validation Clarity (Step 167D)

Step 167D consumes the Step 167C gateway method for the first time,
producing an honest bookable-accommodation-inventory report during
planning -- **the accommodation lookup is now called by
`PlanningOrchestrator`, not just exposed on the gateway.** Default
behavior is unchanged: `not_connected` with an empty offer list, no
live lodging provider connected, and no network call.

- `PlanningState.accommodation_inventory_report: AccommodationSearchResult
  | None` (new field) stores the result. Stays `None` until
  `PlanningOrchestrator.run_stay_transport_stage` has run.
- `backend/app/services/accommodation_inventory_service.py`
  (`AccommodationInventoryService.build_report`) builds an
  `AccommodationSearchRequest` from the trip's own `TripRequest` fields
  (destination, dates, traveler count, currency) and calls
  `ProviderGateway.search_accommodations`. It never reads
  `destination_context` at all, so
  `DestinationContext.candidate_accommodation_pois` (OSM-backed
  location candidates) can never leak into a bookable offer. A trip
  whose dates don't span at least one night is reported `unavailable`
  without ever calling the provider with an invalid request. An
  unexpected exception from the gateway call is caught and converted
  to an honest `failed` result -- never raw exception text or a
  provider payload, and never a fabricated offer.
- `PlanningOrchestrator.run_stay_transport_stage` calls this service
  right after `StayTransportService.run` (Step 166D-style
  exception-hardened: a `try`/`except` around the call always stores a
  safe `failed` result rather than letting an unexpected exception
  crash generation) and stores the result on `PlanningState`.
- **Provider coverage**: the result's status maps onto the existing
  `ProviderCoverage.hotel_prices` field (not `accommodations`) --
  `accommodations` already carries the OSM-backed accommodation-
  location-candidate coverage value set by `StayTransportService`/
  `DestinationContextService` via `ProviderCoverageService`, and this
  bookable-inventory result must never be conflated with that. Mapping:
  `not_connected` -> `"not_connected"`, `failed` -> `"failed"`,
  `unavailable` -> `"unavailable"`, and `success` -> `"success"` only
  when the result actually carries at least one real offer -- a
  `success` result with zero offers is reported `"unavailable"`
  instead, never upgraded to imply bookable inventory exists when it
  doesn't.
- **Validation clarity**: `PlanValidatorService` adds one
  `category="accommodation_inventory"` `WARNING` (never a critical
  issue) explaining the current lodging inventory status in plain
  terms -- explicitly saying price/availability/rating/booking-link
  data "could not be checked" when not connected, rather than implying
  it was checked. This never blocks generation by itself and never
  affects `readiness_status` beyond the existing warning-based
  `needs_review` path.
- No lodging is ever scheduled into the itinerary and no hotel
  recommendation logic is added by this step -- `StayTransportDecision.
  accommodation_recommendations` is untouched.

## 45. Section 167 Summary: Accommodation Inventory Foundation (Steps 167A-167E)

Section 167 adds a complete, honest, end-to-end accommodation inventory
foundation, from contract to frontend wording, without connecting any
real lodging provider:

- **167A** -- Contract models (`backend/app/models/accommodation.py`:
  `AccommodationSearchRequest`/`AccommodationOffer`/
  `AccommodationSearchResult`) and the `AccommodationInventoryProvider`
  `abc.ABC` interface (`backend/app/providers/accommodation/base.py`).
- **167B** -- `NotConnectedAccommodationProvider` (the original default
  implementation) and `get_accommodation_provider` (the config-gated
  factory, `Settings.accommodation_provider`, default `"not_connected"`
  at the time -- later changed to `"scraped_local"` by Step 168F,
  section 51).
- **167C** -- `ProviderGateway.search_accommodations` +
  `accommodation_inventory` constructor slot, exposing the lookup the
  same way `get_route`/`routing` expose routing (section 31/42).
- **167D** -- `AccommodationInventoryService`, called by
  `PlanningOrchestrator.run_stay_transport_stage`, storing
  `PlanningState.accommodation_inventory_report`, mapping its status
  onto `ProviderCoverage.hotel_prices` (kept separate from the
  OSM-backed `accommodations` field), and a non-blocking
  `PlanValidatorService` warning.
- **167E** -- Frontend wording (`AccommodationInventorySection` in
  `frontend/app/page.tsx`, `frontend/lib/types.ts`'s
  `AccommodationInventoryReport`/`AccommodationOffer`) making the
  bookable-inventory-vs-open-data-location-candidate distinction visible
  to the user, plus this final cross-step summary
  (docs/16_frontend_architecture.md section 39).

Throughout every step: **no real Booking.com/Expedia/Hotelbeds/
Hostelworld/Amadeus/Vrbo/Airbnb integration exists, no live lodging API
is ever called, and no scraping occurs.** Default behavior end-to-end is
`not_connected` with an empty `offers` list, `hotel_prices: "not_connected"`
provider coverage, and an honest, non-blocking validation warning. OSM
accommodation-like location candidates
(`DestinationContext.candidate_accommodation_pois`,
`AccommodationSuggestion`, `StayAreaGuidance`) remain, end to end, a
wholly separate, non-bookable concept from `AccommodationOffer` -- no
step in Section 167 ever merges the two or converts one into the other.

## 46. Scraping Policy, Source Registry, and Provenance Foundation (Step 168A)

Section 168 begins a separate, standalone contract for scraping missing
travel data from explicitly-approved public pages -- the same
contract-before-adapter pattern the accommodation subsystem started with
(Section 167). **This step adds no live scraper, parses no real HTML,
and calls no website.** `backend/app/models/scraping.py` defines:

- `ScrapingSourceType` (`str, Enum`) -- one member today,
  `SCRAPED_PUBLIC_PAGE`.
- `ScrapingSourcePolicy` -- one explicitly-approved (or not-yet-approved)
  source: `source_id`/`source_name`/`base_url`, `enabled` (default
  `False`), `approved_for_personal_use` (default `False`),
  `allows_lodging`/`allows_restaurants`/`allows_attractions` (each
  default `False`), `requires_login`/`paywalled`/`captcha_expected`
  (each default `False`), a required positive `rate_limit_seconds`, and
  optional `notes`.
- `ScrapedDataConfidence` (`experimental`/`fragile`) and
  `ScrapingExtractionMethod` (`static_html_parser`/`manual_local_scraper`/
  `unknown`).
- `ScrapedDataProvenance` -- provenance metadata a future scraped item
  would carry: `source_id`/`source_name`, `source_type`/`provenance`
  (both fixed to `scraped_public_page`), `confidence`
  (`experimental`/`fragile`, required), `fetched_at`, `parser_version`,
  `source_url`, `extraction_method`, and `official_provider` (typed
  `Literal[False]` -- pydantic itself rejects `True`, so no scraped item
  can ever be constructed claiming to be official-provider data).
- `ScrapingSourceRegistry` -- an in-memory holder of approved
  `ScrapingSourcePolicy` entries. `active_sources()` returns only sources
  that are both explicitly `enabled=True` *and* safe -- re-checked
  defensively even though `ScrapingSourcePolicy` itself already refuses
  to validate as `enabled=True` while unsafe. `default_scraping_source_
  registry` starts **empty** -- no source is approved for scraping out of
  the box, and this step registers none.

**Scraping config, introduced here as off by default, was later switched
to on by default in Step 168F** (section 51) -- `Settings.
scraping_enabled` and `Settings.scraped_accommodation_provider_enabled`
now both default `True`. `Settings.scraping_default_rate_limit_seconds`
(default `10`, not read by any code path yet) is unaffected. None of
these, nor anything in `backend/app/models/scraping.py`, is imported or
referenced by `ProviderGateway` or `PlanningOrchestrator` yet -- see
section 51 for what actually changed and why default-on still never
fabricates data.

**Policy rules enforced structurally, not just documented:** no
login-required page, no paywalled page, no captcha bypass, no
bot-detection bypass, no aggressive crawling (a source without a
positive `rate_limit_seconds` cannot be constructed at all), and no
source can be `enabled` unless the user has explicitly set
`approved_for_personal_use=True` on it. **Scraped data is never treated
as official-provider data** (`official_provider` can only ever be
`False`) and must be labeled `scraped_public_page`/`experimental`/
`fragile` -- there is no path to a "verified" or "official" scraped
result. No price, rating, availability, amenity, cancellation policy,
opening-hours, route-time, or safety-claim field exists on either model
in this step, so there is nothing here for a default to fabricate; a
future scraped-item model would need every such field to stay `None`/
empty unless a real, approved scrape actually returned it, exactly like
`AccommodationOffer`'s existing honesty contract (section 41.2).

## 47. Static HTML Parser Framework for Scraped Accommodation Data (Step 168B)

Step 168B adds `backend/app/providers/accommodation/scraped_parser.py`'s
`parse_scraped_accommodation_html(html, source_policy, request,
source_url, parser_version) -> AccommodationSearchResult`. **This
function only transforms an HTML string the caller already has -- it
never fetches a URL, opens a socket, or drives a browser, and it does
not fetch any live website.** No source-specific crawling exists; the
expected "property card" HTML micro-format is a fixed, documented,
generic shape used only by this module's own test fixtures, not a real
site's markup.

- **Safety refusal happens before any HTML is touched.** Parsing refuses
  (returns `status=not_connected`, `offers=[]`) when `source_policy` is
  unsafe (`is_unsafe`, section 46), not `enabled`, or
  `allows_lodging=False`. A source that is disabled-and-unsafe (e.g.
  `requires_login=True` with `enabled=False`, which `ScrapingSourcePolicy`
  itself permits to exist) is still refused by the parser as a second
  line of defense, mirroring `ScrapingSourceRegistry.active_sources`'s own
  defensive re-check (section 46).
- **Uses only the Python standard library** (`html.parser.HTMLParser`) --
  no BeautifulSoup, no `httpx`/`requests` call, no browser automation. No
  new dependency was added.
- Every parsed `AccommodationOffer` gets `data_status =
  DataStatus.SCRAPED_PUBLIC_PAGE` (a new `DataStatus` member, common.py)
  and a `scraped_provenance` (`ScrapedDataProvenance`, confidence
  `experimental`, extraction method `static_html_parser`) --
  `AccommodationOffer` now structurally rejects setting
  `scraped_provenance` alongside any other `data_status`, so a scraped
  offer can never be presented under an official-looking status.
  `scraped_provenance.official_provider` stays `Literal[False]` (section
  46).
- **Only fields actually present in the HTML are populated.** A missing
  price/rating/availability/booking-url/address/amenity/cancellation-
  policy stays `None`/`unknown`/empty -- never guessed, estimated, or
  backfilled. A property card missing an identifiable id or name is
  skipped entirely rather than given a fabricated one.
- `status=unavailable` with empty `offers` when the HTML is valid but no
  property card is found; `status=failed` with a safe, generic message
  (never a raw exception/traceback) when parsing or normalizing a card
  fails unexpectedly -- there is no fallback placeholder offer in either
  case.
- **Not wired into `ProviderGateway` or `PlanningOrchestrator` yet** --
  confirmed by dedicated source-inspection tests. No real
  Booking.com/Expedia/Hotelbeds/Hostelworld/Amadeus/Vrbo/Airbnb
  integration exists, and no login-required, paywalled, or
  captcha-protected page is ever parsed.

## 48. Config-Gated Local/Manual Scraped Accommodation Provider (Step 168C)

Step 168C adds `ScrapedAccommodationProvider`
(`backend/app/providers/accommodation/scraped_adapter.py`), the first
concrete `AccommodationInventoryProvider` to use the Step 168B parser.
**It reads only a manually-supplied local HTML file -- it never fetches
a live website, never opens a socket, never drives a browser, and is
not a real Booking.com/Expedia/Hotelbeds/Hostelworld/Amadeus/Vrbo/Airbnb
integration.** Originally gated off by default; **Step 168F (section
51) flipped `Settings.scraping_enabled`/`scraped_accommodation_
provider_enabled`/`accommodation_provider` to select this provider by
default** -- see that section for what changed and why it still never
fabricates data.

Three independent gates must line up before this provider does anything
but return `not_connected` (all three are satisfied by default as of
Step 168F -- see section 51):

1. `Settings.scraping_enabled = True`
2. `Settings.scraped_accommodation_provider_enabled = True`
3. `Settings.accommodation_provider = "scraped_local"` (selected through
   `get_accommodation_provider`, `backend/app/providers/accommodation/
   factory.py`)

Additionally, `Settings.scraped_accommodation_html_path` must point at a
file that actually exists, or the provider returns `unavailable` (never
`not_connected`, since the app-level gates *are* satisfied at that
point -- only the local file is missing) with an empty `offers` list.
This is now the actual out-of-the-box default behavior (Step 168F): the
default path (`.data/manual_scrapes/accommodations.html`, resolved
against the backend project root) is not created automatically, so a
fresh checkout reports `unavailable` until a file is manually placed
there. Any unexpected read/parse failure (e.g. a malformed
`data-currency` value the parser's own validation rejects) is caught
and reported `failed` with a safe message -- never a raw exception, and
never a fallback/placeholder offer.

When all three gates are satisfied and the file exists, the provider
builds a `ScrapingSourcePolicy` (`enabled=True`,
`approved_for_personal_use=True`, `allows_lodging=True`,
`requires_login=False`, `paywalled=False`, `captcha_expected=False`,
`rate_limit_seconds` from `Settings.scraping_default_rate_limit_seconds`)
and passes the file's contents straight to
`parse_scraped_accommodation_html` (section 47) -- unchanged from that
function's own behavior. Every resulting offer carries `data_status =
DataStatus.SCRAPED_PUBLIC_PAGE` and a `scraped_provenance` with
`confidence: experimental` and `official_provider: False` (section 46);
missing fields (price, rating, availability, booking link, amenities,
cancellation policy) stay honestly `None`/`unknown`/empty exactly as the
parser left them -- this adapter adds, guesses, or backfills nothing.

At the time this step was written, `get_accommodation_provider(
"scraped_local")` (or `Settings.accommodation_provider="scraped_local"`)
was the only way to select this provider, with the config default still
`"not_connected"` (falling back to `NotConnectedAccommodationProvider`).
**Step 168F (section 51) changed the default to `"scraped_local"`
itself** -- an unrecognized `accommodation_provider` value, or
explicitly setting it back to `"not_connected"`, still falls back to
`NotConnectedAccommodationProvider`. `AccommodationInventoryService`/
`ProviderGateway` always just delegate to whatever provider they're
given -- as of Step 168F that default provider is
`ScrapedAccommodationProvider`.

## 49. Scraped Accommodation Cache, Rate-Limit Guard, and Provenance Hardening (Step 168D)

Step 168D hardens `ScrapedAccommodationProvider` (section 48) with a
cache and adds a standalone rate-limit guard foundation for a future
live scraper -- **still no live HTTP fetching anywhere in this
codebase.**

- **Cache reuses `ProviderCacheStore`** (the same SQLite-backed store
  real adapters use, "Provider Cache Foundation" section), under source
  `"scraped_accommodation"`. It **stores only the normalized
  `AccommodationSearchResult` payload** (`model_dump(mode="json")`) --
  never the raw HTML file content, matching the "cache must not store
  raw full HTML" rule. Gated by a new, independent
  `Settings.scraped_accommodation_cache_enabled` (default `True` --
  caching never causes a network call or changes returned data, only
  how often the local file is re-read) and `Settings.
  scraped_accommodation_cache_ttl_seconds` (default `3600`).
- **Cache key** (`make_query_hash`) includes: `source_id`, `base_url`,
  `destination`, `check_in_date`/`check_out_date`, `adults`, `children`,
  `rooms`, `currency`, `parser_version`, and the local file's own
  `path`/`mtime_ns`/`size` -- never a secret, never the raw HTML. Because
  the file's mtime/size are part of the key, **editing the local HTML
  file is never served stale cached data**: a changed file simply misses
  the old entry and gets reparsed, satisfying the "stale cache must not
  silently override a changed file" rule.
- **Only a `success` result is ever cached.** `not_connected`/
  `unavailable`/`failed` are never written to cache, so a transient
  failure or a not-yet-configured path is always re-checked next call,
  never "stuck" as a cached failure.
- **Cache reads/writes never fail the provider**: a broken read falls
  back to re-parsing the file, and a broken write still returns the
  freshly-parsed result -- mirroring `OpenMeteoWeatherAdapter`'s own
  cache-failure handling.
- **Provenance survives the cache round-trip unchanged.** A cache hit
  reconstructs the exact same `AccommodationSearchResult` the original
  parse produced (via `AccommodationSearchResult.model_validate` on the
  stored JSON) -- every offer keeps `data_status =
  DataStatus.SCRAPED_PUBLIC_PAGE` and its full `scraped_provenance`
  (`source_id`/`source_name`/`source_url`/`parser_version`/`confidence`/
  `official_provider=False`) exactly as parsed, never relabeled to imply
  fresher or more official data than it is. This is enforced structurally,
  not just by convention: `AccommodationOffer`'s own validator (section
  47) already rejects `scraped_provenance` paired with any `data_status`
  other than `scraped_public_page`, so a cached offer literally cannot
  deserialize into anything that looks official-provider-backed.
- **`ScrapingRateLimitGuard`** (`backend/app/models/scraping.py`) is a
  new, standalone, deterministic in-process guard that enforces a
  minimum gap between two attempts for the same `source_id`, respecting
  `ScrapingSourcePolicy.rate_limit_seconds`. It has no concept of
  `is_unsafe`/`enabled` at all -- it purely tracks timing, so it can
  never be used to grant permission to scrape an unsafe source; a caller
  must still perform its own safety checks separately. Its `clock`/
  `sleep` are injectable so it is fully testable without any real
  `time.sleep`. **Nothing calls this guard yet** -- there is still no
  live scraper adapter in this codebase; this is foundation for a future
  one, the same way `ScrapingSourceRegistry` started empty in Step 168A.

## 50. End-to-End Scraped Accommodation Integration and Frontend Labels (Step 168E)

Step 168E proves the config-gated `scraped_local` provider (section 48)
works end to end -- through `PlanningOrchestrator`, `ProviderCoverage`,
`PlanValidatorService`, the `GET /trips/{trip_id}` API response, and the
frontend -- while adding **no live website fetching, no browser
automation, and no real lodging API integration**. At the time this step
was written, `scraped_local` was opt-in and default generation reported
`not_connected` with empty offers; **Step 168F (section 51) later made
`scraped_local` the default provider itself**, so default generation now
reports `unavailable` (still empty offers, still no fabricated data)
whenever no local HTML file is present.

- **End-to-end proof**: with `SCRAPING_ENABLED=true`, `SCRAPED_
  ACCOMMODATION_PROVIDER_ENABLED=true`, `ACCOMMODATION_PROVIDER=
  scraped_local`, and `SCRAPED_ACCOMMODATION_HTML_PATH` pointing at a
  local test fixture, a full `POST /trips/{id}/generate` produces a
  `PlanningState.accommodation_inventory_report` with `status=success`
  and real, parsed offers, retrievable via `GET /trips/{trip_id}` --
  every offer's `data_status`, `scraped_provenance` (source id/name/url,
  `parser_version`, `confidence`, `official_provider=false`), and
  missing/`null` fields (price, rating, availability, booking link,
  amenities) serialize exactly as the parser produced them.
- **Validator wording is now scraped-aware** (section 44/47/49):
  `PlanValidatorService`'s `accommodation_inventory` warning explicitly
  says "scraped_public_page/experimental/fragile", "not official-provider
  data", and "has not been verified" whenever any offer carries
  `scraped_provenance` -- still always a `WARNING`, never a critical
  issue, and it never claims official price/availability/rating/
  booking-link verification for scraped data.
- **Provider coverage is unaffected by this step's changes** --
  `ProviderCoverage.hotel_prices` already only reported `"success"` when
  real offers existed (section 44), and `accommodations` (the OSM-backed
  field) was already structurally independent (section 44/48); Step
  168E adds end-to-end tests confirming both hold when the offers
  actually come from the scraped path.
- **Frontend labeling** (`AccommodationInventorySection`/
  `ScrapedProvenanceBadge` in `frontend/app/page.tsx`, docs/16_
  frontend_architecture.md section 39.7): any offer carrying
  `scraped_provenance` is now visibly badged "Scraped public page ·
  Experimental/Fragile", with an explicit "not official-provider data,
  not verified" line, its source name/URL, and its parser version when
  present -- never merged into the plain "Connected" success rendering
  used for a hypothetical official-provider offer. Missing fields
  (price, rating, booking link, amenities) are still never rendered as
  if present -- unchanged from Step 167E's existing rule.
- **No itinerary/scheduling change**: `StayTransportDecision.
  accommodation_recommendations` stays empty, no day-plan experience is
  added/removed/reordered because of scraped inventory, and route-aware
  scheduling (Section 165/166) and regeneration refusal (`409
  REGENERATION_NOT_AVAILABLE`) are both confirmed unchanged by dedicated
  tests.

### 50.1 Full Section 168 Summary (Steps 168A-168F)

- **168A** -- `ScrapingSourcePolicy`/`ScrapedDataProvenance`/
  `ScrapingSourceRegistry` (`backend/app/models/scraping.py`), plus
  `Settings.scraping_enabled`/`scraped_accommodation_provider_enabled`/
  `scraping_default_rate_limit_seconds` -- originally all off/empty by
  default; the first two were flipped to on by Step 168F (section 51).
- **168B** -- `parse_scraped_accommodation_html`
  (`backend/app/providers/accommodation/scraped_parser.py`), a static
  HTML-only parser (stdlib `html.parser.HTMLParser`, no new dependency)
  producing normalized `AccommodationSearchResult`/`AccommodationOffer`
  data, plus `AccommodationOffer.scraped_provenance` and
  `DataStatus.SCRAPED_PUBLIC_PAGE`.
- **168C** -- `ScrapedAccommodationProvider`
  (`backend/app/providers/accommodation/scraped_adapter.py`), selectable
  via `get_accommodation_provider("scraped_local")`, reading only a
  manually-supplied local HTML file behind three independent gates --
  originally all off by default, now on by default as of Step 168F.
- **168D** -- A `ProviderCacheStore`-backed cache (normalized result
  only, never raw HTML; key includes the file's own mtime/size so an
  edited file is never served stale data) and `ScrapingRateLimitGuard`
  (foundation for a future live scraper; nothing calls it yet).
- **168E** -- End-to-end integration proof through planning/coverage/
  validation/API/frontend, plus scraped-aware validator wording and
  visible frontend provenance labeling.
- **168F** -- Flips the default: `accommodation_provider` now defaults
  to `"scraped_local"`, and `scraping_enabled`/`scraped_accommodation_
  provider_enabled` now default `True`, with `scraped_accommodation_
  html_path` defaulting to a fixed local path. See section 51.

Throughout every step: **no live website is ever fetched, no browser is
ever automated, no login-required/paywalled/captcha-protected page is
ever scraped, and no real Booking.com/Expedia/Hotelbeds/Hostelworld/
Amadeus/Vrbo/Airbnb integration exists.** As of Step 168F, the scraped
local/manual path is the default accommodation provider, but this still
never fabricates data: with no local HTML file present, it honestly
reports `unavailable` (never a placeholder offer), and whenever it does
return real offers they are always labeled `scraped_public_page`/
`experimental`/`fragile`, structurally barred from ever claiming
`official_provider=true`, and never fabricate a missing hotel, price,
availability, rating, amenity, cancellation policy, or booking link -- a
missing fact stays `None`/`unknown`/empty at every
layer, from the parser through the cache through the API response
through the rendered UI.

## 51. Scraped Accommodation Made the Default Provider (Step 168F)

Step 168F is a deliberate, explicit product decision (not a safety
relaxation): **`scraped_local` is now the default accommodation
provider**, replacing `not_connected` as `Settings.accommodation_
provider`'s default value. This does not add live website fetching,
browser automation, or a real lodging API integration -- it only changes
which already-existing, already-safe provider a fresh installation uses
out of the box.

**Exact config defaults changed:**

- `Settings.accommodation_provider`: `"not_connected"` -> `"scraped_local"`
- `Settings.scraping_enabled`: `False` -> `True`
- `Settings.scraped_accommodation_provider_enabled`: `False` -> `True`
- `Settings.scraped_accommodation_html_path`: `None` ->
  `".data/manual_scrapes/accommodations.html"` (resolved against the
  backend project root via the new `Settings.resolved_scraped_
  accommodation_html_path()`, mirroring `resolved_local_storage_path`/
  `resolved_provider_cache_path`)
- `Settings.scraped_accommodation_cache_enabled` (`True`) and
  `scraped_accommodation_cache_ttl_seconds` (`3600`) are unchanged from
  Step 168D.

**Default behavior with no local file present (the out-of-the-box
state on a fresh checkout):** generation still succeeds; `ProviderGateway.
accommodation_inventory` is a `ScrapedAccommodationProvider`; `Settings.
resolved_scraped_accommodation_html_path()` resolves to `.data/
manual_scrapes/accommodations.html` under the backend project root; that
file does not exist by default and is never created automatically; the
provider's own `path.stat()` check fails, and it returns `status=
unavailable` with `offers=[]` -- **never `not_connected`** (the app-level
gates are satisfied; only the file is missing) and **never a fabricated
offer**. `ProviderCoverage.hotel_prices` reports `"unavailable"`
accordingly (section 44's mapping is unchanged: `unavailable` for a
missing/empty result), and `PlanValidatorService`'s warning uses its
existing `unavailable`-branch wording ("no bookable lodging offers were
available... no price, availability, rating, or booking link data
exists to review").

**Default behavior once an operator places a real file at that path:**
identical to Step 168C/168E's proven end-to-end flow -- the provider
parses it via `parse_scraped_accommodation_html` (section 47), caches
the normalized result (section 49), and every offer carries `scraped_
provenance`/`data_status=scraped_public_page`, never `official_provider=
true`, with every missing field staying `None`/`unknown`/empty exactly
as the parser found it.

**Explicit opt-out is still possible and still fully supported**:
setting `ACCOMMODATION_PROVIDER=not_connected` (or `SCRAPING_ENABLED=false`
/ `SCRAPED_ACCOMMODATION_PROVIDER_ENABLED=false`) still selects/produces
the always-`not_connected` `NotConnectedAccommodationProvider` result,
exactly as it did before this step -- Step 168F changes only the
*default*, not the mechanism.

**Unaffected by this step:** OSM accommodation-like location candidates
(`DestinationContext.candidate_accommodation_pois`, `ProviderCoverage.
accommodations`) remain a wholly separate concept, never converted into
bookable inventory (section 41.1/44); itinerary scheduling, route-aware
scheduling (Section 165/166), and regeneration refusal are all
unchanged; no Groq/Anthropic/Kiwi/MCP call exists anywhere in this
path; and no `requests`/`httpx`/browser-automation dependency was added
-- confirmed by the same import-safety tests every prior Section 168
step already used.

---

## 52. Flight Provider Contract Foundation (Step 169A)

Step 169A adds the equivalent contract-only foundation for flights that
Section 167 (167A) added for accommodation, and nothing more:

- `backend/app/models/flight.py` defines `FlightSearchRequest`,
  `FlightSegment`, `FlightOffer`, `FlightSearchResult`, and
  `FlightSearchStatus` (`success`/`not_connected`/`unavailable`/`failed`,
  mirroring `AccommodationSearchStatus`).
- `backend/app/providers/flights/base.py` defines `FlightInventoryProvider`,
  an `abc.ABC` with one abstract method, `search_flights(request:
  FlightSearchRequest) -> FlightSearchResult` -- mirroring
  `AccommodationInventoryProvider` (section 43). Deliberately named and
  kept separate from the pre-existing, still-unused `app.providers.base.
  FlightProvider` stub interface (used by `ProviderGateway`'s `flight`
  slot), the same way `AccommodationInventoryProvider` was kept separate
  from `app.providers.base.AccommodationProvider`.

**No live flight provider is connected.** No Amadeus/Duffel/Kiwi/Google
Flights (or any other) integration exists, is called, or is implied by
this step. **No flight scraping is implemented yet either** -- there is
no flight equivalent of `parse_scraped_accommodation_html` (section 47)
or `ScrapedAccommodationProvider` (section 48) yet, and no local flight
HTML file path is configured.

**Not wired into `ProviderGateway` or `PlanningOrchestrator`.**
`ProviderGateway`'s constructor and `flight` slot are untouched; no
factory, no adapter selection, and no `Settings.flight_provider`-style
config field exists yet. `TripStrategyService`, `StayTransportService`,
and `PlanValidatorService` do not reference `FlightSearchRequest`/
`FlightSearchResult`/`FlightInventoryProvider` at all. Nothing in the app
currently constructs a `FlightSearchRequest` or consumes a
`FlightSearchResult` outside this subsystem's own tests.

**Future scraped flight data must be labeled `scraped_public_page`/
`experimental`/`fragile`.** `FlightOffer.scraped_provenance` reuses the
exact same `ScrapedDataProvenance` model accommodation offers use
(section 46) -- a future scraped flight offer must carry
`data_status=DataStatus.SCRAPED_PUBLIC_PAGE` to set it, structurally
barred from ever claiming `official_provider=true`, and a future official
API-backed flight offer (once one exists) must stay on a genuinely
separate `data_status` (`live`/`cached`/etc.) and must never carry
`scraped_provenance` -- enforced by the same
`validate_scraped_provenance_consistency` pattern
`AccommodationOffer` already uses.

**No fake airlines, flights, prices, or booking links are created.**
Every optional fact field on `FlightSegment` (airports, carrier name/code,
flight number, departure/arrival time, duration) and `FlightOffer`
(price, currency, booking URL, availability status, baggage policy,
cancellation policy) stays `None` unless a real future adapter supplies
it -- there is no default value anywhere in this module that could be
mistaken for a real flight fact. `FlightSearchResult.offers` may only be
non-empty when `status == success`, matching
`AccommodationSearchResult`'s own rule (section 43) that a
`not_connected`/`unavailable`/`failed` result must never carry a
fabricated or leftover offer.

Confirmed by dedicated import-safety tests
(`backend/app/tests/providers/test_flight_provider.py`) that neither
`backend/app/models/flight.py` nor `backend/app/providers/flights/base.py`
imports `httpx`/`requests`/a browser-automation library/Groq/Anthropic/
Kiwi/MCP -- mirroring every prior Section 167/168 skeleton step's own
import-safety checks.

---

## 53. Flight Provider Config, Not-Connected Adapter, Scraped-Local Stub, and Factory (Step 169B)

Step 169B adds the flight equivalent of Section 167B/C's accommodation
provider-selection foundation, with one deliberate difference: **the
default flight provider is `scraped_local`, not `not_connected`** --
matching the user requirement that flight scraping be on by default the
same way Section 168F made accommodation scraping the default.

**Config** (`backend/app/core/config.py`):

```text
flight_provider: str = "scraped_local"                    (FLIGHT_PROVIDER)
scraped_flight_provider_enabled: bool = True               (SCRAPED_FLIGHT_PROVIDER_ENABLED)
scraped_flight_html_path: str | None =
  ".data/manual_scrapes/flights.html"                      (SCRAPED_FLIGHT_HTML_PATH)
scraped_flight_source_id: str =
  "manual_local_scraped_flight"                             (SCRAPED_FLIGHT_SOURCE_ID)
scraped_flight_source_name: str =
  "Manual local scraped flight source"                      (SCRAPED_FLIGHT_SOURCE_NAME)
scraped_flight_base_url: str | None = None                 (SCRAPED_FLIGHT_BASE_URL)
```

`flight_provider` and `scraped_flight_provider_enabled` reuse the
existing app-wide `scraping_enabled` flag (Step 168A) as a second gate --
no separate flight-specific master switch was added. `Settings.
resolved_scraped_flight_html_path()` mirrors `resolved_scraped_
accommodation_html_path()` exactly: a relative path (including the
default) resolves against the backend project root, never the process's
current working directory, and returns `None` when
`scraped_flight_html_path` is explicitly unset.

**`NotConnectedFlightProvider`**
(`backend/app/providers/flights/not_connected_adapter.py`) mirrors
`NotConnectedAccommodationProvider`: `search_flights` always returns a
deterministic `status=not_connected`, `offers=[]` result with an honest
message, never calling a network service or inspecting `request` beyond
echoing its search context (origin/destination/dates/travelers/currency)
back onto the result -- never a fabricated airline, flight number,
airport, time, duration, price, availability, baggage policy,
cancellation policy, or booking link.

**`ScrapedLocalFlightProvider`**
(`backend/app/providers/flights/scraped_adapter.py`) is a **stub, not a
working scraper** -- the flight HTML parser (the flight equivalent of
`parse_scraped_accommodation_html`, section 47) does not exist until
Step 169C. As of Step 169B:

- If `scraping_enabled` or `scraped_flight_provider_enabled` is `False`,
  it returns `status=not_connected`, `offers=[]`.
- If `scraped_flight_html_path` is unset, or resolves to a path that
  doesn't exist, it returns `status=unavailable`, `offers=[]`.
- **If a local file does exist at that path, it still returns
  `status=unavailable`, `offers=[]`**, with a message explaining that
  flight HTML parsing is not implemented yet -- the provider checks only
  the path's existence (`Path.is_file()`), never opens or reads the
  file's content, and never fetches a live website. No offer is ever
  created from a file simply being present, even one shaped like a real
  flight listing.

**`get_flight_provider(provider_name=None)`**
(`backend/app/providers/flights/factory.py`) mirrors
`get_accommodation_provider`: resolves `provider_name`, or
`Settings.flight_provider` (default `"scraped_local"`) when omitted;
`"scraped_local"` selects `ScrapedLocalFlightProvider`, `"not_connected"`
selects `NotConnectedFlightProvider`, and any unrecognized name falls
back to `NotConnectedFlightProvider` rather than raising or fabricating
inventory. Provider selection itself never touches the network or the
filesystem -- only calling `search_flights` on the resolved provider
does, and even that never reaches the network.

**No live flight API, no flight scraping, and no fake data.** No
Amadeus/Duffel/Kiwi/Google Flights integration exists. No flight is ever
scraped from a real website. Explicitly setting
`FLIGHT_PROVIDER=not_connected` (or either scraping flag to `false`)
remains a fully supported opt-out, exactly like accommodation's Step
168F opt-out. **Still not wired into `ProviderGateway` or
`PlanningOrchestrator`** -- the gateway's existing `flight` slot
(`app.providers.base.FlightProvider`) and `PlanningOrchestrator` are
both untouched; nothing in the app calls `get_flight_provider` outside
this subsystem's own tests.

---

## 54. Static Flight HTML Parser (Step 169C)

Step 169C adds the flight equivalent of Section 168B's accommodation
static HTML parser: `backend/app/providers/flights/scraped_parser.py`'s
`parse_scraped_flight_html(html, source_policy, request, source_url,
parser_version) -> FlightSearchResult`, using only the Python standard
library `html.parser.HTMLParser` (no BeautifulSoup, no new dependency).

**The parser consumes already-provided static HTML only.** `html` must
already be the full page content the caller obtained through its own,
separately gated fetch path (none exists in this codebase yet) --
`parse_scraped_flight_html` itself never fetches `source_url`, never
opens a socket, and never calls a live website. `request`/`source_url`
are used only to label the returned result's context; neither is ever
used to construct or follow a live request.

**Fixed test micro-format** (fixtures only -- no real site is known to
use this exact shape): a "flight offer" is any element carrying class
`flight-offer` and a `data-offer-id` attribute, containing one or more
`outbound-segment` elements (required -- at least one) and zero or more
`return-segment` elements. Inside each segment: `origin-airport`,
`destination-airport`, `departure-time`/`arrival-time` (ISO-8601 text),
`carrier-name`, `carrier-code`, `flight-number`, `duration-minutes`.
Directly inside the offer (not inside a segment): `total-price`
(optional `data-currency` attribute), `currency` (used as a fallback
when `total-price` has no `data-currency` of its own), `availability-
status`, `baggage-policy`, `cancellation-policy`, and a `booking-link`
(`<a href="...">`).

**`ScrapingSourcePolicy` gains `allows_flights: bool = False`**
(`backend/app/models/scraping.py`), mirroring `allows_lodging`/
`allows_restaurants`/`allows_attractions` -- defaulting to `False` so no
existing/default source policy silently gains flight-scraping permission.
The parser refuses to parse (`status=not_connected`, `offers=[]`, HTML
never touched) when `source_policy.is_unsafe` (login-required/paywalled/
captcha-expected/not personally approved -- checked even when the
source is `enabled=False`), when the source is not `enabled`, or when
`allows_flights` is `False` -- mirroring `parse_scraped_accommodation_
html`'s three-check refusal exactly.

**Parsed flight data is `scraped_public_page`/`experimental`/`fragile`,
never official-provider data.** Every parsed `FlightOffer` carries
`data_status=DataStatus.SCRAPED_PUBLIC_PAGE` and a `scraped_provenance`
(`ScrapedDataProvenance`) with `source_type`/`provenance=
scraped_public_page`, `confidence=experimental`,
`extraction_method=static_html_parser`, `official_provider=False`
(structurally fixed, never settable to `True`), and the caller-supplied
`parser_version`/`source_url` plus `source_policy.source_id`/
`source_name` preserved. `FlightOffer`'s existing
`validate_scraped_provenance_consistency` validator (Step 169A) still
rejects any attempt to attach `scraped_provenance` to a non-
`scraped_public_page` offer -- this step adds no new bypass.

**Missing flight fields remain missing, never guessed.** A field class
absent from the HTML leaves the corresponding `FlightSegment`/
`FlightOffer` field `None` (or an empty `outbound_segments`/
`return_segments` list) exactly as `parse_scraped_accommodation_html`
already does for lodging -- no fallback price, airline, carrier code,
flight number, airport, departure/arrival time, duration, availability,
baggage policy, cancellation policy, or booking link is ever created. An
offer missing `data-offer-id` or with zero `outbound-segment` elements
can't be safely identified as a bookable flight and is skipped entirely
rather than given a fabricated id or segment; a page with no valid
offers returns `status=unavailable`, `offers=[]`. A field that fails
`FlightOffer`'s own validation (e.g. an invalid currency code) is caught
and reported as `status=failed`, `offers=[]`, with a safe message --
never a raw traceback, and never a guessed/placeholder value substituted
in to make the offer validate.

**Not wired into `ScrapedLocalFlightProvider`, `ProviderGateway`, or
`PlanningOrchestrator` yet.** `ScrapedLocalFlightProvider` (Step 169B)
still never calls `parse_scraped_flight_html` -- it still only checks
local-file existence and reports `unavailable` either way; wiring the
parser into the provider (plus the accommodation-style cache) is Step
169D. Confirmed by dedicated tests that neither
`scraped_adapter.py`, `ProviderGateway`, nor `PlanningOrchestrator`
references `scraped_parser`/`parse_scraped_flight_html`.

**No fake airlines, flights, prices, or booking links are created** --
confirmed by dedicated import-safety tests
(`backend/app/tests/providers/test_scraped_flight_parser.py`) that
`scraped_parser.py` imports no `httpx`/`requests`/browser-automation
library/Groq/Anthropic/Kiwi/MCP, mirroring every prior Section 167/168
parser step's own import-safety checks.

---

## 55. Scraped Local Flight Provider Wired to the Parser and Cache (Step 169D)

Step 169D wires `ScrapedLocalFlightProvider`
(`backend/app/providers/flights/scraped_adapter.py`) to the Step 169C
parser and a Step 168D-style cache, completing the flight equivalent of
Section 168's accommodation provider. **The default flight provider
remains `scraped_local`** (`Settings.flight_provider`, unchanged from
Step 169B) -- this step only changes what that provider actually does
once a local file exists.

**It reads only the configured local HTML file -- never a live
website.** `ScrapedLocalFlightProvider.search_flights` still checks the
same three gates as Step 169B (`scraping_enabled`,
`scraped_flight_provider_enabled`, and `Settings.resolved_scraped_
flight_html_path()` actually resolving to an existing file); the only
change is that once a file is found, it is now read
(`path.read_text(encoding="utf-8")`) and handed to
`parse_scraped_flight_html` (section 54) via a
`ScrapingSourcePolicy(allows_flights=True, enabled=True,
approved_for_personal_use=True, ...)` the provider builds itself. No
`requests`/`httpx`/browser-automation import exists in this module, and
no code path in it ever opens a socket.

**Cache** (`ProviderCacheStore`, source `"scraped_flight"`, mirroring
section 49's accommodation cache): a successful parse is cached under a
key hashing `source_id`, `base_url`, `origin`, `destination`,
`departure_date`, `return_date`, `adults`, `children`, `cabin_class`,
`currency`, `parser_version`, and the local file's own `path`/
`mtime_ns`/`size` -- so changing any request field, bumping the parser
version, or editing the local file (which changes its mtime/size) all
independently miss the old entry and trigger a fresh parse; the cache
never stores raw HTML, only the normalized `FlightSearchResult` payload
(`result.model_dump(mode="json")`). Gated by new `Settings.scraped_
flight_cache_enabled` (default `True`) and `scraped_flight_cache_ttl_
seconds` (default `3600`) -- when caching is disabled, `get_provider_
cache_store` is never even called, and every request reparses the file
directly. Only a `status=success` result is ever cached; `not_connected`/
`unavailable`/`failed` are never cached, and a broken cache read/write is
logged and treated as a fallback to (re-)parsing -- never a crash, and
never a fabricated result.

**Cached scraped flight data preserves its `scraped_public_page`/
`experimental`/`fragile` provenance exactly.** A cache hit round-trips
through `FlightSearchResult.model_validate(entry.payload)`, so every
returned offer's `data_status=SCRAPED_PUBLIC_PAGE`,
`scraped_provenance` (including `official_provider=False`,
`extraction_method=static_html_parser`, `parser_version`, `source_id`/
`source_name`/`source_url`), and every honestly-missing field are
identical whether the result came from a fresh parse or a cache hit --
nothing is relabeled, upgraded, or reinterpreted as official-provider
data on a hit.

**No fake airlines, flights, prices, or booking links are created** at
any point in this flow -- a field absent from the HTML stays honestly
`None`/empty through the parser, through the cache round-trip, and out
of `search_flights`'s return value; `ScrapedLocalFlightProvider` adds,
guesses, or backfills nothing itself. `ScrapingRateLimitGuard`
(`app.models.scraping`, Step 168D) is still not used by this provider --
that guard exists for a *future live* scraper's repeated network
requests, and this provider only ever reads a local file, so there is
nothing to rate-limit.

**Still not wired into `ProviderGateway` or `PlanningOrchestrator`.**
The gateway's existing `flight` slot (`app.providers.base.
FlightProvider`) and `PlanningOrchestrator` remain untouched -- confirmed
by dedicated tests
(`backend/app/tests/providers/test_scraped_flight_cache.py`) asserting
neither module's source references `ScrapedLocalFlightProvider` or
`app.providers.flights`.

---

## 56. Flight Inventory Wired Through the Full App (Step 169E, final Section 169 step)

Step 169E completes Section 169 by exposing flight inventory end to end
-- `ProviderGateway`, a new `FlightInventoryService`, `PlanningState.
flight_inventory_report`, `ProviderCoverage.flights`, a non-blocking
`PlanValidatorService` warning, and API/frontend display -- while
preserving the exact no-fake-flight-data guarantees every prior Section
169 step already established.

**`ProviderGateway`** gains a `flight_inventory` slot
(`FlightInventoryProvider | None`, defaulting to
`get_flight_provider()`, i.e. `scraped_local`) and a `search_flights(
request: FlightSearchRequest) -> FlightSearchResult` method, mirroring
`accommodation_inventory`/`search_accommodations` (section 43) exactly.
The gateway adds no guessing or fallback of its own -- it only ever
delegates to whatever provider is configured/injected.

**`FlightInventoryService`** (`backend/app/services/
flight_inventory_service.py`) builds a `FlightSearchRequest` from the
trip's own request fields -- `origin_city` (optional, never guessed when
absent) becomes `origin`, `primary_destination` becomes `destination`,
`start_date`/`end_date` become `departure_date`/`return_date` (a
same-day trip is treated as one-way), `travelers_count` becomes
`adults`, `budget_currency` becomes `currency` -- then calls
`ProviderGateway.search_flights`, mirroring
`AccommodationInventoryService` (section 44) exactly, including its
Step 166D-style fail-safe exception handling.

**`PlanningState.flight_inventory_report: FlightSearchResult | None`**
is computed by `FlightInventoryService` directly inside
`PlanningOrchestrator.run_stay_transport_stage`, right alongside
`accommodation_inventory_report` -- never a stage service calling a
provider adapter directly, and never scheduling a flight into the
itinerary as a daily experience.

**`ProviderCoverage.flights`** (the pre-existing field from section 8)
is set from `flight_inventory_report.status`, using the same rule as
`hotel_prices` (section 44): a `success` result with zero offers is
reported `unavailable`, never upgraded to imply inventory exists when it
doesn't. With the default `scraped_local` provider and no local file
present, this is `unavailable`; explicitly opting out to
`not_connected` (or scraping disabled) reports `not_connected` honestly.

**`PlanValidatorService`** adds a non-blocking `flight_inventory`
warning category, mirroring `accommodation_inventory` (section 44)
exactly: missing/`not_connected`/`failed`/`unavailable` flight inventory
never blocks generation; a `success` result with real offers still
never claims those offers were reviewed for accuracy or scheduled into
the itinerary; and when any offer carries `scraped_provenance`, the
message explicitly calls out `scraped_public_page`/`experimental`/
`fragile`, not official-provider data, needing manual review -- never
claiming official schedule/price/availability/baggage/booking-link
verification for scraped data.

**API serialization** required no route change: `GET /trips/{trip_id}`
already serializes the full `PlanningState`, so adding
`flight_inventory_report` to that model exposes it automatically, with
`scraped_provenance.official_provider` serializing as `false` and every
missing field as `null`/empty, exactly as the model already guarantees.

**Frontend** (`frontend/app/page.tsx`, `frontend/lib/types.ts`) adds a
`FlightInventorySection` mirroring `AccommodationInventorySection`
(section 39.6-39.9) -- rendering only backend-returned fields, showing a
`ScrapedFlightProvenanceBadge` for any offer with `scraped_provenance`
(same "Scraped public page · Experimental/Fragile · not
official-provider data" treatment), never rendering a missing price/
airline/flight-number/time/duration/baggage/cancellation/booking-link
as if present, and never adding a flight into any day card in the
itinerary.

### Section 169 Summary (169A-169E)

The complete flight inventory foundation, end to end:

```text
Step 169A -- FlightSearchRequest/FlightSegment/FlightOffer/
             FlightSearchResult models, FlightInventoryProvider
             interface (contract only, no adapter).
Step 169B -- flight_provider config (default "scraped_local"),
             NotConnectedFlightProvider, ScrapedLocalFlightProvider
             (stub, no parser yet), get_flight_provider factory.
Step 169C -- parse_scraped_flight_html static HTML parser (stdlib
             html.parser only), ScrapingSourcePolicy.allows_flights.
Step 169D -- ScrapedLocalFlightProvider wired to the parser plus a
             ProviderCacheStore-backed cache (source "scraped_flight").
Step 169E -- ProviderGateway.search_flights, FlightInventoryService,
             PlanningState.flight_inventory_report,
             ProviderCoverage.flights, PlanValidatorService's
             flight_inventory warning, API exposure via the existing
             GET /trips/{trip_id}, and FlightInventorySection in the
             frontend.
```

Throughout every step: **no live flight website is ever fetched, no
Amadeus/Duffel/Kiwi/Google Flights (or any other) API integration
exists, no Kiwi/MCP integration exists, and no requests/httpx/
browser-automation dependency was added.** The default flight provider
is `scraped_local`; with no local HTML file present (the state of a
fresh checkout), every layer -- provider, service, `PlanningState`,
coverage, validation, API, frontend -- honestly reports
`unavailable`/empty, never a fabricated airline, flight number, airport,
departure/arrival time, duration, price, availability, baggage policy,
cancellation policy, or booking link. Once an operator supplies a real
local HTML fixture, every resulting offer is labeled
`scraped_public_page`/`experimental`/`fragile`, structurally barred
from ever claiming `official_provider=true`, and a flight is never
scheduled into the itinerary as a daily experience -- this remains
inventory reporting only.

## 57. Hotel Ratings Provider Foundation (Section 177, Steps 177A-177E)

A brief note for this document's own provider-catalog purpose (full
detail lives in docs/13_llm_reasoning_pipeline.md and
docs/14_backend_architecture.md, Section 177): `backend/app/models/
hotel_ratings.py` and `backend/app/providers/hotel_ratings/` add a
**provider foundation and conservative enrichment layer for hotel
ratings, not a live ratings integration.**

- `Settings.hotel_ratings_provider` defaults to `"not_connected"`, and
  the only implemented adapter is `NotConnectedHotelRatingsProvider` --
  no real Google Places, Tripadvisor, Amadeus Hotel Ratings, Yelp, or
  any other external ratings API is wired in anywhere in this codebase.
- A future external adapter would need its own provider-specific
  hotel/property identity (a Google Place ID, a Tripadvisor
  `location_id`, an Amadeus hotel ID, a Yelp `business_id`, etc.) and
  must resolve it only through **exact, conservative property identity
  matching** -- never fuzzy name/address/coordinate matching, and never
  a confidence-threshold "probably the same hotel" heuristic.
- `HotelRatingEnrichmentService` (Step 177C) already enforces this
  contract structurally: a lookup result is only ever attached to an
  offer when it echoes back the exact identity correlation the request
  carried, with no duplicate/ambiguous match and no `provider_property_id`
  conflict. **Any ambiguous, unmatched, or conflicting result leaves
  `AccommodationOffer.rating_details` at `null`** -- ratings are optional
  metadata, and a missing rating is never treated as a signal about a
  property's quality.