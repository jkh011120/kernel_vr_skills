#!/usr/bin/env python3
"""
kernel_vr_skills - Stage 4 (CLASSIFY) deterministic enforcement.

The TP/FP/uncertain judgement + impact_class normalization is done by an LLM subagent
(see .claude/skills/vr-classify/SKILL.md) into out/classifications.json. This script reconciles
that with the DEEP status and applies the impact/scope policy ONE place, deterministically.
This is the AUTHORITATIVE and only place scope/impact (e.g. DoS) exclusion happens.

Inputs:
    out/findings.json         DEEP output (each has status: confirmed/uncertain/refuted/unverified)
    out/classifications.json  LLM CLASSIFY output: {name, impact_class, classification, rationale}
    out/profile.json          out_of_scope.impact_classes (e.g. ["dos"])
Output:
    out/classified.json       each finding with final_status (+ reason), nothing deleted

Reconciliation (per finding, by name):
    DEEP status == "refuted"     -> false_positive   (adversarial cited refute wins)
    DEEP status == "unverified"  -> uncertain        (never auto-TP without verification)
    else                          -> use the LLM classification (conservative: FP only if cited)
Scope enforcement (applied AFTER the above):
    if final would be true_positive/uncertain AND impact_class in out_of_scope.impact_classes
        -> out_of_scope   (kept, with reason; the bug may be real but is not in accepted scope)

Buckets: true_positive | uncertain | out_of_scope | false_positive. All retained.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

VALID = {"true_positive", "false_positive", "uncertain"}
BUCKET_ORDER = {"true_positive": 0, "uncertain": 1, "out_of_scope": 2, "false_positive": 3}
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    args = ap.parse_args()
    out = Path(args.out)

    findings = load(out / "findings.json", [])
    classifications = load(out / "classifications.json", [])
    profile = load(out / "profile.json", {})
    oos_impact = set(profile.get("out_of_scope", {}).get("impact_classes") or [])

    cls_by_name = {c.get("name"): c for c in classifications}

    result = []
    counts = {"true_positive": 0, "uncertain": 0, "out_of_scope": 0, "false_positive": 0}
    for f in findings:
        name = f.get("name")
        c = cls_by_name.get(name, {})
        status = f.get("status")
        impact_class = (c.get("impact_class") or f.get("impact_class") or "none").lower()
        llm_class = (c.get("classification") or "uncertain").lower()
        if llm_class not in VALID:
            llm_class = "uncertain"

        # reconcile with DEEP status
        if status == "refuted":
            final, reason = "false_positive", "DEEP adversarial refute (cited)"
        elif status == "unverified":
            final, reason = "uncertain", "DEEP could not verify (no verifier ran)"
        else:
            final, reason = llm_class, (c.get("rationale") or "")

        # scope/impact enforcement — the single authoritative place
        if final in ("true_positive", "uncertain") and impact_class in oos_impact:
            final = "out_of_scope"
            reason = f"impact_class '{impact_class}' excluded by out_of_scope.impact_classes; " \
                     f"(was {llm_class}) — bug may be real but not in accepted scope"

        counts[final] += 1
        result.append({
            **f,
            "impact_class": impact_class,
            "final_status": final,
            "classification_reason": reason,
            "llm_classification": llm_class,
        })

    result.sort(key=lambda x: (BUCKET_ORDER.get(x["final_status"], 9),
                               SEV_ORDER.get((x.get("severity") or "").lower(), 9)))
    (out / "classified.json").write_text(json.dumps(result, indent=2))

    print(f"[CLASSIFY] {len(findings)} findings -> TP {counts['true_positive']}, "
          f"uncertain {counts['uncertain']}, out_of_scope {counts['out_of_scope']}, "
          f"FP {counts['false_positive']} (all retained)")
    print(f"[OUT] {out/'classified.json'}")


if __name__ == "__main__":
    main()
