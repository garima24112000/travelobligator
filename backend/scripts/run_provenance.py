"""What produced a benchmark result (evaluation tooling only).

Two things a quality-benchmark record must carry so that it can be read
later, and compared with another run, without guessing:

  * the EFFECTIVE planner configuration of the process that ran it -- the
    feature flags, budgets and numerical settings that materially affect a
    generation, read from the application's own `Settings` object (never
    from the environment, so a default, a `.env` value and a shell export
    are all reported as what the planner actually used);
  * the SOURCE identity -- git HEAD, whether the working tree is dirty, and
    a fingerprint of the relevant files as they are on disk. A commit hash
    alone says nothing about uncommitted or untracked code.

Benchmark tooling only: nothing here is imported by `backend/app`, it makes
no provider or model call, and it decides nothing about a plan. Only a fixed
list of non-secret setting names is read; no key, URL or path is recorded.

    cd backend
    python scripts/run_provenance.py        # prints the source identity
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPTS_DIR.parent
_REPO_DIR = _BACKEND_DIR.parent

SCHEMA_VERSION = 1

# The files whose content decides what a generation does and how it is
# measured: the application, the evaluation tooling with its benchmark data,
# and the pinned dependencies. The application's own tests are left out --
# they cannot change a plan.
FINGERPRINT_PATHS: tuple[str, ...] = ("backend/app", "backend/scripts", "backend/requirements.txt")
FINGERPRINT_EXCLUDED_PREFIXES: tuple[str, ...] = ("backend/app/tests/",)
_DELETED = "deleted"

# `Settings` attributes recorded with every result, by what they govern.
# Names only from this list are ever read, and none of them is a secret.
SETTING_GROUPS: dict[str, tuple[str, ...]] = {
    "quality_phase_flags": (
        "day_composition_enabled",
        "route_recomposition_enabled",
        "route_burden_repair_enabled",
        "ai_day_spatial_regrouping_enabled",
        "schedule_diversity_enabled",
        "route_aware_scheduling_enabled",
        "inventory_sufficiency_gate_enabled",
    ),
    "engine_and_providers": (
        "planning_engine_mode",
        "geocoding_provider",
        "places_provider",
        "routing_provider",
        "geoapify_routing_mode",
        "persistence_backend",
        "provider_cache_backend",
        "provider_cache_enabled",
        "provider_io_concurrency_enabled",
        "async_generation_enabled",
    ),
    "model_stages": (
        "ai_candidate_proposal_provider",
        "ai_candidate_discovery_enabled",
        "ai_candidate_discovery_shadow_mode_enabled",
        "ai_itinerary_reasoning_enabled",
        "ai_itinerary_reasoning_provider",
        "ai_itinerary_reasoning_model",
        "ai_itinerary_repair_enabled",
        "itinerary_narrator_enabled",
        "itinerary_narrator_provider",
        "itinerary_narrator_model",
        "groq_model",
        "gemini_model",
        "llm_failover_enabled",
        "llm_primary_provider",
        "llm_secondary_provider",
    ),
    "reasoning_budgets": (
        "ai_itinerary_reasoning_max_candidates",
        "ai_itinerary_repair_max_attempts",
        "ai_directed_provider_discovery_max_searches",
        "ai_directed_provider_discovery_max_extra_searches",
        "itinerary_narrator_max_days",
        "itinerary_narrator_max_items_per_day",
        "groq_request_timeout_seconds",
        "groq_max_retries",
        "groq_anchor_total_budget_seconds",
        "groq_reasoning_total_budget_seconds",
        "groq_repair_total_budget_seconds",
        "groq_narrator_total_budget_seconds",
        "itinerary_narrator_timeout_seconds",
        "llm_quota_reserve_ratio",
        "llm_provider_short_cooldown_seconds",
        "llm_provider_probe_seconds",
    ),
    "provider_budgets": (
        "geoapify_max_credits_per_generation",
        "geoapify_max_place_details_per_generation",
        "geoapify_max_identity_lookups_per_generation",
        "geoapify_max_concurrent_requests",
        "geoapify_process_max_concurrent_requests",
        "geoapify_timeout_seconds",
        "geoapify_empty_result_cache_ttl_seconds",
    ),
    "routing_budgets_and_limits": (
        "route_alternate_mode_max_requests_per_generation",
        "route_recomposition_max_requests_per_generation",
        "route_burden_max_leg_seconds",
        "route_burden_max_day_seconds_relaxed",
        "route_burden_max_day_seconds_balanced",
        "route_burden_max_day_seconds_packed",
        "route_burden_max_drive_leg_seconds",
        "route_burden_max_day_transfer_seconds",
        "route_burden_repair_min_improvement_ratio",
        "route_aware_scheduling_min_improvement_seconds",
    ),
    "schedule_limits": (
        "schedule_diversity_max_per_class_per_day",
        "schedule_diversity_max_markets_per_day",
    ),
}

# Numerical values that are constants of the code, not settings. The source
# fingerprint already covers them; they are recorded so that two results can
# be compared without reading the code of each.
_CODE_CONSTANTS: tuple[tuple[str, str], ...] = (
    ("app.services.day_order_heuristics", "GEOGRAPHIC_SPREAD_THRESHOLD_KM"),
    ("app.services.day_order_heuristics", "NEAR_DAY_STOPS_KM"),
    ("app.services.day_order_heuristics", "AS_WELL_PLACED_KM"),
    ("app.services.day_composition", "MAX_EVALUATIONS"),
    ("app.services.pace_targets", "PACE_TARGET_PER_DAY"),
)


class ProvenanceCaptureError(RuntimeError):
    """The configuration or the source identity could not be captured
    reliably. The message names what is missing and never a value."""


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _plain(value: Any) -> Any:
    """A JSON-plain copy of a setting or constant (enums by value)."""
    value = getattr(value, "value", value)
    if isinstance(value, dict):
        return {str(_plain(key)): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ProvenanceCaptureError(f"a recorded value has an unsupported type: {type(value).__name__}")


# -- effective configuration --------------------------------------------------------------------


def capture_configuration(settings: Any | None = None, environ: dict[str, str] | None = None) -> dict[str, Any]:
    """The effective planner configuration of THIS process. Raises
    `ProvenanceCaptureError` when any listed setting or constant cannot be
    read: a partly captured configuration is never returned."""
    if settings is None:
        try:
            from app.core.config import get_settings

            settings = get_settings()
        except Exception as exc:  # by TYPE only -- never the exception text
            raise ProvenanceCaptureError(f"the application settings could not be loaded ({type(exc).__name__})") from None
    environ = dict(os.environ) if environ is None else environ

    missing = [name for names in SETTING_GROUPS.values() for name in names if not hasattr(settings, name)]
    if missing:
        raise ProvenanceCaptureError("settings not found: " + ", ".join(sorted(missing)))
    values = {
        group: {name: _plain(getattr(settings, name)) for name in names} for group, names in SETTING_GROUPS.items()
    }

    constants: dict[str, Any] = {}
    for module_name, constant in _CODE_CONSTANTS:
        try:
            module = __import__(module_name, fromlist=[constant])
            constants[constant] = _plain(getattr(module, constant))
        except ProvenanceCaptureError:
            raise
        except Exception as exc:
            raise ProvenanceCaptureError(f"code constant not found: {constant} ({type(exc).__name__})") from None

    # Which of the recorded settings this process's environment names
    # explicitly (names only). The VALUES above are what the planner used;
    # this only tells a default from a deliberate choice.
    fields = getattr(type(settings), "model_fields", {})
    aliases = {
        name: (getattr(fields.get(name), "alias", None) or name.upper())
        for names in SETTING_GROUPS.values()
        for name in names
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "the application's Settings object of the benchmark process (effective values)",
        "settings": values,
        "code_constants": constants,
        "set_in_process_environment": sorted(alias for alias in aliases.values() if alias in environ),
        # presence only: which model providers of the failover pair could be called
        "llm_providers_configured": {
            "groq": bool(environ.get("GROQ_API_KEY")),
            "gemini": bool(environ.get("GEMINI_API_KEY") and environ.get("GEMINI_MODEL")),
        },
        "configuration_sha256": _digest({"settings": values, "code_constants": constants}),
    }


# -- source identity ------------------------------------------------------------------------------


def _git(repo_dir: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), *args], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProvenanceCaptureError(f"git could not be run ({type(exc).__name__})") from None
    if result.returncode != 0:
        raise ProvenanceCaptureError(f"git {args[0]} failed (exit {result.returncode})")
    return result.stdout


def _relevant(path: str) -> bool:
    return bool(path) and not path.startswith(FINGERPRINT_EXCLUDED_PREFIXES)


def _in_scope(path: str) -> bool:
    """Whether a repository-relative path belongs to the fingerprint."""
    return _relevant(path) and any(path == base or path.startswith(f"{base}/") for base in FINGERPRINT_PATHS)


def fingerprinted_files(repo_dir: Path = _REPO_DIR) -> list[str]:
    """Every tracked file and every untracked, non-ignored file under
    `FINGERPRINT_PATHS` (repository-relative, sorted)."""
    listed = _git(repo_dir, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *FINGERPRINT_PATHS)
    return sorted({path for path in listed.split("\0") if _relevant(path)})


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return _DELETED  # tracked, removed from the working tree
    except OSError as exc:
        raise ProvenanceCaptureError(f"a source file could not be read ({type(exc).__name__})") from None


def source_fingerprint(repo_dir: Path = _REPO_DIR) -> str:
    """SHA-256 over one line per fingerprinted file, in path order:
    `<path>\\0<sha256 of the file's bytes on disk>\\n` (a tracked file that
    is missing on disk counts as `deleted`). The same working tree always
    gives the same value, committed or not."""
    hasher = hashlib.sha256()
    files = fingerprinted_files(repo_dir)
    if not files:
        raise ProvenanceCaptureError("no source file found to fingerprint")
    for path in files:
        hasher.update(f"{path}\0{_file_sha256(repo_dir / path)}\n".encode("utf-8"))
    return hasher.hexdigest()


def capture_source(repo_dir: Path = _REPO_DIR) -> dict[str, Any]:
    """Git HEAD, dirty status and the working-tree fingerprint. Raises
    `ProvenanceCaptureError` when git or a file cannot be read."""
    head = _git(repo_dir, "rev-parse", "HEAD").strip()
    if len(head) < 40:
        raise ProvenanceCaptureError("git HEAD is not a commit id")
    files = fingerprinted_files(repo_dir)
    fingerprint = source_fingerprint(repo_dir)

    modified: list[str] = []
    untracked: list[str] = []
    entries = _git(repo_dir, "status", "--porcelain", "-z", "--untracked-files=all").split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in "RC":  # a rename/copy is followed by its original path
            index += 1
        (untracked if code == "??" else modified).append(path)
    changed = sorted(path for path in (*modified, *untracked) if _in_scope(path))
    return {
        "schema_version": SCHEMA_VERSION,
        "git_head": head,
        "working_tree_dirty": bool(modified or untracked),
        "fingerprinted_paths_dirty": bool(changed),
        "fingerprint_sha256": fingerprint,
        "fingerprint_covers": {
            "paths": list(FINGERPRINT_PATHS),
            "excluded_prefixes": list(FINGERPRINT_EXCLUDED_PREFIXES),
            "file_count": len(files),
            "construction": "sha256 over '<path>\\0<sha256 of file bytes>\\n' for each file in path order",
        },
        # The uncommitted part of the fingerprint, file by file, so a result
        # can be matched to HEAD plus exactly these contents.
        "modified_files": sorted(path for path in modified if _in_scope(path)),
        "untracked_files": sorted(path for path in untracked if _in_scope(path)),
        "changed_file_sha256": {path: _file_sha256(repo_dir / path) for path in changed},
    }


# -- one run's provenance ---------------------------------------------------------------------------


def capture(repo_dir: Path = _REPO_DIR, settings: Any | None = None) -> dict[str, Any]:
    """Configuration and source identity together. All or nothing."""
    return {"configuration": capture_configuration(settings), "source": capture_source(repo_dir)}


def problems(provenance: Any) -> list[str]:
    """Why `provenance` is not a complete capture (empty when it is)."""
    if not isinstance(provenance, dict):
        return ["no provenance recorded"]
    found: list[str] = []
    configuration, source = provenance.get("configuration"), provenance.get("source")
    if not isinstance(configuration, dict) or not configuration.get("configuration_sha256"):
        found.append("effective configuration not captured")
    else:
        recorded = configuration.get("settings") if isinstance(configuration.get("settings"), dict) else {}
        absent = [
            name
            for group, names in SETTING_GROUPS.items()
            for name in names
            if name not in (recorded.get(group) or {})
        ]
        if absent:
            found.append("effective configuration incomplete: " + ", ".join(sorted(absent)))
    if not isinstance(source, dict) or not source.get("git_head") or not source.get("fingerprint_sha256"):
        found.append("source identity not captured")
    return found


def identity(provenance: Any) -> tuple[str | None, str | None]:
    """`(configuration digest, source fingerprint)` of a recorded provenance."""
    if not isinstance(provenance, dict):
        return None, None
    return (
        (provenance.get("configuration") or {}).get("configuration_sha256"),
        (provenance.get("source") or {}).get("fingerprint_sha256"),
    )


def main() -> int:
    try:
        print(json.dumps(capture_source(), ensure_ascii=False, indent=1))
    except ProvenanceCaptureError as exc:
        print(f"Source identity could not be captured: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
