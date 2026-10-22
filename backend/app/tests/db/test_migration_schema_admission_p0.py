from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.base import Base
import app.db.models  # noqa: F401 - registers the tables

# P0 migration (backend/alembic/versions/e52b7c1a9f04_...): static checks, no database. The real upgrade
# / downgrade runs in tests/repositories/test_postgres_admission_p0_integration.py.

_ALEMBIC_DIR = Path(__file__).resolve().parents[3] / "alembic"
_MIGRATION_FILE = _ALEMBIC_DIR / "versions" / "e52b7c1a9f04_add_admission_limit_indexes_and_auth_attempts.py"
_PREVIOUS_HEAD = "d41a7e2c9b53"
_NEW_HEAD = "e52b7c1a9f04"


def _module():
    spec = importlib.util.spec_from_file_location("_migration_p0_under_test", _MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _calls(function_name: str, op_attr: str) -> list[str]:
    tree = ast.parse(_MIGRATION_FILE.read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function_name)
    return [
        ast.unparse(node)
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == op_attr
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    ]


def test_chains_after_the_200c_head_in_one_linear_history() -> None:
    module = _module()
    assert (module.revision, module.down_revision) == (_NEW_HEAD, _PREVIOUS_HEAD)
    config = Config()
    config.set_main_option("script_location", str(_ALEMBIC_DIR))
    # Still one linear history; the head moved on with the P1-A migration, which chains after this one
    # (tests/db/test_migration_schema_deletion_p1a.py).
    scripts = ScriptDirectory.from_config(config)
    assert len(scripts.get_heads()) == 1
    assert _NEW_HEAD in {revision.revision for revision in scripts.walk_revisions()}


def test_the_migration_only_adds() -> None:
    for destructive in ("drop_table", "drop_column", "drop_index", "alter_column", "execute", "drop_constraint"):
        assert _calls("upgrade", destructive) == []
    assert len(_calls("upgrade", "create_table")) == 1
    assert len(_calls("upgrade", "create_index")) == 3


def test_it_adds_the_two_window_count_indexes() -> None:
    created = _calls("upgrade", "create_index")
    assert "op.create_index('ix_generation_jobs_created_at', 'generation_jobs', ['created_at'])" in created
    assert (
        "op.create_index('ix_generation_jobs_owner_id_created_at', 'generation_jobs', ['owner_id', 'created_at'])"
        in created
    )


def test_the_attempts_table_can_hold_no_personal_data() -> None:
    (table,) = _calls("upgrade", "create_table")
    assert table.startswith("op.create_table('auth_attempts'")
    for column in ("attempt_id", "kind", "subject_hash", "ip_hash", "created_at"):
        assert f"sa.Column('{column}'" in table
    for forbidden in ("email", "user_id", "password", "address", "ForeignKey"):
        assert forbidden not in table
    assert "op.create_index('ix_auth_attempts_kind_created_at', 'auth_attempts', ['kind', 'created_at'])" in _calls(
        "upgrade", "create_index"
    )


def test_the_downgrade_undoes_exactly_what_the_upgrade_added() -> None:
    assert _calls("downgrade", "drop_table") == ["op.drop_table('auth_attempts')"]
    dropped = _calls("downgrade", "drop_index")
    assert len(dropped) == 3
    for index in ("ix_auth_attempts_kind_created_at", "ix_generation_jobs_owner_id_created_at", "ix_generation_jobs_created_at"):
        assert any(index in call for call in dropped)


def test_the_models_describe_the_same_schema() -> None:
    attempts = Base.metadata.tables["auth_attempts"]
    assert [column.name for column in attempts.columns] == [
        "attempt_id",
        "kind",
        "subject_hash",
        "ip_hash",
        "created_at",
    ]
    assert [column.name for column in attempts.primary_key.columns] == ["attempt_id"]
    assert attempts.foreign_keys == set()
    assert {index.name for index in attempts.indexes} == {"ix_auth_attempts_kind_created_at"}

    jobs = Base.metadata.tables["generation_jobs"]
    by_name = {index.name: [column.name for column in index.columns] for index in jobs.indexes}
    assert by_name["ix_generation_jobs_created_at"] == ["created_at"]
    assert by_name["ix_generation_jobs_owner_id_created_at"] == ["owner_id", "created_at"]
    # the per-trip rule the limits build on is untouched
    assert by_name["uq_generation_jobs_one_active_per_trip"] == ["trip_id"]
