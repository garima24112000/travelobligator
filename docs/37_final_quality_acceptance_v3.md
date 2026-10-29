# 37. Final independent quality acceptance (quality-holdout-v3) — protocol

Status: **prepared and frozen on 2026-10-27 (protocol version 1); amended to version 2 on 2026-10-28, before any
run (section 10, `evaluation/quality_v3/AMENDMENT_v2.md`); not run.** No itinerary was generated, no provider or
model was called, no production setting changed and nothing was committed while preparing or amending it. The earlier holdouts are
untouched: `quality-holdout` (docs 26/27, a FAIL) and `quality-holdout-v2` (a historical FAIL, 6/10 baseline)
are neither rerun nor re-scored, and this set does not replace their results.

The machine-readable protocol is `evaluation/quality_v3/protocol.json` (pinned by SHA-256 in
`backend/app/tests/scripts/test_quality_acceptance_v3.py`). Where this document and that file differ, the file
wins and the difference is a defect to record in section 10 before the run.

## 1. What is evaluated

| | |
| --- | --- |
| Planner | The final corrected version: base commit `5eda096a54ef993b460416c9816b443e0c6ec3b6` (Final Batch 2, PR #13, which contains Final Batch 1, `905f228`) plus this evaluation tooling, which changes no planner code. Reconciled onto `3fa622b` (main after PR #13 was merged), which changed no fingerprinted file |
| Source fingerprint | `032530cb1e9745392b59bcbf9d10b0ce8cdbccc37e5ac4ca6ee1b73e928a9153` over 335 files of `backend/app` (without its tests), `backend/scripts` and `backend/requirements.txt`, the construction of `scripts/run_provenance.py`. Version 1 froze `b493b1c1bcae9fa16adfbbc5dcc9360a2620d42d9e3f2781cebecf628b0e8da2`; amendment 2 changed only the two evaluation scripts (`quality_acceptance_v3.py`, `quality_review_packets.py`), so `backend/app` and the requirements are byte-identical |
| Configuration | `bdf9620130346a217e382f2ff4206a2de01dd85d8192d1bf5a3164769bffd965`: every literal value of `render.yaml` (the release configuration: Geoapify geocoding, places and routing; Groq `openai/gpt-oss-20b` with Gemini `gemini-3.5-flash-lite` failover for anchors, reasoning, repair and narrator; 100 Geoapify credits per generation; Kiwi, airport lookup, targeted regeneration, feedback interpreter and every manual-file provider off), then the benchmark's own throwaway storage (Local JSON store, SQLite provider cache, synchronous generation). Every other setting is at its code default; all nine quality-phase flags are on |
| Runtime | Python 3.11, the exact `name==version` pins of `backend/requirements.txt` |
| Pipeline | The real generation pipeline through the single-city canary (`canary_city._run`, LangGraph engine), exactly as both earlier quality holdouts ran |

The fingerprint does not cover the `Dockerfile`, so PR #13's base-image fix (CVE-2026-84782, merged as `2281bd5`)
does not change what is evaluated. The run does not use a container image.

Differences from production that every holdout has had and this one keeps: Local JSON instead of PostgreSQL, an
empty SQLite provider cache instead of Redis, a synchronous generation instead of a background job, and
`APP_ENV=development`. None of them selects, orders, groups or routes a place.

## 2. The unseen set

`backend/scripts/benchmark/quality_v3.json`, key `quality_holdout_v3`, 10 scenarios, SHA-256 of the canonical
scenario list `ff521eb3e851a55073a8989cbf84e3dafa7a2f9cd3b02450853b4b2823047384`. Two travellers, a couple, no
origin; every scenario starts on the same day.

| ID | Destination | Days, pace | Interests | Must-visits | Exercises |
| --- | --- | --- | --- | --- | --- |
| Q3-H1 | Naples, Italy | 3, balanced | history, food, waterfront | Pompeii | must-visit outside the city, coastal city, food evidence |
| Q3-H2 | Kuala Lumpur, Malaysia | 3, packed | food, architecture, gardens | Petronas Twin Towers | sprawling city, packed pace, route burden |
| Q3-H3 | Stockholm, Sweden | 4, balanced | museums, harbour, history | Vasa Museum | island geography and ferries, museum weekly closures, waterfront |
| Q3-H4 | Salzburg, Austria | 2, relaxed | history, architecture | Hohensalzburg Fortress | small compact town, thin inventory, same-site redundancy |
| Q3-H5 | Auckland, New Zealand | 3, relaxed | outdoors, waterfront, food | none | topographic separation, ferry dependency, limited-slot usefulness without must-visits |
| Q3-H6 | Cartagena, Colombia | 3, relaxed | history, architecture, food | Castillo San Felipe de Barajas | ambiguous destination name, compact historic core, must-visit in the local language |
| Q3-H7 | Amsterdam, Netherlands | 5, balanced | museums, art, markets | Rijksmuseum; Anne Frank House | longest trip, multiple must-visits, museum-dense redundancy |
| Q3-H8 | Dubai, United Arab Emirates | 3, packed | architecture, markets, waterfront | Burj Khalifa | car-dependent city, vehicle transfers, packed pace |
| Q3-H9 | Tunis, Tunisia | 3, balanced | history, markets, museums | Bardo National Museum | museum weekly closures, thin provider metadata, names in several languages |
| Q3-H10 | Tbilisi, Georgia | 3, balanced | history, architecture, food | none | ambiguous country name, names in a non-Latin script, hilly terrain |

Spread: 2 to 5 days (32 trip days in all); 3 relaxed, 5 balanced, 2 packed; 0, 1 and 2 must-visits (9 in all);
every canonical interest of the contract's categories (food, waterfront, history, museum, art, shopping,
outdoors, architecture); Europe, Asia, Oceania, the Americas, Africa and the Middle East. Every interest term is
tracked by the taxonomy, and no scenario names two terms for one canonical interest (`gardens` and `parks` are
both `outdoors`, so only one is used).

