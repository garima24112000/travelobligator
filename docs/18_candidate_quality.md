# Candidate Quality Scoring

## 1. Purpose

Manual QA showed that after destination resolution was fixed (Step 155C),
`DestinationContext` candidates are geographically correct but still not
travel-intelligent: OpenStreetMap returns real POIs, but many of them are
weak itinerary anchors -- districts, minor memorials, schools, reservoirs,
administrative/local objects, and generic historic districts.

Step 156A adds a deterministic candidate quality layer
(`CandidateQualityService`) that scores and classifies candidates before
future scheduling/ranking work uses them. It sits between provider-backed
candidate collection (`DestinationContextService`) and any future
scheduling improvements.

---

## 2. Core Rule

This layer never calls an LLM, LangGraph, or LangSmith. It never calls a
new provider or external API. It never invents an attraction, restaurant,
or accommodation, and it never attaches a price, rating, opening hour,
route time, review count, booking link, or safety score.

A high `CandidateQualityTier` is a pre-ranking signal only -- never a claim
of final quality, availability, or bookability.

---

## 3. Models (`backend/app/models/candidate_quality.py`)

- `CandidateUseCase`: `attraction`, `restaurant`, `accommodation_poi`, `must_visit`.
- `CandidateQualityTier`: `primary_anchor`, `good_candidate`,
  `secondary_candidate`, `low_priority`, `rejected`.
- `CandidateRejectReason`: `missing_coordinates`, `weak_category`,
  `administrative_or_infrastructure`, `school_or_non_tourist_local_use`,
  `generic_historic_district`, `outside_user_interests`,
  `unsupported_accommodation_inventory`, `duplicate_or_near_duplicate`,
  `insufficient_provider_confidence`.
- `CandidateQualityScore`: one candidate's score, tier, reasons, and
  components. Validated so `rejected` always requires reject reasons, and
  any non-empty reject reasons always require `low_priority`/`rejected`.
- `CandidateQualityReport`: a destination-level rollup across
  `candidate_pois`/`candidate_restaurants`/`candidate_accommodation_pois`.

---

## 4. Service (`backend/app/services/candidate_quality_service.py`)

`CandidateQualityService` exposes:

- `score_attraction(place, user_interests=None, must_visit_names=None)`
- `score_restaurant(place, user_interests=None)`
- `score_accommodation_poi(place)`
- `build_report(planning_state)`

Each accepts a `NormalizedPlace` or the equivalent `dict` shape already
stored in `DestinationContext`. Scoring combines a keyword-based category
signal (checking narrower negative signals like "school"/"reservoir"/
"historic district"/administrative terms before broader positive signals
like "museum"/"park"/"tower", so a name that contains both isn't
mis-classified) with provider confidence and coordinate presence. A
must-visit name match overrides weak-category signals, since a traveler's
explicit request should not be silently demoted.

Tier thresholds (`total_score`, before severe-reason overrides):

```text
>= 0.75  primary_anchor
>= 0.55  good_candidate
>= 0.35  secondary_candidate
>= 0.20  low_priority
otherwise rejected
```

Severe reject reasons (`missing_coordinates`,
`insufficient_provider_confidence`, `unsupported_accommodation_inventory`)
force `rejected` regardless of score. Any other reject reason caps the
tier at `low_priority`.

`build_report` reads `planning_state.destination_context` only and never
mutates `PlanningState`.

---

## 5. Not Implemented Yet

- `duplicate_or_near_duplicate` is applied only within `build_report`
  (repeated candidate names across the same list), not across lists.
- `outside_user_interests` is defined but not yet used to reject a
  candidate outright; interests currently only boost scores.
- Scoring stays deterministic and rule-based -- no LLM, LangGraph, or
  LangSmith is used to compute or adjust a score.
