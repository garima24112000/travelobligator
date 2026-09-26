from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings

# Section 200C: the migration against a REAL database, on a THROWAWAY database created and dropped
# here: 200A head -> (legacy data incl. duplicate active jobs) -> 200C head -> downgrade -> re-upgrade.
pytestmark = [
    pytest.mark.postgres_integration,
    pytest.mark.skipif(
        os.environ.get("TRAVELOB_RUN_POSTGRES_TESTS") != "1" or not os.environ.get("DATABASE_URL"),
        reason="Optional live-Postgres test: set TRAVELOB_RUN_POSTGRES_TESTS=1 and a real DATABASE_URL.",
    ),
]

PREVIOUS_HEAD = "cc7238a1bca3"
NEW_HEAD = "d41a7e2c9b53"


def test_200a_database_upgrades_to_200c_with_legacy_data_and_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    disabled = {n: lg.disabled for n, lg in logging.root.manager.loggerDict.items() if isinstance(lg, logging.Logger)}
    levels = {n: lg.level for n, lg in logging.root.manager.loggerDict.items() if isinstance(lg, logging.Logger)}
    root_handlers, root_level = list(logging.root.handlers), logging.root.level

    admin_url = make_url(os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1))
    scratch = f"tobl_200c_scratch_{uuid.uuid4().hex[:10]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{scratch}"'))
    scratch_url = admin_url.set(database=scratch).render_as_string(hide_password=False)
    engine = None
    try:
        monkeypatch.setenv("DATABASE_URL", scratch_url)
        get_settings.cache_clear()
        config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
        assert ScriptDirectory.from_config(config).get_heads() == [NEW_HEAD]  # exactly one head

        command.upgrade(config, PREVIOUS_HEAD)  # a genuine 200A database
        engine = create_engine(scratch_url)
        with engine.begin() as c:
            c.execute(text("insert into users(user_id,email,password_hash,created_at,updated_at) values ('u1','a@b.c','x',now(),now())"))
            c.execute(text("insert into trips(trip_id,status,owner_id,created_at,updated_at) values ('t1','draft','u1',now(),now()),('t2','draft','u1',now(),now())"))
            c.execute(text("insert into planning_states(trip_id,planning_state_id,current_version,pipeline_status,state,created_at,updated_at) values ('t1','ps1','v1','draft','{}',now(),now())"))
            c.execute(
                text(
                    "insert into generation_jobs(job_id,trip_id,owner_id,job_type,status,created_at) values "
                    "('j_old','t1','u1','generate','running',now()-interval '2 hours'),"
                    "('j_dup','t1','u1','generate','queued',now()),"
                    "('j_other','t2','u1','generate','running',now())"
                )
            )
        assert "lock_version" not in {c["name"] for c in inspect(engine).get_columns("planning_states")}

        command.upgrade(config, "head")  # the 200C migration on top of legacy data

        with engine.begin() as c:
            assert c.execute(text("select lock_version from planning_states where trip_id='t1'")).scalar_one() == 0
            rows = dict(c.execute(text("select job_id, status from generation_jobs")).all())
            assert rows == {"j_old": "running", "j_dup": "failed", "j_other": "running"}  # oldest kept, duplicate closed
            assert c.execute(text("select error_code from generation_jobs where job_id='j_dup'")).scalar_one() == "JOB_INTERRUPTED"
            assert c.execute(text("select lease_owner, lease_expires_at, heartbeat_at from generation_jobs where job_id='j_old'")).one() == (None, None, None)
        columns = {c["name"] for c in inspect(engine).get_columns("generation_jobs")}
        assert {"lease_owner", "lease_expires_at", "heartbeat_at"} <= columns
        with pytest.raises(IntegrityError):  # the database now refuses a second active job for a trip
            with engine.begin() as c:
                c.execute(text("insert into generation_jobs(job_id,trip_id,owner_id,job_type,status,created_at) values ('j_new','t1','u1','generate','queued',now())"))

        # the application works on the upgraded schema
        from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
        from app.db.session import get_session_factory
        from app.repositories.postgres_planning_state_repository import PostgresPlanningStateRepository

        get_settings.cache_clear()
        repo = PostgresPlanningStateRepository(session_factory=get_session_factory(engine=engine))
        fresh = PlanningState(
            trip_id="t2",
            trip_request=TripRequest(primary_destination="X", start_date="2026-10-10", end_date="2026-10-11", travelers_count=1, travel_group_type=TravelGroupType.SOLO),
        )
        repo.save(fresh)
        loaded = repo.get_by_trip_id("t2")
        assert loaded is not None and loaded._lock_version == 0

        command.downgrade(config, PREVIOUS_HEAD)
        assert "lock_version" not in {c["name"] for c in inspect(engine).get_columns("planning_states")}
        assert "lease_owner" not in {c["name"] for c in inspect(engine).get_columns("generation_jobs")}
        command.upgrade(config, "head")  # re-upgrade
        with engine.begin() as c:
            assert c.execute(text("select version_num from alembic_version")).scalar_one() == NEW_HEAD
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        admin.dispose()
        get_settings.cache_clear()
        logging.root.handlers[:] = root_handlers
        logging.root.setLevel(root_level)
        for name, lg in list(logging.root.manager.loggerDict.items()):
            if isinstance(lg, logging.Logger):
                lg.disabled = disabled.get(name, False)
                lg.setLevel(levels.get(name, logging.NOTSET))
