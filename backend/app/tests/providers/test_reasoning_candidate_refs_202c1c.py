from __future__ import annotations

from typing import Any

import pytest

from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningRequest,
    AIItineraryReasoningStatus,
    CandidateOrigin,
    ItineraryCandidateReference,
    ItineraryReasoningCategory,
    TravelerContextSummary,
)
from app.models.common import DataStatus, GeoPoint
from app.providers.ai_itinerary_reasoning import anthropic_adapter, groq_adapter
from app.providers.ai_itinerary_reasoning.candidate_refs import (
    CandidateRefMap,
    UnknownCandidateReference,
    resolve_day_refs,
)
from app.providers.ai_itinerary_reasoning.groq_adapter import GroqAIItineraryReasoningProvider

# Section 202C.1C: the reasoning model selects short per-request references
# (c1, c2, ...); the application resolves them to the exact provider-backed
# identity. Live evidence (202C.1B): asked to copy `...:way/1332068226`, the
# model returned `...:node/1332068226` twice and the whole plan was rejected.
# No network: fake clients only.

_WAY = "openstreetmap_places:way/1332068226"
_NODE = "openstreetmap_places:node/1332068226"  # same number, different OSM element = different place
_RELATION = "openstreetmap_places:relation/1332068226"


def _candidate(candidate_id: str, name: str, lat: float) -> ItineraryCandidateReference:
    provider, place_id = candidate_id.split(":", 1)
    return ItineraryCandidateReference(
        candidate_id=candidate_id,
        name=name,
        category=ItineraryReasoningCategory.ATTRACTION,
        provider_name=provider,
        provider_place_id=place_id,
        coordinates=GeoPoint(lat=lat, lng=-77.0),
        data_status=DataStatus.LIVE,
        quality_score=0.8,
        quality_tier="primary_anchor",
        origin=CandidateOrigin.BROAD_PROVIDER_DISCOVERY,
    )


def _request(candidates: list[ItineraryCandidateReference]) -> AIItineraryReasoningRequest:
    return AIItineraryReasoningRequest(
        trip_id="trip_001",
        destination_name="Washington, DC, USA",
        start_date="2026-10-11",
        end_date="2026-10-13",
        trip_duration_days=3,
        traveler_context=TravelerContextSummary(
            travelers_count=4, travel_group_type="family", pace="balanced", interests=["history"]
        ),
        allowed_candidates=candidates,
    )


class _Client:
    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> Any:
        self.prompts.append(prompt)
        return self.output


def _output(candidate_refs: list[str], placement_ref: str | None = None) -> dict[str, Any]:
    return {
        "strategy": {"summary": "A history plan.", "pace": "balanced", "reason": "Matches the request."},
        "days": [
            {
                "day_index": 1,
                "candidate_ids": candidate_refs,
                "rationale": "Groups the memorial core.",
                "tradeoffs": None,
                "approximate_structure": [
                    {"candidate_id": placement_ref or candidate_refs[0], "time_window": "morning"}
                ],
            }
        ],
        "overall_tradeoffs": [],
        "confidence": 0.8,
    }


# -- the reference map itself -------------------------------------------------------


def test_references_are_deterministic_short_and_only_cover_allowed_candidates() -> None:
    ids = [_WAY, "openstreetmap_places:node/7", "openstreetmap_places:relation/9"]
    first, second = CandidateRefMap(ids), CandidateRefMap(ids)
    assert [first.ref_for(i) for i in ids] == ["c1", "c2", "c3"] == [second.ref_for(i) for i in ids]
    assert len(first) == 3
    assert first.resolve("c1") == _WAY and first.resolve(" c3 ") == "openstreetmap_places:relation/9"


def test_duplicate_candidate_gets_one_reference() -> None:
    ref_map = CandidateRefMap([_WAY, _WAY, "openstreetmap_places:node/7"])
    assert len(ref_map) == 2 and ref_map.ref_for(_WAY) == "c1" and ref_map.resolve("c2") == "openstreetmap_places:node/7"


