"""Satellites blueprint: the /server/satellites/ page + /api/satellites/* JSON.
Hardware-free (Phase 1) — always available, no feature gate."""
import json
import os

from flask import Blueprint, jsonify, request, send_from_directory  # noqa: F401

import appconfig
from common import config_paths
from common.api_shape import clamp_limit, iso_utc
from common.web_guard import require_oasis_request

_HERE = os.path.dirname(os.path.abspath(__file__))
import sys
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import roster        # noqa: E402
import tle           # noqa: E402

SUITE_ROOT = appconfig.SUITE_ROOT
STATIC_DIR = os.path.join(_HERE, "static")

bp = Blueprint("satellites", __name__)

# Contract §4. A real roster is ~150 birds and every consumer renders all of
# them, so these bound the response rather than paginate it.
_ROSTER_DEFAULT_LIMIT = 2000
_ROSTER_MAX_LIMIT = 5000
# Recordings are size-capped on disk (2 GB budget, see listen.prune_recordings),
# so the list is short by construction; the bound is belt-and-braces.
_RECORDINGS_DEFAULT_LIMIT = 500
_RECORDINGS_MAX_LIMIT = 2000


def _station():
    try:
        with open(config_paths.station_json(SUITE_ROOT), encoding="utf-8") as fh:
            s = json.load(fh)
        return {"lat": float(s["lat"]), "lon": float(s["lon"])}
    except (OSError, ValueError, KeyError):
        return {"lat": None, "lon": None}


# Minimum culmination for a pass to be LISTED at all, in degrees.
#
# Site-specific by nature, which is why it cannot be right in the source: what
# makes a low pass unworkable is the OPERATOR'S horizon — treeline, roofline, the
# hill to the north-east — not anything about the satellite. A station looking out
# over water can drop this; one in a valley must raise it. 10 degrees is the
# long-standing default and stays the default, so an untouched install behaves
# exactly as before.
#
# Set "min_elev" in configuration/station.json. Note this is one number for a
# horizon that is not a circle — you may see 5 degrees to the south over water
# and nothing under 20 to the north. A real azimuth-dependent mask is a much
# larger feature; this gets most of the value.
_DEFAULT_MIN_ELEV_DEG = 10.0
_MAX_MIN_ELEV_DEG = 89.0


def _min_elev():
    """The pass-listing floor from station.json, or the default.

    Anything missing, unreadable, non-numeric or out of range falls back rather
    than raising. A typo in a config file must not take the pass list down — and
    a floor of 89 (or -5) would silently empty it, which on screen is
    indistinguishable from a broken predictor."""
    try:
        with open(config_paths.station_json(SUITE_ROOT), encoding="utf-8") as fh:
            v = float(json.load(fh)["min_elev"])
    except (OSError, ValueError, KeyError, TypeError):
        return _DEFAULT_MIN_ELEV_DEG
    return v if 0.0 <= v <= _MAX_MIN_ELEV_DEG else _DEFAULT_MIN_ELEV_DEG


@bp.route("/server/satellites/")
@bp.route("/server/satellites/<path:filename>")
def satellites_static(filename="satellites.html"):
    return send_from_directory(STATIC_DIR, filename)


