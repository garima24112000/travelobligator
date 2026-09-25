#!/usr/bin/env python3
"""Section 202A baseline generation QA runner.

Drives a RUNNING TravelObligator backend over real HTTP (nothing is
imported from or patched in the app), generates each case in
`itinerary_qa_cases.json`, and extracts OBJECTIVE, structurally
measurable facts from the returned `PlanningState`. It deliberately does
NOT assign quality grades: PASS/WARN/FAIL itinerary judgement and "would I
use this" are human labels recorded separately (see README.md).

Usage:
  python evaluation/run_itinerary_qa.py --base-url http://localhost:8001 \
      --mode full_ai --raw-dir <outside-repo-dir> \
      --log-file <backend-log> [--cases NYC-1,DC-1]

Raw payloads (full PlanningState) go to --raw-dir, which must be OUTSIDE
the repository. Only the compact summary is written to evaluation/results/.
No secrets are read, printed, or written: the runner never touches `.env`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

# Documented, deliberately coarse thresholds (see README "Objective metrics").
FAR_APART_KM = 12.0  # a single day's stops spanning more than this is flagged
UNSUPPORTED_CLAIM_PATTERNS = {
    "price": re.compile(r"[$€£]\s?\d|\b\d+\s?(usd|eur|dollars|euros)\b|\bcheap\b|\bexpensive\b|\bfree entry\b|\bfree admission\b", re.I),
    "rating": re.compile(r"\b\d(\.\d)?\s?(/|out of)\s?5\b|\b\d(\.\d)?\s?stars?\b|\bhighly[- ]rated\b|\btop[- ]rated\b|\bbest[- ]rated\b|\bacclaimed\b|\bpopular\b", re.I),
    "hours": re.compile(r"\bopen(s)? (until|from|at|daily)\b|\bcloses? at\b|\b\d{1,2}\s?(am|pm)\b|\bopening hours\b", re.I),
    "availability": re.compile(r"\bavailable\b|\bsold out\b|\btickets? (are|is)\b|\bno (queue|line)s?\b", re.I),
    "booking": re.compile(r"\bbook(ed|ing)?\b|\breserv(e|ation)s?\b|\bconfirmed\b", re.I),
    "safety": re.compile(r"\bsafe(st|ty)?\b|\bdangerous\b|\bcrime\b", re.I),
}
FACT_KEY_HINT = re.compile(r"price|cost|rating|review|hours|availability|booking|url|phone|website", re.I)


def haversine_km(a: dict, b: dict) -> float:
    lat1, lon1 = a["lat"], a.get("lng", a.get("lon"))
    lat2, lon2 = b["lat"], b.get("lng", b.get("lon"))
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def norm_name(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    n = re.sub(r"\b(the|historic|district|national|park|museum|of|and)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def walk_facts(obj: Any, path: str = "", out: list | None = None) -> list:
    """Every non-empty value under a key that looks like a factual claim."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if FACT_KEY_HINT.search(k) and v not in (None, "", [], {}, 0, False):
                if not isinstance(v, (dict, list)):
                    out.append({"path": p, "value": str(v)[:120]})
            walk_facts(v, p, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk_facts(v, f"{path}[{i}]", out)
    return out


def score_state(state: dict, case: dict) -> dict:
    """Objective structural facts. No subjective grading."""
    plan = state.get("experience_plan") or {}
    days = plan.get("daily_plans") or []
    dctx = state.get("destination_context") or {}
    cand_names = {
        norm_name(c["name"])
        for key in ("candidate_pois", "candidate_restaurants", "candidate_accommodation_pois")
        for c in (dctx.get(key) or [])
        if c.get("name")
    }
    for c in (state.get("ai_candidate_promotion_report") or {}).get("promoted_candidates") or []:
        if isinstance(c, dict) and c.get("name"):
            cand_names.add(norm_name(c["name"]))

    exps = [e for d in days for e in d.get("experiences", [])]
    ids = [e.get("experience_id") for e in exps]
    per_day = []
    dup_names: dict[str, list[int]] = {}
    for d in days:
        pts = [e["coordinates"] for e in d.get("experiences", []) if e.get("coordinates")]
        max_pair = max((haversine_km(a, b) for i, a in enumerate(pts) for b in pts[i + 1:]), default=0.0)
        legs = sum(haversine_km(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        per_day.append({
            "day": d["day_number"],
            "stops": len(d.get("experiences", [])),
            "names": [e["name"] for e in d.get("experiences", [])],
            "max_pairwise_km": round(max_pair, 1),
            "sequential_km": round(legs, 1),
            "far_apart": max_pair > FAR_APART_KM,
            "day_warnings": len(d.get("warnings") or []),
        })
        for e in d.get("experiences", []):
            dup_names.setdefault(norm_name(e["name"]), []).append(d["day_number"])
    dups = {k: v for k, v in dup_names.items() if len(v) > 1 and k}

    def _provider_backed(e: dict) -> bool:
        return bool(e.get("provider_place_id")) or any(
            (c or {}).get("source_type") in ("provider_fact", "open_data_fact") for c in e.get("claim_sources") or []
        )

    unsupported = [e["name"] for e in exps if norm_name(e["name"]) not in cand_names and not _provider_backed(e)]
    routing = state.get("route_feasibility_report") or {}
    legs = routing.get("legs") or []
    leg_status: dict[str, int] = {}
    for leg in legs:
        s = leg.get("feasibility_status") or leg.get("status") or "unknown"
        leg_status[s] = leg_status.get(s, 0) + 1
    bufs = (state.get("travel_time_buffer_report") or {}).get("buffers") or []
    buf_status: dict[str, int] = {}
    for b in bufs:
        buf_status[b.get("buffer_status", "unknown")] = buf_status.get(b.get("buffer_status", "unknown"), 0) + 1

    vr = state.get("validation_report") or {}
    warn_cats: dict[str, int] = {}
    for w in vr.get("warnings") or []:
        c = w.get("category", "unknown") if isinstance(w, dict) else "unknown"
        warn_cats[c] = warn_cats.get(c, 0) + 1

    narr = state.get("itinerary_narrative_report") or {}
    narr_text = " ".join(
        [narr.get("summary") or ""]
        + [f"{dn.get('title', '')} {dn.get('narrative', '')} " + " ".join(dn.get("caveats") or [])
           for dn in narr.get("daily_narratives") or []]
    )
    narr_refs = [r for dn in narr.get("daily_narratives") or [] for r in dn.get("referenced_experience_ids") or []]
    claim_hits = {
        k: sorted({m.group(0).lower() for m in pat.finditer(narr_text)})
        for k, pat in UNSUPPORTED_CLAIM_PATTERNS.items()
    }
    claim_hits = {k: v for k, v in claim_hits.items() if v}

    # Non-null factual-looking fields anywhere in the user-facing plan sections.
    fact_paths = walk_facts({
        "experience_plan": plan,
        "experience_cards": state.get("experience_cards"),
        "decision_cards": state.get("decision_cards"),
    })
    fact_paths = [f for f in fact_paths if not f["path"].endswith("data_quality.confidence")]

    ai_prop = (state.get("ai_candidate_proposal_batch") or {}).get("result") or {}
    ai_reason = state.get("ai_itinerary_reasoning_result") or {}
    ai_repair = state.get("ai_itinerary_repair_result") or {}
    interests = [i.lower() for i in case.get("interests", [])]
    exp_text = " ".join(f"{e['name']} {e.get('category','')}" for e in exps).lower()
    stops = [d["stops"] for d in per_day]

    return {
        "scheduled_experience_count": len(exps),
        "days_generated": len(days),
        "empty_days": [d["day"] for d in per_day if d["stops"] == 0],
        "stops_per_day": stops,
        "grounding": {
            "all_have_stable_ids": all(ids) and len(set(ids)) == len(ids),
            "with_coordinates": sum(1 for e in exps if e.get("coordinates")),
            "with_claim_sources": sum(1 for e in exps if e.get("claim_sources")),
            "data_status_live": sum(1 for e in exps if (e.get("data_quality") or {}).get("data_status") == "live"),
            "promoted_from_ai": sum(1 for e in exps if e.get("promoted_from_ai")),
            "unsupported_identity_names": unsupported,
            "unsupported_identity_count": len(unsupported),
            "candidate_pool": {
                "attractions": len(dctx.get("candidate_pois") or []),
                "restaurants": len(dctx.get("candidate_restaurants") or []),
                "accommodation_pois": len(dctx.get("candidate_accommodation_pois") or []),
            },
        },
        "geography": {"per_day": per_day, "days_far_apart": [d["day"] for d in per_day if d["far_apart"]]},
        "duplication": {"repeated_normalized_names": dups},
        "relevance_signals": {
            "interests_requested": interests,
            "interest_words_in_scheduled_names_or_categories": [i for i in interests if i.rstrip("s") in exp_text],
        },
        "routing": {
            "report_status": routing.get("status"),
            "legs_total": len(legs),
            "leg_feasibility_counts": leg_status,
            "providers": sorted({leg.get("provider") for leg in legs if leg.get("provider")}),
            "sequencing_status": (state.get("route_aware_sequencing_report") or {}).get("status"),
            "buffer_status_counts": buf_status,
        },
        "validation": {
            "readiness_status": vr.get("readiness_status"),
            "critical_count": len(vr.get("critical_issues") or []),
            "warning_count": len(vr.get("warnings") or []),
            "suggestion_count": len(vr.get("suggestions") or []),
            "warning_categories": warn_cats,
        },
        "ai": {
            "candidate_proposal": {"status": ai_prop.get("status"), "blocked": ((ai_prop.get("guardrail_report") or {}).get("blocked_reasons") or [])[:1]},
            "itinerary_reasoning": {"status": ai_reason.get("status"), "blocked": ((ai_reason.get("guardrail_report") or {}).get("blocked_reasons") or [])[:1]},
            "repair": {"result": (ai_repair or {}).get("status") if isinstance(ai_repair, dict) else None,
                       "attempts": state.get("ai_itinerary_repair_attempt_count")},
            "narrator": {"status": narr.get("status"), "provider": narr.get("provider"), "message": narr.get("message")},
        },
        "narration": {
            "text_length_chars": len(narr_text),
            "referenced_ids_all_scheduled": all(r in ids for r in narr_refs),
            "referenced_id_count": len(narr_refs),
            "pattern_hits_needing_provenance_review": claim_hits,
        },
        "provider_coverage": state.get("provider_coverage"),
        "unavailable_data": [
            {"field": u.get("field"), "status": u.get("data_status"), "reason": (u.get("reason") or "")[:140]}
            for u in state.get("unavailable_data") or []
        ],
        "factual_looking_fields_non_null": fact_paths[:40],
        "factual_looking_field_count": len(fact_paths),
        "candidate_supply": _candidate_supply(state, interests),
        "scheduled_categories": _scheduled_categories(exps),
        "resolved_destination": (dctx.get("resolved_destination") or {}).get("display_name"),
        "quality_finding_categories": sorted({
            (w.get("category") if isinstance(w, dict) else None)
            for w in (vr.get("warnings") or [])
            if isinstance(w, dict) and w.get("category") in (
                "empty_day", "thin_day", "interest_undercoverage", "interest_supply_limited",
                "category_concentration", "duplicate_experience")
        } | {
            i.get("category") for i in (vr.get("critical_issues") or [])
            if isinstance(i, dict) and i.get("category") in ("duplicate_experience", "destination_unresolved")
        }),
        "pace_requested": case.get("pace"),
        "avg_stops_per_day": round(sum(stops) / len(stops), 2) if stops else 0,
    }


def _candidate_supply(state: dict, interests: list[str]) -> dict:
    """Section 202B.2 (Task 32): factual candidate supply from the quality
    report -- discovered / tier counts / category distribution / supply per
    requested interest (canonical keys the backend itself derived)."""
    report = state.get("candidate_quality_report") or {}
    scores = report.get("attraction_scores") or []
    tiers: dict[str, int] = {}
    cats: dict[str, int] = {}
    per_interest: dict[str, int] = {}
    viable = {"primary_anchor", "good_candidate", "secondary_candidate"}
    for sc in scores:
        tiers[sc.get("quality_tier")] = tiers.get(sc.get("quality_tier"), 0) + 1
        cats[sc.get("normalized_category") or "unclassified"] = cats.get(sc.get("normalized_category") or "unclassified", 0) + 1
        if sc.get("quality_tier") in viable:
            for k in sc.get("matched_interests") or []:
                per_interest[k] = per_interest.get(k, 0) + 1
    return {
        "attractions_discovered": len(scores),
        "tiers": tiers,
        "unsuitable_rejected": sum(1 for sc in scores if "unsuitable_place_type" in (sc.get("reject_reasons") or [])),
        "category_distribution": cats,
        "viable_supply_per_matched_interest": per_interest,
    }


def _scheduled_categories(exps: list) -> dict:
    cats: dict[str, int] = {}
    for e in exps:
        c = e.get("normalized_category") or "unclassified"
        cats[c] = cats.get(c, 0) + 1
    low = sum(1 for e in exps if e.get("low_value_object") or e.get("commercial_gallery"))
    return {"by_category": cats, "low_value_or_gallery_count": low,
            "interests_matched": sorted({m for e in exps for m in (e.get("matched_interests") or [])})}


def compact_itinerary_text(state: dict) -> str:
    lines = []
    for d in (state.get("experience_plan") or {}).get("daily_plans") or []:
        lines.append(f"Day {d['day_number']} ({d.get('date')})")
        for e in d.get("experiences", []):
            c = e.get("coordinates") or {}
            lines.append(f"  {e['stop_order']}. {e['name']} [{e.get('category')}] ({c.get('lat')},{c.get('lng')})")
        for r in (d.get("restaurant_suggestions") or [])[:3]:
            lines.append(f"     meal: {r.get('name') if isinstance(r, dict) else r}")
    narr = state.get("itinerary_narrative_report") or {}
    if narr.get("summary"):
        lines.append("NARRATIVE: " + narr["summary"])
        for dn in narr.get("daily_narratives") or []:
            lines.append(f"  D{dn.get('day_number')}: {dn.get('narrative')}  caveats={dn.get('caveats')}")
    return "\n".join(lines)


def stage_timings(log_file: Path | None, request_id: str | None) -> dict:
    """Only what the app already logs (`duration_ms` on provider/AI calls
    with a `stage`/`provider`). Nothing is estimated for unlogged stages."""
    if not log_file or not request_id or not log_file.exists():
        return {}
    out: dict[str, float] = {}
    with log_file.open() as fh:
        for line in fh:
            if request_id not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("request_id") != request_id or r.get("duration_ms") is None:
                continue
            key = f"{r.get('stage') or r.get('module')}:{r.get('provider') or ''}".rstrip(":")
            out[key] = round(out.get(key, 0.0) + float(r["duration_ms"]) / 1000, 2)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--mode", required=True, help="label only, e.g. as_configured | full_ai")
    ap.add_argument("--raw-dir", required=True, help="OUTSIDE the repo; full payloads are written here")
    ap.add_argument("--log-file", default=None)
    ap.add_argument("--cases", default=None, help="comma-separated case_ids")
    ap.add_argument("--pause", type=float, default=3.0)
    ap.add_argument("--cases-file", default=str(HERE / "itinerary_qa_cases.json"))
    ap.add_argument("--summary-out", default=None)
    ap.add_argument("--rescore", action="store_true",
                    help="recompute `score` for an existing summary from the saved raw payloads (no network, no generation)")
    args = ap.parse_args()

    raw_dir = Path(args.raw_dir).resolve()
    if REPO_ROOT in raw_dir.parents or raw_dir == REPO_ROOT:
        print("refusing: --raw-dir must be outside the repository", file=sys.stderr)
        return 2
    raw_dir.mkdir(parents=True, exist_ok=True)

    spec = json.loads(Path(args.cases_file).read_text())
    if args.rescore:
        out = Path(args.summary_out) if args.summary_out else HERE / "results" / f"generation_{args.mode}.json"
        summary = json.loads(out.read_text())
        by_id = {c["case_id"]: c for c in spec["cases"]}
        for rec in summary["results"]:
            raw = raw_dir / f"{args.mode}__{rec['case_id']}.json"
            if raw.exists():
                rec["score"] = score_state(json.loads(raw.read_text()), by_id[rec["case_id"]])
        out.write_text(json.dumps(summary, indent=1))
        print("rescored ->", out)
        return 0
    defaults = spec["defaults"]
    wanted = set(args.cases.split(",")) if args.cases else None
    client = httpx.Client(base_url=args.base_url, timeout=900)
    r = client.post("/auth/signup", json={"email": f"qa-{args.mode}-{int(time.time())}@example.test", "password": "QaPassw0rd!234"})
    r.raise_for_status()

    results = []
    for case in spec["cases"]:
        if wanted and case["case_id"] not in wanted:
            continue
        start = dt.date.today() + dt.timedelta(days=int(defaults["start_offset_days"]))
        body = {
            "destination_scope": "single_city",
            "primary_destination": case["destination"],
            "origin_city": case.get("origin_city", defaults["origin_city"]),
            "start_date": str(start),
            "end_date": str(start + dt.timedelta(days=case["days"] - 1)),
            "travelers_count": case.get("travelers_count", defaults["travelers_count"]),
            "travel_group_type": case["group"],
            "pace": case["pace"],
            "interests": case.get("interests", []),
            "must_visit": case.get("must_visit", []),
            "constraints": case.get("constraints", []),
        }
        if case.get("free_text_preferences"):
            body["free_text_preferences"] = case["free_text_preferences"]
        rec: dict[str, Any] = {"case_id": case["case_id"], "mode": args.mode, "destination": case["destination"],
                               "requested_days": case["days"], "pace": case["pace"], "interests": case.get("interests", []),
                               "group": case["group"], "traits": case.get("traits")}
        t0 = time.time()
        try:
            cr = client.post("/trips", json=body)
            cr.raise_for_status()
            trip_id = cr.json()["data"]["trip_id"]
            gr = client.post(f"/trips/{trip_id}/generate")
            rec["generate_http_status"] = gr.status_code
            rec["latency_seconds"] = round(time.time() - t0, 1)
            rec["request_id"] = gr.headers.get("x-request-id")
            rec["trip_id"] = trip_id
            if gr.status_code != 200:
                rec["error"] = (gr.json().get("errors") or gr.json().get("message"))
            state = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
            (raw_dir / f"{args.mode}__{case['case_id']}.json").write_text(json.dumps(state, indent=1, default=str))
            (raw_dir / f"{args.mode}__{case['case_id']}.txt").write_text(compact_itinerary_text(state))
            rec["score"] = score_state(state, case)
            rec["logged_component_seconds"] = stage_timings(Path(args.log_file) if args.log_file else None, rec["request_id"])
        except Exception as exc:  # recorded, never hidden
            rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
            rec["latency_seconds"] = round(time.time() - t0, 1)
        results.append(rec)
        sc = rec.get("score", {})
        print(f"{rec['case_id']:9} {rec.get('latency_seconds')}s exps={sc.get('scheduled_experience_count')} stops/day={sc.get('stops_per_day')} "
              f"validation={sc.get('validation', {}).get('readiness_status')} err={rec.get('error')}", flush=True)
        time.sleep(args.pause)

    out = Path(args.summary_out) if args.summary_out else HERE / "results" / f"generation_{args.mode}.json"
    out.write_text(json.dumps({"mode": args.mode, "generated_on": str(dt.date.today()), "results": results}, indent=1))
    print("summary ->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
