"""Satellite roster persisted to configuration/satellites.json. The list is
built by build-roster.py (SatNOGS + CelesTrak aggregation); this module only
loads/saves it and persists the operator's per-satellite flags. Labels/freqs are
not in TLEs, so they live in the records build-roster.py writes."""
import datetime
import json
import os
import re
import tempfile
import threading

import bands

# The fields the OPERATOR owns. Everything else in a record is DERIVED —
# build-roster.py rebuilds it from SatNOGS + CelesTrak and is entitled to
# overwrite it. These are the operator's intent about a bird, and a rebuild must
# carry them across or it silently discards a standing choice.
#
# This list is the contract between the two halves: build_records() stamps every
# name here onto each record, and a test fails if one goes unthreaded. Adding a
# field here is all it should take — the trap is that build_records() builds each
# record from a FIXED key set, so a field added anywhere else survives until the
# next roster rebuild and then vanishes, which reads as "the kiosk forgot".
#
#   selected — monitored: plotted on the map, listed on the kiosk, alerted on.
#   bell     — armed for pass alerts (VVV chime at T-10/T-5, spoken heads-up).
#
# Selecting a bird ARMS its bell (the client does it in one go; a roster from
# before that rule is migrated once by apply_bell_default_once below). The two
# are still separate fields because the bell is the exception the operator keeps:
# monitor the weather birds for the map, disarm the ones that would wake the
# shack at 03:00. They are independent at THIS layer on purpose — the coupling is
# a default, not an invariant, and set_bells_many must be able to break it.
#
# Both are per-BIRD and therefore shared across devices. Muting is deliberately
# NOT here: it is per-DEVICE (localStorage), so the shack kiosk can be silent
# overnight while a laptop still chimes.
OPERATOR_FIELDS = ("selected", "bell")

# LEGACY. Written by the retired one-shot bell backfill, and still carried across
# a rebuild so an existing roster is not rewritten behind the operator's back.
# Nothing reads it to make a decision any more: the bell is per-DEVICE now
# (common/js/sat-bells.js), so "which birds wake a screen" is answered by the
# screen. `bell` likewise stays in OPERATOR_FIELDS as a read-only value for each
# browser's one-time adoption.
BELL_DEFAULT_KEY = "bell_follows_selection"

# Serializes the read-modify-write in set_selected/set_selected_many. Atomic
# save() prevents a TORN file; it does nothing about a LOST UPDATE — two threads
# both load the roster, each flips its own satellite, and the second write erases
# the first. The client fires a burst (reconcileSelection pushes every local-only
# pick on load; clearAll pushes every deselect), and gunicorn serves them on
# --threads 4, so selecting 20 birds landed 1-2 of them and the kiosk showed a
# fraction of what the operator had picked.
#
# A threading.Lock is the right tool *here specifically* because start-oasis.py
# pins gunicorn to --workers 1: one process, several threads. If this ever grows
# to multiple worker processes, this needs a file lock (fcntl.flock) instead —
# separate processes do not share a threading.Lock.
_write_lock = threading.RLock()


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _empty():
    return {"updated": _now(), "source": None, "labels": {}, "satellites": []}


def save(path, data):
    # Atomic write: json.dump straight to the target truncates it first, so a
    # concurrent reader (gunicorn runs multiple threads; boot() fires several
    # /select calls at once) can catch a half-written file, fail to parse it, and
    # — via load()'s garbled path — persist an EMPTY roster over the real one.
    # Write to a UNIQUE temp file (mkstemp — a per-PID name collides across
    # threads and tears itself), fsync, then os.replace() so readers only ever see
    # a complete roster, old or new, never a torn one. Last writer wins (a benign
    # lost update on the `selected` flag); the roster itself is never truncated.
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".satellites-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load(path):
    """Return the roster. Missing → seed an empty envelope (build-roster.py fills
    it on the first online run). Present-but-garbled → return an empty view but
    DO NOT overwrite: never clobber a roster that may be mid-write or recoverable
    (with atomic save() a torn read is practically impossible; this is a guard)."""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("satellites"), list):
                return data
        except (ValueError, OSError):
            pass
        return _empty()                     # garbled — read-only, leave the file alone
    data = _empty()
    save(path, data)                        # genuinely missing — seed it
    return data