@pytest.mark.parametrize(
    "bad",
    ["c4", "c0", "C1", "c01", "1", "", "way/1332068226", _WAY, _NODE, "Lincoln Memorial", None, 1, ["c1"]],
)
def test_anything_that_is_not_a_known_reference_is_rejected(bad: Any) -> None:
    ref_map = CandidateRefMap([_WAY, "openstreetmap_places:node/7", "openstreetmap_places:relation/9"])
    with pytest.raises(UnknownCandidateReference):
        ref_map.resolve(bad)  # even a REAL allowed raw id: the model never authors identities


def test_same_numeric_suffix_across_node_way_relation_stays_three_distinct_places() -> None:
    ref_map = CandidateRefMap([_NODE, _WAY, _RELATION])
    assert {ref_map.resolve(r) for r in ("c1", "c2", "c3")} == {_NODE, _WAY, _RELATION}
    assert ref_map.resolve("c2") == _WAY and ref_map.resolve("c1") == _NODE


def test_resolve_day_refs_replaces_ids_and_placements_and_raises_on_unknown() -> None:
    ref_map = CandidateRefMap([_WAY, "openstreetmap_places:node/7"])
    days = resolve_day_refs(_output(["c2", "c1"], placement_ref="c1")["days"], ref_map)
    assert days[0]["candidate_ids"] == ["openstreetmap_places:node/7", _WAY]
    assert days[0]["approximate_structure"][0]["candidate_id"] == _WAY
    with pytest.raises(UnknownCandidateReference):
        resolve_day_refs(_output(["c1"], placement_ref="c9")["days"], ref_map)


# -- through the real adapter (fake client) -----------------------------------------------


def test_prompt_shows_references_and_never_a_provider_identity() -> None:
    candidates = [_candidate(_WAY, "Lincoln Memorial", 38.889), _candidate("openstreetmap_places:node/7", "A Museum", 38.9)]
    for module in (groq_adapter, anthropic_adapter):
        prompt = module._build_prompt(_request(candidates))
        assert "candidate_id='c1' name='Lincoln Memorial'" in prompt
        assert "candidate_id='c2' name='A Museum'" in prompt
        for leaked in ("1332068226", "way/", "node/", "openstreetmap_places", "38.889"):
            assert leaked not in prompt, leaked


def test_reference_resolves_to_the_exact_provider_identity_source_and_coordinates() -> None:
    candidates = [_candidate(_WAY, "Lincoln Memorial", 38.889), _candidate("openstreetmap_places:node/7", "A Museum", 38.9)]
    request = _request(candidates)
    result = GroqAIItineraryReasoningProvider(client=_Client(_output(["c1"]))).reason(request)

    assert result.status == AIItineraryReasoningStatus.COMPLETED
    assert result.days[0].candidate_ids == [_WAY]
    assert result.days[0].approximate_structure[0].candidate_id == _WAY
    # the identity, source and coordinates are the request's own, untouched by the model
    resolved = next(c for c in request.allowed_candidates if c.candidate_id == result.days[0].candidate_ids[0])
    assert (resolved.provider_name, resolved.provider_place_id) == ("openstreetmap_places", "way/1332068226")
    assert resolved.coordinates.lat == 38.889


def test_the_live_failure_mode_cannot_alter_identity() -> None:
    """Both a node and a way with the same number are allowed; the reference the
    model returns decides which one -- there is no string for it to mistype."""
    request = _request([_candidate(_NODE, "A Bench", 38.1), _candidate(_WAY, "Lincoln Memorial", 38.889)])
    result = GroqAIItineraryReasoningProvider(client=_Client(_output(["c2"]))).reason(request)
    assert result.status == AIItineraryReasoningStatus.COMPLETED
    assert result.days[0].candidate_ids == [_WAY]


@pytest.mark.parametrize("bad_ref", ["c3", _NODE, _WAY, "way/1332068226", "Lincoln Memorial"])
def test_out_of_set_or_raw_identity_output_is_rejected_not_corrected(bad_ref: str) -> None:
    """An allowed way/…; the model answers with node/…, a raw id, a name or an
    unknown reference: always a safe rejection, never a fuzzy match."""
    request = _request([_candidate(_WAY, "Lincoln Memorial", 38.889)])
    result = GroqAIItineraryReasoningProvider(client=_Client(_output([bad_ref]))).reason(request)
    assert result.status == AIItineraryReasoningStatus.REJECTED
    assert result.days == []
    assert result.guardrail_report.passed is False
    assert "outside the allowed set" in result.guardrail_report.blocked_reasons[0]


