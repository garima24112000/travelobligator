"""Blinded human-review packets for the Final Batch 3 acceptance set (evaluation tooling only).

Two jobs, both offline:

  * `traveler_projection(state)` -- taken by `quality_acceptance_v3.py` while
    the set runs, once per scenario, from the stored final `PlanningState`.
    It keeps only what the traveller is shown with the plan (places and their
    English names, estimated visit times, opening-hours status, travel legs,
    day notes and narratives, the plan's own findings, must-visit and
    interest status, nearby food). It keeps NO provider id, provider or model
    name, quality tier, evidence band, prompt, URL or raw model output.
  * `build_review_materials(...)` -- after the run, writes two separate
    trees. `reviewer_bundle/` is everything the reviewer receives before
    scoring: one packet per scenario in a fixed shuffled order (codes P01,
    P02, ...), the empty scoresheet and the reviewer's instructions -- no
    scenario id, no automated verdict, no Part B figure. `operator/` is held
    back by the operator: the mapping from packet code to scenario id, and
    the automated verdict and Part B figures of each plan, released to the
    reviewer only after the whole scoresheet has been submitted and locked.

The rubric, scale and rules are copied from
`docs/24_itinerary_quality_contract.md` section 6; nothing here scores a
plan. Nothing here is imported by `backend/app`, and nothing here calls a
provider or a model.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

PROJECTION_SCHEMA_VERSION = 1

# Verbatim from docs/24_itinerary_quality_contract.md section 6 (field name, dimension, question).
RUBRIC: tuple[tuple[str, str, str], ...] = (
    ("attraction_usefulness", "Attraction usefulness", "Are these places a visitor would be glad to have spent a slot on?"),
    ("geographic_day_coherence", "Geographic and day coherence", "Does each day hold together on a map and as an experience?"),
    ("personalization", "Personalization", "Does the plan reflect the stated interests and must-visits?"),
    ("pace_realism", "Pace realism", "Could the day be done at the stated pace, travel included?"),
    ("redundancy_avoidance", "Redundancy avoidance", "Is every slot a distinct experience?"),
    ("overall_would_use", "Overall", "Would I actually use this plan?"),
)
SCALE = "1 unusable, 2 poor, 3 acceptable with edits, 4 good, 5 excellent."

# The scoresheet's columns, in order. One row per packet.
SCORESHEET_COLUMNS: tuple[str, ...] = (
    "packet",
    *(field for field, _, _ in RUBRIC),
    "verified_factual_errors",
    "part_b_read_after_scoring",
    "reviewer",
    "scored_on",
)

# The two trees of the review material (see the module docstring).
BUNDLE_DIR = "reviewer_bundle"
OPERATOR_DIR = "operator"

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _value(item: Any) -> Any:
    return getattr(item, "value", item)


# -- traveller-view projection (taken during the run) --------------------------------------------


def _findings(validation: Any) -> list[dict[str, Any]]:
    if validation is None:
        return []
    classification = dict(validation.review_code_classification or {})
    found: list[dict[str, Any]] = []
    for issues in (validation.critical_issues, validation.warnings, validation.suggestions):
        for issue in issues:
            found.append(
                {
                    "severity": _value(issue.severity),
                    "category": issue.category,
                    # `None` when the report carries no classification: presented as material, as Traveler view does
                    "classification": classification.get(issue.category),
                    "message": issue.message,
                }
            )
    return found


def traveler_projection(state: Any) -> dict[str, Any]:
    """What the traveller is shown with this plan, as plain data (see the module docstring)."""
    request = state.trip_request
    validation = state.validation_report
    narrative = state.itinerary_narrative_report
    feasibility = state.route_feasibility_report
    legs = feasibility.legs if feasibility is not None else []
    leg_by_pair = {(leg.from_experience_id, leg.to_experience_id): leg for leg in legs}
    venue_by_id = {entry.experience_id: entry for entry in (validation.venue_evidence or [])} if validation else {}
    narratives = {day.day_number: day for day in (narrative.daily_narratives if narrative is not None else [])}
    plan = state.experience_plan

    days: list[dict[str, Any]] = []
    for day in plan.daily_plans if plan is not None else []:
        stops = day.experiences
        day_legs = []
        for a, b in zip(stops, stops[1:]):
            leg = leg_by_pair.get((a.experience_id, b.experience_id))
            verified = leg is not None and leg.distance_meters is not None and leg.duration_seconds is not None
            day_legs.append(
                {
                    "from": a.name,
                    "to": b.name,
                    "verified": verified,
                    # a leg stored without a mode is a walk; an unverified leg states no mode at all
                    "mode": (leg.mode or "walk") if verified else None,
                    "minutes": round(leg.duration_seconds / 60) if verified else None,
                    "km": round(leg.distance_meters / 1000, 1) if verified else None,
                    "includes_ferry": leg.includes_ferry if leg is not None else None,
                }
            )
        told = narratives.get(day.day_number)
        schedule = day.visit_schedule
        days.append(
            {
                "day_number": day.day_number,
                "date": day.date.isoformat(),
                "weekday": _WEEKDAYS[day.date.weekday()],
                "theme": day.theme,
                "goal": day.goal,
                "schedule": (
                    {
                        "window_start": schedule.window_start,
                        "window_end": schedule.window_end,
                        "status": schedule.status,
                        "ends_at": schedule.ends_at,
                        "exceeds_window": schedule.exceeds_window,
                    }
                    if schedule is not None
                    else None
                ),
                "warnings": list(day.warnings),
                "assumptions": list(day.assumptions),
                "narrative": (
                    {"title": told.title, "text": told.narrative, "caveats": list(told.caveats)} if told else None
                ),
                "stops": [
                    {
                        "name": stop.name,
                        "display_name": stop.display_name,
                        "category": stop.category,
                        "start_time": stop.start_time,
                        "end_time": stop.end_time,
                        "estimated_duration_minutes": stop.estimated_duration_minutes,
                        "visit_time_status": stop.visit_time_status,
                        "arrival_wait_minutes": stop.arrival_wait_minutes,
                        "venue_status": venue_by_id[stop.experience_id].status if stop.experience_id in venue_by_id else None,
                        "hours_on_day": list(venue_by_id[stop.experience_id].hours_on_day) if stop.experience_id in venue_by_id else [],
                        "lat": round(stop.coordinates.lat, 5) if stop.coordinates is not None else None,
                        "lon": round(stop.coordinates.lng, 5) if stop.coordinates is not None else None,
                    }
                    for stop in stops
                ],
                "legs": day_legs,
                "nearby_food": [suggestion.name for suggestion in day.restaurant_suggestions],
            }
        )

    return {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "request": {
            "destination": request.primary_destination,
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "pace": _value(request.pace),
            "interests": list(request.interests),
            "must_visit": list(request.must_visit),
            "travelers": request.travelers_count,
        },
        "resolved_destination": state.destination_context.destination_name if state.destination_context else None,
        "readiness": _value(validation.readiness_status) if validation is not None else None,
        "findings": _findings(validation),
        "interest_coverage": (
            [
                {"interest": entry.interest, "requested_as": list(entry.requested_as), "status": entry.status}
                for entry in validation.interest_coverage
            ]
            if validation is not None and validation.interest_coverage is not None
            else None
        ),
        "must_visit_status": [
            {
                "term": entry.term,
                "status": entry.status,
                "place_name": entry.place_name,
                "day_number": entry.day_number,
                "distance_km": entry.distance_km,
                "drive_minutes": entry.drive_minutes,
            }
            for entry in ((validation.must_visit_status or []) if validation is not None else [])
        ],
        "summary": narrative.summary if narrative is not None else None,
        # Traveler view's own line under the summary (it says where the summary came from)
        "summary_from_itinerary": bool(narrative is not None and narrative.narrative_source == "deterministic_fallback"),
        "getting_around_advisory": (narrative.getting_around_advisory or None) if narrative is not None else None,
        "days": days,
    }


# -- presentation order and packets (after the run) ------------------------------------------------


def presentation_order(scenario_ids: list[str], seed: str) -> list[str]:
    """A fixed shuffle: by SHA-256 of `seed:id`. Reproducible, and unrelated to the set's file order."""
    return sorted(scenario_ids, key=lambda scenario_id: hashlib.sha256(f"{seed}:{scenario_id}".encode()).hexdigest())


