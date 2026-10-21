# Operations runbook: backup and restore, deployment, rollback, monitoring

Written in P1-D. This is the procedure document for running the deployed system
(`docs/22_free_tier_deployment.md` is the deployment *contract*; `docs/23_v1_release_reference.md` is the
*state* of the release). Everything here is done by a person, by hand. Nothing in the repository deploys,
migrates, backs up or restores automatically, and nothing here needs a paid service.

**How far each part was verified** is stated in every section, with these words:

| Word | Meaning |
| --- | --- |
| **Verified locally** | Executed on a disposable PostgreSQL 17 on a developer machine |
| **Verified in CI** | Executed by a job of `.github/workflows/ci.yml` on PostgreSQL 16 |
| **Documented, not executed** | Written from the code and the tools' behaviour; nobody has run it against production |
| **Needs the dashboard** | Can only be read or done in the Neon, Render, Vercel or provider console |

No command in this document was run against the production database, and none prints a secret.

## 1. Three facts that shape every procedure

1. **The backend starts only against a database at exactly its own Alembic head**
   (`core/persistence.check_postgres_ready`). A newer or an older schema both stop startup with a fixed
   message, and `/ready` of a backend that is already running turns `not_ready` (503). There is no
   "compatible range". So a backend and its schema always move together, and "roll back the code" across a
   migration means "roll back the schema too".
2. **Migrations are a manual step and are transactional.** `backend/scripts/run_migrations.py` runs
   `alembic upgrade head` and nothing else; the web process never migrates. All pending migrations run in
   one PostgreSQL transaction (`backend/alembic/env.py`), so a failure leaves the database at the revision it
   had: there is no half-applied state to repair.
3. **A stopped backend loses running generations, not data.** A generation in flight when the process stops
   is closed as `JOB_INTERRUPTED` (at once on a graceful stop, at the next start after a hard one); the trip
   stays consistent and the traveller starts again. Every deploy, restart and sleep does this.

Revisions at the time of writing:

| | Revision |
| --- | --- |
| Production (commit `f55401c`) | `d41a7e2c9b53` |
| Pending, in order | `e52b7c1a9f04` (indexes on `generation_jobs`, table `auth_attempts`), then `a94c0e7d2b61` (table `deleted_job_usage`) |
| Head of this branch | `a94c0e7d2b61` |

Both pending migrations only **add** tables and indexes. No existing row, column or constraint changes, and
their downgrades drop only what they added. A test (`test_operations_p1d.py`) fails when a later migration is
not purely additive, so this paragraph cannot silently become false.

## 2. Backup and restore

### 2.1 Three different things are called "backup"

| | Neon-managed recovery | The automated daily backup (section 2.6) | An exported backup (sections 2.2–2.5) |
| --- | --- | --- | --- |
| What it is | Neon keeps a history of the database and can restore a branch to an earlier point in time | A `pg_dump` made by a GitHub Actions workflow on `main` every day, encrypted, stored in Amazon S3 | A `pg_dump` file plus manifest you make and hold yourself |
| Where it lives | Inside the same Neon project | S3 bucket `travelobligator-db-backups-10192026`, prefix `neon/` (AWS `us-east-1`) | Wherever you put it, outside Neon |
| Survives losing the Neon project or account | No | Yes | Yes |
| How far back | Set by the project's plan and history setting. **Needs the dashboard:** read it in the Neon console. This project has not verified its value and states none. | 7 days (S3 lifecycle expiry) | As far back as the files you kept |
| Restores into | A Neon branch | A throwaway local database (drill) or a new Neon branch | Any empty PostgreSQL database |
| Effort | A few clicks; nothing to prepare | None day to day; a monthly restore drill | A command before each deployment; a file to keep safe |

Use all three. Neon's history is the fast answer to "undo the last hour". The automated backup is the
standing answer to "the project is gone". The manifest-checked export before a deployment is the one whose
restore this tool verifies table by table.

**Status:** the automated daily backup runs in production since 2026-10-19 (section 2.6). Neon's history and
the manifest-checked export have not been used against production yet; the first export is taken in the next
deployment (section 3, step 4).

### 2.2 Take a backup

Tool: `backend/scripts/db_backup_restore.py`. It needs `pg_dump` and `pg_restore` **at least as new as the
server's major version** (PostgreSQL refuses to dump a newer server with an older client; check with
`pg_dump --version`, and the server's version is in the Neon console).

```bash
# In a fresh shell. The URL is typed blind and never echoed, logged or written to shell history.
read -rs BACKUP_SOURCE_DATABASE_URL && export BACKUP_SOURCE_DATABASE_URL   # paste Neon's DIRECT connection string
python backend/scripts/db_backup_restore.py backup --out ~/travelobligator-backups
unset BACKUP_SOURCE_DATABASE_URL
```

It writes two files, mode `0600`, in a `0700` directory, and refuses a directory inside the repository:

- `travelobligator-<UTC timestamp>.dump`: `pg_dump` custom format, schema `public`, without owners or
  privileges, so it restores under any role.
- `travelobligator-<UTC timestamp>.manifest.json`: the dump's SHA-256, the Alembic revision, and for every
  table a row count and a content fingerprint. It contains no data.

Take it **when no generation is running** (the report of section 5.3 shows queued and running jobs), so the
manifest and the dump describe the same instant.

**The dump is a full copy of personal data**: email addresses, password hashes, trip text. Keep it on an
encrypted disk, never in the repository, a chat or an issue, and delete it when it is no longer needed.

**The tool does not encrypt the dump.** Encryption is a property of where you keep it.

This subsection is about the **manual export**. The automated daily backup has its own, configured expiry
(7 days; section 2.6) and is described separately in the privacy notice.

**Retention: a recommendation, not yet an operating policy.** The owner's decision is to keep exported
backups for **30 days**, on encrypted storage with restricted access, and to delete them after that. That
recommendation becomes a policy the project may state only once all four of these have been checked by a
person, and until then nobody should describe it as in force:

| To verify | Status |
| --- | --- |
| Where the files are kept, and that the storage is encrypted at rest | **Not verified** |
| Who can read that location (ideally one person, no shared or synced folder) | **Not verified** |
| How a file older than 30 days gets deleted, and that the deletion is actually carried out | **Not verified** (there is no automation; it is a calendar task until someone builds one) |
| That a deleted file is gone from sync histories, trash folders and device backups too | **Not verified** |

A backup holds the data of accounts that are deleted later; it is not purged when an account is deleted.
The privacy notice (`frontend/lib/privacy-notice.ts`) describes the manual export in words that are true
both before and after the first export exists: the operator "may export a copy of the database" before a
deployment, such an export "may be kept temporarily", it "is not edited when you delete a trip or your
account", and "this project has not yet set a retention period for these exports". For the exports it names
no number of days on purpose. When the four checks above are done and a period is really in force, the
notice changes in the same commit that records it here; until then it must not state one for exports. The
automated daily backups (section 2.6), hosting-provider backups and logs, and what was already sent to
external services are separate points of the notice and are not affected by anything the operator does with
an export.

