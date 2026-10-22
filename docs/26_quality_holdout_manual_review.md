# Quality holdout: manual review (Part C)

The record of the human (Part C) review of the frozen ten-scenario quality holdout. The document was
generated, with every score slot empty, from the saved records in `benchmark_results/quality_holdout_v1/`.
**All ten itineraries have since received human scores and signed assessments**: six whole-number scores
each (sixty in all), with evidence, by a human reviewer (Garima Prachi, 10/14/2026). The scores are copied
into section 6 and used in section 7. The signed score tables in section 5 are the authoritative record.
The acceptance report built from them is `docs/27_v1_quality_acceptance_report.md`. The checklist wording
in sections 2 and 5–7 ("to be filled in", "empty") is kept as it was when the document was prepared.

Nothing here is a new result. No scenario was run, no provider was called, and the holdout directory was
only read. The frozen records keep their own empty `manual_rubric`; this file is where the scores are
written, so the historical outputs stay exactly as the run produced them.

## 1. The rubric, exactly as the contract states it

Source: `docs/24_itinerary_quality_contract.md`, section 6 ("Part C — manual human quality"). Part C and
section 6 are the same section. The text below is copied from it; nothing is added.

> A person scores each plan from 1 to 5 on:

| # | Dimension | Question (verbatim) | Field in the saved record |
| --- | --- | --- | --- |
| 1 | Attraction usefulness | Are these places a visitor would be glad to have spent a slot on? | `attraction_usefulness` |
| 2 | Geographic and day coherence | Does each day hold together on a map and as an experience? | `geographic_day_coherence` |
| 3 | Personalization | Does the plan reflect the stated interests and must-visits? | `personalization` |
| 4 | Pace realism | Could the day be done at the stated pace, travel included? | `pace_realism` |
| 5 | Redundancy avoidance | Is every slot a distinct experience? | `redundancy_avoidance` |
| 6 | Overall | Would I actually use this plan? | `overall_would_use` |

**Scale (verbatim):** 1 unusable, 2 poor, 3 acceptable with edits, 4 good, 5 excellent.

**Rules (verbatim):**

- This is subjective evaluation. It supplements the deterministic metrics and never replaces them.
- It never overrides a factual-safety failure. A plan that fails part A is failed, whatever its scores.
- A plan is scored before the scorer reads its part B figures, so the numbers do not anchor the judgment.
- Scores are recorded once and are not revised after later changes.
- The scorer may know the destination. No reference itinerary is used as an answer key.

**Release target, on the final unseen quality holdout (verbatim):**

- median overall score ≥ 4 of 5;
- no scenario with an overall score below 3;
- zero hard factual-safety failures (part A).

The contract also says (section 3): "Part A always wins. A plan with a hard failure is a failed plan
whatever parts B and C say", and that the three parts "are never collapsed into one PASS".

### What the contract does not define

These are gaps in the written rubric. They are listed so the reviewer decides them knowingly; this
document does not fill them in.

1. **Anchors exist for the scale, not per dimension.** The five words above are the only anchors. The
   contract gives no description of what a 2 or a 4 means for, say, Pace realism.
2. **Whole numbers or halves.** The contract says "from 1 to 5", gives five anchors for the five whole
   numbers, and says nothing about half points. **This review uses whole numbers only** (1, 2, 3, 4 or 5)
   in every one of the sixty score cells. That is the plain reading of the scale, not a new rule.
3. **One reviewer or several.** Not stated. If more than one person scores, the contract does not say how
   their scores combine.
4. **Median of ten.** With ten scenarios the median is the mean of the 5th and 6th overall scores in
   order, so it can be 3.5. The target is "≥ 4"; 3.5 does not meet it.
5. **Only the Overall score is in the release target.** The other five dimensions are recorded but have
   no threshold.
6. **What counts as a "hard factual-safety failure (part A)" for the third criterion.** The contract's
   text allows more than one reading. The definitions, the evidence and the readings are set out in
   Appendix C, to be read after scoring; the decision belongs to the contract's owner and is recorded in
   section 7. It does not affect how a plan is scored.
7. **"Scored before the scorer reads its part B figures."** To respect this, each scenario section below
   shows the itinerary as a traveller would receive it (places, days, travel legs, warnings, readiness)
   and the Part B figures are kept apart in Appendix A. The same care is taken with the provisional
   observations (Appendix B) and with the automated verdicts and their interpretation (Appendix C): none of
   them is a score, and all three appendices should be read after scoring.

## 2. How to complete the scores

1. Take one scenario section at a time (section 5). Read the request, then the itinerary day by day.
2. Open a map and look up the places. **The saved records contain no coordinates and no map**, so day
   coherence and "outskirts" questions cannot be answered from this file alone. Looking places up is
   allowed ("the scorer may know the destination"); do not compare against a reference itinerary.
3. Write a whole number from 1 to 5 in each of the six `Score` cells, and a sentence of evidence beside
   each. Do this **before** opening Appendix A, B or C for that scenario.
4. Put your name and the date in the scenario's sign-off line. A score is recorded once.
5. When all ten are done, copy the six scores of each into the comparison table (section 6) and complete
   the acceptance calculation (section 7).
6. Leave `benchmark_results/quality_holdout_v1/` untouched. Do not rerun, regenerate or edit a scenario.

## 3. Provenance of what is being scored

| | |
| --- | --- |
| Set | `quality-holdout` (`benchmark_results/quality_holdout_v1/`) |
| Run | generated 2026-10-11T06:09:05Z; trip start date used: 2026-10-16 |
| Scenarios | 10 in the set, 10 completed, 0 not run |
| Failures to produce a result | generation 0, technical 0, quality-metric extraction 0 |
| Run valid | True (reasons against: 0) |
| Code | commit `9f6277b6a56cf4953ac4c4ed8bb8ab77dabc49cd`, working tree dirty: False, modified files: 0, untracked: 0 |
| Source fingerprint | `2694908ba73aebd921504f9bfc8e0c83a24fc44c3689b05e11f33a6669884ae5` over 303 files of backend/app, backend/scripts, backend/requirements.txt (excluding backend/app/tests/) |
| Configuration hash | `3ba911b49b5f1e3996a822ff3f829214e66d25a77bde7be1ba23d80e72727378` |
| Automated verdicts | Recorded per scenario; listed in Appendix C, to be read after scoring |
| Manual rubric in the saved run | "not scored by this tool; see docs/24_itinerary_quality_contract.md part C" |

Checks made when this document was prepared (2026-10-13), all read-only:

- All ten scenario records and the summary carry the **same** code commit, source fingerprint and
  configuration hash, a clean working tree, and `source_unchanged_during_scenario: true`.
- The source fingerprint was **recomputed from the git objects of that commit** (303 files, the stated
  construction) and equals the recorded value.
- The frozen scenario list is pinned by a hash in `backend/app/tests/scripts/test_benchmark_quality_q0.py`,
  which is part of the test suite that passes in CI.
- All 21 files of the holdout directory were fingerprinted (SHA-256) before this document was generated and
  again afterwards; the two listings are identical, and no file's modification time is later than the run.
- What has changed in the application since that commit, for honesty about what the scores describe:
  34 non-test files under `backend/app`, none of which selects, orders, groups or routes places. They are
  the request limits and generation admission, deletion, security headers and configuration checks, and
  two additions to the validation report that restate requested-interest coverage for display
  (`plan_quality_findings.py`, `plan_validator_service.py`, `models/planning_state.py`). The orchestrator
  change is the saved-trips limit at trip creation. The planner that produced these ten itineraries is
  therefore the planner on the release branch, but the release branch as a whole is **not** the fingerprinted
  code, and this holdout was not, and must not be, rerun to "refresh" it.

## 4. Before you score

Score each plan from its own section in section 5. **The automated verdicts on these plans, and the notes on
how the contract's acceptance rule applies to them, are deliberately not shown here.** They are in the
appendices, which are meant to be read only after the scores for a scenario are written down:

- Appendix A: the Part B quality figures of each plan.
- Appendix B: provisional observations by the assistant that prepared this document. They are not scores.
- Appendix C: the automated baseline verdicts, every acceptance check for every scenario, and the
  interpretation of Part A and of the third release criterion.

Each scenario section does show what a traveller would be shown with the plan: its readiness status, its
review codes and the limitations recorded for that run. Those belong to the plan and are part of what is
being judged.

One note that applies to every day of every plan and is therefore not repeated below. Each day carries
this stored warning: "Days are grouped and attractions are ordered within each day using straight-line
(haversine) geographic proximity when coordinates are available; this is not route optimization. Route
ordering, timing, opening hours, and feasibility checks are not implemented yet, so scheduled attractions
have no start/end time, duration, walking distance, or cost estimate." The plans therefore contain **no
visit durations, opening hours or start times**: Pace realism has to be judged from the number of stops,
what kind of places they are, and the travel legs.

## 5. The ten itineraries

### Q-H1 — Chicago, United States

Saved record: `scenarios/q_h1_chicago_united_states.json` (run 2026-10-11T06:02:39Z).

**Request**

- Destination: Chicago, United States (resolved by the provider as "Chicago, IL, United States of America")
- Length and pace: 3 days, balanced
- Interests: architecture, museums, riverfront
- Must-visits: Art Institute of Chicago; Millennium Park
- Travellers: 2; start date 2026-10-16
- Covers (the generic dimensions the scenario was chosen to exercise): river corridor, architecture, museums/culture, multiple must-visits

**Must-visits**

| Requested | Grounded by the provider as | Scheduled |
| --- | --- | --- |
| Art Institute of Chicago | The Art Institute of Chicago | yes |
| Millennium Park | Millennium Park | yes |

**Itinerary as generated**

*Day 1*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | The Art Institute of Chicago | museum | yes | geoapify_places |
| 2 | Millennium Park | park | yes | geoapify |
| 3 | Chicago Architecture Center | museum | yes | geoapify |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| The Art Institute of Chicago → Millennium Park | walk | 13 min | 0.9 km | — |
| Millennium Park → Chicago Architecture Center | walk | 12 min | 0.8 km | — |

Day travel in total: 25 min, 1.7 km (walking 25 min, vehicle 0 min).

Nearby food suggestions: Osaka Sushi & Fresh Fruit Smoothies; Fontano's Pizza and Subs.

> Stored note for this day: AI itinerary reasoning rationale for this day: Day 1 opens with the must‑visit Art Institute in the morning, followed by Millennium Park midday, and concludes with the Chicago Architecture Center in the afternoon to satisfy museum and architecture interests.

*Day 2*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Chicago Stock Exchange Trading Room | yes | yes | geoapify_places |
| 2 | Pritzker Military Museum & Library | museum | yes | geoapify_places |
| 3 | Money Museum | museum | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Chicago Stock Exchange Trading Room → Pritzker Military Museum & Library | walk | 7 min | 0.4 km | — |
| Pritzker Military Museum & Library → Money Museum | walk | 13 min | 0.9 km | — |

Day travel in total: 20 min, 1.4 km (walking 20 min, vehicle 0 min).

Nearby food suggestions: Crave Coffee; Rafael Restaurant.

*Day 3*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | 12th Street Beach | beach | yes | geoapify_places |
| 2 | Shedd Aquarium | aquarium | yes | geoapify_places |
| 3 | Field Museum | museum | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| 12th Street Beach → Shedd Aquarium | walk | 14 min | 0.9 km | — |
| Shedd Aquarium → Field Museum | walk | 8 min | 0.5 km | — |

Day travel in total: 22 min, 1.4 km (walking 22 min, vehicle 0 min).

Nearby food suggestions: Market By The Blackstone; Mercat a la Planxa.

**Requested-interest coverage (as recorded)**

| Interest (canonical form) | Covered by the plan |
| --- | --- |
| architecture | yes |
| museum | yes |
| waterfront | yes |

**Readiness and findings (as recorded)**

- Readiness: `needs_review`
- Review codes: `HOLIDAYS`, `WEATHER`
- Of those, other than data-coverage notes (weather / holidays not applied): none
- Blocking codes: none; critical issue categories: none
- Scheduled stops: 9 over 3 days (target for this pace T = 9, minimum R = 8); empty days: 0; duplicates: 0

**Recorded limitations of this run**

- Routing: 6 of 6 legs have a provider route (100.0%); legs with movement data the provider did not return: 0.
- Movement: legs by mode {'drive': 0, 'walk': 6}; vehicle alternatives tried for 0 leg(s), adopted for 0. The routing provider was run in walking mode with driving as the only alternative; no public transport is modelled.
- Inventory: 49 viable candidates of 60 found (status `healthy`; healthy from H = 21); search expansion used: False.
- Possible duplicate places among candidates: 1 suspicious pair(s), 1 unresolved, 0 of them scheduled.
- Category concentration: hard violations on days none; soft concentration on days none.

**Scores — to be filled in by the human reviewer**

| # | Dimension | Question | Score (1–5) | Evidence / comment |
| --- | --- | --- | :---: | --- |
| 1 | Attraction usefulness | Are these places a visitor would be glad to have spent a slot on? | 3 | Strong attractions such as the Art Institute, Shedd Aquarium, Field Museum, and Chicago Architecture Center, but weaker choices and an incorrectly located museum reduce usefulness. |
| 2 | Geographic and day coherence | Does each day hold together on a map and as an experience? | 2 | Days 1 and 3 are geographically compact. Day 2 is invalid as planned because the Pritzker Military Museum is no longer at its former downtown location. |
| 3 | Personalization | Does the plan reflect the stated interests and must-visits? | 3 | Both must-visits and all three interests are represented, but architecture and waterfront interests could be addressed more meaningfully. |
| 4 | Pace realism | Could the day be done at the stated pace, travel included? | 2 | Three stops per day seems manageable, but incorrect location data, omitted visit durations, and museum-heavy days make the actual pace unreliable. |
| 5 | Redundancy avoidance | Is every slot a distinct experience? | 2 | The Chicago Stock Exchange Trading Room is inside the Art Institute, which is already scheduled on Day 1. Several museum experiences are also concentrated together. |
| 6 | Overall | Would I actually use this plan? | 2 | Useful starting point, but significant corrections are needed before it is practical for a visitor. |

Scale: 1 unusable, 2 poor, 3 acceptable with edits, 4 good, 5 excellent.

Places I looked up and what I found (independent checks):

- **Chicago Stock Exchange Trading Room:** Verified that the reconstructed Trading Room is located inside the Art Institute of Chicago at 111 S. Michigan Avenue. Scheduling it as a separate attraction on Day 2 creates a repeated venue visit.
- **Pritzker Military Museum & Library:** The museum's current location is 10475 12th Street, Kenosha, WI 53144, rather than downtown Chicago. The itinerary incorrectly treats it as a short walking distance from the other Day 2 attractions.
- **Money Museum:** Verified that it has special weekend opening hours on October 17–18, 2026, for Open House Chicago (10 AM–5 PM).
- **Chicago Architecture Center:** Verified its location at 111 E. Wacker Drive and Friday operating hours of 10 AM–7 PM.
- **Art Institute of Chicago:** The general public opening time on Friday, October 16, is 11 AM. The itinerary's morning visit is feasible only if scheduled after opening.
- **Open House Chicago:** An architecture festival is scheduled for October 17–18, 2026, which could provide more relevant experiences for the traveler's interests. 

Other comments:

The itinerary has a reasonable overall structure, with three stops per day and short reported walking distances. Both requested must-visits are included, and all stated interests receive some coverage.

However, the plan has significant practical issues. The Pritzker Military Museum is incorrectly located, making the Day 2 route infeasible. The Chicago Stock Exchange Trading Room is part of the already-scheduled Art Institute, resulting in a repeated venue visit across days. The itinerary also concentrates heavily on museums while offering relatively limited dedicated waterfront and outdoor architecture experiences.

Day 1 is well organized, and Day 3 has good geographic clustering, but the plan does not specify attraction visit durations. This makes the balanced pace difficult to verify, particularly when combining Shedd Aquarium and Field Museum on the same day.

I would replace the incorrectly located museum, remove the repeated venue visit, consider adding a Chicago River architecture experience, and provide realistic visit durations before using this itinerary. Weather suitability remains unverified.

**Overall assessment: 2/5 — Poor. The plan contains useful attractions but requires significant edits before it is visitor-ready.**

Scored by: Garima Prachi  Date: 10/14/2026  
Part B figures (Appendix A) read only after scoring: [ ]

---

### Q-H2 — Barcelona, Spain

Saved record: `scenarios/q_h2_barcelona_spain.json` (run 2026-10-11T06:03:21Z).

**Request**

- Destination: Barcelona, Spain (resolved by the provider as "Barcelona, CT, Spain")
- Length and pace: 4 days, balanced
- Interests: architecture, food, markets
- Must-visits: Sagrada Família; Park Güell
- Travellers: 2; start date 2026-10-16
- Covers (the generic dimensions the scenario was chosen to exercise): architecture, markets/food, multiple must-visits, topographic/geographic separation

**Must-visits**

| Requested | Grounded by the provider as | Scheduled |
| --- | --- | --- |
| Sagrada Família | not grounded (unresolved) | **no** |
| Park Güell | Park Guell | yes |

**Itinerary as generated**

*Day 1*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Park Guell | attraction | yes | geoapify |
| 2 | Casa del Guarda | museum | yes | geoapify_places |
| 3 | Parc del Carmel | park | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Park Guell → Casa del Guarda | walk | 3 min | 0.1 km | — |
| Casa del Guarda → Parc del Carmel | walk | 32 min | 1.6 km | — |

Day travel in total: 34 min, 1.7 km (walking 34 min, vehicle 0 min).

Nearby food suggestions: afiparc; La Torreta del Parc Güell.

*Day 2*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Mercat de Sant Josep - La Boqueria | market | yes | geoapify |
| 2 | Saló del Tinell | museum | yes | geoapify_places |
| 3 | Palau de la Música Catalana | cultural_area | yes | geoapify |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Mercat de Sant Josep - La Boqueria → Saló del Tinell | walk | 14 min | 0.8 km | — |
| Saló del Tinell → Palau de la Música Catalana | walk | 16 min | 1.0 km | — |

Day travel in total: 30 min, 1.9 km (walking 30 min, vehicle 0 min).

Nearby food suggestions: Haiti Bar; pizzeria da nanni.

*Day 3*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Palau de la Generalitat de Catalunya | attraction | yes | geoapify_places |
| 2 | Mercat de Santa Caterina | marketplace | yes | geoapify_places |
| 3 | Picasso Museum | museum | yes | geoapify |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Palau de la Generalitat de Catalunya → Mercat de Santa Caterina | walk | 7 min | 0.5 km | — |
| Mercat de Santa Caterina → Picasso Museum | walk | 5 min | 0.3 km | — |

Day travel in total: 12 min, 0.8 km (walking 12 min, vehicle 0 min).

