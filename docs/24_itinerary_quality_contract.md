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
| B4 | Per-day coarse class counts and concentration kind | `schedule_diversity` |
| B7 | Stops per day against the pace target; days over the pace walking limit | `pace_targets`, burdens |
| Part A | Routing coverage, meaningful stops, empty days, duplicates, unresolved collisions | existing canary figures |

Straight-line spread is a proxy for a hard-to-travel day, exactly as the validator treats it. It is never
shown as a route distance.

### 5.3 Future metrics (not computed, not estimated)

These need the composition and ranking work of later steps. The report names them under
`future_metrics_not_computed` so that no figure implies they were measured.

| Metric | What is missing today |
| --- | --- |
| Same-excursion or same-complex redundancy | A grounded parent or complex identity. The must-visit multi-slot count and the collision records are only partial proxies. |
| Tourist-value ranking and obscure-filler share | Grounded semantic-ranking evidence. The internal tier is a pre-ranking signal, not a usefulness measure. |
| Repeated crossing between distant clusters | A generic clustering of the day's stops. |
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
