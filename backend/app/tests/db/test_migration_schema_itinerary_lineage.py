from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

# Tests for the Section 199A fifth Postgres schema migration
# (backend/alembic/versions/7cdda8ff3506_create_itinerary_branches_and_revisions.py).
# Never requires a live Postgres/Docker -- static AST parsing, mirroring
# test_migration_schema_generation_jobs.py's approach for the third
# migration (the most recent `create_table`-style one).

_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "alembic" / "versions"
_MIGRATION_FILE = (
    _MIGRATIONS_DIR / "7cdda8ff3506_create_itinerary_branches_and_revisions.py"
)
_PREVIOUS_HEAD_REVISION = "6f678d2b3ed9"


def _migration_source() -> str:
    return _MIGRATION_FILE.read_text(encoding="utf-8")


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "_itinerary_lineage_migration_under_test", _MIGRATION_FILE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _calls_named(tree: ast.Module, op_attr: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == op_attr
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    ]


def _create_table_calls(tree: ast.Module) -> dict[str, list[ast.Call]]:
    tables: dict[str, list[ast.Call]] = {}
    for call in _calls_named(tree, "create_table"):
        if not call.args:
            continue
        table_name_node = call.args[0]
        assert isinstance(table_name_node, ast.Constant)
        column_calls = [
            arg
            for arg in call.args[1:]
            if isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "Column"
        ]
        tables[table_name_node.value] = column_calls
    return tables


def _column_names(column_calls: list[ast.Call]) -> list[str]:
    names = []
    for call in column_calls:
        assert call.args
        name_node = call.args[0]
        assert isinstance(name_node, ast.Constant)
        names.append(name_node.value)
    return names


def _columns_by_name(column_calls: list[ast.Call]) -> dict[str, ast.Call]:
    return {name: call for call, name in zip(column_calls, _column_names(column_calls))}


def test_migration_file_exists() -> None:
    assert _MIGRATION_FILE.is_file()


def test_fifth_migration_chains_after_the_fourth() -> None:
    module = _load_module()
    assert module.down_revision == _PREVIOUS_HEAD_REVISION
    assert module.revision == "7cdda8ff3506"


def test_creates_exactly_two_new_tables() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    assert set(tables.keys()) == {"itinerary_branches", "itinerary_revisions"}


def test_itinerary_branches_has_expected_columns() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    assert _column_names(tables["itinerary_branches"]) == [
        "branch_id",
        "trip_id",
        "display_name",
        "is_default",
        "base_revision_id",
        "head_revision_id",
        "created_at",
    ]


def test_itinerary_revisions_has_expected_columns() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    assert _column_names(tables["itinerary_revisions"]) == [
        "revision_id",
        "trip_id",
        "branch_id",
        "parent_revision_id",
        "version_label",
        "created_by",
        "created_at",
        "feedback_event_id",
        "version_history_item_id",
        "snapshot_available",
        "snapshot",
    ]


def test_itinerary_branches_required_columns_are_not_nullable() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    columns = _columns_by_name(tables["itinerary_branches"])
    for required in ("branch_id", "trip_id", "display_name", "is_default", "created_at"):
        assert "nullable=False" in ast.unparse(columns[required]), f"{required} should be NOT NULL"
    for optional in ("base_revision_id", "head_revision_id"):
        assert "nullable=True" in ast.unparse(columns[optional]), f"{optional} should be nullable"


def test_itinerary_revisions_required_columns_are_not_nullable() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    columns = _columns_by_name(tables["itinerary_revisions"])
    for required in (
        "revision_id",
        "trip_id",
        "branch_id",
        "version_label",
        "created_by",
        "created_at",
        "snapshot_available",
    ):
        assert "nullable=False" in ast.unparse(columns[required]), f"{required} should be NOT NULL"
    for optional in (
        "parent_revision_id",
        "feedback_event_id",
        "version_history_item_id",
        "snapshot",
    ):
        assert "nullable=True" in ast.unparse(columns[optional]), f"{optional} should be nullable"


def test_snapshot_column_is_jsonb() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    columns = _columns_by_name(tables["itinerary_revisions"])
    source = ast.unparse(columns["snapshot"])
    assert "JSONB" in source
    assert "postgresql" in source


def test_itinerary_revisions_unique_constraint_on_branch_and_version_label() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    revisions_call = next(
        call
        for call in _calls_named(tree, "create_table")
        if call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == "itinerary_revisions"
    )
    unique_constraint_calls = [
        arg
        for arg in revisions_call.args
        if isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "UniqueConstraint"
    ]
    assert len(unique_constraint_calls) == 1
    source = ast.unparse(unique_constraint_calls[0])
    assert "'branch_id'" in source
    assert "'version_label'" in source
    del tables  # only used to assert table existence indirectly above


def test_itinerary_branches_partial_unique_index_on_default_per_trip() -> None:
    tree = ast.parse(_migration_source())
    index_calls = _calls_named(tree, "create_index")
    default_index_calls = [
        call for call in index_calls if "uq_itinerary_branches_default_per_trip" in ast.unparse(call)
    ]
    assert len(default_index_calls) == 1
    source = ast.unparse(default_index_calls[0])
    assert "unique=True" in source
    assert "is_default" in source


def test_foreign_key_constraints() -> None:
    tree = ast.parse(_migration_source())
    revisions_call = next(
        call
        for call in _calls_named(tree, "create_table")
        if call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == "itinerary_revisions"
    )
    fk_calls = [
        arg
        for arg in revisions_call.args
        if isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "ForeignKeyConstraint"
    ]
    assert len(fk_calls) == 3

    branches_call = next(
        call
        for call in _calls_named(tree, "create_table")
        if call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == "itinerary_branches"
    )
    branch_fk_calls = [
        arg
        for arg in branches_call.args
        if isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "ForeignKeyConstraint"
    ]
    assert len(branch_fk_calls) == 1
    assert "trips.trip_id" in ast.unparse(branch_fk_calls[0])
    assert "CASCADE" in ast.unparse(branch_fk_calls[0])

    parent_fk = next(call for call in fk_calls if "itinerary_revisions.revision_id" in ast.unparse(call))
    assert "SET NULL" in ast.unparse(parent_fk)


def test_no_secret_or_stack_trace_style_columns() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    all_column_names = {
        name.lower() for column_calls in tables.values() for name in _column_names(column_calls)
    }
    for forbidden in ("password", "session_token", "api_key", "stack_trace", "secret"):
        assert forbidden not in all_column_names


def test_downgrade_drops_both_tables() -> None:
    tree = ast.parse(_migration_source())
    drop_table_calls = _calls_named(tree, "drop_table")
    dropped_table_names = {
        call.args[0].value
        for call in drop_table_calls
        if call.args and isinstance(call.args[0], ast.Constant)
    }
    assert dropped_table_names == {"itinerary_branches", "itinerary_revisions"}
