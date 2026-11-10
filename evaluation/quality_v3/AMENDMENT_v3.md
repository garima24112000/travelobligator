# Protocol amendment v3 — quality acceptance v3

| | |
| --- | --- |
| Amendment | Protocol version 2 → 3 |
| Date | 2026-11-09 |
| Approved by | The owner, by the dated confirmations recorded below (all given on 2026-11-09) |
| Made before any live run | **Yes.** No scenario of `quality-holdout-v3` has been executed, no itinerary generated, and no travel provider or model contacted. No result folder or audit log of this set exists. The runner's own records make this checkable: `run` refuses a result folder or audit log that already exists. |

## Why

Version 2 left `designated_reviewer` and `operator` as `null` and said that naming them is a further amendment
(docs/37 sections 9 and 10). Preflight refuses the run until both are recorded and are different people. This
amendment names them and records the owner confirmations that docs/37 section 9, item 5, requires before the run.

It is one of the changes docs/37 section 10 allows before the run ("naming the designated reviewer and the
execution operator"). It is not motivated by the outcome of any holdout. The historical `quality-holdout-v2`
review is complete and remains a historical FAIL; none of its scores was read for, or used in, this amendment.

## What changes in `protocol.json`

| Field | Version 2 | Version 3 |
| --- | --- | --- |
| `protocol_version` | `2` | `3` |
| `review.designated_reviewer` | `null` | `"Avinash Kumar Singh"` |
| `execution.operator` | `null` | `"Garima Prachi"` |
| `amended_on` | `"2026-10-28"` | `"2026-11-09"` |
| `amendments` | names `AMENDMENT_v2.md` and `protocol_v1.json` | also names this record and `protocol_v2.json` |
| `owner_confirmations` | absent | the date, the designation and the three confirmations below, pointing to this record |

Nothing else in the file changes. A test compares version 2 and version 3 field by field and allows only these.

## Owner confirmations (2026-11-09)

Each statement is the owner's, quoted word for word as given on 2026-11-09.

**Designation.**

> Designated independent reviewer: Avinash Kumar Singh.
> Execution operator: Garima Prachi.

**Cumulative request limits** (docs/37 section 9, item 5).

> I approve the cumulative maximum limits of 1,200 Geoapify requests and credits, 96 combined Groq/Gemini
> requests, 24 Open-Meteo, 36 Nager.Date, 24 Frankfurter and 0 Kiwi.

These equal `execution.cumulative_totals_at_most` of version 2, unchanged in version 3.

**Destination history** (docs/37 sections 2 and 9, item 5).

> I have verified that none of the ten quality_v3 destinations was used in a manual or production planning trip
> by anyone who worked on the planner.

**Reviewer eligibility** (docs/37 section 7).

> I confirm that Avinash Kumar Singh has not developed, tuned, or reviewed TravelObligator planner changes for
> this release. He has not seen any quality_v3 itinerary outputs, automated verdicts, or scenario identities.

The owner also noted that reviewing the historical `quality-holdout-v2` itineraries alone does not count as
reviewing planner changes.

**Execution window.**

> Keep the existing deadline of November 15, 2026 UTC.

The dates are not amended. That the run itself happens inside the window is checked by the runner on the run day.

### What the repository shows (checked 2026-11-09)

- No result folder or audit log of `quality-holdout-v3` exists, so there is no output or automated verdict of this
  evaluation for anyone to have seen.
- Every commit on every branch is by the operator's account. The only co-authors are AI coding assistants. The
  repository's only collaborator is that account, and the only reviews or comments on any pull request are from
  a deployment bot.
- The reviewer's name appears nowhere in the repository before this amendment.

This supports the owner's confirmation. It cannot prove it, because tuning or review outside the repository leaves
no trace there.

### What this amendment does not replace

- **The reviewer's own declaration.** After the run and before any review material exists, the reviewer still
  completes, signs and returns `INDEPENDENCE_DECLARATION.md` himself. `declare` locks it (docs/37 section 7). The
  owner's confirmation above is recorded as the owner's, not as the reviewer's.
- **Preflight on the run day.** It must pass, including the provider credentials in the operator's own shell.

## Digests

| | SHA-256 |
| --- | --- |
| Version 1 (`evaluation/quality_v3/protocol_v1.json`, byte-identical) | `19a39ecdc2ce5bf215b7e7166af18b539f10e170343af1b43f2de9b22e48650b` |
| Version 2 (`evaluation/quality_v3/protocol_v2.json`, kept byte-identical) | `23a9204663522bbd5a0420fa7affe493086ad0e2ae6148e1cc8572900c3a693a` |
| Version 3 (`evaluation/quality_v3/protocol.json`) | `79cd08315c12f28499ac911501046f64e56bb80e7c63c15a81c07f55a8da4060` |

## Unchanged

- The ten scenarios: set SHA-256 `ff521eb3e851a55073a8989cbf84e3dafa7a2f9cd3b02450853b4b2823047384`.
- The source fingerprint `032530cb1e9745392b59bcbf9d10b0ce8cdbccc37e5ac4ca6ee1b73e928a9153` (335 files). No file
  of `backend/app` (outside its tests), `backend/scripts` or `backend/requirements.txt` changed, so the planner and
  both evaluation scripts are byte-identical to version 2.
- The configuration digest `bdf9620130346a217e382f2ff4206a2de01dd85d8192d1bf5a3164769bffd965` and the frozen
  environment (equal to `render.yaml`).
- The base commit `5eda096a54ef993b460416c9816b443e0c6ec3b6`.
- The trip date (Monday 2026-11-23) and the execution window (2026-10-28 to 2026-11-15 UTC).
- The per-attempt limits, the attempt bound (12), the cumulative limits, the interruption rule and `max_resumes`.
- The rubric, the scale, the factual-error definition, the presentation seed, the reviewer requirements, and the
  three acceptance criteria with their thresholds.
- `REVIEWER_INSTRUCTIONS.md`, `INDEPENDENCE_DECLARATION.md` and `AMENDMENT_v2.md`.
- The earlier frozen holdouts (`quality-holdout`, `quality-holdout-v2`) and their historical results.
