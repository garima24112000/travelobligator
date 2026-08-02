# Manual Anthropic Shadow-Mode Smoke Test (Step 161C)

## What this is

`backend/scripts/manual_anthropic_shadow_smoke.py` is a **manual, dev-only**
smoke test. It is not part of the application runtime: it is never imported
by `app.main`, by any backend service, or by the automated `pytest` suite,
and it never runs during `python -m compileall`, normal app startup, or CI.
It only runs when a developer explicitly executes the file by hand.

Its purpose is narrow: confirm that the Anthropic/Claude-backed AI
candidate proposal adapter (Step 161A) can run end to end through the
config-gated shadow-mode integration (Step 161B) -- generating a real trip,
storing an `AICandidateProposalBatch` and a `CandidateGroundingBatch` -- and
that doing so still does not change the itinerary that gets scheduled.

**This script may call the real Anthropic API and may incur cost.** Only
run it when you intend that.

**Claude Code (the CLI coding agent) is not used by the backend anywhere,
including here.** This script, like `AnthropicAICandidateProposalProvider`,
only ever talks to Claude through the official `anthropic` Python SDK's
Messages API, and only through the app's normal HTTP surface
(`POST /trips`, `POST /trips/{trip_id}/generate`).

## Required environment variables

All three must be set, or the script exits gracefully without calling
anything:

```bash
ANTHROPIC_API_KEY=sk-ant-...
AI_CANDIDATE_PROPOSAL_PROVIDER=anthropic
AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=true
```

Optional:

```bash
ANTHROPIC_MODEL=claude-...   # defaults to Settings.anthropic_model if unset
```

## How to run

From the repo root:

```bash
ANTHROPIC_API_KEY=sk-ant-... \
AI_CANDIDATE_PROPOSAL_PROVIDER=anthropic \
AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=true \
python backend/scripts/manual_anthropic_shadow_smoke.py
```

The script creates one small trip request for a real destination (Lisbon,
Portugal), calls normal generation, then prints a short summary and a
final `RESULT: PASS` or `RESULT: FAIL` line. It never prints the raw Claude
API response, the raw prompt sent to Claude, or any API key/secret, and it
never writes any file containing either of those.

## What a PASS result means

- Generation completed successfully through the normal pipeline.
- Both `ai_candidate_proposal_batch` and `candidate_grounding_batch` were
  populated with validated artifacts (Step 160C's storage contract).
- The proposal batch's status was one of `completed` / `rejected` /
  `not_connected`, and if `completed`, it contained at least one proposal
  with no forbidden factual field (no price, rating, opening hours, route
  time, booking URL, review count, ticket price, availability, safety
  score, coordinates, provider name, or provider place id on the proposal
  itself).
- The grounding batch was present and internally validated, with no
  forbidden factual field of its own.
- `experience_plan` still exists, and no scheduled experience is sourced
  directly from either batch.
- `provider_coverage` / `data_sources_used` gained no Anthropic/AI-related
  source name.

## What a PASS result does not mean

- It does **not** mean AI-proposed candidates are scheduled into the trip.
  They are stored for inspection only (shadow mode); scheduling still
  comes exclusively from real provider/open-data candidates.
- It does **not** mean a proposal is a fact. Every `AICandidateProposal` is
  still just an idea with a name and a one-line rationale -- it must be
  independently grounded against real provider/open-data evidence before
  it could ever be treated as real, and grounding alone still does not
  schedule it.
- It does **not** mean `CandidateQualityService` consumes AI-proposed or
  AI-grounded candidates. It still only scores `destination_context`'s
  existing provider-backed candidates.
- It does **not** mean the frontend displays AI suggestions yet. No
  frontend file is touched by Step 161B or Step 161C.

## How to turn shadow mode back off

```bash
unset AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED
AI_CANDIDATE_PROPOSAL_PROVIDER=not_connected
```

With shadow mode disabled (the default), generation behaves exactly as it
did before Step 161B: `ai_candidate_proposal_batch` and
`candidate_grounding_batch` stay `None`.

## Troubleshooting

| Observation | Meaning |
| --- | --- |
| Script exits immediately saying `ANTHROPIC_API_KEY is not set` | The script never ran anything; set the key to proceed. |
| `proposal_status: not_connected` | Either `AI_CANDIDATE_PROPOSAL_PROVIDER` was not `anthropic`, or the key wasn't picked up by `Settings` -- check config, not the network. |
| `proposal_status: rejected` | Claude's response failed `AICandidateProposal`/`AICandidateProposalResult` validation (e.g. a forbidden factual claim, malformed tool output, or the API call itself failed). |
| `proposal_status: completed` but `grounding_status: rejected` | The proposal(s) did not match any real supplied provider/open-data candidate -- an honest "we couldn't verify this idea," not an error. |
| `proposal_status: completed` and `grounding_status: completed`/`partial` | Both batches were stored as shadow artifacts only -- still not scheduled, still not a fact, still not consumed by candidate quality or the frontend. |
