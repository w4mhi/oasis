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
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from collections import namedtuple

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

GROUP_ORDER = ["CORE", "HARDWARE", "SERVICES", "SYSTEM", "DATA"]

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
        "members": ["power", "temp", "disk", "cooling_hat"],
    },
    "REFERENCE": {
        "label": "Reference Data",
        "members": ["fcc", "repeaterbook", "kiwix", "forms", "maps"],
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

# Setup page has no #services / #content / #radio anchors today (only
# id="hardware" exists) -- fix links bare-link to /system/setup.html per the
# task brief's "if an anchor is absent, link bare" rule.
_SETUP_URL = "/system/setup.html"


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
    return _result("fcc", "DATA", "FCC Callsign Database", "ok", "READY",
                    f"Callsign index built ({count:,} entries).")


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
                    "Install with: python3 services/graywolf/install.py")


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
                    "Enabled by: python3 services/graywolf/install.py")


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
                    "Install with: python3 services/winlink/install.py")


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
        lines.append("Feed not enabled. Run: python3 features/rtl-sdr/enable-rtl-sdr.py")
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
        return _result("kiwix", "DATA", "Kiwix (Offline Wikipedia)", "ok", "UP",
                        f"kiwix-serve on :{port} ({_svc_word(svc)}).")
    if bin_:
        return _result(
            "kiwix", "DATA", "Kiwix (Offline Wikipedia)", "warn", "STOPPED",
            (
                f"kiwix-serve installed but not serving ({svc['active']}).\n"
                "     Add ZIM content, then: sudo systemctl start kiwix"
            ),
        )
    return _result("kiwix", "DATA", "Kiwix (Offline Wikipedia)", "warn", "NOT INSTALLED",
                    "Install with: python3 services/kiwix/install.py")


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
                    "Install with: python3 services/webssh/install.py")


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


# ---------------------------------------------------------------------------
# Registry: real checks
# ---------------------------------------------------------------------------

REGISTRY.extend([
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
])
