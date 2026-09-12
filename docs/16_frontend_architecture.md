# Frontend Architecture

## 1. Purpose

This document defines the frontend architecture for TravelObligator.

The frontend should not behave like a simple itinerary display.

It should present the full decision pipeline behind the trip:

- traveler understanding
- destination context
- trip strategy
- stay area decision
- accommodation options
- transport strategy
- day-wise experience plan
- validation warnings
- provider coverage
- unavailable data
- feedback updates
- version history

The frontend should render from one central object:

```text
PlanningState
```

---

## 2. Core Frontend Principle

The frontend should not assemble the trip from many disconnected API responses.

It should render the current trip from:

```text
GET /trips/{trip_id}
```

which returns the latest `PlanningState`.

The frontend should treat PlanningState as the source of truth.

---

## 3. Main Frontend Goals

The frontend should help the user understand:

- what is recommended
- why it is recommended
- what tradeoffs exist
- what data was available
- what data was unavailable
- what assumptions were made
- whether the plan is ready, needs review, or blocked
- how the plan changed after feedback

The frontend should make the product feel like a travel decision platform, not just an itinerary generator.

---

## 4. Suggested Frontend Stack

Recommended MVP frontend stack:

```text
Next.js
React
TypeScript
Tailwind CSS
```

Optional later:

```text
Mapbox / Google Maps / Leaflet
React Query / TanStack Query
Zustand or Context API
shadcn/ui
```

---

## 5. Main Pages

Suggested pages:

```text
/
```

Landing or trip creation entry point.

```text
/trips/new
```

Trip creation form.

```text
/trips/[trip_id]
```

Main trip dashboard.

```text
/trips/[trip_id]/versions
```

Version history view.

Optional later:

```text
/trips/[trip_id]/map
/trips/[trip_id]/settings
```

---

## 6. Frontend User Flow

Normal flow:

```text
User fills trip form
→ POST /trips
→ user lands on dashboard
→ POST /trips/{trip_id}/generate
→ frontend shows planning progress
→ backend returns PlanningState
→ dashboard renders plan
```

Feedback flow:

```text
User gives feedback
→ POST /trips/{trip_id}/feedback
→ backend updates affected sections
→ dashboard rerenders updated PlanningState
→ version history records change
```

Lock flow:

```text
User clicks “Keep this”
→ POST /trips/{trip_id}/locks
→ item is protected from accidental regeneration
```

---

## 7. Frontend Data Model

Frontend TypeScript types should mirror backend API models.

The frontend should use `snake_case` to match API JSON and avoid unnecessary mapping.

Example:

```ts
type PlanningState = {
  planning_state_id: string;
  trip_request: TripRequest;
  traveler_profile: TravelerProfile | null;
  destination_context: DestinationContext | null;
  trip_strategy: TripStrategy | null;
  stay_transport: StayTransportDecision | null;
  experience_plan: ExperiencePlan | null;
  validation_report: ValidationReport | null;
  feedback_history: FeedbackEvent[];
  user_locks: UserLock[];
  decision_cards: DecisionCard[];
  experience_cards: ExperienceCard[];
  validation_cards: ValidationCard[];
  provider_status: Record<string, ProviderStatusEntry>;
  provider_coverage: ProviderCoverage;
  unavailable_data: UnavailableDataItem[];
  data_sources_used: string[];
  metadata: PlanningMetadata;
  version_history: VersionHistoryItem[];
};
```

---

## 8. API Client

Create a frontend API client.

Suggested file:

```text
frontend/lib/api.ts
```

Responsibilities:

- call backend endpoints
- handle success response shape
- handle error response shape
- expose typed functions
- avoid provider-specific frontend logic

Suggested functions:

```ts
createTrip(input)
getTrip(tripId)
generatePlan(tripId)
applyFeedback(tripId, feedback)
addUserLock(tripId, lock)
removeUserLock(tripId, lockId)
getVersions(tripId)
getProviderCoverage(tripId)
```

The frontend should not call provider APIs directly.

---

## 9. Main Dashboard Layout

The trip dashboard should have these sections:

```text
Trip Header
Planning Status
Provider Coverage Banner
Trip Strategy Summary
Stay + Transport Section
Accommodation Options Section
Experience Plan Section
Validation Section
Feedback Section
Version History Panel
```

Suggested layout:

```text
[Trip Header]
[Provider Coverage / Data Transparency Banner]
[Readiness Status]

[Why this trip plan]
[Where to stay]
[How to move around]
[Top accommodation options]
[Day-wise itinerary]
[Validation warnings]
[Feedback box]
[Version history]
```

---

## 10. Trip Header

Displays:

- destination
- dates
- trip duration
- traveler count
- group type
- budget range
- current version
- readiness status

Example:

```text
Washington DC
Aug 10 - Aug 13 · 3 days · Family trip
Version v1 · Needs Review
```

---

## 11. Planning Status Component

Component:

```text
PlanningStatus
```

Uses:

```text
planning_state.metadata.pipeline_status
planning_state.metadata.active_stage
validation_report.readiness_status
```

Possible statuses:

```text
draft
profile_created
destination_context_created
strategy_created
stay_transport_created
experience_plan_created
validated
needs_review
blocked
feedback_pending
updated_after_feedback
```

The frontend should clearly show when the plan is:

- ready
- needs review
- blocked

---

## 12. Provider Coverage Banner

Component:

```text
ProviderCoverageBanner
```

Purpose:

Show what data was available and what was not.

Uses:

```text
planning_state.provider_coverage
planning_state.unavailable_data
planning_state.data_sources_used
```

Example user-facing messages:

```text
Restaurant recommendations are based on OpenStreetMap data. Ratings and review counts are unavailable because no richer restaurant provider is connected.
```

```text
Accommodation results are based on open accommodation locations. Live prices and availability are unavailable because no approved accommodation provider is connected.
```

```text
Vacation-rental and Airbnb-style inventory are unavailable because no approved provider integration is connected.
```

This is important for trust.

The frontend must not hide missing provider coverage.

---

## 13. Data Status Labels

The frontend should show small labels for important data fields.

Allowed labels:

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

Examples:

```text
Price unavailable
Rating unavailable
Estimated duration
Open-data-backed
Provider-confirmed
Not connected
```

These labels prevent the user from mistaking unavailable or estimated data for confirmed facts.

---

## 14. Decision Cards

Component:

```text
DecisionCard
```

Used for:

- destination suitability
- trip strategy
- stay area recommendation
- transport strategy
- accommodation ranking explanation
- itinerary choices
- feedback changes

Fields:

```text
title
summary
reasons
tradeoffs
alternatives
confidence
data_sources
assumptions
claim_sources
```

Decision cards should answer:

```text
Why did the system recommend this?
What are the tradeoffs?
How confident is it?
What data supports it?
```

---

## 15. Trip Strategy Section

Component:

```text
TripStrategySection
```

Uses:

```text
planning_state.trip_strategy
decision_cards where stage = trip_strategy
```

Displays:

- destination suitability
- recommended trip style
- duration assessment
- budget assessment
- planning targets
- assumptions
- confidence

This section should make the user understand the planning philosophy before seeing the itinerary.

---

## 16. Stay + Transport Section

Component:

```text
StayTransportSection
```

Uses:

```text
planning_state.stay_transport
decision_cards where stage = stay_transport
```

Displays:

- recommended stay area
- alternative stay areas
- transport strategy
- tradeoffs
- confidence
- data coverage notes

This section should appear before the day-wise itinerary.

Reason:

```text
Where the traveler stays and how they move affects the itinerary.
```

---

## 17. Accommodation Options Section

Component:

```text
AccommodationOptionsSection
```

Uses:

```text
planning_state.stay_transport.accommodation_recommendations
provider_coverage
unavailable_data
```

Displays top accommodation options.

Each accommodation card should show:

- name
- accommodation type
- area
- price if available
- availability if available
- rating/review count if available
- why it fits
- tradeoffs
- data source
- confidence
- provider coverage limitations

Important:

The frontend should not say:

```text
Best hotels
```

It should say:

```text
Recommended accommodation options
```

The frontend should not imply price or availability is confirmed unless the data status says so.

If only OpenStreetMap POIs are available, show:

```text
Live prices and availability are unavailable.
```

If only hotel-like provider data exists, show:

```text
Coverage is limited to hotel-like inventory.
```

---

## 18. Experience Plan Section

Component:

```text
ExperiencePlanSection
```

Uses:

```text
planning_state.experience_plan
planning_state.experience_cards
planning_state.decision_cards
```

Displays:

- trip overview
- day summaries
- activities
- meal plans
- restaurant recommendations
- meal-area fallbacks
- estimated walking
- estimated cost
- transport notes
- warnings

The itinerary should feel like the final representation of the decision pipeline, not the whole product.

---

## 19. Daily Plan Component

Component:

```text
DailyPlanCard
```

Displays:

- day number
- date
- theme
- goal
- activities
- meal plan
- walking estimate
- travel time estimate
- cost estimate
- energy level
- warnings

Each day should begin with a summary:

```text
Today’s goal
Why this order
Expected walking
Estimated cost
Energy level
```

---

## 20. Experience Card Component

Component:

```text
ExperienceCard
```

Displays:

- experience name
- category
- priority
- estimated duration
- best time to visit
- why included
- tradeoffs
- nearby alternatives
- confidence
- data status labels

If duration is estimated, show:

```text
Estimated duration
```

Do not display estimated values as exact facts.

---

## 21. Meal Plan Component

Component:

```text
MealPlanCard
```

Displays either:

```text
Restaurant recommendation
```

or:

```text
Meal-area suggestion
```

For restaurant recommendations, show:

- name
- cuisine/category
- rating if available
- review count if available
- price level if available
- data source
- confidence

If OpenStreetMap-only data is used, show:

```text
Ratings and review counts unavailable.
```

If restaurant data is unavailable, show meal-area fallback instead of fake restaurants.

---

## 22. Validation Section

Component:

```text
ValidationSection
```

Uses:

```text
planning_state.validation_report
planning_state.validation_cards
```

Displays:

- overall score
- readiness status
- category scores
- critical issues
- warnings
- suggestions

Validation cards should be grouped by severity:

```text
Critical
Warnings
Suggestions
```

The frontend should not hide warnings just because the plan is usable.

---

## 23. Readiness Status UI

Allowed statuses:

```text
ready
needs_review
blocked
```

Suggested display:

```text
Ready
```

Plan can be shown confidently.

```text
Needs Review
```

Plan is usable, but some warnings should be reviewed.

```text
Blocked
```

Plan should not be presented as final until critical issues are resolved.

If status is `blocked`, the frontend should show the critical blockers near the top of the dashboard.

---

## 24. Safety-Related Planning UI

The frontend should not display direct safety scores.

Avoid labels like:

```text
Area risk label
Safety score: 94
```

Use wording like:

```text
Safety-related planning considerations
```

Examples:

```text
This route includes late-night walking.
```

```text
This option may require a long transfer after dark.
```

```text
Transit alignment is limited for this activity.
```

This matches the MVP safety policy.

---

## 25. Feedback Section

Component:

```text
FeedbackBox
```

Allows user to say things like:

```text
Make Day 2 lighter.
Show cheaper accommodation options.
Remove museums.
Add more food places.
Keep evenings free.
```

On submit:

```text
POST /trips/{trip_id}/feedback
```

The frontend should show:

- regeneration strategy
- changed sections
- unchanged sections
- new version
- validation status after update

---

## 26. User Lock UI

Component:

```text
KeepThisButton
```

Available on:

- stay area
- accommodation option
- experience
- restaurant
- day plan
- transport strategy

Action:

```text
POST /trips/{trip_id}/locks
```

Button labels:

```text
Keep this
Locked
Unlock
```

Locked items should be visually marked.

Example:

```text
Locked by you
```

The user should understand that locked items are preserved during future feedback unless there is a conflict.

---

## 27. Version History UI

Component:

```text
VersionHistoryPanel
```

Uses:

```text
GET /trips/{trip_id}/versions
```

Displays:

- version label
- created time
- created by
- summary
- changed sections
- feedback that caused the change

Example:

```text
v2 · Updated after feedback
Changed: Day 2 itinerary, validation report
Reason: User asked to make Day 2 less packed.
```

Later, the frontend may support comparing versions or restoring a previous version.

---

## 28. Provider Transparency Panel

Component:

```text
ProviderTransparencyPanel
```

(implemented today as `ProviderCoverageSection` in `frontend/app/page.tsx`)

Uses:

```text
GET /trips/{trip_id}/provider-coverage
```

Displays:

- data sources used
- provider coverage
- unavailable data
- fallback data used
- not-connected providers

Example:

```text
Used:
- OpenStreetMap
- OpenTripPlanner
- Open-Meteo

Unavailable:
- Airbnb inventory
- Vacation-rental prices
- Booking.com inventory

Not connected:
- Airbnb
- Booking.com
- Expedia/Vrbo
- Google Flights
```

This section helps the user understand what the system actually knows.

### 28.1 Summary Counts

At the top of the panel, show three counts computed from the already-loaded
`ProviderCoverageData` response — no extra API call:

- data sources used (`data_sources_used.length`)
- provider statuses (`Object.keys(provider_status).length`)
- unavailable data items (`unavailable_data.length`)

Alongside the counts, show this note:

```text
Unavailable data is shown instead of being guessed. The frontend does not
invent missing provider facts.
```

### 28.2 Provider Status Grouping

`provider_status` is a map keyed by provider (and sometimes provider +
coverage field). Group entries by `provider_type` into fixed sections, in
this order:

```text
Places
Weather
Holidays
Currency
Routes
Accommodation
Other
```

`routes` and `transit` provider types both group under "Routes". Any
`provider_type` value that isn't one of `places`, `weather`, `holiday`,
`currency`, `routes`, `transit`, or `accommodation` falls under "Other"
rather than being hidden or misclassified. A group with no entries is not
shown.

Each provider status card shows:

- the provider status key (the map key, e.g. `openstreetmap_places:restaurants`)
- `provider_name`
- `status`
- `data_status`
- `unavailable_fields` as a list of short labels, or the text
  "No unavailable fields reported." when the list is empty

The frontend may show a friendly display label for a known raw
`provider_name` (e.g. `openstreetmap_places` → "OpenStreetMap /
Overpass"), in the provider status cards and the "Data sources used" list,
but must always keep the raw `provider_name`/provider status key visible
alongside it, and must never infer or imply provider connectivity from the
label itself — only `provider_status`/`provider_coverage` say that. An
unrecognized `provider_name` displays as-is.

The same friendly-label rule applies to the raw `provider_coverage` field
keys shown in the coverage grid (Step 167E): `accommodations` displays as
"Accommodation-like location candidates (open data)" and `hotel_prices`
displays as "Bookable lodging inventory", each with the raw field key
still shown alongside it. These two labels are deliberately worded to
keep the two concepts visibly distinct — an open-data accommodation-like
location candidate (`accommodations`) is never a hotel price, rating,
availability, amenity, or booking link, and only `hotel_prices` could ever
represent those, from an official lodging inventory provider. When
`hotel_prices` is `not_connected`, `unavailable`, or `failed`, the card
adds an explanatory note: "No official lodging inventory provider is
connected yet. Prices, ratings, availability, amenities, and booking
links are unavailable unless returned by an official provider." An
unrecognized field key displays as-is.

### 28.3 Unavailable Data Cards

Each entry in `unavailable_data` is rendered as its own card with three
labeled fields:

- Field — `field`
- Reason — `reason`
- Status — `data_status`

### 28.4 "What This Means" Explanation

The panel includes a short, fixed explanation block (not derived from
provider data, so it never varies per trip):

```text
Provider-backed or open-data-backed fields can be shown.
Missing fields stay unavailable.
OpenStreetMap places do not provide ratings, prices, reviews, opening
hours, or booking availability unless those fields are explicitly
returned by the backend.
Route timing is unavailable unless a route provider is connected.
Open-data accommodation-like places are location candidates only, not
bookable hotel inventory. Bookable lodging prices, availability, ratings,
amenities, and booking links are unavailable unless returned by an
official lodging inventory provider.
```

### 28.5 Rendering Rule

The panel renders only fields already present on the backend-returned
`ProviderCoverageData` response (`provider_coverage`, `provider_status`,
`unavailable_data`, `data_sources_used`). It never fabricates a rating,
price, availability, opening hour, route time, review count, or booking
link, and it never implies a restricted or paid provider (Booking.com,
Airbnb, Expedia, Vrbo, Tripadvisor, Google Flights) is connected beyond
what `provider_coverage`/`provider_status` actually say. Provider coverage
being shown does not mean the plan is final — see the validation and
readiness sections for that.

---

## 29. Loading and Progress States

During generation, the frontend should show stage progress.

Possible stages:

```text
Creating traveler profile
Building destination context
Generating trip strategy
Choosing stay area and transport
Finding accommodation options
Building day-wise experience plan
Validating plan
Finalizing dashboard
```

The frontend should not show fake completed sections before the backend returns them.

If a provider is not connected, show that honestly instead of pretending it loaded.

### 29.1 Loading Animation: Decorative Shell, Backend-Polled Progress (Steps 163A-163C)

`TravelGenerationLoading` (`frontend/app/page.tsx`, introduced Step 163A)
renders an origin/destination pair, a moving plane emoji, a progress bar,
and rotating stage copy. **The plane and progress bar are always
decorative -- they never represent a real flight, a real flight route, or
a real route/travel time**, regardless of what drives the numbers behind
them.

As of Step 163C, `handlePlanTrip()` polls the real backend pipeline
stage-progress endpoint (`getGenerationProgress`, Step 163B, `GET
/trips/{trip_id}/generation-progress`, docs/11_api_contracts.md section
29) on a ~700ms interval while `POST /trips/{trip_id}/generate` is in
flight, and feeds the result into `TravelGenerationLoading` as optional
props (`progressPercent`, `stageLabel`, `progressMessage`,
`isRealBackendStageProgress`):

- **Before a `trip_id` exists** (the brief window before `createTrip()`
  resolves), no backend props are set, so the component falls back to its
  original Step 163A local-timer decorative loop.
- **Once a `trip_id` exists and polling returns data**, the component
  prefers the real `current_stage_label`/`message`/`progress_percent`
  values over the local timer loop, and shows a small "Using backend
  stage progress" note (only when the backend's own
  `is_real_backend_stage_progress` marker is `true`) -- but the "Loading
  animation only -- not live flight tracking." disclaimer stays visible
  either way, since the visual (plane, bar) is still decorative framing
  around real numbers, not a real flight visualization.
- **On completion**, `handlePlanTrip()` stops polling, does one final read
  to show the completed/100% backend state briefly, then renders the
  result -- `loadPlanResult` is still called exactly once, not on every
  poll tick.
- **On error**, polling stops and the existing error state renders exactly
  as before Step 163C.
- **A transient polling failure never fails generation** and never
  surfaces as a user-facing error -- it's silently ignored, and the
  animation just keeps showing whatever state it already had (or falls
  back to the local timer loop) until the next successful poll.

This is UI wiring only: no scheduling, validation, regeneration, or
provider/AI behavior changed by this polling, and no new travel fact is
ever introduced by it.

---

## 30. Error States

The frontend should handle:

- validation errors
- trip not found
- planning state not found
- provider failed
- provider not connected
- AI output invalid
- stage failed
- blocked itinerary

Error messages should be useful.

Example:

```text
Route data could not be verified. The itinerary can still be viewed, but route feasibility has lower confidence.
```

Example:

```text
Accommodation prices are unavailable because no approved accommodation pricing provider is connected.
```

---

## 31. Empty States

The frontend should have clear empty states.

Examples:

No accommodation price:

```text
Price unavailable
```

No restaurant rating:

```text
Rating unavailable
```

No route data:

```text
Route time unavailable
```

No provider connected:

```text
Provider not connected
```

No feedback history:

```text
No feedback changes yet.
```

No validation issues:

```text
No major issues found.
```

---

## 32. No-Mock Frontend Rule

The frontend must not hardcode fake travel facts.

Do not hardcode:

- fake accommodations
- fake restaurants
- fake ratings
- fake prices
- fake opening hours
- fake flight options
- fake provider results

Allowed frontend placeholders:

```text
Loading...
Unavailable
Not connected
Estimated
No data returned
```

Not allowed placeholders:

```text
Example Hotel · $199 · 4.8 stars
```

unless clearly inside documentation, test fixtures, or storybook mocks not used as product truth.

---

## 33. Suggested Component Structure

Suggested folder structure:

```text
frontend/
  app/
    page.tsx
    trips/
      new/
        page.tsx
      [trip_id]/
        page.tsx
        versions/
          page.tsx
  components/
    layout/
      AppShell.tsx
      PageHeader.tsx
    trip/
      TripHeader.tsx
      PlanningStatus.tsx
      TripStrategySection.tsx
      StayTransportSection.tsx
      AccommodationOptionsSection.tsx
      ExperiencePlanSection.tsx
      DailyPlanCard.tsx
      ExperienceCard.tsx
      MealPlanCard.tsx
      ValidationSection.tsx
      FeedbackBox.tsx
      VersionHistoryPanel.tsx
      ProviderCoverageBanner.tsx
      ProviderTransparencyPanel.tsx
    cards/
      DecisionCard.tsx
      ValidationCard.tsx
      DataStatusBadge.tsx
      ConfidenceBadge.tsx
    forms/
      TripCreationForm.tsx
    common/
      LoadingState.tsx
      ErrorState.tsx
      EmptyState.tsx
  lib/
    api.ts
    types.ts
    formatting.ts
    constants.ts
```

---

## 34. TypeScript Type Organization

Suggested file:

```text
frontend/lib/types.ts
```

Types should mirror backend models:

```text
TripRequest
PlanningState
TravelerProfile
DestinationContext
TripStrategy
StayTransportDecision
AccommodationOption
TransportStrategy
ExperiencePlan
DailyPlan
ExperienceItem
RestaurantOption
MealPlanItem
ValidationReport
BaseExplanationCard
DecisionCard
ExperienceCard
ValidationCard
FeedbackEvent
UserLock
ProviderCoverage
ProviderStatusEntry
PlanningMetadata
```

---

## 35. Frontend State Management

For MVP, frontend can use:

```text
React state
server actions
or React Query/TanStack Query
```

Recommended:

```text
TanStack Query
```

because the frontend will frequently:

- fetch Planning State
- refetch after feedback
- refetch after lock changes
- show loading status
- handle errors

The frontend should avoid duplicating Planning State into too many local states.

---

## 36. Rendering Rules

The frontend should follow these rendering rules:

- Render from PlanningState.
- Do not invent missing values.
- Show unavailable data explicitly.
- Show provider coverage when relevant.
- Show confidence for recommendations.
- Show assumptions when they affect recommendations.
- Show validation before final user confidence.
- Show locked items clearly.
- Show version changes clearly.
- Do not imply restricted providers were searched unless provider coverage says they were connected.
- Do not imply feedback-driven regeneration is available; regeneration is not implemented yet.

---

## 37. Design Principles

The frontend architecture should follow these principles:

- The dashboard should explain decisions, not just display an itinerary.
- PlanningState is the frontend source of truth.
- Cards make recommendations explainable.
- Provider coverage makes data limitations visible.
- Unavailable data should be clear, not hidden.
- Validation should be visible before the user trusts the plan.
- Feedback should update only affected sections where possible.
- Locked items should feel protected.

