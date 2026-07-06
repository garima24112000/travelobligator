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
Safe area
Unsafe area
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
- The user should always know what the system knows, what it assumes, and what it could not verify.