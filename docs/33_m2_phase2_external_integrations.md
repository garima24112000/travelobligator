# 33. M2 Phase 2 — External integration reliability

Status: implemented on branch `m2/phase2-integration-reliability` and verified by bounded DEVELOPMENT live runs
(sections 6–8). **Not deployed.** No production setting changed: Kiwi MCP, the commercial-airport lookup and
targeted regeneration stay off by default and in `render.yaml`; no live site is scraped anywhere.

## 1. Evidence used

- The development end-to-end run of Phase 1 (Gothenburg, Groq only). Its throwaway store held the generation's
  own performance report; only fixed labels and numbers were read from it:

  | Stage | Requests | What happened |
  |---|---|---|
  | anchor | 2 | Groq HTTP 400 = `json_validate_failed` (model output failed the JSON schema), then the structural retry succeeded |
  | reasoning | 2 | Groq 429, waited Groq's Retry-After, retried, 429 again; stage fell back to the deterministic planner |
  | narrator | 1 | Groq 200 |

  Groq's remaining-token ratio was 0.43 before reasoning, which sends the prompt plus `max_tokens=6000`. The
  second refusal shows the short Retry-After did not reflect when enough TOKENS would be free (token reset ≈ 34 s).
- The recorded Kiwi runs: the live call that returned offers sent bare city names ("London" → "Paris"); the run
  that returned zero offers sent the trip's free text ("Jersey City, NJ" → "Orlando, FL").
- Official provider documentation read on 2026-10-23 (section 5).

## 2. Repairs

### A. Kiwi MCP flights (P0)

- **Search inputs.** Kiwi resolves place *names* itself. The adapter now sends the city part of each place
  (`place_query`: text before the first comma) and, for the destination, the city the destination geocoder
  already resolved (`FlightSearchRequest.destination_city`, no new request). No airport code is ever guessed.
  Known limit: ambiguous city names ("Paris, Texas"). Whether this fixes the zero-offer route needs the live run.
- **Currency.** A price is kept only with the currency Kiwi stated: the payload's `currency`, else the code in
  the itinerary's own `priceFormatted` label. The trip's currency is never used as a label; a price with no
  stated currency is dropped (the offer is kept). Traveler view says "Price not provided by the flight source."
- **Bounded time.** One total limit for connect + discovery + search (`KIWI_MCP_TOTAL_TIMEOUT_SECONDS`, 20 s);
  a timeout cancels the call and the MCP session closes on the way out.
- **Caching.** A successful tool discovery (names and schemas, never flight data) is reused in-process
  (`KIWI_MCP_DISCOVERY_CACHE_SECONDS`, 3600; 0 disables). Offers are never cached.
- **Accounting.** Each discovery and search is timed and counted in the generation performance report
  (`kiwi_mcp_discovery`, `kiwi_mcp_search`).
- **Outcomes.** `failure_kind` (`timeout` / `connection` / `tool_error`) on provider failures; a past departure
  date is "search not possible" (no request); only fields the discovered schema declares are sent.
- **Reaching the plan.** Offers are stored in `flight_inventory_report` and shown in Traveler view's Flights
  section (they are not scheduled as day activities). Kiwi stays disabled until a live run and approval.

### B. Scraping and accommodations (P0)

- No live scraper exists, and none was added. The "scraped" sources are parsers of **manually supplied local
  files**. User-visible wording that implied scraping was corrected ("Manually supplied local file"; the
  not-enabled messages now say no live site is ever fetched).
- Accommodation results carry `availability_reason` like flights (`no_source_configured`,
  `local_source_no_matching_offers`, `provider_failed`, `offers_found`, `search_not_possible`); the message for a
  file that held no offer no longer says no file was found.
- Restricted platforms (Airbnb, Booking.com, Expedia, Vrbo, Tripadvisor, Google Flights) are not scraped (project
  rule). No permitted, free, runtime source of bookable hotel prices was found (section 5).

### C. LLM reliability (P1)