@bp.route("/api/satellites")
def api_satellites():
    import listen
    # One-shot, here rather than at boot because the service has no init hook and
    # this is the read EVERY screen makes — including the kiosk, which never opens
    # the Satellites page and is the one that most needs its alerts armed. It is a
    # no-op on all but the first call (a flag in the roster), and it takes the same
    # write lock as /select, so a burst on load cannot race it.
    data = roster.apply_bell_default_once(config_paths.satellites_json(SUITE_ROOT))
    # Attach each roster entry's TLE lines (matched by NORAD id) so the client
    # can propagate live look-angles itself (satellite.js) for the workability
    # pill — no per-satellite server round-trip. None when not in the cache.
    # Also tag each downlink with v1 support (FM family) + a blurb so the roster
    # buttons ghost unsupported modes without their own table.
    by_norad = tle.index_by_norad(tle.load_cache(config_paths.tle_cache_dir(SUITE_ROOT)))
    sats = []
    for s in data["satellites"]:
        entry = by_norad.get(s["norad"])
        s = dict(s)
        s["l1"] = entry[1] if entry else None
        s["l2"] = entry[2] if entry else None
        # Decorate with mode support FIRST, then group: the grouping key is
        # (frequency, demod), and `demod` is what mode_support resolves. Grouping
        # earlier would have to guess which modes capture identically.
        s["downlinks"] = roster.group_downlinks(
            [dict(d, **listen.mode_support(d.get("mode")))
             for d in roster.legacy_downlinks(s)])
        sats.append(s)
    # Fallback ONLY when the roster is empty: the offline bundle ships the TLE cache
    # (so /api/satellites/passes predicts offline) but leaves satellites.json empty
    # until the first ONLINE build-roster run (SatNOGS metadata + RTL-range filter).
    # Without this the page is blank on a fresh box even though passes track 130+
    # sats. Surface each cached sat as a bare record (name + NORAD from the TLE, no
    # labels/downlinks). A POPULATED roster is authoritative and left untouched —
    # never augmented with TLE sats build-roster deliberately filtered out.
    if not sats:
        for norad, (name, l1, l2) in sorted(by_norad.items(), key=lambda kv: kv[1][0]):
            sats.append({"norad": norad, "name": name, "labels": [],
                         "downlinks": [], "l1": l1, "l2": l2})
    # §5, fixed key set: a roster written before an operator field existed has no
    # such key, so every consumer would have to guess whether a missing `bell`
    # meant "off" or "this build doesn't have bells". It means off.
    for s in sats:
        for f in roster.OPERATOR_FIELDS:
            s.setdefault(f, False)
    # §4: the roster is ~150 birds and every consumer wants all of them, so the
    # default is far above any real roster — the bound exists so the response
    # cannot become unbounded, not to paginate.
    limit = clamp_limit(request.args.get("limit"), _ROSTER_DEFAULT_LIMIT,
                        _ROSTER_MAX_LIMIT)
    shown = sats[:limit]
    return jsonify({
        "ok": True,
        "satellites": shown,
        "total": len(sats),
        "count": len(shown),
        "truncated": len(sats) > len(shown),
        "limit": limit,
        "tle_age_days": tle.cache_age_days(config_paths.tle_cache_dir(SUITE_ROOT)),
        "station": _station(),
    })


@bp.route("/api/satellites/refresh", methods=["POST"])
@require_oasis_request
def api_refresh():
    """Rebuild the satellite list from SatNOGS + CelesTrak on demand — backs the
    age pill. ONLINE-ONLY, the one operator-triggered exception to "runtime never
    fetches".

    §2: this is an ACTION, so ok:false is right when the rebuild does not happen
    — but it must not be HTTP 200. Being offline is not a bug in the request, so
    it is 503 (the host cannot do this now) rather than a 4xx, and the `code`
    tells the three failures apart: OFFLINE is "try later", REFRESH_FAILED is
    "something upstream broke", REFRESH_TIMED_OUT is "it is still slow"."""
    import subprocess
    from common.oasis_lib import has_internet
    cache_dir = config_paths.tle_cache_dir(SUITE_ROOT)
    if not has_internet():
        return jsonify({"ok": False, "error": "no internet connection",
                        "code": "OFFLINE",
                        "tle_age_days": tle.cache_age_days(cache_dir)}), 503
    script = os.path.join(_HERE, "build-roster.py")
    try:
        p = subprocess.run([sys.executable, script],
                           capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "refresh timed out",
                        "code": "REFRESH_TIMED_OUT",
                        "tle_age_days": tle.cache_age_days(cache_dir)}), 504
    if p.returncode != 0:
        # A mid-download failure (e.g. CelesTrak unreachable despite DNS) must not
        # 500 — surface it as a handled error the pill can show.
        return jsonify({"ok": False,
                        "error": (p.stderr or p.stdout or "refresh failed").strip()[-300:],
                        "code": "REFRESH_FAILED",
                        "tle_age_days": tle.cache_age_days(cache_dir)}), 502
    try:
        summary = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        summary = {}
    # §5: build-roster's summary is NESTED, not spread. Splatting it put keys
    # nobody declared into the envelope, so the response shape depended on a
    # subprocess's stdout — unknowable to a caller and to this file's reader.
    return jsonify({"ok": True, "tle_age_days": tle.cache_age_days(cache_dir),
                    "summary": summary if isinstance(summary, dict) else {}})


import datetime
import hashlib
import time

