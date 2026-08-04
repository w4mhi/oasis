"""Map OASIS map warnings ↔ GrayWolf APRS object beacons.

Pure APRS-formatting helpers plus WarningBroadcaster, which drives a
GraywolfClient to create the live object beacon, send the killed-object
frame on delete, and reconcile GrayWolf's OASIS-owned beacon set to the
current broadcast warnings.
"""
import re
import time

from .graywolf_client import GraywolfError

SYMBOL_FALLBACK = ("\\", "!")
_NAME_RE = re.compile(r"^W[0-9a-f]{8}$")   # OASIS-owned object-name convention

# GrayWolf's own send_path enum: IS-only (no radio needed), RF-only (uses the
# configured channel), or both.
VALID_SEND_PATHS = {"is_only", "both", "rf"}


def clean_send_path(value, default):
    """Return `value` if it's one of VALID_SEND_PATHS, else `default`."""
    return value if (isinstance(value, str) and value in VALID_SEND_PATHS) else default


def object_name(warning_id):
    """Stable ≤9-char APRS object name: 'W' + first 8 chars of the id."""
    return ("W" + str(warning_id)[:8]).ljust(9)[:9]


def format_lat(lat):
    hemi = "N" if lat >= 0 else "S"
    lat = abs(lat)
    deg = int(lat)
    minutes = (lat - deg) * 60.0
    return f"{deg:02d}{minutes:05.2f}{hemi}"


def format_lon(lon):
    hemi = "E" if lon >= 0 else "W"
    lon = abs(lon)
    deg = int(lon)
    minutes = (lon - deg) * 60.0
    return f"{deg:03d}{minutes:05.2f}{hemi}"


def tactical_name(abbr, existing_names, limit=1000):
    """Smallest-unused tactical APRS object name <=9 chars: ABBR + zero-padded
    sequence, trimming ABBR as the counter widens so it always fits."""
    for n in range(1, limit):
        digits = f"{n:02d}"                 # 2 digits for 1..99, then 3+ for 100..
        name = abbr[:9 - len(digits)] + digits
        if name not in existing_names:
            return name
    # Unreachable in practice (_WARN_MAX caps total warnings); never loop forever.
    return (abbr[:5] + "ZZZZ")[:9]


def object_payload(w, symbol_table, symbol, send_path, interval, source_callsign=None):
    """dto.BeaconRequest for a live APRS object beacon (GrayWolf re-beacons it).

    object_name comes from the warning's own stored tactical aprs_name (set
    once at creation time via tactical_name), not derived from the id.
    """
    payload = {
        "type": "object",
        "object_name": str(w["aprs_name"]),
        "latitude": float(w["lat"]),
        "longitude": float(w["lon"]),
        "symbol_table": symbol_table,
        "symbol": symbol,
        "comment": str(w.get("note") or ""),
        "send_path": send_path,
        "interval": interval,
        "enabled": True,
    }
    if source_callsign:
        payload["callsign"] = source_callsign
    return payload


def kill_info(name9, lat, lon, symbol_table, symbol, ts_utc):
    """Raw APRS killed-object info field. Receivers match the kill by name."""
    ts = time.strftime("%d%H%Mz", ts_utc)
    return (";" + name9[:9].ljust(9) + "_" + ts +
            format_lat(lat) + symbol_table + format_lon(lon) + symbol)


def kill_payload(name9, lat, lon, symbol_table, symbol, send_path, ts_utc,
                  source_callsign=None):
    """dto.BeaconRequest for a one-shot custom beacon carrying the kill frame."""
    payload = {
        "type": "custom",
        "custom_info": kill_info(name9, lat, lon, symbol_table, symbol, ts_utc),
        "send_path": send_path,
        "enabled": True,
    }
    if source_callsign:
        payload["callsign"] = source_callsign
    return payload


