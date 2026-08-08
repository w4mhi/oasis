#!/usr/bin/env python3
"""install-draws-audio.py — enable the DRAWS overlay and apply the radio audio
mixer routing for the on-board TLV320AIC3204 codec (ALSA card `draws`). Mirrors
features/dra-audio-interface/enable-dra-pi.py; reuses common/draws.

Two-phase, exit-10 reboot convention: the first run (on a box without the overlay)
writes `dtoverlay=draws` and exits 10 — the sound card only appears after a reboot.
Once the card is up, it applies the known-good RX/TX mixer routing and persists it
with `alsactl store`, exiting 0. On a box that already ran draws-gps the overlay is
present and the card is live, so a single run goes straight to the mixer.

PTT is a GPIO the TNC (Direwolf/GrayWolf) keys, not set here — the installer prints
the port→service→GPIO map (left=Winlink=GPIO12, right=APRS=GPIO23).

Usage:
  python3 features/draws-audio/install-draws-audio.py               # autodetect phase
  python3 features/draws-audio/install-draws-audio.py --check       # status only
  python3 features/draws-audio/install-draws-audio.py --dry-run     # preview config.txt change
  python3 features/draws-audio/install-draws-audio.py --config-only # force the overlay phase
  python3 features/draws-audio/install-draws-audio.py --mixer-only  # force the mixer phase
  python3 features/draws-audio/install-draws-audio.py --rx-level=-9.0dB
                                                    # trim + persist RX level
                                                    # NOTE the '=' — argparse
                                                    # reads a bare -9.0dB as a
                                                    # switch. "--rx-level -9.0"
                                                    # (no unit) also works.
  python3 features/draws-audio/install-draws-audio.py --livetest W4MHI-6
                                                    # TRANSMIT one test packet
  python3 features/draws-audio/install-draws-audio.py --livetest W4MHI-6 --channel 1
                                                    # ... out the right/APRS port

--livetest proves the whole TX chain (PTT + codec + mixer + cable + radio) by
putting one APRS status packet reading "OASIS DRAWS TEST" on the air through a
running direwolf. It KEYS THE TRANSMITTER. Frames are built in-process, so it
needs no kissutil — only a reachable KISS port.

Exit codes: 0 = done · 10 = done, reboot required · 1 = error.
Requires: Linux (Raspberry Pi), sudo."""
import argparse
import os
import shutil
import socket
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from common import draws
from common import overlays
from common.oasis_lib import _section, _step, _ok, _info, _warn, _fail

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "draws_audio", os.path.join(os.path.dirname(os.path.abspath(__file__)), "draws_audio.py"))
draws_audio = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(draws_audio)


def build_parser():
    p = argparse.ArgumentParser(description="Enable DRAWS radio audio + mixer routing.")
    p.add_argument("--check", action="store_true", help="report status only")
    p.add_argument("--dry-run", action="store_true",
                   help="preview the config.txt change without writing")
    p.add_argument("--config-only", action="store_true",
                   help="only do the overlay phase (force)")
    p.add_argument("--mixer-only", action="store_true",
                   help="only do the ALSA mixer phase (force)")
    p.add_argument("--rx-level", metavar="dB",
                   help="set + persist the RX input level, e.g. "
                        "--rx-level=-9.0dB (the '=' is required for a negative "
                        "value). Radio-specific; aim for a direwolf audio "
                        "level near 50")
    p.add_argument("--tnc", action="store_true",
                   help="(re)install the shared 2-channel direwolf TNC service")
    p.add_argument("--callsign", help="callsign for the TNC config "
                                      "(default: the station callsign)")
    p.add_argument("--livetest", metavar="CALLSIGN",
                   help="TRANSMIT one APRS test packet as CALLSIGN (e.g. W4MHI-6) "
                        "through a running direwolf, then exit")
    p.add_argument("--channel", type=int, default=0, choices=(0, 1),
                   help="radio port for --livetest: 0 = left/Winlink (default), "
                        "1 = right/APRS")
    p.add_argument("--kiss-host", default=draws_audio.KISS_HOST,
                   help="direwolf KISS host (default: 127.0.0.1)")
    p.add_argument("--kiss-port", type=int, default=draws_audio.KISS_PORT,
                   help="direwolf KISS port (default: 8001)")
    return p


