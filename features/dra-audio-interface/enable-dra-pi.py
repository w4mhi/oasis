#!/usr/bin/env python3
"""
enable-dra-pi.py
----------------
Configure the MastersCommunications DRA-Pi-Zero (Wolfson WM8731 I²S codec,
AudioInjector-Zero compatible) so GrayWolf can use it. Based on the verified
known-good config in docs/graywolf-dra-pi.md.

Two phases, auto-detected by whether the sound card is present yet:

  Phase 1 — card NOT present (first run):
    Edit /boot/firmware/config.txt to load the WM8731 overlay and free the I²S
    bus. Backs the file up once, comments the conflicting Pi defaults, and writes
    an OASIS-managed block. Then exits 10 (reboot required) — the codec only
    appears after a reboot.

  Phase 2 — card present (after reboot):
    Apply the ALSA mixer routing (the critical `Input Mux = Mic`, capture gain,
    TX path) and persist it with `alsactl store`. Exits 0.

PTT (GPIO 12) and the green RX LED (GPIO 16) are configured in GrayWolf itself,
not here — see docs/graywolf-dra-pi.md.

Usage:
  python3 features/dra-audio-interface/enable-dra-pi.py            # auto-detect the phase
  python3 features/dra-audio-interface/enable-dra-pi.py --dry-run   # preview the config.txt changes
  python3 features/dra-audio-interface/enable-dra-pi.py --config-only
  python3 features/dra-audio-interface/enable-dra-pi.py --mixer-only

Exit codes: 0 = done · 10 = done, reboot required · 1 = error.
Requires: Linux (Raspberry Pi), sudo.
"""

import argparse
import difflib
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail

CARD       = "audioinjectorpi"      # ALSA card name once the overlay loads
CARD_MATCH = "audioinjector"        # substring used to detect it
CONFIG_CANDIDATES = ("/boot/firmware/config.txt", "/boot/config.txt")
REBOOT_EXIT = 10

BLOCK_BEGIN = "# --- OASIS DRA-Pi-Zero (managed by scripts/enable-dra-pi.py) ---"
BLOCK_END   = "# --- end OASIS DRA-Pi-Zero ---"


def removal_record(repo_root=None):
    """Teardown record for the dra-pi-rx-led chain feature: strip the DRA-Pi
    config.txt block and remove the dra-rx-led service (installed by the chain's
    enable-dra-rx-led.py). Reboot to drop the sound-card overlay."""
    return {"config_blocks": [[BLOCK_BEGIN, BLOCK_END]],
            "config_subs": _config_subs(),
            "services": ["dra-rx-led"],
            "requires_reboot": True}


# The two config.txt lines this feature mutates that are NOT its own: the stock
# on-board audio param and the KMS overlay. Stripping the managed block cannot
# undo either (they live outside the markers), and leaving them behind kills
# EVERY sound card on the box — including a DRAWS HAT installed afterwards,
# which then looks broken for no visible reason (bench 2026-08-06). Derived from
# the same constants transform_config() uses so the pair can never drift.
AUDIO_ON_LINE       = "dtparam=audio=on"
AUDIO_OFF_COMMENT   = f"# {AUDIO_ON_LINE}   # disabled by OASIS DRA-Pi"
VC4_BASE            = "dtoverlay=vc4-kms-v3d"
VC4_NOAUDIO         = f"{VC4_BASE},noaudio"


def _config_subs():
    """(from, to) pairs restoring the stock lines transform_config() rewrote."""
    return [[AUDIO_OFF_COMMENT, AUDIO_ON_LINE],
            [VC4_NOAUDIO, VC4_BASE]]
BLOCK_LINES = [
    "dtparam=i2c_arm=on",                    # WM8731 control interface (I2C)
    "dtoverlay=i2s-mmap",                    # mmap DMA on I2S (harmless if unused)
    "dtoverlay=audioinjector-wm8731-audio",  # THE card: loads + clocks the codec
]

# ALSA mixer routing — the known-good DRA-Pi RX/TX setup. (control, value)
MIXER = [
    ("Input Mux",             "Mic"),    # RX audio arrives on the MIC input — critical
    ("Mic",                   "cap"),
    ("Line",                  "nocap"),
    ("Mic Boost",             "0"),
    ("Capture",               "100%"),
    ("ADC High Pass Filter",  "on"),
    ("Output Mixer HiFi",     "on"),      # TX path
    ("Master",                "98%"),
    ("Sidetone",              "100%"),
]


# ── Detection / IO ──────────────────────────────────────────────────────────────
def card_present():
    """True if the WM8731 (audioinjector) card is currently registered."""
    try:
        with open("/proc/asound/cards") as f:
            if CARD_MATCH in f.read().lower():
                return True
    except OSError:
        pass
    r = subprocess.run(["aplay", "-l"], capture_output=True, text=True)
    return CARD_MATCH in (r.stdout + r.stderr).lower()


def find_config_txt():
    for p in CONFIG_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except (PermissionError, OSError):
        r = subprocess.run(["sudo", "cat", path], capture_output=True, text=True)
        if r.returncode != 0:
            _fail(f"Cannot read {path}: {r.stderr.strip()}")
        return r.stdout


