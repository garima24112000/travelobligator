# Automated Neon backups to Amazon S3

The daily, encrypted, off-Neon copy of the production database: how it works, what it needs, how to restore
it, and how to switch it on. This is operational infrastructure, separate from the application and from CI.

| Part | File |
| --- | --- |
| Workflow | `.github/workflows/neon-backup.yml` |
| Backup script (run by the workflow) | `scripts/backup/neon_s3_backup.py` |
| Recovery drill (run by a person) | `scripts/backup/restore_drill.py` |
| Tests (hermetic, part of the ordinary `pytest` run) | `backend/app/tests/scripts/test_neon_s3_backup.py` |

The manual operator tool `backend/scripts/db_backup_restore.py` (backup before a deployment, manifest,
guarded restore and `verify`; `docs/25_operations_runbook.md` section 2) is unchanged and still the
procedure around a deployment. Both currently exist only on the application release branch and reach `main`
with it. This document adds the **automatic daily** copy beside them; nothing here depends on them.

**Verification words** (as in the runbook):

| Word | Meaning |
| --- | --- |
| **Verified offline** | Executed on a developer machine against disposable local PostgreSQL 16 containers and a local stand-in for S3; no secret, no Neon, no AWS |
| **Verified in CI** | Executed by the hermetic test file on every CI run |
| **Not executed** | Nobody has run it against production Neon or the real bucket yet |

## 1. What a run does

```text
schedule (03:37 UTC, only when enabled) or "Run workflow" by a person, on main only
  -> install postgresql-client-16 + gnupg from Ubuntu's signed archive
  -> GitHub OIDC -> short-lived session of TravelObligatorNeonBackupRole (no stored AWS key)
  -> pg_dump --format=custom --no-owner --no-privileges --schema=public   (role travelobligator_backup, TLS verify-full)
  -> pg_restore --list: the archive is readable and holds alembic_version
  -> gpg symmetric encryption, AES-256-OCB (authenticated)
  -> decrypt the encrypted file again, streaming, and compare SHA-256 and size with the dump
  -> delete the plaintext
  -> S3 PutObject  s3://travelobligator-db-backups-10192026/neon/travelobligator-<UTC>-run<run id>-<attempt>.pgdump.gpg
       with a SHA-256 checksum S3 verifies on receipt, and If-None-Match: * (never overwrites)
  -> S3 HeadObject: stored size and SHA-256 equal what was sent
  -> remove the temporary directory (also on failure and on cancellation)
```

Any failed step fails the run (non-zero exit). The bucket's lifecycle rule deletes objects after 7 days; the
workflow never deletes, lists or reads other objects.

The log and the run summary show: the object name, the encrypted size, the encrypted object's SHA-256, the
server and `pg_dump` versions and how many tables hold data. **Never** the connection string or any part of
it, the passphrase, or any row. If the repository is public, so are these logs; nothing in them is personal
data.

## 2. Encryption format

GnuPG symmetric encryption with one fixed invocation (`GPG_ENCRYPT_ARGS` in the script):

