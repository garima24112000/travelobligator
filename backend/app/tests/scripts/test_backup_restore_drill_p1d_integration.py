from __future__ import annotations

import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.persistence import PersistenceUnavailableError, check_postgres_ready
from app.main import app
from app.tests.conftest import create_trip_payload
from scripts import db_backup_restore as tool

# P1-D: the backup / restore / rollback procedures of docs/25_operations_runbook.md, executed for real on
# THROWAWAY databases that this file creates and drops on the test PostgreSQL server. The data is written by
# the application itself (sign up, create a trip, generate its itinerary), then copied into a database of
# its own, so nothing else in the shared test database takes part.
#
# Needs the PostgreSQL client tools (`pg_dump`, `pg_restore`) at least as new as the server.
pytestmark = [
    pytest.mark.postgres_integration,
    pytest.mark.skipif(
        os.environ.get("TRAVELOB_RUN_POSTGRES_TESTS") != "1" or not os.environ.get("DATABASE_URL"),
        reason="Optional live-Postgres test: set TRAVELOB_RUN_POSTGRES_TESTS=1 and a real DATABASE_URL.",
    ),
]

_BACKEND = Path(__file__).resolve().parents[3]
# The revision production runs today (docs/23_v1_release_reference.md); the two migrations after it are the
# ones the next deployment applies.
PRODUCTION_REVISION = "d41a7e2c9b53"
PENDING_TABLES = {"auth_attempts", "deleted_job_usage"}
PASSWORD = "drill-password-123"
# Rows are copied parent first; a trip's own tables are selected by trip.
_TRIP_TABLES = ("planning_states", "generation_jobs", "itinerary_branches", "itinerary_revisions")


def _admin_url():
    return make_url(os.environ["DATABASE_URL"]).set(drivername="postgresql+psycopg")