def packet_codes(scenario_ids: list[str], seed: str) -> dict[str, str]:
    """Packet code (P01, P02, ...) -> scenario id, in presentation order."""
    return {f"P{index:02d}": scenario_id for index, scenario_id in enumerate(presentation_order(scenario_ids, seed), 1)}


def _place_name(stop: dict[str, Any]) -> str:
    """Traveler view's rule: the English name with the original after it, never translated."""
    english = (stop.get("display_name") or "").strip()
    return f"{english} ({stop['name']})" if english and english != stop["name"] else stop["name"]


def _words(code: Any) -> str:
    return str(code).replace("_", " ") if code else "—"


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ") if text not in (None, "") else "—"


def render_packet(code: str, scenario: dict[str, Any], projection: dict[str, Any] | None) -> str:
    """One blinded packet: the request and the plan as the traveller receives it, then empty score cells."""
    lines = [f"# Review packet {code}", ""]
    request = (projection or {}).get("request") or {}
    lines += [
        "## Request",
        "",
        f"- Destination: {scenario['destination']}",
        f"- Length and pace: {scenario['trip_days']} days, {scenario['pace']}",
        f"- Interests: {', '.join(scenario['interests'])}",
        f"- Must-visits: {'; '.join(scenario['must_visit']) or 'none'}",
        f"- Travellers: {request.get('travelers', 2)}, as a couple",
        f"- Dates: {request.get('start_date', '—')} to {request.get('end_date', '—')}",
        "",
    ]
    if projection is None or not projection.get("days"):
        lines += [
            "## Plan",
            "",
            "**No itinerary was produced for this request.** Score it as the traveller would receive it: with no plan.",
            "",
        ]
    else:
        lines += _render_plan(projection)
    lines += _render_score_block(code)
    return "\n".join(lines) + "\n"


