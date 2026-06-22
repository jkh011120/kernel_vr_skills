#!/usr/bin/env python3
"""
kernel_vr_skills - living hypothesis store: delta merge.

The HYPOTHESIZE subagent (vr-hypothesize) and the FEEDBACK step (after DEEP) emit deltas into
out/hypo_delta.json; this integrates them into out/hypotheses.json (add / refine / status).

Hypotheses carry a `level`:
    area       broad exploratory direction (keeps the search wide; prevents tunnel vision)
    mechanism  a design mechanism to scrutinize
    concrete   a specific, DEEP-testable claim (has target_symbols)

NO-SAFE-PATTERNS GUARD (enforced here, deterministically):
    Only `concrete` hypotheses may be set to "refuted". A delta that tries to refute an
    `area`/`mechanism` hypothesis is DOWNGRADED to "open" with a note — you cannot prove an
    area safe. This keeps broad directions alive so the loop doesn't collapse into one rabbit hole.

REFUTED-IS-NOT-CLEARED (#3, enforced here, deterministically):
    Refuting a `concrete` hypothesis disproves only its STATED MECHANISM, not the safety of the
    functions it touched. On such a refute, every `target_symbols` entry is appended to
    out/explore_queue.json ("refuted-mechanism, re-examine for other bug classes") so the next
    round re-examines that code under a different lens. Prevents the over-generalization
    "hypothesis refuted => code cleared" that can hide a real bug next to a wrong guess.

Ops:
    add     -> new hypothesis, id h#, status default "open"
    refine  -> update fields by id (record before in history)
    status  -> change status (open|queued|confirmed|refuted|needs_info|spawned), guarded as above

Inputs:  out/hypotheses.json (created if absent), out/hypo_delta.json
Output:  out/hypotheses.json (updated)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

NONREFUTABLE = {"area", "mechanism"}
VALID_STATUS = {"open", "queued", "confirmed", "refuted", "needs_info", "spawned"}


def load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


# Bug classes where refuting one *mechanism* plausibly leaves a DIFFERENT real bug in the same
# code (lifetime/ordering/state bugs). Value-bound classes (oob/int_overflow/info_leak), when
# refuted with a cited bound, are genuinely cleared and are NOT re-queued.
REEXAM_CLASSES = {"race", "toctou", "check-then-act", "uaf", "use-after-free", "double_free",
                  "double-free", "refcount"}


def _bug_class_warrants_reexam(hyp: dict) -> bool:
    bc = (hyp.get("bug_class") or "").lower()
    return any(c in bc for c in REEXAM_CLASSES)


def _requeue_refuted_targets(out: Path, hyp: dict, rnd: int) -> int:
    """Append a refuted concrete hypothesis's target_symbols to explore_queue.json for ONE fresh
    look under a different lens. Bounded so genuinely-safe code is not re-examined forever:
      - ONE-SHOT: only fires once per hypothesis (sets hyp['reexam_emitted']); later refutes of the
        same hypothesis add nothing.
      - CLASS-GATED: only for failure-prone classes (REEXAM_CLASSES); cleanly-refuted value-bound
        findings are treated as settled.
    Returns how many new entries were added."""
    if hyp.get("reexam_emitted"):
        return 0
    if not _bug_class_warrants_reexam(hyp):
        return 0
    targets = hyp.get("target_symbols") or []
    if not targets:
        return 0
    hyp["reexam_emitted"] = rnd  # one-shot marker (records the round it fired)
    eq_path = out / "explore_queue.json"
    eq = load(eq_path, [])
    if not isinstance(eq, list):
        eq = []
    seen = {e.get("name") if isinstance(e, dict) else e for e in eq}
    added = 0
    for t in targets:
        if t in seen:
            continue
        eq.append({
            "name": t,
            "source": f"refuted-mechanism:{hyp.get('id')}",
            "note": "refuted-mechanism, re-examine for other bug classes",
            "round": rnd,
        })
        seen.add(t)
        added += 1
    eq_path.write_text(json.dumps(eq, indent=2))
    return added


def next_id(items):
    n = 0
    for it in items:
        i = it.get("id", "")
        if i.startswith("h"):
            try:
                n = max(n, int(i[1:]))
            except ValueError:
                pass
    return f"h{n + 1}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--delta", default=None, help="default: <out>/hypo_delta.json")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    hyps = load(out / "hypotheses.json", [])
    delta = load(Path(args.delta) if args.delta else out / "hypo_delta.json", {})
    rnd = delta.get("round", 0)
    by_id = {h["id"]: h for h in hyps if h.get("id")}

    stats = {"add": 0, "refine": 0, "status": 0, "guarded": 0, "skipped": 0}
    requeued = 0
    for d in delta.get("hypotheses", []):
        op = d.get("op", "add")
        if op == "add":
            nid = next_id(hyps)
            entry = {k: v for k, v in d.items() if k not in ("op", "note")}
            entry["id"] = nid
            entry.setdefault("level", "concrete")
            entry.setdefault("status", "open")
            entry.setdefault("target_symbols", [])
            entry["last_round"] = rnd
            entry["history"] = [{"round": rnd, "change": "add", "note": d.get("note", "")}]
            hyps.append(entry)
            by_id[nid] = entry
            stats["add"] += 1
            continue

        tgt = by_id.get(d.get("id"))
        if not tgt:
            stats["skipped"] += 1
            continue
        before = {k: tgt.get(k) for k in d if k not in ("op", "id", "note")}

        if op == "status":
            new = d.get("status", "open")
            if new == "refuted" and tgt.get("level") in NONREFUTABLE:
                new = "open"
                tgt.setdefault("history", []).append({
                    "round": rnd, "change": "guard",
                    "note": "refute on non-concrete hypothesis downgraded to open "
                            "(no-safe-patterns): " + d.get("note", "")})
                stats["guarded"] += 1
            if new not in VALID_STATUS:
                new = "open"
            # #3: refuting a CONCRETE hypothesis disproves only its stated MECHANISM, not the
            # safety of the functions it touched. Re-queue those functions for a fresh look under
            # a different lens so a real bug sitting next to a wrong guess is not silently cleared.
            if new == "refuted" and tgt.get("level") not in NONREFUTABLE:
                requeued += _requeue_refuted_targets(out, tgt, rnd)
            tgt["status"] = new
            stats["status"] += 1
        elif op == "refine":
            for k, v in d.items():
                if k not in ("op", "id", "note"):
                    tgt[k] = v
            stats["refine"] += 1
        else:
            stats["skipped"] += 1
            continue
        tgt["last_round"] = rnd
        tgt.setdefault("history", []).append(
            {"round": rnd, "change": op, "note": d.get("note", ""), "before": before})

    (out / "hypotheses.json").write_text(json.dumps(hyps, indent=2))

    levels = {}
    statuses = {}
    for h in hyps:
        levels[h.get("level")] = levels.get(h.get("level"), 0) + 1
        statuses[h.get("status")] = statuses.get(h.get("status"), 0) + 1
    print(f"[HYPO-MERGE] +{stats['add']} add, {stats['refine']} refine, {stats['status']} status "
          f"({stats['guarded']} refutes guarded as open), {stats['skipped']} skipped, "
          f"{requeued} refuted-target(s) re-queued for re-exam")
    print(f"[HYPO-MERGE] now {len(hyps)} hypotheses — levels {levels}, statuses {statuses}")
    print(f"[OUT] {out/'hypotheses.json'}")


if __name__ == "__main__":
    main()
