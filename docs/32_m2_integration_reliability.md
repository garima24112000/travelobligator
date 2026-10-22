# 32. M2 Phase 1 — Core integration reliability

Status: implemented on branch `m2/p0-1-provider-evidence`; **verified in development only (section 7), not
deployed**. Offline tests (mocked HTTP, fakes) and a bounded development run with real providers on throwaway
storage back the statements below. Nothing here claims an integration works in production: production still runs
the M1 release until this change is merged and deployed, and the run was not against production.

Scope: the six defects of the M2 integration audit (D1–D6). No new provider, no scraping, no planner change, no
production setting changed.

## 1. What was fixed

### D1 (P0) — holidays and currency for real destinations

Before: Nager.Date and Frankfurter inferred the country from the typed destination with an 11-name table
(`portugal`, `france`, …). "Kyoto, Japan", "Paris", "Istanbul, Turkey", "Prague, Czechia" all resolved to nothing,
so holidays and exchange rates were unavailable for most trips.

Now:

- `DestinationContextService` reads the ISO alpha-2 `country_code` from `places.describe_destination()` — the
  cached, plausibility-checked resolution the place searches already made. The call was **moved**, not added: no
  extra geocoding request. The code goes to `get_public_holidays(..., country_code=)` and
  `get_exchange_rate(..., country_code=)` (optional parameters on the provider base classes).
- Holidays: Nager.Date is asked for that country. Without a code (dev geocoder without one, unresolved destination)
  the old conservative name inference still applies.
- Currency: `app/utils/country_currency.py` maps ISO 3166-1 alpha-2 → ISO 4217, entity by entity from the ISO 4217
  list. A test checks that all 249 `pycountry` countries are accounted for exactly once and every code is a valid
  ISO 4217 code (ZWG and XCG are newer than the pinned `pycountry` data and are listed explicitly). Dated facts:
  HR and BG use EUR; CW/SX use XCG; ZW uses ZWG.
- Countries with more than one circulating currency (BT, HT, LS, NA, PA, SV, VE) get **no** rate and no request
  (`currency_ambiguous`). AQ, GS, PS ("no universal currency") get `currency_not_determined`.
- Same currency as the trip's budget currency: identity rate, no request (unchanged).
- Frankfurter HTTP 404/422 → `currency_not_supported` (confirmed live, section 6). Nager.Date answers an
  uncovered country with **HTTP 204** (observed live), the same answer it gives a covered country without data for
  a year. After an empty answer only, the adapter reads Nager.Date's own covered-country list
  (`/api/v3/AvailableCountries`, kept in memory and in the provider cache at the holiday TTL): not listed →
  `country_not_supported`; listed → `holiday_data_not_provided`; list unreadable → `holiday_data_not_provided`
  (nothing claimed). HTTP 404 still maps to `country_not_supported`. Cost: at most one extra request per process
  and cache period, only after a 204.

### D2 (P0) — flight availability reporting

Before: production's flight provider is `scraped_local` with no file, which reports `unavailable`; Traveler view
mapped every `unavailable` to "No flight offers were found for this trip's route and dates" — although no search
ever ran. The `not_connected` copy told travellers to "Connect or enable a flight provider".

Now `FlightSearchResult.availability_reason` (`FlightAvailabilityReason`, optional, additive):

| Reason | Set when |
| --- | --- |
| `offers_found` | at least one offer |
| `no_source_configured` | not connected, scraped provider off, or no local file (production today) |
| `local_source_no_matching_offers` | a local file exists but holds no offer for the trip |
| `search_not_possible` | Kiwi: no origin, IATA-only schema, zero adults, or no flight-search tool |
| `search_completed_no_offers` | Kiwi answered with an **empty itinerary list and no positive `resultsCount`** |
| `response_unusable` | Kiwi answered, but itineraries could not be parsed, or a count without items, or a malformed payload |
| `provider_failed` | discovery/call failed, tool error, local file unreadable, or an unexpected exception |

