# 29. Release readiness checklist (operator)

For the project owner and whoever operates the deployment. One list: what is done (with its evidence),
what a person still has to do (with exact steps), the decisions only the owner can make, and the blockers.
It changes no threshold, score, procedure or setting. Procedures are those of
`docs/25_operations_runbook.md`; this document only orders them and says what to record.

**Status words.** **DONE**: evidence exists and is named. **OPEN**: a person must do it; steps below.
**DECISION**: only the owner can decide. **BLOCKER**: release cannot proceed until resolved.
Nothing is marked DONE without evidence.

**About dashboard paths.** Menu names below are those of the Neon, Render, Vercel and GitHub consoles as
known when this was written; consoles change their labels. If a label differs, find the setting by the name
given here and change nothing else. Values are given only where the repository establishes them.

## 0. Facts to start from (from the repository; verify them first)

1. **The repository records production as commit `f55401c`, schema `d41a7e2c9b53`** (`docs/23` line 19,
   `docs/22` section 14, `docs/25` section 1). That code predates everything since: the P0 cost and abuse
   limits, trip and account deletion, the P1-B security headers and configuration checks, the beta copy,
   and Phases 1–3. The runbook describes it as having "no per-user limit, no deployment-wide limit, no cap
   on simultaneous generations … and no sign-up or sign-in limit" (`docs/25` section 4.3).
