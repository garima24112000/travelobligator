# Protocol amendment v2 — quality acceptance v3

| | |
| --- | --- |
| Amendment | Protocol version 1 → 2 |
| Date | 2026-10-28 |
| Approved by | The owner (decision "Option A", after the pre-commit protocol review of 2026-10-28) |
| Made before any live run | **Yes.** No scenario of `quality-holdout-v3` has been executed, no itinerary generated, and no travel provider or model contacted. No result folder or audit log of this set exists. The runner's own records make this checkable: `run` refuses a result folder or audit log that already exists. |

## Why

A protocol review before the first commit found three defects in version 1:

1. **Understated worst-case requests.** Version 1 gave 10 × the per-scenario caps as the "whole run, at most" totals.
   With two interruption-only resumes, up to 12 attempts can send requests (section "Budgets" below), so the true
   maximum was 20 % higher. Nothing enforced any total across sessions.
2. **Interrupted-attempt evidence was lost on resume.** The per-scenario request counts and records of an
   interrupted attempt were overwritten when its scenario was run again. That left no record of what the
   interruption rule had been applied to.
3. **Scoring did not verify the review material.** `score` checked the run folder's manifest, but not the review
   folder or the packet-to-scenario mapping. Nothing outside the folders recorded any manifest digest.

The review also found that the reviewer's independence from the execution operator was implied but neither
required nor recorded. The console shows automated verdicts to the operator during the run.

None of these changes is motivated by the outcome of an earlier holdout. They concern only evidence, budget
accounting and review integrity, and they change nothing about how a plan is made or judged.

## Source identity

