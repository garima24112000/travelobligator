from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.models.planning_state import PlanningState

# Section 197B (docs/14_backend_architecture.md, following section 148):
# the execution-result contract. Modeled as a plain dataclass, not a
# Pydantic `BaseModel`, mirroring `RegenerationMutationResult`
# (`app.services.regeneration_mutation_service`) -- the one existing
# repository convention for "a service result that carries a whole
# `PlanningState`" -- rather than nesting a full Pydantic model inside
# another (unnecessary double validation for an internal, non-API-facing
# result object).


class TargetedRegenerationExecutionStatus(str, Enum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    NEEDS_CLARIFICATION = "needs_clarification"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    REASONING_FAILED = "reasoning_failed"


@dataclass
class TargetedRegenerationExecutionResult:
    """Task 2's execution result. `resulting_planning_state` is populated
    ONLY when `status == COMPLETED` -- every other status leaves it
    `None`, so a caller can never mistake a non-completed result for a
    usable new state (Task 3: "a failed execution must retain no partial
    mutation of the source PlanningState" -- enforced here by simply
    never returning one). The source `PlanningState` passed into
    `TargetedRegenerationExecutor.execute` is never mutated regardless of
    outcome; this result's own `resulting_planning_state`, when present,
    is always a separate, deep-copied working state (Task 4/35: 197B
    never persists it).
    """

    trip_id: str
    status: TargetedRegenerationExecutionStatus
    source_version: str | None = None
    resulting_planning_state: PlanningState | None = None

    affected_day_indices: list[int] = field(default_factory=list)
    preserved_day_indices: list[int] = field(default_factory=list)
    affected_experience_ids: list[str] = field(default_factory=list)

    deterministic_edits_applied: list[str] = field(default_factory=list)
    provider_lookup_status: str | None = None
    reasoning_status: str | None = None
    routing_rerun: bool = False
    validation_status: str | None = None
    repair_attempted: bool = False
    repair_status: str | None = None
    narrator_status: str | None = None
    preservation_audit_passed: bool | None = None

    block_reasons: list[str] = field(default_factory=list)
    failure_reason: str | None = None
    clarification_reason: str | None = None

    def is_completed(self) -> bool:
        return self.status == TargetedRegenerationExecutionStatus.COMPLETED
