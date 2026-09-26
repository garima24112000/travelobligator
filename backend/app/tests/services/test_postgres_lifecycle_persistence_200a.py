from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.persistence import PersistenceUnavailableError, check_postgres_ready
from app.main import app
from app.models.targeted_regeneration_runtime import TargetedRegenerationRuntimeStatus
from app.repositories import factory as factory_module
from app.tests.conftest import create_trip_payload
from test_targeted_regeneration_application_service import (  # type: ignore[import-not-found]
    _completed_execution,
    _completed_interpretation,
    _FakeExecutor,
    _FakeInterpreter,
    _FakePlanBuilder,
    _pending_event,
    _planning_state,
    _ready_removal_plan,
    _service,
)

# Section 200A: real-PostgreSQL persistence lifecycle + RESTART verification.
# Gated exactly like every other live-Postgres test in this repo
# (TRAVELOB_RUN_POSTGRES_TESTS=1 + a real, alembic-upgraded DATABASE_URL); the
# `postgres_integration` marker makes conftest select PERSISTENCE_BACKEND=postgres
# explicitly. Fully offline/deterministic: the places provider is the test
# double, no Groq or live provider is called.
#
#   docker run -d --rm --name pg200a -e POSTGRES_DB=... -e POSTGRES_USER=... \
#       -e POSTGRES_PASSWORD=... -p 127.0.0.1:55432:5432 postgres:16
#   (cd backend && DATABASE_URL=... alembic upgrade head)
#   TRAVELOB_RUN_POSTGRES_TESTS=1 DATABASE_URL=... python -m pytest -m postgres_integration
pytestmark = [
    pytest.mark.postgres_integration,
    pytest.mark.skipif(
        os.environ.get("TRAVELOB_RUN_POSTGRES_TESTS") != "1" or not os.environ.get("DATABASE_URL"),
        reason="Optional live-Postgres test: set TRAVELOB_RUN_POSTGRES_TESTS=1 and a real DATABASE_URL.",
    ),
]


def restart_application_context() -> None:
    """Simulate a process restart: drop every cached settings/engine/repository
    (disposing pooled connections) so the next access builds everything fresh
    from the database -- nothing survives in memory."""
    from app.db.session import get_engine

    try:
        get_engine().dispose()
    except Exception:
        pass
    get_settings.cache_clear()
    get_engine.cache_clear()
    for cache in (
        factory_module._postgres_trip_repository,
        factory_module._postgres_planning_state_repository,
        factory_module._postgres_user_repository,
        factory_module._postgres_job_repository,
        factory_module._postgres_lineage_repository,
    ):
        cache.cache_clear()


@pytest.fixture()
def local_state_file(tmp_path: Path) -> Path:
    """The conftest re-points every Local JSON singleton at this temp file; in
    Postgres mode it must never be created."""
    return tmp_path / "test_travelobligator_state.json"


def _signup(client: TestClient) -> str:
    email = f"test-200a-{uuid.uuid4().hex}@example.com"
    response = client.post("/auth/signup", json={"email": email, "password": "testpassword123"})
    assert response.status_code == 201, response.text
    return email


def _trip(client: TestClient) -> str:
    response = client.post("/trips", json=create_trip_payload())
    assert response.status_code == 201, response.text
    trip_id = response.json()["data"]["trip_id"]
    assert client.post(f"/trips/{trip_id}/generate").status_code == 200
    return trip_id


def _state(client: TestClient, trip_id: str) -> dict:
    response = client.get(f"/trips/{trip_id}")
    assert response.status_code == 200, response.text
    return response.json()["data"]["planning_state"]


def _branches(client: TestClient, trip_id: str) -> list[dict]:
    return client.get(f"/trips/{trip_id}/branches").json()["data"]["branches"]


def _plan_ids(state: dict) -> list[list[str]]:
    return [[e["experience_id"] for e in d["experiences"]] for d in state["experience_plan"]["daily_plans"]]


# -- full lifecycle across a restart -----------------------------------------------------------


