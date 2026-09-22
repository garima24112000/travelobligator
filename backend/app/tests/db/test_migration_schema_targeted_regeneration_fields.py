from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

# Tests for the Section 198B fourth Postgres schema migration
# (backend/alembic/versions/6f678d2b3ed9_add_targeted_regeneration_fields_to_.py).
# Never requires a live Postgres/Docker -- static AST parsing, mirroring
# test_migration_schema.py/test_migration_schema_users.py/
# test_migration_schema_generation_jobs.py's approach for the earlier
# migrations.

_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "alembic" / "versions"
_MIGRATION_FILE = (
    _MIGRATIONS_DIR / "6f678d2b3ed9_add_targeted_regeneration_fields_to_.py"
)
_PREVIOUS_HEAD_REVISION = "6fabeaa6455e"

_NEW_TEXT_NULLABLE_COLUMNS = (
    "previous_version",
    "interpretation_status",
    "execution_status",
    "clarification_reason",
)
_NEW_JSONB_LIST_COLUMNS = (
    "affected_day_indices",
    "preserved_day_indices",
    "clarification_possible_experience_ids",
)
_ALL_NEW_COLUMNS = _NEW_TEXT_NULLABLE_COLUMNS + _NEW_JSONB_LIST_COLUMNS + ("targeted", "diff")


def _migration_source() -> str:
    return _MIGRATION_FILE.read_text(encoding="utf-8")


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "_targeted_fields_migration_under_test", _MIGRATION_FILE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _calls_named(tree: ast.Module, op_attr: str) -> list[ast.Call]:
    """Every `op.<op_attr>(...)` call in the migration."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == op_attr
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    ]


def _add_column_calls_for(tree: ast.Module, table_name: str) -> list[ast.Call]:
    return [
        call
        for call in _calls_named(tree, "add_column")
        if call.args
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == table_name
    ]


def _column_name(add_column_call: ast.Call) -> str:
    column_node = add_column_call.args[1]
    assert isinstance(column_node, ast.Call)
    name_node = column_node.args[0]
    assert isinstance(name_node, ast.Constant)
    return name_node.value


def test_migration_file_exists() -> None:
    assert _MIGRATION_FILE.is_file()


def test_fourth_migration_chains_after_the_third() -> None:
    module = _load_module()
    assert module.down_revision == _PREVIOUS_HEAD_REVISION
    assert module.revision == "6f678d2b3ed9"


def test_adds_columns_only_to_generation_jobs() -> None:
    tree = ast.parse(_migration_source())
    add_column_calls = _calls_named(tree, "add_column")
    table_names = {
        call.args[0].value
        for call in add_column_calls
        if call.args and isinstance(call.args[0], ast.Constant)
    }
    assert table_names == {"generation_jobs"}


def test_adds_exactly_the_expected_columns() -> None:
    tree = ast.parse(_migration_source())
    calls = _add_column_calls_for(tree, "generation_jobs")
    names = [_column_name(call) for call in calls]
    assert set(names) == set(_ALL_NEW_COLUMNS)
    assert len(names) == len(_ALL_NEW_COLUMNS)


def test_targeted_is_not_null_boolean_with_false_default() -> None:
    tree = ast.parse(_migration_source())
    calls = _add_column_calls_for(tree, "generation_jobs")
    targeted_call = next(call for call in calls if _column_name(call) == "targeted")
    source = ast.unparse(targeted_call)
    assert "Boolean" in source
    assert "nullable=False" in source
    assert "server_default" in source
    assert "false" in source


def test_diff_is_nullable_jsonb() -> None:
    tree = ast.parse(_migration_source())
    calls = _add_column_calls_for(tree, "generation_jobs")
    diff_call = next(call for call in calls if _column_name(call) == "diff")
    source = ast.unparse(diff_call)
    assert "JSONB" in source
    assert "postgresql" in source
    assert "nullable=True" in source
    assert "server_default" not in source


def test_text_columns_are_nullable() -> None:
    tree = ast.parse(_migration_source())
    calls = _add_column_calls_for(tree, "generation_jobs")
    by_name = {_column_name(call): call for call in calls}
    for name in _NEW_TEXT_NULLABLE_COLUMNS:
        source = ast.unparse(by_name[name])
        assert "Text" in source
        assert "nullable=True" in source


def test_jsonb_list_columns_are_not_null_with_empty_array_default() -> None:
    tree = ast.parse(_migration_source())
    calls = _add_column_calls_for(tree, "generation_jobs")
    by_name = {_column_name(call): call for call in calls}
    for name in _NEW_JSONB_LIST_COLUMNS:
        source = ast.unparse(by_name[name])
        assert "JSONB" in source
        assert "postgresql" in source
        assert "nullable=False" in source
        assert "server_default" in source


def test_no_create_table_or_drop_table() -> None:
    """This migration only extends the existing `generation_jobs` table
    -- it must never create or drop a table."""
    tree = ast.parse(_migration_source())
    assert _calls_named(tree, "create_table") == []
    assert _calls_named(tree, "drop_table") == []


def test_no_secret_or_stack_trace_style_columns() -> None:
    tree = ast.parse(_migration_source())
    calls = _add_column_calls_for(tree, "generation_jobs")
    names = {_column_name(call).lower() for call in calls}
    for forbidden in ("password", "session_token", "api_key", "stack_trace", "secret"):
        assert forbidden not in names


def test_downgrade_drops_exactly_the_added_columns() -> None:
    tree = ast.parse(_migration_source())
    drop_column_calls = _calls_named(tree, "drop_column")
    dropped = {
        call.args[1].value
        for call in drop_column_calls
        if len(call.args) > 1 and isinstance(call.args[1], ast.Constant)
    }
    assert dropped == set(_ALL_NEW_COLUMNS)
