#!/usr/bin/env python3
"""
api-probe.py — call every /api/* endpoint on a RUNNING station and write what
came back, for a human to read and for two boxes to be diffed against each other.

docs/api-contract.md §11. tests/test_api_contract.py reads the SOURCE: it proves
what we wrote, not what the server puts on the wire. This is the other half.

    python3 scripts/api-probe.py --host 192.168.1.28
    python3 scripts/api-probe.py --host pi4oasis --out before-event.txt
    diff <(…station A…) <(…station B…)      # shapes must match, data need not

SAFE BY DEFAULT. Endpoints are tiered by what calling them DOES, not by HTTP
method — a GET that runs `rtl_test -t` seizes the dongle and can knock APRS off
the air, which is not "read-only" on a live station:

    read     safe to call on a running station at any time          (default)
    mutate   changes state, writes a file, or seizes hardware       --mutate
    danger   transmits, reboots, installs, deletes mail, or can cut
             the very connection you are probing over               --danger

A probe that reboots the station it is probing is not a probe.

Stdlib only, so it runs from a Mac, from the Pi itself, or from a rescue shell
with nothing installed.
"""

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)

# ── Tiering ──────────────────────────────────────────────────────────────────
# Anything not named here is treated as `read` when it is a GET, and `danger`
# when it is not — an unknown mutating route is guilty until classified, so a
# newly added endpoint can never be called by accident on the default run.

MUTATE = {
    "/api/forms/save", "/api/save-ics205", "/api/save-chirp",
    "/api/satellites/select", "/api/satellites/bells", "/api/satellites/refresh",
    "/api/hardware/guardian/cancel", "/api/hardware/guardian/config",
    # GETs that are NOT read-only in effect: both run an exclusive `rtl_test -t`
    # scan, which takes the dongle away from whatever is using it.
    "/api/hardware/detect", "/api/setup/hardware-detect",
}

DANGER = {
    "/api/setup/reboot",                       # reboots the host
    "/api/setup/plan", "/api/setup/run", "/api/setup/cancel",   # installs software
    "/api/service",                            # starts/stops services
    "/api/hardware/stop-all", "/api/hardware/service-stop",
    "/api/hardware/assign", "/api/hardware/release", "/api/hardware/route",
    "/api/hardware/lock", "/api/hardware/burn-serial",          # writes dongle EEPROM
    "/api/wifi/connect", "/api/wifi/forget",   # can cut the link you are probing over
    "/api/winlink/connect", "/api/winlink/disconnect",          # keys the transmitter
    "/api/winlink/mailbox/out",                # SENDS radio email
    "/api/aprs/warnings",                      # broadcasts over RF
    "/api/satellites/listen",                  # seizes the SDR and records
    "/api/satellites/listen/stop",
    "/api/satellites/listen/stream",           # seizes the SDR, streams forever
}

# Routes we deliberately never call, with the reason printed in the report.
SKIP = {
    "/api/satellites/listen/stream": "would hold the dongle open for the whole run",
    "/api/fs/pmtiles": "range-reads map tiles; megabytes, no shape to check",
    "/api/browse": "directory listing, environment-specific by design",
    "/api/fs/browse": "directory listing, environment-specific by design",
}

# Query strings / path values for endpoints that need them. Values are chosen to
# exercise the SHAPE without depending on a particular station's data.
PARAMS = {
    "/api/health/probe":   "?service=kiwix&port=8081",
    "/api/health/service": "?name=oasis",
    "/api/health/binary":  "?name=rtl_test",
    "/api/health/file":    "?key=pat_config",
    "/api/forms/list":     "?kind=ics-205",
    "/api/lookup":         "?q=W4MHI",
    "/api/lookup/name":    "?q=SMITH",
    "/api/lookup/grid":    "?grid=EM73",
    "/api/lookup/prefix":  "?q=W4",
    "/api/aprs/track":     "?callsign=W4MHI&minutes=60",
    "/api/adsb/recent":    "?hours=1&limit=5",
    "/api/adsb/history":   "?hours=1&limit=5",
    "/api/adsb/aircraft":  "?limit=5",
    "/api/adsb/alerts":    "?limit=5",
    "/api/satellites":     "?limit=3",
    "/api/satellites/passes": "?window=6",
    "/api/satellites/track":  "?sat=25544",   # no from/to: probes the 400 shape
    "/api/satellites/listen/recordings": "?limit=5",
    "/api/winlink/rmslist": "?mode=ardop",
    "/api/aprs/stations":   "?limit=5",
}

# Placeholders for <path:params>. A deliberately absent id probes the NOT-FOUND
# shape, which is exactly the contract surface a model hits most often.
PATH_VALUES = {
    "box": "in", "mid": "PROBE-NO-SUCH-MID", "attachment": "probe.txt",
    "wid": "probe-no-such-warning", "job_id": "probe-no-such-job",
    "filename": "probe-no-such-file.wav",
}

