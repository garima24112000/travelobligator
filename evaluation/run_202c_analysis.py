#!/usr/bin/env python3
"""Section 202C analysis helper (measurement only; nothing here touches the app).

Reads the full PlanningState payloads a generation run saved OUTSIDE the
repository (`--raw-dir`, files `<mode>__<CASE>.json`) plus the run summary,
and writes a compact digest with the facts Section 202C needs that the 202A
runner's `score` does not carry: narrator source, AI proposal attempts and
rejection reasons, promoted/grounded counts, places-stage outcome, per-day
categories and matched interests, and a per-sentence narrator vocabulary
trace. It never assigns PASS/WARN/FAIL -- those are human labels recorded
in `202c_human_labels.json` and merged by `--render-review`.

No secrets, prompts or raw provider payloads are read or written.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

UNSUPPORTED = {
    "price": re.compile(r"[$€£]\s?\d|\bcheap\b|\bexpensive\b|\bfree (entry|admission)\b", re.I),
    "rating": re.compile(r"\bhighly[- ]rated\b|\btop[- ]rated\b|\bacclaimed\b|\bpopular\b|\bfamous\b|\biconic\b|\bmust[- ]see\b|\bstars?\b", re.I),
    "hours": re.compile(r"\bopen(s)? (until|from|at|daily)\b|\bcloses? at\b|\b\d{1,2}\s?(am|pm)\b", re.I),
    "availability": re.compile(r"\bsold out\b|\btickets?\b|\bno (queue|line)s?\b", re.I),
    "booking": re.compile(r"\bbook(ed|ing)?\b|\breserv(e|ation)s?\b", re.I),
    "safety": re.compile(r"\bsafe(st|ty)?\b|\bdangerous\b", re.I),
    "atmosphere": re.compile(r"\b(charming|vibrant|scenic|stunning|beautiful|lively|cozy|picturesque|breathtaking|bustling|serene|immersive)\b", re.I),
}
_ATTEMPTS = re.compile(r"after (\d+) attempt")


def _proposal(state: dict) -> dict:
    result = (state.get("ai_candidate_proposal_batch") or {}).get("result") or {}
    blocked = (result.get("guardrail_report") or {}).get("blocked_reasons") or []
    m = _ATTEMPTS.search(" ".join(blocked))
    promo = state.get("ai_candidate_promotion_report") or {}
    grounding = state.get("candidate_grounding_batch") or {}
    g_result = grounding.get("result") or {}
    return {
        "status": result.get("status"),
        "proposal_count": len(result.get("proposals") or []),
        "attempts_reported": int(m.group(1)) if m else (1 if result else None),
        "structural_retry_used": bool(m and int(m.group(1)) > 1),
        "rejection_reason": (blocked[0][:160] if blocked else None),
        "rejected_raw_items": len(result.get("rejected_raw_items") or []),
        "grounding_status": g_result.get("status") if isinstance(g_result, dict) else None,
        "grounded_count": len([r for r in (g_result.get("results") or []) if isinstance(r, dict) and r.get("status") in ("grounded", "matched")]) if isinstance(g_result, dict) else None,
        "promotion_status": promo.get("status"),
        "reviewed": promo.get("total_reviewed_candidates"),
        "promoted": promo.get("promoted_count"),
    }


def _narrator(state: dict) -> dict:
    n = state.get("itinerary_narrative_report") or {}
    return {
        "status": n.get("status"),
        "source": n.get("narrative_source"),
        "provider": n.get("provider"),
        "message": (n.get("message") or "")[:160] or None,
    }


def _vocab(state: dict) -> set[str]:
    words: set[str] = set()
    req = state.get("trip_request") or {}
    texts = [req.get("primary_destination", "")]
    for d in (state.get("experience_plan") or {}).get("daily_plans", []):
        for e in d.get("experiences", []):
            texts += [e.get("name", ""), e.get("category", ""), e.get("normalized_category") or ""]
        for r in d.get("restaurant_suggestions") or []:
            texts.append((r or {}).get("name", ""))
    for t in texts:
        words.update(w.lower() for w in re.findall(r"[\wÀ-ÿ'’-]+", t))
    return words


def narration_trace(state: dict) -> dict:
    """Sentence-level check of AI narrator prose against the supplied vocabulary
    (place names, categories, destination) plus unsupported-claim patterns.
    Returns per-sentence flags for HUMAN review; fallback prose is listed too."""
    n = state.get("itinerary_narrative_report") or {}
    vocab = _vocab(state)
    sentences: list[dict] = []
    texts = [n.get("summary") or ""] + [
        f"{dn.get('narrative') or ''}" for dn in n.get("daily_narratives") or []
    ]
    for text in texts:
        for s in re.split(r"(?<=[.!?])\s+", text.strip()):
            if not s:
                continue
            caps = [w for w in re.findall(r"(?<!^)(?<![.!?]\s)\b[A-ZÀ-Ý][\wÀ-ÿ'’-]*", s)]
            unknown_caps = [w for w in caps if w.lower() not in vocab]
            hits = {k: sorted({m.group(0).lower() for m in p.finditer(s)}) for k, p in UNSUPPORTED.items()}
            hits = {k: v for k, v in hits.items() if v}
            sentences.append({"sentence": s[:220], "unknown_capitalised": unknown_caps, "claim_hits": hits})
    flagged = [s for s in sentences if s["unknown_capitalised"] or s["claim_hits"]]
    return {"source": n.get("narrative_source"), "sentence_count": len(sentences), "flagged": flagged}


def digest(state: dict, rec: dict) -> dict:
    plan = (state.get("experience_plan") or {}).get("daily_plans") or []
    dctx = state.get("destination_context") or {}
    vr = state.get("validation_report") or {}
    score = rec.get("score") or {}
    days = []
    for d in plan:
        days.append({
            "day": d["day_number"],
            "places": [
                {
                    "name": e["name"],
                    "category": e.get("category"),
                    "normalized_category": e.get("normalized_category"),
                    "matched_interests": e.get("matched_interests") or [],
                    "provider_source": e.get("provider_source"),
                    "has_provider_place_id": bool(e.get("provider_place_id")),
                    "has_coordinates": bool(e.get("coordinates")),
                    "promoted_from_ai": bool(e.get("promoted_from_ai")),
                }
                for e in d.get("experiences", [])
            ],
        })
    findings = [
        {"severity": i.get("severity"), "category": i.get("category"), "message": (i.get("message") or "")[:200]}
        for i in [*(vr.get("critical_issues") or []), *(vr.get("warnings") or [])]
        if i.get("severity") != "suggestion"
    ]
    coverage = state.get("provider_coverage") or {}
    route = state.get("route_feasibility_report") or {}
    return {
        "case_id": rec["case_id"],
        "requested_destination": (state.get("trip_request") or {}).get("primary_destination"),
        "resolved_destination": (dctx.get("resolved_destination") or {}).get("display_name"),
        "interests": rec.get("interests"),
        "pace": rec.get("pace"),
        "days_requested": rec.get("requested_days"),
        "latency_seconds": rec.get("latency_seconds"),
        "http": rec.get("generate_http_status"),
        "provider_status": {k: coverage.get(k) for k in ("places", "restaurants", "routes", "weather", "holidays", "currency", "flights", "accommodations")},
        "ai": {
            "proposal": _proposal(state),
            "reasoning": (state.get("ai_itinerary_reasoning_result") or {}).get("status"),
            "repair": (state.get("ai_itinerary_repair_result") or {}).get("status") if isinstance(state.get("ai_itinerary_repair_result"), dict) else None,
            "repair_attempts": state.get("ai_itinerary_repair_attempt_count"),
            "narrator": _narrator(state),
        },
        "route": {"status": route.get("status"), "legs": score.get("routing", {}).get("leg_feasibility_counts"), "sequencing": score.get("routing", {}).get("sequencing_status")},
        "days": days,
        "stops_per_day": score.get("stops_per_day"),
        "empty_days": score.get("empty_days"),
        "duplicates": score.get("duplication", {}).get("repeated_normalized_names"),
        "days_far_apart": score.get("geography", {}).get("days_far_apart"),
        "max_pairwise_km": [d.get("max_pairwise_km") for d in score.get("geography", {}).get("per_day", [])],
        "unsupported_identity_count": score.get("grounding", {}).get("unsupported_identity_count"),
        "all_stable_ids": score.get("grounding", {}).get("all_have_stable_ids"),
        "coordinates": f"{score.get('grounding', {}).get('with_coordinates')}/{score.get('scheduled_experience_count')}",
        "claim_sources": f"{score.get('grounding', {}).get('with_claim_sources')}/{score.get('scheduled_experience_count')}",
        "supply_per_interest": (score.get("candidate_supply") or {}).get("viable_supply_per_matched_interest"),
        "interests_matched": (score.get("scheduled_categories") or {}).get("interests_matched"),
        "low_value_or_gallery": (score.get("scheduled_categories") or {}).get("low_value_or_gallery_count"),
        "quality_finding_categories": score.get("quality_finding_categories"),
        "readiness": vr.get("readiness_status"),
        "findings": findings,
        "factual_fields_non_null": [f["path"] + "=" + f["value"] for f in (score.get("factual_looking_fields_non_null") or [])][:12],
        "narration_trace": narration_trace(state),
        "candidate_pool": (score.get("grounding") or {}).get("candidate_pool"),
    }


def aggregate(digests: list[dict]) -> dict:
    lat = [d["latency_seconds"] for d in digests if d.get("latency_seconds") is not None]
    prop = [d["ai"]["proposal"]["status"] for d in digests]
    narr = [d["ai"]["narrator"]["source"] or d["ai"]["narrator"]["status"] for d in digests]

    def count(xs):
        out: dict[str, int] = {}
        for x in xs:
            out[str(x)] = out.get(str(x), 0) + 1
        return out

    return {
        "cases": len(digests),
        "http_200": sum(1 for d in digests if d.get("http") == 200),
        "empty_day_count": sum(len(d["empty_days"] or []) for d in digests),
        "cases_with_empty_day": sum(1 for d in digests if d["empty_days"]),
        "cross_day_duplicate_count": sum(len(d["duplicates"] or {}) for d in digests),
        "unsupported_identity_count": sum(d["unsupported_identity_count"] or 0 for d in digests),
        "scheduled_total": sum(sum(len(day["places"]) for day in d["days"]) for d in digests),
        "scheduled_with_provider_place_id": sum(1 for d in digests for day in d["days"] for p in day["places"] if p["has_provider_place_id"]),
        "scheduled_with_coordinates": sum(1 for d in digests for day in d["days"] for p in day["places"] if p["has_coordinates"]),
        "days_far_apart_cases": [d["case_id"] for d in digests if d["days_far_apart"]],
        "low_value_or_gallery_total": sum(d["low_value_or_gallery"] or 0 for d in digests),
        "proposal_status": count(prop),
        "proposal_structural_retry_used": sum(1 for d in digests if d["ai"]["proposal"]["structural_retry_used"]),
        "narrator": count(narr),
        "places_status": count(d["provider_status"].get("places") for d in digests),
        "restaurants_status": count(d["provider_status"].get("restaurants") for d in digests),
        "routes_status": count(d["provider_status"].get("routes") for d in digests),
        "readiness": count(d["readiness"] for d in digests),
        "latency_median_s": statistics.median(lat) if lat else None,
        "latency_mean_s": round(statistics.mean(lat), 1) if lat else None,
        "latency_min_max_s": [min(lat), max(lat)] if lat else None,
        "interest_supply_limited_cases": sum(1 for d in digests if "interest_supply_limited" in (d["quality_finding_categories"] or [])),
        "interest_undercoverage_cases": sum(1 for d in digests if "interest_undercoverage" in (d["quality_finding_categories"] or [])),
        "narration_flagged_sentences": sum(len(d["narration_trace"]["flagged"]) for d in digests),
    }


def render_review(digests: list[dict], labels: dict, mode: str, out: Path) -> None:
    lines = [
        f"# Section 202C — human itinerary review ({mode})",
        "",
        f"Reviewer: {labels.get('reviewer', 'n/a')}",
        "",
        "PASS/WARN/FAIL = itinerary quality; YES/MAYBE/NO = would I use it as a starting point. "
        "Labels are a preference proxy, not the product owner's own; no numeric score is computed. "
        "No secrets, prompts or raw provider payloads are included.",
        "",
    ]
    by_id = {c["case_id"]: c for c in labels.get("cases", [])}
    for d in digests:
        lab = by_id.get(d["case_id"], {})
        ai = d["ai"]
        lines += [
            f"## {d['case_id']} — {d['requested_destination']}",
            "",
            f"- resolved destination: {d['resolved_destination']}",
            f"- requested interests: {', '.join(d['interests'] or [])} | pace: {d['pace']} | days: {d['days_requested']}",
            f"- narrator: {ai['narrator']['source'] or ai['narrator']['status']} | AI proposal: {ai['proposal']['status']} | places provider: {d['provider_status'].get('places')} | routing: {d['route']['status']} | latency: {d['latency_seconds']}s",
            f"- interests matched by scheduled places: {', '.join(d['interests_matched'] or []) or 'none'} | quality findings: {', '.join(d['quality_finding_categories'] or []) or 'none'}",
        ]
        for day in d["days"]:
            places = "; ".join(
                f"{p['name']} [{p['normalized_category'] or p['category']}"
                + (f" → {'/'.join(p['matched_interests'])}" if p["matched_interests"] else "")
                + "]"
                for p in day["places"]
            )
            lines.append(f"- Day {day['day']}: {places or '(no places)'}")
        important = [f for f in d["findings"] if f["category"] in ("empty_day", "thin_day", "interest_undercoverage", "interest_supply_limited", "category_concentration", "duplicate_experience", "feasibility", "destination_unresolved")]
        for f in important[:5]:
            lines.append(f"- finding ({f['severity']}/{f['category']}): {f['message']}")
        lines += [
            f"- **{lab.get('quality', '?')} / {lab.get('personal_use', '?')}** — {lab.get('note', '(no note)')}",
            "",
        ]
    out.write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--mode", required=True)
    ap.add_argument("--summary", required=True, help="the run summary json written by run_itinerary_qa.py")
    ap.add_argument("--out", required=True)
    ap.add_argument("--render-review", default=None, help="path of a labels json; renders markdown to --review-out")
    ap.add_argument("--review-out", default=None)
    args = ap.parse_args()

    summary = json.loads(Path(args.summary).read_text())
    digests = []
    for rec in summary["results"]:
        raw = Path(args.raw_dir) / f"{args.mode}__{rec['case_id']}.json"
        if not raw.exists():
            continue
        digests.append(digest(json.loads(raw.read_text()), rec))
    Path(args.out).write_text(json.dumps({"mode": args.mode, "aggregate": aggregate(digests), "cases": digests}, indent=1))
    print("digest ->", args.out)
    if args.render_review:
        render_review(digests, json.loads(Path(args.render_review).read_text()), args.mode, Path(args.review_out))
        print("review ->", args.review_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