def _render_plan(projection: dict[str, Any]) -> list[str]:
    lines = ["## About this plan", ""]
    lines.append(f"- Destination as found: {projection.get('resolved_destination') or '—'}")
    lines.append(f"- Plan status: {_words(projection.get('readiness'))}")
    if projection.get("summary"):
        lines += ["", "**Summary**", "", projection["summary"]]
        lines.append("")
        lines.append(
            "_Summary put together directly from your itinerary._" if projection.get("summary_from_itinerary")
            else "_AI-written summary of the plan below. It adds no new facts, and nothing is booked._"
        )
    if projection.get("getting_around_advisory"):
        lines += ["", "**Getting around**", "", projection["getting_around_advisory"]]
    lines.append("")

    must_visits = projection.get("must_visit_status") or []
    if must_visits:
        lines += ["**Must-visits**", "", "| Requested | Status | Place | Day | Distance | Road time |", "| --- | --- | --- | ---: | ---: | ---: |"]
        for entry in must_visits:
            distance = None if entry.get("distance_km") is None else "%d km" % round(entry["distance_km"])
            drive = None if entry.get("drive_minutes") is None else "%d min" % entry["drive_minutes"]
            lines.append(
                f"| {_cell(entry['term'])} | {_words(entry['status'])} | {_cell(entry.get('place_name'))} | "
                f"{_cell(entry.get('day_number'))} | {_cell(distance)} | {_cell(drive)} |"
            )
        lines.append("")
    coverage = projection.get("interest_coverage")
    if coverage is not None:
        lines += ["**Requested interests**", "", "| Interest | Asked as | Status |", "| --- | --- | --- |"]
        for entry in coverage:
            lines.append(f"| {_cell(entry['interest'])} | {_cell(', '.join(entry['requested_as']))} | {_words(entry['status'])} |")
        lines.append("")
    findings = projection.get("findings") or []
    if findings:
        lines += ["**What the plan says needs checking**", ""]
        for finding in findings:
            weight = "material" if finding.get("classification") in (None, "material") else "informational"
            lines.append(f"- [{finding['severity']}, {weight}] {finding['message']}")
        lines.append("")

    for day in projection["days"]:
        lines += [f"## Day {day['day_number']} — {day['weekday']} {day['date']}", ""]
        heading = day.get("narrative") or {}
        if heading.get("title"):
            lines += [f"**{heading['title']}**", ""]
        if heading.get("text"):
            lines += [heading["text"], ""]
        schedule = day.get("schedule")
        if schedule:
            note = (
                f"Times are estimates for a day from {schedule['window_start']} to {schedule['window_end']}: typical "
                "visit lengths, each place's listed hours where it has them, and checked travel times only."
            )
            if schedule.get("status") == "partly_timed":
                note += " Some stops have no time because the travel to them could not be checked."
            if schedule.get("exceeds_window") and schedule.get("ends_at"):
                note += f" This day may run until about {schedule['ends_at']}."
            lines += [f"_{note}_", ""]
        lines += ["| # | Place | Kind | Estimated visit | Opening hours that day | Map |", "| ---: | --- | --- | --- | --- | --- |"]
        for index, stop in enumerate(day["stops"], 1):
            if stop.get("visit_time_status") in (None, "not_timed") or not stop.get("start_time"):
                visit = "no time estimated" if stop.get("visit_time_status") else "—"
            else:
                visit = f"about {stop['start_time']}–{stop['end_time']} ({stop.get('estimated_duration_minutes') or '?'} min)"
                if stop.get("arrival_wait_minutes"):
                    visit += f", after waiting about {stop['arrival_wait_minutes']} min for opening"
                if stop["visit_time_status"] == "outside_listed_hours":
                    visit += "; listed hours leave no time then"
            hours = _words(stop.get("venue_status"))
            if stop.get("hours_on_day"):
                hours += f" ({', '.join(stop['hours_on_day'])})"
            where = (
                f"[{stop['lat']}, {stop['lon']}](https://www.openstreetmap.org/?mlat={stop['lat']}&mlon={stop['lon']}#map=17/{stop['lat']}/{stop['lon']})"
                if stop.get("lat") is not None and stop.get("lon") is not None
                else "—"
            )
            lines.append(f"| {index} | {_cell(_place_name(stop))} | {_cell(stop.get('category'))} | {_cell(visit)} | {_cell(hours)} | {where} |")
        lines.append("")
        if day["legs"]:
            lines += ["| Travel | How | Time | Distance |", "| --- | --- | ---: | ---: |"]
            for leg in day["legs"]:
                if leg["verified"]:
                    how = "ferry and " + leg["mode"] if leg.get("includes_ferry") is True else leg["mode"]
                    lines.append(f"| {_cell(leg['from'])} → {_cell(leg['to'])} | {how} | {leg['minutes']} min | {leg['km']} km |")
                else:
                    lines.append(f"| {_cell(leg['from'])} → {_cell(leg['to'])} | no checked route | — | — |")
            lines.append("")
        if day.get("nearby_food"):
            lines += [f"Nearby food: {'; '.join(day['nearby_food'])}.", ""]
        for warning in day.get("warnings") or []:
            lines.append(f"> {warning}")
        for caveat in heading.get("caveats") or []:
            lines.append(f"> {caveat}")
        if day.get("warnings") or heading.get("caveats"):
            lines.append("")
    return lines


