#!/usr/bin/env python3
"""MANUAL-ONLY dev smoke test -- Step 161C.

This script is not part of the application runtime and is never imported by
`app.main`, any service, or the automated test suite. It exists purely so a
developer can, by hand, confirm that the Anthropic/Claude-backed AI
candidate proposal adapter (Step 161A) can run end to end in the
config-gated shadow-mode integration (Step 161B) without changing what the
itinerary actually contains.

WARNING: running this script with a real ANTHROPIC_API_KEY calls the real
Anthropic API and may incur cost. It is never invoked by pytest, by
`python -m compileall`, or by normal `uvicorn`/app startup -- it only runs
when a human explicitly executes this file.

Claude Code (the CLI coding agent) is not used here or anywhere in the
backend -- this script, like `AnthropicAICandidateProposalProvider`, only
ever talks to the Anthropic Messages API through the official `anthropic`
Python SDK, via the normal HTTP API surface of this app.

Required environment variables (all three, or this script exits without
calling anything):
    ANTHROPIC_API_KEY
    AI_CANDIDATE_PROPOSAL_PROVIDER=anthropic
    AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=true

Optional:
    ANTHROPIC_MODEL (defaults to Settings.anthropic_model)

Run from the repo root:
    ANTHROPIC_API_KEY=sk-ant-... \\
    AI_CANDIDATE_PROPOSAL_PROVIDER=anthropic \\
    AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=true \\
    python backend/scripts/manual_anthropic_shadow_smoke.py

See docs/20_manual_anthropic_shadow_smoke.md for what a PASS/FAIL result
does and does not mean.

Output safety: this script only ever prints a short summary (trip id,
batch statuses/counts, a final PASS/FAIL line). It never prints the raw
Claude API response, the raw prompt sent to Claude, or any API key/secret,
and it never writes any file containing either of those.
"""

from __future__ import annotations

import os
import sys

_FORBIDDEN_PROPOSAL_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "ticket_price",
    "availability",
    "safety_score",
    "coordinates",
    "provider_name",
    "provider_place_id",
}

_FORBIDDEN_GROUNDING_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "ticket_price",
    "availability",
    "safety_score",
}

_AI_SOURCE_SUBSTRINGS = ("ai_candidate_proposal", "candidate_grounding", "anthropic")

_ACCEPTED_PROPOSAL_STATUSES = {"completed", "rejected", "not_connected"}


def _exit_gracefully(message: str) -> None:
    print(f"[manual-smoke] {message}")
    print("[manual-smoke] Exiting without calling the Anthropic API.")
    sys.exit(0)


def _collect_keys(value: object, keys: set[str]) -> None:
    if isinstance(value, dict):
        keys.update(value.keys())
        for nested in value.values():
            _collect_keys(nested, keys)
    elif isinstance(value, list):
        for item in value:
            _collect_keys(item, keys)