- **Same-provider retry after a 429.** The wait is now the longest of the provider's Retry-After and the reset
  of the quota dimension its headers show refused the request (requests left → tokens; none left → requests). A
  wait that does not fit the stage budget is not started: no wasted request, the deterministic fallback runs at
  once. New diagnostic labels: `quota_reset_obeyed` / `quota_reset_skipped`.
- **HTTP 400.** `json_validate_failed` stays a structural failure (recoverable by the structural retry or
  failover); any other 400 is non-transient and never retried.
- Provider order, circuit-breaker timing and the single-provider "try regardless of last state" rule are
  unchanged: the run showed the narrator succeed while Groq was marked open, so skipping by state alone would
  have lost a good answer.

### D. Status accuracy (P1)

- **Route context.** After routing, both engines (and both regeneration paths) restate the final route report in
  `experience_plan.route_feasibility_context`: one segment per transfer with a provider route; a day or plan is
  `live` only when every transfer is verified, otherwise `unavailable` with a count of unverified transfers. No
  report / routing not connected leaves the draft context.
- **`GENERATION_ENABLED=false`.** Readiness now reports `generation_paused` (temporary) instead of offering an
  action the route refuses with 503. A missing capability is still reported first.
- Developer view shows each inventory's reason and failure kind.

All new fields are optional; stored trips load unchanged.

## 3. What works where

| Integration | In code | Live status |
|---|---|---|
| Geoapify, Open-Meteo, Nager.Date, Frankfurter | working | verified in Phase 1 (development) |
| Groq / Gemini routing, retry waits | improved | Groq verified in Phase 1; new wait logic needs a live 429 to observe |
| Route context restatement | working | needs the E2E run |
| Kiwi MCP flights | repaired; location outcome + request accounting remediated (section 6) | control route verified live (15 offers, provider-stated USD); place-without-airport route and accounting need the version-2 run |
| Commercial-airport lookup (flights) | reworked after the live run (section 7), off by default | **needs live verification** with a local OurAirports file |
| Accommodation / flight local files | working (manual files only) | no live source by design |
| Bookable accommodation prices | not available | externally blocked (section 5) |
| Hotel ratings, transit | not connected | no source |

## 4. Bounded live verification

Superseded by section 6.4 (script version 2). Version 1's Kiwi cap was never enforced: see section 6.2.

## 5. External blockers (documentation read 2026-10-23; not legal advice)

| Source | Finding |
|---|---|
| Expedia Rapid | Partner status, site review and business-development enablement required for production. Blocked. |
| Booking.com Demand API | Affiliate ID and token required; the affiliate programme page was unreachable (404). Blocked pending an affiliate agreement. |
| Hotelbeds | Free evaluation key and sandbox; going live requires a commercial profile and certification. Blocked for production. |
| Duffel (flights) | Test mode self-serve; live needs verification, a card on file and per-order fees; terms prohibit metasearch use and redistribution. Not suitable without a commercial decision. |
| Amadeus Self-Service | Pages could not be read; status (including any decommissioning) unconfirmed. |
| Kiwi.com MCP | Terms page could not be read (timeouts); intended-use terms unconfirmed — check before enabling. |
| Travelpayouts | Page returned 403; unconfirmed. |

No paid account was created and nothing was purchased.

## 6. Kiwi live-test remediation (after the version-1 run)

### 6.1 What the version-1 run returned (2026-10-23, Kiwi only, development)

| Route | Searched as | Result |
|---|---|---|
| control | "London" -> "Porto" | 15 offers; all 15 priced in USD, stated by Kiwi (the request asked for USD) |
| previously zero-offer | "Jersey City" -> "Orlando" | 0 offers, `error: null`, reported `search_completed_no_offers` |
| request accounting | — | `sent_total: 0`, although both searches were answered |

### 6.2 Root causes

