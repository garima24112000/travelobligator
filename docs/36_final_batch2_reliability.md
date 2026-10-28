# 36. Final Batch 2 — feature and reliability closure

Status: implemented on branch `stabilization/final-batch2-reliability` (from `main` at `905f228`). **Not
deployed.** Verified offline only (no provider, model or production request). The planner is unchanged: no
quality threshold, acceptance rule, provider budget or default generation behaviour moved. The frozen
`quality-holdout-v2` result stays a historical FAIL and was not touched.

Source: the Batch 2 feature and reliability audit (gaps G1–G9).

## 1. What changed

### A. Production refuses integrations the release was not evaluated with (G1)

`core/operational_config._production_unevaluated_integration_problems`, with `APP_ENV=production` only:

| Integration (allowance name) | Switched on by |
|---|---|
| `targeted_regeneration` | `TARGETED_REGENERATION_ENABLED=true` |
| `feedback_interpreter` | `AI_FEEDBACK_INTERPRETER_ENABLED=true` |
| `kiwi_flights` | `FLIGHT_PROVIDER=kiwi_mcp` or `KIWI_MCP_ENABLED=true` (either alone) |
| `airport_lookup` | `FLIGHT_AIRPORT_LOOKUP_PROVIDER` other than `not_connected` |

Any of these switched on stops startup with a message naming the variable (never a value) — a leftover or
mistyped dashboard value can no longer enable one quietly. The **only** override is
`PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS`, a comma-separated list naming each allowed integration:

- an unknown name (`all`, `*`, `true`, a typo) stops startup instead of being ignored;
- it relaxes this one check only: a switched-on interpreter still needs a connected provider and its key,
  and the beta access, cookies, cost limits and inventory gate are untouched;
- the allowed names are logged at startup (`production_integration_overrides`, fixed names or `none`).

`render.yaml` now writes the release's choices out: the four above off, plus `FLIGHT_PROVIDER`,
`ACCOMMODATION_PROVIDER` and `HOTEL_RATINGS_PROVIDER` = `not_connected` (section 3). No allowance is set.

### B. "Keep this place" is offered only where it does something (G2)