class WarningBroadcaster:
    def __init__(self, client, symbol_map, send_path="both", interval=1800,
                 kill_repeat=3, source_callsign=None):
        self.c = client
        self.symbols = symbol_map
        self.send_path = send_path
        self.interval = interval
        self.kill_repeat = kill_repeat
        # e.g. "W4MHI-1" — used as the beacon's `callsign` field, and as the
        # ownership marker reconcile() uses to find OASIS-owned beacons
        # (never touches the operator's own/main-station beacons).
        self.source_callsign = source_callsign

    def _sym(self, wtype):
        return self.symbols.get(wtype, SYMBOL_FALLBACK)

    def advertise(self, w):
        """Create the live object beacon. Returns gw_beacon_id or None."""
        table, code = self._sym(w.get("type"))
        send_path = clean_send_path(w.get("send_path"), self.send_path)
        payload = object_payload(w, table, code, send_path, self.interval,
                                  self.source_callsign)
        try:
            return self.c.create_beacon(payload)
        except GraywolfError:
            return None

    def unadvertise(self, w):
        """Stop the object beacon and tell receivers to remove it (kill).

        Returns True iff the beacon delete succeeded (or there was nothing
        to delete). The kill broadcast is always attempted, best-effort.
        """
        ok = True
        bid = w.get("gw_beacon_id")
        if bid:
            try:
                self.c.delete_beacon(bid)
            except GraywolfError:
                ok = False
        self._send_kill(w)
        return ok

    def _send_kill(self, w):
        table, code = self._sym(w.get("type"))
        name9 = str(w.get("aprs_name") or object_name(w["id"]))[:9]
        send_path = clean_send_path(w.get("send_path"), self.send_path)
        payload = kill_payload(name9, float(w["lat"]), float(w["lon"]),
                               table, code, send_path, time.gmtime(),
                               self.source_callsign)
        try:
            kid = self.c.create_beacon(payload)
        except GraywolfError:
            return
        for _ in range(self.kill_repeat):
            try:
                self.c.send_now(kid)
            except GraywolfError:
                continue
        try:
            self.c.delete_beacon(kid)
        except GraywolfError:
            pass

    def _ensure_killed(self, w, beacon):
        """Delete the object beacon + send kill frames. True iff confirmed gone."""
        bid = (beacon or {}).get("id") or w.get("gw_beacon_id")
        if bid:
            try:
                self.c.delete_beacon(bid)
            except GraywolfError:
                return False
        self._send_kill(w)
        return True

    def reconcile(self, warnings):
        """Drive GrayWolf's OASIS-owned object beacons to match `warnings`.

        Advertises broadcast-on warnings with no beacon yet, adopts an
        already-on-air beacon id when the local id was lost, kills
        broadcast-off warnings that still have a live beacon, kills+confirms
        pending_delete warnings (reporting confirmed ids in "removed"), and
        kills orphaned OASIS-owned beacons with no matching warning.

        Ownership: an on-air beacon is OASIS-owned iff its `callsign` equals
        self.source_callsign (the `<station>-1` marker). This is the ONLY
        path considered when source_callsign is set, so a beacon under any
        other callsign (the operator's own/main-station beacon, even one
        with an OASIS-looking name) is structurally unreachable by the
        kill/orphan logic below. When source_callsign is None (unconfigured
        callsign), fall back to the legacy object-name-pattern check.
        """
        out = {"created": 0, "killed": 0, "removed": []}
        try:
            existing = self.c.list_beacons()
        except GraywolfError:
            return out
        # index OASIS-owned beacons on the air, keyed by object name
        on_air = {}
        for beac in existing:
            nm = str(beac.get("object_name") or "").strip()
            if not nm:
                continue
            if self.source_callsign:
                owned = str(beac.get("callsign") or "") == self.source_callsign
            else:
                owned = bool(_NAME_RE.match(nm))
            if owned:
                on_air[nm] = beac
        known = set()
        for w in warnings:
            nm = str(w.get("aprs_name") or "").strip()
            if not nm:
                continue
            known.add(nm)
            if w.get("pending_delete"):
                if self._ensure_killed(w, on_air.get(nm)):
                    out["removed"].append(w["id"]); out["killed"] += 1
            elif w.get("broadcast"):
                if nm not in on_air:
                    gw_id = self.advertise(w)
                    if gw_id is not None:
                        w["gw_beacon_id"] = gw_id; out["created"] += 1
                elif not w.get("gw_beacon_id"):
                    w["gw_beacon_id"] = on_air[nm].get("id")   # adopt, no dupe
            else:
                if nm in on_air or w.get("gw_beacon_id"):
                    if self._ensure_killed(w, on_air.get(nm)):
                        w["gw_beacon_id"] = None; out["killed"] += 1
        # kill orphans (on the air but no matching warning at all)
        for nm, beac in on_air.items():
            if nm not in known:
                fake = {"id": nm, "aprs_name": nm,
                        "lat": beac.get("latitude", 0.0),
                        "lon": beac.get("longitude", 0.0), "type": None,
                        "gw_beacon_id": beac.get("id")}
                if self._ensure_killed(fake, beac):
                    out["killed"] += 1
        return out
