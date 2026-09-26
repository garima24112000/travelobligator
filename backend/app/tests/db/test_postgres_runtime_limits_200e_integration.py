from __future__ import annotations

import os
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeout

from app.core.config import get_settings
from app.db.session import build_engine, get_engine
from app.main import app

# Section 200E: the request-transaction bounds against REAL PostgreSQL. Uses 1-second bounds (the minimum the
# settings accept) so nothing sleeps long.
pytestmark = [
    pytest.mark.postgres_integration,
    pytest.mark.skipif(
        os.environ.get("TRAVELOB_RUN_POSTGRES_TESTS") != "1" or not os.environ.get("DATABASE_URL"),
        reason="Optional live-Postgres test: set TRAVELOB_RUN_POSTGRES_TESTS=1 and a real DATABASE_URL.",
    ),
]


def _small_engine(monkeypatch: pytest.MonkeyPatch, **env: str):
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", env.pop("DB_STATEMENT_TIMEOUT_MS", "1000"))
    monkeypatch.setenv("DB_LOCK_TIMEOUT_MS", env.pop("DB_LOCK_TIMEOUT_MS", "1000"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    return build_engine(get_settings())


def test_a_statement_timeout_cancels_a_slow_statement_and_the_pool_stays_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _small_engine(monkeypatch, DB_POOL_SIZE="1", DB_MAX_OVERFLOW="0")
    started = time.monotonic()
    with pytest.raises(OperationalError) as excinfo:
        with engine.connect() as conn:
            conn.execute(text("SELECT pg_sleep(10)"))
    assert 0.8 < time.monotonic() - started < 4  # cancelled by the SERVER at ~1 s, not after 10 s
    assert "statement timeout" in str(excinfo.value.orig).lower()
    assert engine.pool.checkedout() == 0  # the connection went back to the pool (after rollback)
    with engine.connect() as conn:  # and the (single-connection) pool is not poisoned
        assert conn.execute(text("SELECT 1")).scalar_one() == 1
    engine.dispose()


def test_a_lock_timeout_aborts_a_blocked_lock_acquisition_and_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # lock timeout (1 s) below the statement timeout (30 s) -- the same relationship as the shipped defaults (10 s / 30 s)
    engine = _small_engine(monkeypatch, DB_STATEMENT_TIMEOUT_MS="30000")
    name = f"t200e_{uuid.uuid4().hex[:10]}"
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE {name} (id int primary key, v int)"))
        conn.execute(text(f"INSERT INTO {name} VALUES (1, 0)"))
    holder = engine.connect()
    try:
        holder.execute(text(f"SELECT * FROM {name} WHERE id = 1 FOR UPDATE"))  # holds the row lock (open txn)
        started = time.monotonic()
        with pytest.raises(OperationalError) as excinfo:
            with engine.begin() as blocked:  # a second transaction wants the same row
                blocked.execute(text(f"UPDATE {name} SET v = 99 WHERE id = 1"))
        assert 0.8 < time.monotonic() - started < 4
        assert "lock timeout" in str(excinfo.value.orig).lower()
    finally:
        holder.rollback()
        holder.close()
    with engine.connect() as conn:  # the blocked transaction rolled back: nothing changed
        assert conn.execute(text(f"SELECT v FROM {name} WHERE id = 1")).scalar_one() == 0
    assert engine.pool.checkedout() == 0
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE {name}"))
    engine.dispose()


def test_migrations_style_engines_do_not_inherit_the_request_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import create_engine, pool

    from app.db.session import normalize_database_url

    _small_engine(monkeypatch)  # request settings: 1 s
    plain = create_engine(normalize_database_url(os.environ["DATABASE_URL"]), poolclass=pool.NullPool)
    with plain.connect() as conn:
        assert conn.execute(text("SHOW statement_timeout")).scalar_one() in ("0", "0ms")  # unlimited, like alembic
    request_engine = build_engine(get_settings())
    with request_engine.connect() as conn:
        assert conn.execute(text("SHOW statement_timeout")).scalar_one() == "1s"
        assert conn.execute(text("SHOW lock_timeout")).scalar_one() == "1s"
    request_engine.dispose()
    plain.dispose()


def test_pool_exhaustion_fails_within_the_bounded_timeout_and_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _small_engine(monkeypatch, DB_POOL_SIZE="1", DB_MAX_OVERFLOW="0", DB_POOL_TIMEOUT_SECONDS="1")
    held = engine.connect()  # hold the ONLY connection
    try:
        started = time.monotonic()
        with pytest.raises(PoolTimeout) as excinfo:
            engine.connect()
        assert 0.8 < time.monotonic() - started < 3
        message = str(excinfo.value)
        assert os.environ["DATABASE_URL"].split("@")[0] not in message and "password" not in message.lower()
    finally:
        held.close()
    assert engine.pool.checkedout() == 0  # returned
    with engine.connect() as conn:  # and usable again
        assert conn.execute(text("SELECT 1")).scalar_one() == 1
    engine.dispose()


# -- through the real API: sanitized 503s ---------------------------------------------------------------------------------


def _seeded_client(monkeypatch: pytest.MonkeyPatch, **env: str) -> tuple[TestClient, str]:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    get_engine.cache_clear()
    client = TestClient(app, raise_server_exceptions=False)
    signup = client.post("/auth/signup", json={"email": f"t200e-{uuid.uuid4().hex}@example.com", "password": "testpassword123"})
    assert signup.status_code == 201, signup.text
    trip = client.post(
        "/trips",
        json={"destination_scope": "single_city", "primary_destination": "Lisbon, Portugal", "origin_city": "Madrid, Spain",
              "start_date": "2026-08-10", "end_date": "2026-08-12", "travelers_count": 2, "travel_group_type": "couple"},
    )
    assert trip.status_code == 201, trip.text
    return client, trip.json()["data"]["trip_id"]


def test_a_lock_timeout_through_the_api_is_a_sanitized_503_and_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    client, trip_id = _seeded_client(monkeypatch, DB_LOCK_TIMEOUT_MS="1000", DB_STATEMENT_TIMEOUT_MS="30000")
    before = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]["feedback_history"]
    holder = get_engine().connect()  # an external transaction holds the trip's state row lock
    try:
        holder.execute(text("SELECT 1 FROM planning_states WHERE trip_id = :t FOR UPDATE"), {"t": trip_id})
        response = client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Make this less packed"})
    finally:
        holder.rollback()
        holder.close()
    assert response.status_code == 503 and response.json()["errors"][0]["code"] == "PERSISTENCE_UNAVAILABLE"
    for needle in ("lock", "planning_states", "SELECT", "postgres", "psycopg", "UPDATE", os.environ["DATABASE_URL"].split("@")[-1]):
        assert needle not in response.text, needle
    after = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]["feedback_history"]
    assert after == before  # rolled back cleanly; the request is retryable
    assert client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Make this less packed"}).status_code == 200


def test_pool_exhaustion_through_the_api_is_a_sanitized_503_then_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    client, trip_id = _seeded_client(monkeypatch, DB_POOL_SIZE="1", DB_MAX_OVERFLOW="0", DB_POOL_TIMEOUT_SECONDS="1")
    held = get_engine().connect()  # the application's only pooled connection is busy
    try:
        started = time.monotonic()
        response = client.get(f"/trips/{trip_id}")
        assert time.monotonic() - started < 4
    finally:
        held.close()
    assert response.status_code == 503 and response.json()["errors"][0]["code"] == "PERSISTENCE_UNAVAILABLE"
    assert os.environ["DATABASE_URL"].split("@")[-1] not in response.text and "pool" not in response.text.lower()
    assert client.get(f"/trips/{trip_id}").status_code == 200  # connection returned, service recovered
