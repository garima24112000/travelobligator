# 38. Local verification when GitHub-hosted CI is unavailable

Status: **tool implemented; replacement gate and protocol amendment 4 PROPOSED, not approved.** Since
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
- **Local images.** The local image tags `travelobligator-{backend,frontend}:local` are replaced.
- **GitHub.** Nothing is reported to GitHub.

**Independent verification.** Anyone with Docker can rerun the tool on the same commit and compare statuses and
counts. Logs differ in their timestamps and are not expected to be byte-identical. A copy of the evidence is
checked with `shasum -a 256 -c SHA256SUMS`, and its digest is compared with the one posted on the pull request. If
the owner wants a signature, `gpg --detach-sign SHA256SUMS` with the owner's own key adds one.

## 3. Merge procedure while hosted checks are unavailable (PROPOSED: the owner's approval needed)

Nothing here creates or edits a GitHub status or check. The pull request keeps its red "job was not started"
checks; they're never presented as anything else. No `--admin` merge is involved, because there is no protection
to bypass.

1. **Up to date with main.** `git merge-base --is-ancestor origin/main <head sha>`, so that the merge commit's tree
   equals the verified tree.
2. **Local run passes.** `local_ci.py --commit <head sha>` reports `overall: passed`, with the tool files clean.
3. **Evidence posted.** With the owner's approval, the summary and the evidence digest go on the pull request as a
   comment. The repository is public, so the comment is public.
4. **Owner approval.** The owner approves on the pull request, naming the head SHA and the evidence digest.
5. **Merge** with `gh pr merge <n> --merge`, the same kind of merge as #13 and #14.
6. **Tree check.** `git rev-parse origin/main^{tree}` must equal the `tree` in the manifest. If it doesn't, run
   the tool on the merge commit before anything relies on it.

A new commit on the branch makes the evidence stale; the whole procedure starts again.

For a production deployment (`docs/25`), the same tool on the exact `main` commit is the proposed stand-in. That
needs its own owner decision, and none is made here.

## 4. Proposed protocol amendment 4 for quality_v3 (NOT approved, not applied)

`docs/37` section 9, item 1 requires "the commit's CI passes". Section 10 lists the only changes allowed before the
run: naming people, moving dates, and re-freezing the fingerprint or configuration after a recorded change.
**Replacing the source of the CI evidence isn't on that list.** Treating a local run as "CI" without saying so
would silently relax the requirement. So the smallest legitimate change is an explicit amendment that the owner
approves. It would make these changes:

- **Section 9, item 1, appended:** "While GitHub-hosted runners are unavailable to this repository, the CI
  requirement is met instead by a passing run of `scripts/ci/local_ci.py` (tool commit `<sha>`, file digests
  recorded) of all six jobs on the exact commit, whose evidence digest and summary are recorded on the pull request
  and in `AMENDMENT_v4.md`, and which the owner has approved in writing. A GitHub-hosted run, when available again,
  is still preferred."
- **Section 10:** one added allowed change ("the source of the CI evidence of section 9, item 1, as in amendment
  4"), and a row in the table.
- **`protocol.json`:**
  - version 4, `amended_on`, `amendments`;
  - one new field, `ci_evidence`, naming the tool, its digests, and the rule above;
  - version 3 kept byte-identical as `protocol_v3.json`.
- **Tests:** new pins, and a test that allows only those fields to differ.

**Unchanged:** the ten scenarios (`ff521eb3…`), dates, rubric, thresholds, request limits, cumulative budgets,
reviewer and operator, fingerprint (`032530cb…`) and configuration digest (`bdf96201…`). The quality_v3 preflight
doesn't check CI, so no runner code changes.

**Order.**

1. Merge this tool, gated by its own local run under section 3.
2. Add amendment 4 to PR #15.
3. Run the tool on PR #15's new head.
4. Merge under section 3.

**The quickest alternative, needing no amendment.** The repository is public, and GitHub-hosted standard runners
are free for public repositories. The lock is an account billing problem, not Actions usage. If clearing it costs
nothing going forward, hosted CI resumes and this amendment isn't needed.

**Dates.** The window ends 2026-11-15 UTC. If PR #15 can't be merged by **2026-11-13**, leaving the operator two
days, the alternative is a date amendment (section 10 allows it). The trip start would move to **Monday
2026-12-07** and the window would run from **2026-11-16 to 2026-11-30 UTC**: still a Monday, and ending seven
days before the trip.

## 5. Backups and monitoring without GitHub Actions (DESIGN: the owner's approval needed for any new resource)

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
- **Restore check:** follow the recovery drill of `docs/30` against a disposable database.
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