### 2.3 Restore into a new, isolated database

A restore never goes into production.

**What the tool enforces by itself**, whatever URL it is given and however that URL is spelled:

- It never writes into a database that holds **any** object, in any schema. A database in use always holds
  some, so it cannot be restored into, under any name or alias.
- It never drops, cleans or overwrites, and the restore is one transaction: a failed or interrupted restore
  leaves the target exactly as empty as it was.
- It needs the dump's manifest, refuses a dump that does not match its checksum, verifies the restored copy
  against the manifest before reporting success, and reports a copy that differs as **REJECTED**.
- It refuses the two databases it is told about, `BACKUP_SOURCE_DATABASE_URL` and `DATABASE_URL`: first by
  comparing the URLs as text (letter case, default port, this machine's names and Neon's pooled host name
  are treated as the same place), then by asking both servers who they are, which catches another name for
  the same database. If one of them is set and cannot be reached to be asked, it refuses.
- It refuses a URL with a connection option it would not pass on to `pg_restore` (a service file, a second
  host, a socket directory), because such a URL could lead the two programs to different servers.

**What it cannot do: know which database is production.** Nothing in a connection string says so, and an
empty database inside the production project is, to the tool, simply an empty database. So the isolation of
the target is the operator's step, done before the command and confirmed in it:

1. Create the target **for this restore**: a new local database, or a new Neon **branch** (or a separate
   Neon project) made for the purpose, and in it a **new, empty database**. Never the production branch.
2. Copy the target's connection string from the place you just created it, not from memory, history or a
   password-manager entry whose name you have to trust.
3. Leave `BACKUP_SOURCE_DATABASE_URL` and `DATABASE_URL` set only if they are the real source and
   application databases (the tool then also refuses them by identity); otherwise unset both.
4. Name the target database in the command. The tool compares it with what the server reports.

```bash
read -rs RESTORE_TARGET_DATABASE_URL && export RESTORE_TARGET_DATABASE_URL
python backend/scripts/db_backup_restore.py restore \
    --dump ~/travelobligator-backups/travelobligator-<stamp>.dump \
    --confirm-empty-target <the new database's name>
unset RESTORE_TARGET_DATABASE_URL
```

Afterwards, **drop the disposable target** (delete the Neon branch or the local database): it is a full copy
of personal data.

### 2.4 Validate what was restored

```bash
export VERIFY_DATABASE_URL="$RESTORE_TARGET_DATABASE_URL"
python backend/scripts/db_backup_restore.py verify --manifest ~/travelobligator-backups/travelobligator-<stamp>.manifest.json
```

`verify` prints counts and revision ids only, and exits non-zero on any problem:

| Check | How |
| --- | --- |
| Migration revision | Equal to the manifest's; and it says whether that is this code's head |
| Table structure | Tables, columns, NULL rules and indexes against the application's models (when at this code's head) |
| Completeness | Every table has the manifest's row count **and** content fingerprint |
| Trip ownership | No trip, job, branch or revision points at a missing owner or trip; a job belongs to its trip's owner; how many trips have an owner |
| An existing itinerary | Every stored planning state still loads as the application's `PlanningState` model, and how many hold scheduled stops |

A backup taken **before** a migration restores at the old revision. That is correct: `verify --manifest`
passes, and `verify` without a manifest reports that the database is not at this code's head. Run
`alembic upgrade head` on the restored copy (with `DATABASE_URL` pointing at the **copy**) to bring it to the
code you intend to run, then `verify` again.

To look at a restored itinerary through the application itself, start a local backend against the copy
(`PERSISTENCE_BACKEND=postgres`, `DATABASE_URL=<the copy>`) and sign in with the account's own password:
password hashes are part of the backup, so no session secret from production is needed.

### 2.5 Restore drill record

| Where | What ran | Result |
| --- | --- | --- |
| **Verified locally** (PostgreSQL 17.5, `pg_dump` 17; synthetic test data only: 2,707 accounts, 3,520 trips, 1,011 planning states, 31 MB) | backup → restore into a new empty database → `verify --manifest` | Passed: revision `a94c0e7d2b61`, structure matches, all eight tables equal in count and fingerprint, 1,011 of 1,011 planning states load, 479 with scheduled stops. Backup 2 s, restore 1 s, verify 1 s. |
| **Verified locally** | The same restored copy with one trip deleted | `verify` failed, naming the two tables that differ |
| **Verified locally** | Restore into a non-empty database; restore onto the source; backup into the repository | All three refused, nothing written |
| **Verified locally**; repeated by every run of `Backend / Integration` (`test_backup_restore_drill_p1d_integration.py`) | Two accounts sign up and generate an itinerary through the API; their rows are backed up, restored and verified; each trip still belongs to its account; each restored state equals the original document; the password still verifies; a damaged dump, a second restore and a tampered copy are caught | Passes |
| **Verified locally**; repeated in CI (same file) | Every refusal of section 2.3: the source under the same and under another name, the application's database, a source that cannot be reached, a non-empty target, an object outside `public`, a wrong `--confirm-empty-target`, a dump without or not matching its manifest; a restore cut off part-way; a copy changed before acceptance | All refused or REJECTED; the source and the target unchanged; the interrupted restore left the target empty |
| **Verified locally**; repeated in CI (same file) | A backup at production's revision `d41a7e2c9b53` → restore → `alembic upgrade head` → downgrade to `d41a7e2c9b53` → upgrade again; existing tables compared by fingerprint at every step | Passes: no existing row changes in either direction |
| **Documented, not executed** | Any backup or restore of the production database; a restore into Neon | — |

**Required before public release, and not done: a restore drill into an isolated, disposable Neon branch.**
Take a backup, create a branch for the purpose with a new empty database in it, restore, verify, record the
result and the timings in this table, and delete the branch. Until that row exists, restoring into Neon is
an untested procedure. It is the only way to learn what the local drill cannot show:

- how long a dump and a restore of the real database take over the network;
- whether the `pg_dump` / `pg_restore` on the operator's machine is new enough for Neon's server version;
- whether a new, empty Neon database really reports no objects (the tool ignores objects that belong to an
  installed extension; anything else Neon pre-creates would make it refuse, which is the safe direction);
- whether the role may call `pg_control_system()` (without it the tool still tells servers apart by start
  time and database id);
- whether the Neon database holds anything outside schema `public`, which the dump would not include.

### 2.6 Automated daily backups