**How "unseen" was established (2026-10-27).** Every destination and every must-visit name has **zero**
occurrences in: any commit of any branch of the repository (`git log --all -S`, case-insensitive), the
untracked files of every worktree, and every git-ignored result directory (`benchmark_results/`, the local
stores). That excludes, among others, the 18 tuning, 10 holdout and 8 stress cities of `cities.json`, Q-T1…Q-T6
and Q-H1…Q-H10 of `quality_v1.json`, Q2-H1…Q2-H10 of `quality_v2.json`, and every development city of the M2 and
Final Batch 1 live runs (Vienna, Edinburgh, Budapest, Gothenburg, Porto, London, Jersey City, Orlando,
Montreal, Nairobi). Candidates with even one earlier mention were dropped (for example Valencia, Seoul, Fez,
Washington, Cusco, Granada). Tests keep it so: no name of the set may appear in production code, in any
development or verification script, in an earlier benchmark or evaluation file, or in any backend or frontend
test or fixture.

The scenarios were chosen from the contract's generic categories before anything about them was looked up. No
provider was asked about any of them, so nobody knows how well the planner does on them.

**Owner check still needed:** the owner confirms that none of these ten destinations was used in a manual or
production trip by anyone who worked on the planner (section 9, item 5). The repository cannot show that.

## 3. Dates

- Trip start: **Monday 2026-11-23**, the same for every scenario. A Monday start exercises weekly museum
  closures on the first day.
- Execution window: **2026-10-28 to 2026-11-15 (UTC), inclusive.** The runner refuses outside it. The last day
  leaves at least a week before the trip starts, so every plan is for a future trip.
- Weather is a forecast only within the provider's horizon, so a run early in the window records weather as
  unavailable (the accepted `WEATHER` data-coverage code) and a run late in it may have a forecast. It is the same
  for all ten scenarios of one run, and the run date is recorded.
- If the window passes before the prerequisites are met, the dates are amended before the run (section 10).

## 4. Execution procedure and limits

From a clean checkout of the commit that contains this protocol, with no `.env` that changes a recorded setting:

```bash
cd backend
python scripts/quality_acceptance_v3.py preflight                                  # offline, sends nothing
python scripts/quality_acceptance_v3.py run --allow-quality-holdout --execute      # once
python scripts/quality_acceptance_v3.py run --allow-quality-holdout --execute --resume   # only after a guard interruption
python scripts/quality_acceptance_v3.py declare --declaration <signed declaration.md>    # offline, section 7
python scripts/quality_acceptance_v3.py packets                                    # offline
python scripts/quality_acceptance_v3.py submit --scoresheet <completed.csv> --packets <returned packets dir>   # offline
python scripts/quality_acceptance_v3.py score                                      # offline
```

