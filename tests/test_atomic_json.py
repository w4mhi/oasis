"""
Atomic JSON writes, and the station.json race they close.

Two routes read-modify-write configuration/station.json — the Setup station form
(server/routes/setup.py) and the APRS frequency selector
(server/routes/aprs_freq.py). Both used a truncating open(path, "w"), under
gunicorn's `--threads 4`. A reader landing in the truncate window parses nothing,
`_read_station` swallows the error and returns {}, and the next write persists a
station.json with no callsign, grid, lat or lon. Offline, station identity is not
something the operator can re-fetch.

ConcurrentReadTest is the one that matters: it fails against a truncating write.
"""

import json
import os
import sys
import tempfile
import threading
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from common.atomic_json import read_json, write_json  # noqa: E402


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "station.json")

    def test_roundtrip(self):
        write_json(self.p, {"callsign": "W4MHI", "grid": "CN87"})
        self.assertEqual(read_json(self.p)["callsign"], "W4MHI")

    def test_replaces_existing_content(self):
        write_json(self.p, {"a": 1})
        write_json(self.p, {"b": 2})
        self.assertEqual(read_json(self.p), {"b": 2})

    def test_creates_missing_parent_dirs(self):
        nested = os.path.join(self.d, "configuration", "station.json")
        write_json(nested, {"ok": True})
        self.assertTrue(os.path.isfile(nested))

    def test_leaves_no_temp_files_behind(self):
        write_json(self.p, {"a": 1})
        self.assertEqual(os.listdir(self.d), ["station.json"])

    def test_trailing_newline_matches_the_previous_format(self):
        write_json(self.p, {"a": 1})
        self.assertTrue(open(self.p, encoding="utf-8").read().endswith("}\n"))

    def test_a_failed_serialize_leaves_the_original_intact_and_no_debris(self):
        write_json(self.p, {"good": True})
        with self.assertRaises(TypeError):
            write_json(self.p, {"bad": object()})
        self.assertEqual(read_json(self.p), {"good": True})
        self.assertEqual(os.listdir(self.d), ["station.json"])


class ReadTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "station.json")

    def test_missing_file_is_the_default_not_an_error(self):
        self.assertEqual(read_json(self.p), {})
        self.assertEqual(read_json(self.p, default={"x": 1}), {"x": 1})

    def test_missing_file_is_the_default_even_when_strict(self):
        # A fresh install is not a fault.
        self.assertEqual(read_json(self.p, strict=True), {})

    def test_garbled_file_is_the_default_when_lenient(self):
        open(self.p, "w").write("{not json")
        self.assertEqual(read_json(self.p), {})

    def test_garbled_file_raises_when_strict(self):
        # So a read-modify-write caller refuses to overwrite what it can't read.
        open(self.p, "w").write("{not json")
        with self.assertRaises(ValueError):
            read_json(self.p, strict=True)

    def test_empty_file_is_the_default(self):
        open(self.p, "w").write("")
        self.assertEqual(read_json(self.p), {})


class ConcurrentReadTest(unittest.TestCase):
    """A reader must never observe a torn file. Fails on a truncating write."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "station.json")
        # Wide enough that a truncating writer leaves a generous window.
        self.payload = {"callsign": "W4MHI", "grid": "CN87ux",
                        "filler": ["x" * 64 for _ in range(400)]}
        write_json(self.p, self.payload)

    def test_readers_only_ever_see_a_complete_file(self):
        stop = threading.Event()
        torn = []
        lost_callsign = []

        def writer():
            for i in range(150):
                if stop.is_set():
                    return
                body = dict(self.payload)
                body["aprs_freq"] = f"144.{i}M"
                write_json(self.p, body)

        def reader():
            while not stop.is_set():
                try:
                    with open(self.p, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                except FileNotFoundError:
                    torn.append("missing")
                except ValueError as exc:
                    torn.append(str(exc))
                else:
                    if not data.get("callsign"):
                        lost_callsign.append(data)

        threads = [threading.Thread(target=writer) for _ in range(3)]
        threads += [threading.Thread(target=reader) for _ in range(3)]
        for t in threads[3:]:
            t.daemon = True
        for t in threads:
            t.start()
        for t in threads[:3]:
            t.join()
        stop.set()
        for t in threads[3:]:
            t.join(timeout=2)

        self.assertEqual(torn, [], f"reader saw a torn file {len(torn)}x: {torn[:3]}")
        self.assertEqual(lost_callsign, [], "reader saw a station.json with no callsign")


if __name__ == "__main__":
    unittest.main()