def operator_state(data):
    """`{norad: {field: bool}}` for every OPERATOR_FIELD in a loaded roster.

    What build-roster.py must carry across a rebuild. Reading it here — rather
    than having the aggregator reach into record keys itself — keeps the set of
    operator-owned fields declared in exactly one place."""
    out = {}
    for s in (data or {}).get("satellites") or []:
        try:
            norad = int(s["norad"])
        except (KeyError, TypeError, ValueError):
            continue                      # unusable row — nothing to carry
        out[norad] = {f: bool(s.get(f, False)) for f in OPERATOR_FIELDS}
    return out


def set_selected(path, norad, selected):
    """Set one satellite's monitored flag. Serialized against other writers."""
    return set_selected_many(path, {norad: selected})


def set_selected_many(path, selections):
    """Apply a whole `{norad: bool}` monitored set in ONE load-modify-save."""
    return _set_flag_many(path, selections, "selected")


def _set_flag_many(path, values, field, stamp=None):
    """Apply a whole `{norad: bool}` set for one operator field in ONE
    load-modify-save.

    This is what the client should use for anything touching more than one bird.
    Sending N separate requests is not just N times slower — before the lock it
    silently dropped most of them, and even with the lock it leaves the roster
    observable in N intermediate states while a burst is in flight (the kiosk
    polls /api/satellites every 60 s and can sample the middle of one).

    JSON object keys are strings, so norads are coerced. Unknown norads are
    ignored rather than invented — the roster's membership is owned by
    build-roster.py, and a stale client must never be able to add rows to it.
    """
    if field not in OPERATOR_FIELDS:
        # Defensive: a typo here would write a junk key that build-roster.py
        # then drops on the next rebuild — a bug that only shows up weeks later.
        raise ValueError(f"{field!r} is not an operator-owned roster field")
    wanted = {}
    for norad, value in (values or {}).items():
        try:
            wanted[int(norad)] = bool(value)
        except (TypeError, ValueError):
            continue                      # unparseable key — skip, don't fail the batch
    with _write_lock:
        data = load(path)
        if wanted:
            for s in data["satellites"]:
                if s["norad"] in wanted:
                    s[field] = wanted[s["norad"]]
            if stamp:
                data[stamp] = True     # same write, not a second one
            data["updated"] = _now()
            save(path, data)
        return data


def _display_mode(t):
    """A meaningful mode label for a button: prefer a recognizable SERVICE named in
    the description (APRS) over the raw SatNOGS modulation (AFSK/FM). SatNOGS has no
    'APRS' mode — it's 1200-baud AFSK with 'APRS' only in the description — so the
    modulation alone reads as 'AFSK' where an operator expects 'APRS'."""
    if "APRS" in (t.get("description") or "").upper():
        return "APRS"
    return t.get("mode")


# CTCSS lives in the SatNOGS free-text description ("Mode V/U FM - Voice Repeater
# CTCSS 67.0 Hz"), never in a field of its own. It is the difference between
# working the ISS repeater and hearing nothing, so it is worth extracting — but
# only from an unambiguous form, and anything else yields None. No tone shown is
# a correct answer; a wrong tone is worse than none.
#
# Bounded to the standard CTCSS range so a stray number in prose ("CTCSS 1 of 2")
# cannot become a tone.
_CTCSS_RE = re.compile(r"\bCTCSS\s+(\d{2,3}(?:\.\d)?)\s*(?:Hz)?", re.I)
_CTCSS_MIN_HZ, _CTCSS_MAX_HZ = 60.0, 260.0


