from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.models.planning_state import PlanningState
from app.models.targeted_regeneration_diff import TargetedRegenerationDiff

# Section 197C: the application-service-level result contract, mirroring
# `RegenerationMutationResult`/`TargetedRegenerationExecutionResult`'s
# own plain-dataclass convention for "a service result that may carry a
# whole PlanningState."


class TargetedRegenerationRuntimeStatus(str, Enum):
    COMPLETED = "completed"
    NEEDS_CLARIFICATION = "needs_clarification"
    BLOCKED = "blocked"
    FAILED = "failed"
    CONFLICT = "conflict"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


@dataclass
class TargetedRegenerationRuntimeResult:
    trip_id: str
    status: TargetedRegenerationRuntimeStatus
    message: str

    feedback_event_id: str | None = None
    interpretation_status: str | None = None
    plan_status: str | None = None
    execution_status: str | None = None

    source_version: str | None = None
    new_version: str | None = None

    resulting_planning_state: PlanningState | None = None
    diff: TargetedRegenerationDiff | None = None

    clarification_reason: str | None = None
    clarification_possible_experience_ids: list[str] = field(default_factory=list)
    block_reasons: list[str] = field(default_factory=list)

    def is_completed(self) -> bool:
        return self.status == TargetedRegenerationRuntimeStatus.COMPLETED
