#!/usr/bin/env python3
"""Section 202A regeneration QA runner.

Drives a RUNNING backend over HTTP (nothing imported from the app).
For each chain in `regeneration_qa_cases.json` it generates a fresh trip
from `base_case_id`, then for every step: submit feedback -> POST
/regenerate -> re-read the persisted state, and compute OBJECTIVE checks
(request correctness, preservation, grounding, downstream recomputation,
versioning, diff accuracy). It restates the user's request as a predicate;
it never grades itinerary quality.

Full payloads go to --raw-dir (must be OUTSIDE the repo). Only the compact
per-step summary is written to evaluation/results/.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent


def norm_name(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    n = re.sub(r"\b(the|historic|district|national|park|museum|of|and)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def days_of(state: dict) -> dict[int, list[dict]]:
    return {d["day_number"]: d.get("experiences", []) for d in (state.get("experience_plan") or {}).get("daily_plans", [])}


def ids_by_day(state: dict) -> dict[int, list[str]]:
    return {k: [e["experience_id"] for e in v] for k, v in days_of(state).items()}


def name_by_id(state: dict) -> dict[str, str]:
    return {e["experience_id"]: e["name"] for v in days_of(state).values() for e in v}


def resolve_template(text: str, state: dict) -> str | None:
    def sub(m: re.Match) -> str:
        day, stop = int(m.group(1)), int(m.group(2))
        exps = days_of(state).get(day, [])
        if 0 < stop <= len(exps):
            return exps[stop - 1]["name"]
        raise LookupError(f"d{day}s{stop}")

    try:
        return re.sub(r"\{d(\d+)s(\d+)\}", sub, text)
    except LookupError:
        return None


def target_id(spec: str, state: dict) -> str | None:
    m = re.fullmatch(r"d(\d+)s(\d+)", spec)
    if not m:
        return None
    exps = days_of(state).get(int(m.group(1)), [])
    idx = int(m.group(2))
    return exps[idx - 1]["experience_id"] if 0 < idx <= len(exps) else None


def independent_diff(before: dict[int, list[str]], after: dict[int, list[str]]) -> dict:
    b_all = {i: d for d, ids in before.items() for i in ids}
    a_all = {i: d for d, ids in after.items() for i in ids}
    added = sorted(set(a_all) - set(b_all))
    removed = sorted(set(b_all) - set(a_all))
    moved = sorted(i for i in set(a_all) & set(b_all) if a_all[i] != b_all[i])
    reordered = sorted(
        d for d in set(before) & set(after)
        if before[d] != after[d] and sorted(before[d]) == sorted(after[d])
    )
    changed_days = sorted(d for d in set(before) | set(after) if before.get(d) != after.get(d))
    return {"added": added, "removed": removed, "moved": moved, "reordered_days": reordered, "changed_days": changed_days}


def candidate_names(state: dict) -> set[str]:
    dctx = state.get("destination_context") or {}
    names = {norm_name(c["name"]) for k in ("candidate_pois", "candidate_restaurants", "candidate_accommodation_pois")
             for c in dctx.get(k) or [] if c.get("name")}
    for c in (state.get("ai_candidate_promotion_report") or {}).get("promoted_candidates") or []:
        if isinstance(c, dict) and c.get("name"):
            names.add(norm_name(c["name"]))
    return names


def check_expectation(expect: dict, before: dict, after: dict, http_status: int, diff: dict, applied: bool,
                      resolved_targets: dict[str, str | None]) -> tuple[bool | None, str]:
    """Returns (success, note). None = not evaluable (e.g. target unresolved)."""
    t = expect["type"]
    b_ids, a_ids = ids_by_day(before), ids_by_day(after)
    if t == "expect_refusal":
        unchanged = b_ids == a_ids
        return (http_status != 200 and unchanged, f"http={http_status}, plan unchanged={unchanged}")
    if t == "expect_no_ungrounded_addition":
        cands = candidate_names(after)
        an = name_by_id(after)
        by_id = {e["experience_id"]: e for v in days_of(after).values() for e in v}
        bad = [an[i] for i in diff["added"] if norm_name(an[i]) not in cands and not (
            by_id[i].get("provider_place_id") or any((c or {}).get("source_type") in ("provider_fact", "open_data_fact") for c in by_id[i].get("claim_sources") or []))]
        fiction = [n for n in an.values() if "zzyzx" in n.lower()]
        return (not bad and not fiction, f"added={[an[i] for i in diff['added']]}, ungrounded={bad}, fictional_present={fiction}")
    if not applied:
        return (False, f"regeneration not applied (http={http_status})")
    if t == "remove":
        tid = resolved_targets.get(expect["target"])
        if tid is None:
            return (None, "target could not be resolved")
        target_name = norm_name(name_by_id(before).get(tid, ""))
        gone = tid not in {i for ids in a_ids.values() for i in ids} and target_name not in {
            norm_name(n) for n in name_by_id(after).values()
        }
        note = f"target (id and name) absent after={gone}"
        if not expect.get("replace", True):
            ok = gone and sum(map(len, a_ids.values())) == sum(map(len, b_ids.values())) - 1 and not diff["added"]
            return (ok, note + f"; total {sum(map(len, b_ids.values()))}->{sum(map(len, a_ids.values()))}; added={len(diff['added'])}")
        return (gone, note)
    if t == "add":
        an = name_by_id(after)
        hits = [an[i] for i in diff["added"] if any(tok in an[i].lower() for tok in expect["name_contains"])]
        return (bool(hits), f"added={[an[i] for i in diff['added']]}")
    if t == "day_specific":
        d = expect["day"]
        return (bool(diff["changed_days"]) and set(diff["changed_days"]) <= {d}, f"changed_days={diff['changed_days']}")
    if t == "preserve_day":
        d = expect["day"]
        same = b_ids.get(d) == a_ids.get(d)
        other = [x for x in diff["changed_days"] if x != d]
        return (same and bool(other), f"day{d} identical={same}; other days changed={other}")
    if t == "move":
        tid = resolved_targets.get(expect["target"])
        if tid is None:
            return (None, "target could not be resolved")
        now = next((d for d, ids in a_ids.items() if tid in ids), None)
        return (now == expect["to_day"], f"target now on day {now}")
    if t == "less_packed_day":
        d = expect["day"]
        return (len(a_ids.get(d, [])) < len(b_ids.get(d, [])), f"day{d} stops {len(b_ids.get(d, []))}->{len(a_ids.get(d, []))}")
    if t == "global_relaxed":
        pace = str((after.get("traveler_profile") or {}).get("pace")).lower()
        pace_b = str((before.get("traveler_profile") or {}).get("pace")).lower()
        tb, ta = sum(map(len, b_ids.values())), sum(map(len, a_ids.values()))
        if pace_b.endswith("relaxed") and ta == tb:
            return (None, f"not evaluable: baseline pace was already relaxed and total stops unchanged ({tb})")
        return (pace.endswith("relaxed") and ta <= tb and (pace != pace_b or ta < tb), f"pace {pace_b}->{pace}; total stops {tb}->{ta}")
    if t == "interest":
        interests = [str(i).lower() for i in (after.get("traveler_profile") or {}).get("interests", [])]
        an = {e["experience_id"]: e for v in days_of(after).values() for e in v}
        food_added = [an[i]["name"] for i in diff["added"] if any(tok in (an[i].get("category", "") + an[i]["name"]).lower() for tok in expect["tokens"])]
        interests_b = [str(i).lower() for i in (before.get("traveler_profile") or {}).get("interests", [])]
        profile_hit = interests != interests_b and any(tok in i for tok in expect["tokens"] for i in interests)
        return (profile_hit or bool(food_added), f"interests={interests}; food-like added={food_added}")
    return (None, f"unknown expectation type {t}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full-ai-url", required=True)
    ap.add_argument("--as-configured-url", required=True)
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--chains", default=None)
    ap.add_argument("--summary-out", default=str(HERE / "results" / "regeneration_results.json"))
    ap.add_argument("--pause", type=float, default=3.0)
    ap.add_argument("--cases-file", default=str(HERE / "regeneration_qa_cases.json"),
                    help="Section 202C: alternate chain fixture (step definitions are reused verbatim)")
    ap.add_argument("--retry-rate-limit-minutes", type=float, default=0.0,
                    help="Section 202C tooling: when a regenerate call is refused ONLY because the AI provider is "
                         "rate-limited (REGENERATION_PROVIDER_RATE_LIMITED), wait and retry the SAME request for up "
                         "to this many minutes. Every refusal is recorded in `rate_limit_retries`; nothing is hidden.")
    ap.add_argument("--force-mode", choices=["full_ai", "as_configured"], default=None,
                    help="run every selected chain against this backend regardless of the chain's own `mode`")
    args = ap.parse_args()

    raw_dir = Path(args.raw_dir).resolve()
    if REPO_ROOT in raw_dir.parents or raw_dir == REPO_ROOT:
        print("refusing: --raw-dir must be outside the repository", file=sys.stderr)
        return 2
    raw_dir.mkdir(parents=True, exist_ok=True)

    regen = json.loads(Path(args.cases_file).read_text())
    gen = json.loads((HERE / "itinerary_qa_cases.json").read_text())
    base_by_id = {c["case_id"]: c for c in gen["cases"]}
    defaults = gen["defaults"]
    urls = {"full_ai": args.full_ai_url, "as_configured": args.as_configured_url}
    wanted = set(args.chains.split(",")) if args.chains else None

    out: list[dict] = []
    for chain in regen["chains"]:
        if wanted and chain["chain_id"] not in wanted:
            continue
        chain = {**chain, "mode": args.force_mode or chain["mode"]}
        client = httpx.Client(base_url=urls[chain["mode"]], timeout=900)
        client.post("/auth/signup", json={"email": f"qa-regen-{chain['chain_id'].lower()}-{int(time.time())}@example.test",
                                          "password": "QaPassw0rd!234"}).raise_for_status()
        case = base_by_id[chain["base_case_id"]]
        start = dt.date.today() + dt.timedelta(days=int(defaults["start_offset_days"]))
        body = {"destination_scope": "single_city", "primary_destination": case["destination"],
                "origin_city": case.get("origin_city", defaults["origin_city"]), "start_date": str(start),
                "end_date": str(start + dt.timedelta(days=case["days"] - 1)),
                "travelers_count": case.get("travelers_count", defaults["travelers_count"]),
                "travel_group_type": case["group"], "pace": case["pace"], "interests": case.get("interests", []),
                "must_visit": case.get("must_visit", []), "constraints": case.get("constraints", [])}
        trip_id = client.post("/trips", json=body).json()["data"]["trip_id"]
        t0 = time.time()
        client.post(f"/trips/{trip_id}/generate").raise_for_status()
        gen_latency = round(time.time() - t0, 1)
        print(f"[{chain['chain_id']}] baseline generated in {gen_latency}s", flush=True)

        def get_state() -> dict:
            return client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]

        def revisions() -> tuple[int, str | None]:
            br = client.get(f"/trips/{trip_id}/branches").json()["data"]
            active = next(b for b in br["branches"] if b["is_active"])
            revs = client.get(f"/trips/{trip_id}/branches/{active['branch_id']}/revisions").json()["data"]["revisions"]
            return len(revs), active.get("head_version_label")

        for step in chain["steps"]:
            before = get_state()
            rev_before, _ = revisions()
            rec: dict[str, Any] = {"chain_id": chain["chain_id"], "step_id": step["step_id"], "kind": step["kind"],
                                   "mode": chain["mode"], "base_case_id": chain["base_case_id"], "trip_id": trip_id}
            text = resolve_template(step["feedback"], before)
            if text is None:
                rec.update({"skipped": "template target did not exist in the current itinerary (e.g. baseline had too few stops)"})
                out.append(rec)
                print(f"  {step['step_id']}: SKIPPED (unresolvable target)", flush=True)
                continue
            rec["feedback_text"] = text
            expect = step["expect"]
            resolved = {}
            for key in ("target",):
                if key in expect:
                    resolved[expect[key]] = target_id(expect[key], before)
            fb = client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": text})
            rec["feedback_http_status"] = fb.status_code
            t1 = time.time()
            rr = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True, "scope": "affected_stages"})
            retries = 0
            deadline = time.time() + args.retry_rate_limit_minutes * 60
            while (rr.status_code == 409 and args.retry_rate_limit_minutes > 0 and time.time() < deadline
                   and ((rr.json().get("errors") or [{}])[0] or {}).get("code") == "REGENERATION_PROVIDER_RATE_LIMITED"):
                retries += 1
                print(f"    {step['step_id']}: rate-limited (retry {retries}); waiting 75s", flush=True)
                time.sleep(75)
                rr = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True, "scope": "affected_stages"})
            rec["rate_limit_retries"] = retries
            rec["regenerate_latency_seconds"] = round(time.time() - t1, 1)
            rec["regenerate_http_status"] = rr.status_code
            body_json = rr.json()
            data = body_json.get("data") if rr.status_code == 200 else None
            if rr.status_code != 200:
                errs = body_json.get("errors") or []
                rec["error_code"] = errs[0].get("code") if errs and isinstance(errs[0], dict) else None
                rec["error_message"] = (errs[0].get("message") if errs and isinstance(errs[0], dict) else body_json.get("message"))
            after = get_state()
            rev_after, head_label = revisions()
            applied = rr.status_code == 200 and (data or {}).get("status") == "applied"
            b_ids, a_ids = ids_by_day(before), ids_by_day(after)
            diff = independent_diff(b_ids, a_ids)
            rec["applied"] = applied
            b_all_ids = {i for ids in b_ids.values() for i in ids}
            a_all_ids = {i for ids in a_ids.values() for i in ids}
            b_names = {norm_name(n) for n in name_by_id(before).values()}
            a_names = {norm_name(n) for n in name_by_id(after).values()}
            rec["identity_stability"] = {
                "before_experience_count": len(b_all_ids),
                "ids_surviving_fraction": round(len(b_all_ids & a_all_ids) / len(b_all_ids), 2) if b_all_ids else None,
                "names_surviving_fraction": round(len(b_names & a_names) / len(b_names), 2) if b_names else None,
            }
            if data:
                rec.update({"targeted": data.get("targeted"), "interpretation_status": data.get("interpretation_status"),
                            "execution_status": data.get("execution_status"),
                            "previous_version": data.get("previous_version"), "current_version": data.get("current_version"),
                            "changed_sections": data.get("changed_sections"), "message": (data.get("message") or "")[:200]})
            success, note = check_expectation(expect, before, after, rr.status_code, diff, applied, resolved)
            rec["request_correct"] = success
            rec["request_check_note"] = note
            rec["independent_diff"] = {k: (len(v) if isinstance(v, list) and k != "changed_days" and k != "reordered_days" else v) for k, v in diff.items()}

            # Preservation (only meaningful for the targeted path, which reports preserved days).
            pres_days = (data or {}).get("preserved_day_indices") or []
            aff_days = (data or {}).get("affected_day_indices") or []
            rec["preserved_days_reported"] = pres_days
            rec["affected_days_reported"] = aff_days
            rec["preserved_days_unchanged"] = all(b_ids.get(d) == a_ids.get(d) for d in pres_days) if pres_days else None
            rec["unexpected_changed_days"] = [d for d in diff["changed_days"] if aff_days and d not in aff_days] if data else None
            if data and expect["type"] in ("remove", "move", "preserve_day", "less_packed_day", "day_specific"):
                rec["days_changed_beyond_request_scope"] = diff["changed_days"]

            # Grounding of additions.
            cands = candidate_names(after)
            an = name_by_id(after)
            rec["added_names"] = [an[i] for i in diff["added"]]
            after_by_id = {e["experience_id"]: e for v in days_of(after).values() for e in v}

            def _backed(e: dict) -> bool:
                return bool(e.get("provider_place_id")) or any(
                    (c or {}).get("source_type") in ("provider_fact", "open_data_fact") for c in e.get("claim_sources") or []
                )

            rec["ungrounded_additions"] = [an[i] for i in diff["added"]
                                            if norm_name(an[i]) not in cands and not _backed(after_by_id[i])]
            rec["added_without_coordinates"] = [an[i] for i in diff["added"]
                                                 if not next((e.get("coordinates") for v in days_of(after).values() for e in v if e["experience_id"] == i), None)]

            # Section 202B.1: cross-day duplicates by STABLE provider identity
            # (never display name), read from the persisted after-state.
            place_days: dict[str, list[int]] = {}
            for day_number, exps in days_of(after).items():
                for e in exps:
                    if e.get("provider_source") and e.get("provider_place_id"):
                        place_days.setdefault(f"{e['provider_source']}:{e['provider_place_id']}", []).append(day_number)
            rec["cross_day_duplicates_by_provider_identity"] = {k: v for k, v in place_days.items() if len(v) > 1}
            rec["experiences_without_provider_identity"] = sum(
                1 for exps in days_of(after).values() for e in exps if not (e.get("provider_source") and e.get("provider_place_id"))
            )

            # Diff accuracy vs the independent computation.
            rd = (data or {}).get("diff") if data else None
            if rd:
                rec["reported_diff_matches_independent"] = {
                    "added": sorted(rd.get("added_experience_ids", [])) == diff["added"],
                    "removed": sorted(rd.get("removed_experience_ids", [])) == diff["removed"],
                    "moved": sorted(m["experience_id"] for m in rd.get("moved_experiences", [])) == diff["moved"],
                    "reordered_days": sorted(r["day_index"] for r in rd.get("reordered_days", [])) == diff["reordered_days"],
                }
            else:
                rec["reported_diff_matches_independent"] = None

            # Downstream recomputation evidence (reads persisted, post-regeneration state).
            legs = (after.get("route_feasibility_report") or {}).get("legs") or []
            after_all = {i for ids in a_ids.values() for i in ids}
            leg_ids = {leg.get("from_experience_id") for leg in legs} | {leg.get("to_experience_id") for leg in legs}
            narr = after.get("itinerary_narrative_report") or {}
            nrefs = [r for dn in narr.get("daily_narratives") or [] for r in dn.get("referenced_experience_ids") or []]
            rec["downstream"] = {
                "route_legs_reference_only_current_experiences": leg_ids <= (after_all | {None}),
                "route_leg_count": len(legs),
                "validation_rerun": (before.get("validation_report") or {}).get("validated_at") != (after.get("validation_report") or {}).get("validated_at"),
                "narrative_references_only_current_experiences": set(nrefs) <= after_all,
                "narrative_status": narr.get("status"),
                "narrative_changed": (before.get("itinerary_narrative_report") or {}).get("summary") != narr.get("summary"),
            }
            # Versioning / revision lineage.
            rec["versioning"] = {
                "revisions_before": rev_before, "revisions_after": rev_after,
                "revision_created_only_on_success": (rev_after == rev_before + 1) if applied else (rev_after == rev_before),
                "active_head_label_after": head_label,
                "head_label_matches_current_version": (head_label == data.get("current_version")) if applied and data else None,
            }
            fb_events = after.get("feedback_history") or []
            rec["feedback_marked_applied"] = bool(fb_events and fb_events[-1].get("applied_at")) if applied else None
            out.append(rec)
            (raw_dir / f"{chain['chain_id']}__{step['step_id']}__after.json").write_text(json.dumps(after, indent=1, default=str))
            print(f"  {step['step_id']}: http={rr.status_code} applied={applied} correct={success} ({note})", flush=True)
            time.sleep(args.pause)

    Path(args.summary_out).write_text(json.dumps({"generated_on": str(dt.date.today()), "steps": out}, indent=1))
    print("summary ->", args.summary_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
