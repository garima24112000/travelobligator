# Section 202A — Baseline product and itinerary QA (aggregate report)

Run date: 2026-09-23. Real network, real providers (Nominatim, Overpass, Open-Meteo,
Nager.Date, Frankfurter, OSRM, Kiwi MCP) and real Groq models. No mock places.
Three concepts are kept apart: **A** system correctness (objective, `generation_*.json`,
`regeneration_*.json`), **B** itinerary quality (human PASS/WARN/FAIL, `human_review.json`),
**C** personal preference (YES/MAYBE/NO — a *reviewer proxy*, the owner should confirm).

## Setup and honesty notes

| Mode | Backend flags (process-level overrides only; `.env` untouched) | Used for |
|---|---|---|
| `as_configured` | developer's normal `.env`: AI candidate proposal + narrator on Groq gpt-oss-20b; AI reasoning/repair/feedback-interpreter **off**; targeted regeneration **off** | PRIMARY baseline, 18 cases; legacy regeneration, 5 chains |
| `full_ai` (20b) | reasoning + repair + feedback interpreter on Groq gpt-oss-20b, targeted regeneration on | only LIS-1, LIS-2 valid: the Groq **200k tokens/day** quota was exhausted after LIS-2 (later runs: HTTP 429) |
| `full_ai_120b` | same flags with `GROQ_MODEL=openai/gpt-oss-120b` (separate quota bucket) — **deviation from the configured model** | 6 baseline cases + 4 targeted-regeneration chains; its quota was then also exhausted |

Not executed: full-AI baselines for the remaining 11 cases, a live-AI async job failure, and stale-conflict UX (covered by the 198B/199C controlled tests, not re-run here). Provider results vary run to run (Overpass 504s, restaurant fallback), so single-run itineraries are evidence, not statistics.

## Objective generation metrics (as_configured, n = 18)

- HTTP 200 on 18/18 generations; latency median 56 s (15–72 s), synchronous request.
- Scheduled experiences: 153. With stable id / coordinates / `claim_sources`: 153/153. Unsupported identities: 0 (name matches a provider candidate or has provider provenance). Cross-day duplicates in baselines: 0.
- Providers: places success 17/18 (1 unavailable: bare "Lisbon"); routes success 17; weather success 17; holidays/currency success 16 (2 unavailable); restaurants success 11, failed 4, fallback 2; flights success 1/18 (17 unavailable); accommodations open-POI 14, not_connected 4.
- Routing: 100/100 legs `feasible` via OSRM; travel-time buffers **100/100 `not_computable`** (itinerary has no schedule times).
- Validation: 17 `needs_review`, 1 `blocked`; 0 `ready`. Warning categories are almost identical on every case (feasibility, sequencing, movement, geometry, weather, accommodation, flights); `geographic_spread` on 7.
- AI: candidate proposal completed 13, rejected 5 (JSON truncation / missing required field / 429); itinerary reasoning not connected (by config); narrator success 14, failed 4.
- Empty days: NYC-1 (day 4), SF-1 (day 4), NYC-BARE (day 3), LIS-BARE (all). The validator raised no empty-day warning.

## Human review (as_configured, n = 18)

PASS 1 · WARN 12 · FAIL 5 — and personal_use YES 1 · MAYBE 12 · NO 5. Per-case notes with concrete examples are in `human_review.json`.
Recurring reasons: (1) low-value OSM stops dominate (public artworks/monuments, small commercial galleries, city gates, a hospital, sub-exhibits of one attraction listed as separate stops); (2) stated interests not represented (food/nightlife/outdoors in MIA-1, MIA-2, LA-1, SEA-1, LIS-2, CHI-2); (3) iconic anchors missing (Statue of Liberty/Central Park, Golden Gate Bridge, Pike Place/Space Needle, Jerónimos/Castelo) — several were named by the AI proposal that then failed validation/truncated; (4) empty or thin days.

## Regeneration

| | Targeted (full_ai, 120b) | Legacy (as_configured) |
|---|---|---|
| Steps executed | 12 | 13 (+1 skipped: target absent) |
| Applied (HTTP 200) | 9 | 10 |
| Exact requested change | **7 fully correct**, 1 partial (R05), 1 fail (R09) of 9 | **0 of 9 evaluable** (1 not evaluable) |
| Correct no-change refusals (ambiguous, impossible, ungroundable) | 3/3 (`REGENERATION_NEEDS_CLARIFICATION`) | 3/3 (`REGENERATION_NOT_AVAILABLE`, no derivable stage) |
| Reported preserved days actually unchanged | 6 of 7 evaluable (additive step reported `affected=[]`, `preserved=[1,2,3]` but day 2 changed) | not reported |
| Reported diff = independent set-diff (by id) | 9/9 | no diff produced |
| Additions provider-grounded / unknown ids | 1/1 (OSM relation) / 0 | 0 additions |
| Revision only on success; head label = version | 12/12; 9/9 | 13/13; 10/10 |
| Validation rerun / routes reference only current places | 9/9 / 12/12 | 10/10 / 13/13 |
| Narrator succeeded after regeneration | 4/9 (5 failed: forbidden-pattern guardrail, empty narrative) | n/a (Groq quota exhausted) |
| Latency per regeneration | median 68 s (39–73) | median 3 s |
| Experience-id survival for untouched places | 62–100 % on scoped steps, **0 %** on global steps | **0 %** (names 100 % identical) |