| Choice | Value | Why |
| --- | --- | --- |
| Packet | OpenPGP AEAD packet (`--force-ocb`), chunked | Every 4 MiB chunk and the end of the message are authenticated: changed, reordered or truncated data is detected, not just garbled |
| Cipher / mode | AES-256 / OCB | Authenticated encryption, GnuPG's own implementation (Libgcrypt) |
| Passphrase to key | S2K mode 3 (salted, iterated), SHA-512, 65,011,712 iterations (GnuPG's maximum) | Slows guessing; a strong random passphrase is still required (24 characters minimum, enforced) |
| Compression | none | A custom-format dump is already compressed |
| Passphrase handling | Given to `gpg` through an anonymous pipe (`--passphrase-fd`), `--batch --pinentry-mode loopback --no-symkey-cache`, a fresh 0700 GnuPG home per run, its agent stopped afterwards | Never on a command line, in a file or in `gpg`'s environment; never cached |

`openssl enc` was not used: it has no authenticated mode, so a modified file would decrypt to modified data
without an error. `age` was not used: it takes a passphrase only from a terminal.

**Decrypting needs GnuPG 2.3 or newer** (macOS: `brew install gnupg`; Ubuntu 24.04 ships 2.4.4). GnuPG
writes output while it decrypts and detects a damaged or altered file only at the end, with exit status 2
and `WARNING: encrypted message has been manipulated!`. **Output from a run that did not exit 0 is not
trustworthy and must be deleted.** Wrong passphrase: `decryption failed: Bad session key`, exit 2.

Exact manual decryption (the drill script below does this for you):

```bash
mkdir -m 700 ~/tob-restore && cd ~/tob-restore       # a private directory OUTSIDE the repository
gpg --version | head -n 1                           # must be 2.3 or newer
gpg --no-symkey-cache --output backup.pgdump --decrypt travelobligator-<stamp>-run<id>-<n>.pgdump.gpg \
  && echo "AUTHENTIC" \
  || { rm -f backup.pgdump; echo "NOT AUTHENTIC OR WRONG PASSPHRASE: output deleted"; }
```

`gpg` asks for the passphrase in its own prompt (no echo). Verified offline: files encrypted by GnuPG 2.4.4
(Ubuntu 24.04, the runner) decrypt with 2.4.8 (macOS) and the other way round; a flipped byte, a truncated
file and a wrong passphrase all exit 2.

## 3. Security of the workflow

| Concern | How it is handled |
| --- | --- |
| Which events run it | `schedule` and `workflow_dispatch` only. No `pull_request`, `pull_request_target`, `push` or `workflow_run`, so no pull request's code ever runs with these secrets |
| Which branch | The job runs only when `github.ref == 'refs/heads/main'` and in this repository (not a fork). The AWS role's trust policy independently accepts only `repo:garima24112000/travelobligator:ref:refs/heads/main` |
| Scheduling | The scheduled run is skipped until the repository **variable** `NEON_BACKUP_SCHEDULE_ENABLED` is `true`. Merging the file starts nothing by itself |
| Token permissions | Workflow `permissions: {}`; the job gets `contents: read` and `id-token: write` only. `actions/checkout` with `persist-credentials: false` |
| AWS credentials | OIDC through `aws-actions/configure-aws-credentials` v6.3.0, pinned to commit `e1253824e5c10ff9df46874f81ed3ec929e19cfd` (signed tag commit, released 2026-09-15), with `allowed-account-ids`. No access key exists |
| Secrets exposure | The two secrets are passed only to the one step that runs the repository's own script, never to a third-party action. The script passes neither to any child process: `pg_dump` gets libpq variables, `gpg` the passphrase by pipe, `aws` neither. Parts of the URL are registered with `::add-mask::` as well |
| Dependencies with secrets present | No Python package is installed (standard library only, `python3 -I`). PostgreSQL client and GnuPG come from Ubuntu's signed archive, before credentials exist |
| Database role | The URL must name `travelobligator_backup` (`BACKUP_EXPECTED_DB_USER`); an owner URL put in the secret by mistake is refused. Pooled (`-pooler`) hosts, multi-host URLs, unknown options and `sslmode` below `require` are refused. TLS is always `verify-full` against the system trust store (`PGSSLROOTCERT=system`, libpq 16) |
| Plaintext | Only in a new 0700 directory under `$RUNNER_TEMP`, deleted as soon as the encrypted copy is proven, and the directory is deleted on every exit path (SIGINT/SIGTERM included, plus an `always()` cleanup step). The hosted runner is discarded after the job. No artifact is uploaded |
| Overwrite | `If-None-Match: *`: an existing object is never replaced. Names carry the UTC second, run id and attempt |
| Concurrency | One run at a time (`concurrency: neon-backup`, never cancelling a running backup) |

**Residual risks, accepted for now:**

- Repository secrets are readable by any workflow that someone with **write access** pushes to any branch.
  The database secret is only as protected as write access to the repository. Stronger: move both secrets
  into a GitHub **environment** (for example `neon-backup`) limited to `main`. That changes the OIDC subject
  to `repo:garima24112000/travelobligator:environment:neon-backup`, so the IAM trust policy must be changed in
  the same step, or the role can no longer be assumed.
- The bucket's 7-day expiration deletes objects whether or not newer ones exist. If backups fail for more
  than 7 days in a row (a broken run, or GitHub pausing schedules after 60 days without repository
  activity), the prefix empties. A failed run is the alert: confirm that it reaches someone.
- If bucket **versioning** is enabled, expiration only adds a delete marker and the old versions stay until
  a noncurrent-version rule removes them. Check this (section 7).

## 4. PostgreSQL privileges of `travelobligator_backup`

Measured offline with the real `pg_dump` 16.15 against PostgreSQL 16.15 holding this branch's full schema
(revision `a94c0e7d2b61`) and a role with exactly the grants below:

| Situation | Result |
| --- | --- |
| `CONNECT` on the database, `USAGE` on `public`, `SELECT` on all existing tables | **Backup succeeds** (9 tables with data, restore drill passes) |
| The backup role tries `INSERT` | `permission denied` (it cannot write) |
| A table created by the owner after the grant | **Backup fails**: `permission denied for table …` at `LOCK TABLE … IN ACCESS SHARE MODE` |
| A table with an identity column, table `SELECT` granted, sequence not | **Backup fails**: `permission denied for sequence …` (`SELECT last_value, is_called FROM …`) |
| Row-level security enabled on a table | **Backup fails**: `query would be affected by row-level security policy` |

So for **today's schema nothing more is needed**: every table's key is `text` (no sequence exists) and no
table uses row-level security. `pg_dump` needs no superuser, `pg_read_all_data`, `REPLICATION` or
`BYPASSRLS`.

**Recommended, narrowly scoped, run by a person** as the role that runs the migrations (the owner of the
tables; on Neon typically `neondb_owner` — confirm with `select tableowner from pg_tables where schemaname
= 'public'`), so that a table or sequence added by a **future** migration does not break the backup:

```sql
-- Only affects objects that <owner_role> creates from now on, in schema public. Grants read, nothing else.
ALTER DEFAULT PRIVILEGES FOR ROLE <owner_role> IN SCHEMA public GRANT SELECT ON TABLES    TO travelobligator_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE <owner_role> IN SCHEMA public GRANT SELECT ON SEQUENCES TO travelobligator_backup;
```

Without them, the first backup after a migration that adds a table fails loudly (never a silent partial
backup); the fix is then `GRANT SELECT ON <table> TO travelobligator_backup;`. Never grant `pg_read_all_data`
or membership in `neon_superuser` for this.

**Read-only checks** to run in the Neon SQL editor (as the owner) before the first backup:

```sql
-- 1. Expect: t, t
SELECT has_database_privilege('travelobligator_backup', current_database(), 'CONNECT'),
       has_schema_privilege('travelobligator_backup', 'public', 'USAGE');
-- 2. Expect: no rows (every table, view and sequence in public is readable by the backup role)
SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
   AND NOT has_table_privilege('travelobligator_backup', c.oid, 'SELECT');
-- 3. Expect: no rows (no row-level security)
SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relrowsecurity;
-- 4. Expect: no rows (the role cannot write anywhere in public)
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
   AND has_table_privilege('travelobligator_backup', c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE');
-- 5. Expect: f, f, f, f and no memberships (roles created in the Neon console are members of neon_superuser;
--    this one must have been created with SQL)
SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls FROM pg_roles WHERE rolname = 'travelobligator_backup';
SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid
 WHERE m.member = 'travelobligator_backup'::regrole;
-- 6. Expect: 16 (pg_dump 16 refuses a newer server; the job then fails until its client is upgraded)
SHOW server_version;
```

Grants are per database and are copied into Neon branches created afterwards.

## 5. Restore

Restoring needs three things the workflow does not have: **read access to the bucket for a person** (the
backup role trusts only GitHub's `main` workflow; an operator needs their own IAM identity with
`s3:GetObject` on `arn:aws:s3:::travelobligator-db-backups-10192026/neon/*`, and `s3:ListBucket` limited to
`neon/` to find object names), **the passphrase**, and **GnuPG 2.3+ and Docker**.

### 5.1 Recovery drill (the normal case): a throwaway local database

`scripts/backup/restore_drill.py` accepts **no database URL**. It restores only into a PostgreSQL 16
container it starts itself (image pinned by digest) with `--network none` and its data directory on tmpfs,
reached only through `docker exec`, removed when the drill ends. It cannot reach Neon or any other server.

```bash
aws login                                              # or your SSO profile: an identity that may read neon/*
aws s3api list-objects-v2 --bucket travelobligator-db-backups-10192026 --prefix neon/ \
    --query 'Contents[].[Key,Size,LastModified]' --output table
python3 scripts/backup/restore_drill.py --s3-key neon/travelobligator-<stamp>-run<id>-<n>.pgdump.gpg \
    --expect-revision <the Alembic revision production runs>
# or, with a file you already downloaded:  --file <path>.pgdump.gpg
```

It asks for the passphrase without echo, then: downloads with checksum verification → decrypts **and
authenticates** (any failure deletes the output and stops) → copies the dump into the container and deletes
the host copy → restores in one transaction (`--exit-on-error --single-transaction --no-owner
--no-privileges`, without the `CREATE SCHEMA public` entry every new database already has) → checks:
expected core tables present, exactly one Alembic revision (and the expected one, if given), every table's
row count, and that no trip, planning state, job, branch or revision points at a missing owner or trip →
`Drill PASSED` / `Drill FAILED`, exit 0 / 1 → container and temporary files removed on every path.

It prints table names, the revision and row counts only. Run it in a terminal, not in CI, and do not paste
its output anywhere public (row counts are not personal data, but there is no reason to publish them).

Verified offline: backup in an Ubuntu 24.04 container (real `pg_dump` 16.15 and GnuPG 2.4.4, TLS
`verify-full`) → drill on macOS (GnuPG 2.4.8, Docker): passed, with the synthetic counts that were inserted;
wrong passphrase, wrong expected revision: failed; no container or temporary file left behind.

### 5.2 Real recovery into Neon

Only into a **new** target: a new Neon branch (or project) created for the purpose, with a **new, empty
database** in it. Never the production branch.

1. Decrypt as in section 2 (exit status 0 required).
2. In the Neon console create the branch and database; copy **its** direct connection details from there.
3. In a fresh shell, connect through libpq variables (nothing on a command line):

   ```bash
   export PGHOST=<new branch's direct host> PGDATABASE=<new database> PGUSER=<its owner role> PGSSLMODE=verify-full PGSSLROOTCERT=system
   read -rs PGPASSWORD && export PGPASSWORD
   psql -XAtc "select current_database(), count(*) from pg_tables where schemaname = 'public'"   # must print the new name and 0
   pg_restore --list backup.pgdump | grep -v ' SCHEMA - public ' > restore.list
   pg_restore --no-owner --no-privileges --exit-on-error --single-transaction --use-list=restore.list --dbname="$PGDATABASE" backup.pgdump
   unset PGPASSWORD
   ```

   `pg_restore` must be version 16 or newer. If the second check does not print 0, stop: it is not an empty
   database.
4. Validate with the operator tool against the copy, from a checkout that has it (`VERIFY_DATABASE_URL=<the copy>`
   `python backend/scripts/db_backup_restore.py verify`; it checks structure, references and that every
   planning state loads). A CI-made backup has no manifest, so `verify` runs without `--manifest`.
   Verified offline: steps 1–3 exactly as written (decrypt, empty-target check, restore over `verify-full`).
5. Pointing the application at a restored database is a deployment decision (runbook section 4.6), not a
   step of this procedure.
6. Delete `backup.pgdump`, `restore.list` and the directory afterwards (`rm -rf ~/tob-restore`), and the
   branch when it was only a test.

### 5.3 Recovery drill checklist

Do this after the first production backup, then at least monthly and after any change to the workflow, the
passphrase or the PostgreSQL major version:

- [ ] Pick the newest object under `neon/`; note its name and `LastModified` (not older than ~26 hours if
      the schedule is on).
- [ ] Run `restore_drill.py --s3-key … --expect-revision <production revision>` in a terminal on a machine
      with full-disk encryption.
- [ ] Result is `Drill PASSED`; the revision is production's; the row counts are plausible (users, trips and
      planning states not zero; similar to the last drill).
