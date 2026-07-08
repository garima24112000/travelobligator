from __future__ import annotations

from app.models.planning_state import PlanningState


class PlanningStageService:
    """Shared shape for a planning pipeline stage service.

    Per docs/09_planning_state.md section 19 and docs/14_backend_architecture.md
    section 8, each stage service reads only the PlanningState sections it
    needs and updates only the section(s) it owns. `run` should never raise
    for missing upstream/provider data - it should mark the owned section as
    incomplete/unavailable and let PlanValidatorService surface the risk.
    """

    def run(self, planning_state: PlanningState) -> PlanningState:
        raise NotImplementedError
