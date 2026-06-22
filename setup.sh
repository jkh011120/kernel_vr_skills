#!/bin/bash
# kernel_vs_skills - setup
# No Python venv / pip deps (scripts use the stdlib only). The only real external dependency is
# the tree-sitter CLI + C grammar (for symbol indexing & call graph); without it the scripts fall
# back to regex (works, lower accuracy). Run from the repo root.

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
ok()      { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail()    { echo -e "${RED}[ERROR]${NC} $1"; }

echo "============================================"
echo "  kernel_vs_skills - setup"
echo "============================================"

cd "$(dirname "$0")" || exit 1
PROBLEMS=0

# 1. Python 3.10+
info "Checking Python..."
if command -v python3 >/dev/null 2>&1; then
    PV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
    MAJ=${PV%.*}; MIN=${PV#*.}
    if [ "$MAJ" -ge 3 ] && [ "$MIN" -ge 10 ]; then ok "Python $PV"; else fail "Python 3.10+ required (found $PV)"; PROBLEMS=$((PROBLEMS+1)); fi
else
    fail "python3 not found"; PROBLEMS=$((PROBLEMS+1))
fi

# 2. git
info "Checking git..."
if command -v git >/dev/null 2>&1; then ok "git $(git --version | awk '{print $3}')"; else fail "git not found (needed for git ls-files)"; PROBLEMS=$((PROBLEMS+1)); fi

# 3. tree-sitter CLI
info "Checking tree-sitter CLI..."
if ! command -v tree-sitter >/dev/null 2>&1; then
    [ -f "$HOME/.cargo/bin/tree-sitter" ] && export PATH="$HOME/.cargo/bin:$PATH"
fi
if command -v tree-sitter >/dev/null 2>&1; then
    ok "tree-sitter $(tree-sitter --version 2>&1 | head -1 | awk '{print $2}')"
else
    warn "tree-sitter not found — attempting install..."
    if command -v cargo >/dev/null 2>&1; then cargo install tree-sitter-cli && export PATH="$HOME/.cargo/bin:$PATH"
    elif command -v npm >/dev/null 2>&1; then npm install -g tree-sitter-cli
    elif [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then brew install tree-sitter
    else warn "Install one of cargo/npm/brew, then re-run. (Scripts will use regex fallback meanwhile.)"; fi
    command -v tree-sitter >/dev/null 2>&1 && ok "tree-sitter installed" || warn "tree-sitter still unavailable — regex fallback will be used"
fi

# 4. tree-sitter C grammar (needed for `tree-sitter tags`)
if command -v tree-sitter >/dev/null 2>&1; then
    info "Setting up tree-sitter C grammar..."
    TS_DIR="$HOME/.config/tree-sitter"; PARSERS="$TS_DIR/parsers"
    mkdir -p "$PARSERS"
    if [ ! -d "$PARSERS/tree-sitter-c" ]; then
        git clone --depth 1 https://github.com/tree-sitter/tree-sitter-c.git "$PARSERS/tree-sitter-c" >/dev/null 2>&1 \
            && ok "cloned tree-sitter-c" || warn "could not clone tree-sitter-c (regex fallback will be used)"
    else
        ok "tree-sitter-c present"
    fi
    [ -f "$TS_DIR/config.json" ] || printf '{\n  "parser-directories": ["~/.config/tree-sitter/parsers"]\n}\n' > "$TS_DIR/config.json"
    if [ -d "$PARSERS/tree-sitter-c" ]; then ( cd "$PARSERS/tree-sitter-c" && tree-sitter generate >/dev/null 2>&1 ) || true; fi
fi

# 5. out/ dir
mkdir -p out && ok "out/ ready"

# 6. self-check: scripts run + tree-sitter tags works
info "Self-check..."
if python3 scripts/vr_target.py >/dev/null 2>&1 || [ $? -eq 2 ]; then ok "scripts execute"; else fail "scripts failed to run"; PROBLEMS=$((PROBLEMS+1)); fi
if command -v tree-sitter >/dev/null 2>&1; then
    TMP=$(mktemp --suffix=.c); printf 'int foo(int x){return x;}\n' > "$TMP"
    if tree-sitter tags --scope source.c "$TMP" >/dev/null 2>&1; then ok "tree-sitter tags works (full accuracy)"
    else warn "tree-sitter present but 'tags' failed — regex fallback will be used (lower accuracy)"; fi
    rm -f "$TMP"
fi

echo
echo "============================================"
if [ "$PROBLEMS" -eq 0 ]; then echo -e "${GREEN}  Setup complete.${NC}"; else echo -e "${YELLOW}  Setup finished with $PROBLEMS issue(s) above.${NC}"; fi
echo "============================================"
echo
echo "Next: point it at a target (cached after the first time), then run:"
echo "  /vr-analyze --repo /path/to/linux-kernel --scope drivers/gpu/arm/midgard"
echo "  # later, just:  /vr-analyze"
echo
echo "Note: run from the repo root; the scripts use repo-relative paths (configs/, out/)."