`FlightInventoryService.ensure_availability_reason` fills a missing reason only where the status proves it
(`not_connected` → no source, `failed` → provider failed); an unclassified `unavailable` stays `None` and is never
treated as a completed search. Traveler view (`travelerFlightAvailabilityNotice`) says "No flight offers were
found…" **only** for `search_completed_no_offers`; every other case has its own wording, none of it operator
vocabulary.

### D3 (P0) — change requests

Before: readiness said `can_regenerate=True` / "ready" whenever feedback was classified, but production has
`TARGETED_REGENERATION_ENABLED=false` and an intentionally empty `LEGACY_SUPPORTED_OPERATIONS`, so the regenerate
route refused every request with `REGENERATION_FEEDBACK_NOT_INTERPRETABLE`, and Traveler view told people to
"try rewording".

Now:

- `RegenerationReadiness.change_requests_applicable` / `change_requests_unavailable_reason` (optional, additive)
  come from `change_application_unavailable_reason()`, which mirrors the route's own branching: targeted on →
  needs a configured feedback interpreter (enabled + connected provider + its key present; presence only);
  targeted off → needs a registered deterministic operation for the pending requests.
- When unavailable, a would-be "ready" readiness becomes `blocked` (`can_regenerate=False`).
- `GET /trips/{id}/regeneration-readiness` applies the same check to the stored value without saving it, so a
  readiness stored as "ready" by an older build, or before a switch was turned off, never offers the action.
- With targeted regeneration on, the legacy "did not classify to a plan section" refusal no longer applies
  (the route sends targeted requests past that check).
- Traveler view: no "Apply next change" button when applying is unavailable; an explanation instead. Saving a
  request still works and its copy no longer promises it will be applied. The `REGENERATION_FEEDBACK_NOT_INTERPRETABLE`
  refusal no longer suggests rewording.
- Targeted regeneration was **not** enabled.

### D4 (P1) — partial Places failures

Before: a pool shrunk by failed Places requests produced "Only N verified place(s) … were found for this
destination".

Now the Geoapify adapter marks a PARTIAL answer `failure_reason="partial_request_failures"` **only** when some of
its batch's requests failed (a merely small result is PARTIAL without it). `ProviderStatusEntry.failure_reason`
(optional, additive) carries it into `provider_status`. `usefulness_contract.place_lookup_incomplete()` reads it
(or a FAILED places entry), and the insufficient-inventory finding and the inventory report then say the place
search did not complete and that this does not mean the destination has little to see. The code
(`INSUFFICIENT_VERIFIED_INVENTORY`), readiness and the schedule are unchanged.

### D5 (P1) — structured missing-data reasons

`WeatherContext`, `HolidayContext` and `CurrencyContext` have an optional `missing_reason`
(`app/providers/missing_data_reasons.py`): `forecast_not_yet_available`, `destination_coordinates_unavailable`,
`invalid_dates`, `country_unresolved`, `country_not_supported`, `currency_ambiguous`, `currency_not_determined`,
`currency_not_supported`, `request_failed`, `request_timed_out`, `malformed_response`, `provider_reported_error`,
`holiday_data_not_provided`, `no_usable_data`. Traveler view words each one (`weatherMissingNotice` etc.); an
unknown or absent reason falls back to the previous general notice. A service failure is never worded as a fact
about the destination.

Also fixed: a non-object JSON body from Open-Meteo raised `AttributeError` out of the adapter; it is now
`malformed_response`. Frankfurter failure logs no longer include the exception text (which could contain the URL).

### D6 (P1) — routing diagnostics

The planner's `route_feasibility_context`, implementation gaps and readiness checklist are written before routing
runs. With a routing provider configured (`routing_provider_is_configured()`: Geoapify + key, or OSRM + base URL)
they no longer say "no route provider is connected": the context states that it holds no route data and that a
transfer without a verified route in the route report is not verified; the checklist says route times are measured
after the day order is final (never "checked"). Developer view's "Route feasibility" section now summarises the
real `route_feasibility_report` (`routeReportSummary`): "All N transfers have a route" only when every leg
succeeded, otherwise "k of N … verified; the rest are not". Behaviour with routing not configured is unchanged.

## 2. API and compatibility