def _render_score_block(code: str) -> list[str]:
    lines = [
        "## Scores (whole numbers 1–5; copy each into the scoresheet row for " + code + ")",
        "",
        "| # | Dimension | Question | Score | Evidence |",
        "| ---: | --- | --- | :---: | --- |",
    ]
    for index, (_, dimension, question) in enumerate(RUBRIC, 1):
        lines.append(f"| {index} | {dimension} | {question} |   |   |")
    lines += [
        "",
        f"Scale: {SCALE}",
        "",
        "**Verified factual errors** (each scheduled place you checked against an official or primary source and found "
        "permanently or temporarily closed on that date, relocated, not existing, not open to visitors, or located "
        "materially away from where the plan puts it). Count: ___",
        "",
        "| Place | What the plan implies | What you found | Source and date checked |",
        "| --- | --- | --- | --- |",
        "|   |   |   |   |",
        "",
        "Other comments:",
        "",
        "Scored by: ________  Date: ________",
        "",
        "_The automated checks and figures for this plan are released only after the whole scoresheet is submitted._",
        "",
    ]
    return lines


def issued_part(text: str) -> str:
    """The part of a packet the reviewer does not write in: everything before the score block, with trailing
    whitespace ignored. A returned packet must carry exactly the issued plan under its code."""
    head = text.split("\n## Scores", 1)[0]
    return "\n".join(line.rstrip() for line in head.strip().splitlines())


