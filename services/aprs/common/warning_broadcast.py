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
