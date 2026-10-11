# 24. Itinerary Quality Contract

This document defines what a *good* TravelObligator itinerary is, beyond a factually safe one, and how that
is evaluated. It opens the final V1 engineering phase, **Itinerary Quality & Day Composition**.

Phase Q0 (this document, the benchmark data and the reporting harness) changes no planner behaviour.
Production changes start in Q1 and are judged against this contract.

## 1. Why this phase exists

The V1 candidate passed its infrastructure and factual-safety acceptance
([`23_v1_release_reference.md`](23_v1_release_reference.md)). A comparison on a real request then showed a
plan that was grounded and safe but weak as a plan a person would follow. The request was a four-day,
balanced trip for two with the interests *museums* and *piers* and three must-visits. What was observed:

- A place whose name merely contains a must-visit term was treated as a second must-visit and shared a day
  with the place the user actually asked for.
- A walking leg of roughly 114 minutes survived into the final plan as a warning.
- The interest *piers* was not represented in the plan.
- Niche places took scarce itinerary slots while stronger grounded candidates were unused.
- Days read as collections of individually valid places, not as coherent days.

The earlier benchmark measures grounding, fabrication, duplicates, minimum stop count, routing coverage,
persistence and provider budgets. It does not measure tourist usefulness, day composition, geographic
coherence, redundant allocation, or severe travel burden when a better alternative exists. This contract
adds those.

A general-purpose assistant's answer to the same request was more human-shaped. It is **not** the ground
truth and is not copied; it only showed that the gap is visible to a user.

## 2. Generalization principle

That request is the first observed example, not the definition of the problem. The working hypothesis is:

> The planner may produce factually safe but poorly composed itineraries in any destination, because
> candidate desirability, day composition, geographic grouping, redundancy and route burden are not yet
> optimized together strongly enough.

Rules for every metric and every later fix:

- Production logic may use only generic signals: provider-grounded identity, provider taxonomy and
  locality data, coordinates, verified routing, user interests and constraints, candidate quality evidence,
  grounded semantic-ranking evidence, and generic clustering, diversity, redundancy, pace and route-burden
  rules.
- No city, neighbourhood or landmark name, no city-specific threshold, no preferred-attraction list and no
  template itinerary may enter `backend/app`. A test enforces this for every name in the quality benchmark.
- Every proposed rule must still make sense for a compact historic city, a sprawling megacity, a small
  northern town and a coastal city alike. If it does not, it is rejected.
- The frozen quality holdout (section 7) is the main protection against fitting this phase to one city.

## 3. Structure of the contract

The contract has three parts. **They are never collapsed into one PASS.** A result always reports them
separately.

| Part | Nature | Verdict |
| --- | --- | --- |
| A. Hard correctness / safety | Deterministic, binary | PASS or FAIL per run |
| B. Itinerary quality | Deterministic figures from stored state | Reported per run; compared before and after a change |
| C. Manual human quality | Subjective 1–5 rubric | Scored by a person; release target in section 6 |

Part A always wins. A plan with a hard failure is a failed plan whatever parts B and C say.

## 4. Part A — hard correctness / safety

### 4.1 Existing requirements (unchanged and authoritative)

- The destination resolves correctly.
- Zero fabricated scheduled identities; only provider-grounded places are scheduled.
- Zero unsupported factual claims.
- Zero duplicate scheduled provider identities.
- Persistence and reload succeed.
- The provider credit cap is respected.
- When viable inventory ≥ R: at least R meaningful stops and no empty day.
- Routing coverage ≥ the existing release threshold (90%).
- The no-fabrication and provider-truth invariants of
  [`23_v1_release_reference.md`](23_v1_release_reference.md) section 7 remain authoritative. Nothing in
  this phase may weaken them, and a quality gain never justifies an invented fact.

`T`, `R` and `H` are the existing pace targets (`services/pace_targets.py`).

### 4.2 New generic correctness requirements

Q0 stated these and measured what it could. A1 and A2 were implemented in Q1; A3 is still open.

**A1. Must-visit identity.** A user's must-visit term resolves through provider identity. A related entity
does not inherit must-visit protection merely because its name contains the requested term. One term
protects the place it was grounded to; a differently identified place that shares words with it is an
ordinary candidate. *Before Q1:* any pool place whose provider name contained the term was grounded for
it, which is how the related place in section 1 became protected. *Implemented in Q1:* the
destination-context stage is the only stage that establishes the identity. It records the user's term on
the provider candidate (`must_visit_terms`), and the planner, candidate quality, validation and both
repairs read that record through `services/must_visit_matching.py`; none compares a place name with a
term. An exact provider name is only a grounding-time shortcut, and only when exactly one pool candidate
has it; otherwise the provider's targeted lookup decides, and an ungroundable term stays ungrounded.
Several terms may ground to one provider place, which is then scheduled and protected once.

**A2. Requested-interest normalization.** Common lexical variants map consistently into the supported
canonical interests. The waterfront family must be supported explicitly: `pier`, `piers`, `waterfront`,
`riverfront`, `harbor`, `harbour`, `promenade`. The canonical representation follows the existing taxonomy
conventions (`services/place_taxonomy.py`: a canonical interest key backed by provider-derived
categories); no city-specific interest is created. The requirement is that a request for the family is
tracked and can only be satisfied by a place the provider describes as waterfront. *Before Q1:*
`waterfront` mapped to `outdoors`, and the other six terms mapped to nothing, so they were not tracked and
could not even be reported as uncovered. *Implemented in Q1:* the seven words map to one canonical
interest, `waterfront`, backed only by the provider-derived `WATERFRONT` category. A waterfront place
still serves `outdoors`; a park, garden or viewpoint does not serve `waterfront`.

**A3. Severe route burden.** When viable ≥ T, or there is clearly sufficient unused verified inventory, a
severe route-burden finding must lead to recomposition or replacement before finalization. It may not
survive only as a warning when a valid lower-burden alternative exists. If no valid replacement exists,
the warning is honest and acceptable.

"Severe" is **not** a new threshold. It is the existing long-travel condition of
`services/route_burden.py` (`DayRouteBurden.long_route`, reported as `LONG_TRAVEL_DAY`), which uses the
existing `ROUTE_BURDEN_*` settings: one walking leg beyond the walking-leg limit, a day's walking beyond
the pace's walking limit, or unreasonable transfers. This contract introduces no second set of route
limits.

## 5. Part B — itinerary quality

