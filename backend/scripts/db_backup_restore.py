#!/usr/bin/env python3
"""Operator tool: export a PostgreSQL backup, restore it into an EMPTY database, verify a database.

    python backend/scripts/db_backup_restore.py backup  --out <directory outside the repository>
    python backend/scripts/db_backup_restore.py restore --dump <file.dump> --confirm-empty-target <database name>
    python backend/scripts/db_backup_restore.py verify  [--manifest <file.manifest.json>]

Procedure, retention and the restore drill record: docs/25_operations_runbook.md.

Which database each command touches is decided by a DIFFERENT environment variable on purpose, so the
application's own `DATABASE_URL` can never be the target of a restore by accident:

    backup    reads   BACKUP_SOURCE_DATABASE_URL     (read-only: pg_dump)
    restore   writes  RESTORE_TARGET_DATABASE_URL    (must be an EMPTY database; refused otherwise)
    verify    reads   VERIFY_DATABASE_URL            (read-only)

Safety contract:
  * Never run automatically -- not by the application, an image, Compose or CI deployment.
  * `restore` never drops, cleans or overwrites. A target holding ANY object, in any schema, is refused,
    whatever its URL -- so it cannot write into a database that is in use, however that database is
    addressed. It runs in one transaction (a failed or interrupted restore leaves the target empty), needs
    the dump's manifest and checksum, verifies the copy against the manifest afterwards, and reports a
    mismatch as REJECTED.
  * `restore` also refuses the databases it is told about -- BACKUP_SOURCE_DATABASE_URL and DATABASE_URL --
    first by comparing the URLs as text (case, default port, local names and Neon's pooled host folded
    together) and then by asking both servers who they are, so another spelling of the same database is
    caught; if either cannot be reached to be asked, it refuses. And the operator must name the target
    database (`--confirm-empty-target`), which must be the one the server reports.
  * WHAT IT CANNOT DO: know which database is "production". Nothing in a URL says so. The rules above stop
    a restore into any database with content; choosing a new, isolated, disposable target is the operator's
    step (runbook section 2.3) and is not something this tool can promise on its own.
  * No connection string is ever printed, logged, written to a file or put on a command line: `pg_dump` /
    `pg_restore` receive it through libpq environment variables; a tool's error output is filtered and has
    the connection's host, user, database and password blanked; an unforeseen error is reported by type only.
    A URL with a connection option this tool would not pass on is refused.
  * A backup is a full copy of personal data (email addresses, password hashes, trip text). The file is
    created `0600`, in a directory that is new (`0700`) or already private -- an existing directory other
    users can read is refused, never re-permissioned -- and a directory inside this repository is refused.
    The manifest holds counts, digests and revision ids only. The dump itself is NOT encrypted by this
    tool: keep it on encrypted storage.
  * A backup's manifest and dump describe one instant (one exported snapshot).
  * `verify` prints counts and revision ids only -- never an email address, a trip id or itinerary text.

`verify` checks: the Alembic revision (against the code's head, or the backup's manifest); table and column
structure against the application's models (when the database is at the code's head); ownership and
reference integrity; that every stored planning state still loads as a `PlanningState`; and, with
`--manifest`, that every table has exactly the row count and content fingerprint the backup recorded.

Requires `pg_dump` / `pg_restore` at least as new as the server's major version (PostgreSQL refuses to dump
a newer server with an older client).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_REPOSITORY_ROOT = _BACKEND_ROOT.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

BACKUP_SOURCE_ENV = "BACKUP_SOURCE_DATABASE_URL"
RESTORE_TARGET_ENV = "RESTORE_TARGET_DATABASE_URL"
VERIFY_ENV = "VERIFY_DATABASE_URL"
MANIFEST_VERSION = 1
# URL query options this tool passes on to pg_dump / pg_restore, and the libpq variable of each. A URL with
# any other option is refused (see `_url`).
_LIBPQ_OPTIONS = {
    "sslmode": "PGSSLMODE",
    "channel_binding": "PGCHANNELBINDING",
    "sslrootcert": "PGSSLROOTCERT",
    "options": "PGOPTIONS",
}

# Every table the application owns, parents before children. `verify` fails if the models gain a table that
# is not listed here, so a new table cannot silently go unchecked.
APPLICATION_TABLES = (
    "users",
    "trips",
    "planning_states",
    "generation_jobs",
    "itinerary_branches",
    "itinerary_revisions",
    "auth_attempts",
    "deleted_job_usage",
)

# (description, tables it reads, SQL returning one count). Each must be 0 in a consistent database. The foreign keys already
# guarantee most of these; they are re-checked because a restore is exactly when a guarantee might not hold.
INTEGRITY_CHECKS = (
    ("trips whose owner does not exist", ("trips", "users"), "select count(*) from trips t where t.owner_id is not null and not exists (select 1 from users u where u.user_id = t.owner_id)"),
    ("planning states without a trip", ("planning_states", "trips"), "select count(*) from planning_states p where not exists (select 1 from trips t where t.trip_id = p.trip_id)"),
    ("jobs without a trip", ("generation_jobs", "trips"), "select count(*) from generation_jobs j where not exists (select 1 from trips t where t.trip_id = j.trip_id)"),
    ("jobs whose owner does not exist", ("generation_jobs", "users"), "select count(*) from generation_jobs j where not exists (select 1 from users u where u.user_id = j.owner_id)"),
    ("jobs owned by someone other than the trip's owner", ("generation_jobs", "trips"), "select count(*) from generation_jobs j join trips t on t.trip_id = j.trip_id where t.owner_id is not null and t.owner_id <> j.owner_id"),
    ("branches without a trip", ("itinerary_branches", "trips"), "select count(*) from itinerary_branches b where not exists (select 1 from trips t where t.trip_id = b.trip_id)"),
    ("revisions without a trip", ("itinerary_revisions", "trips"), "select count(*) from itinerary_revisions r where not exists (select 1 from trips t where t.trip_id = r.trip_id)"),
    ("trips with more than one active job", ("generation_jobs",), "select count(*) from (select trip_id from generation_jobs where status in ('queued','running') group by trip_id having count(*) > 1) d"),
    ("duplicate email addresses", ("users",), "select count(*) from (select lower(email) from users group by lower(email) having count(*) > 1) d"),
)


class OperatorError(Exception):
    """A refusal or failure, with a fixed message that is safe to print."""


def _say(message: str) -> None:
    print(message, flush=True)


# -- connection handling (the URL never leaves this process as text) ----------------------------------


def _url(env_name: str):
    from sqlalchemy.engine import make_url

    raw = (os.environ.get(env_name) or "").strip()
    if not raw:
        raise OperatorError(f"{env_name} is not set.")
    try:
        url = make_url(raw)
        if not (url.drivername.split("+")[0] in {"postgresql", "postgres"} and url.host and url.database):
            raise ValueError
    except Exception:
        raise OperatorError(f"{env_name} is not a usable PostgreSQL URL.") from None
    # Only connection options this tool hands on to the PostgreSQL client tools are accepted. An option it
    # would silently drop (a second host, a service file, a socket directory ...) could make pg_dump /
    # pg_restore reach a different server than the one this process inspected -- so it is refused instead.
    unknown = sorted(set(url.query) - set(_LIBPQ_OPTIONS))
    if unknown or any(not isinstance(value, str) for value in url.query.values()) or "," in url.host:
        raise OperatorError(f"{env_name} uses a connection option this tool does not pass on. Allowed: {', '.join(sorted(_LIBPQ_OPTIONS))}.")
    return url


def _engine(env_name: str):
    from sqlalchemy import create_engine

    try:
        return create_engine(_url(env_name).set(drivername="postgresql+psycopg"), pool_pre_ping=True)
    except OperatorError:
        raise
    except Exception:
        raise OperatorError(f"Could not prepare a connection from {env_name}.") from None


def _libpq_environment(env_name: str) -> dict[str, str]:
    """The connection as libpq variables, so nothing secret is on a command line (visible in `ps`)."""
    url = _url(env_name)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    for name in (BACKUP_SOURCE_ENV, RESTORE_TARGET_ENV, VERIFY_ENV, "DATABASE_URL"):
        environment.pop(name, None)
    environment.update({"PGHOST": url.host, "PGDATABASE": url.database, "PGAPPNAME": "travelobligator-backup"})
    if url.port:
        environment["PGPORT"] = str(url.port)
    if url.username:
        environment["PGUSER"] = url.username
    if url.password:
        environment["PGPASSWORD"] = str(url.password)
    for option, variable in _LIBPQ_OPTIONS.items():
        value = url.query.get(option)
        if isinstance(value, str) and value:
            environment[variable] = value
    return environment


def _secrets_of(env_name: str) -> list[str]:
    """Every part of the connection that must never be shown, longest first."""
    url = _url(env_name)
    parts = {str(url.password or ""), url.username or "", url.host or "", url.database or ""}
    return sorted((part for part in parts if len(part) >= 3), key=len, reverse=True)


def _written_address(env_name: str) -> tuple[str, int, str]:
    """Where a URL says it points, with the spellings that mean the same place folded together: letter case,
    a trailing dot, the default port, the names of this machine, and Neon's pooled (`-pooler`) host name for
    the same endpoint. This is a comparison of TEXT. Two different names (an IP address, a DNS alias, another
    proxy) can still reach one database; that is what `_server_identity` is for."""
    url = _url(env_name)
    host = url.host.strip().lower().rstrip(".")
    if host in {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}:
        host = "localhost"
    first_label, _, rest = host.partition(".")
    if first_label.endswith("-pooler"):
        host = first_label.removesuffix("-pooler") + ("." + rest if rest else "")
    return host, int(url.port or 5432), url.database


def _server_identity(connection) -> tuple:
    """What the SERVER says it is, whatever name was used to reach it: the cluster's identifier, the moment
    its postmaster started, and this database's own object id. Two connections that report the same triple
    are the same running database."""
    from sqlalchemy import text

    database_oid, started = connection.execute(text("select (select oid from pg_database where datname = current_database()), pg_postmaster_start_time()")).one()
    try:
        with connection.begin_nested():
            system = connection.execute(text("select system_identifier from pg_control_system()")).scalar_one()
    except Exception:
        system = None  # not permitted for this role: the other two still tell servers apart
    return (system, started, int(database_oid))


def _identity_of(env_name: str, what: str) -> tuple:
    engine = _engine(env_name)
    try:
        with engine.connect() as connection:
            return _server_identity(connection)
    except Exception:
        raise OperatorError(
            f"Could not reach {what} ({env_name}) to make sure it is a different database from the restore target. "
            f"Refusing. Unset {env_name} in this shell if it does not belong here, or make it reachable."
        ) from None
    finally:
        engine.dispose()


def _refuse_if_target_is(other_env: str, what: str) -> None:
    """Refuse when the restore target is, or cannot be shown not to be, the database `other_env` names."""
    if not (os.environ.get(other_env) or "").strip():
        return
    if _written_address(RESTORE_TARGET_ENV) == _written_address(other_env):
        raise OperatorError(f"{RESTORE_TARGET_ENV} points at the same database as {other_env} ({what}). A restore only ever goes into a new, separate database.")
    # Different text is not proof of a different database: ask both servers.
    if _identity_of(RESTORE_TARGET_ENV, "the restore target") == _identity_of(other_env, what):
        raise OperatorError(f"{RESTORE_TARGET_ENV} reaches the same database as {other_env} ({what}) under another name. A restore only ever goes into a new, separate database.")


def _run_tool(tool: str, arguments: list[str], env_name: str | None) -> str:
    """Run a PostgreSQL client tool against the database of `env_name` (None: a tool call that reads only a
    local file and gets no connection at all). Returns its standard output."""
    binary = shutil.which(tool)
    if binary is None:
        raise OperatorError(f"{tool} was not found on PATH. Install the PostgreSQL client tools.")
    if env_name is None:
        environment = {key: value for key, value in os.environ.items() if not key.startswith("PG") and not key.endswith("DATABASE_URL")}
    else:
        environment = _libpq_environment(env_name)
    completed = subprocess.run([binary, *arguments], env=environment, capture_output=True, text=True)
    if completed.returncode != 0:
        # The tool's own message is shown, minus every line that could carry connection detail, and with the
        # connection's own host, user, database and password blanked wherever else they appear.
        secrets = _secrets_of(env_name) if env_name else []
        safe = []
        for line in completed.stderr.splitlines():
            if "connection to server" in line or "://" in line or "password" in line.lower():
                continue
            for secret in secrets:
                line = line.replace(secret, "[hidden]")
            safe.append(line)
        for line in safe[-8:]:
            print(f"  {tool}: {line}", file=sys.stderr)
        raise OperatorError(f"{tool} failed (exit code {completed.returncode}).")
    return completed.stdout


def _tool_version(tool: str) -> str:
    binary = shutil.which(tool)
    if binary is None:
        raise OperatorError(f"{tool} was not found on PATH. Install the PostgreSQL client tools.")
    return subprocess.run([binary, "--version"], capture_output=True, text=True).stdout.strip()


# -- what a database contains, as numbers ------------------------------------------------------------


def _existing_objects(connection) -> int:
    """How many tables, views, sequences, types or functions exist outside PostgreSQL's own schemas -- in
    ANY schema, not only `public`. A database that is safe to restore into has none."""
    from sqlalchemy import text

    system = "n.nspname not in ('pg_catalog', 'information_schema') and n.nspname not like 'pg\\_toast%' and n.nspname not like 'pg\\_temp%'"
    return int(
        connection.execute(
            text(
                f"select (select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where {system}"
                "      and not exists (select 1 from pg_depend d where d.objid = c.oid and d.deptype = 'e'))"
                f" + (select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where {system}"
                # functions that belong to an installed extension are the server's, not data
                "      and not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e'))"
            )
        ).scalar_one()
    )


def _public_tables(connection) -> list[str]:
    from sqlalchemy import text

    rows = connection.execute(text("select tablename from pg_tables where schemaname = 'public' order by tablename"))
    return [row[0] for row in rows]


def _revisions(connection) -> list[str]:
    from sqlalchemy import text

    if "alembic_version" not in _public_tables(connection):
        return []
    return sorted(row[0] for row in connection.execute(text("select version_num from alembic_version")))


def _fingerprint(connection, table: str) -> dict[str, Any]:
    """Row count and an order-independent digest of every row's full content. A digest only: nothing of
    the content can be read back from it."""
    from sqlalchemy import text

    count, digest = connection.execute(
        text(
            f'select count(*), coalesce(md5(string_agg(row_digest, \'\' order by row_digest)), \'\') '
            f'from (select md5(t::text) as row_digest from "{table}" t) rows'
        )
    ).one()
    return {"rows": int(count), "digest": digest}


def describe(connection) -> dict[str, Any]:
    from sqlalchemy import text

    # A row's text form includes timestamps: pin how they are written, so the same rows give the same
    # digest in any database, whatever its default time zone or date style.
    connection.execute(text("set time zone 'UTC'"))
    connection.execute(text("set datestyle to 'ISO, YMD'"))
    connection.execute(text("set extra_float_digits to 1"))
    tables = [table for table in _public_tables(connection) if table != "alembic_version"]
    return {
        "alembic_revisions": _revisions(connection),
        "tables": {table: _fingerprint(connection, table) for table in tables},
    }


def code_heads() -> list[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return sorted(ScriptDirectory.from_config(Config(str(_BACKEND_ROOT / "alembic.ini"))).get_heads())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# -- backup ------------------------------------------------------------------------------------------


def backup(out_directory: Path, *, now: datetime | None = None) -> Path:
    out_directory = out_directory.expanduser().resolve()
    if out_directory == _REPOSITORY_ROOT or _REPOSITORY_ROOT in out_directory.parents:
        raise OperatorError("Refusing to write a backup inside the repository. Choose a directory outside it.")
    if out_directory.exists():
        # An existing directory is used only if it is already private. It is never re-permissioned here: the
        # operator may have pointed at a directory that other things rely on.
        if not out_directory.is_dir() or out_directory.stat().st_mode & 0o077:
            raise OperatorError("The output directory exists and is readable by other users. Use a new directory, or one with mode 0700.")
    else:
        out_directory.mkdir(mode=0o700, parents=True)
        os.chmod(out_directory, 0o700)  # mkdir's mode is subject to the umask

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    dump_path = out_directory / f"travelobligator-{stamp}.dump"
    manifest_path = out_directory / f"travelobligator-{stamp}.manifest.json"
    if dump_path.exists() or manifest_path.exists():
        raise OperatorError("A backup with this timestamp already exists in that directory.")

    engine = _engine(BACKUP_SOURCE_ENV)
    created = False
    try:
        # The manifest and the dump describe ONE instant: this transaction exports its snapshot and pg_dump
        # reads through the same one, so a write by the application in between is in both or in neither.
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            from sqlalchemy import text

            try:
                server_version = connection.execute(text("show server_version")).scalar_one()
                before = describe(connection)
                snapshot = connection.execute(text("select pg_export_snapshot()")).scalar_one()
            except Exception:
                raise OperatorError("Could not read the source database.") from None
            if not before["alembic_revisions"]:
                raise OperatorError("The source database has no Alembic revision. It is not a migrated application database.")

            descriptor = os.open(dump_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            created = True
            # custom format: compressed, restorable selectively; no owner / privileges, so it restores under
            # any role (Neon's role names do not exist elsewhere). The snapshot id is not a secret.
            _run_tool("pg_dump", ["--format=custom", "--no-owner", "--no-privileges", "--schema=public", f"--snapshot={snapshot}", f"--file={dump_path}"], BACKUP_SOURCE_ENV)
    except BaseException as error:
        if created:
            dump_path.unlink(missing_ok=True)  # never leave a partial dump behind, not even on Ctrl-C
        if isinstance(error, Exception) and not isinstance(error, OperatorError):
            # e.g. the connection itself failed: a driver's message may name the host, so it is not shown
            raise OperatorError("Could not read the source database.") from None
        raise
    finally:
        engine.dispose()
    if oct(dump_path.stat().st_mode & 0o777) != "0o600":
        dump_path.unlink(missing_ok=True)
        raise OperatorError("The dump file did not keep its private permissions. Nothing was kept.")

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "created_at_utc": stamp,
        "dump_file": dump_path.name,
        "dump_sha256": _sha256(dump_path),
        "dump_bytes": dump_path.stat().st_size,
        "server_version": server_version,
        "pg_dump_version": _tool_version("pg_dump"),
        "code_alembic_heads_at_backup": code_heads(),
        **before,
    }
    descriptor = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")

    _say(f"Backup written: {dump_path.name} ({manifest['dump_bytes']} bytes), revision {', '.join(before['alembic_revisions'])}.")
    for table, entry in sorted(before["tables"].items()):
        _say(f"  {table}: {entry['rows']} rows")
    _say(f"Manifest: {manifest_path.name}. Keep both files together, private, and outside the repository.")
    return manifest_path


# -- restore -----------------------------------------------------------------------------------------


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text())
        if manifest.get("manifest_version") != MANIFEST_VERSION or "tables" not in manifest:
            raise ValueError
    except Exception:
        raise OperatorError("The manifest could not be read or is not a backup manifest of this tool.") from None
    return manifest


def restore(dump_path: Path, confirmed_database: str) -> None:
    """Restore `dump_path` into the EMPTY database of RESTORE_TARGET_DATABASE_URL, then verify it.

    What this function guarantees by itself, whatever the URL is and however it is spelled:
      * it never writes into a database that holds any object (so never into a database in use);
      * it never drops, cleans or overwrites; the restore is one transaction, so a failed or interrupted
        one leaves the target exactly as empty as it was;
      * a dump that does not match its manifest is refused, and a restored copy that does not match the
        manifest is reported as REJECTED.
    What it cannot know is which database is "production". It refuses the databases it is told about
    (BACKUP_SOURCE_DATABASE_URL and DATABASE_URL, compared as text and by asking the servers) and it makes
    the operator name the target database -- but choosing an isolated target is the operator's step, as
    the runbook says."""
    dump_path = dump_path.expanduser().resolve()
    if not dump_path.is_file():
        raise OperatorError("The dump file does not exist.")
    manifest_path = dump_path.with_name(dump_path.name.removesuffix(".dump") + ".manifest.json")
    if not manifest_path.is_file():
        raise OperatorError("There is no manifest beside the dump, so the file cannot be checked. Refusing to restore an unverifiable dump.")
    manifest = _load_manifest(manifest_path)
    if manifest.get("dump_file") != dump_path.name or manifest.get("dump_sha256") != _sha256(dump_path):
        raise OperatorError("The dump file does not match its manifest checksum. It is damaged or was replaced.")
    _say("Checksum matches the manifest.")

    # -- which database is this? ------------------------------------------------------------------
    _refuse_if_target_is(BACKUP_SOURCE_ENV, "the backup source")
    _refuse_if_target_is("DATABASE_URL", "the application's database")
    engine = _engine(RESTORE_TARGET_ENV)
    try:
        with engine.connect() as connection:
            from sqlalchemy import text

            actual_database = connection.execute(text("select current_database()")).scalar_one()
            existing = _existing_objects(connection)
    except Exception:
        raise OperatorError("Could not read the target database.") from None
    finally:
        engine.dispose()
    if confirmed_database != actual_database:
        raise OperatorError(
            "The database named with --confirm-empty-target is not the one the target server reports. "
            "Check which database RESTORE_TARGET_DATABASE_URL really reaches before going on."
        )
    if existing:
        raise OperatorError(
            f"The target database is not empty ({existing} objects). Refusing to restore into it. "
            "Create a new, empty database and restore there."
        )

    # -- restore, in one transaction --------------------------------------------------------------
    # The dump holds the `public` schema only (nothing provider-specific from outside it), and so begins
    # with `CREATE SCHEMA public`, which every new database already has. Restore everything except that one
    # entry, selected from the archive's own table of contents (object names only; private all the same).
    contents = _run_tool("pg_restore", ["--list", str(dump_path)], None)
    wanted = [line for line in contents.splitlines() if not (line and not line.startswith(";") and " SCHEMA - public " in f"{line} ")]
    descriptor, list_name = tempfile.mkstemp(prefix="travelobligator-restore-", suffix=".list")  # 0600
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write("\n".join(wanted) + "\n")
        _run_tool(
            "pg_restore",
            ["--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction", f"--use-list={list_name}", "--dbname", actual_database, str(dump_path)],
            RESTORE_TARGET_ENV,
        )
    finally:
        Path(list_name).unlink(missing_ok=True)

    # -- accept it only if it is the backup -------------------------------------------------------
    _say("Restore finished. Verifying the copy against the manifest:")
    if not verify(manifest_path, env_name=RESTORE_TARGET_ENV):
        raise OperatorError("The restored copy does NOT match the backup's manifest. REJECTED: do not use this database; drop it and investigate.")
    _say("The restored copy matches the backup. It is at the backup's revision; see the runbook for bringing it to the code's head.")


# -- verify ------------------------------------------------------------------------------------------


def _structure_problems(connection) -> list[str]:
    """Tables and columns compared with the application's models. Meaningful only at the code's head."""
    from sqlalchemy import inspect

    from app.db.models import Base

    inspector = inspect(connection)
    actual_tables = set(inspector.get_table_names(schema="public")) - {"alembic_version"}
    expected_tables = set(Base.metadata.tables)
    problems = []
    if expected_tables != set(APPLICATION_TABLES):
        problems.append("the tool's table list is out of date with the application's models")
    for table in sorted(expected_tables - actual_tables):
        problems.append(f"table {table} is missing")
    for table in sorted(actual_tables - expected_tables):
        problems.append(f"unexpected table {table}")
    for table in sorted(expected_tables & actual_tables):
        expected = {column.name: column for column in Base.metadata.tables[table].columns}
        actual = {column["name"]: column for column in inspector.get_columns(table, schema="public")}
        for name in sorted(set(expected) - set(actual)):
            problems.append(f"column {table}.{name} is missing")
        for name in sorted(set(actual) - set(expected)):
            problems.append(f"unexpected column {table}.{name}")
        for name in sorted(set(expected) & set(actual)):
            if bool(expected[name].nullable) != bool(actual[name]["nullable"]):
                problems.append(f"column {table}.{name} has a different NULL rule")
        expected_indexes = {index.name for index in Base.metadata.tables[table].indexes}
        actual_indexes = {index["name"] for index in inspector.get_indexes(table, schema="public")}
        for name in sorted(expected_indexes - actual_indexes):
            problems.append(f"index {name} is missing")
    return problems


def _planning_state_report(connection) -> dict[str, int]:
    """Every stored state must still load as the application's own model -- that is what "the itinerary
    was restored" means. Counts only."""
    from sqlalchemy import text

    from app.models.planning_state import PlanningState

    report = {"states": 0, "loadable": 0, "with_scheduled_stops": 0, "trip_id_mismatch": 0}
    rows = connection.execution_options(stream_results=True).execute(text("select trip_id, state from planning_states"))
    for trip_id, state in rows:
        report["states"] += 1
        try:
            loaded = PlanningState.model_validate(state)
        except Exception:
            continue
        report["loadable"] += 1
        if loaded.trip_id != trip_id:
            report["trip_id_mismatch"] += 1
        plan = loaded.experience_plan
        if plan is not None and any(day.experiences for day in plan.daily_plans):
            report["with_scheduled_stops"] += 1
    return report