A keep matters only for applying requested changes, and an active keep blocks applying them entirely.
Where requested changes cannot be applied (`RegenerationReadiness.change_requests_applicable === false`,
today's production), the per-stop "Keep this place" button is not shown. A keep that is already stored is
still shown ("Kept") and can still be removed. Wording corrected everywhere it promised preservation:

- the button's accessible name is now "Keep this place: <place>. While a place is kept, no requested change
  is applied" (was "Keep <place> for future regeneration"); it starts with the visible text (WCAG 2.5.3);
- Traveler view's kept-places note says, where changes cannot be applied, that keeping a place changes
  nothing (it no longer tells the traveller to "remove the keep first");
- Developer view says an active keep blocks applying changes and never preserves a place through a
  regeneration (was "stored for future regeneration").

Unknown applicability (an older stored readiness) keeps the previous behaviour.

### C. No user action changes saved itinerary content without its revision (G3, G4)

Both defects were reproduced first by `tests/api/test_revision_integrity_batch2.py` failing with the
lineage check's `content_drift`.

- **AI promotion refresh** (`POST /trips/{id}/ai-candidate-promotions`, Developer view): it rewrote
  `ai_candidate_promotion_report` — revision content — with a fresh timestamp and no revision, so the saved
  workspace no longer equalled its branch head and a later regeneration would be refused with
  `BRANCH_STATE_CONFLICT`. It is now development-only: in production it answers exactly as an unknown path
  (404 `Not Found`, before the session or the trip is read), like the LangGraph shadow run.
  `GET /trips/{id}/ai-candidate-review` returns `promotion_refresh_available` (false in production); the
  frontend offers the button only when it is `true` (absent = not offered). In development the tool is
  unchanged and now says it records no version.
- **`POST /trips/{id}/generate` on a trip that already has a plan**: it rebuilt the whole itinerary under the
  unchanged "v1" label (whose snapshot is never re-captured) and spent a generation allowance. It now
  answers **409 `PLAN_ALREADY_GENERATED`** ("This trip already has an itinerary, so it was not built again.
  Nothing was changed.") before any job is admitted — nothing is stored or counted. "Already has a plan"
  (`generation_job_service.has_usable_generated_plan`) = a recorded version, an experience plan, a
  validation report and a resolved destination. A generation that failed, was interrupted, could not
  resolve its destination or produced no destination context can still be retried on the same trip (the
  frontend's "Try again" does this). If "Try again" meets a trip whose earlier attempt did finish (its
  result just could not be loaded), the frontend treats `PLAN_ALREADY_GENERATED` as "already built" and
  loads that plan instead of showing an error. Changing a plan stays `POST /regenerate`, with its own
  refusals.

### D. Disabled providers

Kiwi, the OurAirports lookup, transit, live stays, hotel ratings, booking and payment stay disabled or
unsupported. The manual-file providers (local files saved by hand, some labelled after platforms the project
never scrapes) are `not_connected` in production — see section 3 for why that changes nothing a traveller or
the acceptance tooling sees.

### E. Documentation and checks

- `.env.example` lists every setting (five `SCRAPED_HOTEL_RATINGS_*` were missing), enforced by
  `tests/core/test_env_example_sync.py`.
- `tests/models/test_frontend_types_contract.py`: every field `frontend/lib/types.ts` declares on a type
  that has a backend model of the same name (77 types; two explicit API-schema mappings) must exist on that
  model. It checks top-level keys only and never the reverse (types.ts is a partial mirror).

## 2. Production behaviour and rollback

| Situation | Behaviour |
|---|---|
| Service built from `render.yaml` (Blueprint synced) | starts; all seven pinned values off |
| Dashboard still holds e.g. `TARGETED_REGENERATION_ENABLED=true`, no sync | **refuses to start**, naming the variable |
| An integration is wanted on purpose | set it AND name it in `PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS` (an owner decision; it was not evaluated) |
| Frontend deployed before the backend | refresh button hidden (field absent); keep control as before until the backend reports applicability |

Rollback: every change is code or configuration; no migration, no stored-data change (all new fields are
optional, stored trips load unchanged). Reverting the commit restores the previous behaviour; the
`render.yaml` pins can stay (they are the code defaults, except the three `not_connected` providers, whose
only effect is the status label in section 3). There is no per-feature switch for B or C on purpose: they
remove a misleading control and two ways to corrupt saved state.

## 3. Why `not_connected` for the manual-file providers does not move the baseline

Checked offline before the change: one hermetic generation with the flight and accommodation providers as
`scraped_local` (no file, today's production) and once as `not_connected`. Identical: readiness, review codes
and their classification, every finding's severity and category, the scheduled places, and
`availability_reason` (`no_source_configured` for both). Different only: the reports' `status`
(`unavailable` → `not_connected`), `provider_coverage.flights` (same change) and the two inventory findings'
message text. Traveler view words flights and stays from `availability_reason`, so its text is unchanged;
the canary and evaluation tooling read neither field.

## 4. Developer-view branch workspace (documented limitation, G6)

Developer view (available to every signed-in user) can fork, list, activate and compare itinerary
branches. In the release configuration no regeneration can run, so a new branch is always an identical copy
of an existing revision; activating one swaps in that copy. The workspace is covered by backend API tests
(`tests/api/test_itinerary_fork_and_activation_api.py` and neighbours), not by a browser test. It is left as
is: it never changes saved content without a revision.

## 5. Disabled and unsupported integrations (release register)

| Integration | State in production | Why |
|---|---|---|
| Targeted regeneration + AI feedback interpreter | off; refused at startup | not evaluated; saved requests are kept, not applied |
| Kiwi flight search | off; refused at startup | terms of use unconfirmed (doc 33 §9) |
| OurAirports airport lookup | off; refused at startup | data not packaged, refreshed or monitored |
| Manual-file stays / flights / hotel ratings | `not_connected` | development aid only; no live source |
| Bookable stays, booking, payment | unsupported | no permitted free source (doc 33 §5) |
| Public transit | `not_connected` | no adapter |

## 6. Offline verification (2026-10-26, local; no provider, model or production request)

| Check | Result |
|---|---|
| `compileall` + hermetic `pytest` | 7,389 passed, 143 skipped (the gated PostgreSQL/Redis tests) |
| Integration equivalent (throwaway PostgreSQL 16 + Redis 7.4, both gates, empty database migrated to the single head `a94c0e7d2b61`, `pg_dump` 16) | 7,532 tests, 0 skipped; 2 failed on the first run (below), that file then 34/34 |
| Frontend unit tests, `tsc --noEmit`, lint | 208/208, clean, clean (re-run after the final label change) |
| Browser / E2E (all four viewports, offline backend) | 183 passed, 0 failed, 0 skipped |
| `npm run check:headers` | 13/13 |

Test changes, and why none weakens a check:

- Four tests asserted that `POST /generate` rebuilt a trip with a plan (200 / a second admitted job). They
  now assert the refusal and that nothing changed (`test_version_history`, `test_itinerary_branch_generate_reentry`
  — the fork-untouched invariant still asserted), or prove the same property on a case `/generate` still
  admits: a retry after a failed attempt still counts against the window (`test_generation_admission_p0`);
  the admission/deletion race on a trip without a plan (`test_data_deletion_p1a`, and the bystander step of
  the PostgreSQL race test).
- The feedback-interpreter production test also needs the new allowance; it additionally asserts the
  refusal without it. The startup summary's key-set test lists the new key.
- Browser tests located stops through the old Keep button label; they now use `data-testid="scheduled-stop"`
  with the same counts. The focus test no longer targets a control the release configuration does not show.

Intermittent, root-caused and fixed (a test-isolation defect already on `main`, not a production
regression): `test_alternate_mode_priority_r1.py::test_the_number_of_driving_requests_is_unchanged` failed
once, under load. Its runs named their SQLite route cache `cache-{id(self)}` in one shared directory; Python
reuses an object's `id()` after it is collected, so a later run could open an earlier run's cache and send
fewer routing requests than it counts. A probe forcing two runs to share an id reproduced it (second run: 0
requests). The three test files with that pattern now use a fresh name per run; no routing code changed.
Serving an identical request from the cache is the intended production behaviour.

The two first-run integration failures were one PostgreSQL race test (both parametrizations): the race itself
behaved as before (404, nothing stored, no lock left); its final step expected `/generate` on a bystander's
already-generated trip to be admitted, which is now correctly refused. The step now proves the bystander can
still generate with a new trip.

## 7. Remaining risks and prerequisites

- Before deploying: run the Blueprint sync, or check the Render dashboard for the four refused variables
  (doc 29 A0) — otherwise the new backend refuses to start (fail-closed, by design).
- The Keep control and the promotion refresh are hidden by data the backend sends; an old stored readiness
  without `change_requests_applicable` still shows the control (it is recomputed on the next write).
- `POST /generate` now refuses a trip with a plan: any external script that relied on regenerating through
  it must use `/regenerate`.
- Production is still `b559ff0` (M1); everything since `905f228` and this batch is undeployed. The open
  operational items of doc 29 (B4–B9) are unchanged.
