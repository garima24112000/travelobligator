# AI Itinerary Generator — End-to-End Build Specification

This document is the working spec for the pipeline: what each stage does, what
it receives, what it must output, and how it should fail. It's meant to be
handed to whoever (or whatever) is helping build this, as the source of truth
for the architecture.

---

## 0. Core principle (read this before anything else)

**The LLM is a proposer, never a source of truth.** Every fact that reaches
the user must trace back to either (a) real provider/open data, or (b) an LLM
idea that was independently verified against that data. No price, rating,
opening hour, or "book now" claim is shown unless a real provider actually
supplied it.

Every other design decision in this doc exists to protect that one rule. If a
shortcut anywhere lets an unverified claim slip through to the user, it breaks
the thing that makes this product different from a generic "ask an LLM for an
itinerary" tool.

---

## 1. High-level pipeline

```
Trip Intake
   -> Traveler Profile Construction
   -> Destination Resolution
   -> Provider Candidate Collection  ─┐
   -> LLM Candidate Proposal          ├─> Grounding & Verification
                                      ─┘
   -> Candidate Quality Scoring
   -> Itinerary Scheduling
   -> Restaurant / Accommodation Enrichment
   -> Validation
   -> Frontend Presentation (trust layer)
   -> User Feedback
   -> Controlled Regeneration (loops back into proposal/grounding
      for the affected scope only, then re-validates)
```

---

## 2. Core data object: `PlanningState`

One object flows through the whole pipeline. Every stage reads from it and
writes its own section — nothing gets mutated out of order.

```
PlanningState {
  trip_request:        { destination, origin, dates, travelers, pace,
                          budget, interests[], must_visit[],
                          constraints[], free_text }

  traveler_profile:     { pace, interests[], must_visit[], constraints[],
                           mobility_flags[], budget_tier, decision_weights,
                           llm_inferred_tags[] }   // each tagged with the
                                                    // source text span

  destination_context:  { resolved_name, coordinates, bounding_area,
                           provider_status }

  candidate_pool: {
    raw_provider:        [ pois[], restaurants[], accommodation[] ]
    llm_proposed:        [ { name, rationale } ]
    grounded:            [ { name, source, coordinates, confidence,
                              match_type } ]
    rejected:            [ { name, reason } ]
  }

  scored_candidates:     [ { candidate_ref, tier, factors: {...} } ]

  schedule: {
    days: [ { date, stops: [ { candidate_ref, time_window, tier } ] } ]
  }

  validation_report:     { status, reasons[], per_day_flags[] }

  feedback_log:          [ { edit_type, target_ref, note } ]

  version:               integer   // bumped on every regeneration
}
```

Keep this object versioned and immutable per version — regeneration writes a
new `PlanningState` rather than overwriting the old one.

---

## 3. Stage-by-stage specification

### Stage 1 — Trip Intake
**Purpose:** capture the raw request.
**Input:** destination, origin, dates, travelers, pace, budget, interests,
must-visit, constraints, free text.
**Output:** `trip_request`, validated for completeness (valid dates,
non-empty destination, sane traveler count).
**Failure handling:** missing required fields block progression immediately —
ask the user directly, never let the LLM guess a missing field.

### Stage 2 — Traveler Profile Construction
**Purpose:** turn raw input + free text into a structured profile.
**Logic:**
- Structured fields (pace, dates, traveler count, budget tier) are parsed
  deterministically — no LLM involved.
- The LLM only classifies free text against a **fixed taxonomy** of interest
  tags and accessibility flags. It tags what's already implied — it does not
  invent a constraint category that wasn't in the text.
- Every LLM-inferred tag is logged with a pointer to the source text span, so
  "interpreted vs. invented" is auditable later, not a judgment call made in
  the moment.
