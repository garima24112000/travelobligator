from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

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

# Groq-backed AI candidate proposal adapter (Step 162A,
# itinerary-generator-build-spec.md Stage 5, docs/13_llm_reasoning_
# pipeline.md section 41, docs/14_backend_architecture.md section 25).
# Groq is a cheap/dev-iteration LLM base for AI candidate proposals,
# alongside (not replacing) the Anthropic/Claude adapter (Step 161A) --
# this adapter calls Groq through `langchain_groq.ChatGroq`'s structured
# output support, never through any other SDK or CLI.
#
# This adapter still only ever produces `AICandidateProposal` objects,
# which are not facts -- every proposal must still be independently
# grounded/verified by `CandidateGroundingService` (Step 159A) against
# real provider/open-data candidates before it can be scheduled. This
# module never grounds anything itself, never calls
# `CandidateGroundingService`, never calls a provider adapter, and never
# mutates `PlanningState`.
#
# The `langchain_groq` package import is deliberately deferred into
# `_build_client` (not a module-level import) so the rest of the app --
# and every test that injects a fake client or exercises the no-key path
# -- keeps working whether or not the package is installed. A missing
# `GROQ_API_KEY` (and, for the same reason, a missing `langchain_groq`
# package) never crashes the app: `propose` returns an honest
# `not_connected` result instead.


class _GroqProposalSchema(BaseModel):
    """Structured-output schema for one proposal. Maps 1:1 to
    `AICandidateProposal` fields -- deliberately no coordinate, provider id,
    price, rating, opening-hours, route-time, review-count, ticket-price,
    availability, booking-link, or safety-score field exists in this
    schema, matching the model's own contract (a proposal is a name and a
    one-line rationale, never a fact).
    """

    proposal_id: str = Field(description="A short unique id for this proposal, e.g. 'proposal_001'.")
    candidate_name: str = Field(description="The place or area name being proposed.")
    candidate_type: AICandidateType
    priority_hint: AICandidatePriorityHint = AICandidatePriorityHint.UNKNOWN
    suggested_area: str | None = Field(default=None, description="Optional neighborhood/area name, if known.")
    why_consider: str = Field(
        description="One-line rationale only -- no price, rating, hours, or route claims."
    )
    fit_with_user_preferences: list[str] = Field(
        default_factory=list,
        description="Short phrases explaining fit with the traveler's stated interests, if any.",
    )
    verification_requirements: list[AICandidateVerificationRequirement] = Field(
        min_length=1,
        description="What grounding must still confirm before this idea can be used.",
    )
    confidence: float = Field(ge=0.0, le=1.0)


class _GroqProposalBatchSchema(BaseModel):
    """Structured-output schema for the full response: a list of proposals
    plus a batch-level confidence. Mirrors the Anthropic adapter's forced
    tool-use schema shape, adapted for `with_structured_output`.
    """

    proposals: list[_GroqProposalSchema] = Field(default_factory=list)
    rejected_raw_items: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


_SYSTEM_PROMPT = (
    "You are a candidate-idea proposer for a travel planning system. You are a proposer, "
    "never a source of truth: every idea you submit is a proposal only, not a verified "
    "fact, and every proposal will be independently checked against real provider/open "
    "data before it can be used or scheduled.\n\n"
    "Propose candidate place/area ideas by name and a one-line rationale only. Return "
    "structured candidate proposals only, matching the required schema exactly.\n\n"
    "Use only the request fields given to you. Do not invent factual travel data. Do not "
    "claim ratings, prices, routes, availability, opening hours, or safety anywhere in "
    "your output. If you do not know a fact, omit it entirely -- never guess or invent "
    "one. Every idea you propose still requires independent grounding and verification "
    "against real provider/open data before it can be scheduled."
)


def _build_prompt(request: AICandidateProposalRequest) -> str:
    """Builds a minimal, controlled prompt from `request` fields only --
    never from raw provider data (only summary counts are available on the
    request in the first place).
    """
    return "\n".join(
        [
            _SYSTEM_PROMPT,
            "",
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


class GroqAICandidateProposalProvider(AICandidateProposalProvider):
    """`AICandidateProposalProvider` implementation backed by Groq (via
    `langchain_groq.ChatGroq`), used only when explicitly config-gated in
    via `get_ai_candidate_proposal_provider` (Step 160E, extended here) --
    never the default. Intended for cheap/dev-mode iteration, alongside the
    Anthropic adapter (Step 161A), not as a replacement for it.

    `propose` never invents an attraction, restaurant, accommodation,
    coordinate, price, rating, opening hour, route time, review count,
    ticket price, availability, booking link, or safety score: the
    structured-output schema Groq must respond through has no such fields,
    and every parsed proposal is still validated through
    `AICandidateProposal` before it can appear in a `completed` result. If
    validation fails, the call raises, or no usable structured output comes
    back, this returns an honest `rejected`/`not_connected` result with no
    proposals -- never a fabricated one.
    """

    provider_name = "groq_ai_candidate_proposal_provider"

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
            self._api_key = settings.groq_api_key
        else:
            self._api_key = api_key
        self._model = model if model is not None else settings.groq_model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def propose(self, request: AICandidateProposalRequest) -> AICandidateProposalResult:
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
            return self._rejected_result(
                request, "Groq did not return a structured response."
            )

        return self._build_result_from_output(request, output_dict)

    def _build_client(self) -> Any:
        """Lazily imports and constructs the real Groq client, bound to the
        structured-output schema. Kept inside a method (never a module-level
        import) so the rest of the app imports cleanly whether or not the
        `langchain_groq` package is installed, and so tests never need it
        installed either. `self._api_key`/`self._model` are forwarded
        explicitly rather than relying on ambient environment variables, so
        an explicitly constructed provider always uses its own config.
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
        return chat.with_structured_output(_GroqProposalBatchSchema)

    @staticmethod
    def _coerce_output(raw_output: Any) -> dict[str, Any] | None:
        """Accepts either a `_GroqProposalBatchSchema` instance (the normal
        `with_structured_output` result) or a plain dict (the shape a
        simpler fake/injected client might return), and normalizes both to
        a plain dict. Anything else is treated as malformed output.
        """
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
        self, request: AICandidateProposalRequest, output: dict[str, Any]
    ) -> AICandidateProposalResult:
        raw_proposals = output.get("proposals")
        if not isinstance(raw_proposals, list):
            return self._rejected_result(request, "Groq output did not include a proposals list.")

        proposals: list[AICandidateProposal] = []
        for raw_proposal in raw_proposals:
            if not isinstance(raw_proposal, dict):
                return self._rejected_result(request, "Groq output contained a malformed proposal entry.")
            try:
                proposals.append(AICandidateProposal(**raw_proposal))
            except ValidationError as exc:
                return self._rejected_result(
                    request, f"Groq output failed AICandidateProposal validation: {exc}"
                )

        if not proposals:
            return self._rejected_result(request, "Groq returned no candidate proposals.")

        confidence = output.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = 0.0
        confidence = max(0.0, min(1.0, float(confidence)))

        raw_rejected_items = output.get("rejected_raw_items") or []
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
                checked_fields=["groq_api_key", "groq_client"],
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
                checked_fields=["structured_output"],
            ),
            provider_name=self.provider_name,
            model_name=self._model,
            confidence=0.0,
        )
