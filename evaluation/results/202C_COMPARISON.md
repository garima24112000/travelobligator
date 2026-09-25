# Section 202C — 202A baseline vs 202C release-candidate (generation)

The 202A files are unchanged historical evidence. 202C numbers come from `generation_202c_as_configured.json` (19 cases: the 18 original 202A cases with their exact definitions + COR-ES added in 202B.2), the developer's normal configuration, live Overpass/Nominatim/OSRM/Kiwi, live Groq `openai/gpt-oss-20b` (200k tokens/day limit), serial runs, isolated state and cache. Labels are a reviewer-proxy (see `202C_HUMAN_REVIEW.md`).

## Human acceptance (18 primary cases)

| | 202A | 202C |
|---|---|---|
| PASS / WARN / FAIL | 1 / 12 / 5 | 2 / 13 / 3 |
| YES / MAYBE / NO | 1 / 12 / 5 | 1 / 14 / 3 |

With COR-ES (19 cases): 2 PASS / 14 WARN / 3 FAIL, 1 YES / 15 MAYBE / 3 NO.
PASS: SEA-2, LA-2. FAIL: LIS-2, DC-2, CHI-1. The 202A PASS (NYC-2) is now WARN because its headline stops (Central Park, High Line) came from AI proposals that no longer run (see below).

## Objective metrics (18 primary cases, same runner and definitions)

| Metric | 202A | 202C |
|---|---|---|
| Empty days | 6 days in 4 cases | **0** |
| Cross-day duplicates | 0 | 0 |
| Unsuitable functional places scheduled (hospital/office/school/...) | 1 (Homeopathic Hospital, DC-2) | **0** (6 unsuitable candidates rejected upstream) |
| Scheduled stops | 153 | 180 (189 with COR-ES) |
| Stable id / provider place id / coordinates / claim sources | all | all (189/189) |
| Unsupported identities (name matches no candidate) | 0 | 0 |
| Unsupported price/rating/hours/availability/booking claims in narration | manual review found invented descriptions | **0 in 10 live AI narrations** (109 sentences traced) |
| Non-null price/booking fields | Kiwi flight offers only | Kiwi flight offers only (1 case, `data_status: live`) |
| Days with stops > 12 km apart | NYC-1 | SF-1 (12.7 km) |
| Requested interest with viable supply and NOT represented | MIA-1, MIA-2, LA-1 (FAIL) and several WARN | **0** (`interest_undercoverage` = 0 in 19 runs) |
| `interest_supply_limited` (honest, no viable candidate) | n/a | 6 cases (food 4, nightlife 1, museums 1 -- DC-1) |
| AI candidate proposal completed | 13 / 18 | **0 / 19** (15 structural failures after 2 attempts, 4 rate limited) |
| Stops promoted from AI proposals | 35 of 153 | 0 |
| Narrator: AI success / deterministic fallback | 14 / 4 failed (blank) | 10 / 9 (fallback never blank, 9/9 accurate) |
| Places provider: broad query / per-tag fallback | 17 success, 1 unavailable | 13 broad / 6 fallback |
| Generation latency, median (mean; min-max) | 56 s (52; 15-72) | **63 s** (62; 14-120) |
| Readiness | 17 needs_review, 1 blocked | 19 needs_review |

## The ten 202A failure patterns

| Pattern | Verdict | Evidence |
|---|---|---|
| DC hospital scheduled | **FIXED** | 0 unsuitable functional stops in 189; unsuitable-type rejection active (6 candidates rejected). DC-2 has no hospital. |
| Sculpture/statue domination | **IMPROVED** (residual) | 5 sculpture/artwork stops and 16 memorial/monument stops in 189, but 16 are concentrated in CHI-1 (7), DC-1 (5), CHI-2 (4). |
| Chicago low-value concentration | **IMPROVED for CHI-2 / REGRESSED for CHI-1** | CHI-2: 11 real museums (202A: ~9 of 16 stops public art). CHI-1: 8 of 9 stops memorial/sculpture/site, all matched to "architecture" -- 202B.2 had 0 low-value stops; the places provider used the per-tag fallback this run. |
| Seattle weak attraction selection | **UNCHANGED** | SEA-1/SEA-2 remain in-city and coherent; SEA-2 is now PASS for a museums request, but Space Needle / Pike Place are still not in the provider pool and the AI proposal that once named them does not run. |
| Miami interest failure / zoo sub-features | **FIXED** | MIA-1 now serves nightlife (Club Space, `amenity=nightclub`) and food; 0 zoo sub-exhibit stops. One food stop is an OSM-mis-tagged furniture store. |
| LA gallery domination | **FIXED** | 0 commercial-gallery/low-value stops; LA-2 PASS. |
| San Francisco empty day | **FIXED** | `[4,4,4,4]`, no empty/thin day in any of 19 runs. |
| Lisbon low-value fillers | **UNCHANGED (different filler)** | City gates are gone, but single heritage trees (Tamareira-do-Senegal, Tipuana, Ginkgo; `natural=tree`) appear in 3 Lisbon runs and are counted as "history"/"architecture". |
| Localized destination failure | **FIXED** | Bare "Lisbon" and "Cordoba, Spain" resolve (Lisbon, Portugal / Cordoba, Andalusia, Spain) and produce full plans. |
| Unsupported narrator descriptions | **FIXED (10 live runs)** | 109 sentences, 0 unsupported descriptive/price/hours/availability/safety claims, served-vs-unserved interests stated accurately. AI failures fall back to accurate fact-only prose (9/9). Not a proof for rare outputs; n = 10. |

## What got worse or is newly visible

- **AI candidate proposals stopped working with the configured model** (0 / 19; 202A 13 / 18). The only functional change since 202A is `reasoning_effort="low"` plus a structural retry, added in 202B.2 when live verification was impossible. Headline places that 202A obtained through proposals (Torre de Belem, Central Park + High Line, Smithsonian museums, Met/MoMA) are absent again. See the main report for the diagnosis outcome.
- Places-provider per-tag fallback: 0 / 18 in 202A, 4 / 8 in 202B.2, 6 / 19 in 202C. A fallback run costs ~63 s more (median unlogged remainder 16 s broad vs 79 s fallback); fallback runs return thinner pools (DC-1 has no museum supply).
- Median latency is higher (63 s vs 56 s) despite provider caches being cold for the first occurrence of each destination.
