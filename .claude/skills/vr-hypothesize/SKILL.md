---
name: vr-hypothesize
description: HYPOTHESIZE step of the top-down loop. An LLM subagent reads the architecture model and emits bug hypotheses through three lenses (attacker, contract/invariant, developer role-play), always mixing broad and concrete levels, into the living out/hypotheses.json.
---

# vr-hypothesize — generate/refine bug hypotheses

Turn the architecture model into testable bug hypotheses. Keep a **mix of abstraction levels** so
the search stays wide (no tunnel vision), and apply the no-safe-patterns rule: broad hypotheses
are never closed as safe.

## Step 0 — Inputs

Read `out/arch_model.json` (especially `optimizations` and their `invariants`, and `open_questions`),
the current `out/hypotheses.json`, and `out/explore_queue.json` (broad hypotheses the scheduler
asked to deepen this round).

## Step 1 — HYPOTHESIZE (subagent), three lenses

> From this architecture model, produce bug hypotheses as a delta. Generate through THREE lenses:
> 1. **Attacker**: where does user-controlled input flow into a dangerous operation?
> 2. **Contract/invariant**: for each invariant in the model, where might code VIOLATE it?
> 3. **Developer role-play**: "if I were writing/maintaining this code, which parts would I be
>    nervous about getting right?" — e.g. wrap-around handling, error-path cleanup (missed/double
>    free), a callback reachable from both IRQ and syscall (missing lock), page-vs-byte unit
>    mix-ups. Your unease itself is a hypothesis.
>
> ALWAYS produce a MIX of `level`s — not only concrete:
> - `area`: a broad direction (e.g. "CSF object lifetime is complex → UAF somewhere"). Keeps the
>   search wide. target_symbols may be empty.
> - `mechanism`: a specific mechanism/invariant to scrutinize.
> - `concrete`: a specific, DEEP-testable claim with `target_symbols` and a precise `why`.
> Link each to its source with `from_optimization` / `from_invariant` when applicable. For broad
> hypotheses being deepened (from explore_queue), emit `concrete` children.
> Output ONLY JSON matching profiles/hypo_delta.example.json. Do NOT set status=refuted on
> area/mechanism (you cannot prove an area safe — the merge enforces this).

Write to `out/hypo_delta.json`.

## Step 2 — MERGE

```bash
python3 scripts/vr_hypo_merge.py --out out
```

Integrates into `out/hypotheses.json`. Reports level/status counts and how many refute attempts on
broad hypotheses were guarded back to "open". Hand off to the TARGET scheduler (vr-hypo-loop).