@contextmanager
def _scratch_database(label: str) -> Iterator[str]:
    """A new, empty database on the test server, dropped afterwards whatever happens."""
    name = f"tobl_p1d_{label}_{uuid.uuid4().hex[:10]}"
    admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield _admin_url().set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _alembic(database_url: str, *arguments: str) -> None:
    """The real Alembic command line, as an operator runs it, in its own process."""
    environment = {**os.environ, "DATABASE_URL": database_url, "PERSISTENCE_BACKEND": "postgres", "TRAVELOB_TEST_MODE": "1"}
    completed = subprocess.run([sys.executable, "-m", "alembic", *arguments], cwd=_BACKEND, env=environment, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr[-2000:]


def _application_written_trips(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Two accounts, each with one generated itinerary, written through the API into the shared database."""
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    get_settings.cache_clear()
    accounts = []
    for _ in range(2):
        client = TestClient(app)
        email = f"p1d-drill-{uuid.uuid4().hex}@example.com"
        signup = client.post("/auth/signup", json={"email": email, "password": PASSWORD})
        assert signup.status_code == 201, signup.text
        trip_id = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
        assert client.post(f"/trips/{trip_id}/generate").status_code == 200
        accounts.append({"email": email, "user_id": signup.json()["data"]["user"]["user_id"], "trip_id": trip_id})
    return {"accounts": accounts}


def _copy_application_rows(target_url: str, accounts: list[dict]) -> None:
    """Copy exactly these accounts' rows, column for column, from the shared database into `target_url`,
    and add one ownerless trip (a row from before accounts existed)."""
    source = create_engine(_admin_url())
    target = create_engine(target_url)
    user_ids = [account["user_id"] for account in accounts]
    trip_ids = [account["trip_id"] for account in accounts]
    try:
        metadata = MetaData()
        metadata.reflect(bind=target)  # the target's own columns: it may be at an older revision
        with source.connect() as read, target.begin() as write:
            # one transaction; the cross-references between branches and revisions are checked at its end
            write.execute(text("SET session_replication_role = replica"))
            selections = [("users", "user_id", user_ids), ("trips", "trip_id", trip_ids)]
            selections += [(table, "trip_id", trip_ids) for table in _TRIP_TABLES]
            for table_name, key, values in selections:
                table = metadata.tables[table_name]
                columns = ", ".join(f'"{column.name}"' for column in table.columns)
                rows = read.execute(text(f'select {columns} from "{table_name}" where "{key}" = any(:values)'), {"values": values}).mappings().all()
                assert rows or table_name not in {"users", "trips", "planning_states"}, table_name
                if rows:
                    write.execute(table.insert(), [dict(row) for row in rows])
            # The synchronous test path creates no job row, so each trip gets one finished job of its owner.
            for account in accounts:
                write.execute(
                    text(
                        "insert into generation_jobs(job_id, trip_id, owner_id, job_type, status, created_at, finished_at) "
                        "values (:job, :trip, :owner, 'generate', 'succeeded', now(), now())"
                    ),
                    {"job": f"job_p1d_{uuid.uuid4().hex}", "trip": account["trip_id"], "owner": account["user_id"]},
                )
            write.execute(text("insert into trips(trip_id, status, owner_id, created_at, updated_at) values ('trip_p1d_ownerless', 'draft', null, now(), now())"))
            write.execute(text("SET session_replication_role = DEFAULT"))
    finally:
        source.dispose()
        target.dispose()


def _stored_state(database_url: str, trip_id: str):
    from app.repositories.postgres_planning_state_repository import PostgresPlanningStateRepository

    engine = create_engine(database_url)
    try:
        return PostgresPlanningStateRepository(session_factory=sessionmaker(bind=engine)).get_by_trip_id(trip_id)
    finally:
        engine.dispose()


def _one(database_url: str, query: str, **parameters):
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(text(query), parameters).scalar()
    finally:
        engine.dispose()


def _describe(database_url: str) -> dict:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return tool.describe(connection)
    finally:
        engine.dispose()


def _settings_for(database_url: str) -> Settings:
    # by variable name: a setting passed by field name loses to the same variable in the environment
    settings = Settings(_env_file=None, DATABASE_URL=database_url, PERSISTENCE_BACKEND="postgres")
    assert settings.database_url == database_url
    return settings


def test_a_backup_restores_into_an_empty_database_with_ownership_and_itineraries_intact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    accounts = _application_written_trips(monkeypatch)["accounts"]
    backups = tmp_path / "backups"
    with _scratch_database("source") as source_url, _scratch_database("restored") as restored_url:
        _alembic(source_url, "upgrade", "head")
        _copy_application_rows(source_url, accounts)
        originals = {account["trip_id"]: _stored_state(source_url, account["trip_id"]) for account in accounts}
        assert all(state is not None and state.experience_plan is not None for state in originals.values())

        # -- back up ---------------------------------------------------------------------------------
        monkeypatch.setenv(tool.BACKUP_SOURCE_ENV, source_url)
        manifest_path = tool.backup(backups)
        dump_path = manifest_path.with_name(manifest_path.name.removesuffix(".manifest.json") + ".dump")
        assert dump_path.is_file() and dump_path.stat().st_size > 0
        # private files in a private directory
        assert oct(dump_path.stat().st_mode & 0o777) == "0o600"
        assert oct(manifest_path.stat().st_mode & 0o777) == "0o600"
        assert oct(backups.stat().st_mode & 0o777) == "0o700"
        manifest_text = manifest_path.read_text()
        for account in accounts:  # the manifest holds counts and digests, nothing of the content
            assert account["email"] not in manifest_text and account["trip_id"] not in manifest_text

        # -- a restore is refused anywhere but a new, empty, separate database ------------------------
        source_name, restored_name = make_url(source_url).database, make_url(restored_url).database
        shared_url, shared_name = os.environ["DATABASE_URL"], make_url(os.environ["DATABASE_URL"]).database
        source_before = _describe(source_url)

        # onto the backup source, written exactly the same
        monkeypatch.setenv(tool.RESTORE_TARGET_ENV, source_url)
        with pytest.raises(tool.OperatorError, match="points at the same database as BACKUP_SOURCE_DATABASE_URL"):
            tool.restore(dump_path, source_name)
        # onto the backup source under ANOTHER NAME: the text differs, the servers say it is one database
        with monkeypatch.context() as patched:
            spellings = iter([("a-name.example.test", 5432, source_name), ("another-name.example.test", 5432, source_name)])
            patched.setattr(tool, "_written_address", lambda env_name: next(spellings))
            with pytest.raises(tool.OperatorError, match="reaches the same database as BACKUP_SOURCE_DATABASE_URL .* under another name"):
                tool.restore(dump_path, source_name)
        # the backup source is named but cannot be asked who it is: uncertainty is a refusal
        monkeypatch.setenv(tool.RESTORE_TARGET_ENV, restored_url)
        monkeypatch.setenv(tool.BACKUP_SOURCE_ENV, _admin_url().set(port=1, database="unreachable").render_as_string(hide_password=False))
        with pytest.raises(tool.OperatorError, match="Could not reach the backup source"):
            tool.restore(dump_path, restored_name)
        monkeypatch.setenv(tool.BACKUP_SOURCE_ENV, source_url)
        # onto the application's own database (DATABASE_URL): refused as such
        monkeypatch.setenv(tool.RESTORE_TARGET_ENV, shared_url)
        with pytest.raises(tool.OperatorError, match="points at the same database as DATABASE_URL"):
            tool.restore(dump_path, shared_name)
        # a database in use that the tool was told nothing about: refused because it is not empty
        monkeypatch.delenv(tool.BACKUP_SOURCE_ENV)
        monkeypatch.delenv("DATABASE_URL")
        monkeypatch.setenv(tool.RESTORE_TARGET_ENV, source_url)
        with pytest.raises(tool.OperatorError, match="not empty"):
            tool.restore(dump_path, source_name)
        monkeypatch.setenv("DATABASE_URL", shared_url)
        monkeypatch.setenv(tool.BACKUP_SOURCE_ENV, source_url)
        # the operator names a different database than the one the URL really reaches
        monkeypatch.setenv(tool.RESTORE_TARGET_ENV, restored_url)
        with pytest.raises(tool.OperatorError, match="not the one the target server reports"):
            tool.restore(dump_path, source_name)
        # a target that is empty in `public` but holds something elsewhere is not empty
        with _scratch_database("other_schema") as elsewhere_url:
            engine = create_engine(elsewhere_url)
            with engine.begin() as connection:
                connection.execute(text("create schema kept"))
                connection.execute(text("create table kept.thing (id int)"))
            engine.dispose()
            monkeypatch.setenv(tool.RESTORE_TARGET_ENV, elsewhere_url)
            with pytest.raises(tool.OperatorError, match="not empty"):
                tool.restore(dump_path, make_url(elsewhere_url).database)
        # a dump without its manifest cannot be checked, so it is not restored
        orphan = tmp_path / "orphan" / dump_path.name
        orphan.parent.mkdir()
        orphan.write_bytes(dump_path.read_bytes())
        monkeypatch.setenv(tool.RESTORE_TARGET_ENV, restored_url)
        with pytest.raises(tool.OperatorError, match="no manifest"):
            tool.restore(orphan, restored_name)
        # none of those refusals wrote anything anywhere
        assert _describe(source_url) == source_before
        assert _one(restored_url, "select count(*) from pg_tables where schemaname = 'public'") == 0

        # -- a restore that fails part-way leaves the target empty (one transaction) ------------------
        with monkeypatch.context() as patched:
            real_run = tool._run_tool

            def truncated(tool_name, arguments, env_name):
                if tool_name == "pg_restore" and "--list" not in arguments:
                    # the same restore, but of an archive cut off in the middle of its data
                    cut = tmp_path / "cut.dump"
                    cut.write_bytes(dump_path.read_bytes()[: dump_path.stat().st_size * 2 // 3])
                    arguments = [*arguments[:-1], str(cut)]
                return real_run(tool_name, arguments, env_name)

            patched.setattr(tool, "_run_tool", truncated)
            with pytest.raises(tool.OperatorError, match="pg_restore failed"):
                tool.restore(dump_path, restored_name)
        assert _one(restored_url, "select count(*) from pg_tables where schemaname = 'public'") == 0

        # -- restore; it verifies itself against the manifest before it reports success ---------------
        shown = [capsys.readouterr()]
        tool.restore(dump_path, restored_name)
        shown.append(capsys.readouterr())
        assert "VERIFICATION PASSED." in shown[-1].out
        monkeypatch.setenv(tool.VERIFY_ENV, restored_url)
        assert tool.verify(manifest_path) is True
        assert tool.verify() is True  # and by itself: at this code's head, structure matches the models

        # migration revision and table structure
        assert _describe(restored_url) == _describe(source_url)
        check_postgres_ready(_settings_for(restored_url))  # the application's own startup check accepts it

        # trip ownership: each trip still belongs to its own account, and only to it
        for account in accounts:
            assert _one(restored_url, "select owner_id from trips where trip_id = :trip", trip=account["trip_id"]) == account["user_id"]
            assert _one(restored_url, "select count(*) from trips where owner_id = :owner", owner=account["user_id"]) == 1
        assert _one(restored_url, "select owner_id from trips where trip_id = 'trip_p1d_ownerless'") is None

        # an existing itinerary: the restored state is the same document, read by the application's own repository
        for account in accounts:
            restored = _stored_state(restored_url, account["trip_id"])
            assert restored is not None
            assert restored.model_dump(mode="json") == originals[account["trip_id"]].model_dump(mode="json")
            assert len(restored.experience_plan.daily_plans) == len(originals[account["trip_id"]].experience_plan.daily_plans) > 0

        # the account can still sign in: the stored password hash still verifies
        from app.auth.passwords import verify_password

        stored_hash = _one(restored_url, "select password_hash from users where user_id = :user", user=accounts[0]["user_id"])
        assert verify_password(PASSWORD, stored_hash) and not verify_password("not-the-password", stored_hash)

        # -- a second restore into the now populated copy is refused; nothing is overwritten ---------
        with pytest.raises(tool.OperatorError, match="not empty"):
            tool.restore(dump_path, restored_name)

        # -- a damaged dump is refused before anything is touched ------------------------------------
        damaged = tmp_path / "damaged" / dump_path.name
        damaged.parent.mkdir()
        damaged.write_bytes(dump_path.read_bytes()[:-64] + b"\0" * 64)
        (damaged.parent / manifest_path.name).write_text(manifest_text)
        with pytest.raises(tool.OperatorError, match="checksum"):
            tool.restore(damaged, restored_name)

        # -- a restored copy that is not the backup is REJECTED, not reported as a success -----------
        with _scratch_database("rejected") as rejected_url, monkeypatch.context() as patched:
            real_verify = tool.verify

            def verify_after_damage(manifest=None, *, env_name=tool.VERIFY_ENV):
                engine = create_engine(rejected_url)
                with engine.begin() as connection:  # the copy loses a row between restore and acceptance
                    connection.execute(text("delete from trips where trip_id = 'trip_p1d_ownerless'"))
                engine.dispose()
                return real_verify(manifest, env_name=env_name)

            patched.setattr(tool, "verify", verify_after_damage)
            patched.setenv(tool.RESTORE_TARGET_ENV, rejected_url)
            with pytest.raises(tool.OperatorError, match="REJECTED"):
                tool.restore(dump_path, make_url(rejected_url).database)

        # -- verify notices a copy that lost or changed data -----------------------------------------
        engine = create_engine(restored_url)
        with engine.begin() as connection:
            connection.execute(text("update planning_states set pipeline_status = 'draft' where trip_id = :trip"), {"trip": accounts[0]["trip_id"]})
        assert tool.verify(manifest_path) is False  # same row count, different content
        with engine.begin() as connection:
            connection.execute(text("delete from trips where trip_id = :trip"), {"trip": accounts[1]["trip_id"]})
        engine.dispose()
        assert tool.verify(manifest_path) is False

    # nothing the tool printed carries a connection string, an address, a trip or a user
    shown.append(capsys.readouterr())
    everything = "".join(part.out + part.err for part in shown)
    assert "VERIFICATION FAILED" in everything and "Checksum matches the manifest." in everything  # it did speak
    admin = _admin_url()
    private = ["postgresql", "://", "@", *(account["email"] for account in accounts), *(account["trip_id"] for account in accounts), *(account["user_id"] for account in accounts)]
    # the server's password (CI has one), and every scratch database's connection target
    private += [value for value in (str(admin.password or ""), f"{admin.host}:{admin.port}") if len(value) >= 3]
    for secret_like in private:
        assert secret_like not in everything


def test_a_backup_taken_before_the_pending_migrations_restores_migrates_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The next deployment, on throwaway databases: back up at production's revision, restore, apply the
    pending migrations; then the rollback of the runbook -- downgrade to production's revision -- and forward
    again. The pending migrations only ADD tables and indexes, so no existing row may change either way."""
    accounts = _application_written_trips(monkeypatch)["accounts"]
    with _scratch_database("production_like") as old_url, _scratch_database("restored_old") as restored_url:
        _alembic(old_url, "upgrade", PRODUCTION_REVISION)
        _copy_application_rows(old_url, accounts)
        before = _describe(old_url)
        assert before["alembic_revisions"] == [PRODUCTION_REVISION]
        assert not PENDING_TABLES & set(before["tables"])

        monkeypatch.setenv(tool.BACKUP_SOURCE_ENV, old_url)
        manifest_path = tool.backup(tmp_path / "backups")
        dump_path = manifest_path.with_name(manifest_path.name.removesuffix(".manifest.json") + ".dump")
        monkeypatch.setenv(tool.RESTORE_TARGET_ENV, restored_url)
        tool.restore(dump_path, make_url(restored_url).database)  # accepted against the manifest, at the OLD revision
        monkeypatch.setenv(tool.VERIFY_ENV, restored_url)

        # the restored copy is the backup, at production's revision: valid against its manifest...
        assert tool.verify(manifest_path) is True
        # ...and, exactly as the runbook says, NOT yet a database this code will start against
        assert tool.verify() is False
        with pytest.raises(PersistenceUnavailableError):
            check_postgres_ready(_settings_for(restored_url))

        # -- apply the pending migrations, in order ---------------------------------------------------
        _alembic(restored_url, "upgrade", "head")
        assert tool.verify() is True
        check_postgres_ready(_settings_for(restored_url))
        migrated = _describe(restored_url)
        assert migrated["alembic_revisions"] == tool.code_heads()
        assert PENDING_TABLES <= set(migrated["tables"])
        # every table that existed before holds exactly the rows it held
        assert {table: migrated["tables"][table] for table in before["tables"]} == before["tables"]
        for account in accounts:
            assert _stored_state(restored_url, account["trip_id"]).model_dump(mode="json") == _stored_state(old_url, account["trip_id"]).model_dump(mode="json")

        # rows the new code writes into the new tables (what a rollback gives up)
        engine = create_engine(restored_url)
        with engine.begin() as connection:
            connection.execute(text("insert into auth_attempts(attempt_id, kind, subject_hash, ip_hash, created_at) values ('a1', 'signup', null, null, now())"))
            connection.execute(text("insert into deleted_job_usage(usage_id, owner_id, created_at) values ('u1', null, now())"))
        engine.dispose()

        # -- roll back: an explicit downgrade to production's revision --------------------------------
        _alembic(restored_url, "downgrade", PRODUCTION_REVISION)
        rolled_back = _describe(restored_url)
        assert rolled_back["alembic_revisions"] == [PRODUCTION_REVISION]
        assert rolled_back["tables"] == before["tables"]  # the two new tables are gone; nothing else changed
        with pytest.raises(PersistenceUnavailableError):  # this (newer) code refuses the older schema
            check_postgres_ready(_settings_for(restored_url))

        # -- and forward again -----------------------------------------------------------------------
        _alembic(restored_url, "upgrade", "head")
        assert tool.verify() is True
        again = _describe(restored_url)
        assert {table: again["tables"][table] for table in before["tables"]} == before["tables"]
        assert all(again["tables"][table]["rows"] == 0 for table in PENDING_TABLES)  # the counters start empty


def test_the_operations_report_reads_alert_conditions_from_a_real_database() -> None:
    """`ops_report.collect` against real tables: what it counts, what it leaves out, and that it also
    works (and says so) on a database that is behind this code."""
    from datetime import datetime, timezone

    from scripts import ops_report

    limits = ops_report.Limits(
        generation_global_per_window=10, generation_per_user_per_window=3, generation_window_seconds=86400,
        signup_global_per_window=30, signup_window_seconds=86400, geoapify_daily_credits=3000,
    )
    with _scratch_database("report") as url, _scratch_database("report_old") as old_url:
        _alembic(url, "upgrade", "head")
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(text("insert into users(user_id, email, password_hash, created_at, updated_at) values ('u1', 'one@example.com', 'x', now(), now()), ('u2', 'two@example.com', 'x', now(), now())"))
            connection.execute(text("insert into trips(trip_id, status, owner_id, created_at, updated_at) select 't' || n, 'draft', case when n <= 5 then 'u1' else 'u2' end, now(), now() from generate_series(1, 7) n"))
            connection.execute(
                text(
                    "insert into generation_jobs(job_id, trip_id, owner_id, job_type, status, error_code, created_at, lease_expires_at) values "
                    "('j1', 't1', 'u1', 'generate', 'succeeded', null, now() - interval '1 hour', null),"
                    "('j2', 't2', 'u1', 'generate', 'failed', 'STAGE_FAILED', now() - interval '2 hours', null),"
                    "('j3', 't3', 'u1', 'generate', 'failed', 'JOB_INTERRUPTED', now() - interval '3 hours', null),"
                    # abandoned: still 'running', its lease ran out an hour ago
                    "('j4', 't4', 'u1', 'generate', 'running', null, now() - interval '90 minutes', now() - interval '1 hour'),"
                    # healthy: running under a live lease
                    "('j5', 't6', 'u2', 'generate', 'running', null, now() - interval '1 minute', now() + interval '2 minutes'),"
                    # outside the window: counts for nothing
                    "('j6', 't5', 'u1', 'generate', 'failed', 'STAGE_FAILED', now() - interval '3 days', null)"
                )
            )
            connection.execute(text("insert into deleted_job_usage(usage_id, owner_id, created_at) values ('d1', 'u1', now() - interval '5 hours'), ('d2', null, now() - interval '6 hours'), ('d3', null, now() - interval '4 days')"))
            connection.execute(text("insert into auth_attempts(attempt_id, kind, subject_hash, ip_hash, created_at) values ('a1', 'signup', null, null, now()), ('a2', 'signup', null, null, now()), ('a3', 'login_failure', 'h', null, now()), ('a4', 'signup', null, null, now() - interval '2 days')"))
            connection.execute(
                text(
                    "insert into planning_states(trip_id, planning_state_id, current_version, pipeline_status, state, created_at, updated_at) values "
                    "('t1', 'p1', 'v1', 'needs_review', cast(:spent as jsonb), now(), now()),"
                    "('t2', 'p2', 'v1', 'blocked', cast(:capped as jsonb), now(), now()),"
                    "('t3', 'p3', 'v1', 'blocked', cast(:none as jsonb), now(), now()),"
                    "('t5', 'p5', 'v1', 'blocked', cast(:spent as jsonb), now() - interval '3 days', now() - interval '3 days')"
                ),
                {
                    "spent": '{"provider_usage_report": {"credits_used": 1500, "refused_calls": 0, "budget": 100}}',
                    "capped": '{"provider_usage_report": {"credits_used": 1000, "refused_calls": 2, "budget": 100}}',
                    "none": '{"provider_usage_report": null}',
                },
            )
        try:
            with engine.connect() as connection:
                snapshot = ops_report.collect(connection, limits, datetime.now(timezone.utc))
        finally:
            engine.dispose()

        assert snapshot.database_revisions == snapshot.code_revisions == tool.code_heads()
        assert (snapshot.users, snapshot.trips) == (2, 7) and snapshot.database_bytes > 0
        assert snapshot.jobs_by_status == {"succeeded": 1, "failed": 2, "running": 2}
        assert snapshot.failures_by_code == {"STAGE_FAILED": 1, "JOB_INTERRUPTED": 1}
        assert snapshot.jobs_admitted == 5 + 2  # five jobs in the window, plus two of since-deleted trips
        assert snapshot.jobs_stuck == 1  # the abandoned one, not the one under a live lease
        assert snapshot.users_at_generation_limit == 1  # u1 has four jobs in the window, the limit is three
        assert (snapshot.signups, snapshot.failed_sign_ins) == (2, 1)
        assert (snapshot.plans_updated, snapshot.plans_blocked) == (3, 2)
        assert snapshot.credits_used == 2500 and snapshot.generations_refused_a_provider_call == 1

        findings = {finding.code: finding.severity for finding in ops_report.evaluate(snapshot, limits)}
        assert findings == {
            "jobs_stuck": "critical",
            "generation_failures": "note",
            "generation_interrupted": "note",
            "generation_user_limit_reached": "note",
            "provider_credits_pressure": "warning",
            "provider_credit_cap_hit": "warning",
            "plans_blocked": "note",
        }
        rendered = ops_report.render(snapshot, limits, ops_report.evaluate(snapshot, limits))
        for private in ("one@example.com", "two@example.com", "u1", "t4", "j4", "postgresql"):
            assert private not in rendered  # counts and codes only

        # a database that is behind this code: the report still runs, and says so first
        _alembic(old_url, "upgrade", PRODUCTION_REVISION)
        engine = create_engine(old_url)
        try:
            with engine.connect() as connection:
                behind = ops_report.collect(connection, limits, datetime.now(timezone.utc))
        finally:
            engine.dispose()
        assert behind.database_revisions == [PRODUCTION_REVISION] and behind.signups == 0  # no such table yet
        assert [finding.code for finding in ops_report.evaluate(behind, limits)] == ["schema_mismatch"]
        assert ops_report.exit_code(ops_report.evaluate(behind, limits)) == 2
