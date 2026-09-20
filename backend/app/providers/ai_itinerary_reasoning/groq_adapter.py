from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

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
    "using each candidate's quality_score/quality_tier as a pre-ranking signal.\n\n"
    "A candidate should appear on at most one day. Return only the structured reasoning "
    "result matching the required schema exactly -- every schema key is required, so use "
    "null or an empty list instead of omitting a key or guessing a value."
)


def _format_candidate_line(candidate: Any) -> str:
    return (
        f"- candidate_id={candidate.candidate_id!r} name={candidate.name!r} "
        f"category={candidate.category.value} quality_tier={candidate.quality_tier} "
        f"quality_score={candidate.quality_score:.2f}"
    )


def _build_prompt(request: AIItineraryReasoningRequest) -> str:
    """Builds a minimal, controlled prompt from `request` fields only --
    never a raw `PlanningState` dump, and never a coordinate (193A's own
    design intent: the reasoning model is not asked to reason about
    distances/travel times, that stays deterministic routing's job).
    """
    traveler = request.traveler_context
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
    lines.extend(_format_candidate_line(candidate) for candidate in request.allowed_candidates)
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


def _build_repair_prompt(request: AIItineraryRepairRequest) -> str:
    """Builds a minimal, controlled repair prompt from `request` fields
    only -- never a raw `PlanningState` dump, and never a coordinate."""
    traveler = request.traveler_context
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
        lines.append(f"- day {day.day_index}: candidate_ids={day.candidate_ids}")
    lines.append(
        f"Allowed candidates (use only these candidate_id values, "
        f"{len(request.allowed_candidates)} total, same universe as the original plan):"
    )
    lines.extend(_format_candidate_line(candidate) for candidate in request.allowed_candidates)
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

    def reason(self, request: AIItineraryReasoningRequest) -> AIItineraryReasoningResult:
        if self._client is None and not self._api_key:
            return self._not_connected_result(
                request, "Groq API key is not configured (GROQ_API_KEY unset)."
            )

        if self._client is not None:
            client = self._client
        else:
            try:
                client = self._build_client()
            except Exception as exc:  # missing package / bad config -> not_connected
                return self._not_connected_result(
                    request, f"Groq client could not be initialized: {exc}"
                )

        try:
            raw_output = client.invoke(_build_prompt(request))
        except Exception as exc:  # API/runtime failure -> rejected, never fabricated
            return self._rejected_result(request, f"Groq API call failed: {exc}")

        output_dict = self._coerce_output(raw_output)
        if output_dict is None:
            return self._rejected_result(request, "Groq did not return a structured response.")

        return self._build_result_from_output(request, output_dict)

    def _build_client(self) -> Any:
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

        chat = ChatGroq(
            model=self._model,
            api_key=self._api_key,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
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
        self, request: AIItineraryReasoningRequest, reason: str
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
        )

    # -----------------------------------------------------------------
    # Section 194A: repair. Same client/API-key resolution as `reason`
    # above (Task 9: reuse, never duplicate).
    # -----------------------------------------------------------------

    def repair(self, request: AIItineraryRepairRequest) -> AIItineraryRepairResult:
        if self._client is None and not self._api_key:
            return self._not_connected_repair_result(
                request, "Groq API key is not configured (GROQ_API_KEY unset)."
            )

        if self._client is not None:
            client = self._client
        else:
            try:
                client = self._build_repair_client()
            except Exception as exc:
                return self._not_connected_repair_result(
                    request, f"Groq client could not be initialized: {exc}"
                )

        try:
            raw_output = client.invoke(_build_repair_prompt(request))
        except Exception as exc:  # API/runtime failure -> rejected, never fabricated
            return self._rejected_repair_result(request, f"Groq API call failed: {exc}")

        output_dict = self._coerce_output(raw_output)
        if output_dict is None:
            return self._rejected_repair_result(request, "Groq did not return a structured response.")

        return self._build_repair_result_from_output(request, output_dict)

    def _build_repair_client(self) -> Any:
        try:
            from langchain_groq import ChatGroq
        except ImportError as exc:
            raise RuntimeError("The 'langchain_groq' package is not installed.") from exc

        chat = ChatGroq(
            model=self._model,
            api_key=self._api_key,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
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
        self, request: AIItineraryRepairRequest, reason: str
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
        )
