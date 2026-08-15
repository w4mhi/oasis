"""Shared diagnostics framework: check registry + run_all() aggregation.

This module is the single "brain" behind OASIS station health reporting.
It defines:

  - ``Check``       -- static metadata + callable for one diagnostic check.
  - ``_result()``   -- helper that builds the check-result dict every
                        ``check_*`` function must return.
  - ``REGISTRY``    -- the list of registered ``Check`` instances. Real
                        checks are registered here in later tasks; this
                        module starts with an empty registry.
  - ``run_all()``   -- executes every applicable check, groups results,
                        rolls them up into per-capability verdicts, and
                        picks the single highest-impact failure ("fix now").

Offline-first / stdlib-only: no third-party imports.

Adding or removing a check is registry-only -- ``run_all`` iterates
``REGISTRY`` and never needs to change.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from collections import namedtuple

from common import config_paths, hardware, hardware_detect

# ---------------------------------------------------------------------------
# Registry entry
# ---------------------------------------------------------------------------

# Check(id, group, label, capability, critical, tier, fn)
#   group      in {"CORE", "HARDWARE", "SERVICES", "DATA", "SYSTEM"}
#   capability in {"APRS_RX", "WINLINK", "POSITION", "POWER", "ACCESS", "REFERENCE"}
#   critical   : bool  -- does a fail here take the capability down?
#   tier       in {"v1", "backlog"}
#   fn         : callable(ctx) -> check-result dict (ctx carries host/port)
Check = namedtuple("Check", ["id", "group", "label", "capability", "critical", "tier", "fn"])

# Real checks are registered here starting in Task 2. This module's own
# tests exercise run_all() entirely via fake checks that swap REGISTRY.
REGISTRY: list = []

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GROUP_ORDER = ["CORE", "HARDWARE", "SERVICES", "SYSTEM", "DATA", "UPDATES"]

# Capability -> member checks (v1). Used to render capability tiles and to
# know which capability a check id "belongs" to when building the rollup.
# (id -> {"label": ..., "members": [...]})
CAPABILITIES = {
    "ACCESS": {
        "label": "Core / Access",
        "members": ["server", "station_identity", "webssh"],
    },
    "APRS_RX": {
        "label": "APRS Receive",
        "members": ["rtl_sdr", "digirig", "dra_pi", "graywolf", "graywolf_api", "aprs_feed"],
    },
    "WINLINK": {
        "label": "Winlink",
        "members": ["pat"],
    },
    "POSITION": {
        "label": "Position / GPS",
        "members": ["gps"],
    },
    "POWER": {
        "label": "Power / Health",
        "members": ["power", "temp", "cpu", "disk", "cooling_hat"],
    },
    "REFERENCE": {
        "label": "Reference Data",
        "members": ["fcc", "repeaterbook", "kiwix", "forms", "maps"],
    },
    "UPDATES": {
        "label": "Data Updates",
        "members": ["update_tle", "update_satnogs", "update_fcc",
                    "update_repeaterbook"],
    },
}

_STATUS_RANK = {"fail": 2, "warn": 1, "ok": 0}


def _result(id, group, label, status, badge, detail, breaks=None, fix=None):
    """Build the check-result dict every check function returns.

    {
      "id": "server", "group": "CORE", "label": "OASIS Server",
      "status": "ok",            # "ok" | "warn" | "fail"
      "badge": "RUNNING",        # short uppercase word
      "detail": "Serving on :8083",
      "breaks": None,            # plain-language consequence when status=="fail"
      "fix": None,               # Setup deep-link URL, or None
    }
    """
    return {
        "id": id,
        "group": group,
        "label": label,
        "status": status,
        "badge": badge,
        "detail": detail,
        "breaks": breaks,
        "fix": fix,
    }


def _run_one(check, ctx):
    """Run a single check, converting any exception into a fail/ERROR result.

    This keeps one bad check from sinking the whole sweep.
    """
    try:
        return check.fn(ctx)
    except Exception as exc:  # noqa: BLE001 - deliberately broad; see docstring
        return _result(
            check.id,
            check.group,
            check.label,
            "fail",
            "ERROR",
            f"Check raised an exception: {exc}",
            breaks="This check could not complete, so its status is unknown.",
            fix=None,
        )


def _rollup_capability(cap_id, results_by_id, members):
    """Deterministic, pure capability rollup rule.

    - fail if any *critical* child check is fail.
    - else warn if any child (critical or not) is fail or warn (a
      non-critical fail degrades to warn at capability level).
    - else ok.
    """
    present = [(m, results_by_id[m]) for m in members if m in results_by_id]
    if not present:
        return "ok"

    any_critical_fail = any(
        r["status"] == "fail" and r.get("_critical") for _, r in present
    )
    if any_critical_fail:
        return "fail"

    any_fail_or_warn = any(r["status"] in ("fail", "warn") for _, r in present)
    if any_fail_or_warn:
        return "warn"

    return "ok"


def _fix_now(results, critical_by_id):
    """Pick the single highest-impact fail.

    Among all fail checks, sort by (critical desc, GROUP_ORDER index) and
    return the first. None if there are no fails.
    """
    fails = [r for r in results if r["status"] == "fail"]
    if not fails:
        return None

    def sort_key(r):
        is_critical = critical_by_id.get(r["id"], False)
        try:
            group_idx = GROUP_ORDER.index(r["group"])
        except ValueError:
            group_idx = len(GROUP_ORDER)
        return (0 if is_critical else 1, group_idx)

    return sorted(fails, key=sort_key)[0]


def run_all(host, port, include_backlog=False):
    """Run every applicable registered check and aggregate the results.

    Returns:
    {
      "ran_at": "2026-07-17T21:53:07Z",
      "summary": {"fail": 2, "warn": 1, "ok": 17},
      "capabilities": [
        {"id": "APRS_RX", "label": "APRS Receive", "status": "ok",
         "checks": ["rtl_sdr", "graywolf", "graywolf_api", "aprs_feed"]},
        ...
      ],
      "fix_now": <check-result dict of the single highest-impact fail> | None,
      "groups": [{"name": "CORE", "checks": [<check-result dicts>, ...]}, ...],
    }
    """
    ctx = {"host": host, "port": port}

    checks = [c for c in REGISTRY if include_backlog or c.tier != "backlog"]

    results = []
    critical_by_id = {}
    capability_by_id = {}
    for check in checks:
        result = _run_one(check, ctx)
        results.append(result)
        critical_by_id[check.id] = check.critical
        capability_by_id[check.id] = check.capability

    summary = {"fail": 0, "warn": 0, "ok": 0}
    for r in results:
        if r["status"] in summary:
            summary[r["status"]] += 1

    # Build results_by_id annotated with "_critical" so the rollup can see
    # criticality without a second lookup pass; strip it back out before
    # returning results to the caller.
    results_by_id = {}
    for r in results:
        annotated = dict(r)
        annotated["_critical"] = critical_by_id.get(r["id"], False)
        results_by_id[r["id"]] = annotated

    # Which checks (by id) actually ran, grouped per capability -- this may
    # include ids not in the static CAPABILITIES member table (e.g. tests'
    # fake checks), so build capability groupings from what actually ran
    # rather than solely from the static table.
    capabilities = []
    seen_cap_ids = set()

    def _members_for(cap_id):
        static_members = CAPABILITIES.get(cap_id, {}).get("members", [])
        # Preserve static ordering, then append any ids that ran under
        # this capability but aren't in the static table (keeps this
        # module test-fixture friendly without a real check catalog yet).
        ran_ids = [r["id"] for r in results if capability_by_id.get(r["id"]) == cap_id]
        ordered = [m for m in static_members if m in results_by_id and capability_by_id.get(m) == cap_id]
        extra = [i for i in ran_ids if i not in ordered]
        return ordered + extra

    # Emit capability tiles in CAPABILITIES table order first...
    for cap_id, meta in CAPABILITIES.items():
        members = _members_for(cap_id)
        if not members:
            continue
        status = _rollup_capability(cap_id, results_by_id, members)
        capabilities.append({
            "id": cap_id,
            "label": meta["label"],
            "status": status,
            "checks": members,
        })
        seen_cap_ids.add(cap_id)

    # ...then any capability ids that appear only via registered checks
    # (not in the static table), so nothing silently vanishes.
    extra_cap_ids = sorted({cid for cid in capability_by_id.values() if cid not in seen_cap_ids})
    for cap_id in extra_cap_ids:
        members = [r["id"] for r in results if capability_by_id.get(r["id"]) == cap_id]
        status = _rollup_capability(cap_id, results_by_id, members)
        capabilities.append({
            "id": cap_id,
            "label": cap_id,
            "status": status,
            "checks": members,
        })

    fix_now = _fix_now(results, critical_by_id)

    groups = []
    for group_name in GROUP_ORDER:
        group_checks = [r for r in results if r["group"] == group_name]
        # Failures float to the top within each group.
        group_checks = sorted(group_checks, key=lambda r: _STATUS_RANK.get(r["status"], -1), reverse=True)
        groups.append({"name": group_name, "checks": group_checks})

    # Any group not in GROUP_ORDER (shouldn't happen with real checks, but
    # keep run_all defensive/registry-driven for fixtures/tests) is appended
    # so no result silently disappears.
    known_groups = set(GROUP_ORDER)
    extra_group_names = sorted({r["group"] for r in results if r["group"] not in known_groups})
    for group_name in extra_group_names:
        group_checks = [r for r in results if r["group"] == group_name]
        group_checks = sorted(group_checks, key=lambda r: _STATUS_RANK.get(r["status"], -1), reverse=True)
        groups.append({"name": group_name, "checks": group_checks})

    ran_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "ran_at": ran_at,
        "summary": summary,
        "capabilities": capabilities,
        "fix_now": fix_now,
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# Real check paths / constants (moved from scripts/doctor.py)
# ---------------------------------------------------------------------------

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_MODULE_DIR)

FCC_INDEX = os.path.join(REPO_ROOT, "services", "fcc_database", "data", "EN.idx")
MAPS_DIR = os.path.join(REPO_ROOT, "maps")
WINLINK_DIR = os.path.join(REPO_ROOT, "server", "winlink")

# RepeaterBook: a record_only feature (setup-oasis.py / common/setup_registry.py's
# "repeaterbook" spec) -- there's no installer, the operator drops their own
# export (copyrighted, can't be vendored) at this path.
REPEATERBOOK_CSV = os.path.join(REPO_ROOT, "static", "repeaterbook", "repeaterbook.csv")

# ICS Forms OASIS ships (docs/SETUP.md "ICS Forms"): four self-contained HTML
# forms with PDF export, no server required. This is what the app's own
# "forms" feature key (setup-oasis.py / common/setup_registry.py's record_only
# "forms" spec) actually refers to -- the codebase has no separate Winlink
# "Standard Templates" library to check instead.
ICS_FORMS = [
    ("ics-205", "ICS 205"),
    ("ics-213", "ICS 213"),
    ("ics-214", "ICS 214"),
    ("ics-309", "ICS 309"),
]

# Mirrors services/kiwix/common/kiwix.py's DEFAULT_ZIM_DIR without importing
# that module (it pulls in `pwd`, Unix-only, at import time, plus common.oasis_lib).
# Best-effort only: a custom --zim-dir install won't be reflected here. Used
# only to freshen the kiwix check's detail line, never to change its status.
KIWIX_ZIM_DIR = os.path.expanduser(os.path.join("~", "oasis-offline", "zim"))

# APRS: in normal RF conditions a station should be heard again within this
# window; beyond it the feed is reachable but not actively decoding traffic.
APRS_RECENT_SECONDS = 600

# Default port (matches PORTS in setup.html)
DEFAULT_PORT = 8083

# Live port table -- updated from /server-ports.json if the server is up.
PORTS = {
    "flask":    8083,
    "graywolf": 8080,
    "kiwix":    8081,
    "winlink":  8082,
    "aprs_api": 8085,
    "webssh":   7681,
}

# GrayWolf offline tiles directory (Pi OS default installation path)
GRAYWOLF_TILES_DIR = "/var/lib/graywolf/tiles"

# RTL-SDR DVB driver blacklist written by features/rtl-sdr/install-rtl-sdr.py
RTL_BLACKLIST = "/etc/modprobe.d/rtlsdr-blacklist.conf"

# The Setup Orchestrator page is served statically at /server/system/setup.html
# (SUITE_ROOT = repo root; index.html links it as server/system/setup.html).
# Section anchors (#station, #services, #content, #radio) are added to setup.html
# in the Diagnostic rollout; until then a bare/base link still lands on the page.
_SETUP_URL = "/server/system/setup.html"


# ---------------------------------------------------------------------------
# Check signal helpers (moved from scripts/doctor.py, verbatim)
# ---------------------------------------------------------------------------

def _probe_port(host, port, timeout=3.0):
    """True if a TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError, socket.timeout):
        return False


