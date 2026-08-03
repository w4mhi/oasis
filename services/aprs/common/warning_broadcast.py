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


def object_payload(w, symbol_table, symbol, send_path, interval):
    """dto.BeaconRequest for a live APRS object beacon (GrayWolf re-beacons it)."""
    return {
        "type": "object",
        "object_name": object_name(w["id"]).strip(),   # GrayWolf pads to 9
        "latitude": float(w["lat"]),
        "longitude": float(w["lon"]),
        "symbol_table": symbol_table,
        "symbol": symbol,
        "comment": str(w.get("note") or ""),
        "send_path": send_path,
        "interval": interval,
        "enabled": True,
    }


def kill_info(name9, lat, lon, symbol_table, symbol, ts_utc):
    """Raw APRS killed-object info field. Receivers match the kill by name."""
    ts = time.strftime("%d%H%Mz", ts_utc)
    return (";" + name9[:9].ljust(9) + "_" + ts +
            format_lat(lat) + symbol_table + format_lon(lon) + symbol)


def kill_payload(name9, lat, lon, symbol_table, symbol, send_path, ts_utc):
    """dto.BeaconRequest for a one-shot custom beacon carrying the kill frame."""
    return {
        "type": "custom",
        "custom_info": kill_info(name9, lat, lon, symbol_table, symbol, ts_utc),
        "send_path": send_path,
        "enabled": True,
    }


class WarningBroadcaster:
    def __init__(self, client, symbol_map, send_path="both", interval=1800,
                 kill_repeat=3):
        self.c = client
        self.symbols = symbol_map
        self.send_path = send_path
        self.interval = interval
        self.kill_repeat = kill_repeat

    def _sym(self, wtype):
        return self.symbols.get(wtype, SYMBOL_FALLBACK)

    def advertise(self, w):
        """Create the live object beacon. Returns gw_beacon_id or None."""
        table, code = self._sym(w.get("type"))
        payload = object_payload(w, table, code, self.send_path, self.interval)
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
        name9 = object_name(w["id"])
        payload = kill_payload(name9, float(w["lat"]), float(w["lon"]),
                               table, code, self.send_path, time.gmtime())
        try:
            kid = self.c.create_beacon(payload)
        except GraywolfError:
            return
        for _ in range(self.kill_repeat):
            try:
                self.c.send_now(kid)
            except GraywolfError:
                break
        try:
            self.c.delete_beacon(kid)
        except GraywolfError:
            pass

    def reconcile(self, warnings):
        """Make GrayWolf's OASIS-owned object beacons == broadcast warnings."""
        out = {"created": 0, "killed": 0}
        broadcast = [w for w in warnings if w.get("broadcast")]
        wanted = {object_name(w["id"]).strip(): w for w in broadcast}
        try:
            existing = self.c.list_beacons()
        except GraywolfError:
            return out
        # index OASIS-owned beacons on the air by object name
        on_air = {}
        for b in existing:
            nm = str(b.get("object_name") or "").strip()
            if _NAME_RE.match(nm):
                on_air[nm] = b
        # create missing
        for nm, w in wanted.items():
            if nm not in on_air:
                if self.advertise(w) is not None:
                    out["created"] += 1
        # kill orphans (on the air but no matching broadcast warning)
        for nm, b in on_air.items():
            if nm not in wanted:
                fake = {"id": nm[1:], "lat": b.get("latitude", 0.0),
                        "lon": b.get("longitude", 0.0), "type": None,
                        "gw_beacon_id": b.get("id")}
                if self.unadvertise(fake):
                    out["killed"] += 1
        return out
