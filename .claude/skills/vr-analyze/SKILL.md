---
name: vr-analyze
description: Top-level entry point for kernel vulnerability research. Give it a repo (and scope) and it drives the whole pipeline — MAP → TRIAGE → DEEP → CLASSIFY → FOLLOWUP → hypothesis loop → REPORT. Default is maximum coverage: fully automatic with both loops on. Flags (--interactive, --no-followup, --no-hypo-loop, --quick) reduce it.
---

# vr-analyze — run the whole pipeline from one entry point

Orchestrates every stage so the user only provides a target. It does not re-implement anything:
it invokes the stage skills (`vr-map`, `vr-triage`, `vr-deep`, `vr-classify`, `vr-followup`,
`vr-hypo-loop`, `vr-report`) in order and checks the `out/*.json` handoffs between them.

## Step 0 — Resolve + cache the target (repo is required)

```bash
python3 scripts/vr_target.py [--repo <path>] [--scope <subdir>] [--name <label>]
```

This resolves the target (args > `configs/vr-config.json` target.* ) and **caches args back into
the config**, so once you pass `--repo`/`--scope` they are remembered — a later bare `/vr-analyze`
reuses them. It prints the resolved `{repo, scope, name}`. If it errors with "no repo", ASK the
user for the kernel path and the subsystem subdir (do not guess), then re-run with those args.

Echo back the resolved `repo`, `scope`, mode, and options before starting.

### Step 0b — Fresh run (prevent cross-target contamination)

`out/` accumulates state (profile, candidates, findings, arch_model, followup_state, …). Running
a NEW target over an old `out/` mixes results. So by default, start fresh:

```bash
rm -f out/*.json out/*.md          # default: clear stale artifacts
```

Skip this if `--resume` is passed (continue a prior run of the SAME target). If the cached target
differs from a previous run, always clear regardless.

## Options

**Default = maximum coverage: fully automatic, with BOTH the followup loop and the top-down
hypothesis loop enabled.** Flags only REDUCE scope/effort.

| Flag | Default | Meaning |
|------|---------|---------|
| `--repo` / `--scope` | from config | target (see precedence above) |
| `--interactive` | off (auto) | pause at the two human-decision points (profile + scope) |
| `--no-followup` | off (followup ON) | skip the Stage 5 followup loop |
| `--no-hypo-loop` | off (hypo-loop ON) | skip the top-down hypothesis loop |
| `--quick` | off | bottom-up core only: MAP→TRIAGE→DEEP→CLASSIFY→REPORT (no followup, no hypo-loop) |
| `--resume` | off | keep existing `out/` artifacts (continue same target); default starts fresh |
| `--rounds N` | 2 | round cap for followup / hypo-loop |

## Mode: auto (default) vs --interactive

The only difference is whether it PAUSES at human-decision points (profile + scope). Everything
else is identical. In both modes `out/scope_proposal.md` is generated; auto just doesn't wait.

- **auto (default)**: use the generated profile as-is and the default scope (`out_of_scope` empty
  → nothing excluded, DoS in scope — consistent with "exclude nothing unless you opt in"). Run
  straight through. Afterward, tell the user they can review `out/scope_proposal.md` and re-run
  with `--interactive` (or edit `out/profile.json`) if they want to exclude something.
- **--interactive**: pause to confirm the auto-generated `profile.json` patterns, and pause at the
  scope decision (what to exclude — debugfs / DoS-only / config gates). Apply the user's answers.

## Pipeline sequence

1. **MAP** — run `vr-map`.
   - auto (default): run PROFILE + core once with empty `out_of_scope`; still write
     `scope_proposal.md`.
   - --interactive: do its PROFILE step, then PAUSE for profile confirmation; run the core; then
     PAUSE at SCOPE-PROPOSE for the user's exclusions; re-run the core to apply them.
   - Gate: `out/candidates.json` must be non-empty. If empty, stop and report (suggest a looser
     scope or check tree-sitter).
2. **TRIAGE** — run `vr-triage` → `out/deep_queue.json` (+ `deferred.json`).
3. **DEEP** — run `vr-deep` on `deep_queue.json` → `out/findings.json`.
4. **CLASSIFY** — run `vr-classify` → `out/classified.json`.
5. **FOLLOWUP** (default ON; skipped by `--no-followup` or `--quick`) — run `vr-followup` for up
   to `--rounds`, draining uncertain findings (and deferred with its `--include-deferred`).
6. **HYPOTHESIS LOOP** (default ON; skipped by `--no-hypo-loop` or `--quick`) — run `vr-hypo-loop`
   for up to `--rounds`. Its findings land in the same `findings.json` / `classified.json`, so
   they merge in.
7. **REPORT** — run `vr-report --target "<name>"` → `out/report.md` + `out/report_true_positives.md`.

Between stages, sanity-check the handoff file exists and is non-empty; if a stage produced
nothing, say so plainly (do not silently continue as if it succeeded).

## Step N — Present

Summarize end-to-end: target + mode, candidate count, the four CLASSIFY buckets, the top true
positives (claim / severity / location), and the coverage caveats (deferred / unverified /
excluded). Give paths to `report.md`, `report_true_positives.md`, and (if run) `architecture.md`.
If coverage caveats are non-trivial, recommend `--with-followup` / `--with-hypo-loop`.

## Notes

- This is a Claude-driven skill, not a shell one-liner: the deterministic scripts run via Bash,
  but the analysis stages spawn subagents, which only Claude can do. "One command" = invoking this
  one skill.
- Nothing is dropped anywhere; out-of-scope / FP / deferred are retained and surfaced in the report.
