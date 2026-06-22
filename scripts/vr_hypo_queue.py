#!/usr/bin/env python3
"""
kernel_vs_skills - hypothesis TARGET scheduler (breadth/depth balance).

Turns open hypotheses into work, keeping EXPLORE (broad) and EXPLOIT (deep) in balance so the
loop never collapses into one rabbit hole:

    concrete  hypotheses -> out/deep_queue.json  (DEPTH: vr-deep tests them now)
    area/mechanism        -> out/explore_queue.json (BREADTH: next STUDY/HYPOTHESIZE deepens them)

Inputs:  out/hypotheses.json, out/symbols.json
Outputs: out/deep_queue.json, out/explore_queue.json, out/hypotheses.json (statuses updated)

Selection:
    - concrete open: take up to --depth; resolve each target_symbol via symbols.json to file:line;
      dedup by symbol@file; mark hypothesis status "queued"; pass why/bug_class as a DEEP checklist
    - area/mechanism open: take up to --breadth, least-recently-touched first (rotation via
      last_round bump); kept "open" (no-safe-patterns: broad directions are never closed here)
Nothing is dropped; this only schedules what to look at next.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BROAD = {"area", "mechanism"}


def load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--depth", type=int, default=6, help="concrete hypotheses -> deep_queue")
    ap.add_argument("--breadth", type=int, default=4, help="area/mechanism -> explore_queue")
    args = ap.parse_args()
    out = Path(args.out)

    hyps = load(out / "hypotheses.json", [])
    symbols = load(out / "symbols.json", {})
    by_id = {h["id"]: h for h in hyps if h.get("id")}

    open_concrete = [h for h in hyps if h.get("status") == "open" and h.get("level") == "concrete"]
    open_broad = [h for h in hyps if h.get("status") == "open" and h.get("level") in BROAD]

    # DEPTH: concrete -> deep_queue
    deep_queue, seen = [], set()
    for h in open_concrete[:args.depth]:
        targets = h.get("target_symbols") or []
        resolved_any = False
        for sym in targets:
            locs = symbols.get(sym) or []
            loc = locs[0] if locs else {}
            k = f"{sym}@{loc.get('file','')}"
            if k in seen:
                continue
            seen.add(k)
            resolved_any = True
            deep_queue.append({
                "name": sym, "file": loc.get("file"), "line": loc.get("line"), "context": "",
                "hypothesis_id": h["id"], "bug_class": h.get("bug_class"),
                "advance_reasons": [f"hypothesis {h['id']} ({h.get('level')}): {h.get('why','')}"],
                "triage": {"deep_dive_questions": [h.get("why", "")],
                           "impact_is_dos_only": False, "vuln_class": [h.get("bug_class", "none")]},
            })
        if not targets:
            # concrete but no symbol resolved -> let vr-deep search by area
            deep_queue.append({"name": h.get("area", h["id"]), "file": None, "line": None,
                               "context": "", "hypothesis_id": h["id"],
                               "advance_reasons": [f"hypothesis {h['id']}: {h.get('why','')}"],
                               "triage": {"deep_dive_questions": [h.get("why", "")]}})
            resolved_any = True
        if resolved_any:
            by_id[h["id"]]["status"] = "queued"

    # BREADTH: area/mechanism -> explore_queue (rotation via last_round)
    open_broad.sort(key=lambda h: h.get("last_round", 0))
    explore_queue = []
    for h in open_broad[:args.breadth]:
        explore_queue.append({"id": h["id"], "level": h.get("level"), "area": h.get("area"),
                              "bug_class": h.get("bug_class"), "why": h.get("why"),
                              "from_optimization": h.get("from_optimization"),
                              "from_invariant": h.get("from_invariant")})
        h["last_round"] = h.get("last_round", 0) + 1  # rotate so others get picked next time

    (out / "deep_queue.json").write_text(json.dumps(deep_queue, indent=2))
    (out / "explore_queue.json").write_text(json.dumps(explore_queue, indent=2))
    (out / "hypotheses.json").write_text(json.dumps(hyps, indent=2))

    print(f"[HYPO-QUEUE] depth: {len(deep_queue)} deep targets from {min(len(open_concrete),args.depth)} "
          f"concrete | breadth: {len(explore_queue)} areas to explore "
          f"(open: {len(open_concrete)} concrete, {len(open_broad)} broad)")
    print(f"[OUT] {out/'deep_queue.json'}  {out/'explore_queue.json'}")


if __name__ == "__main__":
    main()
