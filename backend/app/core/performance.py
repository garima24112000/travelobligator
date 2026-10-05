"""Per-generation latency profiling (Section 1A, measurement only).

One `PerformanceRecorder` is created at a generation entry point and made
the ACTIVE recorder for that generation (`activate`), in a context
variable: never a process-global, so two simultaneous generations each
time only themselves. Code on the generation path reports to it through
the module functions below, which are no-ops when no generation is being
profiled and never raise -- a timing problem can never fail, slow down in
any meaningful way, or change a generation.

Three independent measurements:

  * `stage(name)` -- wall-clock per pipeline stage. Stages nest; a stage's
    time is EXCLUSIVE of the stages opened inside it, so the stage times
    plus `other_ms` add up to `total_ms`. The inclusive figure is kept
    alongside.
  * `provider_call(api)` -- wall-clock spent waiting on one external
    provider request, accumulated per provider/API. Every attempt counts,
    including a failed one and a retry.
  * `note_request(kind, *parts)` -- how often the SAME request was made
    in this generation. Only a one-way hash of the request is held, and
    only counts are ever reported.

Everything recorded is a number under a fixed lower-case key. No query,
URL, key, prompt, model output or place name is stored.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from contextvars import ContextVar
from typing import Any, Callable, Iterator
from contextlib import contextmanager

_KEY_PATTERN = re.compile(r"^[a-z0-9_]{1,48}$")

# How the usage tracker's Geoapify API names (`core/provider_usage.py`) are
# reported here. The tracker stays the one owner of request COUNTS; these
# names are only used for wall-clock time and the report's count labels.
GEOAPIFY_PROVIDER_KEYS: dict[str, str] = {
    "geocoding": "geoapify_geocoding",
    "places": "geoapify_places",
    "place_details": "geoapify_details",
    "routing": "geoapify_routing_walk",
    "routing_drive": "geoapify_routing_drive",
}
_GEOAPIFY_COUNT_LABELS: dict[str, str] = {
    "geocoding": "geocode_requests",
    "places": "places_requests",
    "place_details": "details_requests",
    "routing": "walk_route_requests",
    "routing_drive": "drive_route_requests",
}
_GROQ_COUNT_LABELS: dict[str, str] = {
    "groq_anchor": "groq_anchor_calls",
    "groq_reasoning": "groq_reasoning_calls",
    "groq_repair": "groq_repair_calls",
    "groq_narrator": "groq_narrator_calls",
}


# A Gemini request of a stage is counted under its own label, reported only
# when the generation made one (Groq <-> Gemini resilience group).
_GEMINI_COUNT_LABELS: dict[str, str] = {
    "gemini_anchor": "gemini_anchor_calls",
    "gemini_reasoning": "gemini_reasoning_calls",
    "gemini_repair": "gemini_repair_calls",
    "gemini_narrator": "gemini_narrator_calls",
}


def _safe_key(name: object) -> str | None:
    return name if isinstance(name, str) and _KEY_PATTERN.match(name) else None


def _safe_number(value: object) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if value == value and value not in (float("inf"), float("-inf")) else None


def _safe_labels(values: object) -> list[str]:
    return [value for value in values if _safe_key(value)] if isinstance(values, (list, tuple)) else []


# The only provider-health states and quota figures a stage may report.
_LLM_PROVIDER_STATES = frozenset({"healthy", "draining", "open", "half_open"})
_LLM_QUOTA_KEYS: dict[str, frozenset[str]] = {
    "groq_quota": frozenset({"remaining_request_ratio", "remaining_token_ratio", "reset_seconds"}),
    "gemini_quota": frozenset({"configured_rpm", "configured_tpm", "configured_rpd", "advisory_remaining_ratio"}),
}


def _safe_state_map(values: object) -> dict[str, str]:
    if not isinstance(values, dict):
        return {}
    return {key: value for key, value in values.items() if _safe_key(key) and value in _LLM_PROVIDER_STATES}


def _safe_quota(values: object, allowed: frozenset[str]) -> dict[str, float | int | None]:
    if not isinstance(values, dict):
        return {}
    return {key: _safe_number(value) for key, value in values.items() if key in allowed}


def sanitize_llm_providers(providers: object) -> dict[str, Any]:
    """A stage's provider record (`llm_provider_router.StageRoute.diagnostics`)
    reduced to fixed lower-case labels and numbers. Anything that is not
    one is dropped, so no key, header, prompt, model answer or URL can be
    carried into the report through it."""
    if not isinstance(providers, dict):
        return {}
    clean: dict[str, Any] = {
        "preferred_provider": _safe_key(providers.get("preferred_provider")),
        "selected_provider": _safe_key(providers.get("selected_provider")),
        "attempted_providers": _safe_labels(providers.get("attempted_providers")),
        "final_provider": _safe_key(providers.get("final_provider")),
        "failover_used": bool(providers.get("failover_used")),
        "failover_reason": _safe_key(providers.get("failover_reason")),
        "provider_health_before": _safe_state_map(providers.get("provider_health_before")),
        "provider_health_after": _safe_state_map(providers.get("provider_health_after")),
        "total_provider_requests": int(_safe_number(providers.get("total_provider_requests")) or 0),
        "provider_attempts": [
            {
                "provider": _safe_key(attempt.get("provider")),
                "result": _safe_key(attempt.get("result")),
                "transport_failure_kind": _safe_key(attempt.get("transport_failure_kind")),
                "duration_ms": _safe_number(attempt.get("duration_ms")),
                "structural_validation_result": _safe_key(attempt.get("structural_validation_result")),
            }
            for attempt in providers.get("provider_attempts") or []
            if isinstance(attempt, dict)
        ],
    }
    for key, allowed in _LLM_QUOTA_KEYS.items():
        quota = _safe_quota(providers.get(key), allowed)
        if quota:
            clean[key] = quota
    return clean


class PerformanceRecorder:
    """Timings and counts for ONE generation. Thread-safe."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._stacks = threading.local()
        self._started_at: float | None = None
        self._stage_ms: dict[str, float] = {}
        self._stage_inclusive_ms: dict[str, float] = {}
        self._provider_ms: dict[str, float] = {}
        self._provider_attempts: dict[str, int] = {}
        self._requests: dict[str, dict[str, int]] = {}
        self._cache_hits: dict[str, int] = {}
        self._cache_misses: dict[str, int] = {}
        self._counts: dict[str, int] = {}
        self._stage_task_ms: dict[str, float] = {}
        self._batch_sizes: dict[str, list[int]] = {}
        self._peak_in_flight: dict[str, int] = {}
        self._llm_stages: dict[str, dict[str, Any]] = {}

    def start(self) -> None:
        self._started_at = self._clock()

    # -- stages ------------------------------------------------------------------

    def _stack(self) -> list[list[Any]]:
        stack = getattr(self._stacks, "frames", None)
        if stack is None:
            stack = self._stacks.frames = []
        return stack

    def push_stage(self, name: str) -> None:
        self._stack().append([name, self._clock(), 0.0])

    def pop_stage(self) -> None:
        stack = self._stack()
        name, started_at, child_ms = stack.pop()
        elapsed_ms = (self._clock() - started_at) * 1000.0
        if stack:
            stack[-1][2] += elapsed_ms
        nested_in_itself = any(frame[0] == name for frame in stack)
        exclusive_ms = max(0.0, elapsed_ms - child_ms)
        with self._lock:
            if _IN_CONCURRENT_TASK.get():
                # Inside a concurrent batch the wall-clock belongs to the
                # stage that is waiting for the batch; what the tasks spent
                # is summed separately and may exceed that wall-clock.
                self._stage_task_ms[name] = self._stage_task_ms.get(name, 0.0) + exclusive_ms
                return
            self._stage_ms[name] = self._stage_ms.get(name, 0.0) + exclusive_ms
            if not nested_in_itself:
                self._stage_inclusive_ms[name] = self._stage_inclusive_ms.get(name, 0.0) + elapsed_ms

    # -- providers / requests / counts -----------------------------------------------

    def now(self) -> float:
        return self._clock()

    def add_provider_time(self, api: str, started_at: float) -> None:
        elapsed_ms = max(0.0, (self._clock() - started_at) * 1000.0)
        with self._lock:
            self._provider_ms[api] = self._provider_ms.get(api, 0.0) + elapsed_ms
            self._provider_attempts[api] = self._provider_attempts.get(api, 0) + 1

    def note_request(self, kind: str, fingerprint: str) -> None:
        with self._lock:
            seen = self._requests.setdefault(kind, {})
            seen[fingerprint] = seen.get(fingerprint, 0) + 1

    def note_cache(self, source: str, hit: bool) -> None:
        with self._lock:
            target = self._cache_hits if hit else self._cache_misses
            target[source] = target.get(source, 0) + 1

    def count(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counts[name] = self._counts.get(name, 0) + amount

    def note_batch(self, operation: str, size: int) -> None:
        with self._lock:
            self._batch_sizes.setdefault(operation, []).append(size)

    def note_in_flight(self, provider: str, in_flight: int) -> None:
        with self._lock:
            if in_flight > self._peak_in_flight.get(provider, 0):
                self._peak_in_flight[provider] = in_flight

    def note_llm_stage(
        self,
        stage: str,
        attempts: int,
        structural_retries: int,
        transport_retries: int,
        deadline_exceeded: bool,
        result: str,
        transport_failure: str | None = None,
        retry_after: str | None = None,
        providers: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            entry = self._llm_stages.setdefault(
                stage,
                {
                    "attempts": 0,
                    "structural_retries": 0,
                    "transport_retries": 0,
                    "deadline_exceeded": False,
                    "result": result,
                },
            )
            # Present only when there is something to report.
            if transport_failure:
                entry["transport_failure"] = transport_failure
            if retry_after:
                entry["retry_after"] = retry_after
            entry["attempts"] += attempts
            entry["structural_retries"] += structural_retries
            entry["transport_retries"] += transport_retries
            entry["deadline_exceeded"] = bool(entry["deadline_exceeded"] or deadline_exceeded)
            entry["result"] = result
            if providers:
                # A stage can run more than once in a generation (repair):
                # the requests add up, the rest describes the latest run.
                earlier = entry.get("provider_attempts") or []
                entry.update(providers)
                entry["provider_attempts"] = [*earlier, *providers.get("provider_attempts", [])]
                entry["attempted_providers"] = [attempt["provider"] for attempt in entry["provider_attempts"]]
                entry["total_provider_requests"] = len(entry["provider_attempts"])
                entry["failover_used"] = bool(entry.get("failover_used")) or len(set(entry["attempted_providers"])) > 1

    def set_llm_result(self, stage: str, result: str) -> None:
        with self._lock:
            if stage in self._llm_stages:
                self._llm_stages[stage]["result"] = result

    # -- report ------------------------------------------------------------------

    def snapshot(self, usage: dict[str, Any] | None = None) -> dict[str, Any]:
        """The report as plain data. `usage` is the generation's
        `ProviderUsageTracker.snapshot()`: Geoapify request counts are read
        from it, never counted a second time here."""
        now = self._clock()
        with self._lock:
            total_ms = (now - self._started_at) * 1000.0 if self._started_at is not None else None
            stage_ms = {name: round(value, 1) for name, value in self._stage_ms.items()}
            counts = dict(self._counts)
            calls_by_api = (usage or {}).get("calls_by_api") or {}
            for api, label in _GEOAPIFY_COUNT_LABELS.items():
                counts[label] = int(calls_by_api.get(api, 0))
            for api, label in _GROQ_COUNT_LABELS.items():
                counts[label] = self._provider_attempts.get(api, 0)
            for api, label in _GEMINI_COUNT_LABELS.items():
                if self._provider_attempts.get(api):
                    counts[label] = self._provider_attempts[api]
            counts["cache_hits"] = sum(self._cache_hits.values())
            return {
                "total_ms": round(total_ms, 1) if total_ms is not None else None,
                "stage_ms": stage_ms,
                "stage_inclusive_ms": {name: round(value, 1) for name, value in self._stage_inclusive_ms.items()},
                "other_ms": (
                    round(max(0.0, total_ms - sum(self._stage_ms.values())), 1) if total_ms is not None else None
                ),
                "provider_ms": {name: round(value, 1) for name, value in self._provider_ms.items()},
                "provider_attempts": dict(self._provider_attempts),
                "counts": counts,
                "cache_hits": dict(self._cache_hits),
                "cache_misses": dict(self._cache_misses),
                "request_totals": {kind: sum(seen.values()) for kind, seen in self._requests.items()},
                "redundant_requests": {
                    kind: sum(times - 1 for times in seen.values()) for kind, seen in self._requests.items()
                },
                # Section 1B: concurrency diagnostics.
                "stage_task_ms": {name: round(value, 1) for name, value in self._stage_task_ms.items()},
                "peak_geoapify_concurrency": self._peak_in_flight.get("geoapify", 0),
                # Section 1C: the most Geoapify requests the whole PROCESS had
                # in flight while this generation was making one, and what
                # each model stage did (counts and fixed labels only).
                "process_peak_geoapify_concurrency": self._peak_in_flight.get("geoapify_process", 0),
                "llm_stages": {stage: dict(entry) for stage, entry in self._llm_stages.items()},
                "concurrent_batches": sum(len(sizes) for sizes in self._batch_sizes.values()),
                "batch_sizes": {operation: list(sizes) for operation, sizes in self._batch_sizes.items()},
            }


_ACTIVE: ContextVar[PerformanceRecorder | None] = ContextVar("generation_performance_recorder", default=None)
# True inside a task of a concurrent batch (`core/bounded_concurrency.py`).
_IN_CONCURRENT_TASK: ContextVar[bool] = ContextVar("generation_performance_concurrent_task", default=False)


def mark_concurrent_task() -> None:
    """Called by a batch worker, in its own copy of the context."""
    _IN_CONCURRENT_TASK.set(True)


def note_batch(operation: str, size: int) -> None:
    """One concurrent batch of `size` tasks was dispatched for `operation`."""
    try:
        recorder = _ACTIVE.get()
        if recorder is not None and _safe_key(operation) is not None:
            recorder.note_batch(operation, int(size))
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


_LLM_RESULTS = frozenset({"success", "fallback", "failed"})
# Section 3B: the only labels a stage may report (see `providers/ai_failure`
# and `providers/ai_stage_budget`).
_LLM_TRANSPORT_FAILURES = frozenset({"rate_limit", "timeout", "network", "server_error", "other_transport"})
_LLM_RETRY_AFTER = frozenset({"obeyed", "clamped", "skipped"})


def note_llm_stage(
    stage: str,
    *,
    attempts: int,
    structural_retries: int,
    transport_retries: int,
    deadline_exceeded: bool,
    result: str,
    transport_failure: str | None = None,
    retry_after: str | None = None,
    providers: dict[str, Any] | None = None,
) -> None:
    """What one model stage did: request attempts, the retries among them,
    whether its total budget ran out, and how it ended -- plus, as fixed
    labels only, the kind of its last transport failure and what was done
    with a rate limit's Retry-After (anything else is dropped). `providers`
    is the stage's provider record (which provider was preferred, tried and
    final, and why it switched), kept as fixed labels and numbers only."""
    try:
        recorder = _ACTIVE.get()
        if recorder is not None and _safe_key(stage) is not None and result in _LLM_RESULTS:
            recorder.note_llm_stage(
                stage, int(attempts), int(structural_retries), int(transport_retries), bool(deadline_exceeded), result,
                transport_failure if transport_failure in _LLM_TRANSPORT_FAILURES else None,
                retry_after if retry_after in _LLM_RETRY_AFTER else None,
                sanitize_llm_providers(providers) if providers else None,
            )
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


def set_llm_result(stage: str, result: str) -> None:
    """Corrects a recorded stage's final result (e.g. the narrator's text
    was rejected after the model answered, so the fallback was used)."""
    try:
        recorder = _ACTIVE.get()
        if recorder is not None and _safe_key(stage) is not None and result in _LLM_RESULTS:
            recorder.set_llm_result(stage, result)
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


def note_in_flight(provider: str, in_flight: int) -> None:
    """`in_flight` requests to `provider` are on the wire right now."""
    try:
        recorder = _ACTIVE.get()
        if recorder is not None and _safe_key(provider) is not None:
            recorder.note_in_flight(provider, int(in_flight))
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


def current() -> PerformanceRecorder | None:
    return _ACTIVE.get()


@contextmanager
def activate(recorder: PerformanceRecorder | None) -> Iterator[PerformanceRecorder | None]:
    """Makes `recorder` the active one for the enclosed generation."""
    token = _ACTIVE.set(recorder)
    try:
        yield recorder
    finally:
        _ACTIVE.reset(token)


class _StageSpan:
    __slots__ = ("_name", "_recorder")

    def __init__(self, name: str) -> None:
        self._name = name
        self._recorder: PerformanceRecorder | None = None

    def __enter__(self) -> "_StageSpan":
        try:
            recorder = _ACTIVE.get()
            if recorder is not None and _safe_key(self._name) is not None:
                recorder.push_stage(self._name)
                self._recorder = recorder
        except Exception:  # noqa: BLE001 - timing never breaks a generation
            self._recorder = None
        return self

    def __exit__(self, *exc_info: object) -> bool:
        if self._recorder is not None:
            try:
                self._recorder.pop_stage()
            except Exception:  # noqa: BLE001 - timing never breaks a generation
                pass
        return False


class _ProviderSpan:
    __slots__ = ("_api", "_recorder", "_started_at")

    def __init__(self, api: str) -> None:
        self._api = api
        self._recorder: PerformanceRecorder | None = None
        self._started_at = 0.0

    def __enter__(self) -> "_ProviderSpan":
        try:
            recorder = _ACTIVE.get()
            if recorder is not None and _safe_key(self._api) is not None:
                self._started_at = recorder.now()
                self._recorder = recorder
        except Exception:  # noqa: BLE001 - timing never breaks a generation
            self._recorder = None
        return self

    def __exit__(self, *exc_info: object) -> bool:
        if self._recorder is not None:
            try:
                self._recorder.add_provider_time(self._api, self._started_at)
            except Exception:  # noqa: BLE001 - timing never breaks a generation
                pass
        return False


def stage(name: str) -> _StageSpan:
    """`with stage("validation"): ...` -- wall-clock for one pipeline stage."""
    return _StageSpan(name)


def provider_call(api: str) -> _ProviderSpan:
    """`with provider_call("groq_narrator"): ...` -- wall-clock of ONE
    external request attempt (a failed attempt and a retry both count)."""
    return _ProviderSpan(api)


def note_request(kind: str, *parts: object) -> None:
    """Records that one request identified by `parts` was made. Only a
    one-way hash of `parts` is kept, to count repeats."""
    try:
        recorder = _ACTIVE.get()
        if recorder is None or _safe_key(kind) is None:
            return
        encoded = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=True)
        recorder.note_request(kind, hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24])
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


def note_cache(source: str, hit: bool) -> None:
    """One provider-cache read for `source` (a fixed cache namespace)."""
    try:
        recorder = _ACTIVE.get()
        if recorder is not None and _safe_key(source) is not None:
            recorder.note_cache(source, bool(hit))
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


def count(name: str, amount: int = 1) -> None:
    try:
        recorder = _ACTIVE.get()
        if recorder is not None and _safe_key(name) is not None:
            recorder.count(name, int(amount))
    except Exception:  # noqa: BLE001 - diagnostics never break a generation
        pass


def started_recorder() -> PerformanceRecorder | None:
    """A recorder whose clock is running, or None when one cannot be
    started -- the generation then simply runs unprofiled."""
    try:
        recorder = PerformanceRecorder()
        recorder.start()
        return recorder
    except Exception:  # noqa: BLE001 - timing never breaks a generation
        return None


def build_report(recorder: PerformanceRecorder | None, usage: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The recorder's report, or None when there is none or it cannot be
    built -- a generation is never failed for its timing report."""
    if recorder is None:
        return None
    try:
        return recorder.snapshot(usage)
    except Exception:  # noqa: BLE001 - timing never breaks a generation
        return None