Since 2026-10-19 a GitHub Actions workflow on `main` (`.github/workflows/neon-backup.yml`; full design,
restore and drill procedure in `docs/30_automated_neon_backups.md` on `main`) backs the production database
up **once a day at 03:37 UTC**:

- `pg_dump` (custom format, schema `public`) as the read-only role `travelobligator_backup`, over TLS;
- encrypted on the GitHub-hosted runner with GnuPG (AES-256-OCB, an authenticated mode) before upload; the
  plaintext dump exists only in a private temporary directory on that runner until the encrypted copy is
  proven to decrypt to it, and is deleted on every exit path;
- uploaded to the S3 bucket `travelobligator-db-backups-10192026`, prefix `neon/`, in `us-east-1`, through a
  short-lived OIDC session of a role that may only list, read and write `neon/`; public access blocked;
- each object expires **7 days** after it is created (S3 lifecycle rule); S3 removes expired objects
  asynchronously, usually within about a day, so the notice says "about seven days";
- watched hourly by `.github/workflows/production-monitor.yml` (`docs/31_production_monitoring.md` on
  `main`): an issue is opened if the newest valid backup is older than 36 hours or the workflow is off.

The privacy notice describes exactly this ("once a day … deleted automatically about seven days after it is
made"; deletion in the app does not remove backups early). A test keeps the notice and this section in step.

| Fact | Status |
| --- | --- |
| Daily run, encryption, upload and stored-object check | First production backup uploaded and restored by the local drill (reported by the owner, 2026-10-19) |
| 7-day lifecycle expiry on `neon/` | Configured (reported by the owner); **check before release:** bucket versioning is off, or a noncurrent-version expiry rule exists, otherwise expired backups survive as old versions and "about seven days" is not true |
| Who can read `neon/*` | The backup role (GitHub `main` only) and the account's administrators; **check before release:** no other principal or bucket policy grants read access |
| Restore into a Neon branch | Not done (section 2.5) |

**After every migration that adds a table**, grant the backup role read access to it, or the next backup
fails with `permission denied` (and the monitor alerts). For the pending migrations:

```sql
-- as the owner of the tables, after `alembic upgrade head`
GRANT SELECT ON auth_attempts, deleted_job_usage TO travelobligator_backup;
```

## 3. Deployment procedure

Order matters and is forced by section 1: the backend needs its schema, the frontend needs the backend's new
endpoints.

**Before you start**

1. **CI is green on the commit you will deploy.** All six jobs of `ci.yml` (`Backend / Hermetic`,
   `Backend / Integration`, `Frontend / Build`, `Browser / E2E`, `Containers / Build & Smoke`,
   `Security / Scan`) on that exact commit, on `main`.
2. **Configuration, names only. Needs the dashboard.**
   - Render: every variable of `render.yaml` exists. The ones marked `sync: false` are the secrets and the
     CORS origin; list their names with
     `python -c "import yaml;print(*[e['key'] for e in yaml.safe_load(open('render.yaml'))['services'][0]['envVars'] if e.get('sync') is False],sep='\n')"`
     and confirm each has a value in the dashboard without copying any value out. A missing or contradictory
     one stops startup with a message that names the variable.
   - New since production's commit and required now: the P0 limits (`GENERATION_MAX_*`, `MAX_TRIPS_PER_USER`,
     `SIGNUP_MAX_PER_WINDOW_GLOBAL`, `LOGIN_MAX_FAILURES_PER_ACCOUNT`), the model-stage switches and
     `GROQ_MODEL` / `GEMINI_MODEL`. Check that the two model names are still offered by their providers
     (provider consoles); a benchmark that used them does not prove they are still available.
   - **Limited beta, new and required** (all listed in `render.yaml`): `SIGNUP_MODE=invite_link`;
     `BETA_REGISTRATION_TOKEN` (dashboard only, at least 32 random characters, different from
     `SESSION_SECRET_KEY`; keep a copy in your password manager); `REGISTRATION_ACCESS_TTL_SECONDS=1800`;
     `GENERATION_ENABLED=true`; `CLIENT_IP_TRUSTED_PROXY_HOPS=0`. Production code before `615ae9a` ignores
     them, so they can be saved ("Save only") before the migration without restarting anything.
   - **What `APP_ENV=production` enforces at startup** (`core/operational_config.py`; a violation stops
     startup with a message naming the variable, never its value): PostgreSQL only; Redis provider cache (or
     none); `APP_DEBUG=false`; Secure, HttpOnly, non-`none` session cookie; `SESSION_SECRET_KEY` of at least
     32 characters; explicit https CORS origins; Geoapify for geocoding and places with its key; the inventory
     gate on; `ASYNC_GENERATION_ENABLED=true`; none of the six P0 limits at 0 and the per-user generation
     limit not above the global one; `SIGNUP_MODE` not `open`, and `invite_link` only with a valid token; the
     four model stages on with a connected provider and its credential (and both Groq and Gemini while
     failover is on); a feedback interpreter that is switched on needs the same. `OPS_TOKEN`, when set, of at
     least 24 characters. **`APP_ENV` itself must be `production`: any other value switches all of these
     checks off.**
   - Variables that exist only in the dashboard and not in `render.yaml` (for example the feedback
     interpreter, targeted regeneration, `LLM_PROVIDER_*` tuning) must equal the evaluated code defaults or be
     a deliberate, recorded decision. **Find out whether the Render service is managed by a Blueprint**
     (Render → Blueprints): if it is, a Blueprint sync applies every `value:` in `render.yaml` over the
     dashboard; if not, `render.yaml` is documentation and every value is set by hand.
   - Vercel: `BACKEND_ORIGIN` and `API_BASE_URL=/api` on the Production environment. Check what the
     **Preview** environment's `BACKEND_ORIGIN` points at: if it is the production backend, every preview
     deployment is a test against production.
   - **Hold the Vercel production deployment.** Vercel production builds from `main` automatically, and the
     release changes `frontend/`, so merging it would publish the new frontend before the backend is migrated
     and deployed. Before merging, set Vercel's Ignored Build Step to "Don't build anything" for the duration
     (and note the current command to restore it); step 8 deploys the frontend deliberately.
3. **Nothing is generating.** Run the report of section 5.3, or wait for a quiet moment. A deploy interrupts
   running generations (section 1, fact 3).

**Deploy**

4. **Back up** (section 2.2) and keep the two files. Optionally also create a Neon branch as a restore point
   (**needs the dashboard**).