_CACHE_TTL_S = 6 * 3600   # recompute at most every 6 h (TLEs change ~every 3 days)
# Wall-clock budget for one /passes request. Kept well under gunicorn's default
# 30 s worker timeout: a full-roster propagation (150+ sats) cold used to overrun
# it, so the worker was killed at 500 before the cache was written — every retry
# then recomputed cold and the endpoint stayed stuck at 500. Each request now does
# a bounded chunk and persists progress (see api_passes).
_PASSES_BUDGET_S = 20
# On a compute exception we retry the sat on later polls instead of caching it as
# "no passes" — a transient SGP4/skyfield hiccup must not strand an otherwise good
# bird as permanently disabled (a genuine no-pass returns [] WITHOUT raising, so it
# still caches normally). After this many failed attempts we give up and cache [],
# so a truly unpropagatable (decayed / far-past-epoch) TLE isn't retried forever.
_MAX_PASS_RETRIES = 3


def _cache_path(key):
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    d = os.path.join(config_paths.tle_cache_dir(SUITE_ROOT), "_passes")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, h + ".json")


def _cache_plan(cached, now_ts, ttl):
    """Decide how to use an on-disk passes cache: returns (action, base_ts).

      'fresh'  — missing/garbled/legacy/older-than-ttl → discard prior and
                 recompute from base_ts == now_ts.
      'serve'  — fresh AND complete → return it as-is (base_ts == computed_at).
      'resume' — fresh but incomplete → keep filling the same window
                 (base_ts == computed_at, so resumed sats align with the rest).

    Passes are ABSOLUTE-time predictions computed at `computed_at`. Once an entry
    is older than the TTL its whole window has elapsed, so it MUST be recomputed —
    reusing it (as the old 'fill only missing sats' path did for a complete cache)
    freezes the window until every pass slides into the past and the roster goes
    dark. Recomputing on staleness is what rolls the prediction window forward."""
    computed_at = cached.get("computed_at") if isinstance(cached, dict) else None
    if computed_at is None or (now_ts - computed_at) >= ttl:
        return "fresh", now_ts
    if cached.get("complete"):
        return "serve", computed_at
    return "resume", computed_at


def _sats_by_norad(selected_first=False):
    """{norad: EarthSatellite} for roster entries present in the TLE cache,
    matched by NORAD id (not name — CelesTrak's names differ from the roster's,
    e.g. 'SAUDISAT 1C (SO-50)' vs the roster's 'SO-50').

    selected_first orders monitored sats ahead of the rest (dict preserves
    insertion order) so a budgeted /passes computation reaches the ones the
    dashboards actually need before the time cutoff."""
    import predict
    by_norad = tle.index_by_norad(tle.load_cache(config_paths.tle_cache_dir(SUITE_ROOT)))
    data = roster.load(config_paths.satellites_json(SUITE_ROOT))
    entries = data["satellites"]
    if selected_first:
        entries = sorted(entries, key=lambda s: not s.get("selected"))
    out = {}
    for s in entries:
        entry = by_norad.get(s["norad"])
        if entry:
            name, l1, l2 = entry
            try:
                out[s["norad"]] = predict.make_satellite(name, l1, l2)
            except Exception:
                # A malformed/unparsable TLE must not take down the roster lookup —
                # just skip that satellite.
                continue
    # Fallback when the roster yields nothing (never-synced offline box, empty
    # satellites.json): predict straight from the TLE cache so /passes AND /track
    # still work — otherwise selected sats plot with null l1/l2 and draw no orbit.
    # Mirrors the same fallback in api_satellites; a populated roster is
    # authoritative and never augmented with the TLE cache's filtered-out sats.
    if not out:
        for norad, (name, l1, l2) in by_norad.items():
            try:
                out[norad] = predict.make_satellite(name, l1, l2)
            except Exception:
                continue
    return out


