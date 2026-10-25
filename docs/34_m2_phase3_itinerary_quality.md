# 34. M2 Phase 3 — Itinerary quality and real-world feasibility

Status: implemented on branch `m2/phase3-itinerary-quality` and verified by bounded DEVELOPMENT live runs
(sections 6–7). **Not deployed.** The unseen set `quality-holdout-v2` has not been run. No production setting changed. The new planner passes are on by default behind one
temporary rollback switch (`VISIT_DAY_FEASIBILITY_ENABLED`).

Rules are generic: no destination, landmark or neighbourhood name is in production code (enforced for the new
unseen set by `tests/scripts/test_quality_holdout_v2_m2p3.py`). The frozen holdouts were not run, and no
individual holdout itinerary was used to shape a rule.

## 1. Evidence: what the providers actually supply

| Source | Venue status | Opening hours | Check date |
|---|---|---|---|
| Geoapify Places (documented response) | none documented; no "closed" condition | not documented | — |
| Geoapify Places `datasource.raw` (OpenStreetMap record; undocumented, already read by the adapter) | lifecycle prefixes `disused:*`, `was:*`, `abandoned:*`, `demolished:*`, `razed:*`, `removed:*`, `destroyed:*`, `proposed:*`, `construction:*`, `planned:*`; bare `disused=yes` | `opening_hours` (OpenStreetMap syntax) | `check_date`, `check_date:opening_hours`, `survey:date` |
| Geoapify Place Details (documented) | none; `historic.end_date` = "When the object was out of service" (describes historic objects — not used) | `opening_hours` (string; format not stated) | — |
| Overpass (development) | the same OpenStreetMap tags | `opening_hours` | the same |
| Nager.Date | — | public holidays only | — |

Documentation read 2026-10-24 (Geoapify Places and Place Details pages). How often live records carry these
tags is **unknown** until the live run below: no recorded Geoapify response exists in the repository.

Before this phase every one of these tags was discarded (`filter_provider_tags` keeps taxonomy keys only, by
design — two tests pin that), so nothing in the pipeline could know a place was closed.

## 2. Root causes and changes, by workstream

### A. Venue reliability (P0)

*Root cause:* the provider evidence of closure (lifecycle prefixes, `opening_hours=off`) was dropped at the
adapter; a place record existing was implicitly treated as a visitable place.

*Changes:*

- `NormalizedPlace.venue_tags` (new, optional): the record's venue keys only
  (`services/venue_status.venue_evidence_tags`: lifecycle keys, `opening_hours`, check dates in date form —
  never a name, phone, website or address), kept beside the unchanged taxonomy tags by the Geoapify (search and
  Place Details) and Overpass adapters. It flows into the stored candidate pool; places stored earlier load with
  `None`.
- `services/venue_status.venue_status` separates what the record STATES from what is unknown:
  `not_operational` (a visitor-facing use under an "over" prefix and no current use; or a bare
  disused/abandoned flag on a service — amenity, shop, leisure), `not_yet_open`, `listed_closed` (whole value
  `off`/`closed`), `unknown` — which includes a record with no such tag. Nothing ever says "open". Conflicting
  evidence (a current use beside an ended one, e.g. a castle that once held a museum) stays `unknown`; a bare
  flag on a sight (`tourism`/`historic`) is not read, because it often describes the sight itself.
- Candidate quality: a stated status rejects the candidate before scheduling (new reason
  `venue_reported_not_operating`, severe → `rejected`). A grounded must-visit is kept, as with
  no-public-access, and disclosed.
- Duplicates / conflicting records: the existing identity, collision and same-complex rules are unchanged and
  now also bind every replacement made by the new passes.
- Stale evidence: the record's check date is kept and shown; no age threshold is invented, so age never acts.

### B. Opening hours and visit windows (P0)

*Root cause:* no hours evidence was kept, and the itinerary has no visit times (nothing populates
`ExperienceItem.start_time` / `end_time` or a duration), so even known hours could not be checked.

*Changes:*

- `services/opening_hours`: a complete reader for the common subset of the syntax — `24/7`; `;` rules (a later
  rule replaces what it names); additional `, ` rules; weekday ranges incl. wrapping; time lists; overnight
  spans (continuing into the next date); `off`/`closed`; `PH`. Anything else (months, dates, weeks, `SH`,
  sunrise/sunset, open ends, `||`, comments) makes the whole value unsupported: it says nothing.
- `PH` applies only from the trip's own holiday-provider data (`opening_hours.holiday_dates`); without that data
  a value with a `PH` rule is unknown for every date. No holiday schedule is invented.
- **Visit-window checks are date-level**: closed all day on the scheduled date / open at some time that date /
  unknown. Time-of-day compatibility needs visit times, which the planner does not produce (documented limit).
  Dates are the destination-local trip dates.