These are quality figures, not travel facts. None is a popularity, a rating, a ranking, an opening hour or
a route the provider did not return. Where a figure uses the pipeline's quality tier, it is the internal
pre-ranking tier of the candidate quality stage and is labelled as such.

### 5.1 Dimensions

**B1. Interest coverage.** Every canonical requested interest is represented when sufficient verified
supply exists. The evaluation separates *supply unavailable* (no viable candidate serves the interest)
from *the planner did not use available supply*.

**B2. Must-visit coverage.** Every grounded must-visit is scheduled unless that is impossible under an
explicit constraint. One user term does not consume several itinerary slots through related-name matches,
unless those are distinct experiences the user asked for.

**B3. Geographic coherence.** Each final day is measured with the existing implementations only:
`day_order_heuristics.day_spread_km` and its boundary `GEOGRAPHIC_SPREAD_THRESHOLD_KM`, the validator's
`geographic_spread` finding, `route_burden.day_route_burdens`, `LONG_TRAVEL_DAY`, and the stored route
feasibility legs. No second definition of spread exists. A good plan minimizes avoidable cross-city
zig-zagging, very long single legs, repeated crossing between distant clusters, and days whose stops are
individually valid but geographically incoherent.

**B4. Day composition.** A *coherent day* generally has:

- reasonable geographic compactness;
- compatible experiences;
- useful diversity without random scattering;
- no unnecessary duplication of the same excursion or complex;
- a recognizable purpose, such as an area or corridor, or a compatible set of nearby experiences.

A day need not be one category: a park and a museum can be an excellent day. A model's prose theme for a
day is never the sole evidence that the day is coherent.

**B5. Limited-slot usefulness.** A scheduled candidate being real and quality-eligible is not enough. When
many viable candidates compete for few slots, the plan should prefer the more useful grounded ones. Later
steps may rank already-grounded candidates semantically; Q0 implements no ranking. The evaluation looks
at the capture of strongly grounded semantic anchors, the share of avoidable obscure filler, the use of
higher-value eligible alternatives, and redundant sub-feature allocation.

**B6. Redundancy.** The plan does not spend scarce slots on several closely related sub-entities when one
excursion represents the experience, unless the user asked for both, or they are independently meaningful
and chosen deliberately. Examples live in tests and benchmark notes only.

**B7. Pace fit.** Relaxed, balanced and packed are respected by route burden as well as by stop count.
Three stops with two hours of transfer is not automatically a good balanced day.

**B8. First and last day.** This is a known information limitation. The request carries no arrival or
departure time, and none is invented. A later product step may collect an arrival window, a departure
window, and whether the first and last dates are full sightseeing days. Q0 does not change the form.

### 5.2 Metrics measurable now

These are computed by `backend/scripts/quality_metrics.py` from one stored `PlanningState`, with no
provider, routing or model call, and are added to the canary report under `itinerary_quality`. They are
reported only; the canary's acceptance does not read them.

| Dimension | Metric | Source in stored state |
| --- | --- | --- |
| B1, A2 | Requested terms, canonical interests, terms the taxonomy does not recognise | `place_taxonomy.canonical_interests` |
| B1 | Covered and uncovered interests | `interest_coverage.interest_coverage` |
| B1 | Viable candidates per interest; uncovered with supply vs. without supply | `candidate_quality_report` (`matched_interests`, viable tiers of the usefulness contract) |
| B2, A1 | Grounded must-visits scheduled and unscheduled; ungrounded terms | `must_visit_matching.resolve_must_visits` |
| B2, A1, B6 | Scheduled places per must-visit term; terms holding more than one slot | grounded place ids ∩ scheduled provider ids |
| B3 | Per-day spread, maximum, median, days over the boundary | `day_spread_km`, `GEOGRAPHIC_SPREAD_THRESHOLD_KM` |
| B3 | Count of `geographic_spread` findings | `validation_report` |
| B3 | Stops far from their own day and much closer to another day | `day_order_heuristics.misplaced_stops` |
| B3, B7, A3 | Per-day transfer and walking time, longest leg, longest walking leg | `route_burden.day_route_burdens` |
| A3 | Long-travel days; long-travel days that survived with viable ≥ T | burdens, `usefulness_contract.evaluate_usefulness` |
| A3 | Route-repair attempts with their fixed reason codes | `route_burden_repair_report` |
| B5 | Scheduled stops by internal tier; unused viable candidates above the lowest scheduled tier | `ExperienceItem.quality_tier`, `candidate_quality_report` |
| B5 | Grounded anchors scheduled, count and rate | `grounded_anchors.grounded_anchor_place_ids` |
| B5 | Scheduled and unused viable candidates by usefulness evidence band; share of scheduled stops with no stored evidence | `candidate_usefulness.usefulness_by_place_id` (Q2) |
| B5 | Candidates with two or more evidences, available vs. scheduled | same |
| B5 | Unused candidates with more evidence than the weakest *discretionary* scheduled stop | same; a grounded must-visit and the only stop serving a requested interest are not discretionary |
| B5 | Eligible candidates outside the bounded reasoning request, by band; must-visits beyond the bound | `AIItineraryReasoningRequestBuilder`, recomputed with the current cap |
| B4 | Per-day coarse class counts and concentration kind | `schedule_diversity` |
| B3, B4 | Per-day extent and geographic band (compact, extended, dispersed, unmeasured); days per band | `day_order_heuristics.day_extent_km`, `day_composition.geographic_band` (Q3) |
| B3, B4 | Distinct computed areas per day; days within one area; area revisits in stop order; areas split across mixed days | `day_composition.build_areas` over the stored pool (Q3) |
| B3, B5 | Extended or dispersed days for which an unused candidate of equal usefulness would have kept the day compact | same, with `candidate_usefulness` |
| B5 | Grounded anchors left unscheduled; grounded must-visit places beyond the pace capacity | `grounded_anchors`, `must_visit_matching`, `pace_targets` |
| A3, B7 | Route severity per day from verified provider legs; unverified legs; definitive provider refusals; legs whose route includes a ferry | `route_burden.assess_days` (Q4) |
| A3 | Route recomposition attempts: operation, severity before and after, candidates shortlisted / verified / skipped for budget, routing requests used of the cap | `route_burden_repair_report` (Q4) |
| B3 | Largest provider route distance over the straight line between the same stops (a ratio, not a duration) | `route_feasibility_report`, `haversine_distance_km` |
| B7 | Stops per day against the pace target; days over the pace walking limit | `pace_targets`, burdens |
| Part A | Routing coverage, meaningful stops, empty days, duplicates, unresolved collisions | existing canary figures |

