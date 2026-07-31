from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from app.models.ai_candidate_proposal import AICandidateProposal
from app.models.candidate_grounding import CandidateGroundingRequest, ProviderCandidateForGrounding
from app.models.common import DataStatus, GeoPoint
from app.models.planning_state import PlanningState

# Builder that bridges existing PlanningState destination-context candidates
# into a CandidateGroundingRequest (Step 159B, itinerary-generator-build-
# spec.md Stage 6, docs/13_llm_reasoning_pipeline.md section 33,
# docs/14_backend_architecture.md section 25). No LLM, provider call,
# LangGraph, or LangSmith dependency is wired up here. This module never
# calls a network service, never calls `CandidateGroundingService.ground`,
# and never mutates `PlanningState` -- `build_request` only reads it and
# returns a new `CandidateGroundingRequest`.
#
# `provider_candidates` on the returned request are converted only from
# candidates that already exist in `planning_state.destination_context`
# (`candidate_pois`, `candidate_restaurants`, `candidate_accommodation_pois`)
# -- this never performs a provider/open-data lookup of its own, and it
# never invents a name, coordinate, category, or confidence value.

_DEFAULT_BUILDER_CONFIDENCE = 0.5
# Conservative fallback when a supplied candidate dict has no explicit
# `data_status`. Real candidates produced by DestinationContextService
# always carry an explicit status (NormalizedPlace.data_status is
# required), so this only applies to malformed/partial candidate data --
# it deliberately does not assume `live` freshness it cannot confirm.
_DEFAULT_BUILDER_DATA_STATUS = DataStatus.UNAVAILABLE

_LEADING_ARTICLES = ("the ", "a ", "an ")

# (source_collection_attr, provider_candidate_summary_label, default_category)
_CANDIDATE_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("candidate_pois", "attraction", "attraction"),
    ("candidate_restaurants", "restaurant", "restaurant"),
    ("candidate_accommodation_pois", "accommodation", "accommodation"),
)


def _normalize_name(name: str) -> str:
    lowered = name.strip().lower()
    no_punctuation = re.sub(r"[^\w\s]", "", lowered)
    collapsed = re.sub(r"\s+", " ", no_punctuation).strip()
    for article in _LEADING_ARTICLES:
        if collapsed.startswith(article):
            collapsed = collapsed[len(article) :]
            break
    return collapsed


def _extract_coordinates(value: Any) -> GeoPoint | None:
    if isinstance(value, GeoPoint):
        return value
    if isinstance(value, dict):
        lat = value.get("lat")
        lng = value.get("lng")
        if lat is None or lng is None:
            return None
        try:
            return GeoPoint(lat=lat, lng=lng)
        except (ValidationError, TypeError, ValueError):
            return None
    return None


def _coerce_data_status(value: Any) -> DataStatus:
    if isinstance(value, DataStatus):
        return value
    if isinstance(value, str):
        try:
            return DataStatus(value)
        except ValueError:
            pass
    return _DEFAULT_BUILDER_DATA_STATUS


def _coerce_confidence(value: Any) -> float:
    if isinstance(value, bool):
        return _DEFAULT_BUILDER_CONFIDENCE
    if isinstance(value, (int, float)):
        confidence = float(value)
        if 0.0 <= confidence <= 1.0:
            return confidence
    return _DEFAULT_BUILDER_CONFIDENCE


