"""Single source of truth for the Setup Orchestrator's feature registry.

Shared by two callers that must agree exactly on WHAT each feature's install
step does, even though they differ in WHO runs it:
  - server/app.py (the web Setup Orchestrator) — runs non-privileged features
    in-process, and hands privileged features off to the out-of-process root
    worker (see scripts/oasis_installer_worker.py) via RunOptions.privileged_run_fn.
  - scripts/oasis_installer_worker.py — the privileged worker itself, which
    imports build_registry() the same way and calls a privileged feature's
    install_fn() directly (it already runs as root, so the exact same
    subprocess-based install_fn works unmodified: any `sudo <cmd>` inside the
    called script succeeds instantly when the caller is already root).

A FeatureSpec's `privileged` flag marks features whose install_fn needs real
root (writes /etc, /usr/local/bin, apt/GPG, sudoers, systemd units, ...) — the
in-process web server never has that (no TTY, no cached sudo credential).
"""

import importlib
import importlib.util
import os
import queue
import subprocess
import sys
import threading
import time

from common import setup_engine as SE
from common import server as SERVER_SETUP
from common import speech as SPEECH


# ── Per-feature removal records ───────────────────────────────────────────────
# Each feature's removal_record() lives in its own installer module (sourced from
# that module's constants) so removal never drifts from what install created — the
# single-source-of-truth rule. build_registry() wires each spec's
# removal_record_fn to load and call it; install persists the result into
# installed-services.json (see common/installed_services.py).
_REMOVAL_MODULE_CACHE = {}


def _removal_module(repo_root, ref):
    """Load the module hosting a feature's removal_record().

    *ref* is either a dotted importable module name
    ("services.graywolf.common.graywolf") or a repo-relative path to a hyphenated
    script that cannot be imported normally ("scripts/enable-ap-fallback.py")."""
    if ref in _REMOVAL_MODULE_CACHE:
        return _REMOVAL_MODULE_CACHE[ref]
    if ref.endswith(".py"):
        path = os.path.join(repo_root, ref)
        mod_name = "oasis_removal_" + ref[:-3].replace("/", "_").replace("-", "_")
        spec = importlib.util.spec_from_file_location(mod_name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    else:
        mod = importlib.import_module(ref)
    _REMOVAL_MODULE_CACHE[ref] = mod
    return mod


def _removal_record(repo_root, ref):
    return _removal_module(repo_root, ref).removal_record(repo_root)


def _satellites_removal_record(repo_root):
    """Satellites has no dedicated service; it installs the Skyfield/numpy stack
    (venv, shared), the pass-alert voice (apt, shared), and a CelesTrak TLE cache.
    Treated data-style: advise on the TLE cache, deregister from the manifest, and
    leave the shared venv/apt stacks alone (per the design decision)."""
    from common import config_paths as _cp
    return {"data_paths": [_cp.tle_cache_dir(repo_root)]}


# repeaterbook and forms are record-only features (no installer module to host a
# removal_record); their teardown is "hide the card + advise on data", defined here
# at their definition site.
def _repeaterbook_removal_record(repo_root):
    """RepeaterBook ships a single copyrighted CSV under static/repeaterbook/;
    advisory-only (never auto-deleted). Uninstalling hides the dashboard card."""
    return {"data_paths": [os.path.join(repo_root, "static", "repeaterbook")]}


def _forms_removal_record(repo_root):
    """ICS forms ship with the repo (not downloaded data); uninstalling only hides
    the dashboard card. Any saved form instances are left untouched."""
    return {"notes": ["ICS forms ship with the repo; uninstalling hides the "
                      "dashboard card only. Saved form instances are left untouched."]}

# Shared exit-code convention used by several install scripts (e.g.
# features/dra-audio-interface/enable-dra-pi.py, features/cm4stack/
# install-cm4stack.py, features/gps-L76X/
# gps_l76x.py) to mean "config written successfully, but a reboot is needed
# before the next phase/effect can proceed" — NOT a real failure.
_REBOOT_EXIT_CODE = 10

# ── Live console log streaming ──────────────────────────────────────────────
# _setup_run_script() normally buffers a script's stdout/stderr and only
# returns it once the whole process exits — fine for quick scripts, but a
# slow one (FCC database download + index build, Wikipedia ZIM download, ...)
# then looks completely silent in the Setup Orchestrator's console for
# minutes. set_log_sink() lets the *current thread* bind a `line: str -> None`
# callback that gets called live, as the subprocess produces output. It's
# thread-local (not a plain module global) so it can never leak across
# concurrent callers; server/app.py's job runner sets it for the duration of
# one setup job (only one job runs at a time) and clears it afterwards.
_log_ctx = threading.local()


def set_log_sink(fn):
    """Bind (or clear, with fn=None) the live subprocess-output callback for
    the calling thread. See module docstring above _log_ctx for why."""
    _log_ctx.fn = fn


def _current_log_sink():
    return getattr(_log_ctx, "fn", None)


def _stream_process_output(proc, log_fn, timeout):
    """Read *proc*'s combined stdout+stderr as it's produced, calling
    log_fn(line) for each non-empty line as soon as it appears.

    Uses readline() (via iterating the file object), which -- unlike
    read(N) -- returns as soon as a line is available instead of blocking
    until N bytes accumulate or the process exits. That matters here: most
    of our scripts' total output is well under any fixed chunk size, so a
    read(N)-based reader would silently buffer everything until EOF, which
    is exactly the "looks frozen" behavior this whole mechanism exists to
    fix. (common/oasis_lib.py's Progress class prints newline-terminated
    updates when stdout isn't a tty -- i.e. always, under Popen -- so
    download progress streams as real lines too.)

    A background reader thread does the actual (blocking) readline() calls
    so the *timeout* budget can still be enforced here without stalling on a
    read that never returns. Returns (full_text, timed_out)."""
    q = queue.Queue()

    def _reader():
        try:
            for raw_line in proc.stdout:
                q.put(raw_line)
        except Exception:
            pass
        finally:
            q.put(None)  # EOF sentinel

    threading.Thread(target=_reader, daemon=True).start()

    start = time.monotonic()
    chunks = []
    timed_out = False
    while True:
        remaining = timeout - (time.monotonic() - start)
        if remaining <= 0:
            timed_out = True
            proc.kill()
            break
        try:
            raw_line = q.get(timeout=min(1.0, max(0.05, remaining)))
        except queue.Empty:
            continue
        if raw_line is None:
            break
        chunks.append(raw_line)
        line = raw_line.rstrip("\r\n")
        if line and log_fn:
            try:
                log_fn(line)
            except Exception:
                pass

    try:
        proc.stdout.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=5 if timed_out else None)
    except Exception:
        pass
    return "".join(chunks), timed_out


