# 17. Regeneration Manual QA Checklist

## 1. Purpose

This checklist verifies the **current safety contract only**. It does not
verify itinerary quality, provider coverage, or travel usability — those
are covered elsewhere.

Ground rules to keep in mind while testing:

- **Real regeneration exists now (Step 174C, lifecycle completed in Step
  174D), but only for one narrow, deterministic MVP scope**:
  `{"confirm": true}` with at least one *pending* (unapplied) feedback
  event and zero active locks. That one case reruns the affected
  planning stages, creates a new plan version, marks the feedback it
  used as applied, and returns `200`. Every other request shape -- no
  body, `{"confirm": false}`, active locks present, no pending feedback,
  or feedback with no derivable affected stage -- still refuses with a
  `409` and makes no plan changes, exactly as before.
- **No request body** (or an explicit `{"confirm": false}`) preserves the
  exact original refusal: `409 REGENERATION_NOT_AVAILABLE`, unchanged
  since before Step 174B (see `docs/11_api_contracts.md` section 27). Any
  existing script or integration that never sends a body keeps working
  exactly as before.
- **`{"confirm": true}`** (Step 174B guardrails, Step 174C/174D success
  path) runs a small guardrail chain before ever touching the plan:
  blocked by active locks, blocked by no *pending* feedback, blocked
  because no affected stage could be derived from the pending feedback /
  no plan was ever generated, or -- only once none of those apply -- a
  real regeneration. See section 4a below for the exact matrix.
- Regeneration **never reruns the whole pipeline and never calls
  LangGraph or an AI/LLM provider** -- it reruns only the specific
  deterministic stages the pending feedback's own classification already
  named (e.g. `experience_plan`/`validation` for a pacing complaint),
  via the same `PlanningOrchestrator` stage methods `POST /generate`
  itself uses.
- **A successful regeneration marks the feedback it used as applied**
  (`FeedbackEvent.applied_at`/`applied_in_version`/
  `handling_status="applied"`) -- Step 174D closes the gap Step 174C left
  open. Applied feedback stays in `feedback_history` (nothing is
  deleted) but no longer counts as "pending" anywhere:
  `pending_feedback_summary`, `regeneration_readiness`, and
  `plan_diff_preview` are all recomputed immediately after, so a repeat
  `{"confirm": true}` call with no new feedback submitted since correctly
  refuses with `REGENERATION_NO_PENDING_FEEDBACK` rather than creating
  another version from the same feedback. Submitting genuinely new
  feedback after a success creates a fresh pending event that a further
  regeneration can act on normally (e.g. a third version after a second
  success).
- `GET /trips/{trip_id}/regeneration-attempts` returns an audit trail of
  **every** attempt, blocked or applied. It is the only place a `200`
  regeneration is confirmed to have actually run (`status: "applied"`).
- `GET /trips/{trip_id}/regeneration-readiness` explains what regeneration
  would need and why it can or can't run -- it never runs anything
  itself. As of Step 174D, `can_regenerate`/`status: "ready"` are `true`
  only for the exact same MVP scope `POST /trips/{trip_id}/regenerate`
  itself supports (generated plan + pending feedback + zero active locks
  + a real derivable affected stage); every other combination stays
  `false`/`"blocked"` (see docs/14_backend_architecture.md section 78).
  `plan_diff_preview.regeneration_available` follows the identical rule.

If any step in this checklist contradicts the rules above, treat it as a
regression, not a new feature. See section 8 for explicit failure signs.

---

## 2. Prerequisites

Start the backend:

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --app-dir backend
```

Start the frontend:

```bash
cd frontend
npm run dev
```

Open the app:

```text
http://localhost:3000
```

---

## 3. Backend automated checks

Run these before any manual pass. All three must pass before manual QA is
meaningful.

```bash
python -m compileall backend/app
python -m pytest
cd frontend && npm run lint
```

---

## 4. Manual frontend flow (Step 174E: real "Regenerate from feedback" button)

Work through these steps in order on `http://localhost:3000`. As of Step
174E the frontend sends real `{"confirm": true}` requests through its own
UI -- there is no separate "check the refusal" action any more; the same
**Regenerate from feedback** button in the **Regeneration readiness**
section is the one real regenerate control, gated on
`readiness.can_regenerate`.