@bp.route("/api/satellites/passes")
def api_passes():
    # `import predict` (skyfield/numpy) is deferred to the point of ACTUAL USE,
    # deep in the compute loop below — not here. It is an optional dependency: a
    # box that has not run install-predict.py still serves this endpoint, and the
    # paths that never propagate (no station configured, a fresh cache hit, an
    # empty roster) must honour the contract rather than 500 on an import for
    # work they were never going to do.
    try:
        window = int(request.args.get("window", 48))
    except (TypeError, ValueError):
        window = 48
    st = _station()
    # Resolved before the early return so the floor is reported on BOTH paths —
    # one response shape, and the operator can read back what the config did
    # without having to infer it from how many passes appeared.
    min_elev = _min_elev()
    if st["lat"] is None:
        # §2: a station with no location configured is a STATE, not a failed
        # request — nothing about the call was wrong. `computed:false` says the
        # prediction never ran, so an empty `passes` cannot be misread as
        # "nothing is overhead for the next 48 hours".
        return jsonify({"ok": True, "passes": {}, "computed": False,
                        "complete": False, "computed_at": None,
                        "window_h": window, "count": 0,
                        "min_elev": min_elev,
                        "reason": "no-station-location"}), 200
    only = request.args.get("sat")
    tle_stamp = tle.cache_mtime(config_paths.tle_cache_dir(SUITE_ROOT))
    # min_elev is PART OF THE KEY: it changes which passes exist, so a cache
    # written under the old floor is not an answer to the new question. Without
    # it, editing station.json would appear to do nothing for up to the 6 h TTL.
    key = (f"{st['lat']},{st['lon']},{window},{only},"
           f"{int(tle_stamp) if tle_stamp else 0},{min_elev}")
    cp = _cache_path(key)
    start = datetime.datetime.now(datetime.timezone.utc)
    now_ts = start.timestamp()

    cached = {}
    if os.path.exists(cp):
        try:
            with open(cp, encoding="utf-8") as fh:
                cached = json.load(fh) or {}
        except (OSError, ValueError):
            cached = {}
    action, base_ts = _cache_plan(cached, now_ts, _CACHE_TTL_S)
    if action == "serve":                # fresh + complete → hand back the cache
        result = cached
    else:
        # Resume the same window (base_ts == its computed_at) or start a new one
        # (base_ts == now). Compute only the sats not already present, under a
        # wall-clock budget below the worker timeout. Selected sats go first, so the
        # dashboards get their passes on the first poll; the remainder fill in over the
        # next couple of polls. A partial result still returns 200 — never 500 — and
        # progress is persisted (computed_at pins the window) so it completes across
        # requests and, once older than the TTL, is recomputed rather than frozen.
        if action == "resume":
            prior = dict(cached.get("passes", {}))
            prior_err = dict(cached.get("errors", {}))   # nk -> consecutive-failure count
        else:                                # "fresh"
            prior, prior_err = {}, {}
        base = datetime.datetime.fromtimestamp(base_ts, datetime.timezone.utc)
        result = {"passes": dict(prior), "errors": dict(prior_err), "computed_at": base_ts}
        deadline = time.monotonic() + _PASSES_BUDGET_S
        complete = True
        for norad, sat in _sats_by_norad(selected_first=True).items():
            nk = str(norad)
            if only and nk != str(only):
                continue
            if nk in result["passes"]:
                continue                 # already computed (fresh or a prior chunk)
            if result["errors"].get(nk, 0) >= _MAX_PASS_RETRIES:
                result["passes"][nk] = []  # kept failing → treat as no-pass, stop retrying
                result["errors"].pop(nk, None)
                continue
            if time.monotonic() > deadline:
                complete = False         # out of budget — finish on the next poll
                break
            try:
                import predict   # optional dep — see the note at the top of this view
                result["passes"][nk] = predict.compute_passes(
                    sat, st["lat"], st["lon"], base, hours=window, min_elev=min_elev)
                result["errors"].pop(nk, None)      # recovered — clear prior failures
            except Exception:
                # A stale/decayed TLE can make SGP4 propagation blow up (e.g. far past
                # epoch). One bad satellite must never 500 the whole response — but a
                # TRANSIENT failure must not strand a good bird as permanently disabled,
                # so DON'T cache [] here: count the failure, leave the bird out, and let
                # a later poll retry it (up to _MAX_PASS_RETRIES) with the cache kept in
                # fill mode. Only a persistent failure (cap reached, above) seals as [].
                result["errors"][nk] = result["errors"].get(nk, 0) + 1
                complete = False
        result["complete"] = complete
        with open(cp, "w", encoding="utf-8") as fh:
            json.dump(result, fh)

    # ONE exit, one literal dict — the serve and compute paths converge here so
    # the two cannot drift, and §10 is satisfied at the return site rather than
    # inside a helper (`jsonify(_helper(...))` is exactly as invisible to a
    # reader, and to this repo's gate, as `jsonify(<variable>)`).
    #
    # Two things deliberately do NOT leave the cache file. `errors` is retry
    # bookkeeping — a per-satellite consecutive-failure counter used to decide
    # when to stop retrying a decayed TLE — and means nothing to a caller.
    # `computed_at` is stored as an epoch because _cache_plan does arithmetic on
    # it, but the wire gets ISO-8601 UTC (§6).
    passes = result.get("passes") or {}
    return jsonify({
        "ok": True,
        "passes": passes,
        "count": len(passes),
        "computed": True,
        # False while a budgeted run is still filling the roster across polls —
        # the result is usable, just not finished.
        "complete": bool(result.get("complete")),
        "computed_at": iso_utc(result.get("computed_at")),
        "window_h": window,
        # The floor these passes were computed under. On the wire because "no
        # pass in 24 h" and "no pass ABOVE 25 DEGREES in 24 h" are different
        # statements, and only one of them is a reason to check the antenna.
        "min_elev": min_elev,
        "reason": None,
    })


