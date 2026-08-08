"""
App assets must revalidate, so a cached copy can never meet a newer server.

Found the hard way: after /api/adsb/recent stopped sending the epoch `ts` field, a
browser holding a cached pre-migration common/js/traffic-list.js read `ts` as
undefined, computed new Date(NaN).toISOString() and threw — blanking the entire
traffic list, APRS stations included, on a Pi whose server-side files were all
correct and byte-identical to the repo.

`no-cache` is not `no-store`: the browser may still cache, it just has to send an
If-None-Match first. On a LAN that answers 304 and costs nothing, which is the
right trade for a station with no in-band update mechanism.
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))

import app as oasis_app  # noqa: E402


class RevalidationTest(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def _cc(self, path):
        return self.c.get(path).headers.get("Cache-Control", "")

    def test_shared_js_modules_revalidate(self):
        for path in ("/common/js/traffic-list.js", "/common/js/adsb.js",
                     "/common/js/incident-icons.js"):
            self.assertIn("no-cache", self._cc(path), path)

    def test_html_pages_revalidate(self):
        # A stale page can reference a script it no longer ships with.
        self.assertIn("no-cache", self._cc("/maps/traffic/map.html"))

    def test_api_stays_no_store(self):
        # Stronger than no-cache and already relied on — must not be weakened.
        self.assertIn("no-store", self._cc("/api/health/probe"))

    def test_map_engine_assets_revalidate(self):
        self.assertIn("no-cache", self._cc("/maps/mapengine/basemap-style.js"))


if __name__ == "__main__":
    unittest.main()
