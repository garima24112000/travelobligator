from __future__ import annotations

import ast
import re
from pathlib import Path

# Section 200C: source-level guarantees that keep the transaction boundary honest.
_APP = Path(__file__).resolve().parents[2]

_EXTERNAL = (
    "provider_gateway",
    "app.providers",
    "httpx",
    "requests",
    "anthropic",
    "groq",
    "openai",
    "redis",
    "narrative_service",
    "interpreter",
    "executor",
    "langgraph",
)


def _source(*parts: str) -> str:
    return _APP.joinpath(*parts).read_text(encoding="utf-8")


def _function_source(path: tuple[str, ...], name: str) -> str:
    text = _source(*path)
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"{name} not found in {'/'.join(path)}")


def test_no_external_call_can_happen_inside_a_database_transaction() -> None:
    """Every function that runs inside a unit of work is DATABASE-ONLY: provider, AI, Redis and
    network calls all happen BEFORE the short transaction opens."""
    guarded = [
        (("services", "revision_lineage_service.py"), "commit_state_with_revision"),
        (("services", "revision_lineage_service.py"), "_activate_branch"),
        (("services", "revision_lineage_service.py"), "_record_current_revision"),
        (("services", "planning_orchestrator.py"), "_persist_new_trip"),
    ]
    for path, name in guarded:
        body = _function_source(path, name)
        for needle in _EXTERNAL:
            assert needle not in body, (name, needle)


def test_the_persistence_layers_never_import_providers_ai_or_redis() -> None:
    for folder in ("repositories", "db"):
        for file in (_APP / folder).glob("*.py"):
            text = file.read_text(encoding="utf-8").lower()
            for needle in ("app.providers", "import redis", "httpx", "anthropic", "groq", "provider_gateway"):
                assert needle not in text, (file.name, needle)


def test_redis_is_never_a_correctness_lock() -> None:
    """200B Redis stays a provider-response cache: no lock/lease/coordination code imports or
    references it (checked on imports and identifiers, so explanatory comments are fine)."""
    for path in (
        ("services", "generation_job_service.py"),
        ("services", "revision_lineage_service.py"),
        ("services", "targeted_regeneration_application_service.py"),
        ("services", "planning_orchestrator.py"),
        ("repositories", "unit_of_work.py"),
        ("repositories", "postgres_job_repository.py"),
        ("repositories", "postgres_planning_state_repository.py"),
        ("repositories", "postgres_itinerary_lineage_repository.py"),
        ("db", "transactions.py"),
    ):
        tree = ast.parse(_source(*path))
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
                names += [alias.name for alias in node.names]
            elif isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
        for name in names:
            assert "redis" not in name.lower() and "provider_cache" not in name.lower(), (path, name)


def test_sql_is_never_built_from_strings() -> None:
    pattern = re.compile(r"""(text|execute)\(\s*f["']""")
    for folder in ("repositories", "db"):
        for file in (_APP / folder).glob("*.py"):
            assert not pattern.search(file.read_text(encoding="utf-8")), file.name


def test_run_atomic_is_only_used_by_the_known_transition_owners() -> None:
    users = sorted(
        str(p.relative_to(_APP))
        for p in _APP.rglob("*.py")
        if "tests" not in p.parts and "run_atomic(" in p.read_text(encoding="utf-8") and p.name != "unit_of_work.py"
    )
    assert users == ["services/planning_orchestrator.py", "services/revision_lineage_service.py"]


def test_only_the_commit_helper_writes_state_and_revision_together() -> None:
    """No caller pairs `planning_state_repository.save(...)` with `record_current_revision(...)`
    any more -- that two-step sequence is the old crash window."""
    offenders = []
    for path in _APP.rglob("*.py"):
        if "tests" in path.parts or path.name == "revision_lineage_service.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "record_current_revision(" in text:
            offenders.append(str(path.relative_to(_APP)))
    assert offenders == []


def test_unit_of_work_documents_its_isolation_level_and_retry_policy() -> None:
    doc = _source("db", "transactions.py")
    assert "READ COMMITTED" in doc and "SERIALIZABLE is" in doc and "deliberately NOT used" in doc
    assert "40001" in doc and "40P01" in doc
