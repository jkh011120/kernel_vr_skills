#!/usr/bin/env python3
"""
kernel_vr_skills - Stage 6 (REPORT).

Deterministically render the final reports from out/classified.json (which already holds every
structured field from DEEP+CLASSIFY). No LLM needed; this is reproducible markdown assembly.

Inputs:
    out/classified.json   findings in 4 buckets (true_positive/uncertain/out_of_scope/false_positive)
    out/excluded.json     MAP-stage surface exclusions (optional, for honest coverage)
    out/deferred.json     triage 'quiet' candidates never deep-analyzed (optional)
    out/profile.json      scope policy, for the summary (optional)
Outputs:
    out/report_<ts>.md                  full report (all buckets + coverage/limits)
    out/report_true_positives_<ts>.md   true positives only (the actionable output)
    symlinks report.md / report_true_positives.md -> latest

Coverage honesty: out_of_scope findings (real-ish bugs excluded by human policy), deferred
candidates (never analyzed), and unverified findings are all surfaced, never hidden.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def _sev_key(f):
    return SEV_ORDER.get((f.get("severity") or "").lower(), 9)


def _finding_block(i: int, f: dict, *, extra_reason_field: str | None = None) -> list[str]:
    md = []
    sev = (f.get("severity") or "?").lower()
    impact = f.get("impact_class") or f.get("vuln_class") or "?"
    claim = f.get("claim") or f.get("name") or "(no claim)"
    md.append(f"### {i}. {claim}  —  **{sev}** [{impact}]\n")
    loc = f"{f.get('file','?')}:{f.get('line','?')}"
    md.append(f"- **Location**: `{loc}`  (`{f.get('name','?')}`)")
    ver = f.get("verification") or {}
    if ver:
        md.append(f"- **Status**: {f.get('status','?')} "
                  f"(refuters {ver.get('cited_refutes',0)}/{ver.get('votes',0)})")
    else:
        md.append(f"- **Status**: {f.get('status','?')}")
    if f.get("reachable_from_user") is not None:
        md.append(f"- **Reachable from user space**: {f.get('reachable_from_user')}")
    if f.get("trigger_path"):
        md.append(f"- **Trigger path**: {' → '.join(str(s) for s in f['trigger_path'])}")
    if f.get("preconditions"):
        md.append(f"- **Preconditions**: {'; '.join(str(s) for s in f['preconditions'])}")
    if f.get("exploitability"):
        md.append(f"- **Exploitability**: {f.get('exploitability')}")
    if f.get("evidence"):
        md.append("- **Evidence**:")
        for e in f["evidence"]:
            md.append(f"    - {e}")
    if extra_reason_field and f.get(extra_reason_field):
        md.append(f"- **Reason**: {f[extra_reason_field]}")
    if f.get("classification_reason") and extra_reason_field != "classification_reason":
        md.append(f"- **Classification**: {f['classification_reason']}")
    md.append("")
    return md


def _section(title: str, items: list[dict], note: str = "", reason_field: str | None = None) -> list[str]:
    md = [f"## {title} ({len(items)})"]
    if note:
        md.append(f"_{note}_\n")
    if not items:
        md.append("_none_\n")
        return md
    for i, f in enumerate(sorted(items, key=_sev_key), 1):
        md += _finding_block(i, f, extra_reason_field=reason_field)
    return md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--target", default=None)
    ap.add_argument("--timestamp", default=None, help="override timestamp (default: now)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    classified = load(out / "classified.json", [])
    excluded = load(out / "excluded.json", [])
    deferred = load(out / "deferred.json", [])
    profile = load(out / "profile.json", {})

    ts = args.timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    target = args.target or profile.get("target_name") or "(unspecified target)"

    buckets = {"true_positive": [], "uncertain": [], "out_of_scope": [], "false_positive": []}
    for f in classified:
        buckets.get(f.get("final_status"), buckets["uncertain"]).append(f)
    unverified = [f for f in classified if f.get("status") == "unverified"]

    oos = profile.get("out_of_scope", {})
    scope_bits = []
    for k in ("impact_classes", "surface_types", "config_gates", "path_excludes"):
        if oos.get(k):
            scope_bits.append(f"{k}={oos[k]}")
    if oos.get("require_privilege"):
        scope_bits.append("require_privilege=true")
    scope_str = "; ".join(scope_bits) if scope_bits else "none (nothing excluded by policy)"

    # ---- full report ----
    full = [
        f"# Vulnerability Research Report — {target}",
        f"_generated {ts}_\n",
        "## Summary",
        f"- **Target**: {target}",
        f"- **Scope policy (out_of_scope)**: {scope_str}",
        f"- **Findings**: {len(buckets['true_positive'])} true positive, "
        f"{len(buckets['uncertain'])} uncertain, {len(buckets['out_of_scope'])} out-of-scope, "
        f"{len(buckets['false_positive'])} false positive",
        f"- **Coverage caveats**: {len(deferred)} triage candidates deferred (never deep-analyzed), "
        f"{len(unverified)} findings unverified, {len(excluded)} entrypoints excluded at MAP",
        "",
        "> Note: out-of-scope and false-positive findings are retained below for audit — nothing is silently dropped.",
        "",
    ]
    full += _section("True Positives", buckets["true_positive"],
                     "validated, reachable, in-scope vulnerabilities")
    full += _section("Uncertain", buckets["uncertain"],
                     "unresolved — candidates for FOLLOWUP (vr-followup)")
    full += _section("Out of Scope", buckets["out_of_scope"],
                     "may be real bugs, excluded by the human scope policy",
                     reason_field="classification_reason")
    full += _section("False Positives", buckets["false_positive"],
                     "refuted with cited evidence", reason_field="classification_reason")

    full += ["## Coverage & Limits",
             f"- **Deferred (triage 'quiet', not analyzed)**: {len(deferred)}"
             + (": " + ", ".join(d.get("name", "?") for d in deferred[:30]) if deferred else ""),
             f"- **Unverified findings (DEEP ran no refuter)**: {len(unverified)}"
             + (": " + ", ".join(f.get("name", "?") for f in unverified[:30]) if unverified else ""),
             f"- **MAP surface exclusions**: {len(excluded)} (see out/excluded.json)",
             "",
             "_There are no 'safe code patterns' here: deferred and unverified items are provisional, "
             "not cleared. Drain them with vr-followup before treating coverage as complete._",
             ""]

    report_name = f"report_{ts}.md"
    (out / report_name).write_text("\n".join(full), encoding="utf-8")

    # ---- true-positives-only report ----
    tp = [f"# True Positives — {target}",
          f"_generated {ts}_\n",
          f"{len(buckets['true_positive'])} validated, in-scope vulnerabilities.\n"]
    tp += _section("True Positives", buckets["true_positive"], "")
    tp_name = f"report_true_positives_{ts}.md"
    (out / tp_name).write_text("\n".join(tp), encoding="utf-8")

    # ---- symlinks to latest ----
    for link, target_name in [("report.md", report_name),
                              ("report_true_positives.md", tp_name)]:
        lp = out / link
        if lp.is_symlink() or lp.exists():
            lp.unlink()
        lp.symlink_to(target_name)

    print(f"[REPORT] TP {len(buckets['true_positive'])}, uncertain {len(buckets['uncertain'])}, "
          f"out_of_scope {len(buckets['out_of_scope'])}, FP {len(buckets['false_positive'])}")
    print(f"[OUT] {out/report_name}  {out/tp_name}  (symlinks report.md, report_true_positives.md)")


if __name__ == "__main__":
    main()