def test_user_trip_state_revision_feedback_survive_a_restart(local_state_file: Path) -> None:
    client = TestClient(app)
    email = _signup(client)
    trip_id = _trip(client)
    before = _state(client, trip_id)
    branches_before = _branches(client, trip_id)
    assert client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Make this less packed"}).status_code == 200
    with_feedback = _state(client, trip_id)

    restart_application_context()
    client = TestClient(app)  # a brand-new client/session context
    login = client.post("/auth/login", json={"email": email, "password": "testpassword123"})
    assert login.status_code == 200  # the user survived the restart
    me = client.get("/auth/me")
    assert me.status_code == 200 and me.json()["data"]["user"]["email"] == email

    after = _state(client, trip_id)
    assert after["trip_id"] == trip_id  # trip + PlanningState exist
    assert after["metadata"]["current_version"] == before["metadata"]["current_version"] == "v1"
    assert _plan_ids(after) == _plan_ids(before)  # the generated plan is unchanged
    assert [f["feedback_event_id"] for f in after["feedback_history"]] == [
        f["feedback_event_id"] for f in with_feedback["feedback_history"]
    ]
    assert after["pending_feedback_summary"]["total_feedback_items"] == 1

    branches_after = _branches(client, trip_id)
    assert [b["branch_id"] for b in branches_after] == [b["branch_id"] for b in branches_before]
    main = next(b for b in branches_after if b["is_default"])
    revisions = client.get(f"/trips/{trip_id}/branches/{main['branch_id']}/revisions").json()["data"]["revisions"]
    assert [r["version_label"] for r in revisions] == ["v1"]
    assert main["head_revision_id"] == revisions[0]["revision_id"]
    assert not local_state_file.exists()  # no Local JSON artifact in Postgres mode


def test_branch_state_restart_verification(local_state_file: Path) -> None:
    client = TestClient(app)
    email = _signup(client)
    trip_id = _trip(client)
    main = next(b for b in _branches(client, trip_id) if b["is_default"])
    source_revision_id = main["head_revision_id"]
    source_before = client.get(f"/trips/{trip_id}/revisions/{source_revision_id}").json()["data"]
    fork = client.post(
        f"/trips/{trip_id}/branches",
        json={"source_revision_id": source_revision_id, "display_name": "Option B", "activate_after_create": True},
    )
    assert fork.status_code == 201, fork.text
    option_b = fork.json()["data"]["branch"]

    restart_application_context()
    client = TestClient(app)
    assert client.post("/auth/login", json={"email": email, "password": "testpassword123"}).status_code == 200

    branches = {b["display_name"]: b for b in _branches(client, trip_id)}
    assert set(branches) == {"Main", "Option B"}
    assert branches["Option B"]["branch_id"] == option_b["branch_id"]
    assert branches["Option B"]["base_revision_id"] == source_revision_id  # fork lineage persisted
    assert branches["Option B"]["is_active"] and not branches["Main"]["is_active"]  # active branch preserved
    assert branches["Main"]["head_revision_id"] == source_revision_id  # Main head unchanged
    source_after = client.get(f"/trips/{trip_id}/revisions/{source_revision_id}").json()["data"]
    assert source_after == source_before  # source revision immutable
    # switch back to Main, restart again: Main active, Option B remains
    assert client.post(f"/trips/{trip_id}/branches/{branches['Main']['branch_id']}/activate").status_code == 200
    restart_application_context()
    client = TestClient(app)
    assert client.post("/auth/login", json={"email": email, "password": "testpassword123"}).status_code == 200
    again = {b["display_name"]: b for b in _branches(client, trip_id)}
    assert again["Main"]["is_active"] and not again["Option B"]["is_active"]
    assert again["Option B"]["head_revision_id"] == source_revision_id
    assert not local_state_file.exists()


# -- feedback queue (202C.1A) across restarts ----------------------------------------------------


def test_pending_feedback_queue_order_survives_restarts_and_a_successful_application(local_state_file: Path) -> None:
    from app.models.planning_state import FeedbackEvent  # noqa: F401  (documenting the persisted type)
    from app.repositories.postgres_planning_state_repository import PostgresPlanningStateRepository
    from app.repositories.postgres_trip_repository import PostgresTripRepository
    from app.repositories.postgres_user_repository import PostgresUserRepository
    from app.repositories.user_repository import UserRecord
    from app.db.session import get_session_factory

    sf = get_session_factory()
    trip_id = f"trip_200a_{uuid.uuid4().hex}"
    owner_id = f"user_200a_{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    PostgresUserRepository(session_factory=sf).create_user(
        UserRecord(user_id=owner_id, email=f"{owner_id}@example.com", password_hash="x", created_at=now, updated_at=now)
    )
    PostgresTripRepository(session_factory=sf).create(trip_id, owner_id=owner_id)

    state = _planning_state(trip_id)
    a, b = _pending_event("Remove Belem Tower."), _pending_event("Also add Sintra.")
    a.created_at, b.created_at = now, now + timedelta(minutes=5)
    state.feedback_history = [a, b]
    from app.services.feedback_service import FeedbackService

    FeedbackService().recompute_pending_feedback_summary(state)
    PostgresPlanningStateRepository(session_factory=sf).save(state)
    assert state.pending_feedback_summary.next_feedback_event_id == a.feedback_event_id

    def loaded():
        restart_application_context()
        repo = PostgresPlanningStateRepository(session_factory=get_session_factory())
        return repo, repo.get_by_trip_id(trip_id)

    repo, reloaded = loaded()  # restart 1
    assert reloaded is not None
    summary = reloaded.pending_feedback_summary
    assert summary.queue_event_ids == [a.feedback_event_id, b.feedback_event_id]
    assert summary.next_feedback_event_id == a.feedback_event_id  # A is still next
    assert [e.applied_at for e in reloaded.feedback_history] == [None, None]

    service = _service(
        _FakeInterpreter(_completed_interpretation()),
        _FakePlanBuilder(_ready_removal_plan()),
        _FakeExecutor(builder=_completed_execution),
        repo,
    )
    result = service.regenerate(trip_id)
    assert result.status == TargetedRegenerationRuntimeStatus.COMPLETED
    assert result.feedback_event_id == a.feedback_event_id  # the OLDEST is applied first

    repo, after = loaded()  # restart 2
    assert after is not None
    by_id = {e.feedback_event_id: e for e in after.feedback_history}
    assert by_id[a.feedback_event_id].applied_at is not None and by_id[a.feedback_event_id].applied_in_version == "v2"
    assert by_id[b.feedback_event_id].applied_at is None  # B still queued
    assert after.metadata.current_version == "v2"
    assert after.pending_feedback_summary.next_feedback_event_id == b.feedback_event_id
    assert after.pending_feedback_summary.queue_event_ids == [b.feedback_event_id]
    assert not local_state_file.exists()