**Output:** `traveler_profile`.
**Failure handling:** contradictory free text (e.g. "relaxed pace" + "10
things a day") is surfaced as a flagged conflict, not silently resolved one
way.

### Stage 3 — Destination Resolution
**Purpose:** resolve the destination to one confirmed geo entity.
**Logic:** geocode the string → canonical name, coordinates, bounding
area, provider status.
- Multiple matches (e.g. "Springfield") → return disambiguation options to
  the user. Never guess.
- No match / provider down → `destination_context.status = unavailable`,
  and the pipeline **stops here**. Never schedule around an unresolved place.
**Output:** `destination_context`.

### Stage 4 — Baseline Provider Candidate Collection
**Purpose:** pull real candidates before the LLM touches anything.
**Logic:** query POI/restaurant/accommodation providers inside the bounding
area; tag every result with its source and raw metadata (rating, hours if
available, category).
**Provider strategy:** don't rely on a single source. Use a primary open-data
provider (e.g. OSM/Nominatim/Overpass) plus a commercial fallback (e.g.
Google Places/Foursquare) for destinations where open data is thin. Track a
`coverage_score` per destination so later stages know how much weight to put
on LLM proposals vs. real data.
**Output:** `candidate_pool.raw_provider`.

### Stage 5 — LLM Candidate Proposal
**Purpose:** surface ideas the provider data alone might miss (locally known
but under-tagged spots), constrained to the actual traveler profile.
**Input to the LLM:** destination, duration, interests, must-visit,
constraints, pace, a short summary of what provider data already covers, and
an explicit instruction: names and one-line rationales only — no prices,
hours, ratings, routes, or booking links.
**Output:** `candidate_pool.llm_proposed`.
**Failure handling:** cap the number of ideas requested (e.g. 15–20) to bound
the grounding cost in the next stage.

### Stage 6 — Grounding & Verification (the trust firewall)
**Purpose:** nothing from Stage 5 is real until this stage confirms it.
**Logic:**
- Fuzzy-match each proposed name against provider data: name similarity +
  geo-proximity + category match.
- Confidence tiers: exact match within bounds → grounded; fuzzy/partial
  match → grounded at lower confidence, flagged for scoring; no match or
  match outside the bounding area → rejected.
- **Ambiguous matches** (e.g. a chain with several branches): don't silently
  pick the first result. Either choose the branch closest to the day's
  cluster centroid and flag it "best guess," or surface the ambiguity in the
  validation report.
- **Must-visit places get a different failure path than regular candidates.**
  If a user's explicit must-visit can't be grounded, don't drop it silently —
  flag it back to the user ("couldn't verify 'X' — did you mean...?").
**Output:** `candidate_pool.grounded` and `candidate_pool.rejected` (the
rejected list feeds the "what the AI suggested but we didn't use" trust UI).

### Stage 7 — Candidate Quality Scoring
**Purpose:** rank grounded candidates with an explicit, inspectable formula —
not implicit judgment.
**Suggested factors (tune weights later):**
- review count / review score, where the provider has it
- distance to the other candidates likely to share its day (clustering fit)
- match to stated interests/tags
- grounding confidence from Stage 6
- must-visit flag → always `primary_anchor` once grounded
**Tiers:** `primary_anchor`, `good_candidate`, `secondary_candidate`,
`low_priority`, `rejected`.
**Output:** `scored_candidates`, each with the individual factor values that
produced its tier (needed later for "why was this included").

### Stage 8 — Itinerary Scheduling
**Purpose:** turn scored candidates into an actual day-by-day plan.
**Logic:**
- Geographic clustering — each day covers one or two nearby clusters, not
  scattered stops across the whole destination.
- Time budgeting per day, driven by pace and trip duration.
- **Priority flag:** this is the stage most likely to undercut trust if
  skipped. Without travel-time and opening-hours awareness, a schedule can
  look complete while being logistically wrong — two stops 40 minutes apart
  placed back-to-back. For MVP, even a rough estimate (straight-line
  distance × an assumed walking/transit speed) is far better than nothing,
  and should be marked as approximate until real routing is wired in.
- If there aren't enough `good_candidate`+ tier stops to fill a day, produce
  a lighter day. Never pull in `low_priority` items as filler.
**Output:** `schedule`.

### Stage 9 — Restaurant & Accommodation Enrichment
**Purpose:** attach food/stay suggestions without inventing facts.
**Logic:** nearby provider-backed candidates only. No "highly rated,"
"cheap," "book now," or "open until X" claims unless that exact data is
present in the provider payload.
**Output:** schedule enriched with `restaurant_suggestions[]` /
`accommodation_suggestions[]` per day, each tagged with what data is actually
available vs. missing.

### Stage 10 — Validation
**Purpose:** make the trust/incompleteness state explicit, with a concrete
decision table instead of vague labels.

| Status | Trigger examples |
|---|---|
| `blocked` | Destination unresolved · zero grounded candidates for the whole trip · a hard constraint (e.g. wheelchair access) can't be evaluated for any candidate |
| `needs_review` | Opening hours unavailable for scheduled stops · travel time between stops is estimated, not routed · budget stated but unused in filtering · a must-visit failed to ground · a day has fewer than N good-tier stops |
| `ready` | None of the above apply |

**Output:** `validation_report`.

### Stage 11 — Frontend Presentation
**Purpose:** the trust layer — treat this as a first-class deliverable, not
an afterthought, since it's the actual product differentiator.
**Must show, per stop:** why it was included, its source (provider-verified
vs. LLM-proposed-then-verified), its confidence tier, and what's missing
(hours, price, routing).
**Must show, per trip:** the `validation_report` status in plain language
(not raw enum values), and the rejected LLM suggestions — seeing what the
system *didn't* accept builds more trust than only seeing what it did.

### Stage 12 — User Feedback Capture
**Purpose:** capture edits without re-running the whole pipeline.
**Logic:** map feedback to specific `candidate_ref`s or day indices as
structured edits wherever possible. Free-text feedback goes through the same
"interpret, don't invent" rule as Stage 2.

### Stage 13 — Controlled Regeneration & Versioning
**Purpose:** patch, don't rebuild.
**Logic:**
1. Identify the minimum affected scope from the feedback (e.g. "remove Times
   Square" affects that day's cluster only, not the whole trip).
2. Preserve every locked/kept item exactly as-is.
3. Re-run Stage 5 only for replacement ideas within the affected scope, then
   re-ground → re-score → re-schedule → re-validate just that scope.
4. Save as a new `PlanningState` version (v2, v3, ...) rather than
   overwriting, with a diff against the previous version for the UI.

---

## 4. MVP cut vs. later phases

**MVP (v1):** Stages 1–13, with rough travel-time estimates (straight-line +
assumed speed) instead of real routing, a single primary provider, no
pricing or booking data.

**Phase 2:** real routing/opening-hours integration, secondary provider
fallback for low-coverage destinations, budget-aware filtering, booking
links.

**Phase 3:** multi-provider price comparison, live availability, collaborative
trip editing.

---

## 5. Edge cases to design for explicitly

- Zero grounded candidates for an entire day (small or remote destination)
- Ambiguous destination name (multiple cities sharing a name)
- Ambiguous POI match (chain branches, similarly-named venues)
- Provider API downtime or rate limiting mid-request
- A must-visit that can't be grounded at all
- Conflicting free-text constraints
- Feedback edits with cross-day effects (e.g. removing the anchor other days
  were clustered around)

---

## 6. Suggested build order

1. `PlanningState` schema + trip intake + destination resolution — get the
   skeleton and stop-conditions right before anything else.
2. Provider candidate collection for one primary provider.
3. Grounding logic — this is the trust firewall; invest here before
   anything fancier upstream.
4. LLM candidate proposal, wired into grounding.
5. Quality scoring with an explicit, documented formula.
6. Scheduler with rough travel-time awareness.
7. Validation decision table.
8. Frontend trust-label UI.
9. Feedback capture + targeted regeneration.
10. Restaurant/accommodation enrichment.
11. Phase 2 items.