`run` refuses before any request unless every preflight check passes:

- the set's SHA-256 and scenario count;
- today inside the window, and the trip date still in the future;
- HEAD contains the base commit, the working tree is clean, and the source fingerprint equals the frozen one;
- Python 3.11 and every pinned package;
- the effective configuration digest equals the frozen one, after the runner itself applies the frozen
  environment;
- a designated reviewer and an execution operator are named, and they are different people;
- `GEOAPIFY_API_KEY`, `GROQ_API_KEY` and `GEMINI_API_KEY` are present (names only, never values);
- the result directory and the audit log are new (a resume: both exist, and the audit log is intact);
- no review material exists.

It runs **the whole set, once**, in file order, with an 8-second pause between scenarios. There is no option to
choose scenarios, and `benchmark_quality.py`, `canary_city.py` and every development verification script refuse
the set.

**Attempts.** An *attempt* is one start of one scenario's generation. It begins with a write-once start marker,
entered in the audit log before its first request, and ends with a write-once final record:

- scenario id and attempt number, session, start and completion times;
- outcome: `result`, `technical_failure` or `interrupted` (with the guard's reason);
- requests by service, the combined Groq + Gemini count, Geoapify credits (as reported; an unknown count is taken
  at the 100-credit cap, never at zero), requests refused locally, provider HTTP statuses;
- for an interrupted attempt, its scrubbed partial output.

A scenario skipped because the guard had already stopped in that session, or refused by the cumulative check
below, is *not* an attempt and sends nothing. An attempt that started and has no final record (the process died)
is *abandoned*: a resume writes a note for it, and it counts at the full per-attempt caps. No attempt record is
ever replaced, so a resume leaves the earlier evidence exactly as it was.

**Request limits** (a process-wide guard on every HTTP request; any other host is refused). The per-attempt limits
are version 1's per-scenario limits, unchanged:

| Service | Per attempt | 10 completed attempts (no interruption) | Cumulative, at most (12 attempts) |
| --- | ---: | ---: | ---: |
| Geoapify requests | 100 | 1,000 | 1,200 |
| Geoapify credits (the application's own cap per generation) | 100 | 1,000 | 1,200 |
| Groq + Gemini combined (4 model stages × at most 2 requests) | 8 | 80 | 96 |
| Open-Meteo | 2 | 20 | 24 |
| Nager.Date | 3 | 30 | 36 |
| Frankfurter | 2 | 20 | 24 |
| Kiwi, or any other host | 0 | 0 | 0 |

**Why twelve attempts.** There are at most three sessions (the run and two resumes). A guard stop ends a session's
sending, so each session has at most one attempt that is not a result. If all three sessions are interrupted,
the run is void with at most nine results (9 + 3 = 12). Otherwise at most ten results and two interrupted
attempts (10 + 2 = 12). A scenario has at most three attempts. Version 1 stated only the 10-attempt column as the
"whole run" maximum, which understated the worst case by two attempts' worth.

The cumulative limits are **enforced**, over every attempt of every session (interrupted and abandoned ones
included), from the verified attempt records. An attempt starts only if all of its per-attempt caps still fit.
When one would not fit, nothing is sent: the scenario is recorded as refused by the budget, and the run is void.

The guard stops everything on a 401 or 403 from any service, or a 429 from a data provider. A Groq or Gemini
rate limit is not a stop: failover and the deterministic fallback handle it exactly as in production. Reaching a
per-attempt cap is not a stop either: it is part of that attempt's result.

**Interruption rule.** An attempt during which the guard stopped is *not a result*. Its scenario, and every
scenario not yet run, is run by `--resume` once the cause is fixed, and the interrupted attempt's record stays.
At most two resumes are allowed. The run is void if scenarios without a result remain after that, or if a further
full attempt would not fit the cumulative limits. Every other outcome **is** the result and is never run again.
That includes a weak plan, a technical failure, an honest `INSUFFICIENT_VERIFIED_INVENTORY` plan, and a model
fallback.

**Fail-closed resume.** `--resume` refuses before any request when:

- the audit log's chain is broken;
- an attempt file is missing, changed or unknown to the log;
- a stored result has no attempt, or a recorded result attempt has no stored result;
- the run was started under a different protocol;
- the run is already complete or void;
- the resume limit is reached.

**Audit log.** `benchmark_results/quality_holdout_v3.audit.jsonl`, beside the result folders and never inside one.
It is append-only JSON lines, each carrying a sequence number and the SHA-256 of the previous line. It records
digests, counts and fixed labels only: sessions, attempts, budget refusals, voids, and the digests of every
manifest and locked file. Every command prints its head digest, which the operator records outside the machine
(section 8).

**After the run.** The runner writes `MANIFEST.sha256` over every file of the run, attempt records included, and
enters its digest in the audit log before anyone reads a plan. Every later step re-verifies the run folder
against both. Packets are built only from a run that matches them and is valid and complete:

- one configuration and one source, unchanged during every scenario;
- a result for every scenario;
- the protocol the run started under.

The console prints each scenario's automated verdict as it runs. Only the operator sees it; that is why the
reviewer may not be the operator.

## 5. What is measured (the contract, unchanged)

- **Part A (hard correctness)** — the canary's baseline hard-correctness checks, acceptance policy 2, exactly
  as recorded. That covers the destination resolving, no fabricated or unverified scheduled identity, no
  unsupported claim, zero duplicates and no unresolved suspected duplicate, persistence and reload, the credit
  cap, readiness `ready` or `needs_review` only for the accepted data-coverage codes, no unresolved excessive
  walking or unreasonable transfer, no movement data the router did not return, the hard diversity rule, food
  locality, no stale rationale, and either R meaningful stops with no empty day, every grounded must-visit
  scheduled and at least 90% routing coverage, or an honest `INSUFFICIENT_VERIFIED_INVENTORY`. The reviewer's
  verified factual errors are added to it (section 7).
- **Part B (quality figures)** — `itinerary_quality` of each scenario (schema 6, including the venue figures).
  Reported only, never a pass or fail criterion, and never read before scoring.
- **Part C (human rubric)** — the six dimensions of contract section 6, scored by the designated reviewer.

## 6. Acceptance rule (frozen)

The contract's release target, unchanged, on this unseen set. **All three must hold.** They are reported
separately and never collapsed into one score:

1. **median Overall ≥ 4.** With ten scores the median is the mean of the 5th and 6th, so 3.5 does not meet it.
2. **No scenario with Overall < 3.**
3. **Zero hard factual-safety failures**, read as follows (fixed now, before any result, closing the ambiguity
   doc 26 Appendix C had to leave to its owner):
   - the run is valid and complete;
   - every scenario's generation completed and passed the canary's baseline hard correctness (section 5);
   - the reviewer verified **zero** factual errors across the ten plans.

   This is the reading under which `quality-holdout-v2` was recorded as a FAIL, and it is stricter than
   counting only section 4.1 of the contract. An `INSUFFICIENT_VERIFIED_INVENTORY` plan passes criterion 3 when
   honest, and is then judged by criteria 1 and 2 like any other plan.

The other five dimensions are recorded and reported, with no threshold. `quality_acceptance_v3.py score`
applies these rules mechanically to the locked scoresheet and the stored records. Its result is the
decision. Whatever it is, the result is recorded as it comes out, in a new document beside docs 27 and 35. A
defect it exposes is fixed and validated on tuning or development scenarios, never by rerunning, re-scoring or
re-presenting this set.

## 7. Human review

**Reviewer and independence.** Exactly one designated reviewer, named in the protocol before the run (section
10). The reviewer:

- is not the execution operator, who is also named in the protocol;
- has not developed, tuned or reviewed planner changes for this release;
- has seen no output of this evaluation, no automated verdict and no scenario identity before receiving the
  blinded packets.

After the run and before any review material exists, the reviewer completes and signs
`evaluation/quality_v3/INDEPENDENCE_DECLARATION.md`, and returns it in an attributable way (from their own account,
or signed with their own key). `declare` accepts it only if it:

- names the designated reviewer and the recorded operator, two different people;
- has every statement ticked;
- is signed by the reviewer;
- is dated between the end of the run and today.

`declare` then locks it write-once in `benchmark_results/quality_holdout_v3_independence/`, apart from the
review material, and enters its digest in the audit log. `packets` refuses without it.

The verdict uses the designated reviewer's scores only. A second reviewer is optional and is reported separately,
never averaged.

**Blinding.** `packets` writes two trees under `benchmark_results/quality_holdout_v3_review/`:

- `reviewer_bundle/`: the ten packets, the empty scoresheet and the reviewer instructions. The operator gives
  the reviewer a **copy** of this tree and nothing else.
- `operator/`: the packet-to-scenario mapping and the after-scoring files (automated verdict and Part B figures
  of each plan). The operator keeps it until the submission is locked.

The review manifest's digest and the mapping's digest go into the audit log. A packet is the request and the plan
as the traveller receives it:

- must-visit and interest status;
- the plan's own findings and status;
- per day: estimated visit times, opening-hours status, verified travel legs (ferry legs as the provider stated
  them, unverified legs as "no checked route"), nearby food, day notes and the narrative;
- a map link for every stop.

Packets are in a fixed shuffled order, `sha256("travelobligator/quality-acceptance-v3/2026-10-27:" + id)`, so
the file order says nothing. A packet does **not** contain:

- the scenario id or its "exercises" labels;
- any provider id, provider or model name, internal tier or evidence band;
- the automated verdict or the Part B figures.

Nothing in the reviewer bundle names a scenario id or label, the packet mapping, an automated verdict, a check
name or a Part B figure (a test keeps it so).

**Instructions.** `evaluation/quality_v3/REVIEWER_INSTRUCTIONS.md` is part of the bundle. In short:

- score one packet at a time;
- look places up on a map or the web (allowed);
- give six whole-number scores from 1 to 5, each with a sentence of evidence;
- count verified factual errors, each with its source and the date checked;
- record the row on the scoresheet;
- return the scoresheet and the ten packets.

**Submission and lock.** Until submission the scoresheet is the reviewer's working copy, and editing it is never
treated as corruption. `submit` checks that:

- there is exactly one complete, valid row per packet, by the designated reviewer;
- the returned packets are exactly the issued ones;
- the plan part of each returned packet (everything above its score section) equals the plan issued under its
  code, so a packet cannot be scored under another packet's code.

If any check fails, nothing is locked, and the reviewer may correct the submission and submit again. If all pass,
`submit` locks the scoresheet and the packets write-once in `benchmark_results/quality_holdout_v3_submission/`,
with their own manifest, and enters both digests in the audit log. Only after that may the reviewer receive
`operator/after_scoring/`. A locked submission is never replaced.

## 8. Recording the result

`score` refuses unless all of these hold:

- the audit log's chain is intact;
- the run folder matches its manifest, and that manifest's digest is the one the audit log recorded;
- the attempt records match the log, and the protocol is the one the run started under;
- the locked declaration is unchanged;
- the review folder matches its manifest and its recorded digest;
- the packet mapping equals both its recorded digest and the mapping the frozen seed produces (so a swapped
  mapping is caught even if its manifest was rewritten too);
- the locked submission matches its manifest and its recorded digests.

It then computes the result from the **locked** scoresheet. The result is written once to
`benchmark_results/quality_holdout_v3_acceptance_result.json` with the scoresheet's and the protocol's SHA-256, and
its digest is entered in the audit log. A second, different result is refused. Then:

- copy the signed scores and evidence into a new document;
- state the three criteria and the decision exactly as computed;
- list any Part A failure and any factual error with its evidence;
- record the audit log's final head digest in that document (and, after each earlier step, the head the command
  printed, for example in the operator's own notes or the results pull request). With these copies outside the
  machine, removing the log's last entries is also detectable.

Nothing in the run, review, independence or submission folders is edited afterwards: each has a manifest or a
recorded digest. Results and packets stay in the git-ignored `benchmark_results/`, as for the earlier holdouts.

## 9. Prerequisites before running (all required)

1. **This work is reviewed and committed**, with a clean tree. Committing does not change the frozen
   fingerprint, which covers file contents and not the commit. The commit's CI passes (`Backend / Hermetic`
   runs the pins and guards of `test_quality_acceptance_v3.py`). PR #13 is merged (`3fa622b`) and this work
   sits on top of it.
2. **No planner or tooling change after the freeze.** Any change to `backend/app` (outside its tests),
   `backend/scripts` or `backend/requirements.txt` changes the fingerprint, and the runner refuses. If such a
   change is needed before the run, it is a protocol amendment (section 10). After the run, none is possible.
3. **A designated reviewer and an execution operator are named** in `protocol.json` (one amendment, section 10).
   They are two different people, and the reviewer meets section 7.
4. **The operator's environment.** A fresh checkout with no `.env` that sets a recorded setting. Python 3.11
   with `pip install -r backend/requirements.txt`. The three keys exported in the operator's own shell (never in
   a file in the repository, never shown).