def _setup_run_script(repo_root, script_rel, args=None, timeout=300):
    args = args or []
    script = os.path.join(repo_root, script_rel)
    if not os.path.exists(script):
        return {
            "ok": False,
            "reason_code": "MISSING_SCRIPT",
            "reason_text": f"missing script: {script_rel}",
        }

    log_fn = _current_log_sink()
    timed_out = False
    if log_fn is None:
        # No live sink bound (CLI/tests/privileged worker) — cheap buffered path.
        try:
            r = subprocess.run([sys.executable, script, *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "reason_code": "TIMEOUT",
                "reason_text": f"script timed out: {script_rel}",
            }
        except Exception as exc:
            return {"ok": False, "reason_code": "INSTALL_FAILED", "reason_text": str(exc)}
        stdout, stderr, returncode = r.stdout, r.stderr, r.returncode
    else:
        # A live sink is bound (the web Setup Orchestrator's job runner) —
        # stream stdout+stderr line-by-line as the script runs instead of
        # buffering silently until it exits.
        try:
            proc = subprocess.Popen(
                [sys.executable, script, *args],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
        except Exception as exc:
            return {"ok": False, "reason_code": "INSTALL_FAILED", "reason_text": str(exc)}
        stdout, timed_out = _stream_process_output(proc, log_fn, timeout)
        stderr, returncode = "", proc.returncode
        if timed_out:
            return {
                "ok": False,
                "reason_code": "TIMEOUT",
                "reason_text": f"script timed out: {script_rel}",
                "stdout_tail": stdout.strip()[-300:] or None,
            }

    if returncode == _REBOOT_EXIT_CODE:
        return {"ok": True, "requires_reboot": True}
    if returncode != 0:
        err = (stderr or stdout or "script failed").strip()
        low = err.lower()
        reason_code = "INSTALL_FAILED"
        if any(tok in low for tok in [
            "temporary failure in name resolution",
            "failed to fetch",
            "network is unreachable",
            "could not resolve",
            "could not reach the internet",
            "could not download the flightaware apt-repository package",
        ]):
            reason_code = "INTERNET_REQUIRED"
        elif "command not found" in low and ("curl" in low or "gpg" in low):
            reason_code = "MISSING_TOOL"
        return {
            "ok": False,
            "reason_code": reason_code,
            "reason_text": err.splitlines()[-1][:300] if err else f"script failed: {script_rel}",
            "stderr_tail": (stderr or "").strip()[-300:] or None,
            "stdout_tail": (stdout or "").strip()[-300:] or None,
        }
    return {"ok": True}


def _setup_run_chain(repo_root, steps):
    needs_reboot = False
    for step in steps:
        res = _setup_run_script(repo_root, step.get("script"), step.get("args"), timeout=step.get("timeout", 300))
        if not res.get("ok"):
            return res
        needs_reboot = needs_reboot or bool(res.get("requires_reboot"))
    return {"ok": True, "requires_reboot": True} if needs_reboot else {"ok": True}


def _setup_record_only(_name):
    return {"ok": True}


def _setup_server_install(repo_root):
    try:
        SERVER_SETUP.run(check_mode=False, repo_root=repo_root)
        return {"ok": True}
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return {
            "ok": False,
            "reason_code": "INSTALL_FAILED",
            "reason_text": f"server setup exited with code {code}",
        }
    except Exception as exc:
        return {"ok": False, "reason_code": "INSTALL_FAILED", "reason_text": str(exc)}


def _setup_service_controls_install(repo_root):
    script = os.path.join(repo_root, "scripts", "enable-service-controls.py")
    try:
        r = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=120)
    except Exception as exc:
        return {"ok": False, "reason_code": "INSTALL_FAILED", "reason_text": str(exc)}
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "service-controls install failed").strip()
        return {
            "ok": False,
            "reason_code": "INSTALL_FAILED",
            "reason_text": err.splitlines()[-1][:300] if err else "service-controls install failed",
            "stderr_tail": (r.stderr or "").strip()[-300:] or None,
            "stdout_tail": (r.stdout or "").strip()[-300:] or None,
        }
    return {"ok": True}


