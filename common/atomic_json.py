"""
common/atomic_json.py
---------------------
Crash- and thread-safe JSON file writes for OASIS's flat-file config store.

`json.dump` straight to the target **truncates it first**. gunicorn runs
`--workers 1 --threads 4`, so a concurrent reader can catch that empty window,
fail to parse, and — because every reader in this codebase swallows the error and
substitutes `{}` — go on to persist that empty view over the real file. That is
not theoretical: it is exactly how `satellites.json` wiped itself to
`source: None` under concurrent `/select` calls.

The fix is the one `services/satellites/roster.py` already proved: write to a
UNIQUE temp file in the same directory (mkstemp — a per-PID name collides across
threads and tears itself), fsync, then `os.replace`, which is atomic on the same
filesystem. Readers only ever see a complete file, old or new, never a torn one.

Residual, and deliberate: two writers that both read-modify-write can still lose
an update (last writer wins). That's benign — a stale field — where a torn read
loses the operator's callsign and grid. Callers that must not clobber what they
could not read should use `read_json(..., strict=True)`.
"""

import json
import os
import tempfile


def write_json(path, data, indent=2, trailing_newline=True):
    """Atomically replace `path` with `data` as JSON. Creates parent dirs."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    prefix = "." + os.path.basename(path) + "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=indent)
            if trailing_newline:
                fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())      # survive a power cut mid-write
        os.replace(tmp, path)          # atomic on the same filesystem
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path, default=None, strict=False):
    """Parse `path`, or return `default` ({} unless given).

    A missing file always yields the default — that's a fresh install, not a
    fault. `strict=True` re-raises when a file that DOES exist can't be read or
    parsed, so a read-modify-write caller refuses to overwrite content it never
    managed to see. Without that, one unreadable moment turns into a permanent
    loss of every key the caller doesn't set itself.
    """
    default = {} if default is None else default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh) or default
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        if strict:
            raise
        return default
