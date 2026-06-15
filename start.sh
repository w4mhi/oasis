#!/usr/bin/env bash
# EmComm Tools — launcher for Linux and macOS (terminal)
set -e

# ── Resolve the suite root (wherever this script lives) ──────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$SCRIPT_DIR/server"
WHEELS_DIR="$SERVER_DIR/wheels"
VENV_DIR="$SCRIPT_DIR/.venv"

# ── Find Python 3 ─────────────────────────────────────────────────────────────
PYTHON=""
for candidate in python3 python3.12 python3.11 python3.10 python3.9 python; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c "import sys; print(sys.version_info >= (3,9))" 2>/dev/null)
        if [[ "$ver" == "True" ]]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo ""
    echo "  ERROR: Python 3.9+ not found."
    echo "  Install it from https://python.org and re-run this script."
    echo ""
    exit 1
fi

echo ""
echo "  Using: $($PYTHON --version)"

# ── Create virtualenv if not present ─────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
    echo "  Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# ── Install Flask from bundled wheels (offline, idempotent) ───────────────────
echo "  Installing dependencies..."
"$VENV_PIP" install \
    --quiet \
    --no-index \
    --find-links "$WHEELS_DIR" \
    flask 2>&1 | grep -v "already satisfied" || true

# ── Launch server ─────────────────────────────────────────────────────────────
echo "  Starting EmComm Tools..."
echo ""
cd "$SERVER_DIR"
exec "$VENV_PYTHON" app.py