def _ctcss_hz(description):
    m = _CTCSS_RE.search(description or "")
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return v if _CTCSS_MIN_HZ <= v <= _CTCSS_MAX_HZ else None


def _uplink_view(t):
    """The uplink leg of ONE transmitter, shaped for display, or None.

    Measured against the live catalogue: 87 of 807 card-visible transmitters
    carry an uplink, and every one of them is typed Transponder or Transceiver.
    An uplink is not a generic extra field — it is the signal that this bird can
    be worked two ways, which is why 720 entries render no uplink line at all and
    the enriched popup does not become the crowded card again.

    `simplex` (uplink == downlink) is a digipeater and worth saying out loud:
    rendering it as two identical legs reads like a mistake."""
    up = t.get("uplink")
    if not (up and up.get("freq_mhz") is not None):
        return None
    dl = t.get("downlink") or {}
    return {
        "freq_mhz": up["freq_mhz"],
        "freq_high_mhz": up.get("freq_high_mhz"),
        "invert": bool(t.get("invert")),
        "ctcss_hz": _ctcss_hz(t.get("description")),
        "simplex": up["freq_mhz"] == dl.get("freq_mhz"),
    }


def legacy_downlinks(sat):
    """Phase-1 compat view: flatten the on-disk `transmitters` list into the old
    `downlinks` shape [{"mode","freq_mhz"}] that routes/listen/UI still read.
    Transmitters with no downlink leg are skipped.

    De-duplicated by (mode, freq): SatNOGS often lists several active
    transmitters at the same downlink freq+mode (e.g. ISS's FM voice), which
    would otherwise render as duplicate buttons. The entry kept is the first
    one seen at a key, but its uplink is never allowed to depend on that
    order: a bird can list a downlink-only Transmitter ahead of a Transceiver
    at the identical key, and SatNOGS's return order carries no meaning — so
    if the kept entry has no uplink and a later duplicate at the same key
    does, the duplicate's uplink is adopted onto it. Only the uplink crosses
    over; the duplicate record itself is still collapsed away, never
    resurrected as a second card.
    Phase 2 migrates consumers to `transmitters`/uplink and this helper goes away.

    Downlinks this station cannot use are dropped — see bands.usable_downlink.
    The RECORD keeps them; only the view loses them. That split is deliberate:
    `labels_for` decides roster membership from the record, and 345 birds are in
    the roster solely on the strength of an out-of-band telemetry downlink, so
    filtering any earlier would delete them as a side effect of tidying a card.
    The policy lives in bands.py rather than here because this function is a
    phase-1 shim scheduled for deletion, and the policy must outlive it."""
    out, seen = [], {}
    for t in sat.get("transmitters", []):
        dl = t.get("downlink")
        if not (dl and dl.get("freq_mhz") is not None):
            continue
        if not bands.usable_downlink(dl["freq_mhz"]):
            continue
        mode = _display_mode(t)
        key = ((mode or "").strip().lower(), round(float(dl["freq_mhz"]), 6))
        up = _uplink_view(t)
        kept = seen.get(key)
        if kept is not None:
            # Adopt the TYPE with the uplink, never separately: a record left
            # saying "Transmitter" while carrying an uplink contradicts itself,
            # and `one_way` below would then call a two-way channel one-way.
            if not kept["uplink"] and up:
                kept["uplink"] = up
                kept["type"] = t.get("type")
            continue
        entry = {"mode": mode, "freq_mhz": dl["freq_mhz"], "uplink": up,
                 "type": t.get("type")}
        seen[key] = entry
        out.append(entry)
    return out


