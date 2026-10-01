from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core import readiness as readiness_module
from app.core.config import get_settings
from app.core.metrics import registry
from app.core.readiness import reset_readiness_engines
from app.main import app

# Section 200D: REAL PostgreSQL + REAL Redis. Outages and recoveries are produced by putting a small TCP
# proxy in front of each dependency and stopping / restarting it -- the real clients see genuine connection
# failures, and the SAME running app object recovers (no restart). Gated by BOTH gates:
#   TRAVELOB_RUN_POSTGRES_TESTS=1 DATABASE_URL=<alembic-upgraded db>
#   TRAVELOB_RUN_REDIS_TESTS=1    REDIS_URL=redis://host:port/0

_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_REDIS_URL = os.environ.get("REDIS_URL", "")
pytestmark = [
    pytest.mark.postgres_integration,
    pytest.mark.redis_integration,
    pytest.mark.skipif(
        os.environ.get("TRAVELOB_RUN_POSTGRES_TESTS") != "1"
        or os.environ.get("TRAVELOB_RUN_REDIS_TESTS") != "1"
        or not _DATABASE_URL
        or not _REDIS_URL,
        reason="Needs both gates: TRAVELOB_RUN_POSTGRES_TESTS=1 + DATABASE_URL and TRAVELOB_RUN_REDIS_TESTS=1 + REDIS_URL.",
    ),
]

P = "travelobligator_"


class TcpProxy:
    """A controllable TCP forwarder: stop() = the dependency vanishes (listener + live connections die);
    start(same port) = it is back.

    stop() must be SYNCHRONOUS and portable. Closing a listening socket while another thread is blocked in
    accept() wakes that thread on macOS/BSD, but NOT on Linux: there the blocked accept() keeps the listening
    socket alive in the kernel, so the port keeps accepting and the next connection is still forwarded -- the
    "outage" never happens (Section 203A.2: this passed on macOS and failed on the Linux CI runner). So the
    accept loop polls with a short timeout and watches a stop flag, and stop() waits for that thread to exit
    BEFORE closing the listener: when stop() returns nothing is listening on the port and every forwarded
    connection is closed, on any platform."""

    def __init__(self, host: str, port: int) -> None:
        self.target = (host, port)
        self.port: int | None = None
        self._listener: socket.socket | None = None
        self._acceptor: threading.Thread | None = None
        self._stopping = threading.Event()
        self._conns: list[socket.socket] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self.port or 0))
        listener.listen(64)
        listener.settimeout(0.05)  # accept() never blocks indefinitely, so stop() can always join the loop
        self.port = listener.getsockname()[1]
        self._listener = listener
        self._stopping = threading.Event()
        self._acceptor = threading.Thread(target=self._accept, args=(listener, self._stopping), daemon=True)
        self._acceptor.start()

    def _accept(self, listener: socket.socket, stopping: threading.Event) -> None:
        while not stopping.is_set():
            try:
                client, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            client.settimeout(None)
            try:
                upstream = socket.create_connection(self.target, timeout=3)
                upstream.settimeout(None)
            except OSError:
                client.close()
                continue
            with self._lock:
                self._conns += [client, upstream]
            for a, b in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pipe, args=(a, b), daemon=True).start()

    @staticmethod
    def _pipe(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.close()
                except OSError:
                    pass

    def stop(self) -> None:
        self._stopping.set()
        if self._acceptor is not None:
            self._acceptor.join(timeout=5)
            assert not self._acceptor.is_alive(), "proxy accept loop did not stop"
            self._acceptor = None
        if self._listener is not None:
            self._listener.close()  # nothing is in accept() any more: the port is really closed now
            self._listener = None
        with self._lock:
            conns, self._conns = self._conns, []
        for s in conns:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


def _port_is_closed(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    except OSError:
        return True
    return False


def test_proxy_stop_really_closes_the_port_and_start_reopens_it() -> None:
    """Guards the outage mechanism itself: if stop() left the port accepting (the Linux behaviour of a naive
    close()), every outage test below would silently talk to a healthy dependency."""
    proxy = TcpProxy(*_redis_target())
    proxy.start()
    try:
        port = proxy.port
        with socket.create_connection(("127.0.0.1", port), timeout=3) as live:
            live.sendall(b"PING\r\n")
            assert live.recv(16).startswith(b"+PONG")  # forwards to the real Redis
            proxy.stop()
            assert live.recv(16) == b""  # the live connection died with the proxy
        for _ in range(3):  # and no new connection is accepted, not even once
            assert _port_is_closed(port)
        proxy.start()
        assert proxy.port == port
        with socket.create_connection(("127.0.0.1", port), timeout=3) as again:
            again.sendall(b"PING\r\n")
            assert again.recv(16).startswith(b"+PONG")
    finally:
        proxy.stop()


def _pg_target() -> tuple[str, int]:
    url = make_url(_DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1))
    return url.host or "127.0.0.1", url.port or 5432


def _redis_target() -> tuple[str, int]:
    parts = urlsplit(_REDIS_URL)
    return parts.hostname or "127.0.0.1", parts.port or 6379


def _proxied_pg_url(port: int) -> str:
    url = make_url(_DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1))
    return url.set(host="127.0.0.1", port=port).render_as_string(hide_password=False)


