#!/usr/bin/env bash
# OASIS Portable — macOS double-click launcher (.command files open in Terminal).
# ---------------------------------------------------------------------------
# Thin wrapper around run-portable.sh, which holds the real logic (sets the
# portable profile, opens the browser, starts the server). Kept as a separate
# file so Finder users can just double-click; terminal/Linux users run the .sh.

# Keep the Terminal window open if something fails (double-click UX).
trap 'echo ""; echo "  Press any key to close..."; read -r -n1' ERR

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT_DIR/run-portable.sh"
