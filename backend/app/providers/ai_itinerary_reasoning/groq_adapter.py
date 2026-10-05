from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.providers.ai_failure import AIProviderFailureKind, classify_and_message
from app.providers.ai_stage_budget import StageRun
from app.core import performance
from app.core.config import get_settings
from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningRequest,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    ItineraryReasoningCandidatePlacement,
    ItineraryReasoningDayPlan,
    ItineraryReasoningStrategy,
    ItineraryReasoningTimeWindow,
    validate_result_against_request,
)
from app.models.ai_itinerary_repair import (
    AIItineraryRepairRequest,
    AIItineraryRepairResult,
    AIItineraryRepairStatus,
    RepairableIssueType,
    validate_repair_result_against_request,
)
from app.providers.ai_itinerary_reasoning.base import AIItineraryReasoningProvider
from app.providers.llm_provider_health import GEMINI, GROQ
from app.providers.llm_provider_router import (
    PROVIDER_LABELS,
    StageRoute,
    after_structural_failure,
    after_transport_failure,
    answering_provider,
    close_route,
    gemini_wording,
    not_configured_message,
    provider_call,
    resolve_chain,
    start_route,
    unavailable_failure,
)
from app.providers.llm_structured_clients import GeminiStructuredClient, open_groq_quota_capture
from app.providers.ai_itinerary_reasoning.candidate_refs import (
    CandidateRefMap,
    UnknownCandidateReference,
    resolve_day_refs,
    scrub_output_prose,
)

# Groq-backed itinerary-reasoning adapter (Section 193B,
# docs/14_backend_architecture.md section 142). Reuses the exact
# post-191A.1 Structured Outputs pattern
# `app.providers.ai_candidate_proposal.groq_adapter.GroqAICandidateProposalProvider`
# already established -- `with_structured_output(..., method="json_schema",
# strict=True)`, never `method="function_calling"` (forced tool use), and
# never `tools`/`tool_choice` sent alongside it. Deliberately NOT modeled
# on `app.providers.itinerary_narrator.groq_adapter` -- that adapter still
# predates the Section 191A.1 fix and still uses the default (buggy)
# `function_calling` method; it is a stale pattern for this purpose, not a
# convention to copy forward.
#
# This adapter still only ever produces `AIItineraryReasoningResult`
# objects, and `request.allowed_candidates` is the only real fact source
# -- every `candidate_id` a `completed` result references is checked
# against it via `validate_result_against_request` (Section 193A) before
# this adapter ever reports `completed`; a hallucinated/duplicate/out-of-
# range candidate reference downgrades the result to `rejected` instead,
# never silently dropped while still reporting success.
#
# The `langchain_groq` package import is deliberately deferred into
# `_build_client` (not a module-level import), matching every other Groq
# adapter in this repo, so the rest of the app imports cleanly whether or
# not the package is installed, and so tests never need it installed
# either. A missing `GROQ_API_KEY` (or the `langchain_groq` package) never
# crashes the app: `reason` returns an honest `not_connected` result
# instead.


class _GroqCandidatePlacementSchema(BaseModel):
    """Maps 1:1 to `ItineraryReasoningCandidatePlacement`. No coordinate,
    price, rating, opening-hours, or exact-clock-time field exists here --
    only a `candidate_id` (must already be in `allowed_candidates`) and a
    coarse time-of-day bucket.
    """

    candidate_id: str = Field(
        description="Must be one of the candidate_id values from allowed_candidates. "
        "Never a new/invented id."
    )
    time_window: ItineraryReasoningTimeWindow = Field(
        description="A coarse time-of-day bucket -- never an exact clock time."
    )


class _GroqDayPlanSchema(BaseModel):
    """Maps 1:1 to `ItineraryReasoningDayPlan`. Step 191A.1 lesson: no
    `min_length`/`ge` constraint here (Groq's `json_schema` strict mode
    does not reliably support them) -- `ItineraryReasoningDayPlan` itself
    still enforces `day_index >= 1`/`candidate_ids` non-empty/unique one
    layer down, in `_build_result_from_output` below.
    """

    day_index: int = Field(description="1-based day number within the trip.")
    candidate_ids: list[str] = Field(
        description="candidate_id values (from allowed_candidates only) scheduled on this "
        "day. Never invent a new id, and never repeat a candidate_id already used on "
        "another day."
    )
    rationale: str = Field(
        description="One or two sentences explaining this day's grouping -- no price, "
        "rating, hours, or exact route-time claims."
    )
    tradeoffs: str | None = Field(
        description="Optional tradeoff note for this day. Always include this key; use "
        "null if there is none."
    )
    approximate_structure: list[_GroqCandidatePlacementSchema] = Field(
        description="Optional coarse time-of-day placement for candidates on this day. "
        "Always include this key; use an empty list if unsure."
    )


