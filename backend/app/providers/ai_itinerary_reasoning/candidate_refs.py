"""Section 202C.1C: short, bounded candidate references for the reasoning LLM.

Before this module the reasoning/repair prompts listed every allowed candidate
by its real `candidate_id` (`"<provider>:<provider_place_id>"`, e.g.
`openstreetmap_places:way/1332068226`) and the model had to copy that string
back verbatim. 202C.1B saw the same failure twice on live traffic: the model
returned `...:node/1332068226` for an allowed `...:way/1332068226`. The
guardrail correctly rejected the whole result (a different OSM element type is
a different place), so the trip silently fell back to deterministic planning.

The identity is a FACT owned by the provider; an LLM has no business
reproducing it. So the model now only ever sees and returns a short reference
(`c1`, `c2`, ...) that exists for one request:

    prompt:   candidate_id='c7' name='...'
    output:   {"candidate_ids": ["c7", ...]}
    resolve:  c7 -> the exact allowed candidate_id (dictionary lookup)

Rules (each is a safety property, tested in
`tests/providers/test_reasoning_candidate_refs_202c1c.py`):

  * references are assigned deterministically, in the request's own
    `allowed_candidates` order, one per distinct `candidate_id`;
  * resolution is an EXACT dictionary lookup -- no fuzzy matching, no
    name matching, no "node/X is probably way/X" correction;
  * anything that is not a known reference (including a raw provider id,
    even a real one) is an unknown reference and rejects the whole output,
    exactly like an out-of-set candidate did before;
  * after resolution the caller still runs the unchanged
    `validate_result_against_request` / `validate_repair_result_against_request`
    guardrails on the REAL ids.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_REF_PREFIX = "c"
# A reference token in model-written PROSE: "(c5)" or a bare "c5".
_PROSE_REF = re.compile(r"\s*\((c\d+)\)|\b(c\d+)\b")
# Free-text fields of the reasoning / repair outputs that reach the user.
_PROSE_KEYS = frozenset({"summary", "reason", "rationale", "tradeoffs", "repair_summary"})
_PROSE_LIST_KEYS = frozenset({"overall_tradeoffs"})


class UnknownCandidateReference(ValueError):
    """The model returned something that is not one of this request's references."""


class CandidateRefMap:
    def __init__(self, candidate_ids: Iterable[str], names: dict[str, str] | None = None) -> None:
        self._ref_by_id: dict[str, str] = {}
        self._id_by_ref: dict[str, str] = {}
        self._name_by_ref: dict[str, str] = {}
        for candidate_id in candidate_ids:
            if candidate_id in self._ref_by_id:
                continue  # one reference per distinct identity, however often it is listed
            ref = f"{_REF_PREFIX}{len(self._ref_by_id) + 1}"
            self._ref_by_id[candidate_id] = ref
            self._id_by_ref[ref] = candidate_id
            if names and names.get(candidate_id):
                self._name_by_ref[ref] = names[candidate_id]

    @classmethod
    def for_candidates(cls, candidates: Iterable[Any]) -> "CandidateRefMap":
        candidates = list(candidates)
        return cls(
            (candidate.candidate_id for candidate in candidates),
            names={candidate.candidate_id: candidate.name for candidate in candidates},
        )

    def scrub_prose(self, text: Any) -> Any:
        """A reference is an internal handle; it must not reach the traveler
        inside model-written prose (seen live: "Martinho da Arcada (c5)").
        A parenthesised known reference is dropped; a bare known reference is
        replaced by that candidate's provider name. Anything that is not one
        of THIS request's references is left exactly as written."""
        if not isinstance(text, str):
            return text

        def replace(match: re.Match[str]) -> str:
            parenthesised, bare = match.group(1), match.group(2)
            if parenthesised is not None:
                return "" if parenthesised in self._id_by_ref else match.group(0)
            return self._name_by_ref.get(bare, match.group(0)) if bare in self._id_by_ref else match.group(0)

        return _PROSE_REF.sub(replace, text)

    def __len__(self) -> int:
        return len(self._id_by_ref)

    def ref_for(self, candidate_id: str) -> str:
        """The reference shown to the model for an allowed candidate. Raises
        `KeyError` for an id that is not in this request (a programming error:
        the prompt may only ever list allowed candidates)."""
        return self._ref_by_id[candidate_id]

    def refs_for(self, candidate_ids: Iterable[str]) -> list[str]:
        """References for ids that are in this request; an id that is not
        (e.g. a previously scheduled place outside a scoped repair's
        candidate universe) is omitted rather than exposed as a raw id."""
        return [self._ref_by_id[cid] for cid in candidate_ids if cid in self._ref_by_id]

    def resolve(self, ref: Any) -> str:
        """Exact lookup only. Surrounding whitespace is the one tolerated
        deviation; nothing else is normalised, guessed or corrected."""
        if isinstance(ref, str):
            candidate_id = self._id_by_ref.get(ref.strip())
            if candidate_id is not None:
                return candidate_id
        raise UnknownCandidateReference(f"{ref!r} is not a candidate reference in this request")


def resolve_day_refs(raw_days: Any, ref_map: CandidateRefMap) -> list[Any]:
    """Returns a copy of the model's raw day list with every reference in
    `candidate_ids` and `approximate_structure[*].candidate_id` replaced by the
    exact allowed `candidate_id`. Raises `UnknownCandidateReference` on the
    first reference that does not resolve. Entries that are not shaped like a
    day (not a dict / no list) are passed through untouched so the caller's
    existing structural validation still reports them."""
    if not isinstance(raw_days, list):
        return raw_days
    resolved_days: list[Any] = []
    for raw_day in raw_days:
        if not isinstance(raw_day, dict):
            resolved_days.append(raw_day)
            continue
        day = dict(raw_day)
        day_label = raw_day.get("day_index")
        candidate_ids = raw_day.get("candidate_ids")
        if isinstance(candidate_ids, list):
            day["candidate_ids"] = [_resolve(ref, ref_map, day_label) for ref in candidate_ids]
        placements = raw_day.get("approximate_structure")
        if isinstance(placements, list):
            day["approximate_structure"] = [
                {**placement, "candidate_id": _resolve(placement.get("candidate_id"), ref_map, day_label)}
                if isinstance(placement, dict)
                else placement
                for placement in placements
            ]
        resolved_days.append(day)
    return resolved_days


def _resolve(ref: Any, ref_map: CandidateRefMap, day_label: Any) -> str:
    try:
        return ref_map.resolve(ref)
    except UnknownCandidateReference as exc:
        raise UnknownCandidateReference(f"day {day_label}: {exc}") from None


def scrub_output_prose(output: Any, ref_map: CandidateRefMap) -> Any:
    """Returns a copy of a raw reasoning/repair output with reference tokens
    removed from its free-text fields (`CandidateRefMap.scrub_prose`). Only
    the known prose keys are touched; ids and structure are not."""
    if isinstance(output, list):
        return [scrub_output_prose(item, ref_map) for item in output]
    if not isinstance(output, dict):
        return output
    cleaned: dict[str, Any] = {}
    for key, value in output.items():
        if key in _PROSE_KEYS and isinstance(value, str):
            cleaned[key] = ref_map.scrub_prose(value)
        elif key in _PROSE_LIST_KEYS and isinstance(value, list):
            cleaned[key] = [ref_map.scrub_prose(item) for item in value]
        elif isinstance(value, (dict, list)):
            cleaned[key] = scrub_output_prose(value, ref_map)
        else:
            cleaned[key] = value
    return cleaned
