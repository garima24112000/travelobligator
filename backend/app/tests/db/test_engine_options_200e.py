from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.db import session as session_module
from app.db.session import build_engine, engine_options

# Section 200E: production pool/timeout settings are bounded, validated and actually reach create_engine.


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, DATABASE_URL="postgresql://u:pw@db:5432/app", **kw)


def test_defaults_are_conservative_and_the_connection_budget_is_pool_plus_overflow() -> None:
    s = _settings()
    assert (s.db_pool_size, s.db_max_overflow, s.db_pool_timeout_seconds, s.db_pool_recycle_seconds) == (5, 5, 10.0, 1800)
    assert (s.db_statement_timeout_ms, s.db_lock_timeout_ms, s.db_connect_timeout_seconds) == (30000, 10000, 5)
    assert s.db_pool_size + s.db_max_overflow == 10  # theoretical maximum connections per backend container


def test_settings_reach_create_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_create_engine(url: str, **kwargs: object):
        captured["url"], captured["kwargs"] = url, kwargs
        return object()

    monkeypatch.setattr(session_module, "create_engine", fake_create_engine)
    build_engine(
        _settings(
            DB_POOL_SIZE=3, DB_MAX_OVERFLOW=2, DB_POOL_TIMEOUT_SECONDS=7, DB_POOL_RECYCLE_SECONDS=600,
            DB_STATEMENT_TIMEOUT_MS=15000, DB_LOCK_TIMEOUT_MS=4000, DB_CONNECT_TIMEOUT_SECONDS=3,
        )
    )
    kwargs = captured["kwargs"]
    assert kwargs["pool_size"] == 3 and kwargs["max_overflow"] == 2 and kwargs["pool_timeout"] == 7
    assert kwargs["pool_recycle"] == 600 and kwargs["pool_pre_ping"] is True
    assert kwargs["connect_args"]["connect_timeout"] == 3
    assert kwargs["connect_args"]["options"] == "-c statement_timeout=15000 -c lock_timeout=4000"
    assert captured["url"].startswith("postgresql+psycopg://")


def test_zero_disables_a_timeout_and_recycling() -> None:
    options = engine_options(_settings(DB_STATEMENT_TIMEOUT_MS=0, DB_LOCK_TIMEOUT_MS=0, DB_POOL_RECYCLE_SECONDS=0))
    assert options["pool_recycle"] == -1 and "options" not in options["connect_args"]
    only_lock = engine_options(_settings(DB_STATEMENT_TIMEOUT_MS=0, DB_LOCK_TIMEOUT_MS=2000))
    assert only_lock["connect_args"]["options"] == "-c lock_timeout=2000"


@pytest.mark.parametrize(
    "kw",
    [
        {"DB_POOL_SIZE": 0}, {"DB_POOL_SIZE": -1}, {"DB_POOL_SIZE": 1000},
        {"DB_MAX_OVERFLOW": -1}, {"DB_POOL_TIMEOUT_SECONDS": 0}, {"DB_POOL_TIMEOUT_SECONDS": -3},
        {"DB_POOL_RECYCLE_SECONDS": -1},
        {"DB_STATEMENT_TIMEOUT_MS": 500}, {"DB_LOCK_TIMEOUT_MS": 999}, {"DB_STATEMENT_TIMEOUT_MS": -1},
    ],
)
def test_out_of_bounds_values_are_rejected(kw: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        _settings(**kw)


def test_migrations_and_the_readiness_probe_do_not_inherit_the_request_timeouts() -> None:
    import inspect

    from app.core import readiness

    env_source = open(session_module.__file__.replace("db/session.py", "../alembic/env.py")).read()
    assert "engine_options" not in env_source and "statement_timeout" not in env_source
    assert "engine_options" not in inspect.getsource(readiness._probe_engine)


def test_no_sub_second_default_is_shipped() -> None:
    s = _settings()
    assert s.db_statement_timeout_ms >= 1000 and s.db_lock_timeout_ms >= 1000