def _which_binary(name):
    """Locate a binary on PATH + standard dirs. Returns path string or None."""
    path = shutil.which(name)
    if path:
        return path
    for d in ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin",
              "/usr/bin", "/sbin", "/bin"):
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _svc_status(name):
    """Query systemd for a service unit. Always returns a dict — never raises."""
    if sys.platform != "linux":
        return {"active": "n/a", "enabled": "n/a", "installed": False}

    def _q(verb):
        try:
            r = subprocess.run(
                ["systemctl", verb, f"{name}.service"],
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip()
        except Exception:
            return ""

    active  = _q("is-active")
    enabled = _q("is-enabled")
    return {
        "active":    active  or "unknown",
        "enabled":   enabled or "not-found",
        # Absent units make `systemctl is-enabled` print "not-found" (Pi OS
        # Trixie / systemd ≥ 254, on stdout) or nothing (older) — both mean not
        # installed. Don't rely on bool(enabled): "not-found" is a truthy string.
        "installed": enabled not in ("", "not-found"),
    }


def _svc_word(svc):
    """Compact 'active, enabled' description string for a _svc_status result."""
    en = f", {svc['enabled']}" if svc["enabled"] not in ("not-found", "n/a", "") else ""
    return f"{svc['active']}{en}"


def _http_get(url, timeout=5):
    """GET url and JSON-decode. Returns (ok:bool, data:dict|None). Never raises."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "OASIS-Doctor/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        try:
            return True, json.loads(body)
        except Exception:
            return True, {}
    except Exception:
        return False, None


def _data_age(path):
    """mtime -> human freshness string ("41 days old" / "3h old" / "12m old"),
    or None if the path is missing/unreadable. Never raises. Purely cosmetic
    (folded into a check's detail text) -- never drives status."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    age_sec = max(0.0, time.time() - mtime)
    days = age_sec / 86400
    if days >= 1:
        d = int(days)
        return f"{d} day{'s' if d != 1 else ''} old"
    hours = age_sec / 3600
    if hours >= 1:
        return f"{int(hours)}h old"
    minutes = max(1, int(age_sec / 60))
    return f"{minutes}m old"


def _kiwix_zim_age():
    """Best-effort freshness of the newest .zim file in KIWIX_ZIM_DIR. None
    if the dir/files are absent, unreadable, or a custom --zim-dir install
    was used (see KIWIX_ZIM_DIR's docstring). Never raises."""
    try:
        if not os.path.isdir(KIWIX_ZIM_DIR):
            return None
        zims = [os.path.join(KIWIX_ZIM_DIR, f)
                for f in os.listdir(KIWIX_ZIM_DIR) if f.endswith(".zim")]
    except OSError:
        return None
    if not zims:
        return None
    try:
        newest = max(zims, key=os.path.getmtime)
    except OSError:
        return None
    return _data_age(newest)


def _parse_ts(val):
    """Best-effort parse of a last_heard/timestamp value into an aware UTC
    datetime. Accepts:

      - epoch numbers (int/float), or a numeric string of one;
      - GrayWolf's *real* last_heard format --
        'YYYY-MM-DD HH:MM:SS.fffffffff±HH:MM' (a space separator, nanosecond
        -- not microsecond -- fractional precision, and a UTC offset). This is
        what services/aprs actually stores, not simplified SQLite-text;
      - plain ISO 8601 ('T'-separated, with an offset or trailing 'Z').

    Uses a normalize-then-fromisoformat technique:
    swap 'Z' for '+00:00', turn the first space into 'T', then clamp any
    fractional-second digits beyond microseconds so fromisoformat() (which
    only accepts up to 6) can parse it. A timezone-naive result is treated as
    UTC. Returns None on anything unparseable. Never raises."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(val, tz=datetime.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(val, str):
        return None
    text = val.strip()
    if not text:
        return None

    # Epoch given as a numeric string (e.g. "1700000000" or "1700000000.5").
    try:
        return datetime.datetime.fromtimestamp(float(text), tz=datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        pass

    normalized = text.replace("Z", "+00:00").replace(" ", "T", 1)
    normalized = re.sub(r"(\.\d{6})\d+", r"\1", normalized)  # ns -> us
    try:
        dt = datetime.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _aprs_feed_freq():
    """Best-effort tuned frequency, read from the running aprs-sdr-feed
    unit's ExecStart line -- specifically rtl_fm's `-f <freq>` flag (see
    services/rtl-feed/common/feed.py's build_unit(), which embeds
    "rtl_fm -f <freq> -M fm ..." verbatim). Returns the raw token (e.g.
    "144.390M") or None on non-Linux, missing systemctl, an absent/
    unrecognized unit, or any other failure. Never raises."""
    if sys.platform != "linux":
        return None
    try:
        r = subprocess.run(
            ["systemctl", "cat", "aprs-sdr-feed.service"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    m = re.search(r"ExecStart=.*?\s-f\s+(\S+)", r.stdout)
    return m.group(1) if m else None


def _fmt_freq(freq):
    """"144.390M" -> "144.390 MHz"; None -> None."""
    if not freq:
        return None
    txt = freq.strip()
    if txt.upper().endswith("M"):
        txt = txt[:-1]
    return f"{txt} MHz"


# ---------------------------------------------------------------------------
# Check functions (moved from scripts/doctor.py)
#
# Each check_*(ctx) takes ctx={"host":..., "port":...} and returns a
# _result() dict. breaks/fix are populated only on status=="fail" (the
# framework convention: fix is "where to go to fix this", shown when there's
# something broken to fix).
# ---------------------------------------------------------------------------

def check_server(ctx):
    host, port = ctx["host"], ctx["port"]
    up = _probe_port(host, port)
    if not up:
        return _result(
            "server", "CORE", "OASIS Server", "fail", "DOWN",
            (
                f"No response on {host}:{port}. Is OASIS running?\n"
                "     Start with: ./scripts/start-server.sh"
            ),
            breaks="The OASIS web interface is completely unreachable — no station functions work.",
            fix=_SETUP_URL,
        )

    ok, info = _http_get(f"http://{host}:{port}/api/server-info")
    if ok and info:
        wsgi  = info.get("wsgi", "unknown")
        ver   = info.get("wsgi_version", "?")
        oasis = info.get("version", "unknown")
        dev   = wsgi != "gunicorn"
        svc   = _svc_status("oasis")
        svc_t = f", service {_svc_word(svc)}" if svc["installed"] else ""
        ver_t = f"OASIS v{oasis} · " if oasis and oasis != "unknown" else ""
        detail = f"{ver_t}Serving on :{port} via {wsgi} v{ver}{svc_t}."
        if dev:
            detail += "\n     For always-on use, run under gunicorn (see docs/SETUP.md)."
        status = "warn" if dev else "ok"
        badge  = "DEV SERVER" if dev else "RUNNING"
        return _result("server", "CORE", "OASIS Server", status, badge, detail)

    # Listening but /api/server-info unavailable — still counts as up.
    return _result("server", "CORE", "OASIS Server", "ok", "RUNNING", f"Listening on :{port}.")


def check_disk(ctx):
    for mount, label in [
        ("/mnt/ssd", "SSD"),
        ("/mnt/emmc", "eMMC"),
        ("/", "System"),
        ("C:\\", "System"),
    ]:
        try:
            d        = shutil.disk_usage(mount)
            total_gb = round(d.total / 1e9, 1)
            free_gb  = round(d.free  / 1e9, 1)
            pct      = round(d.used  / d.total * 100)
            detail   = f"{free_gb} GB free of {total_gb} GB on {label} ({pct}% used)."
            if free_gb < 1:
                return _result(
                    "disk", "SYSTEM", "Disk Space", "fail", "CRITICAL",
                    detail + "\n     Free space now — maps / Wikipedia / FCC may fail to write.",
                    breaks="Maps, Wikipedia, and FCC downloads may fail to write; the station could stop logging.",
                    fix=_SETUP_URL,
                )
            if free_gb < 2 or pct >= 92:
                return _result(
                    "disk", "SYSTEM", "Disk Space", "warn", "LOW",
                    detail + "\n     Running low; large downloads (maps, ZIM, FCC) may not fit.",
                )
            return _result("disk", "SYSTEM", "Disk Space", "ok", "OK", detail)
        except (FileNotFoundError, OSError):
            continue
    return _result("disk", "SYSTEM", "Disk Space", "warn", "N/A", "Could not read disk usage.")


def check_fcc(ctx):
    if not os.path.isfile(FCC_INDEX):
        return _result(
            "fcc", "DATA", "FCC Callsign Database", "fail", "NOT BUILT",
            (
                "Index not found at services/fcc_database/data/EN.idx.\n"
                "     Run: python3 services/fcc_database/install.py"
            ),
            breaks="Callsign lookups will not work anywhere in the suite.",
            fix=_SETUP_URL,
        )
    try:
        count = sum(1 for _ in open(FCC_INDEX, "rb"))
    except OSError:
        count = 0
    age = _data_age(FCC_INDEX)
    detail = f"Callsign index built ({count:,} entries)."
    if age:
        detail += f" · {age}"
    return _result("fcc", "DATA", "FCC Callsign Database", "ok", "READY", detail)


def check_maps(ctx):
    host, port = ctx["host"], ctx["port"]

    # Local .pmtiles in the suite maps/ dir
    local_tiles = []
    if os.path.isdir(MAPS_DIR):
        local_tiles = [
            f.replace(".pmtiles", "")
            for f in os.listdir(MAPS_DIR)
            if f.endswith(".pmtiles")
        ]

    # GrayWolf offline tiles (Pi-only path). The downloader nests per-region
    # .pmtiles under states/ or country/ subfolders, so walk the tree (not just
    # the top level) — bounded by a file cap so a huge cache can't stall doctor.
    gw_tiles = []
    if os.path.isdir(GRAYWOLF_TILES_DIR):
        for root, _dirs, files in os.walk(GRAYWOLF_TILES_DIR):
            for f in files:
                if f.endswith(".pmtiles"):
                    gw_tiles.append(f)
                    if len(gw_tiles) >= 500:
                        break
            if len(gw_tiles) >= 500:
                break

    total = len(local_tiles) + len(gw_tiles)

    # Check /api/fs/browse only when the server is reachable — a missing route
    # means the server is running stale code and needs a restart.
    server_up = _probe_port(host, port)
    if server_up:
        fs_ok, _ = _http_get(f"http://{host}:{port}/api/fs/browse", timeout=4)
        if not fs_ok:
            extra = f" ({len(local_tiles)} local map(s) in maps/.)" if local_tiles else ""
            return _result(
                "maps", "DATA", "Map Tiles (PMTiles)", "warn", "RESTART",
                (
                    "/api/fs/browse missing — server is running old code.\n"
                    "     Restart OASIS to pick up the latest server/app.py."
                    + extra
                ),
            )

    gw_hint = (
        "GrayWolf provides offline maps. Download a region, then open\n"
        "     Offline Maps → Load maps and browse /var/lib/graywolf/tiles/."
    )

    if not total:
        return _result(
            "maps", "DATA", "Map Tiles (PMTiles)", "warn", "N/A",
            (
                "No .pmtiles maps found in maps/ or /var/lib/graywolf/tiles/.\n"
                "     " + gw_hint
            ),
        )

    parts = []
    if local_tiles:
        parts.append(f"{len(local_tiles)} in maps/: {', '.join(local_tiles)}")
    if gw_tiles:
        parts.append(f"{len(gw_tiles)} in GrayWolf tiles")
    detail = f"Map(s) found — {'; '.join(parts)}."
    if server_up:
        detail += " Load maps endpoint OK."
    return _result("maps", "DATA", "Map Tiles (PMTiles)", "ok", "READY", detail)


def check_graywolf(ctx):
    host = ctx["host"]
    port = PORTS["graywolf"]
    up   = _probe_port(host, port)
    svc  = _svc_status("graywolf")
    if up:
        return _result("graywolf", "SERVICES", "GrayWolf APRS", "ok", "UP",
                        f"graywolf ({_svc_word(svc)}), listening on :{port}.")
    if svc["installed"]:
        return _result(
            "graywolf", "SERVICES", "GrayWolf APRS", "warn", "STOPPED",
            (
                f"Installed but not listening (service graywolf is {svc['active']}).\n"
                "     Start: sudo systemctl start graywolf\n"
                "     Logs:  journalctl -u graywolf -f"
            ),
        )
    return _result("graywolf", "SERVICES", "GrayWolf APRS", "warn", "NOT INSTALLED",
                    f"Install with: python3 services/graywolf/install.py (port {port}).")


def check_graywolf_api(ctx):
    host = ctx["host"]
    port = PORTS["aprs_api"]
    up   = _probe_port(host, port)
    svc  = _svc_status("graywolf-api")
    if up:
        ok2, data = _http_get(f"http://{host}:{port}/api/aprs/stations", timeout=5)
        count  = data.get("count") if ok2 and data else None
        detail = f"API on :{port} ({_svc_word(svc)})"
        detail += f", {count} station(s) in history DB." if count is not None else "."
        return _result("graywolf_api", "SERVICES", "GrayWolf APRS History API", "ok", "UP", detail)
    if svc["installed"]:
        return _result(
            "graywolf_api", "SERVICES", "GrayWolf APRS History API", "warn", "STOPPED",
            (
                f"Installed but not responding ({svc['active']}).\n"
                "     Start: sudo systemctl start graywolf-api"
            ),
        )
    return _result("graywolf_api", "SERVICES", "GrayWolf APRS History API", "warn", "NOT INSTALLED",
                    f"Enabled by: python3 services/graywolf/install.py (port {port}).")


def check_pat(ctx):
    host    = ctx["host"]
    port    = PORTS["winlink"]
    up      = _probe_port(host, port)
    bin_    = _which_binary("pat")
    svc     = _svc_status("pat")
    pat_cfg = os.path.join(os.path.expanduser("~"), ".config", "pat", "config.json")

    if up:
        cfg_note = ""
        if os.path.isfile(pat_cfg):
            try:
                with open(pat_cfg) as fh:
                    cfg = json.load(fh)
                if not cfg.get("secure_login_password"):
                    cfg_note = "\n     Winlink password not set — run: pat configure"
            except Exception:
                pass
        badge  = "WARN" if cfg_note else "UP"
        status = "warn" if cfg_note else "ok"
        return _result("pat", "SERVICES", "Winlink (Pat)", status, badge,
                        f"Pat web UI on :{port} ({_svc_word(svc)}).{cfg_note}")

    if bin_:
        return _result(
            "pat", "SERVICES", "Winlink (Pat)", "warn", "STOPPED",
            (
                f"Pat installed ({bin_}) but web UI not running ({svc['active']}).\n"
                "     Start: sudo systemctl start pat"
            ),
        )
    return _result("pat", "SERVICES", "Winlink (Pat)", "warn", "NOT INSTALLED",
                    f"Install with: python3 services/winlink/install.py (port {port}).")


def check_rtl_sdr(ctx):
    rtl_test = _which_binary("rtl_test")
    rtl_fm   = _which_binary("rtl_fm")
    socat    = _which_binary("socat")
    tcpdump  = _which_binary("tcpdump")
    blk      = os.path.isfile(RTL_BLACKLIST)
    feed_svc = _svc_status("aprs-sdr-feed")

    if not rtl_test:
        return _result(
            "rtl_sdr", "HARDWARE", "RTL-SDR + APRS Audio Feed", "warn", "NOT INSTALLED",
            "RTL-SDR tools not found.\n     Install with: python3 features/rtl-sdr/install-rtl-sdr.py",
        )

    Y = "✓"
    N = "✗"
    lines = [
        f"rtl_test: {rtl_test}",
        f"rtl_fm: {Y if rtl_fm else N}  socat: {Y if socat else N}  tcpdump: {Y if tcpdump else N}",
        (f"DVB driver blacklist: {Y}" if blk
         else "DVB driver blacklist: ✗  (re-run features/rtl-sdr/install-rtl-sdr.py, then reboot)"),
    ]

    if feed_svc["active"] == "active":
        lines.append(f"APRS→GrayWolf feed: active ({_svc_word(feed_svc)})")
        feed_ok = True
    elif feed_svc["installed"]:
        lines.append(
            f"Feed service {feed_svc['active']}.\n"
            "     Start: sudo systemctl start aprs-sdr-feed"
        )
        feed_ok = False
    else:
        lines.append("Feed not enabled. Run: python3 services/rtl-feed/install.py")
        feed_ok = False

    tools_ok = bool(rtl_fm and socat and blk)
    badge    = "READY" if (feed_ok and tools_ok) else ("INSTALLED" if tools_ok else "PARTIAL")
    status   = "ok" if (feed_ok and tools_ok) else "warn"
    return _result("rtl_sdr", "HARDWARE", "RTL-SDR + APRS Audio Feed", status, badge,
                    "\n     ".join(lines))


def check_kiwix(ctx):
    host = ctx["host"]
    port = PORTS["kiwix"]
    up   = _probe_port(host, port)
    bin_ = _which_binary("kiwix-serve")
    svc  = _svc_status("kiwix")
    if up:
        detail = f"kiwix-serve on :{port} ({_svc_word(svc)})."
        zim_age = _kiwix_zim_age()
        if zim_age:
            detail += f" ZIM {zim_age}."
        return _result("kiwix", "DATA", "Kiwix (Offline Wikipedia)", "ok", "UP", detail)
    if bin_:
        zim_age = _kiwix_zim_age()
        zim_note = f" (ZIM {zim_age})" if zim_age else ""
        return _result(
            "kiwix", "DATA", "Kiwix (Offline Wikipedia)", "warn", "STOPPED",
            (
                f"kiwix-serve installed but not serving ({svc['active']}){zim_note}.\n"
                "     Add ZIM content, then: sudo systemctl start kiwix"
            ),
        )
    return _result("kiwix", "DATA", "Kiwix (Offline Wikipedia)", "warn", "NOT INSTALLED",
                    f"Install with: python3 services/kiwix/install.py (port {port}).")


def check_webssh(ctx):
    host = ctx["host"]
    port = PORTS["webssh"]
    up   = _probe_port(host, port)
    bin_ = _which_binary("ttyd")
    svc  = _svc_status("webssh")
    if up:
        return _result("webssh", "CORE", "Web SSH (ttyd)", "ok", "UP",
                        f"ttyd (browser terminal) on :{port} ({_svc_word(svc)}).")
    if bin_:
        return _result(
            "webssh", "CORE", "Web SSH (ttyd)", "warn", "STOPPED",
            (
                f"ttyd installed but not running ({svc['active']}).\n"
                "     Start: sudo systemctl start webssh"
            ),
        )
    return _result("webssh", "CORE", "Web SSH (ttyd)", "warn", "NOT INSTALLED",
                    f"Install with: python3 services/webssh/install.py (port {port}).")


def check_winlink_forms(ctx):
    pages = [
        ("position-report.html", "position-report"),
        ("to-position.html",     "to-position"),
        ("radio-settings.html",  "radio-settings"),
    ]
    present = [lbl for fname, lbl in pages
               if os.path.isfile(os.path.join(WINLINK_DIR, fname))]
    missing = [lbl for fname, lbl in pages
               if not os.path.isfile(os.path.join(WINLINK_DIR, fname))]

    if not present:
        return _result(
            "winlink_forms", "DATA", "Winlink Forms (Offline Pages)", "fail", "MISSING",
            "No Winlink form pages found in services/winlink/static/.",
            breaks="No offline Winlink form pages are available to operators.",
            fix=_SETUP_URL,
        )
    detail = f"{len(present)} form(s) available: {', '.join(present)}"
    if missing:
        detail += f"  |  missing: {', '.join(missing)}"
    status = "warn" if missing else "ok"
    badge  = "PARTIAL" if missing else "READY"
    return _result("winlink_forms", "DATA", "Winlink Forms (Offline Pages)", status, badge, detail)


def check_station_identity(ctx):
    """CORE/ACCESS, critical. Signal: configuration/station.json (written by
    Setup -> Station). No callsign (missing file, or the "N0CALL" placeholder
    setup.py writes when grid/lat/lon are set without a callsign) means every
    beacon/position this station sends is anonymous -- that's a fail, not a
    warn. A callsign with no locator (grid square or lat/lon) still identifies
    the operator, so it only degrades to warn."""
    path = config_paths.station_json(REPO_ROOT)
    if not os.path.isfile(path):
        return _result(
            "station_identity", "CORE", "Station Identity", "fail", "UNSET",
            "No station.json found — callsign not configured.\n     Set it in Setup → Station.",
            breaks="No identity — beacons/positions are anonymous.",
            fix=_SETUP_URL + "#station",
        )
    try:
        with open(path) as fh:
            st = json.load(fh) or {}
    except (OSError, ValueError):
        st = {}

    callsign = (st.get("callsign") or "").strip().upper()
    if not callsign or callsign == "N0CALL":
        return _result(
            "station_identity", "CORE", "Station Identity", "fail", "UNSET",
            "No callsign configured.\n     Set it in Setup → Station.",
            breaks="No identity — beacons/positions are anonymous.",
            fix=_SETUP_URL + "#station",
        )

    grid = (st.get("grid") or "").strip().upper()
    lat, lon = st.get("lat"), st.get("lon")
    if not grid and lat is None and lon is None:
        return _result(
            "station_identity", "CORE", "Station Identity", "warn", "PARTIAL",
            (
                f"{callsign} set, but no grid square or coordinates.\n"
                "     Add a locator in Setup → Station."
            ),
        )

    detail = f"{callsign} · {grid}" if grid else f"{callsign} · {lat:.4f},{lon:.4f}"
    return _result("station_identity", "CORE", "Station Identity", "ok", "SET", detail)


def check_digirig(ctx):
    """HARDWARE/APRS_RX, not critical -- a DRA-Pi or SDR-only station is a
    valid setup with no DigiRig at all. Signal: any /dev/serial/by-id entry
    (hardware_detect.digirig_candidates() -- any USB-serial adapter on this
    suite's supported hardware is the DigiRig; see that function's docstring
    for why label-substring filtering was dropped)."""
    serial_by_id = hardware_detect.list_serial_by_id()
    candidates = hardware_detect.digirig_candidates(serial_by_id)
    if candidates:
        first = candidates[0]
        path = first.get("path") or first.get("label") or "detected"
        extra = f" (+{len(candidates) - 1} more)" if len(candidates) > 1 else ""
        return _result("digirig", "HARDWARE", "DigiRig (USB-Serial PTT)", "ok", "PRESENT",
                        f"{path}{extra}")
    return _result("digirig", "HARDWARE", "DigiRig (USB-Serial PTT)", "warn", "ABSENT",
                    "No USB-serial PTT device found under /dev/serial/by-id.")


def check_dra_pi(ctx):
    """HARDWARE/APRS_RX, not critical -- a DigiRig or SDR-only station is a
    valid setup with no DRA-Pi HAT at all. Signal: configuration/hardware.json
    inventory (common.hardware.load), which the hardware poll keeps
    reconciled against the HAT's audioinjectorpi ALSA card via
    hardware.reconcile_dra_pi(). Reads the persisted inventory rather than
    re-probing ALSA directly so this stays cheap and consistent with what the
    Setup page's Dongles list shows. Never raises -- an unreadable inventory
    degrades to warn/UNKNOWN rather than failing the whole sweep."""
    try:
        inv = hardware.load(REPO_ROOT)
        present = any(d.get("kind") == "dra-pi" for d in inv.devices.values())
    except Exception:
        return _result("dra_pi", "HARDWARE", "DRA-Pi (GrayWolf Audio HAT)", "warn", "UNKNOWN",
                        "Could not read the hardware inventory (configuration/hardware.json).")
    if present:
        return _result("dra_pi", "HARDWARE", "DRA-Pi (GrayWolf Audio HAT)", "ok", "PRESENT",
                        "DRA-Pi HAT declared in the hardware inventory.")
    return _result("dra_pi", "HARDWARE", "DRA-Pi (GrayWolf Audio HAT)", "warn", "ABSENT",
                    "No DRA-Pi HAT declared in the hardware inventory.")


def check_gps(ctx):
    """HARDWARE/POSITION, critical. Signal: GET /api/system's "gps" block
    (server/routes/system.py's _gps_info(), sampled from gpsd every ~10s).
    mode 2/3 = fix (badge 2D/3D); mode 0 = gpsd up but no fix; gps missing/
    null = gpsd itself unreachable (or the OASIS API call failed) -- both of
    those non-fix cases surface as warn, never fail, so a station without a
    GPS antenna attached doesn't get flagged as broken; the capability rollup
    still shows POSITION as degraded via critical=True."""
    host, port = ctx["host"], ctx["port"]
    ok, data = _http_get(f"http://{host}:{port}/api/system", 5)
    gps = (data or {}).get("gps") if ok else None
    if not gps:
        return _result(
            "gps", "HARDWARE", "GPS", "warn", "OFF",
            "No GPS data — gpsd unreachable or not running.\n     Check the gpsd service and antenna.",
            breaks="No position fix — grid/beacon unreliable.",
        )

    mode = gps.get("mode", 0)
    if mode not in (2, 3):
        seen = gps.get("seen")
        # Surface the sky view even without a fix: "12 seen / 0 used" is the
        # cgps signal that tells acquiring-antenna apart from blind-antenna.
        sat_line = ""
        if seen is not None:
            sat_line = f"\n     {seen} sats seen, {gps.get('used') or 0} used in fix."
        badge = f"{seen} SEEN" if seen else "NO FIX"
        return _result(
            "gps", "HARDWARE", "GPS", "warn", badge,
            "gpsd is up but has no position fix yet." + sat_line
            + "\n     Check sky visibility / antenna.",
            breaks="No position fix — grid/beacon unreliable.",
        )

    badge = "3D" if mode == 3 else "2D"
    sats = gps.get("used")
    if sats is None:
        sats = gps.get("seen")
    parts = [badge]
    if sats is not None:
        parts.append(f"{sats} sats")
    lat, lon = gps.get("lat"), gps.get("lon")
    if lat is not None and lon is not None:
        parts.append(f"{lat:.4f},{lon:.4f}")
    return _result("gps", "HARDWARE", "GPS", "ok", badge, " · ".join(parts))



def check_offline_clock(ctx):
    """SYSTEM/TIME, critical. Can this station tell the time with NO internet?

    The check that did not exist while two live stations booted weeks stale —
    one three days, one twenty-six — with every other check green. Each part was
    individually fine; nothing owned the whole, so nothing noticed.

    Three things can carry the clock offline and a station needs one: an RTC
    that holds across a power cut, fake-hwclock, or chrony steering from a
    refclock it actually trusts. Each is probed as a CAPABILITY — /dev/rtc0
    exists on a Pi 5 with a flat cell, fake-hwclock reports installed while
    masked to /dev/null, and a GPS with a 3D fix can be feeding chrony samples
    chrony discards as a falseticker.

    warn rather than fail when the internet is currently reachable: the clock is
    right at this moment, and the finding is about the day it is not."""
    try:
        from common.timekeeping import offline_clock_report
        ok, sources, summary = offline_clock_report()
    except Exception as exc:                                    # noqa: BLE001
        return _result("offline_clock", "SYSTEM", "Offline clock", "warn", "UNKNOWN",
                       f"Could not determine the offline time sources: {exc}")

    lines = "\n".join(f"     {'OK  ' if s_ok else 'no  '}{name}: {detail}"
                       for name, s_ok, detail in sources)
    if ok:
        carried = [n for n, s_ok, _ in sources if s_ok]
        return _result("offline_clock", "SYSTEM", "Offline clock", "ok",
                       str(len(carried)) + " SRC",
                       f"The clock survives without the internet — {summary}.\n{lines}")
    return _result(
        "offline_clock", "SYSTEM", "Offline clock", "fail", "NO SOURCE",
        f"{summary}.\n{lines}",
        breaks=("On the next cold boot with no network this station will come up with a "
                "stale date. Pass predictions, log timestamps, Winlink and every recorded "
                "observation are wrong, silently and confidently."),
    )


def check_cooling_hat(ctx):
    """SYSTEM group / POWER capability, not critical -- cooling is a nice-to-
    have, not something that blocks a capability. Signal: an OASIS fan daemon
    systemd unit -- rgb-cooling-hat.service (features/rgb-cooling-hat) or
    argon-fan.service (features/argon-fan). Whichever is present wins."""
    label = "Cooling fan"
    installed = None
    for name in ("rgb-cooling-hat", "argon-fan"):
        svc = _svc_status(name)
        if svc["active"] == "active":
            return _result("cooling_hat", "SYSTEM", label, "ok", "UP",
                            f"{name} service {_svc_word(svc)}.")
        if svc["installed"] and installed is None:
            installed = (name, svc)
    if installed:
        name, svc = installed
        return _result("cooling_hat", "SYSTEM", label, "warn", "OFF",
                        f"Installed but not running ({name} is {svc['active']}).")
    return _result("cooling_hat", "SYSTEM", label, "warn", "N/A",
                    "No OASIS fan daemon installed (rgb-cooling-hat / argon-fan).")


def check_aprs_feed(ctx):
    """SERVICES/APRS_RX, critical. Signal: GET /api/aprs/stations on the
    GrayWolf history API port -- the newest station last_heard tells us when
    a packet was actually decoded, which `systemctl is-active` can't:
    aprs-sdr-feed stays "active" even with a yanked dongle feeding silence.
    ok/LIVE within APRS_RECENT_SECONDS of the newest decode; warn/IDLE when
    the API is reachable but stale or empty; fail/DOWN when unreachable. The
    tuned frequency (best-effort, from the running unit) is folded into
    detail when known, and simply omitted otherwise."""
    host = ctx["host"]
    port = PORTS["aprs_api"]
    ok, data = _http_get(f"http://{host}:{port}/api/aprs/stations", 5)

    freq_disp = _fmt_freq(_aprs_feed_freq())
    freq_suffix = f" · {freq_disp}" if freq_disp else ""
    breaks = "Not hearing APRS traffic."

    if not ok or data is None:
        return _result(
            "aprs_feed", "SERVICES", "APRS Feed", "fail", "DOWN",
            f"GrayWolf APRS history API unreachable on :{port}{freq_suffix}.",
            breaks=breaks,
        )

    stations = data.get("stations") or []
    newest = None
    for st in stations:
        ts = _parse_ts((st or {}).get("last_heard"))
        if ts is not None and (newest is None or ts > newest):
            newest = ts

    if newest is None:
        return _result(
            "aprs_feed", "SERVICES", "APRS Feed", "warn", "IDLE",
            f"API reachable, no decoded packets yet{freq_suffix}.",
            breaks=breaks,
        )

    age_sec = max(0, int((datetime.datetime.now(datetime.timezone.utc) - newest).total_seconds()))
    age_txt = f"{age_sec}s ago" if age_sec < 120 else f"{age_sec // 60}m ago"

    if age_sec <= APRS_RECENT_SECONDS:
        return _result("aprs_feed", "SERVICES", "APRS Feed", "ok", "LIVE",
                        f"last packet {age_txt}{freq_suffix}")

    return _result(
        "aprs_feed", "SERVICES", "APRS Feed", "warn", "IDLE",
        f"last packet {age_txt}{freq_suffix} — no recent traffic.",
        breaks=breaks,
    )


def check_power(ctx):
    """SYSTEM/POWER, critical. Signal: GET /api/system's "throttle" block
    (server/routes/system.py's _pi_throttled(), from `vcgencmd get_throttled`).
    None means non-Pi/vcgencmd absent -- degrades to warn/N-A rather than
    fail, since a dev machine legitimately has no throttle telemetry.
    fail/UNDERVOLT only when under-voltage or throttling is happening right
    now (next TX could brown out); warn/PAST when it merely happened since
    boot but is currently clean; ok/OK when clean throughout."""
    host, port = ctx["host"], ctx["port"]
    ok, data = _http_get(f"http://{host}:{port}/api/system", 5)
    throttle = (data or {}).get("throttle") if ok else None

    if not throttle:
        return _result(
            "power", "SYSTEM", "Power", "warn", "N/A",
            "No throttle telemetry (vcgencmd unavailable — not a Pi, or server unreachable).",
        )

    now = throttle.get("now") or {}
    ever = throttle.get("ever") or {}
    bits_order = ("under_voltage", "freq_capped", "throttled", "soft_temp")

    if now.get("under_voltage") or now.get("throttled"):
        bits = [b for b in bits_order if now.get(b)]
        return _result(
            "power", "SYSTEM", "Power", "fail", "UNDERVOLT",
            f"Throttling now ({', '.join(bits)}).",
            breaks="Undervoltage now — next TX may brown out.",
            fix=_SETUP_URL,
        )

    if any(ever.get(b) for b in bits_order):
        bits = [b for b in bits_order if ever.get(b)]
        return _result(
            "power", "SYSTEM", "Power", "warn", "PAST",
            f"Throttling occurred since boot ({', '.join(bits)}); currently clean.",
        )

    return _result("power", "SYSTEM", "Power", "ok", "OK",
                    "No under-voltage or throttling detected.")


def check_temp(ctx):
    """SYSTEM/POWER, not critical -- thermal throttling itself is what
    breaks a capability (covered by check_power); this is an earlier warning
    light. Signal: GET /api/system's cpu_temp_c (psutil sensors, Pi-specific
    -- None on macOS/Windows degrades to warn/N-A, never fail)."""
    host, port = ctx["host"], ctx["port"]
    ok, data = _http_get(f"http://{host}:{port}/api/system", 5)
    temp = (data or {}).get("cpu_temp_c") if ok else None

    if temp is None:
        return _result("temp", "SYSTEM", "CPU Temperature", "warn", "N/A",
                        "No temperature sensor reading available.")

    detail = f"{temp:g}°C"
    if temp > 80:
        return _result("temp", "SYSTEM", "CPU Temperature", "fail", "HOT", detail)
    if temp >= 70:
        return _result("temp", "SYSTEM", "CPU Temperature", "warn", "WARM", detail)
    return _result("temp", "SYSTEM", "CPU Temperature", "ok", "OK", detail)


def check_cpu(ctx):
    """SYSTEM/POWER, never critical -- high CPU alone doesn't take a
    capability down. Signal: GET /api/system's cpu_pct (psutil, rolling
    ~2s sample)."""
    host, port = ctx["host"], ctx["port"]
    ok, data = _http_get(f"http://{host}:{port}/api/system", 5)
    pct = (data or {}).get("cpu_pct") if ok else None

    if pct is None:
        return _result("cpu", "SYSTEM", "CPU Load", "warn", "N/A",
                        "No CPU utilization reading available.")

    detail = f"{pct:g}%"
    if pct >= 85:
        return _result("cpu", "SYSTEM", "CPU Load", "warn", "HIGH", detail)
    return _result("cpu", "SYSTEM", "CPU Load", "ok", "OK", detail)


def check_repeaterbook(ctx):
    """DATA/REFERENCE, not critical. RepeaterBook is a record_only feature
    (setup-oasis.py / common/setup_registry.py's "repeaterbook" spec) --
    there's no installer step; the operator drops their own CSV export
    (copyrighted, can't be vendored) at REPEATERBOOK_CSV. Signal: the file's
    on-disk presence + mtime."""
    if not os.path.isfile(REPEATERBOOK_CSV):
        return _result(
            "repeaterbook", "DATA", "RepeaterBook", "warn", "MISSING",
            "No repeaterbook.csv at static/repeaterbook/ — export one from "
            "repeaterbook.com and drop it in.",
        )
    age = _data_age(REPEATERBOOK_CSV)
    detail = "RepeaterBook CSV present" + (f" · {age}" if age else "")
    return _result("repeaterbook", "DATA", "RepeaterBook", "ok", "READY", detail)


def check_forms(ctx):
    """DATA/REFERENCE, not critical. Signal: on-disk presence of the four
    ICS forms OASIS ships (docs/SETUP.md "ICS Forms"): static/ics-{205,213,
    214,309}/ics-*.html. This is what the app's own "forms" feature key
    (setup-oasis.py / common/setup_registry.py's record_only "forms" spec)
    actually refers to on this suite -- there is no separate Winlink
    "Standard Templates" library vendored anywhere in the codebase."""
    present = []
    missing = []
    for slug, label in ICS_FORMS:
        path = os.path.join(REPO_ROOT, "static", slug, f"{slug}.html")
        if os.path.isfile(path):
            present.append((label, path))
        else:
            missing.append(label)

    if not present:
        return _result("forms", "DATA", "ICS Forms", "warn", "MISSING",
                        "No ICS form pages found in static/.")

    try:
        newest_path = max((p for _, p in present), key=os.path.getmtime)
        age = _data_age(newest_path)
    except OSError:
        age = None
    detail = f"{len(present)}/{len(ICS_FORMS)} form(s) present"
    if missing:
        detail += f"  |  missing: {', '.join(missing)}"
    if age:
        detail += f" · {age}"
    status = "warn" if missing else "ok"
    badge = "PARTIAL" if missing else "READY"
    return _result("forms", "DATA", "ICS Forms", status, badge, detail)


# ---------------------------------------------------------------------------
# UPDATES section
# ---------------------------------------------------------------------------
# Its own group rather than rows buried in DATA, because the operator question
# is not "is this check green" but WHAT IS OUT OF DATE, WHEN DID IT LAST UPDATE,
# and WHAT MUST I DO ABOUT IT. The action sentence is the point: a section that
# only says "stale" leaves the operator guessing whether to plug in a cable,
# find a token, or simply wait.

_UPDATE_STATUS = {
    "fresh": "ok",
    "stale": "warn",
    "deferred": "warn",
    "missing": "fail",
    "unconfigured": "ok",   # switched off is not broken
}

_UPDATE_BADGE = {
    "fresh": "CURRENT",
    "stale": "STALE",
    "deferred": "WAITING",
    "missing": "MISSING",
    "unconfigured": "OFF",
}

_TOKEN_KEY = {"repeaterbook": "repeaterbook_token"}


def _update_status(state):
    return _UPDATE_STATUS.get(state, "warn")


def _update_action(state, source_id):
    """The plain-language thing the operator must do. Never empty."""
    if state == "fresh":
        return "Nothing to do."
    if state in ("stale", "missing"):
        return ("Needs an internet connection. It will download by itself the "
                "next time the station is online.")
    if state == "deferred":
        return ("Large download held back because this connection looks "
                "metered. Press Update now to download anyway.")
    if state == "unconfigured":
        key = _TOKEN_KEY.get(source_id, "token")
        return (f'Switched off: no API token. Add "{key}" to '
                f"configuration/station.json to enable automatic updates.")
    return "Needs an internet connection."


def _update_detail(row):
    """What was updated, and when — an actual date, not just an age."""
    last = row.get("last_success")
    age = row.get("age_days")
    if not last and age is None:
        return "Never downloaded."
    if last:
        when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(last))
        if age is None:
            return f"Last updated {when}."
        return f"Last updated {when} ({age:.1f} days ago)."
    return f"On disk, {age:.1f} days old (not yet updated by this station)."


def _update_check(source_id):
    """One Check per auto-update source, built from a dry-run pass."""
    def _fn(ctx, sid=source_id):
        from common import refresh as _R
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            result = _R.run_pass(repo, now=time.time(),
                                 metered=_R.is_metered(), only=[sid],
                                 dry_run=True)
            row = result["sources"][0]
        except Exception as exc:
            return _result(f"update_{sid}", "UPDATES", sid, "warn", "ERROR",
                           f"Could not read update state: {exc}")
        state = row["state"]
        status = _update_status(state)
        return _result(
            f"update_{sid}", "UPDATES", row["label"], status,
            _UPDATE_BADGE.get(state, "?"),
            f"{_update_detail(row)} {_update_action(state, sid)}",
            breaks=("This dataset has never been downloaded"
                    if status == "fail" else None),
            fix=("/system/diagnostics.html#updates"
                 if status in ("warn", "fail") else None))
    return _fn


# ---------------------------------------------------------------------------
# Registry: real checks
# ---------------------------------------------------------------------------

REGISTRY.extend([
    Check(id="update_tle", group="UPDATES",
          label="Satellite TLEs (CelesTrak)",
          capability="UPDATES", critical=False, tier="v1",
          fn=_update_check("tle")),
    Check(id="update_satnogs", group="UPDATES",
          label="Satellite transmitters (SatNOGS)",
          capability="UPDATES", critical=False, tier="v1",
          fn=_update_check("satnogs")),
    Check(id="update_fcc", group="UPDATES", label="FCC callsign database",
          capability="UPDATES", critical=False, tier="v1",
          fn=_update_check("fcc")),
    Check(id="update_repeaterbook", group="UPDATES",
          label="RepeaterBook directory",
          capability="UPDATES", critical=False, tier="v1",
          fn=_update_check("repeaterbook")),
    Check(id="server", group="CORE", label="OASIS Server",
          capability="ACCESS", critical=True, tier="v1", fn=check_server),
    Check(id="disk", group="SYSTEM", label="Disk Space",
          capability="POWER", critical=False, tier="v1", fn=check_disk),
    Check(id="fcc", group="DATA", label="FCC Callsign Database",
          capability="REFERENCE", critical=False, tier="v1", fn=check_fcc),
    Check(id="maps", group="DATA", label="Map Tiles (PMTiles)",
          capability="REFERENCE", critical=False, tier="v1", fn=check_maps),
    Check(id="graywolf", group="SERVICES", label="GrayWolf APRS",
          capability="APRS_RX", critical=True, tier="v1", fn=check_graywolf),
    Check(id="graywolf_api", group="SERVICES", label="GrayWolf APRS History API",
          capability="APRS_RX", critical=False, tier="v1", fn=check_graywolf_api),
    Check(id="pat", group="SERVICES", label="Winlink (Pat)",
          capability="WINLINK", critical=True, tier="v1", fn=check_pat),
    Check(id="rtl_sdr", group="HARDWARE", label="RTL-SDR + APRS Audio Feed",
          capability="APRS_RX", critical=True, tier="v1", fn=check_rtl_sdr),
    Check(id="kiwix", group="DATA", label="Kiwix (Offline Wikipedia)",
          capability="REFERENCE", critical=False, tier="v1", fn=check_kiwix),
    Check(id="webssh", group="CORE", label="Web SSH (ttyd)",
          capability="ACCESS", critical=False, tier="v1", fn=check_webssh),
    Check(id="winlink_forms", group="DATA", label="Winlink Forms (Offline Pages)",
          capability="REFERENCE", critical=False, tier="backlog", fn=check_winlink_forms),
    Check(id="station_identity", group="CORE", label="Station Identity",
          capability="ACCESS", critical=True, tier="v1", fn=check_station_identity),
    Check(id="digirig", group="HARDWARE", label="DigiRig (USB-Serial PTT)",
          capability="APRS_RX", critical=False, tier="v1", fn=check_digirig),
    Check(id="dra_pi", group="HARDWARE", label="DRA-Pi (GrayWolf Audio HAT)",
          capability="APRS_RX", critical=False, tier="v1", fn=check_dra_pi),
    Check(id="gps", group="HARDWARE", label="GPS",
          capability="POSITION", critical=True, tier="v1", fn=check_gps),
    Check(id="offline_clock", group="SYSTEM", label="Offline clock",
          capability="POWER", critical=True, tier="v1", fn=check_offline_clock),
    Check(id="cooling_hat", group="SYSTEM", label="Cooling fan",
          capability="POWER", critical=False, tier="v1", fn=check_cooling_hat),
    Check(id="aprs_feed", group="SERVICES", label="APRS Feed",
          capability="APRS_RX", critical=True, tier="v1", fn=check_aprs_feed),
    Check(id="power", group="SYSTEM", label="Power",
          capability="POWER", critical=True, tier="v1", fn=check_power),
    Check(id="temp", group="SYSTEM", label="CPU Temperature",
          capability="POWER", critical=False, tier="v1", fn=check_temp),
    Check(id="cpu", group="SYSTEM", label="CPU Load",
          capability="POWER", critical=False, tier="v1", fn=check_cpu),
    Check(id="repeaterbook", group="DATA", label="RepeaterBook",
          capability="REFERENCE", critical=False, tier="v1", fn=check_repeaterbook),
    Check(id="forms", group="DATA", label="ICS Forms",
          capability="REFERENCE", critical=False, tier="v1", fn=check_forms),
])
