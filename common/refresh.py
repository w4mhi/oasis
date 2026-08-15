"""Source registry, pass runner, and lock for the OASIS auto-update loop.

The pass runner is the only thing that writes datasets, and BOTH the background
thread (server/routes/refresh.py) and scripts/refresh-data.py can call it — so
it takes a lock. Atomic writes alone are not enough here: that was the finding
in the roster atomic-write fix, where atomic writes still lost updates because
two writers each read, then each wrote.

Adapters wrap scripts that already work rather than reimplementing them. An
adapter's contract is: invoke, report success or failure, and NEVER partially
clobber good data. A failed refresh must leave the previous dataset intact and
usable — losing the callsign database in the field because a hotspot dropped at
80% is the worst outcome this feature could produce.
"""

import contextlib
import errno
import os
import shutil
import subprocess
import sys
import time
from collections import namedtuple

from common import config_paths, freshness as F
from common.atomic_json import read_json, write_json

Source = namedtuple("Source", ["id", "label", "max_age_days", "tier",
                               "credential", "probe", "fetch", "attribution"])


# ── state persistence ────────────────────────────────────────────────────────
def load_state(repo_root):
    """Per-source counters: last attempt, last success, last error, failures.

    A corrupt file degrades to {} rather than raising — a truncated write from a
    power loss must never take the server down at import time.
    """
    data = read_json(config_paths.refresh_state_json(repo_root), default={})
    return data if isinstance(data, dict) else {}


def save_state(repo_root, state):
    write_json(config_paths.refresh_state_json(repo_root), state)


def station_config(repo_root):
    cfg = read_json(config_paths.station_json(repo_root), default={})
    return cfg if isinstance(cfg, dict) else {}


def max_age_for(cfg, source):
    """Per-source threshold, overridable from station.json's max_age_days."""
    overrides = cfg.get("max_age_days") or {}
    try:
        return float(overrides[source.id])
    except (KeyError, TypeError, ValueError):
        return source.max_age_days


def credential_for(cfg, source):
    """The operator's token for this source, or None.

    A blank string counts as absent: the shipped template carries empty token
    fields, and those must read as "switched off", not as "configured".
    """
    if not source.credential:
        return None
    return (cfg.get(source.credential) or "").strip() or None