Straight-line spread is a proxy for a hard-to-travel day, exactly as the validator treats it. It is never
shown as a route distance.

A usefulness evidence band (Q2) is a count, 0 to 3, of stored planning evidence on a candidate: grounded
semantic anchor, strong provider significance, requested-interest fit. Band 0 means that no such evidence
is stored, because the provider metadata is thin or the place was not proposed as an anchor. It does not
mean obscure, filler or low value, and no band is a popularity, a rating or a ranking. The "more evidence
than the weakest discretionary stop" figure is an observation about evidence ordering, not an error
count: class caps, day fit and geography legitimately choose a candidate with less stored evidence.

### 5.3 Future metrics (not computed, not estimated)

These need the composition and ranking work of later steps. The report names them under
`future_metrics_not_computed` so that no figure implies they were measured.

| Metric | What is missing today |
| --- | --- |
| Same-excursion or same-complex redundancy | A grounded parent or complex identity. The must-visit multi-slot count and the collision records are only partial proxies. |
| Tourist-value ranking and obscure-filler share | Provider-backed evidence of how much a place matters to visitors. Neither the internal tier nor the Q2 evidence band is that: a stop without stored evidence is not thereby obscure filler. |
| Recognizable day purpose | Provider locality or area data attached to the scheduled stops. |
| A lower-burden alternative existed | A counterfactual. Today only the repair's reason code hints at it. |
| First and last day fit | Arrival and departure information (B8). |

## 6. Part C — manual human quality

Not every useful property of an itinerary reduces to a deterministic metric. A person scores each plan
from 1 to 5 on:

| Dimension | Question |
| --- | --- |
| Attraction usefulness | Are these places a visitor would be glad to have spent a slot on? |
| Geographic and day coherence | Does each day hold together on a map and as an experience? |
| Personalization | Does the plan reflect the stated interests and must-visits? |
| Pace realism | Could the day be done at the stated pace, travel included? |
| Redundancy avoidance | Is every slot a distinct experience? |
| Overall | Would I actually use this plan? |

Scale: 1 unusable, 2 poor, 3 acceptable with edits, 4 good, 5 excellent.

Rules:

- This is subjective evaluation. It supplements the deterministic metrics and never replaces them.
- It never overrides a factual-safety failure. A plan that fails part A is failed, whatever its scores.
- A plan is scored before the scorer reads its part B figures, so the numbers do not anchor the judgment.
- Scores are recorded once and are not revised after later changes.
- The scorer may know the destination. No reference itinerary is used as an answer key.

**Release target, on the final unseen quality holdout:**

- median overall score ≥ 4 of 5;
- no scenario with an overall score below 3;
- zero hard factual-safety failures (part A).

The runner writes an empty rubric for each scenario. It never fills one in.

## 7. Benchmark data

The quality scenarios live in `backend/scripts/benchmark/quality_v1.json`. That file is benchmark-only: it
is never imported by `backend/app`, and no name in it may enter production logic. A scenario states a
request and the generic dimensions it exercises. It never states an expected itinerary, a day grouping, a
neighbourhood or a stop.

The historical benchmark (`backend/scripts/benchmark/cities.json`: 18 tuning cities, 10 holdout cities,
8 stress scenarios) is not edited, and its results stand as recorded. The original holdout is not rerun
and is not presented as unseen again.

### 7.1 Quality tuning scenarios

These may be run as often as needed while improving the planner. They use only cities of the existing
tuning set, so they consume nothing unseen.

| ID | Destination | Days, pace | Interests | Must-visits | Exercises |
| --- | --- | --- | --- | --- | --- |
| Q-T1 | New York, United States | 4, balanced | museums, piers | Statue of Liberty; Central Park; Empire State Building | Waterfront normalization, must-visit identity, day grouping, severe route burden, limited-slot quality |
| Q-T2 | Kraków, Poland | 3, balanced | history, architecture | Wawel Castle | Dense historic core |
| Q-T3 | Cape Town, South Africa | 4, relaxed | waterfront, outdoors, food | Table Mountain | Coastal grouping, topographic separation, pace fit |
| Q-T4 | Paris, France | 3, packed | museums, art | Louvre Museum; Musée d'Orsay | Museums and art, multiple must-visits, redundancy |
| Q-T5 | Mexico City, Mexico | 4, balanced | food, markets, history | none | Food and markets, sprawling city |
| Q-T6 | Melbourne, Australia | 3, balanced | parks, art, food | Royal Botanic Gardens | Parks and outdoors, mixed interests |

Q-T1 is a development regression for the request in section 1. It is not a template and has no expected
day-by-day answer.

### 7.2 Frozen unseen quality holdout

Frozen on 2026-10-08. None of these cities is in the earlier tuning or holdout sets.

| ID | Destination | Days, pace | Interests | Must-visits | Exercises |
| --- | --- | --- | --- | --- | --- |
| Q-H1 | Chicago, United States | 3, balanced | architecture, museums, riverfront | Art Institute of Chicago; Millennium Park | River corridor, architecture, multiple must-visits |
| Q-H2 | Barcelona, Spain | 4, balanced | architecture, food, markets | Sagrada Família; Park Güell | Architecture, markets and food, geographic separation |
| Q-H3 | Rome, Italy | 3, packed | history, art | Colosseum; Pantheon | Compact historic core, must-visit identity |
| Q-H4 | Singapore | 3, balanced | gardens, food, waterfront | Gardens by the Bay | Dense transit context, waterfront |
| Q-H5 | Bangkok, Thailand | 4, balanced | history, markets, food | Grand Palace | Sprawling city, river corridor |
| Q-H6 | Sydney, Australia | 3, relaxed | harbour, outdoors, museums | Sydney Opera House | Harbour, outdoors |
| Q-H7 | Reykjavík, Iceland | 2, relaxed | architecture, museums, harbor | none | Compact core, thin inventory |
| Q-H8 | Rio de Janeiro, Brazil | 4, balanced | outdoors, history, food | Christ the Redeemer; Sugarloaf Mountain | Topographic separation, multiple must-visits |
| Q-H9 | Prague, Czechia | 3, balanced | history, architecture, art | Charles Bridge; Prague Castle | Compact historic core, river corridor |
| Q-H10 | Mumbai, India | 3, packed | history, markets, food, promenade | Gateway of India | Sprawling dense city, waterfront |

