---
name: vr-triage
description: Stage 2 (TRIAGE) of kernel vulnerability research. Quickly screens each MAP candidate into risky/interesting/benign with an LLM subagent, then deterministically selects which advance to the DEEP stage. Consumes out/candidates.json, produces out/triage.json and out/deep_queue.json.
---

# vr-triage — Stage 2: TRIAGE

Cheap first-pass screening. The goal is to cut the ~25 MAP candidates down to the handful
worth a slow, careful DEEP dive. This is a **shallow, fast judgement** — do NOT do full
vulnerability analysis here (that's Stage 3).

Flow: **SCREEN (LLM subagents) → SELECT (deterministic gate)**.

## Step 0 — Preconditions

`out/candidates.json` must exist (run `vr-map` first). Read it. If empty, stop and tell the
user to re-run MAP with a looser scope.

## Step 1 — SCREEN (fan out triage subagents)

**Core principle — there is no "safe code pattern."** Triage may NOT conclude that code is
safe. The lowest tier means "nothing surfaced in a fast pass" (unverified), never "no bug
exists." A confident claim of safety from a shallow look is exactly how real bugs get missed.
Every verdict — including the lowest tier — must list what a deeper pass would need to check.

Process candidates in parallel batches (e.g. spawn ~5 subagents at a time, each handling a
few candidates) to keep it fast. Use the **Explore** agent type. Give each subagent this task,
substituting the candidate's `name`, `file`, `line`, and `context` from candidates.json:

> You are triaging a Linux kernel function for vulnerability-research worth. This is a FAST
> screen, not a full audit. Here is the function (from candidates.json `context`); you may
> Read the file for callers/structs if needed, but stay brief.
>
> Rule: you may NOT declare code safe. "I didn't find a bug quickly" is not "there is no bug."
> Always fill `deep_dive_questions` with what a deeper pass would have to verify — even for the
> lowest verdict.
>
> Decide:
> - `verdict`: "risky" (plausible memory-safety / privilege bug reachable from the entrypoint),
>   "interesting" (worth a look but unclear), or "quiet" (nothing surfaced in this fast pass —
>   UNVERIFIED, not safe; deprioritized, not dismissed).
> - `risk`: low | medium | high | critical
> - `confidence`: 0.0–1.0 that your verdict reflects a fast pass (NOT confidence that the code
>   is safe — you cannot be confident of that). A "quiet" verdict should keep confidence modest.
> - `vuln_class`: any of uaf, double_free, oob_read, oob_write, int_overflow, race, refcount,
>   type_confusion, info_leak, dos, none (list, may be multiple)
> - `impact_is_dos_only`: true if the worst outcome you can see from a quick look is a
>   crash/hang/leak. IMPORTANT: this is only a shallow first impression — it never excludes a
>   candidate. If you set it true AND you suspect a real bug, Stage 3 will dig in specifically
>   to confirm whether it is truly DoS-only or actually escalatable (UAF/OOB → corruption,
>   info leak, privesc). So add a `deep_dive_question` asking exactly that.
> - `rationale`: 1–3 sentences, cite specific line numbers from the context
> - `deep_dive_questions`: concrete things Stage 3 must verify (e.g. "is `size` bounded before
>   the kmalloc at line N?")
>
> Return ONLY a JSON object with exactly those keys, plus `name`, `file`, `line` echoed back.

Collect every subagent's JSON object into a single array and write it to `out/triage.json`.
(If you use a Workflow with a schema, validation is automatic; otherwise validate the keys
yourself before writing.)

## Step 2 — SELECT (deterministic advancement gate)

```bash
python3 scripts/vr_triage_select.py --out out
```

This reads `out/triage.json` + `out/candidates.json` and writes `out/deep_queue.json` (what
advances to Stage 3). A candidate advances if ANY of these fire (recorded in `advance_reasons`):

- `verdict == "risky"`
- `verdict == "interesting"` and confidence in 0.35–0.85 (uncertain → escalate)
- MAP `priority == "high"` (structural override; protects against the LLM under-rating an entrypoint)
- `impact_is_dos_only` **and** a bug is suspected → escalate to confirm DoS-vs-corruption.
  **Nothing is dropped for being DoS** — triage's impact guess is too shallow to trust; a
  "looks like just a crash" call is exactly what DEEP must re-examine. Impact/scope exclusion
  is decided once, later, in CLASSIFY.

"quiet" candidates with no suspected bug are **deferred** (written to `out/deferred.json`,
kept in `out/triage.json`) — NOT discarded. Deferral is a resource decision (analyze the most
suspicious first), never a safety conclusion. They remain fully analyzable.

## Second pass (drain the deferred queue)

There are no safe patterns, so a "quiet" verdict is provisional. When the `deep_queue` has been
worked through and budget/time remains, run a deeper pass over `out/deferred.json` (feed it to
Stage 3 the same way). The deferred list exists precisely so nothing is silently written off.

## Step 3 — Report

Summarize for the user:
- counts: screened / advanced / parked / dropped-as-DoS
- the advanced list (name, file:line, risk, vuln_class, one-line rationale), highest score first
- note that `out/deep_queue.json` is the input to Stage 3 (DEEP)

## Notes

- Keep triage cheap: prefer the candidate `context` already in candidates.json; only Read the
  source when the verdict genuinely depends on a caller or struct definition.
- The verdict band 0.35–0.85 is intentional: very-confident "interesting" calls are treated as
  decided (not worth a deep dive either way), uncertain ones get escalated.