Nearby food suggestions: Starbucks; Avocados.

*Day 4*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Mercat de Sarrià | marketplace | yes | geoapify_places |
| 2 | Porta de Sarrià | monument | yes | geoapify_places |
| 3 | Plaça de Carmen Tórtola Valencia | park | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Mercat de Sarrià → Porta de Sarrià | walk | 3 min | 0.2 km | — |
| Porta de Sarrià → Plaça de Carmen Tórtola Valencia | walk | 14 min | 0.9 km | — |

Day travel in total: 17 min, 1.2 km (walking 17 min, vehicle 0 min).

Nearby food suggestions: Panino Silvestre; Chocolat-box.

**Requested-interest coverage (as recorded)**

| Interest (canonical form) | Covered by the plan |
| --- | --- |
| architecture | yes |
| food | yes |
| shopping | yes |

How "food" is covered: scheduled places with recorded food evidence: none; otherwise by the nearby food suggestions listed per day.

**Readiness and findings (as recorded)**

- Readiness: `needs_review`
- Review codes: `HOLIDAYS`, `MUST_VISIT`, `WEATHER`
- Of those, other than data-coverage notes (weather / holidays not applied): `MUST_VISIT`
- Blocking codes: none; critical issue categories: none
- Scheduled stops: 12 over 4 days (target for this pace T = 12, minimum R = 10); empty days: 0; duplicates: 0

**Recorded limitations of this run**

- Routing: 8 of 8 legs have a provider route (100.0%); legs with movement data the provider did not return: 0.
- Movement: legs by mode {'drive': 0, 'walk': 8}; vehicle alternatives tried for 0 leg(s), adopted for 0. The routing provider was run in walking mode with driving as the only alternative; no public transport is modelled.
- Inventory: 53 viable candidates of 58 found (status `healthy`; healthy from H = 27); search expansion used: False.
- Possible duplicate places among candidates: 3 suspicious pair(s), 3 unresolved, 0 of them scheduled.
- Category concentration: hard violations on days none; soft concentration on days none.

**Review prompt (a question for the reviewer, not a score):** The request names Sagrada Família as a must-visit and the plan does not schedule it (the record lists it as unresolved: it was not grounded). How much does that matter to you for Personalization and Overall?

Missing Sagrada Família significantly affects both Personalization and Overall quality. It is one of only two explicitly requested must-visits and one of Barcelona's most important architectural landmarks. Although the itinerary includes Park Güell and other culturally relevant attractions, it fails to satisfy a central user requirement.

I assign Personalization 2/5 because the itinerary reflects the broad interests but omits a major must-visit. I also assign Overall 2/5 because I would not use this plan without substantial changes, including adding Sagrada Família and correcting the attractions scheduled on days when they are closed or inaccessible.

**Scores — to be filled in by the human reviewer**

| # | Dimension | Question | Score (1–5) | Evidence / comment |
| --- | --- | --- | :---: | --- |
| 1 | Attraction usefulness | Are these places a visitor would be glad to have spent a slot on? | 3 | Includes worthwhile places such as Park Güell, Palau de la Música Catalana, La Boqueria, and Picasso Museum, but several stops have lower visitor value, particularly on Day 4. |
| 2 | Geographic and day coherence | Does each day hold together on a map and as an experience? | 4 | Attractions are grouped sensibly by area, with short walking routes. However, geographic coherence does not guarantee that every attraction is accessible on its scheduled day. |
| 3 | Personalization | Does the plan reflect the stated interests and must-visits? | 2 | Covers architecture and markets, but misses the explicitly requested Sagrada Família. Food interests are mostly addressed through markets and restaurant suggestions rather than planned food experiences. |
| 4 | Pace realism | Could the day be done at the stated pace, travel included? | 2 | Walking distances are manageable, but Santa Caterina Market is closed on Sunday and the Palau de la Generalitat is not normally open for public tours that weekend. Visit durations and access restrictions are not adequately considered. |
| 5 | Redundancy avoidance | Is every slot a distinct experience? | 3 | Casa del Guarda is within Park Güell, creating some overlap, and three market visits are somewhat repetitive. However, most other stops offer distinct experiences. |
| 6 | Overall | Would I actually use this plan? | 2 | The itinerary needs significant edits, particularly to include Sagrada Família and correct opening-day and accessibility issues. |

Scale: 1 unusable, 2 poor, 3 acceptable with edits, 4 good, 5 excellent.

Places I looked up and what I found (independent checks):

- **Sagrada Família:** Verified that the basilica is a major Barcelona attraction with regular visiting hours in October. It is entirely missing from the generated itinerary despite being an explicitly requested must-visit. The failure to resolve it is a significant personalization issue.
- **Park Güell and Casa del Guarda:** Casa del Guarda is located within Park Güell, near the Monumental Stairway. Treating these as two separate itinerary slots creates partial redundancy.
- **La Boqueria:** The official market operates Monday through Saturday, 8:00 AM–8:30 PM. Its placement on Saturday, October 17, is appropriate.
- **Palau de la Música Catalana:** Guided tours are offered daily, typically 9:00 AM–3:30 PM, and last approximately 50–55 minutes. The itinerary should account for the tour schedule.
- **Palau de la Generalitat:** Public interior tours require advance reservations and normally take place on the second and fourth weekends of the month. The scheduled Sunday, October 18, is the third weekend, so an interior visit is not normally available.
- **Mercat de Santa Caterina:** The market is normally closed on Sundays. Scheduling it on October 18 makes this stop impractical.
- **Picasso Museum:** Open on Sundays in October, from 10:00 AM to 7:00 PM, making it an appropriate Day 3 attraction.
- **Mercat de Sarrià:** Normally open on Mondays from 7:30 AM to 3:00 PM, so the Day 4 market visit is feasible if scheduled within those hours.

Other comments:

The itinerary demonstrates good geographic clustering, with most attractions grouped into walkable neighborhoods and relatively short travel legs. Day 2 is particularly strong in combining markets, history, and architecture.

However, the plan has several important quality issues. Sagrada Família, an explicitly requested must-visit, is missing entirely. Casa del Guarda is already part of Park Güell, creating partial redundancy. Day 3 includes Santa Caterina Market on a Sunday when it is normally closed, along with the Palau de la Generalitat, whose public interior tours are not normally offered that weekend.

Day 4 also contains relatively low-priority attractions despite the omission of a major Barcelona landmark. Three separate market stops introduce some repetition, and food personalization relies heavily on nearby restaurant suggestions rather than structured culinary experiences.

Although the walking distances appear manageable, the itinerary does not provide sufficient visit durations, opening-hour constraints, ticket requirements, or transfers between daily starting locations. The balanced pace therefore cannot be fully validated.

I would add Sagrada Família, correct the inaccessible Day 3 stops, reduce overlap within Park Güell, and strengthen the final day before recommending this itinerary.

**Overall assessment: 2/5 — Poor. The plan contains useful attractions and good geographic grouping but requires significant edits before it is visitor-ready.**

Scored by: Garima Prachi  Date: 10/14/2026  
Part B figures (Appendix A) read only after scoring: [ ]

---

### Q-H3 — Rome, Italy

Saved record: `scenarios/q_h3_rome_italy.json` (run 2026-10-11T06:04:05Z).

**Request**

- Destination: Rome, Italy (resolved by the provider as "Rome, LAZ, Italy")
- Length and pace: 3 days, packed
- Interests: history, art
- Must-visits: Colosseum; Pantheon
- Travellers: 2; start date 2026-10-16
- Covers (the generic dimensions the scenario was chosen to exercise): compact historic core, multiple must-visits, must-visit identity, pace fit

**Must-visits**

| Requested | Grounded by the provider as | Scheduled |
| --- | --- | --- |
| Colosseum | Colosseum | yes |
| Pantheon | Pantheon | yes |

**Itinerary as generated**

*Day 1*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Colosseum | attraction | yes | geoapify |
| 2 | Roman Forum | historic_site | yes | geoapify |
| 3 | Musei Capitolini | museum | yes | geoapify_places |
| 4 | Pantheon | attraction | yes | geoapify |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Colosseum → Roman Forum | walk | 14 min | 0.8 km | — |
| Roman Forum → Musei Capitolini | walk | 22 min | 1.4 km | — |
| Musei Capitolini → Pantheon | walk | 20 min | 1.3 km | — |

Day travel in total: 57 min, 3.5 km (walking 57 min, vehicle 0 min).

Nearby food suggestions: La Terrazza Caffarelli; Caffe del Teatro.

*Day 2*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Area Archeologica di Piano della Comunità | archaeological_site | yes | geoapify_places |
| 2 | Area Archeologica di Campetti - Decumano | archaeological_site | yes | geoapify_places |
| 3 | Santuario dell'Apollo loc. Portonaccio | attraction | yes | geoapify_places |
| 4 | Tomba delle Anatre | archaeological_site | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Area Archeologica di Piano della Comunità → Area Archeologica di Campetti - Decumano | walk | 11 min | 0.7 km | — |
| Area Archeologica di Campetti - Decumano → Santuario dell'Apollo loc. Portonaccio | walk | 17 min | 1.1 km | — |
| Santuario dell'Apollo loc. Portonaccio → Tomba delle Anatre | walk | 19 min | 1.1 km | — |

Day travel in total: 47 min, 2.9 km (walking 47 min, vehicle 0 min).

Nearby food suggestions: L'Isola Felice; Yu Olgiata.

*Day 3*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Tempio di Vespasiano e Tito | archaeological_site | yes | geoapify_places |
| 2 | Museo Nazionale del Palazzo di Venezia | museum | yes | geoapify_places |
| 3 | Foro Boario | archaeological_site | yes | geoapify_places |
| 4 | Castle of the Holy Angel | historic_site | yes | geoapify |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Tempio di Vespasiano e Tito → Museo Nazionale del Palazzo di Venezia | walk | 19 min | 1.2 km | — |
| Museo Nazionale del Palazzo di Venezia → Foro Boario | walk | 16 min | 1.0 km | — |
| Foro Boario → Castle of the Holy Angel | drive | 4 min | 3.2 km | walking the same leg: 43 min, 2.7 km |

Day travel in total: 40 min, 5.5 km (walking 35 min, vehicle 4 min).

Nearby food suggestions: Espresso & Co; Bar Campidoglio.

**Requested-interest coverage (as recorded)**

| Interest (canonical form) | Covered by the plan |
| --- | --- |
| history | yes |
| art | yes |

**Readiness and findings (as recorded)**

- Readiness: `needs_review`
- Review codes: `HOLIDAYS`, `WEATHER`
- Of those, other than data-coverage notes (weather / holidays not applied): none
- Blocking codes: none; critical issue categories: none
- Scheduled stops: 12 over 3 days (target for this pace T = 12, minimum R = 10); empty days: 0; duplicates: 0

**Recorded limitations of this run**

- Routing: 9 of 9 legs have a provider route (100.0%); legs with movement data the provider did not return: 0.
- Movement: legs by mode {'drive': 1, 'walk': 8}; vehicle alternatives tried for 1 leg(s), adopted for 1. The routing provider was run in walking mode with driving as the only alternative; no public transport is modelled.
- Route-burden repair, day 2: attempted, accepted, reason `accepted`; day travel before: 73 min, 7.7 km.
- Inventory: 55 viable candidates of 62 found (status `healthy`; healthy from H = 27); search expansion used: False.
- Possible duplicate places among candidates: 6 suspicious pair(s), 6 unresolved, 0 of them scheduled.
- Category concentration: hard violations on days none; soft concentration on days none.

**Review prompt (a question for the reviewer, not a score):** Check on a map whether any day sits away from the historic centre the request implies, and whether that is a reasonable use of a packed day.

Day 2 is geographically separated from Rome's historic centre. All four scheduled stops are associated with the Veii archaeological area near Isola Farnese, northwest of central Rome. The first stop, Piano della Comunità, is approximately 16 km from the centre.

Although the four attractions are relatively close to each other, the itinerary reports only 47 minutes of walking between them and does not include the substantial journey from central Rome to Veii or the return trip. It also does not adequately establish visitor accessibility for these archaeological sites.

This is not a reasonable allocation of an entire day for a general three-day Rome itinerary, even with a packed pace. The user requested history and art, but did not specifically request Etruscan archaeology or excursions outside the city.

Day 1 is geographically coherent but too intensive, while Day 3 contains some unnecessary backtracking and overlap with the Roman Forum.

I therefore assign Geographic and Day Coherence 2/5 and Pace Realism 2/5.

**Scores — to be filled in by the human reviewer**

| # | Dimension | Question | Score (1–5) | Evidence / comment |
| --- | --- | --- | :---: | --- |
| 1 | Attraction usefulness | Are these places a visitor would be glad to have spent a slot on? | 2 | Day 1 includes major attractions, but Day 2 consists entirely of relatively obscure archaeological sites with uncertain accessibility and limited general tourist appeal. |
| 2 | Geographic and day coherence | Does each day hold together on a map and as an experience? | 2 | Days 1 and 3 are mainly around central Rome, but Day 2 is in the Veii archaeological area north of the historic centre. Its 47 minutes of walking omit the substantial transfer from central Rome. |
| 3 | Personalization | Does the plan reflect the stated interests and must-visits? | 4 | Both Colosseum and Pantheon are included, and the itinerary strongly reflects historical interests. Art is represented through museums, although the overall selection is disproportionately archaeological. |
| 4 | Pace realism | Could the day be done at the stated pace, travel included? | 2 | Day 1 combines four time-intensive attractions, while Day 2 omits travel from Rome to Veii. Day 3 also requires a cross-city transfer. The packed pace does not eliminate logistical constraints. |
| 5 | Redundancy avoidance | Is every slot a distinct experience? | 2 | Several Day 2 archaeological stops provide similar experiences, and Days 1 and 3 return repeatedly to nearby Roman ruins. More variety would improve the itinerary. |
| 6 | Overall | Would I actually use this plan? | 2 | Strong must-visit coverage, but substantial changes to Day 2 and adjustments to the pacing are necessary before the plan is practical. |

Scale: 1 unusable, 2 poor, 3 acceptable with edits, 4 good, 5 excellent.

Places I looked up and what I found (independent checks):

- **Colosseum:** Verified that it is open on October 16, 2026, from 8:30 AM to 6:30 PM, with last admission at 5:30 PM. Entry requires a reserved time slot, making the order and timing of Day 1 important.
- **Roman Forum:** Located next to the Colosseum and open from 9:00 AM to 6:30 PM during the travel dates. It is appropriately grouped with the Colosseum.
- **Musei Capitolini:** Located at Piazza del Campidoglio and generally open daily from 9:30 AM to 7:30 PM. A worthwhile art and history attraction, but adding it to the Colosseum, Forum, and Pantheon makes Day 1 demanding.
- **Area Archeologica di Piano della Comunità:** Located near Isola Farnese in the ancient Veii archaeological area, approximately 16 km northwest of central Rome. It is not a central Rome attraction.
- **Santuario dell'Apollo, Portonaccio:** Located in the Veii archaeological park, near Isola Farnese. The Ministry of Culture confirms its historical significance. Visitor-access arrangements must be checked independently.
- **Tomba delle Anatre:** An ancient Etruscan tomb associated with Veii. It is a genuine archaeological site, but ordinary public access for the planned date was not established.
- **Temple of Vespasian and Titus:** Located within the Roman Forum. Scheduling it separately on Day 3 creates partial overlap with the Roman Forum visit on Day 1.
- **Vatican Museums:** Open on Saturdays but generally closed on Sundays except the last Sunday of the month. This is a possible art-focused alternative for Day 2, subject to ticket availability.

Other comments:

The itinerary successfully includes both requested must-visits, the Colosseum and Pantheon, and strongly reflects an interest in Roman history. Day 1 contains excellent attractions within the historic centre.

However, the itinerary has significant quality issues. Day 2 is entirely dedicated to obscure archaeological remains in Veii, outside central Rome. The recorded walking distances exclude the transfer to and from this area, and public access to all four attractions is not established. This makes the day difficult to recommend for a short sightseeing trip.

Day 1 combines four major attractions that could require most of the day, especially considering admission procedures, queues, museum exploration, and breaks. Day 3 revisits an archaeological site within the previously scheduled Roman Forum, creating partial redundancy.

The plan is also disproportionately focused on ancient archaeological sites, with comparatively limited variety in art-focused experiences. Although the user requested a packed pace, that does not justify inaccessible stops or omitted transfers.

I would replace Day 2 with more accessible central Rome attractions, distribute some Day 1 activities across the three days, and avoid revisiting parts of the Roman Forum unnecessarily.

**Overall assessment: 2/5 — Poor. The itinerary meets both must-visit requirements but requires substantial changes to geographic planning, attraction selection, and pacing before it is visitor-ready.**

Scored by: Garima Prachi  Date: 10/14/2026  
Part B figures (Appendix A) read only after scoring: [ ]

---

### Q-H4 — Singapore

Saved record: `scenarios/q_h4_singapore.json` (run 2026-10-11T06:04:49Z).

**Request**

- Destination: Singapore (resolved by the provider as "Singapore")
- Length and pace: 3 days, balanced
- Interests: gardens, food, waterfront
- Must-visits: Gardens by the Bay
- Travellers: 2; start date 2026-10-16
- Covers (the generic dimensions the scenario was chosen to exercise): dense urban transit context, waterfront/harbor, outdoors/parks, markets/food

**Must-visits**

| Requested | Grounded by the provider as | Scheduled |
| --- | --- | --- |
| Gardens by the Bay | Gardens by the Bay | yes |

**Itinerary as generated**

*Day 1*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Gardens by the Bay | park | yes | geoapify |
| 2 | Esplanade Theatres on the Bay | cultural_area | yes | geoapify |
| 3 | Esplanade Park | park | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Gardens by the Bay → Esplanade Theatres on the Bay | walk | 31 min | 2.0 km | — |
| Esplanade Theatres on the Bay → Esplanade Park | walk | 6 min | 0.4 km | — |

Day travel in total: 37 min, 2.4 km (walking 37 min, vehicle 0 min).

Nearby food suggestions: Yàn; The Aryaa.

*Day 2*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Hong Lim Park | park | yes | geoapify_places |
| 2 | Asian Civilisations Museum | museum | yes | geoapify_places |
| 3 | Fort Canning Park | park | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Hong Lim Park → Asian Civilisations Museum | walk | 22 min | 1.3 km | — |
| Asian Civilisations Museum → Fort Canning Park | walk | 22 min | 1.2 km | — |

Day travel in total: 44 min, 2.5 km (walking 44 min, vehicle 0 min).

Nearby food suggestions: Astoria; Hachi Restaurant.

