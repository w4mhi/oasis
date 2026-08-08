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


def _station():
    try:
        with open(config_paths.station_json(SUITE_ROOT), encoding="utf-8") as fh:
            s = json.load(fh)
        return {"lat": float(s["lat"]), "lon": float(s["lon"])}
    except (OSError, ValueError, KeyError):
        return {"lat": None, "lon": None}


@bp.route("/server/satellites/")
@bp.route("/server/satellites/<path:filename>")
def satellites_static(filename="satellites.html"):
    return send_from_directory(STATIC_DIR, filename)


@bp.route("/api/satellites")
def api_satellites():
    import listen
    data = roster.load(config_paths.satellites_json(SUITE_ROOT))
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
        s["downlinks"] = [dict(d, **listen.mode_support(d.get("mode")))
                          for d in roster.legacy_downlinks(s)]
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
    import predict
    try:
        window = int(request.args.get("window", 48))
    except (TypeError, ValueError):
        window = 48
    st = _station()
    if st["lat"] is None:
        # §2: a station with no location configured is a STATE, not a failed
        # request — nothing about the call was wrong. `computed:false` says the
        # prediction never ran, so an empty `passes` cannot be misread as
        # "nothing is overhead for the next 48 hours".
        return jsonify({"ok": True, "passes": {}, "computed": False,
                        "complete": False, "computed_at": None,
                        "window_h": window, "count": 0,
                        "reason": "no-station-location"}), 200
    only = request.args.get("sat")
    tle_stamp = tle.cache_mtime(config_paths.tle_cache_dir(SUITE_ROOT))
    key = f"{st['lat']},{st['lon']},{window},{only},{int(tle_stamp) if tle_stamp else 0}"
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
                result["passes"][nk] = predict.compute_passes(
                    sat, st["lat"], st["lon"], base, hours=window, min_elev=10.0)
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
        "reason": None,
    })


@bp.route("/api/satellites/track")
def api_track():
    import predict
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
    sat = _sats_by_norad().get(norad_i) if norad_i is not None else None
    if st["lat"] is None:
        # A state, not a bad request — same as /passes.
        return jsonify({"ok": True, "track": [], "count": 0, "norad": norad_i,
                        "l1": None, "l2": None,
                        "reason": "no-station-location"}), 200
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


# ── Phase 2: RTL-SDR listen (record a pass to a WAV) ─────────────────────────
@bp.route("/api/satellites/listen/status")
def api_listen_status():
    import listen
    from common import hardware
    inv = hardware.load(SUITE_ROOT)
    st = listen.status()
    st.update(listen.preconditions(inv=inv))   # busy/holder scoped to our dongle
    return jsonify(st)


class _CaptureError(Exception):
    """A validation failure while preparing an SDR capture; carries an HTTP code."""
    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code


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
    pre = listen.preconditions(inv=inv)
    if pre["missing_deps"]:
        raise _CaptureError("missing tools: " + ", ".join(pre["missing_deps"])
                            + " — run features/rtl-sdr/install-rtl-sdr.py", 400)
    if not pre["dongle_present"]:
        raise _CaptureError("no RTL-SDR dongle detected", 400)
    if pre["busy"]:
        raise _CaptureError(f"the dongle is in use by {pre['holder'] or 'another service'}"
                            " — stop it first", 409)
    if listen.is_capturing():
        raise _CaptureError("already capturing", 409)
    data = roster.load(config_paths.satellites_json(SUITE_ROOT))
    entry = next((s for s in data["satellites"] if s["norad"] == norad), None)
    downlinks = roster.legacy_downlinks(entry) if entry else []
    if not downlinks:
        raise _CaptureError("no downlink frequency for this satellite", 400)
    dl = downlinks[0]
    if req_freq is not None:
        try:
            req = float(req_freq)
        except (TypeError, ValueError):
            raise _CaptureError("bad freq_mhz", 400)
        dl = next((d for d in downlinks if abs(float(d["freq_mhz"]) - req) < 1e-6), None)
        if dl is None:
            raise _CaptureError("frequency is not a downlink of this satellite", 400)
    support = listen.mode_support(dl.get("mode"))
    if not support["supported"]:
        raise _CaptureError(f"{dl.get('mode')} not supported yet — {support['blurb']}", 400)
    freq_hz = listen.mhz_to_hz(dl["freq_mhz"])
    dev = inv.devices.get(inv.assignments.get("satellites"))
    device_serial = (dev or {}).get("serial") or None
    return entry, freq_hz, device_serial, dl.get("mode")


@bp.route("/api/satellites/listen", methods=["POST"])
@require_oasis_request
def api_listen():
    import listen
    body = request.get_json(force=True)
    try:
        norad = int(body["norad"])
    except (TypeError, ValueError, KeyError):
        return jsonify({"error": "bad or missing norad"}), 400
    try:
        entry, freq_hz, device_serial, dmode = _prep_capture(norad, body.get("freq_mhz"))
    except _CaptureError as e:
        return jsonify({"error": str(e)}), e.code
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
        return jsonify({"error": space_err}), 507
    try:
        return jsonify(listen.start(freq_hz, norad, out, device_serial=device_serial, dmode=dmode))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        return jsonify({"error": "bad or missing norad"}), 400
    try:
        _entry, freq_hz, device_serial, dmode = _prep_capture(norad, request.args.get("freq_mhz"))
    except _CaptureError as e:
        return jsonify({"error": str(e)}), e.code
    try:
        gen, mime = listen.stream(freq_hz, norad, device_serial=device_serial, dmode=dmode)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), (409 if "busy" in str(e) else 400)
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
    return jsonify(result)


@bp.route("/api/satellites/listen/recordings")
def api_listen_recordings():
    import listen
    d = listen.recordings_dir(SUITE_ROOT)
    files = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d), reverse=True):
            if fn.endswith(".wav"):
                p = os.path.join(d, fn)
                files.append({"name": fn, "bytes": os.path.getsize(p),
                              "mtime": os.path.getmtime(p)})
    return jsonify({"recordings": files})


@bp.route("/api/satellites/listen/recording/<path:filename>")
def api_listen_recording(filename):
    import listen
    return send_from_directory(listen.recordings_dir(SUITE_ROOT), filename)
