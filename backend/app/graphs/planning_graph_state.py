from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from app.models.planning_state import PlanningState, TripRequest

# LangGraph state schema for the planning graph skeleton (Step 171A,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md). This
# supersedes the earlier Step 162B/162C `PlanningGraphState` prototype
# (which mirrored `PlanningOrchestrator.generate_full_plan`'s exact stage
# order) with a state shape aligned to Section 170's AI candidate review/
# promotion/provider-coverage concepts -- still architecture/resume
# foundation only, not wired into `/generate` or any API route.
#
# `planning_state` remains the single source of truth (docs/CLAUDE.md's
# core rule) -- this graph state only ever carries it through node calls,
# never reconstructs or duplicates any of its fields. `trip_id`/
# `trip_request` are carried alongside it only because a graph run may want
# to identify/validate the run without reaching into `planning_state.
# trip_id`/`planning_state.trip_request` every time -- they are never a
# second, divergent copy a node is allowed to mutate independently.
#
# `errors`/`warnings`/`completed_nodes`/`failed_nodes` all use LangGraph's
# `operator.add` reducer, so each node only ever returns the *new* entries
# it wants appended -- never the full accumulated list -- and LangGraph
# merges them across the run. This mirrors the existing `executed_nodes`/
# `errors` pattern the superseded prototype already used successfully.


class PlanningGraphState(TypedDict):
    """LangGraph state schema for the planning graph skeleton.

    - `trip_id`: the trip this graph run is for (never invented; always the
      caller's own trip id).
    - `trip_request`: the original trip request, read-only for every node.
    - `planning_state`: the single source of truth, mutated only by calling
      an existing deterministic stage/service's own method -- no node
      duplicates that logic itself.
    - `errors`: safe, secret-free, non-exception-text messages appended
      only when a node fails (see `planning_graph_nodes.py`); empty for a
      fully successful run.
    - `warnings`: honest, non-fabricated notes about the run (e.g. a
      summary if some nodes failed); never a fabricated travel fact.
    - `completed_nodes`: which nodes finished successfully, in the order
      they ran -- a test/debug trace only.
    - `failed_nodes`: which nodes raised and were caught safely -- a
      failed node still lets the graph continue to the next node rather
      than crashing the whole run, and never fabricates `planning_state`
      data to compensate for the failure.
    """

    trip_id: str
    trip_request: TripRequest
    planning_state: PlanningState
    errors: Annotated[list[str], operator.add]
    warnings: Annotated[list[str], operator.add]
    completed_nodes: Annotated[list[str], operator.add]
    failed_nodes: Annotated[list[str], operator.add]


def build_initial_planning_graph_state(
    trip_id: str,
    trip_request: TripRequest,
    planning_state: PlanningState,
) -> PlanningGraphState:
    """Builds a fresh `PlanningGraphState` from an already-existing
    `trip_id`/`TripRequest`/`PlanningState` -- never invents any of the
    three, and always starts with empty `errors`/`warnings`/
    `completed_nodes`/`failed_nodes` so a graph run's trace reflects only
    what actually happened during that run.
    """
    return {
        "trip_id": trip_id,
        "trip_request": trip_request,
        "planning_state": planning_state,
        "errors": [],
        "warnings": [],
        "completed_nodes": [],
        "failed_nodes": [],
    }
