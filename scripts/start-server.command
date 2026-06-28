#!/usr/bin/env bash
# EmComm Tools — macOS double-click launcher (.command files run in Terminal)
# Place this file at the root of the EmComm Tools folder on the USB drive.

# Keep Terminal window open on error
trap 'echo ""; echo "  Press any key to close..."; read -n1' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$SCRIPT_DIR/start-server.sh"
