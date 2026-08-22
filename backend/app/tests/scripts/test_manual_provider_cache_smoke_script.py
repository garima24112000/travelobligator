from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

# Safety-boundary tests for the Step 164D.1 manual/dev-only provider-cache
# smoke script, extended in Step 164F to cover OSM/Nominatim geocoding and
# in Step 164H to cover one OSM/Overpass POI search. This suite never calls
# a real Open-Meteo/Nager.Date/Frankfurter/Nominatim/Overpass (or any other)
# network endpoint and never requires RUN_LIVE_PROVIDER_CACHE_SMOKE to be
# truthy -- it only inspects the script's source (AST/string checks) and
# runs it as a subprocess with the guardrail env var deliberately
# missing/falsy, which the script itself is required to handle by exiting
# immediately without any network call.

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPT_PATH = _REPO_ROOT / "backend" / "scripts" / "manual_provider_cache_smoke.py"
_DOC_PATH = _REPO_ROOT / "docs" / "21_manual_provider_cache_smoke.md"

_DISALLOWED_IMPORT_SUBSTRINGS = (
    "groq",
    "anthropic",
    "langgraph",
    "langsmith",
    "openai",
    "gemini",
    "google.generativeai",
    "kiwi",
    "mcp",
    "beautifulsoup",
    "scrapy",
    "selenium",
    "playwright",
    "claude_code",
)

_EXPECTED_SOURCES = (
    "open_meteo",
    "nager_date",
    "frankfurter",
    "openstreetmap_geocode",
    "openstreetmap_poi",
)