The holdout's interest terms are either already canonical or belong to the waterfront family of A2, so
the holdout tests the contract and not vocabulary the contract never promised.

### 7.3 Holdout rules

- The holdout is not run, in whole or in part, while tuning. This includes single-city canaries on a
  holdout city.
- It is run once, after the tuning freeze, and its result is recorded as it comes out.
- If it exposes a defect, the fix is validated on tuning scenarios or a separate canary. The holdout is
  not rerun and re-presented as unseen.
- The scenario list is frozen. A test pins its hash; a change must be explained here.

Guards in the tooling:

- `benchmark_quality.py` defaults to `--set quality-tuning`. `--set quality-holdout` exits without running
  unless `--allow-quality-holdout` is given. There is no combined set.
- The holdout is only run whole: `--scenarios`, `--start` and `--limit` are refused for it. `--resume`
  continues an interrupted run.
- `canary_city.py` refuses a city of the quality holdout without `--allow-quality-holdout`.
- The earlier runner, `benchmark_cities.py`, is unchanged and cannot reach the quality sets.

## 8. Running the quality benchmark

Live runs need provider keys and are done by hand. Nothing in Q0 ran one.

```bash
cd backend
python scripts/benchmark_quality.py --start-date YYYY-MM-DD --out ../benchmark_results/quality_tuning_run1
python scripts/benchmark_quality.py --start-date YYYY-MM-DD --out ../benchmark_results/quality_tuning_run1 --scenarios Q-T1
```

Each scenario writes its canary report, its `itinerary_quality` figures, its `baseline_hard_correctness`
outcome and an empty manual rubric. `summary.json` lists counts per part and gives no combined score.
Results go to `benchmark_results/`, which is git-ignored.