class _GroqStrategySchema(BaseModel):
    summary: str = Field(description="One or two sentences summarizing the overall plan.")
    pace: str = Field(description="One of: relaxed, balanced, packed.")
    reason: str = Field(description="Why this pace/strategy fits the traveler.")


class _GroqItineraryReasoningSchema(BaseModel):
    """Structured-output schema for the full response. Built for Groq's
    `json_schema` strict-output mode from the start (Section 193B), so
    unlike the AI-candidate-proposal schema's history this never went
    through a `function_calling`-mode phase.
    """

    strategy: _GroqStrategySchema
    days: list[_GroqDayPlanSchema] = Field(
        description="One entry per day that has scheduled candidates. Use an empty list "
        "if none apply."
    )
    overall_tradeoffs: list[str] = Field(
        description="Always include this key. Use an empty list if there are none."
    )
    confidence: float = Field(description="Your confidence in this plan, from 0.0 (low) to 1.0 (high).")


_SYSTEM_PROMPT = (
    "You are selecting and organizing only the provider-backed candidates supplied in "
    "allowed_candidates. You are not a source of travel facts -- every candidate you "
    "reference was already verified by a real provider before you saw it.\n\n"
    "Use candidate_id exactly as provided in allowed_candidates. Do not invent new "
    "candidate IDs or new places, and never reference a place by name only.\n\n"
    "Do not infer missing provider facts. Do not claim exact route times, prices, "
    "ratings, availability, opening hours, or booking information unless explicitly "
    "supplied to you (it will not be).\n\n"
    "Use coarse time windows only (morning/midday/afternoon/evening) -- never an exact "
    "clock time or a specific transfer duration.\n\n"
    "Optimize for the traveler's stated interests, pace, trip duration, and constraints, "
    "using each candidate's quality_score/quality_tier as a pre-ranking signal. "
    "When several requested interests have candidates whose matched_interests include them, represent each such interest at least once across the trip, without breaking geography or pace. normalized_category and matched_interests are provider-derived facts -- never assign or infer them yourself.\n\n"
    "A candidate should appear on at most one day. Return only the structured reasoning "
    "result matching the required schema exactly -- every schema key is required, so use "
    "null or an empty list instead of omitting a key or guessing a value."
)


def _format_candidate_line(candidate: Any, ref: str) -> str:
    # Section 202C.1C: the model sees only the short per-request reference
    # (`candidate_refs.py`), never the provider-derived identity.
    line = (
        f"- candidate_id={ref!r} name={candidate.name!r} "
        f"category={candidate.category.value} quality_tier={candidate.quality_tier} "
        f"quality_score={candidate.quality_score:.2f}"
    )
    # Section 202B.2 (Task 11): deterministic, provider-derived evidence only.
    normalized_category = getattr(candidate, "normalized_category", None)
    if normalized_category:
        line += f" normalized_category={normalized_category}"
    matched = getattr(candidate, "matched_interests", None)
    if matched:
        line += f" matched_interests={list(matched)}"
    return line


