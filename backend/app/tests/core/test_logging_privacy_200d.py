from __future__ import annotations

import ast
import io
import json
import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import ops_events
from app.core.config import get_settings
from app.core.logging_config import ALLOWED_EXTRA_FIELDS, JsonFormatter, RedactingPlainFormatter
from app.core.redaction import REDACTED, redact

# Section 200D: the log contract is content-safe. Every secret below is a SYNTHETIC sentinel.

_DB = "SENTINEL_DB_PASSWORD_31337"
_REDIS = "SENTINEL_REDIS_PASSWORD_4242"
_API = "SENTINEL_API_KEY_sk_test_777"
_SESSION_KEY = "SENTINEL_SESSION_SECRET_555"
_FEEDBACK = "SENTINEL_FEEDBACK_TEXT_avoid_the_secret_beach_bar"
_PROMPT = "SENTINEL_AI_PROMPT_you_are_a_travel_writer"
_PROVIDER_BODY = "SENTINEL_RAW_PROVIDER_BODY_lat_38.71_lng_-9.13"


@pytest.fixture()
def secrets_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"postgresql://appuser:{_DB}@dbhost:5432/appdb")
    monkeypatch.setenv("REDIS_URL", f"redis://:{_REDIS}@redishost:6379/0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", _API)
    monkeypatch.setenv("SESSION_SECRET_KEY", _SESSION_KEY)
    get_settings.cache_clear()


def _render(record: logging.LogRecord) -> str:
    return JsonFormatter().format(record)


def _record(message: str, exc: BaseException | None = None, **extra: object) -> logging.LogRecord:
    exc_info = None
    if exc is not None:
        try:
            raise exc
        except BaseException:  # noqa: BLE001
            exc_info = sys.exc_info()
    record = logging.LogRecord("app.test", logging.WARNING, __file__, 1, message, None, exc_info)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_configured_secrets_are_redacted_from_messages_extras_and_tracebacks(secrets_configured: None) -> None:
    driver_error = RuntimeError(
        f'connection to "postgresql://appuser:{_DB}@dbhost:5432/appdb" failed; redis://:{_REDIS}@redishost:6379/0; key={_API}'
    )
    line = _render(
        _record(f"db said {_DB} and {_REDIS}", driver_error, error_kind=f"key {_API}", provider="osrm")
    )
    for secret in (_DB, _REDIS, _API, "appuser", ":5432"):
        assert secret not in line, secret
    assert REDACTED in line
    assert json.loads(line)["provider"] == "osrm"  # ordinary values are untouched


def test_url_credentials_are_redacted_even_for_unconfigured_urls() -> None:
    assert redact("connect https://user:hunter2hunter2@example.org/x") == f"connect https://{REDACTED}"
    assert redact("plain text without urls") == "plain text without urls"


def test_pydantic_input_values_are_dropped_from_error_text() -> None:
    text = "1 validation error [type=enum, input_value='SENTINEL_USER_TEXT', input_type=str]"
    cleaned = redact(text)
    assert "SENTINEL_USER_TEXT" not in cleaned and "input_type=str" in cleaned


def test_the_plain_fallback_formatter_redacts_too(secrets_configured: None) -> None:
    line = RedactingPlainFormatter("%(message)s").format(_record(f"oops {_DB}"))
    assert _DB not in line