2. **This branch is not on `main`.** The uptime workflow and the three operator scripts
   (`db_backup_restore.py`, `ops_report.py`, `public_check.py`) are not on `main`. Run the scripts from a
   checkout of this branch. `origin/main` (`dc65466`) does have the automated, encrypted daily Neon backups
   to S3 (PR #2, `docs/30_automated_neon_backups.md` on `main`; recorded here in `docs/25` section 2.6) and
   the production monitor (backend health, backup freshness, alert issues; PR #3,
   `docs/31_production_monitoring.md` on `main`), both merged and, per the owner, activated.
3. **There is no staging environment.** Manual tests use the isolated local stack of section 6.
4. **The quality gate is NOT MET** (`docs/27`, `docs/28` section 1), against the unchanged target: median
   overall ≥ 4 of 5; no scenario below 3; zero part-A failures.

## 1. Completed checks (with evidence)

| # | Check | Evidence |
| --- | --- | --- |
| 1.1 | Six CI jobs green on `65801a7`, first attempt | Actions run 39082560024: Backend / Hermetic 6,707 passed, 142 skipped; Backend / Integration 6,849 tests, 0 skipped; Browser / E2E 178 passed, 0 failed, 0 skipped; Frontend / Build, Containers / Build & Smoke, Security / Scan success |
| 1.2 | Backup → restore → verify, refusals, migrate + roll back, on throwaway databases | `test_backup_restore_drill_p1d_integration.py` in Backend / Integration (run 39082560024); `docs/25` section 2.5 |
| 1.3 | Production configuration is validated at startup (no missing credential, no disabled stage, strict cookies, CORS) | `test_production_completeness_p1b.py`, `test_production_config_203b.py`, `test_operational_config_200d.py` (in 1.1) |
| 1.4 | `render.yaml` holds no secret; only `.env.example` is tracked | `render.yaml`: six `sync: false` values, two `generateValue`; `git ls-files` |
| 1.5 | Security headers on the production build | `npm run check:headers` 13/13 (Phase 3) and Frontend / Build |
| 1.6 | Health, readiness, metrics, Redis degradation, abuse limits, deletion | Test files named in `docs/28` section 3 (758 passed locally; integration variants in 1.1) |
| 1.7 | No fixable container finding | Trivy gate in Containers / Build & Smoke (run 39082560024) |
| 1.8 | Automated browser checks (Chromium only) | 178 tests at desktop, 375 px, 320 px, 200 % zoom; axe-core rules, keyboard, live regions, maps, tile outage |
| 1.9 | Frozen holdout intact | 21 files byte-identical to their SHA-256 before Phase 1 |
| 1.10 | Phase 1A–1B (container hardening, locked-out deletion, privacy and operations documents) validated **locally** on 2026-10-21 before the local commit. **CI has not yet run on that commit** | Equivalent of each CI job, offline, throwaway services only: **Hermetic** `compileall` + 6,767 passed, 143 skipped; **Integration** (PostgreSQL 16 + Redis 7.4, both gates, empty database migrated to `a94c0e7d2b61`, one head) 6,910 tests, 0 skipped, 0 failed — 2 backup-drill tests fail with the Mac's `pg_dump` 18 (it writes `SET transaction_timeout`, a PostgreSQL 17+ setting a 16 server rejects) and all 3 drill tests pass with the 16 client CI uses; **Browser / E2E** 178 passed (all viewports); **Frontend** unit 167/167, `tsc`, lint, build, `check:headers` 13/13; **Containers** image audit passed (no setuid/setgid file, no `pip`), Trivy HIGH/CRITICAL-with-a-fix gate exit 0 on both images, container smoke passed; **Security** Trivy secret scan 0 findings, dependency gate exit 0 |
| 1.11 | Locked-out deletion procedure rehearsed on a disposable database | By the owner, personally, 2026-10-21 (section 7) |

Not done, and not to be read as done: anything against the deployed Neon, Render or Vercel services in
this list; manual device, browser and screen-reader testing; a new acceptance evaluation.

## 2. Release blockers

| # | Blocker | Resolved by |
| --- | --- | --- |
| B1 | Production (per the repository) runs pre-P0 code with no generation or sign-up limits on a public URL | A0 below, then a deployment of a reviewed commit (`docs/25` section 3) — or, until then, restricting public generation (`docs/25` section 4.3 lists three ways) |
| B2 | This branch is not merged; `docs/25` section 3 step 1 requires the deployed commit to be green on `main`. (PR #6: six CI checks green, no conflicts, owner 2026-10-21; still unmerged) | Owner merges after review; CI re-runs on `main` |
| B3 | **Restricted registration exists in code, not yet in production.** The shareable beta link (section 8) makes new accounts possible only for holders of the link, and production refuses open registration; it takes effect only once a commit containing it is deployed (B1, B2). It restricts registration to *link holders*, not to named people (section 8.4) | Deployment, then section 8 |
| B4 | **OPEN — partly deferred, not complete.** No restore of a backup into a disposable **Neon** branch (A1) has been done. Recovery evidence so far (owner-reported, section 7): an earlier automated, encrypted S3 backup was restored into an isolated local PostgreSQL with the drill of `docs/30` section 5.1; a Neon point-in-time recovery branch was created and inspected; a fresh encrypted S3 backup completed on 2026-10-21 and the production monitor passed (four valid backups, newest 0.1 h old). **Owner decision, 2026-10-21 (D10):** the full S3-to-Neon-branch restore drill is deferred **for the M1 controlled deployment only** (merge, migration and backend/frontend deployment of this release, with the smoke test of section 9.5), on the conditions in section 9.5. **The drill is still required before the beta invitation link is shared with anyone, and before unrestricted public registration** (`docs/28` section 2 item 2; `docs/25` section 2.5). M1 pre-migration conditions 1 (export verified against production) and 2 (restore-point branch `pre-m1-20261021`) are done (section 7); conditions 3–6 and the drill are not. | A1, before the link is shared; M1 pre-migration conditions in section 9.5 |
| B5 | Monitoring: merged and activated (owner), but delivery of an alert to the owner has not been shown to arrive, and nothing independent of GitHub notices scheduled workflows stopping (no heartbeat). The frontend uptime workflow is not on `main` (B2) | A3 steps 5–6; heartbeat decision D8 |
| B6 | Backup storage: the automated backups are in S3 (client-side encrypted, 7-day expiry, role-restricted; `docs/25` section 2.6) and the privacy notice now describes them ("about seven days"). **Before release:** confirm bucket versioning is off or a noncurrent-version expiry exists (otherwise "about seven days" is untrue), and that nobody else can read `neon/*`. Manual exports (`docs/25` section 2.2) are as before | A2 |
| B7 | Deletion for someone who cannot sign in: **decided and implemented** (D3a–D3c; `docs/25` section 8.1, `backend/scripts/locked_out_account_deletion.py`, commit `179df2c`), and described in the privacy notice. **Rehearsed by the owner on a disposable database on 2026-10-21: passed** (section 7). **Contact mailbox ready (owner, 2026-10-21):** 2-Step Verification enabled and both message texts of `docs/25` section 8.1 saved as Gmail drafts. **Not yet usable:** two prerequisites of `docs/25` section 8.2 remain open — the release must be deployed, and the operator must be able to obtain, securely, Neon's direct connection string and Render's `SESSION_SECRET_KEY` | `docs/25` section 8.2 |
| B8 | Unfixed container findings (2 CRITICAL, 61 HIGH, 111 MEDIUM OS findings, none fixable). The setuid path and the `pip` findings are removed by the Phase 1A Dockerfile change (section 5, commit `179df2c`), which takes effect only once that change is merged and deployed. **Resolved for the restricted M1 deployment only:** D5 owner-accepted 2026-10-21 for the 2 CRITICAL and 61 HIGH. **Still open** for sharing invitations (fresh Trivy re-check) and for unrestricted public registration (the 111 MEDIUM unreviewed; read-only root and `no-new-privileges` unverified on Render) | DECISION D5 (section 5) |
| B9 | Quality gate NOT MET | DECISION D1 (beta exception), and a new evaluation (D6) before any wider release |

## 3. Required decisions (owner)

| # | Decision | Options the repository gives | Record it in |
| --- | --- | --- | --- |
| D1 | Explicit exception for a limited beta despite the failed quality result | **DEFERRED (owner, 2026-10-21); not accepted.** Not needed for the restricted M1 deployment, which invites no one; required before the beta invitation link is shared. Options: accept, naming median 2.0 and eight plans "poor" (`docs/27` section 8); or do not release. The quality gate stays **FAILED** | `docs/25` section 7 |
| D2 | Backup retention | **Automated backups:** 7-day S3 expiry is in force and stated in the notice ("about seven days"); approve the wording (section 9.8) after the versioning check of B6. **Manual exports:** the recommendation is 30 days on encrypted, access-restricted storage; it becomes policy only after A2's four checks, and until then the notice states no period for them | `docs/25` sections 2.2, 2.6 and 7; the privacy notice in the same commit |
| D3 | Deletion for someone who cannot sign in | **Decided 2026-10-21:** **D3a** contact `garimaprachi2411@gmail.com` (the owner's Gmail; published in the notice); **D3b** a manual mailbox challenge — a code sent **to** the account's registered address must come back, never a message merely *from* it (residual risk accepted: it proves control of the mailbox, not who registered); **D3c** the operator command `locked_out_account_deletion.py`, which calls the application's own deletion service. **Still open:** whether this hand-run procedure suffices for *unrestricted* registration, or password recovery (V1.1) must come first | `docs/25` sections 7, 8.1, 8.2 |
| D4 | Whether link-holder registration is restrictive enough (B3) | (a) the shareable link of section 8 (implemented; anyone the link is forwarded to can sign up, bounded by the signup cap); (b) in addition, access control for the whole site at the frontend host — check in Vercel whether Deployment Protection can cover **Production** on the current plan (not verified here); (c) do not open the beta | `docs/25` section 7 |
| D5 | Risk acceptance per container finding | **OWNER-ACCEPTED FOR RESTRICTED M1 ONLY (2026-10-21).** Owner's words: "I approve D5 for the restricted M1 deployment only. D1 remains deferred." Scope: the hardened backend image (setuid/setgid bits and `pip` removed) with its **2 CRITICAL and 61 HIGH** residual Debian OS findings, none fixable upstream per the documented scan, with the exposure stated per row in section 5. **Not accepted:** the **111 MEDIUM** findings (unreviewed one by one); anything beyond M1. **Unverified on Render:** read-only root filesystem and `no-new-privileges`. **Conditions:** re-check Trivy before the beta invitation link is shared (a fresh result, recorded in section 7); rebuild when upstream fixes are published. Sharing invitations and unrestricted public registration need their own decision | `docs/28` section 4 |
| D6 | New acceptance evaluation: scenarios and budget | `docs/28` section 5 (≤ 1,000 Geoapify credits and ≤ 80 model requests for ten scenarios) | A new document next to `docs/27` |
| D7 | Whether to merge and deploy | — | `docs/22` section 14, `docs/23` |
| D9 | Model features outside the evaluated configuration | `AI_FEEDBACK_INTERPRETER_*` and `TARGETED_REGENERATION_ENABLED` exist in the Render dashboard but not in `render.yaml`: keep them off, or switch them on deliberately (the release refuses to start with the interpreter on but no connected provider and key) | `docs/25` section 7 |
| D8 | Monitoring scope | (a) scheduled backend health checks (`MONITOR_BACKEND_HEALTH_ENABLED=true`): hourly, each wakes the free-tier backend, which reverses the recorded "scheduled backend check stays off" decision (`docs/25` section 7, `Claude.md`); (b) an external heartbeat (free healthchecks.io check, secret `MONITOR_HEARTBEAT_URL`) as the only watchdog for GitHub no longer running workflows (`docs/31` section 4); (c) neither | `docs/25` section 7 and `Claude.md` for (a); `docs/31` section 7 |
| D10 | Whether the Neon-branch restore drill (A1, B4) must precede the M1 deployment | **Decided 2026-10-21 by the owner: deferred for the M1 controlled deployment only.** Basis: the encrypted S3 backup restored into an isolated local PostgreSQL; the Neon point-in-time recovery branch inspection; the fresh 2026-10-21 S3 backup and the passing backup monitor; the release's migrations are additive (two tables, two indexes). What stays untested is restoring a dump **into Neon** (network duration, client vs server version, what a new Neon database pre-creates, `pg_control_system()` permission, objects outside `public`; `docs/25` section 2.5). That matters on the path where the Neon project or its history is lost, which M1 does not make more likely. **Not deferred:** the drill before the beta link is shared and before unrestricted public registration; and the M1 pre-migration conditions of section 9.5 | Section 7; B4 |

## 4. Operator procedures

Each step: where, what, the value, how to verify, what to record. **Record evidence without copying a
secret, a connection string, an email address or itinerary text.**

### A0. Find out what production actually runs (read-only; do this first)

1. **Render** — `dashboard.render.com` → service `travelobligator-backend` → **Events** (or **Deploys**).
   Read the commit of the live deploy.
2. **Render** → **Logs**: the most recent `Application started.` line; read `migration_head`.
3. **Vercel** — `vercel.com` → the project → **Deployments**: the commit of the deployment marked
   **Production**.
- *Verify:* both commits are known.
- *Record:* the two commit ids, `migration_head`, the date.
- *If the backend is `f55401c` (B1):* decide at once whether to restrict public generation until a reviewed
  commit is deployed. The three ways are in `docs/25` section 4.3. Each is a dashboard action that changes
  production; none is executed by this document.

### A1. Neon history retention and the restore drill (`docs/25` sections 2.1–2.5)

**Timing (D10, 2026-10-21):** the restore drill below is deferred for the M1 controlled deployment only. Do
it **before the beta invitation link is shared** (section 8.3) and before unrestricted public registration.
The M1 pre-migration conditions that replace it for now are in section 9.5.

**Read the retention value**
1. `console.neon.tech` → the project → **Settings** → the history / restore-window setting (named
   **Instant restore** or **History retention**, depending on the console version).
- *Record:* the value exactly as shown, and the plan name. Write it into `docs/25` section 2.1, which states
  none today.

**Restore drill into a disposable branch**
1. Prerequisite: `pg_dump --version` and `pg_restore --version` on your machine ≥ the Neon server's major
   version (the project dashboard shows the PostgreSQL version).
2. Take a backup (`docs/25` section 2.2), at a quiet moment (`ops_report.py` shows no running job). The
   **direct** connection string is under **Connect** (choose the production branch, the application's
   database and the direct, non-pooled option):
   ```bash
   read -rs BACKUP_SOURCE_DATABASE_URL && export BACKUP_SOURCE_DATABASE_URL
   python backend/scripts/db_backup_restore.py backup --out ~/travelobligator-backups
   unset BACKUP_SOURCE_DATABASE_URL
   ```
3. Neon → **Branches** → **New branch**. Parent: the production branch. Name: e.g.
   `restore-drill-<YYYYMMDD>`. This branch holds a copy of production data from the moment it is created.
4. In the new branch → **Databases** → **New database**. Name it e.g. `restore_drill`. It must be empty.
5. **Connect** → choose the **new branch** and the **new database**, direct connection. Copy that string
   now, from this screen.
6. Restore and verify (`docs/25` sections 2.3–2.4):
   ```bash
   read -rs RESTORE_TARGET_DATABASE_URL && export RESTORE_TARGET_DATABASE_URL
   python backend/scripts/db_backup_restore.py restore \
       --dump ~/travelobligator-backups/travelobligator-<stamp>.dump --confirm-empty-target restore_drill
   export VERIFY_DATABASE_URL="$RESTORE_TARGET_DATABASE_URL"
   python backend/scripts/db_backup_restore.py verify \
       --manifest ~/travelobligator-backups/travelobligator-<stamp>.manifest.json
   unset RESTORE_TARGET_DATABASE_URL VERIFY_DATABASE_URL
   ```
7. Neon → **Branches** → the drill branch → **Delete**. Confirm it is gone from the list.
- *Verify:* `restore` reports success; `verify` exits 0; the branch no longer exists.
- *Record in `docs/25` section 2.5* (new row): date; server and client versions; backup, restore and verify
  durations; the revision `verify` printed; table count; whether every table matched; whether the new
  database was accepted as empty; whether the role may call `pg_control_system()` (the tool does not report
  it: in Neon's **SQL Editor** on the drill branch, run `select system_identifier from pg_control_system();`
  and record whether it is permitted — a read-only query); any object outside schema `public`; that the
  branch was deleted. Then keep or delete the backup files per D2.

### A2. Backup destination, retention and access (`docs/25` section 2.2)

Answer the four questions; each needs evidence, not intent.

| Question | Acceptable evidence |
| --- | --- |
| Where are the files kept, and is it encrypted at rest? | The location's name and the encryption setting as shown by the device or service (e.g. FileVault on, or the storage service's encryption page) |
| Who can read it? | The access list of that location: ideally one person; no shared or synced folder |
| How is a file older than the retention deleted? | The calendar task or automation that does it, and its first actual execution |
| Is a deleted file gone from sync history, trash and device backups? | A check of each of those places after a test deletion |

- *Record:* the answers in `docs/25` section 2.2's table. Only when all four are verified does D2 become a
  policy; then change the privacy notice in the same commit (never before).
- *The automated S3 backups* (`docs/30`) answer the four questions differently and need their own evidence:
  S3 default encryption and the client-side GnuPG encryption (`docs/30` section 2); the principals that can
  read `neon/*` (IAM, bucket policy); the 7-day lifecycle rule **and** bucket versioning (with versioning on,
  expiry leaves old versions unless a noncurrent-version rule deletes them); no other copy. A 7-day expiry is
  then in force for these copies, which differs from D2's 30-day recommendation for manual exports and from
  the privacy notice's "no retention period set": decide the wording in D2 and change the notice in the same
  commit, before this branch ships.

### A3. Monitoring and notification delivery

**Production monitor** (`docs/31_production_monitoring.md` section 7; independent of this branch):

1. Merge `ops/production-monitoring` into `main` after review. Nothing scheduled starts by itself.
2. Run **Actions → Production monitor** by hand with only *backups*: `backup-freshness: OK`. An
   `AccessDenied` here means the backup role's `s3:ListBucket` condition does not allow the prefix `neon/`
   (`docs/31` section 5).
3. Run it by hand with only *backend*: `healthy` or `healthy after a cold start`.
4. Set `PRODUCTION_MONITOR_ENABLED=true`. Hourly backup-freshness checks start.
5. Verify delivery once (`docs/31` section 7 step 5): an alert issue opens with an @-mention that reaches
   your inbox, and closes with a "Recovered" comment.
6. Decide D8 (scheduled backend checks; heartbeat) and record it.
- *Verify:* a scheduled run appears in Actions; the alert notification arrived; the issue closed itself.

**Frontend uptime** (`docs/25` section 5.2; on this branch only):

1. **The schedule runs only after B2** (the workflow is not on `main`). After the merge: GitHub → the
   repository → **Actions** → **Uptime**: scheduled runs appear at `23 */4 * * *` and `41 6 * * *` (UTC).
2. Leave the backend check **off**: the repository variable `UPTIME_CHECK_BACKEND` stays unset (owner's
   decision, `docs/25` section 7).
3. **Notification settings** — GitHub → your profile picture → **Settings** → **Notifications** →
   **System** → **Actions**: choose the delivery (email and/or web) and "only notify for failed workflows".
   GitHub sends notifications for a **scheduled** workflow to the user who last changed its schedule (cron).
4. **Testing delivery.** The manual **Run workflow** form has one input (`backend`) and cannot point the check
   at a wrong address, so `docs/25`'s suggestion ("against a deliberately wrong `--frontend`") is not possible
   from the form as it stands. Options: wait for a real failure; or, with the owner's approval, run the
   workflow once from a throwaway branch in which the check targets an address that does not exist, then
   delete the branch. This document does not do it.
- *Verify:* a scheduled run appears; a failure notification is received in the chosen inbox.
- *Record in `docs/25` section 7:* the date of the first scheduled run and of the first received failure
  notification, and to whom it went.

### A4. Production configuration and model settings (`docs/25` section 3 step 2)

1. **Render** → service → **Environment**. Compare **key by key** with `render.yaml` (it is the intended
   configuration; the live service holds what the dashboard holds). Values established by the repository:
   - limits: `GENERATION_MAX_JOBS_PER_USER_PER_WINDOW=5`, `GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW=25`,
     `GENERATION_MAX_CONCURRENT_JOBS=2`, `GENERATION_LIMIT_WINDOW_SECONDS=86400`, `MAX_TRIPS_PER_USER=20`,
     `MAX_TRIP_DAYS=7`, `SIGNUP_MAX_PER_WINDOW_GLOBAL=30`, `LOGIN_MAX_FAILURES_PER_ACCOUNT=10`,
     `CLIENT_IP_TRUSTED_PROXY_HOPS=0`;
   - engine: `PLACES_PROVIDER=geoapify`, `ROUTING_PROVIDER=geoapify`, `GEOCODING_PROVIDER=geoapify`,
     `GEOAPIFY_MAX_CREDITS_PER_GENERATION=100`, `INVENTORY_SUFFICIENCY_GATE_ENABLED=true`;
   - models: `AI_CANDIDATE_PROPOSAL_PROVIDER=groq`, `AI_ITINERARY_REASONING_PROVIDER=groq`,
     `ITINERARY_NARRATOR_PROVIDER=groq`, `GROQ_MODEL=openai/gpt-oss-20b`, `LLM_FAILOVER_ENABLED=true`,
     `LLM_PRIMARY_PROVIDER=groq`, `LLM_SECONDARY_PROVIDER=gemini`, `GEMINI_MODEL=gemini-3.5-flash-lite`,
     and the three stage switches `true`;
   - present, value not copied: `DATABASE_URL`, `REDIS_URL`, `GROQ_API_KEY`, `GEMINI_API_KEY`,
     `GEOAPIFY_API_KEY`, `BACKEND_CORS_ORIGINS` (the https Vercel origin), `SESSION_SECRET_KEY`, `OPS_TOKEN`.
2. **Groq console** → **Models**: `openai/gpt-oss-20b` is listed. **Google AI Studio** → models:
   `gemini-3.5-flash-lite` is listed. A past benchmark does not prove a model is still offered.
3. **Vercel** → project → **Settings** → **Environment Variables**, environment **Production**:
   `BACKEND_ORIGIN` = the Render origin, `API_BASE_URL=/api`.
- *Verify:* after a deploy of a reviewed commit, its log shows one `Application started.` line and no
  startup refusal (a missing or contradictory value stops startup and names the variable).
- *Record:* "every key present / differences: …" (names only), the two model names and the date checked.

### A5. Account deletion for someone who cannot sign in (`docs/25` section 8)

There is nothing to execute: no verified procedure exists and none may be improvised.
- *Today:* a locked-out person can be told truthfully that recovery is not available, that ownership cannot
  be verified without the password, and that the project therefore does not delete accounts on request.
- *Required:* D3.
- *Record:* the decision and its date in `docs/25` section 8.

### A6. Rollback readiness (`docs/25` section 4)

Before the deployment that leaves `f55401c`, confirm — do not assume:
1. A fresh backup exists and its manifest is kept (A1 step 2).
2. Read-only: `(cd backend && alembic current)` against production prints the revision A0 found.
3. `(cd backend && alembic heads)` in the commit to deploy prints its expected head.
4. You know which restriction you would apply if a rollback across the migrations became unavoidable
   (`docs/25` section 4.3: suspend the service, remove the provider keys, or host-level access control).
5. Vercel → **Deployments**: the previous production deployment is still listed (it is the instant-rollback
   target, section 4.1).
- *Record:* the four answers and the date, before deploying.

## 5. Security findings: risk register (backend image)

**Sources.** CI run 39160676077 on `615ae9a` (Containers / Build & Smoke, Trivy, amd64) and a local re-scan
of an image built from the same Dockerfile on 2026-10-20 (Trivy 0.67.2, arm64; same Debian 12.15 package
versions and the same counts). **Reachability was examined in the image itself:** the application was
imported the way the server starts it, with production-style settings and no network, and the process's
mapped libraries (`/proc/self/maps`), imported modules, setuid binaries and exported symbols were recorded.
Exploitation was not attempted for any finding.

**Counts.** OS (Debian 12.15): 2 CRITICAL, 61 HIGH, 111 MEDIUM, **none with a fixed Debian version** (of the
63 CRITICAL/HIGH: 55 `affected`, 7 `fix_deferred`, 1 `will_not_fix`). Python: 10 MEDIUM, all in `pip` 24.0
(two copies: `/usr/local` and `/opt/venv`), **all with a fixed version** (pip 25.3 to 26.2). The earlier
"Fixable: 0" was true only for HIGH/CRITICAL.

**Phase 1A hardening (2026-10-21, `backend/Dockerfile`, uncommitted).** Two changes, both in the image only:
`pip` is uninstalled from the builder's virtualenv and from the base interpreter, and every setuid/setgid bit
is removed in the runtime stage (as root, before `USER 10001:10001`). Same Trivy 0.67.2 and database for both
images, same cached base image:

| | Before (current `main`/`615ae9a` Dockerfile) | After (hardened) |
| --- | --- | --- |
| OS packages (Debian 12.15) | 2 CRITICAL, 61 HIGH, 111 MEDIUM, 107 LOW; 0 fixable | **unchanged**: 2 CRITICAL, 61 HIGH, 111 MEDIUM, 107 LOW; 0 fixable |
| Python packages | 10 MEDIUM + 2 LOW, all `pip` 24.0, all fixable | **0** |
| setuid/setgid files | 11 (`mount`, `umount`, `su`, `passwd`, `chsh`, `chfn`, `gpasswd`, `newgrp`, `chage`, `expiry`, `unix_chkpwd`) | **0** (the binaries remain, without the bit) |
| `pip` importable / on `PATH` | yes / yes | no / no |

The OS findings are **not** fixed: no package changed, and the scanner still reports every one of them,
including the `mount` advisory. What changed is reachability: the setuid path that made the `mount` TOCTOU a
possible privilege escalation no longer exists. Python's standard library still contains the bundled
`ensurepip` wheel `pip-24.0` (used only to bootstrap new virtualenvs); Trivy does not report it, and nothing
in the image runs `ensurepip`.

**Escalation paths checked in the hardened image** (as uid 10001, also started **without** the Compose
hardening, i.e. default Docker capabilities, writable root, `no-new-privileges` off): no setuid/setgid file;
no file capabilities on any binary; no `sudo`; permitted and effective capability sets are empty
(`CapPrm`/`CapEff` 0); `mount` refuses ("must be superuser"); `su` fails; `/etc/passwd`, `/etc/shadow`,
`/etc/group`, the application code, the virtualenv and the Python library are not writable; no writable
directory outside `/tmp`, `/var/tmp`, `/dev`, `/proc`. **Remaining path:** a kernel or container-runtime
vulnerability, which no image change addresses (Render's isolation; not verifiable here).

| Package(s) / advisory | Severity | In the running app? (verified) | Assumptions | Fix / mitigation available | Remaining exposure |
| --- | --- | --- | --- | --- | --- |
| `zlib1g` CVE-2023-45853 (MiniZip `zipOpenNewFileInZip4_64`) | CRITICAL (`will_not_fix`) | `libz` **is loaded** (HTTP decompression, `zipfile` imported by dependencies), but the vulnerable function is **not in it**: the library does not export `zipOpenNewFileInZip4_64`, no `minizip` package is installed, `zlib1g` ships only `libz.so` | — | None needed for this image | **None found** (the scanner matches the source package) |
| `libssl3` / `openssl` CVE-2026-84782 (DTLS retransmission) | HIGH | `libssl` **is loaded** (all TLS), but Python's `ssl` module exposes **no DTLS** (no DTLS name; OpenSSL 3.0.22) | No dependency uses DTLS through another binding (none found among imported modules) | Debian update when released | **Very low**: DTLS is not used |
| `libsqlite3-0` CVE-2025-7458, CVE-2026-11822/11824 (integer overflow; FTS5 code execution; heap overflow) | CRITICAL + 2 HIGH | `libsqlite3` **is loaded** (`app/storage/provider_cache_store.py` imports `sqlite3` at import time). The only `sqlite3.connect` in the application is in the SQLite cache store, reached only through `get_provider_cache_store`, which returns Redis in production; production refuses `PROVIDER_CACHE_BACKEND=sqlite` at startup | No dependency opens an SQLite database at run time (not traced exhaustively); the FTS5 issues need attacker-supplied database content | Debian update when released; or import `sqlite3` lazily so the library is not loaded in production (code change, not made) | **Low**: no attacker-controlled SQLite input path found |
| util-linux family (`util-linux`, `mount`, `libblkid1`, `libmount1`, `libsmartcols1`, `libuuid1`, `bsdutils`, `util-linux-extra`): CVE-2026-53613 (TOCTOU in `mount`), -53615 (DOS partition parser), -76642 (mount helper post-hooks), -78408 (`nsenter`), -78409/-78410 (`X-mount` options) | 48 HIGH | `libblkid`, `libmount`, `libsmartcols` **not loaded**; `libuuid` **loaded** (UUIDs). The application never runs these programs. **`/usr/bin/mount` and `umount` are setuid-root** in the image (also `su`, `passwd`, `chsh`, `chfn`, `gpasswd`, `newgrp`, `chage`, `expiry`, `unix_chkpwd`) | That the advisories concern the programs and parsers named in their titles, not `libuuid`'s UUID code (from titles; not checked in source). Whether Render runs containers with `no-new-privileges` is unknown | **Done (Phase 1A):** setuid/setgid bits removed; verified `mount` now refuses an unprivileged caller even without `no-new-privileges`. Debian update when released | **Low (post-compromise)**: the programs remain and run with the caller's rights only; the scanner still reports all 48 findings |
| `libncursesw6`, `libtinfo6`, `ncurses-base`, `ncurses-bin` CVE-2025-69720 | HIGH | **Not loaded**; `curses` and `readline` not imported | — | Debian update | **None found** in the running app |
| `gzip` CVE-2026-41992 (LZH) | HIGH (`fix_deferred`) | `/usr/bin/gzip` present, **never run** by the app (`gzip`, `tarfile` modules not imported) | — | Debian update | **Post-compromise only** |
| `perl-base` CVE-2026-9538 (Archive::Tar DoS) | HIGH (`fix_deferred`) | `/usr/bin/perl` present (essential package), never run | — | Debian update | **Post-compromise only** |
| `libacl1` CVE-2026-54369 (symlink escalation) | HIGH (`fix_deferred`) | **Not loaded** | — | Debian update | **Post-compromise only** |
| `libsystemd0`, `libudev1` CVE-2026-16742 (systemd-homed) | HIGH (`fix_deferred`) | **Not loaded**; no systemd runs in the container | — | Debian update | **None found** |
| `pip` 24.0: CVE-2025-8869, CVE-2026-13346, -3219, -6357, -8643 (archive and index handling) | 10 MEDIUM (+2 LOW) | **Done (Phase 1A):** `pip` removed from the runtime image; the re-scan reports **0** Python findings | — | — | **None reported** (the `ensurepip` bundled wheel remains in the standard library, unreported and unused) |
| 111 MEDIUM OS findings | MEDIUM | Not reviewed one by one | — | Debian updates | **Unreviewed**: not acceptable without review |

**Facts about the whole container** (verified in the image): the process runs as uid/gid 10001 (non-root);
no network was needed to import the application. **Not verified on Render:** a read-only root filesystem
(set in `docker-compose.yml`, not known for Render) and `no-new-privileges`.

**Status of the recommendations:**
1. Strip setuid/setgid bits: **done in Phase 1A** (uncommitted; `scripts/ci/image_audit.sh` now fails a
   built image that has one, and `test_container_config_audit_200e.py` checks the Dockerfile).
2. Remove `pip` from the runtime image: **done in Phase 1A** (same two checks).
3. **D5: owner-accepted for the restricted M1 deployment only (2026-10-21).** Accepted: the 2 CRITICAL and
   61 HIGH OS findings of the hardened image, none fixable, with the exposure stated in each row above. Not
   accepted: the 111 MEDIUM OS findings, which remain unreviewed one by one; any use beyond M1. Read-only root
   and `no-new-privileges` remain unverified on Render. Conditions: re-check Trivy before the beta invitation
   link is shared, and rebuild when upstream fixes are published so that the Trivy gate reports them.

No Python dependency, application code or base-image tag was changed.

## 6. Manual test procedures

### 6.1 Where to test

**Local, isolated stack (no keys, made-up data, a temporary store deleted on exit).** From the repository
root, in two terminals:
```bash
# 1. the offline backend (127.0.0.1:8100)
.venv/bin/python backend/scripts/e2e_backend.py
# 2. the production frontend build, proxying /api to it (127.0.0.1:3100)
cd frontend && BACKEND_ORIGIN=http://127.0.0.1:8100 npm run build \
  && API_BASE_URL=/api NODE_ENV=production npx next start -p 3100 -H 127.0.0.1
```
Open `http://127.0.0.1:3100`. For a phone on the same network only, start the frontend with `-H 0.0.0.0`
and open `http://<your machine's LAN address>:3100`; the backend stays on 127.0.0.1. Stop both afterwards.
Destinations containing "Slowville" make generation take 6 s (progress state); every other destination
returns the fixed made-up places. Real map tiles load from OpenStreetMap in a normal browser.

**Production-only checks** (cold start, real providers) need the deployed service and spend provider
credits; run them only after D7, with a disposable account deleted afterwards (`docs/25` section 3 step 9).

### 6.2 Checks

For each: browser and version, device, date, pass / fail, and a screenshot of any failure (no personal data).

| Area | Steps | Pass when |
| --- | --- | --- |
| Desktop usability (Chrome, Safari, Firefox current) | Sign up; create a trip; wait for the itinerary; read every day; open a stop's map links; reload; open "My trips"; delete the trip; delete the account | Every step completes; nothing overlaps or is cut off; warnings and the beta notice are visible |
| Mobile (one iPhone with Safari, one Android with Chrome) | The same journey at default and largest text size, portrait and landscape | No sideways scrolling; buttons reachable; maps fit; text readable |
| VoiceOver (macOS Safari: ⌘F5; iOS: Settings → Accessibility → VoiceOver) | Navigate by headings (VO-⌘H) and landmarks; complete sign-up with VoiceOver only; create a trip and listen for the progress and the result; read a day with a material warning; delete the trip | Every control is announced with its name; progress and errors are spoken without moving focus away; the warning is reached before or on the day it concerns |
| Keyboard only | The full journey with Tab / Shift-Tab / Enter / Space / Escape, including the map's zoom buttons | Focus always visible; nothing traps focus; order follows the page |
| Map rendering | Open an itinerary; zoom and pan each day map | Tiles load; numbered markers match the stop list; attribution visible |
| Tile failure | Chrome / Firefox DevTools → Network → block requests matching `tile.openstreetmap.org`; reload | The stop list and map links still give every place; no error message from the page |
| Login and session | Wrong password, unknown email, correct login; reload; second tab; log out | One generic error for both wrong cases; session survives reload and a new tab; after log out the account is not shown |
| Saved trips | Two trips; switch between them; reload on each | Each opens its own itinerary without regenerating |
| Account deletion | Wrong password, then right password with confirmation | Wrong password deletes nothing; right one signs out and the account cannot sign in again |
| Slow network | DevTools → Network → throttling "Slow 4G"; create a trip (use a "Slowville" destination for a long wait) | The progress state is shown and announced; the result arrives; no duplicate trip on double-click |
| Failure states | Stop the backend terminal while the page is open; try to load trips or generate | A plain-language error; nothing claims success; recovers after the backend restarts |
| Contrast by eye | Look at every warning, banner and status in light conditions on a phone | Text is readable |

Record the results in a new table in `docs/28` section 3, replacing a checkbox only with its evidence.

## 7. Evidence log (fill in)

| Item | Date | Who | Result | Evidence (no secrets) |
| --- | --- | --- | --- | --- |
| A0 production commit / migration head | 2026-10-21 | Owner | Backend (Render) live commit `f55401c`; Auto-Deploy Off; Blueprint Auto Sync No; Neon production migration `d41a7e2c9b53`. Vercel project-level Ignored Build Step: "Don't build anything". **The Vercel production commit is not recorded yet** | Owner's dashboard reading; not independently verifiable from the repository |
| A1 Neon retention value | | | | |
| A1 restore drill into a disposable Neon branch | 2026-10-21 (decision) | Owner | **Not done; deferred for the M1 controlled deployment only (D10).** Still required before the beta link is shared and before unrestricted public registration. B4 stays OPEN | D10 in section 3 |
| Recovery evidence behind D10: encrypted S3 backup restored locally | Before 2026-10-21 (exact date not recorded here) | Owner | **Passed.** An earlier automated, encrypted S3 backup was decrypted and restored into an isolated local PostgreSQL (`docs/30` section 5.1) | Owner's report; not independently verifiable from the repository |
| Recovery evidence behind D10: Neon point-in-time recovery branch inspection | Before 2026-10-21 (exact date not recorded here) | Owner | **Passed.** A Neon point-in-time recovery branch was created and inspected successfully. This is not the A1 drill: no dump was restored into Neon. The Neon history-retention value is still not recorded (A1 retention row) | Owner's report |
| Recovery evidence behind D10: fresh backup and monitor | 2026-10-21 | Owner | **Passed.** A fresh encrypted S3 backup completed; the production monitor reported four valid backups, the newest 0.1 h old | Owner's report; the monitor run is in GitHub Actions |
| M1 pre-migration 1: fresh export, `verify --manifest` against production (exit 0, revision `d41a7e2c9b53`, all tables match), dump SHA-256 equals the manifest | 2026-10-22 00:19 UTC (export) | Owner, on their Mac (PostgreSQL 16.15 client tools, Python 3.11.14) | **Passed.** At a quiet moment (read-only query: 0 running, 0 queued, 0 with a live lease). Export `travelobligator-20261022T001901Z.dump` with manifest `travelobligator-20261022T001901Z.manifest.json`, both mode 0600, in a private (0700) directory outside the repository. `verify --manifest` against production (read-only): **VERIFICATION PASSED, exit code 0**, revision `d41a7e2c9b53`. The dump's SHA-256 equals the manifest's `dump_sha256` | Owner's terminal output (counts and revision only); no connection string, email address or itinerary text recorded. The files themselves are personal data and are not in the repository |
| M1 pre-migration 2: temporary Neon restore-point branch created (name, time); later deleted (time) | 2026-10-21 (created) | Owner | **Created:** branch `pre-m1-20261021` from the production branch, with data and schema; its Alembic revision verified as `d41a7e2c9b53`. **This is a restore point, not the A1 restore drill:** no dump was restored into Neon. **Not yet deleted**: delete it once the release is accepted (section 9.5 step 2) and record the time here. It holds a copy of personal data while it exists | Owner's Neon console reading |
| M1 preconditions: PR #6 CI and deployment holds | 2026-10-21 | Owner | PR #6: all six CI checks green, no merge conflicts, **not merged**. Render Auto-Deploy Off; Blueprint Auto Sync No. Vercel project-level Ignored Build Step "Don't build anything" (build hold of section 9.4; whether the empty Production Override can bypass it is confirmed only by the first deployment after the merge showing as Canceled) | Owner's reading of GitHub, Render and Vercel |
| M1 3–5: migration `d41a7e2c9b53` → `a94c0e7d2b61`; backup-role grants; backend deployed immediately (`Application started.` with the new head) | | | | |
| M1 6: post-migration backup run by hand succeeded; monitor reports it fresh | | | | |
| A2 backup storage (four checks) | | | | |
| A3 first scheduled uptime run | | | | |
| A3 failure notification received | | | | |
| A3 monitor: manual backup-freshness run OK | | | | |
| A3 monitor: manual backend-health run OK | | | | |
| A3 monitor: alert issue @-mention received, then auto-closed | | | | |
| D8 scheduled backend checks / heartbeat decided (and heartbeat first ping, if adopted) | | | | |
| 8 beta link created, stored and set (no value recorded) | | | | |
| 8 first link-holder sign-up checked | | | | |
| A4 configuration and model names | | | | |
| A6 rollback pre-checks | | | | |
| D1–D10 decisions (D3a–D3c and D10 recorded 2026-10-21) | | | | |
| D5 container findings: restricted M1 only | 2026-10-21 | Owner | **Accepted for restricted M1 only**: "I approve D5 for the restricted M1 deployment only. D1 remains deferred." 2 CRITICAL + 61 HIGH accepted; 111 MEDIUM not reviewed and not accepted; Render read-only root / `no-new-privileges` unverified. Before sharing invitations: fresh Trivy re-check, recorded here | Owner's approval in the session; section 3 D5; `docs/28` section 4 |
| D1 quality exception | 2026-10-21 | Owner | **Deferred; not accepted.** The quality gate stays FAILED. Required before the beta invitation link is shared | Owner's statement in the session |
| Trivy re-check before sharing invitations (D5 condition) | | | | |
| `docs/25` 8.2 locked-out deletion rehearsal on a disposable database (wrong code, right code, dry run, deletion, reused request refused) | 2026-10-21 | Owner, personally, on their Mac | **Passed.** Disposable local PostgreSQL 16 container (127.0.0.1 only), migrated to `a94c0e7d2b61`; two synthetic `.test` accounts (one with 2 trips + 1 failed sign-in to delete, one with 1 trip + 1 failed sign-in to keep). `challenge`: request created, 72 h expiry; unknown address: no request, no code. `verify`: two wrong codes refused and counted, then verified (3 of 5 attempts). `status`: verified. Dry run: all checks passed, nothing deleted. Other account's address: refused. Real `delete` (rehearsal session secret): account, its 2 trips and its failed-sign-in counter removed. Reuse of the request: refused. After: unrelated account unchanged (present, 1 trip, 1 failed sign-in); request `consumed`, outcome `deleted`. Cleanup: container and temporary challenge store removed; the real default store was never created | Owner's terminal output reviewed in the session; no production system, credential or real address used. Habit noted for real use: the code was pasted once into a chat during the rehearsal (harmless for synthetic data; in real use, start again with `challenge --replace`) |
| `docs/25` 8.2 contact mailbox: 2-Step Verification and the two message texts saved | 2026-10-21 | Owner (confirmed in the session) | **Done.** 2-Step Verification enabled for garimaprachi2411@gmail.com; both message texts of `docs/25` section 8.1 (the neutral reply and the code message) saved as Gmail drafts | Owner's confirmation; not independently verifiable from the repository. Still open in 8.2: release deployment, and secure operator access to Neon's direct connection string and Render's `SESSION_SECRET_KEY` |
| Section 6 manual tests | | | | |

## 8. Limited-beta access: the shareable link and the emergency switches

**Status: implemented and tested on branch `v1-itinerary-quality-disclosures`; not merged and not deployed.**
Everything below works only after a commit that contains it is deployed (B1, B2). Settings take effect when
the Render service restarts.

### 8.1 How it works

- `SIGNUP_MODE=invite_link` (already in `render.yaml`): a new account needs the shareable link. Production
  refuses to start with `SIGNUP_MODE=open`, and with a token shorter than 32 characters or equal to
  `SESSION_SECRET_KEY` (the message names the variable, never a value).
- The link is `https://<frontend origin>/#beta=<token>`. The token is in the URL **fragment**, which browsers
  never send to a server: it does not reach the hosting logs or a `Referer` header. The page removes it from
  the address bar on arrival and sends it in a request **body** to `POST /api/auth/registration-access`.
- A correct token gets a short-lived `HttpOnly` invitation cookie (at most 30 minutes, `SameSite=Lax`,
  `Secure` in production) that only allows creating an account from that browser; it holds a keyed
  fingerprint of the token, never the token, and is removed once the account exists. Sign-up without it is
  refused by the backend (403 `REGISTRATION_LINK_REQUIRED`), whatever the frontend does.
- Existing accounts sign in normally in every mode.
- Verified: hermetic tests (`test_beta_registration_access.py`, `frontend/tests/beta-access.test.mjs`) and a
  Chromium run against the local stack: the fragment is gone from the address bar, the token is in no
  request URL, `Referer` or log line, the invitation cookie is `HttpOnly` and does not contain the token.

### 8.2 Create and store the token (once)

1. In your own terminal: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` — 43 random
   characters. Copy it straight into a password manager entry named e.g. "TravelObligator beta token";
   do not paste it into a chat, an issue, a commit or a document.
2. Render → service → **Environment** → `BETA_REGISTRATION_TOKEN` → paste the value → save, and make sure the
   service restarts with it (deploy or restart from the dashboard).
3. Check `SIGNUP_MODE` is `invite_link` and `REGISTRATION_ACCESS_TTL_SECONDS` is `1800` (both from
   `render.yaml`).
- *Verify:* the deploy log shows `Application started.` (a short or missing token stops startup and names
  `BETA_REGISTRATION_TOKEN`). In a private browser window, the sign-up form says an invitation link is needed,
  and signing up without the link is refused.
- *Record:* the date it was set — never the value.

### 8.3 Share the link

**Precondition (B4, D10, D1, D5):** the A1 restore drill into a disposable Neon branch is recorded in
section 7; the M1 post-migration backup test (section 9.5 step 6) has passed; D1 (the quality exception) is
accepted and recorded; and a fresh Trivy result has been re-checked under D5 and recorded. Until then, do not
send the link to anyone.

- Send `https://travelobligator.vercel.app/#beta=<token>` directly to the person (a message to them, not a
  public post). Open it yourself once in a private window: the address bar must show the site without
  `#beta=`, and the sign-up form must say the invitation was accepted.
- It works for everyone who has it, until you replace the token. There is no per-person link and no list of
  who signed up through it.

### 8.4 What forwarding the link means

The link is a **bearer credential**, not proof of identity. Anyone it is forwarded to — or who sees it in a
screenshot, a shared chat, or a browser history synced from the recipient's device before the page removed it
— can create an account while the token is unchanged. What still bounds them:

- the deployment-wide signup cap (`SIGNUP_MAX_PER_WINDOW_GLOBAL`, 30 new accounts per 24 h). It is reserved
  atomically (an advisory lock, then count and insert in one PostgreSQL transaction) before the account is
  created and given back if none results, so a burst of simultaneous sign-ups cannot pass it. The
  per-address signup limit is not active while `CLIENT_IP_TRUSTED_PROXY_HOPS=0`;
- per account: 5 generations per 24 h and 20 saved trips; for the deployment: 25 generations per 24 h and 2
  at a time (decided atomically in PostgreSQL; if the database is unavailable no generation is admitted).

A burst of simultaneous sign-ups from one browser holding a valid invitation cookie can create more than one
account (the cookie is cleared by the response, not reserved): the signup cap is what limits that, and it
holds under concurrency.

**Cost: no hard ceiling is claimed.** Each generation is capped at 100 Geoapify credits and each model stage
at two requests, so 25 generations a day stay below the Geoapify free plan's 3,000 daily credits; but the
model calls per generation depend on how many planner passes and repairs run, and the providers' own quotas
are the real limit. Watch the provider consoles and `ops_report.py` (`signup_limit_pressure`,
`generation_global_limit_pressure`, `provider_credits_pressure`).

### 8.5 Rotate or revoke the link

- **Rotate** (the link leaked, or a new group): generate a new token (8.2 step 1), replace the value in
  Render, restart. The old link and every invitation cookie issued for it stop working at once; accounts
  already created keep working. Share the new link.
- **Revoke without a replacement**: set `SIGNUP_MODE=closed` (or remove `BETA_REGISTRATION_TOKEN`; an
  invitation link without a token is treated as closed). Restart.
- *Verify:* opening the old link in a private window shows "This beta invitation link is not valid".
- *Record:* the date and the reason — never the value.

### 8.6 Emergency switches

**Activation: not immediate.** Every setting is read at startup. A change in Render → **Environment** takes
effect only once the service has restarted or redeployed with it. After saving, confirm in Render →
**Logs** that a new `Application started.` line appeared after your change before relying on it. While the
free instance is asleep, the new value applies when it next wakes.

| To stop | Set in Render → Environment, then restart | What users see |
| --- | --- | --- |
| New accounts | `SIGNUP_MODE=closed` | "New accounts are not being created right now. If you already have an account, log in." Sign-in still works |
| New itineraries (generation and regeneration) | `GENERATION_ENABLED=false` | "Building new itineraries is paused right now. Your trips are saved." Saved trips stay readable; nothing is counted or started |
| Everything | Suspend the Render service (`docs/25` section 4.3) | The site's API does not answer |

Undo by setting `SIGNUP_MODE=invite_link` / `GENERATION_ENABLED=true` and restarting. There is no
administrative API for any of this, by design: the dashboard is the only control.

### 8.7 Cookies across the real domains: verify before rollout

Production is **same-origin**: the browser only talks to the Vercel origin, and Vercel's `/api/*` rewrite
forwards to Render. The invitation cookie is therefore a first-party, host-only cookie of the Vercel origin
— exactly how the session cookie works, which is recorded as verified in production (`docs/22` section 14:
`Secure`, `HttpOnly`, `SameSite=Lax`, host-scoped to the Vercel origin). CORS plays no part for the browser.

**Not demonstrated**, and to check before sharing the link widely:

- that Vercel's rewrite passes through a response carrying **two** `Set-Cookie` headers (sign-up sets the
  session cookie and deletes the invitation cookie). Next's own server did, in a local Chromium run of the
  production build; Vercel's edge proxy was not tested.
- the whole flow over https on the real hostnames.

How: there is no staging backend, and a Vercel **preview** deployment points at whatever `BACKEND_ORIGIN` its
Preview environment holds — if that is the production Render service, a preview test is a production test.
An isolated check needs a second, disposable backend (another free Render service on a disposable Neon
branch) and a preview with `BACKEND_ORIGIN` pointing at it. Otherwise, do it as the first step after the
deployment, with a disposable account deleted afterwards: open the link in a private window; check in the
browser's developer tools that `travelobligator_registration` is set (`HttpOnly`, `Secure`, `SameSite=Lax`)
and is gone after sign-up, and that the session cookie remains; sign out and in again.
- *Record:* date, browser, result for each cookie.

### 8.8 Not covered by this feature (still open)

- Blockers B1 (production runs old code), B2 (not merged), B4–B9 and decisions D1–D8 are unchanged: the Neon
  restore drill and retention value, backup storage, uptime alerting, the manual accessibility and device
  passes, and the container-finding risk acceptance all still need a person.
- No rate limit on wrong tokens at the exchange endpoint (the existing per-address limits need a trusted
  client address, which production does not have while `CLIENT_IP_TRUSTED_PROXY_HOPS=0`). A 43-character
  random token cannot be guessed and the exchange does no database work, but every refusal writes one
  warning log line (`REGISTRATION_LINK_INVALID`), so a flood costs log volume and a little CPU.
- No email verification and no password recovery (`docs/25` section 8): the link controls who may register,
  not who an account belongs to.
- The V1 quality gate remains **NOT MET**.

## 9. Production preflight for the limited beta (read-only summary; the procedure is `docs/25` section 3)

Production today, per the repository: backend `f55401c` (schema `d41a7e2c9b53`), frontend `8bffd39`. The
release is `615ae9a` or a later commit of this branch, merged into `main`. Nothing below has been executed.

### 9.1 Limited-beta variables (Render)

New, all in `render.yaml`; production code before `615ae9a` ignores them, so they can be saved ("Save only")
before the migration: `SIGNUP_MODE=invite_link`; `BETA_REGISTRATION_TOKEN` (secret, dashboard only, at least
32 random characters, not equal to `SESSION_SECRET_KEY`); `REGISTRATION_ACCESS_TTL_SECONDS=1800`;
`GENERATION_ENABLED=true`; `CLIENT_IP_TRUSTED_PROXY_HOPS=0`; and the P0 limits written out in `render.yaml`
(`GENERATION_MAX_JOBS_PER_USER_PER_WINDOW=5`, `GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW=25`,
`GENERATION_MAX_CONCURRENT_JOBS=2`, `GENERATION_LIMIT_WINDOW_SECONDS=86400`, `MAX_TRIPS_PER_USER=20`,
`MAX_TRIP_DAYS=7`, `SIGNUP_MAX_PER_WINDOW_GLOBAL=30`, `LOGIN_MAX_FAILURES_PER_ACCOUNT=10`), plus
`GROQ_MODEL=openai/gpt-oss-20b` written out. Every backend setting is read at startup: a change applies
after a restart ("Save and deploy"). Whether the Render service follows a Blueprint decides whether
`render.yaml` values are applied automatically (`docs/25` section 3 step 2).

### 9.2 What `APP_ENV=production` refuses at startup

Listed in `docs/25` section 3 step 2. The ones new in this release: `SIGNUP_MODE=open` (also the default
when the variable is missing); `invite_link` without a valid token or with the session secret as token;
any P0 limit at 0; `ASYNC_GENERATION_ENABLED` not true; a release model stage off or without a connected
provider and key; failover on with only one provider; a feedback interpreter switched on without a connected
provider and key (D9). **`APP_ENV` must be exactly `production`**: any other value disables every check.
A refused start fails Render's health check; the previous instance keeps serving only until it restarts.

### 9.3 Two new tables, and the backup role

The migration adds `auth_attempts` (`e52b7c1a9f04`, with two indexes on `generation_jobs`) and
`deleted_job_usage` (`a94c0e7d2b61`); both additive, one transaction. The read-only backup role
`travelobligator_backup` cannot read tables created after its grant, so the next daily backup would fail
until the owner runs, right after the migration:
`GRANT SELECT ON auth_attempts, deleted_job_usage TO travelobligator_backup;` (`docs/25` sections 2.6, 3).

### 9.4 Hold the Vercel production deployment

Vercel builds production from `main` on every push that changes `frontend/`; merging the release would
publish the new frontend before the backend exists. Set the Ignored Build Step to "Don't build anything"
before the merge, deploy the frontend deliberately afterwards (Redeploy with the project's ignore step
unticked), then restore the previous command.

### 9.5 Order

Prepare variables → hold Vercel → quiet moment and fresh backup → merge → migrate → grant → **deploy the
backend immediately** → check `/health`, `/ready`, the startup line → deploy the frontend → smoke test with a
disposable account through the invitation link → run the backup workflow by hand → cold start → record.

**M1 pre-migration conditions (D10).** The Neon-branch restore drill (A1) is deferred for M1 only, so these
steps are required, in this order, and each is recorded in section 7 (no secret, connection string, email
address or itinerary text):

1. **Fresh export, manifest verified** (`docs/25` sections 2.2 and 2.4), at a quiet moment
   (`ops_report.py`: no queued or running job). Take the backup with `db_backup_restore.py backup`. Then,
   straight away, run `db_backup_restore.py verify --manifest <the new manifest>` read-only against
   production's **direct** connection string (as `VERIFY_DATABASE_URL`). Pass when `verify` exits 0, reports
   revision `d41a7e2c9b53`, and every table's row count and fingerprint match. Then unset the variable. A
   mismatch means something was written in between: take a new export and verify again. Also check that
   `shasum -a 256` of the `.dump` equals `dump_sha256` in the manifest. Keep both files on encrypted storage
   (B6/D2).
2. **Temporary Neon restore-point branch.** Neon → **Branches** → **New branch** from the production branch,
   named e.g. `pre-m1-<YYYYMMDD>`, created **after** step 1 and **before** the migration. It is a point to
   go back to, not a drill. Record its name and creation time. Delete it once the release is accepted
   (`docs/25` section 3 step 11) and record the deletion. It holds a copy of personal data while it exists.
3. **Merge, then migrate** (`docs/25` section 3 step 5): `alembic current` shows `d41a7e2c9b53` before and
   `a94c0e7d2b61` after.
4. **Backup-role grants**, immediately, as the owner of the tables:
   `GRANT SELECT ON auth_attempts, deleted_job_usage TO travelobligator_backup;`
5. **Deploy the backend immediately.** Render → Manual Deploy of the merged commit, with nothing else between
   the migration and this step except the grant. The old backend cannot start on the migrated schema. Check
   for one `Application started.` line with `migration_head=a94c0e7d2b61`.
6. **Post-migration backup test.** After the frontend deploy and the smoke test, run **Actions → Neon backup**
   by hand. Pass when it succeeds, which also proves the grant of step 4, and the next production-monitor run
   reports the new backup as fresh. A failure here is a blocker to sharing the link.

**Before the beta invitation link is shared** (section 8.3), and again before unrestricted public
registration: the full A1 restore drill into a disposable Neon branch, recorded in `docs/25` section 2.5 and
in section 7. Using the link yourself in a private window for the smoke test is not sharing it.

### 9.6 No straightforward rollback to the old backend after the migration

The backend starts only on its exact schema head, so `f55401c` will not start on the migrated database (and
will not survive a sleep once the migration has run). Going back means the manual downgrade of `docs/25`
section 4.3, which drops the quota tables and reinstates a backend without cost limits. Commits that do run on
the new schema and predate `615ae9a` reopen registration and have no emergency switches. **Plan to fix
forward**, with `SIGNUP_MODE=closed` / `GENERATION_ENABLED=false` as the first response (section 8.6) and
suspending the service as the last. A frontend-only rollback to `8bffd39` is possible (sign-in works; new
accounts cannot be created through it).

### 9.7 Real-domain cookie check

Sign-up returns two `Set-Cookie` headers (the session cookie set, the invitation cookie deleted) through
Vercel's proxy; that has only been seen on Next's own server locally. Check it on the real domain right after
the deployment, in a private window with a disposable account (section 8.7, `docs/25` section 3 step 9).

### 9.8 Privacy notice (for the owner's approval)

`frontend/lib/privacy-notice.ts` now describes, in "Limits you should know about": the daily encrypted
backups in Amazon S3, made by a GitHub job, deleted automatically **about seven days** after each is made,
not edited and not removed early by a deletion, and occasionally restored into a temporary database for a
drill; separately, manual exports with **no** retention period; and, in "What is stored", how invitation
links are handled. "About seven days" is true only if bucket versioning is off or noncurrent versions expire
(B6). It also describes the locked-out deletion request (D3): email `garimaprachi2411@gmail.com` with the
account's address; the same reply in every case; a one-time code sent to that address, valid for at most 72
hours, a few wrong codes cancel the request; a message merely from the address is not proof; deletion as with
"Delete account"; backup copies remain about seven days and a manual export is not edited; Gmail processes the
messages, and only the date and outcome are kept. That paragraph becomes true only once `docs/25` section 8.2
is done, so it must not be deployed before then (B7).

### 9.9 The limited-beta quality exception (owner approval required)

The historical V1 itinerary-quality gate is **NOT MET** (median overall 2.0 against a target of 4; eight plans
rated "poor"; `docs/27` section 8). This release does not claim otherwise, and the frozen holdout is not
re-run or changed. A shareable-link beta can go ahead only with the owner's explicit, recorded exception
(D1), naming those results, and with the beta copy that tells travellers to check every plan. Any wider
release needs a new acceptance evaluation first (D6).