1. Generate a trip (default form values are fine — Lisbon, Portugal).
2. In the **Regeneration readiness** section, confirm:
   - status shows `blocked`, "Can regenerate" shows `No`
   - the **Regenerate from feedback** button is disabled (greyed out),
     with a tooltip/helper text explaining feedback must be pending and
     no locks may be active
3. In the **Regeneration attempt audit** section, confirm the list is
   empty.
4. In **Request changes**, submit feedback: `Make this less packed`.
5. Confirm **Pending requested changes** total request count increases,
   and its own text now points to the **Regeneration readiness** section
   below rather than offering a second, separate disabled button.
6. Confirm **Regeneration readiness** now shows:
   - status `ready`, "Can regenerate" `Yes`
   - "Would create version" `v2`
   - the **Regenerate from feedback** button is now enabled
7. Create an active lock on any scheduled experience (via a "Keep this
   place" control in the draft itinerary). Confirm **Regeneration
   readiness** flips back to status `blocked`, "Can regenerate" `No`, the
   button disabled again, and "Blocked by" names the active lock
   specifically (not "no pending feedback").
8. Remove that lock. Confirm readiness returns to status `ready`, "Can
   regenerate" `Yes`, and the button re-enables.
9. Click **Regenerate from feedback**.
10. Confirm a green "Regeneration applied" panel appears showing
    `v1 → v2` and `Changed: experience_plan, validation` (or whatever the
    submitted feedback actually classified to) -- values read directly
    from the response, never invented by the frontend.
11. Confirm the whole page refreshes: **Version history** now shows a new
    `v2` entry, **Plan diff preview** and **Regeneration readiness** both
    now reflect `current_version: v2` and (since the feedback used is now
    applied) `status: blocked`/"Can regenerate" `No` again — regeneration
    does not stay "available" for feedback it already consumed.
12. Confirm the itinerary (numbered stops, movement rows, map/route
    paths) is still rendered normally after the refresh -- Section
    172/173 behavior is visually unaffected by regeneration.
13. Confirm **Regeneration attempt audit** now shows one new entry with
    status `applied`.
14. Click **Regenerate from feedback** again immediately (no new feedback
    submitted). Confirm the button is disabled (readiness already shows
    "Can regenerate" `No`, `pending_feedback_count: 0`) -- there is
    nothing to click that would create a `v3`.
15. Submit new feedback (e.g. `More museums please`). Confirm readiness
    becomes `ready` again and the button re-enables, current_version
    still showing `v2`, would-create-version `v3`.
16. Copy the `trip_id`, reload the page, and load the same trip_id via
    "Load existing trip". Confirm version history, the applied feedback
    event's status, and the regeneration attempt audit are all still
    present after reload.

---

## 4a. Confirm=true guardrail matrix (Step 174B guardrails + Step 174C/174D success and repeat-safety; Step 174E frontend)

As of Step 174E, the frontend's **Regenerate from feedback** button
always sends `{"confirm": true, "scope": "affected_stages"}` -- section 4
above is the frontend walkthrough of this same matrix. This section
gives the exact request/precondition/result table for anyone testing via
curl directly, or verifying the frontend's behavior against the raw API.
Using the same `TRIP_ID` from section 5 below:

| Request body | Precondition | Result |
|---|---|---|
| *(no body)*, or `{"confirm": false}` | any | `409 REGENERATION_NOT_AVAILABLE` (original refusal, unchanged) |
| `{"confirm": true}` | at least one active lock | `409 REGENERATION_BLOCKED_BY_LOCKS`, message says to remove active locks first |
| `{"confirm": true}` | zero active locks, no *pending* feedback (none ever submitted, **or every event already applied by a prior regeneration**) | `409 REGENERATION_NO_PENDING_FEEDBACK`, message says feedback is required first |
| `{"confirm": true}` | zero active locks, pending feedback exists, but no affected stage could be derived (e.g. unclassified `general_feedback`), or no plan was ever generated | `409 REGENERATION_NOT_AVAILABLE` -- a safe fallback, not the MVP success path |
| `{"confirm": true}` | zero active locks, pending feedback exists, at least one real affected stage derived | **`200`, `status: "applied"`** -- real regeneration: affected stages rerun, a new version created, the feedback used marked applied, `pending_feedback_summary`/`plan_diff_preview`/`regeneration_readiness` recomputed |

Every refusal row still appends exactly one `RegenerationAttempt` with
`status: "blocked"` and a `reason_code` matching the error actually
returned, and still makes no plan changes or new version. The success
row appends exactly one `RegenerationAttempt` with `status: "applied"`
and `reason_code: "REGENERATION_APPLIED"` instead. The third row (no
pending feedback) is what a repeat `{"confirm": true}` call hits
immediately after a success, as long as no *new* feedback was submitted
in between -- see the repeat-safety checks below.

```bash
# Blocked by an active lock (after creating one via POST /trips/$TRIP_ID/locks):
curl -s -i -X POST "$BASE/trips/$TRIP_ID/regenerate" \
  -H "Content-Type: application/json" \
  -d '{"confirm": true}'
# -> 409, code REGENERATION_BLOCKED_BY_LOCKS

# No pending feedback yet:
curl -s -i -X POST "$BASE/trips/$TRIP_ID/regenerate" \
  -H "Content-Type: application/json" \
  -d '{"confirm": true}'
# -> 409, code REGENERATION_NO_PENDING_FEEDBACK (before any feedback is submitted)

# Real regeneration (feedback submitted, no locks):
curl -s -i -X POST "$BASE/trips/$TRIP_ID/regenerate" \
  -H "Content-Type: application/json" \
  -d '{"confirm": true}'
# -> 200, {"data": {"status": "applied", "previous_version": "v1",
#           "current_version": "v2", "changed_sections": [...], ...}}
```

### Real regeneration success checks (Step 174C/174D)

After the last curl call above returns `200`, verify:

- `data.status` is `"applied"`, `data.previous_version` is `"v1"`,
  `data.current_version` is `"v2"`.
- `data.changed_sections` lists only the stages the submitted feedback's
  classification actually named (e.g. `["experience_plan", "validation"]`
  for "Make this less packed") -- never every stage, never an empty list.
- `data.preserved_sections` is `[]` (locks are disallowed entirely for
  this MVP scope, so nothing is ever reported preserved).
- `data.applied_feedback_event_ids` lists the pending feedback event
  id(s) that were present at the time of the call.
- `GET /trips/$TRIP_ID` shows `version_history` with a new `v2` entry
  (`created_by: "user_feedback"`, `changed_sections` matching the
  response) and `metadata.current_version == "v2"`.
- `GET /trips/$TRIP_ID` shows `plan_diff_preview.from_version == "v2"`,
  changed from before the call.
- `GET /trips/$TRIP_ID/regeneration-readiness` shows
  `current_version == "v2"`, changed from before the call.
- `GET /trips/$TRIP_ID/regeneration-attempts` shows exactly one new
  attempt with `status: "applied"`.
- No new attraction, restaurant, hotel, flight, price, rating, route,
  travel time, distance, opening hour, description, or booking link
  appears anywhere in the response or the updated `experience_plan` that
  wasn't already sourced from a real provider before this call -- the
  rerun stages only re-select from data already fetched, they never
  invent anything new.

### Applied-feedback lifecycle checks (Step 174D)

Still using the same `TRIP_ID`, `GET /trips/$TRIP_ID` and inspect
`feedback_history`:

- The feedback event(s) listed in `data.applied_feedback_event_ids` now
  have `applied_at` set (non-null) and `applied_in_version == "v2"`, and
  `handling_status == "applied"`.
- The event is still present in `feedback_history` -- it is never
  deleted.
- `pending_feedback_summary.total_feedback_items` is now `0` and
  `pending_feedback_summary.affected_stages` is `[]`, even though
  `feedback_history` itself is non-empty -- "pending" excludes applied
  feedback, it does not mean "feedback_history is empty."

### Repeat-safety checks (Step 174D)

Immediately call `POST /trips/$TRIP_ID/regenerate` again with
`{"confirm": true}` and no new feedback submitted in between:

- Response is `409`, `code == "REGENERATION_NO_PENDING_FEEDBACK"`.
- `GET /trips/$TRIP_ID` shows `version_history` still has exactly 2
  entries (`v1`, `v2`) -- **no `v3` is created**.
- `GET /trips/$TRIP_ID/regeneration-attempts` shows a new `blocked`
  attempt appended (three total: the two earlier `applied`/refusal
  attempts from this flow, plus this one).

Then submit a *new* feedback event and call
`POST /trips/$TRIP_ID/regenerate` with `{"confirm": true}` once more:

- Response is `200` again, with `previous_version: "v2"`,
  `current_version: "v3"` -- new feedback is regeneratable normally.

---

## 5. Manual API curl flow

Set a base URL for convenience:

```bash
BASE=http://localhost:8000
```

Create a trip:

```bash
curl -s -X POST "$BASE/trips" \
  -H "Content-Type: application/json" \
  -d '{
    "destination_scope": "single_city",
    "primary_destination": "Lisbon, Portugal",
    "origin_city": "New York",
    "start_date": "2026-08-10",
    "end_date": "2026-08-12",
    "travelers_count": 2,
    "travel_group_type": "couple",
    "pace": "balanced"
  }'
```

Extract `data.trip_id` from the response, then:

```bash
TRIP_ID=<paste trip_id here>
```

Generate the plan:

```bash
curl -s -X POST "$BASE/trips/$TRIP_ID/generate"
```

Check readiness:

```bash
curl -s "$BASE/trips/$TRIP_ID/regeneration-readiness"
```

Submit feedback:

```bash
curl -s -X POST "$BASE/trips/$TRIP_ID/feedback" \
  -H "Content-Type: application/json" \
  -d '{"feedback_text": "Make this less packed"}'
```

Attempt regeneration:

```bash
curl -s -i -X POST "$BASE/trips/$TRIP_ID/regenerate"
```

Read the audit trail:

```bash
curl -s "$BASE/trips/$TRIP_ID/regeneration-attempts"
```

Read the full trip state:

```bash
curl -s "$BASE/trips/$TRIP_ID"
```

### Expected checks

- `POST /trips/{trip_id}/regenerate` returns HTTP `409`.
- The error `code` in the response body is `REGENERATION_NOT_AVAILABLE`.
- The raw response body does not contain the substring `v2`.
- `regeneration_attempts` length increments by exactly 1 per
  `POST /regenerate` call.
- `metadata.current_version` (via `GET /trips/{trip_id}`) remains `v1`.
- `version_history` length remains `1`.
- No entry in `version_history` has `version_label` equal to `v2`.

---

## 6. State mutation checklist

### 6a. Every refusal request shape (unchanged since Step 174B)

Applies to no body, `{"confirm": false}`, blocked-by-locks,
no-pending-feedback, and the "no affected stage derivable" fallback.

`POST /trips/{trip_id}/regenerate` may change **only**:

- `regeneration_attempts` (one new entry appended, `status: "blocked"`)
- `metadata.updated_at`

`POST /trips/{trip_id}/regenerate` must **not** change:

- `experience_plan`
- `route_aware_sequencing_report`
- `travel_time_buffer_report` (or its route geometry)
- `destination_context`
- `validation_report`
- `provider_coverage`
- `route_feasibility_context`
- `feedback_history`
- `pending_feedback_summary`
- `user_locks`
- `version_history`
- `plan_diff_preview`
- `regeneration_readiness`

This is enforced today by
`backend/app/tests/api/test_regenerate_refusal.py`
(`test_regenerate_does_not_change_other_sections`,
`test_regenerate_deep_snapshot_only_allows_attempts_and_updated_at`, and
related invariant tests, plus the Step 174B/174C guardrail-path tests
`test_regenerate_confirm_true_with_active_lock_is_blocked_by_locks`,
`test_regenerate_confirm_true_with_no_feedback_is_blocked`,
`test_regenerate_confirm_true_with_unclassified_feedback_only_refuses_safely`,
and `test_regenerate_failed_stage_rerun_does_not_create_version_and_records_failed_attempt`).
Use those tests as the source of truth if this checklist and the code
ever disagree.

### 6b. The one real success request shape (Step 174C mutation, Step 174D lifecycle)

Applies only to `{"confirm": true}` with zero active locks, *pending*
feedback that classifies to at least one affected stage, and a plan that
has already been generated.

`POST /trips/{trip_id}/regenerate` may change:

- Only the stages named in the response's `changed_sections` (today
  always a subset of `experience_plan`/`route_feasibility_report`/
  `route_aware_sequencing_report`/`travel_time_buffer_report`/
  `provider_coverage`/`validation_report`, exactly as reran by
  `PlanningOrchestrator.rerun_affected_stages`) -- never a stage outside
  that list.
