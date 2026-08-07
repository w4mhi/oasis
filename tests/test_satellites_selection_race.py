"""
Selection writes must not lose updates under a burst.

`configuration/satellites.json` is the single source of truth for which birds are
monitored — the 7" kiosk reads its `selected` flag, and the satellites page keeps
only per-browser colour alongside it. Selection used to be an UNLOCKED
read-modify-write (load → flip one flag → save the whole file), while the client
fired one fire-and-forget POST per pick:

    reconcileSelection()  satellites.html  — one POST per local-only pick on load
    clearAll()            satellites.html  — one POST per pick

Under gunicorn's `--threads 4` those interleave, each thread writing the whole
roster back over what the others just committed. Selecting 20 satellites landed
1-2 of them, so an operator monitoring 20 birds saw 7 on the kiosk. `save()` is
atomic, which prevents a *torn file* and does nothing about a *lost update*.

Fixed on both ends: the write is serialized, and a bulk form applies a whole
selection set in one load/mutate/save so the burst becomes a single write.
"""

import json
import os
import sys
import tempfile
import threading
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "services", "satellites"))

import roster  # noqa: E402


def _seed(path, n=20):
    json.dump({"updated": "", "source": "test", "labels": {},
               "satellites": [{"norad": 1000 + i, "name": f"SAT{i}", "selected": False}
                              for i in range(n)]},
              open(path, "w", encoding="utf-8"))


def _selected(path):
    with open(path, encoding="utf-8") as fh:
        return {s["norad"] for s in json.load(fh)["satellites"] if s.get("selected")}


class ConcurrentSelectTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "satellites.json")
        _seed(self.p)

    def test_a_burst_of_single_selects_loses_nothing(self):
        """The reconcileSelection() shape: N concurrent one-satellite writes."""
        norads = [1000 + i for i in range(20)]
        threads = [threading.Thread(target=roster.set_selected, args=(self.p, n, True))
                   for n in norads]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(_selected(self.p), set(norads),
                         "selections were lost to interleaved read-modify-write")

    def test_a_burst_of_deselects_loses_nothing(self):
        """The clearAll() shape, in reverse — a half-cleared roster is just as wrong."""
        roster.set_selected_many(self.p, {1000 + i: True for i in range(20)})
        threads = [threading.Thread(target=roster.set_selected, args=(self.p, 1000 + i, False))
                   for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(_selected(self.p), set())

    def test_mixed_concurrent_toggles_all_land(self):
        _seed(self.p, n=20)
        want = {1000 + i: (i % 2 == 0) for i in range(20)}
        threads = [threading.Thread(target=roster.set_selected, args=(self.p, n, v))
                   for n, v in want.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(_selected(self.p), {n for n, v in want.items() if v})

    def test_the_roster_stays_parseable_throughout_a_burst(self):
        stop = threading.Event()
        torn = []

        def reader():
            while not stop.is_set():
                try:
                    roster.load(self.p)
                except Exception as exc:            # noqa: BLE001
                    torn.append(str(exc))

        r = threading.Thread(target=reader, daemon=True)
        r.start()
        threads = [threading.Thread(target=roster.set_selected, args=(self.p, 1000 + i, True))
                   for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stop.set()
        r.join(timeout=2)
        self.assertEqual(torn, [])


class BulkSelectTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "satellites.json")
        _seed(self.p)

    def test_applies_the_whole_set_in_one_write(self):
        data = roster.set_selected_many(self.p, {1000: True, 1001: True, 1002: False})
        self.assertEqual(_selected(self.p), {1000, 1001})
        self.assertEqual({s["norad"] for s in data["satellites"] if s.get("selected")},
                         {1000, 1001})

    def test_writes_once_no_matter_how_many_satellites(self):
        calls = []
        original = roster.save
        roster.save = lambda p, d: calls.append(p) or original(p, d)
        try:
            roster.set_selected_many(self.p, {1000 + i: True for i in range(20)})
        finally:
            roster.save = original
        self.assertEqual(len(calls), 1, "bulk select must be a single read-modify-write")

    def test_unknown_norads_are_ignored_not_invented(self):
        roster.set_selected_many(self.p, {999999: True, 1000: True})
        self.assertEqual(_selected(self.p), {1000})
        with open(self.p, encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["satellites"]), 20)

    def test_empty_selection_is_a_no_op_that_still_returns_the_roster(self):
        data = roster.set_selected_many(self.p, {})
        self.assertEqual(len(data["satellites"]), 20)
        self.assertEqual(_selected(self.p), set())

    def test_string_norads_from_json_keys_are_accepted(self):
        # JSON object keys are always strings; the client sends {"1000": true}.
        roster.set_selected_many(self.p, {"1000": True})
        self.assertEqual(_selected(self.p), {1000})

    def test_bulk_and_single_writes_serialize_against_each_other(self):
        threads = [threading.Thread(target=roster.set_selected_many,
                                    args=(self.p, {1000 + i: True for i in range(10)}))]
        threads += [threading.Thread(target=roster.set_selected, args=(self.p, 1010 + i, True))
                    for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(_selected(self.p), {1000 + i for i in range(20)})


if __name__ == "__main__":
    unittest.main()
