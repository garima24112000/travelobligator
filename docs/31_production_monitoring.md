# Production monitoring and backup alerts

How production is watched: backend liveness, backup freshness, how the owner is told, what cannot be watched
from inside GitHub, and what to do when an alert arrives.

| Part | File |
| --- | --- |
| Workflow (hourly + manual) | `.github/workflows/production-monitor.yml` |
| Checks and alerting (standard library only) | `scripts/monitoring/production_monitor.py` |
| Tests (hermetic, part of the ordinary `pytest` run) | `backend/app/tests/scripts/test_production_monitor.py` |
| The backups being watched | `docs/30_automated_neon_backups.md` |

**Verification status:** hermetic tests and static checks only. Nothing here has run against production,
the real bucket or GitHub's issue API yet; the first manual run (section 7) is the first real execution.

## 1. Architecture

Three separate concerns, deliberately not mixed up:

| Concern | Mechanism | Covers | Does not cover |
| --- | --- | --- | --- |
| **Detection** | Two jobs of the hourly workflow: *Backend health* and *Backup freshness* | Backend down or broken; backups missing, stale, malformed; backup workflow disabled or failing; scheduled backups switched off; a check that cannot run | Anything while the workflow itself is not running |
| **Notification delivery** | The *Alerts* job: one GitHub issue per check in this repository, @-mentioning the owner; the run is marked failed | Every detected problem, once (no hourly repeats), and its recovery | Delivery when GitHub itself is down, or when the owner's GitHub notifications go nowhere |
| **Dead-man (heartbeat)** | Optional: each run pings an external service; the service alerts when pings stop or report failure | The workflow not running at all (disabled, paused for inactivity, GitHub outage, broken workflow) | Nothing until the owner creates the external check (section 4) |

**Without the heartbeat, monitoring is incomplete**: a GitHub workflow cannot report its own absence. That is
stated here rather than papered over.

Why a separate workflow rather than an extension of `uptime.yml` (development branch) or of the backup
workflow: production monitoring must not wait for the unreleased application branch, must not touch the
backup job that holds the database secret, and needs different permissions per job (OIDC only for the
backup check, `issues: write` only for alerting, nothing for the health check).

## 2. Checks and thresholds

### 2.1 Backend health

- One URL only: `GET https://travelobligator-backend.onrender.com/health`. No token, no cookie, no other
  endpoint; never sign-in, registration, trips or generation, so never a planning provider or model call.
  Redirects are not followed (a redirect is a failure).
- **Valid** means: HTTP 200, `Content-Type: application/json`, a body under 64 KiB whose JSON has
  `success: true` and `data.status: "ok"` (the backend's `/health` contract, unchanged since production
  commit `f55401c`). A 200 that is a hosting error page, a captive portal or any other JSON is a failure.
- **Attempts:** up to 4, each with a 60 s timeout, waiting 20 s, 60 s and 120 s between them: at most about
  8 minutes. The job is cut off at 12.
- **Classification:**

| Result | Meaning | Alert? |
| --- | --- | --- |
| `healthy (answered in N s)` | First attempt valid in under 10 s: the backend was awake | No |
| `healthy after a cold start (attempt N, first answer after N s)` | Valid, but the first answer took ≥ 10 s or an earlier attempt failed | No. A sleeping Render Free instance always starts this way |
| `no valid health response in 4 attempts over N min (last: …)` | Not valid for ~8 minutes, far longer than a cold start | **Yes** |

The log shows each attempt's status code and duration, never the response body.

**Cost of checking:** each request wakes a sleeping backend, which then stays up for Render's idle period
(about 15 minutes) and sleeps again. Hourly checks are not a keepalive, but they use roughly a quarter of
the hours in a month (Render Free's monthly instance-hour allowance covers this for one service; confirm in
the Render dashboard). They also revisit an earlier decision (runbook section 5.2 on the development
branch, and `Claude.md`): **scheduled backend checks stay off until the owner sets
`MONITOR_BACKEND_HEALTH_ENABLED=true`**, and that decision should then be recorded there too.