- `version_history` (exactly one new entry appended), `metadata.current_version`
- The `applied_at`/`applied_in_version`/`handling_status` fields on
  exactly the pending feedback event(s) used (`feedback_history`'s
  length and every other field on those events, and every other event's
  fields, stay unchanged -- Step 174D never deletes or rewrites feedback
  text/classification, only marks it applied).
- `pending_feedback_summary`, `plan_diff_preview`, `regeneration_readiness`
  (all three recomputed, same as every other write path in this router)
- `regeneration_attempts` (one new entry appended, `status: "applied"`)
- `metadata.updated_at`

`POST /trips/{trip_id}/regenerate`'s success path must **not** change:

- Any feedback event's `feedback_text`/`feedback_type`/`affected_stages`/
  `interpretation`/`change_summary`/`follow_up_question`/`created_at` --
  only the three applied-lifecycle fields above are ever set, and only
  on the event(s) actually used.
- Any feedback event that was already applied by an earlier regeneration
  (it is never re-applied or re-marked).
- `user_locks` (this path never even reaches locked-item logic, since
  locks are disallowed entirely for this scope)
- `destination_context`, `traveler_profile`, `trip_strategy`,
  `stay_transport` -- unless the feedback's own classification actually
  named those stages (a pacing/removal/interest complaint like the QA
  examples above never does)
