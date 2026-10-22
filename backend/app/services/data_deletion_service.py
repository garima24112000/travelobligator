"""Deleting a trip or an account (P1-A).

What a trip is, in storage -- every one of these is removed, explicitly, never left to a cascade rule:

  trips                one row (ownership)
  planning_states      the whole plan as one document: request, itinerary, feedback text, locks,
                       version history, validation and provider reports
  generation_jobs      job control rows of the trip
  itinerary_branches   branches of the trip
  itinerary_revisions  revision snapshots of the trip (each a full copy of the plan)

An account additionally owns its `users` row and, in `auth_attempts`, the failed sign-in counters keyed
by a hash of its email. Signup counters carry no reference to any account and are left alone.

Why not rely on ON DELETE: `trips.owner_id` is ON DELETE SET NULL, so deleting only the user would keep
every trip, ownerless. The other four tables do cascade from `trips`, but this module deletes them itself
so the result does not depend on a rule someone may change, and so it can report what it removed.

Atomicity (PostgreSQL): one unit of work. Any failure rolls everything back -- a half-deleted trip or
account cannot exist. Local JSON (explicit development backend) is not transactional, like the rest of
that backend; it applies the same steps in the same order.

Generation in progress: a trip (or an account with a trip) that has a queued or running job is NOT
deleted (`GenerationInProgressError`). No job is cancelled here: a running job is a thread that cannot be
stopped safely, and it would go on writing to rows that no longer exist. The check and the delete cannot
be separated by a new job: the transaction first takes the advisory lock every job admission takes
(`repositories.admission`), so no job can be created for the trip while it runs. Jobs whose owner is
already gone (expired lease, never claimed) are closed first, so an abandoned job does not make a trip
undeletable.

Generation limits: a deleted job that is still inside the limit window leaves a `deleted_job_usage` row
(its admission time; its owner while the account exists, nobody once the account is deleted). Deleting
therefore frees a place in the SAVED-TRIP limit, and never hands back generation allowance.

No provider, model or Redis call happens anywhere in this module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Text, cast, delete, func, insert, literal, null, or_, select, update

from app.core.config import get_settings
from app.repositories.admission import ADMISSION_ADVISORY_LOCK_KEY
from app.repositories.auth_attempt_repository import LOGIN_FAILURE
from app.repositories.errors import GenerationInProgressError, NothingToDeleteError
from app.repositories.factory import (
    get_auth_attempt_repository,
    get_job_repository,
    get_lineage_repository,
    get_planning_state_repository,
    get_trip_repository,
    get_user_repository,
)
from app.repositories.unit_of_work import run_atomic

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = ("queued", "running")


@dataclass(frozen=True)
class DeletionSummary:
    trips: int = 0
    planning_states: int = 0
    jobs: int = 0
    branches: int = 0
    revisions: int = 0
    account: bool = False


def _usage_since() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=get_settings().generation_limit_window_seconds)


def _close_abandoned_jobs(trip_id: str | None) -> None:
    # Imported here: the job service imports the planning stack, which this module never needs.
    from app.services.generation_job_service import _reconcile_stale_jobs

    _reconcile_stale_jobs(trip_id)


def delete_trip(trip_id: str, *, owner_id: str) -> DeletionSummary:
    """Delete one trip of `owner_id` and everything stored for it. Raises `NothingToDeleteError` when
    that owner has no such trip, `GenerationInProgressError` when a job is queued or running."""
    _close_abandoned_jobs(trip_id)
    if get_settings().persistence_backend == "postgres":
        summary = run_atomic(
            lambda uow: _delete_postgres(uow.session, owner_id=owner_id, trip_id=trip_id, email_digest=None)
        )
    else:
        summary = _delete_local(owner_id=owner_id, trip_id=trip_id, email_digest=None)
    logger.info(
        "Trip deleted.",
        extra={"trip_id": trip_id, "user_id": owner_id, "status": "deleted"},
    )
    return summary


def delete_account(user_id: str, *, email_digest: str) -> DeletionSummary:
    """Delete the account, every trip it owns with everything stored for them, and its failed sign-in
    counters (`email_digest` is the keyed hash they are stored under). Raises
    `GenerationInProgressError` when any of its trips has a queued or running job."""
    _close_abandoned_jobs(None)
    if get_settings().persistence_backend == "postgres":
        summary = run_atomic(
            lambda uow: _delete_postgres(uow.session, owner_id=user_id, trip_id=None, email_digest=email_digest)
        )
    else:
        summary = _delete_local(owner_id=user_id, trip_id=None, email_digest=email_digest)
    logger.info(
        "Account deleted.",
        extra={"auth_event": "account_delete", "status": "succeeded", "user_id": user_id},
    )
    return summary


# -- PostgreSQL: one transaction -------------------------------------------------------------------


def _delete_postgres(session, *, owner_id: str, trip_id: str | None, email_digest: str | None) -> DeletionSummary:
    """`trip_id` given: that one trip. `trip_id` None: the whole account. Runs inside the caller's
    unit of work and never commits; any exception rolls the whole deletion back."""
    from app.db.models import (
        AuthAttemptRow,
        DeletedJobUsageRow,
        GenerationJobRow,
        ItineraryBranchRow,
        ItineraryRevisionRow,
        PlanningStateRow,
        TripRow,
        UserRow,
    )

    whole_account = trip_id is None
    # The lock every job admission takes: no job can be created while this transaction is open.
    session.execute(select(func.pg_advisory_xact_lock(ADMISSION_ADVISORY_LOCK_KEY)))

    owned = select(TripRow.trip_id).where(TripRow.owner_id == owner_id)
    if not whole_account:
        owned = owned.where(TripRow.trip_id == trip_id)
    trip_ids = list(session.execute(owned.with_for_update()).scalars())
    if not whole_account and not trip_ids:
        raise NothingToDeleteError()

    jobs_in_scope = GenerationJobRow.trip_id.in_(trip_ids)
    if whole_account:
        jobs_in_scope = or_(jobs_in_scope, GenerationJobRow.owner_id == owner_id)
    active = session.execute(
        select(func.count())
        .select_from(GenerationJobRow)
        .where(jobs_in_scope, GenerationJobRow.status.in_(_ACTIVE_STATUSES))
    ).scalar_one()
    if active:
        raise GenerationInProgressError()

    # What the generation limits still need from the jobs about to go: when, and (while the account
    # exists) whose. For a deleted account the rows refer to nobody.
    since = _usage_since()
    session.execute(
        insert(DeletedJobUsageRow).from_select(
            ["usage_id", "owner_id", "created_at"],
            select(
                literal("usage_") + func.md5(GenerationJobRow.job_id),
                cast(null(), Text) if whole_account else GenerationJobRow.owner_id,
                GenerationJobRow.created_at,
            ).where(jobs_in_scope, GenerationJobRow.created_at > since),
        )
    )
    if whole_account:
        session.execute(
            update(DeletedJobUsageRow).where(DeletedJobUsageRow.owner_id == owner_id).values(owner_id=None)
        )
    session.execute(delete(DeletedJobUsageRow).where(DeletedJobUsageRow.created_at <= since))

    # Dependents first, then the trips. Explicit on purpose (see the module docstring).
    revisions = session.execute(
        delete(ItineraryRevisionRow).where(ItineraryRevisionRow.trip_id.in_(trip_ids))
    ).rowcount
    branches = session.execute(
        delete(ItineraryBranchRow).where(ItineraryBranchRow.trip_id.in_(trip_ids))
    ).rowcount
    jobs = session.execute(delete(GenerationJobRow).where(jobs_in_scope)).rowcount
    states = session.execute(
        delete(PlanningStateRow).where(PlanningStateRow.trip_id.in_(trip_ids))
    ).rowcount
    trips = session.execute(delete(TripRow).where(TripRow.trip_id.in_(trip_ids))).rowcount

    if whole_account:
        session.execute(
            delete(AuthAttemptRow).where(
                AuthAttemptRow.kind == LOGIN_FAILURE, AuthAttemptRow.subject_hash == email_digest
            )
        )
        if session.execute(delete(UserRow).where(UserRow.user_id == owner_id)).rowcount != 1:
            raise NothingToDeleteError()
        # Nothing owned by the account may be left -- if anything is, the whole deletion rolls back.
        leftover = session.execute(
            select(func.count()).select_from(TripRow).where(TripRow.owner_id == owner_id)
        ).scalar_one()
        if leftover:
            raise RuntimeError("Account deletion left owned trips behind.")

    return DeletionSummary(
        trips=trips,
        planning_states=states,
        jobs=jobs,
        branches=branches,
        revisions=revisions,
        account=whole_account,
    )


# -- Local JSON (development backend): same steps, same order, not transactional ------------------------


def _delete_local(*, owner_id: str, trip_id: str | None, email_digest: str | None) -> DeletionSummary:
    whole_account = trip_id is None
    trips_repository = get_trip_repository()
    jobs_repository = get_job_repository()

    owned = {record.trip_id for record in trips_repository.list_by_owner_id(owner_id)}
    trip_ids = owned if whole_account else owned & {trip_id}
    if not whole_account and not trip_ids:
        raise NothingToDeleteError()
    if whole_account and get_user_repository().get_by_user_id(owner_id) is None:
        raise NothingToDeleteError()

    if any(jobs_repository.list_running_by_trip_id(candidate) for candidate in trip_ids):
        raise GenerationInProgressError()

    branches, revisions = get_lineage_repository().delete_for_trips(trip_ids)
    jobs = jobs_repository.delete_for_trips(trip_ids, usage_since=_usage_since(), keep_owner=not whole_account)
    states = get_planning_state_repository().delete_for_trips(trip_ids)
    trips = trips_repository.delete_many(trip_ids)

    if whole_account:
        jobs_repository.anonymize_deleted_usage(owner_id)
        get_auth_attempt_repository().clear(LOGIN_FAILURE, subject_hash=email_digest or "")
        get_user_repository().delete_user(owner_id)

    return DeletionSummary(
        trips=trips,
        planning_states=states,
        jobs=jobs,
        branches=branches,
        revisions=revisions,
        account=whole_account,
    )
