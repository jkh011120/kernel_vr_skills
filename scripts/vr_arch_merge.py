#!/usr/bin/env python3
"""
kernel_vr_skills - living architecture model: delta merge + render.

The STUDY subagent (vr-arch) emits ONLY deltas (newly-learned / corrected knowledge) into
out/arch_delta.json. This script integrates them into the canonical store out/arch_model.json
and re-renders the human-readable out/architecture.md. The model is never rewritten wholesale,
so detail accumulates and nothing is lost (wiki-style add / refine / retract).

Ops per entry (in components | optimizations | open_questions):
    add      -> new entry, auto-assigned id (c#/o#/q#), confidence default 0.3
    refine   -> update existing entry by id; old field values recorded in history (correct a
                wrong earlier note); confidence nudged up
    retract  -> mark status=retracted (kept for audit, hidden from the rendered model)
    answer   -> close an open_question (status=answered)
Every change appends a history record {round, change, note, before} — corrections are auditable,
never silent. `round` = new model version (auto-incremented each merge).

Inputs:  out/arch_model.json (created if absent), out/arch_delta.json
Outputs: out/arch_model.json (updated), out/architecture.md (rendered)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

LISTS = {"components": "c", "optimizations": "o", "open_questions": "q"}


def load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def next_id(items, prefix):
    n = 0
    for it in items:
        i = it.get("id", "")
        if i.startswith(prefix):
            try:
                n = max(n, int(i[len(prefix):]))
            except ValueError:
                pass
    return f"{prefix}{n + 1}"


def apply_ops(model, delta, rnd):
    stats = {"add": 0, "refine": 0, "retract": 0, "answer": 0, "skipped": 0}
    for key, prefix in LISTS.items():
        items = model.setdefault(key, [])
        by_id = {it["id"]: it for it in items if it.get("id")}
        for d in delta.get(key, []):
            op = d.get("op", "add")
            if op == "add":
                nid = next_id(items, prefix)
                entry = {k: v for k, v in d.items() if k not in ("op", "note")}
                entry["id"] = nid
                entry["last_round"] = rnd
                entry.setdefault("confidence", 0.3)
                entry.setdefault("status", "active")
                entry["history"] = [{"round": rnd, "change": "add", "note": d.get("note", "")}]
                items.append(entry)
                by_id[nid] = entry
                stats["add"] += 1
            elif op in ("refine", "retract", "answer"):
                tgt = by_id.get(d.get("id"))
                if not tgt:
                    stats["skipped"] += 1
                    continue
                before = {k: tgt.get(k) for k in d if k not in ("op", "id", "note")}
                for k, v in d.items():
                    if k not in ("op", "id", "note"):
                        tgt[k] = v
                if op == "retract":
                    tgt["status"] = "retracted"
                elif op == "answer":
                    tgt["status"] = d.get("status", "answered")
                elif op == "refine" and "confidence" not in d:
                    tgt["confidence"] = round(min(1.0, tgt.get("confidence", 0.3) + 0.1), 2)
                tgt["last_round"] = rnd
                tgt.setdefault("history", []).append(
                    {"round": rnd, "change": op, "note": d.get("note", ""), "before": before})
                stats[op] += 1
            else:
                stats["skipped"] += 1
    return stats


def render(model):
    md = [f"# Architecture & Optimization Model  (v{model.get('version', 0)})", ""]
    opts = [o for o in model.get("optimizations", []) if o.get("status") != "retracted"]
    md.append("## Optimizations — primary bug sources")
    md.append("_each trades safety for speed; the place to hunt._\n" if opts else "_none yet_\n")
    for o in sorted(opts, key=lambda x: -(x.get("confidence", 0))):
        md.append(f"### {o.get('name','?')}  `({o['id']}, conf {o.get('confidence')})`")
        md.append(f"- **technique**: {o.get('technique','')}")
        md.append(f"- **where**: {', '.join(o.get('where', []))}")
        md.append(f"- **safety traded**: {o.get('safety_traded','')}")
        md.append(f"- **risk**: {o.get('risk_notes','')}")
        if o.get("invariants"):
            md.append("- **invariants (rules code MUST honor — violations = bugs)**:")
            md += [f"    - {inv}" for inv in o["invariants"]]
        md.append("")
    comps = [c for c in model.get("components", []) if c.get("status") != "retracted"]
    md.append("## Components\n" if comps else "## Components\n_none yet_\n")
    for c in sorted(comps, key=lambda x: x.get("id", "")):
        md.append(f"### {c.get('name','?')}  `({c['id']}, conf {c.get('confidence')})`")
        md.append(f"- **role**: {c.get('role','')}")
        if c.get("files"):
            md.append(f"- **files**: {', '.join(c['files'])}")
        if c.get("key_structs"):
            md.append(f"- **key structs**: {', '.join(c['key_structs'])}")
        if c.get("lifecycle"):
            md.append(f"- **lifecycle**: {c['lifecycle']}")
        if c.get("locking"):
            md.append(f"- **locking**: {c['locking']}")
        if c.get("invariants"):
            md.append("- **invariants (rules code MUST honor — violations = bugs)**:")
            md += [f"    - {inv}" for inv in c["invariants"]]
        if c.get("notes"):
            md.append(f"- **notes**: {c['notes']}")
        md.append("")
    openq = [q for q in model.get("open_questions", []) if q.get("status") == "open"]
    md.append("## Open Questions  _(drive the next STUDY round)_")
    md += ([f"- `[{q['id']}]` {q.get('question')}  (about {q.get('about','?')})" for q in openq]
           or ["_none_"])
    return "\n".join(md) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--delta", default=None, help="default: <out>/arch_delta.json")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    model = load(out / "arch_model.json",
                 {"version": 0, "components": [], "optimizations": [], "open_questions": []})
    delta = load(Path(args.delta) if args.delta else out / "arch_delta.json", {})

    rnd = model.get("version", 0) + 1
    stats = apply_ops(model, delta, rnd)
    model["version"] = rnd
    (out / "arch_model.json").write_text(json.dumps(model, indent=2))
    (out / "architecture.md").write_text(render(model))

    print(f"[ARCH-MERGE] v{rnd}: +{stats['add']} add, {stats['refine']} refine, "
          f"{stats['retract']} retract, {stats['answer']} answer, {stats['skipped']} skipped")
    print(f"[OUT] {out/'arch_model.json'}  {out/'architecture.md'}")


if __name__ == "__main__":
    main()
