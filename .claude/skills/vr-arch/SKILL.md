---
name: vr-arch
description: STUDY step of the top-down hypothesis loop. An LLM subagent reads a slice of the target and emits ONLY deltas (new/corrected architecture knowledge, especially optimizations and the invariants they impose) into the living out/arch_model.json, re-rendering out/architecture.md.
---

# vr-arch — STUDY (build/deepen the architecture model)

Understand how the target is built — especially its **performance optimizations**, because an
optimization trades safety for speed, and the **invariants** it forces on the rest of the code
are where bugs live. The model is a living store updated by deltas; you never rewrite it whole.

## Step 0 — Inputs / what to study this round

- If `out/arch_model.json` exists, read it. Pick the **least-understood** target this round:
  lowest-confidence components, any `open_questions`, or areas touched by recent `out/findings.json`.
  On the first round, do a broad overview pass instead.
- Seed yourself with existing artifacts if present: `out/profile.json`, `out/index.json`,
  `out/symbols.json`, `out/candidates.json`, and `out/explore_queue.json` (areas the scheduler
  asked to deepen). Don't read the whole tree — read the slice relevant to this round.

## Step 1 — STUDY (subagent)

Spawn a subagent (general-purpose) to read that slice and report what it learned:

> Study this slice of the kernel driver. Report, as a delta, ONLY what is new or what corrects a
> prior entry (do not restate the whole model). For each component and especially each
> **optimization** (lockless/RCU, fast paths that skip checks, caching, custom allocators, shared
> HW memory, batching, deferred work), capture:
> - what it is, where (files/symbols), what safety it trades for speed
> - **invariants**: the rules any code touching it MUST honor (e.g. "head/tail must be revalidated
>   vs ring size each use", "must hold csf.lock when mutating queue list", "returned ptr invalid
>   after unlock"). These are the contracts whose violation = a bug.
> If recent findings contradict the current model, emit a `refine` (or `retract`) op for that id
> with a note explaining the correction.
> Output ONLY JSON matching profiles/arch_delta.example.json (components / optimizations /
> open_questions; each may include an `invariants` list). Use add / refine / retract / answer ops.

Write the result to `out/arch_delta.json`.

## Step 2 — MERGE

```bash
python3 scripts/vr_arch_merge.py --out out
```

Integrates the delta into `out/arch_model.json` (history-tracked, self-correcting) and re-renders
`out/architecture.md`. Report the version bump and add/refine/retract counts.

This step feeds vr-hypothesize. In the loop, STUDY runs again each round on the next
least-understood part, so the model gets progressively more detailed without losing prior detail.
