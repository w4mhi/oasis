"""
APRS frequency route blueprint — the Setup page's frequency selector.

GET  /api/aprs/frequency  → the operator's chosen frequency, the region presets
                            (configuration/aprs_frequencies.json), and whether
                            the RTL-SDR feed service is installed.
POST /api/aprs/frequency  → validate + persist the chosen frequency to
                            configuration/station.json (unprivileged; the web
                            layer owns that file), then, when the feed unit
                            exists, hand off to the privileged applier
                            (features/rtl-sdr/set-aprs-freq.py via `sudo -n`,
                            allow-listed by scripts/enable-service-controls.py)
                            to rewrite the unit's ExecStart and restart it.

Persist-then-apply mirrors the hardware assignment flow: the choice sticks in
station.json even when the feed isn't installed yet (enable-rtl-sdr.py reads it
back as its default), so re-enabling the feed keeps the operator on-frequency.
"""

import datetime
import json
import os
import subprocess
import sys

from flask import Blueprint, jsonify, request

import appconfig
from common import config_paths

SUITE_ROOT = appconfig.SUITE_ROOT
FEED_UNIT_PATH = "/etc/systemd/system/aprs-sdr-feed.service"
DEFAULT_FREQ = "144.390M"

# Reuse the SDR tooling's own validator so the dashboard and CLI agree on what a
# valid rtl_fm frequency is.
_RTL_DIR = os.path.join(SUITE_ROOT, "features", "rtl-sdr")
if _RTL_DIR not in sys.path:
    sys.path.insert(0, _RTL_DIR)
from sdr_tune import normalize_freq  # noqa: E402

bp = Blueprint("aprs_freq", __name__)


def _load_presets():
    """Region presets from the tracked config file, normalized to {label, freq}.
    Operator-editable — a missing/garbled file simply yields no presets (the UI
    still offers Custom)."""
    try:
        with open(config_paths.aprs_frequencies_json(SUITE_ROOT), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return [
            {"label": str(e.get("label", "")).strip(), "freq": str(e.get("freq", "")).strip()}
            for e in (data or [])
            if isinstance(e, dict) and str(e.get("freq", "")).strip()
        ]
    except Exception:
        return []


def _read_station():
    try:
        with open(config_paths.station_json(SUITE_ROOT), "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _persist_freq(freq):
    """Write aprs_freq into station.json, preserving every other key."""
    path = config_paths.station_json(SUITE_ROOT)
    os.makedirs(config_paths.config_dir(SUITE_ROOT), exist_ok=True)
    body = _read_station()
    body["aprs_freq"] = freq
    body["updated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=2)
        fh.write("\n")


def _apply_freq(freq):
    """Hand off to the privileged applier. Returns (returncode, combined output)."""
    venv_python = os.path.join(SUITE_ROOT, ".venv", "bin", "python3")
    script = os.path.join(_RTL_DIR, "set-aprs-freq.py")
    try:
        r = subprocess.run(["sudo", "-n", venv_python, script, freq],
                           capture_output=True, text=True, timeout=30)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as exc:
        return 1, str(exc)


@bp.route("/api/aprs/frequency")
def api_get_aprs_frequency():
    return jsonify({
        "ok": True,
        "current": (_read_station().get("aprs_freq") or DEFAULT_FREQ),
        "presets": _load_presets(),
        "feed_installed": os.path.exists(FEED_UNIT_PATH),
    })


@bp.route("/api/aprs/frequency", methods=["POST"])
def api_set_aprs_frequency():
    # CSRF guard: a cross-origin page can't set this custom header without a
    # preflight this endpoint never grants (same pattern as /api/service).
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    freq = normalize_freq(data.get("freq") or "")
    if not freq:
        return jsonify({"ok": False, "error": "invalid frequency"}), 400

    try:
        _persist_freq(freq)
    except OSError as exc:
        return jsonify({"ok": False, "error": f"could not save frequency: {exc}"}), 500

    # Not installed (or not Linux): the choice is saved and enable-rtl-sdr.py will
    # pick it up — nothing to restart now.
    if not os.path.exists(FEED_UNIT_PATH) or sys.platform != "linux":
        return jsonify({"ok": True, "freq": freq, "applied": False,
                        "message": f"Saved {freq} — will apply when the APRS SDR feed is enabled."})

    code, out = _apply_freq(freq)
    if code == 0:
        return jsonify({"ok": True, "freq": freq, "applied": True,
                        "message": f"Applied — APRS feed restarted at {freq}."})
    return jsonify({"ok": False, "freq": freq, "applied": False,
                    "error": f"Saved {freq}, but applying it failed (code {code}): {out[:200]}"}), 500
