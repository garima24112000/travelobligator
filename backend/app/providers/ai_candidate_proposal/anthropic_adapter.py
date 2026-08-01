from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.config import get_settings
from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateProposalGuardrailReport,
    AICandidateProposalRequest,
    AICandidateProposalResult,
    AICandidateProposalStatus,
    AICandidatePriorityHint,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.providers.ai_candidate_proposal.base import AICandidateProposalProvider

# Anthropic/Claude-backed AI candidate proposal adapter (Step 161A,
# itinerary-generator-build-spec.md Stage 5, docs/13_llm_reasoning_
# pipeline.md section 39, docs/14_backend_architecture.md section 25).
# Claude/Anthropic is the selected LLM base for AI candidate proposals --
# this adapter calls Claude through the official Anthropic Python SDK's
# Messages API (`client.messages.create`), never through the Claude Code
# CLI or any local coding-agent runtime.
#
# This adapter still only ever produces `AICandidateProposal` objects,
# which are not facts -- every proposal must still be independently
# grounded/verified by `CandidateGroundingService` (Step 159A) against
# real provider/open-data candidates before it can be scheduled. This
# module never grounds anything itself, never calls
# `CandidateGroundingService`, never calls a provider adapter, and never
# mutates `PlanningState`.
#
# The `anthropic` package import is deliberately deferred into
# `_build_client` (not a module-level import) so the rest of the app --
# and every test that injects a fake client or exercises the no-key path
# -- keeps working whether or not the package is installed. A missing
# `ANTHROPIC_API_KEY` (and, for the same reason, a missing `anthropic`
# package) never crashes the app: `propose` returns an honest
# `not_connected` result instead.

_TOOL_NAME = "submit_ai_candidate_proposals"

# Schema for one proposal inside the tool's `proposals` array. Maps 1:1 to
# `AICandidateProposal` fields -- deliberately no coordinate, provider id,
# price, rating, opening-hours, route-time, review-count, ticket-price,
# availability, booking-link, or safety-score field exists in this schema,
# matching the model's own contract (a proposal is a name and a one-line
# rationale, never a fact).
_PROPOSAL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposal_id": {"type": "string", "description": "A short unique id for this proposal, e.g. 'proposal_001'."},
        "candidate_name": {"type": "string", "description": "The place or area name being proposed."},
        "candidate_type": {"type": "string", "enum": [member.value for member in AICandidateType]},
        "priority_hint": {"type": "string", "enum": [member.value for member in AICandidatePriorityHint]},
        "suggested_area": {"type": "string", "description": "Optional neighborhood/area name, if known."},
        "why_consider": {
            "type": "string",
            "description": "One-line rationale only -- no price, rating, hours, or route claims.",
        },
        "fit_with_user_preferences": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short phrases explaining fit with the traveler's stated interests, if any.",
        },
        "verification_requirements": {
            "type": "array",
            "items": {"type": "string", "enum": [member.value for member in AICandidateVerificationRequirement]},
            "minItems": 1,
            "description": "What grounding must still confirm before this idea can be used.",
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["proposal_id", "candidate_name", "candidate_type", "why_consider", "verification_requirements", "confidence"],
    "additionalProperties": False,
}

_TOOL_DEFINITION: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": (
        "Submit candidate place/area ideas for a trip destination. Each idea is only a "
        "proposal, never a verified fact -- every idea will be independently checked "
        "against real provider/open data before it can be used or scheduled. Do not "
        "include coordinates, provider ids, prices, ratings, opening hours, route times, "
        "review counts, ticket prices, availability, booking links, or safety scores "
        "anywhere in your output."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "proposals": {"type": "array", "items": _PROPOSAL_INPUT_SCHEMA},
            "rejected_raw_items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Raw idea text you considered but could not turn into a safe proposal.",
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["proposals", "confidence"],
        "additionalProperties": False,
    },
}

_SYSTEM_PROMPT = (
    "You are a candidate-idea proposer for a travel planning system. You are a proposer, "
    "never a source of truth: every idea you submit is a proposal only, not a verified "
    "fact, and every proposal will be independently checked against real provider/open "
    "data before it can be used or scheduled.\n\n"
    "Propose candidate place/area ideas by name and a one-line rationale only. You MUST "
    "call the submit_ai_candidate_proposals tool to respond, and your output must match "
    "its schema exactly.\n\n"
    "Do not include coordinates, provider ids, prices, ratings, opening hours, route "
    "times, review counts, ticket prices, availability, booking links, or safety scores "
    "anywhere in your output. If you do not know a fact, omit it entirely -- never guess "
    "or invent one. Every idea you propose still requires independent grounding and "
    "verification against real provider/open data before it can be scheduled."
)


def _build_prompt(request: AICandidateProposalRequest) -> str:
    """Builds a minimal, controlled prompt from `request` fields only --
    never from raw provider data (only summary counts are available on the
    request in the first place).
    """
    return "\n".join(
        [
            f"Task: {request.task.value}",
            f"Destination: {request.destination_name}",
            f"Trip duration (days): {request.trip_duration_days}",
            f"Interests: {', '.join(request.interests) if request.interests else 'none specified'}",
            f"Must-visit: {', '.join(request.must_visit) if request.must_visit else 'none specified'}",
            f"Constraints: {', '.join(request.constraints) if request.constraints else 'none specified'}",
            "Existing provider candidate counts (already covered, do not restate as new ideas): "
            f"{request.provider_candidate_summary or 'none'}",
            "Unavailable data fields: "
            f"{', '.join(request.unavailable_data) if request.unavailable_data else 'none'}",
            f"Maximum candidates to propose: {request.max_candidates}",
        ]
    )


