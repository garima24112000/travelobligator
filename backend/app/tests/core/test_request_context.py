from __future__ import annotations

import pytest

from app.core.request_context import (
    MAX_REQUEST_ID_LENGTH,
    get_current_request_id,
    is_safe_request_id,
    new_request_id,
    normalize_request_id,
    request_id_scope,
    reset_current_request_id,
    set_current_request_id,
)

# Tests for the Step 187C request-scoped correlation id foundation
# (backend/app/core/request_context.py, docs/14_backend_architecture.md
# section 122). Pure unit tests -- no HTTP client, no app import needed;
# see test_request_id_correlation.py (backend/app/tests/api/) for the
# end-to-end header/metadata/log correlation tests.


def test_new_request_id_uses_req_prefix() -> None:
    value = new_request_id()
    assert value.startswith("req_")
    assert len(value) == len("req_") + 32  # uuid4().hex is 32 hex chars


def test_new_request_id_is_unique_across_calls() -> None:
    assert new_request_id() != new_request_id()


@pytest.mark.parametrize(
    "value",
    [
        "req_abc123",
        "simple-id",
        "simple_id",
        "simple.id",
        "trace:12345",
        "ABCDEF0123456789",
        "a",
    ],
)
def test_is_safe_request_id_accepts_valid_ids(value: str) -> None:
    assert is_safe_request_id(value) is True


@pytest.mark.parametrize("value", [None, ""])
def test_is_safe_request_id_rejects_none_and_empty(value: str | None) -> None:
    assert is_safe_request_id(value) is False


def test_is_safe_request_id_rejects_too_long() -> None:
    too_long = "a" * (MAX_REQUEST_ID_LENGTH + 1)
    assert is_safe_request_id(too_long) is False


def test_is_safe_request_id_accepts_exactly_max_length() -> None:
    exactly_max = "a" * MAX_REQUEST_ID_LENGTH
    assert is_safe_request_id(exactly_max) is True


@pytest.mark.parametrize(
    "value",
    [
        "has space",
        "has\nnewline",
        "has\ttab",
        "has\rcarriage",
        "has\x00null",
        "has,comma",
        "has;semicolon",
        "has/slash",
        "has\\backslash",
        "has\"quote",
        "has<angle>",
    ],
)
def test_is_safe_request_id_rejects_whitespace_and_special_characters(value: str) -> None:
    assert is_safe_request_id(value) is False


def test_normalize_request_id_honors_a_safe_incoming_value() -> None:
    assert normalize_request_id("client-supplied-id-123") == "client-supplied-id-123"


def test_normalize_request_id_replaces_missing_value() -> None:
    result = normalize_request_id(None)
    assert is_safe_request_id(result)
    assert result.startswith("req_")


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "",
        "has space",
        "has\nnewline",
        "has\x00null",
        "a" * (MAX_REQUEST_ID_LENGTH + 1),
    ],
)
def test_normalize_request_id_replaces_unsafe_value(unsafe_value: str) -> None:
    result = normalize_request_id(unsafe_value)
    assert result != unsafe_value
    assert is_safe_request_id(result)
    assert result.startswith("req_")


def test_get_current_request_id_defaults_to_none() -> None:
    assert get_current_request_id() is None


def test_set_and_reset_current_request_id_round_trips() -> None:
    assert get_current_request_id() is None
    token = set_current_request_id("req_test_roundtrip")
    try:
        assert get_current_request_id() == "req_test_roundtrip"
    finally:
        reset_current_request_id(token)
    assert get_current_request_id() is None


def test_request_id_scope_sets_and_resets() -> None:
    assert get_current_request_id() is None
    with request_id_scope("req_scoped_value") as value:
        assert value == "req_scoped_value"
        assert get_current_request_id() == "req_scoped_value"
    assert get_current_request_id() is None


def test_request_id_scope_resets_even_on_exception() -> None:
    assert get_current_request_id() is None
    with pytest.raises(RuntimeError):
        with request_id_scope("req_will_raise"):
            assert get_current_request_id() == "req_will_raise"
            raise RuntimeError("simulated failure inside the scope")
    assert get_current_request_id() is None


def test_nested_scopes_restore_the_outer_value() -> None:
    with request_id_scope("req_outer"):
        assert get_current_request_id() == "req_outer"
        with request_id_scope("req_inner"):
            assert get_current_request_id() == "req_inner"
        assert get_current_request_id() == "req_outer"
    assert get_current_request_id() is None