*Day 3*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Merlion Park | park | yes | geoapify_places |
| 2 | Raffles Place Park | park | yes | geoapify_places |
| 3 | National Gallery Singapore | museum | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Merlion Park → Raffles Place Park | walk | 11 min | 0.6 km | — |
| Raffles Place Park → National Gallery Singapore | walk | 17 min | 0.9 km | — |

Day travel in total: 28 min, 1.5 km (walking 28 min, vehicle 0 min).

Nearby food suggestions: Odette; Hachi.

**Requested-interest coverage (as recorded)**

| Interest (canonical form) | Covered by the plan |
| --- | --- |
| outdoors | yes |
| food | yes |
| waterfront | **no** |

How "food" is covered: scheduled places with recorded food evidence: none; otherwise by the nearby food suggestions listed per day.

**Readiness and findings (as recorded)**

- Readiness: `needs_review`
- Review codes: `WEATHER`
- Of those, other than data-coverage notes (weather / holidays not applied): none
- Blocking codes: none; critical issue categories: none
- Scheduled stops: 9 over 3 days (target for this pace T = 9, minimum R = 8); empty days: 0; duplicates: 0

**Recorded limitations of this run**

- Routing: 6 of 6 legs have a provider route (100.0%); legs with movement data the provider did not return: 0.
- Movement: legs by mode {'drive': 0, 'walk': 6}; vehicle alternatives tried for 0 leg(s), adopted for 0. The routing provider was run in walking mode with driving as the only alternative; no public transport is modelled.
- Inventory: 28 viable candidates of 34 found (status `healthy`; healthy from H = 21); search expansion used: False.
- The planner's fallback pass was used for this plan (recorded as `fallback_pass_used: true`).
- Possible duplicate places among candidates: 5 suspicious pair(s), 5 unresolved, 0 of them scheduled.
- Category concentration: hard violations on days none; soft concentration on days none.

**Review prompt (a question for the reviewer, not a score):** The request asks for waterfront and the record says no scheduled stop covers it. Weigh that under Personalization.

The automated record marks waterfront interest coverage as absent, but independent checks show that this is not entirely accurate.

Gardens by the Bay is situated along Marina Bay, Esplanade – Theatres on the Bay is a waterfront performing arts centre, and Merlion Park directly overlooks Marina Bay. These are meaningful waterfront experiences, even though the provider's category mapping does not recognize them as such.

Therefore, I would not heavily penalize the itinerary for missing waterfront coverage. However, the plan could improve the experience by explicitly including a Marina Bay waterfront walk or Singapore River activity.

The more significant personalization weakness is food. Although the user explicitly requested food experiences, no scheduled attraction focuses on Singaporean food culture, hawker centres, or local cuisine. Food is addressed only through nearby restaurant suggestions.

Gardens by the Bay is correctly included, and garden interests are strongly represented. I therefore assign Personalization 3/5: the plan addresses most requested interests but does not fully deliver on food experiences.

**Scores — to be filled in by the human reviewer**

| # | Dimension | Question | Score (1–5) | Evidence / comment |
| --- | --- | --- | :---: | --- |
| 1 | Attraction usefulness | Are these places a visitor would be glad to have spent a slot on? | 3 | Includes major attractions such as Gardens by the Bay, Merlion Park, Asian Civilisations Museum, and National Gallery. However, Hong Lim Park and Raffles Place Park are relatively low-value standalone tourist stops. |
| 2 | Geographic and day coherence | Does each day hold together on a map and as an experience? | 4 | Each day stays within central Singapore, with reasonable walking distances and sensible neighborhood groupings. However, several nearby waterfront attractions are unnecessarily spread across different days. |
| 3 | Personalization | Does the plan reflect the stated interests and must-visits? | 3 | Includes Gardens by the Bay, multiple gardens and parks, and meaningful waterfront locations. Food is poorly addressed because no food experiences are scheduled, and the record does not recognize existing waterfront coverage. |
| 4 | Pace realism | Could the day be done at the stated pace, travel included? | 4 | Three attractions per day and 28–44 minutes of walking are achievable at a balanced pace. However, tropical heat, humidity, rain, and museum visit durations need consideration. |
| 5 | Redundancy avoidance | Is every slot a distinct experience? | 2 | Six of the nine stops are labeled as parks, several of which are brief green spaces. Repeated park visits reduce the diversity of experiences. |
| 6 | Overall | Would I actually use this plan? | 3 | A workable starting itinerary with no obvious major geographic errors, but it requires better food personalization, attraction selection, and variety. |

Scale: 1 unusable, 2 poor, 3 acceptable with edits, 4 good, 5 excellent.

Places I looked up and what I found (independent checks):

- **Gardens by the Bay:** Verified that the main outdoor gardens and major conservatories are open on October 16, 2026, according to published schedules. The Flower Dome and Cloud Forest normally operate from 9 AM to 9 PM. The requested must-visit is appropriately included.
- **Esplanade – Theatres on the Bay:** Located along Marina Bay and provides genuine waterfront experiences. A free outdoor musical performance is listed for October 16, making this a worthwhile stop.
- **Esplanade Park:** A historic urban park along Connaught Drive, near the Civic District. Open 24 hours, but primarily suitable for a short heritage walk rather than a major attraction visit.
- **Hong Lim Park:** A small 0.97-hectare park known for Speakers' Corner and its political history. It has limited value as a dedicated sightseeing slot for a typical tourist.
- **Fort Canning Park:** A 22-hectare historic hilltop park with gardens and heritage attractions. Open 24 hours and worthwhile for visitors interested in outdoor experiences.
- **Merlion Park:** Located on the Marina Bay waterfront, directly overlooking the bay. Open 24 hours and clearly relevant to the waterfront interest despite the saved record marking waterfront coverage as absent.
- **Raffles Place Park:** A small 0.6-hectare green space in the financial district, mainly used for relaxation by nearby workers. Limited standalone tourist appeal.
- **National Gallery Singapore:** Open daily from 10 AM to 7 PM. Visitors typically spend around three hours exploring it. Its inclusion on Sunday, October 18, is reasonable.
- **Food coverage:** None of the nine scheduled attractions is a dedicated food experience. The nearby restaurant suggestions offer options but do not replace an intentionally planned food activity.

Other comments:

The itinerary provides a reasonable foundation for a three-day Singapore trip. All nine attractions are located within the central urban area, and the daily walking distances appear manageable. The must-visit Gardens by the Bay is correctly included, and major attractions such as the Asian Civilisations Museum, Merlion Park, and National Gallery Singapore add cultural and sightseeing value.

However, the itinerary is overly concentrated on parks and green spaces. Hong Lim Park, Esplanade Park, and Raffles Place Park are relatively minor attractions compared with the major destinations included elsewhere. Using dedicated itinerary slots for these places limits the variety of experiences.

Food personalization is also weak. Despite food being an explicit interest, the plan does not schedule a hawker centre, local market, or dedicated Singaporean dining experience. Nearby restaurant suggestions are useful but insufficient for fully satisfying this interest.

The automated record incorrectly reports no waterfront coverage. In reality, Gardens by the Bay, Esplanade – Theatres on the Bay, and Merlion Park provide meaningful waterfront experiences. This should be acknowledged in the independent human evaluation.

The balanced pace is generally achievable, although Singapore's tropical weather, walking conditions, museum visit durations, and potential rain should be considered. The plan could also be improved by combining nearby Marina Bay waterfront attractions more efficiently.

I would replace some minor park stops with local food experiences, reduce repeated green-space visits, and make the waterfront activities more explicit.

**Overall assessment: 3/5 — Acceptable with edits. The itinerary is geographically practical and covers the must-visit, but improvements are needed in attraction usefulness, food personalization, and experience diversity.**

Scored by: Garima Prachi  Date: 10/14/2026  
Part B figures (Appendix A) read only after scoring: [ ]

---

### Q-H5 — Bangkok, Thailand

Saved record: `scenarios/q_h5_bangkok_thailand.json` (run 2026-10-11T06:05:28Z).

**Request**

- Destination: Bangkok, Thailand (resolved by the provider as "Bangkok, Thailand")
- Length and pace: 4 days, balanced
- Interests: history, markets, food
- Must-visits: Grand Palace
- Travellers: 2; start date 2026-10-16
- Covers (the generic dimensions the scenario was chosen to exercise): large/sprawling city, river corridor, markets/food

**Must-visits**

| Requested | Grounded by the provider as | Scheduled |
| --- | --- | --- |
| Grand Palace | Grand Palace | yes |

**Itinerary as generated**

*Day 1*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Grand Palace | attraction | yes | geoapify |
| 2 | Wat Phra Kaew | historic_site | yes | geoapify |
| 3 | Wat Arun | landmark | yes | geoapify |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Grand Palace → Wat Phra Kaew | walk | 5 min | 0.4 km | — |
| Wat Phra Kaew → Wat Arun | drive | 6 min | 5.6 km | walking the same leg: 55 min, 3.6 km |

Day travel in total: 12 min, 6.0 km (walking 5 min, vehicle 6 min).

Nearby food suggestions: Café Amazon; Doi Kham.

*Day 2*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | พระราชวังสราญรมย์ | castle | yes | geoapify_places |
| 2 | พิพิธภัณฑ์ผ้าในสมเด็จพระนางเจ้าสิริกิติ์ พระบรมราชินีนาถ | museum | yes | geoapify_places |
| 3 | พิพิธภัณฑสถานแห่งชาติ พระนคร | museum | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| พระราชวังสราญรมย์ → พิพิธภัณฑ์ผ้าในสมเด็จพระนางเจ้าสิริกิติ์ พระบรมราชินีนาถ | walk | 14 min | 0.9 km | — |
| พิพิธภัณฑ์ผ้าในสมเด็จพระนางเจ้าสิริกิติ์ พระบรมราชินีนาถ → พิพิธภัณฑสถานแห่งชาติ พระนคร | walk | 17 min | 1.0 km | — |

Day travel in total: 31 min, 1.9 km (walking 31 min, vehicle 0 min).

Nearby food suggestions: Cara Cafe; Krisa.

*Day 3*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Chatuchak Market | market | yes | geoapify |
| 2 | Or Tor Kor Market | market | yes | geoapify |
| 3 | Lumphini Park | park | yes | geoapify |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Chatuchak Market → Or Tor Kor Market | walk | 10 min | 0.7 km | — |
| Or Tor Kor Market → Lumphini Park | drive | 10 min | 10.5 km | walking the same leg: 145 min, 9.1 km |

Day travel in total: 20 min, 11.2 km (walking 10 min, vehicle 10 min).

Nearby food suggestions: none.

> Stored note for this day: No restaurant candidate was found near this day's stops, so no nearby food suggestion is shown for this day.

*Day 4*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | พระที่นั่งวิมานเมฆ | castle | yes | geoapify_places |
| 2 | สวนสราญรมย์ | attraction | yes | geoapify_places |
| 3 | พิพิธภัณฑ์พระบาทสมเด็จพระปกเกล้าเจ้าอยู่หัว | museum | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| พระที่นั่งวิมานเมฆ → สวนสราญรมย์ | drive | 9 min | 7.6 km | walking the same leg: 63 min, 4.1 km |
| สวนสราญรมย์ → พิพิธภัณฑ์พระบาทสมเด็จพระปกเกล้าเจ้าอยู่หัว | walk | 31 min | 2.1 km | — |

Day travel in total: 40 min, 9.6 km (walking 31 min, vehicle 9 min).

Nearby food suggestions: Twist & Fork Pasta; บ้านแพร่ง.

**Requested-interest coverage (as recorded)**

| Interest (canonical form) | Covered by the plan |
| --- | --- |
| history | yes |
| shopping | yes |
| food | yes |

How "food" is covered: scheduled places with recorded food evidence: none; otherwise by the nearby food suggestions listed per day.

**Readiness and findings (as recorded)**

- Readiness: `needs_review`
- Review codes: `WEATHER`
- Of those, other than data-coverage notes (weather / holidays not applied): none
- Blocking codes: none; critical issue categories: none
- Scheduled stops: 12 over 4 days (target for this pace T = 12, minimum R = 10); empty days: 0; duplicates: 0

**Recorded limitations of this run**

- Routing: 8 of 8 legs have a provider route (100.0%); legs with movement data the provider did not return: 0.
- Movement: legs by mode {'drive': 3, 'walk': 5}; vehicle alternatives tried for 3 leg(s), adopted for 3. The routing provider was run in walking mode with driving as the only alternative; no public transport is modelled.
- Routability repair attempts: 1 (coverage before 75.0, after 100.0).
- Inventory: 28 viable candidates of 60 found (status `healthy`; healthy from H = 27); search expansion used: False.
- Possible duplicate places among candidates: 10 suspicious pair(s), 10 unresolved, 0 of them scheduled.
- Category concentration: hard violations on days none; soft concentration on days none.

**Scores — to be filled in by the human reviewer**

| # | Dimension | Question | Score (1–5) | Evidence / comment |
| --- | --- | --- | :---: | --- |
| 1 | Attraction usefulness | Are these places a visitor would be glad to have spent a slot on? | 2 | Strong selections such as the Grand Palace, Wat Arun, National Museum, and Chatuchak Market, but Vimanmek Mansion and King Prajadhipok Museum are not currently available for ordinary visits. |
| 2 | Geographic and day coherence | Does each day hold together on a map and as an experience? | 3 | Days 1–3 largely group attractions by area, but Day 4 involves less efficient cross-city movement. The plan also overlooks the convenient river ferry to Wat Arun. |
| 3 | Personalization | Does the plan reflect the stated interests and must-visits? | 3 | Includes the Grand Palace and relevant historical and market attractions. Food experiences are partly addressed through Or Tor Kor Market, but dedicated food activities remain limited. |
| 4 | Pace realism | Could the day be done at the stated pace, travel included? | 2 | Individual daily stop counts are manageable, but inaccessible venues, omitted ferry options, Bangkok traffic, and lack of visit durations make the plan unreliable. |
| 5 | Redundancy avoidance | Is every slot a distinct experience? | 2 | Grand Palace and Wat Phra Kaew are parts of one complex. The Queen Sirikit Museum of Textiles is also within the Grand Palace compound but scheduled separately on Day 2. |
| 6 | Overall | Would I actually use this plan? | 2 | The itinerary contains worthwhile attractions but needs substantial corrections to accessibility, sequencing, and transport before it could be used. |

Scale: 1 unusable, 2 poor, 3 acceptable with edits, 4 good, 5 excellent.

Places I looked up and what I found (independent checks):

- **Grand Palace and Wat Phra Kaew:** Official Grand Palace information confirms that Wat Phra Kaew is inside the palace compound and included in the same admission ticket. Both are appropriate Day 1 attractions, but they form one combined visitor experience.
- **Wat Arun:** Located across the Chao Phraya River from the historic palace area. A cross-river ferry from Tha Tien Pier takes approximately three minutes, excluding waiting time. The itinerary only provides a driving option.
- **Queen Sirikit Museum of Textiles:** Located inside the Grand Palace compound. Admission is included with the Grand Palace ticket. Scheduling it on Day 2 after visiting the Grand Palace on Day 1 creates unnecessary backtracking.
- **National Museum Bangkok:** Normally open Wednesday through Sunday, 8:30 AM–4:00 PM. The planned Saturday visit is appropriate.
- **Chatuchak Market:** The full weekend market operates Saturday and Sunday, typically 9:00 AM–6:00 PM. Its placement on Sunday, October 18, is appropriate.
- **Or Tor Kor Market:** Located near Chatuchak Market and normally open daily, approximately 6:00 AM–6:00 PM. It offers fresh produce, Thai meals, and desserts, making it a meaningful food experience.
- **Vimanmek Mansion:** Reported closed to public visitors since 2016, with no confirmed reopening for October 19, 2026. It should not be scheduled as a normal museum visit.
- **King Prajadhipok Museum:** The official website reports a temporary renovation closure. Its usual operating schedule also excludes Mondays, making the Day 4 visit unsuitable.
- **Saranrom Palace:** A historic palace building, but ordinary public interior access for the scheduled date could not be independently confirmed.

Other comments:

The itinerary successfully includes the Grand Palace and several important Bangkok attractions, particularly Wat Arun, the National Museum, and Chatuchak Market. It reflects the requested historical and market interests, and Day 3 provides an appropriate opportunity to explore Thai food at Or Tor Kor Market.

However, the plan has significant weaknesses in attraction accessibility, sequencing, and transportation. The Grand Palace and Wat Phra Kaew form one combined complex, while the Queen Sirikit Museum of Textiles is located within the same compound but is unnecessarily scheduled on the following day.

Day 4 is particularly problematic because Vimanmek Mansion is not currently open to ordinary visitors, and King Prajadhipok Museum is closed for renovations and is normally closed on Mondays. This leaves most of the final day unusable without replacement attractions.

The itinerary also relies on driving estimates without adequately considering Bangkok's traffic and available public transportation. In particular, the short ferry crossing to Wat Arun would be a more practical and distinctive travel option. The ten-minute estimate between Or Tor Kor Market and Lumphini Park may also be optimistic in traffic.

Food personalization is partially successful through Or Tor Kor Market, but the itinerary could better reflect local cuisine by including a dedicated street-food or dining experience. Daily sightseeing durations, opening hours, transport waiting times, and weather considerations also need more attention.

I would combine the Grand Palace complex attractions into one visit, replace the inaccessible Day 4 museums, introduce practical ferry or transit options, and strengthen the food experiences.

**Overall assessment: 2/5 — Poor. The itinerary includes relevant attractions but requires significant corrections before it is practical for a visitor.**

Scored by: Garima Prachi  Date: 10/14/2026  
Part B figures (Appendix A) read only after scoring: [ ]

---

### Q-H6 — Sydney, Australia

Saved record: `scenarios/q_h6_sydney_australia.json` (run 2026-10-11T06:06:12Z).

**Request**

- Destination: Sydney, Australia (resolved by the provider as "Sydney, NSW, Australia")
- Length and pace: 3 days, relaxed
- Interests: harbour, outdoors, museums
- Must-visits: Sydney Opera House
- Travellers: 2; start date 2026-10-16
- Covers (the generic dimensions the scenario was chosen to exercise): waterfront/harbor, outdoors/parks, museums/culture, pace fit

**Must-visits**

| Requested | Grounded by the provider as | Scheduled |
| --- | --- | --- |
| Sydney Opera House | Sydney Opera House | yes |

**Itinerary as generated**

*Day 1*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Sydney Opera House | attraction | yes | geoapify |
| 2 | Hayes Street Beach | beach | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Sydney Opera House → Hayes Street Beach | drive | 8 min | 6.0 km | walking the same leg: 68 min, 4.2 km |

Day travel in total: 8 min, 6.0 km (walking 0 min, vehicle 8 min).

Nearby food suggestions: none.

> Stored note for this day: No restaurant candidate was found near this day's stops, so no nearby food suggestion is shown for this day.

