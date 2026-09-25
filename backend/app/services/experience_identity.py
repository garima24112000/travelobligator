from __future__ import annotations

import hashlib
from typing import Any

from app.models.ai_itinerary_reasoning import build_candidate_id

# Section 202B.1 (Tasks 5/6/10/11): ONE rule for the identity of a
# provider-grounded place inside an itinerary.
#
# Audit result (recorded so it is never re-derived): `ExperienceItem.
# experience_id` used to be a fresh random id on every construction, and
# `provider_place_id`/`provider_source` were only ever populated for
# AI-promoted candidates. So (a) every rerun of the experience planner gave
# every unchanged place a brand-new id, which the id-based diff/comparison
# then reported as a fake Removed + Added pair, and (b) every consumer that
# keys on provider identity (the targeted executor's reserved-candidate
# set and duplicate audit, the feedback-interpretation request builder,
# the plan builder's preserved ids) saw NOTHING for ordinary candidates --
# which is how a targeted regeneration could schedule the same place on
# three days without any check firing.
#
# The rule: the stable place key is the pair (provider source, provider
# place id), formatted by the existing single-source-of-truth
# `build_candidate_id`. Within one trip, an itinerary "occurrence" of a
# provider place and the place itself are the same thing (the product does
# not support repeated visits), so `experience_id` is derived
# deterministically from (trip_id, place key) whenever that key exists --
# the same grounded place keeps the same `experience_id` across
# regenerations and across branches of the same trip. Never derived from
# display names or coordinates (no fuzzy matching). An item with no
# provider identity keeps a random id and simply has no stable key.


def stable_place_key(provider_source: str | None, provider_place_id: str | None) -> str | None:
    if provider_source and provider_place_id:
        return build_candidate_id(str(provider_source), str(provider_place_id))
    return None


def experience_stable_key(experience: Any) -> str | None:
    """The stable place key of an `ExperienceItem` (or None)."""
    return stable_place_key(
        getattr(experience, "provider_source", None),
        getattr(experience, "provider_place_id", None),
    )


def deterministic_experience_id(
    trip_id: str, provider_source: str | None, provider_place_id: str | None
) -> str | None:
    key = stable_place_key(provider_source, provider_place_id)
    if key is None:
        return None
    digest = hashlib.sha256(f"{trip_id}|{key}".encode("utf-8")).hexdigest()[:32]
    return f"experience_{digest}"
