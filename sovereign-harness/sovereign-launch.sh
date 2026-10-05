#!/bin/bash
# sovereign-launch.sh
# Sovereign launch script for sovereign-harness.
# Activates venv if present, sets PYTHONPATH, resolves model via config,
# ensures ~/.sovereign/ , forwards all args including --help.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate venv if present
if [ -f ".venv/bin/activate" ]; then
    source ".venv/bin/activate"
elif [ -f "venv/bin/activate" ]; then
    source "venv/bin/activate"
elif [ -f "env/bin/activate" ]; then
    source "env/bin/activate"
fi

# Set PYTHONPATH relative to sovereign-harness dir
export PYTHONPATH="${SCRIPT_DIR}${PYTHONPATH:+:$PYTHONPATH}"

# Resolve model using the config if SOVEREIGN_MODEL not already set
if [ -z "${SOVEREIGN_MODEL:-}" ]; then
    MODEL_RESOLVED=$(python3 -c '
import sys
sys.path.insert(0, ".")
try:
    from config import load_config
    cfg = load_config()
    m = cfg.get("model", "").strip()
    if m:
        print(m)
except Exception:
    pass
' 2>/dev/null || true)
    if [ -n "$MODEL_RESOLVED" ]; then
        export SOVEREIGN_MODEL="$MODEL_RESOLVED"
    fi
fi

# Ensure config dir ~/.sovereign/ exists (consistent with harness)
mkdir -p "$HOME/.sovereign"
mkdir -p "$HOME/.sovereign/sessions"
mkdir -p "$HOME/.sovereign/memory"

# Launch the harness, support all options like --help, --model etc.
exec python3 sovereign_harness.py "$@"
