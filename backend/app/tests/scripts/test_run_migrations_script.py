from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

# Safety-boundary and behavior tests for the Step 188F manual/operator-
# invoked Alembic migration runner
# (docs/14_backend_architecture.md section 131). None of these tests
# require a real running Postgres -- the "real command gets invoked"
# and "failure is handled safely" checks below monkeypatch
# `alembic.command.upgrade` directly rather than actually connecting to
# anything; the one subprocess test that exercises the script as a real
# process deliberately points DATABASE_URL at an address nothing is
# listening on, so it fails fast (connection refused) rather than
# requiring live infrastructure.

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPT_PATH = _REPO_ROOT / "backend" / "scripts" / "run_migrations.py"
_ALEMBIC_INI_PATH = _REPO_ROOT / "backend" / "alembic.ini"

_FORBIDDEN_OUTPUT_SUBSTRINGS = (
    "postgresql://",
    "postgres://",
    "DATABASE_URL",
    "SESSION_SECRET_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "change_me",
    "fakepassword_dont_leak_me",
)


def _load_module() -> ModuleType:
    """Loads run_migrations.py by file path (not `import scripts.
    run_migrations`) -- mirrors the existing convention in
    test_manual_provider_cache_smoke_script.py, avoiding any
    pythonpath/namespace-package ambiguity across different pytest
    invocation directories."""
    spec = importlib.util.spec_from_file_location(
        "run_migrations_import_test", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. Script exists, at the documented path.
# ---------------------------------------------------------------------------


def test_script_exists() -> None:
    assert _SCRIPT_PATH.is_file()


def test_alembic_ini_exists_alongside_it() -> None:
    """The script resolves alembic.ini relative to its own file location
    (backend/scripts/run_migrations.py -> parents[1] == backend/) --
    confirms that path actually holds a real alembic.ini, both in this
    checkout and (by the same relative-path logic) inside the backend
    Docker image, where WORKDIR /app mirrors backend/ exactly."""
    assert _ALEMBIC_INI_PATH.is_file()


def test_script_not_collected_as_a_pytest_test_module() -> None:
    assert not _SCRIPT_PATH.name.startswith("test_")
    assert not _SCRIPT_PATH.name.endswith("_test.py")


# ---------------------------------------------------------------------------
# 2. No side effects on import -- only `main()` actually runs a migration.
# ---------------------------------------------------------------------------


def test_module_imports_cleanly_with_no_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    # Patch before import: if the module's top-level code called
    # `command.upgrade` (it must not), this would record it.
    import alembic.command as alembic_command_module

    monkeypatch.setattr(
        alembic_command_module, "upgrade", lambda *a, **k: calls.append((a, k))
    )

    module = _load_module()

    assert calls == []
    assert hasattr(module, "main")
    assert callable(module.main)
    assert hasattr(module, "run_upgrade_head")
    assert callable(module.run_upgrade_head)


# ---------------------------------------------------------------------------
# 3. main() invokes Alembic's real `upgrade` command, targeting "head".
# ---------------------------------------------------------------------------


def test_main_invokes_alembic_upgrade_head(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()

    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        module.command, "upgrade", lambda config, target: calls.append((config, target))
    )

    exit_code = module.main()

    assert exit_code == 0
    assert len(calls) == 1
    config, target = calls[0]
    assert target == "head"
    # The Config passed through is this project's own alembic.ini, not a
    # freshly-invented/duplicated one.
    assert config.config_file_name == str(_ALEMBIC_INI_PATH)

    captured = capsys.readouterr()
    assert "Migrations applied successfully" in captured.out
    for forbidden in _FORBIDDEN_OUTPUT_SUBSTRINGS:
        assert forbidden not in captured.out


# ---------------------------------------------------------------------------
# 4. A failure (any exception -- connection error, migration script
#    error, etc.) exits non-zero and never leaks the underlying error.
# ---------------------------------------------------------------------------


def test_main_exits_nonzero_on_migration_failure_without_leaking_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()

    def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(
            "connection to server at postgresql://travelobligator_user:"
            "fakepassword_dont_leak_me@10.0.0.5:5432/travelobligator failed"
        )

    monkeypatch.setattr(module.command, "upgrade", _raise)

    exit_code = module.main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Migration failed" in captured.out
    for forbidden in _FORBIDDEN_OUTPUT_SUBSTRINGS:
        assert forbidden not in captured.out
        assert forbidden not in captured.err


# ---------------------------------------------------------------------------
# 5. Source-level safety checks (never downgrade, never create_all, never
#    mutate PERSISTENCE_BACKEND, no new runtime dependency).
# ---------------------------------------------------------------------------


def _code_identifiers(tree: ast.AST) -> set[str]:
    """Every Name/Attribute/import identifier actually referenced by the
    *code* -- deliberately excludes string literals (docstrings,
    comments aren't even part of the AST), so a safety-explaining
    docstring that mentions a forbidden word in prose (e.g. "never
    calls Base.metadata.create_all") never falsely trips these checks.
    Only a real `x.downgrade`/`x.create_all`/etc. reference would."""
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                identifiers.add(alias.name)
                if alias.asname:
                    identifiers.add(alias.asname)
    return identifiers


def test_script_never_calls_downgrade() -> None:
    identifiers = _code_identifiers(ast.parse(_SCRIPT_PATH.read_text()))
    assert "downgrade" not in identifiers


def test_script_never_calls_create_all() -> None:
    identifiers = _code_identifiers(ast.parse(_SCRIPT_PATH.read_text()))
    assert "create_all" not in identifiers
    assert "metadata" not in identifiers


def test_script_never_creates_database_or_role() -> None:
    source = _SCRIPT_PATH.read_text().lower()
    for forbidden in ("create database", "create role", "create user "):
        assert forbidden not in source


def test_script_never_mutates_persistence_backend() -> None:
    identifiers = _code_identifiers(ast.parse(_SCRIPT_PATH.read_text()))
    assert "PERSISTENCE_BACKEND" not in identifiers
    assert "persistence_backend" not in identifiers
    # `os` isn't even imported (see the imports-whitelist test below),
    # so there is no code path by which this script could reach
    # `os.environ` at all, let alone mutate it.
    assert "environ" not in identifiers
    assert "putenv" not in identifiers
    assert "setenv" not in identifiers


def test_script_imports_only_stdlib_plus_alembic() -> None:
    """No new runtime dependency: only stdlib + `alembic` (already a
    pinned requirements.txt dependency since Step 183B)."""
    source = _SCRIPT_PATH.read_text()
    tree = ast.parse(source)
    allowed_top_level = {"__future__", "sys", "pathlib", "alembic"}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed_top_level, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] in allowed_top_level, node.module


