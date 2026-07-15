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

import os
import subprocess
import sys

from common import setup_engine as SE
from common import server as SERVER_SETUP


def _setup_run_script(repo_root, script_rel, args=None, timeout=900):
    args = args or []
    script = os.path.join(repo_root, script_rel)
    if not os.path.exists(script):
        return {
            "ok": False,
            "reason_code": "MISSING_SCRIPT",
            "reason_text": f"missing script: {script_rel}",
        }
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
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "script failed").strip()
        low = err.lower()
        reason_code = "INSTALL_FAILED"
        if any(tok in low for tok in [
            "temporary failure in name resolution",
            "failed to fetch",
            "network is unreachable",
            "could not resolve",
            "could not reach the internet",
            "could not download the flightaware repo signing key",
        ]):
            reason_code = "INTERNET_REQUIRED"
        elif "command not found" in low and ("curl" in low or "gpg" in low):
            reason_code = "MISSING_TOOL"
        return {
            "ok": False,
            "reason_code": reason_code,
            "reason_text": err.splitlines()[-1][:300] if err else f"script failed: {script_rel}",
            "stderr_tail": (r.stderr or "").strip()[-300:] or None,
            "stdout_tail": (r.stdout or "").strip()[-300:] or None,
        }
    return {"ok": True}


def _setup_run_chain(repo_root, steps):
    for step in steps:
        res = _setup_run_script(repo_root, step.get("script"), step.get("args"), timeout=step.get("timeout", 900))
        if not res.get("ok"):
            return res
    return {"ok": True}


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
    return _setup_run_script(repo_root, "scripts/install-winlink.py", args)


# Feature keys whose install_fn needs real root. Kept as one explicit list right
# next to build_registry() (rather than inferred) so it's easy to audit — every
# key here MUST have a corresponding install_fn that is safe to run as root
# unattended (no interactive prompts beyond `sudo`, which is a no-op when the
# caller is already root).
PRIVILEGED_FEATURES = {
    "webssh", "service-controls", "ap-fallback", "graywolf", "winlink", "kiwix",
    "openwebrx", "adsb", "rtl-sdr-feed", "gps", "dra-pi-rx-led", "rtc",
    "pi-local-monitor", "pi-small-screen-7", "cm4stack",
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
            install_fn=lambda: _setup_run_script(repo_root, "scripts/install-webssh.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "service-controls": SE.FeatureSpec(
            key="service-controls",
            dependencies=["server"],
            install_fn=lambda: _setup_service_controls_install(repo_root),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "ap-fallback": SE.FeatureSpec(
            key="ap-fallback",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "scripts/enable-ap-fallback.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "graywolf": SE.FeatureSpec(
            key="graywolf",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "scripts/install-graywolf.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "winlink": SE.FeatureSpec(
            key="winlink",
            dependencies=["server"],
            install_fn=lambda: _setup_winlink_install_fn(repo_root, payload),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "kiwix": SE.FeatureSpec(
            key="kiwix",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "scripts/install-kiwix.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "openwebrx": SE.FeatureSpec(
            key="openwebrx",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "scripts/install-openwebrx.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "adsb": SE.FeatureSpec(
            key="adsb",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "services/adsb/install.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "rtl-sdr-feed": SE.FeatureSpec(
            key="rtl-sdr-feed",
            dependencies=[],
            install_fn=lambda: _setup_run_chain(repo_root, [
                {"script": "features/rtl-sdr/install-rtl-sdr.py"},
                {"script": "features/rtl-sdr/enable-rtl-sdr.py"},
            ]),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "gps": SE.FeatureSpec(
            key="gps",
            dependencies=[],
            install_fn=lambda: _setup_run_script(repo_root, "features/gps/install-gps.py"),
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
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "rtc": SE.FeatureSpec(
            key="rtc",
            dependencies=[],
            install_fn=lambda: _setup_run_script(repo_root, "features/rtc-hat/enable-rtc.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            privileged=True,
        ),
        "pi-headless": SE.FeatureSpec(
            key="pi-headless",
            dependencies=["server"],
            install_fn=lambda: _setup_record_only("pi-headless"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "pi-local-monitor": SE.FeatureSpec(
            key="pi-local-monitor",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "scripts/enable-autostart-pi.py", ["--with-browser"]),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            requires_reboot=True,
            privileged=True,
        ),
        "pi-small-screen-7": SE.FeatureSpec(
            key="pi-small-screen-7",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "scripts/enable-autostart-pi.py", ["--7inch"]),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            requires_reboot=True,
            privileged=True,
        ),
        "cm4stack": SE.FeatureSpec(
            key="cm4stack",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "displays/cm4stack/install-cm4stack.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            requires_reboot=True,
            privileged=True,
        ),
        "pi-e-ink": SE.FeatureSpec(
            key="pi-e-ink",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "displays/e-ink/install-e-ink.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "fcc": SE.FeatureSpec(
            key="fcc",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script(repo_root, "scripts/install-fcc-database.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "wikipedia": SE.FeatureSpec(
            key="wikipedia",
            dependencies=["kiwix"],
            install_fn=lambda: _setup_run_script(repo_root, "scripts/download-wikipedia.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "repeaterbook": SE.FeatureSpec(
            key="repeaterbook",
            dependencies=[],
            install_fn=lambda: _setup_record_only("repeaterbook"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "forms": SE.FeatureSpec(
            key="forms",
            dependencies=[],
            install_fn=lambda: _setup_record_only("forms"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
    }
