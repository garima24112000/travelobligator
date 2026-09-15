from __future__ import annotations

import json
import logging
import sys

import pytest

from app.core.config import Settings
from app.core.logging_config import (
    ALLOWED_EXTRA_FIELDS,
    APP_LOGGER_NAME,
    SENSITIVE_LOG_FIELD_NAMES,
    JsonFormatter,
    RequestIdLogFilter,
    _HANDLER_NAME,
    _is_sensitive_field_name,
    configure_logging,
)
from app.core.request_context import request_id_scope

# Tests for the Step 187B structured logging foundation
# (backend/app/core/logging_config.py, docs/14_backend_architecture.md
# section 121). No route/service/provider/auth/persistence/async-job
# behavior is touched by any test here -- these are pure formatter/config
# unit tests plus a couple of stdout-capturing integration checks of
# `configure_logging()` itself.


def _make_record(
    *, level: int = logging.WARNING, msg: str = "test message", extra: dict | None = None
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.tests.fake_module",
        level=level,
        pathname=__file__,
        lineno=42,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


# --- JsonFormatter ----------------------------------------------------


def test_json_formatter_emits_valid_json() -> None:
    record = _make_record()
    rendered = JsonFormatter().format(record)
    parsed = json.loads(rendered)
    assert isinstance(parsed, dict)


def test_json_formatter_default_fields_exist() -> None:
    record = _make_record(msg="hello world")
    parsed = json.loads(JsonFormatter().format(record))

    assert parsed["message"] == "hello world"
    assert parsed["level"] == "WARNING"
    assert parsed["logger"] == "app.tests.fake_module"
    assert "timestamp" in parsed
    assert "module" in parsed
    assert "function" in parsed
    assert parsed["line"] == 42


def test_json_formatter_does_not_crash_without_extra_fields() -> None:
    record = _make_record(extra=None)
    rendered = JsonFormatter().format(record)
    parsed = json.loads(rendered)
    for field in ALLOWED_EXTRA_FIELDS:
        assert field not in parsed


def test_json_formatter_includes_allowed_extra_fields() -> None:
    record = _make_record(
        extra={"trip_id": "trip_abc", "job_id": "job_123", "status": "succeeded"}
    )
    parsed = json.loads(JsonFormatter().format(record))

    assert parsed["trip_id"] == "trip_abc"
    assert parsed["job_id"] == "job_123"
    assert parsed["status"] == "succeeded"


def test_json_formatter_includes_every_allowed_extra_field_name() -> None:
    """Every name in ALLOWED_EXTRA_FIELDS must actually be readable by
    the formatter -- not just a subset of them."""
    extra = {field: f"value-{field}" for field in ALLOWED_EXTRA_FIELDS}
    record = _make_record(extra=extra)
    parsed = json.loads(JsonFormatter().format(record))

    for field in ALLOWED_EXTRA_FIELDS:
        assert parsed[field] == f"value-{field}"


def test_json_formatter_drops_unallowlisted_fields() -> None:
    record = _make_record(extra={"some_random_field": "should not appear"})
    parsed = json.loads(JsonFormatter().format(record))
    assert "some_random_field" not in parsed


@pytest.mark.parametrize(
    "field_name",
    [
        "password",
        "password_hash",
        "session",
        "session_cookie",
        "cookie",
        "authorization",
        "token",
        "secret",
        "api_key",
        "session_secret_key",
        "groq_api_key",
        "anthropic_api_key",
    ],
)
def test_json_formatter_drops_secret_looking_fields(field_name: str) -> None:
    record = _make_record(extra={field_name: "should-never-appear"})
    parsed = json.loads(JsonFormatter().format(record))
    assert field_name not in parsed
    assert "should-never-appear" not in json.dumps(parsed)


@pytest.mark.parametrize(
    "field_name",
    ["request_body", "response_body", "provider_payload", "planning_state", "itinerary"],
)
def test_json_formatter_drops_payload_dump_fields(field_name: str) -> None:
    record = _make_record(extra={field_name: {"this": "should not be logged"}})
    parsed = json.loads(JsonFormatter().format(record))
    assert field_name not in parsed


def test_json_formatter_never_dumps_record_dict_wholesale() -> None:
    """Standard, always-present LogRecord attributes that are NOT in the
    default-field list or the allowlist (e.g. `process`/`thread`/
    `pathname`/`args`/`msg`) must never leak into the JSON payload."""
    record = _make_record()
    parsed = json.loads(JsonFormatter().format(record))
    for standard_attr in ("process", "thread", "pathname", "args", "msg", "relativeCreated"):
        assert standard_attr not in parsed


def test_json_formatter_renders_bounded_exc_info_when_caller_set_it() -> None:
    """Exception info is only ever rendered because an existing call site
    already passed `exc_info=True` -- this formatter never sets it
    itself, and the rendered string is capped in length."""
    try:
        raise ValueError("a real exception for the test")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="app.tests.fake_module",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="failed",
        args=(),
        exc_info=exc_info,
    )
    parsed = json.loads(JsonFormatter().format(record))
    assert "exc_info" in parsed
    assert "ValueError" in parsed["exc_info"]
    assert len(parsed["exc_info"]) <= 4000 + len("... [truncated]")