def _setup_winlink_install_fn(repo_root, payload):
    win = (payload or {}).get("winlink", {})
    args = []
    callsign = (win.get("callsign") or "").strip()
    if callsign:
        args += ["--callsign", callsign]
    locator = (win.get("locator") or "").strip()
    if locator:
        args += ["--locator", locator]
    password = win.get("password") or ""
    if password:
        args += ["--password", password]
    else:
        args += ["--no-password"]
    return _setup_run_script(repo_root, "services/winlink/install.py", args)


def _setup_dashboard_install_fn(repo_root, payload):
    """Install the OASIS Dashboard kiosk at the operator's chosen resolution.
    Resolution rides in on the payload (defaults to the 800x480 panel)."""
    dash = (payload or {}).get("oasis-dashboard", {})
    res = (dash.get("resolution") or "800x480").strip()
    if res not in ("800x480", "1920x1200"):
        res = "800x480"
    return _setup_run_script(repo_root, "scripts/enable-autostart-pi.py", ["--resolution", res])


# Feature keys whose install_fn needs real root. Kept as one explicit list right
# next to build_registry() (rather than inferred) so it's easy to audit — every
# key here MUST have a corresponding install_fn that is safe to run as root
# unattended (no interactive prompts beyond `sudo`, which is a no-op when the
# caller is already root).
PRIVILEGED_FEATURES = {
    "webssh", "service-controls", "ap-fallback", "graywolf", "winlink", "kiwix",
    "openwebrx", "adsb", "satellites",
    "rtl-sdr", "rtl-sdr-feed", "gps", "gps-l76x", "dra-pi-rx-led", "rtc",
    "rtc-raspad",
    "draws-gps", "draws-audio",
    "pi-headless", "pi-local-monitor", "pi-oasis-dashboard", "cm4stack", "rgb-cooling-hat",
    "argon-fan",
}


