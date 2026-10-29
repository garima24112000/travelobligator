# Reviewer instructions — quality acceptance v3

You will score ten travel itineraries. Your scores decide whether the planner meets its quality release target.
Nobody can change a score once you have recorded it, and nobody will ask you to.

## Before you start

You must not be the person who ran the evaluation, and you must not have worked on the planner. Before you
receive the packets, complete and sign the independence declaration (`INDEPENDENCE_DECLARATION.md`) and return
it to the operator. The packets are prepared only after it has been accepted.

## What you receive

The review bundle, and nothing else:

- `packets/P01.md` … `packets/P10.md`. Each packet is one trip request and the itinerary the planner produced
  for it, shown as a traveller would receive it: places, estimated visit times, opening-hours status, travel
  between stops, food nearby, day notes, and what the plan itself says needs checking. Each stop has a map
  link.
- `scoresheet.csv`: one empty row per packet.
- These instructions.

The bundle holds no scenario name or number, no automated check and no quality figure. Do not ask for, and do
not look at, any other output of this evaluation before your scoresheet is submitted: the run's records, its
automated checks, or anyone's opinion of the plans.

## The rubric

Score each dimension with a **whole number from 1 to 5**. No halves.

| Field | Dimension | Question |
| --- | --- | --- |
| `attraction_usefulness` | Attraction usefulness | Are these places a visitor would be glad to have spent a slot on? |
| `geographic_day_coherence` | Geographic and day coherence | Does each day hold together on a map and as an experience? |
| `personalization` | Personalization | Does the plan reflect the stated interests and must-visits? |
| `pace_realism` | Pace realism | Could the day be done at the stated pace, travel included? |
| `redundancy_avoidance` | Redundancy avoidance | Is every slot a distinct experience? |
| `overall_would_use` | Overall | Would I actually use this plan? |

Scale: **1** unusable, **2** poor, **3** acceptable with edits, **4** good, **5** excellent.

The questions and the scale are the contract's own (`docs/24_itinerary_quality_contract.md`, section 6). There
are no other anchors: use your own judgement as a traveller making this trip on these dates.

## Steps for each packet

1. Read the request: destination, number of days, pace, interests, must-visits and dates. The trip starts on a
   Monday.
2. Read the plan day by day, including its notes and what it says needs checking. Those are part of what a
   traveller would get.
3. Look places up. Open the map links and use any map or website you like. You may know the destination. Do
   not compare the plan with a reference itinerary or another planner's answer; judge it as it stands.
4. **Check facts that matter.** For any scheduled place you check against an **official or primary source**
   (the venue's own site or listing, the operator, a government site), note whether it is:
   - closed, permanently or on that date;
   - relocated;
   - not existing;
   - not open to visitors;
   - or materially away from where the plan puts it.

   Each one you confirm is a **verified factual error**. Write the place, what you found, the source and the
   date you checked it in the packet's table. Count them in the scoresheet's `verified_factual_errors` (0 if
   none). An unconfirmed suspicion is a comment, not an error.
5. Write the six scores, a sentence of evidence for each, and any other comments in the packet's score section
   (below its plan; do not edit the plan itself). Then fill in the packet's row of `scoresheet.csv`:
   - the six scores and the error count;
   - `reviewer`: your name exactly as recorded in the protocol;
   - `scored_on`: `YYYY-MM-DD`;
   - `part_b_read_after_scoring`: `yes` (you have seen no automated check or figure for any plan).

Score the packets in the order given (P01 to P10). The order is shuffled and says nothing about the plans.

## Rules

- Score what is in the packet. A missing place, a long day or a vague note counts against the plan as a
  traveller would experience it.
- A packet that says no itinerary was produced is scored as a traveller with no plan would score it.
- Record every score once. Until you submit, you may correct a typing mistake on the scoresheet; note the
  correction in the packet.
- Do not discuss the plans with anyone who worked on the planner until your scoresheet is submitted.

## When you have finished

Return the completed `scoresheet.csv` and the ten packets with your evidence and sign-off. The operator checks
that every row is complete and that each returned packet carries the plan issued under its code, then locks
them. From then on nothing you returned can change. Only after that may you receive the automated checks and
figures for each plan, if you wish to read them. The acceptance calculation is done from the locked files
mechanically. You are not asked to decide whether the release target is met.
