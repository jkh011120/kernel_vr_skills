---
name: vr-followup
description: Stage 5 (FOLLOWUP, optional) of kernel vulnerability research. Iteratively resolves open findings by chasing the symbols they reference and (optionally) draining the deferred triage queue, re-running DEEP + CLASSIFY each round until nothing new or the round cap is hit. Consumes out/classified.json, updates out/findings.json and out/classified.json.
---

# vr-followup — Stage 5: FOLLOWUP (optional, iterative)

Kernel bugs cross function boundaries: DEEP often leaves a finding `uncertain` because the
answer lives in a caller or callee. FOLLOWUP chases those `followup_symbols`, analyzes them with
DEEP, re-classifies, and repeats — bounded by a round cap so it terminates. This is the engine
of "nothing is written off": it drains `uncertain` findings and the `deferred` triage queue.

Run it only when you want more depth/completeness; the pipeline is already complete without it.

Flow per round: **QUEUE (deterministic) → ANALYZE+VERIFY (vr-deep) → MERGE(append) → CLASSIFY → loop**.

## Step 0 — Preconditions

`out/classified.json` and `out/symbols.json` must exist (run MAP→TRIAGE→DEEP→CLASSIFY first).

## Step 1 — Build this round's queue

```bash
python3 scripts/vr_followup_queue.py --out out [--include-deferred] [--max-rounds 2] [--max-targets 8]
```

This resolves `followup_symbols` from still-open findings (true_positive / uncertain; it skips
false_positive and out_of_scope) to `file:line` via `symbols.json`, dedups against everything
already analyzed, and writes `out/followup_queue.json`. With `--include-deferred` it also pulls
the triage "quiet" candidates from `out/deferred.json`. A round counter in
`out/followup_state.json` enforces `--max-rounds`.

**If `followup_queue.json` is empty → stop. The loop is done.**

## Step 2 — Analyze + verify the queue (reuse vr-deep)

Run the Stage 3 (vr-deep) ANALYZE and VERIFY steps using `out/followup_queue.json` as the
candidate list (instead of deep_queue.json). The candidates may have empty `context` — that's
fine, the analyze subagent Reads the source at `file:line` itself. Write the new findings to
`out/findings_raw.json` and the refuter verdicts to `out/verifications.json`.

## Step 3 — Merge (append, don't overwrite)

```bash
python3 scripts/vr_deep_merge.py --out out --append
```

`--append` merges this round's findings into the cumulative `out/findings.json` (dedup by
name+file), keeping prior rounds.

## Step 4 — Re-classify

Re-run Stage 4 (vr-classify) so the new findings get TP/FP/uncertain + scope enforcement, and
previously `uncertain` findings that are now resolved get updated. This refreshes
`out/classified.json`.

## Step 5 — Loop

Go back to Step 1. The queue builder increments the round and will return an empty queue once
`--max-rounds` is reached or no new symbols remain — that's the termination signal. Then report:
how many rounds ran, new findings per round, and the final bucket counts. Hand off to Stage 6
(REPORT).

## Notes

- Termination is guaranteed by `--max-rounds` + dedup (`followup_state.json`). To start a fresh
  followup campaign, delete `out/followup_state.json`.
- Deferred draining is opt-in because deferred items are provisional "quiet" candidates, not
  unresolved findings — pursue real open findings first, then deferred if budget remains.
