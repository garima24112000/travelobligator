from __future__ import annotations

from typing import Any

import pytest

from app.models.ai_feedback_interpretation import (
    AIFeedbackInterpretationResult,
    AIFeedbackInterpretationStatus,
)
from app.models.planning_state import PlanningState
from app.models.targeted_regeneration_execution import (
    TargetedRegenerationExecutionResult,
    TargetedRegenerationExecutionStatus,
)
from app.models.targeted_regeneration_runtime import TargetedRegenerationRuntimeStatus
from app.providers.ai_failure import (
    AIProviderFailureKind,
    classify_ai_provider_exception,
    safe_ai_failure_message,
)
from app.providers.ai_feedback_interpreter.anthropic_adapter import AnthropicAIFeedbackInterpreterProvider
from app.providers.ai_feedback_interpreter.groq_adapter import GroqAIFeedbackInterpreterProvider

# Reuse the application-service scaffolding (fake repository/interpreter/
# executor and the standard v1 trip fixture).
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

# Section 202B.1 (Tasks 3, 8, 9, 16, 17, 19, 21).


class _StatusError(Exception):
    def __init__(self, status_code: int | None = None, code: str | None = None, body: Any = None) -> None:
        super().__init__(f"raw provider text org_SECRET {body}")
        self.status_code = status_code
        self.code = code
        self.body = body


class _Timeout(Exception):
    pass


# -- taxonomy ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (_StatusError(429), AIProviderFailureKind.RATE_LIMITED),
        (_StatusError(401), AIProviderFailureKind.AUTHENTICATION),
        (_StatusError(403), AIProviderFailureKind.AUTHENTICATION),
        (_StatusError(504), AIProviderFailureKind.TIMEOUT_OR_NETWORK),
        (TimeoutError("x"), AIProviderFailureKind.TIMEOUT_OR_NETWORK),
        (_Timeout("x"), AIProviderFailureKind.TIMEOUT_OR_NETWORK),
        (_StatusError(400, code="json_validate_failed"), AIProviderFailureKind.MALFORMED_OUTPUT),
        (
            _StatusError(400, body={"error": {"code": "json_validate_failed"}}),
            AIProviderFailureKind.MALFORMED_OUTPUT,
        ),
        (_StatusError(500), AIProviderFailureKind.PROVIDER_ERROR),
        (RuntimeError("anything"), AIProviderFailureKind.PROVIDER_ERROR),
    ],
)
def test_classification_uses_structured_attributes(exc: BaseException, kind: AIProviderFailureKind) -> None:
    assert classify_ai_provider_exception(exc) == kind


def test_classification_never_parses_message_text() -> None:
    # A message that MENTIONS a rate limit but carries no structured signal.
    assert classify_ai_provider_exception(RuntimeError("Error code: 429 rate limit")) == (
        AIProviderFailureKind.PROVIDER_ERROR
    )


def test_safe_message_contains_no_raw_exception_text() -> None:
    for kind in AIProviderFailureKind:
        message = safe_ai_failure_message("Groq", kind)
        assert "org_" not in message and "raw provider" not in message


