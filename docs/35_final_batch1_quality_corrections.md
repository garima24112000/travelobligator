# 35. Final Batch 1 — generalizable quality corrections

Status: implemented on branch `quality/final-batch1-corrections` (from `main` at `71c215b`). **Not deployed.**
Verified offline (synthetic fixtures) and by one bounded DEVELOPMENT verification (four development cities, see
"Development verification" below). The frozen `quality-holdout-v2` result stays a **historical FAIL** (6/10 baseline
hard-correctness PASS); nothing here re-runs, re-scores or re-presents it, and no holdout itinerary was used to
shape a rule. Every rule is generic: no destination, landmark or neighbourhood name is in production code.

Source of the work: the generalized failure categories of the Final Batch 1 acceptance report (must-visit
resolution, remote single-stop days, time-of-day feasibility, same-site duplication, hours syntax, stale and
internal wording, unlocalized names).

## A. Must-visit reliability (P0)

| Root cause | Change |
|---|---|
| The named-place lookup took only the geocoder's FIRST result; a street or settlement ranking above the place meant "not found" | `GeoapifyGeocoder.lookup_named_place` reads up to 5 results of the same single request and takes the first that passes the **unchanged** specificity rule (result type, match type, confidence ≥ 0.5). Geocoding is charged per request. |
| Why a lookup failed was not recorded anywhere | `NamedPlaceLookup.rejection` / `ProviderResponse.failure_reason`: fixed labels `no_result`, `not_a_specific_place`, `weak_name_match`, `low_confidence`, `no_coordinates_or_identity`, `outside_destination`. Recorded per term in `DestinationContext.must_visit_lookups`. |
| A pool place recorded under its local name was not matched to the traveller's English name (its other names are excluded from serialisation) | The context stage passes each pool record's own other names (`name:en`, `int_name`, `official_name`, …) in memory to `exact_name_candidates`. Still **comparable-name equality**, unique in the pool, never containment; nothing extra is persisted. |
| A place just outside the city was silently "not found" | One wider lookup (`search_must_visit_excursion`: 60 km search circle, the destination's country instead of the city) only after a non-failure miss. Inside the destination → grounded as usual. Outside → grounded **only** when a driving route from the destination point is obtained through the existing alternate-mode allowance and takes ≤ `ROUTE_BURDEN_MAX_DRIVE_LEG_SECONDS`; the candidate is marked `outside_destination`. Otherwise recorded as `excursion_too_far` / `excursion_route_unverified` and not scheduled. Nothing is substituted. |
| A scheduled place could be reported missing | `must_visit_matching.resolve_must_visits` reconciles against the FINAL itinerary by identity: the grounded place, or a scheduled record of the same entity (same Wikidata reference within the existing 1 km merge rule). No name is compared. |
| Statuses were not distinguished | `services/must_visit_status`: `scheduled`, `scheduled_excursion`, `found_inaccessible`, `found_not_scheduled`, `outside_destination_not_feasible`, `outside_destination_unverified`, `unresolved` → `ValidationReport.must_visit_status`. The `MUST_VISIT` finding is rewritten in traveller wording per status; the remote-day finding of a planned excursion states the verified road time ("how you travel there is not arranged or checked"). |

## B. Remote and thin days (P0)

- The remote-day rebuild (Phase 3 §D) now applies to plans the reasoning model chose as well. This is a
  deliberate exception to Q3's "a model's plan keeps its places": a whole day beyond the pool's reach cannot be
  fixed one stop at a time. Must-visit days and days that alone cover a requested interest are kept.
- A rebuilt day holds `max(stops it had, MIN_STOPS_PER_DAY[pace])`, never above the pace cap, so a single
  isolated stop is not rebuilt as another single-stop day.
- New `fill_thin_days`: a day below `pace_targets.MIN_STOPS_PER_DAY` (now the one value also read by the
  validator's thin-day rule) is topped up with the most useful unused places within reach — within the spread
  boundary, at most one tier below the day's weakest stop, not closed that date, not a duplicate or same-site
  record. Nothing weaker is added (a lighter day beats filler). Only with viable ≥ T and no active lock.
- A requested excursion day (all stops are must-visits beyond reach) is not called thin.

## C. Time-of-day feasibility (P0)

`services/visit_schedule` runs on the final routed days in both engines (after the route-burden repair, before
the day explanations and the travel-time buffers) and in targeted regeneration (changed days only, no repair).

- **Timeline:** day window per pace (relaxed 10:00–17:30, balanced 09:30–18:00, packed 09:00–19:00 — a planning
  assumption: the day starts at its first stop); estimated visit length by kind of place (museum 120 min,
  architecture/religious 45, viewpoint 30, …); travel from **verified legs only**. After a leg with no verified
  route no later stop gets a time (`not_timed`). A late opening is waited for, never compressed. Local wall-clock
  times on the trip's own (destination) dates; no time zone is stored.
- **Evidence:** listed hours only from the place's record; unknown hours stay `hours_unknown` and are never
  called open.
- **Enforced before routing:** the known-closure pass now also treats hours that leave no visit of its estimated
  length inside the pace window as closed that date (`unvisitable_on`) — move / swap / replace / drop as before.
- **Enforced after routing (once per day, never under a lock):** an order-dependent conflict is repaired by
  re-ordering (orders compared with known legs and a straight-line estimate that only ORDERS them; the chosen
  order is routed through the existing day-route allowance and accepted only if every leg is verified, no
  conflict remains on verified times and route severity is no worse), else by removing the stop (never a
  must-visit, never below R, never a day's only stop; an interior stop only with a verified joining leg), else
  kept and disclosed.
- **Disclosed:** `visit_outside_listed_hours` and `day_exceeds_time_window` (both material). A long day is never
  trimmed: lengths are estimates.
- Stored fields: `ExperienceItem.start_time/end_time/estimated_duration_minutes/visit_time_status/
  arrival_wait_minutes`, `DailyPlan.visit_schedule`. All optional; older itineraries load unchanged.
- Rollback switch: `VISIT_TIME_SCHEDULING_ENABLED` (default true; off = no times, pre-routing rule as before).

## D. Same-site duplication (P1)

`services/same_site`: a heritage site that can house a collection (castle, fort, palace, manor, monastery,
place of worship — provider tags) and a museum/gallery record, **co-located** within the existing
`COLLOCATED_METERS` (50 m), form one site group (`same_site_group`, written by the context stage). The planner
treats it like its sub-feature collapse: the deterministic selection takes one; a model's plan keeps the more
useful record (a must-visit first), refills the slot only within one tier, and never empties a day. Two museums
in one building, two sites side by side, or a site and a museum down the street stay separate. No name is read.

## E. Hours syntax and presentation (P1)

- Hours reader: `Mo[1]`, `Su[-1]`, `Sa[1,3]`, `Th[2-4]` (n-th weekday of the month) and the syntax's `:` after
  a month selector. A day offset (`Su[-1] -1 day`) stays unsupported.
- Stale statements removed: the holiday finding no longer says no hours are checked; the planner's day note,
  plan assumptions and the no-route-data finding describe what is actually applied.
- Internal planning notes: a model rationale describing how the plan was assembled, or naming a requested
  interest nothing on the day serves, is not attached (`day_rationale`); Traveler view also filters planner
  wording from empty-day notes.
- Names: `NormalizedPlace.english_name` / `ExperienceItem.display_name` — the record's own `name:en` when it
  differs; Traveler view shows "English (original)". `name` stays the identity. (Geoapify adapter; the
  development Overpass adapter does not fill it.)
- Frontend: per-stop estimated time row, day assumption note, "About the places you asked for" notice.

## Enforced vs. disclosed

| Enforced (the plan changes) | Disclosed only |
|---|---|
| Lookup reads further results; alias match; reconciliation by identity | Why a must-visit is not on the plan; out-of-city road time |
| Out-of-city must-visit planned only with a verified feasible route | Excursion travel itself (mode, service) |
| Remote day without a reason rebuilt (any plan); thin days topped up | Remote must-visit days |
| Hours leaving no visit in the day window → moved/replaced/dropped | Unknown hours |
| Order-dependent hours conflict → reorder (verified) / removal | A conflict that could not be repaired; a day past its window |
| One record per heritage site + housed collection | Same-site records both kept (must-visits / only stop) |

## Remaining limitations

- Visit lengths and day windows are generic estimates; no per-venue duration, queue or ticket evidence.
- No accommodation, so a day's start and the journey to its first stop are not modelled.
- Hours coverage is limited by the records (the acceptance run had 63% unchecked); no second hours source.
- Comments on open rules, sunrise/sunset, school holidays, years/weeks remain unsupported.
- The excursion route is from the destination point (not a hotel); only one wider lookup per term.
- No IANA time zone is stored; times are destination wall-clock.
- Same-site detection needs both role tags and co-location; a museum mapped without tags, or farther than 50 m
  from its site, is not caught. No visitor-significance signal exists for filler beyond the existing guards.
- Whether more geocoder results and the alias match recover real must-visits is untested live.

## Follow-up: an outlying must-visit paired with a central optional stop

Found by the development verification (F2, below). Day composition offered replacement candidates only near
the day's OTHER stops (`NEAR_DAY_STOPS_KM`, 3 km). A must-visit on the edge of the area, with no unused place
within 3 km of it, paired with an optional stop in the centre stayed dispersed (11.4 km), although unused places
within the validator's 8 km boundary of it existed -- the classifier correctly called it
`discretionary_dispersion`, and the canary's readiness gate correctly failed it. Fix (candidate generation
only, `day_composition.proposals`): a DISPERSED day may also be repaired by an unused place that keeps the day
within the existing spread boundary -- the pre-Q3 spread pass's own candidates. The judge, the guards, the entry
rules, the must-visit and lock protection and the evaluation budget are unchanged; a dispersion nothing can
repair is still reported (`test_fb1_dispersed_outlier_repair.py`). Composition's existing objective (largest
material extent gain, one-tier guard) chose the replacement; it may prefer a nearer, lower-tier place to a
farther, more significant one.

## Development verification (2026-10-25, development cities only)

`backend/scripts/verify_fb1.py` (the existing request guard; app cap 100 Geoapify credits per generation;
8 combined LLM requests per generation; Kiwi 0; refuses production and every benchmark-file city, including the
tuning sets). Trip start Monday 2026-11-16. Each run once, never retried. No frozen holdout was touched.

| Scenario | Exercises | Result |
|---|---|---|
| F1 Budapest, 2 d balanced, 2 must-visits | identity / other names, reconciliation, same site, English names | all checks pass; canary PASS (review codes: `HOLIDAYS` only) |
| F2 Edinburgh, 2 d relaxed, 2 must-visits | outlying must-visit, same site | first run: all script checks pass, canary FAIL (`GEOGRAPHIC_DISPERSION` -- a genuine, avoidable dispersed day, see above) |
| F3 Gothenburg, 3 d relaxed | remote / thin days, ferry honesty | all checks pass; canary PASS |
| F4 Vienna, 2 d packed | visit times, closing days, long days | all checks pass; canary PASS |
| F2 again, after the fix (`--scenario F2`, same dates) | readiness gate still enforced | canary PASS; 2/2 must-visits scheduled; day 2 Rosslyn Chapel + a park 5.5 km away by a verified 6-minute drive (no dispersion); 2/2 legs verified, no ferry; 3 stops within listed hours, 1 hours unknown; no hours or venue conflict |

Requests: four-scenario cycle 168 Geoapify credits / 16 LLM requests (Groq 4 × 429, absorbed by Gemini); F2
re-run 40 credits / 4 LLM requests. No request refused; no 401/403; no credential in either report.

Limitations of this evidence: four development cities and one start week; the AI model's day choices differ
between runs, so a scenario's plan is not reproducible; F2 was re-run once after a code change (a focused
verification of that change, not a retry of an unchanged plan). Not exercised live, so covered by synthetic tests
only: the out-of-city excursion path (both F2 must-visits lay inside the destination's bounding box), same-site
grouping (no scheduled record of any run was in a site group), and the remote-day rebuild / thin-day fill (no
run produced a remote or thin day). Exercised live: must-visit lookup and reconciliation (4/4 scheduled across
F1/F2), visit times (fits listed hours 12 / hours unknown 7 stops in F1, F3, F4; no conflict), English display
names (3 stops in F3/F4), the dispersion repair (F2 re-run). The original independent quality acceptance (`quality-holdout-v2`, 6/10)
remains a historical FAIL; this evidence does not re-score or replace it, and any acceptance claim needs a new
unseen set and human review.

## Bounded development-only verification plan (executed above)

Development / tuning destinations only (never a `quality_v2` city; the canary refuses them), each run once,
through the existing guard, after explicit approval:

1. `verify_m2_phase3.py --scenarios 1 --execute` (development city) — ≤ 100 Geoapify credits, ≤ 8 LLM requests.
2. Two tuning canaries with exonym must-visits and a museum-heavy packed day (e.g. Q-T4, Q-T2) via
   `canary_city.py` — ≤ 100 credits each.
3. One tuning canary whose destination boundary includes outlying areas (e.g. Q-T3).

Check: must-visit statuses and lookup labels; rebuilt/topped-up days; visit times, conflicts and repairs; route
requests stay within allowances; zero stated closures/closing-day conflicts for non-must-visits. Total ≤ 400
Geoapify credits and ≤ 32 LLM requests. A new unseen holdout is required for any acceptance claim.