- [ ] `docker ps -a --filter label=travelobligator.restore-drill=true` shows nothing; no
      `travelobligator-restore-drill-*` directory remains in `$TMPDIR`.
- [ ] Record date, object name, duration and result (no counts needed) in section 8 of this document.
- [ ] Every few drills: also decrypt by hand (section 2) to prove the passphrase copy outside GitHub works
      without the script.

## 6. Passphrase: storage, loss, rotation

**GitHub secrets cannot be read back.** The passphrase must also exist outside GitHub — in the owner's
password manager (and, ideally, a sealed offline copy). If the only copy is the GitHub secret, no backup can
ever be decrypted. Confirm this before the first run.

- **Lost passphrase:** every backup encrypted with it is permanently unreadable; that is the point of the
  encryption and there is no recovery. Generate a new one (`openssl rand -base64 32`), store it outside
  GitHub first, then replace `BACKUP_ENCRYPTION_PASSPHRASE`, run the workflow by hand and drill the new
  object. Until then the only recovery path is Neon's own point-in-time history (runbook section 2.1).
  Objects under the lost passphrase expire within 7 days.
- **Rotation** (planned, or when someone who knew it should no longer): store the new passphrase outside
  GitHub; update the secret; note the UTC time of the change; run by hand and drill the new object. Keep the
  old passphrase for 8 days (objects made before the change still use it), then destroy it. Object names
  carry their UTC time, so the change time tells which passphrase an object needs; a wrong one fails
  cleanly with `Bad session key`.