class AnthropicAICandidateProposalProvider(AICandidateProposalProvider):
    """`AICandidateProposalProvider` implementation backed by the Anthropic
    Messages API (Claude), used only when explicitly config-gated in via
    `get_ai_candidate_proposal_provider` (Step 160E) -- never the default.

    `propose` never invents an attraction, restaurant, accommodation,
    coordinate, price, rating, opening hour, route time, review count,
    ticket price, availability, booking link, or safety score: the tool
    schema Claude must respond through has no such fields, and every
    parsed proposal is still validated through `AICandidateProposal`
    before it can appear in a `completed` result. If validation fails, the
    call raises, or no usable structured output comes back, this returns
    an honest `rejected`/`not_connected` result with no proposals --
    never a fabricated one.
    """

    provider_name = "anthropic_ai_candidate_proposal_provider"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
        max_tokens: int = 1200,
        temperature: float = 0.2,
    ) -> None:
        settings = get_settings()

        self._client = client
        if api_key is None and client is None:
            self._api_key = settings.anthropic_api_key
        else:
            self._api_key = api_key
        self._model = model if model is not None else settings.anthropic_model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def propose(self, request: AICandidateProposalRequest) -> AICandidateProposalResult:
        if self._client is None and not self._api_key:
            return self._not_connected_result(
                request, "Anthropic API key is not configured (ANTHROPIC_API_KEY unset)."
            )

        if self._client is not None:
            client = self._client
        else:
            try:
                client = self._build_client()
            except Exception as exc:  # missing package / bad config -> not_connected
                return self._not_connected_result(
                    request, f"Anthropic client could not be initialized: {exc}"
                )

        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                system=_SYSTEM_PROMPT,
                tools=[_TOOL_DEFINITION],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                messages=[{"role": "user", "content": _build_prompt(request)}],
            )
        except Exception as exc:  # API/runtime failure -> rejected, never fabricated
            return self._rejected_result(request, f"Anthropic API call failed: {exc}")

        tool_input = self._extract_tool_input(response)
        if tool_input is None:
            return self._rejected_result(
                request, "Claude did not return a structured tool_use response."
            )

        return self._build_result_from_tool_input(request, tool_input)

    @staticmethod
    def _build_client() -> Any:
        """Lazily imports and constructs the real Anthropic client. Kept
        inside a method (never a module-level import) so the rest of the
        app imports cleanly whether or not the `anthropic` package is
        installed, and so tests never need it installed either.
        """
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("The 'anthropic' package is not installed.") from exc
        return anthropic.Anthropic()

    @staticmethod
    def _extract_tool_input(response: Any) -> dict[str, Any] | None:
        content = getattr(response, "content", None) or []
        for block in content:
            if getattr(block, "type", None) != "tool_use":
                continue
            if getattr(block, "name", None) != _TOOL_NAME:
                continue
            tool_input = getattr(block, "input", None)
            if isinstance(tool_input, dict):
                return tool_input
        return None

    def _build_result_from_tool_input(
        self, request: AICandidateProposalRequest, tool_input: dict[str, Any]
    ) -> AICandidateProposalResult:
        raw_proposals = tool_input.get("proposals")
        if not isinstance(raw_proposals, list):
            return self._rejected_result(request, "Tool output did not include a proposals list.")

        proposals: list[AICandidateProposal] = []
        for raw_proposal in raw_proposals:
            if not isinstance(raw_proposal, dict):
                return self._rejected_result(request, "Tool output contained a malformed proposal entry.")
            try:
                proposals.append(AICandidateProposal(**raw_proposal))
            except ValidationError as exc:
                return self._rejected_result(
                    request, f"Tool output failed AICandidateProposal validation: {exc}"
                )

        if not proposals:
            return self._rejected_result(request, "Claude returned no candidate proposals.")

        confidence = tool_input.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = 0.0
        confidence = max(0.0, min(1.0, float(confidence)))

        raw_rejected_items = tool_input.get("rejected_raw_items") or []
        rejected_raw_items = (
            [item for item in raw_rejected_items if isinstance(item, str)]
            if isinstance(raw_rejected_items, list)
            else []
        )

        try:
            return AICandidateProposalResult(
                task=request.task,
                status=AICandidateProposalStatus.COMPLETED,
                proposals=proposals,
                rejected_raw_items=rejected_raw_items,
                guardrail_report=AICandidateProposalGuardrailReport(passed=True),
                provider_name=self.provider_name,
                model_name=self._model,
                confidence=confidence,
            )
        except ValidationError as exc:
            return self._rejected_result(request, f"Assembled proposal result failed validation: {exc}")

    def _not_connected_result(
        self, request: AICandidateProposalRequest, reason: str
    ) -> AICandidateProposalResult:
        return AICandidateProposalResult(
            task=request.task,
            status=AICandidateProposalStatus.NOT_CONNECTED,
            proposals=[],
            rejected_raw_items=[],
            guardrail_report=AICandidateProposalGuardrailReport(
                passed=False,
                blocked_reasons=[reason],
                checked_fields=["anthropic_api_key", "anthropic_client"],
            ),
            provider_name=self.provider_name,
            model_name=self._model,
            confidence=0.0,
        )

    def _rejected_result(
        self, request: AICandidateProposalRequest, reason: str
    ) -> AICandidateProposalResult:
        return AICandidateProposalResult(
            task=request.task,
            status=AICandidateProposalStatus.REJECTED,
            proposals=[],
            rejected_raw_items=[],
            guardrail_report=AICandidateProposalGuardrailReport(
                passed=False,
                blocked_reasons=[reason],
                checked_fields=["tool_use_output"],
            ),
            provider_name=self.provider_name,
            model_name=self._model,
            confidence=0.0,
        )
