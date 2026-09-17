from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.models.candidate_grounding import ProviderCandidateForGrounding

# Contract models for the Section 192 AI-directed provider discovery step
# (docs/14_backend_architecture.md section 138). These models exist to give
# `AIDirectedProviderDiscoveryService` a typed result shape instead of a
# dict -- never a new source of truth of their own. Every factual field on
# `AIProviderDiscoveryAttempt.match` is a `ProviderCandidateForGrounding`
# (Step 159A), the exact same real-provider-evidence model
# `CandidateGroundingService` already trusts -- this module invents no new
# way for a coordinate/category/confidence value to enter the system.
#
# Core rule (unchanged from Sections 191A/191B): an `AICandidateProposal`'s
# `search_query`/`candidate_name` is a lookup hint, never a fact. Only a
# `ProviderCandidateForGrounding` this module receives back from
# `ProviderGateway.places` (a real provider response) can ever populate
# `AIProviderDiscoveryAttempt.match`.


class AIProviderDiscoveryAttemptStatus(str, Enum):
    """Outcome of one targeted provider lookup for one AI candidate
    proposal. `NOT_SEARCHED` covers both "this proposal already had a
    clean broad-pool match, so no targeted lookup was needed" and "the
    per-generation search bound was already exhausted" -- either way, no
    provider call was made for that proposal.
    """

    MATCHED = "matched"
    NOT_FOUND = "not_found"
    PROVIDER_FAILED = "provider_failed"
    PROVIDER_NOT_CONNECTED = "provider_not_connected"
    NOT_SEARCHED = "not_searched"


class AIProviderDiscoveryAttempt(BaseModel):
    """One proposal's targeted provider-discovery outcome. `match` is the
    real provider evidence found for `proposal_id`'s `search_query`, and is
    required (non-None) exactly when `status="matched"` -- never present
    for any other status, and never guessed when the provider found
    nothing or failed.
    """

    proposal_id: str
    search_query: str
    status: AIProviderDiscoveryAttemptStatus
    match: ProviderCandidateForGrounding | None = None
    message: str

    @field_validator("proposal_id", "search_query", "message")
    @classmethod
    def validate_not_blank(cls, value: str, info: ValidationInfo) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be blank.")
        return value

    @model_validator(mode="after")
    def validate_match_matches_status(self) -> "AIProviderDiscoveryAttempt":
        if self.status == AIProviderDiscoveryAttemptStatus.MATCHED and self.match is None:
            raise ValueError(
                "AIProviderDiscoveryAttempt.match is required when status is 'matched'."
            )
        if self.status != AIProviderDiscoveryAttemptStatus.MATCHED and self.match is not None:
            raise ValueError(
                "AIProviderDiscoveryAttempt.match must be None when status is not 'matched'."
            )
        return self


class AIProviderDiscoveryResult(BaseModel):
    """Plan-level, deterministic rollup of every targeted provider-
    discovery attempt run for one AI candidate proposal batch. Read-only
    bookkeeping only -- never mutates a `PlanningState` itself; the caller
    decides what to do with `attempts`/`matches_by_proposal_id()`.
    """

    model_config = ConfigDict(protected_namespaces=())

    attempts: list[AIProviderDiscoveryAttempt] = Field(default_factory=list)
    provider_name: str | None = None
    searched_count: int = Field(default=0, ge=0)
    matched_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_counts_match_attempts(self) -> "AIProviderDiscoveryResult":
        actual_searched = sum(
            1
            for attempt in self.attempts
            if attempt.status != AIProviderDiscoveryAttemptStatus.NOT_SEARCHED
        )
        actual_matched = sum(
            1
            for attempt in self.attempts
            if attempt.status == AIProviderDiscoveryAttemptStatus.MATCHED
        )
        if actual_searched != self.searched_count:
            raise ValueError(
                "AIProviderDiscoveryResult.searched_count "
                f"({self.searched_count}) does not match the number of searched "
                f"attempts ({actual_searched})."
            )
        if actual_matched != self.matched_count:
            raise ValueError(
                "AIProviderDiscoveryResult.matched_count "
                f"({self.matched_count}) does not match the number of matched "
                f"attempts ({actual_matched})."
            )
        return self

    def matches_by_proposal_id(self) -> dict[str, ProviderCandidateForGrounding]:
        """The only thing `CandidateGroundingService` needs from this
        result: a `proposal_id -> ProviderCandidateForGrounding` mapping
        for every proposal this service actually matched. Proposal
        identity is preserved end to end -- a match is only ever looked up
        by the same `proposal_id` the originating `AICandidateProposal`
        already carried, never by name-text similarity.
        """
        return {
            attempt.proposal_id: attempt.match
            for attempt in self.attempts
            if attempt.status == AIProviderDiscoveryAttemptStatus.MATCHED and attempt.match is not None
        }
