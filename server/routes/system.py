"""
System route blueprint — server/runtime info (/api/server-info, /api/config,
/server-ports.json, /api/installed-services) and the live system monitor
(/api/system, /api/audio) with its background sampler thread (rolling CPU%,
Pi throttle state, Wi-Fi, GPS via gpsd, chrony clock state). Extracted
verbatim from server/app.py in the blueprint split; URLs unchanged.
"""

import json
import os
import socket
import subprocess
import threading
import time

from flask import Blueprint, jsonify

import appconfig
from common import gpsd_chrony
from common.api_shape import iso_utc

SUITE_ROOT = appconfig.SUITE_ROOT
VERSION_FILE = appconfig.VERSION_FILE
INSTALLED_SERVICES_FILE = appconfig.INSTALLED_SERVICES_FILE

bp = Blueprint("system", __name__)


@bp.route("/api/server-info")
def server_info():
    """Report which WSGI server is running (gunicorn vs Flask dev server)."""
    import sys
    from importlib.metadata import version as pkg_version, PackageNotFoundError

    def get_ver(pkg):
        try:
            return pkg_version(pkg)
        except PackageNotFoundError:
            return "unknown"

    def oasis_ver():
        try:
            import json as _json
            with open(VERSION_FILE, encoding="utf-8") as fh:
                return str(_json.load(fh).get("version") or "unknown")
        except (OSError, ValueError):
            return "unknown"

    if "gunicorn" in sys.modules:
        wsgi = "gunicorn"
        wsgi_version = get_ver("gunicorn")
    else:
        wsgi = "werkzeug"
        wsgi_version = get_ver("werkzeug")

    return jsonify({
        "ok":            True,
        "version":       oasis_ver(),
        "wsgi":          wsgi,
        "wsgi_version":  wsgi_version,
        "flask_version": get_ver("flask"),
        "port":          appconfig.PORT,
    })


@bp.route("/api/config")
def api_config():
    """Return runtime configuration (port, feature flags) so HTML pages need
    not hardcode any values.  The PORT global is updated by __main__ before
    the server starts, so this always reflects the actual listening port."""
    payload = {
        "ok": True,
        "port": appconfig.PORT,
        "ports": {
            "flask": appconfig.PORT,
            "graywolf": 8080,
            "kiwix": 8081,
            "aprs_api": 8085,
            "webssh": 7681,
            "winlink": 8082,
            "openwebrx": 8073,
        },
    }
    return jsonify(payload)


@bp.route("/server-ports.json")
def server_ports():
    """Alias for /api/config — canonical service-discovery endpoint.
    HTML pages fetch this on load so no port numbers need to be hardcoded."""
    return api_config()


@bp.route("/api/installed-services")
def api_installed_services():
    """Report which features setup-oasis.py recorded as installed, so the
    dashboard can hide cards for services the operator chose not to install.

    Returns {"ok": True, "features": [...]} when the manifest exists, or
    {"ok": True, "features": None} when it's absent/unreadable — the dashboard
    treats a null list as “no manifest” and shows every card.

    Portable mode (OASIS_FEATURES set) overrides the on-disk manifest: it
    returns exactly that list with locked=True, so the dashboard shows only
    these cards and hides the reveal button. Nothing is written to disk."""
    if appconfig.PORTABLE_FEATURES is not None:
        return jsonify({"ok": True, "locked": True,
                        "features": appconfig.PORTABLE_FEATURES})
    try:
        with open(INSTALLED_SERVICES_FILE) as fh:
            data = json.load(fh)
        feats = data.get("features")
        if isinstance(feats, list):
            return jsonify({"ok": True,
                            "features": sorted({str(k) for k in feats})})
    except FileNotFoundError:
        pass
    except (ValueError, OSError):
        pass
    return jsonify({"ok": True, "features": None})


# ── Raspberry Pi power/thermal + Wi-Fi helpers ────────────────────────────────
def _pi_throttled():
    """Pi power/thermal throttling via `vcgencmd get_throttled`. Returns None on
    non-Pi hosts (vcgencmd absent). Bitmask: bits 0-3 = under-voltage / freq
    capped / throttled / soft-temp-limit happening *now*; bits 16-19 = the same
    having *occurred since boot*."""
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"],
                             capture_output=True, text=True, timeout=2)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or "throttled=" not in out.stdout:
        return None
    try:
        val = int(out.stdout.strip().split("throttled=")[1], 16)
    except (ValueError, IndexError):
        return None
    now  = {"under_voltage": bool(val & 0x1), "freq_capped": bool(val & 0x2),
            "throttled": bool(val & 0x4), "soft_temp": bool(val & 0x8)}
    ever = {"under_voltage": bool(val & 0x10000), "freq_capped": bool(val & 0x20000),
            "throttled": bool(val & 0x40000), "soft_temp": bool(val & 0x80000)}
    return {"raw": hex(val), "now": now, "ever": ever,
            "now_any": any(now.values()), "ever_any": any(ever.values())}


