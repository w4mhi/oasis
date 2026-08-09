# Speech (Piper neural voice)

Station-wide text-to-speech, backed by
[Piper](https://github.com/OHF-voice/piper1-gpl) —
pass alerts today, guardian and Winlink announcements next. Optional and
**opt-in**: without this feature, everything that speaks still speaks, using
the espeak-ng fallback voice the voice ladder in `common/js/sat-alerts.js`
already falls back to.

## What's here

- `install.py` — the installer/uninstaller (see below).
- `voices/` — the installed `.onnx` model + its `.onnx.json` sidecar
  (created by the installer; gitignored, not shipped in the repo).
- `cache/` — synthesised WAVs, keyed by `sha256(params, voice, text)`
  (`common/speech.py`); size-bounded, oldest evicted first.
- `packages/` — the bundled wheels (`packages/speech/wheels/`) and the voice
  model (`packages/speech/`), placed here by
  `scripts/create-oasis-offline.py`'s `phase_speech`.

The actual synthesis logic lives in `common/speech.py` (text → cached WAV
path) and `common/speech_play.py` (WAV → the box's own speaker). This
directory only installs what those two modules need.

## Why a subprocess, not the Python API

`common/speech.py` shells out to `python -m piper` per cache miss rather than
keeping a `PiperVoice` resident in-process. gunicorn runs several workers; an
in-process model would put onnxruntime plus a ~60 MB model in *every* one of
them, which is the difference between comfortable and swapping on a 2 GB
Pi 3. See the module docstring for the full reasoning (also: no shell
quoting, because text goes on stdin).

## Why it can be uninstalled without touching espeak-ng

Piper is one voice among several the ladder can pick; espeak-ng
(`satellites-voice`) is the always-present fallback, installed by a
different feature entirely. `install.py --uninstall` removes only what this
feature added — the venv's `piper-tts`, `features/speech/voices/`, and
`features/speech/cache/` — and never touches espeak-ng. A station that never
opted into Piper is unaffected by installing or removing it.

## Platform support

Piper needs **Python 3.11+** and is **not available on 32-bit ARM**
(`armv7l`/`armv6l`) — `onnxruntime` publishes no wheels for either. The
installer checks both *before* touching pip and exits `0` (not `1`): an
unsupported platform is a correct outcome, not a failed install, and the
espeak-ng voice stays in place either way.

## Install / uninstall

```bash
python3 features/speech/install.py              # install
python3 features/speech/install.py --uninstall   # remove (idempotent)
```

Not privileged — it only writes into the venv and this directory, so it runs
as the operator's own user (it is not in
`common/setup_registry.py`'s `PRIVILEGED_FEATURES`).

## What "success" means here

The 2026-08-08 speech-dispatcher installer for the pass-alert voice printed a
success mark four times while producing nothing audible on the operator's
Pi. This installer prints exactly one success mark for the engine itself —
and only after a real synthesis (`SPEECH.synthesize`) produces a **non-empty
WAV**. Everything else — pip install, copying the voice model, local
playback — is reported as information (`_info`/`_warn`), never as success.

In particular, **playing the test utterance locally is not part of pass/fail**.
A headless box legitimately has nowhere to play it (`speech_play.player()`
returns `None`), and that must not fail an otherwise-good install.

## Debugging a station with no voice

1. **Is the feature even installed?**
   `python3 -c "from common import speech as S; print(S.available('.'))"`
   from the repo root. `False` means no `.onnx` + `.onnx.json` pair is in
   `features/speech/voices/` — check the installer's output for why (missing
   from the bundle, platform declined, pip failed).

2. **Does synthesis work at all, independent of the caller?**
   ```bash
   .venv/bin/python -c "
   from common import speech as S
   print(S.synthesize('.', 'test'))"
   ```
   A `SpeechUnavailable` here is an engine/model problem — re-run
   `install.py` and read its output. A `SpeechRejected` means the *text*, not
   the engine, is the problem (empty, too long, or has control characters —
   see `common/speech.py`'s `validate()`).

3. **Synthesis works but nothing comes out of the speaker?** That is a
   playback problem, not a speech problem — see `common/speech_play.py`.
   Check `speech_play.player()` finds one of `pw-play`/`paplay`/`aplay` on
   `PATH`, and that `XDG_RUNTIME_DIR` is right for a systemd *system*
   service running as the operator's user (PipeWire's socket is
   UID-scoped — see the module docstring).

4. **Piper subprocess errors** (`piper exited N: ...`) print the tail of
   Piper's own stderr — that is almost always the actual root cause (a
   missing/corrupt `.onnx.json` sidecar, an ABI mismatch on the installed
   `onnxruntime` wheel, or a timeout on a very slow Pi).

## Requirements

Python 3.11+, 64-bit ARM/x86 (or macOS/Windows for dev). `piper-tts` from
PyPI or the offline bundle. The voice model has no online fallback at
install time — OASIS does not download it itself; it must already be in the
offline bundle (`scripts/create-oasis-offline.py`, ~60 MB from upstream).

## Credits and licences

OASIS **ships neither the engine nor the voice**. Both are fetched from upstream
by the operator's own `scripts/create-oasis-offline.py` run — the same posture as
the Wikipedia ZIM and the FCC database. That is deliberate: it keeps large
binaries out of the repo and leaves each licence between the operator and
upstream rather than making OASIS a redistributor.

**Voice — Jenny (Dioco)**
`en_GB-jenny_dioco-medium`, from the [Jenny TTS
dataset](https://github.com/dioco-group/jenny-tts-dataset), packaged by
[piper-voices](https://huggingface.co/rhasspy/piper-voices).

The dataset carries a **custom attribution licence**, not CC-BY — do not
describe it as CC-BY, as there is no licence URL to reproduce and no
statement-of-changes requirement. It asks that any software or interface
generating audio from the voice credit it, calling it **"Jenny"** and, where at
all practical, **"Jenny (Dioco)"**. A voice interface that speaks on user action
is exactly the case the licence names, so OASIS is in scope. Commercial use is
permitted and ownership is not claimed. Attribution is *not* required when
distributing generated audio clips — so a recorded pass alert needs no notice,
but the station that produced it does.

`install.py` writes `voices/ATTRIBUTION.txt` alongside the model, so the credit
travels with the file onto whatever SD card it is copied to. `ATTRIBUTION` in
`install.py` is the single source of that text.

**Engine — Piper**, Open Home Foundation, **GPL-3.0**:
<https://github.com/OHF-voice/piper1-gpl>. It embeds **espeak-ng** (GPL) purely
as a phonemiser — text to phonemes; the audio itself comes from the neural
model. Note that the older `rhasspy/piper` repository is superseded.

If you hand this station, or a USB image built from it, to someone else, pass
the attribution along with it.
