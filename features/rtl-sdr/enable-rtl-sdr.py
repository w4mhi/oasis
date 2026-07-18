#!/usr/bin/env python3
"""
enable-rtl-sdr.py
-----------------
Bring an installed RTL-SDR online as a GrayWolf APRS receive source.

Pairs with features/rtl-sdr/install-rtl-sdr.py: that one installs the tools, this one tests the
dongle, wires up the always-on audio feed, and tells you how to finish in the
GrayWolf web UI. Receive-only — an RTL-SDR cannot transmit.

What this does:
  1. Checks prerequisites (Linux + systemd, rtl_fm, socat, tcpdump — all
     installed by features/rtl-sdr/install-rtl-sdr.py)
  2. Tests the SDR: rtl_test sees the dongle, then a short live capture at the
     APRS frequency confirms real demodulated audio (measures RMS — no speakers
     needed). Prints OK when the chain is good.
  3. Installs + enables aprs-sdr-feed.service:
        rtl_fm -f <freq> -M fm -s 48000 -g <gain> -p <ppm> -
          | socat -u -b 1920 - UDP-SENDTO:127.0.0.1:<port>
     (-s 48000 because this rtl_fm build ignores -r; -b 1920 because GrayWolf's
      sdr_udp reader silently drops socat's default 8192-byte datagrams.)
  4. Inspects GrayWolf's journal (journalctl) to report whether its modem is
     RUNNING, still on a soundcard (POLLERR), or has no channel configured yet.
  5. Prints the step-by-step GrayWolf web-UI setup.

The GrayWolf web-UI steps are manual on purpose — GrayWolf has no config-file
import, so the channel/device must be created in the browser. See
docs/sdr-to-graywolf.md for the full writeup and troubleshooting table.

Usage:
  python3 features/rtl-sdr/enable-rtl-sdr.py                 # test + enable + instructions
  python3 features/rtl-sdr/enable-rtl-sdr.py --freq 144.800M # EU APRS
  python3 features/rtl-sdr/enable-rtl-sdr.py --gain 28 --ppm 12
  python3 features/rtl-sdr/enable-rtl-sdr.py --check         # test only, no service changes
  python3 features/rtl-sdr/enable-rtl-sdr.py --no-enable     # write the unit but don't start it

Requires: Linux, systemd, sudo. rtl_fm + socat + tcpdump — all installed by
features/rtl-sdr/install-rtl-sdr.py (run that first). A dongle and 2 m antenna plugged in. Stop
any manual 'rtl_fm | socat' first — only one process can own the dongle.
"""

import argparse
import array
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time

# ── Shared library ─────────────────────────────────────────────────────
_SUITE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')
sys.path.insert(0, _SUITE_ROOT)
from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run
from common import config_paths

DEFAULT_FREQ = "144.390M"           # NA 2 m APRS, used when station.json has none


def _station_freq():
    """The operator's chosen APRS frequency from configuration/station.json, or
    the NA default. Setup persists it there; this keeps a re-enable on-frequency
    instead of snapping back to 144.390M."""
    try:
        import json
        with open(config_paths.station_json(_SUITE_ROOT), "r", encoding="utf-8") as fh:
            freq = (json.load(fh) or {}).get("aprs_freq")
        return freq.strip() if isinstance(freq, str) and freq.strip() else DEFAULT_FREQ
    except Exception:
        return DEFAULT_FREQ

SERVICE_NAME = "aprs-sdr-feed.service"
SERVICE_PATH = f"/etc/systemd/system/{SERVICE_NAME}"
SAMPLE_RATE  = 48000           # rtl_fm -s; must equal GrayWolf's sample_rate
DATAGRAM     = 1920            # socat -b: one 20 ms audio chunk (960 samples x 2 B)
DEFAULT_PORT = 7355            # GQRX UDP-audio convention; matches docs/sdr-to-graywolf.md