5. **Migrate**, from a checkout of the commit being deployed:

   ```bash
   read -rs DATABASE_URL && export DATABASE_URL      # Neon's DIRECT connection string
   export PERSISTENCE_BACKEND=postgres
   (cd backend && alembic current)                   # expect d41a7e2c9b53 before the next deployment
   python backend/scripts/run_migrations.py          # applies e52b7c1a9f04 then a94c0e7d2b61, in one transaction
   (cd backend && alembic current)                   # expect a94c0e7d2b61 (head)
   unset DATABASE_URL
   ```

   From this moment until step 6 finishes, the **old** backend still serves requests it has already started
   for, but its `/ready` says `not_ready`, and if it restarts (it sleeps after a quiet period) it will not
   come back. **Do step 6 immediately.** There is no way back to the old backend from here without the manual
   downgrade of section 4.3: plan to fix forward.

   Then, as the owner of the tables, give the backup role read access to the two new tables, or the next
   daily backup fails (section 2.6):
   `GRANT SELECT ON auth_attempts, deleted_job_usage TO travelobligator_backup;`
6. **Deploy the backend** on Render (manual deploy of the commit; `autoDeploy` is off).
7. **Check the backend.** The deploy log shows one `Application started.` line with the new
   `migration_head` and no secret. Then:

   ```bash
   python backend/scripts/public_check.py --backend
   # readiness, with the ops token typed blind:
   read -rs OPS_TOKEN && export OPS_TOKEN
   python backend/scripts/public_check.py --backend --ready --backend-origin https://<the Render origin>
   unset OPS_TOKEN
   ```

8. **Deploy the frontend** on Vercel (the same commit): Deployments → the commit → **Redeploy** with "Use
   project's Ignore Build Step" **unticked**, then restore the Ignored Build Step command noted in step 2.
9. **Smoke test, non-destructive first:**
   `python backend/scripts/public_check.py --backend --expect-security-headers`. Then, in a browser, the
   checklist of `docs/22_free_tier_deployment.md` section 11-G with a disposable account, which you delete
   afterwards through "Delete account" (that is itself part of the test). Create that account **through the
   invitation link on the real domain**, in a private window, and check in the developer tools
   (`docs/29_release_readiness_checklist.md` section 8.7): `travelobligator_registration` is set (`HttpOnly`,
   `Secure`, `SameSite=Lax`) after opening the link; after sign-up it is gone and the session cookie is
   present (sign-up returns two `Set-Cookie` headers through Vercel's proxy, which has not been verified
   before); sign out and in again. Then run **Actions → Neon backup** by hand: it must succeed (it proves the
   grant of step 5).
10. **Cold start.** Leave the site untouched until Render has put the backend to sleep (the dashboard shows
    when; allow well over a quarter of an hour without any request, including your own checks), then open it
    and sign in: the first request is slow and then succeeds. This has never been verified after a genuine
    idle sleep and is the last open acceptance item.
11. **Record** the deployed commit, the migration head and the results in `docs/22` section 14 and `docs/23`.
12. **Tag** only after all of the above is accepted: `git tag -a v1.0.0 <commit> -m "..."`, then push the tag.

Status: **documented, not executed.** Steps 5 and the schema checks of step 7 are exercised on throwaway
databases in CI (section 2.5, and `scripts/ci/container_smoke.sh` proves that a backend on an un-migrated
database never reports ready).

## 4. Rollback runbook

Decide first **what** is wrong; roll back the smallest thing. Rolling back never recovers an interrupted
generation; travellers start those again.

### 4.1 Frontend only

The new frontend is wrong, the backend is fine.

- Vercel → Deployments → the previous production deployment → **Promote / Instant Rollback**. No build, no
  data. **Needs the dashboard.**
- An older frontend works with a newer backend (it simply does not use the newer endpoints). The reverse is
  not true: the P1 frontend calls delete endpoints that the pre-P0 backend does not have.

### 4.2 Backend only, no migration between the two commits

- Render → the service → Manual Deploy → the previous commit (or "Rollback" on the previous deploy).
- Check with section 3 step 7. Running generations are interrupted.
- **After the next deployment, "the previous commit" is not production's old one.** Only commits whose
  Alembic head is `a94c0e7d2b61` start on the migrated schema (`3eb11c7` … `615ae9a` and later). Every one of
  them **before `615ae9a` has open registration and neither emergency switch** (`SIGNUP_MODE`,
  `GENERATION_ENABLED` arrived in `615ae9a`), so deploying one of them reopens sign-up to anyone. For a
  misbehaving limited-beta backend, first use the switches on the current commit (`SIGNUP_MODE=closed`,
  `GENERATION_ENABLED=false`, `docs/29` section 8.6), then fix forward; suspend the service if even that is
  not enough.

### 4.3 Backend across a migration (the next deployment is this case)

The previous code will not start on the migrated schema (section 1, fact 1). The schema must go back first,
which is an explicit, manual, data-losing step.

**A production database is never downgraded automatically.** No script, workflow, image or deploy hook in
this repository runs `alembic downgrade`, and none may be added. **Never automate it and never do it as a
first reaction.** The downgrade tests on throwaway databases prove the mechanics; they are not a procedure
to run against production and must not be turned into one.

**Fix forward whenever it is feasible.** A fault in code is repaired by a new commit on top of the current
schema. Going back across these two migrations is for the case where the new backend cannot be made to work
at all, and it has three costs that a fix does not:

1. **It removes the quota accounting.** The downgrade to `d41a7e2c9b53` drops `auth_attempts` and
   `deleted_job_usage` and two indexes on `generation_jobs`: the sign-up and failed-sign-in counters, and the
   record of generations whose trip or account was deleted. After a later re-upgrade those tables start
   empty, so for up to one window (24 hours) the limits under-count: accounts that had used their allowance
   and then deleted trips get it back, and the sign-up limit restarts from zero. No account, trip or
   itinerary is lost.
2. **It puts back a backend with no cost controls.** The pre-P0 code (`f55401c`) has no per-user limit, no
   deployment-wide limit, no cap on simultaneous generations, no limit on trips per account, no bound on
   trip length or list sizes, and no sign-up or sign-in limit. Anyone who signs up can start generations
   until the provider credits and model quota for the day are gone. On free provider plans that means the
   service stops working for everyone; on a paid plan it means spending.
3. **It does not undo deletions.** Trips and accounts deleted through the new deletion feature stay deleted
   (that needs a restore, 4.6).

**So, if reverting across these migrations is unavoidable, public generation must stay disabled or
restricted until the cost controls are back.** The pre-P0 backend has no switch for that, so the restriction
is applied around it — choose at least one, and write down which (**needs the dashboard**; none of these has
been executed):

- Keep the Render service **suspended** and bring it up only for the time needed. This is the default.
- Take the provider keys out of the service's environment (or disable them in the provider consoles) so a
  generation cannot spend credits. Plans then come back blocked, which is honest, and nothing is charged.
- Put the site behind access control at the frontend host for the duration.

and lift it only when a backend **with** the limits is running again.