- Planner pass (`services/visit_day_feasibility.resolve_known_closures`, run after interest coverage and before
  routing, so routing is requested once for the final stops): a stop its own listed hours close on its date is
  moved to a day it is not closed on (room and spread boundary permitting) → swapped → replaced (not a
  must-visit; most useful unused candidate, at most one tier lower, not closed that date, not a duplicate or
  same-complex sibling, within the boundary) → dropped when the day keeps a stop and the plan keeps R → kept.
  Only a CLOSED reading acts; unknown never moves or removes a stop. An active user lock leaves the plan
  untouched.
- Validator: `venue_closed_on_scheduled_day` and `venue_reported_not_operating` (both material, traveller
  wording, "kept because you asked" for a must-visit); `ValidationReport.venue_evidence` restates per stop the
  status, the listed hours for that date, the record's check date and source (presentation only).
- Frontend: Traveler view shows per stop "Listed opening hours this day: 10:00–18:00. Hours can change; check
  before you go." / "Its listed opening hours show it closed on this day." / "Opening hours not checked.";
  Developer view shows the raw status, hours, check date and source.

### C. Route feasibility (P0)

*Finding:* already handled by earlier phases, not re-implemented: Q4 route recomposition acts on verified
provider legs before finalisation; over-limit walking legs are presented as "Long walking route … no faster way
of making this transfer was verified" (never "Walk"); unverified legs get an explicit row; ferries are flagged
only from the provider's own `ferry` flag.

*Not addressed (no reliable evidence):* elevation / uphill effort (no elevation source is connected); transit
(`not_connected`); a per-day travel budget against visit times (no visit times).

### D. Remote-day feasibility (P0)

*Root cause:* a day whose stops all lie beyond the planner's own pool reach was only disclosed after
generation; nothing asked whether the day had a reason.

*Change:* `resolve_unreasoned_remote_days`: in a **deterministic** plan with healthy supply (viable ≥ T), a
remote day with no grounded must-visit and no sole cover of a requested interest is rebuilt from unused
candidates within reach (most useful first, within the spread boundary, ≤ one tier below its weakest stop, not
closed that date, no duplicate or same-complex sibling); a smaller rebuilt day is accepted only if the plan
keeps R. A model-chosen plan keeps its places (Q3 contract: at most one replacement per day) and stays
disclosed. The journey to and from a remote day is still unchecked (no start point: accommodation is not
connected), and no transport is assumed.

### E. Candidate and day usefulness (P0)

- Closed / not-operating places no longer consume slots; every replacement goes through the one Q2 usefulness
  ordering with a one-tier guard; when nothing good enough exists, the stop is dropped (above R) rather than
  replaced by weaker filler — fewer worthwhile stops over an unrealistic day.
- *Not addressed:* a provider-backed visitor-significance signal (none exists — contract §5.3) and
  parent/child complex identity beyond the existing tagged sub-feature collapse.

### F. Quality evaluation and regression testing

- `quality_metrics.py` schema 6 adds `venue`: stops by evidence status; stated-not-operating and
  listed-closing-day counts (overall and excluding must-visits — target zero); share of stops with unchecked
  hours; stops with a record check date; remote days without a requested place.
- Holdout protections audited (contract §7.3; runner, canary and verification-script guards). The original
  Q-H holdout has been run and reported (docs/27), so it is no longer unseen.
- **New unseen acceptance set** `backend/scripts/benchmark/quality_v2.json` (`quality-holdout-v2`, 10 new
  cities, frozen 2026-10-24, hash-pinned): refused by the runner without `--allow-quality-holdout` and run only
  whole; refused by the canary and by every verification script. It has not been run.
- Fault injection (synthetic): missing, malformed, conflicting and stale evidence; unsupported hours syntax;
  missing holiday data; full receiving days; dispersing moves; duplicate and same-complex replacements;
  must-visit protection; minimum-stop protection; rollback switch.

## 3. Acceptance metrics (for the live and final rounds)

| Metric | Target |
|---|---|
| Scheduled stops stated as not operating, not a must-visit | 0 |
| Scheduled stops on a listed closing day, not a must-visit | 0 |
| Must-visits kept despite either | 100% disclosed on their day |
| Share of stops with hours unchecked | reported (evidence coverage; no target) |
| Remote days without a requested place, deterministic plan, viable ≥ T | 0, unless no alternative existed (reported) |
| Long-travel days surviving with viable ≥ T and a verified alternative | 0 (A3, unchanged) |
| Human: human-checked closed / relocated / inaccessible stops per plan (P0-1) | 0 |
| Human rubric on the unseen set | median overall ≥ 4, none < 3 (contract §6) |

