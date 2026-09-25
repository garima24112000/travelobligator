from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.ai_feedback_interpretation import (
    AIFeedbackInterpretationResult,
    AIFeedbackInterpretationStatus,
)
from app.models.targeted_regeneration_runtime import TargetedRegenerationRuntimeStatus
from app.services.feedback_service import FeedbackService, pending_feedback_events
from test_targeted_regeneration_application_service import (  # type: ignore[import-not-found]
    _completed_execution,
    _completed_interpretation,
    _FakeExecutor,
    _FakeInterpreter,
    _FakePlanBuilder,
    _FakeRepository,
    _pending_event,
    _planning_state,
    _ready_removal_plan,
    _service,
)

# Section 202C.1A: pending-feedback queue semantics. The backend behaviour is
# unchanged (one pending event per regenerate call, OLDEST first); these tests
# pin it AND the published order the UI reads, so "next to apply" can never
# disagree with what Regenerate really processes.

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _rate_limited() -> AIFeedbackInterpretationResult:
    return AIFeedbackInterpretationResult(
        status=AIFeedbackInterpretationStatus.REJECTED,
        blocked_reasons=["sanitized"],
        failure_kind="rate_limited",
        confidence=0.0,
    )


def _attempt(repository: _FakeRepository, interpretation: AIFeedbackInterpretationResult):
    service = _service(
        _FakeInterpreter(interpretation),
        _FakePlanBuilder(_ready_removal_plan()),
        _FakeExecutor(builder=_completed_execution),
        repository,
    )
    result = service.regenerate("trip_x")
    return result, repository.get_by_trip_id("trip_x")


def _summary(state):
    return FeedbackService().recompute_pending_feedback_summary(state).pending_feedback_summary


def test_a_rate_limited_then_b_submitted_makes_a_next_and_b_waiting() -> None:
    state = _planning_state()
    a = _pending_event("Remove Belem Tower.")
    a.created_at = T0
    state.feedback_history = [a]
    repository = _FakeRepository(state)

    result, persisted = _attempt(repository, _rate_limited())

    assert result.status == TargetedRegenerationRuntimeStatus.RATE_LIMITED
    assert [e.feedback_event_id for e in pending_feedback_events(persisted.feedback_history)] == [a.feedback_event_id]
    assert persisted.metadata.current_version == "v1" and len(persisted.version_history) == 1  # no version on refusal
    assert persisted.feedback_history[0].applied_at is None  # no false Applied state

    b = _pending_event("Also add Sintra.")
    b.created_at = T0 + timedelta(minutes=5)
    persisted.feedback_history = [*persisted.feedback_history, b]
    summary = _summary(persisted)
    assert summary.queue_event_ids == [a.feedback_event_id, b.feedback_event_id]
    assert summary.next_feedback_event_id == a.feedback_event_id
    assert summary.total_feedback_items == 2


def test_regenerate_after_b_submitted_processes_a_first_and_b_becomes_next() -> None:
    state = _planning_state()
    a, b = _pending_event("Remove Belem Tower."), _pending_event("Also add Sintra.")
    a.created_at, b.created_at = T0, T0 + timedelta(minutes=5)
    state.feedback_history = [a, b]
    repository = _FakeRepository(state)

    _, after_failure = _attempt(repository, _rate_limited())  # a rate-limited call consumes nothing
    assert len(pending_feedback_events(after_failure.feedback_history)) == 2
    assert after_failure.metadata.current_version == "v1"

    result, after_success = _attempt(repository, _completed_interpretation())

    assert result.status == TargetedRegenerationRuntimeStatus.COMPLETED
    assert result.feedback_event_id == a.feedback_event_id  # A, not the newest instruction
    by_id = {e.feedback_event_id: e for e in after_success.feedback_history}
    assert by_id[a.feedback_event_id].applied_at is not None  # exactly one consumed
    assert by_id[b.feedback_event_id].applied_at is None
    assert after_success.metadata.current_version == "v2"
    assert [v.version_label for v in after_success.version_history].count("v2") == 1
    summary = after_success.pending_feedback_summary
    assert summary.next_feedback_event_id == b.feedback_event_id
    assert summary.queue_event_ids == [b.feedback_event_id]


def test_queue_order_is_deterministic_regardless_of_list_order_and_stable_on_ties() -> None:
    state = _planning_state()
    older, newer, tie_first, tie_second = (_pending_event(t) for t in ("old", "new", "tie1", "tie2"))
    older.created_at = T0
    tie_first.created_at = tie_second.created_at = T0 + timedelta(minutes=1)
    newer.created_at = T0 + timedelta(minutes=2)
    state.feedback_history = [newer, tie_first, tie_second, older]

    summary = _summary(state)

    assert summary.queue_event_ids == [
        older.feedback_event_id,
        tie_first.feedback_event_id,
        tie_second.feedback_event_id,
        newer.feedback_event_id,
    ]
    assert summary.next_feedback_event_id == older.feedback_event_id


def test_the_published_next_event_is_the_one_the_service_processes() -> None:
    state = _planning_state()
    older, newer = _pending_event("first"), _pending_event("second")
    older.created_at, newer.created_at = T0, T0 + timedelta(minutes=9)
    state.feedback_history = [newer, older]
    published = _summary(state).next_feedback_event_id
    repository = _FakeRepository(state)

    result, _ = _attempt(repository, _completed_interpretation())

    assert result.feedback_event_id == published == older.feedback_event_id


def test_no_pending_feedback_publishes_an_empty_queue() -> None:
    state = _planning_state()
    state.feedback_history = []
    summary = _summary(state)
    assert summary.queue_event_ids == [] and summary.next_feedback_event_id is None
