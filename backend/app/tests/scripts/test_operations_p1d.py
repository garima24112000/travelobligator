from __future__ import annotations

import ast
import http.server
import json
import re
import threading
from pathlib import Path

import pytest
import yaml

from app.db.models import Base
from scripts import db_backup_restore as backup_tool
from scripts import ops_report, public_check

# P1-D, hermetic (no database, no network beyond 127.0.0.1): the operator scripts' refusals and judgement,
# the rules the runbook relies on, and that the documents and workflows say what the repository does.
# The procedures themselves run for real in test_backup_restore_drill_p1d_integration.py.

_ROOT = Path(__file__).resolve().parents[4]
_VERSIONS = _ROOT / "backend" / "alembic" / "versions"
_RUNBOOK = (_ROOT / "docs" / "25_operations_runbook.md").read_text()
_CI = yaml.safe_load((_ROOT / ".github" / "workflows" / "ci.yml").read_text())
_UPTIME_TEXT = (_ROOT / ".github" / "workflows" / "uptime.yml").read_text()
_UPTIME = yaml.safe_load(_UPTIME_TEXT)

# The project instructions file is tracked as `Claude.md`; a case-insensitive file system also answers to
# `CLAUDE.md`, a case-sensitive one (CI) does not. Read whichever name the repository really has.
_INSTRUCTIONS = next(path for path in _ROOT.iterdir() if path.name.lower() == "claude.md").read_text()

# The revision production runs (docs/23_v1_release_reference.md). Change it, here and in the runbook, in
# the same commit that records a deployment.
PRODUCTION_REVISION = "d41a7e2c9b53"
SECRET_URL = "postgresql://operator:s3cr3t-p4ss@db.example.test:5432/appdb?sslmode=require"


# -- migrations --------------------------------------------------------------------------------------


def _migrations() -> dict[str, dict]:
    found = {}
    for path in _VERSIONS.glob("*.py"):
        tree = ast.parse(path.read_text())
        values = {
            target.id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
            if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}
        }
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        found[values["revision"]] = {"down": values["down_revision"], "functions": functions, "path": path}
    return found


def _operations(function: ast.FunctionDef) -> list[str]:
    return [
        node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "op"
    ]


def _chain_after(revision: str) -> list[str]:
    migrations = _migrations()
    children = {entry["down"]: name for name, entry in migrations.items()}
    chain = []
    while revision in children:
        revision = children[revision]
        chain.append(revision)
    return chain


def test_there_is_one_linear_migration_history_and_the_runbook_names_its_ends() -> None:
    migrations = _migrations()
    roots = [name for name, entry in migrations.items() if entry["down"] is None]
    assert len(roots) == 1
    full_chain = [roots[0], *_chain_after(roots[0])]
    assert len(full_chain) == len(migrations)  # no branch, no orphan
    head = full_chain[-1]
    assert backup_tool.code_heads() == [head]

    pending = _chain_after(PRODUCTION_REVISION)
    assert pending, "production's revision must be an ancestor of the head"
    # the runbook's revision table is the one an operator reads during a deployment
    assert f"| Production (commit `f55401c`) | `{PRODUCTION_REVISION}` |" in _RUNBOOK
    assert f"| Head of this branch | `{head}` |" in _RUNBOOK
    for revision in pending:
        assert f"`{revision}`" in _RUNBOOK
    # ... in the order they are applied
    positions = [_RUNBOOK.index(f"`{revision}` (") for revision in pending]
    assert positions == sorted(positions)
    # the rollback command goes back to exactly production's revision
    assert f"alembic downgrade {PRODUCTION_REVISION}" in _RUNBOOK


def test_every_migration_since_production_only_adds_and_its_downgrade_only_removes_what_it_added() -> None:
    """The rollback runbook (section 4.3) promises that going back loses only the new tables. That holds
    only while a migration adds tables and indexes and nothing else. A migration that alters or drops
    something existing must not ship before that section is rewritten -- and this test changed with it."""
    additive = {"create_table", "create_index"}
    removing = {"drop_table", "drop_index"}
    migrations = _migrations()
    for revision in _chain_after(PRODUCTION_REVISION):
        upgrade = _operations(migrations[revision]["functions"]["upgrade"])
        downgrade = _operations(migrations[revision]["functions"]["downgrade"])
        assert upgrade and set(upgrade) <= additive, (revision, upgrade)
        assert set(downgrade) <= removing, (revision, downgrade)
        assert upgrade.count("create_table") == downgrade.count("drop_table"), revision
        assert upgrade.count("create_index") == downgrade.count("drop_index"), revision
        source = migrations[revision]["path"].read_text()
        assert "op.execute" not in source and "batch_alter_table" not in source, revision


def test_every_migration_can_be_downgraded() -> None:
    for revision, entry in _migrations().items():
        assert _operations(entry["functions"]["downgrade"]), f"{revision} has an empty downgrade"


# -- the backup tool ---------------------------------------------------------------------------------


def test_the_backup_tool_knows_every_table_of_the_application() -> None:
    assert set(backup_tool.APPLICATION_TABLES) == set(Base.metadata.tables)
    for _, tables, query in backup_tool.INTEGRITY_CHECKS:
        assert set(tables) <= set(backup_tool.APPLICATION_TABLES)
        assert query.lstrip().lower().startswith("select count(*)")  # read-only, one number


