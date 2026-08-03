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

    def test_extract_no_downloader_and_unsupported_400(self):
        # Binary missing AND no prebuilt for this platform -> 400 (nothing to install).
        with mock.patch.object(mapdata.mapctl, "resolve_pmtiles", return_value=None), \
             mock.patch.object(mapdata.mapctl, "pmtiles_asset", return_value=None):
            r = self.client.post("/api/maps/extract", json={"state": "Alabama"})
        self.assertEqual(r.status_code, 400)

    def test_extract_auto_installs_when_missing(self):
        # Binary missing but a prebuilt exists -> the stream installs it, then extracts.
        seen = []
        def fake_resolve(_):
            seen.append(1)
            return None if len(seen) == 1 else "/fake/pmtiles"
        with mock.patch.object(mapdata.mapctl, "resolve_pmtiles", side_effect=fake_resolve), \
             mock.patch.object(mapdata.mapctl, "pmtiles_asset", return_value={"url": "x", "kind": "tar.gz"}), \
             mock.patch.object(mapdata.mapctl, "install_pmtiles",
                               return_value=iter(["fetching\n", "[install complete] ok\n"])), \
             mock.patch.object(mapdata.mapctl, "extract",
                               return_value=iter(["extracting\n", "[extract complete] saved Alabama.pmtiles\n"])):
            r = self.client.post("/api/maps/extract", json={"state": "Alabama"})
            self.assertEqual(r.status_code, 200)
            body = r.get_data(as_text=True)
        self.assertIn("installing the map downloader", body)
        self.assertIn("[install complete]", body)
        self.assertIn("[extract complete]", body)

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