def _wifi_info():
    """Best-effort Wi-Fi SSID + associated-station count (for Pi access-point
    use). Returns None when the tools/interface are absent (e.g. on a Mac).

    Contract §5: null means "no Wi-Fi information at all"; a dict always carries
    BOTH keys. Omitting `clients` when only the SSID was readable would make
    "no stations associated" and "we couldn't ask" the same answer."""
    info = {}
    try:
        out = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=2)
        if out.returncode == 0 and out.stdout.strip():
            info["ssid"] = out.stdout.strip()
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        pass
    for iface in ("wlan0", "wlan1"):
        try:
            out = subprocess.run(["iw", "dev", iface, "station", "dump"],
                                 capture_output=True, text=True, timeout=2)
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            break  # `iw` not installed — stop trying
        if out.returncode == 0 and "Station " in out.stdout:
            info["clients"] = out.stdout.count("Station ")
            break
    if not info:
        return None
    return {"ssid": info.get("ssid"), "clients": info.get("clients")}


def _gps_interface(device_path):
    """Classify a gpsd device path as 'usb' (features/gps's own candidates)
    or 'uart' (features/gps-L76X's candidates), or None if unrecognized or
    absent. Path shape only — features/gps's own CANDIDATES list includes
    /dev/ttyAMA0 and /dev/serial0 as fallbacks in case no dedicated USB path
    is found, so this classifier (not "which feature wrote the config") is
    the single source of truth for what the wire actually is. See
    specs/2026-07-14-gps-detection-status-design.md §3."""
    if not device_path:
        return None
    if device_path.startswith("/dev/ttyACM") or device_path.startswith("/dev/ttyUSB"):
        return "usb"
    if (device_path.startswith("/dev/ttyS") or device_path == "/dev/serial0"
            or device_path.startswith("/dev/ttyAMA")):
        return "uart"
    return None


# Union of features/gps's CANDIDATES (USB) and features/gps-L76X's
# DEVICE_CANDIDATES (UART) — see specs/2026-07-14-gps-detection-status-design.md.
_GPS_CANDIDATE_PATHS = ("/dev/ttyACM0", "/dev/ttyUSB0", "/dev/ttyS0",
                        "/dev/serial0", "/dev/ttyAMA0")


def _gps_presence_status():
    """Which device gpsd is configured for (interface-classified), plus any
    OTHER GPS-shaped path that's physically present but unused. Pure
    filesystem/config-file reads only — NEVER opens the serial port itself
    (gpsd may already hold it; see specs/2026-07-14-gps-detection-status-design.md
    §3 for why that matters on a box that needs steady GPS-disciplined time).
    Independent of whether gpsd itself is reachable right now — this is why
    it's a separate function from _gps_info(), not inlined into its
    socket-dependent early-return path."""
    configured = gpsd_chrony.configured_device()
    other = sorted(p for p in _GPS_CANDIDATE_PATHS
                   if p != configured and os.path.exists(p))
    return {
        "device": configured,
        "interface": _gps_interface(configured),
        "otherDetected": [{"path": p, "interface": _gps_interface(p)} for p in other],
    }