def run_livetest(args):
    """Put one real packet on the air to prove the whole TX chain — PTT, codec,
    mixer, cable, radio — in a single command. This KEYS THE TRANSMITTER."""
    _step(1, "Transmit an APRS test packet")
    try:
        call, ssid = draws_audio.parse_callsign(args.livetest)
        frame = draws_audio.build_livetest_frame(args.livetest, args.channel)
    except ValueError as exc:
        _fail(str(exc))
        return 1
    display = call if ssid == 0 else "%s-%d" % (call, ssid)

    try:
        port = draws_audio.port_for_channel(args.channel)
    except ValueError:
        port = None
    _info("callsign : %s" % display)
    _info("comment  : %s" % draws_audio.LIVETEST_COMMENT)
    if port:
        _info("port     : channel %d → %s connector → %s → PTT GPIO %d"
              % (args.channel, port["port"], port["service"], port["gpio"]))
    _warn("This keys the transmitter — make sure the radio is on a frequency you "
          "are licensed to transmit on.")

    try:
        with socket.create_connection((args.kiss_host, args.kiss_port), timeout=5) as sock:
            sock.sendall(frame)
    except ConnectionRefusedError:
        # _fail() exits, so the remedy has to travel inside the message — a
        # follow-up _info() here would be unreachable.
        _fail("Nothing listening on %s:%d — direwolf is not running (or has no "
              "KISSPORT).\n  Start it first, e.g.:\n"
              "    direwolf -t 0 -c ~/.config/direwolf/oasis-draws.conf"
              % (args.kiss_host, args.kiss_port))
        return 1
    except OSError as exc:
        _fail("Could not reach the TNC on %s:%d — %s"
              % (args.kiss_host, args.kiss_port, exc))
        return 1

    _ok("Sent %d bytes to the TNC." % len(frame))
    print()
    _info("Confirm on another station: look for %s with the status text %r."
          % (display, draws_audio.LIVETEST_COMMENT))
    _info("No digipeater path is used, so it is direct RF only — a receiving "
          "station must be in simplex range.")
    return 0