def test_short_config_values_do_not_blank_ordinary_words(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_SECRET_KEY", "abc")  # shorter than the literal-match minimum
    get_settings.cache_clear()
    assert redact("abc def") == "abc def"


def _capture_all_app_logs() -> tuple[io.StringIO, logging.Handler, logging.Logger, int]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.setLevel(logging.DEBUG)
    logger = logging.getLogger("app")
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    return stream, handler, logger, previous


def test_real_request_flows_never_log_feedback_text_session_cookies_or_secrets(
    client: TestClient, created_trip_id: str, secrets_configured: None
) -> None:
    session_values = [c.value for c in client.cookies.jar if c.value]
    assert session_values
    stream, handler, logger, previous = _capture_all_app_logs()
    try:
        client.post(f"/trips/{created_trip_id}/feedback", json={"feedback_text": _FEEDBACK})
        client.post(f"/trips/{created_trip_id}/regenerate", json={"confirm": True})
        client.post(f"/trips/{created_trip_id}/regenerate", json={"confirm": False})
        client.get(f"/trips/{created_trip_id}", headers={"Authorization": "Bearer SENTINEL_BEARER_TOKEN"})
        client.post("/auth/login", json={"email": "nobody@example.com", "password": "SENTINEL_LOGIN_PASSWORD"})
        client.post(f"/trips/{created_trip_id}/generate")
        client.get("/ready")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    output = stream.getvalue()
    assert output.strip(), "expected some structured log output"
    for sentinel in (_FEEDBACK, "SENTINEL_BEARER_TOKEN", "SENTINEL_LOGIN_PASSWORD", "nobody@example.com", *session_values):
        assert sentinel not in output, sentinel
    for secret in (_DB, _REDIS, _API, _SESSION_KEY):
        assert secret not in output


def test_provider_gateway_logs_carry_no_raw_provider_response_or_query(secrets_configured: None) -> None:
    from app.providers.gateway import _log_provider_call

    stream, handler, logger, previous = _capture_all_app_logs()
    try:
        _log_provider_call(provider="osrm", stage="routing", status="failed", duration_ms=12.5)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    record = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert record["event"] == "provider.failure" and record["outcome"] == "failed" and record["provider"] == "osrm"
    assert _PROVIDER_BODY not in stream.getvalue()


# -- static contract ---------------------------------------------------------------------------------------------


_APP = Path(__file__).resolve().parents[2]
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
_FORBIDDEN_NAMES = {
    "prompt", "system_prompt", "user_prompt", "feedback_text", "raw_response", "response_text", "narrative",
    "narrative_text", "payload", "raw_payload", "response_body", "request_body", "password", "session_token",
    "cookie", "authorization", "database_url", "redis_url", "api_key", "itinerary",
}


def _logged_names(call: ast.Call) -> set[str]:
    names: set[str] = set()
    bare = list(call.args) + [k.value for k in call.keywords if k.arg != "extra"]
    for arg in bare:
        for node in ast.walk(arg):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Name):
                names.add(node.id)
    # keys of `extra={...}` dict literals
    for keyword in call.keywords:
        if keyword.arg == "extra" and isinstance(keyword.value, ast.Dict):
            names |= {k.value for k in keyword.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return names


def test_no_logger_call_references_user_content_prompts_or_credentials() -> None:
    offenders = []
    for path in _APP.rglob("*.py"):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _LOG_METHODS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"logger", "log", "_logger"}
            ):
                bad = _logged_names(node) & _FORBIDDEN_NAMES
                # `planning_state.trip_id`-style attribute reads are fine; a bare forbidden Name is not.
                if bad:
                    offenders.append((str(path.relative_to(_APP)), node.lineno, sorted(bad)))
    assert offenders == []


def test_the_field_allowlist_has_no_sensitive_looking_name() -> None:
    for field in ALLOWED_EXTRA_FIELDS:
        assert not any(bad in field for bad in ("password", "secret", "token", "cookie", "body", "prompt", "text", "url", "key"))


def test_every_operational_event_is_a_stable_dotted_name() -> None:
    required = {
        "persistence.ready", "persistence.unavailable", "persistence.schema_mismatch",
        "cache.hit", "cache.miss", "cache.bypass", "cache.error",
        "transaction.committed", "transaction.rollback", "transaction.conflict", "transaction.retry", "transaction.deadlock",
        "job.created", "job.claimed", "job.heartbeat_failed", "job.succeeded", "job.failed", "job.interrupted", "job.claim_conflict",
        "provider.success", "provider.failure",
    }
    assert required <= ops_events.ALL_EVENTS


def test_log_event_never_raises_and_carries_the_event_field() -> None:
    seen: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    logger = logging.getLogger("app.test_events")
    logger.addHandler(Collect(level=logging.DEBUG))
    logger.setLevel(logging.DEBUG)
    ops_events.log_event(logger, logging.INFO, ops_events.JOB_CREATED, "x", job_id="job_1", job_type="generate")
    ops_events.log_event(logger, logging.INFO, ops_events.JOB_CREATED, "x", message="collides with LogRecord")  # must not raise
    assert seen[0].event == "job.created" and seen[0].funcName == "test_log_event_never_raises_and_carries_the_event_field"