*Day 2*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Australian Museum | museum | yes | geoapify_places |
| 2 | Hyde Park | park | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Australian Museum → Hyde Park | walk | 9 min | 0.5 km | — |

Day travel in total: 9 min, 0.5 km (walking 9 min, vehicle 0 min).

Nearby food suggestions: Little Sparrow; Cellini's Cafe.

> Stored note for this day: AI itinerary reasoning rationale for this day: Combines a cultural museum visit with a relaxed afternoon in nearby green spaces.

*Day 3*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Darling Harbour | ferry_or_waterfront | yes | geoapify |
| 2 | Australian National Maritime Museum | museum | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Darling Harbour → Australian National Maritime Museum | walk | 3 min | 0.3 km | — |

Day travel in total: 3 min, 0.3 km (walking 3 min, vehicle 0 min).

Nearby food suggestions: Pendolino; Black Sugar.

**Requested-interest coverage (as recorded)**

| Interest (canonical form) | Covered by the plan |
| --- | --- |
| waterfront | yes |
| outdoors | yes |
| museum | yes |

**Readiness and findings (as recorded)**

- Readiness: `needs_review`
- Review codes: `ROUTE_INCLUDES_FERRY`, `WEATHER`
- Of those, other than data-coverage notes (weather / holidays not applied): `ROUTE_INCLUDES_FERRY`
- Blocking codes: none; critical issue categories: none
- Scheduled stops: 6 over 3 days (target for this pace T = 6, minimum R = 5); empty days: 0; duplicates: 0

**Recorded limitations of this run**

- Routing: 3 of 3 legs have a provider route (100.0%); legs with movement data the provider did not return: 0.
- Ferry: the provider identified 1 leg(s) as including a ferry, on day(s) [3]; a traveller-facing ferry warning is on day(s) [3]. No timetable, fare or operator is recorded.
- Movement: legs by mode {'drive': 1, 'walk': 2}; vehicle alternatives tried for 1 leg(s), adopted for 1. The routing provider was run in walking mode with driving as the only alternative; no public transport is modelled.
- Inventory: 59 viable candidates of 61 found (status `healthy`; healthy from H = 14); search expansion used: False.
- Possible duplicate places among candidates: 2 suspicious pair(s), 2 unresolved, 0 of them scheduled.
- Category concentration: hard violations on days none; soft concentration on days none.

**Scores — to be filled in by the human reviewer**

| # | Dimension | Question | Score (1–5) | Evidence / comment |
| --- | --- | --- | :---: | --- |
| 1 | Attraction usefulness | Are these places a visitor would be glad to have spent a slot on? | 3 | Includes major attractions such as Sydney Opera House, Australian Museum, Darling Harbour, and Maritime Museum. Hayes Street Beach and Hyde Park are pleasant but relatively modest standalone stops. |
| 2 | Geographic and day coherence | Does each day hold together on a map and as an experience? | 3 | Days 2 and 3 are geographically compact. Day 1 crosses the harbour to Neutral Bay, which is feasible but less convenient than a nearby Opera House waterfront experience. |
| 3 | Personalization | Does the plan reflect the stated interests and must-visits? | 4 | Includes the requested Opera House and meaningfully covers harbour, outdoors, and museums through waterfront attractions, a beach, a park, and two museums. |
| 4 | Pace realism | Could the day be done at the stated pace, travel included? | 4 | Two attractions per day and short walking distances make the schedule manageable at a relaxed pace. However, Day 1 requires a cross-harbour transfer, and the Day 3 ferry warning needs clarification. |
| 5 | Redundancy avoidance | Is every slot a distinct experience? | 4 | The six stops provide distinct experiences, including architecture, beach scenery, natural history, parks, harbour sightseeing, and maritime history. Some thematic overlap exists, but it is appropriate for the requested interests. |
| 6 | Overall | Would I actually use this plan? | 3 | A usable foundation with good interest coverage, but the itinerary needs transport clarification and somewhat stronger activity selection before recommendation. |

Scale: 1 unusable, 2 poor, 3 acceptable with edits, 4 good, 5 excellent.

Places I looked up and what I found (independent checks):

- **Sydney Opera House:** Verified that guided tours generally operate daily, subject to availability, and advance booking is recommended. The must-visit is correctly included on Day 1. An Opera House performance is also scheduled for October 16, 2026, although attending a performance is not part of the generated itinerary.
- **Hayes Street Beach:** Located in Neutral Bay, across Sydney Harbour from the Opera House. Neutral Bay Wharf is approximately two minutes away on foot and is served by the F5 ferry route. The itinerary omits this public transport option and proposes driving instead.
- **Australian Museum:** Located at 1 William Street, directly opposite Hyde Park. Normally open daily from 10 AM to 5 PM, with free general admission. Its Saturday scheduling is appropriate.
- **Hyde Park:** A major central Sydney public park immediately adjacent to the Australian Museum. The pairing is geographically sensible and suitable for a relaxed afternoon.
- **Darling Harbour:** A major waterfront precinct with walking areas, dining, recreation, and cultural attractions. An appropriate destination for the requested harbour interest.
- **Australian National Maritime Museum:** Located at 2 Murray Street, Darling Harbour. Normally open from 10 AM to 4 PM, with last vessel boarding at 3:10 PM. Its proximity to Darling Harbour makes the Day 3 pairing appropriate.
- **Day 3 ferry warning:** The saved record flags one ferry-containing route on Day 3, but the displayed route between Darling Harbour and the Maritime Museum is a 300-metre walk. The ferry requirement is not evident from these attraction locations and should be investigated rather than accepted without verification.

Other comments:

The itinerary provides a reasonable foundation for a relaxed three-day Sydney trip. It includes the requested Sydney Opera House and covers the stated interests in harbour experiences, outdoor activities, and museums.

Day 2 is particularly coherent, combining the Australian Museum with nearby Hyde Park. Day 3 also offers a sensible pairing of Darling Harbour and the Australian National Maritime Museum, with minimal walking between attractions.

However, Day 1 requires travel across Sydney Harbour from the Opera House to Hayes Street Beach. While this is feasible, the itinerary presents only a driving option and does not account for the nearby Neutral Bay ferry service. This reduces the usefulness of the transport recommendations.

A further concern is the recorded ferry warning on Day 3. The itinerary describes a short walking route between Darling Harbour and the Maritime Museum, yet the saved metadata identifies a ferry-containing leg. This inconsistency suggests that route classification or warning generation needs investigation.

The schedule is realistic for a relaxed pace, but some days feel underutilized. Hayes Street Beach is a relatively minor standalone attraction, and there are opportunities to include more distinctive harbour scenery or optional sightseeing without making the itinerary overly demanding.

The itinerary offers good variety, including an architectural landmark, a beach, a park, a general museum, a waterfront precinct, and a maritime museum. No major attraction duplication is apparent.

I would clarify the Day 3 ferry warning, incorporate practical ferry and public transport options, and strengthen Day 1's sightseeing choices.

**Overall assessment: 3/5 — Acceptable with edits. The itinerary covers the requested interests and is generally practical, but transportation guidance and attraction selection could be improved.**

Scored by: Garima Prachi  Date: 10/14/2026  
Part B figures (Appendix A) read only after scoring: [ ]

---

### Q-H7 — Reykjavík, Iceland

Saved record: `scenarios/q_h7_reykjavik_iceland.json` (run 2026-10-11T06:06:49Z).

**Request**

- Destination: Reykjavík, Iceland (resolved by the provider as "Reykjavik, Iceland")
- Length and pace: 2 days, relaxed
- Interests: architecture, museums, harbor
- Must-visits: none
- Travellers: 2; start date 2026-10-16
- Covers (the generic dimensions the scenario was chosen to exercise): compact core, thin inventory, waterfront/harbor, museums/culture

**Itinerary as generated**

*Day 1*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Hallgrímskirkja | architecture | yes | geoapify |
| 2 | Listasafn Reykjavíkur | museum | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Hallgrímskirkja → Listasafn Reykjavíkur | walk | 17 min | 1.2 km | — |

Day travel in total: 17 min, 1.2 km (walking 17 min, vehicle 0 min).

Nearby food suggestions: Biang Reykjavík; Dragon Dim Sum.

*Day 2*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Listasafn Íslands | museum | yes | geoapify_places |
| 2 | Skarfaklettur | beach | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Listasafn Íslands → Skarfaklettur | drive | 7 min | 4.7 km | walking the same leg: 65 min, 4.4 km |

Day travel in total: 7 min, 4.7 km (walking 0 min, vehicle 7 min).

Nearby food suggestions: Iðnó; La Barceloneta.

**Requested-interest coverage (as recorded)**

| Interest (canonical form) | Covered by the plan |
| --- | --- |
| architecture | yes |
| museum | yes |
| waterfront | yes |

**Readiness and findings (as recorded)**

- Readiness: `needs_review`
- Review codes: `WEATHER`
- Of those, other than data-coverage notes (weather / holidays not applied): none
- Blocking codes: none; critical issue categories: none
- Scheduled stops: 4 over 2 days (target for this pace T = 4, minimum R = 4); empty days: 0; duplicates: 0

**Recorded limitations of this run**

- Routing: 2 of 2 legs have a provider route (100.0%); legs with movement data the provider did not return: 0.
- Movement: legs by mode {'drive': 1, 'walk': 1}; vehicle alternatives tried for 1 leg(s), adopted for 1. The routing provider was run in walking mode with driving as the only alternative; no public transport is modelled.
- Inventory: 39 viable candidates of 52 found (status `healthy`; healthy from H = 9); search expansion used: False.
- Possible duplicate places among candidates: 2 suspicious pair(s), 2 unresolved, 0 of them scheduled.
- Category concentration: hard violations on days none; soft concentration on days none.

**Scores — to be filled in by the human reviewer**

| # | Dimension | Question | Score (1–5) | Evidence / comment |
| --- | --- | --- | :---: | --- |
| 1 | Attraction usefulness | Are these places a visitor would be glad to have spent a slot on? | 2 | Hallgrímskirkja and both art museums are worthwhile, but Skarfaklettur has limited general tourist appeal and is incorrectly categorized as a beach. |
| 2 | Geographic and day coherence | Does each day hold together on a map and as an experience? | 3 | Day 1 is a sensible central-city route. Day 2 involves a transfer from the city-centre gallery to an industrial harbour area, which is less coherent as a visitor experience. |
| 3 | Personalization | Does the plan reflect the stated interests and must-visits? | 3 | Architecture and museum interests are represented, but harbour coverage is limited to Skarfaklettur rather than a more meaningful harbour attraction or experience. |
| 4 | Pace realism | Could the day be done at the stated pace, travel included? | 4 | Two stops per day and short travel times are realistic for a relaxed pace. However, October weather and transport arrangements for Day 2 require consideration. |
| 5 | Redundancy avoidance | Is every slot a distinct experience? | 4 | The four stops are distinct. The two art museums have related themes but separate collections, so they do not constitute meaningful duplication. |
| 6 | Overall | Would I actually use this plan? | 2 | The itinerary is feasible in parts, but Skarfaklettur is a weak destination and the plan misses more useful harbour experiences. Significant attraction-selection edits are needed. |

Scale: 1 unusable, 2 poor, 3 acceptable with edits, 4 good, 5 excellent.

Places I looked up and what I found (independent checks):

- **Hallgrímskirkja:** Official opening hours for Friday, October 16, 2026, are 10:00 AM–5:00 PM, with last tower entry at 4:45 PM. The church nave will be temporarily closed from 12:45 PM to 1:30 PM for a private service. It is an appropriate architecture-focused attraction.
- **Reykjavík Art Museum (Listasafn Reykjavíkur):** The museum operates across multiple locations. Hafnarhús, the downtown branch near the Old Harbour, is open daily from 10:00 AM–5:00 PM. The itinerary should identify the specific branch rather than using the general museum name.
- **National Gallery of Iceland (Listasafn Íslands):** Located at Fríkirkjuvegur 7 and open daily from 10:00 AM–5:00 PM. Its placement on Saturday, October 17, is appropriate.
- **Skarfaklettur:** Identified as a coastal rock formation near Skarfagarður and the Sundahöfn harbour area, rather than a conventional tourist beach. It is a questionable standalone attraction for a relaxed Reykjavík visit.
- **Reykjavík Old Harbour:** A central harbour area offering waterfront walks, harbour views, restaurants, and access to sightseeing boat tours. It would be a stronger match for the requested harbour interest than Skarfaklettur.
- **Weather considerations:** October sightseeing in Reykjavík can involve cold temperatures, strong winds, and rain. The exposed coastal stop should include a weather-dependent alternative. No specific forecast for October 16–17 was verified.

Other comments:

The itinerary provides a manageable two-day schedule and includes relevant architectural and cultural attractions. Hallgrímskirkja, Reykjavík Art Museum, and the National Gallery of Iceland are worthwhile stops, and their published opening hours support the planned dates.

However, the attraction selection is not consistently useful. Skarfaklettur is a coastal rock formation near the industrial harbour rather than a conventional visitor beach. Although it provides some waterfront exposure, it is a relatively weak choice for a dedicated attraction slot.

The itinerary also misses an opportunity to explore Reykjavík's Old Harbour, which would offer a more meaningful harbour experience and integrate better with the central-city attractions. Day 1 is geographically coherent, but Day 2 involves travelling away from the historic centre for a limited sightseeing experience.

There is some thematic concentration on art museums, although the two museum visits are distinct and not inherently redundant. The relaxed pace is achievable with two attractions per day, but the itinerary should specify museum locations and account for opening hours, weather, and transport options.

The recorded interest coverage is technically broad, but the quality of the harbour experience is weak. I would replace Skarfaklettur with an Old Harbour visit, clarify the Reykjavík Art Museum branch, and include a weather-dependent alternative for outdoor activities.

**Overall assessment: 2/5 — Poor. The itinerary includes worthwhile museums and architecture but needs significant improvement in harbour attraction selection and visitor experience.**

Scored by: Garima Prachi  Date: 10/14/2026  
Part B figures (Appendix A) read only after scoring: [ ]

---

### Q-H8 — Rio de Janeiro, Brazil

Saved record: `scenarios/q_h8_rio_de_janeiro_brazil.json` (run 2026-10-11T06:07:15Z).

**Request**

- Destination: Rio de Janeiro, Brazil (resolved by the provider as "Rio de Janeiro, RJ, Brazil")
- Length and pace: 4 days, balanced
- Interests: outdoors, history, food
- Must-visits: Christ the Redeemer; Sugarloaf Mountain
- Travellers: 2; start date 2026-10-16
- Covers (the generic dimensions the scenario was chosen to exercise): topographic/geographic separation, outdoors/parks, multiple must-visits

**Must-visits**

| Requested | Grounded by the provider as | Scheduled |
| --- | --- | --- |
| Christ the Redeemer | Christ the Redeemer | yes |
| Sugarloaf Mountain | Sugarloaf | yes |

**Itinerary as generated**

*Day 1*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Museu Penitenciário do Estado do Rio de Janeiro | museum | yes | geoapify_places |
| 2 | instituto casa Roberto Marinho | museum | yes | geoapify_places |
| 3 | Centro de Visitantes Paineiras | museum | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Museu Penitenciário do Estado do Rio de Janeiro → instituto casa Roberto Marinho | drive | 6 min | 5.4 km | walking the same leg: 94 min, 5.4 km |
| instituto casa Roberto Marinho → Centro de Visitantes Paineiras | drive | 9 min | 4.6 km | walking the same leg: 122 min, 4.6 km |

Day travel in total: 16 min, 10.0 km (walking 0 min, vehicle 16 min).

Nearby food suggestions: Monet; Cheirin Bão.

*Day 2*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Sugarloaf | attraction | yes | geoapify |
| 2 | Museu de Ciências da Terra | museum | yes | geoapify_places |
| 3 | Forte Duque de Caxias | museum | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Sugarloaf → Museu de Ciências da Terra | walk | 30 min | 1.8 km | — |
| Museu de Ciências da Terra → Forte Duque de Caxias | walk | 80 min | 4.3 km | a vehicle alternative was tried: allowance_exhausted |

Day travel in total: 110 min, 6.1 km (walking 110 min, vehicle 0 min).

Nearby food suggestions: Bar Urca; Restaurante Garota da Urca.

*Day 3*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Rio de Janeiro Botanical Garden | park | yes | geoapify |
| 2 | Parque Henrique Lage | park | yes | geoapify_places |
| 3 | Christ the Redeemer | attraction | yes | geoapify |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Rio de Janeiro Botanical Garden → Parque Henrique Lage | walk | 35 min | 2.1 km | — |
| Parque Henrique Lage → Christ the Redeemer | walk | 166 min | 6.3 km | a vehicle alternative was tried: allowance_exhausted |

Day travel in total: 201 min, 8.4 km (walking 201 min, vehicle 0 min).

Nearby food suggestions: Terraço Corcovado by J. Schweppe&CO.; Plage Cafe.

*Day 4*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Praça da Apoteose | attraction | yes | geoapify_places |
| 2 | Museum of Tomorrow | museum | yes | geoapify |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Praça da Apoteose → Museum of Tomorrow | walk | 52 min | 3.4 km | a vehicle alternative was tried: allowance_exhausted |

Day travel in total: 52 min, 3.4 km (walking 52 min, vehicle 0 min).

Nearby food suggestions: Restaurante Galeto Divino; Alqui.

**Requested-interest coverage (as recorded)**

| Interest (canonical form) | Covered by the plan |
| --- | --- |
| outdoors | yes |
| history | yes |
| food | yes |

How "food" is covered: scheduled places with recorded food evidence: none; otherwise by the nearby food suggestions listed per day.

**Readiness and findings (as recorded)**

- Readiness: `needs_review`
- Review codes: `LONG_TRAVEL_DAY`, `WEATHER`
- Of those, other than data-coverage notes (weather / holidays not applied): `LONG_TRAVEL_DAY`
- Blocking codes: none; critical issue categories: none
- Scheduled stops: 11 over 4 days (target for this pace T = 12, minimum R = 10); empty days: 0; duplicates: 0

**Recorded limitations of this run**

- Routing: 7 of 7 legs have a provider route (100.0%); legs with movement data the provider did not return: 0.
- Movement: legs by mode {'drive': 2, 'walk': 5}; vehicle alternatives tried for 5 leg(s), adopted for 2. The routing provider was run in walking mode with driving as the only alternative; no public transport is modelled.
- Route-burden repair, day 3: attempted, not accepted, reason `no_material_improvement`; day travel before: 201 min, 8.4 km.
- Route-burden repair, day 2: not attempted, not accepted, reason `route_budget_exhausted`; day travel before: 110 min, 6.1 km.
- Route-burden repair, day 4: not attempted, not accepted, reason `route_budget_exhausted`; day travel before: 52 min, 3.4 km.
- Travel-burden flags set by the checker: day 2: excessive walking, long route; day 3: excessive walking, long route; day 4: excessive walking, long route.
- Inventory: 53 viable candidates of 62 found (status `healthy`; healthy from H = 27); search expansion used: False.
- The planner's fallback pass was used for this plan (recorded as `fallback_pass_used: true`).
- Possible duplicate places among candidates: 1 suspicious pair(s), 1 unresolved, 0 of them scheduled.
- Category concentration: hard violations on days none; soft concentration on days none.