def _proxied_redis_url(port: int) -> str:
    parts = urlsplit(_REDIS_URL)
    return urlunsplit((parts.scheme, f"127.0.0.1:{port}", parts.path, "", ""))


@pytest.fixture()
def stack(monkeypatch: pytest.MonkeyPatch):
    """The running app wired to real Postgres + Redis THROUGH controllable proxies."""
    pg, rd = TcpProxy(*_pg_target()), TcpProxy(*_redis_target())
    pg.start()
    rd.start()
    monkeypatch.setenv("DATABASE_URL", _proxied_pg_url(pg.port))
    monkeypatch.setenv("REDIS_URL", _proxied_redis_url(rd.port))
    monkeypatch.setenv("REDIS_KEY_PREFIX", f"ops200d-{uuid.uuid4().hex[:8]}")
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("REDIS_SOCKET_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("REDIS_CONNECT_TIMEOUT_SECONDS", "1")
    get_settings.cache_clear()
    reset_readiness_engines()
    readiness_module._last_state.clear()
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, pg, rd
    finally:
        pg.stop()
        rd.stop()
        reset_readiness_engines()


def _ready(client: TestClient) -> tuple[int, dict[str, Any]]:
    response = client.get("/ready")
    return response.status_code, response.json()["data"]


def _expected_head() -> str:
    return next(iter(readiness_module.alembic_head_revisions()))


# -- Cases A-E: liveness / readiness across real outages and recoveries ----------------------------------------------


def test_A_healthy_postgres_and_redis_is_ready(stack) -> None:
    client, _, _ = stack
    assert client.get("/health").status_code == 200
    status, data = _ready(client)
    assert status == 200 and data["status"] == "ready"
    assert data["checks"]["persistence"] == {"status": "ready", "backend": "postgres"}
    assert data["checks"]["schema"] == {"status": "ok", "expected_head": _expected_head(), "current_head": _expected_head()}
    assert data["checks"]["provider_cache"]["status"] == "healthy"


def test_B_C_redis_outage_is_degraded_and_recovery_needs_no_restart(stack) -> None:
    client, _, rd = stack
    assert _ready(client)[1]["status"] == "ready"

    rd.stop()  # B: Redis vanishes
    assert client.get("/health").status_code == 200
    status, data = _ready(client)
    assert status == 200 and data["status"] == "degraded" and data["checks"]["provider_cache"]["status"] == "degraded"
    assert data["checks"]["persistence"]["status"] == "ready"  # PostgreSQL is unaffected

    rd.start()  # C: Redis is back -- same process, same app object
    status, data = _ready(client)
    assert status == 200 and data["status"] == "ready"
    assert data["checks"]["provider_cache"]["status"] in ("recovering", "healthy")


def test_D_E_postgres_outage_is_503_while_health_stays_200_and_recovery_needs_no_restart(stack) -> None:
    client, pg, _ = stack
    assert _ready(client)[0] == 200

    pg.stop()  # D: PostgreSQL vanishes AFTER startup
    started = time.monotonic()
    status, data = _ready(client)
    assert status == 503 and data["status"] == "not_ready" and time.monotonic() - started < 6
    assert data["checks"]["persistence"]["status"] == "unavailable"
    assert client.get("/health").status_code == 200  # liveness is independent of dependencies

    pg.start()  # E: back -- the probe engine pre-pings and reconnects; nothing restarts
    status, data = _ready(client)
    assert status == 200 and data["checks"]["schema"]["status"] == "ok"


def test_F_a_database_at_the_previous_schema_head_is_not_ready(stack, monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic import command
    from alembic.config import Config

    client, _, _ = stack
    admin_url = make_url(_DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1))
    scratch = f"tobl_200d_scratch_{uuid.uuid4().hex[:10]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{scratch}"'))
    scratch_url = admin_url.set(database=scratch).render_as_string(hide_password=False)
    # alembic's env.py reconfigures logging process-wide; restore it afterwards
    saved = {n: (lg.disabled, lg.level) for n, lg in logging.root.manager.loggerDict.items() if isinstance(lg, logging.Logger)}
    root_handlers, root_level = list(logging.root.handlers), logging.root.level
    try:
        monkeypatch.setenv("DATABASE_URL", scratch_url)
        get_settings.cache_clear()
        reset_readiness_engines()
        config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
        command.upgrade(config, "cc7238a1bca3")  # the previous (200C-1) head: schema BEHIND the code
        response = client.get("/ready")
        schema = response.json()["data"]["checks"]["schema"]
        assert response.status_code == 503 and schema["status"] == "mismatch"
        assert schema["current_head"] == "cc7238a1bca3" and schema["expected_head"] == _expected_head()
        assert scratch not in response.text and "tobl:" not in response.text
        assert client.get("/health").status_code == 200

        command.upgrade(config, "head")
        assert _ready(client)[1]["checks"]["schema"]["status"] == "ok"  # recovers after the release step
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        admin.dispose()
        reset_readiness_engines()
        logging.root.handlers[:] = root_handlers
        logging.root.setLevel(root_level)
        for name, lg in list(logging.root.manager.loggerDict.items()):
            if isinstance(lg, logging.Logger) and name in saved:
                lg.disabled, lg.level = saved[name]


# -- Task 29: metrics smoke against the real stack ---------------------------------------------------------------------------------


def _fake_frankfurter(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    from app.providers.currency import frankfurter_adapter

    calls: list[int] = []

    class _Resp:
        def raise_for_status(self) -> None: ...
        def json(self) -> Any:
            return {"amount": 1.0, "base": "USD", "date": "2026-08-10", "rates": {"EUR": 0.92}}

    class _Client:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None):
            calls.append(1)
            return _Resp()

    monkeypatch.setattr(frankfurter_adapter.httpx, "Client", lambda **kw: _Client())
    return calls


def test_metrics_reflect_real_cache_hits_conflicts_jobs_and_requests(stack, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timezone

    from app.db.session import get_session_factory
    from app.models.generation_job import GenerationJobType, create_queued_job
    from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
    from app.models.user import UserRecord
    from app.providers.currency import frankfurter_adapter
    from app.providers.currency.frankfurter_adapter import FrankfurterCurrencyAdapter
    from app.repositories.errors import ConcurrentStateUpdateError
    from app.repositories.factory import get_job_repository, get_planning_state_repository
    from app.repositories.postgres_trip_repository import PostgresTripRepository
    from app.repositories.postgres_user_repository import PostgresUserRepository
    from app.services import generation_job_service as svc
    from app.storage.provider_cache_store import get_provider_cache_store

    client, _, rd = stack
    calls = _fake_frankfurter(monkeypatch)
    monkeypatch.setattr(frankfurter_adapter, "get_provider_cache_store", get_provider_cache_store)

    def v(name: str, **labels: str) -> float:
        return registry.value(P + name, labels)

    # a normal request
    http0 = v("http_requests_total", method="GET", route="/health", status_class="2xx")
    assert client.get("/health").status_code == 200
    assert v("http_requests_total", method="GET", route="/health", status_class="2xx") - http0 == 1

    # provider cache against REAL Redis: MISS, then HIT (and the provider was called once)
    miss0, hit0 = v("provider_cache_total", provider="frankfurter", status="MISS"), v("provider_cache_total", provider="frankfurter", status="HIT")
    adapter = FrankfurterCurrencyAdapter()
    adapter.get_exchange_rate("USD", "Lisbon, Portugal")
    adapter.get_exchange_rate("USD", "Lisbon, Portugal")
    assert v("provider_cache_total", provider="frankfurter", status="MISS") - miss0 == 1
    assert v("provider_cache_total", provider="frankfurter", status="HIT") - hit0 == 1
    assert len(calls) == 1

    # a Redis outage: ERROR (then BYPASS during the cooldown); the provider is still called
    err0, byp0 = v("provider_cache_total", provider="frankfurter", status="ERROR"), v("provider_cache_total", provider="frankfurter", status="BYPASS")
    rd.stop()
    monkeypatch.setattr(FrankfurterCurrencyAdapter, "__init__", FrankfurterCurrencyAdapter.__init__)
    adapter.get_exchange_rate("USD", "Lisbon, Portugal")
    adapter.get_exchange_rate("USD", "Lisbon, Portugal")
    assert v("provider_cache_total", provider="frankfurter", status="ERROR") - err0 >= 1
    assert v("provider_cache_total", provider="frankfurter", status="BYPASS") - byp0 >= 1
    rd.start()

    # a real optimistic-concurrency conflict
    now = datetime.now(timezone.utc)
    owner_id, trip_id = f"user_{uuid.uuid4().hex}", f"trip_{uuid.uuid4().hex}"
    sf = get_session_factory()
    PostgresUserRepository(session_factory=sf).create_user(UserRecord(user_id=owner_id, email=f"{owner_id}@example.com", password_hash="x", created_at=now, updated_at=now))
    PostgresTripRepository(session_factory=sf).create(trip_id, owner_id=owner_id)
    state = PlanningState(trip_id=trip_id, trip_request=TripRequest(primary_destination="X", start_date="2026-10-10", end_date="2026-10-11", travelers_count=1, travel_group_type=TravelGroupType.SOLO))
    repo = get_planning_state_repository()
    repo.save(state)
    a, b = repo.get_by_trip_id(trip_id), repo.get_by_trip_id(trip_id)
    conflicts0, commits0 = v("db_concurrency_conflicts_total", kind="state"), v("db_transactions_total", scope="single", outcome="commit")
    repo.save(a)
    with pytest.raises(ConcurrentStateUpdateError):
        repo.save(b)
    assert v("db_concurrency_conflicts_total", kind="state") - conflicts0 == 1
    assert v("db_transactions_total", scope="single", outcome="commit") > commits0

    # a job: create -> claim -> succeed (real PostgreSQL lease)
    def generate(t: str, *args: Any, **kw: Any) -> PlanningState:
        return PlanningState(trip_id=t, trip_request=state.trip_request)

    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan", generate)
    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan_via_langgraph", generate)
    base = {e: v("jobs_total", job_type="generate", event=e) for e in ("created", "claimed", "succeeded")}
    job = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE)
    svc._create_job_or_conflict(job)
    svc.run_generate_job(job.job_id, lease_owner="inst-metrics")
    assert get_job_repository().get_by_job_id(job.job_id).status.value == "succeeded"
    for event in ("created", "claimed", "succeeded"):
        assert v("jobs_total", job_type="generate", event=event) - base[event] == 1, event
    assert v("jobs_running_local") == 0

    # the scrape exposes all of it and nothing sensitive
    text_out = client.get("/metrics").text
    for needle in ("provider_cache_total", "db_concurrency_conflicts_total", "jobs_total", "db_transactions_total", "http_requests_total"):
        assert needle in text_out
    for forbidden in (trip_id, job.job_id, owner_id, "postgresql://", "redis://", "127.0.0.1", "inst-metrics"):
        assert forbidden not in text_out, forbidden
