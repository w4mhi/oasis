#!/usr/bin/env bash
# OASIS - Off-grid Amateur Station Information Suite — launcher for Linux and macOS (terminal)
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
"$VENV_PIP" install \
    --quiet \
    --no-index \
    --find-links "$WHEELS_DIR" \
    flask gunicorn 2>&1 | grep -v "already satisfied" || true

# ── Pre-flight check ──────────────────────────────────────────────────────────
echo ""
echo "  ────────────────────────────────────────"
echo "  Pre-flight check"
echo "  ────────────────────────────────────────"

_check() {
    local label="$1" ok="$2" note="$3"
    if [[ "$ok" == "1" ]]; then
        printf "  ✓  %-20s %s\n" "$label" "$note"
    else
        printf "  ✗  %-20s %s\n" "$label" "$note"
    fi
}
_warn_check() {
    local label="$1" note="$2"
    printf "  ⚠  %-20s %s\n" "$label" "$note"
}

# Python
PY_VER=$("$PYTHON" --version 2>&1)
_check "Python" "1" "$PY_VER"

# Flask
"$VENV_PYTHON" -c "import flask" 2>/dev/null \
    && _check "Flask" "1" \
    || _check "Flask" "0" "→ run: python3 scripts/setup-server.py"

# gunicorn (optional — used as WSGI server when available)
"$VENV_PYTHON" -c "import gunicorn" 2>/dev/null \
    && _check "gunicorn" "1" "(WSGI server)" \
    || _warn_check "gunicorn" "(not installed — using Flask dev server)"

# psutil (optional)
"$VENV_PYTHON" -c "import psutil" 2>/dev/null \
    && _check "psutil" "1" \
    || _warn_check "psutil" "(APRS stats unavailable)"

# FCC index
FCC_INDEX="$SCRIPT_DIR/fcc-offline-database/data/EN.idx"
if [[ -f "$FCC_INDEX" ]]; then
    FCC_KB=$(( $(wc -c < "$FCC_INDEX") / 1024 ))
    _check "FCC index" "1" "${FCC_KB} KB"
else
    _check "FCC index" "0" "→ run: python3 scripts/setup-fcc-database.py"
fi

echo "  ────────────────────────────────────────"
echo ""

# ── Launch server ─────────────────────────────────────────────────────────────
PORT=8083
echo "  Starting OASIS..."
echo ""
cd "$SERVER_DIR"
if "$VENV_PYTHON" -c "import gunicorn" 2>/dev/null; then
    exec "$VENV_DIR/bin/gunicorn" \
        --workers 2 \
        --bind "0.0.0.0:$PORT" \
        --access-logfile - \
        app:app
else
    exec "$VENV_PYTHON" app.py
fi