def _gps_info():
    """Snapshot from gpsd (127.0.0.1:2947): fix mode, sats, position, plus
    which physical interface (usb/uart) is providing it and whether another
    GPS-shaped device is present but unused (see _gps_presence_status()).
    Returns None if gpsd isn't reachable (so the card hides — this also
    means the presence/interface fields are only populated once gpsd is up;
    see specs/2026-07-14-gps-detection-status-design.md for why that's an
    intentional scope boundary, not an oversight). Otherwise a dict (mode 0
    = gpsd up, no fix yet). Dependency-free — speaks gpsd's JSON protocol
    over a socket."""
    import json as _json
    try:
        s = socket.create_connection(("127.0.0.1", 2947), timeout=1.5)
    except OSError:
        return None
    info = {}
    try:
        s.sendall(b'?WATCH={"enable":true,"json":true};\n')
        s.settimeout(1.5)
        buf = b""
        deadline = time.time() + 2.0
        have_tpv = have_sky = False
        while time.time() < deadline and not (have_tpv and have_sky):
            try:
                chunk = s.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = _json.loads(line)
                except ValueError:
                    continue
                if msg.get("class") == "TPV":
                    info["mode"] = msg.get("mode", 0)
                    if msg.get("lat") is not None:
                        info["lat"] = msg["lat"]
                    if msg.get("lon") is not None:
                        info["lon"] = msg["lon"]
                    alt = msg.get("altMSL", msg.get("alt"))
                    if alt is not None:
                        info["alt_m"] = alt
                    have_tpv = True
                elif msg.get("class") == "SKY":
                    if msg.get("hdop") is not None:
                        info["hdop"] = msg["hdop"]
                    # gpsd emits several SKY messages per cycle, and most are
                    # DOP-only (no nSat/uSat/satellites). Only a SKY that
                    # actually carries satellite data is a valid sat-count
                    # snapshot — treating a DOP-only SKY as complete captured
                    # 0/0 and exited before the real satellite-bearing SKY
                    # arrived. nSat present (even 0) or a non-empty satellites
                    # list both count as real.
                    sats = msg.get("satellites")
                    if msg.get("nSat") is not None or sats:
                        info["seen"] = msg.get("nSat", len(sats or []))
                        info["used"] = msg.get("uSat", sum(1 for x in (sats or []) if x.get("used")))
                        have_sky = True
    except OSError:
        pass
    finally:
        try:
            s.close()
        except OSError:
            pass
    # Contract §5: every key present, null when unknown. gpsd omits lat/lon/hdop
    # until it has them, so an acquiring receiver used to return a DIFFERENT SET
    # OF KEYS from a fixed one — a consumer could not tell "no position yet"
    # from "this field doesn't exist on this build".
    result = {
        "mode":     info.get("mode", 0),
        "lat":      info.get("lat"),
        "lon":      info.get("lon"),
        "alt_m":    info.get("alt_m"),
        "hdop":     info.get("hdop"),
        "seen":     info.get("seen"),
        "used":     info.get("used"),
    }
    result.update(_gps_presence_status())
    return result


