#!/usr/bin/env python3
"""Apply a new APRS frequency to the running RTL-SDR feed — privileged.

Invoked as root by the dashboard's /api/aprs/frequency endpoint via a narrow
`sudo -n` sudoers rule (scripts/enable-service-controls.py → OASIS_APRS_FREQ).
It surgically rewrites the `-f <freq>` token in the aprs-sdr-feed.service
ExecStart (leaving gain / ppm / sample-rate / socat port untouched), reloads
systemd, and restarts the feed so the change is live.

The chosen frequency is *persisted* separately in configuration/station.json by
the (unprivileged) web layer, and enable-rtl-sdr.py reads it back as its default,
so this applier only ever touches the live unit — it does not own the setting.

Usage:  set-aprs-freq.py <freq>      e.g. set-aprs-freq.py 144.800M

Exit codes:  0 applied · 2 feed not installed · 3 invalid frequency ·
             4 unit has no rtl_fm -f to rewrite · 1 other error.
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sdr_tune import normalize_freq

SERVICE_NAME = "aprs-sdr-feed.service"
SERVICE_PATH = f"/etc/systemd/system/{SERVICE_NAME}"

# Match the rtl_fm invocation's frequency token (the segment up to the first
# pipe, so socat's flags are never touched) and capture the `-f <token>` in it.
_EXECSTART_FREQ_RE = re.compile(r"(rtl_fm\b[^|\n]*?\s-f\s+)(\S+)")


def rewrite_execstart_freq(unit_text, new_freq):
    """Return `unit_text` with rtl_fm's `-f <freq>` replaced by `new_freq`, or
    None if no rtl_fm `-f` token is present (nothing to rewrite)."""
    if not _EXECSTART_FREQ_RE.search(unit_text):
        return None
    return _EXECSTART_FREQ_RE.sub(lambda m: m.group(1) + new_freq, unit_text, count=1)


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: set-aprs-freq.py <freq>\n")
        return 1
    freq = normalize_freq(argv[1])
    if not freq:
        sys.stderr.write(f"invalid frequency: {argv[1]!r}\n")
        return 3
    if not os.path.exists(SERVICE_PATH):
        sys.stderr.write(f"{SERVICE_NAME} not installed\n")
        return 2
    try:
        with open(SERVICE_PATH, "r", encoding="utf-8") as fh:
            unit = fh.read()
    except OSError as exc:
        sys.stderr.write(f"could not read {SERVICE_PATH}: {exc}\n")
        return 1
    new_unit = rewrite_execstart_freq(unit, freq)
    if new_unit is None:
        sys.stderr.write(f"{SERVICE_NAME} ExecStart has no 'rtl_fm -f' to rewrite\n")
        return 4
    if new_unit != unit:
        try:
            with open(SERVICE_PATH, "w", encoding="utf-8") as fh:
                fh.write(new_unit)
        except OSError as exc:
            sys.stderr.write(f"could not write {SERVICE_PATH}: {exc}\n")
            return 1
        subprocess.run(["systemctl", "daemon-reload"], check=False)
    # Restart even when the text was unchanged — the operator asked to apply, and
    # a restart is the cheapest way to guarantee the running feed matches.
    subprocess.run(["systemctl", "restart", SERVICE_NAME], check=False)
    sys.stdout.write(f"aprs-sdr-feed frequency set to {freq}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
