from __future__ import annotations

from pydantic import BaseModel

from app.models.planning_state import GenerationProgress


class GenerationProgressResponseData(BaseModel):
    trip_id: str
    generation_progress: GenerationProgress
