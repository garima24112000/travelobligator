"""Where one generation's wall-clock time went (Section 1A, measurement only).

Built by `app/core/performance.py`. Numbers under fixed keys only: never a
query, URL, key, prompt, model output or place name. Every field is
optional, so a state stored before this report existed -- or a generation
whose timing could not be recorded -- loads and behaves exactly as before.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LLMStagePerformance(BaseModel):
    """One model stage of a generation (Section 1C). Counts and fixed
    labels only -- never a prompt or any model output."""

    # HTTP/model request attempts made (the first one included).
    attempts: int = 0
    structural_retries: int = 0
    transport_retries: int = 0
    # The stage's total wall-clock budget ran out.
    deadline_exceeded: bool = False
    # `success` | `fallback` (an answer arrived but was rejected) | `failed`
    # (no usable answer). For both of the latter the pipeline's
    # deterministic fallback was used.
    result: str | None = None


class GenerationPerformanceReport(BaseModel):
    # Which engine produced the plan (`langgraph` / `legacy`).
    engine: str | None = None
    # Wall-clock of the whole generation call, monotonic clock.
    total_ms: float | None = None
    # Per stage, EXCLUSIVE of the stages nested inside it, so
    # `sum(stage_ms) + other_ms == total_ms`.
    stage_ms: dict[str, float] = Field(default_factory=dict)
    # Per stage, including everything nested inside it.
    stage_inclusive_ms: dict[str, float] = Field(default_factory=dict)
    other_ms: float | None = None
    # Wall-clock spent waiting on external requests, per provider/API.
    # Every attempt counts, including a failed one and a retry.
    provider_ms: dict[str, float] = Field(default_factory=dict)
    provider_attempts: dict[str, int] = Field(default_factory=dict)
    # Request counts. The Geoapify ones are the usage tracker's own
    # (successful, charged requests); the Groq ones are request attempts.
    counts: dict[str, int] = Field(default_factory=dict)
    cache_hits: dict[str, int] = Field(default_factory=dict)
    cache_misses: dict[str, int] = Field(default_factory=dict)
    # How many requests of each kind were made, and how many of those
    # repeated an identical earlier request in the same generation.
    request_totals: dict[str, int] = Field(default_factory=dict)
    redundant_requests: dict[str, int] = Field(default_factory=dict)
    # Section 1B: concurrency diagnostics (reported, never judged).
    # Time spent by stages INSIDE concurrent batch tasks, summed over the
    # tasks. It overlaps the wall-clock of the stage that waited for the
    # batch, so it is not part of `stage_ms` and can exceed `total_ms`.
    stage_task_ms: dict[str, float] = Field(default_factory=dict)
    # The most Geoapify requests this generation had in flight at once.
    peak_geoapify_concurrency: int = 0
    concurrent_batches: int = 0
    # The size of every concurrent batch dispatched, per operation.
    batch_sizes: dict[str, list[int]] = Field(default_factory=dict)
    # Section 1C. The most Geoapify requests the whole PROCESS (every
    # generation in it) had in flight while this generation was making one.
    process_peak_geoapify_concurrency: int = 0
    # What each generation-time model stage did, keyed by stage
    # (`groq_anchor`, `groq_reasoning`, `groq_repair`, `groq_narrator`):
    # request attempts, the structural / transport retries among them,
    # whether the stage's total budget ran out, and how it ended.
    llm_stages: dict[str, LLMStagePerformance] = Field(default_factory=dict)
    # False on the report stored with the plan: it is written by the final
    # commit, so it cannot contain that commit's own duration. True only on
    # a report a profiling caller (the canary) builds from its own recorder
    # after the generation has returned.
    includes_final_commit: bool = False