- Scoring never creates a place, restaurant, or accommodation, and never
  attaches a price, rating, opening hour, route time, review count,
  booking link, or safety score. See section 7 for how
  `ExperiencePlannerService` consumes these scores as of Step 156C.

---

## 6. Persistence and API (Step 156B)

`PlanningState.candidate_quality_report` (`CandidateQualityReport | None`)
stores the most recently computed report. `PlanningOrchestrator` computes
it via `CandidateQualityService.build_report` immediately after
`DestinationContextService` runs (`run_destination_context_stage`), for
both the initial `generate_full_plan` flow and any future feedback-driven
rerun of the destination-context stage. It is `None` until a destination
context has been generated at least once, and reloads correctly from an
older persisted JSON record that predates this field (Pydantic's default
applies).

`GET /trips/{trip_id}/candidate-quality` (docs/11_api_contracts.md section
28) exposes it read-only: it never recomputes, mutates `PlanningState`, or
bumps `metadata.updated_at` itself.

Scores are metadata only in this step -- `experience_plan` scheduling,
`validation_report`, and regeneration behavior are all unchanged by
computing or exposing this report.

---

## 7. Consumed by Scheduling (Step 156C)

`ExperiencePlannerService` now reads `PlanningState.candidate_quality_report`
(read-only -- it is never mutated) as a deterministic pre-ranking signal
before scheduling attractions and suggesting nearby restaurants/
accommodation POIs:

- **Attraction scheduling.** Before the existing must-visit/interest/
  provider-order tiering runs, candidates are filtered/reordered by
  quality: `rejected` candidates (missing coordinates, insufficient
  provider confidence, etc.) are excluded from scheduling entirely.
  Remaining candidates are stable-sorted by quality tier/score (highest
  first, ties keep original order). See section 8 (Step 156E) for how
  `low_priority` candidates are handled -- they are excluded too, never
  used as filler.
- **Must-visit handling.** A grounded must-visit is never demoted to
  `low_priority` by `CandidateQualityService`'s own must-visit override
  (Step 156A), and the existing must-visit bucket still anchors the
  earliest possible day regardless of quality score -- so must-visit
  priority is preserved exactly as before. A missing/ungrounded must-visit
  is still never replaced with an unrelated attraction.
- **Restaurant and accommodation-POI suggestions**, including plan-level
  stay-area guidance, keep straight-line distance as the primary signal;
  when two candidates sit at a similar distance (within a conservative
  ~100m tolerance) the higher-quality one is preferred. This never treats a
  restaurant as a confirmed pick, never implies bookable accommodation
  inventory, and never adds a rating/price/reservation/opening-hour claim.
