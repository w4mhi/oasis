#!/usr/bin/env bash
# run-tests.sh — run the OASIS unit suite the supported way, in one command.
#
# Two problems this solves (analysis-report §2/P2):
#   1. `python3 -m unittest` on system Python fails with ModuleNotFoundError:
#      flask — the suite needs the .venv that scripts/setup-server.py builds.
#      This script always uses .venv/bin/python.
#   2. Several tests exercise code that prints banners/progress to stdout, which
#      buries the final OK/FAILED line. unittest's -b/--buffer captures per-test
#      stdout/stderr and only shows it when that test fails — so a green run is
#      quiet and the verdict is the last line.
#
# Usage:
#   scripts/run-tests.sh                 # full suite, quiet
#   scripts/run-tests.sh -v              # verbose (per-test names)
#   scripts/run-tests.sh -k forms_backup # only tests matching a substring
#   scripts/run-tests.sh -f              # stop on first failure
# Extra args pass straight through to `unittest discover`.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PY="$REPO_ROOT/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
  echo "error: $VENV_PY not found." >&2
  echo "       Build the server venv first:  python3 scripts/setup-server.py" >&2
  exit 1
fi

cd "$REPO_ROOT"
exec "$VENV_PY" -m unittest discover -s tests -b "$@"
