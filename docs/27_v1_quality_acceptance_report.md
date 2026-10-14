# 27. V1 Quality Acceptance Report

Status: final record of the human (Part C) review of the frozen quality holdout, 2026-10-14.
Sources: `docs/26_quality_holdout_manual_review.md` (signed scores and reviewer evidence),
`docs/24_itinerary_quality_contract.md` (the contract, unchanged), and the frozen run
`benchmark_results/quality_holdout_v1/` (read-only, unchanged).

This report is documentation and analysis only. No itinerary was generated, no provider was called, no
threshold was changed, no score was revised and nothing was implemented.

## 1. Executive summary

**The original V1 quality release target is NOT MET.**

The contract (`docs/24_itinerary_quality_contract.md`, section 6) sets this target, on the final unseen
quality holdout:

| Criterion (verbatim) | Result on the holdout | Met? |
| --- | --- | :---: |
| median overall score ≥ 4 of 5 | median Overall = **2.0** | **No** |
| no scenario with an overall score below 3 | **8 of 10** scenarios have Overall 2; minimum = **2** | **No** |
| zero hard factual-safety failures (part A) | Section 4.1 met in all ten; A1/A2 no violation; **A3 undetermined for Q-H8** | Undetermined |

Criteria 1 and 2 fail by a wide margin, so the target is not met whatever is decided about criterion 3.
No reading of the contract and no decision on Q-H8's A3 condition can change that.

- **Overall scores:** two scenarios scored 3 ("acceptable with edits": Singapore, Sydney); eight scored 2
  ("poor"). None scored 4 or 5.
- **Scope and provenance.** Ten scenarios of the frozen unseen quality holdout (contract section 7.2,
  frozen 2026-10-08), run once on 2026-10-11 at commit `9f6277b6a56cf4953ac4c4ed8bb8ab77dabc49cd` (clean
  tree, source fingerprint `2694908b…884ae5`, configuration hash `3ba911b4…727378`), trip start date
  2026-10-16. 10 of 10 completed and the run is recorded as valid. One human reviewer (Garima Prachi)
  scored all ten plans on 10/14/2026, using the contract's rubric unchanged and whole-number scores.
- **The manual review does not change any historical benchmark result.** The recorded holdout result stands
  as it is: 10 completed, **7 of 10 baseline hard correctness PASS** (FAIL: Barcelona, Rio de Janeiro,
  Mumbai). A manual score never turns a baseline FAIL into a PASS. A baseline PASS also does not mean a
  plan is good: five of the seven baseline PASS plans scored Overall 2.
- **Main reasons the plans scored poorly** (as reported in the reviewer's evidence and not independently
  re-checked here; section 6): closed, relocated or
  not publicly accessible venues scheduled as ordinary stops; low-value or obscure stops in limited slots;
  unrealistic travel (a 166-minute uphill walk, driving where a ferry or train is the normal way, and day
  trips out of the centre with no journey there and back); parts of one venue scheduled as separate stops;
  and weak handling of food and waterfront interests.

## 2. Status of the manual review record

These checks were made on `docs/26_quality_holdout_manual_review.md`, reading the signed scenario tables as
the authoritative source.

| Check | Result |
| --- | --- |
| Six human scores in every scenario | Yes, 60 of 60 cells filled |
| Every score is a whole number from 1 to 5 | Yes (values used: 1, 2, 3, 4) |
| Every score has supporting evidence | Yes, a non-empty evidence sentence beside each of the 60 scores, plus per-scenario lookups and comments |
| Reviewer and date on every scenario | Yes: Garima Prachi, 10/14/2026, on all ten |
| Rubric unchanged | Yes. The dimensions, questions, scale, rules and release target quoted in doc 26 section 1 match contract section 6 word for word, and `docs/24_itinerary_quality_contract.md` has no uncommitted change |
| Section 6 comparison table matches the signed tables | Yes, all 60 cells |
| Section 7 arithmetic | Correct (section 3 below) |
| Frozen holdout untouched | Yes: all 21 files have the same SHA-256 as before this work |

**Corrections made in this phase** (formatting only; no score, judgment or evidence changed):

- The sign-off lines of Q-H1, Q-H3 and Q-H9 rendered "Scored by … Date" and the "Part B figures …" line as
  one line in Markdown (a missing line break, or both on one line). They now match the other seven.
- The opening paragraph said the file "contains no score", which was true when it was prepared. It now
  states that all ten itineraries have received human scores and signed assessments, and that the
  section 5 tables are authoritative.

**The two known issues were already correct in the working file when this phase began:**

- Barcelona (Q-H2) Attraction usefulness is 3 in the signed table **and** 3 in the comparison table. The
  earlier 2 in the comparison table was no longer present, so no change was needed.
- Every review date reads `10/14/2026` (ten sign-offs and the section 7 result line). No `0/14/2026`
  typo is present.

**Open items in the record** (reported, not resolved here, because they are not transcription errors):

