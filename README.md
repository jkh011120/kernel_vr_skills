# kernel_vr_skills

Claude Code **skills for kernel Vulnerability Research (VR)** — a static analysis pipeline that
finds memory-safety / privilege bugs in Linux kernel drivers by combining two complementary
search strategies:

- **bottom-up** — scan patterns, score functions, analyze the suspicious ones.
- **top-down** — *understand* the driver's architecture (especially its performance
  optimizations) and *reason* about where the design breaks, as testable hypotheses.

Both feed the same verification/classification/report back-end, so one run produces one report.

It is built **native to Claude Code**: LLM subagents do the judging, small deterministic Python
scripts do the deciding/aggregating. There is no LLM-orchestration framework and (almost) no
dependency — the analysis loop is driven by Claude following the skills.

---

## Design principles

These run through every stage:

1. **LLM judges, scripts decide.** Subagents read code and form opinions; deterministic scripts
   apply the selection/voting/merge rules — so *why* a result happened is reproducible and auditable.
2. **There is no "safe code pattern."** No stage may conclude code is safe. "I didn't find a bug
   in a fast pass" ≠ "there is no bug." The lowest triage tier is *quiet* (unverified), not *benign*.
3. **Nothing is dropped.** Out-of-scope, false-positive, refuted, deferred items are all retained
   with a reason and surfaced in the report. The report states what was **not** covered.
4. **Knowledge is delta + merge, never rewritten.** The architecture model and hypotheses
   accumulate and self-correct (wiki-style add / refine / retract with history).
5. **DoS is a valid bug by default.** Scope exclusion (DoS, debugfs, config gates…) is an explicit
   human opt-in, enforced in one place (CLASSIFY) against verified impact — never on a shallow guess.

---

## Requirements

- **Claude Code** (the skills run inside it; subagents are spawned by Claude).
- **tree-sitter CLI** with the C grammar/tags configured — used for symbol indexing and the call
  graph. If absent, the scripts fall back to regex (lower accuracy).
- **git** (for `git ls-files`) and **Python 3.10+**.
- **No pip dependencies** — the scripts use only the Python standard library.

---

## Quick start

```bash
# 0. One-time setup (checks Python/git, installs tree-sitter + C grammar, self-checks):
git clone <this-repo> && cd kernel_vr_skills
./setup.sh

# 1. Point it at a target once (cached to configs/vr-config.json afterward):
/vr-analyze --repo <kernel-source-root> --scope <subsystem-subdir>

# 2. From then on, just:
/vr-analyze
```

> Run from the repo root — the scripts use repo-relative paths (`configs/`, `out/`).
> If `setup.sh` can't install tree-sitter, the scripts still run via a regex fallback (lower
> accuracy); install tree-sitter (cargo/npm/brew) + re-run setup for full accuracy.

- `--repo` = the kernel source tree root.
- `--scope` = the subdirectory (driver/subsystem) to analyze, relative to the repo. This is what
  keeps the run fast and focused (analyze one driver, not the whole kernel).
- The target is **cached**: once you pass `--repo`/`--scope`, a later bare `/vr-analyze` reuses it.

### Default = maximum coverage

A bare run is **fully automatic** and engages **all 10 skills**: it runs the bottom-up pipeline,
the followup loop, and the top-down hypothesis loop. Flags only *reduce* it:

| Flag | Effect |
|------|--------|
| `--interactive` | pause to confirm the generated profile and to choose scope exclusions |
| `--no-followup` | skip the Stage 5 followup loop |
| `--no-hypo-loop` | skip the top-down hypothesis loop |
| `--quick` | bottom-up core only: MAP → TRIAGE → DEEP → CLASSIFY → REPORT |
| `--fresh` | clear `out/` and start clean; **default preserves `out/` (resume)** — a target change clears regardless |
| `--rounds N` | rounds to *advance* per invocation (default 2). On a same-target resume the cap auto-extends by N each run, so a bare re-run is never a no-op at the cap |

By default a re-run **resumes**: existing non-empty handoffs are reused and the followup / hypothesis
loops pick up where they left off, advancing `--rounds` more rounds each time. Use `--fresh` to discard
prior results and start over.

> `/vr-analyze` is one **skill invocation**, not a shell one-liner: the deterministic scripts run
> via Bash, but the analysis stages spawn subagents, which only Claude can do.

---

## How it works

```
                   ┌─ (A) bottom-up: find by pattern ───────┐
  target source  ─►┤                                        ├─►  findings.json → classified.json → report
                   └─ (B) top-down: find by understanding ──┘
```

### (A) Bottom-up pipeline

| Stage | Skill | Deterministic helper | Produces |
|-------|-------|----------------------|----------|
| 1. MAP | `vr-map` | `vr_map.py` | `candidates.json` |
| 2. TRIAGE | `vr-triage` | `vr_triage_select.py` | `deep_queue.json`, `deferred.json` |
| 3. DEEP | `vr-deep` | `vr_deep_merge.py` | `findings.json` |
| 4. CLASSIFY | `vr-classify` | `vr_classify.py` | `classified.json` |
| 5. FOLLOWUP | `vr-followup` | `vr_followup_queue.py` | (updates findings) |
| 6. REPORT | `vr-report` | `vr_report.py` | `report.md`, `report_true_positives.md` |