def group_downlinks(entries):
    """Collapse downlinks that are the SAME ACTION into one card entry.

    Takes entries already decorated with listen.mode_support (so they carry
    `demod`), and groups on (frequency, demod) — not on frequency alone.

    The ISS lists three active transmitters on 437.800: FM voice repeater, SSTV,
    and IORS telemetry. "Active" in SatNOGS means *a valid configuration*, not
    *on air now* — it is one radio, reconfigured over time, and the three are
    mutually exclusive. They also produce a byte-identical capture command
    (`rtl_fm -M fm`, 48 kHz, no offset), so three buttons offered a choice that
    did not exist. Physically right too: SSTV on 437.800 IS FM audio.

    Frequency alone would be the wrong key. Roster-wide, 88 frequencies carry
    modes needing DIFFERENT demodulators — NORAD 31130 has FM and CW on 435.245,
    and CW is tuned 700 Hz low so the carrier lands as an audible tone.
    Collapsing those would break the capture. Two entries are the same entry
    exactly when they would run the same command.

    Grouping collapses the ACTION, never the INFORMATION: the merged entry keeps
    every mode in `modes` and every distinct blurb, so a card that says
    "437.800 FM SSTV FSK" has already told the operator that listening for the
    repeater may yield SSTV warble instead — the spacecraft chooses, not them.
    A merged entry labelled just "FM 437.800" would be the blindness this exists
    to prevent.

    An uplink belongs to a TRANSMITTER, not to a frequency, so a grouped entry
    keeps `uplinks` as a list: the ISS's 437.800 groups FM, SSTV and FSK, and
    only the FM member can be transmitted to.

    `mode` stays a single token for consumers that drive the demodulator from it
    (the listen route). Any member is correct — they share a demod by
    construction — so it is the alphabetically first, which is deterministic
    across rebuilds rather than dependent on SatNOGS ordering."""
    groups, order = {}, []
    for e in entries:
        freq = e.get("freq_mhz")
        key = (round(float(freq), 6) if freq is not None else None, e.get("demod"))
        if key not in groups:
            groups[key] = dict(e)
            groups[key]["modes"] = []
            groups[key]["blurbs"] = []
            groups[key]["uplinks"] = []
            groups[key]["_types"] = []
            order.append(key)
        g = groups[key]
        g["_types"].append(e.get("type"))
        mode = (e.get("mode") or "").strip()
        if mode and mode not in g["modes"]:
            g["modes"].append(mode)
        blurb = (e.get("blurb") or "").strip()
        if blurb and blurb not in g["blurbs"]:
            g["blurbs"].append(blurb)
        up = e.get("uplink")
        if up:
            u = dict(up, mode=mode)
            if u not in g["uplinks"]:
                g["uplinks"].append(u)
    out = []
    for key in order:
        g = groups[key]
        g["modes"] = sorted(g["modes"])
        if g["modes"]:
            g["mode"] = g["modes"][0]
        g["blurb"] = " · ".join(g.pop("blurbs"))
        # Sorted for a stable render, and the singular key dropped: the group was
        # seeded from its FIRST member, so leaving `uplink` behind would leave two
        # sources of truth disagreeing whenever a later member owns the uplink.
        g["uplinks"].sort(key=lambda u: (u["freq_mhz"], u["mode"] or ""))
        g.pop("uplink", None)
        # `one_way` ASSERTS what an absent uplink only implies. SatNOGS types a
        # transmitter as Transmitter (one-way), Transceiver or Transponder; the
        # first is its own word for "does not listen". Measured over the live
        # catalogue the two agree perfectly — all 87 Transceiver/Transponder
        # entries carry an uplink and all 720 Transmitter ones do not — but
        # agreement in today's data is not a guarantee, and the card would
        # otherwise print "no uplink exists" on the strength of a field simply
        # being absent. That is the permission-denied-looks-like-not-present
        # mistake, and this is the one field that separates them.
        #
        # ALL members must say Transmitter. A mixed group (the ISS's 437.800
        # holds an FM Transceiver beside SSTV and FSK Transmitters) is not
        # one-way: you can transmit to it, just not to every service on it.
        # An absent/unknown type never yields True — it is the case we cannot
        # speak for.
        types = g.pop("_types")
        g["one_way"] = bool(types) and all(t == "Transmitter" for t in types)
        out.append(g)
    return out