- **Backward compatibility.** If `candidate_quality_report` is `None` (or
  its score lists don't line up with the current candidate lists),
  scheduling and suggestion ordering fall back to the exact pre-156C
  behavior -- pure must-visit/interest/provider-order tiering and pure
  nearest-distance suggestions.
- **Validation is unchanged.** If quality-based filtering leaves too few
  (or zero) candidates to build a schedule, `PlanValidatorService`'s
  existing checks (which compare `destination_context.candidate_pois`
  against what was actually scheduled) already report that honestly as
  `blocked`/`needs_review` -- no validation logic was modified for this
  step.

Scores still never create a place, restaurant, or accommodation, and never
attach a price, rating, opening hour, route time, review count, booking
link, or safety score.

---

## 8. Trust-Over-Fullness Scheduling (Step 156E)

`itinerary-generator-build-spec.md` Stage 8 is explicit: if there aren't
enough `good_candidate`+ tier stops to fill a day, produce a lighter day
rather than padding it with `low_priority` filler. Step 156C's original
attraction-scheduling behavior did not fully match this -- it held
`low_priority` candidates back as a fallback, using them to fill a day
when there weren't enough `primary_anchor`/`good_candidate`/
`secondary_candidate` candidates available. Step 156E corrects this.

**Current behavior when `candidate_quality_report` is present:**

- `rejected` attractions are excluded from scheduling, as before.
- `low_priority` attractions are **also excluded from scheduling by
  default** -- they are never used, even as filler, to fill out a day.
  Only `primary_anchor`/`good_candidate`/`secondary_candidate` attractions
  are eligible.
- If there are not enough eligible attractions to fill every requested
  day/pace slot, the scheduler produces a **lighter day** rather than
  padding it with weak candidates. Days may legitimately end up with fewer
  scheduled experiences than the pace-based per-day cap would normally
  allow.
- When a day ends up lighter for this reason, it carries an honest,
  concise warning (e.g. "Some low-priority or rejected candidate
  attractions were not scheduled. This day may be lighter because not
  enough stronger provider-backed candidates were available.") -- never a
  silent gap.
- A grounded must-visit is unaffected by this exclusion: `CandidateQualityService`'s
  must-visit override (Step 156A) keeps a grounded must-visit out of
  `low_priority`/`rejected` unless a severe issue applies (missing
  coordinates, very low provider confidence). If a must-visit candidate is
  rejected for one of those severe reasons, it is not scheduled and no
  unrelated attraction is substituted for it -- `PlanValidatorService`
  surfaces this honestly (see section 9, Step 156G).
- Restaurant/accommodation-POI suggestions and stay-area guidance are
  unchanged by this step: they still exclude only `rejected` candidates and
  use quality only as a proximity tie-break, since a `low_priority`
  location candidate is still safe to surface as a nearby-only suggestion
  (it is never scheduled into the itinerary itself).
- If `candidate_quality_report` is `None` (or doesn't correspond to the
  current candidate list), scheduling falls back to the exact pre-156C
  behavior unchanged.

Validation is not directly modified by this step: `PlanValidatorService`
already compares candidate availability against what was actually
scheduled, so a lighter (or empty) schedule caused by this exclusion is
honestly reported as `needs_review`/`blocked` through existing checks. See
section 9 (Step 156G) for how a specific must-visit failure -- as opposed
to a day just being lighter overall -- is surfaced.

---

## 9. Must-Visit Grounding Failure Visibility (Step 156G)

`itinerary-generator-build-spec.md`'s destination-grounding stage is
explicit: *"Must-visit places get a different failure path than regular
candidates. If a user's explicit must-visit can't be grounded, don't drop
it silently -- flag it back to the user."* Trust-over-fullness (section 8)
extends this: a must-visit can be a real, grounded, provider-backed
candidate in `destination_context.candidate_pois` and still end up
excluded from scheduling for a severe candidate-quality reason (missing
coordinates, insufficient provider confidence). That case must be
surfaced honestly too, not treated as "found" just because it exists
somewhere in the candidate list.

**Current behavior:** `PlanValidatorService` compares
`trip_request.must_visit` (or `traveler_profile.must_visit` when present)
against the names of experiences actually present in
`experience_plan.daily_plans[*].experiences` -- not against
`destination_context.candidate_pois`. Matching is conservative
case-insensitive containment (the must-visit term must appear as a
substring of a scheduled experience's name), the same rule already used
elsewhere for must-visit prioritization.

If one or more must-visit entries have no matching scheduled experience,
a single `category="must_visit"` warning is added to
`validation_report.warnings` naming every unmatched entry, e.g.:

```text
The following must-visit place(s) were requested but were not
grounded/scheduled for this destination: Eiffel Tower, Louvre Museum.
They were not replaced with unrelated attractions.
```

This never blocks the plan by itself (it is always a `WARNING`, never a
`critical_issue`) and never marks the plan `ready` -- readiness stays
`needs_review` (or `blocked`, if nothing else could be scheduled either),
following the same existing validator style as every other warning. No
unrelated attraction is ever substituted for a failed must-visit, and no
coordinate/rating/price/opening-hour/route/availability/booking value is
ever invented to make a fuzzy match "work."