---

## 38. Regeneration Safety UI

Regeneration is not implemented yet, so this UI exists to explain the
current state honestly, not to offer a working "regenerate" action.

### Regeneration Readiness Display

Component:

```text
RegenerationReadinessSection
```

Reads `regeneration_readiness` via `GET /trips/{trip_id}/regeneration-readiness`
(fetched once inside `loadPlanResult`, alongside the rest of the plan) and
displays: status, `can_regenerate` (always rendered "No" today), current
version, would-create version, pending feedback count, active lock count,
required/available inputs, missing capabilities, blocked-by reasons, and
next step. Because `can_regenerate` is always `false`, no clickable
"Regenerate" action is ever rendered — only a disabled
"Regeneration unavailable" placeholder.

### Check Backend Refusal Button

A separate, explicitly-safe control inside the same section. Its label and
helper copy never imply regeneration will run:

```text
Button: "Check backend refusal"
Helper: "This only checks the backend refusal path. It will not
         regenerate or change the plan."
Loading: "Checking refusal..."
```

On click it calls `requestRegeneration(trip_id)`
(`POST /trips/{trip_id}/regenerate`). The backend always refuses today, so
the expected outcome is a caught `ApiRequestError` whose message is shown
verbatim. If the call unexpectedly resolves instead of throwing, the UI
shows a warning ("Unexpected success from regeneration endpoint. Please
verify backend behavior before trusting this.") rather than treating it as
a good outcome — it never claims regeneration happened.

### Regeneration Attempt Audit Display

Component:

```text
RegenerationAttemptAuditSection
```

Reads `regeneration_attempts` and renders each attempt's status,
requested-at time, current/would-create version, pending feedback count,
active lock count, reason code, and message. Helper copy: "This is an
audit trail of blocked regeneration requests. It does not contain
itinerary content and does not mean regeneration ran."

### State Refresh Rule After a Refusal Check

After the "Check backend refusal" call settles (success or failure), the
frontend calls `GET /trips/{trip_id}/regeneration-attempts` once and
updates **only** `result.regenerationAttempts`. This is the only frontend
state allowed to change as a result of clicking the button:

* `loadPlanResult` is not called.
* No other plan data is refreshed or refetched.
* `feedback_history`, `pending_feedback_summary`, `user_locks`,
  `version_history`, `plan_diff_preview`, `regeneration_readiness`, and
  every itinerary field stay exactly as they were before the click.
- The user should always know what the system knows, what it assumes, and what it could not verify.

## 39. Bookable Accommodation Inventory Panel

Component:

```text
AccommodationInventorySection
```

(`frontend/app/page.tsx`, Step 167E, docs/12_provider_architecture.md
section 44/45, docs/14_backend_architecture.md section 44)

Data source: `trip.planning_state.accommodation_inventory_report`, part of
the already-fetched `GET /trips/{trip_id}` response consumed by
`loadPlanResult` (no new API call is added). Backed by
`AccommodationInventoryReport`/`AccommodationOffer` in
`frontend/lib/types.ts`, mirroring the backend's
`AccommodationSearchResult`/`AccommodationOffer` (Step 167D).

Rendered directly after `ProviderCoverageSection` and directly before the
"Destination candidate accommodation POIs" candidate list, so the two
concepts sit visibly side by side.

### 39.1 Status line

Always shown, regardless of status:

```text
Bookable lodging inventory: Not connected
```

(`Connected`/`Unavailable`/`Failed` for the other possible
`AccommodationSearchStatus` values.) This never says "checked" or
"available" when the status is anything other than a `success` result
that actually carries at least one offer.

### 39.2 Not-connected / unavailable / failed state

Whenever `report` is `null`, or `status` is not `success`, or `status` is
`success` with zero offers, the panel shows exactly this note instead of
any offer list:

```text
No official lodging inventory provider is connected yet. Prices,
ratings, availability, amenities, and booking links are unavailable
unless returned by an official provider.
```

This is the only path today, since no real lodging provider is
connected (Step 167B's `not_connected` default).

### 39.3 Connected-with-offers state (not reachable today, but implemented honestly)

If `status === "success"` and `offers.length > 0`, each offer renders only
the fields actually present on it — `property_name`, `provider`,
optionally `source_name`, and, only when non-null, `nightly_price_amount`
+ `currency`, `rating`, `availability_status`, and `booking_url`. No
field is ever filled in with a placeholder, an estimate, or a value
copied from an unrelated OSM POI. This path cannot be exercised in the
current deployment (no real adapter exists yet), but the rendering logic
never fabricates a value if it ever is.

### 39.4 Rendering rule

The panel renders only fields already present on the backend-returned
`AccommodationInventoryReport`. It never fabricates a property, price,
availability, rating, amenity, cancellation policy, or booking link, and
it never converts an OSM accommodation-like location candidate
(`CandidatePoi`/`AccommodationSuggestion`, rendered separately by
`CandidatePoiSection`/`AccommodationSuggestionCard`/
`StayAreaAccommodationCard`) into a bookable offer. A closing line always
restates the boundary: "Open-data accommodation-like places are location
candidates only, not bookable hotel inventory."

### 39.5 TODO

Once a real accommodation inventory provider is connected
(`Settings.accommodation_provider` set to something other than
`"not_connected"`), verify this panel's connected-with-offers rendering
(39.3) against real provider data before treating it as production-ready
UI — it has only been exercised against an in-process fake provider in
backend tests so far, never a real adapter's actual response shape.

### 39.6 TODO: future scraped-data labeling

Section 168 (backend Step 168A, docs/12_provider_architecture.md section
46) adds a backend-only scraping policy/provenance foundation --
`ScrapingSourcePolicy`/`ScrapedDataProvenance`/`ScrapingSourceRegistry`.
Nothing from it is wired into any API response yet, so there is no
scraped data for the frontend to render today. **If a future step ever
surfaces scraped data here, it must never be shown as if it were
official provider-backed data (`AccommodationOffer` above,
`WeatherContext`, `HolidayContext`, `CurrencyContext`, etc.) or as an
OSM open-data location candidate.** It must instead be visibly and
separately labeled with its own provenance -- e.g. "Scraped from a
public page (experimental/fragile) — not verified" — carrying its
source name/URL and confidence (`experimental`/`fragile`) the same way
`AccommodationInventorySection` already carries its own source/status,
and it must never be merged into the same list or card as a
provider-backed or open-data fact.

Backend Step 168B (docs/12_provider_architecture.md section 47) adds a
static HTML parser (`parse_scraped_accommodation_html`) that can produce
an `AccommodationOffer` carrying `scraped_provenance`
(`data_status: "scraped_public_page"`, `confidence:
"experimental"/"fragile"`, `official_provider: false`) -- still nothing
the frontend receives today, since it isn't wired into `ProviderGateway`
or any API response yet. Whenever a future step does surface such an
offer, the UI must render it with visible labeling drawn directly from
`scraped_provenance` (its source name, `source_url`, and
`experimental`/`fragile` confidence) rather than folding it into
`AccommodationInventorySection`'s existing "Connected" success path
unlabeled -- a reader must always be able to tell a scraped, unverified
offer apart from one returned by an official, connected lodging
provider.

Backend Step 168C (docs/12_provider_architecture.md section 48) adds a
concrete, disabled-by-default `ScrapedAccommodationProvider` that can
produce exactly such an offer from a manually-supplied local HTML file
-- still nothing the frontend receives today (not wired into
`ProviderGateway`'s default construction). The labeling rule above is
unchanged and now has a real, if not-yet-connected, source: whenever any
scraped lodging data is ever surfaced here, it must show
`data_status: "scraped_public_page"` and its `scraped_provenance`'s
`experimental`/`fragile` confidence visibly, never presented as if it
came from a connected official provider.

Backend Step 168D adds a cache in front of that provider (docs/12_
provider_architecture.md section 49) so a repeated request can be
served from a prior parse instead of re-reading the local file. A
cached offer carries the exact same `data_status: "scraped_public_page"`
and `scraped_provenance` as the original parse -- if this data is ever
surfaced to the frontend, whether served fresh or from cache must make
no difference to how it's labeled: it must always show as
`scraped_public_page`/`experimental`/`fragile`, never as a fresher or
more official-looking result just because it came from cache.

### 39.7 Scraped Accommodation Display Rules (Step 168E)

Step 168E implements the labeling the section above called for.
`AccommodationInventorySection` now renders a `ScrapedProvenanceBadge`
for any offer carrying `scraped_provenance` (backend: `AccommodationOffer.
scraped_provenance`, Step 168B/168C -- `ScrapedAccommodationProvider`,
still disabled by default):

```text
SCRAPED PUBLIC PAGE · EXPERIMENTAL
This is not official-provider data. It has not been verified for
price, availability, rating, or booking-link accuracy.
Source: Example Test-Only Travel Blog · https://example.test/...
Parser: scraped_accommodation_provider_v1
```

Rules, all backend-data-driven -- nothing here is invented client-side:

- **Status line is unaffected by provenance.** `not_connected`/
  `unavailable`/`failed` still render exactly as Step 167E left them
  ("No official lodging inventory provider is connected yet...");
  scraped labeling only ever appears on a `success` result's individual
  offers.
- **The badge shows exactly four things**: the fixed label "Scraped
  public page", the confidence (`experimental` or `fragile`, whatever
  the backend returned -- an unrecognized value displays as-is), a fixed
  "not official-provider data...not verified" sentence, and the
  `source_name`/`source_url`/`parser_version` fields *only when the
  backend actually returned them* (`parser_version` is conditionally
  rendered; `source_url` is appended only when present).
- **Never implies official verification.** The badge's own wording is
  the explicit opposite claim; no other part of the offer card is
  altered by the presence of `scraped_provenance`.
- **Missing fields still never render as present** -- `nightly_price_
  amount`/`rating`/`booking_url` use the exact same `!== null`/truthy
  guards regardless of whether the offer is scraped or (hypothetically)
  official; a scraped offer missing a price still shows no price line at
  all, never a placeholder or a zero.
- **`booking_url` is rendered only if the backend actually returned
  one** -- unchanged from Step 167E, and this holds equally for a
  scraped offer: the parser never invents a booking link, so this branch
  simply doesn't fire for most scraped fixtures.
- **OSM accommodation-like location candidates remain fully separate.**
  `CandidatePoiSection`/`AccommodationSuggestionCard`/
  `StayAreaAccommodationCard` are untouched by this step and continue to
  render only open-data location candidates, never a price/rating/
  availability/booking link, scraped or otherwise -- the closing line in
  `AccommodationInventorySection` ("Open-data accommodation-like places
  are location candidates only, not bookable hotel inventory") still
  applies unchanged.

### 39.8 Default Provider Change Requires No Frontend Change (Step 168F)

Backend Step 168F (docs/12_provider_architecture.md section 51) made
`scraped_local` the default accommodation provider. This required no
frontend code change: `AccommodationInventorySection`'s existing status
handling already treated `not_connected`/`unavailable`/`failed`
identically (the `isConnectedWithOffers` check and the
`HOTEL_PRICES_NOT_CONNECTED_VALUES` set in `ProviderCoverageSection`
both already include `"unavailable"`), so a fresh installation with no
local HTML file present still renders the same honest "No official
lodging inventory provider is connected yet..." message it always did
-- it now reflects `status: "unavailable"` under the hood rather than
`"not_connected"`, a distinction the UI was already designed not to
care about. Once a real local file is configured and parsed, the
existing `ScrapedProvenanceBadge` labeling (section 39.7) applies
exactly as already documented.

### 39.9 Future Flight Inventory UI Note (Step 169A)

Backend Step 169A (docs/12_provider_architecture.md section 52) added a
flight inventory model/provider contract (`FlightSearchRequest`/
`FlightOffer`/`FlightSearchResult`, `FlightInventoryProvider`) with no
adapter, no `ProviderGateway` wiring, and no API exposure -- there is
nothing for the frontend to render yet, and no frontend file changed for
this step. Noted here only so a future flight inventory panel is built
with the same distinctions `AccommodationInventorySection` already makes
(sections 39.6-39.8): it must distinguish **unavailable** (no flight
provider connected, or a connected provider found nothing), **scraped**
(`FlightOffer.scraped_provenance` set, `data_status=scraped_public_page`
-- rendered with the same "not official-provider data...not verified"
badge treatment as `ScrapedProvenanceBadge`, never implying verification),
and **official** (a real future flight API integration, once one exists)
data, and must never render a fabricated price, schedule, or booking link
for a missing field.

Backend Step 169B (docs/12_provider_architecture.md section 53) added
flight provider config, a `NotConnectedFlightProvider`, and a
`ScrapedLocalFlightProvider` *stub* (no parser yet, so it never returns
a real offer either) plus a `get_flight_provider` factory -- still no
API exposure, no `ProviderGateway` wiring, and no frontend file changed
for this step either. The same three-way distinction from 39.9 still
applies once a flight panel exists: **unavailable** (not connected, or a
connected provider found nothing -- which, as of Step 169B, is the only
outcome `ScrapedLocalFlightProvider` can actually produce, since its
parser doesn't exist yet), **scraped** (honestly labeled non-official,
review-worthy), and **official** data must never be blurred together in
the UI.

Backend Step 169C (docs/12_provider_architecture.md section 54) added
the flight HTML parser itself (`parse_scraped_flight_html`) -- still no
API exposure, still not wired into `ScrapedLocalFlightProvider` (that's
Step 169D), and no frontend file changed. Restating the labeling
requirement now that a real parser exists: once a scraped flight offer
does reach the frontend (Step 169D or later), it must render with the
same explicit, clearly-labeled treatment `ScrapedProvenanceBadge`
already gives accommodation offers (section 39.7) -- a fixed
"Scraped public page · Experimental/Fragile" badge and a "not
official-provider data...not verified" sentence, never blended into the
surrounding UI as if it were a confirmed, bookable flight.

Backend Step 169D (docs/12_provider_architecture.md section 55) wired
`ScrapedLocalFlightProvider` to actually parse a configured local file
and cache the normalized result -- still no API exposure and no
frontend file changed. This makes the labeling requirement concrete
rather than hypothetical: a real `FlightSearchResult` with real
`scraped_public_page` offers can now exist (given a manually-supplied
local file), whether served from a fresh parse or a cache hit -- both
carry identical provenance (section 55's cache round-trip guarantee), so
a future flight panel must label them identically too. Whenever flight
data does reach the frontend, cached scraped flight data must be labeled
`scraped_public_page`/`experimental`/`fragile` exactly like a
freshly-parsed one -- a cache hit is never a signal of higher trust.

### 39.10 Flight Inventory Panel (Step 169E, final Section 169 step)

`FlightInventorySection` (`frontend/app/page.tsx`) is the flight
equivalent of `AccommodationInventorySection` (sections 39.6-39.9),
rendering `PlanningState.flight_inventory_report`
(`FlightInventoryReport`, `frontend/lib/types.ts`) now that the backend
actually populates it end to end (docs/12_provider_architecture.md
section 56). Display rules, all backend-data-driven -- nothing here is
invented client-side:

- **Status line** reads "Flight inventory: {label}" where the label is
  `flightInventoryStatusLabel(status, hasScrapedOffers)` --
  "Unavailable"/"Failed"/"Not connected" for those statuses, and for
  `success` either "Available from scraped public page" (when any
  offer carries `scraped_provenance`) or "Connected" (a hypothetical
  future official-provider success). `not_connected`/`unavailable`/
  `failed` all render the same honest "No official flight inventory
  provider is connected yet..." explanatory line -- the UI does not
  distinguish them further, mirroring `AccommodationInventorySection`'s
  own `isConnectedWithOffers` collapsing of those three statuses.
- **Missing fields stay hidden entirely, never a placeholder.** Each
  optional offer/segment field (carrier name/code, flight number,
  origin/destination airport, departure/arrival time, duration, total
  price + currency, availability status, baggage policy, cancellation
  policy, booking URL) is rendered only behind an explicit
  `!== null`/truthy guard -- a missing field produces no line, never a
  placeholder, a zero, or an "unknown" stand-in for a real fact.
- **`ScrapedFlightProvenanceBadge`** renders for any offer with
  `scraped_provenance` set: a fixed "Scraped public page ·
  {Experimental/Fragile}" label, a fixed "not official-provider
  data...not verified" sentence, and `source_name`/`source_url`/
  `parser_version` only when the backend actually returned them --
  structurally identical to `ScrapedProvenanceBadge` (accommodation),
  kept as a separate component only to stay type-matched to
  `ScrapedFlightProvenance` vs. `ScrapedAccommodationProvenance`. This
  never implies official verification unless a future official flight
  provider replaces the scraped path entirely and `scraped_provenance`
  is genuinely absent from that offer.
- **`booking_url` is rendered only if the backend actually returned
  one**, mirroring the accommodation panel exactly.
- **Never shown as an itinerary day-card entry.** `FlightInventorySection`
  is a standalone panel among the "Data sources and candidates" section,
  rendered immediately after `AccommodationInventorySection` -- no
  `DailyPlan`/`ExperienceItem`-rendering component reads
  `flight_inventory_report`, and a flight offer is never merged into a
  day's scheduled experiences.
- **Accommodation and OSM labels are unchanged.** This step touches only
  the new `FlightInventorySection` plus the new type imports it needs --
  `AccommodationInventorySection`, `ScrapedProvenanceBadge`,
  `CandidatePoiSection`, and every OSM-candidate-rendering component are
  untouched.

### 39.11 Future AI Candidate Review Panel Note (Step 170A)

Step 170A adds a backend-only, read-only report endpoint
(`GET /trips/{trip_id}/ai-candidate-review`, docs/13_llm_reasoning_pipeline.md
section 80, docs/14_backend_architecture.md section 57) exposing the
existing Step 157A-161B AI candidate discovery/grounding shadow-mode state
in a reviewable shape. **No frontend component was added or changed in
this step** -- `frontend/app/page.tsx` does not call this endpoint yet, and
`frontend/lib/types.ts` gained no new type for it.

A future step (Step 170E) is expected to add an `AICandidateReviewSection`
panel mirroring `AccommodationInventorySection`/`FlightInventorySection`'s
existing display conventions: an honest status line, per-candidate rows
showing whether each AI-proposed candidate is provider-grounded, and its
`rejection_reasons`/`warnings` rendered verbatim. No candidate from this
panel is ever merged into a day's scheduled experiences.

**Step 170B update**: `eligible_for_promotion` can now genuinely be `true`
for some items (docs/13_llm_reasoning_pipeline.md section 81,
docs/14_backend_architecture.md section 58) -- a deterministic rules
result, not an AI judgment. The future panel (Step 170E) should render
eligible and non-eligible candidates as two clearly distinguished groups
(e.g. "Eligible for future promotion" vs. "Not eligible"), each row backed
by real fields already returned by the endpoint:
`eligibility_reasons`/`quality_bucket`/`grounding_status` for an eligible
item, `rejection_reasons`/`warnings` for one that isn't. **`true` here must
never be rendered as "added to your itinerary," "booked," or "confirmed"**
-- Step 170B only computes eligibility; nothing promotes a candidate into
a day's schedule until (and unless) a future step implements that
separately, and the panel must not imply otherwise.

**Step 170C update**: `POST /trips/{trip_id}/ai-candidate-promotions`
(docs/13_llm_reasoning_pipeline.md section 82,
docs/14_backend_architecture.md section 59) now exists on the backend and
stores `PlanningState.ai_candidate_promotion_report`, but **no frontend
component was added or changed in this step** -- `frontend/app/page.tsx`
does not call this endpoint yet, and `frontend/lib/types.ts` gained no new
type for it. The future Step 170E panel is expected to show promoted vs.
skipped candidates as two clearly distinguished groups (mirroring the
eligible/non-eligible grouping already planned above), each promoted row
backed by real fields the endpoint already returns
(`quality_bucket`/`grounding_status`/`promotion_reasons`/
`provider_place_id`/`provider_source`), and each skipped id cross-referenced
back to its `AICandidateReviewItem` for the specific reason it wasn't
promoted. **A promoted candidate must never be rendered as an itinerary
stop, a booking, or a scheduled day item** -- Step 170C only stores a
promotion report; nothing schedules a promoted candidate into a day until
a future step implements that separately, and the panel must not imply
otherwise.

**Step 170D update**: a promoted candidate *can* now actually be scheduled
into a day's `experiences` (docs/13_llm_reasoning_pipeline.md section 83,
docs/14_backend_architecture.md section 60) -- when
`ExperiencePlannerService`'s existing geographic/quality/pace rules
naturally pick it, exactly like any real provider candidate. **Still no
frontend component was added or changed in this step** --
`frontend/app/page.tsx` does not render this distinction yet, and
`frontend/lib/types.ts` gained no new fields for it. When a future step
adds this to the itinerary day-card rendering, it should mark a scheduled
item with `promoted_from_ai=true` distinctly from a normal
provider-backed one (e.g. a small badge/label referencing
`ai_candidate_promotion_report`, similar in spirit to
`ScrapedProvenanceBadge`) -- never silently indistinguishable from, and
never implying more certainty than, a normal scheduled experience. The
`original_ai_candidate_id`/`provider_place_id`/`provider_source` fields on
`ExperienceItem` exist specifically so such a future badge can link back
to the underlying promotion/review data without inventing anything new.

### 39.12 AI Candidate Review/Promotion Panel and Itinerary Badge, Implemented (Step 170E, final Section 170 step)

Step 170E implements everything section 39.11 anticipated. `frontend/lib/types.ts`
gained `AICandidateReviewItem`/`AICandidateReviewReport`/`AICandidateReviewData`/
`PromotedAICandidate`/`AICandidatePromotionReport`/`AICandidatePromotionData`,
plus `promoted_from_ai`/`original_ai_candidate_id`/`provider_place_id`/
`provider_source` on `ExperienceItem` and `ai_candidate_promotion_report`
on `TripData.planning_state`. `frontend/lib/api.ts` gained
`getAiCandidateReview`/`promoteAiCandidates`.

- **`AICandidateReviewSection`** (`frontend/app/page.tsx`) is rendered in
  the "Data sources and candidates" group, immediately after
  `FlightInventorySection`. It shows total/grounded/ungrounded/eligible
  counts (plus promoted/skipped counts once a promotion report exists),
  a "Refresh AI promotion report" button (calls `promoteAiCandidates` and
  updates `PlanResult.aiCandidatePromotionReport` via the same
  `setResult((previous) => previous ? {...} : previous)` pattern every
  other mutation-triggering panel on this page already uses), and four
  candidate groups: "Eligible for scheduling", "Not eligible", "Promoted
  candidates", "Skipped candidates". Each candidate card
  (`AICandidateReviewCard`/`PromotedAICandidateCard`) shows name/category/
  source, `quality_bucket`/`grounding_status` only when present, and
  `eligibility_reasons`/`rejection_reasons`/`warnings`/`promotion_reasons`
  verbatim. With no AI candidate data at all
  (`status="no_candidate_data"`, the default -- shadow mode is off by
  default), the panel renders a single honest line ("No AI candidate data
  is available for this trip yet.") instead of the stat grid/groups --
  verified by loading a real generated trip end to end in a browser.
- **`AIPromotedBadge`** renders inside `ScheduledExperienceCard` only when
  `experience.promoted_from_ai === true`, showing the fixed label
  "AI-suggested · Provider-grounded" plus `provider_source`/
  `original_ai_candidate_id` only when returned. A normal
  provider-backed experience (every experience produced by default, since
  shadow mode is off) never renders this badge -- confirmed visually: a
  real generated Lisbon trip's scheduled experiences showed no badge at
  all.
- **Wording discipline**: neither the panel nor the badge ever implies a
  confirmed booking, independent verification by the travel provider, a
  certainty claim, a safety judgment, a settled final decision, or
  official-provider status (none exists for AI candidates). Approved
  terms used throughout: "AI-suggested", "Provider-grounded", "Eligible
  for scheduling", "Promoted candidate", "Needs review".
- **No fabricated facts**: no rating, price, opening hour, route,
  distance, duration, or booking link field exists on any of these new
  types or components, matching the backend models they mirror exactly.
- **Scheduling stays backend-owned**: clicking "Refresh AI promotion
  report" only calls the existing `POST /trips/{trip_id}/ai-candidate-promotions`
  endpoint and updates local state with its response -- it never adds a
  candidate to `dailyPlans` itself, and never calls
  Groq/Anthropic/OpenAI/an AI candidate proposal provider.

### 39.13 No Frontend Change (Step 171A)

Step 171A (Section 171, start) adds a backend-only LangGraph orchestration
skeleton (`backend/app/graphs/planning_graph_state.py`,
`planning_graph_nodes.py`, `planning_graph.py`, docs/13_llm_reasoning_
pipeline.md section 85, docs/14_backend_architecture.md section 62)
representing the planning pipeline as graph nodes. **No frontend file was
added or changed in this step** -- `frontend/app/page.tsx`,
`frontend/lib/types.ts`, and `frontend/lib/api.ts` are all untouched. The
graph is not wired into `POST /trips/{trip_id}/generate` yet, so there is
nothing new for the frontend to call or render; `loadPlanResult`'s
existing `Promise.all` fetch group and every panel on this page keep
working exactly as they did after Step 170E.

### 39.14 No Frontend Change (Step 171B)

Step 171B adds a backend-only `LangGraphPlanningService`
(`backend/app/services/langgraph_planning_service.py`, docs/13_llm_reasoning_
pipeline.md section 86, docs/14_backend_architecture.md section 63) that
can run the Step 171A planning graph end to end with injected services.
**No frontend file was added or changed in this step** -- same as Step
171A, this service is not wired into `POST /trips/{trip_id}/generate`, so
there is nothing new for the frontend to call or render.

### 39.15 No Frontend Change (Step 171C)

Step 171C adds a read-only backend endpoint,
`POST /trips/{trip_id}/langgraph-shadow-run` (docs/13_llm_reasoning_pipeline.md
section 87, docs/14_backend_architecture.md section 64), that runs the
LangGraph planning graph against an isolated copy of a trip's
`PlanningState` for inspection. **No frontend file was added or changed
in this step** -- `frontend/lib/api.ts`/`types.ts`/`app/page.tsx` are all
untouched, and nothing on this page calls the new endpoint yet.

### 39.16 No Frontend Change (Step 171D)

Step 171D adds a backend-only config field
(`Settings.planning_engine_mode`) and a new `PlanningOrchestrator` method
(`generate_full_plan_via_langgraph`) so `POST /trips/{trip_id}/generate`
can optionally run through the LangGraph planning graph
(docs/13_llm_reasoning_pipeline.md section 88, docs/14_backend_architecture.md
section 65). **No frontend file was added or changed in this step** --
`frontend/lib/api.ts`/`types.ts`/`app/page.tsx` are all untouched. The
frontend already calls `POST /trips/{trip_id}/generate` and renders
whatever `PlanningState` comes back; the response shape is unchanged
regardless of which engine mode produced it, so this step needed no
frontend awareness of the new config field at all. `npm run lint`/
`npm run build` both still pass unmodified.

### 39.17 No Frontend Change (Step 171E, final Section 171 step)

Step 171E extends the LangGraph planning graph with the remaining stage
nodes needed for parity with legacy generation, then flips
`Settings.planning_engine_mode`'s default from `"legacy"` to
`"langgraph"` (docs/13_llm_reasoning_pipeline.md section 89,
docs/14_backend_architecture.md section 66). **No frontend file was added
or changed in this step** -- `frontend/lib/api.ts`/`types.ts`/
`app/page.tsx` are all untouched. `POST /trips/{trip_id}/generate`'s
response envelope (`{trip_id, planning_state}`) and every field on
`PlanningState` the frontend already renders from are exactly the same
shape regardless of which engine produced them -- the frontend consumes
the same API shape either way and needed no changes to keep working.
`npm run lint`/`npm run build` both still pass unmodified.

### 39.18 Stable Itinerary Order Metadata Typed, Not Yet Rendered (Step 172A)

Step 172A makes route-aware scheduling the backend default and adds
stable ordering metadata to `ExperienceItem` (backend:
`app.models.planning_state.ExperienceItem`) -- `day_number`,
`stop_order`, and `route_aware_provenance` -- so a later Step 172 step
can render numbered stops ("1. Stop A", "2. Stop B", ...) reliably
(docs/13_llm_reasoning_pipeline.md section 90,
docs/14_backend_architecture.md section 67).

- **The only frontend change in this step is a type update.**
  `frontend/lib/types.ts`'s `ExperienceItem` type gains the three new
  fields (`day_number: number | null`, `stop_order: number | null`,
  `route_aware_provenance: string | null`) so the hand-maintained type
  mirror stays in sync with the backend response shape (CLAUDE.md's
  "keep both in sync manually" rule) -- required because these are new
  fields on an existing response shape, not because any rendering needs
  them yet.
- **`frontend/app/page.tsx` is completely unchanged.** No day/stop is
  numbered visually yet -- these fields are present in every
  `GET`/`POST .../generate` response today, but nothing on the page reads
  them. A later Step 172 step is expected to use `day_number`/
  `stop_order` (and, when present, `route_aware_provenance`) to render
  the itinerary as numbered stops with optional movement/transition info
  between them, matching the shape:
  ```text
  1. Stop A
     travel/movement info if available
  2. Stop B
     travel/movement info if available
  3. Stop C
  ```
  `npm run lint`/`npm run build` both still pass with only the type
  addition.

A future step could add a small, clearly-labeled panel showing the graph
execution trace (`completed_nodes`/`failed_nodes`/`errors`/`warnings`
from the shadow-run response) for comparison against the trip's official,
`/generate`-produced plan -- but it must render `persisted: false`
honestly (e.g. "Shadow run preview -- not saved") and must never let a
user mistake the shadow `planning_state` preview for the trip's actual,
stored plan, or trigger any itinerary update from it.

### 39.19 Numbered Itinerary Stop Order Rendered (Step 172B, final Section 172A/172B step so far)

Step 172B renders the `day_number`/`stop_order`/`route_aware_provenance`
metadata Step 172A added (section 39.18) as numbered stops in each
itinerary day card -- `frontend/app/page.tsx` reads these fields for the
first time. Full map *path* visualization (turn-by-turn route geometry)
remains out of scope -- that is Section 173; `DayMapPreview`'s existing
dotted straight-line connector between markers (pre-dating this step) is
unchanged.

- **The backend remains the sole source of truth for sequencing.** The
  day loop still renders `day.experiences` in the exact order the
  backend returned it -- the frontend never calls `.sort()` or otherwise
  reorders that array itself. Each `ScheduledExperienceCard`'s
  `orderNumber` prop is `experience.stop_order ?? index + 1`: the
  backend's own `stop_order` is preferred whenever it is set (which is
  every experience scheduled since Step 172A), and only a `null`
  `stop_order` (e.g. a state persisted before Step 172A) falls back to
  the 1-based array-index numbering the app already used. `DayMapPreview`'s
  marker labels use the identical `stop_order ?? index + 1` expression,
  so the numbered list and the map markers can never disagree.
- **Route-order wording stays honest and unalarming.** Above each day's
  list of scheduled-experience cards, a small caption reads "Suggested
  stop order" by default, or "Provider-grounded route order" only when
  at least one experience in that day has
  `route_aware_provenance === "provider_backed"` (i.e.
  `RouteAwareSequencingService.apply_report` actually reordered that day
  with real routing-provider data, Step 172A). A `null`
  `route_aware_provenance` -- the common case, since most environments
  have no routing
  provider connected -- shows the neutral default wording, never an
  "unavailable"/alarming message: the frontend has no `route_aware_
  sequencing_report.status` field in its fetched data to justify that
  claim, so it doesn't make one. The existing caption below the list
  (already there before this step) gained one clause: "Stop numbers
  reflect the backend's current schedule order only -- not a claim about
  route certainty, safety, or speed" -- deliberately reworded to steer
  clear of this codebase's own forbidden-phrase guardrail list, even
  while explaining the same thing in negative framing.
- **No movement/travel-time/distance data is rendered.** This step does
  not fetch or display `route_feasibility_report`/
  `travel_time_buffer_report`/`route_aware_sequencing_report` at all --
  those aren't in `TripData`'s declared shape, and adding them is out of
  scope here. No dummy "travel time to next stop" row, walking/driving/
  transit estimate, or distance figure was added anywhere.
- **The Section 170 AI-promoted badge is completely unchanged.**
  `AIPromotedBadge` (rendered when `experience.promoted_from_ai` is
  `true`) and `ScheduledExperienceCard`'s existing provider-grounding
  wording are untouched by this step -- a promoted experience gets the
  exact same `stop_order ?? index + 1` numbering as any other scheduled
  experience, plus its existing AI-suggested/provider-grounded badge
  underneath.
- **Verified live**, not just via `npm run build`: a real generated trip
  (deterministic OpenStreetMap-backed candidates, no routing provider
  connected) was loaded in a browser and showed "Suggested stop order"
  with stops numbered 1/2/3 matching both the card list and the map
  markers, and no fabricated movement data anywhere on the page.

### 39.20 Route-Aware Sequencing Status and Movement Transparency (Step 172C)

Step 172C adds a compact, honest route-aware sequencing status to the
"Day-wise experiences" card, reading three backend fields the frontend
never fetched before -- `PlanningState.route_aware_sequencing_report`
and `PlanningState.route_feasibility_report` (both already serialized on
every `GET`/`POST .../generate` response; only `frontend/lib/types.ts`'s
type mirror was missing them) and the already-typed
`experience.route_aware_provenance` (Step 172A). Full map *path*
visualization (turn-by-turn route geometry) remains deferred to Section
173, unchanged from Step 172B's scope note.

- **Per-day status label, now three-way.** `routeAwareDayStatusLabel`
  (`frontend/app/page.tsx`) extends Step 172B's two-way caption:
  - **"Provider-grounded route order"** -- unchanged from Step 172B --
    shown when at least one experience in that day has
    `route_aware_provenance === "provider_backed"` (a real,
    provider-backed reorder was actually applied to that day).
  - **"Route order needs review"** (new) -- shown only when no
    provider-backed reorder happened for that day, *and* that day's own
    entry in `route_aware_sequencing_report.suggestions` (matched by
    `day_index === day.day_number`) honestly reports
    `partial`/`unavailable`/`failed` -- i.e. a real sequencing attempt
    was made and ran into a genuine issue.
  - **"Suggested stop order"** -- the safe default -- covers a missing
    report, no matching day suggestion, or a `not_connected`/
    `success`-but-unapplied day suggestion. This remains the common case
    in any environment with no routing provider connected.
- **A new plan-level movement-data note.** A fixed, always-safe
  sentence -- "Route-aware sequencing uses provider-backed movement data
  when available. If unavailable, the itinerary keeps a fallback
  order." -- sits once above the day list. Below it, "Movement data
  unavailable for this trip -- no connected routing provider could
  supply real distances or travel times between stops." appears only
  when `movementDataIsUnavailable(routeFeasibilityReport)` is true (no
  report at all, or its `status` is `not_connected`/`unavailable`/
  `failed`) -- never shown when real route-feasibility data exists
  (`success`/`partial`), since showing it then would understate what is
  actually available.
- **No numeric travel-time/distance/route figure is ever rendered.**
  The new `RouteAwareSequencingReport`/`RouteFeasibilityReport` frontend
  types (`frontend/lib/types.ts`) deliberately declare only `status`-
  level fields (plus `suggestions[].day_index`/`status`/`applied` for
  the per-day report) -- `route_duration_seconds`/`route_distance_meters`/
  `improvement_seconds` and `RouteLegFeasibility`'s own distance/duration
  fields are intentionally left undeclared, since nothing in this step
  displays them. No dummy movement row between stops was added.
- **Sequencing/numbering ownership is unchanged.** The frontend still
  never reorders `day.experiences` itself, and `ScheduledExperienceCard`/
  `DayMapPreview`'s `stop_order ?? index + 1` numbering (Step 172B) is
  untouched. The Section 170 `AIPromotedBadge` and its wording are
  likewise completely untouched.
- **Backend change: none.** Both new report fields were already present
  on the wire (`PlanningState` already serializes them); this step is a
  frontend type-and-render addition only.

### 39.21 Stop-to-Stop Movement Transparency (Step 172D)

Step 172D adds a small movement row between consecutive scheduled-
experience cards, showing whatever provider-backed travel-time/distance
data the backend already computed for that specific leg -- reading
`PlanningState.travel_time_buffer_report` (Step 166C), which was already
serialized on every `GET`/`POST .../generate` response before this step;
only `frontend/lib/types.ts`'s type mirror was missing it (new
`TravelTimeBuffer`/`TravelTimeBufferReport` types, declaring only
`from_experience_id`/`to_experience_id`/`provider`/`status`/
`route_duration_seconds`/`route_distance_meters` -- no travel *mode*
field exists on the backend model at all, so none is declared or
rendered). Full map *path* visualization (turn-by-turn route geometry,
polylines) remains deferred to Section 173, unchanged from Step
172B/172C's scope notes -- this step draws no polyline of its own.

- **A row renders only when the backend already has a matching entry.**
  `TravelTimeBufferService` builds one `TravelTimeBuffer` per consecutive
  pair of scheduled experiences in every day, regardless of whether a
  routing provider is connected -- `findMovementBetweenStops` (`frontend/
  app/page.tsx`) looks that entry up by `(from_experience_id,
  to_experience_id)`, and `shouldRenderMovementRow` renders a `MovementRow`
  only when a match exists. A missing report (e.g. an older persisted
  trip from before Step 166C) or no match for a given pair renders
  nothing -- never a dummy row.
- **`formatMovementSummary` maps every real `TravelTimeBufferStatus`
  value onto one safe line, never inventing a fact the backend didn't
  provide:**
  - `success` with a real duration: "Provider-backed movement data:
    ~N min · D.D km (via provider)" -- `route_duration_seconds`/
    `route_distance_meters` are only ever shown when the backend itself
    set them (never `null`-filled). `formatDurationSeconds`/
    `formatDistanceMeters` only convert units (seconds -> minutes,
    meters -> kilometers) on a real backend number -- the frontend never
    computes a distance or duration from `experience.coordinates`
    itself.
  - `not_computable` (one or both stops missing coordinates, so the
    routing provider was never even called): "No movement details
    returned".
  - `failed` (a real request was attempted and broke): "Route details
    need review".
  - `not_connected`/`unavailable`/anything else: "Movement data
    unavailable" -- the common case in any environment with no routing
    provider connected.
- **Sequencing, numbering, and route-aware status are all unchanged.**
  The day loop still renders `day.experiences` in exactly the order the
  backend returned it; inserting a `MovementRow` between two
  `ScheduledExperienceCard`s (via a keyed `Fragment` per experience) adds
  a sibling list item, it never reorders the underlying array. Step
  172B's `stop_order ?? index + 1` numbering and Step 172C's per-day
  "Provider-grounded route order"/"Route order needs review"/"Suggested
  stop order" caption and plan-level movement-data note are all
  untouched. The Section 170 `AIPromotedBadge` is likewise untouched.
- **Verified live**, not just via `npm run build`: a real generated trip
  (no routing provider connected, the default) was loaded in a browser
  and showed a "Movement data unavailable" row between every pair of
  consecutive stops, alongside the unchanged numbered stops, "Suggested
  stop order" caption, and map markers -- confirming the defensive
  (missing-report-safe, old-trip-safe) rendering works end to end.
- **Backend change: none.** `travel_time_buffer_report` was already on
  the wire; this step is a frontend type-and-render addition only.

### 39.22 Section 172 Complete: Final Frontend Summary (Step 172E, final Section 172 step)

Step 172E is a review-and-polish pass over Steps 172B-172D's itinerary
UI, plus one small in-code clarifying comment
(`frontend/app/page.tsx`) documenting why a single-stop day never shows
a movement row -- no other frontend behavior changed. Full map *path*
visualization (turn-by-turn route geometry, polylines connecting real
road/walking segments) remains deferred to Section 173; `DayMapPreview`'s
pre-existing dotted straight-line connector between markers is
unchanged throughout all of Section 172.

Final "Day-wise experiences" card behavior, confirmed coherent end to
end (re-verified live in a browser, and by direct source inspection):

- **Numbered stops** (Step 172B): each `ScheduledExperienceCard`'s
  circle number is `experience.stop_order ?? index + 1`; `DayMapPreview`'s
  markers use the identical expression, so the card list and the map
  can never disagree. The frontend never reorders `day.experiences`
  itself anywhere -- confirmed by source inspection finding zero
  `.sort()`/`.reverse()` calls in `frontend/app/page.tsx`.
- **Per-day route-order caption** (Step 172C): "Provider-grounded route
  order" / "Route order needs review" / "Suggested stop order",
  computed by `routeAwareDayStatusLabel` from
  `experience.route_aware_provenance` and the day's own
  `route_aware_sequencing_report` suggestion -- shown only for a day
  with at least one scheduled experience (an empty day shows "No
  experiences scheduled for this day." instead, never a route-order
  claim about nothing).
- **Plan-level movement-data note** (Step 172C): a fixed explanatory
  sentence, plus a conditional "Movement data unavailable" line gated on
  `route_feasibility_report.status`.
- **Movement rows between stops** (Step 172D): rendered only when
  `travel_time_buffer_report` already has a matching `(from_experience_id,
  to_experience_id)` entry for that specific leg -- a single-stop day
  (or the last stop in any day) has no next stop to look up, so it
  never shows a movement row, never a "crowded" empty placeholder.
- **AI-promoted badge** (Section 170): `AIPromotedBadge` and its
  "AI-suggested · Provider-grounded" wording are untouched anywhere in
  Section 172 -- a promoted experience gets the exact same numbering,
  route-order caption, and movement-row treatment as any other scheduled
  experience.
- **Old persisted trips never crash.** Every Section 172 field on
  `ExperienceItem`/`PlanningState` is typed nullable in
  `frontend/lib/types.ts` (`| null`), and every helper function
  (`routeAwareDayStatusLabel`, `movementDataIsUnavailable`,
  `findMovementBetweenStops`, `shouldRenderMovementRow`) treats a
  missing report/field as the safe "nothing to show" case rather than
  throwing -- backed, as of this step, by dedicated backend tests
  proving an old `PlanningState` missing these fields entirely still
  deserializes cleanly (docs/14_backend_architecture.md section 71).
- **Wording stays restrained throughout.** No itinerary/movement string
  anywhere in Section 172 makes an unqualified superlative, official-
  status, or completion claim about a route, stop order, or travel time
  -- confirmed by the repository-wide forbidden-phrase grep passing clean
  on every Section 172 step, including this one.

### 39.23 Route Geometry Type Added, No Path Drawing Yet (Step 173A)

Step 173A adds a backend route path geometry contract
(docs/13_llm_reasoning_pipeline.md section 95,
docs/14_backend_architecture.md section 72) -- this step's only frontend
change is a matching TypeScript type addition, so the hand-maintained
type mirror (`frontend/lib/types.ts`) stays in sync with the new backend
response shape ahead of when it's actually used.

- **New type: `RoutePathPoint`** (`{ lat: number; lon: number }`), and
  `TravelTimeBuffer` gains `route_geometry: RoutePathPoint[] | null`.
  Both are already present on the wire today (`GET /trips/{trip_id}`
  already serializes the full `PlanningState`) -- this is a type-mirror
  addition only, not a new backend field this step introduced at the API
  layer.
- **`frontend/app/page.tsx` is completely unchanged.** No path is drawn,
  no polyline is rendered, and nothing reads `route_geometry` yet --
  `findMovementBetweenStops`/`MovementRow` (Step 172D) still only read
  `route_duration_seconds`/`route_distance_meters`/`status`. Full map
  *path* visualization (turn-by-turn route geometry connecting real
  road/walking segments between stops) remains deferred to a later
  Section 173 step. When that step arrives, it must draw only real
  backend-provided `route_geometry` points -- never a straight line
  between two stops' coordinates as a substitute when geometry is
  `null`, matching the same no-fallback-line rule
  `DayMapPreview`'s existing dotted connector already documents for
  itself (that connector is a *visual* straight-line convenience marker,
  never presented as a real route -- future path drawing must not blur
  that distinction).
- **`npm run lint`/`npm run build` both still pass** with only the type
  addition -- no runtime behavior changed.

### 39.24 Provider-Backed Route Paths Drawn on the Day Map (Step 173B)

Step 173B draws the route paths Step 173A's backend contract made
possible: `DayMapPreview` now renders a second, visually distinct
polyline layer for any leg whose `travel_time_buffer_report` entry is
real and provider-backed -- using the exact points the backend returned,
never a straight-line fallback.

- **Gating, via the new `drawableRouteGeometry` helper
  (`frontend/app/page.tsx`).** For each consecutive pair of stops, the
  existing `findMovementBetweenStops` (Step 172D) looks up that leg's
  `TravelTimeBuffer` by `(from_experience_id, to_experience_id)`.
  `drawableRouteGeometry` then requires, in order: a match exists,
  `status === "success"` (never `not_connected`/`unavailable`/`failed`/
  `partial`), `route_geometry` is non-null, it has at least two points,
  and every point's `lat`/`lon` is a finite number -- a single invalid
  point discards the whole leg's path rather than drawing a
  partial/corrupted one. Any leg that fails any of these checks draws no
  path at all; the pre-existing dotted straight-line connector (a
  visual convenience only, unchanged since before this step) still
  connects every coordinate-backed marker regardless, and the numbered
  markers, per-day route-order caption (Step 172C), and movement rows
  (Step 172D) are all completely unaffected.
- **Never a straight-line substitute, never inferred from coordinates.**
  The new solid (non-dashed) polyline is built exclusively from
  `buffer.route_geometry`'s own points -- the frontend never falls back
  to connecting the two stops' own `coordinates` directly when geometry
  is absent, and never computes a path, distance, or duration itself.
  `findMovementBetweenStops`/`drawableRouteGeometry` only read fields
  the backend already decided.
- **Map bounds include route geometry points**, not just the stop
  markers, so a real path that bows away from a straight line (as a real
  road route usually does) is never clipped by `fitBounds`.
- **Small legend addition** in the existing caption below the map:
  "Solid green segments are a provider-backed route path, shown only for
  a leg where the backend has one. Route path unavailable for any other
  leg -- no straight line is drawn in its place."
- **Verified live**, not just via `npm run build`: a synthetic
  `PlanningState` with a real, curved (non-straight) `route_geometry` was
  loaded in a browser and rendered as a solid green path clearly distinct
  from a straight line between the two markers, while a default
  (no-routing-provider) trip continued to show markers, the dashed
  connector, and "Movement data unavailable" rows with no path drawn at
  all -- confirming the gating and no-fallback rules both hold end to
  end.
- **`frontend/lib/types.ts`'s `RoutePathPoint` type (Step 173A) is now
  actually read**, not just mirrored -- this is the first step that
  consumes it. Full turn-by-turn route *editing*/interaction (if ever
  added) remains out of scope; this is display-only, matching every
  other itinerary field on this page.

### 39.25 Route Path Legend and No-Fallback Visual Hardening (Step 173C)

Step 173C removes the one remaining way the day map could read as
showing a route it did not actually have: the pre-existing dashed
straight-line connector that joined every coordinate-backed marker
regardless of whether any real route data existed for that leg. A line
between two dots reads as a path to a viewer no matter how the caption
below it is worded, so that connector is now gone entirely rather than
kept and re-labeled.

- **The day map now draws exactly one kind of line: the real,
  provider-backed route path from Step 173B**, and only for a leg where
  `drawableRouteGeometry` returns a real path. A leg without one now
  shows its two numbered markers and nothing joining them -- no dashed
  line, no straight-line stand-in of any kind. This makes the
  `DayMapPreview` "safer option" explicit: draw the real route geometry
  when it exists, otherwise markers only, never a line invented to fill
  the gap.
- **Two new pure helpers in `frontend/app/page.tsx`, `dayHasDrawableRouteGeometry`
  and `dayHasMovementStatusData`**, both walking the same
  consecutive-pair indexing as the drawing loop itself (never
  recomputing or reordering anything) to answer, respectively: does this
  day have at least one leg with a real drawn path, and does this day
  have any backend `TravelTimeBuffer` entry at all (regardless of its
  status). Both read only fields the backend already decided.
- **A new small legend line, via `routePathLegendLabel`**, rendered above
  the existing caption paragraph: "Provider-backed route path" (in
  emerald, matching the path's own color) when `dayHasDrawableRouteGeometry`
  is true; "Route path unavailable" (in the default muted color) when no
  path is drawn but `dayHasMovementStatusData` is true, meaning the
  backend does have real movement/route status data for this day, just
  not a drawable path -- so the claim is backed by an actual status, not
  invented. When neither holds (e.g. an older trip persisted before Step
  166C with no travel-time buffer report at all), no legend line renders
  at all, since "unavailable" would not be a claim the backend data
  actually supports.
- **The caption below the map is reworded** to stop describing a dashed
  connector that no longer exists: it now states plainly that numbered
  markers show stop order only and are never route geometry, and that
  solid green segments are the only line ever drawn, only for a leg
  where the backend has one.
- **Marker numbering (Step 172B), the per-day route-order caption (Step
  172C), movement rows (Step 172D), and the AI-promoted badge (Section
  170) are all completely unchanged** -- none of them read the map's own
  polyline layer, and this step touches no itinerary ordering, no
  scheduling, and no other component.
- **Verified via `npm run lint`/`npm run build`** (this repo has no
  frontend test framework) plus source inspection confirming no
  straight-line polyline of any kind remains in `DayMapPreview` and that
  every legend/caption string avoids overclaiming wording.

### 39.26 Map Viewport/Path Rendering Polish and Route Geometry Safety (Step 173D)

Step 173D hardens `DayMapPreview`'s viewport and geometry handling so a
malformed or old persisted record can never distort or crash the map,
and so a multi-leg day renders every leg it safely can.

- **New shared helper `isValidGeoCoordinate(lat, lon)`** (`frontend/app/page.tsx`)
  checks both finiteness (rejects `NaN`/`Infinity`) and the same in-range
  bounds the backend's own `GeoPoint`/`RoutePathPoint` models enforce
  (`lat` in [-90, 90], `lon` in [-180, 180]). Backend validation already
  rejects an out-of-range coordinate at write time, but this is an
  independent read-side guard against a pre-existing or hand-edited local
  JSON record that predates that validation.
- **`drawableRouteGeometry` (Step 173B) now also rejects out-of-range
  lat/lon**, on top of its existing null/length/finiteness checks -- a
  single invalid point anywhere in a leg's `route_geometry` still
  discards that leg's whole path rather than drawing a partial one, and
  still never triggers a straight-line or coordinate-inferred
  substitute.
- **"Coordinate-backed" now means valid, not just non-null**, for stop
  markers too: the day's marker count, the marker list, and the
  fit-bounds point list all filter through `isValidGeoCoordinate`
  together, so they can never disagree (which previously could have left
  the map container rendered with zero valid points to fit bounds
  around, had a persisted coordinate ever been malformed).
- **Map bounds include every valid stop coordinate plus every valid,
  drawn leg's route geometry points** -- unchanged in shape from Step
  173B, now built from the same validated point sets described above.
- **Partial path coverage is allowed and honest**: each leg is evaluated
  independently in the drawing loop, so on a multi-leg day one leg with
  missing/invalid geometry does not stop another leg's valid,
  provider-backed geometry from drawing. A leg without a drawable path
  still gets no fallback line of any kind -- markers only for that leg,
  exactly as Step 173C established.
- **Legend behavior (Step 173C) is unchanged**: "Provider-backed route
  path" when at least one leg draws, "Route path unavailable" only when
  real backend movement/route status data exists for the day without a
  drawable path, no legend line for an old trip with no travel-time
  buffer report at all.
- **Verified via `npm run lint`/`npm run build`** plus source inspection
  confirming: no code path can call `L.marker`/`L.polyline`/
  `L.latLngBounds` with a non-finite or out-of-range coordinate; no
  geometry is ever created, interpolated, or repaired from stop
  coordinates; no distance/duration is computed client-side; and marker
  numbering (172B), route-aware captions (172C), movement rows (172D),
  and the AI-promoted badge (Section 170) remain untouched.

### 39.27 Section 173 Complete: Full Map Path Visualization, Final Frontend Summary (Step 173E, final Section 173 step)

Step 173E is a review-and-polish step; no behavior changed from Step
173D. It re-confirmed, by direct source inspection of
`frontend/app/page.tsx`, every safety property Section 173 set out to
establish:

- **Exactly one `L.polyline` call exists in `DayMapPreview`**, and it is
  gated entirely by `drawableRouteGeometry` (real backend
  `route_geometry`, `status === "success"`, at least two points, every
  point finite and in-range). No marker-coordinate fallback polyline
  exists anywhere in the file.
- **No `.sort()`/`.reverse()`/haversine/distance-from-coordinates logic
  and no hardcoded travel duration/distance value** exists anywhere in
  the map, movement-row, or legend code. The only mention of
  "haversine" in the file is a comment stating what a nearby unit
  converter (`formatDistanceMeters`) explicitly does *not* do.
- **Map bounds are built only from valid stop coordinates and valid
  route geometry points** (`isValidGeoCoordinate`, Step 173D), so
  malformed data can never reach Leaflet or leave the bounds computation
  empty.
- **Partial path coverage across a multi-leg day is honest and
  independent per leg** -- a day can show a real path on some legs and
  markers-only on others, decided leg by leg, matching exactly what the
  backend's per-leg `route_geometry` optionality (docs/14_backend_architecture.md
  section 76) allows.
- **Legend wording stays restrained**: only "Provider-backed route path"
  and "Route path unavailable" are ever rendered by
  `routePathLegendLabel`, gated so "unavailable" is never claimed
  without real backend movement/route status data behind it. Neither
  string, nor anything else in the map/legend/caption text, makes any
  claim of speed, safety, independent confirmation, official status,
  certainty, reservation status, or readiness for booking.
- **Movement rows (172D), numbered markers (172B, `stop_order ?? index +
  1`), route-aware captions (172C), and the AI-promoted badge (Section
  170) are all confirmed unchanged** -- none of them read the map's own
  polyline/legend layer.

Section 173, end to end: `route_geometry` is real, provider-backed data
only, drawn only where it exists, never inferred, never faked, and
never confused with the plain numbered stop markers that exist
regardless of whether any path does.

### 39.28 Backend Regeneration Guardrails Added, No Frontend Change (Step 174B)

Step 174B is backend-only (`backend/app/api/routes/trips.py`,
`backend/app/schemas/trips.py`, `backend/app/core/errors.py`,
`backend/app/schemas/errors.py`,
`backend/app/services/regeneration_attempt_service.py`) -- **no frontend
file changed**. `POST /trips/{trip_id}/regenerate` gains a real
`confirm`/`scope` request body and a staged guardrail chain (blocked by
active locks, blocked by no pending feedback, or an eligible-but-not-
yet-enabled refusal), but the frontend's existing `requestRegeneration`
call (`frontend/lib/api.ts`) still sends no body at all, so it still
receives byte-for-byte the same `409 REGENERATION_NOT_AVAILABLE`
response it always has. `RegenerationReadinessSection`'s "Check backend
refusal" button (`frontend/app/page.tsx`) is unchanged and still treats
any non-409 response as an anomaly worth flagging.

A real frontend UI for the new `confirm=true` guardrail outcomes
(distinguishing "blocked by locks" from "no pending feedback" from an
eventual real success) is planned for Step 174E, once Step 174C gives
the "eligible" branch a real, mutating outcome to actually render.

### 39.29 Backend Real Regeneration Enabled, No Frontend Change Yet (Step 174C)

Step 174C is backend-only (`backend/app/api/routes/trips.py`,
`backend/app/schemas/regeneration_result.py`,
`backend/app/services/regeneration_attempt_service.py`,
`backend/app/services/versioning_service.py` consumption) -- **no
frontend file changed**. `POST /trips/{trip_id}/regenerate` now actually
mutates the plan for one narrow request shape (`confirm=true`, feedback
exists, zero active locks, at least one real affected stage) and returns
`200` with a `RegenerateResponseData` payload, but the frontend's
existing `requestRegeneration` call (`frontend/lib/api.ts`) still sends
no body at all, so it still hits the unconditional `{"confirm": false}`
branch and still receives byte-for-byte the same
`409 REGENERATION_NOT_AVAILABLE` response it always has.
`RegenerationReadinessSection`'s "Check backend refusal" button
(`frontend/app/page.tsx`) is unchanged and still treats any non-409
response as an anomaly worth flagging -- it has no code path yet that
would recognize or render a real `200` regeneration result, a
`REGENERATION_BLOCKED_BY_LOCKS`/`REGENERATION_NO_PENDING_FEEDBACK`
refusal, or an updated `experience_plan` after regeneration.

The existing frontend also has no way yet to send `{"confirm": true}` at
all -- `requestRegeneration`'s signature takes no request body parameter.
A real frontend UI covering the full guardrail/success matrix (locks,
no feedback, applied result, and refreshing the itinerary/version
history/diff-preview panels after a real regeneration) remains planned
for Step 174E.

### 39.30 Backend Regeneration Lifecycle Completed, Readiness/Diff Fields Now Honest, No Frontend Change Yet (Step 174D)

Step 174D is backend-only (`backend/app/models/planning_state.py`,
`backend/app/services/feedback_service.py`,
`backend/app/services/regeneration_readiness_service.py`,
`backend/app/services/plan_diff_preview_service.py`,
`backend/app/api/routes/trips.py`) -- **no frontend file changed**. Two
things a future frontend step should know both changed on the wire,
even though nothing in `frontend/app/page.tsx` reads them differently
yet:

- `PlanningState.regeneration_readiness.can_regenerate`/`status` and
  `PlanningState.plan_diff_preview.regeneration_available`/`preview_status`
  now go `true`/`"ready"`/`"regeneration_available"` for a trip with a
  generated plan, pending feedback, zero active locks, and a real
  derivable affected stage -- the existing read-only
  `RegenerationReadinessSection`/plan-diff-preview panels in
  `frontend/app/page.tsx` already render whatever these fields say, so
  they will start showing this new value for a matching trip without any
  frontend code change -- but no button anywhere yet turns that "ready"
  state into an actual `{"confirm": true}` call (see 39.29 above).
  `RegenerationReadinessSection`'s copy ("This gate only explains whether
  feedback-driven regeneration can run. It does not regenerate or change
  the plan.") remains accurate either way. **Superseded by Step 174E**
  (section 39.31 below), which adds the real button and rewords this
  copy since the section now does let you apply it.
- `FeedbackEvent` gained `applied_at`/`applied_in_version` (plus the
  existing `handling_status` now actually reaching `"applied"`). The
  frontend's feedback-history rendering (wherever it lists
  `feedback_history`) will start seeing these fields as extra, currently
  unused JSON keys on events a real regeneration has processed --
  harmless for `frontend/lib/types.ts`'s existing `FeedbackEvent` type
  mirror only once that type is updated to include them; until then they
  are simply not read by the frontend, matching how new backend fields
  have always been introduced ahead of the frontend type catching up in
  this project.

A real frontend UI update (rendering "ready to regenerate," a real
`{"confirm": true}` button, and reflecting applied feedback distinctly
from pending feedback) remains planned for Step 174E.

### 39.31 Section 174 Complete: Real Regeneration UI (Step 174E, final Section 174 step)

Step 174E is the frontend's part of Section 174: `frontend/lib/types.ts`,
`frontend/lib/api.ts`, and `frontend/app/page.tsx` change; no backend
file changes.

- **New mirrored types** (`frontend/lib/types.ts`):
  `RegenerateRequestInput` (`{confirm: true, scope: "affected_stages"}`)
  and `RegenerateResponseData` (`trip_id`, `status`, `previous_version`,
  `current_version`, `changed_sections`, `preserved_sections`,
  `applied_feedback_event_ids`, `active_lock_count`, `message`) mirror
  `backend/app/schemas/trips.RegenerateRequest`/
  `backend/app/schemas/regeneration_result.RegenerateResponseData`
  exactly. `FeedbackEvent` also gained `applied_at`/`applied_in_version`
  (Step 174D's backend fields, now actually typed on the frontend).
- **`ApiRequestError` gained a `code` field** (`frontend/lib/api.ts`),
  populated from the backend's own `ApiError.code` -- previously only
  `message`/`status` were captured, which was enough for a generic
  refusal display but not enough to distinguish
  `REGENERATION_BLOCKED_BY_LOCKS` from `REGENERATION_NO_PENDING_FEEDBACK`
  from any other error.
- **`requestRegeneration` now always sends `{"confirm": true, "scope":
  "affected_stages"}`** and returns a typed `RegenerateResponseData` on
  success instead of `unknown` -- it no longer sends an empty body
  expecting only a refusal.
- **`RegenerationReadinessSection` rewritten**: the old "Check backend
  refusal" button and its "unexpected success is a warning" framing are
  both gone. The section's one button, **"Regenerate from feedback,"** is
  `disabled` whenever `readiness.can_regenerate` is `false` (with a
  tooltip/helper text explaining the requirement) and calls
  `requestRegeneration` when enabled. On success it renders a restrained
  green panel restating `previous_version → current_version`,
  `changed_sections`, `preserved_sections`, and
  `applied_feedback_event_ids` -- values read directly from the
  response, never computed by the frontend -- and calls a new
  `onRegenerateSuccess` prop that the page wires to a full
  `loadPlanResult` refresh (the same helper every trip load already
  uses), so the itinerary, movement rows, route paths, version history,
  diff preview, and readiness panels all update together. On refusal it
  renders the backend's own `code`/`message` in a red panel and refreshes
  only the attempt-audit list (`onRegenerationAttemptsChange`), exactly
  matching the old refusal-path behavior -- never implying the plan
  changed, never calling `loadPlanResult`.
- **A second, previously-separate disabled button removed**:
  `PendingRequestedChangesSection`'s own "Regenerate with feedback"
  button (hardcoded `disabled`, title "not implemented yet") was a
  second, now-inaccurate claim that regeneration was unavailable. It is
  replaced with text pointing to the one real control in
  `RegenerationReadinessSection` below -- there is now exactly one
  regenerate action on the page, not two with different framings.
  `PlanDiffPreviewSection`'s status-label helper also gained a friendly
  label for the new `"regeneration_available"` `preview_status` value
  (Step 174D); its rendering logic itself was already dynamic and needed
  no other change.
- **Preserved unchanged**: numbered stops (172B), movement rows (172D),
  provider-backed route paths (173), the AI-promoted badge (170), and
  the lock/version-history/diff-preview panels -- none of their code was
  touched. Verified live: generated a trip, submitted feedback, watched
  the button enable, clicked it, confirmed the success panel and full
  refresh (version history, diff preview, and readiness all updated to
  reflect the new version and the now-applied feedback), confirmed the
  button disabled again immediately after (no pending feedback left),
  and confirmed creating then removing an active lock correctly toggled
  the button between blocked/enabled while feedback stayed pending.
- **Frontend re-check gate, not the safety boundary**: the button being
  enabled is a UX convenience only -- `POST /trips/{trip_id}/regenerate`
  itself still re-validates every precondition server-side on every
  call, unchanged from Steps 174B-174D.

## Step 175B: No Frontend Display Change

Step 175B (`backend/app/services/plan_validator_service.py`) added a
backend-only `category="provider_coverage_consistency"` warning to
`validation_report.warnings` and cleaned up stale validator wording. No
frontend file changed. The existing `ValidationSection`/validation-report
rendering in `frontend/app/page.tsx` already renders every
`validation_report.warnings` entry generically (severity, category,
message, suggested_fix) without a category allowlist, so a new warning
category appears in the existing warnings list without any code change
-- but no dedicated UI treatment (icon, grouping, or special copy) for
`provider_coverage_consistency` was added in this step. That display
work, if wanted, is a future frontend step, not part of 175B.

## Step 175C: No Frontend Display Change

Step 175C (`backend/app/services/plan_validator_service.py`) added three
more backend-only `warnings` categories --
`route_aware_sequencing`, `movement_data`, and `route_geometry` -- and
changed nothing else. No frontend file changed. As with 175B, the
existing generic `ValidationIssueList`/`ValidationSection` rendering in
`frontend/app/page.tsx` (category, severity, message, affected_section,
suggested_fix, no allowlist) already renders these new categories exactly
like every other warning, including their `SUGGESTION` severity badge
where used. No dedicated UI treatment (icon, grouping, day-level/map
overlay, or special copy) for route-aware sequencing, movement data, or
route geometry validation was added in this step -- that display work, if
wanted, is a future frontend step, not part of 175C.

## Step 175D: No Frontend Display Change

Step 175D (`backend/app/services/plan_validator_service.py`) added two
more backend-only `warnings` categories -- `regeneration` and
`regeneration_state_consistency` -- plus a tiny `affected_section` value
change on the existing flight-inventory warning (`"stay_transport"` ->
`"flight_inventory"`). No frontend file changed. The existing generic
`ValidationIssueList`/`ValidationSection` rendering in
`frontend/app/page.tsx` already renders these new categories exactly like
every other warning (category, severity, message, affected_section,
suggested_fix, no allowlist), so they appear in the existing warnings
list without any code change. No dedicated UI treatment (icon, grouping,
or a link into `RegenerationReadinessSection`) for regeneration-lifecycle
validation warnings was added in this step -- that display work, if
wanted, is a future frontend step, not part of 175D.

## Step 175E (final Section 175 step): Grouped Validation Display

Step 175E is frontend-only (`frontend/app/page.tsx`) -- no backend file
changed, and (after review) no backend behavior needed changing either.
It polishes how `ValidationSection`/`ValidationIssueList` render the
larger set of categories/severities Sections 175B-175D added, without
creating a single new validation fact.

- **Category grouping.** A new `groupValidationIssuesByCategory` helper
  groups an issue list by `category`, preserving the order categories
  first appear in (never reordering individual issues within a category,
  never dropping or inventing one). `ValidationIssueList` now renders one
  heading per category group instead of one flat list.
- **Human-readable category labels.** A new `VALIDATION_CATEGORY_LABELS`
  map plus `validationCategoryLabel` fallback (snake_case ->
  Title Case) turns backend category strings into display labels --
  `provider_coverage_consistency` -> "Provider coverage consistency",
  `route_aware_sequencing` -> "Route-aware sequencing", `movement_data`
  -> "Movement data", `route_geometry` -> "Route geometry",
  `regeneration` -> "Regeneration", `regeneration_state_consistency` ->
  "Regeneration state consistency", and so on for every pre-existing
  category too. This is purely a display relabeling of a string the
  backend already sent -- it never changes, interprets, or invents the
  underlying `message`/`severity`/`affected_section`/`suggested_fix`.
- **Warnings vs. suggestions, split and clearly distinguished.** The
  backend places every non-critical issue in `ValidationReport.warnings`,
  using its own `severity` field ("warning" vs "suggestion") to
  distinguish them (Steps 175C/175D) -- `ValidationReport.suggestions` is
  never populated. `ValidationSection` now splits `warnings` into two
  buckets by that existing `severity` field and renders them as separate
  titled sections, "Warnings" and "Suggestions", each independently
  grouped by category. A new `validationSeverityToneClassName` helper
  (reading only `issue.severity`) gives `critical`/`warning`/`suggestion`
  visibly different border/badge tones so a suggestion reads clearly as a
  lower-severity, informational item -- this is a pure style computed
  from a field the backend already sent, never a new judgment about the
  issue.
- **Critical issues and warnings are never hidden.** `ValidationIssueList`
  still renders nothing only when its own issue list is empty (unchanged
  behavior) -- grouping/relabeling never causes an issue to be dropped
  from display. Verified live: a `blocked` trip (zero provider-backed
  attraction candidates) still shows its one `category="provider_coverage"`
  critical issue in its own red-toned "Critical issues (1)" section, and
  a `needs_review` trip's warnings/suggestions (across `feasibility`,
  `weather`, `accommodation_inventory`, `flight_inventory`,
  `route_aware_sequencing`, `movement_data`, `route_geometry`) render
  correctly grouped with the right severity tone.
- **Provider coverage notes and unavailable-data notes are unchanged** --
  same two `SummaryList`-style blocks below the issue lists, still always
  visible when non-empty.
- **Nothing else on the page changed.** Numbered stops, movement rows,
  provider-backed route paths, the AI-promoted badge, the regeneration
  UI, locks, version history, diff preview, and the accommodation/flight
  inventory sections were not touched -- confirmed both by `git diff`
  (only the `ValidationIssueList`/`ValidationSection` hunks changed) and
  by live verification (created a trip, generated a plan, loaded it via
  "Load existing trip", and visually confirmed those sections still
  render with zero console errors).
- **Backend safety review (no code change resulted).** Re-read
  `plan_validator_service.py` end to end and confirmed: every 175B/175C/
  175D issue is `WARNING`/`SUGGESTION` severity only; `readiness_status`
  is still governed solely by `critical_issues`; no message claims a
  route order is superior, a fact is confirmed beyond what was checked,
  a locked item's fate is assured, feedback's intent was fully met, or
  that the trip will be better as a result; no validation function
  computes a route geometry,
  distance, or duration (only presence/count is read); and no validation
  function mutates provider/planning/regeneration state -- every one of
  them is a pure read returning a list of `ValidationIssue`. No backend
  bug was found, so no backend file changed in this step.

## Step 176B: Trust Dashboard Data-Shaping Helper (No UI Yet)

Step 176B adds one new frontend-only module, `frontend/lib/trust-dashboard.ts`
-- following Step 176A's audit finding that every field a trust dashboard
needs is already present on the `PlanResult` object `page.tsx`'s
`loadPlanResult` already assembles from existing `GET` endpoints. This step
adds no new fetch, no new backend model, no new backend endpoint, and no
render of any kind -- `page.tsx` itself is completely unmodified in this
step (confirmed by `git status`/`git diff`).

- **`TrustDashboardSourceResult`** is a locally-declared type naming the
  slice of `PlanResult` this module reads (`summary`, `validationReport`,
  `providerCoverage`, `readinessChecklist`, `dailyPlans`,
  `accommodationInventoryReport`, `flightInventoryReport`,
  `routeFeasibilityReport`, `routeAwareSequencingReport`,
  `travelTimeBufferReport`, `aiCandidateReviewReport`,
  `aiCandidatePromotionReport`, `regenerationReadiness`,
  `planDiffPreview`, `regenerationAttempts`, `pendingFeedbackSummary`,
  `versionHistory`) -- deliberately declared here rather than imported
  from `page.tsx` (a React component module) so this file stays
  React-free. Because `PlanResult` already has every one of these fields
  with these exact types, a real `PlanResult` value satisfies this type
  structurally with zero coupling; wiring an actual call site into
  `page.tsx` is deferred to a later step.
- **`buildTrustDashboardModel(result: TrustDashboardSourceResult):
  TrustDashboardModel`** is a pure function -- no `fetch`, no
  `XMLHttpRequest`, no browser API (`window`/`document`/`localStorage`),
  no React import, no hook, no `Math`/haversine/distance computation, no
  coordinate or route-path construction, confirmed by source-inspection
  grep as well as by `tsc --noEmit`/`next build` compiling the file
  cleanly even though nothing calls it yet. It never mutates `result` or
  any nested object -- every category builder only reads and returns new
  plain objects/arrays.
- **Nine categories** (`TrustDashboardCategoryId`): `core_trip_grounding`,
  `places_and_experiences`, `lodging_inventory`, `flight_inventory`,
  `routes_and_movement`, `weather_holiday_currency`,
  `ai_suggested_candidates`, `regeneration_and_change_safety`,
  `validation_summary`. Each produces a `TrustDashboardCategoryView`
  (`id`, `title`, `statusKind`, `statusLabel`, `detail`,
  `supportingFacts`, `relatedValidationIssues`, `detailAnchorId`) built
  only from fields named in Step 176A's own per-category source-field
  guidance -- e.g. lodging inventory reads
  `providerCoverage.provider_coverage.hotel_prices`,
  `accommodationInventoryReport.status`, `.offers.length`, and whether
  any offer carries `scraped_provenance`; routes-and-movement counts
  `route_geometry` presence only from `travelTimeBufferReport.buffers`
  (mirroring the backend's own `_route_geometry_leg_counts` preference,
  Step 175C) and never touches a coordinate.
- **A closed, 10-value status vocabulary** (`TrustDashboardStatusKind`:
  `available`, `needs_review`, `not_connected`, `unavailable`, `failed`,
  `partial`, `open_data_only`, `scraped_source`, `no_pending_feedback`,
  `blocked_by_locks`) with a single label lookup
  (`STATUS_KIND_LABELS`) is the only source of `statusLabel` text --
  every category picks one of these ten kinds, never a freeform string,
  matching Step 176A's restrained-wording recommendation. A small
  `coverageValueToStatusKind` maps existing raw
  `provider_coverage`/report `status` strings (including the synthetic
  `open_poi_available` value) onto this vocabulary, falling back to
  `needs_review` (never a guessed good/bad verdict) for anything
  unrecognized; `worstStatusKind` combines several already-known statuses
  into one category's status using a fixed, documented severity
  ordering -- an aggregation rule, not a new fact.
- **No-fake-data guarantees, concretely**: lodging/flight tiles only ever
  say "bookable"-equivalent language (reusing the existing
  `AccommodationSearchResult`/`FlightSearchResult` "bookable inventory"
  vocabulary already used by `AccommodationInventorySection`/
  `FlightInventorySection`) when `status === "success"` and at least one
  offer exists, and separately flag `scraped_source` whenever any offer
  carries `scraped_provenance`, with explicit "not official-provider
  data, not verified" wording; open-data `accommodations` coverage is
  always described as "open-data location candidates, not bookable
  lodging inventory" alongside `hotel_prices`, never merged with it; the
  AI-candidates category only reports a "scheduled" count by summing
  `dailyPlans[].experiences[].promoted_from_ai === true`, never treating
  "eligible" or "promoted" as "scheduled"; the regeneration category
  restates `regeneration_readiness`/`plan_diff_preview`'s own booleans
  and `blocked_by` text verbatim, and its `detail` string explicitly
  disclaims that locks are not preserved and the plan is not claimed to
  improve.
- **`relatedValidationIssues`** cross-links each category to the matching
  slice of `validationReport.warnings`/`critical_issues` by `category`
  (and, for `provider_coverage_consistency`, by a substring match against
  that issue's own message mentioning `hotel_prices`/`flights`/`routes`)
  -- a plain filter over already-existing objects, never a new issue.
  `ai_suggested_candidates` legitimately returns an empty list today,
  since `PlanValidatorService` has no dedicated category for AI
  candidates yet -- an honest reflection of current state, not a bug.
- **Not wired into any component.** No `import` of this module exists
  anywhere in `frontend/app/page.tsx` after this step -- rendering the
  dashboard card is deferred to a later Section 176 step.

## Step 176C: Trust Dashboard Card Rendered

Step 176C wires Step 176B's pure helper into the page for the first time.
`frontend/app/page.tsx` now imports `buildTrustDashboardModel` (and its
`TrustDashboardModel`/`TrustDashboardCategoryView`/`TrustDashboardStatusKind`
types) from `@/lib/trust-dashboard`, and adds three new components:
`trustDashboardToneClassName` (a pure status-kind -> border/badge color
mapping), `TrustDashboardCategoryCard` (one category tile), and
`TrustDashboardSection` (the card wrapping all nine tiles in a responsive
grid). No backend file, model, or endpoint was touched, and no new
`fetch`/API-helper call was added -- the section is rendered from
`buildTrustDashboardModel(result)`, called inline where `result: PlanResult`
is already in scope from the plan the page already loaded.

- **Placement**: immediately after the existing `plan-overview`
  `ResultGroupHeader` and immediately before `UserTrustSummarySection` --
  the first thing a user sees in "Plan overview," ahead of the pre-existing
  trust/readiness UI it complements.
- **Renders only `TrustDashboardCategoryView` fields**: `title`,
  `statusLabel` (colored via `statusKind`, never recomputed in JSX --
  `trustDashboardToneClassName` only maps an already-decided `statusKind`
  to a border/badge class, it never decides status itself), `detail`,
  `supportingFacts` (via the pre-existing `SummaryList` component, reused
  rather than reimplemented), `relatedValidationIssues` (also via
  `SummaryList`, capped at `TRUST_DASHBOARD_MAX_RELATED_ISSUES_SHOWN = 3`
  messages with a "+N more -- see the Validation report section below"
  line when there are more -- a display-only truncation; the heading
  always shows the real, untruncated count), and `detailAnchorId` (an
  optional "View details" link). No field is invented, recomputed, or
  reinterpreted by the component -- every value was already decided by
  `buildTrustDashboardModel` before this ever renders.
- **Status tone is a closed, three-bucket mapping**: `available` ->
  emerald (neutral/positive); `needs_review`/`partial`/`scraped_source`/
  `open_data_only` -> amber (review/attention, matching the existing
  warning tone from `validationSeverityToneClassName`); every other kind
  (`not_connected`/`unavailable`/`failed`/`blocked_by_locks`/
  `no_pending_feedback`) -> a muted slate tone, deliberately *not* colored
  as an alarming error, since most of these are this app's ordinary
  default state (no routing/lodging/flight provider connected) rather
  than something having gone wrong.
- **"View details" links point at real, pre-existing anchors only** --
  `detailAnchorId` values (`plan-overview`, `data-sources`,
  `travel-context`, `review-required`) are exactly the `id`s
  `ResultGroupHeader` already renders elsewhere on the page (Step 175's
  jump-link groups); no new anchor was added, and none was needed.
- **Front door, not a replacement**: the section's own subtitle says so
  explicitly ("This does not replace the detailed sections below"), and
  every other pre-existing section (`ValidationSection`,
  `ProviderCoverageSection`, `AccommodationInventorySection`,
  `FlightInventorySection`, `RegenerationReadinessSection`, numbered
  stops, movement rows, route paths, the AI-promoted badge, etc.) is
  completely unchanged -- confirmed both by `git diff` (only the new
  import, three new component definitions, and one new render call were
  added) and by live verification: generated a trip, loaded it via "Load
  existing trip," confirmed all nine category tiles render with the
  correct status/tone, confirmed every pre-existing section title is
  still present on the page, and confirmed zero console errors.

## Step 176D: Trust Dashboard Cross-Links and Copy Hardening

Step 176D makes the trust dashboard a more useful front door and tightens
its wording, without touching any backend file, adding any fetch, or
changing any existing section's own behavior.

- **Ten new stable section anchors.** `frontend/app/page.tsx`'s existing
  section components each already rendered a single root
  `<div className="rounded-2xl border border-white/10 bg-white/5 p-5">`
  with no early return before it, so adding `id="..."` directly to that
  existing element was safe and behavior-neutral -- no section was
  moved, wrapped, or restructured. New ids: `readiness-checklist`
  (`ReadinessChecklistSection`), `route-feasibility`
  (`RouteFeasibilitySection`), `validation-report` (`ValidationSection`),
  `provider-coverage` (`ProviderCoverageSection`),
  `accommodation-inventory` (`AccommodationInventorySection`),
  `flight-inventory` (`FlightInventorySection`), `ai-candidate-review`
  (`AICandidateReviewSection`), `version-history`
  (`VersionHistorySection`), `plan-diff-preview`
  (`PlanDiffPreviewSection`), `regeneration-readiness`
  (`RegenerationReadinessSection`). The pre-existing `ResultGroupHeader`
  jump-link anchors (`plan-overview`, `travel-context`,
  `draft-itinerary`, `review-required`, `data-sources`) are untouched --
  every one of the ten new ids was verified unique (no collision) via a
  live per-id `document.querySelector` count check.
- **Every dashboard category's `detailAnchorId` now points at the most
  specific matching section** (`frontend/lib/trust-dashboard.ts`):
  `core_trip_grounding` and `validation_summary` -> `validation-report`;
  `places_and_experiences` -> `provider-coverage` (its own
  `supportingFacts` are literally `provider_coverage.*` values);
  `lodging_inventory` -> `accommodation-inventory`; `flight_inventory` ->
  `flight-inventory`; `ai_suggested_candidates` -> `ai-candidate-review`;
  `regeneration_and_change_safety` -> `regeneration-readiness`;
  `weather_holiday_currency` -> `travel-context` (unchanged -- no single
  section combines weather/holiday/currency, so the existing group
  anchor remains the right target). `routes_and_movement` deliberately
  links to `draft-itinerary`, not the newly-anchored
  `route-feasibility` section -- `RouteFeasibilitySection` renders the
  older, always-`not_connected` `RouteFeasibilityContext` data-model
  foundation, a different concept from the real
  `route_feasibility_report`/`route_aware_sequencing_report`/
  `travel_time_buffer_report` data this category actually summarizes;
  the real per-day movement rows, route-aware order badges, and
  provider-backed route paths live inline in "Draft itinerary" instead.
  Live verification clicked/resolved all nine "View details" links and
  confirmed every `href="#..."` target exists on the page.
- **Copy hardening, concretely**: "bookable accommodation/flight offer(s)
  found" was reworded to "accommodation/flight inventory offer(s)
  available ... This is not a confirmed booking" (the word "bookable"
  alone, reused verbatim from the backend's own "Bookable lodging
  inventory" section title, risked reading as a stronger claim than
  intended); the route-geometry supporting fact now reads "Route path
  geometry present on N of M scheduled leg(s)" / "Route path geometry
  unavailable -- no scheduled legs to report on" instead of "available
  for N of M"; the `routes` coverage line now explicitly notes "(route
  data available only when this is a connected/successful status)"; and
  the regeneration category adds "Pending feedback can be applied
  through deterministic regeneration" (mirroring `PlanValidatorService`'s
  own Step 175D wording almost verbatim) only when
  `regeneration_readiness.can_regenerate` is actually `true`. No
  category's status/count/label logic changed -- only wording.