# -- async job persistence -------------------------------------------------------------------------


def test_async_job_state_survives_a_restart_and_an_interrupted_job_is_recovered(
    monkeypatch: pytest.MonkeyPatch, local_state_file: Path
) -> None:
    from app.db.session import get_session_factory
    from app.models.generation_job import GenerationJobType
    from app.repositories.postgres_job_repository import PostgresJobRepository
    from app.repositories.postgres_trip_repository import PostgresTripRepository
    from app.repositories.postgres_user_repository import PostgresUserRepository
    from app.repositories.user_repository import UserRecord
    from app.services import generation_job_service
    from app.services.generation_job_service import create_queued_job, mark_job_running

    sf = get_session_factory()
    trip_id, owner_id = f"trip_200a_{uuid.uuid4().hex}", f"user_200a_{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    PostgresUserRepository(session_factory=sf).create_user(
        UserRecord(user_id=owner_id, email=f"{owner_id}@example.com", password_hash="x", created_at=now, updated_at=now)
    )
    PostgresTripRepository(session_factory=sf).create(trip_id, owner_id=owner_id)
    repo = PostgresJobRepository(session_factory=sf)
    job = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE)
    repo.create(job)
    mark_job_running(job)
    repo.save(job)

    restart_application_context()  # the process "dies" while the job is running
    fresh = factory_module.get_job_repository()
    assert type(fresh).__name__ == "PostgresJobRepository"
    persisted = fresh.get_by_job_id(job.job_id)
    assert persisted is not None and persisted.status.value == "running"

    # startup recovery on the "new process": marks it interrupted, honestly, never resumes it
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()
    recovered = generation_job_service.recover_interrupted_jobs()
    assert recovered >= 1

    restart_application_context()
    final = factory_module.get_job_repository().get_by_job_id(job.job_id)
    assert final is not None and final.status.value == "failed"
    assert final.error_code == "JOB_INTERRUPTED"
    assert not local_state_file.exists()


# -- schema / startup verification ---------------------------------------------------------------------


def test_startup_check_passes_against_the_migrated_database() -> None:
    restart_application_context()
    check_postgres_ready(get_settings())  # connectivity + alembic head; raises otherwise