- **Suspected exposure of the passphrase** (with possible access to the bucket): rotate at once. Old objects
  stay readable with the old passphrase until they expire; delete them earlier only if that risk outweighs
  losing the restore points.

## 7. Switching it on

The AWS role trusts only `main`, so the workflow must be on `main` to run. The application release branch is
not ready to merge, so these files go to `main` **alone**:

1. From a clean tree: `git switch -c ops/neon-s3-backup origin/main` (the new files are untracked and come
   along; none exists on `main`).
2. `git add .github/workflows/neon-backup.yml scripts/backup/ backend/app/tests/scripts/test_neon_s3_backup.py docs/30_automated_neon_backups.md`
   — **only** these; `git status` must show nothing else staged. Run
   `pytest backend/app/tests/scripts/test_neon_s3_backup.py` and `python -m compileall backend/app scripts/backup`.
3. Commit, push the branch, open a pull request to `main`. CI runs as usual; the backup workflow does not run
   on pull requests. Review, then merge.
4. On `main`, leave the variable `NEON_BACKUP_SCHEDULE_ENABLED` **unset**. Complete the prerequisites below.
5. First backup by hand: Actions → **Neon backup** → Run workflow → branch `main`. Read the log: one object
   stored and verified.
6. Drill that object (section 5.3) and record it in section 8.
7. Only then: Settings → Secrets and variables → Actions → Variables → `NEON_BACKUP_SCHEDULE_ENABLED` =
   `true`. To stop scheduled backups at any time, delete the variable (or disable the workflow).