class CandidateGroundingRequestBuilder:
    """Converts existing `PlanningState.destination_context` candidates
    into `ProviderCandidateForGrounding` entries and assembles a
    `CandidateGroundingRequest` from them plus caller-supplied proposals.

    This is deterministic, read-only assembly -- it does not ground
    anything, does not call `CandidateGroundingService`, does not call a
    provider or LLM, and does not mutate `PlanningState`.
    """

    def build_request(
        self,
        planning_state: PlanningState,
        proposals: list[AICandidateProposal],
    ) -> CandidateGroundingRequest:
        trip_id = planning_state.trip_id
        destination_name = planning_state.trip_request.primary_destination

        provider_candidates, summary_counts = self._build_provider_candidates(planning_state)
        unavailable_data = self._unavailable_data_from_state(planning_state)

        return CandidateGroundingRequest(
            trip_id=trip_id,
            destination_name=destination_name,
            proposals=list(proposals),
            provider_candidates=provider_candidates,
            provider_candidate_summary=summary_counts,
            unavailable_data=unavailable_data,
        )

    def _build_provider_candidates(
        self, planning_state: PlanningState
    ) -> tuple[list[ProviderCandidateForGrounding], dict[str, int]]:
        destination_context = planning_state.destination_context
        summary_counts: dict[str, int] = {label: 0 for _, label, _ in _CANDIDATE_SOURCES}

        if destination_context is None:
            return [], summary_counts

        provider_candidates: list[ProviderCandidateForGrounding] = []
        seen_keys: set[tuple[str, str, str, tuple[float, float]]] = set()

        for attr_name, summary_label, default_category in _CANDIDATE_SOURCES:
            source_candidates = getattr(destination_context, attr_name, None) or []
            for index, raw_candidate in enumerate(source_candidates):
                if not isinstance(raw_candidate, dict):
                    continue

                candidate = self._convert_candidate(
                    raw_candidate,
                    default_category=default_category,
                    source_label=attr_name,
                    index=index,
                )
                if candidate is None:
                    continue

                key = (
                    _normalize_name(candidate.provider_name),
                    candidate.provider_place_id,
                    _normalize_name(candidate.name),
                    (candidate.coordinates.lat, candidate.coordinates.lng),
                )
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                provider_candidates.append(candidate)
                summary_counts[summary_label] += 1

        return provider_candidates, summary_counts

    def _convert_candidate(
        self,
        raw_candidate: dict[str, Any],
        *,
        default_category: str,
        source_label: str,
        index: int,
    ) -> ProviderCandidateForGrounding | None:
        name = raw_candidate.get("name")
        if not isinstance(name, str) or not name.strip():
            return None

        coordinates = _extract_coordinates(raw_candidate.get("coordinates"))
        if coordinates is None:
            return None

        # `provider_name` documents where this candidate came from. If the
        # candidate dict carries no explicit provider/source field, fall
        # back to the internal label "destination_context" -- this is
        # never a claim that a new provider was searched, only a marker
        # that the candidate was read from PlanningState.destination_context.
        provider_name = (
            raw_candidate.get("provider_name")
            or raw_candidate.get("source")
            or raw_candidate.get("provider")
            or raw_candidate.get("source_provider")
        )
        provider_name = str(provider_name).strip() if provider_name else ""
        if not provider_name:
            provider_name = "destination_context"

        # `provider_place_id` should trace back to a real provider id when
        # one exists. If not, fall back to a deterministic internal
        # reference into PlanningState itself (collection + index) --
        # never a newly invented provider fact.
        provider_place_id = (
            raw_candidate.get("provider_place_id")
            or raw_candidate.get("place_id")
            or raw_candidate.get("osm_id")
            or raw_candidate.get("source_id")
        )
        provider_place_id = str(provider_place_id).strip() if provider_place_id else ""
        if not provider_place_id:
            provider_place_id = f"destination_context.{source_label}[{index}]"

        category = raw_candidate.get("category")
        category = category.strip() if isinstance(category, str) and category.strip() else default_category

        data_status = _coerce_data_status(raw_candidate.get("data_status"))
        confidence = _coerce_confidence(raw_candidate.get("confidence"))

        try:
            return ProviderCandidateForGrounding(
                provider_name=provider_name,
                provider_place_id=provider_place_id,
                name=name.strip(),
                category=category,
                coordinates=coordinates,
                data_status=data_status,
                confidence=confidence,
            )
        except ValidationError:
            return None

    def _unavailable_data_from_state(self, planning_state: PlanningState) -> list[str]:
        seen: set[str] = set()
        fields: list[str] = []
        for item in planning_state.unavailable_data:
            field_name = getattr(item, "field", None)
            if not field_name or field_name in seen:
                continue
            seen.add(field_name)
            fields.append(field_name)
        return fields
