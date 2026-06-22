#!/usr/bin/env python3
"""
kernel_vr_skills - Stage 2 (TRIAGE) deterministic gate.

TRIAGE judgement itself is done by LLM subagents (see .claude/skills/vr-triage/SKILL.md),
which write per-candidate verdicts to out/triage.json. This script applies the *selection
rule* deterministically so "what advances to DEEP dive" is reproducible and auditable.

Inputs:
    out/triage.json     list of verdict objects (see SKILL.md schema)
    out/candidates.json MAP output (for priority lookup + carry-forward)
Outputs:
    out/deep_queue.json candidates that advance to Stage 3 (DEEP), each with advance_reasons
    out/deferred.json   candidates held back for a later/second pass (NOT discarded)

Advancement rule (a candidate advances if ANY reason fires):
    - verdict == "risky"
    - verdict == "interesting" and 0.35 <= confidence <= 0.85   (uncertain -> escalate)
    - MAP priority == "high"                                     (structural override)
    - impact_is_dos_only and a bug is suspected                  (DO NOT drop DoS-looking
        bugs: triage's impact guess is shallow; escalate to DEEP to confirm whether it is
        truly DoS-only or actually memory corruption / info leak / privesc)

There is NO "safe" outcome and NOTHING is dropped here. A "quiet" verdict (nothing surfaced in
the fast pass) is provisional, not a safety claim: those candidates are DEFERRED to
out/deferred.json for a later pass, never written off. Impact/scope exclusion is decided ONCE,
later, in CLASSIFY.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

UNCERTAIN_LO, UNCERTAIN_HI = 0.35, 0.85


def load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    args = ap.parse_args()
    out = Path(args.out)

    triage = load(out / "triage.json", [])
    candidates = load(out / "candidates.json", [])

    priority = {c["name"]: c.get("priority") for c in candidates}
    cand_by_name = {c["name"]: c for c in candidates}

    advanced, deferred = [], []
    dos_escalations = 0
    for v in triage:
        name = v.get("name")
        verdict = (v.get("verdict") or "").lower()
        conf = float(v.get("confidence") or 0.0)
        is_high = priority.get(name) == "high"
        vclasses = [x for x in (v.get("vuln_class") or []) if x and x != "none"]
        suspected_bug = verdict in ("risky", "interesting") or bool(vclasses)

        reasons = []
        if verdict == "risky":
            reasons.append("verdict=risky")
        if verdict == "interesting" and UNCERTAIN_LO <= conf <= UNCERTAIN_HI:
            reasons.append(f"interesting+uncertain(conf={conf:g})")
        if is_high:
            reasons.append("MAP priority=high")
        if v.get("impact_is_dos_only") and suspected_bug:
            reasons.append("verify-impact: triage flagged DoS-only — DEEP must confirm "
                           "it is not memory corruption / info leak / privesc")
            dos_escalations += 1

        entry = dict(cand_by_name.get(name, {"name": name}))
        entry["triage"] = v
        if reasons:
            entry["advance_reasons"] = reasons
            advanced.append(entry)
        else:
            # "quiet" / provisional: deferred for a later pass, never discarded.
            deferred.append(entry)

    advanced.sort(key=lambda c: c.get("score", 0), reverse=True)
    deferred.sort(key=lambda c: c.get("score", 0), reverse=True)
    (out / "deep_queue.json").write_text(json.dumps(advanced, indent=2))
    (out / "deferred.json").write_text(json.dumps(deferred, indent=2))

    print(f"[TRIAGE-SELECT] {len(triage)} verdicts -> advance {len(advanced)}, "
          f"defer {len(deferred)} (of advances, {dos_escalations} escalated to verify "
          f"DoS-vs-corruption). Deferred are provisional, not safe — drain them in a 2nd pass.")
    print(f"[OUT] {out/'deep_queue.json'}  {out/'deferred.json'}")


if __name__ == "__main__":
    main()
