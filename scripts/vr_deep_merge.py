#!/usr/bin/env python3
"""
kernel_vr_skills - Stage 3 (DEEP) deterministic merge.

The deep analysis and the adversarial verification are done by LLM subagents
(see .claude/skills/vr-deep/SKILL.md). This script aggregates their output into a final
per-finding status using a fixed, auditable voting rule.

Inputs:
    out/findings_raw.json   list of findings from the ANALYZE subagents
    out/verifications.json  list of verdicts from the VERIFY subagents (>=1 per finding),
                            each: {name, refuted: bool, reason: str, missed_guard: str?}
Output:
    out/findings.json       findings, each annotated with verification[] + status + counts

Voting rule (per finding, grouped by name):
    A refute vote counts ONLY if refuted==true AND it cites concrete evidence (non-empty
    reason) -- "looks safe" with no citation does not count (no-safe-patterns principle).
        no verifier ran            -> status "unverified"   (keep; needs a verify pass)
        cited-refutes > half        -> status "refuted"      (kept, not deleted)
        zero cited-refutes          -> status "confirmed"
        otherwise (split)           -> status "uncertain"
Nothing is dropped: refuted findings are retained with their status and the refutation reason,
so a human can audit and overturn. Severity/scope rulings belong to the CLASSIFY stage.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
STATUS_ORDER = {"confirmed": 0, "uncertain": 1, "unverified": 2, "refuted": 3}


def load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--append", action="store_true",
                    help="merge into existing findings.json (for FOLLOWUP rounds) instead of overwriting")
    args = ap.parse_args()
    out = Path(args.out)

    findings = load(out / "findings_raw.json", [])
    verifications = load(out / "verifications.json", [])

    by_name: dict[str, list[dict]] = {}
    for v in verifications:
        by_name.setdefault(v.get("name"), []).append(v)

    merged = []
    counts = {"confirmed": 0, "uncertain": 0, "refuted": 0, "unverified": 0}
    for f in findings:
        votes = by_name.get(f.get("name"), [])
        cited_refutes = [v for v in votes if v.get("refuted") and (v.get("reason") or "").strip()]
        n = len(votes)
        if n == 0:
            status = "unverified"
        elif len(cited_refutes) > n / 2:
            status = "refuted"
        elif len(cited_refutes) == 0:
            status = "confirmed"
        else:
            status = "uncertain"
        counts[status] += 1
        merged.append({
            **f,
            "status": status,
            "verification": {
                "votes": n,
                "cited_refutes": len(cited_refutes),
                "details": votes,
            },
        })

    if args.append:
        existing = load(out / "findings.json", [])
        seen = {(e.get("name"), e.get("file")) for e in existing}
        merged = existing + [m for m in merged if (m.get("name"), m.get("file")) not in seen]
    merged.sort(key=lambda x: (STATUS_ORDER.get(x["status"], 9),
                               SEV_ORDER.get((x.get("severity") or "").lower(), 9)))
    (out / "findings.json").write_text(json.dumps(merged, indent=2))

    print(f"[DEEP-MERGE] {len(findings)} findings: "
          f"confirmed {counts['confirmed']}, uncertain {counts['uncertain']}, "
          f"refuted {counts['refuted']}, unverified {counts['unverified']} "
          f"(refuted are kept, not deleted)")
    print(f"[OUT] {out/'findings.json'}")


if __name__ == "__main__":
    main()
