#!/usr/bin/env python3
"""
kernel_vr_skills - resolve + cache the analysis target.

Resolution precedence: --repo/--scope args  >  configs/vr-config.json target.*  > (caller asks).
When args are supplied, they are CACHED back into the config so the next run can omit them
(`/vr-analyze` with no args reuses the last target). Prints the resolved repo/scope as JSON.

Usage:
    python3 scripts/vr_target.py                       # read cached target
    python3 scripts/vr_target.py --repo R --scope S    # use + cache R/S
    python3 scripts/vr_target.py --no-save ...         # use but do not cache
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# repo-root-relative, so it works regardless of the current working directory
CONFIG = Path(__file__).resolve().parent.parent / "configs" / "vr-config.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None)
    ap.add_argument("--scope", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--no-save", action="store_true", help="do not cache args back to config")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    target = cfg.get("target", {})

    placeholder = {"/path/to/linux-kernel", "", None}
    repo = args.repo or (target.get("repo") if target.get("repo") not in placeholder else None)
    scope = args.scope or target.get("scope")
    name = args.name or target.get("name")

    if not repo:
        print(json.dumps({"error": "no repo: pass --repo or set target.repo in config"}))
        sys.exit(2)

    # cache back when caller provided args (and saving allowed)
    if not args.no_save and (args.repo or args.scope or args.name):
        cfg.setdefault("target", {})
        cfg["target"]["repo"] = repo
        if scope:
            cfg["target"]["scope"] = scope
        if name:
            cfg["target"]["name"] = name
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(cfg, indent=2))

    print(json.dumps({"repo": repo, "scope": scope, "name": name, "cached": bool(not args.no_save and (args.repo or args.scope))}))


if __name__ == "__main__":
    main()
