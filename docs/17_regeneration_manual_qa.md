# 17. Regeneration Manual QA Checklist

## 1. Purpose

This checklist verifies the **current safety contract only**. It does not
verify itinerary quality, provider coverage, or travel usability — those
are covered elsewhere.

Ground rules to keep in mind while testing:

- Regeneration is not implemented yet. There is no regeneration engine.
- `POST /trips/{trip_id}/regenerate` is a hard-refusal endpoint. It always
  returns `409 REGENERATION_NOT_AVAILABLE` and makes no plan changes
  (see `docs/11_api_contracts.md` section 27).
- `GET /trips/{trip_id}/regeneration-attempts` returns an audit trail of
  **blocked attempts only**. It is never evidence that regeneration ran.
- `GET /trips/{trip_id}/regeneration-readiness` explains what regeneration
  would need and why it can't run yet — it never runs anything itself.

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

## 4. Manual frontend flow

Work through these steps in order on `http://localhost:3000`.

1. Generate a trip (default form values are fine — Lisbon, Portugal).
2. In the **Regeneration readiness** section, confirm:
   - status shows `blocked`
   - "Can regenerate" shows `No`
3. In the **Regeneration attempt audit** section, confirm the list is
   empty.
4. Click **Check backend refusal**.
5. Confirm the refusal message is shown (it comes from
   `POST /trips/{trip_id}/regenerate`, which always returns 409).
6. Confirm the audit section now shows exactly one blocked attempt.
7. Click **Check backend refusal** again.
8. Confirm the audit section now shows exactly two blocked attempts.
9. In **Request changes**, submit feedback: `Make this less packed`.
10. Confirm **Pending requested changes** total request count increases.
11. Confirm **Regeneration readiness** status still shows `blocked` and
    "Can regenerate" still shows `No`.
12. Confirm **Regeneration readiness** "Would create version" now shows
    `v2` — this is a readiness/audit hint only, not a created version.
13. Click **Check backend refusal** again.
14. Confirm a third blocked audit attempt appears, showing pending
    feedback count `1` and would-create-version `v2`.
15. Confirm **Regeneration readiness** "Current version" still shows `v1`.
16. Confirm **Version history** still contains only `v1`.
17. Confirm the itinerary (draft itinerary / scheduled experiences) has
    not changed from before step 4.
18. Copy the `trip_id`, reload the page, and load the same trip_id via
    "Load existing trip".
19. Confirm the three recorded attempts are still present after reload.

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

`POST /trips/{trip_id}/regenerate` may change **only**:

- `regeneration_attempts` (one new entry appended)
- `metadata.updated_at`

`POST /trips/{trip_id}/regenerate` must **not** change:

- `experience_plan`
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
related invariant tests). Use those tests as the source of truth if this
checklist and the code ever disagree.

---

## 7. Screenshot checklist

Capture these during a manual pass and attach them to the QA record:

1. Empty **Regeneration attempt audit** section (before any refusal
   check).
2. First blocked attempt in the audit section.
3. Second blocked attempt in the audit section.
4. **Regeneration readiness** section after submitting feedback (status
   still `blocked`, would-create-version `v2`).
5. Audit attempt recorded after feedback, showing pending feedback count
   `1` and would-create-version `v2`.
6. **Version history** section still showing only `v1`.
7. The same `trip_id` reloaded, with all recorded attempts still present.

---

## 8. Failure signs

Treat any of the following as a regression and stop shipping regeneration
work until it is fixed:

- `POST /regenerate` returns `200` instead of `409`.
- The refusal response body mentions `v2` anywhere.
- Version history shows a `v2` entry.
- The itinerary (scheduled experiences, ordering, daily plans) changes
  after clicking "Check backend refusal".
- Reading `GET /trips/{trip_id}/regeneration-attempts` appends a new
  attempt (the audit read endpoint must be read-only).
- `regeneration_readiness` changes as a result of calling
  `POST /regenerate`.
- The frontend reloads the entire plan (re-fetches destination context,
  experience plan, validation report, etc.) after clicking "Check backend
  refusal" — it should only refresh the attempt audit list.
- Any provider or LLM call happens during `POST /regenerate`.