def verify(manifest_path: Path | None = None, *, env_name: str = VERIFY_ENV) -> bool:
    from sqlalchemy import text

    manifest = _load_manifest(manifest_path.expanduser().resolve()) if manifest_path else None
    problems: list[str] = []
    engine = _engine(env_name)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            actual = describe(connection)
            heads = code_heads()
            revisions = actual["alembic_revisions"]
            _say(f"Alembic revision in the database: {', '.join(revisions) or 'none'}; head of this code: {', '.join(heads)}.")

            at_code_head = revisions == heads
            if manifest is not None:
                if revisions != manifest["alembic_revisions"]:
                    problems.append("the Alembic revision differs from the one recorded in the backup manifest")
                if not at_code_head:
                    _say("  The database is not at this code's head. That is expected for a backup taken before a migration;")
                    _say("  the application will not start against it until it is migrated (alembic upgrade head).")
            elif not at_code_head:
                problems.append("the database is not at the Alembic head of this code")

            if at_code_head:
                structure = _structure_problems(connection)
                problems.extend(structure)
                _say(f"Structure against the application's models: {'matches' if not structure else f'{len(structure)} difference(s)'}.")
            else:
                _say("Structure against the application's models: not compared (the database is at an older revision).")

            for table in sorted(actual["tables"]):
                _say(f"  {table}: {actual['tables'][table]['rows']} rows")
            if manifest is not None:
                expected_tables = manifest["tables"]
                for table in sorted(set(expected_tables) | set(actual["tables"])):
                    if table not in actual["tables"]:
                        problems.append(f"table {table} is in the backup manifest but not in the database")
                    elif table not in expected_tables:
                        problems.append(f"table {table} is in the database but not in the backup manifest")
                    elif actual["tables"][table]["rows"] != expected_tables[table]["rows"]:
                        problems.append(f"table {table} has {actual['tables'][table]['rows']} rows; the backup recorded {expected_tables[table]['rows']}")
                    elif actual["tables"][table]["digest"] != expected_tables[table]["digest"]:
                        problems.append(f"table {table} has the recorded row count but different content")
                _say(f"Row counts and content fingerprints against the manifest: {'checked' if expected_tables else 'nothing recorded'}.")

            present = set(actual["tables"])
            for description, needed, query in INTEGRITY_CHECKS:
                if not set(needed) <= present:
                    continue  # a table of a later migration: nothing to check at this revision
                count = connection.execute(text(query)).scalar_one()
                if count:
                    problems.append(f"{count} {description}")
            ownerless = connection.execute(text("select count(*) from trips where owner_id is null")).scalar_one() if "trips" in present else 0
            owned = connection.execute(text("select count(*) from trips where owner_id is not null")).scalar_one() if "trips" in present else 0
            _say(f"Ownership: {owned} trips with an owner, {ownerless} without one (unreachable through the application).")

            if "planning_states" in present:
                states = _planning_state_report(connection)
                _say(
                    f"Planning states: {states['states']} stored, {states['loadable']} load as the application's model, "
                    f"{states['with_scheduled_stops']} hold an itinerary with scheduled stops."
                )
                if states["loadable"] != states["states"]:
                    problems.append(f"{states['states'] - states['loadable']} planning state(s) no longer load")
                if states["trip_id_mismatch"]:
                    problems.append(f"{states['trip_id_mismatch']} planning state(s) name a different trip than their row")
    except OperatorError:
        raise
    except Exception:
        raise OperatorError("Could not read the database being verified.") from None
    finally:
        engine.dispose()

    if problems:
        _say(f"VERIFICATION FAILED: {len(problems)} problem(s).")
        for problem in problems:
            _say(f"  - {problem}")
        return False
    _say("VERIFICATION PASSED.")
    return True