def test_json_formatter_exc_info_never_exposes_unallowlisted_extra_fields() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="app.tests.fake_module",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="failed",
        args=(),
        exc_info=exc_info,
    )
    record.password = "should-never-appear"
    record.some_random_field = "also should never appear"

    parsed = json.loads(JsonFormatter().format(record))
    assert "password" not in parsed
    assert "some_random_field" not in parsed
    assert "should-never-appear" not in json.dumps(parsed)


# --- Allowlist/denylist invariants ------------------------------------


def test_allowed_extra_fields_and_sensitive_names_never_overlap() -> None:
    assert not (ALLOWED_EXTRA_FIELDS & SENSITIVE_LOG_FIELD_NAMES)
    for field in ALLOWED_EXTRA_FIELDS:
        assert not _is_sensitive_field_name(field)


def test_is_sensitive_field_name_matches_secret_key_and_api_key_suffixes() -> None:
    assert _is_sensitive_field_name("session_secret_key")
    assert _is_sensitive_field_name("SESSION_SECRET_KEY")
    assert _is_sensitive_field_name("groq_api_key")
    assert _is_sensitive_field_name("random_unrelated_field") is False


def test_allowed_extra_fields_includes_auth_event() -> None:
    """Step 187E: the one new allowlisted name -- confirms it wasn't
    accidentally left out alongside the dynamic per-field tests above,
    and that "email" (deliberately never allowlisted) still isn't."""
    assert "auth_event" in ALLOWED_EXTRA_FIELDS
    assert "email" not in ALLOWED_EXTRA_FIELDS


# --- Settings ----------------------------------------------------------


def test_log_level_default_is_info() -> None:
    field_info = Settings.model_fields["log_level"]
    assert field_info.default == "INFO"
    assert field_info.alias == "LOG_LEVEL"


def test_structured_logging_enabled_default_is_true() -> None:
    field_info = Settings.model_fields["structured_logging_enabled"]
    assert field_info.default is True
    assert field_info.alias == "STRUCTURED_LOGGING_ENABLED"


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_log_level_env_override_accepts_valid_levels(level: str) -> None:
    settings = Settings(_env_file=None, LOG_LEVEL=level)
    assert settings.log_level == level


def test_log_level_normalizes_lowercase_input() -> None:
    settings = Settings(_env_file=None, LOG_LEVEL="debug")
    assert settings.log_level == "DEBUG"


def test_invalid_log_level_normalizes_to_info_rather_than_crashing() -> None:
    settings = Settings(_env_file=None, LOG_LEVEL="NOT_A_REAL_LEVEL")
    assert settings.log_level == "INFO"


def test_settings_constructs_without_any_logging_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("STRUCTURED_LOGGING_ENABLED", raising=False)
    settings = Settings(_env_file=None)

    assert settings.log_level == "INFO"
    assert settings.structured_logging_enabled is True


# --- configure_logging() ------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_app_logger():
    """Isolates each test's view of the shared "app" logger -- removes
    whatever handler `configure_logging()` (called once already by
    `app.main`'s own import, and/or a previous test in this file) may
    have attached, then restores the logger to that same state
    afterward so later test files' own logging still behaves exactly as
    `app.main`'s real, one-time startup call configured it."""
    app_logger = logging.getLogger(APP_LOGGER_NAME)
    original_handlers = list(app_logger.handlers)
    original_level = app_logger.level
    original_propagate = app_logger.propagate
    for handler in original_handlers:
        app_logger.removeHandler(handler)
    yield
    for handler in list(app_logger.handlers):
        app_logger.removeHandler(handler)
    for handler in original_handlers:
        app_logger.addHandler(handler)
    app_logger.setLevel(original_level)
    app_logger.propagate = original_propagate


def test_configure_logging_is_idempotent_and_does_not_duplicate_handlers() -> None:
    configure_logging(log_level="INFO", structured_logging_enabled=True)
    configure_logging(log_level="INFO", structured_logging_enabled=True)
    configure_logging(log_level="DEBUG", structured_logging_enabled=True)

    app_logger = logging.getLogger(APP_LOGGER_NAME)
    matching = [h for h in app_logger.handlers if h.name == _HANDLER_NAME]
    assert len(matching) == 1


def test_configure_logging_emits_json_to_stdout_when_enabled(capsys: pytest.CaptureFixture) -> None:
    configure_logging(log_level="INFO", structured_logging_enabled=True)
    logger = logging.getLogger("app.tests.manual_emit")
    logger.warning("a real warning", extra={"trip_id": "trip_xyz"})

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["message"] == "a real warning"
    assert parsed["trip_id"] == "trip_xyz"