Every scenario record also carries its provenance: the effective planner configuration (feature flags,
budgets, limits, read from the application's own settings) and the source identity (git HEAD, dirty
status, and a fingerprint of the working tree's relevant files, committed or not). A run whose
provenance cannot be captured does not start. The summary lists generation failures, baseline failures
and quality-metric extraction failures apart; only a scenario whose figures were extracted is
`quality_measured`, and a directory that mixes configurations or sources is `run_valid: false`. A second
arm is the same command with other settings in the environment and its own `--out` directory; two arms
are never combined into one score.

**`baseline_hard_correctness` is not the whole of part A.** It is the pre-Q1 canary acceptance baseline:
the existing factual-safety and infrastructure checks of section 4.1. A1–A3 (section 4.2) are not
acceptance checks during Q0. They are reported by `itinerary_quality` and become enforceable after their
Q1 implementation. So a Q0 run can show `baseline_hard_correctness.passed = true` while its
`itinerary_quality` figures still show an unrecognised waterfront term or one must-visit term holding
several slots. The summary fields are named `baseline_hard_correctness_passed` and
`baseline_hard_correctness_failed` for the same reason.

## 9. Scope of Q0

Q0 added this contract, the benchmark data, the runner, the reported-only metrics and their tests. It did
not change planner behaviour, candidate scoring, the interest taxonomy, must-visit matching, prompts,
routing, repair, the frontend, configuration, deployment or CI.

## 10. Scope of Q1

Q1 implemented A1 and A2 (section 4.2) as generic production fixes, and nothing else. It did not change
candidate scoring weights, ranking, AI anchor handling, clustering, day composition, route repair logic,
prompts, providers, configuration or the frontend, and it ran no benchmark. A3 and the quality dimensions
of part B are the subject of the later steps. The quality metrics of section 5.2 were not edited; their
values change only because the production semantics did.

## 11. Scope of Q2

Q2 addresses B5 for candidates that are already eligible: which of them deserve a limited slot. It added
no model call and no provider call.

- **One ordering.** `app/services/candidate_usefulness.py` owns the only usefulness ordering. Its
  preference is lexicographic: grounded must-visit, evidence band, semantic anchor, provider
  significance, quality tier; then the quality score and a neutral name / provider-id tie-break that
  carries no tourism meaning and exists so that provider result order never decides anything.
- **Evidence.** A grounded, promoted semantic anchor (by provider place id; when interests were requested
  it must serve one), place-level provider significance (a Wikipedia article or a heritage designation
  the provider carries for that place; never a bare Wikidata id), and provider-derived fit with a
  requested interest. The kind of place is not evidence: a major historic type stays a signal of the
  quality stage, which already rewards it in the score, and does not raise the band. Counting it let
  every place of such a kind outrank equally suitable places by type alone. Evidence counts only for a
  primary or good tier candidate with no reject reason that is not a single object or a commercial
  gallery. The model's own confidence and priority are not inputs.
- **Eligibility is untouched.** Tiers, scores, reject reasons, caps and the corroborated-anchor boost are
  what they were. Usefulness orders the eligible set and never changes it.
- **Bounded reasoning request.** The cap is hard. In order: every grounded must-visit; one attraction per
  requested interest; counting anchors; then, from the space left, one restaurant per trip day at most;
  a second attraction per interest; the remaining attractions. If the grounded must-visits alone exceed
  the cap, no request is sent (`MUST_VISIT_EXCEEDS_REASONING_BOUND`) and the deterministic planner, which
  has no candidate cap, plans the trip.
- **Deterministic selection.** The same ordering drives the fill, the empty-day fill, the top-up and the
  bounded exchange passes. Class caps, the diluted share and the coverage step stay filters around it;
  repeat avoidance only refines the choice among candidates of equal preference.
- **One candidate universe.** `app/services/candidate_universe.py` decides which broad-pool candidates
  are schedulable and which promoted candidates join them. The planner and the reasoning request both
  read it, so every candidate id the model may cite resolves in the planner. A promoted candidate is left
  out only when it is a place already held: the same provider place id as a broad candidate, or the same
  Wikidata entity or the same comparable name as a schedulable candidate that is also close by. A shared
  name alone conflates nothing, a rejected broad record suppresses nothing by name or entity, and neither
  a must-visit nor an anchor identity is transferred to a namesake.
- **Not in Q2.** Geographic grouping, day composition and route-aware recomposition (Q3, Q4). The only
  geography Q2 reads is the existing pool-reach rule of the outlier control, with its thresholds
  unchanged. At selection time a candidate located beyond it, anchor or not, takes a slot only when
  nothing nearer is left; a grounded must-visit is never deferred.
- **Known limitations.** A single object never carries an evidence band, because a stored quality score
  does not record the object's kind; a documented artwork on an art-focused trip therefore ranks without
  evidence, while galleries keep their art exception. The figure for eligible candidates outside the
  reasoning bound is recomputed with the current cap, since the request itself is not stored.

## 12. Scope of Q3

Q3 addresses B3 and B4: individually useful places do not automatically form a coherent day. It added no
model call, no provider call and no routing call, and it changed neither eligibility nor the Q2 ordering.

- **One module.** `app/services/day_composition.py` owns the composition objective. The deterministic
  planner and a plan the reasoning model chose are both improved against it, and the bounded reasoning
  request reads its areas. It is pure and names no destination.
- **Every distance is a straight-line proxy.** It is a great-circle distance between provider
  coordinates: never a route length, a walking distance or a travel duration, and never shown, stored or
  validated. A day Q3 calls compact can still be hard to travel. Provider-route burden, and any
  recomposition driven by it, is Q4.
- **Areas.** Located candidates are grouped by complete linkage with a largest pairwise distance of
  `NEAR_DAY_STOPS_KM` (3 km), the planner's existing "near the day's other stops" proxy. It is not a
  neighbourhood definition and is not derived from the validator's spread warning. Complete linkage does
  not chain along a corridor. Areas are opaque ids for one request. The objective reads distances, never
  an area id, so an area edge is not a cliff. No locality name is stored or invented.
- **Bands.** A day's *extent* is the largest straight-line distance between two of its located stops.
  *Dispersed* is the validator's own spread check; *extended* is an extent beyond 3 km; *compact* is the
  rest; a day with fewer than two located stops is *unmeasured*, never compact.
- **Hard rules.** A move is inadmissible when it would remove a grounded must-visit, bring in a place
  that may not enter (low-value object, commercial gallery on a non-art trip, unlocated, beyond the pool
  reach), schedule two places of one conflict group, exceed a plan-level class cap, add to a day's hard
  marketplace excess, take away the only stop covering a requested interest, or create a dispersed day.
- **Precedence.** Among admissible moves: requested-interest coverage; then geography, which counts only
  when the plan's total extent falls by at least the materiality tolerance `AS_WELL_PLACED_KM` (1 km);
  then, for moves that keep the same places, less unjustified class concentration and more requested
  interests per day. A smaller geographic difference, including one that merely crosses a band boundary,
  is not an improvement and never outweighs usefulness. A variety gain can never make a day dispersed or
  cost a material amount of extent.
- **Usefulness guards.** A replacement that removes a dispersed day, or covers an interest, may sit at
  most one quality tier below the stop it replaces. Any other geographic replacement may lose at most
  one usefulness evidence band and no tier. A grounded semantic anchor is a preference and may give way
  under these guards; only a grounded must-visit is mandatory.
- **Search.** Best-improvement local search over the complete multi-day assignment: swap two stops
  between days, move a stop to a day at least two lighter, replace a discretionary stop, or rebuild a day
  around one kept stop. It stops when nothing improves, after one move per scheduled stop, or after
  `MAX_EVALUATIONS` (30 000) judged plans, and then returns the best valid plan found. Timing or an error
  never fails a generation.
- **Bounds.** At most 300 located candidates are clustered (the rest join an area they fit entirely or
  stand alone) and the working set is at most 120. The working set is not the best-ranked 120: it is
  filled by requested-interest coverage, places near each scheduled must-visit, places near every other
  scheduled stop, the best of every area, and only then usefulness order. A plan with more scheduled
  stops than the bound, or a pool whose candidates do not each have their own provider id, is not
  composed; the pre-Q3 passes run instead and nothing is dropped.
- **The two paths.** The deterministic path starts from the Q2 selection and its spatial clustering, so it
  never schedules fewer stops. A model's plan starts from the model's own days, keeps the existing
  conditions for a replacement (sufficient verified inventory, no active lock), and replaces at most one
  stop per trip day. A grounded must-visit the starting plan left out is added to a free slot, or takes
  the place of a discretionary stop; the pace cap is never broken.
- **Reasoning request.** Steps A to D of the bound are unchanged. Step E is filled by companions (at most
  half of its space: areas that already hold a retained candidate, up to a day's worth each), then
  alternatives (at most half of the rest: one candidate per other area), then usefulness order. Each
  candidate line carries an opaque `area`, and a short block gives each area's size and its near areas.
  No coordinate, distance or duration is sent, and an area id in model prose is replaced before it can
  reach the traveller.
- **Rollback.** `DAY_COMPOSITION_ENABLED=false` restores the pre-Q3 planner, request and prompts exactly.
  The switch is temporary.
- **Not in Q3.** Provider-route-cost-aware replacement, severe walking-leg rejection, route-burden-driven
  recomposition and route feasibility repair (Q4). Provider locality metadata is not stored, so
  "recognizable day purpose" stays a future metric.
- **Known limitations.** A documented place a few kilometres from a compact group can give way to an
  undocumented neighbour, because one evidence band is the agreed price of a material geographic
  improvement. The interest-coverage and anchor-utilization passes still protect grounded anchors. The
  candidate-quality stage marks a second broad candidate of the same name as a duplicate whatever the
  distance between them; that is an eligibility rule and Q3 does not change it.

## 13. Scope of Q4

Q4 addresses A3 and B7: a day that is compact on the map can still be impractical on the provider's own
routes. It makes verified route burden actionable. It added no model call and no routing matrix.

- **One stage.** `app/services/route_recomposition_service.py` runs once, after routing, mode adaptation
  and the routability repair, in both engines. It replaces the single-attempt route-burden repair;
  `ROUTE_RECOMPOSITION_ENABLED=false` restores that one exactly, and `ROUTE_BURDEN_REPAIR_ENABLED` is
  still the master switch.
- **Severity, leg by leg.** `route_burden.assess_legs` classifies a day from the provider's own legs and
  the existing `ROUTE_BURDEN_*` limits only. *Severe*: the verified legs alone exceed a limit. *Unverified*:
  not severe, and a required leg has no provider route. *Elevated*: verified and within the limits, but the
  day needs a vehicle transfer or more walking than the relaxed pace allows. *Normal*: the rest. An
  unavailable leg never hides a verified long leg of the same day, and is never proof that a day is
  feasible or infeasible. A definitive provider refusal (`no_route`, `unroutable_endpoint`) is counted
  apart from an unavailable or failed request.
- **One composition engine.** The moves and hard rules are Q3's (`day_composition.focused_moves`): replace
  a discretionary stop of an offending leg, move it to another day, or swap it with another day's stop.
  Q4 adds reorders of the day, the user locks, and the verdict, which comes only from provider routes. A
  grounded must-visit is never replaced and a locked stop is neither moved nor replaced. A grounded
  semantic anchor is a preference and may give way under the one-tier guard.
- **Shortlist.** At most three proposals per severe day. Proposals with no leg left to route come first; a
  straight-line estimate then orders the rest and drops only those materially longer than today's
  arrangement. When more than one kind of move is available, two kinds are represented before the third
  place is filled by rank. The estimate never accepts anything.
- **Verification.** Only the legs a move changes are routed; legs with existing evidence are reused. A
  cross-day move verifies both days. A move that meets a definitive routing failure is rejected, and a
  transient failure leaves it unverified; two in a row end the stage.
- **Acceptance.** A move that resolves the day (its verified legs are no longer severe) is preferred, a
  fully verified one first. A day that stays severe is changed only when the provider-measured total falls
  by `ROUTE_BURDEN_REPAIR_MIN_IMPROVEMENT_RATIO` and no leg, walking total or vehicle transfer gets
  worse; it keeps its severe class and `LONG_TRAVEL_DAY`. No other day may become severe, and no move may
  add an unverified leg. A day with any unverified leg is never reported as fully verified.
- **Budgets.** Every request of the stage is an ordinary routing request: it takes one of the generation's
  shared `route_requests_left` and its provider credits, and a driving route for an over-long walking leg
  comes from the unchanged alternate-mode cap. `ROUTE_RECOMPOSITION_MAX_REQUESTS_PER_GENERATION` (at
  most 8) only limits the stage further; nothing is added to the shared allowances. A move is dispatched
  only when every changed leg fits what is left of all three limits, so no move is verified in part. A
  leg the provider already holds costs no request and no credit.
- **Final state.** An accepted move rewrites the affected days' order and replaces their legs in the route
  report with the legs of the final order, so the validator judges the final itinerary and nothing stale.
- **Ferries.** The Geoapify routing response documents a per-step `ferry` flag. The adapter keeps it as
  `includes_ferry` on the leg: true or false only when the provider said so, unknown otherwise. A leg
  whose route includes a ferry gets a `route_includes_ferry` warning that states only that fact. No
  timetable, fare, ticket or availability is known or implied, nothing is inferred from geometry, and
  there is no ferry or transit planner. Supported modes remain walk and drive.
- **Not in Q4.** Transit, opening hours, arrival and departure times, repair of elevated days, and any
  change to the Q2 ordering or the Q3 objective.
- **Known limitations.** Three candidates per severe day is a small search. The stage cannot see the
  provider cache without a request, so a move whose legs are all cached is still skipped once the request
  allowance is used up. The ferry flag has not been observed on a live response from this repository;
  it is read defensively from the documented field. A leg cached before Q4 has no ferry statement until
  its cache entry expires.

## 14. Quality tuning corrections

After Q4 the six tuning scenarios were run twice on one source: once with Q3 and Q4 on, once with both
off. The two runs are a comparison of configurations, not a controlled experiment. They showed generic
gaps that neither Q3 nor Q4 owns. This section records the corrections; it is not a new architecture
step, and it changed no threshold, no usefulness ordering, no composition objective and no routing cap.

- **Diagnostics, inactive by default.** `app/core/generation_diagnostics.py` is a generation-scoped
  recorder bound by a context variable, like the performance recorder. Nothing in the application
  activates it; `canary_city.py` does. It records provider place ids, fixed stage labels and numbers
  only: the places on each day after every planner pass and after each repair, what the day-composition
  stage did (ran or not, changed or not, accepted moves, plans judged, search budget exhausted, dispersed
  days before and after), and the routing allowances left. The canary turns that into
  `planning_diagnostics`: which stage introduced, moved or removed each final stop, each stop's evidence
  band, tier, grounding source and provider categories, and every unrouted leg with its endpoints, the
  modes attempted and the provider's failure reason. It is never written to `PlanningState`, never shown
  to a traveller and never read by a planning decision. The composition status
  `unchanged_no_admissible_improvement` covers hard rules, usefulness guards, missing alternatives and
  immaterial gains together; the search does not tell them apart.
- **The geographic boundary is a path measure.** The one existing boundary, 8 km
  (`GEOGRAPHIC_SPREAD_THRESHOLD_KM`), is defined for `day_spread_km`: the straight-line path through a
  day's stops in order. It is not the day's extent (the largest distance between two stops) and is
  never applied to it. `keeps_day_within_spread` is the prospective form of the same measure.
- **Filling prefers compatible places.** The empty-day fill starts a day from the best-ranked place it
  can be built around: a grounded must-visit, or a place with at least one other unused candidate inside
  the boundary. The fallback top-up takes candidates that keep some open day within the boundary first.
  Only when no unused candidate keeps any open day within it may a dispersing one be added, and then
  only a grounded must-visit, or any candidate while the plan still holds fewer than R meaningful stops.
  Stored usefulness evidence (the Q2 evidence band, a grounded anchor) is not a reason: it says a place
  is worth a slot, not that the traveller meant a regional excursion, and a must-visit is the only
  stored input that establishes a deliberately distant stop. The pace target T is a target: a day stays
  below it rather than take remote discretionary filler. An empty day is still never left empty, a
  grounded must-visit is never refused for distance, and no distant place is banned outright.
- **`geographic_dispersion` is its own finding.** A day beyond the boundary whose legs are all verified
  and within their limits used to carry no finding, because an acceptable drive answered the
  feasibility question. It now carries `geographic_dispersion`, which states the straight-line figure
  and never calls the day compact. It is kept apart from `long_travel_day` (provider-verified burden)
  and from `geographic_spread` (a leg unverified, or the routed day over its limits), both unchanged.
  `app/services/geographic_dispersion.py` gives the cause the stored state supports:
  `mandatory_destination` (a note, not a review finding; see the next point),
  `limited_compatible_inventory` (no unused viable candidate would have kept the day within
  the boundary and the plan needs the stop to reach R), `discretionary_dispersion` (a compatible
  candidate existed, or the stop was not needed), and `cause_unverified`. Every cause but the first is
  a warning and therefore a review code. A grounded semantic anchor is discretionary: a preference,
  never an intentional excursion.
- **A must-visit on a dispersed day does not make the day mandatory.** The first focused run labelled
  a day `mandatory_destination` because a central must-visit shared it with two optional places far
  away; had the drive been within its limits, the day would have received a note and passed. The cause
  is `mandatory_destination` only when the day's grounded must-visits are dispersed by themselves, the
  trip's must-visits cannot be shared out over its days without a dispersed day (worked out exactly
  for up to eight places), and the day's optional stops add nothing material to its spread (the
  existing 1 km tolerance). Optional stops that cause or materially increase the dispersion keep the
  warning, as do requested places that could have had days of their own. When the stored state cannot
  establish that the requests alone explain the distance, the cause is `cause_unverified` and the
  finding is not downgraded.
