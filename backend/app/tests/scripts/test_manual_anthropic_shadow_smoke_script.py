from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

# Safety-boundary tests for the Step 161C manual/dev-only Anthropic
# shadow-mode smoke script. This suite never calls the real Anthropic API
# and never requires ANTHROPIC_API_KEY -- it only inspects the script's
# source (AST/string checks) and runs it as a subprocess with guardrail
# env vars deliberately missing/invalid, which the script itself is
# required to handle by exiting immediately without any network call.

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPT_PATH = _REPO_ROOT / "backend" / "scripts" / "manual_anthropic_shadow_smoke.py"
_DOC_PATH = _REPO_ROOT / "docs" / "20_manual_anthropic_shadow_smoke.md"

_DISALLOWED_IMPORT_SUBSTRINGS = (
    "langgraph",
    "langsmith",
    "openai",
    "gemini",
    "google.generativeai",
    "claude_code",
)


def _clean_env(**overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "ANTHROPIC_API_KEY",
        "AI_CANDIDATE_PROPOSAL_PROVIDER",
        "AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED",
    ):
        env.pop(key, None)
    env.update(overrides)
    return env


def _run_script(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT_PATH)],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# 1. Script exists.
# ---------------------------------------------------------------------------


def test_manual_smoke_script_exists() -> None:
    assert _SCRIPT_PATH.is_file()


# ---------------------------------------------------------------------------
# 2. Script is not imported by any app runtime module.
# ---------------------------------------------------------------------------


def test_manual_smoke_script_is_not_imported_by_app_runtime() -> None:
    app_dir = _REPO_ROOT / "backend" / "app"
    needle_variants = (
        "manual_anthropic_shadow_smoke",
        "scripts.manual_anthropic_shadow_smoke",
    )
    for path in app_dir.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text()
        for needle in needle_variants:
            assert needle not in text, f"{path} unexpectedly references {needle}"


def test_manual_smoke_script_not_collected_as_a_pytest_test_module() -> None:
    # pytest's default discovery only matches test_*.py / *_test.py -- this
    # script's name matches neither, so it is never collected/executed by
    # pytest itself. This assertion pins that filename contract in place.
    assert not _SCRIPT_PATH.name.startswith("test_")
    assert not _SCRIPT_PATH.name.endswith("_test.py")


# ---------------------------------------------------------------------------
# 3. Script contains an explicit manual-only guard/main block.
# ---------------------------------------------------------------------------


def test_manual_smoke_script_has_main_guard_and_manual_only_docs() -> None:
    source = _SCRIPT_PATH.read_text()
    assert 'if __name__ == "__main__":' in source
    assert "MANUAL-ONLY" in source
    assert "never" in source.lower() and "runtime" in source.lower()


# ---------------------------------------------------------------------------
# 4. Script refuses to run without ANTHROPIC_API_KEY (and without the other
#    two required env vars), and never calls the network to do so.
# ---------------------------------------------------------------------------


def test_manual_smoke_script_exits_gracefully_without_api_key() -> None:
    result = _run_script(_clean_env())

    assert result.returncode == 0
    assert "ANTHROPIC_API_KEY is not set" in result.stdout
    assert "Exiting without calling the Anthropic API" in result.stdout


def test_manual_smoke_script_exits_gracefully_when_provider_not_anthropic() -> None:
    result = _run_script(
        _clean_env(ANTHROPIC_API_KEY="dummy-test-key-should-never-be-printed")
    )

    assert result.returncode == 0
    assert "AI_CANDIDATE_PROPOSAL_PROVIDER" in result.stdout
    assert "dummy-test-key-should-never-be-printed" not in result.stdout
    assert "dummy-test-key-should-never-be-printed" not in result.stderr


