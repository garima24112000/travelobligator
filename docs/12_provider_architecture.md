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