5. **Owner confirmations.**
   - None of the ten destinations was used in a manual or production trip by anyone who worked on the planner.
   - The cumulative request limits of section 4 (at most 1,200 Geoapify credits and 96 model requests) are
     approved.
   - The run happens inside the execution window.
6. **`preflight` passes** on the run day. It is the last step before `run`.

## 10. Protocol amendments

Allowed **only before the run**, and only for the following:

- naming the designated reviewer and the execution operator;
- moving the dates (the trip start must stay a Monday, and the window must end at least seven days before it);
- re-freezing the fingerprint or configuration digest after a reviewed change that is recorded here.

The scenarios, the per-attempt limits and the acceptance rule are not amended. Each amendment:

- edits `protocol.json`, increases `protocol_version` and updates the pin in the test;
- keeps the previous version byte-identical as `protocol_v<N>.json`;
- writes `AMENDMENT_v<N>.md` with the old and new digests;
- adds a row below with its reason.

None is allowed after `run` has started. The runner records the protocol's digest when the run starts, and every
later step refuses another one.

| Version | Date | Change | Reason |
| --- | --- | --- | --- |
| 1 | 2026-10-27 | Frozen | — |
| 2 | 2026-10-28 | Cumulative limits corrected (12 attempts: 1,200 Geoapify credits, 96 model requests); write-once attempt records and enforced cumulative limits; hash-chained audit log; review and submission manifests, the mapping check and the locked scoresheet; execution operator, reviewer ≠ operator and a locked independence declaration; fingerprint re-frozen for the two evaluation scripts only | The pre-commit protocol review found that version 1 understated the worst-case requests with resumes, lost an interrupted attempt's evidence on resume, and did not verify the review material at scoring. `evaluation/quality_v3/AMENDMENT_v2.md` |