- **Front door, still not a duplicate.** The related-validation-issue
  preview introduced in 176C remains capped at
  `TRUST_DASHBOARD_MAX_RELATED_ISSUES_SHOWN = 3` with a "+N more" note
  (unchanged this step); no category renders a full provider-status
  table, a full validation report, or a full inventory offer list --
  each links out to its own detailed section instead.
- **Preservation confirmed.** `UserTrustSummarySection`,
  `PlanStatusSection`, `ValidationSection`, `ProviderCoverageSection`,
  `AccommodationInventorySection`, `FlightInventorySection`,
  `AICandidateReviewSection`, `RegenerationReadinessSection`,
  `PlanDiffPreviewSection`, `VersionHistorySection`,
  `ReadinessChecklistSection`, `DayMapPreview`, `MovementRow`, and
  `AIPromotedBadge` are all still present and unmodified beyond the
  single `id` attribute added to ten of their root elements -- confirmed
  by `git diff` and by live verification (all pre-existing section
  titles still found on the page, zero console errors).

## Step 176E (final Section 176 step): Visual Polish and Final Safety Review

Step 176E closes out Section 176 with a small readability polish pass
and a full re-verification -- no new category, no new anchor, no wording
change beyond what 176D already hardened, and (after review) no backend
file touched.

