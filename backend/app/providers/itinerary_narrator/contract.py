from __future__ import annotations

from app.models.itinerary_narrative import ItineraryNarrativeRequest

# Section 202B.3 (Tasks 1-3): the narrator's factual boundary, in ONE place
# shared by every narrator adapter.
#
# Audit of 202A: the prompt asked for "polished, traveler-facing prose" and
# also handed the model free text that invites embellishment -- the
# planner's own `why_included` sentence, LLM #2's rationale prose and
# strategy summary ("context only"), plus restaurant names. Successful
# runs therefore contained claims no input field supports ("a popular
# spot on the waterfront", "Fado music", "a touch of modern design",
# "immersive exhibits"). Growing a forbidden-word list cannot fix that, so
# the contract changed instead: the narrator is a SUMMARIZER of supplied
# structured fields, and the prompt contains only those fields.
#
# Facts the narrator may state (all present in the request):
#   - which scheduled place names are on which day, and in what order
#   - that a place serves a requested interest, ONLY when its own line
#     lists that interest (a provider-derived match)
#   - the provider-derived category label printed next to a place
#   - whether movement/route data is available for a day (yes/no)
#   - validation counts and the listed unavailable data fields
# Everything else -- popularity, atmosphere, scenery, quality judgments,
# history, what can be seen or done, cuisine, hours, prices, ratings,
# availability, safety, booking -- is not in the input and must not appear.
#
# The one deliberate exception is `getting_around_advisory`: a separate
# output field (never part of summary/day prose) holding 1-2 sentences of
# general guidance on how visitors usually get around the destination. It
# is general knowledge, not input data, so the service sanitizes it on its
# own and drops it (never the narrative) on any violation.
#
# Live canary (1/6 cities): the first wording ended with "Use an empty string
# if you are not confident" after a prompt that forbids every new fact, so
# the model resolved the conflict by leaving the field empty for well-known
# cities. The instructions now state the exemption explicitly and allow an
# empty string only for an ambiguous/unrecognized destination.
#
# Live canary (6/6, but templated): every city got "public transit and
# walking ... taxis for longer trips". The model now first picks a
# `getting_around_profile` (an enum, emitted before the advisory) and must
# write the advisory for that profile, with an explicit anti-default rule.

GETTING_AROUND_ADVISORY_INSTRUCTIONS = (
    "\n\nGetting-around advisory -- the ONE exception to the summarizer rules above. The rules "
    "above (no new facts, no describing places or areas) apply to summary, daily_narratives, "
    "caveats, assumptions and warnings only. The getting_around_profile and "
    "getting_around_advisory fields are different: they are expected general travel guidance "
    "from your own knowledge about the destination city, not itinerary data. For any "
    "recognized destination city, ALWAYS write both.\n"
    "Step 1 -- getting_around_profile. Decide which broad visitor transport pattern fits THIS "
    "destination, from your general knowledge of it:\n"
    "  transit_walk: public transit plus walking is normally the main practical combination.\n"
    "  rail_walk: metro/subway/local rail plus walking is especially dominant.\n"
    "  taxi_driver_walk: taxis/cabs, auto-rickshaw-style hired transport or a car with driver are "
    "often more practical for longer sightseeing movements, with walking in compact areas.\n"
    "  car_rideshare: a rental/self-drive car or taxis/rideshare are often more practical for "
    "dispersed trips; transit may still help in suitable areas.\n"
    "  mixed: no single mode clearly dominates; different areas suit different modes.\n"
    "Do NOT automatically choose transit_walk or public transit plus walking. Choose the profile "
    "the destination actually fits; cities that genuinely share a pattern may share a profile, "
    "but different cities often do not.\n"
    "Step 2 -- getting_around_advisory, written FOR the chosen profile: name ONE primary transport "
    "pattern, optionally one or two secondary modes, and say in a few words when each is useful. "
    "One sentence (two only if needed), at most 320 characters, hedged wording such as "
    "'generally practical', 'often convenient', 'can be useful' or 'commonly suitable'. Generic "
    "modes only: walking, public transit, metro/subway, buses, local trains, "
    "taxis/cabs/rideshare, auto-rickshaws, rental/self-drive car, car with driver. Do not open "
    "with 'Public transit and walking' unless the profile is transit_walk. Style examples per "
    "profile (not tied to any city): rail_walk: 'Metro or local trains and walking are usually "
    "the easiest combination, with taxis useful when rail is less convenient.' / "
    "taxi_driver_walk: 'Taxis or a car with a driver can be practical for longer sightseeing "
    "transfers, while walking works well within compact areas.' / car_rideshare: 'A rental car "
    "or taxis can be useful for dispersed trips, while public transit and walking work better "
    "within some individual areas.' / mixed: 'Public transit works well for many trips, while "
    "taxis or a car can be useful for destinations that are more spread out.'\n"
    "In the advisory do not include: prices or fares; schedules or frequencies; durations, "
    "distances or any numbers; safety claims; legal, licensing or permit claims; named "
    "operators, apps, brands, transit systems or line names (say 'metro' or 'public transit', "
    "never a system's own name); any reference to this itinerary, its days, routes or listed "
    "places; or any statement that the traveler must rent, own or use a car. Return an empty "
    "string for BOTH fields ONLY when the destination is ambiguous or not a place you "
    "recognize. Never repeat this guidance in the summary or day narratives."
)