@bp.route("/api/satellites/track")
def api_track():
    # Deferred for the same reason as /passes: this view's whole point is that it
    # validates BEFORE it works, and importing an optional dependency on entry
    # threw that away — on a box without skyfield, a malformed date range came
    # back 500 where the contract says 400.
    st = _station()
    norad = request.args.get("sat")
    try:
        norad_i = int(norad) if norad else None
    except (TypeError, ValueError):
        norad_i = None
    # `from`/`to` were read as request.args["from"] and parsed unguarded, so a
    # missing param raised KeyError and a malformed one ValueError — both a bare
    # HTTP 500 on what is plainly a bad request. Validate BEFORE the work.
    try:
        frm = datetime.datetime.fromisoformat(request.args["from"])
        to = datetime.datetime.fromisoformat(request.args["to"])
    except KeyError:
        return jsonify({"ok": False, "error": "from and to are required "
                                              "(ISO-8601)",
                        "code": "MISSING_TIME_RANGE"}), 400
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "from and to must be ISO-8601",
                        "code": "INVALID_TIME_RANGE"}), 400
    if to <= frm:
        return jsonify({"ok": False, "error": "to must be after from",
                        "code": "INVALID_TIME_RANGE"}), 400
    if st["lat"] is None:
        # A state, not a bad request — same as /passes. Checked BEFORE the roster
        # lookup below: precedence is unchanged (no-station already outranked
        # unknown-satellite), but there is no reason to load the roster and the
        # whole TLE cache — which needs the predictor — to answer a request we
        # are about to decline anyway.
        return jsonify({"ok": True, "track": [], "count": 0, "norad": norad_i,
                        "l1": None, "l2": None,
                        "reason": "no-station-location"}), 200
    sat = _sats_by_norad().get(norad_i) if norad_i is not None else None
    if sat is None:
        # This one IS the caller's fault: they named a satellite that is not in
        # the roster or the TLE cache. Distinguishing it from the above is the
        # whole point — one is fixed in Setup, the other by asking differently.
        return jsonify({"ok": False, "error": "unknown satellite",
                        "code": "UNKNOWN_SATELLITE"}), 404
    data = roster.load(config_paths.satellites_json(SUITE_ROOT))
    entry = next((s for s in data["satellites"] if s["norad"] == norad_i), None)
    dls = roster.legacy_downlinks(entry) if entry else []
    dl = int(dls[0]["freq_mhz"] * 1_000_000) if dls else None
    try:
        import predict   # optional dep — see the note at the top of this view
        track = predict.compute_track(sat, st["lat"], st["lon"], frm, to,
                                       step_s=10, downlink_hz=dl)
    except Exception:
        # Same stale-TLE risk as passes — never 500, just return an empty track.
        track = []
    by_norad = tle.index_by_norad(tle.load_cache(config_paths.tle_cache_dir(SUITE_ROOT)))
    tle_lines = by_norad.get(norad_i)   # (name, l1, l2), matched by NORAD id
    return jsonify({
        "ok": True,
        "track": track,
        "count": len(track),
        "norad": norad_i,
        "l1": tle_lines[1] if tle_lines else None,
        "l2": tle_lines[2] if tle_lines else None,
        "reason": None,
    })