## 11. Offline preflight on 2026-10-27 (the freeze day; nothing sent)

Passed:

- set unchanged (10 scenarios);
- trip start in the future;
- HEAD contains `5eda096`;
- source fingerprint equal;
- Python 3.11;
- every pinned package installed;
- configuration digest equal (reproduced from the protocol's own environment);
- result directory new.

Failed, as expected before the prerequisites:

- the window opens on 2026-10-28;
- the work is not yet committed (dirty tree);
- no reviewer is named;
- no provider key is present in the preparation shell (deliberately).

A rehearsal ran the complete run → manifest → packets → scoresheet → acceptance path offline. It used a stand-in
for the generation and was not run on the set's plans, which do not exist.

**Version 2, 2026-10-28 (nothing sent).** Passed:

- set unchanged;
- inside the window;
- trip start in the future;
- HEAD contains `5eda096` (reconciled onto `3fa622b`);
- source fingerprint `032530cb…` equal;
- Python 3.11 and every pinned package;
- configuration digest `bdf96201…` equal;
- result directory and audit log new.

Failed, as expected:

- dirty tree (not committed);
- no reviewer;
- no operator, so reviewer ≠ operator cannot be shown;
- no provider key.

The offline tests use a stand-in generation and send nothing. They rehearse:

- interrupted attempts across two resumes, the resume limit and the cumulative-budget refusal;
- an abandoned attempt;
- six kinds of tampered evidence on resume;
- the complete declare → packets → submit → score path;
- seven kinds of altered material at scoring;
- a protocol changed after the run.

## 12. Limitations

- **One run.** The model stages are not deterministic. Each plan is one sample, recorded as it comes out.
- **Ten scenarios and one reviewer.** It is a release gate, not a statistical estimate.
- **The reviewer judges a faithful rendering of what Traveler view shows, not the running web page.** The run's
  storage is throwaway.
- **Visit lengths are the planner's generic estimates**, and hours come only from the records (Final Batch 1
  limitations).
- **Provider data is checked by the reviewer, not by the run.** A closed or relocated place counts only when the
  reviewer verifies it with a source.
- **The audit log lives on the operator's machine.** Its chain shows any edited, removed or reordered entry. Only
  the copies of its head recorded elsewhere (section 8) show removed final entries. Independence rests on the
  reviewer's attributable declaration; no tool can prove what a person has seen.
- **Fail-closed, not self-healing.** If the process dies in the instant between writing an attempt file and
  entering it in the audit log, the file is unknown to the log. A resume then refuses, and the run cannot
  continue: it is void, never repaired by hand.
