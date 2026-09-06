from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.planning_state import PlanningState


class LangGraphShadowRunResponseData(BaseModel):
    """Response shape for `POST /trips/{trip_id}/langgraph-shadow-run`
    (Step 171C, docs/13_llm_reasoning_pipeline.md,
    docs/14_backend_architecture.md).

    `planning_state` here is a *preview/result* of a shadow LangGraph run
    against an isolated deep copy of the trip's current `PlanningState` --
    it is never the trip's official, stored `PlanningState`, and this
    endpoint never saves it anywhere. `persisted` is always `False`
    (structurally enforced via `Literal[False]`, mirroring the same
    pattern `ScrapedDataProvenance.official_provider: Literal[False]`
    already uses elsewhere in this codebase) so a future bug can never
    silently claim this preview was persisted.

    `status` is derived purely from `failed_nodes`: `"completed"` when
    empty, `"completed_with_failures"` otherwise -- never a claim that the
    resulting plan is ready, verified, or official; that judgment stays
    `ValidationReport.readiness_status`'s job alone, computed the same way
    it always is inside the graph's own `validation` node.
    """

    trip_id: str
    status: str
    planning_state: PlanningState
    completed_nodes: list[str] = Field(default_factory=list)
    failed_nodes: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    persisted: Literal[False] = False
