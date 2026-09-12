from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

# Tests for the Step 184C second Postgres schema migration
# (backend/alembic/versions/835e5782d7a5_create_users_and_trips_owner_id.py).
# Never requires a live Postgres/Docker -- static AST parsing, mirroring
# test_migration_schema.py's approach for the first migration.

_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "alembic" / "versions"
_MIGRATION_FILE = (
    _MIGRATIONS_DIR / "835e5782d7a5_create_users_and_trips_owner_id.py"
)
_FIRST_MIGRATION_REVISION = "2fe86f95d81e"


def _migration_source() -> str:
    return _MIGRATION_FILE.read_text(encoding="utf-8")


def _load_module():
    spec = importlib.util.spec_from_file_location("_users_migration_under_test", _MIGRATION_FILE)
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


def test_migration_file_exists() -> None:
    assert _MIGRATION_FILE.is_file()


def test_second_migration_chains_after_the_first() -> None:
    module = _load_module()
    assert module.down_revision == _FIRST_MIGRATION_REVISION
    assert module.revision == "835e5782d7a5"


def test_creates_exactly_one_new_table_named_users() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    assert set(tables.keys()) == {"users"}


def test_users_table_has_expected_columns() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    names = _column_names(tables["users"])
    assert names == ["user_id", "email", "password_hash", "created_at", "updated_at"]


def test_users_table_email_is_not_nullable() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    columns = {
        name: call for call, name in zip(tables["users"], _column_names(tables["users"]))
    }
    email_call = columns["email"]
    email_source = ast.unparse(email_call)
    assert "nullable=False" in email_source


def test_users_table_has_email_unique_constraint() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    users_call_args = None
    for call in _calls_named(tree, "create_table"):
        if call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == "users":
            users_call_args = call.args
            break
    assert users_call_args is not None

    unique_constraint_calls = [
        arg
        for arg in users_call_args
        if isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "UniqueConstraint"
    ]
    assert len(unique_constraint_calls) == 1
    source = ast.unparse(unique_constraint_calls[0])
    assert "email" in source


def test_users_table_has_primary_key_on_user_id() -> None:
    tree = ast.parse(_migration_source())
    for call in _calls_named(tree, "create_table"):
        if call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == "users":
            pk_calls = [
                arg
                for arg in call.args
                if isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Attribute)
                and arg.func.attr == "PrimaryKeyConstraint"
            ]
            assert len(pk_calls) == 1
            assert "user_id" in ast.unparse(pk_calls[0])
            return
    raise AssertionError("users table not found")


def test_adds_owner_id_column_to_trips() -> None:
    tree = ast.parse(_migration_source())
    add_column_calls = _calls_named(tree, "add_column")
    trips_owner_id_calls = [
        call
        for call in add_column_calls
        if call.args
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "trips"
    ]
    assert len(trips_owner_id_calls) == 1
    source = ast.unparse(trips_owner_id_calls[0])
    assert "owner_id" in source


def test_owner_id_column_is_nullable() -> None:
    """Nullable is required for backward compatibility with every
    existing `trips` row (none of which have an owner)."""
    tree = ast.parse(_migration_source())
    add_column_calls = _calls_named(tree, "add_column")
    trips_owner_id_call = next(
        call
        for call in add_column_calls
        if call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == "trips"
    )
    source = ast.unparse(trips_owner_id_call)
    assert "nullable=True" in source


def test_owner_id_foreign_key_uses_on_delete_set_null() -> None:
    tree = ast.parse(_migration_source())
    fk_calls = _calls_named(tree, "create_foreign_key")
    assert len(fk_calls) == 1
    source = ast.unparse(fk_calls[0])
    assert "users" in source
    assert "owner_id" in source
    assert "SET NULL" in source
    assert "CASCADE" not in source


def test_owner_id_has_an_index() -> None:
    tree = ast.parse(_migration_source())
    index_calls = _calls_named(tree, "create_index")
    owner_id_index_calls = [call for call in index_calls if "owner_id" in ast.unparse(call)]
    assert len(owner_id_index_calls) == 1


def test_no_owner_id_or_user_id_column_touches_planning_states() -> None:
    """Ownership lives on `trips` only -- `planning_states` must be
    completely untouched by this migration."""
    tree = ast.parse(_migration_source())
    for op_name in ("create_table", "add_column", "alter_column", "create_foreign_key"):
        for call in _calls_named(tree, op_name):
            source = ast.unparse(call)
            assert "planning_states" not in source


def test_no_sessions_table() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    assert "sessions" not in tables
    assert "user_sessions" not in tables


def test_no_role_or_admin_columns() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    all_column_names = {
        name.lower() for column_calls in tables.values() for name in _column_names(column_calls)
    }
    for forbidden in ("role", "is_admin", "admin", "permission"):
        assert forbidden not in all_column_names


def test_no_provider_cache_table() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    assert "provider_cache" not in tables


def test_downgrade_drops_owner_id_index_fk_column_and_users_table() -> None:
    tree = ast.parse(_migration_source())

    drop_index_calls = _calls_named(tree, "drop_index")
    assert any("owner_id" in ast.unparse(call) for call in drop_index_calls)

    drop_constraint_calls = _calls_named(tree, "drop_constraint")
    assert any(
        "trips" in ast.unparse(call) and "foreignkey" in ast.unparse(call)
        for call in drop_constraint_calls
    )

    drop_column_calls = _calls_named(tree, "drop_column")
    assert any(
        "trips" in ast.unparse(call) and "owner_id" in ast.unparse(call)
        for call in drop_column_calls
    )

    drop_table_calls = _calls_named(tree, "drop_table")
    dropped_table_names = {
        call.args[0].value
        for call in drop_table_calls
        if call.args and isinstance(call.args[0], ast.Constant)
    }
    assert dropped_table_names == {"users"}