def _check_guardrails() -> bool:
    """Reads only the three required env vars (never any other secret) and
    returns True if every guard passes, or False if this script should
    exit gracefully having already printed why. Never returns or exposes
    the API key value itself -- only whether it is present.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _exit_gracefully(
            "ANTHROPIC_API_KEY is not set. This is required to run this "
            "manual smoke test; the app itself never requires it."
        )
        return False

    provider = os.environ.get("AI_CANDIDATE_PROPOSAL_PROVIDER")
    if provider != "anthropic":
        _exit_gracefully(
            "AI_CANDIDATE_PROPOSAL_PROVIDER is not set to 'anthropic' "
            f"(got {provider!r}). Set AI_CANDIDATE_PROPOSAL_PROVIDER=anthropic "
            "to run this manual smoke test."
        )
        return False

    shadow_flag = os.environ.get("AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED", "")
    if shadow_flag.strip().lower() not in ("1", "true", "yes"):
        _exit_gracefully(
            "AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED is not enabled "
            f"(got {shadow_flag!r}). Set "
            "AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=true to run this "
            "manual smoke test."
        )
        return False

    return True


def _run_smoke_test() -> bool:
    """Runs the smoke test through the app's normal HTTP surface (FastAPI
    TestClient), the same path `POST /trips` and `POST /trips/{id}/generate`
    take in production -- no separate/parallel code path is added. The
    Anthropic API key itself is read only by `Settings` (via the normal
    `ANTHROPIC_API_KEY` env var), never handled directly in this function.
    Returns True on PASS, False on FAIL.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)

    checks: list[tuple[str, bool]] = []

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Lisbon, Portugal",
            "origin_city": "New York",
            "start_date": "2026-09-10",
            "end_date": "2026-09-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "interests": ["local food", "walkable neighborhoods"],
        },
    )
    if create_response.status_code != 201:
        print(f"[manual-smoke] Trip creation failed with status {create_response.status_code}.")
        return False

    trip_id = create_response.json()["data"]["trip_id"]
    print(f"trip_id: {trip_id}")

    generate_response = client.post(f"/trips/{trip_id}/generate")
    generation_succeeded = generate_response.status_code == 200 and generate_response.json().get(
        "success"
    ) is True
    checks.append(("generation returns success", generation_succeeded))
    if not generation_succeeded:
        _print_final(checks)
        return False

    planning_state = generate_response.json()["data"]["planning_state"]

    proposal_batch = planning_state.get("ai_candidate_proposal_batch")
    grounding_batch = planning_state.get("candidate_grounding_batch")
    experience_plan = planning_state.get("experience_plan")

    checks.append(("ai_candidate_proposal_batch is not None", proposal_batch is not None))
    checks.append(("candidate_grounding_batch is not None", grounding_batch is not None))
    checks.append(("experience_plan is not None", experience_plan is not None))

    proposal_status = None
    proposal_count = 0
    if proposal_batch is not None:
        proposal_result = proposal_batch.get("result") or {}
        proposal_status = proposal_result.get("status")
        proposals = proposal_result.get("proposals") or []
        proposal_count = len(proposals)

        checks.append(
            ("proposal batch status is completed/rejected/not_connected", proposal_status in _ACCEPTED_PROPOSAL_STATUSES)
        )
        if proposal_status == "completed":
            checks.append(("completed proposal batch has non-empty proposals", proposal_count > 0))

        proposal_keys: set[str] = set()
        _collect_keys(proposals, proposal_keys)
        forbidden_in_proposals = proposal_keys & _FORBIDDEN_PROPOSAL_FIELD_NAMES
        checks.append(
            (
                "proposal objects contain no forbidden factual fields",
                forbidden_in_proposals == set(),
            )
        )

    grounding_status = None
    grounded_count = 0
    rejected_count = 0
    if grounding_batch is not None:
        grounding_result = grounding_batch.get("result") or {}
        grounding_status = grounding_result.get("status")
        grounded_count = len(grounding_result.get("grounded_candidates") or [])
        rejected_count = len(grounding_result.get("rejected_proposals") or [])

        grounding_keys: set[str] = set()
        _collect_keys(grounding_batch, grounding_keys)
        forbidden_in_grounding = grounding_keys & _FORBIDDEN_GROUNDING_FIELD_NAMES
        checks.append(
            (
                "grounding batch contains no forbidden factual fields",
                forbidden_in_grounding == set(),
            )
        )
        checks.append(("grounding batch has a validated guardrail_report", "guardrail_report" in grounding_result))

    scheduled_experience_count = 0
    if experience_plan is not None:
        all_experiences = [
            experience
            for day_plan in experience_plan.get("daily_plans", [])
            for experience in day_plan.get("experiences", [])
        ]
        scheduled_experience_count = len(all_experiences)
        scheduled_names = {experience.get("name") for experience in all_experiences}

        proposal_names = set()
        if proposal_batch is not None:
            proposal_names = {
                proposal.get("candidate_name")
                for proposal in (proposal_batch.get("result") or {}).get("proposals") or []
            }
        no_direct_overlap = not (scheduled_names & proposal_names)

        import json as _json

        dump_text = _json.dumps(experience_plan)
        no_ai_markers_in_plan = not any(marker in dump_text for marker in _AI_SOURCE_SUBSTRINGS)

        checks.append(
            (
                "no scheduled experience sourced from ai_candidate_proposal_batch/candidate_grounding_batch",
                no_direct_overlap and no_ai_markers_in_plan,
            )
        )

    provider_coverage = planning_state.get("provider_coverage") or {}
    data_sources_used = planning_state.get("data_sources_used") or []
    coverage_text = str(provider_coverage) + str(data_sources_used)
    no_ai_sources_added = not any(
        substring in coverage_text.lower() for substring in _AI_SOURCE_SUBSTRINGS
    )
    checks.append(
        ("provider_coverage/data_sources_used has no Anthropic/AI source names", no_ai_sources_added)
    )

    print(f"proposal_status: {proposal_status}")
    print(f"proposal_count: {proposal_count}")
    print(f"grounding_status: {grounding_status}")
    print(f"grounded_count: {grounded_count}")
    print(f"rejected_count: {rejected_count}")
    print(f"scheduled_experience_count: {scheduled_experience_count}")

    return _print_final(checks)


def _print_final(checks: list[tuple[str, bool]]) -> bool:
    all_passed = all(passed for _, passed in checks)
    for label, passed in checks:
        print(f"  [{'ok' if passed else 'FAIL'}] {label}")
    print(f"RESULT: {'PASS' if all_passed else 'FAIL'}")
    return all_passed


def main() -> int:
    if not _check_guardrails():
        return 0

    try:
        passed = _run_smoke_test()
    except Exception:
        # Never let a raw exception (which could echo request/response
        # details) reach stdout -- only a generic failure line is printed.
        print("[manual-smoke] Smoke test raised an unexpected exception.")
        print("RESULT: FAIL")
        return 1

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