**Before the rollback, verify — do not assume:**

- A **fresh backup** of the production database exists and its manifest matches (section 2.2; then
  `verify --manifest` against a restored copy if there is any doubt about the backup itself).
- **Migration compatibility**: `alembic current` on production prints the revision you believe it has;
  the commit you will run the downgrade from contains both migrations; the commit you will deploy
  afterwards has exactly `d41a7e2c9b53` as its head (`alembic heads` in a checkout of it).
- No generation is running (section 5.3).

Then:

1. Roll the **frontend** back first (4.1), so no browser calls endpoints that are about to disappear.
2. **Suspend** the Render service (so the new code is not writing to tables that are about to be dropped).
3. Apply the restriction chosen above.
4. From a checkout of the **newer** commit (it is the one that knows how to downgrade), by hand:

   ```bash
   read -rs DATABASE_URL && export DATABASE_URL
   export PERSISTENCE_BACKEND=postgres
   (cd backend && alembic downgrade d41a7e2c9b53 && alembic current)   # expect d41a7e2c9b53
   unset DATABASE_URL
   ```

5. Deploy the previous backend commit on Render. Resume the service only under the restriction of step 3.
   Check as in section 3 step 7.
6. Plan the way forward at once: the corrected commit, its migration back to head, and the removal of the
   restriction.

**Verified locally** on throwaway databases, and repeated by every run of `Backend / Integration`: after the
downgrade every pre-existing table has exactly the rows it had, the newer code refuses the older schema, and
upgrading again works and leaves the two counter tables empty
(`test_a_backup_taken_before_the_pending_migrations_restores_migrates_and_rolls_back`). **Documented, not
executed** on Render and Neon.

### 4.4 A migration that fails

- `run_migrations.py` prints a failure and exits non-zero. Because the migrations share one transaction,
  nothing was applied: `alembic current` still prints the old revision.
- The old backend is unaffected and keeps running. **Do not deploy the new backend.**
- Read the cause from Alembic's output (it never contains the connection string), fix it in a new commit, run
  CI, and start the deployment again from step 1. Do not edit the database by hand to "help" a migration.
- If `alembic current` shows something unexpected (neither the old nor the new revision): stop, do not start
  or deploy anything, and compare with the backup's manifest (`verify --manifest`) before deciding.

### 4.5 Interrupted or stuck generation jobs

- **Interrupted** (`JOB_INTERRUPTED`): expected after any stop. Nothing to do; the traveller sees "was
  interrupted" and retries. Many of them means the service is being restarted under load (section 5.4).
- **Stuck** (status `running`, lease expired, not closed): the next backend start and the next job admission
  both close expired leases. To force it, restart the service once. Never edit job rows by hand, and never
  cancel a healthy job to free a slot.
- A stuck job holds one of the concurrent-generation slots until it is closed; the report of section 5.3
  shows it as `jobs_stuck`.

### 4.6 Restoring from backup

The last resort, for lost or damaged **data** (not for bad code). It discards everything written since the
backup, for every user.

1. Stop: suspend the Render service. Decide with the facts in hand what was lost and since when.
2. Prefer Neon's own point-in-time restore when the damage is recent and the project is intact: restore to a
   **new branch**, verify it (section 2.4), and only then point the service at it. **Needs the dashboard.**
3. Otherwise restore the exported backup into a **new, empty** database (section 2.3) and verify it
   (section 2.4). Never restore over the damaged database: it is the evidence, and the tool refuses anyway.
4. If the backup predates a migration, run `alembic upgrade head` on the restored copy with the code you will
   run.
5. Point the service at the verified copy by changing `DATABASE_URL` on Render (**needs the dashboard**),
   resume, and check as in section 3 step 7.
6. Tell users what was lost. There is no user-facing channel today other than the repository.

Status: **documented, not executed** against Neon or Render. Steps 3 and 4 are verified locally on
throwaway databases and repeated by every run of `Backend / Integration`.

## 5. Monitoring

### 5.1 What the application already exposes

| Signal | Where | Available in production today |
| --- | --- | --- |
| Liveness | `GET /health` (public, no dependency call) | Yes |
| Readiness | `GET /ready`: database reachable **and** schema at the code's head (else 503 `not_ready`); cache down = `degraded` | Yes, with `Authorization: Bearer <OPS_TOKEN>`; 404 without |
| Metrics | `GET /metrics` (Prometheus text: requests, transactions, jobs, provider calls, cache, dependency state) | **No.** `METRICS_ENABLED=false`, and nothing scrapes it. The counters are per process and reset at every sleep, so on the free tier they would say little |
| Structured logs | Render's log view. One JSON line per event, redacted, with a `request_id` | Yes; Render's own retention applies (**needs the dashboard**) |
| Request ids | `metadata.request_id` in every API response; the same id on the request's log lines | Yes |
| Generation lifecycle | Log events `job.created`, `job.claimed`, `job.succeeded`, `job.failed`, `job.interrupted`, `job.heartbeat_failed`, `job.admission_refused`; and the `generation_jobs` table | Yes |
| Provider failures | Log events `provider.failure` (WARNING) and `provider.no_data` (INFO), with provider and operation, never a URL or payload | Yes |
| Credit accounting | Per generation, in the trip's `provider_usage_report` (credits used, calls refused at the per-generation cap). No running daily total | Partly: see 5.3 |
| Schema problems | Log event `persistence.schema_mismatch` at startup; `/ready` `not_ready` | Yes |

There is no error tracker and no alerting service, by decision (free tier, no external account).

### 5.2 Is the site up? (automatic)

`.github/workflows/uptime.yml` runs `backend/scripts/public_check.py` from a GitHub-hosted runner:

- **Every four hours: the frontend page only.** It is served by Vercel alone; the request does not reach the
  backend, so this is not a keepalive and the backend still sleeps.
- **Backend through the frontend: disabled, by decision.** Checking the backend wakes it. The scheduled
  backend check stays off: it would run only if the repository variable `UPTIME_CHECK_BACKEND` were set to
  `true`, and the decision is not to set it. Nothing is to be added that keeps the free-tier backend awake.
  A person can still start the workflow by hand with the `backend` box ticked (one request, on demand).
- The scheduled run sends no token and no secret anywhere: it requests one public page. The manual backend
  check also uses none; it checks, from outside, that `/api/ready` and `/api/metrics` are 404 and that an
  unauthenticated API request is refused and uncacheable. Readiness with `OPS_TOKEN` is never part of the
  workflow: it is a manual step from the operator's own shell, the token goes only to the `--backend-origin`
  the operator names (https only), and a redirect is never followed.
- Every request has a timeout (20 s), a sleeping backend is waited for no longer than two minutes, and the
  job itself is cut off after ten. No response at all (DNS, TLS, refused, timed out) is a failure with its
  own line, not a crash.

