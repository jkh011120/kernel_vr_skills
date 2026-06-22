#!/usr/bin/env python3
"""
kernel_vr_skills - Stage 1 (MAP) deterministic core.

Pipeline (no LLM here; the LLM steps PROFILE and SCOPE-PROPOSE live in the skill):

    INDEX   git ls-files + tree-sitter tags  -> symbol map (raw source, all #ifdef branches kept)
    SURFACE profile.surface_patterns / name patterns -> user-reachable entrypoints,
            each tagged with surface_type, privilege gate, and CONFIG_ gate (method-1 config tagging)
    SELECT  call graph -> reachability from entrypoints -> score & rank candidates
    ENRICH  drop low-score candidates, attach full function body (+ tags) as context

Inputs:  --repo, --scope, --profile (profile.json), --out (output dir)
Outputs: out/index.json, out/candidates.json, out/excluded.json

out_of_scope filtering: if profile.out_of_scope has any rules, matching entrypoints are
removed from candidates and recorded (with reason) in out/excluded.json. With no rules,
nothing is dropped -- everything is just TAGGED so scope_proposal can present the menu.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import fnmatch
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


# ----------------------------------------------------------------------------
# Scoring config (adapted from the proven kernel_claude_skills weights)
# ----------------------------------------------------------------------------
SCORE_WEIGHTS = {
    "user_control": 3.0,
    "lifetime": 2.5,
    "concurrency": 1.5,
    "reachability": 2.0,
    "entrypoint": 3.0,
}
SCORE_THRESHOLDS = {"high": 10.0, "med": 6.0}
MAX_GRAPH_DEPTH_DEFAULT = 3
MAX_CANDIDATES_DEFAULT = 25

# Default indicator patterns (used only if the profile omits a category).
DEFAULT_INDICATORS = {
    "user_control": [r"\bcopy_from_user\b", r"\bget_user\b", r"\bmemdup_user\b", r"\b__user\b"],
    "lifetime": [r"\bk[zmc]alloc\b", r"\bkfree\b", r"\bkvfree\b", r"\bkref_(get|put)\b", r"\brefcount_"],
    "concurrency": [r"\bspin_lock\b", r"\bmutex_lock\b", r"\brcu_dereference\b", r"\batomic_"],
    "guards": [r"\baccess_ok\b", r"\bIS_ERR(_OR_NULL)?\b", r"\bWARN_ON\b", r"\bBUG_ON\b"],
}

# Privilege gate detection (method-1 scope tagging).
PRIVILEGE_RE = re.compile(r"\b(capable|ns_capable|file_ns_capable|perfmon_capable)\s*\(|\bCAP_[A-Z_]+\b")
CONFIG_RE = re.compile(r"CONFIG_[A-Z0-9_]+")


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def git_ls_files(repo: str, scope: str) -> list[str]:
    try:
        r = subprocess.run(
            ["git", "-C", repo, "ls-files", scope],
            capture_output=True, text=True, check=True,
        )
        rels = [l for l in r.stdout.splitlines() if l.strip()]
        if rels:
            return rels
    except Exception:
        pass
    # Fallback: walk the filesystem (non-git trees).
    base = Path(repo) / scope
    return [str(p.relative_to(repo)) for p in base.rglob("*") if p.is_file()]


def load_repo_config() -> dict:
    """Load configs/vr-config.json relative to the repo root (cwd-independent)."""
    p = Path(__file__).resolve().parent.parent / "configs" / "vr-config.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def read_lines(path: str) -> list[str]:
    try:
        return Path(path).read_text(errors="ignore").splitlines()
    except OSError:
        return []


def extract_function_body(path: str, start_line: int, max_lines: int = 400) -> str:
    """Extract a function body by brace matching, starting at/just before start_line.

    Returns numbered lines ("    12  <code>") so downstream pattern scans can report
    real line numbers. Keeps ALL preprocessor branches verbatim (no #ifdef evaluation).
    """
    lines = read_lines(path)
    if not lines:
        return ""
    n = len(lines)
    i = max(0, start_line - 1)
    # Find the first '{' at or after the definition line.
    brace_line = None
    for j in range(i, min(n, i + 40)):
        if "{" in lines[j]:
            brace_line = j
            break
    if brace_line is None:
        lo, hi = i, min(n, i + max_lines)
    else:
        depth = 0
        end = brace_line
        for j in range(brace_line, min(n, brace_line + max_lines)):
            depth += lines[j].count("{") - lines[j].count("}")
            end = j
            if depth <= 0 and j > brace_line:
                break
        lo, hi = i, end + 1
    return "\n".join(f"{k + 1:6d}  {lines[k]}" for k in range(lo, hi))


def numbered_iter(context: str):
    for raw in context.splitlines():
        m = re.match(r"^\s*(\d+)\s+(.*)$", raw)
        if m:
            yield int(m.group(1)), m.group(2)


# ----------------------------------------------------------------------------
# INDEX: tree-sitter tags (primary) with a regex fallback
# ----------------------------------------------------------------------------
def _ts_tags_one_file(abs_path: str, repo: str) -> tuple[str, list[dict], str | None]:
    try:
        r = subprocess.run(
            ["tree-sitter", "tags", "--scope", "source.c", abs_path],
            cwd=repo, capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return abs_path, [], (r.stderr or "tree-sitter failed")[:120]
    except Exception as e:
        return abs_path, [], str(e)

    tags = []
    for line in r.stdout.splitlines():
        parts = line.strip().split("\t")
        if len(parts) < 3:
            continue
        name = parts[0].strip()
        kind = parts[1].strip().lstrip("|").strip()
        m = re.search(r"\((\d+),\s*\d+\)", parts[2])
        lineno = int(m.group(1)) + 1 if m else None
        tags.append({"name": name, "file": abs_path, "line": lineno, "kind": kind})
    return abs_path, tags, None


_FUNC_DEF_RE = re.compile(
    r"^[A-Za-z_][\w\s\*]*?\b([A-Za-z_]\w*)\s*\([^;{]*\)\s*\{?\s*$"
)


def _regex_tags_one_file(abs_path: str) -> list[dict]:
    """Fallback function-definition finder when tree-sitter tags is unavailable."""
    tags = []
    lines = read_lines(abs_path)
    for idx, line in enumerate(lines, start=1):
        s = line.rstrip()
        if not s or s.startswith(("#", "/", "*", "}")) or s.lstrip().startswith("return"):
            continue
        m = _FUNC_DEF_RE.match(s)
        if m and ("(" in s):
            name = m.group(1)
            if name not in ("if", "for", "while", "switch", "sizeof", "return"):
                tags.append({"name": name, "file": abs_path, "line": idx, "kind": "function"})
    return tags


def build_index(repo: str, scope: str) -> tuple[dict[str, list[dict]], list[str]]:
    rels = git_ls_files(repo, scope)
    repo_p = Path(repo).resolve()
    c_files = [str(repo_p / r) for r in rels if r.endswith(".c") and (repo_p / r).exists()]

    by_file: dict[str, list[dict]] = {}
    ts_ok = 0
    max_workers = min(8, len(c_files)) if c_files else 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_ts_tags_one_file, fp, str(repo_p)): fp for fp in c_files}
        for fut in concurrent.futures.as_completed(futs):
            abs_path, tags, err = fut.result()
            if err is None and tags:
                ts_ok += 1
            if err is not None or not tags:
                tags = _regex_tags_one_file(abs_path)
            if tags:
                tags.sort(key=lambda t: (t["line"] is None, t["line"] or 10**9))
                by_file[abs_path] = tags
    print(f"[INDEX] {len(c_files)} .c files, tree-sitter ok on {ts_ok}, "
          f"{sum(len(v) for v in by_file.values())} symbols")
    return by_file, rels


def func_index_of(by_file: dict[str, list[dict]]) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for f, tags in by_file.items():
        for t in tags:
            if (t.get("kind") or "").lower().startswith("function") and t.get("name"):
                idx.setdefault(t["name"], []).append({"file": f, "line": t.get("line")})
    return idx


def enclosing_function(tags: list[dict], hit_line: int) -> dict | None:
    best = None
    for t in tags:
        if not (t.get("kind") or "").lower().startswith("function"):
            continue
        ln = t.get("line")
        if ln is None:
            continue
        if ln <= hit_line:
            best = t
        else:
            break
    return best


# ----------------------------------------------------------------------------
# config-gate map (method-1 #ifdef tagging)
# ----------------------------------------------------------------------------
def config_gate_map(lines: list[str]) -> dict[int, list[str]]:
    """Approximate which CONFIG_* macros are active at each line via a #if/#endif stack."""
    out: dict[int, list[str]] = {}
    stack: list[list[str]] = []
    for i, raw in enumerate(lines, start=1):
        s = raw.strip()
        if s.startswith("#if"):
            stack.append(CONFIG_RE.findall(s))
        elif s.startswith("#elif"):
            if stack:
                stack[-1] = CONFIG_RE.findall(s)
        elif s.startswith("#endif"):
            if stack:
                stack.pop()
        active = sorted({t for toks in stack for t in toks})
        if active:
            out[i] = active
    return out


# ----------------------------------------------------------------------------
# SURFACE: find entrypoints + tag surface_type / privilege / config
# ----------------------------------------------------------------------------
def detect_surface_type(file_path: str, reason: str, func_name: str) -> str:
    fp = file_path.lower()
    nm = func_name.lower()
    if "debugfs" in fp or "debugfs" in nm or "debugfs" in reason.lower():
        return "debugfs"
    if "sysfs" in fp or "sysfs" in reason.lower() or "_show" in nm or "_store" in nm:
        return "sysfs"
    if "module_param" in reason.lower():
        return "module_param"
    if "ioctl" in reason.lower() or nm.endswith("_ioctl") or "_api_" in nm:
        return "ioctl"
    if nm.endswith("_mmap") or "mmap" in reason.lower():
        return "mmap"
    return "file_ops"


def extract_entrypoints(by_file: dict[str, list[dict]], profile: dict) -> dict[str, dict]:
    fidx = func_index_of(by_file)
    surface_res = [re.compile(p) for p in profile.get("surface_patterns", [])]
    name_res = [re.compile(p) for p in profile.get("entrypoint_name_patterns", [])]
    eps: dict[str, dict] = {}

    def add(symbol: str, file_hint: str | None, reason: str):
        if not symbol or not re.match(r"^[A-Za-z_]\w*$", symbol):
            return
        locs = fidx.get(symbol, [])
        loc = next((l for l in locs if l["file"] == file_hint), locs[0] if locs else {})
        file_path = loc.get("file") or file_hint
        line = loc.get("line")
        ep = eps.setdefault(symbol, {
            "symbol": symbol, "file": file_path, "line": line, "reasons": [],
        })
        if reason not in ep["reasons"]:
            ep["reasons"].append(reason)

    # Content-pattern matches -> enclosing function is an entrypoint.
    for file_path, tags in by_file.items():
        if not surface_res:
            break
        lines = read_lines(file_path)
        for idx, line in enumerate(lines, start=1):
            for rx in surface_res:
                if rx.search(line):
                    fn = enclosing_function(tags, idx)
                    if fn:
                        add(fn["name"], file_path, f"surface_pattern {rx.pattern}")
                    break

    # Name-pattern matches.
    for symbol in fidx:
        for rx in name_res:
            if rx.match(symbol):
                add(symbol, None, f"name_pattern {rx.pattern}")
                break

    # Tag each entrypoint: surface_type, privilege, config gates.
    cfg_cache: dict[str, dict[int, list[str]]] = {}
    for ep in eps.values():
        fp = ep["file"]
        ep["surface_type"] = detect_surface_type(fp or "", " ".join(ep["reasons"]), ep["symbol"])
        body = extract_function_body(fp, ep["line"], 400) if fp and ep["line"] else ""
        ep["privileged"] = bool(PRIVILEGE_RE.search(body))
        if fp and fp not in cfg_cache:
            cfg_cache[fp] = config_gate_map(read_lines(fp))
        ep["config_gates"] = cfg_cache.get(fp, {}).get(ep["line"] or -1, [])
    return eps


# ----------------------------------------------------------------------------
# SELECT: call graph -> reachability -> score
# ----------------------------------------------------------------------------
def build_call_graph(by_file: dict[str, list[dict]], repo: str) -> dict[str, set[str]]:
    files = list(by_file.keys())
    query = Path(__file__).resolve().parent.parent / "queries" / "call_graph.scm"
    calls: list[dict] = []
    if query.exists() and files:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as tf:
            tf.write("\n".join(files) + "\n")
            paths_file = tf.name
        try:
            r = subprocess.run(
                ["tree-sitter", "query", str(query), "--scope", "source.c", "--paths", paths_file],
                capture_output=True, text=True, timeout=180, cwd=repo,
            )
            if r.returncode == 0:
                current = None
                cre = re.compile(r"start:\s*\((\d+),.*text:\s*`([^`]*)`")
                for raw in r.stdout.splitlines():
                    if raw and not raw.startswith(" "):
                        current = raw.strip()
                        continue
                    m = cre.search(raw)
                    if current and m:
                        calls.append({"file": current, "line": int(m.group(1)) + 1, "name": m.group(2).strip()})
        except Exception:
            calls = []
        finally:
            Path(paths_file).unlink(missing_ok=True)

    graph: dict[str, set[str]] = {}
    for c in calls:
        tags = by_file.get(c["file"], [])
        caller = enclosing_function(tags, c["line"])
        if caller and caller.get("name") and c.get("name"):
            graph.setdefault(caller["name"], set()).add(c["name"])
    return graph


def compute_reachability(eps: dict[str, dict], graph: dict[str, set[str]], max_depth: int) -> dict[str, dict]:
    reach: dict[str, dict] = {}
    for start in eps:
        queue = [(start, 0, [start])]
        visited = {start}
        while queue:
            node, depth, path = queue.pop(0)
            info = reach.setdefault(node, {"distance_min": depth, "entrypoints": set(), "path": path})
            info["entrypoints"].add(start)
            if depth < info["distance_min"]:
                info["distance_min"], info["path"] = depth, path
            if depth >= max_depth:
                continue
            for callee in sorted(graph.get(node, set())):
                if callee not in visited:
                    visited.add(callee)
                    queue.append((callee, depth + 1, path + [callee]))
    return reach


def scan_indicators(context: str, profile: dict) -> dict[str, list[dict]]:
    ip = profile.get("indicator_patterns", {})
    cats = {k: [re.compile(p) for p in ip.get(k, DEFAULT_INDICATORS[k])] for k in DEFAULT_INDICATORS}
    out = {k: [] for k in cats}
    for line_no, text in numbered_iter(context):
        for cat, res in cats.items():
            if any(rx.search(text) for rx in res):
                out[cat].append({"line": line_no, "text": text.strip()})
    return out


def score(counts: dict[str, int], distance_min: int | None, is_ep: bool, max_depth: int) -> float:
    s = (SCORE_WEIGHTS["user_control"] * counts.get("user_control", 0)
         + SCORE_WEIGHTS["lifetime"] * counts.get("lifetime", 0)
         + SCORE_WEIGHTS["concurrency"] * counts.get("concurrency", 0))
    if distance_min is not None:
        s += SCORE_WEIGHTS["reachability"] * max(0, max_depth - distance_min + 1)
    if is_ep:
        s += SCORE_WEIGHTS["entrypoint"]
    return round(s, 2)


# ----------------------------------------------------------------------------
# out_of_scope filtering
# ----------------------------------------------------------------------------
def scope_verdict(cand: dict, oos: dict) -> str | None:
    """Return a reason string if the candidate is out of scope, else None."""
    if not oos:
        return None
    st = cand.get("surface_type")
    if st and st in (oos.get("surface_types") or []):
        return f"surface_type={st} excluded"
    if oos.get("require_privilege") and cand.get("privileged"):
        return "behind privilege gate (capable/CAP_)"
    gates = set(cand.get("config_gates") or [])
    hit = gates & set(oos.get("config_gates") or [])
    if hit:
        return f"config gate {sorted(hit)} excluded"
    for pat in oos.get("path_excludes") or []:
        if fnmatch.fnmatch(cand.get("file") or "", pat):
            return f"path matches {pat}"
    return None


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--profile", required=True, help="path to profile.json")
    ap.add_argument("--out", default="out")
    # defaults None -> fall back to configs/vr-config.json [map], then hardcoded defaults
    ap.add_argument("--max-candidates", type=int, default=None)
    ap.add_argument("--max-depth", type=int, default=None)
    ap.add_argument("--enrich-body-lines", type=int, default=None)
    args = ap.parse_args()

    mapcfg = load_repo_config().get("map", {})
    max_candidates = args.max_candidates if args.max_candidates is not None else mapcfg.get("max_candidates", MAX_CANDIDATES_DEFAULT)
    max_depth = args.max_depth if args.max_depth is not None else mapcfg.get("max_graph_depth", MAX_GRAPH_DEPTH_DEFAULT)
    enrich_body = args.enrich_body_lines if args.enrich_body_lines is not None else mapcfg.get("enrich_max_body_lines", 400)
    min_factor = mapcfg.get("min_enrich_score_factor", 0.5)

    profile = json.loads(Path(args.profile).read_text())
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    # INDEX
    by_file, rels = build_index(args.repo, args.scope)
    (outdir / "index.json").write_text(json.dumps(
        {"file_count": len(rels), "symbol_count": sum(len(v) for v in by_file.values()),
         "files": sorted(by_file.keys())}, indent=2))

    # SURFACE
    eps = extract_entrypoints(by_file, profile)
    print(f"[SURFACE] {len(eps)} entrypoints "
          f"(debugfs={sum(1 for e in eps.values() if e['surface_type']=='debugfs')}, "
          f"privileged={sum(1 for e in eps.values() if e['privileged'])}, "
          f"config-gated={sum(1 for e in eps.values() if e['config_gates'])})")

    # SELECT
    graph = build_call_graph(by_file, args.repo)
    reach = compute_reachability(eps, graph, max_depth)
    fidx = func_index_of(by_file)
    # persist the symbol index so FOLLOWUP can resolve a symbol name -> file:line
    (outdir / "symbols.json").write_text(json.dumps(fidx, indent=2))
    ep_syms = set(eps)

    raw_candidates = []
    targets = reach if reach else {s: {} for s in ep_syms}
    for symbol, r in targets.items():
        locs = fidx.get(symbol)
        if not locs:
            continue
        loc = locs[0]
        body = extract_function_body(loc["file"], loc.get("line") or 1, enrich_body)
        ind = scan_indicators(body, profile)
        counts = {k: len(v) for k, v in ind.items()}
        sc = score(counts, r.get("distance_min"), symbol in ep_syms, max_depth)
        ep = eps.get(symbol, {})
        raw_candidates.append({
            "name": symbol,
            "file": loc["file"],
            "line": loc.get("line"),
            "score": sc,
            "priority": "high" if sc >= SCORE_THRESHOLDS["high"] else "med" if sc >= SCORE_THRESHOLDS["med"] else "low",
            "is_entrypoint": symbol in ep_syms,
            "surface_type": ep.get("surface_type"),
            "privileged": ep.get("privileged", False),
            "config_gates": ep.get("config_gates", []),
            "reasons": ep.get("reasons", []),
            "distance_min": r.get("distance_min"),
            "reachability_path": r.get("path", []),
            "indicator_counts": counts,
            "indicator_hits": {k: v[:8] for k, v in ind.items()},
            "context": body,
        })
    raw_candidates.sort(key=lambda c: c["score"], reverse=True)

    # out_of_scope filtering (logged, never silent)
    oos = profile.get("out_of_scope", {})
    kept, excluded = [], []
    for c in raw_candidates:
        reason = scope_verdict(c, oos)
        if reason:
            excluded.append({"name": c["name"], "file": c["file"], "line": c["line"],
                             "surface_type": c["surface_type"], "reason": reason})
        else:
            kept.append(c)

    # ENRICH: drop very low score (keep high-priority regardless)
    min_enrich = SCORE_THRESHOLDS["med"] * min_factor
    candidates = [c for c in kept if c["score"] >= min_enrich or c["priority"] == "high"][:max_candidates]

    (outdir / "candidates.json").write_text(json.dumps(candidates, indent=2))
    (outdir / "excluded.json").write_text(json.dumps(excluded, indent=2))
    print(f"[SELECT] {len(raw_candidates)} scored -> {len(excluded)} out-of-scope -> "
          f"{len(candidates)} candidates written")
    print(f"[OUT] {outdir/'candidates.json'}  {outdir/'excluded.json'}  {outdir/'index.json'}")


if __name__ == "__main__":
    main()
