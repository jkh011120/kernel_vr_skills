---
name: vr-hypo-loop
description: Orchestrates the top-down hypothesis-driven analysis loop — STUDY the architecture, HYPOTHESIZE bugs (3 lenses, mixed breadth/depth), TARGET, analyze with vr-deep, and feed results back to deepen both the model and the hypotheses, until convergence. Complements the bottom-up MAP→…→REPORT pipeline.
---

# vr-hypo-loop — top-down, self-correcting analysis loop

A complement (or alternative) to the bottom-up pipeline: understand the design, hypothesize where
it breaks, test, and feed what you learn back so understanding and hypotheses deepen each round.
Reuses vr-deep / vr-classify / vr-report. Can run standalone or after the bottom-up pass.

Living stores (delta+merge, never rewritten): `out/arch_model.json` (→ `architecture.md`) and
`out/hypotheses.json`. Helpers: `vr_arch_merge.py`, `vr_hypo_merge.py`, `vr_hypo_queue.py`.

## Per round

1. **STUDY** — run the `vr-arch` skill: subagent reads the least-understood slice (lowest
   confidence / open_questions / areas from `explore_queue.json` / areas touched by recent
   findings) → `arch_delta.json` → `vr_arch_merge.py` updates `arch_model.json` + `architecture.md`.
2. **HYPOTHESIZE** — run the `vr-hypothesize` skill: subagent reads the model (optimizations +
   invariants) → bug hypotheses via 3 lenses (attacker / contract / developer role-play), mixing
   area/mechanism/concrete → `hypo_delta.json` → `vr_hypo_merge.py` updates `hypotheses.json`.
3. **TARGET** — schedule with balance:
   ```bash
   python3 scripts/vr_hypo_queue.py --out out --depth 6 --breadth 4
   ```
   concrete → `deep_queue.json` (depth), area/mechanism → `explore_queue.json` (breadth, fed back
   to STUDY/HYPOTHESIZE next round).
4. **ANALYZE** — run the `vr-deep` skill on `deep_queue.json` (ANALYZE+VERIFY), then
   `vr_deep_merge.py --append`, then `vr-classify`. Each deep target carries its `hypothesis_id`
   and the hypothesis `why` as the checklist.
5. **FEEDBACK** — close the loop deterministically via deltas:
   - For each finding that resolves a hypothesis, write a `status` op (confirmed / refuted /
     needs_info) into `hypo_delta.json` and run `vr_hypo_merge.py` (the merge keeps area/mechanism
     open even if a refute is attempted — no-safe-patterns).
   - For anything DEEP learned that contradicts/extends the model, write a `refine`/`add` into
     `arch_delta.json` and run `vr_arch_merge.py`. This is what makes the model self-correcting
     and progressively more detailed.

## Termination

Stop when ALL hold (or a round/budget cap is hit):
- `vr_arch_merge.py` reported 0 add for K consecutive rounds (model converged), AND
- no `open` `concrete` hypotheses remain (all confirmed / refuted / needs_info), AND
- the round cap (default ~3–5) or token budget is reached.

Broad (area/mechanism) hypotheses staying open is EXPECTED and not a reason to keep looping
forever — they persist by design (you can't prove an area safe), so the cap bounds the loop.

## Output / handoff

Findings flow into the same `findings.json` / `classified.json`, so **Stage 6 `vr-report`**
renders everything (bottom-up + hypothesis-driven) in one report. `architecture.md` is a valuable
standalone artifact for the human. Report per-round: model version, new/confirmed/refuted
hypotheses, and new findings.