def test_each_command_reads_its_own_variable_and_never_the_applications(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    names = {backup_tool.BACKUP_SOURCE_ENV, backup_tool.RESTORE_TARGET_ENV, backup_tool.VERIFY_ENV, ops_report.DATABASE_ENV}
    assert len(names) == 4 and "DATABASE_URL" not in names
    for name in (*names, "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", SECRET_URL)  # present, and must be ignored by every command

    assert backup_tool.main(["backup", "--out", str(tmp_path / "out")]) == 2
    dump = _dump_with_manifest(tmp_path)
    assert backup_tool.main(["restore", "--dump", str(dump), "--confirm-empty-target", "appdb"]) == 2
    with pytest.raises(SystemExit):  # the operator must name the target database: there is no default
        backup_tool.main(["restore", "--dump", str(dump)])
    assert backup_tool.main(["verify"]) == 2
    assert ops_report.main([]) == 3
    printed = capsys.readouterr()
    for name in names:
        assert f"{name} is not set." in printed.err
    assert "s3cr3t" not in printed.out + printed.err and "db.example.test" not in printed.out + printed.err
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


def test_a_backup_is_never_written_inside_the_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(backup_tool.BACKUP_SOURCE_ENV, SECRET_URL)
    for inside in (_ROOT, _ROOT / "backups", _ROOT / "backend" / ".data" / "backups"):
        with pytest.raises(backup_tool.OperatorError, match="inside the repository"):
            backup_tool.backup(inside)  # refused before any connection is attempted
        assert not (inside / "travelobligator").exists()


def _dump_with_manifest(directory: Path, name: str = "travelobligator-x.dump") -> Path:
    """A stand-in dump with a correct manifest beside it, for refusals that happen before any restore."""
    import hashlib

    directory.mkdir(parents=True, exist_ok=True)
    dump = directory / name
    dump.write_bytes(b"not a real archive")
    manifest = {
        "manifest_version": backup_tool.MANIFEST_VERSION,
        "dump_file": dump.name,
        "dump_sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
        "alembic_revisions": ["x"],
        "tables": {},
    }
    dump.with_name(dump.name.removesuffix(".dump") + ".manifest.json").write_text(json.dumps(manifest))
    return dump


@pytest.mark.parametrize(
    "other_spelling",
    [
        "postgresql://operator:s3cr3t-p4ss@db.example.test:5432/appdb?sslmode=require",  # identical
        "postgresql+psycopg://someone-else@db.example.test/appdb",  # driver prefix, default port, other credentials
        "postgres://operator@DB.Example.Test.:5432/appdb",  # letter case and a trailing dot
        "postgresql://operator@db-pooler.example.test/appdb",  # Neon's pooled host for the same endpoint
    ],
)
def test_a_restore_onto_the_source_or_the_applications_database_is_refused_before_connecting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, other_spelling: str
) -> None:
    dump = _dump_with_manifest(tmp_path)
    monkeypatch.setattr(backup_tool, "_engine", lambda env_name: pytest.fail("a connection was attempted"))
    monkeypatch.setenv(backup_tool.RESTORE_TARGET_ENV, SECRET_URL)
    for other in (backup_tool.BACKUP_SOURCE_ENV, "DATABASE_URL"):
        monkeypatch.delenv(backup_tool.BACKUP_SOURCE_ENV, raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv(other, other_spelling)
        with pytest.raises(backup_tool.OperatorError, match=f"points at the same database as {other}"):
            backup_tool.restore(dump, "appdb")


def test_the_names_of_this_machine_are_one_place_and_different_places_stay_different(monkeypatch: pytest.MonkeyPatch) -> None:
    def address(url: str):
        monkeypatch.setenv(backup_tool.VERIFY_ENV, url)
        return backup_tool._written_address(backup_tool.VERIFY_ENV)

    local = {address(f"postgresql://u@{host}/db") for host in ("localhost", "127.0.0.1", "LOCALHOST", "[::1]", "127.0.0.1:5432")}
    assert len(local) == 1
    # a different port, database or host is a different place: the comparison must not over-match either
    assert len({address("postgresql://u@h.example.test/db"), address("postgresql://u@h.example.test:6543/db"), address("postgresql://u@h.example.test/other"), address("postgresql://u@i.example.test/db")}) == 4
    # only the first label loses `-pooler`; a host that merely contains the word is left alone
    assert address("postgresql://u@ep-a-pooler.region.example.test/db") == address("postgresql://u@ep-a.region.example.test/db")
    assert address("postgresql://u@my-pooler-host.example.test/db") != address("postgresql://u@my-host.example.test/db")


def test_a_database_that_cannot_be_asked_who_it_is_is_a_refusal_not_an_assumption(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The URLs differ as text, so only the servers can say whether they are one database. Neither can be
    reached here: the restore must stop, and must not say where it tried to connect."""
    dump = _dump_with_manifest(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(backup_tool.RESTORE_TARGET_ENV, "postgresql://operator:s3cr3t-p4ss@127.0.0.1:9/scratchdb")
    monkeypatch.setenv(backup_tool.BACKUP_SOURCE_ENV, "postgresql://operator:s3cr3t-p4ss@127.0.0.1:1/appdb")
    assert backup_tool.main(["restore", "--dump", str(dump), "--confirm-empty-target", "scratchdb"]) == 2
    printed = capsys.readouterr()
    assert "Could not reach the restore target" in printed.err and "Refusing." in printed.err
    for leaked in ("s3cr3t", "127.0.0.1", "operator", "scratchdb@", "Traceback"):
        assert leaked not in printed.out + printed.err


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://operator@db.example.test/appdb?service=production",  # a service file decides the real target
        "postgresql://operator@db.example.test/appdb?host=/var/run/postgresql",  # a socket directory overrides the host
        "postgresql://operator@db.example.test/appdb?passfile=/tmp/x",
        "postgresql://operator@db.example.test/appdb?sslmode=require&sslmode=disable",  # ambiguous
        "postgresql://operator@db-a.example.test,db-b.example.test/appdb",  # more than one host
        "postgresql://operator@/appdb",  # no host: libpq would pick a local socket
        "mysql://operator@db.example.test/appdb",
        "not a url",
    ],
)
def test_a_url_this_tool_cannot_follow_exactly_is_refused(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv(backup_tool.RESTORE_TARGET_ENV, url)
    with pytest.raises(backup_tool.OperatorError) as refusal:
        backup_tool._url(backup_tool.RESTORE_TARGET_ENV)
    assert "db.example.test" not in str(refusal.value) and "operator" not in str(refusal.value)
    with pytest.raises(backup_tool.OperatorError):
        backup_tool._libpq_environment(backup_tool.RESTORE_TARGET_ENV)


def test_a_dump_is_restored_only_with_its_own_matching_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(backup_tool, "_engine", lambda env_name: pytest.fail("a connection was attempted"))
    monkeypatch.setenv(backup_tool.RESTORE_TARGET_ENV, SECRET_URL)
    # no manifest
    alone = tmp_path / "alone" / "travelobligator-a.dump"
    alone.parent.mkdir()
    alone.write_bytes(b"x")
    with pytest.raises(backup_tool.OperatorError, match="no manifest"):
        backup_tool.restore(alone, "appdb")
    # the file changed after the backup
    changed = _dump_with_manifest(tmp_path / "changed")
    changed.write_bytes(changed.read_bytes() + b"!")
    with pytest.raises(backup_tool.OperatorError, match="checksum"):
        backup_tool.restore(changed, "appdb")
    # another backup's manifest renamed to sit beside this dump
    swapped = _dump_with_manifest(tmp_path / "swapped", "travelobligator-b.dump")
    manifest = swapped.with_name("travelobligator-b.manifest.json")
    manifest.write_text(manifest.read_text().replace("travelobligator-b.dump", "travelobligator-other.dump"))
    with pytest.raises(backup_tool.OperatorError, match="checksum"):
        backup_tool.restore(swapped, "appdb")
    # something that is not a manifest of this tool
    manifest.write_text('{"tables": {}}')
    with pytest.raises(backup_tool.OperatorError, match="not a backup manifest"):
        backup_tool.restore(swapped, "appdb")


def test_a_backup_goes_only_into_a_private_directory_and_never_changes_an_existing_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # unreachable on purpose: these are decided before, or fail at, the first connection
    monkeypatch.setenv(backup_tool.BACKUP_SOURCE_ENV, "postgresql://operator:s3cr3t-p4ss@127.0.0.1:1/appdb")
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    with pytest.raises(backup_tool.OperatorError, match="readable by other users"):
        backup_tool.backup(shared)
    assert oct(shared.stat().st_mode & 0o777) == "0o755" and not list(shared.iterdir())  # refused, and left as it was
    a_file = tmp_path / "file"
    a_file.write_text("x")
    with pytest.raises(backup_tool.OperatorError):
        backup_tool.backup(a_file)

    fresh = tmp_path / "new" / "backups"
    with pytest.raises(backup_tool.OperatorError, match="Could not read the source database"):
        backup_tool.backup(fresh)
    assert oct(fresh.stat().st_mode & 0o777) == "0o700"
    assert not list(fresh.iterdir())  # a failed backup leaves no partial dump and no manifest

    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    with pytest.raises(backup_tool.OperatorError, match="Could not read the source database"):
        backup_tool.backup(private)  # accepted as a directory; only the connection failed
    assert not list(private.iterdir())


def test_an_unforeseen_error_is_reported_by_type_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def explode(*arguments, **keywords):
        raise RuntimeError(f"could not connect to {SECRET_URL}")

    monkeypatch.setattr(backup_tool, "backup", explode)
    monkeypatch.setattr(backup_tool, "verify", explode)
    assert backup_tool.main(["backup", "--out", str(tmp_path / "out")]) == 2
    assert backup_tool.main(["verify"]) == 2
    monkeypatch.setattr(ops_report, "default_limits", explode)
    assert ops_report.main([]) == 3
    printed = capsys.readouterr()
    assert printed.err.count("unexpected error (RuntimeError)") == 3
    for leaked in ("s3cr3t", "db.example.test", "operator", "Traceback", "postgresql://"):
        assert leaked not in printed.out + printed.err


def test_the_connection_reaches_the_postgresql_tools_through_the_environment_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(backup_tool.BACKUP_SOURCE_ENV, SECRET_URL)
    monkeypatch.setenv("DATABASE_URL", "postgresql://other@elsewhere.example.test/other")
    monkeypatch.setenv("PGPASSWORD", "inherited-and-must-not-apply")
    seen = {}

    def fake_run(command, **keywords):
        seen["command"], seen["env"] = command, keywords["env"]
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(backup_tool.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(backup_tool.subprocess, "run", fake_run)
    backup_tool._run_tool("pg_dump", ["--format=custom", "--file=/tmp/x.dump"], backup_tool.BACKUP_SOURCE_ENV)

    assert all("s3cr3t" not in part and "://" not in part for part in seen["command"])  # nothing secret in `ps`
    environment = seen["env"]
    assert (environment["PGHOST"], environment["PGPORT"], environment["PGUSER"], environment["PGDATABASE"]) == ("db.example.test", "5432", "operator", "appdb")
    assert environment["PGPASSWORD"] == "s3cr3t-p4ss" and environment["PGSSLMODE"] == "require"
    # no URL variable is handed on, so the tool cannot pick up a different database than the one named
    assert not any(name.endswith("DATABASE_URL") for name in environment)


def test_a_failing_postgresql_tool_is_reported_without_connection_detail(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(backup_tool.BACKUP_SOURCE_ENV, SECRET_URL)
    stderr = (
        'pg_dump: error: connection to server at "db.example.test" (203.0.113.9), port 5432 failed: FATAL: password authentication failed for user "operator"\n'
        "pg_dump: detail: see postgresql://operator:s3cr3t-p4ss@db.example.test/appdb\n"
        # lines the line filter does not recognise still must not name the connection
        'pg_dump: error: permission denied for database "appdb" (role operator on db.example.test)\n'
        "pg_dump: hint: PGPASSWORD=s3cr3t-p4ss was rejected\n"
        "pg_dump: error: aborting"
    )
    monkeypatch.setattr(backup_tool.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(backup_tool.subprocess, "run", lambda *a, **k: type("Done", (), {"returncode": 1, "stdout": "", "stderr": stderr})())
    with pytest.raises(backup_tool.OperatorError, match=r"pg_dump failed \(exit code 1\)"):
        backup_tool._run_tool("pg_dump", [], backup_tool.BACKUP_SOURCE_ENV)
    printed = capsys.readouterr().err
    assert "aborting" in printed and "permission denied for database" in printed  # still useful to the operator
    for leaked in ("s3cr3t", "db.example.test", "203.0.113.9", "operator", "appdb"):
        assert leaked not in printed


def test_the_scripts_never_modify_a_database_they_only_read() -> None:
    report_source = (_ROOT / "backend" / "scripts" / "ops_report.py").read_text()
    tool_source = (_ROOT / "backend" / "scripts" / "db_backup_restore.py").read_text()
    writing = re.compile(r"\b(insert\s+into|update\s+\w+\s+set|delete\s+from|drop\s+(table|database|schema|index)|truncate|alter\s+table|create\s+table)\b", re.IGNORECASE)
    for name, source in (("ops_report.py", report_source), ("db_backup_restore.py", tool_source)):
        statements = [node.value for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Constant) and isinstance(node.value, str) and "select" in node.value.lower()]
        assert statements, name
        assert not [statement for statement in statements if writing.search(statement)], name
    # the only thing that writes is pg_restore, and it is never told to clean or drop
    assert "--clean" not in tool_source and "--create" not in tool_source and "--if-exists" not in tool_source
    assert '"--single-transaction"' in tool_source and '"--exit-on-error"' in tool_source
    assert "postgresql_readonly=True" in report_source


# -- alert conditions --------------------------------------------------------------------------------

_LIMITS = ops_report.Limits(
    generation_global_per_window=25, generation_per_user_per_window=5, generation_window_seconds=86400,
    signup_global_per_window=30, signup_window_seconds=86400, geoapify_daily_credits=3000,
)


def _snapshot(**fields) -> ops_report.Snapshot:
    return ops_report.Snapshot(**{"database_revisions": ["head"], "code_revisions": ["head"], **fields})


def _codes(snapshot: ops_report.Snapshot) -> dict[str, str]:
    return {finding.code: finding.severity for finding in ops_report.evaluate(snapshot, _LIMITS)}


def test_a_quiet_healthy_deployment_raises_nothing() -> None:
    quiet = _snapshot(users=12, trips=20, jobs_admitted=6, jobs_by_status={"succeeded": 6}, plans_updated=6, credits_used=400, signups=2)
    assert ops_report.evaluate(quiet, _LIMITS) == []
    assert ops_report.exit_code([]) == 0
    assert "Nothing to act on." in ops_report.render(quiet, _LIMITS, [])


@pytest.mark.parametrize(
    ("fields", "code", "severity"),
    [
        # database or migration problems
        ({"database_revisions": ["d41a7e2c9b53"]}, "schema_mismatch", "critical"),
        ({"database_revisions": []}, "schema_mismatch", "critical"),
        # generation failures and interrupted jobs
        ({"jobs_by_status": {"failed": 3, "succeeded": 20}, "failures_by_code": {"STAGE_FAILED": 3}}, "generation_failures", "warning"),
        ({"jobs_by_status": {"failed": 2, "succeeded": 2}, "failures_by_code": {"STAGE_FAILED": 2}}, "generation_failures", "warning"),
        ({"jobs_by_status": {"failed": 1, "succeeded": 20}, "failures_by_code": {"STAGE_FAILED": 1}}, "generation_failures", "note"),
        ({"jobs_by_status": {"failed": 3, "succeeded": 20}, "failures_by_code": {"JOB_INTERRUPTED": 3}}, "generation_interrupted", "warning"),
        ({"jobs_by_status": {"failed": 1, "succeeded": 20}, "failures_by_code": {"JOB_INTERRUPTED": 1}}, "generation_interrupted", "note"),
        ({"jobs_stuck": 1}, "jobs_stuck", "critical"),
        # cost-limit pressure
        ({"jobs_admitted": 20}, "generation_global_limit_pressure", "warning"),
        ({"jobs_admitted": 25}, "generation_global_limit_reached", "warning"),
        ({"signups": 24}, "signup_limit_pressure", "warning"),
        ({"signups": 31}, "signup_limit_reached", "warning"),
        ({"users_at_generation_limit": 2}, "generation_user_limit_reached", "note"),
        # provider exhaustion
        ({"credits_used": 2400}, "provider_credits_pressure", "warning"),
        ({"credits_used": 3000}, "provider_credits_reached", "warning"),
        ({"generations_refused_a_provider_call": 1}, "provider_credit_cap_hit", "warning"),
        ({"plans_updated": 4, "plans_blocked": 2}, "plans_blocked", "warning"),
        ({"plans_updated": 10, "plans_blocked": 1}, "plans_blocked", "note"),
    ],
)
def test_each_alert_condition_is_detected(fields: dict, code: str, severity: str) -> None:
    assert _codes(_snapshot(**fields)).get(code) == severity


def test_the_thresholds_sit_exactly_where_the_runbook_says() -> None:
    assert "generation_global_limit_pressure" not in _codes(_snapshot(jobs_admitted=19))  # 76 %: under 80 %
    assert "provider_credits_pressure" not in _codes(_snapshot(credits_used=2399))
    # an interruption is not counted as a generation failure, and the other way round
    interrupted_only = _codes(_snapshot(jobs_by_status={"failed": 5, "succeeded": 1}, failures_by_code={"JOB_INTERRUPTED": 5}))
    assert "generation_failures" not in interrupted_only and interrupted_only["generation_interrupted"] == "warning"
    # a limit that is switched off (0) is never "reached"
    off = ops_report.Limits(0, 0, 86400, 0, 86400, 0)
    assert ops_report.evaluate(_snapshot(jobs_admitted=500, signups=500, credits_used=9000), off) == []
    # severity decides the exit code
    assert ops_report.exit_code(ops_report.evaluate(_snapshot(users_at_generation_limit=1), _LIMITS)) == 0
    assert ops_report.exit_code(ops_report.evaluate(_snapshot(jobs_admitted=25), _LIMITS)) == 1
    assert ops_report.exit_code(ops_report.evaluate(_snapshot(jobs_admitted=25, jobs_stuck=1), _LIMITS)) == 2
    # every finding the code can raise is explained in the runbook's table
    source = (_ROOT / "backend" / "scripts" / "ops_report.py").read_text()
    fixed_codes = set(re.findall(r'add\(\s*"(?:critical|warning|note)",\s*"([a-z_]+)"', source))
    assert {"schema_mismatch", "jobs_stuck", "generation_failures", "generation_interrupted", "provider_credit_cap_hit", "plans_blocked", "generation_user_limit_reached"} == fixed_codes
    pressure_codes = set(re.findall(r'pressure\("([a-z_]+)"', source))
    assert pressure_codes == {"generation_global_limit", "signup_limit", "provider_credits"}
    for code in fixed_codes | {f"{code}_pressure" for code in pressure_codes}:
        assert f"`{code}`" in _RUNBOOK, f"the runbook does not explain {code}"


def test_the_report_compares_against_the_limits_declared_for_production(monkeypatch: pytest.MonkeyPatch) -> None:
    # a different value in this shell must not change what "production's limit" means
    monkeypatch.setenv("GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW", "999")
    declared = {entry["key"]: entry.get("value") for entry in yaml.safe_load((_ROOT / "render.yaml").read_text())["services"][0]["envVars"]}
    limits = ops_report.default_limits(geoapify_daily_credits=3000)
    assert limits.generation_global_per_window == int(declared["GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW"])
    assert limits.generation_per_user_per_window == int(declared["GENERATION_MAX_JOBS_PER_USER_PER_WINDOW"])
    assert limits.signup_global_per_window == int(declared["SIGNUP_MAX_PER_WINDOW_GLOBAL"])


# -- the availability check, against a stub on 127.0.0.1 ---------------------------------------------


class _Stub(http.server.BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, dict[str, str], bytes]] = {}
    seen: list[tuple[str, str | None]] = []

    def do_GET(self) -> None:  # noqa: N802
        type(self).seen.append((self.path, self.headers.get("Authorization")))
        status, headers, body = type(self).routes.get(self.path, (404, {"Content-Type": "text/plain"}, b"Not Found\n"))
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 -- the check must never send anything but GET
        type(self).seen.append((f"POST {self.path}", None))
        self.send_response(500)
        self.end_headers()

    do_DELETE = do_PUT = do_PATCH = do_POST

    def log_message(self, *arguments) -> None:
        pass


_PAGE_HEADERS = {
    "Content-Type": "text/html; charset=utf-8",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy-Report-Only": "default-src 'self'",
}
_HEALTHY = {
    "/": (200, _PAGE_HEADERS, b"<html><head><title>TravelObligator</title></head><body></body></html>"),
    "/api/health": (200, {"Content-Type": "application/json"}, json.dumps({"success": True, "data": {"status": "ok"}}).encode()),
    "/api/auth/me": (401, {"Content-Type": "application/json", "Cache-Control": "no-store"}, b"{}"),
    "/ready": (200, {"Content-Type": "application/json"}, json.dumps({"success": True, "data": {"status": "ready"}}).encode()),
}


@pytest.fixture()
def stub():
    _Stub.routes, _Stub.seen = dict(_HEALTHY), []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _check(origin: str, *extra: str) -> int:
    return public_check.main(["--frontend", origin, "--timeout", "5", "--cold-start-seconds", "0", *extra])


def test_a_healthy_deployment_passes_and_only_get_requests_are_sent(stub: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("OPS_TOKEN", "ops-t0ken-value")
    assert _check(stub) == 0
    # the default run asks for the page only: the backend is not contacted, so it is not a keepalive
    assert [path for path, _ in _Stub.seen] == ["/"]

    assert _check(stub, "--backend", "--expect-security-headers", "--ready", "--backend-origin", stub) == 0
    paths = [path for path, _ in _Stub.seen]
    assert not [path for path in paths if path.startswith("POST")]
    assert {"/api/health", "/api/ready", "/api/metrics", "/api/auth/me", "/ready"} <= set(paths)
    # the token goes to /ready on the backend origin and nowhere else, and is never printed
    assert {path for path, authorization in _Stub.seen if authorization} == {"/ready"}
    printed = capsys.readouterr()
    assert "ops-t0ken-value" not in printed.out + printed.err
    assert "All checks passed." in printed.out


@pytest.mark.parametrize(
    ("change", "arguments", "failed_check"),
    [
        # the public frontend is unavailable
        ({"/": (503, {"Content-Type": "text/html"}, b"down")}, (), "frontend page"),
        ({"/": (200, {"Content-Type": "text/html"}, b"<html>some other site</html>")}, (), "frontend page"),
        ({"/": (302, {"Location": "https://elsewhere.example.test/"}, b"")}, (), "frontend page"),
        # the backend does not answer
        ({"/api/health": (502, {"Content-Type": "text/html"}, b"<html>Bad Gateway</html>")}, ("--backend",), "backend liveness through the frontend"),
        ({"/api/health": (200, {"Content-Type": "text/html"}, b"<html>a page, not the API</html>")}, ("--backend",), "backend liveness through the frontend"),
        # an operational endpoint became public
        ({"/api/ready": (200, {"Content-Type": "application/json"}, b'{"data": {"status": "ready"}}')}, ("--backend",), "/api/ready is not public"),
        ({"/api/metrics": (200, {"Content-Type": "text/plain"}, b"travelob_http_requests_total 1")}, ("--backend",), "/api/metrics is not public"),
        # API data became reachable or cacheable without a session
        ({"/api/auth/me": (200, {"Content-Type": "application/json", "Cache-Control": "no-store"}, b"{}")}, ("--backend",), "an unauthenticated API request is refused and uncacheable"),
        ({"/api/auth/me": (401, {"Content-Type": "application/json", "Cache-Control": "public, max-age=60"}, b"{}")}, ("--backend",), "an unauthenticated API request is refused and uncacheable"),
        # the released headers are gone, or the policy was switched to enforcing without the documented procedure
        ({"/": (200, {"Content-Type": "text/html"}, _HEALTHY["/"][2])}, ("--expect-security-headers",), "frontend security headers"),
        ({"/": (200, {**_PAGE_HEADERS, "Content-Security-Policy": "default-src 'self'"}, _HEALTHY["/"][2])}, ("--expect-security-headers",), "frontend security headers"),
    ],
)
def test_each_outside_failure_is_detected(stub: str, capsys: pytest.CaptureFixture[str], change: dict, arguments: tuple, failed_check: str) -> None:
    _Stub.routes.update(change)
    assert _check(stub, *arguments) == 1
    assert f"FAIL {failed_check}" in capsys.readouterr().out


def test_readiness_failures_are_detected_and_the_database_state_is_named(stub: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("OPS_TOKEN", "ops-t0ken-value")
    _Stub.routes["/ready"] = (503, {"Content-Type": "application/json"}, json.dumps({"success": False, "data": {"status": "not_ready"}}).encode())
    assert _check(stub, "--backend", "--ready", "--backend-origin", stub) == 1
    assert "status not_ready" in capsys.readouterr().out
    _Stub.routes["/ready"] = (200, {"Content-Type": "application/json"}, json.dumps({"success": True, "data": {"status": "degraded"}}).encode())
    assert _check(stub, "--backend", "--ready", "--backend-origin", stub) == 0  # cache down: degraded, not an outage
    _Stub.routes["/ready"] = (404, {"Content-Type": "text/plain"}, b"Not Found\n")  # wrong or missing token
    assert _check(stub, "--backend", "--ready", "--backend-origin", stub) == 1

    monkeypatch.delenv("OPS_TOKEN")
    assert _check(stub, "--ready", "--backend-origin", stub) == 2  # refuses to run without the token
    assert _check("http://example.test") == 2  # plain http is accepted for 127.0.0.1 only


def test_an_unreachable_site_is_a_failure_not_a_crash(capsys: pytest.CaptureFixture[str]) -> None:
    assert _check("http://127.0.0.1:9") == 1  # nothing listens on the discard port
    assert "FAIL frontend page: no response" in capsys.readouterr().out


# -- the uptime workflow -----------------------------------------------------------------------------


def test_the_uptime_workflow_is_a_public_get_only_check_and_not_a_keepalive() -> None:
    assert "secrets." not in _UPTIME_TEXT and "OPS_TOKEN" not in re.sub(r"(?m)^\s*#.*$", "", _UPTIME_TEXT)
    assert _UPTIME["permissions"] == {"contents": "read"}
    job = _UPTIME["jobs"]["public-site"]
    assert job["timeout-minutes"] <= 10
    steps = job["steps"]
    for step in steps:
        if "uses" in step:  # pinned to a full commit, like every action of the CI workflow
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
    commands = [step["run"] for step in steps if "run" in step]
    assert all(command.startswith("python3 backend/scripts/public_check.py") for command in commands)
    assert not any("--ready" in command or "--backend-origin" in command or "curl" in command for command in commands)

    # the scheduled, unconditional step contacts the frontend only
    unconditional = [step for step in steps if "run" in step and "if" not in step]
    assert [step["run"] for step in unconditional] == ["python3 backend/scripts/public_check.py"]
    backend_step = next(step for step in steps if "--backend" in step.get("run", ""))
    condition = backend_step["if"]
    assert "vars.UPTIME_CHECK_BACKEND == 'true'" in condition and "inputs.backend" in condition

    schedules = [entry["cron"] for entry in _UPTIME[True]["schedule"]]  # YAML reads the key `on` as True
    daily = [cron for cron in schedules if re.fullmatch(r"\d+ \d+ \* \* \*", cron)]
    assert len(daily) == 1 and f"github.event.schedule == '{daily[0]}'" in condition  # the backend: at most once a day
    for cron in schedules:
        minute, hour = cron.split()[:2]
        assert minute.isdigit(), cron  # never "every N minutes"
        assert hour.isdigit() or int(hour.removeprefix("*/")) >= 4, cron
    assert _UPTIME[True]["workflow_dispatch"]["inputs"]["backend"]["default"] is False
    # it is its own workflow: not a job of CI, so never a required check
    assert "uptime" not in json.dumps(_CI).lower()


# -- documents say what the repository does ----------------------------------------------------------


def test_the_documents_name_the_six_ci_jobs_that_exist() -> None:
    names = [job["name"] for job in _CI["jobs"].values()]
    assert names == ["Backend / Hermetic", "Backend / Integration", "Frontend / Build", "Browser / E2E", "Containers / Build & Smoke", "Security / Scan"]
    claude = _INSTRUCTIONS
    overview = (_ROOT / "docs" / "CODEBASE_OVERVIEW.md").read_text()
    release = (_ROOT / "docs" / "23_v1_release_reference.md").read_text()
    for name in names:
        for label, document in (("CLAUDE.md", claude), ("CODEBASE_OVERVIEW.md", overview), ("23_v1_release_reference.md", release), ("25_operations_runbook.md", _RUNBOOK)):
            assert f"`{name}`" in document, f"{label} does not name {name}"
    assert "runs six jobs" in claude and "runs five jobs on" not in claude
    assert "runs six jobs" in overview
    assert "has six jobs" in release
    # the history is kept, not rewritten
    assert "Previous text, Section 203A" in overview
    architecture = (_ROOT / "docs" / "14_backend_architecture.md").read_text()
    assert "Browser / E2E" not in architecture  # section 165 stays the record of the five-job workflow


def test_the_browser_tests_are_documented_with_their_limits() -> None:
    readme = (_ROOT / "README.md").read_text()
    claude = _INSTRUCTIONS
    release = (_ROOT / "docs" / "23_v1_release_reference.md").read_text()
    assert json.loads((_ROOT / "frontend" / "package.json").read_text())["scripts"]["e2e"] == "playwright test"
    for document in (readme, claude, release):
        assert "npm run e2e" in document and "npx playwright install chromium" in document
        assert "WCAG" in document  # each says the suite is not a conformance statement
    assert "frontend/e2e/README.md" in readme and "docs/25_operations_runbook.md" in readme
    assert "25_operations_runbook.md" in (_ROOT / "docs" / "22_free_tier_deployment.md").read_text()


def test_the_runbook_matches_the_tools_and_the_deployment_configuration() -> None:
    render = yaml.safe_load((_ROOT / "render.yaml").read_text())["services"][0]
    # the scripts and variables it tells an operator to use exist under exactly those names
    for script in ("db_backup_restore.py", "ops_report.py", "public_check.py", "run_migrations.py"):
        assert f"backend/scripts/{script}" in _RUNBOOK and (_ROOT / "backend" / "scripts" / script).is_file()
    for variable in (backup_tool.BACKUP_SOURCE_ENV, backup_tool.RESTORE_TARGET_ENV, backup_tool.VERIFY_ENV, ops_report.DATABASE_ENV, "UPTIME_CHECK_BACKEND"):
        assert variable in _RUNBOOK
    for flag in ("--backend", "--ready", "--backend-origin", "--expect-security-headers", "--geoapify-daily-credits", "--manifest", "--dump", "--out"):
        assert flag in _RUNBOOK
    assert (_ROOT / ".github" / "workflows" / "uptime.yml").is_file() and ".github/workflows/uptime.yml" in _RUNBOOK
    # what it says about the deployment is what render.yaml says
    assert render["autoDeploy"] is False and "`autoDeploy` is off" in _RUNBOOK
    assert render["healthCheckPath"] == "/health"
    declared = {entry["key"]: entry for entry in render["envVars"]}
    assert declared["METRICS_ENABLED"]["value"] == "false" and "`METRICS_ENABLED=false`" in _RUNBOOK
    secret_names = {key for key, entry in declared.items() if entry.get("sync") is False}
    assert {"DATABASE_URL", "REDIS_URL", "GEOAPIFY_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY"} <= secret_names
    # the runbook contains no credential and no real connection string
    assert not re.search(r"postgres(ql)?://[^\s<`]+@", _RUNBOOK)
    assert not re.search(r"\b(sk|gsk|AIza)[-_A-Za-z0-9]{20,}", _RUNBOOK)
    # and it never tells anyone to restore into, or downgrade, production automatically
    assert "A restore never goes into production." in _RUNBOOK
    assert "Never automate it" in _RUNBOOK
    # every command it shows for the backup tool reads the URL blind
    assert _RUNBOOK.count("read -rs") >= 6


# -- P1-D safety review: what the documents must keep saying -----------------------------------------


def test_the_ops_token_is_never_carried_to_another_address_by_a_redirect(stub: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("OPS_TOKEN", "ops-t0ken-value")
    _Stub.routes["/ready"] = (307, {"Location": f"{stub}/elsewhere"}, b"")
    _Stub.routes["/elsewhere"] = _HEALTHY["/ready"]
    assert _check(stub, "--ready", "--backend-origin", stub) == 1  # a redirect is a failed check, not followed
    assert "/elsewhere" not in [path for path, _ in _Stub.seen]
    assert "ops-t0ken-value" not in "".join(capsys.readouterr())
    # the token is accepted for an https origin (or this machine) only
    assert public_check.main(["--ready", "--backend-origin", "http://backend.example.test"]) == 2
    source = (_ROOT / "backend" / "scripts" / "public_check.py").read_text()
    assert source.count('"Authorization"') == 1 and "check_ready" in source.split('"Authorization"')[0].rsplit("def ", 1)[1]
    assert "arguments.timeout" in source and "timeout=timeout" in source  # every request is bounded


def test_the_runbook_states_the_restore_limits_and_the_operator_step() -> None:
    assert "--confirm-empty-target" in _RUNBOOK
    for sentence in (
        "A restore never goes into production.",
        "**What it cannot do: know which database is production.**",
        "If one of them is set and cannot be reached to be asked, it refuses.",
        "leaves the target exactly as empty as it was",
        "reports a copy that differs as **REJECTED**",
        "**The tool does not encrypt the dump.**",
    ):
        assert sentence in _RUNBOOK, sentence
    # every restore command shown names its target
    for block in re.findall(r"```bash\n(.*?)```", _RUNBOOK, flags=re.DOTALL):
        if "db_backup_restore.py restore" in block:
            assert "--confirm-empty-target" in block and "read -rs RESTORE_TARGET_DATABASE_URL" in block
    tool_source = (_ROOT / "backend" / "scripts" / "db_backup_restore.py").read_text()
    assert "WHAT IT CANNOT DO: know which database is \"production\"" in tool_source


def test_the_runbook_keeps_the_rollback_safeguards() -> None:
    section = _RUNBOOK[_RUNBOOK.index("### 4.3 ") : _RUNBOOK.index("### 4.4 ")]
    for sentence in (
        "**A production database is never downgraded automatically.**",
        "**Fix forward whenever it is feasible.**",
        "**It removes the quota accounting.**",
        "**It puts back a backend with no cost controls.**",
        "public generation must stay disabled or",
        "A **fresh backup** of the production database exists",
        "**Migration compatibility**",
        "they are not a procedure",
    ):
        assert sentence in section, sentence
    # the restriction comes BEFORE the downgrade command, and the service is resumed only under it
    assert section.index("Apply the restriction chosen above") < section.index(f"alembic downgrade {PRODUCTION_REVISION}")
    assert "Resume the service only under the restriction" in section
    # nothing in the repository downgrades a database outside a test or the runbook's by-hand command
    offenders = []
    for path in [*(_ROOT / "backend" / "scripts").glob("*.py"), *(_ROOT / "scripts").rglob("*.sh"), *(_ROOT / ".github").rglob("*.yml"), _ROOT / "render.yaml", _ROOT / "docker-compose.yml", _ROOT / "backend" / "Dockerfile"]:
        if not path.is_file():
            continue
        # code that would run a downgrade (a docstring promising never to is not one): the Alembic API
        # call, the word as a command argument, or the command line in a shell script or workflow
        pattern = r"command\.downgrade\(|[\"']downgrade[\"']\s*[,\]]" if path.suffix == ".py" else r"alembic[^\n#]*\bdowngrade\b"
        if re.search(pattern, path.read_text()):
            offenders.append(path.name)
    assert offenders == []


def test_only_the_automated_backups_state_a_retention_period_and_it_is_the_one_in_force() -> None:
    source = (_ROOT / "frontend" / "lib" / "privacy-notice.ts").read_text()
    # the notice text itself (the points shown to people), not the module's comments
    notice = source[source.index("export const PRIVACY_NOTICE: PrivacySection[]") : source.index("// -- deletion controls")]
    sentences = re.split(r"(?<=[.!?])\s+", notice)
    # the automated daily backups: the one period the notice states for a copy, and the runbook records it
    automated = notice[notice.index("Daily encrypted backups:") : notice.index("Database exports that the project operator keeps:")]
    assert "Each backup is deleted automatically about seven days after it is made." in automated
    assert "Backups are not edited when you delete a trip or your account, and deleting does not remove them early" in automated
    record = _RUNBOOK[_RUNBOOK.index("### 2.6 Automated daily backups") : _RUNBOOK.index("## 3. Deployment procedure")]
    assert "once a day at 03:37 UTC" in record and "expires **7 days** after it is created" in record
    assert "**check before release:** bucket versioning is off" in record
    # no sentence anywhere claims that deleting a trip or an account removes backups
    for sentence in sentences:
        assert not re.search(r"backups? (is|are) (deleted|removed|purged) (immediately|straight away|at once|when you delete)", sentence, re.IGNORECASE), sentence
    # every OTHER sentence that mentions a backup (the hosting services') states no period or promise
    about_other_backups = [s for s in sentences if "backup" in s.lower() and s not in automated and "Daily encrypted backups" not in s]
    assert about_other_backups
    for sentence in about_other_backups:
        assert not re.search(r"\d", sentence), sentence
        assert not re.search(r"\b(retain|retained|retention of|kept for|deleted after|expire)\b", sentence, re.IGNORECASE), sentence
    assert not re.search(r"30[\s-]*day", notice)
    # the same holds for the operator's own exports, which the notice describes without the word "backup"
    about_exports = [sentence for sentence in re.split(r"(?<=[.!?])\s+", notice) if "export" in sentence.lower() and "export const" not in sentence]
    assert len(about_exports) >= 3
    for sentence in about_exports:
        assert not re.search(r"\d", sentence), sentence
        assert not re.search(r"\b(retained for|kept for|deleted after|expires? after|within)\b", sentence, re.IGNORECASE), sentence
    # the four holders of a copy are told apart
    assert "Deleting removes your records from this app's own database, and only from there." in notice
    assert "Database exports that the project operator keeps:" in notice
    assert "Backups and logs kept by the hosting services:" in notice
    assert "What was already sent to the external services above" in notice
    # true before the first export exists ("may"), and explicit that no period is set
    assert "the operator may export a copy of the database" in notice and "may be kept temporarily" in notice
    assert "It is not edited when you delete a trip or your account" in notice
    assert "This project has not yet set a retention period for these exports." in notice
    # the runbook calls 30 days a recommendation and lists what is unverified
    section = _RUNBOOK[_RUNBOOK.index("**Retention: a recommendation, not yet an operating policy.**") : _RUNBOOK.index("### 2.3 ")]
    assert "**30 days**" in section and section.count("**Not verified**") == 4
    assert "For the exports it names\nno number of days on purpose." in section and "until then it must not state one for exports" in section
    assert "is not an\n   operating policy" in _RUNBOOK or "It is not an operating policy" in _RUNBOOK.replace("\n   ", " ")


def test_the_runbook_records_the_decisions_and_what_is_still_unverified() -> None:
    decisions = _RUNBOOK[_RUNBOOK.index("## 7. Decisions") :]
    assert "every four hours" in decisions and "stays **disabled**" in decisions
    assert "required before public release" in decisions and "Not done." in decisions
    for open_item in ("**Neon history retention**", "**Workflow notifications**", "**Backup storage**", "**Privacy notice**"):
        assert open_item in decisions
    assert "**Required before public release, and not done: a restore drill into an isolated, disposable Neon branch.**" in _RUNBOOK
    assert "**Alerting is not verified.**" in _RUNBOOK and "**It does not run yet.**" in _RUNBOOK
    # the unresolved build-tool finding is named, and the forbidden shortcut is named as forbidden
    assert "**Unresolved high-severity build-tool exposure:** `braces`" in _RUNBOOK
    assert "`npm audit fix --force`\nwas not used and must not be" in _RUNBOOK
    lock = json.loads((_ROOT / "frontend" / "package-lock.json").read_text())["packages"]
    assert lock["node_modules/next"]["version"].startswith("16."), "Next.js must not be downgraded to silence an audit"
    assert lock["node_modules/eslint-config-next"]["version"].startswith("16.")
    installed = lambda name: tuple(int(part) for part in lock[f"node_modules/{name}"]["version"].split("."))  # noqa: E731
    assert installed("baseline-browser-mapping") >= (2, 11, 0)
    assert installed("js-yaml") >= (4, 3, 2) and installed("browserslist") > (4, 28, 6)


# -- release-copy alignment: no operator deletion is promised without a way to verify ownership --------


def test_a_locked_out_deletion_needs_proof_of_mailbox_control_and_runs_only_through_the_operator_command() -> None:
    section = _RUNBOOK[_RUNBOOK.index("## 8. An account deletion request from someone who cannot sign in") :]
    for sentence in (
        "**nothing the system stores can prove that a requester owns an account.**",
        "**Knowing an account's email address is not proof of ownership.**",
        "That ability is not a procedure",
        "Never improvise around it.",
        "**D3a: contact.** Requests go to **garimaprachi2411@gmail.com**",
        "A message that merely comes *from* the address is never\nenough",
        "the code goes **to** the address",
        "Reply to the requester with the **same text in every case** (it must not reveal whether an account exists)",
        "Never SQL.",
        "### 8.2 Prerequisites before the notice offering this is published",
        "**Rehearsal:**",
    ):
        assert sentence in section, sentence
    assert "not yet\n   usable** until the prerequisites of section 8.2 are done" in _RUNBOOK
    # the commands shown are the tool's, and its own variables are typed blind
    tool = (_ROOT / "backend" / "scripts" / "locked_out_account_deletion.py").read_text()
    for name in ("ACCOUNT_DELETION_DATABASE_URL", "ACCOUNT_DELETION_SESSION_SECRET_KEY"):
        assert f'"{name}"' in tool and f"read -rs {name}" in section
    assert "MAX_HOURS = 72" in tool and "within 72 hours" in section and "MAX_ATTEMPTS = 5" in tool and "five wrong" in section

    # what the runbook says the system has is what the code has: the only deletion route needs the
    # password, and there is nothing that could verify an email address or reset a password
    auth_routes = (_ROOT / "backend" / "app" / "api" / "routes" / "auth.py").read_text()
    paths = re.findall(r'@router\.(?:get|post|put|patch|delete)\(\s*"([^"]+)"', auth_routes)
    # (the limited beta's two registration routes only decide whether a NEW account may be created)
    assert sorted(paths) == [
        "/delete-account", "/login", "/logout", "/me", "/registration", "/registration-access", "/signup",
    ]
    assert "password" in auth_routes[auth_routes.index('"/delete-account"') : auth_routes.index('"/me"')]
    application = "\n".join(
        path.read_text() for path in (_ROOT / "backend" / "app").rglob("*.py") if "tests" not in path.parts
    )
    assert not re.search(r"\b(smtplib|sendgrid|send_email|reset_token|verification_token|magic_link|pyotp)\b", application)
    requirements = (_ROOT / "backend" / "requirements.txt").read_text().lower()
    assert not re.search(r"^(sendgrid|resend|mailgun|aiosmtplib|fastapi-mail|pyotp)\b", requirements, re.MULTILINE)

    # and the public notice offers no such deletion, in the same words as the registration form
    notice = (_ROOT / "frontend" / "lib" / "privacy-notice.ts").read_text()
    warning = "Password recovery is not available during this beta. If you lose your password, you may lose access to your saved trips and may be unable to delete your account yourself."
    assert warning in notice and warning in (_ROOT / "frontend" / "lib" / "beta-notice.ts").read_text()
    # the notice describes the mailbox challenge, names the owner-chosen address, and promises no reset
    assert 'export const ACCOUNT_DELETION_CONTACT = "garimaprachi2411@gmail.com";' in notice
    assert "A message that merely comes from the account's address is not accepted as proof on its own." in notice
    assert "a one-time code is sent to that address, and the account is deleted only if that code is sent back" in notice
    assert "does not delete accounts on request" not in notice
    for promise in (r"we (will|can) delete your account (for you|on request)", r"password reset is available", r"reset your password",
                    r"deleted (immediately|straight away) on request"):
        assert not re.search(promise, notice, re.IGNORECASE), promise