@bp.route("/api/satellites/select", methods=["POST"])
@require_oasis_request
def api_select():
    """Set which satellites are monitored — the roster is the single source of truth.

    Two accepted shapes:

        {"norad": 25544, "selected": true}                  one toggle
        {"selections": {"25544": true, "43017": false}}      a whole set, one write

    Use the bulk shape for anything touching more than one bird. The client used
    to fan a set out into one fire-and-forget request per satellite, which raced
    itself and lost most of them; the single-toggle shape is kept so a stale
    cached page keeps working.
    """
    body = request.get_json(force=True) or {}
    if "selections" in body:
        selections = body.get("selections") or {}
        if not isinstance(selections, dict):
            return jsonify({"ok": False, "error": "selections must be an object of "
                                                  "{norad: bool}",
                            "code": "INVALID_SELECTIONS"}), 400
    else:
        try:
            selections = {int(body["norad"]): bool(body["selected"])}
        except (KeyError, TypeError, ValueError):
            return jsonify({"ok": False, "error": "expected {norad, selected} or "
                                                  "{selections:{norad: bool}}",
                            "code": "INVALID_SELECTIONS"}), 400
    try:
        data = roster.set_selected_many(config_paths.satellites_json(SUITE_ROOT), selections)
    except OSError as exc:
        # Almost always a permissions problem: satellites.json left root-owned by
        # the privileged installer worker, so the non-root server can't rewrite it.
        # Surface the cause instead of a blank 500 (build-roster now chowns it to
        # the operator; this guards a stale file from an older bundle).
        return jsonify({"ok": False, "error": f"could not save selection: {exc} — is "
                        "configuration/satellites.json writable by the server user?",
                        "code": "ROSTER_NOT_WRITABLE"}), 500
    # This was `jsonify(data)` — the ENTIRE roster, ~150 birds with TLE lines,
    # echoed back for a single checkbox. The client discards it, so the only
    # thing it ever did was cost a Pi its bandwidth. Return what a caller
    # actually needs to confirm the write: which birds are now monitored.
    selected = sorted(s["norad"] for s in data.get("satellites", [])
                      if s.get("selected"))
    return jsonify({"ok": True, "selected": selected, "count": len(selected),
                    "applied": len(selections)})


@bp.route("/api/satellites/bells", methods=["POST"])
@require_oasis_request
def api_bells():
    """Arm/disarm pass alerts per satellite — the roster is the single source of
    truth, so a bell armed on a laptop reaches the shack kiosk.

    Two accepted shapes, mirroring /api/satellites/select:

        {"norad": 25544, "bell": true}                  one toggle
        {"bells": {"25544": true, "43017": false}}      a whole set, one write

    Use the bulk shape for anything touching more than one bird — the same rule
    that /select learned the hard way, where a set fanned out into one request
    per satellite raced itself and landed 1-2 of 20.

    The bell is per-BIRD and therefore shared. MUTING is not here: it is
    per-DEVICE and stays in the browser, so silencing the kiosk overnight does
    not silence a laptop (and vice versa).
    """
    body = request.get_json(force=True) or {}
    if "bells" in body:
        bells = body.get("bells") or {}
        if not isinstance(bells, dict):
            return jsonify({"ok": False, "error": "bells must be an object of "
                                                  "{norad: bool}",
                            "code": "INVALID_BELLS"}), 400
    else:
        try:
            bells = {int(body["norad"]): bool(body["bell"])}
        except (KeyError, TypeError, ValueError):
            return jsonify({"ok": False, "error": "expected {norad, bell} or "
                                                  "{bells:{norad: bool}}",
                            "code": "INVALID_BELLS"}), 400
    try:
        data = roster.set_bells_many(config_paths.satellites_json(SUITE_ROOT), bells)
    except OSError as exc:
        # Same failure as /select: satellites.json left root-owned by the
        # privileged installer worker, so the non-root server can't rewrite it.
        return jsonify({"ok": False, "error": f"could not save bells: {exc} — is "
                        "configuration/satellites.json writable by the server user?",
                        "code": "ROSTER_NOT_WRITABLE"}), 500
    # Echo the armed set, not the roster — /select's lesson: returning the whole
    # ~150-bird roster for one checkbox cost a Pi its bandwidth for nothing.
    armed = sorted(s["norad"] for s in data.get("satellites", []) if s.get("bell"))
    return jsonify({"ok": True, "bells": armed, "count": len(armed),
                    "applied": len(bells)})


# ── Phase 2: RTL-SDR listen (record a pass to a WAV) ─────────────────────────
@bp.route("/api/satellites/listen/status")
def api_listen_status():
    import listen
    from common import hardware
    inv = hardware.load(SUITE_ROOT)
    st = listen.status()
    pre = listen.preconditions(inv=inv)         # busy/holder scoped to our dongle
    # §10 built inline rather than `jsonify(st)` after a dict.update(): the union
    # of two helpers' keys is not a shape anyone could read off the return site,
    # and this endpoint is polled by three pages plus the assistant.
    return jsonify({
        "ok":             True,
        # Capture state.
        "recording":      st["recording"],
        "streaming":      st["streaming"],
        "mode":           st["mode"],
        "norad":          st["norad"],
        "file":           st["file"],
        "seconds":        st["seconds"],
        "freq_hz":        st["freq_hz"],
        # Whether a capture is possible at all right now.
        "missing_deps":   pre["missing_deps"],
        "dongle_present": pre["dongle_present"],
        "assigned":       pre["assigned"],
        "device":         pre["device"],
        "busy":           pre["busy"],
        "holder":         pre["holder"],
    })