# ── Step 1: Prerequisites ──────────────────────────────────────────────────────
def check_prereqs():
    _step(1, "Checking prerequisites")

    if sys.platform != "linux":
        _fail("This script configures a systemd service — Linux only.")

    if not shutil.which("systemctl"):
        _fail("systemctl not found. This script targets systemd systems "
              "(Raspberry Pi OS / Debian / Ubuntu).")

    # rtl_fm, socat and tcpdump are all installed (offline-first, apt fallback)
    # by features/rtl-sdr/install-rtl-sdr.py — this script only checks they're present.
    rtl_fm = shutil.which("rtl_fm")
    if not rtl_fm:
        _fail("rtl_fm not found. Install the RTL-SDR tools first:\n"
              "       python3 features/rtl-sdr/install-rtl-sdr.py")
    _ok(f"rtl_fm: {rtl_fm}")

    socat = shutil.which("socat")
    if not socat:
        _fail("socat not found (it carries the feed). Run the installer, which "
              "bundles it:\n       python3 features/rtl-sdr/install-rtl-sdr.py")
    _ok(f"socat:  {socat}")

    # tcpdump is only used for verification — warn but continue without it.
    tcpdump = shutil.which("tcpdump")
    if tcpdump:
        _ok(f"tcpdump: {tcpdump}")
    else:
        _warn("tcpdump not found — verification step 3 (UDP capture) won't work. "
              "Install it: python3 features/rtl-sdr/install-rtl-sdr.py")

    return rtl_fm, socat


# ── Step 2: Test the SDR ────────────────────────────────────────────────────────
def rtl_test_present():
    """Return True if rtl_test reports a device, False if none is plugged in.
    The R820T '-t' abort is expected and still counts as present."""
    try:
        # errors="replace": rtl_test can emit non-UTF-8 bytes (e.g. 0x83 from the
        # R820T -t probe); without it text-mode decoding raises UnicodeDecodeError
        # from inside subprocess.run — not one of the excepts below — and aborts.
        r = subprocess.run(["rtl_test", "-t"], capture_output=True, text=True,
                           errors="replace", timeout=20)
    except FileNotFoundError:
        _fail("rtl_test not found — features/rtl-sdr/install-rtl-sdr.py did not complete.")
    except subprocess.TimeoutExpired as e:
        # -t normally exits on its own; a timeout still means a device responded.
        out = (e.stdout or b"")
        out = out.decode(errors="replace") if isinstance(out, bytes) else (out or "")
        return "Found" in out and "device" in out

    out = r.stdout + r.stderr
    if "No supported devices found" in out or "usb_open error" in out:
        return False   # no dongle enumerated — caller decides (defer vs. report)
    # 'No E4000 tuner found, aborting' + 'PLL not locked!' are EXPECTED on R820T.
    for line in out.splitlines():
        if "Found" in line and "device" in line:
            _ok(line.strip())
        if "tuner" in line.lower() and "Found" in line:
            _info(line.strip())
    _info("('PLL not locked' + 'No E4000 tuner found, aborting' is expected on "
          "R820T — that's just the E4000-specific -t probe bailing out.)")
    return True


