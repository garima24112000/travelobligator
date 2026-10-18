"""Generation admission: the limits a new job must pass, decided by the repository (P0).

`GenerationJobRepositoryProtocol.admit(job, policy, now=...)` either stores `job` (queued) or raises
`AdmissionRefusedError` and stores nothing. The decision and the insert are ONE step:

  * PostgreSQL: one transaction that first takes a transaction-scoped advisory lock, so every
    admission in the deployment -- from any process or container -- is decided one at a time; the
    counts it reads therefore include every job admitted before it, and the lock is released by the
    commit or rollback itself (nothing to clean up, nothing left behind by a crash).
  * Local JSON (development / hermetic tests): the same checks under the repository's in-process lock.
    No cross-process guarantee, like the rest of that backend.

What is counted:

  * a WINDOW limit counts every job whose `created_at` is later than `now - window`, whatever its
    type (generate / regenerate) and whatever became of it (queued, running, succeeded, failed,
    interrupted). A refused request never created a row, so it counts for nothing;
  * the CONCURRENT limit counts jobs that are `queued` or `running` right now. The caller closes
    expired leases and never-claimed queued jobs first (`recover_expired_jobs`), so a job whose owner
    is gone does not keep a slot.

A limit of 0 is "not enforced". Nothing here reads Redis or process memory in PostgreSQL mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

# Key of the advisory lock that serialises admissions (an arbitrary constant, unique to this use).
ADMISSION_ADVISORY_LOCK_KEY = 7_203_001


# First key of the two-key advisory lock that serialises ONE owner's trip creations (the second key is
# a hash of the owner id), so different accounts never wait on each other.
TRIP_LIMIT_ADVISORY_LOCK_KEY = 7_203_002

# Key of the advisory lock that serialises the deployment-wide signup count, so simultaneous signups
# cannot both take the last place of `SIGNUP_MAX_PER_WINDOW_GLOBAL` (`AuthAttemptRepository.reserve`).
SIGNUP_LIMIT_ADVISORY_LOCK_KEY = 7_203_003


class TripLimitReachedError(Exception):
    """The owner already holds the maximum number of saved trips; no trip row was written."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__("The saved-trip limit for this account was reached.")


class AdmissionRefusal(str, Enum):
    USER_WINDOW = "user_window"
    GLOBAL_WINDOW = "global_window"
    CONCURRENT = "concurrent"


@dataclass(frozen=True)
class AdmissionPolicy:
    max_jobs_per_user_per_window: int
    max_jobs_global_per_window: int
    max_concurrent_jobs: int
    window_seconds: int

    def window_start(self, now: datetime) -> datetime:
        return now - timedelta(seconds=self.window_seconds)

    def refusal(
        self, *, user_jobs_in_window: int, global_jobs_in_window: int, active_jobs: int
    ) -> AdmissionRefusal | None:
        """The first limit a new job would exceed, most personal first (the caller's own allowance,
        then the deployment's allowance, then the momentary capacity)."""
        if 0 < self.max_jobs_per_user_per_window <= user_jobs_in_window:
            return AdmissionRefusal.USER_WINDOW
        if 0 < self.max_jobs_global_per_window <= global_jobs_in_window:
            return AdmissionRefusal.GLOBAL_WINDOW
        if 0 < self.max_concurrent_jobs <= active_jobs:
            return AdmissionRefusal.CONCURRENT
        return None


class AdmissionRefusedError(Exception):
    """A new job was not admitted; no job row was written. Carries only which limit applied."""

    def __init__(self, reason: AdmissionRefusal) -> None:
        self.reason = reason
        super().__init__("The generation job was not admitted.")