class _CaptureError(Exception):
    """A validation failure while preparing an SDR capture; carries an HTTP
    status AND a stable §3 slug, so a caller can branch on the cause without
    matching on prose that is written for a human."""
    def __init__(self, msg, code, slug):
        super().__init__(msg)
        self.code = code
        self.slug = slug


def _prep_capture(norad, req_freq):
    """Shared validation for /listen (record) and /listen/stream: deps, dongle,
    exclusivity, and resolving the requested downlink + mode. Returns
    (entry, freq_hz, device_serial) or raises _CaptureError(msg, http_code).
    Server-side re-validation — never trust the button. The dongle is pinned to
    the satellites-assigned serial so rtl_fm doesn't grab device 0 (another
    service's dongle on a multi-dongle Pi)."""
    import listen
    from common import hardware
    inv = hardware.load(SUITE_ROOT)
    dev = inv.devices.get(inv.assignments.get("satellites")) or {}
    # A radio port is a different SOURCE with different prerequisites: arecord +
    # the sound card, NOT rtl_fm + a free dongle. Branch before the SDR checks
    # rather than making them lie about a box that has no dongle at all.
    on_radio = dev.get("kind") == "draws"
    if on_radio:
        rpre = listen.radio_preconditions()
        if rpre["missing_deps"]:
            raise _CaptureError("missing tools: " + ", ".join(rpre["missing_deps"]),
                                400, "AUDIO_TOOLS_MISSING")
        if not rpre["card_present"]:
            raise _CaptureError("the DRAWS sound card is not present",
                                400, "NO_SOUND_CARD")
    else:
        pre = listen.preconditions(inv=inv)
        if pre["missing_deps"]:
            raise _CaptureError("missing tools: " + ", ".join(pre["missing_deps"])
                                + " — run features/rtl-sdr/install-rtl-sdr.py",
                                400, "SDR_TOOLS_MISSING")
        if not pre["dongle_present"]:
            raise _CaptureError("no RTL-SDR dongle detected", 400, "NO_DONGLE")
        if pre["busy"]:
            raise _CaptureError(f"the dongle is in use by {pre['holder'] or 'another service'}"
                                " — stop it first", 409, "DONGLE_BUSY")
    if listen.is_capturing():
        raise _CaptureError("already capturing", 409, "ALREADY_CAPTURING")
    data = roster.load(config_paths.satellites_json(SUITE_ROOT))
    entry = next((s for s in data["satellites"] if s["norad"] == norad), None)
    downlinks = roster.legacy_downlinks(entry) if entry else []
    if not downlinks:
        raise _CaptureError("no downlink frequency for this satellite",
                            400, "NO_DOWNLINK")
    dl = downlinks[0]
    if req_freq is not None:
        try:
            req = float(req_freq)
        except (TypeError, ValueError):
            raise _CaptureError("bad freq_mhz", 400, "INVALID_FREQ")
        dl = next((d for d in downlinks if abs(float(d["freq_mhz"]) - req) < 1e-6), None)
        if dl is None:
            raise _CaptureError("frequency is not a downlink of this satellite",
                                400, "FREQ_NOT_A_DOWNLINK")
    support = listen.mode_support(dl.get("mode"))
    if not support["supported"]:
        raise _CaptureError(f"{dl.get('mode')} not supported yet — {support['blurb']}",
                            400, "MODE_UNSUPPORTED")
    freq_hz = listen.mhz_to_hz(dl["freq_mhz"])
    device_serial = dev.get("serial") or None
    radio_channel = int(dev.get("channel") or 0) if on_radio else None
    return entry, freq_hz, device_serial, dl.get("mode"), radio_channel


