import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SERVER = os.path.join(_ROOT, "server")
for _p in (_ROOT, _SERVER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app as app_module
from maps import routes as mapdata


class MapDownloadRoutesTest(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_inventory_shape(self):
        r = self.client.get("/api/maps")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        for k in ("states", "present", "source", "pmtiles_available", "extracting"):
            self.assertIn(k, j)
        # us-states.geojson lists all 50 states + DC + PR.
        self.assertIn("Alabama", j["states"])
        self.assertIsInstance(j["present"], list)

    def test_extract_unknown_state_400(self):
        # Stub the binary present so we exercise the unknown-state pre-check,
        # not the missing-binary one (this box may lack go-pmtiles).
        with mock.patch.object(mapdata.mapctl, "resolve_pmtiles", return_value="/fake/pmtiles"):
            r = self.client.post("/api/maps/extract", json={"state": "Nowhere"})
        self.assertEqual(r.status_code, 400)

    def test_extract_missing_binary_400(self):
        with mock.patch.object(mapdata.mapctl, "resolve_pmtiles", return_value=None):
            r = self.client.post("/api/maps/extract", json={"state": "Alabama"})
        self.assertEqual(r.status_code, 400)

    def test_extract_busy_409(self):
        # Binary stubbed present + a held lock → busy 409 (in real use a held lock
        # implies the binary is present, so this ordering matches production).
        mapdata._extract_lock.acquire()
        try:
            with mock.patch.object(mapdata.mapctl, "resolve_pmtiles", return_value="/fake/pmtiles"):
                r = self.client.post("/api/maps/extract", json={"state": "Alabama"})
            self.assertEqual(r.status_code, 409)
        finally:
            mapdata._extract_lock.release()

    def test_cancel_when_idle(self):
        r = self.client.post("/api/maps/extract/cancel")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["cancelled"])


if __name__ == "__main__":
    unittest.main()