def test_manual_smoke_script_exits_gracefully_when_shadow_mode_disabled() -> None:
    result = _run_script(
        _clean_env(
            ANTHROPIC_API_KEY="dummy-test-key-should-never-be-printed",
            AI_CANDIDATE_PROPOSAL_PROVIDER="anthropic",
        )
    )

    assert result.returncode == 0
    assert "AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED" in result.stdout
    assert "dummy-test-key-should-never-be-printed" not in result.stdout
    assert "dummy-test-key-should-never-be-printed" not in result.stderr


# ---------------------------------------------------------------------------
# 5. No OpenAI/LangGraph/LangSmith/Gemini/Claude Code CLI import.
# ---------------------------------------------------------------------------


def test_manual_smoke_script_has_no_disallowed_llm_framework_imports() -> None:
    source = _SCRIPT_PATH.read_text()
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        for disallowed in _DISALLOWED_IMPORT_SUBSTRINGS:
            assert disallowed not in lowered, f"Disallowed import found: {name}"

    # The script never imports the `anthropic` SDK directly -- it goes
    # through the app's normal HTTP surface, so `AnthropicAICandidateProposalProvider`
    # (which itself lazily imports `anthropic`) is the only place that ever does.
    assert "import anthropic" not in source


# ---------------------------------------------------------------------------
# 6. Script source does not print secrets or raw Claude responses.
# ---------------------------------------------------------------------------


def test_manual_smoke_script_never_prints_api_key_variable() -> None:
    source = _SCRIPT_PATH.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"):
            continue
        printed_source = ast.get_source_segment(source, node) or ""
        assert "api_key" not in printed_source
        assert "ANTHROPIC_API_KEY" not in printed_source
        assert ".content" not in printed_source  # raw Anthropic response content
        assert "messages.create" not in printed_source


def test_manual_smoke_script_does_not_read_response_content_directly() -> None:
    """The script only reads the already-validated JSON API response
    (`generate_response.json()`), never a raw Anthropic SDK response object
    -- it has no access to one, since it goes through the HTTP layer only.
    """
    source = _SCRIPT_PATH.read_text()
    assert "response.content" not in source
    assert "client.messages" not in source


# ---------------------------------------------------------------------------
# 7. Docs mention manual-only and the cost/API-call warning.
# ---------------------------------------------------------------------------


def test_manual_smoke_doc_exists_and_warns_about_cost_and_manual_only() -> None:
    assert _DOC_PATH.is_file()
    text = _DOC_PATH.read_text()
    assert "manual" in text.lower()
    assert "cost" in text.lower()
    assert "real anthropic api" in text.lower() or "real claude" in text.lower()
    assert "Claude Code" in text
    assert "ANTHROPIC_API_KEY" in text
    assert "AI_CANDIDATE_PROPOSAL_PROVIDER=anthropic" in text
    assert "AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=true" in text


def test_manual_smoke_doc_explains_what_pass_does_not_mean() -> None:
    text = _DOC_PATH.read_text()
    assert "does not mean" in text.lower()
    assert "scheduled" in text.lower()
    assert "fact" in text.lower()
    assert "frontend" in text.lower()


# ---------------------------------------------------------------------------
# Avoided-phrase check local to this suite's own additions (the broader
# repo-wide grep is run separately per the task's verification steps).
#
# Each phrase is built via split-string concatenation rather than written as
# a literal, so this test file itself never contains the literal phrase --
# otherwise the repo-wide forbidden-phrase grep (which this test exists to
# guard against) would flag this file as a false positive.
# ---------------------------------------------------------------------------

_AVOIDED_PHRASES = (
    "best " + "hotels",
    "top " + "rated",
    "booking" + "-ready",
    "guaran" + "teed",
    "verified " + "route time",
    "safe " + "area",
    "final " + "recommendation",
    "updated " + "plan",
    "diff " + "generated",
    "travel" + "-ready",
)


def test_script_and_doc_avoid_forbidden_marketing_phrases() -> None:
    for path in (_SCRIPT_PATH, _DOC_PATH):
        text = path.read_text().lower()
        for phrase in _AVOIDED_PHRASES:
            assert phrase not in text, f"{path} contains avoided phrase: {phrase!r}"