# Values that differ every call or every box. Recorded, but replaced in the body
# so two environments diff on SHAPE rather than on the weather.
VOLATILE_KEYS = {
    "ts", "time", "now", "last_seen", "last_heard", "age_s", "seconds",
    "uptime_s", "uptime_sec", "boot_time", "computed_at", "ran_at",
    "elapsed_ms", "pps", "packets", "cpu_pct", "cpu_cores", "cpu_temp_c",
    "top_procs", "ram", "disk", "load", "seconds_left", "stats",
    "tle_age_days", "recorded_at", "drift_s", "offset_s", "samples_per_sec",
    "messages_per_min", "last_json_age_s", "hostname", "ip", "serial",
    "fcc_db_updated", "fcc_db_date", "detail", "error",
}

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_LIST_BOUNDS = ("total", "truncated", "limit")


# ── Route discovery ──────────────────────────────────────────────────────────
def discover_routes():
    """Every /api/* rule + methods, read from the source with the same AST
    scanner the contract gate uses. A hand-kept list here would drift from the
    real route table, and a route this script has never heard of is exactly the
    one worth probing."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))
    try:
        from api_contract_scan import scan_tree
    except ImportError:
        sys.exit("cannot import tests/api_contract_scan.py — run me from the repo")
    out = {}
    for rule, path, _fn, _ln, surface, _facts in scan_tree(REPO_ROOT):
        if surface != "oasis":
            continue                     # internal daemons are not the OASIS API
        src = open(path, encoding="utf-8").read()
        pat = r'@bp\.route\(\s*["\']' + re.escape(rule) + r'["\'](.*?)\)\s*\n'
        m = re.search(pat, src, re.S)
        methods = re.findall(r'["\'](GET|POST|PUT|DELETE|PATCH)["\']',
                             m.group(1) if m else "") or ["GET"]
        out.setdefault(rule, set()).update(methods)
    return {r: sorted(m) for r, m in sorted(out.items())}


# Rules whose GET is itself intrusive — the tier is about EFFECT, not verb.
INTRUSIVE_GET = {
    "/api/hardware/detect",        # exclusive `rtl_test -t`: takes the dongle
    "/api/setup/hardware-detect",  # same scan
    "/api/satellites/listen/stream",
}


def tier_of(rule, method):
    """Tier a (rule, METHOD) pair, not a rule.

    GET /api/aprs/warnings just lists the operator's map pins; POST to the same
    rule BROADCASTS OVER RF. Tiering by rule would either skip a useful read or
    — far worse — let a verb through because its neighbour was harmless.
    """
    if method == "GET":
        return "mutate" if rule in INTRUSIVE_GET else "read"
    if rule in DANGER:
        return "danger"
    if rule in MUTATE:
        return "mutate"
    return "danger"          # unclassified and mutating → never by accident


def concrete_path(rule):
    """Substitute <path:params> with probe values."""
    def sub(m):
        name = m.group(1).split(":")[-1]
        return PATH_VALUES.get(name, "probe")
    return re.sub(r"<([^>]+)>", sub, rule)


# ── Response shaping ─────────────────────────────────────────────────────────
def type_skeleton(value, depth=0):
    """Keys and TYPES, recursively — the thing that must match across boxes even
    when the data does not. A list becomes its first element's skeleton, since a
    homogeneous list is what every OASIS list endpoint returns."""
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        return {k: type_skeleton(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [type_skeleton(value[0], depth + 1)] if value else []
    if value is None:
        return "null"
    return type(value).__name__


def redact(value, key=None, found=None, path=""):
    """Replace volatile values with a marker, collecting what they really were."""
    if found is None:
        found = []
    if isinstance(value, dict):
        return ({k: redact(v, k, found, f"{path}.{k}")[0]
                 for k, v in sorted(value.items())}, found)
    if isinstance(value, list):
        return [redact(v, key, found, f"{path}[{i}]")[0] for i, v in enumerate(value)], found
    if key in VOLATILE_KEYS and value is not None:
        found.append((path, value))
        return "<volatile>", found
    return value, found


def check_contract(rule, status, body):
    """The runtime half of the gate. Returns a list of violation strings."""
    bad = []
    if not isinstance(body, dict):
        return ["response is not a JSON object (§1)"]
    if "ok" not in body:
        bad.append("no `ok` envelope (§1)")
    else:
        ok = body["ok"]
        if ok is False and status == 200:
            bad.append("ok:false served with HTTP 200 (§2)")
        if ok is True and "error" in body:
            bad.append("ok:true carrying an `error` key (§2)")
        if ok is False and not body.get("code"):
            bad.append("ok:false with no stable `code` (§3)")
        if ok is True and status >= 400:
            bad.append(f"ok:true served with HTTP {status} (§2)")
    # §4 — a list-bearing response declares its bounds.
    for key, val in body.items():
        if isinstance(val, list) and key not in ("errors", "warnings", "lines",
                                                 "capabilities", "groups",
                                                 "missing_deps", "cpu_cores",
                                                 "top_procs", "aliases",
                                                 "otherDetected", "path"):
            missing = [b for b in _LIST_BOUNDS if b not in body]
            if missing:
                bad.append(f"list `{key}` without {'/'.join(missing)} (§4)")
            break
    # §6 — anything that looks like a timestamp is ISO-8601 UTC.
    for key, val in body.items():
        if key in ("time", "last_seen", "last_heard", "boot_time", "computed_at",
                   "ran_at", "recorded_at", "fcc_db_updated") and val is not None:
            if not (isinstance(val, str) and _ISO_RE.match(val)):
                bad.append(f"`{key}` is not ISO-8601 UTC (§6): {val!r}")
    return bad


# ── Probing ──────────────────────────────────────────────────────────────────
def call(base, method, path, timeout):
    url = base + path
    req = urllib.request.Request(url, method=method)
    req.add_header("User-Agent", "OASIS-api-probe/1.0")
    req.add_header("X-OASIS-Request", "1")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except Exception as exc:                       # noqa: BLE001 — report anything
        return None, str(exc), round((time.monotonic() - t0) * 1000)
    ms = round((time.monotonic() - t0) * 1000)
    try:
        return status, json.loads(raw), ms
    except ValueError:
        return status, raw[:400].decode("utf-8", "replace"), ms


def probe_one(w, base, rule, method, args, counts, violations):
    """Probe one (rule, METHOD) pair and append its record."""
    tier = tier_of(rule, method)
    label = f"{method} {rule}"
    if tier == "danger" and not args.danger:
        w(f"\n--- {label}\n    skipped [danger] — needs --danger")
        counts["skipped"] += 1
        return
    if tier == "mutate" and not args.mutate:
        w(f"\n--- {label}\n    skipped [mutate] — needs --mutate")
        counts["skipped"] += 1
        return

    path = concrete_path(rule) + PARAMS.get(rule, "")
    status, body, ms = call(base, method, path, args.timeout)
    counts["probed"] += 1

    w(f"\n--- {label}   [{tier}]")
    w(f"    {method} {path}")
    if status is None:
        w(f"    TRANSPORT FAILURE after {ms} ms: {body}")
        counts["failed"] += 1
        return
    w(f"    HTTP {status}   {ms} ms")

    if not isinstance(body, (dict, list)):
        w(f"    non-JSON body: {body!r}")
        return

    if isinstance(body, dict):
        bad = check_contract(rule, status, body)
        if bad:
            counts["violations"] += len(bad)
            for b in bad:
                violations.append(f"{method} {rule}: {b}")
                w(f"    ** CONTRACT: {b}")
    else:
        counts["violations"] += 1
        violations.append(f"{method} {rule}: bare JSON array (§1)")
        w("    ** CONTRACT: response is a bare JSON array (§1)")

    w("    shape:")
    for line in json.dumps(type_skeleton(body), indent=2, sort_keys=True).splitlines():
        w("      " + line)
    clean, vol = redact(body)
    w("    body:")
    for line in json.dumps(clean, indent=2, sort_keys=True).splitlines():
        w("      " + line)
    if vol:
        w("    volatile (excluded above so environments diff cleanly):")
        for pth, val in vol[:40]:
            w(f"      {pth.lstrip('.')} = {val!r}")
        if len(vol) > 40:
            w(f"      … and {len(vol) - 40} more")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True, help="station host or IP")
    ap.add_argument("--port", type=int, default=8083)
    ap.add_argument("--out", help="write here instead of stdout")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--only", help="probe only rules containing this substring")
    ap.add_argument("--mutate", action="store_true",
                    help="also call endpoints that change state or seize hardware")
    ap.add_argument("--danger", action="store_true",
                    help="ALSO call endpoints that transmit, reboot, install, "
                         "delete mail, or can cut your connection. Implies --mutate.")
    args = ap.parse_args()
    if args.danger:
        args.mutate = True

    base = f"http://{args.host}:{args.port}"
    routes = discover_routes()
    out = []
    w = out.append

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    w(f"OASIS API probe — {base}")
    w(f"run at {stamp}")
    w(f"tiers enabled: read{' + mutate' if args.mutate else ''}"
      f"{' + DANGER' if args.danger else ''}")
    w(f"{len(routes)} routes known")
    w("=" * 78)

    counts = {"probed": 0, "skipped": 0, "failed": 0, "violations": 0}
    violations = []

    for rule, methods in routes.items():
        if args.only and args.only not in rule:
            continue
        if rule in SKIP:
            w(f"\n--- {rule}\n    SKIPPED — {SKIP[rule]}")
            counts["skipped"] += 1
            continue
        for method in methods:
            probe_one(w, base, rule, method, args, counts, violations)

    w("\n" + "=" * 78)
    w(f"probed {counts['probed']}, skipped {counts['skipped']}, "
      f"transport failures {counts['failed']}, contract violations {counts['violations']}")
    if violations:
        w("\nCONTRACT VIOLATIONS")
        for v in violations:
            w("  " + v)

    text = "\n".join(out) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {args.out}  "
              f"({counts['probed']} probed, {counts['violations']} violations)")
    else:
        sys.stdout.write(text)

    # Loud failure: §11 requires this to double as the runtime half of the gate.
    return 1 if (violations or counts["failed"]) else 0


if __name__ == "__main__":
    sys.exit(main())