def read_rx_level():
    """The card's current RX level for one channel (e.g. "-9.00dB"), or "" if it
    cannot be read (no card, non-Linux)."""
    r = subprocess.run(["amixer", "-c", draws_audio.CARD, "sget",
                        draws_audio.RX_LEVEL_CONTROL],
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if "Front Left:" in line or line.strip().startswith("Mono:"):
            start = line.find("[")
            while start != -1:
                end = line.find("]", start)
                token = line[start + 1:end]
                if token.lower().endswith("db"):
                    return token
                start = line.find("[", end)
    return ""


def set_rx_level(value):
    """Apply and persist an RX level. Returns the exit code for the CLI."""
    _step(1, "Set the DRAWS RX input level")
    try:
        normalised = draws_audio.parse_rx_level(value)
    except ValueError as exc:
        _fail(str(exc))
        return 1
    if not draws.sound_card_present(draws_audio.CARD_MATCH):
        _fail("Sound card '%s' not detected — run the mixer phase first."
              % draws_audio.CARD)
        return 1

    before = read_rx_level()
    r = subprocess.run(draws_audio.build_rx_level_command(normalised),
                       capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr.strip().splitlines() or ["control not found"])[-1]
        _fail("Could not set %s: %s" % (draws_audio.RX_LEVEL_CONTROL, tail))
        return 1
    _ok("%s: %s -> %s" % (draws_audio.RX_LEVEL_CONTROL, before or "?",
                          read_rx_level()))

    if subprocess.run(["sudo", "alsactl", "store"]).returncode == 0:
        _ok("Persisted (survives reboot, and reinstalls keep it).")
    else:
        _warn("alsactl store failed — the level will not survive a reboot.")
    print()
    _info("Verify: watch `audio level` on received packets in direwolf; aim "
          "near 50.")
    return 0


def rx_level_hint():
    """RX input level is radio-dependent, so the installer ships NW Digital
    Radio's known-good baseline and leaves the trim to the operator — the same
    convention TX deviation already follows. Print how to do it, because the
    baseline clips on a radio with a hot audio output and the failure is not
    obvious: it still decodes, just with degraded margin."""
    _info("RX level is per-radio — the baseline above may need trimming:")
    _info("  • watch `audio level` on received packets in direwolf; aim near 50")
    _info("  • falling level as you raise gain means you are clipping — back off")
    _info("  • trim:  python3 features/draws-audio/install-draws-audio.py "
          "--rx-level=-9.0dB")
    _info("    (the '=' is required — argparse reads a leading - as a switch)")
    _info("  • it is persisted, and a reinstall will KEEP it")


def ptt_reminder():
    _info("PTT is Direwolf/GrayWolf-side (a GPIO the TNC keys), not set here:")
    for port in draws_audio.PORTS:
        _info("  • %-5s connector → %-7s → PTT GPIO %d"
              % (port["port"], port["service"], port["gpio"]))


def _target_user_home():
    """The invoking (non-root) operator and their home — the TNC runs as them,
    not root, so direwolf's config lives in their ~/.config. Mirrors what the
    winlink installer does with SUDO_USER."""
    user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "pi"
    home = os.path.expanduser("~" + user)
    if not os.path.isdir(home):
        home = "/home/" + user
    return user, home


def station_callsign():
    """Operator callsign from the suite-root station.json, or None."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        from common import config_paths
        import json
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        with open(config_paths.station_json(repo_root)) as fh:
            call = json.load(fh).get("callsign")
        return (str(call or "").strip().upper()) or None
    except Exception:                              # noqa: BLE001 - optional
        return None


def install_tnc(callsign_override=None):
    """Install + enable the shared 2-channel direwolf TNC that owns the card."""
    _step(3, "Install the shared DRAWS TNC (direwolf, both ports)")

    if not shutil.which("direwolf"):
        _warn("direwolf is not installed — skipping the TNC service.")
        _info("Install it first:  sudo apt-get install -y direwolf")
        return False

    ensure_alsa_shared_pcm()

    left = draws.sysfs_gpio(draws_audio.PORTS[0]["gpio"])
    right = draws.sysfs_gpio(draws_audio.PORTS[1]["gpio"])
    if left is None or right is None:
        _warn("Could not resolve the sysfs PTT numbers (no 40-pin gpiochip "
              "bank found) — skipping the TNC service.")
        return False
    _ok("PTT lines: left BCM %d -> %d, right BCM %d -> %d"
        % (draws_audio.PORTS[0]["gpio"], left, draws_audio.PORTS[1]["gpio"], right))

    callsign = callsign_override or station_callsign()
    if not callsign:
        _warn("No station callsign set — writing the TNC config with N0CALL.")
        _info("Set it in Setup (station step), then re-run with --tnc.")

    user, home = _target_user_home()
    conf_dir = os.path.join(home, ".config", "direwolf")
    conf_path = os.path.join(conf_dir, draws_audio.TNC_CONF_NAME)
    try:
        os.makedirs(conf_dir, exist_ok=True)
        with open(conf_path, "w") as fh:
            fh.write(draws_audio.build_tnc_conf(callsign, left, right))
    except (OSError, ValueError) as exc:
        _warn("Could not write %s (%s) — skipping the TNC service." % (conf_path, exc))
        return False
    _ok("Wrote %s" % conf_path)

    try:
        draws._write_text(draws_audio.TNC_UNIT_PATH,
                          draws_audio.build_tnc_service(user, home, left, right))
        subprocess.run(["sudo", "chmod", "0644", draws_audio.TNC_UNIT_PATH],
                       capture_output=True)
    except Exception as exc:                       # noqa: BLE001 - advisory only
        _warn("Could not write %s (%s)." % (draws_audio.TNC_UNIT_PATH, exc))
        return False
    _ok("Wrote %s" % draws_audio.TNC_UNIT_PATH)

    subprocess.run(["sudo", "systemctl", "daemon-reload"], capture_output=True)
    r = subprocess.run(["sudo", "systemctl", "enable", "--now",
                        draws_audio.TNC_UNIT_NAME], capture_output=True, text=True)
    if r.returncode != 0:
        _warn("Could not enable %s: %s"
              % (draws_audio.TNC_UNIT_NAME, (r.stderr or "").strip().splitlines()[-1:]))
        return False
    _ok("%s enabled and started (AGW :%d, KISS :%d)."
        % (draws_audio.TNC_UNIT_NAME, draws_audio.TNC_AGW_PORT, draws_audio.TNC_KISS_PORT))
    _info("Channel 0 = left/Winlink · channel 1 = right/APRS "
          "(pat uses agwpe.radio_port 0 — the only port it can use).")
    return True


def ensure_alsa_shared_pcm():
    """Install the ALSA drop-in defining the shared capture pcm. Idempotent;
    returns True when the file changed. Must exist BEFORE the TNC starts —
    direwolf's ADEVICE names it."""
    want = draws_audio.build_alsa_shared_conf()
    path = draws_audio.ALSA_CONF_PATH
    try:
        with open(path) as fh:
            if fh.read() == want:
                _ok("Shared capture pcm already defined (%s)." % path)
                return False
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        pass
    try:
        draws._write_text(path, want)
        subprocess.run(["sudo", "chmod", "0644", path], capture_output=True)
    except Exception as exc:                       # noqa: BLE001 - advisory only
        _warn("Could not write %s (%s)." % (path, exc))
        _warn("The TNC will fail to open '%s'."
              % draws_audio.SHARED_CAPTURE_PCM)
        return False
    _ok("Shared capture pcm '%s' defined (%s)."
        % (draws_audio.SHARED_CAPTURE_PCM, path))
    _info("Other processes (e.g. a satellite recorder) can now capture the "
          "radios while the TNC runs.")
    return True


def ensure_acp_ignore():
    """Install the udev rule that keeps PipeWire/PulseAudio off the radio codec.

    Idempotent; returns True when the file changed. Must run BEFORE the mixer is
    stored, because a sound server holding the card will overwrite PCM again on
    the next boot no matter what alsactl saved."""
    want = draws_audio.build_acp_ignore_rule(draws_audio.CARD)
    path = draws_audio.ACP_IGNORE_RULE
    try:
        with open(path) as fh:
            if fh.read() == want:
                _ok("Desktop audio stack already excluded (%s)." % path)
                return False
    except OSError:
        pass

    try:
        draws._write_text(path, want)
        # _write_text stages through mkstemp (0600) and `sudo cp`, which carries
        # that mode onto a NEW file. A root-only udev rule still works, but it is
        # wrong for /etc/udev/rules.d and makes the idempotency check above
        # unreadable as a normal user — so every run would rewrite and
        # re-trigger udev. Force the conventional mode.
        subprocess.run(["sudo", "chmod", "0644", path], capture_output=True)
    except Exception as exc:                       # noqa: BLE001 - advisory only
        _warn("Could not write %s (%s)." % (path, exc))
        _warn("PipeWire may reclaim the card and reset PCM on the next boot.")
        return False

    _ok("Excluded the card from PipeWire/PulseAudio (%s)." % path)
    for cmd in (["sudo", "udevadm", "control", "--reload-rules"],
                ["sudo", "udevadm", "trigger", "--action=add",
                 "/sys/class/sound/card%s" % _card_index()]):
        subprocess.run(cmd, capture_output=True)
    _info("A reboot makes the exclusion fully effective.")
    return True


def _card_index():
    """Index of the DRAWS card in /proc/asound/cards, for the udev re-trigger.
    Falls back to a wildcard-ish empty match rather than guessing wrong."""
    try:
        with open("/proc/asound/cards") as fh:
            for line in fh:
                if draws_audio.CARD_MATCH in line.lower():
                    return line.strip().split()[0]
    except OSError:
        pass
    return ""


def apply_mixer():
    _step(2, "Apply the DRAWS ALSA mixer routing")
    if not draws.sound_card_present(draws_audio.CARD_MATCH):
        _warn("Sound card '%s' not detected." % draws_audio.CARD)
        _info("Run the overlay phase first and reboot, then re-run to apply the mixer:")
        _info("  python3 features/draws-audio/install-draws-audio.py --config-only")
        return 1

    ensure_acp_ignore()

    # Never clobber an RX level the operator has already tuned: it is
    # radio-specific and the shipped baseline measured too hot on real radios,
    # so re-running the installer (or a Setup reinstall) would silently undo a
    # correct receiver. A fresh board reads the baseline, so nothing is skipped
    # on a first install.
    keep_rx = draws_audio.rx_level_is_customised(read_rx_level())

    failures = 0
    for cmd in draws_audio.build_mixer_commands():
        ctrl, val = cmd[-2], cmd[-1]
        if ctrl == draws_audio.RX_LEVEL_CONTROL and keep_rx:
            _info("%s = %s (kept — tuned for this radio; --rx-level to change)"
                  % (ctrl, read_rx_level()))
            continue
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            _ok("%s = %s" % (ctrl, val))
        else:
            failures += 1
            tail = (r.stderr.strip().splitlines() or ["control not found"])[-1]
            _warn("%s: not set (%s)" % (ctrl, tail))

    if subprocess.run(["sudo", "alsactl", "store"]).returncode == 0:
        _ok("Mixer state persisted (alsactl store).")
    else:
        _warn("alsactl store failed — settings may not survive a reboot.")

    if failures:
        _warn("%d mixer control(s) could not be set — check with "
              "`amixer -c %s scontrols`." % (failures, draws_audio.CARD))
    else:
        _ok("DRAWS audio mixer applied.")
    install_tnc()
    print()
    rx_level_hint()
    print()
    ptt_reminder()
    # A control that did not apply means the radio audio path is misconfigured —
    # exactly how the 2026-08-06 negative-dB bug hid behind a green exit code
    # while TX sat 25dB hot. Report it as a failure, not a warning.
    return 1 if failures else 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    _section("DRAWS radio audio")

    # Before the platform/overlay guards: --livetest only needs to reach a TNC,
    # so it also works from a laptop pointed at the go-box with --kiss-host.
    if args.livetest:
        return run_livetest(args)

    if args.rx_level:
        if sys.platform != "linux":
            _fail("This installer requires Linux (Raspberry Pi).")
            return 1
        return set_rx_level(args.rx_level)

    if args.tnc:
        if sys.platform != "linux":
            _fail("This installer requires Linux (Raspberry Pi).")
            return 1
        return 0 if install_tnc(args.callsign) else 1

    if sys.platform != "linux":
        _fail("This installer requires Linux (Raspberry Pi).")
        return 1
    # Put OASIS's vendored draws.dtbo in place first. Pi OS Trixie ships one that
    # does not bring the HAT up on kernel 6.18.34 (neither the codec at 0x18 nor
    # the SC16IS752 binds), and a firmware update can wipe a hand-copied file, so
    # this runs on EVERY install rather than only when the overlay is missing.
    for _ov in ("draws", "udrc"):
        _changed, _why = overlays.install(_ov)
        if _changed:
            _ok("installed %s.dtbo (%s)" % (_ov, _why))
        elif _why not in ("already-current", "no-vendored-copy"):
            _warn("could not install %s.dtbo: %s" % (_ov, _why))
    if not draws.overlay_available():
        _fail("draws.dtbo not found in /boot/firmware/overlays — update Raspberry "
              "Pi OS; this image is too old to drive the DRAWS HAT.")
        return 1
    if args.config_only and args.mixer_only:
        _fail("--config-only and --mixer-only are mutually exclusive.")
        return 1

    if args.check:
        _info("overlay dtbo: present")
        _cfg = draws.config_path()
        _info("config.txt has dtoverlay=draws: %s"
              % (bool(_cfg) and draws.OVERLAY_LINE in open(_cfg).read()))
        _info("sound card '%s' present: %s"
              % (draws_audio.CARD, draws.sound_card_present(draws_audio.CARD_MATCH)))
        return 0

    if args.dry_run:
        cfg = draws.config_path()
        _, changed = draws.add_overlay_line(open(cfg).read()) if cfg else ("", False)
        _info("would add dtoverlay=draws: %s" % changed)
        return 0

    if args.mixer_only:
        return apply_mixer()

    _step(1, "Enable the DRAWS overlay")
    _cfg = draws.config_path()
    if _cfg and draws.conflicting_overlay(open(_cfg).read()):
        _fail("This box already loads the DRA-Pi HAT (%s). The DRA-Pi and "
              "DRAWS are different boards for the same 40-pin header and the "
              "same I2S bus — installing both leaves the Pi with no working "
              "sound card.\n  Remove the dra-pi feature first (Setup, or "
              "scripts/remove-oasis.py), reboot, then re-run this."
              % draws.DRA_PI_OVERLAY)
        return 1
    overlay_changed = draws.ensure_overlay()
    _ok("dtoverlay=draws %s" % ("added" if overlay_changed else "already present"))

    if args.config_only:
        _warn("Reboot required: the sound card appears only after the overlay loads.")
        return 10

    card_present = draws.sound_card_present(draws_audio.CARD_MATCH)
    code = draws_audio.decide_exit_code(overlay_changed, card_present)
    if code == 10:
        _warn("Reboot required: the sound card appears only after the overlay loads.")
        _info("After rebooting, re-run this script to apply the ALSA mixer routing.")
        print()
        ptt_reminder()
        return code
    return apply_mixer()


if __name__ == "__main__":
    sys.exit(main())