def build_registry(repo_root, payload=None):
    """Build the Setup Orchestrator's feature -> FeatureSpec registry.

    *repo_root* is the suite root (SUITE_ROOT in server/app.py; the worker
    resolves its own equivalent). *payload* is the setup form payload
    (station/winlink/wifi/...) — the same dict shape both callers pass, so a
    privileged feature's install_fn is byte-for-byte identical whether it runs
    in-process (non-privileged) or in the out-of-process root worker.
    """
    return {
        "server": SE.FeatureSpec(
            key="server",
            dependencies=[],
            install_fn=lambda: _setup_server_install(repo_root),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "webssh": SE.FeatureSpec(
            key="webssh",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "services/webssh/install.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "common.webssh"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            # Remote shell — a lifeline for an SSH-driven uninstall, so torn down
            # late (after everything but pi-headless).
            teardown_priority=20,
            privileged=True,
        ),
        "service-controls": SE.FeatureSpec(
            key="service-controls",
            dependencies=["server"],
            install_fn=lambda: _setup_service_controls_install(repo_root),
            removal_record_fn=lambda: _removal_record(repo_root, "scripts/enable-service-controls.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            # Dashboard service control — kept late so the operator can still
            # start/stop units through most of the teardown.
            teardown_priority=10,
            privileged=True,
        ),
        "ap-fallback": SE.FeatureSpec(
            key="ap-fallback",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "scripts/enable-ap-fallback.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "scripts/enable-ap-fallback.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "graywolf": SE.FeatureSpec(
            key="graywolf",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "services/graywolf/install.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "services.graywolf.common.graywolf"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "winlink": SE.FeatureSpec(
            key="winlink",
            dependencies=["server"],
            install_fn=lambda: _setup_winlink_install_fn(repo_root, payload),
            removal_record_fn=lambda: _removal_record(repo_root, "services.winlink.common.winlink"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "kiwix": SE.FeatureSpec(
            key="kiwix",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "services/kiwix/install.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "services.kiwix.common.kiwix"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "openwebrx": SE.FeatureSpec(
            key="openwebrx",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "services/openwebrx/install.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "services.openwebrx.common.openwebrx"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "adsb": SE.FeatureSpec(
            key="adsb",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "services/adsb/install.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "services.adsb.common.adsb"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        # The Satellites page itself ships with `server` (Flask blueprint); this
        # feature installs its three dependencies — the Python prediction stack
        # (Skyfield + numpy into the venv, else /passes and /track 500), the
        # CelesTrak TLE cache (the elements pass prediction propagates), and the
        # pass-alert voice (speech-dispatcher + espeak-ng). Prediction runs first
        # so the engine is ready; each sub-script self-guards off-Linux/offline,
        # so the chain degrades gracefully rather than failing (voice needs apt →
        # privileged).
        "satellites": SE.FeatureSpec(
            key="satellites",
            dependencies=["server"],
            install_fn=lambda: _setup_run_chain(repo_root, [
                {"script": "services/satellites/install-predict.py"},
                {"script": "services/satellites/build-roster.py"},
                {"script": "services/satellites/install-voice.py", "timeout": 600},
            ]),
            removal_record_fn=lambda: _satellites_removal_record(repo_root),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        # Station-wide speech service (Piper neural TTS): pass alerts today,
        # guardian and Winlink announcements next — so no `dependencies` on
        # `satellites`, unlike the speech-dispatcher-era feature this replaces.
        # Not privileged: nothing needs root now that /etc/speech-dispatcher is
        # out of the picture (see common/speech.py — a subprocess piped to a
        # cached WAV, no system TTS daemon involved). Self-guards on an
        # unsupported platform or a bundle with no engine/voice, so a station
        # built without them installs cleanly and simply keeps the espeak
        # fallback.
        "speech": SE.FeatureSpec(
            key="speech",
            dependencies=[],                 # guardian and Winlink want it too
            install_fn=lambda: _setup_run_script(
                repo_root, "features/speech/install.py", timeout=900),
            removal_record_fn=lambda: {
                "script": [os.path.join(repo_root, "features/speech/install.py"),
                           "--uninstall"],
            },
            verify_fn=lambda: {"ok": SPEECH.available(repo_root)},
            enable_policy="none",
            privileged=False,
        ),
        # RTL-SDR driver + tools (librtlsdr / rtl_fm / socat + the DVB blacklist).
        # Its own feature so ADS-B, OpenWebRX and the APRS feed share one detection
        # layer instead of each re-installing it; owns the blacklist teardown.
        "rtl-sdr": SE.FeatureSpec(
            key="rtl-sdr",
            dependencies=[],
            install_fn=lambda: _setup_run_script(repo_root, "features/rtl-sdr/install-rtl-sdr.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "features/rtl-sdr/rtl_sdr.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        # APRS SDR audio feed (rtl_fm -> UDP -> GrayWolf sdr_udp). Depends on the
        # rtl-sdr tools; installed with --no-enable (off by default, started from
        # the dashboard). In setup.html its checkbox sits before adsb so a single
        # setup run probes the feed's dongle before dump1090-fa claims one.
        "rtl-sdr-feed": SE.FeatureSpec(
            key="rtl-sdr-feed",
            dependencies=["rtl-sdr"],
            install_fn=lambda: _setup_run_script(repo_root, "services/rtl-feed/install.py", ["--no-enable"]),
            removal_record_fn=lambda: _removal_record(repo_root, "services/rtl-feed/common/feed.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "gps": SE.FeatureSpec(
            key="gps",
            dependencies=[],
            install_fn=lambda: _setup_run_script(repo_root, "features/gps/install-gps.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "features.gps.gps"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        # UART/GPIO GPS HAT (Waveshare L76X, on ttyS0/serial0). Separate from the
        # generic USB 'gps' feature: it enables the hardware UART + frees the
        # serial console (reboot-required, exit 10) and points gpsd at serial0.
        # Mutually exclusive with 'gps' — both drive gpsd (the UI unchecks the
        # other; install-gps-l76x.py's check_exclusive is the runtime backstop).
        "gps-l76x": SE.FeatureSpec(
            key="gps-l76x",
            dependencies=[],
            install_fn=lambda: _setup_run_script(repo_root, "features/gps-L76X/install-gps-l76x.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "features/gps-L76X/gps_l76x.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        # NW Digital Radio DRAWS HAT — on-board GPS/PPS on the SC16IS752's
        # /dev/ttySC0. Third member of the gpsd mutual-exclusion group with
        # 'gps' (USB) and 'gps-l76x' (UART): all three repoint gpsd, so the UI
        # unchecks the others and install-draws-gps.py's check_exclusive is the
        # runtime backstop. No dependency on draws-audio — the shared
        # `dtoverlay=draws` line is written idempotently by whichever runs first.
        "draws-gps": SE.FeatureSpec(
            key="draws-gps",
            dependencies=[],
            install_fn=lambda: _setup_run_script(repo_root, "features/draws-gps/install-draws-gps.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "features/draws-gps/draws_gps.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        # DRAWS radio audio: TLV320AIC3204 mixer routing for BOTH mDin6 ports
        # (left/Winlink, right/APRS) plus the udev rule that keeps PipeWire off
        # the codec. Mutually exclusive with 'dra-pi-rx-led' in the UI — two
        # radio-interface HATs cannot both own the 40-pin audio/PTT lines.
        "draws-audio": SE.FeatureSpec(
            key="draws-audio",
            dependencies=[],
            install_fn=lambda: _setup_run_script(repo_root, "features/draws-audio/install-draws-audio.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "features/draws-audio/draws_audio.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "dra-pi-rx-led": SE.FeatureSpec(
            key="dra-pi-rx-led",
            dependencies=["graywolf"],
            install_fn=lambda: _setup_run_chain(repo_root, [
                {"script": "features/dra-audio-interface/enable-dra-pi.py"},
                {"script": "features/dra-audio-interface/enable-dra-rx-led.py"},
            ]),
            removal_record_fn=lambda: _removal_record(repo_root, "features/dra-audio-interface/enable-dra-pi.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "rtc": SE.FeatureSpec(
            key="rtc",
            dependencies=[],
            install_fn=lambda: _setup_run_script(repo_root, "features/rtc-hat/enable-rtc.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "features/rtc-hat/enable-rtc.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        # Second RTC board, not a variant of the first: the BigTreeTech 7" panel's
        # PCF8563 hangs off the DSI ribbon's i2c-10 bus, so it needs a different
        # overlay and a different config.txt block than the Witty Pi's GPIO-bus
        # DS3231. Both may be ticked on one box; each owns only its own block.
        "rtc-raspad": SE.FeatureSpec(
            key="rtc-raspad",
            dependencies=[],
            install_fn=lambda: _setup_run_script(repo_root, "features/rtc-raspad/enable-rtc.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "features/rtc-raspad/enable-rtc.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "pi-headless": SE.FeatureSpec(
            key="pi-headless",
            dependencies=["server"],
            # Headless Pi (no monitor/kiosk): still needs the plain oasis.service
            # systemd unit enabled so the server actually starts on boot. This
            # used to be a no-op (_setup_record_only) which left the checkbox
            # cosmetic — the service was never installed, so the server never
            # came back up after a reboot.
            install_fn=lambda: _setup_run_script(repo_root, "scripts/enable-autostart-pi.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "scripts/enable-autostart-pi.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            # Removed LAST (highest teardown_priority) and only disables autostart
            # — the running oasis.service stays alive until the reboot this signals
            # — so tearing it down never kills the server driving the uninstall.
            requires_reboot=True,
            teardown_priority=30,
            privileged=True,
        ),
        "pi-local-monitor": SE.FeatureSpec(
            key="pi-local-monitor",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "scripts/enable-autostart-pi.py", ["--with-browser"]),
            removal_record_fn=lambda: _removal_record(repo_root, "scripts/enable-autostart-pi.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            requires_reboot=True,
            privileged=True,
        ),
        "pi-oasis-dashboard": SE.FeatureSpec(
            key="pi-oasis-dashboard",
            dependencies=["server"],
            install_fn=lambda: _setup_dashboard_install_fn(repo_root, payload),
            removal_record_fn=lambda: _removal_record(repo_root, "oasis-dashboard/uninstall.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            requires_reboot=True,
            privileged=True,
        ),
        "cm4stack": SE.FeatureSpec(
            key="cm4stack",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "features/cm4stack/install-cm4stack.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "features/cm4stack/install-cm4stack.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            requires_reboot=True,
            privileged=True,
        ),
        "rgb-cooling-hat": SE.FeatureSpec(
            key="rgb-cooling-hat",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "features/rgb-cooling-hat/install-rgb-cooling-hat.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "features/rgb-cooling-hat/install-rgb-cooling-hat.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "argon-fan": SE.FeatureSpec(
            key="argon-fan",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "features/argon-fan/install-argon-fan.py"),
            removal_record_fn=lambda: _removal_record(repo_root, "features/argon-fan/install-argon-fan.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "fcc": SE.FeatureSpec(
            key="fcc",
            dependencies=["server"],
            # Downloads ~160 MB from the FCC then builds 3 binary-search
            # indexes over it — the default 300s script timeout can be too
            # tight on a slow connection or a low-power Pi doing the index build,
            # so give it a generous 20 minutes.
            install_fn=lambda: _setup_run_script(repo_root, "services/fcc_database/install.py", timeout=1200),
            removal_record_fn=lambda: _removal_record(repo_root, "services.fcc_database.common.fcc_database"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "wikipedia": SE.FeatureSpec(
            key="wikipedia",
            dependencies=["kiwix"],
            # Automation runs headless (no TTY), so pass an explicit --edition —
            # otherwise download-wikipedia.py falls through to its interactive
            # picker and input() raises EOFError. "simple-mini" is the smallest
            # Simple-English edition (~447 MB), a sane default for a small SD
            # card; operators can fetch a larger one later from the CLI. The
            # generous timeout covers the download on a slow field link (the
            # default 300s can't finish ~447 MB on the Pi's connection).
            install_fn=lambda: _setup_run_script(
                repo_root, "services/kiwix/download-wikipedia.py",
                ["--edition", "top-mini"], timeout=1800),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "repeaterbook": SE.FeatureSpec(
            key="repeaterbook",
            dependencies=[],
            install_fn=lambda: _setup_record_only("repeaterbook"),
            removal_record_fn=lambda: _repeaterbook_removal_record(repo_root),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "forms": SE.FeatureSpec(
            key="forms",
            dependencies=[],
            install_fn=lambda: _setup_record_only("forms"),
            removal_record_fn=lambda: _forms_removal_record(repo_root),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
    }
