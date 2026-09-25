# Evaluation area (Section 202A)

Repeatable product/itinerary QA for the current single-destination
TravelObligator. This directory is **measurement only**: nothing here is
imported by, or patched into, the application.

## Three things are recorded separately

| Concept | Question | How it is recorded |
|---|---|---|
| **A. System correctness** | Did the architecture behave per its contracts? | Objective, automated (`run_*_qa.py` output) |
| **B. Itinerary quality** | Would a reasonable traveler find the plan practical and coherent? | Human-labelled PASS / WARN / FAIL + a note per case |
| **C. Personal preference** | Would the reviewer use it as a starting point? | Human-labelled YES / MAYBE / NO — preference evidence, not a model score |

A technically valid itinerary can still be mediocre; an appealing one can
still contain unsupported facts. They are never merged into one number.
No pseudo-precise "quality %" is computed: report counts (`13 PASS / 2 WARN /
1 FAIL`) and only mathematically defined rates.

## Files

- `itinerary_qa_cases.json` — generation cases (only supported `TripRequest` inputs).
- `regeneration_qa_cases.json` — regeneration requests, bound to a generated trip at run time.
- `run_itinerary_qa.py` — generates every case against a **running** backend over HTTP and extracts objective metrics.
- `run_regeneration_qa.py` — executes regeneration chains and checks request correctness, preservation, grounding, versioning and diff accuracy.
- `results/` — compact machine-readable summaries and the human review notes / aggregate report. Full `PlanningState` payloads are **not** stored here.

## Running it

Real providers need real network. Feature flags are set with **process-level
environment overrides only** (never edit `.env`; never print it):

```bash
# as_configured: the developer's normal .env
cd backend && LOCAL_STORAGE_PATH=/tmp/qa/state_a.json PROVIDER_CACHE_PATH=/tmp/qa/cache_a.sqlite3 \
  uvicorn app.main:app --port 8002

# full_ai: AI reasoning + repair + feedback interpreter + targeted regeneration (Groq)
cd backend && LOCAL_STORAGE_PATH=/tmp/qa/state_f.json PROVIDER_CACHE_PATH=/tmp/qa/cache_f.sqlite3 \
  AI_ITINERARY_REASONING_ENABLED=true AI_ITINERARY_REASONING_PROVIDER=groq \
  AI_ITINERARY_REPAIR_ENABLED=true \
  AI_FEEDBACK_INTERPRETER_ENABLED=true AI_FEEDBACK_INTERPRETER_PROVIDER=groq \
  TARGETED_REGENERATION_ENABLED=true uvicorn app.main:app --port 8001

python evaluation/run_itinerary_qa.py --base-url http://localhost:8002 --mode as_configured \
  --raw-dir /tmp/qa/raw --log-file /tmp/qa/backend_a.log
```

Use a throwaway `LOCAL_STORAGE_PATH`/`PROVIDER_CACHE_PATH` so evaluation never
pollutes real trips, and run cases serially (Overpass/Nominatim rate limits).
`--raw-dir` is refused if it is inside the repository.

## Objective metrics (definitions)

- **Grounded experience**: has a stable `experience_id`, coordinates, and `claim_sources`.
- **Unsupported identity**: a scheduled name that matches no candidate in `destination_context` (attractions / restaurants / accommodation POIs) or promoted AI candidate, after name normalisation.
- **Far-apart day**: any two stops on one day more than 12 km apart (straight line). A coarse flag for human review, not a verdict (car-oriented cities legitimately exceed it).
- **Duplicate**: same normalised name on more than one day / twice in a day.
- **Pattern hits**: regex hits for price / rating / hours / availability / booking / safety wording in the narrator text. A hit is a *review prompt*, not proof of fabrication — provenance is checked by hand.
- **Non-null factual-looking fields**: every non-empty value under a key like `price|rating|hours|url|...` in user-facing plan sections, listed with its path so provenance can be checked.
- **Stage timings**: only what the app already logs (`duration_ms` on provider/AI calls). Stages that are not independently logged are not estimated.

## What is *not* here

No multi-city cases, no unsupported input fields, no controlled/fabricated
places standing in for real quality measurements. Controlled fixtures are
acceptable only for deterministic behaviour tests (they are labelled where used).

## Privacy

No secrets, cookies, auth state, screenshots of private data, or raw prompts
belong here. Test accounts use `@example.test` addresses.

## Section 202C additions (release-candidate evaluation)

New results are written as NEW files; the 202A baseline files are never modified.

- `run_202c_analysis.py` — reads the saved raw payloads and a run summary and writes a digest (narrator source, AI proposal attempts/rejection reasons, places-stage outcome, per-day categories/matched interests, per-sentence narrator vocabulary trace, aggregates), and renders `202C_HUMAN_REVIEW.md` from a human labels file (`202c_human_labels.json`). It never assigns PASS/WARN/FAIL itself.
- `run_202c_narrator_audit.py` — sentence-level audit of AI narrator prose against the structured facts it was given (place belongs to that day, "serves X" only for provider-matched interests, category text, route-data yes/no, served-vs-unserved interest statements). Output is evidence for a human verdict.
- `run_regeneration_qa.py` gained `--cases-file` and `--retry-rate-limit-minutes` (retry the SAME request while the AI provider is rate-limited; every retry count is recorded per step). `regeneration_202c_cases.json` reuses the original 202A step definitions verbatim, one step per fresh trip.
- Results: `generation_202c_as_configured.json`, `202c_digest_as_configured.json`, `202c_narrator_live_audit.json`, `regeneration_202c_*.json`, `202C_HUMAN_REVIEW.md`, `202C_COMPARISON.md`.
- Quota note: the configured Groq model has a 200k tokens-per-day limit that a single 19-case run exhausts; AI-dependent phases must be planned around it.