- **Rejection diagnostics.** The composition search counts, under fixed labels, what it could draw on
  (unused candidates, how many may not enter a plan and why, how many fell outside the working set),
  how many moves of each kind it judged, and why they were not taken: a hard rule (an interest would
  lose its cover, a new dispersed day, market excess), a usefulness guard, admissible but not
  improving, a must-visit that may not be replaced, replacement not allowed, and whether an improving
  replacement existed. The interest-coverage pass counts, per uncovered interest, the candidates that
  serve it and why none took a slot (protected stop, tier, the only cover of another interest, class
  concentration, too far from the day), with the provider ids of at most three of the strongest
  candidates tried. The canary adds where an interest's viable candidates are lost between the quality
  stage, the scheduling universe and the bounded reasoning request. Counts and ids only; they are
  bounded, read by no decision, and change neither which moves exist nor their order.
- **Acceptance policy 2: a provider-confirmed ferry is informational.** `ROUTE_INCLUDES_FERRY` is not
  on the Q0 list of accepted review codes, and that list is unchanged. From policy version 2 the canary
  also accepts that one code for a report in which all of this holds: the routing provider's own step
  data positively identifies a ferry on a final leg; every such day carries the `route_includes_ferry`
  warning in the validation report, which the itinerary page shows verbatim; that warning states that
  no timetable, fare, ticket or availability is known; and no ferry warning exists without a
  provider-confirmed leg. A leg the provider said nothing about is unknown, neither a ferry nor the
  absence of one. The policy accepts nothing else: an unrouted leg, a long-travel day, a dispersed day
  or an uncovered interest still fails. Each stored report carries the policy version it was judged
  with, and results recorded under policy 1 are not re-judged. The disclosure is a finding of the
  validation report; since the V1 disclosure contract (section 15) the traveller page also shows it on
  the affected day and marks the leg itself.
