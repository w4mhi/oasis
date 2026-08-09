#!/usr/bin/env python3
"""
features/speech/install.py
---------------------------
Install Piper (neural text-to-speech) into the OASIS server venv, plus its
voice model, for the station-wide speech service (common/speech.py). Optional
and OPT-IN: without it every announcement still speaks — the callers that use
common/speech.py already fall back to espeak-ng (see common/js/sat-alerts.js's
voice ladder) when SPEECH.available() is False.

Not privileged: everything lands in the venv and features/speech/, nothing
under /etc or /usr/local, so this runs as the operator's own user (it is not
in common/setup_registry.py's PRIVILEGED_FEATURES).

Offline-first: wheels come from the bundled features/speech/packages/ tree
(scripts/create-oasis-offline.py's phase_speech), falling back to PyPI when
the wheel dir is empty and there's internet — the same common.server
machinery as setup-server.py and services/satellites/install-predict.py, so
behaviour never drifts. The voice model (~60 MB) has NO online fallback: OASIS
does not download it at install time (see the 'speech-voice' manifest entry).
A bundle built without either piece installs cleanly and simply reports what
is missing, the same posture services/satellites/install-piper.py had before
it (now removed — this replaces the speech-dispatcher approach entirely).

The 2026-08-08 speech-dispatcher installer printed a success mark four times
while producing nothing audible on the operator's Pi. This installer prints
exactly one success mark for the engine — and only after a real synthesis
produces a non-empty WAV. Local playback is reported as information, not
pass/fail: a headless box legitimately has nowhere to play a test utterance,
and that must not fail an otherwise good install.

Usage:
  python3 features/speech/install.py
  python3 features/speech/install.py --uninstall
"""

import argparse
import os
import shutil
import sys

# features/speech/install.py -> repo root is three dirname() calls up
# (features/speech/install.py -> features/speech -> features -> repo root).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
from common import server as S  # noqa: E402
from common import manifest as M  # noqa: E402
from common import config_paths as CP  # noqa: E402
from common.oasis_lib import _hr, _ok, _info, _warn  # noqa: E402
from common import speech as SPEECH, speech_play as PLAY  # noqa: E402

FEATURE = "speech"              # manifest pypi group (piper-tts)
VOICE_FEATURE = "speech-voice"  # manifest url group (the .onnx + sidecar)


def _wheels_dir():
    return os.path.join(
        M.bundle_dir(os.path.join(REPO_ROOT, "offline-packages"), FEATURE), "wheels")


def _voice_bundle_dir():
    return M.bundle_dir(os.path.join(REPO_ROOT, "offline-packages"), VOICE_FEATURE)


def _install_packages():
    """pip-install piper-tts (+ deps) into the venv. Returns a failure count,
    not a bool, so a caller can log exactly what went wrong; 0 means every
    package in the 'speech' manifest group installed."""
    venv_dir = os.path.join(REPO_ROOT, ".venv")
    pip = S._venv_bin(venv_dir, "pip")
    wheels_dir = _wheels_dir()

    specs = S._packages_from_manifest(FEATURE)
    if not specs:
        _warn("No 'speech' packages in the manifest — nothing to install.")
        return 1

    online, banner = S.decide_source(wheels_dir)
    if online is None:
        _warn(f"{os.path.relpath(wheels_dir, REPO_ROOT)}/ is empty and no internet "
              "is reachable — can't fetch Piper now. Re-run with the offline bundle "
              "(or online) later; the espeak-ng voice stays in place meanwhile.")
        return 1
    S.print_source_banner(online, banner, wheels_dir)

    failures = 0
    for spec in specs:
        if not S.install_one(pip, spec, online, wheels_dir):
            failures += 1
    return failures


def _place_voice():
    """Copy the bundled voice model into features/speech/voices/. Returns a
    failure count. Its absence is a hard failure, not a skip: an engine with
    no voice cannot speak, and installing "successfully" without one would be
    exactly the kind of false success this installer exists to rule out."""
    bundle = _voice_bundle_dir()
    try:
        feat = M.get_feature(VOICE_FEATURE)
    except KeyError as e:
        _warn(f"scripts/offline-manifest.json has no '{VOICE_FEATURE}' entry: {e}")
        return 1

    voice = feat["voice"]
    names = [f.replace("{voice}", voice) for f in feat["files"]]
    srcs = [os.path.join(bundle, n) for n in names]
    missing = [n for n, p in zip(names, srcs) if not os.path.isfile(p)]
    if missing:
        _warn(f"Voice model not in the bundle ({', '.join(missing)} missing under "
              f"{os.path.relpath(bundle, REPO_ROOT)}/). An engine with no voice "
              "cannot speak.")
        _info("Rebuild the offline bundle on a connected machine "
              "(scripts/create-oasis-offline.py) to include it.")
        return 1

    dest_dir = CP.speech_voices_dir(REPO_ROOT)
    os.makedirs(dest_dir, exist_ok=True)
    for name, src in zip(names, srcs):
        shutil.copy2(src, os.path.join(dest_dir, name))
    _ok(f"Voice installed: {voice}")
    return 0


