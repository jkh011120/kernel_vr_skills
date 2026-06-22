---
name: vr-classify
description: Stage 4 (CLASSIFY) of kernel vulnerability research. Makes the final true-positive / false-positive / uncertain call for each DEEP finding and applies the authoritative impact/scope policy (e.g. DoS exclusion). Consumes out/findings.json, produces out/classified.json.
---

# vr-classify — Stage 4: CLASSIFY

The policy/reporting layer. DEEP decided the technical question ("is there a real, reachable
bug?"). CLASSIFY decides the reporting question ("do we report this, and as what?") and applies
the scope policy. **This is the one and only place impact/scope exclusion (e.g. DoS) happens** —
on top of DEEP's verified `true_impact`, never on a shallow earlier guess.

Flow: **CLASSIFY (LLM, conservative) → ENFORCE (deterministic)**.

Keep the no-safe-patterns principle: only call something a false positive with concrete cited
evidence (a guard DEEP confirmed, a framework guarantee). When in doubt → uncertain, not FP.

## Step 0 — Preconditions

`out/findings.json` must exist (run `vr-deep` first). Read it. Each finding carries the DEEP
`status` (confirmed / uncertain / refuted / unverified), `true_impact`, `vuln_class`,
`trigger_path`, `evidence`.

## Step 1 — CLASSIFY (LLM, conservative)

For each finding (focus on confirmed/uncertain; refuted/unverified are handled deterministically
in Step 2 but you may still annotate them), assign the final call. Batch a few per subagent.

> Make the final reporting call for this finding: <finding summary: claim, true_impact,
> trigger_path, evidence, DEEP status>. Read the source to confirm if needed.
> Return ONLY JSON: `name`,
> - `impact_class`: normalize the true impact to one of memory_corruption | info_leak | privesc
>   | dos | none
> - `classification`: true_positive | false_positive | uncertain. Be CONSERVATIVE: mark
>   false_positive only with concrete cited evidence (a guard that provably blocks it, a
>   framework invariant). If you are not sure it is safe, say uncertain — never false_positive.
> - `severity`: low | medium | high | critical (impact-adjusted)
> - `rationale`: 1–3 sentences citing lines.

Collect into `out/classifications.json`.

## Step 2 — ENFORCE (deterministic policy)

```bash
python3 scripts/vr_classify.py --out out
```

Writes `out/classified.json`. Rules (auditable, fixed):
- DEEP `status == refuted` → `false_positive` (the cited adversarial refute wins)
- DEEP `status == unverified` → `uncertain` (never auto-promote to TP without verification)
- otherwise → the LLM's conservative classification
- **scope**: if the result would be true_positive/uncertain and `impact_class` is in
  `profile.out_of_scope.impact_classes` (e.g. `dos`) → reclassified `out_of_scope`, with a reason.
  The bug may be real; it is simply not in accepted scope. **Nothing is deleted** — out_of_scope
  and false_positive are retained buckets.

## Step 3 — Report

Summarize the four buckets (true_positive / uncertain / out_of_scope / false_positive). List the
true positives (claim, severity, impact_class, trigger_path) first, then uncertain ones that may
deserve FOLLOWUP. `classified.json` feeds Stage 6 (REPORT); `uncertain` items and the deferred
triage queue feed Stage 5 (FOLLOWUP).