def render_after_scoring(code: str, record: dict[str, Any]) -> str:
    """The automated verdict and Part B figures of one plan. Read only after its packet is scored."""
    baseline = record.get("baseline_hard_correctness") or {}
    lines = [
        f"# {code} — after scoring only",
        "",
        f"Scenario: {record.get('id')} ({(record.get('scenario') or {}).get('destination')})",
        f"Generation: {record.get('status')} (technical failure: {record.get('technical_failure') or 'none'})",
        f"Baseline hard correctness: {baseline.get('outcome')}",
        f"Failed checks: {', '.join(baseline.get('failed_checks') or []) or 'none'}",
        f"Quality figures: {(record.get('quality_metrics') or {}).get('status')}",
        "",
        "## Part B figures (as recorded)",
        "",
        "```json",
        json.dumps(record.get("itinerary_quality"), ensure_ascii=False, indent=1, sort_keys=True),
        "```",
        "",
    ]
    return "\n".join(lines)


def empty_scoresheet(codes: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(SCORESHEET_COLUMNS)
    for code in codes:
        writer.writerow([code, *([""] * (len(SCORESHEET_COLUMNS) - 1))])
    return buffer.getvalue()


def build_review_materials(
    *,
    scenarios: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    projections: dict[str, dict[str, Any] | None],
    seed: str,
    out_dir: Path,
    instructions: str,
) -> dict[str, str]:
    """Writes `reviewer_bundle/` (packets, empty scoresheet, instructions) and `operator/` (the mapping and the
    after-scoring files) under `out_dir`. Returns code -> scenario id."""
    by_id = {scenario["id"]: scenario for scenario in scenarios}
    codes = packet_codes(list(by_id), seed)
    bundle, operator = out_dir / BUNDLE_DIR, out_dir / OPERATOR_DIR
    (bundle / "packets").mkdir(parents=True, exist_ok=False)
    (operator / "after_scoring").mkdir(parents=True, exist_ok=False)
    for code, scenario_id in codes.items():
        (bundle / "packets" / f"{code}.md").write_text(
            render_packet(code, by_id[scenario_id], projections.get(scenario_id)), encoding="utf-8"
        )
        if scenario_id in records:
            (operator / "after_scoring" / f"{code}.md").write_text(
                render_after_scoring(code, records[scenario_id]), encoding="utf-8"
            )
    (bundle / "scoresheet.csv").write_text(empty_scoresheet(list(codes)), encoding="utf-8")
    (bundle / "REVIEWER_INSTRUCTIONS.md").write_text(instructions, encoding="utf-8")
    (operator / "unblinding.json").write_text(json.dumps(codes, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return codes