class _RaisingClient:
    """Raises from both the Groq (`invoke`) and Anthropic
    (`messages.create`) call shapes."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.messages = self

    def invoke(self, prompt: Any) -> Any:
        raise self._exc

    def create(self, **kwargs: Any) -> Any:
        raise self._exc


@pytest.mark.parametrize("provider_cls", [GroqAIFeedbackInterpreterProvider, AnthropicAIFeedbackInterpreterProvider])
def test_interpreter_adapters_report_rate_limit_structurally_and_sanitized(provider_cls: Any) -> None:
    from app.services.ai_feedback_interpretation_request_builder import AIFeedbackInterpretationRequestBuilder

    provider = provider_cls(client=_RaisingClient(_StatusError(429, body={"leak": "org_SECRET"})), api_key="k")
    state = _planning_state()
    request = AIFeedbackInterpretationRequestBuilder().build_request(state, "Remove Museum A")

    result = provider.interpret(request)

    assert result.status == AIFeedbackInterpretationStatus.REJECTED
    assert result.failure_kind == "rate_limited"
    joined = " ".join(result.blocked_reasons)
    assert "org_SECRET" not in joined and "raw provider" not in joined
    assert "rate-limited" in joined


# -- targeted outcomes never mutate / consume / version --------------------------


def _rejected(kind: str | None) -> AIFeedbackInterpretationResult:
    return AIFeedbackInterpretationResult(
        status=AIFeedbackInterpretationStatus.REJECTED,
        blocked_reasons=["sanitized"],
        failure_kind=kind,
        confidence=0.0,
    )


def _run_with_interpretation(interpretation: AIFeedbackInterpretationResult) -> tuple[Any, PlanningState]:
    state = _planning_state()
    state.feedback_history = [_pending_event()]
    repository = _FakeRepository(state)
    service = _service(
        _FakeInterpreter(interpretation),
        _FakePlanBuilder(_ready_removal_plan()),
        _FakeExecutor(builder=_completed_execution),
        repository,
    )
    result = service.regenerate("trip_x")
    persisted = repository.get_by_trip_id("trip_x")
    assert persisted is not None
    return result, persisted


def test_rate_limit_is_its_own_status_and_mutates_nothing() -> None:
    result, persisted = _run_with_interpretation(_rejected("rate_limited"))

    assert result.status == TargetedRegenerationRuntimeStatus.RATE_LIMITED
    assert result.provider_failure_kind == "rate_limited"
    assert "rate-limited" in result.message
    assert "not been implemented" not in result.message
    assert persisted.metadata.current_version == "v1"
    assert len(persisted.version_history) == 1
    assert persisted.feedback_history[0].applied_at is None
    assert persisted.experience_plan.daily_plans[1].experiences  # day 2 untouched
    assert persisted.regeneration_attempts[-1].reason_code == "REGENERATION_PROVIDER_RATE_LIMITED"
    assert persisted.regeneration_attempts[-1].status == "blocked"


@pytest.mark.parametrize("kind", ["authentication", "timeout_or_network", "provider_error", "malformed_output"])
def test_other_provider_failures_are_provider_unavailable_with_their_own_copy(kind: str) -> None:
    result, persisted = _run_with_interpretation(_rejected(kind))

    assert result.status == TargetedRegenerationRuntimeStatus.PROVIDER_UNAVAILABLE
    assert result.provider_failure_kind == kind
    assert persisted.regeneration_attempts[-1].reason_code == "REGENERATION_AI_UNAVAILABLE"
    assert persisted.feedback_history[0].applied_at is None
    assert persisted.metadata.current_version == "v1"


def test_not_connected_reports_not_connected_copy() -> None:
    result, persisted = _run_with_interpretation(
        AIFeedbackInterpretationResult(
            status=AIFeedbackInterpretationStatus.NOT_CONNECTED, blocked_reasons=["off"], confidence=0.0
        )
    )
    assert result.status == TargetedRegenerationRuntimeStatus.PROVIDER_UNAVAILABLE
    assert result.provider_failure_kind == "not_connected"
    assert "not currently connected" in result.message
    assert persisted.feedback_history[0].applied_at is None


# -- truthful preservation + no-op (Tasks 3, 8, 9) --------------------------------


def _executor_result(state: PlanningState, working: PlanningState, *, affected: list[int], preserved: list[int]) -> Any:
    return TargetedRegenerationExecutionResult(
        trip_id="trip_x",
        status=TargetedRegenerationExecutionStatus.COMPLETED,
        source_version=state.metadata.current_version,
        resulting_planning_state=working,
        affected_day_indices=affected,
        preserved_day_indices=preserved,
        provider_lookup_status="not_required",
        reasoning_status="not_required",
        routing_rerun=True,
        validation_status="ready",
        preservation_audit_passed=True,
    )


def test_reported_preserved_days_come_from_the_final_state_not_the_plan() -> None:
    state = _planning_state()
    state.feedback_history = [_pending_event()]
    repository = _FakeRepository(state)

    def _builder(planning_state: PlanningState) -> Any:
        working = planning_state.model_copy(deep=True)
        # The PLAN (below) says only day 2 is affected and 1,3 are
        # preserved -- but a downstream step also changed day 3.
        working.experience_plan.daily_plans[1].experiences = []
        working.experience_plan.daily_plans[2].warnings.append("changed downstream")
        return _executor_result(planning_state, working, affected=[2], preserved=[1, 3])

    result = _service(
        _FakeInterpreter(_completed_interpretation()),
        _FakePlanBuilder(_ready_removal_plan()),
        _FakeExecutor(builder=_builder),
        repository,
    ).regenerate("trip_x")

    assert result.status == TargetedRegenerationRuntimeStatus.COMPLETED
    assert result.diff.affected_day_indices == [2, 3]
    assert result.diff.preserved_day_indices == [1]
    persisted = repository.get_by_trip_id("trip_x")
    assert "day_1" in persisted.version_history[-1].preserved_sections
    assert "day_3" not in persisted.version_history[-1].preserved_sections


def test_result_identical_to_source_is_a_no_effect_and_never_a_version() -> None:
    state = _planning_state()
    state.feedback_history = [_pending_event()]
    repository = _FakeRepository(state)

    def _noop(planning_state: PlanningState) -> Any:
        return _executor_result(planning_state, planning_state.model_copy(deep=True), affected=[2], preserved=[1, 3])

    result = _service(
        _FakeInterpreter(_completed_interpretation()),
        _FakePlanBuilder(_ready_removal_plan()),
        _FakeExecutor(builder=_noop),
        repository,
    ).regenerate("trip_x")

    assert result.status == TargetedRegenerationRuntimeStatus.NO_EFFECT
    persisted = repository.get_by_trip_id("trip_x")
    assert persisted.metadata.current_version == "v1"
    assert len(persisted.version_history) == 1
    assert persisted.feedback_history[0].applied_at is None
    assert persisted.feedback_history[0].applied_in_version is None
    assert persisted.regeneration_attempts[-1].reason_code == "REGENERATION_NO_EFFECT"
    assert persisted.regeneration_attempts[-1].status == "blocked"
