# 38. Local verification when GitHub-hosted CI is unavailable

Status: **tool implemented; local verification approved by the owner on 2026-11-10** (protocol amendment 4,
`evaluation/quality_v3/AMENDMENT_v4.md`, on the branch of PR #15). Since
2026-11-06, GitHub refuses to start any Actions job for this account ("The job was not started because your
account is locked due to a billing issue"). That stops every job of `ci.yml`, and also the scheduled
`neon-backup.yml`, `production-monitor.yml` and `uptime.yml`. The owner has decided not to pay for GitHub
Actions.

## 1. What is mandatory, and what depends on GitHub

**GitHub enforces nothing on `main`.** The branch has no branch protection and no ruleset (checked
2026-11-10: `GET /branches/main/protection` → 404 "Branch not protected", `GET /rulesets` → `[]`). A merge is not
technically blocked by any check. The requirements below are the project's own, written in its documents:

| Requirement | Where | Applies to |
| --- | --- | --- |
| "The commit's CI passes" | `docs/37` section 9, item 1 | quality_v3: before the run |
| "CI is green on the commit you will deploy": all six jobs, on that exact commit, on `main` | `docs/25` "Before you start", item 1 | every production deployment |
| The six job names are the check names | `CLAUDE.md`, `ci.yml` header | all pull requests |

All six jobs depend on GitHub-hosted runners. None of them needs a GitHub service otherwise. They use no secret, no
OIDC and no registry, and every credential in them is a throwaway value for a container. The following parts
are GitHub-only and have no local equivalent: the pass/fail check shown on the pull request, the `gha` build
cache, `actions/upload-artifact`, the job summary page, and concurrency cancellation.

## 2. The local verification tool

`scripts/ci/local_ci.py` (standard library only), with `scripts/ci/local-runner.Dockerfile`. It isn't part of the
quality_v3 fingerprint, which covers `backend/app`, `backend/scripts` and `backend/requirements.txt`.

```bash
python3.11 scripts/ci/local_ci.py --commit <full sha>      # all six jobs, about an hour on a cold machine
python3.11 scripts/ci/local_ci.py --commit <sha> --jobs backend-hermetic security   # a subset (never "passed")
```

**What it runs.** For one exact commit, the six jobs of `ci.yml`: the same commands, in the same order, with the
same gates. That includes the zero-skip assertion of `Backend / Integration`, the four Trivy steps of
`Containers / Build & Smoke` with the same severities and `--ignore-unfixed`, and the three Trivy steps of
`Security / Scan`.

- **Checkout.** A one-commit (depth 1) git repository detached at the commit, fetched from the local repository,
  like `actions/checkout`'s default. An untracked or modified file in the operator's checkout can't take part.
  Each job gets a fresh container and a fresh copy.
- **Runner.** Every job except Containers and Security runs as a non-root `runner` user in an image providing
  Python 3.11, Node 22, the PostgreSQL 16 client, GnuPG and Chromium's libraries for the commit's Playwright
  version.
- **Services.** PostgreSQL 16 and Redis 7.4 run as service containers on `127.0.0.1`, as on CI.
- **Trivy.** Version 0.70.0, the version that `trivy-action` v0.36.0 installs.

**Fail closed.**

- A step after a failed gate step is recorded as `skipped_after_failure`; it is never omitted.
- A job is `passed` only if every gate step ran and exited 0.
- Steps that `ci.yml` itself marks non-blocking are recorded as `gate: false`: the two Trivy "report" steps and
  "Report counts".
- An infrastructure fault is `error`. A time-out is `timed_out`; each job's budget is twice its `timeout-minutes`,
  because nothing is cached.
- The run is `passed` only if all six jobs passed. A subset run is `incomplete` at best. Exit status 0 means
  `passed`, and nothing else does.

**Evidence** is written outside the repository, by default under
`~/TravelObligatorEvidence/local-ci/<sha>/<run id>/`. The tool refuses an evidence folder inside the repository.

| File | Contents |
| --- | --- |
| `manifest.json` | The commit (SHA, tree, refs that point at it, SHA-256 of its `git archive`). The tool's own commit and file digests, and whether they were clean. Host OS, architecture, and the Docker, buildx, Compose and git versions. Every image's ID and digests. Tool versions inside the runner. The Trivy database date. Every step's exact command, where it ran, gate flag, status, exit code, UTC start and end, duration, log path and log SHA-256. Each job's junit or Playwright counts. The list of differences from CI. |
| `SUMMARY.md` | The same, for a person |
| `logs/<job>/NN-<step>.log`, `results/<job>/…` | Logs, junit XML, Playwright results and screenshots |
| `SHA256SUMS` | Every file above. **The SHA-256 of this file is the run's evidence digest.** |

**Privacy.**

- The folders are mode 0700 and the files 0600.
- Host-side commands get only `PATH`, `HOME`, `TMPDIR`, locale and Docker variables. The operator's shell, which
  may hold provider keys, is never passed on.
- The home directory is replaced by `~` in every log.
- No secret is used. Logs hold only throwaway CI values.
- The evidence is local. Publishing its summary to the public pull request is a separate, owner-approved step.

**Differences from GitHub-hosted CI** (also written into every manifest):

- **Platform.** Debian trixie in Docker on the operator's machine, instead of GitHub's Ubuntu 24.04 VM. Trixie
  has GnuPG 2.4, as Ubuntu 24.04 does; the backup script needs it.
- **Architecture.** It follows the host: linux/arm64 on Apple silicon, while CI is linux/amd64. Image
  vulnerability results can differ by architecture.
- **Caching.** Every install is cold.
- **Checkout.** For a pull request, CI checks out GitHub's merge of the branch into `main`. The tool checks out
  the commit itself. The two trees are equal only when the branch contains `main`'s tip, which the merge
  procedure requires.
- **Chromium's libraries** are installed when the runner image is built, not with `--with-deps` during the job.
- **Trivy database.** Results depend on the database date, which is recorded in the manifest.
- **Order.** The jobs run one after another, not in parallel.
- **Environment.** `CI=true` is set in every step, as GitHub does; `GITHUB_ACTIONS` and the other `GITHUB_*`
  variables are not. Machine size differs, and the Docker VM's CPUs and memory are recorded.
- **Local images.** The local image tags `travelobligator-{backend,frontend}:local` are replaced.
- **GitHub.** Nothing is reported to GitHub.

**Independent verification.** Anyone with Docker can rerun the tool on the same commit and compare statuses and
counts. Logs differ in their timestamps and are not expected to be byte-identical. A copy of the evidence is
checked with `shasum -a 256 -c SHA256SUMS`, and its digest is compared with the one posted on the pull request. If
the owner wants a signature, `gpg --detach-sign SHA256SUMS` with the owner's own key adds one.

## 3. Merge procedure while hosted checks are unavailable (approved by the owner, 2026-11-10)

Nothing here creates or edits a GitHub status or check. The pull request keeps its red "job was not started"
checks, and they're never presented as anything else. No `--admin` merge is involved, because there is no
protection to bypass. Merging stays the owner's decision for each pull request.

1. **Up to date with main.** `git merge-base --is-ancestor origin/main <head sha>`, so that the merge commit's tree
   equals the verified tree.
2. **Local run passes.** `local_ci.py --commit <head sha>` reports `overall: passed`. The tool files must be clean
   and equal to the digests that amendment 4 records.
3. **Evidence posted.** A sanitized summary goes on the pull request as a comment: commit SHA, tree, the six
   results, the evidence digest, and every earlier failed or aborted run of the same change. It never includes
   raw logs, traces, screenshots, environment variables or the evidence folder. The repository is public, so the
   comment is public.
4. **Owner approval.** The owner approves on the pull request, naming the head SHA and the evidence digest.
5. **Merge** with `gh pr merge <n> --merge`, the same kind of merge as #13 and #14. The merge commit's message
   carries the evidence digest. It isn't written into a tracked file, because that would create a new commit
   that the evidence doesn't cover.
6. **Tree check.** `git rev-parse origin/main^{tree}` must equal the `tree` in the manifest. If it doesn't, run
   the tool on the merge commit before anything relies on it.

A new commit on the branch makes the evidence stale, and the whole procedure starts again.

For a production deployment (`docs/25`), the same tool on the exact `main` commit is the proposed stand-in. That
needs its own owner decision, and none is made here.

## 4. Protocol amendment 4 for quality_v3 (approved by the owner, 2026-11-10)

`docs/37` section 9, item 1 requires "the commit's CI passes". Section 10 lists the only changes allowed before the
run. Replacing the source of the CI evidence wasn't on that list, so it needed an explicit, owner-approved
amendment rather than a silent reinterpretation. Amendment 4 does three things:

- it states the CI rule above in `protocol.json` (`ci_evidence`), pinning the tool by file digest;
- it adds that one item to section 10's list;
- it moves the dates: execution window **2026-11-16 to 2026-11-30 UTC**, trip start **Monday 2026-12-07**.

Version 3 is kept byte-identical. The ten scenarios (`ff521eb3…`), rubric, thresholds, limits, budgets, reviewer
and operator, fingerprint (`032530cb…`) and configuration digest (`bdf96201…`) are unchanged. The dates live only
in `protocol.json`, and `scripts/ci` is outside the fingerprint.

**If the tool changes,** its new file digests need a further amendment before they can be used.

### 4.1 Local runs so far (all disclosed)

| Run (UTC) | Commit | Tool | Outcome |
| --- | --- | --- | --- |
| 2026-11-10 04:18 | `4288103` (PR #15) | `803935c` | **Aborted**, not evidence. No `.git` in the checkout and GnuPG 2.2: 31 hermetic failures from the tool's environment. |
| 2026-11-10 04:44 | `4288103` | `a6a0006` | **Aborted**, not evidence. `CI=true` wasn't set, so Playwright ran 4 workers instead of 2. |
| 2026-11-10 05:43 | `4288103` | `4de59d8` | **Failed**, evidence `e233c90c…`. Hermetic, Integration, Frontend and Security passed. E2E had 182 passed and 1 failed: a trip deletion took 10.1 s against a 10 s expectation. Containers failed because the tool's private umask reached the checkout (a tool defect). |
| trials, scratch evidence | `4288103` | `a6a0006` to `aeefbdf` | Single-job trials while fixing the tool: Hermetic passed, E2E passed (183/0), Containers showed CVE-2026-109518 in the backend image. Not evidence. |

### 4.2 The Browser / E2E deletion failure: diagnosis (2026-11-10)

**What failed.** In the evidence run of 05:43, one of 183 browser tests failed: `[mobile-375] accessibility.spec.ts
› keyboard › deleting a trip can be done, and cancelled, with the keyboard alone`. Its Playwright trace shows
`DELETE /trips/{id}` answered **200 after 10,112 ms**. The test waits at most **10,000 ms** (Playwright's
`expect` timeout) for "Trip deleted.". The deletion itself succeeded; the page was still showing "Deleting…".

**Cause: the offline test backend's development store, not the product's deletion.**

- The browser suite's backend (`backend/scripts/e2e_backend.py`) uses the Local JSON development store, which is
  documented "Not for production use". It holds one file for the whole run: every test signs up a fresh account
  and nothing is pruned, so the file grows through the suite.
- Every read of that store parses the whole file. Every write rewrites it whole, with `indent=2`, `sort_keys` and
  `fsync`, under one process-wide lock.
- Deleting a trip there makes about ten whole-file reads and five whole-file rewrites.
- A generation by a parallel test worker also rewrites the file after each stage, under the same lock.

**Measured** with `scripts/ci/diagnostics/e2e_delete_latency.py`: the real offline backend in the same runner image,
no provider, three deletions per point.

| Trips in store | Store | Delete alone (median) | Delete during a generation |
| ---: | ---: | ---: | ---: |
| 0 | 0 MB | 0.02 s | 0.06 s |
| 40 | 9.2 MB | 1.58 s | 3.18 s |
| 80 | 18.4 MB | 3.72 s | 7.37 s |
| 120 | 27.5 MB | 6.89 s | 11.25 s |
| 160 | 36.7 MB | 9.65 s (max 10.11) | 15.88 s |

Latency grows linearly with the total stored data, about 0.06 s per stored trip, and a concurrent generation
roughly doubles it. The failure's 10.1 s matches a store of about 100–120 trips, plus a generation by the other
worker. That is where the suite was at that moment.

**Classification.**

- **Not database contention, transaction locks or an inefficient production deletion.** Production deletes in
  PostgreSQL, in one unit of work with indexed deletes. The browser suite never exercises that path; its
  integration tests do, and they pass.
- **Not fixture interference by another test's data either.** It's the size of all tests' data together, plus
  serialization on the store's single lock, with backend saturation from one parallel generation.
- **The underlying cost is a known property of a dev-only store,** not a product defect.
- **Not GitHub-specific.** GitHub's runner has the same fixture. A faster disk makes each whole-file rewrite
  cheaper, so it fails there less often. This Docker VM measured 0.88 s per 37 MB rewrite with `fsync`.

**What was not done.** No timeout was raised, no test or application behaviour changed, and nothing was rerun to
hide the failure.

**Correction options (owner decision).** Each one touches different frozen material:

| Option | Change | Effect on quality_v3 |
| --- | --- | --- |
| A | Make the E2E backend's store cost independent of the suite's size, in `backend/scripts/e2e_backend.py` (test infrastructure) | `backend/scripts` is fingerprinted: re-freeze the fingerprint by a recorded amendment (section 10 allows it). The planner is unchanged. |
| B | Make `LocalJsonStore` writes cheaper (compact JSON, or no `fsync` in test mode) | `backend/app`: fingerprint re-freeze, and an application change to a dev-only store. Still linear in size. |
| C | For the deletion confirmation only, wait for the deletion request itself, with a budget stated from the measurement above, before checking the message | Test expectation only (`frontend/e2e`): no fingerprint change. It doesn't make the slow store faster. |

## 5. Backups and monitoring without GitHub Actions

*The owner approved designing this on 2026-11-10. Nothing is provisioned. Any service that may incur a cost needs
the owner's approval before it's activated.*

**Where things stand.**

- The last successful automated backup was 2026-11-05 at 11:25 UTC. The scheduled runs of 11-06 to 11-09 were
  refused by GitHub.
- The bucket's lifecycle rule deletes objects after 7 days, so the `neon/` prefix is expected to be empty from
  about **2026-11-12**.
- The hourly production monitor and uptime check have also stopped. Nothing is watching.

### 5.1 Immediately: a manual backup from the operator's Mac (no new resource)

This is the same script and safety contract as the workflow. It needs Homebrew's `postgresql@16` client (version
16 matches Neon's server) and AWS credentials that may write `neon/` in the bucket, signed in with `aws login`.
Run it from a clean checkout of `main`:

```bash
read -rs NEON_BACKUP_DATABASE_URL && export NEON_BACKUP_DATABASE_URL        # the backup role's DIRECT URL
read -rs BACKUP_ENCRYPTION_PASSPHRASE && export BACKUP_ENCRYPTION_PASSPHRASE
BACKUP_EXPECTED_DB_USER=travelobligator_backup BACKUP_S3_BUCKET=travelobligator-db-backups-10192026 \
BACKUP_S3_PREFIX=neon/ AWS_REGION=us-east-1 PG_BIN_DIR=/opt/homebrew/opt/postgresql@16/bin \
  python3 -I scripts/backup/neon_s3_backup.py
unset NEON_BACKUP_DATABASE_URL BACKUP_ENCRYPTION_PASSPHRASE
```

- **What the script does:** it encrypts, proves that the encrypted file decrypts to the dump, uploads without
  overwriting, and checks the stored object's SHA-256.
- **Restore check (required before the backup counts as done).** Run the recovery drill of `docs/30` section 5.1.
  It restores only into a throwaway PostgreSQL 16 container with no network:

  ```bash
  aws s3api list-objects-v2 --bucket travelobligator-db-backups-10192026 --prefix neon/ \
      --query 'Contents[].[Key,Size,LastModified]' --output table
  python3 scripts/backup/restore_drill.py --s3-key neon/<the new object> --expect-revision a94c0e7d2b61
  ```

  `a94c0e7d2b61` is the Alembic head of `main` today. Production must be at exactly this revision, so check it
  before relying on it. The drill must print `Drill PASSED`. Afterwards, `docker ps -a --filter
  label=travelobligator.restore-drill=true` must show nothing. Record the date, object name and result (not the
  row counts) in `docs/30` section 8.
- **Repeat daily** until automation is back. The 7-day rule applies to these objects too.

Don't keep an extra local copy: manual exports have no retention policy in force (`docs/25`).

### 5.2 Recommended automation: the existing AWS account, free tier (approval needed)

- **Backup.** An EventBridge Scheduler rule runs daily at 03:37 UTC. It triggers a Lambda function: an arm64
  container image holding `pg_dump` 16, GnuPG, the AWS CLI and the unchanged `neon_s3_backup.py`. The function
  writes to the same bucket and prefix under the same lifecycle rule.
- **Secrets.** Kept in SSM Parameter Store SecureString (standard tier, with the default `aws/ssm` key; no
  Secrets Manager charge). The function's role may only read those two parameters and put and head objects in
  `neon/`.
- **Restore verification.** Weekly, a second invocation downloads the newest object, decrypts it, and runs
  `pg_restore --list` (structure only, no database). A full restore drill stays a monthly manual step (`docs/30`).
- **Alerts.**
  - CloudWatch alarms on the function's `Errors`, and on `Invocations < 1` per day with missing data treated as
    breaching (an absent run is an alert), notify an SNS topic with an email subscription.
  - An hourly monitor function checks that the newest `neon/` object is under 36 hours old, and GETs the public
    frontend page only (never the backend: no keepalive, per the owner's decision).
- **Cost.** Lambda, EventBridge Scheduler, SNS email and up to 10 alarms are within AWS's always-free allowances.
  The container image is stored in ECR, about 0.3 GB, roughly $0.03 a month once any ECR free allowance has run
  out. **That's a small but non-zero charge: it needs the owner's approval.** Nothing runs on the owner's Mac.

### 5.3 Alternatives

- **GitLab.com pipeline schedule (free tier).** The same script, daily, using about 1–2 of the 400 free compute
  minutes per run. AWS access would be through a new GitLab OIDC trust. It needs a GitLab account, which may ask
  for card verification for shared runners. Failure emails are built in. Too few minutes for hourly monitoring.
- **External uptime service with a free plan, such as UptimeRobot.** For the frontend page; email alerts. It's a
  new third-party account, and its free-plan terms must be checked.
- **Neon's own restore history.** Not an independent copy, and its length depends on the plan. A complement, never the
  backup.