### 2.2 Backup freshness

- Lists `s3://travelobligator-db-backups-10192026/neon/` with `ListObjectsV2`. **No download, no
  decryption**: the session cannot read objects at all (section 5).
- **Valid backup:** a name the backup workflow writes
  (`neon/travelobligator-<UTC>-run<id>-<attempt>.pgdump.gpg`), at least 1 KiB, and an S3 `LastModified`
  that is a real time not more than 10 minutes in the future.
- **Age comes from S3's `LastModified`**, not from the name.
- **Problems:**

| Finding | Threshold |
| --- | --- |
| Empty prefix / no valid backup | Any time |
| Newest valid backup too old | Older than **36 hours** = one daily interval (backups run at 03:37 UTC) + 12 hours for GitHub's scheduling delays and a manual re-run. A backup a few hours late never alerts |
| Unexpected objects under `neon/` | Any zero-byte object, any object under 1 KiB, any unrecognised name, any unusable or future timestamp (only the backup workflow writes there, so these mean a bug or interference). A console "folder" placeholder `neon/` is ignored |
| Backup workflow not active | GitHub reports `disabled_manually`, `disabled_inactivity`, … |
| Scheduled backups switched off | The variable `NEON_BACKUP_SCHEDULE_ENABLED` is not `true` |
| Latest completed backup run on `main` failed | Conclusion `failure`, `timed_out` or `startup_failure`; reported at once, before the backups go stale |

The log shows counts and the newest backup's age, never an object name, size, ETag or account detail.

### 2.3 "Could not run"

An AWS refusal (`AccessDenied`), missing credentials, an unreadable answer, a GitHub API error, missing
configuration, or a check job that ended without a result (cancelled, timed out, runner failure). These are
alerted too, but worded differently: **the state is unknown**, which is not the same as "broken". A stale
backup is still reported as a problem when the GitHub part of the check fails, and the reverse.

## 3. Notifications

| Event | What the owner gets |
| --- | --- |
| First run with a problem | A new issue `[monitor] Backend health check failing` or `[monitor] Backup freshness check failing`, mentioning `@<owner>`, with the finding, first/last seen and the run link. GitHub notifies a mention by web, email and mobile according to the owner's notification settings. The workflow run is also marked failed. |
| First run where a check could not run | A new issue `[monitor] Backend health check could not run` / `… Backup freshness check could not run` |
| Every further failing run | The same issue's text is updated (last seen, latest finding). **No new notification**; the failed run is visible in Actions |
| First passing run | A comment `Recovered at …` (a notification) and the issue is closed |
| A check that was not run (switched off, or unticked in a manual run) | Nothing; its open issue stays as it is |
| A run cancelled by hand, or a check job that timed out | The check counts as "could not run" (an issue opens; the next passing run closes it) |
| Heartbeat configured | Sent **last**, after every result and alert update: a success ping only when every check that ran passed and every alert update succeeded; `/fail` on a problem or a failed alert update; **nothing** when the run crashed before the end or no check ran. The external service emails when pings stop or report failure (section 4) |

Issue text holds only the fixed findings above (no hostnames, keys or secrets); summaries are stripped of
`@` so they cannot mention anyone else. **The repository is public, so alert issues are public**: they say
that something is down or stale, nothing more.

Before relying on it, confirm delivery once (section 7, step 5): GitHub → Settings → Notifications →
"Participating, @mentions and custom" has email on, and the email arrives.

## 4. When GitHub stops scheduling

The workflow cannot notice that it is not running. Causes that silence it **and** the backups:

- the workflow or the variable `PRODUCTION_MONITOR_ENABLED` was switched off;
- **GitHub pauses scheduled workflows in a public repository after 60 days without activity** (a commit,
  for instance). Both this workflow and the backup workflow then stop; in 7 days the bucket's lifecycle
  rule has deleted every backup, and nothing alerts;
- a GitHub Actions outage, or the workflow file broken by a later change.

Within GitHub there is partial coverage only: the backup check notices the *backup* workflow being disabled
or failing, but nothing notices the *monitor* stopping.

