# Protocol amendment v4 — quality acceptance v3

| | |
| --- | --- |
| Amendment | Protocol version 3 → 4 |
| Date | 2026-11-10 |
| Approved by | The owner, by the written decisions of 2026-11-10 quoted below |
| Made before any live run | **Yes.** No scenario of `quality-holdout-v3` has been executed, no itinerary generated, and no travel provider or model contacted. No result folder or audit log of this set exists. |

## Why

Two things changed after version 3, and neither concerns how a plan is made or judged.

1. **GitHub-hosted CI stopped.** Since 2026-11-06, GitHub refuses to start any Actions job for this account
   because of a billing restriction. The owner has decided not to pay for GitHub Actions. docs/37 section 9,
   item 1 requires "the commit's CI passes", and with no hosted runner that can't be met as written. Treating a
   local run as "CI" without saying so would silently relax the requirement, so this amendment states the
   replacement explicitly.
2. **The execution window can't be met safely.** The window ended 2026-11-15 UTC. Before the run, the merge
   prerequisites need a verified local CI run, a security fix to the backend image, and the investigation of one
   browser-test failure. The owner approved a formal move of the dates instead of a shortened validation.

## Owner decisions (2026-11-10), quoted word for word

> **3. GitHub Actions replacement — APPROVE LOCAL VERIFICATION**
>
> Prepare Amendment 4 and the documented local merge procedure in docs/38.
>
> The alternative release gate must require all six jobs to pass on the exact release candidate using a
> committed, identifiable local runner.
>
> Preserve the frozen quality evaluation's scoring thresholds, scenarios, attempt limits and integrity rules.
> Any required changes to dates, protocols or fingerprints must be explicitly versioned and documented.
>
> Do not fabricate GitHub checks or represent local runs as GitHub-hosted CI.

> **6. Evaluation dates — APPROVE FORMAL EXTENSION**
>
> Prepare a formal amendment extending the quality_v3 execution window to November 16–30, 2026, and moving the
> trip start to Monday, December 7, 2026.
>
> Preserve all ten destination identities, evaluation methodology, acceptance thresholds and provider budgets.
>
> Follow the protocol's amendment and hash requirements. Do not silently modify frozen inputs or run the
> evaluation under an invalid protocol.

## What changes in `protocol.json`

| Field | Version 3 | Version 4 |
| --- | --- | --- |
| `protocol_version` | `3` | `4` |
| `dates.trip_start_date` | `"2026-11-23"` (Monday) | `"2026-12-07"` (Monday) |
| `dates.execution_window.first_day` | `"2026-10-28"` | `"2026-11-16"` |
| `dates.execution_window.last_day` | `"2026-11-15"` | `"2026-11-30"` (exactly seven days before the trip start) |
| `amended_on` | `"2026-11-09"` | `"2026-11-10"` |
| `amendments` | names amendments 2 and 3 | also names this record and `protocol_v3.json` |
| `owner_confirmations.execution_window` | the window the owner kept on 2026-11-09 | the same statement, marked as superseded by this amendment |
| `ci_evidence` | absent | the CI rule below |

Nothing else changes. A test compares version 3 and version 4 field by field and allows only these.

## The CI rule (`ci_evidence`)

docs/37 section 9, item 1 is met by one of two things.

**The six jobs of `ci.yml` on GitHub-hosted runners**, when GitHub runs them for this repository.

**Or, while GitHub-hosted runners are unavailable, one run of the committed local runner on the exact commit.**
The runner is `scripts/ci/local_ci.py` with `scripts/ci/local-runner.Dockerfile`, introduced in commit `aeefbdf`:

| File | SHA-256 |
| --- | --- |
| `local_ci.py` | `db8ff7ca47c33e574729c21b2f9d4dcf633fd83336345752a020d9763dcc5dd7` |
| `local-runner.Dockerfile` | `0909b283ff7dc282dfd2ca13c3dff394069989b27b23549fc96226c65b107966` |

The run must meet all of these:

- `overall_status` is `passed`: every gate step of all six jobs ran and exited 0 on that commit;
- its tool files are clean and equal to these digests;
- the commit SHA, the evidence digest (SHA-256 of the run's `SHA256SUMS`) and a sanitized summary are posted on the
  pull request;
- every earlier failed or aborted local run of the same change is disclosed there with it;
- the evidence digest is written in the merge commit's message, and the merge commit's tree equals the verified
  commit's tree, so exactly the verified content is merged. The digest isn't written into a tracked file: that would
  make a new commit that the evidence doesn't cover.

A local run is never presented as GitHub-hosted CI, and it never creates or edits a GitHub status or check. The
runner, its differences from GitHub's runner and its fail-closed rules are documented in
`docs/38_local_verification.md`. `scripts/ci` is outside the source fingerprint.

docs/37 section 10 limits what an amendment may change before the run. **This amendment adds one item to that
list, with the owner's approval:** the source of the CI evidence of section 9, item 1, as stated here. Nothing
else is added to it.

## Digests

| | SHA-256 |
| --- | --- |
| Version 1 (`protocol_v1.json`) | `19a39ecdc2ce5bf215b7e7166af18b539f10e170343af1b43f2de9b22e48650b` |
| Version 2 (`protocol_v2.json`) | `23a9204663522bbd5a0420fa7affe493086ad0e2ae6148e1cc8572900c3a693a` |
| Version 3 (`protocol_v3.json`, kept byte-identical) | `79cd08315c12f28499ac911501046f64e56bb80e7c63c15a81c07f55a8da4060` |
| Version 4 (`protocol.json`) | `79d7db63e7e0cd8ddeb784ecd61aabf46b67298533c38af3c265df4538932c5a` |

## Unchanged

- **The ten scenarios** and their destinations: set SHA-256
  `ff521eb3e851a55073a8989cbf84e3dafa7a2f9cd3b02450853b4b2823047384`. The dates live only in `protocol.json`, never
  in the scenario file.
- **The source fingerprint** `032530cb1e9745392b59bcbf9d10b0ce8cdbccc37e5ac4ca6ee1b73e928a9153` (335 files). No
  file of `backend/app` (outside its tests), `backend/scripts` or `backend/requirements.txt` changed. The runner
  reads the dates from the protocol.
- **The configuration digest** `bdf9620130346a217e382f2ff4206a2de01dd85d8192d1bf5a3164769bffd965`, and the base
  commit.
- **Limits.** The per-attempt limits, the attempt bound (12), the cumulative limits approved on 2026-11-09, the
  interruption rule and `max_resumes`.
- **Review and acceptance.** The rubric, the scale, the factual-error definition, the presentation seed, the
  reviewer and operator and their requirements, and the three acceptance criteria with their thresholds.
- **Owner confirmations.** The budget and destination-history confirmations of 2026-11-09.
- **Documents.** `REVIEWER_INSTRUCTIONS.md`, `INDEPENDENCE_DECLARATION.md`, and amendments 2 and 3.
- **The earlier frozen holdouts** and their historical results.

## Weather

With the trip start on 2026-12-07 and a window ending 2026-11-30, a run late in the window may have a forecast
for the first trip days. A run early in it records weather as unavailable, the accepted `WEATHER` data-coverage
code. That holds for all ten scenarios of one run, and the run date is recorded, as before.
