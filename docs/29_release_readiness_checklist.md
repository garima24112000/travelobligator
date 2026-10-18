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
2. **This branch is not on `main`.** `origin/main` is `8bffd39`, 18 commits behind `65801a7`. The uptime
   workflow and the three operator scripts (`db_backup_restore.py`, `ops_report.py`, `public_check.py`) are
   not on `main`. Run the scripts from a checkout of this branch.
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

Not done, and not to be read as done: anything against the deployed Neon, Render or Vercel services in
this list; manual device, browser and screen-reader testing; a new acceptance evaluation.

## 2. Release blockers

| # | Blocker | Resolved by |
| --- | --- | --- |
| B1 | Production (per the repository) runs pre-P0 code with no generation or sign-up limits on a public URL | A0 below, then a deployment of a reviewed commit (`docs/25` section 3) — or, until then, restricting public generation (`docs/25` section 4.3 lists three ways) |
| B2 | This branch is not merged; `docs/25` section 3 step 1 requires the deployed commit to be green on `main` | Owner merges after review; CI re-runs on `main` |
| B3 | **Restricted registration exists in code, not yet in production.** The shareable beta link (section 8) makes new accounts possible only for holders of the link, and production refuses open registration; it takes effect only once a commit containing it is deployed (B1, B2). It restricts registration to *link holders*, not to named people (section 8.4) | Deployment, then section 8 |
| B4 | No Neon restore drill | A1 |
| B5 | Uptime checks do not run (not on `main`) and failure notifications are unverified | B2, then A3 |
| B6 | Backup storage, access and deletion unverified | A2 |
| B7 | No verified deletion path for someone who cannot sign in | DECISION D3 |
| B8 | Unfixed container findings not risk-accepted | DECISION D5 (section 5) |
| B9 | Quality gate NOT MET | DECISION D1 (beta exception), and a new evaluation (D6) before any wider release |

## 3. Required decisions (owner)

| # | Decision | Options the repository gives | Record it in |
| --- | --- | --- | --- |
| D1 | Explicit exception for a limited beta despite the failed quality result | Accept, naming median 2.0 and eight plans "poor" (`docs/27` section 8); or do not release | `docs/25` section 7 |
| D2 | Backup retention | The recommendation is 30 days on encrypted, access-restricted storage; it becomes policy only after A2's four checks | `docs/25` sections 2.2 and 7; the privacy notice in the same commit |
| D3 | Deletion for someone who cannot sign in | (1) password recovery with email verification; (2) an owner-approved identity-verification procedure; (3) keep registration limited until 1 or 2 exists (`docs/25` section 8) | `docs/25` section 8 |
| D4 | Whether link-holder registration is restrictive enough (B3) | (a) the shareable link of section 8 (implemented; anyone the link is forwarded to can sign up, bounded by the signup cap); (b) in addition, access control for the whole site at the frontend host — check in Vercel whether Deployment Protection can cover **Production** on the current plan (not verified here); (c) do not open the beta | `docs/25` section 7 |
| D5 | Risk acceptance per container finding | Section 5 | `docs/28` section 4 |
| D6 | New acceptance evaluation: scenarios and budget | `docs/28` section 5 (≤ 1,000 Geoapify credits and ≤ 80 model requests for ten scenarios) | A new document next to `docs/27` |
| D7 | Whether to merge and deploy | — | `docs/22` section 14, `docs/23` |

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

### A3. Uptime monitoring and notification delivery (`docs/25` section 5.2)

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

## 5. Security findings (Trivy on the backend image, run 39082560024)

**Fixable: 0.** **Unfixed:** 174 findings in Debian 12 base packages: 2 CRITICAL, 61 HIGH, 111 MEDIUM. Debian
status of the 63 HIGH and CRITICAL ones: 55 `affected`, 7 `fix_deferred`, 1 `will_not_fix`; none has a fixed
version. Python packages: 10 MEDIUM. **Exploitability against this image was not tested for any finding.**