- **The rollback switch is unchanged.** `ROUTE_RECOMPOSITION_ENABLED=false` still runs the single-attempt
  repair exactly as before; that repair's code was not edited. The final validation reports a dispersed
  day whichever repair ran.
- **Waterfront discovery.** The broad attraction search never read the traveller's interests, and none
  of its category groups holds a waterfront category, so a waterfront request had no supply path. When
  a waterfront-family interest is requested, the Geoapify adapter now asks for one extra group
  (`man_made.pier`, `beach`, `beach.beach_resort`). Its six places come out of the largest general
  group's share, so the pool is no larger. `maritime.marina` is in the snapshot for CLASSIFICATION
  only and is never queried: a marina met some other way is recognised as waterfront, and whether it
  deserves a slot stays the usefulness ordering's question. The snapshot has no category for a
  promenade or coastal access, and none is invented. A grounded targeted lookup is classified by the
  same rule. A name is never evidence, and a park is never waterfront.
- **Mixed local and broad discovery.** A bounded provider probe showed that an unbiased request inside
  a large destination boundary can return only places tens of kilometres from the destination point,
  while a request biased towards that point returns only what is next to it. Each group's limit is
  therefore split into one BROAD request (the destination filter, no bias) and one LOCAL request (the
  same filter with the provider's proximity bias). The split is fixed and generic
  (`geoapify_categories.LOCAL_SHARE`); at the default 60-place pool it is sights 14 broad / 7 local,
  attractions 6 / 3, culture 6 / 12, parks-heritage-markets 6 / 6 (32 / 28), and restaurants half and
  half. Accommodation, an expansion page and a group without a share are unsplit. There is no global
  bias, and the pool is no larger. **The allocation is a request target, not a guarantee:** a request
  may return fewer places, and a place two requests return is one candidate.
  - *Locality anchors.* The default anchor is the resolved destination point. For a trip with
    must-visits, half the local share of culture, parks-heritage-markets and restaurants (6, 3 and 7
    of 15 at the default sizes) is held back until the must-visits have been grounded, in the existing
    order. A follow-up batch then asks near the coordinates of GROUNDED must-visits only: anchors
    within the existing 3 km proxy of the destination point or of each other are one locality, and at
    most two distinct anchors are used (the first takes the odd slot). With no distinct anchor the
    held slots continue the destination-local list from where it stopped. No model is called, no
    stage is reordered, and a must-visit's identity is never replaced by a follow-up record.
  - *Budget.* The split is planned only when the whole batch plus the worst-case follow-up fits what
    is left of the generation's credit cap; otherwise the unsplit plan runs and nothing is held back.
    Worst case at the default pool (waterfront requested, must-visits, two anchors): 9 attraction
    requests, 2 restaurant requests and 1 accommodation request, then 4 + 2 follow-up requests: 18
    Places requests and 18 credits reserved, against 6 requests and 10 credits reserved before. Each
    request is billed by itself; the 100-credit cap is unchanged.
  - *Cache and identity.* A cached response is keyed by group, category set, geographic filter, bias
    anchor, limit, offset, language and schema version; the boundary / bounding-box / circle fallbacks
    apply to a biased request unchanged. Requests are planned and applied in a fixed order (broad,
    local, interest group; then the follow-up), whatever order the responses arrive in, and merged by
    the existing identity rules. Quality gates and the usefulness ordering are untouched.
  - *Reporting.* The canary's planning diagnostics list every discovery request (source, group,
    requested, returned) and count unique identities, viable and low-value candidates, candidates
    serving a requested interest and scheduled stops by source (`broad`, `destination_local`,
    `must_visit_local`, `interest_local`).
- **One definition of food coverage.** The food interest is covered by a scheduled place the provider's
  own evidence establishes as a food experience, or by a nearby restaurant suggestion that is on the
  plan. A marketplace alone is not food evidence (it may sell flowers): it counts only as a food court,
  or when the provider records a food trade for it. `interest_coverage.food_coverage_evidence` reports
  which kind of evidence covered the interest. What the provider actually returns for a food market has
  not been observed from this repository, so until that is established most markets do not count, and
  food is then covered by restaurants or honestly uncovered.