All new fields are optional with a `None` default, so states stored before this change load unchanged and old
clients ignore them: `WeatherContext/HolidayContext/CurrencyContext.missing_reason`,
`FlightSearchResult.availability_reason`, `RegenerationReadiness.change_requests_applicable` /
`change_requests_unavailable_reason`, `ProviderStatusEntry.failure_reason`. No migration (PlanningState is JSON).

Behaviour changes a client can observe: readiness reports `blocked` where it used to say `ready` in production's
configuration (that "ready" was false); `failure_reason` is now set on Nager.Date/Frankfurter/Open-Meteo request
failures (it was `None`).

Existing tests changed, and why (no assertion was weakened):

- Four readiness tests asserting "ready" now request the existing `synthetic_legacy_regeneration_support` fixture —
  "ready" requires a deployment that can apply the request, which is exactly D3.
- Exact key-set contract tests (`test_trips_smoke.py` ×3, `test_regeneration_readiness.py`) list the new fields;
  the context tests also assert the new reason values.
- `test_nager_real_failures_are_still_failures_and_are_never_cached` asserted `failure_reason is None`; it now
  asserts a request-failure reason and that it is not the neutral no-data reason.

## 3. Still unsupported (unchanged by this phase)

- Flights in production: no flight source is configured (`no_source_configured`). Kiwi MCP stays off.
- Change requests in production: cannot be applied (no deterministic operation, targeted regeneration off pending
  its own quality evaluation). Requests are saved.
- Transit, hotel ratings, bookable accommodation: no source; `not_connected`.
- Kiwi: price currency still falls back to the request currency when Kiwi omits one; the per-call timeout is a
  read timeout, not a total deadline; no usage accounting. Must be fixed before Kiwi is enabled.
- LLM routing order (Groq primary, mostly rate-limited in evaluation) is unchanged and unmeasured in production.

## 4. Final review corrections

A consolidated review of the change set found and corrected:

- **Validator flight wording (D2 leftover).** `_build_flight_inventory_warning` still said "The flight inventory
  provider was checked, but no flight offers were available" for production's no-source case. It now follows
  `availability_reason` (no source / search not possible / unusable response); the "was checked" wording remains
  only for a search that completed, and for older reports without a reason.
- **Expansion requests (D4).** A failed, or partly failed, inventory-expansion page was not recorded, so it could
  not trigger the incomplete-search wording. It is now recorded under `<provider>:places_expansion` (only when it
  failed) and read by `place_lookup_incomplete`.
- **Currency wording.** `currency_not_supported` blamed "this destination's currency"; Frankfurter's 404 can also
  mean the trip's budget currency is the unsupported side. The traveller copy now names neither.
- **Kosovo.** Geocoders return the user-assigned code `XK`; it is now mapped to EUR (documented as the one
  non-ISO 3166 key).
- **Traveler copy.** With applying unavailable, the apply section no longer says requests are "waiting".
- **Developer route section.** Its status line showed the plan-time context's `unavailable` beside a summary of
  verified routes; it now shows the route report's status once routing ran.

Reviewed and left as is: no new provider request was introduced (the `describe_destination` call was moved, and
the currency adapter sends nothing for same-currency, multi-currency or undetermined countries); readiness does
not reflect the `GENERATION_ENABLED` kill switch (the regenerate route refuses with 503 then — a known gap, not a
misleading "cannot apply" claim).

## 5. Bounded live verification (prepared, not yet run)

No live or paid request has been made. `backend/scripts/verify_m2_integrations.py` is the one development-only
verification. Dry run (default) prints the plan and sends nothing:

    cd backend
    python scripts/verify_m2_integrations.py
    GEOAPIFY_API_KEY=… GROQ_API_KEY=… python scripts/verify_m2_integrations.py --execute --out /tmp/m2_verify.json

What it checks: Geoapify `country_code` for five development cities (Nara JP, Gothenburg SE, Ljubljana SI,
Chiang Mai TH, Panama City PA — none in a frozen set; the script refuses frozen cities); holidays, exchange rate and
forecast through the application's adapters; the Nager.Date 404 and Frankfurter 404/422 assumptions (probes: `AQ`,
`VN`); the beyond-horizon weather path; and one 2-day end-to-end generation (Gothenburg) on throwaway storage,
read back for the M2 fields. Assumptions that do not hold are reported as `FAIL`.