## 4. Bounded live verification (development only)

    cd backend
    python scripts/verify_m2_phase3.py                          # dry run: plan + caps, NO request
    python scripts/verify_m2_phase3.py --scenarios 1 --execute  # smallest: one development city
    python scripts/verify_m2_phase3.py --execute --out /tmp/m2p3.json

Scenarios: D1 Vienna (3 days, balanced, museums + history), D2 Edinburgh (2 days, relaxed, history +
outdoors, must-visit Edinburgh Castle); the trip starts on a Monday three weeks out. Neither city is in any
frozen set (checked at run time).

| Run | Geoapify requests / credits | LLM (combined) | Open-Meteo / Nager.Date / Frankfurter | Kiwi | Total |
|---|---|---|---|---|---|
| `--scenarios 1` | ≤ 100 / ≤ 100 | ≤ 8 | 2 / 3 / 2 | 0 | ≤ 115 |
| both | ≤ 200 / ≤ 200 | ≤ 16 | 4 / 6 / 4 | 0 | ≤ 230 |

Phase 3 adds no provider request: venue evidence comes from the records the generation already fetches.

PASS/FAIL: generation completed; venue figures measured; zero stops stated not operating or on a listed
closing day except must-visits; no credential value in the report. Reported, not judged: evidence coverage
(stops by status, unchecked share — the answer to "what do the live records carry"), remote days, route
severity, the canary's own acceptance (new venue codes are deliberately not on its accepted list, so a surviving
finding fails the canary).

## 5. Remaining limitations

- Live evidence coverage is unknown; if the live records rarely carry `opening_hours` or lifecycle tags, most
  stops stay "Opening hours not checked" and closures stay undetected (P0-1 then needs a second status source).
- No visit times or durations: no time-of-day window check.
- OpenStreetMap statements can be outdated; age is shown, never acted on.
- A model-chosen remote day is disclosed, not rebuilt. *(Superseded by Final Batch 1: it is now rebuilt, and
  visit times exist — see `35_final_batch1_quality_corrections.md`.)*
- No elevation, transit or ticketing evidence; no visitor-significance signal.

## 6. First development run (Vienna, 2026-10-24) and the evidence-gap cycle

### 6.1 What the run reported

9 scheduled stops: 1 `listed_open_on_day`, 4 `hours_not_listed`, 4 `hours_not_understood`; no stated closure or
closing-day conflict; route severity 2 normal / 1 elevated; 52 HTTP requests, 49 Geoapify credits; Groq 1 × 200,
2 × 429 (deterministic fallback completed the plan). 17 of the 60 pool places carried `opening_hours`.

### 6.2 Root causes (from the run's own stored records; only value shapes are recorded here)

- **The four "not understood" values were well-formed and are now read:**
  - `Mo-Fr, Su 10:00-18:00` and `Mo-Tu, Th-Fr 09:00-14:00; We 09:00-20:00` — a weekday list written with `, `;
  - `PH,Tu-Su 09:00-18:00` — a defect: the additional-rule split treated `,` without a space as a new rule;
  - `closed "<comment>"` — a closure with a comment ("temporarily closed").
  Read correctly, two of the nine stops were on a day their own listed hours close them: a weekly closing
  Monday, and the "temporarily closed" record (now `listed_closed`, rejected before scheduling). The run could
  not see either.
- **The four "not listed" stops were grounded AI anchors**, not in the broad pool. Their Place Details answer was
  fetched (credits spent), and three of the four carried `opening_hours` — one in a seasonal month-day form
  (`Apr 1-Jun 30,Sep 1-Oct 31 …; Nov 1-Mar 31 …`) — but the grounding match, the grounding evidence and the
  promoted candidate had no venue field, and the evidence lookup only read the broad pool. One anchor's record
  has no hours. Lost evidence, not missing provider data, for three.
- **The elevated day was a conservative label, not impractical travel**: both transfers were verified vehicle
  routes (≈12 and ≈8 minutes, ≈5.7 km each), every limit was met, the straight-line spread was 5.6 km (boundary
  8 km), and no traveller finding was raised. `elevated` means "within the limits, but needs a vehicle transfer"
  and is reported, never repaired — correctly here.

### 6.3 Changes

- Hours reader: comma-space weekday lists; additional rules require `, ` + weekday; a quoted comment after
  `off`/`closed`; months and month-day ranges (lists, `Dec 24-26`, across the year end), with the same
  later-rule-replaces precedence. A comment on an OPEN rule ("by appointment") stays unsupported.
  `unsupported_feature` names what made a value unsupported (fixed labels).
- `venue_status`: a whole value `off`/`closed` with a quoted comment is `listed_closed`.
- Pipeline: `venue_tags` on the grounding match, the grounding evidence and the promoted candidate (validated:
  venue keys only), and on the promoted planner candidate; the evidence lookup reads promoted records.
  `venue_tags == {}` now means "the record was read and holds no venue keys"; `None` means nothing was read.