1. **Part B blind-scoring attestation.** Each of the ten sign-offs has "Part B figures (Appendix A) read only
   after scoring: [ ]" left unticked. The contract's rule is that a plan is scored before its Part B
   figures are read. The box is the reviewer's own attestation, so it was not ticked here. The reviewer
   should tick it or explain it. The scores stand either way ("scores are recorded once and are not
   revised").
2. **Criterion 3 decision.** Doc 26 section 7 records criterion 3 as `undetermined`, with "Decided by" and
   "Date" left blank and the Q-H8 A3 treatment "pending contract-owner decision". That decision is still
   needed for the record. It does not affect the verdict.

## 3. Official human-quality outcome

Computed directly from the ten signed tables. AU = Attraction usefulness, GC = Geographic and day
coherence, PE = Personalization, PR = Pace realism, RA = Redundancy avoidance, OV = Overall.

| Scenario | AU | GC | PE | PR | RA | **OV** |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| Q-H1 Chicago | 3 | 2 | 3 | 2 | 2 | **2** |
| Q-H2 Barcelona | 3 | 4 | 2 | 2 | 3 | **2** |
| Q-H3 Rome | 2 | 2 | 4 | 2 | 2 | **2** |
| Q-H4 Singapore | 3 | 4 | 3 | 4 | 2 | **3** |
| Q-H5 Bangkok | 2 | 3 | 3 | 2 | 2 | **2** |
| Q-H6 Sydney | 3 | 3 | 4 | 4 | 4 | **3** |
| Q-H7 Reykjavík | 2 | 3 | 3 | 4 | 4 | **2** |
| Q-H8 Rio de Janeiro | 3 | 2 | 3 | 1 | 4 | **2** |
| Q-H9 Prague | 2 | 2 | 3 | 2 | 3 | **2** |
| Q-H10 Mumbai | 2 | 2 | 3 | 2 | 3 | **2** |
| Median | 2.5 | 2.5 | 3 | 2 | 3 | **2** |
| Mean (for information) | 2.5 | 2.7 | 3.1 | 2.5 | 2.9 | 2.2 |

The release target uses only the Overall score. The other dimensions have no threshold and are shown to
explain it.

- Overall, in scenario order (Q-H1 … Q-H10): `2 2 2 3 2 3 2 2 2 2`
- Sorted: `2 2 2 2 2 2 2 2 3 3`
- Median = (5th + 6th) ÷ 2 = (2 + 2) ÷ 2 = **2.0**
- Minimum = **2** (Q-H1, Q-H2, Q-H3, Q-H5, Q-H7, Q-H8, Q-H9, Q-H10)
- Overall below 3: **8**. Overall at least 4: **0**.
- **Criterion 1** (median ≥ 4): **not met** (2.0).
- **Criterion 2** (no Overall below 3): **not met** (8 scenarios below 3).

Pace realism (median 2) and Attraction usefulness and Geographic coherence (median 2.5) are the weakest
dimensions. Personalization (median 3) is the strongest, mostly because 13 of the 14 requested must-visits
were scheduled.

## 4. Part A and the baseline: three separate results

The contract says the parts "are never collapsed into one PASS". The three results below are reported
separately and are not merged.

**A. Official baseline hard correctness** (the canary's twenty acceptance checks, policy version 2), as
recorded:

- 10 scenarios completed; **7 of 10 PASS**.
- FAIL: **Q-H2 Barcelona** (review code `MUST_VISIT` outside the accepted list), **Q-H8 Rio de Janeiro**
  (`LONG_TRAVEL_DAY` outside the accepted list; a day with unresolved excessive walking), **Q-H10 Mumbai**
  (`GEOGRAPHIC_DISPERSION` outside the accepted list).
- These three failures are **not** classified as factual-safety failures. The baseline includes checks the
  contract does not list under Part A, and it leaves out A1–A3 (doc 26, appendix C.3).

**B. Part A, section 4.1** ("Existing requirements (unchanged and authoritative)"): **all ten scenarios
meet every recorded requirement**, including the factual-safety invariants. All ten have zero fabricated or
unverified scheduled identities, zero unsupported factual claims and zero legs with movement data the
provider did not return.

**C. Part A, section 4.2:**

- A1 (must-visit identity) and A2 (requested-interest normalization): **no violation recorded** in any
  scenario.
- A3 (severe route burden): no long-travel day in nine scenarios. **Q-H8 Rio de Janeiro is UNDETERMINED from
  the saved evidence.** Long-travel days survived on days 2, 3 and 4 with viable inventory ≥ T. On day 3 the
  two alternatives that were checked brought no material improvement. On days 2 and 4 no alternative was
  checked because the routing allowance was exhausted. The record does not compute whether a valid
  lower-burden alternative existed. Under the holdout rules this cannot be settled by rerunning the
  scenario.

**It is not claimed that the whole of Part A conclusively passed.**

What Part A's factual-safety checks cover matters for the findings below. They confirm that every
scheduled place is a **provider-grounded identity** and that the narrative makes no unsupported claim. They
do **not** check that a place is open, still at the location the provider records, publicly accessible, or
a separate venue from another scheduled stop. Several of the human findings (section 6, categories 1, 2 and
5) fall in that gap: they are not Part A failures under the contract's text, but they are serious problems
for a traveller.

## 5. Ten-scenario scorecard

| Scenario | Request | AU | GC | PE | PR | RA | **OV** | Baseline | Key limitations the reviewer observed |
| --- | --- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | --- |
| Q-H1 Chicago | 3 d, balanced; architecture, museums, riverfront; 2 must-visits | 3 | 2 | 3 | 2 | 2 | **2** | PASS | Pritzker Military Museum reported to have moved to Kenosha, WI, but planned as a 7-min walk downtown (day 2 infeasible); Chicago Stock Exchange Trading Room is inside the Art Institute, which is already on day 1; riverfront and architecture covered thinly; no visit durations |
| Q-H2 Barcelona | 4 d, balanced; architecture, food, markets; 2 must-visits | 3 | 4 | 2 | 2 | 3 | **2** | FAIL | Sagrada Família (must-visit) missing, not grounded; Santa Caterina market closed on the scheduled Sunday; Palau de la Generalitat not normally open that weekend; Casa del Guarda is inside Park Güell; weak day 4; food only through restaurant suggestions |
| Q-H3 Rome | 3 d, packed; history, art; 2 must-visits | 2 | 2 | 4 | 2 | 2 | **2** | PASS | Day 2 entirely at Veii, about 16 km out, with no journey there or back in the totals; public access to those sites not established; day 1 holds four time-intensive sites; Temple of Vespasian and Titus (in the Forum) repeated on day 3 |
| Q-H4 Singapore | 3 d, balanced; gardens, food, waterfront; 1 must-visit | 3 | 4 | 3 | 4 | 2 | **3** | PASS | Six of nine stops are parks, several of them minor (Hong Lim, Raffles Place); no food experience scheduled; the record marks waterfront "not covered" although Gardens by the Bay, Esplanade and Merlion Park are on Marina Bay |
| Q-H5 Bangkok | 4 d, balanced; history, markets, food; 1 must-visit | 2 | 3 | 3 | 2 | 2 | **2** | PASS | Vimanmek Mansion reported closed since 2016; King Prajadhipok Museum closed for renovation and normally closed on Mondays (the scheduled day); Wat Phra Kaew and the Textile Museum are within the Grand Palace compound; drive to Wat Arun instead of the short ferry; traffic not considered |
| Q-H6 Sydney | 3 d, relaxed; harbour, outdoors, museums; 1 must-visit | 3 | 3 | 4 | 4 | 4 | **3** | PASS | Cross-harbour drive to Hayes Street Beach although the F5 ferry stops nearby; a ferry warning on a 300 m walking leg on day 3 (reviewer asks for investigation); modest standalone stops |
| Q-H7 Reykjavík | 2 d, relaxed; architecture, museums, harbor | 2 | 3 | 3 | 4 | 4 | **2** | PASS | Harbour interest "covered" by Skarfaklettur, a coastal rock near the industrial harbour (labelled `beach`), instead of the Old Harbour; the Reykjavík Art Museum branch is not identified |
| Q-H8 Rio de Janeiro | 4 d, balanced; outdoors, history, food; 2 must-visits | 3 | 2 | 3 | 1 | 4 | **2** | FAIL | 166-min uphill walk to Christ the Redeemer (normally reached by the Corcovado train or authorized vans), 80-min and 52-min walks; Paineiras visitor centre on a different day from Christ the Redeemer; niche museums; food only through restaurant suggestions |
| Q-H9 Prague | 3 d, balanced; history, architecture, art; 2 must-visits | 2 | 2 | 3 | 2 | 3 | **2** | PASS | Kinský Palace permanently closed as an exhibition venue since 1 June 2025; day 3 in Zbraslav/Točná with no journey from the centre, castle interiors closed, no public opening day for the aviation museum on 18 October; Sex Machines Museum does not fit the stated interests; St Vitus is within Prague Castle |
| Q-H10 Mumbai | 3 d, packed; history, markets, food, promenade; 1 must-visit | 2 | 2 | 3 | 2 | 3 | **2** | FAIL | Day 1 detours 9.5 km to Imperial Mahal (a minor stop); "promenade" served by Chimbai Beach rather than Marine Drive or Bandstand; niche day 3 with access not confirmed; driving times optimistic in traffic; no street-food experience |

The facts in the last column are the reviewer's own checks recorded in doc 26 (the reviewer cites official
sites where named there). This report has not checked them independently and does not call any of them
verified beyond what the review records.

## 6. Cross-scenario problem categories

The ten categories below are the ones this phase was asked to consider. Each is backed only by the signed
reviews. "Caught" means caught by an existing automated check or traveller-facing finding in the saved
records. Classification uses four labels: **application planning** (what the planner chose to do with the
data it had), **provider-data limitation** (the data or capability the providers give), **validation gap**
(nothing checks it), and **uncertain**.

**Attribution.** Every statement in the examples below about a real place — its location, opening days
and hours, closure, access, ticketing, containment in another site, or nearby transport — is what the
human reviewer reported in doc 26 from their own look-ups, sometimes citing an official source. It is
quoted or paraphrased from that review. This report did not check any of it independently, and it states
no place fact as verified. Statements about the plans themselves (stops, legs, codes, coverage, Part B
figures) come from the saved records.

### Category 1 — Incorrect or outdated attraction locations

- **Definition.** A scheduled place whose recorded position is not where a visitor would find it today, or
  whose name does not identify which of several sites is meant.
- **Affected.** Q-H1 (definite in the review). Q-H7 (ambiguous identity rather than a wrong position).
- **Examples.** Q-H1: the reviewer reports that the Pritzker Military Museum & Library is now at
  10475 12th Street, Kenosha, WI. The plan routes to it as a 7-minute walk from the Chicago Stock Exchange
  Trading Room, so day 2 cannot be done as planned. Q-H7: "Listasafn Reykjavíkur" operates at several
  locations, and the plan does not say which one.
- **Severity / impact.** High where it happens: the traveller goes to a place that is not there, and the
  day's travel figures are wrong. It appears in one clear case among 96 scheduled stops, but the human
  checks covered selected places, not every stop.
- **Caught?** No. Routing coverage is 100% and the identity is provider-grounded. Grounding confirms that
  the provider has a record, not that the record is current.
- **Classification.** Provider-data limitation (an out-of-date place record), plus a validation gap (no
  freshness or relocation signal is checked).

### Category 2 — Closed, inaccessible or unsuitable attractions

- **Definition.** A scheduled stop that a visitor cannot enter: permanently closed, closed for renovation,
  closed on the scheduled weekday, interiors closed, open only on event days, or public access not
  established. Also stops that do not suit the stated request.
- **Affected.** Q-H2, Q-H3, Q-H5, Q-H8, Q-H9, Q-H10 (and Q-H1 through category 1).
- **Examples.**
  - Long-term or permanent closure reported: Q-H5 Vimanmek Mansion ("closed to public visitors since
    2016"); Q-H5 King Prajadhipok Museum (renovation closure on its official site); Q-H9 Kinský Palace
    ("permanently closed as an exhibition venue since June 1, 2025", National Gallery site).
  - Closed or not open on the scheduled day: Q-H2 Mercat de Santa Caterina (closed Sundays) and Palau de la
    Generalitat (tours on the 2nd and 4th weekends; 18 October is the 3rd); Q-H5 King Prajadhipok Museum
    (normally closed Mondays); Q-H9 Točná Aviation Museum (next public open day 14 November); Q-H9 Zbraslav
    Castle (interiors closed).
  - Access not established by the reviewer: Q-H3 the four Veii sites; Q-H5 Saranrom Palace; Q-H9 the
    Manoušek bell-making museum; Q-H10 Nature Education Centre and BEST Museum (Sunday access); Q-H10 Kala
    Qila (dilapidated, limited visitor facilities); Q-H8 Praça da Apoteose (limited without an event).
  - Unsuitable for the request: Q-H9 Sex Machines Museum for a history, architecture and art trip.
- **Severity / impact.** High. Q-H5 day 4 is left "mostly unusable", and Q-H9 days 2 and 3 are undermined.
  This is the most frequent reason the reviewer gave for Overall 2.
- **Caught?** No. Every day carries the stored note that opening hours and feasibility checks are not
  implemented, so the gap is disclosed in general, but no stop is flagged.
- **Classification.** Provider-data limitation (no operational status or opening hours are used) and
  validation gap. The unsuitable-for-request case is application planning.

### Category 3 — Must-visits missing despite being requested

- **Definition.** A must-visit the traveller named that does not appear in the plan.
- **Affected.** Q-H2 only. 13 of 14 requested must-visits across the holdout were scheduled.
- **Example.** Sagrada Família was recorded as "not grounded (unresolved)" and not scheduled. The reviewer
  calls it a central requirement and scored Personalization 2 and Overall 2 partly because of it.
- **Severity / impact.** High for the scenario concerned (one of two explicitly requested places, and a
  major landmark), but rare.
- **Caught?** Yes. The plan carries the review code `MUST_VISIT`, the traveller sees `needs_review`, and the
  baseline FAILs. Under A1, "an ungroundable term stays ungrounded", so this is not an A1 violation.
- **Classification.** Uncertain: the record shows the grounding step returned nothing usable, but not
  whether the cause is the geocoder's data or the application's matching (for example of the accented
  name). An investigation item, not a confirmed defect.

### Category 4 — Low-value or obscure attraction filler

- **Definition.** A limited slot spent on a place a general visitor would seldom choose, while a better fit
  for the request was available.
- **Affected.** Nine of ten (all but Q-H5, where the reviewer's usefulness concerns are closures).
- **Examples.** Q-H1 "weaker choices" on day 2; Q-H2 day 4 (Mercat de Sarrià, Porta de Sarrià, a small
  square); Q-H3 day 2 (four Veii sites, "relatively obscure"); Q-H4 Hong Lim Park and Raffles Place Park;
  Q-H6 Hayes Street Beach; Q-H7 Skarfaklettur; Q-H8 the Penitentiary Museum and the Paineiras visitor centre
  as a separate stop; Q-H9 day 3 (bell-making and aviation museums); Q-H10 Imperial Mahal, Chimbai Beach and
  day 3. The reviewer names stronger fits (Chicago River architecture, the Reykjavík Old Harbour, Marine
  Drive or Bandstand, the Vatican Museums). For Q-H1 the record shows the "Chicago Riverwalk" was a grounded
  anchor that was not scheduled.
- **Severity / impact.** Medium to high. It is the most common complaint, and it is cited in the
  evidence for every Attraction usefulness score of 2 except Q-H5's (Q-H3, Q-H7, Q-H9, Q-H10).
- **Caught?** No. The contract lists "tourist-value ranking and obscure-filler share" as a future metric
  that is not computed (section 5.3). The Part B records show unused viable candidates above the lowest
  scheduled tier in eight scenarios (from 7 to 20). Whether those were better places is not established.
- **Classification.** Application planning (which stops fill the slots, and filling to the pace target T),
  limited by a provider-data limitation: there is no provider-backed signal of how much a place matters to
  visitors, and provider order must not be used as one.

### Category 5 — Duplicate venues or sub-features treated as distinct attractions

- **Definition.** Two scheduled stops that are one visitor experience (a part inside a larger site, or a
  site's own visitor facility), sometimes on different days.
- **Affected.** Q-H1, Q-H2, Q-H3, Q-H5, Q-H9; and Q-H8 as a related same-excursion case.
- **Examples.** Q-H1 the Trading Room inside the Art Institute (day 2, Art Institute on day 1); Q-H2 Casa
  del Guarda within Park Güell; Q-H3 Temple of Vespasian and Titus within the Roman Forum (day 3, Forum on
  day 1); Q-H5 Wat Phra Kaew inside the Grand Palace compound on the same ticket, and the Queen Sirikit
  Museum of Textiles in the same compound on day 2; Q-H9 St Vitus Cathedral within Prague Castle; Q-H8 the
  Paineiras visitor centre on day 1, apart from Christ the Redeemer on day 3.
- **Severity / impact.** Medium. A slot is wasted, the stop count overstates how full the plan is, and the
  cross-day cases send the traveller back to a venue they already visited. Redundancy avoidance was 2 in
  four scenarios, three of them (Q-H1, Q-H3, Q-H5) because of this. Q-H4's 2 is for repeated parks.
- **Caught?** No. "Zero duplicates" and "no unresolved suspected duplicate scheduled" passed everywhere. The
  contract and CLAUDE.md record sub-feature redundancy as a known V1 limitation and forbid inferring
  parent/child from names. "Same-excursion or complex redundancy" is a future metric that is not computed.
- **Classification.** Provider-data limitation (no containment relation is used) and validation gap. The
  cross-day placement is also application planning.

### Category 6 — Missing travel to and from daily attraction clusters

- **Definition.** A day whose stops sit away from where a visitor starts, with no journey there or back in
  the plan, so the day's travel total understates the effort.
- **Affected.** Q-H3 (day 2), Q-H9 (day 3). Q-H2: the reviewer notes in general that "transfers between
  daily starting locations" are missing.
- **Examples.** Q-H3 day 2 reports 47 minutes of walking among four Veii sites about 16 km from the centre,
  with no journey from Rome. Q-H9 day 3 reports 21 minutes among Zbraslav and Točná, with no journey from
  central Prague.
- **Severity / impact.** High where it happens. The figures shown are true for the legs they describe, but
  a traveller reading "47 minutes" for a day outside the city is misled. Both scenarios scored Geographic
  coherence 2.
- **Caught?** No. Legs connect only the stops of one day, and no day origin is modelled (accommodation is
  not connected). The geography checks measure spread within a day, not distance from the destination, so
  both days are recorded as `compact`.
- **Classification.** Application planning (an out-of-centre day composed without justification) and
  validation gap (no origin leg, and nothing tells the traveller the totals exclude getting there).

### Category 7 — Unrealistic or incomplete travel modes

- **Definition.** A transfer shown in a mode a visitor would not or could not reasonably use, or without
  the mode they normally would use.
- **Affected.** Q-H8, Q-H5, Q-H6, Q-H10 (and Q-H7, where the reviewer notes day 2 transport needs arranging).
- **Examples.** Q-H8: a 166-minute, 6.3 km walk up to Christ the Redeemer, an 80-minute walk with an uphill
  approach to Forte Duque de Caxias, and a 52-minute walk. The vehicle alternative was not checked because
  the allowance was exhausted. Q-H5: a drive to Wat Arun where a cross-river ferry of about 3 minutes
  exists. Q-H6: a drive to Hayes Street Beach although the F5 ferry stops about 2 minutes away. Q-H5 and
  Q-H10: driving times the reviewer considers optimistic in traffic. Q-H6: the record flags a ferry on day 3,
  where the leg shown is a 300 m walk; the reviewer asks for this to be investigated rather than accepted.
- **Severity / impact.** High for Q-H8: Pace realism 1, the only 1 in the review, and a walk that is
  unsuitable as a normal visitor transfer. Medium elsewhere.
- **Caught?** Partly. Q-H8's long-travel days are flagged (`LONG_TRAVEL_DAY`, excessive walking), shown to
  the traveller and failed by the baseline, but the 166-minute walk is still shown as the leg's mode. No
  check covers the other cases. "No public transport is modelled" is recorded in every scenario, and
  transit is `not_connected` by design.
- **Classification.** Provider-data limitation (no transit, ferry-service or funicular routing; no traffic),
  plus application planning for Q-H8 (the routing allowance ran out before the severe legs were checked).
  The Q-H6 ferry flag is uncertain: provider data or interpretation.

### Category 8 — Missing visit durations, hours, admission and schedule feasibility

- **Definition.** The plan gives no visit length, opening hours, timed-entry or ticket requirement, so it
  cannot be checked whether a day fits in clock time.
- **Affected.** All ten (structural, and stated on every day by the stored note). The reviewer cites it
  specifically in Q-H1, Q-H2, Q-H3, Q-H4, Q-H5, Q-H7, Q-H8, Q-H9, Q-H10.
- **Examples.** Q-H1 the Art Institute opens at 11 AM on the Friday, while the stored day rationale says it
  is visited "in the morning"; Q-H3 the Colosseum needs a reserved time slot, and day 1 holds four
  time-intensive sites; Q-H9 St Vitus open to tourists only 12–5 PM on 16 October; Q-H10 CSMVS takes
  3–4 hours; Q-H4 National Gallery about 3 hours; Q-H2 Palau de la Música tours 9 AM–3:30 PM, 50–55 minutes.
  The reviewer also confirms several placements that do fit (La Boqueria on Saturday, Picasso Museum on
  Sunday, Chatuchak on Sunday).
- **Severity / impact.** Medium on its own, because it is disclosed. It makes Pace realism hard to judge
  (median 2), and together with category 2 it produces days that cannot be done.
- **Caught?** Disclosed, not checked. One related observation: a model-written day rationale may suggest a
  time of day ("in the morning") that the plan does not model.
- **Classification.** Provider-data limitation (no hours or duration source is connected) and validation
  gap.

### Category 9 — Weak personalization of food, waterfront and other stated interests

- **Definition.** A requested interest is formally "covered" but not delivered as an experience the traveller
  would recognise.
- **Affected.** Food: all five scenarios that requested it (Q-H2, Q-H4, Q-H5, Q-H8, Q-H10). Waterfront
  family: Q-H1 (riverfront), Q-H7 (harbor), Q-H10 (promenade). Other interests: Q-H3 (art outweighed by
  archaeology), Q-H9 (niche day 3).
- **Examples.** In all five food scenarios the record says "scheduled places with recorded food evidence:
  none", and food is covered only by nearby restaurant suggestions. Part B records viable food supply 0 in
  each. The reviewer counted Or Tor Kor Market (Q-H5) as a meaningful food experience, while the record
  classed it as a market. Q-H7 harbour was served by Skarfaklettur instead of the Old Harbour. Q-H10 promenade
  was served by Chimbai Beach instead of Marine Drive or Bandstand. Q-H1: the reviewer suggests a Chicago
  River architecture experience.
- **Severity / impact.** Medium. It is the main reason Personalization is 3 rather than 4 in most
  scenarios.
- **Caught?** No. Coverage is binary, and a weak but grounded place can cover an interest. The contract
  already records this as a V1.1 item (section 15, "Known limits").
- **Classification.** Application planning (which stop serves an interest; the food definition) together
  with a provider-data limitation (food-specific places were not retrieved as viable supply in any of the
  five).

### Category 10 — Incorrect interest-coverage classifications

- **Definition.** The recorded coverage status disagrees with what the reviewer found.
- **Affected.** Q-H4 (false "not covered"). Q-H7 and Q-H10 ("covered" by a place the reviewer considers a
  poor fit). Q-H1 is uncertain: the record marks riverfront covered, and doc 26 appendix B asks whether a
  lakefront beach is doing it; the reviewer's scores do not answer that directly.
- **Examples.** Q-H4: Gardens by the Bay, Esplanade – Theatres on the Bay and Merlion Park are on Marina Bay,
  but the record says waterfront is not covered and that no viable candidate served it. Since the holdout,
  the traveller page would show this as an uncovered interest with "no supply" wording, which in this case
  is untrue. Q-H7: Skarfaklettur is categorized as `beach`; the reviewer says this is incorrect (a coastal
  rock formation).
- **Severity / impact.** Medium. A false "not covered" tells the traveller something untrue about their
  plan, and a weak "covered" hides a gap.
- **Caught?** No. The coverage figures are the classifier's own output.
- **Classification.** Application planning (the interest taxonomy over provider categories) together with a
  provider-data limitation (category labels as the provider returns them).

### Summary table

| # | Category | Scenarios | Severity | Caught automatically | Classification |
| ---: | --- | --- | --- | --- | --- |
| 1 | Incorrect / outdated locations | H1 (H7 ambiguous) | High, rare | No | Provider data; validation gap |
| 2 | Closed / inaccessible / unsuitable | H2, H3, H5, H8, H9, H10 | High | No (general disclosure only) | Provider data; validation gap |
| 3 | Must-visit missing | H2 | High, rare | **Yes** (`MUST_VISIT`) | Uncertain |
| 4 | Low-value filler | 9 of 10 | Medium–high | No | Application planning; provider data |
| 5 | Sub-features as distinct stops | H1, H2, H3, H5, H9 (H8) | Medium | No (known V1 limitation) | Provider data; validation gap; planning |
| 6 | Missing travel to/from clusters | H3, H9 (H2 general) | High | No | Application planning; validation gap |
| 7 | Unrealistic travel modes | H8, H5, H6, H10 (H7) | High (H8), medium | Partly (H8 only) | Provider data; planning (H8); H6 uncertain |
| 8 | No durations / hours / admission | 10 of 10 | Medium (disclosed) | Disclosed, not checked | Provider data; validation gap |
| 9 | Weak food / waterfront personalization | food 5 of 5; H1, H7, H10; H3, H9 | Medium | No | Application planning; provider data |
| 10 | Coverage misclassification | H4, H7, H10 (H1 uncertain) | Medium | No | Application planning; provider data |

## 7. Prioritized improvement backlog

This backlog is for discussion only and nothing in it is scheduled or implemented. Place facts in its
examples are the reviewer's reports, as in section 6, and are not independently verified. Every direction is
generic: no city, landmark or neighbourhood name may enter production logic (contract section 7; CLAUDE.md).
The fact that these cities exposed a problem does not justify a fix aimed at them. "Cost" names what each
item probably needs; none has been estimated.

### P0 — investigate before any broader public release

**P0-1. Places that are closed, relocated or not open to the public are scheduled as ordinary stops.**

- *Examples:* Q-H1 Pritzker Military Museum (relocated); Q-H5 Vimanmek Mansion and King Prajadhipok
  Museum; Q-H9 Kinský Palace, Zbraslav Castle interiors and Točná Aviation Museum.
- *Direction:* find out what operational-status, access or "disused" evidence the current Places and Place
  Details responses already contain, and whether any of it is discarded. If such evidence exists, exclude
  or flag the place. If not, assess a second source of status data, and meanwhile tell the traveller per
  stop that current status and access were not checked. Never infer closure from a name.
- *Acceptance evidence:* on new development cases, a human-checked count per plan of stops that are
  permanently closed, relocated or not publicly accessible. Target zero, reported separately from the
  rubric. A new automated check is added only once a trustworthy signal exists.
- *Cost:* possibly more Place Details calls (credits). A status source would be a new provider capability.

**P0-2. Infeasible walking transfers shown as the planned mode.**

- *Examples:* Q-H8: 166 minutes uphill to Christ the Redeemer, plus 80 and 52 minutes, with
  `allowance_exhausted` on the vehicle alternative and `route_budget_exhausted` on the repair.
- *Direction:* (a) when a walking leg exceeds the pace's walking limit and no alternative was verified, the
  traveller view should not present that walk as the recommendation. It should state that the transfer
  needs other transport and that none was verified, as the existing unverified-leg row already does.
  (b) Investigate spending the route allowance on severe legs before lower-value checks, without raising
  the cap unless the evidence supports it (CLAUDE.md, routing by planner pass). (c) Settle the Q-H8 A3
  question on development cases, not the holdout.
- *Acceptance evidence:* on development cases, zero legs over the pace walking limit shown as the
  recommended mode without an explicit traveller warning. A3 judged determinable, through the
  `lower_burden_alternative_existed` evidence, on every long-travel day.
- *Cost:* (a) none. (b) possibly more routing requests within the existing per-generation budget.
  Funicular, train or transit options would need a new provider capability (transit is `not_connected`).

**P0-3. Days away from the centre with no journey there or back.**

- *Examples:* Q-H3 day 2 (Veii, about 16 km out, 47 minutes shown); Q-H9 day 3 (Zbraslav and Točná,
  21 minutes shown).
- *Direction:* (a) a traveller-facing disclosure on any day whose stops all lie well away from the resolved
  destination point, saying that travel to and from the day's area is not included. The boundary used must
  come from an existing contract boundary or be agreed by the contract owner; no new threshold may be
  invented here. (b) Investigate why day composition chose an entire out-of-centre day when central supply
  was healthy, and consider requiring an interest or must-visit reason for such a day.
- *Acceptance evidence:* on development cases, every such day carries the disclosure (automated), and human
  Geographic coherence ≥ 3 on every case with a healthy central inventory.
- *Cost:* (a) none. Routing an origin leg per day would cost one or two routing requests per day (credits),
  and would only be meaningful once a start point (accommodation) exists.

### P1 — material usefulness and personalization deficiencies

**P1-1. Low-value filler in limited slots** (category 4; nine of ten scenarios).

- *Direction:* (a) find out why grounded anchors that fit the request (for example Q-H1's unscheduled
  riverwalk anchor) lost slots to stops with less stored evidence, inside the existing single usefulness
  ordering, without adding a second formula. (b) Evaluate a provider-backed visitor-significance signal,
  which contract section 5.3 says this needs. (c) Consider whether a plan at T should prefer fewer,
  stronger stops when a stop's only reason is reaching T. Contract section 14 already says "The pace target
  T is a target: a day stays below it rather than take remote discretionary filler".
- *Acceptance:* human Attraction usefulness median ≥ 4 on the next unseen holdout; once defined, an
  obscure-filler share metric reported in Part B.
- *Cost:* (b) may need a new data source.

**P1-2. Sub-features and same-complex stops scheduled separately** (category 5; five scenarios plus Q-H8).

- *Direction:* use provider geometry or containment relations (a place inside another place's area), not
  names, to treat a contained place as part of its parent visit. Keep such stops on the same day as their
  parent, or leave them out. Implement the `same_excursion_or_complex_redundancy` metric first, so the
  problem is measured before it is optimized.
- *Acceptance:* zero same-complex pairs in human review of development cases, and the new metric agrees
  with human labels on them.
- *Cost:* depends on whether the provider exposes area geometry for the parent. Possibly more Place Details
  calls.

**P1-3. Food interest delivered only by restaurant suggestions** (category 9; five of five food scenarios).

- *Direction:* find out why viable food supply was 0 in all five (discovery categories, the rule that a
  generic marketplace alone never serves food, quality filtering). When food is requested and food-evidence
  places exist, schedule at least one where the pace allows.
- *Acceptance:* in development cases requesting food, at least one scheduled stop with recorded food
  evidence whenever supply exists; human Personalization ≥ 4 on those cases.
- *Cost:* possibly one more Places request per generation (credits).

**P1-4. Interest coverage is binary and sometimes wrong** (category 10; Q-H4 false negative; Q-H7 and
Q-H10 weak coverage).

- *Direction:* audit the waterfront classification against provider categories and geometry (a place on
  the water versus a place categorized as a waterfront feature). Add a graded "covered weakly" state, which
  the contract already lists as a V1.1 item, and make sure the traveller page never states "no supply"
  when the classifier may have missed it.
- *Acceptance:* agreement between recorded coverage and a human coverage label on development cases. Zero
  false "not covered" on human-confirmed cases.
- *Cost:* none expected beyond engineering.

**P1-5. Must-visit grounding failure** (category 3; Q-H2).

- *Direction:* investigate the grounding trace for an accented, well-known landmark name using a separate
  development canary, not the holdout. Check the geocoder's result and the application's matching rules.
- *Acceptance:* grounding rate on a development list of requested must-visits, including names with
  diacritics and multi-word names. No requested must-visit missing from a plan without the `MUST_VISIT`
  disclosure (already true).
- *Cost:* geocoder requests during development only.

**P1-6. Opening hours, weekday closures and visit durations** (category 8, and the day-specific part of
category 2).

- *Direction:* assess a provider for opening hours, then check weekday feasibility against the trip date
  before adding any clock-time scheduling. Until a source exists, make sure model-written rationales do not
  suggest times of day the plan does not check (Q-H1 "in the morning").
- *Acceptance:* zero scheduled stops on a day the source reports as closed. Rationale wording audited on
  development cases.
- *Cost:* a new provider capability, and probably credits per generation.

**P1-7. Public transport and ferry options; the Q-H6 ferry flag** (category 7, apart from P0-2).

- *Direction:* investigate why a 300 m walking leg carries a provider ferry flag, through the adapter's
  handling of the per-step flag and a development reproduction. Record public transport as an explicit
  `not_connected` limitation on each long or driving leg until a transit provider exists.
- *Acceptance:* the ferry flag agrees with the displayed leg on development cases. Traveller view states on
  each affected leg that public transport is not considered.
- *Cost:* transit routing would be a new provider capability.

### P2 — polish, presentation and lower-impact items

- **P2-1. Ambiguous multi-site names** (Q-H7, the Reykjavík Art Museum branch). Show the provider's address
  or locality with the name. *Acceptance:* every scheduled stop shows a disambiguating address line.
  *Cost:* none.
- **P2-2. Raw provider category labels** (Q-H7 Skarfaklettur as `beach`). Avoid presenting a provider
  category as a description of what the visitor will find, and audit the category-to-interest mapping
  (with P1-4). *Cost:* none.
- **P2-3. Cross-day sequencing of related places** (Q-H5 textile museum the day after the Grand Palace;
  Q-H8 Paineiras visitor centre apart from Christ the Redeemer). Mostly solved by P1-2; otherwise a
  composition preference to keep related places together. *Cost:* none.
- **P2-4. Under-used relaxed days** (Q-H6: "some days feel underutilized"). Consider optional nearby
  suggestions that do not count as scheduled stops. *Cost:* none or minimal.
- **P2-5. Weather-dependent alternatives** (Q-H4 heat and rain; Q-H7 exposed coastal stop). Weather is
  currently a data-coverage note. An indoor alternative for outdoor stops could follow later. *Cost:* uses
  the existing weather provider.
- **P2-6. Dated events** (Q-H1 Open House Chicago on 17–18 October; the Opera House performance in Q-H6).
  Noted by the reviewer as missed opportunities. Out of V1 scope; it would need an events source (new
  provider capability).

## 8. Release recommendation

| Decision | Recommendation |
| --- | --- |
| **Original full V1 quality acceptance** | **NO-GO.** Criteria 1 and 2 of the contract's release target fail (median 2.0; 8 of 10 below 3). The contract is not reinterpreted, and no threshold is changed. |
| **Controlled private demonstration** | **Possible, assessed separately from the quality target.** It is acceptable only where the presenter controls the audience and presents the system as a decision pipeline whose plans are drafts needing review, not as a source of usable itineraries. The traveller-facing safeguards are working as designed on this holdout: every plan is `needs_review`, Part A section 4.1 holds in all ten, and the material findings (`MUST_VISIT`, `LONG_TRAVEL_DAY`, `GEOGRAPHIC_DISPERSION`) are disclosed. Do not demonstrate a plan as followable, and avoid presenting Q-H-style plans without their findings. |
| **Limited public beta** | **Not approved by this review.** It would require an explicit, recorded exception by the contract owner that names this result (Overall median 2.0, eight plans "poor", the P0 items above) and accepts the risk. The remaining operational safeguards must also be in place: the labelled limited-beta copy that calls no itinerary complete, verified or ready to follow; the generation and abuse limits; and the open items of the operations runbook (section 7: the restore drill into an isolated Neon branch, notification of a failed uptime check, backup storage; and section 8: no verified deletion path for someone who cannot sign in, which requires registration to stay limited). This review recommends investigating P0-1 to P0-3 before any such exception. The beta label does not change the quality result and must not be presented as if it did. |
| **Broader public registration** | **Not approved by this quality review.** Runbook section 8 independently requires a decision before registration is opened without restriction. |

## 9. V1.1 evaluation approach

**The original holdout is now exposed.** The ten Q-H scenarios, their saved plans, and the reviewer's
lookups, scores and named alternatives are evaluation evidence that is now known to everyone working on
the planner. Under contract section 7.3 the holdout "is not rerun and re-presented as unseen", and a
defect it exposes "is validated on tuning scenarios or a separate canary". This report adds no exception.

Recommended approach for future acceptance:

1. **Keep the V1 record intact.** `benchmark_results/quality_holdout_v1/`, doc 26 and this report remain the
   V1 result. They are never edited to reflect later improvements, and are never cited as evidence that a
   later version passes.
2. **Use separate development cases for remediation.** Use the existing tuning scenarios (Q-T1–Q-T6) plus
   new development cases built around the generic problem categories in section 6 (for example: a
   must-visit with diacritics; a destination whose healthy central inventory competes with out-of-centre
   places; a steep-terrain must-visit; a food request; a waterfront request; a landmark containing named
   sub-features). Choose them from cities that are not in any earlier tuning or holdout set, and label them
   development cases. They may be run as often as needed.
3. **Do not tune against the exposed holdout.** Do not rerun the Q-H scenarios to "check the fix". Do not
   turn the reviewer's place-specific findings (for example a named closed museum) into exclusions. Do not
   add any name from the holdout to production logic. If the owner ever wants the Q-H scenarios as
   regression cases, that needs an explicit amendment to contract section 7.3, and they must be labelled
   *exposed* in every report.
4. **Freeze a genuinely new unseen holdout before remediation begins.** Select it (not here, and not now)
   from cities in no earlier set, ideally by someone not doing the tuning. Pin it by hash as section 7.3
   requires for the current one, and run it whole, once, after a tuning freeze. The scenario list should
   exercise the same generic dimensions as v1 plus the failure categories above, stated as dimensions,
   never as expected stops.
5. **Settle the review protocol before that run.** Settle the gaps doc 26 section 1 lists (per-dimension
   anchors, one reviewer or several and how scores combine) and the criterion 3 reading, including whether
   A3 is an acceptance check, so that the next result can be decided. Record the blind-scoring attestation
   at signing. Add a per-stop human checklist (closed or relocated? accessible on the date? part of another
   stop? fits the request?) so that categories 1, 2 and 5 become counts as well as scores.
6. **Keep the release target unchanged.** Median Overall ≥ 4, no Overall below 3, and zero hard
   factual-safety failures, on the new unseen holdout. Any change to the target would have to be made in
   the contract, before the new holdout is run, and never to fit a result.