| Finding | What the advisory title says | Verified mitigation in this application | Status |
| --- | --- | --- | --- |
| `zlib1g` CVE-2023-45853 (CRITICAL, Debian `will_not_fix`) | overflow in `zipOpenNewFileInZip4_64` (the MiniZip add-on) | The application imports no zip, gzip or tar module and runs no subprocess (checked in `backend/app`) | **Acceptable with this evidence**, pending the owner's acceptance (D5) |
| `libssl3` / `openssl` CVE-2026-84782 (HIGH) | information disclosure via **DTLS** handshake retransmission | Python's `ssl` module, used by every client of this application, has no DTLS support (checked: no DTLS name in `ssl`) | **Acceptable with this evidence**, pending D5 |
| `libsqlite3-0` CVE-2025-7458 (CRITICAL), CVE-2026-11822, CVE-2026-11824 (HIGH) | integer overflow; code execution via crafted FTS5 data; heap overflow | SQLite is opened only by the provider-cache store, and production refuses that backend at startup (`test_production_rejects_the_sqlite_provider_cache`). The library is still loaded | **Needs acceptance (D5)**; residual risk is any other code path that opens SQLite |
| util-linux family (48 HIGH: `util-linux`, `mount`, `libblkid1`, `libmount1`, `libsmartcols1`, `libuuid1`, `bsdutils`, `util-linux-extra`) | mount / nsenter / partition-parsing flaws | The application runs no subprocess; the container runs as non-root with a read-only root. These tools are reachable only by someone already executing code in the container | **Needs acceptance (D5)** as post-compromise local risk |
| `libncursesw6`, `libtinfo6`, `ncurses-*` CVE-2025-69720 (HIGH) | buffer overflow, possible code execution | No `curses` or `readline` import in the application | **Needs acceptance (D5)**; low evidence of reach |
| `gzip` CVE-2026-41992, `perl-base` CVE-2026-9538, `libacl1` CVE-2026-54369, `libsystemd0` / `libudev1` CVE-2026-16742 (HIGH) | LZH decompression; Archive::Tar DoS; acl symlink escalation; systemd-homed escalation | None of these tools or services is run by the application | **Needs acceptance (D5)** |
| 111 MEDIUM OS findings, 10 MEDIUM Python findings | not reviewed one by one | — | **Cannot responsibly be accepted without review**: listed in the CI log of Containers / Build & Smoke |

No dependency or image was changed. A rebuild picks up Debian fixes when they ship; the Trivy gate then
reports them as fixable.

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
| A0 production commit / migration head | | | | |
| A1 Neon retention value | | | | |
| A1 restore drill | | | | |
| A2 backup storage (four checks) | | | | |
| A3 first scheduled uptime run | | | | |
| A3 failure notification received | | | | |
| 8 beta link created, stored and set (no value recorded) | | | | |
| 8 first link-holder sign-up checked | | | | |
| A4 configuration and model names | | | | |
| A6 rollback pre-checks | | | | |
| D1–D7 decisions | | | | |
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

- Blockers B1 (production runs old code), B2 (not merged), B4–B9 and decisions D1–D7 are unchanged: the Neon
  restore drill and retention value, backup storage, uptime alerting, the manual accessibility and device
  passes, and the container-finding risk acceptance all still need a person.
- No rate limit on wrong tokens at the exchange endpoint (the existing per-address limits need a trusted
  client address, which production does not have while `CLIENT_IP_TRUSTED_PROXY_HOPS=0`). A 43-character
  random token cannot be guessed and the exchange does no database work, but every refusal writes one
  warning log line (`REGISTRATION_LINK_INVALID`), so a flood costs log volume and a little CPU.
- No email verification and no password recovery (`docs/25` section 8): the link controls who may register,
  not who an account belongs to.
- The V1 quality gate remains **NOT MET**.