- Anything about `route_geometry` beyond what a real, unmodified
  `RouteFeasibilityService`/`TravelTimeBufferService`/`OSRMRoutingAdapter`
  call already produces -- the rerun never computes, fabricates, or
  infers a route, distance, duration, price, rating, or booking link
  itself.

This is enforced by the Step 174C/174D sections of
`backend/app/tests/api/test_regenerate_refusal.py`
(`test_regenerate_confirm_true_with_feedback_and_zero_locks_returns_200`,
`test_regenerate_success_calls_rerun_affected_stages_with_derived_stages`,
`test_regenerate_success_does_not_call_langgraph_or_full_generation`,
`test_regenerate_success_creates_exactly_one_new_version`,
`test_regenerate_success_recomputes_plan_diff_preview_and_readiness`,
`test_regenerate_success_records_applied_attempt`,
`test_regenerate_success_marks_used_feedback_as_applied`,
`test_regenerate_success_pending_feedback_summary_reflects_zero_pending`,
`test_repeated_regenerate_after_success_refuses_with_no_pending_feedback`,
`test_regenerate_new_feedback_after_success_is_regeneratable_again`, and
`test_regenerate_failed_stage_rerun_does_not_mark_feedback_applied`).

---

## 7. Screenshot checklist (Step 174E)

