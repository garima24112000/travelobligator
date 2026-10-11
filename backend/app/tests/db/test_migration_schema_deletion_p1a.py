from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.base import Base
import app.db.models  # noqa: F401 - registers the tables

# P1-A migration (backend/alembic/versions/a94c0e7d2b61_...) and the foreign-key facts the deletion
# code was written against: static checks, no database. The real upgrade and the real cascades run in
# tests/repositories/test_postgres_data_deletion_p1a_integration.py.

_ALEMBIC_DIR = Path(__file__).resolve().parents[3] / "alembic"
_MIGRATION_FILE = _ALEMBIC_DIR / "versions" / "a94c0e7d2b61_add_deleted_job_usage.py"
_PREVIOUS_HEAD = "e52b7c1a9f04"
_NEW_HEAD = "a94c0e7d2b61"


def _module():
    spec = importlib.util.spec_from_file_location("_migration_p1a_under_test", _MIGRATION_FILE)
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


def test_chains_after_the_p0_head_and_is_the_single_head() -> None:
    module = _module()
    assert (module.revision, module.down_revision) == (_NEW_HEAD, _PREVIOUS_HEAD)
    config = Config()
    config.set_main_option("script_location", str(_ALEMBIC_DIR))
    assert ScriptDirectory.from_config(config).get_heads() == [_NEW_HEAD]


def test_the_migration_only_adds_one_table_and_one_index() -> None:
    for destructive in ("drop_table", "drop_column", "drop_index", "alter_column", "execute", "drop_constraint"):
        assert _calls("upgrade", destructive) == []
    (table,) = _calls("upgrade", "create_table")
    assert table.startswith("op.create_table('deleted_job_usage'")
    assert _calls("upgrade", "create_index") == [
        "op.create_index('ix_deleted_job_usage_created_at', 'deleted_job_usage', ['created_at'])"
    ]
    assert _calls("downgrade", "drop_table") == ["op.drop_table('deleted_job_usage')"]
    assert len(_calls("downgrade", "drop_index")) == 1


def test_the_usage_table_keeps_a_time_and_an_owner_and_nothing_else() -> None:
    usage = Base.metadata.tables["deleted_job_usage"]
    assert [column.name for column in usage.columns] == ["usage_id", "owner_id", "created_at"]
    assert usage.columns["owner_id"].nullable is True  # NULL once the account is deleted
    # not a foreign key on purpose: deleting the user must not delete the count
    assert usage.foreign_keys == set()
    (table,) = _calls("upgrade", "create_table")
    for forbidden in ("trip_id", "job_id", "email", "destination", "ForeignKey", "error"):
        assert forbidden not in table


def _foreign_keys(table_name: str) -> dict[str, tuple[str, str | None]]:
    table = Base.metadata.tables[table_name]
    return {
        fk.parent.name: (fk.target_fullname, fk.ondelete) for fk in table.foreign_keys
    }


def test_the_foreign_keys_the_deletion_code_was_written_against() -> None:
    """If one of these changes, `services.data_deletion_service` must be read again. It deletes every
    dependent table explicitly, so it does not RELY on these rules -- but the first one is the reason
    an account cannot be deleted by deleting only the user."""
    assert _foreign_keys("trips") == {"owner_id": ("users.user_id", "SET NULL")}
    assert _foreign_keys("planning_states") == {"trip_id": ("trips.trip_id", "CASCADE")}
    assert _foreign_keys("generation_jobs") == {
        "trip_id": ("trips.trip_id", "CASCADE"),
        "owner_id": ("users.user_id", "CASCADE"),
    }
    assert _foreign_keys("itinerary_branches") == {"trip_id": ("trips.trip_id", "CASCADE")}
    assert _foreign_keys("itinerary_revisions") == {
        "trip_id": ("trips.trip_id", "CASCADE"),
        "branch_id": ("itinerary_branches.branch_id", "CASCADE"),
        "parent_revision_id": ("itinerary_revisions.revision_id", "SET NULL"),
    }
    assert _foreign_keys("auth_attempts") == {} and _foreign_keys("deleted_job_usage") == {}


def test_every_table_is_accounted_for_by_the_deletion_code() -> None:
    """A new table that holds trip or user data must be added to the deletion service. This fails when
    a table appears that the service neither deletes nor is listed here as holding no personal data."""
    import inspect

    from app.services import data_deletion_service

    source = inspect.getsource(data_deletion_service._delete_postgres)
    deleted_by_the_service = {
        "trips": "TripRow",
        "planning_states": "PlanningStateRow",
        "generation_jobs": "GenerationJobRow",
        "itinerary_branches": "ItineraryBranchRow",
        "itinerary_revisions": "ItineraryRevisionRow",
        "users": "UserRow",
        "auth_attempts": "AuthAttemptRow",  # the account's failed sign-in rows
    }
    for row_class in deleted_by_the_service.values():
        assert f"delete({row_class})" in source
    assert set(Base.metadata.tables) == set(deleted_by_the_service) | {"deleted_job_usage"}
