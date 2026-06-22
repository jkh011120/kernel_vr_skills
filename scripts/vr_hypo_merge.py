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
          f"({stats['guarded']} refutes guarded as open), {stats['skipped']} skipped")
    print(f"[HYPO-MERGE] now {len(hyps)} hypotheses — levels {levels}, statuses {statuses}")
    print(f"[OUT] {out/'hypotheses.json'}")


if __name__ == "__main__":
    main()
