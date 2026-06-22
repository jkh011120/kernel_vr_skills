---
name: vr-report
description: Stage 6 (REPORT) of kernel vulnerability research. Renders the final markdown reports from out/classified.json — a full report (all buckets + coverage caveats) and a true-positives-only report — with latest-symlinks. Completes the pipeline end-to-end.
---

# vr-report — Stage 6: REPORT

Render the final reports. Everything needed is already structured in `out/classified.json`, so
this is deterministic markdown assembly — reproducible, no LLM required for the core report.

## Step 0 — Preconditions

`out/classified.json` must exist (run MAP→TRIAGE→DEEP→CLASSIFY, optionally FOLLOWUP).

## Step 1 — Render

```bash
python3 scripts/vr_report.py --out out --target "<target name>"
```

Writes (timestamped + latest-symlinks):
- `out/report_<ts>.md` — full report: True Positives, Uncertain, Out of Scope, False Positives,
  plus a **Coverage & Limits** section (deferred/unverified/MAP-excluded counts).
- `out/report_true_positives_<ts>.md` — true positives only (the actionable deliverable).
- symlinks `out/report.md`, `out/report_true_positives.md` → latest.

## Step 2 — Present

Show the user the summary line and the path to both reports. Highlight high/critical true
positives. **Surface the coverage caveats** — say plainly how many candidates were deferred
(never analyzed), how many findings are unverified, and what scope policy excluded. Do not imply
completeness if items remain deferred/unverified.

## Step 3 — Optional prose summary

If the user wants an executive narrative, read `report.md` and write a short prose summary at the
top (root-cause themes, the most serious TP, recommended next steps). The structured report stands
on its own without this.

## Notes

- Out-of-scope and false-positive findings are intentionally retained in the full report for
  audit — never present the pipeline as having "found nothing" when items sit in other buckets.
- If coverage caveats are non-trivial (many deferred/unverified), recommend a `vr-followup` pass
  before treating the report as final.