def test_configure_logging_falls_back_to_plain_text_when_disabled(
    capsys: pytest.CaptureFixture,
) -> None:
    configure_logging(log_level="INFO", structured_logging_enabled=False)
    logger = logging.getLogger("app.tests.manual_emit_plain")
    logger.warning("a plain warning")

    captured = capsys.readouterr()
    assert "a plain warning" in captured.out
    with pytest.raises(json.JSONDecodeError):
        json.loads(captured.out.strip().splitlines()[0])


def test_configure_logging_repeated_calls_produce_no_duplicate_log_lines(
    capsys: pytest.CaptureFixture,
) -> None:
    configure_logging(log_level="INFO", structured_logging_enabled=True)
    configure_logging(log_level="INFO", structured_logging_enabled=True)
    configure_logging(log_level="INFO", structured_logging_enabled=True)

    logger = logging.getLogger("app.tests.manual_emit_repeat")
    logger.warning("only once please")

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if "only once please" in line]
    assert len(lines) == 1


def test_configure_logging_respects_log_level_filtering(
    capsys: pytest.CaptureFixture,
) -> None:
    configure_logging(log_level="ERROR", structured_logging_enabled=True)
    logger = logging.getLogger("app.tests.manual_level_filter")
    logger.warning("should be filtered out")
    logger.error("should appear")

    captured = capsys.readouterr()
    assert "should be filtered out" not in captured.out
    assert "should appear" in captured.out


def test_configure_logging_reads_settings_when_args_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("STRUCTURED_LOGGING_ENABLED", "true")
    get_settings.cache_clear()
    try:
        configure_logging()
        app_logger = logging.getLogger(APP_LOGGER_NAME)
        assert app_logger.level == logging.DEBUG
    finally:
        get_settings.cache_clear()


# --- No-secrets-on-import regression -----------------------------------


def test_importing_main_does_not_print_secrets(capsys: pytest.CaptureFixture) -> None:
    """Importing app.main triggers this step's own `configure_logging()`
    call -- confirms that alone never prints a secret, a cookie, or any
    other sensitive value to stdout/stderr."""
    import importlib
    import sys as _sys

    _sys.modules.pop("app.main", None)
    importlib.import_module("app.main")

    captured = capsys.readouterr()
    combined = (captured.out + captured.err).lower()
    for forbidden in ("password", "secret_key", "session_secret", "api_key", "cookie"):
        assert forbidden not in combined


# --- RequestIdLogFilter (Step 187C) -------------------------------------


def test_request_id_log_filter_injects_current_request_id() -> None:
    record = _make_record()
    with request_id_scope("req_filter_test_value"):
        result = RequestIdLogFilter().filter(record)

    assert result is True
    assert record.request_id == "req_filter_test_value"


def test_request_id_log_filter_leaves_record_alone_outside_request() -> None:
    record = _make_record()
    result = RequestIdLogFilter().filter(record)

    assert result is True
    assert not hasattr(record, "request_id")


def test_request_id_log_filter_never_overrides_an_explicit_extra_value() -> None:
    record = _make_record(extra={"request_id": "req_explicitly_set"})
    with request_id_scope("req_from_context_should_be_ignored"):
        RequestIdLogFilter().filter(record)

    assert record.request_id == "req_explicitly_set"


def test_json_formatter_omits_request_id_outside_any_request_context() -> None:
    """Confirms Q6's own boundary: a log line emitted with no active
    request context must never carry a stale/invented request_id."""
    record = _make_record()
    RequestIdLogFilter().filter(record)
    parsed = json.loads(JsonFormatter().format(record))

    assert "request_id" not in parsed


def test_configure_logging_includes_request_id_in_json_output_during_a_request(
    capsys: pytest.CaptureFixture,
) -> None:
    configure_logging(log_level="INFO", structured_logging_enabled=True)
    logger = logging.getLogger("app.tests.manual_request_id_emit")

    with request_id_scope("req_end_to_end_check"):
        logger.warning("a warning emitted mid-request, no extra= passed")

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if "mid-request" in line]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["request_id"] == "req_end_to_end_check"


def test_configure_logging_omits_request_id_outside_a_request(
    capsys: pytest.CaptureFixture,
) -> None:
    configure_logging(log_level="INFO", structured_logging_enabled=True)
    logger = logging.getLogger("app.tests.manual_request_id_emit_outside")
    logger.warning("a warning with no active request context")

    captured = capsys.readouterr()
    lines = [
        line for line in captured.out.splitlines() if "no active request context" in line
    ]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert "request_id" not in parsed


def test_configure_logging_does_not_duplicate_the_request_id_filter() -> None:
    configure_logging(log_level="INFO", structured_logging_enabled=True)
    configure_logging(log_level="INFO", structured_logging_enabled=True)
    configure_logging(log_level="DEBUG", structured_logging_enabled=True)

    app_logger = logging.getLogger(APP_LOGGER_NAME)
    handler = next(h for h in app_logger.handlers if h.name == _HANDLER_NAME)
    matching_filters = [f for f in handler.filters if isinstance(f, RequestIdLogFilter)]
    assert len(matching_filters) == 1
