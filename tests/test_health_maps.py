"""
GET /api/health/maps — the offline-map count, and the cache behind it.

This replaces a client-side recursive walk that issued one /api/browse request
per directory, on every health round-robin pass, from every open dashboard. On a
live station that made /api/browse the busiest endpoint on the box.

The cache is the interesting part, so most of these tests are about when it goes
dirty rather than what it counts. The contract the dashboards depend on: a map
that appears on disk must be visible on the NEXT call, with no restart and no
timer to wait out. The nested-layout and depth-cap cases came from
tests/js/service-registry.test.js, which guarded the old client-side walk.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routes import health  # noqa: E402


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("x")


class MapsWalkTests(unittest.TestCase):
    """_maps_walk — what counts as a map, and how deep we look."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "maps")
        os.makedirs(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_a_map_nested_under_tiles_state(self):
        """The real-Pi layout. A flat listing false-WARNs here — the original bug."""
        _touch(os.path.join(self.root, "tiles", "state", "washington.pmtiles"))
        _touch(os.path.join(self.root, "us-states.geojson"))
        count, _ = health._maps_walk(self.root)
        self.assertEqual(count, 1)

    def test_empty_tree_counts_zero(self):
        """A legitimate WARN, not an error: the station simply has no maps."""
        _touch(os.path.join(self.root, "convert-mbtiles.py"))
        count, _ = health._maps_walk(self.root)
        self.assertEqual(count, 0)

    def test_counts_across_several_nested_dirs(self):
        _touch(os.path.join(self.root, "a.pmtiles"))
        _touch(os.path.join(self.root, "tiles", "state", "wa.pmtiles"))
        _touch(os.path.join(self.root, "tiles", "state", "or.pmtiles"))
        count, _ = health._maps_walk(self.root)
        self.assertEqual(count, 3)

    def test_depth_cap_stops_descent(self):
        """A stray deep tree must not fan out the walk on a Pi."""
        _touch(os.path.join(self.root, "d1", "d2", "deep.pmtiles"))
        self.assertEqual(health._maps_walk(self.root, max_depth=1)[0], 0)
        self.assertEqual(health._maps_walk(self.root, max_depth=3)[0], 1)

    def test_hidden_and_internal_dirs_are_skipped(self):
        """Mirrors /api/browse's suppression so the count matches what a browse
        would have found — otherwise moving this server-side changes the answer."""
        _touch(os.path.join(self.root, "__pycache__", "junk.pmtiles"))
        _touch(os.path.join(self.root, ".hidden", "junk.pmtiles"))
        self.assertEqual(health._maps_walk(self.root)[0], 0)

    def test_missing_root_is_zero_not_an_error(self):
        """A station with no maps/ directory at all still answers the probe."""
        count, sig = health._maps_walk(os.path.join(self.tmp.name, "nope"))
        self.assertEqual(count, 0)
        self.assertEqual(sig, {})


class MapsCacheTests(unittest.TestCase):
    """The signature: when is the cached count still good?"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "maps")
        os.makedirs(os.path.join(self.root, "tiles", "state"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_unchanged_tree_stays_fresh(self):
        _, sig = health._maps_walk(self.root)
        self.assertTrue(health._maps_sig_fresh(sig))

    def test_a_new_map_dirties_the_cache(self):
        """THE requirement: drop in a .pmtiles and the next call must re-count."""
        count, sig = health._maps_walk(self.root)
        self.assertEqual(count, 0)
        self.assertTrue(health._maps_sig_fresh(sig))

        _touch(os.path.join(self.root, "tiles", "state", "washington.pmtiles"))

        self.assertFalse(health._maps_sig_fresh(sig), "adding a map left the cache clean")
        self.assertEqual(health._maps_walk(self.root)[0], 1)

    def test_a_removed_map_dirties_the_cache(self):
        path = os.path.join(self.root, "tiles", "state", "wa.pmtiles")
        _touch(path)
        count, sig = health._maps_walk(self.root)
        self.assertEqual(count, 1)

        os.remove(path)

        self.assertFalse(health._maps_sig_fresh(sig))
        self.assertEqual(health._maps_walk(self.root)[0], 0)

    def test_a_new_subdirectory_dirties_the_cache(self):
        """A map arriving inside a brand-new directory is the case a naive
        top-level-mtime check misses: the new dir isn't in the signature at all,
        so invalidation has to come from its PARENT's mtime changing."""
        _, sig = health._maps_walk(self.root)
        os.makedirs(os.path.join(self.root, "tiles", "country"))
        self.assertFalse(health._maps_sig_fresh(sig))

    def test_a_deleted_directory_dirties_the_cache(self):
        _, sig = health._maps_walk(self.root)
        os.rmdir(os.path.join(self.root, "tiles", "state"))
        self.assertFalse(health._maps_sig_fresh(sig))

    def test_empty_signature_is_never_fresh(self):
        """Guards the cold start: no signature must not read as 'still valid'."""
        self.assertFalse(health._maps_sig_fresh(None))
        self.assertFalse(health._maps_sig_fresh({}))

    def test_torn_read_sentinel_forces_a_rescan(self):
        """A directory that changed mid-read records -1, which no real
        st_mtime_ns can equal — so the next request always re-walks rather than
        caching a count that never matched the tree."""
        self.assertFalse(health._maps_sig_fresh({self.root: -1}))


if __name__ == "__main__":
    unittest.main()


class MapsRootTests(unittest.TestCase):
    """WHICH directory the endpoint counts — the part that has been wrong twice.

    Tiles are downloaded inside GrayWolf, under a registered callsign, and land
    in its offline store (maps/routes.py's GW_STATE_DIR). OASIS does not stage
    them under <suite>/maps any more. Counting the suite tree returns 0 on every
    real station, which the dashboards render as WARN "no maps" on a box that
    has them — a false alarm that looks exactly like a missing download.

    The walker tests above pass a root in directly, so none of them could catch
    the endpoint pointing at the wrong one. These do.
    """

    def setUp(self):
        import app as oasis_app
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()
        self.tmp = tempfile.TemporaryDirectory()
        self.gw = os.path.join(self.tmp.name, "graywolf", "tiles", "state")
        os.makedirs(self.gw)
        health._MAPS_CACHE["sig"] = None      # module-level cache: never leak between tests
        health._MAPS_CACHE["count"] = 0

    def tearDown(self):
        health._MAPS_CACHE["sig"] = None
        health._MAPS_CACHE["count"] = 0
        self.tmp.cleanup()

    def _get(self):
        from unittest import mock
        import maps.routes as mr
        with mock.patch.object(mr, "GW_STATE_DIR", self.gw):
            return self.c.get("/api/health/maps").get_json()

    def test_counts_graywolfs_store(self):
        _touch(os.path.join(self.gw, "Washington.pmtiles"))
        _touch(os.path.join(self.gw, "Oregon.pmtiles"))
        body = self._get()
        self.assertTrue(body["ok"])
        self.assertEqual(body["count"], 2)

    def test_reports_the_directory_it_actually_read(self):
        # So "why does it say no maps?" is answerable from one curl.
        self.assertEqual(self._get()["dir"], self.gw)

    def test_an_empty_store_is_zero_and_still_ok(self):
        # §2: having no maps is the ANSWER, not a failed request.
        body = self._get()
        self.assertTrue(body["ok"])
        self.assertEqual(body["count"], 0)

    def test_does_not_count_the_suite_maps_tree(self):
        # The regression this class exists for. A .pmtiles under <suite>/maps
        # must NOT be counted: that tree is not where downloads land, and
        # counting it hid the real store being empty.
        import app as oasis_app
        suite_maps = os.path.join(oasis_app.SUITE_ROOT, "maps", "tiles", "state")
        decoy = os.path.join(suite_maps, "NotAReal.pmtiles")
        made = not os.path.exists(decoy)
        if made:
            _touch(decoy)
        try:
            self.assertEqual(self._get()["count"], 0)
        finally:
            if made:
                os.remove(decoy)
