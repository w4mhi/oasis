"""Play a WAV out of this box's own speaker.

Probe order is pw-play, paplay, aplay. The CM4 measured on 2026-08-08 has
pw-play and aplay and no paplay, so the fallback chain is not theoretical.

XDG_RUNTIME_DIR: the OASIS unit is a SYSTEM service running as the operator's
user (scripts/enable-autostart-pi.py:209), so it inherits the right UID but not
the session environment. PipeWire's socket lives at $XDG_RUNTIME_DIR/pipewire-0
and is owned by that same UID — so without the variable, pw-play cannot find a
server it has every right to talk to. We inject it only when the directory
actually exists; on a headless box with no lingering session there is nothing
to point at and aplay is the honest answer.

Calls are serialised: two announcements at once are mush. A call that arrives
while something is already speaking waits briefly and is then DROPPED rather
than queued — a late warning is noise, and a backlog of stale ones is worse.
"""
import functools
import logging
import os
import shutil
import subprocess
import threading

log = logging.getLogger(__name__)

PLAYERS = ("pw-play", "paplay", "aplay")
PLAY_TIMEOUT_S = 60
_DEFAULT_WAIT_S = 5.0
_LOCK = threading.Lock()


@functools.lru_cache(maxsize=1)
def player():
    """First available player, or None. Cached — PATH does not change under a
    running server, and this is called per announcement."""
    for name in PLAYERS:
        path = shutil.which(name)
        if path:
            return path
    return None


def child_env():
    env = dict(os.environ)
    if not env.get("XDG_RUNTIME_DIR"):
        candidate = f"/run/user/{os.geteuid()}"
        if os.path.isdir(candidate):
            env["XDG_RUNTIME_DIR"] = candidate
    return env


def play(path, timeout_s=PLAY_TIMEOUT_S, wait_s=_DEFAULT_WAIT_S):
    """True only if a player ran and exited 0."""
    exe = player()
    if not exe:
        log.info("speech: no audio player found (%s)", ", ".join(PLAYERS))
        return False
    if not _LOCK.acquire(timeout=wait_s):
        log.info("speech: dropped '%s' — already speaking", path)
        return False
    try:
        r = subprocess.run([exe, path], env=child_env(), capture_output=True,
                           timeout=timeout_s)
        if r.returncode != 0:
            stderr = r.stderr if isinstance(r.stderr, bytes) else b""
            log.warning("speech: %s exited %s: %s", exe, r.returncode,
                        stderr.decode("utf-8", "replace").strip()[:200])
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("speech: %s failed: %s", exe, e)
        return False
    finally:
        _LOCK.release()
