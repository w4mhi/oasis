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
  python3 features/rtl-sdr/tune-rtl-sdr.py                 # 144.390M, 48k
  python3 features/rtl-sdr/tune-rtl-sdr.py --freq 144.800M # EU APRS
  python3 features/rtl-sdr/tune-rtl-sdr.py --conf my.conf  # reuse an existing conf

Requires: Linux, rtl_fm + sox + direwolf (install-rtl-sdr.py provides them),
a dongle + 2 m antenna. Stop aprs-sdr-feed.service first — one process owns the
dongle.
"""

import argparse
import collections
import curses
import datetime
import json
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


RESULTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "tune-results.json")


def append_result(path, record):
    """Append a tuning record to a JSON array file (create if absent)."""
    data = []
    if os.path.exists(path):
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (ValueError, OSError):
            data = []
    data.append(record)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)


def save_result(stdscr, state, decodes):
    record = {
        "freq": state.freq,
        "gain": round(state.gain, 1),
        "ppm": state.ppm,
        "vol": round(state.vol, 2),
        "srate": state.rate,
        "decodes_per_min": round(decodes * 2),   # 30 s window → per-minute
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    append_result(RESULTS_PATH, record)
    feed = S.build_feed_command(state.freq, f"{state.gain:.1f}", state.ppm)
    _sweep_message(
        stdscr,
        "Saved to features/rtl-sdr/tune-results.json\n\n"
        "  GrayWolf feed command for aprs-sdr-feed.service:\n"
        f"  {feed}\n\n"
        f"  (bench vol {state.vol:.2f} recorded for reference; not used by the "
        "GrayWolf path)\n\n  Press any key.")


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
        # Raw output ring buffer: keeps the last lines the pipeline emitted so
        # rtl_fm/sox/direwolf error text survives even though parse_line drops
        # everything that isn't an audio-level/packet line. Guarded by a lock
        # because the reader thread appends while the UI thread snapshots it.
        self._tail = collections.deque(maxlen=12)
        self._tail_lock = threading.Lock()

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
                text = line.rstrip("\n")
                self._q.put(text)
                with self._tail_lock:
                    self._tail.append(text)
        except (ValueError, OSError):
            pass

    def tail(self):
        """Snapshot of the most recent raw output lines (thread-safe)."""
        with self._tail_lock:
            return list(self._tail)

    def returncode(self):
        """Exit code of the shell pipeline, or None while it is still running."""
        return self._proc.poll() if self._proc is not None else None

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
    p.add_argument("--rate", type=int, default=48000, choices=(24000, 48000),
                   help="Initial audio sample rate (default 48000, matches "
                        "GrayWolf's feed).")
    p.add_argument("--conf", default=None,
                   help="Reuse an existing Direwolf conf instead of generating one.")
    return p


def write_conf(logdir):
    """Create a default Direwolf conf in the current working directory when it
    does not already exist; otherwise reuse the existing file."""
    conf_path = os.path.join(os.getcwd(), "rtl-sdr-direwolf.conf")
    if os.path.exists(conf_path):
        return conf_path
    with open(conf_path, "w") as fh:
        fh.write(S.SDR_CONF_TEMPLATE.format(logdir=logdir))
    return conf_path


def probe_device():
    """Run `rtl_test -t` once up front. Return (error_reason, gains) where
    error_reason is "busy"/"absent"/None and gains is the supported gain list
    (static R820T fallback when the dongle can't be queried)."""
    try:
        out = subprocess.run(["rtl_test", "-t"], capture_output=True, text=True,
                             timeout=6).stderr
    except Exception:
        return None, list(S.STATIC_R820T_GAINS)   # can't probe — don't block launch
    reason = S.device_error(out)
    gains = S.parse_gains(out) or list(S.STATIC_R820T_GAINS)
    return reason, gains


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


def run_tui(stdscr, args, conf_path, gains):
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

    state = State(args, conf_path, gains)
    runner = respawn(None, state)

    window = collections.deque()          # (timestamp, event)
    recent = collections.deque(maxlen=3)  # recent Decoded
    last_level = S.AudioLevel(0, 0, 0)
    last_stat = None                      # most recent AudioStat (-a 2 heartbeat)
    last_stat_ts = 0.0
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
                elif isinstance(ev, S.AudioStat):
                    last_stat = ev
                    last_stat_ts = now
                    # Continuous CH0 level drives the bar even with zero decodes.
                    last_level = last_level._replace(level=ev.level)
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
            # Audio-flow heartbeat from Direwolf -a 2 (proves the RF/USB/audio
            # path is alive independent of decodes — the bench's pkt/s-equivalent).
            alive = runner.alive()
            flowing = last_stat is not None and (now - last_stat_ts) < 6.0
            if flowing:
                nom = state.rate / 1000.0
                sag = abs(last_stat.rate_k - nom) > nom * 0.10   # >10% off nominal
                audio_txt = (f"audio: flowing  ~{last_stat.rate_k:.1f}k  "
                             f"CH0 {last_stat.level}  err {last_stat.errors}")
                audio_attr = band_attr["low"] if sag else band_attr["good"]
            elif alive:
                audio_txt = "audio: STALLED — no samples arriving (check SDR/USB)"
                audio_attr = band_attr["high"]
            else:
                audio_txt = "audio: —  (pipeline down)"
                audio_attr = 0
            stdscr.addstr(4, 1, audio_txt, audio_attr)
            rate = decodes / WINDOW_SECS
            last_calls = "  ".join(d.src for d in list(recent)[-2:]) or "-"
            stdscr.addstr(5, 1, f"decodes  {decodes} in {WINDOW_SECS}s   "
                                f"({rate:.2f}/s)      last: {last_calls}")
            stdscr.addstr(6, 1, "─" * 60)
            stdscr.addstr(7, 1, "recent packets:")
            for i, d in enumerate(list(recent)[-3:]):
                stdscr.addstr(8 + i, 3, f"{d.src}>{d.dest}: {d.payload}"[:56])
            h, _w = stdscr.getmaxyx()
            stdscr.addstr(11, 1, "─" * 60)
            if h > 12:
                stdscr.addstr(12, 1, "g/G gain  v/V vol  p/P ppm  r rate  "
                                     "f freq  s sweep  w save  q quit")
            if not runner.alive() and h > 13:
                rc = runner.returncode()
                stdscr.addstr(13, 1,
                              f"PIPELINE EXITED (exit {rc}) — check rtl_fm/dongle "
                              "(is aprs-sdr-feed.service running?)",
                              curses.color_pair(2))
                # Show the last raw lines so the operator can see the actual
                # rtl_fm/sox/direwolf error instead of guessing.
                tail = [t for t in runner.tail() if t.strip()]
                for i, t in enumerate(tail[-(h - 15):]):
                    if 14 + i < h:
                        stdscr.addstr(14 + i, 3, t[:_w - 4],
                                      curses.color_pair(3))
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
            elif c == "f":
                entered = _prompt(stdscr, "New frequency (e.g. 144.800M): ")
                newfreq = S.normalize_freq(entered)
                if newfreq:
                    state.freq = newfreq; runner = respawn(runner, state)
                elif entered:
                    _sweep_message(stdscr, f"'{entered}' is not a valid "
                                           "frequency (24 MHz–1.766 GHz). "
                                           "Press any key.")
            elif c == "s":
                runner = run_sweep(stdscr, runner, state)   # Task 10
            elif c == "w":
                save_result(stdscr, state, decodes)         # Task 11
    except KeyboardInterrupt:
        pass   # Ctrl-C at the main screen = clean quit (no traceback)
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

    reason, gains = probe_device()
    if reason:
        print(S.device_help(reason))
        return 1

    workdir = tempfile.mkdtemp(prefix="sdr-tune-")
    conf_path = args.conf or write_conf(workdir)

    try:
        curses.wrapper(run_tui, args, conf_path, gains)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


STEP_SECS = 60           # fixed listen window per sweep step
STEP_MIN_PKTS = 3        # a step must decode at least this many packets to score


def _measure(stdscr, state, label, step_n, step_m, global_i, total_steps, log):
    """Fixed-window step: listen for STEP_SECS and count decodes. A step needs
    at least STEP_MIN_PKTS packets to be scored (more packets = better score);
    fewer is inconclusive. Press 'q' to abort the whole sweep.
    Returns (decodes, avg_level, aborted)."""
    runner = PipelineRunner(state.command())
    runner.start()
    events = []
    t0 = time.time()
    aborted = False
    try:
        while True:
            for line in runner.poll_lines():
                ev = S.parse_line(line)
                if ev is not None:
                    events.append(ev)
            d, a = S.score(events)
            elapsed = time.time() - t0
            if elapsed >= STEP_SECS:
                break
            ch = stdscr.getch()
            if ch != -1 and 0 <= ch < 256 and chr(ch) == "q":
                aborted = True
                break
            # Worst-case time left = full windows still queued after this one,
            # plus the rest of this window. Labelled "≤".
            overall = (total_steps - global_i) * STEP_SECS + \
                      max(0.0, STEP_SECS - elapsed)
            stdscr.erase()
            stdscr.addstr(0, 1, f"SWEEP {label}  step {step_n}/{step_m}   "
                                f"listening… {elapsed:.0f}/{STEP_SECS}s   "
                                f"[q/Ctrl-C abort]")
            stdscr.addstr(1, 1, f"overall  \u2264 {overall:.0f}s left")
            stdscr.addstr(2, 1, f"gain {state.gain:.1f}  ppm {state.ppm}   "
                                f"decodes {d} (need {STEP_MIN_PKTS})")
            stat = next((e for e in reversed(events)
                         if isinstance(e, S.AudioStat)), None)
            if stat is not None:
                stdscr.addstr(3, 1, f"audio ~{stat.rate_k:.1f}k  CH0 {stat.level}"
                                    f"  err {stat.errors}")
            for j, row in enumerate(list(log)[-6:]):
                stdscr.addstr(4 + j, 3, row[:72])
            stdscr.refresh()
            time.sleep(0.2)
    except KeyboardInterrupt:
        aborted = True   # Ctrl-C aborts the sweep, not the whole bench
    finally:
        runner.stop()

    d, a = S.score(events)
    tag = f"{label} g{state.gain:.1f}/ppm{state.ppm}"
    if aborted:
        return d, a, True
    pkts = [e for e in events if isinstance(e, S.Decoded)]
    if d >= STEP_MIN_PKTS:
        log.append(f"{tag}: {d} pkts, last {pkts[-1].src} (level {a:.0f})")
    else:
        log.append(f"{tag}: heard {d}/{STEP_MIN_PKTS}. Inconclusive")
    return d, a, False


def run_sweep(stdscr, runner, state):
    if runner is not None:
        runner.stop()

    orig_gi = state.gi
    orig_ppm = state.ppm

    ppm_vals = S.ppm_sweep_values()
    n_gain = len(state.gains)
    total_steps = n_gain + len(ppm_vals)
    log = collections.deque(maxlen=8)   # rolling per-step outcomes

    # Stage 1: gain.
    results = []
    for i, g in enumerate(state.gains, 1):
        state.gi = i - 1
        d, a, aborted = _measure(stdscr, state, "GAIN", i, n_gain,
                                 global_i=i, total_steps=total_steps, log=log)
        if aborted:
            state.gi = orig_gi
            state.ppm = orig_ppm
            _sweep_message(stdscr, "Sweep aborted (q/Ctrl-C) — parameters left "
                                   "unchanged. Press any key.")
            return respawn(None, state)
        results.append((g, d, a))
    best_gain = S.rank_sweep(results, min_decodes=STEP_MIN_PKTS)

    if best_gain is None:
        state.gi = orig_gi          # no conclusive step — leave params unchanged
        state.ppm = orig_ppm
        _sweep_message(stdscr, f"No gain step heard {STEP_MIN_PKTS}+ packets — "
                               "inconclusive, parameters left unchanged. "
                               "Press any key.")
        return respawn(None, state)
    state.gi = state.gains.index(best_gain)

    # Stage 2: ppm at the best gain.
    results = []
    for i, p in enumerate(ppm_vals, 1):
        state.ppm = p
        d, a, aborted = _measure(stdscr, state, "PPM", i, len(ppm_vals),
                                 global_i=n_gain + i, total_steps=total_steps,
                                 log=log)
        if aborted:
            state.ppm = orig_ppm    # keep the winning gain, restore ppm
            _sweep_message(stdscr, f"Sweep aborted (q/Ctrl-C) during PPM — gain "
                                   f"{best_gain:.1f} kept, ppm restored. "
                                   "Press any key.")
            return respawn(None, state)
        results.append((p, d, a))
    best_ppm = S.rank_sweep(results, min_decodes=STEP_MIN_PKTS)
    state.ppm = best_ppm if best_ppm is not None else orig_ppm

    _sweep_message(stdscr, f"Sweep done: gain {best_gain:.1f}, "
                           f"ppm {state.ppm}. Press any key.")
    return respawn(None, state)


def _sweep_message(stdscr, msg):
    stdscr.erase()
    for i, row in enumerate(msg.split("\n")):
        stdscr.addstr(1 + i, 1, row)
    stdscr.refresh()
    stdscr.nodelay(False)
    stdscr.getch()
    stdscr.nodelay(True)


def _prompt(stdscr, label):
    """Read a line of text from the operator on the bottom row (blocking)."""
    h, w = stdscr.getmaxyx()
    curses.echo()
    curses.curs_set(1)
    stdscr.nodelay(False)
    try:
        stdscr.addstr(h - 1, 1, " " * (w - 2))
        stdscr.addstr(h - 1, 1, label)
        stdscr.refresh()
        raw = stdscr.getstr(h - 1, 1 + len(label), 24)
    finally:
        curses.noecho()
        curses.curs_set(0)
        stdscr.nodelay(True)
    return raw.decode("utf-8", "replace").strip()


if __name__ == "__main__":
    sys.exit(main())
