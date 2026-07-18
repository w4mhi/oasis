"""Curated satellite roster (labels + downlink frequencies + selection),
persisted to configuration/satellites.json. Labels/freqs are not in TLEs."""
import datetime
import json
import os

DEFAULT_ROSTER = [
    {"name": "NOAA 15", "norad": 25338, "labels": ["WEATHER", "APT"],
     "downlinks": [{"mode": "APT", "freq_mhz": 137.620}], "selected": False},
    {"name": "NOAA 18", "norad": 28654, "labels": ["WEATHER", "APT"],
     "downlinks": [{"mode": "APT", "freq_mhz": 137.9125}], "selected": False},
    {"name": "NOAA 19", "norad": 33591, "labels": ["WEATHER", "APT"],
     "downlinks": [{"mode": "APT", "freq_mhz": 137.100}], "selected": False},
    {"name": "METEOR-M2 3", "norad": 57166, "labels": ["WEATHER", "LRPT"],
     "downlinks": [{"mode": "LRPT", "freq_mhz": 137.900}], "selected": False},
    {"name": "ISS (ZARYA)", "norad": 25544, "labels": ["APRS", "VOICE", "CREWED"],
     "downlinks": [{"mode": "APRS", "freq_mhz": 145.825},
                   {"mode": "FM voice", "freq_mhz": 145.800}], "selected": True},
    {"name": "SO-50", "norad": 27607, "labels": ["VOICE"],
     "downlinks": [{"mode": "FM", "freq_mhz": 436.795}], "selected": False},
    {"name": "AO-91", "norad": 43017, "labels": ["VOICE"],
     "downlinks": [{"mode": "FM", "freq_mhz": 145.960}], "selected": False},
    {"name": "RS-44", "norad": 44909, "labels": ["LINEAR", "SSB"],
     "downlinks": [{"mode": "SSB", "freq_mhz": 435.640}], "selected": False},
    {"name": "PO-101", "norad": 43678, "labels": ["APRS", "VOICE"],
     "downlinks": [{"mode": "FM", "freq_mhz": 145.900}], "selected": False},
]


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def load(path):
    """Return the roster, seeding configuration/satellites.json with the
    defaults on first run. A garbled file is replaced with defaults."""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data.get("satellites"), list):
                return data
        except (ValueError, OSError):
            pass
    data = {"updated": _now(), "satellites": [dict(s) for s in DEFAULT_ROSTER]}
    save(path, data)
    return data


def set_selected(path, norad, selected):
    data = load(path)
    for s in data["satellites"]:
        if s["norad"] == norad:
            s["selected"] = bool(selected)
    data["updated"] = _now()
    save(path, data)
    return data