**Review prompt (a question for the reviewer, not a score):** Days 2, 3 and 4 contain long walking legs (the leg tables show the minutes). Judge Pace realism from them.

I assign Pace Realism 1/5 because the itinerary includes multiple excessive walking transfers that are inconsistent with the requested balanced pace.

On Day 2, the 80-minute, 4.3 km walk from the Earth Sciences Museum to Forte Duque de Caxias is unnecessarily demanding, particularly because the fort itself requires an uphill approach.

Day 3 is the most serious problem. The itinerary schedules a 166-minute, 6.3 km walk from Parque Lage to Christ the Redeemer. This involves difficult hillside terrain and should not be treated as a normal visitor transfer. Visitors would ordinarily use the Corcovado train or authorized transport services.

Day 4 includes a 52-minute, 3.4 km walk between Praça da Apoteose and the Museum of Tomorrow. Although more manageable, this could be replaced with a public transportation connection.

The itinerary reports 110 minutes of walking on Day 2, 201 minutes on Day 3, and 52 minutes on Day 4, excluding time spent exploring attractions, waiting, or taking breaks. The routing system also does not model public transportation.

These issues make the plan excessively tiring and impractical for a balanced-paced trip. Therefore, Pace Realism deserves 1/5 rather than 2/5 or higher.

**Scores — to be filled in by the human reviewer**

| # | Dimension | Question | Score (1–5) | Evidence / comment |
| --- | --- | --- | :---: | --- |
| 1 | Attraction usefulness | Are these places a visitor would be glad to have spent a slot on? | 3 | Includes excellent landmarks such as Sugarloaf, Christ the Redeemer, Botanical Garden, Parque Lage, and Museum of Tomorrow, but some museum and visitor-centre stops are less useful. |
| 2 | Geographic and day coherence | Does each day hold together on a map and as an experience? | 2 | Days 2 and 3 combine attractions separated by difficult walking routes and elevation changes. Day 4 also includes an unnecessarily long transfer. |
| 3 | Personalization | Does the plan reflect the stated interests and must-visits? | 3 | Both must-visits and outdoor interests are covered. History is partially represented, but food is addressed almost entirely through nearby restaurant suggestions. |
| 4 | Pace realism | Could the day be done at the stated pace, travel included? | 1 | The 80-minute walk on Day 2, 166-minute walk uphill toward Christ the Redeemer on Day 3, and 52-minute walk on Day 4 are inappropriate for the requested balanced pace. |
| 5 | Redundancy avoidance | Is every slot a distinct experience? | 4 | Most stops offer different experiences, including landmarks, gardens, museums, historic attractions, and viewpoints. Some museum concentration exists, but there is little actual duplication. |
| 6 | Overall | Would I actually use this plan? | 2 | The plan includes important attractions but requires major changes to its transportation, daily routes, and attraction selection before it is usable. |

Scale: 1 unusable, 2 poor, 3 acceptable with edits, 4 good, 5 excellent.

Places I looked up and what I found (independent checks):

- **Museu Penitenciário do Estado do Rio de Janeiro:** A genuine specialist museum covering the history of Rio's prison system. Riotur lists its opening hours as Monday–Friday, 10 AM–4 PM. The Friday visit is feasible, but it is a relatively niche attraction.
- **Instituto Casa Roberto Marinho:** Located at Rua Cosme Velho 1105 and normally open Tuesday–Sunday, noon–6 PM. The Friday visit is appropriate.
- **Centro de Visitantes Paineiras:** Associated with access to Christ the Redeemer. Official visitor information recommends authorized vans because parking is unavailable. Scheduling this centre separately from Christ the Redeemer creates unnecessary travel.
- **Sugarloaf Mountain:** The official cable car operates daily, normally 8:30 AM–9 PM, with last boarding at 7:30 PM. This is an appropriate must-visit.
- **Museu de Ciências da Terra:** Located in Urca and listed as open Wednesday–Saturday, 10 AM–4 PM. Saturday is a suitable day, although it is a more specialized museum.
- **Forte Duque de Caxias:** Located in Leme rather than Urca. Reaching the fort involves an uphill approach of approximately 800 metres. The 80-minute walk from the Earth Sciences Museum adds excessive travel for a balanced day.
- **Christ the Redeemer:** Access is available by Corcovado train or authorized vans. The generated 166-minute walk from Parque Lage is unsuitable as a normal transfer. Escalator and inclined-elevator construction is also underway during October 2026.
- **Praça da Apoteose:** Located in the Sambadrome complex. It is culturally significant but offers limited standalone sightseeing without an event or organized visit.
- **Museum of Tomorrow:** Located at Praça Mauá, 1. Current official hours are Thursday–Tuesday, 10 AM–6 PM. It is open on Monday, October 19, under the published schedule, and can be reached by VLT light rail. 

Other comments:

The itinerary successfully schedules both requested must-visits, Christ the Redeemer and Sugarloaf Mountain, and includes several worthwhile attractions such as the Botanical Garden, Parque Lage, and Museum of Tomorrow. It provides a reasonable variety of museums, historic landmarks, and outdoor experiences.

However, the itinerary has serious transportation and geographic planning problems. Day 2 includes an 80-minute walking transfer to Forte Duque de Caxias. Day 3 includes a particularly unrealistic 166-minute walk from Parque Lage to Christ the Redeemer, failing to account for the conventional train and authorized visitor transport options. Day 4 also requires an unnecessarily long 52-minute walk.

The Paineiras Visitor Center is scheduled on Day 1 despite being closely associated with visiting Christ the Redeemer on Day 3. This introduces avoidable travel and reduces geographic efficiency.

Some museum selections are relatively specialized compared with Rio's major visitor attractions. Food personalization is also weak, as the itinerary relies primarily on restaurant suggestions rather than scheduling meaningful Brazilian food experiences.

Although all scheduled stops have provider identities and the routing coverage is reported as complete, this does not ensure that the proposed routes are appropriate for tourists. The route-repair process was unable to resolve several excessive travel legs.

I would restructure Days 2 and 3 around practical visitor transport, combine the Paineiras Visitor Center with Christ the Redeemer if needed, replace some lower-value attractions, and add a dedicated local food experience.

**Overall assessment: 2/5 — Poor. The itinerary contains strong attractions but has major routing and pacing failures that require substantial correction before it is visitor-ready.**

Scored by: Garima Prachi  Date: 10/14/2026  
Part B figures (Appendix A) read only after scoring: [ ]

---

### Q-H9 — Prague, Czechia

Saved record: `scenarios/q_h9_prague_czechia.json` (run 2026-10-11T06:07:59Z).

**Request**

- Destination: Prague, Czechia (resolved by the provider as "Prague, Czechia")
- Length and pace: 3 days, balanced
- Interests: history, architecture, art
- Must-visits: Charles Bridge; Prague Castle
- Travellers: 2; start date 2026-10-16
- Covers (the generic dimensions the scenario was chosen to exercise): compact historic core, river corridor, architecture, multiple must-visits

**Must-visits**

| Requested | Grounded by the provider as | Scheduled |
| --- | --- | --- |
| Charles Bridge | Charles Bridge | yes |
| Prague Castle | Prague Castle | yes |

**Itinerary as generated**

*Day 1*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Charles Bridge | attraction | yes | geoapify |
| 2 | Prague Castle | castle | yes | geoapify |
| 3 | Saint Vitus Cathedral | architecture | yes | geoapify |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Charles Bridge → Prague Castle | walk | 31 min | 1.9 km | — |
| Prague Castle → Saint Vitus Cathedral | walk | 0 min | 0.0 km | — |

Day travel in total: 32 min, 1.9 km (walking 32 min, vehicle 0 min).

Nearby food suggestions: Staroměstská restaurace; Bílý koníček.

*Day 2*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Národní galerie v Praze - Palác Kinských | museum | yes | geoapify_places |
| 2 | Central Gallery | museum | yes | geoapify_places |
| 3 | Sex Machines Museum | museum | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Národní galerie v Praze - Palác Kinských → Central Gallery | walk | 1 min | 0.1 km | — |
| Central Gallery → Sex Machines Museum | walk | 9 min | 0.6 km | — |

Day travel in total: 10 min, 0.7 km (walking 10 min, vehicle 0 min).

Nearby food suggestions: At the Bell; Einstein Bistrot.

*Day 3*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | zámek Zbraslav | castle | yes | geoapify_places |
| 2 | Muzeum zvonařství rodu Manoušků | museum | yes | geoapify_places |
| 3 | Letecké muzeum Točná | museum | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| zámek Zbraslav → Muzeum zvonařství rodu Manoušků | walk | 8 min | 0.6 km | — |
| Muzeum zvonařství rodu Manoušků → Letecké muzeum Točná | drive | 13 min | 10.0 km | walking the same leg: 82 min, 4.8 km |

Day travel in total: 21 min, 10.5 km (walking 8 min, vehicle 13 min).

Nearby food suggestions: Stromeček; U Lišků.

**Requested-interest coverage (as recorded)**

| Interest (canonical form) | Covered by the plan |
| --- | --- |
| history | yes |
| architecture | yes |
| art | yes |

**Readiness and findings (as recorded)**

- Readiness: `needs_review`
- Review codes: `WEATHER`
- Of those, other than data-coverage notes (weather / holidays not applied): none
- Blocking codes: none; critical issue categories: none
- Scheduled stops: 9 over 3 days (target for this pace T = 9, minimum R = 8); empty days: 0; duplicates: 0

**Recorded limitations of this run**

- Routing: 6 of 6 legs have a provider route (100.0%); legs with movement data the provider did not return: 0.
- Movement: legs by mode {'drive': 1, 'walk': 5}; vehicle alternatives tried for 1 leg(s), adopted for 1. The routing provider was run in walking mode with driving as the only alternative; no public transport is modelled.
- Inventory: 42 viable candidates of 60 found (status `healthy`; healthy from H = 21); search expansion used: False.
- Possible duplicate places among candidates: 11 suspicious pair(s), 11 unresolved, 0 of them scheduled.
- Category concentration: hard violations on days none; soft concentration on days none.

**Review prompt (a question for the reviewer, not a score):** Check on a map whether any day sits away from the historic centre the request implies, and whether those stops are ones a visitor would choose.

Day 3 is geographically separated from Prague's historic centre. Zbraslav Castle and the Manoušek bell-making attraction are in the Zbraslav area, while Točná Aviation Museum is farther away near Točná airfield, to the south of central Prague.

The itinerary reports only 21 minutes of travel between Day 3 attractions, including a 13-minute drive. However, it does not account for the journey from central Prague to Zbraslav or the return journey.

These are relatively niche destinations for a first-time visitor spending only three days in Prague. Zbraslav Castle's interiors are inaccessible, and Točná Aviation Museum does not have a scheduled public open day on October 18. Although the bell-making museum could interest someone specifically interested in craftsmanship, the user did not request such specialized activities.

Days 1 and 2 are much better geographically organized, staying within Prague Castle and the historic Old Town areas.

I assign Geographic and Day Coherence 2/5 because Day 3 is an unnecessary excursion away from the historic centre, with limited visitor value and accessibility problems. I would replace it with more relevant central Prague history, architecture, or art attractions.

**Scores — to be filled in by the human reviewer**

| # | Dimension | Question | Score (1–5) | Evidence / comment |
| --- | --- | --- | :---: | --- |
| 1 | Attraction usefulness | Are these places a visitor would be glad to have spent a slot on? | 2 | Charles Bridge, Prague Castle and St. Vitus Cathedral are excellent, but Kinský Palace is closed and several Day 3 selections have limited visitor accessibility or appeal. |
| 2 | Geographic and day coherence | Does each day hold together on a map and as an experience? | 2 | Days 1 and 2 are compact and well grouped, but Day 3 moves to Zbraslav and Točná, away from Prague's historic centre, without a compelling reason. |
| 3 | Personalization | Does the plan reflect the stated interests and must-visits? | 3 | Both must-visits and the stated interests are represented, but Day 3 focuses on niche bell-making and aviation attractions instead of stronger history, architecture and art experiences. |
| 4 | Pace realism | Could the day be done at the stated pace, travel included? | 2 | Walking distances are reasonable, but inaccessible attractions make the schedule impractical. Day 1 also requires careful timing around the cathedral's restricted hours. |
| 5 | Redundancy avoidance | Is every slot a distinct experience? | 3 | Prague Castle and St. Vitus Cathedral form one visitor complex, creating some overlap. Day 2 is also concentrated on museums, although most attractions are conceptually distinct. |
| 6 | Overall | Would I actually use this plan? | 2 | An excellent first day cannot compensate for the significant attraction-access problems on Days 2 and 3. Major edits are required. |

Scale: 1 unusable, 2 poor, 3 acceptable with edits, 4 good, 5 excellent.

Places I looked up and what I found (independent checks):

- **Charles Bridge:** An iconic historic pedestrian bridge across the Vltava River. It is an excellent match for the architecture and history interests and is correctly included as a must-visit.
- **Prague Castle:** The castle complex is normally accessible daily, with visitor attractions operating approximately 9 AM–5 PM in October. It is appropriately included on Day 1.
- **St. Vitus Cathedral:** Located within the Prague Castle complex. On Friday, October 16, 2026, tourist access is restricted to 12 PM–5 PM, with last entry at 4:40 PM. The visit requires appropriate scheduling.
- **National Gallery – Kinský Palace:** The National Gallery's official website confirms that Kinský Palace has been permanently closed as an exhibition venue since June 1, 2025. The scheduled Day 2 museum visit is therefore invalid.
- **Central Gallery:** Located at Old Town Square 15 and open daily from 10 AM–8 PM. It features exhibitions associated with Alfons Mucha, Salvador Dalí, and Andy Warhol. This is a suitable attraction for the requested art interest.
- **Sex Machines Museum:** A specialist adult-themed museum in Prague's Old Town. It is geographically convenient but may not match a general history, architecture, and art itinerary without an expressed interest in the subject.
- **Zbraslav Castle:** Located outside the central historic district in Prague-Zbraslav. The official website states that the castle interiors are closed to visitors, although its park is open and the nearby church has limited access.
- **Museum of the Manoušek Bell-Making Family:** A specialized bell-making heritage attraction associated with the Zbraslav area. Visitor opening arrangements for October 18 could not be conclusively verified.
- **Točná Aviation Museum:** Located outside central Prague. The museum's official calendar lists public hangar open days, with the next scheduled opening on November 14, 2026. A normal public museum visit on Sunday, October 18, is not established.

Other comments:

The itinerary successfully includes both requested must-visits, Charles Bridge and Prague Castle, and broadly reflects the user's interests in history, architecture, and art.

Day 1 is particularly strong, combining iconic landmarks within a geographically coherent historic area. However, St. Vitus Cathedral is part of Prague Castle, and the itinerary should account for its restricted visitor hours on October 16.

Day 2 is geographically compact, but Kinský Palace is permanently closed as a National Gallery exhibition venue. Central Gallery is a worthwhile art attraction, while the Sex Machines Museum is a niche experience that may not match the user's stated preferences.

Day 3 is the most problematic. All three attractions are outside Prague's main historic sightseeing area. Zbraslav Castle is not open for interior visits, and Točná Aviation Museum is not scheduled for a regular public opening on October 18. The travel totals also exclude the journey from and back to central Prague.

Although the plan reports complete routing coverage and no duplicates, these automated findings do not account for closed venues, questionable attraction choices, or incomplete daily transport requirements.

I would retain the central Prague sightseeing structure, replace the inaccessible museums, reconsider the niche attractions, and redesign Day 3 around more worthwhile historic and cultural destinations.

**Overall assessment: 2/5 — Poor. The itinerary contains strong must-visits but requires significant changes to attraction accessibility, geographic planning, and overall visitor usefulness.**

Scored by: Garima Prachi  Date: 10/14/2026  
Part B figures (Appendix A) read only after scoring: [ ]

---

### Q-H10 — Mumbai, India

Saved record: `scenarios/q_h10_mumbai_india.json` (run 2026-10-11T06:08:36Z).

**Request**

- Destination: Mumbai, India (resolved by the provider as "Mumbai, MH, India")
- Length and pace: 3 days, packed
- Interests: history, markets, food, promenade
- Must-visits: Gateway of India
- Travellers: 2; start date 2026-10-16
- Covers (the generic dimensions the scenario was chosen to exercise): large/sprawling city, dense urban transit context, waterfront/harbor, markets/food

**Must-visits**

| Requested | Grounded by the provider as | Scheduled |
| --- | --- | --- |
| Gateway of India | Gateway of India | yes |

**Itinerary as generated**

*Day 1*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Gateway of India | monument | yes | geoapify |
| 2 | Chhatrapati Shivaji Maharaj Vastu Sangrahalaya | museum | yes | geoapify |
| 3 | Chhatrapati Shivaji Maharaj Terminus | architecture | yes | geoapify |
| 4 | Imperial Mahal | building | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Gateway of India → Chhatrapati Shivaji Maharaj Vastu Sangrahalaya | walk | 13 min | 0.8 km | — |
| Chhatrapati Shivaji Maharaj Vastu Sangrahalaya → Chhatrapati Shivaji Maharaj Terminus | walk | 30 min | 1.9 km | — |
| Chhatrapati Shivaji Maharaj Terminus → Imperial Mahal | drive | 13 min | 9.5 km | walking the same leg: 138 min, 9.2 km |

Day travel in total: 56 min, 12.1 km (walking 43 min, vehicle 13 min).

Nearby food suggestions: Jyoti; Swagat Veg Treat.

*Day 2*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Chaitya Bhumi | monument | yes | geoapify_places |
| 2 | Samyukta Maharashtra Daalan | museum | yes | geoapify_places |
| 3 | Chhatrapati Shivaji Municipal Market | marketplace | yes | geoapify_places |
| 4 | Chimbai Beach | beach | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Chaitya Bhumi → Samyukta Maharashtra Daalan | walk | 10 min | 0.6 km | — |
| Samyukta Maharashtra Daalan → Chhatrapati Shivaji Municipal Market | walk | 20 min | 1.3 km | — |
| Chhatrapati Shivaji Municipal Market → Chimbai Beach | drive | 8 min | 6.6 km | walking the same leg: 83 min, 5.6 km |

