from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

# Section 200C migration (backend/alembic/versions/d41a7e2c9b53_...): static checks, no database.
_ALEMBIC_DIR = Path(__file__).resolve().parents[3] / "alembic"
_MIGRATION_FILE = _ALEMBIC_DIR / "versions" / "d41a7e2c9b53_add_lock_version_job_leases_active_job_uniqueness.py"
_PREVIOUS_HEAD = "cc7238a1bca3"
_NEW_HEAD = "d41a7e2c9b53"


def _module():
    spec = importlib.util.spec_from_file_location("_migration_200c_under_test", _MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _calls(op_attr: str) -> list[str]:
    tree = ast.parse(_MIGRATION_FILE.read_text(encoding="utf-8"))
    return [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == op_attr
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    ]


def test_chains_after_the_200a_head_and_is_the_single_head() -> None:
    module = _module()
    assert (module.revision, module.down_revision) == (_NEW_HEAD, _PREVIOUS_HEAD)
    config = Config()
    config.set_main_option("script_location", str(_ALEMBIC_DIR))
    assert ScriptDirectory.from_config(config).get_heads() == [_NEW_HEAD]


def test_adds_the_optimistic_concurrency_token_with_a_safe_default() -> None:
    columns = [c for c in _calls("add_column") if "planning_states" in c]
    assert len(columns) == 1
    assert "lock_version" in columns[0] and "nullable=False" in columns[0] and "server_default='0'" in columns[0]
    checks = " ".join(_calls("create_check_constraint"))
    assert "lock_version >= 0" in checks


def test_adds_nullable_lease_columns_only() -> None:
    columns = " ".join(c for c in _calls("add_column") if "generation_jobs" in c)
    for name in ("lease_owner", "lease_expires_at", "heartbeat_at"):
        assert name in columns
    assert "nullable=False" not in columns  # existing rows must stay valid


def test_creates_the_partial_unique_active_job_index() -> None:
    (index,) = _calls("create_index")
    assert "uq_generation_jobs_one_active_per_trip" in index
    assert "unique=True" in index and "'trip_id'" in index
    assert "_ACTIVE_JOB_PREDICATE" in index
    assert "status IN ('queued', 'running')" in _MIGRATION_FILE.read_text(encoding="utf-8")


def test_upgrade_first_closes_pre_existing_duplicate_active_jobs() -> None:
    source = _MIGRATION_FILE.read_text(encoding="utf-8")
    assert source.index("JOB_INTERRUPTED") < source.index("op.create_index")  # dedupe BEFORE the index
    assert "DISTINCT ON (trip_id)" in source  # keeps the OLDEST active job per trip


def test_never_creates_or_drops_a_table_and_downgrade_is_symmetric() -> None:
    assert _calls("create_table") == [] and _calls("drop_table") == []
    assert len(_calls("drop_index")) == 1
    dropped = " ".join(_calls("drop_column"))
    for name in ("lock_version", "lease_owner", "lease_expires_at", "heartbeat_at"):
        assert name in dropped
    assert "ck_planning_states_lock_version_non_negative" in " ".join(_calls("drop_constraint"))


def test_model_metadata_matches_the_migration() -> None:
    from app.db.base import Base
    import app.db.models  # noqa: F401

    states = Base.metadata.tables["planning_states"]
    assert "lock_version" in states.columns and states.columns["lock_version"].nullable is False
    jobs = Base.metadata.tables["generation_jobs"]
    assert {"lease_owner", "lease_expires_at", "heartbeat_at"} <= set(jobs.columns.keys())
    index = next(i for i in jobs.indexes if i.name == "uq_generation_jobs_one_active_per_trip")
    assert index.unique and [c.name for c in index.columns] == ["trip_id"]