def test_unknown_reference_in_a_placement_also_rejects() -> None:
    request = _request([_candidate(_WAY, "Lincoln Memorial", 38.889)])
    result = GroqAIItineraryReasoningProvider(client=_Client(_output(["c1"], placement_ref="c2"))).reason(request)
    assert result.status == AIItineraryReasoningStatus.REJECTED


def test_the_original_allowed_set_guardrail_still_runs_after_resolution() -> None:
    """A reference repeated on two days resolves fine but must still be caught
    by the unchanged `validate_result_against_request` (one candidate, one day)."""
    request = _request([_candidate(_WAY, "Lincoln Memorial", 38.889)])
    output = _output(["c1"])
    output["days"].append({**output["days"][0], "day_index": 2})
    result = GroqAIItineraryReasoningProvider(client=_Client(output)).reason(request)
    assert result.status == AIItineraryReasoningStatus.REJECTED


def test_repair_prompts_show_references_for_candidates_and_original_days() -> None:
    from app.models.ai_itinerary_reasoning import ItineraryReasoningDayPlan
    from app.models.ai_itinerary_repair import AIItineraryRepairIssue, AIItineraryRepairRequest, RepairableIssueType
    from app.models.common import ValidationSeverity

    candidates = [_candidate(_WAY, "Lincoln Memorial", 38.889), _candidate("openstreetmap_places:node/7", "A Museum", 38.9)]
    request = AIItineraryRepairRequest(
        trip_id="trip_001",
        destination_name="Washington, DC, USA",
        start_date="2026-10-11",
        end_date="2026-10-13",
        trip_duration_days=3,
        traveler_context=TravelerContextSummary(travelers_count=4, travel_group_type="family", pace="balanced"),
        allowed_candidates=candidates,
        original_days=[
            ItineraryReasoningDayPlan(
                day_index=1, candidate_ids=[_WAY, "openstreetmap_places:node/7"], rationale="Day one."
            )
        ],
        issues=[
            AIItineraryRepairIssue(
                issue_type=RepairableIssueType.GEOGRAPHIC_SPREAD, day_index=1, source_category="geographic_spread",
                severity=ValidationSeverity.WARNING, message="Spread out."
            )
        ],
        affected_days=[1],
    )
    for module in (groq_adapter, anthropic_adapter):
        prompt = module._build_repair_prompt(request)
        assert "candidate_ids=['c1', 'c2']" in prompt
        assert "candidate_id='c1' name='Lincoln Memorial'" in prompt
        for leaked in ("1332068226", "way/", "node/", "openstreetmap_places"):
            assert leaked not in prompt, leaked


def test_references_never_reach_the_traveler_in_model_written_prose() -> None:
    """Seen live: a repair summary read "Martinho da Arcada (c5) in the morning"."""
    request = _request([_candidate(_WAY, "Lincoln Memorial", 38.889), _candidate("openstreetmap_places:node/7", "A Museum", 38.9)])
    output = _output(["c1", "c2"])
    output["strategy"]["summary"] = "Start at Lincoln Memorial (c1), then c2."
    output["days"][0]["rationale"] = "c1 and c2 are close; c9 and the C1 gate are not references."
    output["days"][0]["tradeoffs"] = "Skips nothing (c2)."
    output["overall_tradeoffs"] = ["c2 is indoors."]

    result = GroqAIItineraryReasoningProvider(client=_Client(output)).reason(request)

    assert result.status == AIItineraryReasoningStatus.COMPLETED
    assert result.strategy.summary == "Start at Lincoln Memorial, then A Museum."
    assert result.days[0].rationale == "Lincoln Memorial and A Museum are close; c9 and the C1 gate are not references."
    assert result.days[0].tradeoffs == "Skips nothing."
    assert result.overall_tradeoffs == ["A Museum is indoors."]
    assert result.days[0].candidate_ids == [_WAY, "openstreetmap_places:node/7"]  # ids untouched by the scrub