Day travel in total: 38 min, 8.5 km (walking 30 min, vehicle 8 min).

Nearby food suggestions: Adarsha Family Restaurant; Trupti.

*Day 3*

| # | Place (name as the provider returned it) | Category | Provider identity | Source |
| ---: | --- | --- | --- | --- |
| 1 | Kala Qila | attraction | yes | geoapify_places |
| 2 | Nature Education Centre | museum | yes | geoapify_places |
| 3 | सायन किल्ला | attraction | yes | geoapify_places |
| 4 | BEST Museum | museum | yes | geoapify_places |

| Travel leg | Mode | Time | Distance | Note |
| --- | --- | ---: | ---: | --- |
| Kala Qila → Nature Education Centre | walk | 7 min | 0.4 km | — |
| Nature Education Centre → सायन किल्ला | drive | 8 min | 6.8 km | walking the same leg: 53 min, 3.4 km |
| सायन किल्ला → BEST Museum | drive | 5 min | 4.1 km | walking the same leg: 42 min, 2.8 km |

Day travel in total: 20 min, 11.3 km (walking 7 min, vehicle 13 min).

Nearby food suggestions: Apna Hotel; Butterfly High.

**Requested-interest coverage (as recorded)**

| Interest (canonical form) | Covered by the plan |
| --- | --- |
| history | yes |
| shopping | yes |
| food | yes |
| waterfront | yes |

How "food" is covered: scheduled places with recorded food evidence: none; otherwise by the nearby food suggestions listed per day.

**Readiness and findings (as recorded)**

- Readiness: `needs_review`
- Review codes: `GEOGRAPHIC_DISPERSION`, `WEATHER`
- Of those, other than data-coverage notes (weather / holidays not applied): `GEOGRAPHIC_DISPERSION`
- Blocking codes: none; critical issue categories: none
- Scheduled stops: 12 over 3 days (target for this pace T = 12, minimum R = 10); empty days: 0; duplicates: 0

**Recorded limitations of this run**

- Routing: 9 of 9 legs have a provider route (100.0%); legs with movement data the provider did not return: 0.
- Movement: legs by mode {'drive': 4, 'walk': 5}; vehicle alternatives tried for 4 leg(s), adopted for 4. The routing provider was run in walking mode with driving as the only alternative; no public transport is modelled.
- Inventory: 33 viable candidates of 56 found (status `healthy`; healthy from H = 27); search expansion used: True.
- Possible duplicate places among candidates: 11 suspicious pair(s), 11 unresolved, 0 of them scheduled.
- Category concentration: hard violations on days none; soft concentration on days none.

**Review prompt (a question for the reviewer, not a score):** The record carries a geographic-dispersion finding for day 1. Look at where that day's stops are on a map before scoring Geographic and day coherence.

The geographic-dispersion finding for Day 1 is justified.

The first three attractions—Gateway of India, Chhatrapati Shivaji Maharaj Vastu Sangrahalaya, and Chhatrapati Shivaji Maharaj Terminus—are located within South Mumbai. They form a sensible historic sightseeing route, with reported walking legs of 13 and 30 minutes.

However, the fourth attraction, Imperial Mahal, is located near Khodadad Circle in Dadar East, away from the South Mumbai sightseeing cluster. The itinerary adds a 9.5 km driving transfer, estimated at 13 minutes, solely to visit this relatively minor historic building.

The geographic separation is especially unnecessary because the user did not request Imperial Mahal and there are more relevant historical, market, and waterfront attractions closer to the first three stops.

The 13-minute driving estimate may also be optimistic given Mumbai's variable traffic conditions. Public transportation alternatives are not evaluated.

Day 2 similarly extends from Dadar to Chimbai Beach in Bandra, and Day 3 requires multiple transfers among specialized heritage sites.

I therefore assign Geographic and Day Coherence 2/5. The itinerary has some sensible local groupings but suffers from unnecessary cross-city travel and weak attraction prioritization.

**Scores — to be filled in by the human reviewer**

| # | Dimension | Question | Score (1–5) | Evidence / comment |
| --- | --- | --- | :---: | --- |
| 1 | Attraction usefulness | Are these places a visitor would be glad to have spent a slot on? | 2 | Gateway of India, CSMVS and CSMT are excellent attractions. However, Imperial Mahal, Chimbai Beach and some Day 3 stops have limited mainstream sightseeing value. |
| 2 | Geographic and day coherence | Does each day hold together on a map and as an experience? | 2 | Day 1 travels from South Mumbai to Dadar unnecessarily. Day 2 moves from Dadar to Bandra, while Day 3 requires multiple transfers between the Dharavi/Sion and Wadala areas. |
| 3 | Personalization | Does the plan reflect the stated interests and must-visits? | 3 | The must-visit and history interests are covered, with some market and waterfront stops. Food is mainly addressed through restaurant suggestions, and a proper promenade experience is missing. |
| 4 | Pace realism | Could the day be done at the stated pace, travel included? | 2 | Four attractions daily are appropriate for a packed pace, but driving estimates are optimistic in congested Mumbai, and museum visit durations and accessibility are not adequately considered. |
| 5 | Redundancy avoidance | Is every slot a distinct experience? | 3 | The stops are generally distinct, though Days 2 and 3 contain multiple niche heritage and museum experiences that offer limited variety. |
| 6 | Overall | Would I actually use this plan? | 2 | Several strong attractions are undermined by inefficient routing, lower-value stops, and insufficient attention to local transport and visitor experience. |

Scale: 1 unusable, 2 poor, 3 acceptable with edits, 4 good, 5 excellent.

Places I looked up and what I found (independent checks):

- **Gateway of India:** A major historic waterfront monument in Colaba, South Mumbai. It is an appropriate must-visit and strongly matches the user's history interest.
- **Chhatrapati Shivaji Maharaj Vastu Sangrahalaya (CSMVS):** Located near the Gateway of India. Official visiting hours are 10:15 AM–6:00 PM daily, with the ticket counter closing at 5:45 PM. A thorough visit can take 3–4 hours.
- **Chhatrapati Shivaji Maharaj Terminus:** A UNESCO World Heritage railway station and major architectural landmark in South Mumbai. Its inclusion on Day 1 is appropriate.
- **Imperial Mahal:** A historic building near Khodadad Circle in Dadar East. It is approximately 9.5 km from CSMT by the generated route and has limited standalone tourist value. Including it unnecessarily disperses Day 1.
- **Samyukta Maharashtra Smriti Dalan:** Located near Shivaji Park, Dadar West. The municipal website lists opening hours of 10:30 AM–5:30 PM, with Monday closure. Saturday is generally an appropriate visiting day.
- **Chimbai Beach:** Located in Bandra West, within a traditional fishing-community area. It is not a conventional tourist promenade. Environmental reports have documented litter and shoreline-management challenges, making it a weaker choice than Marine Drive or Bandstand.
- **Kala Qila (Riwa Fort):** Located near Dharavi and the Mithi River. The historic fort is in a dilapidated condition, with limited conventional visitor facilities.
- **Nature Education Centre:** Situated near Maharashtra Nature Park and Riwa Fort. Its inclusion is geographically reasonable, but its exact visitor opening arrangements for October 18 were not conclusively verified.
- **Sion Fort:** A historic hilltop fort near Sion. It is a distinct historical attraction, although the visit requires some uphill walking.
- **BEST Museum:** Located at Anik Bus Depot in Wadala. It documents Mumbai's public transportation history through vintage vehicles, tickets, photographs, and other memorabilia. Its existence was independently verified, but its exact Sunday access arrangements should be reconfirmed.

Other comments:

The itinerary successfully includes the requested Gateway of India and offers several valuable historical attractions, particularly CSMVS and Chhatrapati Shivaji Maharaj Terminus. It also includes local markets, museums, heritage sites, and waterfront locations.

However, the itinerary has significant geographic and attraction-selection weaknesses. Day 1 combines three well-grouped South Mumbai attractions with Imperial Mahal in Dadar East, introducing an unnecessary 9.5 km transfer for a relatively minor sightseeing stop.

Day 2 includes historically relevant attractions around Dadar but ends at Chimbai Beach in Bandra, which is less suitable for the requested promenade experience. A dedicated Marine Drive or Bandstand visit would provide a stronger waterfront experience.

Day 3 concentrates on relatively niche historical attractions in the Dharavi, Sion, and Wadala areas. Although these places are genuine, some have limited tourist facilities or require additional visitor-access verification. The choices are not particularly strong for a three-day Mumbai itinerary.

Food personalization is also insufficient. The plan includes restaurant suggestions and a market but does not schedule a dedicated Mumbai street-food experience, despite food being an explicitly stated interest.

The packed pace allows for four stops per day, but Mumbai's congestion, museum exploration time, transfers, and possible waiting times are not adequately addressed. The routing system also does not model public transportation.

I would keep the major South Mumbai landmarks together, replace lower-value stops, add a proper waterfront promenade, include a dedicated food experience, and improve public transport recommendations.

**Overall assessment: 2/5 — Poor. The itinerary includes important landmarks but requires significant improvements to geographic planning, attraction usefulness, transport realism, and personalization.**

Scored by: Garima Prachi  Date: 10/14/2026  
Part B figures (Appendix A) read only after scoring: [ ]

---

## 6. Comparison table

The left half is copied from the records. The six score columns are empty and are filled in from
section 5 by the reviewer (AU = Attraction usefulness, GC = Geographic and day coherence, PE =
Personalization, PR = Pace realism, RA = Redundancy avoidance, OV = Overall).

| Scenario | Destination | Days, pace | Stops | Must-visits scheduled | Requested interests not covered | Review codes other than weather / holidays | Baseline | AU | GC | PE | PR | RA | OV |
| --- | --- | --- | ---: | --- | --- | --- | --- | :---: | :---: | :---: | :---: | :---: | :---: |
| Q-H1 | Chicago, United States | 3, balanced | 9 | 2 of 2 | none | none | PASS | 3 | 2 | 3 | 2 | 2 | 2 |
| Q-H2 | Barcelona, Spain | 4, balanced | 12 | 1 of 2 | none | MUST_VISIT | FAIL | 3 | 4 | 2 | 2 | 3 | 2 |
| Q-H3 | Rome, Italy | 3, packed | 12 | 2 of 2 | none | none | PASS | 2 | 2 | 4 | 2 | 2 | 2 |
| Q-H4 | Singapore | 3, balanced | 9 | 1 of 1 | waterfront | none | PASS | 3 | 4 | 3 | 4 | 2 | 3 |
| Q-H5 | Bangkok, Thailand | 4, balanced | 12 | 1 of 1 | none | none | PASS | 2 | 3 | 3 | 2 | 2 | 2 |
| Q-H6 | Sydney, Australia | 3, relaxed | 6 | 1 of 1 | none | ROUTE_INCLUDES_FERRY | PASS | 3 | 3 | 4 | 4 | 4 | 3 |
| Q-H7 | Reykjavík, Iceland | 2, relaxed | 4 | none requested | none | none | PASS | 2 | 3 | 3 | 4 | 4 | 2 |
| Q-H8 | Rio de Janeiro, Brazil | 4, balanced | 11 | 2 of 2 | none | LONG_TRAVEL_DAY | FAIL | 3 | 2 | 3 | 1 | 4 | 2 |
| Q-H9 | Prague, Czechia | 3, balanced | 9 | 2 of 2 | none | none | PASS | 2 | 2 | 3 | 2 | 3 | 2 |
| Q-H10 | Mumbai, India | 3, packed | 12 | 1 of 1 | none | GEOGRAPHIC_DISPERSION | FAIL | 2 | 2 | 3 | 2 | 3 | 2 |

## 7. Acceptance calculation (to be completed by the reviewer)

The three criteria are the contract's release target (section 1). They are reported separately; the
contract forbids collapsing the parts into one PASS.

**Criterion 1 — median overall score ≥ 4 of 5**

1. The ten Overall scores, in scenario order (Q-H1 … Q-H10): `2 2 2 3 2 3 2 2 2 2`
2. The same ten, sorted from lowest to highest: `2 2 2 2 2 2 2 2 3 3`
3. Median = (5th value + 6th value) ÷ 2 = (`2` + `2`) ÷ 2 = `2.0`
4. Median ≥ 4? `No`

**Criterion 2 — no scenario with an overall score below 3**

1. Lowest Overall score: `2`, in scenario(s): `Q-H1, Q-H2, Q-H3, Q-H5, Q-H7, Q-H8, Q-H9, Q-H10`
2. Every Overall score ≥ 3? `No`

**Criterion 3 — zero hard factual-safety failures (part A)**

The criterion is quoted as the contract states it. What follows is the recorded evidence, kept in separate
lines because the contract's parts "are never collapsed into one PASS", and then the decision that is still
required. Details and citations: Appendix C.

*Evidence (from the saved records; none of it is a manual score):*

1. **Factual safety.** In all ten scenarios: zero fabricated or unverified scheduled identities, zero
   unsupported factual claims, zero travel legs with movement data the provider did not return.
2. **Part A, section 4.1** ("Existing requirements (unchanged and authoritative)"): every requirement
   passed in all ten scenarios.
3. **Part A, section 4.2, A1 and A2:** no violation recorded in any scenario.
4. **Part A, section 4.2, A3 (severe route burden): UNDETERMINED for Q-H8 Rio de Janeiro.** Long-travel
   days survived on days 2, 3 and 4 with viable inventory ≥ T. Alternatives were verified for day 3 only
   (no material improvement); for days 2 and 4 none was verified because the routing allowance was
   exhausted, and the record does not compute whether a valid lower-burden alternative existed. The other
   nine scenarios have no long-travel day. **It is therefore not claimed that the whole of Part A
   conclusively passed.**
5. **Official baseline hard correctness: 7 of 10 PASS; FAIL: Q-H2, Q-H8, Q-H10.** This result stands as
   recorded. The baseline is the canary's twenty acceptance checks, which is not the same list as Part A;
   the three failures are a review code outside the accepted list (Q-H2 `MUST_VISIT`, Q-H8
   `LONG_TRAVEL_DAY`, Q-H10 `GEOGRAPHIC_DISPERSION`) and, for Q-H8, excessive walking. They are **not**
   classified automatically as factual-safety failures.