**Alerting is not verified.** A failed run is the only alert there is. Whether anyone is told depends on
GitHub's notification settings of the account that owns the workflow, and on that inbox being read.
**Nobody has confirmed that a failure notification arrives**; until a person has seen one (start the workflow
by hand against a deliberately wrong `--frontend`, or check Settings → Notifications → Actions), assume that
a failed run is noticed only when someone looks at the Actions tab.

Limits:

- **It does not run yet.** A scheduled workflow runs from the default branch only; on this branch the file
  does nothing. It starts with the first schedule after the merge into `main`.
- GitHub may delay scheduled runs, and pauses them after 60 days without repository activity.
- Four hours is the detection time, not minutes, and only for the frontend. A backend that cannot start is
  **not** detected automatically; it is found by a visitor, or by running section 5.3 or
  `public_check.py --backend` by hand.

### 5.3 Is it healthy? (by hand, read-only)

```bash
read -rs OPS_REPORT_DATABASE_URL && export OPS_REPORT_DATABASE_URL
python backend/scripts/ops_report.py        # exit 0 nothing to do, 1 warning, 2 critical, 3 could not run
unset OPS_REPORT_DATABASE_URL
```

SELECTs only; it prints counts and revision ids, never an address, an id or itinerary text. It compares
against the limits declared in `render.yaml`.

| Finding | Severity | Means |
| --- | --- | --- |
| `schema_mismatch` | critical | The database is not at this code's head: a backend built from it will not start |
| `jobs_stuck` | critical | Running jobs whose lease expired more than 15 minutes ago and were not closed (4.5) |
| `generation_failures` | warning from 3, or half of at least 4 | Jobs that failed for a reason other than interruption |
| `generation_interrupted` | warning from 3 | The service is being stopped while people generate |
| `generation_global_limit_pressure` / `_reached` | warning | 80 % / 100 % of the deployment's generations for the window are used |
| `signup_limit_pressure` / `_reached` | warning | The same for new accounts |
| `provider_credits_pressure` / `_reached` | warning | Geoapify credits recorded in the last day against the plan's daily credits (`--geoapify-daily-credits`, default 3,000). **A lower bound**: only each trip's last generation is stored. The provider's console is authoritative |
| `provider_credit_cap_hit` | warning | A generation hit `GEOAPIFY_MAX_CREDITS_PER_GENERATION` and had a request refused locally |
| `plans_blocked` | warning at half of at least 4 | Many plans end blocked: a provider is rate-limited or out of credits, or destinations lack inventory |
| `generation_user_limit_reached` | note | Accounts at their own daily limit |

When to run it: before and after every deployment, when the uptime check fails, when someone reports a
problem, and otherwise weekly while the site is public.

It cannot see: a provider's rate limiting or outage (only its effects), the model providers' quotas, or
whether the web service is reachable. For those, the logs:

| Question | Search the Render log for |
| --- | --- |
| Why did a generation fail? | `job.failed`, then the lines with the same `request_id` / job |
| Is a provider failing or out of quota? | `provider.failure` (look at `provider=` and `status=`), `rate_limited`, `deadline_exceeded` |
| Are requests being refused by a limit? | `job.admission_refused` |
| Did the service restart? | `Application started.`, `app.shutdown`, `job.interrupted` |
| Database or schema trouble? | `persistence.unavailable`, `persistence.schema_mismatch`, `readiness.changed` |

Provider consoles (**needs the dashboard**): Geoapify credits used today, Groq and Gemini quota, Neon storage
and compute hours, Upstash commands, Render instance hours, Vercel usage.

### 5.4 Incident checklist

1. **What do users see?** Open the site. Run `python backend/scripts/public_check.py --backend`.
2. **Frontend page fails** → Vercel status and the latest deployment. Roll back the frontend (4.1) if a
   deployment caused it.
3. **Page loads, API fails or hangs**
   - Wait two minutes and retry: a cold start is slow and can fail once.
   - Still failing → Render: is the service running, suspended, or failing to start? Read the last
     `Application started.` or the startup error. A configuration error names its variable.
   - Startup says the schema is not at head → a deploy or migration was left half done: finish it (section 3)
     or roll back (4.3). Do not edit `alembic_version` by hand.
   - `/ready` (with the token) says `not_ready` → the database: Neon console (suspended? storage or compute
     limit reached?). `degraded` → only the cache is down; the site works, more slowly and using more
     provider credits.
4. **Site works, generations fail** → run the report (5.3). Then the log for `provider.failure`. If a
   provider is out of credits or quota, nothing in the application fixes it: wait for the provider's reset,
   and consider lowering `GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW` so travellers get an honest "limit reached"
   instead of blocked plans.
5. **"Limit reached" for everyone** → `generation_global_limit_reached` in the report. That is the cost
   control working. Raise it only after checking the provider consoles.
