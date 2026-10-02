"""Per-proposal validation shared by every AI candidate proposal adapter
(Section 203C.2B, Lisbon canary correction).

A model response is a LIST of independent hypotheses. One malformed or
guardrail-violating entry is dropped on its own; the valid entries are
kept. Previously a single invalid entry rejected the whole batch, so one
bad rationale discarded every anchor.

Nothing here relaxes a check: every kept proposal still passed the full
`AICandidateProposal` validation (no coordinates, ids, ratings, prices,
hours, routes or availability). The summary of what was dropped names only
schema field names and error types -- never the model's text.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.models.ai_candidate_proposal import AICandidateProposal


def validate_proposals(raw_proposals: list[Any]) -> tuple[list[AICandidateProposal], int, str]:
    """`(valid, dropped_count, safe_summary)` for a raw proposals list."""
    valid: list[AICandidateProposal] = []
    problems: dict[str, int] = {}
    dropped = 0
    for raw in raw_proposals:
        if not isinstance(raw, dict):
            dropped += 1
            problems["entry:not_an_object"] = problems.get("entry:not_an_object", 0) + 1
            continue
        try:
            valid.append(AICandidateProposal(**raw))
        except ValidationError as exc:
            dropped += 1
            for error in exc.errors(include_input=False, include_context=False, include_url=False):
                field = ".".join(str(part) for part in error.get("loc", ())) or "proposal"
                key = f"{field}:{error.get('type', 'invalid')}"
                problems[key] = problems.get(key, 0) + 1
    summary = ", ".join(f"{key} x{count}" for key, count in sorted(problems.items()))
    return valid, dropped, summary