**Recommendation (lowest complexity): a free [healthchecks.io](https://healthchecks.io) check**, which needs
owner approval because it creates an account and a credential:

1. Create a check: period **1 hour**, grace **2 hours** (so one delayed or slow run is tolerated), email
   notification to the owner.
2. Copy its ping URL and store it as the repository secret `MONITOR_HEARTBEAT_URL`. Nothing is sent
   anywhere until this secret exists. The URL is never printed.
3. Each run then pings the URL when everything passed, and `<URL>/fail` when a problem is open, so the
   owner also gets an email that does not depend on GitHub's notifications.

An external uptime monitor polling `/health` directly (UptimeRobot and the like) is **not** recommended:
polling every few minutes would keep the free-tier backend permanently awake.

## 5. Permissions and IAM

| Job | `GITHUB_TOKEN` permissions | AWS |
| --- | --- | --- |
| Backend health | `contents: read` | none |
| Backup freshness | `contents: read`, `id-token: write`, `actions: read` | OIDC → `TravelObligatorNeonBackupRole`, 900 s session, **inline session policy: `s3:ListBucket` on the bucket with prefix `neon/*` only** |
| Alerts | `contents: read`, `issues: write` | none |

- The existing role is reused unchanged. Its trust policy accepts
  `repo:garima24112000/travelobligator:ref:refs/heads/main`, which is exactly the subject of this workflow's
  scheduled and manual runs on `main` (the repository uses GitHub's default subject format, and no job uses
  an `environment`). Runs elsewhere are skipped before any credential is requested.
- A session policy can only **remove** permissions: this session cannot `GetObject` or `PutObject` even
  though the role can. A monitor bug cannot read, overwrite or create a backup.
- **Not yet verified against AWS:** that the role's `s3:ListBucket` statement allows a listing with prefix
  `neon/` (the backup workflow itself never lists). If it does not, the first run reports
  `S3 refused the listing (AccessDenied)` as "could not run", and the role's ListBucket condition needs
  `s3:prefix` to match `neon/` (for example `StringLike` `neon/*`). That is the only IAM change that could
  become necessary.
- No permanent AWS key, no access to decrypted backups, no pull-request trigger; actions pinned by commit
  (`actions/checkout` v7.0.1, `aws-actions/configure-aws-credentials` v6.3.0 `e1253824…`).

## 6. Owner response checklist

**Backend health check failing**

1. Open the issue's run: every attempt's status code and duration are listed. `no response` on all four
   attempts = not reachable; `HTTP 502/503` throughout = Render cannot start or route to it; a 200 that is
   "not JSON" or "status is not ok" = something else answers at that address.
2. Render dashboard → the service → **Events** and **Logs**: a failed deploy, a crash at startup (the
   backend refuses to start on a database not at its Alembic head), or a suspended free instance (monthly
   hours used up).
3. Check from your own machine: `curl -sS -o /dev/null -w '%{http_code} %{time_total}\n' https://travelobligator-backend.onrender.com/health`
   (allow a minute for a cold start).
4. Fix (redeploy the last good commit; rollback across a migration is a manual procedure, runbook section 4
   on the development branch). Do not change the database to make the check pass.
5. Run the monitor by hand (section 7); the issue closes when it passes.

**Backup freshness check failing**

1. Read the finding. *Stale / no valid backup*: open **Actions → Neon backup**: did the 03:37 runs happen,
   and did they fail?
2. A failed backup run: read its log (it names the failing step without secrets). Usual causes:
   `permission denied for table …` after a migration (grant SELECT, `docs/30` section 4); `server version
   mismatch` after a Neon major upgrade (raise the client version in the workflow); `AccessDenied` from S3;
   a refused URL or passphrase (the secrets); a GitHub outage.
3. *Workflow not active* or *scheduled backups switched off*: re-enable the workflow / set
   `NEON_BACKUP_SCHEDULE_ENABLED=true`. *disabled_inactivity* means the 60-day pause: re-enable it and expect
   the monitor to be paused too (section 4).
4. *Unexpected objects*: list the prefix with your own read access and find out who wrote them; do not delete
   anything before you know (it may be evidence).
5. Fix, then **Run workflow** on Neon backup, then run the monitor by hand. Then do a restore drill
   (`docs/30` section 5.3) if the cause could have affected the backup content.
6. If no valid backup is less than 7 days old there is nothing in S3 to restore from: Neon's own
   point-in-time history is the only fallback, so treat it as urgent.

**A check could not run**

1. The issue names the reason. `AccessDenied` / no credentials: the role, its trust policy or its
   ListBucket condition (section 5). GitHub API error: usually transient; re-run. Job cancelled or timed
   out: re-run and look at the runner log.
2. Until it runs again the state is unknown: check backups (Actions → Neon backup) or the backend by hand.

**Heartbeat email (if configured)**

1. Actions → Production monitor: are runs happening? If not: disabled, `PRODUCTION_MONITOR_ENABLED` unset,
   60-day pause, or a GitHub outage (githubstatus.com). Re-enable and run by hand.
2. If runs happen but fail before the Alerts job: open the failing run.

## 7. Running it and switching it on

**By hand:** Actions → **Production monitor** → Run workflow → branch `main`, with *backend* and/or
*backups* ticked. A manual run opens and closes issues exactly like a scheduled one.

**Activation (after this change is merged into `main`):**

1. Confirm the first real backup is in S3 (it is, per the backup record) and `NEON_BACKUP_SCHEDULE_ENABLED`
   is `true`.
2. Run by hand with only *backups* ticked. Expected: `backup-freshness: OK - fresh: …`. If it reports
   `AccessDenied`, see section 5 before going on.
3. Run by hand with only *backend* ticked. Expected: `healthy` or `healthy after a cold start`.
4. Set the repository variable `PRODUCTION_MONITOR_ENABLED=true` (scheduled backup checks start hourly).
5. Confirm notification delivery once: the simplest safe test is to set `NEON_BACKUP_SCHEDULE_ENABLED` to
   `false` for one manual monitor run (the backup check then reports *scheduled backups are switched off*,
   which opens an issue and @-mentions you), set it back to `true`, and run again (the issue closes). Do
   this well away from 03:37 UTC so no backup is skipped.
6. Decide on the scheduled backend check (section 2.1); if yes, set `MONITOR_BACKEND_HEALTH_ENABLED=true`
   and record the changed decision in the runbook and `Claude.md`.
7. Decide on the heartbeat (section 4); if yes, create it and add `MONITOR_HEARTBEAT_URL`.

**To switch off:** delete `PRODUCTION_MONITOR_ENABLED` (all scheduled runs stop) or
`MONITOR_BACKEND_HEALTH_ENABLED` (backend checks stop), or disable the workflow.

## 8. Remaining risks

- **No independent watchdog until the heartbeat exists.** The 60-day inactivity pause can silently stop
  both backups and monitoring; with 7-day S3 expiry, that ends with no backups.
- **Delivery rests on GitHub notifications** (and, with the heartbeat, on one email address). If the owner
  does not read them, nothing escalates.
- **Detection latency:** a backend outage is seen within about an hour plus up to 8 minutes of retries
  (more if GitHub delays the schedule); a missed backup is alerted 36 hours after the last good one (a failed
  run is reported at the next hourly check).
- **Liveness only:** `/health` does not touch the database. A backend that is up but cannot reach Neon passes.
  The deeper check (`/ready` with `OPS_TOKEN`) is deliberately not automated: it would need a production
  token in GitHub.
- **Public alerts:** issues in a public repository show that production is down or backups are stale.
- **Freshness is not restorability:** the monitor proves an encrypted object of a sane size arrived on time,
  not that it decrypts and restores. That is what the restore drill (`docs/30` section 5.3) is for.
- **One process's view:** retries happen from one GitHub-hosted runner; a regional network problem there can
  look like an outage (it is then a "problem" for one run and closes on the next).
