import os
import sys
import tempfile
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


class MapInventoryRoutesTest(unittest.TestCase):
    """OASIS reads GrayWolf's offline tiles; /api/maps just reports what's present.
    (The old planet-extract downloader was removed — GrayWolf is the map source.)"""

    def setUp(self):
        self.client = app_module.app.test_client()

    def test_inventory_shape(self):
        r = self.client.get("/api/maps")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        for k in ("present", "source", "graywolf_dir", "have_maps"):
            self.assertIn(k, j)
        self.assertIsInstance(j["present"], list)
        self.assertEqual(j["source"], "graywolf")

    def test_inventory_lists_graywolf_pmtiles(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "washington.pmtiles"), "wb").close()
            open(os.path.join(d, "oregon.pmtiles"), "wb").close()
            open(os.path.join(d, "notes.txt"), "wb").close()   # ignored (not .pmtiles)
            with mock.patch.object(mapdata, "GW_STATE_DIR", d):
                r = self.client.get("/api/maps")
        j = r.get_json()
        self.assertEqual(j["present"], ["oregon", "washington"])
        self.assertTrue(j["have_maps"])

    def test_inventory_empty_when_dir_missing(self):
        with mock.patch.object(mapdata, "GW_STATE_DIR", "/no/such/graywolf/dir"):
            r = self.client.get("/api/maps")
        j = r.get_json()
        self.assertEqual(j["present"], [])
        self.assertFalse(j["have_maps"])

    def test_extract_routes_removed(self):
        # The planet-extract downloader is gone: no POST handler remains. (The root
        # static handler matches the path for GET only, so a POST yields 404 or 405
        # — either way, no extract endpoint answers it.)
        self.assertIn(self.client.post("/api/maps/extract", json={"state": "Alabama"}).status_code, (404, 405))
        self.assertIn(self.client.post("/api/maps/extract/cancel").status_code, (404, 405))


if __name__ == "__main__":
    unittest.main()