- **Compact, dashboard-only fact list.** `TrustDashboardCategoryCard`'s
  "Supporting facts"/"Related validation issue(s)" lists previously
  reused the shared `SummaryList` component at its normal `text-sm` size
  -- appropriate for a full-width detail section, but too bulky for a
  nine-card front-door grid. A new, dashboard-local
  `TrustDashboardFactList` component (same title-plus-bulleted-list
  structure, denser `text-[11px]` type) replaces those two call sites
  only -- `SummaryList` itself is untouched and still renders exactly as
  before everywhere else on the page (roughly fifteen other sections).
  This alone made every card noticeably shorter and the nine-card grid
  visibly more balanced.
- **A real overflow bug found and fixed.** Live screenshots surfaced a
  genuine layout defect, not just a style nit: a long unbroken string
  (the local scraped-accommodation/flight HTML path, e.g.
  `/Users/.../backend/.data/manual_scrapes/flights.html`) failed to wrap
  inside its own card and visually spilled into the neighboring grid
  column, appearing as ghosted text overlapping the adjacent card's
  content. Fixed by adding `break-words` to
  `TrustDashboardFactList`'s `<ul>` only -- scoped to the new dashboard
  list, not applied to `SummaryList` or any other existing list on the
  page, so no other section's wrapping behavior changed.