## Branch and frontend exploratory QA (real browser)

Branch create → activate → regenerate on Option B → compare → historical preview → switch back → logout/login → reopen all worked; version labels stayed clear ("Main · v2" / "Option B · v3"), preview was read-only. Weaknesses: with legacy regeneration the comparison lists every place as both *Added* and *Removed* (ids regenerated, names identical); browser reload returns to the home screen (saved trip must be reopened from My trips). No horizontal overflow at 375/390/1440 in either view. The regenerate button disabled while a legacy regeneration was in flight.

## Findings

**P0 — none.** No unsupported price/rating/hours/availability/booking claim was found in any structured field of 51 real payloads: the only non-null price/booking values are Kiwi MCP flight offers carrying provider/`data_status: live` provenance and the "not reviewed" caveat. Whether narrator *descriptions* count as fabrication is a policy call (see P1-5).

**P1**
1. **Default-config regeneration ignores the request but reports success.** With targeted regeneration off (the developer's `.env`), "Remove X" etc. yield "Regeneration applied v1 → v2" while X remains; content is identical and every experience id changes (0 % id survival).
2. **Destination geocode plausibility rejects legitimate localized names** ("Lisbon" → "Lisboa", "Cordoba" → "Córdoba"), producing an empty, blocked plan; the UI never suggests "Lisbon, Portugal". Shown on two structurally different cities.
3. **Empty/sparse days and no validator signal.** Days with zero stops in 4/18 runs; validator did not warn; all 17 non-blocked plans read `needs_review`, so warnings do not discriminate.
4. **Candidate selection quality.** Low-value/irrelevant stops and unrepresented interests (5 FAIL, 12 WARN); AI candidate proposal rejected in 5/18 (max-token truncation, missing required schema field, 429), discarding valid landmark proposals.
5. **Narrator descriptions go beyond the data** ("Arco Escuro adds a touch of modern design", "a popular spot on the waterfront", "immersive exhibits"), and the narrator fails often (4/18; 5/9 after targeted regeneration) because of over-broad forbidden-pattern checks (`verified`, `reserv…`, `highly rated`), leaving an empty narrative. Treat as release-blocking if place descriptions count as facts.
6. **Additive targeted regeneration misreports preservation** (`affected_day_indices=[]`, `preserved_day_indices=[1,2,3]`, `changed_sections=[]` while day 2 changed).
7. **Targeted regeneration produced cross-day duplicates** (Pelourinho de Lisboa on 3 days) in 1 of 4 chains (small candidate pool); validator missed them.
8. **Misleading failure copy when the AI provider is rate-limited:** refusal says "The regeneration engine has not been implemented" and the feedback panel says "No AI interpretation provider is connected".

**P2**: travel-time buffers inert (100 % `not_computable`); Open-Meteo 400 for dates beyond its forecast horizon reported as `failed`; Kiwi flights 1/18 success and a wasted ~11 s failure path; Overpass 504/fallback causes run-to-run variation; global "more relaxed" changes the label not the workload (7 → 7 stops); "more local food" applied with no food stop; global regeneration re-ids every place so the diff shows the same place added and removed; 15–75 s synchronous generation and ~68 s targeted regeneration; Traveler view leaks internals (`Candidate: proposal_001`, `destination_context.candidate_restaurants`, "haversine", "osrm", `openstreetmap_places`, internal field names in feedback history, trip id); mobile 375 px content column ~255 px inside nested cards, 51 controls under 32 px; browser reload loses the open plan; raw model output (`failed_generation`) is stored in narrator/proposal failure messages.
**P3**: benign 401 console error before login; repeated identical caveats per day in narration; page length (≈10 screens Traveler, ≈38 Developer).

## Release gates

| Gate | Status |
|---|---|
| A no fabricated provider facts | **PASS** (provider-fact categories; narrator descriptions tracked as P1-5) |
| B create/generate/reload reliable | **PASS** for reliability (every scripted generation returned HTTP 200); quality caveat P1-2 |
| C feedback/regeneration reliable | **FAIL** (default config ignores requests; targeted depends on AI quota) |
| D preservation correctness | **FAIL** (misreported additive preservation, duplicates, id churn) |
| E branch workflows | **PASS** (mechanics); comparison noisy after legacy regeneration |
| F itinerary quality | **FAIL** (1 PASS / 12 WARN / 5 FAIL) |
| G limitations understandable | **FAIL** (non-discriminating warnings, jargon, misleading refusal copy) |
| H mobile usable | **PASS** (no overflow, all flows work; density P2) |
| I no P0 | **PASS** |

Not release-ready.

## Recommended Section 202B order

1. Make regeneration honest and stable: enable/decide the targeted path as default or make legacy refuse what it cannot do; never say "applied" for a no-op; keep experience ids stable for unchanged places.
2. Fix destination resolution (localized names/diacritics/`name:en`) and give an actionable message when geocoding fails.
3. Candidate quality and interest coverage (general rules: demote artworks/monuments/small galleries/non-visitor POIs, merge sub-exhibits, interest-aware selection, no empty days) and harden the AI proposal (token budget, required fields, retry/fallback).
4. Validator: empty/sparse-day, cross-day duplicate, interest-coverage and spread checks so warnings discriminate.
5. Narrator: ground descriptions strictly to provided fields; fix false-positive forbidden patterns; retry rather than blank.
6. Targeted regeneration: correct affected/preserved reporting for additions, dedupe on refill, real effect for global pacing/interest requests.
7. AI-provider failure copy and quota handling (TPD/TPM), async generation for 15–75 s work.
8. Traveler-view jargon removal and mobile density.

---

# Follow-up: Section 202B.1 (added after the fact; the 202A baseline above is unchanged historical evidence)

Scope: regeneration honesty, stable identity, preservation truth, duplicate prevention, destination resolution, AI failure semantics. Item quality is NOT addressed here (202B.2/202B.3). Raw evidence: `followup_202b1_verification.json`. Real network and providers; the Groq daily quota was exhausted again, which reproduced the rate-limit case for real but prevented live additive/preservation runs (NOT_EXECUTED live; covered by deterministic tests).

| 202A P1 | 202B.1 result | Evidence |
|---|---|---|
| False legacy success (0/9 changes happened, all ids regenerated) | Fixed: legacy refuses free text with `REGENERATION_FEEDBACK_NOT_INTERPRETABLE`; no rerun, no version, feedback pending | Real: 2 of 2 legacy requests refused, ids surviving 100 % (nothing reran), no revision created |
| Additive preservation misreport | Fixed in code: affected/preserved days derived from final states; no-op = `REGENERATION_NO_EFFECT` | Deterministic tests (partition, downstream mutation, additive insertion, no-op). Live additive: NOT_EXECUTED (quota) |
| Cross-day duplicate | Fixed in code: provider identity now on every experience -> reserved ids + audit work; repeat dropped from non-preserved days with a warning; validator reports `duplicate_experience` | Deterministic tests; real targeted steps: 0 duplicates by provider identity |
| Localized geocode ("Lisbon", "Cordoba") | Fixed: structural provider-evidence rule | Real: Lisbon -> 9 places incl. Jerónimos; Cordoba resolves (bare = Córdoba, Argentina via provider ranking; "Cordoba, Spain" resolves but its plan is blocked by candidate quality, 202B.2); unresolvable name -> 422 `DESTINATION_UNRESOLVED` |
| Misleading "engine not implemented" copy | Fixed: per-kind copy; rate limit is its own code; raw provider bodies no longer stored | Real: 3 of 3 rate-limited requests returned `REGENERATION_PROVIDER_RATE_LIMITED` with no mutation/version and feedback still pending |

Also verified for real: after a targeted removal, unchanged places kept their `experience_id` (8 of 9 and 8 of 8 surviving vs 62–100 % / 0 % in 202A), reported diffs matched an independent set-diff, reported preserved days were unchanged, revisions were created only on success.

---

# Follow-up: Section 202B.2 — candidate quality, interest coverage, complete itineraries (added after the fact; 202A and 202B.1 above are unchanged historical evidence)

Scope: itinerary CONTENT quality only. Data: `generation_202b2_as_configured.json` (machine-readable, includes the new candidate-supply and scheduled-category metrics). Configuration: the developer's normal `.env`, real Overpass/Nominatim/Open-Meteo/OSRM. **AI stages are NOT evaluated**: the Groq daily quota was exhausted, so AI candidate proposal was `rejected` in all 8 runs and the narrator was degraded; every result below is the provider-backed deterministic path (which is also the fallback the AI path shares). Single runs against a public, load-shedding Overpass; `places=fallback_used` in 4 of 8 runs (primary query timed out), so results are evidence, not statistics. Cases: 202A requests unchanged, plus one new case (COR-ES, "Cordoba, Spain", 3 days, food + history). No human PASS/WARN/FAIL labels were re-assigned (numbers below are objective; the human comparison is qualitative).

| Case | 202A baseline | 202B.2 (as-configured, provider-backed) |
|---|---|---|
| DC-1 (history, museums) | day 3 was three small statues; museum coverage depended on AI proposals (DC-2 scheduled a hospital) | 0 unsuitable; 4 museums + 3 landmarks + park; requested `history` and `museum` both represented; 0 memorial-object stops flagged low-value |
| CHI-1 (architecture, food) | (CHI-2: ~9/16 stops public sculptures) | 0 low-value stops flagged; `architecture` and `food` (Christkindlmarket) both represented; Architecture Center + Riverwalk-area museums; a few memorial-type stops remain (they carry a wikipedia article, so are not classed low-value) |
| SEA-1 (outdoors, food) | 3 West-Seattle viewpoints, no food | `outdoors` represented; `food` reported as `interest_supply_limited` (no viable food candidate returned); Space Needle / Pike Place still absent from the provider pool |
| MIA-1 (nightlife, food) | zero nightlife/food stops; 4 zoo sub-exhibits as separate stops | `nightlife` represented by 4 theatre/cultural venues (provider `amenity=theatre`, mapped to entertainment); 0 zoo sub-exhibits; `food` reported as supply-limited |
| LA-1 (food, outdoors) | 9/12 stops small commercial galleries in one block | 0 gallery/low-value stops flagged; `outdoors` (viewpoints) and `food` (Cole's P.E. Buffet-type food supply) represented; 6 museums + 4 landmarks |
| SF-1 (outdoors, food) | empty day 4 (`[4,4,2,0]`), Golden Gate Bridge absent | `[4,4,4,4]`, no empty/thin day; `outdoors` represented (5 viewpoints); Golden Gate Bridge Vista Point present; `food` supply-limited |
| COR-ES ("Cordoba, Spain") | resolved but EMPTY plan (all candidates low priority, Overpass failure) | 9 stops, no empty day; Mezquita-area sights, museums, Mercado Victoria (`food`); resolved destination "Córdoba, Andalusia, Spain" |
| LIS-1 (food, history) | Belém mixed with city-gate filler; no food stop | 0 gate/low-value filler; museums, landmarks, a food market; both interests represented |

Aggregates over the 8 runs (82 scheduled experiences): unsuitable (hospital/office/etc.) scheduled **0**; empty days **0**; cross-day stable-identity duplicates **0**; low-value/gallery stops **0**; every requested interest with viable supply represented **8/8 cases** (interest with no viable supply is disclosed as a limitation: 5 cases for `food`/`nightlife`); unsupported identities **0**; unsupported provider facts **0** (no price/rating/hours/availability/booking fields in any plan; `provider_place_id` present on every stop); AI-promoted stops 0 (AI unavailable). Resolved destination stored for all 8 (e.g. "Chicago, South Chicago Township, Cook County, Illinois, United States").

Honest weaknesses that remain (not fixed here):
- **Landmark coverage is limited by provider evidence, not solved.** Fame cannot be inferred from OSM tags: many small local features carry `wikipedia`/`wikidata`, so Space Needle / Pike Place / Millennium Park / Navy Pier are not guaranteed. The AI proposal path (name -> provider grounding -> promotion) is the intended source for headline attractions and could not be evaluated (quota).
- **Some memorials survive** when they carry a wikipedia article (Chicago, DC); a bare `wikidata` tag no longer exempts a small object from the low-value cap.
- **Overpass reliability:** the wider per-family query is heavier; 4 of 8 runs fell back to per-tag queries (still significance/category-ranked). Latency rose (median ~66 s vs 56 s).
- **Food supply is thin** because restaurants are not attractions; only `amenity=marketplace` places satisfy `food`, and the outcome is reported (`interest_supply_limited`), never faked.
- Validation warning volume dropped only slightly (7–9 vs 7–9) because the informational provider notes were downgraded to `suggestion` severity rather than removed; case-specific findings (`empty_day`, `thin_day`, `interest_undercoverage`, `category_concentration`) fired 0 times on these plans, `interest_supply_limited` 5 times.
- AI proposal: live probe on gpt-oss-120b showed intermittent empty generations (`failed_generation: ''`, HTTP 400) — the failure class the new single bounded retry targets; the retry's effect on the 202A rejection rate could not be measured (quota).

---

# Follow-up: Section 202B.3 — narrator grounding, Traveler UX, reload persistence, mobile/accessibility (added after the fact; 202A, 202B.1 and 202B.2 above are unchanged historical evidence)

Scope: narrator behaviour and release-facing frontend. Labels: **REAL** = real browser (Playwright/Chromium) against the live backend with real Nominatim/Overpass/OSRM and the developer's normal `.env` (Groq narrator enabled; Groq was rate-limited most of the time); **CONTROLLED** = deterministic fixtures in the pytest suite. State was isolated in a scratch `LOCAL_STORAGE_PATH`. No `.env` was read or printed; async mode was a process-level `ASYNC_GENERATION_ENABLED=true`.

| Item | Result | Label |
|---|---|---|
| Narrator failure (Groq HTTP 429) | Plan, validation and experiences untouched; `narrative_source = deterministic_fallback`; fixed safe sentence shown; no raw provider text or HTTP status in Traveler view. Seen on Lisbon, Kyoto, Reykjavik (repeat runs). | REAL + CONTROLLED (failure matrix in `test_narrator_grounding_and_fallback.py`) |
| Narrator success, first 202B.3 contract | One live success (Reykjavik): no place descriptions/adjectives, but it wrote "Trip covers museums, food, nightlife" for a plan serving museum/food, and printed raw `transit_feasibility, weather_forecast, ...` identifiers. Fixed: requested-vs-served split in the prompt, plain-word limitations, and `travelerText` on displayed AI prose. | REAL (n = 1) |
| Narrator success, hardened contract | **NOT_EXECUTED**: three later attempts hit Groq 429 and fell back. The hardened contract is verified only by unit tests (CONTROLLED). No model was switched to manufacture a success. Carried to 202C. | — |
| Resolved destination | Heading shows the provider's `display_name` ("Kyoto, Kyoto Prefecture, Japan" / "Reykjavik, Capital Region, Iceland"), "You entered: ..." when it differs, plus the include-city-and-country hint. | REAL |
| Reload persistence | `?trip=` restored the same trip after reload (sync mode on the dev server; async mode on the dev server and on the production build), including reload mid-async-generation (job resumed, one `POST /generate`). Active branch after switch survives reload (Main -> Alt A -> reload -> Alt A -> Main -> reload -> Main). | REAL |
| Logout / re-login | URL hint cleared on logout; reload after logout stays on login; re-login opens home with no trip open. | REAL |
| Async release mode (production build) | create -> job -> polling -> completion -> loaded -> Main branch initialised; submit disabled while running; `DESTINATION_UNRESOLVED` job failure shows one actionable message, hint cleared, failed trip listed. | REAL |
| Targeted regeneration UX | Feedback saved, pending count visible; regenerate refused honestly ("Request needs AI interpretation... Nothing was changed and no new version was created"); no "Regeneration applied" banner; feedback stays pending. A successful targeted regeneration, and revision comparison (needs >= 2 revisions), were **not exercised in the browser** this section (no AI quota); covered by earlier 198/199 browser runs and the pytest suite. | REAL (refusal path only) |
| Traveler jargon scan | Full-page text scan for `proposal_`, `candidate_`, `haversine`, `openstreetmap_places`, `_report`, `destination_context`, snake_case triples, raw trip id, `needs_review`/`not_connected`: 0 hits (only the friendly label "OSRM routing" remains). | REAL |
| Mobile 375 / 390 | No horizontal overflow at 375, 390, 1440. Controls under 44px: 51 (202A, <32px) -> 6, all Leaflet/OSM attribution links (inline text). | REAL |
| Accessibility | axe-core 4.10: 0 violations in Traveler and Developer view; disclosure buttons toggle with Enter/Space; jump link opens its disclosure; day headings are `h3`. | REAL |
| Developer view density | 32 `h2` sections, no duplicated headings; nothing shortened. | REAL |
| Destinations spot-checked | Lisbon, Kyoto, Reykjavik (three only, to save Groq quota for 202C). | REAL |

Bugs found by this browser pass and fixed: a duplicate `id="branches"` (wrapper + panel), an invalid `<dl>` child, "via osrm" and `ai_candidate_promotion_report` leaking into Traveler text, Groq/HTTP detail in the Traveler narrative note, a stale reload hint after a failed generation, the same async failure shown twice, and a timer-driven fake progress bar.

Remaining / not claimed: landmark coverage is still open (unchanged from 202B.2); food supply stays `interest_supply_limited` and no restaurant provider was added; generation latency is 4-8 s when providers are cached but ~115-150 s cold; full-AI quality (proposal + reasoning + hardened narrator), the one-retry proposal policy and successful targeted regeneration under live Groq are **not verified** and carry to 202C. This section does not claim release readiness.

---

# Follow-up: Section 202C — final release-candidate evaluation (added after the fact; 202A, 202B.1, 202B.2, 202B.3 above are unchanged historical evidence)

Full detail: `202C_HUMAN_REVIEW.md` (every itinerary, place by place), `202C_COMPARISON.md` (202A vs 202C tables and the ten failure patterns), machine-readable `generation_202c_as_configured.json`, `202c_digest_as_configured.json`, `202c_narrator_live_audit.json`, `regeneration_202c_*.json`. Human labels are a reviewer proxy; the owner should confirm them.

**Environment.** Groq `openai/gpt-oss-20b` (the configured model; no other model was substituted), live Overpass/Nominatim/OSRM/Kiwi, production build of the frontend, real Chromium (Playwright). The Phase 1 probe succeeded, but the configured model has a **200,000 tokens/day** limit that the 19-case run itself exhausted (each case spends ~10k tokens on proposal attempts + narration); afterwards headroom returned at ~140 tokens/minute, so later AI-dependent phases ran one step at a time or not at all. Labels: **REAL** = live browser/backend/providers/Groq; **CONTROLLED** = real backend/browser with a labelled seam (`controlled_server.py`, outside the repo) that registers a legacy regeneration op and deterministically edits the regenerated plan, used only to exercise branch/compare/preview UI without AI quota.

## Decision inputs

| Area | Result | Label |
|---|---|---|
| Generation, 19 cases | 19/19 HTTP 200, 0 empty days, 0 cross-day duplicates, 0 unsuitable functional stops, 189/189 stops with stable id + provider place id + coordinates + claim sources, 0 unsupported identities | REAL |
| Itinerary acceptance (18 primary) | 2 PASS / 13 WARN / 3 FAIL; 1 YES / 14 MAYBE / 3 NO (202A: 1/12/5 and 1/12/5) | human proxy |
| Requested interests with viable supply | represented in every case (0 `interest_undercoverage`); 6 honest `interest_supply_limited` (food 4, nightlife 1, museums 1) | REAL |
| Remaining quality defects | single heritage trees, sculptures and memorials count as "architecture"/"history" (LIS-2, CHI-1, DC-1); headline landmarks absent when the AI proposal path is down (Central Park, Smithsonian, Space Needle, Mezquita); an OSM-mis-tagged furniture store counted as a food market (MIA-1); Marin-side stops in SF-1 | REAL |
| AI candidate proposal | **0/19 completed** (15 structural failures after the bounded retry, 4 rate-limited) vs 13/18 in 202A. Root cause found and fixed in 202C (below) | REAL |
| Narrator (hardened contract) | 10 live AI successes / 9 deterministic fallbacks (4 malformed-output failures, 1 grounding rejection, 4 rate limits); 109 sentences traced, 0 unsupported claims, served-vs-unserved interests always accurate; 9/9 fallbacks accurate | REAL |
| Guard false positives | ordinary grounded prose: 0/19 rejected offline; live: 1 of 11 model-returned narrations rejected (NYC-1) — the rejected text was not stored, cause unknown | REAL + offline |
| Async release path | 202 → polling → completion → reload mid-job (once at each of 375/390/1440) → Main branch initialised; one `POST /generate`; no fake percentage | REAL |
| Branches / comparison / preview | fork, activate, regenerate on Option B, Main head/content unchanged, switch both ways restores exact content, comparison matches the API payload (1 removed, 1 moved, 1 reordered day, no false add+remove), read-only preview with a single "Back" action, no ranking language; no overflow at 375/390/1440 | CONTROLLED regeneration + REAL branch/compare/preview code |
| Axe / mobile | axe 0 violations Traveler and Developer; tap targets < 44px at 375/390 only Leaflet/OSM attribution links; no clipped controls | REAL |
| Provider failure UX | dead Overpass + routing not connected → honest blocked plan ("no provider-backed attraction candidates are available"), critical finding, no invented stops | REAL |
| Pending feedback | see Findings P1-3 | REAL + unit tests |

## 202C code change (one, narrow, evidence-backed)

`backend/app/providers/ai_candidate_proposal/groq_adapter.py`: removed the forced `reasoning_effort="low"` added in 202B.2. Live reproduction at production size (15 proposals, 4000 max tokens): with `low`, Groq returned HTTP 400 `json_validate_failed` — the model omitted the required per-proposal `confidence` key; with the model default the same-size request returned a valid 15-proposal batch. The bounded structural retry was kept. 458 proposal/Groq-related tests pass. **The fix is verified by one live success only**; a full re-run of the 5-destination full-AI set was not possible under the token limit.

## Findings

**P0** — none. No fabricated identity, coordinate, price, rating, hours, availability, booking or safety claim in any structured field or rendered page (only Kiwi flight offers carry price/booking URL, `data_status: live`, "not a booking confirmation").

**P1**
1. AI candidate proposal path was broken by an unverified 202B.2 change (0/19 live) — fixed in 202C, minimally verified. Headline-landmark coverage still depends on it.
2. Itinerary quality is below a deployable bar: 3 FAIL / 13 WARN of 18. Recurrent, general causes: single trees / sculptures / memorial plaques / neighbourhood polygons treated as landmarks and matched to "architecture"/"history"; the places provider's per-tag fallback (6/19 runs) returns thin pools (DC-1 has no museum supply) and costs ~63 s more.
3. Pending-feedback semantics are surprising and not disclosed. Targeted regeneration applies exactly ONE pending event per call, the OLDEST first. After a rate-limited request A, submitting B and pressing Regenerate applies A, while the Traveler UI lists two identical "CAPTURED" rows and says only "Feedback is waiting". Nothing is deleted, but a user believing the newest instruction is being processed is misled. (REAL: two pending events retained across two refusals; code/test `test_multiple_pending_events_only_oldest_processed`.)
4. Targeted regeneration acceptance is incomplete (quota): see the regeneration table below.

**P2**: Overpass down and "no candidates" are presented identically (no "provider unreachable, retry" wording); a rate limit during the AI *reasoning* stage of a targeted regeneration surfaces as generic `REGENERATION_NOT_AVAILABLE` ("execution did not complete") instead of a rate-limit message; each retried AI stage spends tokens (a failed reasoning stage wastes the interpretation call); Kiwi flights fail/unavailable in 18/19 runs (+2-12 s each); the Traveler view does not say which stop serves which interest or which branch is being edited above the fold; median generation latency 63 s (sync request); AI proposal + narrator burn ~10k tokens/case against a 200k/day limit.
**P3**: comparison labels show abbreviated revision ids ("revision_cee56…"); "Kept for future regeneration" block above the trip summary is noise; 401 console errors while signed out.

## Targeted regeneration (`regeneration_202c_targeted.json`)

| Step | Result | Label |
|---|---|---|
| Exact removal (R01, DC-1) | **PASS**: only the target removed, other days unchanged, no replacement, ids 8/9 kept, no duplicates, v1→v2 once, Main head advanced once, 13 earlier rate-limited attempts audited and none consumed the feedback, validation rerun, route legs reference current stops | REAL |
| Ambiguous ("Swap that place for something better") | **PASS**: AI returned `needs_clarification`, plan unchanged, no version, feedback pending | REAL |
| Additive | **NOT_EXECUTED_QUOTA** (interpretation succeeded, then the reasoning stage was rate-limited; nothing changed) | REAL attempt |
| Preservation, move, pacing, interest adjustment | **NOT_EXECUTED_QUOTA** | — |
| Successful alternate-branch regeneration with real AI | **NOT_EXECUTED_QUOTA** (branch mechanics verified with the CONTROLLED seam only) | — |
| Narrator/fallback rerun after a real regeneration | not verified (narrator disabled in these runs to save tokens); fallback correctness verified on 9 generation runs | — |

## Not executed (Groq quota; no model substitution, no waiting for renewal)

Full-AI candidate proposal re-run with the 202C fix on the five representative destinations; provider-only vs AI-enhanced landmark comparison (**not evaluable**; 202A had 35 of 153 stops promoted from proposals, 202C 0); AI itinerary reasoning (grounded-candidate compliance, geography vs deterministic fallback); further live narrator runs (10 successes exist, from the as-configured run); the cause of the single live grounding rejection (NYC-1); additive/move/preservation/pacing/interest regeneration and their branch/comparison variants under real AI.

## Release gates

| Gate | Status |
|---|---|
| A no fabrication | PASS |
| B create/generate/reload | PASS |
| C targeted regeneration | NOT_EXECUTED (2 of 7 categories executed and passed) |
| D preservation | NOT_EXECUTED (only removal's "other days unchanged" observed) |
| E branches/comparison | PASS for mechanics with a CONTROLLED regeneration seam; real-AI branch regeneration NOT_EXECUTED |
| F itinerary quality | **FAIL** (3 FAIL / 13 WARN of 18) |
| G limitation clarity | PASS (P2 items above) |
| H mobile | PASS |
| I no P0 | PASS |
| J full-AI path | **FAIL** (0/19 live; fix minimally verified) / reasoning NOT_EXECUTED |
| K narrator grounding | PASS (10 live runs, n small) |
| L async release path | PASS |

**Decision: RELEASE_CANDIDATE_BLOCKED.** Quota is not the only reason: independent P1s exist (itinerary quality gate F, the undisclosed oldest-first pending-feedback semantics, and the AI-proposal regression whose fix is only minimally verified). Not ready for deployment.

Validation after QA (one production file changed in 202C): `compileall` OK, `pytest` 4326 passed / 25 skipped, `tsc` OK, `lint` clean, `build` OK, `git diff --check` clean.

---

# Follow-up: Section 202C.1A — non-AI release blockers (added after the fact; every 202A/202B/202C number above is unchanged)

Scope: the independent, non-quota P1s from 202C. No Groq call, no `.env` change, no model change, no full real run. Evidence is deterministic tests plus a DETERMINISTIC re-plan of the saved 202C candidate pools (`202c1a_pattern_rescore.json`; no provider call) and one REAL browser check of the pending-feedback UI (AI interpreter deliberately not connected, so nothing depended on quota).

## 1. Pending-feedback semantics (P1-3)
- Unchanged backend behaviour: a failed/rate-limited request stays pending; targeted regeneration processes exactly one pending event per call, the OLDEST first (`created_at`, list order on ties).
- New single source of truth: `PendingFeedbackSummary.queue_event_ids` / `next_feedback_event_id`, computed by the same rule the service uses (`min` by `created_at`), so the UI can never disagree with what Regenerate does.
- UX (Traveler and Developer): pending items are labelled **Next to apply** or **Waiting · #N in line**; an explainer says Regenerate handles one saved request at a time, oldest first, and that waiting requests are never skipped, reordered or deleted; the next item shows "Last attempt: AI temporarily rate-limited / interpretation unavailable. Nothing was changed; this request is still saved." from the recorded attempts. Applied items keep "Applied in vN". The Regenerate hint says it applies the oldest saved request first.
- REAL browser (two pending events across two refusals): NEXT TO APPLY + the last-attempt note on A, WAITING · #2 IN LINE on B, both still saved, no version created.
- Tests (`test_pending_feedback_queue_202c1a.py`): A rate-limited → A pending, no version, no false Applied → B submitted → A is next; a successful call consumes exactly A (B becomes next, v2 once); deterministic order incl. ties; published next event == the event the service processes; empty queue.

## 2. Low-value candidate rules (P1-2)
Root causes found by auditing `place_taxonomy` / `candidate_quality_service` / planner (structural provider tags only, no names):
1. A **heritage tree** (`tourism=attraction` + `natural=tree` + heritage/wikipedia) passed the "strong evidence" test, became a LANDMARK and matched architecture/history/outdoors.
2. **Notable small objects** (sculptures/memorial plaques with a wikipedia article) also became LANDMARKs and matched **architecture**.
3. A **bare wikidata id** raised any candidate — even a capped low-value object — back to a 0.6+ score floor.
4. Low-value objects could still fill a day whenever nothing better remained (the 202B.2 soft cap only applied "while alternatives remain").

Changes: an object model (`tree`, `artwork`, `memorial`, `fountain`, `gate`); an isolated tree is low value even with heritage/wikipedia tags (those describe the tree itself) unless the provider also marks a park/garden/reserve, museum/viewpoint/zoo type or a major historic type; trees are scored below the scheduling threshold (only a must-visit can schedule one); documented objects (wikipedia/heritage) are `notable_object`: eligible but ranked below real attractions and never LANDMARK/architecture unless the provider itself tags `tourism=attraction`; single objects can satisfy only `history` (documented memorials) or `art` (artworks), never `architecture`/`outdoors`; only strong signals (wikipedia/heritage/major historic type) lift a score; low-value objects are hard-capped at the diluted share even when nothing else remains, and the validator then reports `low_value_filler_skipped` (a suggestion) instead of a false "selection gap". Parks, gardens, reserves, viewpoints and significant natural attractions are untouched; art-focused trips keep artworks; `interest_supply_limited` vs `interest_undercoverage` semantics are unchanged.

Deterministic re-plan of saved 202C pools (before → after; not city expectations):

| Pattern | Before (202C) | After |
|---|---|---|
| Lisbon-style heritage-tree filler (LIS-2, LIS-1, LIS-BARE) | 3 + 2 + 2 single trees scheduled, counted as architecture/history | 0 trees; museums / real landmarks fill the days; architecture served only by non-tree stops |
| Chicago-style memorial/sculpture concentration (CHI-2) | 5 of 16 stops memorial/plaque/sign filler | 0 small objects; 14 of 16 real museums/viewpoints |
| Chicago-style, thin fallback pool (CHI-1) | 8 of 9 stops memorials/sculptures, all "architecture" | 0 small objects; replaced by the pool's other historic items (still a weak, thin pool) |
| DC-style weak filler (DC-1, DC-2) | memorial-only plan | 0 small objects; the pool's next-best historic areas fill in |
| Controls (LA-2, SEA-2, NYC-1) | as reviewed | unchanged composition, no empty day |

Honest limits: the replacements in CHI-1/DC-1 show what a thin per-tag fallback pool contains — DC-1 day 3 now spans 19.5 km (out-of-district historic polygons that structurally outrank small memorials). Removing junk cannot create supply; distance-aware selection and provider breadth remain open (P2). This was NOT re-verified on live providers or Groq.

Tests: `test_low_value_object_rules_202c1a.py` (15) plus one updated 202B.2 expectation ("small objects still fill days when nothing else exists" became "…do not pad") and the bare-wikidata scoring test.

## 3. Groq candidate-proposal regression guard
202C root cause: `reasoning_effort="low"` (added in 202B.2 without live verification) made gpt-oss-20b omit the required per-proposal `confidence` key at production size → HTTP 400 `json_validate_failed` in 15/15 live runs; the default effort returned a valid 15-proposal batch. The forced value stays removed. `test_groq_ai_candidate_proposal_provider.py` now asserts, without a live call: no `reasoning_effort` (top-level or `model_kwargs`) for gpt-oss-20b/120b and a non-gpt-oss model, the configured model is never substituted, `max_tokens` stays 4000 (token budget deliberately NOT inflated), structured output is `json_schema` + `strict=True`, every proposal field incl. `confidence` is required, the single bounded structural retry (exactly 2 attempts, batch halved) and that rate limits, auth (401/403) and timeouts (408/504) are never retried. Token-cost optimisation is a later infrastructure item; correct structured output takes priority.

## 4. Failure copy (structured causes only)
- AI reasoning stage rate limit/unavailability during a targeted regeneration (was generic `REGENERATION_NOT_AVAILABLE`): the reasoning adapters now record the structured `failure_kind`; the executor/application service map it to the same rate-limited / AI-unavailable code and copy as interpreter failures. Nothing changes, no version, feedback stays pending. A reasoning failure with no structured provider cause (guardrail/preservation rejection) stays generic.
- Empty plan: the validator names the recorded places-provider status — provider `failed`/`unavailable` ("could not be reached… does not mean the destination has nothing to see", retry later), `not_connected`, "responded but returned no attraction candidates", and — when candidates exist but none pass the quality rules — "N provider-backed candidate(s) were found, but none passed the quality checks" (replacing developer wording about running a planner stage). Missing coverage information is not inferred to be an outage.
- Unsupported/uninterpretable requests were already distinct (`REGENERATION_FEEDBACK_NOT_INTERPRETABLE` vs rate limit vs no-effect); unchanged. Tests: `test_failure_copy_202c1a.py`, `test_reasoning_failure_kind_202c1a.py`.

## Still quota-blocked (unchanged from 202C)
Live re-run of AI candidate proposals with the fix, AI itinerary reasoning, AI-assisted landmark coverage, successful additive/move/preservation/pacing/interest regeneration, and real-AI alternate-branch regeneration. Nothing here changes the 202C release decision: quality was re-checked only deterministically, so gates F and J are not re-run.