def write_text(path, content):
    bak = path + ".oasis-bak"
    if not os.path.exists(bak):
        if subprocess.run(["sudo", "cp", path, bak]).returncode == 0:
            _ok(f"Backed up {os.path.basename(path)} → {os.path.basename(bak)}")
        else:
            _warn("Could not create a backup — aborting to be safe.")
            return False
    proc = subprocess.Popen(["sudo", "tee", path],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    proc.communicate(content.encode())
    return proc.returncode == 0


# ── config.txt transform ────────────────────────────────────────────────────────
def transform_config(text):
    """Return (new_text, changes). Neutralises conflicting Pi defaults and upserts
    the OASIS-managed overlay block. Idempotent."""
    changes = []
    # Stock lines are left ALONE. Earlier versions commented out
    # `dtparam=audio=on` and appended `,noaudio` to the KMS overlay, on the
    # premise that they had to be silenced to "free the I2S bus". That premise
    # is wrong: on-board audio is PWM/headphone and HDMI audio goes out HDMI —
    # neither contends for I2S. Bench 2026-08-06: a DRAWS I2S codec, the
    # on-board card and both HDMI cards coexist with `dtparam=audio=on`. The
    # edits also could not be undone by a block strip, so uninstalling the
    # feature left the Pi with no sound cards at all.
    body = text

    # 3) Upsert the managed block (replace any existing one).
    if BLOCK_BEGIN in body:
        pre, _, rest = body.partition(BLOCK_BEGIN)
        _, _, post = rest.partition(BLOCK_END)
        body = pre.rstrip("\n") + "\n" + post.lstrip("\n")
        changes.append("refreshed the OASIS managed block")
    else:
        changes.append("added the OASIS managed block")

    block = "\n".join([BLOCK_BEGIN, *BLOCK_LINES, BLOCK_END])
    new_text = body.rstrip("\n") + "\n\n" + block + "\n"
    return new_text, changes


# ── Phases ──────────────────────────────────────────────────────────────────────
def ptt_led_reminder():
    _info("PTT and the green RX LED are GrayWolf-side, not config.txt:")
    _info("  • GrayWolf PTT → method=gpio, /dev/gpiochip0, line 12")
    _info("  • Green RX LED (GPIO 16) → see docs/graywolf-dra-pi.md")


def do_config_phase(dry_run):
    _step(1, "Configuring config.txt for the DRA-Pi-Zero")
    path = find_config_txt()
    if not path:
        _fail("config.txt not found (looked in /boot/firmware and /boot). "
              "Is this a Raspberry Pi?")
    _info(f"Boot config: {path}")

    text = read_text(path)
    new_text, changes = transform_config(text)

    if new_text == text:
        _ok("config.txt already has the DRA-Pi overlays — no change needed.")
    elif dry_run:
        _info("Planned changes (dry run — nothing written):")
        for c in changes:
            _info(f"  - {c}")
        diff = difflib.unified_diff(text.splitlines(), new_text.splitlines(),
                                    fromfile=path, tofile=path + " (new)", lineterm="")
        print("\n".join(diff))
        _info("Re-run without --dry-run to apply.")
        return 0
    else:
        if not write_text(path, new_text):
            _fail("Could not write config.txt.")
        _ok(f"Updated {path}")
        for c in changes:
            _info(f"  - {c}")

    print()
    ptt_led_reminder()
    print()
    _warn("Reboot required — the WM8731 codec only appears after a reboot.")
    _info("After rebooting, re-run this script to apply the ALSA mixer routing.")
    return REBOOT_EXIT


def do_mixer_phase():
    _step(2, "Applying the DRA-Pi ALSA mixer routing")
    if not card_present():
        _warn(f"Sound card '{CARD}' not detected.")
        _info("Run the config phase first and reboot, then re-run to apply the mixer:")
        _info("  python3 features/dra-audio-interface/enable-dra-pi.py --config-only")
        return 1

    failures = 0
    for ctrl, val in MIXER:
        r = subprocess.run(["amixer", "-c", CARD, "sset", ctrl, val],
                           capture_output=True, text=True)
        if r.returncode == 0:
            _ok(f"{ctrl} = {val}")
        else:
            failures += 1
            tail = (r.stderr.strip().splitlines() or ["control not found"])[-1]
            _warn(f"{ctrl}: not set ({tail})")

    if subprocess.run(["sudo", "alsactl", "store"]).returncode == 0:
        _ok("Mixer state persisted (alsactl store).")
    else:
        _warn("alsactl store failed — settings may not survive a reboot.")

    if failures:
        _warn(f"{failures} mixer control(s) could not be set — check the card with "
              f"`amixer -c {CARD} scontrols`.")
    else:
        _ok("DRA-Pi audio mixer applied.")
    print()
    ptt_led_reminder()
    return 0


# ── Entry point ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Configure the DRA-Pi-Zero (WM8731) for GrayWolf.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 features/dra-audio-interface/enable-dra-pi.py             # auto-detect phase\n"
                "  python3 features/dra-audio-interface/enable-dra-pi.py --dry-run   # preview config.txt edits\n"),
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show the config.txt changes without writing.")
    parser.add_argument("--config-only", action="store_true",
                        help="Only do the config.txt phase (force).")
    parser.add_argument("--mixer-only", action="store_true",
                        help="Only do the ALSA mixer phase (force).")
    args = parser.parse_args()

    print("\n  OASIS — enable-dra-pi")
    _hr()
    if sys.platform != "linux":
        _fail("This configures a Raspberry Pi sound card — Linux only.")

    if args.config_only and args.mixer_only:
        _fail("--config-only and --mixer-only are mutually exclusive.")

    if args.mixer_only:
        sys.exit(do_mixer_phase())
    if args.config_only:
        sys.exit(do_config_phase(args.dry_run))

    # Auto: if the card is already up, do the mixer; otherwise configure + reboot.
    if card_present():
        _info(f"Sound card '{CARD}' detected — applying the mixer (post-reboot phase).")
        sys.exit(do_mixer_phase())
    else:
        _info("DRA-Pi sound card not detected — configuring config.txt (pre-reboot phase).")
        sys.exit(do_config_phase(args.dry_run))


if __name__ == "__main__":
    main()