- **Zero offers.** Kiwi's MCP server exposes one tool, `search-flight`, with no location or airport lookup (the
  public README says "The MCP server exposes a single tool: search-flight"; Section 178B's live discovery found the
  same plus `feedback-to-devs`). Its `flyFrom`/`flyTo` take "IATA code, city or airport name" and the response
  never says which airports a name was matched to. Jersey City has no airport of its own, so whether Kiwi failed
  to match the name or matched it to nothing searchable, the answer is an empty list with no error — exactly what
  a real "no flights" looks like. The defect on our side: that answer was reported as a completed search with no
  offers. (Not established: which of the two Kiwi did; the version-2 run records Kiwi's live schema text.)
- **0 HTTP requests.** The MCP SDK (`mcp` 2.2.0) sends over `httpx2` (version 2.13.1), a separate package. The
  verification guard patched only `httpx`, so it never saw, counted or capped a single Kiwi request: version 1's
  "Kiwi <= 18 requests" was never enforced.

### 6.3 Fixes

- **Honest outcome.** A search in which either side was sent as a place NAME and that returned zero offers is now
  `location_unconfirmed` ("Kiwi does not say which airports it matched to a place name ..."). Only a search between
  airport codes (an all-capitals three-letter entry, e.g. `LHR` — not "Ufa" or "lhr") can be
  `search_completed_no_offers`. New reason `no_searchable_airport`: the place could not be found on the map, or no
  international airport lies within the lookup radius — no search is reported for it.
- **Nearby airports, with provenance** — *superseded by section 7: the Geoapify category selection described here
  picked a seaplane base and an executive airport in the live run* (`providers/flights/airport_lookup.py`,
  `FLIGHT_AIRPORT_LOOKUP_PROVIDER`, default `not_connected`). Only after a name search found nothing: the place is geocoded (it must be a settlement
  or area), then Geoapify Places is asked for `airport.international` within `FLIGHT_AIRPORT_LOOKUP_RADIUS_KM`
  (60 km straight line), nearest first. The category was checked on the official supported-categories list on
  2026-10-23 and added to the verified snapshot; it is never used by itinerary discovery. An airport's code is
  used only when the record carries a three-letter OpenStreetMap `iata` tag (returned by Geoapify, not a
  documented field, hence optional); otherwise it is sent by name, which Kiwi's schema accepts. Nothing is
  hardcoded. One further Kiwi search is made against the nearest airport of each side searched by name, so a
  route costs at most 2 Kiwi searches and 4 Geoapify requests (4 credits), charged to the generation's own credit
  cap (`stay_transport` now receives the generation's provider context in both engines).
- **Provenance in the result.** `FlightSearchResult.location_resolution` records, per side, what the traveller
  asked, what Kiwi was sent and how (`airport_code` / `place_name` / `nearby_airport` / `not_resolved`), the airport's
  name, code, straight-line distance and source. Traveler view says e.g. "Flights were searched from <airport>
  (<code>), the nearest international airport found near <place> (about N km in a straight line). It is not in
  <place> itself."; Developer view lists every side, the number of searches and the HTTP requests observed.
- **Request accounting.** The Kiwi client now builds its own `httpx2` client whose transport counts every request
  (initialize, notifications, the GET stream, tool calls, the closing DELETE) and refuses the request beyond
  `KIWI_MCP_MAX_HTTP_REQUESTS_PER_SESSION` (8) before it is sent (`failure_kind=request_cap`). Each result carries
  `http_requests` / `provider_http_requests`; a reused discovery counts 0; the generation's performance report
  counts `kiwi_mcp_http_requests`. An injected test client reports `None` — nothing is claimed for traffic the
  client cannot see. Offline evidence: the real MCP SDK against an in-process MCP server counted 2 requests for a
  discovery session and 3 for a search session, all status 200; a cap of 1 stopped the session as `request_cap`.
  Real Kiwi may use more per session (an SSE GET, the closing DELETE); 8 leaves room for that.
- **The verification guard** now patches `httpx2` as well and reports requests per package.

### 6.4 Bounded live verification, version 2

    cd backend
    python scripts/verify_m2_phase2.py --skip-e2e                              # dry run: plan + caps, NO request
    python scripts/verify_m2_phase2.py --skip-e2e --execute --out /tmp/m2p2_kiwi.json
    python scripts/verify_m2_phase2.py --skip-e2e --airport-lookup --execute --out /tmp/m2p2_lookup.json
    python scripts/verify_m2_phase2.py --execute --out /tmp/m2p2_e2e.json      # optional: + one generation

| Run | Kiwi HTTP (guard cap) | Kiwi sessions | Geoapify requests / credits | LLM | Total requests |
|---|---|---|---|---|---|
| Kiwi only | <= 40 | <= 5 (1 discovery + 2 routes x 2 searches) | 0 / 0 | 0 | <= 40 |
| + airport lookup | <= 40 | <= 5 | <= 8 / <= 8 | 0 | <= 48 |
| + end-to-end | <= 56 | <= 7 | <= 100 / <= 100 (generation cap) | <= 8 combined | <= 171 |

Each MCP session is also capped at 8 HTTP requests by the client itself. The first 401/403, or a 429 from Kiwi or
a data service, stops every further request.

**Pass/fail criteria** (the script exits 1 on any FAIL):

- discovery succeeded and names a flight tool (its schema text is recorded);
- each route: no provider or request-cap failure; every priced offer carries a currency Kiwi stated; at most 2
  searches;
- control: offers found;
- place without an airport: PASS when offers are found and every substituted airport carries name, source and
  distance, or the outcome is `location_unconfirmed` / `no_searchable_airport`; FAIL when a search by NAME is
  reported as `search_completed_no_offers`, or on any other outcome. Whether offers were found is reported as
  information, not as a pass condition — without the lookup, `location_unconfirmed` is the correct result;
- accounting: the guard's Kiwi count equals the Kiwi client's own count and is above 0, and is within the cap.
  A run whose Kiwi traffic could not be observed FAILS this check: no cap is claimed for traffic nobody counted;
- end-to-end (optional): unchanged from version 1.

## 7. Commercial-airport selection (second correction, after the version-2 lookup run)

### 7.1 Live evidence (version 2, development, 2026-10-23)

| Run | Jersey City -> Orlando | Kiwi HTTP (guard = client) | Geoapify |
|---|---|---|---|
| without the lookup | `location_unconfirmed`, 0 offers | 8 = 8 | 0 |
| with the lookup | searched `NYS` -> `ORL`, 0 offers, reported `search_completed_no_offers` (check FAILED) | 11 = 11 | 4 |

The lookup chose "New York Skyports Incorporated Seaplane Base" (NYS, 6.5 km) and "Orlando Executive Airport" (ORL,
4.7 km): the NEAREST Geoapify `airport.international` features, both with an IATA tag, neither with scheduled
airline service. Kiwi's live schema (recorded by the run) takes ONE location per side: `flyFrom` "IATA code, city
or airport name (e.g. "PRG", "Prague", "London Heathrow")" — unlike `stopover_airports`, no comma-separated list.

### 7.2 Root cause

1. **Nearest-first selection over a category that is not evidence of service.** Geoapify documents
   `airport.international` as "Handles international flights with customs and immigration facilities"; in the data
   it returned it covered a seaplane base and a general-aviation airport. An IATA code is not evidence either —
   both had one.
2. **"Codes on both sides" was taken as proof of an honest empty search**, so an empty answer between two airports
   that have no scheduled flights became `search_completed_no_offers`.

### 7.3 Selection strategy (`providers/flights/airport_lookup.py`, `FLIGHT_AIRPORT_LOOKUP_PROVIDER=ourairports_file`)

- **Evidence source: OurAirports** (ourairports.com/data; "All data is released to the Public Domain, and comes
  with no guarantee of accuracy or fitness for use"; data dictionary read 2026-10-23). `airports.csv` has
  `scheduled_service` — "yes" if the airport currently has scheduled airline service — and `type` (`large_airport`,
  `medium_airport`, `small_airport`, `heliport`, `seaplane_base`, `balloonport`, `closed_airport`). The legend
  describes large as "scheduled major airline service with millions of passengers/year, or major military base",
  medium as "scheduled regional airline service, or regular general aviation or military traffic" — so size alone
  is NOT evidence; `scheduled_service=yes` is required in addition.
- The file is supplied by the operator (`FLIGHT_AIRPORT_DATA_PATH`; e.g. downloaded from
  `https://davidmegginson.github.io/ourairports-data/airports.csv`, updated daily upstream) and read locally; it is
  never downloaded at runtime and is not committed. Its SHA-256 fingerprint is recorded with every selection.
- **Eligible only:** `scheduled_service == "yes"`, a land airport type (`large` / `medium` / `small`; heliports,
  seaplane bases, balloonports and closed airports are never loaded), a valid three-letter IATA code, within
  `FLIGHT_AIRPORT_LOOKUP_RADIUS_KM` (60 km, straight line) of the geocoded place.
- **Ranking:** service class first (large, then medium, then small), then straight-line distance, then the code
  as a neutral tie-break. A nearer airport with less service never displaces a larger one in range.
- **Location of the place:** the configured geocoder (Geoapify in the live configuration), one request per side,
  charged to the generation's provider context. No Geoapify Places request is made any more, and
  `airport` / `airport.international` were removed from the verified category snapshot.
- **Provenance per substituted side:** name, IATA code, latitude/longitude, airport type, country, source
  (`ourairports`), evidence (`ourairports:scheduled_service=yes`), straight-line distance, data-set fingerprint and
  the rationale ("Chosen as a large airport (scheduled major airline service) with scheduled airline service in the
  OurAirports data, about N km in a straight line; larger service classes are preferred over nearer smaller ones,
  and airports without scheduled service are never chosen.").
- **Honest availability:** an airport without scheduled-service evidence or without a code is never substituted;
  no data file -> `not_connected` (no geocoder request is made) -> the empty name search stays
  `location_unconfirmed`; no eligible airport in range -> `no_searchable_airport` ("no airport with scheduled
  airline service was found within 60 km of it in the airport data used"); a completed empty search counts as
  `search_completed_no_offers` only when every substituted side carries commercial-service evidence — otherwise
  `location_unconfirmed`.
- Unchanged: at most 2 Kiwi searches per route, the Kiwi client's transport accounting and 8-request session
  cap, the total time limit, and the generation's credit cap. No airport or destination is named in the code.

### 7.4 Bounded live rerun (script version 3)

    # once, outside the repository or under the git-ignored backend/.data/ (public-domain data; not a provider call
    # of the application):
    curl -fsSL -o /tmp/ourairports-airports.csv https://davidmegginson.github.io/ourairports-data/airports.csv

    cd backend
    python scripts/verify_m2_phase2.py --skip-e2e --airport-lookup --airport-data /tmp/ourairports-airports.csv   # dry run
    python scripts/verify_m2_phase2.py --skip-e2e --airport-lookup --airport-data /tmp/ourairports-airports.csv \
        --execute --out /tmp/m2p2_v3.json

Caps: Kiwi <= 40 HTTP requests (<= 5 sessions x 8), Geoapify <= 4 geocoding requests / <= 4 credits, no LLM —
<= 44 requests in total. **Pass criteria added in version 3:** every substituted airport must have
scheduled-service evidence, a land-airport type, an IATA code, a location, a source and a rationale (any outcome
fails otherwise — including offers); with the lookup on, the place-without-airport route must return offers (an
honest `location_unconfirmed` is not a pass there). The accounting checks of version 2 are unchanged.

## 8. Live verification evidence (development, 2026-10-23)

All runs: `APP_ENV=development`, throwaway Local JSON storage, Kiwi and the airport lookup switched on in the
script's own process only, every `httpx` and `httpx2` request through the guard. Reports were kept outside the
repository; only codes and counts are quoted here.

### 8.1 Kiwi probes (script version 3, `--skip-e2e --airport-lookup`)

| Route | Searched | Result |
|---|---|---|
| control | "London" -> "Porto" (place names) | 15 offers, all priced in USD stated by Kiwi; departures LGW, LHR, LTN, STN |
| place without an airport | "Jersey City" -> "Orlando" (empty, `location_unconfirmed`), then `EWR` -> `MCO` | 15 offers, all USD, all departing EWR |

EWR and MCO were selected from OurAirports (`large_airport`, `scheduled_service=yes`) with full provenance; Kiwi
HTTP 11 = 11 (guard = client), Geoapify 2 requests / 2 credits.

### 8.2 End-to-end generation (script version 4, `--e2e-only`)

One generation through the real HTTP routes: Jersey City, NJ -> Orlando, Florida, 2 days, airport data
`sha256:a19e9690fc3bffa8` (OurAirports; 4,008 land airports with scheduled service).

| Area | Observed |
|---|---|
| Job | `succeeded`; trip saved; the saved state and the API return the same 15 offers |
| Flights | 15 offers, all with price, provider-stated USD, an https booking link and a departure airport; 2 searches |
| Airport provenance | origin EWR (large, 11.0 km straight line), destination MCO (large, 14.3 km), both `ourairports:scheduled_service=yes`; every offer departs EWR |
| Routing | 4 of 4 transfers verified; route context `live` with 4 segments (agrees with the route report) |
| Accommodation | `no_source_configured`, honest wording (no scraping claim) |
| Context | weather `forecast_not_yet_available` (dates beyond the forecast window), holidays live, currency live |
| LLM (Groq only) | anchor: success; reasoning: HTTP 400 malformed answer -> deterministic fallback; narrator: HTTP 429 -> deterministic fallback; no wasted same-provider retry |
| Readiness | `needs_review` |
| Requests | 45 in total: Geoapify 33 (35 credits, cap 100), Kiwi 8 (guard = client), Groq 3 (cap 8), Nager.Date 1 |

All automated checks passed; no credential value appeared in the report or the UI snapshot.

### 8.3 Both views (offline browser replay of the saved trip)

`frontend/e2e-replay/` replays the saved trip's API responses in the production frontend build (no backend, no
provider): Traveler view (airport note, prices, booking-link label, honest "Where to stay") and Developer view
(reasons, `commercial-service evidence`, HTTP request count, booking links, the route summary line) — 2/2 passed.
The first replay found a real Developer-view defect: the route-report summary was shown only when the plan had
no per-day route context, which a routed plan always has after this phase; it is now always shown
(`data-testid="route-report-summary"`).

## 9. Remaining limitations

- **Kiwi MCP terms** for public or commercial use remain unconfirmed (the terms pages could not be read). Kiwi stays
  disabled in production until they are checked and the owner approves.
- **OurAirports data** is supplied manually as a local file. Packaging it with the backend image, refreshing it
  (upstream publishes daily) and alerting on a stale copy are not built; the lookup stays `not_connected` in
  production. The data comes "with no guarantee of accuracy"; a stale `scheduled_service` flag would mislead the
  selection (each selection records the file's fingerprint). Service class is a coarse proxy for frequency, Kiwi
  takes one airport per side, and only one fallback search is made per route.
- **Bookable accommodation** sources remain unavailable (section 5): no free, permitted runtime source exists;
  accommodation stays `no_source_configured` unless a local file is supplied.
- **Groq** rate limits (429) and structural failures (400 `json_validate_failed` / malformed answers) still send
  stages to their deterministic fallback, as the end-to-end run showed for reasoning and the narrator. The retry is
  no longer wasted, but quality depends on Groq quota; Gemini failover was not exercised live in this phase.
- The browser replay renders saved data in Chromium only: no real map tiles, other browsers, screen readers or a
  live backend after the snapshot.
- A city name shared by several places can still be resolved to the wrong one by the geocoder.