- Diagnostics: per stop the record origin (`pool` / `promoted_anchor` / `no_record`) and the cause of unknown
  hours (`no_record`, `record_without_venue_evidence`, `no_hours_in_record`, `holiday_data_unavailable`,
  `unsupported:<feature>`); per day the reasons a day is elevated (`needs_a_vehicle_transfer`,
  `walking_beyond_the_relaxed_pace_total`). `verify_m2_phase3.py` version 2 prints them and FAILS when a
  scheduled stop has no record at all.

### 6.4 Offline replay of the stored run (no provider call)

With the new reader, the stored plan shows 2 stops on their listed closing day. Re-scored and re-planned
offline: 1 pool candidate rejected for a stated closure, 0 closing-day conflicts, 4 stops with listed hours for
their day (was 1). The promoted anchors still show `record_without_venue_evidence`, because the stored run
predates the pipeline fix — only a new live run shows whether their evidence now arrives.

### 6.5 Remaining evidence gaps

- Whether the hand-over fix delivers the anchors' hours live (expected for three of four in this city).
- Coverage elsewhere: 17 of 60 pool places had hours here; other cities are unknown.
- Still unsupported: comments on open rules, sunrise/sunset, open ends, school holidays, years / weeks, `Mo[1]`.
- Groq rate limits still force the deterministic fallback for reasoning and the narrator.

## 7. Development verification after the evidence-gap cycle (2026-10-24)

`verify_m2_phase3.py` version 2, development configuration, throwaway storage, every request through the guard;
neither city is in a frozen set. Trip start Monday 2026-11-16. Codes and counts only.

### 7.1 Vienna, one city (after the fixes)

| Figure | Before the fixes (§6) | After |
|---|---|---|
| Scheduled stops | 9 | 9 |
| Listed open on their day | 1 | 8 |
| Hours unknown | 8 (4 not listed, 4 not understood) | 1 (`no_hours_in_record`) |
| Stops by record | pool 5 / anchors with no record 4 | pool 4 / promoted anchors 5 |
| Stated closures, closing-day conflicts, remote days | 0 / 0 / 0 (two conflicts were invisible) | 0 / 0 / 0 |
| Route severity | 2 normal, 1 elevated | 1 normal, 2 elevated — both `needs_a_vehicle_transfer`, within every limit |

44 requests (Geoapify 37 / 40 credits, Groq 4 — 200 ×1, 400 ×1, 429 ×2, Frankfurter 2, Nager.Date 1). Canary PASS.

### 7.2 Both development scenarios

| Scenario | Stops | Listed open | Unknown hours (cause) | Records | Route days | Canary |
|---|---|---|---|---|---|---|
| D1 Vienna, 3 d balanced | 9 | 7 | 2 (`no_hours_in_record`) | pool 5 / anchors 4 | 3 normal | PASS |
| D2 Edinburgh, 2 d relaxed, 1 must-visit | 4 | 3 | 1 (`unsupported:other_syntax`) | pool 3 / anchor 1 | 2 normal | PASS |

84 requests in total (Geoapify 71 / 74 credits, Groq 7 — 200 ×2, 400 ×1, 429 ×4, Frankfurter 4, Nager.Date 2);
no request refused, no credential value in the report; zero stated closures, closing-day conflicts or unjustified
remote days. (The model's day choices differ between runs, so the D1 figures of 7.1 and 7.2 are separate
generations, not a regression.)

### 7.3 What these runs do and do not show

- The anchor hand-over works live: every scheduled stop had a record; anchors' hours arrived.
- The remaining unknowns are real absences (`no_hours_in_record`) or one syntax variant: a colon after a
  seasonal range (`Jan DD-Mar DD: HH:MM-HH:MM; …`), a readability form of the syntax not yet supported (left as
  unknown, never guessed).
- No closure or closing-day conflict occurred in these plans, so the closure passes were not exercised live;
  they are covered by the offline fixtures and the replay of §6.4.
- Two cities, one start week: not evidence of coverage elsewhere. The final judgement is the unseen set, run once.
- Groq rate limits (429) and one malformed answer (400) recurred; the deterministic fallback completed both runs.

## 8. Review fix before the pull request

The two Phase 3 passes run after the schedule-diversity pass, so a replacement, a move or a rebuilt remote day
could have exceeded the HARD marketplace cap (`SCHEDULE_DIVERSITY_MAX_MARKETS_PER_DAY`). Every such change now has
to leave its day with no more hard marketplace excess than it had (the planner's own `schedule_diversity.hard_excess`);
regression tests cover the replacement, the move and the rebuild.