- **Unroutable grounded endpoints: evidence first.** Both tuning runs had legs the provider refused to
  route to places grounded through the geocoder. The diagnostics above now record the endpoints, modes
  and failure reasons. No access point is invented and no transport link is assumed. A bounded driving
  attempt for a definitively failed walking leg is not implemented; it waits for that evidence.
- **Fallback top-up chooses by usefulness.** The deterministic top-up of an underfilled plan took the
  candidate NEAREST the day among those that keep it within the geographic boundary. With local
  discovery supplying many small nearby places, a day was completed with whatever stood next door
  while better-evidenced compatible places stayed unused (the six-scenario run with mixed discovery).
  It now takes the most useful compatible candidate: the one usefulness ordering's preference, then
  its quality score; distance to the day only separates candidates that ordering holds equal, ahead
  of its neutral name / place-id tie-break. Nothing else changed: the emptiest open day is still
  served first, a candidate that keeps some day within the boundary is always used before a
  dispersing one, a dispersing place is still added only for a grounded must-visit or below R (and
  then the least dispersing one, never the best-evidenced), the top-up only adds, and the duplicate
  separation and coverage passes that follow it are untouched. No second usefulness formula exists.
- **Routing allowance across planner passes: recorded, not changed.** The day-route allowance
  (`2 x days + 4`) belongs to the generation, not to a planner pass. When the planner is re-entered
  (after an AI repair or the usefulness fallback) and returns the SAME days, the second routing pass
  costs nothing: successful legs and definitively refused requests are remembered for the
  generation. When it returns different days that still hold a place the provider cannot route to,
  the new day is a new request, its failed legs are asked one at a time again, and the repair can
  stop on `route_budget_exhausted` before it verifies a replacement -- an itinerary that an earlier
  pass had made fully routable ends with unverified legs and `FEASIBILITY` / `MOVEMENT_DATA`. What is
  remembered is a REQUEST (an exact waypoint sequence), never a place. The diagnostics now record,
  per pass: the allowance before and after, the requests made while building the routes and inside
  each repair and how each was answered (live, definitively refused, served from known legs or from
  the failure memory, refused by an allowance), the legs verified / failed / unverified before and
  after the repairs, each repair attempt, whether the failing places and the suspect had already been
  seen in an earlier pass, and whether an earlier pass ended more routable. They read the state and
  the context only. No allowance, routing decision or repair rule was changed.
- **Known limitation: sub-feature redundancy.** Two records of one complex (a place and one of its
  parts) can both be scheduled. The existing identity rules need the same provider entity or records
  within 50 m of a compatible class, and no provider-backed containment or membership evidence is
  kept. A parent and child are never inferred from names, so this stays a V1 limitation and
  `same_excursion_or_complex_redundancy` stays a future metric.
- **Not changed.** Q0 scenario data, the frozen holdout and its hash, acceptance thresholds, the Q2
  ordering, the Q3 objective and guards, Q4's severity and acceptance rules, every provider and model
  budget. The recorded results of the two tuning runs are kept as they were written.

## 15. V1 disclosure contract

The planner is frozen. This section is about how an outcome is PRESENTED; it changes no plan, no
readiness decision, no review or blocking code, no acceptance rule and no threshold.

**The outcomes (unchanged).** `ready` (no warning, no critical issue), `needs_review` (at least one
review code), `blocked` (a critical issue; the generation still completed), and a failed generation.
No state was added.

**Why presentation needed a contract.** Nearly every generated plan is `needs_review`, because a
warning is raised whenever weather or holiday data exists but was not applied. The same status
therefore covered "weather not applied" and "a day you may not be able to travel", and the traveller
page showed one banner for both. Two material findings -- the ferry disclosure and the dispersed-day
finding -- were also filtered out of the traveller view because their text contained implementation
wording.

**One classification, owned by the backend.** `app/services/review_classification.py` puts every
finding code in one of two classes: `informational` (data that was not applied or not connected,
and internal bookkeeping) or `material` (something the traveller should check before following the
plan: travel burden, an unverified route, a ferry, a dispersed day, an uncovered interest or
requested place, a thin or underfilled plan). A code the module does not know is `material`. Every
validation report carries `review_code_classification` (`{code: class}`) for its codes and finding
categories; a report stored earlier has none. The classification is computed after readiness and the
codes, from them, and nothing reads it to decide anything.

It is **not** the benchmark's acceptance list. The evaluation tooling's accepted codes are all
informational here, but a ferry is material to a traveller even where acceptance policy 2 accepts it
for a benchmark outcome. The tooling does not read the classification and its policy is unchanged.

**What the traveller page shows.**

- *Banner.* `ready` is "Checks passed". `needs_review` is worded and toned by the classification:
  informational-only is a planning draft with details to confirm; any material code is "Review
  needed before you follow this", visibly stronger and announced as an alert. A report without a
  classification is presented as material. `blocked` is unchanged. Nothing is ever upgraded: an
  informational-only plan is still `needs_review`.
- *Findings.* A warning the backend classified as material is never hidden. When its own text is
  implementation prose (an old report) a fixed traveller sentence stands in for it.
- *The affected day.* A material finding the backend attached to a day is shown on that day's card,
  as are a ferry on one of its legs and travel that could not be verified.
- *Ferry legs.* A leg is a ferry leg only when the routing provider positively flagged it
  (`includes_ferry` is true); it is never inferred. Such a leg is never labelled "Walk" or "Vehicle
  transfer" alone: it reads "Includes a ferry crossing", its figures are called a route estimate, and
  it says that waiting, ferry times, tickets and availability are not verified. The stored route and
  mode are unchanged.
- *Unverified legs.* A leg without a verified route gets an explicit row between its two stops
  stating that the travel could not be verified. No mode, time or service is stated. When a trip has
  no routed leg at all, the single trip-level note says so once.

**Wording.** The ferry and dispersed-day findings are written for a traveller and contain no
implementation terms. The ferry finding still states that no timetable, fare, ticket or availability
is known, and now also that the time shown is a route estimate and not a ferry schedule.

**Known limits.** A walking-profile figure on a ferry leg is still what the provider returned; it is
labelled, not corrected. Requested-interest coverage is still binary: a weak but provider-grounded
place can cover an interest and no finding says so. Both are V1.1 items.
