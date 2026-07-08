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
import collections
import curses
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

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


def current_gains():
    """Supported gains from rtl_test, or the static R820T list as fallback."""
    try:
        out = subprocess.run(["rtl_test", "-t"], capture_output=True, text=True,
                             timeout=6).stderr
        gains = S.parse_gains(out)
        if gains:
            return gains
    except Exception:
        pass
    return list(S.STATIC_R820T_GAINS)


class State:
    def __init__(self, args, conf_path, gains):
        self.freq = args.freq
        self.conf = conf_path
        self.gains = gains
        self.gi = min(range(len(gains)),
                      key=lambda i: abs(gains[i] - float(args.gain)))
        self.ppm = args.ppm
        self.vol = float(args.vol)
        self.rate = args.rate

    @property
    def gain(self):
        return self.gains[self.gi]

    def command(self):
        return S.build_pipeline(self.freq, f"{self.gain:.1f}", self.ppm,
                                f"{self.vol:.2f}", self.rate, self.conf)


def respawn(runner, state):
    if runner is not None:
        runner.stop()
    r = PipelineRunner(state.command())
    r.start()
    return r


def run_tui(stdscr, args, conf_path):
    curses.curs_set(0)
    stdscr.nodelay(True)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)   # good
    curses.init_pair(2, curses.COLOR_RED, -1)     # high/clip
    curses.init_pair(3, curses.COLOR_YELLOW, -1)  # low
    band_attr = {"good": curses.color_pair(1),
                 "high": curses.color_pair(2),
                 "low": curses.color_pair(3)}

    state = State(args, conf_path, current_gains())
    runner = respawn(None, state)

    window = collections.deque()          # (timestamp, event)
    recent = collections.deque(maxlen=3)  # recent Decoded
    last_level = S.AudioLevel(0, 0, 0)
    WINDOW_SECS = 30

    try:
        while True:
            now = time.time()
            for line in runner.poll_lines():
                ev = S.parse_line(line)
                if ev is None:
                    continue
                window.append((now, ev))
                if isinstance(ev, S.AudioLevel):
                    last_level = ev
                elif isinstance(ev, S.Decoded):
                    recent.append(ev)
            while window and now - window[0][0] > WINDOW_SECS:
                window.popleft()

            events = [e for _, e in window]
            decodes, avg = S.score(events)
            band = S.level_band(last_level.level)

            stdscr.erase()
            stdscr.addstr(0, 1, f"APRS Tune — {state.freq}    "
                                f"gain {state.gain:.1f}  vol {state.vol:.2f}  "
                                f"ppm {state.ppm}  rate {state.rate // 1000}k")
            stdscr.addstr(1, 1, "─" * 60)
            bar = S.format_bar(last_level.level)
            stdscr.addstr(2, 1, f"level {last_level.level:3d}  ")
            stdscr.addstr(2, 12, bar, band_attr[band])
            stdscr.addstr(2, 12 + len(bar) + 2,
                          f"{band:5s}  ({last_level.lo}/{last_level.hi} demod)")
            stdscr.addstr(3, 8, "0        25        50        75       100")
            rate = decodes / WINDOW_SECS
            last_calls = "  ".join(d.src for d in list(recent)[-2:]) or "-"
            stdscr.addstr(4, 1, f"decodes  {decodes} in {WINDOW_SECS}s   "
                                f"({rate:.2f}/s)      last: {last_calls}")
            stdscr.addstr(5, 1, "─" * 60)
            stdscr.addstr(6, 1, "recent packets:")
            for i, d in enumerate(list(recent)[-3:]):
                stdscr.addstr(7 + i, 3, f"{d.src}>{d.dest}: {d.payload}"[:56])
            stdscr.addstr(11, 1, "─" * 60)
            stdscr.addstr(12, 1, "g/G gain  v/V vol  p/P ppm  r rate  "
                                 "s sweep  w save  q quit")
            if not runner.alive():
                stdscr.addstr(13, 1, "PIPELINE EXITED — check rtl_fm/dongle "
                                     "(is aprs-sdr-feed.service running?)",
                              curses.color_pair(2))
            stdscr.refresh()

            ch = stdscr.getch()
            if ch == -1:
                time.sleep(0.1)
                continue
            c = chr(ch) if 0 <= ch < 256 else ""
            if c == "q":
                break
            elif c == "g":
                state.gi = max(0, state.gi - 1); runner = respawn(runner, state)
            elif c == "G":
                state.gi = min(len(state.gains) - 1, state.gi + 1); runner = respawn(runner, state)
            elif c == "v":
                state.vol = max(0.0, round(state.vol - 0.05, 2)); runner = respawn(runner, state)
            elif c == "V":
                state.vol = min(4.0, round(state.vol + 0.05, 2)); runner = respawn(runner, state)
            elif c == "p":
                state.ppm -= 1; runner = respawn(runner, state)
            elif c == "P":
                state.ppm += 1; runner = respawn(runner, state)
            elif c == "r":
                state.rate = 48000 if state.rate == 24000 else 24000; runner = respawn(runner, state)
            elif c == "s":
                runner = run_sweep(stdscr, runner, state)   # Task 10
            elif c == "w":
                save_result(stdscr, state, decodes)         # Task 11
    finally:
        if runner is not None:
            runner.stop()


def main(argv=None):
    args = build_argparser().parse_args(argv)

    which = {b: shutil.which(b) for b in ("rtl_fm", "sox", "direwolf")}
    missing = S.check_deps(which)
    if missing:
        print(S.deps_message(missing))
        return 1

    workdir = tempfile.mkdtemp(prefix="sdr-tune-")
    conf_path = args.conf or write_conf(workdir)

    try:
        curses.wrapper(run_tui, args, conf_path)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
