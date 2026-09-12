from __future__ import annotations

from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base
import app.db.models  # noqa: F401  -- registers TripRow/PlanningStateRow/UserRow on Base.metadata

# Tests for the Step 183C SQLAlchemy table metadata (extended in Step
# 184C with UserRow/TripRow.owner_id). Pure Python-level metadata
# inspection -- never opens a connection, never requires Postgres/Docker,
# never calls `Base.metadata.create_all`.


def test_importing_models_registers_exactly_three_tables() -> None:
    assert set(Base.metadata.tables.keys()) == {"trips", "planning_states", "users"}


def test_trip_row_columns() -> None:
    table = Base.metadata.tables["trips"]
    assert [c.name for c in table.columns] == [
        "trip_id",
        "status",
        "owner_id",
        "created_at",
        "updated_at",
    ]
    assert table.columns["trip_id"].primary_key is True
    assert table.columns["status"].nullable is False
    assert table.columns["created_at"].nullable is False
    assert table.columns["updated_at"].nullable is False


def test_trip_row_owner_id_is_nullable_for_backward_compatibility() -> None:
    table = Base.metadata.tables["trips"]
    assert table.columns["owner_id"].nullable is True


def test_trip_row_owner_id_foreign_key_uses_set_null() -> None:
    table = Base.metadata.tables["trips"]
    foreign_keys = list(table.columns["owner_id"].foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "users.user_id"
    assert foreign_keys[0].ondelete == "SET NULL"


def test_user_row_columns() -> None:
    table = Base.metadata.tables["users"]
    assert [c.name for c in table.columns] == [
        "user_id",
        "email",
        "password_hash",
        "created_at",
        "updated_at",
    ]
    assert table.columns["user_id"].primary_key is True
    assert table.columns["email"].nullable is False
    assert table.columns["email"].unique is True
    assert table.columns["password_hash"].nullable is False
    assert table.columns["created_at"].nullable is False
    assert table.columns["updated_at"].nullable is False


def test_user_row_has_no_role_or_admin_column() -> None:
    table = Base.metadata.tables["users"]
    column_names = {name.lower() for name in table.columns.keys()}
    for forbidden in ("role", "is_admin", "admin", "permission"):
        assert forbidden not in column_names


def test_planning_states_table_has_no_owner_or_user_column() -> None:
    """Ownership lives on `trips` only."""
    table = Base.metadata.tables["planning_states"]
    for column_name in table.columns.keys():
        lowered = column_name.lower()
        assert "owner_id" not in lowered
        assert "user_id" not in lowered


def test_planning_state_row_columns() -> None:
    table = Base.metadata.tables["planning_states"]
    assert [c.name for c in table.columns] == [
        "trip_id",
        "planning_state_id",
        "current_version",
        "pipeline_status",
        "state",
        "created_at",
        "updated_at",
    ]
    assert table.columns["trip_id"].primary_key is True
    for not_null_column in (
        "planning_state_id",
        "current_version",
        "pipeline_status",
        "state",
        "created_at",
        "updated_at",
    ):
        assert table.columns[not_null_column].nullable is False


def test_planning_state_row_state_column_is_postgres_jsonb() -> None:
    table = Base.metadata.tables["planning_states"]
    assert isinstance(table.columns["state"].type, JSONB)


def test_planning_state_row_has_foreign_key_to_trips_with_cascade() -> None:
    table = Base.metadata.tables["planning_states"]
    foreign_keys = list(table.columns["trip_id"].foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "trips.trip_id"
    assert foreign_keys[0].ondelete == "CASCADE"


def test_models_module_has_no_create_all_or_connection_call() -> None:
    """Importing app.db.models is pure Python class-body execution --
    registering metadata, never touching a database. Statically confirms
    the module's actual CODE (not its docstrings, which explain what it
    deliberately doesn't do) never calls `Base.metadata.create_all(...)`
    or reaches for a live engine/session/connection -- if it ever grew
    one, merely importing this module anywhere (e.g. from alembic/env.py)
    would require a live database, which is exactly what Step 183B/183C
    forbid."""
    import ast
    import inspect

    import app.db.models as models_module

    tree = ast.parse(inspect.getsource(models_module))
    forbidden_attrs = {"create_all", "get_engine", "get_session_factory", "connect"}
    called_attrs = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called_attrs.isdisjoint(forbidden_attrs)