def test_an_empty_database_fails_the_schema_check_safely_and_upgrade_downgrade_upgrade_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uses a THROWAWAY database (created and dropped here) -- never the configured
    one's data -- to prove: empty DB -> safe schema error; alembic upgrade -> at head;
    downgrade to base -> re-upgrade -> at head."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    import logging

    # alembic's env.py calls logging.config.fileConfig(), which disables every existing
    # logger process-wide; snapshot and restore so later log-capture tests are unaffected.
    disabled = {n: lg.disabled for n, lg in logging.root.manager.loggerDict.items() if isinstance(lg, logging.Logger)}
    root_handlers, root_level = list(logging.root.handlers), logging.root.level
    levels = {n: lg.level for n, lg in logging.root.manager.loggerDict.items() if isinstance(lg, logging.Logger)}

    def _restore_logging() -> None:
        logging.root.handlers[:] = root_handlers
        logging.root.setLevel(root_level)
        for name, lg in list(logging.root.manager.loggerDict.items()):
            if isinstance(lg, logging.Logger):
                lg.disabled = disabled.get(name, False)
                lg.setLevel(levels.get(name, logging.NOTSET))

    admin_url = make_url(os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1))
    scratch_name = f"tobl_200a_scratch_{uuid.uuid4().hex[:10]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{scratch_name}"'))
    scratch_url = admin_url.set(database=scratch_name).render_as_string(hide_password=False)
    try:
        monkeypatch.setenv("DATABASE_URL", scratch_url)
        restart_application_context()

        with pytest.raises(PersistenceUnavailableError) as excinfo:
            check_postgres_ready(get_settings())
        assert "migration head" in str(excinfo.value) and scratch_name not in str(excinfo.value)

        config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
        command.upgrade(config, "head")
        check_postgres_ready(get_settings())
        command.downgrade(config, "base")
        with pytest.raises(PersistenceUnavailableError):
            check_postgres_ready(get_settings())
        command.upgrade(config, "head")
        check_postgres_ready(get_settings())
    finally:
        _restore_logging()
        restart_application_context()
        monkeypatch.undo()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_name}" WITH (FORCE)'))
        admin.dispose()
        restart_application_context()


# -- connection / session behaviour (Task 20) -----------------------------------------------------------


def test_sessions_close_failed_transactions_roll_back_and_connections_return_to_the_pool() -> None:
    from sqlalchemy.exc import IntegrityError

    from app.db.session import get_engine, get_session_factory
    from app.repositories.postgres_trip_repository import PostgresTripRepository
    from app.repositories.postgres_user_repository import PostgresUserRepository
    from app.repositories.user_repository import UserRecord

    restart_application_context()
    engine = get_engine()
    assert engine.pool._pre_ping is True  # pool_pre_ping: stale connections are replaced, not surfaced
    sf = get_session_factory()
    users = PostgresUserRepository(session_factory=sf)
    now = datetime.now(timezone.utc)
    email = f"pool-200a-{uuid.uuid4().hex}@example.com"
    users.create_user(UserRecord(user_id=f"user_{uuid.uuid4().hex}", email=email, password_hash="x", created_at=now, updated_at=now))
    assert engine.pool.checkedout() == 0  # a successful call returns its connection

    with pytest.raises(Exception) as excinfo:  # duplicate email -> unique violation
        users.create_user(UserRecord(user_id=f"user_{uuid.uuid4().hex}", email=email, password_hash="y", created_at=now, updated_at=now))
    assert isinstance(excinfo.value, (IntegrityError, ValueError)) or "email" in str(excinfo.value).lower()
    assert engine.pool.checkedout() == 0  # the failed transaction released its connection (rolled back)

    # ...and the repository is fully usable afterwards (no poisoned/aborted transaction left behind)
    assert users.get_by_email(email) is not None
    trip_id = f"trip_{uuid.uuid4().hex}"
    PostgresTripRepository(session_factory=sf).create(trip_id, owner_id=users.get_by_email(email).user_id)
    assert engine.pool.checkedout() == 0


def test_the_engine_and_session_factory_are_process_level_but_sessions_are_never_shared() -> None:
    import inspect

    from app.db import session as session_module
    from app.repositories import (
        postgres_itinerary_lineage_repository,
        postgres_job_repository,
        postgres_planning_state_repository,
        postgres_trip_repository,
        postgres_user_repository,
    )

    from app.db import transactions as transactions_module

    for module in (
        postgres_itinerary_lineage_repository,
        postgres_job_repository,
        postgres_planning_state_repository,
        postgres_trip_repository,
    ):
        source = inspect.getsource(module)
        # Section 200C: sessions are still short-lived and never shared implicitly -- a
        # repository either opens (and closes) its own via `session_scope`, or uses the ONE
        # session a unit of work explicitly injected. It never opens/keeps one by itself.
        assert "session_scope(" in source or "_scope()" in source
        assert "self._session_factory()" not in source
    assert "with session_factory() as session" in inspect.getsource(transactions_module.session_scope)
    user_source = inspect.getsource(postgres_user_repository)
    assert "self._session_factory() as session" in user_source
    assert "self._session =" not in user_source
    assert "lru_cache" in inspect.getsource(session_module.get_engine)


def test_full_planning_state_is_identical_after_a_restart() -> None:
    client = TestClient(app)
    email = _signup(client)
    trip_id = _trip(client)
    client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Make this less packed"})
    before = _state(client, trip_id)

    restart_application_context()
    client = TestClient(app)
    assert client.post("/auth/login", json={"email": email, "password": "testpassword123"}).status_code == 200
    after = _state(client, trip_id)

    assert after == before  # every persisted field (202B/202C additions included) round-trips through JSONB
