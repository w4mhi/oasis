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
the port→service→GPIO map (left=APRS=GPIO12, right=Winlink=GPIO23).

Usage:
  python3 features/draws-audio/install-draws-audio.py               # autodetect phase
  python3 features/draws-audio/install-draws-audio.py --check       # status only
  python3 features/draws-audio/install-draws-audio.py --dry-run     # preview config.txt change
  python3 features/draws-audio/install-draws-audio.py --config-only # force the overlay phase
  python3 features/draws-audio/install-draws-audio.py --mixer-only  # force the mixer phase

Exit codes: 0 = done · 10 = done, reboot required · 1 = error.
Requires: Linux (Raspberry Pi), sudo."""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from common import draws
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
    return p


def rx_level_hint():
    """RX input level is radio-dependent, so the installer ships NW Digital
    Radio's known-good baseline and leaves the trim to the operator — the same
    convention TX deviation already follows. Print how to do it, because the
    baseline clips on a radio with a hot audio output and the failure is not
    obvious: it still decodes, just with degraded margin."""
    _info("RX level is per-radio — the baseline above may need trimming:")
    _info("  • watch `audio level` on received packets in direwolf; aim near 50")
    _info("  • falling level as you raise gain means you are clipping — back off")
    _info("  • trim:  amixer -c %s sset -- \"ADC Level\" -12.0dB,-12.0dB"
          % draws_audio.CARD)
    _info("  • keep it: sudo alsactl store")
    _info("  (the -- is required; amixer reads a leading - as a switch)")


def ptt_reminder():
    _info("PTT is Direwolf/GrayWolf-side (a GPIO the TNC keys), not set here:")
    for port in draws_audio.PORTS:
        _info("  • %-5s connector → %-7s → PTT GPIO %d"
              % (port["port"], port["service"], port["gpio"]))


def apply_mixer():
    _step(2, "Apply the DRAWS ALSA mixer routing")
    if not draws.sound_card_present(draws_audio.CARD_MATCH):
        _warn("Sound card '%s' not detected." % draws_audio.CARD)
        _info("Run the overlay phase first and reboot, then re-run to apply the mixer:")
        _info("  python3 features/draws-audio/install-draws-audio.py --config-only")
        return 1

    failures = 0
    for cmd in draws_audio.build_mixer_commands():
        ctrl, val = cmd[-2], cmd[-1]
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

    if sys.platform != "linux":
        _fail("This installer requires Linux (Raspberry Pi).")
        return 1
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