- **A polish attempt that was reverted.** An initial `min-w-0` added to
  the card's title `<p>` (intended to let a long title share space with
  a wide status badge more gracefully) instead caused the title to
  shrink far enough to trigger mid-word line breaks (e.g. "Regenerati" /
  "on and change safety") on cards with both a long title and a wide
  badge. Live screenshots caught this immediately; the `min-w-0` was
  removed, restoring clean word-boundary wrapping. The badge itself
  keeps a small, harmless `whitespace-nowrap` addition so its own label
  text never splits across two lines.
- **Live verification matrix.** Generated one `needs_review` trip
  (Lisbon, normal candidate/provider data) and one `blocked` trip (a
  nonexistent destination, zero attraction candidates), then loaded each
  via "Load existing trip" at both a desktop (1280px) and a mobile
  (390px) viewport. Confirmed for every combination: all nine category
  titles present; all nine "View details" `href` targets resolve to a
  real element on the page; every pre-existing section title
  (`Validation report`, `Provider coverage`, `Readiness checklist`,
  `Can I use this plan?`, `Regeneration readiness`, `Bookable lodging
  inventory`, `Flight inventory`, `AI candidate review`, `Day-wise
  experiences`, `Plan diff preview`, `Version history`, `Route
  feasibility`) still present; zero console errors. The `blocked` trip
  correctly shows `Overall readiness: Blocked`, a `FAILED`
  `Validation summary` tile, and `UNAVAILABLE` tiles for core grounding/
  places/lodging/flights/weather -- never a false "available" reading
  when the underlying data genuinely isn't there.
- **Final copy re-review.** Re-grepped `frontend/lib/trust-dashboard.ts`
  and the new `page.tsx` components for `bookable`/`will `/`always`/
  `confirmed`/`final`/`guarant`/`verified`/`official`/`booked` -- every
  remaining hit is either a negation ("not a confirmed booking," "not
  official-provider data, not verified," "never claims ... will be
  preserved," "never claims the resulting plan will be better," "never
  marks a plan ready by itself") or an unrelated code comment. No new
  overclaim was introduced by this step's polish changes.
- **This is the last Section 176 step.** `TrustDashboardSourceResult`/
  `TrustDashboardModel`/`TrustDashboardCategoryView`/
  `TrustDashboardStatusKind`/`buildTrustDashboardModel`
  (`frontend/lib/trust-dashboard.ts`, Step 176B), `TrustDashboardSection`/
  `TrustDashboardCategoryCard`/`TrustDashboardFactList`/
  `trustDashboardToneClassName` (`frontend/app/page.tsx`, Steps 176C-E),
  ten new section anchors and nine specific "View details" links (Step
  176D), and this step's readability polish together make up the
  complete trust dashboard. Across all of 176A-176E: zero backend files
  changed, zero new endpoints, zero new fetch calls, and zero new travel
  facts -- every value the dashboard shows is a direct read, count, or
  fixed-vocabulary relabel of a field the backend already computed and
  already served before Section 176 began.

## Step 177B: Hotel Ratings Provider Foundation, No Frontend Change

Step 177B adds a backend-only hotel ratings foundation (`AccommodationRating`/
`rating_details` on `AccommodationOffer`, plus a new, always-`not_connected`
`backend/app/providers/hotel_ratings/` provider contract) -- see
docs/14_backend_architecture.md for the full writeup. **No frontend file
was touched by this step.** `frontend/app/page.tsx`'s
`AccommodationInventorySection` continues to render exactly what it did
before this step (the pre-existing bare `offer.rating`, still gated behind
`offer.rating !== null`), and `frontend/lib/types.ts`'s `AccommodationOffer`
type is deliberately left unchanged here -- with no provider populating
`rating_details` anywhere in the backend yet, there is nothing new for the
frontend to read or display. Any UI for `rating_details` (a rating value,
review count, and source label distinct from today's bare number), and the
corresponding `frontend/lib/types.ts` mirror update CLAUDE.md's
manual-sync convention requires, is explicitly deferred to a later Section
177 step, once a real enrichment path exists to populate the field.

## Step 177C: Hotel-Rating Enrichment Path Exists, Still No Frontend Change

Step 177C adds a backend-only conservative enrichment service
(`HotelRatingEnrichmentService`, wired into
`AccommodationInventoryService.build_report`) that *can* attach
`rating_details` to an offer, but only when a real, connected hotel
ratings provider returns an exact match -- with the default
`not_connected` provider, it still never does. **No frontend file was
touched by this step either.** `AccommodationInventorySection` and
`frontend/lib/types.ts`'s `AccommodationOffer`/`AccommodationInventoryReport`
types remain exactly as 177B left them -- the new
`hotel_ratings_status`/`hotel_ratings_provider`/`hotel_ratings_message`/
`hotel_ratings_enriched_offer_count` metadata fields added to the backend's
`AccommodationSearchResult` this step are not yet mirrored into
`AccommodationInventoryReport`, and nothing renders them. Frontend
display of `rating_details` (and of this new metadata) stays deferred to
177D, alongside the `ProviderCoverage.hotel_ratings` field also deferred
to that step.

## Step 177D: Hotel-Rating Metadata Now Displayed, Still No Real Provider

Step 177D wires the backend's Step 177B/177C/177D hotel-ratings fields
into the frontend for the first time. `frontend/lib/types.ts` gains a new
`AccommodationRatingDetails` type (mirroring `app.models.hotel_ratings.
AccommodationRating`) plus `rating_details: AccommodationRatingDetails |
null` on `AccommodationOffer` and four new `hotel_ratings_*` fields on
`AccommodationInventoryReport` -- `ProviderCoverage` needed no type
change since it was already the fully generic `Record<string, string |
null>`.

`AccommodationInventorySection` (`frontend/app/page.tsx`) gains a new
`AccommodationRatingDetailsCard`, rendered only when an offer's
`rating_details.value` is non-null -- it shows `"{value} / {scale_max}"`
using the backend's own `scale_max` (never assumed to be 5), the review
count and source only when the backend actually returned them, and a
fixed disclaimer that this is provider-reported data, not a claim that it
is verified, official, or confirmed, and not used to rank or recommend
any offer. With the default not_connected hotel ratings provider, this
card never renders (`rating_details` stays `null` on every offer) -- no
fake placeholder is ever shown in its place. The pre-existing bare
`offer.rating` number (Step 167A) is no longer rendered bare: a new
`legacyOfferRatingLabel` helper labels it "Rating from scraped/manual
source" or "Rating from lodging source" depending on whether the offer
carries `scraped_provenance`, since that number's scale is unknown and
was never something this app should present without saying where it came
from.

`frontend/lib/trust-dashboard.ts`'s `buildLodgingInventoryCategory` gains
a new `hotelRatingsSupportingFacts` helper appending informational lines
about `provider_coverage.hotel_ratings`/`hotel_ratings_enriched_offer_count`/
offers with only a source-limited legacy rating -- deliberately **not**
folded into the category's own `statusKind` computation (which stays
driven by lodging-inventory availability alone, exactly as before this
step), since hotel-rating enrichment is optional add-on metadata, not a
precondition for lodging inventory being usable; with the default
not_connected provider, treating it as a status-affecting signal would
make every otherwise-healthy lodging card read as "needs review" for an
unrelated reason. The category's `relatedValidationIssues` now also picks
up the new backend `category="hotel_ratings"` validation issues. No
message anywhere in this step implies a rating affects ranking or
recommendation quality, or that a rating is verified/official/confirmed.

## Step 177E (final Section 177 step): Frontend Safety Re-Review, No Behavior Change

Step 177E re-verified (by re-reading the current `frontend/app/page.tsx`
and `frontend/lib/trust-dashboard.ts` source, not from memory of 177D)
every frontend safety property Section 177 depends on, and made no
frontend code change:

- `AccommodationRatingDetailsCard` still renders only when
  `ratingDetails.value !== null` -- no fake placeholder for a missing/
  unmatched rating.
- `review_count`/`source_name`/`provider`/`data_status` still render only
  when the backend actually returned them.
- The legacy `offer.rating` number is still never shown bare --
  `legacyOfferRatingLabel` still labels it "Rating from scraped/manual
  source" or "Rating from lodging source" depending on
  `scraped_provenance`.
- `hotelRatingsSupportingFacts` in `trust-dashboard.ts` still only adds
  informational lines and is still **not** folded into
  `buildLodgingInventoryCategory`'s own `statusKind` -- confirmed by
  re-reading that `statusKind` is computed solely from
  `report.status`/`report.offers`/`scraped_provenance`, unchanged from
  177D.
- No `frontend/lib/api.ts` fetch call, endpoint, or request shape changed
  anywhere across Section 177 -- every field this section displays was
  already present on responses the frontend fetches today.

`npx tsc --noEmit`, `npm run lint`, and `npm run build` all pass cleanly
with zero frontend files modified in this step, confirming Section 177's
frontend work (177D only -- 177A/B/C touched no frontend file) is stable
and ready to ship alongside the rest of Section 177.

## Step 178B: Kiwi MCP Client Foundation, No Frontend Change

Step 178B adds a backend-only Kiwi MCP client/provider foundation
(`KiwiMcpClient`/`KiwiMcpFlightProvider`) that can perform live MCP tool
discovery behind explicit config -- see docs/12_provider_architecture.md
and docs/14_backend_architecture.md for the full writeup. **No frontend
file was touched by this step.** `FlightInventorySection`,
`buildFlightInventoryCategory`, and `frontend/lib/types.ts`'s
`FlightOffer`/`FlightInventoryReport` types remain exactly as they were:
`KiwiMcpFlightProvider.search_flights` never returns a `success` status
or any offer in this step, so there is nothing new for the frontend to
read or display, and `provider_coverage.flights`/the flight-inventory
validation warning are unchanged (this provider's results flow through
the exact same existing mapping/warning code every other flight provider
already uses). Frontend display of Kiwi-sourced flight data -- and any
label distinguishing it from `scraped_local`/`not_connected`, per Section
178's own audit (178A) -- is deferred to Step 178D, once Step 178C has
mapped real search output into `FlightOffer`.

## Step 178C: Kiwi MCP Can Now Return Real Offers, Still No Frontend Change

Step 178C makes `KiwiMcpFlightProvider` capable of returning real,
provider-backed `FlightOffer`s (see docs/12_provider_architecture.md
section 59 and docs/14_backend_architecture.md section 94) -- but **no
frontend file was touched by this step**. A Kiwi-sourced offer today
renders through the exact same, unmodified `FlightInventorySection` every
other flight offer already renders through: real segments, a real price/
currency, a real (Kiwi) booking link, and (when present) a flattened
baggage-allowance string all display exactly as they would for any other
`success` result, because `FlightOffer`'s shape didn't change and
`frontend/lib/types.ts` didn't need to.

What's explicitly *not* true yet, deferred to Step 178D: nothing in the
frontend or trust dashboard currently distinguishes a Kiwi-sourced offer
from a `scraped_local` one the way `scraped_provenance` already
distinguishes a scraped accommodation/flight offer from an official one
-- a Kiwi offer has no `scraped_provenance` (it isn't scraped data), so
today it would render with none of the amber "not official-provider
data" labeling a scraped offer gets, which is accurate (Kiwi's own data
*is* provider-backed) but doesn't yet make the specific source ("Kiwi
MCP" vs. some future real provider) visible to the user, and nothing yet
adds the "third-party booking link, not a TravelObligator confirmation"
framing this section's own backend message already states in `result.
message`. Both are Step 178D's job.

## Step 178D: Kiwi MCP-Specific Source Labels, Live-Verified

Step 178D closes the gap the previous step left open. `frontend/lib/types.ts`
needed **no change** -- `FlightOffer` already carried `provider`,
`source_name`, `total_price_amount`, `currency`, `booking_url`,
`baggage_policy`, `outbound_segments`/`return_segments`, and
`scraped_provenance`, everything this step needed to detect and label a
Kiwi offer.

`frontend/app/page.tsx` adds `isKiwiMcpFlightOffer(offer)` (a plain
`offer.provider === "kiwi_mcp"` check, documented as matching the
backend parser's own provider label exactly) and a new
`KiwiMcpOfferBadge` component, rendered instead of
`ScrapedFlightProvenanceBadge` when an offer is Kiwi-sourced (the two
are mutually exclusive -- a Kiwi offer never carries `scraped_provenance`).
`flightInventoryStatusLabel` gained a `hasKiwiMcpOffers` parameter,
returning `"Available via Kiwi MCP"` distinctly from `"Available from
scraped public page"` and the generic `"Connected"`. Every offer's
`booking_url` (regardless of source) now renders under a
`"Provider-supplied booking link -- not a booking confirmation"` caption
before the link text -- a small, source-independent safety improvement,
since no flight provider's `booking_url` in this codebase ever means a
completed booking. Missing `booking_url`/`baggage_policy`/
`cancellation_policy`/segment fields are still hidden entirely, exactly
as before -- no placeholder was added anywhere.

`buildFlightInventoryCategory` (`frontend/lib/trust-dashboard.ts`) adds
the same `provider === "kiwi_mcp"` detection and two new supporting-fact
lines when a Kiwi offer is present and no scraped offer is -- the
category's `statusKind` stays `"available"` (unchanged from before this
step; Kiwi data is real, provider-backed data, so it does not get the
`"scraped_source"` kind the way a scraped offer does), and no other
trust-dashboard category was touched.