def capture_rms(rtl_fm, freq, gain, ppm, seconds):
    """Run rtl_fm for *seconds* and measure the audio. Returns (peak, floor, chunks, stderr)."""
    cmd = [rtl_fm, "-f", freq, "-M", "fm", "-s", str(SAMPLE_RATE),
           "-g", str(gain), "-p", str(ppm), "-"]
    _info("Capturing: " + " ".join(cmd))
    errfile = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errfile)

    peak, floor, chunks = 0.0, None, 0
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            buf = proc.stdout.read(SAMPLE_RATE // 5)   # ~0.1 s of S16 mono
            if not buf:
                break
            samples = array.array("h")
            samples.frombytes(buf[: len(buf) // 2 * 2])
            if not samples:
                continue
            rms = math.sqrt(sum(s * s for s in samples) / len(samples))
            peak = max(peak, rms)
            floor = rms if floor is None else min(floor, rms)
            chunks += 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    errfile.seek(0)
    err = errfile.read().decode(errors="replace")
    errfile.close()
    return peak, floor, chunks, err


def test_sdr(rtl_fm, freq, gain, ppm, seconds):
    """Return True if live demodulated audio was verified, False if no RTL-SDR is
    present. A False result is not fatal — the caller installs the feed service
    anyway (it's deferred and starts when the dongle appears)."""
    _step(2, "Testing the SDR")
    if not rtl_test_present():
        _warn("No RTL-SDR present.")
        _info("No dongle is plugged in (or it hasn't enumerated yet — a reboot "
              "applies the DVB-driver blacklist if you just installed the tools). "
              "Skipping the audio test; the feed service is still installed and "
              "enabled, and starts automatically once the dongle appears "
              "(Restart=always). Verify reception later with:\n"
              "       python3 features/rtl-sdr/enable-rtl-sdr.py --check")
        return False

    _info(f"Listening on {freq} for {seconds}s (hardware tunes ~252 kHz higher — "
          "that offset is normal)...")
    peak, floor, chunks, err = capture_rms(rtl_fm, freq, gain, ppm, seconds)

    if "Output at" in err:
        for line in err.splitlines():
            if "Output at" in line or "Tuned to" in line:
                _info(line.strip())
        if f"Output at {SAMPLE_RATE} Hz" not in err:
            _warn(f"Expected 'Output at {SAMPLE_RATE} Hz' — check the rate.")

    if chunks == 0 or peak == 0:
        _warn("No audio samples captured. rtl_fm said:")
        for line in err.strip().splitlines()[-8:]:
            _info(line)
        _fail("SDR test failed — no audio. Common causes: another process owns "
              "the dongle (stop any manual 'rtl_fm | socat'), gain too low, or no "
              "antenna.")

    _info(f"RMS floor {floor:.0f}, peak {peak:.0f}  (S16 full scale 32768)")
    if peak >= 30000:
        _warn(f"Peak {peak:.0f} is near clipping — lower --gain (try {max(0, gain-12)}).")
    elif peak < 10:
        _fail(f"Audio level ~0 (peak {peak:.0f}). Check antenna and --gain.")

    _ok("SDR OK — live demodulated audio at the APRS frequency.")
    return True


# ── Step 3: Install + enable the feed service ────────────────────────────────────
def find_graywolf_unit():
    """Return the graywolf systemd unit name, or None."""
    r = subprocess.run(
        ["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain"],
        capture_output=True, text=True,
    )
    for line in r.stdout.splitlines():
        parts = line.split()
        if parts and "graywolf" in parts[0].lower():
            return parts[0]
    return None


def build_unit(rtl_fm, socat, freq, gain, ppm, port, gw_unit):
    feed = (f"{rtl_fm} -f {freq} -M fm -s {SAMPLE_RATE} -g {gain} -p {ppm} - "
            f"| {socat} -u -b {DATAGRAM} - UDP-SENDTO:127.0.0.1:{port}")
    ordering = ""
    if gw_unit:
        ordering = f"After={gw_unit}\nWants={gw_unit}\n"
    return (
        "[Unit]\n"
        "Description=RTL-SDR APRS audio feed -> GrayWolf (sdr_udp)\n"
        f"{ordering}"
        "\n"
        "[Service]\n"
        f"ExecStart=/bin/sh -c '{feed}'\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def install_service(unit_text, enable):
    _step(3, "Installing the feed service")
    _info(f"Writing {SERVICE_PATH}")
    r = subprocess.run(["sudo", "tee", SERVICE_PATH], input=unit_text,
                       text=True, capture_output=True)
    if r.returncode != 0:
        _fail(f"Could not write {SERVICE_PATH}: {r.stderr.strip()}")
    _ok(f"{SERVICE_NAME} written")

    _run(["sudo", "systemctl", "daemon-reload"], check=False)

    if not enable:
        _info(f"--no-enable: not starting the service. To start it later:\n"
              f"       sudo systemctl enable --now {SERVICE_NAME}")
        return

    _info("Stopping any running instance, then enabling + starting ...")
    _run(["sudo", "systemctl", "stop", SERVICE_NAME], check=False)
    _run(["sudo", "systemctl", "enable", "--now", SERVICE_NAME], check=False)

    time.sleep(2)
    active = subprocess.run(["systemctl", "is-active", SERVICE_NAME],
                            capture_output=True, text=True).stdout.strip()
    if active == "active":
        _ok(f"{SERVICE_NAME} is active")
    else:
        _warn(f"{SERVICE_NAME} is '{active}'. Recent log:")
        log = subprocess.run(
            ["journalctl", "-u", SERVICE_NAME, "-n", "12", "--no-pager", "--no-hostname"],
            capture_output=True, text=True, errors="replace",
        )
        for line in (log.stdout or log.stderr).strip().splitlines():
            _info(line)
        _warn("If you see 'usb_claim_interface error', another process owns the "
              "dongle — stop it and `sudo systemctl restart " + SERVICE_NAME + "`.")


# ── Step 4: Inspect GrayWolf's journal ───────────────────────────────────────────
def journal_lines(unit, n=120):
    """Return recent journal lines for *unit*, trying sudo if needed."""
    for cmd in (
        ["journalctl", "-u", unit, "-n", str(n), "--no-pager", "--no-hostname"],
        ["sudo", "journalctl", "-u", unit, "-n", str(n), "--no-pager", "--no-hostname"],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    return ""


def check_graywolf(gw_unit):
    _step(4, "Checking GrayWolf's modem (journal)")
    if not gw_unit:
        _warn("No graywolf systemd unit found — is GrayWolf installed and running?")
        _info("Skipping journal check. Configure GrayWolf manually (step 5).")
        return
    _info(f"Reading journal for {gw_unit} ...")
    text = journal_lines(gw_unit)
    if not text:
        _warn(f"Could not read the journal for {gw_unit} (permissions?). "
              f"Run manually:\n       journalctl -u {gw_unit} -f --no-hostname")
        return

    low = text.lower()
    if "pollerr" in low or "cpal input stream error" in low:
        _warn("GrayWolf is logging cpal/POLLERR — its channel is bound to a "
              "SOUNDCARD, not your sdr_udp device.")
        _info("Fix: repoint the channel's RX input to the sdr_udp device and "
              "delete the soundcard device. Never use 'Detect Devices'.")
    elif "state=running" in low or "modem ready" in low:
        _ok("GrayWolf modem reports RUNNING — if a channel uses the sdr_udp "
            "device, the level meter should move.")
    elif "no channels configured" in low:
        _info("GrayWolf has no channel configured yet — expected at this point. "
              "Create one in step 5.")
    else:
        _info("No decisive modem state in the recent journal. Watch it live:")
        _info(f"       journalctl -u {gw_unit} -f --no-hostname")


# ── Step 5: GrayWolf web-UI instructions ─────────────────────────────────────────
def print_instructions(port, gw_unit):
    _step(5, "Finish in the GrayWolf web UI")
    unit = gw_unit or "graywolf"
    steps = f"""
  The audio feed is running. Now wire it into GrayWolf (browser config — there
  is no config-file import). Full writeup: docs/graywolf-rtl-sdr.md

  1. Audio Devices  →  + Add Device   (DO NOT click 'Detect Devices' — it
     auto-creates a soundcard device that hijacks the channel → POLLERR loop)
        source_type   sdr_udp
        path          127.0.0.1:{port}
        sample_rate   {SAMPLE_RATE}
        channels      Mono
        format        s16le
        direction     input
     Enable it. Delete any soundcard device that appears.

  2. Channels  →  add an AFSK / RX channel
        bit rate      1200 bps
        mark / space  1200 / 2200 Hz
        RX input      the sdr_udp device (path 127.0.0.1:{port}) — NOT a soundcard

  3. RESTART GrayWolf so it loads the new device/channel:
        sudo systemctl restart {unit}
     GrayWolf reads channel config when the modem STARTS — a device/channel you
     add at runtime is not live until a restart. It can even keep showing
     'state=RUNNING' on the old config while the new channel does nothing.

  4. Confirm it works:
        journalctl -u {unit} -f --no-hostname
     Good: 'modem ready' + 'modembridge state=RUNNING', NO cpal/POLLERR.
     The sdr_udp level meter moves; a packet appears in the stream within minutes.

  5. Reboot once and re-check — GrayWolf channel config has been seen to not
     persist ('no channels configured, skipping audio setup'). If it evaporates,
     recreate it and chase persistence (see docs/graywolf-rtl-sdr.md, caveat 6).
"""
    print(steps)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Test an RTL-SDR and enable it as a GrayWolf APRS RX feed.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 features/rtl-sdr/enable-rtl-sdr.py\n"
            "  python3 features/rtl-sdr/enable-rtl-sdr.py --freq 144.800M --gain 28 --ppm 12\n"
            "  python3 features/rtl-sdr/enable-rtl-sdr.py --check       # test only\n"
        ),
    )
    parser.add_argument("--freq", default=_station_freq(),
                        help="APRS frequency (keep the 'M' suffix — a bare number "
                             "is parsed as Hz). Default: configuration/station.json's "
                             "aprs_freq, else 144.390M (NA 2 m).")
    parser.add_argument("--gain", type=float, default=32.8,
                        help="Tuner gain in dB (default 32.8 — the R820T step "
                             "that bench-tested best for APRS; omit-style AGC not "
                             "supported here). Lower if clipping.")
    parser.add_argument("--ppm", type=int, default=0,
                        help="Frequency-error correction in ppm (default 0).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"UDP port GrayWolf's sdr_udp listens on (default {DEFAULT_PORT}).")
    parser.add_argument("--seconds", type=int, default=5,
                        help="SDR test capture duration (default 5).")
    parser.add_argument("--check", action="store_true",
                        help="Test the SDR only — no service changes, no instructions.")
    parser.add_argument("--no-enable", action="store_true",
                        help="Write the service unit but don't enable/start it.")
    args = parser.parse_args()

    print("\n  OASIS — enable-rtl-sdr")
    _hr()
    _info("Tests the RTL-SDR and wires it into GrayWolf as a receive-only APRS feed.")
    _info("Receive-only — an RTL-SDR cannot transmit. For TX use the DRA-Pi-Zero.")
    print()

    rtl_fm, socat = check_prereqs()
    verified = test_sdr(rtl_fm, args.freq, args.gain, args.ppm, args.seconds)

    if args.check:
        _hr()
        if verified:
            print("\n  SDR test passed. (--check: no service changes made.)\n")
        else:
            print("\n  No RTL-SDR present. (--check: no service changes made.)\n")
        return

    gw_unit = find_graywolf_unit()
    unit_text = build_unit(rtl_fm, socat, args.freq, args.gain, args.ppm,
                           args.port, gw_unit)
    install_service(unit_text, enable=not args.no_enable)
    check_graywolf(gw_unit)
    print_instructions(args.port, gw_unit)

    _hr()
    if verified:
        print("\n  RTL-SDR feed enabled. Finish GrayWolf setup with the steps "
              "above.\n")
    else:
        print("\n  RTL-SDR feed service installed and enabled — deferred: no "
              "dongle detected yet, so it will start automatically when you plug "
              "one in. Finish GrayWolf setup with the steps above.\n")


if __name__ == "__main__":
    main()


# ── Device binding (hardware-aware engine, Slice 2b) ───────────────────────────
# The APRS RX feed (rtl_fm | socat -> GrayWolf) must run on the SAME dongle
# assigned to the `aprs` logical service (only relevant when aprs is in SDR
# mode — see common/hardware.py's dual-mode resolver). This mirrors the ADS-B
# apply(device) pattern (services/adsb/common/adsb.py).
def aprs_feed_device_args(device):
    """Pure: the rtl_fm device selector for the APRS feed's assigned dongle.
    Only rtl-sdr devices with a serial bind; anything else -> [] (feed unused
    when aprs is on a radio port, or unassigned)."""
    if not device or device.get("kind") != "rtl-sdr":
        return []
    serial = device.get("serial")
    return ["-d", serial] if serial else []


def apply(repo_root, device):
    """Bind (or unbind, when device is None or not an rtl-sdr) the APRS feed's
    rtl_fm invocation to the assigned dongle's serial, by rewriting any `-d
    <value>` token in the existing aprs-sdr-feed.service ExecStart line.

    Linux/root only (no-op elsewhere). BENCH-VERIFY on the target Pi: the exact
    aprs-sdr-feed.service ExecStart shape this regex targets, and that this
    rtl_fm build selects a dongle by SERIAL via `-d <serial>` (some builds want
    `-d <index>` instead). This writer is the one element that can't be
    validated off-Pi — the pure arg builder above is what the tests cover.
    """
    if sys.platform != "linux":
        return
    args = aprs_feed_device_args(device)
    device_token = " ".join(args)  # "-d 1090" or ""
    try:
        with open(SERVICE_PATH) as f:
            existing = f.read()
    except OSError:
        return  # unit not installed yet — nothing to rewrite
    import re
    def _rewrite(m):
        cmd = m.group(1)
        cmd = re.sub(r'-d\s+\S+', '', cmd).strip()
        if device_token:
            # insert the device flag right after "rtl_fm"
            cmd = re.sub(r'(rtl_fm)\b', r'\1 ' + device_token, cmd, count=1)
        return f"ExecStart={cmd}"
    new_text = re.sub(r'ExecStart=(.*)', _rewrite, existing)
    proc = subprocess.Popen(["sudo", "tee", SERVICE_PATH], stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL)
    proc.communicate(new_text.encode())
    # No daemon-reload here — the apply-hardware orchestrator does exactly one
    # reload after calling every service's hook.