def _greeting():
    """The sentence the new voice says on a successful install.

    It doubles as the verification utterance, so it has to be worth listening
    to: the operator has just installed a voice and this is the first and often
    only thing they will hear it say. It names itself and says what it is for,
    so "did it work?" and "what does it do?" are answered in one line.

    The name is DERIVED from the installed model rather than hardcoded. The
    voice is configurable — any .onnx in features/speech/voices/ — and a
    greeting that introduces itself as Jenny on a station running lessac is a
    small lie the operator cannot correct. en_GB-jenny_dioco-medium yields
    "Jenny"; anything unparseable simply drops the name rather than guessing.

    Deliberately not "your assistant": OASIS has a real assistant feature (a
    local LLM on its own page), and this is a text-to-speech voice. Borrowing
    that word here would promise something this does not do.
    """
    model = (SPEECH.voice_info(REPO_ROOT).get("name") or "")
    parts = model.split("-")
    who = parts[1].split("_")[0].capitalize() if len(parts) > 1 and parts[1] else ""
    if who:
        return (f"Hello, I am {who}. I will read your satellite pass alerts "
                "and station announcements.")
    return "Hello. I will read your satellite pass alerts and station announcements."


def run():
    _hr()
    print("  ▶  Speech (Piper neural voice)")
    _hr()

    # Refuse early and cleanly on a platform Piper cannot serve, before
    # touching pip: an unsupported platform is a correct outcome, not a
    # failed install, and it must leave the existing espeak-ng voice alone.
    # SPEECH.platform_supported() is the single source of truth for this —
    # common/setup_registry.py's verify_fn reads the same function, so the
    # Setup Orchestrator and this script never again disagree about the same
    # box the way they did before (installer declines and exits 0; the
    # registry still called available(), also False here, and reported a red
    # "verify failed" right after this printed "nothing was changed").
    supported, reason = SPEECH.platform_supported()
    if not supported:
        _warn(f"Piper is not supported on this platform: {reason}. "
              "The espeak-ng voice stays in place; nothing was changed.")
        return 0

    venv_dir = os.path.join(REPO_ROOT, ".venv")
    if not os.path.exists(S._venv_bin(venv_dir, "python")):
        _warn(f"No server venv at {venv_dir} — run scripts/setup-server.py first "
              "(the Speech feature depends on the server). Nothing to do.")
        return 1

    if _install_packages():
        _warn("Piper package install failed — see the pip errors above.")
        return 1

    if _place_voice():
        return 1

    # Verification is real, and it is the only thing allowed to print success.
    try:
        wav = SPEECH.synthesize(REPO_ROOT, _greeting())
    except Exception as e:
        _warn(f"Piper installed but could not synthesise: {e}")
        return 1
    if os.path.getsize(wav) == 0:
        _warn("Piper produced an empty file — not installing over that.")
        return 1
    _ok(f"Synthesised {os.path.getsize(wav)} bytes with "
        f"{SPEECH.voice_info(REPO_ROOT)['name']}.")

    # Playback is INFORMATION, not pass/fail: a headless box with no sound
    # output legitimately has nowhere to play this, and that must not fail an
    # otherwise good install.
    if PLAY.player():
        _info(f"Playing a test utterance through {PLAY.player()} …")
        if PLAY.play(wav):
            _ok("Heard it? If not, check the mixer — the audio itself is fine.")
        else:
            _warn("The player exited non-zero. Synthesis is fine; this is an "
                  "audio-device problem. See docs/SETUP.md.")
    else:
        _info("No local audio player found — browser playback still works.")

    return 0


def cmd_uninstall():
    _hr()
    print("  ▶  Removing Piper speech")
    _hr()

    venv_dir = os.path.join(REPO_ROOT, ".venv")
    pip = S._venv_bin(venv_dir, "pip")
    if os.path.exists(pip):
        # Deliberately leaves onnxruntime (piper-tts's dependency, and the
        # large half of the install) in the venv — pip uninstall doesn't
        # cascade to dependencies, and removing it ourselves risks pulling
        # out something else in the venv still relies on.
        r = S._pip([pip, "uninstall", "--yes", "piper-tts"])
        if r.returncode == 0:
            _ok("piper-tts removed from the venv (no-op if it wasn't installed).")
        else:
            _warn(f"pip uninstall reported an error (continuing): "
                  f"{(r.stderr or '').strip()[:200]}")
    else:
        _info("No venv found — nothing to pip-uninstall.")

    for d in (CP.speech_voices_dir(REPO_ROOT), CP.speech_cache_dir(REPO_ROOT)):
        rel = os.path.relpath(d, REPO_ROOT)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            _ok(f"Removed {rel}/")
        else:
            _info(f"{rel}/ already gone.")

    _info("The espeak-ng voice is untouched — that is a different feature "
          "('satellites-voice'), and removing it here would silence a "
          "station that never asked for Piper.")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Install Piper (neural text-to-speech) for the OASIS "
                    "speech service. Optional; espeak-ng is the fallback.")
    ap.add_argument("--uninstall", action="store_true",
                    help="Remove the voice, the cache, and pip-uninstall piper-tts.")
    args = ap.parse_args()
    return cmd_uninstall() if args.uninstall else run()


if __name__ == "__main__":
    sys.exit(main())