- **MAP**: an LLM subagent reads the target and generates a `profile.json` (entrypoint + indicator
  patterns) — no hardcoded framework packs. tree-sitter indexes symbols; entrypoints are found and
  tagged with `surface_type` / privilege / `#ifdef CONFIG_*` gates; functions are scored and ranked;
  it proposes an out-of-scope policy for the human (`scope_proposal.md`).
- **TRIAGE**: subagents screen each candidate (risky / interesting / quiet). A deterministic gate
  selects what advances to DEEP. DoS-looking bugs are *escalated*, never dropped.
- **DEEP**: per-candidate data-flow / reachability / **true-impact** analysis, then **adversarial
  verification** — 3 independent refuters per finding; a refutation only counts if it cites
  concrete evidence. A vote decides confirmed / refuted / uncertain / unverified. Refuters obey
  hard anti-false-negative rules: a re-check that skips a required update is **not** a guard, a
  lock only refutes if it covers the exact corrupted object/timepoint, the finding's *area* isn't
  cleared just because its *stated mechanism* is wrong, and safety-claiming comments aren't trusted.
  Race / TOCTOU / check-then-act findings require the full 3-lens panel (no single-verdict kill).
- **CLASSIFY**: conservative TP/FP/uncertain call, then the single authoritative scope/impact
  enforcement (e.g. DoS exclusion if the human opted in). Buckets: TP / uncertain / out_of_scope / FP.
- **FOLLOWUP** (optional, iterative): chases `followup_symbols` from open findings across function
  boundaries and drains the deferred queue, re-running DEEP + CLASSIFY, bounded by a round cap.
- **REPORT**: renders the full report (all four buckets + a Coverage & Limits section) and a
  true-positives-only report.

### (B) Top-down hypothesis loop (`vr-hypo-loop`)

A self-correcting loop around two living, delta-merged stores:

- `arch_model.json` → rendered to **`architecture.md`**: components, **optimizations** (where
  safety is traded for speed = where bugs live), and **invariants** (rules the code must honor).
- `hypotheses.json`: bug hypotheses at three **levels** — `area` (broad, exploratory),
  `mechanism`, `concrete` (DEEP-testable).

Per round:

1. **STUDY** (`vr-arch` + `vr_arch_merge.py`) — read the least-understood slice; emit only deltas
   (add/refine/retract) into the architecture model.
2. **HYPOTHESIZE** (`vr-hypothesize` + `vr_hypo_merge.py`) — generate hypotheses through three
   lenses: **attacker** (input → dangerous op), **contract/invariant** (where an invariant is
   violated), **developer role-play** ("if I wrote this, where would I be nervous?"). Always a mix
   of levels, to keep the search wide.
3. **TARGET** (`vr_hypo_queue.py`) — balance depth vs breadth: `concrete` → `deep_queue.json`,
   `area`/`mechanism` → `explore_queue.json` (deepened next round).
4. **ANALYZE** — reuse `vr-deep` + `vr-classify`.
5. **FEEDBACK** — findings update hypothesis status and **correct the architecture model**, so it
   gets more detailed and more accurate each round.

Guardrail: only `concrete` hypotheses can be refuted; refuting an `area`/`mechanism` is downgraded
to "open" — you cannot prove an area safe, so broad directions stay alive (no tunnel vision).
And refuting a `concrete` hypothesis disproves only its *stated mechanism*, not the safety of the
code it touched: `vr_hypo_merge.py` re-queues that function for one more look (one-shot, gated to
failure-prone classes like race/uaf/double_free) so a real bug next to a wrong guess isn't cleared.

Termination: model deltas dry up for K rounds **and** no open concrete hypotheses remain, or the
round/budget cap is hit.

---

## Output (`out/`)

| File | What |
|------|------|
| `profile.json` | generated entrypoint/indicator patterns + the human scope policy |
| `candidates.json` | ranked candidate functions (MAP) |
| `deep_queue.json` / `deferred.json` | what advances to DEEP / what is parked (TRIAGE) |
| `findings.json` | analyzed + adversarially verified findings (DEEP) |
| `classified.json` | findings bucketed TP / uncertain / out_of_scope / FP (CLASSIFY) |
| `excluded.json` | what MAP excluded by scope, with reasons |
| `architecture.md` / `arch_model.json` | the living architecture & optimization model |
| `hypotheses.json` | the living hypothesis store |
| `report.md` / `report_true_positives.md` | final reports (latest-symlinked) |

Reports are timestamped; `report.md`, `report_true_positives.md` symlink to the latest run.

---

## Project layout

```
.claude/skills/vr-*/SKILL.md   # 10 skills (orchestration instructions Claude follows)
scripts/vr_*.py                # 10 deterministic helpers (Python stdlib only)
configs/vr-config.json         # target (repo/scope) + tunables; target is auto-cached
profiles/*.example.json        # delta/profile schemas the subagents emit
queries/call_graph.scm         # tree-sitter call-graph query
setup.sh                       # one-time setup (tree-sitter + C grammar, self-check)
out/                           # all run artifacts (gitignored)
```
