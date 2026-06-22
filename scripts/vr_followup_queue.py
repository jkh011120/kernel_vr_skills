#!/usr/bin/env python3
"""
kernel_vr_skills - Stage 5 (FOLLOWUP) deterministic queue builder + loop state.

Builds the next round's analysis queue by chasing follow-up symbols from unresolved findings
and (optionally) draining the deferred triage queue. Maintains round + dedup state so the loop
TERMINATES. The analysis itself reuses Stage 3 (vr-deep) on the produced queue.

Inputs:
    out/classified.json      findings with final_status + followup_symbols
    out/symbols.json         symbol name -> [{file,line}] (persisted by MAP)
    out/deferred.json        triage 'quiet' queue (only drained with --include-deferred)
    out/followup_state.json  {round, analyzed: [keys]} (created/updated here)
Output:
    out/followup_queue.json  candidates for vr-deep this round (deep_queue.json-compatible)

Rules:
    - dedup by "symbol@file"; never re-queue something already analyzed (seeded from existing
      findings so we don't re-chase them)
    - chase followup_symbols only from findings that are still open (true_positive / uncertain);
      skip false_positive and out_of_scope (the latter is a deliberate human exclusion)
    - deferred items are provisional 'quiet' triage candidates -> drained only on opt-in
    - --max-rounds stops the loop; --max-targets bounds per-round work
Nothing is dropped; this only decides what to look at NEXT.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def key(name, file):
    return f"{name}@{file or ''}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--max-rounds", type=int, default=2)
    ap.add_argument("--max-targets", type=int, default=8)
    ap.add_argument("--include-deferred", action="store_true",
                    help="also drain the triage 'quiet' deferred queue this round")
    args = ap.parse_args()
    out = Path(args.out)

    classified = load(out / "classified.json", [])
    symbols = load(out / "symbols.json", {})
    deferred = load(out / "deferred.json", [])
    state = load(out / "followup_state.json", {"round": 0, "analyzed": []})

    rnd = state.get("round", 0)
    analyzed = set(state.get("analyzed", []))
    # seed: everything that is already a finding counts as analyzed
    for f in classified:
        analyzed.add(key(f.get("name"), f.get("file")))

    if rnd >= args.max_rounds:
        (out / "followup_queue.json").write_text("[]")
        print(f"[FOLLOWUP] max-rounds ({args.max_rounds}) reached — nothing more to queue")
        return

    targets = []

    def add_symbol(sym, source):
        if len(targets) >= args.max_targets:
            return
        locs = symbols.get(sym) or []
        if not locs:
            return
        loc = locs[0]
        k = key(sym, loc.get("file"))
        if k in analyzed:
            return
        analyzed.add(k)
        targets.append({"name": sym, "file": loc.get("file"), "line": loc.get("line"),
                        "context": "", "followup_source": source})

    # 1) chase followup_symbols from still-open findings
    for f in classified:
        if f.get("final_status") in ("false_positive", "out_of_scope"):
            continue
        for sym in (f.get("followup_symbols") or []):
            add_symbol(sym, f"finding:{f.get('name')}")

    # 2) optionally drain deferred 'quiet' triage candidates
    if args.include_deferred:
        for c in deferred:
            if len(targets) >= args.max_targets:
                break
            k = key(c.get("name"), c.get("file"))
            if k in analyzed:
                continue
            analyzed.add(k)
            targets.append({**c, "followup_source": "deferred"})

    (out / "followup_queue.json").write_text(json.dumps(targets, indent=2))
    (out / "followup_state.json").write_text(json.dumps(
        {"round": rnd + 1, "analyzed": sorted(analyzed)}, indent=2))

    print(f"[FOLLOWUP] round {rnd + 1}/{args.max_rounds}: queued {len(targets)} new targets "
          f"({'incl. deferred' if args.include_deferred else 'findings only'})")
    if not targets:
        print("[FOLLOWUP] queue empty — loop can stop")
    print(f"[OUT] {out/'followup_queue.json'} (+ followup_state.json)")


if __name__ == "__main__":
    main()