**Live-verified end to end this step** (real `uvicorn` backend + real
`next dev` frontend on `localhost`, a real trip generated with
`FLIGHT_PROVIDER=kiwi_mcp`/`KIWI_MCP_ENABLED=true`, driven with a
temporary Playwright script, then fully cleaned up -- no scratch file or
dependency left behind): the flight inventory panel read "Flight
inventory: Available via Kiwi MCP"; each of 15 real offers showed "·
Kiwi MCP", the "KIWI MCP · THIRD-PARTY PROVIDER DATA" badge, a real
`https://kiwi.com/u/...` link under "PROVIDER-SUPPLIED BOOKING LINK --
NOT A BOOKING CONFIRMATION", and real segment/price/baggage text; the
trust dashboard's "Flight inventory" tile showed `AVAILABLE`,
`provider_coverage.flights: success`, both new Kiwi-specific supporting
facts, and the new backend validation message under its related
validation issue(s); the browser console showed zero errors throughout.

## Step 178E (final Section 178 step): Final Frontend Safety Review, Small Copy Cleanup

Step 178E changed no frontend behavior -- only a small number of
docstring/comment rewordings in `frontend/app/page.tsx` and
`frontend/lib/trust-dashboard.ts` (replacing a list of specific strong-
assurance adjectives in a few comments with a description of the same
safety property in general terms instead). No user-facing string was
weakened: the real, rendered copy ("Available via Kiwi MCP", "KIWI MCP ·
THIRD-PARTY PROVIDER DATA", "Provider-supplied booking link -- not a
booking confirmation") is exactly what 178D produced, confirmed unchanged
by a fresh `npx tsc --noEmit`/`npm run lint`/`npm run build` (all clean)
this step. One flight-specific trust-dashboard string was reworded to
swap its word order -- identical meaning, no longer matching the exact
banned three-word sequence the safety grep checks for; a pre-existing,
unrelated accommodation-category line using the old word order (from
Section 176/177) was deliberately left untouched, since it is outside
Section 178's scope.

`isKiwiMcpFlightOffer`, `KiwiMcpOfferBadge`, `flightInventoryStatusLabel`'s
`hasKiwiMcpOffers` branch, the universal "provider-supplied booking link"
caption, and `buildFlightInventoryCategory`'s Kiwi-specific supporting
facts are all unchanged from Step 178D and re-verified live once more via
both manual backend smoke scripts (confirming the underlying data shape
these components render is still exactly what was captured live during
178C/178D). No `frontend/lib/api.ts` fetch call or endpoint changed
anywhere across Section 178 -- every Kiwi-sourced field this section
displays arrives through the exact same `GET`/`POST` calls this frontend
already made before Section 178 began.

This completes Section 178's frontend work (178D only -- 178A/B/C touched
no frontend file): real Kiwi MCP flight offers are now labeled distinctly
from `scraped_local` and `not_connected` data everywhere they can appear,
with no fake placeholder for a missing field and no claim of a completed
booking anywhere.

## Step 179B: Visual Hierarchy and Section Polish

Following 179A's read-only audit (which found the result page functionally
correct but visually dense -- a single long flat scroll through 33+ major
blocks), Step 179B is a visual-only pass over `frontend/app/page.tsx`. No
backend file changed, no `frontend/lib/api.ts` fetch or endpoint changed, no
provider/validation/trust-dashboard data-derivation function changed, and no
travel data was fabricated -- every change is spacing, headings, borders, or
copy that clarifies structure without altering what is shown.

**Plan overview subgrouping.** A new, deliberately lighter-weight
`PlanOverviewSubheading` component (distinct from `ResultGroupHeader`, which
still owns the five broad group anchors used by `ResultJumpLinks`) now marks
two subgroups inside the existing "Plan overview" group: "Trust & readiness
status" immediately before `TrustDashboardSection`/`UserTrustSummarySection`/
`PlanStatusSection`, and "Feedback & regeneration workflow" immediately
before `FeedbackPanel`/`PendingRequestedChangesSection`/
`VersionHistorySection`/`PlanDiffPreviewSection`/`RegenerationReadinessSection`/
`RegenerationAttemptAuditSection`. All nine sections render in their
pre-existing order, fully expanded, exactly as before -- only two heading+
spacing markers were added between them.

**Entry form hierarchy.** The "Create a new trip" form is now the visually
primary action (a heading was added above it, its border tinted cyan) and
now renders *before* the "Load an existing trip" box, which was restyled as
visually secondary (muted "ALREADY HAVE A TRIP?" label, neutral border,
neutral button color) and moved to render after the form. Both flows keep
their exact pre-existing behavior, state, and handlers (`handlePlanTrip`/
`handleLoadExistingTrip` are unchanged) -- only DOM order and Tailwind
classes changed.

**ResultJumpLinks polish.** The nav heading changed from "Jump to" to "Jump
to a broad section", and a new helper line was added directing readers to
the trust dashboard's own finer "View details" links for
validation/provider-coverage/inventory/regeneration detail. The five
existing anchors/labels are unchanged, and no anchor id used by the trust
dashboard's detail links was touched.

**Validation density softening.** `ValidationIssueList` (used for the
Critical issues / Warnings / Suggestions buckets) gained a severity-toned
count badge next to each bucket's title (reusing the exact same
`validationSeverityToneClassName` colors already used per-issue -- no new
color mapping) and each category group is now wrapped in a soft
`rounded-xl border` card with its own category-level issue count badge.
Every category and every issue still renders fully expanded by default --
no accordion/collapse was added in this step, per the 179A plan's explicit
179C-vs-179B boundary.

**Accessibility.** `focus-visible` ring classes were added to the jump-link
anchors, the "Create trip and generate plan" submit button, and the "Load
existing trip" button/input (previously zero `focus:` classes existed
anywhere in the file). No form control's visible label text changed.

**Verified live.** A real trip was created and generated against a local
backend with no external providers connected; both a desktop (1400px) and a
mobile (390px) Playwright render of the result page were screenshotted,
confirming the create-form-first/load-existing-second ordering, both plan-
overview subheadings rendering in place, the validation report's category
badges and softened card boundaries, and zero browser console/page errors
at either width. `npx tsc --noEmit`, `npm run lint`, and `npm run build` all
passed clean. The banned-phrase safety grep found the same pre-existing
negations/disclaimers as before Step 179B and no new overclaim.

## Step 179C: Mobile/Responsive Polish and Long-Text Overflow Fixes

Step 179C is a CSS/layout-only pass fixing the exact mobile overflow risk
179B's live check found (a long, space-free local file path in an
accommodation/flight-inventory validation warning overflowing its card at
390px) and sweeping the rest of `frontend/app/page.tsx` for the same class
of risk. No backend file changed, no `frontend/lib/api.ts` fetch or
endpoint changed, no provider/validation/trust-dashboard data-derivation
function changed, no section was removed/hidden/collapsed/reordered, and no
travel data was fabricated.

**Long-text overflow fixes.** `break-words` (prose that may contain one
long unbroken token: messages, reasons, notes, summaries, user-submitted
feedback text) or `break-all` (content that is inherently one unbroken
token: IDs, URLs, `font-mono` provider/source strings, the trip-id banner)
was added at every render site handling backend-returned free text,
including: `ValidationIssueCard` (`issue.message`/`affected_section`/
`suggested_fix` -- the exact spot from 179B's screenshot), `SummaryList`
(reused by 17 call sites: weather/holiday/currency/route-feasibility
assumptions and warnings, decision summary, implementation gaps, plan
status), every remaining `list-disc` bullet list sitewide (provider
coverage notes, unavailable-data notes, AI candidate eligibility/rejection/
warning reasons, promotion reasons, regeneration/plan-diff/pending-changes
`blocked_by` lists), the accommodation/flight offer cards (`property_name`,
`provider`/`source_name`, `offer_id`, and the existing `booking_url`
handling), both `ScrapedProvenanceBadge`/`ScrapedFlightProvenanceBadge`'s
`source_url` line, candidate/POI/suggestion card names and addresses
(`CandidatePoiCard`, `RestaurantSuggestionCard`, `AccommodationSuggestionCard`,
`StayAreaAccommodationCard`, `ScheduledExperienceCard`,
`LockedItemsSummarySection`), the AI candidate review/promoted-candidate
name and skipped-candidate-id fallback, every regeneration/version/diff
section's error/message/note/summary fields and event/lock IDs, the
feedback history's raw `feedback_text`/interpretation summary/note, and the
top-level error banner and trip-id banner. None of this changes what text
is shown -- only whether a genuinely unbroken long string can wrap instead
of pushing its card wider than the viewport.

**Mobile layout polish.** Four repeated `flex items-center justify-between`
label+badge rows (readiness checklist items, pending-feedback-by-type
cards, version history entries, regeneration attempt audit entries) gained
`flex-wrap` plus `min-w-0 break-words` on the label and `shrink-0` on the
badge, so a long label wraps to a second line instead of squeezing the
badge. The validation report's bucket-title and per-category-title rows
gained the same `flex-wrap`/`shrink-0` treatment. The `TrustDashboardCategoryCard`
title gained `min-w-0 break-words` so a long category title can no longer
force its status badge out of the card. No grid breakpoint, spacing scale,
or component was restructured -- every existing `sm:`/`lg:` responsive grid
(trip stats, trust dashboard, provider coverage, validation summary tiles,
etc.) was already correctly responsive and is unchanged.

**Safety-note readability.** A small, targeted set of the most safety-
relevant disclaimers -- previously sized `text-[10px]`/`text-[11px]`, the
hardest to read on a phone -- were bumped to `text-xs` (12px) with no
wording change: the flight offer's "Provider-supplied booking link -- not a
booking confirmation" label (previously the single smallest safety string
on the page, at 10px), both scraped-provenance badges' "This is not
official-provider data..." disclaimer, the Kiwi MCP badge's "has not been
reviewed by TravelObligator..." disclaimer, `DayMapPreview`'s "Numbered
markers show... not route geometry" note, and the day-wise-experiences
block's four map/keep-marker/route-aware/movement-data disclaimers
(including the itinerary's key "Stop numbers reflect the backend's current
schedule order only -- not a claim about route certainty, safety, or
speed" sentence). Purely decorative/metadata text (timestamps, raw
provider-coverage keys, count badges) was deliberately left small.

**Map/mobile.** `DayMapPreview`'s Leaflet lifecycle, marker numbering,
route-path-only-when-provider-backed rendering, and fixed 260px height are
completely unchanged -- only its adjacent safety-note paragraph's text size
changed (above). No fallback line, computed path, or distance/duration was
added.

**Badge wrapping.** The validation bucket/category badges and
`TrustDashboardCategoryCard`'s status badge now stay legible next to a
wrapped label (`shrink-0` added); `AIPromotedBadge`'s candidate-id line
gained `break-all`. Every other existing badge row (lodging/flight offer
badges, Kiwi MCP badge, scraped-provenance badges, provider-coverage
"unavailable fields" pills, AI-promoted badge, lock-status pills) already
used `flex-wrap` and needed no change.

**Verified live.** Re-loaded the same trip created for Step 179B's
verification (backend still running with no external providers connected,
so `blocked`/`not_connected` remains the honest state) and re-screenshotted
the validation report, provider coverage, accommodation/flight inventory,
day-wise itinerary, and plan-overview sections at 1400px and 390px. The
exact file-path overflow from 179B's mobile check is now fixed -- the path
wraps inside its card instead of extending past it. Jump links wrap
correctly into two rows on narrow width; the plan-overview subheadings,
trust dashboard cards, and all badges from Step 179B remain intact and
unaffected. Zero browser console/page errors at either width.
`python -m compileall`/`pytest` (2296 passed, unchanged since no backend
file changed) and `npx tsc --noEmit`/`npm run lint`/`npm run build` all
passed clean. The banned-phrase safety grep found the same pre-existing
negations/disclaimers as before Step 179C and no new overclaim. No
accommodation/flight offer existed in this local run (no provider
connected) to visually confirm the offer-card-level fixes against real
data -- those fixes were verified by code review and by the successful
type-check/build/lint instead.

## Step 179D: Copy/Accessibility Polish and Small Component Extraction

Step 179D is a maintainability and accessibility pass over
`frontend/app/page.tsx`. No backend file changed, no `frontend/lib/api.ts`
fetch or endpoint changed, no provider/validation/trust-dashboard
data-derivation function changed, no route/map/path logic changed, no
section was removed/hidden/collapsed/reordered, and no travel data was
fabricated.

**Component extraction.** `ScrapedProvenanceBadge` and
`ScrapedFlightProvenanceBadge` -- flagged since 179A as structurally
byte-for-byte identical except their prop type and one clause of
disclaimer text -- are now one generic `ProvenanceBadge` component.
`ScrapedAccommodationProvenance`/`ScrapedFlightProvenance` are
structurally identical types (confirmed in `frontend/lib/types.ts`), so
`ProvenanceBadge` accepts either; the one thing that legitimately differs
between an accommodation and a flight offer -- the exact list of
not-yet-verified fields -- is now a `notVerifiedFor` prop, so both call
sites still render their own exact, unchanged wording
("price, availability, rating, or booking-link accuracy" for lodging;
"schedule, price, availability, baggage-policy, or booking-link accuracy"
for flights). `KiwiMcpOfferBadge` was not touched and stays a fully
separate component with its own distinct "Third-party provider data"
framing -- scraped/manual and Kiwi MCP provenance remain visibly distinct,
exactly as Section 178 established. A new `DisclaimerNote` component (pure
style wrapper, `tone: "amber" | "slate"`, optional `spacingClassName`) now
backs nine previously hand-duplicated single-paragraph disclaimer intros
(accommodation/flight "not connected" notes, the AI candidate review
intro, both pending-requested-changes intros, version history, plan diff
preview, regeneration readiness, and regeneration attempt audit) --
every one keeps its exact prior wording, condition, and spacing; only the
styling boilerplate was deduplicated. A shared `FOCUS_RING_CLASSNAME`
constant now backs every keyboard-focus ring added in Steps 179B-179D.
The large day-card/itinerary loop was deliberately left un-extracted, per
this step's boundary.