Capture these during a manual pass and attach them to the QA record:

1. **Regeneration readiness** section on a freshly generated trip:
   status `blocked`, button disabled, empty **Regeneration attempt
   audit**.
2. **Regeneration readiness** section after submitting feedback: status
   `ready`, "Can regenerate" `Yes`, would-create-version `v2`, button
   enabled.
3. **Regeneration readiness** section after creating an active lock on
   top of that pending feedback: status `blocked` again, "Blocked by"
   naming the active lock, button disabled.
4. The same section after removing that lock: status `ready` again,
   button re-enabled.
5. The green "Regeneration applied" panel immediately after clicking
   **Regenerate from feedback**, showing `v1 → v2` and the real
   `changed_sections`.
6. **Version history** section showing the new `v2` entry.
7. **Regeneration readiness**/**Plan diff preview** immediately after
   that success, both back to `blocked`/"not available" (feedback used
   is now applied, not pending).
8. **Regeneration attempt audit** showing the new `applied` entry.
9. The itinerary (numbered stops, movement rows, map/route paths)
   rendering normally right after the refresh.
10. The button disabled again on an immediate repeat click attempt (no
    new feedback submitted) -- confirm no `v3` appears in version
    history.
11. The same `trip_id` reloaded via "Load existing trip", with version
    history, the audit trail, and the applied feedback event's state all
    still present.

---

## 8. Failure signs

Treat any of the following as a regression and stop shipping regeneration
work until it is fixed:

- The refusal response body (for any request shape that should refuse)
  mentions `v2` anywhere.
- Version history shows a `v2` entry when it shouldn't (any request
  shape other than the one real success case in section 4a).
- The itinerary (scheduled experiences, ordering, daily plans) changes
  after a **Regenerate from feedback** click that refuses (button somehow
  clickable while blocked, or a genuine backend refusal response).
- Reading `GET /trips/{trip_id}/regeneration-attempts` appends a new
  attempt (the audit read endpoint must be read-only).
- `regeneration_readiness`/`plan_diff_preview` change as a result of a
  `POST /regenerate` call that refused.
- The frontend reloads the entire plan (re-fetches destination context,
  experience plan, validation report, etc.) after a **Regenerate from
  feedback** click that refuses -- a refusal should only refresh the
  attempt audit list, never the rest of the plan. (A *success* is the one
  case that correctly triggers a full reload -- see section 4 step 11.)
- The **Regenerate from feedback** button is ever clickable
  (not `disabled`) while `readiness.can_regenerate` is `false`.
- A refusal response's `code`/`message` is not shown to the user, or the
  frontend implies the plan changed when a refusal occurred.
- Any provider or LLM call happens during `POST /regenerate`, for any
  request shape, including the real success path -- the rerun stages
  only re-select from data already fetched by a real provider; they
  never call one directly, and never call Groq/Anthropic/OpenAI/LangGraph.
- `POST /regenerate` with `{"confirm": true}` returns `200` for any
  precondition **other than** "zero active locks, feedback exists, at
  least one affected stage derivable, plan already generated" -- every
  other precondition must still return `409`.
- `POST /regenerate` with `{"confirm": true}` returns anything other
  than `200` when all four of that precondition's parts are true, or
  returns `200` without actually creating a new version.
- The real success path's `changed_sections` includes a stage the
  submitted feedback's own classification didn't name, or omits one it
  did name.
- The real success path's `preserved_sections` is ever non-empty (locks
  are disallowed entirely for this scope, so nothing should ever be
  reported preserved).
- The real success path fabricates any attraction, restaurant, hotel,
  flight, price, rating, route, route geometry, travel time, distance,
  opening hour, description, or booking link not already sourced from a
  real provider before the call.
- A failed stage rerun (simulated or real) still creates a new version,
  or returns `200`.
- `{"confirm": true}` with an active lock does not return
  `REGENERATION_BLOCKED_BY_LOCKS`, or that code appears when there are
  zero active locks.
- `{"confirm": true}` with no *pending* feedback does not return
  `REGENERATION_NO_PENDING_FEEDBACK`, or that code appears when pending
  (unapplied) feedback actually exists.
- Any `{"confirm": true}` outcome **other than the one real success case**
  changes `experience_plan`, `route_aware_sequencing_report`,
  `travel_time_buffer_report` (or its route geometry), `validation_report`,
  `provider_coverage`, or creates a `v2` in `version_history` -- every
  guardrail refusal (locks, no feedback, no derivable stage, failed
  rerun) must still leave the plan byte-for-byte unchanged.
- `regeneration_readiness.can_regenerate`/`plan_diff_preview.regeneration_available`
  are `true` for a request shape **other than** the exact MVP scope
  (generated plan + pending feedback + zero active locks + derivable
  stage), or stay `false` **for** that exact scope -- Step 174D makes
  both honestly track this one scope, not "always false" and not
  "always false except after a real 200."
- A successful regeneration does **not** set `applied_at`/
  `applied_in_version`/`handling_status: "applied"` on the feedback
  event(s) it used, or sets those fields on a *different* event than the
  one(s) reported in `applied_feedback_event_ids`.
- A failed stage rerun (simulated or real) still marks any feedback
  event applied.
- `pending_feedback_summary` still reports the just-applied feedback as
  pending after a successful regeneration (`total_feedback_items`/
  `affected_stages` should reflect zero pending items until new feedback
  is submitted).
- A repeat `{"confirm": true}` call right after a success (no new
  feedback submitted since) returns anything other than `409
  REGENERATION_NO_PENDING_FEEDBACK`, or creates a `v3`.
- Submitting genuinely new feedback after a success and calling
  `{"confirm": true}` again does *not* succeed (it should -- new
  feedback is its own fresh pending event).
- Any frontend UI copy claims regeneration "is not implemented" or
  frames the button as only a refusal check -- as of Step 174E, real
  regeneration exists and the button applies it whenever
  `readiness.can_regenerate` is `true`.
- Numbered stops (Step 172B), movement rows (Step 172D), provider-backed
  route paths (Step 173), or the AI-promoted badge (Step 170) stop
  rendering, render incorrectly, or reorder after a regeneration success
  refresh -- the refresh must only reflect real backend state, never
  reorder or reinterpret it on the frontend.
- The frontend computes or displays a diff, changed-section list, route
  fact, or travel fact that did not come directly from
  `RegenerateResponseData` or a `GET /trips/{trip_id}` refresh.