# ── lock ─────────────────────────────────────────────────────────────────────
@contextlib.contextmanager
def pass_lock(repo_root):
    """Yield True if this caller owns the pass, False if one is already running.

    SKIP, DO NOT QUEUE: a refresh five minutes late is fine; two simultaneous
    160 MB FCC downloads are not. The operator can run the CLI by hand while the
    background thread is mid-pass, which is a genuine concurrent-writer case the
    resource guardian never faces (the guardian only reads).
    """
    path = os.path.join(config_paths.config_dir(repo_root), ".refresh.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = None
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        # Stale lock from a hard kill (SIGKILL, power cut) — reclaim after an
        # hour so a crash cannot wedge updates forever.
        try:
            if time.time() - os.path.getmtime(path) > 3600:
                os.unlink(path)
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except OSError:
            fd = None
    if fd is None:
        yield False
        return
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        fd = None
        yield True
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(path)
        except OSError:
            pass


# ── the pass ─────────────────────────────────────────────────────────────────
def run_pass(repo_root, *, now, metered, registry=None, only=None, force=False,
             dry_run=False):
    """Evaluate every source and fetch the ones that are due.

    `ok` reports that the pass RAN, not that the data is current — an offline
    pass returns ok:true with per-source reasons. Data freshness is the payload,
    never the status code.
    """
    registry = REGISTRY if registry is None else registry
    cfg = station_config(repo_root)
    state = load_state(repo_root)
    rows = []

    for src in registry:
        if only and src.id not in only:
            continue
        row = _evaluate(repo_root, cfg, state, src, now, metered)
        due = force or (F.is_due(row["state"]) and not row["backoff_active"])
        if due and not dry_run:
            row.update(_attempt(repo_root, cfg, state, src, now))
        rows.append(row)

    if not dry_run:
        save_state(repo_root, state)
    return {"ok": True, "checked_at": now, "metered": metered,
            "sources": rows}


def _evaluate(repo_root, cfg, state, src, now, metered):
    try:
        mtime = src.probe(repo_root)
    except Exception:
        # A probe that cannot read its own data is indistinguishable from data
        # that is not there — both mean "we have nothing usable".
        mtime = None
    age = F.age_days(mtime, now)
    cred = credential_for(cfg, src)
    st = F.verdict(age, max_age_for(cfg, src),
                   has_credential=cred is not None,
                   needs_credential=bool(src.credential),
                   tier=src.tier, metered=metered)
    prev = state.get(src.id, {})
    fails = int(prev.get("consecutive_failures") or 0)
    wait = F.backoff_seconds(fails)
    last = float(prev.get("last_attempt") or 0.0)
    # No recorded attempt means there is nothing to back off FROM. Without this
    # guard a failure counter with no timestamp reads as "backing off since the
    # epoch", which would suppress the very retry that clears it.
    backoff_active = bool(wait) and last > 0.0 and (now - last) < wait
    return {
        "id": src.id, "label": src.label, "state": st, "tier": src.tier,
        "age_days": age, "max_age_days": max_age_for(cfg, src),
        "attribution": src.attribution, "fetched": False,
        "error": prev.get("last_error"),
        "last_success": prev.get("last_success"),
        "backoff_active": backoff_active,
    }


def _attempt(repo_root, cfg, state, src, now):
    rec = state.setdefault(src.id, {})
    rec["last_attempt"] = now
    try:
        ok = bool(src.fetch(repo_root, cfg))
    except Exception as exc:
        ok, err = False, f"{type(exc).__name__}: {exc}"
    else:
        err = None if ok else "fetch reported failure"
    if ok:
        rec["consecutive_failures"] = 0
        rec["last_success"] = now
        rec["last_error"] = None
    else:
        rec["consecutive_failures"] = int(
            rec.get("consecutive_failures") or 0) + 1
        rec["last_error"] = err
    return {"fetched": ok, "error": err}


def _by_id(sid):
    return [s for s in REGISTRY if s.id == sid][0]


# ── shared adapter helpers ───────────────────────────────────────────────────
def _newest_mtime(path):
    """Newest mtime under a directory, or of a file. None if absent or empty."""
    if not os.path.exists(path):
        return None
    if os.path.isfile(path):
        return os.path.getmtime(path)
    times = [os.path.getmtime(os.path.join(path, n))
             for n in os.listdir(path)
             if os.path.isfile(os.path.join(path, n))]
    return max(times) if times else None


def _run_script(repo_root, rel_path, timeout):
    """Run a repo script in a subprocess; True on exit 0.

    Timeouts and OS errors are failures, not crashes: a refresher must never
    take the server down, and the caller records the reason.
    """
    script = os.path.join(repo_root, *rel_path)
    try:
        proc = subprocess.run([sys.executable, script], capture_output=True,
                              text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


# ── satellites: one script refreshes both sources ────────────────────────────
def probe_tle(repo_root):
    return _newest_mtime(config_paths.tle_cache_dir(repo_root))


def probe_satnogs(repo_root):
    return _newest_mtime(config_paths.satellites_json(repo_root))


def fetch_roster(repo_root, cfg):
    """Run build-roster.py, which refreshes the CelesTrak TLE cache AND the
    SatNOGS roster in one pass — hence one fetch shared by two sources.

    Online-only by design; the runtime only reads what it produces, so a failed
    run leaves the previous roster intact and usable.
    """
    return _run_script(repo_root, ("services", "satellites",
                                   "build-roster.py"), timeout=300)


# ── metered link detection ───────────────────────────────────────────────────
def is_metered():
    """True if large downloads must be deferred to an operator tap.

    FAILS CLOSED, deliberately. nmcli reports yes / no / guess-yes / guess-no /
    unknown, and unknown is the common answer for exactly the phone hotspots
    that matter most — plus nmcli does not exist at all in portable mode on
    macOS or Windows. Only a definite "no" unlocks a large download, so an
    undetected hotspot can never cost 160 MB of someone's data plan.

    NOT YET MEASURED on real hardware: if a Pi on an Android and an iPhone
    hotspot both report "unknown", this check is decorative and the honest
    description of the feature is "large downloads are always one-tap".
    """
    try:
        proc = subprocess.run(
            ["nmcli", "-t", "-f", "GENERAL.METERED", "general"],
            capture_output=True, text=True, timeout=5)
    except Exception:
        return True
    if proc.returncode != 0:
        return True
    text = (proc.stdout or "").strip().lower()
    if not text:
        return True
    value = text.split(":", 1)[-1].strip()
    # "no (4)" and "no (guessed) (2)" are the only unmetered answers.
    return not value.startswith("no")


# ── FCC callsign database ────────────────────────────────────────────────────
# The ULS dump is ~160 MB, plus room to extract and index it. Refuse rather than
# half-fill an SD card and take the whole station down.
FCC_REQUIRED_BYTES = 600 * 1024 * 1024


def free_bytes(path):
    """Free bytes on the filesystem that would hold `path`.

    Walks up to the nearest existing parent, since the target directory may not
    exist yet on a fresh install.
    """
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            return 0
        path = parent
    return shutil.disk_usage(path).free


def probe_fcc(repo_root):
    return _newest_mtime(os.path.join(repo_root, "services", "fcc_database",
                                      "data", "EN.dat"))


def fetch_fcc(repo_root, cfg):
    """Re-run the FCC installer, which re-downloads and re-indexes.

    The installer writes the new files itself, so a failed run leaves the
    existing database in place and queryable — losing callsign lookup in the
    field because a hotspot dropped mid-download is the worst outcome available.
    """
    data_dir = os.path.join(repo_root, "services", "fcc_database", "data")
    if free_bytes(data_dir) < FCC_REQUIRED_BYTES:
        return False
    return _run_script(repo_root, ("services", "fcc_database", "install.py"),
                       timeout=3600)


def _not_implemented(*_a, **_kw):
    raise NotImplementedError("adapter wired in a later task")


REGISTRY = [
    Source(id="tle", label="Satellite TLEs (CelesTrak)", max_age_days=3.0,
           tier="small", credential=None, probe=probe_tle,
           fetch=fetch_roster, attribution="TLE data from CelesTrak."),
    Source(id="satnogs", label="Satellite transmitters (SatNOGS)",
           max_age_days=30.0, tier="small", credential=None,
           probe=probe_satnogs, fetch=fetch_roster,
           attribution="Transmitter data from SatNOGS (CC BY-SA)."),
    Source(id="fcc", label="FCC callsign database", max_age_days=14.0,
           tier="large", credential=None, probe=probe_fcc, fetch=fetch_fcc,
           attribution="Data from the FCC ULS."),
    Source(id="repeaterbook", label="RepeaterBook directory",
           max_age_days=180.0, tier="large", credential="repeaterbook_token",
           probe=_not_implemented, fetch=_not_implemented,
           attribution="Data courtesy of RepeaterBook.com"),
]