def _chrony_state():
    """Clock state from chrony — INDEPENDENT of GPS (chrony runs regardless and
    may sync from NTP or the RTC).

    `running` comes from systemd (authoritative, needs no privilege). Sync detail
    comes from `chronyc -c tracking` *when queryable* — the server user often
    can't reach chronyd's command socket, so we also try forcing the localhost
    UDP path, and degrade to {running, queryable:False} if neither works.
    CSV: field 1 = reference name ('GPS' for the refclock, else an NTP host),
    field 4 = system-time offset (s), last field = leap status.

    Contract §5: this returned three DIFFERENT SHAPES ({running}, {running,
    queryable}, and the full detail). It now always returns the same six keys
    with null for what could not be determined, so `chrony.synced === null`
    ("couldn't ask") is distinguishable from `false` ("asked; not synced")."""
    def _state(**kw):
        base = {"running": False, "queryable": False, "synced": None,
                "source": None, "gps": None, "offset_s": None}
        base.update(kw)
        return base

    active = ""
    for unit in ("chrony", "chronyd"):
        try:
            active = subprocess.run(["systemctl", "is-active", unit],
                                   capture_output=True, text=True, timeout=5).stdout.strip()
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            active = ""
        if active == "active":
            break
    if active != "active":
        return _state(running=False)

    out = None
    for cmd in (["chronyc", "-c", "tracking"],
                ["chronyc", "-h", "127.0.0.1", "-c", "tracking"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and r.stdout.strip():
            out = r.stdout.strip()
            break
    if out is None:
        return _state(running=True)                    # daemon up, can't read detail

    f = out.split(",")
    if len(f) < 5:
        return _state(running=True)
    leap = f[-1].strip()
    try:
        offset = float(f[4])
    except ValueError:
        offset = None
    return _state(
        running=True,
        queryable=True,
        synced=leap in ("Normal", "Insert second", "Delete second"),
        source=f[1],
        gps="GPS" in f[1].upper(),
        offset_s=offset,
    )


# ── Background sampler ─────────────────────────────────────────────────────────
# CPU% over a rolling 2s window, plus slower-changing Pi facts (throttle state,
# Wi-Fi), are measured on a daemon thread and cached. /api/system then never
# spawns a subprocess or blocks in the request path — stable, Pi-friendly, and
# values agree across repeated polls. (Under gunicorn each worker samples its
# own; they track closely since both average the same window.)
_CPU_PCT  = None  # most recent rolling CPU%; None until the first sample lands
_CPU_CORES = []   # most recent per-core CPU%; [] until the first sample
_TOP_PROCS = []   # most recent top-3 procs [{name, cpu, mem}]; [] until sampled
_THROTTLE = None  # cached _pi_throttled(); None on non-Pi
_NET      = None  # cached _wifi_info();    None when unavailable
_GPS      = None  # cached _gps_info();    None when gpsd unreachable
_CHRONY   = None  # cached _chrony_state(); clock state, independent of GPS
_COOLING  = None  # cached _cooling_status(); which fan daemon is present, or None


# Which OASIS fan daemon feeds the CPU-card fan blade, in priority order.
_COOLING_UNITS = (("argon", "argon-fan.service"), ("rgb", "rgb-cooling-hat.service"))


def _cooling_status():
    """Which OASIS cooling-fan daemon is installed, and whether it's running —
    for the CPU card's fan blade. Returns e.g. {"kind":"argon","installed":True,
    "running":True}, or None when neither daemon is present. Cheap (one
    `systemctl show` per unit) and run on the sampler's slow tick, never in the
    request path. `systemctl show` exits 0 even for an absent unit (LoadState=
    not-found), so a missing daemon just doesn't match."""
    for kind, unit in _COOLING_UNITS:
        try:
            out = subprocess.run(
                ["systemctl", "show", unit, "-p", "LoadState", "-p", "ActiveState"],
                capture_output=True, text=True, timeout=3).stdout
        except Exception:
            continue
        props = dict(ln.split("=", 1) for ln in out.strip().splitlines() if "=" in ln)
        if props.get("LoadState") == "loaded":
            return {"kind": kind, "installed": True,
                    "running": props.get("ActiveState") == "active"}
    return None

def _read_top_procs(procmap, limit=3):
    """Top `limit` processes by CPU% over the last sample window. `procmap` is a
    {pid: psutil.Process} whose cpu_percent() was primed one interval earlier
    (the sampler primes before its blocking window), so cpu_percent(None) here
    returns the delta. Best-effort: dead/denied procs are skipped. CPU% is the
    psutil convention (can exceed 100 on multi-core, matching syscore.py)."""
    procs = []
    for p in procmap.values():
        try:
            cpu = p.cpu_percent(None) or 0.0
            if cpu <= 0.0:
                continue
            with p.oneshot():
                procs.append({
                    "name": (p.name() or "?")[:20],
                    "cpu":  round(cpu, 1),
                    "mem":  round(p.memory_percent() or 0.0, 1),
                })
        except Exception:
            continue
    procs.sort(key=lambda x: x["cpu"], reverse=True)
    return procs[:limit]


def _sampler():
    try:
        import psutil
    except ImportError:
        psutil = None
    global _CPU_PCT, _CPU_CORES, _TOP_PROCS, _THROTTLE, _NET, _GPS, _CHRONY, _COOLING
    if psutil:
        psutil.cpu_percent(interval=None)              # prime overall baseline
    i = 0
    while True:
        if psutil:
            # Sample processes only every ~6s to bound /proc churn on the Pi.
            # Prime cpu_percent on PERSISTENT Process objects just before the 2s
            # block, then read the delta after — process_iter() yields fresh
            # objects each call, so the same instances must be held across the wait.
            sample_procs = (i % 3 == 0)
            procmap = {}
            if sample_procs:
                for p in psutil.process_iter():
                    try:
                        p.cpu_percent(None)
                        procmap[p.pid] = p
                    except Exception:
                        pass
            cores = psutil.cpu_percent(interval=2.0, percpu=True)   # blocks ~2s
            _CPU_CORES = [round(c, 1) for c in cores]
            _CPU_PCT   = round(sum(cores) / len(cores), 1) if cores else None
            if sample_procs:
                _TOP_PROCS = _read_top_procs(procmap)
        else:
            time.sleep(2.0)
        if i % 5 == 0:                                  # refresh ~every 10s
            _THROTTLE = _pi_throttled()
            _NET      = _wifi_info()
            _GPS      = _gps_info()
            _CHRONY   = _chrony_state()
            _COOLING  = _cooling_status()
        i += 1

threading.Thread(target=_sampler, name="oasis-sampler", daemon=True).start()


def _lan_ip():
    """Best-effort primary LAN IP. First tries the UDP-connect trick to pick the
    outbound interface (no packets sent, no internet needed). When there is no
    default route — e.g. the Pi is hosting the OASIS AP with no upstream — that
    returns loopback, so fall back to the first non-loopback IPv4 address,
    preferring the Wi-Fi interface (the AP's 10.42.0.1)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    finally:
        s.close()
    try:
        import psutil
        addrs = psutil.net_if_addrs()
        # Prefer wlan* (AP/client radio), then other real interfaces; skip lo.
        for name in sorted(addrs, key=lambda n: (0 if n.startswith("wlan") else 1, n)):
            if name.startswith("lo"):
                continue
            for a in addrs[name]:
                if (a.family == socket.AF_INET and a.address
                        and not a.address.startswith("127.")):
                    return a.address
    except Exception:
        pass
    return "127.0.0.1"


@bp.route("/api/system")
def api_system():
    """System resource stats (CPU, RAM, disk, temp, load, uptime) — on the
    contract, docs/api-contract.md.

    Every key is always present; null means "not measurable here" (§5). A
    top-level block is null only when the whole subsystem is absent — no gpsd,
    no Wi-Fi tooling, no Pi throttle registers — and when a block IS a dict it
    always carries its full key set. That distinction is the whole point: a
    caller can tell "this machine has no GPS" from "the GPS has no fix yet"
    without knowing anything about OASIS."""
    try:
        import psutil
    except ImportError:
        # §2/§3: a real failure, so NOT 200. psutil is what this endpoint is
        # made of — unlike a stopped optional service, there is no answer to
        # give. Windows bundles without psutil land here.
        return jsonify({"ok": False, "error": "psutil not installed",
                        "code": "SYSTEM_METRICS_UNAVAILABLE"}), 503

    # Disk — auto-detect: SSD → eMMC → system root
    disk_info = None
    for mount, label in [("/mnt/ssd", "SSD"), ("/mnt/emmc", "eMMC"),
                         ("/", "System"), ("C:\\", "System")]:
        try:
            d = psutil.disk_usage(mount)
            disk_info = {
                "label":    label,
                "total_gb": round(d.total / 1e9, 1),
                "used_gb":  round(d.used  / 1e9, 1),
                "free_gb":  round(d.free  / 1e9, 1),
                "pct":      d.percent,
            }
            break
        except Exception:
            continue
    if disk_info is None:
        # §5: NOT {"error": "unavailable"}. Switching a metrics block to an
        # error block gave `disk` two incompatible shapes, which is why both
        # dashboards had to guard on `!d.disk.error` before reading `d.disk.pct`.
        disk_info = {"label": None, "total_gb": None, "used_gb": None,
                     "free_gb": None, "pct": None}

    # CPU — use the cached rolling sample; fall back to a quick snapshot only
    # during the brief window before the background sampler produces its first.
    cpu_pct   = _CPU_PCT if _CPU_PCT is not None else psutil.cpu_percent(interval=0.1)
    cpu_count = psutil.cpu_count(logical=True) or 1

    # RAM
    ram = psutil.virtual_memory()
    ram_info = {
        "total_mb": round(ram.total     / 1e6, 1),
        "used_mb":  round(ram.used      / 1e6, 1),
        "free_mb":  round(ram.available / 1e6, 1),
        "pct":      ram.percent,
    }

    # Load average (Linux/macOS only). §5: always the same three keys — `cores`
    # is knowable even on Windows, where the average itself is not.
    try:
        load1, _, _ = psutil.getloadavg()
        load_info = {
            "avg1":  round(load1, 2),
            "cores": cpu_count,
            "pct":   round((load1 / cpu_count) * 100, 1),
        }
    except AttributeError:
        load_info = {"avg1": None, "cores": cpu_count, "pct": None}

    # Boot time / uptime. §6: the old `boot_str` was "Fri Aug 07 19:29" — no
    # year, no zone, unparseable by anything that isn't a human reading a card.
    boot_ts   = psutil.boot_time()
    uptime_s  = int(time.time() - boot_ts)
    boot_time = iso_utc(boot_ts)

    # CPU temperature (Pi-specific; absent on macOS/Windows)
    temp = None
    try:
        temps = psutil.sensors_temperatures()
        for key in ("cpu_thermal", "cpu-thermal", "soc_thermal", "coretemp"):
            if key in temps and temps[key]:
                temp = round(temps[key][0].current, 1)
                break
    except Exception:
        pass

    # FCC DB last modified. §6: an ISO instant, not the local calendar date the
    # old `fcc_db_date` reported — "2026-08-04" means a different thing either
    # side of midnight depending on where the reader is.
    fcc_db_updated = None
    fcc_db_candidates = [
        os.path.join(SUITE_ROOT, "services", "fcc_database", "data", "EN.dat"),
        "/mnt/ssd/Documents/reference/fcc-offline-database/data/EN.dat",
    ]
    for path in fcc_db_candidates:
        try:
            fcc_db_updated = iso_utc(os.path.getmtime(path))
            break
        except Exception:
            continue

    return jsonify({
        "ok":             True,
        "hostname":       socket.gethostname(),
        "ip":             _lan_ip(),
        "cpu_pct":        cpu_pct,
        "cpu_count":      cpu_count,
        "cpu_cores":      _CPU_CORES,
        "top_procs":      _TOP_PROCS,
        "cpu_temp_c":     temp,
        "ram":            ram_info,
        "disk":           disk_info,
        "load":           load_info,
        "uptime_s":       uptime_s,
        "boot_time":      boot_time,
        "fcc_db_updated": fcc_db_updated,
        # null = subsystem absent on this machine; a dict always has every key.
        "throttle":       _THROTTLE,
        "net":            _NET,
        "gps":            _GPS,
        "chrony":         _CHRONY,
        "cooling":        _COOLING,
    })


@bp.route("/api/audio")
def api_audio():
    """List ALSA sound cards (for choosing a GrayWolf audio device).

    Reads /proc/asound — no extra Python deps. Linux/ALSA only; returns
    supported=False on macOS/Windows so the UI can show 'n/a'.
    Each card reports capture (RX) / playback (TX) capability and the ALSA
    address (hw:N,0) to plug straight into GrayWolf's config.
    """
    import re
    import sys

    # Audio enumeration is Linux/ALSA only (reads /proc/asound). Windows and
    # macOS have no procfs sound nodes, so report unsupported rather than
    # erroring — the cross-platform bundle keeps working and the UI shows "n/a".
    if not sys.platform.startswith("linux") or not os.path.exists("/proc/asound/cards"):
        return jsonify({"ok": True, "supported": False, "cards": []})

    try:
        with open("/proc/asound/cards") as fh:
            text = fh.read()
    except OSError as exc:
        return jsonify({"ok": False, "supported": True, "error": str(exc)}), 503

    def pcm_kinds(n):
        """Return (capture, playback) by inspecting /proc/asound/cardN/pcm*."""
        cap = play = False
        try:
            for entry in os.listdir(f"/proc/asound/card{n}"):
                if re.match(r"pcm\d+c$", entry):
                    cap = True
                elif re.match(r"pcm\d+p$", entry):
                    play = True
        except OSError:
            pass
        return cap, play

    cards = []
    # Line format: " N [id   ]: driver - full name"
    for m in re.finditer(r'^\s*(\d+)\s*\[([^\]]+)\]\s*:\s*(\S+)\s*-\s*(.+?)\s*$',
                         text, re.M):
        idx, cid, driver, name = m.groups()
        n = int(idx)
        cap, play = pcm_kinds(n)
        cards.append({
            "index":    n,
            "id":       cid.strip(),
            "name":     name.strip(),
            "driver":   driver.strip(),
            "capture":  cap,
            "playback": play,
            "usb":      "usb" in (driver + name).lower(),
            "alsa":     f"hw:{n},0",
        })

    # ── RTL-SDR feed service (not an ALSA card; check systemd) ──────────
    # aprs-sdr-feed.service pipes rtl_fm audio to GrayWolf via sdr_udp UDP.
    # If it is active we expose it as a synthetic capture-only card so the UI
    # can show green even when zero ALSA sound cards are attached.
    import subprocess as _sp
    SDR_SERVICE = "aprs-sdr-feed.service"
    try:
        rc = _sp.run(
            ["systemctl", "is-active", "--quiet", SDR_SERVICE],
            timeout=2,
        ).returncode
        if rc == 0:
            cards.append({
                "index":    -1,
                "id":       "sdr",
                "name":     "RTL-SDR (aprs-sdr-feed)",
                "driver":   "rtlsdr",
                "capture":  True,
                "playback": False,
                "usb":      True,
                "alsa":     "sdr_udp",
                "sdr":      True,
            })
    except Exception:
        pass

    return jsonify({"ok": True, "supported": True, "cards": cards})