Caps (worst case: every retry, redirect and year boundary taken), enforced on every httpx request of the process,
with a host allowlist (Kiwi MCP and any other host are refused before connecting):

| Service | Max requests |
| --- | --- |
| Geoapify | 105 (5 geocodes + the generation's 100-credit cap) |
| Open-Meteo | 12 |
| Nager.Date | 14 |
| Frankfurter | 14 |
| LLM (Groq / Gemini) | 8 (4 stages × at most 2) |
| **Total** | **153** |

Geoapify credits: at most 105. The first 401/403 from any service, or 429 from a data service, stops every further
request. `--skip-e2e` runs steps 1–3 only (at most 39 requests, 5 credits, no LLM key needed). Caveat: requests an LLM SDK
might make outside httpx are not counted (both SDKs in use send through httpx); the application's own per-stage
limit still bounds them.

## 6. First live run (`--execute --skip-e2e`) and corrections

Observed: 3 requests, none refused. Frankfurter 404 → `currency_not_supported` (pass). Nager.Date answered the
uncovered-country probe with 204, not the assumed 404 (fail). Every city: geocoding `not_connected`, zero Geoapify
requests, so no coordinates and no weather check.

Root causes and fixes:

- **Geoapify `not_connected` — a script defect plus a worktree configuration fact.** The application reads keys
  only from the process environment and from `<checkout>/.env`. In the M2 worktree that file does not exist (the
  developer key lives in the main checkout's `.env`, which a worktree does not read — by design; secrets are not
  copied). The script's hidden prompt then read the key into a local variable and never put it into the
  environment, so the settings saw no key and `geoapify_get` refused locally with `not_connected` before any
  request. Fixed: a prompted key is placed in the process environment; a `preflight` (presence only, no request,
  shown in the dry run) refuses `--execute` when the application settings cannot see the key.
- **Nager.Date 204** — handled as above (covered-country list), with regression tests for covered, uncovered,
  unreadable-list and cached-list cases.
- **Open-Meteo without coordinates** — the beyond-horizon probe used to be called with no coordinates and so
  returned `destination_coordinates_unavailable`, judged as a horizon failure. Weather is now requested only for a
  city whose coordinates resolved; otherwise the city and the probe report `skipped: no_coordinates` (check `n/a`).
- **Probes are selectable** (`--probe`), so the Frankfurter probe that passed is not repeated.

Revised rerun (from the worktree; export the key in the same shell, or type it at the hidden prompt):

    cd backend
    python scripts/verify_m2_integrations.py --skip-e2e --probe nager --probe open_meteo            # dry run: preflight must list no problems
    python scripts/verify_m2_integrations.py --skip-e2e --probe nager --probe open_meteo --execute --out /tmp/m2_verify_rerun.json

Caps for that rerun: at most 38 requests (Geoapify 5, Open-Meteo 10, Nager.Date 13, Frankfurter 10, LLM 0) and
5 Geoapify credits.

Still for later: Kiwi (only if it is ever to be enabled) needs its own run — discovery plus ≤3 searches, recording
offer count, `resultsCount`, currency presence and response keys — after explaining the earlier zero-offer result.

## 7. Development verification results (operator-run, as reported)

Run by the owner with `verify_m2_integrations.py` from the M2 worktree, development keys, throwaway storage;
recorded here from the owner's report (the JSON reports are kept outside the repository).

- Provider rerun (`--skip-e2e --probe nager --probe open_meteo`): Geoapify country codes 5/5; Open-Meteo forecasts
  5/5; Frankfurter rates for every applicable city; Nager.Date working, including the uncovered-country case
  (`country_not_supported` from a 204 plus the covered-country list). 25 requests, 5 Geoapify credits, no error,
  no refused request.
- End-to-end (Gothenburg, 2 days): all 9 script checks passed. 45 requests, 34 Geoapify credits, no refused request.

Not established by these runs: behaviour in production, other destinations, Kiwi, and LLM provider routing quality.