| | Version 1 | Version 2 |
| --- | --- | --- |
| Source fingerprint (335 files) | `b493b1c1bcae9fa16adfbbc5dcc9360a2620d42d9e3f2781cebecf628b0e8da2` | `032530cb1e9745392b59bcbf9d10b0ce8cdbccc37e5ac4ca6ee1b73e928a9153` |
| `backend/scripts/quality_acceptance_v3.py` | `34382a64a2bce9a373fb21d8a81abd3d0f54bb01283fc09edc935eb690c22c79` | `ae09179e4c5b1dfdb46b32f8e7f440b642256ba97f76c82146a54f0c12c659b7` |
| `backend/scripts/quality_review_packets.py` | `e4c3d974b490c375e547310175e40a55361c7ca5a3769c6466d14ab8e353cb80` | `ed3e685f4fece4c8b5a405b49e741edebf064f9705dd4a179ea33020839b01ec` |
| Configuration digest | `bdf9620130346a217e382f2ff4206a2de01dd85d8192d1bf5a3164769bffd965` | unchanged |
| Base commit | `5eda096a54ef993b460416c9816b443e0c6ec3b6` | unchanged (now reconciled onto `3fa622b`, main after PR #13, which changed no fingerprinted file) |

**Only these two evaluation scripts changed.** Substituting their version-1 contents into the version-2 tree
reproduces the version-1 fingerprint exactly. Every other fingerprinted file is byte-identical, so the planner
(`backend/app` outside its tests) and `backend/requirements.txt` are unchanged.

## Protocol digests

| | SHA-256 of the file |
| --- | --- |
| Version 1 (`evaluation/quality_v3/protocol_v1.json`, kept byte-identical) | `19a39ecdc2ce5bf215b7e7166af18b539f10e170343af1b43f2de9b22e48650b` |
| Version 2 (`evaluation/quality_v3/protocol.json`) | `23a9204663522bbd5a0420fa7affe493086ad0e2ae6148e1cc8572900c3a693a` |

## Budgets

The per-attempt limits are version 1's per-scenario limits, unchanged: Geoapify 100 requests and 100 credits,
Groq + Gemini 8 combined, Open-Meteo 2, Nager.Date 3, Frankfurter 2, Kiwi 0.

| | 10 completed attempts | Cumulative maximum (enforced) |
| --- | ---: | ---: |
| Geoapify requests | 1,000 | 1,200 |
| Geoapify credits | 1,000 | 1,200 |
| Groq + Gemini combined | 80 | 96 |
| Open-Meteo | 20 | 24 |
| Nager.Date | 30 | 36 |
| Frankfurter | 20 | 24 |
| Kiwi | 0 | 0 |

How the attempt bound works:

- **Sessions.** At most 1 + `max_resumes` = 3 sessions.
- **One non-result per session.** A guard stop ends a session's sending, so each session has at most one attempt
  that is not a result. A scenario skipped after the stop sends nothing and is not an attempt.
- **Twelve attempts at most.** Three interrupted sessions void the run with at most 9 results (9 + 3 = 12).
  Otherwise there are at most 10 results and 2 interrupted attempts (10 + 2 = 12).
- **Per scenario.** At most 3 attempts.
- **Before each attempt.** An attempt starts only if every per-attempt cap still fits inside the cumulative
  limits; otherwise nothing is sent and the run is void.
- **Counting.** Abandoned attempts, and attempts with an unknown credit count, are counted at the full caps.

## Runner changes (`quality_acceptance_v3.py`)

- **Attempt ledger.** Every attempt gets a write-once, read-only start marker before its first request, and a
  write-once final record. The record holds:
  - scenario id, attempt number, session, start and completion times;
  - outcome (`result`, `technical_failure`, `interrupted`) and interruption reason;
  - requests by service, combined LLM requests, Geoapify credits as reported and as counted;
  - local refusals and provider HTTP statuses;
  - the scrubbed partial output of an interrupted attempt.

  An attempt that never finished gets a write-once abandonment note on the next resume. No attempt file is ever
  replaced.
- **Kinds of "not a result".** A scenario skipped after a guard stop (`RunNotStartedGuardStopped`), or refused by
  the budget (`RunBudgetExhausted`), is recorded apart from an interrupted attempt (`RunInterruptedByGuard`). It
  sends nothing.
- **Cumulative limits** (`budget_shortfall`), computed from the verified ledger over every session, are checked
  before each attempt and before each session.
- **Audit log.** `AuditLog` is append-only and hash-chained, kept beside the result folders, never inside one. It
  records:
  - session starts and ends, and every attempt event;
  - budget refusals and voids;
  - the run manifest, the declaration, the review manifest and mapping, the submission, and the acceptance result.

  Every command prints its head digest.
- **Fail-closed resume.** Resume refuses before any request when:
  - the chain is broken;
  - an attempt file is missing, changed or unknown;
  - attempt numbering has gaps;
  - a stored result has no attempt, or a result attempt has no stored result;
  - the protocol differs from the one the run started under;
  - the run is complete or void;
  - the resume limit is reached.
- **Independence.** `declare` locks the reviewer's signed declaration (`INDEPENDENCE_DECLARATION.md`) before any
  review material exists, apart from that material. It checks:
  - the designated reviewer and the recorded operator, two different people;
  - every statement ticked;
  - signed by the reviewer;
  - dated between the end of the run and today.

  Preflight requires both names in the protocol, and requires that they differ.
- **Review material** (`quality_review_packets.py`). The material is split into `reviewer_bundle/` (packets,
  empty scoresheet, instructions: no scenario id, mapping, verdict or Part B figure) and `operator/` (mapping and
  after-scoring files, released only after submission).
- **Submission.** `submit` validates and locks write-once:
  - the completed scoresheet, read once, so what is validated is what is locked;
  - the returned packets, whose plan part must equal the packet issued under the same code.

  Edits before submission are the reviewer's own. An incomplete submission locks nothing.
- **Scoring.** `score` verifies:
  - the audit chain, and the run folder against its manifest and recorded digest;
  - the attempt ledger, and the protocol against the one the run started under;
  - the locked declaration;
  - the review folder against its manifest and recorded digest;
  - the mapping against its recorded digest and against the frozen seed;
  - the locked submission.

  It then scores the locked scoresheet, writes the result once and records it.

## Unchanged

- The ten scenarios: set SHA-256 `ff521eb3e851a55073a8989cbf84e3dafa7a2f9cd3b02450853b4b2823047384`.
- The trip date (Monday 2026-11-23) and the execution window (2026-10-28 to 2026-11-15 UTC).
- The rubric, the scale, the factual-error definition, the presentation seed and the three acceptance criteria
  with their thresholds. The `acceptance` block is identical to version 1's.
- The planner implementation and its dependencies.
- The production configuration and feature flags (the frozen environment equals `render.yaml`).
- The per-attempt request limits, the guard's stop conditions and `max_resumes`.
- The earlier frozen holdouts (`quality-holdout`, `quality-holdout-v2`) and their historical results. They were
  neither read for this amendment nor changed.

## Still open (later amendment, before the run)

`designated_reviewer` and `operator` are `null`. Naming them is a further amendment: version 3, with its own
record. Until then, preflight refuses the run.