6. **Suspected abuse** (the report shows sign-ups or one account's generations at their limits) → the limits
   are holding. Lower them in the Render dashboard if needed; the service restarts and interrupts running
   generations.
7. **Data looks wrong or is missing** → stop writing (suspend the service) before anything else, then 4.6.
8. **A secret may be exposed** → rotate it where it is issued (provider console, Neon, Upstash; for
   `SESSION_SECRET_KEY` and `OPS_TOKEN`, generate a new value in Render), update the Render variable, and
   redeploy. Rotating `SESSION_SECRET_KEY` signs every user out and resets the sign-in counters' keys.
9. **Afterwards**: write down what happened, when, what was done and what was lost, in `docs/22` section 14.

## 6. Dependency advisory: `baseline-browser-mapping` (CVE-2026-45819)

Reported as MEDIUM by `Security / Scan` (below its HIGH/CRITICAL gate, so it does not fail the job).

| | |
| --- | --- |
| Advisory | GHSA-w5vr-8v7q-w6rv / CVE-2026-45819, published 2026-08-13: versions 2.x before 2.11.0 call `process.exit()` on invalid or conflicting input instead of throwing, which can end the process (denial of service) |
| Installed when reported | 2.10.24, in `frontend/package-lock.json` |
| Why it is there | `next@16.3.8` depends on it (`^2.9.19`), as does `browserslist` (`^2.10.12`, a development dependency through ESLint) |
| Where it runs | At build time, when Next.js resolves browser targets through its bundled `browserslist`. It is **not** in the traced production server output (`.next/standalone`), so the running frontend does not load it. Its input here is the project's own build configuration, not anything a visitor can send |
| Fixed in | 2.11.0 |
| Remediation, applied in P1-D | The lock file entry was updated within the range Next.js already declares (`npm update baseline-browser-mapping` in `frontend/`): 2.10.24 → 2.11.28. One entry of `package-lock.json` changed; no Next.js upgrade, no `package.json` change. `npm audit --omit=dev` now reports no finding |

### 6.1 Development-only findings of `npm audit`

`npm audit` listed nine more findings that `Security / Scan` does not report, because Trivy scans the
dependencies that ship and these are development dependencies. `npm ls --omit=dev` contains none of them,
and none is in the production image or in `.next/standalone`. They run on a developer's machine and on the
CI runner, on this repository's own files (its ESLint configuration and source), never on anything a visitor
sends. The exposure is therefore: a malicious or malformed file in the repository could make the lint step
slow, crash or (for `@babel/core`) read a local file — a risk to the build tooling, not to the running site.

| Package (installed when reported) | Severity | Advisories | Fix | Done in P1-D |
| --- | --- | --- | --- | --- |
| `browserslist` 4.28.2 | high | GHSA-c83g-rgw3-j3cx (unbounded cache growth), GHSA-73wf-gq98-2v4g (crash / prototype write from an untrusted stats file) | > 4.28.6, non-breaking | Updated to 4.29.3 |
| `js-yaml` 4.1.1 | high | GHSA-h67p-54hq-rp68, GHSA-52cp-r559-cp3m, GHSA-5p4m-2wfm-xmqj, GHSA-2883-xcg3-v3hh (quadratic CPU use on crafted YAML) | ≥ 4.3.2, non-breaking | Updated to 4.3.2 |
| `brace-expansion` 1.1.14 and 5.0.5 | high | GHSA-jxxr-4gwj-5jf2, GHSA-3jxr-9vmj-r5cp, GHSA-mh99-v99m-4gvg, GHSA-rgw5-rvv9-x895, GHSA-q2hr-2g5m-vwhr, GHSA-qhr7-859c-m2p7, GHSA-6j4f-fj2g-mc7p (denial of service on crafted patterns) | ≥ 1.1.21 / ≥ 5.0.12, non-breaking | Updated to 1.1.21 and 5.0.12 |
| `@babel/core` 7.29.0 | low | GHSA-4x5r-pxfx-6jf8 (file read through a `sourceMappingURL` comment) | > 7.29.0, non-breaking | Updated to 7.29.7 (with its `@babel/*` companions) |
| `braces` 3.0.3 | high | GHSA-vfj7-8cjw-p6xm (stack exhaustion on deeply nested patterns) | **None exists**: 3.0.3 is the newest release and is affected | **Unresolved** |
| `micromatch` 4.0.8, `fast-glob` 3.3.1, `@next/eslint-plugin-next` 16.3.8, `eslint-config-next` 16.3.8 | high (inherited) | No advisory of their own: each is listed because it depends on `braces` | The only "fix" npm offers is `eslint-config-next@14.2.35`, a major downgrade two framework versions back | **Unresolved; not taken** |

The updates were made with `npm update <the four packages>` and changed `frontend/package-lock.json` only.
Updating `browserslist` also moved its data packages (`caniuse-lite`, `electron-to-chromium`,
`node-releases`, `update-browserslist-db`); `caniuse-lite` is one that Next.js itself depends on, so the
production build and the whole browser suite were run again on the new lock file. `npm audit fix --force`
was not used and must not be: it would downgrade the lint configuration across major versions.

**Unresolved high-severity build-tool exposure:** `braces` ≤ 3.0.3, reached only through ESLint's Next.js
plugin (`eslint-config-next` → `@next/eslint-plugin-next` → `fast-glob` → `micromatch` → `braces`). It
matches file-name patterns from the project's own lint configuration. There is no fixed release to move to;
revisit when `braces` or `micromatch` publishes one, or when `eslint-config-next` drops the dependency. The
CI security gate (`Security / Scan`: any HIGH or CRITICAL finding with a fix, in what ships) is unchanged.

## 7. Decisions

**Decided by the owner (P1-D review):**

1. The scheduled check of the public frontend page runs every four hours (section 5.2).
2. The scheduled backend check stays **disabled**, and no service is added that keeps the free-tier backend
   awake.
3. Exported backups: **30 days** on encrypted, access-restricted storage is the recommendation. It is not an
   operating policy, and is not to be described as one, until the four checks of section 2.2 are done.
4. A successful restore drill into an isolated, disposable **Neon branch** is required before public release
   (section 2.5). Not done.

**Still open, each needing a person and a dashboard:**

1. **Neon history retention**: read it in the Neon console and record the value in section 2.1. Unknown.
2. **Workflow notifications**: confirm that a failed `Uptime` run actually reaches someone (section 5.2).
   Unverified.
3. **Backup storage**: for manual exports, location, encryption, access and the deletion routine
   (section 2.2) are unverified. For the automated S3 backups, bucket versioning and who else can read
   `neon/*` (section 2.6) are unverified.
4. **Privacy notice**: it describes the automated daily backups with their 7-day expiry, and operator-held
   exports without a period (sections 2.2 and 2.6). The backups' "about seven days" depends on the versioning
   check of item 3. Change the exports' wording only together with a retention period that is really in
   force.
5. **Deletion for someone who cannot sign in** (section 8): decided (D3a contact
   garimaprachi2411@gmail.com, D3b mailbox challenge, D3c the operator command) and implemented; **not yet
   usable** until the prerequisites of section 8.2 are done (deployment, operator access, a rehearsal on a
   disposable database). Whether this suffices for **unrestricted** registration is still open.

**Decided by the owner (release-copy alignment):** the first release is a clearly labelled **limited beta**;
no itinerary is advertised as complete, verified or ready to follow; the holdout's manual rubric
(`docs/24_itinerary_quality_contract.md` part C) is completed before a public launch; password recovery does
not exist and is not claimed.

## 8. An account deletion request from someone who cannot sign in

**Status: decided by the owner (D3a, D3b, D3c, 2026-10-21) and implemented as the operator command
`backend/scripts/locked_out_account_deletion.py` (section 8.1). Usable only once the prerequisites of section
8.2 are done. Never improvise around it.**

What the system has, checked in the code:

- An account can be deleted in exactly one way: `POST /auth/delete-account`, by a signed-in session that
  also supplies the account's password.
- The authentication routes are sign-up, log-in, log-out, delete-account and "who am I", plus the limited
  beta's registration status and invitation-link exchange (`docs/29` section 8), which only decide whether
  a NEW account may be created. There is no password reset, no email verification, no mail-sending code or
  dependency, no recovery code and no second factor. An email address is accepted at sign-up without proof
  that the person controls it; an invitation link controls who may register, not who an account belongs to.
- So, without the password, **nothing the system stores can prove that a requester owns an account.** The
  procedure of section 8.1 adds one proof from outside the system: control of the account's mailbox.

What follows from that:

- **Knowing an account's email address is not proof of ownership.** Nor is knowing its trip destinations,
  dates, or anything else a third party could know, guess or have been told. A request that offers only
  such details must not lead to a deletion: deleting on that basis would let anyone destroy another
  person's account.
- A message sent *from* the email address is not proof either, as far as this project can tell: the
  address was never verified at sign-up, and the project has no mail system of its own in which to check
  where a message really came from.
- The operator is technically able to remove rows from the database. That ability is not a procedure: an
  account is deleted only by the command of section 8.1, after a verified challenge, never by SQL.
- Password recovery still does not exist and is not offered. The registration form's warning ("may be unable
  to delete your account yourself") stays true: the person cannot do it themselves, only through 8.1.

Before **unrestricted** public registration, the owner still has to decide whether this hand-run procedure is
enough or whether password recovery through a verified email address (V1.1) must come first.

### 8.1 Procedure (owner decisions D3a, D3b, D3c)

**D3a: contact.** Requests go to **garimaprachi2411@gmail.com** (the owner's Gmail mailbox), the address the
privacy notice publishes. GitHub issues are never used for this: they are public.

**D3b: ownership check: a mailbox challenge.** A code is sent **to** the account's registered address; only
the code coming back proves control of that mailbox. A message that merely comes *from* the address is never
enough, and neither is anything a third party could know (trip details, dates, screenshots).

**D3c: deletion.** Only `backend/scripts/locked_out_account_deletion.py delete`, which calls the
application's own `data_deletion_service.delete_account` (one transaction, every table including the
failed-sign-in counters, refused while a generation runs, generation usage kept for the window) exactly as
"Delete account" does. Never SQL.

**Steps:**

1. A request arrives at the contact address and names the account's email address (it may come from any
   address).
