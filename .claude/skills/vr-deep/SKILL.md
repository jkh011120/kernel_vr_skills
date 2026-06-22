---
name: vr-deep
description: Stage 3 (DEEP) of kernel vulnerability research. Deeply analyzes each triage-advanced candidate (data-flow tracing, reachability, true impact), then adversarially verifies each finding with independent refuters. Consumes out/deep_queue.json, produces out/findings.json.
---

# vr-deep — Stage 3: DEEP

The slow, careful stage. Take each candidate that survived TRIAGE and decide whether a real,
reachable vulnerability exists — and what its TRUE impact is. Then try hard to disprove every
finding before trusting it.

Flow: **ANALYZE (per-candidate) → VERIFY (adversarial) → MERGE (deterministic)**.

Carry forward the no-safe-patterns principle: you may not conclude code is safe by
pattern-matching. A "not a bug" call must rest on concrete evidence (a specific guard that
provably blocks the path, an invariant the framework guarantees, a path that cannot be reached
from user space) — cite the line.

## Step 0 — Preconditions

`out/deep_queue.json` must exist (run `vr-triage` first). Read it. Each entry carries the MAP
candidate (`name`, `file`, `line`, `context`, reachability) plus its `triage` verdict
(including `deep_dive_questions` and any `verify-impact` escalation). If budget remains after
this queue, repeat the whole flow on `out/deferred.json` (see vr-triage second pass).

## Step 1 — ANALYZE (one subagent per candidate)

Spawn a thorough subagent per candidate (general-purpose; fan out a few at a time). It should
Read the real source freely — callers, callees, struct definitions, lock context. Task:

> Determine whether `<name>` (`<file>:<line>`) contains a real, user-reachable vulnerability.
> Work through the triage `deep_dive_questions` as your checklist. Steps:
> 1. Control/data flow: where does attacker-controlled input enter, and does it reach the
>    suspect operation? Trace it concretely with line numbers.
> 2. Reachability: is there a real path from a user-space entrypoint, given every guard on the
>    way? If a guard blocks it, name the guard and line.
> 3. TRUE impact: if a bug exists, what is the worst realistic outcome — DoS only, or memory
>    corruption (read/write), info leak, or privilege escalation? Resolve any `verify-impact`
>    escalation from triage here; do not default to "just DoS".
> 4. Conclusion.
>
> Return ONLY a JSON object: `name`, `file`, `line`, `is_vulnerability` (bool),
> `vuln_class` (string), `severity` (low|medium|high|critical), `true_impact` (string),
> `reachable_from_user` (bool), `trigger_path` (list of "symbol (file:line)" steps),
> `preconditions` (list), `evidence` (list of "line N: ..." citations), `exploitability`
> (likely|requires-conditions|unclear), `claim` (one-sentence statement of the bug),
> `followup_symbols` (list of OTHER function names whose behavior you'd need to analyze to
> fully resolve this — e.g. a caller that may or may not bound a size, a callee that frees;
> these drive Stage 5 FOLLOWUP).
> If you find no bug, set `is_vulnerability=false` and in `evidence` cite the SPECIFIC guard /
> invariant / infeasible step that rules it out (not "looks safe").

Collect every object into `out/findings_raw.json`.

## Step 2 — VERIFY (adversarial, independent refuters)

For each finding in `findings_raw.json` (you may focus on `is_vulnerability==true`, but a
`false` with weak evidence deserves a refuter too), spawn **3 independent** subagents whose job
is to REFUTE it. Give each a different lens: (a) correctness/guards, (b) reachability from user
space, (c) does the claimed impact actually hold. Task:

> Try to REFUTE this finding: <claim + trigger_path + evidence>. Read the source yourself.
> A refutation only counts if you cite concrete evidence: a specific guard at a line that
> blocks the path, an invariant that prevents the state, or a reason the path is unreachable
> from user space. "It looks safe" is NOT a refutation. Default to refuted=false if you cannot
> cite something concrete.
>
> HARD RULES (a refutation that violates any of these does NOT count — set refuted=false):
> 1. CHECK ≠ GUARD. If you cite a re-check / re-validation / status re-read as protective, you
>    MUST show that the relevant branch either (a) returns an error, (b) actually performs the
>    required state update (cite the write), or (c) cancels the dangerous action. A branch that
>    DETECTS a changed state but then SKIPS a required update while STILL RETURNING SUCCESS is
>    the BUG, not a guard.
> 2. ANTI-STRAWMAN. Do not refute by attacking a slightly-different mechanism than the finding's.
>    If the finding's *exact* mechanism is wrong but the SAME window/state/object yields a
>    different bad outcome, that is NOT a refutation — set refuted=false and say so.
> 3. LOCK ALONE IS NOT A REFUTATION. "an isolation refcount / lock serializes it" only counts if
>    you show that lock covers the EXACT object and timepoint the finding claims corruption for
>    (a refcount protecting an object *during* an operation does NOT protect a stale pointer left
>    *after* a success-return). Cite the line proving the coverage.
> 4. DISTRUST COMMENTS. A code comment asserting safety ("state can no longer change here") is not
>    evidence; verify it against the actual lock / state-transition code before relying on it.
> Return ONLY: `name`, `refuted` (bool), `reason` (your cited evidence, or why you could not
> refute), `missed_guard` (string, optional).

For any finding whose `vuln_class` is a **race / TOCTOU / check-then-act** (or whose claim hinges
on a dropped-then-reacquired lock), add this question to every lens: *"On the re-validation
branch, is any required state update skipped while success is still returned? If so, what frees /
reuses the object afterward?"* — and NEVER let such a finding be closed by a single verdict; the
full 3-lens panel is mandatory (see Step 2b).

Collect all verdicts (multiple per finding) into `out/verifications.json`.

### Step 2b — Mandatory panel for race / TOCTOU findings (no single-verdict kill)

A `refuted` verdict on a race / TOCTOU / check-then-act finding is only valid if the **full 3-lens
panel ran** and a majority cited a rule-compliant refutation (per the HARD RULES above). Never
flip such a finding to `refuted` from a single deep-analysis note. If only one verdict exists for
such a finding, treat it as `uncertain` and re-queue it for the full panel. (This is the failure
mode that produced a false-negative on a real page-migration race: a single note cited a
re-validation as a "guard" when that branch actually skipped a required `pages[]` update and still
returned success.)

## Step 3 — MERGE (deterministic status)

```bash
python3 scripts/vr_deep_merge.py --out out
```

Writes `out/findings.json`. Per finding, status is decided by a fixed vote rule: a refute
counts only if it cites evidence; majority cited-refutes → `refuted`, zero → `confirmed`, split
→ `uncertain`, no verifier → `unverified`. **Refuted findings are kept, not deleted** (status
attached, reason preserved) so they remain auditable.

## Step 4 — Report

Summarize: confirmed / uncertain / refuted / unverified counts, then list confirmed findings
(claim, severity, true_impact, trigger_path) highest severity first. Note that `findings.json`
feeds Stage 4 (CLASSIFY), which makes the final TP/FP call and applies impact/scope (e.g. DoS)
exclusion.