def _clean_env(**overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("RUN_LIVE_PROVIDER_CACHE_SMOKE", None)
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
# 2. Script is not imported by any app runtime module, and is not itself
#    collected as a pytest test module.
# ---------------------------------------------------------------------------


def test_manual_smoke_script_is_not_imported_by_app_runtime() -> None:
    app_dir = _REPO_ROOT / "backend" / "app"
    needle_variants = (
        "manual_provider_cache_smoke",
        "scripts.manual_provider_cache_smoke",
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
# 3. Script has an explicit manual-only guard/main block and documents that
#    it is never used in CI.
# ---------------------------------------------------------------------------


def test_manual_smoke_script_has_main_guard_and_manual_only_docs() -> None:
    source = _SCRIPT_PATH.read_text()
    assert 'if __name__ == "__main__":' in source
    assert "MANUAL-ONLY" in source
    assert "never" in source.lower() and "runtime" in source.lower()


def test_manual_smoke_script_documents_no_ci_usage() -> None:
    source = _SCRIPT_PATH.read_text().lower()
    assert "ci" in source
    assert "never invoked by pytest" in source or "never run by pytest" in source


# ---------------------------------------------------------------------------
# 4. Script refuses to run without RUN_LIVE_PROVIDER_CACHE_SMOKE=true, and
#    never calls the network to do so.
# ---------------------------------------------------------------------------


def test_manual_smoke_script_exits_gracefully_without_env_var() -> None:
    result = _run_script(_clean_env())

    assert result.returncode == 0
    assert "RUN_LIVE_PROVIDER_CACHE_SMOKE is not set" in result.stdout
    assert "Exiting without calling any live provider" in result.stdout


def test_manual_smoke_script_exits_gracefully_with_falsy_env_var() -> None:
    result = _run_script(_clean_env(RUN_LIVE_PROVIDER_CACHE_SMOKE="false"))

    assert result.returncode == 0
    assert "RUN_LIVE_PROVIDER_CACHE_SMOKE is not set" in result.stdout
    assert "Exiting without calling any live provider" in result.stdout


def test_manual_smoke_script_missing_env_var_prevents_live_calls() -> None:
    """No PASS/FAIL/provider-summary output should ever appear when the
    guard is missing -- proof the live/cache code path never runs."""
    result = _run_script(_clean_env())

    assert "RESULT:" not in result.stdout
    for source in _EXPECTED_SOURCES:
        assert f"provider={source}" not in result.stdout


# ---------------------------------------------------------------------------
# 5. No Groq/Anthropic/Kiwi/MCP/scraping/other-LLM-framework import.
# ---------------------------------------------------------------------------


def test_manual_smoke_script_has_no_disallowed_imports() -> None:
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


# ---------------------------------------------------------------------------
# 6. Script imports without making a network call (top-level imports are
#    stdlib only; every `app.*` import is deferred into functions that only
#    run after the guard passes).
# ---------------------------------------------------------------------------


def test_manual_smoke_script_top_level_imports_are_stdlib_only() -> None:
    source = _SCRIPT_PATH.read_text()
    tree = ast.parse(source)

    _STDLIB_MODULES = {
        "__future__",
        "os",
        "shutil",
        "sqlite3",
        "sys",
        "tempfile",
        "datetime",
        "pathlib",
        "typing",
    }

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level = alias.name.split(".")[0]
                assert top_level in _STDLIB_MODULES, (
                    f"Non-stdlib top-level import found: {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level = node.module.split(".")[0]
            assert top_level in _STDLIB_MODULES, (
                f"Non-stdlib top-level import found: {node.module}"
            )


def test_manual_smoke_script_module_imports_cleanly_without_network() -> None:
    """Actually importing the script module (not just static AST parsing)
    must not raise and must not require RUN_LIVE_PROVIDER_CACHE_SMOKE --
    top-level code is only path/constant setup, never a provider call."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "manual_provider_cache_smoke_import_test", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # must not raise, must not print RESULT:

    assert hasattr(module, "main")
    assert hasattr(module, "_check_guardrail")


# ---------------------------------------------------------------------------
# 7. Script checks all five expected providers, including OSM geocoding
#    (Step 164F) and OSM/Overpass POI search (Step 164H).
# ---------------------------------------------------------------------------


def test_manual_smoke_script_checks_five_expected_providers() -> None:
    source = _SCRIPT_PATH.read_text()
    for provider_source in _EXPECTED_SOURCES:
        assert provider_source in source
    assert "open_meteo_adapter" in source
    assert "nager_date_adapter" in source
    assert "frankfurter_adapter" in source
    assert "openstreetmap_adapter" in source


def test_manual_smoke_script_includes_osm_geocoding_coverage() -> None:
    source = _SCRIPT_PATH.read_text()
    assert "openstreetmap_geocode" in source
    assert "OpenStreetMapPlacesAdapter" in source
    assert "resolve_coordinates" in source


def test_manual_smoke_script_includes_osm_poi_coverage() -> None:
    """Step 164H: the script calls exactly one Overpass POI method,
    `search_attractions`, and checks cache rows for source
    `"openstreetmap_poi"`."""
    source = _SCRIPT_PATH.read_text()
    assert "openstreetmap_poi" in source
    assert ".search_attractions(" in source


def test_manual_smoke_script_limits_overpass_calls_to_search_attractions() -> None:
    """The script must never call `search_restaurants`,
    `search_accommodation_pois`, `search_must_visit_place`, or Overpass
    directly (`_query_overpass`) -- only the one respectful
    `search_attractions` call. Comments explaining this intentional
    limitation (e.g. the word "Overpass" itself) are expected and fine --
    only actual call sites are disallowed."""
    source = _SCRIPT_PATH.read_text()
    for disallowed_call in (
        ".search_restaurants(",
        ".search_accommodation_pois(",
        ".search_must_visit_place(",
        "._query_overpass(",
    ):
        assert disallowed_call not in source, f"Unexpected Overpass/POI call: {disallowed_call}"


# ---------------------------------------------------------------------------
# 8. Script does not print raw payload contents -- only the safe summary
#    fields (provider name, live_path_ok, cache_path_ok, status,
#    cache_row_count).
# ---------------------------------------------------------------------------


def test_manual_smoke_script_does_not_print_raw_payload() -> None:
    source = _SCRIPT_PATH.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            continue
        printed_source = (ast.get_source_segment(source, node) or "").lower()
        assert "payload" not in printed_source
        assert ".data)" not in printed_source
        assert "model_dump" not in printed_source
        assert "json.dumps" not in printed_source


def test_manual_smoke_script_does_not_print_raw_overpass_query_text() -> None:
    """No `print()` call may reference the raw Overpass tag filters/query
    text or a raw destination string -- only the safe summary fields."""
    source = _SCRIPT_PATH.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            continue
        printed_source = (ast.get_source_segment(source, node) or "").lower()
        assert "tag_filters" not in printed_source
        assert "known_destination" not in printed_source
        assert "overpass_url" not in printed_source


# ---------------------------------------------------------------------------
# 9. Script uses a temporary/clearly-named cache path, never the app's real
#    default provider cache path.
# ---------------------------------------------------------------------------


def test_manual_smoke_script_uses_safe_temporary_cache_path() -> None:
    source = _SCRIPT_PATH.read_text()
    assert "tempfile" in source
    assert "manual_provider_cache_smoke" in source
    # Never reuses the app's real default provider cache path/file.
    assert ".data/provider_cache.sqlite3" not in source
    assert "travelobligator_state.json" not in source


def test_manual_smoke_script_never_writes_default_provider_cache_path(
    tmp_path: Path,
) -> None:
    """Running the guarded-off path must never create the real repo-local
    provider cache file as a side effect."""
    default_cache_path = _REPO_ROOT / "backend" / ".data" / "provider_cache.sqlite3"
    existed_before = default_cache_path.exists()
    mtime_before = default_cache_path.stat().st_mtime if existed_before else None

    _run_script(_clean_env())

    if existed_before:
        assert default_cache_path.stat().st_mtime == mtime_before
    else:
        assert not default_cache_path.exists()


# ---------------------------------------------------------------------------
# 10. Docs exist and cover the required warnings.
# ---------------------------------------------------------------------------


def test_manual_smoke_doc_exists_and_covers_required_sections() -> None:
    assert _DOC_PATH.is_file()
    text = _DOC_PATH.read_text()
    lowered = text.lower()
    assert "manual" in lowered
    assert "purpose" in lowered
    assert "how to run" in lowered or "how to run manually" in lowered
    assert "RUN_LIVE_PROVIDER_CACHE_SMOKE" in text
    assert "ci" in lowered
    assert "does not mean" in lowered
    assert "exact" in lowered


def test_manual_smoke_doc_warns_values_are_not_asserted_exactly() -> None:
    text = _DOC_PATH.read_text().lower()
    assert "not asserted exactly" in text or "never asserts an exact" in text or "not assert" in text


def test_manual_smoke_doc_warns_this_is_not_ci() -> None:
    text = _DOC_PATH.read_text().lower()
    assert "not part of ci" in text or "never run by ci" in text or "not run by ci" in text or "never runs in ci" in text


# ---------------------------------------------------------------------------
# 11. Docs clarify OSM coverage is geocoding only, and does not validate
#     OSM/Overpass POI behavior (Step 164F).
# ---------------------------------------------------------------------------


def test_manual_smoke_doc_mentions_osm_geocoding_coverage() -> None:
    """OSM geocoding coverage (Step 164F) remains part of the script even
    after Step 164H added POI coverage alongside it -- the doc no longer
    says "geocoding only" (that phrase became inaccurate once POI coverage
    was added), but it must still clearly describe the geocoding check."""
    text = _DOC_PATH.read_text().lower()
    assert "openstreetmap_geocode" in text
    assert "resolve_coordinates" in text


def test_manual_smoke_doc_clarifies_it_does_not_validate_overpass_poi() -> None:
    text = _DOC_PATH.read_text().lower()
    assert "overpass" in text
    assert "does not" in text


# ---------------------------------------------------------------------------
# 12. Docs clarify OSM/Overpass POI smoke scope (Step 164H): structural
#     validation only, not every POI/category/destination, and no
#     rating/price/opening-hours/booking/availability/route-time claim.
# ---------------------------------------------------------------------------


def test_manual_smoke_doc_clarifies_poi_smoke_is_structural_only() -> None:
    text = _DOC_PATH.read_text().lower()
    assert "openstreetmap_poi" in text
    assert "structural" in text


def test_manual_smoke_doc_clarifies_poi_pass_does_not_cover_every_destination() -> None:
    text = _DOC_PATH.read_text().lower()
    assert "every" in text
    assert "poi" in text or "category" in text or "destination" in text


def test_manual_smoke_doc_clarifies_poi_smoke_excludes_commercial_claims() -> None:
    text = _DOC_PATH.read_text().lower()
    for claim in ("rating", "price", "opening hours", "booking", "availability", "route time"):
        assert claim in text, f"doc does not mention excluded claim: {claim!r}"


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