@bp.route("/api/satellites/listen", methods=["POST"])
@require_oasis_request
def api_listen():
    import listen
    body = request.get_json(force=True)
    try:
        norad = int(body["norad"])
    except (TypeError, ValueError, KeyError):
        return jsonify({"ok": False, "error": "bad or missing norad",
                        "code": "INVALID_NORAD"}), 400
    try:
        entry, freq_hz, device_serial, dmode, radio_channel = _prep_capture(
            norad, body.get("freq_mhz"))
    except _CaptureError as e:
        return jsonify({"ok": False, "error": str(e), "code": e.slug}), e.code
    safe = "".join(c if c.isalnum() else "_" for c in entry["name"]).strip("_")
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    rec_dir = listen.recordings_dir(SUITE_ROOT)
    out = os.path.join(rec_dir, f"{safe}_{ts}.wav")
    # Make room before keying the capture, then refuse outright if the card still
    # can't take a 20-minute worst case. Filling the root filesystem mid-pass
    # doesn't break one feature on a field station — it takes the station down.
    listen.prune_recordings(rec_dir)
    space_ok, space_err = listen.check_free_space(rec_dir)
    if not space_ok:
        return jsonify({"ok": False, "error": space_err,
                        "code": "INSUFFICIENT_STORAGE"}), 507
    try:
        started = listen.start(freq_hz, norad, out,
                               device_serial=device_serial, dmode=dmode,
                               radio_channel=radio_channel)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e),
                        "code": "CAPTURE_START_FAILED"}), 500
    # Same capture-state keys as /listen/status, so a caller that starts a
    # recording and one that polls for it read the same shape.
    return jsonify({
        "ok":        True,
        "recording": started["recording"],
        "streaming": started["streaming"],
        "mode":      started["mode"],
        "norad":     started["norad"],
        "file":      started["file"],
        "seconds":   started["seconds"],
        "freq_hz":   started["freq_hz"],
    })


@bp.route("/api/satellites/listen/stream")
def api_listen_stream():
    """Live pass audio for a browser <audio> element — a chunked MP3 stream. GET
    so a plain <audio src=…> works. Holds the dongle for the connection; the
    pipeline is torn down on disconnect. Same validation + exclusivity as /listen
    (they can't both run — one dongle)."""
    from flask import Response, stream_with_context
    import listen
    try:
        norad = int(request.args.get("norad"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad or missing norad",
                        "code": "INVALID_NORAD"}), 400
    try:
        _entry, freq_hz, device_serial, dmode, _rch = _prep_capture(
            norad, request.args.get("freq_mhz"))
    except _CaptureError as e:
        return jsonify({"ok": False, "error": str(e), "code": e.slug}), e.code
    try:
        gen, mime = listen.stream(freq_hz, norad, device_serial=device_serial, dmode=dmode)
    except RuntimeError as e:
        busy = "busy" in str(e)
        return jsonify({"ok": False, "error": str(e),
                        "code": "DONGLE_BUSY" if busy else "STREAM_START_FAILED"}), \
            (409 if busy else 400)
    resp = Response(stream_with_context(gen), mimetype=mime)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/api/satellites/listen/stop", methods=["POST"])
@require_oasis_request
def api_listen_stop():
    import listen
    result = listen.stop()
    # Sweep after the file is closed and its final size is known. The recording
    # just made is protected inside prune_recordings (newest is always kept).
    listen.prune_recordings(listen.recordings_dir(SUITE_ROOT))
    # Idempotent: stopping an idle capture is a success, not an error — the
    # caller asked for "not recording" and that is the state they got.
    return jsonify({"ok": True, "recording": result["recording"]})


@bp.route("/api/satellites/listen/recordings")
def api_listen_recordings():
    import listen
    d = listen.recordings_dir(SUITE_ROOT)
    files = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d), reverse=True):
            if fn.endswith(".wav"):
                p = os.path.join(d, fn)
                try:
                    files.append({"name": fn, "bytes": os.path.getsize(p),
                                  # §6: was a raw epoch float.
                                  "recorded_at": iso_utc(os.path.getmtime(p))})
                except OSError:
                    # Pruned between listdir and stat — skip rather than 500.
                    continue
    limit = clamp_limit(request.args.get("limit"), _RECORDINGS_DEFAULT_LIMIT,
                        _RECORDINGS_MAX_LIMIT)
    shown = files[:limit]
    return jsonify({
        "ok": True,
        "recordings": shown,
        "total": len(files),
        "count": len(shown),
        "truncated": len(files) > len(shown),
        "limit": limit,
    })


@bp.route("/api/satellites/listen/recording/<path:filename>")
def api_listen_recording(filename):
    import listen
    return send_from_directory(listen.recordings_dir(SUITE_ROOT), filename)
