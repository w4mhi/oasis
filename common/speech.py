"""Text to speech audio, as a cached file. No audio device is touched here.

Why a subprocess and not the piper-tts Python API: gunicorn runs several
workers, and an in-process PiperVoice keeps onnxruntime plus a ~60 MB model
resident in EVERY one of them. On a 2 GB Pi 3 that is the difference between
comfortable and swapping. The cost is the ~0.37 s model load (measured on a
Pi 5), and the cache means it is paid once per DISTINCT sentence, not per
request. A fault in native onnxruntime also kills a child instead of an API
worker.

Why text goes on stdin: satellite names come from TLE files and future message
subjects come off the air. Neither is ours. With shell=False and text never
placed in argv, there is no quoting to get right — which is exactly what the
2026-08-08 speech-dispatcher attempt had to get right, and did not.
"""
import hashlib
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading

from common import config_paths as CP

MAX_TEXT_CHARS = 300
CACHE_BUDGET_BYTES = 50 * 1024 * 1024      # ~100 KB per phrase → thousands of them
SYNTH_TIMEOUT_S = 120                      # generous: a Pi 3 is ~5-8x a Pi 5
SYNTH_WAIT_S = 30                          # how long a caller waits for the lock
PARAMS_VERSION = "2"                       # bump to invalidate every cached WAV

# ── Readable cache names ─────────────────────────────────────────────────────
# A cache full of 64-hex filenames tells a human nothing. The two announcement
# kinds that recur forever — the hour bell and the greeting — carry a readable
# label; everything else stays a bare hash, and that unevenness is DELIBERATE:
#
#   * a pass alert's text is transient and names a bird already in the roster,
#     so a label would add noise without adding knowledge;
#   * the easter egg's text must NOT appear on disk. jenny.spk is base64-encoded
#     purely as spoiler protection — against code search, an idle grep, the file
#     open in an editor — and a readable name in the speech cache would undo that
#     encoding completely. A bare hash does not even reveal that an egg exists.
#
# So: do NOT "tidy" this into slugging every kind. See tests/test_speech_cache.py.
#
# `kind` reaches this from an unauthenticated LAN endpoint and becomes part of a
# FILENAME, so it is whitelisted rather than sanitised: uppercase, digits and
# underscore only. No dot and no separator means no traversal and no `..`, by
# construction rather than by escaping.
KIND_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")


def clean_kind(kind):
    """A safe filename prefix, or None. Never raises: a malformed kind costs the
    name its label, and must not cost the operator the announcement."""
    if not kind:
        return None
    kind = str(kind).strip().upper()
    return kind if KIND_RE.match(kind) else None

# gunicorn runs this app with --workers 1 --threads 4 (start-oasis.py,
# scripts/start-server.sh), so concurrency here is threads within ONE process
# — a plain threading.Lock is enough. It serialises cache-miss synthesis so N
# simultaneous requests for N distinct phrases cannot spawn N concurrent piper
# subprocesses, each loading onnxruntime plus a ~60 MB model: exactly the
# memory the subprocess design exists to keep off a 2 GB Pi 3.
_SYNTH_LOCK = threading.Lock()

# Everything except tab (09), newline (0a) and carriage return (0d).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class SpeechUnavailable(RuntimeError):
    """No engine, no model, or synthesis failed. Callers fall back."""


class SpeechRejected(ValueError):
    """The text itself is not acceptable. `code` is a stable slug (§3)."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def validate(text):
    if not isinstance(text, str) or not text.strip():
        raise SpeechRejected("no text to speak", "EMPTY_TEXT")
    if len(text) > MAX_TEXT_CHARS:
        raise SpeechRejected(f"text longer than {MAX_TEXT_CHARS} characters",
                             "TEXT_TOO_LONG")
    if _CONTROL_RE.search(text):
        raise SpeechRejected("text contains control characters", "INVALID_TEXT")
    return text


def platform_supported():
    """Whether THIS platform can run Piper at all, regardless of whether the
    engine or a voice model is actually installed — the single source of
    truth for both features/speech/install.py's early gate and the setup
    registry's verify_fn, so the two never drift the way they did before
    (installer declines and exits 0; the Setup Orchestrator's verify_fn still
    called `available()`, which is also False on a declined platform, and
    reported it as a red "verify failed").

    Returns (True, None) when supported, or (False, reason) with a short
    human string when not — onnxruntime, Piper's own dependency, publishes no
    wheel for Python < 3.11 or for 32-bit ARM.
    """
    if sys.version_info < (3, 11):
        return False, "needs Python 3.11+"
    if platform.machine() in ("armv7l", "armv6l"):
        return False, "no onnxruntime wheel for 32-bit ARM"
    return True, None


def voice_model_path(repo_root):
    """The .onnx to speak with, or None. A model without its .onnx.json sidecar
    does not count — Piper will not load one without the other, so reporting it
    would promise a voice that cannot speak."""
    voices = CP.speech_voices_dir(repo_root)
    try:
        names = sorted(f for f in os.listdir(voices) if f.endswith(".onnx"))
    except OSError:
        return None
    for name in names:
        path = os.path.join(voices, name)
        if os.path.isfile(path + ".json"):
            return path
    return None


def available(repo_root):
    return voice_model_path(repo_root) is not None


def voice_info(repo_root):
    """§5: every key always present, null for what cannot be known."""
    model = voice_model_path(repo_root)
    info = {"name": None, "model": None, "sample_rate_hz": None}
    if not model:
        return info
    info["name"] = os.path.basename(model)[:-len(".onnx")]
    info["model"] = model
    try:
        import json
        with open(model + ".json", encoding="utf-8") as fh:
            info["sample_rate_hz"] = json.load(fh).get("audio", {}).get("sample_rate")
    except (OSError, ValueError):
        pass
    return info


def cache_key(text, voice_id, kind=None):
    """The content hash. `kind` is INSIDE it, not merely a prefix, so the name
    stays a single deterministic stat — no globbing, and no cache miss when a
    caller starts or stops passing one. The same words in two contexts are
    genuinely two announcements, so two entries is correct rather than wasteful."""
    h = hashlib.sha256()
    h.update(PARAMS_VERSION.encode())
    h.update(b"\0")
    h.update(str(voice_id).encode())
    h.update(b"\0")
    h.update((clean_kind(kind) or "").encode())
    h.update(b"\0")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def cache_name(text, voice_id, kind=None):
    """The WAV's basename without extension: `KIND_<hash>` or just `<hash>`."""
    prefix = clean_kind(kind)
    key = cache_key(text, voice_id, kind)
    return f"{prefix}_{key}" if prefix else key