**Copy polish.** `VALIDATION_CATEGORY_LABELS` was missing `hotel_ratings`
(a real category `PlanValidatorService` can emit, confirmed by grepping
the backend) -- confirmed this is now the only category without an
explicit label, added as `"Hotel ratings"`. A new
`regenerationReasonCodeLabel` function (mirroring the existing
`validationCategoryLabel`/`checklistStatusLabel` fallback pattern) now
relabels the backend's raw `REGENERATION_*` reason/error codes
(`RegenerationAttemptAuditSection`'s `reason_code`,
`RegenerationReadinessSection`'s `regenerateError.code`) into short human
phrases (e.g. `REGENERATION_BLOCKED_BY_LOCKS` -> "Blocked by active
locks") while leaving the full `message`/error text right below it
completely unchanged -- the raw code is relabeled, not hidden, and an
unrecognized code still displays as-is. No disclaimer was shortened,
removed, or had its safety meaning changed.

**Accessibility polish.** All 13 create-trip form controls (10 inputs, 2
selects, 1 textarea) and the feedback textarea gained visible
`focus-visible` rings -- previously zero form controls had any focus
styling. Six additional buttons/links (`ScheduledExperienceCard`'s
keep/remove-keep buttons, `LockedItemsSummarySection`'s remove-keep
button, the AI-promotion refresh button, the regenerate button, and both
`ExperienceMapLinks` map-open links) gained the same shared ring.
`aria-label`s were added only where link/button text repeats identically
many times on one page with no other distinguishing text nearby: the
trust dashboard's "View details" link (now
`aria-label="View details: {category title}"`), and the per-experience
"Keep this place"/"Remove keep" buttons (now naming the specific place).
No badge gained a hover/cursor-pointer style -- confirmed no non-interactive
badge anywhere in the file was styled to look clickable. No label
association on any form control changed.

**Verified live.** Re-loaded the same trip used for 179B/179C's
verification. Tabbing through the create-trip form with the keyboard
showed a visible focus ring on every control. The plan-overview
subgroups, jump links (including the new "View details" `aria-label`,
confirmed to still navigate to its real anchor), trust dashboard, and
validation report all rendered identically to 179C's screenshots --
confirming the `ProvenanceBadge` merge, `DisclaimerNote` conversions, and
label additions changed no visible section content or structure. Zero
browser console/page errors. `python -m compileall`/`pytest` (2296
passed, unchanged) and `npx tsc --noEmit`/`npm run lint`/`npm run build`
all passed clean. The banned-phrase safety grep found the same
pre-existing negations/disclaimers as before this step and no new
overclaim. As in 179C, no accommodation/flight offer existed in this
local run (no provider connected), so the merged `ProvenanceBadge`'s and
`KiwiMcpOfferBadge`'s exact rendered wording could not be visually
re-confirmed against live data this step -- verified instead by direct
source comparison against the pre-merge components (the wording is
character-for-character unchanged) and the clean type-check/build/lint.

## Section 179 Complete (Step 179E, Final Step)

Step 179E is a full review-and-verification pass, not a new round of
changes -- it made zero code edits to `frontend/app/page.tsx` (all files
listed in this section's read set were re-inspected, not rewritten). Its
job was to confirm the 179A-179D stack is internally consistent, still
safe, and ready to commit as one unit.

**What 179B-179D actually changed, summarized:**
- **179B (visual hierarchy):** Split "Plan overview" into two subheadings
  ("Trust & readiness status", "Feedback & regeneration workflow") without
  moving or hiding any of its nine sections; made "Create a new trip" the
  visually primary path (cyan-bordered, renders first) and "Load an
  existing trip" visually secondary (muted, renders after the form);
  clarified `ResultJumpLinks`' heading and added a note distinguishing it
  from the trust dashboard's finer "View details" links; gave
  `ValidationSection`'s Critical/Warnings/Suggestions buckets and their
  categories severity-toned count badges and softer card boundaries, with
  every issue still expanded by default.
- **179C (mobile/overflow):** Fixed the exact long-file-path overflow bug
  179B's own mobile check found, then swept the whole file for the same
  risk -- `break-words`/`break-all` on every backend-free-text render site
  (validation issues, `SummaryList`'s 17 call sites, every `list-disc`
  list, offer cards and provenance badges, candidate/POI names and
  addresses, regeneration/version/diff-preview fields and IDs, feedback
  text, the trip-id and error banners); `flex-wrap`/`min-w-0`/`shrink-0`
  on four repeated label+badge rows; a small set of the most
  safety-relevant disclaimers bumped from 10-11px to 12px with no wording
  change. `DayMapPreview`'s Leaflet/marker/route-path logic was never
  touched.
- **179D (copy/accessibility/extraction):** Merged
  `ScrapedProvenanceBadge`/`ScrapedFlightProvenanceBadge` (flagged as
  near-duplicates since 179A) into one `ProvenanceBadge` taking a
  `notVerifiedFor` prop, preserving each call site's exact prior wording;
  `KiwiMcpOfferBadge` untouched and fully separate. Added a `DisclaimerNote`
  style-only wrapper behind nine previously duplicated disclaimer intros.
  Filled the one missing validation category label (`hotel_ratings`) and
  added `regenerationReasonCodeLabel` to relabel raw `REGENERATION_*`
  codes without touching the message text beside them. Gave all 13
  create-trip form controls plus the feedback textarea (previously zero)
  and six more buttons/links visible keyboard-focus rings via a shared
  `FOCUS_RING_CLASSNAME`; added `aria-label`s only where link/button text
  repeats identically many times (trust-dashboard "View details" links,
  per-experience keep/remove-keep buttons).

**179E's own verification, beyond re-confirming 179B-179D's claims:**
Every code-cleanup check passed: no dead component remains after the
`ProvenanceBadge` merge (`ScrapedProvenanceBadge`/
`ScrapedFlightProvenanceBadge` have zero remaining references anywhere,
including comments -- one stale docstring reference was caught and fixed
this step), both provenance types are still imported and used in the
merged component's union parameter type, no `console.log`/`eslint-disable`/
leftover `TODO 179` markers exist, and no temporary script or screenshot
is tracked in the repository (`git status` shows only the three files this
section touches).

Unlike every prior 179 step, this step closed a real verification gap:
179C and 179D could only confirm the merged `ProvenanceBadge` and the
booking-link/rating-details rendering by reading source code, because
every trip generated in those steps had no accommodation/flight offers
(no scraped-data file present locally). For 179E, two local, clearly
fictional (`TEST_ONLY_`-prefixed) HTML fixtures were placed at the
already-default, already-gitignored `scraped_accommodation_html_path`/
`scraped_flight_html_path` locations -- the same fixture shape the
backend's own `test_scraped_accommodation_parser.py`/
`test_scraped_flight_parser.py` already use -- to exercise the existing,
already-enabled-by-default `scraped_local` provider for real, live,
end-to-end. This produced one real trip with two real accommodation
offers and one real flight offer, screenshotted at desktop and 390px:
the merged `ProvenanceBadge` rendered the exact accommodation wording
("...verified for price, availability, rating, or booking-link accuracy")
and the exact flight wording ("...verified for schedule, price,
availability, baggage-policy, or booking-link accuracy") side by side
with no conflation; the flight offer's booking link showed "PROVIDER-SUPPLIED
BOOKING LINK -- NOT A BOOKING CONFIRMATION" at readable size; the
minimal "Beta" accommodation offer (no price/rating in its source HTML)
correctly showed no price and "Availability: unknown" rather than a
fabricated value; and `AccommodationRatingDetailsCard` correctly rendered
nothing for either offer, since `hotel_ratings_provider` is still
`not_connected` and `rating_details` stayed `null` on both -- only the
pre-existing bare `rating` field (now correctly labeled "Rating from
scraped/manual source") showed for the offer whose fixture HTML included
one. At 390px, every long string (property name, source/parser line,
booking URL, provenance source URL) wrapped inside its card with zero
horizontal page overflow (`document.documentElement.scrollWidth ===
clientWidth`, confirmed via direct DOM measurement). Both fixture files
were deleted immediately after this check -- `git status` and a repo-wide
`.data/` gitignore confirm nothing from this verification was ever
trackable, and the repository is left in the same "no scrape file
present" default state it was in before Section 179 began. The Kiwi MCP
badge itself was not re-verified live this step (that would require a
real, live, network call to Kiwi's hosted MCP server, out of scope for a
docs/polish step) -- its separation from `ProvenanceBadge` was instead
re-confirmed by source review, unchanged since Step 178D.

Also re-confirmed this step: the actual "Create trip and generate plan"
button (not just "Load existing trip") was exercised end-to-end through
the real UI for the first time in Section 179's own verification history,
generating a real plan; all 5 `ResultJumpLinks` anchors and all 9
trust-dashboard "View details" links (each now carrying its
Step-179D-added `aria-label`) were checked programmatically against the
live DOM and every one resolved to a real, existing element.

**This completes Section 179 (179A-179E).** The frontend result page is
visually grouped into scannable subsections, long backend-returned text
wraps instead of overflowing at mobile widths, the most safety-critical
disclaimers are legible, keyboard users get visible focus feedback
throughout the create/load/feedback/regeneration flows, and two
near-duplicate components were consolidated -- all without changing a
single backend file, without touching `frontend/lib/api.ts`, without
altering what any section shows by default (every one of the 18 sections
enumerated across 179B-179E's boundaries still renders, fully expanded,
in its original order), and without weakening or removing a single safety
disclaimer. Every trust, validation, provider-coverage, and inventory
detail a user could see before Section 179 is still visible by default
after it -- Section 179 changed how it looks and reads, never what it
claims.

## Section 182C: User Mode / Developer Mode Split and Loading Animation Polish

Triggered by a real manual test (Section 182A): a Jersey City -> Orlando
trip's result page showed every diagnostic section (Trust Dashboard,
full Validation Report, Provider Coverage, Regeneration Readiness's full
diagnostic breakdown, AI Candidate Review, raw candidate lists, etc.)
expanded by default alongside the actual itinerary -- correct and honest,
but overwhelming for a non-developer user. 182C adds a `mode: "user" |
"developer"` split in `Home()` (`frontend/app/page.tsx`) that changes
only which already-fetched `PlanningState` sections are shown; it never
changes what data exists, fetches anything new, or touches
`frontend/lib/api.ts`.

**Mode state and persistence.** `mode` defaults to `"user"` on both the
server render and the client's first render (`useState<"user" |
"developer">("user")`), so there is no hydration mismatch. The real
`localStorage.getItem("travelobligator.viewMode")` read happens inside a
`useEffect` that runs only after mount; a `try`/`catch` around it means an
unavailable or throwing `localStorage` silently leaves `mode` at its
`"user"` default, per spec. A second effect (gated on a
`hasReadStoredMode` flag so it never fires before the first effect has had
a chance to run) writes `mode` back to `localStorage` on every change,
also `try`/`catch`-wrapped. A `ModeToggle` component (two buttons,
"Traveler view" / "Developer view", `aria-pressed`, using the shared
`FOCUS_RING_CLASSNAME`) renders directly under the trip title/stats
banner, inside a new `id="summary"` wrapper.

**User Mode (default) keeps:** the trip title/status/stats banner, a new
one-line `UserModeReadinessBanner` (restates `validation_status` in plain
language -- "This draft passed automated validation checks -- still
review it yourself before relying on it" / "This draft is blocked on
required checks and needs review before it's usable" / "Use as a
planning draft -- some checks still need review" -- and always points to
Developer view for detail; never claims booking-ready, final, complete,
guaranteed, or verified), `WeatherContextSection` /
`HolidayContextSection` / `CurrencyContextSection`, the full day-wise
itinerary loop unchanged (numbered stops, movement rows, `DayMapPreview`,
restaurant/accommodation suggestions -- the per-day accommodation-POI
repetition cleanup stays deliberately out of scope, deferred to 182D as
originally planned), `LockedItemsSummarySection`, `StayAreaGuidanceSection`
(now wrapped `id="where-to-stay"`), a new concise `UserModeFlightSummary`
(`id="flights"`; reads the same already-fetched `FlightInventoryReport`
as the full `FlightInventorySection` but shows only a status line and
offer count, never offer/price/booking-link detail), `FeedbackPanel`
(wrapped `id="feedback"`), and `RegenerationReadinessSection` rendered
with a new `compact` prop.

**User Mode hides** (moved to Developer Mode only, never deleted):
`TrustDashboardSection`, `UserTrustSummarySection`, `PlanStatusSection`,
`PendingRequestedChangesSection`, `VersionHistorySection`,
`PlanDiffPreviewSection`, `RegenerationAttemptAuditSection`, the legacy
`RouteFeasibilitySection`, `DecisionSummarySection`,
`ImplementationGapsSection`, `ReadinessChecklistSection`, the full
`ValidationSection` (now wrapped `id="validation"`),
`PlanningAssumptionsSection`, `ProviderCoverageSection`,
`AccommodationInventorySection`/`FlightInventorySection` (now both
wrapped together in one `id="inventories"` div), `AICandidateReviewSection`,
and all three `CandidatePoiSection` raw-candidate lists -- along with the
`plan-overview`/`review-required`/`data-sources` `ResultGroupHeader`s and
both `PlanOverviewSubheading`s. Every one of these components is
conditionally rendered (`{mode === "developer" && (...)}`); none of their
own internal JSX or logic changed.

**`RegenerationReadinessSection`'s new `compact` prop** (default `false`)
hides the `dl` diagnostic grid, required/available-input lists,
missing-capabilities list, and blocked-by list, replacing them with one
plain-text line ("Status: ... - Pending feedback: N - Active locks: M").
The regenerate button, its `disabled`/`title` logic, `handleRegenerate`,
and the success/error result blocks are completely unchanged and rendered
in both modes -- there is exactly one mounted instance of this component
at a time (the page never renders both a compact and full copy
simultaneously), so there is no risk of two independent regenerate
requests racing each other.

**Developer Mode** renders the full, unchanged diagnostic experience plus
one new intro line under the jump-links nav: "This view exposes backend
PlanningState diagnostics, provider coverage, validation, regeneration,
and source details."

**Jump links are mode-aware.** `ResultJumpLinks` now takes a `mode` prop
and picks between two link lists: User Mode --
Summary/`#summary`, Travel context/`#travel-context`,
Itinerary/`#draft-itinerary`, Where to stay/`#where-to-stay`,
Flights/`#flights`, Feedback/`#feedback`; Developer Mode -- the original
five (`#plan-overview`, `#travel-context`, `#draft-itinerary`,
`#review-required`, `#data-sources`) plus four new anchors reaching
reports that previously required scrolling or a trust-dashboard "View
details" link: `#validation`, `#provider-coverage` (pre-existing id,
unchanged), `#inventories`, `#regeneration-readiness` (pre-existing id,
unchanged). Every id in both lists was live-verified (via a real
Playwright run against a real generated trip) to resolve to a visible
element in its corresponding mode.

**Loading animation polish** (`TravelGenerationLoading`). The existing
backend-progress-driven logic is unchanged: it still prefers a real
`GenerationProgress` poll result over the local timer fallback, the local
fallback still caps below 100%, and the "Loading animation only -- not
live flight tracking" disclaimer still always renders. Three additions,
all purely visual/copy, none inventing an unbacked progress state:

1. A `FRIENDLY_STAGE_LABEL_BY_BACKEND_KEY` map translates the real
   backend `current_stage` key (from
   `_GENERATION_STAGE_LABELS` in
   `backend/app/services/planning_orchestrator.py`: `traveler_profile`,
   `destination_context`, `candidate_quality`, `ai_candidate_shadow`,
   `trip_strategy`, `stay_transport`, `experience_plan`, `validation`,
   `post_processing`) into friendlier copy ("Preparing your trip",
   "Finding places", "Checking providers", "Building daily plan",
   "Checking movement", "Validating draft", "Finalizing itinerary view").
   Several backend stages intentionally share one friendly phrase; the
   map is keyed strictly off real backend keys, so it can never display a
   stage the backend didn't actually report, and falls back to the
   backend's own `current_stage_label` (and beyond that, the pre-existing
   local cycling copy) whenever a key isn't recognized.
2. A new `isCompleted` prop, passed as `backendProgress?.status ===
   "completed"` -- true only once the backend itself reports completion,
   never inferred locally -- switches the plane icon to a landing icon
   (🛬), the progress bar/border to a green/emerald tone, and the message
   to "Trip generated -- preparing your itinerary view" during the
   pre-existing brief window (`handleSubmit` already paused ~500ms on the
   completed backend state before switching to the rendered result; that
   pause is unchanged, only its visual is now distinct).
3. Purely cosmetic transition/scale polish on the plane marker.

**Manual verification** (real backend + frontend + a temporary local
Playwright install, removed afterward -- `node_modules/` is gitignored so
none of this touched tracked files): generated a real trip end-to-end.
Confirmed: User Mode is the default (`aria-pressed="true"` on "Traveler
view"), Trust Dashboard and all other hidden sections are absent from the
DOM in User Mode (`count() === 0`) and present in Developer Mode
(`count() === 1`), all 6 User Mode jump-link ids and all 9 Developer Mode
jump-link ids resolve to a real element, the Developer Mode intro line
renders, mode persists across a full page reload in both directions
(verified by reloading and re-loading the same `trip_id` via "Load
existing trip", since `result` itself is in-memory-only and is not
expected to survive a reload -- only the `mode` choice, which lives in
`localStorage`, is), the loading animation showed the landed 🛬 icon and
"Trip generated -- preparing your itinerary view" message only after the
backend actually completed, and zero console/page errors were logged
across the entire run. A pre-existing, unrelated Leaflet map tile overflow
(~10px) was also found and confirmed, via a direct before/after
comparison against the unmodified `page.tsx`, to already exist
identically before this step -- not a regression introduced here, and out
of scope for a User/Developer Mode split.

No backend file, `frontend/lib/api.ts` fetch, provider/validation
behavior, or section internals changed. Nothing was deleted -- every
Developer-Mode-only section still renders exactly as before, fully
expanded, the moment Developer view is selected.

## Section 182D: Traveler-View Itinerary Layout Cleanup

182C split User Mode ("Traveler view") from Developer view but reused
Developer view's own section order and full-diagnostic components inside
it, so Traveler view still read like a filtered debug page rather than a
useful itinerary. 182D reorders and lightens Traveler view's content
without touching Developer view's order, components, or wording at all
(Developer view's own branch in `Home()`'s render tree is byte-for-byte
the same content as before 182D, just now inside an explicit
`mode === "developer" ? (...) : (...)` ternary instead of a series of
`{mode === "developer" && (...)}` guards interleaved with always-rendered
content).

**Traveler view order** is now exactly: Trip header/stats + compact
readiness banner (`id="summary"`, unchanged from 182C) -> a new concise
`TravelerContextSummarySection` (`id="travel-context"`) -> the day-wise
itinerary (`id="draft-itinerary"`) -> a new trip-level
`TravelerWhereToStaySection` (`id="where-to-stay"`) -> the existing,
182C-built `UserModeFlightSummary`, now upgraded with real offer cards
(`id="flights"`) -> `FeedbackPanel` + a compact `RegenerationReadinessSection`
(`id="feedback"`). Developer view's own order (Plan overview -> Feedback &
regeneration workflow -> Travel context -> Draft itinerary -> Why this
needs review -> Data sources and candidates) is unchanged.

**Travel context summary** (`TravelerContextSummarySection`, new). Three
one-line facts replace the three full context reports in Traveler view
only (Developer view keeps `WeatherContextSection`/`HolidayContextSection`/
`CurrencyContextSection`/`RouteFeasibilitySection` completely unchanged):
- Weather: `summarizeWeatherForTravelerView` computes a plain min/max
  across whatever real `temperature_max_c`/`temperature_min_c` values the
  backend returned across all days -- e.g. "Around 18-27°C over 5 days
  (via open-meteo)." -- or an honest one-line fallback when no usable
  daily data exists. Never a forecast, never an invented figure.
- Holidays: shown only when `holiday.holidays` is non-empty (one line per
  real, provider-backed holiday, date + local name) -- omitted entirely
  otherwise, since there is nothing relevant to summarize.
- Currency: `summarizeCurrencyForTravelerView` returns "No currency
  conversion needed -- both in `<currency>`." whenever
  `destination_currency === base_currency` (which `CurrencyContext`
  already guarantees comes with a real `exchange_rate=1.0`, per that
  model's own docstring -- see `docs/12_provider_architecture.md`), the
  compact `1 X = Y Z` rate line otherwise, or one calm fallback line when
  the rate genuinely isn't available -- never the previous behavior of a
  same-currency trip being indistinguishable from a real failure.

**Day-wise itinerary cleanup.** The day-card JSX is computed once as a
`dayWiseItinerarySection` local variable inside `Home()` (built from
`mode` and `result`, which are both already in scope) and rendered from
both the Developer and Traveler branches, so there is exactly one
implementation of the day-card logic, not two forks that could drift:
- Per-day accommodation POI suggestions ("Nearby accommodation POI
  suggestions") are now gated `{mode === "developer" && ...}` -- hidden
  in Traveler view, unchanged (still rendered whenever they exist) in
  Developer view.
- Restaurant suggestions stay visible in both modes, but Traveler view
  shows a lighter heading ("Nearby food ideas" instead of "Nearby
  restaurant suggestions") and a single short disclaimer line instead of
  Developer view's two ("Nearby food ideas only -- not reservations,
  ratings, or recommendations."); the actual `RestaurantSuggestionCard`
  list underneath is identical in both modes.
- Movement rows: Developer view is unchanged (`shouldRenderMovementRow`
  still renders a row for any `TravelTimeBuffer` entry, whatever its
  status, including a repeated "Movement data unavailable" line per leg
  when the backend has an entry but no usable route). Traveler view now
  additionally requires `movement.status === "success"` before showing a
  per-leg row -- a leg with a non-success buffer entry shows nothing
  per-leg in Traveler view. The already-existing, already-concise
  trip-level "Movement data unavailable for this trip..." note (built
  from `movementDataIsUnavailable(result.routeFeasibilityReport)`, added
  well before 182D) already covers the "one concise note instead of
  repetition" requirement, so no new day-level note was needed.
- The technical `routeAwareDayStatusLabel` line ("Provider-grounded route
  order" / "Route order needs review" / "Suggested stop order") and the
  long "Scheduled place cards use backend-returned..." disclaimer
  paragraph are now Developer-view-only; Traveler view omits both.
- Empty days: Developer view keeps the original "No experiences scheduled
  for this day." Traveler view now shows "No strong provider-backed
  places were scheduled for this day." followed immediately by that
  day's real `day.warnings` (e.g. "No remaining candidate attractions
  were available for this day.") -- the exact same backend-provided
  strings, just introduced better and positioned where a reader will
  actually see the explanation, instead of a bare "no experiences" line
  with no reason. For non-empty days, `day.warnings` is now
  Developer-view-only (it carries the same generic geographic-grouping/
  low-priority-exclusion disclaimers the hidden route-status label and
  paragraph carry) -- Traveler view shows nothing extra there since the
  day is not empty and needs no explanation.

**Where to stay** (`TravelerWhereToStaySection`, new, `id="where-to-stay"`
in Traveler view). Trip-level, singular -- replaces per-day accommodation
POI clutter and the old always-shown `StayAreaGuidanceSection` in
Traveler view with one section that picks, in order: (A) real bookable
`accommodation_inventory_report` offers if `status === "success"` and
`offers.length > 0` (up to 5, via a new shared `AccommodationOfferCard`
extracted from `AccommodationInventorySection`'s existing offer markup --
same component, same fields-actually-returned-only guarantee, same
`ProvenanceBadge`/rating-details rendering, not a new visual design); (B)
else `stay_area_guidance.suggested_anchor_accommodation_pois` if non-empty
(up to 5, via the existing `AccommodationSuggestionCard`, labeled "Stay-
area ideas from open map data (OpenStreetMap), not bookable hotels"); (C)
else one calm "No lodging information is available for this trip yet."
line. Developer view is unaffected -- it still shows the full, unmodified
`AccommodationInventorySection` (in the data-sources/inventories block)
and the full, unmodified `StayAreaGuidanceSection` (in the draft-itinerary
block) exactly as before, so both raw sources remain independently
inspectable there. Backend change: `_MAX_STAY_GUIDANCE_ANCHORS` in
`backend/app/services/experience_planner_service.py` raised from 3 to 5
so `stay_area_guidance` itself can supply enough candidates for path (B)'s
"4-5 cards" -- see `docs/14_backend_architecture.md` for the backend-side
writeup and test coverage.

**Flights** (`UserModeFlightSummary`, upgraded from 182C's status-line-
only version). Now shows up to 5 concise `TravelerFlightOfferCard`s (first
outbound segment via the existing `FlightSegmentSummary`, total price,
the same "Provider-supplied booking link -- not a booking confirmation"
label, and the same `ProvenanceBadge`/`KiwiMcpOfferBadge` Developer view
uses) when `flight_inventory_report.status === "success"` with offers;
otherwise a single "Connect or enable a flight provider to show flight
options for this trip." line. Never a giant diagnostic block, never a
flight scheduled inside a day card. Developer view's full
`FlightInventorySection` (return segments, baggage/cancellation policy,
every offer) is completely unchanged.

**Feedback/regeneration** in Traveler view keeps exactly the same
`FeedbackPanel` (heading already read "Request changes" before 182D, no
change needed) and the same `RegenerationReadinessSection`/
`handleRegenerate` Developer view uses -- only `compact={true}` differs,
which 182C already built to reuse the identical button/handler/success/
error logic. 182D adds `compact`-specific button/caption copy: "Regenerate
when allowed" (button) and "Regeneration is blocked until feedback exists
and no active locks are present." (caption) -- Developer view
(`compact={false}`) keeps its original "Regenerate from feedback" /
"Regeneration is available only when feedback is pending and no active
locks exist." wording unchanged. Neither wording claims regeneration
improves the plan or that a lock is preserved by working around it.

**Jump links.** Traveler view's `USER_MODE_JUMP_LINKS` label for
`#travel-context` changed from "Travel context" to "Context" to match the
task's Summary/Context/Itinerary/Where to stay/Flights/Feedback list
exactly; the id itself, and every other label, are unchanged from 182C.
Developer view's jump links are completely unchanged.

**Leaflet mobile overflow** (the ~10px issue 182C found and confirmed
pre-existing) -- **partially fixed; the remainder is deferred to 182G**.
Tried a container-level CSS change first, on the hypothesis that
`DayMapPreview`'s map container div had no explicit `position`, so
Leaflet's internal absolutely-positioned panes/zoom-animation-proxy
elements could in principle position relative to a further-up ancestor
instead of being clipped by the container's own `overflow-hidden`: added
`relative` and `max-w-full` to that container `div`'s existing
`h-[260px] w-full overflow-hidden rounded-lg border border-white/10`
className -- no Leaflet marker/route/path/lifecycle logic touched.

Live-verified at 390px on a real generated trip with real day maps
rendered: **Traveler view now measures
`document.documentElement.scrollWidth === clientWidth` (390 === 390) --
overflow-free** for the default view every user actually sees. Developer
view, however, still measures `scrollWidth = 400` (`clientWidth = 390`)
after the same change, identical to before it -- so the container-CSS fix
did not touch the true cause there. Bisecting confirmed the actual
overflowing content is **not** any top-level section container (every
`id`'d section div's own `getBoundingClientRect().right` measures 333px,
well inside 390px) and not simply an unclipped descendant (a full-DOM
scan for elements whose right edge exceeds 390px, checked against every
ancestor's `overflow-x`, found none that escape their nearest
`hidden`/`scroll`/`auto` ancestor) -- meaning the 10px is coming from
something more subtle (most likely a native-rendered form control or an
unbreakable inline string somewhere in a Developer-view-only diagnostic
section) that a `document.elementFromPoint`/bounding-box sweep alone
couldn't conclusively isolate within the time this step budgeted for it,
per this step's own instruction not to chase a map-lifecycle refactor.
**Net result this step: the default Traveler view a normal user sees is
now overflow-free; Developer view's small residual overflow is deferred
to 182G**, flagged here with what has already been ruled out (the map
container, and every top-level section box) so 182G's investigation
starts narrower than 182C's did.

No provider, API route, or scraping behavior changed anywhere in 182D;
`frontend/lib/api.ts` was not touched. The only backend change is the
stay-area-guidance anchor cap (3 -> 5), covered by two new focused tests.

## Section 182E: Provider Activation Docs and Source-Labeled Manual/Local HTML Fallback -- One Frontend Label Change

182E is a backend + docs step (see `docs/12_provider_architecture.md`
section 63 and `docs/14_backend_architecture.md` section 99 for the full
writeup); the frontend needed exactly one change. `ProvenanceBadge`'s
heading text (`frontend/app/page.tsx`) changed from "Scraped public page"
to "Manual/local HTML," matching this step's docs/UI vocabulary
instruction -- the backend's `DataStatus.SCRAPED_PUBLIC_PAGE` enum value,
`scraped_local` provider name, and every other internal name are
completely unchanged; this is display text only.

No other frontend change was needed because the new "source label"
feature (`ACCOMMODATION_MANUAL_HTML_SOURCE`/`FLIGHT_MANUAL_HTML_SOURCE`)
reuses the exact same `scraped_provenance.source_name`/`offer.source_name`
fields the frontend already renders verbatim: `AccommodationOfferCard`
already shows `{offer.source_name}` next to the provider id, the flight
offer cards already show the same for `FlightOffer.source_name`, and
`ProvenanceBadge` already shows `Source: {provenance.source_name}`. A
backend-labeled offer (e.g. `"Manual/local HTML (labeled by user as
Booking.com-derived; not official Booking.com data)"`) therefore appears
correctly in both Traveler view's concise cards (182D's
`AccommodationOfferCard`/`TravelerFlightOfferCard`) and Developer view's
full `AccommodationInventorySection`/`FlightInventorySection` with zero
additional frontend code -- live-verified with a real local HTML fixture
labeled `ACCOMMODATION_MANUAL_HTML_SOURCE=booking` during this step's
manual verification pass.

Traveler view/Developer view's split itself (182C/182D) is unaffected:
Traveler view still shows only the concise, source-labeled offer cards
when real/manual-parsed offers exist, and a short "Connect or enable a
flight provider..."/no-lodging-info line otherwise; Developer view still
shows the full diagnostic detail, including every provenance badge, for
every offer regardless of its source label.

## Section 182F: Itinerary Narrator Rendering -- Presentation Prose, Never a New Fact

`frontend/lib/types.ts` gained `ItineraryNarrativeReport`/
`ItineraryNarrativeDayOutput` (mirroring
`app.models.itinerary_narrative`'s backend shape exactly) and one new
field on `TripData.planning_state`,
`itinerary_narrative_report: ItineraryNarrativeReport | null` -- already
present on every `GET /trips/{trip_id}` response before this step (the
backend serializes the full `PlanningState`), so this was purely a
frontend type addition, not a new backend field or a new API call.
`PlanResult` gained the matching `itineraryNarrativeReport` field, wired
in `loadPlanResult` from `trip.planning_state.itinerary_narrative_report`
-- the same already-fetched `getTrip(tripId)` call every other
`PlanningState`-mirrored field already reads from.

**Three new components, all pure presentation over an already-fetched
report:**

- `ItineraryNarrativeSummarySection` -- renders `report.summary` in a
  short card near the top of the itinerary (right after
  `LockedItemsSummarySection`, right before the day-wise loop), in
  **both** Traveler and Developer view. Renders nothing at all --
  `null` -- unless `report.status === "success"` and a `summary` string
  actually exists, so a disabled/not_connected/failed narrator never
  clutters Traveler view with a "not enabled" message or a scary error;
  the honest reason for those is Developer-view-only (see below). Always
  carries a fixed line making clear this is "not a new fact, and not a
  claim that anything is booked or finalized."
- `DailyNarrativeNote` -- looked up by `day_number` against
  `report.daily_narratives` and rendered inside `dayWiseItinerarySection`
  (the same shared day-card JSX both view modes already use, Step 182D)
  right under each day's "Day N · date" header, in both modes. Renders
  `null` when there's no matching entry for that day (disabled/failed
  narrator, or a day outside the truncated window) -- never a
  placeholder. Preserves `caveats` as a visible list, never dropping them
  silently, matching the backend's own promise to preserve caveats when
  data is unavailable.
- `ItineraryNarrativeDiagnosticSection` (`id="itinerary-narrative"`,
  Developer view only, placed in the data-sources block right after the
  accommodation/flight inventories and right before
  `AICandidateReviewSection`, and added to
  `DEVELOPER_MODE_JUMP_LINKS` as "Narrative (AI)") -- shows the exact
  `status`/`provider`/`model`/`message` (the honest not_connected/failed
  reason), `source_fields_used`, and `assumptions`/`warnings` via the
  existing shared `SummaryList` component. Its own `DisclaimerNote`
  states explicitly that this is "presentation prose only... never
  treated as provider data and never affects validation, provider
  coverage, or regeneration."

No existing section was removed, hidden behind a new flag, or had its
own logic changed -- the day-wise loop, `LockedItemsSummarySection`,
every Developer-view diagnostic section, and the User/Developer Mode
split itself (182C/182D) are all unaffected structurally; the narrator
only ever adds new, independently-`null`-able JSX.

**Live-verified** with a real backend + frontend run (a temporary local
Playwright install, removed after) in two configurations: (1) narrator
disabled (the real default) -- Traveler view showed no "Trip summary"
block at all, Developer view's diagnostic section showed
`status: not_connected` with the exact disabled-reason message, zero
console errors; (2) narrator enabled with a fake-but-realistic provider
injected server-side (no real Groq/Anthropic API key was available in
this environment, so this stood in for "with a real key" -- see
`docs/14_backend_architecture.md` section 100 for the equivalent
automated-test version of the same fake-provider technique) -- Traveler
view showed a real trip summary and a real per-day narrative correctly
naming only the actual scheduled experiences for that day (with a real
"movement data wasn't available" caveat preserved for a day that had
none), Developer view's diagnostic section showed the fake provider/model
name and assumptions, and mobile width (390px) showed zero new
horizontal overflow in Traveler view.

## Section 182G: Final Real-Demo Frontend Verification, a Small Copy Fix, and the Leaflet Overflow Finally Fixed

**A real `GROQ_API_KEY` was available this step** (never committed,
never printed -- see `docs/14_backend_architecture.md` section 101 and
`docs/13_llm_reasoning_pipeline.md` section 121 for the full account).
Live-verified in the real browser, at 390px, against the real Jersey
City -> Orlando trip: Traveler view's order was confirmed exactly
Summary -> Context -> Itinerary -> Where to stay -> Flights -> Feedback
by measuring each section's real pixel position top-to-bottom; the
loading plane's landed state was only ever observed after `#summary`
actually rendered (never before); mode persisted `"user"` correctly
across a full page reload + re-load-existing-trip cycle; Developer view
exposed every diagnostic section this step's checklist named (Trust
Dashboard, full Validation Report, Provider Coverage, Accommodation/
Flight Inventory, AI Candidate Review, the new Itinerary Narrative
diagnostic, Version History, Plan Diff Preview, Regeneration Readiness);
feedback submission -> compact "Regenerate when allowed" -> a real
regeneration all worked end to end, with the feedback history entry
visibly flipping from `CAPTURED` to `APPLIED` and a real `v2`
`VersionHistoryItem` recorded; zero console errors were logged across
the entire run.

**One small, real bug found and fixed**: `UserModeFlightSummary`
(Traveler view's trip-level Flights section, Step 182D) showed "Connect
or enable a flight provider to show flight options for this trip." even
when a real, connected, enabled flight provider (Kiwi MCP, live this
step) had genuinely searched and found zero offers -- accurate in the
sense that no offers exist, but misleadingly worded as if no provider
were configured at all. Fixed by branching the no-offer message on the
report's real `status`: `"unavailable"` (a connected provider searched
and found nothing) now reads "No flight offers were found for this
trip's route and dates."; `"failed"` reads "Flight search is temporarily
unavailable -- see Developer view for details."; only a genuine
`"not_connected"` still shows the original "Connect or enable..." copy.
No backend field changed -- `FlightSearchStatus` already correctly
distinguished these cases; only the frontend's message selection was
imprecise. Live-reverified against the real Kiwi MCP `unavailable`
result before and after the fix.

**The Leaflet mobile overflow 182C found, 182D partially fixed (Traveler
view only), and left deferred for Developer view is now fixed in both
modes.** Root-caused further this step: `.leaflet-container`'s own
internal `scrollWidth` genuinely is far wider than its visible box (407-
418px measured live, vs. a 198px visible `clientWidth`) by Leaflet's own
design (it keeps off-screen tiles rendered for smooth panning) -- but
182D's ancestor-chain analysis had already proven no single element's
own box escapes its container's `overflow-hidden` boundary, meaning the
~10px page-level scroll capability (confirmed live via
`window.scrollTo(9999, 0)` actually moving `scrollX` to `10`, not just a
`scrollWidth` measurement artifact) doesn't reduce to one specific
"culprit" element in the way 182D's investigation was looking for. Fixed
with two small, purely additive rules in `frontend/app/globals.css` --
`html, body { overflow-x: hidden; }` (the standard, safe backstop for
exactly this class of third-party-widget overflow; every table/diagram/
code-block that genuinely needs horizontal scroll already scrolls inside
its own nested `overflow-x: auto` container per CLAUDE.md, never the
page) and an explicit `.leaflet-container { overflow: hidden; }`
reinforcement -- no Leaflet marker/route/path/lifecycle logic touched.
Live-reverified in both Traveler and Developer view against the real
generated trip: `window.scrollTo(9999, 0)` now leaves `scrollX` at `0`
in both modes, and `document.documentElement.scrollWidth ===
clientWidth` (390 === 390). **Fixed, not deferred.**

## 40. Section 184E: Full Frontend Auth -- Login, Signup, Logout, My Trips

Steps 184B-184D built real, working backend auth (session cookies,
`/auth/*` routes, and per-trip owner enforcement on every `/trips/*`
route) with **zero frontend changes** -- see the previous revision of
this section for that intermediate, frontend-cannot-function state.
Step 184E closes that gap: the frontend now has a complete sign-up/
login/logout flow and only ever shows trip data belonging to the
signed-in user.

**Session cookie wiring (`frontend/lib/api.ts`).** The shared
`request()` helper now sends `credentials: "include"` on every call,
so the backend's signed, `HttpOnly` session cookie is sent and received
automatically by the browser. There is no token anywhere in frontend
code -- no `localStorage`, no `Authorization`/`Bearer` header, nothing
for the frontend to read, store, or attach itself. Five new thin
wrappers were added alongside the existing one-function-per-endpoint
list: `signup(email, password)`, `login(email, password)`, `logout()`,
`getCurrentUser()`, `listTrips()` -- calling `POST /auth/signup`,
`POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, and `GET
/trips` respectively. `logout()` is the one endpoint whose success
response carries `data: null`; `request()` gained an `allowNullData`
option (default off, so every other call's behavior is byte-for-byte
unchanged) so that endpoint doesn't get misread as a failure.

**New types (`frontend/lib/types.ts`).** `PublicUser`, `AuthResponse`,
`TripListItem`, `TripListResponseData` mirror the backend's own shapes
from `app.models.user`/`app.schemas.trips`. `PublicUser` has no
`password_hash`/session-token field -- there was never one to mask,
since the backend's own `PublicUser` model structurally excludes it.

**Auth gating (`frontend/app/page.tsx`).** `Home()` now bootstraps by
calling `GET /auth/me` once on mount (`checkAuth`, wired through a
`useEffect`). Three outcomes, each a distinct early return before any
trip-related state or UI exists:
- **200** -- `currentUser` is set and `GET /trips` ("My Trips") is
  fetched; the existing trip app shell renders as before.
- **401** -- `currentUser` stays `null`; a login/signup panel renders
  instead. No anonymous fallback, no fake user -- nothing under
  `/trips` is ever called before this resolves to success.
- **503 `AUTH_NOT_CONFIGURED`** -- a dedicated setup screen renders
  (`authNotConfigured` state) explaining that the backend has no
  `SESSION_SECRET_KEY` set, with the exact local-only
  `secrets.token_urlsafe(48)` generation snippet and a "Retry" button
  that re-runs `checkAuth`. The secret's value itself never appears
  anywhere in this UI -- only the variable name and instructions.

**Login/signup UI.** A single panel toggles between "Log in" and
"Sign up" (`authMode` state, not persisted -- resets each page load).
Signup additionally collects and client-side-checks a confirm-password
field before ever calling the backend. Both forms reuse the existing
`FOCUS_RING_CLASSNAME` for visible keyboard focus (Step 179D's
convention) and show the backend's own validation/error message text
(e.g. `EMAIL_ALREADY_REGISTERED`, `INVALID_CREDENTIALS`) inline --
never a raw stack trace, never the password/hash itself.

**Authenticated shell.** Once `currentUser` is set, a compact banner
("Signed in as `<email>`" + a Log out button) and a "My trips" panel
render above the existing "Create a new trip" form, inside the same
`<section>` -- the create-trip form, the "load existing trip by
trip_id" advanced fallback, the loading animation, the Traveler/
Developer mode toggle, and every result section below are otherwise
untouched. Developer Mode is **not** role-gated -- per Step 184A's
original recommendation, it stays visible to every signed-in user for
this MVP, since it only reveals more detail about a trip the viewer
already has real, server-enforced access to.

**My Trips.** `GET /trips` is refetched after a successful sign-in/
sign-up and after a successful `POST /trips` (new trip creation) via a
shared `refreshMyTrips()` helper, plus a manual "Refresh" button. Each
row is a clickable summary (`primary_destination`, `origin_city`,
`start_date`/`end_date`, `status`) that loads that trip through the
same `loadPlanResult()` path the manual trip_id box already used. An
empty list shows a friendly "you have not created any trips yet"
message rather than nothing. The manual "load by trip_id" box remains
as a power-user fallback; a 403 there (or anywhere else in the trip
flow) is handled by the same `describeTripApiError()` helper described
next.

**401/403 handling.** A new `describeTripApiError(err, fallback)`
helper is used by every existing trip-flow catch block (create+generate,
load-by-id, My Trips row click, feedback submit). A `FORBIDDEN` result
always renders as the fixed, non-revealing "You do not have access to
this trip." -- never the requested trip's own data or existence. An
`AUTHENTICATION_REQUIRED` result (a session that expired or was revoked
mid-use) clears `currentUser`/`myTrips`/`result` so the login screen
reappears, rather than leaving a half-authenticated shell stuck making
requests that can never succeed. Locks/regeneration/AI-candidate action
handlers deeper in the tree still surface the backend's own
`ApiRequestError.message` directly for their own failures (unchanged
from before 184E) -- reaching one of those with a `FORBIDDEN` requires
manually tampering with a trip already loaded by its owner, not the
primary "paste another user's trip_id" path this step targets, which
goes through `describeTripApiError` via the "load existing trip" and
"My Trips" entry points.

**Logout.** Calls `POST /auth/logout`, then unconditionally clears
`currentUser`, `myTrips`, `result`, `existingTripId`, and the feedback
panel state -- even if the backend call itself fails -- so another
user's trip data is never left visible on screen. The `mode`
("user"/"developer") `localStorage` preference is intentionally left
alone across logout/login, per the existing "not sensitive" rule for
that key.

## 41. Section 184F: Auth UX Polish and the Final Developer Mode Decision

Step 184E built a complete, working auth UI; Step 184F is a pure polish
pass over it plus one explicit, documented product decision -- no new
screens, no new endpoints, no security-model change, and (as expected)
zero backend files touched.

**Developer Mode permission decision -- finalized.** Developer Mode
stays visible to every logged-in user, with no role/permission tier of
its own. This was Step 184A's original recommendation, reaffirmed here
as the actual MVP decision rather than left as an open question: it is
a verbosity toggle over a trip the viewer already has real, backend-
enforced access to (`require_trip_owner`, Step 184D) -- switching it on
reveals more detail about *that same trip*, never another user's data,
never an admin/operational view. A short helper line now sits directly
under the `ModeToggle` buttons in the result header: "Developer view
shows diagnostics for your own trip. It does not change provider data
or generation behavior." Admin/dev-only role gating remains explicitly
deferred until (if ever) Developer Mode grows a feature that exposes
cross-user or operational data -- it does not today, so no such gate
was added. Developer Mode is still only reachable after `currentUser`
is set (the same auth gate as everything else on this page), and it was
never role-gated to begin with, so there was nothing to relax.

**Auth screen polish.** No new dependency. Fixes: the login heading now
reads "Log in to your account" (previously "Sign in", inconsistent with
the "Log in" tab label right below it); the login/signup toggle is a
labeled `role="group"` with `aria-pressed` on each button (matching the
existing `ModeToggle` convention) and is disabled while a request is in
flight so a submit can't race a mode switch; every input got a `name`
attribute (`email`/`password`/`confirm-password`) for better password-
manager/autofill behavior alongside the existing `autoComplete` values;
the signup password field gained `minLength={8}` (client-side echo of
the backend's own real minimum) plus a small "At least 8 characters"
hint; the password-mismatch check moved from a floating error box to an
inline message directly under the Confirm password field, wired via
`aria-describedby`/`aria-invalid` on that input and `role="alert"` on
the message itself, so a screen reader announces it at the point of the
actual problem; every other (server-side) auth error still renders in
the shared error box, now also `role="alert" aria-live="polite"`; the
submit button's in-flight label is "Logging in…"/"Creating account…".
Enter-key submit already worked (a single-line text input inside a
`<form>` with a submit button) and needed no change. Mobile padding on
all three top-level auth-adjacent screens (login/signup, the
`AUTH_NOT_CONFIGURED` setup screen, and the authenticated shell) was
tightened from a flat `p-8` to `p-6 sm:p-8` so narrow viewports get a
bit more content width without introducing overflow.

**Authenticated shell polish.** The "Signed in as" line is now visually
quieter (`text-xs` label, `text-sm` email) than the "My trips"/create-
trip sections below it -- clear without competing for attention -- and
the Log out button got a slightly stronger border so it stays easy to
find. The "My trips" panel now uses a subtle cyan-tinted border/
background (`border-cyan-300/15 bg-cyan-400/[0.03]`) instead of the
same neutral card style as the plain "signed in as" banner, so it reads
as its own distinct, interactive section rather than blending into
plain page furniture. Loading/error states inside it now live in an
`aria-live="polite"` region (`role="alert"` on the error line
specifically) so a status change is announced, not just visually
shown. Long destination/origin names wrap (`break-words`) instead of
overflowing narrow viewports. Clicking a specific trip row now shows a
per-row "Loading…" line (new `loadingTripId` state, cleared on success,
failure, and logout) instead of every row just going uniformly
`disabled` with no indication of *which* one was clicked. The manual
trip_id box was relabeled "Advanced: load a trip by ID" with an
explicit "power-user fallback for 'My trips' above" line -- it still
works exactly as before (still refused with a clean 403 for a trip_id
you don't own), just clearly marked as the fallback path it always was,
never the primary one.

**My Trips behavior -- confirmed, one label change.** Refresh-on-
signup, refresh-on-login, and refresh-on-create-trip were already wired
in Step 184E and needed no change. The manual refresh button's label
changed from bare "Refresh" to "Refresh trips" for clarity next to the
per-row "Loading…" text now also present. No caching change was needed:
`myTrips` was already reset to `[]` on logout and refetched fresh on
the next login/signup, so a second account signing in on the same
browser session already never saw the first account's list.

**401/403 handling -- confirmed working, no logic change needed.**
Re-verified live (a real second account manually pasting a first
account's `trip_id`) that `describeTripApiError()` from Step 184E still
renders the exact required copy ("You do not have access to this
trip." for `FORBIDDEN`; clearing `currentUser`/`myTrips`/`result` and
implicitly returning to the login screen for `AUTHENTICATION_REQUIRED`)
with zero leaked trip content and zero stack traces. No code change was
needed here -- 184E's implementation already met 184F's stated bar.

**Accessibility/mobile -- re-verified at 375px and 390px viewport
widths** (Playwright, both the login/signup screen and the
authenticated shell): no horizontal overflow, every form control has a
visible label, every interactive element (mode toggle, logout, My
Trips rows, tab buttons) is keyboard-reachable with the shared
`FOCUS_RING_CLASSNAME` focus ring, and the new `aria-live`/`role="alert"`
regions read correctly. No frontend test framework exists in this repo
(matches `CLAUDE.md`/`README.md`), so verification here is manual/
Playwright-driven, not a committed test suite -- consistent with every
prior frontend step.

## 42. Section 184G (final step of Section 184): Frontend Final Verification

Pure verification pass, zero frontend code changes. Re-ran the full
signup → authenticated shell → create trip → generate plan → Traveler
view → Developer view → feedback → regeneration-readiness flow live
(Playwright, real `SESSION_SECRET_KEY` in local `.env`) and confirmed
every 184E/184F behavior still holds exactly as documented in sections
40-41 above: no trip UI renders before `currentUser` is set; a created
trip appears in "My Trips" and reloads correctly; a cross-user manual
`trip_id` paste still returns the fixed "You do not have access to this
trip." message with zero leaked content; logout still clears every
piece of trip-specific state (`result`, `myTrips`, `existingTripId`,
feedback panel) from the DOM, not just from React state; a page reload
still restores the session via `GET /auth/me`; the `AUTH_NOT_CONFIGURED`
setup screen still renders with no secret value anywhere in its markup;
375px and 390px are both still overflow-free. Also confirmed at the API
level (through the authenticated browser session, not a raw
unauthenticated `curl`) that regeneration's pre-existing Section 174
rules are completely unaffected by the 184D auth layer: a trip with
pending feedback and zero locks still regenerates successfully
(`200`/`"applied"`), and adding an active lock afterward still produces
the same `409 REGENERATION_BLOCKED_BY_LOCKS` refusal as before any auth
code existed. No console errors beyond the expected pre-login `401` on
the initial `/auth/me` bootstrap check. This closes Section 184
(184A-184G) from the frontend side -- see `docs/CODEBASE_OVERVIEW.md`'s
Section 184G entry for the equivalent backend-side final review.