2. Reply to the requester with the **same text in every case** (it must not reveal whether an account exists):
   > Thank you. If a TravelObligator account uses the address you gave, a one-time code has been sent to that
   > address. To confirm the deletion, send that code to garimaprachi2411@gmail.com within 72 hours. If no
   > code arrives, nothing will be deleted.
3. On your own machine, in a fresh shell, create the challenge (the address is typed without echo):

   ```bash
   read -rs ACCOUNT_DELETION_DATABASE_URL && export ACCOUNT_DELETION_DATABASE_URL   # Neon's DIRECT connection string
   python backend/scripts/locked_out_account_deletion.py challenge
   unset ACCOUNT_DELETION_DATABASE_URL
   ```

   "No account uses that address" → send nothing more; the reply of step 2 already went out. Otherwise the
   command prints a request id, the account's **fingerprint**, the expiry (72 hours) and the **code**.
4. Send a **new** message from the contact mailbox **to the account's address** (not as a reply to the
   requester, unless it is the same address):
   > Someone asked for the TravelObligator account registered to this address to be deleted. If that was
   > you, send this code to garimaprachi2411@gmail.com within 72 hours: CODE. If it was not you, ignore this
   > message; nothing will be deleted.

   The code goes nowhere else: not into an issue, a note, a chat, the repository or a log.
5. When a code comes back (from any address), check it. Every attempt is counted; five wrong ones lock the
   request; an expired, used or replaced request is refused:

   ```bash
   python backend/scripts/locked_out_account_deletion.py verify --request R-...
   ```

6. Delete, first as a dry run (all checks, nothing deleted), then for real. The real run needs that
   deployment's `SESSION_SECRET_KEY` (Render dashboard) so that the account's failed-sign-in counters go too,
   asks for the account's address again (it must be the same account the request was verified for) and for
   `DELETE <fingerprint>` typed exactly:

   ```bash
   read -rs ACCOUNT_DELETION_DATABASE_URL && export ACCOUNT_DELETION_DATABASE_URL
   python backend/scripts/locked_out_account_deletion.py delete --request R-... --confirm-account <fingerprint> --dry-run
   read -rs ACCOUNT_DELETION_SESSION_SECRET_KEY && export ACCOUNT_DELETION_SESSION_SECRET_KEY
   python backend/scripts/locked_out_account_deletion.py delete --request R-... --confirm-account <fingerprint>
   unset ACCOUNT_DELETION_DATABASE_URL ACCOUNT_DELETION_SESSION_SECRET_KEY
   ```

   "A generation is running" → nothing was deleted and the request stays verified: try again later.
7. Tell the requester it is done, that copies in the daily encrypted backups expire about seven days later
   (section 2.6) and that a manual export kept by the operator is not edited (section 2.2).
8. Close: delete both email threads from the mailbox **and its trash**; keep only the date and the outcome,
   without the address, in a private record. The challenge store (`~/.travelobligator-account-deletion`)
   holds only keyed hashes and may be kept.

**What the challenge proves, and what it does not.** It proves that the requester can read the account's
mailbox now: a forged sender cannot fake it, because the code goes **to** the address. It does not prove that
the requester created the account (sign-up never verified addresses): an account someone registered with
another person's address can be deleted by the owner of that mailbox. The owner accepted that residual risk
with D3b.

**How the command keeps the challenge, and its limits** (an operator-local store instead of new server
infrastructure):

- It lives in a private directory on the operator's machine (0700, files 0600, outside the repository,
  locked while in use) and holds no address, account id or code: an scrypt hash of the code, keyed hashes
  of the account id and the address, times and attempt counters.
- A code is random (10 characters from a 31-letter alphabet, about 49 bits), valid for at most 72 hours,
  accepted once, and locked after five wrong attempts. The requester never enters it anywhere: the operator
  does, which is itself a rate limit.
- Single operator, single machine: requests open on a lost or different machine cannot be continued; start
  a new one (`challenge --replace` cancels an account's open request).
- Gmail sees the addresses and the code; a code is useless after use or after 72 hours.
- The production database URL and session secret are on the operator's machine for the duration of a run:
  typed blind, never written to a file, unset afterwards. The command refuses a database that is not at its
  code's migration head, so run it from a checkout of the commit production runs.
- No response time is promised.

### 8.2 Prerequisites before the notice offering this is published

The notice describes the process; it reaches visitors only with the release. Before that release:

- [ ] The release containing `locked_out_account_deletion.py` and the notice is deployed (`docs/29` B1, B2).
- [ ] The operator can obtain Neon's **direct** connection string and Render's `SESSION_SECRET_KEY`.
- [x] **Rehearsal:** done by the owner on 2026-10-21, passed (recorded in `docs/29` section 7). The whole of section 8.1 against a disposable database (a local one or a new Neon
      branch, never production) with a synthetic account: a wrong code, the right code, a dry run, the
      deletion, and a second attempt with the used request (refused). Record it in `docs/29` section 7.
- [ ] The contact mailbox has two-step verification enabled, and the two message texts above are saved.
