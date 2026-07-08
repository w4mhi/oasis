#!/usr/bin/env python3
"""
tune-rtl-sdr.py
---------------
Interactive RTL-SDR APRS tuning bench (curses TUI, run over SSH on the Pi).

Wraps  rtl_fm | sox | direwolf  and lets you live-adjust gain, sox volume, ppm,
and the audio sample rate (24k/48k) while watching Direwolf's 46(6/6) audio-level
feedback as a banded bar graph, plus a rolling decode count and recent callsigns.
Press 's' to auto-sweep gain then ppm; 'w' to save the known-good settings and
emit the exact GrayWolf feed command. Receive-only — an RTL-SDR cannot transmit.

Pure logic (command builder, parser, scorer, sweep ranking, formatter) lives in
scripts/common/sdr_tune.py and is unit-tested. This file holds the subprocess
runner, the curses loop, and sweep driving.

Usage:
  python3 features/rtl-sdr/tune-rtl-sdr.py                 # 144.390M, 24k
  python3 features/rtl-sdr/tune-rtl-sdr.py --freq 144.800M # EU APRS
  python3 features/rtl-sdr/tune-rtl-sdr.py --conf my.conf  # reuse an existing conf

Requires: Linux, rtl_fm + sox + direwolf (install-rtl-sdr.py provides them),
a dongle + 2 m antenna. Stop aprs-sdr-feed.service first — one process owns the
dongle.
"""

import argparse
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from common import sdr_tune as S


class PipelineRunner:
    """Runs the rtl_fm|sox|direwolf shell pipeline in its own process group and
    streams Direwolf's stdout/stderr lines into a thread-safe queue. The whole
    group is killed as a unit on stop() or on any parameter change, so no rtl_fm
    is ever orphaned on the USB device."""

    def __init__(self, command):
        self.command = command
        self._proc = None
        self._q = queue.Queue()
        self._reader = None

    def start(self):
        self._proc = subprocess.Popen(
            self.command, shell=True, start_new_session=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self):
        try:
            for line in self._proc.stdout:
                self._q.put(line.rstrip("\n"))
        except (ValueError, OSError):
            pass

    def poll_lines(self):
        """Non-blocking drain of everything queued since the last call."""
        out = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out

    def alive(self):
        return self._proc is not None and self._proc.poll() is None

    def stop(self):
        if self._proc is None:
            return
        try:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                self._proc.wait(timeout=1)   # reap; SIGKILL is unblockable
            except subprocess.TimeoutExpired:
                pass
        if self._proc.stdout:
            self._proc.stdout.close()        # unblock _pump; no ResourceWarning
        if self._reader is not None:
            self._reader.join(timeout=1)     # deterministic teardown
        self._proc = None


def build_argparser():
    p = argparse.ArgumentParser(
        description="Interactive RTL-SDR APRS tuning bench (curses TUI).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--freq", default="144.390M",
                   help="APRS frequency (default 144.390M; EU 144.800M).")
    p.add_argument("--gain", default="32.8", help="Initial tuner gain (dB).")
    p.add_argument("--ppm", type=int, default=0, help="Initial ppm correction.")
    p.add_argument("--vol", default="0.50", help="Initial sox vol factor.")
    p.add_argument("--rate", type=int, default=24000, choices=(24000, 48000),
                   help="Initial audio sample rate.")
    p.add_argument("--conf", default=None,
                   help="Reuse an existing Direwolf conf instead of generating one.")
    return p


def write_conf(logdir):
    """Write the generated Direwolf conf into a temp dir; return its path."""
    conf_path = os.path.join(logdir, "sdr.conf")
    with open(conf_path, "w") as fh:
        fh.write(S.SDR_CONF_TEMPLATE.format(logdir=logdir))
    return conf_path


def main(argv=None):
    args = build_argparser().parse_args(argv)

    which = {b: shutil.which(b) for b in ("rtl_fm", "sox", "direwolf")}
    missing = S.check_deps(which)
    if missing:
        print(S.deps_message(missing))
        return 1

    workdir = tempfile.mkdtemp(prefix="sdr-tune-")
    conf_path = args.conf or write_conf(workdir)

    import curses
    curses.wrapper(run_tui, args, conf_path)   # run_tui added in Task 9
    return 0


if __name__ == "__main__":
    sys.exit(main())
