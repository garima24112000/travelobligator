from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

# Tests for the Step 186F third Postgres schema migration
# (backend/alembic/versions/6fabeaa6455e_create_generation_jobs.py). Never
# requires a live Postgres/Docker -- static AST parsing, mirroring
# test_migration_schema.py/test_migration_schema_users.py's approach for
# the earlier two migrations.

_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "alembic" / "versions"
_MIGRATION_FILE = _MIGRATIONS_DIR / "6fabeaa6455e_create_generation_jobs.py"
_PREVIOUS_HEAD_REVISION = "835e5782d7a5"


def _migration_source() -> str:
    return _MIGRATION_FILE.read_text(encoding="utf-8")


def _load_module():
    spec = importlib.util.spec_from_file_location("_jobs_migration_under_test", _MIGRATION_FILE)
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


def test_third_migration_chains_after_the_second() -> None:
    module = _load_module()
    assert module.down_revision == _PREVIOUS_HEAD_REVISION
    assert module.revision == "6fabeaa6455e"


def test_creates_exactly_one_new_table_named_generation_jobs() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    assert set(tables.keys()) == {"generation_jobs"}


def test_generation_jobs_table_has_expected_columns() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    names = _column_names(tables["generation_jobs"])
    assert names == [
        "job_id",
        "trip_id",
        "owner_id",
        "job_type",
        "status",
        "progress_stage",
        "message",
        "error_code",
        "error_message",
        "created_at",
        "started_at",
        "finished_at",
        "result_version",
        "changed_sections",
    ]


def test_required_columns_are_not_nullable() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    columns = _columns_by_name(tables["generation_jobs"])
    for required in (
        "job_id",
        "trip_id",
        "owner_id",
        "job_type",
        "status",
        "created_at",
        "changed_sections",
    ):
        source = ast.unparse(columns[required])
        assert "nullable=False" in source, f"{required} should be NOT NULL"


def test_optional_columns_are_nullable() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    columns = _columns_by_name(tables["generation_jobs"])
    for optional in (
        "progress_stage",
        "message",
        "error_code",
        "error_message",
        "started_at",
        "finished_at",
        "result_version",
    ):
        source = ast.unparse(columns[optional])
        assert "nullable=True" in source, f"{optional} should be nullable"


def test_result_version_is_text_not_integer() -> None:
    """Deliberate deviation: mirrors `GenerationJob.result_version: str |
    None` (a version label like "v2"), never a numeric id."""
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    columns = _columns_by_name(tables["generation_jobs"])
    source = ast.unparse(columns["result_version"])
    assert "Text" in source
    assert "Integer" not in source


def test_changed_sections_is_jsonb_with_empty_list_default() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    columns = _columns_by_name(tables["generation_jobs"])
    source = ast.unparse(columns["changed_sections"])
    assert "JSONB" in source
    assert "postgresql" in source
    assert "server_default" in source


def test_trip_id_foreign_key_uses_on_delete_cascade() -> None:
    tree = ast.parse(_migration_source())
    fk_calls = _calls_named(tree, "create_table")
    generation_jobs_call = next(
        call
        for call in fk_calls
        if call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == "generation_jobs"
    )
    fk_constraint_calls = [
        arg
        for arg in generation_jobs_call.args
        if isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "ForeignKeyConstraint"
    ]
    trip_fk = next(call for call in fk_constraint_calls if "trips.trip_id" in ast.unparse(call))
    source = ast.unparse(trip_fk)
    assert "CASCADE" in source


def test_owner_id_foreign_key_uses_on_delete_cascade() -> None:
    tree = ast.parse(_migration_source())
    create_table_calls = _calls_named(tree, "create_table")
    generation_jobs_call = next(
        call
        for call in create_table_calls
        if call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == "generation_jobs"
    )
    fk_constraint_calls = [
        arg
        for arg in generation_jobs_call.args
        if isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "ForeignKeyConstraint"
    ]
    owner_fk = next(call for call in fk_constraint_calls if "users.user_id" in ast.unparse(call))
    source = ast.unparse(owner_fk)
    assert "CASCADE" in source


def test_generation_jobs_has_exactly_two_foreign_keys() -> None:
    tree = ast.parse(_migration_source())
    create_table_calls = _calls_named(tree, "create_table")
    generation_jobs_call = next(
        call
        for call in create_table_calls
        if call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == "generation_jobs"
    )
    fk_constraint_calls = [
        arg
        for arg in generation_jobs_call.args
        if isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "ForeignKeyConstraint"
    ]
    assert len(fk_constraint_calls) == 2


def test_generation_jobs_has_primary_key_on_job_id() -> None:
    tree = ast.parse(_migration_source())
    create_table_calls = _calls_named(tree, "create_table")
    generation_jobs_call = next(
        call
        for call in create_table_calls
        if call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == "generation_jobs"
    )
    pk_calls = [
        arg
        for arg in generation_jobs_call.args
        if isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "PrimaryKeyConstraint"
    ]
    assert len(pk_calls) == 1
    assert "job_id" in ast.unparse(pk_calls[0])


def test_expected_indexes_exist() -> None:
    tree = ast.parse(_migration_source())
    index_calls = _calls_named(tree, "create_index")
    index_sources = [ast.unparse(call) for call in index_calls]

    def _has_index(name: str) -> bool:
        return any(name in source for source in index_sources)

    assert _has_index("ix_generation_jobs_trip_id")
    assert _has_index("ix_generation_jobs_owner_id")
    assert _has_index("ix_generation_jobs_status")

    composite = next(
        source for source in index_sources if "ix_generation_jobs_trip_id_status" in source
    )
    assert "'trip_id'" in composite
    assert "'status'" in composite


def test_no_provider_cache_table() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    assert "provider_cache" not in tables


def test_no_secret_or_stack_trace_style_columns() -> None:
    tree = ast.parse(_migration_source())
    tables = _create_table_calls(tree)
    all_column_names = {
        name.lower() for column_calls in tables.values() for name in _column_names(column_calls)
    }
    for forbidden in ("password", "session_token", "api_key", "stack_trace", "secret"):
        assert forbidden not in all_column_names


def test_downgrade_drops_indexes_and_table() -> None:
    tree = ast.parse(_migration_source())

    drop_index_calls = _calls_named(tree, "drop_index")
    dropped_index_names = {
        call.args[0].value
        for call in drop_index_calls
        if call.args and isinstance(call.args[0], ast.Constant)
    }
    assert dropped_index_names == {
        "ix_generation_jobs_trip_id",
        "ix_generation_jobs_owner_id",
        "ix_generation_jobs_status",
        "ix_generation_jobs_trip_id_status",
    }

    drop_table_calls = _calls_named(tree, "drop_table")
    dropped_table_names = {
        call.args[0].value
        for call in drop_table_calls
        if call.args and isinstance(call.args[0], ast.Constant)
    }
    assert dropped_table_names == {"generation_jobs"}
