"""
station.json read-modify-write safety, at the route level.

Two blueprints own writes to configuration/station.json — the Setup station form
and the APRS frequency selector. Both must preserve keys they don't own, write
atomically, and refuse rather than clobber a file they couldn't read. Losing a
callsign/grid/position on an offline station is not recoverable from the box.
"""

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "server"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common import config_paths  # noqa: E402


class _StationCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "configuration"), exist_ok=True)
        self.path = config_paths.station_json(self.tmp)

    def write_station(self, body):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(body, fh)

    def read_station(self):
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)


class AprsFreqWriteTest(_StationCase):
    def setUp(self):
        super().setUp()
        from routes import aprs_freq
        self.mod = aprs_freq
        self._orig_root = aprs_freq.SUITE_ROOT
        aprs_freq.SUITE_ROOT = self.tmp
        self.addCleanup(lambda: setattr(self.mod, "SUITE_ROOT", self._orig_root))

    def test_setting_the_frequency_preserves_station_identity(self):
        self.write_station({"callsign": "W4MHI", "grid": "CN87ux",
                            "lat": 47.5, "lon": -122.0})
        self.mod._persist_freq("144.390M")
        got = self.read_station()
        self.assertEqual(got["callsign"], "W4MHI")
        self.assertEqual(got["grid"], "CN87ux")
        self.assertEqual(got["lat"], 47.5)
        self.assertEqual(got["lon"], -122.0)
        self.assertEqual(got["aprs_freq"], "144.390M")

    def test_write_is_atomic_and_leaves_no_debris(self):
        self.write_station({"callsign": "W4MHI"})
        self.mod._persist_freq("144.390M")
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["station.json"])

    def test_missing_file_is_created(self):
        self.mod._persist_freq("144.390M")
        self.assertEqual(self.read_station()["aprs_freq"], "144.390M")

    def test_garbled_station_json_is_not_clobbered(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write('{"callsign": "W4MHI", trunc')
        with self.assertRaises(ValueError):
            self.mod._persist_freq("144.390M")
        # Byte-for-byte untouched — a later manual fix can still recover it.
        self.assertEqual(open(self.path, encoding="utf-8").read(),
                         '{"callsign": "W4MHI", trunc')


class SetupWriteStationTest(_StationCase):
    def setUp(self):
        super().setUp()
        from routes import setup as setup_routes
        self.mod = setup_routes
        self._orig_root = setup_routes.SUITE_ROOT
        setup_routes.SUITE_ROOT = self.tmp
        self.addCleanup(lambda: setattr(self.mod, "SUITE_ROOT", self._orig_root))

    def test_saving_the_station_preserves_the_aprs_frequency(self):
        self.write_station({"callsign": "OLD", "aprs_freq": "144.800M"})
        self.mod._setup_write_station(
            {"station": {"callsign": "W4MHI", "grid": "CN87ux", "lat": 47.5, "lon": -122.0}})
        got = self.read_station()
        self.assertEqual(got["callsign"], "W4MHI")
        self.assertEqual(got["aprs_freq"], "144.800M")

    def test_omitted_fields_fall_back_to_what_is_on_disk(self):
        self.write_station({"callsign": "W4MHI", "grid": "CN87ux", "lat": 47.5, "lon": -122.0})
        self.mod._setup_write_station({"station": {"grid": "CN88aa"}})
        got = self.read_station()
        self.assertEqual(got["grid"], "CN88AA")   # the form normalizes to upper
        self.assertEqual(got["callsign"], "W4MHI")
        self.assertEqual(got["lat"], 47.5)

    def test_write_is_atomic_and_leaves_no_debris(self):
        self.mod._setup_write_station({"station": {"callsign": "W4MHI"}})
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["station.json"])

    def test_garbled_station_json_aborts_with_a_readable_reason(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("not json at all")
        with self.assertRaises(ValueError) as ctx:
            self.mod._setup_write_station({"station": {"callsign": "W4MHI"}})
        self.assertIn("refusing to overwrite", str(ctx.exception))
        self.assertEqual(open(self.path, encoding="utf-8").read(), "not json at all")


class NoTruncatingWritersRemainTest(unittest.TestCase):
    """station.json has exactly two writers; neither may truncate in place."""

    def test_no_route_opens_station_json_for_truncating_write(self):
        offenders = []
        for sub in ("server", "services", "maps"):
            for dirpath, dirnames, filenames in os.walk(os.path.join(_ROOT, sub)):
                dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                for fn in filenames:
                    if not fn.endswith(".py"):
                        continue
                    path = os.path.join(dirpath, fn)
                    lines = open(path, encoding="utf-8").read().split("\n")
                    for i, line in enumerate(lines):
                        if "station_json(" not in line:
                            continue
                        window = "\n".join(lines[max(0, i - 4):i + 6])
                        if 'open(' in window and '"w"' in window:
                            offenders.append(f"{os.path.relpath(path, _ROOT)}:{i + 1}")
        self.assertEqual(offenders, [],
                         "station.json written with a truncating open(): " + ", ".join(offenders)
                         + " — use common/atomic_json.write_json")


if __name__ == "__main__":
    unittest.main()