def _build_prompt(request: AIItineraryReasoningRequest, ref_map: CandidateRefMap | None = None) -> str:
    """Builds a minimal, controlled prompt from `request` fields only --
    never a raw `PlanningState` dump, and never a coordinate (193A's own
    design intent: the reasoning model is not asked to reason about
    distances/travel times, that stays deterministic routing's job).
    """
    traveler = request.traveler_context
    if ref_map is None:
        ref_map = CandidateRefMap.for_candidates(request.allowed_candidates)
    lines = [
        _SYSTEM_PROMPT,
        "",
        *[f"Instruction: {instruction}" for instruction in request.reasoning_instructions],
        "",
        f"Destination: {request.destination_name}",
        f"Trip dates: {request.start_date} to {request.end_date} "
        f"({request.trip_duration_days} day(s))",
        f"Travelers: {traveler.travelers_count} ({traveler.travel_group_type})",
        f"Pace: {traveler.pace}",
        f"Interests: {', '.join(traveler.interests) if traveler.interests else 'none specified'}",
        f"Must-visit: {', '.join(traveler.must_visit) if traveler.must_visit else 'none specified'}",
        f"Must-avoid: {', '.join(traveler.must_avoid) if traveler.must_avoid else 'none specified'}",
        f"Constraints: {', '.join(traveler.constraints) if traveler.constraints else 'none specified'}",
    ]
    if request.trip_strategy_summary is not None:
        strategy = request.trip_strategy_summary
        lines.append(
            "Existing trip strategy: "
            f"style={strategy.recommended_trip_style or 'unspecified'}, "
            f"planning_strategy={strategy.planning_strategy}, tradeoffs={strategy.tradeoffs}"
        )
    lines.append(f"Factual context: {request.factual_context.model_dump()}")
    lines.append(f"Allowed candidates (use only these candidate_id values, {len(request.allowed_candidates)} total):")
    lines.extend(
        _format_candidate_line(candidate, ref_map.ref_for(candidate.candidate_id))
        for candidate in request.allowed_candidates
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section 194A: repair schema/prompt. Same Structured Outputs pattern as
# the `reason` schema above -- `json_schema`/`strict=True`, no
# `min_length`/`ge` constraints (domain models re-validate one layer
# down). The wire schema is deliberately narrower than
# `_GroqItineraryReasoningSchema`: only the repaired day(s), never a full
# day list, mirroring `AIItineraryRepairResult.repaired_days`.
# ---------------------------------------------------------------------------


class _GroqRepairedDaySchema(BaseModel):
    day_index: int = Field(
        description="Must be one of the day_index values in affected_days. Never a day "
        "outside affected_days."
    )
    candidate_ids: list[str] = Field(
        description="candidate_id values (from allowed_candidates only) for this repaired "
        "day. Never invent a new id."
    )
    rationale: str = Field(
        description="One or two sentences explaining this day's revised grouping -- no "
        "price, rating, hours, or exact route-time claims."
    )
    tradeoffs: str | None = Field(
        description="Optional tradeoff note for this day. Always include this key; use "
        "null if there is none."
    )
    approximate_structure: list[_GroqCandidatePlacementSchema] = Field(
        description="Optional coarse time-of-day placement for candidates on this day. "
        "Always include this key; use an empty list if unsure."
    )


class _GroqRepairSchema(BaseModel):
    repaired_days: list[_GroqRepairedDaySchema] = Field(
        description="Only the day(s) you actually changed -- never restate an unaffected day."
    )
    repair_summary: str = Field(
        description="One or two sentences summarizing what changed and why."
    )
    addressed_issue_types: list[str] = Field(
        description="Which issue_type value(s) from the supplied issues this repair "
        "addresses. Always include this key; use an empty list if unsure."
    )
    confidence: float = Field(description="Your confidence in this repair, from 0.0 (low) to 1.0 (high).")


_REPAIR_SYSTEM_PROMPT = (
    "You are repairing an existing itinerary in response to deterministic validation "
    "findings. The validator is authoritative about what is wrong -- you are not being "
    "asked to judge feasibility yourself, only to choose a better arrangement of the "
    "already-verified candidates supplied in allowed_candidates.\n\n"
    "Use candidate_id exactly as provided in allowed_candidates. Do not invent new "
    "candidate IDs or new places, and never reference a place by name only.\n\n"
    "Modify only the day_index values listed in affected_days. Do not restate or alter "
    "any day not in affected_days.\n\n"
    "Do not infer missing provider facts. Do not claim exact route times, prices, "
    "ratings, availability, opening hours, or booking information.\n\n"
    "Prefer the smallest change that resolves the supplied issues. Return only the "
    "structured repair result matching the required schema exactly -- every schema key is "
    "required, so use null or an empty list instead of omitting a key or guessing a value."
)


def _format_issue_line(issue: Any) -> str:
    return (
        f"- day {issue.day_index}: issue_type={issue.issue_type.value!r} "
        f"severity={issue.severity.value} message={issue.message!r}"
    )


def _build_repair_prompt(request: AIItineraryRepairRequest, ref_map: CandidateRefMap | None = None) -> str:
    """Builds a minimal, controlled repair prompt from `request` fields
    only -- never a raw `PlanningState` dump, and never a coordinate."""
    traveler = request.traveler_context
    if ref_map is None:
        ref_map = CandidateRefMap.for_candidates(request.allowed_candidates)
    lines = [
        _REPAIR_SYSTEM_PROMPT,
        "",
        *[f"Instruction: {instruction}" for instruction in request.repair_instructions],
        "",
        f"Destination: {request.destination_name}",
        f"Trip dates: {request.start_date} to {request.end_date} "
        f"({request.trip_duration_days} day(s))",
        f"Travelers: {traveler.travelers_count} ({traveler.travel_group_type})",
        f"Pace: {traveler.pace}",
        f"Repair attempt number: {request.attempt_number}",
        f"Affected days (you may only change these): {request.affected_days}",
        "Validator findings to address:",
    ]
    lines.extend(_format_issue_line(issue) for issue in request.issues)
    lines.append("Original day plans (unaffected days must come back unchanged, i.e. omitted):")
    for day in request.original_days:
        lines.append(f"- day {day.day_index}: candidate_ids={ref_map.refs_for(day.candidate_ids)}")
    lines.append(
        f"Allowed candidates (use only these candidate_id values, "
        f"{len(request.allowed_candidates)} total, same universe as the original plan):"
    )
    lines.extend(
        _format_candidate_line(candidate, ref_map.ref_for(candidate.candidate_id))
        for candidate in request.allowed_candidates
    )
    return "\n".join(lines)


class GroqAIItineraryReasoningProvider(AIItineraryReasoningProvider):
    """`AIItineraryReasoningProvider` implementation backed by Groq (via
    `langchain_groq.ChatGroq`), used only when explicitly config-gated in
    via `get_ai_itinerary_reasoning_provider` -- never the default.

    `reason` never invents a candidate_id, place, coordinate, price,
    rating, opening hour, availability claim, booking link, or route
    time/distance: the structured-output schema Groq must respond through
    has no such fields, every parsed strategy/day is still validated
    through `ItineraryReasoningStrategy`/`ItineraryReasoningDayPlan`
    before it can appear in a `completed` result, and every candidate_id
    referenced is checked against `request.allowed_candidates` via
    `validate_result_against_request` before `completed` is ever
    reported. If validation fails, the call raises, or no usable
    structured output comes back, this returns an honest
    `rejected`/`not_connected` result -- never a fabricated one.
    """

    provider_name = "groq_ai_itinerary_reasoning_provider"
    # The member of the Groq <-> Gemini pair this adapter stands for when
    # failover is off (`app/providers/llm_provider_router.py`).
    _selected_provider = GROQ

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
        max_tokens: int = 6000,
        temperature: float = 0.2,
    ) -> None:
        settings = get_settings()

        self._client = client
        if api_key is None and client is None:
            self._api_key = settings.groq_api_key
        else:
            self._api_key = api_key
        self._model = (
            model if model is not None else (settings.ai_itinerary_reasoning_model or settings.groq_model)
        )
        self._max_tokens = max_tokens
        self._temperature = temperature

    def _start_route(self, run: StageRun) -> StageRoute | None:
        """The stage's provider route. None with an injected client: that
        client is the only thing called and nothing is selected."""
        if self._client is not None:
            return None
        return start_route(run, self._selected_provider, get_settings(), groq_api_key=self._api_key or "")

    def _has_no_provider(self) -> bool:
        return self._client is None and not resolve_chain(
            self._selected_provider, get_settings(), groq_api_key=self._api_key or ""
        )

    def reason(self, request: AIItineraryReasoningRequest) -> AIItineraryReasoningResult:
        if self._has_no_provider():
            return self._attributed(
                self._not_connected_result(request, not_configured_message(self._selected_provider)), None
            )

        # Section 1C: one total wall-clock budget for the stage and at most
        # one recovery attempt (a transport retry, or the other provider of
        # the Groq <-> Gemini pair). Any non-completed result leaves the
        # deterministic planner to schedule the days, as before.
        settings = get_settings()
        run = StageRun(
            "groq_reasoning",
            total_budget_seconds=settings.groq_reasoning_total_budget_seconds,
            request_timeout_seconds=settings.groq_request_timeout_seconds,
            transport_retries=settings.groq_max_retries,
        )
        route = self._start_route(run)
        result = self._reason_within(request, run, route)
        close_route(run, route, completed=result.status == AIItineraryReasoningStatus.COMPLETED)
        return self._attributed(result, route)

    def _attributed(self, result: Any, route: StageRoute | None) -> Any:
        """Names the provider that actually answered. Provider identity is
        metadata only: the result went through the same checks either way."""
        if answering_provider(route, self._selected_provider) != GEMINI:
            if self._selected_provider == GROQ:
                return result
            return result.model_copy(update={"provider_name": GroqAIItineraryReasoningProvider.provider_name})
        report = result.guardrail_report
        return result.model_copy(
            update={
                "provider_name": "gemini_ai_itinerary_reasoning_provider",
                "model_name": get_settings().gemini_model,
                "guardrail_report": report.model_copy(
                    update={"blocked_reasons": [gemini_wording(reason) for reason in report.blocked_reasons]}
                ),
            }
        )

    def _build_gemini_client(self, schema: type[BaseModel], timeout: float) -> Any:
        """The same stage on Gemini: same prompt, same wire schema, one
        request, no tools (`app/providers/llm_structured_clients.py`)."""
        settings = get_settings()
        return GeminiStructuredClient(
            schema,
            api_key=settings.gemini_api_key or "",
            model=settings.gemini_model or "",
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            timeout=timeout,
        )

    def _invoke_within(
        self,
        run: StageRun,
        build_client: Any,
        prompt: str,
        timing_key: str,
        route: StageRoute | None = None,
        schema: type[BaseModel] | None = None,
    ) -> tuple[Any, tuple[str, str] | None, str | None]:
        """One model answer within the stage budget:
        `(raw output, None, None)`, or `(None, (failure kind, safe message), None)`
        for a failed / out-of-time call, or `(None, None, reason)` when no
        client could be built (not connected). The stage makes at most ONE
        recovery request, while the budget allows it: the same provider again
        after a transient transport failure, or -- when the stage is routed
        over the Groq <-> Gemini pair -- the other provider after a transport
        failure or a malformed / schema-invalid answer. There is no
        same-provider retry for a malformed answer."""
        provider = route.first_provider() if route is not None else GROQ
        if provider is None:
            assert route is not None
            kind, message = unavailable_failure(route)
            return None, (kind.value, message), None
        while True:
            label = PROVIDER_LABELS[provider]
            timeout = run.next_attempt_timeout()
            if timeout is None:
                return None, (AIProviderFailureKind.DEADLINE_EXCEEDED.value, run.deadline_message(label)), None
            if self._client is not None:
                client = self._client
            else:
                try:
                    client = (
                        build_client(timeout=timeout)
                        if provider == GROQ
                        else self._build_gemini_client(schema or _GroqItineraryReasoningSchema, timeout)
                    )
                except Exception as exc:  # missing package / bad config -> not_connected
                    run.attempts -= 1  # no request was made
                    if run.attempts == 0:
                        return None, None, f"{label} client could not be initialized: {exc}"
                    return None, (AIProviderFailureKind.NOT_CONNECTED.value, f"{label} client could not be initialized."), None
            if route is not None:
                route.begin_attempt(provider)
            try:
                with provider_call(timing_key, provider):
                    raw_output = client.invoke(prompt)
            except Exception as exc:  # API/runtime failure -> rejected, never fabricated
                kind, message = classify_and_message(label, exc)
                if kind == AIProviderFailureKind.MALFORMED_OUTPUT:
                    # The provider answered; the answer is not usable.
                    run.answered = True
                    if route is not None:
                        route.record_exception(provider, exc, client=client)
                    next_provider = after_structural_failure(run, route, provider, same_provider_retry=False)
                else:
                    next_provider = after_transport_failure(run, route, provider, exc)
                if next_provider is not None:
                    provider = next_provider
                    continue
                if run.deadline_exceeded:
                    return (
                        None,
                        (AIProviderFailureKind.DEADLINE_EXCEEDED.value, run.deadline_message(label)),
                        None,
                    )
                return None, (kind.value, message), None
            run.answered = True
            structured = self._coerce_output(raw_output) is not None
            if route is not None:
                route.record_answer(provider, client, structured=structured)
            if not structured:
                next_provider = after_structural_failure(run, route, provider, same_provider_retry=False)
                if next_provider is not None:
                    provider = next_provider
                    continue
                if run.deadline_exceeded:
                    return (
                        None,
                        (AIProviderFailureKind.DEADLINE_EXCEEDED.value, run.deadline_message(label)),
                        None,
                    )
            return raw_output, None, None

    def _reason_within(
        self, request: AIItineraryReasoningRequest, run: StageRun, route: StageRoute | None = None
    ) -> AIItineraryReasoningResult:
        ref_map = CandidateRefMap.for_candidates(request.allowed_candidates)
        raw_output, failure, not_connected = self._invoke_within(
            run, self._build_client, _build_prompt(request, ref_map), "groq_reasoning", route,
            _GroqItineraryReasoningSchema,
        )
        if not_connected is not None:
            return self._not_connected_result(request, not_connected)
        if failure is not None:
            return self._rejected_result(request, failure[1], failure_kind=failure[0])

        output_dict = self._coerce_output(raw_output)
        if output_dict is None:
            return self._rejected_result(request, "Groq did not return a structured response.")

        # Section 202C.1C: references -> exact allowed candidate_ids (exact
        # lookup, never a correction). An unknown reference rejects the whole
        # output, and `_build_result_from_output` still validates the real
        # ids against `request.allowed_candidates` afterwards.
        try:
            output_dict = {**output_dict, "days": resolve_day_refs(output_dict.get("days"), ref_map)}
            output_dict = scrub_output_prose(output_dict, ref_map)
        except UnknownCandidateReference as exc:
            return self._rejected_result(
                request, f"Groq output referenced a candidate outside the allowed set: {exc}."
            )

        return self._build_result_from_output(request, output_dict)

    def _build_client(self, timeout: float | None = None) -> Any:
        """Lazily imports and constructs the real Groq client, bound to the
        structured-output schema -- same `method="json_schema", strict=True`
        selection Section 191A.1 established for
        `GroqAICandidateProposalProvider`, verified there directly against
        the installed `langchain_groq` package source (never sends
        `tools`/`tool_choice` in this mode).
        """
        try:
            from langchain_groq import ChatGroq
        except ImportError as exc:
            raise RuntimeError("The 'langchain_groq' package is not installed.") from exc

        capture = open_groq_quota_capture()
        chat = ChatGroq(
            model=self._model,
            api_key=self._api_key,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            # Section 1C: the timeout of THIS attempt (never more than the
            # stage budget has left), and no hidden SDK retries -- the one
            # recovery attempt is made by the adapter, under the stage budget.
            timeout=timeout if timeout is not None else get_settings().groq_request_timeout_seconds,
            max_retries=0,
            # Reads the response's rate-limit numbers (nothing else).
            http_client=capture.http_client,
        )
        return chat.with_structured_output(
            _GroqItineraryReasoningSchema, method="json_schema", strict=True
        )

    @staticmethod
    def _coerce_output(raw_output: Any) -> dict[str, Any] | None:
        if isinstance(raw_output, dict):
            return raw_output
        model_dump = getattr(raw_output, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump()
            except Exception:
                return None
            return dumped if isinstance(dumped, dict) else None
        return None

    def _build_result_from_output(
        self, request: AIItineraryReasoningRequest, output: dict[str, Any]
    ) -> AIItineraryReasoningResult:
        raw_strategy = output.get("strategy")
        if not isinstance(raw_strategy, dict):
            return self._rejected_result(request, "Groq output did not include a strategy.")
        try:
            strategy = ItineraryReasoningStrategy(**raw_strategy)
        except ValidationError as exc:
            return self._rejected_result(request, f"Groq strategy failed validation: {exc}")

        raw_days = output.get("days")
        if not isinstance(raw_days, list):
            return self._rejected_result(request, "Groq output did not include a days list.")

        days: list[ItineraryReasoningDayPlan] = []
        for raw_day in raw_days:
            if not isinstance(raw_day, dict):
                return self._rejected_result(request, "Groq output contained a malformed day entry.")
            try:
                placements = [
                    ItineraryReasoningCandidatePlacement(**placement)
                    for placement in raw_day.get("approximate_structure") or []
                ]
                days.append(
                    ItineraryReasoningDayPlan(
                        day_index=raw_day.get("day_index"),
                        candidate_ids=raw_day.get("candidate_ids") or [],
                        rationale=raw_day.get("rationale"),
                        tradeoffs=raw_day.get("tradeoffs"),
                        approximate_structure=placements,
                    )
                )
            except ValidationError as exc:
                return self._rejected_result(request, f"Groq output failed day validation: {exc}")

        if not days:
            return self._rejected_result(request, "Groq returned no scheduled days.")

        raw_overall_tradeoffs = output.get("overall_tradeoffs") or []
        overall_tradeoffs = (
            [item for item in raw_overall_tradeoffs if isinstance(item, str)]
            if isinstance(raw_overall_tradeoffs, list)
            else []
        )

        confidence = output.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = 0.0
        confidence = max(0.0, min(1.0, float(confidence)))

        try:
            result = AIItineraryReasoningResult(
                status=AIItineraryReasoningStatus.COMPLETED,
                strategy=strategy,
                days=days,
                overall_tradeoffs=overall_tradeoffs,
                guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
                provider_name=self.provider_name,
                model_name=self._model,
                confidence=confidence,
            )
        except ValidationError as exc:
            return self._rejected_result(request, f"Assembled reasoning result failed validation: {exc}")

        # Section 193A candidate-ID safety validation: even schema-valid,
        # domain-valid output must still be checked against
        # request.allowed_candidates -- structured output never eliminates
        # this semantic check.
        violations = validate_result_against_request(request, result)
        if violations:
            return self._rejected_result(
                request,
                "Groq output referenced candidate(s) outside the allowed set or violated "
                "day/candidate placement rules: " + "; ".join(violations),
            )

        return result

    def _not_connected_result(
        self, request: AIItineraryReasoningRequest, reason: str
    ) -> AIItineraryReasoningResult:
        return AIItineraryReasoningResult(
            status=AIItineraryReasoningStatus.NOT_CONNECTED,
            days=[],
            guardrail_report=AIItineraryReasoningGuardrailReport(
                passed=False,
                blocked_reasons=[reason],
                checked_fields=["groq_api_key", "groq_client"],
            ),
            provider_name=self.provider_name,
            model_name=self._model,
            confidence=0.0,
        )

    def _rejected_result(
        self, request: AIItineraryReasoningRequest, reason: str, failure_kind: str | None = None
    ) -> AIItineraryReasoningResult:
        return AIItineraryReasoningResult(
            status=AIItineraryReasoningStatus.REJECTED,
            days=[],
            guardrail_report=AIItineraryReasoningGuardrailReport(
                passed=False,
                blocked_reasons=[reason],
                checked_fields=["structured_output"],
            ),
            provider_name=self.provider_name,
            model_name=self._model,
            confidence=0.0,
            failure_kind=failure_kind,
        )

    # -----------------------------------------------------------------
    # Section 194A: repair. Same client/API-key resolution as `reason`
    # above (Task 9: reuse, never duplicate).
    # -----------------------------------------------------------------

    def repair(self, request: AIItineraryRepairRequest) -> AIItineraryRepairResult:
        if self._has_no_provider():
            return self._attributed(
                self._not_connected_repair_result(request, not_configured_message(self._selected_provider)), None
            )

        # Section 1C: same total-budget / single-recovery policy as `reason`.
        # A repair that does not complete leaves the plan to the existing
        # deterministic top-up / needs-review path.
        settings = get_settings()
        run = StageRun(
            "groq_repair",
            total_budget_seconds=settings.groq_repair_total_budget_seconds,
            request_timeout_seconds=settings.groq_request_timeout_seconds,
            transport_retries=settings.groq_max_retries,
        )
        route = self._start_route(run)
        result = self._repair_within(request, run, route)
        close_route(run, route, completed=result.status == AIItineraryRepairStatus.COMPLETED)
        return self._attributed(result, route)

    def _repair_within(
        self, request: AIItineraryRepairRequest, run: StageRun, route: StageRoute | None = None
    ) -> AIItineraryRepairResult:
        ref_map = CandidateRefMap.for_candidates(request.allowed_candidates)
        raw_output, failure, not_connected = self._invoke_within(
            run, self._build_repair_client, _build_repair_prompt(request, ref_map), "groq_repair", route,
            _GroqRepairSchema,
        )
        if not_connected is not None:
            return self._not_connected_repair_result(request, not_connected)
        if failure is not None:
            return self._rejected_repair_result(request, failure[1], failure_kind=failure[0])

        output_dict = self._coerce_output(raw_output)
        if output_dict is None:
            return self._rejected_repair_result(request, "Groq did not return a structured response.")

        try:
            output_dict = {
                **output_dict,
                "repaired_days": resolve_day_refs(output_dict.get("repaired_days"), ref_map),
            }
            output_dict = scrub_output_prose(output_dict, ref_map)
        except UnknownCandidateReference as exc:
            return self._rejected_repair_result(
                request, f"Groq repair output referenced a candidate outside the allowed set: {exc}."
            )

        return self._build_repair_result_from_output(request, output_dict)

    def _build_repair_client(self, timeout: float | None = None) -> Any:
        try:
            from langchain_groq import ChatGroq
        except ImportError as exc:
            raise RuntimeError("The 'langchain_groq' package is not installed.") from exc

        capture = open_groq_quota_capture()
        chat = ChatGroq(
            model=self._model,
            api_key=self._api_key,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            # Section 1C: the timeout of THIS attempt (never more than the
            # stage budget has left), and no hidden SDK retries -- the one
            # recovery attempt is made by the adapter, under the stage budget.
            timeout=timeout if timeout is not None else get_settings().groq_request_timeout_seconds,
            max_retries=0,
            http_client=capture.http_client,
        )
        return chat.with_structured_output(_GroqRepairSchema, method="json_schema", strict=True)

    def _build_repair_result_from_output(
        self, request: AIItineraryRepairRequest, output: dict[str, Any]
    ) -> AIItineraryRepairResult:
        raw_days = output.get("repaired_days")
        if not isinstance(raw_days, list):
            return self._rejected_repair_result(request, "Groq output did not include a repaired_days list.")

        repaired_days: list[ItineraryReasoningDayPlan] = []
        for raw_day in raw_days:
            if not isinstance(raw_day, dict):
                return self._rejected_repair_result(request, "Groq output contained a malformed day entry.")
            try:
                placements = [
                    ItineraryReasoningCandidatePlacement(**placement)
                    for placement in raw_day.get("approximate_structure") or []
                ]
                repaired_days.append(
                    ItineraryReasoningDayPlan(
                        day_index=raw_day.get("day_index"),
                        candidate_ids=raw_day.get("candidate_ids") or [],
                        rationale=raw_day.get("rationale"),
                        tradeoffs=raw_day.get("tradeoffs"),
                        approximate_structure=placements,
                    )
                )
            except ValidationError as exc:
                return self._rejected_repair_result(request, f"Groq output failed day validation: {exc}")

        if not repaired_days:
            return self._rejected_repair_result(request, "Groq returned no repaired days.")

        repair_summary = output.get("repair_summary")
        if not isinstance(repair_summary, str) or not repair_summary.strip():
            return self._rejected_repair_result(request, "Groq output did not include a repair_summary.")

        raw_addressed = output.get("addressed_issue_types") or []
        addressed_issue_types: list[RepairableIssueType] = []
        if isinstance(raw_addressed, list):
            for item in raw_addressed:
                try:
                    addressed_issue_types.append(RepairableIssueType(item))
                except ValueError:
                    continue

        confidence = output.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = 0.0
        confidence = max(0.0, min(1.0, float(confidence)))

        try:
            result = AIItineraryRepairResult(
                status=AIItineraryRepairStatus.COMPLETED,
                repaired_days=repaired_days,
                repair_summary=repair_summary,
                addressed_issue_types=addressed_issue_types,
                guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
                provider_name=self.provider_name,
                model_name=self._model,
                confidence=confidence,
                attempt_number=request.attempt_number,
            )
        except ValidationError as exc:
            return self._rejected_repair_result(request, f"Assembled repair result failed validation: {exc}")

        violations = validate_repair_result_against_request(request, result)
        if violations:
            return self._rejected_repair_result(
                request,
                "Groq repair output referenced a candidate/day outside the allowed scope: "
                + "; ".join(violations),
            )

        return result

    def _not_connected_repair_result(
        self, request: AIItineraryRepairRequest, reason: str
    ) -> AIItineraryRepairResult:
        return AIItineraryRepairResult(
            status=AIItineraryRepairStatus.NOT_CONNECTED,
            repaired_days=[],
            guardrail_report=AIItineraryReasoningGuardrailReport(
                passed=False,
                blocked_reasons=[reason],
                checked_fields=["groq_api_key", "groq_client"],
            ),
            provider_name=self.provider_name,
            model_name=self._model,
            confidence=0.0,
            attempt_number=request.attempt_number,
        )

    def _rejected_repair_result(
        self, request: AIItineraryRepairRequest, reason: str, failure_kind: str | None = None
    ) -> AIItineraryRepairResult:
        return AIItineraryRepairResult(
            status=AIItineraryRepairStatus.REJECTED,
            repaired_days=[],
            guardrail_report=AIItineraryReasoningGuardrailReport(
                passed=False,
                blocked_reasons=[reason],
                checked_fields=["structured_output"],
            ),
            provider_name=self.provider_name,
            model_name=self._model,
            confidence=0.0,
            attempt_number=request.attempt_number,
            failure_kind=failure_kind,
        )