*Decision required before release (by the contract's owner; not a score, and not made in this document):*

- How criterion 3 is judged given item 4, that is, how the undetermined A3 condition of Q-H8 is treated:
  `Pending contract-owner decision on Q-H8 A3`
- Criterion 3: `undetermined`   Decided by: ____________________  Date: ____________

**Result**

| Criterion | Met? |
| --- | :---: |
| 1. Median overall ≥ 4 | No |
| 2. No overall below 3 | No |
| 3. Zero hard factual-safety failures (part A) | Undetermined |

Release target met (all three)? `No`  Reviewer: Garima Prachi  Date: 10/14/2026

Whatever the outcome, the recorded holdout result stays as it is: 10 completed, 7 of 10 baseline hard
correctness PASS. A manual score never turns a baseline FAIL into a PASS.

## Appendix A. Part B figures — read only after scoring that scenario

Copied from each record's `itinerary_quality` block ("reported only; never an acceptance check"). Every
distance in the geography figures is a straight-line proxy, as the record itself states, never a route.

### Q-H1 — Chicago, United States

- Interests: covered ['architecture', 'museum', 'waterfront']; uncovered none (uncovered although viable candidates existed: none; uncovered with no viable candidate: none); viable supply by interest {'architecture': 1, 'museum': 18, 'waterfront': 6}; unused viable supply {'architecture': 0, 'museum': 13, 'waterfront': 5}
- Must-visits: grounded ['Art Institute of Chicago', 'Millennium Park']; ungrounded none; grounded but unscheduled none
- Geography (path of straight lines between consecutive stops, per day, km): {'1': 0.94, '2': 1.0, '3': 0.97}; boundary 8.0 km; days over it none; dispersed days none; stops closer to another day's stops: 0 (days none)
- Day geography bands: {'compact': 3}; areas in the candidate pool 15; days within one area 3; days with a compact alternative of equal usefulness none
- Route burden: longest single leg 14 min; longest walking leg 14 min; heaviest day's transfers 25 min; pace walking limit per day 90 min; long-travel days none; routing coverage 100.0%
- Route severity (verified provider legs only), days by severity: {'normal': 3}; largest route-to-straight-line ratio 2.68
- Pace fit: target 3 stops per day (T = 9, R = 8); stops per day [3, 3, 3]; days over the pace target none; days over the pace walking limit none
- Limited slots: scheduled by internal tier {'primary_anchor': 7, 'good_candidate': 2}; scheduled by usefulness-evidence band {'1': 3, '2': 4, '3': 2} (0–3 stored evidences; the record says this is not a ranking); unused viable candidates above the lowest scheduled tier 13; grounded anchors scheduled 3 of 5
- Not computed by the tool (future metrics): same_excursion_or_complex_redundancy, tourist_value_ranking_and_obscure_filler_share, recognisable_day_purpose, lower_burden_alternative_existed, first_and_last_day_fit

### Q-H2 — Barcelona, Spain

- Interests: covered ['architecture', 'food', 'shopping']; uncovered none (uncovered although viable candidates existed: none; uncovered with no viable candidate: none); viable supply by interest {'architecture': 7, 'food': 0, 'shopping': 5}; unused viable supply {'architecture': 3, 'food': 0, 'shopping': 2}
- Must-visits: grounded ['Park Güell']; ungrounded ['Sagrada Família']; grounded but unscheduled none
- Geography (path of straight lines between consecutive stops, per day, km): {'1': 0.66, '2': 0.94, '3': 0.63, '4': 0.9}; boundary 8.0 km; days over it none; dispersed days none; stops closer to another day's stops: 0 (days none)
- Day geography bands: {'compact': 4}; areas in the candidate pool 8; days within one area 4; days with a compact alternative of equal usefulness none
- Route burden: longest single leg 32 min; longest walking leg 32 min; heaviest day's transfers 34 min; pace walking limit per day 90 min; long-travel days none; routing coverage 100.0%
- Route severity (verified provider legs only), days by severity: {'normal': 4}; largest route-to-straight-line ratio 2.81
- Pace fit: target 3 stops per day (T = 12, R = 10); stops per day [3, 3, 3, 3]; days over the pace target none; days over the pace walking limit none
- Limited slots: scheduled by internal tier {'primary_anchor': 9, 'good_candidate': 3}; scheduled by usefulness-evidence band {'0': 3, '1': 3, '2': 5, '3': 1} (0–3 stored evidences; the record says this is not a ranking); unused viable candidates above the lowest scheduled tier 18; grounded anchors scheduled 3 of 8
- Not computed by the tool (future metrics): same_excursion_or_complex_redundancy, tourist_value_ranking_and_obscure_filler_share, recognisable_day_purpose, lower_burden_alternative_existed, first_and_last_day_fit

### Q-H3 — Rome, Italy

- Interests: covered ['art', 'history']; uncovered none (uncovered although viable candidates existed: none; uncovered with no viable candidate: none); viable supply by interest {'history': 44, 'art': 18}; unused viable supply {'history': 32, 'art': 16}
- Must-visits: grounded ['Colosseum', 'Pantheon']; ungrounded none; grounded but unscheduled none
- Geography (path of straight lines between consecutive stops, per day, km): {'1': 1.68, '2': 1.99, '3': 3.28}; boundary 8.0 km; days over it none; dispersed days none; stops closer to another day's stops: 0 (days none)
- Day geography bands: {'compact': 3}; areas in the candidate pool 14; days within one area 3; days with a compact alternative of equal usefulness none
- Route burden: longest single leg 22 min; longest walking leg 22 min; heaviest day's transfers 57 min; pace walking limit per day 120 min; long-travel days none; routing coverage 100.0%
- Route severity (verified provider legs only), days by severity: {'elevated': 1, 'normal': 2}; largest route-to-straight-line ratio 3.5
- Pace fit: target 4 stops per day (T = 12, R = 10); stops per day [4, 4, 4]; days over the pace target none; days over the pace walking limit none
- Limited slots: scheduled by internal tier {'primary_anchor': 12}; scheduled by usefulness-evidence band {'1': 2, '2': 5, '3': 5} (0–3 stored evidences; the record says this is not a ranking); unused viable candidates above the lowest scheduled tier 0; grounded anchors scheduled 5 of 6
- Not computed by the tool (future metrics): same_excursion_or_complex_redundancy, tourist_value_ranking_and_obscure_filler_share, recognisable_day_purpose, lower_burden_alternative_existed, first_and_last_day_fit

### Q-H4 — Singapore

- Interests: covered ['food', 'outdoors']; uncovered ['waterfront'] (uncovered although viable candidates existed: none; uncovered with no viable candidate: ['waterfront']); viable supply by interest {'outdoors': 8, 'food': 0, 'waterfront': 0}; unused viable supply {'outdoors': 2, 'food': 0, 'waterfront': 0}
- Must-visits: grounded ['Gardens by the Bay']; ungrounded none; grounded but unscheduled none
- Geography (path of straight lines between consecutive stops, per day, km): {'1': 1.62, '2': 1.57, '3': 1.01}; boundary 8.0 km; days over it none; dispersed days none; stops closer to another day's stops: 0 (days none)
- Day geography bands: {'compact': 3}; areas in the candidate pool 3; days within one area 2; days with a compact alternative of equal usefulness none
- Route burden: longest single leg 31 min; longest walking leg 31 min; heaviest day's transfers 44 min; pace walking limit per day 90 min; long-travel days none; routing coverage 100.0%
- Route severity (verified provider legs only), days by severity: {'normal': 3}; largest route-to-straight-line ratio 2.13
- Pace fit: target 3 stops per day (T = 9, R = 8); stops per day [3, 3, 3]; days over the pace target none; days over the pace walking limit none
- Limited slots: scheduled by internal tier {'primary_anchor': 4, 'good_candidate': 5}; scheduled by usefulness-evidence band {'1': 3, '2': 4, '3': 2} (0–3 stored evidences; the record says this is not a ranking); unused viable candidates above the lowest scheduled tier 10; grounded anchors scheduled 4 of 7
- Not computed by the tool (future metrics): same_excursion_or_complex_redundancy, tourist_value_ranking_and_obscure_filler_share, recognisable_day_purpose, lower_burden_alternative_existed, first_and_last_day_fit

### Q-H5 — Bangkok, Thailand

- Interests: covered ['food', 'history', 'shopping']; uncovered none (uncovered although viable candidates existed: none; uncovered with no viable candidate: none); viable supply by interest {'history': 24, 'shopping': 2, 'food': 0}; unused viable supply {'history': 14, 'shopping': 0, 'food': 0}
- Must-visits: grounded ['Grand Palace']; ungrounded none; grounded but unscheduled none
- Geography (path of straight lines between consecutive stops, per day, km): {'1': 1.21, '2': 1.17, '3': 7.94, '4': 4.88}; boundary 8.0 km; days over it none; dispersed days none; stops closer to another day's stops: 0 (days none)
- Day geography bands: {'compact': 2, 'extended': 2}; areas in the candidate pool 8; days within one area 2; days with a compact alternative of equal usefulness [4]
- Route burden: longest single leg 31 min; longest walking leg 31 min; heaviest day's transfers 40 min; pace walking limit per day 90 min; long-travel days none; routing coverage 100.0%
- Route severity (verified provider legs only), days by severity: {'elevated': 3, 'normal': 1}; largest route-to-straight-line ratio 5.85
- Pace fit: target 3 stops per day (T = 12, R = 10); stops per day [3, 3, 3, 3]; days over the pace target none; days over the pace walking limit none
- Limited slots: scheduled by internal tier {'primary_anchor': 10, 'good_candidate': 2}; scheduled by usefulness-evidence band {'1': 1, '2': 6, '3': 5} (0–3 stored evidences; the record says this is not a ranking); unused viable candidates above the lowest scheduled tier 12; grounded anchors scheduled 7 of 9
- Not computed by the tool (future metrics): same_excursion_or_complex_redundancy, tourist_value_ranking_and_obscure_filler_share, recognisable_day_purpose, lower_burden_alternative_existed, first_and_last_day_fit

### Q-H6 — Sydney, Australia

- Interests: covered ['museum', 'outdoors', 'waterfront']; uncovered none (uncovered although viable candidates existed: none; uncovered with no viable candidate: none); viable supply by interest {'waterfront': 6, 'outdoors': 24, 'museum': 13}; unused viable supply {'waterfront': 5, 'outdoors': 22, 'museum': 11}
- Must-visits: grounded ['Sydney Opera House']; ungrounded none; grounded but unscheduled none
- Geography (path of straight lines between consecutive stops, per day, km): {'1': 1.78, '2': 0.34, '3': 0.22}; boundary 8.0 km; days over it none; dispersed days none; stops closer to another day's stops: 0 (days none)
- Day geography bands: {'compact': 3}; areas in the candidate pool 22; days within one area 2; days with a compact alternative of equal usefulness none
- Route burden: longest single leg 9 min; longest walking leg 9 min; heaviest day's transfers 9 min; pace walking limit per day 60 min; long-travel days none; routing coverage 100.0%
- Route severity (verified provider legs only), days by severity: {'elevated': 1, 'normal': 2}; largest route-to-straight-line ratio 3.36
- Pace fit: target 2 stops per day (T = 6, R = 5); stops per day [2, 2, 2]; days over the pace target none; days over the pace walking limit none
- Limited slots: scheduled by internal tier {'primary_anchor': 4, 'good_candidate': 2}; scheduled by usefulness-evidence band {'1': 2, '2': 2, '3': 2} (0–3 stored evidences; the record says this is not a ranking); unused viable candidates above the lowest scheduled tier 20; grounded anchors scheduled 4 of 10
- Not computed by the tool (future metrics): same_excursion_or_complex_redundancy, tourist_value_ranking_and_obscure_filler_share, recognisable_day_purpose, lower_burden_alternative_existed, first_and_last_day_fit

### Q-H7 — Reykjavík, Iceland

- Interests: covered ['architecture', 'museum', 'waterfront']; uncovered none (uncovered although viable candidates existed: none; uncovered with no viable candidate: none); viable supply by interest {'architecture': 4, 'museum': 14, 'waterfront': 6}; unused viable supply {'architecture': 3, 'museum': 12, 'waterfront': 5}
- Must-visits: grounded none; ungrounded none; grounded but unscheduled none
- Geography (path of straight lines between consecutive stops, per day, km): {'1': 1.05, '2': 3.65}; boundary 8.0 km; days over it none; dispersed days none; stops closer to another day's stops: 1 (days [2])
- Day geography bands: {'compact': 1, 'extended': 1}; areas in the candidate pool 5; days within one area 1; days with a compact alternative of equal usefulness [2]
- Route burden: longest single leg 17 min; longest walking leg 17 min; heaviest day's transfers 17 min; pace walking limit per day 60 min; long-travel days none; routing coverage 100.0%
- Route severity (verified provider legs only), days by severity: {'elevated': 1, 'normal': 1}; largest route-to-straight-line ratio 1.29
- Pace fit: target 2 stops per day (T = 4, R = 4); stops per day [2, 2]; days over the pace target none; days over the pace walking limit none
- Limited slots: scheduled by internal tier {'primary_anchor': 3, 'good_candidate': 1}; scheduled by usefulness-evidence band {'1': 1, '3': 3} (0–3 stored evidences; the record says this is not a ranking); unused viable candidates above the lowest scheduled tier 14; grounded anchors scheduled 3 of 6
- Not computed by the tool (future metrics): same_excursion_or_complex_redundancy, tourist_value_ranking_and_obscure_filler_share, recognisable_day_purpose, lower_burden_alternative_existed, first_and_last_day_fit

### Q-H8 — Rio de Janeiro, Brazil

- Interests: covered ['food', 'history', 'outdoors']; uncovered none (uncovered although viable candidates existed: none; uncovered with no viable candidate: none); viable supply by interest {'outdoors': 13, 'history': 38, 'food': 0}; unused viable supply {'outdoors': 10, 'history': 28, 'food': 0}
- Must-visits: grounded ['Christ the Redeemer', 'Sugarloaf Mountain']; ungrounded none; grounded but unscheduled none
- Geography (path of straight lines between consecutive stops, per day, km): {'1': 4.37, '2': 2.87, '3': 2.49, '4': 2.75}; boundary 8.0 km; days over it none; dispersed days none; stops closer to another day's stops: 0 (days none)
- Day geography bands: {'compact': 3, 'extended': 1}; areas in the candidate pool 28; days within one area 1; days with a compact alternative of equal usefulness [1]
- Route burden: longest single leg 166 min; longest walking leg 166 min; heaviest day's transfers 201 min; pace walking limit per day 90 min; long-travel days [2, 3, 4]; routing coverage 100.0%
- Route severity (verified provider legs only), days by severity: {'elevated': 1, 'severe': 3}; largest route-to-straight-line ratio 7.4
- Pace fit: target 3 stops per day (T = 12, R = 10); stops per day [3, 3, 3, 2]; days over the pace target none; days over the pace walking limit [2, 3]
- Limited slots: scheduled by internal tier {'primary_anchor': 10, 'good_candidate': 1}; scheduled by usefulness-evidence band {'1': 2, '2': 5, '3': 4} (0–3 stored evidences; the record says this is not a ranking); unused viable candidates above the lowest scheduled tier 13; grounded anchors scheduled 4 of 6
- Not computed by the tool (future metrics): same_excursion_or_complex_redundancy, tourist_value_ranking_and_obscure_filler_share, recognisable_day_purpose, lower_burden_alternative_existed, first_and_last_day_fit

### Q-H9 — Prague, Czechia

- Interests: covered ['architecture', 'art', 'history']; uncovered none (uncovered although viable candidates existed: none; uncovered with no viable candidate: none); viable supply by interest {'history': 23, 'architecture': 5, 'art': 15}; unused viable supply {'history': 14, 'architecture': 1, 'art': 10}
- Must-visits: grounded ['Charles Bridge', 'Prague Castle']; ungrounded none; grounded but unscheduled none
- Geography (path of straight lines between consecutive stops, per day, km): {'1': 0.92, '2': 0.23, '3': 2.53}; boundary 8.0 km; days over it none; dispersed days none; stops closer to another day's stops: 0 (days none)
- Day geography bands: {'compact': 3}; areas in the candidate pool 9; days within one area 2; days with a compact alternative of equal usefulness none
- Route burden: longest single leg 31 min; longest walking leg 31 min; heaviest day's transfers 32 min; pace walking limit per day 90 min; long-travel days none; routing coverage 100.0%
- Route severity (verified provider legs only), days by severity: {'elevated': 1, 'normal': 2}; largest route-to-straight-line ratio 4.23
- Pace fit: target 3 stops per day (T = 9, R = 8); stops per day [3, 3, 3]; days over the pace target none; days over the pace walking limit none
- Limited slots: scheduled by internal tier {'primary_anchor': 9}; scheduled by usefulness-evidence band {'1': 3, '2': 2, '3': 4} (0–3 stored evidences; the record says this is not a ranking); unused viable candidates above the lowest scheduled tier 0; grounded anchors scheduled 4 of 5
- Not computed by the tool (future metrics): same_excursion_or_complex_redundancy, tourist_value_ranking_and_obscure_filler_share, recognisable_day_purpose, lower_burden_alternative_existed, first_and_last_day_fit

### Q-H10 — Mumbai, India

- Interests: covered ['food', 'history', 'shopping', 'waterfront']; uncovered none (uncovered although viable candidates existed: none; uncovered with no viable candidate: none); viable supply by interest {'history': 22, 'shopping': 3, 'food': 0, 'waterfront': 1}; unused viable supply {'history': 12, 'shopping': 2, 'food': 0, 'waterfront': 0}
- Must-visits: grounded ['Gateway of India']; ungrounded none; grounded but unscheduled none
- Geography (path of straight lines between consecutive stops, per day, km): {'1': 10.76, '2': 5.98, '3': 2.21}; boundary 8.0 km; days over it [1]; dispersed days [{'day': 1, 'spread_km': 10.76, 'cause': 'discretionary_dispersion'}]; stops closer to another day's stops: 1 (days [1])
- Day geography bands: {'compact': 1, 'dispersed': 1, 'extended': 1}; areas in the candidate pool 9; days within one area 0; days with a compact alternative of equal usefulness [2]
- Route burden: longest single leg 30 min; longest walking leg 30 min; heaviest day's transfers 56 min; pace walking limit per day 120 min; long-travel days none; routing coverage 100.0%
- Route severity (verified provider legs only), days by severity: {'elevated': 3}; largest route-to-straight-line ratio 8.13
- Pace fit: target 4 stops per day (T = 12, R = 10); stops per day [4, 4, 4]; days over the pace target none; days over the pace walking limit none
- Limited slots: scheduled by internal tier {'primary_anchor': 11, 'good_candidate': 1}; scheduled by usefulness-evidence band {'1': 6, '2': 3, '3': 3} (0–3 stored evidences; the record says this is not a ranking); unused viable candidates above the lowest scheduled tier 7; grounded anchors scheduled 3 of 6
- Not computed by the tool (future metrics): same_excursion_or_complex_redundancy, tourist_value_ranking_and_obscure_filler_share, recognisable_day_purpose, lower_burden_alternative_existed, first_and_last_day_fit

## Appendix B. Provisional observations — not scores; read after scoring

Written by the assistant that prepared this document, from the saved records only. They are **not human
rubric scores and must not be copied into a score cell**. They are placed last so that they do not anchor
the reviewer, in the spirit of the contract's rule about Part B figures. Nothing below was checked against
a map, an official source or the destination itself; where a statement goes beyond what the record says, it
is listed under "needs independent checking" and is a question, not a finding.

### B.1 Documented provider and routing limitations (stated by the records themselves)

- **No visit times.** Every day of every plan carries the stored note that timing, opening hours and
  durations are not implemented. No plan can be checked for whether a day "fits" in clock time.
- **Walking or driving only.** Legs are routed on foot, with driving as the one alternative. No public
  transport, cable car, funicular or rack railway is modelled anywhere, which matters most where the
  terrain is steep (Q-H8) or the city is large (Q-H5, Q-H10).
- **Q-H8 Rio de Janeiro.** Three legs stayed as walking with the note that a vehicle alternative was tried
  and the outcome was `allowance_exhausted`: 80 min (day 2), 166 min (day 3, the leg ending at Christ the
  Redeemer) and 52 min (day 4). The route-burden repair was attempted for day 3 (`no_material_improvement`)
  and not attempted for days 2 and 4 (`route_budget_exhausted`). The checker flagged excessive walking on
  days 2, 3 and 4. These are the recorded reasons for the baseline FAIL. The fallback planning pass was used.
- **Q-H2 Barcelona.** Sagrada Família is recorded as not grounded ("unresolved"), so it could not be
  scheduled; the plan's review code `MUST_VISIT` is the recorded reason for the baseline FAIL. The stored
  narrative for this run came from the deterministic fallback (the AI narration attempt failed).
- **Q-H10 Mumbai.** Day 1 carries a geographic-dispersion finding (recorded cause:
  `discretionary_dispersion`), and its last leg is a 9.5 km drive. The review code `GEOGRAPHIC_DISPERSION`
  is the recorded reason for the baseline FAIL. Search expansion was used for this destination.
- **Q-H4 Singapore.** `waterfront` is recorded as not covered, and as having no viable candidate in the
  pool that the system classified as serving it. The fallback planning pass was used.
- **Q-H6 Sydney.** The provider identified one leg on day 3 as including a ferry. No timetable, fare or
  operator is recorded, by design. Day 1 has no nearby food suggestion (none was found near its stops).
- **Q-H5 Bangkok.** Day 3 has no nearby food suggestion, on a trip whose interests include food.
- **Food.** Where "food" was requested, the records show it covered by nearby restaurant suggestions; the
  per-scenario line "scheduled places with recorded food evidence" says whether any scheduled stop itself
  counts.
- **Category labels come from the provider as they are.** For example the category of "Chicago Stock
  Exchange Trading Room" (Q-H1) is recorded as `yes`. A category is not a statement of what a visitor
  will find there.

### B.2 Likely usefulness questions (the assistant's impression from names and categories alone)

These are the places a reviewer may want to look at first. They are impressions from a list of names, not
judgements of the places.

- **Q-H1 Chicago, day 2:** a stock-exchange trading room, a military museum and library, and a money
  museum. Is that the day a visitor interested in architecture, museums and the riverfront would choose?
  The record lists "Chicago Riverwalk" among the grounded anchors, and it is not among the scheduled stops.
- **Q-H2 Barcelona, days 1 and 4:** day 1 pairs Park Güell with a small museum and a neighbouring park;
  day 4 is a market, a gate monument and a small square. With Sagrada Família absent, does the plan still
  read as an architecture trip?
- **Q-H3 Rome, day 2:** four archaeological sites. See B.3 for where they may be.
- **Q-H5 Bangkok, days 2 and 4:** six places recorded with Thai-script names only. A reviewer who does not
  read Thai cannot tell from the plan what they are, which is itself a usability observation.
- **Q-H6 Sydney and Q-H7 Reykjavík:** two stops a day at a relaxed pace (6 and 4 stops in all). Is that
  thin, or right for "relaxed"? In Reykjavík the target for the trip is recorded as T = 4.
- **Q-H8 Rio de Janeiro, day 1:** a penitentiary museum, a cultural institute and a visitors' centre,
  reached by two drives. Day 4: a parade square and one museum.
- **Q-H9 Prague, day 2:** a national-gallery palace, a "Central Gallery" and a "Sex Machines Museum", for
  interests of history, architecture and art. Day 3: a château, a bell-founding museum and an aviation
  museum.
- **Q-H10 Mumbai, days 2 and 3:** stops such as a memorial, a municipal market, a nature education centre
  and a transport company's museum.

### B.3 Facts that need independent human checking (questions, not findings)

The records hold no coordinates, so none of these can be settled from the saved data.

- **Q-H3 Rome, day 2.** The names "Area Archeologica di Piano della Comunità", "Area Archeologica di
  Campetti - Decumano", "Santuario dell'Apollo loc. Portonaccio" and "Tomba delle Anatre" read like sites of
  one archaeological area outside the historic centre. Where are they, how far from the centre, and how
  would a visitor get there and back on a packed three-day trip? The legs *between* them are short walks
  (11 to 19 minutes); the plan records no leg from the city to the first of them, because legs connect
  only the stops of one day.
- **Q-H9 Prague, day 3.** "zámek Zbraslav" and "Letecké muzeum Točná" carry the names of outlying
  districts; the recorded leg between the second and third stops is a 10.0 km drive. Same questions.
- **Q-H2 Barcelona, day 4.** "Mercat de Sarrià" and "Porta de Sarrià": is this an area a first-time
  visitor would give a day to?
- **Q-H6 Sydney, day 1.** "Hayes Street Beach" is reached from the Opera House by a recorded 6.0 km,
  8-minute drive. Where is it, and is driving how a visitor would make that trip? Day 3: the leg the
  provider flagged as including a ferry is recorded as a 3-minute, 0.3 km walk between Darling Harbour and
  the maritime museum; does a ferry plausibly belong on that leg?
- **Q-H7 Reykjavík, day 2.** "Skarfaklettur" (category beach), 4.7 km by car from the national gallery:
  what is it, and does it serve the "harbor" interest the record marks as covered?
- **Q-H1 Chicago.** The record marks `riverfront` (as "waterfront") covered. Which scheduled stop covers
  it: is it the beach on day 3 and not the river?
- **Q-H10 Mumbai.** The record marks `promenade` (as "waterfront") covered. Which stop covers it, and is
  it a promenade? Where is "Imperial Mahal" relative to the rest of day 1?
- **Possible parts of one place scheduled as separate stops** (the contract lists sub-feature redundancy as
  a known V1 limitation; whether each pair is one visit or two is for the reviewer): Prague Castle and Saint
  Vitus Cathedral (Q-H9 day 1; the recorded leg between them is 0 minutes); Grand Palace and Wat Phra Kaew
  (Q-H5 day 1; 5 minutes); Park Guell and Casa del Guarda (Q-H2 day 1; 3 minutes); Roman Forum on day 1 and
  Tempio di Vespasiano e Tito on day 3 (Q-H3).
- **Current status of every place.** Whether each place exists today, is open to visitors, needs a ticket
  or booking, and is accessible is not in the records and was not checked.

## Appendix C. Automated verdicts and their interpretation — read after scoring

This appendix holds what the tooling concluded about the ten plans and how those conclusions relate to the
contract's Part A and to the third release criterion. It was moved here, unchanged in substance, so that
it is read after the manual scores and not before. Nothing in it is a manual score.

**Recorded position (decision of the contract's owner, 2026-10-13).** It changes no wording and no
threshold of the contract.

- The official baseline hard-correctness result stands as recorded: **7 of 10 PASS**; FAIL: Q-H2, Q-H8,
  Q-H10.
- All ten scenarios passed the existing authoritative requirements of Part A **section 4.1**, including the
  factual-safety invariants.
- It is **not** claimed that the whole of Part A conclusively passed.
- Rio de Janeiro's (Q-H8) A3 condition is **UNDETERMINED** on the saved evidence.
- The three baseline failures are **not** classified automatically as factual-safety failures.

### C.1 What the baseline verdict is made of

Each record carries the list of checks behind its baseline PASS or FAIL. For orientation, these are the
checks that failed, exactly as recorded:

| Scenario | Baseline | Failed checks (verbatim) | Fabricated or unverified scheduled identities | Unsupported factual claims |
| --- | --- | --- | ---: | ---: |
| Q-H1 Chicago, United States | PASS | — | 0 | 0 |
| Q-H2 Barcelona, Spain | FAIL | final readiness is ready, or needs_review only for accepted data-coverage codes (unaccepted: MUST_VISIT) | 0 | 0 |
| Q-H3 Rome, Italy | PASS | — | 0 | 0 |
| Q-H4 Singapore | PASS | — | 0 | 0 |
| Q-H5 Bangkok, Thailand | PASS | — | 0 | 0 |
| Q-H6 Sydney, Australia | PASS | — | 0 | 0 |
| Q-H7 Reykjavík, Iceland | PASS | — | 0 | 0 |
| Q-H8 Rio de Janeiro, Brazil | FAIL | final readiness is ready, or needs_review only for accepted data-coverage codes (unaccepted: LONG_TRAVEL_DAY); no day with unresolved excessive walking | 0 | 0 |
| Q-H9 Prague, Czechia | PASS | — | 0 | 0 |
| Q-H10 Mumbai, India | FAIL | final readiness is ready, or needs_review only for accepted data-coverage codes (unaccepted: GEOGRAPHIC_DISPERSION) | 0 | 0 |

### C.2 Baseline verdict of each scenario, as recorded

Moved here from the ten scenario sections, unchanged, so that a plan's automated verdict is read after it
has been scored.

**Q-H1 — Chicago, United States**

- Outcome: **PASS**
- Factual safety: fabricated or unverified scheduled identities: 0; unsupported factual claims in the stored narrative: 0; narrative source: ai (AI attempt: success)

**Q-H2 — Barcelona, Spain**

- Outcome: **FAIL**
- Failed check: final readiness is ready, or needs_review only for accepted data-coverage codes (unaccepted: MUST_VISIT)
- Factual safety: fabricated or unverified scheduled identities: 0; unsupported factual claims in the stored narrative: 0; narrative source: deterministic_fallback (AI attempt: failed)

**Q-H3 — Rome, Italy**

- Outcome: **PASS**
- Factual safety: fabricated or unverified scheduled identities: 0; unsupported factual claims in the stored narrative: 0; narrative source: ai (AI attempt: success)

**Q-H4 — Singapore**

- Outcome: **PASS**
- Factual safety: fabricated or unverified scheduled identities: 0; unsupported factual claims in the stored narrative: 0; narrative source: ai (AI attempt: success)

**Q-H5 — Bangkok, Thailand**

- Outcome: **PASS**
- Factual safety: fabricated or unverified scheduled identities: 0; unsupported factual claims in the stored narrative: 0; narrative source: ai (AI attempt: success)

**Q-H6 — Sydney, Australia**

- Outcome: **PASS**
- Factual safety: fabricated or unverified scheduled identities: 0; unsupported factual claims in the stored narrative: 0; narrative source: ai (AI attempt: success)

**Q-H7 — Reykjavík, Iceland**

- Outcome: **PASS**
- Factual safety: fabricated or unverified scheduled identities: 0; unsupported factual claims in the stored narrative: 0; narrative source: ai (AI attempt: success)

**Q-H8 — Rio de Janeiro, Brazil**

- Outcome: **FAIL**
- Failed check: final readiness is ready, or needs_review only for accepted data-coverage codes (unaccepted: LONG_TRAVEL_DAY)
- Failed check: no day with unresolved excessive walking
- Factual safety: fabricated or unverified scheduled identities: 0; unsupported factual claims in the stored narrative: 0; narrative source: ai (AI attempt: success)

**Q-H9 — Prague, Czechia**

- Outcome: **PASS**
- Factual safety: fabricated or unverified scheduled identities: 0; unsupported factual claims in the stored narrative: 0; narrative source: ai (AI attempt: success)

**Q-H10 — Mumbai, India**

- Outcome: **FAIL**
- Failed check: final readiness is ready, or needs_review only for accepted data-coverage codes (unaccepted: GEOGRAPHIC_DISPERSION)
- Factual safety: fabricated or unverified scheduled identities: 0; unsupported factual claims in the stored narrative: 0; narrative source: ai (AI attempt: success)


### C.3 Part A, the baseline verdict and criterion 3: what the definitions say

A clarification added after the draft was reviewed. It quotes the contract
(`docs/24_itinerary_quality_contract.md`) and the evaluation code (`backend/scripts/canary_city.py`,
`backend/scripts/benchmark_quality.py`) and changes neither. Sections 3, 4 and 6 of the contract are
word for word what they were at the commit the holdout ran on, and the two evaluation scripts have not
changed since that commit.

#### What Part A is, in the contract's words

Part A is section 4, titled "Part A — hard correctness / safety". It has two halves.

**Section 4.1, "Existing requirements (unchanged and authoritative)":**

1. The destination resolves correctly.
2. Zero fabricated scheduled identities; only provider-grounded places are scheduled.
3. Zero unsupported factual claims.
4. Zero duplicate scheduled provider identities.
5. Persistence and reload succeed.
6. The provider credit cap is respected.
7. When viable inventory ≥ R: at least R meaningful stops and no empty day.
8. Routing coverage ≥ the existing release threshold (90%).
9. The no-fabrication and provider-truth invariants of `23_v1_release_reference.md` section 7 remain
   authoritative.

**Section 4.2, "New generic correctness requirements":** A1 (must-visit identity), A2 (requested-interest
normalization) and A3 (severe route burden). The section opens: "A1 and A2 were implemented in Q1; A3 is
still open."

The contract has **no separate definition of "hard factual safety"**. It uses the words this way:

- Section 3: "Part A always wins. A plan with a hard failure is a failed plan whatever parts B and C say."
- Section 6, rule 2: "It never overrides a factual-safety failure. A plan that fails part A is failed,
  whatever its scores." (the two sentences treat a factual-safety failure and failing Part A as one thing)
- Section 6, release target: "zero hard factual-safety failures (part A)."
- Section 8: "`baseline_hard_correctness` is not the whole of part A. It is the pre-Q1 canary acceptance
  baseline: the existing factual-safety and infrastructure checks of section 4.1. A1–A3 (section 4.2) are
  not acceptance checks during Q0. They are reported by `itinerary_quality` and become enforceable after
  their Q1 implementation."

So, by the contract's own usage, "hard factual-safety failures (part A)" means failures of Part A, and
Part A is the nine requirements of 4.1 plus A1–A3.

#### What the baseline verdict is, in the evaluation code

`baseline_hard_correctness` is the outcome of the canary's acceptance function (`_acceptance`, acceptance
policy version 2), which runs twenty checks. It is PASS only if all of them pass. The table shows each
check's recorded result and whether the contract lists it under Part A 4.1.

| # | Check (as named in the record) | Part A 4.1 item | H1 | H2 | H3 | H4 | H5 | H6 | H7 | H8 | H9 | H10 |
| ---: | --- | --- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 1 | destination geocoded | 1 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 2 | no fabricated/unverified scheduled identity | 2, 9 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 3 | no unsupported factual claim | 3, 9 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 4 | zero duplicates | 4 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 5 | no unresolved suspected duplicate scheduled | 4 (the code's stricter form) | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 6 | persistence and reload | 5 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 7 | provider budget not exceeded | 6 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 8 | anchor proposal completed | not listed | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 9 | final readiness is ready, or needs_review only for accepted data-coverage codes | **not listed** | ok | **FAIL** | ok | ok | ok | ok | ok | **FAIL** | ok | **FAIL** |
| 10 | no day with unresolved excessive walking | **not listed** | ok | ok | ok | ok | ok | ok | ok | **FAIL** | ok | ok |
| 11 | no day with an unreasonable transfer burden | not listed | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 12 | no movement data the routing provider did not return | 9 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 13 | no unresolved hard diversity violation while alternatives existed | not listed | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 14 | no restaurant repeated across days | not listed | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 15 | no food suggestion beyond the nearby radius | not listed | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 16 | no stale rationale | not listed | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 17 | ≥ R meaningful scheduled attractions | 7 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 18 | zero empty days | 7 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 19 | every grounded must-visit scheduled | not listed in 4.1 (related to A1) | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| 20 | routing coverage ≥ 90% | 8 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |

The "Part A 4.1 item" column is this document's reading of which contract bullet a check corresponds to;
the check names and results are the record's.

Check 9 fails when a plan carries a review code outside the accepted list. The accepted list in the code is
the "standing data-coverage limits of V1" (`ACCOMMODATION_INVENTORY`, `FLIGHT_INVENTORY`, `HOTEL_RATINGS`,
`WEATHER`, `HOLIDAYS`, `BUDGET`, `PROVIDER_COVERAGE`), plus `ROUTE_INCLUDES_FERRY` under its policy-2
conditions (accepted for Q-H6). The unaccepted codes were `MUST_VISIT` (Q-H2), `LONG_TRAVEL_DAY` (Q-H8)
and `GEOGRAPHIC_DISPERSION` (Q-H10).

**The baseline verdict and Part A are therefore not identical, in two directions.** The baseline includes
checks the contract does not list under Part A (8 to 11, 13 to 16 and 19), and it does not include A1–A3,
which the contract does. Note also a difference between the contract's description and the code: section 8
describes the baseline as "the existing factual-safety and infrastructure checks of section 4.1", and the
code's baseline checks more than section 4.1 lists. The code is the stricter of the two.

#### Factual safety and Part A, scenario by scenario

| Scenario | Factual safety (4.1 items 2, 3, 9: checks 2, 3, 12) | All of 4.1 (checks 1–7, 12, 17, 18, 20) | A1: a must-visit term protecting more than one place | A2: interest terms not recognised | A3: long-travel days surviving with viable ≥ T | Baseline (all 20) |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| Q-H1 Chicago | pass | pass | none | none | none | PASS |
| Q-H2 Barcelona | pass | pass | none | none | none | **FAIL** (check 9) |
| Q-H3 Rome | pass | pass | none | none | none | PASS |
| Q-H4 Singapore | pass | pass | none | none | none | PASS |
| Q-H5 Bangkok | pass | pass | none | none | none | PASS |
| Q-H6 Sydney | pass | pass | none | none | none | PASS |
| Q-H7 Reykjavík | pass | pass | none | none | none | PASS |
| Q-H8 Rio de Janeiro | pass | pass | none | none | **days 2, 3, 4** | **FAIL** (checks 9, 10) |
| Q-H9 Prague | pass | pass | none | none | none | PASS |
| Q-H10 Mumbai | pass | pass | none | none | none | **FAIL** (check 9) |

In all ten: zero fabricated or unverified scheduled identities, zero unsupported factual claims, zero
legs with movement data the provider did not return.

#### The three baseline failures against the Part A text

- **Q-H2 Barcelona, the missed must-visit.** Not a 4.1 item: 4.1 does not require every *requested*
  must-visit to be scheduled, and the code's own check is "every **grounded** must-visit scheduled", which
  passed. Under A1 the contract says "an ungroundable term stays ungrounded"; the record shows Sagrada
  Família ungrounded and no term holding more than one slot, which is A1 behaving as specified. The plan
  failed the baseline because it carries the review code `MUST_VISIT`, which the acceptance list does not
  accept. **By the contract's text this is a baseline acceptance failure, not a Part A failure.** It
  remains a real shortcoming of the plan, to be weighed under Personalization and Overall.
- **Q-H10 Mumbai, geographic dispersion.** `GEOGRAPHIC_DISPERSION` is a validator finding introduced in
  the quality tuning corrections (contract section 14). It is not a 4.1 item, and A3 is specifically "the
  existing long-travel condition … reported as `LONG_TRAVEL_DAY`", which this plan does not have. **By the
  contract's text this is a baseline acceptance failure, not a Part A failure.** It bears on Geographic
  and day coherence.
- **Q-H8 Rio de Janeiro, travel burden.** Not a 4.1 item. But A3 is about exactly this: "When viable ≥ T
  … a severe route-burden finding must lead to recomposition or replacement before finalization. It may
  not survive only as a warning when a valid lower-burden alternative exists. If no valid replacement
  exists, the warning is honest and acceptable." The record shows viable ≥ T, and `LONG_TRAVEL_DAY`
  surviving on days 2, 3 and 4. For day 3 a recomposition was attempted: two candidate moves were verified
  with the routing provider and gave no material improvement (a third was skipped for budget). For days 2
  and 4, three candidate moves each were shortlisted and **none was verified**, because the generation's
  routing allowance was exhausted. Whether "a valid lower-burden alternative exist[ed]" is therefore
  **not known**: the record lists `lower_burden_alternative_existed` among the figures it does not
  compute. **Whether Q-H8 violates A3 cannot be decided from the saved record**, and it may not be
  investigated by rerunning the scenario.

#### Criterion 3 under the readings the text allows

| Reading of "zero hard factual-safety failures (part A)" | Basis in the text | Result on this holdout |
| --- | --- | --- |
| **(a) Part A as section 4.1** — the "existing factual-safety and infrastructure checks", which is what section 8 says the baseline was meant to capture | Sections 4.1 and 8 | **Met: zero failures in ten scenarios.** This holds independently of the 7 of 10 baseline result |
| **(b) Part A as the whole of section 4** — 4.1 plus A1, A2 and A3 | Section 4's heading; section 6 rule 2 ("a plan that fails part A") | A1 and A2: no violation recorded. A3: nine scenarios have no long-travel day; **Q-H8 is undetermined** (above). So: met for nine, **open for one** |
| **(c) Any baseline hard-correctness FAIL counts** — because the run names its verdict "hard correctness" and section 3 says "a plan with a hard failure is a failed plan" | Section 3; the name of the recorded field | **Not met: three failures** (Q-H2, Q-H8, Q-H10) |

What can be said without choosing: reading (c) is not what the contract's section 8 says the baseline is
("not the whole of part A", and described as the 4.1 checks), and the contract nowhere states that a
review code outside the accepted list is a Part A failure. Reading (a) is the narrowest and is fully
supported by the record. Reading (b) is what the heading of section 4 says Part A is, and it leaves A3 for
Q-H8 unanswered. There is also an unresolved point about A3 itself: section 4.2 calls it "still open",
section 8 says A1–A3 "become enforceable after their Q1 implementation" although A3 was not part of Q1,
and section 13 says Q4 "addresses A3" without stating that it became an acceptance check; no check named
A3 exists in the acceptance function.

**Two things do not depend on the reading.** First, the recorded result is unchanged: 7 of 10 baseline hard
correctness PASS, and the three failing plans carry material findings a traveller must be told about (they
are: the plans are `needs_review` with those codes). Second, manual scores can lower a plan's standing but
never repair it: a score does not turn a baseline FAIL into a PASS under any reading.
