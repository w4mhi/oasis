"""POST /api/satellites/horizon — the operator's skyline, saved.

The mask is the only thing this route writes into station.json, and station.json
holds the callsign, grid and position that an OFFLINE station cannot re-fetch.
So the tests here are mostly about what the route refuses to do.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))

import app as oasis_app                # noqa: E402
from routes import horizon as horizon_route   # noqa: E402
# NOT `from server.routes import horizon` — with sys.path holding both the
# suite root and server/, that import resolves to a SEPARATE module object
# from the one app.py registers (`routes.horizon`, found via server/ on the
# path). mock.patch.object against the wrong module is a silent no-op: the
# view keeps reading the real SUITE_ROOT, and every "isolated" test below
# would hit the operator's live configuration/station.json instead of the
# tmpdir. `from routes import horizon` is the same alias app.py uses, so the
# patch lands on the module the test client actually dispatches to.

_FULL = {"N": 25, "NNE": 24, "NE": 20, "ENE": 14, "E": 8, "ESE": 6,
         "SE": 5, "SSE": 5, "S": 5, "SSW": 5, "SW": 5, "WSW": 8,
         "W": 12, "WNW": 16, "NW": 20, "NNW": 23}


class HorizonValidation(unittest.TestCase):
    def test_a_full_mask_is_accepted(self):
        self.assertEqual(horizon_route.normalise(_FULL), _FULL)

    def test_a_partial_mask_is_legal(self):
        # A missing sector falls back to min_elev client-side, so {"N": 25} is a
        # complete statement: "I am blocked to the north and nowhere else."
        self.assertEqual(horizon_route.normalise({"N": 25}), {"N": 25})

    def test_an_empty_mask_is_legal_and_means_clear_all_round(self):
        self.assertEqual(horizon_route.normalise({}), {})

    def test_an_unknown_sector_is_rejected(self):
        with self.assertRaises(ValueError):
            horizon_route.normalise({"NORTH": 25})

    def test_names_outside_the_sixteen_are_rejected(self):
        with self.assertRaises(ValueError):
            horizon_route.normalise({"NNEE": 25})

    def test_out_of_range_values_are_rejected(self):
        for bad in ({"N": -1}, {"N": 90}, {"N": 900}):
            with self.assertRaises(ValueError):
                horizon_route.normalise(bad)

    def test_non_numeric_values_are_rejected(self):
        for bad in ({"N": "tall"}, {"N": None}, {"N": [25]}):
            with self.assertRaises(ValueError):
                horizon_route.normalise(bad)

    def test_a_non_dict_is_rejected(self):
        for bad in ([], "N", None, 25):
            with self.assertRaises(ValueError):
                horizon_route.normalise(bad)

    def test_values_are_normalised_to_float(self):
        self.assertIsInstance(horizon_route.normalise({"N": 25})["N"], float)


class HorizonRoute(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "configuration"), exist_ok=True)
        self.path = os.path.join(self.tmp.name, "configuration", "station.json")
        oasis_app.app.config["TESTING"] = True
        self.client = oasis_app.app.test_client()

    def _seed(self, body):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(body, fh)

    def _post(self, payload, csrf=True):
        headers = {"X-OASIS-Request": "1"} if csrf else {}
        with mock.patch.object(horizon_route, "SUITE_ROOT", self.tmp.name):
            return self.client.post("/api/satellites/horizon",
                                    json=payload, headers=headers)

    def test_a_save_preserves_every_other_key(self):
        # station.json carries the callsign, grid and position. Offline, the
        # operator cannot re-fetch any of it, so a save that drops them is
        # unrecoverable.
        self._seed({"callsign": "W4MHI", "grid": "CN87XN", "lat": 47.5,
                    "lon": -122.0, "aprs_freq": "144.390M", "min_elev": 10})
        r = self._post({"horizon": _FULL})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        saved = json.load(open(self.path))
        self.assertEqual(saved["callsign"], "W4MHI")
        self.assertEqual(saved["grid"], "CN87XN")
        self.assertEqual(saved["lat"], 47.5)
        self.assertEqual(saved["aprs_freq"], "144.390M")
        self.assertEqual(saved["min_elev"], 10)
        self.assertEqual(saved["horizon"], _FULL)

    def test_a_second_save_replaces_rather_than_merges_sectors(self):
        # Dragging a handle back down to "clear" must be able to REMOVE a sector,
        # which a merge would make impossible.
        self._seed({"lat": 47.5, "lon": -122.0, "horizon": _FULL})
        self._post({"horizon": {"N": 25}})
        self.assertEqual(json.load(open(self.path))["horizon"], {"N": 25.0})

    def test_an_invalid_mask_is_rejected_and_nothing_is_written(self):
        self._seed({"lat": 47.5, "lon": -122.0})
        r = self._post({"horizon": {"NORTH": 25}})
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("horizon", json.load(open(self.path)))

    def test_the_csrf_header_is_required(self):
        self._seed({"lat": 47.5, "lon": -122.0})
        r = self._post({"horizon": _FULL}, csrf=False)
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("horizon", json.load(open(self.path)))

    def test_a_json_array_body_is_rejected_not_a_500(self):
        # A syntactically valid body that isn't an object must take the same
        # 400 path as any other invalid mask, not fall through to data.get()
        # on a list and blow up with a 500.
        self._seed({"lat": 47.5, "lon": -122.0})
        r = self._post([1, 2, 3])
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("horizon", json.load(open(self.path)))

    def test_a_json_string_body_is_rejected_not_a_500(self):
        self._seed({"lat": 47.5, "lon": -122.0})
        r = self._post("just a string")
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("horizon", json.load(open(self.path)))

    def test_a_json_number_body_is_rejected_not_a_500(self):
        self._seed({"lat": 47.5, "lon": -122.0})
        r = self._post(42)
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("horizon", json.load(open(self.path)))

    def test_a_garbled_station_json_is_not_clobbered(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        r = self._post({"horizon": _FULL})
        self.assertEqual(r.status_code, 500)
        self.assertIn("NOT saved", r.get_json()["error"])
        self.assertEqual(open(self.path).read(), "{ this is not json")


if __name__ == "__main__":
    unittest.main()
