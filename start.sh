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
if [[ ! -x "$VENV_DIR/bin/python" ]] && [[ ! -x "$VENV_DIR/bin/python3" ]]; then
    echo "  Creating virtual environment..."
    rm -rf "$VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
fi

# Prefer 'python' symlink, fall back to 'python3'
if [[ -x "$VENV_DIR/bin/python" ]]; then
    VENV_PYTHON="$VENV_DIR/bin/python"
elif [[ -x "$VENV_DIR/bin/python3" ]]; then
    VENV_PYTHON="$VENV_DIR/bin/python3"
else
    echo ""
    echo "  ERROR: Virtual environment is broken. Delete '$VENV_DIR' and re-run."
    echo ""
    exit 1
fi
VENV_PIP="$VENV_DIR/bin/pip"

# ── Install Flask + psutil from bundled wheels (offline, idempotent) ────────────
"$VENV_PIP" install \
    --quiet \
    --no-index \
    --find-links "$WHEELS_DIR" \
    flask gunicorn psutil 2>&1 | grep -v "already satisfied" || true

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

# psutil
"$VENV_PYTHON" -c "import psutil" 2>/dev/null \
    && _check "psutil" "1" \
    || _check "psutil" "0" "→ run: python3 scripts/setup-server.py"

# FCC index
FCC_INDEX="$SCRIPT_DIR/fcc-offline-database/data/EN.idx"
if [[ -f "$FCC_INDEX" ]]; then
    FCC_KB=$(( $(wc -c < "$FCC_INDEX") / 1024 ))
    _check "FCC index" "1" "${FCC_KB} KB"
else
    _check "FCC index" "0" "→ run: python3 scripts/setup-fcc-database.py"
fi

# System fonts (Linux only — needed for emoji/mono rendering in Chromium)
if [[ "$(uname)" == "Linux" ]]; then
    if dpkg-query -W -f='${Status}' fonts-noto-color-emoji 2>/dev/null | grep -q "install ok installed"; then
        _check "System fonts" "1" "fonts-noto-color-emoji"
    else
        _warn_check "System fonts" "(emoji may not render → run: python3 scripts/setup-server.py)"
    fi
fi

echo "  ────────────────────────────────────────"
echo ""

# ── Launch server ─────────────────────────────────────────────────────────────
PORT=8083
echo "  Starting OASIS..."
echo ""

# macOS: gunicorn uses fork() which clashes with the Objective-C runtime when
# any ObjC class is being initialised in another thread at fork time. Setting
# this env var tells the ObjC runtime not to abort in the child process.
if [[ "$(uname)" == "Darwin" ]]; then
    export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
fi

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