def _python(repo_root):
    """The venv interpreter, which is where piper-tts is installed. Falls back
    to this process's interpreter for a dev box running outside the venv."""
    cand = os.path.join(repo_root, ".venv", "bin", "python")
    return cand if os.path.isfile(cand) else sys.executable


def _cached(out):
    if os.path.isfile(out) and os.path.getsize(out) > 0:
        try:
            os.utime(out, None)          # touch: prune evicts by age, keep hot entries
        except OSError:
            pass
        return out
    return None


def synthesize(repo_root, text, *, kind=None, wait_s=SYNTH_WAIT_S):
    """text -> absolute path to a WAV. Raises SpeechRejected / SpeechUnavailable.

    `kind` is an optional readable filename label (see KIND_RE). It changes the
    cache key, so passing one for the first time re-synthesises that phrase once."""
    text = validate(text)
    model = voice_model_path(repo_root)
    if not model:
        raise SpeechUnavailable("no voice model installed")

    cache = CP.speech_cache_dir(repo_root)
    out = os.path.join(cache, cache_name(text, os.path.basename(model), kind) + ".wav")
    hit = _cached(out)
    if hit:
        return hit

    if not _SYNTH_LOCK.acquire(timeout=wait_s):
        raise SpeechUnavailable("speech engine is busy, try again shortly")
    try:
        # Someone may have synthesised this exact phrase while we waited.
        hit = _cached(out)
        if hit:
            return hit

        tmp = None
        try:
            os.makedirs(cache, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=cache, suffix=".wav.part")
            os.close(fd)
            argv = [_python(repo_root), "-m", "piper", "-m", model, "-f", tmp]
            r = subprocess.run(argv, input=text, text=True, capture_output=True,
                               timeout=SYNTH_TIMEOUT_S)
            if r.returncode != 0:
                raise SpeechUnavailable(
                    f"piper exited {r.returncode}: {(r.stderr or '').strip()[:200]}")
            if os.path.getsize(tmp) == 0:
                raise SpeechUnavailable("piper produced an empty file")
            os.replace(tmp, out)         # atomic: a reader never sees a half WAV
        except subprocess.TimeoutExpired:
            raise SpeechUnavailable(f"piper timed out after {SYNTH_TIMEOUT_S}s")
        except FileNotFoundError as e:
            raise SpeechUnavailable(f"piper is not installed: {e}")
        except OSError as e:
            raise SpeechUnavailable(f"speech cache unavailable: {e}")
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

        prune(repo_root)
        return out
    finally:
        _SYNTH_LOCK.release()


def _entries(repo_root):
    cache = CP.speech_cache_dir(repo_root)
    try:
        names = os.listdir(cache)
    except OSError:
        return []
    rows = []
    for name in names:
        if not name.endswith(".wav"):
            continue
        p = os.path.join(cache, name)
        try:
            st = os.stat(p)
        except OSError:
            continue
        rows.append((st.st_mtime, st.st_size, p))
    return sorted(rows)                  # oldest first


def prune(repo_root, budget_bytes=CACHE_BUDGET_BYTES):
    """Evict oldest-first until the cache fits. The newest entry is NEVER
    evicted: it is almost always the one just written, and dropping it would
    mean synthesising the same sentence again on the very next request."""
    rows = _entries(repo_root)
    total = sum(size for _, size, _ in rows)
    removed = 0
    for _, size, path in rows[:-1]:      # [:-1] protects the newest
        if total <= budget_bytes:
            break
        try:
            os.unlink(path)
            total -= size
            removed += 1
        except OSError:
            pass
    return removed


def cache_stats(repo_root):
    rows = _entries(repo_root)
    return {"entries": len(rows),
            "bytes": sum(size for _, size, _ in rows),
            "budget_bytes": CACHE_BUDGET_BYTES}
