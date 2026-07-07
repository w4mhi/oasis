#!/usr/bin/env bash
# OASIS Portable — standalone tools only (no Winlink, no APRS)   [macOS / Linux]
# ---------------------------------------------------------------------------
# Runs OASIS in the "portable" profile: the dashboard shows ONLY the features
# that do not depend on a background service —
#
#     FCC lookup · Net logger · ICS forms · Radio references ·
#     Operating tools · Handbook · calculators
#
# Winlink and APRS (and the other daemon-backed cards) are hidden and their
# server routes are disabled. Driven entirely by OASIS_FEATURES, so the suite
# runs read-mostly straight off a USB stick.
#
# This wrapper sets OASIS_FEATURES, opens the default web browser once the
# server is accepting connections, then hands off to scripts/start-server.sh
# (which bootstraps the Python environment from the bundled wheels on first run
# and starts the server, preferring gunicorn).
set -u

export OASIS_FEATURES="fcc,forms,repeaterbook"
PORT="${OASIS_PORT:-8083}"
export OASIS_PORT="$PORT"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL="http://localhost:$PORT"

echo ""
echo "  ────────────────────────────────────────"
echo "  OASIS Portable — standalone tools only"
echo "  (FCC lookup · net logger · ICS forms · radio refs · handbook)"
echo "  No Winlink · No APRS   →   $URL"
echo "  ────────────────────────────────────────"

# Open the default browser once the port is up. Runs in the background so it
# fires after the server (started below) begins accepting connections. Uses
# bash's /dev/tcp to probe the port (no curl/nc dependency); gives up after
# ~30 s and opens anyway. Silent on headless hosts where no opener exists.
_open_browser() {
  local i
  for i in $(seq 1 60); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
      exec 3>&- 3<&- 2>/dev/null
      break
    fi
    sleep 0.5
  done
  if command -v open >/dev/null 2>&1; then
    open "$URL" >/dev/null 2>&1               # macOS
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1           # Linux desktop
  fi
}
_open_browser &

# OASIS_FEATURES / OASIS_PORT are inherited by the exec'd launcher.
exec bash "$ROOT_DIR/scripts/start-server.sh"