def test_script_has_main_guard() -> None:
    source = _SCRIPT_PATH.read_text()
    assert 'if __name__ == "__main__":' in source
    assert "sys.exit(main())" in source


def test_script_never_prints_database_url_literally() -> None:
    """Static confirmation alongside the dynamic capsys checks above:
    the script's own *code* never references `database_url`/
    `get_settings` directly at all -- only alembic/env.py (unchanged by
    this step) does, and this script never duplicates that."""
    identifiers = _code_identifiers(ast.parse(_SCRIPT_PATH.read_text()))
    assert "database_url" not in identifiers
    assert "get_settings" not in identifiers


# ---------------------------------------------------------------------------
# 6. Real subprocess run against an address nothing is listening on --
#    exercises the script exactly as an operator would invoke it,
#    without requiring a real Postgres. Must fail fast and safely.
# ---------------------------------------------------------------------------


def test_subprocess_run_against_unreachable_postgres_fails_safely() -> None:
    env = os.environ.copy()
    env["TRAVELOB_TEST_MODE"] = "1"
    # 127.0.0.1:65535 -- nothing listens there in CI/dev; loopback means
    # the connection is refused immediately (no DNS delay, no long
    # timeout), so this test stays fast without needing live Postgres.
    # The fake password below is deliberately distinctive so the
    # forbidden-substring check below is a real, specific assertion, not
    # a vacuous one.
    env["DATABASE_URL"] = (
        "postgresql://fakeuser:fakepassword_dont_leak_me@127.0.0.1:65535/fakedb"
    )

    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH)],
        cwd=str(_REPO_ROOT / "backend"),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "Migration failed" in result.stdout
    for forbidden in _FORBIDDEN_OUTPUT_SUBSTRINGS:
        assert forbidden not in combined
