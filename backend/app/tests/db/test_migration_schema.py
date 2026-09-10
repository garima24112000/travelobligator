from __future__ import annotations

import ast
from pathlib import Path

# Tests for the Step 183C initial Postgres schema migration
# (backend/alembic/versions/2fe86f95d81e_create_trips_and_planning_states.py).
# These never require a live Postgres/Docker: the migration file's source
# is statically parsed (mirroring the AST-based safety checks already used
# by e.g. test_flight_factory.py's disallowed-imports test) rather than
# executed against a real database -- `op.create_table`'s Postgres-only
# `JSONB` column type can't compile against a hermetic SQLite stand-in
# anyway, so static inspection is both simpler and more portable here.

_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "alembic" / "versions"
_MIGRATION_FILE = (
    _MIGRATIONS_DIR / "2fe86f95d81e_create_trips_and_planning_states.py"
)


def _migration_source() -> str:
    return _MIGRATION_FILE.read_text(encoding="utf-8")


def _create_table_calls(tree: ast.Module) -> dict[str, list[ast.Call]]:
    """Maps each `op.create_table("name", ...)` call's table name to its
    column-defining `sa.Column(...)` call nodes."""
    tables: dict[str, list[ast.Call]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_create_table = (
            isinstance(func, ast.Attribute)
            and func.attr == "create_table"
            and isinstance(func.value, ast.Name)
            and func.value.id == "op"
        )
        if not is_create_table or not node.args:
            continue
        table_name_node = node.args[0]
        assert isinstance(table_name_node, ast.Constant)
        table_name = table_name_node.value

        column_calls = [
            arg
            for arg in node.args[1:]
            if isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "Column"
        ]
        tables[table_name] = column_calls
    return tables


def _column_names(column_calls: list[ast.Call]) -> list[str]:
    names = []
    for call in column_calls:
        assert call.args, "sa.Column(...) call with no positional name argument"
        name_node = call.args[0]
        assert isinstance(name_node, ast.Constant)
        names.append(name_node.value)
    return names


def test_migration_file_exists() -> None:
    assert _MIGRATION_FILE.is_file()


def test_migration_is_the_root_revision() -> None:
    """The very first Postgres migration must have no down_revision."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_migration_under_test", _MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.down_revision is None
    assert module.revision == "2fe86f95d81e"


def test_upgrade_creates_only_trips_and_planning_states() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)

    assert set(tables.keys()) == {"trips", "planning_states"}


def test_no_provider_cache_table() -> None:
    """Checks the actual created table names (not the file's own
    docstring, which explicitly documents provider_cache as
    out-of-scope-for-this-step prose, not code)."""
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    assert "provider_cache" not in tables


def test_no_user_or_auth_columns() -> None:
    """Checks the actual created column names (not the file's own
    docstring prose about what this step deliberately omits)."""
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    all_column_names = {
        name.lower()
        for column_calls in tables.values()
        for name in _column_names(column_calls)
    }
    for forbidden in ("user_id", "owner_id", "auth"):
        assert forbidden not in all_column_names


def test_planning_states_state_column_is_jsonb() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    state_column_types = {
        name: call
        for call, name in zip(tables["planning_states"], _column_names(tables["planning_states"]))
    }
    state_call = state_column_types["state"]

    # The type expression is the second positional arg to sa.Column(...).
    type_node = state_call.args[1]
    type_source = ast.unparse(type_node)
    assert "JSONB" in type_source
    assert "postgresql" in type_source


def test_planning_states_has_expected_columns() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    names = _column_names(tables["planning_states"])

    assert names == [
        "trip_id",
        "planning_state_id",
        "current_version",
        "pipeline_status",
        "state",
        "created_at",
        "updated_at",
    ]


def test_trips_has_expected_columns() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    names = _column_names(tables["trips"])

    assert names == ["trip_id", "status", "created_at", "updated_at"]


def test_planning_states_has_foreign_key_to_trips_with_cascade() -> None:
    tree = ast.parse(_migration_source())

    foreign_key_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "ForeignKeyConstraint"
    ]
    assert len(foreign_key_calls) == 1
    source = ast.unparse(foreign_key_calls[0])
    assert "trips.trip_id" in source
    assert "CASCADE" in source


def test_downgrade_drops_both_tables() -> None:
    tree = ast.parse(_migration_source())
    drop_table_names = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "drop_table"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert drop_table_names == {"trips", "planning_states"}
