from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.main import app
from app.tests.conftest import create_trip_payload

# Section 200E.1 against REAL PostgreSQL: Redis unreachable BEFORE startup (a closed port).
pytestmark = [
    pytest.mark.postgres_integration,
    pytest.mark.skipif(
        os.environ.get("TRAVELOB_RUN_POSTGRES_TESTS") != "1" or not os.environ.get("DATABASE_URL"),
        reason="Optional live-Postgres test: set TRAVELOB_RUN_POSTGRES_TESTS=1 and a real DATABASE_URL.",
    ),
]


def test_backend_starts_degraded_with_redis_down_and_state_goes_to_postgres_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sqlite_path = tmp_path / "provider_cache.sqlite3"
    json_path = tmp_path / "state.json"
    monkeypatch.setenv("PROVIDER_CACHE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")  # nothing listens: Redis is down from the beginning
    monkeypatch.setenv("PROVIDER_CACHE_PATH", str(sqlite_path))
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(json_path))
    get_settings.cache_clear()

    with TestClient(app) as client:  # startup (persistence + schema checks) must still succeed
        assert client.get("/health").status_code == 200
        ready = client.get("/ready")
        data = ready.json()["data"]
        assert ready.status_code == 200 and data["status"] == "degraded"
        assert data["checks"]["persistence"]["status"] == "ready" and data["checks"]["schema"]["status"] == "ok"
        assert data["checks"]["provider_cache"] == {"status": "degraded", "backend": "redis"}

        signup = client.post("/auth/signup", json={"email": f"t200e1-{uuid.uuid4().hex}@example.com", "password": "testpassword123"})
        assert signup.status_code == 201, signup.text
        created = client.post("/trips", json=create_trip_payload())
        assert created.status_code == 201
        trip_id = created.json()["data"]["trip_id"]
        generated = client.post(f"/trips/{trip_id}/generate")  # provider-backed pipeline runs uncached
        assert generated.status_code == 200, generated.text

    with get_session_factory()() as session:  # authoritative state is in PostgreSQL
        assert session.execute(text("select count(*) from planning_states where trip_id=:t"), {"t": trip_id}).scalar_one() == 1
        assert session.execute(text("select count(*) from itinerary_revisions where trip_id=:t"), {"t": trip_id}).scalar_one() >= 1
    assert not json_path.exists()  # no Local JSON persistence fallback
    assert not sqlite_path.exists()  # no SQLite provider-cache fallback
    assert get_settings().provider_cache_backend == "redis" and get_settings().persistence_backend == "postgres"
