#!/usr/bin/env python3
"""Section 202C narrator sentence audit (measurement only).

For every saved PlanningState whose narrator produced AI prose
(`narrative_source == "ai"`), classify each sentence against the structured
facts the narrator was given and report anything it cannot trace:

  - place-visit sentences: every named place must be one of THAT day's
    scheduled places (no invented / cross-day place);
  - "<place> serves <interest>": the interest must be in that place's
    provider-derived `matched_interests`;
  - "<place> category: <c>" / "(<c>)": must equal the place's category;
  - route sentences: `Route data available` must agree with movement data;
  - summary: any requested interest described as covered/served must be in
    the served set (interest mention of an UNSERVED interest is only allowed
    inside a limitation phrase such as "No approved place for X");
  - everything else is listed as UNCLASSIFIED for a human to read.

The output is evidence for a human verdict, not a verdict.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services import place_taxonomy as tx  # noqa: E402

CLAIM = {
    "popularity": r"\b(popular|famous|renowned|iconic|must[- ]see|beloved|well[- ]known|top)\b",
    "atmosphere": r"\b(charming|vibrant|lively|cozy|scenic|stunning|beautiful|picturesque|bustling|serene|immersive|peaceful|relaxing)\b",
    "history": r"\b(century|built in|founded|ancient|heritage|centuries|dating|era)\b",
    "price_hours_avail": r"[$€£]\s?\d|\bfree\b|\bcheap\b|\bopen(s)?\b|\bclos(e|es|ed)\b|\btickets?\b|\bavailability\b|\bavailable (for|on|at|until)\b|\btickets? available",
    "rating": r"\b(rated|stars?|reviews?|best|highly)\b",
    "safety_booking": r"\b(safe|safety|book(ed|ing)?|reserv\w+)\b",
}


def audit(state: dict) -> dict:
    n = state.get("itinerary_narrative_report") or {}
    plan = (state.get("experience_plan") or {}).get("daily_plans") or []
    interests = state.get("trip_request", {}).get("interests", [])
    canon = tx.canonical_interests(interests)
    exps = [e for d in plan for e in d["experiences"]]
    served = [i for i in canon if any(i in e.get("matched_interests", []) for e in exps)]
    unserved = [i for i in canon if i not in served]
    by_day = {d["day_number"]: d for d in plan}
    all_names = {e["name"] for e in exps}
    issues, unclassified, traced = [], [], 0
    movement = {b["from_experience_id"] for b in (state.get("travel_time_buffer_report") or {}).get("buffers", []) if b.get("status") == "success"}
    for dn in n.get("daily_narratives", []):
        day = by_day.get(dn["day_number"])
        if not day:
            issues.append({"day": dn["day_number"], "issue": "narrative for a day not in the plan"})
            continue
        names = [e["name"] for e in day["experiences"]]
        text = dn["narrative"]
        mentioned = [nm for nm in all_names if nm in text]
        foreign = [nm for nm in mentioned if nm not in names]
        if foreign:
            issues.append({"day": dn["day_number"], "issue": "place from another day", "places": foreign})
        missing = [nm for nm in names if nm not in text]
        if missing:
            issues.append({"day": dn["day_number"], "issue": "scheduled place not mentioned (omission, not fabrication)", "places": missing})
        for s in re.split(r"(?<=[.!?])\s+", text):
            s = s.strip()
            if not s:
                continue
            ok = False
            if s.startswith("Visit "):
                ok = True
                for m in re.finditer(r"\(([^)]*)\)", s):
                    for nm in names:
                        pass
                # "(cat, serves x)" / "(cat)" annotations
                for nm in names:
                    e = next(x for x in day["experiences"] if x["name"] == nm)
                    for m in re.finditer(re.escape(nm) + r"\s*\(([^)]*)\)", s):
                        parts = [p.strip() for p in m.group(1).split(",")]
                        cat = parts[0]
                        if cat not in (e.get("normalized_category"), e.get("category")):
                            issues.append({"day": dn["day_number"], "issue": "category mismatch", "place": nm, "said": cat})
                        for p in parts[1:]:
                            mm = re.match(r"serves (.+)", p)
                            if mm and mm.group(1) not in (e.get("matched_interests") or []):
                                issues.append({"day": dn["day_number"], "issue": "serves interest not provider-matched", "place": nm, "said": mm.group(1)})
                tail = re.sub(re.escape(", then "), ", ", s)
            m = re.match(r"(.+?) serves (.+?)\.?$", s)
            if m and not s.startswith("Visit "):
                subj = m.group(1)
                e = next((x for x in day["experiences"] if x["name"] == subj), None)
                if e is None:
                    issues.append({"day": dn["day_number"], "issue": "serves-sentence subject not a scheduled place of the day", "subject": subj})
                else:
                    said = re.split(r" and |, ", m.group(2).strip("."))
                    bad = [w for w in said if w not in (e.get("matched_interests") or [])]
                    if bad:
                        issues.append({"day": dn["day_number"], "issue": "serves interest not provider-matched", "place": subj, "said": bad})
                    ok = True
            m = re.match(r"(.+?) category: (.+?)\.?$", s)
            if m:
                e = next((x for x in day["experiences"] if x["name"] == m.group(1)), None)
                if e is None or m.group(2).strip(".") not in (e.get("normalized_category"), e.get("category")):
                    issues.append({"day": dn["day_number"], "issue": "category sentence mismatch", "sentence": s[:120]})
                ok = True
            if re.match(r"Route data available(: (yes|no))?\.?$", s):
                has = any(e["experience_id"] in movement for e in day["experiences"])
                said_yes = "no" not in s.lower().split(":")[-1] if ":" in s else True
                if said_yes != has:
                    issues.append({"day": dn["day_number"], "issue": "route-data statement disagrees with movement data", "sentence": s})
                ok = True
            if s.startswith("Known limitations:") or s.startswith("No approved place for"):
                ok = True
            if ok:
                traced += 1
            else:
                unclassified.append({"day": dn["day_number"], "sentence": s[:200]})
            for k, pat in CLAIM.items():
                # strip place names first so "Historic District" in a NAME is not a claim
                scrub = s
                for nm in sorted(all_names, key=len, reverse=True):
                    scrub = scrub.replace(nm, "")
                scrub = re.sub(r"serv(es|ing)( requested interest:)? [\w, ]+", "", scrub)
                if re.search(pat, scrub, re.I):
                    issues.append({"day": dn["day_number"], "issue": f"possible {k} claim", "sentence": s[:160]})
    summary = n.get("summary") or ""
    scrub = summary
    for nm in all_names:
        scrub = scrub.replace(nm, "")
    said_covered = []
    raw_to_canon = {r.lower(): (tx.canonical_interests([r]) or [None])[0] for r in interests}
    for raw, c in raw_to_canon.items():
        if c and (raw in scrub.lower() or raw.rstrip("s") in scrub.lower() or c in scrub.lower()):
            said_covered.append((raw, c))
    misstated = [(r, c) for r, c in said_covered if c in unserved and not re.search(r"no approved place|not available|no place|limit", scrub, re.I)]
    for k, pat in CLAIM.items():
        if re.search(pat, scrub, re.I):
            issues.append({"issue": f"summary possible {k} claim", "sentence": summary[:200]})
    return {
        "served": served, "unserved": unserved, "requested": interests,
        "summary": summary, "summary_mentions": said_covered,
        "summary_claims_unserved_interest_as_covered": misstated,
        "traced_sentences": traced, "unclassified_sentences": unclassified, "issues": issues,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--mode", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = {}
    for f in sorted(Path(args.raw_dir).glob(f"{args.mode}__*.json")):
        st = json.loads(f.read_text())
        n = st.get("itinerary_narrative_report") or {}
        cid = f.stem.split("__")[1]
        out[cid] = {"source": n.get("narrative_source"), "status": n.get("status"), "model": n.get("model")}
        if n.get("narrative_source") == "ai":
            out[cid]["audit"] = audit(st)
    Path(args.out).write_text(json.dumps(out, indent=1))
    ai = {k: v for k, v in out.items() if "audit" in v}
    print("AI narrations audited:", len(ai))
    for k, v in ai.items():
        a = v["audit"]
        real = [i for i in a["issues"] if "not mentioned" not in i["issue"]]
        print(f"{k}: traced={a['traced_sentences']} unclassified={len(a['unclassified_sentences'])} issues={len(real)} omissions={len(a['issues'])-len(real)} unserved-as-covered={a['summary_claims_unserved_interest_as_covered']}")
        for i in real[:6]:
            print("    ", json.dumps(i)[:230])
        for u in a["unclassified_sentences"][:3]:
            print("     UNCLASSIFIED:", json.dumps(u)[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
