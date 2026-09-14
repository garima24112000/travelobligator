from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.generation_job import GenerationJob, GenerationJobStatus, GenerationJobType

# Public API shapes for the async job foundation (Step 186C,
# docs/14_backend_architecture.md section 117). `JobResponseData` mirrors
# `GenerationJob` field-for-field, with one deliberate omission:
# `owner_id`. Every job endpoint is already owner-scoped via
# `require_trip_owner` before this schema is ever built, so the caller
# already knows they own it -- there is no reason to additionally echo
# their own user id back in the response body, and never any other
# user's. No secret, password, session token, API key, or stack trace is
# ever included (see `GenerationJob`'s own docstring for that contract) --
# `error_message` is always the same short, controlled string
# `mark_job_failed` was given, never a raw exception.


class JobResponseData(BaseModel):
    job_id: str
    trip_id: str
    job_type: GenerationJobType
    status: GenerationJobStatus
    progress_stage: str | None = None
    message: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result_version: str | None = None
    changed_sections: list[str] = Field(default_factory=list)

    @classmethod
    def from_job(cls, job: GenerationJob) -> "JobResponseData":
        return cls(
            job_id=job.job_id,
            trip_id=job.trip_id,
            job_type=job.job_type,
            status=job.status,
            progress_stage=job.progress_stage,
            message=job.message,
            error_code=job.error_code,
            error_message=job.error_message,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            result_version=job.result_version,
            changed_sections=list(job.changed_sections),
        )


class StartJobResponseData(JobResponseData):
    """Returned by `POST /trips/{trip_id}/generate`/`.../regenerate` (202
    Accepted) the moment a job is created -- structurally identical to
    `JobResponseData` (`status` is always `"queued"` at this exact
    moment, since the background task that would advance it hasn't run
    yet from the route body's own point of view). Kept as its own named
    type only so this codebase's response schemas keep naming "the job
    was just created" apart from "here is a job's current status" at the
    type level, mirroring `RegenerateResponseData` being its own named
    type despite sharing shape with other response models.
    """


class JobListResponseData(BaseModel):
    trip_id: str
    jobs: list[JobResponseData] = Field(default_factory=list)