NARRATOR_SYSTEM_PROMPT = (
    "You summarize an already-finalized draft itinerary for a travel planning system. You "
    "are a summarizer of the structured facts below, never a travel writer and never a "
    "source of new facts.\n\n"
    "Allowed statements ONLY: which listed places are on which day and in what order; that a "
    "place serves a requested interest, but only when its own line says 'serves requested "
    "interest'; the provider category printed beside a place; whether route data is "
    "available for a day; and the limitations listed under 'Known limitations'.\n\n"
    "Do NOT describe or judge any place or area: no adjectives about quality, popularity, "
    "fame, scenery, charm, atmosphere or character; no history, no explanation of what "
    "something is like, what can be seen or done there, or what food is served; no opening "
    "hours, prices, ratings, availability, safety, or booking statements; no place name that "
    "is not listed. If an interest was requested but no listed place serves it, say only "
    "that no approved place for it was available. A requested interest is only 'covered' or "
    "'served' when it appears under 'Requested interests served'; never say the trip covers, "
    "includes or focuses on an interest that appears under 'Requested interests with no serving "
    "place'.\n\n"
    "Style: short, plain, factual sentences such as 'Visit A, then B.' The day title must "
    "be neutral, of the form 'Day N'. Every day_number you return must match a day_number "
    "in the input. For each day set referenced_experience_ids to exactly the experience_id "
    "value(s) of that day's listed places -- never an id from another day or an invented one. "
    "Use empty lists rather than omitting keys. Mention that the plan was adjusted after "
    "feasibility checks only when the input states it."
    + GETTING_AROUND_ADVISORY_INSTRUCTIONS
)


def build_grounded_prompt_body(request: ItineraryNarrativeRequest) -> str:
    """The input facts only. Deliberately NOT included: the planner's
    `why_included` sentence, LLM #2 rationale/strategy prose, and any other
    free text that is not a provider-derived or deterministic field."""
    lines = [
        f"Destination: {request.destination}",
        f"Dates: {request.start_date} to {request.end_date}",
        f"Travelers: {request.travelers_count}"
        + (f" ({request.travel_group_type})" if request.travel_group_type else ""),
        f"Pace: {request.pace or 'unspecified'}",
        f"Requested interests: {', '.join(request.interests) if request.interests else 'none specified'}",
        f"Requested interests served by a scheduled place: "
        f"{', '.join(request.interests_served) if request.interests_served else 'none'}",
        f"Requested interests with no serving place: "
        f"{', '.join(request.interests_unserved) if request.interests_unserved else 'none'}",
    ]
    if request.weather_available and request.weather_summary:
        lines.append(f"Weather: {request.weather_summary}")
    if request.validation_status:
        lines.append(
            f"Validation status: {request.validation_status} "
            f"({request.critical_issue_count} critical issue(s), {request.warning_count} warning(s))"
        )
    lines.append(
        "Known limitations: "
        + (
            ", ".join(field.replace("_", " ") for field in request.unavailable_data_fields)
            if request.unavailable_data_fields
            else "none listed"
        )
    )
    if request.was_adjusted_after_feasibility_checks:
        lines.append("Plan adjusted after feasibility checks: yes")

    lines.append("")
    lines.append("Days:")
    for day in request.days:
        lines.append(f"- day_number {day.day_number} ({day.date}):")
        if day.experiences:
            for experience in day.experiences:
                piece = f"[experience_id={experience.experience_id}] {experience.name}"
                label = experience.normalized_category or experience.category
                if label:
                    piece += f" (category: {label})"
                if experience.matched_interests:
                    piece += f" -- serves requested interest: {', '.join(experience.matched_interests)}"
                lines.append(f"    * {piece}")
        else:
            lines.append("    * No scheduled places for this day.")
        if day.restaurant_names:
            lines.append(f"    Nearby food places (names only): {', '.join(day.restaurant_names)}")
        lines.append(f"    Route data available: {'yes' if day.has_movement_data else 'no'}")
    if request.truncated:
        lines.append("")
        lines.append("Note: this input was truncated for length -- some days/items were omitted.")
    return "\n".join(lines)