8. The release branch picks these files up when it next merges `main`: the same files, no conflict. Add a
   pointer to this document in `docs/25_operations_runbook.md` section 2 at that point.

**Prerequisites before step 5:**

- [ ] The passphrase is stored outside GitHub (section 6), and is at least 24 characters on one line.
- [ ] The `NEON_BACKUP_DATABASE_URL` secret is the **direct** (not `-pooler`) endpoint, user
      `travelobligator_backup`; the read-only checks of section 4 return what they should, including
      PostgreSQL 16.
- [ ] Decide on the default-privilege grants of section 4 (recommended).
- [ ] AWS (owner, in the console): bucket versioning off, or a noncurrent-version expiration rule exists;
      default encryption is on; no other principal can read `neon/*` that should not; the role's maximum
      session duration is at least 1 hour (the workflow asks for 3600 s).
- [ ] An identity for a person to read `neon/*` for restores exists (section 5).
- [ ] Failed-run notifications for scheduled workflows reach the owner (GitHub notification settings).
- [ ] **Privacy notice and retention.** Once this runs, the project holds daily encrypted copies for 7
      days. The release branch's privacy notice (`frontend/lib/privacy-notice.ts`) currently says exports
      are made before deployments and that no retention period is set, and the runbook (section 2.2) keeps
      a 30-day *recommendation* with four unverified checks. Production (`main`) shows no privacy notice.
      Decide the wording, and update the notice and the runbook's retention section together in the release
      branch, before that branch ships. This change does not touch either.

## 8. Drill record

| Date (UTC) | Where | Object | Result |
| --- | --- | --- | --- |
| 2026-10-19 | **Verified offline** (synthetic data; local TLS PostgreSQL 16.15; stand-in S3) | `neon/travelobligator-20261019T201654Z-run111-1.pgdump.gpg` (local only) | Backup passed; drill passed; wrong passphrase and wrong revision refused |
| — | **Not executed**: production Neon → real bucket → drill | — | — |
