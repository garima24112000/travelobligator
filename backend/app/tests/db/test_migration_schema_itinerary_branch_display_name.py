from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

# Tests for the Section 199B sixth Postgres schema migration
# (backend/alembic/versions/cc7238a1bca3_add_itinerary_branch_display_name_.py).
# Never requires a live Postgres/Docker -- static AST parsing, mirroring
# every earlier migration test file's approach.

_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "alembic" / "versions"
_MIGRATION_FILE = (
    _MIGRATIONS_DIR / "cc7238a1bca3_add_itinerary_branch_display_name_.py"
)
_PREVIOUS_HEAD_REVISION = "7cdda8ff3506"


def _migration_source() -> str:
    return _MIGRATION_FILE.read_text(encoding="utf-8")


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "_display_name_migration_under_test", _MIGRATION_FILE
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


def test_migration_file_exists() -> None:
    assert _MIGRATION_FILE.is_file()


def test_sixth_migration_chains_after_the_fifth() -> None:
    module = _load_module()
    assert module.down_revision == _PREVIOUS_HEAD_REVISION
    assert module.revision == "cc7238a1bca3"


def test_no_create_or_drop_table() -> None:
    """This migration only adds an index -- it must never create/drop a
    table or add/drop a column."""
    tree = ast.parse(_migration_source())
    assert _calls_named(tree, "create_table") == []
    assert _calls_named(tree, "drop_table") == []
    assert _calls_named(tree, "add_column") == []
    assert _calls_named(tree, "drop_column") == []


def test_creates_exactly_one_unique_index_on_itinerary_branches() -> None:
    tree = ast.parse(_migration_source())
    index_calls = _calls_named(tree, "create_index")
    assert len(index_calls) == 1
    source = ast.unparse(index_calls[0])
    assert "uq_itinerary_branches_trip_id_display_name_lower" in source
    assert "itinerary_branches" in source
    assert "unique=True" in source
    assert "lower(display_name)" in source


def test_downgrade_drops_the_same_index() -> None:
    tree = ast.parse(_migration_source())
    drop_calls = _calls_named(tree, "drop_index")
    assert len(drop_calls) == 1
    source = ast.unparse(drop_calls[0])
    assert "uq_itinerary_branches_trip_id_display_name_lower" in source