# -- command line ------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up, restore into an empty database, or verify a PostgreSQL database.")
    commands = parser.add_subparsers(dest="command", required=True)
    backup_parser = commands.add_parser("backup", help=f"pg_dump the database named by {BACKUP_SOURCE_ENV}")
    backup_parser.add_argument("--out", required=True, type=Path, help="directory OUTSIDE the repository")
    restore_parser = commands.add_parser("restore", help=f"pg_restore into the EMPTY database named by {RESTORE_TARGET_ENV}")
    restore_parser.add_argument("--dump", required=True, type=Path)
    restore_parser.add_argument(
        "--confirm-empty-target",
        required=True,
        metavar="DATABASE_NAME",
        help="the name of the new, empty, isolated database you are restoring into; it must be the database the target server reports",
    )
    verify_parser = commands.add_parser("verify", help=f"check the database named by {VERIFY_ENV}")
    verify_parser.add_argument("--manifest", type=Path, help="the backup's manifest, to compare revision, row counts and content")
    arguments = parser.parse_args(argv)

    # The application's settings must never pull a connection or a key from the project's .env here.
    os.environ.setdefault("TRAVELOB_TEST_MODE", "1")
    try:
        if arguments.command == "backup":
            backup(arguments.out)
        elif arguments.command == "restore":
            restore(arguments.dump, arguments.confirm_empty_target)
        else:
            return 0 if verify(arguments.manifest) else 1
    except OperatorError as error:
        print(f"REFUSED / FAILED: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        # Anything unforeseen: its type only. A driver's or a library's message can carry a host name or a
        # fragment of a connection string, and a traceback is not shown for the same reason.
        print(f"REFUSED / FAILED: unexpected error ({type(error).__name__}). Nothing further was done.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
