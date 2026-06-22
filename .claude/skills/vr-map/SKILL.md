---
name: vr-map
description: Stage 1 (MAP) of kernel vulnerability research. Profiles the target with an LLM, indexes symbols, finds user-reachable entrypoints, scores candidate functions, and proposes an out-of-scope policy for human approval. Produces out/candidates.json for later stages.
---

# vr-map — Stage 1: MAP

Turn a kernel source tree into a ranked, scoped list of candidate functions worth
analyzing. No vulnerability judgement happens here — this stage decides **where to look**.

Flow: **PROFILE → INDEX → SURFACE → SELECT → ENRICH → SCOPE-PROPOSE**. The middle four
are deterministic (`scripts/vr_map.py`); PROFILE and SCOPE-PROPOSE are your jobs.

## Step 0 — Resolve the target

Read `configs/vr-config.json` for `target.repo` / `target.scope` / `target.name`.
If the user passed `--repo` / `--scope` on the command, those win. If neither is set,
ask the user for the kernel path and the driver/subsystem subdirectory.

## Step 1 — PROFILE (method B: LLM generates the pattern set)

If `out/profile.json` already exists, ask the user whether to reuse it (skip to Step 2)
or regenerate. Otherwise generate it:

1. Sample the target so you understand it (do NOT read everything):
   - `git -C <repo> ls-files <scope>` to see the file layout
   - read 2–4 representative `.c` files and the main ioctl/uapi header
2. Spawn an **Explore** (or general-purpose) subagent with this task:
   > Read the sampled files of `<scope>`. Identify (a) how user space reaches this
   > driver (ioctl/mmap/read/write/debugfs registration sites) and (b) the risky
   > operation idioms it uses (allocators, refcounting, user-copy, locking). Return a
   > profile JSON matching `profiles/profile.schema.json`: `surface_patterns`,
   > `entrypoint_name_patterns`, `indicator_patterns` (user_control/lifetime/concurrency/guards).
   > Leave `out_of_scope` empty. All patterns are Python `re` regexes.
3. Write the returned JSON to `out/profile.json`. Show the user the surface/indicator
   patterns and let them tweak before continuing.

## Step 2 — Run the deterministic core (INDEX → SURFACE → SELECT → ENRICH)

```bash
python3 scripts/vr_map.py --repo <repo> --scope <scope> --profile out/profile.json --out out
```

This writes `out/index.json`, `out/candidates.json`, `out/excluded.json` and prints a
summary line per phase. tree-sitter is preferred for symbols/call-graph; it falls back to
regex if tree-sitter `tags`/`query` isn't configured. Every candidate is tagged with
`surface_type`, `privileged` (behind `capable()`/`CAP_*`), and `config_gates`
(enclosing `CONFIG_*`, kept because all `#ifdef` branches are preserved — method-1 tagging).

## Step 3 — SCOPE-PROPOSE (LLM proposes, human decides)

The first run leaves `out_of_scope` empty, so nothing is dropped yet — everything is tagged.
Now build the scope menu:

1. Read `out/candidates.json`. Summarize the out-of-scope *candidates* present:
   - entrypoints by `surface_type` (e.g. how many `debugfs`, `sysfs`, `module_param`)
   - functions that are `privileged`
   - functions sitting under notable `config_gates` (e.g. `CONFIG_*_DEBUG`)
2. Write `out/scope_proposal.md` listing these with concrete examples (name + file:line).
3. Ask the user (AskUserQuestion) what, if anything, to exclude. Nothing is excluded by
   default — present options like "debugfs surface", "DEBUG config gates", "root-gated
   handlers", and the engagement-policy question "do you accept DoS-only bugs?" Make clear
   that DoS is a valid vulnerability by default; excluding it is an explicit choice for this
   engagement only.
4. Write their decision into `out/profile.json` → `out_of_scope`
   (`surface_types`, `require_privilege`, `config_gates`, `path_excludes`, `impact_classes`).
   Leave fields empty if the user excludes nothing. Note: `impact_classes` (e.g. `"dos"`) is
   recorded here but enforced later in CLASSIFY against DEEP's verified impact, not in MAP.

## Step 4 — Re-run to apply scope, then report

```bash
python3 scripts/vr_map.py --repo <repo> --scope <scope> --profile out/profile.json --out out
```

Now `out/excluded.json` lists what was dropped and why (never silent). Present to the user:
- candidate count, top ~10 by score (name, file:line, score, surface_type)
- how many were excluded and the breakdown of reasons

`out/candidates.json` is the input to Stage 2 (TRIAGE).

## Parameters

| Flag | Default | Meaning |
|------|---------|---------|
| `--repo` | from config | kernel source root |
| `--scope` | from config | subdir to analyze (relative to repo) |
| `--max-candidates` | 25 | cap on candidates kept |
| `--max-depth` | 3 | call-graph reachability depth from entrypoints |
